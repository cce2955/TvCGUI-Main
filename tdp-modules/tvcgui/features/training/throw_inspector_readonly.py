"""Read-only TvC throw packet and live descriptor inspector.

This module only reads Dolphin memory. It never patches game code and never
writes emulated memory.

Recomp mapping (2026-08-11):
    fighter + 0x198C -> current action packet table
    packet stride     -> 0x1C bytes, up to 90 entries

Authored throw entry fields:
    +0x00 flags
    +0x04 throw action id
    +0x08 active lifetime in frames
    +0x14 authored range float (effective range = value * 0.01)

Live throw descriptor:
    +0x20DC flags
    +0x20E0 thrower action
    +0x20E4 victim action
    +0x20E8 active frames remaining
    +0x20EC effective range
    +0x20F0 source packet index / priority
"""
from __future__ import annotations

import math
import struct
from typing import Any

from tvcgui.core.constants import ATT_ID_OFF_PRIMARY, CHAR_NAMES, OFF_CHAR_ID, SLOTS
from tvcgui.platform.dolphin import addr_in_ram, rd32, rdf32, rbytes
from tvcgui.tools.scanners.fighter_resolver import RESOLVER

ACTION_PACKET_TABLE_OFF = 0x198C
ACTION_PACKET_ENTRY_SIZE = 0x1C
ACTION_PACKET_MAX_ENTRIES = 90

ENTRY_FLAGS_OFF = 0x00
ENTRY_THROW_ACTION_OFF = 0x04
ENTRY_ACTIVE_FRAMES_OFF = 0x08
ENTRY_RANGE_RAW_OFF = 0x14

LIVE_FLAGS_OFF = 0x20DC
LIVE_THROWER_ACTION_OFF = 0x20E0
LIVE_VICTIM_ACTION_OFF = 0x20E4
LIVE_ACTIVE_FRAMES_OFF = 0x20E8
LIVE_RANGE_OFF = 0x20EC
LIVE_PACKET_SLOT_OFF = 0x20F0

LINKED_VICTIM_OFF = 0x43E8
LINKED_THROWER_OFF = 0x43EC
REACTION_SOURCE_OFF = 0x43F4

THROW_GROUND = 0x00000080
THROW_AIR = 0x00000100
TARGET_STANDING = 0x00000200
TARGET_CROUCHING = 0x00000400
TARGET_AIRBORNE = 0x00000800
FAILED_CAPTURE_ACTION = 0x00004000
CONTACT_CAPTURE = 0x00008000

THROW_CONTEXT_MASK = THROW_GROUND | THROW_AIR
THROW_TARGET_MASK = TARGET_STANDING | TARGET_CROUCHING | TARGET_AIRBORNE
THROW_RELEVANT_MASK = THROW_CONTEXT_MASK | THROW_TARGET_MASK | FAILED_CAPTURE_ACTION | CONTACT_CAPTURE


def _u32(value: Any) -> int:
    try:
        return int(value) & 0xFFFFFFFF
    except Exception:
        return 0


def _s32(value: Any) -> int:
    raw = _u32(value)
    return raw - 0x100000000 if raw & 0x80000000 else raw


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    if not math.isfinite(out):
        return None
    return out


def decode_throw_flags(value: Any) -> dict[str, Any]:
    """Decode only the throw bits proven by the recomp trace."""
    flags = _u32(value)
    contexts: list[str] = []
    if flags & THROW_GROUND:
        contexts.append("Ground")
    if flags & THROW_AIR:
        contexts.append("Air")

    targets: list[str] = []
    if flags & TARGET_STANDING:
        targets.append("Standing")
    if flags & TARGET_CROUCHING:
        targets.append("Crouching")
    if flags & TARGET_AIRBORNE:
        targets.append("Airborne")

    return {
        "flags": flags,
        "flags_hex": f"0x{flags:08X}",
        "context": " / ".join(contexts) if contexts else "-",
        "contexts": contexts,
        "targets": targets,
        "targets_text": " / ".join(targets) if targets else "-",
        "failed_capture_action": bool(flags & FAILED_CAPTURE_ACTION),
        "contact_capture": bool(flags & CONTACT_CAPTURE),
        "authored_proximity_throw": bool(flags & THROW_CONTEXT_MASK),
        "throw_relevant": bool(flags & THROW_RELEVANT_MASK),
        "unknown_flags": flags & ~THROW_RELEVANT_MASK,
    }


def read_throw_entries(fighter_base: int) -> dict[str, Any]:
    """Read the current action's authored proximity-throw entries for one fighter.

    The packet array is fetched in one bulk read so the inspector does not turn
    a 90-entry scan into hundreds of cross-process reads per refresh.
    """
    base = _u32(fighter_base)
    if not addr_in_ram(base):
        return {"ok": False, "reason": "invalid fighter base", "fighter_base": base, "entries": []}

    table = _u32(rd32(base + ACTION_PACKET_TABLE_OFF))
    if not addr_in_ram(table):
        return {
            "ok": False,
            "reason": "no current action packet table",
            "fighter_base": base,
            "packet_table": table,
            "entries": [],
        }

    wanted = ACTION_PACKET_MAX_ENTRIES * ACTION_PACKET_ENTRY_SIZE
    blob = rbytes(table, wanted)
    if not blob or len(blob) < ACTION_PACKET_ENTRY_SIZE:
        return {
            "ok": False,
            "reason": "packet table read failed",
            "fighter_base": base,
            "packet_table": table,
            "entries": [],
        }

    entries: list[dict[str, Any]] = []
    terminated = False
    scanned = 0
    available = min(ACTION_PACKET_MAX_ENTRIES, len(blob) // ACTION_PACKET_ENTRY_SIZE)
    for index in range(available):
        off = index * ACTION_PACKET_ENTRY_SIZE
        flags = struct.unpack_from(">I", blob, off + ENTRY_FLAGS_OFF)[0]
        scanned = index + 1
        if flags == 0:
            terminated = True
            break

        decoded = decode_throw_flags(flags)
        # 0x200/0x400/0x800 only have throw-target meaning inside an entry
        # activated by the proven 0x80/0x100 proximity-throw paths.
        if not decoded["authored_proximity_throw"]:
            continue

        action_raw = struct.unpack_from(">I", blob, off + ENTRY_THROW_ACTION_OFF)[0]
        frames_raw = struct.unpack_from(">I", blob, off + ENTRY_ACTIVE_FRAMES_OFF)[0]
        try:
            range_raw = _finite(struct.unpack_from(">f", blob, off + ENTRY_RANGE_RAW_OFF)[0])
        except Exception:
            range_raw = None
        effective_range = None if range_raw is None else range_raw * 0.01
        packet = table + off

        entries.append({
            "index": index,
            "address": packet,
            "address_hex": f"0x{packet:08X}",
            **decoded,
            "throw_action": _s32(action_raw),
            "active_frames": _s32(frames_raw),
            "range_raw": range_raw,
            "range_effective": effective_range,
        })

    return {
        "ok": True,
        "reason": "",
        "fighter_base": base,
        "fighter_base_hex": f"0x{base:08X}",
        "packet_table": table,
        "packet_table_hex": f"0x{table:08X}",
        "entries": entries,
        "scanned_entries": scanned,
        "terminated": terminated,
    }


def read_live_throw_descriptor(fighter_base: int) -> dict[str, Any]:
    """Read the live resolved throw descriptor and relationship pointers."""
    base = _u32(fighter_base)
    if not addr_in_ram(base):
        return {"ok": False, "reason": "invalid fighter base", "fighter_base": base}

    flags = _u32(rd32(base + LIVE_FLAGS_OFF))
    decoded = decode_throw_flags(flags)
    thrower_action = _s32(rd32(base + LIVE_THROWER_ACTION_OFF))
    victim_action = _s32(rd32(base + LIVE_VICTIM_ACTION_OFF))
    active_frames = _s32(rd32(base + LIVE_ACTIVE_FRAMES_OFF))
    packet_slot = _s32(rd32(base + LIVE_PACKET_SLOT_OFF))
    live_range = _finite(rdf32(base + LIVE_RANGE_OFF))

    linked_victim = _u32(rd32(base + LINKED_VICTIM_OFF))
    linked_thrower = _u32(rd32(base + LINKED_THROWER_OFF))
    reaction_source = _u32(rd32(base + REACTION_SOURCE_OFF))

    descriptor_valid = thrower_action >= 0 and (
        packet_slot >= 0 or decoded["contact_capture"]
    )

    return {
        "ok": True,
        **decoded,
        "thrower_action": thrower_action,
        "victim_action": victim_action,
        "active_frames_remaining": active_frames,
        "range_effective": live_range,
        "packet_slot": packet_slot,
        "descriptor_valid": descriptor_valid,
        "linked_victim": linked_victim if addr_in_ram(linked_victim) else 0,
        "linked_thrower": linked_thrower if addr_in_ram(linked_thrower) else 0,
        "reaction_source": reaction_source if addr_in_ram(reaction_source) else 0,
    }


def read_slot_throw_snapshot(slot_label: str, slot_ptr: int) -> dict[str, Any]:
    """Resolve one roster slot and return authored + live throw information."""
    base, _changed = RESOLVER.resolve_base(int(slot_ptr))
    if not base or not addr_in_ram(base):
        return {
            "slot": str(slot_label),
            "slot_ptr": int(slot_ptr),
            "fighter_base": 0,
            "character_id": 0,
            "character": "-",
            "connected": False,
            "authored": {"ok": False, "entries": [], "reason": "fighter unavailable"},
            "live": {"ok": False, "reason": "fighter unavailable"},
        }

    char_id = _s32(rd32(base + OFF_CHAR_ID))
    action_id = _s32(rd32(base + ATT_ID_OFF_PRIMARY))
    return {
        "slot": str(slot_label),
        "slot_ptr": int(slot_ptr),
        "fighter_base": int(base),
        "fighter_base_hex": f"0x{int(base):08X}",
        "character_id": char_id,
        "character": CHAR_NAMES.get(char_id, f"Character {char_id}"),
        "action_id": action_id,
        "connected": True,
        "authored": read_throw_entries(int(base)),
        "live": read_live_throw_descriptor(int(base)),
    }


def read_named_slot_throw_snapshot(slot_label: str) -> dict[str, Any]:
    """Resolve one named normal roster slot without scanning the other three."""
    wanted = str(slot_label or "").upper()
    for label, ptr, _team in SLOTS:
        if str(label).upper() == wanted:
            return read_slot_throw_snapshot(label, ptr)
    return {
        "slot": str(slot_label),
        "connected": False,
        "fighter_base": 0,
        "character": "-",
        "authored": {"ok": False, "entries": [], "reason": "unknown slot"},
        "live": {"ok": False, "reason": "unknown slot"},
    }


def read_all_throw_snapshots() -> dict[str, dict[str, Any]]:
    """Return throw snapshots for the four normal fighter slots."""
    return {
        slot_label: read_slot_throw_snapshot(slot_label, slot_ptr)
        for slot_label, slot_ptr, _team in SLOTS
    }


__all__ = [
    "ACTION_PACKET_TABLE_OFF",
    "ACTION_PACKET_ENTRY_SIZE",
    "ACTION_PACKET_MAX_ENTRIES",
    "THROW_GROUND",
    "THROW_AIR",
    "TARGET_STANDING",
    "TARGET_CROUCHING",
    "TARGET_AIRBORNE",
    "FAILED_CAPTURE_ACTION",
    "CONTACT_CAPTURE",
    "decode_throw_flags",
    "read_throw_entries",
    "read_live_throw_descriptor",
    "read_slot_throw_snapshot",
    "read_named_slot_throw_snapshot",
    "read_all_throw_snapshots",
]
