"""
Scheduler — APScheduler jobs (all times KST / Asia-Seoul).

  • Data sweep: every 30 minutes (no AI tokens).
  • AI cycle:   every 2 hours, with extra cycles around the onshore FX session
                (09:00 open and 15:30 close) when USD/KRW is most active.

Adjust the cadence to your token budget. More cycles = fresher signals = more cost.
"""
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger("krw_watcher.scheduler")
scheduler = AsyncIOScheduler(timezone="Asia/Seoul")


def init_scheduler(trigger_cycle, run_data_collection, run_daily_brief=None,
                   run_daily_report=None):
    from backend.config import settings

    # Data collection every 30 minutes (free — no AI tokens).
    scheduler.add_job(run_data_collection, IntervalTrigger(minutes=30),
                      id="data_sweep", replace_existing=True, max_instances=1)

    # Daily report guarantee — one report/day even in DATA-ONLY mode. Registered BEFORE the
    # DISABLE_AUTO_CYCLE early-return so it runs regardless of the every-2h cost switch.
    # (The 30-min sweep also calls it, self-healing a missed fire; this cron is prompt delivery.)
    if run_daily_report is not None and not settings.DISABLE_DAILY_REPORT:
        scheduler.add_job(run_daily_report,
                          CronTrigger(hour=settings.DAILY_REPORT_HOUR_KST, minute=15),
                          id="daily_report_guarantee", replace_existing=True, max_instances=1)

    # DATA-ONLY mode: skip the every-2h token-burning cycles (read-only mirror / cost control).
    # The daily report guarantee above still runs (bounded to one report/day).
    if settings.DISABLE_AUTO_CYCLE:
        scheduler.start()
        logger.info("Scheduler started in DATA-ONLY mode (DISABLE_AUTO_CYCLE=true): "
                    "30-min sweeps + daily report guarantee %02d:15 KST, NO every-2h cycles. "
                    "Trigger extra cycles via /api/cycle (cron-secret) when needed.",
                    settings.DAILY_REPORT_HOUR_KST)
        return

    # AI cycle every 2 hours.
    scheduler.add_job(trigger_cycle, IntervalTrigger(hours=2),
                      id="ai_cycle", replace_existing=True, max_instances=1,
                      kwargs={"cycle_type": "scheduled"})

    # Extra cycles at the onshore session open/close (KST).
    scheduler.add_job(trigger_cycle, CronTrigger(hour=9, minute=5),
                      id="ai_cycle_open", replace_existing=True, max_instances=1,
                      kwargs={"cycle_type": "session_open"})
    scheduler.add_job(trigger_cycle, CronTrigger(hour=15, minute=35),
                      id="ai_cycle_close", replace_existing=True, max_instances=1,
                      kwargs={"cycle_type": "session_close"})

    # Daily brief → Telegram, after the session-open cycle (default 08:00 KST).
    if run_daily_brief is not None:
        scheduler.add_job(run_daily_brief, CronTrigger(hour=settings.BRIEFING_HOUR_KST, minute=10),
                          id="daily_brief", replace_existing=True, max_instances=1)

    scheduler.start()
    logger.info("Scheduler started: data sweep /30min, AI cycle /2h + session open/close, "
                "daily brief %02d:10 KST → Telegram, daily report guarantee %02d:15 KST",
                settings.BRIEFING_HOUR_KST, settings.DAILY_REPORT_HOUR_KST)
