from sqlalchemy import (
    Column, Integer, String, Float, DateTime, Boolean, Text, ForeignKey, Index,
)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime, timezone, timedelta

Base = declarative_base()

# FX trades on shorter horizons than rates — intraday-to-annual.
HORIZONS = ("1w", "1m", "3m", "12m")

KST = timezone(timedelta(hours=9))


def now_utc():
    return datetime.utcnow()


def today_kst() -> str:
    """Calendar date in KST (the user's market timezone), independent of server TZ."""
    return datetime.now(KST).date().isoformat()


class RunLog(Base):
    __tablename__ = "run_log"
    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, default=now_utc)
    completed_at = Column(DateTime)
    status = Column(String(20), default="running")
    cycle_type = Column(String(20), default="scheduled")
    collaboration_rounds = Column(Integer, default=1)
    spot_at_run = Column(Float)                  # USD/KRW spot when cycle ran
    outputs = relationship("AgentOutput", back_populates="run", cascade="all, delete-orphan")
    forecasts = relationship("HorizonForecast", back_populates="run", cascade="all, delete-orphan")
    signals = relationship("TradeSignal", back_populates="run", cascade="all, delete-orphan")


class AgentOutput(Base):
    __tablename__ = "agent_output"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("run_log.id"))
    agent_id = Column(Integer)
    agent_name = Column(String(60))
    round = Column(Integer, default=1)           # 1=independent, 2=post-collaboration
    signal = Column(String(20))                  # krw_weak | neutral | krw_strong
    delta_krw = Column(Float)                    # 1m delta (won) for quick reference
    horizons_json = Column(Text)                 # {1w,1m,3m,12m}
    confidence = Column(Float)
    weight_applied = Column(Float)
    duration_ms = Column(Integer)
    raw_json = Column(Text)
    run = relationship("RunLog", back_populates="outputs")


class HorizonForecast(Base):
    """One stabilized forecast per horizon per cycle."""
    __tablename__ = "horizon_forecast"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("run_log.id"))
    horizon = Column(String(5))                  # 1w|1m|3m|12m
    published_at = Column(DateTime, default=now_utc)
    target_date = Column(String(10))
    spot_at_run = Column(Float)                  # base spot the delta is relative to
    raw_delta_krw = Column(Float)
    smoothed_delta = Column(Float)
    published_delta = Column(Float)
    implied_rate = Column(Float)                 # spot + published_delta
    confidence = Column(Float)
    signal = Column(String(20))
    trigger_event = Column(String(40))
    unchanged_streak_days = Column(Integer, default=0)
    change_justification = Column(Text)
    is_published = Column(Boolean, default=False)
    report_text = Column(Text)                   # Korean derivation report
    report_text_en = Column(Text)
    run = relationship("RunLog", back_populates="forecasts")


class DataSnapshot(Base):
    """Web/FRED data captured every sweep, no AI tokens."""
    __tablename__ = "data_snapshot"
    id = Column(Integer, primary_key=True)
    collected_at = Column(DateTime, default=now_utc)
    spot = Column(Float)
    macro_text = Column(Text)
    news_text = Column(Text)
    has_new_data = Column(Boolean, default=True)


class FeedbackEntry(Base):
    """Realized error per agent — feeds adaptive weights + negative examples."""
    __tablename__ = "feedback_entry"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("run_log.id"))
    agent_id = Column(Integer)
    horizon = Column(String(5))
    predicted_delta = Column(Float)
    actual_delta = Column(Float)
    divergence_krw = Column(Float)
    negative_example_text = Column(Text)
    created_at = Column(DateTime, default=now_utc)


class RealizedRate(Base):
    """Daily USD/KRW close (FRED DEXKOUS), persisted so any past forecast can be scored
    against what actually happened without needing a live spot. Powers the
    predicted-vs-actual track record and the auto bias-correction feedback loop."""
    __tablename__ = "realized_rate"
    id = Column(Integer, primary_key=True)
    observation_date = Column(String(10), unique=True, index=True)  # YYYY-MM-DD
    rate = Column(Float)
    source = Column(String(24), default="FRED DEXKOUS")
    fetched_at = Column(DateTime, default=now_utc)


class DailyOHLCForecast(Base):
    """Same-day USD/KRW High/Low/Close prediction (quantitative, no LLM), scored against the
    day's actual OHLC — its own predicted-vs-actual feedback loop (band-multiplier calibration)."""
    __tablename__ = "daily_ohlc_forecast"
    id = Column(Integer, primary_key=True)
    forecast_date = Column(String(10), unique=True, index=True)  # the day predicted (KST)
    made_at = Column(DateTime, default=now_utc)
    prev_close = Column(Float)
    pred_open = Column(Float)
    pred_high = Column(Float)
    pred_low = Column(Float)
    pred_close = Column(Float)
    band_mult = Column(Float)            # band multiplier used (feedback-calibrated)
    exp_range = Column(Float)            # expected High−Low
    actual_high = Column(Float)
    actual_low = Column(Float)
    actual_close = Column(Float)
    err_high = Column(Float)             # pred_high − actual_high
    err_low = Column(Float)
    err_close = Column(Float)
    close_in_band = Column(Boolean)      # actual close within [pred_low, pred_high]
    range_contained = Column(Boolean)    # actual High ≤ pred_high AND Low ≥ pred_low
    scored_at = Column(DateTime)


class TradeSignal(Base):
    """Translation of a forecast into an actionable position — the broker bridge."""
    __tablename__ = "trade_signal"
    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("run_log.id"))
    created_at = Column(DateTime, default=now_utc)
    horizon = Column(String(5))                  # which horizon drove the trade
    side = Column(String(8))                     # LONG | SHORT | FLAT (USD/KRW)
    spot_entry = Column(Float)
    target = Column(Float)
    stop = Column(Float)
    notional_usd = Column(Float)
    confidence = Column(Float)
    expected_edge_krw = Column(Float)
    rationale = Column(Text)
    status = Column(String(16), default="proposed")  # proposed|paper_filled|live_filled|closed
    run = relationship("RunLog", back_populates="signals")


class PaperPosition(Base):
    """Open/closed simulated position for the mock-trading P&L track record."""
    __tablename__ = "paper_position"
    id = Column(Integer, primary_key=True)
    signal_id = Column(Integer, ForeignKey("trade_signal.id"))
    side = Column(String(8))
    notional_usd = Column(Float)
    entry_rate = Column(Float)
    exit_rate = Column(Float)
    pnl_krw = Column(Float)
    pnl_pct = Column(Float)
    opened_at = Column(DateTime, default=now_utc)
    closed_at = Column(DateTime)
    status = Column(String(12), default="open")  # open | closed


class NewsArticle(Base):
    """Per-headline news archive, grouped by KST date and pruned to a retention window.
    Lets the dashboard show news day-by-day and lets the daily brief synthesize the
    full day's scraped headlines (not just the latest 30-min sweep)."""
    __tablename__ = "news_article"
    id = Column(Integer, primary_key=True)
    article_date = Column(String(10), index=True)    # YYYY-MM-DD (KST), date first seen
    first_seen = Column(DateTime, default=now_utc)
    source = Column(String(80))
    title = Column(String(500))
    link = Column(String(800))
    published = Column(String(120))                  # source-reported publish string
    score = Column(Float, default=0.0)               # USD/KRW materiality score
    dedup_key = Column(String(160), index=True)      # normalized title prefix, for de-dup


# Composite index: the dashboard reads "recent dates, best-scored first".
Index("ix_news_date_score", NewsArticle.article_date, NewsArticle.score)


class DailyBriefing(Base):
    __tablename__ = "daily_briefing"
    id = Column(Integer, primary_key=True)
    briefing_date = Column(String(10))
    language = Column(String(5))
    title = Column(String(500))
    headline = Column(String(500))
    summary_json = Column(Text)
    sources_json = Column(Text)
    article_count = Column(Integer, default=0)
    status = Column(String(20), default="draft")
    created_at = Column(DateTime, default=now_utc)
