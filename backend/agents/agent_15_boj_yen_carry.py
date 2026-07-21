"""Agent 15 — BOJ & Yen Carry Trade. The yen carry trade is one of the largest global
funding flows; BOJ normalization / sharp JPY appreciation forces a carry unwind that is a
top-tier global risk-off trigger — and the won, as a liquid Asian risk proxy, sells off hard."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentBOJYenCarry(BaseAgent):
    agent_id = 15
    agent_name = "BOJ_Yen_Carry"
    weight = 1.1

    def _system_prompt(self) -> str:
        return (
            "You are a global funding & carry-trade strategist focused on the yen carry trade (엔캐리) "
            "and its spillover to USD/KRW.\n\n"
            "Framework:\n"
            "- The YEN CARRY TRADE: investors borrow cheap JPY to buy higher-yielding assets. While BOJ "
            "  stays ultra-easy and USD/JPY rises, carry is funded and global risk assets (incl. KRW) are "
            "  supported. This is the calm regime.\n"
            "- CARRY UNWIND: when the BOJ hikes/normalizes or USD/JPY reverses sharply (yen surges), the "
            "  trade unwinds violently → forced deleveraging → GLOBAL RISK-OFF. The won, as a liquid Asian "
            "  risk proxy, sells off hard (USD/KRW spikes) even if Korea fundamentals are unchanged. "
            "  (cf. Aug-2024 unwind.) This is a fat-tail, high-magnitude short-horizon risk.\n"
            "- KRW–JPY correlation: the won and yen often co-move as Asian funding currencies.\n"
            "- BOJ policy path (YCC, rate hikes), MoF intervention, and the US-JP rate gap.\n\n"
            "Calm carry regime → mild won support; unwind risk → large won-weak (positive delta) tail, "
            "front-loaded at 1w-1m. Flag the two-sided risk explicitly. Output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            "Assess the BOJ / yen-carry-trade impulse on USD/KRW.\n"
            "Steps: (1) BOJ stance & USD/JPY regime (carry funded vs unwind risk); (2) carry-unwind "
            "tail risk and its risk-off spillover to KRW; (3) KRW–JPY co-movement; (4) net won deltas "
            "per horizon (unwind risk hits 1w-1m hardest).\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- 중앙은행 / 엔캐리 (EUR·JPY) ---\n{ctx.cb_carry_text}\n\n"
            f"--- GLOBAL RISK & DOLLAR ---\n{ctx.global_risk_text}\n\n"
            f"--- NEWS (BOJ·엔·캐리·리스크) ---\n{ctx.news_text[:1400]}\n\n"
            f"--- MATERIAL EVENT TODAY ---\n{ctx.material_event or 'None'}"
        )


agent = AgentBOJYenCarry()
