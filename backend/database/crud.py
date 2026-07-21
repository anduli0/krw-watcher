import re
from datetime import datetime, timedelta
from sqlalchemy import select, desc, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from backend.database.models import (
    RunLog, AgentOutput, HorizonForecast, FeedbackEntry, TradeSignal, PaperPosition,
    NewsArticle, RealizedRate, DailyOHLCForecast, KST, today_kst,
)


# ── Runs ──────────────────────────────────────────────────────────────────────
async def create_run(db: AsyncSession, cycle_type: str, spot: float | None) -> RunLog:
    run = RunLog(cycle_type=cycle_type, status="running", spot_at_run=spot)
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


async def complete_run(db: AsyncSession, run_id: int, status: str, rounds: int = 1):
    run = await db.get(RunLog, run_id)
    if run:
        run.status = status
        run.completed_at = datetime.utcnow()
        run.collaboration_rounds = rounds
        await db.commit()


async def save_agent_output(db: AsyncSession, data: dict):
    db.add(AgentOutput(**data))
    await db.commit()


# ── Horizon forecasts ──────────────────────────────────────────────────────────
async def save_horizon_forecast(db: AsyncSession, data: dict):
    db.add(HorizonForecast(**data))
    await db.commit()


async def get_latest_horizon_forecast(db: AsyncSession, horizon: str) -> HorizonForecast | None:
    res = await db.execute(
        select(HorizonForecast)
        .where(HorizonForecast.horizon == horizon)
        .order_by(desc(HorizonForecast.id))
        .limit(1)
    )
    return res.scalar_one_or_none()


async def get_horizon_history(db: AsyncSession, horizon: str, limit: int = 8):
    res = await db.execute(
        select(HorizonForecast)
        .where(HorizonForecast.horizon == horizon)
        .order_by(desc(HorizonForecast.id))
        .limit(limit)
    )
    return list(res.scalars().all())


async def get_published_forecasts(db: AsyncSession) -> list[HorizonForecast]:
    """Latest published forecast for each horizon."""
    from backend.database.models import HORIZONS
    out = []
    for h in HORIZONS:
        res = await db.execute(
            select(HorizonForecast)
            .where(HorizonForecast.horizon == h, HorizonForecast.is_published == True)  # noqa: E712
            .order_by(desc(HorizonForecast.id))
            .limit(1)
        )
        row = res.scalar_one_or_none()
        if row:
            out.append(row)
    return out


# ── Trade signals + paper positions ─────────────────────────────────────────────
async def save_trade_signal(db: AsyncSession, data: dict) -> TradeSignal:
    sig = TradeSignal(**data)
    db.add(sig)
    await db.commit()
    await db.refresh(sig)
    return sig


async def open_paper_position(db: AsyncSession, data: dict) -> PaperPosition:
    pos = PaperPosition(**data)
    db.add(pos)
    await db.commit()
    await db.refresh(pos)
    return pos


async def get_open_positions(db: AsyncSession) -> list[PaperPosition]:
    res = await db.execute(select(PaperPosition).where(PaperPosition.status == "open"))
    return list(res.scalars().all())


async def get_recent_signals(db: AsyncSession, limit: int = 20) -> list[TradeSignal]:
    res = await db.execute(select(TradeSignal).order_by(desc(TradeSignal.id)).limit(limit))
    return list(res.scalars().all())


# ── Feedback ────────────────────────────────────────────────────────────────────
async def save_feedback(db: AsyncSession, data: dict):
    db.add(FeedbackEntry(**data))
    await db.commit()


async def get_negative_examples(db: AsyncSession, limit: int = 10) -> list[str]:
    res = await db.execute(
        select(FeedbackEntry.negative_example_text)
        .where(FeedbackEntry.negative_example_text.isnot(None))
        .order_by(desc(FeedbackEntry.id))
        .limit(limit)
    )
    return [r for (r,) in res.all() if r]


# ── Realized rates (daily DEXKOUS) — score forecasts vs what actually happened ───
async def upsert_realized_rates(db: AsyncSession, rows: list[tuple[str, float]]) -> int:
    """Insert any (date, rate) not already stored. Idempotent (date is unique)."""
    if not rows:
        return 0
    existing = {d for (d,) in (await db.execute(select(RealizedRate.observation_date))).all()}
    added = 0
    for d, r in rows:
        if d in existing or r is None:
            continue
        db.add(RealizedRate(observation_date=d, rate=float(r)))
        existing.add(d)
        added += 1
    if added:
        await db.commit()
    return added


async def count_realized(db: AsyncSession) -> int:
    return (await db.execute(select(func.count(RealizedRate.id)))).scalar() or 0


async def max_realized_date(db: AsyncSession) -> str | None:
    return (await db.execute(select(func.max(RealizedRate.observation_date)))).scalar()


async def latest_realized_rate(db: AsyncSession) -> RealizedRate | None:
    """Most recent stored USD/KRW close — a network-free spot fallback for when the
    live sources (yfinance/FRED) come back empty (weekend/holiday/transient outage),
    so a blank spot never collapses the committee's confidence to zero."""
    res = await db.execute(
        select(RealizedRate).order_by(desc(RealizedRate.observation_date)).limit(1))
    return res.scalar_one_or_none()


async def get_realized_on_or_after(db: AsyncSession, date_str: str) -> RealizedRate | None:
    """First observed close on or after a target date (the realized outcome of a forecast)."""
    res = await db.execute(
        select(RealizedRate).where(RealizedRate.observation_date >= date_str)
        .order_by(RealizedRate.observation_date).limit(1))
    return res.scalar_one_or_none()


async def get_realized_series(db: AsyncSession, start_date: str | None = None,
                              limit: int = 1200) -> list[RealizedRate]:
    q = select(RealizedRate)
    if start_date:
        q = q.where(RealizedRate.observation_date >= start_date)
    return list((await db.execute(q.order_by(RealizedRate.observation_date).limit(limit))).scalars().all())


# ── Daily OHLC (High/Low/Close) forecasts ───────────────────────────────────────
async def get_daily_ohlc(db: AsyncSession, forecast_date: str) -> DailyOHLCForecast | None:
    return (await db.execute(select(DailyOHLCForecast)
            .where(DailyOHLCForecast.forecast_date == forecast_date))).scalar_one_or_none()


async def save_daily_ohlc(db: AsyncSession, data: dict) -> DailyOHLCForecast:
    row = DailyOHLCForecast(**data)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def get_recent_daily_ohlc(db: AsyncSession, limit: int = 140) -> list[DailyOHLCForecast]:
    rows = (await db.execute(select(DailyOHLCForecast)
            .order_by(desc(DailyOHLCForecast.forecast_date)).limit(limit))).scalars().all()
    return list(reversed(rows))   # chronological


async def get_unscored_daily_ohlc(db: AsyncSession, before_date: str) -> list[DailyOHLCForecast]:
    """Past forecasts (date < today) without an actual yet — ready to be scored."""
    return list((await db.execute(select(DailyOHLCForecast)
            .where(DailyOHLCForecast.actual_close.is_(None),
                   DailyOHLCForecast.forecast_date < before_date)
            .order_by(DailyOHLCForecast.forecast_date))).scalars().all())


async def update_daily_ohlc(db: AsyncSession, row_id: int, data: dict):
    row = await db.get(DailyOHLCForecast, row_id)
    if row:
        for k, v in data.items():
            setattr(row, k, v)
        await db.commit()


# ── News archive (dated, deduped, pruned) ───────────────────────────────────────
def _dedup_key(title: str) -> str:
    return re.sub(r"\s+", " ", (title or "")).strip().lower()[:120]


async def archive_news(db: AsyncSession, items: list, day: str | None = None) -> int:
    """Persist newly-seen headlines under today's KST date. De-dupes against what's
    already archived (by normalized title) so repeated sweeps don't duplicate rows.
    `items` are NewsItem-like (need .source/.title/.link/.score, optional .published)."""
    if not items:
        return 0
    day = day or today_kst()
    keys = list({_dedup_key(it.title) for it in items if getattr(it, "title", None)})
    if not keys:
        return 0
    existing = {k for (k,) in (await db.execute(
        select(NewsArticle.dedup_key).where(NewsArticle.dedup_key.in_(keys)))).all()}
    added = 0
    for it in items:
        title = getattr(it, "title", None)
        if not title:
            continue
        k = _dedup_key(title)
        if k in existing:
            continue
        existing.add(k)
        db.add(NewsArticle(
            article_date=day, source=getattr(it, "source", ""), title=title,
            link=getattr(it, "link", ""), published=str(getattr(it, "published", "") or "")[:120],
            score=float(getattr(it, "score", 0.0) or 0.0), dedup_key=k))
        added += 1
    if added:
        await db.commit()
    return added


async def prune_old_news(db: AsyncSession, keep_days: int = 14) -> int:
    """Delete archived news older than the retention window (sequential cleanup)."""
    cutoff = (datetime.now(KST).date() - timedelta(days=max(1, keep_days) - 1)).isoformat()
    res = await db.execute(delete(NewsArticle).where(NewsArticle.article_date < cutoff))
    await db.commit()
    return res.rowcount or 0


async def get_news_days(db: AsyncSession, days: int = 7, per_day: int = 40) -> list[dict]:
    """Recent news grouped by KST date, newest date first, best-scored first within a day."""
    cutoff = (datetime.now(KST).date() - timedelta(days=max(1, days) - 1)).isoformat()
    rows = (await db.execute(
        select(NewsArticle).where(NewsArticle.article_date >= cutoff)
        .order_by(desc(NewsArticle.article_date), desc(NewsArticle.score), desc(NewsArticle.id))
    )).scalars().all()
    grouped: dict[str, list] = {}
    for r in rows:
        bucket = grouped.setdefault(r.article_date, [])
        if len(bucket) < per_day:
            bucket.append({"source": r.source, "title": r.title, "link": r.link,
                           "published": r.published, "score": round(r.score or 0.0, 1)})
    return [{"date": d, "count": len(grouped[d]), "articles": grouped[d]}
            for d in sorted(grouped, reverse=True)]


async def get_recent_news_rows(db: AsyncSession, days: int = 1, limit: int = 40) -> list[NewsArticle]:
    """Archived headlines for the brief — today's (and optionally prior days') scrape."""
    cutoff = (datetime.now(KST).date() - timedelta(days=max(1, days) - 1)).isoformat()
    return list((await db.execute(
        select(NewsArticle).where(NewsArticle.article_date >= cutoff)
        .order_by(desc(NewsArticle.score), desc(NewsArticle.id)).limit(limit)
    )).scalars().all())
