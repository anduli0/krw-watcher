"""
Live accuracy & performance metrics — your money's report card.

  • forecast_accuracy: directional hit-rate + MAE per horizon, per-agent ranking
    (from FeedbackEntry: predicted vs realized USD/KRW move).
  • trading_performance: win-rate, total P&L, Sharpe, profit factor + a confidence
    calibration curve (from closed PaperPositions joined to their TradeSignal).
"""
import statistics
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import FeedbackEntry, PaperPosition, TradeSignal

HORIZONS = ("1w", "1m", "3m", "12m")


async def forecast_accuracy(db: AsyncSession) -> dict:
    rows = (await db.execute(select(FeedbackEntry))).scalars().all()
    by_h: dict[str, list[FeedbackEntry]] = {h: [] for h in HORIZONS}
    by_agent: dict[str, list[float]] = {}
    for r in rows:
        if r.horizon in by_h:
            by_h[r.horizon].append(r)
        if r.divergence_krw is not None:
            by_agent.setdefault(r.agent_id, []).append(abs(r.divergence_krw))

    horizons = {}
    for h, entries in by_h.items():
        if not entries:
            horizons[h] = {"samples": 0}
            continue
        hits = sum(1 for e in entries
                   if e.predicted_delta is not None and e.actual_delta is not None
                   and e.predicted_delta * e.actual_delta > 0)
        directional = [e for e in entries if e.predicted_delta and abs(e.predicted_delta) > 1]
        hit_rate = (sum(1 for e in directional if e.predicted_delta * e.actual_delta > 0)
                    / len(directional)) if directional else None
        mae = statistics.mean([abs(e.divergence_krw) for e in entries if e.divergence_krw is not None]) \
            if entries else None
        horizons[h] = {
            "samples": len(entries),
            "directional_hit_rate": round(hit_rate, 3) if hit_rate is not None else None,
            "mae_krw": round(mae, 2) if mae is not None else None,
        }

    ranking = sorted(
        ({"agent_id": aid, "mae_krw": round(statistics.mean(v), 2), "n": len(v)}
         for aid, v in by_agent.items() if len(v) >= 3),
        key=lambda x: x["mae_krw"])
    return {"horizons": horizons, "agent_ranking": ranking[:20], "total_samples": len(rows)}


async def trading_performance(db: AsyncSession) -> dict:
    closed = (await db.execute(
        select(PaperPosition).where(PaperPosition.status == "closed")
    )).scalars().all()
    if not closed:
        return {"closed_trades": 0}

    pnls = [p.pnl_krw or 0.0 for p in closed]
    pcts = [p.pnl_pct or 0.0 for p in closed]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    gross_w, gross_l = sum(wins), abs(sum(losses))
    sharpe = 0.0
    if len(pcts) > 1 and statistics.pstdev(pcts) > 0:
        sharpe = statistics.mean(pcts) / statistics.pstdev(pcts)

    # Confidence calibration: bucket by the originating signal's confidence.
    buckets = {"0.55-0.65": [], "0.65-0.75": [], "0.75-0.85": [], "0.85+": []}
    for p in closed:
        sig = await db.get(TradeSignal, p.signal_id) if p.signal_id else None
        c = sig.confidence if sig and sig.confidence is not None else None
        if c is None:
            continue
        key = ("0.85+" if c >= 0.85 else "0.75-0.85" if c >= 0.75
               else "0.65-0.75" if c >= 0.65 else "0.55-0.65")
        buckets[key].append(1 if (p.pnl_krw or 0) > 0 else 0)
    calibration = {k: {"n": len(v), "win_rate": round(sum(v) / len(v), 3)} for k, v in buckets.items() if v}

    return {
        "closed_trades": len(closed),
        "win_rate": round(len(wins) / len(closed), 3),
        "total_pnl_krw": round(sum(pnls), 0),
        "avg_pnl_pct": round(statistics.mean(pcts), 3),
        "profit_factor": round(gross_w / gross_l, 2) if gross_l else None,
        "sharpe_per_trade": round(sharpe, 2),
        "best_krw": round(max(pnls), 0),
        "worst_krw": round(min(pnls), 0),
        "calibration": calibration,
    }
