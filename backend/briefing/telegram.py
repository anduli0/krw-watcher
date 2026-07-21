"""Telegram delivery — pushes the daily brief to the owner's chat via Bot API.
Reuses the existing personal bot. Degrades to a no-op (logged) if unconfigured."""
import asyncio
import logging
import httpx
from backend.config import settings
from backend.data import activity_log as AL

logger = logging.getLogger("krw_watcher.telegram")
MAX_LEN = 3800   # Telegram hard limit 4096; leave headroom


def _chat_ids() -> list[str]:
    return [c.strip() for c in (settings.TELEGRAM_CHAT_ID or "").split(",") if c.strip()]


def _chunks(text: str) -> list[str]:
    if len(text) <= MAX_LEN:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        if len(cur) + len(line) + 1 > MAX_LEN:
            out.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur)
    return out


async def send_forecast_update(pub_view: dict, report_ko: str, spot, changed: list) -> bool:
    """Real-time push: fires when the published forecast materially changes on a cycle."""
    import html as _h
    e = lambda s: _h.escape(str(s or ""))
    names = {"1w": "1주", "1m": "1개월", "3m": "3개월", "12m": "1년"}
    sig_ko = {"krw_weak": "원화 약세", "krw_strong": "원화 강세", "neutral": "중립"}
    lines = [f"🔔 <b>USD/KRW 예측 업데이트</b> · 스팟 {spot:.2f}원" if spot else "🔔 <b>USD/KRW 예측 업데이트</b>"]
    for h in ("1w", "1m", "3m", "12m"):
        v = pub_view.get(h)
        if not v:
            continue
        star = " ◀ 변경" if h in changed else ""
        lines.append(f"· {names[h]}: {v['delta']:+.1f}원 → {v['implied']} "
                     f"({sig_ko.get(v['signal'], v['signal'])}, {round(v['conf']*100)}%){star}")
    if report_ko:
        lines.append("\n" + e(report_ko[:1200]))
    lines.append("\n<i>KRW-Watcher · 22 에이전트 · 자동 업데이트</i>")
    return await send_telegram("\n".join(lines))


async def send_telegram(text: str, parse_mode: str = "HTML") -> bool:
    token = settings.TELEGRAM_BOT_TOKEN
    chats = _chat_ids()
    if not token or not chats:
        AL.emit("telegram", "telegram", "skipped (TELEGRAM_BOT_TOKEN/CHAT_ID not set)", "#9AA0A6", "warn")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    ok_all = True
    async with httpx.AsyncClient(timeout=20) as client:
        for chat_id in chats:
            for chunk in _chunks(text):
                payload = {"chat_id": chat_id, "text": chunk,
                           "parse_mode": parse_mode, "disable_web_page_preview": True}
                sent = False
                for attempt in range(3):                  # retry transient ConnectErrors
                    try:
                        r = await client.post(url, json=payload)
                        if r.status_code == 200:
                            sent = True
                            break
                        logger.warning("Telegram send failed %s: %s", r.status_code, r.text[:200])
                        if 400 <= r.status_code < 500:
                            break                          # bad request — retrying won't help
                    except Exception as e:
                        logger.warning("Telegram send error (try %d): %s: %r", attempt + 1, type(e).__name__, e)
                    await asyncio.sleep(1.5 * (attempt + 1))
                ok_all = ok_all and sent
    AL.emit("telegram", "telegram",
            f"브리프 전송 {'성공' if ok_all else '일부 실패'} → {len(chats)} chat",
            "#41d18b" if ok_all else "#E5A03E", "ok" if ok_all else "warn")
    return ok_all
