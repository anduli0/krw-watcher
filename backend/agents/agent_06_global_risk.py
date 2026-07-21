"""Agent 06 — Global Risk & Dollar Smile. The risk-sentiment lens: the won is a
high-beta, highly liquid risk proxy that sells off hard in global risk-off episodes."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentGlobalRisk(BaseAgent):
    agent_id = 6
    agent_name = "Global_Risk"
    weight = 1.2

    def _system_prompt(self) -> str:
        return (
            "You are a global cross-asset risk strategist. You assess how risk sentiment moves "
            "USD/KRW. The won is among the most liquid Asian risk proxies, so it is a high-beta "
            "expression of global risk appetite.\n\n"
            "Framework:\n"
            "- VIX / equity vol regime: spikes → risk-off → won SELLS OFF sharply (USD/KRW up).\n"
            "- The 'dollar smile': USD strengthens both in US-outperformance booms AND in global "
            "  risk panics; it's weakest in synchronized soft-landing risk-on.\n"
            "- KOSPI foreign equity flows: foreign selling of Korean equities = won-negative.\n"
            "- Global liquidity / credit-spread conditions; geopolitical shocks (incl. Korean peninsula).\n\n"
            "Risk-off → won WEAK (positive delta_krw), risk-on → won STRONG. Magnitude scales with "
            "the severity of the vol move. Always output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Assess the global-risk impulse on USD/KRW.\n"
            "Steps: (1) VIX/vol regime; (2) where we sit on the dollar smile; (3) equity/foreign-flow "
            "signal; (4) net risk impulse per horizon (risk shocks hit 1w-1m hardest, mean-revert by 12m).\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- GLOBAL RISK & DOLLAR ---\n{ctx.global_risk_text}\n\n"
            f"--- MARKET / RISK NEWS ---\n{ctx.news_text[:1500]}\n\n"
            f"--- MATERIAL EVENT TODAY ---\n{ctx.material_event or 'None'}"
        )


agent = AgentGlobalRisk()
