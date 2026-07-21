"""
Trade-signal layer — the bridge from forecast to position (precision + measured aggression).

NOTIONAL BASIS (왜 이 노셔널인가):
    notional_usd = MAX_TRADE_NOTIONAL_USD × size_factor
    size_factor  = conviction × agreement × edge   (each 0–~1.3, product clamped to 1.0)
      • conviction = committee 1m confidence ramped from CONVICTION_FLOOR→FULL_SIZE_CONF
      • agreement  = how aligned the committee is (consensus tightness; from confidence_eval)
      • edge       = expected move ÷ volatility (a Sharpe-like quality of the setup)
    So the position scales with HOW SURE + HOW UNIFIED + HOW CLEAN the setup is, capped by
    your per-trade ceiling (MAX_TRADE_NOTIONAL_USD in .env). Portfolio risk limits then apply.

STOPS/TARGETS are volatility-aware (ATR-style): stop = STOP_VOL_MULT × realized daily vol,
target = the model's expected move (floored to keep R:R ≥ MIN_RR). This adapts to regime —
tight stops in calm tape, wider in volatile tape — instead of a fixed fraction of the move.

Tuned to be a bit more aggressive (lower floor, full size sooner, edge can amplify) without
being reckless (vol-aware stops, hard caps, portfolio risk manager downstream).

Sign reminder: delta_krw > 0 → USD/KRW up → LONG USD/KRW.
"""
from dataclasses import dataclass, asdict
from typing import Optional

from backend.config import settings

CONVICTION_FLOOR = 0.48          # below this confidence → FLAT (bolder)
FULL_SIZE_CONF = 0.74            # reach full conviction scaling here (ramps faster → bolder)
MIN_TRADEABLE_KRW = 4.0          # expected move below this → FLAT
TRADE_HORIZONS = ("1w", "1m", "3m")   # pick the cleanest of these to trade (12m too long)
STOP_VOL_MULT = 1.3              # stop distance = 1.3 × realized daily vol (ATR-style)
MIN_STOP_KRW = 4.0
MIN_RR = 1.3                     # ensure target is at least 1.3× the stop
BASE_SIZE = 0.55                 # floor fraction of cap once we DO trade (bolder aggression floor)
EDGE_FLOOR = 0.75               # edge multiplier floor (bolder)
EDGE_CAP = 1.4
EDGE_VOL_MULT = 1.5             # edge = |Δ| / (1.5 × vol) — easier to clear 1.0 (bolder)


@dataclass
class TradeDecision:
    horizon: str
    side: str
    spot_entry: Optional[float]
    target: Optional[float]
    stop: Optional[float]
    notional_usd: float
    confidence: float
    expected_delta_krw: float
    expected_edge_krw: float
    risk_reward: Optional[float]
    rationale: str
    sizing: Optional[dict] = None     # breakdown of the notional basis

    def to_db(self, run_id: int) -> dict:
        return {
            "run_id": run_id, "horizon": self.horizon, "side": self.side,
            "spot_entry": self.spot_entry, "target": self.target, "stop": self.stop,
            "notional_usd": self.notional_usd, "confidence": self.confidence,
            "expected_edge_krw": self.expected_edge_krw, "rationale": self.rationale,
            "status": "proposed",
        }

    def as_dict(self) -> dict:
        return asdict(self)


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _best_horizon(horizon_aggregates: dict[str, dict]) -> str:
    """Trade the CLEANEST setup: the horizon (1w/1m/3m) with the highest |Δ|×confidence.
    More precise than always trading 1m — lets a strong, high-conviction call on any
    tradeable horizon drive the position."""
    best, best_score = "1m", -1.0
    for h in TRADE_HORIZONS:
        agg = horizon_aggregates.get(h, {})
        score = abs(float(agg.get("weighted_delta_krw", 0.0))) * float(agg.get("confidence", 0.0))
        if score > best_score:
            best_score, best = score, h
    return best


def build_trade_signal(
    spot: Optional[float],
    horizon_aggregates: dict[str, dict],
    realized_vol: Optional[float] = None,
    agreement: Optional[float] = None,
    primary_horizon: Optional[str] = None,
) -> TradeDecision:
    primary_horizon = primary_horizon or _best_horizon(horizon_aggregates)
    agg = horizon_aggregates.get(primary_horizon, {})
    delta = float(agg.get("weighted_delta_krw", 0.0))
    conf = float(agg.get("confidence", 0.0))

    if spot is None or conf < CONVICTION_FLOOR or abs(delta) < MIN_TRADEABLE_KRW:
        reason = ("FLAT - " + (
            "no spot" if spot is None else
            f"best-horizon({primary_horizon}) confidence {conf:.0%} < floor {CONVICTION_FLOOR:.0%}"
            if conf < CONVICTION_FLOOR else
            f"expected move {abs(delta):.1f}원 < {MIN_TRADEABLE_KRW:.0f}원 threshold"))
        return TradeDecision(primary_horizon, "FLAT", spot, None, None, 0.0,
                             conf, delta, abs(delta) * conf, None, reason)

    side = "LONG" if delta > 0 else "SHORT"
    sign = 1.0 if side == "LONG" else -1.0

    # ── Volatility-aware stop / target (ATR-style) ──
    vol = realized_vol if (realized_vol and realized_vol > 0) else max(3.0, abs(delta) * 0.5)
    stop_dist = max(MIN_STOP_KRW, STOP_VOL_MULT * vol)
    target_dist = max(abs(delta), MIN_RR * stop_dist)
    target = round(spot + sign * target_dist, 2)
    stop = round(spot - sign * stop_dist, 2)
    rr = round(target_dist / stop_dist, 2)

    # ── Conviction × agreement × edge sizing ──
    conf_factor = _clamp((conf - CONVICTION_FLOOR) / (FULL_SIZE_CONF - CONVICTION_FLOOR), 0.0, 1.0)
    agree = agreement if agreement is not None else 0.6
    agree_factor = _clamp((agree - 0.5) / 0.4, 0.0, 1.0)          # 0.5→0, 0.9→1
    edge_factor = _clamp(abs(delta) / (EDGE_VOL_MULT * vol), EDGE_FLOOR, EDGE_CAP)  # move vs vol
    size_factor = _clamp((BASE_SIZE + (1 - BASE_SIZE) * conf_factor)
                         * (0.7 + 0.3 * agree_factor) * edge_factor, 0.0, 1.0)
    notional = round(settings.MAX_TRADE_NOTIONAL_USD * size_factor, 2)

    sizing = {
        "max_notional_usd": settings.MAX_TRADE_NOTIONAL_USD,
        "size_factor": round(size_factor, 3),
        "conviction": round(conf_factor, 3),
        "agreement": round(agree, 3),
        "edge_x": round(edge_factor, 3),
        "vol_krw": round(vol, 2),
        "stop_dist_krw": round(stop_dist, 2),
    }
    rationale = (
        f"{side} USD/KRW · {primary_horizon} 모델경로 {delta:+.1f}원 @ 신뢰도 {conf:.0%}. "
        f"진입 {spot:.2f} / 목표 {target:.2f} / 손절 {stop:.2f} (R:R {rr}, 손절=1.3×변동성 {vol:.1f}원). "
        f"노셔널 {notional:,.0f} USD = 상한 {settings.MAX_TRADE_NOTIONAL_USD:,.0f} × 사이즈팩터 {size_factor:.2f} "
        f"[확신 {conf_factor:.2f} × 합의 {agree:.0%} × 엣지 {edge_factor:.2f}].")
    return TradeDecision(primary_horizon, side, spot, target, stop, notional,
                         conf, delta, abs(delta) * conf, rr, rationale, sizing)
