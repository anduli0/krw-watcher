"""Agent 01 — Fed Policy. The US monetary-policy lens: how the Fed's rate path and
balance sheet drive the dollar leg of USD/KRW."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentFedPolicy(BaseAgent):
    agent_id = 1
    agent_name = "Fed_Policy"
    weight = 1.4
    enable_thinking = True

    def _system_prompt(self) -> str:
        return (
            "You are a senior Fed-watcher at a global macro desk. You assess how the "
            "Federal Reserve's policy stance moves the DOLLAR leg of USD/KRW.\n\n"
            "Framework:\n"
            "- Fed funds level & expected path vs market pricing. Hawkish surprise → USD up → won weak.\n"
            "- Real US rates (DGS10 − breakeven): higher real yields pull capital into USD.\n"
            "- QT / balance-sheet runoff tightens dollar liquidity → USD bid.\n"
            "- Powell/FOMC communication tone, dot plot drift, and data-dependence.\n"
            "- The 'dollar smile': Fed easing into a soft landing is USD-negative; easing into "
            "  a global risk event is USD-positive (safe haven).\n\n"
            "Translate the Fed stance into USD/KRW won deltas. Always output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Assess the Fed policy impulse on USD/KRW from the data below.\n"
            "Steps: (1) current funds level & implied path; (2) real-rate signal; "
            "(3) communication/QT tone; (4) net dollar impulse → won deltas per horizon.\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- US MACRO & RATES ---\n{ctx.us_macro_text}\n\n"
            f"--- RATE DIFFERENTIAL ---\n{ctx.rate_diff_text}\n\n"
            f"--- FED-RELEVANT NEWS ---\n{ctx.news_text[:1500]}\n\n"
            f"--- MATERIAL EVENT TODAY ---\n{ctx.material_event or 'None'}"
        )


agent = AgentFedPolicy()
