#!/usr/bin/env python3
"""
hitbox_overlay_v2.py
Transparent always-on-top hitbox overlay for TvC.
Reads live hitbox data from Dolphin memory.

v2: Translation-based despawn system.
    Hitboxes that aren't moving fast enough across frames are
    considered "stale" and suppressed from rendering.
    This eliminates leftover radii from previous moves that stay
    on screen when a shorter-hitbox move is performed next.
"""

from __future__ import annotations

import ctypes
import math
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
import os
import pygame
import win32con
import win32gui

from dolphin_io import hook, rd32

import json as _json

WORLD_Y_OFFSET = -0.7

PERSPECTIVE_Z_OVERRIDE: Optional[float] = None
HITBOX_FILTER_FILE = "hitbox_filter.json"
_last_filter_mtime = 0.0
_slot_filter = {"P1": True, "P2": True, "P3": True, "P4": True}

# ----------------------------
# Translation-based despawn config
# ----------------------------

# Minimum world-unit movement per frame for a hitbox to be considered "live".
# Hitboxes moving less than this are treated as stale/leftover.
# Tune this: too low = stale hitboxes linger; too high = real hitboxes flicker.
MOTION_THRESHOLD: float = 0.003

# How many consecutive "slow" frames before a hitbox is hidden.
# A small value (e.g. 3) despawns quickly; larger values add lag but reduce flicker.
STILL_FRAME_LIMIT: int = 4

# How many consecutive "fast" frames needed to RE-SPAWN a suppressed hitbox.
# Prevents noisy single-frame spikes from re-enabling stale hitboxes.
MOTION_FRAME_REQUIRED: int = 2


def _read_slot_filter() -> dict:
    global _last_filter_mtime, _slot_filter
    try:
        import os
        mt = os.path.getmtime(HITBOX_FILTER_FILE)
        if mt != _last_filter_mtime:
            _last_filter_mtime = mt
            with open(HITBOX_FILTER_FILE) as f:
                _slot_filter = _json.load(f)
    except Exception:
        pass
    return _slot_filter


# ----------------------------
# DPI awareness
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
# Config
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


SLOT_BASES: Dict[str, int] = {
    "P1": 0x9246B9C0,
    "P2": 0x92B6BA00,
    "P3": 0x927EB9E0,
    "P4": 0x92EEBA20,
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

COLORS: Dict[str, List[Tuple[int, int, int]]] = {
    "P1": [(255, 60, 60), (255, 140, 0), (255, 220, 0)],
    "P2": [(180, 60, 255), (60, 200, 255), (60, 255, 180)],
    "P3": [(255, 80, 180), (255, 0, 120), (255, 120, 200)],
    "P4": [(80, 255, 120), (0, 255, 80), (120, 255, 200)],
}

COL_CROSS = (255, 255, 255)
COL_DIM = (120, 120, 120)
COL_BG = (0, 0, 0)
COL_DEBUG = (0, 255, 0)


# ----------------------------
# Translation tracker
# ----------------------------
@dataclass
class HitboxMotionState:
    """
    Tracks per-hitbox position history to determine if it is
    actively translating (live) or sitting still (stale).

    still_frames:
        Consecutive frames below MOTION_THRESHOLD.
    motion_frames:
        Consecutive frames above MOTION_THRESHOLD (used for re-spawn).
    suppressed:
        Whether this hitbox is currently hidden due to low motion.
    prev_x / prev_y:
        Last observed world position for delta computation.
    """
    still_frames: int = 0
    motion_frames: int = 0
    suppressed: bool = False
    prev_x: float = 0.0
    prev_y: float = 0.0
    initialized: bool = False


class MotionFilter:
    """
    Manages one HitboxMotionState per (slot, hitbox_index) pair.
    Call update() each frame with the current world position and radius.
    Returns True if the hitbox should be rendered.
    """

    def __init__(self):
        # Key: (slot_name, hitbox_index)  →  HitboxMotionState
        self._states: Dict[Tuple[str, int], HitboxMotionState] = {}

    def _key(self, slot: str, idx: int) -> Tuple[str, int]:
        return (slot, idx)

    def update(self, slot: str, idx: int, x: float, y: float, r: float) -> bool:
        """
        Feed current frame position. Returns True if hitbox should render.

        A hitbox with r <= 0 is never rendered regardless of motion.
        """
        key = self._key(slot, idx)

        if key not in self._states:
            self._states[key] = HitboxMotionState()

        state = self._states[key]

        # No radius → always hidden; reset state so it re-evaluates cleanly
        # when the slot becomes active again.
        if r <= 0.001:
            state.still_frames = 0
            state.motion_frames = 0
            state.suppressed = False
            state.initialized = False
            return False

        # First frame this hitbox has a radius: show it optimistically,
        # start tracking from here.
        if not state.initialized:
            state.prev_x = x
            state.prev_y = y
            state.initialized = True
            state.still_frames = 0
            state.motion_frames = MOTION_FRAME_REQUIRED  # treat first appearance as moving
            state.suppressed = False
            return True

        # Compute displacement from last frame
        dx = x - state.prev_x
        dy = y - state.prev_y
        delta = math.sqrt(dx * dx + dy * dy)

        state.prev_x = x
        state.prev_y = y

        if delta >= MOTION_THRESHOLD:
            state.still_frames = 0
            state.motion_frames = min(state.motion_frames + 1, MOTION_FRAME_REQUIRED + 1)
            if state.suppressed and state.motion_frames >= MOTION_FRAME_REQUIRED:
                # Enough consecutive motion frames → re-enable
                state.suppressed = False
        else:
            state.motion_frames = 0
            state.still_frames = min(state.still_frames + 1, STILL_FRAME_LIMIT + 1)
            if not state.suppressed and state.still_frames >= STILL_FRAME_LIMIT:
                state.suppressed = True

        return not state.suppressed

    def reset_slot(self, slot: str, count: int):
        """Clear state for all hitbox indices of a slot."""
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


def rf(addr: int) -> float:
    v = rd32(addr)
    if v is None:
        return 0.0
    try:
        f = struct.unpack(">f", struct.pack(">I", v))[0]
        return f if math.isfinite(f) else 0.0
    except Exception:
        return 0.0


def read_hitboxes(slot_base: int, layout: HitboxLayout):
    base = slot_base + layout.struct_shift
    out = []
    flag = rb(base + layout.off_flag)

    for b in layout.blocks:
        x = rf(base + b + layout.off_x)
        y = rf(base + b + layout.off_y)
        r = rf(base + b + layout.off_r)
        out.append((x, y, r, flag))

    return out

def read_fighter_root(slot_base: int):
    # Resolved base is at slot_base
    # +0xB0 appears to be world position from your dump
    root_x = rf(slot_base + 0xB0)
    root_y = rf(slot_base + 0xB4)
    root_z = rf(slot_base + 0xB8)
    return root_x, root_y, root_z

def read_camera_pos(layout: CameraLayout):
    return (
        rf(layout.base + layout.off_x),
        rf(layout.base + layout.off_y),
        rf(layout.base + layout.off_z),
        rf(layout.base + layout.off_w),
    )
CAM_VIEW_BASE = 0x8053CB20  # confirmed live view matrix region

def read_view16() -> List[float]:
    return [rf(CAM_VIEW_BASE + i * 4) for i in range(16)]

def mul_vec4_mat4_colmajor(m, v):
    # m: length-16 list of floats, column-major (OpenGL style)
    # v: (x,y,z,w)
    x, y, z, w = v
    return (
        m[0]*x  + m[4]*y  + m[8]*z  + m[12]*w,
        m[1]*x  + m[5]*y  + m[9]*z  + m[13]*w,
        m[2]*x  + m[6]*y  + m[10]*z + m[14]*w,
        m[3]*x  + m[7]*y  + m[11]*z + m[15]*w,
    )

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
        for tok in ("jit", "jit64", "opengl", "vulkan", "d3d", "direct3d", "hле", "hle"):
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
    style &= ~(win32con.WS_CAPTION |
               win32con.WS_THICKFRAME |
               win32con.WS_MINIMIZE |
               win32con.WS_MAXIMIZE |
               win32con.WS_SYSMENU)
    style |= win32con.WS_POPUP
    win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)

    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    ex |= win32con.WS_EX_LAYERED
    ex &= ~win32con.WS_EX_TOPMOST
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)

    win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 0, win32con.LWA_COLORKEY)

    win32gui.SetWindowPos(
        hwnd,
        win32con.HWND_NOTOPMOST,
        0, 0, 0, 0,
        win32con.SWP_FRAMECHANGED |
        win32con.SWP_NOMOVE |
        win32con.SWP_NOSIZE |
        win32con.SWP_NOACTIVATE
    )


def sync_overlay_to_dolphin(dolphin_hwnd: int, overlay_hwnd: int):
    x, y, w, h = get_client_screen_rect(dolphin_hwnd)
    win32gui.SetWindowPos(
        overlay_hwnd,
        win32con.HWND_NOTOPMOST,
        x, y, w, h,
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

    def init_pygame(self) -> int:
        pygame.init()
        self.font_small = pygame.font.SysFont("consolas", 11)
        self.font_hud = pygame.font.SysFont("consolas", 13, bold=True)
        self.screen = pygame.display.set_mode((self.w, self.h), pygame.SRCALPHA)
        pygame.display.set_caption("TvC Hitbox Overlay v2")

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
        self.screen = pygame.display.set_mode((self.w, self.h), pygame.SRCALPHA)
        scale_y = self.h / float(self.cfg.baseline_h)
        self.ppu = self.cfg.baseline_ppu * scale_y
        self.cx = self.w // 2
        self.cy = self.h // 2 + self.cfg.center_y_offset_px

    def _perspective_scale(self) -> float:
        return self.ppu * self.zoom
    
    def world_to_screen(self, world_x: float, world_y: float, world_z: float):
        # Simple planar projection for 2.5D fighter

        # Stable zoom based on cam_z (inverse relationship)
        if abs(self.cam_z) > 0.0001:
            zoom_scale = self.ppu * (7.26 / self.cam_z)
        else:
            zoom_scale = self.ppu

        sx = self.cx + (world_x - self.cam_x) * zoom_scale
        sy = self.cy - ((world_y - self.cam_y) + WORLD_Y_OFFSET) * zoom_scale

        return int(sx), int(sy), 1.0, zoom_scale
    def clear(self):
        self.screen.fill(COL_BG)

    def draw_debug_axes(self):
        if not self.debug_axes:
            return
        pygame.draw.line(self.screen, COL_DEBUG, (0, self.h // 2), (self.w, self.h // 2), 1)
        pygame.draw.line(self.screen, COL_DEBUG, (self.w // 2, 0), (self.w // 2, self.h), 1)

    def draw_hitbox(self, x, y, z, r, color, label, is_active=False):
        if r <= 0.001 or not math.isfinite(r):
            return
        if not math.isfinite(x) or not math.isfinite(y):
            return

        r = min(r, self.cfg.max_radius_units)
        proj = self.world_to_screen(x, y, z)
        if not proj:
            return
        sx, sy, depth, focal = proj
        rpx = max(2, int((r / depth) * focal))
        if rpx <= 0 or rpx > 5000:
            return

        if len(color) == 3:
            r_c, g_c, b_c = color
        else:
            r_c, g_c, b_c, _ = color

        hit_surf = pygame.Surface((rpx * 2 + 8, rpx * 2 + 8), pygame.SRCALPHA)
        center = (rpx + 4, rpx + 4)

        if is_active:
            pygame.draw.circle(hit_surf, (r_c, g_c, b_c, 140), center, rpx)
            pygame.draw.circle(hit_surf, (255, 255, 255, 255), center, rpx, 4)
            pygame.draw.circle(hit_surf, (r_c, g_c, b_c, 120), center, rpx + 3, 4)
        else:
            pygame.draw.circle(hit_surf, (r_c, g_c, b_c, 70), center, rpx + 2, 3)
            pygame.draw.circle(hit_surf, (r_c, g_c, b_c, 220), center, rpx, 3)

        self.screen.blit(hit_surf, (sx - rpx - 4, sy - rpx - 4))

        pygame.draw.circle(self.screen, COL_CROSS, (sx, sy), 2)
        cs = 6
        pygame.draw.line(self.screen, COL_CROSS, (sx - cs, sy), (sx + cs, sy), 2)
        pygame.draw.line(self.screen, COL_CROSS, (sx, sy - cs), (sx, sy + cs), 2)

        txt = self.font_small.render(f"{label} r={r:.2f}", True, (r_c, g_c, b_c))
        self.screen.blit(txt, (sx + rpx + 6, sy - 10))

    def draw_hud(self, counts, motion_filter: MotionFilter):
        base = " | ".join([f"{k}={v}" for k, v in counts.items()])
        ref_str = f"{self.ref_cam_z:.4f}" if self.ref_cam_z is not None else "none"
        debug = f"  |  cam_z={self.cam_z:.4f}  ref_z={ref_str}"

        # Count suppressed hitboxes for HUD visibility
        suppressed_total = sum(
            1 for s in motion_filter._states.values() if s.suppressed
        )
        supp_str = f"  |  suppressed={suppressed_total}"

        hud = self.font_hud.render(base + debug + supp_str, True, COL_DIM)
        self.screen.blit(hud, (8, 8))

    def present(self):
        pygame.display.flip()


# ----------------------------
# Main
# ----------------------------
def main():
    hook()

    dolphin_hwnd = find_dolphin_hwnd()
    if not dolphin_hwnd:
        print("Dolphin not found.")
        return

    overlay = Overlay(DISPLAY)
    overlay_hwnd = overlay.init_pygame()
    win32gui.SetWindowLong(
        overlay_hwnd,
        win32con.GWL_HWNDPARENT,
        dolphin_hwnd
    )
    clock = pygame.time.Clock()

    # One shared motion filter tracks all slots × hitbox indices
    motion_filter = MotionFilter()

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

        camx, camy, camz, camw = read_camera_pos(CAMERA)
        if USE_LIVE_CAMERA:
            overlay.cam_x = camx
            overlay.cam_y = camy
            overlay.cam_z = camz

        _slot_filter = _read_slot_filter()

        slots = {name: read_hitboxes(base, HITBOX)
                 for name, base in SLOT_BASES.items()
                 if _slot_filter.get(name, True)}

        overlay.clear()
        overlay.draw_debug_axes()

        counts = {}
        for name, base in SLOT_BASES.items():
            if not _slot_filter.get(name, True):
                continue

            boxes = read_hitboxes(base, HITBOX)
            root_x, root_y, root_z = read_fighter_root(base)

            active = 0
            palette = COLORS.get(name, [(255, 255, 255)])

            for i, (x, y, r, flag) in enumerate(boxes):
                # --- Translation filter ---
                # Ask the motion filter if this hitbox is "live enough" to render.
                # Stale hitboxes (not moving) are suppressed even if r > 0.
                should_render = motion_filter.update(name, i, x, y, r)

                if not should_render:
                    continue

                if r > 0.001:
                    active += 1
                    base_color = palette[i % len(palette)]
                    is_active = (flag == 0x53)

                    if is_active:
                        pulse = int(50 + 40 * math.sin(pygame.time.get_ticks() * 0.02))
                        alpha = min(255, 180 + pulse)
                    else:
                        alpha = 220

                    color = (*base_color, alpha)
                    world_x = x
                    world_y = y

                    world_z = 0
                    overlay.draw_hitbox(world_x, world_y, world_z, r, base_color, f"{name}[{i}]", is_active=is_active)

            counts[name] = active

        overlay.draw_hud(counts, motion_filter)
        overlay.present()
        clock.tick(DISPLAY.fps)

    pygame.quit()


if __name__ == "__main__":
    main()