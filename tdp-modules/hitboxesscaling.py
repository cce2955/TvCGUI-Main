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

WORLD_Y_OFFSET = -0.7
PROJECTILE_Y_OFFSET: float = 0
PROJECTILE_RADIUS_SCALE: float = 0.5
PROJECTILE_DESPAWN_FRAMES: int = 6
PERSPECTIVE_Z_OVERRIDE: Optional[float] = None
HITBOX_FILTER_FILE = "hitbox_filter.json"
_last_filter_mtime = 0.0
_slot_filter = {"P1": True, "P2": True, "P3": True, "P4": True}

MOTION_THRESHOLD: float = 0.003
STILL_FRAME_LIMIT: int = 4
MOTION_FRAME_REQUIRED: int = 2
ACTOR_TABLE = 0x80476E50
ACTOR_MAX   = 16

ACTOR_OFF_X = 0x5C
ACTOR_OFF_Y = 0x6C
ACTOR_OFF_Z = 0x7C

HITBOX_MAX_RENDER_RADIUS: float = 4.0

# ----------------------------
# HUD data (frame data feed from main)
# ----------------------------

HUD_DATA_FILE = "hud_overlay_data.json"
_hud_data: Dict[str, dict] = {}
_last_hud_mtime = 0.0

# Per-slot move state tracking
# state = { "move_id": int|None, "frame": int, "phase": str }
# phase: "idle" | "startup" | "active" | "recovery"
_slot_move_state: Dict[str, dict] = {}

# TvC runs at 30fps internally; overlay runs at 60fps.
# Frame counts from scan_normals are in 30fps ticks.
# We divide our internal counter by 2 when comparing.
OVERLAY_FPS_DIVISOR = 2


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
    slot_info = hud_data.get(slot_label, {})
    cur_id = slot_info.get("mv_id_display")
    active_start = slot_info.get("active_start")
    active_end = slot_info.get("active_end")

    state = _slot_move_state.get(slot_label)

    if state is None or cur_id != state.get("move_id"):
        state = {
            "move_id": cur_id,
            "frame": 0,
            "game_frame": 0,
            "phase": "idle" if cur_id is None else "startup",
            "has_data": (active_start is not None and active_end is not None),
            "active_start": active_start,
            "active_end": active_end,
        }
    else:
        state["frame"] += 1
        state["game_frame"] = state["frame"] // OVERLAY_FPS_DIVISOR

        # Only replace frame-data window when new valid data exists.
        if active_start is not None and active_end is not None:
            state["has_data"] = True
            state["active_start"] = active_start
            state["active_end"] = active_end

    if cur_id is None:
        state["phase"] = "idle"
    elif state.get("has_data") and state.get("active_start") is not None and state.get("active_end") is not None:
        gf = state["game_frame"]
        if gf < state["active_start"]:
            state["phase"] = "startup"
        elif gf <= state["active_end"]:
            state["phase"] = "active"
        else:
            state["phase"] = "recovery"
    else:
        state["phase"] = "fallback"

    _slot_move_state[slot_label] = state
    return state
# ----------------------------
# Projectile pools
# ----------------------------

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

PROJ_OFF_X: int = 0x00
PROJ_OFF_Y: int = 0x10
PROJ_OFF_Z: int = 0x20
PROJ_OFF_DIM_0: int = 0x08
PROJ_OFF_DIM_1: int = 0x18
PROJ_OFF_DIM_2: int = 0x28

PROJ_PTR_CANDIDATES = (0x04, 0x0C, 0x14, 0x1C, 0x24, 0x2C)

# Projectile hitbox sub-node offsets within actor struct
PROJ_HB_OFFSETS = [
    (0x064, 0x06C),   # node 0: (x_off, r_off)
    (0x094, 0x09C),   # node 1
    (0x0C4, 0x0CC),   # node 2
]

OFF_CHAR_ID = 0x14

# Projectile motion tracking (no motion filter needed — actors vanish naturally)
_proj_last_pos: Dict[int, Tuple[float, float]] = {}
_proj_still_frames: Dict[int, int] = {}
PROJ_STILL_LIMIT = 4


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


# ----------------------------
# Addresses
# ----------------------------

SLOT_BASES: Dict[str, int] = {
    "P1": 0x9246B9C0,
    "P2": 0x92B6BA00,
    "P3": 0x927EB9E0,
    "P4": 0x92EEBA20,
}

# Map overlay slot names to HUD data keys
SLOT_TO_HUD_KEY: Dict[str, str] = {
    "P1": "P1-C1",
    "P2": "P2-C1",
    "P3": "P1-C2",
    "P4": "P2-C2",
}

HITBOX = HitboxLayout(
    struct_shift=0x4C0,
    blocks=(0x64, 0xA4, 0xE4),
    off_x=0x00,
    off_y=0x04,
    off_r=0x18,
    off_flag=0xC3,
)

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

# ----------------------------
# Colors
# ----------------------------

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

COL_CROSS   = (255, 255, 255)
COL_DIM     = (120, 120, 120)
COL_BG      = (0, 0, 0)
COL_DEBUG   = (0, 255, 0)
COL_PROJ    = (100, 220, 255)   # cyan for projectiles


# ----------------------------
# Surface cache
# ----------------------------

_surface_cache: Dict[Tuple[int, Tuple[int,int,int], bool], pygame.Surface] = {}


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
# Node tracker (kept for pool scanning, used as fallback reference)
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


class ProjectileNodeTracker:
    def __init__(self, pool_count: int):
        self._nodes: Dict[int, ProjectileNodeState] = {
            i: ProjectileNodeState() for i in range(pool_count)
        }

    def update_from_node(self, node_idx: int, node_addr: int) -> None:
        state = self._nodes[node_idx]
        x     = _rf(node_addr + PROJ_OFF_X)
        y     = _rf(node_addr + PROJ_OFF_Y)
        z     = _rf(node_addr + PROJ_OFF_Z)
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
        sane_pos    = abs(x) < 30 and abs(y) < 30 and abs(z) < 30

        if sane_pos and has_any_dim:
            state.x = x; state.y = y; state.z = z
            state.dim_0 = dim_0; state.dim_1 = dim_1; state.dim_2 = dim_2
            state.actor_ptr = actor_ptr
            state.inactive_frames = 0
            state.active = True
        else:
            state.inactive_frames += 1
            if state.inactive_frames >= PROJECTILE_DESPAWN_FRAMES:
                state.active = False
                state.dim_0 = state.dim_1 = state.dim_2 = 0.0
                state.actor_ptr = 0

    def visible_nodes(self) -> List[ProjectileNodeState]:
        return [s for s in self._nodes.values() if s.active]

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
# Motion filter (for fallback / character hitboxes)
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


def _clean_dim(v: float) -> float:
    if not math.isfinite(v):
        return 0.0
    if v < 0.01 or v > 3.0:
        return 0.0
    return v


def _looks_like_ptr(v: int) -> bool:
    if v is None:
        return False
    if v < 0x90000000 or v > 0x94000000:
        return False
    if (v & 3) != 0:
        return False
    return True


def read_hitboxes(slot_base: int, layout: HitboxLayout):
    base = slot_base + layout.struct_shift
    out  = []
    flag = rb(base + layout.off_flag)
    for b in layout.blocks:
        x = _rf(base + b + layout.off_x)
        y = _rf(base + b + layout.off_y)
        r = _rf(base + b + layout.off_r)
        out.append((x, y, r, flag))
    return out


def read_fighter_root(slot_base: int):
    return _rf(slot_base + 0xB0), _rf(slot_base + 0xB4), _rf(slot_base + 0xB8)


def read_camera_pos(layout: CameraLayout):
    return (
        _rf(layout.base + layout.off_x),
        _rf(layout.base + layout.off_y),
        _rf(layout.base + layout.off_z),
        _rf(layout.base + layout.off_w),
    )


# ----------------------------
# Projectile helpers
# ----------------------------

def get_projectile_actors() -> List[int]:
    actors = []
    for i in range(ACTOR_MAX):
        ptr = rd32(ACTOR_TABLE + i * 4)
        if ptr is None:
            continue
        if 0x91000000 <= ptr <= 0x94000000:
            actors.append(ptr)
    return actors


def read_projectile_positions() -> List[Tuple[float, float, float, int]]:
    result = []
    for a in get_projectile_actors():
        x = _rf(a + ACTOR_OFF_X)
        y = _rf(a + ACTOR_OFF_Y)
        z = _rf(a + ACTOR_OFF_Z)
        if abs(x) < 30 and abs(y) < 30:
            result.append((x, y, z, a))
    return result


def read_projectile_hitboxes(actor_ptr: int) -> List[Tuple[float, float, float, float]]:
    """Read per-sphere hitboxes from a projectile actor struct."""
    actor_y = _rf(actor_ptr + ACTOR_OFF_Y)
    actor_z = _rf(actor_ptr + ACTOR_OFF_Z)
    results = []
    for x_off, r_off in PROJ_HB_OFFSETS:
        x = _rf(actor_ptr + x_off)
        r = _rf(actor_ptr + r_off)
        if 0.05 < r < 3.0 and abs(x) < 30:
            results.append((x, actor_y, actor_z, r))
    return results


def update_projectile_nodes(tracker: ProjectileNodeTracker) -> None:
    node_idx = 0
    for pool in PROJECTILE_POOLS:
        for i in range(PROJECTILE_NODE_COUNT):
            node_addr = pool + i * PROJECTILE_NODE_STRIDE
            tracker.update_from_node(node_idx, node_addr)
            node_idx += 1


def resolve_projectile_pools() -> List[int]:
    manager = 0x80476E50
    pools   = []
    for i in range(16):
        ptr = rd32(manager + i * 4)
        if ptr is None:
            continue
        if 0x91000000 <= ptr <= 0x94000000:
            pools.append(ptr - 0x6C)
    return pools


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
            s += 50 + min(30, t.count("|") * 5)
        for tok in ("jit", "jit64", "opengl", "vulkan", "d3d", "direct3d", "hle"):
            if tok in tl:
                s += 20
        if "(" in t and ")" in t:
            s += 30
        for bad in ("memory", "watch", "log", "breakpoint", "register",
                    "disassembly", "config", "settings"):
            if bad in tl:
                s -= 25
        if t.count("|") >= 3:
            s += 20
        return s

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if not title or "dolphin" not in title.lower():
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
    style &= ~(win32con.WS_CAPTION | win32con.WS_THICKFRAME |
               win32con.WS_MINIMIZE | win32con.WS_MAXIMIZE | win32con.WS_SYSMENU)
    style |= win32con.WS_POPUP
    win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)

    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    ex |= win32con.WS_EX_LAYERED
    ex &= ~win32con.WS_EX_TOPMOST
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)

    win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 0, win32con.LWA_COLORKEY)
    win32gui.SetWindowPos(
        hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
        win32con.SWP_FRAMECHANGED | win32con.SWP_NOMOVE |
        win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE,
    )


def sync_overlay_to_dolphin(dolphin_hwnd: int, overlay_hwnd: int):
    x, y, w, h = get_client_screen_rect(dolphin_hwnd)
    win32gui.SetWindowPos(
        overlay_hwnd, win32con.HWND_NOTOPMOST, x, y, w, h,
        win32con.SWP_NOACTIVATE,
    )
    return w, h


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

    def init_pygame(self) -> int:
        pygame.init()
        self.font_small = pygame.font.SysFont("consolas", 11)
        self.font_hud   = pygame.font.SysFont("consolas", 13, bold=True)
        self.screen     = pygame.display.set_mode((self.w, self.h), pygame.SRCALPHA)
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
        if w <= 0 or h <= 0 or (w == self.w and h == self.h):
            return
        self.w, self.h = w, h
        self.screen = pygame.display.set_mode((w, h), pygame.SRCALPHA)
        self.viewport_scale = h / 720.0
        self.aspect_ratio   = w / float(h)
        self.base_aspect    = 4.0 / 3.0
        self.x_aspect_scale = self.base_aspect / self.aspect_ratio
        self.cx = int(w * 0.5)
        self.cy = int(h * 0.5)
        self.window_aspect  = w / float(h)
        self.stretch_factor = self.window_aspect / self.base_aspect

    def world_to_screen(self, world_x: float, world_y: float, world_z: float):
        if self.ref_cam_z is None and abs(self.cam_z) > 0.0001:
            self.ref_cam_z = self.cam_z
        camera_scale = (
            self.ref_cam_z / self.cam_z
            if (self.ref_cam_z is not None and abs(self.cam_z) > 0.0001)
            else 1.0
        )
        zoom_scale = self.cfg.baseline_ppu * self.viewport_scale * camera_scale
        sx = self.cx + (world_x - self.cam_x) * zoom_scale * self.stretch_factor
        sy = self.cy - ((world_y - self.cam_y) + WORLD_Y_OFFSET) * zoom_scale
        return int(sx), int(sy), 1.0, zoom_scale

    def clear(self):
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
        return sx, sy, rpx

    def draw_hitbox(self, x, y, z, r, color, label, is_active=False):
        result = self._project_hitbox(x, y, z, r)
        if result is None:
            return
        sx, sy, rpx = result
        r_c, g_c, b_c = color[:3]
        pad  = 6
        surf = _get_cached_hitbox_surface(rpx, (r_c, g_c, b_c), is_active)
        cx = cy = rpx + pad

        if is_active:
            pygame.draw.circle(surf, (r_c, g_c, b_c, 55),  (cx, cy), rpx + 4, 4)
            pygame.draw.circle(surf, (r_c, g_c, b_c, 110), (cx, cy), rpx)
            pygame.draw.circle(surf, (r_c, g_c, b_c, 220), (cx, cy), rpx, 2)
            hi = (min(r_c + 90, 255), min(g_c + 90, 255), min(b_c + 90, 255))
            pygame.draw.circle(surf, (*hi, 150), (cx, cy), max(rpx - 3, 1), 1)
        else:
            pygame.draw.circle(surf, (r_c, g_c, b_c, 55),  (cx, cy), rpx)
            pygame.draw.circle(surf, (r_c, g_c, b_c, 170), (cx, cy), rpx, 2)

        self.screen.blit(surf, (sx - rpx - pad, sy - rpx - pad))

        cross_col = (min(r_c + 50, 255), min(g_c + 50, 255), min(b_c + 50, 255))
        cs = max(4, min(9, rpx // 3))
        cross_s = pygame.Surface((cs * 2 + 2, cs * 2 + 2), pygame.SRCALPHA)
        pygame.draw.line(cross_s, (*cross_col, 190), (0, cs + 1), (cs * 2 + 2, cs + 1), 1)
        pygame.draw.line(cross_s, (*cross_col, 190), (cs + 1, 0), (cs + 1, cs * 2 + 2), 1)
        self.screen.blit(cross_s, (sx - cs - 1, sy - cs - 1))

        if rpx >= 12 and self.font_small is not None:
            txt = self.font_small.render(label, True, color[:3])
            self.screen.blit(txt, (sx + rpx + 5, sy - 8))

    def draw_projectile_hitbox(self, x, y, z, r, color, label):
        if abs(x - self.cam_x) > 30 or abs(y - self.cam_y) > 25:
            return
        result = self._project_hitbox(x, y, z, r)
        if result is None:
            return
        sx, sy, rpx = result
        if rpx > 400:
            return

        r_c, g_c, b_c = color[:3]
        pad  = 8
        size = rpx * 2 + pad * 2
        surf = pygame.Surface((size, size), pygame.SRCALPHA)
        cx = cy = rpx + pad

        pygame.draw.circle(surf, (r_c, g_c, b_c, 55),  (cx, cy), rpx + 3, 2)
        pygame.draw.circle(surf, (r_c, g_c, b_c, 190), (cx, cy), rpx, 1)
        if rpx >= 6:
            pygame.draw.circle(surf, (r_c, g_c, b_c, 95), (cx, cy), max(rpx - 3, 1), 1)
        pygame.draw.circle(surf, (r_c, g_c, b_c, 45), (cx, cy), rpx)

        self.screen.blit(surf, (sx - rpx - pad, sy - rpx - pad))

        d = max(3, min(7, rpx // 3))
        dia_s = pygame.Surface((d * 2 + 3, d * 2 + 3), pygame.SRCALPHA)
        dc = d + 1
        pygame.draw.polygon(
            dia_s, (r_c, g_c, b_c, 200),
            [(dc, dc - d), (dc + d, dc), (dc, dc + d), (dc - d, dc)], 1,
        )
        self.screen.blit(dia_s, (sx - d - 1, sy - d - 1))

        if rpx >= 10 and self.font_small is not None:
            txt = self.font_small.render(f"{label} r={r:.2f}", True, (*color[:3], 150))
            self.screen.blit(txt, (sx + rpx + 5, sy - 8))

    def draw_hud(self, counts, motion_filter: MotionFilter,
                 node_tracker: ProjectileNodeTracker, slot_phases: Dict[str, str]):
        if self.font_hud is None:
            return
        base       = " | ".join([f"{k}={v}" for k, v in counts.items()])
        ref_str    = f"{self.ref_cam_z:.4f}" if self.ref_cam_z is not None else "none"
        debug      = f"  |  cam_z={self.cam_z:.4f}  ref_z={ref_str}"
        suppressed = sum(1 for s in motion_filter._states.values() if s.suppressed)
        supp_str   = f"  |  suppressed={suppressed}"
        active_prj = len(node_tracker.visible_nodes())
        prj_str    = f"  |  prj={active_prj}"

        # Show phase for each slot
        phase_str = "  |  " + "  ".join(
            f"{k}:{v}" for k, v in slot_phases.items()
        )

        hud = self.font_hud.render(
            base + debug + supp_str + prj_str + phase_str, True, COL_DIM
        )
        self.screen.blit(hud, (8, 8))

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
            x  = _rf(addr + PROJ_OFF_X)
            y  = _rf(addr + PROJ_OFF_Y)
            z  = _rf(addr + PROJ_OFF_Z)
            d0 = _clean_dim(_rf(addr + PROJ_OFF_DIM_0))
            d1 = _clean_dim(_rf(addr + PROJ_OFF_DIM_1))
            d2 = _clean_dim(_rf(addr + PROJ_OFF_DIM_2))
            if abs(x) > 0.001 or abs(y) > 0.001 or abs(z) > 0.001:
                print(
                    f"node {i:02d} "
                    f"x={x:7.3f} y={y:7.3f} z={z:7.3f} "
                    f"d0={d0:.3f} d1={d1:.3f} d2={d2:.3f}"
                )


def debug_dump_actor_hitboxes(actor_ptr: int) -> None:
    print(f"\n--- actor hitbox scan @ 0x{actor_ptr:08X} ---")
    for off in range(0x40, 0x200, 4):
        v = _rf(actor_ptr + off)
        if 0.05 < v < 3.0:
            x_c = _rf(actor_ptr + off - 8)
            y_c = _rf(actor_ptr + off - 4)
            if abs(x_c) < 30 and abs(y_c) < 15:
                print(f"  +0x{off:03X}  r={v:.4f}  nearby_x={x_c:.3f}  nearby_y={y_c:.3f}")


# ----------------------------
# Main
# ----------------------------

def main():
    hook()
    dolphin_hwnd = find_dolphin_hwnd()
    if not dolphin_hwnd:
        print("Dolphin not found.")
        return

    overlay     = Overlay(DISPLAY)
    overlay_hwnd = overlay.init_pygame()
    win32gui.SetWindowLong(overlay_hwnd, win32con.GWL_HWNDPARENT, dolphin_hwnd)
    clock = pygame.time.Clock()

    motion_filter = MotionFilter()

    total_nodes  = len(PROJECTILE_POOLS) * PROJECTILE_NODE_COUNT
    node_tracker = ProjectileNodeTracker(total_nodes)

    _last_char_ids: Dict[str, int] = {}

    print("TvC Hitbox Overlay active.")
    print("  F1 = toggle debug axes")
    print("  F3 = dump active node table")
    print("  F4 = dump projectile pools")
    print("  F5 = dump actor hitbox scan")
    print()
    print("  Hitbox phases (when frame data available):")
    print("    startup  = hitbox hidden")
    print("    active   = bright/filled hitbox")
    print("    recovery = hitbox hidden")
    print("    fallback = no data, use motion filter (original behaviour)")

    running = True
    while running:
        w, h = sync_overlay_to_dolphin(dolphin_hwnd, overlay_hwnd)
        overlay.on_resize(w, h)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_F1:
                    overlay.debug_axes = not overlay.debug_axes
                elif event.key == pygame.K_F2:
                    print("F2: (no-op)")
                elif event.key == pygame.K_F3:
                    node_tracker.dump_active()
                elif event.key == pygame.K_F4:
                    debug_dump_pools()
                elif event.key == pygame.K_F5:
                    projectiles = read_projectile_positions()
                    if projectiles:
                        x, y, z, a = projectiles[0]
                        print(f"Probing actor 0x{a:08X} at world ({x:.2f}, {y:.2f})")
                        debug_dump_actor_hitboxes(a)
                    else:
                        print("No projectile actors found")

        # Character change detection
        for name, base in SLOT_BASES.items():
            cid = rd32(base + OFF_CHAR_ID) or 0
            if _last_char_ids.get(name) != cid:
                print(f"[CharChange] {name} char_id {_last_char_ids.get(name)} -> {cid}")
                _last_char_ids[name] = cid
                break

        # Camera
        camx, camy, camz, camw = read_camera_pos(CAMERA)
        if USE_LIVE_CAMERA:
            overlay.cam_x = camx
            overlay.cam_y = camy
            overlay.cam_z = camz

        _slot_filter = _read_slot_filter()

        # Read HUD data (frame data from main.py)
        hud_data = _read_hud_data()

        # Update projectile nodes (every other frame for perf)
        if pygame.time.get_ticks() % 2 == 0:
            update_projectile_nodes(node_tracker)

        overlay.clear()
        overlay.draw_debug_axes()

        counts      = {}
        slot_phases = {}  # for HUD display

        for name, base in SLOT_BASES.items():
            if not _slot_filter.get(name, True):
                counts[name] = 0
                slot_phases[name] = "off"
                continue

            # Resolve HUD key (P1->P1-C1, P2->P2-C1, P3->P1-C2, P4->P2-C2)
            hud_key    = SLOT_TO_HUD_KEY.get(name, f"{name[0:2]}-C1")
            slot_info  = hud_data.get(hud_key, {})
            cur_id     = slot_info.get("mv_id_display")
            mv_label   = (slot_info.get("mv_label") or "").strip().lower()

            non_attack_keywords = (
                "idle", "stand", "walk", "crouch", "jump", "landing", "land",
                "fall", "block", "guard", "pushblock", "rising","crouching", "forward", "backward", "hitstun", "hurt",
                "tech", "throw tech", "recovery", "wake", "taunt", "intro",
                "win", "lose"
            )

            is_attack_move = (
                cur_id is not None
                and not any(k in mv_label for k in non_attack_keywords)
            )

            phase = "attack" if is_attack_move else "non-attack"
            slot_phases[name] = phase

            boxes   = read_hitboxes(base, HITBOX)
            active  = 0
            palette = COLORS.get(name, [(255, 255, 255)])

            for i, (x, y, r, flag) in enumerate(boxes):
                if not is_attack_move:
                    motion_filter.update(name, i, x, y, r)
                    continue

                if r > 0.001:
                    active += 1
                    base_color = palette[i % len(palette)]
                    overlay.draw_hitbox(
                        x, y, 0, r, base_color, f"{name}[{i}]",
                        is_active=(flag == 0x53)
                    )

            counts[name] = active

        # Projectiles — always use actor table + radius from struct
        projectiles = read_projectile_positions()
        for x, y, z, a in projectiles:
            hitboxes = read_projectile_hitboxes(a)
            if hitboxes:
                _, _, _, hr = hitboxes[0]
                overlay.draw_projectile_hitbox(
                    x, y + PROJECTILE_Y_OFFSET, z, hr, COL_PROJ, "PRJ"
                )
            else:
                overlay.draw_projectile_hitbox(
                    x, y + PROJECTILE_Y_OFFSET, z, 0.35, COL_PROJ, "PRJ"
                )

        # Periodic cleanup
        if pygame.time.get_ticks() % 300 == 0:
            motion_filter.cleanup()

        overlay.draw_hud(counts, motion_filter, node_tracker, slot_phases)
        overlay.present()
        clock.tick(DISPLAY.fps)

    pygame.quit()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        traceback.print_exc()
        input("\n[CRASHED] Press Enter to close...")