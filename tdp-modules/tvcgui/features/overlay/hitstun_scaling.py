"""Live air-recovery deterioration state for the transparent HUD.

TvC keeps ordinary resolved hitstun at fighter +0x1210, but airborne recovery
uses a separate lockout timer at fighter +0x1220. The native deterioration
path reduces that lockout by floor(fighter[+0x11CC] / 4) and clamps the
initialized result to a minimum of four frames.

This module is read-only. It reports the native deterioration driver, the
victim's current air-recovery lockout, and a small latched estimate of the
lockout before deterioration so the HUD can draw a useful deflation gauge.
"""
from __future__ import annotations

from typing import Any

RULE_CONTEXT_PTR_ADDR = 0x803EFB4C
RULE_ARRAY_OFF = 0x5C
HITSTUN_DECAY_RULE_INDEX = 11

OFF_STATE_FLAGS_6C = 0x006C
OFF_COMBO_COUNT = 0x11C4
OFF_DECAY_COUNTER = 0x11CC
OFF_UNTECH_TIMER = 0x1220

CANTUKEMI_MASK = 0x02000000
MIN_UNTECH_FRAMES = 4
DECAY_NUMERATOR = 1
DECAY_DENOMINATOR = 4

SLOT_LABELS = ("P1-C1", "P1-C2", "P2-C1", "P2-C2")
_STATE_CACHE: dict[str, dict] = {}
_LAST_BASES: dict[str, int] = {}
_LATCHES: dict[str, dict] = {}
_POLL_COUNTER = 0


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _read_u32(address: int) -> int:
    if not address:
        return 0
    try:
        from tvcgui.platform.dolphin import rd32

        return _safe_int(rd32(int(address))) & 0xFFFFFFFF
    except Exception:
        return 0


def _valid_runtime_ptr(value: int) -> bool:
    value = int(value) & 0xFFFFFFFF
    return 0x80000000 <= value <= 0x817FFFFF or 0x90000000 <= value <= 0x93FFFFFF


def _rule_enabled() -> bool | None:
    """Read the native rule gate used by the deterioration branch."""
    context = _read_u32(RULE_CONTEXT_PTR_ADDR)
    if not _valid_runtime_ptr(context):
        return None
    value = _read_u32(context + RULE_ARRAY_OFF + HITSTUN_DECAY_RULE_INDEX * 4)
    return bool(value)


def _point_slot(payload: dict, team: str) -> tuple[str, dict] | None:
    rows: list[tuple[str, dict]] = []
    for suffix in ("C1", "C2"):
        label = f"{team}-{suffix}"
        row = payload.get(label)
        if isinstance(row, dict):
            rows.append((label, row))
    if not rows:
        return None

    point_rows = [
        item for item in rows
        if bool(item[1].get("damage_point_active", item[1].get("damage_is_point")))
    ]
    if len(point_rows) == 1:
        return point_rows[0]
    return rows[0]


def _owner_slot(payload: dict, team: str) -> tuple[str, dict] | None:
    c1 = f"{team}-C1"
    row = payload.get(c1)
    if isinstance(row, dict) and _safe_int(row.get("base")):
        return c1, row
    return _point_slot(payload, team)


def _sanitize_counter(value: int) -> int:
    return max(0, min(999, _safe_int(value)))


def _sanitize_timer(value: int) -> int:
    value = _safe_int(value) & 0xFFFFFFFF
    if value & 0x80000000:
        value -= 0x100000000
    if value < 0 or value > 999:
        return 0
    return int(value)


def _empty_state(rule_enabled: bool | None) -> dict:
    return {
        "hitstun_decay_live": False,
        "hitstun_decay_rule_enabled": rule_enabled,
        "hitstun_decay_combo_count": 0,
        "hitstun_decay_counter": 0,
        "hitstun_decay_frames": 0,
        "hitstun_decay_min_frames": MIN_UNTECH_FRAMES,
        "hitstun_untech_remaining": 0,
        "hitstun_untech_effective_start": 0,
        "hitstun_untech_base_estimate": 0,
        "hitstun_untech_latched_loss": 0,
        "hitstun_untech_approximate": False,
        "hitstun_cantukemi": False,
        "hitstun_decay_owner_slot": "",
        "hitstun_decay_victim_slot": "",
        "_hitstun_victim_base": 0,
    }


def _snapshot_team(payload: dict, team: str, rule_enabled: bool | None) -> dict:
    owner_info = _owner_slot(payload, team)
    opponent = "P2" if team == "P1" else "P1"
    victim_info = _point_slot(payload, opponent)
    if owner_info is None:
        return _empty_state(rule_enabled)

    owner_label, owner = owner_info
    owner_base = _safe_int(owner.get("base"))
    combo_count = _sanitize_counter(_read_u32(owner_base + OFF_COMBO_COUNT)) if owner_base else 0
    counter = _sanitize_counter(_read_u32(owner_base + OFF_DECAY_COUNTER)) if owner_base else 0

    decay_frames = counter // DECAY_DENOMINATOR
    if rule_enabled is False:
        decay_frames = 0

    victim_label = ""
    victim_base = 0
    untech_remaining = 0
    cantukemi = False
    if victim_info is not None:
        victim_label, victim = victim_info
        victim_base = _safe_int(victim.get("base"))
        if victim_base:
            untech_remaining = _sanitize_timer(_read_u32(victim_base + OFF_UNTECH_TIMER))
            cantukemi = bool(_read_u32(victim_base + OFF_STATE_FLAGS_6C) & CANTUKEMI_MASK)

    return {
        "hitstun_decay_live": bool(owner_base),
        "hitstun_decay_rule_enabled": rule_enabled,
        "hitstun_decay_combo_count": combo_count,
        "hitstun_decay_counter": counter,
        "hitstun_decay_frames": max(0, decay_frames),
        "hitstun_decay_min_frames": MIN_UNTECH_FRAMES,
        "hitstun_untech_remaining": untech_remaining,
        "hitstun_untech_effective_start": 0,
        "hitstun_untech_base_estimate": 0,
        "hitstun_untech_latched_loss": 0,
        "hitstun_untech_approximate": False,
        "hitstun_cantukemi": cantukemi,
        "hitstun_decay_owner_slot": owner_label,
        "hitstun_decay_victim_slot": victim_label,
        "_hitstun_victim_base": victim_base,
    }


def _apply_untech_latch(team: str, state: dict) -> dict:
    """Latch the start of each observed untech timer for gauge rendering.

    The subprocess can sample after the native timer has already decremented,
    so the reconstructed pre-deterioration base is deliberately marked as an
    estimate. The native decay amount itself is exact.
    """
    latch = _LATCHES.setdefault(team, {
        "victim_base": 0,
        "prev_remaining": 0,
        "prev_counter": 0,
        "effective_start": 0,
        "base_estimate": 0,
        "loss": 0,
        "approximate": False,
    })

    victim_base = _safe_int(state.get("_hitstun_victim_base"))
    remaining = max(0, _safe_int(state.get("hitstun_untech_remaining")))
    counter = max(0, _safe_int(state.get("hitstun_decay_counter")))
    combo_count = max(0, _safe_int(state.get("hitstun_decay_combo_count")))
    loss = max(0, _safe_int(state.get("hitstun_decay_frames")))

    victim_changed = victim_base != _safe_int(latch.get("victim_base"))
    previous = max(0, _safe_int(latch.get("prev_remaining")))
    counter_changed = counter != max(0, _safe_int(latch.get("prev_counter")))

    # A native hit normally writes a new lockout that jumps above the previous
    # countdown. The first observed nonzero value and a same-height refresh on
    # a changed deterioration counter are also treated as a new hit.
    new_lock = remaining > 0 and (
        victim_changed
        or previous <= 0
        or remaining > previous
        or (counter_changed and remaining >= previous)
    )

    if new_lock:
        latch["effective_start"] = remaining
        latch["loss"] = loss
        latch["base_estimate"] = max(remaining, remaining + loss)
        latch["approximate"] = True
    elif remaining > 0 and _safe_int(latch.get("effective_start")) <= 0:
        latch["effective_start"] = remaining
        latch["loss"] = loss
        latch["base_estimate"] = max(remaining, remaining + loss)
        latch["approximate"] = True

    # A fresh neutral state clears old last-hit geometry. During an active
    # combo, keep the last initialized bar so the removed segment stays visible
    # even after the current timer reaches zero.
    if remaining <= 0 and combo_count <= 0 and counter <= 0:
        latch["effective_start"] = 0
        latch["base_estimate"] = 0
        latch["loss"] = 0
        latch["approximate"] = False

    latch["victim_base"] = victim_base
    latch["prev_remaining"] = remaining
    latch["prev_counter"] = counter

    state["hitstun_untech_effective_start"] = max(0, _safe_int(latch.get("effective_start")))
    state["hitstun_untech_base_estimate"] = max(0, _safe_int(latch.get("base_estimate")))
    state["hitstun_untech_latched_loss"] = max(0, _safe_int(latch.get("loss")))
    state["hitstun_untech_approximate"] = bool(latch.get("approximate", False))
    return state


def annotate_hitstun_scaling_payload(payload: dict, render_snap_by_slot: dict | None = None) -> None:
    """Add native air-recovery deterioration fields to each team row in-place."""
    global _POLL_COUNTER
    del render_snap_by_slot
    if not isinstance(payload, dict):
        return

    _POLL_COUNTER += 1
    bases = {
        label: _safe_int((payload.get(label) or {}).get("base"))
        for label in SLOT_LABELS
        if isinstance(payload.get(label), dict)
    }
    roster_changed = bases != _LAST_BASES
    missing = any(team not in _STATE_CACHE for team in ("P1", "P2"))
    refresh = roster_changed or missing or (_POLL_COUNTER % 2) == 0

    if refresh:
        rule = _rule_enabled()
        for team in ("P1", "P2"):
            state = _snapshot_team(payload, team, rule)
            _STATE_CACHE[team] = _apply_untech_latch(team, state)
        _LAST_BASES.clear()
        _LAST_BASES.update(bases)

    for team in ("P1", "P2"):
        state = dict(_STATE_CACHE.get(team) or {})
        state.pop("_hitstun_victim_base", None)
        for suffix in ("C1", "C2"):
            row = payload.get(f"{team}-{suffix}")
            if isinstance(row, dict):
                row.update(state)
