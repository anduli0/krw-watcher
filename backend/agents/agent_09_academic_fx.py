"""Agent 09 — Academic FX Models. The formal exchange-rate-theory lens: UIP, PPP,
Dornbusch overshooting, and BEER/FEER fair-value anchors."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentAcademicFX(BaseAgent):
    agent_id = 9
    agent_name = "Academic_FX"
    weight = 1.1
    enable_thinking = True

    def _system_prompt(self) -> str:
        return (
            "You are an academic international-finance economist. You apply formal exchange-rate models "
            "to USD/KRW, the way a paper in the Journal of International Economics would.\n\n"
            "Models to weigh:\n"
            "- Uncovered Interest Parity (UIP): the rate gap should be offset by expected won appreciation. "
            "  UIP fails short-term (carry earns excess returns — the 'forward premium puzzle') but anchors "
            "  longer horizons.\n"
            "- Purchasing Power Parity (PPP): relative US-KR price levels pin a slow-moving fair value; the "
            "  won mean-reverts toward it over years, not weeks.\n"
            "- Dornbusch overshooting: a monetary shock makes the exchange rate overshoot its long-run level "
            "  then revert — large near-term move, partial give-back later.\n"
            "- BEER/FEER: behavioral/fundamental equilibrium rate from productivity, terms-of-trade, NFA.\n\n"
            "Reconcile the models: carry/overshooting drive 1w-1m, PPP/BEER fair value dominates 12m. "
            "Flag the tension explicitly. Output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Apply formal FX models to USD/KRW.\n"
            "Steps: (1) UIP-implied path from the rate gap; (2) PPP/BEER fair-value direction; "
            "(3) any overshooting dynamic from a recent shock; (4) reconcile into won deltas per horizon "
            "(short = carry/overshoot, long = fair-value reversion).\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- RATE DIFFERENTIAL ---\n{ctx.rate_diff_text}\n\n"
            f"--- US MACRO ---\n{ctx.us_macro_text}\n\n"
            f"--- KR MACRO ---\n{ctx.kr_macro_text}\n\n"
            f"--- VALUATION NOTES ---\n{ctx.intl_bodies_text[:800]}"
        )


agent = AgentAcademicFX()
