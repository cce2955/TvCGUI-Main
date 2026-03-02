#!/usr/bin/env python3
"""
hitbox_overlay.py
Transparent always-on-top hitbox overlay for TvC.
Reads live hitbox data from Dolphin memory.
"""

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

from dolphin_io import hook, rd32

import json as _json
# WORLD_Y_OFFSET shifts world Y before projection.
# TvC world origin is not centered on character midline.
# This value compensates for in-game coordinate bias so hitboxes
# visually align with the character model on screen.
WORLD_Y_OFFSET = -0.7

# Perspective baseline Z reference.
# If set to a float, that value is used as the fixed reference camera Z.
# If None, the first valid camera Z observed at runtime is captured
# and treated as the neutral center-stage baseline.
PERSPECTIVE_Z_OVERRIDE: Optional[float] = None
HITBOX_FILTER_FILE = "hitbox_filter.json"
_last_filter_mtime = 0.0
_slot_filter = {"P1": True, "P2": True, "P3": True, "P4": True}

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
    """
    Describes the layout of the hitbox struct inside a fighter object.

    struct_shift:
        Offset from fighter base to hitbox struct.

    blocks:
        Relative offsets for each hitbox entry.

    off_x, off_y, off_r:
        Per-block offsets for X, Y, and radius floats.

    off_flag:
        Offset for the hitbox state flag byte.
    """
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


# Fighter slot base pointers.
# These are static pointer addresses that resolve to each fighter struct.
# The actual hitbox struct is located at (slot_base + struct_shift).
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
# Memory helpers
# ----------------------------
def rb(addr: int) -> int:
    """
    Read a single big-endian byte from Dolphin memory.
    Uses rd32 and bit shifting to avoid separate 8-bit reads.
    """
    v = rd32(addr & ~3)
    if v is None:
        return 0
    shift = (3 - (addr & 3)) * 8
    return (v >> shift) & 0xFF


def rf(addr: int) -> float:
    """
    Read a big-endian 32-bit float from Dolphin memory.
    Returns 0.0 if value is invalid or non-finite.
    """
    
    v = rd32(addr)
    if v is None:
        return 0.0
    try:
        f = struct.unpack(">f", struct.pack(">I", v))[0]
        return f if math.isfinite(f) else 0.0
    except Exception:
        return 0.0


def read_hitboxes(slot_base: int, layout: HitboxLayout):
    """
    Read all configured hitbox entries for a fighter slot.

    Returns:
        List of tuples:
            (x, y, radius, flag)

    flag:
        Raw state byte used to determine active vs inactive hitboxes.
    """
    base = slot_base + layout.struct_shift
    out = []
    flag = rb(base + layout.off_flag)

    for b in layout.blocks:
        x = rf(base + b + layout.off_x)
        y = rf(base + b + layout.off_y)
        r = rf(base + b + layout.off_r)
        out.append((x, y, r, flag))

    return out


def read_camera_pos(layout: CameraLayout):
    return (
        rf(layout.base + layout.off_x),
        rf(layout.base + layout.off_y),
        rf(layout.base + layout.off_z),
        rf(layout.base + layout.off_w),
    )


# ----------------------------
# Win32 helpers
# ----------------------------
def find_dolphin_hwnd() -> Optional[int]:
    """
    Prefer the actual game/render window over the Dolphin main UI.
    Strategy:
      - Score visible windows whose title contains "dolphin"
      - Strongly prefer titles that look like the render window:
          contain '|' and common backend tokens (JIT/OpenGL/Vulkan/D3D/HLE)
          or contain a game ID in parentheses e.g. (STKE08)
      - As a fallback, return the highest scoring Dolphin window.
    """
    candidates: List[Tuple[int, int, str]] = []

    def score_title(t: str) -> int:
        tl = t.lower()
        if "dolphin" not in tl:
            return -10_000

        s = 0

        # Render window usually has a bunch of " | " segments
        if "|" in t:
            s += 50
            s += min(30, t.count("|") * 5)

        # Typical render-title tokens
        for tok in ("jit", "jit64", "opengl", "vulkan", "d3d", "direct3d", "hле", "hle"):
            if tok in tl:
                s += 20

        # Game ID pattern "(STKE08)" or any "(xxxxx)" of 5+ chars
        if "(" in t and ")" in t:
            s += 30

        # Penalize common non-render UI windows
        for bad in ("memory", "watch", "log", "breakpoint", "register", "disassembly", "config", "settings"):
            if bad in tl:
                s -= 25

        # Strong preference: title includes a game name-ish chunk after pipes
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
    best_score, best_hwnd, best_title = candidates[0]

    return best_hwnd


def get_client_screen_rect(hwnd: int):
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    tl = win32gui.ClientToScreen(hwnd, (left, top))
    br = win32gui.ClientToScreen(hwnd, (right, bottom))
    return tl[0], tl[1], br[0] - tl[0], br[1] - tl[1]


def apply_overlay_style(hwnd: int) -> None:
    """
    Convert pygame window into a borderless layered overlay window.
    Removes decorations and enables color-key transparency.
    """
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
    """
    Resize and reposition overlay to exactly match Dolphin client area.
    Called every frame to follow window moves and resizes.
    """
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
        pygame.display.set_caption("TvC Hitbox Overlay")

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
        """
        Compute pixels-per-unit adjusted for camera Z perspective.
        Keeps hitboxes visually consistent as camera zoom changes.
        """
        if not math.isfinite(self.cam_z) or abs(self.cam_z) < 0.0001:
            return self.ppu * self.zoom
        return self.ppu * self.zoom * (self.ref_cam_z / self.cam_z)

    def world_to_screen(self, x: float, y: float):
        """
        Convert world coordinates into screen pixel coordinates.
        Applies camera offset and perspective scaling.
        """
        # Capture reference Z once (center-stage baseline)
        if PERSPECTIVE_Z_OVERRIDE is not None:
            self.ref_cam_z = PERSPECTIVE_Z_OVERRIDE
        elif self.ref_cam_z is None and math.isfinite(self.cam_z) and abs(self.cam_z) > 0.0001:
            self.ref_cam_z = self.cam_z

        scale = self._perspective_scale()

        sx = self.cx + int((x - self.cam_x) * scale)
        sy = self.cy - int(((y + WORLD_Y_OFFSET) - self.cam_y) * scale)
        return sx, sy

    def clear(self):
        self.screen.fill(COL_BG)

    def draw_debug_axes(self):
        if not self.debug_axes:
            return
        pygame.draw.line(self.screen, COL_DEBUG, (0, self.h // 2), (self.w, self.h // 2), 1)
        pygame.draw.line(self.screen, COL_DEBUG, (self.w // 2, 0), (self.w // 2, self.h), 1)

    def draw_hitbox(self, x, y, r, color, label, is_active=False):
        """
        Render a single hitbox circle with crosshair and label.
        Active hitboxes are rendered filled and glowing.
        Inactive hitboxes are rendered hollow.
        """
        if r <= 0.001 or not math.isfinite(r):
            return
        if not math.isfinite(x) or not math.isfinite(y):
            return

        r = min(r, self.cfg.max_radius_units)
        sx, sy = self.world_to_screen(x, y)

        scale = self._perspective_scale()
        rpx = max(2, int(r * scale))
        if rpx <= 0 or rpx > 5000:
            return

        if len(color) == 3:
            r_c, g_c, b_c = color
        else:
            r_c, g_c, b_c, _ = color

        hit_surf = pygame.Surface((rpx * 2 + 8, rpx * 2 + 8), pygame.SRCALPHA)
        center = (rpx + 4, rpx + 4)

        if is_active:
            # Strong interior fill
            pygame.draw.circle(
                hit_surf,
                (r_c, g_c, b_c, 140),
                center,
                rpx
            )

            # Thick outline
            pygame.draw.circle(
                hit_surf,
                (255, 255, 255, 255),
                center,
                rpx,
                4
            )

            # Outer glow
            pygame.draw.circle(
                hit_surf,
                (r_c, g_c, b_c, 120),
                center,
                rpx + 3,
                4
            )
        else:
            # Hollow version
            pygame.draw.circle(
                hit_surf,
                (r_c, g_c, b_c, 70),
                center,
                rpx + 2,
                3
            )
            pygame.draw.circle(
                hit_surf,
                (r_c, g_c, b_c, 220),
                center,
                rpx,
                3
            )

        self.screen.blit(hit_surf, (sx - rpx - 4, sy - rpx - 4))

        # Crosshair
        pygame.draw.circle(self.screen, COL_CROSS, (sx, sy), 2)
        cs = 6
        pygame.draw.line(self.screen, COL_CROSS, (sx - cs, sy), (sx + cs, sy), 2)
        pygame.draw.line(self.screen, COL_CROSS, (sx, sy - cs), (sx, sy + cs), 2)

        txt = self.font_small.render(f"{label} r={r:.2f}", True, (r_c, g_c, b_c))
        self.screen.blit(txt, (sx + rpx + 6, sy - 10))

    def draw_hud(self, counts):
        base = " | ".join([f"{k}={v}" for k, v in counts.items()])
        ref_str = f"{self.ref_cam_z:.4f}" if self.ref_cam_z is not None else "none"
        debug = f"  |  cam_z={self.cam_z:.4f}  ref_z={ref_str}"
        hud = self.font_hud.render(base + debug, True, COL_DIM)
        self.screen.blit(hud, (8, 8))
    def present(self):
        pygame.display.flip()


# ----------------------------
# Main
# ----------------------------
def main():
    """
    Entry point for the standalone hitbox overlay.

    Responsibilities:
        - Attach to Dolphin memory.
        - Locate Dolphin render window.
        - Create borderless transparent overlay.
        - Read hitbox data each frame.
        - Project world coordinates to screen space.
        - Render hitboxes aligned with game window.
        - Sync overlay position with Dolphin client area.
    """
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
        for name, boxes in slots.items():
            active = 0
            palette = COLORS.get(name, [(255, 255, 255)])

            for i, (x, y, r, flag) in enumerate(boxes):
                if r > 0.001:
                    active += 1
                    base_color = palette[i % len(palette)]
                    # Hitbox active-state detection.
                    # Through reverse-engineering, flag value 0x53 corresponds to an
                    # active attacking hitbox in TvC.
                    #
                    # Other observed values typically represent:
                    #   - Inactive / idle state
                    #   - Pre-activation frames
                    #   - Disabled slots
                    #
                    # If future research reveals additional active values,
                    # this condition should be expanded into a set.
                    is_active = (flag == 0x53)

                    if is_active:
                        pulse = int(50 + 40 * math.sin(pygame.time.get_ticks() * 0.02))
                        alpha = min(255, 180 + pulse)
                    else:
                        alpha = 220

                    color = (*base_color, alpha)
                    overlay.draw_hitbox(x, y, r, base_color, f"{name}[{i}]", is_active=is_active)

            counts[name] = active

        overlay.draw_hud(counts)
        overlay.present()
        clock.tick(DISPLAY.fps)

    pygame.quit()


if __name__ == "__main__":
    main()