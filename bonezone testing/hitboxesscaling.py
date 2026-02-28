#!/usr/bin/env python3
"""
hitbox_overlay.py
Transparent always-on-top hitbox overlay for TvC.
Reads live hitbox data from Dolphin memory.

Fixes included:
- DPI aware process (prevents Windows scale virtualization offsets)
- Tracks Dolphin client rect using both corners (true client->screen bounds)
- Moves/resizes overlay every frame to match Dolphin client area
- Recomputes center every resize
- PPU scales from a 720p baseline
"""

from __future__ import annotations

import ctypes
import math
import struct
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional

import pygame
import win32con
import win32gui

from dolphin_io import hook, rd32
WORLD_Y_OFFSET = -0.7

# ----------------------------
# DPI awareness (must happen before any window sizing logic)
# ----------------------------
def set_dpi_aware() -> None:
    try:
        # Best effort: newer API first, fallback to older
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
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


# VERIFIED SLOT BASES
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
)

CAMERA = CameraLayout(
    base=0x8053CB20,
    off_x=0x00,
    off_y=0x04,
    off_z=0x08,
    off_w=0x0C,
)

# NOTE: start center_y_offset_px at 0 while validating alignment.
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
# Win32 helpers
# ----------------------------
def find_dolphin_hwnd() -> Optional[int]:
    found: List[int] = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if "dolphin" in title.lower():
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def get_client_screen_rect(hwnd: int) -> Tuple[int, int, int, int]:
    """
    Returns Dolphin client area in screen coordinates using both corners.
    This avoids subtle offset errors versus assuming (0,0)+w,h mapping.
    """
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    tl = win32gui.ClientToScreen(hwnd, (left, top))
    br = win32gui.ClientToScreen(hwnd, (right, bottom))
    x = tl[0]
    y = tl[1]
    w = br[0] - tl[0]
    h = br[1] - tl[1]
    return x, y, w, h


def apply_overlay_style(hwnd: int) -> None:
    # Remove normal window decorations
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    style &= ~(win32con.WS_CAPTION |
               win32con.WS_THICKFRAME |
               win32con.WS_MINIMIZE |
               win32con.WS_MAXIMIZE |
               win32con.WS_SYSMENU)
    style |= win32con.WS_POPUP
    win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)

    # Extended styles
    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    ex |= win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)

    # Transparency (black = transparent)
    win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 0, win32con.LWA_COLORKEY)

    # Force style refresh
    win32gui.SetWindowPos(
        hwnd,
        win32con.HWND_TOPMOST,
        0, 0, 0, 0,
        win32con.SWP_FRAMECHANGED |
        win32con.SWP_NOMOVE |
        win32con.SWP_NOSIZE |
        win32con.SWP_NOACTIVATE
    )

def sync_overlay_to_dolphin(dolphin_hwnd: int, overlay_hwnd: int) -> Tuple[int, int]:
    x, y, w, h = get_client_screen_rect(dolphin_hwnd)
    win32gui.SetWindowPos(
        overlay_hwnd,
        win32con.HWND_TOPMOST,
        x,
        y,
        w,
        h,
        win32con.SWP_NOACTIVATE,
    )
    return w, h


# ----------------------------
# Memory helpers
# ----------------------------
def rf(addr: int) -> float:
    v = rd32(addr)
    if v is None:
        return 0.0
    try:
        f = struct.unpack(">f", struct.pack(">I", v))[0]
        return f if math.isfinite(f) else 0.0
    except Exception:
        return 0.0


def read_hitboxes(slot_base: int, layout: HitboxLayout) -> List[Tuple[float, float, float]]:
    base = slot_base + layout.struct_shift
    out: List[Tuple[float, float, float]] = []
    for b in layout.blocks:
        x = rf(base + b + layout.off_x)
        y = rf(base + b + layout.off_y)
        r = rf(base + b + layout.off_r)
        out.append((x, y, r))
    return out


def read_camera_pos(layout: CameraLayout) -> Tuple[float, float, float, float]:
    x = rf(layout.base + layout.off_x)
    y = rf(layout.base + layout.off_y)
    z = rf(layout.base + layout.off_z)
    w = rf(layout.base + layout.off_w)
    return x, y, z, w


# ----------------------------
# Overlay renderer
# ----------------------------
class Overlay:
    def __init__(self, cfg: DisplayConfig):
        self.cfg = cfg

        self.cam_x = 0.0
        self.cam_y = 0.0

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
        overlay_hwnd = pygame.display.get_wm_info()["window"]
        apply_overlay_style(overlay_hwnd)
        return overlay_hwnd

    def on_resize(self, w: int, h: int) -> None:
        if w <= 0 or h <= 0:
            return
        if w == self.w and h == self.h:
            return

        self.w, self.h = w, h
        self.screen = pygame.display.set_mode((self.w, self.h), pygame.SRCALPHA)

        # Scale PPU based on vertical baseline. Also compute X/Y scales in case you want them later.
        scale_y = self.h / float(self.cfg.baseline_h)
        self.ppu = self.cfg.baseline_ppu * scale_y

        self.cx = self.w // 2
        self.cy = self.h // 2 + self.cfg.center_y_offset_px

    def world_to_screen(self, x: float, y: float) -> Tuple[int, int]:
        sx = self.cx + int((x - self.cam_x) * self.ppu * self.zoom)
        sy = self.cy - int(((y + WORLD_Y_OFFSET) - self.cam_y) * self.ppu * self.zoom)
        return sx, sy

    def clear(self) -> None:
        assert self.screen is not None
        self.screen.fill(COL_BG)

    def draw_debug_axes(self) -> None:
        if not self.debug_axes:
            return
        assert self.screen is not None
        pygame.draw.line(self.screen, COL_DEBUG, (0, self.h // 2), (self.w, self.h // 2), 1)
        pygame.draw.line(self.screen, COL_DEBUG, (self.w // 2, 0), (self.w // 2, self.h), 1)

    def draw_hitbox(self, x: float, y: float, r: float, color: Tuple[int, int, int], label: str) -> None:
        if r <= 0.001 or not math.isfinite(r):
            return

        if not math.isfinite(x) or not math.isfinite(y):
            return

        r = min(r, self.cfg.max_radius_units)

        sx, sy = self.world_to_screen(x, y)

        # Ensure screen coords are sane integers
        if not isinstance(sx, int) or not isinstance(sy, int):
            return

        if abs(sx) > 20000 or abs(sy) > 20000:
            return  # guard against camera explosion frames

        rpx = int(r * self.ppu * self.zoom)

        if rpx <= 0 or rpx > 5000:
            return

        rpx = max(2, rpx)

        if self.screen is None or self.font_small is None:
            return

        

        hit_surf = pygame.Surface((rpx * 2 + 6, rpx * 2 + 6), pygame.SRCALPHA)

        center = (rpx + 3, rpx + 3)

        # Subtle outer glow ring (soft alpha, slightly larger)
        pygame.draw.circle(
            hit_surf,
            (*color, 60),
            center,
            rpx + 2,
            3
        )

        # Main crisp ring
        pygame.draw.circle(
            hit_surf,
            (*color, 255),
            center,
            rpx,
            3
        )
        pygame.draw.circle(self.screen, COL_CROSS, (sx, sy), 2)
        dest_x = int(sx - rpx - 3)
        dest_y = int(sy - rpx - 3)

        self.screen.blit(hit_surf, (dest_x, dest_y))
        dest_x = int(sx - rpx - 1)
        dest_y = int(sy - rpx - 1)

        self.screen.blit(hit_surf, (dest_x, dest_y))

        dest_x = int(sx - rpx - 1)
        dest_y = int(sy - rpx - 1)

        self.screen.blit(hit_surf, (dest_x, dest_y))

        cs = 5
        pygame.draw.line(self.screen, COL_CROSS, (sx - cs, sy), (sx + cs, sy), 1)
        pygame.draw.line(self.screen, COL_CROSS, (sx, sy - cs), (sx, sy + cs), 1)

        txt = self.font_small.render(f"{label} r={r:.2f}", True, color)
        self.screen.blit(txt, (sx + rpx + 4, sy - 8))
    def draw_hud(self, counts: Dict[str, int]) -> None:
        assert self.screen is not None
        assert self.font_hud is not None
        hud = self.font_hud.render(
            " | ".join([f"{k}={v}" for k, v in counts.items()]),
            True,
            COL_DIM,
        )
        self.screen.blit(hud, (8, 8))

    def present(self) -> None:
        pygame.display.flip()


# ----------------------------
# App loop
# ----------------------------
def main() -> None:
    hook()

    dolphin_hwnd = find_dolphin_hwnd()
    if not dolphin_hwnd:
        print("Dolphin not found.")
        return

    overlay = Overlay(DISPLAY)
    overlay_hwnd = overlay.init_pygame()

    clock = pygame.time.Clock()

    running = True
    while running:
        # Move/resize overlay to Dolphin client area, always
        w, h = sync_overlay_to_dolphin(dolphin_hwnd, overlay_hwnd)
        overlay.on_resize(w, h)

        # Input
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_F1:
                    overlay.debug_axes = not overlay.debug_axes

        # Camera
        camx, camy, _, _ = read_camera_pos(CAMERA)
        if USE_LIVE_CAMERA:
            overlay.cam_x = camx
            overlay.cam_y = camy

        # Read hitboxes
        slots: Dict[str, List[Tuple[float, float, float]]] = {
            name: read_hitboxes(base, HITBOX) for name, base in SLOT_BASES.items()
        }

        # Render
        overlay.clear()
        overlay.draw_debug_axes()

        counts: Dict[str, int] = {}
        for name, boxes in slots.items():
            active = 0
            palette = COLORS.get(name, [(255, 255, 255)])
            for i, (x, y, r) in enumerate(boxes):
                if r > 0.001:
                    active += 1
                    overlay.draw_hitbox(
                        x=x,
                        y=y,
                        r=r,
                        color=palette[i % len(palette)],
                        label=f"{name}[{i}]",
                    )
            counts[name] = active

        overlay.draw_hud(counts)
        overlay.present()
        clock.tick(DISPLAY.fps)

    pygame.quit()


if __name__ == "__main__":
    main()