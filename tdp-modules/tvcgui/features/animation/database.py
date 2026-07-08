"""MOT animation-frame database lookup for TvC frame-data rows.

The bundled animation_frames.json is generated directly from character FPKs.
It maps a SEQ action ID to its selected 0000.mot clip duration / total frames.

MOT-derived recovery is intentionally *static animation-tail recovery*:
    total animation frames - final active frame
For a single active window that is equivalent to:
    total - ((first_active - 1) + active_count)

It does not claim to replace any future runtime-cancel/landing/exit exception.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from tvcgui.core.paths import data_path, user_data_path

_DB_LOCK = threading.RLock()
_DB_CACHE: Optional[Dict[str, Any]] = None
_DB_PATH: Optional[Path] = None
_DB_MTIME_NS: Optional[int] = None


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _candidate_paths() -> Iterable[Path]:
    override = str(os.environ.get("TVC_ANIMATION_FRAMES_JSON") or "").strip()
    if override:
        yield Path(override)
    # The shipped database lives with the other animation resources.
    yield Path(data_path("animation", "animation_frames.json"))
    # Keep a persistent adjacent-to-EXE override available for frozen installs.
    try:
        import sys
        if getattr(sys, "frozen", False):
            yield Path(user_data_path("animation", "animation_frames.json"))
    except Exception:
        pass


def _load_database() -> Dict[str, Any]:
    global _DB_CACHE, _DB_PATH, _DB_MTIME_NS
    with _DB_LOCK:
        for path in _candidate_paths():
            try:
                stat = path.stat()
            except OSError:
                continue
            if _DB_CACHE is not None and _DB_PATH == path and _DB_MTIME_NS == stat.st_mtime_ns:
                return _DB_CACHE
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(doc, dict) or not isinstance(doc.get("characters"), dict):
                continue
            _DB_CACHE = doc
            _DB_PATH = path
            _DB_MTIME_NS = stat.st_mtime_ns
            return doc
        _DB_CACHE = {"characters": {}, "character_aliases": {}}
        _DB_PATH = None
        _DB_MTIME_NS = None
        return _DB_CACHE


def _resolve_character_key(char_name: Any, char_id: Any = None) -> Optional[str]:
    doc = _load_database()
    characters = doc.get("characters") or {}
    aliases = doc.get("character_aliases") or {}
    if not isinstance(characters, dict):
        return None

    raw = str(char_name or "")
    candidates = [raw]
    # Profile keys look like id_12_ryu.  Preserve the final human-name portion.
    if "_" in raw:
        candidates.append(raw.rsplit("_", 1)[-1])
    if char_id is not None:
        candidates.append(f"id_{char_id}")

    for candidate in candidates:
        norm = _normalize(candidate)
        if not norm:
            continue
        alias = aliases.get(norm)
        if isinstance(alias, str) and alias in characters:
            return alias
        if candidate.lower() in characters:
            return candidate.lower()
        if norm in characters:
            return norm

    # Exact normalized comparison covers JSONs built without an aliases table.
    for key in characters:
        if _normalize(key) in {_normalize(c) for c in candidates}:
            return str(key)
    return None


def _action_record(char_key: str, action_id: Any) -> Optional[Dict[str, Any]]:
    try:
        aid = int(action_id)
    except Exception:
        return None
    doc = _load_database()
    char = (doc.get("characters") or {}).get(char_key)
    if not isinstance(char, dict):
        return None
    motions = char.get("motions") or {}
    # 0000.mot is the primary action MOT for every character FPK scanned here.
    motion = motions.get("0000.mot")
    if not isinstance(motion, dict):
        return None
    actions = motion.get("actions") or {}
    record = actions.get(f"0x{aid:04X}")
    return record if isinstance(record, dict) else None


def _mot_recovery(total_frames: int, move: Dict[str, Any]) -> Optional[int]:
    """Return recovery after the final known active frame in the action."""
    ends = []
    for key in ("active_end", "active2_end"):
        try:
            value = move.get(key)
            if value is not None:
                ends.append(int(value))
        except Exception:
            pass
    if not ends:
        return None
    return max(0, int(total_frames) - max(ends))


def apply_animation_metadata(moves: Iterable[Dict[str, Any]], char_name: Any, char_id: Any = None) -> None:
    """Attach MOT total/recovery metadata in-place to scanner/profile rows."""
    char_key = _resolve_character_key(char_name, char_id)
    if not char_key:
        return

    for move in moves:
        if not isinstance(move, dict):
            continue
        record = _action_record(char_key, move.get("id"))
        if record is None:
            continue
        try:
            total = int(record.get("total_frames"))
        except Exception:
            continue
        if total < 0:
            continue

        move["animation_total_frames"] = total
        move["animation_duration_seconds"] = record.get("duration_seconds")
        move["animation_char_key"] = char_key
        move["animation_motion"] = "0000.mot"
        move["animation_clip_offset"] = record.get("clip_offset")
        move["animation_duration_field_offset"] = record.get("duration_field_offset")

        recovery = _mot_recovery(total, move)
        if recovery is None:
            continue

        # Preserve any prior runtime result separately for audit/debugging, but
        # let the UI's Recovery column show the requested MOT-derived value.
        if str(move.get("recovery_source") or "") == "runtime_observed" and move.get("recovery") is not None:
            move["runtime_observed_recovery"] = move.get("recovery")
        move["recovery"] = recovery
        move["recovery_source"] = "mot_derived"
        move["recovery_formula"] = "total_animation_frames - final_active_frame"

        # Advantage already consumes recovery downstream. Refresh it using the
        # same static frame-tail value so frame-data rows stay internally aligned.
        try:
            hitstun = int(move.get("hitstun") or 0)
        except Exception:
            hitstun = 0
        try:
            blockstun = int(move.get("blockstun") or 0)
        except Exception:
            blockstun = 0
        move["adv_hit"] = hitstun - recovery
        move["adv_hit_derived"] = hitstun - recovery
        move["adv_block"] = blockstun - recovery
        move["adv_block_derived"] = blockstun - recovery


def animation_database_status() -> Dict[str, Any]:
    """Small diagnostic payload for UI/logging without exposing raw JSON."""
    doc = _load_database()
    return {
        "path": str(_DB_PATH) if _DB_PATH else "",
        "characters": len(doc.get("characters") or {}),
        "schema_version": doc.get("schema_version"),
    }
