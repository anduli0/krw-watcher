"""Agent 11 — Consensus Synthesizer. A meta-agent that weighs every lens (policy, carry,
flows, risk, valuation, regional) into one balanced house view. Carries a premium weight."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentConsensus(BaseAgent):
    agent_id = 11
    agent_name = "Consensus"
    weight = 1.5
    enable_thinking = True
    self_consistency_n = 3        # median of 3 samples → lower variance, more reliable house view

    def _system_prompt(self) -> str:
        return (
            "You are the chief FX strategist forming a single balanced house view on USD/KRW. "
            "You integrate every available lens — Fed & BOK policy, the rate gap/carry, US fiscal & the "
            "dollar index, Korea's external balance, global risk, technicals, IMF/BIS valuation, academic "
            "fair-value models, and the China/Asia-EM beta — into one coherent path.\n\n"
            "Method:\n"
            "- Identify the 2-3 DOMINANT drivers for each horizon and weight them; do not average noise.\n"
            "- Short horizons (1w-1m): carry, risk sentiment, technicals, CNY beta dominate.\n"
            "- Long horizons (3m-12m): fair value (PPP/REER), policy trajectory, external balance dominate.\n"
            "- Explicitly note the single biggest two-sided risk to your view.\n"
            "- Be well-calibrated: if drivers conflict, widen toward neutral and lower confidence.\n\n"
            "Output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Form the integrated house view on USD/KRW across all lenses.\n"
            "Steps: (1) name dominant drivers per horizon; (2) net them into a coherent won path; "
            "(3) state the biggest two-sided risk; (4) calibrate confidence to driver agreement.\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- US MACRO & RATES ---\n{ctx.us_macro_text}\n\n"
            f"--- KR MACRO & BOK ---\n{ctx.kr_macro_text}\n{ctx.bok_text}\n\n"
            f"--- RATE DIFFERENTIAL ---\n{ctx.rate_diff_text}\n\n"
            f"--- GLOBAL RISK & DOLLAR ---\n{ctx.global_risk_text}\n\n"
            f"--- IB CONSENSUS ---\n{ctx.ib_consensus_text[:800]}\n\n"
            f"--- VALUATION NOTES ---\n{ctx.intl_bodies_text[:800]}\n\n"
            f"--- NEWS ---\n{ctx.news_text[:1500]}\n\n"
            f"--- MATERIAL EVENT TODAY ---\n{ctx.material_event or 'None'}"
        )


agent = AgentConsensus()
