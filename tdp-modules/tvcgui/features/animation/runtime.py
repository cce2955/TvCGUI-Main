"""Runtime MOT table resolution and animation-only pointer writes.

A character action's SEQ block controls gameplay flow. Its 01 xx 01 3C
packets are action links, not MOT clip selectors. This module changes the
loaded 0000.mot table entry instead: the source action remains unchanged while
its table slot points at a selected clip in the same loaded MOT bank.
"""
from __future__ import annotations

import struct
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Optional

MEM1_LO = 0x80000000
MEM1_HI = 0x81800000
MEM2_LO = 0x90000000
MEM2_HI = 0x94000000

_RESOURCE_WINDOW_LO = 0x80700000
_RESOURCE_WINDOW_HI = 0x80800000

_CACHE_LOCK = threading.RLock()
_MOT_CACHE: dict[str, "LoadedMot"] = {}
_ORIGINAL_POINTERS: dict[int, int] = {}


@dataclass(frozen=True)
class MotionDescriptor:
    char_key: str
    action_id: int
    clip_offset: int
    table_count: int
    uncompressed_size: int


@dataclass(frozen=True)
class LoadedMot:
    char_key: str
    base: int
    table_addr: int
    table_count: int
    uncompressed_size: int


def _u32(data: bytes, offset: int = 0) -> int:
    return struct.unpack_from(">I", data, offset)[0]


def _coerce_int(value: Any) -> Optional[int]:
    try:
        if isinstance(value, str):
            return int(value, 0)
        return int(value)
    except Exception:
        return None


def _motion_database() -> dict[str, Any]:
    try:
        from . import database as frames
        loader = getattr(frames, "_load_database", None)
        if callable(loader):
            doc = loader()
            return doc if isinstance(doc, dict) else {}
    except Exception:
        pass
    return {}


def resolve_char_key(char_name: Any = None, char_id: Any = None, move: Optional[dict] = None) -> Optional[str]:
    if isinstance(move, dict):
        raw = str(move.get("animation_char_key") or "").strip()
        if raw:
            return raw
    try:
        from . import database as frames
        resolver = getattr(frames, "_resolve_character_key", None)
        if callable(resolver):
            key = resolver(char_name, char_id)
            if key:
                return str(key)
    except Exception:
        pass

    doc = _motion_database()
    chars = doc.get("characters") or {}
    aliases = doc.get("character_aliases") or {}
    candidates = [str(char_name or "").strip().lower()]
    if isinstance(move, dict):
        candidates.append(str(move.get("animation_char_key") or "").strip().lower())
    for candidate in candidates:
        if not candidate:
            continue
        if candidate in chars:
            return candidate
        alias = aliases.get(candidate)
        if isinstance(alias, str) and alias in chars:
            return alias
    return None


def motion_descriptor(char_key: str, action_id: Any) -> Optional[MotionDescriptor]:
    aid = _coerce_int(action_id)
    if aid is None:
        return None
    doc = _motion_database()
    char = (doc.get("characters") or {}).get(str(char_key))
    if not isinstance(char, dict):
        return None
    motion = (char.get("motions") or {}).get("0000.mot")
    if not isinstance(motion, dict):
        return None
    rec = (motion.get("actions") or {}).get(f"0x{aid & 0xFFFF:04X}")
    if not isinstance(rec, dict):
        return None
    clip_offset = _coerce_int(rec.get("clip_offset"))
    table_count = _coerce_int(motion.get("motion_table_count"))
    uncompressed_size = _coerce_int(motion.get("uncompressed_size"))
    if clip_offset is None or table_count is None or uncompressed_size is None:
        return None
    return MotionDescriptor(
        char_key=str(char_key),
        action_id=aid & 0xFFFF,
        clip_offset=clip_offset,
        table_count=table_count,
        uncompressed_size=uncompressed_size,
    )


def _valid_loaded_mot(candidate: int, char_key: str, source: MotionDescriptor, rbytes: Callable[[int, int], bytes]) -> Optional[LoadedMot]:
    if not (MEM2_LO <= int(candidate) < MEM2_HI):
        return None
    try:
        head = bytes(rbytes(int(candidate), 0x10) or b"")
    except Exception:
        return None
    if len(head) < 0x10:
        return None
    try:
        version, count, table_addr, size = struct.unpack(">IIII", head)
    except Exception:
        return None
    if version != 1 or count != source.table_count or size != source.uncompressed_size:
        return None
    if table_addr != int(candidate) + 0x10:
        return None
    if not (MEM2_LO <= table_addr < MEM2_HI):
        return None
    slot_addr = table_addr + source.action_id * 4
    if not (table_addr <= slot_addr < table_addr + count * 4):
        return None
    try:
        slot = bytes(rbytes(slot_addr, 4) or b"")
    except Exception:
        return None
    if len(slot) < 4:
        return None
    current_ptr = _u32(slot)
    expected_ptr = int(candidate) + source.clip_offset
    if current_ptr != expected_ptr:
        # The selected slot may already be replaced. Validate nearby immutable
        # anchor slots before accepting the bank.
        anchors = _anchor_descriptors(char_key, source.action_id)
        valid_anchor = False
        for anchor in anchors:
            a_addr = table_addr + anchor.action_id * 4
            if not (table_addr <= a_addr < table_addr + count * 4):
                continue
            try:
                word = bytes(rbytes(a_addr, 4) or b"")
            except Exception:
                continue
            if len(word) == 4 and _u32(word) == int(candidate) + anchor.clip_offset:
                valid_anchor = True
                break
        if not valid_anchor:
            return None
    return LoadedMot(
        char_key=char_key,
        base=int(candidate),
        table_addr=table_addr,
        table_count=count,
        uncompressed_size=size,
    )


def _anchor_descriptors(char_key: str, source_action_id: int) -> list[MotionDescriptor]:
    doc = _motion_database()
    char = (doc.get("characters") or {}).get(str(char_key))
    motion = ((char or {}).get("motions") or {}).get("0000.mot") if isinstance(char, dict) else None
    actions = (motion or {}).get("actions") if isinstance(motion, dict) else {}
    ordered_ids: list[int] = []
    for candidate in (0x0100, 0x0101, 0x0102, source_action_id, 0x0000, 0x0001, 0x0002):
        if candidate not in ordered_ids:
            ordered_ids.append(candidate)
    out: list[MotionDescriptor] = []
    for aid in ordered_ids:
        rec = actions.get(f"0x{aid & 0xFFFF:04X}") if isinstance(actions, dict) else None
        if not isinstance(rec, dict):
            continue
        desc = motion_descriptor(char_key, aid)
        if desc is not None:
            out.append(desc)
    return out


def _candidate_headers_from_resource_block(block: bytes, block_addr: int) -> Iterable[int]:
    if not block:
        return ()
    out: list[int] = []
    for off in range(0, max(0, len(block) - 4), 4):
        ptr = _u32(block, off)
        if MEM2_LO <= ptr < MEM2_HI:
            out.append(ptr)
    return out


def _find_resource_paths(char_key: str, rbytes: Callable[[int, int], bytes]) -> list[int]:
    path = f"chr/{char_key}/0000.mot\x00".encode("ascii", "ignore")
    locations: list[int] = []
    ranges = ((_RESOURCE_WINDOW_LO, _RESOURCE_WINDOW_HI - _RESOURCE_WINDOW_LO), (MEM1_LO, MEM1_HI - MEM1_LO))
    seen: set[int] = set()
    for start, length in ranges:
        try:
            data = bytes(rbytes(start, length) or b"")
        except Exception:
            continue
        pos = 0
        found_here = False
        while True:
            hit = data.find(path, pos)
            if hit < 0:
                break
            addr = start + hit
            if addr not in seen:
                seen.add(addr)
                locations.append(addr)
            pos = hit + 1
            found_here = True
        if found_here:
            break
    return locations


def locate_loaded_mot(
    char_name: Any = None,
    char_id: Any = None,
    move: Optional[dict] = None,
    *,
    rbytes: Callable[[int, int], bytes],
    force_refresh: bool = False,
) -> tuple[Optional[LoadedMot], str]:
    char_key = resolve_char_key(char_name, char_id, move)
    if not char_key:
        return None, "character MOT key unavailable"
    source_id = _coerce_int((move or {}).get("id") if isinstance(move, dict) else None)
    if source_id is None:
        return None, "source action unavailable"
    source = motion_descriptor(char_key, source_id)
    if source is None:
        return None, f"MOT clip missing for action 0x{source_id & 0xFFFF:04X}"

    with _CACHE_LOCK:
        cached = _MOT_CACHE.get(char_key)
    if cached is not None and not force_refresh:
        checked = _valid_loaded_mot(cached.base, char_key, source, rbytes)
        if checked is not None:
            return checked, "cached"

    path_hits = _find_resource_paths(char_key, rbytes)
    for path_addr in path_hits:
        try:
            block = bytes(rbytes(path_addr, 0x100) or b"")
        except Exception:
            continue
        for pointer in _candidate_headers_from_resource_block(block, path_addr):
            resolved = _valid_loaded_mot(pointer, char_key, source, rbytes)
            if resolved is not None:
                with _CACHE_LOCK:
                    _MOT_CACHE[char_key] = resolved
                return resolved, "resource registry"

    return None, "loaded 0000.mot bank not found"


def animation_id_from_pointer(loaded: LoadedMot, char_key: str, pointer: int) -> Optional[int]:
    doc = _motion_database()
    char = (doc.get("characters") or {}).get(char_key)
    motion = ((char or {}).get("motions") or {}).get("0000.mot") if isinstance(char, dict) else None
    actions = (motion or {}).get("actions") if isinstance(motion, dict) else {}
    if not isinstance(actions, dict):
        return None
    relative = int(pointer) - int(loaded.base)
    for key, rec in actions.items():
        if not isinstance(rec, dict):
            continue
        clip_offset = _coerce_int(rec.get("clip_offset"))
        if clip_offset == relative:
            try:
                return int(key, 0)
            except Exception:
                continue
    return None


def get_current_animation_id(
    move: dict,
    *,
    char_name: Any = None,
    char_id: Any = None,
    rbytes: Callable[[int, int], bytes],
) -> tuple[Optional[int], str]:
    source_id = _coerce_int(move.get("id") if isinstance(move, dict) else None)
    if source_id is None:
        return None, "source action unavailable"
    loaded, source = locate_loaded_mot(char_name, char_id, move, rbytes=rbytes)
    if loaded is None:
        return None, source
    slot_addr = loaded.table_addr + (source_id & 0xFFFF) * 4
    try:
        word = bytes(rbytes(slot_addr, 4) or b"")
    except Exception:
        return None, "source MOT slot unreadable"
    if len(word) < 4:
        return None, "source MOT slot unreadable"
    action = animation_id_from_pointer(loaded, loaded.char_key, _u32(word))
    if action is None:
        return None, "source MOT slot uses an unmapped clip"
    return action, "ok"


def write_animation_only(
    move: dict,
    target_action_id: Any,
    *,
    char_name: Any = None,
    char_id: Any = None,
    rbytes: Callable[[int, int], bytes],
    wd32: Callable[[int, int], bool],
) -> tuple[bool, dict[str, Any]]:
    source_id = _coerce_int(move.get("id") if isinstance(move, dict) else None)
    target_id = _coerce_int(target_action_id)
    if source_id is None or target_id is None:
        return False, {"reason": "source or target action unavailable"}

    loaded, source = locate_loaded_mot(char_name, char_id, move, rbytes=rbytes)
    if loaded is None:
        return False, {"reason": source}
    target = motion_descriptor(loaded.char_key, target_id)
    if target is None:
        return False, {"reason": f"MOT clip missing for action 0x{target_id & 0xFFFF:04X}"}
    if target.table_count != loaded.table_count or target.uncompressed_size != loaded.uncompressed_size:
        return False, {"reason": "target clip belongs to a different MOT bank"}

    table_slot = loaded.table_addr + (source_id & 0xFFFF) * 4
    if not (loaded.table_addr <= table_slot < loaded.table_addr + loaded.table_count * 4):
        return False, {"reason": "source action is outside the MOT table"}
    target_ptr = loaded.base + target.clip_offset
    try:
        before_bytes = bytes(rbytes(table_slot, 4) or b"")
    except Exception:
        return False, {"reason": "source MOT slot unreadable"}
    if len(before_bytes) < 4:
        return False, {"reason": "source MOT slot unreadable"}
    before = _u32(before_bytes)
    with _CACHE_LOCK:
        _ORIGINAL_POINTERS.setdefault(table_slot, before)
    try:
        ok = bool(wd32(table_slot, target_ptr))
    except Exception as exc:
        return False, {"reason": f"MOT table write failed: {exc}"}
    if not ok:
        return False, {"reason": "MOT table write failed"}

    move["animation_id"] = target_id & 0xFFFF
    move["animation_runtime_table_addr"] = table_slot
    move["animation_runtime_mot_base"] = loaded.base
    move["animation_runtime_target_ptr"] = target_ptr
    move["animation_runtime_source_action"] = source_id & 0xFFFF
    move["animation_runtime_bank"] = loaded.char_key
    return True, {
        "reason": "ok",
        "source_action": source_id & 0xFFFF,
        "target_action": target_id & 0xFFFF,
        "table_slot": table_slot,
        "before_ptr": before,
        "target_ptr": target_ptr,
        "mot_base": loaded.base,
        "char_key": loaded.char_key,
    }


def restore_animation_only(move: dict, *, wd32: Callable[[int, int], bool]) -> tuple[bool, str]:
    table_slot = _coerce_int(move.get("animation_runtime_table_addr") if isinstance(move, dict) else None)
    if table_slot is None:
        return False, "source MOT slot unavailable"
    with _CACHE_LOCK:
        original = _ORIGINAL_POINTERS.get(table_slot)
    if original is None:
        return False, "source MOT original pointer unavailable"
    try:
        ok = bool(wd32(table_slot, original))
    except Exception as exc:
        return False, f"MOT table restore failed: {exc}"
    if not ok:
        return False, "MOT table restore failed"
    source = _coerce_int(move.get("id"))
    if source is not None:
        move["animation_id"] = source & 0xFFFF
    return True, "ok"


def clear_cache() -> None:
    with _CACHE_LOCK:
        _MOT_CACHE.clear()
