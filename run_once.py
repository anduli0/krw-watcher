"""
Run a single data sweep + AI cycle from the CLI (no server). Useful for testing the
committee end-to-end and seeing the forecast + trade signal in the terminal.

    python run_once.py
"""
import asyncio
import logging
import sys

# Korean Windows consoles default to cp949 — force UTF-8 so 원/→ and any unicode print cleanly.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")


async def main():
    from backend.database.init_db import init_db
    from backend.data.collector import collect_data
    from backend.main import trigger_cycle

    await init_db()
    print("\n=== DATA SWEEP ===")
    await collect_data()
    print("\n=== AI CYCLE ===")
    await trigger_cycle("forced")

    # Print the result
    from backend.database.init_db import AsyncSessionLocal
    from backend.database import crud
    from backend.brokers.factory import get_broker
    async with AsyncSessionLocal() as db:
        forecasts = await crud.get_published_forecasts(db)
        sigs = await crud.get_recent_signals(db, limit=1)
    print("\n=== USD/KRW FORECAST (Δ in won vs spot) ===")
    for f in forecasts:
        print(f"  {f.horizon:>4}: {f.published_delta:+.1f}원  "
              f"→ implied {f.implied_rate}  ({f.signal}, conf {f.confidence:.0%})")
    if sigs:
        s = sigs[0]
        print(f"\n=== TRADE SIGNAL ===\n  {s.side}  {s.rationale}")
    print("\n=== BROKER ===")
    print(" ", await get_broker().get_balance())


if __name__ == "__main__":
    asyncio.run(main())
