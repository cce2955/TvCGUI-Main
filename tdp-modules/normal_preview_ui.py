"""Extracted runtime module from :mod:`main`.

This module deliberately preserves the original function names and behavior so
`main.py` can remain a compatibility-oriented entry point while the subsystem
has a focused home.
"""
from __future__ import annotations

import time

import pygame

try:
    from scan_normals_all import ANIM_MAP as SCAN_ANIM_MAP
except Exception:
    SCAN_ANIM_MAP = {}

from ui_components import (
    GUI_ACCENT_BLUE,
    GUI_CONFIRM,
    GUI_TEXT,
    GUI_TEXT_DIM,
    GUI_TEXT_MUTED,
    _brighten,
    _darken,
    _draw_vertical_gradient,
    _fit_text,
    _render_outlined_text,
    _slot_accent_for_label,
    draw_glass_button,
)

def _normal_button_accent(label: str) -> tuple[int, int, int]:
    text = str(label or "").upper()
    if "A" in text or text.endswith("L"):
        return (115, 155, 235)
    if "B" in text or text.endswith("M"):
        return (220, 195, 105)
    if "C" in text or text.endswith("H"):
        return (105, 215, 155)
    return GUI_ACCENT_BLUE


def _normal_id_to_label(value, *, allow_low: bool = True) -> str | None:
    """Resolve a normal label from the scanner's authoritative ANIM_MAP.

    The old preview table was shifted for crouching normals and also invented
    j.2B/j.2C rows from raw ids. Keep this helper tied to scan_normals_all so
    the preview cannot drift away from the actual scanner again.
    """
    try:
        raw = int(value)
    except Exception:
        return None

    if raw < 0:
        return None

    fallback_map = {
        0x00: "5A",
        0x01: "5B",
        0x02: "5C",
        0x03: "2A",
        0x04: "2B",
        0x05: "2C",
        0x06: "6C",
        0x08: "3C",
        0x09: "j.A",
        0x0A: "j.B",
        0x0B: "j.C",
        0x0E: "6B",
    }

    scan_map = SCAN_ANIM_MAP if isinstance(SCAN_ANIM_MAP, dict) and SCAN_ANIM_MAP else fallback_map
    low = raw & 0xFF

    if raw >= 0x100 and low in scan_map:
        return str(scan_map[low])

    if allow_low and raw in scan_map:
        return str(scan_map[raw])

    return None


def _normal_move_label(mv: dict) -> str:
    if not isinstance(mv, dict):
        return "?"

    forced = mv.get("_normal_display_label")
    if forced:
        return str(forced)

    # Prefer actual scanner/editor labels first. move_name is what
    # scan_normals_all attaches; the older preview code accidentally ignored it.
    for key in ("label", "move_name", "move", "pretty_name", "name"):
        value = mv.get(key)
        if value:
            return str(value)

    label = _normal_id_to_label(mv.get("id"), allow_low=True)
    if label:
        return label

    label = _normal_id_to_label(mv.get("table_index"), allow_low=False)
    if label:
        return label

    return "?"


def _normal_canon_label(label: str) -> str:
    """Canonicalize display labels for preview-row highlighting.

    The UI should prefer the live move label first, because multiple rows can
    sometimes share a raw animation ID or carry overlapping fallback IDs. Using
    the canonical display label avoids accidental double-highlighting.
    """
    text = str(label or "").strip().lower()
    if not text:
        return ""
    text = text.replace(" ", "")
    text = text.replace("jump.", "j.")
    text = text.replace("jump", "j")
    text = text.replace("crouching", "2")
    text = text.replace("crouch", "2")
    text = text.replace("standing", "5")
    text = text.replace("stand", "5")
    text = text.replace("close", "")
    text = text.replace("far", "")
    return text


def _normal_int(mv: dict, *keys: str) -> int | None:
    for key in keys:
        value = mv.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except Exception:
            continue
    return None


def _normal_recovery(mv: dict) -> int | None:
    """Return the MOT-derived/static recovery stored on a normal row."""
    return _normal_int(mv, "recovery", "recover", "recovery_frames")

def _normal_advantage(mv: dict, kind: str) -> int | None:
    """Return stored calculated advantage, or reproduce the local fallback.

    The scanner/profile pass stores ``adv_hit`` and ``adv_block`` once MOT
    recovery is known.  Keeping the small fallback here lets the preview still
    show the same estimate for a freshly scanned row that has raw stun and
    recovery but has not yet been profile-saved.
    """
    if str(kind).lower().startswith("b"):
        stored = _normal_int(mv, "adv_block", "on_block", "block_adv", "plus_block")
        stun = _normal_int(mv, "blockstun", "block", "b")
    else:
        stored = _normal_int(mv, "adv_hit", "on_hit", "hit_adv", "plus_hit")
        stun = _normal_int(mv, "hitstun", "hit", "h")
    if stored is not None:
        return stored
    recovery = _normal_recovery(mv)
    if stun is None or recovery is None:
        return None
    return int(stun) - int(recovery)

def _normal_damage(mv: dict) -> int | None:
    """Return the decoded primary-hit damage without summing multihits."""
    direct = _normal_int(mv, "damage", "dmg", "base_damage")
    if direct is not None:
        return direct
    segments = mv.get("hit_segments")
    if isinstance(segments, list):
        for seg in segments:
            if isinstance(seg, dict):
                damage = _normal_int(seg, "damage", "dmg", "base_damage")
                if damage is not None:
                    return damage
    return None




_NORMAL_PREVIEW_ORDER = (
    "5A", "2A",
    "5B", "2B",
    "6B",
    "5C", "2C",
    "4C", "6C", "3C",
    "j.A", "j.B", "j.C",
)
_NORMAL_PREVIEW_RANK = {name.lower(): i for i, name in enumerate(_NORMAL_PREVIEW_ORDER)}

# These labels are optional/character-specific. Do not let a raw fallback id
# manufacture them for everyone. j.2B/j.2C are intentionally not in the preview
# order until they are promoted by a real character-specific scanner label.
_OPTIONAL_PREVIEW_NORMALS = {"6B"}
_HIDDEN_PREVIEW_NORMALS = {"j.2B", "j.2C"}


def _normal_canonical_label(label: str) -> str | None:
    text = str(label or "").strip()
    if not text or text == "?":
        return None

    low = text.lower()
    low = low.replace(" ", "")
    low = low.replace("_", "")
    low = low.replace("jump.", "j.")
    low = low.replace("jump", "j.")
    low = low.replace("air.", "j.")
    low = low.replace("air", "j.")
    low = low.replace("stand", "5")
    low = low.replace("standing", "5")
    low = low.replace("crouch", "2")
    low = low.replace("crouching", "2")

    aliases = {
        "a": "5A", "5a": "5A",
        "2a": "2A",
        "b": "5B", "5b": "5B",
        "2b": "2B",
        "6b": "6B",
        "c": "5C", "5c": "5C",
        "2c": "2C",
        "4c": "4C",
        "6c": "6C",
        "3c": "3C",
        "j.a": "j.A", "ja": "j.A", "jA".lower(): "j.A",
        "j.b": "j.B", "jb": "j.B", "jB".lower(): "j.B",
        "j.c": "j.C", "jc": "j.C", "jC".lower(): "j.C",
    }

    return aliases.get(low)


def _normal_preview_label_allowed(mv: dict, canon: str, raw_label: str) -> bool:
    """Gate optional labels that raw ids can falsely create.

    Core normals are allowed from the scanner map. 6B is allowed only when the
    row was produced by an explicit/character-specific label source. This keeps
    random 0x010E system/script records from appearing as 6B for every cast
    member. j.2B/j.2C stay hidden from the compact preview for now.
    """
    if canon in _HIDDEN_PREVIEW_NORMALS:
        return False

    if canon not in _OPTIONAL_PREVIEW_NORMALS:
        return True

    if not isinstance(mv, dict):
        return False

    if bool(mv.get("normal_confirmed")):
        return True

    source = str(mv.get("move_name_source") or mv.get("label_source") or "").strip().lower()
    if source in {"lookup", "char_map", "character", "csv", "explicit"}:
        return True

    # If another module supplied an actual display label, trust that over the
    # raw id fallback. Do not count move_name here because older scanner builds
    # filled move_name from ANIM_MAP fallback.
    for key in ("label", "move", "pretty_name"):
        explicit = mv.get(key)
        if explicit and _normal_canonical_label(str(explicit)) == canon:
            return True

    return False


def _normal_row_quality(mv: dict) -> tuple[int, int, int, int]:
    """Prefer rows that actually have useful frame values if duplicates exist."""
    if not isinstance(mv, dict):
        return (0, 0, 0, 0)
    startup = _normal_int(mv, "startup", "start", "active_start")
    a1 = _normal_int(mv, "active_start", "a_start")
    a2 = _normal_int(mv, "active_end", "a_end")
    hit = _normal_int(mv, "hitstun", "hit", "h")
    block = _normal_int(mv, "blockstun", "block", "b")
    filled = sum(v is not None for v in (startup, a1, a2, hit, block))
    active_span = 0 if a1 is None or a2 is None else max(0, a2 - a1)
    damage = _normal_int(mv, "damage", "dmg") or 0
    return (filled, active_span, damage, -int(mv.get("_scan_index", 0) or 0))


def _normal_visible_moves(moves: list) -> list:
    """Return only the curated normal rows, in fighting-game notation order.

    The scan can contain duplicate/system/debug rows, and some characters put
    command normals before or after jump normals. The preview should not depend
    on raw scan order. It shows the useful set only:
      5A, 2A, 5B, 2B, optional confirmed 6B, 5C, 2C, optional 4C/6C/3C, j.A, j.B, j.C
    """
    if not isinstance(moves, list):
        return []

    best_by_label: dict[str, dict] = {}

    for scan_i, mv in enumerate(moves):
        if not isinstance(mv, dict):
            continue

        label = _normal_move_label(mv)
        canon = _normal_canonical_label(label)
        if canon is None:
            continue
        if not _normal_preview_label_allowed(mv, canon, label):
            continue

        row = dict(mv)
        row["_normal_display_label"] = canon
        row.setdefault("_scan_index", scan_i)

        old = best_by_label.get(canon)
        if old is None or _normal_row_quality(row) > _normal_row_quality(old):
            best_by_label[canon] = row

    out: list[dict] = []
    for label in _NORMAL_PREVIEW_ORDER:
        row = best_by_label.get(label)
        if row is not None:
            out.append(row)
    return out



_NORMAL_PREVIEW_MODE_META = {
    "fast": {"label": "Fast", "color": (112, 182, 245)},
    "damage": {"label": "Damage", "color": (232, 190, 96)},
    "adv_block": {"label": "+Block", "color": (177, 145, 244)},
    "matchup": {"label": "Match", "color": (104, 211, 227)},
    "safe": {"label": "Safe", "color": (108, 214, 158)},
    "unsafe": {"label": "Unsafe", "color": (232, 124, 124)},
    "punish": {"label": "Punish", "color": (236, 136, 112)},
    "live_punish": {"label": "Live", "color": (100, 209, 223)},
}


def _normal_preview_move_id(mv: dict) -> int | None:
    """Return the most specific available move identifier for a preview row."""
    if not isinstance(mv, dict):
        return None
    for key in ("id", "anim", "move_id", "table_index"):
        value = mv.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except Exception:
            continue
    return None


def _normal_preview_key(mv: dict) -> str:
    """Build a stable local key for preview selection and highlighting."""
    label = _normal_canonical_label(_normal_move_label(mv)) or _normal_canon_label(_normal_move_label(mv)) or "?"
    move_id = _normal_preview_move_id(mv)
    return f"{label}|{'' if move_id is None else move_id}"


def _normal_preview_selection_matches(selection: dict | None, slot_label: str, mv: dict) -> bool:
    """Check whether a preview row matches the retained selection."""
    if not isinstance(selection, dict):
        return False
    if str(selection.get("slot_label") or "") != str(slot_label or ""):
        return False
    return str(selection.get("key") or "") == _normal_preview_key(mv)


def _normal_preview_selected_move(slots: list[dict], selection: dict | None) -> tuple[str, dict] | tuple[None, None]:
    """Resolve the selected preview row from the current slot dataset."""
    if not isinstance(selection, dict):
        return None, None
    wanted_slot = str(selection.get("slot_label") or "")
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        slot_label = str(slot.get("slot_label") or slot.get("slot") or "")
        if slot_label != wanted_slot:
            continue
        for mv in _normal_visible_moves(slot.get("moves") or []):
            if _normal_preview_selection_matches(selection, slot_label, mv):
                return slot_label, mv
    return None, None


def _normal_preview_is_air_move(mv: dict) -> bool:
    """Return whether a preview normal is an aerial attack."""
    label = _normal_canonical_label(_normal_move_label(mv)) or _normal_canon_label(_normal_move_label(mv)) or ""
    return str(label).lower().startswith("j.")


def _normal_preview_current_move(slot: dict) -> dict | None:
    """Resolve the currently executing visible normal for one slot."""
    if not isinstance(slot, dict):
        return None
    visible = _normal_visible_moves(slot.get("moves") or [])
    if not visible:
        return None

    cur_id = slot.get("cur_anim") or slot.get("current_anim") or slot.get("mv_id_display") or slot.get("move_id")
    try:
        cur_id = int(cur_id) if cur_id is not None else None
    except Exception:
        cur_id = None
    cur_label = str(slot.get("cur_label") or slot.get("current_move") or slot.get("mv_label") or "").strip()
    cur_label_canon = _normal_canon_label(cur_label)

    if cur_label_canon:
        for mv in visible:
            if _normal_canon_label(_normal_move_label(mv)) == cur_label_canon:
                return mv
    if cur_id is not None:
        for mv in visible:
            if _normal_preview_move_id(mv) == cur_id:
                return mv
    return None


def _normal_preview_assist_standby(slot: dict) -> bool:
    """Identify the inactive partner state used by the character slots."""
    if not isinstance(slot, dict):
        return False
    text = " ".join(str(slot.get(key) or "") for key in ("cur_label", "current_move", "mv_label")).lower()
    if "assist standby" in text:
        return True
    current_id = slot.get("cur_anim") or slot.get("current_anim") or slot.get("mv_id_display") or slot.get("move_id")
    try:
        return int(current_id) == 430
    except Exception:
        return False


def _normal_preview_active_slot(slots: list[dict], team: str) -> tuple[str, dict] | tuple[None, None]:
    """Resolve the point slot for a team from the current two-character state."""
    team = str(team or "").upper()
    candidates: list[tuple[str, dict]] = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        label = str(slot.get("slot_label") or slot.get("slot") or "")
        if label.startswith(f"{team}-"):
            candidates.append((label, slot))
    if not candidates:
        return None, None
    if len(candidates) == 1:
        return candidates[0]

    active = [(label, slot) for label, slot in candidates if not _normal_preview_assist_standby(slot)]
    if len(active) == 1:
        return active[0]
    for label, slot in candidates:
        if label.endswith("C1"):
            return label, slot
    return candidates[0]


def _normal_preview_live_source(slots: list[dict]) -> tuple[str, dict] | tuple[None, None]:
    """Resolve a single current point-normal as the live punish source."""
    found: list[tuple[str, dict]] = []
    for team in ("P1", "P2"):
        slot_label, slot = _normal_preview_active_slot(slots, team)
        if not slot_label or not isinstance(slot, dict):
            continue
        move = _normal_preview_current_move(slot)
        if isinstance(move, dict):
            found.append((slot_label, move))
    if len(found) != 1:
        return None, None
    return found[0]


def _normal_preview_best_keys(moves: list[dict], mode: str) -> set[str]:
    """Return the best metric rows for one character card.

    Fast/damage/+block highlights are split into grounded and aerial groups so
    each card can show one best standing normal and one best jumping normal.
    Fast ignores 1f/2f startup values to avoid false positives.
    """
    mode = str(mode or "")
    if mode == "adv_hit":
        mode = "adv_block"

    grouped: dict[bool, list[tuple[int, str]]] = {False: [], True: []}
    for mv in moves:
        if not isinstance(mv, dict):
            continue
        is_air = _normal_preview_is_air_move(mv)
        if mode == "fast":
            value = _normal_int(mv, "startup", "start", "active_start")
            if value is None:
                continue
            value = int(value)
            if value < 3:
                continue
            grouped[is_air].append((-value, _normal_preview_key(mv)))
        elif mode == "damage":
            value = _normal_damage(mv)
            if value is None:
                continue
            grouped[is_air].append((int(value), _normal_preview_key(mv)))
        elif mode == "adv_block":
            value = _normal_advantage(mv, "block")
            if value is None:
                continue
            grouped[is_air].append((int(value), _normal_preview_key(mv)))
        elif mode == "safe":
            value = _normal_advantage(mv, "block")
            if value is None or int(value) < 0:
                continue
            grouped[is_air].append((int(value), _normal_preview_key(mv)))
        elif mode == "unsafe":
            value = _normal_advantage(mv, "block")
            if value is None:
                continue
            grouped[is_air].append((-int(value), _normal_preview_key(mv)))

    out: set[str] = set()
    for is_air in (False, True):
        ranked = grouped[is_air]
        if not ranked:
            continue
        best_value = max(value for value, _key in ranked)
        out.update(key for value, key in ranked if value == best_value)
    return out


def _normal_preview_punish_ladder(slot: dict, source_mv: dict, window: int) -> dict[str, object] | None:
    """Return fastest and highest-damage legal normal punish options for one slot."""
    source_is_air = _normal_preview_is_air_move(source_mv)
    candidates: list[dict[str, object]] = []
    for mv in _normal_visible_moves(slot.get("moves") or []):
        if _normal_preview_is_air_move(mv) != source_is_air:
            continue
        startup = _normal_int(mv, "startup", "start", "active_start")
        if startup is None or int(startup) > int(window):
            continue
        damage = _normal_damage(mv)
        candidates.append({
            "key": _normal_preview_key(mv),
            "label": _normal_move_label(mv),
            "startup": int(startup),
            "damage": int(damage) if damage is not None else 0,
        })
    if not candidates:
        return None

    fastest = min(candidates, key=lambda item: (int(item["startup"]), -int(item["damage"]), str(item["key"])))
    strongest = max(candidates, key=lambda item: (int(item["damage"]), -int(item["startup"]), str(item["key"])))
    return {"fast": fastest, "damage": strongest}


def _normal_preview_punish_candidate(slot: dict, source_mv: dict, window: int) -> str | None:
    """Compatibility helper: return the highest-damage valid punish key."""
    ladder = _normal_preview_punish_ladder(slot, source_mv, window)
    if not ladder:
        return None
    best = ladder.get("damage")
    return str(best.get("key")) if isinstance(best, dict) else None


def _normal_preview_punish_from_source(
    slots: list[dict],
    source_slot: str,
    source_mv: dict,
) -> tuple[dict[str, set[str]], dict[str, dict[str, str]], int | None, str | None]:
    """Return a fastest/highest-damage punish ladder for both opposing slots."""
    adv_block = _normal_advantage(source_mv, "block")
    if adv_block is None or adv_block >= 0:
        return {}, {}, adv_block, source_slot

    source_team = "P1" if str(source_slot).startswith("P1") else "P2" if str(source_slot).startswith("P2") else ""
    target_team = "P2" if source_team == "P1" else "P1" if source_team == "P2" else ""
    if not target_team:
        return {}, {}, adv_block, source_slot

    keys_by_slot: dict[str, set[str]] = {}
    roles_by_slot: dict[str, dict[str, str]] = {}
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        slot_label = str(slot.get("slot_label") or slot.get("slot") or "")
        if not slot_label.startswith(target_team):
            continue
        ladder = _normal_preview_punish_ladder(slot, source_mv, -int(adv_block))
        if not ladder:
            continue
        role_keys: dict[str, str] = {}
        for role in ("fast", "damage"):
            entry = ladder.get(role)
            if isinstance(entry, dict) and entry.get("key"):
                role_keys[role] = str(entry["key"])
        if role_keys:
            roles_by_slot[slot_label] = role_keys
            keys_by_slot[slot_label] = set(role_keys.values())
    return keys_by_slot, roles_by_slot, adv_block, source_slot


def _normal_preview_matchup_from_source(
    slots: list[dict],
    source_slot: str,
    source_mv: dict,
) -> tuple[dict[str, set[str]], dict[str, dict[str, str]], str | None]:
    """Match a selected normal's notation against both opposing character cards."""
    source_team = "P1" if str(source_slot).startswith("P1") else "P2" if str(source_slot).startswith("P2") else ""
    target_team = "P2" if source_team == "P1" else "P1" if source_team == "P2" else ""
    wanted = _normal_canonical_label(_normal_move_label(source_mv)) or _normal_canon_label(_normal_move_label(source_mv))
    if not target_team or not wanted:
        return {}, {}, source_slot

    keys_by_slot: dict[str, set[str]] = {}
    roles_by_slot: dict[str, dict[str, str]] = {}
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        slot_label = str(slot.get("slot_label") or slot.get("slot") or "")
        if not slot_label.startswith(target_team):
            continue
        for mv in _normal_visible_moves(slot.get("moves") or []):
            canon = _normal_canonical_label(_normal_move_label(mv)) or _normal_canon_label(_normal_move_label(mv))
            if canon != wanted:
                continue
            key = _normal_preview_key(mv)
            keys_by_slot.setdefault(slot_label, set()).add(key)
            roles_by_slot.setdefault(slot_label, {})["match"] = key
            break
    return keys_by_slot, roles_by_slot, source_slot


def _normal_preview_punish_keys(slots: list[dict], selection: dict | None) -> tuple[dict[str, set[str]], dict[str, dict[str, str]], int | None, str | None]:
    """Resolve a selected normal into both opponents' punish ladders."""
    source_slot, source_mv = _normal_preview_selected_move(slots, selection)
    if not source_slot or not isinstance(source_mv, dict):
        return {}, {}, None, None
    return _normal_preview_punish_from_source(slots, source_slot, source_mv)


def _normal_preview_live_punish_keys(slots: list[dict]) -> tuple[dict[str, set[str]], dict[str, dict[str, str]], int | None, str | None]:
    """Resolve the one currently executing point normal into a punish ladder."""
    source_slot, source_mv = _normal_preview_live_source(slots)
    if not source_slot or not isinstance(source_mv, dict):
        return {}, {}, None, None
    return _normal_preview_punish_from_source(slots, source_slot, source_mv)


def _normal_preview_matchup_keys(slots: list[dict], selection: dict | None) -> tuple[dict[str, set[str]], dict[str, dict[str, str]], int | None, str | None]:
    """Resolve the selected row to equivalent notation on the opposing team."""
    source_slot, source_mv = _normal_preview_selected_move(slots, selection)
    if not source_slot or not isinstance(source_mv, dict):
        return {}, {}, None, None
    keys, roles, source = _normal_preview_matchup_from_source(slots, source_slot, source_mv)
    return keys, roles, None, source


def _normal_preview_highlight_keys(slots: list[dict], mode: str, selection: dict | None) -> tuple[dict[str, set[str]], dict[str, dict[str, str]], int | None, str | None]:
    """Return highlighted rows plus role details for the active preview tool."""
    mode = str(mode or "none")
    if mode == "adv_hit":
        mode = "adv_block"
    if mode == "punish":
        return _normal_preview_punish_keys(slots, selection)
    if mode == "live_punish":
        return _normal_preview_live_punish_keys(slots)
    if mode == "matchup":
        return _normal_preview_matchup_keys(slots, selection)
    if mode not in {"fast", "damage", "adv_block", "safe", "unsafe"}:
        return {}, {}, None, None
    out: dict[str, set[str]] = {}
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        slot_label = str(slot.get("slot_label") or slot.get("slot") or "")
        keys = _normal_preview_best_keys(_normal_visible_moves(slot.get("moves") or []), mode)
        if keys:
            out[slot_label] = keys
    return out, {}, None, None


def _normal_preview_status(slots: list[dict], mode: str, selection: dict | None) -> str:
    """Build the compact status label beside the preview controls."""
    mode = str(mode or "none")
    if mode == "adv_hit":
        mode = "adv_block"
    if mode not in {"punish", "live_punish", "matchup"}:
        return "Click active filter to clear"

    if mode == "live_punish":
        source_slot, source_mv = _normal_preview_live_source(slots)
        if not source_slot or not isinstance(source_mv, dict):
            return "Waiting for one live normal"
    else:
        source_slot, source_mv = _normal_preview_selected_move(slots, selection)
        if not source_slot or not isinstance(source_mv, dict):
            return "Select a move to test"

    label = _normal_move_label(source_mv)
    if mode == "matchup":
        highlighted, _roles, _source = _normal_preview_matchup_from_source(slots, source_slot, source_mv)
        if not highlighted:
            return f"{source_slot} {label}: no matching normal on the opposing team"
        return f"{source_slot} {label}: matched on {', '.join(sorted(highlighted))}"

    adv_block = _normal_advantage(source_mv, "block")
    prefix = "Live " if mode == "live_punish" else ""
    if adv_block is None:
        return f"{prefix}{source_slot} {label}: block value unavailable"
    if adv_block >= 0:
        return f"{prefix}{source_slot} {label}: safe on block ({adv_block:+d})"

    highlighted, ladders, _adv, _source = _normal_preview_punish_from_source(slots, source_slot, source_mv)
    response_kind = "air-to-air" if _normal_preview_is_air_move(source_mv) else "ground"
    window = -int(adv_block)
    if not highlighted:
        return f"{prefix}{source_slot} {label}: no {response_kind} punish in {window}f"

    bits = []
    for target in sorted(ladders):
        slot = next((item for item in slots if str(item.get("slot_label") or item.get("slot") or "") == target), {})
        ladder = _normal_preview_punish_ladder(slot, source_mv, window) if isinstance(slot, dict) else None
        fast = ladder.get("fast") if isinstance(ladder, dict) else None
        damage = ladder.get("damage") if isinstance(ladder, dict) else None
        if isinstance(fast, dict) and isinstance(damage, dict):
            bits.append(f"{target} F:{fast.get('label')} {fast.get('startup')}f D:{damage.get('label')} {damage.get('damage')}")
        elif isinstance(fast, dict):
            bits.append(f"{target} F:{fast.get('label')} {fast.get('startup')}f")
    return f"{prefix}{source_slot} {label} {adv_block:+d}: " + " | ".join(bits)


def _draw_scan_metric_chip(
    surf: pygame.Surface,
    rect: pygame.Rect,
    smallfont: pygame.font.Font,
    label: str,
    value: str,
    accent: tuple[int, int, int],
) -> None:
    """Draw a cleaner metric cell for the normals preview.

    The earlier chip style worked, but looked a bit busy once multiplied across
    four cards. This version keeps a subtle boxed cell, a calm neutral fill,
    and a tiny accent rail on the left so the preview feels more like a polished
    data table and less like a wall of little buttons.
    """
    _draw_vertical_gradient(
        surf,
        rect,
        (22, 25, 35),
        (15, 18, 26),
        236,
    )
    pygame.draw.rect(surf, (44, 51, 71), rect, 1, border_radius=4)

    rail = pygame.Rect(rect.x + 1, rect.y + 1, 2, max(1, rect.height - 2))
    pygame.draw.rect(surf, _darken(accent, 12), rail, border_radius=1)

    label_s = smallfont.render(label, True, GUI_TEXT_DIM)
    value_s = _render_outlined_text(
        smallfont,
        value,
        GUI_TEXT,
        (0, 0, 0),
        rect.width - label_s.get_width() - 10,
        outline_px=1,
    )

    x = rect.x + 6
    surf.blit(label_s, (x, rect.y + (rect.height - label_s.get_height()) // 2))
    surf.blit(
        value_s,
        (
            rect.right - value_s.get_width() - 5,
            rect.y + (rect.height - value_s.get_height()) // 2,
        ),
    )


def draw_scan_normals_polished(
    surf: pygame.Surface,
    rect: pygame.Rect,
    font: pygame.font.Font,
    smallfont: pygame.font.Font,
    scan_data,
    *,
    t_ms: int = 0,
    scan_fx_by_slot: dict | None = None,
    highlight_mode: str = "none",
    selection: dict | None = None,
    mouse_pos: tuple[int, int] | None = None,
    advanced_open: bool = False,
) -> dict:
    """Draw the normals preview and return local click targets."""
    interaction = {"controls": {}, "rows": []}
    if rect.width <= 0 or rect.height <= 0:
        return interaction

    scan_fx_by_slot = scan_fx_by_slot or {}
    highlight_mode = str(highlight_mode or "none")
    mouse_pos = mouse_pos or (-10000, -10000)

    panel = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
    _draw_vertical_gradient(panel, panel.get_rect(), (14, 17, 26), (10, 12, 18), 255)
    surf.blit(panel, rect.topleft)

    title = smallfont.render("Scan: Normals Preview", True, GUI_TEXT)
    legend = smallfont.render("S startup | A active | R recovery | +H on hit | +B on block | D damage | blue = patched", True, GUI_TEXT_DIM)
    surf.blit(title, (rect.x + 10, rect.y + 7))
    surf.blit(legend, (rect.right - legend.get_width() - 10, rect.y + 7))
    pygame.draw.line(surf, (52, 61, 82), (rect.x + 8, rect.y + 24), (rect.right - 8, rect.y + 24))

    try:
        slots = list(scan_data or [])
    except Exception:
        slots = []

    ordered_labels = ["P1-C1", "P1-C2", "P2-C1", "P2-C2"]
    slot_map = {}
    for _s in [s for s in slots if isinstance(s, dict)]:
        _lbl = str(_s.get("slot_label") or _s.get("slot") or "")
        if _lbl and _lbl not in slot_map:
            slot_map[_lbl] = _s
    slots = [slot_map.get(lbl, {"slot_label": lbl, "char_name": "No character", "moves": []}) for lbl in ordered_labels]

    highlight_keys, highlight_roles, _punish_adv, _punish_source_slot = _normal_preview_highlight_keys(slots, highlight_mode, selection)
    status_text = _normal_preview_status(slots, highlight_mode, selection)

    control_y = rect.y + 28
    control_h = 17
    control_row_gap = 20
    advanced_modes = {"safe", "unsafe"}
    highlight_more_active = highlight_mode in advanced_modes
    label_s = smallfont.render("Highlight", True, GUI_TEXT_DIM)
    surf.blit(label_s, (rect.x + 10, control_y + (control_h - label_s.get_height()) // 2))
    control_x = rect.x + 10 + label_s.get_width() + 7
    for control_key in ("fast", "damage", "adv_block", "matchup"):
        meta = _NORMAL_PREVIEW_MODE_META[control_key]
        control_w = max(38, smallfont.size(meta["label"])[0] + 14)
        control_rect = pygame.Rect(control_x, control_y, control_w, control_h)
        draw_glass_button(
            surf,
            control_rect,
            meta["label"],
            smallfont,
            active=(highlight_mode == control_key),
            hover=control_rect.collidepoint(mouse_pos),
            accent=meta["color"],
            fill=(24, 29, 41),
        )
        interaction["controls"][control_key] = control_rect.copy()
        control_x += control_w + 4

    more_label = "More ▴" if advanced_open else "More ▾"
    more_w = max(44, smallfont.size(more_label)[0] + 14)
    more_rect = pygame.Rect(control_x, control_y, more_w, control_h)
    draw_glass_button(
        surf,
        more_rect,
        more_label,
        smallfont,
        active=(advanced_open or highlight_more_active),
        hover=more_rect.collidepoint(mouse_pos),
        accent=(146, 162, 192),
        fill=(24, 29, 41),
    )
    interaction["controls"]["__more__"] = more_rect.copy()
    control_x = more_rect.right + 4

    if advanced_open:
        adv_x = rect.x + 10 + label_s.get_width() + 7
        adv_y = control_y + control_row_gap
        adv_hint = smallfont.render("Advanced", True, GUI_TEXT_DIM)
        surf.blit(adv_hint, (rect.x + 10, adv_y + (control_h - adv_hint.get_height()) // 2))
        adv_x = rect.x + 10 + adv_hint.get_width() + 7
        for control_key in ("safe", "unsafe"):
            meta = _NORMAL_PREVIEW_MODE_META[control_key]
            control_w = max(42, smallfont.size(meta["label"])[0] + 14)
            control_rect = pygame.Rect(adv_x, adv_y, control_w, control_h)
            draw_glass_button(
                surf,
                control_rect,
                meta["label"],
                smallfont,
                active=(highlight_mode == control_key),
                hover=control_rect.collidepoint(mouse_pos),
                accent=meta["color"],
                fill=(24, 29, 41),
            )
            interaction["controls"][control_key] = control_rect.copy()
            adv_x += control_w + 4

    divider_x = control_x + 2
    pygame.draw.line(surf, (62, 73, 99), (divider_x, control_y + 2), (divider_x, control_y + control_h - 2))
    control_x = divider_x + 6
    punish_meta = _NORMAL_PREVIEW_MODE_META["punish"]
    punish_rect = pygame.Rect(control_x, control_y, max(48, smallfont.size(punish_meta["label"])[0] + 14), control_h)
    draw_glass_button(
        surf,
        punish_rect,
        punish_meta["label"],
        smallfont,
        active=(highlight_mode == "punish"),
        hover=punish_rect.collidepoint(mouse_pos),
        accent=punish_meta["color"],
        fill=(24, 29, 41),
    )
    interaction["controls"]["punish"] = punish_rect.copy()
    control_x = punish_rect.right + 4

    live_meta = _NORMAL_PREVIEW_MODE_META["live_punish"]
    live_rect = pygame.Rect(control_x, control_y, max(38, smallfont.size(live_meta["label"])[0] + 14), control_h)
    draw_glass_button(
        surf,
        live_rect,
        live_meta["label"],
        smallfont,
        active=(highlight_mode == "live_punish"),
        hover=live_rect.collidepoint(mouse_pos),
        accent=live_meta["color"],
        fill=(24, 29, 41),
    )
    interaction["controls"]["live_punish"] = live_rect.copy()
    control_x = live_rect.right + 9
    status_w = max(0, rect.right - control_x - 10)
    if status_w >= 48:
        status_s = _fit_text(smallfont, status_text, GUI_TEXT_MUTED, status_w)
        surf.blit(status_s, (control_x, control_y + (control_h - status_s.get_height()) // 2))

    controls_bottom_y = rect.y + (69 if advanced_open else 49)
    pygame.draw.line(surf, (52, 61, 82), (rect.x + 8, controls_bottom_y), (rect.right - 8, controls_bottom_y))

    pad, gap = 8, 10
    top = controls_bottom_y + 5
    card_h = max(44, rect.bottom - top - 8)
    count = 4
    card_w = max(140, (rect.width - pad * 2 - gap * (count - 1)) // count)
    dense = rect.height < 260 or rect.width < 930
    header_h = 24 if not dense else 22
    table_header_h = 16 if not dense else 14

    def _section_for_label(label: str) -> str:
        low = str(label or "").lower()
        if low.startswith("j.") or low.startswith("j"):
            return "Jump"
        if low.startswith("2"):
            return "Crouch"
        if low.startswith(("3", "4", "6")):
            return "Command"
        return "Stand"

    for si, slot in enumerate(slots):
        card_x = rect.x + pad + si * (card_w + gap)
        card = pygame.Rect(card_x, top, card_w, card_h)
        slot_label = str(slot.get("slot_label") or slot.get("slot") or f"S{si + 1}")
        slot_fx = scan_fx_by_slot.get(slot_label, {}) if isinstance(scan_fx_by_slot, dict) else {}

        card_fill = pygame.Surface((card.width, card.height), pygame.SRCALPHA)
        _draw_vertical_gradient(card_fill, card_fill.get_rect(), (18, 22, 32), (12, 14, 21), 238)
        surf.blit(card_fill, card.topleft)

        char_name = str(slot.get("char_name") or slot.get("character") or slot.get("name") or "No character")
        accent = _slot_accent_for_label(slot_label, muted=True)
        pygame.draw.rect(surf, (43, 52, 72), card, 1, border_radius=6)
        pygame.draw.rect(surf, accent, pygame.Rect(card.x, card.y, 3, card.height), border_radius=2)

        header_rect = pygame.Rect(card.x + 1, card.y + 1, card.width - 2, header_h)
        _draw_vertical_gradient(surf, header_rect, (25, 30, 43), (17, 20, 30), 236)
        pygame.draw.rect(surf, (180, 205, 245, 16), pygame.Rect(header_rect.x + 4, header_rect.y + 2, header_rect.width - 8, max(2, header_rect.height // 5)), border_radius=3)
        pygame.draw.line(surf, (44, 52, 72), (header_rect.x + 6, header_rect.bottom), (header_rect.right - 6, header_rect.bottom))
        slot_s = _render_outlined_text(font, slot_label, accent, (0, 0, 0), 76, outline_px=1)
        surf.blit(slot_s, (card.x + 9, card.y + 4))
        name_s = _fit_text(smallfont, char_name, GUI_TEXT_MUTED, card.width - 90)
        surf.blit(name_s, (card.x + 72, card.y + 6))

        moves = slot.get("moves") or []
        if not isinstance(moves, list):
            moves = []
        visible_moves = _normal_visible_moves(moves)
        is_empty_card = len(visible_moves) <= 0

        cur_id = slot.get("cur_anim") or slot.get("current_anim") or slot.get("mv_id_display") or slot.get("move_id")
        try:
            cur_id = int(cur_id) if cur_id is not None else None
        except Exception:
            cur_id = None
        cur_label = str(slot.get("cur_label") or slot.get("current_move") or slot.get("mv_label") or "").strip().lower()

        table_x = card.x + 6
        table_y = card.y + header_h + 4
        table_w = card.width - 12
        table_h = card.height - header_h - 8
        # Keep a small breathing gap beneath the metric header.  Without it,
        # the top stroke of the first normal row can visually merge with the
        # header separator on compact cards.
        first_row_gap = 3
        metric_headers = ("S", "A", "R", "+H", "+B", "D")
        metric_count = len(metric_headers)
        # The preview prioritizes startup, active, recovery, advantage, and
        # damage. Raw hitstun/blockstun remain available in the Frame Data view.
        preferred_move_col_w = 48 if card.width >= 260 else 42
        metric_col_w = max(1, (table_w - preferred_move_col_w) // metric_count)
        move_col_w = table_w - metric_col_w * metric_count
        grid_x, grid_y = table_x, table_y
        grid_w, grid_h = table_w, table_h

        table_bg = pygame.Rect(grid_x, grid_y, grid_w, grid_h)
        pygame.draw.rect(surf, (13, 16, 24), table_bg, border_radius=4)
        pygame.draw.rect(surf, (49, 59, 82), table_bg, 1, border_radius=4)

        hdr = pygame.Rect(grid_x, grid_y, grid_w, table_header_h)
        pygame.draw.rect(surf, (18, 22, 31), hdr, border_radius=4)
        header_labels = ("GND",) + metric_headers
        header_widths = (move_col_w,) + (metric_col_w,) * metric_count
        cell_x = grid_x
        for i, (txt, cell_w) in enumerate(zip(header_labels, header_widths)):
            header_cell = pygame.Rect(cell_x, grid_y, cell_w, table_header_h)
            header_fill = (29, 35, 49) if i % 2 == 0 else (23, 28, 40)
            pygame.draw.rect(surf, header_fill, header_cell)
            pygame.draw.rect(surf, (57, 68, 94), header_cell, 1)
            hdr_s = smallfont.render(txt, True, GUI_TEXT_DIM)
            surf.blit(hdr_s, (header_cell.x + (header_cell.width - hdr_s.get_width()) // 2, header_cell.y + (header_cell.height - hdr_s.get_height()) // 2))
            cell_x += cell_w

        if is_empty_card:
            empty_body = pygame.Rect(
                grid_x + 1,
                grid_y + table_header_h + first_row_gap + 1,
                grid_w - 2,
                max(1, grid_h - table_header_h - first_row_gap - 2),
            )
            pygame.draw.rect(surf, (11, 14, 21), empty_body, border_radius=4)
            had_scan_entry = bool(slot.get("_had_scan_entry"))
            if char_name == "No character":
                empty_msg = "No character loaded"
                sub_msg = "This slot is currently empty"
            elif bool(slot.get("profile_cache_miss")):
                empty_msg = "No saved normal profile"
                sub_msg = "Open Frame Data to run an explicit scan."
            elif had_scan_entry:
                empty_msg = "No normals returned"
                sub_msg = "The scan completed, but this slot returned no normal data"
            else:
                empty_msg = "Waiting for scan"
                sub_msg = "Normals will appear here when data is available"
            def _wrap_preview_message(text, text_font, max_width):
                words = str(text or "").split()
                if not words:
                    return [""]
                lines = []
                current = ""
                for word in words:
                    candidate = word if not current else f"{current} {word}"
                    if current and text_font.size(candidate)[0] > max_width:
                        lines.append(current)
                        current = word
                    else:
                        current = candidate
                if current:
                    lines.append(current)
                return lines

            title_lines = _wrap_preview_message(empty_msg, font, empty_body.width - 16)
            detail_lines = _wrap_preview_message(sub_msg, smallfont, empty_body.width - 16)
            line_items = [(line, font) for line in title_lines] + [(line, smallfont) for line in detail_lines]
            total_h = sum(text_font.get_height() + 2 for _, text_font in line_items)
            text_y = empty_body.y + max(8, (empty_body.height - total_h) // 2)
            for line, text_font in line_items:
                line_s = _render_outlined_text(text_font, line, GUI_TEXT_DIM, (0, 0, 0), empty_body.width - 16, 1)
                surf.blit(line_s, (empty_body.x + (empty_body.width - line_s.get_width()) // 2, text_y))
                text_y += text_font.get_height() + 2
            continue

        row_count = max(1, len(visible_moves))
        available_h = max(1, grid_h - table_header_h - first_row_gap)

        # Reserve a real section band before the first aerial normal instead of
        # painting the AIR badge on top of the preceding ground row.
        air_start_indexes: set[int] = set()
        previous_air = None
        for _index, _move in enumerate(visible_moves):
            if not isinstance(_move, dict):
                continue
            _is_air = _normal_preview_is_air_move(_move)
            if _index > 0 and _is_air and previous_air is False:
                air_start_indexes.add(_index)
            previous_air = _is_air
        divider_count = len(air_start_indexes)
        desired_divider_h = 10 if divider_count else 0
        row_h = max(9, min(18, (available_h - desired_divider_h * divider_count) // row_count))
        divider_h = 0
        if divider_count:
            divider_h = max(5, min(10, (available_h - row_h * row_count) // divider_count))
        # The last line remains inside the grid even on compact layouts.
        while divider_count and row_h * row_count + divider_h * divider_count > available_h and row_h > 8:
            row_h -= 1
            divider_h = max(5, min(10, (available_h - row_h * row_count) // divider_count))

        y = grid_y + table_header_h + first_row_gap
        sweep_frac = float(slot_fx.get("row_sweep", 0.0) or 0.0)
        for mi, mv in enumerate(visible_moves):
            if not isinstance(mv, dict):
                continue
            label = _normal_move_label(mv)
            is_air_row = _normal_preview_is_air_move(mv)
            if mi in air_start_indexes and divider_h > 0:
                band = pygame.Rect(grid_x + 2, y, max(1, grid_w - 4), divider_h)
                pygame.draw.rect(surf, (15, 28, 44), band)
                pygame.draw.line(surf, (86, 132, 185), (band.x, band.y), (band.right, band.y))
                pygame.draw.line(surf, (41, 69, 103), (band.x, band.bottom - 1), (band.right, band.bottom - 1))
                air_tag = _render_outlined_text(smallfont, "AIR", (144, 202, 255), (0, 0, 0), band.width - 14, outline_px=1)
                surf.blit(air_tag, (band.x + 7, band.y + (band.height - air_tag.get_height()) // 2))
                y += divider_h

            row = pygame.Rect(grid_x, y, grid_w, row_h)
            if is_air_row:
                row_fill = (14, 20, 31) if mi % 2 == 0 else (12, 18, 28)
            else:
                row_fill = (16, 19, 28) if mi % 2 == 0 else (13, 16, 24)
            mv_id = mv.get("id") or mv.get("anim") or mv.get("move_id")
            try:
                mv_id = int(mv_id) if mv_id is not None else None
            except Exception:
                mv_id = None
            row_label_canon = _normal_canon_label(label)
            row_key = _normal_preview_key(mv)
            is_selected = _normal_preview_selection_matches(selection, slot_label, mv)
            is_highlighted = row_key in highlight_keys.get(slot_label, set())
            row_roles = highlight_roles.get(slot_label, {}) if isinstance(highlight_roles, dict) else {}
            is_ladder_fast = row_key == row_roles.get("fast")
            is_ladder_damage = row_key == row_roles.get("damage")
            is_matchup = row_key == row_roles.get("match")
            cur_label_canon = _normal_canon_label(cur_label)
            if cur_label_canon:
                is_current = (row_label_canon == cur_label_canon)
            else:
                is_current = (cur_id is not None and mv_id == cur_id)
            if is_current:
                glow = pygame.Surface((row.width, row.height), pygame.SRCALPHA)
                glow.fill((*accent, 48))
                surf.blit(glow, row.topleft)
                pygame.draw.rect(surf, (*accent, 130), row, 1)
                pygame.draw.line(surf, (*accent, 95), (row.x + 1, row.bottom - 1), (row.right - 1, row.bottom - 1))
                if sweep_frac > 0.0:
                    sweep_x = row.x - 20 + int((row.width + 40) * sweep_frac)
                    sweep = pygame.Surface((24, row.height + 6), pygame.SRCALPHA)
                    pygame.draw.rect(sweep, (*_brighten(accent, 28), 70), pygame.Rect(0, 0, 10, row.height + 6), border_radius=4)
                    pygame.draw.rect(sweep, (*_brighten(accent, 48), 28), pygame.Rect(8, 0, 16, row.height + 6), border_radius=4)
                    surf.blit(sweep, (sweep_x, row.y - 3), special_flags=pygame.BLEND_ALPHA_SDL2 if hasattr(pygame, 'BLEND_ALPHA_SDL2') else 0)
            else:
                pygame.draw.rect(surf, row_fill, row)
                pygame.draw.rect(surf, (34, 41, 58), row, 1)
            pygame.draw.line(surf, (28, 34, 48), (row.x + 1, row.bottom), (row.right - 1, row.bottom))

            if is_highlighted:
                highlight_col = _NORMAL_PREVIEW_MODE_META.get(highlight_mode, {}).get("color", GUI_ACCENT_BLUE)
                highlight_fill = pygame.Surface((row.width, row.height), pygame.SRCALPHA)
                highlight_fill.fill((*highlight_col, 28))
                surf.blit(highlight_fill, row.topleft)
                pygame.draw.rect(surf, (*highlight_col, 178), pygame.Rect(row.x + 1, row.y + 1, 3, max(1, row.height - 2)), border_radius=1)
                pygame.draw.line(surf, (*highlight_col, 108), (row.x + 5, row.bottom - 2), (row.right - 3, row.bottom - 2))

            if is_ladder_fast:
                pygame.draw.line(surf, (102, 218, 255), (row.x + 5, row.y + 2), (row.right - 5, row.y + 2), 1)
            if is_ladder_damage:
                pygame.draw.line(surf, (244, 194, 98), (row.x + 5, row.bottom - 3), (row.right - 5, row.bottom - 3), 1)
            if is_matchup:
                pygame.draw.rect(surf, (104, 211, 227, 180), row.inflate(-4, -4), 1, border_radius=2)

            if is_selected:
                selection_col = (244, 180, 92)
                pygame.draw.rect(surf, (*selection_col, 205), row.inflate(-2, -2), 1, border_radius=2)
                pygame.draw.rect(surf, (*selection_col, 190), pygame.Rect(row.right - 4, row.y + 2, 2, max(1, row.height - 4)), border_radius=1)

            interaction["rows"].append({"rect": row.copy(), "slot_label": slot_label, "key": row_key})

            label_col = GUI_TEXT if (is_current or is_selected) else (218, 224, 234)
            label_s = _render_outlined_text(smallfont, label, label_col, (0, 0, 0), move_col_w - 20, outline_px=1)
            surf.blit(label_s, (row.x + 6, row.y + (row.height - label_s.get_height()) // 2))
            role_tag = ""
            role_col = None
            if is_ladder_fast and is_ladder_damage:
                role_tag, role_col = "F/D", (208, 228, 255)
            elif is_ladder_fast:
                role_tag, role_col = "F", (102, 218, 255)
            elif is_ladder_damage:
                role_tag, role_col = "D", (244, 194, 98)
            elif is_matchup:
                role_tag, role_col = "=", (104, 211, 227)
            if role_tag:
                tag_s = smallfont.render(role_tag, True, role_col)
                surf.blit(tag_s, (row.x + move_col_w - tag_s.get_width() - 4, row.y + (row.height - tag_s.get_height()) // 2))

            startup = _normal_int(mv, "startup", "start", "active_start")
            a1 = _normal_int(mv, "active_start", "a_start")
            a2 = _normal_int(mv, "active_end", "a_end")
            recovery = _normal_recovery(mv)
            hit = _normal_int(mv, "hitstun", "hit", "h")
            block = _normal_int(mv, "blockstun", "block", "b")
            active_txt = "-"
            hit_segments = mv.get("hit_segments") or []
            if isinstance(hit_segments, list) and hit_segments:
                first_seg = hit_segments[0] if isinstance(hit_segments[0], dict) else {}
                startup = _normal_int(first_seg, "startup", "start", "active_start") or startup
                hit = _normal_int(first_seg, "hitstun", "hit", "h") if _normal_int(first_seg, "hitstun", "hit", "h") is not None else hit
                block = _normal_int(first_seg, "blockstun", "block", "b") if _normal_int(first_seg, "blockstun", "block", "b") is not None else block
            if isinstance(hit_segments, list) and len(hit_segments) > 1:
                parts = []
                for seg in hit_segments[:3]:
                    if not isinstance(seg, dict):
                        continue
                    s1 = _normal_int(seg, "active_start", "a_start")
                    s2 = _normal_int(seg, "active_end", "a_end")
                    if s1 is not None and s2 is not None:
                        parts.append(f"{s1}-{s2}")
                    elif s1 is not None:
                        parts.append(str(s1))
                if len(hit_segments) > 3:
                    parts.append(f"+{len(hit_segments) - 3}")
                active_txt = "/".join(parts) if parts else "-"
            elif a1 is not None and a2 is not None:
                active_txt = f"{a1}-{a2}"
            elif a1 is not None:
                active_txt = str(a1)

            # Stored advantage is preferred. For freshly scanned rows that do
            # not have it yet, use the same local stun-minus-recovery estimate.
            adv_hit = _normal_advantage(mv, "hit")
            adv_block = _normal_advantage(mv, "block")
            if adv_hit is None and hit is not None and recovery is not None:
                adv_hit = int(hit) - int(recovery)
            if adv_block is None and block is not None and recovery is not None:
                adv_block = int(block) - int(recovery)
            damage = _normal_damage(mv)

            values = [
                "-" if startup is None else str(startup),
                active_txt,
                "-" if recovery is None else str(recovery),
                "-" if adv_hit is None else f"{adv_hit:+d}",
                "-" if adv_block is None else f"{adv_block:+d}",
                "-" if damage is None else str(damage),
            ]
            values = [str(value).replace(",", "") for value in values]
            patch_fields = mv.get("_fd_patch_fields") or set()
            try:
                patch_fields = set(patch_fields)
            except Exception:
                patch_fields = set()
            if isinstance(hit_segments, list):
                for seg in hit_segments:
                    if not isinstance(seg, dict):
                        continue
                    try:
                        patch_fields.update(set(seg.get("_fd_patch_fields") or []))
                    except Exception:
                        pass
            metric_groups = ("active", "active", "recovery", "adv_hit", "adv_block", "damage")
            value_col = GUI_TEXT if is_current else (205, 211, 224)
            patched_col = _brighten(accent, 52) if is_current else (145, 194, 255)
            for i, value in enumerate(values):
                col_left = grid_x + move_col_w + i * metric_col_w
                is_patched_metric = metric_groups[i] in patch_fields
                if is_patched_metric:
                    chip_rect = pygame.Rect(col_left + 2, row.y + 2, metric_col_w - 4, max(1, row.height - 4))
                    chip = pygame.Surface((chip_rect.width, chip_rect.height), pygame.SRCALPHA)
                    pygame.draw.rect(chip, (*patched_col, 28), chip.get_rect(), border_radius=3)
                    pygame.draw.rect(chip, (*patched_col, 92), chip.get_rect(), 1, border_radius=3)
                    surf.blit(chip, chip_rect.topleft)
                draw_col = patched_col if is_patched_metric else value_col
                val_s = _render_outlined_text(smallfont, value, draw_col, (0, 0, 0), metric_col_w - 6, outline_px=1)
                surf.blit(val_s, (col_left + (metric_col_w - val_s.get_width()) // 2, row.y + (row.height - val_s.get_height()) // 2))
            y += row_h

        # Full-height separators are drawn after the row fills so each column
        # remains visible across the complete data grid.
        for i in range(metric_count + 1):
            vx = grid_x + move_col_w + metric_col_w * i
            pygame.draw.line(surf, (62, 73, 99), (vx, grid_y + 1), (vx, grid_y + grid_h - 2))
        pygame.draw.line(surf, (62, 73, 99), (grid_x, grid_y + table_header_h), (grid_x + grid_w, grid_y + table_header_h))

    return interaction


_QUICK_ASSIST_STRENGTH_MARKS = ("α", "β", "γ")
# UMvC3-style assist strength colors: Alpha red, Beta green, Gamma blue.
_QUICK_ASSIST_STRENGTH_COLORS = (
    (236, 70, 82),
    (74, 214, 114),
    (82, 156, 255),
)


def _quick_assist_strength_meta(quick_index: int, is_default: bool = False) -> tuple[str, tuple[int, int, int]] | None:
    """Return the visual assist-strength marker for custom quick assists.

    This is intentionally display-only. The quick-assist JSON labels stay
    unchanged for lookup/write logic, while the first three non-default buttons
    get Marvel-style Alpha/Beta/Gamma markers.
    """
    if is_default:
        return None
    try:
        qi = int(quick_index)
    except Exception:
        return None
    if 0 <= qi < len(_QUICK_ASSIST_STRENGTH_MARKS):
        return _QUICK_ASSIST_STRENGTH_MARKS[qi], _QUICK_ASSIST_STRENGTH_COLORS[qi]
    return None


def _quick_assist_display_label(label: str, quick_index: int, is_default: bool = False) -> str:
    """Return the raw visible move label. Strength marks are drawn separately."""
    return str(label or "")


def _quick_assist_accent_for_label(
    label: str,
    is_default: bool = False,
    quick_index: int | None = None,
) -> tuple[int, int, int]:
    """Return the accent color used by quick-assist buttons."""
    if is_default:
        return GUI_TEXT_DIM
    meta = _quick_assist_strength_meta(quick_index, is_default) if quick_index is not None else None
    if meta:
        return meta[1]
    return GUI_CONFIRM


def _draw_quick_assist_button(
    surf: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    font: pygame.font.Font,
    *,
    active: bool = False,
    hover: bool = False,
    accent: tuple[int, int, int] = GUI_ACCENT_BLUE,
    fill: tuple[int, int, int] | None = None,
    mark_meta: tuple[str, tuple[int, int, int]] | None = None,
) -> None:
    """Draw a quick-assist button with a colored Alpha/Beta/Gamma marker lane."""
    draw_glass_button(
        surf,
        rect,
        "",
        font,
        active=active,
        hover=hover,
        accent=accent,
        fill=fill,
        align="center",
    )

    draw_rect = rect.move(0, -1 if hover else 0)
    text_col = GUI_TEXT if active or hover else GUI_TEXT_MUTED

    if mark_meta:
        mark, mark_col = mark_meta
        mark_lane_w = max(20, min(26, rect.width // 4))
        divider_x = draw_rect.x + mark_lane_w

        mark_surf = _render_outlined_text(
            font,
            mark,
            mark_col,
            (0, 0, 0),
            max(8, mark_lane_w - 5),
            outline_px=1,
        )
        surf.blit(
            mark_surf,
            (
                draw_rect.x + (mark_lane_w - mark_surf.get_width()) // 2,
                draw_rect.y + (draw_rect.height - mark_surf.get_height()) // 2,
            ),
        )

        divider_top = draw_rect.y + 4
        divider_bottom = draw_rect.bottom - 4
        pygame.draw.line(
            surf,
            _darken(mark_col, 46),
            (divider_x, divider_top),
            (divider_x, divider_bottom),
            1,
        )
        pygame.draw.line(
            surf,
            _brighten(mark_col, 20),
            (divider_x + 1, divider_top),
            (divider_x + 1, divider_bottom),
            1,
        )

        text_x = divider_x + 6
        text_w = max(8, draw_rect.right - text_x - 6)
        label_surf = _render_outlined_text(
            font,
            label,
            text_col,
            (0, 0, 0),
            text_w,
            outline_px=1,
        )
        tx = text_x + (text_w - label_surf.get_width()) // 2
    else:
        text_w = max(8, draw_rect.width - 12)
        label_surf = _render_outlined_text(
            font,
            label,
            text_col,
            (0, 0, 0),
            text_w,
            outline_px=1,
        )
        tx = draw_rect.x + (draw_rect.width - label_surf.get_width()) // 2

    ty = draw_rect.y + (draw_rect.height - label_surf.get_height()) // 2
    surf.blit(label_surf, (tx, ty))


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return 1.0 - ((1.0 - t) * (1.0 - t) * (1.0 - t))


def _ease_in_out_smootherstep(t: float) -> float:
    """Smooth 0..1 easing with gentle start and finish.

    This reads better for short UI travel than a pure ease-out curve because
    the selector does not launch at full speed on the first visible frame.
    """
    t = max(0.0, min(1.0, float(t)))
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)

def _apply_panel_element_enter_animation(
    panel_surf: pygame.Surface,
    panel_fx: dict | None,
    now: float,
) -> pygame.Surface:
    """Cascade fighter-card contents in after the card itself starts entering.

    This is intentionally cheap: it slices the already-rendered card into a few
    horizontal content bands and gives each band a tiny delayed fade/slide.  The
    scanner profile cache buys us enough room for this polish without creating
    new per-widget draw state or expensive per-pixel work every frame.
    """
    if not isinstance(panel_fx, dict):
        return panel_surf
    entry = panel_fx.get("panel_enter")
    if not isinstance(entry, dict):
        return panel_surf

    try:
        start = float(entry.get("start", 0.0) or 0.0)
        dur = max(0.001, float(entry.get("dur", 0.68) or 0.68))
    except Exception:
        return panel_surf
    if start <= 0.0:
        return panel_surf

    raw = (float(now) - start) / dur
    if raw <= 0.0:
        raw = 0.0
    if raw >= 1.0:
        return panel_surf

    w, h = panel_surf.get_size()
    if w <= 0 or h <= 0:
        return panel_surf

    out = pygame.Surface((w, h), pygame.SRCALPHA)

    # Leave a low-alpha ghost of the full card so the panel never looks empty
    # while the content bands are staggering in.
    ghost = panel_surf.copy()
    ghost.set_alpha(max(20, min(110, int(28 + 72 * _ease_out_cubic(raw)))))
    out.blit(ghost, (0, 0))

    bands = [
        (0, int(h * 0.35), 0.00, -10, 5),       # portrait/header/HP
        (int(h * 0.27), int(h * 0.56), 0.08, -8, 4),   # pool/baroque
        (int(h * 0.48), int(h * 0.74), 0.16, -6, 3),   # move/status
        (int(h * 0.66), h, 0.24, 0, 5),         # buttons/quick assists
    ]

    for y0, y1, delay, dx, dy in bands:
        y0 = max(0, min(h, int(y0)))
        y1 = max(y0, min(h, int(y1)))
        if y1 <= y0:
            continue
        denom = max(0.001, 1.0 - delay)
        local = max(0.0, min(1.0, (raw - delay) / denom))
        eased = _ease_out_cubic(local)
        if eased <= 0.0:
            continue
        piece = panel_surf.subsurface(pygame.Rect(0, y0, w, y1 - y0)).copy()
        piece.set_alpha(max(0, min(255, int(255 * eased))))
        out.blit(piece, (int((1.0 - eased) * dx), y0 + int((1.0 - eased) * dy)))

    return out

def draw_quick_assist_footer(
    surf: pygame.Surface,
    panel_rect: pygame.Rect,
    slot_label: str,
    snap: dict | None,
    smallfont: pygame.font.Font,
    *,
    mx_local: int,
    my_local: int,
    btn_y: int,
    get_quick_defs_fn,
    active_quick_index: int | None = None,
    flash_quick_index: int | None = None,
    slide_anim: dict | None = None,
) -> dict:
    """Draw a compact one-line quick-assist strip.

    The first polish pass used a two-line footer with a visible header plus
    buttons. It looked nice, but it stole too much vertical room from the
    character panels. This version keeps the same click behavior and assist
    logic, but compresses the UI into one clean row:

        Assist | move | move | move | default
    """
    quick_defs = []

    if snap:
        try:
            quick_defs = get_quick_defs_fn(slot_label, snap)[:4]
        except Exception:
            quick_defs = []

    if not quick_defs and snap:
        quick_defs = [
            {"label": "304", "table": 304},
            {"label": "305", "table": 305},
            {"label": "306", "table": 306},
            {"label": "Default", "default": True},
        ]

    if not quick_defs:
        return {}

    qa_count = min(4, len(quick_defs))
    qa_gap = 6
    qa_h = 20
    label_w = 64
    side_pad = 10

    qa_y = max(72, btn_y - qa_h - 10)
    strip_y = max(0, qa_y - 5)
    strip_h = min(panel_rect.height - strip_y - 4, qa_h + 10)
    strip_rect = pygame.Rect(6, strip_y, panel_rect.width - 12, strip_h)

    _draw_vertical_gradient(
        surf,
        strip_rect,
        (22, 25, 35),
        (15, 17, 24),
        230,
    )
    pygame.draw.rect(surf, (54, 62, 82), strip_rect, 1, border_radius=5)

    label_surf = smallfont.render("Assist", True, GUI_TEXT_DIM)
    surf.blit(
        label_surf,
        (
            strip_rect.x + 8,
            qa_y + (qa_h - label_surf.get_height()) // 2,
        ),
    )

    qa_x0 = strip_rect.x + label_w
    qa_total_w = strip_rect.width - label_w - side_pad
    qa_w = max(48, int((qa_total_w - qa_gap * (qa_count - 1)) / qa_count))

    out = {}

    # Precompute button geometry so the selected marker can slide from the old
    # quick assist to the new quick assist without keeping the old one lit.
    button_rows = []
    for qi, quick in enumerate(quick_defs):
        qx = qa_x0 + qi * (qa_w + qa_gap)
        qrect_local = pygame.Rect(qx, qa_y, qa_w, qa_h)
        raw_qlabel = str(quick.get("label", f"A{qi + 1}"))
        is_default_quick = bool(quick.get("default", False))
        qlabel = _quick_assist_display_label(raw_qlabel, qi, is_default_quick)
        mark_meta = _quick_assist_strength_meta(qi, is_default_quick)
        accent = _quick_assist_accent_for_label(raw_qlabel, is_default_quick, qi)
        button_rows.append((qi, quick, qrect_local, qlabel, accent, mark_meta))

    # Sliding selection plate. Use a time-based smootherstep motion, a longer
    # duration, and no immediate selected-button fill during travel. That keeps
    # the change readable at 60 FPS instead of feeling like a jump plus a small
    # underline animation.
    selected_rect = None
    selected_accent = GUI_ACCENT_BLUE
    slide_is_active = False
    slide_frac = 1.0

    if active_quick_index is not None:
        for qi, _quick, qrect_local, _qlabel, accent, _mark_meta in button_rows:
            if qi == int(active_quick_index):
                selected_rect = qrect_local
                selected_accent = accent
                break

    if selected_rect is not None:
        marker_rect = selected_rect.copy()
        src_rect = None
        dst_rect = selected_rect.copy()

        if isinstance(slide_anim, dict):
            try:
                src_i = int(slide_anim.get("from", active_quick_index))
                dst_i = int(slide_anim.get("to", active_quick_index))
                start_ts = float(slide_anim.get("start", 0.0) or 0.0)
                dur = max(0.001, float(slide_anim.get("dur", 0.38) or 0.38))

                if dst_i == int(active_quick_index) and start_ts > 0.0:
                    for qi, _quick, qrect_local, _qlabel, _accent, _mark_meta in button_rows:
                        if qi == src_i:
                            src_rect = qrect_local
                        if qi == dst_i:
                            dst_rect = qrect_local

                    if src_rect is not None and dst_rect is not None:
                        raw_frac = max(0.0, min(1.0, (time.time() - start_ts) / dur))
                        slide_frac = _ease_in_out_smootherstep(raw_frac)
                        slide_is_active = raw_frac < 0.995 and src_i != dst_i

                        marker_rect = pygame.Rect(
                            round(src_rect.x + (dst_rect.x - src_rect.x) * slide_frac),
                            round(src_rect.y + (dst_rect.y - src_rect.y) * slide_frac),
                            round(src_rect.width + (dst_rect.width - src_rect.width) * slide_frac),
                            round(src_rect.height + (dst_rect.height - src_rect.height) * slide_frac),
                        )
            except Exception:
                marker_rect = selected_rect.copy()
                slide_is_active = False

        # Motion trail. This is subtle, but it gives the selector a continuous
        # path across the buttons instead of only a single hard-edged rectangle.
        if slide_is_active and src_rect is not None and dst_rect is not None:
            for back_i, alpha_mul in ((2, 0.22), (1, 0.38)):
                lag = max(0.0, slide_frac - 0.08 * back_i)
                trail_rect = pygame.Rect(
                    round(src_rect.x + (dst_rect.x - src_rect.x) * lag),
                    round(src_rect.y + (dst_rect.y - src_rect.y) * lag),
                    round(src_rect.width + (dst_rect.width - src_rect.width) * lag),
                    round(src_rect.height + (dst_rect.height - src_rect.height) * lag),
                )
                trail = pygame.Surface((trail_rect.width + 14, trail_rect.height + 14), pygame.SRCALPHA)
                pygame.draw.rect(
                    trail,
                    (*selected_accent, int(46 * alpha_mul)),
                    pygame.Rect(7, 7, trail_rect.width, trail_rect.height),
                    border_radius=8,
                )
                surf.blit(trail, (trail_rect.x - 7, trail_rect.y - 7))

        # Main selector plate. Keep it pronounced, but with a smoother glow and
        # a softer top sheen so the movement reads cleanly.
        glow = pygame.Surface((marker_rect.width + 20, marker_rect.height + 20), pygame.SRCALPHA)
        pygame.draw.rect(
            glow,
            (*selected_accent, 68),
            pygame.Rect(10, 10, marker_rect.width, marker_rect.height),
            border_radius=8,
        )
        pygame.draw.rect(
            glow,
            (*selected_accent, 28),
            pygame.Rect(4, 4, marker_rect.width + 12, marker_rect.height + 12),
            2,
            border_radius=10,
        )
        surf.blit(glow, (marker_rect.x - 10, marker_rect.y - 10))

        plate = pygame.Surface((marker_rect.width + 4, marker_rect.height + 4), pygame.SRCALPHA)
        plate_rect = plate.get_rect()
        pygame.draw.rect(
            plate,
            (*selected_accent, 46),
            plate_rect,
            border_radius=6,
        )
        pygame.draw.rect(
            plate,
            (150, 165, 190, 18),
            pygame.Rect(2, 2, plate_rect.width - 4, max(2, plate_rect.height // 5)),
            border_radius=5,
        )
        pygame.draw.rect(
            plate,
            (*selected_accent, 165),
            plate_rect,
            2,
            border_radius=6,
        )
        surf.blit(plate, (marker_rect.x - 2, marker_rect.y - 2))

        rail_h = 4
        rail_rect = pygame.Rect(
            marker_rect.x + 5,
            marker_rect.bottom - rail_h - 1,
            max(4, marker_rect.width - 10),
            rail_h,
        )
        pygame.draw.rect(surf, selected_accent, rail_rect, border_radius=2)

        # Tiny settle pulse after the slide lands.
        if isinstance(slide_anim, dict):
            try:
                start_ts = float(slide_anim.get("start", 0.0) or 0.0)
                dur = max(0.001, float(slide_anim.get("dur", 0.38) or 0.38))
                raw = (time.time() - start_ts) / dur if start_ts else 99.0
                if 1.0 <= raw <= 1.32:
                    settle_t = (raw - 1.0) / 0.32
                    ring_alpha = int((1.0 - settle_t) * 85)
                    ring_expand = int(settle_t * 7)
                    pulse_rect = marker_rect.inflate(6 + ring_expand * 2, 4 + ring_expand * 2)
                    pulse = pygame.Surface((pulse_rect.width + 8, pulse_rect.height + 8), pygame.SRCALPHA)
                    pygame.draw.rect(
                        pulse,
                        (*selected_accent, ring_alpha),
                        pygame.Rect(4, 4, pulse_rect.width, pulse_rect.height),
                        2,
                        border_radius=10,
                    )
                    surf.blit(pulse, (pulse_rect.x - 4, pulse_rect.y - 4))
            except Exception:
                pass

    for qi, quick, qrect_local, qlabel, accent, mark_meta in button_rows:
        qhover = qrect_local.collidepoint(mx_local, my_local)

        is_selected = active_quick_index is not None and int(active_quick_index) == qi
        is_flashing = flash_quick_index is not None and int(flash_quick_index) == qi

        # During a slide, the moving selector is the highlight. Do not also
        # repaint the destination button as fully selected on frame 1; that
        # double-state is what made the animation feel choppy.
        is_selected_fill = is_selected and not slide_is_active
        active = bool(quick.get("active", False)) or is_selected_fill or is_flashing

        fill = (58, 72, 104) if is_selected_fill else (35, 43, 62)
        if is_flashing:
            fill = _brighten(fill, 30)

        _draw_quick_assist_button(
            surf,
            qrect_local,
            qlabel,
            smallfont,
            active=active,
            hover=qhover,
            accent=accent,
            fill=fill,
            mark_meta=mark_meta,
        )

        if is_selected_fill:
            pygame.draw.rect(
                surf,
                (*accent, 95),
                qrect_local.inflate(-3, -3),
                2,
                border_radius=4,
            )

        qclick = pygame.Rect(
            panel_rect.x + qrect_local.x,
            panel_rect.y + qrect_local.y,
            qrect_local.width,
            qrect_local.height,
        ).inflate(8, 8)

        out[(slot_label, qi)] = qclick

    return out

__all__ = [
    '_normal_button_accent',
    '_normal_id_to_label',
    '_normal_move_label',
    '_normal_canon_label',
    '_normal_int',
    '_normal_recovery',
    '_normal_advantage',
    '_normal_damage',
    '_NORMAL_PREVIEW_ORDER',
    '_NORMAL_PREVIEW_RANK',
    '_OPTIONAL_PREVIEW_NORMALS',
    '_HIDDEN_PREVIEW_NORMALS',
    '_normal_canonical_label',
    '_normal_preview_label_allowed',
    '_normal_row_quality',
    '_normal_visible_moves',
    '_NORMAL_PREVIEW_MODE_META',
    '_normal_preview_move_id',
    '_normal_preview_key',
    '_normal_preview_selection_matches',
    '_normal_preview_selected_move',
    '_normal_preview_is_air_move',
    '_normal_preview_current_move',
    '_normal_preview_assist_standby',
    '_normal_preview_active_slot',
    '_normal_preview_live_source',
    '_normal_preview_best_keys',
    '_normal_preview_punish_ladder',
    '_normal_preview_punish_candidate',
    '_normal_preview_punish_from_source',
    '_normal_preview_matchup_from_source',
    '_normal_preview_punish_keys',
    '_normal_preview_live_punish_keys',
    '_normal_preview_matchup_keys',
    '_normal_preview_highlight_keys',
    '_normal_preview_status',
    '_draw_scan_metric_chip',
    'draw_scan_normals_polished',
    '_QUICK_ASSIST_STRENGTH_MARKS',
    '_QUICK_ASSIST_STRENGTH_COLORS',
    '_quick_assist_strength_meta',
    '_quick_assist_display_label',
    '_quick_assist_accent_for_label',
    '_draw_quick_assist_button',
    '_ease_out_cubic',
    '_ease_in_out_smootherstep',
    '_apply_panel_element_enter_animation',
    'draw_quick_assist_footer'
]
