# fd_patch_runtime.py
#
# Runtime helpers for shareable frame-data patch configs.
#
# fd_window owns the workbench UI, but the main HUD also needs to know about
# saved patches so it can offer startup/per-character loading and keep the
# Normals Preview in sync with patch/manual GUI edits.

from __future__ import annotations

import glob
import json
import os
import sys
from dataclasses import dataclass, field
from typing import Any

SCHEMA = "tvc_continuo.frame_data_patch.v1"
PATCH_DIR_NAME = "fd_patches"

# In-memory display overlays. These are intentionally separate from the actual
# Dolphin writes. They let already-scanned data reflect GUI edits immediately
# even before the next scan_worker result lands.
_LIVE_ENTRIES_BY_CHAR: dict[str, list[dict]] = {}


def app_base_dir() -> str:
    """Persistent app folder for user-editable files.

    In source runs this is the tdp-modules folder. In a PyInstaller one-file
    build, __file__ points into the temporary _MEIPASS extraction directory,
    which is deleted after exit. Patch configs must live beside TvCGUI.exe
    instead so saved/shared balance patches survive restarts and can be dropped
    into dist\fd_patches.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def module_dir() -> str:
    # Kept for existing callers, but it now means the persistent app folder.
    return app_base_dir()


def patch_default_dir() -> str:
    base = app_base_dir()
    path = os.path.join(base, PATCH_DIR_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        path = base
    return path


def normalize_char_key(value: Any) -> str:
    return str(value or "").strip().lower()


def patch_char_key_for_slot(slot_data: dict | None) -> str:
    if not isinstance(slot_data, dict):
        return "Unknown"
    return str(
        slot_data.get("char_name")
        or slot_data.get("character")
        or slot_data.get("name")
        or slot_data.get("slot_label")
        or "Unknown"
    )


def discover_patch_files(directory: str | None = None) -> list[str]:
    directory = directory or patch_default_dir()
    paths = sorted(glob.glob(os.path.join(directory, "*.json")))
    return [p for p in paths if read_patch_document(p, quiet=True) is not None]


def read_patch_document(path: str, *, quiet: bool = False) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        if not quiet:
            print(f"[fd patch] could not read {path}: {e}")
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema") != SCHEMA:
        return None
    if not isinstance(data.get("characters"), dict):
        data["characters"] = {}
    data.setdefault("_path", path)
    return data


def character_section(doc: dict, char_key: str) -> tuple[str | None, dict | None]:
    chars = doc.get("characters") or {}
    if char_key in chars and isinstance(chars.get(char_key), dict):
        return char_key, chars[char_key]
    wanted = normalize_char_key(char_key)
    for k, v in chars.items():
        if normalize_char_key(k) == wanted and isinstance(v, dict):
            return str(k), v
    return None, None


def parse_patch_abs(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, str):
            txt = value.strip()
            return int(txt, 16) if txt.lower().startswith("0x") else int(txt, 10)
        return int(value)
    except Exception:
        return None


def _patch_bool_enabled(value: Any) -> bool:
    try:
        from fd_patterns import SUPERBG_ON
    except Exception:
        SUPERBG_ON = 0x04
    if isinstance(value, dict):
        if "enabled" in value:
            return bool(value.get("enabled"))
        if "raw" in value:
            try:
                return int(value.get("raw")) == SUPERBG_ON
            except Exception:
                return False
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "on", "yes", "enabled"}
    try:
        return int(value) == SUPERBG_ON
    except Exception:
        return bool(value)


def _coerce_int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except Exception:
        return default


def _coerce_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except Exception:
        return default


def _address_value(value: Any) -> int | None:
    return parse_patch_abs(value)


def _inject_entry_addresses(mv: dict, entry: dict) -> None:
    """Populate lazy addresses from a patch entry when the current scan row lacks them.

    Saved configs store both absolute and relative addresses. Absolute addresses
    work on the same build. Relative addresses let the same selector survive if
    a row is found by its current move-table base.
    """
    if not isinstance(mv, dict) or not isinstance(entry, dict):
        return
    addresses = entry.get("addresses") or {}
    if not isinstance(addresses, dict):
        return

    base = _coerce_int(mv.get("abs"), None)
    keys = (
        "damage_addr", "meter_addr", "active_addr", "active2_addr",
        "stun_addr", "knockback_addr", "speed_mod_addr", "attack_property_addr",
        "hit_reaction_addr", "hit_result_addr", "superbg_addr", "combo_kb_mod_addr", "proj_tpl",
    )
    for key in keys:
        if mv.get(key) not in (None, ""):
            continue
        rel_key = f"{key}_rel"
        val = None
        if base is not None and rel_key in addresses:
            try:
                val = int(base) + int(addresses.get(rel_key))
            except Exception:
                val = None
        if val is None:
            val = _address_value(addresses.get(key))
        if val is not None:
            mv[key] = int(val)

    if mv.get("abs") in (None, ""):
        abs_val = _address_value(addresses.get("abs"))
        if abs_val is not None:
            mv["abs"] = abs_val

    if mv.get("hb_off") in (None, "") and addresses.get("hb_off") is not None:
        try:
            mv["hb_off"] = int(addresses.get("hb_off"))
        except Exception:
            pass


def _iter_move_hit_segments(moves: list[dict]):
    for parent in moves:
        if not isinstance(parent, dict):
            continue
        segments = parent.get("hit_segments") or []
        if not isinstance(segments, list):
            continue
        for idx, seg in enumerate(segments, start=1):
            if not isinstance(seg, dict):
                continue
            seg.setdefault("kind", "hit")
            seg.setdefault("id", parent.get("id"))
            seg.setdefault("move_name", parent.get("move_name"))
            seg.setdefault("parent_abs", parent.get("abs"))
            seg.setdefault("parent_id", parent.get("id"))
            seg.setdefault("hit_index", idx)
            if seg.get("abs") in (None, ""):
                seg["abs"] = seg.get("active_addr") or seg.get("damage_addr") or parent.get("abs")
            yield parent, seg


def _entry_address_values(entry: dict) -> set[int]:
    values: set[int] = set()
    addresses = entry.get("addresses") or {}
    if not isinstance(addresses, dict):
        return values
    for key, value in addresses.items():
        if str(key).endswith("_rel"):
            continue
        if key in {"abs", "damage_addr", "active_addr", "active2_addr", "stun_addr", "knockback_addr", "attack_property_addr", "hit_reaction_addr", "hit_result_addr"}:
            parsed = parse_patch_abs(value)
            if parsed is not None:
                values.add(int(parsed))
    return values


def find_patch_target_in_slot(slot_data: dict, entry: dict) -> tuple[dict | None, str]:
    if not isinstance(slot_data, dict):
        return None, "missing slot"
    moves = slot_data.get("moves") or []
    if not isinstance(moves, list):
        return None, "missing moves"

    selector = entry.get("selector") or {}
    wanted_abs = parse_patch_abs(selector.get("abs") or (entry.get("addresses") or {}).get("abs"))
    wanted_id = _coerce_int(selector.get("move_id"), None)
    wanted_kind = selector.get("kind")
    wanted_tier = _coerce_int(selector.get("tier"), None)
    wanted_scan = _coerce_int(selector.get("scan_index"), None)
    wanted_segment = _coerce_int(selector.get("segment_index"), None)
    address_values = _entry_address_values(entry)

    # Exact address match: first normal rows, then nested per-hit rows.
    if wanted_abs is not None:
        for mv in moves:
            if not isinstance(mv, dict):
                continue
            try:
                if int(mv.get("abs") or -1) == wanted_abs:
                    return mv, "abs"
            except Exception:
                pass
        for _parent, seg in _iter_move_hit_segments(moves):
            try:
                if int(seg.get("abs") or seg.get("active_addr") or -1) == wanted_abs:
                    return seg, "hit_abs"
            except Exception:
                pass

    # Field-address match catches exported hit rows where the selector stores a
    # segment's active/damage/stun/KB address rather than the parent move base.
    if address_values:
        for _parent, seg in _iter_move_hit_segments(moves):
            for key in ("abs", "active_addr", "damage_addr", "stun_addr", "knockback_addr", "attack_property_addr", "hit_reaction_addr", "hit_result_addr"):
                try:
                    if int(seg.get(key) or -1) in address_values:
                        return seg, f"hit_{key}"
                except Exception:
                    pass

    if str(wanted_kind or "") == "hit":
        hit_candidates: list[dict] = []
        for parent, seg in _iter_move_hit_segments(moves):
            if wanted_id is not None:
                try:
                    parent_id = int(parent.get("id")) if parent.get("id") is not None else int(seg.get("parent_id"))
                    if parent_id != wanted_id:
                        continue
                except Exception:
                    continue
            if wanted_segment is not None:
                try:
                    if int(seg.get("hit_index") or seg.get("_hit_segment_index") or -1) != wanted_segment:
                        continue
                except Exception:
                    continue
            hit_candidates.append(seg)
        if hit_candidates:
            return hit_candidates[0], "hit_segment"

    candidates: list[dict] = []
    for mv in moves:
        if not isinstance(mv, dict):
            continue
        if wanted_id is not None:
            try:
                if int(mv.get("id")) != wanted_id:
                    continue
            except Exception:
                continue
        if wanted_kind and mv.get("kind") != wanted_kind:
            continue
        candidates.append(mv)

    if wanted_tier is not None:
        for mv in candidates:
            try:
                if int(mv.get("dup_index")) == wanted_tier:
                    return mv, "move_id+tier"
            except Exception:
                pass

    if wanted_scan is not None:
        for mv in candidates:
            try:
                if int(mv.get("_scan_index")) == wanted_scan:
                    return mv, "move_id+scan_index"
            except Exception:
                pass

    if candidates:
        return candidates[0], "move_id"

    return None, "not found"

def _ensure_speed_mod(mv: dict) -> bool:
    if mv.get("speed_mod_addr") is not None:
        return True
    move_abs = mv.get("abs")
    if not move_abs:
        return False
    try:
        from dolphin_io import rbytes
        from fd_patterns import find_speed_mod_addr
        addr, cur, sig = find_speed_mod_addr(move_abs, rbytes)
    except Exception:
        addr, cur, sig = (None, None, None)
    if addr:
        mv["speed_mod_addr"] = addr
        mv["speed_mod"] = cur
        mv["speed_mod_sig"] = sig
        return True
    return False


def _ensure_attack_property(mv: dict) -> bool:
    if mv.get("attack_property_addr") is not None:
        return True
    move_abs = mv.get("abs")
    if not move_abs:
        return False
    try:
        from dolphin_io import rbytes
        from fd_patterns import find_attack_property_addr
        addr, cur, sig = find_attack_property_addr(move_abs, rbytes)
    except Exception:
        addr, cur, sig = (None, None, None)
    if addr:
        mv["attack_property_addr"] = addr
        mv["attack_property"] = cur
        mv["attack_property_sig"] = sig
        return True
    return False


def _ensure_superbg(mv: dict) -> bool:
    if mv.get("superbg_addr") is not None:
        return True
    move_abs = mv.get("abs")
    if not move_abs:
        return False
    try:
        from dolphin_io import rbytes, rd8
        from fd_patterns import find_superbg_addr
        addr, cur = find_superbg_addr(move_abs, rbytes, rd8)
    except Exception:
        addr, cur = (None, None)
    if addr:
        mv["superbg_addr"] = addr
        mv["superbg_val"] = cur
        return True
    return False


def _ensure_combo_kb_mod(mv: dict) -> bool:
    if mv.get("combo_kb_mod_addr") is not None:
        return True
    move_abs = mv.get("abs")
    if not move_abs:
        return False
    try:
        from dolphin_io import rbytes
        from fd_patterns import find_combo_kb_mod_addr
        addr, cur, sig = find_combo_kb_mod_addr(move_abs, rbytes)
    except Exception:
        addr, cur, sig = (None, None, None)
    if addr:
        mv["combo_kb_mod_addr"] = addr
        mv["combo_kb_mod"] = cur
        mv["combo_kb_sig"] = sig
        return True
    return False


def _write_attack_property_inline(mv: dict, value: int) -> bool:
    addr = mv.get("attack_property_addr")
    if not addr:
        return False
    try:
        from dolphin_io import wbytes
        wbytes(int(addr), bytes([int(value) & 0xFF]))
        return True
    except Exception:
        pass
    try:
        from dolphin_io import wd8
        wd8(int(addr), int(value) & 0xFF)
        return True
    except Exception:
        return False


def _write_anim_id_runtime(mv: dict, new_anim_id: int) -> bool:
    try:
        import fd_utils as U
        if not U.WRITER_AVAILABLE:
            return False
    except Exception:
        return False
    base = mv.get("abs")
    if not base:
        return False
    try:
        from dolphin_io import rbytes, wd8
    except Exception:
        return False

    try:
        buf = rbytes(base, 0x80)
    except Exception as e:
        print(f"[fd patch] anim-id read failed @0x{int(base):08X}: {e}")
        return False

    target_off = None
    for i in range(0, len(buf) - 4):
        b0, _b1, b2, b3 = buf[i], buf[i + 1], buf[i + 2], buf[i + 3]
        if b0 == 0x01 and b2 == 0x01 and b3 == 0x3C:
            target_off = i
            break
    if target_off is None:
        print(f"[fd patch] anim-id pattern not found @0x{int(base):08X}")
        return False

    addr = int(base) + target_off
    new_hi = (int(new_anim_id) >> 8) & 0xFF
    new_lo = int(new_anim_id) & 0xFF
    try:
        return bool(wd8(addr, new_hi) and wd8(addr + 1, new_lo))
    except Exception as e:
        print(f"[fd patch] anim-id write failed @0x{addr:08X}: {e}")
        return False


def apply_patch_change_to_move(mv: dict, entry: dict, *, write_to_dolphin: bool = True) -> tuple[bool, str]:
    if not isinstance(mv, dict) or not isinstance(entry, dict):
        return False, "bad entry"

    _inject_entry_addresses(mv, entry)
    group = str(entry.get("group") or "")
    value = entry.get("value")
    if not group:
        return False, "missing group"

    if not write_to_dolphin:
        apply_patch_value_to_move(mv, entry)
        return True, "overlay"

    try:
        import fd_utils as U
        from fd_write_helpers import (
            write_active2_frames_inline,
            write_combo_kb_mod_inline,
            write_hit_reaction_inline,
            write_proj_dmg_inline,
            write_speed_mod_inline,
            write_superbg_inline,
            write_u32_field_inline,
            write_f32_field_inline,
        )
    except Exception as e:
        return False, f"writer imports failed: {e}"

    if not getattr(U, "WRITER_AVAILABLE", False):
        return False, "writer unavailable"

    try:
        if group == "move":
            new_val = int(value)
            if not _write_anim_id_runtime(mv, new_val):
                return False, "write failed"
            mv["id"] = new_val

        elif group == "damage":
            new_val = int(value)
            if not U.write_damage(mv, new_val):
                return False, "write failed"
            mv["damage"] = new_val

        elif group == "meter":
            new_val = int(value)
            if not U.write_meter(mv, new_val):
                return False, "write failed"
            mv["meter"] = new_val

        elif group == "active":
            s = int((value or {}).get("start"))
            e = int((value or {}).get("end"))
            if e < s:
                e = s
            if not U.write_active_frames(mv, s, e):
                return False, "write failed"
            mv["active_start"] = s
            mv["active_end"] = e
            mv["startup"] = s

        elif group == "active2":
            s = int((value or {}).get("start"))
            e = int((value or {}).get("end"))
            if e < s:
                e = s
            if not write_active2_frames_inline(mv, s, e, U.WRITER_AVAILABLE):
                return False, "write failed"
            mv["active2_start"] = s
            mv["active2_end"] = e

        elif group == "hitstun":
            new_val = int(value)
            if not U.write_hitstun(mv, new_val):
                return False, "write failed"
            mv["hitstun"] = new_val

        elif group == "blockstun":
            new_val = int(value)
            if not U.write_blockstun(mv, new_val):
                return False, "write failed"
            mv["blockstun"] = new_val

        elif group == "hitstop":
            new_val = int(value)
            if not U.write_hitstop(mv, new_val):
                return False, "write failed"
            mv["hitstop"] = new_val

        elif group in {"hit_spark", "stretch_part", "stretch_time", "post_link"}:
            mapping = {
                "hit_spark": ("hit_spark_addr", "hit_spark"),
                "stretch_part": ("stretch_part_addr", "stretch_part"),
                "stretch_time": ("stretch_time_addr", "stretch_time"),
                "post_link": ("post_link_addr", "post_link"),
            }
            addr_key, val_key = mapping[group]
            if not write_u32_field_inline(mv, addr_key, val_key, int(value)):
                return False, "write failed"

        elif group in {"stretch_len", "stretch_width", "stretch_height"}:
            mapping = {
                "stretch_len": ("stretch_len_addr", "stretch_len"),
                "stretch_width": ("stretch_width_addr", "stretch_width"),
                "stretch_height": ("stretch_height_addr", "stretch_height"),
            }
            addr_key, val_key = mapping[group]
            if not write_f32_field_inline(mv, addr_key, val_key, float(value)):
                return False, "write failed"

        elif group == "launch_profile":
            new_val = int(value) & 0xFFFFFFFF
            if not U.write_knockback(mv, launch_profile=new_val):
                return False, "write failed"
            mv["launch_profile"] = new_val

        elif group == "kb_unknown":
            new_val = int(value) & 0xFFFFFFFF
            if not U.write_knockback(mv, kb_unknown=new_val):
                return False, "write failed"
            mv["kb_unknown"] = new_val

        elif group == "kb_x":
            new_val = float(value)
            if not U.write_knockback(mv, kb_x=new_val):
                return False, "write failed"
            mv["kb_x"] = new_val

        elif group == "air_kb":
            new_val = float(value)
            if not U.write_knockback(mv, air_kb=new_val):
                return False, "write failed"
            mv["air_kb"] = new_val

        elif group == "speed_mod":
            _ensure_speed_mod(mv)
            new_val = int(value) & 0xFF
            if not write_speed_mod_inline(mv, new_val, U.WRITER_AVAILABLE):
                return False, "write failed"
            mv["speed_mod"] = new_val

        elif group == "attack_property":
            _ensure_attack_property(mv)
            new_val = int(value) & 0xFF
            if not _write_attack_property_inline(mv, new_val):
                return False, "write failed"
            mv["attack_property"] = new_val

        elif group == "hit_reaction":
            new_val = int(value) & 0xFFFFFFFF
            if not write_hit_reaction_inline(mv, new_val, U.WRITER_AVAILABLE):
                return False, "write failed"
            mv["hit_reaction"] = new_val

        elif group == "hit_result_flags":
            new_val = int(value) & 0xFFFFFFFF
            if not write_u32_field_inline(mv, "hit_result_addr", "hit_result_flags", new_val):
                return False, "write failed"

        elif group == "superbg":
            _ensure_superbg(mv)
            enabled = _patch_bool_enabled(value)
            if not write_superbg_inline(mv, enabled, U.WRITER_AVAILABLE):
                return False, "write failed"

        elif group == "combo_kb_mod":
            _ensure_combo_kb_mod(mv)
            new_val = int(value) & 0xFF
            if not write_combo_kb_mod_inline(mv, new_val, U.WRITER_AVAILABLE):
                return False, "write failed"
            mv["combo_kb_mod"] = new_val

        elif group == "proj_dmg":
            new_val = int(value) & 0xFFFF
            if not write_proj_dmg_inline(mv, new_val, U.WRITER_AVAILABLE):
                return False, "write failed"
            mv["proj_dmg"] = new_val

        elif group == "hb":
            new_val = float(value)
            if not U.write_hitbox_radius(mv, new_val):
                return False, "write failed"
            mv["hb_r"] = new_val

        else:
            return False, f"unsupported group {group}"

        mark_move_patched(mv, group)
        return True, "ok"
    except Exception as e:
        return False, str(e)


def mark_move_patched(mv: dict, group: str) -> None:
    try:
        fields = mv.setdefault("_fd_patch_fields", set())
        if isinstance(fields, set):
            fields.add(str(group))
        elif isinstance(fields, list):
            if str(group) not in fields:
                fields.append(str(group))
        else:
            mv["_fd_patch_fields"] = {str(group)}
        mv["_fd_patched"] = True
    except Exception:
        pass


def apply_patch_value_to_move(mv: dict, entry: dict) -> None:
    if not isinstance(mv, dict) or not isinstance(entry, dict):
        return
    group = str(entry.get("group") or "")
    value = entry.get("value")
    try:
        if group == "move":
            mv["id"] = int(value)
        elif group == "damage":
            mv["damage"] = int(value)
        elif group == "meter":
            mv["meter"] = int(value)
        elif group == "active":
            s = int((value or {}).get("start"))
            e = int((value or {}).get("end"))
            if e < s:
                e = s
            mv["active_start"] = s
            mv["active_end"] = e
            mv["startup"] = s
        elif group == "active2":
            s = int((value or {}).get("start"))
            e = int((value or {}).get("end"))
            if e < s:
                e = s
            mv["active2_start"] = s
            mv["active2_end"] = e
        elif group in {"hitstun", "blockstun", "hitstop", "hit_spark", "stretch_part", "stretch_time", "post_link", "launch_profile", "kb_unknown", "speed_mod", "attack_property", "hit_reaction", "hit_result_flags", "combo_kb_mod", "proj_dmg"}:
            mv[group] = int(value)
        elif group in {"kb_x", "air_kb", "stretch_len", "stretch_width", "stretch_height", "hb"}:
            if group == "hb":
                mv["hb_r"] = float(value)
            else:
                mv[group] = float(value)
        elif group == "superbg":
            try:
                from fd_patterns import SUPERBG_ON, SUPERBG_OFF
            except Exception:
                SUPERBG_ON, SUPERBG_OFF = 0x04, 0x01
            mv["superbg_val"] = SUPERBG_ON if _patch_bool_enabled(value) else SUPERBG_OFF
        mark_move_patched(mv, group)
    except Exception:
        pass


def set_live_entries_for_character(char_key: str, entries: list[dict] | None) -> None:
    key = normalize_char_key(char_key)
    if not key:
        return
    clean = [e for e in (entries or []) if isinstance(e, dict)]
    if clean:
        _LIVE_ENTRIES_BY_CHAR[key] = clean
    else:
        _LIVE_ENTRIES_BY_CHAR.pop(key, None)


def add_live_entries_for_character(char_key: str, entries: list[dict] | None) -> None:
    key = normalize_char_key(char_key)
    if not key:
        return
    clean = [e for e in (entries or []) if isinstance(e, dict)]
    if not clean:
        return
    existing = _LIVE_ENTRIES_BY_CHAR.setdefault(key, [])
    # Replace by selector abs + group where possible to avoid stacking stale
    # versions of the same value.
    for entry in clean:
        group = str(entry.get("group") or "")
        selector = entry.get("selector") or {}
        ident = selector.get("abs") or selector.get("move_id") or selector.get("scan_index")
        replaced = False
        for i, old in enumerate(list(existing)):
            old_sel = old.get("selector") or {}
            old_ident = old_sel.get("abs") or old_sel.get("move_id") or old_sel.get("scan_index")
            if str(old.get("group") or "") == group and old_ident == ident:
                existing[i] = entry
                replaced = True
                break
        if not replaced:
            existing.append(entry)


def overlay_scan_data(scan_data: list[dict] | None) -> int:
    """Apply current live display entries to scan_data in-place.

    This does not write to Dolphin. It only updates the dicts the HUD/FD editor
    are already displaying, so manual edits and loaded patches are visible in
    the preview immediately.
    """
    if not scan_data:
        return 0
    applied = 0
    for slot_data in list(scan_data or []):
        if not isinstance(slot_data, dict):
            continue
        char_key = patch_char_key_for_slot(slot_data)
        entries = _LIVE_ENTRIES_BY_CHAR.get(normalize_char_key(char_key)) or []
        if not entries:
            continue
        for entry in entries:
            mv, _match = find_patch_target_in_slot(slot_data, entry)
            if not mv:
                continue
            _inject_entry_addresses(mv, entry)
            apply_patch_value_to_move(mv, entry)
            applied += 1
    return applied


@dataclass
class LoadedPatch:
    path: str
    document: dict


@dataclass
class PatchAutoloadController:
    mode: str = "none"  # none | all | per_character
    patches: list[LoadedPatch] = field(default_factory=list)
    decided_chars: set[str] = field(default_factory=set)
    loaded_slot_keys: set[tuple] = field(default_factory=set)

    def available_characters(self) -> list[str]:
        chars: set[str] = set()
        for patch in self.patches:
            for k in (patch.document.get("characters") or {}).keys():
                chars.add(str(k))
        return sorted(chars)

    def matching_sections(self, char_key: str) -> list[tuple[LoadedPatch, str, dict]]:
        out = []
        for patch in self.patches:
            sec_key, sec = character_section(patch.document, char_key)
            if sec_key and isinstance(sec, dict):
                out.append((patch, sec_key, sec))
        return out

    def _slot_apply_key(self, slot_data: dict, char_key: str, patch_path: str) -> tuple:
        moves = slot_data.get("moves") or []
        first_abs = None
        last_abs = None
        try:
            abses = [int(mv.get("abs")) for mv in moves if isinstance(mv, dict) and mv.get("abs")]
            if abses:
                first_abs = min(abses)
                last_abs = max(abses)
        except Exception:
            pass
        return (str(slot_data.get("slot_label") or slot_data.get("slot") or ""), normalize_char_key(char_key), os.path.abspath(patch_path), first_abs, last_abs, len(moves))

    def _ask_load_char(self, char_key: str, sections: list[tuple[LoadedPatch, str, dict]]) -> bool:
        if self.mode != "per_character":
            return self.mode == "all"
        norm = normalize_char_key(char_key)
        if norm in self.decided_chars:
            # decided means it was either loaded once or dismissed. Use live
            # registry to know whether there are active entries.
            return bool(_LIVE_ENTRIES_BY_CHAR.get(norm))
        self.decided_chars.add(norm)
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            names = []
            for patch, _sec_key, sec in sections:
                title = patch.document.get("title") or os.path.basename(patch.path)
                count = int(sec.get("change_count") or len(sec.get("changes") or []))
                names.append(f"{title} ({count})")
            msg = "Patch section found for {0}.\n\nLoad it now?\n\n{1}".format(char_key, "\n".join(names[:8]))
            result = bool(messagebox.askyesno("Load frame-data patch", msg, parent=root))
            try:
                root.destroy()
            except Exception:
                pass
            return result
        except Exception as e:
            print(f"[fd patch] per-character prompt failed for {char_key}: {e}")
            return False

    def apply_to_scan_data(self, scan_data: list[dict] | None, *, write_to_dolphin: bool = True) -> dict:
        result = {"applied": 0, "skipped": 0, "failures": []}
        if self.mode == "none" or not self.patches or not scan_data:
            overlay_scan_data(scan_data)
            return result

        for slot_data in list(scan_data or []):
            if not isinstance(slot_data, dict):
                continue
            char_key = patch_char_key_for_slot(slot_data)
            sections = self.matching_sections(char_key)
            if not sections:
                continue
            if not self._ask_load_char(char_key, sections):
                continue

            entries_for_live: list[dict] = []
            for patch, sec_key, sec in sections:
                changes = sec.get("changes") or []
                if not isinstance(changes, list) or not changes:
                    continue
                slot_key = self._slot_apply_key(slot_data, char_key, patch.path)
                if slot_key in self.loaded_slot_keys:
                    entries_for_live.extend([e for e in changes if isinstance(e, dict)])
                    continue

                # Apply animation swaps last so old ID selectors still find the
                # row for the rest of that move's edits.
                ordered = sorted(
                    [e for e in changes if isinstance(e, dict)],
                    key=lambda e: 1 if str(e.get("group") or "") == "move" else 0,
                )
                for entry in ordered:
                    mv, match_kind = find_patch_target_in_slot(slot_data, entry)
                    if not mv:
                        result["skipped"] += 1
                        selector = entry.get("selector") or {}
                        label = selector.get("move_label") or selector.get("abs") or entry.get("group") or "unknown"
                        result["failures"].append(f"{char_key}: not found: {label}")
                        continue
                    ok, reason = apply_patch_change_to_move(mv, entry, write_to_dolphin=write_to_dolphin)
                    if ok:
                        result["applied"] += 1
                        entries_for_live.append(entry)
                    else:
                        result["skipped"] += 1
                        selector = entry.get("selector") or {}
                        label = selector.get("move_label") or selector.get("abs") or entry.get("group") or "unknown"
                        result["failures"].append(f"{char_key}: {label}: {reason}")
                self.loaded_slot_keys.add(slot_key)

            if entries_for_live:
                add_live_entries_for_character(char_key, entries_for_live)

        overlay_scan_data(scan_data)
        if result["applied"] or result["skipped"]:
            print(f"[fd patch] applied={result['applied']} skipped={result['skipped']}")
            for line in result["failures"][:10]:
                print(f"[fd patch] {line}")
        return result


def create_patch_autoload_controller() -> PatchAutoloadController | None:
    paths = discover_patch_files()
    if not paths:
        return None

    patches: list[LoadedPatch] = []
    for path in paths:
        doc = read_patch_document(path, quiet=True)
        if doc is not None:
            patches.append(LoadedPatch(path=path, document=doc))
    if not patches:
        return None

    mode = "none"
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        total_chars = sorted({str(k) for p in patches for k in (p.document.get("characters") or {}).keys()})
        msg = (
            f"Found {len(patches)} frame-data patch file(s) in fd_patches.\n\n"
            f"Characters in patches: {', '.join(total_chars) if total_chars else 'none'}\n\n"
            "Choose Yes to auto-load all matching patch sections when characters are scanned.\n"
            "Choose No to ask per character.\n"
            "Choose Cancel to skip patch loading."
        )
        ans = messagebox.askyesnocancel("Frame-data patches detected", msg, parent=root)
        if ans is True:
            mode = "all"
        elif ans is False:
            mode = "per_character"
        else:
            mode = "none"
        try:
            root.destroy()
        except Exception:
            pass
    except Exception as e:
        print(f"[fd patch] startup prompt failed: {e}")
        mode = "none"

    if mode == "none":
        print(f"[fd patch] found {len(patches)} patch file(s); autoload disabled")
    else:
        print(f"[fd patch] autoload mode: {mode}; files={len(patches)}")
    return PatchAutoloadController(mode=mode, patches=patches)
