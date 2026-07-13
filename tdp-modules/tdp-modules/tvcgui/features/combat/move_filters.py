"""Shared move visibility filters for known character notation exceptions."""
from __future__ import annotations

import re
from typing import Any, Iterable

NO_6B_CHARACTER_IDS = {3, 6, 10, 16, 20, 26, 27, 29, 30}

_NO_6B_NAME_KEYS = {
    "tekkaman",
    "doronjo",
    "karas",
    "alex",
    "saki",
    "tekkamanblade",
    "joethecondor",
    "condor",
    "zero",
    "frankwest",
    "frank",
}

_NO_6B_PROFILE_KEYS = {
    "id_03_tekkaman",
    "id_06_doronjo",
    "id_10_karas",
    "id_16_alex",
    "id_20_saki",
    "id_26_tekkaman_blade",
    "id_27_joe_the_condor",
    "id_29_zero",
    "id_30_frank_west",
}

_MOVE_LABEL_KEYS = (
    "_normal_display_label",
    "label",
    "move_name",
    "move_label",
    "wiki_label",
    "move",
    "pretty_name",
    "name",
)

_CHAR_ID_KEYS = ("char_id", "character_id", "fighter_id")
_CHAR_NAME_KEYS = ("char_name", "character", "name", "display_name")
_CHAR_KEY_KEYS = ("profile_key", "profile", "char_key", "key")


def _compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def _profile_key_text(value: Any) -> str:
    return str(value or "").strip().lower()


def _move_label_text(move: dict[str, Any] | None, explicit_label: Any = None) -> str:
    if explicit_label is not None and str(explicit_label).strip():
        return str(explicit_label).strip()
    if isinstance(move, dict):
        for key in _MOVE_LABEL_KEYS:
            value = move.get(key)
            if value is not None and str(value).strip():
                return str(value).strip()
    return ""


def canonical_move_label(move: dict[str, Any] | None = None, explicit_label: Any = None) -> str:
    text = _move_label_text(move, explicit_label)
    low = text.strip().lower().replace(" ", "").replace("_", "")
    low = low.replace("jump.", "j.").replace("jump", "j.")
    low = low.replace("air.", "j.").replace("air", "j.")
    aliases = {
        "6b": "6B",
        "forwardb": "6B",
        "fwd+b": "6B",
        "fwdb": "6B",
    }
    return aliases.get(low, text.strip())


def _char_ids_from_ref(char_ref: Any, move: dict[str, Any] | None = None) -> list[int]:
    ids: list[int] = []
    for obj in (char_ref, move):
        if not isinstance(obj, dict):
            continue
        for key in _CHAR_ID_KEYS:
            try:
                cid = int(obj.get(key))
            except Exception:
                continue
            ids.append(cid)
    return ids


def _char_keys_from_ref(char_ref: Any, move: dict[str, Any] | None = None) -> list[str]:
    keys: list[str] = []
    for obj in (char_ref, move):
        if not isinstance(obj, dict):
            continue
        for key in _CHAR_KEY_KEYS:
            value = _profile_key_text(obj.get(key))
            if value:
                keys.append(value)
    return keys


def _char_names_from_ref(char_ref: Any, move: dict[str, Any] | None = None) -> list[str]:
    names: list[str] = []
    for obj in (char_ref, move):
        if not isinstance(obj, dict):
            continue
        for key in _CHAR_NAME_KEYS:
            value = obj.get(key)
            if value is not None and str(value).strip():
                names.append(str(value).strip())
    return names


def character_has_no_6b(char_ref: Any, move: dict[str, Any] | None = None) -> bool:
    for cid in _char_ids_from_ref(char_ref, move):
        if cid in NO_6B_CHARACTER_IDS:
            return True
    for key in _char_keys_from_ref(char_ref, move):
        if key in _NO_6B_PROFILE_KEYS:
            return True
        match = re.search(r"id_(\d+)_", key)
        if match:
            try:
                if int(match.group(1)) in NO_6B_CHARACTER_IDS:
                    return True
            except Exception:
                pass
    for name in _char_names_from_ref(char_ref, move):
        if _compact(name) in _NO_6B_NAME_KEYS:
            return True
    return False


def is_purged_move_label(char_ref: Any, move: dict[str, Any] | None = None, explicit_label: Any = None) -> bool:
    return canonical_move_label(move, explicit_label) == "6B" and character_has_no_6b(char_ref, move)


def filter_purged_moves_for_char(char_ref: Any, moves: Iterable[Any] | None) -> list[Any]:
    out: list[Any] = []
    for move in list(moves or []):
        if isinstance(move, dict) and is_purged_move_label(char_ref, move):
            continue
        out.append(move)
    return out
