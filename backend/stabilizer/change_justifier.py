"""
Change justifier — when a published horizon forecast moves, the Chief writes a short
KO explanation of WHY (which drivers/agents/event drove it). Stored for transparency
and audit (you should know why your money signal changed before acting on it).
"""
import anthropic
from backend.config import settings


async def justify_change(horizon: str, new_delta: float, prev_delta: float,
                         event: dict | None, agent_results: list[dict]) -> str | None:
    direction = "원화 약세(USD/KRW↑)" if new_delta > prev_delta else "원화 강세(USD/KRW↓)"
    # Pull the most influential agents (highest |delta|×confidence) for this horizon.
    scored = []
    for ar in agent_results:
        hz = (ar.get("horizons") or {}).get(horizon, {})
        d = hz.get("delta_krw", ar.get("delta_krw", 0))
        c = hz.get("confidence", ar.get("confidence", 0))
        scored.append((abs(d) * c, ar.get("agent_name", "?"), d, c, ar.get("reasoning", "")[:160]))
    scored.sort(reverse=True)
    top = scored[:4]
    drivers = "\n".join(f"- {n}: {d:+.0f}원 (conf {c:.0%}) — {r}" for _, n, d, c, r in top)

    prompt = (
        f"USD/KRW {horizon} 예측이 {prev_delta:+.1f}원 → {new_delta:+.1f}원으로 바뀌었습니다 ({direction}).\n"
        f"오늘 이벤트: {event.get('label') if event else '없음'}\n\n"
        f"가장 영향력 큰 에이전트:\n{drivers}\n\n"
        "이 변경의 핵심 이유를 한국어 2문장 이내로 설명하세요. 구체적 드라이버를 인용하고, "
        "프리앰블 없이 설명만 출력하세요."
    )
    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    try:
        resp = await client.messages.create(
            model=settings.MODEL_ID, max_tokens=300,
            messages=[{"role": "user", "content": prompt}])
        return resp.content[0].text.strip()
    except Exception:
        return None
