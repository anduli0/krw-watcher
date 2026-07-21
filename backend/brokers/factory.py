"""Broker factory — returns the adapter selected by BROKER in .env.
A single process-wide instance keeps paper P&L / KIS token state across cycles."""
from backend.config import settings
from backend.brokers.base_broker import BrokerAdapter
from backend.brokers.paper_broker import PaperBroker

_INSTANCE: BrokerAdapter | None = None


def get_broker() -> BrokerAdapter:
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE
    choice = (settings.BROKER or "paper").lower()
    if choice == "kis":
        from backend.brokers.kis_broker import KISBroker
        _INSTANCE = KISBroker()
    else:
        _INSTANCE = PaperBroker()
    return _INSTANCE


async def reset_broker() -> None:
    global _INSTANCE
    if _INSTANCE is not None:
        await _INSTANCE.close()
    _INSTANCE = None
