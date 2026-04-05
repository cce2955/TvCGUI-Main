#!/usr/bin/env python3
"""
hud_overlay.py
--------------
Transparent Dolphin-parented overlay that displays live per-slot HUD data:
    HP | Meter | MoveID | Baroque | Frame Advantage

Frame advantage is tracked locally in this process for all four slots.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from typing import Optional
import math
import pygame
import win32con
import win32gui

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_FILE = "hud_overlay_data.json"
TARGET_FPS = 60
COLORKEY = (0, 0, 0)

BASE_W          = 1280
BASE_H          = 720
BASE_FONT_SIZE  = 14
BASE_ROW_H      = 22
BG_ALPHA        = 180

SLOT_LAYOUT = {
    "P1-C1": ("left",  28, 178),
    "P1-C2": ("left",  28, 207),
    "P2-C1": ("right", 28, 178),
    "P2-C2": ("right", 28, 207),
}
SLOT_COLORS = {
    "P1-C1": (255, 100, 100),
    "P1-C2": (255, 150, 120),
    "P2-C1": ( 90, 160, 255),
    "P2-C2": (120, 190, 255),
}

COL_TEXT         = (220, 220, 220)
COL_TEXT_DIM     = (120, 120, 120)
COL_DEAD         = ( 90,  90,  90)
COL_HP_HIGH      = ( 60, 200,  90)
COL_HP_LOW       = (220,  60,  60)
COL_HP_DEAD      = ( 70,  70,  70)
COL_HP_BG        = ( 40,  40,  40)
COL_METER_FULL   = ( 70, 140, 255)
COL_METER_EMPTY  = ( 35,  35,  50)
COL_BAROQUE_ON   = (255, 200,  60)
COL_BAROQUE_BG   = (100,  60,   8)

BG_ALPHA         = 200

ASSIST_STANDBY_IDS = {430, 432, 433}
ASSIST_ATTACK_IDS  = {420, 426, 427, 428}
ASSIST_OFF_IDS     = ASSIST_STANDBY_IDS | ASSIST_ATTACK_IDS

PASSIVE_LABELS = {
    "idle", "crouched", "couching", "standing", "jump", "jump forward",
    "jump back", "landing", "rising", "assist standby", "assist leave",
    "assist attack", "assist taunt", "tag out", "tag in",
}

# Move IDs that mean the character is in hitstun / blockstun
REACTION_IDS = {48, 49, 50, 51, 52, 64, 65, 66, 73,75, 79, 80, 81, 
                82, 83, 89, 90, 92, 95, 96, 98,
                102,105,106,113, 114,115,116, 117, 118 ,119, 160}

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_display_slots: dict = {}
_frame: int = 0

# Per cross-team pair state machine: (atk_slot, vic_slot) -> state dict
_adv_pairs: dict = {}

# ---------------------------------------------------------------------------
# ADV helpers
# ---------------------------------------------------------------------------

def _mv(slot_label: str):
    v = _display_slots.get(slot_label, {}).get("mv_id_display")
    return int(v) if v is not None else None

def _is_attacking(mv):
    return mv is not None and 256 <= mv <= 512

def _is_stuck(mv):
    return mv is not None and mv in REACTION_IDS

def _is_actionable(mv):
    return mv is not None and not _is_attacking(mv) and not _is_stuck(mv)

def _get_active_slot(team: str) -> str | None:
    c1, c2 = f"{team}-C1", f"{team}-C2"
    s1, s2 = _display_slots.get(c1), _display_slots.get(c2)
    if not s1 and not s2:
        return None
    if s1 and not s2:
        return c1
    if s2 and not s1:
        return c2
    mv1 = int(s1.get("mv_id_display") or 0)
    mv2 = int(s2.get("mv_id_display") or 0)
    if mv2 in ASSIST_OFF_IDS and mv1 not in ASSIST_OFF_IDS:
        return c1
    if mv1 in ASSIST_OFF_IDS and mv2 not in ASSIST_OFF_IDS:
        return c2
    return c1

def _push_adv(slot_label: str, value: int) -> None:
    sa = _get_slot_anim(slot_label)
    events = sa["adv_events"]
    events.insert(0, {"value": value, "life": 1.0, "x_offset": 20})
    if len(events) > 3:
        events.pop()

def _update_adv() -> None:
    """Called once per frame. Tracks frame advantage for the active pair."""
    p1_slot = _get_active_slot("P1")
    p2_slot = _get_active_slot("P2")
    if not p1_slot or not p2_slot:
        return

    # Check both directions (P1 attacks P2, and P2 attacks P1)
    for a_slot, v_slot in ((p1_slot, p2_slot), (p2_slot, p1_slot)):
        key = (a_slot, v_slot)
        st = _adv_pairs.setdefault(key, {
            "state": 0,
            "first_end": None,
            "first_slot": None,
            "prev_a": None,
            "prev_v": None,
        })

        a_mv   = _mv(a_slot)
        v_mv   = _mv(v_slot)
        prev_a = st["prev_a"]
        prev_v = st["prev_v"]
        st["prev_a"] = a_mv
        st["prev_v"] = v_mv

        if st["state"] == 0:
            if _is_attacking(a_mv) and _is_stuck(v_mv):
                st["state"]      = 1
                st["first_end"]  = None
                st["first_slot"] = None

        elif st["state"] == 1:
            # New hit or move-id change — reset timer, stay tracking
            if _is_attacking(a_mv) and _is_stuck(v_mv) and (
                not _is_attacking(prev_a) or a_mv != prev_a
            ):
                st["first_end"]  = None
                st["first_slot"] = None
                if not _is_attacking(prev_a):
                    st["state"] = 0
                continue

            a_act = _is_actionable(a_mv)
            v_act = _is_actionable(v_mv)

            if a_act and v_act:
                _push_adv(a_slot,  0)
                _push_adv(v_slot,  0)
                st["state"] = 0
            elif a_act:
                st["state"]      = 2
                st["first_end"]  = _frame
                st["first_slot"] = "A"
            elif v_act:
                st["state"]      = 2
                st["first_end"]  = _frame
                st["first_slot"] = "V"

        elif st["state"] == 2:
            # Attacker hit again before adv resolved — discard stale timer, restart
            if _is_attacking(a_mv) and _is_stuck(v_mv):
                st["first_end"]  = None
                st["first_slot"] = None
                st["state"]      = 1 if _is_attacking(prev_a) else 0
                continue

            # Attacker went idle for a frame but victim still stuck — they're in a
            # blockstring gap. Don't resolve yet; if they start attacking again,
            # state 1 above will catch it. If victim recovers first we resolve below.
            if st["first_slot"] == "A" and _is_stuck(v_mv) and not _is_attacking(a_mv):
                # attacker is in gap — wait, don't commit yet
                continue

            if st["first_slot"] == "A" and _is_actionable(v_mv):
                diff = _frame - st["first_end"]
                _push_adv(a_slot,  diff)
                _push_adv(v_slot, -diff)
                st["state"] = 0

            elif st["first_slot"] == "V" and _is_actionable(a_mv):
                diff = _frame - st["first_end"]
                _push_adv(a_slot, -diff)
                _push_adv(v_slot,  diff)
                st["state"] = 0

# ---------------------------------------------------------------------------
# Animation system
# ---------------------------------------------------------------------------

ANIM_SPEED = 10.0
FADE_SPEED = 6.0
PIP_SPEED  = 12.0
_anim_state = {
    "overlay_alpha": 0.0,
    "slots": {},
}

def _approach(current: float, target: float, speed: float, dt: float) -> float:
    if current < target:
        return min(current + speed * dt, target)
    else:
        return max(current - speed * dt, target)

def _get_slot_anim(slot_label: str):
    return _anim_state["slots"].setdefault(slot_label, {
        "alpha": 0.0,
        "meter_display": 0.0,
        "pip_values": [0.0] * 5,
        "present": False,
        "baroque_last_pct": 0.0,
        "baroque_display_pct": 0.0,
        "baroque_freeze_timer": 0,
        "baroque_prev_ready": False,
        "prev_hp": None,
        "last_hit_damage": 0,
        "damage_timer": 0,
        "damage_events": [],
        "adv_events": [],
        "prev_meter": None,
        "meter_events": [],
    })

# ---------------------------------------------------------------------------
# DPI / Win32
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

def find_dolphin_hwnd() -> Optional[int]:
    candidates = []
    def score_title(t: str) -> int:
        tl = t.lower()
        if "dolphin" not in tl:
            return -10_000
        s = 0
        if "|" in t:
            s += 50 + min(30, t.count("|") * 5)
        for tok in ("jit", "jit64", "opengl", "vulkan", "d3d", "direct3d", "hle"):
            if tok in tl: s += 20
        if "(" in t and ")" in t: s += 30
        for bad in ("memory", "watch", "log", "breakpoint", "register", "disassembly", "config", "settings"):
            if bad in tl: s -= 25
        if t.count("|") >= 3: s += 20
        return s
    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd): return
        title = win32gui.GetWindowText(hwnd) or ""
        if not title or "dolphin" not in title.lower(): return
        candidates.append((score_title(title), hwnd, title))
    win32gui.EnumWindows(cb, None)
    if not candidates: return None
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
    win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
        win32con.SWP_FRAMECHANGED | win32con.SWP_NOMOVE |
        win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)

def sync_overlay_to_dolphin(dolphin_hwnd: int, overlay_hwnd: int):
    x, y, w, h = get_client_screen_rect(dolphin_hwnd)
    win32gui.SetWindowPos(overlay_hwnd, win32con.HWND_NOTOPMOST, x, y, w, h, win32con.SWP_NOACTIVATE)
    return w, h

# ---------------------------------------------------------------------------
# Data reader
# ---------------------------------------------------------------------------

_last_data_mtime: float = 0.0
_cached_slots: dict = {}

def read_slot_data() -> dict:
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
# Drawing helpers
# ---------------------------------------------------------------------------

def make_font(size: int, bold: bool = True) -> pygame.font.Font:
    try:
        return pygame.font.SysFont("consolas", max(8, size), bold=bold)
    except Exception:
        return pygame.font.Font(None, max(8, size))

def _draw_hp_bar(screen, x, y, bar_w, bar_h, hp_cur, hp_max, is_dead):
    pygame.draw.rect(screen, COL_HP_BG, (x, y, bar_w, bar_h), border_radius=2)
    if hp_max and hp_max > 0:
        frac = max(0.0, min(1.0, hp_cur / hp_max))
        fill_w = max(1, int(bar_w * frac))
        bar_col = COL_HP_DEAD if is_dead else (COL_HP_LOW if frac <= 0.30 else COL_HP_HIGH)
        pygame.draw.rect(screen, bar_col, (x, y, fill_w, bar_h), border_radius=2)
        if not is_dead:
            flash = pygame.Surface((fill_w, bar_h), pygame.SRCALPHA)
            flash.fill((255, 255, 255, 18))
            screen.blit(flash, (x, y))

def _draw_meter_pips_animated(screen, x, y, pip_w, pip_h, pip_gap, slot_anim, is_dead):
    meter_val = slot_anim["meter_display"]
    for i in range(5):
        px = x + i * (pip_w + pip_gap)
        target = 1.0 if i < int(meter_val) else 0.0
        slot_anim["pip_values"][i] = _approach(slot_anim["pip_values"][i], target, PIP_SPEED, 1/60.0)
        v = slot_anim["pip_values"][i]
        scale = 0.75 + 0.25 * v
        w = int(pip_w * scale); h = int(pip_h * scale)
        ox = (pip_w - w) // 2; oy = (pip_h - h) // 2
        col = COL_METER_EMPTY if is_dead else tuple(
            int(COL_METER_EMPTY[c] + (COL_METER_FULL[c] - COL_METER_EMPTY[c]) * v) for c in range(3))
        pygame.draw.rect(screen, col, (px + ox, y + oy, w, h), border_radius=1)

def _draw_divider(screen, x, y, row_h, scale, alpha=220):
    w = max(2, int(2 * scale)); h = int(row_h * 0.7); dy = (row_h - h) // 2
    surf = pygame.Surface((w, h), pygame.SRCALPHA)
    surf.fill((220, 220, 220, alpha))
    screen.blit(surf, (x, y + dy))
    glow = pygame.Surface((w + 2, h + 4), pygame.SRCALPHA)
    glow.fill((180, 180, 180, 80))
    screen.blit(glow, (x - 1, y + dy - 2))

# ---------------------------------------------------------------------------
# Main row renderer
# ---------------------------------------------------------------------------

def _draw_slot_row(screen, font, font_sm, slot_label, snap,
                   anchor_x, anchor_y, row_h, scale,
                   is_active_char, slot_anim, overlay_alpha,
                   measure_only=False):

    slot_col = SLOT_COLORS.get(slot_label, (200, 200, 200))
    hp_cur   = snap.get("cur") or 0
    hp_max   = snap.get("max") or 1
    is_dead  = (hp_cur <= 0)

    # Damage tracking
    prev_hp = slot_anim["prev_hp"]
    if prev_hp is not None and hp_cur < prev_hp:
        dmg = prev_hp - hp_cur
        if dmg > 1:
            slot_anim["last_hit_damage"] = dmg
            slot_anim["damage_timer"] = 45
            events = slot_anim["damage_events"]
            events.insert(0, {"value": int(dmg), "life": 1.0, "x_offset": -20, "type": "self"})
            if len(events) > 5:
                events.pop()

            opponent = None
            if slot_label.startswith("P1"):
                opponent = _get_active_slot("P2")
            else:
                opponent = _get_active_slot("P1")

            if opponent:
                opp_anim = _get_slot_anim(opponent)
                opp_events = opp_anim["damage_events"]
                opp_events.insert(0, {
                    "value": int(dmg),
                    "life": 1.0,
                    "x_offset": 20,
                    "type": "opponent"
                })
                if len(opp_events) > 5:
                    opp_events.pop()
    slot_anim["prev_hp"] = hp_cur

    show_damage = (
        slot_anim["damage_timer"] > 0
        or any(ev["life"] > 0 for ev in slot_anim["damage_events"])
    )

    if slot_anim["damage_timer"] > 0:
        slot_anim["damage_timer"] -= 1

    # Colors
    if is_dead:
        name_col = text_col = COL_DEAD
    elif not is_active_char:
        name_col = text_col = COL_TEXT_DIM
    else:
        name_col = text_col = COL_TEXT

    # Layout
    pad      = int(6  * scale)
    acc_w    = int(3  * scale)
    badge_w  = int(26 * scale)
    name_gap = int(6  * scale)
    bar_w    = int(80 * scale)
    bar_h    = max(4, int(6 * scale))
    pip_w    = max(4, int(8 * scale))
    pip_h    = max(4, int(8 * scale))
    pip_gap  = max(1, int(2 * scale))
    meter_w  = 5 * pip_w + 4 * pip_gap
    sep      = int(10 * scale)

    char_name = snap.get("name") or "???"
    name_surf = font.render(char_name, True, name_col)
    name_w    = name_surf.get_width()

    hp_str   = f"{int(hp_cur)}/{int(hp_max)}"
    hp_num_s = font_sm.render(hp_str, True, text_col)

    meter_val = snap.get("meter")
    try:
        raw_meter = float(meter_val) if meter_val is not None else 0.0
        if   raw_meter < 10000: meter_f = 0
        elif raw_meter < 20000: meter_f = 1
        elif raw_meter < 30000: meter_f = 2
        elif raw_meter < 40000: meter_f = 3
        elif raw_meter < 50000: meter_f = 4
        else:                   meter_f = 5
        meter_str = f"{raw_meter:.0f}"
    except (TypeError, ValueError):
        meter_f = 0; meter_str = "---"

    # Meter gain tracking
    prev_meter = slot_anim["prev_meter"]
    cur_meter  = raw_meter if meter_val is not None else 0
    if prev_meter is not None and cur_meter > prev_meter:
        gain = int(cur_meter - prev_meter)
        if gain > 0:
            events = slot_anim["meter_events"]
            events.insert(0, {"value": gain, "life": 1.0, "x_offset": 20})
            if len(events) > 5:
                events.pop()
    slot_anim["prev_meter"] = cur_meter

    meter_num_s = font_sm.render(meter_str, True, text_col)

    move_id  = snap.get("mv_id_display")
    mv_label = (snap.get("mv_label") or "").strip()
    if not mv_label and move_id is not None:
        mv_label = f"0x{int(move_id):04X}"
    is_passive = mv_label.lower() in PASSIVE_LABELS
    move_col   = COL_TEXT_DIM if (is_passive or not is_active_char or is_dead) else COL_TEXT
    move_surf  = font_sm.render(mv_label or "---", True, move_col)

    # Baroque
    baroque_ready = snap.get("baroque_ready_local", False) and not is_dead
    baroque_pct   = snap.get("baroque_red_pct_max", 0.0)
    if baroque_ready:
        slot_anim["baroque_last_pct"] = baroque_pct
        display_pct = baroque_pct
        show_baroque_badge = True
        slot_anim["baroque_freeze_timer"] = 0
    else:
        if slot_anim["baroque_prev_ready"]:
            slot_anim["baroque_display_pct"] = slot_anim["baroque_last_pct"]
            slot_anim["baroque_freeze_timer"] = 120
        if slot_anim["baroque_freeze_timer"] > 0:
            display_pct = slot_anim["baroque_display_pct"]
            show_baroque_badge = True
            slot_anim["baroque_freeze_timer"] -= 1
        else:
            display_pct = 0.0
            show_baroque_badge = False
    slot_anim["baroque_prev_ready"] = baroque_ready

    baroque_badge_w = 0
    if show_baroque_badge:
        bq_surf_tmp = font_sm.render(f"BBQ {display_pct:.1f}%", True, (255, 255, 255))
        baroque_badge_w = bq_surf_tmp.get_width() + int(10 * scale)

    # Determine live popup events for width calculation
    adv_events_live    = [e for e in slot_anim["adv_events"]    if e["life"] > 0]
    damage_events_live = [e for e in slot_anim["damage_events"] if e["life"] > 0]
    meter_events_live  = [e for e in slot_anim["meter_events"]  if e["life"] > 0]
    has_popups = bool(adv_events_live or damage_events_live or meter_events_live or show_baroque_badge)
    popup_max_w = (font.size("-9999")[0] + int(12 * scale)) if has_popups else 0

    total_w = (
        acc_w + pad
        + badge_w + name_gap
        + name_w + sep
        + font_sm.size("HP")[0] + int(4*scale) + bar_w + int(4*scale) + hp_num_s.get_width() + sep
        + font_sm.size("M")[0]  + int(4*scale) + meter_w + int(4*scale) + meter_num_s.get_width() + sep
        + move_surf.get_width() + sep
        + (baroque_badge_w + sep if show_baroque_badge else 0)
        + popup_max_w
        + pad
    )

    MAX_ROW_W = int(600 * scale)
    total_w = min(total_w, MAX_ROW_W)

    if measure_only:
        return total_w

    # Background pill (neo-futurist metallic)
    pill = pygame.Surface((total_w, row_h), pygame.SRCALPHA)
    base_alpha = int(BG_ALPHA * slot_anim["alpha"] * overlay_alpha)

    for y in range(row_h):
        shade = int(18 + 18 * (1 - y / row_h))
        pygame.draw.line(pill, (shade, shade + 2, shade + 4, base_alpha), (0, y), (total_w, y))

    for y in range(0, row_h, 2):
        pygame.draw.line(pill, (255, 255, 255, 8), (0, y), (total_w, y))

    pygame.draw.line(pill, (200, 220, 255, int(30 * overlay_alpha)), (0, 0), (total_w, 0))
    pygame.draw.line(pill, (0, 0, 0, int(80 * overlay_alpha)), (0, row_h - 1), (total_w, row_h - 1))

    if is_active_char and not is_dead:
        pygame.draw.rect(pill, (*slot_col, 120), (0, 0, total_w, row_h), 1)

        speed  = 120 + (slot_anim["meter_display"] * 10)
        t      = (time.time() * speed) % total_w
        scan_x = int(t) if slot_label.startswith("P1") else int(total_w - t)

        scan = pygame.Surface((6, row_h), pygame.SRCALPHA)
        scan.fill((*slot_col, 40))
        pill.blit(scan, (scan_x, 0))

    screen.blit(pill, (anchor_x, anchor_y))

    if is_active_char and not is_dead:
        pygame.draw.rect(screen, (*slot_col, 160), (anchor_x, anchor_y, total_w, row_h), 1, border_radius=2)

    pygame.draw.rect(screen, slot_col, (anchor_x, anchor_y, acc_w, row_h), border_radius=1)

    badge_x   = anchor_x + acc_w + pad
    badge_col = slot_col if is_active_char and not is_dead else COL_DEAD
    pygame.draw.rect(screen, (*badge_col, 200),
                     (badge_x, anchor_y + int(3*scale), badge_w, row_h - int(6*scale)), border_radius=2)
    badge_label = "C1" if slot_label.endswith("C1") else "C2"
    bs = font_sm.render(badge_label, True, (240, 240, 240))
    screen.blit(bs, (badge_x + (badge_w - bs.get_width()) // 2,
                     anchor_y + (row_h - bs.get_height()) // 2))

    cx     = badge_x + badge_w + name_gap
    mid_y  = anchor_y + row_h // 2
    popup_y = anchor_y + row_h + int(4 * scale)

    damage_y = popup_y
    meter_y  = popup_y
    adv_y    = popup_y
    sm_top   = anchor_y + int(2 * scale)
    sm_bot   = anchor_y + row_h - int(2 * scale) - font_sm.get_height()

    screen.blit(name_surf, (cx, mid_y - font.get_height() // 2))
    cx += name_w + sep
    _draw_divider(screen, cx - sep // 2, anchor_y, row_h, scale)

    lbl = font_sm.render("HP", True, COL_TEXT_DIM)
    screen.blit(lbl, (cx, sm_top))
    _draw_hp_bar(screen, cx, mid_y - bar_h // 2, bar_w, bar_h, hp_cur, hp_max, is_dead)
    cx += bar_w + int(4 * scale)
    screen.blit(hp_num_s, (cx, sm_bot))
    cx += hp_num_s.get_width() + sep
    hp_anchor_x = cx - int(100 * scale)

    _draw_divider(screen, cx - sep // 2, anchor_y, row_h, scale)

    lbl = font_sm.render("M", True, COL_TEXT_DIM)
    screen.blit(lbl, (cx, sm_top))
    slot_anim["meter_display"] = _approach(slot_anim["meter_display"], meter_f, PIP_SPEED, 1/60.0)
    _draw_meter_pips_animated(screen, cx, mid_y - pip_h // 2, pip_w, pip_h, pip_gap, slot_anim, is_dead)
    cx += meter_w + int(4 * scale)
    screen.blit(meter_num_s, (cx, sm_bot))
    cx += meter_num_s.get_width() + sep
    meter_anchor_x = cx - int(60 * scale)

    _draw_divider(screen, cx - sep // 2, anchor_y, row_h, scale)

    screen.blit(move_surf, (cx, mid_y - move_surf.get_height() // 2))
    cx += move_surf.get_width() + sep
    _draw_divider(screen, cx - sep // 2, anchor_y, row_h, scale)

    # Damage popup
    if show_damage:
        dx  = hp_anchor_x
        gap = int(6 * scale)
        for i, ev in enumerate(slot_anim["damage_events"]):
            ev["life"] -= 0.010
            speed = 240 if abs(ev["x_offset"]) > 5 else 80
            ev["x_offset"] = _approach(ev["x_offset"], 0, speed, 1/60.0)
            if ev["life"] <= 0:
                continue
            base_col = (80, 255, 120) if ev.get("type") == "opponent" else (255, 80, 80)
            if i > 0:
                base_col = tuple(int(c * 0.75) for c in base_col)
            alpha    = int(255 * ev["life"])
            dmg_font = font if i == 0 else font_sm
            dmg_surf = dmg_font.render(f"-{ev['value']}", True, base_col)
            dmg_surf.set_alpha(alpha)
            w = dmg_surf.get_width(); h = dmg_surf.get_height()
            pad_x = int(4 * scale); pad_y = int(2 * scale)
            bg = pygame.Surface((w + pad_x * 2, h + pad_y * 2), pygame.SRCALPHA)
            bg.fill((20, 0, 0, int(180 * ev["life"])))
            draw_x = dx + int(ev["x_offset"]) + (int(30 * scale) if ev.get("type") == "opponent" else 0)
            screen.blit(bg, (draw_x - pad_x, damage_y - pad_y))
            screen.blit(dmg_surf, (draw_x, damage_y))
            dx += w + gap

    # Meter gain popup
    meter_events = slot_anim["meter_events"]
    if meter_events:
        dx  = meter_anchor_x
        gap = int(6 * scale)
        for i, ev in enumerate(meter_events):
            ev["life"] -= 0.010
            ev["x_offset"] = _approach(ev["x_offset"], 0, 120, 1/60.0)
            if ev["life"] <= 0:
                continue
            base_col = (80, 160, 255) if i == 0 else (60, 120, 200)
            alpha    = int(255 * ev["life"])
            m_font   = font if i == 0 else font_sm
            surf     = m_font.render(f"+{ev['value']}", True, base_col)
            surf.set_alpha(alpha)
            w = surf.get_width(); h = surf.get_height()
            pad_x = int(4 * scale); pad_y = int(2 * scale)
            bg = pygame.Surface((w + pad_x * 2, h + pad_y * 2), pygame.SRCALPHA)
            bg.fill((0, 20, 50, int(180 * ev["life"])))
            draw_x = dx + int(ev["x_offset"])
            screen.blit(bg, (draw_x - pad_x, meter_y - pad_y))
            screen.blit(surf, (draw_x, meter_y))
            dx += w + gap
        slot_anim["meter_events"] = [e for e in meter_events if e["life"] > 0]

    # Baroque badge
    if show_baroque_badge:
        bq_text  = f"BBQ {display_pct:.1f}%"
        bq_base  = font_sm.render(bq_text, True, (255, 255, 255))
        rainbow  = pygame.Surface(bq_base.get_size(), pygame.SRCALPHA)
        t = time.time() * 0.4
        for x in range(bq_base.get_width()):
            phase = (x / bq_base.get_width() + t) % 1.0
            r = int(200 + 55 * math.sin(2 * math.pi * phase))
            g = int(160 + 55 * math.sin(2 * math.pi * (phase + 0.33)))
            pygame.draw.line(rainbow, (r, g, 255, 255), (x, 0), (x, bq_base.get_height()))
        bq_base.blit(rainbow, (0, 0), special_flags=pygame.BLEND_MULT)
        bq_surf = bq_base

        bq_w    = bq_surf.get_width() + int(8 * scale)
        bq_h    = row_h - int(6 * scale)
        bq_pill = pygame.Surface((bq_w, bq_h), pygame.SRCALPHA)
        bq_pill.fill((35, 30, 20, 220))
        screen.blit(bq_pill, (cx, anchor_y + int(3 * scale)))
        screen.blit(bq_surf, (cx + int(4 * scale), anchor_y + (row_h - bq_surf.get_height()) // 2))
        cx += bq_w + sep

    # Frame advantage popup
    adv_anchor_x = anchor_x + total_w - popup_max_w - int(6 * scale)
    adv_events   = slot_anim["adv_events"]
    if adv_events:
        dx  = adv_anchor_x
        gap = int(6 * scale)
        for i, ev in enumerate(adv_events):
            ev["life"] -= 0.010
            ev["x_offset"] = _approach(ev["x_offset"], 0, 120, 1/60.0)
            if ev["life"] <= 0:
                continue
            val      = ev["value"]
            base_col = (80, 255, 120) if val > 0 else ((255, 80, 80) if val < 0 else (200, 200, 200))
            txt      = f"+{val}" if val > 0 else str(val)
            alpha    = int(255 * ev["life"])
            adv_font = font if i == 0 else font_sm
            adv_surf = adv_font.render(txt, True, base_col)
            adv_surf.set_alpha(alpha)
            w = adv_surf.get_width(); h = adv_surf.get_height()
            pad_x = int(4 * scale); pad_y = int(2 * scale)
            bg_col = (0, 30, 0) if val > 0 else ((40, 0, 0) if val < 0 else (20, 20, 20))
            bg = pygame.Surface((w + pad_x * 2, h + pad_y * 2), pygame.SRCALPHA)
            bg.fill((*bg_col, int(180 * ev["life"])))
            draw_x = dx + int(ev["x_offset"])
            screen.blit(bg, (draw_x - pad_x, adv_y - pad_y))
            screen.blit(adv_surf, (draw_x, adv_y))
            dx += w + gap
        slot_anim["adv_events"] = [e for e in adv_events if e["life"] > 0]

    return total_w


def _compute_active_slots(slots: dict) -> set[str]:
    active = set()
    for team, (c1, c2) in (("P1", ("P1-C1", "P1-C2")), ("P2", ("P2-C1", "P2-C2"))):
        s1, s2 = slots.get(c1), slots.get(c2)
        if s1 and not s2:
            active.add(c1)
        elif s2 and not s1:
            active.add(c2)
        elif s1 and s2:
            c1_off = int(s1.get("mv_id_display") or 0) in ASSIST_OFF_IDS
            c2_off = int(s2.get("mv_id_display") or 0) in ASSIST_OFF_IDS
            if c2_off and not c1_off:
                active.add(c1)
            elif c1_off and not c2_off:
                active.add(c2)
            else:
                active.add(c1); active.add(c2)
    return active


def draw_overlay(screen, font, font_sm, slots, scale, dt) -> None:
    screen.fill(COLORKEY)
    _anim_state["overlay_alpha"] = _approach(_anim_state["overlay_alpha"], 1.0, FADE_SPEED, dt)
    overlay_alpha = _anim_state["overlay_alpha"]
    row_h   = max(14, int(BASE_ROW_H * scale))
    row_gap = int(14 * scale)
    active  = _compute_active_slots(slots)

    for slot_label, (side, base_x, base_y) in SLOT_LAYOUT.items():
        snap      = slots.get(slot_label)
        slot_anim = _get_slot_anim(slot_label)

        target_alpha = 1.0 if slot_anim["present"] else 0.0
        slot_anim["alpha"] = _approach(slot_anim["alpha"], target_alpha, FADE_SPEED, dt)

        if slot_anim["alpha"] <= 0.01 and not slot_anim["present"]:
            _display_slots.pop(slot_label, None)
            continue
        if slot_anim["alpha"] <= 0.01:
            continue
        if not snap:
            continue

        scaled_y = int(base_y * scale) + (row_gap if slot_label.endswith("C2") else 0)

        # First pass: measure width
        row_w = _draw_slot_row(screen, font, font_sm, slot_label, snap,
                               0, scaled_y, row_h, scale, slot_label in active,
                               slot_anim, overlay_alpha, measure_only=True)

        if side == "right":
            anchor_x = screen.get_width() - int(base_x * scale) - row_w
        else:
            anchor_x = int(base_x * scale)

        # Second pass: draw
        _draw_slot_row(screen, font, font_sm, slot_label, snap,
                       anchor_x, scaled_y, row_h, scale, slot_label in active,
                       slot_anim, overlay_alpha)

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    global _frame

    dolphin_hwnd = find_dolphin_hwnd()
    if not dolphin_hwnd:
        print("[hud_overlay] Dolphin window not found — exiting.")
        return

    pygame.init()
    screen = pygame.display.set_mode((BASE_W, BASE_H), pygame.SRCALPHA)
    pygame.display.set_caption("TvC HUD Overlay")

    overlay_hwnd = pygame.display.get_wm_info()["window"]
    apply_overlay_style(overlay_hwnd)
    win32gui.SetWindowLong(overlay_hwnd, win32con.GWL_HWNDPARENT, dolphin_hwnd)

    cur_w, cur_h = BASE_W, BASE_H
    scale   = 1.0
    font    = make_font(BASE_FONT_SIZE, bold=True)
    font_sm = make_font(int(BASE_FONT_SIZE * 0.78), bold=False)
    clock   = pygame.time.Clock()
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
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

        dt = clock.tick(TARGET_FPS) / 1000.0
        _frame += 1

        new_slots = read_slot_data()

        for slot_label in SLOT_LAYOUT.keys():
            _get_slot_anim(slot_label)["present"] = slot_label in new_slots

        for k, v in new_slots.items():
            if isinstance(v, dict):
                _display_slots[k] = v

        _update_adv()

        draw_overlay(screen, font, font_sm, _display_slots, scale, dt)
        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()