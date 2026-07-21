"""Daily krw-watcher job for GitHub Actions — no server, no Render.

Runs the same pipeline the Render deployment ran, but on the Actions runner,
then snapshots every read-only endpoint the dashboard calls into static JSON
under _site/data/. The single-file dashboard (backend/static/index.html) is
copied into _site/ and, when served from *.github.io, reads those JSON files
instead of a live API (see the static adapter in index.html). Result: the exact
same dashboard, served by GitHub Pages with zero backend — so the free-tier
750-hour Render limit no longer applies.

MODE env selects what runs (default: full):
  full      collect data + AI committee cycle (+ daily report) + export
  refresh   collect data + export   — no AI tokens (market-data freshness)
  export    export only             — re-publish from the stored DB
"""
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# CI defaults — set before any backend import reads them.
os.environ.setdefault("DEV_MODE", "true")            # skip MAC hardware lock + JWT
os.environ.setdefault("ALLOWED_IPS", "*")
os.environ.setdefault(
    "DATABASE_URL",
    f"sqlite+aiosqlite:///{(ROOT / 'data' / 'krw_watcher.db').as_posix()}",
)
(ROOT / "data").mkdir(exist_ok=True)

STATIC_SRC = ROOT / "backend" / "static"
SITE_DIR = ROOT / "_site"
DATA_DIR = SITE_DIR / "data"

MODE = os.getenv("MODE", "full").strip().lower()

# GET endpoints the dashboard's get() helper calls. The output filename mirrors
# the static adapter in index.html: path with '/' -> '_', plus '_<horizon>' when
# a horizon query param is present. Keep these two in sync.
GET_ENDPOINTS = [
    ("/health", "health.json"),
    ("/api/forecast", "api_forecast.json"),
    ("/api/signal", "api_signal.json"),
    ("/api/agents", "api_agents.json"),
    ("/api/accuracy", "api_accuracy.json"),
    ("/api/daily-ohlc", "api_daily-ohlc.json"),
    ("/api/hierarchy", "api_hierarchy.json"),
    ("/api/news", "api_news.json"),
    ("/api/briefing/latest", "api_briefing_latest.json"),
    ("/api/briefing/list", "api_briefing_list.json"),
    ("/api/activity?after=0", "api_activity.json"),
]
HORIZONS = ("1w", "1m", "3m", "12m")
for _h in HORIZONS:
    GET_ENDPOINTS.append(
        (f"/api/accuracy/track?horizon={_h}", f"api_accuracy_track_{_h}.json"))

# POST endpoints the dashboard triggers on-demand (button clicks). Snapshotted so
# the static site can serve them read-only; the query mirrors index.html exactly.
POST_ENDPOINTS = [
    ("/api/backtest?years=12&lookback=20&horizon=1m", "api_backtest_1m.json"),
]


def _now_kst():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Seoul"))


async def run_pipeline():
    from backend.main import run_data_collection, trigger_cycle, run_daily_report_guarantee
    await run_data_collection()
    if MODE == "full":
        await trigger_cycle("scheduled")
        # Guarantee today's report/brief once past the morning hour (idempotent).
        if _now_kst().hour >= 7:
            try:
                await run_daily_report_guarantee()
            except Exception as e:
                print(f"[ci_daily] daily report guarantee failed (non-fatal): {e}")


async def export_static():
    import httpx
    from backend.main import app

    def write(rel: str, payload) -> None:
        path = DATA_DIR / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        print(f"[ci_daily] exported data/{rel} ({path.stat().st_size} B)")

    failures = []
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://ci",
                                 timeout=120) as client:
        for url, rel in GET_ENDPOINTS:
            try:
                r = await client.get(url)
                if r.status_code == 200:
                    write(rel, r.json())
                else:
                    failures.append(f"GET {url} -> {r.status_code}")
            except Exception as e:
                failures.append(f"GET {url} -> {e!r}")
        for url, rel in POST_ENDPOINTS:
            try:
                r = await client.post(url)
                if r.status_code == 200:
                    write(rel, r.json())
                else:
                    failures.append(f"POST {url} -> {r.status_code}")
            except Exception as e:
                failures.append(f"POST {url} -> {e!r}")

    if failures:
        print("[ci_daily] endpoints that failed to export:")
        for f in failures:
            print("  ", f)

    # The forecast is the one payload the dashboard cannot render without.
    if not (DATA_DIR / "api_forecast.json").exists():
        raise SystemExit("[ci_daily] FATAL: api_forecast.json was not exported")


def assemble_site():
    """Copy the single-file dashboard + assets into _site/ (data/ already written)."""
    SITE_DIR.mkdir(exist_ok=True)
    for name in ("index.html", "favicon.svg"):
        src = STATIC_SRC / name
        if src.exists():
            shutil.copy2(src, SITE_DIR / name)
            print(f"[ci_daily] copied {name}")
    # Disable Jekyll so files/dirs starting with '_' are served verbatim.
    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")


async def main():
    print(f"[ci_daily] mode={MODE}")
    from backend.database.init_db import init_db
    await init_db()
    if MODE != "export":
        await run_pipeline()
    await export_static()
    assemble_site()
    print("[ci_daily] done")


if __name__ == "__main__":
    asyncio.run(main())
