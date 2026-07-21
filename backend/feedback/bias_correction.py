"""
Automatic accuracy-feedback loop — per-horizon bias / scale / confidence correction.

Closes the loop the user asked for: the system's OWN realized errors (published
committee forecast vs what USD/KRW actually did) are turned into corrections applied
to FUTURE forecasts, recomputed every cycle so it self-tunes.

What it learns per horizon, from matured committee forecasts:
  • bias_krw   — systematic directional miss (mean predicted − realized) → subtracted.
  • scale      — magnitude calibration (regress realized on predicted) → multiplied.
  • conf_scale — confidence re-scaling from the realized directional hit-rate.

Cold start: until enough committee forecasts mature, the bias prior is SEEDED from the
walk-forward simulator's own recent out-of-sample residual (same forces, real USD/KRW),
half-weighted and hard-clamped — an honest error signal, not an invented one. conf_cap
stays active throughout: a per-horizon confidence ceiling reflecting how forecastable
each horizon actually is (1-week ≈ random-walk → capped hardest). As real matured
forecasts accumulate, the loop shifts from sim-prior → data-driven (w_real = n/(n+K)).

Applied in main.trigger_cycle just before stabilization; surfaced on the dashboard.
"""
import logging
import time
from datetime import datetime, timedelta

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import crud
from backend.database.models import HorizonForecast
from backend.feedback.feedback_loop import HORIZON_DAYS

logger = logging.getLogger("krw_watcher.biascorr")

MIN_SAMPLES = 6           # below this, lean on the sim residual prior (not identity)
SIM_PRIOR_W = 0.5         # cold-start: half-weight the walk-forward residual as a prior
BLEND_K = 10              # w_real = n / (n + K): trust grows with sample count
SCALE_CLAMP = (0.6, 1.3)  # never flip sign or amplify wildly
BIAS_CLAMP_FRAC = 0.8     # |bias| ≤ 0.8 × typical realized move at that horizon
MATURED_SCAN = 300        # cap matured-forecast scan (recency-weighted)
_CACHE_TTL = 600          # adjustments change slowly — cache 10 min (cycle + dashboard)
_CACHE: dict = {"t": 0.0, "data": None}
# Confidence ceilings by measured forecastability (1w ≈ coin-flip → capped hardest).
CONF_CAP = {"1w": 0.62, "1m": 0.76, "3m": 0.84, "12m": 0.84}


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


async def _matured_pairs(db: AsyncSession, h: str, now: datetime) -> list[tuple[float, float]]:
    """(predicted_delta, realized_delta) for committee forecasts whose horizon elapsed."""
    days = HORIZON_DAYS[h]
    cutoff = now - timedelta(days=days)
    rows = (await db.execute(
        select(HorizonForecast)
        .where(HorizonForecast.horizon == h,
               HorizonForecast.is_published.is_(True),
               HorizonForecast.published_at <= cutoff,
               HorizonForecast.spot_at_run.isnot(None))
        .order_by(desc(HorizonForecast.published_at)).limit(MATURED_SCAN))).scalars().all()
    pairs: list[tuple[float, float]] = []
    for r in rows:
        if r.published_delta is None or r.spot_at_run is None or r.published_at is None:
            continue
        target = (r.published_at.date() + timedelta(days=days)).isoformat()
        rr = await crud.get_realized_on_or_after(db, target)
        if not rr:
            continue
        pairs.append((r.published_delta, rr.rate - r.spot_at_run))
    return pairs


def _scale_through_origin(pairs: list[tuple[float, float]]) -> float:
    sp = sum(p * p for p, _ in pairs)
    return (sum(p * a for p, a in pairs) / sp) if sp else 1.0


async def compute_horizon_adjustments(db: AsyncSession, use_cache: bool = True) -> dict:
    """Per-horizon {bias_krw, scale, conf_scale, conf_cap, n_real, source}. TTL-cached so the
    cycle and the 15s dashboard refresh don't recompute (matured data changes slowly)."""
    now0 = time.time()
    if use_cache and _CACHE["data"] is not None and (now0 - _CACHE["t"] < _CACHE_TTL):
        return _CACHE["data"]
    # rw_mae prior (typical move size) from the cached simulation, for bias clamping.
    rw = {}
    sim_recent = {}
    try:
        from backend.backtest.forecast_sim import get_sim
        sim = await get_sim()
        for h, sd in (sim.get("horizons") or {}).items():
            rw[h] = ((sd.get("calibrated") or {}).get("rw_mae_krw")) or None
            sim_recent[h] = sd.get("recent")    # {resid_bias_krw, n, real_move_krw} or None
    except Exception:
        sim = {}
    now = datetime.utcnow()
    out: dict = {}
    for h in HORIZON_DAYS:
        cap = CONF_CAP.get(h, 0.85)
        dir_hit = None   # realized directional hit-rate (only known once enough forecasts mature)
        n_dir = 0
        try:
            pairs = await _matured_pairs(db, h, now)
        except Exception as e:
            logger.warning("matured-pairs %s failed: %s", h, e)
            pairs = []
        n = len(pairs)
        if n >= MIN_SAMPLES:
            mean_pred = sum(p for p, _ in pairs) / n
            mean_real = sum(a for _, a in pairs) / n
            real_bias = mean_pred - mean_real
            real_scale = _clamp(_scale_through_origin(pairs), *SCALE_CLAMP)
            w = n / (n + BLEND_K)
            cap_krw = BIAS_CLAMP_FRAC * (rw.get(h) or 50.0)
            bias = _clamp(w * real_bias, -cap_krw, cap_krw)
            scale = w * real_scale + (1 - w) * 1.0
            dirn = [(p, a) for p, a in pairs if abs(p) > 1]
            hit = (sum(1 for p, a in dirn if p * a > 0) / len(dirn)) if dirn else 0.5
            conf_scale = _clamp(0.45 + hit, 0.7, 1.05)
            if dirn:
                dir_hit, n_dir = round(hit, 3), len(dirn)
            source = f"live · n={n}"
        else:
            # No matured committee forecasts yet → don't sit at identity. Seed from the
            # walk-forward simulator's OWN recent residual (a real out-of-sample error
            # signal from the same forces), half-weighted and hard-clamped. Decays to the
            # live data automatically once n ≥ MIN_SAMPLES.
            rec = sim_recent.get(h)
            if rec and rec.get("n") and rec.get("resid_bias_krw") is not None:
                cap_krw = BIAS_CLAMP_FRAC * (rw.get(h) or 50.0)
                bias = _clamp(SIM_PRIOR_W * rec["resid_bias_krw"], -cap_krw, cap_krw)
                scale, conf_scale = 1.0, 1.0
                source = f"sim-prior · resid={rec['resid_bias_krw']}krw (n={rec['n']})"
            else:
                bias, scale, conf_scale = 0.0, 1.0, 1.0
                source = f"prior · n={n} (awaiting matured forecasts)"
        out[h] = {"bias_krw": round(bias, 2), "scale": round(scale, 3),
                  "conf_scale": round(conf_scale, 3), "conf_cap": cap,
                  "n_real": n, "dir_hit": dir_hit, "n_dir": n_dir, "source": source}
    _CACHE.update(t=now0, data=out)
    return out


def apply(adj: dict | None, h: str, delta: float, confidence: float) -> tuple[float, float, dict]:
    """Apply one horizon's adjustment. Returns (corrected_delta, corrected_conf, info)."""
    a = (adj or {}).get(h) or {}
    corr_delta = (delta - a.get("bias_krw", 0.0)) * a.get("scale", 1.0)
    corr_conf = min(a.get("conf_cap", 1.0), confidence * a.get("conf_scale", 1.0))
    return round(corr_delta, 2), round(corr_conf, 3), a
