import asyncio, json

async def main():
    out = {}
    try:
        from backend.data.collector import collect_data, get_latest as _gl
    except Exception:
        _gl = None
    try:
        from backend.data.collector import collect_data
        await collect_data()
    except Exception as e:
        out["collect_err"] = str(e)[:150]

    # Pull whatever the collector cached
    try:
        from backend.data import collector as C
        latest = getattr(C, "_LATEST", None)
        if latest:
            snap = latest.get("snapshot")
            out["spot"] = getattr(snap, "spot", None) if snap else None
            out["bok"] = str(latest.get("bok"))[:400]
            news = latest.get("news_items") or []
            out["news"] = [{"title": getattr(n, "title", str(n))[:160],
                            "published_at": str(getattr(n, "published_at", ""))[:19]} for n in news[:20]]
            out["macro"] = (snap.summary_text()[:1500] if snap and hasattr(snap, "summary_text") else str(snap)[:1500])
            out["collected_at"] = str(latest.get("collected_at"))
    except Exception as e:
        out["cache_err"] = str(e)[:150]

    # Current published horizon forecast (deltas already computed, conf collapsed)
    try:
        from backend.database.init_db import AsyncSessionLocal
        from backend.database import crud
        async with AsyncSessionLocal() as db:
            fc = await crud.get_published_forecasts(db)
            out["forecast"] = [{"horizon": getattr(f, "horizon", None),
                                "delta": getattr(f, "published_delta", None),
                                "confidence": getattr(f, "confidence", None),
                                "signal": getattr(f, "signal", None)} for f in (fc or [])]
    except Exception as e:
        out["fc_err"] = str(e)[:150]

    open("_inputs2.json", "w", encoding="utf-8").write(json.dumps(out, ensure_ascii=False, indent=1))
    print("krw inputs: news", len(out.get("news", [])), "spot", out.get("spot"), "fc", len(out.get("forecast", [])))

asyncio.run(main())
