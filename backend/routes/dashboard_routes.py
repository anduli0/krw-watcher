"""Public dashboard API — forecasts, trade signal, agents, activity feed."""
import asyncio
import json
from fastapi import APIRouter, Header, HTTPException
from backend.config import settings
from backend.database.init_db import AsyncSessionLocal
from backend.database import crud
from backend.database.models import HORIZONS, AgentOutput, RunLog
from sqlalchemy import select, desc

router = APIRouter(prefix="/api", tags=["dashboard"])

_cycle_lock = asyncio.Lock()


def _check_cron_secret(x_cron_secret: str | None):
    """Gate token-burning triggers behind a shared secret once CRON_SECRET is set.
    If CRON_SECRET is empty (dev / trusted network) the endpoint stays open."""
    if settings.CRON_SECRET and x_cron_secret != settings.CRON_SECRET:
        raise HTTPException(status_code=401, detail="invalid or missing X-Cron-Secret")


@router.get("/forecast")
async def get_forecast():
    async with AsyncSessionLocal() as db:
        rows = await crud.get_published_forecasts(db)
        horizons = {}
        report_ko = report_en = None
        for r in rows:
            horizons[r.horizon] = {
                "published_delta_krw": r.published_delta,
                "implied_rate": r.implied_rate,
                "confidence": r.confidence,
                "signal": r.signal,
                "unchanged_streak_days": r.unchanged_streak_days,
                "spot_at_run": r.spot_at_run,
                "target_date": r.target_date,
                "change_justification": r.change_justification,
            }
            if r.horizon == "1m":
                report_ko, report_en = r.report_text, r.report_text_en
        return {"horizons": horizons, "report_ko": report_ko, "report_en": report_en}


@router.get("/forecast/history")
async def forecast_history(horizon: str = "1m", limit: int = 40):
    async with AsyncSessionLocal() as db:
        rows = await crud.get_horizon_history(db, horizon, limit=limit)
    rows = list(reversed(rows))
    return {"horizon": horizon, "points": [
        {"date": r.target_date, "published_delta": r.published_delta,
         "implied_rate": r.implied_rate, "confidence": r.confidence}
        for r in rows]}


@router.get("/signal")
async def get_signal():
    from backend.brokers.factory import get_broker
    async with AsyncSessionLocal() as db:
        sigs = await crud.get_recent_signals(db, limit=1)
        latest = sigs[0] if sigs else None
        signal = None
        if latest:
            signal = {
                "side": latest.side, "horizon": latest.horizon,
                "spot_entry": latest.spot_entry, "target": latest.target, "stop": latest.stop,
                "notional_usd": latest.notional_usd, "confidence": latest.confidence,
                "expected_edge_krw": latest.expected_edge_krw, "rationale": latest.rationale,
                "status": latest.status, "created_at": latest.created_at.isoformat(),
            }
    balance = await get_broker().get_balance()
    return {"signal": signal, "broker": balance}


@router.get("/positions")
async def get_positions():
    from backend.brokers.factory import get_broker
    positions = await get_broker().get_positions()
    return {"positions": [p.__dict__ for p in positions]}


@router.get("/agents")
async def get_agents():
    async with AsyncSessionLocal() as db:
        run = (await db.execute(
            select(RunLog).where(RunLog.status == "completed")
            .order_by(desc(RunLog.id)).limit(1))).scalar_one_or_none()
        if not run:
            return {"run": None, "agents": [], "confidence_eval": None}
        outs = (await db.execute(
            select(AgentOutput).where(AgentOutput.run_id == run.id)
            .order_by(AgentOutput.agent_id))).scalars().all()
        agents = [{
            "agent_id": o.agent_id, "agent_name": o.agent_name, "signal": o.signal,
            "delta_krw": o.delta_krw, "confidence": o.confidence,
            "weight_applied": o.weight_applied, "round": o.round,
            "horizons": json.loads(o.horizons_json) if o.horizons_json else {},
        } for o in outs]
        # Enrich the reliability score with cross-horizon coherence (published forecasts)
        # and the system's OWN realized hit-rate (feedback loop) — see signals/confidence.py.
        published_horizons, calib_hit = await _confidence_inputs(db)
        return {
            "run": {"id": run.id, "cycle_type": run.cycle_type,
                    "spot_at_run": run.spot_at_run,
                    "completed_at": run.completed_at.isoformat() if run.completed_at else None,
                    "collaboration_rounds": run.collaboration_rounds},
            "agents": agents,
            "confidence_eval": _confidence_eval(agents, published_horizons, calib_hit),
        }


async def _confidence_inputs(db) -> tuple[dict, float | None]:
    """Gather the two extra reliability inputs: published per-horizon deltas (for
    cross-horizon coherence) and the realized 1m directional hit-rate (for calibration).
    Both are best-effort — the score degrades gracefully to agreement+conviction if absent."""
    published_horizons: dict = {}
    try:
        for r in await crud.get_published_forecasts(db):
            published_horizons[r.horizon] = {"published_delta_krw": r.published_delta,
                                              "confidence": r.confidence}
    except Exception:
        pass
    calib_hit = None
    try:
        from backend.feedback.bias_correction import compute_horizon_adjustments, MIN_SAMPLES
        adj = await compute_horizon_adjustments(db)
        a1m = adj.get("1m") or {}
        if (a1m.get("n_real") or 0) >= MIN_SAMPLES and a1m.get("dir_hit") is not None:
            calib_hit = a1m["dir_hit"]        # proven realized hit-rate at the lead horizon
    except Exception:
        pass
    return published_horizons, calib_hit


def _confidence_eval(agents: list[dict], published_horizons: dict | None = None,
                     calibration_hit: float | None = None) -> dict:
    """Calibrated committee-confidence reliability score (signals/confidence.py)."""
    from backend.signals.confidence import evaluate
    return evaluate(agents, published_horizons, calibration_hit)


@router.get("/activity")
async def get_activity(after: int = 0):
    from backend.data import activity_log as AL
    return {"events": AL.recent(after_seq=after, limit=200)}


@router.get("/news")
async def get_news(days: int = 7, per_day: int = 40):
    """Dated news archive: headlines grouped by KST date (newest day first), pruned to
    the retention window. Falls back to the live sweep cache until the archive fills."""
    async with AsyncSessionLocal() as db:
        grouped = await crud.get_news_days(db, days=days, per_day=per_day)
    if not grouped:  # fresh DB / first boot before the first sweep archived anything
        from backend.data.collector import get_latest
        from backend.database.models import today_kst
        items = get_latest().get("news_items") or []
        if items:
            grouped = [{"date": today_kst(), "count": len(items), "articles": [
                {"source": n.source, "title": n.title, "link": n.link,
                 "published": n.published, "score": round(n.score, 1)} for n in items[:per_day]]}]
    today_articles = grouped[0]["articles"] if grouped else []
    return {"days": grouped, "total": sum(d["count"] for d in grouped),
            # backward-compatible flat view (today's headlines)
            "count": len(today_articles), "articles": today_articles}


@router.get("/hierarchy")
async def get_hierarchy():
    """Latest hierarchical synthesis: 학계 / 전문분석 group views + 수석 reconciliation."""
    from backend.data import runtime_state
    return runtime_state.get_hierarchy()


@router.post("/cycle")
async def manual_cycle(x_cron_secret: str | None = Header(default=None)):
    """Trigger an AI cycle on demand (dev/testing or external cron)."""
    _check_cron_secret(x_cron_secret)
    from backend.main import trigger_cycle
    if _cycle_lock.locked():
        return {"status": "busy", "message": "a cycle is already running"}
    async def _run():
        async with _cycle_lock:
            await trigger_cycle("forced")
    asyncio.create_task(_run())
    return {"status": "started"}


@router.post("/report/daily")
async def daily_report(x_cron_secret: str | None = Header(default=None)):
    """Guarantee today's report exists (AI cycle + brief), generating it if missing.
    The single endpoint an external scheduler (cron-job.org / Render cron) should call
    once a day — PC-independent, idempotent, bounded to one report/day. Runs the
    generation synchronously so the caller gets a real status back."""
    _check_cron_secret(x_cron_secret)
    from backend.main import run_daily_report_guarantee
    result = await run_daily_report_guarantee(force=True)
    return result


@router.get("/report/daily/status")
async def daily_report_status():
    """Is today's report present? (KST-day completed run + Korean brief.)"""
    from backend.database.models import today_kst
    from backend.scheduler.daily_guarantee import _has_completed_run_today, _has_brief_today
    today = today_kst()
    async with AsyncSessionLocal() as db:
        run_ok = await _has_completed_run_today(db, today)
        brief_ok = await _has_brief_today(db, today)
    return {"date": today, "run": run_ok, "brief": brief_ok,
            "complete": bool(run_ok and brief_ok)}
