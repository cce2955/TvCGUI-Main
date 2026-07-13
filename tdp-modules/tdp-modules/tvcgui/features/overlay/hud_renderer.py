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
import random
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
OOMPH_SCALE     = 0.40

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
BLOCK_REACTION_IDS = {48, 49, 50, 51, 52, 53}
BAROQUE_CANCEL_IDS = {162, 163, 164}
INPUT_DIRECTION_MASK = 0x0F
INPUT_FACE_BUTTON_MASK = 0xF0
INPUT_TAUNT_MASK = 0x0C00
INPUT_BUTTON_MASK = INPUT_FACE_BUTTON_MASK | INPUT_TAUNT_MASK
INPUT_TRACK_MASK = INPUT_DIRECTION_MASK | INPUT_BUTTON_MASK

_INPUT_DIRECTION_TEXT = {
    0x0: "5",
    0x1: "6",
    0x2: "4",
    0x4: "8",
    0x5: "9",
    0x6: "7",
    0x8: "2",
    0x9: "3",
    0xA: "1",
}


def _format_overlay_input_token(direction_bits: int, button_bits: int = 0) -> str:
    direction = _INPUT_DIRECTION_TEXT.get(int(direction_bits) & INPUT_DIRECTION_MASK, "5")
    buttons: list[str] = []
    word = int(button_bits) & INPUT_BUTTON_MASK
    if word & 0x80:
        buttons.append("A")
    if word & 0x40:
        buttons.append("B")
    if word & 0x20:
        buttons.append("C")
    if word & 0x10:
        buttons.append("P")
    if (word & INPUT_TAUNT_MASK) == INPUT_TAUNT_MASK:
        buttons.append("T")
    return direction + "".join(buttons)


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

_HISTORY_HEADER_CHIP_CACHE: dict[tuple, pygame.Surface] = {}
_COMPACT_PANEL_SHELL_CACHE: dict[tuple, tuple[pygame.Surface, pygame.Surface]] = {}

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


def _snap_attack_property_label(slot_label: str) -> str:
    snap = _display_slots.get(slot_label) or {}
    label = str(snap.get("attack_property_label") or "").strip().upper()
    if label:
        return label
    try:
        value = int(snap.get("attack_property")) & 0xFF
    except (TypeError, ValueError):
        return "UNKNOWN"
    return {
        0x04: "UNBLOCKABLE",
        0x09: "MID", 0x0A: "MID", 0x0C: "MID",
        0x11: "OVERHEAD", 0x12: "OVERHEAD", 0x14: "OVERHEAD",
        0x21: "LOW", 0x22: "LOW", 0x24: "LOW",
    }.get(value, "UNKNOWN")


def _contact_is_block(victim_slot: str) -> bool:
    victim_move = _mv(victim_slot)
    if victim_move in BLOCK_REACTION_IDS:
        return True
    victim_label = str((_display_slots.get(victim_slot) or {}).get("mv_label") or "").strip().lower()
    return "blockstun" in victim_label or victim_label.startswith("block ")


def _emit_first_contact_badge(st: dict, attacker_slot: str, victim_slot: str) -> bool:
    label = str(st.get("attack_guard_label") or "").strip().upper()
    if not label or label == "UNKNOWN":
        label = _snap_attack_property_label(attacker_slot)
        if label and label != "UNKNOWN":
            st["attack_guard_label"] = label
    if not label or label == "UNKNOWN":
        st["guard_indicator_pending"] = True
        return False
    hit = not bool(st.get("first_contact_blocked", False))
    _set_guard_indicator(victim_slot, label, hit)
    st["guard_indicator_pending"] = False
    st["guard_indicator_emitted"] = True
    return True


def _begin_adv_contact(st: dict, attacker_slot: str, victim_slot: str) -> None:
    current_contact_blocked = _contact_is_block(victim_slot)

    # Hits lock to the first contacting move for the whole combo. Blocks should
    # retrigger for each newly blocked move in a blockstring.
    if bool(st.get("combo_property_locked", False)):
        if current_contact_blocked:
            st["victim_hp_start"] = _snap_int(victim_slot, "cur")
            st["attack_move"] = _snap_move(attacker_slot)
            st["attack_guard_label"] = _snap_attack_property_label(attacker_slot)
            st["attacker_name"] = _snap_name(attacker_slot)
            st["victim_name"] = _snap_name(victim_slot)
            st["first_contact_blocked"] = True
            st["guard_indicator_pending"] = False
            st["guard_indicator_emitted"] = False
            _emit_first_contact_badge(st, attacker_slot, victim_slot)
            return
        if bool(st.get("guard_indicator_pending", False)) and not bool(st.get("guard_indicator_emitted", False)):
            _emit_first_contact_badge(st, attacker_slot, victim_slot)
        return
    st["victim_hp_start"] = _snap_int(victim_slot, "cur")
    st["attack_move"] = _snap_move(attacker_slot)
    st["attack_guard_label"] = _snap_attack_property_label(attacker_slot)
    st["attacker_name"] = _snap_name(attacker_slot)
    st["victim_name"] = _snap_name(victim_slot)
    st["first_contact_blocked"] = current_contact_blocked
    st["guard_indicator_pending"] = False
    st["guard_indicator_emitted"] = False
    st["combo_property_locked"] = True
    _emit_first_contact_badge(st, attacker_slot, victim_slot)


def _set_guard_indicator(slot_label: str, label: str, hit: bool) -> None:
    slot_anim = _get_slot_anim(slot_label)
    guard_label = str(label or "").strip().upper() or "UNKNOWN"
    slot_anim["guard_indicator_label"] = guard_label
    slot_anim["guard_indicator_result"] = "HIT" if hit else "BLOCK"
    slot_anim["guard_indicator_life"] = 1.0
    slot_anim["guard_indicator_flash"] = 1.0
    _trigger_team_panel_fx(_team_from_slot(slot_label), (255, 110, 110) if hit else (92, 232, 146), 1.05 if hit else 0.85, 8 if hit else 6)


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
    st["combo_property_locked"] = False
    st["attack_guard_label"] = ""
    st["attack_move"] = ""
    st["first_contact_blocked"] = False
    st["guard_indicator_pending"] = False
    st["guard_indicator_emitted"] = False


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
            "attack_guard_label": "",
            "combo_property_locked": False,
            "first_contact_blocked": False,
            "guard_indicator_pending": False,
            "guard_indicator_emitted": False,
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
            if _is_actionable(a_mv) and _is_actionable(v_mv):
                st["combo_property_locked"] = False
                st["attack_guard_label"] = ""
                st["attack_move"] = ""
                st["first_contact_blocked"] = False
                st["guard_indicator_pending"] = False
                st["guard_indicator_emitted"] = False
            if _is_attacking(a_mv) and _is_stuck(v_mv):
                st["state"]      = 1
                st["first_end"]  = None
                st["first_slot"] = None
                _begin_adv_contact(st, a_slot, v_slot)

        elif st["state"] == 1:
            if bool(st.get("guard_indicator_pending", False)) and not bool(st.get("guard_indicator_emitted", False)):
                _emit_first_contact_badge(st, a_slot, v_slot)

            # New hit or move-id change  -  reset timer, stay tracking
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
            # Attacker hit again before adv resolved  -  discard stale timer, restart
            if _is_attacking(a_mv) and _is_stuck(v_mv):
                st["first_end"]  = None
                st["first_slot"] = None
                st["state"]      = 1 if _is_attacking(prev_a) else 0
                continue

            # Attacker went idle for a frame but victim still stuck  -  they're in a
            # blockstring gap. Don't resolve yet; if they start attacking again,
            # state 1 above will catch it. If victim recovers first the module resolve below.
            if st["first_slot"] == "A" and _is_stuck(v_mv) and not _is_attacking(a_mv):
                # attacker is in gap  -  wait, don't commit yet
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
        "input_history": [],
        "input_chips": [],
        "pending_input_chip_tokens": [],
        "pending_input_chip_start_frame": None,
        "pending_input_last_frame": None,
        "input_chip_break": False,
        "button_hold_active": {},
        "button_hold_events": [],
        "button_hold_seq": 0,
        "qualified_hold_mask": 0,
        "prev_input_state": None,
        "prev_visible_input_state": None,
        "prev_input_key": None,
        "last_input_frame": -9999,
        "last_input_sample_seq": 0,
        "hp_display_frac": None,
        "partner_hp_display_frac": None,
        "meter_display_value": None,
        "baroque_alpha": 0.0,
        "baroque_fade_direction": 0,
        "event_history": [],
        "prev_compact_move_key": "",
        "guard_indicator_label": "",
        "guard_indicator_result": "",
        "guard_indicator_life": 0.0,
        "guard_indicator_flash": 0.0,
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
        "input_history_signature": (),
        "input_history_prev": [],
        "input_history_current": [],
        "input_history_slide": 0.0,
        "hold_history_signature": (),
        "hold_history_prev": [],
        "hold_history_current": [],
        "hold_history_slide": 0.0,
        "hold_expand": 0.0,
        "log_history_signature": (),
        "log_history_prev": [],
        "log_history_slide": 0.0,
        "pulse_life": 0.0,
        "pulse_color": (107, 154, 232),
        "sweep_pos": -0.25,
        "shake": 0.0,
        "sparks": [],
        "tag_card": None,
    })


def _push_event_history(
    slot_anim: dict,
    label: str,
    value: str,
    color: tuple[int, int, int],
    rainbow: bool = False,
) -> None:
    items = slot_anim["event_history"]
    items.insert(0, {
        "label": label,
        "value": value,
        "color": color,
        "life": 1.0,
        "rainbow": bool(rainbow),
    })
    del items[6:]


def _team_from_slot(slot_label: str) -> str:
    return "P1" if str(slot_label).startswith("P1") else "P2"


def _trigger_team_panel_fx(team: str, color: tuple[int, int, int], strength: float = 1.0, spark_count: int = 6) -> None:
    team_anim = _get_team_anim(team)
    strength = max(0.25, min(1.6, float(strength or 1.0)))
    team_anim["pulse_life"] = max(float(team_anim.get("pulse_life", 0.0)), min(0.72, (0.28 + 0.14 * strength) * OOMPH_SCALE / 0.40))
    team_anim["pulse_color"] = tuple(max(0, min(255, int(c))) for c in (color or (107, 154, 232)))
    team_anim["sweep_pos"] = -0.20
    team_anim["shake"] = max(float(team_anim.get("shake", 0.0)), min(0.42, (0.14 + 0.18 * strength) * OOMPH_SCALE / 0.40))
    sparks = team_anim.setdefault("sparks", [])
    for _ in range(max(1, int(round(spark_count * OOMPH_SCALE)))):
        sparks.append({
            "column": random.choice(("left", "right")),
            "x": random.uniform(0.06, 0.94),
            "y": random.uniform(0.12, 0.82),
            "vx": random.uniform(-0.22, 0.22),
            "vy": random.uniform(-0.22, 0.22),
            "life": random.uniform(0.45, 0.9),
            "size": random.uniform(1.2, 3.4),
            "color": team_anim["pulse_color"],
        })
    if len(sparks) > 24:
        del sparks[:-24]


def _tick_team_panel_fx(team_anim: dict, dt: float) -> None:
    team_anim["pulse_life"] = max(0.0, float(team_anim.get("pulse_life", 0.0)) - dt * 2.25)
    team_anim["shake"] = max(0.0, float(team_anim.get("shake", 0.0)) - dt * 5.4)
    team_anim["sweep_pos"] = min(1.20, float(team_anim.get("sweep_pos", -0.25)) + dt * 1.30)
    updated = []
    for spark in list(team_anim.get("sparks", [])):
        life = max(0.0, float(spark.get("life", 0.0)) - dt * 1.6)
        if life <= 0.01:
            continue
        spark["life"] = life
        spark["x"] = float(spark.get("x", 0.0)) + float(spark.get("vx", 0.0)) * dt
        spark["y"] = float(spark.get("y", 0.0)) + float(spark.get("vy", 0.0)) * dt
        spark["vy"] = float(spark.get("vy", 0.0)) * 0.985
        if -0.2 <= float(spark.get("x", 0.0)) <= 1.2 and -0.2 <= float(spark.get("y", 0.0)) <= 1.2:
            updated.append(spark)
    team_anim["sparks"] = updated[-24:]


def _team_panel_fx_columns(x: int, y: int, width: int, height: int, scale: float) -> tuple[pygame.Rect, pygame.Rect]:
    inset_x = max(4, int(5 * scale))
    inset_y = max(6, int(8 * scale))
    col_w = max(max(18, int(22 * scale)), min(int(width * 0.12), int(56 * scale)))
    inner_h = max(8, height - inset_y * 2)
    left_rect = pygame.Rect(x + inset_x, y + inset_y, col_w, inner_h)
    right_rect = pygame.Rect(x + width - inset_x - col_w, y + inset_y, col_w, inner_h)
    return left_rect, right_rect


def _draw_team_panel_fx(screen, team_anim: dict, x: int, y: int, width: int, height: int, accent: tuple[int, int, int], scale: float, alpha: float) -> None:
    pulse = max(0.0, min(1.0, float(team_anim.get("pulse_life", 0.0))))
    if pulse <= 0.01 and not team_anim.get("sparks"):
        return
    fx_color = tuple(team_anim.get("pulse_color") or accent or (107, 154, 232))
    radius = max(6, int(7 * scale))
    left_col, right_col = _team_panel_fx_columns(x, y, width, height, scale)
    columns = (left_col, right_col)
    if pulse > 0.01:
        glow = pygame.Surface((width + int(18 * scale), height + int(18 * scale)), pygame.SRCALPHA)
        glow_rect = pygame.Rect(int(9 * scale), int(9 * scale), width, height)
        pygame.draw.rect(glow, (*fx_color, int(14 * pulse * alpha)), glow_rect, border_radius=radius + 3)
        pygame.draw.rect(glow, (*fx_color, int(31 * pulse * alpha)), glow_rect, 2, border_radius=radius + 3)
        screen.blit(glow, (x - int(9 * scale), y - int(9 * scale)))

        sweep = float(team_anim.get("sweep_pos", -0.25))
        if -0.05 <= sweep <= 1.20:
            for col_rect in columns:
                sheen = pygame.Surface((col_rect.width, col_rect.height), pygame.SRCALPHA)
                center_x = int((sweep * col_rect.width))
                band_w = max(10, int(col_rect.width * 0.90))
                poly = [
                    (center_x - band_w, 0),
                    (center_x + int(band_w * 0.40), 0),
                    (center_x - int(band_w * 0.40), col_rect.height),
                    (center_x - int(band_w * 1.40), col_rect.height),
                ]
                pygame.draw.polygon(sheen, (*fx_color, int(10 * pulse * alpha)), poly)
                pygame.draw.line(sheen, (255, 255, 255, int(7 * pulse * alpha)), (max(0, center_x - band_w), 2), (min(col_rect.width - 1, center_x + int(band_w * 0.25)), 2), 1)
                screen.blit(sheen, col_rect.topleft)

    for spark in list(team_anim.get("sparks", [])):
        life = max(0.0, min(1.0, float(spark.get("life", 0.0))))
        if life <= 0.01:
            continue
        col_rect = left_col if str(spark.get("column", "left")) == "left" else right_col
        sx = col_rect.x + int(float(spark.get("x", 0.0)) * col_rect.width)
        sy = col_rect.y + int(float(spark.get("y", 0.0)) * col_rect.height)
        size = max(1, int(float(spark.get("size", 1.0)) * scale * (0.70 + life * 0.55)))
        spark_color = tuple(spark.get("color") or fx_color)
        pygame.draw.circle(screen, (*spark_color, int(70 * life * alpha)), (sx, sy), size)
        pygame.draw.line(screen, (*spark_color, int(48 * life * alpha)), (sx - size, sy), (sx + size, sy), 1)



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

_last_data_signature: tuple[int, int, int, int] | None = None
_cached_slots: dict = {}

def read_slot_data() -> dict:
    global _last_data_signature, _cached_slots
    try:
        stat = os.stat(DATA_FILE)
        signature = (
            int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000))),
            int(getattr(stat, "st_ctime_ns", int(stat.st_ctime * 1_000_000_000))),
            int(stat.st_size),
            int(getattr(stat, "st_ino", 0)),
        )
        if signature != _last_data_signature:
            with open(DATA_FILE, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                _cached_slots = loaded
                _last_data_signature = signature
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
        _push_event_history(slot_anim, "BBQ", f"{delta:+.0f}%", (255, 180, 92) if delta < 0 else (172, 112, 255), rainbow=True)
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
    return f"{cur_i}/{max_i}"


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


def _track_compact_input_packet(
    slot_anim: dict,
    input_held: int,
    input_pressed: int,
    input_released: int,
    frame_number: int,
) -> None:
    """Record one raw input sample without dropping repeated direction taps."""
    input_held = int(input_held) & 0xFFFF
    input_pressed = int(input_pressed) & 0xFFFF
    input_released = int(input_released) & 0xFFFF
    frame_number = int(frame_number)

    _update_button_hold_log(slot_anim, input_held, frame_number)

    current_input_state = input_held & INPUT_TRACK_MASK
    direction_key = current_input_state & INPUT_DIRECTION_MASK
    held_buttons = current_input_state & INPUT_BUTTON_MASK
    previous_input_state = slot_anim.get("prev_input_state")
    previous_visible_state = slot_anim.get("prev_visible_input_state")
    input_history = slot_anim["input_history"]

    if previous_input_state is None:
        previous_input_state_int = current_input_state
    else:
        previous_input_state_int = int(previous_input_state) & INPUT_TRACK_MASK

    previous_qualified_mask = int(slot_anim.get("qualified_hold_mask", 0)) & INPUT_BUTTON_MASK
    qualified_hold_mask = _qualified_button_hold_mask(slot_anim, frame_number)
    newly_qualified_mask = qualified_hold_mask & ~previous_qualified_mask
    slot_anim["qualified_hold_mask"] = qualified_hold_mask

    # The game's pressed/released words can remain asserted for a complete game
    # frame. The overlay may sample that frame more than once, so only accept the
    # newly observed edge bits. A real second tap is still preserved because the
    # edge word clears, or the held state returns to neutral, between taps.
    previous_raw_pressed = int(slot_anim.get("prev_raw_pressed", 0) or 0) & INPUT_BUTTON_MASK
    previous_raw_released = int(slot_anim.get("prev_raw_released", 0) or 0) & INPUT_BUTTON_MASK
    raw_pressed = input_pressed & INPUT_BUTTON_MASK
    raw_released = input_released & INPUT_BUTTON_MASK
    fresh_raw_pressed = raw_pressed & ~previous_raw_pressed
    fresh_raw_released = raw_released & ~previous_raw_released

    derived_pressed = current_input_state & ~previous_input_state_int & INPUT_BUTTON_MASK
    derived_released = previous_input_state_int & ~current_input_state & INPUT_BUTTON_MASK
    pressed_buttons = (fresh_raw_pressed | derived_pressed) & INPUT_BUTTON_MASK
    released_buttons = (fresh_raw_released | derived_released) & INPUT_BUTTON_MASK

    visible_held_buttons = held_buttons & (~qualified_hold_mask & INPUT_BUTTON_MASK)
    visible_input_state = direction_key | visible_held_buttons

    if previous_visible_state is None:
        previous_visible_state_int = visible_input_state
    else:
        previous_visible_state_int = int(previous_visible_state) & INPUT_TRACK_MASK

    visible_pressed_buttons = pressed_buttons & (~qualified_hold_mask & INPUT_BUTTON_MASK)
    hidden_hold_edges = previous_qualified_mask | qualified_hold_mask
    visible_released_buttons = released_buttons & (~hidden_hold_edges & INPUT_BUTTON_MASK)

    input_events: list[tuple[str, str]] = []
    previous_direction = previous_visible_state_int & INPUT_DIRECTION_MASK
    direction_changed = direction_key != previous_direction

    if visible_pressed_buttons:
        press_state = direction_key | visible_held_buttons | visible_pressed_buttons
        input_events.append((
            "press",
            _format_overlay_input_token(
                press_state & INPUT_DIRECTION_MASK,
                press_state & INPUT_BUTTON_MASK,
            ),
        ))

    if direction_changed:
        if direction_key == 0 and not visible_held_buttons:
            # Neutral is the separator between taps, not an input-history chip.
            _freeze_active_input_chip(slot_anim, frame_number)
        else:
            direction_text = _format_overlay_input_token(
                visible_input_state & INPUT_DIRECTION_MASK,
                visible_input_state & INPUT_BUTTON_MASK,
            )
            if not input_events or input_events[-1][1] != direction_text:
                input_events.append(("direction", direction_text))

    if newly_qualified_mask or visible_released_buttons:
        _freeze_active_input_chip(slot_anim, frame_number)

    if previous_input_state is None and visible_input_state and not input_events:
        input_events.append((
            "initial",
            _format_overlay_input_token(
                visible_input_state & INPUT_DIRECTION_MASK,
                visible_input_state & INPUT_BUTTON_MASK,
            ),
        ))

    for event_kind, input_text in input_events:
        if not input_text:
            continue
        # Keep every real state transition. A direction pressed on one frame and
        # a button pressed later are two distinct inputs. Same-frame direction +
        # button packets already arrive as one combined token above.
        input_history.append(input_text)
        _append_input_chip_token(slot_anim, input_text, frame_number)
        slot_anim["last_input_frame"] = frame_number

    slot_anim["prev_input_state"] = current_input_state
    slot_anim["prev_visible_input_state"] = visible_input_state
    slot_anim["prev_input_key"] = direction_key
    slot_anim["prev_raw_pressed"] = raw_pressed
    slot_anim["prev_raw_released"] = raw_released
    del input_history[:-12]


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
                _trigger_team_panel_fx(_team_from_slot(slot_label), (255, 110, 110), min(1.35, 0.55 + damage / 2400.0), 10)
                _combo_register_damage(slot_label, damage)
                opponent = _get_active_slot("P2" if slot_label.startswith("P1") else "P1")
                if opponent:
                    opp_anim = _get_slot_anim(opponent)
                    other = opp_anim["damage_events"]
                    other.insert(0, {"value": damage, "life": 1.0, "age": 0.0, "x_offset": 0, "type": "opponent"})
                    _push_event_history(opp_anim, "DMG OUT", _compact_short_number(damage), (255, 110, 110))
                    _trigger_team_panel_fx(_team_from_slot(opponent), (255, 132, 92), min(1.05, 0.45 + damage / 3200.0), 7)
                    del other[3:]
            else:
                events.insert(0, {"value": hp_delta, "life": 1.0, "age": 0.0, "x_offset": 0, "type": "heal"})
                _push_event_history(slot_anim, "HP +", _compact_short_number(hp_delta), (92, 232, 146))
                _trigger_team_panel_fx(_team_from_slot(slot_label), (92, 232, 146), 0.70, 5)
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
            _trigger_team_panel_fx(_team_from_slot(slot_label), (96, 182, 255) if meter_delta > 0 else (255, 164, 92), 0.55, 4)
            del meter_events[3:]
    slot_anim["prev_meter"] = meter_cur

    try:
        baroque_cur = float(snap.get("baroque_red_pct_max") or 0.0)
    except (TypeError, ValueError):
        baroque_cur = 0.0
    prev_baroque = slot_anim.get("prev_baroque_pct")
    if prev_baroque is not None:
        baroque_delta = baroque_cur - float(prev_baroque)
        if abs(baroque_delta) >= 0.05:
            baroque_events = slot_anim["baroque_events"]
            baroque_events.insert(0, {
                "value": baroque_delta,
                "direction": "gain" if baroque_delta > 0 else "loss",
                "life": 1.0,
                "age": 0.0,
                "x_offset": 0,
            })
            baroque_text = f"{'+' if baroque_delta > 0 else '-'}{abs(baroque_delta):.1f}%"
            baroque_color = (255, 211, 92) if baroque_delta > 0 else (255, 132, 104)
            _push_event_history(slot_anim, "BBQ", baroque_text, baroque_color, rainbow=True)
            _trigger_team_panel_fx(_team_from_slot(slot_label), (172, 112, 255) if baroque_delta > 0 else (255, 180, 92), 0.85, 7)
            del baroque_events[3:]
    slot_anim["prev_baroque_pct"] = baroque_cur

    try:
        input_held = int(snap.get("input_held") or 0) & 0xFFFF
        input_pressed = int(snap.get("input_pressed") or 0) & 0xFFFF
        input_released = int(snap.get("input_released") or 0) & 0xFFFF
    except (TypeError, ValueError):
        input_held = 0
        input_pressed = 0
        input_released = 0

    raw_samples = snap.get("input_samples")
    samples = [item for item in raw_samples if isinstance(item, dict)] if isinstance(raw_samples, list) else []
    last_sample_seq = int(slot_anim.get("last_input_sample_seq", 0) or 0)
    max_available_seq = max((int(item.get("seq", 0) or 0) for item in samples), default=0)
    if max_available_seq and max_available_seq < last_sample_seq:
        # Producer restarted, so its sequence counter began again.
        last_sample_seq = 0

    pending_samples = [
        item for item in samples
        if int(item.get("seq", 0) or 0) > last_sample_seq
    ]
    pending_samples.sort(key=lambda item: int(item.get("seq", 0) or 0))

    if pending_samples:
        sample_count = len(pending_samples)
        for sample_index, sample in enumerate(pending_samples):
            sample_frame = int(_frame) - (sample_count - sample_index - 1)
            _track_compact_input_packet(
                slot_anim,
                int(sample.get("held", 0) or 0) & 0xFFFF,
                int(sample.get("pressed", 0) or 0) & 0xFFFF,
                int(sample.get("released", 0) or 0) & 0xFFFF,
                sample_frame,
            )
        slot_anim["last_input_sample_seq"] = int(pending_samples[-1].get("seq", 0) or 0)
    else:
        # Continue active frame counters and hold qualification while unchanged.
        _track_compact_input_packet(
            slot_anim, input_held, input_pressed, input_released, int(_frame)
        )

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

    slot_anim["guard_indicator_life"] = max(0.0, float(slot_anim.get("guard_indicator_life", 0.0)) - 0.008)
    slot_anim["guard_indicator_flash"] = max(0.0, float(slot_anim.get("guard_indicator_flash", 0.0)) - 0.030)


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


def _update_compact_baroque_anim(slot_anim: dict, snap: dict, is_dead: bool, dt: float) -> bool:
    ready = bool((snap or {}).get("baroque_ready_local", False)) and not bool(is_dead)
    previous_alpha = float(slot_anim.get("baroque_alpha", 0.0))
    if ready:
        slot_anim["baroque_last_pct"] = float((snap or {}).get("baroque_red_pct_max") or 0.0)
        slot_anim["baroque_display_pct"] = _approach(
            float(slot_anim.get("baroque_display_pct", 0.0)),
            float(slot_anim.get("baroque_last_pct", 0.0)),
            80.0,
            dt,
        )
        slot_anim["baroque_alpha"] = _approach(previous_alpha, 1.0, 5.5, dt)
    else:
        slot_anim["baroque_alpha"] = _approach(previous_alpha, 0.0, 2.1, dt)
    current_alpha = float(slot_anim.get("baroque_alpha", 0.0))
    if current_alpha > previous_alpha + 0.0001:
        slot_anim["baroque_fade_direction"] = 1
    elif current_alpha < previous_alpha - 0.0001:
        slot_anim["baroque_fade_direction"] = -1
    else:
        slot_anim["baroque_fade_direction"] = 0
    return current_alpha > 0.03


def _draw_compact_baroque_badge(screen, font_sm, rect: pygame.Rect, percent: float, scale: float, is_left: bool, alpha: float = 1.0, fade_direction: int = 0, owner_label: str = "") -> None:
    pct = max(0.0, float(percent or 0.0))
    fade = max(0.0, min(1.0, float(alpha or 0.0)))
    if fade <= 0.01 or rect.width <= 2 or rect.height <= 2:
        return

    entering = int(fade_direction or 0) > 0
    leaving = int(fade_direction or 0) < 0
    # Entry is revealed by the wipe itself, so avoid multiplying the content
    # down to near-invisible alpha a second time. Exit keeps the approved fade.
    visual_fade = min(1.0, 0.34 + 0.66 * math.sqrt(fade)) if entering else fade

    radius = max(4, int(5 * scale))
    pulse = 0.70 + 0.30 * ((math.sin(time.time() * 3.8) + 1.0) * 0.5)
    border = _compact_rainbow_color(0.16 + pct * 0.004, 1.0)
    border_glow = _compact_rainbow_color(0.48 + pct * 0.003, 1.15)

    glow = pygame.Surface((rect.width + max(6, int(8 * scale)), rect.height + max(6, int(8 * scale))), pygame.SRCALPHA)
    glow_rect = pygame.Rect(max(3, int(4 * scale)), max(3, int(4 * scale)), rect.width, rect.height)
    pygame.draw.rect(glow, (*border_glow, int(22 * visual_fade * pulse)), glow_rect, border_radius=radius + 2)
    screen.blit(glow, (rect.x - glow_rect.x, rect.y - glow_rect.y))

    badge = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
    pygame.draw.rect(badge, (14, 18, 28, int(212 * visual_fade)), badge.get_rect(), border_radius=radius)
    pygame.draw.rect(badge, (22, 27, 39, int(188 * visual_fade)), badge.get_rect(), 1, border_radius=radius)
    pygame.draw.rect(badge, (*border, int(210 * visual_fade)), pygame.Rect(1, 1, max(1, rect.width - 2), max(2, int(2 * scale))), border_top_left_radius=radius, border_top_right_radius=radius)

    label_pad_x = max(5, int(6 * scale))
    label_gap = max(4, int(5 * scale))
    owner = str(owner_label or "").strip().upper()
    badge_label = f"{owner} BBQ" if owner else "BBQ"
    label_text = font_sm.render(badge_label, True, (225, 232, 246))
    value_text = _render_compact_rainbow_text(font_sm, f"{pct:.2f}%", 0.18)

    chip_h = max(int(13 * scale), label_text.get_height() + max(2, int(3 * scale)))
    chip_w = max(int(24 * scale), label_text.get_width() + label_pad_x * 2)
    chip_y = badge.get_height() // 2 - chip_h // 2
    chip_x = max(4, int(5 * scale))
    chip_rect = pygame.Rect(chip_x, chip_y, chip_w, chip_h)
    pygame.draw.rect(badge, (34, 42, 60, int(216 * visual_fade)), chip_rect, border_radius=max(3, int(4 * scale)))
    pygame.draw.rect(badge, (*border, int(148 * visual_fade)), chip_rect, 1, border_radius=max(3, int(4 * scale)))

    label_text.set_alpha(int(245 * visual_fade))
    value_text.set_alpha(int(255 * visual_fade))
    badge.blit(label_text, (chip_rect.centerx - label_text.get_width() // 2, chip_rect.centery - label_text.get_height() // 2))

    value_x = chip_rect.right + label_gap
    available_w = rect.width - value_x - max(5, int(6 * scale))
    if value_text.get_width() > available_w and available_w > 8:
        scale_mul = available_w / max(1, value_text.get_width())
        value_text = pygame.transform.smoothscale(value_text, (max(1, int(value_text.get_width() * scale_mul)), max(1, int(value_text.get_height() * scale_mul))))
        value_text.set_alpha(int(255 * visual_fade))
    badge.blit(value_text, (value_x, badge.get_height() // 2 - value_text.get_height() // 2))

    if pct >= 1.0:
        marker_r = max(2, int(2 * scale))
        marker_x = rect.width - max(7, int(8 * scale))
        marker_y = badge.get_height() // 2
        pygame.draw.circle(badge, (*border_glow, int(210 * visual_fade * pulse)), (marker_x, marker_y), marker_r)

    if fade < 0.995:
        soft_w = max(10, int(16 * scale))
        if is_left:
            solid_w = max(0, min(rect.width, int(rect.width * fade)))
            edge_start = solid_w
            edge_end = min(rect.width, solid_w + soft_w)
            wipe_center = edge_start + soft_w * 0.45
        else:
            solid_start = max(0, rect.width - int(rect.width * fade))
            edge_start = max(0, solid_start - soft_w)
            edge_end = solid_start
            wipe_center = edge_end - soft_w * 0.45

        mask = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        for col in range(rect.width):
            col_alpha = 0
            if is_left:
                if col < edge_start:
                    col_alpha = 255
                elif col < edge_end:
                    t = 1.0 - ((col - edge_start) / max(1, edge_end - edge_start))
                    col_alpha = int(255 * t)
            else:
                if col >= edge_end:
                    col_alpha = 255
                elif col >= edge_start:
                    t = (col - edge_start) / max(1, edge_end - edge_start)
                    col_alpha = int(255 * t)
            if col_alpha > 0:
                pygame.draw.line(mask, (255, 255, 255, col_alpha), (col, 0), (col, rect.height - 1))
        badge.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)

        wipe = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        if entering:
            # Softest at the endpoints, clearest while the reveal edge crosses.
            wipe_alpha = max(0.0, min(1.0, math.sin(math.pi * fade)))
        elif leaving:
            wipe_alpha = max(0.0, min(1.0, 1.0 - fade))
        else:
            wipe_alpha = max(0.0, min(1.0, 1.0 - fade))
        band_w = max(10, int(18 * scale))
        for idx in range(4):
            color = _compact_rainbow_color(0.12 + idx * 0.12 + time.time() * 0.08, 1.0)
            offset = (idx - 1.5) * max(2, int(3 * scale))
            cx = wipe_center + offset
            poly = [
                (int(cx - band_w), 0),
                (int(cx + band_w * 0.35), 0),
                (int(cx - band_w * 0.25), rect.height),
                (int(cx - band_w * 1.60), rect.height),
            ]
            pygame.draw.polygon(wipe, (*color, int((18 + idx * 6) * wipe_alpha)), poly)
        badge.blit(wipe, (0, 0))

    screen.blit(badge, rect.topleft)


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
    rainbow: bool = False,
) -> int:
    if rainbow:
        label_surface = _render_compact_rainbow_text(font_sm, label, 0.10)
        value_surface = _render_compact_rainbow_text(font_sm, value, 0.48)
    else:
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
    border = _compact_rainbow_color(0.28, 1.1) if rainbow else _compact_chip_color(color, life)
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
    chips: list[tuple[str, str, tuple[int, int, int], float, dict | None, bool]] = []

    if damage_event is not None:
        event_type = str(damage_event.get("type") or "")
        if event_type == "opponent":
            label, color = "DMG OUT", (255, 110, 110)
        elif event_type == "heal":
            label, color = "HP +", (92, 232, 146)
        else:
            label, color = "DMG IN", (255, 110, 110)
        chips.append((label, _compact_short_number(damage_event.get("value", 0)), color, float(damage_event.get("life", 1.0)), damage_event, False))

    if meter_event is not None:
        direction = str(meter_event.get("direction") or "gain")
        gain = direction != "loss"
        chips.append(("MTR", f"{'+' if gain else '-'}{_compact_short_number(meter_event.get('value', 0))}", (96, 182, 255) if gain else (255, 164, 92), float(meter_event.get("life", 1.0)), meter_event, False))

    if advantage_event is not None:
        value = int(advantage_event.get("value", 0))
        value_text = f"{value:+d}" if value else "0"
        color = (92, 232, 146) if value > 0 else ((255, 112, 112) if value < 0 else (196, 205, 220))
        chips.append(("FRAME", value_text, color, float(advantage_event.get("life", 1.0)), advantage_event, False))

    if baroque_event is not None:
        value = float(baroque_event.get("value", 0.0))
        value_text = f"{value:+.0f}%"
        color = (172, 112, 255) if value > 0 else (255, 180, 92)
        chips.append(("BBQ", value_text, color, float(baroque_event.get("life", 1.0)), baroque_event, True))

    action_text, action_color, action_kind = _compact_action_chip(action_label)
    draw_x = x
    gap = max(4, int(5 * scale))
    for label, value, color, life, event, rainbow in chips:
        if rainbow:
            label_surface = _render_compact_rainbow_text(font_sm, label, 0.10)
            value_surface = _render_compact_rainbow_text(font_sm, value, 0.48)
        else:
            label_surface = font_sm.render(label, True, (142, 151, 169))
            value_surface = font_sm.render(value, True, color)
        width = label_surface.get_width() + value_surface.get_width() + max(3, int(4 * scale)) + max(8, int(10 * scale))
        if draw_x + width > right:
            continue
        used = _draw_compact_stat_chip(screen, font_sm, draw_x, y, label, value, color, scale, life, event, rainbow=rainbow)
        draw_x += used + gap

    if action_text and draw_x < right:
        label_surface = font_sm.render(action_kind, True, (142, 151, 169))
        pad_x = max(4, int(5 * scale))
        text_gap = max(3, int(4 * scale))
        available = right - draw_x - label_surface.get_width() - pad_x * 2 - text_gap
        value = _compact_fit_text(font_sm, action_text, available)
        if value:
            _draw_compact_stat_chip(screen, font_sm, draw_x, y, action_kind, value, action_color, scale, 1.0)


def _render_compact_text_chip(font_sm, primary: str, color: tuple[int, int, int], scale: float, alpha: float = 1.0, secondary: str = "", rainbow: bool = False, emphasis: float = 1.0) -> pygame.Surface:
    pad_x = max(5, int(6 * scale))
    pad_y = max(2, int(3 * scale))
    inner_gap = max(3, int(4 * scale))
    border_radius = max(5, int(6 * scale))

    prim = str(primary or "").strip()
    sec = str(secondary or "").strip()
    if rainbow and prim:
        prim_surf = _render_compact_rainbow_text(font_sm, prim, 0.24)
    else:
        prim_surf = font_sm.render(prim, True, color)
    if emphasis != 1.0:
        prim_surf = pygame.transform.smoothscale(prim_surf, (max(1, int(prim_surf.get_width() * emphasis)), max(1, int(prim_surf.get_height() * emphasis))))
    if sec:
        sec_surf = font_sm.render(sec, True, (156, 166, 184))
    else:
        sec_surf = None

    width = prim_surf.get_width() + pad_x * 2
    height = prim_surf.get_height() + pad_y * 2
    if sec_surf is not None:
        width += inner_gap + sec_surf.get_width()
        height = max(height, sec_surf.get_height() + pad_y * 2)

    surf = pygame.Surface((max(1, width), max(1, height)), pygame.SRCALPHA)
    bg_alpha = max(0, min(255, int(168 * alpha)))
    border_alpha = max(0, min(255, int(116 * alpha)))
    pygame.draw.rect(surf, (26, 31, 40, bg_alpha), (0, 0, width, height), border_radius=border_radius)
    pygame.draw.rect(surf, (70, 84, 104, border_alpha), (0, 0, width, height), 1, border_radius=border_radius)
    pygame.draw.rect(
        surf,
        (*color, max(0, min(255, int(90 * alpha)))),
        (1, 1, max(1, width - 2), max(2, int(2 * scale))),
        border_top_left_radius=border_radius,
        border_top_right_radius=border_radius,
    )
    if alpha < 0.999:
        prim_surf.set_alpha(max(0, min(255, int(255 * alpha))))
        if sec_surf is not None:
            sec_surf.set_alpha(max(0, min(255, int(255 * alpha))))
    dx = pad_x
    surf.blit(prim_surf, (dx, (height - prim_surf.get_height()) // 2))
    dx += prim_surf.get_width()
    if sec_surf is not None:
        dx += inner_gap
        surf.blit(sec_surf, (dx, (height - sec_surf.get_height()) // 2))
    return surf


def _history_label_width(font_sm, scale: float) -> int:
    labels = ("LOG", "INPUTS", "FRAMES", "HOLD", "MOVES")
    return max(font_sm.size(label)[0] for label in labels) + max(16, int(20 * scale))


def _draw_history_header_chip(screen, font_sm, title: str, x: int, y: int, scale: float) -> int:
    label_w = _history_label_width(font_sm, scale)
    label_h = max(font_sm.get_height() + max(4, int(5 * scale)), int(15 * scale))
    key = (id(font_sm), str(title), label_w, label_h, round(float(scale), 3))
    chip = _HISTORY_HEADER_CHIP_CACHE.get(key)
    if chip is None:
        radius = max(3, int(4 * scale))
        chip = pygame.Surface((label_w, label_h), pygame.SRCALPHA)
        pygame.draw.rect(chip, (16, 21, 28, 188), (0, 0, label_w, label_h), border_radius=radius)
        pygame.draw.rect(chip, (52, 67, 92, 148), (0, 0, label_w, label_h), 1, border_radius=radius)
        accent_w = max(3, int(4 * scale))
        pygame.draw.rect(chip, (86, 142, 228, 190), (0, 0, accent_w, label_h), border_top_left_radius=radius, border_bottom_left_radius=radius)
        pygame.draw.line(chip, (238, 242, 248, 48), (accent_w + 2, 1), (label_w - 3, 1), 1)
        label_surface = font_sm.render(title, True, (194, 208, 228))
        chip.blit(label_surface, (accent_w + max(6, int(7 * scale)), (label_h - label_surface.get_height()) // 2))
        if len(_HISTORY_HEADER_CHIP_CACHE) >= 32:
            _HISTORY_HEADER_CHIP_CACHE.clear()
        _HISTORY_HEADER_CHIP_CACHE[key] = chip
    screen.blit(chip, (x, y))
    return label_w


def _draw_compact_history_line(screen, font_sm, title: str, items: list[dict], x: int, y: int, right: int, scale: float, prev_items: list[dict] | None = None, slide_progress: float = 0.0) -> None:
    label_w = _draw_history_header_chip(screen, font_sm, title, x, y, scale)
    draw_x = x + label_w + max(6, int(7 * scale))
    gap = max(6, int(7 * scale))
    clip_rect = pygame.Rect(draw_x, y - 1, max(1, right - draw_x), max(font_sm.get_height() + int(8 * scale), int(18 * scale)))

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
                "rainbow": bool(item.get("rainbow", False) or label.upper() == "BBQ"),
            })
            if len(out) >= 4:
                break
        return out

    def _render_parts(source_items, alpha_override=None):
        rendered = []
        for idx, item in enumerate(source_items):
            alpha = max(0.35, min(1.0, alpha_override if alpha_override is not None else item.get("life", 1.0)))
            primary = f"{item.get('label','')} {item.get('value','')}".strip()
            color = item.get("color") or (196, 205, 220)
            surf = _render_compact_text_chip(font_sm, primary, color, scale, alpha, rainbow=item.get("rainbow", False), emphasis=1.04 if idx == 0 else 1.0)
            rendered.append(surf)
        return rendered

    current_items = _norm(items)
    previous_items = _norm(prev_items)
    if not current_items and not previous_items:
        empty = font_sm.render(" - ", True, (86, 96, 114))
        screen.blit(empty, (draw_x, y))
        return

    current_parts = _render_parts(current_items, 1.0)
    previous_parts = _render_parts(previous_items, max(0.0, min(1.0, slide_progress))) if previous_items else []
    inserted_shift = max(int(30 * scale), current_parts[0].get_width() + gap if current_parts else int(30 * scale))

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




HOLD_LOG_LIFETIME_FRAMES = 80
HOLD_LOG_MIN_FRAMES = 26

_BUTTON_HOLD_SPECS = (
    ("A", 0x0080),
    ("B", 0x0040),
    ("C", 0x0020),
    ("P", 0x0010),
    ("T", 0x0C00),
)

_DIRECTION_HOLD_SPECS = {
    0x1: "6",
    0x2: "4",
    0x4: "8",
    0x5: "9",
    0x6: "7",
    0x8: "2",
    0x9: "3",
    0xA: "1",
}

_BUTTON_HOLD_COLORS = {
    "A": (92, 232, 146),
    "B": (132, 204, 255),
    "C": (255, 148, 112),
    "P": (190, 132, 255),
    "T": (242, 205, 92),
    "1": (134, 190, 255),
    "2": (112, 176, 255),
    "3": (134, 190, 255),
    "4": (112, 176, 255),
    "6": (112, 176, 255),
    "7": (134, 190, 255),
    "8": (112, 176, 255),
    "9": (134, 190, 255),
}


def _button_mask_is_held(input_word: int, mask: int) -> bool:
    word = int(input_word) & 0xFFFF
    if mask == INPUT_TAUNT_MASK:
        return (word & mask) == mask
    return bool(word & mask)


def _qualified_button_hold_mask(slot_anim: dict, current_frame: int) -> int:
    active = slot_anim.get("button_hold_active", {})
    qualified = 0
    for label, mask in _BUTTON_HOLD_SPECS:
        hold = active.get(label)
        if not isinstance(hold, dict):
            continue
        start_frame = int(hold.get("start_frame", current_frame))
        held_frames = max(0, int(current_frame) - start_frame)
        if held_frames >= HOLD_LOG_MIN_FRAMES:
            qualified |= int(mask)
    return qualified & INPUT_BUTTON_MASK


def _update_button_hold_log(slot_anim: dict, input_held: int, frame_number: int) -> None:
    active = slot_anim.setdefault("button_hold_active", {})
    events = slot_anim.setdefault("button_hold_events", [])

    def _begin_hold(label: str, kind: str) -> None:
        if label in active:
            return
        slot_anim["button_hold_seq"] = int(slot_anim.get("button_hold_seq", 0)) + 1
        active[label] = {
            "start_frame": int(frame_number),
            "seq": int(slot_anim["button_hold_seq"]),
            "kind": kind,
        }

    def _end_hold(label: str) -> None:
        if label not in active:
            return
        hold = active.pop(label)
        start_frame = int(hold.get("start_frame", frame_number))
        held_frames = max(0, int(frame_number) - start_frame)
        if held_frames >= HOLD_LOG_MIN_FRAMES:
            events.insert(0, {
                "label": label,
                "start_frame": start_frame,
                "end_frame": int(frame_number),
                "frames": held_frames,
                "seq": int(hold.get("seq", 0)),
                "kind": str(hold.get("kind") or "button"),
            })

    for label, mask in _BUTTON_HOLD_SPECS:
        held_now = _button_mask_is_held(input_held, mask)
        if held_now:
            _begin_hold(label, "button")
        else:
            _end_hold(label)

    direction_bits = int(input_held) & INPUT_DIRECTION_MASK
    direction_label = _DIRECTION_HOLD_SPECS.get(direction_bits, "")
    for old_label in tuple(_DIRECTION_HOLD_SPECS.values()):
        if old_label != direction_label:
            _end_hold(old_label)
    if direction_label:
        _begin_hold(direction_label, "direction")

    events[:] = [
        event
        for event in events
        if int(frame_number) - int(event.get("end_frame", frame_number)) < HOLD_LOG_LIFETIME_FRAMES
    ]
    del events[12:]


def _display_button_holds(slot_anim: dict, current_frame: int, limit: int = 8) -> list[dict]:
    out: list[dict] = []
    active = slot_anim.get("button_hold_active", {})

    active_rows = []
    for label, hold in active.items():
        start_frame = int(hold.get("start_frame", current_frame))
        held_frames = max(0, int(current_frame) - start_frame)
        if held_frames < HOLD_LOG_MIN_FRAMES:
            continue
        active_rows.append({
            "id": f"active:{label}:{int(hold.get('seq', 0))}",
            "label": label,
            "frames": held_frames,
            "active": True,
            "alpha": 1.0,
            "seq": int(hold.get("seq", 0)),
        })
    active_rows.sort(key=lambda item: item["seq"], reverse=True)
    out.extend(active_rows)

    for event in slot_anim.get("button_hold_events", []):
        age = max(0, int(current_frame) - int(event.get("end_frame", current_frame)))
        if age >= HOLD_LOG_LIFETIME_FRAMES:
            continue
        out.append({
            "id": f"done:{event.get('label')}:{int(event.get('seq', 0))}",
            "label": str(event.get("label") or "?"),
            "frames": max(0, int(event.get("frames", 0))),
            "active": False,
            "alpha": max(0.0, 1.0 - age / HOLD_LOG_LIFETIME_FRAMES),
            "seq": int(event.get("seq", 0)),
        })
        if len(out) >= limit:
            break

    return out[:limit]


def _draw_compact_hold_history(
    screen,
    font_sm,
    items: list[dict],
    x: int,
    y: int,
    right: int,
    scale: float,
    prev_items: list[dict] | None = None,
    slide_progress: float = 0.0,
) -> None:
    label_w = _draw_history_header_chip(screen, font_sm, "HOLD", x, y, scale)
    draw_x = x + label_w + max(6, int(7 * scale))
    gap = max(5, int(6 * scale))
    clip_rect = pygame.Rect(
        draw_x,
        y - 1,
        max(1, right - draw_x),
        max(font_sm.get_height() + int(8 * scale), int(18 * scale)),
    )

    def _build_parts(source, alpha_override=None):
        rendered = []
        for index, item in enumerate(list(source or [])[:8]):
            label = str(item.get("label") or "?")
            frames = max(0, int(item.get("frames") or 0))
            item_alpha = float(item.get("alpha", 1.0))
            alpha = item_alpha if alpha_override is None else min(item_alpha, float(alpha_override))
            color = _BUTTON_HOLD_COLORS.get(label, (196, 205, 220))
            primary = f"{label} {frames}f"
            secondary = "HELD" if item.get("active") else ""
            rendered.append(
                _render_compact_text_chip(
                    font_sm,
                    primary,
                    color,
                    scale,
                    max(0.0, min(1.0, alpha)),
                    secondary=secondary,
                    emphasis=1.04 if index == 0 else 1.0,
                )
            )
        return rendered

    def _draw_parts(parts, base_x):
        dx = base_x
        for index, surf in enumerate(parts):
            if dx > right:
                break
            screen.blit(surf, (dx, y))
            dx += surf.get_width()
            if index < len(parts) - 1:
                dx += gap

    current_parts = _build_parts(items)
    previous_parts = _build_parts(prev_items, max(0.0, min(1.0, slide_progress))) if prev_items else []

    if not current_parts and not previous_parts:
        empty = font_sm.render(" - ", True, (86, 96, 114))
        screen.blit(empty, (draw_x, y))
        return

    inserted_shift = max(
        int(30 * scale),
        current_parts[0].get_width() + gap if current_parts else int(30 * scale),
    )

    old_clip = screen.get_clip()
    screen.set_clip(clip_rect)
    if slide_progress > 0.001 and previous_parts:
        _draw_parts(previous_parts, draw_x + int(inserted_shift * (1.0 - slide_progress)))
        _draw_parts(current_parts, draw_x - int(inserted_shift * slide_progress))
    else:
        _draw_parts(current_parts, draw_x)
    screen.set_clip(old_clip)


def _freeze_active_input_chip(slot_anim: dict, frame_number: int) -> None:
    """Stop the active frame counter without creating a new history entry."""
    chips = slot_anim.setdefault("input_chips", [])
    if chips and chips[-1].get("end_frame") is None:
        chips[-1]["end_frame"] = int(frame_number)


def _coalesce_recent_direction_chip(
    slot_anim: dict,
    input_history: list,
    token: str,
    frame_number: int,
    *,
    max_age_frames: int = 3,
) -> bool:
    """Fold a brief setup direction into the button input that immediately follows."""
    token = str(token or "").strip()
    if not token or not _input_token_has_buttons(token):
        return False

    chips = slot_anim.setdefault("input_chips", [])
    if not chips:
        return False

    last = chips[-1]
    if last.get("end_frame") is not None:
        return False

    previous_tokens = [
        str(item or "").strip()
        for item in last.get("tokens", [])
        if str(item or "").strip()
    ]
    if len(previous_tokens) != 1:
        return False

    previous_token = previous_tokens[0]
    if _input_token_has_buttons(previous_token):
        return False

    previous_direction, _previous_buttons, _previous_extra = _split_input_token(
        previous_token
    )
    current_direction, _current_buttons, _current_extra = _split_input_token(token)
    if previous_direction != current_direction:
        return False

    start_frame = int(last.get("start_frame") or frame_number)
    age = max(0, int(frame_number) - start_frame)
    if age > max(0, int(max_age_frames)):
        return False

    last["tokens"] = [token]
    last["start_frame"] = int(frame_number)
    last["end_frame"] = None

    if input_history and str(input_history[-1] or "").strip() == previous_token:
        input_history[-1] = token
    else:
        input_history.append(token)

    return True


def _append_input_chip_token(slot_anim: dict, token: str, frame_number: int) -> None:
    """Create one timed chip per raw input change, including direction-only inputs."""
    token = str(token or "").strip()
    if not token or token == "·":
        return

    chips = slot_anim.setdefault("input_chips", [])

    # Any new raw input freezes the previous chip's counter.
    if chips and chips[-1].get("end_frame") is None:
        chips[-1]["end_frame"] = frame_number

    # Avoid double-appending the same token on the same frame from held+pressed paths.
    if chips:
        last = chips[-1]
        if last.get("tokens") == [token] and int(last.get("start_frame") or -1) == int(frame_number):
            return

    chips.append({
        "tokens": [token],
        "start_frame": frame_number,
        "end_frame": None,
    })
    del chips[:-12]

    slot_anim["pending_input_chip_tokens"] = []
    slot_anim["pending_input_chip_start_frame"] = None
    slot_anim["pending_input_last_frame"] = None
    slot_anim["input_chip_break"] = False


def _display_input_chips(slot_anim: dict, current_frame: int, limit: int = 8) -> list[dict]:
    visible: list[dict] = []

    for chip in list(slot_anim.get("input_chips", [])):
        tokens = [
            str(tok).strip()
            for tok in chip.get("tokens", [])
            if str(tok).strip() and str(tok).strip() != "·"
        ]
        if not tokens:
            continue

        start_frame = chip.get("start_frame")
        end_frame = chip.get("end_frame")
        if start_frame is None:
            frames = 0
        else:
            stop = current_frame if end_frame is None else int(end_frame)
            frames = max(0, int(stop) - int(start_frame))

        item = {
            "tokens": tokens,
            "frames": frames,
            "active": end_frame is None,
        }

        # Same-frame duplicates are rejected when chips are created. Keep every
        # later chip here so a real repeated press remains a separate entry.
        visible.append(item)

    chips = visible[-max(1, int(limit)):]
    chips.reverse()
    return chips


_INPUT_DIRECTION_VECTORS = {
    "1": (-1.0, 1.0),
    "2": (0.0, 1.0),
    "3": (1.0, 1.0),
    "4": (-1.0, 0.0),
    "5": (0.0, 0.0),
    "6": (1.0, 0.0),
    "7": (-1.0, -1.0),
    "8": (0.0, -1.0),
    "9": (1.0, -1.0),
}


def _split_input_token(token: str) -> tuple[str, str, str]:
    text = str(token or "").strip().upper()
    if not text or text == "·":
        return text, "", ""
    direction = text[0] if text and text[0] in _INPUT_DIRECTION_VECTORS else "5"
    rest = text[1:] if text[:1] == direction else text
    extra = ""
    if " +" in rest:
        rest, extra = rest.split(" +", 1)
        extra = f"+{extra}"
    return direction, "".join(ch for ch in rest if ch.isalpha()), extra


def _input_token_has_buttons(token: str) -> bool:
    _direction, buttons, _extra = _split_input_token(token)
    return bool(buttons)


def _group_input_history_tokens(source, limit: int = 5) -> list[list[str]]:
    tokens = [str(item or "").strip() for item in (source or []) if str(item or "").strip()]
    groups: list[list[str]] = []
    current: list[str] = []

    for token in tokens:
        if token == "·":
            if current:
                groups.append(current)
                current = []
            continue
        direction, buttons, extra = _split_input_token(token)
        if direction == "5" and not buttons and not extra:
            continue
        if current and current[-1] == token:
            continue
        if not buttons and not extra and current:
            prev_dir, prev_buttons, prev_extra = _split_input_token(current[-1])
            if not prev_buttons and not prev_extra and prev_dir == direction:
                continue
        current.append(token)
        if buttons:
            groups.append(current)
            current = []

    if current:
        groups.append(current)

    if not groups:
        return []
    trimmed = groups[-limit:]
    trimmed.reverse()
    return trimmed


def _render_input_direction_icon(direction: str, color: tuple[int, int, int], scale: float, alpha: float = 1.0) -> pygame.Surface:
    size = max(11, int(14 * scale))
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    rgba = (*color, max(0, min(255, int(255 * alpha))))
    cx = size / 2.0
    cy = size / 2.0
    vec = _INPUT_DIRECTION_VECTORS.get(str(direction), (0.0, 0.0))
    if vec == (0.0, 0.0):
        pygame.draw.circle(surf, rgba, (round(cx), round(cy)), max(2, int(size * 0.18)), 0)
        pygame.draw.circle(surf, (255, 255, 255, max(0, min(255, int(110 * alpha)))), (round(cx), round(cy)), max(3, int(size * 0.30)), 1)
        return surf

    vx, vy = vec
    mag = math.hypot(vx, vy) or 1.0
    ux = vx / mag
    uy = vy / mag
    start = (cx - ux * (size * 0.24), cy - uy * (size * 0.24))
    end = (cx + ux * (size * 0.24), cy + uy * (size * 0.24))
    shaft_w = max(2, int(size * 0.12))
    pygame.draw.line(surf, rgba, start, end, shaft_w)
    px = -uy
    py = ux
    tip = (cx + ux * (size * 0.40), cy + uy * (size * 0.40))
    back = (cx + ux * (size * 0.12), cy + uy * (size * 0.12))
    head_half = size * 0.16
    points = [
        (round(tip[0]), round(tip[1])),
        (round(back[0] + px * head_half), round(back[1] + py * head_half)),
        (round(back[0] - px * head_half), round(back[1] - py * head_half)),
    ]
    pygame.draw.polygon(surf, rgba, points)
    return surf


def _render_input_token_surface(font_sm, token: str, color: tuple[int, int, int], scale: float, alpha: float = 1.0) -> pygame.Surface:
    text = str(token or "").strip()
    if not text:
        return pygame.Surface((1, max(1, font_sm.get_height())), pygame.SRCALPHA)
    if text == "·":
        dot = font_sm.render("•", True, color)
        if alpha < 0.999:
            dot.set_alpha(max(0, min(255, int(255 * alpha))))
        return dot

    direction, buttons, extra = _split_input_token(text)
    icon = _render_input_direction_icon(direction, color, scale, alpha)
    parts = [icon]
    gap = max(3, int(4 * scale))
    width = icon.get_width()
    height = max(icon.get_height(), font_sm.get_height())

    if buttons:
        btn = font_sm.render(buttons, True, color)
        if alpha < 0.999:
            btn.set_alpha(max(0, min(255, int(255 * alpha))))
        parts.append(btn)
        width += gap + btn.get_width()
        height = max(height, btn.get_height())
    if extra:
        extra_surf = font_sm.render(extra, True, (120, 128, 144))
        if alpha < 0.999:
            extra_surf.set_alpha(max(0, min(255, int(255 * alpha))))
        parts.append(extra_surf)
        width += gap + extra_surf.get_width()
        height = max(height, extra_surf.get_height())

    surf = pygame.Surface((max(1, width), max(1, height)), pygame.SRCALPHA)
    dx = 0
    for idx, part in enumerate(parts):
        surf.blit(part, (dx, (height - part.get_height()) // 2))
        dx += part.get_width()
        if idx < len(parts) - 1:
            dx += gap
    return surf


def _render_input_group_surface(font_sm, chip: dict, color: tuple[int, int, int], scale: float, alpha: float = 1.0, emphasis: float = 1.0, show_counter: bool = True, dense: bool = False) -> pygame.Surface:
    """Render an input chip, optionally with its frozen frame counter."""
    tokens = list(chip.get("tokens") or [])
    frames = max(0, int(chip.get("frames") or 0))

    if dense:
        inner_gap = max(2, int(3 * scale))
        pad_x = max(3, int(4 * scale))
        pad_y = max(1, int(2 * scale))
        unit_gap = max(2, int(3 * scale))
        border_radius = max(4, int(5 * scale))
    else:
        inner_gap = max(4, int(5 * scale))
        pad_x = max(5, int(6 * scale))
        pad_y = max(2, int(3 * scale))
        unit_gap = max(3, int(4 * scale))
        border_radius = max(5, int(6 * scale))

    rendered_parts: list[pygame.Surface] = []
    visible_tokens = [str(tok).strip() for tok in tokens if str(tok).strip()]
    for idx, token in enumerate(visible_tokens):
        rendered_parts.append(_render_input_token_surface(font_sm, token, color, scale * emphasis, alpha))
        if idx < len(visible_tokens) - 1:
            sep = font_sm.render(">", True, (90, 98, 114))
            if alpha < 0.999:
                sep.set_alpha(max(0, min(255, int(255 * alpha))))
            rendered_parts.append(sep)

    if not rendered_parts:
        rendered_parts.append(font_sm.render(" - ", True, color))

    content_w = sum(part.get_width() for part in rendered_parts) + inner_gap * max(0, len(rendered_parts) - 1)
    content_h = max(part.get_height() for part in rendered_parts)
    chip_w = content_w + pad_x * 2
    chip_h = content_h + pad_y * 2

    chip_surface = pygame.Surface((max(1, chip_w), max(1, chip_h)), pygame.SRCALPHA)
    bg_alpha = max(0, min(255, int(168 * alpha)))
    border_alpha = max(0, min(255, int(116 * alpha)))
    pygame.draw.rect(chip_surface, (26, 31, 40, bg_alpha), (0, 0, chip_w, chip_h), border_radius=border_radius)
    pygame.draw.rect(chip_surface, (70, 84, 104, border_alpha), (0, 0, chip_w, chip_h), 1, border_radius=border_radius)
    pygame.draw.rect(chip_surface, (*color, max(0, min(255, int(90 * alpha)))), (1, 1, max(1, chip_w - 2), max(2, int(2 * scale))), border_top_left_radius=border_radius, border_top_right_radius=border_radius)

    dx = pad_x
    for idx, part in enumerate(rendered_parts):
        chip_surface.blit(part, (dx, (chip_h - part.get_height()) // 2))
        dx += part.get_width()
        if idx < len(rendered_parts) - 1:
            dx += inner_gap

    if not show_counter:
        return chip_surface

    counter_text = f"{frames}f"
    counter_color = (232, 240, 252) if bool(chip.get("active")) else (154, 165, 184)
    counter_surface = font_sm.render(counter_text, True, counter_color)
    if alpha < 0.999:
        counter_surface.set_alpha(max(0, min(255, int(255 * alpha))))
    counter_pad_x = max(5, int(6 * scale))
    counter_w = counter_surface.get_width() + counter_pad_x * 2
    counter_h = chip_h
    counter_box = pygame.Surface((counter_w, counter_h), pygame.SRCALPHA)
    active = bool(chip.get("active"))
    counter_fill = (34, 44, 58, max(0, min(255, int((190 if active else 148) * alpha))))
    counter_border = (*color, max(0, min(255, int((138 if active else 72) * alpha))))
    pygame.draw.rect(counter_box, counter_fill, (0, 0, counter_w, counter_h), border_radius=border_radius)
    pygame.draw.rect(counter_box, counter_border, (0, 0, counter_w, counter_h), 1, border_radius=border_radius)
    counter_box.blit(counter_surface, ((counter_w - counter_surface.get_width()) // 2, (counter_h - counter_surface.get_height()) // 2))

    unit_w = chip_w + unit_gap + counter_w
    unit_h = max(chip_h, counter_h)
    unit = pygame.Surface((unit_w, unit_h), pygame.SRCALPHA)
    unit.blit(chip_surface, (0, (unit_h - chip_h) // 2))
    unit.blit(counter_box, (chip_w + unit_gap, (unit_h - counter_h) // 2))
    return unit


def _draw_compact_input_history(
    screen,
    font_sm,
    chips: list[dict],
    x: int,
    y: int,
    right: int,
    scale: float,
    prev_chips: list[dict] | None = None,
    slide_progress: float = 0.0,
) -> None:
    row_gap = max(2, int(2 * scale))
    label_w = _history_label_width(font_sm, scale)
    label_h = max(font_sm.get_height() + max(4, int(5 * scale)), int(15 * scale))
    frame_y = y + label_h + row_gap
    draw_x = x + label_w + max(5, int(6 * scale))
    gap = max(2, int(3 * scale))

    _draw_history_header_chip(screen, font_sm, "INPUTS", x, y, scale)
    _draw_history_header_chip(screen, font_sm, "FRAMES", x, frame_y, scale)

    recency_colors = [
        (92, 232, 146),
        (132, 204, 255),
        (232, 236, 244),
        (198, 207, 220),
        (170, 181, 199),
        (146, 158, 178),
        (124, 137, 158),
        (106, 119, 140),
    ]

    def _build_units(source, alpha: float):
        rendered = []
        for idx, chip in enumerate(list(source or [])[:8]):
            color = recency_colors[min(idx, len(recency_colors) - 1)]
            emphasis = 1.03 if idx == 0 else 1.0
            input_surface = _render_input_group_surface(
                font_sm,
                chip,
                color,
                scale,
                alpha,
                emphasis,
                show_counter=False,
                dense=True,
            )

            frames = max(0, int(chip.get("frames") or 0))
            frame_color = (232, 240, 252) if bool(chip.get("active")) else (154, 165, 184)
            frame_surface = font_sm.render(f"{frames}f", True, frame_color)
            if alpha < 0.999:
                frame_surface.set_alpha(max(0, min(255, int(255 * alpha))))

            counter_pad = max(2, int(3 * scale))
            unit_width = max(
                input_surface.get_width(),
                frame_surface.get_width() + counter_pad * 2,
            )
            unit_height = (
                input_surface.get_height()
                + row_gap
                + frame_surface.get_height()
            )
            unit = pygame.Surface(
                (max(1, unit_width), max(1, unit_height)),
                pygame.SRCALPHA,
            )
            unit.blit(
                input_surface,
                ((unit_width - input_surface.get_width()) // 2, 0),
            )
            unit.blit(
                frame_surface,
                (
                    (unit_width - frame_surface.get_width()) // 2,
                    input_surface.get_height() + row_gap,
                ),
            )
            rendered.append(unit)
        return rendered

    def _draw_units(units, base_x: int):
        dx = base_x
        for idx, unit in enumerate(units):
            if dx >= right:
                break
            screen.blit(unit, (dx, y))
            dx += unit.get_width()
            if idx < len(units) - 1:
                dx += gap

    if not chips and not prev_chips:
        empty = font_sm.render(" - ", True, (86, 96, 114))
        screen.blit(empty, (draw_x, y))
        screen.blit(empty, (draw_x, frame_y))
        return

    current_units = _build_units(chips, 1.0)
    previous_units = (
        _build_units(prev_chips, max(0.0, min(1.0, slide_progress)))
        if prev_chips
        else []
    )

    if not current_units and not previous_units:
        empty = font_sm.render(" - ", True, (86, 96, 114))
        screen.blit(empty, (draw_x, y))
        screen.blit(empty, (draw_x, frame_y))
        return

    input_height = current_units[0].get_height() if current_units else (
        previous_units[0].get_height() if previous_units else font_sm.get_height() * 2
    )
    clip_rect = pygame.Rect(
        draw_x,
        y - 2,
        max(1, right - draw_x),
        max(input_height + int(4 * scale), int(32 * scale)),
    )

    inserted_shift = int(28 * scale)
    if current_units:
        inserted_shift = max(
            inserted_shift,
            current_units[0].get_width() + gap,
        )

    old_clip = screen.get_clip()
    screen.set_clip(clip_rect)
    if current_units:
        screen.blit(current_units[0], (draw_x, y))
        tail_x = draw_x + current_units[0].get_width() + gap
        current_tail = current_units[1:]
        previous_tail = previous_units
        if slide_progress > 0.001 and previous_tail:
            _draw_units(
                previous_tail,
                tail_x + int(inserted_shift * (1.0 - slide_progress)),
            )
            if current_tail:
                _draw_units(
                    current_tail,
                    tail_x - int(inserted_shift * slide_progress),
                )
        elif current_tail:
            _draw_units(current_tail, tail_x)
    elif previous_units:
        _draw_units(previous_units, draw_x)
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
    label_w = _draw_history_header_chip(screen, font_sm, "MOVES", x, y, scale)
    draw_x = x + label_w + max(6, int(7 * scale))
    gap = max(5, int(6 * scale))
    clip_rect = pygame.Rect(draw_x, y - 1, max(1, right - draw_x), max(font_sm.get_height() + int(8 * scale), int(18 * scale)))

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
            surf = _render_compact_text_chip(font_sm, txt.upper(), color, scale, alpha, emphasis=1.04 if idx == 0 else 1.0)
            rendered.append(surf)
        return rendered

    def _draw_parts(parts, base_x: int):
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
        empty = font_sm.render(" - ", True, (86, 96, 114))
        screen.blit(empty, (draw_x, y))
        return

    current_parts = _build_parts(current_texts, 1.0)
    previous_parts = _build_parts(previous_texts, max(0.0, min(1.0, slide_progress))) if previous_texts else []

    inserted_shift = 0
    if current_parts:
        inserted_shift = max(current_parts[0].get_width() + gap, int(24 * scale))

    old_clip = screen.get_clip()
    screen.set_clip(clip_rect)
    if slide_progress > 0.001 and previous_parts:
        _draw_parts(previous_parts, draw_x + int(inserted_shift * (1.0 - slide_progress)))
        _draw_parts(current_parts, draw_x - int(inserted_shift * slide_progress))
    else:
        _draw_parts(current_parts, draw_x)
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


def _draw_compact_guard_indicator(screen, font_sm, rect: pygame.Rect, label: str, result: str, scale: float, life: float = 1.0, flash: float = 0.0) -> None:
    life = max(0.0, min(1.0, float(life or 0.0)))
    if life <= 0.01 or rect.width <= 2 or rect.height <= 2:
        return

    property_label = str(label or "UNKNOWN").strip().upper() or "UNKNOWN"
    if property_label == "HIGH":
        property_label = "OVERHEAD"
    if property_label in {"UNBLK", "UNBLOCK"}:
        property_label = "UNBLOCKABLE"

    result = str(result or "").strip().upper()
    blocked = result == "BLOCK"
    result_text = "BLOCK" if blocked else "ATK HIT"

    pulse = 0.62 + 0.38 * ((math.sin(time.time() * 16.0) + 1.0) * 0.5) if flash > 0.0 else 1.0
    if blocked:
        base_fill = (22, 48, 34)
        base_border = (
            min(255, int(92 + 36 * pulse)),
            min(255, int(232 + 18 * pulse)),
            min(255, int(146 + 28 * pulse)),
        )
        result_color = (220, 255, 232)
        property_color = (148, 232, 176)
        glow_alpha = int(34 * pulse)
    else:
        base_fill = (72, 20, 26)
        base_border = (
            min(255, int(220 + 35 * pulse)),
            min(255, int(70 + 60 * pulse)),
            min(255, int(82 + 60 * pulse)),
        )
        result_color = (255, 228, 232)
        property_color = (255, 164, 174)
        glow_alpha = int(42 * pulse)

    alpha = max(0, min(255, int(255 * life)))
    badge = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
    radius = max(3, int(4 * scale))
    pygame.draw.rect(badge, (*base_fill, min(230, int(210 * life))), badge.get_rect(), border_radius=radius)
    pygame.draw.rect(badge, (*base_border, alpha), badge.get_rect(), 1, border_radius=radius)
    if glow_alpha > 0:
        pygame.draw.rect(badge, (*base_border, min(255, int(glow_alpha * life))), pygame.Rect(1, 1, max(1, rect.width - 2), max(2, int(2 * scale))), border_top_left_radius=radius, border_top_right_radius=radius)
    else:
        pygame.draw.rect(badge, (92, 232, 146, min(255, int(90 * life))), pygame.Rect(1, 1, max(1, rect.width - 2), max(2, int(2 * scale))), border_top_left_radius=radius, border_top_right_radius=radius)

    max_text_width = rect.width - max(8, int(10 * scale))
    result_surface = font_sm.render(_compact_fit_text(font_sm, result_text, max_text_width), True, result_color)
    property_surface = font_sm.render(_compact_fit_text(font_sm, property_label, max_text_width), True, property_color)
    result_surface.set_alpha(alpha)
    property_surface.set_alpha(alpha)

    total_h = result_surface.get_height() + property_surface.get_height() - max(1, int(2 * scale))
    top_y = max(1, (rect.height - total_h) // 2)
    badge.blit(result_surface, ((rect.width - result_surface.get_width()) // 2, top_y))
    badge.blit(property_surface, ((rect.width - property_surface.get_width()) // 2, top_y + result_surface.get_height() - max(1, int(2 * scale))))
    screen.blit(badge, rect.topleft)

def _draw_live_interaction_ribbon(screen, font, font_sm, scale: float, dt: float) -> None:
    life = max(0.0, float(_interaction_ribbon.get("life") or 0.0) - dt * 0.72)
    _interaction_ribbon["life"] = life
    if life <= 0.01 or not _interaction_ribbon.get("title"):
        return
    fade = min(1.0, life * 2.6)
    title = str(_interaction_ribbon.get("title") or "")
    detail = str(_interaction_ribbon.get("detail") or "")
    accent = tuple(_interaction_ribbon.get("color") or (130, 175, 255))
    title_s = font.render(title, True, (246, 248, 252))
    detail_s = font_sm.render(detail, True, (196, 208, 226))
    width = max(int(280 * scale), title_s.get_width() + int(34 * scale), detail_s.get_width() + int(34 * scale))
    height = max(int(42 * scale), title_s.get_height() + detail_s.get_height() + int(12 * scale))
    x = screen.get_width() // 2 - width // 2
    y = int(100 * scale - (1.0 - fade) * 14 * scale)
    radius = max(6, int(7 * scale))
    shadow = pygame.Surface((width + 10, height + 10), pygame.SRCALPHA)
    pygame.draw.rect(shadow, (0, 0, 0, int(88 * fade)), (5, 5, width, height), border_radius=radius + 2)
    screen.blit(shadow, (x - 5, y + 2))
    card = pygame.Surface((width, height), pygame.SRCALPHA)
    pygame.draw.rect(card, (10, 13, 19, int(232 * fade)), card.get_rect(), border_radius=radius)
    pygame.draw.rect(card, (34, 41, 55, int(216 * fade)), card.get_rect(), 1, border_radius=radius)
    pygame.draw.rect(card, (*accent, int(220 * fade)), (0, 0, max(5, int(6 * scale)), height), border_top_left_radius=radius, border_bottom_left_radius=radius)
    pygame.draw.polygon(card, (*accent, int(76 * fade)), [(max(5, int(6 * scale)), 0), (int(width * 0.40), 0), (int(width * 0.28), height - 1), (0, height - 1)])
    pygame.draw.line(card, (248, 250, 254, int(58 * fade)), (max(8, int(9 * scale)), 1), (width - int(10 * scale), 1), 1)
    title_s.set_alpha(int(255 * fade)); detail_s.set_alpha(int(240 * fade))
    card.blit(title_s, (int(14 * scale), int(5 * scale)))
    card.blit(detail_s, (int(14 * scale), height - detail_s.get_height() - int(5 * scale)))
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
    h = max(int(29 * scale), title_s.get_height() + resource_s.get_height() + int(8 * scale))
    w = min(width, max(int(194 * scale), title_s.get_width() + int(22 * scale), resource_s.get_width() + int(22 * scale)))
    draw_x = x if is_left else x + width - w
    slide = int((1.0 - fade) * 8 * scale)
    draw_y = y + slide
    shadow = pygame.Surface((w + 8, h + 8), pygame.SRCALPHA)
    card = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(shadow, (0, 0, 0, int(72 * fade)), (4, 4, w, h), border_radius=max(4, int(5 * scale)))
    pygame.draw.rect(card, (15, 19, 28, int(216 * fade)), card.get_rect(), border_radius=max(4, int(5 * scale)))
    pygame.draw.rect(card, (107, 154, 232, int(178 * fade)), card.get_rect(), 1, border_radius=max(3, int(4 * scale)))
    rail_x = 0 if is_left else w - max(2, int(3 * scale))
    pygame.draw.rect(card, (107, 154, 232, int(220 * fade)), (rail_x, 0, max(2, int(3 * scale)), h))
    title_s.set_alpha(int(255 * fade)); resource_s.set_alpha(int(238 * fade))
    card.blit(title_s, (int(8 * scale), int(3 * scale)))
    card.blit(resource_s, (int(8 * scale), h - resource_s.get_height() - int(3 * scale)))
    screen.blit(shadow, (draw_x - 4, draw_y - 1))
    screen.blit(shadow, (draw_x - 4, draw_y + 1))
    screen.blit(card, (draw_x, draw_y))


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
    slide = int((1.0 - fade) * 10 * scale)
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
        detail += f"  |  BBQ {bbq:.2f}%"
    detail_s = font_sm.render(detail, True, detail_color)
    h = max(int(28 * scale), title_s.get_height() + detail_s.get_height() + int(7 * scale))
    w = min(width, max(int(185 * scale), title_s.get_width() + int(18 * scale), detail_s.get_width() + int(18 * scale)))
    draw_x = x if is_left else x + width - w
    draw_y = y + slide
    shadow = pygame.Surface((w + 8, h + 8), pygame.SRCALPHA)
    pygame.draw.rect(shadow, (0, 0, 0, int(70 * fade)), (4, 4, w, h), border_radius=max(4, int(5 * scale)))
    card = pygame.Surface((w, h), pygame.SRCALPHA)
    pygame.draw.rect(card, (*fill, int(224 * fade)), card.get_rect(), border_radius=max(4, int(5 * scale)))
    pygame.draw.rect(card, (*accent, int(200 * fade)), card.get_rect(), 1, border_radius=max(3, int(4 * scale)))
    rail_w = max(2, int(3 * scale))
    rail_x = 0 if is_left else w - rail_w
    pygame.draw.rect(card, (*accent, int(238 * fade)), (rail_x, 0, rail_w, h), border_radius=max(2, int(3 * scale)))
    title_s.set_alpha(int(255 * fade)); detail_s.set_alpha(int(245 * fade))
    card.blit(title_s, (int(8 * scale), int(3 * scale)))
    card.blit(detail_s, (int(8 * scale), h - detail_s.get_height() - int(3 * scale)))
    screen.blit(shadow, (draw_x - 4, draw_y + 1))
    screen.blit(card, (draw_x, draw_y))


def _cached_compact_panel_shell(
    width: int,
    height: int,
    accent: tuple[int, int, int],
    is_left: bool,
    scale: float,
    panel_alpha: float,
) -> tuple[pygame.Surface, pygame.Surface]:
    alpha_bucket = max(1, min(20, int(round(float(panel_alpha) * 20.0))))
    key = (int(width), int(height), tuple(accent), bool(is_left), round(float(scale), 3), alpha_bucket)
    cached = _COMPACT_PANEL_SHELL_CACHE.get(key)
    if cached is not None:
        return cached

    alpha = alpha_bucket / 20.0
    shadow = pygame.Surface((width + int(16 * scale), height + int(16 * scale)), pygame.SRCALPHA)
    panel = pygame.Surface((width, height), pygame.SRCALPHA)
    base_alpha = int(220 * alpha)
    notch = max(8, int(10 * scale))
    if is_left:
        points = [(notch, 0), (width - 1, 0), (width - 1, height - 1), (0, height - 1), (0, notch)]
    else:
        points = [(0, 0), (width - notch - 1, 0), (width - 1, notch), (width - 1, height - 1), (0, height - 1)]

    pygame.draw.polygon(shadow, (0, 0, 0, int(76 * alpha)), [(p[0] + int(8 * scale), p[1] + int(8 * scale)) for p in points])
    pygame.draw.polygon(panel, (16, 21, 28, base_alpha), points)
    for py in range(height):
        blend = py / max(1, height - 1)
        line_color = _lerp_color((60, 68, 80), (12, 16, 22), blend)
        pygame.draw.line(panel, (*line_color, int(172 * alpha)), (1, py), (width - 2, py))

    core_h = max(56, int(60 * scale))
    analysis_y = max(core_h + int(16 * scale), int(78 * scale))
    pygame.draw.rect(panel, (22, 28, 38, int(120 * alpha)), (int(8 * scale), int(8 * scale), width - int(16 * scale), core_h), border_radius=max(6, int(7 * scale)))
    pygame.draw.rect(panel, (8, 12, 18, int(104 * alpha)), (int(8 * scale), analysis_y, width - int(16 * scale), height - analysis_y - int(8 * scale)), border_radius=max(6, int(7 * scale)))

    sheen_surface = pygame.Surface((width, height), pygame.SRCALPHA)
    pygame.draw.polygon(sheen_surface, (255, 255, 255, int(18 * alpha)), [
        (int(width * 0.08), 0),
        (int(width * 0.42), 0),
        (int(width * 0.28), height - 1),
        (0, height - 1),
    ])
    pygame.draw.line(sheen_surface, (250, 252, 255, int(26 * alpha)), (int(width * 0.08), 1), (int(width * 0.42), 1), 1)
    panel.blit(sheen_surface, (0, 0))
    pygame.draw.polygon(panel, (*accent, int(170 * alpha)), points, 1)
    pygame.draw.line(panel, (250, 252, 255, int(42 * alpha)), (notch if is_left else 0, 0), (width - 1 if is_left else width - notch - 1, 0))
    pygame.draw.line(panel, (8, 10, 14, int(178 * alpha)), (0, height - 1), (width - 1, height - 1))
    pygame.draw.line(panel, (*accent, int(60 * alpha)), (int(8 * scale), int(74 * scale)), (width - int(8 * scale), int(74 * scale)))
    rail_w = max(4, int(5 * scale))
    rail_x = 0 if is_left else width - rail_w
    pygame.draw.rect(panel, (*accent, int(238 * alpha)), (rail_x, 0, rail_w, height))

    if len(_COMPACT_PANEL_SHELL_CACHE) >= 96:
        _COMPACT_PANEL_SHELL_CACHE.clear()
    cached = (shadow, panel)
    _COMPACT_PANEL_SHELL_CACHE[key] = cached
    return cached


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

    team_anim = _get_team_anim(team)
    _tick_team_panel_fx(team_anim, dt)
    hold_items = _display_button_holds(point_anim, _frame)
    hold_target = 1.0 if hold_items else 0.0
    team_anim["hold_expand"] = _approach(
        float(team_anim.get("hold_expand", 0.0)),
        hold_target,
        7.5 if hold_target else 4.5,
        dt,
    )
    hold_expand = max(0.0, min(1.0, float(team_anim.get("hold_expand", 0.0))))

    width = max(442, int(486 * scale))
    collapsed_height = max(154, int(166 * scale))
    hold_extra_height = max(18, int(20 * scale))
    height = collapsed_height + int(hold_extra_height * hold_expand)
    margin_x = int(12 * scale)
    base_y = int(144 * scale)
    is_left = team == "P1"
    base_x = margin_x if is_left else screen.get_width() - margin_x - width
    accent = SLOT_COLORS.get(point_label, SLOT_COLORS[f"{team}-C1"])
    point_dead = int(point.get("cur") or 0) <= 0
    partner_dead = int(partner.get("cur") or 0) <= 0
    point_anim["ko_alpha"] = _approach(float(point_anim.get("ko_alpha", 0.0)), 1.0 if point_dead else 0.0, 6.5 if point_dead else 3.2, dt)
    point_anim["ko_scale"] = _approach(float(point_anim.get("ko_scale", 0.90)), 1.08 if point_dead else 0.92, 8.0 if point_dead else 4.0, dt)

    team_present = bool(_get_slot_anim(first_label).get("present") or _get_slot_anim(second_label).get("present"))
    if team_present and not team_anim.get("present"):
        team_anim["slide_y"] = -22.0
        team_anim["slide_x"] = 0.0
        team_anim["alpha"] = min(team_anim.get("alpha", 0.0), 0.45)
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
        team_anim["slide_y"] = _approach(float(team_anim.get("slide_y", -22.0)), 0.0, 176.0, dt)
        team_anim["slide_x"] = _approach(float(team_anim.get("slide_x", 0.0)), 0.0, 1800.0, dt)
        team_anim["alpha"] = _approach(float(team_anim.get("alpha", 0.0)), 1.0, 4.8, dt)
        team_anim["swap_progress"] = _approach(float(team_anim.get("swap_progress", 0.0)), 0.0, 4.8, dt)
    else:
        off_target = -float(width + margin_x + 18) if is_left else float(width + margin_x + 18)
        team_anim["slide_x"] = _approach(float(team_anim.get("slide_x", 0.0)), off_target, 1700.0, dt)
        team_anim["alpha"] = _approach(float(team_anim.get("alpha", 0.0)), 0.0, 3.2, dt)

    panel_alpha = overlay_alpha * float(team_anim.get("alpha", 0.0))
    if panel_alpha <= 0.01:
        return
    shake = float(team_anim.get("shake", 0.0))
    shake_dir = -1.0 if is_left else 1.0
    shake_x = math.sin(time.time() * 42.0) * (1.0 * shake) * shake_dir
    shake_y = math.sin(time.time() * 28.0 + (0.8 if is_left else 1.4)) * (0.5 * shake)
    x = int(base_x + float(team_anim.get("slide_x", 0.0)) + shake_x)
    y = int(base_y + float(team_anim.get("slide_y", 0.0)) + shake_y)

    shadow, panel = _cached_compact_panel_shell(width, height, accent, is_left, scale, panel_alpha)
    screen.blit(shadow, (x - int(4 * scale), y + int(4 * scale)))
    screen.blit(panel, (x, y))
    _draw_team_panel_fx(screen, team_anim, x, y, width, height, accent, scale, panel_alpha)

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
    input_y = history_y + max(12, int(13 * scale)) + int(2 * scale)
    input_row_h = max(15, int(17 * scale))
    frames_y = input_y + input_row_h + int(2 * scale)
    frames_row_h = max(13, int(15 * scale))
    hold_y = frames_y + frames_row_h + int(2 * scale)
    hold_row_h = max(15, int(17 * scale))
    move_history_y = (
        frames_y
        + frames_row_h
        + int((hold_row_h + int(2 * scale)) * hold_expand)
        + int(2 * scale)
    )
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

    show_point_baroque = _update_compact_baroque_anim(point_anim, point, point_dead, dt)
    show_partner_baroque = _update_compact_baroque_anim(partner_anim, partner, partner_dead, dt)
    power_w = max(
        int(108 * scale),
        font_sm.size("C1 BBQ 00.00%")[0] + int(18 * scale),
        font_sm.size("METER 5")[0] + int(12 * scale),
    )
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

    guard_y = strip_y + int(1 * scale)
    bq_h = max(14, int(16 * scale))
    bq_gap = max(2, int(3 * scale))
    if show_point_baroque:
        point_bq_rect = pygame.Rect(power_left, guard_y, power_w, bq_h)
        _draw_compact_baroque_badge(
            screen,
            font_sm,
            point_bq_rect,
            float(point_anim.get("baroque_display_pct", point.get("baroque_red_pct_max") or 0.0)),
            scale,
            is_left,
            float(point_anim.get("baroque_alpha", 0.0)),
            int(point_anim.get("baroque_fade_direction", 0) or 0),
            point_badge,
        )
        guard_y = point_bq_rect.bottom + bq_gap
    if show_partner_baroque:
        partner_bq_rect = pygame.Rect(power_left, guard_y, power_w, bq_h)
        _draw_compact_baroque_badge(
            screen,
            font_sm,
            partner_bq_rect,
            float(partner_anim.get("baroque_display_pct", partner.get("baroque_red_pct_max") or 0.0)),
            scale,
            is_left,
            float(partner_anim.get("baroque_alpha", 0.0)),
            int(partner_anim.get("baroque_fade_direction", 0) or 0),
            partner_badge,
        )
        guard_y = partner_bq_rect.bottom + bq_gap

    guard_label = str(point_anim.get("guard_indicator_label") or "")
    guard_result = str(point_anim.get("guard_indicator_result") or "")
    guard_life = float(point_anim.get("guard_indicator_life", 0.0) or 0.0)
    if guard_label and guard_life > 0.01:
        guard_h = max(25, int(28 * scale))
        guard_rect = pygame.Rect(power_left, guard_y, power_w, guard_h)
        _draw_compact_guard_indicator(
            screen,
            font_sm,
            guard_rect,
            guard_label,
            guard_result,
            scale,
            guard_life,
            float(point_anim.get("guard_indicator_flash", 0.0) or 0.0),
        )

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
            team_anim["log_history_slide"] = 0.52
        team_anim["log_history_signature"] = log_signature
    team_anim["log_history_slide"] = _approach(float(team_anim.get("log_history_slide", 0.0)), 0.0, 6.6, dt)
    _draw_compact_history_line(screen, font_sm, "LOG", log_items, left, history_y, right, scale, team_anim.get("log_history_prev", []), float(team_anim.get("log_history_slide", 0.0)))
    # Keep the raw input tail in chronological order. The grouped renderer
    # builds commands from oldest to newest, then places the newest group first.
    input_chips = _display_input_chips(point_anim, _frame)
    input_signature = tuple("|".join(chip.get("tokens", [])) for chip in input_chips)
    previous_input_signature = tuple(team_anim.get("input_history_signature", ()))
    if input_signature != previous_input_signature:
        if previous_input_signature:
            team_anim["input_history_prev"] = [dict(chip) for chip in input_chips[1:]]
            team_anim["input_history_slide"] = 0.56
        team_anim["input_history_signature"] = input_signature
    team_anim["input_history_current"] = [dict(chip) for chip in input_chips]
    team_anim["input_history_slide"] = _approach(float(team_anim.get("input_history_slide", 0.0)), 0.0, 6.8, dt)
    _draw_compact_input_history(
        screen,
        font_sm,
        input_chips,
        left,
        input_y,
        info_right,
        scale,
        team_anim.get("input_history_prev", []),
        float(team_anim.get("input_history_slide", 0.0)),
    )

    hold_signature = tuple(str(item.get("id") or "") for item in hold_items)
    previous_hold_signature = tuple(team_anim.get("hold_history_signature", ()))
    if hold_signature != previous_hold_signature:
        if previous_hold_signature:
            team_anim["hold_history_prev"] = [
                dict(item) for item in team_anim.get("hold_history_current", [])
            ]
            team_anim["hold_history_slide"] = 0.50
        team_anim["hold_history_signature"] = hold_signature
    team_anim["hold_history_current"] = [dict(item) for item in hold_items]
    team_anim["hold_history_slide"] = _approach(
        float(team_anim.get("hold_history_slide", 0.0)),
        0.0,
        6.6,
        dt,
    )
    if hold_expand > 0.03:
        faded_hold_items = []
        for item in hold_items:
            entry = dict(item)
            entry["alpha"] = float(entry.get("alpha", 1.0)) * hold_expand
            faded_hold_items.append(entry)
        _draw_compact_hold_history(
            screen,
            font_sm,
            faded_hold_items,
            left,
            hold_y,
            info_right,
            scale,
            team_anim.get("hold_history_prev", []),
            float(team_anim.get("hold_history_slide", 0.0)),
        )

    merged_moves = _merge_move_history(point_anim.get("move_events", []), partner_anim.get("move_events", []))
    move_signature = tuple(str(item.get("text") or "").strip() for item in merged_moves[:5] if str(item.get("text") or "").strip())
    previous_signature = tuple(team_anim.get("move_history_signature", ()))
    if move_signature != previous_signature:
        if previous_signature:
            team_anim["move_history_prev"] = list(previous_signature)
            team_anim["move_history_slide"] = 0.54
        team_anim["move_history_signature"] = move_signature
    team_anim["move_history_slide"] = _approach(float(team_anim.get("move_history_slide", 0.0)), 0.0, 6.8, dt)
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
    state_x = power_left - int(8 * scale) - state_surface.get_width()
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
        print("[hud_overlay] Dolphin window not found  -  exiting.")
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