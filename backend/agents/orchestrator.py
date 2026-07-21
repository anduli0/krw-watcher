"""
KRW-Watcher orchestrator.

- 22 agents: 15 specialist lenses + 7 sell-side/securities-house desks.
- 4 horizons per agent (1w, 1m, 3m, 12m), output in won.
- 2-round collaboration: 1m-horizon outliers see the consensus and may revise.
- Bayesian precision-weighted aggregation (quantitative anchor).
- Hierarchical synthesis: 학계 + 전문분석 group orchestrators → 수석(Chief) reconciles → final.
"""
import asyncio
import json
from datetime import datetime
from backend.data import activity_log as AL
from backend.llm import make_client

from backend.agents.base_agent import (
    AgentContext, AgentResult, BaseAgent, HORIZONS, SIGNAL_DEADBAND_KRW,
)
from backend.agents.agent_01_fed_policy import agent as a1
from backend.agents.agent_02_bok_policy import agent as a2
from backend.agents.agent_03_rate_carry import agent as a3
from backend.agents.agent_04_us_fiscal_dollar import agent as a4
from backend.agents.agent_05_korea_external import agent as a5
from backend.agents.agent_06_global_risk import agent as a6
from backend.agents.agent_07_technical_flow import agent as a7
from backend.agents.agent_08_international_bodies import agent as a8
from backend.agents.agent_09_academic_fx import agent as a9
from backend.agents.agent_10_cny_asia_em import agent as a10
from backend.agents.agent_11_consensus import agent as a11
from backend.agents.agent_12_monetary_bop import agent as a12
from backend.agents.agent_13_market_linkage import agent as a13
from backend.agents.agent_14_ecb_global_cb import agent as a14
from backend.agents.agent_15_boj_yen_carry import agent as a15
from backend.agents.agent_16_news_sentiment import agent as a16
from backend.agents.bank_desks import BANK_DESK_AGENTS
from backend.config import settings

SPECIALIST_AGENTS: list[BaseAgent] = [a1, a2, a3, a4, a5, a6, a7, a8, a9, a10, a11, a12, a13, a14, a15, a16]
ALL_AGENTS: list[BaseAgent] = SPECIALIST_AGENTS + BANK_DESK_AGENTS

# ── Hierarchical orchestration: 3 sectors → 3 group orchestrators → Chief ───────
# 학계 (Academic): formal-theory & official-research lenses.
ACADEMIC_IDS = {8, 9, 12}            # Intl_Bodies(IMF/BIS/OECD), Academic_FX(UIP/PPP), Monetary_BoP
# 퍼블릭 (Public sector): central banks, treasury/fiscal, national external accounts.
PUBLIC_IDS = {1, 2, 4, 5, 14, 15}    # Fed, BOK, US_Fiscal_Dollar, Korea_External, ECB, BOJ
# 프라이빗 (Private sector): market/flow lenses + sell-side desks = everyone else.

GROUP_LABELS = {
    "academic": "학계(Academic)",
    "public": "퍼블릭(Public)",
    "private": "프라이빗(Private)",
}


def _group_of(agent_id: int) -> str:
    if agent_id in ACADEMIC_IDS:
        return "academic"
    if agent_id in PUBLIC_IDS:
        return "public"
    return "private"

# Collaboration / aggregation parameters (tuned for won-denominated FX deltas).
OUTLIER_KRW_THRESHOLD = 12.0   # |Δ1m − consensus| > 12원 → Round-2 review
MAX_OUTLIERS_REVIEWED = 5
MIN_CONFIDENCE_FOR_INCLUSION = 0.30
CONFIDENCE_EXPONENT = 2.5
TIGHT_CONSENSUS_STD = 8.0      # won stdev — below → agreement boost
WIDE_DISAGREEMENT_STD = 25.0   # won stdev — above → disagreement penalty
CONSENSUS_BOOST = 1.18
DISAGREEMENT_PENALTY = 0.78
MAX_AGGREGATE_CONF = 0.93

_WEIGHT_OVERRIDES: dict[int, float] = {}
_ADAPTIVE_MULTIPLIERS: dict[int, float] = {}
_AGENT_BY_ID: dict[int, BaseAgent] = {a.agent_id: a for a in ALL_AGENTS}


def apply_weight_override(agent_id: int, weight: float):
    _WEIGHT_OVERRIDES[agent_id] = weight


async def compute_adaptive_weights(db, event: dict | None = None) -> dict[int, float]:
    """Accuracy-based + event-based weight multipliers, capped to [0.5×, 2.0×]."""
    from sqlalchemy import select, func
    from backend.database.models import FeedbackEntry
    multipliers: dict[int, float] = {}
    try:
        result = await db.execute(
            select(FeedbackEntry.agent_id,
                   func.avg(func.abs(FeedbackEntry.divergence_krw)).label("avg_div"),
                   func.count(FeedbackEntry.id).label("n"))
            .group_by(FeedbackEntry.agent_id)
            .having(func.count(FeedbackEntry.id) >= 3)
        )
        rows = result.all()
        if rows:
            divs = sorted(row.avg_div for row in rows)
            median_div = divs[len(divs) // 2]
            for row in rows:
                if median_div and row.avg_div < median_div * 0.6:
                    multipliers[row.agent_id] = 1.35
                elif median_div and row.avg_div > median_div * 1.6:
                    multipliers[row.agent_id] = 0.65
            if multipliers:
                AL.system_event(f"Adaptive weights: {len(multipliers)} agents adjusted from accuracy history")
    except Exception:
        pass

    multipliers = {k: max(0.5, min(2.0, v)) for k, v in multipliers.items()}

    if event:
        label = event.get("label", "")
        if "FOMC" in label:
            for aid in [1, 3, 4, 12]:    # Fed, Carry, Fiscal/Dollar, Monetary/BoP
                multipliers[aid] = max(multipliers.get(aid, 1.0), 1.4)
        elif "BOK" in label:
            for aid in [2, 3, 5, 12]:    # BOK, Carry, Korea External, Monetary/BoP
                multipliers[aid] = max(multipliers.get(aid, 1.0), 1.4)
        elif "US_CPI" in label or "CPI" in label:
            for aid in [1, 9, 12]:       # Fed, Academic, Monetary/BoP
                multipliers[aid] = max(multipliers.get(aid, 1.0), 1.35)
        elif "RISK" in label:
            for aid in [6, 10, 13, 15, 16]:  # Global Risk, CNY/EM, Market Linkage, BOJ/Carry, News
                multipliers[aid] = max(multipliers.get(aid, 1.0), 1.4)
        elif "ECB" in label:
            for aid in [14, 4]:          # ECB/Global CB, US Fiscal/Dollar
                multipliers[aid] = max(multipliers.get(aid, 1.0), 1.4)
        elif "BOJ" in label or "JPY" in label:
            for aid in [15, 10]:         # BOJ/Yen Carry, CNY/Asia EM
                multipliers[aid] = max(multipliers.get(aid, 1.0), 1.4)
        elif "NFP" in label or "JOBS" in label:
            for aid in [1, 14]:          # Fed (jobs → rate path), ECB divergence
                multipliers[aid] = max(multipliers.get(aid, 1.0), 1.4)
        # News reacts to every scheduled catalyst → always nudge the news lens on event days.
        multipliers[16] = max(multipliers.get(16, 1.0), 1.25)

    global _ADAPTIVE_MULTIPLIERS
    _ADAPTIVE_MULTIPLIERS = multipliers
    return multipliers


def _effective_weight(result: AgentResult, base_weight: float) -> float:
    w = _WEIGHT_OVERRIDES.get(result.agent_id, base_weight)
    w *= _ADAPTIVE_MULTIPLIERS.get(result.agent_id, 1.0)
    if result.limited_mode:
        w *= 0.4
    if result.agent_id == 11:  # Consensus premium
        w *= 1.4
    return max(0.1, w)


def _signal_for(delta: float) -> str:
    if delta >= SIGNAL_DEADBAND_KRW:
        return "krw_weak"
    if delta <= -SIGNAL_DEADBAND_KRW:
        return "krw_strong"
    return "neutral"


def _aggregate_horizon(valid: list[AgentResult], horizon: str) -> tuple[float, float]:
    total_w = 0.0
    weighted_delta = 0.0
    weighted_conf = 0.0
    included_deltas: list[float] = []
    included_weights: list[float] = []
    for r in valid:
        h_conf = r.horizon_confidence(horizon)
        if h_conf < MIN_CONFIDENCE_FOR_INCLUSION:
            continue
        agent_obj = _AGENT_BY_ID.get(r.agent_id)
        base_w = agent_obj.weight if agent_obj else 1.0
        w = _effective_weight(r, base_w) * (h_conf ** CONFIDENCE_EXPONENT)
        delta = r.horizon_delta(horizon)
        weighted_delta += delta * w
        weighted_conf += h_conf * w
        total_w += w
        included_deltas.append(delta)
        included_weights.append(w)
    if total_w == 0:
        return 0.0, 0.0
    weighted_delta /= total_w
    weighted_conf /= total_w
    if len(included_deltas) >= 4:
        var = sum(wi * (di - weighted_delta) ** 2
                  for di, wi in zip(included_deltas, included_weights)) / total_w
        std = var ** 0.5
        if std < TIGHT_CONSENSUS_STD:
            weighted_conf *= CONSENSUS_BOOST
        elif std > WIDE_DISAGREEMENT_STD:
            weighted_conf *= DISAGREEMENT_PENALTY
    return weighted_delta, min(MAX_AGGREGATE_CONF, weighted_conf)


def _identify_outliers(valid: list[AgentResult], consensus_1m: float) -> list[AgentResult]:
    scored = [(abs(r.horizon_delta("1m") - consensus_1m), r) for r in valid]
    scored = [t for t in scored if t[0] > OUTLIER_KRW_THRESHOLD]
    scored.sort(key=lambda t: -t[0])
    return [r for _, r in scored[:MAX_OUTLIERS_REVIEWED]]


async def run_full_cycle(ctx: AgentContext, cycle_type: str = "scheduled") -> dict:
    AL.orchestrator_event(f"Cycle start: {len(ALL_AGENTS)} agents dispatched (Round 1)")
    raw_results = await asyncio.gather(*[a.run(ctx) for a in ALL_AGENTS], return_exceptions=True)

    valid: list[AgentResult] = []
    errors: list[dict] = []
    for agent, r in zip(ALL_AGENTS, raw_results):
        if isinstance(r, Exception):
            errors.append({"agent": agent.agent_name, "error": str(r)[:200]})
            AL.collect_failed(agent.agent_name, str(r)[:60])
        else:
            valid.append(r)
            AL.agent_done(r.agent_name, r.signal, r.delta_krw, r.confidence)

    consensus_1m, _ = _aggregate_horizon(valid, "1m")

    # ── Round 2: outliers review consensus ──
    outliers = _identify_outliers(valid, consensus_1m)
    revisions: dict[int, AgentResult] = {}
    if outliers:
        import statistics as _st
        AL.orchestrator_event(
            f"Round 2: {len(outliers)} outliers reviewing consensus — "
            f"{', '.join(o.agent_name for o in outliers)}")
        sig_dist = {"krw_weak": 0, "neutral": 0, "krw_strong": 0}
        for r in valid:
            sig_dist[r.signal] = sig_dist.get(r.signal, 0) + 1
        anchors = sorted(valid, key=lambda r: abs(r.horizon_delta("1m") - consensus_1m))[:3]
        all_1m = [r.horizon_delta("1m") for r in valid]
        median_1m, lo, hi = _st.median(all_1m), min(all_1m), max(all_1m)

        async def review(orig: AgentResult) -> AgentResult:
            agent_obj = _AGENT_BY_ID.get(orig.agent_id)
            if agent_obj is None:
                return orig
            err = orig.horizon_delta("1m") - consensus_1m
            direction = "MORE KRW-WEAK (higher USD/KRW)" if err > 0 else "MORE KRW-STRONG (lower USD/KRW)"
            anchor_txt = " | ".join(
                f"[{a.agent_name} {a.horizon_delta('1m'):+.0f}원]: {a.reasoning[:110]}" for a in anchors)
            summary = (
                f"COMMITTEE CONSENSUS (1m): {consensus_1m:+.1f}원 "
                f"[median {median_1m:+.0f}, range {lo:+.0f}..{hi:+.0f}]. "
                f"Distribution: krw_weak={sig_dist['krw_weak']}, neutral={sig_dist['neutral']}, "
                f"krw_strong={sig_dist['krw_strong']} (of {len(valid)}).\n"
                f"YOUR R1 IS {abs(err):.0f}원 {direction} than consensus. Reconsider:\n"
                f"  1. Over-weighting one driver vs peers?\n"
                f"  2. Assuming a regime shift others don't see?\n"
                f"  3. Anchoring on an event peers already discounted?\n\n"
                f"3 AGENTS CLOSEST TO CONSENSUS: {anchor_txt}\n\n"
                f"Either defend your contrarian view with stronger evidence, or revise toward consensus. "
                f"Updating on new information is rewarded, not penalized.")
            rctx = AgentContext(**{**ctx.__dict__,
                                   "consensus_summary": summary,
                                   "own_round1_output": (
                                       f"signal={orig.signal}, 1m={orig.delta_krw:+.0f}원, "
                                       f"conf={orig.confidence:.2f}, reasoning={orig.reasoning[:200]}")})
            try:
                rev = await agent_obj.run(rctx)
                rev.round = 2
                rev.revised = abs(rev.delta_krw - orig.delta_krw) > 3
                return rev
            except Exception:
                return orig

        for rev in await asyncio.gather(*[review(o) for o in outliers]):
            revisions[rev.agent_id] = rev
            AL.agent_done(rev.agent_name, rev.signal, rev.delta_krw, rev.confidence, revised=rev.revised)

    final_results = [revisions.get(r.agent_id, r) for r in valid]

    # ── Quantitative anchor: precision-weighted math aggregate (a sanity input) ──
    math_aggregates: dict[str, dict] = {}
    for h in HORIZONS:
        delta, conf = _aggregate_horizon(final_results, h)
        math_aggregates[h] = {"weighted_delta_krw": round(delta, 2),
                              "confidence": round(conf, 3), "signal": _signal_for(delta)}

    # ── Tier 2: three sector orchestrators debate & synthesize ──
    groups = {"academic": [], "public": [], "private": []}
    for r in final_results:
        groups[_group_of(r.agent_id)].append(r)
    for key in ("academic", "public", "private"):
        AL.orchestrator_event(
            f"{GROUP_LABELS[key]} 오케스트레이터: {len(groups[key])}개 에이전트 의견 토론·종합 중…")
    academic_view, public_view, private_view = await asyncio.gather(
        _group_orchestrate(GROUP_LABELS["academic"], groups["academic"], math_aggregates, ctx),
        _group_orchestrate(GROUP_LABELS["public"], groups["public"], math_aggregates, ctx),
        _group_orchestrate(GROUP_LABELS["private"], groups["private"], math_aggregates, ctx),
    )

    # ── Tier 3: Chief reconciles all three sector views into ONE final conclusion ──
    AL.orchestrator_event("수석 오케스트레이터: 학계·퍼블릭·프라이빗 협의 → 최종 결론 도출…")
    chief = await _chief_orchestrate(
        {"academic": academic_view, "public": public_view, "private": private_view},
        math_aggregates, ctx)

    horizon_aggregates: dict[str, dict] = {}
    for h in HORIZONS:
        ch = (chief.get("horizons") or {}).get(h) or {}
        if ch.get("delta_krw") is not None:
            d, c = float(ch["delta_krw"]), float(ch.get("confidence", math_aggregates[h]["confidence"]))
        else:
            d, c = math_aggregates[h]["weighted_delta_krw"], math_aggregates[h]["confidence"]
        c = max(0.0, min(MAX_AGGREGATE_CONF, c))
        horizon_aggregates[h] = {"weighted_delta_krw": round(d, 2), "confidence": round(c, 3),
                                 "signal": _signal_for(d)}
        AL.orchestrator_event(f"{h} → {horizon_aggregates[h]['signal'].upper()} {d:+.1f}원 (conf {c:.0%})")

    # ── Inter-sector corroboration → confidence (raises reliability only when justified) ──
    for h in HORIZONS:
        ds = []
        for v in (academic_view, public_view, private_view):
            d = ((v.get("horizons") or {}).get(h) or {}).get("delta_krw")
            if d is not None:
                ds.append(d)
        signs = {(1 if d > 1 else -1 if d < -1 else 0) for d in ds}
        signs.discard(0)
        agg = horizon_aggregates[h]
        if len(ds) >= 2 and len(signs) == 1:        # all sectors point the same way → corroborated
            agg["confidence"] = round(min(MAX_AGGREGATE_CONF, agg["confidence"] * 1.10), 3)
            agg["sector_agreement"] = "aligned"
        elif len(signs) > 1:                         # sectors disagree on direction → less reliable
            agg["confidence"] = round(agg["confidence"] * 0.90, 3)
            agg["sector_agreement"] = "split"
        else:
            agg["sector_agreement"] = "mixed"

    report_ko = chief.get("report_ko") or await _derivation_report(
        final_results, horizon_aggregates, outliers, revisions, errors, ctx, "ko")
    report_en = chief.get("report_en") or ""

    # ── Capture the deliberation trail so the dashboard can show HOW the forecast was reached ──
    # Round-2 coordination: each outlier's pre/post-consensus revision.
    collab_reviews = []
    for o in outliers:
        rev = revisions.get(o.agent_id)
        if not rev:
            continue
        collab_reviews.append({
            "agent": o.agent_name, "group": _group_of(o.agent_id),
            "before_delta": round(o.delta_krw, 1), "after_delta": round(rev.delta_krw, 1),
            "before_signal": o.signal, "after_signal": rev.signal, "revised": bool(rev.revised),
        })
    # Each group's member agents + their final stance (the opinions that were exchanged).
    members: dict[str, list] = {"academic": [], "public": [], "private": []}
    for r in final_results:
        members[_group_of(r.agent_id)].append({
            "name": r.agent_name, "signal": r.signal,
            "delta_1m": round(r.horizon_delta("1m"), 1), "conf": round(r.confidence, 2),
            "round": r.round, "revised": bool(r.revised),
        })
    sector_agreement = {h: horizon_aggregates[h].get("sector_agreement") for h in HORIZONS}

    return {
        "cycle_type": cycle_type,
        "timestamp": datetime.utcnow().isoformat(),
        "spot": ctx.spot,
        "horizons": horizon_aggregates,
        "math_aggregates": math_aggregates,
        "report_ko": report_ko,
        "report_en": report_en,
        "hierarchy": {
            "academic": academic_view,
            "public": public_view,
            "private": private_view,
            "chief": {"reconciliation": chief.get("reconciliation", ""),
                      "horizons": chief.get("horizons", {})},
            "members": members,
            "collaboration": {
                "consensus_1m": round(consensus_1m, 1),
                "outlier_count": len(outliers),
                "revised_count": len([r for r in revisions.values() if r.revised]),
                "reviews": collab_reviews,
            },
            "sector_agreement": sector_agreement,
            "spot": ctx.spot,
        },
        "agent_results": [
            {
                "agent_id": r.agent_id,
                "agent_name": r.agent_name,
                "group": _group_of(r.agent_id),
                "signal": r.signal,
                "delta_krw": r.delta_krw,
                "confidence": r.confidence,
                "horizons": {h: {"delta_krw": r.horizon_delta(h),
                                 "confidence": r.horizon_confidence(h),
                                 "rationale": (r.horizons[h].rationale if h in r.horizons else "")}
                             for h in HORIZONS},
                "weight_applied": _effective_weight(
                    r, (_AGENT_BY_ID[r.agent_id].weight if r.agent_id in _AGENT_BY_ID else 1.0)),
                "limited_mode": r.limited_mode,
                "duration_ms": r.duration_ms,
                "round": r.round,
                "revised": r.revised,
                "reasoning": r.reasoning,
            }
            for r in final_results
        ],
        "errors": errors,
        "collaboration": {
            "outliers_reviewed": [r.agent_name for r in outliers],
            "agents_revised": [r.agent_name for r in revisions.values() if r.revised],
        },
    }


GROUP_SCHEMA = """Respond ONLY with JSON:
{
  "horizons": {
    "1w":  {"delta_krw": <float>, "confidence": <0-0.95>},
    "1m":  {"delta_krw": <float>, "confidence": <0-0.95>},
    "3m":  {"delta_krw": <float>, "confidence": <0-0.95>},
    "12m": {"delta_krw": <float>, "confidence": <0-0.95>}
  },
  "synthesis": "<2-3 문장: 이 그룹의 통합 견해와 그 근거>",
  "key_debate": "<그룹 내 가장 큰 이견과 어떻게 정리했는지 1문장>"
}
delta_krw: +면 USD/KRW 상승(원화 약세). 코히런스: |1w→1m|≤45, |1m→3m|≤70, |3m→12m|≤130원."""

CHIEF_SCHEMA = """Respond ONLY with JSON:
{
  "horizons": {
    "1w":  {"delta_krw": <float>, "confidence": <0-0.93>},
    "1m":  {"delta_krw": <float>, "confidence": <0-0.93>},
    "3m":  {"delta_krw": <float>, "confidence": <0-0.93>},
    "12m": {"delta_krw": <float>, "confidence": <0-0.93>}
  },
  "reconciliation": "<학계 vs 전문분석 견해를 어떻게 가중·조정했는지 2-3문장>",
  "report_ko": "<최종 분석 리포트, 한국어 마크다운 700자 이내: 요약(4 horizon)·핵심동인·학계vs실무 이견·트레이딩 함의·리스크>",
  "report_en": "<final report, English markdown under 700 chars>"
}
delta_krw: +면 원화 약세. 코히런스 준수."""


def _parse_json(raw: str) -> dict:
    import re
    raw = re.sub(r"```(?:json)?", "", raw or "")
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group())
    except Exception:
        return {}


async def _group_orchestrate(group_label: str, results: list[AgentResult],
                             math_aggregates: dict, ctx: AgentContext) -> dict:
    """A sub-orchestrator debates its group's agents and reconciles one group view."""
    if not results:
        return {"group": group_label, "horizons": {}, "synthesis": "(에이전트 없음)", "agents": []}
    rows = "\n".join(
        f"- {r.agent_name} [{r.signal}]: 1w {r.horizon_delta('1w'):+.0f} / 1m {r.horizon_delta('1m'):+.0f} / "
        f"3m {r.horizon_delta('3m'):+.0f} / 12m {r.horizon_delta('12m'):+.0f} (conf {r.confidence:.0%}) — "
        f"{r.reasoning[:160]}" for r in results)
    anchor = " · ".join(f"{h} {math_aggregates[h]['weighted_delta_krw']:+.0f}원" for h in HORIZONS)
    if "학계" in group_label:
        role = "이론·공식모형·공신력 연구(UIP/PPP/통화모형/IMF·BIS·OECD) 관점"
    elif "퍼블릭" in group_label:
        role = "중앙은행·재무/정부·국가대외 등 공공·정책 경로(연준/BOK/ECB/BOJ/재정) 관점"
    else:
        role = "시장 수급·모멘텀·캐리·셀사이드 실무(트레이딩 데스크) 관점"
    prompt = (
        f"당신은 USD/KRW 위원회의 '{group_label}' 그룹 오케스트레이터입니다 ({role}). "
        f"아래는 당신 그룹 소속 에이전트들의 호라이즌별 예측(Δ원, +면 원화 약세)입니다. "
        f"이들을 토론·검토하여(동의/이견 식별, 근거가 탄탄한 견해에 가중) 그룹의 통합 견해 하나로 종합하세요.\n\n"
        f"[그룹 에이전트]\n{rows}\n\n[전체 위원회 정량 앵커]\n{anchor}\n\n{GROUP_SCHEMA}")
    client = make_client()
    data = {}
    try:
        resp = await client.messages.create(model=settings.MODEL_ID, max_tokens=1400,
                                            messages=[{"role": "user", "content": prompt}])
        data = _parse_json("".join(b.text for b in resp.content if hasattr(b, "text")))
    except Exception as e:
        AL.emit("orchestrator", group_label, f"종합 실패: {str(e)[:60]}", "#E5A03E", "warn")
    # Fallback to math anchor restricted to this group if LLM failed.
    if not data.get("horizons"):
        gh = {h: {"delta_krw": round(_aggregate_horizon(results, h)[0], 2),
                  "confidence": round(_aggregate_horizon(results, h)[1], 3)} for h in HORIZONS}
        data = {"horizons": gh, "synthesis": "(정량 집계 대체)", "key_debate": ""}
    data["group"] = group_label
    data["agents"] = [r.agent_name for r in results]
    return data


async def _chief_orchestrate(views: dict, math_aggregates: dict, ctx: AgentContext) -> dict:
    """The chief consults all three sector orchestrators and derives the single final conclusion."""
    def hz_line(v):
        h = (v or {}).get("horizons", {})
        return " · ".join(f"{k} {((h.get(k) or {}).get('delta_krw', 0)):+.0f}원"
                          f"(c{round(((h.get(k) or {}).get('confidence', 0))*100)})" for k in HORIZONS)
    anchor = " · ".join(f"{h} {math_aggregates[h]['weighted_delta_krw']:+.0f}원" for h in HORIZONS)
    a, pub, pri = views.get("academic", {}), views.get("public", {}), views.get("private", {})
    sv = (ctx.structural_view or "").strip()
    sv_block = (f"\n[데스크 구조적 전제 — 반드시 반영, 단 주로 3m·12m에]\n{sv}\n\n") if sv else ""
    prompt = (
        "당신은 USD/KRW 위원회의 최종 총괄 수석 오케스트레이터입니다. 세 섹터 하위 오케스트레이터가 각자 "
        "그룹 견해를 종합해 보고했습니다. 세 견해를 협의·조정하여 단 하나의 최종 결론을 도출하세요.\n\n"
        + sv_block +
        f"[학계(Academic) — 이론·공식모형·공신력 연구] {hz_line(a)}\n"
        f"  종합: {a.get('synthesis','')}\n  이견정리: {a.get('key_debate','')}\n\n"
        f"[퍼블릭(Public) — 중앙은행·재무/정부·국가대외] {hz_line(pub)}\n"
        f"  종합: {pub.get('synthesis','')}\n  이견정리: {pub.get('key_debate','')}\n\n"
        f"[프라이빗(Private) — 시장·수급·셀사이드] {hz_line(pri)}\n"
        f"  종합: {pri.get('synthesis','')}\n  이견정리: {pri.get('key_debate','')}\n\n"
        f"[정량 앵커] {anchor}\n현재 스팟: {ctx.spot}원\n\n"
        "학계는 적정가치·평균회귀(장기), 퍼블릭은 정책 경로·개입(정책 분기), 프라이빗은 수급·모멘텀·캐리"
        "(단기)에 강합니다. 세 섹터의 긴장을 어떻게 가중·조정했는지 명시하고(예: 호라이즌별 가중), "
        "호라이즌별 최종 Δ와 신뢰도, 그리고 최종 분석 리포트(한/영)를 작성하세요.\n\n"
        f"{CHIEF_SCHEMA}")
    client = make_client()
    try:
        resp = await client.messages.create(model=settings.MODEL_ID, max_tokens=2600,
                                            messages=[{"role": "user", "content": prompt}])
        data = _parse_json("".join(b.text for b in resp.content if hasattr(b, "text")))
        if data.get("horizons"):
            return data
    except Exception as e:
        AL.emit("orchestrator", "수석", f"최종 종합 실패: {str(e)[:60]}", "#E5A03E", "warn")
    return {"horizons": {}, "reconciliation": "(수석 종합 실패 — 정량 앵커 사용)"}


async def _derivation_report(results, horizon_aggregates, outliers, revisions, errors, ctx, lang):
    rows = []
    for r in sorted(results, key=lambda x: x.agent_id):
        row = (f"| {r.agent_name} | {r.signal.upper()} | "
               f"{r.horizon_delta('1w'):+.0f} | {r.horizon_delta('1m'):+.0f} | "
               f"{r.horizon_delta('3m'):+.0f} | {r.horizon_delta('12m'):+.0f} | {r.confidence:.2f} |")
        if r.revised:
            row += " *(revised)*"
        rows.append(row)
    agent_table = "\n".join(rows)
    horizon_rows = "\n".join(
        f"| {h} | {horizon_aggregates[h]['weighted_delta_krw']:+.1f}원 | "
        f"{horizon_aggregates[h]['signal'].upper()} | {horizon_aggregates[h]['confidence']:.2f} |"
        for h in HORIZONS)
    outlier_text = ", ".join(o.agent_name for o in outliers) if outliers else "None — broad consensus"
    revised_text = ", ".join(r.agent_name for r in revisions.values() if r.revised) or "None"

    if lang == "ko":
        instruction = (
            "Write the derivation report in KOREAN (한국어), under 800 characters:\n"
            "1. **요약**: 4개 horizon(1주/1개월/3개월/1년) 한 줄씩, 원화 방향과 예상 폭(원)\n"
            "2. **핵심 동인**: 어떤 드라이버(금리차/달러/위안/리스크/수급)가 신호를 이끌었는지 2-3개\n"
            "3. **주요 이견**: 어느 데스크/에이전트가 반대 방향이었고 왜\n"
            "4. **트레이딩 함의**: USD/KRW 롱/숏/관망 및 무효화 시나리오 1-2개")
    else:
        instruction = (
            "Write the derivation report in ENGLISH, under 800 characters:\n"
            "1. **Summary**: one line per horizon (1w/1m/3m/12m) with won direction & magnitude\n"
            "2. **Key drivers**: which 2-3 drivers led the signal\n"
            "3. **Key divergences**: which desks/agents differed and why\n"
            "4. **Trading implication**: long/short/flat USD/KRW + 1-2 invalidation scenarios")

    prompt = f"""You are the Chief FX Strategist. Generate a concise markdown derivation report for USD/KRW.

Current spot: {ctx.spot if ctx.spot else 'n/a'}원

## Aggregated Horizon Outputs (Δ in won vs spot; + = won weaker / USD higher)
| Horizon | Δ (won) | Signal | Confidence |
|---------|---------|--------|-----------|
{horizon_rows}

## Agent Outputs (Δ won)
| Agent | Signal | 1W | 1M | 3M | 12M | Conf |
|-------|--------|----|----|----|-----|------|
{agent_table}

## Collaboration
- Outliers reviewed: {outlier_text}
- Agents that revised: {revised_text}
- Failed agents: {len(errors)}

## Material event today
{ctx.material_event or "None"}

{instruction}

Output ONLY the markdown report, no preamble."""
    client = make_client()
    try:
        resp = await client.messages.create(
            model=settings.MODEL_ID, max_tokens=1200,
            messages=[{"role": "user", "content": prompt}])
        return resp.content[0].text
    except Exception as e:
        return f"Report generation failed: {e}"
