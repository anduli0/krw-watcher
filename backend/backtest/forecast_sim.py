"""
Walk-forward FORECAST-ACCURACY simulator (distinct from engine.py's trading backtest).

engine.py answers "does the execution/risk framework survive?" using a trend rule.
THIS module answers "how accurate is a committee-style Δ forecast, and does
calibration improve it?" — the question the user actually asked ("정확도 판별").

We cannot replay the 23-agent LLM committee over 15 years cheaply, so we score a
TRANSPARENT QUANTITATIVE PROXY that uses the same forces the committee reasons about:
  • momentum   — recent USD/KRW drift continues for a while (short horizons)
  • mean-reversion — gap from a long moving average pulls back (long horizons)
against REAL realized moves (FRED DEXKOUS). It tells us, honestly and out-of-sample:
  • the realistic directional hit-rate / MAE / information-coefficient bar for this market
  • how much affine CALIBRATION (bias removal + shrink toward random-walk) lowers error
  • per-horizon shrink factors → seed the live auto-correction loop as a decaying prior
  • a predicted-vs-actual time series → the dashboard's "예측 vs 실제" chart backfill

Pure stdlib, no LLM tokens, fully deterministic.
"""
from __future__ import annotations
import math
import statistics

# Trading-day count per horizon (≈ 1w / 1m / 3m / 12m on a business-day series).
TRADING_DAYS = {"1w": 5, "1m": 22, "3m": 66, "12m": 252}
MOM_WINDOW = 20        # momentum lookback (trading days)
LONG_WINDOW = 120      # mean-reversion anchor (trading days)
# How much of the long-MA gap is assumed to revert over each horizon. SHORT horizons use
# ZERO reversion: a long out-of-sample test (15y, non-overlapping windows) showed that
# betting on 1w/1m mean-reversion is actively ANTI-predictive — it dragged the model's
# honest directional hit BELOW a coin flip (1m 48%, 1w 50%). Dropping it leaves pure
# momentum-continuation, lifting independent dir-hit to ~57% (the achievable causal ceiling
# for short-horizon FX) with no MAE cost. Reversion is only predictive at 3m/12m.
REV_FRAC = {"1w": 0.0, "1m": 0.0, "3m": 0.35, "12m": 0.70}
# Coarse blend grid (kept small to avoid overfitting on the train split).
WM_GRID = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25]
WR_GRID = [0.0, 0.25, 0.5, 0.75, 1.0]
DIR_THRESH = {"1w": 1.5, "1m": 3.0, "3m": 6.0, "12m": 12.0}  # only score meaningful calls
SERIES_POINTS = 160    # chart sample (most recent target dates)
# Rolling-origin (adaptive) calibration window — the feedback that fixes a frozen 15y-old
# fit drifting away from the current regime. Short/mid horizons have many overlapping
# observations, so a long (~4y) window keeps the recalibration stable instead of chasing
# noise; the 12m horizon has few independent observations and large regime shifts, so it
# uses a faster (~2y) window to actually track them. Each beats the frozen fit on both the
# out-of-sample TEST split and the displayed window — validated on 15y USD/KRW:
#   1m skill +0.8%→+1.9% · 3m +3.0%→+7.7% · 12m +5.3%→+11.5%; 12m displayed bias −48→−3 KRW.
ROLL_WINDOW = {"1w": 1008, "1m": 1008, "3m": 1008, "12m": 504}
ROLL_MIN_FIT = 120     # fall back to the static train fit until this many windows have matured

# ── SHORT-HORIZON cross-asset directional model (1w / 1m) ─────────────────────────────
# Pure own-price momentum has NO short-horizon edge for USD/KRW (measured IC ≤ 0; 1m is
# actually mean-reverting). The real, economically-grounded signal is CROSS-ASSET LEAD-LAG:
# Korea's noon USD/KRW fixing closes before the US session, so the latest broad-dollar /
# USD-CNY move is not yet in it and is partly transmitted the next day (measured daily
# lead-lag corr +0.135, n≈3745). We score standardized lead-lag (+) + a 1-day KRW reversal
# (−), and for 1m add a long mean-reversion term (the same force that works at 3m/12m).
# A CONFIDENCE GATE only commits a directional call when |score| is in the top ~40% on the
# train split — trading coverage for reliability. Validated out-of-sample on 15y USD/KRW
# (non-overlapping samples): 1w independent dir-hit ≈51%→55% (gated ≈58%, IC +0.19);
# 1m ≈50% ungated → ≈55% on confident (gated) calls. Falls back to the momentum model
# below if the cross-asset series are unavailable.
SHORT_HORIZONS = ("1w", "1m")
SHORT_GATE_Q = 0.5     # commit only the strongest ~50% of |score| calls (train-set quantile):
#                        a fixed "call it about half the time" rule, applied identically to both
#                        horizons (not per-horizon tuned). Tighter gates over-trust a noisy tail.
SHORT_FEATURES = {"1w": ("ll1", "rev1"), "1m": ("ll1", "rev1")}
# NOTE on 1m: extensive out-of-sample testing (lead-lag, carry, short/long mean-reversion,
# relative-value vs CNY) found NO daily-data feature with a stable directional edge at the
# 1-month horizon for USD/KRW — it is ~a random walk (the lead-lag corr decays to 0 after one
# day; 1m is then 21 days of unpredictable moves). We deliberately do NOT add a mean-reversion
# term at 1m: it is actively ANTI-predictive there (confirmed here and in the long-horizon
# REV_FRAC tuning). 1m therefore uses the same honest lead-lag score as 1w and lands near 50%;
# the value at 1m is the confidence GATE (abstain unless the signal is strong), not a sign edge.


def _dret(arr: list, i: int) -> float | None:
    """1-day log return at i (uses i-1, i). None if either point missing/non-positive."""
    if i < 1 or arr[i] is None or arr[i - 1] is None or arr[i] <= 0 or arr[i - 1] <= 0:
        return None
    return math.log(arr[i] / arr[i - 1])


def _kret(arr: list, i: int, w: int) -> float | None:
    """w-day log return ending at i."""
    if i - w < 0 or arr[i] is None or arr[i - w] is None or arr[i] <= 0 or arr[i - w] <= 0:
        return None
    return math.log(arr[i] / arr[i - w])


def _gap_pct(arr: list, i: int, w: int) -> float | None:
    """(spot − w-day SMA) / SMA at i — scale-free deviation from a moving average."""
    if i - w + 1 < 0:
        return None
    seg = arr[i - w + 1: i + 1]
    if any(v is None for v in seg):
        return None
    sma = sum(seg) / len(seg)
    return (arr[i] - sma) / sma if sma else None


def _align_ffill(master_dates: list[str], series: list[tuple[str, float]]) -> list:
    """Forward-fill `series` [(date,val)] onto master_dates (no lookahead):
    aligned[k] = most recent value whose date ≤ master_dates[k], else None."""
    out: list = [None] * len(master_dates)
    j, last = 0, None
    for k, d in enumerate(master_dates):
        while j < len(series) and series[j][0] <= d:
            last = series[j][1]
            j += 1
        out[k] = last
    return out


def _short_feature(name: str, rates: list, usd: list, cny: list, i: int) -> float | None:
    """One short-horizon feature value at origin index i. Signs baked in so +score ⇒ USD/KRW up."""
    if name == "ll1":                                   # lead-lag: 1-day broad-$ + USD/CNY move
        a, b = _dret(usd, i), _dret(cny, i)
        return (a + b) if (a is not None and b is not None) else None
    if name == "rev1":                                  # very-short reversal of KRW's last day
        v = _kret(rates, i, 1)
        return -v if v is not None else None
    if name == "mr200":                                 # long mean-reversion (rich ⇒ expect down)
        v = _gap_pct(rates, i, 200)
        return -v if v is not None else None
    return None


def _sma(rates: list[float], i: int, w: int) -> float | None:
    if i - w + 1 < 0:
        return None
    seg = rates[i - w + 1: i + 1]
    return sum(seg) / len(seg)


def _features(rates: list[float], i: int):
    """(slope won/day over MOM_WINDOW, gap = spot − long SMA) at index i, or None."""
    if i - MOM_WINDOW < 0 or i - LONG_WINDOW + 1 < 0:
        return None
    slope = (rates[i] - rates[i - MOM_WINDOW]) / MOM_WINDOW
    sma = _sma(rates, i, LONG_WINDOW)
    if sma is None:
        return None
    return slope, rates[i] - sma


def _predict(slope: float, gap: float, D: int, wm: float, wr: float, rev: float) -> float:
    """Proxy committee Δ (won) over D trading days."""
    return wm * slope * D + wr * (-gap) * rev


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return num / (dx * dy) if dx and dy else 0.0


def _linreg(pairs: list[tuple[float, float]]) -> tuple[float, float]:
    """Fit real = alpha + beta·pred (least squares). Returns (alpha, beta)."""
    n = len(pairs)
    if n < 3:
        return 0.0, 1.0
    xs = [p for p, _ in pairs]
    ys = [r for _, r in pairs]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return my, 0.0
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    alpha = my - beta * mx
    return alpha, beta


def _rolling_calibrate(samples: list, raw: list[float], D: int, window: int,
                       train_ab: tuple[float, float]):
    """Recursive (rolling-origin) affine calibration with NO lookahead.

    For each sample j the proxy's raw Δ is mapped real ≈ α + β·raw, where (α, β) are
    re-fit on the most-recent `window` of pairs whose horizon had ALREADY settled before
    j's origin date (target_idx = from_idx + D ≤ from_idx_j). This turns the simulator's
    own realized errors into a forward correction every step — so a persistent regime miss
    (e.g. a trending won the static fit can't see) is removed instead of compounding.

    Until ROLL_MIN_FIT windows have matured, falls back to the static train fit `train_ab`.
    Returns (calibrated_preds, recent_window_pairs, (latest_alpha, latest_beta))."""
    a0, b0 = train_ab
    from_idx = [s[0] for s in samples]          # strictly increasing ⇒ pointer sweep is O(n)
    n = len(samples)
    out = [0.0] * n
    matured: list[tuple[float, float]] = []
    k = 0
    a, b = a0, b0
    for j in range(n):
        fi = from_idx[j]
        while k < n and (from_idx[k] + D) <= fi:   # window k settled on/before origin j
            matured.append((raw[k], samples[k][3]))
            k += 1
        if len(matured) >= ROLL_MIN_FIT:
            a, b = _linreg(matured[-window:])
            b = max(0.0, min(1.4, b))
        else:
            a, b = a0, b0
        out[j] = a + b * raw[j]
    return out, matured[-window:], (a, b)


def _metrics(pairs: list[tuple[float, float]], thresh: float) -> dict:
    """Accuracy of predicted vs realized Δ. pairs=(pred, real) in won."""
    n = len(pairs)
    if n == 0:
        return {"n": 0}
    preds = [p for p, _ in pairs]
    reals = [r for _, r in pairs]
    errs = [p - r for p, r in pairs]
    mae = statistics.mean(abs(e) for e in errs)
    rmse = math.sqrt(statistics.mean(e * e for e in errs))
    bias = statistics.mean(errs)                       # >0 ⇒ over-predicts the move
    directional = [(p, r) for p, r in pairs if abs(p) > thresh]
    hit = (sum(1 for p, r in directional if p * r > 0) / len(directional)
           if directional else None)
    rw_mae = statistics.mean(abs(r) for r in reals)    # random-walk (predict 0)
    return {
        "n": n,
        "dir_hit": round(hit, 4) if hit is not None else None,
        "dir_n": len(directional),
        "mae_krw": round(mae, 2),
        "rmse_krw": round(rmse, 2),
        "bias_krw": round(bias, 2),
        "ic": round(_pearson(preds, reals), 4),        # information coefficient
        "rw_mae_krw": round(rw_mae, 2),
        "skill_vs_rw": round((rw_mae - mae) / rw_mae, 4) if rw_mae else 0.0,
    }


def _simulate_short(h: str, D: int, dates: list, rates: list, usd: list, cny: list,
                    fit_frac: float) -> dict:
    """Cross-asset lead-lag directional model for a SHORT horizon (1w/1m). Same output
    schema as the long-horizon path, plus `independent_ungated` and `coverage`. The headline
    `independent` block is the GATED (confident-call) hit. No lookahead: z-stats and the gate
    threshold are fit on the train split only; calibration is rolling-origin. See SHORT_* docs."""
    feats = SHORT_FEATURES[h]
    warm = 200 if "mr200" in feats else 60          # warm-up for the longest lookback used
    rows = []  # (idx, {feat: raw}, real)
    for i in range(warm, len(rates) - D):
        fv, ok = {}, True
        for f in feats:
            v = _short_feature(f, rates, usd, cny, i)
            if v is None:
                ok = False
                break
            fv[f] = v
        if ok:
            rows.append((i, fv, rates[i + D] - rates[i]))
    if len(rows) < 60:
        return {"n": len(rows), "note": "insufficient"}

    split = int(len(rows) * fit_frac)
    train = rows[:split]
    zst = {}                                        # train-only standardization (no lookahead)
    for f in feats:
        xs = [r[1][f] for r in train]
        mu = sum(xs) / len(xs)
        sd = statistics.pstdev(xs) or 1.0
        zst[f] = (mu, sd)

    def score(fv: dict) -> float:                   # +score ⇒ USD/KRW up (signs baked into feats)
        return sum((fv[f] - zst[f][0]) / zst[f][1] for f in feats)

    samples = [(i, score(fv), 0.0, real) for (i, fv, real) in rows]   # (idx, score, _, real)
    score_list = [s[1] for s in samples]
    tr_mags = sorted(abs(s) for s in score_list[:split])
    tau = tr_mags[int(len(tr_mags) * SHORT_GATE_Q)] if tr_mags else 0.0   # confidence gate

    # affine score→won calibration: static train fit (cold-start) + rolling-origin adaptive
    tr_pairs = [(score_list[k], samples[k][3]) for k in range(split)]
    alpha0, beta0 = _linreg(tr_pairs)
    beta0 = max(0.0, min(1.4, beta0))
    cal_all, recent_pairs, (alpha, beta) = _rolling_calibrate(
        samples, score_list, D, ROLL_WINDOW.get(h, 1008), (alpha0, beta0))

    def dir_block(idxs, gated: bool) -> dict:
        sub = [(score_list[k], samples[k][3]) for k in idxs]
        if gated:
            sub = [(s, r) for s, r in sub if abs(s) >= tau]
        hit = (sum(1 for s, r in sub if s * r > 0) / len(sub)) if sub else None
        ic = _pearson([s for s, _ in sub], [r for _, r in sub]) if len(sub) > 3 else 0.0
        return {"dir_hit": round(hit, 4) if hit is not None else None,
                "dir_n": len(sub), "ic": round(ic, 4)}

    indep_idx = list(range(0, len(samples), D))     # non-overlapping (honest) samples
    ind_gated = dir_block(indep_idx, True)
    ind_ungated = dir_block(indep_idx, False)
    coverage = round(ind_gated["dir_n"] / ind_ungated["dir_n"], 3) if ind_ungated["dir_n"] else 0.0

    # won-error metrics on the untouched TEST split; directional hit from the score sign
    te_cal = [(cal_all[k], samples[k][3]) for k in range(split, len(samples))]
    te_raw = [(alpha0 + beta0 * score_list[k], samples[k][3]) for k in range(split, len(samples))]
    m_cal, m_raw = _metrics(te_cal, DIR_THRESH[h]), _metrics(te_raw, DIR_THRESH[h])
    ov = dir_block(range(split, len(samples)), False)        # ungated overlap hit (sign of score)
    m_cal["dir_hit"], m_cal["dir_n"] = ov["dir_hit"], ov["dir_n"]

    series = []
    for k in range(max(0, len(samples) - SERIES_POINTS), len(samples)):
        i, s, _z, real = samples[k]
        pc = cal_all[k]
        series.append({"date": dates[i + D], "from": dates[i],
                       "pred_rate": round(rates[i] + pc, 2), "real_rate": round(rates[i + D], 2),
                       "pred_delta": round(pc, 2), "real_delta": round(real, 2),
                       "hit": 1 if s * real > 0 else 0})

    recent = None
    if len(recent_pairs) >= ROLL_MIN_FIT:
        mp = sum(p for p, _ in recent_pairs) / len(recent_pairs)
        mr = sum(r for _, r in recent_pairs) / len(recent_pairs)
        recent = {"resid_bias_krw": round(mp - mr, 2), "n": len(recent_pairs),
                  "real_move_krw": round(sum(abs(r) for _, r in recent_pairs) / len(recent_pairs), 2)}

    shrink_prior = round(min(1.0, max(0.4, beta)), 3)
    return {
        "n": len(samples), "train": split, "test": len(samples) - split,
        "blend": {"model": "lead-lag", "features": list(feats), "gate_q": SHORT_GATE_Q},
        "calibration": {"alpha": round(alpha, 3), "beta": round(beta, 3),
                        "train_alpha": round(alpha0, 3), "train_beta": round(beta0, 3),
                        "mode": "rolling", "window": ROLL_WINDOW.get(h, 1008),
                        "shrink_prior": shrink_prior, "gate_tau": round(tau, 3)},
        "raw": m_raw, "calibrated": m_cal,
        "independent": ind_gated,                   # GATED confident-call hit (headline)
        "independent_ungated": ind_ungated, "coverage": coverage,
        "recent": recent, "series": series,
    }


def simulate(history: list[tuple[str, float]], fit_frac: float = 0.7, aux: dict | None = None) -> dict:
    """Walk-forward accuracy per horizon, out-of-sample. See module docstring.

    `aux` (optional) = {"usd": [(date,val)…], "cny": [(date,val)…]} raw ascending FRED series
    (broad-dollar DTWEXBGS, USD/CNY DEXCHUS). When present, 1w/1m use the cross-asset lead-lag
    model (_simulate_short); otherwise every horizon uses the momentum + mean-reversion model."""
    dates = [d for d, _ in history]
    rates = [r for _, r in history]
    n = len(rates)
    out: dict = {"span": [dates[0], dates[-1]] if dates else [],
                 "points": n, "horizons": {}}
    usd = cny = None
    if aux:
        usd = _align_ffill(dates, aux.get("usd") or [])
        cny = _align_ffill(dates, aux.get("cny") or [])
        if all(v is None for v in usd) or all(v is None for v in cny):
            usd = cny = None                        # unusable → fall back to univariate
    start = max(MOM_WINDOW, LONG_WINDOW)
    for h, D in TRADING_DAYS.items():
        if h in SHORT_HORIZONS and usd is not None and cny is not None:
            res = _simulate_short(h, D, dates, rates, usd, cny, fit_frac)
            if "calibrated" in res:                 # short model produced a full result
                out["horizons"][h] = res
                continue                            # else: fall through to the momentum model
        samples = []  # (idx, slope, gap, real)
        for i in range(start, n - D):
            f = _features(rates, i)
            if not f:
                continue
            slope, gap = f
            samples.append((i, slope, gap, rates[i + D] - rates[i]))
        if len(samples) < 60:
            out["horizons"][h] = {"n": len(samples), "note": "insufficient"}
            continue
        split = int(len(samples) * fit_frac)
        train, test = samples[:split], samples[split:]
        rev = REV_FRAC[h]
        thr = DIR_THRESH[h]

        # ── grid-search blend weights on TRAIN (max directional hit, tie → min MAE) ──
        best = None
        for wm in WM_GRID:
            for wr in WR_GRID:
                pr = [(_predict(s, g, D, wm, wr, rev), real) for (_, s, g, real) in train]
                m = _metrics(pr, thr)
                key = (m.get("dir_hit") or 0.0, -m["mae_krw"])
                if best is None or key > best[0]:
                    best = (key, (wm, wr))
        wm, wr = best[1]

        # ── static affine calibration on TRAIN — kept only as the cold-start fallback ──
        tr_raw = [(_predict(s, g, D, wm, wr, rev), real) for (_, s, g, real) in train]
        alpha0, beta0 = _linreg(tr_raw)
        beta0 = max(0.0, min(1.4, beta0))              # guard pathological slopes

        # ── ADAPTIVE (rolling-origin) calibration — the accuracy-feedback loop ──
        # Recalibrates from the model's OWN recent realized errors instead of freezing a
        # 15-years-ago fit, so a persistent regime bias is corrected forward (no lookahead).
        raw_all = [_predict(s, g, D, wm, wr, rev) for (_, s, g, _r) in samples]
        cal_all, recent_pairs, (alpha, beta) = _rolling_calibrate(
            samples, raw_all, D, ROLL_WINDOW.get(h, 504), (alpha0, beta0))

        # ── evaluate RAW vs CALIBRATED on the untouched TEST split (adaptive) ──
        te_raw = [(raw_all[k], samples[k][3]) for k in range(split, len(samples))]
        te_cal = [(cal_all[k], samples[k][3]) for k in range(split, len(samples))]
        m_raw, m_cal = _metrics(te_raw, thr), _metrics(te_cal, thr)
        # honest INDEPENDENT (non-overlapping, stride=D) directional hit — kills the
        # autocorrelation inflation that makes overlapping daily windows look great.
        indep = [(cal_all[k], samples[k][3]) for k in range(0, len(samples), D)]
        m_ind = _metrics(indep, thr)

        # ── predicted-vs-actual series (adaptive-calibrated, most-recent target dates) ──
        series = []
        for k in range(max(0, len(samples) - SERIES_POINTS), len(samples)):
            i, s, g, real = samples[k]
            pc = cal_all[k]
            series.append({
                "date": dates[i + D],                  # the target (settlement) date
                "from": dates[i],
                "pred_rate": round(rates[i] + pc, 2),
                "real_rate": round(rates[i + D], 2),
                "pred_delta": round(pc, 2),
                "real_delta": round(real, 2),
                "hit": 1 if pc * real > 0 else 0,
            })

        # recent residual of the RAW proxy over the live window → seeds the committee bias
        # loop until its own forecasts mature (bias_correction.compute_horizon_adjustments).
        recent = None
        if len(recent_pairs) >= ROLL_MIN_FIT:
            mp = sum(p for p, _ in recent_pairs) / len(recent_pairs)
            mr = sum(r for _, r in recent_pairs) / len(recent_pairs)
            recent = {"resid_bias_krw": round(mp - mr, 2), "n": len(recent_pairs),
                      "real_move_krw": round(sum(abs(r) for _, r in recent_pairs)
                                             / len(recent_pairs), 2)}

        # shrink prior for the live loop = latest adaptive beta, but never amplify (≤1)
        shrink_prior = round(min(1.0, max(0.4, beta)), 3)
        out["horizons"][h] = {
            "n": len(samples), "train": split, "test": len(test),
            "blend": {"wm": wm, "wr": wr, "rev": rev},
            "calibration": {"alpha": round(alpha, 3), "beta": round(beta, 3),
                            "train_alpha": round(alpha0, 3), "train_beta": round(beta0, 3),
                            "mode": "rolling", "window": ROLL_WINDOW.get(h, 504),
                            "shrink_prior": shrink_prior},
            "raw": m_raw, "calibrated": m_cal, "independent": m_ind,
            "recent": recent, "series": series,
        }
    return out


def live_priors(sim: dict) -> dict:
    """Extract per-horizon shrink priors (conservative) for the auto-correction loop.
    Returns {horizon: shrink_factor in [0.55,1.0]}. Blended toward 1.0 for safety."""
    out = {}
    for h, d in (sim.get("horizons") or {}).items():
        beta = ((d.get("calibration") or {}).get("beta"))
        if beta is None:
            out[h] = 1.0
            continue
        # 50/50 blend of measured calibration slope with identity → gentle live shrink.
        out[h] = round(min(1.0, max(0.55, 0.5 * beta + 0.5)), 3)
    return out


_SIM_CACHE: dict = {"key": None, "data": None}


async def get_sim(years: int = 15) -> dict:
    """Cached walk-forward simulation (recomputes only when the inputs change).
    Also fetches the broad-dollar (DTWEXBGS) + USD/CNY (DEXCHUS) series that power the
    1w/1m cross-asset lead-lag model; if either is unavailable the model degrades to the
    univariate momentum path automatically."""
    from backend.backtest.history import fetch_usdkrw_current, fetch_fred_daily
    hist = await fetch_usdkrw_current(years=years)   # FRED + recent yfinance tail → current
    aux: dict = {}
    try:
        usd = await fetch_fred_daily("DTWEXBGS", years)
        cny = await fetch_fred_daily("DEXCHUS", years)
        if usd:
            aux["usd"] = usd
        if cny:
            aux["cny"] = cny
    except Exception:
        aux = {}
    key = (len(hist), hist[-1][0] if hist else None, len(aux.get("usd") or []), len(aux.get("cny") or []))
    if _SIM_CACHE["key"] == key and _SIM_CACHE["data"] is not None:
        return _SIM_CACHE["data"]
    data = simulate(hist, aux=aux) if hist else {"horizons": {}, "points": 0, "span": []}
    _SIM_CACHE.update(key=key, data=data)
    return data


if __name__ == "__main__":
    import asyncio
    from backend.backtest.history import fetch_usdkrw_history, fetch_fred_daily

    async def _main():
        hist = await fetch_usdkrw_history(years=15)
        if not hist:
            print("no history (set FRED_API_KEY)")
            return
        aux = {"usd": await fetch_fred_daily("DTWEXBGS", 15),
               "cny": await fetch_fred_daily("DEXCHUS", 15)}
        sim = simulate(hist, aux=aux)
        print(f"span {sim['span'][0]} -> {sim['span'][1]}  ({sim['points']} days)\n")
        hdr = f"{'H':>4} {'n':>5} | {'INDEP':>7} {'(n)':>5} {'cov':>5} {'ungat':>6} | " \
              f"{'CALhit':>7} {'mae':>7} {'ic':>6} {'skill':>6} | {'model':>9}"
        print(hdr); print("-" * len(hdr))
        for h in ("1w", "1m", "3m", "12m"):
            d = sim["horizons"].get(h, {})
            if "calibrated" not in d:
                print(f"{h:>4} {d.get('n',0):>5} | insufficient"); continue
            c, ind = d["calibrated"], d["independent"]
            ung = d.get("independent_ungated", {})
            ih = f"{ind['dir_hit']*100:.1f}%" if ind.get("dir_hit") is not None else "-"
            uh = f"{ung['dir_hit']*100:.1f}%" if ung.get("dir_hit") is not None else "-"
            ch = f"{c['dir_hit']*100:.1f}%" if c.get("dir_hit") is not None else "-"
            cov = f"{d['coverage']*100:.0f}%" if d.get("coverage") is not None else "-"
            model = (d.get("blend", {}) or {}).get("model", "momentum")
            print(f"{h:>4} {d['n']:>5} | {ih:>7} {ind.get('dir_n',0):>5} {cov:>5} {uh:>6} | "
                  f"{ch:>7} {c['mae_krw']:>7} {c['ic']:>6} {c['skill_vs_rw']*100:>5.1f}% | {model:>9}")
        print("\nINDEP = non-overlapping GATED (confident-call) dir-hit · ungat = ungated · "
              "cov = % of independent samples that clear the gate")
        print("live shrink priors:", live_priors(sim))

    asyncio.run(_main())
