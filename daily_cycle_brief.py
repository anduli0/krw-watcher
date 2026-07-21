"""
Daily KRW cycle + brief — run as a FRESH process from on-disk code, NOT via the
long-running server's API.

Why a fresh process: the always-on uvicorn (port 8010) is launched elevated (SYSTEM)
and cannot be restarted without admin/reboot, so it can keep serving STALE code after
an edit. Running the cycle + brief here guarantees the daily forecast/brief always use
the LATEST on-disk logic — e.g. the desk structural view in agents/house_view.py
(경상흑자가 국내로 환류돼야만 외환공급, 삼성·SK하이닉스 미국 재투자 등). It writes to the same
SQLite DB the server serves from, and pushes the brief to Telegram directly.

Idempotent: if today's Korean brief already exists, it exits WITHOUT spending tokens.
That makes it safe to fire from the daily 08:30 trigger AND the at-logon safety-net
trigger, and harmless if the in-process scheduler (once a reboot revives it) already
produced the 08:10 brief.

    .venv\\Scripts\\python.exe daily_cycle_brief.py
"""
import asyncio
import logging
import sys
from datetime import date

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(level=logging.WARNING,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")


async def main() -> int:
    from backend.database.init_db import init_db, AsyncSessionLocal
    from backend.database.models import DailyBriefing
    from sqlalchemy import select

    await init_db()
    today = date.today().isoformat()

    # Idempotency: today's brief already present → nothing to do (cheap exit).
    async with AsyncSessionLocal() as db:
        exists = (await db.execute(
            select(DailyBriefing.id).where(
                DailyBriefing.briefing_date == today,
                DailyBriefing.language == "ko"))).first()
    if exists:
        print(f"SKIP: today's brief ({today}) already exists — no cycle/brief run.", flush=True)
        return 0

    from backend.data.collector import collect_data
    from backend.main import trigger_cycle
    from backend.briefing.generator import generate_and_send

    print("DATA SWEEP…", flush=True)
    await collect_data()
    print("AI CYCLE (disk code · structural view injected)…", flush=True)
    await trigger_cycle("forced")
    print("BRIEF + Telegram…", flush=True)
    async with AsyncSessionLocal() as db:
        brief = await generate_and_send(db, send=True)
    print(f"DONE: telegram_sent={brief.get('_telegram_sent')} "
          f"title={brief.get('title')}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
