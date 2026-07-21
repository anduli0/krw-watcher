"""
Portfolio-level risk manager — the last gate before a trade reaches the broker.

Independent of per-trade sizing in trade_signal.py, this enforces account-wide rules:
  1. Aggregate exposure cap (MAX_TOTAL_NOTIONAL_USD) — trims or blocks if over.
  2. Daily loss limit (DAILY_LOSS_LIMIT_KRW) — halts NEW risk once breached.
  3. Volatility throttle — shrinks size when USD/KRW daily vol is elevated.

Returns the (possibly reduced or flattened) TradeDecision plus an audit note.
"""
import logging
from datetime import datetime, time
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database.models import PaperPosition
from backend.data import activity_log as AL

logger = logging.getLogger("krw_watcher.risk")


def _flatten(decision, reason: str):
    decision.side = "FLAT"
    decision.notional_usd = 0.0
    decision.target = None
    decision.stop = None
    decision.rationale = f"BLOCKED by risk manager: {reason}"
    return decision


async def _today_realized_pnl(db: AsyncSession) -> float:
    start = datetime.combine(datetime.utcnow().date(), time.min)
    rows = (await db.execute(
        select(PaperPosition.pnl_krw)
        .where(PaperPosition.status == "closed",
               PaperPosition.closed_at.isnot(None),
               PaperPosition.closed_at >= start)
    )).all()
    return sum((r[0] or 0.0) for r in rows)


async def apply_risk_limits(db: AsyncSession, decision, broker, daily_vol_krw: float | None = None):
    if decision.side == "FLAT" or decision.notional_usd <= 0:
        return decision

    notes = []

    # ── 1. Volatility throttle (EXTREME vol only) ───────────────────────────
    # trade_signal already sizes for volatility (edge factor + vol-based stop), so this
    # only trims in genuinely extreme tape to avoid double-penalizing normal-elevated vol.
    VOL_EXTREME = 14.0
    if daily_vol_krw and daily_vol_krw > VOL_EXTREME:
        scale = max(0.7, min(1.0, VOL_EXTREME / daily_vol_krw))
        if scale < 1.0:
            decision.notional_usd = round(decision.notional_usd * scale, 2)
            notes.append(f"extreme-vol throttle ×{scale:.2f} (daily {daily_vol_krw:.1f}원)")

    # ── 2. Daily loss limit ─────────────────────────────────────────────────
    try:
        realized = await _today_realized_pnl(db)
        bal = await broker.get_balance()
        unrealized = float(bal.get("unrealized_pnl_krw", 0) or 0)
        day_pnl = realized + unrealized
        if day_pnl <= -settings.DAILY_LOSS_LIMIT_KRW:
            AL.trade_event(f"Risk: daily loss limit hit ({day_pnl:,.0f}원) — halting new risk")
            return _flatten(decision, f"daily loss {day_pnl:,.0f}원 ≤ -{settings.DAILY_LOSS_LIMIT_KRW:,.0f}원")
    except Exception:
        pass

    # ── 3. Aggregate exposure cap ───────────────────────────────────────────
    try:
        positions = await broker.get_positions()
        open_notional = sum(p.qty for p in positions)
        room = settings.MAX_TOTAL_NOTIONAL_USD - open_notional
        if room <= 0:
            return _flatten(decision, f"exposure cap ({open_notional:,.0f}/{settings.MAX_TOTAL_NOTIONAL_USD:,.0f} USD)")
        if decision.notional_usd > room:
            decision.notional_usd = round(room, 2)
            notes.append(f"trimmed to exposure room {room:,.0f} USD")
    except Exception:
        pass

    if notes:
        decision.rationale += " | RISK: " + "; ".join(notes)
    return decision
