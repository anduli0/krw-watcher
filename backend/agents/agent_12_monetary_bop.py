"""Agent 12 — Monetary & Balance-of-Payments. The formal macro-economics of FX:
the monetary model (relative money supply), the BoP identity (current + financial
account), and relative-rate/income drivers."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentMonetaryBoP(BaseAgent):
    agent_id = 12
    agent_name = "Monetary_BoP"
    weight = 1.3
    enable_thinking = True

    def _system_prompt(self) -> str:
        return (
            "You are an international macroeconomist applying the textbook economic determinants of "
            "the exchange rate to USD/KRW. You reason at the level of FORMAL MODELS.\n\n"
            "1. MONETARY MODEL (통화량): the exchange rate is the relative price of two monies. Faster "
            "   relative M2 growth in Korea vs the US → won depreciation (more won chasing goods → higher "
            "   USD/KRW). Combine with relative output growth and relative interest rates. A widening "
            "   KR−US M2 growth gap is structurally won-negative.\n"
            "2. BALANCE-OF-PAYMENTS IDENTITY (국제수지): current account + financial account ≈ 0. A large "
            "   current-account SURPLUS (경상수지 흑자) is won-supportive, BUT two leakages blunt it: "
            "   (a) capital OUTFLOWS (financial account deficit — NPS overseas buying, residents' foreign "
            "   assets, 서학개미); and (b) COMPOSITION — a growing share of the surplus is investment "
            "   INCOME (본원소득수지), and income that is reinvested abroad or retained in foreign "
            "   subsidiaries (e.g., Samsung/SK Hynix US fabs) generates NO won-buying FX supply even though "
            "   it scores as a CA-surplus line item (BOK 2026-15; IMF sees 본원소득 share of CA rising "
            "   23%→42% by 2030). So a record CA surplus need NOT mean won strength — net the realized FX "
            "   supply, not the accounting surplus. Always net (a) and (b) against the surplus.\n"
            "3. INTEREST-RATE DIFFERENTIAL (한미 금리차): higher US rates pull capital out of Korea "
            "   (financial-account outflow) and raise USD carry → won weak. Quantify the US−KR gap.\n"
            "4. CAPITAL FLOWS (자본 유출입): foreign bond/equity inflows = won demand; outflows = won supply. "
            "   Sudden-stop risk on EM during dollar-liquidity squeezes.\n\n"
            "Synthesize these into a coherent won path: monetary/BoP fundamentals anchor the 3m-12m "
            "horizons; flow/rate dynamics drive 1w-1m. Cite the actual M2 gap, CA position, and rate gap. "
            "Output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Apply the monetary model + BoP framework to USD/KRW.\n"
            "Steps: (1) relative M2 growth gap → long-run direction; (2) current account vs capital "
            "account net (BoP); (3) US−KR rate gap → capital-flow direction; (4) net into won deltas "
            "per horizon (fundamentals weight 3m-12m, flows weight 1w-1m).\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- 통화량 / MONETARY MODEL ---\n{ctx.monetary_text}\n\n"
            f"--- 한미 금리차 / RATE DIFFERENTIAL ---\n{ctx.rate_diff_text}\n\n"
            f"--- KOREA MACRO (경상수지·물가) ---\n{ctx.kr_macro_text}\n{ctx.bok_text}\n\n"
            f"--- 자본 유출입 / FLOWS ---\n{ctx.capital_flows_text}\n\n"
            f"--- NEWS ---\n{ctx.news_text[:1400]}"
        )


agent = AgentMonetaryBoP()
