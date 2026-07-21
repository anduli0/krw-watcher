"""One-off operator script: run a single FORCED KRW cycle against the live DB.

Mirrors fed-watcher/_run_forced_cycle.py. Runs as a FRESH process from on-disk
code (the always-on uvicorn on :8010 is SYSTEM-elevated and may serve stale code).
Refreshes data then re-publishes the forecast into the same SQLite the server reads
(WAL -> concurrent-safe). Does NOT regenerate the brief or push Telegram, and has no
idempotency gate -- it always runs a clean forced cycle. Use daily_cycle_brief.py for
the full daily brief+Telegram path.

    .venv\\Scripts\\python.exe _run_forced_cycle.py
"""
import asyncio

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass


async def main() -> int:
    from backend.database.init_db import init_db
    from backend.data.collector import collect_data
    from backend.main import trigger_cycle

    await init_db()
    print("DATA SWEEP…", flush=True)
    try:
        await collect_data()
    except Exception as e:  # data sweep is best-effort; the cycle can use cached snapshot
        print(f"WARN collect_data: {e}", flush=True)
    print("AI CYCLE (forced)…", flush=True)
    await trigger_cycle("forced")
    print("KRW_FORCED_CYCLE_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
