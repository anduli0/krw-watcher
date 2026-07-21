"""
PaperBroker — fully simulated execution against live USD/KRW spot.

Use this to build a track record before risking capital. It quotes real spot (from
fx_client) with a synthetic spread, fills instantly, and tracks positions/PnL in
memory. Identical interface to a live broker, so promoting to KIS later is a config flip.
"""
from datetime import datetime
from backend.brokers.base_broker import BrokerAdapter, Quote, OrderResult, Position
from backend.data import activity_log as AL

SYNTHETIC_SPREAD_KRW = 0.4   # ~bid/ask around mid for realism


class PaperBroker(BrokerAdapter):
    name = "paper"
    is_live = False

    def __init__(self, starting_cash_usd: float = 100_000.0):
        self._cash = starting_cash_usd
        self._positions: dict[str, Position] = {}
        self._orders: list[OrderResult] = []
        self._seq = 0

    async def _spot(self, symbol: str) -> float:
        # Prefer the collector's cached spot (fast) — only hit the network if cold.
        from backend.data.collector import get_latest
        cached = get_latest().get("snapshot")
        if cached and getattr(cached, "spot", None):
            return cached.spot
        from backend.data.fx_client import get_snapshot
        snap = await get_snapshot()
        if snap.spot:
            return snap.spot
        raise RuntimeError("paper broker: no spot available")

    async def get_quote(self, symbol: str = "USDKRW") -> Quote:
        mid = await self._spot(symbol)
        half = SYNTHETIC_SPREAD_KRW / 2
        return Quote(symbol, round(mid - half, 2), round(mid + half, 2), round(mid, 2),
                     datetime.utcnow().isoformat())

    async def place_order(self, side: str, notional_usd: float,
                          symbol: str = "USDKRW", **kwargs) -> OrderResult:
        side = side.upper()
        if side not in ("LONG", "SHORT", "BUY", "SELL"):
            return OrderResult(False, None, side, 0, None, {}, f"bad side {side}")
        q = await self.get_quote(symbol)
        # LONG/BUY USD/KRW fills at ask; SHORT/SELL at bid.
        price = q.ask if side in ("LONG", "BUY") else q.bid
        norm_side = "LONG" if side in ("LONG", "BUY") else "SHORT"
        self._seq += 1
        oid = f"PAPER-{self._seq:06d}"

        existing = self._positions.get(symbol)
        if existing and existing.side == norm_side:
            total_qty = existing.qty + notional_usd
            existing.avg_price = (existing.avg_price * existing.qty + price * notional_usd) / total_qty
            existing.qty = total_qty
        else:
            self._positions[symbol] = Position(symbol, norm_side, notional_usd, price)

        res = OrderResult(True, oid, norm_side, notional_usd, price,
                          {"simulated": True}, "filled (paper)")
        self._orders.append(res)
        AL.trade_event(f"[PAPER] {norm_side} {notional_usd:,.0f} USD @ {price:.2f} → {oid}")
        return res

    async def get_positions(self) -> list[Position]:
        out = []
        try:
            q = await self.get_quote()
            for pos in self._positions.values():
                sign = 1 if pos.side == "LONG" else -1
                pos.unrealized_pnl = round(sign * (q.mid - pos.avg_price) * pos.qty, 2)
                out.append(pos)
        except Exception:
            out = list(self._positions.values())
        return out

    async def get_balance(self) -> dict:
        positions = await self.get_positions()
        upnl = sum(p.unrealized_pnl or 0 for p in positions)
        return {
            "broker": self.name,
            "is_live": self.is_live,
            "cash_usd": round(self._cash, 2),
            "open_positions": len(positions),
            "unrealized_pnl_krw": round(upnl, 2),
            "orders_count": len(self._orders),
        }
