from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

_MAX_EVENTS = 2048
_LOCK = threading.RLock()
_EVENTS: deque[dict[str, Any]] = deque(maxlen=_MAX_EVENTS)
_SEQUENCE = 0


def current_action_event_sequence() -> int:
    """Return the newest published event sequence number."""
    with _LOCK:
        return int(_SEQUENCE)


def publish_action_event(event_type: str, **payload: Any) -> dict[str, Any]:
    """Publish a small in-process action tooling event.

    The bus is intentionally process-local. Cancel Lab and Action Recorder run in
    the same TvCGUI process, so this avoids polling a transient mailbox value and
    gives the recorder authoritative context about custom cancel requests.
    """
    global _SEQUENCE
    event = dict(payload)
    with _LOCK:
        _SEQUENCE += 1
        event["sequence"] = int(_SEQUENCE)
        event["event_type"] = str(event_type or "event")
        event.setdefault("monotonic", time.monotonic())
        event.setdefault("timestamp", time.strftime("%H:%M:%S"))
        _EVENTS.append(event)
    return dict(event)


def action_events_since(sequence: int) -> tuple[int, list[dict[str, Any]]]:
    """Return all retained events newer than *sequence* and the newest cursor."""
    cursor = int(sequence or 0)
    with _LOCK:
        newest = int(_SEQUENCE)
        events = [dict(event) for event in _EVENTS if int(event.get("sequence", 0)) > cursor]
    return newest, events


def clear_action_events() -> None:
    """Clear retained events. Intended for focused tests and explicit resets."""
    global _SEQUENCE
    with _LOCK:
        _EVENTS.clear()
        _SEQUENCE = 0


__all__ = [
    "publish_action_event",
    "action_events_since",
    "current_action_event_sequence",
    "clear_action_events",
]
