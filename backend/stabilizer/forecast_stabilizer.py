"""
5-stage volatility-adaptive forecast stabilizer (ported from Fed-Watcher, won-scaled).

Prevents the published USD/KRW path from whipsawing on noisy single-cycle output, while
still reacting fast on event days. Money-critical: a stable published path is what the
trade layer sizes against.

Stage 0: bypass_ema — quantize raw directly (forced/session cycles).
Stage 1: adaptive EMA — alpha by regime: first_run > event > volatility(low/normal/high).
Stage 2: sub-quantum gate — sub-0.5원 changes are always noise → hold.
Stage 3: conviction gate — confidence < floor blocks updates (event days bypass).
Stage 4: quantize (1원) + change detection + unchanged-streak.
"""
from dataclasses import dataclass
from typing import Optional
import statistics

# Adaptive EMA alphas (weight on the NEW reading). Tuned higher = bolder: the published
# path tracks the committee's actual view more closely rather than hugging history.
ALPHA_LOW_VOL   = 0.30    # stable regime → still anchor somewhat
ALPHA_NORMAL    = 0.45    # baseline (was 0.32 — too sticky/cautious)
ALPHA_HIGH_VOL  = 0.62    # volatile → respond fast
ALPHA_EVENT     = 0.80    # scheduled catalyst day → react hard
ALPHA_FIRST_RUN = 0.92    # cold start

# Volatility regime thresholds — stdev (won) of recent raw 1m deltas across cycles.
VOL_LOW_THRESHOLD  = 5.0
VOL_HIGH_THRESHOLD = 20.0

# Gates / quantization (won).
MIN_CONFIDENCE = 0.45     # publish more moves rather than holding (bolder; was 0.50)
QUANTIZE_KRW   = 1.0
HALF_QUANTUM   = QUANTIZE_KRW / 2


@dataclass
class StabilizationResult:
    raw_delta: float
    smoothed_delta: float
    published_delta: float
    changed: bool
    unchanged_streak: int
    alpha_used: float = 0.0
    regime: str = "normal"     # low_vol|normal|high_vol|event|first_run|bypass


def _adaptive_alpha(recent: Optional[list[float]], is_event: bool,
                    is_first_run: bool, event_alpha: Optional[float]) -> tuple[float, str]:
    if is_first_run:
        return ALPHA_FIRST_RUN, "first_run"
    if is_event:
        return (event_alpha if event_alpha is not None else ALPHA_EVENT), "event"
    if recent and len(recent) >= 3:
        vol = statistics.stdev(recent)
        if vol < VOL_LOW_THRESHOLD:
            return ALPHA_LOW_VOL, "low_vol"
        if vol > VOL_HIGH_THRESHOLD:
            return ALPHA_HIGH_VOL, "high_vol"
    return ALPHA_NORMAL, "normal"


def _hold(raw, smoothed, prev, streak, alpha, regime) -> StabilizationResult:
    return StabilizationResult(raw, smoothed, prev, False, streak + 1, alpha, regime)


def stabilize(
    new_raw_delta: float,
    new_confidence: float,
    prev_published_delta: float,
    prev_streak: int,
    event: dict | None = None,
    bypass_ema: bool = False,
    recent_raw_deltas: list[float] | None = None,
) -> StabilizationResult:
    # Stage 0
    if bypass_ema:
        rounded = round(new_raw_delta / QUANTIZE_KRW) * QUANTIZE_KRW
        changed = rounded != prev_published_delta
        return StabilizationResult(new_raw_delta, new_raw_delta, rounded, changed,
                                   0 if changed else prev_streak + 1, 1.0, "bypass")

    # Stage 1
    first_run = (prev_streak == 0 and prev_published_delta == 0.0)
    event_alpha = event.get("alpha") if event else None
    alpha, regime = _adaptive_alpha(recent_raw_deltas, bool(event), first_run, event_alpha)
    smoothed = alpha * new_raw_delta + (1 - alpha) * prev_published_delta
    delta_from_prev = abs(smoothed - prev_published_delta)

    # Stage 2: sub-quantum gate
    if delta_from_prev < HALF_QUANTUM:
        return _hold(new_raw_delta, smoothed, prev_published_delta, prev_streak, alpha, regime)

    # Stage 3: conviction gate (event days bypass)
    if not event and new_confidence < MIN_CONFIDENCE:
        return _hold(new_raw_delta, smoothed, prev_published_delta, prev_streak, alpha, regime)

    # Stage 4: quantize + change detect
    rounded = round(smoothed / QUANTIZE_KRW) * QUANTIZE_KRW
    changed = rounded != prev_published_delta
    streak = 0 if changed else prev_streak + 1
    return StabilizationResult(new_raw_delta, round(smoothed, 2), rounded, changed, streak, alpha, regime)
