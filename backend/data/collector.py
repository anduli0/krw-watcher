"""
Data collector — the no-token sweep that feeds the agent committee.

Pulls FRED macro + USD/KRW spot, BOK base rate (optional), and ranked news, then
assembles the text blocks each agent reads. Runs on a schedule independent of the
AI cycle so fresh data is always cached when the committee fires.
"""
import asyncio
import logging
from datetime import datetime

from backend.data import activity_log as AL
from backend.data.fx_client import get_snapshot, Snapshot
from backend.data.bok_client import fetch_base_rate, base_rate_text
from backend.briefing.news import fetch_news, to_text, NewsItem

logger = logging.getLogger("krw_watcher.collector")

_LATEST: dict = {
    "snapshot": None,        # fx_client.Snapshot
    "bok": None,
    "news_items": [],
    "collected_at": None,
}


def get_latest() -> dict:
    return _LATEST


async def _last_known_spot() -> tuple[float, str] | None:
    """Most recent realized USD/KRW close from the DB — a network-free spot fallback."""
    try:
        from backend.database.init_db import AsyncSessionLocal
        from backend.database import crud
        async with AsyncSessionLocal() as db:
            row = await crud.latest_realized_rate(db)
        if row and row.rate:
            return float(row.rate), str(row.observation_date)
    except Exception as e:
        logger.warning("spot fallback (realized_rate) failed: %s", e)
    return None


async def collect_data() -> dict:
    AL.system_event("Data sweep started (no AI tokens)")
    started = datetime.utcnow()
    AL.collecting("FRED + spot", "stlouisfed.org + yfinance KRW=X")

    results = await asyncio.gather(
        get_snapshot(),
        fetch_base_rate(),
        fetch_news(top_n=45),
        return_exceptions=True,
    )
    snapshot, bok, news = [(None if isinstance(r, Exception) else r) for r in results]

    if not isinstance(snapshot, Snapshot):
        snapshot = Snapshot()
        AL.collect_failed("FRED + spot", "snapshot failed")

    if snapshot.spot:
        AL.collected("FRED + spot", len(snapshot.series), f"series · spot {snapshot.spot:.2f}원")
    else:
        # Spot fallback: live sources (yfinance/FRED) returned empty — common on a
        # weekend/holiday with markets closed, or a transient outage. Reuse the last
        # stored realized close so a blank spot never trips the data-availability cap
        # and collapses the committee's confidence to zero.
        fb = await _last_known_spot()
        if fb:
            snapshot.spot, snapshot.spot_asof = fb[0], fb[1]
            snapshot.spot_source = f"last close (DB {fb[1]})"
            AL.collected("spot (fallback)", len(snapshot.series),
                         f"last close {fb[0]:.2f}원 (DB {fb[1]})")
        else:
            AL.collect_failed("spot", "no USD/KRW spot (yfinance/FRED/DB all empty)")

    _LATEST.update({
        "snapshot": snapshot,
        "bok": bok,
        "news_items": news or [],
        "collected_at": started.isoformat(),
    })

    # Archive today's scrape (dated + deduped) and prune the retention window so the
    # dashboard can show news day-by-day and the daily brief sees the full day's news.
    try:
        from backend.database.init_db import AsyncSessionLocal
        from backend.database import crud
        from backend.database.models import today_kst
        from backend.config import settings
        async with AsyncSessionLocal() as db:
            added = await crud.archive_news(db, news or [], today_kst())
            pruned = await crud.prune_old_news(db, settings.NEWS_RETENTION_DAYS)
        if added or pruned:
            AL.system_event(f"News archive: +{added} new · -{pruned} pruned ({today_kst()})")
    except Exception as e:
        logger.warning("News archiving failed: %s", e)

    elapsed = (datetime.utcnow() - started).total_seconds()
    AL.system_event(f"Data sweep complete in {elapsed:.1f}s · "
                    f"spot {snapshot.spot if snapshot.spot else 'n/a'} · {len(news or [])} headlines")
    return {"collected_at": _LATEST["collected_at"], "duration_s": elapsed}


def _news_subset(items: list[NewsItem], categories: tuple[str, ...] | None = None,
                 contains: tuple[str, ...] | None = None, limit: int = 12) -> str:
    """Filter ranked news by source category or keyword for a targeted context block."""
    from backend.briefing.sources import ENABLED_SOURCES
    cat_by_name = {s.name: s.category for s in ENABLED_SOURCES}
    out = []
    for it in items:
        if categories and cat_by_name.get(it.source) not in categories:
            continue
        if contains and not any(c in it.title.lower() for c in contains):
            continue
        out.append(f"- [{it.source}] {it.title}")
        if len(out) >= limit:
            break
    return "\n".join(out) if out else "(none)"


def _structural_view() -> str:
    """The desk's persistent structural theses (editable in agents/house_view.py)."""
    try:
        from backend.agents.house_view import structural_view_text
        return structural_view_text()
    except Exception:
        return ""


def build_context(negative_examples: list[str] | None = None, event: dict | None = None):
    """Assemble an AgentContext from the latest sweep."""
    from backend.agents.base_agent import AgentContext
    snap: Snapshot = _LATEST.get("snapshot") or Snapshot()
    bok = _LATEST.get("bok")
    items: list[NewsItem] = _LATEST.get("news_items") or []

    spot = snap.spot
    flows = ""
    dex = snap.series.get("DEXKOUS")
    if dex and dex.change is not None:
        flows = f"USD/KRW daily change (FRED): {dex.change:+.2f}원"

    capital_flows = _news_subset(items, contains=(
        "외국인", "자금", "유입", "유출", "순매수", "순매도", "국민연금", "서학개미",
        "채권", "코스피", "capital", "inflow", "outflow", "foreign", "bond", "equity"), limit=12)

    return AgentContext(
        spot=spot,
        spot_text=snap.spot_text(),
        us_macro_text=snap.us_text(),
        kr_macro_text=snap.kr_text(),
        rate_diff_text=snap.rate_diff_text(),
        global_risk_text=snap.risk_text(),
        flows_text=flows,
        bok_text=base_rate_text(bok),
        monetary_text=snap.monetary_text(),
        financial_text=snap.financial_text(),
        capital_flows_text=capital_flows,
        jobs_text=snap.jobs_text(),
        cb_carry_text=snap.cb_carry_text(),
        news_text=to_text(items, limit=30),
        ib_consensus_text=_news_subset(items, categories=("analysis",), limit=12),
        intl_bodies_text=_news_subset(
            items, contains=("imf", "bis", "oecd", "valuation", "reer", "fair value")),
        negative_examples=negative_examples or [],
        material_event=event.get("label") if event else None,
        structural_view=_structural_view(),
    )
