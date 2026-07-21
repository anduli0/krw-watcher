"""In-memory ring buffer of activity events for the live dashboard.
Every module emits here; the frontend polls /api/activity to render the feed.
No DB, no tokens — pure observability."""
from collections import deque
from datetime import datetime
from threading import Lock

_MAX = 400
_EVENTS: deque = deque(maxlen=_MAX)
_LOCK = Lock()
_SEQ = 0


def emit(category: str, source: str, message: str,
         color: str = "#8AB4F8", level: str = "info") -> None:
    global _SEQ
    with _LOCK:
        _SEQ += 1
        _EVENTS.append({
            "seq": _SEQ,
            "ts": datetime.utcnow().isoformat(),
            "category": category,
            "source": source,
            "message": message,
            "color": color,
            "level": level,
        })


def recent(after_seq: int = 0, limit: int = 200) -> list[dict]:
    with _LOCK:
        items = [e for e in _EVENTS if e["seq"] > after_seq]
    return items[-limit:]


# ── Convenience emitters (mirrors fed-watcher call sites) ─────────────────────
def system_event(msg: str) -> None:
    emit("system", "system", msg, "#9AA0A6", "info")


def orchestrator_event(msg: str) -> None:
    emit("orchestrator", "orchestrator", msg, "#C58AF9", "info")


def collecting(label: str, source: str) -> None:
    emit("collect", label, f"collecting from {source}…", "#8AB4F8", "info")


def collected(label: str, count: int, detail: str = "") -> None:
    emit("collect", label, f"{count} {detail}".strip(), "#81C995", "ok")


def collect_failed(label: str, msg: str) -> None:
    emit("collect", label, f"failed: {msg}", "#F28B82", "warn")


def agent_start(name: str, round_num: int = 1) -> None:
    emit("agent", name, f"analyzing (round {round_num})…", "#8AB4F8", "info")


def agent_done(name: str, signal: str, delta_krw: float, confidence: float,
               revised: bool = False) -> None:
    tag = " (revised)" if revised else ""
    emit("agent", name,
         f"{signal.upper()} {delta_krw:+.1f}원 · conf {confidence:.0%}{tag}",
         "#81C995", "ok")


def trade_event(msg: str) -> None:
    emit("trade", "trade", msg, "#FBBC04", "info")
