"""Agent 14 — ECB & Global Central-Bank Divergence. The euro leg of the dollar:
the EUR is ~57% of the dollar index, so ECB-vs-Fed policy divergence is a primary
driver of broad USD strength, which transmits straight into USD/KRW."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentECBGlobalCB(BaseAgent):
    agent_id = 14
    agent_name = "ECB_Global_CB"
    weight = 1.1

    def _system_prompt(self) -> str:
        return (
            "You are a G10 central-bank strategist. You assess how NON-Fed developed-market "
            "central banks — primarily the ECB, plus BoE/SNB/BoC — move the DOLLAR, and thus USD/KRW.\n\n"
            "Framework:\n"
            "- The euro is ~57% of the DXY/broad-dollar basket. ECB-vs-Fed policy DIVERGENCE is the "
            "  single biggest driver of broad dollar direction: ECB cutting faster than the Fed → EUR "
            "  weak → DXY up → won weak (KRW tracks the broad dollar with high beta).\n"
            "- ECB rate path, inflation (HICP), euro-area growth/recession risk, and any fragmentation.\n"
            "- Relative real-rate and growth differentials between the US and Europe.\n"
            "- US jobs data matters here too: a strong US labor market widens Fed-ECB divergence → USD up.\n\n"
            "Translate ECB-vs-Fed divergence into a broad-dollar impulse, then into USD/KRW won deltas "
            "(EUR/USD direction × KRW's dollar-beta). Output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Assess the ECB / global-DM-central-bank impulse on USD/KRW via the dollar.\n"
            "Steps: (1) ECB vs Fed policy divergence & EUR/USD direction; (2) US jobs/growth read-through "
            "to the divergence; (3) broad-dollar (DXY) impulse; (4) net won deltas per horizon "
            "(KRW ≈ high-beta to the broad dollar).\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- 글로벌 중앙은행 / EUR·JPY ---\n{ctx.cb_carry_text}\n\n"
            f"--- US RATES & JOBS ---\n{ctx.us_macro_text}\n{ctx.jobs_text}\n\n"
            f"--- GLOBAL RISK & DOLLAR ---\n{ctx.global_risk_text}\n\n"
            f"--- NEWS (ECB·유럽·달러) ---\n{ctx.news_text[:1400]}"
        )


agent = AgentECBGlobalCB()
