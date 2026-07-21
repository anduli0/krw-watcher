"""Bank of Korea ECOS (한국은행 경제통계시스템) client.
Optional — only used when BOK_ECOS_KEY is set. Fetches the BOK base rate and a
few Korea-side series. Degrades gracefully to None when the key is absent so the
rest of the system keeps running on FRED data alone.

API docs: https://ecos.bok.or.kr/api/
"""
import httpx
from backend.config import settings
from backend.data.cache import data_cache

ECOS_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"
TTL = 6 * 3600

# ECOS statistic codes (통계표코드 / 항목코드).
# 722Y001 = 한국은행 기준금리 / 정책금리 관련 표.
SERIES = {
    "base_rate": ("722Y001", "0101000"),   # 한국은행 기준금리 (월)
}


async def fetch_base_rate() -> dict | None:
    if not settings.BOK_ECOS_KEY:
        return None
    cache_key = "bok_base_rate"
    cached = data_cache.get(cache_key, TTL)
    if cached:
        return cached

    stat_code, item_code = SERIES["base_rate"]
    # /{key}/json/kr/1/5/{STAT_CODE}/M/{start}/{end}/{ITEM_CODE}
    url = (
        f"{ECOS_BASE}/{settings.BOK_ECOS_KEY}/json/kr/1/5/"
        f"{stat_code}/M/200001/202612/{item_code}"
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
        rows = data.get("StatisticSearch", {}).get("row", [])
        if not rows:
            return None
        latest = rows[-1]
        result = {
            "base_rate": float(latest.get("DATA_VALUE")),
            "as_of": latest.get("TIME"),
        }
        data_cache.set(cache_key, result)
        return result
    except Exception:
        return None


def base_rate_text(snapshot: dict | None) -> str:
    if not snapshot:
        return "BOK base rate: unavailable (set BOK_ECOS_KEY for live data)"
    return f"한국은행 기준금리: {snapshot['base_rate']}% (as of {snapshot['as_of']})"
