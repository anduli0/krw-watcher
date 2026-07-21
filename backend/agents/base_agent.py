"""
Base agent for KRW-Watcher.

Each agent emulates one institutional lens on USD/KRW and outputs a *path* of
expected moves across 4 horizons, in won (delta_krw) relative to current spot.

Sign convention (READ THIS):
    delta_krw > 0  → USD/KRW RISES → won WEAKENS → signal "krw_weak"  → go LONG USD/KRW
    delta_krw < 0  → USD/KRW FALLS → won STRENGTHENS → signal "krw_strong" → go SHORT USD/KRW

Mirrors fed-watcher's BaseAgent: cache_control system prompt, optional extended
thinking, optional self-consistency sampling, cross-horizon coherence checks, and
a data-availability confidence ceiling to fight overconfidence on sparse inputs.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import asyncio
import time
import json
import re
import statistics
import anthropic
from backend.config import settings

REASONING_TRUNCATE = 600
HORIZONS = ("1w", "1m", "3m", "12m")

# Max acceptable |Δ(h2) − Δ(h1)| in won. FX vol scales ~√time, so the band widens
# with horizon. A path that swings more than this between adjacent horizons is suspect.
COHERENCE_LIMITS = {
    ("1w", "1m"):  45.0,
    ("1m", "3m"):  70.0,
    ("3m", "12m"): 130.0,
}
COHERENCE_PENALTY_CAP = 0.5

# Confidence ceilings when the agent ran on a thin context (calibrates overconfidence).
DATA_MEDIUM_CONF_CAP = 0.65   # 2 of 4 source groups present
DATA_SPARSE_CONF_CAP = 0.45   # ≤1 present

# Signal deadband (won) — below this the move isn't tradeable → neutral.
SIGNAL_DEADBAND_KRW = 6.0


@dataclass
class AgentContext:
    spot_text: str = ""
    us_macro_text: str = ""
    kr_macro_text: str = ""
    rate_diff_text: str = ""
    global_risk_text: str = ""
    flows_text: str = ""
    news_text: str = ""
    ib_consensus_text: str = ""
    intl_bodies_text: str = ""
    bok_text: str = ""
    monetary_text: str = ""        # 통화량(M2) · 통화모형
    financial_text: str = ""       # 주식·채권 시장 연계
    capital_flows_text: str = ""   # 외국인 주식/채권 자금, NPS, 서학개미
    jobs_text: str = ""            # 미 고용지표 (NFP·실업률·신규청구)
    cb_carry_text: str = ""        # ECB·BOJ 등 글로벌 중앙은행 + 엔캐리 트레이드
    spot: Optional[float] = None
    negative_examples: list[str] = field(default_factory=list)
    material_event: Optional[str] = None
    structural_view: str = ""        # desk's persistent structural theses (house_view.py)
    consensus_summary: Optional[str] = None
    own_round1_output: Optional[str] = None

    def structural_view_block(self) -> str:
        if not self.structural_view:
            return ""
        return (
            "\n\n### DESK STRUCTURAL VIEW (위원회 공통 구조적 전제 — 반드시 반영)\n"
            "느리게 움직이는 구조적 전제다. 해당 호라이즌(주로 3m·12m)에 반영하되 과장하지 말 것.\n"
            f"{self.structural_view}\n"
        )

    def negative_examples_block(self) -> str:
        if not self.negative_examples:
            return ""
        block = "\n\n### NEGATIVE EXAMPLES (past forecast errors — learn from these):\n"
        for ex in self.negative_examples[-10:]:
            block += f"- {ex}\n"
        return block

    def collaboration_block(self) -> str:
        if not self.consensus_summary:
            return ""
        return (
            "\n\n### COLLABORATION REVIEW\n"
            "You analyzed independently. Now you see the committee consensus.\n"
            f"Your Round 1 output: {self.own_round1_output}\n\n"
            f"Peer consensus: {self.consensus_summary}\n\n"
            "Either confirm your estimate OR revise it. Update honestly if peers have "
            "stronger evidence; defend a contrarian thesis only if you can cite specifics.\n"
        )


@dataclass
class HorizonOutput:
    delta_krw: float
    confidence: float
    rationale: str = ""


@dataclass
class AgentResult:
    agent_id: int
    agent_name: str
    signal: str
    delta_krw: float          # 1m delta — headline number
    confidence: float
    reasoning: str
    horizons: dict[str, HorizonOutput] = field(default_factory=dict)
    limited_mode: bool = False
    duration_ms: int = 0
    round: int = 1
    revised: bool = False
    coherent: bool = True

    def horizon_delta(self, h: str) -> float:
        ho = self.horizons.get(h)
        return ho.delta_krw if ho else self.delta_krw

    def horizon_confidence(self, h: str) -> float:
        ho = self.horizons.get(h)
        return ho.confidence if ho else self.confidence


def check_coherence(horizons: dict[str, HorizonOutput]) -> tuple[bool, str]:
    for (h1, h2), limit in COHERENCE_LIMITS.items():
        d1 = horizons.get(h1, HorizonOutput(0, 0)).delta_krw
        d2 = horizons.get(h2, HorizonOutput(0, 0)).delta_krw
        if abs(d2 - d1) > limit:
            return False, f"|{h1}→{h2}| {abs(d2 - d1):.0f}원 > {limit:.0f}원 limit"
    deltas = [horizons.get(h, HorizonOutput(0, 0)).delta_krw for h in HORIZONS]
    sign_flips = sum(
        1 for i in range(len(deltas) - 1)
        if deltas[i] * deltas[i + 1] < 0 and abs(deltas[i]) > 8 and abs(deltas[i + 1]) > 8
    )
    if sign_flips >= 2:
        return False, f"{sign_flips} large-magnitude sign reversals"
    return True, ""


def data_availability_cap(ctx: "AgentContext") -> float:
    score = 0
    if ctx.spot_text and "unavailable" not in ctx.spot_text:
        score += 1
    if ctx.us_macro_text and len(ctx.us_macro_text.strip()) > 80:
        score += 1
    if (ctx.kr_macro_text and len(ctx.kr_macro_text.strip()) > 80) or \
       (ctx.rate_diff_text and "insufficient" not in ctx.rate_diff_text):
        score += 1
    if (ctx.news_text and len(ctx.news_text.strip()) > 80) or \
       (ctx.global_risk_text and len(ctx.global_risk_text.strip()) > 80):
        score += 1
    if score >= 3:
        return 1.0
    if score == 2:
        return DATA_MEDIUM_CONF_CAP
    return DATA_SPARSE_CONF_CAP


class BaseAgent(ABC):
    agent_id: int
    agent_name: str
    weight: float = 1.0

    enable_thinking: bool = False
    thinking_budget: int = 4000
    self_consistency_n: int = 1

    def __init__(self):
        self._client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    @abstractmethod
    def _system_prompt(self) -> str: ...

    @abstractmethod
    def _user_message(self, ctx: AgentContext) -> str: ...

    async def run(self, ctx: AgentContext) -> AgentResult:
        from backend.data import activity_log as AL
        round_num = 2 if ctx.consensus_summary else 1
        AL.agent_start(self.agent_name, round_num)
        t0 = time.time()

        if self.self_consistency_n > 1:
            result = await self._self_consistency_call(ctx)
        else:
            result = await self._call_claude(ctx)

        coherent, reason = check_coherence(result.horizons)
        if not coherent:
            result.coherent = False
            for ho in result.horizons.values():
                ho.confidence = min(ho.confidence, COHERENCE_PENALTY_CAP)
            result.confidence = min(result.confidence, COHERENCE_PENALTY_CAP)
            AL.emit("agent", self.agent_name,
                    f"Incoherent path ({reason}) — confidence capped", "#E5A03E", "warn")

        cap = data_availability_cap(ctx)
        if cap < 1.0:
            for ho in result.horizons.values():
                ho.confidence = min(ho.confidence, cap)
            result.confidence = min(result.confidence, cap)
            result.limited_mode = True

        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    async def _self_consistency_call(self, ctx: AgentContext) -> AgentResult:
        tasks = [self._call_claude(ctx, temperature=0.7) for _ in range(self.self_consistency_n)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        valid = [r for r in results if isinstance(r, AgentResult)]
        if not valid:
            return await self._call_claude(ctx)
        if len(valid) == 1:
            return valid[0]
        merged = {}
        for h in HORIZONS:
            deltas = [r.horizon_delta(h) for r in valid]
            confs = [r.horizon_confidence(h) for r in valid]
            merged[h] = HorizonOutput(
                delta_krw=statistics.median(deltas),
                confidence=statistics.mean(confs),
                rationale=valid[0].horizons.get(h, HorizonOutput(0, 0)).rationale,
            )
        sig_counts = {"krw_weak": 0, "neutral": 0, "krw_strong": 0}
        for r in valid:
            sig_counts[r.signal] = sig_counts.get(r.signal, 0) + 1
        majority = max(sig_counts, key=sig_counts.get)
        one_m = merged["1m"]
        return AgentResult(
            agent_id=self.agent_id, agent_name=self.agent_name, signal=majority,
            delta_krw=one_m.delta_krw, confidence=one_m.confidence,
            reasoning=f"[Self-consistency n={len(valid)}] " + valid[0].reasoning[:400],
            horizons=merged,
        )

    async def _call_claude(self, ctx: AgentContext, temperature: float = 1.0) -> AgentResult:
        user_msg = (
            ctx.structural_view_block()
            + ctx.negative_examples_block()
            + ctx.collaboration_block()
            + "\n\n"
            + self._user_message(ctx)
        )
        kwargs: dict = {
            "model": settings.MODEL_ID,
            "max_tokens": 1500 + (self.thinking_budget if self.enable_thinking else 0),
            "system": [{
                "type": "text",
                "text": self._system_prompt(),
                "cache_control": {"type": "ephemeral"},
            }],
            "messages": [{"role": "user", "content": user_msg}],
        }
        if self.enable_thinking:
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
            kwargs["temperature"] = 1.0
        else:
            kwargs["temperature"] = temperature
        try:
            response = await self._client.messages.create(**kwargs)
        except anthropic.BadRequestError:
            kwargs.pop("thinking", None)
            kwargs["temperature"] = temperature
            response = await self._client.messages.create(**kwargs)

        raw = "".join(b.text for b in response.content if hasattr(b, "text"))
        return self._parse(raw)

    def _parse(self, raw: str) -> AgentResult:
        try:
            # Strip ```json fences the model sometimes adds, then take the outermost {...}.
            cleaned = re.sub(r"```(?:json)?", "", raw)
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                data = json.loads(match.group())
                hz_raw = data.get("horizons", {}) or {}
                horizons = {}
                for h in HORIZONS:
                    hd = hz_raw.get(h, {})
                    horizons[h] = HorizonOutput(
                        delta_krw=float(hd.get("delta_krw", 0)),
                        confidence=max(0.0, min(0.95, float(hd.get("confidence", 0.4)))),
                        rationale=str(hd.get("rationale", ""))[:200],
                    )
                one_m = horizons["1m"]
                return AgentResult(
                    agent_id=self.agent_id, agent_name=self.agent_name,
                    signal=data.get("signal", "neutral"),
                    delta_krw=one_m.delta_krw, confidence=one_m.confidence,
                    reasoning=str(data.get("reasoning", raw[:REASONING_TRUNCATE]))[:REASONING_TRUNCATE],
                    horizons=horizons,
                )
        except Exception:
            pass
        # Fallback heuristic parse
        signal = "neutral"
        low = raw.lower()
        if any(w in low for w in ["krw_weak", "depreciat", "weaker won", "won weak", "long usd"]):
            signal = "krw_weak"
        elif any(w in low for w in ["krw_strong", "appreciat", "stronger won", "won strong", "short usd"]):
            signal = "krw_strong"
        return AgentResult(
            agent_id=self.agent_id, agent_name=self.agent_name, signal=signal,
            delta_krw=0.0, confidence=0.3, reasoning=raw[:REASONING_TRUNCATE],
            horizons={h: HorizonOutput(0.0, 0.3, "parse_failed") for h in HORIZONS},
        )


OUTPUT_SCHEMA = """
Respond ONLY with this JSON — no prose before or after:
{
  "signal": "krw_weak" | "neutral" | "krw_strong",
  "horizons": {
    "1w":  {"delta_krw": <float, won vs current spot>, "confidence": <0.00-0.95>, "rationale": "<one sentence>"},
    "1m":  {"delta_krw": <float>, "confidence": <0.00-0.95>, "rationale": "<one sentence>"},
    "3m":  {"delta_krw": <float>, "confidence": <0.00-0.95>, "rationale": "<one sentence>"},
    "12m": {"delta_krw": <float>, "confidence": <0.00-0.95>, "rationale": "<one sentence>"}
  },
  "reasoning": "<2-3 sentences, dominant thesis citing specific data points>"
}

SIGN CONVENTION (critical — do not invert):
  delta_krw > 0  → USD/KRW RISES, won WEAKENS  → signal "krw_weak"  (you would LONG USD/KRW)
  delta_krw < 0  → USD/KRW FALLS, won STRENGTHENS → signal "krw_strong" (you would SHORT USD/KRW)
  |delta| < 6원 over the horizon → "neutral".

RULES:
1. CROSS-HORIZON COHERENCE (hard): |Δ1w→Δ1m| ≤ 45원, |Δ1m→Δ3m| ≤ 70원, |Δ3m→Δ12m| ≤ 130원.
   No more than one large sign reversal across the path. The 12m anchor should reflect
   fair-value (PPP/REER/BEER) mean-reversion, not just extrapolated momentum.
2. CONFIDENCE CALIBRATION (honest but DECISIVE — confidence is precision-weighted):
   0.80-0.92 = direct fresh data + clear causal chain (cite specifics).
   0.60-0.80 = strong data, minor interpretation uncertainty.
   0.40-0.60 = mixed signals / your lens only weakly informs this horizon.
   0.15-0.40 = sparse data, inferring more than measuring.
   When the evidence clearly leans, COMMIT — don't reflexively hedge to mid-confidence,
   but don't fabricate certainty either.
3. MAGNITUDE & CONVICTION (take a clear stance — neutral-hugging is a real cost):
   USD/KRW realistically moves ~5-15원/week, 15-40원/month in trends, 40-100원+ per quarter
   in a genuine move. When your lens points directionally, commit to a MEANINGFUL delta —
   do NOT default to near-zero/neutral to play safe (reserve that for genuinely balanced
   setups). A muddy, over-hedged committee misses real moves. Only avoid implausible deltas.
4. REASONING: cite specifics ("US10Y−KR10Y widened to +1.4pp + DXY 105→107 → carry favors USD → +12원/1m"),
   not vague ("dollar strength suggests upside"). Lead with your strongest signal.
"""
