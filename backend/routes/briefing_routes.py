"""Daily brief API — generate (+Telegram push), fetch latest/by-date, and list recent."""
import json
from fastapi import APIRouter
from sqlalchemy import select, desc
from backend.database.init_db import AsyncSessionLocal
from backend.database.models import DailyBriefing
from backend.briefing.generator import generate_and_send

router = APIRouter(prefix="/api/briefing", tags=["briefing"])


def _unpack(row):
    if not row:
        return None
    try:
        full = json.loads(row.summary_json) if row.summary_json else {}
    except Exception:
        full = {}
    return {
        "date": row.briefing_date, "title": row.title, "headline": row.headline,
        "forecast_summary": full.get("forecast_summary"), "analysis": full.get("analysis"),
        "news_digest": full.get("news_digest", []), "trading_view": full.get("trading_view"),
        "risks": full.get("risks", []), "sources": full.get("_sources", []),
        "fc_lines": full.get("_fc_lines", []),
    }


@router.post("/generate")
async def generate(send: bool = True):
    async with AsyncSessionLocal() as db:
        brief = await generate_and_send(db, send=send)
    public = {k: v for k, v in brief.items() if not k.startswith("_")}
    return {"ok": True, "telegram_sent": brief.get("_telegram_sent"),
            "sources": brief.get("_sources", []), "brief": public}


@router.get("/latest")
async def latest(date: str | None = None):
    """Latest Korean brief, or a specific date's brief if ?date=YYYY-MM-DD is given."""
    async with AsyncSessionLocal() as db:
        q = select(DailyBriefing).where(DailyBriefing.language == "ko")
        if date:
            q = q.where(DailyBriefing.briefing_date == date)
        row = (await db.execute(q.order_by(desc(DailyBriefing.id)).limit(1))).scalar_one_or_none()
    return {"brief": _unpack(row)}


@router.get("/list")
async def brief_list(limit: int = 20):
    """Recent briefs (newest first) for the archive list."""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(DailyBriefing).where(DailyBriefing.language == "ko")
            .order_by(desc(DailyBriefing.briefing_date)).limit(limit)
        )).scalars().all()
    return {"items": [{"date": r.briefing_date, "title": r.title, "headline": r.headline}
                      for r in rows]}
