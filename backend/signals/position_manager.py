"""
Position manager — marks open paper positions to market and closes them, so the P&L
track record and the daily-loss limit are real (not just entries).

Exit rules per open position (uses its TradeSignal target/stop/horizon):
  • target hit  → close at current spot (take profit)
  • stop hit    → close at current spot (stop loss)
  • time exit   → horizon elapsed → close at current spot regardless

Runs at the START of every cycle against fresh spot, before new positions are opened.
"""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import PaperPosition, TradeSignal
from backend.data import activity_log as AL

logger = logging.getLogger("krw_watcher.positions")

HORIZON_DAYS = {"1w": 7, "1m": 30, "3m": 90, "12m": 365}


async def manage_open_positions(db: AsyncSession, current_spot: float | None) -> dict:
    if not current_spot:
        return {"closed": 0, "realized_krw": 0.0}
    now = datetime.utcnow()
    rows = (await db.execute(
        select(PaperPosition).where(PaperPosition.status == "open")
    )).scalars().all()

    closed, realized = 0, 0.0
    for pos in rows:
        sig = await db.get(TradeSignal, pos.signal_id) if pos.signal_id else None
        reason = None
        if sig and sig.target and sig.stop:
            if pos.side == "LONG":
                if current_spot >= sig.target:
                    reason = "target"
                elif current_spot <= sig.stop:
                    reason = "stop"
            else:  # SHORT
                if current_spot <= sig.target:
                    reason = "target"
                elif current_spot >= sig.stop:
                    reason = "stop"
        # Time exit: horizon elapsed.
        if reason is None and sig and pos.opened_at:
            days = HORIZON_DAYS.get(sig.horizon, 30)
            if pos.opened_at <= now - timedelta(days=days):
                reason = "time"
        if reason is None:
            continue

        sign = 1.0 if pos.side == "LONG" else -1.0
        pnl = sign * (current_spot - pos.entry_rate) * pos.notional_usd
        pos.exit_rate = round(current_spot, 2)
        pos.pnl_krw = round(pnl, 2)
        pos.pnl_pct = round(sign * (current_spot - pos.entry_rate) / pos.entry_rate * 100, 3)
        pos.status = "closed"
        pos.closed_at = now
        closed += 1
        realized += pnl
        AL.trade_event(f"[PAPER] CLOSED {pos.side} {pos.notional_usd:,.0f} USD @ "
                       f"{current_spot:.2f} ({reason}) · PnL {pnl:+,.0f}원")
    if closed:
        await db.commit()
        logger.info("Closed %d positions, realized %+.0f원", closed, realized)
    return {"closed": closed, "realized_krw": round(realized, 2)}


async def has_open_position(db: AsyncSession, side: str, horizon: str) -> bool:
    """Avoid stacking duplicate exposure: one open paper position per (side, horizon)."""
    row = (await db.execute(
        select(PaperPosition.id)
        .join(TradeSignal, TradeSignal.id == PaperPosition.signal_id)
        .where(PaperPosition.status == "open",
               PaperPosition.side == side,
               TradeSignal.horizon == horizon)
        .limit(1)
    )).scalar_one_or_none()
    return row is not None
