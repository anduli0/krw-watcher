"""Persist a pre-synthesized KRW daily brief (KO) for a given date into DailyBriefing.
Reads _krw_ko.json (authored by Claude via the harness, grounded in real data).
Mirrors backend/briefing/generator.generate_and_send persistence exactly.

Usage: python _persist_brief.py --date 2026-07-02
"""
import argparse, asyncio, html, json, sys

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


async def main(ds, brief):
    from sqlalchemy import select
    from backend.database.models import DailyBriefing
    from backend.database.init_db import AsyncSessionLocal

    sources = brief.get("news_digest", [])
    payload = {
        "briefing_date": ds, "language": "ko",
        "title": brief.get("title"), "headline": brief.get("headline"),
        "summary_json": json.dumps(brief, ensure_ascii=False),
        "sources_json": json.dumps(sources, ensure_ascii=False),
        "article_count": len(sources), "status": "published",
    }
    async with AsyncSessionLocal() as db:
        existing = (await db.execute(
            select(DailyBriefing).where(DailyBriefing.briefing_date == ds,
                                        DailyBriefing.language == "ko").limit(1)
        )).scalar_one_or_none()
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
        else:
            db.add(DailyBriefing(**payload))
        await db.commit()
    print("KRW_BRIEF_PUBLISHED", ds, "| title:", (brief.get("title") or "")[:40])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="2026-07-02")
    a = ap.parse_args()
    brief = _scrub(json.load(open("_krw_ko.json", encoding="utf-8")))
    for f in REQUIRED:
        if f not in brief or not brief[f]:
            raise SystemExit(f"VALIDATION FAILED: missing/empty {f}")
    sys.exit(asyncio.run(main(a.date, brief)))
