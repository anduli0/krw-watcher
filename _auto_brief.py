"""Automated daily KRW (USD/KRW) brief — quality protocol v2.

Synthesis via `claude -p` (Claude Max subscription, headless — bypasses the dead
watcher API key). Grounded in the live collector data (spot, macro, ranked news),
the house horizon forecast, and yesterday's brief for continuity. Persists via
the same DailyBriefing upsert the real generator uses. Idempotent unless --force.
Exits non-zero without touching data if headless auth is unavailable.

Usage:  python _auto_brief.py [--date YYYY-MM-DD] [--force]
"""
import argparse
import asyncio
import html
import json
import re
import subprocess
import sys
from datetime import date

REQUIRED = ["title", "headline", "forecast_summary", "analysis", "news_digest",
            "trading_view", "risks"]


def _scrub(o):
    if isinstance(o, str):
        return html.unescape(o).strip()
    if isinstance(o, list):
        return [_scrub(x) for x in o]
    if isinstance(o, dict):
        return {k: _scrub(v) for k, v in o.items()}
    return o


def _validate(data: dict) -> dict:
    for f in REQUIRED:
        if f not in data or not data[f]:
            raise ValueError(f"missing/empty field: {f}")
    if not (3 <= len(data["news_digest"]) <= 8):
        raise ValueError("news_digest must be 3-8 bullets")
    if not (3 <= len(data["risks"]) <= 6):
        raise ValueError("risks must be 3-6 bullets")
    return data


def _claude_json(prompt: str, timeout: int = 600) -> dict:
    proc = subprocess.run(
        ["claude", "-p", "--model", "claude-opus-4-8", prompt],
        capture_output=True, text=True, encoding="utf-8", timeout=timeout,
    )
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 or "401" in out or "authenticate" in out.lower():
        raise RuntimeError(f"claude -p auth/exec failed (rc={proc.returncode}): "
                           f"{(proc.stderr or out)[:200]}")
    start, end = out.find("{"), out.rfind("}")
    if start < 0 or end < 0:
        raise ValueError(f"no JSON in claude output: {out[:200]}")
    return json.loads(out[start:end + 1])


async def _gather():
    """Collect live data (no LLM) + house forecast + yesterday's brief."""
    out = {}
    try:
        from backend.data.collector import collect_data
        await collect_data()
    except Exception as e:
        out["collect_err"] = str(e)[:150]

    from backend.data import collector as C
    latest = getattr(C, "_LATEST", {}) or {}
    snap = latest.get("snapshot")
    out["spot"] = getattr(snap, "spot", None) if snap else None
    out["bok"] = str(latest.get("bok"))[:400]
    news = latest.get("news_items") or []
    out["news"] = [{"title": getattr(n, "title", str(n))[:160],
                    "published_at": str(getattr(n, "published_at", ""))[:19]}
                   for n in news[:20]]
    out["macro"] = (snap.summary_text()[:1500] if snap and hasattr(snap, "summary_text")
                    else str(snap)[:1200])

    from backend.database.init_db import AsyncSessionLocal
    from backend.database import crud
    async with AsyncSessionLocal() as db:
        fc = await crud.get_published_forecasts(db)
        out["forecast"] = [{"horizon": f.horizon, "delta": f.published_delta,
                            "signal": f.signal} for f in (fc or [])]

    from sqlalchemy import select, desc
    from backend.database.models import DailyBriefing
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(DailyBriefing).where(DailyBriefing.language == "ko",
                                        DailyBriefing.status == "published")
            .order_by(desc(DailyBriefing.briefing_date)).limit(1)
        )).scalar_one_or_none()
    out["yesterday"] = (f"[{row.briefing_date}] {row.title} — {row.headline}"
                        if row else "")
    return out


def _build_prompt(date_str: str, g: dict) -> str:
    news_block = "\n".join(f"- {n['title']}" for n in g["news"])
    fc_block = ", ".join(f"{f['horizon']}: {f['delta']:+.0f}원({f['signal']})"
                         for f in g["forecast"])
    schema_hint = json.dumps({
        "title": "1줄 제목", "headline": "오늘의 핵심 동인 1문장",
        "forecast_summary": "1주/1개월/3개월/12개월 경로 요약 2-3문장",
        "analysis": "2-3개 짧은 문단", "news_digest": ["3-8개 한국어 불릿"],
        "trading_view": "실전 포지셔닝 관점 1문단", "risks": ["3-6개 한국어 불릿"]},
        ensure_ascii=False)
    return f"""당신은 원-달러(USD/KRW) 환율 예측 시스템의 합성 엔진이다. {date_str} 데일리 브리프를 한국어로 작성하라.

문체 규칙: 쉬운 한국어. 학술 용어나 영어 음차(예: 레짐) 대신 평이한 표현(예: 시장 국면). 문장은 짧고 명확하게.

근거 규칙(필수):
- 아래 입력에 있는 사실만 쓴다. 입력에 없는 사건·이름·숫자는 존재하지 않는 것으로 간주한다.
- 모든 수치는 입력의 값과 정확히 일치해야 한다.
- 하우스 예측(아래 delta)은 위원회의 공식 경로다. 양수 delta = 원화 약세. 이 경로와 일관된 확신 있는 서사를 쓰되, 내부 시스템 상태(신뢰도 등)는 언급하지 않는다.

현물 환율: {g['spot']}
하우스 예측 경로: {fc_block}
한국은행: {g['bok']}
매크로: {g['macro']}
어제 브리프(연속성 참고): {g['yesterday'] or '(없음)'}
오늘 뉴스 헤드라인:
{news_block}

다음 JSON만 응답하라(앞뒤 산문 금지):
{schema_hint}"""


async def _persist(date_str: str, brief: dict):
    from sqlalchemy import select
    from backend.database.models import DailyBriefing
    from backend.database.init_db import AsyncSessionLocal

    sources = brief.get("news_digest", [])
    payload = {
        "briefing_date": date_str, "language": "ko",
        "title": brief.get("title"), "headline": brief.get("headline"),
        "summary_json": json.dumps(brief, ensure_ascii=False),
        "sources_json": json.dumps(sources, ensure_ascii=False),
        "article_count": len(sources), "status": "published",
    }
    async with AsyncSessionLocal() as db:
        existing = (await db.execute(
            select(DailyBriefing).where(DailyBriefing.briefing_date == date_str,
                                        DailyBriefing.language == "ko").limit(1)
        )).scalar_one_or_none()
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            db.add(DailyBriefing(**payload))
        await db.commit()


async def main(date_str: str, force: bool) -> int:
    if not force:
        from sqlalchemy import select
        from backend.database.models import DailyBriefing
        from backend.database.init_db import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            row = (await db.execute(
                select(DailyBriefing).where(DailyBriefing.briefing_date == date_str,
                                            DailyBriefing.language == "ko",
                                            DailyBriefing.status == "published").limit(1)
            )).scalar_one_or_none()
        if row:
            print(f"BRIEF_ALREADY_PUBLISHED {date_str}")
            return 0

    g = await _gather()
    print(f"gathered: news={len(g['news'])} spot={g['spot']} fc={len(g['forecast'])} "
          f"yesterday={'yes' if g['yesterday'] else 'no'}")

    # Audit trail
    try:
        import os
        os.makedirs("logs", exist_ok=True)
        with open(f"logs/brief_inputs_{date_str}.json", "w", encoding="utf-8") as f:
            json.dump(g, f, ensure_ascii=False)
    except Exception:
        pass

    prompt = _build_prompt(date_str, g)
    try:
        brief = _validate(_scrub(_claude_json(prompt)))
    except (ValueError, json.JSONDecodeError) as e:
        retry = prompt + f"\n\n이전 시도가 검증에 실패했다: {e}. 수정해서 JSON만 다시 응답하라."
        brief = _validate(_scrub(_claude_json(retry)))

    # Grounding audit (warn-only)
    src = json.dumps(g, ensure_ascii=False)
    nums = sorted(set(re.findall(r"\d+\.\d+", json.dumps(brief, ensure_ascii=False))))
    warn = [n for n in nums if n not in src]
    if warn:
        print("GROUND_WARN:", warn[:10])

    await _persist(date_str, brief)
    print("KRW_BRIEF_PUBLISHED", date_str, "|", (brief.get("title") or "")[:40])
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.date, a.force)))
