"""
Event calendar — flags days with a scheduled macro catalyst so the orchestrator can
boost the relevant agents and the stabilizer can react faster.

This is a lightweight, manually-maintained calendar. Add known FOMC / BOK / CPI / NFP
dates to EVENTS (YYYY-MM-DD → label). Labels the orchestrator understands:
  "FOMC", "BOK", "US_CPI", "KR_CPI", "NFP", "RISK"
Unknown days return None (calm regime).
"""
from datetime import date

# Fill in upcoming catalysts here. Example placeholders — update each quarter.
EVENTS: dict[str, str] = {
    # "2026-06-17": "FOMC",
    # "2026-07-10": "BOK",
    # "2026-07-15": "US_CPI",
    # "2026-08-01": "NFP",
}


async def get_today_event(today: date | None = None) -> dict | None:
    d = (today or date.today()).isoformat()
    label = EVENTS.get(d)
    if label:
        return {"date": d, "label": label}
    return None
