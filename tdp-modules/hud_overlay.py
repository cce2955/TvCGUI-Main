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

# Team accent colors
SLOT_COLORS = {
    "P1-C1": (255, 100, 100),
    "P1-C2": (255, 150, 120),
    "P2-C1": ( 90, 160, 255),
    "P2-C2": (120, 190, 255),
}

COL_TEXT         = (220, 220, 220)
COL_TEXT_DIM     = (120, 120, 120)   # passive state text
COL_DEAD         = ( 90,  90,  90)   # KO'd character
COL_HP_HIGH      = ( 60, 200,  90)   # HP bar > 30%
COL_HP_LOW       = (220,  60,  60)   # HP bar <= 30%
COL_HP_DEAD      = ( 70,  70,  70)   # HP bar when KO'd
COL_HP_BG        = ( 40,  40,  40)   # HP bar background
COL_METER_FULL   = ( 70, 140, 255)   # filled pip
COL_METER_EMPTY  = ( 35,  35,  50)   # empty pip
COL_BAROQUE_ON   = (255, 200,  60)
COL_BAROQUE_BG   = (100,  60,   8)   # amber badge bg
COL_ACTIVE_GLOW  = (255, 230, 100)   # active character highlight
BG_ALPHA         = 200

# Move IDs for assist/DHC off-screen states
ASSIST_STANDBY_IDS = {430, 432, 433}
ASSIST_ATTACK_IDS  = {420, 426, 427, 428}
ASSIST_OFF_IDS     = ASSIST_STANDBY_IDS | ASSIST_ATTACK_IDS

# Move labels that are passive — dim the move text
PASSIVE_LABELS = {
    "idle", "crouched", "couching", "standing", "jump", "jump forward",
    "jump back", "landing", "rising", "assist standby", "assist leave",
    "assist attack", "assist taunt", "tag out", "tag in",
}


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

def make_font(size: int, bold: bool = True) -> pygame.font.Font:
    try:
        return pygame.font.SysFont("consolas", max(8, size), bold=bold)
    except Exception:
        return pygame.font.Font(None, max(8, size))


def _is_active(snap: dict) -> bool:
    """True if this slot is the on-screen active fighter (not assist/standby)."""
    move_id = snap.get("mv_id_display")
    if move_id is None:
        return True
    return int(move_id) not in ASSIST_OFF_IDS


def _partner_slot(slot_label: str) -> str:
    """Return the other character slot on the same team."""
    return {
        "P1-C1": "P1-C2",
        "P1-C2": "P1-C1",
        "P2-C1": "P2-C2",
        "P2-C2": "P2-C1",
    }.get(slot_label, "")


def _draw_hp_bar(screen, x, y, bar_w, bar_h, hp_cur, hp_max, is_dead):
    """Draw a proportional HP bar with color coding."""
    pygame.draw.rect(screen, COL_HP_BG, (x, y, bar_w, bar_h), border_radius=2)
    if hp_max and hp_max > 0:
        frac = max(0.0, min(1.0, hp_cur / hp_max))
        fill_w = max(1, int(bar_w * frac))
        if is_dead:
            bar_col = COL_HP_DEAD
        elif frac <= 0.30:
            bar_col = COL_HP_LOW
        else:
            bar_col = COL_HP_HIGH
        pygame.draw.rect(screen, bar_col, (x, y, fill_w, bar_h), border_radius=2)


def _draw_meter_pips(screen, x, y, pip_w, pip_h, pip_gap, meter_val, is_dead):
    """Draw 5 meter pips (TvC max = 5 bars)."""
    MAX_PIPS = 5
    try:
        bars = float(meter_val) if meter_val is not None else 0.0
    except (TypeError, ValueError):
        bars = 0.0

    full_pips  = int(bars)
    partial    = bars - full_pips

    for i in range(MAX_PIPS):
        px = x + i * (pip_w + pip_gap)
        if is_dead:
            col = COL_METER_EMPTY
        elif i < full_pips:
            col = COL_METER_FULL
        elif i == full_pips and partial > 0.05:
            # Partial pip: interpolate brightness
            t = partial
            col = tuple(int(COL_METER_EMPTY[c] + (COL_METER_FULL[c] - COL_METER_EMPTY[c]) * t)
                        for c in range(3))
        else:
            col = COL_METER_EMPTY
        pygame.draw.rect(screen, col, (px, y, pip_w, pip_h), border_radius=1)


def _draw_slot_row(screen: pygame.Surface,
                   font: pygame.font.Font,
                   font_sm: pygame.font.Font,
                   slot_label: str,
                   snap: dict,
                   anchor_x: int, anchor_y: int,
                   row_h: int, scale: float,
                   is_active_char: bool) -> None:
    """Draw one polished fighter row."""

    slot_col  = SLOT_COLORS.get(slot_label, (200, 200, 200))
    hp_cur    = snap.get("cur") or 0
    hp_max    = snap.get("max") or 1
    is_dead   = (hp_cur <= 0)

    # Dim everything when dead
    if is_dead:
        name_col = COL_DEAD
        text_col = COL_DEAD
    elif not is_active_char:
        name_col = COL_TEXT_DIM
        text_col = COL_TEXT_DIM
    else:
        name_col = COL_TEXT
        text_col = COL_TEXT

    # ── Layout measurements ──────────────────────────────────────────────
    pad        = int(6  * scale)
    acc_w      = int(3  * scale)          # left accent bar width
    badge_w    = int(26 * scale)          # C1/C2 badge
    name_gap   = int(6  * scale)
    bar_w      = int(80 * scale)          # HP bar width
    bar_h      = max(4, int(6 * scale))
    pip_w      = max(4, int(8  * scale))  # meter pip
    pip_h      = max(4, int(8  * scale))
    pip_gap    = max(1, int(2  * scale))
    meter_w    = 5 * pip_w + 4 * pip_gap

    char_name  = snap.get("name") or "???"
    name_surf  = font.render(char_name, True, name_col)
    name_w     = name_surf.get_width()

    # HP number
    hp_str     = f"{int(hp_cur)}/{int(hp_max)}"
    hp_num_s   = font_sm.render(hp_str, True, text_col)

    # Meter value
    meter_val  = snap.get("meter")
    try:
        meter_f  = float(meter_val) if meter_val is not None else 0.0
        meter_str = f"{meter_f:.2f}"
    except (TypeError, ValueError):
        meter_f  = 0.0
        meter_str = "---"
    meter_num_s = font_sm.render(meter_str, True, text_col)

    # Move label
    move_id    = snap.get("mv_id_display")
    mv_label   = (snap.get("mv_label") or "").strip()
    if not mv_label and move_id is not None:
        mv_label = f"0x{int(move_id):04X}"
    is_passive = mv_label.lower() in PASSIVE_LABELS
    move_col   = COL_TEXT_DIM if (is_passive or not is_active_char or is_dead) else COL_TEXT
    move_surf  = font_sm.render(mv_label or "---", True, move_col)

    # Baroque
    baroque_ready = snap.get("baroque_ready_local", False) and not is_dead
    baroque_pct   = snap.get("baroque_red_pct_max", 0.0)

    # ── Total row width ──────────────────────────────────────────────────
    sep       = int(10 * scale)  # section separator gap
    label_sm  = font_sm.render("HP", True, COL_TEXT_DIM)
    label_h   = label_sm.get_height()

    baroque_badge_w = 0
    if baroque_ready:
        bq_text     = f"◆ {baroque_pct:.1f}%"
        bq_surf     = font_sm.render(bq_text, True, COL_BAROQUE_ON)
        baroque_badge_w = bq_surf.get_width() + int(10 * scale)

    total_w = (
        acc_w + pad
        + badge_w + name_gap
        + name_w + sep
        + font_sm.size("HP")[0] + int(4 * scale) + bar_w + int(4 * scale) + hp_num_s.get_width() + sep
        + font_sm.size("M")[0] + int(4 * scale) + meter_w + int(4 * scale) + meter_num_s.get_width() + sep
        + move_surf.get_width() + sep
        + baroque_badge_w
        + pad
    )

    # ── Background pill ──────────────────────────────────────────────────
    bg_col = (10, 10, 10)
    pill   = pygame.Surface((total_w, row_h), pygame.SRCALPHA)
    pill.fill((*bg_col, BG_ALPHA))
    screen.blit(pill, (anchor_x, anchor_y))

    # Active highlight border
    if is_active_char and not is_dead:
        pygame.draw.rect(screen, (*slot_col, 160),
                         (anchor_x, anchor_y, total_w, row_h), 1, border_radius=2)

    # Left accent bar
    pygame.draw.rect(screen, slot_col,
                     (anchor_x, anchor_y, acc_w, row_h), border_radius=1)

    # C1/C2 badge
    badge_x   = anchor_x + acc_w + pad
    badge_col = slot_col if is_active_char and not is_dead else COL_DEAD
    pygame.draw.rect(screen, (*badge_col, 200),
                     (badge_x, anchor_y + int(3*scale), badge_w, row_h - int(6*scale)),
                     border_radius=2)
    badge_label = "C1" if slot_label.endswith("C1") else "C2"
    bs = font_sm.render(badge_label, True, (240, 240, 240))
    screen.blit(bs, (badge_x + (badge_w - bs.get_width()) // 2,
                     anchor_y + (row_h - bs.get_height()) // 2))

    cx      = badge_x + badge_w + name_gap
    mid_y   = anchor_y + row_h // 2
    text_y  = mid_y - font.get_height() // 2
    sm_top  = anchor_y + int(2 * scale)
    sm_bot  = anchor_y + row_h - int(2 * scale) - font_sm.get_height()

    # Character name
    screen.blit(name_surf, (cx, text_y))
    cx += name_w + sep

    # HP section
    lbl = font_sm.render("HP", True, COL_TEXT_DIM)
    screen.blit(lbl, (cx, sm_top))
    bar_y = mid_y - bar_h // 2
    _draw_hp_bar(screen, cx, bar_y, bar_w, bar_h, hp_cur, hp_max, is_dead)
    cx += bar_w + int(4 * scale)
    screen.blit(hp_num_s, (cx, sm_bot))
    cx += hp_num_s.get_width() + sep

    # Meter section
    lbl = font_sm.render("M", True, COL_TEXT_DIM)
    screen.blit(lbl, (cx, sm_top))
    pip_y = mid_y - pip_h // 2
    _draw_meter_pips(screen, cx, pip_y, pip_w, pip_h, pip_gap, meter_f, is_dead)
    cx += meter_w + int(4 * scale)
    screen.blit(meter_num_s, (cx, sm_bot))
    cx += meter_num_s.get_width() + sep

    # Move label
    screen.blit(move_surf, (cx, mid_y - move_surf.get_height() // 2))
    cx += move_surf.get_width() + sep

    # Baroque badge (only when active)
    if baroque_ready:
        bq_text = f"◆ {baroque_pct:.1f}%"
        bq_surf = font_sm.render(bq_text, True, COL_BAROQUE_ON)
        bq_w    = bq_surf.get_width() + int(8 * scale)
        bq_h    = row_h - int(6 * scale)
        bq_pill = pygame.Surface((bq_w, bq_h), pygame.SRCALPHA)
        bq_pill.fill((*COL_BAROQUE_BG, 220))
        screen.blit(bq_pill, (cx, anchor_y + int(3 * scale)))
        screen.blit(bq_surf, (cx + int(4 * scale),
                               anchor_y + (row_h - bq_surf.get_height()) // 2))


def _compute_active_slots(slots: dict) -> set[str]:
    """
    For each team, determine which slot is the active (on-screen) character.
    If C2 is in an assist-off state, C1 is active (and vice versa).
    If both are on-screen (or neither), both are treated as active.
    """
    active = set()
    for team, (c1, c2) in (("P1", ("P1-C1", "P1-C2")), ("P2", ("P2-C1", "P2-C2"))):
        s1 = slots.get(c1)
        s2 = slots.get(c2)

        if s1 and not s2:
            active.add(c1)
        elif s2 and not s1:
            active.add(c2)
        elif s1 and s2:
            c1_off = int(s1.get("mv_id_display") or 0) in ASSIST_OFF_IDS
            c2_off = int(s2.get("mv_id_display") or 0) in ASSIST_OFF_IDS
            if c2_off and not c1_off:
                active.add(c1)   # C1 is on screen
            elif c1_off and not c2_off:
                active.add(c2)   # C2 is on screen (unusual but possible)
            else:
                active.add(c1)   # both or neither — highlight C1 by default
                active.add(c2)

    return active


def draw_overlay(screen: pygame.Surface,
                 font: pygame.font.Font,
                 font_sm: pygame.font.Font,
                 slots: dict, scale: float) -> None:
    """Clear to colorkey then draw each slot row at its bar-aligned position."""
    screen.fill(COLORKEY)

    row_h   = max(14, int(BASE_ROW_H * scale))
    active  = _compute_active_slots(slots)

    for slot_label, (base_x, base_y) in SLOT_LAYOUT.items():
        snap = slots.get(slot_label)
        if not snap:
            continue

        ax = int(base_x * scale)
        ay = int(base_y * scale)
        is_active_char = slot_label in active

        _draw_slot_row(screen, font, font_sm, slot_label, snap,
                       ax, ay, row_h, scale, is_active_char)


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
    scale   = 1.0
    font    = make_font(BASE_FONT_SIZE, bold=True)
    font_sm = make_font(int(BASE_FONT_SIZE * 0.78), bold=False)

    clock = pygame.time.Clock()
    running = True

    while running:
        w, h = sync_overlay_to_dolphin(dolphin_hwnd, overlay_hwnd)

        if w > 0 and h > 0 and (w, h) != (cur_w, cur_h):
            cur_w, cur_h = w, h
            screen  = pygame.display.set_mode((w, h), pygame.SRCALPHA)
            scale   = min(w / BASE_W, h / BASE_H)
            font    = make_font(int(BASE_FONT_SIZE * scale), bold=True)
            font_sm = make_font(int(BASE_FONT_SIZE * scale * 0.78), bold=False)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False

        slots = read_slot_data()
        draw_overlay(screen, font, font_sm, slots, scale)
        pygame.display.flip()
        clock.tick(TARGET_FPS)

    pygame.quit()


if __name__ == "__main__":
    main()