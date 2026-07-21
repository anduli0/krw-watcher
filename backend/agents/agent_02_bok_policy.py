"""Agent 02 — Bank of Korea Policy. The won-side monetary lens: BOK base rate,
domestic inflation/growth, and verbal/actual FX intervention."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentBOKPolicy(BaseAgent):
    agent_id = 2
    agent_name = "BOK_Policy"
    weight = 1.3

    def _system_prompt(self) -> str:
        return (
            "You are a Bank of Korea (한국은행) policy specialist. You assess the WON leg of "
            "USD/KRW: how BOK policy and FX-stability actions move the won.\n\n"
            "Framework:\n"
            "- BOK base rate vs Fed: a narrowing US−KR gap supports the won; a widening gap pressures it.\n"
            "- Korean CPI vs BOK's 2% target and household-debt/property constraints on rate cuts.\n"
            "- FX intervention reaction function: above key psychological levels (e.g. 1,400-1,450),\n"
            "  authorities (BOK + 기획재정부) lean against won weakness via verbal warnings, NPS swap\n"
            "  lines, and smoothing operations — caps the speed of depreciation.\n"
            "- Capital-flow rules, FX reserve adequacy.\n\n"
            "Higher relative BOK hawkishness / intervention → won STRONG (negative delta_krw). "
            "Always output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Assess the BOK / Korean-authority impulse on USD/KRW.\n"
            "Steps: (1) base-rate stance vs Fed; (2) domestic inflation/debt constraints; "
            "(3) intervention risk at current spot level; (4) net won impulse per horizon.\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- BOK / KR RATES ---\n{ctx.bok_text}\n{ctx.kr_macro_text}\n\n"
            f"--- RATE DIFFERENTIAL ---\n{ctx.rate_diff_text}\n\n"
            f"--- KOREA NEWS ---\n{ctx.news_text[:1500]}"
        )


agent = AgentBOKPolicy()
