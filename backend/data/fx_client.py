"""
FX data client — the no-token data backbone for KRW-Watcher.

Sources (all free):
  • FRED  → US macro (Fed funds, UST yields, CPI), USD/KRW (DEXKOUS), broad
            dollar index (DTWEXBGS), VIX (VIXCLS), CNY (DEXCHUS).
  • yfinance "KRW=X" → intraday USD/KRW spot (falls back to FRED DEXKOUS daily).

Everything degrades gracefully: a missing FRED key or a dead series never throws
up the stack — the agent layer simply runs on whatever data arrived.
"""
import asyncio
from dataclasses import dataclass, field
from typing import Optional

import httpx

from backend.config import settings
from backend.data.cache import data_cache

FRED_BASE = "https://api.stlouisfed.org/fred"
TTL = 3 * 3600  # 3-hour cache for slow-moving macro series

# ── FRED series grouped by analytical theme ──────────────────────────────────
US_SERIES = {
    "DFF":      "Fed Funds Rate (Effective) %",
    "DGS2":     "US 2Y Treasury Yield %",
    "DGS10":    "US 10Y Treasury Yield %",
    "CPIAUCSL": "US CPI (All Urban) index",
    "T5YIE":    "US 5Y Breakeven Inflation %",
}
RISK_SERIES = {
    "DTWEXBGS":     "Broad USD Index (dollar strength)",
    "VIXCLS":       "VIX (equity vol / risk sentiment)",
    "DEXCHUS":      "USD/CNY (China proxy for KRW)",
    "DCOILBRENTEU": "Brent Crude $ (Korea import bill / won pressure)",
    "SP500":        "S&P 500 (global equity risk-on/off)",
}
KR_SERIES = {
    # Korea series on FRED are patchy/lagged — best-effort, tolerant of failure.
    "IRLTLT01KRM156N": "KR 10Y Govt Bond Yield % (monthly)",
    "KORCPIALLMINMEI":  "KR CPI index (monthly)",
}
# Monetary aggregates — the monetary model of exchange rates: faster relative money
# growth → currency depreciation pressure (relative price levels, PPP drift).
# US M2SL is current; Korea broad-money on FRED is patchy, so YoY uses freshness-guarded
# candidates (MABMM301KRM189S = OECD M3, more current than the discontinued MYAGM2KRM189S).
MONETARY_SERIES = {
    "M2SL": "US M2 Money Stock $bn",
}
US_M2_CANDIDATES = ["M2SL"]
KR_M2_CANDIDATES = ["MABMM301KRM189S", "MANMM101KRM189S", "MYAGM2KRM189S"]
# US jobs (고용) — the dominant Fed-path driver. NFP surprises move the dollar hardest.
JOBS_SERIES = {
    "PAYEMS": "US Nonfarm Payrolls (level, thousands)",
    "UNRATE": "US Unemployment Rate %",
    "ICSA":   "US Initial Jobless Claims",
}
# Other-DM central banks & the yen carry trade — EUR is ~57% of DXY; JPY carry
# unwind is a top global risk-off trigger that hits the won hard.
GLOBAL_FX_SERIES = {
    "DEXUSEU": "EUR/USD (ECB vs Fed divergence → DXY)",
    "DEXJPUS": "USD/JPY (BOJ policy / yen carry trade)",
}
FX_SERIES = {
    "DEXKOUS": "USD/KRW (won per dollar, daily)",
}


@dataclass
class SeriesData:
    series_id: str
    label: str
    latest_value: Optional[float]
    latest_date: Optional[str]
    prior_value: Optional[float]
    change: Optional[float]


@dataclass
class Snapshot:
    series: dict[str, SeriesData] = field(default_factory=dict)
    spot: Optional[float] = None
    spot_source: str = ""
    spot_asof: str = ""
    realized_vol_krw: Optional[float] = None   # ~22d daily-change stdev (won)
    us_m2_yoy: Optional[float] = None          # US M2 YoY growth %
    kr_m2_yoy: Optional[float] = None          # Korea M2 YoY growth %

    def get(self, sid: str) -> Optional[float]:
        s = self.series.get(sid)
        return s.latest_value if s else None

    def _block(self, ids: dict) -> str:
        lines = []
        for sid in ids:
            s = self.series.get(sid)
            if not s or s.latest_value is None:
                continue
            chg = f" (chg {s.change:+.3f})" if s.change is not None else ""
            lines.append(f"  {s.label}: {s.latest_value} [{s.latest_date}]{chg}")
        return "\n".join(lines) if lines else "  (no data)"

    def spot_text(self) -> str:
        if self.spot is None:
            return "USD/KRW spot: unavailable"
        return (f"USD/KRW spot: {self.spot:.2f}원 "
                f"(source: {self.spot_source}, {self.spot_asof})")

    def us_text(self) -> str:
        return "US macro & rates:\n" + self._block(US_SERIES)

    def kr_text(self) -> str:
        return "Korea macro & rates:\n" + self._block(KR_SERIES)

    def risk_text(self) -> str:
        base = "Global risk & dollar:\n" + self._block(RISK_SERIES)
        if self.realized_vol_krw is not None:
            base += f"\n  USD/KRW realized daily vol (~22d): {self.realized_vol_krw:.2f}원"
        return base

    def monetary_text(self) -> str:
        """Monetary model: relative money-supply growth drives the long-run exchange rate."""
        lines = ["Monetary aggregates (통화량) & monetary model:"]
        lines.append(self._block(MONETARY_SERIES))
        if self.us_m2_yoy is not None and self.kr_m2_yoy is not None:
            diff = self.kr_m2_yoy - self.us_m2_yoy
            pressure = ("원화 약세 압력" if diff > 0 else "원화 강세 압력")
            lines.append(f"  M2 YoY: US {self.us_m2_yoy:+.1f}% vs KR {self.kr_m2_yoy:+.1f}% "
                         f"→ 상대 통화증가율 격차 {diff:+.1f}pp ({pressure}; 빠른 통화증가국이 약세)")
        elif self.us_m2_yoy is not None:
            lines.append(f"  US M2 YoY {self.us_m2_yoy:+.1f}% (KR M2 unavailable)")
        return "\n".join(lines)

    def jobs_text(self) -> str:
        """US labor market (고용) — the dominant Fed-path driver."""
        return "US jobs / labor (고용):\n" + self._block(JOBS_SERIES)

    def cb_carry_text(self) -> str:
        """Other DM central banks (ECB/BOJ) + the yen carry trade."""
        lines = ["Global central banks & carry (ECB·BOJ·엔캐리):"]
        lines.append(self._block(GLOBAL_FX_SERIES))
        eur = self.series.get("DEXUSEU")
        jpy = self.series.get("DEXJPUS")
        if eur and eur.latest_value:
            lines.append(f"  ECB-Fed divergence proxy via EUR/USD {eur.latest_value} — "
                         f"강달러(EUR 약세)면 DXY↑ → 원화 약세 동조")
        if jpy and jpy.latest_value:
            lines.append(f"  USD/JPY {jpy.latest_value} — BOJ 초완화/엔약세는 캐리 자금 공급, "
                         f"BOJ 긴축·엔강세 급반전(엔캐리 청산)은 글로벌 리스크오프 → 원화 급락 트리거")
        return "\n".join(lines)

    def financial_text(self) -> str:
        """Equity & bond market linkage to USD/KRW."""
        parts = ["Financial-market linkage (주식·채권 연계):"]
        sp = self.series.get("SP500")
        vix = self.series.get("VIXCLS")
        if sp and sp.latest_value:
            parts.append(f"  S&P500 {sp.latest_value} (chg {sp.change:+.1f}) — 위험선호 시 원화 강세 동조")
        if vix and vix.latest_value:
            parts.append(f"  VIX {vix.latest_value} — 급등 시 외국인 위험자산 회피 → 원화 약세")
        us10, kr10, us2 = self.get("DGS10"), self.get("IRLTLT01KRM156N"), self.get("DGS2")
        if us10 is not None and kr10 is not None:
            parts.append(f"  채권: US10Y {us10}% vs KR10Y {kr10}% (격차 {us10 - kr10:+.2f}pp) "
                         f"→ 외국인 원화채권 자금 유출입 채널")
        parts.append("  (코스피 외국인 순매수·KTB 외국인 보유·서학개미 자금은 뉴스에서 보강)")
        return "\n".join(parts)

    def rate_diff_text(self) -> str:
        us10 = self.get("DGS10")
        kr10 = self.get("IRLTLT01KRM156N")
        us2 = self.get("DGS2")
        parts = []
        if us10 is not None and kr10 is not None:
            parts.append(f"US10Y−KR10Y differential: {us10 - kr10:+.2f}pp "
                         f"(US {us10}% vs KR {kr10}%)")
        if us2 is not None:
            parts.append(f"US 2Y: {us2}% (front-end carry anchor)")
        return "Rate differential (carry):\n  " + ("\n  ".join(parts) if parts else "(insufficient data)")

    def full_text(self) -> str:
        return "\n\n".join([
            self.spot_text(), self.us_text(), self.jobs_text(), self.kr_text(),
            self.risk_text(), self.rate_diff_text(), self.cb_carry_text(),
            self.monetary_text(), self.financial_text(),
        ])


async def _fetch_series(client: httpx.AsyncClient, sid: str, label: str) -> Optional[SeriesData]:
    cache_key = f"fred_{sid}"
    cached = data_cache.get(cache_key, TTL)
    if cached:
        return SeriesData(**cached)
    if not settings.FRED_API_KEY:
        return None
    params = {
        "series_id": sid,
        "api_key": settings.FRED_API_KEY,
        "file_type": "json",
        "limit": 5,
        "sort_order": "desc",
    }
    try:
        r = await client.get(f"{FRED_BASE}/series/observations", params=params)
        r.raise_for_status()
        obs = [o for o in r.json()["observations"] if o["value"] != "."]
    except Exception:
        return None
    if not obs:
        return None
    latest = float(obs[0]["value"])
    prior = float(obs[1]["value"]) if len(obs) > 1 else None
    chg = round(latest - prior, 4) if prior is not None else None
    result = SeriesData(sid, label, latest, obs[0]["date"], prior, chg)
    data_cache.set(cache_key, result.__dict__)
    return result


async def _fetch_spot_yf() -> Optional[tuple[float, str]]:
    """Intraday USD/KRW via yfinance KRW=X. Returns (price, asof) or None."""
    try:
        import yfinance as yf  # optional dependency
    except Exception:
        return None
    try:
        loop = asyncio.get_event_loop()
        def _pull():
            t = yf.Ticker("KRW=X")
            hist = t.history(period="1d", interval="5m")
            if hist is None or hist.empty:
                fi = getattr(t, "fast_info", None)
                if fi and fi.get("last_price"):
                    return float(fi["last_price"]), "yfinance.fast_info"
                return None
            return float(hist["Close"].iloc[-1]), str(hist.index[-1])
        return await loop.run_in_executor(None, _pull)
    except Exception:
        return None


async def _realized_vol(client: httpx.AsyncClient) -> Optional[float]:
    """Stdev of recent DEXKOUS day-over-day changes (won) — ~22 trading days."""
    if not settings.FRED_API_KEY:
        return None
    params = {"series_id": "DEXKOUS", "api_key": settings.FRED_API_KEY,
              "file_type": "json", "limit": 23, "sort_order": "desc"}
    try:
        r = await client.get(f"{FRED_BASE}/series/observations", params=params)
        r.raise_for_status()
        vals = [float(o["value"]) for o in r.json()["observations"] if o["value"] != "."]
    except Exception:
        return None
    if len(vals) < 5:
        return None
    changes = [vals[i] - vals[i + 1] for i in range(len(vals) - 1)]
    import statistics as _st
    return round(_st.pstdev(changes), 3)


def _is_fresh(date_str: str, max_age_days: int = 160) -> bool:
    """Guard against discontinued series — only trust observations within max_age_days."""
    from datetime import date as _date
    try:
        y, m, d = (int(x) for x in date_str.split("-"))
        return (_date.today() - _date(y, m, d)).days <= max_age_days
    except Exception:
        return False


async def _m2_yoy(client: httpx.AsyncClient, sids: list[str]) -> Optional[float]:
    """YoY money-supply growth % (latest vs 12 months ago), from the first FRESH candidate.
    Returns None if every candidate is discontinued/stale — never reports a years-old figure."""
    if not settings.FRED_API_KEY:
        return None
    for sid in sids:
        params = {"series_id": sid, "api_key": settings.FRED_API_KEY,
                  "file_type": "json", "limit": 14, "sort_order": "desc"}
        try:
            r = await client.get(f"{FRED_BASE}/series/observations", params=params)
            r.raise_for_status()
            obs = [o for o in r.json()["observations"] if o["value"] != "."]
        except Exception:
            continue
        if len(obs) < 13 or not _is_fresh(obs[0]["date"]):
            continue
        vals = [float(o["value"]) for o in obs]
        if vals[12] == 0:
            continue
        return round((vals[0] / vals[12] - 1) * 100, 2)
    return None


async def get_snapshot() -> Snapshot:
    snap = Snapshot()
    all_ids = {**US_SERIES, **JOBS_SERIES, **GLOBAL_FX_SERIES, **RISK_SERIES,
               **KR_SERIES, **MONETARY_SERIES, **FX_SERIES}
    async with httpx.AsyncClient(timeout=20) as client:
        tasks = [_fetch_series(client, sid, label) for sid, label in all_ids.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        snap.realized_vol_krw = await _realized_vol(client)
        snap.us_m2_yoy = await _m2_yoy(client, US_M2_CANDIDATES)
        snap.kr_m2_yoy = await _m2_yoy(client, KR_M2_CANDIDATES)
    for r in results:
        if isinstance(r, SeriesData):
            snap.series[r.series_id] = r

    # Spot: prefer yfinance intraday, fall back to FRED DEXKOUS daily.
    yf_spot = await _fetch_spot_yf()
    if yf_spot:
        snap.spot, snap.spot_asof = yf_spot[0], yf_spot[1]
        snap.spot_source = "yfinance KRW=X"
    else:
        dex = snap.series.get("DEXKOUS")
        if dex and dex.latest_value:
            snap.spot, snap.spot_asof = dex.latest_value, dex.latest_date or ""
            snap.spot_source = "FRED DEXKOUS (daily)"
    return snap
