"""Historical USD/KRW loader (FRED DEXKOUS daily) for backtesting."""
import httpx
from backend.config import settings
from backend.data.cache import data_cache

FRED_BASE = "https://api.stlouisfed.org/fred"
TTL = 12 * 3600


async def fetch_usdkrw_history(years: int = 10) -> list[tuple[str, float]]:
    """Return [(YYYY-MM-DD, rate), ...] ascending by date. Empty if no FRED key."""
    cache_key = f"dexkous_hist_{years}"
    cached = data_cache.get(cache_key, TTL)
    if cached:
        return cached
    if not settings.FRED_API_KEY:
        return []
    from datetime import date
    start = date.today().replace(year=max(1990, date.today().year - years)).isoformat()
    params = {
        "series_id": "DEXKOUS",
        "api_key": settings.FRED_API_KEY,
        "file_type": "json",
        "observation_start": start,
        "sort_order": "asc",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{FRED_BASE}/series/observations", params=params)
            r.raise_for_status()
            obs = r.json()["observations"]
    except Exception:
        return []
    series = [(o["date"], float(o["value"])) for o in obs if o["value"] not in (".", "", None)]
    data_cache.set(cache_key, series)
    return series


async def fetch_fred_daily(series_id: str, years: int = 15) -> list[tuple[str, float]]:
    """Generic full-history daily FRED series → [(YYYY-MM-DD, value), ...] ascending.
    Used by the accuracy simulator's cross-asset predictors (broad dollar, USD/CNY,
    rates, VIX). Empty list if no FRED key or the series is unavailable."""
    cache_key = f"fred_daily_{series_id}_{years}"
    cached = data_cache.get(cache_key, TTL)
    if cached:
        return cached
    if not settings.FRED_API_KEY:
        return []
    from datetime import date
    start = date.today().replace(year=max(1990, date.today().year - years)).isoformat()
    params = {
        "series_id": series_id,
        "api_key": settings.FRED_API_KEY,
        "file_type": "json",
        "observation_start": start,
        "sort_order": "asc",
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(f"{FRED_BASE}/series/observations", params=params)
            r.raise_for_status()
            obs = r.json()["observations"]
    except Exception:
        return []
    series = [(o["date"], float(o["value"])) for o in obs if o["value"] not in (".", "", None)]
    data_cache.set(cache_key, series)
    return series


async def fetch_recent_yf(days: int = 120) -> list[tuple[str, float]]:
    """Recent daily USD/KRW closes from yfinance KRW=X (current to ~today). FRED DEXKOUS
    lags a few business days; this fills the gap so charts reach the latest date.
    Returns [(YYYY-MM-DD, close), ...] ascending, or [] if yfinance unavailable."""
    import asyncio
    cache_key = f"yf_recent_{days}"
    cached = data_cache.get(cache_key, 3600)   # 1h freshness
    if cached:
        return cached
    try:
        import yfinance as yf
        loop = asyncio.get_event_loop()

        def _pull():
            period = "3mo" if days <= 90 else "6mo" if days <= 180 else "1y"
            hist = yf.Ticker("KRW=X").history(period=period, interval="1d")
            if hist is None or hist.empty:
                return []
            out = []
            for idx, row in hist.iterrows():
                c = float(row["Close"])
                if c and c == c:                # skip NaN
                    out.append((idx.strftime("%Y-%m-%d"), round(c, 2)))
            return out
        res = await loop.run_in_executor(None, _pull)
    except Exception:
        res = []
    if res:
        data_cache.set(cache_key, res)
    return res


async def fetch_recent_ohlc_yf(days: int = 730) -> list[tuple]:
    """Recent daily OHLC from yfinance KRW=X for the same-day High/Low/Close predictor.
    Returns [(YYYY-MM-DD, open, high, low, close), ...] ascending, or [] if unavailable."""
    import asyncio
    cache_key = f"yf_ohlc_{days}"
    cached = data_cache.get(cache_key, 3600)
    if cached:
        return cached
    try:
        import yfinance as yf
        loop = asyncio.get_event_loop()

        def _pull():
            period = "1y" if days <= 365 else "2y" if days <= 730 else "5y"
            hist = yf.Ticker("KRW=X").history(period=period, interval="1d")
            if hist is None or hist.empty:
                return []
            out = []
            for idx, row in hist.iterrows():
                o, h, l, c = (float(row["Open"]), float(row["High"]),
                              float(row["Low"]), float(row["Close"]))
                if all(v == v and v > 0 for v in (o, h, l, c)) and h >= l:   # drop NaN/bad
                    out.append((idx.strftime("%Y-%m-%d"),
                                round(o, 2), round(h, 2), round(l, 2), round(c, 2)))
            return out
        res = await loop.run_in_executor(None, _pull)
    except Exception:
        res = []
    if res:
        data_cache.set(cache_key, res)
    return res


async def fetch_usdkrw_current(years: int = 15) -> list[tuple[str, float]]:
    """FRED DEXKOUS history extended with yfinance's more-recent daily closes so the
    series runs up to ~today (not just FRED's lagged last point). Used by the accuracy
    simulation + realized-rate store; the trading backtest can keep using FRED-only."""
    base = await fetch_usdkrw_history(years=years)
    recent = await fetch_recent_yf(180)
    if not base:
        return recent
    if not recent:
        return base
    last = base[-1][0]
    tail = [(d, r) for (d, r) in recent if d > last]
    return base + tail if tail else base
