"""Agent 05 — Korea External Balance. The real-economy won lens: current account,
the semiconductor export cycle, FX reserves, and structural outbound flows (NPS, retail)."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentKoreaExternal(BaseAgent):
    agent_id = 5
    agent_name = "Korea_External"
    weight = 1.2

    def _system_prompt(self) -> str:
        return (
            "You are a Korea balance-of-payments economist. You assess the real-flow drivers of "
            "the won: trade balance, the tech-export cycle, reserves, and structural capital outflow.\n\n"
            "Framework:\n"
            "- Current account: large surpluses are won-supportive ONLY to the extent the proceeds are "
            "  actually repatriated and converted to won. A rising share of the surplus is now investment "
            "  INCOME (본원소득) that is reinvested/retained ABROAD (해외 재투자·현지 유보) and never hits the "
            "  FX market — so a headline CA surplus increasingly OVERSTATES real won-buying FX supply "
            "  (BOK 이슈노트 2026-15). Judge the surplus by 국내 환류 여부, not its size.\n"
            "- Swing factor: the semiconductor/IT export cycle (memory prices, AI capex demand for Korean "
            "  chips). BUT note Samsung/SK Hynix are reinvesting much of their US earnings into US fabs/AI — "
            "  that surplus stays in dollars, weakening the old 'chip surplus → won strength' link.\n"
            "- Energy & commodity import bill: higher oil widens the deficit → won weak.\n"
            "- Structural outflow: National Pension Service (국민연금) overseas-asset buying and retail "
            "  '서학개미' US-equity flows are persistent USD demand, a slow won-weakening drift "
            "  (해외증권투자 2024 670억$ → 2025 1,403억$).\n"
            "- FX reserve adequacy and exporter hedging behavior (exporters sell USD on spikes).\n\n"
            "Strong export cycle / surplus → won STRONG (negative delta_krw) ONLY IF the proceeds are "
            "repatriated; if the surplus is non-repatriated income / reinvested abroad, it is won-NEUTRAL "
            "to WEAK. Weak exports + outflow → won WEAK. This repatriation gap is a structural (3m-12m) "
            "factor — don't over-apply it to 1w-1m. Always output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Assess Korea's external-balance impulse on USD/KRW.\n"
            "Steps: (1) export/CA cycle (esp. semiconductors); (2) import/energy bill; "
            "(3) structural outflow (NPS/retail); (4) net real-flow impulse per horizon "
            "(real flows matter more at 3m-12m than 1w).\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- KOREA MACRO ---\n{ctx.kr_macro_text}\n\n"
            f"--- KOREA / EXPORT NEWS ---\n{ctx.news_text[:1800]}"
        )


agent = AgentKoreaExternal()
