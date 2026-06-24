from __future__ import annotations

import time

_STATE: dict[str, object] = {
    "active": False,
    "reason": "waiting_for_match",
    "updated_at": 0.0,
}


def set_win_counter_runtime_active(active: bool, *, reason: str = "") -> None:
    _STATE["active"] = bool(active)
    _STATE["reason"] = str(reason or ("active_match" if active else "waiting_for_match"))
    _STATE["updated_at"] = float(time.monotonic())


def is_win_counter_runtime_active() -> bool:
    return bool(_STATE.get("active", False))


def get_win_counter_runtime_state() -> dict[str, object]:
    return dict(_STATE)
