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

from tvcgui.core.paths import user_data_path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DATA_FILE = user_data_path("overlay", "hud_overlay_data.json")
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

# Overlay layout: compact is the default match presentation; detail preserves the legacy per-slot rows.
HUD_LAYOUT_MODE = "compact"

ASSIST_STANDBY_IDS = {430, 432, 433}
ASSIST_ATTACK_IDS  = {420, 426, 427, 428}
ASSIST_OFF_IDS     = ASSIST_STANDBY_IDS 

PASSIVE_LABELS = {
    "idle", "crouched", "couching", "standing", "jump", "jump forward",
    "jump back", "landing", "rising", "assist standby", "assist leave",
    "assist attack", "assist taunt", "tag out", "tag in",
}

# Move IDs that mean the character is in hitstun / blockstun
REACTION_IDS = {48, 49, 50, 51, 52, 64, 65, 66, 73,75, 79, 80, 81, 
                82, 83, 89, 90, 92, 95, 96, 98,
                102,105,106,113, 114,115,116, 117, 118 ,119, 160}
BAROQUE_CANCEL_IDS = {162, 163, 164}



# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------

_display_slots: dict = {}
_frame: int = 0

# Per cross-team pair state machine: (atk_slot, vic_slot) -> state dict
_adv_pairs: dict = {}

# Short-lived match widgets sourced from the same live HUD snapshots.
_interaction_ribbon = {
    "title": "",
    "detail": "",
    "color": (130, 175, 255),
    "life": 0.0,
}
_combo_ledgers: dict[str, dict] = {"P1": {}, "P2": {}}

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
    events.insert(0, {"value": value, "life": 1.0, "age": 0.0, "x_offset": 20})
    if len(events) > 3:
        events.pop()


def _snap_int(slot_label: str, key: str, default: int = 0) -> int:
    try:
        return int((_display_slots.get(slot_label) or {}).get(key) or default)
    except (TypeError, ValueError):
        return default


def _snap_float(slot_label: str, key: str, default: float = 0.0) -> float:
    try:
        return float((_display_slots.get(slot_label) or {}).get(key) or default)
    except (TypeError, ValueError):
        return default


def _snap_name(slot_label: str) -> str:
    name = str((_display_slots.get(slot_label) or {}).get("name") or slot_label or "---").strip()
    return name or slot_label


def _snap_move(slot_label: str) -> str:
    snap = _display_slots.get(slot_label) or {}
    label = str(snap.get("mv_label") or "").strip()
    if label and label.lower() not in PASSIVE_LABELS:
        return label
    try:
        move_id = int(snap.get("mv_id_display"))
        return f"0x{move_id:04X}"
    except (TypeError, ValueError):
        return "Action"


def _begin_adv_contact(st: dict, attacker_slot: str, victim_slot: str) -> None:
    st["victim_hp_start"] = _snap_int(victim_slot, "cur")
    st["attack_move"] = _snap_move(attacker_slot)
    st["attacker_name"] = _snap_name(attacker_slot)
    st["victim_name"] = _snap_name(victim_slot)


def _publish_interaction(attacker_slot: str, victim_slot: str, st: dict, advantage: int) -> None:
    start_hp = int(st.get("victim_hp_start") or _snap_int(victim_slot, "cur"))
    current_hp = _snap_int(victim_slot, "cur")
    damage = max(0, start_hp - current_hp)
    hit = damage > 0
    kind = "HIT" if hit else "BLOCK"
    move = str(st.get("attack_move") or _snap_move(attacker_slot))
    attacker = str(st.get("attacker_name") or _snap_name(attacker_slot))
    victim = str(st.get("victim_name") or _snap_name(victim_slot))
    detail = f"{victim}  •  {advantage:+d}"
    if hit:
        detail += f"  •  {damage:,} DMG"
    _interaction_ribbon.update({
        "title": f"{kind}  |  {attacker}  {move}",
        "detail": detail,
        "color": (255, 126, 126) if hit else (128, 180, 255),
        "life": 1.0,
    })


def _combo_register_damage(victim_slot: str, damage: int) -> None:
    """Accumulate a compact combo ledger from discrete HP-loss events."""
    if damage <= 0:
        return
    attacker_team = "P2" if str(victim_slot).startswith("P1") else "P1"
    attacker_slot = _get_active_slot(attacker_team)
    if not attacker_slot:
        return
    ledger = _combo_ledgers.setdefault(attacker_team, {})
    same_chain = (
        ledger.get("attacker_slot") == attacker_slot
        and ledger.get("victim_slot") == victim_slot
        and (_frame - int(ledger.get("last_hit_frame") or -9999)) <= 75
    )
    if not same_chain:
        ledger.clear()
        ledger.update({
            "attacker_slot": attacker_slot,
            "victim_slot": victim_slot,
            "attacker_name": _snap_name(attacker_slot),
            "victim_name": _snap_name(victim_slot),
            "hits": 0,
            "damage": 0,
            "meter_start": _snap_int(attacker_slot, "meter"),
            "baroque_start": _snap_float(attacker_slot, "baroque_red_pct_max"),
            "last_hit_frame": _frame,
            "life": 1.0,
        })
    ledger["hits"] = int(ledger.get("hits") or 0) + 1
    ledger["damage"] = int(ledger.get("damage") or 0) + int(damage)
    ledger["last_hit_frame"] = _frame
    ledger["life"] = 1.0


def _tick_combo_ledgers(dt: float) -> None:
    for team, ledger in _combo_ledgers.items():
        if not ledger:
            continue
        age = _frame - int(ledger.get("last_hit_frame") or _frame)
        if age > 75:
            ledger["life"] = max(0.0, float(ledger.get("life") or 0.0) - dt * 1.45)
        if float(ledger.get("life") or 0.0) <= 0.01:
            ledger.clear()

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
            "victim_hp_start": None,
            "attack_move": "",
            "attacker_name": "",
            "victim_name": "",
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
                _begin_adv_contact(st, a_slot, v_slot)

        elif st["state"] == 1:
            # New hit or move-id change — reset timer, stay tracking
            if _is_attacking(a_mv) and _is_stuck(v_mv) and (
                not _is_attacking(prev_a) or a_mv != prev_a
            ):
                st["first_end"]  = None
                st["first_slot"] = None
                _begin_adv_contact(st, a_slot, v_slot)
                if not _is_attacking(prev_a):
                    st["state"] = 0
                continue

            a_act = _is_actionable(a_mv)
            v_act = _is_actionable(v_mv)

            if a_act and v_act:
                _push_adv(a_slot,  0)
                _push_adv(v_slot,  0)
                _publish_interaction(a_slot, v_slot, st, 0)
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
            # state 1 above will catch it. If victim recovers first the module resolve below.
            if st["first_slot"] == "A" and _is_stuck(v_mv) and not _is_attacking(a_mv):
                # attacker is in gap — wait, don't commit yet
                continue

            if st["first_slot"] == "A" and _is_actionable(v_mv):
                diff = _frame - st["first_end"]
                _push_adv(a_slot,  diff)
                _push_adv(v_slot, -diff)
                _publish_interaction(a_slot, v_slot, st, diff)
                st["state"] = 0

            elif st["first_slot"] == "V" and _is_actionable(a_mv):
                diff = _frame - st["first_end"]
                _push_adv(a_slot, -diff)
                _push_adv(v_slot,  diff)
                _publish_interaction(a_slot, v_slot, st, -diff)
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
    "teams": {},
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
        "prev_move_label": "",
        "move_events": [],
        "move_scroll_px": 0.0,
        "prev_baroque_pct": None,
        "baroque_events": [],
        "hp_display_frac": None,
        "partner_hp_display_frac": None,
        "meter_display_value": None,
        "baroque_alpha": 0.0,
        "event_history": [],
        "prev_compact_move_key": "",
        "ko_alpha": 0.0,
        "ko_scale": 0.90,
    })


def _get_team_anim(team: str):
    return _anim_state["teams"].setdefault(team, {
        "alpha": 0.0,
        "slide_x": 0.0,
        "slide_y": -34.0,
        "present": False,
        "current_point_label": None,
        "swap_progress": 0.0,
        "move_history_signature": (),
        "move_history_prev": [],
        "move_history_slide": 0.0,
        "log_history_signature": (),
        "log_history_prev": [],
        "log_history_slide": 0.0,
        "tag_card": None,
    })


def _push_event_history(slot_anim: dict, label: str, value: str, color: tuple[int, int, int]) -> None:
    items = slot_anim["event_history"]
    items.insert(0, {"label": label, "value": value, "color": color, "life": 1.0})
    del items[6:]



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

def _draw_gradient_frame(screen, rect, top_color, pulse=1.0):
    x, y, w, h = rect
    if w <= 2 or h <= 2:
        return

    border = max(1, int(round(pulse)))
    frame = pygame.Surface((w, h), pygame.SRCALPHA)

    for yy in range(h):
        t = yy / max(1, h - 1)
        r = int(top_color[0] * (1.0 - t))
        g = int(top_color[1] * (1.0 - t))
        b = int(top_color[2] * (1.0 - t))
        a = int(170 + 55 * pulse)
        col = (r, g, b, a)

        if yy < border:
            pygame.draw.line(frame, col, (0, yy), (w - 1, yy))
        elif yy >= h - border:
            pygame.draw.line(frame, col, (0, yy), (w - 1, yy))
        else:
            pygame.draw.line(frame, col, (0, yy), (border - 1, yy))
            pygame.draw.line(frame, col, (w - border, yy), (w - 1, yy))

    glow = pygame.Surface((w + 6, h + 6), pygame.SRCALPHA)
    glow_col = (*top_color, int(45 + 45 * pulse))
    pygame.draw.rect(glow, glow_col, (3, 3, w, h), width=max(1, border), border_radius=3)

    screen.blit(glow, (x - 3, y - 3))
    screen.blit(frame, (x, y))

# ---------------------------------------------------------------------------
# Main row renderer
# ---------------------------------------------------------------------------

def _draw_slot_row(screen, font, font_sm, slot_label, snap,
                   anchor_x, anchor_y, row_h, scale,
                   is_active_char, slot_anim, overlay_alpha,
                   dt,
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

    # Baroque gain/loss tracking
    prev_baroque_pct = slot_anim.get("prev_baroque_pct")
    cur_baroque_pct  = float(snap.get("baroque_red_pct_max") or 0.0)
    if prev_baroque_pct is not None and abs(cur_baroque_pct - prev_baroque_pct) >= 0.01:
        delta = cur_baroque_pct - prev_baroque_pct
        events = slot_anim["baroque_events"]
        events.insert(0, {"value": delta, "life": 1.0, "x_offset": 20})
        _push_event_history(slot_anim, "BBQ", f"{delta:+.0f}%", (255, 180, 92) if delta < 0 else (172, 112, 255))
        if len(events) > 5:
            events.pop()
    slot_anim["prev_baroque_pct"] = cur_baroque_pct

    meter_num_s = font_sm.render(meter_str, True, text_col)

    move_id  = snap.get("mv_id_display")
    mv_label = (snap.get("mv_label") or "").strip()
    if not mv_label and move_id is not None:
        mv_label = f"0x{int(move_id):04X}"

    mission_suffix = ""
    if snap.get("mission_wrong_ready"):
        mission_suffix = " | WRONG READY"
    elif snap.get("mission_varing"):
        mission_suffix = " | VAR"
    elif snap.get("mission_var_ready"):
        mission_suffix = " | AIR OK"
    elif snap.get("mission_target"):
        mission_suffix = " | TARGET"

    mv_label_display = f"{mv_label}{mission_suffix}" if mv_label else mission_suffix.strip(" |")
    is_passive = mv_label.lower() in PASSIVE_LABELS
    is_baroque = (move_id is not None and int(move_id) in BAROQUE_CANCEL_IDS)
    move_col   = COL_TEXT_DIM if ((is_passive and not is_baroque) or not is_active_char or is_dead) else COL_TEXT
    move_surf  = font_sm.render(mv_label_display or "---", True, move_col)

    # Move history tracking
    prev_move_label = slot_anim.get("prev_move_label", "")
    if (
        mv_label
        and mv_label != "---"
        and mv_label != prev_move_label
        and ((not is_passive) or is_baroque)
        and not is_dead
    ):
        move_events = slot_anim["move_events"]
        move_events.insert(0, {"text": mv_label, "life": 1.0, "frame": _frame})
        if len(move_events) > 6:
            move_events.pop()
        slot_anim["move_scroll_px"] = max(int(28 * scale), 14)
    slot_anim["prev_move_label"] = mv_label

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
    hp_bar_x = cx
    _draw_hp_bar(screen, hp_bar_x, mid_y - bar_h // 2, bar_w, bar_h, hp_cur, hp_max, is_dead)
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

    move_anchor_x = cx
    screen.blit(move_surf, (move_anchor_x, mid_y - move_surf.get_height() // 2))
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

    # Baroque gain/loss popup
    baroque_events = slot_anim["baroque_events"]
    if baroque_events:
        dx  = meter_anchor_x + int(90 * scale)
        gap = int(6 * scale)
        for i, ev in enumerate(baroque_events):
            ev["life"] -= 0.010
            ev["x_offset"] = _approach(ev["x_offset"], 0, 120, 1/60.0)
            if ev["life"] <= 0:
                continue
            val = ev["value"]
            txt = f"{val:+.1f}%"
            alpha = int(255 * ev["life"])
            b_font = font if i == 0 else font_sm

            base = b_font.render(txt, True, (255, 255, 255))
            rainbow = pygame.Surface(base.get_size(), pygame.SRCALPHA)
            t = time.time() * 0.4
            for x in range(base.get_width()):
                phase = (x / max(1, base.get_width()) + t) % 1.0
                r = int(200 + 55 * math.sin(2 * math.pi * phase))
                g = int(160 + 55 * math.sin(2 * math.pi * (phase + 0.33)))
                pygame.draw.line(rainbow, (r, g, 255, 255), (x, 0), (x, base.get_height()))
            base.blit(rainbow, (0, 0), special_flags=pygame.BLEND_MULT)
            surf = base
            surf.set_alpha(alpha)

            w = surf.get_width(); h = surf.get_height()
            pad_x = int(4 * scale); pad_y = int(2 * scale)
            bg = pygame.Surface((w + pad_x * 2, h + pad_y * 2), pygame.SRCALPHA)
            bg.fill((45, 25, 0, int(180 * ev["life"])))
            draw_x = dx + int(ev["x_offset"])
            screen.blit(bg, (draw_x - pad_x, meter_y - pad_y))
            screen.blit(surf, (draw_x, meter_y))
            dx += w + gap
        slot_anim["baroque_events"] = [e for e in baroque_events if e["life"] > 0]

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

    # One-line move history under this slot's move field
    move_events = slot_anim["move_events"]
    if move_events:
        move_list_y = anchor_y + row_h + int(18 * scale)

        # newest stays pinned while it is still the current live move
        newest_is_live = (
            not is_passive
            and mv_label
            and mv_label != "---"
            and len(move_events) > 0
            and move_events[0]["text"] == mv_label
        )

        # scroll animation for new inserts
        slot_anim["move_scroll_px"] = _approach(
            float(slot_anim.get("move_scroll_px", 0.0)),
            0.0,
            180.0,
            dt
        )
        scroll_px = int(slot_anim["move_scroll_px"])

        # display oldest -> newest
        display_items = list(reversed(move_events))

        parts = []
        newest_rect_index = None

        for idx, ev in enumerate(display_items):
            recency_from_newest = len(display_items) - 1 - idx
            ev_is_baroque = ev["text"].lower() == "baroque cancel"

            if ev_is_baroque:
                phase = (time.time() * 0.8) % 1.0
                color = (
                    int(200 + 55 * math.sin(2 * math.pi * phase)),
                    int(160 + 55 * math.sin(2 * math.pi * (phase + 0.33))),
                    255,
                )
            elif recency_from_newest == 0:
                color = (80, 255, 120)    # newest = green
            elif recency_from_newest == 1:
                color = (80, 160, 255)    # mid = blue
            elif recency_from_newest == 2:
                color = (255, 220, 90)    # old = yellow
            else:
                color = COL_TEXT          # 4th/5th neutral

            parts.append({
                "text": ev["text"],
                "color": color,
                "life": ev["life"],
                "is_newest": (recency_from_newest == 0),
            })
            if recency_from_newest == 0:
                newest_rect_index = len(parts) - 1

            if idx < len(display_items) - 1:
                parts.append({
                    "text": " > ",
                    "color": COL_TEXT,
                    "life": ev["life"],
                    "is_newest": False,
                })

        line_life = min(ev["life"] for ev in move_events)
        rendered_parts = []
        total_line_w = 0
        max_line_h = 0

        for part in parts:
            surf = font_sm.render(part["text"], True, part["color"])
            rendered_parts.append((surf, part["is_newest"]))
            total_line_w += surf.get_width()
            max_line_h = max(max_line_h, surf.get_height())

        move_list_x = badge_x - int(2 * scale) - scroll_px

        pad_x = int(5 * scale)
        pad_y = int(2 * scale)
        bg = pygame.Surface((total_line_w + pad_x * 2, max_line_h + pad_y * 2), pygame.SRCALPHA)
        bg.fill((12, 12, 12, 185))
        screen.blit(bg, (move_list_x - pad_x, move_list_y - pad_y))
        dx = move_list_x
        newest_rect = None
        for surf, is_newest_part in rendered_parts:
            screen.blit(surf, (dx, move_list_y))
            if is_newest_part:
                newest_rect = (
                    dx - int(3 * scale),
                    move_list_y - int(2 * scale),
                    surf.get_width() + int(6 * scale),
                    surf.get_height() + int(4 * scale),
                )
            dx += surf.get_width()

        # pulsing gradient frame around newest move only
        if newest_rect is not None:
            newest_text = move_events[0]["text"].lower() if move_events else ""
            if newest_text == "baroque cancel":
                phase = (time.time() * 0.8) % 1.0
                frame_col = (
                    int(200 + 55 * math.sin(2 * math.pi * phase)),
                    int(160 + 55 * math.sin(2 * math.pi * (phase + 0.33))),
                    255,
                )
            else:
                frame_col = (80, 255, 120)
            pulse = 0.85 + 0.35 * (0.5 + 0.5 * math.sin(time.time() * 6.0))
            _draw_gradient_frame(screen, newest_rect, frame_col, pulse=pulse)

        

        # fade only older entries; keep current active move pinned
        for i, ev in enumerate(move_events):
            if i == 0 and newest_is_live:
                ev["life"] = 1.0
            else:
                ev["life"] -= 0.006

        slot_anim["move_events"] = [e for e in move_events if e["life"] > 0]

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


def _draw_overlay_detail(screen, font, font_sm, slots, scale, dt) -> None:
    _anim_state["overlay_alpha"] = _approach(_anim_state["overlay_alpha"], 1.0, FADE_SPEED, dt)
    overlay_alpha = _anim_state["overlay_alpha"]
    row_h   = max(14, int(BASE_ROW_H * scale))
    row_gap = int(25 * scale)
    active  = _compute_active_slots(slots)
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
                               slot_anim, overlay_alpha, dt,
                               measure_only=True)

        if side == "right":
            anchor_x = screen.get_width() - int(base_x * scale) - row_w
        else:
            anchor_x = int(base_x * scale)

        # Second pass: draw
        _draw_slot_row(screen, font, font_sm, slot_label, snap,
                       anchor_x, scaled_y, row_h, scale, slot_label in active,
                       slot_anim, overlay_alpha, dt)


# ---------------------------------------------------------------------------
# Compact team overlay
# ---------------------------------------------------------------------------

def _compact_meter_level(meter_value) -> int:
    try:
        meter = float(meter_value or 0.0)
    except (TypeError, ValueError):
        return 0
    return max(0, min(5, int(meter // 10000.0)))


def _compact_meter_text(meter_value) -> str:
    try:
        meter_i = max(0, int(meter_value or 0))
    except (TypeError, ValueError):
        meter_i = 0
    return f"{meter_i}/50000"


def _lerp_color(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, float(t)))
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _compact_meter_color(meter_value) -> tuple[int, int, int]:
    try:
        meter = max(0.0, min(50000.0, float(meter_value or 0.0)))
    except (TypeError, ValueError):
        meter = 0.0
    stops = [
        (0.0, (82, 156, 255)),
        (10000.0, (82, 156, 255)),
        (20000.0, (72, 194, 255)),
        (30000.0, (72, 224, 164)),
        (40000.0, (242, 198, 88)),
        (50000.0, (255, 116, 116)),
    ]
    for (m1, c1), (m2, c2) in zip(stops, stops[1:]):
        if meter <= m2:
            span = max(1.0, m2 - m1)
            return _lerp_color(c1, c2, (meter - m1) / span)
    return stops[-1][1]


def _compact_hp_text(cur, maximum) -> str:
    try:
        cur_i = max(0, int(cur or 0))
        max_i = max(1, int(maximum or 1))
    except (TypeError, ValueError):
        return "--"

    def _short(value: int) -> str:
        if value >= 10000:
            whole = value / 1000.0
            return f"{whole:.1f}K" if value % 1000 else f"{int(whole)}K"
        return str(value)

    return f"{_short(cur_i)}/{_short(max_i)}"


def _compact_trim(text: str, max_chars: int) -> str:
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max(1, max_chars - 1)].rstrip() + "…"


def _compact_move_label(snap: dict) -> str:
    move_id = snap.get("mv_id_display")
    label = (snap.get("mv_label") or "").strip()
    if not label and move_id is not None:
        label = f"0x{int(move_id):04X}"
    if not label:
        return ""

    lowered = label.lower()
    is_baroque = move_id is not None and int(move_id) in BAROQUE_CANCEL_IDS
    if lowered in PASSIVE_LABELS and not is_baroque:
        return ""
    return label


def _compact_partner_state(snap: dict) -> str:
    current = int(snap.get("mv_id_display") or 0)
    label = (snap.get("mv_label") or "").lower()
    hp = int(snap.get("cur") or 0)
    if hp <= 0:
        return "KO"
    if current in ASSIST_ATTACK_IDS or "assist attack" in label:
        return "ACTIVE"
    if "assist leave" in label or "tag out" in label:
        return "RETURN"
    if current in ASSIST_STANDBY_IDS or "standby" in label:
        return "READY"
    return "READY"


def _compact_track_slot(slot_label: str, snap: dict) -> None:
    """Update compact-overlay event state from the latest slot snapshot."""
    slot_anim = _get_slot_anim(slot_label)
    try:
        hp_cur = int(snap.get("cur") or 0)
    except (TypeError, ValueError):
        hp_cur = 0

    prev_hp = slot_anim.get("prev_hp")
    if prev_hp is not None and hp_cur != prev_hp:
        hp_delta = hp_cur - prev_hp
        if abs(hp_delta) > 1:
            events = slot_anim["damage_events"]
            if hp_delta < 0:
                damage = -hp_delta
                events.insert(0, {"value": damage, "life": 1.0, "age": 0.0, "x_offset": 0, "type": "self"})
                _push_event_history(slot_anim, "DMG IN", _compact_short_number(damage), (255, 110, 110))
                _combo_register_damage(slot_label, damage)
                opponent = _get_active_slot("P2" if slot_label.startswith("P1") else "P1")
                if opponent:
                    opp_anim = _get_slot_anim(opponent)
                    other = opp_anim["damage_events"]
                    other.insert(0, {"value": damage, "life": 1.0, "age": 0.0, "x_offset": 0, "type": "opponent"})
                    _push_event_history(opp_anim, "DMG OUT", _compact_short_number(damage), (255, 110, 110))
                    del other[3:]
            else:
                events.insert(0, {"value": hp_delta, "life": 1.0, "age": 0.0, "x_offset": 0, "type": "heal"})
                _push_event_history(slot_anim, "HP +", _compact_short_number(hp_delta), (92, 232, 146))
            del events[3:]
    slot_anim["prev_hp"] = hp_cur

    try:
        meter_cur = int(snap.get("meter") or 0)
    except (TypeError, ValueError):
        meter_cur = 0
    prev_meter = slot_anim.get("prev_meter")
    if prev_meter is not None and meter_cur != prev_meter:
        meter_delta = meter_cur - prev_meter
        if abs(meter_delta) > 10:
            meter_events = slot_anim["meter_events"]
            meter_events.insert(0, {
                "value": abs(meter_delta),
                "direction": "gain" if meter_delta > 0 else "loss",
                "life": 1.0,
                "age": 0.0,
                "x_offset": 0,
            })
            _push_event_history(slot_anim, "MTR", f"{'+' if meter_delta > 0 else '-'}{_compact_short_number(abs(meter_delta))}", (96, 182, 255) if meter_delta > 0 else (255, 164, 92))
            del meter_events[3:]
    slot_anim["prev_meter"] = meter_cur

    move_id = snap.get("mv_id_display")
    move_label = _compact_move_label(snap)
    try:
        move_id_key = int(move_id) if move_id is not None else -1
    except (TypeError, ValueError):
        move_id_key = -1
    move_key = f"{move_id_key}:{move_label.lower()}" if move_label else ""
    previous_key = str(slot_anim.get("prev_compact_move_key") or "")
    if move_key and move_key != previous_key:
        events = slot_anim["move_events"]
        events.insert(0, {"text": move_label, "life": 1.0, "frame": _frame})
        del events[5:]
    slot_anim["prev_compact_move_key"] = move_key

    if slot_anim.get("damage_timer", 0) > 0:
        slot_anim["damage_timer"] -= 1


def _draw_compact_meter(screen, x: int, y: int, width: int, meter_value_visual, scale: float, is_dead: bool) -> int:
    level = _compact_meter_level(meter_value_visual)
    height = max(8, int(10 * scale))
    rect = pygame.Rect(x, y, max(30, width), height)
    radius = max(2, int(3 * scale))
    pygame.draw.rect(screen, (30, 36, 46), rect, border_radius=radius)
    pygame.draw.rect(screen, (96, 118, 150), rect, 1, border_radius=radius)

    inner = rect.inflate(-2, -2)
    gap = max(1, int(2 * scale))
    cell_w = max(3, (inner.width - gap * 4) // 5)
    pulse = 0.86 + 0.14 * ((math.sin(time.time() * 1.9) + 1.0) * 0.5)
    current_color = _compact_meter_color(meter_value_visual)
    for index in range(5):
        cell_x = inner.x + index * (cell_w + gap)
        cell = pygame.Rect(cell_x, inner.y, cell_w, inner.height)
        pygame.draw.rect(screen, (42, 50, 64), cell, border_radius=max(1, radius - 1))
        if index < level:
            base = current_color
            if index == level - 1:
                base = tuple(min(255, int(component * pulse)) for component in base)
            pygame.draw.rect(screen, base, cell, border_radius=max(1, radius - 1))
            pygame.draw.line(screen, (242, 248, 255), (cell.x + 1, cell.y + 1), (cell.right - 2, cell.y + 1), 1)
    return rect.width


def _draw_compact_health(screen, x: int, y: int, width: int, height: int, cur, maximum, is_dead: bool, display_fraction=None) -> None:
    rect = pygame.Rect(x, y, width, height)
    pygame.draw.rect(screen, (24, 29, 38), rect, border_radius=max(2, height // 2))
    inner = rect.inflate(-2, -2)
    try:
        target_fraction = max(0.0, min(1.0, float(cur or 0) / max(1.0, float(maximum or 1))))
    except (TypeError, ValueError):
        target_fraction = 0.0
    fraction = target_fraction if display_fraction is None else max(0.0, min(1.0, float(display_fraction)))
    if inner.width > 0 and inner.height > 0 and fraction > 0.0:
        fill = max(1, int(inner.width * fraction))
        color = COL_HP_DEAD if is_dead else (COL_HP_LOW if target_fraction <= 0.30 else COL_HP_HIGH)
        pygame.draw.rect(screen, color, (inner.x, inner.y, fill, inner.height), border_radius=max(1, inner.height // 2))
    for ratio in (0.25, 0.50, 0.75):
        tick_x = inner.x + int(inner.width * ratio)
        pygame.draw.line(screen, (10, 13, 18), (tick_x, inner.y + 1), (tick_x, inner.bottom - 2), 1)


def _compact_short_number(value) -> str:
    try:
        value_i = abs(int(value))
    except (TypeError, ValueError):
        return "0"
    if value_i >= 10000:
        scaled = value_i / 1000.0
        return f"{scaled:.1f}K" if value_i % 1000 else f"{int(scaled)}K"
    return str(value_i)


def _compact_action_chip(label: str) -> tuple[str, tuple[int, int, int], str]:
    raw = (label or "").strip()
    low = raw.lower()
    if not raw:
        return "", COL_TEXT_DIM, ""
    if "blockstun" in low:
        return "BLOCK", (255, 190, 88), "STATE"
    if "hitstun" in low:
        return "HITSTUN", (255, 110, 110), "STATE"
    if "knockdown" in low or "down" in low:
        return "DOWN", (255, 110, 110), "STATE"
    return raw.upper(), COL_TEXT, "MOVE"


def _compact_fit_text(font, text: str, max_width: int) -> str:
    text = (text or "").strip()
    if max_width <= 0 or not text:
        return ""
    if font.size(text)[0] <= max_width:
        return text
    suffix = "…"
    suffix_w = font.size(suffix)[0]
    low, high = 0, len(text)
    best = ""
    while low <= high:
        mid = (low + high) // 2
        candidate = text[:mid].rstrip() + suffix
        if font.size(candidate)[0] <= max_width:
            best = candidate
            low = mid + 1
        else:
            high = mid - 1
    return best or suffix


def _compact_rainbow_color(position: float, speed: float = 1.0) -> tuple[int, int, int]:
    phase = time.time() * (speed * 0.58) + position
    return (
        int(166 + 84 * math.sin(phase * 2.05)),
        int(166 + 84 * math.sin(phase * 2.05 + 2.10)),
        int(166 + 84 * math.sin(phase * 2.05 + 4.20)),
    )


def _render_compact_rainbow_text(font, text: str, phase_offset: float = 0.0) -> pygame.Surface:
    base = font.render(text, True, (255, 255, 255))
    gradient = pygame.Surface(base.get_size(), pygame.SRCALPHA)
    width = max(1, base.get_width())
    for px in range(width):
        color = _compact_rainbow_color((px / width) * 3.2 + phase_offset, 1.1)
        pygame.draw.line(gradient, (*color, 255), (px, 0), (px, max(0, base.get_height() - 1)))
    base.blit(gradient, (0, 0), special_flags=pygame.BLEND_MULT)
    return base


def _draw_compact_baroque_badge(screen, font_sm, rect: pygame.Rect, percent: float, scale: float, is_left: bool, alpha: float = 1.0) -> None:
    rainbow = _render_compact_rainbow_text(font_sm, f"BBQ {max(0.0, percent):.0f}%", 0.18)
    rainbow.set_alpha(max(0, min(255, int(255 * alpha))))
    text_x = rect.x + max(0, (rect.width - rainbow.get_width()) // 2)
    text_y = rect.y + max(0, (rect.height - rainbow.get_height()) // 2)
    screen.blit(rainbow, (text_x, text_y))


def _compact_chip_color(color: tuple[int, int, int], life: float) -> tuple[int, int, int]:
    weight = max(0.30, min(1.0, float(life)))
    return tuple(int(28 + (component - 28) * weight) for component in color)


def _compact_smoothstep(value: float) -> float:
    value = max(0.0, min(1.0, float(value)))
    return value * value * (3.0 - 2.0 * value)


def _compact_event_fade(event: dict | None) -> float:
    if not event:
        return 1.0
    age = float(event.get("age", 1.0))
    life = float(event.get("life", 1.0))
    fade_in = _compact_smoothstep(age / 0.15)
    fade_out = _compact_smoothstep(life / 0.28)
    return min(fade_in, fade_out)


def _draw_compact_stat_chip(
    screen,
    font_sm,
    x: int,
    y: int,
    label: str,
    value: str,
    color: tuple[int, int, int],
    scale: float,
    life: float = 1.0,
    event: dict | None = None,
) -> int:
    label_surface = font_sm.render(label, True, (142, 151, 169))
    value_surface = font_sm.render(value, True, _compact_chip_color(color, life))
    pad_x = max(4, int(5 * scale))
    seg_gap = max(2, int(2 * scale))
    label_w = label_surface.get_width() + pad_x * 2
    value_w = value_surface.get_width() + pad_x * 2
    height = max(int(15 * scale), label_surface.get_height() + int(4 * scale))
    width = label_w + seg_gap + value_w
    fade = _compact_event_fade(event)
    if fade <= 0.01:
        return width

    rise = int((1.0 - fade) * max(1, int(3 * scale)))
    chip = pygame.Surface((width, height), pygame.SRCALPHA)
    radius = max(2, int(2 * scale))
    border = _compact_chip_color(color, life)
    label_rect = pygame.Rect(0, 0, label_w, height)
    value_rect = pygame.Rect(label_w + seg_gap, 0, value_w, height)
    pygame.draw.rect(chip, (16, 21, 31, int(222 * fade)), chip.get_rect(), border_radius=radius)
    pygame.draw.rect(chip, (26, 31, 43, int(232 * fade)), label_rect, border_radius=radius)
    pygame.draw.rect(chip, (*border, int(66 * fade)), value_rect, border_radius=radius)
    pygame.draw.rect(chip, (*border, int(235 * fade)), chip.get_rect(), 1, border_radius=radius)
    if seg_gap > 0:
        pygame.draw.line(chip, (*border, int(160 * fade)), (label_rect.right + seg_gap // 2, 2), (label_rect.right + seg_gap // 2, height - 3), 1)
    label_surface.set_alpha(int(235 * fade))
    value_surface.set_alpha(int(255 * fade))
    chip.blit(label_surface, (label_rect.x + pad_x, chip.get_height() // 2 - label_surface.get_height() // 2))
    chip.blit(value_surface, (value_rect.x + pad_x, chip.get_height() // 2 - value_surface.get_height() // 2))
    screen.blit(chip, (x, y - rise))
    return width


def _compact_tick_event_queue(events: list[dict]) -> dict | None:
    if not events:
        return None
    event = events[0]
    event["age"] = float(event.get("age", 0.0)) + 0.014
    event["life"] = float(event.get("life", 0.0)) - 0.014
    active = event if event.get("life", 0.0) > 0.0 else None
    events[:] = [entry for entry in events if entry.get("life", 0.0) > 0.0]
    return active


def _compact_consume_panel_events(slot_anim: dict) -> tuple[dict | None, dict | None, dict | None, dict | None]:
    damage_event = _compact_tick_event_queue(slot_anim.get("damage_events", []))
    meter_event = _compact_tick_event_queue(slot_anim.get("meter_events", []))
    advantage_event = _compact_tick_event_queue(slot_anim.get("adv_events", []))
    baroque_event = _compact_tick_event_queue(slot_anim.get("baroque_events", []))
    return damage_event, meter_event, advantage_event, baroque_event


def _draw_compact_info_strip(
    screen,
    font_sm,
    slot_anim: dict,
    x: int,
    y: int,
    right: int,
    action_label: str,
    scale: float,
) -> None:
    damage_event, meter_event, advantage_event, baroque_event = _compact_consume_panel_events(slot_anim)
    chips: list[tuple[str, str, tuple[int, int, int], float, dict | None]] = []

    if damage_event is not None:
        event_type = str(damage_event.get("type") or "")
        if event_type == "opponent":
            label, color = "DMG OUT", (255, 110, 110)
        elif event_type == "heal":
            label, color = "HP +", (92, 232, 146)
        else:
            label, color = "DMG IN", (255, 110, 110)
        chips.append((label, _compact_short_number(damage_event.get("value", 0)), color, float(damage_event.get("life", 1.0)), damage_event))

    if meter_event is not None:
        direction = str(meter_event.get("direction") or "gain")
        gain = direction != "loss"
        chips.append(("MTR", f"{'+' if gain else '-'}{_compact_short_number(meter_event.get('value', 0))}", (96, 182, 255) if gain else (255, 164, 92), float(meter_event.get("life", 1.0)), meter_event))

    if advantage_event is not None:
        value = int(advantage_event.get("value", 0))
        value_text = f"{value:+d}" if value else "0"
        color = (92, 232, 146) if value > 0 else ((255, 112, 112) if value < 0 else (196, 205, 220))
        chips.append(("FRAME", value_text, color, float(advantage_event.get("life", 1.0)), advantage_event))

    if baroque_event is not None:
        value = float(baroque_event.get("value", 0.0))
        value_text = f"{value:+.0f}%"
        color = (172, 112, 255) if value > 0 else (255, 180, 92)
        chips.append(("BBQ", value_text, color, float(baroque_event.get("life", 1.0)), baroque_event))

    action_text, action_color, action_kind = _compact_action_chip(action_label)
    draw_x = x
    gap = max(4, int(5 * scale))
    for label, value, color, life, event in chips:
        label_surface = font_sm.render(label, True, (142, 151, 169))
        value_surface = font_sm.render(value, True, color)
        width = label_surface.get_width() + value_surface.get_width() + max(3, int(4 * scale)) + max(8, int(10 * scale))
        if draw_x + width > right:
            continue
        used = _draw_compact_stat_chip(screen, font_sm, draw_x, y, label, value, color, scale, life, event)
        draw_x += used + gap

    if action_text and draw_x < right:
        label_surface = font_sm.render(action_kind, True, (142, 151, 169))
        pad_x = max(4, int(5 * scale))
        text_gap = max(3, int(4 * scale))
        available = right - draw_x - label_surface.get_width() - pad_x * 2 - text_gap
        value = _compact_fit_text(font_sm, action_text, available)
        if value:
            _draw_compact_stat_chip(screen, font_sm, draw_x, y, action_kind, value, action_color, scale, 1.0)


def _draw_compact_history_line(screen, font_sm, title: str, items: list[dict], x: int, y: int, right: int, scale: float, prev_items: list[dict] | None = None, slide_progress: float = 0.0) -> None:
    title_surface = font_sm.render(title, True, (110, 122, 142))
    screen.blit(title_surface, (x, y))
    draw_x = x + title_surface.get_width() + max(6, int(7 * scale))
    gap = max(6, int(7 * scale))
    clip_rect = pygame.Rect(draw_x, y - 1, max(1, right - draw_x), font_sm.get_height() + int(4 * scale))

    def _norm(source) -> list[dict]:
        out = []
        for item in source or []:
            if not item:
                continue
            label = str(item.get("label") or "").strip()
            value = str(item.get("value") or "").strip()
            if not label and not value:
                continue
            out.append({
                "label": label,
                "value": value,
                "color": item.get("color") or (196, 205, 220),
                "life": float(item.get("life", 1.0)),
            })
            if len(out) >= 4:
                break
        return out

    def _render_parts(source_items, alpha_override=None):
        rendered = []
        for idx, item in enumerate(source_items):
            alpha = max(0.35, min(1.0, alpha_override if alpha_override is not None else item.get("life", 1.0)))
            txt = f"{item.get('label','')} {item.get('value','')}".strip()
            surf = font_sm.render(txt, True, item.get("color") or (196, 205, 220))
            surf.set_alpha(int(255 * alpha))
            rendered.append(surf)
            if idx < len(source_items) - 1:
                dot = font_sm.render("•", True, (86, 96, 114))
                dot.set_alpha(int(180 * alpha))
                rendered.append(dot)
        return rendered

    current_items = _norm(items)
    previous_items = _norm(prev_items)
    if not current_items and not previous_items:
        empty = font_sm.render("—", True, (86, 96, 114))
        screen.blit(empty, (draw_x, y))
        return

    current_parts = _render_parts(current_items, 1.0)
    previous_parts = _render_parts(previous_items, max(0.0, min(1.0, slide_progress))) if previous_items else []
    inserted_shift = max(int(26 * scale), current_parts[0].get_width() + gap if current_parts else int(26 * scale))

    def _draw_parts(parts, base_x):
        dx = base_x
        for idx, surf in enumerate(parts):
            if dx > right:
                break
            screen.blit(surf, (dx, y))
            dx += surf.get_width()
            if idx < len(parts) - 1:
                dx += gap

    old_clip = screen.get_clip()
    screen.set_clip(clip_rect)
    if slide_progress > 0.001 and previous_parts:
        _draw_parts(previous_parts, draw_x + int(inserted_shift * (1.0 - slide_progress)))
        _draw_parts(current_parts, draw_x - int(inserted_shift * slide_progress))
    else:
        _draw_parts(current_parts, draw_x)
    screen.set_clip(old_clip)


def _merge_move_history(*lists: list[dict]) -> list[dict]:
    merged: list[dict] = []
    for seq in lists:
        for item in seq or []:
            if item and item.get("text"):
                merged.append(item)
    merged.sort(key=lambda item: int(item.get("frame", 0)), reverse=True)
    # remove immediate duplicates while preserving order
    filtered: list[dict] = []
    last_text = None
    for item in merged:
        text = str(item.get("text") or "")
        if text == last_text:
            continue
        filtered.append(item)
        last_text = text
        if len(filtered) >= 5:
            break
    return filtered


def _draw_compact_move_history(screen, font_sm, items: list[dict], x: int, y: int, right: int, scale: float, prev_texts: list[str] | None = None, slide_progress: float = 0.0) -> None:
    title_surface = font_sm.render("MOVES", True, (110, 122, 142))
    screen.blit(title_surface, (x, y))
    draw_x = x + title_surface.get_width() + max(6, int(7 * scale))
    gap = max(5, int(6 * scale))
    clip_rect = pygame.Rect(draw_x, y - 1, max(1, right - draw_x), font_sm.get_height() + int(4 * scale))

    recency_colors = [
        (92, 232, 146),
        (132, 204, 255),
        (232, 236, 244),
        (178, 188, 204),
        (122, 134, 153),
    ]

    def _normalize_texts(source) -> list[str]:
        out = []
        for item in source or []:
            if isinstance(item, dict):
                txt = str(item.get("text") or "").strip()
            else:
                txt = str(item or "").strip()
            if txt:
                out.append(txt)
            if len(out) >= 5:
                break
        return out

    def _build_parts(texts: list[str], alpha: float):
        rendered = []
        for idx, txt in enumerate(texts[:5]):
            color = recency_colors[min(idx, len(recency_colors) - 1)]
            surf = font_sm.render(txt.upper(), True, color)
            if alpha < 0.999:
                surf.set_alpha(max(0, min(255, int(255 * alpha))))
            rendered.append(surf)
            if idx < len(texts[:5]) - 1:
                sep = font_sm.render(">", True, (82, 92, 108))
                if alpha < 0.999:
                    sep.set_alpha(max(0, min(255, int(255 * alpha))))
                rendered.append(sep)
        return rendered

    def _line_width(parts) -> int:
        total = 0
        move_index = 0
        for part in parts:
            total += part.get_width()
            if part != parts[-1]:
                total += gap if move_index % 2 == 0 else gap
            move_index += 1
        return total

    def _draw_parts(parts, base_x: int, alpha: float):
        dx = base_x
        for idx, surf in enumerate(parts):
            if dx > right:
                break
            screen.blit(surf, (dx, y))
            dx += surf.get_width()
            if idx < len(parts) - 1:
                dx += gap

    current_texts = _normalize_texts(items)
    previous_texts = _normalize_texts(prev_texts)

    if not current_texts and not previous_texts:
        empty = font_sm.render("—", True, (86, 96, 114))
        screen.blit(empty, (draw_x, y))
        return

    current_parts = _build_parts(current_texts, 1.0)
    previous_parts = _build_parts(previous_texts, max(0.0, min(1.0, slide_progress))) if previous_texts else []

    inserted_shift = 0
    if current_texts:
        newest = font_sm.render(current_texts[0].upper(), True, recency_colors[0])
        inserted_shift = newest.get_width() + gap
        if len(current_texts) > 1:
            inserted_shift += font_sm.render(">", True, (82, 92, 108)).get_width() + gap
        inserted_shift = max(inserted_shift, int(24 * scale))

    old_clip = screen.get_clip()
    screen.set_clip(clip_rect)
    if slide_progress > 0.001 and previous_parts:
        old_alpha = max(0.0, min(1.0, slide_progress))
        old_x = draw_x + int(inserted_shift * (1.0 - slide_progress))
        _draw_parts(previous_parts, old_x, old_alpha)
        new_x = draw_x - int(inserted_shift * slide_progress)
        _draw_parts(current_parts, new_x, 1.0)
    else:
        _draw_parts(current_parts, draw_x, 1.0)
    screen.set_clip(old_clip)


def _draw_compact_ko_badge(screen, font_sm, rect: pygame.Rect, scale: float, alpha: float = 1.0, scale_mul: float = 1.0) -> None:
    alpha = max(0.0, min(1.0, float(alpha)))
    scale_mul = max(0.65, float(scale_mul))
    if alpha <= 0.01:
        return
    w = max(1, int(rect.width * scale_mul))
    h = max(1, int(rect.height * scale_mul))
    draw_rect = pygame.Rect(0, 0, w, h)
    draw_rect.center = rect.center
    radius = max(2, int(3 * scale * scale_mul))
    badge = pygame.Surface((draw_rect.width, draw_rect.height), pygame.SRCALPHA)
    pygame.draw.rect(badge, (54, 16, 22, int(214 * alpha)), badge.get_rect(), border_radius=radius)
    pygame.draw.rect(badge, (255, 92, 104, int(245 * alpha)), badge.get_rect(), 1, border_radius=radius)
    label = font_sm.render("KO", True, (255, 224, 228))
    if scale_mul != 1.0:
        label = pygame.transform.smoothscale(label, (max(1, int(label.get_width() * scale_mul)), max(1, int(label.get_height() * scale_mul))))
    label.set_alpha(int(255 * alpha))
    badge.blit(label, (badge.get_width() // 2 - label.get_width() // 2, badge.get_height() // 2 - label.get_height() // 2))
    screen.blit(badge, draw_rect.topleft)


def _draw_live_interaction_ribbon(screen, font, font_sm, scale: float, dt: float) -> None:
    life = max(0.0, float(_interaction_ribbon.get("life") or 0.0) - dt * 0.72)
    _interaction_ribbon["life"] = life
    if life <= 0.01 or not _interaction_ribbon.get("title"):
        return
    fade = min(1.0, life * 2.6)
    title = str(_interaction_ribbon.get("title") or "")
    detail = str(_interaction_ribbon.get("detail") or "")
    accent = tuple(_interaction_ribbon.get("color") or (130, 175, 255))
    title_s = font.render(title, True, (242, 245, 250))
    detail_s = font_sm.render(detail, True, (192, 204, 222))
    width = max(int(238 * scale), title_s.get_width() + int(28 * scale), detail_s.get_width() + int(28 * scale))
    height = max(int(35 * scale), title_s.get_height() + detail_s.get_height() + int(10 * scale))
    x = screen.get_width() // 2 - width // 2
    y = int(106 * scale - (1.0 - fade) * 9 * scale)
    card = pygame.Surface((width, height), pygame.SRCALPHA)
    pygame.draw.rect(card, (18, 23, 34, int(224 * fade)), card.get_rect(), border_radius=max(3, int(5 * scale)))
    pygame.draw.rect(card, (*accent, int(225 * fade)), card.get_rect(), 1, border_radius=max(3, int(5 * scale)))
    pygame.draw.rect(card, (*accent, int(210 * fade)), (0, 0, max(3, int(4 * scale)), height), border_radius=max(2, int(3 * scale)))
    title_s.set_alpha(int(255 * fade)); detail_s.set_alpha(int(240 * fade))
    card.blit(title_s, (int(11 * scale), int(4 * scale)))
    card.blit(detail_s, (int(11 * scale), height - detail_s.get_height() - int(4 * scale)))
    screen.blit(card, (x, y))


def _draw_combo_ledger(screen, font_sm, team: str, x: int, y: int, width: int, scale: float, is_left: bool) -> None:
    ledger = _combo_ledgers.get(team) or {}
    life = float(ledger.get("life") or 0.0)
    if life <= 0.01:
        return
    attacker_slot = str(ledger.get("attacker_slot") or "")
    if not attacker_slot:
        return
    fade = min(1.0, life * 2.4)
    meter_delta = _snap_int(attacker_slot, "meter") - int(ledger.get("meter_start") or 0)
    baroque_delta = _snap_float(attacker_slot, "baroque_red_pct_max") - float(ledger.get("baroque_start") or 0.0)
    hits = int(ledger.get("hits") or 0)
    damage = int(ledger.get("damage") or 0)
    title = f"COMBO  {hits} HIT{'S' if hits != 1 else ''}  |  {damage:,} DMG"
    resource = f"MTR {meter_delta:+,}"
    if abs(baroque_delta) >= 0.5:
        resource += f"  •  BBQ {baroque_delta:+.0f}%"
    title_s = font_sm.render(title, True, (236, 241, 248))
    resource_s = font_sm.render(resource, True, (124, 188, 255) if meter_delta >= 0 else (255, 174, 104))
    h = max(int(27 * scale), title_s.get_height() + resource_s.get_height() + int(7 * scale))
    w = min(width, max(int(178 * scale), title_s.get_width() + int(18 * scale), resource_s.get_width() + int(18 * scale)))
    draw_x = x if is_left else x + width - w
    card = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(card, (15, 19, 28, int(208 * fade)), card.get_rect(), border_radius=max(3, int(4 * scale)))
    pygame.draw.rect(card, (107, 154, 232, int(178 * fade)), card.get_rect(), 1, border_radius=max(3, int(4 * scale)))
    rail_x = 0 if is_left else w - max(2, int(3 * scale))
    pygame.draw.rect(card, (107, 154, 232, int(220 * fade)), (rail_x, 0, max(2, int(3 * scale)), h))
    title_s.set_alpha(int(255 * fade)); resource_s.set_alpha(int(238 * fade))
    card.blit(title_s, (int(8 * scale), int(3 * scale)))
    card.blit(resource_s, (int(8 * scale), h - resource_s.get_height() - int(3 * scale)))
    screen.blit(card, (draw_x, y))


def _draw_tag_card(screen, font_sm, team_anim: dict, x: int, y: int, width: int, scale: float, is_left: bool, dt: float) -> None:
    card_data = team_anim.get("tag_card")
    if not isinstance(card_data, dict):
        return
    card_data["life"] = max(0.0, float(card_data.get("life") or 0.0) - dt * 0.62)
    life = float(card_data.get("life") or 0.0)
    if life <= 0.01:
        team_anim["tag_card"] = None
        return
    fade = min(1.0, life * 2.5)
    slide = int((1.0 - fade) * 16 * scale)
    name = _compact_trim(str(card_data.get("name") or "---"), 16)
    hp = _compact_hp_text(card_data.get("cur"), card_data.get("max"))
    meter = _compact_meter_text(card_data.get("meter"))
    bbq = float(card_data.get("bbq") or 0.0)
    try:
        cur_hp = max(0.0, float(card_data.get("cur") or 0.0))
        max_hp = max(1.0, float(card_data.get("max") or 1.0))
        hp_pct = min(1.0, cur_hp / max_hp)
    except Exception:
        hp_pct = 1.0

    if hp_pct <= 0.25:
        hp_state = "CRITICAL"
        accent = (235, 94, 100)
        fill = (52, 20, 27)
        detail_color = (255, 177, 181)
    elif hp_pct <= 0.55:
        hp_state = "CAUTION"
        accent = (239, 192, 83)
        fill = (51, 42, 20)
        detail_color = (255, 225, 144)
    else:
        hp_state = "READY"
        accent = (91, 210, 137)
        fill = (18, 48, 34)
        detail_color = (166, 241, 194)

    title_s = font_sm.render(f"TAG IN  •  {name}", True, (236, 241, 248))
    detail = f"{hp_state}  •  HP {hp}  |  MTR {meter}"
    if bbq > 0.0:
        detail += f"  |  BBQ {bbq:.0f}%"
    detail_s = font_sm.render(detail, True, detail_color)
    h = max(int(28 * scale), title_s.get_height() + detail_s.get_height() + int(7 * scale))
    w = min(width, max(int(185 * scale), title_s.get_width() + int(18 * scale), detail_s.get_width() + int(18 * scale)))
    draw_x = x if is_left else x + width - w
    draw_y = y + slide
    card = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(card, (*fill, int(224 * fade)), card.get_rect(), border_radius=max(3, int(4 * scale)))
    pygame.draw.rect(card, (*accent, int(200 * fade)), card.get_rect(), 1, border_radius=max(3, int(4 * scale)))
    rail_w = max(2, int(3 * scale))
    rail_x = 0 if is_left else w - rail_w
    pygame.draw.rect(card, (*accent, int(238 * fade)), (rail_x, 0, rail_w, h), border_radius=max(2, int(3 * scale)))
    title_s.set_alpha(int(255 * fade)); detail_s.set_alpha(int(245 * fade))
    card.blit(title_s, (int(8 * scale), int(3 * scale)))
    card.blit(detail_s, (int(8 * scale), h - detail_s.get_height() - int(3 * scale)))
    screen.blit(card, (draw_x, draw_y))


def _draw_compact_team_panel(screen, font, font_sm, team: str, slots: dict, scale: float, overlay_alpha: float, dt: float, control=None) -> None:
    first_label, second_label = f"{team}-C1", f"{team}-C2"
    point_label = _get_active_slot(team) or first_label
    partner_label = second_label if point_label == first_label else first_label
    point = slots.get(point_label) or slots.get(first_label) or slots.get(second_label)
    partner = slots.get(partner_label) or {}
    if not point:
        return

    point_anim = _get_slot_anim(point_label)
    partner_anim = _get_slot_anim(partner_label)
    point_anim["meter_display"] = _approach(
        point_anim["meter_display"], _compact_meter_level(point.get("meter")), PIP_SPEED, dt
    )
    partner_anim["meter_display"] = _approach(
        partner_anim["meter_display"], _compact_meter_level(partner.get("meter")), PIP_SPEED, dt
    )

    try:
        point_cur = max(0, int(point.get("cur") or 0))
        point_max = max(1, int(point.get("max") or 1))
    except (TypeError, ValueError):
        point_cur, point_max = 0, 1
    point_hp_target = max(0.0, min(1.0, point_cur / point_max))
    if point_anim.get("hp_display_frac") is None:
        point_anim["hp_display_frac"] = point_hp_target
    point_anim["hp_display_frac"] = _approach(point_anim["hp_display_frac"], point_hp_target, 2.4, dt)

    try:
        partner_cur = max(0, int(partner.get("cur") or 0))
        partner_max = max(1, int(partner.get("max") or 1))
    except (TypeError, ValueError):
        partner_cur, partner_max = 0, 1
    partner_hp_target = max(0.0, min(1.0, partner_cur / partner_max))
    if partner_anim.get("hp_display_frac") is None:
        partner_anim["hp_display_frac"] = partner_hp_target
    partner_anim["hp_display_frac"] = _approach(partner_anim["hp_display_frac"], partner_hp_target, 2.6, dt)

    try:
        point_meter_target = max(0.0, float(point.get("meter") or 0.0))
    except (TypeError, ValueError):
        point_meter_target = 0.0
    if point_anim.get("meter_display_value") is None:
        point_anim["meter_display_value"] = point_meter_target
    point_anim["meter_display_value"] = _approach(point_anim["meter_display_value"], point_meter_target, 32000.0, dt)

    width = max(430, int(470 * scale))
    height = max(118, int(126 * scale))
    margin_x = int(12 * scale)
    base_y = int(148 * scale)
    is_left = team == "P1"
    base_x = margin_x if is_left else screen.get_width() - margin_x - width
    accent = SLOT_COLORS.get(point_label, SLOT_COLORS[f"{team}-C1"])
    point_dead = int(point.get("cur") or 0) <= 0
    point_anim["ko_alpha"] = _approach(float(point_anim.get("ko_alpha", 0.0)), 1.0 if point_dead else 0.0, 6.5 if point_dead else 3.2, dt)
    point_anim["ko_scale"] = _approach(float(point_anim.get("ko_scale", 0.90)), 1.08 if point_dead else 0.92, 8.0 if point_dead else 4.0, dt)

    team_anim = _get_team_anim(team)
    team_present = bool(_get_slot_anim(first_label).get("present") or _get_slot_anim(second_label).get("present"))
    if team_present and not team_anim.get("present"):
        team_anim["slide_y"] = -34.0
        team_anim["slide_x"] = 0.0
        team_anim["alpha"] = min(team_anim.get("alpha", 0.0), 0.20)
    if team_present:
        prior_point = team_anim.get("current_point_label")
        if prior_point is None:
            team_anim["current_point_label"] = point_label
        elif prior_point != point_label:
            team_anim["current_point_label"] = point_label
            team_anim["swap_progress"] = 1.0
            team_anim["tag_card"] = {
                "name": str(point.get("name") or "---"),
                "cur": point.get("cur"),
                "max": point.get("max"),
                "meter": point.get("meter"),
                "bbq": point.get("baroque_red_pct_max"),
                "life": 1.0,
            }
    team_anim["present"] = team_present
    if team_present:
        team_anim["slide_y"] = _approach(float(team_anim.get("slide_y", -34.0)), 0.0, 160.0, dt)
        team_anim["slide_x"] = _approach(float(team_anim.get("slide_x", 0.0)), 0.0, 1800.0, dt)
        team_anim["alpha"] = _approach(float(team_anim.get("alpha", 0.0)), 1.0, 4.8, dt)
        team_anim["swap_progress"] = _approach(float(team_anim.get("swap_progress", 0.0)), 0.0, 4.0, dt)
    else:
        off_target = -float(width + margin_x + 18) if is_left else float(width + margin_x + 18)
        team_anim["slide_x"] = _approach(float(team_anim.get("slide_x", 0.0)), off_target, 1700.0, dt)
        team_anim["alpha"] = _approach(float(team_anim.get("alpha", 0.0)), 0.0, 3.2, dt)

    panel_alpha = overlay_alpha * float(team_anim.get("alpha", 0.0))
    if panel_alpha <= 0.01:
        return
    x = int(base_x + float(team_anim.get("slide_x", 0.0)))
    y = int(base_y + float(team_anim.get("slide_y", 0.0)))

    panel = pygame.Surface((width, height), pygame.SRCALPHA)
    base_alpha = int(228 * panel_alpha)
    notch = max(8, int(10 * scale))
    if is_left:
        points = [(notch, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1), (0, notch)]
    else:
        points = [(0, 0), (width - notch - 1, 0), (width - 1, notch), (width - 1, height - 1), (0, height - 1)]
    pygame.draw.polygon(panel, (33, 38, 46, base_alpha), points)
    for py in range(height):
        blend = py / max(1, height - 1)
        c1 = (66, 72, 82)
        c2 = (28, 32, 39)
        line_color = _lerp_color(c1, c2, blend)
        pygame.draw.line(panel, (*line_color, int(172 * panel_alpha)), (1, py), (width - 2, py))
    sheen_surface = pygame.Surface((width, height), pygame.SRCALPHA)
    pygame.draw.polygon(sheen_surface, (255, 255, 255, int(18 * panel_alpha)), [
        (int(width * 0.08), 0),
        (int(width * 0.36), 0),
        (int(width * 0.24), height - 1),
        (0, height - 1),
    ])
    pygame.draw.line(sheen_surface, (250, 252, 255, int(26 * panel_alpha)), (int(width * 0.08), 1), (int(width * 0.36), 1), 1)
    panel.blit(sheen_surface, (0, 0))
    pygame.draw.polygon(panel, (*accent, int(155 * panel_alpha)), points, 1)
    pygame.draw.line(panel, (250, 252, 255, int(42 * panel_alpha)), (notch if is_left else 0, 0), (width - 1 if is_left else width - notch - 1, 0))
    pygame.draw.line(panel, (8, 10, 14, int(178 * panel_alpha)), (0, height - 1), (width - 1, height - 1))
    pygame.draw.line(panel, (*accent, int(42 * panel_alpha)), (int(8 * scale), int(35 * scale)), (width - int(8 * scale), int(35 * scale)))
    rail_w = max(3, int(4 * scale))
    rail_x = 0 if is_left else width - rail_w
    pygame.draw.rect(panel, (*accent, int(238 * overlay_alpha)), (rail_x, 0, rail_w, height))
    screen.blit(panel, (x, y))

    outer_pad = int(10 * scale)
    left = x + outer_pad
    right = x + width - outer_pad
    # Two complete character rows sit together at the top of the panel:
    # badge + name + health + state.  LOG/MOVES begin only after both rows,
    # so a C2 marker never becomes visually detached from its character.
    primary_y = y + int(7 * scale)
    hp_y = primary_y + max(16, int(18 * scale)) + int(4 * scale)
    secondary_y = hp_y + max(7, int(8 * scale)) + int(7 * scale)
    partner_hp_y = secondary_y + font_sm.get_height() + int(2 * scale)
    strip_y = partner_hp_y + max(5, int(6 * scale)) + int(5 * scale)
    history_y = strip_y + max(17, int(18 * scale)) + int(3 * scale)
    move_history_y = history_y + max(12, int(13 * scale)) + int(2 * scale)
    # Preserve the old tag-swap motion: when the active point changes, the
    # incoming fighter rises from the reserve row while the outgoing fighter
    # drops into it.  The C1/C2 badges travel with their full character rows.
    swap_progress = float(team_anim.get("swap_progress", 0.0))
    row_distance = secondary_y - primary_y
    hp_distance = partner_hp_y - hp_y
    top_row_y = int(primary_y + row_distance * swap_progress)
    top_hp_y = int(hp_y + hp_distance * swap_progress)
    bottom_row_y = int(secondary_y - row_distance * swap_progress)
    bottom_hp_y = int(partner_hp_y - hp_distance * swap_progress)
    top_row_alpha = max(0.55, 1.0 - 0.18 * swap_progress)
    bottom_row_alpha = max(0.55, 1.0 - 0.18 * swap_progress)

    # Character identity stays attached to each character row.  C1 and C2 are
    # still directly one under the other, but each now carries its own name,
    # health, and status instead of leaving C2 stranded above the LOG/MOVES area.
    point_badge = "C1" if point_label.endswith("C1") else "C2"
    partner_badge = "C1" if partner_label.endswith("C1") else "C2"
    partner_color = SLOT_COLORS.get(partner_label, accent)
    badge_w = max(22, int(25 * scale))
    badge_h = max(16, int(18 * scale))
    badge_radius = max(2, int(2 * scale))

    badge_rect = pygame.Rect(left, top_row_y, badge_w, badge_h)
    pygame.draw.rect(screen, accent, badge_rect, border_radius=badge_radius)
    badge = font_sm.render(point_badge, True, (250, 250, 252))
    screen.blit(badge, (badge_rect.centerx - badge.get_width() // 2, badge_rect.centery - badge.get_height() // 2))

    partner_badge_rect = pygame.Rect(left, bottom_row_y, badge_w, badge_h)
    partner_fill = tuple(max(24, int(channel * 0.24)) for channel in partner_color)
    pygame.draw.rect(screen, partner_fill, partner_badge_rect, border_radius=badge_radius)
    pygame.draw.rect(screen, partner_color, partner_badge_rect, 1, border_radius=badge_radius)
    partner_badge_surface = font_sm.render(partner_badge, True, (183, 193, 210))
    screen.blit(
        partner_badge_surface,
        (
            partner_badge_rect.centerx - partner_badge_surface.get_width() // 2,
            partner_badge_rect.centery - partner_badge_surface.get_height() // 2,
        ),
    )

    name_x = badge_rect.right + int(7 * scale)
    name = _compact_trim(str(point.get("name") or "???"), 13)
    name_surface = font.render(name, True, COL_DEAD if point_dead else (235, 238, 245))
    name_surface.set_alpha(int(255 * top_row_alpha))
    screen.blit(name_surface, (name_x, top_row_y - max(0, int(1 * scale))))

    baroque = bool(point.get("baroque_ready_local", False)) and not point_dead
    if baroque:
        point_anim["baroque_last_pct"] = float(point.get("baroque_red_pct_max") or 0.0)
        point_anim["baroque_display_pct"] = _approach(float(point_anim.get("baroque_display_pct", 0.0)), point_anim["baroque_last_pct"], 80.0, dt)
        point_anim["baroque_alpha"] = _approach(float(point_anim.get("baroque_alpha", 0.0)), 1.0, 5.5, dt)
    else:
        point_anim["baroque_alpha"] = _approach(float(point_anim.get("baroque_alpha", 0.0)), 0.0, 2.1, dt)
    show_baroque_badge = float(point_anim.get("baroque_alpha", 0.0)) > 0.03
    power_w = max(int(76 * scale), font_sm.size("METER 5")[0] + int(12 * scale))
    power_left = right - power_w
    separator_x = power_left - int(6 * scale)
    pygame.draw.line(screen, (48, 65, 94), (separator_x, primary_y), (separator_x, move_history_y + max(12, int(13 * scale))), 1)

    meter_level = int(round(point_anim["meter_display"]))
    meter_color = _compact_meter_color(point.get("meter"))
    meter_caption = font_sm.render("METER", True, (122, 144, 184))
    meter_value = font_sm.render(str(meter_level), True, meter_color if not point_dead else COL_DEAD)
    meter_exact = font_sm.render(_compact_meter_text(point.get("meter")), True, COL_DEAD if point_dead else (188, 198, 214))
    meter_label_y = primary_y - max(0, int(1 * scale))
    screen.blit(meter_caption, (power_left, meter_label_y))
    screen.blit(meter_value, (right - meter_value.get_width(), meter_label_y))
    meter_y = primary_y + meter_caption.get_height() + max(1, int(1 * scale))
    _draw_compact_meter(screen, power_left, meter_y, power_w, point_anim.get("meter_display_value", point.get("meter")), scale, point_dead)
    meter_exact_x = right - meter_exact.get_width()
    meter_exact_y = meter_y + max(9, int(11 * scale))
    screen.blit(meter_exact, (meter_exact_x, meter_exact_y))

    if show_baroque_badge:
        bq_h = max(14, int(16 * scale))
        bq_rect = pygame.Rect(power_left, strip_y + int(1 * scale), power_w, bq_h)
        _draw_compact_baroque_badge(screen, font_sm, bq_rect, float(point_anim.get("baroque_display_pct", point.get("baroque_red_pct_max") or 0.0)), scale, is_left, float(point_anim.get("baroque_alpha", 0.0)))

    hp_h = max(6, int(7 * scale))
    hp_text = font_sm.render(_compact_hp_text(point.get("cur"), point.get("max")), True, COL_DEAD if point_dead else (182, 192, 208))
    hp_gap = int(5 * scale)
    ko_gap = int(7 * scale)
    ko_w = max(22, font_sm.size("KO")[0] + int(10 * scale)) if point_dead else 0
    ko_h = max(14, int(16 * scale)) if point_dead else 0
    reserve_w = hp_text.get_width() + hp_gap + (ko_w + ko_gap if point_dead else 0)
    hp_w = max(84, min(max(140, int(164 * scale)), power_left - name_x - reserve_w - int(4 * scale)))
    _draw_compact_health(screen, name_x, top_hp_y, hp_w, hp_h, point.get("cur"), point.get("max"), point_dead, point_anim.get("hp_display_frac"))
    hp_text_x = name_x + hp_w + hp_gap
    hp_text.set_alpha(int(255 * top_row_alpha)); screen.blit(hp_text, (hp_text_x, top_hp_y - max(1, int(2 * scale))))
    if point_dead:
        ko_x = hp_text_x + hp_text.get_width() + ko_gap
        ko_x = min(ko_x, power_left - ko_w - int(4 * scale))
        _draw_compact_ko_badge(screen, font_sm, pygame.Rect(ko_x, top_hp_y - int(4 * scale), ko_w, ko_h), scale, float(point_anim.get("ko_alpha", 1.0)), float(point_anim.get("ko_scale", 1.0)))

    for entry in point_anim.get("event_history", []):
        entry["life"] = max(0.30, float(entry.get("life", 1.0)) - 0.003)
    point_anim["event_history"] = point_anim.get("event_history", [])[:6]

    action_label = _compact_move_label(point)
    info_right = power_left - int(12 * scale)
    _draw_compact_info_strip(screen, font_sm, point_anim, left, strip_y, info_right, action_label, scale)
    log_items = point_anim.get("event_history", [])[:4]
    log_signature = tuple(f"{str(item.get('label') or '').strip()} {str(item.get('value') or '').strip()}".strip() for item in log_items if item)
    previous_log_signature = tuple(team_anim.get("log_history_signature", ()))
    if log_signature != previous_log_signature:
        if previous_log_signature:
            team_anim["log_history_prev"] = [
                {"label": (txt.split(' ', 1)[0] if ' ' in txt else txt), "value": (txt.split(' ', 1)[1] if ' ' in txt else ''), "color": (196, 205, 220), "life": 1.0}
                for txt in previous_log_signature
            ]
            team_anim["log_history_slide"] = 1.0
        team_anim["log_history_signature"] = log_signature
    team_anim["log_history_slide"] = _approach(float(team_anim.get("log_history_slide", 0.0)), 0.0, 6.0, dt)
    _draw_compact_history_line(screen, font_sm, "LOG", log_items, left, history_y, right, scale, team_anim.get("log_history_prev", []), float(team_anim.get("log_history_slide", 0.0)))
    merged_moves = _merge_move_history(point_anim.get("move_events", []), partner_anim.get("move_events", []))
    move_signature = tuple(str(item.get("text") or "").strip() for item in merged_moves[:5] if str(item.get("text") or "").strip())
    previous_signature = tuple(team_anim.get("move_history_signature", ()))
    if move_signature != previous_signature:
        if previous_signature:
            team_anim["move_history_prev"] = list(previous_signature)
            team_anim["move_history_slide"] = 1.0
        team_anim["move_history_signature"] = move_signature
    team_anim["move_history_slide"] = _approach(float(team_anim.get("move_history_slide", 0.0)), 0.0, 6.0, dt)
    _draw_compact_move_history(
        screen,
        font_sm,
        merged_moves,
        left,
        move_history_y,
        right,
        scale,
        team_anim.get("move_history_prev", []),
        float(team_anim.get("move_history_slide", 0.0)),
    )

    partner_dead = int(partner.get("cur") or 0) <= 0
    partner_anim["ko_alpha"] = _approach(float(partner_anim.get("ko_alpha", 0.0)), 1.0 if partner_dead else 0.0, 6.5 if partner_dead else 3.2, dt)
    partner_anim["ko_scale"] = _approach(float(partner_anim.get("ko_scale", 0.90)), 1.08 if partner_dead else 0.92, 8.0 if partner_dead else 4.0, dt)

    # Reserve row: C2 badge, name, health, and status are one unit.
    partner_name = _compact_trim(str(partner.get("name") or "---"), 15)
    partner_name_surface = font_sm.render(partner_name, True, COL_DEAD if partner_dead else (168, 177, 194))
    partner_name_x = partner_badge_rect.right + int(7 * scale)
    partner_name_surface.set_alpha(int(255 * bottom_row_alpha))
    screen.blit(partner_name_surface, (partner_name_x, bottom_row_y))

    partner_state = _compact_partner_state(partner)
    state_color = (255, 112, 120) if partner_dead else (partner_color if partner_state == "ACTIVE" else (132, 144, 164))
    state_surface = font_sm.render(partner_state, True, state_color)
    partner_bq_pct = float(partner.get("baroque_red_pct_max") or 0.0)
    bq_surface = _render_compact_rainbow_text(font_sm, f"BBQ {partner_bq_pct:.0f}%", 0.18) if (partner_bq_pct > 0.0 and not partner_dead) else None
    if bq_surface is not None:
        bq_x = right - bq_surface.get_width()
        bq_surface.set_alpha(int(255 * bottom_row_alpha))
        screen.blit(bq_surface, (bq_x, bottom_row_y))
        state_x = bq_x - int(8 * scale) - state_surface.get_width()
    else:
        state_x = right - state_surface.get_width()
    state_x = max(partner_name_x + partner_name_surface.get_width() + int(9 * scale), state_x)
    state_surface.set_alpha(int(255 * bottom_row_alpha))
    screen.blit(state_surface, (state_x, bottom_row_y))

    partner_hp_text = font_sm.render(_compact_hp_text(partner.get("cur"), partner.get("max")), True, COL_DEAD if partner_dead else (150, 161, 180))
    partner_hp_gap = int(5 * scale)
    partner_ko_w = max(22, font_sm.size("KO")[0] + int(10 * scale)) if partner_dead else 0
    partner_ko_gap = int(7 * scale)
    partner_hp_reserve = partner_hp_text.get_width() + partner_hp_gap + (partner_ko_w + partner_ko_gap if partner_dead else 0)
    partner_hp_w = max(54, min(max(98, int(118 * scale)), state_x - partner_name_x - partner_hp_reserve - int(6 * scale)))
    _draw_compact_health(
        screen, partner_name_x, bottom_hp_y, partner_hp_w, max(4, int(5 * scale)),
        partner.get("cur"), partner.get("max"), partner_dead, partner_anim.get("hp_display_frac")
    )
    partner_hp_text_x = partner_name_x + partner_hp_w + partner_hp_gap
    partner_hp_text.set_alpha(int(255 * bottom_row_alpha))
    screen.blit(partner_hp_text, (partner_hp_text_x, bottom_hp_y - max(1, int(2 * scale))))
    if partner_dead:
        ko_x = min(partner_hp_text_x + partner_hp_text.get_width() + partner_ko_gap, state_x - partner_ko_w - int(4 * scale))
        _draw_compact_ko_badge(
            screen, font_sm, pygame.Rect(ko_x, bottom_hp_y - int(4 * scale), partner_ko_w, max(13, int(15 * scale))),
            scale, float(partner_anim.get("ko_alpha", 1.0)), float(partner_anim.get("ko_scale", 1.0))
        )
    if control is None or getattr(control, "show_tag_card", True):
        _draw_tag_card(screen, font_sm, team_anim, x, y + height + int(5 * scale), width, scale, is_left, dt)
    if control is None or getattr(control, "show_combo_card", True):
        _draw_combo_ledger(screen, font_sm, team, x, y + height + int(38 * scale), width, scale, is_left)

def draw_overlay(screen, font, font_sm, slots, scale, dt, control=None) -> None:
    if HUD_LAYOUT_MODE != "compact":
        _draw_overlay_detail(screen, font, font_sm, slots, scale, dt)
        return

    _anim_state["overlay_alpha"] = _approach(_anim_state["overlay_alpha"], 1.0, FADE_SPEED, dt)
    overlay_alpha = _anim_state["overlay_alpha"]

    for slot_label, snap in slots.items():
        if isinstance(snap, dict):
            _compact_track_slot(slot_label, snap)

    _draw_compact_team_panel(screen, font, font_sm, "P1", slots, scale, overlay_alpha, dt, control)
    _draw_compact_team_panel(screen, font, font_sm, "P2", slots, scale, overlay_alpha, dt, control)
    if control is None or getattr(control, "show_interaction_card", True):
        _draw_live_interaction_ribbon(screen, font, font_sm, scale, dt)
    _tick_combo_ledgers(dt)


# ---------------------------------------------------------------------------
# Renderer class for master_overlay.py
# ---------------------------------------------------------------------------

class HudRenderer:
    def __init__(self) -> None:
        self.w = BASE_W
        self.h = BASE_H
        self.scale = 1.0
        self.font = make_font(BASE_FONT_SIZE, bold=True)
        self.font_sm = make_font(int(BASE_FONT_SIZE * 0.78), bold=False)

    def on_resize(self, w: int, h: int) -> None:
        if w <= 0 or h <= 0:
            return
        self.w = w
        self.h = h
        self.scale = min(w / BASE_W, h / BASE_H)
        self.font = make_font(int(BASE_FONT_SIZE * self.scale), bold=True)
        self.font_sm = make_font(int(BASE_FONT_SIZE * self.scale * 0.78), bold=False)

    def update(self, dt: float, control=None) -> None:
        global _frame
        _frame += 1

        new_slots = read_slot_data()

        for slot_label in SLOT_LAYOUT.keys():
            _get_slot_anim(slot_label)["present"] = slot_label in new_slots

        for k, v in new_slots.items():
            if isinstance(v, dict):
                _display_slots[k] = v

        _update_adv()

    def draw(self, screen: pygame.Surface, control=None) -> None:
        if control is not None and not getattr(control, "show_hud", True):
            return

        draw_overlay(screen, self.font, self.font_sm, _display_slots, self.scale, 1 / 60.0, control)

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
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        input("\n[CRASHED] Press Enter to close...")