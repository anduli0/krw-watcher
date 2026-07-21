"""
Daily report guarantee — makes the prediction report generate EVERY day, reliably,
independent of the every-2h AI-cycle cost switch (DISABLE_AUTO_CYCLE) and resilient to
Render restarts / missed cron fires.

Why this exists: the every-2h scheduled cycle can be turned off for cost control, and a
container restart can skip a scheduled fire — so on some days no fresh forecast/brief was
produced. This guarantees exactly ONE report per KST day.

Mechanism (belt-and-suspenders), all converging on the same idempotent check:
  • The 30-min data sweep (which runs even in DATA-ONLY mode) calls ensure_daily_report()
    → self-healing catch-up: the first sweep after DAILY_REPORT_HOUR_KST generates the day's
    report if it is missing. This works on the current always-on deployment with no extra
    infrastructure.
  • A dedicated scheduler cron at DAILY_REPORT_HOUR:15 KST calls it too (prompt delivery).
  • POST /api/report/daily calls it with force=True for an external cron (cron-job.org /
    Render cron) — fully PC-independent.

Idempotency: keyed on the KST calendar date. A day is "done" once a COMPLETED run and
today's Korean brief both exist. Bounded retries per day so a transient failure re-attempts
on the next sweep without hammering.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select, desc

from backend.config import settings
from backend.database.models import KST, RunLog, DailyBriefing, today_kst

logger = logging.getLogger("krw_watcher.daily_guarantee")

MAX_ATTEMPTS_PER_DAY = 4          # transient-failure retries (re-tried on later sweeps)
_lock = asyncio.Lock()
_state: dict = {"date": None, "attempts": 0, "run_done": False, "brief_done": False}


def _reset_if_new_day(today: str) -> None:
    if _state["date"] != today:
        _state.update(date=today, attempts=0, run_done=False, brief_done=False)


async def _has_completed_run_today(db, today: str) -> bool:
    """A run whose completed_at (UTC) falls on today's KST calendar date."""
    row = (await db.execute(
        select(RunLog).where(RunLog.status == "completed")
        .order_by(desc(RunLog.id)).limit(1))).scalar_one_or_none()
    if not row or row.completed_at is None:
        return False
    completed = row.completed_at
    if completed.tzinfo is None:                      # stored naive-UTC (datetime.utcnow)
        completed = completed.replace(tzinfo=timezone.utc)
    return completed.astimezone(KST).date().isoformat() == today


async def _has_brief_today(db, today: str) -> bool:
    row = (await db.execute(
        select(DailyBriefing.id).where(
            DailyBriefing.briefing_date == today,
            DailyBriefing.language == "ko"))).first()
    return row is not None


def _kst_hour_now() -> int:
    return datetime.now(KST).hour


async def ensure_daily_report(session_factory, trigger_cycle_fn, run_brief_fn,
                              force: bool = False) -> dict:
    """Generate today's report (one AI cycle + brief) if it is missing.

    session_factory  — AsyncSessionLocal (callable returning an async session ctx mgr)
    trigger_cycle_fn — async fn(cycle_type: str) that runs one full AI cycle
    run_brief_fn     — async fn() that generates + persists (+delivers) today's brief
    force            — bypass the opt-out flag and the "too early" hour gate
                       (used by the explicit endpoint)

    Returns a small status dict (also used by the endpoint's JSON response).
    """
    today = today_kst()
    _reset_if_new_day(today)

    if settings.DISABLE_DAILY_REPORT and not force:
        return {"status": "disabled", "date": today}

    hour = _kst_hour_now()
    if not force and hour < settings.DAILY_REPORT_HOUR_KST:
        return {"status": "too_early", "date": today, "hour_kst": hour,
                "target_hour_kst": settings.DAILY_REPORT_HOUR_KST}

    # Idempotent check — cheap, runs on every sweep.
    async with session_factory() as db:
        run_ok = await _has_completed_run_today(db, today)
        brief_ok = await _has_brief_today(db, today)
    _state["run_done"], _state["brief_done"] = run_ok, brief_ok
    if run_ok and brief_ok:
        return {"status": "already_done", "date": today,
                "run": True, "brief": True}

    if _state["attempts"] >= MAX_ATTEMPTS_PER_DAY and not force:
        return {"status": "exhausted", "date": today,
                "attempts": _state["attempts"], "run": run_ok, "brief": brief_ok}

    if _lock.locked():
        return {"status": "in_progress", "date": today}

    async with _lock:
        # Re-check inside the lock (another caller may have just finished).
        async with session_factory() as db:
            run_ok = await _has_completed_run_today(db, today)
            brief_ok = await _has_brief_today(db, today)
        if run_ok and brief_ok:
            _state.update(run_done=True, brief_done=True)
            return {"status": "already_done", "date": today, "run": True, "brief": True}

        _state["attempts"] += 1
        logger.info("Daily report guarantee: generating (attempt %d/%d, run_ok=%s brief_ok=%s)",
                    _state["attempts"], MAX_ATTEMPTS_PER_DAY, run_ok, brief_ok)

        cycle_ran = False
        if not run_ok:
            try:
                await trigger_cycle_fn("daily")
                cycle_ran = True
                _state["run_done"] = True
            except Exception as e:
                logger.error("Daily report guarantee: cycle failed: %s", e)

        brief_ran = False
        if run_brief_fn is not None and not brief_ok:
            try:
                await run_brief_fn()
                brief_ran = True
                _state["brief_done"] = True
            except Exception as e:
                logger.error("Daily report guarantee: brief failed: %s", e)

        status = "generated" if (cycle_ran or brief_ran) else "noop"
        logger.info("Daily report guarantee: %s (cycle_ran=%s brief_ran=%s)",
                    status, cycle_ran, brief_ran)
        return {"status": status, "date": today,
                "cycle_ran": cycle_ran, "brief_ran": brief_ran,
                "attempts": _state["attempts"]}
