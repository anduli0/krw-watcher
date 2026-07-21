"""
Source registry for USD/KRW news & analysis.

Authority tiers:
  • official  — BOK, 기획재정부, Fed, US Treasury (highest reliability).
  • analysis  — WSJ, Financial Times, Bloomberg, Reuters, Harvard Business Review,
                Project Syndicate (credible markets/econ journalism & academia).
  • korea     — 연합인포맥스, Yonhap, domestic FX coverage.

Google News RSS is used for paywalled/blocked outlets (WSJ/FT/Bloomberg/Reuters) and
Korean queries because it is reliable from any server and returns the publication's
headline + link. Direct RSS is used where the outlet publishes an open feed (HBR, Fed).
"""
from dataclasses import dataclass

_GN_EN = "https://news.google.com/rss/search?hl=en-US&gl=US&ceid=US:en&q="
_GN_KR = "https://news.google.com/rss/search?hl=ko&gl=KR&ceid=KR:ko&q="


@dataclass
class NewsSource:
    id: str
    name: str
    category: str          # official | analysis | korea
    feed_url: str
    reliability_weight: float
    max_items: int = 12
    enabled: bool = True


SOURCES: list[NewsSource] = [
    # ── Authoritative markets/econ journalism (via Google News scoping) ──────
    NewsSource("wsj_fx", "Wall Street Journal — Dollar/FX", "analysis",
               _GN_EN + "dollar+won+OR+%22USD/KRW%22+OR+currency+site:wsj.com", 0.95, 8),
    NewsSource("ft_fx", "Financial Times — Korea/FX", "analysis",
               _GN_EN + "Korea+won+OR+dollar+OR+currency+site:ft.com", 0.95, 8),
    NewsSource("bbg_fx", "Bloomberg — KRW / Asia FX", "analysis",
               _GN_EN + "Korean+won+OR+dollar+Asia+currency+site:bloomberg.com", 0.9, 8),
    NewsSource("reuters_fx", "Reuters — Korea won / FX", "analysis",
               _GN_EN + "Korean+won+dollar+exchange+rate+site:reuters.com", 0.9, 10),
    NewsSource("hbr", "Harvard Business Review", "analysis",
               "https://hbr.org/feed", 0.8, 6),
    NewsSource("project_syndicate", "Project Syndicate", "analysis",
               "https://www.project-syndicate.org/rss", 0.8, 6),

    # ── Major foreign outlets (주요 외신) ─────────────────────────────────────
    NewsSource("nikkei", "Nikkei Asia (일본·BOJ·엔)", "analysis",
               _GN_EN + "yen+BOJ+OR+Korea+won+OR+dollar+currency+site:asia.nikkei.com", 0.9, 8),
    NewsSource("scmp", "South China Morning Post (중국·위안)", "analysis",
               _GN_EN + "yuan+China+OR+won+OR+dollar+currency+site:scmp.com", 0.82, 6),
    NewsSource("economist", "The Economist", "analysis",
               _GN_EN + "dollar+OR+currency+OR+Korea+economy+site:economist.com", 0.9, 5),
    NewsSource("cnbc", "CNBC Markets", "analysis",
               _GN_EN + "dollar+won+OR+Fed+OR+currency+Asia+site:cnbc.com", 0.82, 8),
    NewsSource("marketwatch", "MarketWatch", "analysis",
               _GN_EN + "dollar+index+OR+won+OR+currency+Fed+site:marketwatch.com", 0.8, 6),
    NewsSource("ap_biz", "AP Business", "news",
               _GN_EN + "dollar+OR+won+OR+Fed+OR+economy+currency+site:apnews.com", 0.85, 6),
    NewsSource("guardian_econ", "The Guardian — Economics", "analysis",
               "https://www.theguardian.com/business/economics/rss", 0.8, 6),
    NewsSource("bbc_biz", "BBC Business", "news",
               "https://feeds.bbci.co.uk/news/business/rss.xml", 0.82, 6),
    NewsSource("yonhap_en", "Yonhap News (한국 영문)", "korea",
               _GN_EN + "Korean+won+OR+exchange+rate+OR+Bank+of+Korea+site:en.yna.co.kr", 0.85, 6),
    NewsSource("korea_herald", "Korea Herald (한국 영문)", "korea",
               _GN_EN + "won+OR+exchange+rate+OR+BOK+Korea+economy+site:koreaherald.com", 0.82, 6),

    # ── Thematic Google News queries (the workhorse coverage) ────────────────
    NewsSource("gn_usdkrw", "USD/KRW exchange rate", "analysis",
               _GN_EN + "%22Korean+won%22+dollar+exchange+rate+forecast", 0.85, 12),
    NewsSource("gn_dxy", "US dollar index / DXY", "analysis",
               _GN_EN + "US+dollar+index+DXY+strength+Federal+Reserve", 0.8, 8),
    NewsSource("gn_cny", "Chinese yuan / PBoC", "analysis",
               _GN_EN + "Chinese+yuan+PBoC+fixing+depreciation", 0.8, 6),
    NewsSource("gn_kr_exports", "Korea exports / semiconductors", "analysis",
               _GN_EN + "Korea+exports+semiconductor+trade+balance+current+account", 0.8, 6),

    # ── Korean-language coverage ─────────────────────────────────────────────
    NewsSource("gn_krw_ko", "원/달러 환율", "korea",
               _GN_KR + "%EC%9B%90%EB%8B%AC%EB%9F%AC+%ED%99%98%EC%9C%A8", 0.85, 12),
    NewsSource("gn_bok_ko", "한국은행 기준금리", "korea",
               _GN_KR + "%ED%95%9C%EA%B5%AD%EC%9D%80%ED%96%89+%EA%B8%B0%EC%A4%80%EA%B8%88%EB%A6%AC", 0.9, 8),
    NewsSource("gn_infomax_ko", "연합인포맥스 외환", "korea",
               _GN_KR + "%EC%99%B8%ED%99%98+%ED%99%98%EC%9C%A8+site:einfomax.co.kr", 0.85, 8),
    NewsSource("gn_mof_ko", "기획재정부 외환시장", "korea",
               _GN_KR + "%EA%B8%B0%ED%9A%8D%EC%9E%AC%EC%A0%95%EB%B6%80+%EC%99%B8%ED%99%98%EC%8B%9C%EC%9E%A5", 0.85, 6),

    # ── Official feeds ───────────────────────────────────────────────────────
    NewsSource("fed_press", "Federal Reserve Press", "official",
               "https://www.federalreserve.gov/feeds/press_all.xml", 1.0, 6),
    NewsSource("ustreasury", "US Treasury (Google News)", "official",
               _GN_EN + "US+Treasury+dollar+yields+issuance", 0.9, 6),
]

ENABLED_SOURCES = [s for s in SOURCES if s.enabled]

# Relevance keywords for scoring USD/KRW materiality.
FX_KEYWORDS: dict[str, float] = {
    "won": 3.0, "krw": 3.0, "원": 3.0, "원화": 3.0, "환율": 3.0, "원달러": 3.5, "원/달러": 3.5,
    "usd/krw": 3.5, "dollar": 2.0, "exchange rate": 2.5, "currency": 2.0,
    "dxy": 2.5, "dollar index": 2.5, "intervention": 2.5, "개입": 2.5,
    "기준금리": 2.5, "한국은행": 2.5, "bank of korea": 2.5, "bok": 2.5,
    "federal reserve": 2.5, "fed": 2.0, "fomc": 2.5, "rate cut": 2.0, "rate hike": 2.0,
    "yuan": 2.0, "cny": 2.0, "pboc": 2.0, "위안": 2.0,
    "current account": 2.0, "trade balance": 2.0, "exports": 1.5, "수출": 1.5, "반도체": 1.5,
    "semiconductor": 1.5, "treasury": 1.5, "yield": 1.5, "vix": 1.5, "risk-off": 2.0,
    "carry trade": 2.5, "capital outflow": 2.0, "외국인": 1.5, "코스피": 1.2,
    "기획재정부": 2.0, "국민연금": 2.0, "nps": 1.5,
}
