"""Accuracy & backtest API — the precision/validation surface."""
from fastapi import APIRouter
from backend.database.init_db import AsyncSessionLocal
from backend.accuracy.metrics import forecast_accuracy, trading_performance

router = APIRouter(prefix="/api", tags=["accuracy"])

_LAST_BACKTEST: dict | None = None


@router.get("/accuracy")
async def get_accuracy():
    async with AsyncSessionLocal() as db:
        fc = await forecast_accuracy(db)
        tp = await trading_performance(db)
    return {"forecast": fc, "trading": tp}


@router.post("/backtest")
async def run_backtest_endpoint(years: int = 12, lookback: int = 20, horizon: str = "1m",
                                target_pct: float = 0.012, stop_pct: float = 0.008):
    from backend.backtest.history import fetch_usdkrw_history
    from backend.backtest.engine import run_backtest
    global _LAST_BACKTEST
    history = await fetch_usdkrw_history(years=years)
    if not history:
        return {"ok": False, "detail": "no history (set FRED_API_KEY)"}
    res = run_backtest(history, lookback=lookback, horizon=horizon,
                       target_pct=target_pct, stop_pct=stop_pct)
    _LAST_BACKTEST = res.as_dict()
    _LAST_BACKTEST["history_points"] = len(history)
    _LAST_BACKTEST["span"] = f"{history[0][0]} → {history[-1][0]}"
    return {"ok": True, "result": _LAST_BACKTEST}


@router.get("/backtest")
async def get_last_backtest():
    return {"result": _LAST_BACKTEST}


@router.get("/accuracy/simulation")
async def get_accuracy_simulation():
    """Walk-forward forecast-accuracy verdict over 15y of real USD/KRW (per horizon)."""
    from backend.accuracy.track import simulation_summary
    async with AsyncSessionLocal() as db:
        return await simulation_summary(db)


@router.get("/accuracy/track")
async def get_accuracy_track(horizon: str = "1m"):
    """Predicted-vs-actual time series (sim backfill + live matured forecasts) + the
    current auto-correction the feedback loop is applying for this horizon."""
    from backend.accuracy.track import predicted_vs_actual
    async with AsyncSessionLocal() as db:
        return await predicted_vs_actual(db, horizon)


@router.get("/daily-ohlc")
async def get_daily_ohlc():
    """Today's predicted High/Low/Close + the scored predicted-vs-actual track record and
    its band-calibration feedback state (a daily quant forecast, separate from the committee)."""
    from backend.accuracy.daily_ohlc import daily_summary
    async with AsyncSessionLocal() as db:
        return await daily_summary(db)
