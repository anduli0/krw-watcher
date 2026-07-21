import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from backend.config import settings
from backend.database.init_db import init_db, AsyncSessionLocal
from backend.database.models import HORIZONS
from backend.scheduler.window_manager import init_scheduler, scheduler

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("krw_watcher")

# Hardware lock (skipped in DEV_MODE). A money-handling server refuses unexpected hardware.
from backend.auth.mac_validator import validate_or_exit  # noqa: E402
validate_or_exit()


# ── No-token data sweep ───────────────────────────────────────────────────────
async def run_data_collection():
    from backend.data.collector import collect_data
    try:
        await collect_data()
    except Exception as e:
        logger.error("Data collection failed: %s", e)
    # Keep realized-rate history current so newly-matured forecasts get scored — this is
    # what keeps the auto-improvement feedback loop running going forward (not just at boot).
    try:
        from backend.accuracy.track import ensure_realized_history
        async with AsyncSessionLocal() as db:
            await ensure_realized_history(db, force=True)
    except Exception as e:
        logger.warning("Realized-rate refresh failed: %s", e)
    # Daily High/Low/Close prediction: generate today's + score matured days (no LLM tokens).
    try:
        from backend.accuracy.daily_ohlc import daily_tick
        async with AsyncSessionLocal() as db:
            await daily_tick(db, force=True)
    except Exception as e:
        logger.warning("Daily OHLC tick failed: %s", e)
    # Self-healing daily report guarantee: the first sweep after DAILY_REPORT_HOUR_KST
    # generates today's report (AI cycle + brief) if it is missing — so a missed schedule
    # or a DATA-ONLY container still produces one report/day. Idempotent + bounded.
    try:
        await run_daily_report_guarantee()
    except Exception as e:
        logger.warning("Daily report guarantee failed: %s", e)


# ── Daily brief → Telegram ────────────────────────────────────────────────────
async def run_daily_brief():
    from backend.briefing.generator import generate_and_send
    try:
        async with AsyncSessionLocal() as db:
            await generate_and_send(db, send=True)
        logger.info("Daily brief generated + Telegram delivered.")
    except Exception as e:
        logger.error("Daily brief failed: %s", e)


# ── Daily report guarantee (one report/day, resilient) ────────────────────────
async def run_daily_report_guarantee(force: bool = False) -> dict:
    """Ensure today's report exists; generate it (AI cycle + brief) if missing.
    Wired into the 30-min sweep, a daily cron, and POST /api/report/daily."""
    from backend.scheduler.daily_guarantee import ensure_daily_report
    return await ensure_daily_report(
        AsyncSessionLocal, trigger_cycle, run_daily_brief, force=force)


async def startup_brief():
    """On boot, ensure a brief exists for today — so a reboot mid-day still shows/sends one.
    Runs at most once per day (skips if today's brief already exists). Honors DISABLE_AUTO_CYCLE."""
    import asyncio as _a
    from datetime import date
    if settings.DISABLE_AUTO_CYCLE:
        return
    await _a.sleep(20)   # let the first data sweep populate
    try:
        from sqlalchemy import select
        from backend.database.models import DailyBriefing
        today = date.today().isoformat()
        async with AsyncSessionLocal() as db:
            exists = (await db.execute(
                select(DailyBriefing.id).where(
                    DailyBriefing.briefing_date == today,
                    DailyBriefing.language == "ko"))).first()
        if exists:
            logger.info("Startup brief skipped (today's brief already exists).")
            return
        logger.info("No brief for today yet — generating startup brief…")
        await run_daily_brief()
    except Exception as e:
        logger.error("Startup brief check failed: %s", e)


# ── Core AI cycle ─────────────────────────────────────────────────────────────
async def trigger_cycle(cycle_type: str = "scheduled"):
    from backend.agents.orchestrator import run_full_cycle, compute_adaptive_weights
    from backend.data.collector import get_latest, collect_data, build_context
    from backend.stabilizer.event_calendar import get_today_event
    from backend.stabilizer.forecast_stabilizer import stabilize
    from backend.signals.trade_signal import build_trade_signal
    from backend.feedback.feedback_loop import evaluate_matured_forecasts
    from backend.feedback import bias_correction
    from backend.database import crud

    logger.info("Cycle start: %s", cycle_type)
    if get_latest().get("snapshot") is None:
        await collect_data()

    async with AsyncSessionLocal() as db:
        event = await get_today_event()
        # Close the learning loop: score matured forecasts vs realized spot first,
        # so fresh negative examples are available to this cycle's agents.
        snap_now = get_latest().get("snapshot")
        spot_now = getattr(snap_now, "spot", None)
        try:
            await evaluate_matured_forecasts(db, spot_now)
        except Exception as e:
            logger.warning("Feedback evaluation failed: %s", e)
        # Mark open paper positions to market; close any that hit target/stop/time.
        try:
            from backend.signals.position_manager import manage_open_positions
            await manage_open_positions(db, spot_now)
        except Exception as e:
            logger.warning("Position management failed: %s", e)
        neg = await crud.get_negative_examples(db)
        ctx = build_context(negative_examples=neg, event=event)
        await compute_adaptive_weights(db, event)

        run = await crud.create_run(db, cycle_type, ctx.spot)
        try:
            result = await run_full_cycle(ctx, cycle_type)
        except Exception as e:
            await crud.complete_run(db, run.id, "failed")
            logger.error("Cycle failed: %s", e)
            return

        for ar in result["agent_results"]:
            await crud.save_agent_output(db, {
                "run_id": run.id,
                "agent_id": ar["agent_id"],
                "agent_name": ar["agent_name"],
                "round": ar.get("round", 1),
                "signal": ar["signal"],
                "delta_krw": ar["delta_krw"],
                "horizons_json": json.dumps(ar.get("horizons", {})),
                "confidence": ar["confidence"],
                "weight_applied": ar["weight_applied"],
                "duration_ms": ar["duration_ms"],
                "raw_json": json.dumps(ar)[:6000],
            })

        rounds = 2 if result.get("collaboration", {}).get("agents_revised") else 1
        await crud.complete_run(db, run.id, "completed", rounds)

        # Stash the hierarchical synthesis (학계/전문분석/수석) for the dashboard.
        try:
            from backend.data import runtime_state
            runtime_state.set_hierarchy(result.get("hierarchy"), result.get("timestamp"))
        except Exception:
            pass

        # ── Stabilize + persist each horizon ──
        spot = ctx.spot
        changed_h, pub_view = [], {}
        # Auto accuracy-feedback: per-horizon bias/scale/confidence corrections learned
        # from the committee's own realized errors (identity until forecasts mature; the
        # confidence ceiling — reflecting measured forecastability — applies immediately).
        try:
            from backend.accuracy.track import ensure_realized_history
            await ensure_realized_history(db)   # fresh realized rates before scoring
        except Exception:
            pass
        try:
            adj_all = await bias_correction.compute_horizon_adjustments(db)
        except Exception as e:
            logger.warning("Bias-correction failed: %s", e)
            adj_all = {}
        for h in HORIZONS:
            agg = result["horizons"][h]
            prev = await crud.get_latest_horizon_forecast(db, h)
            prev_delta = prev.published_delta if prev else 0.0
            prev_streak = prev.unchanged_streak_days if prev else 0
            recent = []
            try:
                hist = await crud.get_horizon_history(db, h, limit=8)
                recent = [float(x.raw_delta_krw) for x in hist if x.raw_delta_krw is not None]
            except Exception:
                pass
            corr_delta, corr_conf, _adj = bias_correction.apply(
                adj_all, h, agg["weighted_delta_krw"], agg["confidence"])
            st = stabilize(corr_delta, corr_conf,
                           prev_delta, prev_streak, event=event,
                           bypass_ema=(cycle_type in ("forced", "session_open", "session_close")),
                           recent_raw_deltas=recent)
            sig = ("krw_weak" if st.published_delta >= 6 else
                   "krw_strong" if st.published_delta <= -6 else "neutral")
            justification = None
            if st.changed and prev is not None:
                try:
                    from backend.stabilizer.change_justifier import justify_change
                    justification = await justify_change(
                        h, st.published_delta, prev_delta, event, result["agent_results"])
                except Exception:
                    justification = None
            await crud.save_horizon_forecast(db, {
                "run_id": run.id, "horizon": h, "target_date": date.today().isoformat(),
                "spot_at_run": spot,
                "raw_delta_krw": st.raw_delta, "smoothed_delta": st.smoothed_delta,
                "published_delta": st.published_delta,
                "implied_rate": round(spot + st.published_delta, 2) if spot else None,
                "confidence": corr_conf, "signal": sig,
                "trigger_event": event.get("label") if event else None,
                "unchanged_streak_days": st.unchanged_streak,
                "change_justification": justification,
                "is_published": True,
                "report_text": result.get("report_ko") if h == "1m" else None,
                "report_text_en": result.get("report_en") if h == "1m" else None,
            })
            if st.changed:
                changed_h.append(h)
            pub_view[h] = {"delta": st.published_delta,
                           "implied": round(spot + st.published_delta, 2) if spot else None,
                           "signal": sig, "conf": corr_conf}

        # ── Forecast → trade signal → paper execution ──
        published = {h: (await crud.get_latest_horizon_forecast(db, h)) for h in HORIZONS}
        pub_aggregates = {
            h: {"weighted_delta_krw": (published[h].published_delta if published[h] else 0.0),
                "confidence": (published[h].confidence if published[h] else 0.0)}
            for h in HORIZONS
        }
        # Volatility + committee agreement feed the precise, conviction-scaled sizing.
        daily_vol = getattr(snap_now, "realized_vol_krw", None)
        if daily_vol is None:
            dex = (snap_now.series.get("DEXKOUS") if snap_now and getattr(snap_now, "series", None) else None)
            if dex and dex.change is not None:
                daily_vol = abs(dex.change)
        from collections import Counter
        _sigs = [a["signal"] for a in result["agent_results"]]
        agreement = (Counter(_sigs).most_common(1)[0][1] / len(_sigs)) if _sigs else None
        decision = build_trade_signal(spot, pub_aggregates, realized_vol=daily_vol, agreement=agreement)
        # Portfolio-level risk gate BEFORE persisting/executing the signal.
        from backend.risk.risk_manager import apply_risk_limits
        from backend.brokers.factory import get_broker
        broker = get_broker()
        decision = await apply_risk_limits(db, decision, broker, daily_vol_krw=daily_vol)
        sig_row = await crud.save_trade_signal(db, decision.to_db(run.id))
        await _execute_signal(db, decision, sig_row.id, broker)

        # Real-time Telegram push when the published forecast materially changed.
        if changed_h:
            try:
                from backend.briefing.telegram import send_forecast_update
                await send_forecast_update(pub_view, result.get("report_ko", ""), spot, changed_h)
            except Exception as e:
                logger.warning("Forecast Telegram push failed: %s", e)

    logger.info("Cycle done. 1w %+.1f / 1m %+.1f / 3m %+.1f / 12m %+.1f 원 · trade: %s",
                result["horizons"]["1w"]["weighted_delta_krw"],
                result["horizons"]["1m"]["weighted_delta_krw"],
                result["horizons"]["3m"]["weighted_delta_krw"],
                result["horizons"]["12m"]["weighted_delta_krw"],
                decision.side)


async def _execute_signal(db, decision, signal_id: int, broker):
    """Route the decision to the configured broker. Paper fills are recorded for the
    P&L track record; live orders go through KISBroker's hard safety gates."""
    from backend.database import crud
    from backend.data import activity_log as AL

    if decision.side == "FLAT" or decision.notional_usd <= 0:
        AL.trade_event(f"No trade: {decision.rationale}")
        return
    # Avoid stacking duplicate paper exposure (one open per side+horizon).
    if not broker.is_live:
        from backend.signals.position_manager import has_open_position
        if await has_open_position(db, decision.side, decision.horizon):
            AL.trade_event(f"Skip: already holding {decision.side} {decision.horizon} (paper)")
            return
    try:
        order = await broker.place_order(decision.side, decision.notional_usd)
    except NotImplementedError as e:
        AL.trade_event(f"Live trading gated: {e}")
        return
    except Exception as e:
        AL.trade_event(f"Order error: {e}")
        return
    if order.ok and not broker.is_live:
        await crud.open_paper_position(db, {
            "signal_id": signal_id, "side": order.side, "notional_usd": order.filled_qty,
            "entry_rate": order.avg_price, "status": "open",
        })


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    _restore_admin_weights()
    init_scheduler(trigger_cycle, run_data_collection, run_daily_brief,
                   run_daily_report=run_daily_report_guarantee)
    asyncio.create_task(run_data_collection())
    asyncio.create_task(startup_brief())   # ensure today's brief exists after a (re)boot
    asyncio.create_task(_startup_realized())  # backfill realized-rate history for accuracy tracking
    logger.info("KRW-Watcher started. Model: %s · Broker: %s", settings.MODEL_ID, settings.BROKER)
    yield
    try:
        scheduler.shutdown(wait=False)
    except Exception:
        pass


async def _startup_realized():
    """Backfill daily DEXKOUS history so predicted-vs-actual scoring works immediately."""
    import asyncio as _a
    await _a.sleep(8)
    try:
        from backend.accuracy.track import ensure_realized_history
        async with AsyncSessionLocal() as db:
            n = await ensure_realized_history(db)
        logger.info("Realized-rate history ready (%s rows).", n)
    except Exception as e:
        logger.warning("Realized history backfill failed: %s", e)


def _restore_admin_weights():
    try:
        from backend.routes.admin_routes import load_weights
        from backend.agents.orchestrator import apply_weight_override
        for aid, w in (load_weights() or {}).items():
            apply_weight_override(int(aid), float(w))
    except Exception:
        pass


app = FastAPI(title="KRW-Watcher", docs_url="/docs", redoc_url=None,
              lifespan=lifespan, redirect_slashes=False)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list,
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

from backend.auth.middleware import SecurityMiddleware                   # noqa: E402
app.add_middleware(SecurityMiddleware)

from backend.routes.auth_routes import router as auth_router            # noqa: E402
from backend.routes.dashboard_routes import router as dashboard_router  # noqa: E402
from backend.routes.admin_routes import router as admin_router          # noqa: E402
from backend.routes.accuracy_routes import router as accuracy_router    # noqa: E402
from backend.routes.briefing_routes import router as briefing_router    # noqa: E402
app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(admin_router)
app.include_router(accuracy_router)
app.include_router(briefing_router)


_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/")
async def dashboard():
    # no-cache so browsers always fetch the latest dashboard (no stale UI after updates)
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"),
                        headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/health")
async def health():
    from backend.data.collector import get_latest
    from backend.agents.orchestrator import ALL_AGENTS
    snap = get_latest().get("snapshot")
    return {
        "status": "ok",
        "model": settings.MODEL_ID,
        "broker": settings.BROKER,
        "live_trading": settings.ENABLE_LIVE_TRADING,
        "spot": getattr(snap, "spot", None),
        "realized_vol_krw": getattr(snap, "realized_vol_krw", None),
        "data_last_collected": get_latest().get("collected_at"),
        "agent_count": len(ALL_AGENTS),
    }
