"""
Sell-side bank & securities-house panel — KRW-Watcher's analog to fed-watcher's
12 regional-Fed agents.

Each desk emulates the house style and forecasting process of a major FX research
desk (global IBs + Korean securities houses). They give the committee a spread of
real-world institutional views, which the orchestrator treats as a sub-committee.

These are STYLIZED personas built from each house's publicly-known approach — they
are not the firms' actual proprietary forecasts.
"""
from dataclasses import dataclass
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


@dataclass
class DeskProfile:
    agent_id: int
    name: str
    house_style: str


PROFILES: list[DeskProfile] = [
    DeskProfile(
        101, "Desk_GS",
        "Goldman Sachs FX Research style: model-driven (GSDEER fair value), "
        "structurally constructive on the dollar when US growth leads; thematic, "
        "macro-regime framing; willing to hold contrarian 12m targets vs spot."),
    DeskProfile(
        102, "Desk_JPM",
        "J.P. Morgan FX style: flow- and positioning-aware, heavy weight on the rate "
        "differential and global PMI cycle; pragmatic, quick to revise on data; uses "
        "fair-value bands and risk-premium decomposition."),
    DeskProfile(
        103, "Desk_MS",
        "Morgan Stanley FX style: scenario-tree thinking (bull/base/bear), strong focus "
        "on the dollar smile and US exceptionalism debate; explicit risk-reward skew."),
    DeskProfile(
        104, "Desk_Nomura",
        "Nomura Asia-FX style: the deepest Asia/KRW specialist; weights the semiconductor "
        "export cycle, KOSPI foreign flows, NPS hedging, and BOK intervention reaction "
        "function more heavily than global houses; CNY-beta aware."),
    DeskProfile(
        105, "Desk_Citi",
        "Citi FX style: broadest real-money + corporate flow franchise; emphasizes "
        "client positioning (CitiFX flows), behavioral-equilibrium fair value (BEER), "
        "and short-term momentum signals."),
    DeskProfile(
        106, "Desk_Samsung_Sec",
        "삼성증권 (Samsung Securities) style: Korea onshore desk view; close read of "
        "exporter USD-selling, onshore corporate hedging demand, retail '서학개미' outflow, "
        "and 기재부/BOK smoothing signals; tends to fade extreme won weakness near "
        "intervention zones."),
    DeskProfile(
        107, "Desk_Mirae",
        "미래에셋 (Mirae Asset) style: Korea onshore + global-allocation view; weights NPS "
        "and institutional overseas-asset flows, the AI/semiconductor demand cycle for "
        "Korean exports, and cross-asset (KOSPI/UST) signals."),
]


class BankDeskAgent(BaseAgent):
    weight = 0.85   # individual desks are lighter; the panel matters in aggregate

    def __init__(self, profile: DeskProfile):
        super().__init__()
        self.agent_id = profile.agent_id
        self.agent_name = profile.name
        self._profile = profile

    def _system_prompt(self) -> str:
        return (
            f"You are the USD/KRW forecasting desk emulating the house style of {self._profile.name}.\n\n"
            f"House style: {self._profile.house_style}\n\n"
            "Produce a USD/KRW forecast IN THAT HOUSE'S STYLE. Lean into the lens that house is known "
            "for rather than giving a generic view — the committee benefits from your distinct angle. "
            "Stay disciplined on the sign convention and confidence calibration. Output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        return (
            f"Give {self._profile.name}'s USD/KRW forecast across horizons, in your house style.\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- US MACRO & RATES ---\n{ctx.us_macro_text}\n\n"
            f"--- KR MACRO & BOK ---\n{ctx.kr_macro_text}\n{ctx.bok_text}\n\n"
            f"--- RATE DIFFERENTIAL ---\n{ctx.rate_diff_text}\n\n"
            f"--- GLOBAL RISK & DOLLAR ---\n{ctx.global_risk_text}\n\n"
            f"--- STREET CONSENSUS NOTES ---\n{ctx.ib_consensus_text[:1000]}\n\n"
            f"--- NEWS ---\n{ctx.news_text[:1200]}"
        )


BANK_DESK_AGENTS: list[BankDeskAgent] = [BankDeskAgent(p) for p in PROFILES]
