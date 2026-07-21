"""
Broker adapter interface — the seam between the forecasting brain and a real
securities/FX account.

Every concrete broker (PaperBroker now; KISBroker / Kiwoom / a futures broker later)
implements this same surface, so the engine never needs to know which venue it is
talking to. Swap brokers by changing BROKER in .env — no engine code changes.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class Quote:
    symbol: str
    bid: float
    ask: float
    mid: float
    ts: str


@dataclass
class OrderResult:
    ok: bool
    order_id: Optional[str]
    side: str
    filled_qty: float
    avg_price: Optional[float]
    raw: dict
    message: str = ""


@dataclass
class Position:
    symbol: str
    side: str            # LONG | SHORT
    qty: float
    avg_price: float
    unrealized_pnl: Optional[float] = None


class BrokerAdapter(ABC):
    name: str = "base"
    # Whether this adapter is allowed to move real money. PaperBroker = False.
    is_live: bool = False

    @abstractmethod
    async def get_quote(self, symbol: str = "USDKRW") -> Quote: ...

    @abstractmethod
    async def place_order(self, side: str, notional_usd: float,
                          symbol: str = "USDKRW", **kwargs) -> OrderResult: ...

    @abstractmethod
    async def get_positions(self) -> list[Position]: ...

    @abstractmethod
    async def get_balance(self) -> dict: ...

    async def close(self) -> None:
        """Optional cleanup (close HTTP clients, etc.)."""
        return None
