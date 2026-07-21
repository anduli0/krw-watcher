"""Agent 03 — Rate Differential & Carry. The interest-rate-parity / carry-trade lens
plus the FX-swap (NDF) basis that actually clears KRW flows."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentRateCarry(BaseAgent):
    agent_id = 3
    agent_name = "Rate_Carry"
    weight = 1.3
    self_consistency_n = 3

    def _system_prompt(self) -> str:
        return (
            "You are an FX rates strategist focused on the carry and cross-currency basis "
            "that drive USD/KRW positioning.\n\n"
            "Framework:\n"
            "- Covered/uncovered interest parity: a positive US−KR rate gap means holding USD pays "
            "  carry, structurally pressuring the won weaker unless offset by expected won appreciation.\n"
            "- Front-end (2Y) gap drives short-horizon carry; long-end (10Y) gap drives strategic flows.\n"
            "- The KRW cross-currency basis / NDF points: a deeply negative basis signals dollar-funding "
            "  stress and forces won weakness regardless of spot rate gaps.\n"
            "- Carry is profitable until it isn't: crowded long-USD carry unwinds violently on risk-off.\n\n"
            "Net positive carry to USD → won WEAK (positive delta_krw), scaled by gap size and stability. "
            "Always output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Quantify the carry/rate-differential impulse on USD/KRW.\n"
            "Steps: (1) front-end & long-end US−KR gaps; (2) direction & stability of the gap; "
            "(3) any funding-stress / basis signal; (4) net carry impulse per horizon (note carry "
            "dominates short horizons, fair-value dominates 12m).\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- RATE DIFFERENTIAL ---\n{ctx.rate_diff_text}\n\n"
            f"--- US RATES ---\n{ctx.us_macro_text}\n\n"
            f"--- KR RATES ---\n{ctx.kr_macro_text}\n{ctx.bok_text}"
        )


agent = AgentRateCarry()
