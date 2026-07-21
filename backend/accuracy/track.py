"""
Predicted-vs-actual track record — the data behind the dashboard's accuracy-history chart.

Two layers, on a shared (target-date) time axis:
  • sim  — walk-forward backtest of the calibrated quantitative proxy over 15y of real
           USD/KRW. Populated immediately so the chart is meaningful from day one.
  • live — the actual committee's published forecasts, scored against the realized rate
           once their horizon elapses. Empty at first; grows as forecasts mature.

Also exposes the current auto-correction state (bias_correction) so the dashboard can
show exactly what the feedback loop is applying.
"""
import time
from datetime import datetime, timedelta

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import crud
from backend.database.models import HorizonForecast
from backend.backtest.forecast_sim import get_sim
from backend.feedback.feedback_loop import HORIZON_DAYS
from backend.feedback.bias_correction import compute_horizon_adjustments

REALIZED_MIN = 200          # below this we do a full backfill from FRED
_REFRESH_INTERVAL = 1800    # then a light refresh at most every 30 min
_LIVE_SCAN = 300            # cap matured-forecast scans (recency-weighted)
_last_ensure = {"t": 0.0}


async def ensure_realized_history(db: AsyncSession, years: int = 3, force: bool = False) -> int:
    """Keep the realized-rate table populated AND current. Full backfill when empty, then a
    cheap recent refresh (≤ every 30 min) so newly-matured forecasts can be scored — this
    is what keeps the auto-improvement loop alive going forward (not just at startup)."""
    n = await crud.count_realized(db)
    now = time.time()
    if not force and n >= REALIZED_MIN and (now - _last_ensure["t"] < _REFRESH_INTERVAL):
        return n
    _last_ensure["t"] = now
    try:
        from backend.backtest.history import fetch_usdkrw_current
        # Full history once; thereafter a 1y window. fetch_usdkrw_current appends yfinance's
        # recent daily closes so realized rates reach ~today (FRED DEXKOUS lags a few days).
        hist = await fetch_usdkrw_current(years=years if n < REALIZED_MIN else 1)
        if hist:
            await crud.upsert_realized_rates(db, hist)
    except Exception:
        pass
    return await crud.count_realized(db)


async def _live_points(db: AsyncSession, h: str) -> list[dict]:
    """Matured committee forecasts scored against the realized close at maturity."""
    days = HORIZON_DAYS[h]
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (await db.execute(
        select(HorizonForecast)
        .where(HorizonForecast.horizon == h,
               HorizonForecast.is_published.is_(True),
               HorizonForecast.published_at <= cutoff,
               HorizonForecast.spot_at_run.isnot(None))
        .order_by(desc(HorizonForecast.published_at)).limit(_LIVE_SCAN))).scalars().all()
    rows = list(reversed(rows))   # chronological for the chart
    pts = []
    for r in rows:
        if r.implied_rate is None or r.spot_at_run is None or r.published_at is None:
            continue
        target = (r.published_at.date() + timedelta(days=days)).isoformat()
        rr = await crud.get_realized_on_or_after(db, target)
        if not rr:
            continue
        pred_d = r.published_delta or 0.0
        real_d = rr.rate - r.spot_at_run
        pts.append({"date": target, "from": r.published_at.date().isoformat(),
                    "pred_rate": round(r.implied_rate, 2), "real_rate": round(rr.rate, 2),
                    "pred_delta": round(pred_d, 2), "real_delta": round(real_d, 2),
                    "hit": 1 if pred_d * real_d > 0 else 0})
    return pts


def _sim_metrics(sd: dict) -> dict:
    c = sd.get("calibrated") or {}
    ind = sd.get("independent") or {}
    return {"independent_hit": ind.get("dir_hit"), "independent_n": ind.get("dir_n"),
            "overlap_hit": c.get("dir_hit"), "mae_krw": c.get("mae_krw"),
            "ic": c.get("ic"), "skill_vs_rw": c.get("skill_vs_rw"),
            "rw_mae_krw": c.get("rw_mae_krw"), "n": sd.get("n")}


async def predicted_vs_actual(db: AsyncSession, horizon: str = "1m") -> dict:
    if horizon not in HORIZON_DAYS:
        horizon = "1m"
    await ensure_realized_history(db)
    sim = await get_sim()
    sd = (sim.get("horizons") or {}).get(horizon, {})
    live = await _live_points(db, horizon)
    live_metrics = None
    if live:
        dirn = [p for p in live if abs(p["pred_delta"]) > 1]
        live_metrics = {
            "n": len(live),
            "dir_hit": round(sum(p["hit"] for p in dirn) / len(dirn), 3) if dirn else None,
            "mae_krw": round(sum(abs(p["pred_delta"] - p["real_delta"]) for p in live) / len(live), 2),
        }
    adj = {}
    try:
        adj = (await compute_horizon_adjustments(db)).get(horizon, {})
    except Exception:
        pass
    return {
        "horizon": horizon,
        "sim": {"series": sd.get("series", []), "metrics": _sim_metrics(sd) if sd else {},
                "span": sim.get("span")},
        "live": {"points": live, "metrics": live_metrics},
        "adjustment": adj,
    }


async def simulation_summary(db: AsyncSession | None = None) -> dict:
    sim = await get_sim()
    out = {"span": sim.get("span"), "points": sim.get("points"), "horizons": {}}
    for h, sd in (sim.get("horizons") or {}).items():
        out["horizons"][h] = _sim_metrics(sd) if "calibrated" in sd else {"n": sd.get("n", 0)}
    return out
