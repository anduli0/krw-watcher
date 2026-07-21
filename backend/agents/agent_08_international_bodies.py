"""Agent 08 — International Bodies. The IMF / BIS / OECD lens: official-sector views on
KRW valuation, REER misalignment, and capital-flow vulnerability."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentInternationalBodies(BaseAgent):
    agent_id = 8
    agent_name = "Intl_Bodies"
    weight = 1.0

    def _system_prompt(self) -> str:
        return (
            "You synthesize the official-sector / multilateral view on the won, as the IMF, BIS, and "
            "OECD would frame it. This is a slow-moving, valuation-and-stability anchor — most relevant "
            "at the 3m-12m horizons.\n\n"
            "Framework:\n"
            "- IMF Article IV / External Sector Report: is the won assessed as over- or under-valued vs "
            "  its medium-term fundamentals (REER gap)? Under-valuation argues for eventual appreciation.\n"
            "- BIS: global dollar-funding conditions, cross-border bank flows, FX-swap stress, and "
            "  warnings on carry-trade build-up and financial-stability spillovers.\n"
            "- OECD: relative growth/inflation outlooks and PPP-based fair value.\n"
            "- Capital-flow-at-risk: EM vulnerability to a dollar-liquidity squeeze.\n\n"
            "Frame the medium-term valuation pull (toward fair value) as won deltas. Be measured: "
            "official-sector signals are directional, not tactical. Output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Give the official-sector / multilateral read on USD/KRW.\n"
            "Steps: (1) REER / valuation gap direction; (2) dollar-funding & flow-stability signal; "
            "(3) relative growth-inflation fair value; (4) net valuation pull per horizon (small at 1w, "
            "larger at 12m).\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- INTERNATIONAL-BODY / VALUATION NOTES ---\n{ctx.intl_bodies_text}\n\n"
            f"--- MACRO CONTEXT ---\n{ctx.us_macro_text}\n{ctx.kr_macro_text}\n\n"
            f"--- RELEVANT NEWS ---\n{ctx.news_text[:1200]}"
        )


agent = AgentInternationalBodies()
