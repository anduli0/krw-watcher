"""
News fetcher — pulls RSS from the source registry, scores items by USD/KRW
materiality, dedupes, and formats a compact text block for the agent context.
No AI tokens. Tolerant of dead feeds.
"""
import asyncio
import html
import re
from dataclasses import dataclass

try:
    import feedparser
except Exception:  # optional dependency — news simply degrades to empty
    feedparser = None

from backend.briefing.sources import ENABLED_SOURCES, FX_KEYWORDS, NewsSource
from backend.data import activity_log as AL


@dataclass
class NewsItem:
    source: str
    title: str
    link: str
    published: str
    score: float


def _score(title: str, weight: float) -> float:
    low = title.lower()
    s = sum(w for kw, w in FX_KEYWORDS.items() if kw in low)
    return s * weight


def _clean(text: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def _fetch_one(src: NewsSource) -> list[NewsItem]:
    if feedparser is None:
        return []
    try:
        feed = feedparser.parse(src.feed_url)
    except Exception:
        return []
    items = []
    for e in feed.entries[: src.max_items]:
        title = _clean(getattr(e, "title", ""))
        if not title:
            continue
        items.append(NewsItem(
            source=src.name,
            title=title,
            link=getattr(e, "link", ""),
            published=getattr(e, "published", ""),
            score=_score(title, src.reliability_weight),
        ))
    return items


async def fetch_news(top_n: int = 30) -> list[NewsItem]:
    loop = asyncio.get_event_loop()
    AL.collecting("News", f"{len(ENABLED_SOURCES)} sources (WSJ/FT/Bloomberg/HBR/연합인포맥스…)")
    tasks = [loop.run_in_executor(None, _fetch_one, s) for s in ENABLED_SOURCES]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_items: list[NewsItem] = []
    for r in results:
        if isinstance(r, list):
            all_items.extend(r)

    # Dedupe by title prefix, keep highest score.
    seen: dict[str, NewsItem] = {}
    for it in all_items:
        key = it.title[:60].lower()
        if key not in seen or it.score > seen[key].score:
            seen[key] = it
    ranked = sorted(seen.values(), key=lambda x: -x.score)
    ranked = [it for it in ranked if it.score > 0][:top_n]
    AL.collected("News", len(ranked), "relevant headlines")
    return ranked


def to_text(items: list[NewsItem], limit: int = 30) -> str:
    if not items:
        return "(no relevant news fetched)"
    lines = [f"- [{it.source}] {it.title}" for it in items[:limit]]
    return "Recent USD/KRW-relevant headlines (ranked by materiality):\n" + "\n".join(lines)
