"""
Realized-accuracy feedback loop — closes the learning circuit.

When a past forecast's horizon has elapsed, compare each agent's predicted won-delta
against the REALIZED move (current spot − spot at run time). Large misses become:
  • FeedbackEntry rows → feed compute_adaptive_weights() (accurate agents up-weighted).
  • Negative-example strings → injected into future agent prompts so they learn.

Bounded: evaluates at most the oldest unevaluated matured run per horizon per call, so
it catches up gradually without flooding the DB.
"""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database.models import RunLog, AgentOutput, FeedbackEntry

logger = logging.getLogger("krw_watcher.feedback")

HORIZON_DAYS = {"1w": 7, "1m": 30, "3m": 90, "12m": 365}
# A miss larger than this (won) at the given horizon is worth remembering.
NEG_EXAMPLE_KRW = {"1w": 12.0, "1m": 25.0, "3m": 50.0, "12m": 90.0}


async def evaluate_matured_forecasts(db: AsyncSession, current_spot: float | None) -> int:
    if not current_spot:
        return 0
    now = datetime.utcnow()
    written = 0

    for h, days in HORIZON_DAYS.items():
        cutoff = now - timedelta(days=days)
        candidates = (await db.execute(
            select(RunLog)
            .where(RunLog.status == "completed",
                   RunLog.completed_at.isnot(None),
                   RunLog.completed_at <= cutoff,
                   RunLog.spot_at_run.isnot(None))
            .order_by(RunLog.completed_at)        # oldest first
            .limit(20)
        )).scalars().all()

        for run in candidates:
            already = (await db.execute(
                select(FeedbackEntry.id)
                .where(FeedbackEntry.run_id == run.id, FeedbackEntry.horizon == h)
                .limit(1)
            )).scalar_one_or_none()
            if already:
                continue

            realized = current_spot - run.spot_at_run
            outputs = (await db.execute(
                select(AgentOutput).where(AgentOutput.run_id == run.id)
            )).scalars().all()
            if not outputs:
                continue

            run_date = run.completed_at.date().isoformat() if run.completed_at else "?"
            for ao in outputs:
                try:
                    horizons = json.loads(ao.horizons_json) if ao.horizons_json else {}
                except Exception:
                    horizons = {}
                predicted = float(horizons.get(h, {}).get("delta_krw", ao.delta_krw or 0.0))
                divergence = predicted - realized
                neg = None
                if abs(divergence) > NEG_EXAMPLE_KRW[h]:
                    neg = (f"[{run_date} · {h}] {ao.agent_name} predicted {predicted:+.0f}원 "
                           f"but USD/KRW realized {realized:+.0f}원 (off by {divergence:+.0f}원). "
                           f"Its signal was {ao.signal}. Avoid this failure mode.")
                db.add(FeedbackEntry(
                    run_id=run.id, agent_id=ao.agent_id, horizon=h,
                    predicted_delta=predicted, actual_delta=realized,
                    divergence_krw=divergence, negative_example_text=neg,
                ))
                written += 1
            await db.commit()
            logger.info("Evaluated run %s @ %s: realized %+.1f원, %d agents scored",
                        run.id, h, realized, len(outputs))
            break  # one matured run per horizon per call — stays bounded

    return written
