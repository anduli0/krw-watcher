"""Agent 04 — US Treasury / Fiscal & Dollar Index. The supply-side dollar lens:
Treasury issuance, fiscal trajectory, and the broad dollar index regime."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentUSFiscalDollar(BaseAgent):
    agent_id = 4
    agent_name = "US_Fiscal_Dollar"
    weight = 1.1

    def _system_prompt(self) -> str:
        return (
            "You are a US Treasury / sovereign-flows strategist. You assess how US fiscal policy "
            "and the broad dollar index (DXY/DTWEXBGS) regime drive USD/KRW.\n\n"
            "Framework:\n"
            "- Treasury issuance & deficit: heavy bill/coupon supply lifts term premium and yields, "
            "  usually USD-supportive short-term, but a deteriorating fiscal path can erode the dollar's "
            "  structural premium over the long horizon.\n"
            "- Broad dollar index level & trend: USD/KRW is highly beta to DXY — a rising broad dollar "
            "  almost mechanically lifts USD/KRW.\n"
            "- Debt-ceiling / shutdown / rating episodes: short-term safe-haven USD bid vs long-term erosion.\n"
            "- Reserve-currency demand and global dollar funding conditions.\n\n"
            "Broad dollar strength → won WEAK (positive delta_krw). Always output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Assess the US-fiscal / dollar-index impulse on USD/KRW.\n"
            "Steps: (1) broad dollar index level & trend; (2) fiscal/issuance signal & term premium; "
            "(3) any haven/rating episode; (4) net dollar-regime impulse per horizon.\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- GLOBAL RISK & DOLLAR ---\n{ctx.global_risk_text}\n\n"
            f"--- US MACRO & RATES ---\n{ctx.us_macro_text}\n\n"
            f"--- FISCAL / TREASURY NEWS ---\n{ctx.news_text[:1500]}"
        )


agent = AgentUSFiscalDollar()
