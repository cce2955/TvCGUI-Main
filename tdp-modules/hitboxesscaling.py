#!/usr/bin/env python3

from __future__ import annotations

import ctypes
import math
import struct
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import os
import pygame
import win32con
import win32gui

from dolphin_io import hook, rd32, rbytes

import json as _json

# ----------------------------
# World / display constants
# ----------------------------

WORLD_Y_OFFSET             = -0.7
PROJECTILE_Y_OFFSET: float = 0
PROJECTILE_DESPAWN_FRAMES: int = 6
HITBOX_FILTER_FILE         = "hitbox_filter.json"
_last_filter_mtime         = 0.0
_slot_filter               = {"P1": True, "P2": True, "P3": True, "P4": True}

MOTION_THRESHOLD: float    = 0.003
STILL_FRAME_LIMIT: int     = 4
MOTION_FRAME_REQUIRED: int = 2
ACTOR_TABLE                = 0x80476E50
ACTOR_MAX                  = 16

ACTOR_OFF_X = 0x5C
ACTOR_OFF_Y = 0x6C
ACTOR_OFF_Z = 0x7C

HITBOX_MAX_RENDER_RADIUS: float = 4.0

CACHE_MANIFEST_FILE = "hitbox_surface_manifest.json"
_seen_surface_keys: set = set()

# ----------------------------
# Move classification
# ----------------------------
# If mv_label contains ANY of these words, the character is considered
# idle/non-attacking and NO hitboxes are drawn at all.
IDLE_KEYWORDS = (
    "idle", "stand", "walk", "crouch", "jump", "landing", "land",
    "fall", "block", "guard", "pushblock", "rising", "crouching",
    "forward", "backward", "hitstun", "hurt", "tech", "throw tech",
    "recovery", "wake", "taunt", "intro", "win", "lose",
    "passive", "aura", "field", "stance",
)
SUPPRESSED_MOVE_IDS = {
    1, 2, 3, 6, 7, 8, 9, 10, 11, 13, 14, 18, 19, 20, 21, 22, 24, 25,
    28, 29, 30, 31, 32, 33, 35, 36, 40, 41, 42, 46,
    48, 49, 50, 52, 53, 55, 56, 57, 58, 59, 60, 61, 62, 64, 65, 66, 67,
    69, 70, 73, 74, 75, 77, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89,
    90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100, 101, 102, 103, 104,
    105, 106, 108, 109, 113, 115, 116, 119, 124, 126, 128, 129, 130,
    132, 133, 142, 154, 155, 158, 159, 160, 161, 162, 163, 164, 165,
    166, 167,
    416, 417, 418, 420, 421, 423, 424, 425, 426, 427, 428, 430, 432, 433,
    444, 445, 446, 447, 448, 449, 465, 468, 471, 513, 514, 575,
    4562, 4565, 4571,
    4609, 4610, 4611, 4613, 4614, 4615, 4616, 4617, 4618, 4619, 4620,
    4621, 4622, 4623,
    4294967295,
}
# ----------------------------
# HUD data (frame-data feed from main.py)
# ----------------------------

HUD_DATA_FILE    = "hud_overlay_data.json"
_hud_data: Dict[str, dict] = {}
_last_hud_mtime  = 0.0
_slot_move_state: Dict[str, dict] = {}

# active_start / active_end from scan_normals are compared 1:1 against
# our internal per-move frame counter.  Adjust if timings feel off by 2x.
OVERLAY_FPS_DIVISOR = 1


def _read_hud_data() -> Dict[str, dict]:
    global _last_hud_mtime, _hud_data
    try:
        mt = os.path.getmtime(HUD_DATA_FILE)
        if mt != _last_hud_mtime:
            _last_hud_mtime = mt
            with open(HUD_DATA_FILE) as f:
                _hud_data = _json.load(f)
    except Exception:
        pass
    return _hud_data


def _update_move_state(slot_label: str, hud_data: dict) -> dict:
    """
    Track per-slot move phase.

    Returns a state dict with:
        move_id, frame, game_frame,
        phase: "startup" | "active" | "recovery" | "fallback"
        has_data, active_start, active_end
    """
    slot_info    = hud_data.get(slot_label, {})
    cur_id       = slot_info.get("mv_id_display")
    active_start = slot_info.get("active_start")
    active_end   = slot_info.get("active_end")
    has_data     = (active_start is not None and active_end is not None)

    state = _slot_move_state.get(slot_label)

    if state is None:
        state = {
            "move_id":      cur_id,
            "frame":        0,
            "game_frame":   0,
            "phase":        "startup" if cur_id is not None else "fallback",
            "has_data":     has_data,
            "active_start": active_start,
            "active_end":   active_end,
        }
    else:
        prev_phase   = state.get("phase", "fallback")
        prev_move_id = state.get("move_id")

        # Reset counter on new move, or when the same move fires again after recovery
        same_restarted = (
            cur_id is not None
            and cur_id == prev_move_id
            and prev_phase == "recovery"
        )

        if cur_id != prev_move_id or same_restarted:
            state["move_id"]      = cur_id
            state["frame"]        = 0
            state["game_frame"]   = 0
            state["phase"]        = "startup" if cur_id is not None else "fallback"
            state["has_data"]     = has_data
            state["active_start"] = active_start
            state["active_end"]   = active_end
        else:
            state["frame"]     += 1
            state["game_frame"] = state["frame"] // OVERLAY_FPS_DIVISOR
            if has_data:
                state["has_data"]     = True
                state["active_start"] = active_start
                state["active_end"]   = active_end

    # Determine phase from frame position
    if state.get("has_data") and state.get("active_start") is not None:
        gf  = state["game_frame"]
        as_ = state["active_start"]
        ae  = state["active_end"]
        if gf < as_:
            state["phase"] = "startup"
        elif gf <= ae:
            state["phase"] = "active"
        else:
            state["phase"] = "recovery"
    else:
        # No frame data — show hitbox whenever memory has a valid radius
        state["phase"] = "fallback"

    _slot_move_state[slot_label] = state
    return state


# ----------------------------
# Projectile pools
# ----------------------------

PROJECTILE_POOLS = [
    0x91B15900, 0x91B15A10, 0x91B15B50,
    0x91B15C90, 0x91B15DD0, 0x91B15F10,
]
PROJECTILE_NODE_STRIDE  = 0x30
PROJECTILE_NODE_COUNT   = 16

PROJ_OFF_X:    int = 0x00
PROJ_OFF_Y:    int = 0x10
PROJ_OFF_Z:    int = 0x20
PROJ_OFF_DIM_0: int = 0x08
PROJ_OFF_DIM_1: int = 0x18
PROJ_OFF_DIM_2: int = 0x28

PROJ_PTR_CANDIDATES = (0x04, 0x0C, 0x14, 0x1C, 0x24, 0x2C)

PROJ_HB_OFFSETS = [
    (0x064, 0x06C),
    (0x094, 0x09C),
    (0x0C4, 0x0CC),
]

OFF_CHAR_ID = 0x14


# ----------------------------
# Slot filter
# ----------------------------

def _read_slot_filter() -> dict:
    global _last_filter_mtime, _slot_filter
    try:
        mt = os.path.getmtime(HITBOX_FILTER_FILE)
        if mt != _last_filter_mtime:
            _last_filter_mtime = mt
            with open(HITBOX_FILTER_FILE) as f:
                _slot_filter = _json.load(f)
    except Exception:
        pass
    return _slot_filter


# ----------------------------
# DPI
# ----------------------------

def set_dpi_aware() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


set_dpi_aware()


# ----------------------------
# Data classes
# ----------------------------

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


@dataclass(frozen=True)
class HitboxStyle:
    fill_alpha: int
    edge_alpha: int
    edge_width: int
    outer_alpha: int
    outer_width: int
    inner_alpha: int
    inner_width: int
    cross_alpha: int
    cross_size_div: int
    cross_min: int
    cross_max: int
    pad: int
    filled: bool


# ----------------------------
# Hitbox styles
# ----------------------------

STYLE_ACTIVE = HitboxStyle(
    fill_alpha=110, edge_alpha=220, edge_width=2,
    outer_alpha=55,  outer_width=4,
    inner_alpha=150, inner_width=1,
    cross_alpha=190, cross_size_div=3, cross_min=4, cross_max=9,
    pad=6, filled=True,
)

STYLE_INACTIVE = HitboxStyle(
    fill_alpha=55,  edge_alpha=220, edge_width=2,
    outer_alpha=0,  outer_width=0,
    inner_alpha=0,  inner_width=0,
    cross_alpha=0,  cross_size_div=3, cross_min=4, cross_max=9,
    pad=6, filled=True,
)

STYLE_PROJECTILE = HitboxStyle(
    fill_alpha=45,  edge_alpha=190, edge_width=1,
    outer_alpha=55, outer_width=2,
    inner_alpha=95, inner_width=1,
    cross_alpha=200, cross_size_div=3, cross_min=3, cross_max=7,
    pad=8, filled=True,
)


# ----------------------------
# Addresses
# ----------------------------

SLOT_BASES: Dict[str, int] = {
    "P1": 0x9246B9C0,
    "P2": 0x92B6BA00,
    "P3": 0x927EB9E0,
    "P4": 0x92EEBA20,
}

SLOT_TO_HUD_KEY: Dict[str, str] = {
    "P1": "P1-C1",
    "P2": "P2-C1",
    "P3": "P1-C2",
    "P4": "P2-C2",
}

HITBOX = HitboxLayout(
    struct_shift=0x4C0,
    blocks=(0x64, 0xA4, 0xE4),
    off_x=0x00, off_y=0x04, off_r=0x18, off_flag=0xC3,
)

CAMERA = CameraLayout(
    base=0x8053CB20,
    off_x=0x00, off_y=0x04, off_z=0x08, off_w=0x0C,
)

DISPLAY = DisplayConfig(
    baseline_w=1280, baseline_h=720,
    baseline_ppu=160.0, zoom=1.0,
    center_y_offset_px=0, max_radius_units=8.0,
    fps=30, show_debug_axes=False,
)

USE_LIVE_CAMERA = True

# ----------------------------
# Colors
# ----------------------------

COLORS: Dict[str, List[Tuple[int, int, int]]] = {
    "P1": [(255, 60, 60),   (255, 140, 0),  (255, 220, 0)],
    "P2": [(180, 60, 255),  (60, 200, 255), (60, 255, 180)],
    "P3": [(255, 80, 180),  (255, 0, 120),  (255, 120, 200)],
    "P4": [(80, 255, 120),  (0, 255, 80),   (120, 255, 200)],
}

COL_DIM   = (120, 120, 120)
COL_BG    = (0, 0, 0)
COL_DEBUG = (0, 255, 0)
COL_PROJ  = (100, 220, 255)

SHOW_HITBOX_LABELS     = False
SHOW_PROJECTILE_LABELS = False


# ----------------------------
# Surface cache
# ----------------------------

_surface_cache: Dict[Tuple[int, Tuple[int, int, int], HitboxStyle], pygame.Surface] = {}


def _clamp_rgb(color: Tuple) -> Tuple[int, int, int]:
    return (max(0, min(255, int(color[0]))),
            max(0, min(255, int(color[1]))),
            max(0, min(255, int(color[2]))))


def _quantize_radius(rpx: int) -> int:
    return max(2, min(160, int(round(rpx / 4.0)) * 4))


def _style_name(style: HitboxStyle) -> str:
    if style == STYLE_ACTIVE:     return "active"
    if style == STYLE_INACTIVE:   return "inactive"
    if style == STYLE_PROJECTILE: return "projectile"
    return "unknown"


def _style_from_name(name: str) -> HitboxStyle:
    if name == "active":     return STYLE_ACTIVE
    if name == "inactive":   return STYLE_INACTIVE
    if name == "projectile": return STYLE_PROJECTILE
    raise ValueError(f"Unknown style name: {name}")


def _record_surface_key(rpx: int, color: Tuple, style: HitboxStyle) -> None:
    _seen_surface_keys.add((_quantize_radius(rpx), _clamp_rgb(color), _style_name(style)))


def _make_hitbox_surface(rpx: int, color: Tuple,
                         style: HitboxStyle) -> pygame.Surface:
    rpx   = _quantize_radius(rpx)
    color = _clamp_rgb(color)
    pad   = style.pad
    surf  = pygame.Surface((rpx * 2 + pad * 2, rpx * 2 + pad * 2), pygame.SRCALPHA)
    cx = cy = rpx + pad
    r_c, g_c, b_c = color

    if style.outer_alpha > 0 and style.outer_width > 0:
        pygame.draw.circle(surf, (r_c, g_c, b_c, style.outer_alpha),
                           (cx, cy), rpx + 4, style.outer_width)
    if style.filled and style.fill_alpha > 0:
        pygame.draw.circle(surf, (r_c, g_c, b_c, style.fill_alpha), (cx, cy), rpx, 0)
    if style.edge_alpha > 0 and style.edge_width > 0:
        pygame.draw.circle(surf, (r_c, g_c, b_c, style.edge_alpha),
                           (cx, cy), rpx, style.edge_width)
    if style.inner_alpha > 0 and rpx >= 6:
        hi = (min(r_c + 90, 255), min(g_c + 90, 255), min(b_c + 90, 255))
        pygame.draw.circle(surf, (*hi, style.inner_alpha),
                           (cx, cy), max(rpx - 3, 1), style.inner_width)
    if style.cross_alpha > 0:
        cs = max(style.cross_min, min(style.cross_max, rpx // style.cross_size_div))
        cc = (min(r_c + 50, 255), min(g_c + 50, 255), min(b_c + 50, 255), style.cross_alpha)
        pygame.draw.line(surf, cc, (cx - cs, cy), (cx + cs, cy), 1)
        pygame.draw.line(surf, cc, (cx, cy - cs), (cx, cy + cs), 1)
    return surf


def _get_cached_hitbox_surface(rpx: int, color: Tuple,
                                style: HitboxStyle) -> pygame.Surface:
    qr  = _quantize_radius(rpx)
    cc  = _clamp_rgb(color)
    _record_surface_key(qr, cc, style)
    key = (qr, cc, style)
    s   = _surface_cache.get(key)
    if s is None:
        s = _make_hitbox_surface(qr, cc, style)
        _surface_cache[key] = s
    return s


def prebuild_all_hitbox_surfaces() -> None:
    colors = {_clamp_rgb(c) for p in COLORS.values() for c in p}
    colors.add(_clamp_rgb(COL_PROJ))
    count = 0
    for rpx in range(2, 161, 4):
        for color in colors:
            for style in (STYLE_ACTIVE, STYLE_INACTIVE, STYLE_PROJECTILE):
                _get_cached_hitbox_surface(rpx, color, style)
                count += 1
    print(f"[surface prebuild] built {count} surfaces")


def save_surface_manifest(path: str = CACHE_MANIFEST_FILE) -> None:
    entries = [
        {"rpx": rpx, "color": list(color), "style": sname}
        for rpx, color, sname in sorted(_seen_surface_keys,
                                        key=lambda x: (x[2], x[1], x[0]))
    ]
    with open(path, "w", encoding="utf-8") as f:
        _json.dump({"version": 1, "entries": entries}, f, indent=2)


def load_surface_manifest(path: str = CACHE_MANIFEST_FILE) -> None:
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = _json.load(f)
    except Exception as e:
        print(f"[surface manifest] load failed: {e}"); return
    loaded = 0
    for entry in payload.get("entries", []):
        try:
            _get_cached_hitbox_surface(
                int(entry["rpx"]),
                tuple(int(v) for v in entry["color"]),
                _style_from_name(str(entry["style"])))
            loaded += 1
        except Exception:
            continue
    print(f"[surface manifest] prebuilt {loaded} surfaces from disk")


# ----------------------------
# Node tracker
# ----------------------------

@dataclass
class ProjectileNodeState:
    x: float = 0.0; y: float = 0.0; z: float = 0.0
    dim_0: float = 0.0; dim_1: float = 0.0; dim_2: float = 0.0
    actor_ptr: int = 0; inactive_frames: int = 0; active: bool = False


class ProjectileNodeTracker:
    def __init__(self, pool_count: int):
        self._nodes: Dict[int, ProjectileNodeState] = {
            i: ProjectileNodeState() for i in range(pool_count)}

    def update_from_node(self, node_idx: int, node_addr: int) -> None:
        s     = self._nodes[node_idx]
        x     = _rf(node_addr + PROJ_OFF_X)
        y     = _rf(node_addr + PROJ_OFF_Y)
        z     = _rf(node_addr + PROJ_OFF_Z)
        dim_0 = _clean_dim(_rf(node_addr + PROJ_OFF_DIM_0))
        dim_1 = _clean_dim(_rf(node_addr + PROJ_OFF_DIM_1))
        dim_2 = _clean_dim(_rf(node_addr + PROJ_OFF_DIM_2))
        ptr   = 0
        for off in PROJ_PTR_CANDIDATES:
            p = rd32(node_addr + off)
            if _looks_like_ptr(p):
                ptr = p; break
        if (dim_0 or dim_1 or dim_2) and abs(x) < 30 and abs(y) < 30 and abs(z) < 30:
            s.x = x; s.y = y; s.z = z
            s.dim_0 = dim_0; s.dim_1 = dim_1; s.dim_2 = dim_2
            s.actor_ptr = ptr; s.inactive_frames = 0; s.active = True
        else:
            s.inactive_frames += 1
            if s.inactive_frames >= PROJECTILE_DESPAWN_FRAMES:
                s.active = False
                s.dim_0 = s.dim_1 = s.dim_2 = 0.0
                s.actor_ptr = 0

    def visible_nodes(self) -> List[ProjectileNodeState]:
        return [s for s in self._nodes.values() if s.active]

    def dump_active(self, max_nodes: int = 8) -> None:
        active = [(i, s) for i, s in self._nodes.items() if s.active]
        if not active:
            print("[NodeWatcher.dump] no active nodes"); return
        print("\n[NodeWatcher.dump] active nodes:")
        print(" idx        actor_ptr        x        y        z      d0      d1      d2")
        for idx, s in active[:max_nodes]:
            print(f"{idx:4d}   0x{s.actor_ptr:08X}   "
                  f"{s.x:7.3f} {s.y:7.3f} {s.z:7.3f}   "
                  f"{s.dim_0:5.3f} {s.dim_1:5.3f} {s.dim_2:5.3f}")


# ----------------------------
# Motion filter (fallback when no frame data)
# ----------------------------

@dataclass
class HitboxMotionState:
    still_frames: int = 0; motion_frames: int = 0; suppressed: bool = False
    prev_x: float = 0.0;   prev_y: float = 0.0;   initialized: bool = False


class MotionFilter:
    def __init__(self):
        self._states: Dict[Tuple[str, int], HitboxMotionState] = {}

    def cleanup(self):
        self._states = {k: v for k, v in self._states.items()
                        if not v.suppressed or v.motion_frames > 0}

    def update(self, slot: str, idx: int, x: float, y: float, r: float) -> bool:
        key = (slot, idx)
        if key not in self._states:
            self._states[key] = HitboxMotionState()
        st = self._states[key]

        if r <= 0.001:
            st.still_frames = st.motion_frames = 0
            st.suppressed = st.initialized = False
            return False

        if not st.initialized:
            st.prev_x = x; st.prev_y = y; st.initialized = True
            st.still_frames = 0; st.motion_frames = MOTION_FRAME_REQUIRED
            st.suppressed = False
            return True

        dx = x - st.prev_x; dy = y - st.prev_y
        delta = math.sqrt(dx * dx + dy * dy)
        st.prev_x = x; st.prev_y = y

        if delta >= MOTION_THRESHOLD:
            st.still_frames = 0
            st.motion_frames = min(st.motion_frames + 1, MOTION_FRAME_REQUIRED + 1)
            if st.suppressed and st.motion_frames >= MOTION_FRAME_REQUIRED:
                st.suppressed = False
        else:
            st.motion_frames = 0
            st.still_frames = min(st.still_frames + 1, STILL_FRAME_LIMIT + 1)
            if not st.suppressed and st.still_frames >= STILL_FRAME_LIMIT:
                st.suppressed = True

        return not st.suppressed

    def reset_slot(self, slot: str, count: int):
        for i in range(count):
            self._states.pop((slot, i), None)


# ----------------------------
# Memory helpers
# ----------------------------

def rb(addr: int) -> int:
    v = rd32(addr & ~3)
    if v is None: return 0
    return (v >> ((3 - (addr & 3)) * 8)) & 0xFF


def _rf(addr: int) -> float:
    v = rd32(addr)
    if v is None: return 0.0
    try:
        f = struct.unpack(">f", struct.pack(">I", v))[0]
        return f if math.isfinite(f) else 0.0
    except Exception:
        return 0.0


rf = _rf


def _clean_dim(v: float) -> float:
    return 0.0 if (not math.isfinite(v) or v < 0.01 or v > 3.0) else v


def _looks_like_ptr(v) -> bool:
    return (v is not None and 0x90000000 <= v <= 0x94000000 and (v & 3) == 0)


def read_hitboxes(slot_base: int, layout: HitboxLayout):
    base = slot_base + layout.struct_shift
    flag = rb(base + layout.off_flag)
    return [(_rf(base + b + layout.off_x), _rf(base + b + layout.off_y),
             _rf(base + b + layout.off_r), flag)
            for b in layout.blocks]


def read_camera_pos(layout: CameraLayout):
    return (_rf(layout.base + layout.off_x), _rf(layout.base + layout.off_y),
            _rf(layout.base + layout.off_z), _rf(layout.base + layout.off_w))


# ----------------------------
# Projectile helpers
# ----------------------------

def get_projectile_actors() -> List[int]:
    return [ptr for i in range(ACTOR_MAX)
            if (ptr := rd32(ACTOR_TABLE + i * 4)) is not None
            and 0x91000000 <= ptr <= 0x94000000]


def read_projectile_positions() -> List[Tuple[float, float, float, int]]:
    result = []
    for a in get_projectile_actors():
        x = _rf(a + ACTOR_OFF_X); y = _rf(a + ACTOR_OFF_Y); z = _rf(a + ACTOR_OFF_Z)
        if abs(x) < 30 and abs(y) < 30:
            result.append((x, y, z, a))
    return result


def read_projectile_hitboxes(actor_ptr: int) -> List[Tuple[float, float, float, float]]:
    ay = _rf(actor_ptr + ACTOR_OFF_Y); az = _rf(actor_ptr + ACTOR_OFF_Z)
    return [(_rf(actor_ptr + xo), ay, az, r)
            for xo, ro in PROJ_HB_OFFSETS
            if 0.05 < (r := _rf(actor_ptr + ro)) < 3.0
            and abs(_rf(actor_ptr + xo)) < 30]


def update_projectile_nodes(tracker: ProjectileNodeTracker) -> None:
    node_idx = 0
    for pool in PROJECTILE_POOLS:
        for i in range(PROJECTILE_NODE_COUNT):
            tracker.update_from_node(node_idx, pool + i * PROJECTILE_NODE_STRIDE)
            node_idx += 1


# ----------------------------
# Win32 helpers
# ----------------------------

def find_dolphin_hwnd() -> Optional[int]:
    candidates: List[Tuple[int, int, str]] = []

    def score_title(t: str) -> int:
        tl = t.lower()
        if "dolphin" not in tl: return -10_000
        s = 0
        if "|" in t: s += 50 + min(30, t.count("|") * 5)
        for tok in ("jit", "jit64", "opengl", "vulkan", "d3d", "direct3d", "hle"):
            if tok in tl: s += 20
        if "(" in t and ")" in t: s += 30
        for bad in ("memory", "watch", "log", "breakpoint", "register",
                    "disassembly", "config", "settings"):
            if bad in tl: s -= 25
        if t.count("|") >= 3: s += 20
        return s

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd): return
        title = win32gui.GetWindowText(hwnd) or ""
        if title and "dolphin" in title.lower():
            candidates.append((score_title(title), hwnd, title))

    win32gui.EnumWindows(cb, None)
    if not candidates: return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def get_client_screen_rect(hwnd: int):
    l, t, r, b = win32gui.GetClientRect(hwnd)
    tl = win32gui.ClientToScreen(hwnd, (l, t))
    br = win32gui.ClientToScreen(hwnd, (r, b))
    return tl[0], tl[1], br[0] - tl[0], br[1] - tl[1]


def apply_overlay_style(hwnd: int) -> None:
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    style &= ~(win32con.WS_CAPTION | win32con.WS_THICKFRAME |
               win32con.WS_MINIMIZE | win32con.WS_MAXIMIZE | win32con.WS_SYSMENU)
    style |= win32con.WS_POPUP
    win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)
    ex = (win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
          | win32con.WS_EX_LAYERED) & ~win32con.WS_EX_TOPMOST
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)
    win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 0, win32con.LWA_COLORKEY)
    win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                          win32con.SWP_FRAMECHANGED | win32con.SWP_NOMOVE |
                          win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)


def sync_overlay_to_dolphin(dolphin_hwnd: int, overlay_hwnd: int):
    x, y, w, h = get_client_screen_rect(dolphin_hwnd)
    win32gui.SetWindowPos(overlay_hwnd, win32con.HWND_NOTOPMOST, x, y, w, h,
                          win32con.SWP_NOACTIVATE)
    return w, h


# ----------------------------
# Overlay
# ----------------------------

class Overlay:
    def __init__(self, cfg: DisplayConfig):
        self.cfg = cfg
        self.cam_x = self.cam_y = self.cam_z = 0.0
        self.ref_cam_z = None
        self.w, self.h = cfg.baseline_w, cfg.baseline_h
        self.cx = self.w // 2
        self.cy = self.h // 2 + cfg.center_y_offset_px
        self.screen: Optional[pygame.Surface] = None
        self.font_small: Optional[pygame.font.Font] = None
        self.debug_axes = cfg.show_debug_axes
        self.viewport_scale = self.h / 720.0
        self.base_aspect    = 4.0 / 3.0
        self.stretch_factor = (self.w / float(self.h)) / self.base_aspect

    def init_pygame(self) -> int:
        pygame.init()
        self.font_small = pygame.font.SysFont("consolas", 11)
        self.screen     = pygame.display.set_mode((self.w, self.h), pygame.SRCALPHA)
        pygame.display.set_caption("TvC Hitbox Overlay v3")
        icon_path = os.path.join("assets", "portraits", "Placeholder.png")
        if not os.path.exists(icon_path):
            icon_path = os.path.join("assets", "icon.png")
        if os.path.exists(icon_path):
            pygame.display.set_icon(pygame.image.load(icon_path).convert_alpha())
        hwnd = pygame.display.get_wm_info()["window"]
        apply_overlay_style(hwnd)
        return hwnd

    def on_resize(self, w: int, h: int):
        if w <= 0 or h <= 0 or (w == self.w and h == self.h): return
        self.w, self.h = w, h
        self.screen         = pygame.display.set_mode((w, h), pygame.SRCALPHA)
        self.viewport_scale = h / 720.0
        self.cx, self.cy    = int(w * 0.5), int(h * 0.5)
        self.stretch_factor = (w / float(h)) / self.base_aspect

    def world_to_screen(self, wx: float, wy: float, wz: float):
        if self.ref_cam_z is None and abs(self.cam_z) > 0.0001:
            self.ref_cam_z = self.cam_z
        cam_scale  = (self.ref_cam_z / self.cam_z
                      if self.ref_cam_z and abs(self.cam_z) > 0.0001 else 1.0)
        zoom_scale = self.cfg.baseline_ppu * self.viewport_scale * cam_scale
        sx = self.cx + (wx - self.cam_x) * zoom_scale * self.stretch_factor
        sy = self.cy - ((wy - self.cam_y) + WORLD_Y_OFFSET) * zoom_scale
        return int(sx), int(sy), 1.0, zoom_scale

    def clear(self):
        self.screen.fill(COL_BG)

    def draw_debug_axes(self):
        if not self.debug_axes: return
        pygame.draw.line(self.screen, COL_DEBUG, (0, self.h // 2), (self.w, self.h // 2), 1)
        pygame.draw.line(self.screen, COL_DEBUG, (self.w // 2, 0), (self.w // 2, self.h), 1)

    def _project_hitbox(self, x, y, z, r):
        if r <= 0.001 or not math.isfinite(r) or r > HITBOX_MAX_RENDER_RADIUS: return None
        if not math.isfinite(x) or not math.isfinite(y): return None
        r   = min(r, self.cfg.max_radius_units)
        sx, sy, depth, focal = self.world_to_screen(x, y, z)
        rpx = _quantize_radius(max(2, int((r / depth) * focal)))
        return sx, sy, rpx

    def draw_hitbox(self, x, y, z, r, color, label, is_active=False):
        res = self._project_hitbox(x, y, z, r)
        if res is None: return
        sx, sy, rpx = res
        style = STYLE_ACTIVE if is_active else STYLE_INACTIVE
        surf  = _get_cached_hitbox_surface(rpx, color[:3], style)
        qr    = _quantize_radius(rpx)
        self.screen.blit(surf, (sx - qr - style.pad, sy - qr - style.pad))
        if SHOW_HITBOX_LABELS and qr >= 12 and self.font_small:
            self.screen.blit(self.font_small.render(label, True, color[:3]),
                             (sx + qr + 5, sy - 8))

    def draw_projectile_hitbox(self, x, y, z, r, color, label):
        if abs(x - self.cam_x) > 30 or abs(y - self.cam_y) > 25: return
        res = self._project_hitbox(x, y, z, r)
        if res is None: return
        sx, sy, rpx = res
        qr = _quantize_radius(rpx)
        if qr > 400: return
        surf = _get_cached_hitbox_surface(qr, color[:3], STYLE_PROJECTILE)
        self.screen.blit(surf, (sx - qr - STYLE_PROJECTILE.pad, sy - qr - STYLE_PROJECTILE.pad))
        if SHOW_PROJECTILE_LABELS and qr >= 10 and self.font_small:
            self.screen.blit(
                self.font_small.render(f"{label} r={r:.2f}", True, (*color[:3], 150)),
                (sx + qr + 5, sy - 8))

    def present(self):
        pygame.display.flip()


# ----------------------------
# Debug dumps
# ----------------------------

def debug_dump_pools() -> None:
    print("\n--- projectile pool dump ---")
    for pool in PROJECTILE_POOLS:
        print(f"\nPOOL 0x{pool:08X}")
        for i in range(PROJECTILE_NODE_COUNT):
            addr = pool + i * PROJECTILE_NODE_STRIDE
            x  = _rf(addr + PROJ_OFF_X); y = _rf(addr + PROJ_OFF_Y); z = _rf(addr + PROJ_OFF_Z)
            d0 = _clean_dim(_rf(addr + PROJ_OFF_DIM_0))
            d1 = _clean_dim(_rf(addr + PROJ_OFF_DIM_1))
            d2 = _clean_dim(_rf(addr + PROJ_OFF_DIM_2))
            if abs(x) > 0.001 or abs(y) > 0.001 or abs(z) > 0.001:
                print(f"node {i:02d} x={x:7.3f} y={y:7.3f} z={z:7.3f} "
                      f"d0={d0:.3f} d1={d1:.3f} d2={d2:.3f}")


def debug_dump_actor_hitboxes(actor_ptr: int) -> None:
    print(f"\n--- actor hitbox scan @ 0x{actor_ptr:08X} ---")
    for off in range(0x40, 0x200, 4):
        v = _rf(actor_ptr + off)
        if 0.05 < v < 3.0:
            xc = _rf(actor_ptr + off - 8); yc = _rf(actor_ptr + off - 4)
            if abs(xc) < 30 and abs(yc) < 15:
                print(f"  +0x{off:03X}  r={v:.4f}  nearby_x={xc:.3f}  nearby_y={yc:.3f}")


# ----------------------------
# Main
# ----------------------------

def main():
    hook()
    dolphin_hwnd = find_dolphin_hwnd()
    if not dolphin_hwnd:
        print("Dolphin not found.")
        return

    overlay      = Overlay(DISPLAY)
    overlay_hwnd = overlay.init_pygame()
    prebuild_all_hitbox_surfaces()
    win32gui.SetWindowLong(overlay_hwnd, win32con.GWL_HWNDPARENT, dolphin_hwnd)
    clock = pygame.time.Clock()

    motion_filter = MotionFilter()
    node_tracker  = ProjectileNodeTracker(len(PROJECTILE_POOLS) * PROJECTILE_NODE_COUNT)
    _last_char_ids: Dict[str, int] = {}

    print("TvC Hitbox Overlay active.")
    print("  idle / walk / block / hurt / etc  ->  hitboxes HIDDEN")
    print("  any attack, no frame data         ->  motion filter fallback")
    print("  any attack, frame data present    ->  startup visible | active bright | recovery hidden")

    running = True
    while running:
        w, h = sync_overlay_to_dolphin(dolphin_hwnd, overlay_hwnd)
        overlay.on_resize(w, h)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if   event.key == pygame.K_ESCAPE: running = False
                elif event.key == pygame.K_F1: overlay.debug_axes = not overlay.debug_axes
                elif event.key == pygame.K_F3: node_tracker.dump_active()
                elif event.key == pygame.K_F4: debug_dump_pools()
                elif event.key == pygame.K_F5:
                    projs = read_projectile_positions()
                    if projs:
                        x, y, z, a = projs[0]
                        print(f"Probing actor 0x{a:08X} at ({x:.2f}, {y:.2f})")
                        debug_dump_actor_hitboxes(a)
                    else:
                        print("No projectile actors found")

        # Character change detection
        for name, base in SLOT_BASES.items():
            cid = rd32(base + OFF_CHAR_ID) or 0
            if _last_char_ids.get(name) != cid:
                print(f"[CharChange] {name} {_last_char_ids.get(name)} -> {cid}")
                _last_char_ids[name] = cid
                break

        # Camera
        camx, camy, camz, _ = read_camera_pos(CAMERA)
        if USE_LIVE_CAMERA:
            overlay.cam_x = camx
            overlay.cam_y = camy
            overlay.cam_z = camz

        _slot_filter = _read_slot_filter()
        hud_data     = _read_hud_data()

        if pygame.time.get_ticks() % 2 == 0:
            update_projectile_nodes(node_tracker)

        overlay.clear()
        overlay.draw_debug_axes()

        for name, base in SLOT_BASES.items():
            if not _slot_filter.get(name, True):
                continue

            hud_key   = SLOT_TO_HUD_KEY.get(name, f"{name[0:2]}-C1")
            slot_info = hud_data.get(hud_key, {})
            cur_id    = slot_info.get("mv_id_display")
            mv_label  = (slot_info.get("mv_label") or "").strip().lower()

            # -------------------------------------------------------
            # IDLE CHECK
            # If no move ID, or the label matches an idle keyword,
            # skip drawing entirely and keep motion filter ticking so
            # it resets cleanly for when an attack comes.
            # -------------------------------------------------------
            try:
                cur_id = int(cur_id) if cur_id is not None else None
            except Exception:
                cur_id = None

            is_idle = (
                cur_id is None
                or cur_id in SUPPRESSED_MOVE_IDS
            )

            boxes = read_hitboxes(base, HITBOX)

            if is_idle:
                for i, (x, y, r, _) in enumerate(boxes):
                    motion_filter.update(name, i, x, y, r)
                continue

            # -------------------------------------------------------
            # ATTACK PATH
            # Anything not idle gets hitboxes drawn.
            # Use frame data if available, else fall back to motion filter.
            # -------------------------------------------------------
            move_state = _update_move_state(hud_key, hud_data)
            phase      = move_state["phase"]
            palette    = COLORS.get(name, [(255, 255, 255)])

            for i, (x, y, r, flag) in enumerate(boxes):
                if r <= 0.001:
                    continue

                base_color = palette[i % len(palette)]

                if phase == "fallback":
                    # No frame data — original motion filter behaviour
                    if motion_filter.update(name, i, x, y, r):
                        overlay.draw_hitbox(
                            x, y, 0, r, base_color, f"{name}[{i}]",
                            is_active=(flag == 0x53)
                        )

                elif phase == "startup":
                    # Show immediately on valid move ID, but not flashing yet
                    overlay.draw_hitbox(
                        x, y, 0, r, base_color, f"{name}[{i}]",
                        is_active=False
                    )

                elif phase == "active":
                    # Flash / bright when active window begins
                    overlay.draw_hitbox(
                        x, y, 0, r, base_color, f"{name}[{i}]",
                        is_active=True
                    )

                elif phase == "recovery":
                    # Hide after active window ends
                    motion_filter.update(name, i, x, y, r)
        # Projectiles — always drawn regardless of move state
        for x, y, z, a in read_projectile_positions():
            hbs = read_projectile_hitboxes(a)
            r   = hbs[0][3] if hbs else 0.35
            overlay.draw_projectile_hitbox(x, y + PROJECTILE_Y_OFFSET, z, r, COL_PROJ, "PRJ")

        if pygame.time.get_ticks() % 300 == 0:
            motion_filter.cleanup()

        overlay.present()
        clock.tick(DISPLAY.fps)

    save_surface_manifest()
    pygame.quit()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            save_surface_manifest()
        except Exception:
            pass
        import traceback
        traceback.print_exc()
        input("\n[CRASHED] Press Enter to close...")