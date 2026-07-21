"""
KISBroker — 한국투자증권 KIS Developers REST API adapter (SCAFFOLD).

This is the future live-trading seam. It implements OAuth token issuance against the
KIS Developers gateway and quote retrieval, but ORDER PLACEMENT IS INTENTIONALLY
GATED and left as a clearly-marked stub: you must (1) choose the actual tradable
instrument (KRX USD futures, or a USD/KRW product your account supports), (2) wire the
correct order endpoint + TR_ID, and (3) set ENABLE_LIVE_TRADING=true — only after
validating on the 모의투자 (paper) domain.

Docs: https://apiportal.koreainvestment.com/  (KIS Developers)
Domains:
  실전투자: https://openapi.koreainvestment.com:9443
  모의투자: https://openapivts.koreainvestment.com:29443

SAFETY: never places a live order unless settings.ENABLE_LIVE_TRADING is True AND the
notional is within MAX_TRADE_NOTIONAL_USD. By default this raises NotImplementedError
on place_order so nothing can fire by accident.
"""
import httpx
from datetime import datetime
from backend.config import settings
from backend.brokers.base_broker import BrokerAdapter, Quote, OrderResult, Position

REAL_BASE = "https://openapi.koreainvestment.com:9443"
PAPER_BASE = "https://openapivts.koreainvestment.com:29443"


class KISBroker(BrokerAdapter):
    name = "kis"

    def __init__(self):
        self._base = PAPER_BASE if settings.KIS_PAPER else REAL_BASE
        self.is_live = settings.ENABLE_LIVE_TRADING and not settings.KIS_PAPER
        self._token: str | None = None
        self._client = httpx.AsyncClient(base_url=self._base, timeout=20)

    async def _ensure_token(self) -> str:
        if self._token:
            return self._token
        if not (settings.KIS_APP_KEY and settings.KIS_APP_SECRET):
            raise RuntimeError("KIS credentials missing (set KIS_APP_KEY / KIS_APP_SECRET)")
        r = await self._client.post("/oauth2/tokenP", json={
            "grant_type": "client_credentials",
            "appkey": settings.KIS_APP_KEY,
            "appsecret": settings.KIS_APP_SECRET,
        })
        r.raise_for_status()
        self._token = r.json()["access_token"]
        return self._token

    async def get_quote(self, symbol: str = "USDKRW") -> Quote:
        """KIS does not serve a clean spot USD/KRW quote on the retail REST gateway,
        so we fall back to fx_client spot for marking. Replace with the correct KIS
        market-data endpoint for your chosen instrument when you wire live trading."""
        from backend.data.collector import get_latest
        cached = get_latest().get("snapshot")
        if cached and getattr(cached, "spot", None):
            return Quote(symbol, cached.spot, cached.spot, cached.spot, datetime.utcnow().isoformat())
        from backend.data.fx_client import get_snapshot
        snap = await get_snapshot()
        if not snap.spot:
            raise RuntimeError("KIS quote fallback: no spot available")
        return Quote(symbol, snap.spot, snap.spot, snap.spot, datetime.utcnow().isoformat())

    async def place_order(self, side: str, notional_usd: float,
                          symbol: str = "USDKRW", **kwargs) -> OrderResult:
        # ── HARD SAFETY GATES ──────────────────────────────────────────────
        if not settings.ENABLE_LIVE_TRADING:
            raise NotImplementedError(
                "Live trading disabled. Validate on PaperBroker first, then set "
                "ENABLE_LIVE_TRADING=true and implement the KIS order endpoint below.")
        if notional_usd > settings.MAX_TRADE_NOTIONAL_USD:
            return OrderResult(False, None, side, 0, None, {},
                               f"notional {notional_usd} exceeds cap {settings.MAX_TRADE_NOTIONAL_USD}")
        # ── TODO (wire before going live) ──────────────────────────────────
        # await self._ensure_token()
        # 1. Pick the tradable instrument (e.g. KRX USD futures code) and TR_ID.
        # 2. POST /uapi/.../order with headers {authorization, appkey, appsecret, tr_id}.
        # 3. Parse order id / fill from the response and return OrderResult.
        raise NotImplementedError(
            "KIS order endpoint not wired. See module docstring + TODO. "
            "This stub refuses to place live orders so capital is never risked by accident.")

    async def get_positions(self) -> list[Position]:
        # TODO: call the KIS balance/positions inquiry endpoint.
        return []

    async def get_balance(self) -> dict:
        return {
            "broker": self.name,
            "is_live": self.is_live,
            "domain": "paper" if settings.KIS_PAPER else "real",
            "live_trading_enabled": settings.ENABLE_LIVE_TRADING,
            "note": "scaffold — order endpoint not yet wired",
        }

    async def close(self) -> None:
        await self._client.aclose()
