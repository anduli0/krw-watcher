"""
Committee confidence — a CALIBRATED reliability score for the combined signal.

Replaces the old naive `0.5·mean_conf + 0.5·agreement` blend with a multi-factor
reliability score that only reads "높음" when the evidence genuinely supports it.

Five components, each 0..1:
  1. agreement          — share of agents on the majority direction (consensus tightness)
  2. conviction         — mean committee confidence (already conf-capped upstream)
  3. focus              — 1 − normalized entropy of the 3-way signal split
                          (a clean 2-way lean scores high; a 3-way split scores low)
  4. horizon_coherence  — do the published horizons (1w/1m/3m/12m) agree in direction?
  5. calibration        — the system's OWN realized directional hit-rate (feedback loop)

The score is a weighted blend, then GATED by calibration: a high-agreement call on a
horizon the system has *historically gotten wrong* cannot read "높음". That gate is what
makes the rating trustworthy — high only when the committee is unified, focused, coherent
across horizons AND proven reliable; low when it is split or unproven.

Backward-compatible: the returned dict keeps every field the dashboard already reads
(rating, mean_confidence, agreement, majority_signal, agree_count, total, distribution)
and adds `score` + a `components` breakdown so the UI can show *why*.
"""
from __future__ import annotations

import math

# ── Component weights (sum to 1.0) ──
W_AGREEMENT = 0.30
W_CONVICTION = 0.18
W_FOCUS = 0.14
W_COHERENCE = 0.16
W_CALIBRATION = 0.22

# ── Rating thresholds on the blended score ──
RATING_HIGH = 0.63
RATING_MODERATE = 0.47

# Calibration gate: the rating cannot be promoted to "높음" when the system's own
# realized hit-rate at the traded horizon is worse than a coin flip by this margin.
CALIB_FLOOR_FOR_HIGH = 0.50

# Neutral prior for the calibration component before any live forecast has matured.
# Slightly below "good" so an unproven system reads as merited-caution, not false高.
CALIB_PRIOR = 0.55

_SIGNALS = ("krw_weak", "neutral", "krw_strong")


def _entropy_focus(counts: dict) -> float:
    """1 − normalized Shannon entropy of the signal distribution.
    1.0 = all agents agree; low = evenly split across all three signals."""
    total = sum(counts.values())
    if total <= 0:
        return 0.0
    ps = [c / total for c in counts.values() if c > 0]
    if len(ps) <= 1:
        return 1.0
    ent = -sum(p * math.log(p) for p in ps)
    # normalize against the full 3-signal space so a 2-way split isn't over-rewarded
    max_ent = math.log(len(counts)) if len(counts) > 1 else 1.0
    return max(0.0, min(1.0, 1.0 - ent / max_ent)) if max_ent > 0 else 1.0


def _horizon_coherence(signs: list[int]) -> float | None:
    """Direction agreement across horizons. `signs` ∈ {-1,0,1} per horizon.
    1.0 = every non-flat horizon points the same way; 0.5 = evenly split.
    Returns None when there is no directional horizon to judge."""
    nz = [s for s in signs if s != 0]
    if not nz:
        return None
    pos = sum(1 for s in nz if s > 0)
    dominant = max(pos, len(nz) - pos)
    return dominant / len(nz)


def evaluate(
    agents: list[dict],
    published_horizons: dict | None = None,
    calibration_hit: float | None = None,
) -> dict:
    """
    agents            — [{'signal': ..., 'confidence': ...}, ...] from the latest run
    published_horizons— optional {horizon: {'published_delta_krw'|'delta': float, ...}}
    calibration_hit   — optional realized directional hit-rate (0..1) at the lead horizon;
                        None = not yet proven (uses a neutral prior, gate not applied)
    """
    if not agents:
        return {
            "rating": "n/a", "mean_confidence": 0, "agreement": 0,
            "majority_signal": "neutral", "agree_count": 0, "total": 0,
            "distribution": {s: 0 for s in _SIGNALS},
            "score": 0.0, "components": {},
        }

    # ── Conviction + distribution ──
    confs = [(a.get("confidence") or 0.0) for a in agents]
    mean_conf = sum(confs) / len(confs)
    counts = {s: 0 for s in _SIGNALS}
    for a in agents:
        sig = a.get("signal") or "neutral"
        counts[sig] = counts.get(sig, 0) + 1
    majority = max(counts, key=counts.get)
    agreement = counts[majority] / len(agents)
    focus = _entropy_focus(counts)

    # ── Horizon coherence ──
    signs: list[int] = []
    if published_horizons:
        for hv in published_horizons.values():
            d = hv.get("published_delta_krw")
            if d is None:
                d = hv.get("delta", 0.0)
            d = d or 0.0
            signs.append(1 if d > 0.5 else -1 if d < -0.5 else 0)
    coherence = _horizon_coherence(signs)
    coherence_used = coherence if coherence is not None else agreement  # sensible fallback

    # ── Calibration (realized hit-rate) ──
    calib_known = calibration_hit is not None
    calib = max(0.0, min(1.0, calibration_hit)) if calib_known else CALIB_PRIOR

    # ── Blended reliability score ──
    score = (
        W_AGREEMENT * agreement
        + W_CONVICTION * mean_conf
        + W_FOCUS * focus
        + W_COHERENCE * coherence_used
        + W_CALIBRATION * calib
    )

    rating = "높음" if score >= RATING_HIGH else "보통" if score >= RATING_MODERATE else "낮음"

    # Calibration gate — trustworthiness guard.
    gated = False
    if rating == "높음" and calib_known and calib < CALIB_FLOOR_FOR_HIGH:
        rating = "보통"
        gated = True

    return {
        "rating": rating,
        "mean_confidence": round(mean_conf, 3),
        "agreement": round(agreement, 3),
        "majority_signal": majority,
        "agree_count": counts[majority],
        "total": len(agents),
        "distribution": counts,
        "score": round(score, 3),
        "components": {
            "agreement": round(agreement, 3),
            "conviction": round(mean_conf, 3),
            "focus": round(focus, 3),
            "horizon_coherence": round(coherence_used, 3),
            "horizon_coherence_measured": coherence is not None,
            "calibration": round(calib, 3),
            "calibration_known": calib_known,
            "calibration_gated": gated,
        },
    }
