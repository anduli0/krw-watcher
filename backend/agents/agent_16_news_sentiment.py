"""Agent 16 — News & Headline Reaction. FX is a price variable that reacts to news in
real time. This lens reads the day's ranked, scraped headlines and scores the NET
news-driven impulse on USD/KRW — surprises, sentiment skew, and event catalysts that the
slower fundamental lenses underweight. News impact is front-loaded and decays with horizon."""
from backend.agents.base_agent import BaseAgent, AgentContext, OUTPUT_SCHEMA


class AgentNewsSentiment(BaseAgent):
    agent_id = 16
    agent_name = "News_Sentiment"
    weight = 1.1
    self_consistency_n = 3        # headline reading is noisy — median of 3 reduces variance

    def _system_prompt(self) -> str:
        return (
            "You are a news-flow / headline-reaction strategist on the USD/KRW desk. The exchange rate "
            "is a price that reacts to NEWS in real time — your job is to read the day's ranked headlines "
            "and extract the net news-driven impulse the slower fundamental lenses miss.\n\n"
            "Framework:\n"
            "- SURPRISE, not level: markets move on news vs EXPECTATIONS. A hawkish-surprise Fed headline, "
            "  a hot CPI print, an unexpected BOK signal, or a weak export number moves spot immediately.\n"
            "- SENTIMENT SKEW: aggregate whether the flow of headlines tilts risk-on (won-supportive) or "
            "  risk-off / dollar-bid (won-weak). Weight authoritative outlets (WSJ/FT/Bloomberg/Reuters) "
            "  and official sources higher than noise.\n"
            "- EVENT CATALYSTS: FOMC/BOK decisions, US jobs, CPI, geopolitics (Korean peninsula, trade/tariffs), "
            "  and especially FX-AUTHORITY INTERVENTION WARNINGS (기재부/BOK 구두개입) — these cap or reverse moves.\n"
            "- HEADLINE HALF-LIFE: news shocks hit 1w-1m hardest and decay by 3m-12m (mean-reversion as the "
            "  news is digested) unless they signal a regime change.\n"
            "- Distinguish market-moving news from noise; if headlines are quiet/mixed, say so and keep "
            "  magnitude + confidence low. Do NOT manufacture a signal from stale or irrelevant headlines.\n\n"
            "Output the net news impulse as won deltas per horizon. Output valid JSON per schema."
        )

    def _user_message(self, ctx: AgentContext) -> str:
        news = ctx.news_text.strip() or "(no headlines fetched)"
        return (
            "Read the ranked headlines below and score the NET news-driven impulse on USD/KRW.\n"
            "Steps: (1) identify the 2-4 most market-moving headlines & their surprise direction; "
            "(2) aggregate the risk-on/off & dollar sentiment skew; (3) flag any intervention warning or "
            "event catalyst; (4) net into won deltas per horizon (front-loaded 1w-1m, decaying by 12m). "
            "Keep confidence honest — quiet/mixed news → small delta, low confidence.\n\n"
            f"{OUTPUT_SCHEMA}\n\n"
            f"--- SPOT ---\n{ctx.spot_text}\n\n"
            f"--- MATERIAL EVENT TODAY ---\n{ctx.material_event or 'None'}\n\n"
            f"--- RANKED HEADLINES (중요도순, 공신력 가중) ---\n{news[:2600]}\n\n"
            f"--- 자본 유출입 뉴스 ---\n{ctx.capital_flows_text[:600]}"
        )


agent = AgentNewsSentiment()
