#!/usr/bin/env python3
"""
hud_overlay.py
--------------
Transparent Dolphin-parented overlay that displays live per-slot HUD data:
    HP | Meter | MoveID | Baroque

Data is passed from main.py via hud_overlay_data.json (written each frame
when the overlay is active). This follows the same subprocess + colorkey
pattern used by hitboxesscaling.py.

Black (0, 0, 0) is the colorkey transparent colour -- do not draw anything
in pure black; use (1, 1, 1) or dark grays instead.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from typing import Optional

import pygame
import win32con
import win32gui

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_FILE = "hud_overlay_data.json"
TARGET_FPS = 60
COLORKEY = (0, 0, 0)          # fully transparent pixels

# Baseline layout values at 1280x720 — everything scales from these.
BASE_W          = 1280
BASE_H          = 720
BASE_FONT_SIZE  = 14
BASE_ROW_H      = 22           # height of each fighter row
BG_ALPHA        = 180          # background pill transparency (0-255)

# Per-slot anchor points in 720p units, measured from the TvC UI.
# X: left edge of the row text. Y: just below the character's health bar.
# Tweak these to fine-tune position without touching anything else.
SLOT_LAYOUT = {
    #  slot       x      y
    "P1-C1": (  28,   178),
    "P1-C2": (  28,   207),
    "P2-C1": ( 712,   178),
    "P2-C2": ( 712,   207),
}

SLOT_COLORS = {
    "P1-C1": (255, 110, 110),
    "P1-C2": (255, 160, 130),
    "P2-C1": (110, 170, 255),
    "P2-C2": (130, 200, 255),
}

COL_TEXT        = (220, 220, 220)
COL_BAROQUE_ON  = (255, 200, 60)
COL_BAROQUE_OFF = (150, 150, 150)


# ---------------------------------------------------------------------------
# DPI awareness (matches hitboxesscaling.py)
# ---------------------------------------------------------------------------

def set_dpi_aware() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


set_dpi_aware()


# ---------------------------------------------------------------------------
# Win32 helpers (copied verbatim from hitboxesscaling.py)
# ---------------------------------------------------------------------------

def find_dolphin_hwnd() -> Optional[int]:
    candidates = []

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
    ex |= win32con.WS_EX_LAYERED
    ex &= ~win32con.WS_EX_TOPMOST
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)

    win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 0, win32con.LWA_COLORKEY)
    win32gui.SetWindowPos(
        hwnd,
        win32con.HWND_NOTOPMOST,
        0, 0, 0, 0,
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
        x, y, w, h,
        win32con.SWP_NOACTIVATE,
    )
    return w, h


# ---------------------------------------------------------------------------
# Data reader
# ---------------------------------------------------------------------------

_last_data_mtime: float = 0.0
_cached_slots: dict = {}


def read_slot_data() -> dict:
    """Read hud_overlay_data.json; return cached value if file unchanged."""
    global _last_data_mtime, _cached_slots
    try:
        mt = os.path.getmtime(DATA_FILE)
        if mt != _last_data_mtime:
            _last_data_mtime = mt
            with open(DATA_FILE) as f:
                _cached_slots = json.load(f)
    except Exception:
        pass
    return _cached_slots


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

def make_font(size: int) -> pygame.font.Font:
    """Return a Consolas font at the given size, with fallback."""
    try:
        return pygame.font.SysFont("consolas", max(8, size), bold=True)
    except Exception:
        return pygame.font.Font(None, max(8, size))


def _draw_slot_row(screen: pygame.Surface, font: pygame.font.Font,
                   slot_label: str, snap: dict,
                   anchor_x: int, anchor_y: int, row_h: int, scale: float) -> None:
    """Draw a single compact fighter row at the given screen-space anchor."""

    char_name = snap.get("name") or "???"

    hp_cur = snap.get("cur")
    hp_max = snap.get("max")
    hp_str = (
        f"{int(hp_cur)}/{int(hp_max)}"
        if hp_cur is not None and hp_max
        else "---"
    )

    meter_val = snap.get("meter")
    try:
        meter_str = f"{float(meter_val):.2f}" if meter_val is not None else "---"
    except (TypeError, ValueError):
        meter_str = str(meter_val)

    move_id  = snap.get("mv_id_display")
    mv_label = snap.get("mv_label") or ""
    if move_id is not None:
        move_str = f"0x{int(move_id):04X}"
        if mv_label:
            move_str += f" {mv_label}"
    else:
        move_str = "---"

    baroque_ready = snap.get("baroque_ready_local", False)
    baroque_pct   = snap.get("baroque_red_pct_max", 0.0)
    baroque_str   = f"READY {baroque_pct:.1f}%" if baroque_ready else "---"
    baroque_col   = COL_BAROQUE_ON if baroque_ready else COL_BAROQUE_OFF
    slot_col      = SLOT_COLORS.get(slot_label, (200, 200, 200))

    seg_slot    = f"[{slot_label}]"
    seg_name    = f" {char_name}"
    seg_stats   = f"  HP:{hp_str}  Meter:{meter_str}  Move:{move_str}  Baroque:"
    seg_baroque = baroque_str

    total_w = (
        font.size(seg_slot)[0]
        + font.size(seg_name)[0]
        + font.size(seg_stats)[0]
        + font.size(seg_baroque)[0]
        + int(12 * scale)
    )

    pill = pygame.Surface((total_w, row_h), pygame.SRCALPHA)
    pill.fill((10, 10, 10, BG_ALPHA))
    screen.blit(pill, (anchor_x - 4, anchor_y - 1))

    cx     = anchor_x
    text_y = anchor_y + max(1, (row_h - font.get_height()) // 2)

    for text, color in (
        (seg_slot,    slot_col),
        (seg_name,    COL_TEXT),
        (seg_stats,   COL_TEXT),
        (seg_baroque, baroque_col),
    ):
        s = font.render(text, True, color)
        screen.blit(s, (cx, text_y))
        cx += s.get_width()


def draw_overlay(screen: pygame.Surface, font: pygame.font.Font,
                 slots: dict, scale: float) -> None:
    """Clear to colorkey then draw each slot row at its bar-aligned position."""
    screen.fill(COLORKEY)

    row_h = max(12, int(BASE_ROW_H * scale))

    for slot_label, (base_x, base_y) in SLOT_LAYOUT.items():
        snap = slots.get(slot_label)
        if not snap:
            continue

        ax = int(base_x * scale)
        ay = int(base_y * scale)

        _draw_slot_row(screen, font, slot_label, snap, ax, ay, row_h, scale)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    dolphin_hwnd = find_dolphin_hwnd()
    if not dolphin_hwnd:
        print("[hud_overlay] Dolphin window not found — exiting.")
        return

    pygame.init()

    # Start at baseline size; will resize to match Dolphin on first sync.
    screen = pygame.display.set_mode((BASE_W, BASE_H), pygame.SRCALPHA)
    pygame.display.set_caption("TvC HUD Overlay")

    overlay_hwnd = pygame.display.get_wm_info()["window"]
    apply_overlay_style(overlay_hwnd)
    win32gui.SetWindowLong(overlay_hwnd, win32con.GWL_HWNDPARENT, dolphin_hwnd)

    cur_w, cur_h = BASE_W, BASE_H
    scale = 1.0
    font = make_font(BASE_FONT_SIZE)

    clock = pygame.time.Clock()
    running = True

    while running:
        w, h = sync_overlay_to_dolphin(dolphin_hwnd, overlay_hwnd)

        if w > 0 and h > 0 and (w, h) != (cur_w, cur_h):
            cur_w, cur_h = w, h
            screen = pygame.display.set_mode((w, h), pygame.SRCALPHA)
            # Scale by the shorter axis so the overlay fits regardless of
            # widescreen / 4:3 / integer-scaled etc.
            scale = min(w / BASE_W, h / BASE_H)
            font  = make_font(int(BASE_FONT_SIZE * scale))

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False

        slots = read_slot_data()
        draw_overlay(screen, font, slots, scale)
        pygame.display.flip()
        clock.tick(TARGET_FPS)

    pygame.quit()


if __name__ == "__main__":
    main()