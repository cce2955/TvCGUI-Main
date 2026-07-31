"""Live move-definition profiler for TvC attack properties.

The collision packet exists only while the engine is resolving an active attack.
The supplied live dumps show the owner lists empty and all 128 collision records
back on the free list immediately after the move, so an external GUI poll cannot
reliably present those transient records.

The normal application path therefore follows each fighter's current action ID,
resolves that action through the native character move table, and reads the same
packed Property A byte used by the frame-data scanner. The latest resolved move is
latched after the fighter returns to idle and is explicitly labeled LAST MOVE.
Whiffed moves update too because this path follows the action, not contact.

Native resolver capture is available as an explicit opt-in. The entry hook captures
packet fields while the exit hook captures the finalized damage written through the
native output pointers. The application remains read-only unless the opt-in is set.
"""
from __future__ import annotations

import csv
import json
import os
import struct
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

try:
    from tvcgui.platform.dolphin import addr_in_ram, rd32, rbytes, wbytes
except Exception:
    def addr_in_ram(_addr: int) -> bool:
        return False

    def rd32(_addr: int):
        return None

    def rbytes(_addr: int, _size: int):
        return b""

    def wbytes(_addr: int, _payload: bytes):
        return False

from tvcgui.core.constants import CHAR_NAMES

try:
    from tvcgui.features.combat.move_id_map import lookup_move_name
except Exception:
    def lookup_move_name(_action_id, _char_id=None):
        return None
from tvcgui.core.paths import user_data_path
from tvcgui.runtime.deferred_work import DeferredWorkLoop

try:
    from tvcgui.features.frame_data.attack_property_runtime import (
        collect_live_projectile_properties,
        resolve_live_attack_definition,
        resolve_live_attack_property,
    )
except Exception:
    def collect_live_projectile_properties(_owner_bases, **_kwargs):
        return []

    def resolve_live_attack_definition(_fighter_base_abs: int, _action_id: int, **_kwargs):
        return {"status": "IMPORT_FAILED"}

    def resolve_live_attack_property(_fighter_base_abs: int, _action_id: int, **_kwargs):
        return None

PROFILE_VERSION = 18
PROFILE_FILE = "runtime_attack_property_profiles.json"
EVENT_FILE = "runtime_attack_property_events.csv"
SAMPLING_MODE = "recomp_action_script_plus_spawned_attack_actors_native_damage_opt_in_v18"
PROJECTILE_LATCH_FRAMES = 120
ATTACK_ACTOR_LATCH_FRAMES = PROJECTILE_LATCH_FRAMES

# The native owner list is a circular sentinel list embedded in each fighter.
OWNER_LIST_OFFSET = 0x4350
LIST_NEXT_OFFSET = 0x08
LIST_NODE_OFFSET = 0x04
MAX_ACTORS_PER_OWNER = 128

# Native fixed-pool manager created by 0x8007BF68. r13 is initialized to
# 0x80567BA0 and the manager pointer lives at r13-0x6A80 = 0x80561120.
# 0x8014FCEC rounds the 5436-byte allocation request up to 0x1540 bytes and
# adds a four-byte free-list header, producing a 0x1544-byte slot stride.
ATTACK_POOL_MANAGER_PTR_ADDR = 0x80561120
POOL_MANAGER_META_SIZE = 0x10
POOL_HEAD_OFFSET = 0x00
POOL_FIRST_NODE_OFFSET = 0x04
POOL_COUNT_OFFSET = 0x08
POOL_PAYLOAD_SIZE_OFFSET = 0x0C
POOL_STORAGE_OFFSET = 0x10
POOL_EXPECTED_COUNT = 128
POOL_EXPECTED_PAYLOAD_SIZE = 0x1540
POOL_NODE_HEADER_SIZE = 0x04
POOL_EXPECTED_STRIDE = POOL_EXPECTED_PAYLOAD_SIZE + POOL_NODE_HEADER_SIZE

# Native damage capture uses four small hooks.
#
# 1. 0x801404DC captures the native damage-calculator inputs.
# 2. 0x80140A78 captures the calculator outputs through the saved r7/r8 pointers.
# 3. 0x8004E8A8 marks which calculator event belongs to the collision resolver.
# 4. 0x8004F74C captures r1+0x14 immediately before the HP subtraction call.
#
# The fourth value is the post-route damage after the collision resolver has
# applied chip and other Property-B-dependent corrections. It is therefore the
# preferred native value for contact attribution. The 0x801404DC output remains
# available separately as damage_calc_output.
RESOLVER_HOOK_ADDR = 0x801404DC
RESOLVER_HOOK_ORIGINAL = 0x9421FF80
RESOLVER_CAVE_ADDR = 0x817F0000
RESOLVER_EXIT_HOOK_ADDR = 0x80140A78
RESOLVER_EXIT_HOOK_ORIGINAL = 0x39610060  # addi r11,r1,96
RESOLVER_EXIT_CAVE_ADDR = 0x817F0200
RESOLVER_COLLISION_RETURN_HOOK_ADDR = 0x8004E8A8
RESOLVER_COLLISION_RETURN_HOOK_ORIGINAL = 0x38600005  # li r3,5
RESOLVER_COLLISION_RETURN_CAVE_ADDR = 0x817F0400
RESOLVER_APPLY_HOOK_ADDR = 0x8004F74C
RESOLVER_APPLY_HOOK_ORIGINAL = 0x80010014  # lwz r0,20(r1)
RESOLVER_APPLY_CAVE_ADDR = 0x817F0600
RESOLVER_COLLISION_CALLER_LR = 0x8004E8A8
RESOLVER_MAILBOX_ADDR = 0x817F1000
RESOLVER_MAILBOX_MAGIC = 0x41544B36  # "ATK6"
RESOLVER_MAILBOX_LEGACY_MAGICS = {0x41544B34, 0x41544B35}  # "ATK4", "ATK5"
RESOLVER_MAILBOX_VERSION = 6
RESOLVER_MAILBOX_HEADER_SIZE = 0x20
RESOLVER_HEADER_PENDING_SEQUENCE = 0x14
RESOLVER_HEADER_COLLISION_SEQUENCE = 0x18
RESOLVER_RING_COUNT = 32
RESOLVER_ENTRY_SIZE = 0x60
RESOLVER_MAILBOX_SIZE = RESOLVER_MAILBOX_HEADER_SIZE + RESOLVER_RING_COUNT * RESOLVER_ENTRY_SIZE
RESOLVER_RETRY_SEC = 0.50
RESOLVER_APPLICATION_DEFER_SEC = 0.050

# Ring entry layout. r3/r4 are attacker/defender, r5/r6 are property B/A, and
# r10 is the packet sub-structure at actor+0x64. The damage routine saves the
# original r7/r8 output pointers in r26/r27. The collision resolver later mutates
# the primary output in its stack local before applying it to HP, so both stages
# are retained instead of conflated.
RES_OFF_SEQUENCE = 0x00
RES_OFF_ATTACKER = 0x04
RES_OFF_DEFENDER = 0x08
RES_OFF_PROPERTY_B = 0x0C
RES_OFF_PROPERTY_A = 0x10
RES_OFF_RESULT_PTR_A = 0x14
RES_OFF_RESULT_PTR_B = 0x18
RES_OFF_ROUTE_ARG = 0x1C
RES_OFF_PACKET = 0x20
RES_OFF_CALLER_LR = 0x24
RES_OFF_AUTHORED_DAMAGE = 0x28
RES_OFF_BASE_DAMAGE = RES_OFF_AUTHORED_DAMAGE  # compatibility alias
RES_OFF_PHASE_A = 0x2C
RES_OFF_PHASE_B = 0x30
RES_OFF_RUNTIME_STATUS = 0x34
RES_OFF_PACKET_OWNER = 0x38
RES_OFF_ACTION_ID = 0x3C
RES_OFF_DAMAGE_CALC_OUTPUT = 0x40
RES_OFF_DAMAGE_CALC_AUX = 0x44
RES_OFF_CALC_COMPLETION_SEQUENCE = 0x48
RES_OFF_APPLIED_DAMAGE = 0x4C
RES_OFF_APPLICATION_SEQUENCE = 0x50
RES_OFF_RESERVED_54 = 0x54
RES_OFF_RESERVED_58 = 0x58
RES_OFF_RESERVED_5C = 0x5C
# Compatibility aliases used by older callers/tests.
RES_OFF_RESOLVED_DAMAGE = RES_OFF_APPLIED_DAMAGE
RES_OFF_RESOLVED_AUX = RES_OFF_DAMAGE_CALC_AUX
RES_OFF_COMPLETION_SEQUENCE = RES_OFF_CALC_COMPLETION_SEQUENCE

# Fighter metadata read directly by the sampler.
FIGHTER_META_START = 0x0014
FIGHTER_CHAR_ID = 0x0014
FIGHTER_ACTION_FRAME = 0x01D8
FIGHTER_ACTION_ID = 0x01E8
FIGHTER_META_END = FIGHTER_ACTION_ID + 4
FIGHTER_META_SIZE = FIGHTER_META_END - FIGHTER_META_START

# The adjacent role words are read separately so idle polling remains cheap.
# +0x44A0 selects the stable point character. +0x44A4 is normally synchronized
# with that role, but native move/transition logic may change it independently.
# The combo helper at 0x8013C398 uses +0x44A4 to select its proration lane.
FIGHTER_ROLE_START = 0x44A0
FIGHTER_POINT_ACTIVE = 0x44A0
FIGHTER_COMBO_LANE_ACTIVE = 0x44A4
FIGHTER_ROLE_END = FIGHTER_COMBO_LANE_ACTIVE + 4
FIGHTER_ROLE_SIZE = FIGHTER_ROLE_END - FIGHTER_ROLE_START

# Attack actor range read in one coherent block. The list node starts at +0x04.
ACTOR_BLOCK_START = 0x0004
ACTOR_BLOCK_END = 0x0364
ACTOR_BLOCK_SIZE = ACTOR_BLOCK_END - ACTOR_BLOCK_START

OFF_LIST_PREV = 0x0008
OFF_LIST_NEXT = 0x000C
OFF_OBJECT_FLAGS_10 = 0x0010
OFF_OBJECT_FLAGS_14 = 0x0014
OFF_OBJECT_FLAGS_18 = 0x0018
OFF_OBJECT_FLAGS_1C = 0x001C
OFF_RUNTIME_STATUS_20 = 0x0020
OFF_OBJECT_FLAGS_24 = 0x0024
OFF_OBJECT_FLAGS_28 = 0x0028
OFF_OBJECT_FLAGS_2C = 0x002C
OFF_OWNER = 0x0030
OFF_VICTIM = 0x0034
OFF_PROPERTY_A = 0x0080
OFF_PROPERTY_B = 0x0084
OFF_BASE_DAMAGE = 0x008C
OFF_PHASE_PROPERTY_A = 0x035C
OFF_PHASE_PROPERTY_B = 0x0360

DEFAULT_POLL_HZ = 240.0
MAX_EVENT_HISTORY = 20000
WRITE_INTERVAL_SEC = 0.75

PROPERTY_A_GUARD_BITS = {
    0x08: "Mid",
    0x10: "High",
    0x20: "Low",
}
PROPERTY_A_STRENGTH_BITS = {
    0x01: "Light",
    0x02: "Medium",
    0x04: "Heavy",
}

# Exact high/result words confirmed or strongly correlated through controlled
# cross-character comparisons. Keep composite words intact. Do not infer that
# an individual sub-bit carries the full meaning of a composite result.
PROPERTY_A_RESULT_FLAG_LABELS = {
    0x00000100: "Launch / soft-knockdown route",
    0x00000200: "Hard knockdown",
    0x00000300: "Spiral knockdown",
    0x00000400: "Sweep",
    0x00000800: "Stagger",
    0x00001000: "Capture / throw connection",
    0x00001800: "Capture + stagger",
    0x00004000: "OTG enabled",
    0x00004100: "OTG + launch / soft-knockdown",
    0x00004200: "OTG + hard knockdown",
    0x00008000: "Wall bounce (powered Roll Swing observed)",
    0x00008200: "Hard knockdown (Roll Swing family)",
    0x00008300: "Exact reaction 0x00008300 (unresolved)",
    0x00008800: "Exact reaction 0x00008800 (unresolved)",
    0x00010000: "Airborne soft-knockdown override (provisional)",
    0x00010100: "Conditional reaction composite (unresolved)",
    0x00020000: "Special reaction component (unresolved)",
    0x00024000: "Special reaction component + OTG",
    0x00040000: "Launcher",
    0x00080000: "Air knockdown",
    0x00100100: "Stabilized soft knockdown",
    0x00110000: "Repeated-juggle route (correlated)",
    0x00201000: "Cinematic impact / transition",
    0x00420000: "Megacrash blowback",
    0x00300100: "Cinematic + launch",
    0x00301000: "Cinematic + capture",
    0x00301100: "Cinematic + capture + launch",
    0x80000080: "Crumple",
    0x80000200: "Wall-interaction hard knockdown",
    0x80008200: "Powered/charged wall-bounce reaction",
    0x80000800: "Stagger + forced turnaround",
    0x80004200: "OTG + hard knockdown + unresolved high modifier",
    0x80080000: "Special air-knockdown composite",
}

# Mechanical labels are promoted only where the recomp or controlled live
# transitions isolate the individual bit. Route-family guesses remain marked
# correlated so an exact composite packet can still be interpreted cautiously.
PROPERTY_B_CONFIRMED_BITS = {
    0x00000001: "Native result modifier +0004",
    0x00000020: "Sustained-contact actor route",
    0x00000040: "Standard-strike baseline",
    0x00000100: "Initial actor phase",
    0x00040000: "Special chip route B, one-eighth damage",
    0x00400000: "Ground capture route core",
    0x01000000: "Repeat-contact handling",
    0x40000000: "Result propagation modifier +1",
}
PROPERTY_B_OBSERVED_BITS = {
    0x00000002: "Spawned-attack core flag (correlated)",
    0x00000008: "Alternate normal/contact route 0x00000008 (unresolved)",
    0x00000010: "Target-acquired / contact-lock state (correlated)",
    0x00000080: "Spawned-actor contact family 0x80 (correlated)",
    0x00080000: "Capture/cinematic modifier 0x00080000 (correlated)",
    0x00100000: "Persistent/paired actor route 0x00100000 (correlated)",
}
PROPERTY_B_CORRELATED_BITS = {}


CSV_FIELDS = [
    "timestamp_utc",
    "gui_frame",
    "event_sequence",
    "caller_lr",
    "route_arg",
    "result_ptr_a",
    "result_ptr_b",
    "packet_source",
    "packet_live",
    "pool_manager",
    "pool_free_head",
    "actor",
    "owner_slot",
    "owner_base",
    "owner_char_id",
    "owner_name",
    "owner_action_id",
    "owner_action_name",
    "owner_action_frame",
    "owner_point_active",
    "owner_combo_lane_active",
    "scaling_loss_per_hit",
    "scaling_floor",
    "scaling_track_text",
    "victim_slot",
    "victim_base",
    "victim_char_id",
    "victim_name",
    "property_a",
    "property_a_text",
    "property_b",
    "property_b_text",
    "runtime_status_20",
    "phase_property_a",
    "phase_property_a_text",
    "phase_property_b",
    "phase_property_b_text",
    "phase_property_b_unknown_mask",
    "base_damage",
    "authored_damage",
    "damage_calc_output",
    "damage_calc_aux",
    "native_damage_calc_complete",
    "applied_damage",
    "resolved_damage",
    "resolved_aux",
    "native_damage_complete",
    "object_flags_10",
    "object_flags_14",
    "object_flags_18",
    "object_flags_1c",
    "object_flags_24",
    "object_flags_28",
    "object_flags_2c",
]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _u32be(blob: bytes, absolute_offset: int, *, block_start: int = 0) -> int:
    rel = int(absolute_offset) - int(block_start)
    if rel < 0 or rel + 4 > len(blob):
        return 0
    return struct.unpack_from(">I", blob, rel)[0]


def _ppc_lis(rt: int, imm: int) -> int:
    return 0x3C000000 | ((int(rt) & 31) << 21) | (int(imm) & 0xFFFF)


def _ppc_ori(ra: int, rs: int, imm: int) -> int:
    return 0x60000000 | ((int(rs) & 31) << 21) | ((int(ra) & 31) << 16) | (int(imm) & 0xFFFF)


def _ppc_lwz(rt: int, ra: int, disp: int) -> int:
    return 0x80000000 | ((int(rt) & 31) << 21) | ((int(ra) & 31) << 16) | (int(disp) & 0xFFFF)


def _ppc_stw(rs: int, ra: int, disp: int) -> int:
    return 0x90000000 | ((int(rs) & 31) << 21) | ((int(ra) & 31) << 16) | (int(disp) & 0xFFFF)


def _ppc_addi(rt: int, ra: int, imm: int) -> int:
    return 0x38000000 | ((int(rt) & 31) << 21) | ((int(ra) & 31) << 16) | (int(imm) & 0xFFFF)


def _ppc_mulli(rt: int, ra: int, imm: int) -> int:
    return 0x1C000000 | ((int(rt) & 31) << 21) | ((int(ra) & 31) << 16) | (int(imm) & 0xFFFF)


def _ppc_rlwinm(ra: int, rs: int, sh: int, mb: int, me: int) -> int:
    return (
        0x54000000
        | ((int(rs) & 31) << 21)
        | ((int(ra) & 31) << 16)
        | ((int(sh) & 31) << 11)
        | ((int(mb) & 31) << 6)
        | ((int(me) & 31) << 1)
    )


def _ppc_add(rt: int, ra: int, rb: int) -> int:
    return 0x7C000214 | ((int(rt) & 31) << 21) | ((int(ra) & 31) << 16) | ((int(rb) & 31) << 11)


def _ppc_branch(source: int, target: int) -> int:
    displacement = int(target) - int(source)
    if displacement & 3 or not (-(1 << 25) <= displacement < (1 << 25)):
        raise ValueError(f"PPC branch out of range: 0x{int(source):08X} -> 0x{int(target):08X}")
    return 0x48000000 | (displacement & 0x03FFFFFC)


def resolver_hook_word() -> int:
    return _ppc_branch(RESOLVER_HOOK_ADDR, RESOLVER_CAVE_ADDR)


def resolver_exit_hook_word() -> int:
    return _ppc_branch(RESOLVER_EXIT_HOOK_ADDR, RESOLVER_EXIT_CAVE_ADDR)


def resolver_collision_return_hook_word() -> int:
    return _ppc_branch(
        RESOLVER_COLLISION_RETURN_HOOK_ADDR,
        RESOLVER_COLLISION_RETURN_CAVE_ADDR,
    )


def resolver_apply_hook_word() -> int:
    return _ppc_branch(RESOLVER_APPLY_HOOK_ADDR, RESOLVER_APPLY_CAVE_ADDR)


def resolver_stub_words() -> tuple[int, ...]:
    mailbox_hi = (RESOLVER_MAILBOX_ADDR >> 16) & 0xFFFF
    mailbox_lo = RESOLVER_MAILBOX_ADDR & 0xFFFF
    words = [
        RESOLVER_HOOK_ORIGINAL,                     # original stwu r1,-128(r1)
        _ppc_lis(11, mailbox_hi),
        _ppc_ori(11, 11, mailbox_lo),
        _ppc_lwz(12, 11, 0),                       # last committed sequence
        _ppc_addi(12, 12, 1),                      # reserve next sequence
        _ppc_stw(12, 11, RESOLVER_HEADER_PENDING_SEQUENCE),
        _ppc_rlwinm(0, 12, 0, 27, 31),             # sequence & 31
        _ppc_mulli(0, 0, RESOLVER_ENTRY_SIZE),
        _ppc_add(11, 11, 0),
        _ppc_addi(11, 11, RESOLVER_MAILBOX_HEADER_SIZE),
        _ppc_stw(12, 11, RES_OFF_SEQUENCE),
        _ppc_stw(3, 11, RES_OFF_ATTACKER),
        _ppc_stw(4, 11, RES_OFF_DEFENDER),
        _ppc_stw(5, 11, RES_OFF_PROPERTY_B),
        _ppc_stw(6, 11, RES_OFF_PROPERTY_A),
        _ppc_stw(7, 11, RES_OFF_RESULT_PTR_A),
        _ppc_stw(8, 11, RES_OFF_RESULT_PTR_B),
        _ppc_stw(9, 11, RES_OFF_ROUTE_ARG),
        _ppc_stw(10, 11, RES_OFF_PACKET),
        0x7C0802A6,                                # mflr r0
        _ppc_stw(0, 11, RES_OFF_CALLER_LR),
        _ppc_lwz(12, 10, 0x28),                    # packet authored damage
        _ppc_stw(12, 11, RES_OFF_AUTHORED_DAMAGE),
        _ppc_lwz(12, 10, 0x2F8),                   # actor+0x35C phase A
        _ppc_stw(12, 11, RES_OFF_PHASE_A),
        _ppc_lwz(12, 10, 0x2FC),                   # actor+0x360 phase B
        _ppc_stw(12, 11, RES_OFF_PHASE_B),
        _ppc_lwz(12, 10, -0x44),                   # actor+0x20 runtime status
        _ppc_stw(12, 11, RES_OFF_RUNTIME_STATUS),
        _ppc_lwz(12, 10, -0x34),                   # actor+0x30 packet owner
        _ppc_stw(12, 11, RES_OFF_PACKET_OWNER),
        _ppc_lwz(12, 3, FIGHTER_ACTION_ID),         # attacker action at contact
        _ppc_stw(12, 11, RES_OFF_ACTION_ID),
        0x39800000,                                # li r12,0; zero completion fields
        _ppc_stw(12, 11, RES_OFF_DAMAGE_CALC_OUTPUT),
        _ppc_stw(12, 11, RES_OFF_DAMAGE_CALC_AUX),
        _ppc_stw(12, 11, RES_OFF_CALC_COMPLETION_SEQUENCE),
        _ppc_stw(12, 11, RES_OFF_APPLIED_DAMAGE),
        _ppc_stw(12, 11, RES_OFF_APPLICATION_SEQUENCE),
    ]
    branch_source = RESOLVER_CAVE_ADDR + len(words) * 4
    words.append(_ppc_branch(branch_source, RESOLVER_HOOK_ADDR + 4))
    return tuple(int(word) & 0xFFFFFFFF for word in words)


def resolver_stub_bytes() -> bytes:
    words = resolver_stub_words()
    return struct.pack(">" + "I" * len(words), *words)


def resolver_exit_stub_words() -> tuple[int, ...]:
    mailbox_hi = (RESOLVER_MAILBOX_ADDR >> 16) & 0xFFFF
    mailbox_lo = RESOLVER_MAILBOX_ADDR & 0xFFFF
    words = [
        _ppc_lis(11, mailbox_hi),
        _ppc_ori(11, 11, mailbox_lo),
        _ppc_lwz(12, 11, RESOLVER_HEADER_PENDING_SEQUENCE),
        _ppc_rlwinm(0, 12, 0, 27, 31),             # sequence & 31
        _ppc_mulli(0, 0, RESOLVER_ENTRY_SIZE),
        _ppc_add(11, 11, 0),
        _ppc_addi(11, 11, RESOLVER_MAILBOX_HEADER_SIZE),
        _ppc_lwz(0, 26, 0),                        # damage calculator output (*r7)
        _ppc_stw(0, 11, RES_OFF_DAMAGE_CALC_OUTPUT),
        _ppc_lwz(0, 27, 0),                        # auxiliary calculator output (*r8)
        _ppc_stw(0, 11, RES_OFF_DAMAGE_CALC_AUX),
        _ppc_stw(12, 11, RES_OFF_CALC_COMPLETION_SEQUENCE),
        _ppc_lis(11, mailbox_hi),
        _ppc_ori(11, 11, mailbox_lo),
        0x38000000,                                # li r0,0; reloaded by epilogue
        _ppc_stw(0, 11, RESOLVER_HEADER_PENDING_SEQUENCE),
        _ppc_stw(12, 11, 0),                       # commit after calculator output exists
        RESOLVER_EXIT_HOOK_ORIGINAL,                # original addi r11,r1,96
    ]
    branch_source = RESOLVER_EXIT_CAVE_ADDR + len(words) * 4
    words.append(_ppc_branch(branch_source, RESOLVER_EXIT_HOOK_ADDR + 4))
    return tuple(int(word) & 0xFFFFFFFF for word in words)


def resolver_exit_stub_bytes() -> bytes:
    words = resolver_exit_stub_words()
    return struct.pack(">" + "I" * len(words), *words)


def resolver_collision_return_stub_words() -> tuple[int, ...]:
    """Remember which committed calculator event belongs to 0x8004DFD4."""
    mailbox_hi = (RESOLVER_MAILBOX_ADDR >> 16) & 0xFFFF
    mailbox_lo = RESOLVER_MAILBOX_ADDR & 0xFFFF
    words = [
        _ppc_lis(11, mailbox_hi),
        _ppc_ori(11, 11, mailbox_lo),
        _ppc_lwz(12, 11, 0),
        _ppc_stw(12, 11, RESOLVER_HEADER_COLLISION_SEQUENCE),
        RESOLVER_COLLISION_RETURN_HOOK_ORIGINAL,    # original li r3,5
    ]
    branch_source = RESOLVER_COLLISION_RETURN_CAVE_ADDR + len(words) * 4
    words.append(_ppc_branch(branch_source, RESOLVER_COLLISION_RETURN_HOOK_ADDR + 4))
    return tuple(int(word) & 0xFFFFFFFF for word in words)


def resolver_collision_return_stub_bytes() -> bytes:
    words = resolver_collision_return_stub_words()
    return struct.pack(">" + "I" * len(words), *words)


def resolver_apply_stub_words() -> tuple[int, ...]:
    """Capture the post-route damage immediately before the HP subtraction."""
    mailbox_hi = (RESOLVER_MAILBOX_ADDR >> 16) & 0xFFFF
    mailbox_lo = RESOLVER_MAILBOX_ADDR & 0xFFFF
    words = [
        _ppc_lis(11, mailbox_hi),
        _ppc_ori(11, 11, mailbox_lo),
        _ppc_lwz(12, 11, RESOLVER_HEADER_COLLISION_SEQUENCE),
        _ppc_rlwinm(0, 12, 0, 27, 31),             # sequence & 31
        _ppc_mulli(0, 0, RESOLVER_ENTRY_SIZE),
        _ppc_add(11, 11, 0),
        _ppc_addi(11, 11, RESOLVER_MAILBOX_HEADER_SIZE),
        RESOLVER_APPLY_HOOK_ORIGINAL,               # original lwz r0,20(r1)
        _ppc_stw(0, 11, RES_OFF_APPLIED_DAMAGE),
        _ppc_stw(12, 11, RES_OFF_APPLICATION_SEQUENCE),
        RESOLVER_APPLY_HOOK_ORIGINAL,               # leave r0 exactly as native code expects
    ]
    branch_source = RESOLVER_APPLY_CAVE_ADDR + len(words) * 4
    words.append(_ppc_branch(branch_source, RESOLVER_APPLY_HOOK_ADDR + 4))
    return tuple(int(word) & 0xFFFFFFFF for word in words)


def resolver_apply_stub_bytes() -> bytes:
    words = resolver_apply_stub_words()
    return struct.pack(">" + "I" * len(words), *words)


def _f32be(blob: bytes, absolute_offset: int, *, block_start: int = 0) -> Optional[float]:
    rel = int(absolute_offset) - int(block_start)
    if rel < 0 or rel + 4 > len(blob):
        return None
    try:
        value = struct.unpack_from(">f", blob, rel)[0]
    except Exception:
        return None
    if value != value or abs(value) > 100000.0:
        return None
    return float(value)


def _decode_action_frame(value: Optional[float]) -> int:
    if value is None or value < 0.0 or value > 4000.0:
        return 0
    return max(0, int(round(value - 1.0)))


def _hex32(value: Any) -> str:
    return f"0x{_safe_int(value) & 0xFFFFFFFF:08X}"


def decode_property_a(value: Any) -> dict:
    """Decode the proven packed guard-height and strength fields."""
    raw = _safe_int(value) & 0xFFFFFFFF
    low = raw & 0x3F
    guard_mask = low & 0x38
    strength_mask = low & 0x07

    guards = [label for bit, label in PROPERTY_A_GUARD_BITS.items() if guard_mask & bit]
    strengths = [label for bit, label in PROPERTY_A_STRENGTH_BITS.items() if strength_mask & bit]

    if not guards and strength_mask:
        guard_text = "Unblockable"
    elif guards:
        guard_text = "/".join(guards)
    else:
        guard_text = "No guard category"

    if strengths:
        strength_text = "/".join(strengths) + " Hit"
    else:
        strength_text = "No strength tier"

    high_flags = raw & ~0x3F
    text_parts = [guard_text, strength_text]
    if high_flags == 0x00200000:
        text_parts.append("Capture trigger / opponent lock 0x00200000")
    elif high_flags == 0x00100000:
        text_parts.append("Victim stabilizer / carried-reaction modifier 0x00100000")
    elif high_flags in PROPERTY_A_RESULT_FLAG_LABELS:
        text_parts.append(f"{PROPERTY_A_RESULT_FLAG_LABELS[high_flags]} {_hex32(high_flags)}")
    elif high_flags:
        text_parts.append(f"A flags {_hex32(high_flags)}")
    return {
        "raw": raw,
        "low_byte": raw & 0xFF,
        "guard_mask": guard_mask,
        "guard": guard_text,
        "strength_mask": strength_mask,
        "strength": strength_text,
        "high_flags": high_flags,
        "text": ", ".join(text_parts),
    }


def decode_property_b(value: Any) -> dict:
    """Decode confirmed property B branches while preserving unknown bits."""
    raw = _safe_int(value) & 0xFFFFFFFF
    labels: list[str] = []
    known_mask = 0
    for bit, label in PROPERTY_B_CONFIRMED_BITS.items():
        if raw & bit:
            labels.append(label)
            known_mask |= bit
    observed: list[str] = []
    for bit, label in PROPERTY_B_OBSERVED_BITS.items():
        if raw & bit:
            observed.append(label)
            known_mask |= bit
    correlated: list[str] = []
    for bit, label in PROPERTY_B_CORRELATED_BITS.items():
        if raw & bit:
            correlated.append(label)
            known_mask |= bit
    unknown = raw & ~known_mask
    text_parts = list(labels)
    text_parts.extend(observed)
    text_parts.extend(correlated)
    if unknown:
        text_parts.append(f"Unknown B bits {_hex32(unknown)}")
    if not text_parts:
        text_parts.append("No property B bits")
    return {
        "raw": raw,
        "confirmed": labels,
        "observed": observed,
        "correlated": correlated,
        "unknown_mask": unknown,
        "text": ", ".join(text_parts),
    }


def decode_combo_scaling_lane(active: Optional[bool]) -> dict:
    """Describe the exact native proration lane selected by fighter+0x44A4."""
    if active is None:
        return {
            "active": None,
            "loss_per_hit": None,
            "floor": None,
            "text": "Combat lane unknown",
        }
    if bool(active):
        return {
            "active": True,
            "loss_per_hit": 0.05,
            "floor": 0.35,
            "text": "+0x44A4 ON: -5% per hit, 35% floor",
        }
    return {
        "active": False,
        "loss_per_hit": 0.03,
        "floor": 0.43,
        "text": "+0x44A4 OFF: -3% per hit, 43% floor",
    }


def default_profile_path() -> Path:
    return Path(user_data_path("runtime")) / PROFILE_FILE


def default_event_path() -> Path:
    return Path(user_data_path("runtime")) / EVENT_FILE


def _empty_doc() -> dict:
    return {
        "version": PROFILE_VERSION,
        "sampling_mode": SAMPLING_MODE,
        "updated_utc": "",
        "signatures": {},
        "events": [],
    }


def _read_doc(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_doc()
    if not isinstance(raw, dict):
        return _empty_doc()
    raw["version"] = PROFILE_VERSION
    raw["sampling_mode"] = SAMPLING_MODE
    if not isinstance(raw.get("signatures"), dict):
        raw["signatures"] = {}
    if not isinstance(raw.get("events"), list):
        raw["events"] = []
    raw.setdefault("updated_utc", "")
    return raw


def _write_json_atomic(path: Path, doc: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(doc)
        payload["version"] = PROFILE_VERSION
        payload["sampling_mode"] = SAMPLING_MODE
        payload["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(tmp_name, path)
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except Exception:
                pass
        return True
    except Exception:
        return False


@dataclass(frozen=True)
class _SlotMeta:
    slot: str
    base: int
    char_id: int = 0
    name: str = ""
    action_id: int = 0
    action_name: str = ""
    point_active: Optional[bool] = None
    combo_lane_active: Optional[bool] = None


@dataclass(frozen=True)
class _FighterLiveMeta:
    slot: str
    base: int
    char_id: int
    name: str
    action_id: int
    action_name: str
    action_frame: int
    point_active: Optional[bool]
    combo_lane_active: Optional[bool]


class RuntimeAttackPropertyProfiler:
    """Resolve the current move definition, with optional contact capture."""

    def __init__(
        self,
        path: Optional[Path] = None,
        event_path: Optional[Path] = None,
        *,
        read_u32: Optional[Callable[[int], Optional[int]]] = None,
        read_block: Optional[Callable[[int, int], bytes]] = None,
        write_block: Optional[Callable[[int, bytes], bool]] = None,
        is_valid_addr: Optional[Callable[[int], bool]] = None,
        poll_hz: float = DEFAULT_POLL_HZ,
        start_worker: bool = True,
        emit_console: bool = True,
        enable_resolver_hook: bool = False,
    ):
        self.path = Path(path or default_profile_path())
        self.event_path = Path(event_path or default_event_path())
        self.doc = _read_doc(self.path)
        self._read_u32 = read_u32 or rd32
        self._read_block = read_block or rbytes
        self._enable_resolver_hook = bool(enable_resolver_hook)
        native_writer = write_block or wbytes
        self._write_block = native_writer if self._enable_resolver_hook else (lambda _address, _payload: False)
        self._is_valid_addr = is_valid_addr or addr_in_ram
        self._poll_interval = 1.0 / max(60.0, float(poll_hz or DEFAULT_POLL_HZ))
        self._idle_poll_interval = 1.0 / 60.0
        env_console = str(os.environ.get("TVC_ATTACK_PROPERTY_DEBUG", "1")).strip().lower()
        self._emit_console = bool(emit_console) and env_console not in {"0", "false", "no", "off"}
        self._lock = threading.RLock()
        self._console_definition_signatures: Dict[str, str] = {}
        self._console_projectile_signatures: Dict[str, str] = {}
        # Registry slots and linked records are aggressively recycled. Track a
        # monotonically increasing allocation epoch for each raw identity so a
        # reused actor cannot inherit the previous lifetime's name or flags.
        self._attack_actor_epoch_counter: Dict[tuple[str, int, int, int], int] = {}
        self._attack_actor_live_epoch: Dict[tuple[str, int, int, int], int] = {}
        self._attack_actor_name_by_lifetime: Dict[tuple[str, int, int, int, int], str] = {}
        self._stop = threading.Event()
        self._slot_meta: Dict[str, _SlotMeta] = {}
        self._action_name_cache: Dict[tuple[int, int], str] = {}
        self._last_actor_signatures: Dict[int, tuple] = {}
        self._active_by_slot: Dict[str, list[dict]] = {}
        self._latest_by_slot: Dict[str, dict] = {}
        self._pool_manager = 0
        self._pool_free_head = 0
        self._pool_status = "DISABLED"
        self._resolver_hook_state = "WAITING" if self._enable_resolver_hook else "DISABLED"
        self._resolver_hook_error = ""
        self._resolver_last_attempt = 0.0
        self._resolver_last_sequence = 0
        self._resolver_lost_events = 0
        self._resolver_hook_installed_by_us = False
        self._resolver_deferred_entries: Dict[int, dict] = {}
        self._definition_by_slot: Dict[str, dict] = {}
        self._definition_status_by_slot: Dict[str, dict] = {}
        self._latched_definition_by_slot: Dict[str, dict] = {}
        self._definition_signature_by_slot: Dict[str, tuple[int, int, int, int]] = {}
        self._definition_active_slots: set[str] = set()
        self._definition_sequence = 0
        self._latched_projectiles_by_slot: Dict[str, list[dict]] = {}
        self._sampler_error = ""
        self._pending_csv: list[dict] = []
        self._dirty = False
        self._change_serial = 0
        self._changed_pending = False
        self._gui_frame = 0
        self._last_write = 0.0
        self._thread: Optional[threading.Thread] = None
        if start_worker:
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="TvCAttackPropertySampler",
                daemon=True,
            )
            self._thread.start()
        self._writer = DeferredWorkLoop(
            lambda: self._write_pending(force=False),
            interval=WRITE_INTERVAL_SEC,
            name="TvCAttackPropertyWriter",
        )

    def _read_word(self, address: int) -> Optional[int]:
        try:
            value = self._read_u32(int(address))
        except Exception:
            value = None
        if value is None:
            return None
        return int(value) & 0xFFFFFFFF

    def _read_bytes(self, address: int, size: int) -> bytes:
        try:
            blob = self._read_block(int(address), int(size))
        except Exception:
            return b""
        return bytes(blob or b"")

    def _valid(self, address: int) -> bool:
        try:
            return bool(address and self._is_valid_addr(int(address)))
        except Exception:
            return False

    def _write_bytes(self, address: int, payload: bytes) -> bool:
        try:
            return bool(self._write_block(int(address), bytes(payload)))
        except Exception:
            return False

    @staticmethod
    def _word_bytes(value: int) -> bytes:
        return struct.pack(">I", int(value) & 0xFFFFFFFF)

    def _set_hook_error(self, state: str, detail: str = "") -> bool:
        with self._lock:
            self._resolver_hook_state = str(state)
            self._resolver_hook_error = str(detail or "")
        return False

    def _ensure_resolver_hook(self, *, force: bool = False) -> bool:
        if not self._enable_resolver_hook:
            return False
        now = time.monotonic()
        with self._lock:
            if self._resolver_hook_state == "READY":
                return True
            if not force and now - self._resolver_last_attempt < RESOLVER_RETRY_SEC:
                return False
            self._resolver_last_attempt = now

        hook_specs = (
            ("entry", RESOLVER_HOOK_ADDR, RESOLVER_HOOK_ORIGINAL, resolver_hook_word(),
             RESOLVER_CAVE_ADDR, resolver_stub_bytes()),
            ("exit", RESOLVER_EXIT_HOOK_ADDR, RESOLVER_EXIT_HOOK_ORIGINAL, resolver_exit_hook_word(),
             RESOLVER_EXIT_CAVE_ADDR, resolver_exit_stub_bytes()),
            ("collision return", RESOLVER_COLLISION_RETURN_HOOK_ADDR,
             RESOLVER_COLLISION_RETURN_HOOK_ORIGINAL, resolver_collision_return_hook_word(),
             RESOLVER_COLLISION_RETURN_CAVE_ADDR, resolver_collision_return_stub_bytes()),
            ("apply", RESOLVER_APPLY_HOOK_ADDR, RESOLVER_APPLY_HOOK_ORIGINAL,
             resolver_apply_hook_word(), RESOLVER_APPLY_CAVE_ADDR, resolver_apply_stub_bytes()),
        )
        current = {label: self._read_word(address) for label, address, *_rest in hook_specs}
        if any(value is None for value in current.values()):
            return self._set_hook_error("WAITING", "Dolphin memory is not available")

        caves_match = all(
            self._read_bytes(cave, len(payload)) == payload
            for _label, _addr, _original, _hook, cave, payload in hook_specs
        )
        hooks_match = all(current[label] == hook for label, _addr, _orig, hook, _cave, _payload in hook_specs)
        if hooks_match and caves_match:
            header = self._read_bytes(RESOLVER_MAILBOX_ADDR, RESOLVER_MAILBOX_HEADER_SIZE)
            if (
                len(header) == RESOLVER_MAILBOX_HEADER_SIZE
                and _u32be(header, 0x04) == RESOLVER_MAILBOX_MAGIC
                and _u32be(header, 0x08) == RESOLVER_MAILBOX_VERSION
                and _u32be(header, 0x0C) == RESOLVER_RING_COUNT
                and _u32be(header, 0x10) == RESOLVER_ENTRY_SIZE
            ):
                with self._lock:
                    self._resolver_hook_state = "READY"
                    self._resolver_hook_error = ""
                return True

        for label, _address, original, hook, _cave, _payload in hook_specs:
            value = current[label]
            if value not in (original, hook):
                return self._set_hook_error(
                    "MISMATCH",
                    f"resolver {label} word is 0x{value:08X}, expected 0x{original:08X}",
                )

        # Disarm the entry first. Then no new calculator call can reserve a slot
        # while the remaining caves and mailbox are being upgraded.
        disarm_order = (hook_specs[0], hook_specs[3], hook_specs[2], hook_specs[1])
        for label, address, original, hook, _cave, _payload in disarm_order:
            if current[label] == hook and not self._write_bytes(address, self._word_bytes(original)):
                return self._set_hook_error("WRITE FAILED", f"could not disarm stale resolver {label} branch")

        mailbox_probe = self._read_bytes(RESOLVER_MAILBOX_ADDR, RESOLVER_MAILBOX_SIZE)
        if len(mailbox_probe) != RESOLVER_MAILBOX_SIZE:
            return self._set_hook_error("WAITING", "resolver mailbox is not readable")
        mailbox_magic = _u32be(mailbox_probe, 0x04)
        mailbox_known = (
            mailbox_magic == RESOLVER_MAILBOX_MAGIC
            and _u32be(mailbox_probe, 0x08) == RESOLVER_MAILBOX_VERSION
        )
        mailbox_upgradeable = mailbox_magic == 0 or mailbox_magic in RESOLVER_MAILBOX_LEGACY_MAGICS or mailbox_magic == RESOLVER_MAILBOX_MAGIC
        if not mailbox_known and any(mailbox_probe) and not mailbox_upgradeable:
            return self._set_hook_error("MAILBOX BUSY", f"0x{RESOLVER_MAILBOX_ADDR:08X} is not empty")
        legacy_mailbox = mailbox_magic in RESOLVER_MAILBOX_LEGACY_MAGICS

        for label, _address, _original, _hook, cave, payload in hook_specs:
            probe = self._read_bytes(cave, len(payload))
            if len(probe) != len(payload):
                return self._set_hook_error("WAITING", f"resolver {label} code cave is not readable")
            # ATK4/ATK5 used the first two caves. They are explicitly safe to
            # replace during a versioned mailbox upgrade. New caves must be empty.
            legacy_cave = legacy_mailbox and label in {"entry", "exit"}
            if probe != payload and any(probe) and not legacy_cave:
                return self._set_hook_error("CAVE BUSY", f"0x{cave:08X} is not empty")

        mailbox = bytearray(RESOLVER_MAILBOX_SIZE)
        struct.pack_into(">I", mailbox, 0x04, RESOLVER_MAILBOX_MAGIC)
        struct.pack_into(">I", mailbox, 0x08, RESOLVER_MAILBOX_VERSION)
        struct.pack_into(">I", mailbox, 0x0C, RESOLVER_RING_COUNT)
        struct.pack_into(">I", mailbox, 0x10, RESOLVER_ENTRY_SIZE)

        for label, _address, _original, _hook, cave, payload in hook_specs:
            if not self._write_bytes(cave, payload):
                return self._set_hook_error("WRITE FAILED", f"could not write resolver {label} cave")
        if not self._write_bytes(RESOLVER_MAILBOX_ADDR, mailbox):
            return self._set_hook_error("WRITE FAILED", "could not initialize resolver mailbox")
        for label, _address, _original, _hook, cave, payload in hook_specs:
            if self._read_bytes(cave, len(payload)) != payload:
                return self._set_hook_error("VERIFY FAILED", f"resolver {label} cave did not verify")

        # Arm consumers first and the calculator entry last.
        arm_order = (hook_specs[1], hook_specs[2], hook_specs[3], hook_specs[0])
        armed = []
        for label, address, original, hook, _cave, _payload in arm_order:
            if not self._write_bytes(address, self._word_bytes(hook)):
                for _armed_label, armed_address, armed_original, _armed_hook in reversed(armed):
                    self._write_bytes(armed_address, self._word_bytes(armed_original))
                return self._set_hook_error("WRITE FAILED", f"could not arm resolver {label} branch")
            armed.append((label, address, original, hook))
        if any(self._read_word(address) != hook for _label, address, _original, hook, _cave, _payload in hook_specs):
            for _label, address, original, _hook, _cave, _payload in hook_specs:
                if self._read_word(address) == _hook:
                    self._write_bytes(address, self._word_bytes(original))
            return self._set_hook_error("VERIFY FAILED", "resolver four-hook install did not verify")

        with self._lock:
            self._resolver_hook_state = "READY"
            self._resolver_hook_error = ""
            self._resolver_last_sequence = 0
            self._resolver_deferred_entries.clear()
            self._resolver_hook_installed_by_us = True
        if self._emit_console:
            print(f"[attack flags] native damage mailbox v6 armed at 0x{RESOLVER_MAILBOX_ADDR:08X}", flush=True)
        return True

    def _restore_resolver_hook(self) -> bool:
        if not self._enable_resolver_hook:
            return True
        specs = (
            (RESOLVER_HOOK_ADDR, RESOLVER_HOOK_ORIGINAL, resolver_hook_word()),
            (RESOLVER_APPLY_HOOK_ADDR, RESOLVER_APPLY_HOOK_ORIGINAL, resolver_apply_hook_word()),
            (RESOLVER_COLLISION_RETURN_HOOK_ADDR, RESOLVER_COLLISION_RETURN_HOOK_ORIGINAL,
             resolver_collision_return_hook_word()),
            (RESOLVER_EXIT_HOOK_ADDR, RESOLVER_EXIT_HOOK_ORIGINAL, resolver_exit_hook_word()),
        )
        ok = True
        for address, original, hook in specs:
            value = self._read_word(address)
            if value == hook:
                ok = self._write_bytes(address, self._word_bytes(original)) and ok
            elif value not in (None, original):
                ok = False
        if ok:
            with self._lock:
                self._resolver_hook_state = "RESTORED"
                self._resolver_deferred_entries.clear()
        return bool(ok)

    @staticmethod
    def _decode_resolver_entry(entry_blob: bytes, sequence: int) -> Optional[dict]:
        if len(entry_blob) != RESOLVER_ENTRY_SIZE:
            return None
        entry_sequence = _u32be(entry_blob, RES_OFF_SEQUENCE) & 0xFFFFFFFF
        if entry_sequence != (int(sequence) & 0xFFFFFFFF):
            return None
        calc_completion = _u32be(entry_blob, RES_OFF_CALC_COMPLETION_SEQUENCE) & 0xFFFFFFFF
        application_sequence = _u32be(entry_blob, RES_OFF_APPLICATION_SEQUENCE) & 0xFFFFFFFF
        calc_complete = calc_completion == entry_sequence
        application_complete = application_sequence == entry_sequence
        applied_damage = _u32be(entry_blob, RES_OFF_APPLIED_DAMAGE) if application_complete else None
        return {
            "event_sequence": entry_sequence,
            "attacker_base": _u32be(entry_blob, RES_OFF_ATTACKER),
            "defender_base": _u32be(entry_blob, RES_OFF_DEFENDER),
            "property_b": _u32be(entry_blob, RES_OFF_PROPERTY_B),
            "property_a": _u32be(entry_blob, RES_OFF_PROPERTY_A),
            "result_ptr_a": _u32be(entry_blob, RES_OFF_RESULT_PTR_A),
            "result_ptr_b": _u32be(entry_blob, RES_OFF_RESULT_PTR_B),
            "route_arg": _u32be(entry_blob, RES_OFF_ROUTE_ARG),
            "packet": _u32be(entry_blob, RES_OFF_PACKET),
            "caller_lr": _u32be(entry_blob, RES_OFF_CALLER_LR),
            "base_damage": _u32be(entry_blob, RES_OFF_AUTHORED_DAMAGE),
            "authored_damage": _u32be(entry_blob, RES_OFF_AUTHORED_DAMAGE),
            "damage_calc_output": _u32be(entry_blob, RES_OFF_DAMAGE_CALC_OUTPUT),
            "damage_calc_aux": _u32be(entry_blob, RES_OFF_DAMAGE_CALC_AUX),
            "calc_completion_sequence": calc_completion,
            "native_damage_calc_complete": calc_complete,
            "applied_damage": applied_damage,
            "resolved_damage": applied_damage,
            "resolved_aux": _u32be(entry_blob, RES_OFF_DAMAGE_CALC_AUX) if calc_complete else None,
            "application_sequence": application_sequence,
            "native_damage_complete": application_complete,
            "phase_property_a": _u32be(entry_blob, RES_OFF_PHASE_A),
            "phase_property_b": _u32be(entry_blob, RES_OFF_PHASE_B),
            "runtime_status_20": _u32be(entry_blob, RES_OFF_RUNTIME_STATUS),
            "packet_owner_base": _u32be(entry_blob, RES_OFF_PACKET_OWNER),
            "action_id": _u32be(entry_blob, RES_OFF_ACTION_ID),
        }

    def _read_resolver_entries(self) -> list[dict]:
        if not self._ensure_resolver_hook():
            return []
        hook_checks = (
            (RESOLVER_HOOK_ADDR, resolver_hook_word()),
            (RESOLVER_EXIT_HOOK_ADDR, resolver_exit_hook_word()),
            (RESOLVER_COLLISION_RETURN_HOOK_ADDR, resolver_collision_return_hook_word()),
            (RESOLVER_APPLY_HOOK_ADDR, resolver_apply_hook_word()),
        )
        if any(self._read_word(address) != hook for address, hook in hook_checks):
            self._set_hook_error("RESET", "resolver hook was restored or game memory reloaded")
            return []

        blob = b""
        for _attempt in range(2):
            before = self._read_word(RESOLVER_MAILBOX_ADDR)
            candidate = self._read_bytes(RESOLVER_MAILBOX_ADDR, RESOLVER_MAILBOX_SIZE)
            after = self._read_word(RESOLVER_MAILBOX_ADDR)
            if before is not None and before == after and len(candidate) == RESOLVER_MAILBOX_SIZE:
                if _u32be(candidate, 0x00) == after:
                    blob = candidate
                    break
        if len(blob) != RESOLVER_MAILBOX_SIZE:
            self._set_hook_error("READ RETRY", "resolver mailbox changed during the read")
            return []
        if _u32be(blob, 0x04) != RESOLVER_MAILBOX_MAGIC or _u32be(blob, 0x08) != RESOLVER_MAILBOX_VERSION:
            self._set_hook_error("RESET", "resolver mailbox header was cleared")
            return []

        committed = _u32be(blob, 0x00) & 0xFFFFFFFF
        with self._lock:
            previous = self._resolver_last_sequence & 0xFFFFFFFF
            deferred_snapshot = dict(self._resolver_deferred_entries)

        def decode(sequence: int) -> Optional[dict]:
            slot_index = sequence & (RESOLVER_RING_COUNT - 1)
            off = RESOLVER_MAILBOX_HEADER_SIZE + slot_index * RESOLVER_ENTRY_SIZE
            return self._decode_resolver_entry(blob[off:off + RESOLVER_ENTRY_SIZE], sequence)

        new_sequences: list[int] = []
        if committed != previous:
            delta = (committed - previous) & 0xFFFFFFFF
            if delta > 0x7FFFFFFF:
                previous = 0
                delta = committed
            if delta > RESOLVER_RING_COUNT:
                with self._lock:
                    self._resolver_lost_events += int(delta - RESOLVER_RING_COUNT)
                start = (committed - RESOLVER_RING_COUNT + 1) & 0xFFFFFFFF
            else:
                start = (previous + 1) & 0xFFFFFFFF
            sequence = start
            for _ in range(min(int(delta), RESOLVER_RING_COUNT)):
                new_sequences.append(sequence)
                sequence = (sequence + 1) & 0xFFFFFFFF

        now = time.monotonic()
        emitted: list[dict] = []
        processed: set[int] = set()

        def consider(sequence: int, entry: Optional[dict], old: Optional[dict] = None) -> None:
            processed.add(sequence)
            if entry is None:
                if old and (committed != sequence or now - float(old.get("first_seen", now)) >= RESOLVER_APPLICATION_DEFER_SEC):
                    emitted.append(dict(old.get("entry") or {}))
                    with self._lock:
                        self._resolver_deferred_entries.pop(sequence, None)
                else:
                    with self._lock:
                        self._resolver_lost_events += 1
                return
            is_collision = _safe_int(entry.get("caller_lr")) == RESOLVER_COLLISION_CALLER_LR
            needs_apply = is_collision and bool(entry.get("native_damage_calc_complete")) and not bool(entry.get("native_damage_complete"))
            if needs_apply:
                prior = old or deferred_snapshot.get(sequence)
                first_seen = float((prior or {}).get("first_seen", now))
                with self._lock:
                    self._resolver_deferred_entries[sequence] = {"first_seen": first_seen, "entry": dict(entry)}
                if committed != sequence or now - first_seen >= RESOLVER_APPLICATION_DEFER_SEC:
                    emitted.append(entry)
                    with self._lock:
                        self._resolver_deferred_entries.pop(sequence, None)
                return
            emitted.append(entry)
            with self._lock:
                self._resolver_deferred_entries.pop(sequence, None)

        for sequence in new_sequences:
            consider(sequence, decode(sequence))
        for sequence, old in deferred_snapshot.items():
            if sequence not in processed:
                consider(sequence, decode(sequence), old)

        with self._lock:
            self._resolver_last_sequence = committed
        emitted.sort(key=lambda item: (_safe_int(item.get("event_sequence")) - committed) & 0xFFFFFFFF)
        return emitted

    def _record_from_resolver_entry(
        self,
        entry: dict,
        slot_meta_by_base: dict[int, _SlotMeta],
        pointer_meta: dict[int, _FighterLiveMeta],
    ) -> Optional[dict]:
        owner_base = _safe_int(entry.get("attacker_base"))
        victim_base = _safe_int(entry.get("defender_base"))
        fallback = slot_meta_by_base.get(owner_base)
        if fallback is None:
            return None
        owner_meta = self._fighter_live_meta(fallback)
        stored_action = _safe_int(entry.get("action_id"), owner_meta.action_id)
        action_name = self._action_name_cache.get((owner_base, stored_action), "")
        if not action_name and fallback.action_id == stored_action:
            action_name = fallback.action_name
        owner_meta = _FighterLiveMeta(
            slot=owner_meta.slot,
            base=owner_meta.base,
            char_id=owner_meta.char_id,
            name=owner_meta.name,
            action_id=stored_action,
            action_name=action_name,
            action_frame=owner_meta.action_frame,
            point_active=owner_meta.point_active,
            combo_lane_active=owner_meta.combo_lane_active,
        )
        pointer_meta[owner_base] = owner_meta
        victim_meta = pointer_meta.get(victim_base)
        if victim_meta is None and victim_base in slot_meta_by_base:
            victim_meta = self._fighter_live_meta(slot_meta_by_base[victim_base])
            pointer_meta[victim_base] = victim_meta

        property_a = _safe_int(entry.get("property_a"))
        property_b = _safe_int(entry.get("property_b"))
        phase_a = _safe_int(entry.get("phase_property_a"))
        phase_b = _safe_int(entry.get("phase_property_b"))
        decoded_a = decode_property_a(property_a)
        decoded_b = decode_property_b(property_b)
        decoded_phase_a = decode_property_a(phase_a)
        decoded_phase_b = decode_property_b(phase_b)
        scaling_lane = decode_combo_scaling_lane(owner_meta.combo_lane_active)
        packet = _safe_int(entry.get("packet"))
        actor = packet - 0x64 if packet >= 0x64 else 0

        return {
            "event_sequence": _safe_int(entry.get("event_sequence")),
            "caller_lr": _safe_int(entry.get("caller_lr")),
            "route_arg": _safe_int(entry.get("route_arg")),
            "result_ptr_a": _safe_int(entry.get("result_ptr_a")),
            "result_ptr_b": _safe_int(entry.get("result_ptr_b")),
            "actor": actor,
            "packet": packet,
            "packet_source": "resolver_mailbox",
            "packet_state": "CONTACT",
            "packet_live": False,
            "pool_manager": 0,
            "pool_free_head": 0,
            "owner_slot": owner_meta.slot,
            "owner_base": owner_base,
            "packet_owner_base": _safe_int(entry.get("packet_owner_base")),
            "owner_char_id": owner_meta.char_id,
            "owner_name": owner_meta.name,
            "owner_action_id": owner_meta.action_id,
            "owner_action_name": owner_meta.action_name,
            "owner_action_frame": owner_meta.action_frame,
            "owner_point_active": owner_meta.point_active,
            "owner_combo_lane_active": owner_meta.combo_lane_active,
            "scaling_loss_per_hit": scaling_lane["loss_per_hit"],
            "scaling_floor": scaling_lane["floor"],
            "scaling_track_text": scaling_lane["text"],
            "victim_slot": victim_meta.slot if victim_meta else "",
            "victim_base": victim_base,
            "victim_char_id": victim_meta.char_id if victim_meta else 0,
            "victim_name": victim_meta.name if victim_meta else "",
            "property_a": property_a,
            "property_a_text": decoded_a["text"],
            "property_a_guard_mask": decoded_a["guard_mask"],
            "property_a_strength_mask": decoded_a["strength_mask"],
            "property_a_high_flags": decoded_a["high_flags"],
            "property_b": property_b,
            "property_b_text": decoded_b["text"],
            "property_b_unknown_mask": decoded_b["unknown_mask"],
            "runtime_status_20": _safe_int(entry.get("runtime_status_20")),
            "phase_property_a": phase_a,
            "phase_property_a_text": decoded_phase_a["text"],
            "phase_property_b": phase_b,
            "phase_property_b_text": decoded_phase_b["text"],
            "phase_property_b_unknown_mask": decoded_phase_b["unknown_mask"],
            "base_damage": _safe_int(entry.get("authored_damage", entry.get("base_damage"))),
            "authored_damage": _safe_int(entry.get("authored_damage", entry.get("base_damage"))),
            "damage_calc_output": (
                _safe_int(entry.get("damage_calc_output"))
                if entry.get("native_damage_calc_complete") else None
            ),
            "damage_calc_aux": (
                _safe_int(entry.get("damage_calc_aux"))
                if entry.get("native_damage_calc_complete") else None
            ),
            "native_damage_calc_complete": bool(entry.get("native_damage_calc_complete")),
            "applied_damage": (
                _safe_int(entry.get("applied_damage")) if entry.get("native_damage_complete") else None
            ),
            "resolved_damage": (
                _safe_int(entry.get("resolved_damage")) if entry.get("native_damage_complete") else None
            ),
            "resolved_aux": (
                _safe_int(entry.get("resolved_aux")) if entry.get("native_damage_calc_complete") else None
            ),
            "native_damage_complete": bool(entry.get("native_damage_complete")),
            "object_flags_10": 0,
            "object_flags_14": 0,
            "object_flags_18": 0,
            "object_flags_1c": 0,
            "object_flags_24": 0,
            "object_flags_28": 0,
            "object_flags_2c": 0,
        }

    def _read_pool_state(self) -> Optional[dict]:
        """Read and validate the native 128-entry attack-actor pool manager."""
        manager = self._read_word(ATTACK_POOL_MANAGER_PTR_ADDR)
        if manager is None or not self._valid(manager):
            return None
        blob = self._read_bytes(manager, POOL_MANAGER_META_SIZE)
        if len(blob) != POOL_MANAGER_META_SIZE:
            return None
        free_head = _u32be(blob, POOL_HEAD_OFFSET)
        first_node = _u32be(blob, POOL_FIRST_NODE_OFFSET)
        count = _u32be(blob, POOL_COUNT_OFFSET)
        payload_size = _u32be(blob, POOL_PAYLOAD_SIZE_OFFSET)
        if count != POOL_EXPECTED_COUNT or payload_size != POOL_EXPECTED_PAYLOAD_SIZE:
            return None
        if first_node != manager + POOL_STORAGE_OFFSET:
            return None
        if free_head and not self._valid(free_head):
            return None
        return {
            "manager": int(manager),
            "free_head": int(free_head),
            "first_node": int(first_node),
            "count": int(count),
            "payload_size": int(payload_size),
            "stride": int(payload_size) + POOL_NODE_HEADER_SIZE,
        }

    def _read_last_freed_pool_actor(
        self,
        pointer_meta: dict[int, _FighterLiveMeta],
        slot_meta_by_base: dict[int, _SlotMeta],
    ) -> Optional[dict]:
        """Return the stable packet left in the most recently released actor."""
        state = self._read_pool_state()
        if state is None:
            self._pool_manager = 0
            self._pool_free_head = 0
            self._pool_status = "UNAVAILABLE"
            return None
        self._pool_manager = _safe_int(state.get("manager"))
        self._pool_free_head = _safe_int(state.get("free_head"))
        self._pool_status = "READY"
        free_head = self._pool_free_head
        if not free_head:
            self._pool_status = "FULL"
            return None
        actor = free_head + POOL_NODE_HEADER_SIZE
        owner_base = self._read_word(actor + OFF_OWNER)
        if owner_base is None or owner_base not in slot_meta_by_base:
            return None
        owner_live = self._fighter_live_meta(slot_meta_by_base[owner_base])
        pointer_meta[owner_base] = owner_live
        record = self._read_actor(actor, owner_live, pointer_meta)
        if record is None:
            return None
        # Newly initialized or never-used free slots are all zero. Do not let a
        # pool pop expose one of those as a fake packet.
        meaningful = any(
            _safe_int(record.get(key))
            for key in (
                "property_a", "property_b", "runtime_status_20",
                "phase_property_a", "phase_property_b", "base_damage",
            )
        )
        if not meaningful:
            return None
        record["packet_source"] = "last_freed_pool"
        record["packet_live"] = False
        record["pool_manager"] = self._pool_manager
        record["pool_free_head"] = self._pool_free_head
        return record

    def _fighter_live_meta(self, fallback: _SlotMeta) -> _FighterLiveMeta:
        blob = self._read_bytes(fallback.base + FIGHTER_META_START, FIGHTER_META_SIZE)
        role_blob = self._read_bytes(fallback.base + FIGHTER_ROLE_START, FIGHTER_ROLE_SIZE)
        char_id = fallback.char_id
        action_id = fallback.action_id
        action_frame = 0
        point_active = fallback.point_active
        combo_lane_active = fallback.combo_lane_active
        if len(blob) == FIGHTER_META_SIZE:
            char_id = _u32be(blob, FIGHTER_CHAR_ID, block_start=FIGHTER_META_START) or char_id
            action_id = _u32be(blob, FIGHTER_ACTION_ID, block_start=FIGHTER_META_START) or action_id
            action_frame = _decode_action_frame(
                _f32be(blob, FIGHTER_ACTION_FRAME, block_start=FIGHTER_META_START)
            )
        if len(role_blob) == FIGHTER_ROLE_SIZE:
            point_active = bool(
                _u32be(role_blob, FIGHTER_POINT_ACTIVE, block_start=FIGHTER_ROLE_START)
            )
            combo_lane_active = bool(
                _u32be(role_blob, FIGHTER_COMBO_LANE_ACTIVE, block_start=FIGHTER_ROLE_START)
            )
        name = CHAR_NAMES.get(char_id, fallback.name or f"ID_{char_id}")
        action_name = fallback.action_name if not fallback.action_id or fallback.action_id == action_id else ""
        return _FighterLiveMeta(
            slot=fallback.slot,
            base=fallback.base,
            char_id=char_id,
            name=name,
            action_id=action_id,
            action_name=action_name,
            action_frame=action_frame,
            point_active=point_active,
            combo_lane_active=combo_lane_active,
        )

    def _read_actor(self, actor: int, owner: Optional[_FighterLiveMeta], pointer_meta: dict[int, _FighterLiveMeta]) -> Optional[dict]:
        blob = self._read_bytes(actor + ACTOR_BLOCK_START, ACTOR_BLOCK_SIZE)
        if len(blob) != ACTOR_BLOCK_SIZE:
            return None

        owner_base = _u32be(blob, OFF_OWNER, block_start=ACTOR_BLOCK_START)
        victim_base = _u32be(blob, OFF_VICTIM, block_start=ACTOR_BLOCK_START)
        next_node = _u32be(blob, OFF_LIST_NEXT, block_start=ACTOR_BLOCK_START)
        owner_meta = pointer_meta.get(owner_base) or owner
        if owner_meta is None:
            return None
        victim_meta = pointer_meta.get(victim_base)
        property_a = _u32be(blob, OFF_PROPERTY_A, block_start=ACTOR_BLOCK_START)
        property_b = _u32be(blob, OFF_PROPERTY_B, block_start=ACTOR_BLOCK_START)
        phase_a = _u32be(blob, OFF_PHASE_PROPERTY_A, block_start=ACTOR_BLOCK_START)
        phase_b = _u32be(blob, OFF_PHASE_PROPERTY_B, block_start=ACTOR_BLOCK_START)
        status20 = _u32be(blob, OFF_RUNTIME_STATUS_20, block_start=ACTOR_BLOCK_START)
        damage = _u32be(blob, OFF_BASE_DAMAGE, block_start=ACTOR_BLOCK_START)
        decoded_a = decode_property_a(property_a)
        decoded_b = decode_property_b(property_b)
        decoded_phase_a = decode_property_a(phase_a)
        decoded_phase_b = decode_property_b(phase_b)
        scaling_lane = decode_combo_scaling_lane(owner_meta.combo_lane_active)

        return {
            "actor": actor,
            "next_node": next_node,
            "packet_source": "live_owner_list",
            "packet_live": True,
            "pool_manager": self._pool_manager,
            "pool_free_head": self._pool_free_head,
            "owner_slot": owner_meta.slot,
            "owner_base": owner_base or owner_meta.base,
            "owner_char_id": owner_meta.char_id,
            "owner_name": owner_meta.name,
            "owner_action_id": owner_meta.action_id,
            "owner_action_name": owner_meta.action_name,
            "owner_action_frame": owner_meta.action_frame,
            "owner_point_active": owner_meta.point_active,
            "owner_combo_lane_active": owner_meta.combo_lane_active,
            "scaling_loss_per_hit": scaling_lane["loss_per_hit"],
            "scaling_floor": scaling_lane["floor"],
            "scaling_track_text": scaling_lane["text"],
            "victim_slot": victim_meta.slot if victim_meta else "",
            "victim_base": victim_base,
            "victim_char_id": victim_meta.char_id if victim_meta else 0,
            "victim_name": victim_meta.name if victim_meta else "",
            "property_a": property_a,
            "property_a_text": decoded_a["text"],
            "property_a_guard_mask": decoded_a["guard_mask"],
            "property_a_strength_mask": decoded_a["strength_mask"],
            "property_a_high_flags": decoded_a["high_flags"],
            "property_b": property_b,
            "property_b_text": decoded_b["text"],
            "property_b_unknown_mask": decoded_b["unknown_mask"],
            "runtime_status_20": status20,
            "phase_property_a": phase_a,
            "phase_property_a_text": decoded_phase_a["text"],
            "phase_property_b": phase_b,
            "phase_property_b_text": decoded_phase_b["text"],
            "phase_property_b_unknown_mask": decoded_phase_b["unknown_mask"],
            "base_damage": damage,
            "object_flags_10": _u32be(blob, OFF_OBJECT_FLAGS_10, block_start=ACTOR_BLOCK_START),
            "object_flags_14": _u32be(blob, OFF_OBJECT_FLAGS_14, block_start=ACTOR_BLOCK_START),
            "object_flags_18": _u32be(blob, OFF_OBJECT_FLAGS_18, block_start=ACTOR_BLOCK_START),
            "object_flags_1c": _u32be(blob, OFF_OBJECT_FLAGS_1C, block_start=ACTOR_BLOCK_START),
            "object_flags_24": _u32be(blob, OFF_OBJECT_FLAGS_24, block_start=ACTOR_BLOCK_START),
            "object_flags_28": _u32be(blob, OFF_OBJECT_FLAGS_28, block_start=ACTOR_BLOCK_START),
            "object_flags_2c": _u32be(blob, OFF_OBJECT_FLAGS_2C, block_start=ACTOR_BLOCK_START),
        }

    def _enumerate_owner_actors(
        self,
        owner: _FighterLiveMeta,
        pointer_meta: dict[int, _FighterLiveMeta],
        *,
        first_node: Optional[int] = None,
    ) -> list[dict]:
        sentinel = owner.base + OWNER_LIST_OFFSET
        node = first_node if first_node is not None else self._read_word(sentinel + LIST_NEXT_OFFSET)
        if node is None or node == sentinel:
            return []

        out: list[dict] = []
        seen: set[int] = set()
        while node and node != sentinel and len(out) < MAX_ACTORS_PER_OWNER:
            if node in seen or not self._valid(node):
                break
            seen.add(node)
            actor = int(node) - LIST_NODE_OFFSET
            if not self._valid(actor):
                break
            record = self._read_actor(actor, owner, pointer_meta)
            if record is None:
                break
            out.append(record)
            next_node = _safe_int(record.get("next_node"), 0)
            if next_node == node:
                break
            node = next_node
        return out

    @staticmethod
    def _signature(record: dict) -> tuple:
        return (
            _safe_int(record.get("owner_base")),
            _safe_int(record.get("owner_char_id")),
            _safe_int(record.get("owner_action_id")),
            -1 if record.get("owner_combo_lane_active") is None else int(bool(record.get("owner_combo_lane_active"))),
            _safe_int(record.get("victim_base")),
            _safe_int(record.get("property_a")),
            _safe_int(record.get("property_b")),
            _safe_int(record.get("runtime_status_20")),
            _safe_int(record.get("phase_property_a")),
            _safe_int(record.get("phase_property_b")),
            _safe_int(record.get("base_damage")),
            _safe_int(record.get("object_flags_10")),
            _safe_int(record.get("object_flags_14")),
            _safe_int(record.get("object_flags_18")),
            _safe_int(record.get("object_flags_1c")),
            _safe_int(record.get("object_flags_24")),
            _safe_int(record.get("object_flags_28")),
            _safe_int(record.get("object_flags_2c")),
        )

    @staticmethod
    def _signature_key(record: dict) -> str:
        fields = (
            _safe_int(record.get("owner_char_id")),
            _safe_int(record.get("owner_action_id")),
            -1 if record.get("owner_combo_lane_active") is None else int(bool(record.get("owner_combo_lane_active"))),
            _safe_int(record.get("property_a")),
            _safe_int(record.get("property_b")),
            _safe_int(record.get("runtime_status_20")),
            _safe_int(record.get("phase_property_a")),
            _safe_int(record.get("phase_property_b")),
            _safe_int(record.get("base_damage")),
        )
        return ":".join(f"{value:X}" for value in fields)

    def _record_transition_locked(self, record: dict, *, gui_frame: int) -> None:
        now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        event = dict(record)
        event.pop("next_node", None)
        event["timestamp_utc"] = now_utc
        event["gui_frame"] = int(gui_frame)

        key = self._signature_key(record)
        signatures = self.doc.setdefault("signatures", {})
        aggregate = signatures.get(key)
        if not isinstance(aggregate, dict):
            aggregate = dict(event)
            aggregate["first_seen_utc"] = now_utc
            aggregate["observations"] = 0
            aggregate["actors"] = []
            aggregate["owner_slots"] = []
            aggregate["victim_slots"] = []
        aggregate["last_seen_utc"] = now_utc
        aggregate["observations"] = _safe_int(aggregate.get("observations")) + 1
        actors = {str(value) for value in aggregate.get("actors") or []}
        actors.add(_hex32(record.get("actor")))
        aggregate["actors"] = sorted(actors)
        owner_slots = {str(value) for value in aggregate.get("owner_slots") or [] if str(value)}
        if record.get("owner_slot"):
            owner_slots.add(str(record["owner_slot"]))
        aggregate["owner_slots"] = sorted(owner_slots)
        victim_slots = {str(value) for value in aggregate.get("victim_slots") or [] if str(value)}
        if record.get("victim_slot"):
            victim_slots.add(str(record["victim_slot"]))
        aggregate["victim_slots"] = sorted(victim_slots)
        signatures[key] = aggregate

        history = self.doc.setdefault("events", [])
        history.append(event)
        if len(history) > MAX_EVENT_HISTORY:
            del history[:-MAX_EVENT_HISTORY]
        self._pending_csv.append(event)
        self._dirty = True
        self._change_serial += 1
        self._changed_pending = True

        if self._emit_console:
            owner_label = str(record.get("owner_slot") or "?")
            owner_name = str(record.get("owner_name") or "?")
            action = _safe_int(record.get("owner_action_id"))
            action_frame = _safe_int(record.get("owner_action_frame"))
            print(
                f"[attack flags] {owner_label} {owner_name} "
                f"action 0x{action:04X} f{action_frame} "
                f"A={_hex32(record.get('property_a'))} "
                f"B={_hex32(record.get('property_b'))} "
                f"S20={_hex32(record.get('runtime_status_20'))} "
                f"DMG={_safe_int(record.get('base_damage'))} "
                f"LANE={record.get('scaling_track_text') or 'unknown'}",
                flush=True,
            )

    def sample_once(self) -> int:
        with self._lock:
            slots = dict(self._slot_meta)
            gui_frame = self._gui_frame

        unique: dict[int, _SlotMeta] = {}
        for meta in slots.values():
            if meta.base and self._valid(meta.base):
                unique.setdefault(meta.base, meta)

        if not unique:
            self._ensure_resolver_hook()
            return 0

        entries = self._read_resolver_entries()
        if not entries:
            with self._lock:
                self._active_by_slot = {}
            return 0

        pointer_meta: dict[int, _FighterLiveMeta] = {
            base: _FighterLiveMeta(
                slot=meta.slot,
                base=meta.base,
                char_id=meta.char_id,
                name=CHAR_NAMES.get(meta.char_id, meta.name or f"ID_{meta.char_id}"),
                action_id=meta.action_id,
                action_name=meta.action_name,
                action_frame=0,
                point_active=meta.point_active,
                combo_lane_active=meta.combo_lane_active,
            )
            for base, meta in unique.items()
        }

        rows_by_slot: Dict[str, list[dict]] = {}
        transitions = 0
        with self._lock:
            for entry in entries:
                record = self._record_from_resolver_entry(entry, unique, pointer_meta)
                if record is None:
                    continue
                record["capture_gui_frame"] = int(gui_frame)
                self._record_transition_locked(record, gui_frame=gui_frame)
                slot = str(record.get("owner_slot") or "")
                if slot:
                    rows_by_slot.setdefault(slot, []).append(record)
                    self._latest_by_slot[slot] = dict(record)
                transitions += 1
            self._active_by_slot = rows_by_slot
        return transitions

    def _worker_loop(self) -> None:
        deadline = time.perf_counter()
        while not self._stop.is_set():
            try:
                self.sample_once()
                with self._lock:
                    self._sampler_error = ""
            except Exception as exc:
                with self._lock:
                    self._sampler_error = repr(exc)
                    self._resolver_hook_state = "SAMPLER ERROR"
                    self._resolver_hook_error = repr(exc)
            with self._lock:
                active = any(bool(rows) for rows in self._active_by_slot.values())
            deadline += self._poll_interval if active else self._idle_poll_interval
            delay = deadline - time.perf_counter()
            if delay <= 0:
                deadline = time.perf_counter()
                delay = 0.0005
            self._stop.wait(delay)

    def _write_pending(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_write < WRITE_INTERVAL_SEC:
                return True
            dirty = self._dirty
            change_serial = self._change_serial
            pending = list(self._pending_csv)
            if not dirty and not pending:
                self._last_write = now
                return True
            doc_copy = json.loads(json.dumps(self.doc))

        json_ok = True
        csv_ok = True
        if dirty:
            json_ok = _write_json_atomic(self.path, doc_copy)
        if pending:
            try:
                self.event_path.parent.mkdir(parents=True, exist_ok=True)
                needs_header = not self.event_path.exists() or self.event_path.stat().st_size == 0
                with self.event_path.open("a", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
                    if needs_header:
                        writer.writeheader()
                    for row in pending:
                        formatted = dict(row)
                        for key in (
                            "actor", "owner_base", "victim_base", "property_a", "property_b",
                            "caller_lr", "route_arg", "result_ptr_a", "result_ptr_b",
                            "runtime_status_20", "phase_property_a", "phase_property_b",
                            "object_flags_10", "object_flags_14", "object_flags_18",
                            "object_flags_1c", "object_flags_24", "object_flags_28",
                            "object_flags_2c",
                        ):
                            formatted[key] = _hex32(formatted.get(key))
                        formatted["owner_action_id"] = f"0x{_safe_int(formatted.get('owner_action_id')):04X}"
                        writer.writerow(formatted)
            except Exception:
                csv_ok = False

        with self._lock:
            if json_ok and self._change_serial == change_serial:
                self._dirty = False
            if csv_ok and pending:
                del self._pending_csv[:len(pending)]
            self._last_write = now
        return bool(json_ok and csv_ok)

    @staticmethod
    def _debug_hex(value: Any, width: int = 8) -> str:
        return f"0x{_safe_int(value) & 0xFFFFFFFF:0{int(width)}X}"

    @classmethod
    def _debug_operation(cls, operation: dict) -> dict:
        return {
            "offset": cls._debug_hex(operation.get("offset"), 4),
            "address": cls._debug_hex(operation.get("address")),
            "op": str(operation.get("operation_name") or operation.get("operation") or ""),
            "field": str(operation.get("field_name") or ""),
            "field_id": cls._debug_hex(operation.get("field_id"), 4),
            "value": cls._debug_hex(operation.get("value")),
        }

    def _print_debug_event(self, payload: dict) -> None:
        if not self._emit_console:
            return
        try:
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        except Exception as exc:
            encoded = json.dumps({"event": "encode_error", "error": repr(exc)}, separators=(",", ":"))
        print(f"[ATKPROP_JSON] {encoded}", flush=True)

    def _emit_native_property_debug(
        self,
        definitions: Dict[str, dict],
        projectile_rows_by_slot: Dict[str, list[dict]],
        metas: Dict[str, _SlotMeta],
        *,
        frame: int,
    ) -> None:
        """Emit copy/paste friendly JSONL only when native values change.

        This is intentionally raw. It does not merge profile data or observed
        frame data. Fighter-script events include every native SET/OR/CLEAR
        operation. Projectile events include both raw runtime words so layout
        mistakes can be diagnosed from user captures.
        """
        if not self._emit_console:
            return

        definition_events: list[tuple[str, str, dict]] = []
        for slot, definition in sorted((definitions or {}).items()):
            if not isinstance(definition, dict):
                continue
            meta = metas.get(str(slot))
            phases = []
            for phase in definition.get("phases") or []:
                if not isinstance(phase, dict):
                    continue
                hit_result = phase.get("hit_result_raw")
                reaction = phase.get("a_result_code", phase.get("hit_reaction"))
                result_flags = phase.get("a_result_flags_raw", phase.get("hit_result_raw"))
                phases.append({
                    "index": _safe_int(phase.get("phase_index")),
                    "script_offset": self._debug_hex(phase.get("script_offset"), 4),
                    "a_initial": self._debug_hex(phase.get("property_a_initial")),
                    "a_class": self._debug_hex(phase.get("property_a")),
                    "a_initial_unknown": self._debug_hex(phase.get("property_a_initial_unknown_mask")),
                    "a_final": self._debug_hex(phase.get("property_a_final", phase.get("property_a"))),
                    "a_final_unknown": self._debug_hex(phase.get("property_a_final_unknown_mask")),
                    "a_all_or": self._debug_hex(phase.get("property_a_all_or_mask")),
                    "a_all_clear": self._debug_hex(phase.get("property_a_all_clear_mask")),
                    "a_post_result_or": self._debug_hex(phase.get("property_a_post_result_or_mask")),
                    "b_initial": self._debug_hex(phase.get("property_b_initial")),
                    "b_final": self._debug_hex(phase.get("property_b")),
                    "b_unknown": self._debug_hex(phase.get("property_b_unknown_mask")),
                    "result_clear": self._debug_hex(phase.get("result_clear_mask")),
                    "result_raw": None if hit_result is None else self._debug_hex(hit_result),
                    "a_result_flags": None if result_flags is None else self._debug_hex(result_flags),
                    "a_result_code": None if reaction is None else self._debug_hex(reaction, 6),
                    "reaction": None if reaction is None else self._debug_hex(reaction, 6),
                    "a_addr": self._debug_hex(phase.get("property_a_addr")),
                    "b_addr": self._debug_hex(phase.get("property_b_addr")),
                    "result_addr": self._debug_hex(phase.get("hit_result_addr")),
                    "ops": [
                        self._debug_operation(operation)
                        for operation in (phase.get("native_operations") or [])
                        if isinstance(operation, dict)
                    ],
                })
            payload = {
                "event": "fighter_script",
                "frame": int(frame),
                "slot": str(slot),
                "character": str(definition.get("owner_name") or (meta.name if meta else "") or ""),
                "fighter_base": self._debug_hex(definition.get("owner_base")),
                "action": self._debug_hex(definition.get("owner_action_id"), 4),
                "action_source": str(definition.get("owner_action_source") or ""),
                "move": str(definition.get("owner_action_name") or (meta.action_name if meta else "") or ""),
                "chr_table": self._debug_hex(definition.get("definition_chr_tbl")),
                "table_generation": _safe_int(definition.get("definition_table_generation")),
                "move_root": self._debug_hex(definition.get("definition_move_root")),
                "scan_size": self._debug_hex(definition.get("definition_scan_size"), 4),
                "phase_count": len(phases),
                "phases": phases,
            }
            signature = json.dumps(
                {k: v for k, v in payload.items() if k not in {"frame", "action_source"}},
                sort_keys=True,
                separators=(",", ":"),
            )
            definition_events.append((str(slot), signature, payload))

        projectile_events: list[tuple[str, str, dict]] = []
        current_projectile_keys: set[str] = set()
        for slot, rows in sorted((projectile_rows_by_slot or {}).items()):
            meta = metas.get(str(slot))
            for row in rows or []:
                if not isinstance(row, dict) or not bool(row.get("projectile_live", True)):
                    continue
                actor = _safe_int(row.get("actor"))
                linked = _safe_int(row.get("linked"))
                projectile_id = _safe_int(row.get("projectile_id"))
                allocation_epoch = _safe_int(row.get("allocation_epoch"), 1)
                key = f"{slot}:{actor:08X}:{linked:08X}:{projectile_id:04X}:{allocation_epoch}"
                current_projectile_keys.add(key)
                payload = {
                    "event": "attack_actor_native",
                    "object_kind": "spawned_attack_actor",
                    "state": "live",
                    "frame": int(frame),
                    "slot": str(slot),
                    "character": str((meta.name if meta else "") or ""),
                    "owner_base": self._debug_hex(row.get("owner_base")),
                    "owner_action": self._debug_hex(row.get("owner_action_id"), 4),
                    "owner_move": str(row.get("owner_action_name") or (meta.action_name if meta else "") or ""),
                    "move": str(row.get("attack_actor_name") or row.get("projectile_action_name") or row.get("owner_action_name") or ""),
                    "actor_index": _safe_int(row.get("attack_actor_index", row.get("projectile_index"))),
                    "actor_id": self._debug_hex(row.get("attack_actor_id", row.get("projectile_id")), 4),
                    "projectile_index": _safe_int(row.get("projectile_index")),
                    "projectile_id": self._debug_hex(row.get("projectile_id"), 4),
                    "allocation_epoch": allocation_epoch,
                    "lifetime_key": str(row.get("lifetime_key") or ""),
                    "actor": self._debug_hex(actor),
                    "linked": self._debug_hex(linked),
                    "registry": str(row.get("registry_source") or ""),
                    "layout": str(row.get("property_layout") or ""),
                    "raw_80": self._debug_hex(row.get("raw_property_80")),
                    "raw_84": self._debug_hex(row.get("raw_property_84")),
                    "a": self._debug_hex(row.get("property_a")),
                    "b": self._debug_hex(row.get("property_b")),
                    "a_unknown": self._debug_hex(row.get("property_a_initial_unknown_mask")),
                    "b_unknown": self._debug_hex(row.get("property_b_unknown_mask")),
                    "next_a": self._debug_hex(row.get("phase_property_a")),
                    "next_b": self._debug_hex(row.get("phase_property_b")),
                    "next_b_unknown": self._debug_hex(row.get("phase_property_b_unknown_mask")),
                    "runtime_status_20": self._debug_hex(row.get("runtime_status_20")),
                    "target": self._debug_hex(row.get("target")),
                    "linked_owner": self._debug_hex(row.get("linked_owner")),
                    "inactive_generic_actor": bool(row.get("inactive_generic_actor")),
                    "cleanup_observed": bool(row.get("cleanup_observed")),
                }
                # Owner actions continue changing after a spawned actor appears.
                # They are useful context in the payload, but they must not turn
                # an unchanged actor definition into a fake property update.
                signature = json.dumps(
                    {
                        k: v for k, v in payload.items()
                        if k not in {"frame", "state", "owner_action", "owner_move"}
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                projectile_events.append((key, signature, payload))

        events_to_print: list[dict] = []
        with self._lock:
            for slot, signature, payload in definition_events:
                if self._console_definition_signatures.get(slot) != signature:
                    self._console_definition_signatures[slot] = signature
                    events_to_print.append(payload)

            for key, signature, payload in projectile_events:
                previous = self._console_projectile_signatures.get(key)
                if previous != signature:
                    payload["state"] = "spawn" if previous is None else "update"
                    self._console_projectile_signatures[key] = signature
                    events_to_print.append(payload)

            for key in sorted(set(self._console_projectile_signatures) - current_projectile_keys):
                slot, actor_hex, linked_hex, projectile_hex, epoch_text = key.split(":", 4)
                events_to_print.append({
                    "event": "attack_actor_native",
                    "object_kind": "spawned_attack_actor",
                    "state": "despawn",
                    "frame": int(frame),
                    "slot": slot,
                    "actor": f"0x{actor_hex}",
                    "linked": f"0x{linked_hex}",
                    "actor_id": f"0x{projectile_hex}",
                    "projectile_id": f"0x{projectile_hex}",
                    "allocation_epoch": _safe_int(epoch_text, 0),
                    "lifetime_key": f"{slot}:{actor_hex}:{linked_hex}:{projectile_hex}:E{epoch_text}",
                })
                self._console_projectile_signatures.pop(key, None)

            active_slots = set(definitions)
            for slot in list(self._console_definition_signatures):
                if slot not in active_slots:
                    # Clear silently so repeating the same move after idle logs
                    # again. The user asked for flag dumps, not idle spam.
                    self._console_definition_signatures.pop(slot, None)

        for payload in events_to_print:
            self._print_debug_event(payload)

    def _resolved_action_name(self, meta: _SlotMeta, action_id: int) -> str:
        """Resolve the captured action ID directly, avoiding one-poll name lag."""
        action = _safe_int(action_id, 0)
        if not action:
            return str(meta.action_name or "")
        try:
            mapped = str(lookup_move_name(action, int(meta.char_id)) or "").strip()
        except TypeError:
            try:
                mapped = str(lookup_move_name(action) or "").strip()
            except Exception:
                mapped = ""
        except Exception:
            mapped = ""
        if mapped:
            return mapped
        cached = str(self._action_name_cache.get((int(meta.base), action), "") or "").strip()
        if cached:
            return cached
        if _safe_int(meta.action_id, 0) == action:
            return str(meta.action_name or "")
        return f"Action {action:04X}"

    @staticmethod
    def _generic_actor_name(value: str) -> bool:
        """Return True for owner-state labels that are not actor identities."""
        text = str(value or "").strip().lower()
        if not text:
            return True
        if text.startswith("action ") or text.startswith("flag_"):
            return True
        exact = {
            "idle", "forward", "backward", "crouching", "crouched",
            "jump", "pre jump", "landing", "back dash", "dash",
            "tag out", "dhc tag out", "assist leave", "assist enter",
        }
        if text in exact:
            return True
        return any(text.startswith(prefix) for prefix in (
            "idle ", "jump ", "landing ", "pre jump ", "back dash ",
            "forward ", "backward ", "crouch ",
        ))

    def _attack_actor_name_candidate(
        self,
        meta: _SlotMeta | None,
        projectile_id: int,
        owner_action_id: int,
    ) -> str:
        """Choose the most stable human name available for one actor lifetime.

        Many actor IDs equal their spawning action ID, but some are tiny local
        object IDs. Prefer a meaningful ID-derived move when the ID is in the
        action range, then fall back to the owner's current attack action. Never
        replace a meaningful lifetime name with idle, movement, or transition
        states sampled after the actor has already spawned.
        """
        if meta is None:
            return ""
        pid = _safe_int(projectile_id, 0)
        if pid >= 0x0100:
            pid_name = self._resolved_action_name(meta, pid)
            if not self._generic_actor_name(pid_name):
                return pid_name
        owner_name = self._resolved_action_name(meta, owner_action_id)
        if not self._generic_actor_name(owner_name):
            return owner_name
        return ""

    @staticmethod
    def _projectile_cleanup_candidate(row: dict, previous: dict | None) -> bool:
        """Recognize a generic reset only inside the same allocation epoch.

        A=0x09/B=0x40 is not globally discarded. It is treated as cleanup only
        when this exact actor lifetime previously held a more specific native
        definition. A fresh lifetime that begins with 0x09/0x40 remains visible
        as an unresolved inactive actor instead of inheriting stale properties.
        """
        if not previous:
            return False
        if _safe_int(row.get("allocation_epoch"), 0) != _safe_int(previous.get("allocation_epoch"), -1):
            return False
        if _safe_int(row.get("property_a"), 0) != 0x00000009:
            return False
        if _safe_int(row.get("property_b"), 0) != 0x00000040:
            return False
        return (
            _safe_int(previous.get("property_a"), 0) != 0x00000009
            or _safe_int(previous.get("property_b"), 0) != 0x00000040
        )

    def _resolve_move_definitions(self, metas: Dict[str, _SlotMeta]) -> Dict[str, dict]:
        """Resolve native Property A/B commands for each fighter's live action.

        The recomp path is authoritative here: fighter+0x1E8 is the action ID,
        fighter+0x1E0 is the character action table, and script fields 0x240 /
        0x244 write Property A / Property B. Snapshot action fields are retained
        only as a fallback because they may have been sampled earlier in the
        frame than this profiler update.
        """
        definitions: Dict[str, dict] = {}
        statuses: Dict[str, dict] = {}
        for slot, meta in metas.items():
            if not meta.base:
                statuses[str(slot)] = {"status": "NO_FIGHTER", "action_id": int(meta.action_id or 0)}
                continue

            direct_action = _safe_int(self._read_word(meta.base + FIGHTER_ACTION_ID), 0)
            candidates: list[tuple[str, int]] = []
            for source, action in (("fighter+0x1E8", direct_action), ("snapshot", int(meta.action_id or 0))):
                if action and all(existing != action for _name, existing in candidates):
                    candidates.append((source, action))
            if not candidates:
                statuses[str(slot)] = {"status": "NO_ACTION", "action_id": 0}
                continue

            native: dict = {}
            attempts: list[dict] = []
            resolved_action = 0
            resolved_source = ""
            for source, action in candidates:
                try:
                    attempt = resolve_live_attack_definition(
                        meta.base,
                        action,
                        read_u32=self._read_word,
                        read_block=self._read_bytes,
                    )
                except Exception as exc:
                    attempt = {"status": "LOOKUP_EXCEPTION", "error": repr(exc), "action_id": action}
                attempt = dict(attempt or {})
                attempt["action_source"] = source
                attempts.append(attempt)
                if attempt.get("status") == "OK" and attempt.get("property_a") is not None:
                    native = attempt
                    resolved_action = action
                    resolved_source = source
                    break

            if not native:
                best = attempts[0] if attempts else {"status": "NO_ACTION"}
                best = dict(best)
                best["attempts"] = attempts
                statuses[str(slot)] = best
                continue

            native["attempts"] = attempts
            statuses[str(slot)] = dict(native)
            packed = int(native["property_a"]) & 0xFF
            prop_b = _safe_int(native.get("property_b"), 0)
            decoded = decode_property_a(packed)
            decoded_b = decode_property_b(prop_b)
            phases = list(native.get("phases") or [])
            phase_rows: list[dict] = []
            for phase_index, phase in enumerate(phases, 1):
                if not isinstance(phase, dict):
                    continue
                phase_a_raw = _safe_int(phase.get("property_a"), 0) & 0xFF
                phase_b_raw = _safe_int(phase.get("property_b"), 0) & 0xFFFFFFFF
                phase_a_decoded = decode_property_a(phase_a_raw)
                phase_b_decoded = decode_property_b(phase_b_raw)
                native_operations = [
                    dict(operation)
                    for operation in (phase.get("operations") or [])
                    if isinstance(operation, dict)
                ]
                phase_rows.append({
                    "phase_index": phase_index,
                    "source": "native_action_script",
                    "property_a": phase_a_raw,
                    "property_a_text": phase_a_decoded.get("text", ""),
                    "property_a_initial": _safe_int(phase.get("property_a_initial"), phase_a_raw),
                    "property_a_initial_unknown_mask": _safe_int(phase.get("property_a_initial_unknown_mask"), 0),
                    "property_a_final": _safe_int(phase.get("property_a_final"), phase_a_raw),
                    "property_a_final_unknown_mask": _safe_int(phase.get("property_a_final_unknown_mask"), 0),
                    "property_a_all_or_mask": _safe_int(phase.get("property_a_all_or_mask"), 0),
                    "property_a_all_clear_mask": _safe_int(phase.get("property_a_all_clear_mask"), 0),
                    "property_a_post_result_or_mask": _safe_int(phase.get("property_a_post_result_or_mask"), 0),
                    "property_b": phase_b_raw,
                    "property_b_initial": _safe_int(phase.get("property_b_initial"), phase_b_raw),
                    "property_b_text": phase_b_decoded.get("text", ""),
                    "property_b_unknown_mask": phase_b_decoded.get("unknown_mask", 0),
                    "result_clear_mask": _safe_int(phase.get("result_clear_mask"), 0),
                    "hit_result_raw": phase.get("hit_result_raw"),
                    "a_result_flags_raw": phase.get("a_result_flags_raw", phase.get("hit_result_raw")),
                    "a_result_code": phase.get("a_result_code", phase.get("hit_reaction")),
                    "hit_reaction": phase.get("hit_reaction"),
                    "hit_result_addr": _safe_int(phase.get("hit_result_addr"), 0),
                    "operation_count": _safe_int(phase.get("operation_count"), len(native_operations)),
                    "native_operations": native_operations,
                    "property_a_modifiers": [
                        dict(operation)
                        for operation in (phase.get("property_a_modifiers") or [])
                        if isinstance(operation, dict)
                    ],
                    "property_b_modifiers": [
                        dict(operation)
                        for operation in (phase.get("property_b_modifiers") or [])
                        if isinstance(operation, dict)
                    ],
                    "script_offset": _safe_int(phase.get("script_offset"), 0),
                    "property_a_addr": _safe_int(phase.get("property_a_addr"), 0),
                    "property_b_addr": _safe_int(phase.get("property_b_addr"), 0),
                })
            next_phase = phases[1] if len(phases) > 1 else {}
            phase_a = _safe_int(next_phase.get("property_a"), 0)
            phase_b = _safe_int(next_phase.get("property_b"), 0)
            decoded_phase_a = decode_property_a(phase_a) if phase_a else {"text": ""}
            decoded_phase_b = decode_property_b(phase_b) if phase_b else {"text": "", "unknown_mask": 0}
            definitions[str(slot)] = {
                "packet_state": "CURRENT MOVE",
                "packet_source": "move_definition",
                "owner_slot": str(slot),
                "owner_base": int(meta.base),
                "owner_char_id": int(meta.char_id),
                "owner_name": str(meta.name or ""),
                "owner_action_id": int(resolved_action),
                "owner_action_source": resolved_source,
                "owner_action_name": self._resolved_action_name(meta, resolved_action),
                "property_a": packed,
                "property_a_text": decoded["text"],
                "property_b": prop_b,
                "property_b_text": decoded_b["text"],
                "property_b_unknown_mask": decoded_b["unknown_mask"],
                "phase_property_a": phase_a,
                "phase_property_a_text": decoded_phase_a.get("text", ""),
                "phase_property_b": phase_b,
                "phase_property_b_text": decoded_phase_b.get("text", ""),
                "phase_property_b_unknown_mask": decoded_phase_b.get("unknown_mask", 0),
                "runtime_status_20": 0,
                "base_damage": 0,
                "victim_slot": "",
                "definition_status": str(native.get("status") or "OK"),
                "definition_chr_tbl": _safe_int(native.get("chr_tbl"), 0),
                "definition_table_generation": _safe_int(native.get("table_generation"), 0),
                "definition_move_root": _safe_int(native.get("move_root"), 0),
                "definition_property_a_addr": _safe_int(native.get("property_a_addr"), 0),
                "definition_property_b_addr": _safe_int(native.get("property_b_addr"), 0),
                "definition_phase_count": _safe_int(native.get("phase_count"), 1),
                "phases": phase_rows,
                "definition_scan_size": _safe_int(native.get("scan_size"), 0),
                "owner_point_active": meta.point_active,
                "owner_combo_lane_active": meta.combo_lane_active,
            }
        with self._lock:
            self._definition_status_by_slot = statuses
        return definitions

    def update(self, snaps: dict[str, dict], *, frame: int = 0, now: Optional[float] = None) -> bool:
        del now
        metas: Dict[str, _SlotMeta] = {}
        for slot, snap in (snaps or {}).items():
            if not isinstance(snap, dict):
                continue
            base = _safe_int(snap.get("base"), 0)
            if not base:
                continue
            action_id = 0
            for key in ("mv_id_display", "attA", "timing_action_id", "move_id", "attB"):
                action_id = _safe_int(snap.get(key), 0)
                if action_id:
                    break
            point_value = snap.get("damage_point_active")
            lane_value = snap.get("damage_combo_lane_active", snap.get("damage_baroque_permission"))
            metas[str(slot)] = _SlotMeta(
                slot=str(slot),
                base=base,
                char_id=_safe_int(snap.get("id"), 0),
                name=str(snap.get("name") or ""),
                action_id=action_id,
                action_name=str(snap.get("mv_label_display") or snap.get("mv_label") or ""),
                point_active=None if point_value is None else bool(point_value),
                combo_lane_active=None if lane_value is None else bool(lane_value),
            )

        current_definitions = self._resolve_move_definitions(metas)

        # Spawned attack actors leave the fighter action script and remain in a
        # persistent live actor table. Harvest their linked native A/B words
        # independently, then group them under the fighter that owns them.
        try:
            raw_projectiles = collect_live_projectile_properties(
                {slot: meta.base for slot, meta in metas.items()},
                read_u32=self._read_word,
                read_block=self._read_bytes,
            )
        except Exception as exc:
            raw_projectiles = []
            with self._lock:
                self._sampler_error = f"spawned attack actor read: {exc!r}"
        projectile_rows_by_slot: Dict[str, list[dict]] = {}

        # Resolve one allocation epoch per currently registered actor identity.
        # The same actor/link/id tuple can be reused later, so lifetime identity
        # is raw identity plus this epoch, not the memory addresses alone.
        raw_keys: set[tuple[str, int, int, int]] = set()
        for raw_row in raw_projectiles:
            if not isinstance(raw_row, dict):
                continue
            owner_slot = str(raw_row.get("owner_slot") or "")
            if owner_slot not in metas:
                continue
            raw_keys.add((
                owner_slot,
                _safe_int(raw_row.get("actor"), 0),
                _safe_int(raw_row.get("linked"), 0),
                _safe_int(raw_row.get("projectile_id"), 0),
            ))
        with self._lock:
            for raw_key in raw_keys:
                if raw_key not in self._attack_actor_live_epoch:
                    epoch = self._attack_actor_epoch_counter.get(raw_key, 0) + 1
                    self._attack_actor_epoch_counter[raw_key] = epoch
                    self._attack_actor_live_epoch[raw_key] = epoch
            for stale_key in list(self._attack_actor_live_epoch):
                if stale_key not in raw_keys:
                    self._attack_actor_live_epoch.pop(stale_key, None)

        for raw_row in raw_projectiles:
            if not isinstance(raw_row, dict):
                continue
            owner_slot = str(raw_row.get("owner_slot") or "")
            if owner_slot not in metas:
                continue
            prop_a = _safe_int(raw_row.get("property_a"), 0) & 0xFFFFFFFF
            prop_b = _safe_int(raw_row.get("property_b"), 0) & 0xFFFFFFFF
            phase_a = _safe_int(raw_row.get("phase_property_a"), 0) & 0xFFFFFFFF
            phase_b = _safe_int(raw_row.get("phase_property_b"), 0) & 0xFFFFFFFF
            decoded_a = decode_property_a(prop_a)
            decoded_b = decode_property_b(prop_b)
            decoded_phase_a = decode_property_a(phase_a) if phase_a else {"text": ""}
            decoded_phase_b = decode_property_b(phase_b) if phase_b else {"text": "", "unknown_mask": 0}
            row = dict(raw_row)
            owner_meta = metas.get(owner_slot)
            projectile_id = _safe_int(raw_row.get("projectile_id"), 0)
            raw_identity = (
                owner_slot,
                _safe_int(raw_row.get("actor"), 0),
                _safe_int(raw_row.get("linked"), 0),
                projectile_id,
            )
            with self._lock:
                allocation_epoch = self._attack_actor_live_epoch.get(raw_identity, 1)
            lifetime_identity = raw_identity + (allocation_epoch,)
            live_owner_action = _safe_int(raw_row.get("owner_action_id"), owner_meta.action_id if owner_meta else 0)
            candidate_name = self._attack_actor_name_candidate(
                owner_meta, projectile_id, live_owner_action
            )
            with self._lock:
                actor_action_name = str(self._attack_actor_name_by_lifetime.get(lifetime_identity, "") or "")
                # Allow a generic first-frame observation to be promoted once a
                # meaningful move identity becomes available, but never rename a
                # meaningful actor after the owner returns to movement or idle.
                if candidate_name and (not actor_action_name or self._generic_actor_name(actor_action_name)):
                    actor_action_name = candidate_name
                    self._attack_actor_name_by_lifetime[lifetime_identity] = actor_action_name
                if len(self._attack_actor_name_by_lifetime) > 512:
                    for stale_key in list(self._attack_actor_name_by_lifetime)[:128]:
                        self._attack_actor_name_by_lifetime.pop(stale_key, None)
            if not actor_action_name:
                actor_action_name = f"Spawned Actor {projectile_id:04X}" if projectile_id else "Spawned Attack Actor"
            row.update({
                "packet_state": "LIVE ATTACK ACTOR",
                "packet_source": "live_attack_actor",
                "projectile_live": True,
                "attack_actor_live": True,
                "allocation_epoch": allocation_epoch,
                "lifetime_key": f"{owner_slot}:{raw_identity[1]:08X}:{raw_identity[2]:08X}:{projectile_id:04X}:E{allocation_epoch}",
                "owner_action_id": live_owner_action,
                "owner_action_name": (
                    self._resolved_action_name(owner_meta, live_owner_action)
                    if owner_meta else ""
                ),
                # Compatibility field names are retained for existing payload
                # consumers, but these rows represent any spawned attack actor.
                "projectile_action_id": projectile_id,
                "projectile_action_name": actor_action_name,
                "attack_actor_id": projectile_id,
                "attack_actor_name": actor_action_name,
                "inactive_generic_actor": bool(prop_a == 0x00000009 and prop_b == 0x00000040),
                "property_a_text": decoded_a.get("text", ""),
                "property_a_initial_unknown_mask": decoded_a.get("high_flags", 0),
                "property_b_text": decoded_b.get("text", ""),
                "property_b_unknown_mask": decoded_b.get("unknown_mask", 0),
                "phase_property_a_text": decoded_phase_a.get("text", ""),
                "phase_property_b_text": decoded_phase_b.get("text", ""),
                "phase_property_b_unknown_mask": decoded_phase_b.get("unknown_mask", 0),
            })
            projectile_rows_by_slot.setdefault(owner_slot, []).append(row)
        for owner_slot, rows_for_slot in projectile_rows_by_slot.items():
            for index, row in enumerate(rows_for_slot, 1):
                row["projectile_index"] = index
                row["attack_actor_index"] = index

        self._emit_native_property_debug(
            current_definitions,
            projectile_rows_by_slot,
            metas,
            frame=int(frame),
        )

        with self._lock:
            # Keep a short, explicitly labeled native attack-actor latch. Fast
            # attack actors can disappear between the collision frame and the
            # next readable HUD frame; retaining the last direct actor sample
            # makes the harvested A/B words inspectable without pretending the
            # actor is still live. A new unrelated native fighter action clears
            # the old projectile latch rather than combining two moves.
            for slot, meta in metas.items():
                active_rows = projectile_rows_by_slot.get(slot, [])
                if active_rows:
                    previous_by_identity = {
                        (
                            _safe_int(row.get("actor")),
                            _safe_int(row.get("linked")),
                            _safe_int(row.get("projectile_id")),
                            _safe_int(row.get("allocation_epoch")),
                        ): row
                        for row in self._latched_projectiles_by_slot.get(slot, [])
                    }
                    for row in active_rows:
                        identity = (
                            _safe_int(row.get("actor")),
                            _safe_int(row.get("linked")),
                            _safe_int(row.get("projectile_id")),
                            _safe_int(row.get("allocation_epoch")),
                        )
                        previous = previous_by_identity.get(identity, {})
                        if self._projectile_cleanup_candidate(row, previous):
                            raw_cleanup = {
                                "cleanup_property_a": _safe_int(row.get("property_a")),
                                "cleanup_property_b": _safe_int(row.get("property_b")),
                                "cleanup_target": _safe_int(row.get("target")),
                                "cleanup_observed": True,
                            }
                            preserved = dict(previous)
                            preserved.update(raw_cleanup)
                            preserved["runtime_status_20"] = _safe_int(row.get("runtime_status_20"))
                            preserved["target"] = _safe_int(row.get("target"))
                            row.clear()
                            row.update(preserved)
                        row["capture_gui_frame"] = _safe_int(previous.get("capture_gui_frame"), frame)
                        row["last_seen_gui_frame"] = int(frame)
                        row["packet_state"] = "LIVE ATTACK ACTOR"
                        row["packet_source"] = "live_attack_actor"
                        row["projectile_live"] = True
                        row["attack_actor_live"] = True
                    self._latched_projectiles_by_slot[slot] = [dict(row) for row in active_rows]
                    continue

                previous_rows = self._latched_projectiles_by_slot.get(slot, [])
                if not previous_rows:
                    continue
                newest_seen = max(_safe_int(row.get("last_seen_gui_frame"), -999999) for row in previous_rows)
                age = max(0, int(frame) - newest_seen)
                current_definition = current_definitions.get(slot)
                previous_action = _safe_int(previous_rows[0].get("owner_action_id"), 0)
                current_action = _safe_int((current_definition or {}).get("owner_action_id"), meta.action_id)
                unrelated_current_move = bool(
                    current_definition
                    and previous_action
                    and current_action
                    and previous_action != current_action
                )
                if age > PROJECTILE_LATCH_FRAMES or unrelated_current_move:
                    self._latched_projectiles_by_slot.pop(slot, None)
                    continue
                held_rows = []
                for row in previous_rows:
                    held = dict(row)
                    held["packet_state"] = "LAST ATTACK ACTOR"
                    held["packet_source"] = "live_attack_actor_latched"
                    held["projectile_live"] = False
                    held["attack_actor_live"] = False
                    held["age_frames"] = age
                    held_rows.append(held)
                projectile_rows_by_slot[slot] = held_rows

            for slot in list(self._latched_projectiles_by_slot):
                if slot not in metas:
                    self._latched_projectiles_by_slot.pop(slot, None)

            self._slot_meta = metas
            for meta in metas.values():
                if meta.base and meta.action_id and meta.action_name:
                    self._action_name_cache[(meta.base, meta.action_id)] = meta.action_name
            if len(self._action_name_cache) > 2048:
                # Keep insertion order bounded without coupling the cache to a
                # particular character roster size.
                for key in list(self._action_name_cache)[:512]:
                    self._action_name_cache.pop(key, None)
            self._gui_frame = int(frame)

            # Static move definitions are visible only while an attack action is
            # current. Latch the latest resolved action so the HUD remains
            # readable after the move returns to idle, but label it LAST MOVE so
            # it is never mistaken for a live runtime packet. Repeating the same
            # move after idle receives a new sequence number.
            resolved_definitions: Dict[str, dict] = {}
            now_active = set(current_definitions)
            for slot, meta in metas.items():
                current = current_definitions.get(slot)
                if current is not None:
                    signature = (
                        int(meta.base),
                        int(meta.char_id),
                        _safe_int(current.get("owner_action_id"), meta.action_id),
                        _safe_int(current.get("property_a")),
                    )
                    previous = self._latched_definition_by_slot.get(slot)
                    repeated_after_idle = slot not in self._definition_active_slots
                    changed_action = self._definition_signature_by_slot.get(slot) != signature
                    if previous is None or repeated_after_idle or changed_action:
                        self._definition_sequence = (self._definition_sequence + 1) & 0xFFFFFFFF
                        if self._definition_sequence == 0:
                            self._definition_sequence = 1
                        current["event_sequence"] = self._definition_sequence
                        current["capture_gui_frame"] = int(frame)
                    else:
                        current["event_sequence"] = _safe_int(previous.get("event_sequence"))
                        current["capture_gui_frame"] = _safe_int(previous.get("capture_gui_frame"), frame)
                    current["packet_state"] = "CURRENT MOVE"
                    current["packet_source"] = "move_definition"
                    self._definition_signature_by_slot[slot] = signature
                    self._latched_definition_by_slot[slot] = dict(current)
                    resolved_definitions[slot] = dict(current)
                    continue

                # Projectile-only actions must not resurrect the previous
                # fighter script behind the live projectile. Once a spawned
                # actor is observed, discard that stale fighter-definition latch.
                if projectile_rows_by_slot.get(slot):
                    self._latched_definition_by_slot.pop(slot, None)
                    self._definition_signature_by_slot.pop(slot, None)
                    continue

                previous = self._latched_definition_by_slot.get(slot)
                if previous is not None and (
                    _safe_int(previous.get("owner_base")) == int(meta.base)
                    and _safe_int(previous.get("owner_char_id")) == int(meta.char_id)
                ):
                    held = dict(previous)
                    held["packet_state"] = "LAST MOVE"
                    held["packet_source"] = "move_definition_latched"
                    resolved_definitions[slot] = held
                else:
                    self._latched_definition_by_slot.pop(slot, None)
                    self._definition_signature_by_slot.pop(slot, None)

            for slot in list(self._latched_definition_by_slot):
                if slot not in metas:
                    self._latched_definition_by_slot.pop(slot, None)
                    self._definition_signature_by_slot.pop(slot, None)

            self._definition_active_slots = now_active
            self._definition_by_slot = resolved_definitions

        # The telemetry scheduler already runs off the render thread. Contact
        # capture is experimental and disabled by the normal application path.
        if self._enable_resolver_hook:
            try:
                self.sample_once()
            except Exception as exc:
                with self._lock:
                    self._sampler_error = repr(exc)
                    self._resolver_hook_state = "SAMPLER ERROR"
                    self._resolver_hook_error = repr(exc)

        with self._lock:
            active_copy = {slot: [dict(row) for row in rows] for slot, rows in self._active_by_slot.items()}
            latest_copy = {slot: dict(row) for slot, row in self._latest_by_slot.items()}
            definition_copy = {slot: dict(row) for slot, row in self._definition_by_slot.items()}
            definition_status_copy = {slot: dict(row) for slot, row in self._definition_status_by_slot.items()}
            pool_manager = self._pool_manager
            pool_free_head = self._pool_free_head
            pool_status = self._pool_status
            hook_state = self._resolver_hook_state
            hook_error = self._resolver_hook_error
            lost_events = self._resolver_lost_events
            sampler_error = self._sampler_error
            changed = bool(self._changed_pending)
            self._changed_pending = False

        for slot, snap in (snaps or {}).items():
            if not isinstance(snap, dict):
                continue
            rows = active_copy.get(str(slot), [])
            contact = latest_copy.get(str(slot))
            definition = definition_copy.get(str(slot))
            current_meta = metas.get(str(slot))
            current_action = int(current_meta.action_id) if current_meta else 0
            contact_action = _safe_int((contact or {}).get("owner_action_id"), 0)
            contact_frame = _safe_int((contact or {}).get("capture_gui_frame"), frame)
            contact_age = max(0, int(frame) - contact_frame)
            use_contact = bool(contact and contact_action == current_action and contact_age <= 90)
            projectiles = [dict(row) for row in projectile_rows_by_slot.get(str(slot), [])]
            definition_is_current = bool(definition and definition.get("packet_source") == "move_definition")
            # A live projectile is authoritative for spawned-actor-only actions. Do
            # not leave the previous fighter script (for example Tatsu Super)
            # on screen while Shinkuu's actor is active.
            if projectiles and not definition_is_current and not use_contact:
                latest = None
            else:
                latest = contact if use_contact else definition
            snap["attack_property_packet_count"] = len(rows)
            snap["attack_property_projectile_count"] = len(projectiles)
            snap["attack_property_projectiles"] = projectiles
            snap["attack_property_actor_count"] = len(projectiles)
            snap["attack_property_actors"] = projectiles
            snap["attack_property_pool_manager"] = pool_manager or None
            snap["attack_property_pool_free_head"] = pool_free_head or None
            snap["attack_property_pool_status"] = pool_status
            snap["attack_property_resolver_hook_state"] = hook_state
            snap["attack_property_resolver_hook_error"] = hook_error
            snap["attack_property_resolver_lost_events"] = lost_events
            snap["attack_property_sampler_error"] = sampler_error
            definition_status = definition_status_copy.get(str(slot), {})
            snap["attack_property_definition_status"] = str(definition_status.get("status") or "")
            snap["attack_property_definition_error"] = str(definition_status.get("error") or "")
            snap["attack_property_definition_chr_tbl"] = _safe_int(definition_status.get("chr_tbl"), 0) or None
            snap["attack_property_definition_move_root"] = _safe_int(definition_status.get("move_root"), 0) or None
            snap["attack_property_definition_scan_size"] = _safe_int(definition_status.get("scan_size"), 0) or None
            snap["attack_property_definition_action_id"] = _safe_int(definition_status.get("action_id"), 0) or None
            snap["attack_property_definition_action_source"] = str(definition_status.get("action_source") or "")
            snap["attack_property_display_active"] = bool(latest or projectiles)
            live_projectiles = any(bool(row.get("projectile_live")) for row in projectiles)
            if projectiles and latest:
                display_source = (
                    "native_script_and_live_attack_actor"
                    if live_projectiles else "native_script_and_last_attack_actor"
                )
            elif projectiles:
                display_source = "live_attack_actor" if live_projectiles else "live_attack_actor_latched"
            else:
                display_source = str((latest or {}).get("packet_source") or "")
            snap["attack_property_display_source"] = display_source
            if latest:
                snap["attack_property_packet_state"] = str(latest.get("packet_state") or "CONTACT")
                snap["attack_property_packet_source"] = str(latest.get("packet_source") or "")
                snap["attack_property_packet_capture_frame"] = _safe_int(latest.get("capture_gui_frame"), frame)
                snap["attack_property_packet_age_frames"] = max(0, int(frame) - snap["attack_property_packet_capture_frame"])
                snap["attack_property_packet_action_id"] = _safe_int(latest.get("owner_action_id"))
                snap["attack_property_packet_action_name"] = str(latest.get("owner_action_name") or "")
                snap["attack_property_event_sequence"] = _safe_int(latest.get("event_sequence"))
                snap["attack_property_event_caller_lr"] = _safe_int(latest.get("caller_lr"))
                snap["attack_property_live_actor"] = _safe_int(latest.get("actor"))
                snap["attack_property_live_a"] = _safe_int(latest.get("property_a"))
                snap["attack_property_live_b"] = _safe_int(latest.get("property_b"))
                snap["attack_property_live_status20"] = _safe_int(latest.get("runtime_status_20"))
                snap["attack_property_live_damage"] = _safe_int(latest.get("authored_damage", latest.get("base_damage")))
                snap["attack_property_live_authored_damage"] = _safe_int(latest.get("authored_damage", latest.get("base_damage")))
                snap["attack_property_live_damage_calc_output"] = (
                    _safe_int(latest.get("damage_calc_output"))
                    if latest.get("native_damage_calc_complete") else None
                )
                snap["attack_property_live_damage_calc_aux"] = (
                    _safe_int(latest.get("damage_calc_aux"))
                    if latest.get("native_damage_calc_complete") else None
                )
                snap["attack_property_native_damage_calc_complete"] = bool(
                    latest.get("native_damage_calc_complete")
                )
                snap["attack_property_live_applied_damage"] = (
                    _safe_int(latest.get("applied_damage")) if latest.get("native_damage_complete") else None
                )
                snap["attack_property_live_resolved_damage"] = (
                    _safe_int(latest.get("resolved_damage")) if latest.get("native_damage_complete") else None
                )
                snap["attack_property_live_resolved_aux"] = (
                    _safe_int(latest.get("resolved_aux"))
                    if latest.get("native_damage_calc_complete") else None
                )
                snap["attack_property_native_damage_complete"] = bool(latest.get("native_damage_complete"))
                snap["attack_property_live_a_text"] = str(latest.get("property_a_text") or "")
                snap["attack_property_live_b_text"] = str(latest.get("property_b_text") or "")
                snap["attack_property_live_b_unknown"] = _safe_int(latest.get("property_b_unknown_mask"))
                snap["attack_property_live_phase_a"] = _safe_int(latest.get("phase_property_a"))
                snap["attack_property_live_phase_a_text"] = str(latest.get("phase_property_a_text") or "")
                snap["attack_property_live_phase_b"] = _safe_int(latest.get("phase_property_b"))
                snap["attack_property_live_phase_b_text"] = str(latest.get("phase_property_b_text") or "")
                snap["attack_property_live_phase_b_unknown"] = _safe_int(latest.get("phase_property_b_unknown_mask"))
                snap["attack_property_phase_count"] = max(
                    1,
                    _safe_int(latest.get("definition_phase_count"), len(latest.get("phases") or []) or 1),
                )
                snap["attack_property_phases"] = [
                    dict(row) for row in (latest.get("phases") or []) if isinstance(row, dict)
                ]
                snap["attack_property_live_owner_point_active"] = latest.get("owner_point_active")
                snap["attack_property_live_combo_lane_active"] = latest.get("owner_combo_lane_active")
                snap["attack_property_live_scaling_loss_per_hit"] = latest.get("scaling_loss_per_hit")
                snap["attack_property_live_scaling_floor"] = latest.get("scaling_floor")
                snap["attack_property_live_scaling_track"] = str(latest.get("scaling_track_text") or "")
                snap["attack_property_live_victim_slot"] = str(latest.get("victim_slot") or "")
                snap["attack_property_live_action_frame"] = _safe_int(latest.get("owner_action_frame"))
            elif projectiles:
                first_projectile = projectiles[0]
                projectile_live = bool(first_projectile.get("projectile_live"))
                snap["attack_property_packet_state"] = "LIVE ATTACK ACTOR" if projectile_live else "LAST ATTACK ACTOR"
                snap["attack_property_packet_source"] = "live_attack_actor" if projectile_live else "live_attack_actor_latched"
                snap["attack_property_packet_capture_frame"] = _safe_int(first_projectile.get("capture_gui_frame"), frame)
                snap["attack_property_packet_age_frames"] = max(0, int(frame) - _safe_int(first_projectile.get("last_seen_gui_frame"), frame))
                snap["attack_property_packet_action_id"] = _safe_int(
                    first_projectile.get("projectile_action_id", first_projectile.get("projectile_id")),
                    current_action,
                )
                snap["attack_property_packet_action_name"] = str(
                    first_projectile.get("attack_actor_name")
                    or first_projectile.get("projectile_action_name")
                    or first_projectile.get("owner_action_name")
                    or (current_meta.action_name if current_meta else "")
                    or ""
                )
                snap["attack_property_event_sequence"] = None
                snap["attack_property_event_caller_lr"] = None
                snap["attack_property_live_actor"] = _safe_int(first_projectile.get("actor"))
                snap["attack_property_live_a"] = _safe_int(first_projectile.get("property_a"))
                snap["attack_property_live_b"] = _safe_int(first_projectile.get("property_b"))
                snap["attack_property_live_status20"] = _safe_int(first_projectile.get("runtime_status_20"))
                snap["attack_property_live_damage"] = 0
                snap["attack_property_live_authored_damage"] = None
                snap["attack_property_live_damage_calc_output"] = None
                snap["attack_property_live_damage_calc_aux"] = None
                snap["attack_property_native_damage_calc_complete"] = False
                snap["attack_property_live_applied_damage"] = None
                snap["attack_property_live_resolved_damage"] = None
                snap["attack_property_live_resolved_aux"] = None
                snap["attack_property_native_damage_complete"] = False
                snap["attack_property_live_a_text"] = str(first_projectile.get("property_a_text") or "")
                snap["attack_property_live_b_text"] = str(first_projectile.get("property_b_text") or "")
                snap["attack_property_live_b_unknown"] = _safe_int(first_projectile.get("property_b_unknown_mask"))
                snap["attack_property_live_phase_a"] = _safe_int(first_projectile.get("phase_property_a"))
                snap["attack_property_live_phase_a_text"] = str(first_projectile.get("phase_property_a_text") or "")
                snap["attack_property_live_phase_b"] = _safe_int(first_projectile.get("phase_property_b"))
                snap["attack_property_live_phase_b_text"] = str(first_projectile.get("phase_property_b_text") or "")
                snap["attack_property_live_phase_b_unknown"] = _safe_int(first_projectile.get("phase_property_b_unknown_mask"))
                snap["attack_property_phase_count"] = 0
                snap["attack_property_phases"] = []
                snap["attack_property_live_owner_point_active"] = current_meta.point_active if current_meta else None
                snap["attack_property_live_combo_lane_active"] = current_meta.combo_lane_active if current_meta else None
                snap["attack_property_live_scaling_loss_per_hit"] = None
                snap["attack_property_live_scaling_floor"] = None
                snap["attack_property_live_scaling_track"] = ""
                snap["attack_property_live_victim_slot"] = ""
                snap["attack_property_live_action_frame"] = None
            else:
                snap["attack_property_packet_state"] = "NONE"
                snap["attack_property_packet_source"] = ""
                snap["attack_property_packet_capture_frame"] = None
                snap["attack_property_packet_age_frames"] = None
                snap["attack_property_packet_action_id"] = None
                snap["attack_property_packet_action_name"] = ""
                snap["attack_property_event_sequence"] = None
                snap["attack_property_event_caller_lr"] = None
                snap["attack_property_live_actor"] = None
                snap["attack_property_live_a"] = None
                snap["attack_property_live_b"] = None
                snap["attack_property_live_status20"] = None
                snap["attack_property_live_damage"] = None
                snap["attack_property_live_authored_damage"] = None
                snap["attack_property_live_damage_calc_output"] = None
                snap["attack_property_live_damage_calc_aux"] = None
                snap["attack_property_native_damage_calc_complete"] = False
                snap["attack_property_live_applied_damage"] = None
                snap["attack_property_live_resolved_damage"] = None
                snap["attack_property_live_resolved_aux"] = None
                snap["attack_property_native_damage_complete"] = False
                snap["attack_property_live_a_text"] = ""
                snap["attack_property_live_b_text"] = ""
                snap["attack_property_live_b_unknown"] = None
                snap["attack_property_live_phase_a"] = None
                snap["attack_property_live_phase_a_text"] = ""
                snap["attack_property_live_phase_b"] = None
                snap["attack_property_live_phase_b_text"] = ""
                snap["attack_property_live_phase_b_unknown"] = None
                snap["attack_property_phase_count"] = 0
                snap["attack_property_phases"] = []
                snap["attack_property_live_owner_point_active"] = None
                snap["attack_property_live_combo_lane_active"] = None
                snap["attack_property_live_scaling_loss_per_hit"] = None
                snap["attack_property_live_scaling_floor"] = None
                snap["attack_property_live_scaling_track"] = ""
                snap["attack_property_live_victim_slot"] = ""
                snap["attack_property_live_action_frame"] = None

        if changed:
            self._writer.request()
        return changed

    def flush(self) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        result = {"ok": True}
        self._writer.close(
            final_callback=lambda: result.__setitem__("ok", self._write_pending(force=True)),
            timeout=1.5,
        )
        self._restore_resolver_hook()
        return bool(result["ok"])


__all__ = [
    "RuntimeAttackPropertyProfiler",
    "decode_property_a",
    "decode_property_b",
    "decode_combo_scaling_lane",
    "resolver_hook_word",
    "resolver_exit_hook_word",
    "resolver_stub_words",
    "resolver_stub_bytes",
    "resolver_exit_stub_words",
    "resolver_exit_stub_bytes",
    "default_profile_path",
    "default_event_path",
]
