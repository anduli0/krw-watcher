"""Tiny in-process TTL cache so the collector can re-use data without re-hitting
external APIs. Mirrors fed-watcher's cache interface: get(key, ttl) / set(key, value)."""
import time
from typing import Any, Optional

_STORE: dict[str, tuple[float, Any]] = {}


def get(key: str, ttl_seconds: int) -> Optional[Any]:
    entry = _STORE.get(key)
    if not entry:
        return None
    ts, value = entry
    if (time.time() - ts) > ttl_seconds:
        _STORE.pop(key, None)
        return None
    return value


def set(key: str, value: Any) -> None:
    _STORE[key] = (time.time(), value)


def clear() -> None:
    _STORE.clear()
