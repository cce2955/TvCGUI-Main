#!/usr/bin/env python3

from __future__ import annotations

import ctypes
import math
import struct
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple, Optional
import os
import re
import threading
import pygame
import win32con
import win32gui
import traceback
import sys

def pause_on_error(context: str, exc: BaseException) -> None:
    print(f"\n[{context}]")
    print(f"error={exc!r}")
    traceback.print_exc()

    try:
        input("\nCrash detected. Press Enter to close...")
    except EOFError:
        pass
from dolphin_io import hook, rd32, rbytes

import json as _json

WORLD_Y_OFFSET = -0.7
GIANT_FALLBACK_REF_CAM_Z = 7.260000228881836
GIANT_CAMERA_Z_THRESHOLD = 10.0
GIANT_X_SCALE = 0.42
GIANT_Y_SCALE = 0.76
GIANT_Y_SCREEN_OFFSET = 38.0
HURTBOX_BUILD_TAG = "TAKEWHEEL_V24_STABLE_HITS"
_last_hurt_debug_text = "hurt=not-run"
_legend_window_started = False
_legend_visible_requested = False
_legend_manual_hidden = False
PROJECTILE_Y_OFFSET: float = 0
PROJECTILE_RADIUS_SCALE: float = 0.5
PROJECTILE_DESPAWN_FRAMES: int = 6
PERSPECTIVE_Z_OVERRIDE: Optional[float] = None
HITBOX_FILTER_FILE = "hitbox_filter.json"
_last_filter_mtime = 0.0
_slot_filter = {"P1": True, "P2": True, "P3": True, "P4": True}
_hurtbox_filter = {"P1": True, "P2": True, "P3": True, "P4": True}
_show_hurtboxes = True
_hurtbox_view_mode = "clean"

MOTION_THRESHOLD: float = 0.003
STILL_FRAME_LIMIT: int = 4
MOTION_FRAME_REQUIRED: int = 2
ACTOR_TABLE = 0x80476E50
ACTOR_MAX   = 16  # confirmed live projectile table slots; multiple actors appear inside this table

# Some supers (Morrigan Finishing Shower / similar missile showers) allocate
# many projectile actors in the projectile actor pool, but only a couple of
# those actors are exposed through ACTOR_TABLE on a given frame.  Scan this
# small stride-based pool too so multi-missile moves do not collapse to one
# or two misleading probes.  Normal player hitboxes are untouched by this.
PROJECTILE_ACTOR_POOL_BASES = (
    0x91B159B4,
)
PROJECTILE_ACTOR_POOL_STRIDE = 0x1A4
PROJECTILE_ACTOR_POOL_COUNT = 48


ACTOR_OFF_X = 0x5C
ACTOR_OFF_Y = 0x6C
ACTOR_OFF_Z = 0x7C

# Live projectile actor layout, confirmed from Volnutt dump sequences.
# The actor table can contain duplicate pointers, so projectile reads de-dupe
# by actor address and then validate owner/id/position before drawing.
ACTOR_OFF_PREV_X = 0xBC
ACTOR_OFF_PREV_Y = 0xCC
ACTOR_OFF_PREV_Z = 0xDC

ACTOR_OFF_SWEEP_X = 0xE0
ACTOR_OFF_SWEEP_Y = 0xE4
ACTOR_OFF_SWEEP_Z = 0xE8

ACTOR_OFF_OWNER = 0x130
ACTOR_OFF_PROJ_ID = 0x134
ACTOR_OFF_OWNER_MIRROR = 0x138
ACTOR_OFF_LINKED_RECORD = 0x13C

# Secondary projectile contact/result anchor. Casshan/FLAG_309 tests showed
# +0x118/+0x11C becomes populated on hit and snaps near the defender. That is
# useful as an impact/result point, but it is NOT the live projectile hitbox,
# so do not use it as the projectile anchor by default.
ACTOR_OFF_IMPACT_X = 0x118
ACTOR_OFF_IMPACT_Y = 0x11C
ACTOR_OFF_IMPACT_Z = 0x120
PROJECTILE_USE_IMPACT_ANCHOR = False
PROJECTILE_IMPACT_ANCHOR_MAX_DIST = 1.75

# Projectile actor positions are useful, but the actor-table radii/sweep fields
# are NOT confirmed collision boxes. Keep this conservative: draw a small probe
# at the live actor point only, and do not draw actor +0xE0/+0xE4 as a sweep.
PROJECTILE_FALLBACK_RADIUS = 0.35
PROJECTILE_SWEEP_MIN_WORLD = 0.03
PROJECTILE_SWEEP_MAX_WORLD = 12.0

# Candidate collision visualizer.  This intentionally draws a SHORT local
# capsule in front of the projectile root, using the projectile direction vector
# and the actor's scale-ish field.  It does not use actor +0xE0/+0xE4, because
# live Hadouken tests proved that field is an emitter/origin anchor, not a
# collision sweep.
ACTOR_OFF_SCALE_CANDIDATE = 0xF8
ACTOR_OFF_DIR_X = 0x108
ACTOR_OFF_DIR_Y = 0x10C
ACTOR_OFF_DIR_Z = 0x104
LINKED_OFF_TARGET = 0x34
LINKED_OFF_DIR_X = 0xAC
LINKED_OFF_DIR_Y = 0xB0
PROJECTILE_SCALE_RADIUS_MIN = 0.08
PROJECTILE_SCALE_RADIUS_MAX = 0.85
PROJECTILE_DEFAULT_EXTENT = 0.80
PROJECTILE_EXTENT_MIN = 0.35
PROJECTILE_EXTENT_MAX = 1.25
PROJECTILE_EXTENT_BY_ID = {
    0x135: 0.80,  # Casshan FLAG_309 probe: impact lands ~0.75-0.85u ahead of root.
    0x160: 0.70,  # Volnutt/Morrigan-style multi actors observed as separate 0x160 projectiles.
    0x163: 0.80,  # Morrigan Finishing Shower missile actors.
}

# Doronjo/Odronjo-style giant objects can be actor-table projectiles with a
# very small actor id (0x1), so they pass through the actor system but do not
# carry the usual actor +0xF8 radius.  The linked hit-state card below was
# observed on the big object:
#   linked +0x80 == 0x0000030C
#   linked +0x84 == 0x00000123
# Use this as a narrow signature instead of treating every id=1 actor as huge.
PROJECTILE_LARGE_FIELD_RADIUS = 1.10
PROJECTILE_LARGE_FIELD_EXTENT = 1.45
PROJECTILE_LARGE_FIELD_CARDS = {
    (0x0000030C, 0x00000123),
}

# For some missile showers, actor +0x108/+0x10C is the local/up vector,
# not forward.  These ids draw their candidate capsule from per-frame motion
# first, then fall back to the actor/link direction fields if motion is zero.
PROJECTILE_MOTION_DIR_FIRST_IDS = {
    0x163,
}

# Leave empty until a projectile ID has a collision-confirmed radius.
# Earlier 0x130/0x134/0x160 = 1.20 guesses were visual/template scale and
# produced bogus half-screen circles on Hadouken-style projectiles.
PROJECTILE_RADIUS_BY_ID = {
}

# ----------------------------
# Projectile signature scanner (kept but commented out from active use)
# ----------------------------
# PROJ_SIG         = b"\x04\x01\x02\x00\x00"
# PROJ_RADIUS_OFF  = 0x2F
# PROJ_SCAN_START  = 0x90000000
# PROJ_SCAN_END    = 0x94000000
# PROJ_SCAN_BLOCK  = 0x40000

# Physical hitboxes with radius above this world-unit value are skipped entirely.
HITBOX_MAX_RENDER_RADIUS: float = 4.0

PROJECTILE_POOLS = [
    0x91B15900,
    0x91B15A10,
    0x91B15B50,
    0x91B15C90,
    0x91B15DD0,
    0x91B15F10,
]
PROJECTILE_NODE_STRIDE = 0x30
PROJECTILE_NODE_COUNT = 16

# Node layout (confirmed):
#   Row 0: +0x00 = X,  +0x08 = dim_0
#   Row 1: +0x10 = Y,  +0x18 = dim_1
#   Row 2: +0x20 = Z,  +0x28 = dim_2
PROJ_OFF_X: int = 0x00
PROJ_OFF_Y: int = 0x10
PROJ_OFF_Z: int = 0x20
PROJ_OFF_DIM_0: int = 0x08
PROJ_OFF_DIM_1: int = 0x18
PROJ_OFF_DIM_2: int = 0x28

# Candidate offsets inside the node that may contain an owning actor pointer.
PROJ_PTR_CANDIDATES = (
    0x04,
    0x0C,
    0x14,
    0x1C,
    0x24,
    0x2C,
)

OFF_CHAR_ID = 0x14
# Per-slot activity marker: active/on-stage fighter is 0; standby teammate is 1
# in all supplied team, jump, and giant dumps.  The body descriptors remain
# allocated while standby, so use this before rendering them.
OFF_SLOT_ACTIVITY = 0x04
OFF_STATE_ID = 0x1EA  # current action/state id
OFF_CHR_TBL = 0x1E0   # live character action-table pointer

# Slot-local action-frame counter.
# User observed slot 1 base 0x9246B9C0 and counter addr 0x9246BB98.
# 0x9246BB98 - 0x9246B9C0 = 0x1D8.
# Values are big-endian floats: 0x40000000=2.0, 0x40400000=3.0,
# 0x40800000=4.0, etc. The -1 bias maps 2.0 to action frame 1.
OFF_ACTION_COUNTER = 0x1D8
ACTION_COUNTER_FRAME_BIAS = -1.0
ACTION_COUNTER_MIN = 0.0
ACTION_COUNTER_MAX = 600.0
FRAME_DATA_SCAN_ENABLED = True


PASSIVE_STATE_IDS = {
    1,   # idle
    2,   # forward
    3,   # backward
    6,   # forward dash
    7,   # back dash
    8,   # air dash
    9,   # rising
    10,  # crouching
    11,  # crouched
    13,  # landing
    19,  # pre jump
    20,  # jump
    21,  # jump forward
    22,  # jump back
    25,  # push block post anim
    28,  # super jump
    29,  # super jump
    30,  # landing
    31,  # pre super jump
    35,  # air dash forward
    36,  # air dash back
    48,  # block
    49,  # crouching block
    50,  # air block
    52,  # air pushblock
    53,  # crouch pushblock
}

PASSIVE_STATE_IDS = {
    1, 2, 3,
    6, 7, 8, 9, 10, 11, 13,
    19, 20, 21, 22,
    25, 28, 29, 30, 31, 35, 36,
    48, 49, 50, 52, 53,
}

# Megacrash has large dormant volumes in memory before/after the actual burst.
# Keep those hidden unless frame-data gating says the burst is currently active.
MEGACRASH_STATE_IDS = {448}

def read_state_raw(slot_base: int) -> int:
    raw = rd32(slot_base + OFF_STATE_ID)
    return 0 if raw is None else raw

def decode_state_id(raw: int) -> int:
    hi16 = (raw >> 16) & 0xFFFF
    low16 = raw & 0xFFFF
    low8 = raw & 0xFF

    if hi16 != 0:
        return hi16
    if low16 != 0:
        return low16
    return low8

def read_state_id(slot_base: int) -> int:
    raw = read_state_raw(slot_base)
    state_id = decode_state_id(raw)
    return state_id

def is_passive_state(state_id: int) -> bool:
    return state_id in PASSIVE_STATE_IDS
_last_state_ids: Dict[str, int] = {}
_last_state_raws: Dict[str, int] = {}
def _slots_from_payload(value, fallback):
    out = dict(fallback)
    if isinstance(value, dict):
        for slot in ("P1", "P2", "P3", "P4"):
            if slot in value:
                out[slot] = bool(value.get(slot))
    return out


def _normalize_hurtbox_view_mode(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"clean", "simple", "minimal"}:
        return "clean"
    if raw in {"detailed", "detail", "full"}:
        return "detailed"
    if raw in {"debug", "raw", "labels"}:
        return "debug"
    return "clean"


def set_hurtbox_view_mode(value: Any) -> str:
    """Set and persist the legend-selected hurtbox presentation mode.

    The main overlay polls hitbox_filter.json, so persisting here keeps the
    selection through a relaunch without touching any existing hitbox filters.
    """
    global _hurtbox_view_mode, _last_filter_mtime
    mode = _normalize_hurtbox_view_mode(value)
    _hurtbox_view_mode = mode
    try:
        payload = {}
        if os.path.exists(HITBOX_FILTER_FILE):
            with open(HITBOX_FILTER_FILE, "r", encoding="utf-8") as f:
                existing = _json.load(f)
            if isinstance(existing, dict):
                payload = existing
        payload["hurtbox_view_mode"] = mode
        temp_path = HITBOX_FILTER_FILE + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            _json.dump(payload, f, indent=2, sort_keys=True)
        os.replace(temp_path, HITBOX_FILTER_FILE)
        _last_filter_mtime = 0.0
    except Exception as exc:
        print(f"[HitboxView] could not save mode {mode!r}: {exc!r}")
    return mode


def _read_filter_payload() -> None:
    global _last_filter_mtime, _slot_filter, _hurtbox_filter, _show_hurtboxes, _hurtbox_view_mode
    try:
        mt = os.path.getmtime(HITBOX_FILTER_FILE)
        if mt == _last_filter_mtime:
            return
        _last_filter_mtime = mt
        with open(HITBOX_FILTER_FILE) as f:
            data = _json.load(f)

        if not isinstance(data, dict):
            return

        _slot_filter = _slots_from_payload(data.get("hitbox_slots", data), _slot_filter)
        _hurtbox_filter = _slots_from_payload(data.get("hurtbox_slots", data.get("_hurtbox_slots", _hurtbox_filter)), _hurtbox_filter)
        _show_hurtboxes = bool(data.get("show_hurtboxes", any(_hurtbox_filter.values())))
        _hurtbox_view_mode = _normalize_hurtbox_view_mode(data.get("hurtbox_view_mode", _hurtbox_view_mode))
    except Exception:
        pass


def _read_slot_filter() -> dict:
    _read_filter_payload()
    return _slot_filter


def _read_hurtbox_filter() -> dict:
    _read_filter_payload()
    return _hurtbox_filter


def _hurtbox_layer_requested() -> bool:
    _read_filter_payload()
    return bool(_show_hurtboxes and any(_hurtbox_filter.values()))


def _read_hurtbox_view_mode(control=None) -> str:
    _read_filter_payload()
    if control is not None and bool(getattr(control, "show_debug", False)):
        return "debug"
    return _hurtbox_view_mode


def dump_state18(slot_name: str, slot_base: int) -> None:
    raw = rd32(slot_base + OFF_STATE_ID)
    raw = 0 if raw is None else raw

    hi16 = (raw >> 16) & 0xFFFF
    low16 = raw & 0xFFFF
    low8 = raw & 0xFF

    print(
        f"[State18] {slot_name} "
        f"addr=0x{slot_base + OFF_STATE_ID:08X} "
        f"raw=0x{raw:08X} ({raw}) "
        f"hi16=0x{hi16:04X} ({hi16}) "
        f"low16=0x{low16:04X} ({low16}) "
        f"low8=0x{low8:02X} ({low8})"
    )

def set_dpi_aware() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


set_dpi_aware()


@dataclass(frozen=True)
class HitboxLayout:
    struct_shift: int
    blocks: Tuple[int, ...]
    off_x: int
    off_y: int
    off_r: int
    off_flag: int


@dataclass(frozen=True)
class CameraLayout:
    base: int
    off_x: int
    off_y: int
    off_z: int
    off_w: int


@dataclass(frozen=True)
class DisplayConfig:
    baseline_w: int
    baseline_h: int
    baseline_ppu: float
    zoom: float
    center_y_offset_px: int
    max_radius_units: float
    fps: int
    show_debug_axes: bool


SLOT_BASES: Dict[str, int] = {
    "P1": 0x9246B9C0,
    "P2": 0x92B6BA00,
    "P3": 0x927EB9E0,
    "P4": 0x92EEBA20,
}
SLOT_ORDER: Tuple[str, ...] = tuple(SLOT_BASES.keys())


@dataclass(frozen=True)
class MoveFrameData:
    move_id: int
    move_name: str
    active_windows: Tuple[Tuple[int, int], ...]
    invuln_frames: int = 0

    def active_text(self) -> str:
        if not self.active_windows:
            return "?"
        return ",".join(f"{s}-{e}" if s != e else str(s) for s, e in self.active_windows)

    def invuln_text(self) -> str:
        try:
            frames = int(self.invuln_frames or 0)
        except Exception:
            frames = 0
        return f"{frames}f" if frames > 0 else ""


def _valid_active_window(start: Any, end: Any) -> Optional[Tuple[int, int]]:
    try:
        s = int(start)
        e = int(end)
    except Exception:
        return None
    if s <= 0 or e <= 0:
        return None
    if e < s:
        e = s
    return (s, e)


def _state_lookup_keys(state_id: int) -> Tuple[int, ...]:
    keys: List[int] = []
    try:
        raw = int(state_id) & 0xFFFF
    except Exception:
        return tuple(keys)

    for candidate in (raw, raw & 0xFF, 0x0100 | (raw & 0xFF)):
        if candidate not in keys:
            keys.append(candidate)
    return tuple(keys)


def _parse_invuln_frames(raw: Any) -> int:
    if raw is None:
        return 0
    if isinstance(raw, (int, float)):
        try:
            return max(0, int(raw))
        except Exception:
            return 0
    txt = str(raw or "").strip().lower()
    if not txt:
        return 0
    vals = []
    for m in re.finditer(r"(\d+)\s*f", txt):
        try:
            vals.append(int(m.group(1)))
        except Exception:
            pass
    if vals:
        return max(vals)
    return 0


def is_frame_data_invuln(fd: Optional[MoveFrameData], action_frame: Optional[int]) -> bool:
    if fd is None or action_frame is None:
        return False
    try:
        frames = int(fd.invuln_frames or 0)
    except Exception:
        return False
    return frames > 0 and 1 <= int(action_frame) <= frames


def _dim_hitbox_color(color: Tuple[int, int, int]) -> Tuple[int, int, int]:
    r, g, b = color[:3]
    return (
        max(35, int(r * 0.45)),
        max(35, int(g * 0.45)),
        max(35, int(b * 0.45)),
    )


def _soften_hurt_color(color: Tuple[int, int, int], factor: float = 0.58, floor: int = 36) -> Tuple[int, int, int]:
    r, g, b = color[:3]
    return (
        max(floor, int(r * factor)),
        max(floor, int(g * factor)),
        max(floor, int(b * factor)),
    )


def _renderer_slot_from_scan_label(label: str, fallback_idx: int) -> Optional[str]:
    s = str(label or "").strip().lower().replace("_", "-").replace(" ", "")
    mapping = {
        "p1-c1": "P1", "p1c1": "P1", "p1-char1": "P1", "p1char1": "P1",
        "p2-c1": "P2", "p2c1": "P2", "p2-char1": "P2", "p2char1": "P2",
        "p1-c2": "P3", "p1c2": "P3", "p1-char2": "P3", "p1char2": "P3",
        "p2-c2": "P4", "p2c2": "P4", "p2-char2": "P4", "p2char2": "P4",
        "p1": "P1", "p2": "P2", "p3": "P3", "p4": "P4",
    }
    if s in mapping:
        return mapping[s]
    if 0 <= fallback_idx < len(SLOT_ORDER):
        return SLOT_ORDER[fallback_idx]
    return None


def read_action_counter_float(slot_base: int) -> Optional[float]:
    raw = rd32(slot_base + OFF_ACTION_COUNTER)
    if raw is None:
        return None
    try:
        value = struct.unpack(">f", struct.pack(">I", raw & 0xFFFFFFFF))[0]
    except Exception:
        return None
    if not math.isfinite(value):
        return None
    if value < ACTION_COUNTER_MIN or value > ACTION_COUNTER_MAX:
        return None
    return value


def read_action_frame(slot_base: int) -> Optional[int]:
    value = read_action_counter_float(slot_base)
    if value is None:
        return None
    return max(0, int(round(value + ACTION_COUNTER_FRAME_BIAS)))


def is_frame_data_active(fd: Optional[MoveFrameData], action_frame: Optional[int]) -> bool:
    if fd is None or action_frame is None:
        return False
    for start, end in fd.active_windows:
        if start <= action_frame <= end:
            return True
    return False


def lookup_frame_data(
    fd_by_slot: Dict[str, Dict[int, MoveFrameData]],
    slot_name: str,
    state_id: int,
) -> Optional[MoveFrameData]:
    slot_fd = fd_by_slot.get(slot_name) or {}
    if not slot_fd:
        return None
    for key in _state_lookup_keys(state_id):
        fd = slot_fd.get(key)
        if fd is not None:
            return fd
    return None


def build_frame_data_cache() -> Dict[str, Dict[int, MoveFrameData]]:
    """Build per-render-slot frame data from scan_normals_all.scan_once().

    The scanner already resolves the current characters and attaches active-frame
    windows. This cache maps those moves back onto the fixed renderer slot names
    P1/P2/P3/P4 by scan order.
    """
    if not FRAME_DATA_SCAN_ENABLED:
        return {}

    try:
        import scan_normals_all as fdscan
    except Exception as exc:
        print(f"[FrameGate] scan_normals_all import failed: {exc!r}")
        return {}

    try:
        scanned = fdscan.scan_once()
    except Exception as exc:
        print(f"[FrameGate] frame-data scan failed: {exc!r}")
        return {}

    fd_by_slot: Dict[str, Dict[int, MoveFrameData]] = {}
    for idx, entry in enumerate(scanned or []):
        if not isinstance(entry, dict):
            continue
        slot_name = _renderer_slot_from_scan_label(str(entry.get("slot_label") or ""), idx)
        if slot_name is None:
            continue
        slot_map: Dict[int, MoveFrameData] = {}

        for mv in entry.get("moves", []):
            move_id = mv.get("id")
            if move_id is None:
                continue

            windows: List[Tuple[int, int]] = []
            primary = _valid_active_window(mv.get("active_start"), mv.get("active_end"))
            secondary = _valid_active_window(mv.get("active2_start"), mv.get("active2_end"))
            for win in (primary, secondary):
                if win is not None and win not in windows:
                    windows.append(win)

            invuln_frames = _parse_invuln_frames(mv.get("invuln_frames") or mv.get("invuln"))
            if not windows and invuln_frames <= 0:
                continue

            try:
                mid = int(move_id) & 0xFFFF
            except Exception:
                continue

            fd = MoveFrameData(
                move_id=mid,
                move_name=str(mv.get("move_name") or f"anim_{mid:04X}"),
                active_windows=tuple(windows),
                invuln_frames=invuln_frames,
            )

            for key in _state_lookup_keys(mid):
                previous = slot_map.get(key)
                # Prefer the canonical full action ID over a low-byte/internal
                # script alias.  This matters for Shoryu 0x0136/7/8, whose
                # live state must not be shadowed by an unrelated 0x0036 row.
                if previous is None or ((mid & 0xFF00) and not (int(previous.move_id) & 0xFF00)):
                    slot_map[key] = fd

        fd_by_slot[slot_name] = slot_map

    loaded = sum(len(v) for v in fd_by_slot.values())
    print(f"[FrameGate] loaded {loaded} frame-data lookup entries")
    return fd_by_slot


PASSIVE_HOLD_FRAMES = 3
_slot_passive_hold: Dict[str, int] = {k: 0 for k in SLOT_BASES}

HITBOX = HitboxLayout(
    struct_shift=0x4C0,
    blocks=(0x64, 0xA4, 0xE4),
    off_x=0x00,
    off_y=0x04,
    off_r=0x18,
    off_flag=0xC3,
)

# Skeletal hurtbox descriptors.  This is separate from the existing normal
# slot hitbox reader above.  Each descriptor points at a live 3x4 bone matrix
# in MEM1, has a local offset, and a radius.  Draw this only for active hit
# targets for now so normal/projectile UX does not get buried.
HURTBOX_DESC_BASE = 0xC20
HURTBOX_DESC_STRIDE = 0x18
HURTBOX_DESC_COUNT = 24
HURTBOX_MIN_RADIUS = 0.025
HURTBOX_MAX_RADIUS = 0.85
HURTBOX_CONTACT_PAD = 0.03
# Standby teammates keep descriptor pointers, but many of those matrices are
# zero/degenerate/stale.  Reject bad transforms before drawing and require a
# coherent body rig before a whole slot is considered renderable.
HURTBOX_MATRIX_MIN_ROW_NORM = 0.18
HURTBOX_MATRIX_MAX_ROW_NORM = 4.00
HURTBOX_MATRIX_MAX_TRANSLATION = 45.0
HURTBOX_MIN_COHERENT_DESCRIPTORS = 5
HURTBOX_COHERENT_XY_RANGE = 6.0
HURTBOX_COHERENT_Z_RANGE = 5.0
# Reaction/hitstun states from move_id_map_charagnostic.csv.  The first
# hurtbox probe only drew when a transient hit-contact record was found,
# which often disappears by the time the overlay frame paints.  Keep the
# contact highlight path, but also draw the victim skeletal hurtboxes while
# they are visibly in hitstun/knockdown/recovery states.
HURTBOX_REACTION_STATE_IDS = {
    60, 61, 62, 64, 65,
    70, 73, 74, 75, 76, 77, 79, 80,
    89, 90, 91, 92, 93, 94, 95, 96, 98,
    101, 102, 104, 108, 109,
    113, 115, 116, 119, 124, 126, 128, 129, 130, 132, 133, 142,
    161, 166,
}
HIT_EVENT_SCAN_START = 0x91970000
HIT_EVENT_SCAN_END = 0x91978000

CAMERA = CameraLayout(
    base=0x8053CB20,
    off_x=0x00,
    off_y=0x04,
    off_z=0x08,
    off_w=0x0C,
)

DISPLAY = DisplayConfig(
    baseline_w=1280,
    baseline_h=720,
    baseline_ppu=160.0,
    zoom=1.0,
    center_y_offset_px=0,
    max_radius_units=8.0,
    fps=60,
    show_debug_axes=False,
)

USE_LIVE_CAMERA = True
# TvC camera's Y/Z view affine is kept beside the camera position.  The
# overlay used to ignore this tilt transform, which is why bone hurtboxes
# gradually drifted during camera follow / super-jump pans.
CAMERA_VIEW_COS_OFF = 0x20
CAMERA_VIEW_SIN_OFF = 0x24
CAMERA_VIEW_Y_TRANSLATE_OFF = 0x28
CAMERA_DEPTH_Y_COEFF_OFF = 0x30
CAMERA_DEPTH_Z_COEFF_OFF = 0x34
CAMERA_DEPTH_TRANSLATE_OFF = 0x38

COLORS: Dict[str, List[Tuple[int, int, int]]] = {
    "P1": [(255, 60, 60), (255, 140, 0), (255, 220, 0)],
    "P2": [(180, 60, 255), (60, 200, 255), (60, 255, 180)],
    "P3": [(255, 80, 180), (255, 0, 120), (255, 120, 200)],
    "P4": [(80, 255, 120), (0, 255, 80), (120, 255, 200)],
}

PROJ_COLORS: Dict[str, Tuple[int, int, int]] = {
    "P1": (255, 180, 120),
    "P2": (180, 140, 255),
    "P3": (255, 160, 210),
    "P4": (160, 255, 200),
}

HURT_REGION_COLORS: Dict[str, Tuple[int, int, int]] = {
    "head": (165, 245, 255),
    "torso": (88, 210, 255),
    "pelvis": (88, 160, 255),
    "arm_l": (90, 250, 205),
    "arm_r": (90, 250, 205),
    "leg_l": (135, 210, 255),
    "leg_r": (135, 210, 255),
    "other": (95, 235, 255),
}

COL_CROSS = (255, 255, 255)
COL_DIM = (120, 120, 120)
COL_BG = (0, 0, 0)
COL_DEBUG = (0, 255, 0)
COL_PROJ = (255, 255, 255)

COL_CROSS = (255, 255, 255)
COL_DIM = (120, 120, 120)
COL_BG = (0, 0, 0)
COL_DEBUG = (0, 255, 0)
COL_PROJ = (255, 255, 255)

HITBOX_SPAWN_FRAMES = 6
HITBOX_CROSS_DELAY_FRAMES = 1
HITBOX_ACTIVE_PULSE_SPEED = 0.22

# --- surface cache ---
_surface_cache: Dict[Tuple[int, Tuple[int,int,int], bool], pygame.Surface] = {}

# Cached hurtbox sprites.  Always-on hurtboxes need to be readable, but creating
# a translucent Surface per box per frame tanks FPS.  Cache by screen radius,
# color, and highlight state so the draw path is a cheap blit.
_hurt_surface_cache: Dict[Tuple[int, Tuple[int,int,int], bool, bool, bool], pygame.Surface] = {}

def _get_cached_hurtbox_surface(rpx: int, color: Tuple[int,int,int], highlight: bool, detail: bool = True, invuln: bool = False) -> pygame.Surface:
    """Cached bright-outline hurtbox sprite.

    Cyan = normal hurtbox, yellow = contacted/highlighted, green = startup
    invulnerability currently active for the owning move. Geometry is unchanged;
    this is only paint style.
    """
    rpx = max(2, int(rpx))
    rpx = max(2, int(round(rpx / 2.0) * 2))

    if highlight:
        rim = (255, 232, 100)
        halo = (255, 247, 180)
        inner = (255, 255, 255)
        key = (rpx, rim, True, bool(detail), False)
    elif invuln:
        rim = (88, 255, 130)
        halo = (170, 255, 190)
        inner = (240, 255, 244)
        key = (rpx, rim, False, bool(detail), True)
    else:
        rim = color[:3] if color else (95, 235, 255)
        halo = tuple(min(255, c + 25) for c in rim)
        inner = tuple(min(255, c + 120) for c in rim)
        key = (rpx, rim, False, bool(detail), False)

    surf = _hurt_surface_cache.get(key)
    if surf is not None:
        return surf

    scale = 2
    pad = 8
    size = (rpx * 2 + pad * 2 + 8) * scale
    hi = pygame.Surface((size, size), pygame.SRCALPHA)
    cx = cy = size // 2
    rr = rpx * scale

    if highlight:
        pygame.draw.circle(hi, (*halo, 50), (cx, cy), rr + 5 * scale, 2 * scale)
        pygame.draw.circle(hi, (*rim, 255), (cx, cy), rr, 3 * scale)
        pygame.draw.circle(hi, (*inner, 210), (cx, cy), max(2, rr - 4 * scale), 1 * scale)
    else:
        outline_w = 2 * scale if detail else 1 * scale
        halo_alpha = 30 if detail else 12
        rim_alpha = 220 if detail else 108
        inner_alpha = 92 if detail else 44
        pygame.draw.circle(hi, (*halo, halo_alpha), (cx, cy), rr + 3 * scale, outline_w)
        pygame.draw.circle(hi, (*rim, rim_alpha), (cx, cy), rr, outline_w)
        pygame.draw.circle(hi, (*inner, inner_alpha), (cx, cy), max(2, rr - 4 * scale), 1 * scale)

    # Minimal center reticle.
    c = (4 if highlight else (3 if detail else 2)) * scale
    a = 225 if highlight else (125 if detail else 68)
    pygame.draw.line(hi, (255, 255, 255, a), (cx - c, cy), (cx + c, cy), 1 * scale)
    pygame.draw.line(hi, (255, 255, 255, a), (cx, cy - c), (cx, cy + c), 1 * scale)

    try:
        surf = pygame.transform.smoothscale(hi, (size // scale, size // scale))
    except Exception:
        surf = pygame.transform.scale(hi, (size // scale, size // scale))
    _hurt_surface_cache[key] = surf
    return surf

def slot_passive_override(name: str, state_id: int) -> bool:
    return is_passive_state(state_id)
def _get_cached_hitbox_surface(rpx: int, color: Tuple[int,int,int], active: bool):
    key = (rpx, color, active)
    if key in _surface_cache:
        return _surface_cache[key]

    pad = 6
    size = rpx * 2 + pad * 2
    surf = pygame.Surface((size, size), pygame.SRCALPHA)

    cx = cy = rpx + pad
    r_c, g_c, b_c = color

    if active:
        pygame.draw.circle(surf, (r_c, g_c, b_c, 110), (cx, cy), rpx)
        pygame.draw.circle(surf, (r_c, g_c, b_c, 220), (cx, cy), rpx, 2)
    else:
        pygame.draw.circle(surf, (r_c, g_c, b_c, 55), (cx, cy), rpx)

    _surface_cache[key] = surf
    return surf

# ----------------------------
# Projectile scanner (kept, just not wired into main loop)
# ----------------------------

class ProjectileScanner:
    def __init__(self):
        self._radius_addrs: List[int] = []
        self._scan_count: int = 0

    # def scan(self) -> int:
    #     found: List[int] = []
    #     for base_addr in range(PROJ_SCAN_START, PROJ_SCAN_END, PROJ_SCAN_BLOCK):
    #         data = rbytes(base_addr, PROJ_SCAN_BLOCK)
    #         if not data:
    #             continue
    #         idx = data.find(PROJ_SIG)
    #         while idx != -1:
    #             sig_addr = base_addr + idx
    #             radius_addr = sig_addr + PROJ_RADIUS_OFF
    #             r = _rf(radius_addr)
    #             if 0.0 < r < 20.0:
    #                 found.append(radius_addr)
    #             idx = data.find(PROJ_SIG, idx + 1)
    #     self._radius_addrs = found
    #     self._scan_count += 1
    #     print(f"[ProjectileScanner] scan #{self._scan_count}: {len(found)} radius address(es) found")
    #     for a in found:
    #         print(f"  radius_addr=0x{a:08X}  r={_rf(a):.4f}")
    #     return len(found)

    def scan(self) -> int:
        print("[ProjectileScanner] signature scan disabled (using node watcher instead)")
        return 0

    def dump(self, max_hits: int = 3) -> None:
        print("[ProjectileScanner.dump] signature scan disabled — use NodeWatcher.dump() (F3) instead")

    @property
    def radius_addrs(self) -> List[int]:
        return self._radius_addrs

    @property
    def scan_count(self) -> int:
        return self._scan_count


# ----------------------------
# Multi-offset node watcher
# ----------------------------

@dataclass
class ProjectileNodeState:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    dim_0: float = 0.0
    dim_1: float = 0.0
    dim_2: float = 0.0
    actor_ptr: int = 0
    inactive_frames: int = 0
    active: bool = False


@dataclass
class ProjectileActorState:
    actor: int
    owner: int
    owner_slot: str
    proj_id: int
    x: float
    y: float
    z: float
    prev_x: float
    prev_y: float
    prev_z: float
    sweep_x: float
    sweep_y: float
    sweep_z: float
    radius: float
    linked_record: int = 0
    anchor_source: str = "root"
    root_x: float = 0.0
    root_y: float = 0.0
    root_z: float = 0.0
    impact_x: float = 0.0
    impact_y: float = 0.0
    impact_z: float = 0.0
    contact_valid: bool = False
    target_ptr: int = 0
    dir_x: float = 0.0
    dir_y: float = 0.0
    dir_z: float = 0.0
    hit_start_x: float = 0.0
    hit_start_y: float = 0.0
    hit_start_z: float = 0.0
    hit_end_x: float = 0.0
    hit_end_y: float = 0.0
    hit_end_z: float = 0.0
    extent: float = 0.0

    @property
    def has_sweep(self) -> bool:
        # Disabled: actor +0xE0/+0xE4 is not collision sweep in live tests.
        return False

    def label(self, debug: bool = False) -> str:
        if debug:
            return (
                f"PRJ {self.owner_slot}:0x{self.proj_id:X} "
                f"{self.anchor_source} @0x{self.actor:08X}"
            )
        return f"PRJ {self.owner_slot}:0x{self.proj_id:X}@{self.actor & 0xFFFF:04X}"


@dataclass
class HurtboxState:
    slot_name: str
    slot_base: int
    index: int
    desc_addr: int
    matrix_ptr: int
    local_x: float
    local_y: float
    local_z: float
    x: float
    y: float
    z: float
    radius: float
    raw_type: int = 0

    def label(self) -> str:
        return f"HURT {self.slot_name}[{self.index}]"


@dataclass
class HitContactState:
    event_addr: int
    source: int
    source_slot: str
    target: int
    target_slot: str
    x: float
    y: float
    z: float
    dir_x: float = 0.0
    dir_y: float = 0.0

    def label(self) -> str:
        return f"HIT {self.source_slot}>{self.target_slot}"


class ProjectileNodeTracker:
    def __init__(self, pool_count: int):
        self._nodes: Dict[int, ProjectileNodeState] = {
            i: ProjectileNodeState() for i in range(pool_count)
        }

    def update_from_node(self, node_idx: int, node_addr: int) -> None:
        state = self._nodes[node_idx]

        x = _rf(node_addr + PROJ_OFF_X)
        y = _rf(node_addr + PROJ_OFF_Y)
        z = _rf(node_addr + PROJ_OFF_Z)

        dim_0 = _clean_dim(_rf(node_addr + PROJ_OFF_DIM_0))
        dim_1 = _clean_dim(_rf(node_addr + PROJ_OFF_DIM_1))
        dim_2 = _clean_dim(_rf(node_addr + PROJ_OFF_DIM_2))

        actor_ptr = 0
        for off in PROJ_PTR_CANDIDATES:
            p = rd32(node_addr + off)
            if _looks_like_ptr(p):
                actor_ptr = p
                break

        has_any_dim = (dim_0 > 0.0) or (dim_1 > 0.0) or (dim_2 > 0.0)
        sane_pos = abs(x) < 30 and abs(y) < 30 and abs(z) < 30

        if sane_pos and has_any_dim:

            state.x = x
            state.y = y
            state.z = z

            state.dim_0 = dim_0
            state.dim_1 = dim_1
            state.dim_2 = dim_2

            state.actor_ptr = actor_ptr
            state.inactive_frames = 0
            state.active = True

        else:

            state.inactive_frames += 1

            if state.inactive_frames >= PROJECTILE_DESPAWN_FRAMES:
                state.active = False
                state.dim_0 = 0.0
                state.dim_1 = 0.0
                state.dim_2 = 0.0
                state.actor_ptr = 0

    def visible_nodes(self) -> List[ProjectileNodeState]:
        return [s for s in self._nodes.values() if s.active]

    def actor_clusters(self):

        clusters = []
        threshold = 0.6

        for s in self._nodes.values():

            if not s.active:
                continue

            placed = False

            for cluster in clusters:

                ref = cluster[0]

                dx = s.x - ref.x
                dy = s.y - ref.y
                dz = s.z - ref.z

                dist = abs(dx) + abs(dy) + abs(dz)

                if dist < threshold:
                    cluster.append(s)
                    placed = True
                    break

            if not placed:
                clusters.append([s])

        return clusters
    def dump_active(self, max_nodes: int = 8) -> None:
        active = [(idx, s) for idx, s in self._nodes.items() if s.active]
        if not active:
            print("[NodeWatcher.dump] no active nodes")
            return

        print()
        print("[NodeWatcher.dump] active projectile nodes:")
        print(" idx        actor_ptr        x        y        z      d0      d1      d2")
        for idx, s in active[:max_nodes]:
            print(
                f"{idx:4d}   0x{s.actor_ptr:08X}   "
                f"{s.x:7.3f} {s.y:7.3f} {s.z:7.3f}   "
                f"{s.dim_0:5.3f} {s.dim_1:5.3f} {s.dim_2:5.3f}"
            )
        print()


# ----------------------------
# Translation tracker
# ----------------------------

@dataclass
class HitboxMotionState:
    still_frames: int = 0
    motion_frames: int = 0
    suppressed: bool = False
    prev_x: float = 0.0
    prev_y: float = 0.0
    initialized: bool = False


class MotionFilter:
    def __init__(self):
        self._states: Dict[Tuple[str, int], HitboxMotionState] = {}

    def _key(self, slot: str, idx: int) -> Tuple[str, int]:
        return (slot, idx)
    def cleanup(self):
        self._states = {
            k: v for k, v in self._states.items()
            if not v.suppressed or v.motion_frames > 0
        }
    def update(self, slot: str, idx: int, x: float, y: float, r: float) -> bool:
        key = self._key(slot, idx)
        if key not in self._states:
            self._states[key] = HitboxMotionState()
        state = self._states[key]

        if r <= 0.001:
            state.still_frames = 0
            state.motion_frames = 0
            state.suppressed = False
            state.initialized = False
            return False

        if not state.initialized:
            state.prev_x = x
            state.prev_y = y
            state.initialized = True
            state.still_frames = 0
            state.motion_frames = MOTION_FRAME_REQUIRED
            state.suppressed = False
            return True

        dx = x - state.prev_x
        dy = y - state.prev_y
        delta = math.sqrt(dx * dx + dy * dy)
        state.prev_x = x
        state.prev_y = y

        if delta >= MOTION_THRESHOLD:
            state.still_frames = 0
            state.motion_frames = min(state.motion_frames + 1, MOTION_FRAME_REQUIRED + 1)
            if state.suppressed and state.motion_frames >= MOTION_FRAME_REQUIRED:
                state.suppressed = False
        else:
            state.motion_frames = 0
            state.still_frames = min(state.still_frames + 1, STILL_FRAME_LIMIT + 1)
            if not state.suppressed and state.still_frames >= STILL_FRAME_LIMIT:
                state.suppressed = True

        return not state.suppressed

    def reset_slot(self, slot: str, count: int):
        for i in range(count):
            key = self._key(slot, i)
            if key in self._states:
                del self._states[key]


# ----------------------------
# Memory helpers
# ----------------------------

def rb(addr: int) -> int:
    v = rd32(addr & ~3)
    if v is None:
        return 0
    shift = (3 - (addr & 3)) * 8
    return (v >> shift) & 0xFF


def _rf(addr: int) -> float:
    v = rd32(addr)
    if v is None:
        return 0.0
    try:
        f = struct.unpack(">f", struct.pack(">I", v))[0]
        return f if math.isfinite(f) else 0.0
    except Exception:
        return 0.0


rf = _rf

_SENTINEL_BITS: frozenset = frozenset({0x3F7FFFFE, 0x3F800000})
_DIM_MAX: float = 2.0


def _clean_dim(v: float) -> float:

    if not math.isfinite(v):
        return 0.0

    if v < 0.01:
        return 0.0

    if v > 3.0:
        return 0.0

    return v


def _looks_like_ptr(v: int) -> bool:
    if v is None:
        return False
    if v < 0x90000000:
        return False
    if v > 0x94000000:
        return False
    if (v & 3) != 0:
        return False
    return True


def read_hitboxes(slot_base: int, layout: HitboxLayout):
    base = slot_base + layout.struct_shift
    out = []
    flag = rb(base + layout.off_flag)
    for b in layout.blocks:
        x = _rf(base + b + layout.off_x)
        y = _rf(base + b + layout.off_y)
        r = _rf(base + b + layout.off_r)
        out.append((x, y, r, flag))
    return out


def _valid_matrix_ptr(ptr: int) -> bool:
    ptr = int(ptr or 0) & 0xFFFFFFFF
    return 0x80000000 <= ptr < 0x81800000 and (ptr & 3) == 0


def _read_matrix_3x4(matrix_ptr: int) -> Optional[Tuple[float, ...]]:
    vals: List[float] = []
    for off in range(0, 0x30, 4):
        raw = rd32(matrix_ptr + off)
        if raw is None:
            return None
        try:
            value = struct.unpack(">f", struct.pack(">I", int(raw) & 0xFFFFFFFF))[0]
        except Exception:
            return None
        if not math.isfinite(value):
            return None
        vals.append(value)
    return tuple(vals)


def _valid_bone_matrix(matrix_ptr: int) -> bool:
    """Reject uninitialized/stale bone matrices without rejecting large fighters.

    PTX's live giant-body matrices still have normal-sized orthonormal rows.
    A parked teammate in the supplied dump instead has zero rows, a 59.0 row,
    and an absurd translation.  Those are descriptor leftovers, not renderable
    body geometry.
    """
    values = _read_matrix_3x4(matrix_ptr)
    if values is None:
        return False
    rows = ((values[0], values[1], values[2]),
            (values[4], values[5], values[6]),
            (values[8], values[9], values[10]))
    for row in rows:
        norm = math.sqrt(sum(component * component for component in row))
        if not (HURTBOX_MATRIX_MIN_ROW_NORM <= norm <= HURTBOX_MATRIX_MAX_ROW_NORM):
            return False
    if any(abs(values[idx]) > HURTBOX_MATRIX_MAX_TRANSLATION for idx in (3, 7, 11)):
        return False
    # A body matrix must span 3D space.  This rejects all-zero / collapsed rows
    # while allowing normal mirrored bones.
    det = (
        values[0] * ((values[5] * values[10]) - (values[6] * values[9]))
        - values[1] * ((values[4] * values[10]) - (values[6] * values[8]))
        + values[2] * ((values[4] * values[9]) - (values[5] * values[8]))
    )
    return 0.015 <= abs(det) <= 16.0


def _matrix_transform_point(matrix_ptr: int, lx: float, ly: float, lz: float) -> Tuple[float, float, float]:
    # Live bone matrices are row-major 3x4:
    #   [r00 r01 r02 tx]
    #   [r10 r11 r12 ty]
    #   [r20 r21 r22 tz]
    x = (_rf(matrix_ptr + 0x00) * lx) + (_rf(matrix_ptr + 0x04) * ly) + (_rf(matrix_ptr + 0x08) * lz) + _rf(matrix_ptr + 0x0C)
    y = (_rf(matrix_ptr + 0x10) * lx) + (_rf(matrix_ptr + 0x14) * ly) + (_rf(matrix_ptr + 0x18) * lz) + _rf(matrix_ptr + 0x1C)
    z = (_rf(matrix_ptr + 0x20) * lx) + (_rf(matrix_ptr + 0x24) * ly) + (_rf(matrix_ptr + 0x28) * lz) + _rf(matrix_ptr + 0x2C)
    return x, y, z


def _sane_world_box(x: float, y: float, z: float, r: float) -> bool:
    return (
        math.isfinite(x) and math.isfinite(y) and math.isfinite(z) and math.isfinite(r)
        and abs(x) < 40.0 and abs(y) < 40.0 and abs(z) < 40.0
        and HURTBOX_MIN_RADIUS <= r <= HURTBOX_MAX_RADIUS
    )


def read_hurtboxes(slot_name: str, slot_base: int) -> List[HurtboxState]:
    out: List[HurtboxState] = []
    for i in range(HURTBOX_DESC_COUNT):
        desc = slot_base + HURTBOX_DESC_BASE + i * HURTBOX_DESC_STRIDE
        raw_type = rd32(desc) or 0
        matrix_ptr = rd32(desc + 0x04) or 0

        # A blank descriptor means the current list is done.
        if raw_type == 0 and matrix_ptr == 0:
            break
        if not _valid_matrix_ptr(matrix_ptr):
            continue
        if not _valid_bone_matrix(matrix_ptr):
            continue

        lx = _rf(desc + 0x08)
        ly = _rf(desc + 0x0C)
        lz = _rf(desc + 0x10)
        r = _rf(desc + 0x14)
        x, y, z = _matrix_transform_point(matrix_ptr, lx, ly, lz)
        if not _sane_world_box(x, y, z, r):
            continue

        out.append(HurtboxState(
            slot_name=slot_name,
            slot_base=slot_base,
            index=i,
            desc_addr=desc,
            matrix_ptr=matrix_ptr,
            local_x=lx,
            local_y=ly,
            local_z=lz,
            x=x,
            y=y,
            z=z,
            radius=r,
            raw_type=raw_type,
        ))
    return out


def _slot_has_coherent_hurt_rig(slot_base: int, hlist: List[HurtboxState]) -> bool:
    """Return whether a slot currently owns a renderable on-stage body rig.

    This is intentionally geometry-based rather than move-ID based: assist
    standby can use the same idle action ID as the active fighter, but its bone
    list is partial/stale.  A real body has several sane descriptors clustered
    around the fighter root.
    """
    min_required = max(4, min(HURTBOX_MIN_COHERENT_DESCRIPTORS, len(hlist)))
    if len(hlist) < min_required:
        return False
    try:
        char_id = int(rd32(slot_base + OFF_CHAR_ID) or 0)
        if char_id <= 0 or char_id == 0xFFFFFFFF:
            return False
        # Do not hard-reject by +0x04 activity here.
        # Giant/solo layouts can place the visible opponent in a non-zero slot,
        # while true standby/parked partners are already rejected by the root
        # sentinel/offscreen tests below.
        root_x, root_y, root_z = read_fighter_root(slot_base)
    except Exception:
        return False
    if not all(math.isfinite(v) for v in (root_x, root_y, root_z)):
        return False
    # The common parked/off-stage sentinel is 90,90.
    if abs(root_y) >= 70.0 or abs(root_z) >= 70.0:
        return False

    coherent = 0
    for hb in hlist:
        if (abs(hb.x - root_x) <= HURTBOX_COHERENT_XY_RANGE
                and abs(hb.y - root_y) <= HURTBOX_COHERENT_XY_RANGE
                and abs(hb.z - root_z) <= HURTBOX_COHERENT_Z_RANGE):
            coherent += 1
    return coherent >= min_required


def _slot_name_from_base(ptr: int) -> Optional[str]:
    ptr = int(ptr or 0) & 0xFFFFFFFF
    for name, base in SLOT_BASES.items():
        if ptr == int(base):
            return name
    return None


def read_hit_contacts() -> List[HitContactState]:
    # Normal hit resolution records show source/target at +0x30/+0x34 and
    # resolved contact-ish point at +0x5C/+0x60.  +0x48 is 0 while the contact
    # is live, then returns to FFFFFFFF when the record is stale.
    out: List[HitContactState] = []
    seen = set()
    for addr in range(HIT_EVENT_SCAN_START, HIT_EVENT_SCAN_END, 4):
        source = rd32(addr + 0x30) or 0
        target = rd32(addr + 0x34) or 0
        source_slot = _slot_name_from_base(source)
        target_slot = _slot_name_from_base(target)
        if source_slot is None or target_slot is None or source == target:
            continue
        if (rd32(addr + 0x48) or 0) != 0:
            continue
        x = _rf(addr + 0x5C)
        y = _rf(addr + 0x60)
        z = _rf(addr + 0x64)
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            continue
        if abs(x) > 40.0 or abs(y) > 40.0 or abs(z) > 40.0:
            continue
        key = (source, target, round(x, 3), round(y, 3))
        if key in seen:
            continue
        seen.add(key)
        out.append(HitContactState(
            event_addr=addr,
            source=source,
            source_slot=source_slot,
            target=target,
            target_slot=target_slot,
            x=x,
            y=y,
            z=z,
            dir_x=_rf(addr + 0x6C),
            dir_y=_rf(addr + 0x70),
        ))
    return out


def hurtbox_contains_contact(hurtbox: HurtboxState, contact: HitContactState) -> bool:
    return math.hypot(hurtbox.x - contact.x, hurtbox.y - contact.y) <= (hurtbox.radius + HURTBOX_CONTACT_PAD)


def classify_hurtbox_regions(hlist: List[HurtboxState]) -> Dict[str, Any]:
    """Pick a compact body-region subset from the live skeletal circles.

    The descriptor order is not a stable semantic order, so this works from
    screen/world position: highest = head, central large zones = torso/pelvis,
    outer upper zones = arms, outer lower zones = legs. It is a display grouping
    only; it does not alter the actual collision geometry.
    """
    if not hlist:
        return {"region_by_index": {}, "major_indices": set(), "label_by_index": {}}

    weight = sum(max(0.05, float(hb.radius)) for hb in hlist)
    center_x = (
        sum(float(hb.x) * max(0.05, float(hb.radius)) for hb in hlist) / weight
        if weight > 0.0 else sum(float(hb.x) for hb in hlist) / float(len(hlist))
    )
    ys = [float(hb.y) for hb in hlist]
    top_y, bottom_y = max(ys), min(ys)
    span = max(0.25, top_y - bottom_y)
    arm_threshold = max(0.14, max(abs(float(hb.x) - center_x) for hb in hlist) * 0.50)

    used: set[int] = set()

    def choose(candidates: List[HurtboxState], score):
        best = None
        best_value = None
        for hb in candidates:
            if hb.index in used:
                continue
            value = score(hb)
            if best is None or value > best_value:
                best, best_value = hb, value
        if best is not None:
            used.add(best.index)
        return best

    head = choose(list(hlist), lambda hb: float(hb.y) + float(hb.radius) * 0.25)
    torso_band = [hb for hb in hlist if bottom_y + span * 0.48 <= float(hb.y) <= bottom_y + span * 0.84]
    torso = choose(torso_band or list(hlist), lambda hb: float(hb.radius) * 2.0 - abs(float(hb.x) - center_x))
    pelvis_band = [hb for hb in hlist if bottom_y + span * 0.25 <= float(hb.y) <= bottom_y + span * 0.60]
    pelvis = choose(pelvis_band or list(hlist), lambda hb: float(hb.radius) * 2.0 - abs(float(hb.x) - center_x))

    left_arm_pool = [hb for hb in hlist if float(hb.x) < center_x - arm_threshold and float(hb.y) > bottom_y + span * 0.36]
    right_arm_pool = [hb for hb in hlist if float(hb.x) > center_x + arm_threshold and float(hb.y) > bottom_y + span * 0.36]
    left_arm = choose(left_arm_pool or [hb for hb in hlist if float(hb.x) < center_x], lambda hb: abs(float(hb.x) - center_x) + float(hb.radius) * 0.15)
    right_arm = choose(right_arm_pool or [hb for hb in hlist if float(hb.x) > center_x], lambda hb: abs(float(hb.x) - center_x) + float(hb.radius) * 0.15)

    left_leg_pool = [hb for hb in hlist if float(hb.x) < center_x - arm_threshold * 0.35 and float(hb.y) < bottom_y + span * 0.48]
    right_leg_pool = [hb for hb in hlist if float(hb.x) > center_x + arm_threshold * 0.35 and float(hb.y) < bottom_y + span * 0.48]
    left_leg = choose(left_leg_pool or [hb for hb in hlist if float(hb.x) < center_x], lambda hb: (bottom_y + span * 0.52 - float(hb.y)) + float(hb.radius) * 0.10)
    right_leg = choose(right_leg_pool or [hb for hb in hlist if float(hb.x) > center_x], lambda hb: (bottom_y + span * 0.52 - float(hb.y)) + float(hb.radius) * 0.10)

    chosen = {
        "head": head,
        "torso": torso,
        "pelvis": pelvis,
        "arm_l": left_arm,
        "arm_r": right_arm,
        "leg_l": left_leg,
        "leg_r": right_leg,
    }
    pretty = {
        "head": "HEAD", "torso": "TORSO", "pelvis": "PELVIS",
        "arm_l": "ARM", "arm_r": "ARM", "leg_l": "LEG", "leg_r": "LEG",
    }
    region_by_index: Dict[int, str] = {}
    label_by_index: Dict[int, str] = {}
    for region, hb in chosen.items():
        if hb is None:
            continue
        region_by_index[hb.index] = region
        label_by_index[hb.index] = pretty[region]

    # Give the detailed/debug modes a section tint for every remaining circle.
    for hb in hlist:
        if hb.index in region_by_index:
            continue
        y_norm = (float(hb.y) - bottom_y) / span
        dx = float(hb.x) - center_x
        if y_norm >= 0.84:
            region = "head"
        elif y_norm <= 0.34:
            region = "leg_l" if dx < 0.0 else "leg_r"
        elif abs(dx) >= arm_threshold and y_norm >= 0.35:
            region = "arm_l" if dx < 0.0 else "arm_r"
        elif y_norm <= 0.54:
            region = "pelvis"
        else:
            region = "torso"
        region_by_index[hb.index] = region

    return {
        "region_by_index": region_by_index,
        "major_indices": set(label_by_index),
        "label_by_index": label_by_index,
    }


def should_draw_reaction_hurtboxes(slot_base: int) -> bool:
    state_id = read_state_id(slot_base)
    return state_id in HURTBOX_REACTION_STATE_IDS


def read_and_draw_hurtboxes_for_slot(overlay, slot_name: str, slot_base: int, contacts=None) -> int:
    contacts = contacts or []
    hurt_palette = COLORS.get(slot_name, [(120, 220, 255)])
    drawn = 0
    for hurt in read_hurtboxes(slot_name, slot_base):
        highlight = any(hurtbox_contains_contact(hurt, contact) for contact in contacts)
        color = hurt_palette[hurt.index % len(hurt_palette)]
        if not highlight:
            color = _dim_hitbox_color(color)
        overlay.draw_hurtbox(
            hurt.x, hurt.y, hurt.z, hurt.radius, color, hurt.label(), highlight=highlight
        )
        drawn += 1
    return drawn


def read_fighter_root(slot_base: int):
    return _rf(slot_base + 0xB0), _rf(slot_base + 0xB4), _rf(slot_base + 0xB8)


def read_camera_pos(layout: CameraLayout):
    return (
        _rf(layout.base + layout.off_x),
        _rf(layout.base + layout.off_y),
        _rf(layout.base + layout.off_z),
        _rf(layout.base + layout.off_w),
    )


def read_camera_view_affine(layout: CameraLayout) -> Tuple[float, float, float, float, float, float]:
    """Read the live Y/Z camera rows.

    The first row maps world Y/Z into screen Y. The second row maps world Y/Z
    into camera-space depth. Giant bodies have much larger Z spread, so their
    hurtboxes drift unless the per-point depth row is applied too.
    """
    return (
        _rf(layout.base + CAMERA_VIEW_COS_OFF),
        _rf(layout.base + CAMERA_VIEW_SIN_OFF),
        _rf(layout.base + CAMERA_VIEW_Y_TRANSLATE_OFF),
        _rf(layout.base + CAMERA_DEPTH_Y_COEFF_OFF),
        _rf(layout.base + CAMERA_DEPTH_Z_COEFF_OFF),
        _rf(layout.base + CAMERA_DEPTH_TRANSLATE_OFF),
    )


def update_projectile_nodes(tracker):

    node_idx = 0
    pools = resolve_projectile_pools()

    for pool in pools:

        for i in range(PROJECTILE_NODE_COUNT):

            node_addr = pool + i * PROJECTILE_NODE_STRIDE
            tracker.update_from_node(node_idx, node_addr)

            node_idx += 1

# ----------------------------
# Win32 helpers
# ----------------------------

def find_dolphin_hwnd() -> Optional[int]:
    candidates: List[Tuple[int, int, str]] = []

    def score_title(t: str) -> int:
        tl = t.lower()
        if "dolphin" not in tl:
            return -10_000
        s = 0
        if "|" in t:
            s += 50
            s += min(30, t.count("|") * 5)
        for tok in ("jit", "jit64", "opengl", "vulkan", "d3d", "direct3d", "hle"):
            if tok in tl:
                s += 20
        if "(" in t and ")" in t:
            s += 30
        for bad in ("memory", "watch", "log", "breakpoint", "register", "disassembly", "config", "settings"):
            if bad in tl:
                s -= 25
        if t.count("|") >= 3:
            s += 20
        return s

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if not title:
            return
        if "dolphin" not in title.lower():
            return
        candidates.append((score_title(title), hwnd, title))

    win32gui.EnumWindows(cb, None)
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def get_client_screen_rect(hwnd: int):
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    tl = win32gui.ClientToScreen(hwnd, (left, top))
    br = win32gui.ClientToScreen(hwnd, (right, bottom))
    return tl[0], tl[1], br[0] - tl[0], br[1] - tl[1]


def apply_overlay_style(hwnd: int) -> None:
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    style &= ~(
        win32con.WS_CAPTION
        | win32con.WS_THICKFRAME
        | win32con.WS_MINIMIZE
        | win32con.WS_MAXIMIZE
        | win32con.WS_SYSMENU
    )
    style |= win32con.WS_POPUP
    win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)

    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    # Overlay must never intercept clicks intended for Dolphin or the main GUI.
    # It is a visual layer only; all control stays in the existing application.
    ex |= (
        win32con.WS_EX_LAYERED
        | win32con.WS_EX_TRANSPARENT
        | win32con.WS_EX_NOACTIVATE
        | win32con.WS_EX_TOOLWINDOW
    )
    ex &= ~win32con.WS_EX_TOPMOST
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)

    win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 0, win32con.LWA_COLORKEY)
    win32gui.SetWindowPos(
        hwnd,
        win32con.HWND_NOTOPMOST,
        0,
        0,
        0,
        0,
        win32con.SWP_FRAMECHANGED
        | win32con.SWP_NOMOVE
        | win32con.SWP_NOSIZE
        | win32con.SWP_NOACTIVATE,
    )


def sync_overlay_to_dolphin(dolphin_hwnd: int, overlay_hwnd: int):
    x, y, w, h = get_client_screen_rect(dolphin_hwnd)
    win32gui.SetWindowPos(
        overlay_hwnd,
        win32con.HWND_NOTOPMOST,
        x,
        y,
        w,
        h,
        win32con.SWP_NOACTIVATE,
    )
    return w, h


def _legend_window_worker() -> None:
    try:
        import tkinter as tk
    except Exception:
        return

    root = tk.Tk()
    root.title("TvC Hitbox Legend")
    root.configure(bg="#101820")
    root.resizable(False, False)
    try:
        root.attributes("-topmost", False)
    except Exception:
        pass

    items = [
        ("#ff7a38", "HIT", "player attack hitbox"),
        ("#79efff", "HURT", "normal hurtbox (body-section colors in clean mode)"),
        ("#6dff8c", "INVUL", "hurtbox while startup invulnerability is active"),
        ("#ffffff", "CONTACT", "resolver contact marker / impact focus"),
        ("#ffb478", "PROJECTILE", "projectile hitbox or sweep"),
    ]

    tk.Label(root, text="Overlay legend", fg="#e8f4ff", bg="#101820", font=("Consolas", 12, "bold")).pack(anchor="w", padx=10, pady=(10, 6))
    tk.Label(root, text="Hurtbox view", fg="#b8d8f0", bg="#101820", font=("Consolas", 10, "bold")).pack(anchor="w", padx=10)

    mode_row = tk.Frame(root, bg="#101820")
    mode_row.pack(anchor="w", padx=10, pady=(4, 8))
    mode_buttons = {}

    def _refresh_mode_buttons() -> None:
        current = _read_hurtbox_view_mode()
        for mode, button in mode_buttons.items():
            selected = (mode == current)
            button.configure(
                bg="#2f6f8c" if selected else "#1a2a38",
                fg="#ffffff" if selected else "#b8d8f0",
                activebackground="#3a83a3" if selected else "#253b4d",
                activeforeground="#ffffff",
                relief="sunken" if selected else "raised",
            )

    def _choose_mode(mode: str) -> None:
        set_hurtbox_view_mode(mode)
        _refresh_mode_buttons()

    for _mode, _label in (("clean", "Clean"), ("detailed", "Detailed"), ("debug", "Debug")):
        btn = tk.Button(
            mode_row,
            text=_label,
            command=lambda m=_mode: _choose_mode(m),
            font=("Consolas", 10, "bold"),
            bd=1,
            padx=10,
            pady=3,
            cursor="hand2",
        )
        btn.pack(side="left", padx=(0, 5))
        mode_buttons[_mode] = btn
    _refresh_mode_buttons()

    tk.Label(root, text="Clean = core sections  •  Detailed = all boxes, helpers dim  •  Debug = all labels", fg="#b8d8f0", bg="#101820", font=("Consolas", 9)).pack(anchor="w", padx=10, pady=(0, 8))

    body = tk.Frame(root, bg="#101820")
    body.pack(fill="both", expand=True, padx=10, pady=(0, 10))
    for color, name, desc in items:
        row = tk.Frame(body, bg="#101820")
        row.pack(anchor="w", fill="x", pady=2)
        sw = tk.Canvas(row, width=18, height=18, bg="#101820", highlightthickness=0, bd=0)
        sw.create_oval(2, 2, 16, 16, outline=color, width=3)
        sw.pack(side="left")
        tk.Label(row, text=name, fg="#f4fbff", bg="#101820", font=("Consolas", 10, "bold"), width=10, anchor="w").pack(side="left", padx=(8, 0))
        tk.Label(row, text=desc, fg="#d6e5ef", bg="#101820", font=("Consolas", 10), anchor="w").pack(side="left", padx=(6, 0))

    shown = False

    def _manual_hide() -> None:
        global _legend_manual_hidden
        _legend_manual_hidden = True
        root.withdraw()

    def _sync_visibility() -> None:
        nonlocal shown
        effective = bool(_legend_visible_requested and not _legend_manual_hidden)
        # Only change visibility when the overlay toggle changes.  Never call
        # lift(), never force-focus, and never resurrect a manually hidden or
        # minimized legend every frame.
        if effective != shown:
            try:
                if effective:
                    root.deiconify()
                else:
                    root.withdraw()
            except Exception:
                pass
            shown = effective
        try:
            _refresh_mode_buttons()
        except Exception:
            pass
        root.after(200, _sync_visibility)

    root.withdraw()
    root.protocol("WM_DELETE_WINDOW", _manual_hide)
    root.after(0, _sync_visibility)
    root.mainloop()


def set_legend_window_visible(visible: bool) -> None:
    global _legend_window_started, _legend_visible_requested, _legend_manual_hidden
    was_visible = bool(_legend_visible_requested)
    _legend_visible_requested = bool(visible)
    # Turning the overlay off resets a manual hide.  The next explicit overlay
    # enable can show the legend again, but it never reappears while still on.
    if not _legend_visible_requested:
        _legend_manual_hidden = False
        return
    if not was_visible:
        _legend_manual_hidden = False
    if _legend_window_started:
        return
    _legend_window_started = True
    try:
        threading.Thread(target=_legend_window_worker, name="hitbox_legend_window", daemon=True).start()
    except Exception:
        _legend_window_started = False


def _read_live_invuln_frames(slot_base: int) -> int:
    """Read the active action's +0x1218 invul signature without profile cache.

    This is intentionally a small direct read so the hitbox layer stays correct
    even while Frame Data's optional profile work is still running.
    """
    try:
        chr_tbl = rd32(slot_base + OFF_CHR_TBL) or 0
        action_id = decode_state_id(read_state_raw(slot_base))
        if not (0x90000000 <= chr_tbl <= 0x94000000) or action_id <= 0 or action_id > 0x1FFF:
            return 0
        rel = rd32(chr_tbl + (int(action_id) * 4)) or 0
        if rel <= 0 or rel > 0x00400000:
            return 0
        root = chr_tbl + rel
        # Shoryu stores the header 8 bytes before its action root; Jun 6B has
        # it inside the action section.  Scan both layouts in one compact read.
        block = rbytes(max(0, root - 8), 0x900)
        if not block:
            return 0
        sig = b"\x04\x01\x60\x00\x00\x00\x12\x18\x3F\x00\x00\x00"
        best = 0
        pos = 0
        while True:
            idx = block.find(sig, pos)
            if idx < 0:
                break
            pos = idx + 1
            if idx + 16 > len(block):
                continue
            raw = int.from_bytes(block[idx + 12:idx + 16], "big", signed=False)
            frames = (raw >> 8) & 0xFFFF if (raw & 0xFF) == 0 else (raw & 0xFFFF)
            if 3 <= frames <= 120:
                best = max(best, frames)
        return best
    except Exception:
        return 0





# ----------------------------
# Overlay
# ----------------------------

class Overlay:
    def __init__(self, cfg: DisplayConfig):
        self.cfg = cfg
        self.cam_x = 0.0
        self.cam_y = 0.0
        self.cam_z = 0.0
        self.ref_cam_z = None
        self.cam_view_cos = 1.0
        self.cam_view_sin = 0.0
        self.cam_view_y_translate = 0.0
        self.cam_depth_y_coeff = 0.0
        self.cam_depth_z_coeff = 1.0
        self.cam_depth_translate = 0.0
        self.cam_view_affine_valid = False
        self.cam_depth_affine_valid = False
        self.giant_x_anchor: Optional[float] = None
        self.ppu = cfg.baseline_ppu
        self.zoom = cfg.zoom
        self.w = cfg.baseline_w
        self.h = cfg.baseline_h
        self.cx = self.w // 2
        self.cy = self.h // 2 + cfg.center_y_offset_px
        self.screen: Optional[pygame.Surface] = None
        self.font_small: Optional[pygame.font.Font] = None
        self.font_hud: Optional[pygame.font.Font] = None
        self.debug_axes = cfg.show_debug_axes
        self.viewport_scale = self.h / 720.0
        self.aspect_ratio = self.w / float(self.h)
        self.base_aspect = 4.0 / 3.0
        self.x_aspect_scale = self.base_aspect / self.aspect_ratio
        self.window_aspect = self.w / float(self.h)
        self.stretch_factor = self.window_aspect / self.base_aspect

        self.frame_index = 0
        self.hitbox_spawn_start: Dict[str, int] = {}
        self.hitbox_last_seen: Dict[str, int] = {}

    def init_pygame(self) -> int:
        pygame.init()
        self.font_small = pygame.font.SysFont("consolas", 11)
        self.font_hud = pygame.font.SysFont("consolas", 13, bold=True)
        self.screen = pygame.display.set_mode((self.w, self.h), pygame.SRCALPHA)
        pygame.display.set_caption("TvC Hitbox Overlay v3")

        icon_path = os.path.join("assets", "portraits", "Placeholder.png")
        if not os.path.exists(icon_path):
            icon_path = os.path.join("assets", "icon.png")
        if os.path.exists(icon_path):
            icon = pygame.image.load(icon_path).convert_alpha()
            pygame.display.set_icon(icon)

        hwnd = pygame.display.get_wm_info()["window"]
        apply_overlay_style(hwnd)
        return hwnd

    def on_resize(self, w: int, h: int):
        if w <= 0 or h <= 0:
            return
        if w == self.w and h == self.h:
            return
        self.w, self.h = w, h
        self.screen = pygame.display.set_mode((w, h), pygame.SRCALPHA)
        self.viewport_scale = h / 720.0
        self.aspect_ratio = w / float(h)
        self.base_aspect = 4.0 / 3.0
        self.x_aspect_scale = self.base_aspect / self.aspect_ratio
        self.cx = int(w * 0.5)
        self.cy = int(h * 0.5)
        self.window_aspect = w / float(h)
        self.stretch_factor = self.window_aspect / self.base_aspect

    def set_camera_view_affine(
        self,
        cos_yz: float,
        sin_yz: float,
        translate_y: float,
        depth_y_coeff: float = 0.0,
        depth_z_coeff: float = 1.0,
        depth_translate: float = 0.0,
    ) -> None:
        try:
            valid = (
                math.isfinite(cos_yz) and math.isfinite(sin_yz) and math.isfinite(translate_y)
                and 0.75 <= abs(cos_yz) <= 1.25
                and abs(sin_yz) <= 0.60
            )
        except Exception:
            valid = False
        if valid:
            self.cam_view_cos = float(cos_yz)
            self.cam_view_sin = float(sin_yz)
            self.cam_view_y_translate = float(translate_y)
            self.cam_view_affine_valid = True
        else:
            self.cam_view_cos = 1.0
            self.cam_view_sin = 0.0
            self.cam_view_y_translate = 0.0
            self.cam_view_affine_valid = False

        try:
            depth_valid = (
                math.isfinite(depth_y_coeff) and math.isfinite(depth_z_coeff) and math.isfinite(depth_translate)
                and abs(depth_z_coeff) >= 0.75 and abs(depth_z_coeff) <= 1.25
                and abs(depth_y_coeff) <= 0.60
            )
        except Exception:
            depth_valid = False
        if depth_valid:
            self.cam_depth_y_coeff = float(depth_y_coeff)
            self.cam_depth_z_coeff = float(depth_z_coeff)
            self.cam_depth_translate = float(depth_translate)
            self.cam_depth_affine_valid = True
        else:
            self.cam_depth_y_coeff = 0.0
            self.cam_depth_z_coeff = 1.0
            self.cam_depth_translate = 0.0
            self.cam_depth_affine_valid = False

    def set_giant_x_anchor(self, anchor_x: Optional[float]) -> None:
        # Compatibility only.  Giant mode now uses the real camera X just like
        # the normal projection path; no synthetic midpoint anchor is applied.
        self.giant_x_anchor = None

    def world_to_screen(self, world_x: float, world_y: float, world_z: float):
        """Single camera projection for normal and giant fighters.

        Giant mode is not given a fabricated midpoint, X/Y multiplier, or screen
        offset.  It uses the same camera X/Y transform as ordinary fighters;
        the only difference is the live camera distance (Z), exactly as the
        game camera reports it.
        """
        if self.ref_cam_z is None and abs(self.cam_z) > 0.0001:
            self.ref_cam_z = self.cam_z

        effective_ref_cam_z = self.ref_cam_z
        # When launched directly in giant mode, seed from the ordinary camera
        # distance rather than from the already-zoomed-out giant distance.
        if abs(self.cam_z) > 0.0001 and self.cam_z >= GIANT_CAMERA_Z_THRESHOLD:
            if effective_ref_cam_z is None or effective_ref_cam_z >= GIANT_CAMERA_Z_THRESHOLD:
                effective_ref_cam_z = GIANT_FALLBACK_REF_CAM_Z

        camera_scale = (
            effective_ref_cam_z / self.cam_z
            if (effective_ref_cam_z is not None and abs(self.cam_z) > 0.0001)
            else 1.0
        )
        zoom_scale = self.cfg.baseline_ppu * self.viewport_scale * camera_scale
        sx = self.cx + (world_x - self.cam_x) * zoom_scale * self.stretch_factor

        if self.cam_view_affine_valid:
            center_world_y = self.cam_y - WORLD_Y_OFFSET
            view_y = (self.cam_view_cos * world_y) + (self.cam_view_sin * world_z) + self.cam_view_y_translate
            center_view_y = (self.cam_view_cos * center_world_y) + self.cam_view_y_translate
            projected_y = view_y - center_view_y
        else:
            projected_y = (world_y - self.cam_y) + WORLD_Y_OFFSET

        sy = self.cy - (projected_y * zoom_scale)
        if abs(self.cam_z) >= GIANT_CAMERA_Z_THRESHOLD:
            sy += GIANT_Y_SCREEN_OFFSET
        return int(sx), int(sy), 1.0, zoom_scale

    def clear(self):
        self.frame_index += 1

        stale = [
            label
            for label, last_seen in self.hitbox_last_seen.items()
            if self.frame_index - last_seen > 2
        ]
        for label in stale:
            self.hitbox_last_seen.pop(label, None)
            self.hitbox_spawn_start.pop(label, None)

        self.screen.fill(COL_BG)

    def draw_debug_axes(self):
        if not self.debug_axes:
            return
        pygame.draw.line(self.screen, COL_DEBUG, (0, self.h // 2), (self.w, self.h // 2), 1)
        pygame.draw.line(self.screen, COL_DEBUG, (self.w // 2, 0), (self.w // 2, self.h), 1)

    def _project_hitbox(self, x, y, z, r):
        if r <= 0.001 or not math.isfinite(r):
            return None
        if r > HITBOX_MAX_RENDER_RADIUS:
            return None
        if not math.isfinite(x) or not math.isfinite(y):
            return None
        r = min(r, self.cfg.max_radius_units)
        sx, sy, depth, focal = self.world_to_screen(x, y, z)
        rpx = max(2, int((r / depth) * focal))
        if rpx <= 0 or rpx > 5000:
            return None
        # Do not build/blit a surface for off-stage pools or stale descriptors.
        # This also removes partial circles that were being clipped at screen edges
        # for fighters parked at ±30 in assist standby.
        margin = max(72, rpx + 28)
        if (sx + rpx) < -margin or (sx - rpx) > (self.w + margin) or (sy + rpx) < -margin or (sy - rpx) > (self.h + margin):
            return None
        return sx, sy, rpx

    def draw_hitbox(self, x, y, z, r, color, label, is_active=False, invuln=False):
        result = self._project_hitbox(x, y, z, r)
        if result is None:
            return
        sx, sy, rpx = result

        r_c, g_c, b_c = color[:3]

        self.hitbox_last_seen[label] = self.frame_index
        if label not in self.hitbox_spawn_start:
            self.hitbox_spawn_start[label] = self.frame_index

        age = self.frame_index - self.hitbox_spawn_start[label]
        spawn_t = min(1.0, age / float(max(1, HITBOX_SPAWN_FRAMES - 1)))

        draw_rpx = max(2, int(rpx * (0.72 + 0.28 * spawn_t)))
        show_cross = age >= 2

        pad = 12
        size = draw_rpx * 2 + pad * 2 + 18
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        cx = cy = size // 2

        pulse_t = 0.0
        if is_active:
            pulse_t = 0.5 + 0.5 * math.sin(self.frame_index * HITBOX_ACTIVE_PULSE_SPEED)

        # Force a strong "danger" visual identity that separates from cyan hurtboxes.
        core_fill = (255, 105, 40) if is_active else (215, 78, 38)
        core_hi   = (255, 182, 70)
        white_rim = (255, 248, 235)
        dark_rim  = (24, 8, 4)

        halo_alpha = int((42 if is_active else 28) + 26 * pulse_t)
        pygame.draw.circle(surf, (*core_fill, halo_alpha), (cx, cy), draw_rpx + 7)

        # Invulnerable startup stays obvious even when the attacker already has
        # orange active hitboxes: preserve the danger core, add a neon-green
        # outer ring that matches the defender's green hurtbox outlines.
        if invuln:
            pygame.draw.circle(surf, (88, 255, 130, 235), (cx, cy), draw_rpx + 10, 3)
            pygame.draw.circle(surf, (178, 255, 196, 120), (cx, cy), draw_rpx + 14, 1)

        # Dark punch-out ring so the hitbox remains visible over dense hurtbox lines.
        pygame.draw.circle(surf, (*dark_rim, 190), (cx, cy), draw_rpx + 4, 5)
        pygame.draw.circle(surf, (*white_rim, 230), (cx, cy), draw_rpx + 1, 3)

        fill_alpha = 180 if is_active else 148
        pygame.draw.circle(surf, (*core_fill, fill_alpha), (cx, cy), draw_rpx)
        pygame.draw.circle(surf, (*core_hi, 120 if is_active else 90), (cx, cy), max(draw_rpx - 4, 1))
        pygame.draw.circle(surf, (255, 255, 255, 62 if is_active else 40), (cx, cy), max(draw_rpx - 7, 1), 1)

        # Active pulse ring.
        if is_active:
            pulse_px = int(2 + 4 * pulse_t)
            pulse_alpha = int(80 + 80 * pulse_t)
            pygame.draw.circle(surf, (*core_hi, pulse_alpha), (cx, cy), draw_rpx + 8 + pulse_px, 2)

        self.screen.blit(surf, (sx - cx, sy - cy))

        if show_cross:
            cs = max(4, min(10, draw_rpx // 3))
            cross_s = pygame.Surface((cs * 2 + 2, cs * 2 + 2), pygame.SRCALPHA)
            pygame.draw.line(cross_s, (255, 250, 242, 220), (0, cs + 1), (cs * 2 + 2, cs + 1), 1)
            pygame.draw.line(cross_s, (255, 250, 242, 220), (cs + 1, 0), (cs + 1, cs * 2 + 2), 1)
            self.screen.blit(cross_s, (sx - cs - 1, sy - cs - 1))

        if rpx >= 12 and self.font_small is not None:
            label_txt = label if str(label).startswith("HIT ") else f"HIT {label}"
            txt = self.font_small.render(label_txt, True, (255, 245, 230))
            tw, th = txt.get_size()
            bx = sx + rpx + 6
            by = sy - 10
            badge = pygame.Surface((tw + 8, th + 4), pygame.SRCALPHA)
            pygame.draw.rect(badge, (18, 6, 4, 210), (0, 0, tw + 8, th + 4), border_radius=4)
            pygame.draw.rect(badge, (255, 168, 70, 255), (0, 0, tw + 8, th + 4), 1, border_radius=4)
            badge.blit(txt, (4, 2))
            self.screen.blit(badge, (bx, by))

    def draw_hurtbox(self, x, y, z, r, color, label, highlight=False, detail=True, invuln=False, show_label=False):
        """Polished cached hurtbox draw.

        Same data and same geometry as V13.  The difference is visual language:
        hitboxes are solid/warm/labeled; hurtboxes are blue glass outlines.
        """
        result = self._project_hitbox(x, y, z, r)
        if result is None:
            return
        sx, sy, rpx = result
        rpx = max(2, int(rpx))

        surf = _get_cached_hurtbox_surface(rpx, color[:3], bool(highlight), bool(detail), bool(invuln))
        cx = surf.get_width() // 2
        cy = surf.get_height() // 2
        self.screen.blit(surf, (sx - cx, sy - cy))

        if (highlight or show_label) and self.font_small is not None:
            if highlight:
                fg = (255, 245, 130)
            elif invuln:
                fg = (115, 255, 155)
            else:
                fg = (228, 246, 255)
            shadow = self.font_small.render(label, True, (0, 0, 0))
            txt = self.font_small.render(label, True, fg)
            self.screen.blit(shadow, (sx + rpx + 7, sy - 7))
            self.screen.blit(txt, (sx + rpx + 6, sy - 8))

    def draw_hit_contact(self, contact: HitContactState, color=(255, 255, 255)):
        try:
            sx, sy, _d, _f = self.world_to_screen(contact.x, contact.y, contact.z)
            d = 8
            pygame.draw.line(self.screen, color, (sx - d, sy - d), (sx + d, sy + d), 2)
            pygame.draw.line(self.screen, color, (sx - d, sy + d), (sx + d, sy - d), 2)
            if self.font_small is not None:
                txt = self.font_small.render(contact.label(), True, color)
                self.screen.blit(txt, (sx + d + 4, sy - 8))
        except Exception:
            pass

    def draw_projectile_hitbox(
        self,
        x,
        y,
        z,
        r,
        color,
        label,
        sweep_x=None,
        sweep_y=None,
        sweep_z=None,
        contact_x=None,
        contact_y=None,
        contact_z=None,
        contact_valid=False,
        root_x=None,
        root_y=None,
        root_z=None,
    ):
        result = self._project_hitbox(x, y, z, r)
        if result is None:
            return
        sx, sy, rpx = result

        r_c, g_c, b_c = color[:3]

        # Candidate local capsule. Unlike the failed +0xE0/+0xE4 sweep, this
        # uses caller-provided short endpoints: root -> root + dir*extent.
        if sweep_x is not None and sweep_y is not None:
            try:
                ex, ey, _depth_e, _focal_e = self.world_to_screen(x, y, z)
                bx, by, _depth_b, _focal_b = self.world_to_screen(sweep_x, sweep_y, sweep_z or z)
                width = max(2, min(180, int(rpx * 2)))
                pad = max(12, rpx + 10)
                min_x = min(bx, ex) - pad
                min_y = min(by, ey) - pad
                max_x = max(bx, ex) + pad
                max_y = max(by, ey) + pad
                w = max(1, max_x - min_x)
                h = max(1, max_y - min_y)
                surf = pygame.Surface((w, h), pygame.SRCALPHA)
                p0 = (bx - min_x, by - min_y)
                p1 = (ex - min_x, ey - min_y)
                # Same projectile geometry as the frame-verified build; only the paint
                # changed.  Lower fill alpha keeps large missile showers readable
                # without clamping radius, hiding actors, or altering labels.
                pygame.draw.line(surf, (20, 8, 4, 115), p0, p1, width + 4)
                pygame.draw.line(surf, (255, 126, 50, 30), p0, p1, width)
                pygame.draw.circle(surf, (20, 8, 4, 115), p0, max(1, width // 2) + 2)
                pygame.draw.circle(surf, (255, 126, 50, 30), p0, max(1, width // 2))
                pygame.draw.circle(surf, (20, 8, 4, 115), p1, max(1, width // 2) + 2)
                pygame.draw.circle(surf, (255, 126, 50, 30), p1, max(1, width // 2))
                pygame.draw.line(surf, (255, 248, 235, 205), p0, p1, max(1, min(3, width // 3)))
                pygame.draw.line(surf, (255, 108, 38, 235), p0, p1, max(1, min(2, width // 4)))
                pygame.draw.circle(surf, (255, 248, 235, 235), p0, max(3, min(7, width // 5)) + 1, 1)
                pygame.draw.circle(surf, (255, 108, 38, 245), p0, max(3, min(7, width // 5)), 1)
                pygame.draw.circle(surf, (255, 248, 235, 235), p1, max(3, min(7, width // 5)) + 1, 1)
                pygame.draw.circle(surf, (255, 108, 38, 245), p1, max(3, min(7, width // 5)), 1)
                self.screen.blit(surf, (min_x, min_y))
            except Exception:
                pass
        else:
            pad = 8
            size = rpx * 2 + pad * 2
            surf = pygame.Surface((size, size), pygame.SRCALPHA)
            cx = cy = rpx + pad
            pygame.draw.circle(surf, (20, 8, 4, 200), (cx, cy), rpx + 4, 4)
            pygame.draw.circle(surf, (255, 248, 235, 220), (cx, cy), rpx + 1, 2)
            pygame.draw.circle(surf, (255, 108, 38, 235), (cx, cy), rpx, 2)
            pygame.draw.circle(surf, (255, 120, 45, 42), (cx, cy), rpx)
            self.screen.blit(surf, (sx - rpx - pad, sy - rpx - pad))

        # Root marker: actor transform point, not the hit result.
        if root_x is not None and root_y is not None:
            try:
                rx, ry, _d, _f = self.world_to_screen(root_x, root_y, root_z or z)
                d = 5
                pygame.draw.line(self.screen, (r_c, g_c, b_c), (rx - d, ry), (rx + d, ry), 1)
                pygame.draw.line(self.screen, (r_c, g_c, b_c), (rx, ry - d), (rx, ry + d), 1)
            except Exception:
                pass

        # Contact marker: only after the linked hit-state reports a target ptr.
        # It should snap to the defender on hit; label it so it is not mistaken
        # for the live hitbox.
        if contact_valid and contact_x is not None and contact_y is not None:
            try:
                cx, cy, _d, _f = self.world_to_screen(contact_x, contact_y, contact_z or z)
                d = max(6, min(12, rpx // 2))
                contact_col = (255, 255, 255)
                pygame.draw.polygon(
                    self.screen,
                    contact_col,
                    [(cx, cy - d), (cx + d, cy), (cx, cy + d), (cx - d, cy)],
                    2,
                )
                if self.font_small is not None:
                    txt = self.font_small.render("CONTACT", True, contact_col)
                    self.screen.blit(txt, (cx + d + 3, cy - 8))
            except Exception:
                pass

        if rpx >= 8 and self.font_small is not None:
            label_text = f"{label} r={r:.2f}"
            # Same label content and placement; add a tiny shadow so it is readable
            # without needing heavy projectile fills behind it.
            shadow = self.font_small.render(label_text, True, (0, 0, 0))
            txt = self.font_small.render(label_text, True, color[:3])
            self.screen.blit(shadow, (sx + rpx + 6, sy - 7))
            self.screen.blit(txt, (sx + rpx + 5, sy - 8))


    def draw_hud(self, counts, motion_filter: MotionFilter, node_tracker: ProjectileNodeTracker, hurt_counts=None):
        # The old multi-field debug header was useful during bring-up, but it
        # obscures the playfield in normal use.  The legend now lives in a
        # separate helper window instead of the top-left overlay area.
        return

    def present(self):
        pygame.display.flip()


def _derive_giant_x_anchor() -> Optional[float]:
    """Return the live on-stage fighter midpoint for giant camera framing.

    In giant matches the camera object keeps X at a neutral scene origin even
    though the visible fight is framed around the giant/opponent pair.  Using
    that stale zero makes the error grow as either fighter walks away from
    center.  Root X midpoint is live, cheap, and follows the real framing.
    """
    xs: List[float] = []
    for slot_base in SLOT_BASES.values():
        try:
            char_id = int(rd32(slot_base + OFF_CHAR_ID) or 0)
            if char_id <= 0 or char_id == 0xFFFFFFFF:
                continue
            x, y, z = read_fighter_root(slot_base)
            if not all(math.isfinite(v) for v in (x, y, z)):
                continue
            if abs(y) >= 70.0 or abs(z) >= 70.0:
                continue
            xs.append(float(x))
        except Exception:
            continue
    if len(xs) >= 2:
        return (min(xs) + max(xs)) * 0.5
    if len(xs) == 1:
        return xs[0]
    return None


def _slot_root_is_on_screen(overlay: Overlay, slot_base: int) -> bool:
    """Cull an entire parked/off-stage fighter before stale box descriptors draw."""
    try:
        x, y, z = read_fighter_root(slot_base)
        if not all(math.isfinite(v) for v in (x, y, z)):
            return False
        sx, sy, _d, _f = overlay.world_to_screen(x, y, z)
        # Giant framing can place a valid opponent near/just beyond the edge
        # before their own circles re-enter view. Keep this broad; individual
        # geometry still clips naturally at the pygame surface.
        margin = 700
        return (-margin <= sx <= overlay.w + margin) and (-margin <= sy <= overlay.h + margin)
    except Exception:
        return False


class HitboxRenderer:
    def __init__(self) -> None:
        self.motion_filter = MotionFilter()
        self.scanner = ProjectileScanner()

        total_nodes = len(PROJECTILE_POOLS) * PROJECTILE_NODE_COUNT
        self.node_tracker = ProjectileNodeTracker(total_nodes)

        self.cached_projectiles: List[ProjectileActorState] = []
        self.cached_hurtboxes: Dict[str, List[HurtboxState]] = {}
        self.cached_hit_contacts: List[HitContactState] = []
        self.hurt_counts: Dict[str, int] = {}
        self.slot_renderable: Dict[str, bool] = {slot: False for slot in SLOT_BASES}
        self._last_hurt_update_ms: int = 0
        self._last_contact_update_ms: int = 0
        self.last_char_ids: Dict[str, int] = {}
        self.last_counts: Dict[str, int] = {}
        self.fd_by_slot: Dict[str, Dict[int, MoveFrameData]] = {}
        self._fd_scan_done = False
        self._last_camera_mode_key: Optional[str] = None

        self.w = DISPLAY.baseline_w
        self.h = DISPLAY.baseline_h

        self.overlay = Overlay(DISPLAY)
        self.overlay.font_small = pygame.font.SysFont("consolas", 11)
        self.overlay.font_hud = pygame.font.SysFont("consolas", 13, bold=True)

    @staticmethod
    def _hitboxes_enabled(control=None) -> bool:
        """Attack/projectile hitbox layer toggle."""
        return bool(getattr(control, "show_hitboxes", True))

    @staticmethod
    def _hurtboxes_enabled(control=None) -> bool:
        """Defender/body hurtbox layer toggle."""
        return bool(getattr(control, "show_hurtboxes", True)) and _hurtbox_layer_requested()

    def _any_collision_layer_enabled(self, control=None) -> bool:
        return self._hitboxes_enabled(control) or self._hurtboxes_enabled(control)

    def _clear_runtime_hitbox_state(self) -> None:
        self.cached_projectiles.clear()
        self.cached_hurtboxes.clear()
        self.cached_hit_contacts.clear()
        self.hurt_counts = {}
        self.slot_renderable = {slot: False for slot in SLOT_BASES}
        self.last_counts = {}
        self.motion_filter._states.clear()
        # Keep the camera zoom anchor across character/giant switches.
        # Giant mode uses a farther live cam_z, and the normal ref_cam_z anchor
        # is what shrinks projection back into the correct screen scale.
        for node in getattr(self.node_tracker, "_nodes", {}).values():
            node.active = False
            node.inactive_frames = PROJECTILE_DESPAWN_FRAMES
            node.dim_0 = 0.0
            node.dim_1 = 0.0
            node.dim_2 = 0.0
            node.actor_ptr = 0

    def on_resize(self, w: int, h: int) -> None:
        if w <= 0 or h <= 0:
            return
        self.w = w
        self.h = h
        self.overlay.on_resize(w, h)
        _surface_cache.clear()

    def _refresh_frame_data_cache(self) -> None:
        self.fd_by_slot = build_frame_data_cache()
        self._fd_scan_done = True

    def _frame_gate_for_slot(
        self,
        slot_name: str,
        slot_base: int,
        state_id: int,
        hitbox_flag: int,
    ) -> Tuple[bool, bool, Optional[int], Optional[MoveFrameData]]:
        """Return (draw_now, is_active, action_frame, frame_data).

        Frame-data lookup is also the source of the on-screen move label.  For
        a labelled action, keep its currently allocated attack geometry visible
        for the *entire lifetime of that state*: startup and recovery are dim;
        only the resolved active window gets the bright/pulsing treatment.
        The geometry disappears as soon as the labelled action/state ends.

        This deliberately does not use ``active_end`` as a visibility cutoff.
        In TvC, the live hitbox descriptors remain useful through recovery,
        even after their collision flag has turned off.  Restricting rendering
        to startup + active was what made them blink for only a frame.

        Unknown/unlabelled states retain the exact live-flag fallback so helper
        states, assists, and idle do not fabricate persistent hitboxes.
        """
        fd = lookup_frame_data(self.fd_by_slot, slot_name, state_id)
        action_frame = read_action_frame(slot_base)

        # A profiled action is the same condition the move-label layer uses.
        # Keep the box visible for as long as that action remains current,
        # regardless of whether action-counter data is temporarily unavailable.
        if fd is not None and fd.active_windows:
            is_active = is_frame_data_active(fd, action_frame)
            return True, is_active, action_frame, fd

        # No labelled action: retain the old exact live-box signal.
        is_active = (hitbox_flag == 0x53)
        return is_active, is_active, action_frame, fd

    def update(self, dt: float, control=None) -> None:
        hitboxes_on = self._hitboxes_enabled(control)
        hurtboxes_on = self._hurtboxes_enabled(control)
        set_legend_window_visible(hitboxes_on or hurtboxes_on)
        if not (hitboxes_on or hurtboxes_on):
            self._clear_runtime_hitbox_state()
            return

        char_changed = False
        for name, base in SLOT_BASES.items():
            cid = rd32(base + OFF_CHAR_ID) or 0
            if self.last_char_ids.get(name) != cid:
                print(f"[CharChange] {name} char_id {self.last_char_ids.get(name)} -> {cid}")
                self.last_char_ids[name] = cid
                char_changed = True

        try:
            _camx, _camy, _camz, _camw = read_camera_pos(CAMERA)
            camera_mode_key = "giant" if float(_camz or 0.0) >= 10.0 else "normal"
        except Exception:
            camera_mode_key = self._last_camera_mode_key or "normal"

        if self._last_camera_mode_key != camera_mode_key:
            print(f"[CameraMode] {self._last_camera_mode_key} -> {camera_mode_key}")
            self._last_camera_mode_key = camera_mode_key
            char_changed = True

        if char_changed:
            self._clear_runtime_hitbox_state()

        if char_changed or not self._fd_scan_done:
            self._refresh_frame_data_cache()

        # Projectiles move fast; do not throttle this on pygame tick parity.
        # The old parity gate made projectile overlays appear to update at a
        # lower frame rate than the actual projectile.
        pools = resolve_projectile_pools() or PROJECTILE_POOLS
        node_idx = 0
        for pool in pools:
            for i in range(PROJECTILE_NODE_COUNT):
                node_addr = pool + i * PROJECTILE_NODE_STRIDE
                self.node_tracker.update_from_node(node_idx, node_addr)
                node_idx += 1

        slot_filter = _read_slot_filter()
        counts: Dict[str, int] = {}

        if hitboxes_on:
            for name, base in SLOT_BASES.items():
                try:
                    if not slot_filter.get(name, True):
                        counts[name] = 0
                        continue

                    raw_state = read_state_raw(base)
                    state_id = decode_state_id(raw_state)
                    if slot_passive_override(name, state_id):
                        continue

                    boxes = read_hitboxes(base, HITBOX)
                    active = 0
                    for i, (x, y, r, flag) in enumerate(boxes):
                        visible = self.motion_filter.update(name, i, x, y, r)
                        if visible and r > 0.001:
                            active += 1
                    counts[name] = active

                except Exception as slot_exc:
                    print(f"[SlotError] {name}: {slot_exc!r}")

            # Read projectile actors every overlay update so the drawn volume
            # tracks fast projectiles frame-for-frame.
            self.cached_projectiles = read_projectile_hitboxes()
        else:
            self.cached_projectiles = []
            self.motion_filter.cleanup()

        now_ms = pygame.time.get_ticks()

        # Cache skeletal hurtboxes in update(), not draw().  The same tiny
        # geometry pass also tells us whether a partner slot is actually on-stage
        # so stale standby hitboxes can be culled even when hurtbox display is off.
        if (hitboxes_on or hurtboxes_on) and now_ms - self._last_hurt_update_ms >= 16:
            hurt_filter = _read_hurtbox_filter()
            hurt_counts: Dict[str, int] = {}
            sample_bits = []
            cached: Dict[str, List[HurtboxState]] = {}
            renderable: Dict[str, bool] = {}
            for hurt_slot, hurt_base in SLOT_BASES.items():
                raw_hlist = read_hurtboxes(hurt_slot, hurt_base)
                live_rig = _slot_has_coherent_hurt_rig(hurt_base, raw_hlist)
                renderable[hurt_slot] = live_rig
                if not hurt_filter.get(hurt_slot, True) or not live_rig:
                    cached[hurt_slot] = []
                    hurt_counts[hurt_slot] = 0
                    sample_bits.append(f"{hurt_slot}[0]={'STANDBY' if raw_hlist and not live_rig else 'OFF'}")
                    continue
                cached[hurt_slot] = raw_hlist if hurtboxes_on else []
                hurt_counts[hurt_slot] = len(raw_hlist) if hurtboxes_on else 0
                if raw_hlist:
                    h0 = raw_hlist[0]
                    sample_bits.append(f"{hurt_slot}[0]={h0.x:.2f},{h0.y:.2f} r={h0.radius:.2f}")
                else:
                    sample_bits.append(f"{hurt_slot}[0]=NONE")
            self.slot_renderable = renderable
            self.cached_hurtboxes = cached
            self.hurt_counts = hurt_counts
            globals()["_last_hurt_debug_text"] = " | ".join(sample_bits)
            self._last_hurt_update_ms = now_ms
        elif not hurtboxes_on:
            self.cached_hurtboxes = {}
            self.hurt_counts = {}
            self.cached_hit_contacts = []
            globals()["_last_hurt_debug_text"] = "hurt=off"

        # Never scan the broad resolver/contact pool during normal overlay play.
        #
        # That old path scanned 0x91970000..0x91978000 at 4-byte stride every
        # 50 ms once a fighter entered hitstun.  It performs thousands of live
        # Dolphin reads exactly on hit, which is why the overlay looked frozen
        # until both players returned to idle.  Hitboxes and skeletal hurtboxes
        # remain live without this speculative contact marker; only the optional
        # yellow CONTACT marker/focus mode is disabled.
        self.cached_hit_contacts = []

        if pygame.time.get_ticks() % 300 == 0:
            self.motion_filter.cleanup()

        self.last_counts = counts

    def draw(self, screen: pygame.Surface, control=None) -> None:
        hitboxes_on = self._hitboxes_enabled(control)
        hurtboxes_on = self._hurtboxes_enabled(control)
        if not (hitboxes_on or hurtboxes_on):
            return

        camx, camy, camz, camw = read_camera_pos(CAMERA)
        cam_cos, cam_sin, cam_translate_y, cam_depth_y, cam_depth_z, cam_depth_translate = read_camera_view_affine(CAMERA)

        ov = self.overlay
        ov.screen = screen
        ov.cam_x = camx
        ov.cam_y = camy
        ov.cam_z = camz
        ov.set_camera_view_affine(cam_cos, cam_sin, cam_translate_y, cam_depth_y, cam_depth_z, cam_depth_translate)
        ov.set_giant_x_anchor(_derive_giant_x_anchor() if abs(float(camz or 0.0)) >= GIANT_CAMERA_Z_THRESHOLD else None)
        ov.on_resize(screen.get_width(), screen.get_height())

        slot_filter = _read_slot_filter()
        hurt_filter = _read_hurtbox_filter()
        debug_labels = bool(getattr(control, "show_debug", False))

        contacts_by_target: Dict[str, List[HitContactState]] = {}
        focus_mode = False
        focus_sources = set()
        focus_targets = set()
        focus_points = []
        if self.cached_hit_contacts:
            focus_mode = True
            for contact in self.cached_hit_contacts:
                if hurt_filter.get(contact.target_slot, True):
                    contacts_by_target.setdefault(contact.target_slot, []).append(contact)
                    focus_sources.add(contact.source_slot)
                    focus_targets.add(contact.target_slot)
                    focus_points.append((contact.x, contact.y, contact.z, contact))

        # In collision focus mode, collapse the scene to only the relevant data.
        # This reduces both clutter and draw cost exactly when impact frames get dense.
        HURT_FOCUS_MARGIN = 1.05
        HIT_FOCUS_PAD = 2.4
        PROJ_FOCUS_PAD = 2.8

        slot_invuln: Dict[str, bool] = {}
        slot_invuln_text: Dict[str, str] = {}
        for _slot_name, _slot_base in SLOT_BASES.items():
            try:
                _raw_state = read_state_raw(_slot_base)
                _state_id = decode_state_id(_raw_state)
                _fd = lookup_frame_data(self.fd_by_slot, _slot_name, _state_id)
                _action_frame = read_action_frame(_slot_base)
                # Prefer the direct current-action signature.  It avoids any
                # cache/listing mismatch for special wrappers and guarantees the
                # visual changes on the exact live move that owns the timer.
                _live_frames = _read_live_invuln_frames(_slot_base)
                _fd_frames = int(_fd.invuln_frames or 0) if _fd is not None else 0
                _invuln_frames = _live_frames or _fd_frames
                slot_invuln[_slot_name] = bool(_invuln_frames and _action_frame is not None and 1 <= int(_action_frame) <= _invuln_frames)
                slot_invuln_text[_slot_name] = f"{_invuln_frames}f" if slot_invuln[_slot_name] else ""
            except Exception:
                slot_invuln[_slot_name] = False
                slot_invuln_text[_slot_name] = ""

        if hurtboxes_on:
            hurt_view_mode = _read_hurtbox_view_mode(control)
            for hurt_slot, hlist in self.cached_hurtboxes.items():
                if not hurt_filter.get(hurt_slot, True):
                    continue
                if not self.slot_renderable.get(hurt_slot, False):
                    continue
                if not _slot_root_is_on_screen(ov, SLOT_BASES[hurt_slot]):
                    continue

                if focus_mode and hurt_slot not in focus_targets:
                    continue

                contacts = contacts_by_target.get(hurt_slot, [])
                invuln_active = bool(slot_invuln.get(hurt_slot, False))
                layout = classify_hurtbox_regions(hlist)
                region_by_index = layout.get("region_by_index", {})
                major_indices = set(layout.get("major_indices", set()))
                label_by_index = dict(layout.get("label_by_index", {}))

                extra_indices = set()
                for hurt in hlist:
                    if any(hurtbox_contains_contact(hurt, contact) for contact in contacts):
                        extra_indices.add(hurt.index)
                if focus_mode:
                    for hurt in hlist:
                        for fx, fy, fz, _c in focus_points:
                            if math.hypot(hurt.x - fx, hurt.y - fy) <= (hurt.radius + HURT_FOCUS_MARGIN):
                                extra_indices.add(hurt.index)
                                break

                visible_indices = set(h.index for h in hlist)
                if hurt_view_mode == "clean":
                    visible_indices = set(major_indices) | set(extra_indices)

                first_label_drawn = False
                for hurt in hlist:
                    if hurt.index not in visible_indices:
                        continue
                    highlight = any(hurtbox_contains_contact(hurt, contact) for contact in contacts)
                    if focus_mode and not (highlight or (hurt.index in extra_indices)):
                        continue

                    region = region_by_index.get(hurt.index, "other")
                    base_color = HURT_REGION_COLORS.get(region, HURT_REGION_COLORS["other"])

                    if hurt_view_mode == "clean":
                        detail = True
                        show_label = (hurt.index in label_by_index)
                        label = label_by_index.get(hurt.index, hurt.label())
                        color = base_color
                    elif hurt_view_mode == "detailed":
                        detail = highlight or (hurt.index in major_indices) or (hurt.index in extra_indices)
                        show_label = (hurt.index in label_by_index and detail)
                        label = label_by_index.get(hurt.index, hurt.label())
                        color = base_color if detail else _soften_hurt_color(base_color, 0.52, 34)
                    else:
                        detail = True
                        show_label = True
                        short_region = region.split("_")[0].upper()
                        label = f"{short_region}[{hurt.index}]"
                        color = base_color

                    ov.draw_hurtbox(
                        hurt.x,
                        hurt.y,
                        hurt.z,
                        hurt.radius,
                        color,
                        label,
                        highlight=highlight,
                        detail=detail,
                        invuln=invuln_active and not highlight,
                        show_label=show_label,
                    )
                    if invuln_active and not first_label_drawn and ov.font_hud is not None:
                        try:
                            _sx, _sy, _d, _f = ov.world_to_screen(hurt.x, hurt.y, hurt.z)
                            _txt = ov.font_hud.render(f"INVUL {slot_invuln_text.get(hurt_slot, '')}", True, (105, 255, 145))
                            ov.screen.blit(_txt, (_sx + 12, _sy - 24))
                            first_label_drawn = True
                        except Exception:
                            pass

            for contact in self.cached_hit_contacts:
                if hurt_filter.get(contact.target_slot, True):
                    ov.draw_hit_contact(contact)

        if hitboxes_on:
            for name, base in SLOT_BASES.items():
                if not slot_filter.get(name, True):
                    continue
                if not self.slot_renderable.get(name, False):
                    continue
                if not _slot_root_is_on_screen(ov, base):
                    continue
                if focus_mode and name not in focus_sources:
                    continue

                raw_state = read_state_raw(base)
                state_id = decode_state_id(raw_state)
                if slot_passive_override(name, state_id):
                    continue

                boxes = read_hitboxes(base, HITBOX)
                palette = COLORS.get(name, [(255, 255, 255)])

                for i, (x, y, r, flag) in enumerate(boxes):
                    if r <= 0.001:
                        continue

                    base_color = palette[i % len(palette)]
                    draw_now, is_active, action_frame, fd = self._frame_gate_for_slot(name, base, state_id, flag)
                    if state_id in MEGACRASH_STATE_IDS and not is_active:
                        continue
                    # Show the assigned attack geometry throughout startup as a
                    # dim preview, then let the existing active pulse make the
                    # actual hit frames unmistakable.  Recovery/idle stay hidden.
                    if not draw_now:
                        continue

                    if focus_mode:
                        # Only active hitboxes near the actual impact matter in focus mode.
                        if not is_active:
                            continue
                        near_focus = False
                        for fx, fy, fz, _c in focus_points:
                            if math.hypot(x - fx, y - fy) <= (r + HIT_FOCUS_PAD):
                                near_focus = True
                                break
                        if not near_focus:
                            continue

                    draw_color = base_color if is_active else _dim_hitbox_color(base_color)
                    label = f"{name}[{i}]"
                    if debug_labels and fd is not None and action_frame is not None:
                        label = f"{name}[{i}] f={action_frame}/{fd.active_text()}"
                    ov.draw_hitbox(
                        x, y, 0, r, draw_color, label,
                        is_active=is_active,
                        invuln=bool(slot_invuln.get(name, False)),
                    )

            for proj in self.cached_projectiles:
                if not slot_filter.get(proj.owner_slot, True):
                    continue
                if focus_mode and proj.owner_slot not in focus_sources:
                    continue

                if focus_mode:
                    near_focus = False
                    for fx, fy, fz, _c in focus_points:
                        if math.hypot(proj.x - fx, (proj.y + PROJECTILE_Y_OFFSET) - fy) <= (proj.radius + PROJ_FOCUS_PAD):
                            near_focus = True
                            break
                    if not near_focus and not proj.contact_valid:
                        continue

                color = PROJ_COLORS.get(proj.owner_slot, COL_PROJ)
                ov.draw_projectile_hitbox(
                    proj.x,
                    proj.y + PROJECTILE_Y_OFFSET,
                    proj.z,
                    proj.radius,
                    color,
                    proj.label(debug_labels and not focus_mode),
                    sweep_x=proj.hit_start_x,
                    sweep_y=proj.hit_start_y + PROJECTILE_Y_OFFSET,
                    sweep_z=proj.hit_start_z,
                    contact_x=proj.impact_x,
                    contact_y=proj.impact_y + PROJECTILE_Y_OFFSET,
                    contact_z=proj.impact_z,
                    contact_valid=proj.contact_valid,
                    root_x=proj.root_x,
                    root_y=proj.root_y + PROJECTILE_Y_OFFSET,
                    root_z=proj.root_z,
                )

        ov.draw_hud(self.last_counts, self.motion_filter, self.node_tracker, self.hurt_counts if hurtboxes_on else {})

# ----------------------------
# Main
# ----------------------------
def _valid_projectile_actor_candidate(actor: int, require_link_or_table: bool = True, table_seen: Optional[set] = None) -> bool:
    actor = int(actor or 0) & 0xFFFFFFFF
    if not (0x91000000 <= actor <= 0x94000000):
        return False

    owner = rd32(actor + ACTOR_OFF_OWNER) or 0
    if _owner_slot_name(owner) is None:
        return False

    proj_id = (rd32(actor + ACTOR_OFF_PROJ_ID) or 0) & 0xFFFFFFFF
    if proj_id <= 0 or proj_id > 0xFFFF:
        return False

    root_x = _rf(actor + ACTOR_OFF_X)
    root_y = _rf(actor + ACTOR_OFF_Y)
    root_z = _rf(actor + ACTOR_OFF_Z)
    if not _sane_projectile_world_point(root_x, root_y, root_z):
        return False
    if not _nonzero_projectile_world_point(root_x, root_y, root_z):
        return False

    if not require_link_or_table:
        return True

    if table_seen is not None and actor in table_seen:
        return True

    linked_record = rd32(actor + ACTOR_OFF_LINKED_RECORD) or 0
    return 0x90000000 <= linked_record <= 0x94000000


def get_projectile_actors():
    """Return unique raw projectile actor pointers.

    ACTOR_TABLE is reliable for simple projectiles, but Morrigan-style missile
    showers can have many actor structs in the projectile actor pool while only
    exposing a small subset through ACTOR_TABLE.  We seed from ACTOR_TABLE, then
    scan the small stride-based actor pool and include candidates with valid
    owner/id/root and either a valid linked hit record or table presence.
    """
    actors: List[int] = []
    seen = set()
    table_seen = set()

    for i in range(ACTOR_MAX):
        ptr = rd32(ACTOR_TABLE + i * 4)
        if ptr is None:
            continue
        if not (0x91000000 <= ptr <= 0x94000000):
            continue
        if ptr in seen:
            continue
        seen.add(ptr)
        table_seen.add(ptr)
        actors.append(ptr)

    for pool_base in PROJECTILE_ACTOR_POOL_BASES:
        for i in range(PROJECTILE_ACTOR_POOL_COUNT):
            ptr = pool_base + i * PROJECTILE_ACTOR_POOL_STRIDE
            if ptr in seen:
                continue
            if not _valid_projectile_actor_candidate(ptr, require_link_or_table=True, table_seen=table_seen):
                continue
            seen.add(ptr)
            actors.append(ptr)

    return actors


def _owner_slot_name(owner: int) -> Optional[str]:
    owner = int(owner or 0) & 0xFFFFFFFF
    for slot_name, slot_base in SLOT_BASES.items():
        if owner == int(slot_base):
            return slot_name
    return None


def _sane_projectile_world_point(x: float, y: float, z: float) -> bool:
    if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
        return False
    return abs(x) < 30.0 and abs(y) < 30.0 and abs(z) < 30.0


def _nonzero_projectile_world_point(x: float, y: float, z: float) -> bool:
    return abs(x) + abs(y) + abs(z) > 0.001


def _projectile_anchor_distance(a_x: float, a_y: float, b_x: float, b_y: float) -> float:
    dx = float(a_x) - float(b_x)
    dy = float(a_y) - float(b_y)
    return math.sqrt(dx * dx + dy * dy)


def _valid_slot_ptr(ptr: int) -> bool:
    ptr = int(ptr or 0) & 0xFFFFFFFF
    return ptr in set(int(v) for v in SLOT_BASES.values())


def _safe_radius_from_scale(scale_value: float, fallback: float = PROJECTILE_FALLBACK_RADIUS) -> float:
    if math.isfinite(scale_value) and PROJECTILE_SCALE_RADIUS_MIN <= scale_value <= PROJECTILE_SCALE_RADIUS_MAX:
        return float(scale_value)
    return float(fallback)


def _normalize_2d(dx: float, dy: float) -> Tuple[float, float]:
    if not (math.isfinite(dx) and math.isfinite(dy)):
        return 0.0, 0.0
    mag = math.sqrt(dx * dx + dy * dy)
    if mag < 0.05 or mag > 4.0:
        return 0.0, 0.0
    return dx / mag, dy / mag


def _is_large_projectile_field(proj_id: int, linked_record: int) -> bool:
    if proj_id != 0x1:
        return False
    if not (0x90000000 <= int(linked_record or 0) <= 0x94000000):
        return False
    card0 = rd32(linked_record + 0x80) or 0
    card1 = rd32(linked_record + 0x84) or 0
    return (card0, card1) in PROJECTILE_LARGE_FIELD_CARDS


def _projectile_direction(actor: int, linked_record: int, proj_id: int, root_x: float, root_y: float, prev_x: float, prev_y: float) -> Tuple[float, float, float]:
    # Morrigan Finishing Shower / id 0x163 showed actor +0x108/+0x10C as
    # local/up, not forward. Doronjo/Odronjo's large field id 0x1 does the
    # same, so use per-frame root motion first for those cases.
    if proj_id in PROJECTILE_MOTION_DIR_FIRST_IDS or _is_large_projectile_field(proj_id, linked_record):
        ndx, ndy = _normalize_2d(root_x - prev_x, root_y - prev_y)
        if ndx or ndy:
            return ndx, ndy, 0.0

    # Casshan FLAG_309: actor +0x108/+0x10C and linked +0xAC/+0xB0 both
    # hold the useful forward vector.  actor +0x104 is Z-ish/unused for 2D.
    dx = _rf(actor + ACTOR_OFF_DIR_X)
    dy = _rf(actor + ACTOR_OFF_DIR_Y)
    ndx, ndy = _normalize_2d(dx, dy)
    if ndx or ndy:
        return ndx, ndy, _rf(actor + ACTOR_OFF_DIR_Z)

    if linked_record and (0x90000000 <= linked_record <= 0x94000000):
        dx = _rf(linked_record + LINKED_OFF_DIR_X)
        dy = _rf(linked_record + LINKED_OFF_DIR_Y)
        ndx, ndy = _normalize_2d(dx, dy)
        if ndx or ndy:
            return ndx, ndy, 0.0

    # Last fallback: motion vector from previous point to current root.
    ndx, ndy = _normalize_2d(root_x - prev_x, root_y - prev_y)
    return ndx, ndy, 0.0


def _projectile_extent(proj_id: int, radius: float) -> float:
    if proj_id in PROJECTILE_EXTENT_BY_ID:
        return float(PROJECTILE_EXTENT_BY_ID[proj_id])
    # Conservative generic guess: one diameter ahead of the root, clamped.
    return max(PROJECTILE_EXTENT_MIN, min(PROJECTILE_EXTENT_MAX, float(radius) * 2.0))


def _read_projectile_actor(actor: int) -> Optional[ProjectileActorState]:
    owner = rd32(actor + ACTOR_OFF_OWNER) or 0
    owner_slot = _owner_slot_name(owner)
    if owner_slot is None:
        return None

    proj_id = rd32(actor + ACTOR_OFF_PROJ_ID) or 0
    proj_id &= 0xFFFFFFFF
    if proj_id <= 0 or proj_id > 0xFFFF:
        return None

    root_x = _rf(actor + ACTOR_OFF_X)
    root_y = _rf(actor + ACTOR_OFF_Y)
    root_z = _rf(actor + ACTOR_OFF_Z)
    if not _sane_projectile_world_point(root_x, root_y, root_z):
        return None

    prev_x = _rf(actor + ACTOR_OFF_PREV_X)
    prev_y = _rf(actor + ACTOR_OFF_PREV_Y)
    prev_z = _rf(actor + ACTOR_OFF_PREV_Z)
    if not _sane_projectile_world_point(prev_x, prev_y, prev_z):
        prev_x, prev_y, prev_z = root_x, root_y, root_z

    linked_record = rd32(actor + ACTOR_OFF_LINKED_RECORD) or 0
    target_ptr = 0
    if linked_record and (0x90000000 <= linked_record <= 0x94000000):
        target_ptr = rd32(linked_record + LINKED_OFF_TARGET) or 0

    impact_x = _rf(actor + ACTOR_OFF_IMPACT_X)
    impact_y = _rf(actor + ACTOR_OFF_IMPACT_Y)
    impact_z = _rf(actor + ACTOR_OFF_IMPACT_Z)
    contact_valid = (
        _valid_slot_ptr(target_ptr)
        and _sane_projectile_world_point(impact_x, impact_y, impact_z)
        and _nonzero_projectile_world_point(impact_x, impact_y, impact_z)
    )

    # The candidate collision shape is a short local capsule projected forward
    # from the actor root.  This is a probe for the actual damaging volume, not
    # a proven final answer yet.  Contact/result is drawn separately and never
    # used as the live projectile anchor.
    large_field = _is_large_projectile_field(proj_id, linked_record)
    if large_field:
        radius = PROJECTILE_LARGE_FIELD_RADIUS
        extent = PROJECTILE_LARGE_FIELD_EXTENT
    else:
        scale_radius = _safe_radius_from_scale(_rf(actor + ACTOR_OFF_SCALE_CANDIDATE))
        radius = float(PROJECTILE_RADIUS_BY_ID.get(proj_id, scale_radius))
        extent = _projectile_extent(proj_id, radius)

    dir_x, dir_y, dir_z = _projectile_direction(actor, linked_record, proj_id, root_x, root_y, prev_x, prev_y)
    if not (dir_x or dir_y):
        # If direction is unknown, keep the probe centered on the root rather
        # than drawing misleading geometry.
        hit_start_x, hit_start_y, hit_start_z = root_x, root_y, root_z
        hit_end_x, hit_end_y, hit_end_z = root_x, root_y, root_z
        x, y, z = root_x, root_y, root_z
        anchor_source = "root"
    else:
        hit_start_x, hit_start_y, hit_start_z = root_x, root_y, root_z
        hit_end_x = root_x + dir_x * extent
        hit_end_y = root_y + dir_y * extent
        hit_end_z = root_z
        x = root_x + dir_x * (extent * 0.5)
        y = root_y + dir_y * (extent * 0.5)
        z = root_z
        anchor_source = "field" if large_field else "cand"

    # Keep these fields populated for debug/backward compatibility, but do not
    # draw actor +0xE0/+0xE4 as a sweep. That field caused bogus half-screen
    # bars in live tests.
    sweep_x = hit_start_x
    sweep_y = hit_start_y
    sweep_z = hit_start_z

    return ProjectileActorState(
        actor=actor,
        owner=owner,
        owner_slot=owner_slot,
        proj_id=proj_id,
        x=x,
        y=y,
        z=z,
        prev_x=prev_x,
        prev_y=prev_y,
        prev_z=prev_z,
        sweep_x=sweep_x,
        sweep_y=sweep_y,
        sweep_z=sweep_z,
        radius=radius,
        linked_record=linked_record,
        anchor_source=anchor_source,
        root_x=root_x,
        root_y=root_y,
        root_z=root_z,
        impact_x=impact_x,
        impact_y=impact_y,
        impact_z=impact_z,
        contact_valid=contact_valid,
        target_ptr=target_ptr,
        dir_x=dir_x,
        dir_y=dir_y,
        dir_z=dir_z,
        hit_start_x=hit_start_x,
        hit_start_y=hit_start_y,
        hit_start_z=hit_start_z,
        hit_end_x=hit_end_x,
        hit_end_y=hit_end_y,
        hit_end_z=hit_end_z,
        extent=extent,
    )

def read_projectile_hitboxes(slot_filter: Optional[Dict[str, bool]] = None) -> List[ProjectileActorState]:
    """Read live projectile actor hitboxes from ACTOR_TABLE.

    This replaces the old synthetic projectile position reader.  Normal slot
    hitboxes/hurtboxes still come from read_hitboxes() and are not touched here.

    Multi-projectile moves are handled by unique actor pointer, not by owner/id.
    Example: owner P1/id 0x160 can appear as two different actor pointers in
    the same frame, and both should draw even though they share the same id.
    """
    result: List[ProjectileActorState] = []

    for actor in get_projectile_actors():
        proj = _read_projectile_actor(actor)
        if proj is None:
            continue
        if slot_filter is not None and not slot_filter.get(proj.owner_slot, True):
            continue
        result.append(proj)

    return result


def read_projectile_positions():
    """Compatibility shim for old callers: return current points only."""
    return [(p.x, p.y, p.z) for p in read_projectile_hitboxes()]

def debug_dump_pools():

    print("\n--- projectile pool dump ---")

    for pool in PROJECTILE_POOLS:

        print(f"\nPOOL 0x{pool:08X}")

        for i in range(PROJECTILE_NODE_COUNT):

            addr = pool + i * PROJECTILE_NODE_STRIDE

            x = _rf(addr + PROJ_OFF_X)
            y = _rf(addr + PROJ_OFF_Y)
            z = _rf(addr + PROJ_OFF_Z)

            d0 = _clean_dim(_rf(addr + PROJ_OFF_DIM_0))
            d1 = _clean_dim(_rf(addr + PROJ_OFF_DIM_1))
            d2 = _clean_dim(_rf(addr + PROJ_OFF_DIM_2))

            if abs(x) > 0.001 or abs(y) > 0.001 or abs(z) > 0.001:
                print(
                    f"node {i:02d} "
                    f"x={x:7.3f} y={y:7.3f} z={z:7.3f} "
                    f"d0={d0:.3f} d1={d1:.3f} d2={d2:.3f}"
                )

def resolve_projectile_pools():

    manager = 0x80476E50
    pools = []

    for i in range(16):

        ptr = rd32(manager + i * 4)
        if ptr is None:
            continue

        if 0x91000000 <= ptr <= 0x94000000:

            node_base = ptr - 0x6C
            pools.append(node_base)

    return pools
def has_valid_state_id(raw_state: int, state_id: int) -> bool:
    if raw_state == 0:
        return False
    if state_id <= 0:
        return False
    return True
            
def main():
    hook()
    dolphin_hwnd = find_dolphin_hwnd()
    if not dolphin_hwnd:
        print("Dolphin not found.")
        return

    overlay = Overlay(DISPLAY)
    overlay_hwnd = overlay.init_pygame()
    win32gui.SetWindowLong(overlay_hwnd, win32con.GWL_HWNDPARENT, dolphin_hwnd)
    clock = pygame.time.Clock()

    motion_filter = MotionFilter()

    scanner = ProjectileScanner()
    print("Projectile node watcher active (signature scan disabled).")
    print("  F3 = dump active node offset table to console")
    print("  F2 = (no-op, scan disabled)")

    total_nodes = len(PROJECTILE_POOLS) * PROJECTILE_NODE_COUNT
    node_tracker = ProjectileNodeTracker(total_nodes)

    _last_char_ids: Dict[str, int] = {}
    fd_by_slot: Dict[str, Dict[int, MoveFrameData]] = build_frame_data_cache()

    running = True
    while running:
        try:
            w, h = sync_overlay_to_dolphin(dolphin_hwnd, overlay_hwnd)
            overlay.on_resize(w, h)

            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                # Keyboard hotkeys intentionally disabled; use the main GUI controls.

            char_changed = False
            for name, base in SLOT_BASES.items():
                cid = rd32(base + OFF_CHAR_ID) or 0
                if _last_char_ids.get(name) != cid:
                    print(f"[CharChange] {name} char_id {_last_char_ids.get(name)} -> {cid}")
                    _last_char_ids[name] = cid
                    char_changed = True

            if char_changed:
                fd_by_slot = build_frame_data_cache()

            camx, camy, camz, camw = read_camera_pos(CAMERA)
            cam_cos, cam_sin, cam_translate_y, cam_depth_y, cam_depth_z, cam_depth_translate = read_camera_view_affine(CAMERA)
            if USE_LIVE_CAMERA:
                overlay.cam_x = camx
                overlay.cam_y = camy
                overlay.cam_z = camz
                overlay.set_camera_view_affine(cam_cos, cam_sin, cam_translate_y, cam_depth_y, cam_depth_z, cam_depth_translate)
                overlay.set_giant_x_anchor(_derive_giant_x_anchor() if abs(float(camz or 0.0)) >= GIANT_CAMERA_Z_THRESHOLD else None)

            slot_filter = _read_slot_filter()

            if pygame.time.get_ticks() % 2 == 0:
                update_projectile_nodes(node_tracker)

            overlay.clear()
            overlay.draw_debug_axes()

            counts = {}

            for name, base in SLOT_BASES.items():
                try:
                    if not slot_filter.get(name, True):
                        counts[name] = 0
                        continue

                
                    raw_state = read_state_raw(base)
                    state_id = decode_state_id(raw_state)
                    char_id = rd32(base + OFF_CHAR_ID) or 0

                    if _last_state_raws.get(name) != raw_state:
                        dump_state18(name, base)
                        print(
                            f"[StateChange] {name} char_id={char_id} "
                            f"raw=0x{raw_state:08X} "
                            f"hi16=0x{(raw_state >> 16) & 0xFFFF:04X} ({(raw_state >> 16) & 0xFFFF}) "
                            f"low16=0x{raw_state & 0xFFFF:04X} ({raw_state & 0xFFFF}) "
                            f"low8=0x{raw_state & 0xFF:02X} ({raw_state & 0xFF}) "
                            f"decoded={state_id} passive={is_passive_state(state_id)}"
                        )

                        _last_state_raws[name] = raw_state
                        _last_state_ids[name] = state_id
                    boxes = read_hitboxes(base, HITBOX)
                    active = 0
                    palette = COLORS.get(name, [(255, 255, 255)])

                    if slot_passive_override(name, state_id):
                        counts[name] = 0
                        continue

                    for i, (x, y, r, flag) in enumerate(boxes):
                        visible = motion_filter.update(name, i, x, y, r)

                        fd = lookup_frame_data(fd_by_slot, name, state_id)
                        # When frame data is available, keep drawing the dim startup
                        # hitboxes instead of letting the stillness filter hide them.
                        if not visible and fd is None:
                            continue

                        if r > 0.001:
                            active += 1
                            base_color = palette[i % len(palette)]
                            action_frame = read_action_frame(base)
                            is_active = is_frame_data_active(fd, action_frame) if fd is not None else (flag == 0x53)
                            draw_color = base_color if is_active else _dim_hitbox_color(base_color)
                            overlay.draw_hitbox(
                                x,
                                y,
                                0,
                                r,
                                draw_color,
                                f"{name}[{i}]",
                                is_active=is_active,
                            )

                    counts[name] = active

                except Exception as slot_exc:
                    print(
                        f"[SlotError] slot={name} base=0x{base:08X} "
                        f"char_id={rd32(base + OFF_CHAR_ID) or 0} "
                        f"state_id={read_state_id(base)} "
                        f"err={slot_exc!r}"
                    )
                    pause_on_error(f"SlotError:{name}", slot_exc)
                    running = False
                    break
                
            # Keep standalone rendering responsive during hits as well.  The
            # broad resolver-pool contact scan is intentionally disabled here;
            # it caused thousands of Dolphin reads per hit frame.
            contacts_by_target: Dict[str, List[HitContactState]] = {}

            hurt_counts: Dict[str, int] = {}
            for hurt_slot, hurt_base in SLOT_BASES.items():
                hurt_counts[hurt_slot] = read_and_draw_hurtboxes_for_slot(
                    overlay,
                    hurt_slot,
                    hurt_base,
                    contacts_by_target.get(hurt_slot, []),
                )

            if any(slot_filter.values()):
                projectiles = read_projectile_hitboxes(slot_filter)
                for proj in projectiles:
                    color = PROJ_COLORS.get(proj.owner_slot, COL_PROJ)
                    overlay.draw_projectile_hitbox(
                        proj.x,
                        proj.y + PROJECTILE_Y_OFFSET,
                        proj.z,
                        proj.radius,
                        color,
                        proj.label(False),
                        sweep_x=proj.hit_start_x,
                        sweep_y=proj.hit_start_y + PROJECTILE_Y_OFFSET,
                        sweep_z=proj.hit_start_z,
                        contact_x=proj.impact_x,
                        contact_y=proj.impact_y + PROJECTILE_Y_OFFSET,
                        contact_z=proj.impact_z,
                        contact_valid=proj.contact_valid,
                        root_x=proj.root_x,
                        root_y=proj.root_y + PROJECTILE_Y_OFFSET,
                        root_z=proj.root_z,
                    )

            # Late hurtbox pass: draw this layer after normal boxes/projectiles so
            # it cannot be hidden underneath the existing overlay primitives.
            # Recompute counts here so the HUD reflects the late pass too.
            hurt_counts = {}
            for hurt_slot, hurt_base in SLOT_BASES.items():
                hurt_counts[hurt_slot] = read_and_draw_hurtboxes_for_slot(
                    overlay,
                    hurt_slot,
                    hurt_base,
                    contacts_by_target.get(hurt_slot, []),
                )

            try:
                sample_bits = []
                for _hs, _hb in SLOT_BASES.items():
                    _hlist = read_hurtboxes(_hs, _hb)
                    if _hlist:
                        _h0 = _hlist[0]
                        sample_bits.append(f"{_hs}[0]={_h0.x:.2f},{_h0.y:.2f} r={_h0.radius:.2f}")
                    else:
                        sample_bits.append(f"{_hs}[0]=NONE")
                globals()["_last_hurt_debug_text"] = " | ".join(sample_bits)
            except Exception as _e:
                globals()["_last_hurt_debug_text"] = f"hurt debug err={_e!r}"

            if pygame.time.get_ticks() % 300 == 0:
                motion_filter.cleanup()

            overlay.draw_hud(counts, motion_filter, node_tracker, hurt_counts)
            overlay.present()
            clock.tick(DISPLAY.fps)

        except Exception as exc:
            pause_on_error("MainLoopCrash", exc)
            running = False

    pygame.quit()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        pause_on_error("FatalCrash", exc)
        raise