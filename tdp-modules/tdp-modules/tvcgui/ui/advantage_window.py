"""Observed block advantage comparison window for the lower workspace."""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import pygame

tk = None
ttk = None
tk_call = None

from tvcgui.core.paths import resolve_data_path, resource_path
from tvcgui.features.combat.move_filters import is_purged_move_label
from tvcgui.ui.components import (
    GUI_APP_ACCENT,
    GUI_CONFIRM,
    GUI_DANGER,
    GUI_TEXT,
    GUI_TEXT_DIM,
    GUI_TEXT_MUTED,
    _brighten,
    _darken,
    _draw_vertical_gradient,
    _fit_text,
    _render_outlined_text,
    draw_glass_button,
)
from tvcgui.ui.normal_preview import (
    _NORMAL_PREVIEW_RANK,
    _normal_canon_label,
    _normal_canonical_label,
    _normal_move_label,
    _normal_observed_block_advantage,
    _normal_advantage,
    _normal_int,
    _normal_damage,
    _normal_preview_active_slot,
)

_ADVANTAGE_CACHE: dict[str, Any] = {
    "next_check": 0.0,
    "stamp": None,
    "data": None,
}

_ADV_TK_WIN: Any = None
_ADV_TK_SOURCE_KEY: str | None = None
_ADV_CATEGORY_RANK = {"normal": 0, "special": 1, "super": 2}
_ADV_CATEGORY_LABEL = {"normal": "Normals", "special": "Specials", "super": "Supers"}
_ADV_MIN_VALID_STARTUP = 3
_ADV_MAX_PUNISHES = 3
_ADV_ICON_CACHE: dict[str, Any] = {"asset_map": None, "images": {}}
_ADV_STRENGTH_ORDER = (("light", "L"), ("medium", "M"), ("heavy", "H"))
_TK_TEXT = "#e2e6ee"
_TK_MUTED = "#969eb0"
_ADV_CHAR_COL_WIDTH = 116
_ADV_ATTACK_COL_WIDTH = 178
_ADV_HEADER_HEIGHT = 70


def _adv_norm_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _adv_attack_key(label: Any) -> str:
    label_text = str(label or "").strip()
    canon = _normal_canonical_label(label_text) or _normal_canon_label(label_text)
    if canon:
        return str(canon)
    compact = re.sub(r"\s+", " ", label_text).strip()
    return compact if compact else "?"


def _adv_contextual_label(char_id: int | None, char_name: str, profile_key: str, label: Any) -> str:
    """Split character-specific command variants that share the same input text."""
    text = str(label or "").strip()
    compact = re.sub(r"[^a-z0-9]+", "", text.lower())
    name_key = _adv_norm_text(char_name)
    profile_text = str(profile_key or "").strip().lower()
    is_volnutt = char_id == 18 or name_key in {"volnutt", "megamanvolnutt"} or profile_text == "id_18_volnutt"
    if is_volnutt:
        if compact == "6b":
            return "6B Fist"
        if compact == "3b":
            return "Drill 3B"
    return text




def _adv_strip_wiki_label(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = text.replace("{{Tv-olors", "{{TvC-Colors")
    text = re.sub(r"\{\{TvC-Colors\|[^|{}]+\|([^{}]*?)\}\}", r"\1", text)
    text = re.sub(r"\{\{TvC-Colors\|[^|{}]+\|", "", text)
    text = re.sub(r"\{\{TvCInput\|([^{}|]+)\}\}", r"\1", text)
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.I)
    text = re.sub(r"\[\[File:[^\]]*?\|thumb\|center\|", "", text, flags=re.I)
    text = re.sub(r"\[\[File:[^\]]*?\]\]", "", text, flags=re.I)
    text = text.replace("[[", "").replace("]]", "")
    text = text.replace("{{", "").replace("}}", "")
    text = text.replace("thumb|center", "")
    text = re.sub(r"\s*\|\s*File currently missing\s*$", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text).strip(" ' \t\r\n")
    return text


def _adv_label_override(char_id: int | None, char_name: str, profile_key: str, label: Any) -> str | None:
    raw = str(label or "").strip()
    clean = _adv_strip_wiki_label(raw)
    name_key = _adv_norm_text(char_name)
    profile_text = str(profile_key or "").strip().lower()
    raw_key = _adv_norm_text(raw)
    clean_key = _adv_norm_text(clean)

    def is_char(*keys: str) -> bool:
        return name_key in keys or profile_text in keys

    overrides: dict[tuple[str, str], str] = {
        ("kentheeagle", "a"): "Bird Shoot",
        ("juntheswan", "a"): "Lightning Kick",
        ("tekkamanblade", "a"): "Baselard",
        ("kentheeagle", "eaglerushj"): "Eagle Rush",
        ("tekkamanblade", "a_b"): "Katzbalger",
        ("yatterman1", "623x"): "Yatterspin",
        ("polimar", "46x"): "Hurricane Destruction Fist",
        ("ptx40a", "caliberexecution2_8xthumbcenter"): "Caliber Execution",
        ("ptx40a", "fileptxtacklesnapbackwebmthumbcenter4_6csnapback"): "Tackle Snapback",
    }
    for key in (raw_key, clean_key):
        mapped = overrides.get((name_key, key))
        if mapped:
            return mapped
    if is_char("id_01_ken_the_eagle") and raw_key == "a":
        return "Bird Shoot"
    if is_char("id_08_jun_the_swan") and raw_key == "a":
        return "Lightning Kick"
    if is_char("id_26_tekkaman_blade") and raw_key == "a":
        return "Baselard"
    return None


def _adv_clean_display_label(char_id: int | None, char_name: str, profile_key: str, label: Any) -> str:
    override = _adv_label_override(char_id, char_name, profile_key, label)
    if override:
        return override
    clean = _adv_strip_wiki_label(label)
    override = _adv_label_override(char_id, char_name, profile_key, clean)
    if override:
        return override
    return clean or str(label or "").strip()


def _adv_move_segment_from_label(label: Any) -> str:
    """Return air for jump-only moves, otherwise ground."""
    text = str(label or "").strip().lower()
    compact = re.sub(r"\s+", " ", text)
    canon = _normal_canonical_label(compact) or _normal_canon_label(compact)
    if str(canon or "").lower().startswith("j."):
        return "air"
    if compact.startswith(("j.", "j ", "jump ", "air ")):
        return "air"
    if compact.endswith(" j.") or compact.endswith(" air"):
        return "air"
    if re.search(r"(?:^|[\s\[\(])(?:j\.|jump|air|airborne)(?:$|[\s\]\)])", compact):
        return "air"
    return "ground"


def _adv_item_segment(item: dict[str, Any] | None) -> str:
    if not isinstance(item, dict):
        return "ground"
    segment = str(item.get("segment") or item.get("air_ground") or "").strip().lower()
    if segment in {"air", "airborne", "jump", "jumping"}:
        return "air"
    if segment in {"ground", "grounded", "standing", "crouching"}:
        return "ground"
    return _adv_move_segment_from_label(item.get("label") or item.get("key") or "")


def _adv_clean_value(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return int(value)
    text = str(value).strip()
    if not text or text in {"-", "?", "n/a", "N/A"}:
        return None
    text = text.replace("\u2212", "-").replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"\s+", "", text)
    try:
        return int(text)
    except Exception:
        return text


def _adv_block_advantage_value(mv: dict[str, Any]) -> Any:
    if not isinstance(mv, dict):
        return None
    for key in (
        "adv_block_observed",
        "observed_adv_block",
        "wiki_adv_block",
        "block_adv_observed",
        "on_block_observed",
        "observed_block_advantage",
        "advantage_on_block",
        "blockadv",
    ):
        value = _adv_clean_value(mv.get(key))
        if value is not None:
            return value
    value = _normal_observed_block_advantage(mv)
    if value is not None:
        return value
    return _normal_advantage(mv, "block", prefer_observed=True)


def _adv_startup_value(mv: dict[str, Any]) -> int | None:
    if not isinstance(mv, dict):
        return None
    startup = _normal_int(mv, "startup", "start", "active_start", "a_start")
    if startup is None or int(startup) < _ADV_MIN_VALID_STARTUP:
        return None
    return int(startup)


def _adv_damage_value(mv: dict[str, Any]) -> int | None:
    if not isinstance(mv, dict):
        return None
    try:
        value = _normal_damage(mv)
    except Exception:
        value = None
    if value is not None:
        return value
    return _normal_int(mv, "damage", "dmg", "base_damage")


def _adv_int_value(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except Exception:
        match = re.search(r"[+-]?\d+", str(value or ""))
        if not match:
            return None
        try:
            return int(match.group(0))
        except Exception:
            return None


def _adv_move_category(mv: dict[str, Any], label: str = "") -> str | None:
    kind = str((mv or {}).get("kind") or (mv or {}).get("move_kind") or (mv or {}).get("category") or "").strip().lower()
    kind = kind.replace(" ", "_").replace("-", "_")
    if kind in {"normal", "normals", "button"}:
        return "normal"
    if kind in {"special", "specials", "special_move", "specials_move"}:
        return "special"
    if kind in {"super", "supers", "hyper", "hyper_combo", "super_move", "super_art"}:
        return "super"
    canon = _normal_canonical_label(label) or _normal_canon_label(label)
    if canon:
        return "normal"
    return None


def _adv_attack_rank(attack_key: str, label: str, category: str | None = None) -> tuple[int, int, str]:
    low = str(attack_key or label or "").lower()
    cat_rank = _ADV_CATEGORY_RANK.get(str(category or "").lower(), 9)
    if low in _NORMAL_PREVIEW_RANK:
        return (cat_rank, _NORMAL_PREVIEW_RANK[low], low)
    return (cat_rank, 9000, low)


def _adv_format(value: Any) -> str:
    if value is None or value == "":
        return "-"
    try:
        return f"{int(value):+d}"
    except Exception:
        return str(value)

def _adv_danger_badge(value: Any) -> str:
    adv = _adv_int_value(value)
    if adv is None:
        return ""
    if adv >= 0:
        return "OK"
    window = abs(int(adv))
    if window >= 13:
        return "H"
    if window >= 9:
        return "M"
    if window >= 5:
        return "L"
    return "safe"


def _adv_danger_label(value: Any) -> str:
    badge = _adv_danger_badge(value)
    labels = {
        "OK": "plus or even",
        "safe": "negative but usually safe",
        "L": "light punish range",
        "M": "medium punish range",
        "H": "heavy punish range",
    }
    return labels.get(badge, "unknown")


def _adv_format_cell(value: Any, *, show_badge: bool = True) -> str:
    return _adv_format(value)


def _adv_cell_window(value: Any) -> int | None:
    adv = _adv_int_value(value)
    if adv is None or adv >= 0:
        return None
    return abs(int(adv))


def _adv_row_filter_match(values: list[Any], mode: str) -> bool:
    mode = str(mode or "").strip().lower()
    ints = [_adv_int_value(value) for value in values]
    ints = [value for value in ints if value is not None]
    if not ints:
        return mode in {"", "all rows"}
    if mode in {"", "all rows"}:
        return True
    if mode == "unsafe":
        return any(value < 0 for value in ints)
    if mode == "punishable":
        return any(value <= -5 for value in ints)
    if mode == "big unsafe":
        return any(value <= -13 for value in ints)
    if mode == "plus or even":
        return any(value >= 0 for value in ints)
    return True


def _adv_char_side(char: dict[str, Any]) -> str:
    name = _adv_norm_text(char.get("name") or char.get("key") or "")
    capcom = {
        "alex",
        "batsu",
        "chunli",
        "frankwest",
        "megamanvolnutt",
        "morriganaensland",
        "morrigan",
        "roll",
        "ryu",
        "sakiomokane",
        "saki",
        "kaijinnosoki",
        "soki",
        "viewtifuljoe",
        "zero",
    }
    return "capcom" if name in capcom else "tatsunoko"



def _adv_color(value: Any) -> tuple[int, int, int]:
    try:
        ivalue = int(value)
    except Exception:
        match = re.search(r"[+-]?\d+", str(value or ""))
        if not match:
            return GUI_TEXT_MUTED
        try:
            ivalue = int(match.group(0))
        except Exception:
            return GUI_TEXT_MUTED
    if ivalue >= 0:
        return _brighten(GUI_CONFIRM, 18)
    if ivalue <= -10:
        return _brighten(GUI_DANGER, 18)
    return GUI_TEXT


def _adv_profile_path() -> str:
    return resolve_data_path("frame_data", "frame_data_preview_profiles.json")


def _adv_observed_profile_path() -> str:
    return resolve_data_path("frame_data", "observed_block_advantage_profiles.json")


def _adv_existing_stamp(path: str) -> tuple[str, int | None, int | None]:
    try:
        stat = os.stat(path)
        return (path, int(stat.st_mtime), int(stat.st_size))
    except Exception:
        return (path, None, None)


def _adv_merge_data(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    by_key = base.setdefault("by_key", {})
    by_name = base.setdefault("by_name", {})
    by_id = base.setdefault("by_id", {})
    chars = base.setdefault("chars", [])
    for incoming in overlay.get("chars") or []:
        if not isinstance(incoming, dict):
            continue
        key = str(incoming.get("key") or "").strip()
        if not key:
            continue
        target = by_key.get(key)
        if target is None:
            target = dict(incoming)
            incoming_attacks = dict(incoming.get("attacks_by_key") or {})
            target["attacks_by_key"] = {
                attack_key: item
                for attack_key, item in incoming_attacks.items()
                if not is_purged_move_label(target, item if isinstance(item, dict) else None, item.get("label") if isinstance(item, dict) else attack_key)
            }
            target["attacks"] = sorted(target["attacks_by_key"].values(), key=lambda item: item.get("rank", (9999, 9999, str(item.get("label") or ""))))
            target["has_observed"] = bool(target.get("attacks"))
            by_key[key] = target
            chars.append(target)
        else:
            merged = dict(target.get("attacks_by_key") or {})
            for attack_key, incoming_item in (incoming.get("attacks_by_key") or {}).items():
                if not isinstance(incoming_item, dict):
                    continue
                if is_purged_move_label(target, incoming_item, incoming_item.get("label") or attack_key):
                    merged.pop(attack_key, None)
                    continue
                old_item = merged.get(attack_key)
                if isinstance(old_item, dict):
                    item = dict(old_item)
                    for field, value in incoming_item.items():
                        if value is None or value == "":
                            continue
                        item[field] = value
                    for preserved in ("startup", "damage", "category", "category_label", "rank", "label", "key", "segment"):
                        if (item.get(preserved) is None or item.get(preserved) == "") and old_item.get(preserved) not in (None, ""):
                            item[preserved] = old_item.get(preserved)
                    merged[attack_key] = item
                else:
                    merged[attack_key] = dict(incoming_item)
            merged = {
                key: item
                for key, item in merged.items()
                if not is_purged_move_label(target, item if isinstance(item, dict) else None, (item or {}).get("label") if isinstance(item, dict) else key)
            }
            target["attacks_by_key"] = merged
            target["attacks"] = sorted(merged.values(), key=lambda item: item.get("rank", (9999, 9999, str(item.get("label") or ""))))
            target["has_observed"] = bool(target.get("attacks"))
        norm_name = _adv_norm_text(target.get("name") or incoming.get("name"))
        if norm_name and norm_name not in by_name:
            by_name[norm_name] = key
        try:
            cid = int(target.get("char_id"))
        except Exception:
            cid = None
        if cid is not None and cid not in by_id:
            by_id[cid] = key
    chars.sort(key=lambda item: item.get("sort", (999, str(item.get("name") or "").lower())))
    base["observed_chars"] = [char for char in chars if bool(char.get("has_observed"))]
    return base


def _adv_pretty_key(key: str) -> str:
    text = str(key or "").strip()
    text = re.sub(r"^id_\d+_", "", text)
    text = text.replace("_", " ").replace("-", "-")
    return " ".join(part.capitalize() for part in text.split()) or "Unknown"


def _adv_build_profile_data(doc: Any) -> dict[str, Any]:
    profiles = doc.get("profiles") if isinstance(doc, dict) else {}
    if not isinstance(profiles, dict):
        profiles = {}

    chars: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    by_name: dict[str, str] = {}
    by_id: dict[int, str] = {}

    for profile_key, prof in profiles.items():
        if not isinstance(prof, dict):
            continue
        key = str(profile_key or prof.get("key") or "").strip()
        if not key:
            continue
        char_name = str(prof.get("char_name") or _adv_pretty_key(key)).strip() or _adv_pretty_key(key)
        try:
            char_id = int(prof.get("char_id")) if prof.get("char_id") is not None else None
        except Exception:
            char_id = None

        attacks_by_key: dict[str, dict[str, Any]] = {}
        moves = prof.get("moves") or []
        if not isinstance(moves, list):
            moves = []
        for mv in moves:
            if not isinstance(mv, dict):
                continue
            observed = _adv_block_advantage_value(mv)
            if observed is None:
                continue
            label = _normal_move_label(mv)
            if not label or label == "?":
                label = str(mv.get("move_label") or mv.get("wiki_label") or "").strip()
            if not label or label == "?":
                continue
            label = _adv_contextual_label(char_id, char_name, key, label)
            label = _adv_clean_display_label(char_id, char_name, key, label)
            category = _adv_move_category(mv, str(label))
            if category not in _ADV_CATEGORY_RANK:
                continue
            char_ref = {"char_id": char_id, "char_name": char_name, "profile_key": key}
            if is_purged_move_label(char_ref, mv, label):
                continue
            base_attack_key = _adv_attack_key(label)
            attack_key = base_attack_key if category == "normal" else f"{category}:{base_attack_key}"
            old = attacks_by_key.get(attack_key)
            try:
                display_order = int(mv.get("display_order"))
            except Exception:
                display_order = None
            rank = _adv_attack_rank(base_attack_key, str(label), category)
            if display_order is not None and (category != "normal" or str(base_attack_key).lower() not in _NORMAL_PREVIEW_RANK):
                rank = (_ADV_CATEGORY_RANK.get(category, 9), display_order, str(label).lower())
            item = {
                "key": attack_key,
                "label": str(label),
                "adv": observed,
                "startup": _adv_startup_value(mv),
                "damage": _adv_damage_value(mv),
                "category": category,
                "category_label": _ADV_CATEGORY_LABEL.get(category, str(category).capitalize()),
                "segment": _adv_move_segment_from_label(label),
                "rank": rank,
            }
            if old is None or item["rank"] < old.get("rank", (9999, 9999, "")):
                attacks_by_key[attack_key] = item

        attacks = sorted(attacks_by_key.values(), key=lambda item: item.get("rank", (9999, 9999, str(item.get("label") or ""))))
        char = {
            "key": key,
            "char_id": char_id,
            "name": char_name,
            "attacks": attacks,
            "attacks_by_key": attacks_by_key,
            "has_observed": bool(attacks),
            "sort": (999 if char_id is None else int(char_id), char_name.lower()),
        }
        chars.append(char)
        by_key[key] = char
        norm_name = _adv_norm_text(char_name)
        if norm_name and norm_name not in by_name:
            by_name[norm_name] = key
        if char_id is not None and char_id not in by_id:
            by_id[char_id] = key

    chars.sort(key=lambda item: item.get("sort", (999, str(item.get("name") or "").lower())))
    observed_chars = [char for char in chars if bool(char.get("has_observed"))]
    return {
        "chars": chars,
        "observed_chars": observed_chars,
        "by_key": by_key,
        "by_name": by_name,
        "by_id": by_id,
        "loaded_at": time.time(),
    }


def load_observed_advantage_data(*, force: bool = False) -> dict[str, Any]:
    """Load the small observed-preview profile cache on demand."""
    now = time.time()
    if not force and _ADVANTAGE_CACHE.get("data") is not None and now < float(_ADVANTAGE_CACHE.get("next_check") or 0.0):
        return _ADVANTAGE_CACHE["data"]

    path = _adv_profile_path()
    observed_path = _adv_observed_profile_path()
    stamp = (_adv_existing_stamp(path), _adv_existing_stamp(observed_path))

    if not force and _ADVANTAGE_CACHE.get("data") is not None and _ADVANTAGE_CACHE.get("stamp") == stamp:
        _ADVANTAGE_CACHE["next_check"] = now + 1.0
        return _ADVANTAGE_CACHE["data"]

    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
    except Exception:
        doc = {}

    data = _adv_build_profile_data(doc)

    try:
        with open(observed_path, "r", encoding="utf-8") as f:
            observed_doc = json.load(f)
    except Exception:
        observed_doc = {}
    if isinstance(observed_doc, dict) and observed_doc.get("profiles"):
        data = _adv_merge_data(data, _adv_build_profile_data(observed_doc))

    data["path"] = path
    data["observed_path"] = observed_path
    _ADVANTAGE_CACHE["stamp"] = stamp
    _ADVANTAGE_CACHE["data"] = data
    _ADVANTAGE_CACHE["next_check"] = now + 1.0
    return data


def _adv_order_scan_rows(scan_data: Any) -> list[dict[str, Any]]:
    try:
        rows = list(scan_data or [])
    except Exception:
        rows = []
    slot_map: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("slot_label") or row.get("slot") or "")
        if label and label not in slot_map:
            slot_map[label] = row
    ordered = []
    for label in ("P1-C1", "P1-C2", "P2-C1", "P2-C2"):
        ordered.append(slot_map.get(label, {"slot_label": label, "moves": []}))
    return ordered


def _adv_key_for_slot(slot: dict[str, Any], data: dict[str, Any]) -> str | None:
    by_key = data.get("by_key") or {}
    by_name = data.get("by_name") or {}
    by_id = data.get("by_id") or {}

    for key_name in ("profile_key", "profile", "char_key"):
        key = str(slot.get(key_name) or "").strip()
        if key and key in by_key:
            return key

    for id_name in ("char_id", "character_id", "id"):
        try:
            cid = int(slot.get(id_name))
        except Exception:
            continue
        key = by_id.get(cid)
        if key:
            return key

    char_name = str(slot.get("char_name") or slot.get("character") or slot.get("name") or "").strip()
    norm = _adv_norm_text(char_name)
    if norm and norm in by_name:
        return by_name[norm]
    return None


def _adv_default_char_key(scan_data: Any, data: dict[str, Any]) -> str | None:
    slots = _adv_order_scan_rows(scan_data)
    for team in ("P1", "P2"):
        _slot_label, slot = _normal_preview_active_slot(slots, team)
        if isinstance(slot, dict):
            key = _adv_key_for_slot(slot, data)
            if key:
                return key

    by_id = data.get("by_id") or {}
    if 12 in by_id:
        return by_id[12]

    for key, char in (data.get("by_key") or {}).items():
        if "ryu" in str(key).lower() or _adv_norm_text(char.get("name")) == "ryu":
            return key

    chars = data.get("chars") or []
    if chars:
        return str(chars[0].get("key") or "") or None
    return None


def _adv_active_char_keys(scan_data: Any, data: dict[str, Any]) -> list[str]:
    """Return unique character keys currently visible in the live slots."""
    keys: list[str] = []
    seen: set[str] = set()
    for slot in _adv_order_scan_rows(scan_data):
        if not isinstance(slot, dict):
            continue
        key = _adv_key_for_slot(slot, data)
        if not key or key in seen:
            continue
        keys.append(key)
        seen.add(key)
    return keys


def _adv_order_chars_for_active_slots(chars: list[dict[str, Any]], active_keys: list[str]) -> list[dict[str, Any]]:
    """Place active slot characters first, then the rest of the cast in normal ID order."""
    by_key: dict[str, dict[str, Any]] = {}
    for char in chars:
        if not isinstance(char, dict):
            continue
        key = str(char.get("key") or "").strip()
        if key and key not in by_key:
            by_key[key] = char

    ordered: list[dict[str, Any]] = []
    used: set[str] = set()
    for key in active_keys:
        char = by_key.get(str(key or ""))
        if char is None:
            continue
        ordered.append(char)
        used.add(str(char.get("key") or ""))

    for char in chars:
        key = str(char.get("key") or "").strip()
        if not key or key in used:
            continue
        ordered.append(char)
        used.add(key)
    return ordered


def _adv_current_char_key(scan_data: Any, data: dict[str, Any], selection: dict[str, Any] | None) -> str | None:
    if isinstance(selection, dict) and bool(selection.get("lock_char")):
        key = str(selection.get("source_char_key") or "").strip()
        if key in (data.get("by_key") or {}):
            return key
    return _adv_default_char_key(scan_data, data)


def _adv_selected_attack(char: dict[str, Any] | None, selection: dict[str, Any] | None) -> str | None:
    attacks = char.get("attacks") if isinstance(char, dict) else []
    if not isinstance(attacks, list) or not attacks:
        return None
    if isinstance(selection, dict):
        wanted = str(selection.get("attack_key") or "").strip()
        if wanted and any(str(item.get("key") or "") == wanted for item in attacks if isinstance(item, dict)):
            return wanted
    return str(attacks[0].get("key") or "")



def _adv_norm_asset_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _adv_portrait_asset_map() -> dict[str, str]:
    cached = _ADV_ICON_CACHE.get("asset_map")
    if isinstance(cached, dict):
        return cached
    asset_map: dict[str, str] = {}
    try:
        portrait_dir = resource_path("assets", "portraits")
        for filename in os.listdir(portrait_dir):
            if not filename.lower().endswith((".png", ".gif")):
                continue
            stem = os.path.splitext(filename)[0]
            key = _adv_norm_asset_key(stem)
            if key and key not in asset_map:
                asset_map[key] = os.path.join(portrait_dir, filename)
    except Exception:
        asset_map = {}
    _ADV_ICON_CACHE["asset_map"] = asset_map
    return asset_map


def _adv_char_icon_path(char: dict[str, Any]) -> str | None:
    asset_map = _adv_portrait_asset_map()
    if not asset_map:
        return None
    candidates = (char.get("portrait"), char.get("icon"), char.get("name"), char.get("key"))
    for value in candidates:
        key = _adv_norm_asset_key(value)
        if not key:
            continue
        if key in asset_map:
            return asset_map[key]
        key = re.sub(r"^id\d+", "", key)
        if key in asset_map:
            return asset_map[key]
    return None


def _adv_load_char_icon(win: Any, char: dict[str, Any]) -> Any:
    if tk is None:
        return None
    path = _adv_char_icon_path(char)
    if not path:
        return None
    cache_key = f"{id(win)}|{path}|26"
    images = _ADV_ICON_CACHE.setdefault("images", {})
    if cache_key in images:
        return images[cache_key]
    try:
        image = tk.PhotoImage(master=win, file=path)
        sx = max(1, int(round(image.width() / 26)))
        sy = max(1, int(round(image.height() / 26)))
        image = image.subsample(sx, sy)
    except Exception:
        return None
    images[cache_key] = image
    try:
        refs = getattr(win, "_adv_header_icons", [])
        refs.append(image)
        setattr(win, "_adv_header_icons", refs)
    except Exception:
        pass
    return image


def _adv_set_char_heading(tree: Any, key: str, char: dict[str, Any], icon: Any = None) -> None:
    text = _adv_char_heading(char)
    try:
        if icon is not None:
            tree.heading(key, text=text, image=icon, anchor="center")
        else:
            tree.heading(key, text=text, anchor="center")
    except Exception:
        try:
            tree.heading(key, text=text)
        except Exception:
            pass


def _adv_char_short_name(char: dict[str, Any]) -> str:
    name = str(char.get("name") or char.get("key") or "Unknown").strip()
    replacements = {
        "Ken The Eagle": "Ken",
        "Jun The Swan": "Jun",
        "Gold Lightan": "Lightan",
        "Yatterman-1": "Yatt-1",
        "Yatterman 1": "Yatt-1",
        "Yatterman-2": "Yatt-2",
        "Yatterman 2": "Yatt-2",
        "Joe The Condor": "Joe",
        "Roll-Chan": "Roll",
    }
    short = replacements.get(name, name)
    if len(short) > 10:
        short = short[:9] + "…"
    return short or "Unknown"


def _adv_char_heading(char: dict[str, Any]) -> str:
    short = _adv_char_short_name(char)
    try:
        cid = int(char.get("char_id"))
    except Exception:
        cid = None
    return f"{cid:02d} {short}" if cid is not None else short


def _adv_char_header_text(char: dict[str, Any]) -> str:
    short = _adv_char_short_name(char)
    try:
        cid = int(char.get("char_id"))
    except Exception:
        cid = None
    return f"{cid:02d}\n{short}" if cid is not None else short


def _adv_source_label(char: dict[str, Any]) -> str:
    name = str(char.get("name") or char.get("key") or "Unknown").strip()
    try:
        cid = int(char.get("char_id"))
    except Exception:
        cid = None
    return f"{cid:02d} {name}" if cid is not None else name


def _adv_matrix_rows(source_char: dict[str, Any] | None, enabled_categories: set[str]) -> list[dict[str, Any]]:
    attacks = source_char.get("attacks") if isinstance(source_char, dict) else []
    if not isinstance(attacks, list):
        return []
    rows = []
    for item in attacks:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").lower()
        if category not in enabled_categories:
            continue
        rows.append(item)
    return rows


def _adv_punish_window(item: dict[str, Any] | None) -> int | None:
    adv = _adv_int_value((item or {}).get("adv") if isinstance(item, dict) else None)
    if adv is None or adv >= 0:
        return None
    return abs(int(adv))


def _adv_candidate_sort_key(item: dict[str, Any]) -> tuple[int, int, int, str]:
    return (
        int(_adv_int_value(item.get("startup")) or 9999),
        -int(_adv_int_value(item.get("damage")) or 0),
        _ADV_CATEGORY_RANK.get(str(item.get("category") or "").lower(), 9),
        str(item.get("label") or item.get("key") or ""),
    )


def _adv_startup_candidates(
    char: dict[str, Any] | None,
    enabled_categories: set[str],
    *,
    max_startup: int | None = None,
    strict_less_than: int | None = None,
    limit: int | None = None,
    segment: str | None = None,
) -> list[dict[str, Any]]:
    attacks = (char or {}).get("attacks") or []
    if not isinstance(attacks, list):
        return []
    candidates: list[dict[str, Any]] = []
    for item in attacks:
        if not isinstance(item, dict):
            continue
        category = str(item.get("category") or "").lower()
        if category not in enabled_categories:
            continue
        if segment in {"ground", "air"} and _adv_item_segment(item) != segment:
            continue
        startup = _adv_int_value(item.get("startup"))
        if startup is None or startup < _ADV_MIN_VALID_STARTUP:
            continue
        if max_startup is not None and startup > int(max_startup):
            continue
        if strict_less_than is not None and startup >= int(strict_less_than):
            continue
        candidates.append(item)
    candidates.sort(key=_adv_candidate_sort_key)
    if limit is not None:
        return candidates[: max(0, int(limit))]
    return candidates


def _adv_move_strength(item: dict[str, Any] | None) -> str | None:
    """Return light, medium, or heavy for button-style moves."""
    if not isinstance(item, dict):
        return None
    label = str(item.get("label") or item.get("key") or "").strip()
    canon = _normal_canonical_label(label)
    if canon:
        button = str(canon)[-1:].upper()
        if button == "A":
            return "light"
        if button == "B":
            return "medium"
        if button == "C":
            return "heavy"

    raw = re.sub(r"\s+", " ", label).strip().lower()
    if re.search(r"(?:^|[^a-z0-9])(?:a|light)(?:$|[^a-z0-9])", raw):
        return "light"
    if re.search(r"(?:^|[^a-z0-9])(?:b|medium)(?:$|[^a-z0-9])", raw):
        return "medium"
    if re.search(r"(?:^|[^a-z0-9])(?:c|heavy)(?:$|[^a-z0-9])", raw):
        return "heavy"
    return None


def _adv_strength_candidates(
    char: dict[str, Any] | None,
    enabled_categories: set[str],
    *,
    max_startup: int | None = None,
    strict_less_than: int | None = None,
    segment: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Pick the fastest valid light, medium, and heavy candidate, when present."""
    grouped: dict[str, dict[str, Any]] = {}
    for item in _adv_startup_candidates(
        char,
        enabled_categories,
        max_startup=max_startup,
        strict_less_than=strict_less_than,
        limit=None,
        segment=segment,
    ):
        strength = _adv_move_strength(item)
        if not strength or strength in grouped:
            continue
        grouped[strength] = item
        if len(grouped) >= len(_ADV_STRENGTH_ORDER):
            break
    return grouped


def _adv_strength_labels(grouped: dict[str, dict[str, Any]] | None) -> str:
    parts: list[str] = []
    grouped = grouped or {}
    for strength, short in _ADV_STRENGTH_ORDER:
        item = grouped.get(strength)
        if not item:
            continue
        label = _adv_move_startup_label(item)
        if label:
            parts.append(f"{short}: {label}")
    return " / ".join(parts)


def _adv_fastest_strength_item(grouped: dict[str, dict[str, Any]] | None) -> dict[str, Any] | None:
    items = [item for item in (grouped or {}).values() if isinstance(item, dict)]
    if not items:
        return None
    return sorted(items, key=_adv_candidate_sort_key)[0]


def _adv_punish_candidates(
    char: dict[str, Any],
    window: int,
    enabled_categories: set[str],
    *,
    limit: int = _ADV_MAX_PUNISHES,
    segment: str | None = None,
) -> list[dict[str, Any]]:
    return _adv_startup_candidates(char, enabled_categories, max_startup=int(window), limit=limit, segment=segment)


def _adv_punish_strength_candidates(
    char: dict[str, Any],
    window: int,
    enabled_categories: set[str],
    *,
    segment: str | None = None,
) -> dict[str, dict[str, Any]]:
    return _adv_strength_candidates(char, enabled_categories, max_startup=int(window), segment=segment)


def _adv_punish_candidate(char: dict[str, Any], window: int, enabled_categories: set[str]) -> dict[str, Any] | None:
    candidates = _adv_punish_candidates(char, window, enabled_categories, limit=1)
    return candidates[0] if candidates else None


def _adv_move_startup_label(item: dict[str, Any] | None) -> str:
    if not isinstance(item, dict):
        return ""
    label = str(item.get("label") or item.get("key") or "?").strip()
    startup = _adv_int_value(item.get("startup"))
    if len(label) > 8:
        label = label[:7] + "..."
    return f"{label} {startup}f" if startup is not None else label


def _adv_punish_label(item: dict[str, Any] | None) -> str:
    return _adv_move_startup_label(item)


def _adv_punish_labels(items: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> str:
    labels = [_adv_move_startup_label(item) for item in list(items or [])[:_ADV_MAX_PUNISHES]]
    return " / ".join(label for label in labels if label)


def _adv_attack_category(item: dict[str, Any] | None) -> str:
    if not isinstance(item, dict):
        return ""
    return str(item.get("category") or "").strip().lower()


def _adv_uses_unique_attack_window(item: dict[str, Any] | None) -> bool:
    return _adv_attack_category(item) in {"special", "super"}


def _adv_row_punish_segment(row_item: dict[str, Any] | None, cell_item: dict[str, Any] | None = None) -> str:
    item = cell_item if isinstance(cell_item, dict) else row_item
    if _adv_uses_unique_attack_window(row_item):
        return "air" if _adv_item_segment(item) == "air" else "ground"
    return _adv_item_segment(item)


def _adv_row_cell_window(row_item: dict[str, Any] | None, cell_item: dict[str, Any] | None) -> int | None:
    if isinstance(cell_item, dict):
        window = _adv_cell_window(cell_item.get("adv"))
        if window is not None:
            return window
    if _adv_uses_unique_attack_window(row_item):
        return _adv_cell_window((row_item or {}).get("adv") if isinstance(row_item, dict) else None)
    return None


def _adv_counter_trap_candidates(
    source_char: dict[str, Any] | None,
    selected_adv: Any,
    defender_punish: dict[str, Any] | None,
    enabled_categories: set[str],
    *,
    limit: int = _ADV_MAX_PUNISHES,
) -> list[dict[str, Any]]:
    adv = _adv_int_value(selected_adv)
    punish_startup = _adv_int_value((defender_punish or {}).get("startup") if isinstance(defender_punish, dict) else None)
    if adv is None or punish_startup is None:
        return []
    # The source move has to connect before the defender punish connects.
    # This is a startup-only check. It does not prove range, pushback, or invulnerability.
    strict_limit = int(punish_startup) + int(adv)
    if strict_limit <= _ADV_MIN_VALID_STARTUP:
        return []
    return _adv_startup_candidates(source_char, enabled_categories, strict_less_than=strict_limit, limit=limit)


def _adv_counter_trap_strength_candidates(
    source_char: dict[str, Any] | None,
    selected_adv: Any,
    defender_punish: dict[str, Any] | None,
    enabled_categories: set[str],
) -> dict[str, dict[str, Any]]:
    adv = _adv_int_value(selected_adv)
    punish_startup = _adv_int_value((defender_punish or {}).get("startup") if isinstance(defender_punish, dict) else None)
    if adv is None or punish_startup is None:
        return {}
    strict_limit = int(punish_startup) + int(adv)
    if strict_limit <= _ADV_MIN_VALID_STARTUP:
        return {}
    return _adv_strength_candidates(source_char, enabled_categories, strict_less_than=strict_limit)


def _adv_make_scan_display(scan_data: Any, live_slots: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    base_scan_map: dict[str, dict[str, Any]] = {}
    try:
        for row in list(scan_data or []):
            if isinstance(row, dict):
                label = str(row.get("slot_label") or row.get("slot") or "")
                if label:
                    base_scan_map[label] = dict(row)
    except Exception:
        base_scan_map = {}

    rows: list[dict[str, Any]] = []
    for label in ("P1-C1", "P1-C2", "P2-C1", "P2-C2"):
        row = dict(base_scan_map.get(label, {"slot_label": label, "moves": []}))
        snap = (live_slots or {}).get(label) if isinstance(live_slots, dict) else None
        if isinstance(snap, dict):
            row["mv_id_display"] = snap.get("mv_id_display")
            row["mv_label"] = snap.get("mv_label")
            row["char_name"] = snap.get("name") or snap.get("char_name") or row.get("char_name")
            if snap.get("char_id") is not None:
                row["char_id"] = snap.get("char_id")
        rows.append(row)
    return rows


def _adv_ensure_tk() -> bool:
    global tk, ttk, tk_call
    if tk is None or ttk is None:
        try:
            import tkinter as _tk
            from tkinter import ttk as _ttk
            tk = _tk
            ttk = _ttk
        except Exception:
            tk = None
            ttk = None
            return False
    if tk_call is None:
        try:
            from tvcgui.core.tk_host import tk_call as _tk_call
            tk_call = _tk_call
        except Exception:
            tk_call = None
            return False
    return True


def _adv_configure_tree_style(win: Any) -> str:
    style_name = "Advantage.Treeview"
    if ttk is None:
        return style_name
    try:
        style = ttk.Style(win)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(
            style_name,
            background="#11151e",
            fieldbackground="#11151e",
            foreground=_TK_TEXT,
            rowheight=22,
            borderwidth=0,
            relief="flat",
            font=("Segoe UI", 9),
        )
        style.configure(
            f"{style_name}.Heading",
            background="#202638",
            foreground=_TK_MUTED,
            borderwidth=1,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            padding=(2, 6, 2, 6),
        )
        style.map(style_name, background=[("selected", "#2a3655")], foreground=[("selected", _TK_TEXT)])
    except Exception:
        pass
    return style_name


def open_advantage_window(scan_data: Any = None, live_slots: dict[str, Any] | None = None) -> None:
    """Open the observed block advantage matrix in a native popup."""
    global _ADV_TK_WIN, _ADV_TK_SOURCE_KEY
    if not _adv_ensure_tk():
        print("[advantage] tkinter window unavailable", flush=True)
        return

    scan_display = _adv_make_scan_display(scan_data, live_slots)

    def _show(master_root: Any) -> None:
        global _ADV_TK_WIN, _ADV_TK_SOURCE_KEY
        old = _ADV_TK_WIN
        try:
            if old is not None and bool(old.winfo_exists()):
                old.destroy()
        except Exception:
            pass

        win = tk.Toplevel(master_root)
        _ADV_TK_WIN = win
        win.title("Advantage Matrix")
        try:
            win.geometry("1240x720")
            win.minsize(900, 520)
        except Exception:
            pass
        win.configure(bg="#151821")

        def _close() -> None:
            global _ADV_TK_WIN
            _ADV_TK_WIN = None
            try:
                win.destroy()
            except Exception:
                pass

        win.protocol("WM_DELETE_WINDOW", _close)
        style_name = _adv_configure_tree_style(win)

        body = tk.Frame(win, bg="#151821", padx=12, pady=12)
        body.pack(fill="both", expand=True)

        header = tk.Frame(body, bg="#151821")
        header.pack(fill="x")
        tk.Label(
            header,
            text="Advantage Matrix",
            bg="#151821",
            fg=_TK_TEXT,
            font=("Segoe UI", 12, "bold"),
            anchor="w",
        ).pack(side="left")
        tk.Label(
            header,
            text="Rows show observed block advantage. Click a negative row for L, M, and H punish rows. Use < > or Ctrl+Left/Right to change source.",
            bg="#151821",
            fg=_TK_MUTED,
            font=("Segoe UI", 9),
            anchor="e",
        ).pack(side="right")

        loading_label = tk.Label(
            body,
            text="Loading observed frame data...",
            bg="#151821",
            fg=_TK_MUTED,
            font=("Segoe UI", 10),
            anchor="center",
            justify="center",
        )
        loading_label.pack(fill="both", expand=True, padx=16, pady=24)
        try:
            win.update_idletasks()
        except Exception:
            pass

        try:
            data = load_observed_advantage_data(force=True)
            chars = list(data.get("chars") or [])
            active_keys = _adv_active_char_keys(scan_display, data)
            chars = _adv_order_chars_for_active_slots(chars, active_keys)
            default_key = _adv_default_char_key(scan_display, data)
            if default_key:
                _ADV_TK_SOURCE_KEY = str(default_key)
        except Exception as exc:
            print(f"[advantage] data load failed: {exc!r}", flush=True)
            try:
                import traceback
                traceback.print_exc()
            except Exception:
                pass
            loading_label.configure(
                text=f"Advantage data failed to load.\n\n{type(exc).__name__}: {exc}",
                fg="#ff8f8f",
            )
            try:
                win.lift()
                win.focus_force()
            except Exception:
                pass
            return

        if not chars:
            print("[advantage] no frame-data preview profiles found", flush=True)
            loading_label.configure(
                text="No observed frame-data profiles were found.\nThe Advantage Matrix window is ready and will populate when data becomes available.",
                fg=_TK_MUTED,
            )
            try:
                win.lift()
                win.focus_force()
            except Exception:
                pass
            return

        try:
            loading_label.destroy()
        except Exception:
            pass

        toolbar = tk.Frame(body, bg="#1f2430", highlightthickness=1, highlightbackground="#2c3345", padx=10, pady=8)
        toolbar.pack(fill="x", pady=(10, 10))

        by_key = data.get("by_key") or {}
        char_order = [str(char.get("key") or "") for char in chars if char.get("key")]
        source_options = []
        option_to_key = {}
        key_to_option = {}
        for char in chars:
            key = str(char.get("key") or "")
            if not key:
                continue
            label = _adv_source_label(char)
            source_options.append(label)
            option_to_key[label] = key
            key_to_option[key] = label

        source_var = tk.StringVar()
        source_key = _ADV_TK_SOURCE_KEY if _ADV_TK_SOURCE_KEY in by_key else (default_key or (char_order[0] if char_order else ""))
        if source_key in key_to_option:
            source_var.set(key_to_option[source_key])
            _ADV_TK_SOURCE_KEY = source_key

        tk.Label(toolbar, text="Source", bg="#1f2430", fg=_TK_MUTED, font=("Segoe UI", 9, "bold")).pack(side="left")
        source_box = ttk.Combobox(toolbar, values=source_options, textvariable=source_var, state="readonly", width=22)
        source_box.pack(side="left", padx=(8, 10))

        category_vars = {
            "normal": tk.BooleanVar(value=True),
            "special": tk.BooleanVar(value=True),
            "super": tk.BooleanVar(value=True),
        }
        for key, text in (("normal", "Normals"), ("special", "Specials"), ("super", "Supers")):
            cb = tk.Checkbutton(
                toolbar,
                text=text,
                variable=category_vars[key],
                bg="#1f2430",
                fg=_TK_TEXT,
                selectcolor="#11151e",
                activebackground="#1f2430",
                activeforeground=_TK_TEXT,
                font=("Segoe UI", 9),
            )
            cb.pack(side="left", padx=(0, 8))

        row_filter_var = tk.StringVar(value="All rows")
        tk.Label(toolbar, text="Rows", bg="#1f2430", fg=_TK_MUTED, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(6, 4))
        row_filter_box = ttk.Combobox(
            toolbar,
            values=("All rows", "Unsafe", "Punishable", "Big unsafe", "Plus or even"),
            textvariable=row_filter_var,
            state="readonly",
            width=13,
        )
        row_filter_box.pack(side="left", padx=(0, 8))

        column_mode_var = tk.StringVar(value="All cast")
        tk.Label(toolbar, text="Cols", bg="#1f2430", fg=_TK_MUTED, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(0, 4))
        column_mode_box = ttk.Combobox(
            toolbar,
            values=("All cast", "Live slots", "Capcom", "Tatsunoko"),
            textvariable=column_mode_var,
            state="readonly",
            width=10,
        )
        column_mode_box.pack(side="left", padx=(0, 8))

        status_var = tk.StringVar(value="")
        detail_var = tk.StringVar(value="Click a value cell for source punish details. Use row filters to narrow unsafe or punishable moves.")

        table_card = tk.Frame(body, bg="#1f2430", highlightthickness=1, highlightbackground="#2c3345")
        table_card.pack(fill="both", expand=True)
        table = tk.Frame(table_card, bg="#1f2430", padx=8, pady=8)
        table.pack(fill="both", expand=True)

        attack_header = tk.Frame(table, bg="#202638", highlightthickness=1, highlightbackground="#39445d")
        tk.Label(
            attack_header,
            text="Attack",
            bg="#202638",
            fg=_TK_MUTED,
            font=("Segoe UI", 9, "bold"),
            anchor="center",
        ).pack(fill="both", expand=True)

        matrix_cols = [str(char.get("key") or "") for char in chars if char.get("key")]
        chars_by_key = {str(char.get("key") or ""): char for char in chars if char.get("key")}
        visible_cols_ref: dict[str, list[str]] = {"keys": list(matrix_cols)}
        header_canvas = tk.Canvas(
            table,
            height=_ADV_HEADER_HEIGHT,
            bg="#202638",
            highlightthickness=1,
            highlightbackground="#39445d",
            bd=0,
        )
        attack_tree = ttk.Treeview(table, columns=("attack",), show="", style=style_name, selectmode="browse")
        matrix_tree = ttk.Treeview(table, columns=matrix_cols, show="", style=style_name, selectmode="browse")
        vsb = ttk.Scrollbar(table, orient="vertical")

        attack_tree.column("attack", width=_ADV_ATTACK_COL_WIDTH, minwidth=120, anchor="w", stretch=False)
        header_icons: dict[str, Any] = {}
        for char in chars:
            key = str(char.get("key") or "")
            if not key:
                continue
            icon = _adv_load_char_icon(win, char)
            if icon is not None:
                header_icons[key] = icon
            matrix_tree.column(key, width=_ADV_CHAR_COL_WIDTH, minwidth=96, anchor="center", stretch=False)

        def _draw_char_header() -> None:
            try:
                header_canvas.delete("all")
                visible_keys = list(visible_cols_ref.get("keys") or matrix_cols)
                total_width = max(1, len(visible_keys) * _ADV_CHAR_COL_WIDTH)
                header_canvas.configure(scrollregion=(0, 0, total_width, _ADV_HEADER_HEIGHT))
                x = 0
                for idx, key in enumerate(visible_keys):
                    char = chars_by_key.get(key) or {}
                    fill = "#222a3c" if idx % 2 == 0 else "#1d2434"
                    header_canvas.create_rectangle(x, 0, x + _ADV_CHAR_COL_WIDTH, _ADV_HEADER_HEIGHT, fill=fill, outline="#39445d")
                    icon = header_icons.get(key)
                    if icon is not None:
                        header_canvas.create_image(x + _ADV_CHAR_COL_WIDTH // 2, 17, image=icon, anchor="center")
                        text_y = 48
                    else:
                        text_y = 35
                    header_canvas.create_text(
                        x + _ADV_CHAR_COL_WIDTH // 2,
                        text_y,
                        text=_adv_char_header_text(char),
                        fill=_TK_TEXT,
                        font=("Segoe UI", 8, "bold"),
                        anchor="center",
                        justify="center",
                        width=_ADV_CHAR_COL_WIDTH - 8,
                    )
                    x += _ADV_CHAR_COL_WIDTH
            except Exception:
                pass

        def _xscroll_set(first: Any, last: Any) -> None:
            try:
                hsb.set(first, last)
            except Exception:
                pass
            try:
                header_canvas.xview_moveto(float(first))
            except Exception:
                pass

        def _xscroll_both(*args: Any) -> None:
            try:
                matrix_tree.xview(*args)
            except Exception:
                pass
            try:
                header_canvas.xview(*args)
            except Exception:
                pass

        hsb = ttk.Scrollbar(table, orient="horizontal", command=_xscroll_both)

        def _scroll_both(*args: Any) -> None:
            attack_tree.yview(*args)
            matrix_tree.yview(*args)

        def _set_y(*args: Any) -> None:
            try:
                vsb.set(*args)
            except Exception:
                pass

        attack_tree.configure(yscrollcommand=_set_y)
        matrix_tree.configure(yscrollcommand=_set_y, xscrollcommand=_xscroll_set)
        vsb.configure(command=_scroll_both)

        attack_header.grid(row=0, column=0, sticky="nsew")
        header_canvas.grid(row=0, column=1, sticky="ew")
        attack_tree.grid(row=1, column=0, sticky="nsw")
        matrix_tree.grid(row=1, column=1, sticky="nsew")
        vsb.grid(row=1, column=2, sticky="ns")
        hsb.grid(row=2, column=1, sticky="ew")
        table.grid_columnconfigure(1, weight=1)
        table.grid_rowconfigure(1, weight=1)
        _draw_char_header()

        try:
            attack_tree.tag_configure("normal", background="#121722")
            matrix_tree.tag_configure("normal", background="#121722")
            attack_tree.tag_configure("special", background="#151b29")
            matrix_tree.tag_configure("special", background="#151b29")
            attack_tree.tag_configure("super", background="#181d2d")
            matrix_tree.tag_configure("super", background="#181d2d")
            attack_tree.tag_configure("punish", background="#1b2439", foreground="#dfe6ff")
            matrix_tree.tag_configure("punish", background="#1b2439", foreground="#dfe6ff")
            attack_tree.tag_configure("punish_light", background="#1c2940", foreground="#e8edff")
            matrix_tree.tag_configure("punish_light", background="#1c2940", foreground="#e8edff")
            attack_tree.tag_configure("punish_medium", background="#1b263a", foreground="#e8edff")
            matrix_tree.tag_configure("punish_medium", background="#1b263a", foreground="#e8edff")
            attack_tree.tag_configure("punish_heavy", background="#192335", foreground="#e8edff")
            matrix_tree.tag_configure("punish_heavy", background="#192335", foreground="#e8edff")
        except Exception:
            pass

        detail = tk.Label(
            body,
            textvariable=detail_var,
            bg="#10141d",
            fg=_TK_TEXT,
            anchor="w",
            justify="left",
            font=("Segoe UI", 9),
            padx=8,
            pady=6,
            wraplength=1180,
        )
        detail.pack(fill="x", pady=(8, 0))

        status = tk.Label(
            body,
            textvariable=status_var,
            bg="#151821",
            fg=_TK_MUTED,
            anchor="w",
            justify="left",
            font=("Segoe UI", 9),
        )
        status.pack(fill="x", pady=(6, 0))

        def _selected_source_key() -> str | None:
            label = str(source_var.get() or "")
            key = option_to_key.get(label)
            if key in by_key:
                return key
            return None

        def _enabled_categories() -> set[str]:
            enabled = {key for key, var in category_vars.items() if bool(var.get())}
            return enabled or {"normal", "special", "super"}

        row_items_by_iid: dict[str, dict[str, Any]] = {}
        base_values_by_iid: dict[str, tuple[str, ...]] = {}
        selected_iid = {"value": None}

        def _current_visible_keys() -> list[str]:
            mode = str(column_mode_var.get() or "All cast").strip().lower()
            if mode == "live slots":
                keys = [key for key in active_keys if key in chars_by_key]
                return keys or list(matrix_cols)
            if mode == "capcom":
                return [key for key in matrix_cols if _adv_char_side(chars_by_key.get(key) or {}) == "capcom"]
            if mode == "tatsunoko":
                return [key for key in matrix_cols if _adv_char_side(chars_by_key.get(key) or {}) == "tatsunoko"]
            return list(matrix_cols)

        def _apply_column_mode() -> None:
            visible_keys = _current_visible_keys()
            visible_cols_ref["keys"] = visible_keys
            try:
                matrix_tree.configure(displaycolumns=tuple(visible_keys))
            except Exception:
                pass
            _draw_char_header()

        def _reset_headings() -> None:
            _apply_column_mode()

        def _base_iid_from_any_iid(iid: str | None) -> str | None:
            text = str(iid or "")
            match = re.search(r"row_\d+", text)
            return match.group(0) if match else None

        def _remove_punish_rows() -> None:
            for tree in (attack_tree, matrix_tree):
                try:
                    for row_iid in list(tree.get_children()):
                        if str(row_iid).startswith("punish_") or str(row_iid).startswith("counter_"):
                            tree.delete(row_iid)
                except Exception:
                    pass

        def _apply_punish_view(iid: str | None) -> None:
            _reset_headings()
            _remove_punish_rows()
            base_iid = _base_iid_from_any_iid(iid)
            if not base_iid:
                return
            selected_item = row_items_by_iid.get(base_iid)
            if not isinstance(selected_item, dict):
                return
            source_key = _selected_source_key()
            source_char = by_key.get(source_key or "")
            source_name = str((source_char or {}).get("name") or "Unknown")
            attack_label = str(selected_item.get("label") or selected_item.get("key") or "?")
            selected_adv = _adv_int_value(selected_item.get("adv"))
            enabled = _enabled_categories()
            visible_keys = list(visible_cols_ref.get("keys") or matrix_cols)
            unique_attack = _adv_uses_unique_attack_window(selected_item)
            row_has_window = False
            for col_key in visible_keys:
                char = chars_by_key.get(col_key) or {}
                match = (char.get("attacks_by_key") or {}).get(str(selected_item.get("key") or ""))
                if _adv_row_cell_window(selected_item, match if isinstance(match, dict) else None) is not None:
                    row_has_window = True
                    break
            if not row_has_window:
                status_var.set(f"{source_name} {attack_label}: no negative visible values, no punish rows added.")
                return
            strength_rows: list[tuple[str, str, list[str], int]] = []
            punishers: list[str] = []
            for strength, short in _ADV_STRENGTH_ORDER:
                values: list[str] = []
                count = 0
                for col_key in matrix_cols:
                    char = chars_by_key.get(col_key) or {}
                    match = (char.get("attacks_by_key") or {}).get(str(selected_item.get("key") or ""))
                    match_item = match if isinstance(match, dict) else None
                    cell_window = _adv_row_cell_window(selected_item, match_item)
                    label = ""
                    punisher_char = char if unique_attack else source_char
                    if cell_window is not None and isinstance(punisher_char, dict):
                        segment = _adv_row_punish_segment(selected_item, match_item)
                        grouped = _adv_punish_strength_candidates(punisher_char, cell_window, enabled, segment=segment)
                        item = grouped.get(strength) if isinstance(grouped, dict) else None
                        label = _adv_move_startup_label(item) if item else ""
                    values.append(label)
                    if label:
                        count += 1
                        if len(punishers) < 8 and col_key in visible_keys:
                            name = str(char.get("name") or char.get("key") or "Unknown")
                            if unique_attack:
                                punishers.append(f"{name} {short}: {label}")
                            else:
                                punishers.append(f"vs {name} {short}: {label}")
                if count > 0:
                    strength_rows.append((strength, short, values, count))
            if not strength_rows:
                adv_text = _adv_format(selected_adv) if selected_adv is not None else "?"
                status_var.set(f"{source_name} {attack_label}: {adv_text} on block. No startup-backed L/M/H punish found for the visible row values.")
                return
            try:
                attack_index = int(attack_tree.index(base_iid)) + 1
            except Exception:
                attack_index = "end"
            try:
                matrix_index = int(matrix_tree.index(base_iid)) + 1
            except Exception:
                matrix_index = "end"
            for offset, (strength, short, values, _count) in enumerate(strength_rows):
                punish_iid = f"punish_{strength}_{base_iid}"
                punish_label = f"Cast punish {short}" if unique_attack else f"{source_name} punish {short}"
                tag = f"punish_{strength}"
                try:
                    attack_tree.insert("", attack_index + offset if isinstance(attack_index, int) else "end", iid=punish_iid, values=(punish_label,), tags=("punish", tag))
                    matrix_tree.insert("", matrix_index + offset if isinstance(matrix_index, int) else "end", iid=punish_iid, values=tuple(values), tags=("punish", tag))
                except Exception:
                    pass
            preview = "; ".join(punishers)
            if not preview:
                preview = "no punishers listed"
            adv_text = _adv_format(selected_adv) if selected_adv is not None else "?"
            if unique_attack:
                status_var.set(f"{source_name} {attack_label}: added cast L, M, and H punish rows from this move's block advantage. Air specials and air supers use air punish candidates. Startup only, range and pushback are not proven. {preview}")
            else:
                status_var.set(f"{source_name} {attack_label}: added source L, M, and H punish rows using each cell value. Startup only, range and pushback are not proven. {preview}")

        def _rebuild() -> None:
            global _ADV_TK_SOURCE_KEY
            source_key = _selected_source_key()
            if not source_key:
                return
            _ADV_TK_SOURCE_KEY = source_key
            source_char = by_key.get(source_key)
            rows = _adv_matrix_rows(source_char, _enabled_categories())
            row_items_by_iid.clear()
            base_values_by_iid.clear()
            selected_iid["value"] = None
            _reset_headings()
            for tree in (attack_tree, matrix_tree):
                try:
                    tree.delete(*tree.get_children())
                except Exception:
                    pass
            visible_keys = list(visible_cols_ref.get("keys") or matrix_cols)
            visible_set = set(visible_keys)
            inserted = 0
            for idx, item in enumerate(rows):
                attack_key = str(item.get("key") or "")
                category = str(item.get("category") or "normal")
                label = str(item.get("label") or attack_key)
                values = []
                raw_visible_values: list[Any] = []
                for col_key in matrix_cols:
                    char = chars_by_key.get(col_key) or {}
                    cell = ""
                    match = (char.get("attacks_by_key") or {}).get(attack_key)
                    if isinstance(match, dict):
                        raw_adv = match.get("adv")
                        cell = _adv_format_cell(raw_adv, show_badge=True)
                        if col_key in visible_set:
                            raw_visible_values.append(raw_adv)
                    values.append(cell)
                if not _adv_row_filter_match(raw_visible_values, row_filter_var.get()):
                    continue
                iid = f"row_{idx}"
                row_items_by_iid[iid] = item
                attack_tree.insert("", "end", iid=iid, values=(label,), tags=(category,))
                base_values_by_iid[iid] = tuple(values)
                matrix_tree.insert("", "end", iid=iid, values=values, tags=(category,))
                inserted += 1
            source_name = str((source_char or {}).get("name") or "Unknown")
            active_names = []
            for key in active_keys:
                active_char = by_key.get(key)
                if isinstance(active_char, dict):
                    active_names.append(str(active_char.get("name") or key))
            active_note = f" Active slots first: {', '.join(active_names)}." if active_names else ""
            visible_count = len(visible_cols_ref.get("keys") or matrix_cols)
            status_var.set(f"{source_name}: {inserted}/{len(rows)} row(s), {visible_count}/{len(chars)} visible column(s).{active_note} Click a row for source L/M/H punish rows or click a value cell for exact cell details.")

        def _use_live_default() -> None:
            global _ADV_TK_SOURCE_KEY
            key = _adv_default_char_key(scan_display, data)
            if key and key in key_to_option:
                source_var.set(key_to_option[key])
                _ADV_TK_SOURCE_KEY = key
                picked = by_key.get(key) or {}
                _rebuild()
                status_var.set(f"Use Live Source selected {picked.get('name') or key} from the live slots.")
            else:
                _rebuild()
                status_var.set("Use Live Source could not resolve a live slot, so the current source stayed selected.")

        def _step_source(delta: int) -> None:
            cur = _selected_source_key()
            if not char_order:
                return
            try:
                idx = char_order.index(cur or "")
            except Exception:
                idx = 0
            nxt = char_order[(idx + delta) % len(char_order)]
            if nxt in key_to_option:
                source_var.set(key_to_option[nxt])
            _rebuild()

        try:
            win.bind("<Control-Left>", lambda _event: (_step_source(-1), "break")[-1])
            win.bind("<Control-Right>", lambda _event: (_step_source(1), "break")[-1])
        except Exception:
            pass

        def _show_cell_detail(row_iid: str | None, col_key: str | None) -> None:
            base_iid = _base_iid_from_any_iid(row_iid)
            if not base_iid or not col_key:
                return
            selected_item = row_items_by_iid.get(base_iid)
            if not isinstance(selected_item, dict):
                return
            char = chars_by_key.get(str(col_key or ""))
            if not isinstance(char, dict):
                return
            attack_key = str(selected_item.get("key") or "")
            match = (char.get("attacks_by_key") or {}).get(attack_key)
            match_item = match if isinstance(match, dict) else None
            unique_attack = _adv_uses_unique_attack_window(selected_item)
            if not isinstance(match_item, dict) and not unique_attack:
                detail_var.set(f"{_adv_char_short_name(char)} has no value for {selected_item.get('label') or attack_key}.")
                return
            source_key = _selected_source_key()
            source_char = by_key.get(source_key or "")
            source_name = str((source_char or {}).get("name") or "Unknown")
            char_name = str(char.get("name") or char.get("key") or "Unknown")
            label = str((match_item or selected_item).get("label") or selected_item.get("label") or attack_key)
            adv = (match_item or selected_item).get("adv")
            adv_text = _adv_format(adv)
            danger = _adv_danger_label(adv)
            window = _adv_row_cell_window(selected_item, match_item)
            if window is None:
                if unique_attack:
                    detail_var.set(f"{source_name} {label}: {adv_text} on block. No punish window for {char_name}.")
                else:
                    detail_var.set(f"{char_name} {label}: {adv_text} on block, {danger}. No punish window for {source_name}.")
                return
            if unique_attack:
                segment = _adv_row_punish_segment(selected_item, match_item)
                grouped = _adv_punish_strength_candidates(char, window, _enabled_categories(), segment=segment)
                punish_text = _adv_strength_labels(grouped) or "none found"
                seg_text = "air" if segment == "air" else "ground"
                detail_var.set(f"{source_name} {label}: {adv_text} on block, {danger}. {char_name} {seg_text} punishes up to {window}f: {punish_text}. Startup only, range and pushback not proven.")
                return
            if not isinstance(source_char, dict):
                detail_var.set(f"{char_name} {label}: {adv_text} on block, {danger}. No punish window for {source_name}.")
                return
            segment = _adv_row_punish_segment(selected_item, match_item)
            grouped = _adv_punish_strength_candidates(source_char, window, _enabled_categories(), segment=segment)
            punish_text = _adv_strength_labels(grouped) or "none found"
            seg_text = "air" if segment == "air" else "ground"
            detail_var.set(f"{char_name} {label}: {adv_text} on block, {danger}. {source_name} {seg_text} punishes up to {window}f: {punish_text}. Startup only, range and pushback not proven.")

        def _matrix_cell_click(event: Any) -> None:
            try:
                row_iid = matrix_tree.identify_row(event.y)
                col_text = str(matrix_tree.identify_column(event.x) or "")
                col_index = int(col_text.replace("#", "")) - 1
            except Exception:
                return
            visible_keys = list(visible_cols_ref.get("keys") or matrix_cols)
            if col_index < 0 or col_index >= len(visible_keys):
                return
            _show_cell_detail(str(row_iid or ""), visible_keys[col_index])

        selection_syncing = {"busy": False}

        def _sync_selection(event: Any = None) -> None:
            if selection_syncing.get("busy"):
                return
            widget = getattr(event, "widget", None)
            if widget is None:
                return
            try:
                sel = widget.selection()
            except Exception:
                sel = ()
            if not sel:
                return
            raw_iid = str(sel[0])
            iid = _base_iid_from_any_iid(raw_iid) or raw_iid
            selected_iid["value"] = iid
            selection_syncing["busy"] = True
            try:
                for tree in (attack_tree, matrix_tree):
                    try:
                        current = tree.selection()
                    except Exception:
                        current = ()
                    if tuple(current) != (iid,):
                        tree.selection_set(iid)
                    tree.see(iid)
                _apply_punish_view(iid)
            except Exception as exc:
                try:
                    status_var.set(f"Punish view error: {exc}")
                except Exception:
                    pass
            finally:
                selection_syncing["busy"] = False

        def _wheel(event: Any) -> str:
            delta = -1 if getattr(event, "delta", 0) > 0 else 1
            try:
                attack_tree.yview_scroll(delta, "units")
                matrix_tree.yview_scroll(delta, "units")
            except Exception:
                pass
            return "break"

        attack_tree.bind("<<TreeviewSelect>>", _sync_selection)
        matrix_tree.bind("<<TreeviewSelect>>", _sync_selection)
        attack_tree.bind("<MouseWheel>", _wheel)
        matrix_tree.bind("<MouseWheel>", _wheel)
        source_box.bind("<<ComboboxSelected>>", lambda _event: _rebuild())
        row_filter_box.bind("<<ComboboxSelected>>", lambda _event: _rebuild())
        column_mode_box.bind("<<ComboboxSelected>>", lambda _event: (_apply_column_mode(), _rebuild()))
        matrix_tree.bind("<ButtonRelease-1>", _matrix_cell_click, add="+")
        for var in category_vars.values():
            try:
                var.trace_add("write", lambda *_args: _rebuild())
            except Exception:
                pass

        actions = tk.Frame(toolbar, bg="#1f2430")
        actions.pack(side="right")
        for text, command in (
            ("<", lambda: _step_source(-1)),
            (">", lambda: _step_source(1)),
            ("Use Live Source", _use_live_default),
            ("Refresh", _rebuild),
        ):
            tk.Button(
                actions,
                text=text,
                command=command,
                bg="#2b3142",
                fg=_TK_TEXT,
                activebackground="#394058",
                activeforeground=_TK_TEXT,
                relief="flat",
                padx=8,
                pady=4,
                font=("Segoe UI", 9, "bold"),
            ).pack(side="left", padx=(4, 0))

        _rebuild()
        try:
            win.lift()
            win.focus_force()
        except Exception:
            pass

    tk_call(_show)

def _draw_adv_table_header(
    surf: pygame.Surface,
    rect: pygame.Rect,
    smallfont: pygame.font.Font,
    labels: tuple[str, ...],
    widths: tuple[int, ...],
) -> None:
    x = rect.x
    for i, (label, width) in enumerate(zip(labels, widths)):
        cell = pygame.Rect(x, rect.y, width, rect.height)
        pygame.draw.rect(surf, (28, 34, 48) if i % 2 == 0 else (22, 27, 39), cell)
        pygame.draw.rect(surf, (56, 67, 92), cell, 1)
        label_s = smallfont.render(label, True, GUI_TEXT_DIM)
        surf.blit(label_s, (cell.x + (cell.width - label_s.get_width()) // 2, cell.y + (cell.height - label_s.get_height()) // 2))
        x += width


def _draw_adv_value(
    surf: pygame.Surface,
    rect: pygame.Rect,
    smallfont: pygame.font.Font,
    value: Any,
    *,
    selected: bool = False,
) -> None:
    col = _adv_color(value)
    if selected:
        col = _brighten(col, 28)
    value_s = _render_outlined_text(smallfont, _adv_format(value), col, (0, 0, 0), rect.width - 6, outline_px=1)
    surf.blit(value_s, (rect.x + (rect.width - value_s.get_width()) // 2, rect.y + (rect.height - value_s.get_height()) // 2))


def draw_advantage_window(
    surf: pygame.Surface,
    rect: pygame.Rect,
    font: pygame.font.Font,
    smallfont: pygame.font.Font,
    scan_data: Any,
    *,
    selection: dict[str, Any] | None = None,
    mouse_pos: tuple[int, int] | None = None,
    t_ms: int = 0,
) -> dict[str, Any]:
    """Draw the observed block advantage workspace and return click targets."""
    interaction: dict[str, Any] = {"controls": {}, "rows": [], "char_order": [], "current_char_key": None}
    if rect.width <= 0 or rect.height <= 0:
        return interaction

    mouse_pos = mouse_pos or (-10000, -10000)
    data = load_observed_advantage_data()
    chars = list(data.get("chars") or [])
    active_keys = _adv_active_char_keys(scan_data, data)
    chars = _adv_order_chars_for_active_slots(chars, active_keys)
    char_order = [str(char.get("key") or "") for char in chars if char.get("key")]
    interaction["char_order"] = char_order

    panel = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
    _draw_vertical_gradient(panel, panel.get_rect(), (14, 17, 26), (10, 12, 18), 255)
    surf.blit(panel, rect.topleft)

    current_key = _adv_current_char_key(scan_data, data, selection)
    by_key = data.get("by_key") or {}
    current_char = by_key.get(current_key) if current_key else None
    if current_char is None and chars:
        current_char = chars[0]
        current_key = str(current_char.get("key") or "")
    interaction["current_char_key"] = current_key

    selected_attack_key = _adv_selected_attack(current_char, selection)
    source_name = str(current_char.get("name") or "No character") if isinstance(current_char, dict) else "No character"
    title = smallfont.render("Advantage", True, GUI_TEXT)
    subtitle = smallfont.render("Observed block advantage only. Click an attack to compare it across the cast.", True, GUI_TEXT_DIM)
    surf.blit(title, (rect.x + 10, rect.y + 7))
    surf.blit(subtitle, (rect.right - subtitle.get_width() - 10, rect.y + 7))
    pygame.draw.line(surf, (52, 61, 82), (rect.x + 8, rect.y + 24), (rect.right - 8, rect.y + 24))

    toolbar_y = rect.y + 29
    control_h = 18
    source_s = smallfont.render("Source", True, GUI_TEXT_DIM)
    surf.blit(source_s, (rect.x + 10, toolbar_y + (control_h - source_s.get_height()) // 2))
    x = rect.x + 10 + source_s.get_width() + 8

    prev_rect = pygame.Rect(x, toolbar_y, 22, control_h)
    draw_glass_button(surf, prev_rect, "<", smallfont, hover=prev_rect.collidepoint(mouse_pos), accent=GUI_APP_ACCENT, fill=(24, 29, 41))
    interaction["controls"]["__char_prev__"] = prev_rect.move(-rect.x, -rect.y)
    x = prev_rect.right + 4

    name_w = min(230, max(110, rect.width // 4))
    name_rect = pygame.Rect(x, toolbar_y, name_w, control_h)
    _draw_vertical_gradient(surf, name_rect, (25, 31, 45), (18, 22, 32), 235)
    pygame.draw.rect(surf, (54, 66, 92), name_rect, 1, border_radius=4)
    name_s = _fit_text(smallfont, source_name, GUI_TEXT, name_rect.width - 10)
    surf.blit(name_s, (name_rect.x + 5, name_rect.y + (name_rect.height - name_s.get_height()) // 2))
    x = name_rect.right + 4

    next_rect = pygame.Rect(x, toolbar_y, 22, control_h)
    draw_glass_button(surf, next_rect, ">", smallfont, hover=next_rect.collidepoint(mouse_pos), accent=GUI_APP_ACCENT, fill=(24, 29, 41))
    interaction["controls"]["__char_next__"] = next_rect.move(-rect.x, -rect.y)
    x = next_rect.right + 8

    live_rect = pygame.Rect(x, toolbar_y, max(72, smallfont.size("Use Live Source")[0] + 14), control_h)
    live_locked = isinstance(selection, dict) and bool(selection.get("lock_char"))
    draw_glass_button(
        surf,
        live_rect,
        "Use Live Source",
        smallfont,
        active=not live_locked,
        hover=live_rect.collidepoint(mouse_pos),
        accent=GUI_CONFIRM,
        fill=(24, 29, 41),
    )
    interaction["controls"]["__live_default__"] = live_rect.move(-rect.x, -rect.y)

    info = f"Observed chars: {len(data.get('observed_chars') or [])}/{len(chars)}"
    info_s = _fit_text(smallfont, info, GUI_TEXT_MUTED, max(0, rect.right - live_rect.right - 16))
    surf.blit(info_s, (live_rect.right + 8, toolbar_y + (control_h - info_s.get_height()) // 2))

    content_top = toolbar_y + control_h + 7
    pygame.draw.line(surf, (52, 61, 82), (rect.x + 8, content_top - 3), (rect.right - 8, content_top - 3))

    pad = 8
    gap = 10
    left_w = max(230, min(340, int(rect.width * 0.38)))
    left = pygame.Rect(rect.x + pad, content_top, left_w, rect.bottom - content_top - 8)
    right = pygame.Rect(left.right + gap, content_top, rect.right - left.right - gap - pad, left.height)

    for box, box_title in ((left, f"{source_name} observed"), (right, "Cast comparison")):
        _draw_vertical_gradient(surf, box, (18, 22, 32), (12, 14, 21), 238)
        pygame.draw.rect(surf, (43, 52, 72), box, 1, border_radius=6)
        pygame.draw.rect(surf, GUI_APP_ACCENT, pygame.Rect(box.x, box.y, 3, box.height), border_radius=2)
        title_s = _fit_text(smallfont, box_title, GUI_TEXT, box.width - 14)
        surf.blit(title_s, (box.x + 9, box.y + 5))
        pygame.draw.line(surf, (44, 52, 72), (box.x + 7, box.y + 23), (box.right - 7, box.y + 23))

    row_h = 18
    header_h = 16
    table_y = left.y + 27
    attack_col_w = max(120, left.width - 76)
    adv_col_w = left.width - 12 - attack_col_w
    header = pygame.Rect(left.x + 6, table_y, left.width - 12, header_h)
    _draw_adv_table_header(surf, header, smallfont, ("Attack", "Obs BA"), (attack_col_w, adv_col_w))

    attacks = current_char.get("attacks") if isinstance(current_char, dict) else []
    if not isinstance(attacks, list):
        attacks = []
    max_left_rows = max(0, (left.bottom - (table_y + header_h + 3) - 4) // row_h)
    y = table_y + header_h + 3
    if not attacks:
        msg = "No observed block adv found for this source."
        msg_s = _fit_text(smallfont, msg, GUI_TEXT_MUTED, left.width - 20)
        surf.blit(msg_s, (left.x + 10, y + 8))
    for item in attacks[:max_left_rows]:
        if not isinstance(item, dict):
            continue
        attack_key = str(item.get("key") or "")
        selected = attack_key == selected_attack_key
        row = pygame.Rect(left.x + 6, y, left.width - 12, row_h)
        fill = (31, 38, 55) if selected else ((17, 21, 31) if ((y // row_h) % 2 == 0) else (14, 17, 26))
        pygame.draw.rect(surf, fill, row)
        pygame.draw.rect(surf, (35, 43, 61), row, 1)
        if selected:
            pygame.draw.rect(surf, (*GUI_APP_ACCENT, 170), pygame.Rect(row.x + 1, row.y + 1, 3, row.height - 2), border_radius=1)
        interaction["rows"].append({
            "rect": row.move(-rect.x, -rect.y),
            "type": "source_attack",
            "attack_key": attack_key,
            "source_char_key": current_key,
        })
        label_s = _fit_text(smallfont, str(item.get("label") or attack_key), GUI_TEXT, attack_col_w - 8)
        surf.blit(label_s, (row.x + 6, row.y + (row.height - label_s.get_height()) // 2))
        _draw_adv_value(surf, pygame.Rect(row.x + attack_col_w, row.y, adv_col_w, row.height), smallfont, item.get("adv"), selected=selected)
        y += row_h

    if len(attacks) > max_left_rows:
        more = f"+{len(attacks) - max_left_rows} more"
        more_s = smallfont.render(more, True, GUI_TEXT_DIM)
        surf.blit(more_s, (left.right - more_s.get_width() - 8, left.bottom - more_s.get_height() - 5))

    right_title = "Pick an attack" if not selected_attack_key else f"Attack {selected_attack_key} across cast"
    right_hint = _fit_text(smallfont, right_title, GUI_TEXT_MUTED, right.width - 18)
    surf.blit(right_hint, (right.x + 9, right.y + 25))

    cast_y = right.y + 43
    cast_h = right.bottom - cast_y - 6
    if cast_h <= 20 or right.width <= 120:
        return interaction

    cols = 2 if right.width >= 430 else 1
    col_gap = 8
    col_w = (right.width - 12 - col_gap * (cols - 1)) // cols
    rows_per_col = max(1, (cast_h - header_h - 3) // row_h)
    display_chars = chars[: rows_per_col * cols]

    for col in range(cols):
        col_x = right.x + 6 + col * (col_w + col_gap)
        hdr = pygame.Rect(col_x, cast_y, col_w, header_h)
        name_w = max(80, col_w - 58)
        val_w = col_w - name_w
        _draw_adv_table_header(surf, hdr, smallfont, ("Character", "Obs BA"), (name_w, val_w))
        y = cast_y + header_h + 3
        for idx in range(rows_per_col):
            char_i = col * rows_per_col + idx
            if char_i >= len(display_chars):
                break
            char = display_chars[char_i]
            row = pygame.Rect(col_x, y, col_w, row_h)
            same_source = str(char.get("key") or "") == str(current_key or "")
            fill = (26, 33, 49) if same_source else ((17, 21, 31) if idx % 2 == 0 else (14, 17, 26))
            pygame.draw.rect(surf, fill, row)
            pygame.draw.rect(surf, (35, 43, 61), row, 1)
            if same_source:
                pygame.draw.rect(surf, (*GUI_APP_ACCENT, 155), pygame.Rect(row.x + 1, row.y + 1, 3, row.height - 2), border_radius=1)
            name = str(char.get("name") or char.get("key") or "Unknown")
            name_s = _fit_text(smallfont, name, GUI_TEXT if same_source else GUI_TEXT_MUTED, name_w - 8)
            surf.blit(name_s, (row.x + 6, row.y + (row.height - name_s.get_height()) // 2))
            adv = None
            if selected_attack_key:
                item = (char.get("attacks_by_key") or {}).get(selected_attack_key)
                if isinstance(item, dict):
                    adv = item.get("adv")
            _draw_adv_value(surf, pygame.Rect(row.x + name_w, row.y, val_w, row.height), smallfont, adv, selected=same_source)
            y += row_h

    if len(chars) > len(display_chars):
        more = f"+{len(chars) - len(display_chars)} cast rows off-screen"
        more_s = smallfont.render(more, True, GUI_TEXT_DIM)
        surf.blit(more_s, (right.right - more_s.get_width() - 8, right.bottom - more_s.get_height() - 5))

    return interaction


__all__ = ["draw_advantage_window", "load_observed_advantage_data", "open_advantage_window"]
