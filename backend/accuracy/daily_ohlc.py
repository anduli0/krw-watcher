"""
Same-day USD/KRW High / Low / Close predictor + its own predicted-vs-actual feedback loop.

Distinct from the 23-agent horizon committee (Δ over 1w~12m). This is a transparent daily
QUANTITATIVE model — cheap, runs every data sweep, scored against each day's actual OHLC:
  • pred_close = prev_close + small momentum drift (daily close ≈ random-walk → drift small)
  • pred_high  = prev_close + band_mult × avg upside excursion (High − prev close)
  • pred_low   = prev_close − band_mult × avg downside excursion (prev close − Low)
band_mult is backtest-calibrated to a target coverage, then auto-tuned by the realized
close-in-band rate (the feedback loop the user asked for). All from yfinance daily OHLC.
"""
import time
from datetime import datetime

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import crud
from backend.database.models import DailyOHLCForecast, today_kst

LOOKBACK = 20            # days for avg up/down excursion
MOM_WIN = 5              # short momentum window (days)
DRIFT_GRID = [0.0, 0.1, 0.2, 0.3]
DRIFT_CAP_FRAC = 0.5     # |drift| ≤ 0.5 × expected range
TARGET_COVERAGE = 0.80   # predicted band should contain the close ~80% of days
BAND_CLAMP = (0.7, 2.2)
BACKFILL_DAYS = 120
_TICK_THROTTLE = 60      # daily_tick at most once / minute (dashboard refreshes are 15s)

_PARAMS: dict = {"key": None, "data": None}
_last_tick = {"t": 0.0}


# ── pure predictor ───────────────────────────────────────────────────────────────
def _avg_excursions(rows, i, lb):
    ups, downs = [], []
    for t in range(max(1, i - lb), i):
        pc = rows[t - 1][4]
        ups.append(max(0.0, rows[t][2] - pc))
        downs.append(max(0.0, pc - rows[t][3]))
    if not ups:
        return None
    return sum(ups) / len(ups), sum(downs) / len(downs)


def _momentum(rows, i, w):
    if i - 1 - w < 0:
        return 0.0
    return (rows[i - 1][4] - rows[i - 1 - w][4]) / w


def predict_day(rows, i, band_mult, drift_w):
    """Predict day i's OHLC from rows[:i] (rows = [(date,o,h,l,c)]). i may == len(rows)
    to predict the day AFTER the last row (it never indexes rows[i])."""
    if i < max(LOOKBACK, MOM_WIN) + 1:
        return None
    exc = _avg_excursions(rows, i, LOOKBACK)
    if not exc:
        return None
    up, down = exc
    prev_close = rows[i - 1][4]
    exp_range = up + down
    drift = drift_w * _momentum(rows, i, MOM_WIN)
    cap = DRIFT_CAP_FRAC * exp_range
    drift = max(-cap, min(cap, drift))
    pred_high = prev_close + band_mult * up
    pred_low = prev_close - band_mult * down
    pred_close = max(pred_low, min(pred_high, prev_close + drift))
    return {"prev_close": round(prev_close, 2), "pred_open": round(prev_close, 2),
            "pred_high": round(pred_high, 2), "pred_low": round(pred_low, 2),
            "pred_close": round(pred_close, 2), "exp_range": round(exp_range, 2),
            "band_mult": round(band_mult, 3)}


def _backtest(rows, fit_frac=0.7):
    n = len(rows)
    start = max(LOOKBACK, MOM_WIN) + 1
    idx = list(range(start, n))
    if len(idx) < 60:
        return None
    split = int(len(idx) * fit_frac)
    train, test = idx[:split], idx[split:]

    # drift weight: minimize close MAE on train (band_mult irrelevant to close)
    best_dw, best_mae = 0.0, 1e9
    for dw in DRIFT_GRID:
        es = [abs(predict_day(rows, i, 1.0, dw)["pred_close"] - rows[i][4])
              for i in train if predict_day(rows, i, 1.0, dw)]
        if es:
            m = sum(es) / len(es)
            if m < best_mae:
                best_mae, best_dw = m, dw

    def coverage(bm, sample):
        ins = tot = 0
        for i in sample:
            p = predict_day(rows, i, bm, best_dw)
            if not p:
                continue
            tot += 1
            if p["pred_low"] <= rows[i][4] <= p["pred_high"]:
                ins += 1
        return ins / tot if tot else 0.0

    lo, hi = BAND_CLAMP
    for _ in range(24):                       # bisection → band_mult hitting TARGET coverage
        mid = (lo + hi) / 2
        if coverage(mid, train) < TARGET_COVERAGE:
            lo = mid
        else:
            hi = mid
    bm = round((lo + hi) / 2, 3)

    ce, he, le, cov, rc, tot = [], [], [], 0, 0, 0
    for i in test:
        p = predict_day(rows, i, bm, best_dw)
        if not p:
            continue
        h_a, l_a, c_a = rows[i][2], rows[i][3], rows[i][4]
        tot += 1
        ce.append(abs(p["pred_close"] - c_a)); he.append(abs(p["pred_high"] - h_a))
        le.append(abs(p["pred_low"] - l_a))
        if p["pred_low"] <= c_a <= p["pred_high"]:
            cov += 1
        if h_a <= p["pred_high"] and l_a >= p["pred_low"]:
            rc += 1

    def mean(x):
        return round(sum(x) / len(x), 2) if x else None
    return {"band_mult": bm, "drift_w": best_dw, "n": len(idx), "test": tot,
            "close_mae": mean(ce), "high_mae": mean(he), "low_mae": mean(le),
            "coverage": round(cov / tot, 3) if tot else None,
            "range_contained": round(rc / tot, 3) if tot else None}


async def get_params():
    """(backtest dict, ohlc rows) — cached; recomputed only when the OHLC history changes."""
    from backend.backtest.history import fetch_recent_ohlc_yf
    rows = await fetch_recent_ohlc_yf(730)
    key = (len(rows), rows[-1][0] if rows else None)
    if _PARAMS["key"] == key and _PARAMS["data"] is not None:
        return _PARAMS["data"], rows
    bt = _backtest(rows) if len(rows) >= 80 else None
    _PARAMS.update(key=key, data=bt)
    return bt, rows


# ── feedback loop: nudge band_mult by realized close-in-band coverage ──────────────
async def effective_band_mult(db: AsyncSession, prior_bm: float):
    rows = (await db.execute(select(DailyOHLCForecast)
            .where(DailyOHLCForecast.close_in_band.isnot(None))
            .order_by(desc(DailyOHLCForecast.forecast_date)).limit(60))).scalars().all()
    n = len(rows)
    if n < 8:
        return round(prior_bm, 3), {"n": n, "realized_coverage": None}
    cov = sum(1 for r in rows if r.close_in_band) / n
    adj = prior_bm * (1.0 + 1.2 * (TARGET_COVERAGE - cov))   # tight band → widen, loose → narrow
    w = n / (n + 20)
    bm = (1 - w) * prior_bm + w * adj
    bm = max(BAND_CLAMP[0], min(BAND_CLAMP[1], bm))
    return round(bm, 3), {"n": n, "realized_coverage": round(cov, 3)}


# ── lifecycle: generate today · score matured · backfill history ──────────────────
async def ensure_today_forecast(db: AsyncSession):
    today = today_kst()
    ex = await crud.get_daily_ohlc(db, today)
    if ex:
        return ex
    bt, rows = await get_params()
    if not bt or not rows or len(rows) < LOOKBACK + 2:
        return None
    hist = rows[:-1] if rows[-1][0] >= today else rows   # drop today's partial bar if present
    if len(hist) < LOOKBACK + 2:
        return None
    eff_bm, _ = await effective_band_mult(db, bt["band_mult"])
    p = predict_day(hist, len(hist), eff_bm, bt["drift_w"])
    if not p:
        return None
    return await crud.save_daily_ohlc(db, {"forecast_date": today, **p})


async def score_daily_forecasts(db: AsyncSession) -> int:
    from backend.backtest.history import fetch_recent_ohlc_yf
    today = today_kst()
    unscored = await crud.get_unscored_daily_ohlc(db, today)
    if not unscored:
        return 0
    rows = await fetch_recent_ohlc_yf(730)
    ohlc = {r[0]: r for r in rows}
    done = 0
    for f in unscored:
        a = ohlc.get(f.forecast_date)
        if not a:
            continue
        _, _, h, l, c = a
        await crud.update_daily_ohlc(db, f.id, {
            "actual_high": h, "actual_low": l, "actual_close": c,
            "err_high": round(f.pred_high - h, 2), "err_low": round(f.pred_low - l, 2),
            "err_close": round(f.pred_close - c, 2),
            "close_in_band": bool(f.pred_low <= c <= f.pred_high),
            "range_contained": bool(h <= f.pred_high and l >= f.pred_low),
            "scored_at": datetime.utcnow()})
        done += 1
    return done


async def backfill(db: AsyncSession, days: int = BACKFILL_DAYS) -> int:
    bt, rows = await get_params()
    if not bt or not rows:
        return 0
    n = len(rows)
    start = max(LOOKBACK, MOM_WIN) + 1
    made = 0
    for i in range(max(start, n - days), n):
        d = rows[i][0]
        if await crud.get_daily_ohlc(db, d):
            continue
        p = predict_day(rows, i, bt["band_mult"], bt["drift_w"])
        if not p:
            continue
        _, _, h_a, l_a, c_a = rows[i]
        await crud.save_daily_ohlc(db, {"forecast_date": d, **p,
            "actual_high": h_a, "actual_low": l_a, "actual_close": c_a,
            "err_high": round(p["pred_high"] - h_a, 2), "err_low": round(p["pred_low"] - l_a, 2),
            "err_close": round(p["pred_close"] - c_a, 2),
            "close_in_band": bool(p["pred_low"] <= c_a <= p["pred_high"]),
            "range_contained": bool(h_a <= p["pred_high"] and l_a >= p["pred_low"]),
            "scored_at": datetime.utcnow()})
        made += 1
    return made


async def daily_tick(db: AsyncSession, force: bool = False):
    """Idempotent housekeeping: backfill once, score matured, ensure today's prediction."""
    now = time.time()
    if not force and (now - _last_tick["t"] < _TICK_THROTTLE):
        return
    _last_tick["t"] = now
    try:
        if not await crud.get_recent_daily_ohlc(db, 3):
            await backfill(db)
        await score_daily_forecasts(db)
        await ensure_today_forecast(db)
    except Exception:
        pass


def _row(r: DailyOHLCForecast) -> dict:
    return {"date": r.forecast_date, "prev_close": r.prev_close,
            "pred_open": r.pred_open, "pred_high": r.pred_high, "pred_low": r.pred_low,
            "pred_close": r.pred_close, "band_mult": r.band_mult, "exp_range": r.exp_range,
            "actual_high": r.actual_high, "actual_low": r.actual_low, "actual_close": r.actual_close,
            "err_close": r.err_close, "close_in_band": r.close_in_band,
            "range_contained": r.range_contained}


async def daily_summary(db: AsyncSession) -> dict:
    await daily_tick(db)
    bt, _ = await get_params()
    recent = await crud.get_recent_daily_ohlc(db, 140)
    today = await crud.get_daily_ohlc(db, today_kst())
    scored = [r for r in recent if r.actual_close is not None]

    def mae(attr_p, attr_a):
        xs = [abs(getattr(r, attr_p) - getattr(r, attr_a)) for r in scored
              if getattr(r, attr_p) is not None and getattr(r, attr_a) is not None]
        return round(sum(xs) / len(xs), 2) if xs else None
    ns = len(scored)
    live = {
        "n": ns,
        "close_mae": mae("pred_close", "actual_close"),
        "high_mae": mae("pred_high", "actual_high"),
        "low_mae": mae("pred_low", "actual_low"),
        "coverage": round(sum(1 for r in scored if r.close_in_band) / ns, 3) if ns else None,
        "range_contained": round(sum(1 for r in scored if r.range_contained) / ns, 3) if ns else None,
    }
    eff_bm, fb = await effective_band_mult(db, bt["band_mult"] if bt else 1.0)
    return {
        "today": _row(today) if today else None,
        "track": [_row(r) for r in recent],
        "live": live,
        "backtest": bt,
        "band": {"current": eff_bm, "prior": (bt["band_mult"] if bt else None),
                 "target_coverage": TARGET_COVERAGE, **fb},
    }


if __name__ == "__main__":
    import asyncio
    from backend.backtest.history import fetch_recent_ohlc_yf

    async def _m():
        rows = await fetch_recent_ohlc_yf(730)
        print(f"OHLC rows: {len(rows)}  ({rows[0][0]} → {rows[-1][0]})")
        bt = _backtest(rows)
        print("backtest:", bt)
        p = predict_day(rows, len(rows), bt["band_mult"], bt["drift_w"])
        print("today prediction:", p)
    asyncio.run(_m())
