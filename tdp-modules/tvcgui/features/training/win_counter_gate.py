from __future__ import annotations

import time
from typing import Any, Iterable

# The three appended Yami variations load a different/unsafe HUD resource route.
# Do not touch the shared digit texture panes while any one is resident.
YAMI_CHARACTER_IDS = frozenset((0x17, 0x18, 0x19))

_STATE: dict[str, object] = {
    "active": False,
    "reason": "waiting_for_match",
    "unsafe_character_ids": (),
    "updated_at": 0.0,
}


def _normalized_unsafe_ids(character_ids: Iterable[Any] | None) -> tuple[int, ...]:
    out: set[int] = set()
    for raw in character_ids or ():
        try:
            cid = int(raw)
        except (TypeError, ValueError):
            continue
        if cid in YAMI_CHARACTER_IDS:
            out.add(cid)
    return tuple(sorted(out))


def set_win_counter_runtime_active(
    active: bool,
    *,
    reason: str = "",
    unsafe_character_ids: Iterable[Any] | None = None,
) -> None:
    """Set whether the win-counter may write this frame.

    Any live Yami route is fail-closed: the UI may remain open/readable, but
    texture overrides and freeze writes are disabled before a Dolphin write is
    attempted.
    """
    unsafe_ids = _normalized_unsafe_ids(unsafe_character_ids)
    enabled = bool(active) and not unsafe_ids
    if unsafe_ids:
        resolved_reason = "yami_safeguard"
    else:
        resolved_reason = str(reason or ("active_match" if enabled else "waiting_for_match"))
    _STATE["active"] = enabled
    _STATE["reason"] = resolved_reason
    _STATE["unsafe_character_ids"] = unsafe_ids
    _STATE["updated_at"] = float(time.monotonic())


def is_win_counter_runtime_active() -> bool:
    return bool(_STATE.get("active", False))


def get_win_counter_runtime_state() -> dict[str, object]:
    return dict(_STATE)


def win_counter_block_message() -> str:
    reason = str(_STATE.get("reason") or "")
    if reason == "yami_safeguard":
        return "Yami safeguard active: Win Counter writes are disabled while any Yami is loaded."
    if reason == "outside_match":
        return "Win Counter writes are paused outside an active match."
    return "Win Counter writes are currently unavailable."
