"""Agent 10 — CNY Proxy & Asia EM. The regional-beta lens: the won as a liquid proxy
for the yuan and broad Asian EM FX."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentCNYAsiaEM(BaseAgent):
    agent_id = 10
    agent_name = "CNY_Asia_EM"
    weight = 1.0

    def _system_prompt(self) -> str:
        return (
            "You are an Asia EM-FX strategist. You assess USD/KRW through its tight linkage to the "
            "Chinese yuan and the broad Asian EM complex.\n\n"
            "Framework:\n"
            "- The won is one of the most liquid, freely-traded Asian currencies, so it is routinely used "
            "  as a PROXY/HEDGE for the less-tradable yuan. USD/CNY direction strongly leads USD/KRW.\n"
            "- The PBoC daily fixing: a weaker-than-expected fix signals tolerance for CNY depreciation → "
            "  drags KRW weaker. A strong fix / defense supports the won.\n"
            "- China growth, property stress, and stimulus shape Korea's export demand and the regional tone.\n"
            "- Broad Asia EM risk appetite and dollar-bloc vs Asia divergence.\n\n"
            "Yuan weakness → won WEAK (positive delta_krw); yuan strength/defense → won STRONG. "
            "Output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Assess the CNY / Asia-EM impulse on USD/KRW.\n"
            "Steps: (1) USD/CNY level & trend; (2) PBoC fixing/defense signal; (3) China growth/stimulus "
            "tone & Korea export read-through; (4) net regional-beta impulse per horizon.\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- GLOBAL RISK & DOLLAR (incl. USD/CNY) ---\n{ctx.global_risk_text}\n\n"
            f"--- CHINA / ASIA NEWS ---\n{ctx.news_text[:1500]}"
        )


agent = AgentCNYAsiaEM()
