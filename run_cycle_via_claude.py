"""
One-shot KRW-Watcher cycle driven by THIS Claude Code session (no console credits).

The forecast intelligence (23-lens committee → 3 sectors → chief) is produced by a
Claude Code Workflow and handed to this script as cycle_result.json. We then run the
worker's REAL pipeline `trigger_cycle("forced")` verbatim, but monkeypatch out the two
functions that would call the (credit-exhausted) Anthropic API:

  • orchestrator.run_full_cycle  → returns our pre-built result dict
  • change_justifier.justify_change → returns None (skip the dead LLM call)

Everything else (data sweep, feedback eval, adaptive weights, bias-correction,
stabilizer, horizon persistence, trade signal, paper exec, Telegram push) is the
worker's own deterministic code, so the DB + dashboard update exactly as a normal cycle.

Run from the krw-watcher root with the venv python:
    .venv\\Scripts\\python.exe run_cycle_via_claude.py
"""
import asyncio
import json
import os
from datetime import datetime

RESULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cycle_result.json")


def _load_committee() -> dict:
    with open(RESULT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _build_result(ctx, cycle_type: str, committee: dict) -> dict:
    """Shape the committee output into the exact dict run_full_cycle returns."""
    from backend.agents import orchestrator as orch

    spot = ctx.spot
    HORIZONS = ("1w", "1m", "3m", "12m")

    def group_of(aid: int) -> str:
        return orch._group_of(aid)

    # ── Per-agent results with REAL effective weights (adaptive multipliers already set) ──
    class _Stub:
        def __init__(self, aid, limited):
            self.agent_id = aid
            self.limited_mode = limited

    agent_results = []
    for a in committee["agents"]:
        aid = int(a["agent_id"])
        base_w = orch._AGENT_BY_ID[aid].weight if aid in orch._AGENT_BY_ID else 1.0
        w_applied = orch._effective_weight(_Stub(aid, False), base_w)
        hz = {h: {"delta_krw": float(a["horizons"][h]["delta_krw"]),
                  "confidence": float(a["horizons"][h]["confidence"]),
                  "rationale": str(a["horizons"][h].get("rationale", ""))[:200]}
              for h in HORIZONS}
        agent_results.append({
            "agent_id": aid,
            "agent_name": a["agent_name"],
            "group": group_of(aid),
            "signal": a["signal"],
            "delta_krw": float(a["horizons"]["1m"]["delta_krw"]),
            "confidence": float(a["horizons"]["1m"]["confidence"]),
            "horizons": hz,
            "weight_applied": round(w_applied, 3),
            "limited_mode": False,
            "duration_ms": 0,
            "round": 1,
            "revised": False,
            "reasoning": str(a.get("reasoning", ""))[:600],
        })

    # ── Final horizon aggregates (chief) ──
    horizons = {}
    for h in HORIZONS:
        agg = committee["horizons"][h]
        horizons[h] = {
            "weighted_delta_krw": round(float(agg["weighted_delta_krw"]), 2),
            "confidence": round(float(agg["confidence"]), 3),
            "signal": agg.get("signal", "neutral"),
        }

    # ── Hierarchy for the dashboard ──
    sectors = committee.get("sectors", {})
    members = {"academic": [], "public": [], "private": []}
    for a in agent_results:
        members[a["group"]].append({
            "name": a["agent_name"], "signal": a["signal"],
            "delta_1m": round(a["horizons"]["1m"]["delta_krw"], 1),
            "conf": round(a["confidence"], 2), "round": 1, "revised": False,
        })

    def sector_view(key):
        v = sectors.get(key, {}) or {}
        return {
            "group": v.get("label", key),
            "horizons": v.get("horizons", {}),
            "synthesis": v.get("synthesis", ""),
            "key_debate": v.get("key_debate", ""),
            "agents": [m["name"] for m in members.get(key, [])],
        }

    # Inter-sector agreement label per horizon.
    sector_agreement = {}
    for h in HORIZONS:
        ds = []
        for key in ("academic", "public", "private"):
            d = ((sectors.get(key, {}).get("horizons", {}) or {}).get(h) or {}).get("delta_krw")
            if d is not None:
                ds.append(d)
        signs = {(1 if d > 1 else -1 if d < -1 else 0) for d in ds}
        signs.discard(0)
        sector_agreement[h] = ("aligned" if len(ds) >= 2 and len(signs) == 1
                               else "split" if len(signs) > 1 else "mixed")

    chief = committee.get("chief", {})
    hierarchy = {
        "academic": sector_view("academic"),
        "public": sector_view("public"),
        "private": sector_view("private"),
        "chief": {"reconciliation": chief.get("reconciliation", ""),
                  "horizons": chief.get("horizons", {})},
        "members": members,
        "collaboration": {"consensus_1m": round(horizons["1m"]["weighted_delta_krw"], 1),
                          "outlier_count": 0, "revised_count": 0, "reviews": []},
        "sector_agreement": sector_agreement,
        "spot": spot,
    }

    return {
        "cycle_type": cycle_type,
        "timestamp": datetime.utcnow().isoformat(),
        "spot": spot,
        "horizons": horizons,
        "math_aggregates": horizons,
        "report_ko": committee.get("report_ko", ""),
        "report_en": committee.get("report_en", ""),
        "hierarchy": hierarchy,
        "agent_results": agent_results,
        "errors": [],
        "collaboration": {"outliers_reviewed": [], "agents_revised": []},
        "source": "claude-code-session",
    }


async def main():
    committee = _load_committee()
    print(f"[load] committee: {len(committee['agents'])} agents · spot~{committee.get('spot')}")

    # Patch the two LLM entrypoints BEFORE importing/calling trigger_cycle.
    from backend.agents import orchestrator as orch
    from backend.stabilizer import change_justifier

    async def _patched_run_full_cycle(ctx, cycle_type="scheduled"):
        print(f"[patch] run_full_cycle intercepted → using session committee (spot {ctx.spot})")
        return _build_result(ctx, cycle_type, committee)

    async def _patched_justify(*args, **kwargs):
        return None

    orch.run_full_cycle = _patched_run_full_cycle
    change_justifier.justify_change = _patched_justify

    # Import trigger_cycle AFTER patching the orchestrator module attribute. trigger_cycle
    # does `from backend.agents.orchestrator import run_full_cycle` at call time, so it
    # resolves our patched attribute.
    from backend.main import trigger_cycle

    print("[run] trigger_cycle('forced') …")
    await trigger_cycle("forced")
    print("[done] cycle complete.")

    # Verify what landed.
    from backend.database.init_db import AsyncSessionLocal
    from backend.database import crud
    from backend.database.models import HORIZONS
    async with AsyncSessionLocal() as db:
        for h in HORIZONS:
            f = await crud.get_latest_horizon_forecast(db, h)
            if f:
                print(f"  {h:>3}: pub Δ{f.published_delta:+.1f}원 → {f.implied_rate} "
                      f"({f.signal}, conf {f.confidence:.2f})")


if __name__ == "__main__":
    asyncio.run(main())
