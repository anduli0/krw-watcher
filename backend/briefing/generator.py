"""
Daily FX brief generator.

Synthesizes a Korean daily brief from three inputs:
  1. The committee's USD/KRW forecast (4 horizons + Chief derivation report).
  2. The agent committee's reasoning (expert FX-analysis synthesis).
  3. The day's ranked news headlines (synthesized, not just listed).

Output is a structured object rendered both for Telegram (HTML) and DB storage.
"""
import json
import html as _html
import logging
from datetime import date

import anthropic
from sqlalchemy import select, desc

from backend.config import settings
from backend.database.models import RunLog, AgentOutput, DailyBriefing
from backend.database import crud

logger = logging.getLogger("krw_watcher.brief")

BRIEF_SCHEMA = """Respond ONLY with JSON:
{
  "title": "<오늘 원/달러 한 줄 제목>",
  "headline": "<시장 임팩트 한 문장>",
  "forecast_summary": "<환율 전망을 자연스러운 서술체 2-3문장으로: 1주/1개월/3개월/1년 방향과 폭을 흐름 있게>",
  "analysis": "<핵심 드라이버(금리차/달러/위안/리스크/수급/통화량/엔캐리)를 자연스럽게 이어지는 4-6문장 서술체로 종합 — 개조식 금지>",
  "news_digest": ["<핵심 뉴스를 환율 함의로 해석한 완결된 서술 문장 1>", "<2>", "<3>", "<4>"],
  "trading_view": "<트레이딩 함의 1-2문장: 방향/레벨/사이즈 톤>",
  "risks": ["<관전 포인트·리스크 1>", "<2>", "<3>"]
}"""


async def _latest_run_agents(db) -> list[dict]:
    run = (await db.execute(
        select(RunLog).where(RunLog.status == "completed").order_by(desc(RunLog.id)).limit(1)
    )).scalar_one_or_none()
    if not run:
        return []
    outs = (await db.execute(
        select(AgentOutput).where(AgentOutput.run_id == run.id).order_by(AgentOutput.agent_id)
    )).scalars().all()
    rows = []
    for o in outs:
        reasoning = ""
        try:
            reasoning = (json.loads(o.raw_json) or {}).get("reasoning", "") if o.raw_json else ""
        except Exception:
            pass
        rows.append({"name": o.agent_name, "signal": o.signal, "delta": o.delta_krw,
                     "conf": o.confidence, "reasoning": reasoning})
    return rows


def _discussion_text(hierarchy: dict | None) -> str:
    """Flatten the committee's hierarchical debate (학계/퍼블릭/프라이빗 → 수석) into a
    compact block so the brief is grounded in the agents' actual discussion."""
    if not hierarchy:
        return ""
    parts = []
    labels = {"academic": "학계", "public": "퍼블릭(중앙은행·재정·대외)",
              "private": "프라이빗(IB데스크·수급)"}
    for key, label in labels.items():
        v = hierarchy.get(key) or {}
        txt = v.get("synthesis") or v.get("reconciliation") or v.get("key_debate") or ""
        if txt:
            parts.append(f"[{label}] {str(txt)[:400]}")
    chief = hierarchy.get("chief") or {}
    ctxt = chief.get("reconciliation") or chief.get("synthesis") or ""
    if ctxt:
        parts.append(f"[수석 최종 협의] {str(ctxt)[:600]}")
    return "\n".join(parts)


async def gather_context(db) -> dict:
    from backend.data.collector import get_latest
    from backend.data import runtime_state
    forecasts = await crud.get_published_forecasts(db)
    hz = {f.horizon: f for f in forecasts}
    agents = await _latest_run_agents(db)
    snap = get_latest().get("snapshot")
    # News: prefer the dated archive (the full day's scrape); fall back to the live sweep.
    news = await crud.get_recent_news_rows(db, days=1, limit=40)
    if not news:
        news = get_latest().get("news_items") or []
    spot = getattr(snap, "spot", None)
    report = hz["1m"].report_text if "1m" in hz else None
    hierarchy = (runtime_state.get_hierarchy() or {}).get("hierarchy")
    return {"forecasts": forecasts, "hz": hz, "agents": agents, "spot": spot,
            "news": news, "report": report, "hierarchy": hierarchy}


async def generate_brief(db, lang: str = "ko") -> dict:
    ctx = await gather_context(db)
    order = ["1w", "1m", "3m", "12m"]
    names = {"1w": "1주", "1m": "1개월", "3m": "3개월", "12m": "1년"}
    fc_lines = []
    for h in order:
        f = ctx["hz"].get(h)
        if f:
            fc_lines.append(f"{names[h]}: {f.published_delta:+.1f}원 → {f.implied_rate} "
                            f"({f.signal}, conf {round((f.confidence or 0)*100)}%)")
    # Top agents by conviction (|delta|×conf) for the analysis synthesis.
    ranked = sorted(ctx["agents"], key=lambda a: -abs((a["delta"] or 0) * (a["conf"] or 0)))[:7]
    agent_lines = [f"- {a['name']} [{a['signal']} {a['delta']:+.0f}원/{round((a['conf'] or 0)*100)}%]: "
                   f"{(a['reasoning'] or '')[:200]}" for a in ranked]
    news_lines = [f"- [{n.source}] {n.title}" for n in ctx["news"][:20]]
    discussion = _discussion_text(ctx.get("hierarchy"))
    try:
        from backend.agents.house_view import structural_view_text
        structural = structural_view_text()
    except Exception:
        structural = ""

    prompt = (
        f"당신은 원/달러(USD/KRW) 외환 전문 스트래티지스트입니다. 아래 내부 위원회의 예측·논의와 "
        f"오늘 수집된 뉴스 스크랩을 바탕으로 투자자용 데일리 브리프를 한국어로 작성하세요.\n\n"
        f"현재 스팟: {ctx['spot']}원\n\n"
        f"=== 위원회 예측 (Δ는 현재 스팟 대비 원, +면 원화 약세) ===\n" + "\n".join(fc_lines) +
        f"\n\n=== 위원회 내부 논의 (학계·퍼블릭·프라이빗 → 수석 협의) ===\n{discussion or '없음'}\n\n"
        f"=== Chief 도출 리포트 ===\n{(ctx['report'] or '없음')[:1200]}\n\n"
        f"=== 핵심 에이전트 분석 (컨빅션 상위) ===\n" + "\n".join(agent_lines) +
        (f"\n\n=== 구조적 배경 (느린 외환수급 전제 — 단기 흐름과 별개) ===\n{structural}" if structural else "") +
        f"\n\n=== 오늘의 뉴스 스크랩 (중요도순, 일자별 아카이브) ===\n" + ("\n".join(news_lines) or "없음") +
        f"\n\n{BRIEF_SCHEMA}\n\n"
        "[작성 지침] 위원회의 내부 논의(학계·퍼블릭·프라이빗 그룹 견해와 수석의 협의 결론)와 오늘의 "
        "뉴스 스크랩을 실제로 반영해, 어떤 근거로 이 전망에 도달했는지가 드러나게 쓰세요. "
        "[균형] 단기 자금 이동(외국인 수급·당국 개입 같은 일시적 흐름)에만 치우치지 말고, '구조적 배경'의 "
        "외환수급 펀더멘털 — 경상흑자가 실제로 국내로 환류되는지(본원소득 재투자·해외 유보), 대형 수출기업의 "
        "미국 재투자 — 을 중기(3개월·1년) 전망의 근거로 자연스럽게 엮으세요. 단, 구조적 요인은 단기(1주·1개월)엔 "
        "과도 적용 금지. [문체] 공식적이고 전문적인 신뢰도는 유지하되, 개조식 나열이 아니라 자연스럽게 이어지는 "
        "서술체로 쓰세요 — 노련한 외환 스트래티지스트가 아침 데스크 노트를 쓰듯 매끄럽고 읽기 좋게. "
        "구체적 수치·드라이버를 문장 안에 자연스럽게 녹이고, 뉴스는 단순 나열이 아니라 환율 함의로 엮어 종합하세요."
    )
    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    brief = {}
    try:
        resp = await client.messages.create(
            model=settings.MODEL_ID, max_tokens=3500, temperature=0.4,
            messages=[{"role": "user", "content": prompt}])
        raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
        import re
        # strip code fences, then take the outermost {...}
        raw = re.sub(r"```(?:json)?", "", raw)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            brief = json.loads(m.group())
    except Exception as e:
        logger.warning("Brief generation failed: %s", e)

    # Guaranteed-useful fallback if the LLM JSON was missing/truncated.
    if not brief.get("title"):
        brief = {
            "title": "원/달러 데일리 브리프",
            "headline": (fc_lines[1] if len(fc_lines) > 1 else "USD/KRW 위원회 전망"),
            "forecast_summary": " / ".join(fc_lines),
            "analysis": (ctx["report"] or "").replace("#", "").strip()[:700],
            "news_digest": [n.title for n in ctx["news"][:4]],
            "trading_view": "", "risks": [],
        }
    brief["_spot"] = ctx["spot"]
    brief["_fc_lines"] = fc_lines
    brief["_sources"] = [{"source": n.source, "title": n.title, "link": n.link} for n in ctx["news"][:10]]
    return brief


def format_telegram(brief: dict) -> str:
    e = lambda s: _html.escape(str(s or ""))
    today = date.today().isoformat()
    parts = [
        f"🇰🇷💵 <b>원/달러 데일리 브리프</b> · {today}",
        f"<b>{e(brief.get('title'))}</b>",
        f"<i>{e(brief.get('headline'))}</i>",
        f"\n📊 <b>환율 전망</b>\n{e(brief.get('forecast_summary'))}",
    ]
    if brief.get("_fc_lines"):
        parts.append("<pre>" + e("\n".join(brief["_fc_lines"])) + "</pre>")
    parts.append(f"\n🔎 <b>전문 분석</b>\n{e(brief.get('analysis'))}")
    if brief.get("news_digest"):
        parts.append("\n📰 <b>핵심 뉴스 종합</b>\n" + "\n".join(f"• {e(x)}" for x in brief["news_digest"]))
    if brief.get("trading_view"):
        parts.append(f"\n🎯 <b>트레이딩 함의</b>\n{e(brief.get('trading_view'))}")
    if brief.get("risks"):
        parts.append("\n⚠️ <b>리스크·관전 포인트</b>\n" + "\n".join(f"• {e(x)}" for x in brief["risks"]))
    parts.append("\n<i>KRW-Watcher · 18 에이전트 위원회. 투자 책임은 본인에게 있습니다.</i>")
    return "\n".join(parts)


async def generate_and_send(db, send: bool = True) -> dict:
    from backend.briefing.telegram import send_telegram
    from backend.data import activity_log as AL
    AL.emit("brief", "brief", "데일리 브리프 생성 중…", "#C58AF9", "info")
    brief = await generate_brief(db)
    today = date.today().isoformat()

    # Persist (upsert by date+lang).
    existing = (await db.execute(
        select(DailyBriefing).where(DailyBriefing.briefing_date == today,
                                    DailyBriefing.language == "ko").limit(1)
    )).scalar_one_or_none()
    payload = {
        "briefing_date": today, "language": "ko",
        "title": brief.get("title"), "headline": brief.get("headline"),
        "summary_json": json.dumps(brief, ensure_ascii=False),
        "sources_json": json.dumps(brief.get("_sources", []), ensure_ascii=False),
        "article_count": len(brief.get("_sources", [])), "status": "published",
    }
    if existing:
        for k, v in payload.items():
            setattr(existing, k, v)
    else:
        db.add(DailyBriefing(**payload))
    await db.commit()

    # Keep ~30 days of brief history; prune anything older.
    try:
        from sqlalchemy import delete
        from datetime import timedelta
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        await db.execute(delete(DailyBriefing).where(DailyBriefing.briefing_date < cutoff))
        await db.commit()
    except Exception:
        pass

    msg = format_telegram(brief)
    sent = await send_telegram(msg) if send else False
    brief["_telegram_sent"] = sent
    return brief
