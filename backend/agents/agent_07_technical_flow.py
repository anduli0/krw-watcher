"""Agent 07 — Technical & Positioning. The price-action lens: momentum, key levels,
and positioning extremes on USD/KRW."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentTechnicalFlow(BaseAgent):
    agent_id = 7
    agent_name = "Technical_Flow"
    weight = 0.9

    def _system_prompt(self) -> str:
        return (
            "You are a technical/positioning strategist on the USD/KRW desk. You read price action "
            "and crowding, not fundamentals.\n\n"
            "Framework:\n"
            "- Trend & momentum: is spot above/below its recent range; is it trending or mean-reverting.\n"
            "- Round-number magnets and intervention zones (e.g. 1,300 / 1,350 / 1,400 / 1,450) act as "
            "  support/resistance because authorities and exporters cluster orders there.\n"
            "- Positioning extremes: crowded long-USD carry is prone to sharp won-strength snapbacks.\n"
            "- Volatility regime: realized vol expansion widens the expected path.\n\n"
            "You only have spot and recent change here — be HONEST about limited data and keep "
            "confidence modest (≤0.6) unless the level signal is unambiguous. Output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Give a technical/positioning read on USD/KRW from spot and recent moves.\n"
            "Steps: (1) trend & momentum; (2) nearest support/resistance & intervention zone; "
            "(3) positioning risk; (4) net technical impulse per horizon (technicals dominate 1w, "
            "fade by 12m).\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT & RECENT MOVE ---\n{ctx.spot_text}\n{ctx.flows_text}\n\n"
            f"--- GLOBAL RISK & DOLLAR (context) ---\n{ctx.global_risk_text}"
        )


agent = AgentTechnicalFlow()
