"""
Backtest engine — validates the TRADE FRAMEWORK (entry/target/stop/horizon, R:R,
position sizing) over real USD/KRW history.

The committee signal is the expensive LLM ensemble, so the backtest uses a transparent,
zero-token BASELINE signal (trend-following: spot vs SMA) as a proxy. This tells you
whether the execution framework has edge and what the realistic win-rate / drawdown
look like. Once you accumulate logged live model signals, swap them in via `signal_fn`
for a true model backtest.

Metrics: n_trades, win_rate, total_return_pct, avg_trade_pct, profit_factor,
sharpe (annualized), max_drawdown_pct, directional_hit_rate, exposure.
"""
import statistics
from dataclasses import dataclass, asdict
from typing import Callable, Optional

HORIZON_DAYS = {"1w": 7, "1m": 22, "3m": 66, "12m": 252}  # trading-day approximations


@dataclass
class BacktestResult:
    n_trades: int
    win_rate: float
    total_return_pct: float
    avg_trade_pct: float
    profit_factor: float
    sharpe: float
    max_drawdown_pct: float
    directional_hit_rate: float
    avg_hold_days: float
    params: dict
    equity_curve: list[float]
    note: str

    def as_dict(self) -> dict:
        return asdict(self)


def _sma(vals: list[float], i: int, n: int) -> Optional[float]:
    if i < n:
        return None
    return sum(vals[i - n:i]) / n


def _baseline_signal(rates: list[float], i: int, lookback: int) -> int:
    """Trend follow: +1 LONG USD/KRW if above SMA, -1 SHORT if below, 0 flat."""
    sma = _sma(rates, i, lookback)
    if sma is None:
        return 0
    spot = rates[i]
    band = sma * 0.001  # 0.1% deadband around the average
    if spot > sma + band:
        return 1
    if spot < sma - band:
        return -1
    return 0


def run_backtest(
    history: list[tuple[str, float]],
    lookback: int = 20,
    horizon: str = "1m",
    target_pct: float = 0.012,     # take-profit distance (≈1.2%)
    stop_pct: float = 0.008,       # stop distance (≈0.8%) → R:R 1.5
    signal_fn: Optional[Callable[[list[float], int, int], int]] = None,
) -> BacktestResult:
    rates = [r for _, r in history]
    n = len(rates)
    hold_max = HORIZON_DAYS.get(horizon, 22)
    sig_fn = signal_fn or _baseline_signal
    if n < lookback + hold_max + 5:
        return BacktestResult(0, 0, 0, 0, 0, 0, 0, 0, 0,
                              {"lookback": lookback, "horizon": horizon},
                              [1.0], "insufficient history")

    equity = 1.0
    curve = [equity]
    trades: list[float] = []          # per-trade return fraction
    holds: list[int] = []
    dir_hits = 0
    dir_total = 0

    pos = 0          # 0 flat, +1 long, -1 short
    entry = 0.0
    entry_i = 0

    for i in range(lookback, n):
        spot = rates[i]

        # Manage open position
        if pos != 0:
            ret = (spot - entry) / entry * pos
            hit_target = ret >= target_pct
            hit_stop = ret <= -stop_pct
            timed = (i - entry_i) >= hold_max
            if hit_target or hit_stop or timed:
                trades.append(ret)
                holds.append(i - entry_i)
                equity *= (1 + ret)
                curve.append(equity)
                pos = 0

        # Entry (only when flat)
        if pos == 0:
            s = sig_fn(rates, i, lookback)
            if s != 0:
                pos = s
                entry = spot
                entry_i = i
                # directional check: did it move our way over the horizon?
                fwd = rates[min(i + hold_max, n - 1)]
                if (fwd - spot) * s > 0:
                    dir_hits += 1
                dir_total += 1

    if not trades:
        return BacktestResult(0, 0, 0, 0, 0, 0, 0,
                              round(dir_hits / dir_total, 4) if dir_total else 0, 0,
                              {"lookback": lookback, "horizon": horizon,
                               "target_pct": target_pct, "stop_pct": stop_pct},
                              curve, "no trades triggered")

    wins = [t for t in trades if t > 0]
    losses = [t for t in trades if t <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    # Sharpe from per-trade returns, annualized by trades→year via avg hold.
    avg_hold = statistics.mean(holds) if holds else hold_max
    trades_per_year = 252 / max(1, avg_hold)
    sharpe = 0.0
    if len(trades) > 1 and statistics.pstdev(trades) > 0:
        sharpe = (statistics.mean(trades) / statistics.pstdev(trades)) * (trades_per_year ** 0.5)

    peak = curve[0]
    mdd = 0.0
    for v in curve:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)

    return BacktestResult(
        n_trades=len(trades),
        win_rate=round(len(wins) / len(trades), 4),
        total_return_pct=round((equity - 1) * 100, 2),
        avg_trade_pct=round(statistics.mean(trades) * 100, 3),
        profit_factor=round(gross_win / gross_loss, 2) if gross_loss else float("inf"),
        sharpe=round(sharpe, 2),
        max_drawdown_pct=round(mdd * 100, 2),
        directional_hit_rate=round(dir_hits / dir_total, 4) if dir_total else 0,
        avg_hold_days=round(avg_hold, 1),
        params={"lookback": lookback, "horizon": horizon,
                "target_pct": target_pct, "stop_pct": stop_pct, "years_points": n},
        equity_curve=[round(v, 4) for v in curve[:: max(1, len(curve) // 200)]],
        note="baseline trend signal (proxy for execution framework)",
    )
