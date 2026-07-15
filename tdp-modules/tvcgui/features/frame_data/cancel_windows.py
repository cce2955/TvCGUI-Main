from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Iterable

from tvcgui.core.paths import user_data_path

PROFILE_PATH = user_data_path("frame_data", "custom_cancel_windows.json")
_PROFILE_VERSION = 1
_CACHE: dict[str, Any] | None = None
_CACHE_MTIME: float | None = None


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def normalize_character_key(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "unknown"


def normalize_window(earliest: Any, latest: Any = 0) -> tuple[int, int]:
    start = max(0, _as_int(earliest, 0))
    end = max(0, _as_int(latest, 0))
    if end and end < start:
        start, end = end, start
    return start, end


def parse_window_text(value: Any) -> tuple[int, int] | None:
    text = str(value or "").strip().lower()
    if not text or text in {"clear", "none", "off", "remove", "delete", "-"}:
        return None
    text = text.replace("frames", "").replace("frame", "").replace(" ", "")
    if text.endswith("+"):
        start = int(text[:-1], 0)
        return normalize_window(start, 0)
    match = re.fullmatch(r"(0x[0-9a-f]+|\d+)[-:](0x[0-9a-f]+|\d+)", text)
    if match:
        return normalize_window(int(match.group(1), 0), int(match.group(2), 0))
    if re.fullmatch(r"0x[0-9a-f]+|\d+", text):
        return normalize_window(int(text, 0), 0)
    raise ValueError("Use 8+, 8-20, a single start frame, or clear.")


def format_window(window: dict[str, Any] | tuple[int, int] | None) -> str:
    if not window:
        return ""
    if isinstance(window, tuple):
        start, end = normalize_window(window[0], window[1])
    else:
        start, end = normalize_window(window.get("earliest", 0), window.get("latest", 0))
    if start <= 0 and end <= 0:
        return ""
    return f"{start}-{end}" if end else f"{start}+"


def _empty_profile() -> dict[str, Any]:
    return {"version": _PROFILE_VERSION, "characters": {}}


def _load_uncached() -> dict[str, Any]:
    try:
        with open(PROFILE_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            return _empty_profile()
    except Exception:
        return _empty_profile()
    data.setdefault("version", _PROFILE_VERSION)
    data.setdefault("characters", {})
    if not isinstance(data.get("characters"), dict):
        data["characters"] = {}
    return data


def load_profile(force: bool = False) -> dict[str, Any]:
    global _CACHE, _CACHE_MTIME
    try:
        mtime = os.path.getmtime(PROFILE_PATH)
    except Exception:
        mtime = None
    if force or _CACHE is None or mtime != _CACHE_MTIME:
        _CACHE = _load_uncached()
        _CACHE_MTIME = mtime
    return _CACHE


def save_profile(profile: dict[str, Any]) -> bool:
    global _CACHE, _CACHE_MTIME
    try:
        os.makedirs(os.path.dirname(PROFILE_PATH), exist_ok=True)
        with open(PROFILE_PATH, "w", encoding="utf-8") as handle:
            json.dump(profile, handle, indent=2, sort_keys=True)
        _CACHE = profile
        try:
            _CACHE_MTIME = os.path.getmtime(PROFILE_PATH)
        except Exception:
            _CACHE_MTIME = None
        return True
    except Exception:
        return False


def get_window(char_name: Any, action_id: Any) -> dict[str, Any] | None:
    aid = _as_int(action_id, -1)
    if aid < 0:
        return None
    profile = load_profile()
    char_key = normalize_character_key(char_name)
    entry = (((profile.get("characters") or {}).get(char_key) or {}).get("moves") or {}).get(f"0x{aid:04X}")
    if not isinstance(entry, dict):
        return None
    start, end = normalize_window(entry.get("earliest", 0), entry.get("latest", 0))
    if start <= 0 and end <= 0:
        return None
    out = dict(entry)
    out["earliest"] = start
    out["latest"] = end
    return out


def set_window(
    char_name: Any,
    action_id: Any,
    earliest: Any,
    latest: Any = 0,
    *,
    source: str = "manual",
    tested_target_id: Any | None = None,
) -> dict[str, Any] | None:
    aid = _as_int(action_id, -1)
    if aid < 0:
        return None
    start, end = normalize_window(earliest, latest)
    if start <= 0 and end <= 0:
        clear_window(char_name, aid)
        return None
    profile = load_profile(force=True)
    characters = profile.setdefault("characters", {})
    char_key = normalize_character_key(char_name)
    char_entry = characters.setdefault(char_key, {"name": str(char_name or "Unknown"), "moves": {}})
    char_entry["name"] = str(char_name or char_entry.get("name") or "Unknown")
    moves = char_entry.setdefault("moves", {})
    key = f"0x{aid:04X}"
    prior = moves.get(key) if isinstance(moves.get(key), dict) else {}
    tested = []
    for raw in prior.get("tested_targets", []) if isinstance(prior, dict) else []:
        value = _as_int(raw, -1)
        if value >= 0 and value not in tested:
            tested.append(value)
    target_id = _as_int(tested_target_id, -1) if tested_target_id is not None else -1
    if target_id >= 0 and target_id not in tested:
        tested.append(target_id)
    entry = {
        "earliest": start,
        "latest": end,
        "source": str(source or "manual"),
        "updated_at": int(time.time()),
        "tested_targets": tested,
    }
    moves[key] = entry
    return entry if save_profile(profile) else None


def clear_window(char_name: Any, action_id: Any) -> bool:
    aid = _as_int(action_id, -1)
    if aid < 0:
        return False
    profile = load_profile(force=True)
    char_key = normalize_character_key(char_name)
    chars = profile.get("characters") or {}
    char_entry = chars.get(char_key)
    if not isinstance(char_entry, dict):
        return True
    moves = char_entry.get("moves")
    if not isinstance(moves, dict):
        return True
    moves.pop(f"0x{aid:04X}", None)
    if not moves:
        chars.pop(char_key, None)
    return save_profile(profile)


def apply_windows_to_moves(moves: Iterable[dict[str, Any]], char_name: Any) -> None:
    for move in moves or []:
        if not isinstance(move, dict):
            continue
        action_id = move.get("id")
        window = get_window(char_name, action_id)
        move["custom_cancel_window"] = format_window(window)
        move["custom_cancel_window_data"] = dict(window) if window else None


__all__ = [
    "PROFILE_PATH",
    "normalize_character_key",
    "normalize_window",
    "parse_window_text",
    "format_window",
    "load_profile",
    "save_profile",
    "get_window",
    "set_window",
    "clear_window",
    "apply_windows_to_moves",
]
