"""Holder for the latest cycle's hierarchical-synthesis output (학계/퍼블릭/프라이빗/수석
판단 과정 서술), so the dashboard can show it without extra DB columns.

Persisted to a small JSON file so the reasoning narrative SURVIVES a server restart —
previously it lived only in memory and vanished on every reboot (repopulating only after
the next 2h cycle), which made the 계층형 종합 panel look empty after any restart.
"""
import json
import os

_STATE: dict = {"hierarchy": None, "updated_at": None}
_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "_hierarchy_state.json")


def set_hierarchy(hierarchy: dict, ts: str | None = None):
    _STATE["hierarchy"] = hierarchy
    _STATE["updated_at"] = ts
    try:
        with open(_FILE, "w", encoding="utf-8") as f:
            json.dump(_STATE, f, ensure_ascii=False)
    except Exception:
        pass


def get_hierarchy() -> dict:
    # Lazily restore the last persisted synthesis after a restart (in-memory is empty).
    if _STATE["hierarchy"] is None:
        try:
            with open(_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and data.get("hierarchy"):
                _STATE["hierarchy"] = data["hierarchy"]
                _STATE["updated_at"] = data.get("updated_at")
        except Exception:
            pass
    return _STATE
