"""Live damage-scaling state used by the transparent HUD.

This module reads the confirmed native scaling fields and converts them into
compact labels for the existing combo card. It does not replace or wrap the HUD
manager, so new manager keyword arguments remain fully compatible.
"""
from __future__ import annotations

import math
import struct
from typing import Any

TEAM_CORRECTION_ADDR = {
    "P1": 0x8055FBC0,
    "P2": 0x8055FBC4,
}
ROLL_ID = 19
GIANT_IDS = {11, 22}
SLOT_LABELS = {"P1-C1", "P1-C2", "P2-C1", "P2-C2"}
_STATE_CACHE: dict[str, dict] = {}
_LAST_HP: dict[str, int] = {}
_LAST_BASES: dict[str, int] = {}
_POLL_COUNTER = 0


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if math.isfinite(parsed) else default


def _read_u32(address: int) -> int:
    if not address:
        return 0
    try:
        from tvcgui.platform.dolphin import rd32

        value = rd32(int(address))
        return _safe_int(value) & 0xFFFFFFFF
    except Exception:
        return 0


def _read_f32(address: int, default: float = 0.0) -> float:
    raw = _read_u32(address)
    try:
        value = struct.unpack(">f", struct.pack(">I", raw))[0]
    except Exception:
        return default
    if not math.isfinite(value) or abs(value) > 100000.0:
        return default
    return float(value)


def _read_bytes(address: int, size: int) -> bytes:
    if not address or size <= 0:
        return b""
    try:
        from tvcgui.platform.dolphin import rbytes

        raw = rbytes(int(address), int(size))
        return bytes(raw or b"")
    except Exception:
        return b""


def _block_u32(block: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(block):
        return 0
    return int.from_bytes(block[offset:offset + 4], "big", signed=False)


def _block_f32(block: bytes, offset: int, default: float = 0.0) -> float:
    raw = _block_u32(block, offset)
    try:
        value = struct.unpack(">f", struct.pack(">I", raw))[0]
    except Exception:
        return default
    if not math.isfinite(value) or abs(value) > 100000.0:
        return default
    return float(value)


def _snapshot_damage_state(
    slot_label: str,
    snap: dict,
    *,
    team_correction: float,
) -> dict:
    base = _safe_int(snap.get("base"))
    if not base:
        return {
            "damage_live": False,
            "damage_combo_count": 0,
            "damage_combo_scale": 1.0,
            "damage_point_active": False,
            "damage_is_point": False,
            "damage_combo_lane_active": False,
            "damage_baroque_permission": 0,
            "damage_baroque_active": False,
            "damage_baroque_red_spent": 0,
            "damage_roll_power_flags": 0,
            "damage_roll_puddle_stacks": 0,
            "damage_height": 0.0,
            "damage_script_state_active": False,
            "damage_team_correction": 1.0,
        }

    state_block = _read_bytes(base + 0x70, 0x24)
    combo_block = _read_bytes(base + 0x11C4, 0x18)
    point_block = _read_bytes(base + 0x44A0, 0x08)
    special_block = _read_bytes(base + 0x44BC, 0x14C)

    combo_scale = _block_f32(combo_block, 0x14, 1.0)
    if combo_scale <= 0.0 or combo_scale > 4.0:
        combo_scale = 1.0

    if team_correction <= 0.0 or team_correction > 4.0:
        team_correction = 1.0

    puddle_stacks = max(
        0,
        min(5, _safe_int(_block_u32(special_block, 0x148))),
    )
    height = max(0.0, _block_f32(state_block, 0x20, 0.0))
    if height > 20.0:
        height = 0.0

    return {
        "damage_live": True,
        "damage_combo_count": max(
            0,
            _safe_int(_block_u32(combo_block, 0x00)),
        ),
        "damage_combo_scale": combo_scale,
        # +0x44A0 is the stable point fighter flag. +0x44A4 is a separate
        # runtime state that the native combo helper reads when choosing the
        # 5% point lane versus the 3% reserve lane.
        "damage_point_active": bool(_block_u32(point_block, 0x00)),
        "damage_is_point": bool(_block_u32(point_block, 0x00)),
        "damage_combo_lane_active": bool(_block_u32(point_block, 0x04)),
        "damage_baroque_permission": _block_u32(point_block, 0x04),
        "damage_baroque_active": bool(_block_u32(special_block, 0x00)),
        "damage_baroque_red_spent": max(
            0,
            _safe_int(_block_u32(special_block, 0x90)),
        ),
        "damage_roll_power_flags": _block_u32(special_block, 0x144),
        "damage_roll_puddle_stacks": puddle_stacks,
        "damage_height": height,
        "damage_script_state_active": bool(
            _block_u32(state_block, 0x00) & 0x00100000
        ),
        "damage_team_correction": team_correction,
    }


def _team_corrections() -> dict[str, float]:
    values: dict[str, float] = {}
    for team, address in TEAM_CORRECTION_ADDR.items():
        value = _read_f32(address, 1.0)
        if value <= 0.0 or value > 4.0:
            value = 1.0
        values[team] = value
    return values


def annotate_damage_scaling_payload(
    payload: dict,
    render_snap_by_slot: dict,
) -> None:
    """Add native scaling fields to the four fighter rows in-place."""
    global _POLL_COUNTER
    if not isinstance(payload, dict):
        return
    _POLL_COUNTER += 1

    snaps = {
        slot_label: (render_snap_by_slot or {}).get(slot_label)
        for slot_label in SLOT_LABELS
    }
    current_bases = {
        slot_label: _safe_int((snap or {}).get("base"))
        for slot_label, snap in snaps.items()
        if isinstance(snap, dict)
    }
    current_hp = {
        slot_label: _safe_int((snap or {}).get("cur"))
        for slot_label, snap in snaps.items()
        if isinstance(snap, dict)
    }
    roster_changed = current_bases != _LAST_BASES
    hp_dropped = any(
        slot_label in _LAST_HP
        and hp < _LAST_HP[slot_label]
        for slot_label, hp in current_hp.items()
    )
    missing_state = any(
        base and slot_label not in _STATE_CACHE
        for slot_label, base in current_bases.items()
    )
    # The combo card only needed state on hit, but the standalone damage badge
    # must also notice Roll puddles, Baroque activation, and team corrections
    # while both fighters are idle. Poll every four manager writes to keep it
    # responsive without turning the HUD data path into a full memory scan.
    periodic_refresh = (_POLL_COUNTER % 4) == 0
    refresh = roster_changed or hp_dropped or missing_state or periodic_refresh

    if refresh:
        corrections = _team_corrections()
        for slot_label, snap in snaps.items():
            if not isinstance(snap, dict):
                _STATE_CACHE.pop(slot_label, None)
                continue
            team = "P1" if slot_label.startswith("P1") else "P2"
            _STATE_CACHE[slot_label] = _snapshot_damage_state(
                slot_label,
                snap,
                team_correction=corrections.get(team, 1.0),
            )

    _LAST_BASES.clear()
    _LAST_BASES.update(current_bases)
    _LAST_HP.clear()
    _LAST_HP.update(current_hp)

    for slot_label in SLOT_LABELS:
        row = payload.get(slot_label)
        snap = snaps.get(slot_label)
        if not isinstance(row, dict) or not isinstance(snap, dict):
            continue
        row["id"] = snap.get("id")
        row["base"] = snap.get("base")
        row.update(_STATE_CACHE.get(slot_label) or {
            "damage_live": False,
            "damage_combo_count": 0,
            "damage_combo_scale": 1.0,
            "damage_point_active": False,
            "damage_is_point": False,
            "damage_combo_lane_active": False,
            "damage_baroque_permission": 0,
            "damage_baroque_active": False,
            "damage_baroque_red_spent": 0,
            "damage_roll_power_flags": 0,
            "damage_roll_puddle_stacks": 0,
            "damage_height": 0.0,
            "damage_script_state_active": False,
            "damage_team_correction": 1.0,
        })

    for slot_label in SLOT_LABELS:
        row = payload.get(slot_label)
        if not isinstance(row, dict):
            continue
        team = "P1" if slot_label.startswith("P1") else "P2"
        other_label = f"{team}-C2" if slot_label.endswith("C1") else f"{team}-C1"
        other = payload.get(other_label)
        same_fighter = (
            isinstance(other, dict)
            and _safe_int(other.get("base"))
            and _safe_int(other.get("base")) == _safe_int(row.get("base"))
        )
        row["damage_teammate_ko"] = bool(
            isinstance(other, dict)
            and not same_fighter
            and _safe_int(other.get("max")) > 0
            and _safe_int(other.get("cur")) <= 0
        )

def _hp_ratio(snapshot: dict, *, add_damage: int = 0) -> float:
    maximum = max(1, _safe_int(snapshot.get("max"), 1))
    current = max(
        0,
        _safe_int(snapshot.get("cur")) + max(0, _safe_int(add_damage)),
    )
    return min(1.0, current / float(maximum))


def _victim_guts(snapshot: dict, damage: int) -> float:
    ratio = _hp_ratio(snapshot, add_damage=damage)
    if ratio <= 0.125:
        return 0.70
    if ratio <= 0.25:
        return 0.80
    return 1.0


def _attacker_low_health(snapshot: dict) -> float:
    ratio = _hp_ratio(snapshot)
    if ratio <= 0.125:
        return 1.125
    if ratio <= 0.25:
        return 1.075
    return 1.0


def _baroque_modifier(snapshot: dict) -> float | None:
    if not bool(snapshot.get("damage_baroque_active")):
        return None
    spent = max(0, _safe_int(snapshot.get("damage_baroque_red_spent")))
    maximum = max(1, _safe_int(snapshot.get("max"), 1))
    if spent < maximum / 10.0:
        return 0.75
    return 0.30 + 0.14 * (spent / 1000.0)


def _team_correction_label(value: float) -> str | None:
    known = (
        (1.0, None),
        (0.80, "VAR 80%"),
        (0.70, "DHC1 70%"),
        (0.49, "DHC2 49%"),
        (0.75, "START 75%"),
        (0.5625, "START2 56.25%"),
    )
    for expected, label in known:
        if abs(value - expected) <= 0.0025:
            return label
    if 0.0 < value < 4.0:
        return f"TEAM {value * 100.0:.1f}%"
    return None


def _percent_label(prefix: str, value: float, decimals: int = 0) -> str:
    return f"{prefix} {value * 100.0:.{decimals}f}%"


def _pack_chips(
    chips: list[str],
    limit: int = 43,
    max_lines: int = 4,
) -> list[str]:
    lines: list[str] = []
    current = ""
    for chip in chips:
        chip = str(chip or "").strip()
        if not chip:
            continue
        candidate = chip if not current else f"{current}  |  {chip}"
        if current and len(candidate) > limit:
            lines.append(current)
            current = chip
            if len(lines) >= max_lines:
                break
        else:
            current = candidate
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines


def _team_owner_slot(attacker_slot: str) -> str:
    team = "P1" if str(attacker_slot).startswith("P1") else "P2"
    return f"{team}-C1"


def build_damage_breakdown_lines(
    display_slots: dict,
    attacker_slot: str,
    victim_slot: str,
    damage: int,
    *,
    victim_is_point: bool,
    owner_slot: str | None = None,
) -> list[str]:
    """Build compact per-hit modifier rows for the combo card.

    The native damage routine resolves both team members to one team owner for
    combo scaling and script state, while Roll, Baroque, health bonuses, and
    other character-specific stages read the actual attacking fighter.
    """
    attacker = dict((display_slots or {}).get(attacker_slot) or {})
    victim = dict((display_slots or {}).get(victim_slot) or {})
    owner_label = owner_slot or _team_owner_slot(attacker_slot)
    owner = dict((display_slots or {}).get(owner_label) or attacker)
    chips: list[str] = []

    if bool(owner.get("damage_live")):
        combo_scale = _safe_float(owner.get("damage_combo_scale"), 1.0)
        chips.append(_percent_label("SCALE", combo_scale))

    chips.append(
        "POINT TRACK 5%→35%"
        if victim_is_point
        else "ASSIST TRACK 3%→43%"
    )

    char_id = _safe_int(attacker.get("id"), -1)
    if char_id == ROLL_ID:
        roll_flags = _safe_int(attacker.get("damage_roll_power_flags"))
        puddles = max(
            0,
            min(5, _safe_int(attacker.get("damage_roll_puddle_stacks"))),
        )
        if roll_flags & 1:
            chips.append("ROLL POWER 110%")
        if puddles:
            chips.append(f"PUDDLES ×{puddles} {100 + puddles * 10}%")

    if bool(owner.get("damage_script_state_active")):
        chips.append("SCRIPT MOD")

    baroque = _baroque_modifier(attacker)
    if baroque is not None:
        chips.append(_percent_label("BAROQUE", baroque, 1))

    team_label = _team_correction_label(
        _safe_float(owner.get("damage_team_correction"), 1.0)
    )
    if team_label:
        chips.append(team_label)

    height = _safe_float(victim.get("damage_height"), 0.0)
    if height > 0.01:
        chips.append(_percent_label("HEIGHT", 1.0 + 0.05 * height, 1))

    guts = _victim_guts(victim, damage)
    if guts < 0.999:
        chips.append(_percent_label("GUTS", guts))

    if (
        bool(attacker.get("damage_teammate_ko"))
        and char_id not in GIANT_IDS
    ):
        chips.append("LAST 101.25%")

    danger = _attacker_low_health(attacker)
    if danger > 1.001:
        chips.append(_percent_label("DANGER", danger, 1))

    return _pack_chips(chips)



def build_live_damage_modifier(
    display_slots: dict,
    attacker_slot: str,
    victim_slot: str,
    *,
    owner_slot: str | None = None,
) -> dict:
    """Return the current outgoing modifier for one live point fighter.

    Combo scale and script state come from the team owner object. Character
    buffs, Baroque, last-character bonus, and danger bonus come from the live
    attacking fighter. This matches the native register flow at 0x801404DC.
    """
    attacker = dict((display_slots or {}).get(attacker_slot) or {})
    victim = dict((display_slots or {}).get(victim_slot) or {})
    owner_label = owner_slot or _team_owner_slot(attacker_slot)
    owner = dict((display_slots or {}).get(owner_label) or attacker)
    multiplier = 1.0
    factors: list[str] = []
    approximate = False

    combo_scale = _safe_float(owner.get("damage_combo_scale"), 1.0)
    if combo_scale <= 0.0 or combo_scale > 4.0:
        combo_scale = 1.0
    multiplier *= combo_scale
    if abs(combo_scale - 1.0) > 0.0025:
        factors.append(_percent_label("TEAM SCALE", combo_scale))

    char_id = _safe_int(attacker.get("id"), -1)
    if char_id == ROLL_ID:
        roll_flags = _safe_int(attacker.get("damage_roll_power_flags"))
        puddles = max(
            0,
            min(5, _safe_int(attacker.get("damage_roll_puddle_stacks"))),
        )
        if roll_flags & 1:
            multiplier *= 1.10
            factors.append("ROLL 110%")
        if puddles:
            puddle_modifier = 1.0 + 0.10 * puddles
            multiplier *= puddle_modifier
            factors.append(f"PUDDLES {puddle_modifier * 100.0:.0f}%")

    if bool(owner.get("damage_script_state_active")):
        factors.append("SCRIPT ?")
        approximate = True

    baroque = _baroque_modifier(attacker)
    if baroque is not None:
        multiplier *= baroque
        factors.append(_percent_label("BAROQUE", baroque, 1))
        approximate = True

    team_correction = _safe_float(
        owner.get("damage_team_correction"),
        1.0,
    )
    if team_correction <= 0.0 or team_correction > 4.0:
        team_correction = 1.0
    multiplier *= team_correction
    team_label = _team_correction_label(team_correction)
    if team_label:
        factors.append(team_label)
        approximate = True

    height = _safe_float(victim.get("damage_height"), 0.0)
    if height > 0.01:
        height_modifier = 1.0 + 0.05 * height
        multiplier *= height_modifier
        factors.append(_percent_label("HEIGHT", height_modifier, 1))
        approximate = True

    guts = _victim_guts(victim, 0)
    multiplier *= guts
    if guts < 0.999:
        factors.append(_percent_label("GUTS", guts))

    if (
        bool(attacker.get("damage_teammate_ko"))
        and char_id not in GIANT_IDS
    ):
        multiplier *= 1.0125
        factors.append("LAST 101.25%")

    danger = _attacker_low_health(attacker)
    multiplier *= danger
    if danger > 1.001:
        factors.append(_percent_label("DANGER", danger, 1))

    multiplier = max(0.0, min(9.999, multiplier))
    return {
        "multiplier": multiplier,
        "percent": multiplier * 100.0,
        "factors": factors or ["BASE"],
        "approximate": approximate,
        "live": bool(attacker.get("damage_live", False)) and bool(owner.get("damage_live", False)),
        "attacker_slot": attacker_slot,
        "owner_slot": owner_label,
    }

