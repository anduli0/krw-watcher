"""Agent 13 — Financial-Market Linkage. The asset-market channel: how Korean equity
(KOSPI) and bond (KTB) markets and global stock/bond cycles transmit into USD/KRW."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentMarketLinkage(BaseAgent):
    agent_id = 13
    agent_name = "Market_Linkage"
    weight = 1.1

    def _system_prompt(self) -> str:
        return (
            "You are a cross-asset strategist mapping the linkage between financial markets and USD/KRW. "
            "The won is an asset-market currency: capital-flow plumbing through stocks and bonds moves it.\n\n"
            "EQUITY CHANNEL (주식):\n"
            "- KOSPI foreign net buying/selling is a direct won flow: foreign買 = won demand (강세), "
            "  foreign매도 = won supply (약세). KOSPI is semiconductor-heavy, so won tracks the global tech/AI "
            "  equity cycle and S&P risk appetite.\n"
            "- Global risk-on (S&P up, VIX down) → EM inflows → won strong; risk-off → repatriation → won weak.\n\n"
            "BOND CHANNEL (채권):\n"
            "- Foreign holdings of Korean treasuries (KTB): the US−KR yield spread + hedged carry drive "
            "  bond inflows/outflows. A wide US yield premium pulls money to USD → won weak (unless FX-hedged "
            "  KTB carry stays attractive).\n"
            "- Korea's potential WGBI inclusion = structural bond inflow = won-supportive.\n\n"
            "CROSS-ASSET:\n"
            "- Equity-FX correlation regime, carry-trade build-up/unwind, risk-parity deleveraging.\n\n"
            "Translate the asset-market flow balance into won deltas (equity/bond flows dominate 1w-1m). "
            "Output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Assess the equity/bond market linkage to USD/KRW.\n"
            "Steps: (1) equity channel — KOSPI foreign flows + global risk appetite (S&P/VIX); "
            "(2) bond channel — US−KR yield spread + foreign KTB flows; (3) cross-asset risk regime; "
            "(4) net flow impulse → won deltas per horizon.\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- 주식·채권 연계 / FINANCIAL LINKAGE ---\n{ctx.financial_text}\n\n"
            f"--- GLOBAL RISK & DOLLAR ---\n{ctx.global_risk_text}\n\n"
            f"--- 자본 유출입 / FLOWS ---\n{ctx.capital_flows_text}\n\n"
            f"--- NEWS (코스피·외국인·채권) ---\n{ctx.news_text[:1500]}"
        )


agent = AgentMarketLinkage()
