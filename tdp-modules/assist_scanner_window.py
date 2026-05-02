from __future__ import annotations

import csv
import os
import struct
import threading
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox

from tk_host import tk_call

try:
    from dolphin_io import rbytes, wbytes
except Exception:
    rbytes = None
    wbytes = None

try:
    from move_id_map import lookup_move_name as _lookup_move_name
except Exception:
    def _lookup_move_name(aid: int) -> str | None:
        return None

SCAN_START = 0x90000000
SCAN_END = 0x94000000
SCAN_BLOCK = 0x40000
OVERLAP = 0x400

_CHR_TBL_BASES = [
    0x90896640,
    0x908F1920,
    0x909478E0,
    0x9099D9C0,
]

# Confirmed Chun assist selector graft.
#
# User-confirmed live patch:
#   Graft this 0x2C-byte Ryu-style selector setup over the active Chun assist
#   block that begins 0x48 bytes before the live Tensho state wrapper.
#
# Original live Chun block:
#   0F060027 3732203F 00000003 3733203F 00000009
#   013C0000 00001610 013C0000 000016C8 013C0000 00001744
#
# Grafted selector block:
#   0F060027 01500060 000044B4 00000003
#   0003BCC8 0003C4AC 0003CC90
#   3732203F 00000002 3733203F 00000004
#
# After install, the selector words are:
#   graft_addr + 0x10
#   graft_addr + 0x14
#   graft_addr + 0x18
CHUN_SELECTOR_ORIGINAL_BLOCK = bytes.fromhex(
    "0F 06 00 27 "
    "37 32 20 3F 00 00 00 03 "
    "37 33 20 3F 00 00 00 09 "
    "01 3C 00 00 00 00 16 10 "
    "01 3C 00 00 00 00 16 C8 "
    "01 3C 00 00 00 00 17 44"
)

CHUN_SELECTOR_GRAFT_BLOCK = bytes.fromhex(
    "0F 06 00 27 "
    "01 50 00 60 00 00 44 B4 00 00 00 03 "
    "00 03 BC C8 00 03 C4 AC 00 03 CC 90 "
    # Ryu-style selector args. The original Chun graft used 3/9; that proved
    # the graft mechanism, but direct chr_tbl move presets behave more like
    # Ryu's native assist selector and need 2/4.
    "37 32 20 3F 00 00 00 02 "
    "37 33 20 3F 00 00 00 04"
)

CHUN_SELECTOR_GRAFT_DELTA_TO_WRAPPER = 0x48
CHUN_SELECTOR_WORD_OFFSETS = (0x10, 0x14, 0x18)

CHUN_SELECTOR_PAYLOADS = [
    ("force all lanes -> 0003D620", bytes.fromhex("00 03 D6 20")),
    ("force all lanes -> 0003BCC8", bytes.fromhex("00 03 BC C8")),
    ("force all lanes -> 0003C4AC", bytes.fromhex("00 03 C4 AC")),
    ("force all lanes -> 0003CC90", bytes.fromhex("00 03 CC 90")),
]

CHUN_SELECTOR_RESET_WORDS = {
    0x10: bytes.fromhex("00 03 BC C8"),
    0x14: bytes.fromhex("00 03 C4 AC"),
    0x18: bytes.fromhex("00 03 CC 90"),
}

CHUN_SELECTOR_WORD_PRESETS = [
    ("Chun payload 0003D620", 0x0003D620),
    ("Chun payload 0003BCC8", 0x0003BCC8),
    ("Chun payload 0003C4AC", 0x0003C4AC),
    ("Chun payload 0003CC90", 0x0003CC90),
]

_FIGHTER_BASES = [
    0x9246B9C0,
    0x927EB9E0,
    0x92B6BA00,
    0x92EEBA20,
]
_CHAR_ID_OFF = 0x14

CHAR_ID_TO_KEY = {
    1: "Ken the Eagle", 2: "Casshan", 3: "Tekkaman", 4: "Polimar",
    5: "Yatterman-1", 6: "Doronjo", 7: "Ippatsuman", 8: "Jun the Swan",
    10: "Karas", 11: "Gold Lightan", 12: "Ryu", 13: "Chun-Li",
    14: "Batsu", 15: "Morrigan", 16: "Alex", 17: "Viewtiful Joe",
    18: "Volnutt", 19: "Roll", 20: "Saki", 21: "Soki", 22: "PTX-40A",
    23: "Yami", 24: "Yami", 25: "Yami", 26: "Tekkaman Blade",
    27: "Joe the Condor", 28: "Yatterman-2", 29: "Zero", 30: "Frank West",
}

# Confirmed live Ryu selector chain:
#   0x908C7680: 00 03 11 0C 00 03 14 6C 00 03 1C DC 37 32 20 3F
# User-confirmed effects:
#   0003110C = Hadouken
#   0003146C = Shoryu
#   00031CDC = Tatsu
RYU_KNOWN_TARGETS = {
    0x0003110C: "Ryu Hadouken",
    0x0003146C: "Ryu Shoryu",
    0x00031CDC: "Ryu Tatsu",
}
RYU_PRESETS = [
    ("Hadouken", 0x0003110C),
    ("Shoryu", 0x0003146C),
    ("Tatsu", 0x00031CDC),
]


# Confirmed Ryu assist selector graft/table.
#
# Ryu already has the full Ryu-style selector setup in the live assist route.
# This exposes it the same way as the Chun graft table, so a lane can be
# double-clicked and written with a preset/manual selector word.
RYU_SELECTOR_GRAFT_BLOCK = bytes.fromhex(
    "0F 06 00 27 "
    "01 50 00 60 00 00 44 B4 00 00 00 03 "
    "00 03 11 0C 00 03 14 6C 00 03 1C DC "
    "37 32 20 3F 00 00 00 02 "
    "37 33 20 3F 00 00 00 04"
)

RYU_SELECTOR_WORD_OFFSETS = (0x10, 0x14, 0x18)

RYU_SELECTOR_RESET_WORDS = {
    0x10: bytes.fromhex("00 03 11 0C"),
    0x14: bytes.fromhex("00 03 14 6C"),
    0x18: bytes.fromhex("00 03 1C DC"),
}

RYU_SELECTOR_WORD_PRESETS = [
    ("Ryu Hadouken 0003110C", 0x0003110C),
    ("Ryu Shoryu 0003146C", 0x0003146C),
    ("Ryu Tatsu 00031CDC", 0x00031CDC),
]


# Generic assist selector graft probing.
#
# This is the experimental path for every other character. It looks for two
# shapes:
#   1) Native Ryu-style selector tables:
#      0F060027 01500060 000044B4 00000003 [3 words] 3732203F ... 3733203F ...
#   2) Chun-style graftable assist blocks:
#      0F060027 3732203F [arg] 3733203F [arg] 013C0000 ...
#
# Double-clicking a generic lane writes a full Ryu-style selector setup first.
# For graft candidates that do not have selector words yet, the chosen manual
# word is applied to all three lanes by default so the install has valid words.
GENERIC_SELECTOR_SETUP_PREFIX = bytes.fromhex(
    "0F 06 00 27 "
    "01 50 00 60 00 00 44 B4 00 00 00 03"
)
GENERIC_SELECTOR_WORD_OFFSETS = (0x10, 0x14, 0x18)
GENERIC_SELECTOR_TABLE_LEN = 0x2C
GENERIC_SELECTOR_DEFAULT_ARG_32 = bytes.fromhex("00 00 00 03")
GENERIC_SELECTOR_DEFAULT_ARG_33 = bytes.fromhex("00 00 00 09")
GENERIC_SELECTOR_MANUAL_PRESETS = [
    ("Chun 0003D620", 0x0003D620),
    ("Chun 0003BCC8", 0x0003BCC8),
    ("Chun 0003C4AC", 0x0003C4AC),
    ("Chun 0003CC90", 0x0003CC90),
    ("Ryu Hadouken 0003110C", 0x0003110C),
    ("Ryu Shoryu 0003146C", 0x0003146C),
    ("Ryu Tatsu 00031CDC", 0x00031CDC),
]

# Move preset harvesting for assist grafts.
#
# These rows use the same basic logic as scan normals all:
#   selector_word = move_abs - chr_tbl_abs
# Double-clicking one writes that selector word into the active graft lanes.
MOVE_PRESET_CHR_TBL_NUM_ENTRIES = 705
MOVE_PRESET_DATA_START_OFF = 0x3600
MOVE_PRESET_SLOT_SIZE = 0x90000
MOVE_PRESET_LOOKAHEAD = 0x80
MOVE_PRESET_ENTRY_OFFSETS = (0x00, 0x10, 0x20, 0x40, 0x70, 0x90)
MOVE_PRESET_ANIM_HDR = bytes.fromhex("04 01 60 00 00 00 01 E8 3F 00 00 00")
MOVE_PRESET_NORMAL_LABELS = {
    0x00: "5A", 0x01: "5B", 0x02: "5C",
    0x03: "2A", 0x04: "2B", 0x05: "2C",
    0x06: "6C", 0x08: "3C",
    0x09: "j.A", 0x0A: "j.B", 0x0B: "j.C",
    0x0E: "6B",
}
MOVE_PRESET_NORMAL_IDS = set(MOVE_PRESET_NORMAL_LABELS.keys())

# Optional assist preset name map.
#
# Do not embed/manual-define the preset names here. The assist picker reads
# move_id_map_charagnostic.csv at runtime and uses its table/index number as
# the preset key. Put the CSV beside this scanner/module, in the current
# working directory, or in ./assets or ./data.
ASSIST_PRESET_NAME_FILE = "move_id_map_charagnostic.csv"
ASSIST_PRESET_NAME_FILE_CANDIDATES = (
    ASSIST_PRESET_NAME_FILE,
    os.path.join("assets", ASSIST_PRESET_NAME_FILE),
    os.path.join("data", ASSIST_PRESET_NAME_FILE),
)

_ASSIST_PRESET_NAME_CACHE: dict[tuple[int, int], tuple[int, str]] | None = None


def _parse_int_loose(text: str) -> int | None:
    s = (text or "").strip().lower()
    if not s:
        return None
    try:
        return int(s, 0)
    except Exception:
        try:
            return int(s, 16)
        except Exception:
            return None


def _clean_preset_name(name: str | None) -> str:
    s = str(name or "").strip()
    while "  " in s:
        s = s.replace("  ", " ")
    return s


def _is_filtered_assist_preset_name(name: str | None) -> bool:
    """Hide noisy non-attack presets from the assist preset picker."""
    s = _clean_preset_name(name).lower()
    if not s:
        return False
    normalized = "".join(ch if ch.isalnum() else " " for ch in s)
    tokens = set(normalized.split())
    return bool(tokens.intersection({"idle", "landing"}))


def _is_named_preset_name(name: str | None) -> bool:
    s = _clean_preset_name(name)
    if not s:
        return False
    if _is_filtered_assist_preset_name(s):
        return False
    low = s.lower()
    if low in ("?", "unknown", "none", "move_????", "anim_--"):
        return False
    if low.startswith("anim_"):
        return False
    if low.startswith("move 0x"):
        return False
    if " / anim 0x" in low:
        return False
    return True


def _norm_csv_key(key: str | None) -> str:
    return "".join(ch for ch in str(key or "").strip().lower() if ch.isalnum())


def _dict_pick(row: dict[str, str], names: tuple[str, ...]) -> str | None:
    wanted = {_norm_csv_key(n) for n in names}
    for key, value in row.items():
        if _norm_csv_key(key) in wanted:
            return value
    return None


def _candidate_assist_csv_paths() -> list[str]:
    search_dirs: list[str] = []
    try:
        search_dirs.append(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        pass
    try:
        search_dirs.append(os.getcwd())
    except Exception:
        pass

    out: list[str] = []
    seen: set[str] = set()
    for folder in search_dirs:
        if not folder:
            continue
        for filename in ASSIST_PRESET_NAME_FILE_CANDIDATES:
            path = filename if os.path.isabs(filename) else os.path.join(folder, filename)
            norm = os.path.normcase(os.path.abspath(path))
            if norm in seen:
                continue
            seen.add(norm)
            out.append(path)
    return out


def _parse_assist_csv_dict_row(row: dict[str, str]) -> tuple[int, int, int, str] | None:
    table_text = _dict_pick(row, (
        "table_index", "table", "entry", "index", "number", "num", "action", "action_id",
        "input", "input_id", "input_number", "move_index", "move_number",
    ))
    anim_text = _dict_pick(row, (
        "anim_id", "animation_id", "anim", "animation", "state_id", "state", "aid",
    ))
    name_text = _dict_pick(row, (
        "name", "move", "move_name", "label", "display", "display_name", "description",
    ))
    char_text = _dict_pick(row, (
        "char_id", "character_id", "cid", "character", "character_num", "character_number",
    ))

    table_index = _parse_int_loose(table_text or "")
    anim_id = _parse_int_loose(anim_text or "")
    char_id = _parse_int_loose(char_text or "")
    name = _clean_preset_name(name_text)
    if table_index is None or anim_id is None or char_id is None or not name:
        return None
    return int(char_id), int(table_index), int(anim_id), name


def _parse_assist_csv_list_row(parts: list[str]) -> tuple[int, int, int, str] | None:
    # Current move_id_map_charagnostic.csv layout used by the project:
    #   table/index number, anim/state id, name, ..., ..., ..., char_id
    # Keep a couple of fallbacks so older local copies still work.
    layouts = (
        (0, 1, 2, 6),  # table, anim, name, char_id
        (0, 1, 2, 3),  # table, anim, name, char_id
        (1, 2, 3, 0),  # char_id, table, anim, name
    )
    for table_i, anim_i, name_i, char_i in layouts:
        if max(table_i, anim_i, name_i, char_i) >= len(parts):
            continue
        table_index = _parse_int_loose(parts[table_i])
        anim_id = _parse_int_loose(parts[anim_i])
        char_id = _parse_int_loose(parts[char_i])
        name = _clean_preset_name(parts[name_i])
        if table_index is None or anim_id is None or char_id is None or not name:
            continue
        return int(char_id), int(table_index), int(anim_id), name
    return None


def _load_assist_preset_name_map() -> dict[tuple[int, int], tuple[int, str]]:
    global _ASSIST_PRESET_NAME_CACHE
    if _ASSIST_PRESET_NAME_CACHE is not None:
        return _ASSIST_PRESET_NAME_CACHE

    out: dict[tuple[int, int], tuple[int, str]] = {}

    for path in _candidate_assist_csv_paths():
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig", errors="ignore", newline="") as f:
                sample = f.read(4096)
                f.seek(0)
                try:
                    dialect = csv.Sniffer().sniff(sample) if sample else csv.excel
                except Exception:
                    dialect = csv.excel

                reader = csv.reader(f, dialect)
                rows = [row for row in reader if row and any(str(cell).strip() for cell in row)]
        except Exception:
            continue

        if not rows:
            continue

        header = rows[0]
        header_norm = {_norm_csv_key(c) for c in header}
        has_header = bool(header_norm & {
            "tableindex", "table", "entry", "number", "animid", "animationid",
            "name", "movename", "charid", "characterid", "cid",
        })

        if has_header:
            for parts in rows[1:]:
                row = {header[i]: parts[i] if i < len(parts) else "" for i in range(len(header))}
                parsed = _parse_assist_csv_dict_row(row)
                if parsed is None:
                    parsed = _parse_assist_csv_list_row(parts)
                if parsed is None:
                    continue
                char_id, table_index, anim_id, name = parsed
                out[(char_id, table_index)] = (anim_id, name)
        else:
            for parts in rows:
                parsed = _parse_assist_csv_list_row(parts)
                if parsed is None:
                    continue
                char_id, table_index, anim_id, name = parsed
                out[(char_id, table_index)] = (anim_id, name)

    _ASSIST_PRESET_NAME_CACHE = out
    return out


def _assist_csv_name(char_id: int | None, table_index: int, aid: int | None = None) -> str | None:
    names = _load_assist_preset_name_map()
    keys: list[tuple[int, int]] = []
    if char_id is not None:
        keys.append((int(char_id), int(table_index)))
    # Universal normals/fallback rows use char id 100 in the CSV.
    keys.append((100, int(table_index)))
    if aid is not None:
        if char_id is not None:
            keys.append((int(char_id), int(aid)))
        keys.append((100, int(aid)))

    for key in keys:
        row = names.get(key)
        if not row:
            continue
        _anim, name = row
        name = _clean_preset_name(name)
        if name:
            return name
    return None


def _lookup_move_name_safe(char_id: int | None, char_name: str | None, value: int | None) -> str | None:
    if value is None:
        return None
    tries: list[tuple] = []
    if char_name:
        tries.append((char_name, int(value)))
    if char_id is not None:
        tries.append((int(value), int(char_id)))
        tries.append((int(char_id), int(value)))
    tries.append((int(value),))

    for args in tries:
        try:
            s = _lookup_move_name(*args)
        except TypeError:
            continue
        except Exception:
            continue
        s = _clean_preset_name(s)
        if s:
            return s
    return None

SELECTOR_TAIL = b"\x37\x32\x20\x3F"
SELECTOR_COMPANION_TAIL = b"\x37\x33\x20\x3F"
STATE_WRAPPER_PREFIX = (
    b"\x34\x04\x00\x20"
    b"\x00\x00\x00\x03"
    b"\x00\x00\x00\x00"
    b"\x04\x01\x02\x3F"
)
STATE_WRAPPER_LEN = 0x16
STATE_ID_OFF = 0x12
STATE_MARKER_OFF = 0x14

# Targeted Tensho descriptor scan. These are the live 0xD0-sized records
# found in MEM2 during Chun assist/Tensho. The animation wrapper at
# 0x90984D52 changes the visual state, but these descriptor records are the
# current best target for X/Y/attach behavior.
TENSHO_DESC_IDS = (0x0112, 0x0113, 0x0114)
TENSHO_DESC_GROUP = 0x011F
TENSHO_DESC_STRIDE = 0xD0
TENSHO_FLOAT_OFFS = (0x34, 0x38, 0x3C, 0x40, 0x44, 0x48)
TENSHO_FLOAT_NAMES = {
    0x34: "f0 maybe X/pos",
    0x38: "f1 maybe Y/pos",
    0x3C: "f2 maybe Z/pos",
    0x40: "f3 maybe rot/vel X",
    0x44: "f4 maybe rot/vel Y",
    0x48: "f5 maybe rot/vel Z",
}

PHRASES: list[tuple[str, bytes]] = [
    ("Cmd 33 03 20 3F", bytes.fromhex("33 03 20 3F")),
    ("Cmd 33 35 20 3F", bytes.fromhex("33 35 20 3F")),
    ("Cmd 33 38 00 20", bytes.fromhex("33 38 00 20")),
    ("Cmd 34 04 00 20", bytes.fromhex("34 04 00 20")),
    ("Cmd 34 3D 00 20", bytes.fromhex("34 3D 00 20")),
    ("Cmd 34 41 00 20", bytes.fromhex("34 41 00 20")),
    ("Cmd 35 0A 00 20", bytes.fromhex("35 0A 00 20")),
    ("Cmd 35 01 20 3F", bytes.fromhex("35 01 20 3F")),
    ("Cmd 35 03 20 3F", bytes.fromhex("35 03 20 3F")),
    ("Cmd 37 32 20 3F", bytes.fromhex("37 32 20 3F")),
    ("Cmd 37 33 20 3F", bytes.fromhex("37 33 20 3F")),
    ("Cmd 04 01 60", bytes.fromhex("04 01 60")),
    ("Cmd 04 17 60", bytes.fromhex("04 17 60")),
]

COLS = [
    ("owner", "Character"),
    ("slot", "CID"),
    ("entry", "Current Assist"),
    ("address", "Selector Address"),
    ("raw", "Raw"),
    ("target", "Target"),
    ("guess", "Action"),
]
COL_IDS = [c[0] for c in COLS]


def _owning_chr_tbl(addr: int) -> int | None:
    best_base = None
    best_dist = None
    for base in _CHR_TBL_BASES:
        if addr < base:
            continue
        dist = addr - base
        if dist > 0x90000:
            continue
        if best_dist is None or dist < best_dist:
            best_base = base
            best_dist = dist
    return best_base


def _slot_index_for_base(base: int | None) -> int | None:
    if base is None:
        return None
    try:
        return _CHR_TBL_BASES.index(base)
    except ValueError:
        return None


def _read_slot_char_ids() -> dict[int, int]:
    result: dict[int, int] = {}
    if rbytes is None:
        return result
    for idx, chr_base in enumerate(_CHR_TBL_BASES):
        fighter_base = _FIGHTER_BASES[idx]
        try:
            b = rbytes(fighter_base + _CHAR_ID_OFF, 4)
            if b and len(b) == 4:
                result[chr_base] = struct.unpack(">I", b)[0]
        except Exception:
            pass
    return result


def _owner_name(addr: int, slot_char_ids: dict[int, int]) -> str:
    base = _owning_chr_tbl(addr)
    if base is None:
        return "?"
    cid = slot_char_ids.get(base)
    name = CHAR_ID_TO_KEY.get(cid, "?") if cid is not None else "?"
    idx = _slot_index_for_base(base)
    slot = f"S{idx}" if idx is not None else "S?"
    return f"{slot} {name} @0x{base:08X}"


def _slot_cid(addr: int, slot_char_ids: dict[int, int]) -> str:
    base = _owning_chr_tbl(addr)
    if base is None:
        return "?"
    cid = slot_char_ids.get(base)
    if cid is None:
        return "?"
    return f"0x{cid:02X}"


def _fmt_context(data: bytes, local: int, radius: int = 20, mark_len: int = 4) -> str:
    start = max(0, local - radius)
    end = min(len(data), local + radius + 24)
    parts: list[str] = []
    for i in range(start, end):
        b = data[i]
        if i == local:
            parts.append(f"[{b:02X}")
        elif i == local + mark_len - 1:
            parts.append(f"{b:02X}]")
        else:
            parts.append(f"{b:02X}")
    return " ".join(parts)


def _u16be(data: bytes, off: int) -> int | None:
    if off < 0 or off + 2 > len(data):
        return None
    return struct.unpack_from(">H", data, off)[0]


def _u32be(data: bytes, off: int) -> int | None:
    if off < 0 or off + 4 > len(data):
        return None
    return struct.unpack_from(">I", data, off)[0]


def _f32be(data: bytes, off: int) -> float | None:
    if off < 0 or off + 4 > len(data):
        return None
    return struct.unpack_from(">f", data, off)[0]


def _fmt_f32(v: float | None) -> str:
    if v is None:
        return "?"
    return f"{v:.6g}"


def _raw4(data: bytes, off: int) -> str:
    if off < 0 or off + 4 > len(data):
        return "?"
    return f"0x{_u32be(data, off):08X}"


def _selector_count(data: bytes, idx: int) -> int:
    if idx < 0 or idx + 16 > len(data):
        return 0
    if data[idx + 12:idx + 16] != SELECTOR_TAIL:
        return 0
    return sum(1 for off in (0, 4, 8) if data[idx + off] == 0x00 and data[idx + off + 1] == 0x03)


def _selector_score(data: bytes, idx: int, strict_count: int) -> int:
    score = 30 + strict_count * 15
    if strict_count == 3:
        score += 10
    if idx + 24 <= len(data) and data[idx + 20:idx + 24] == SELECTOR_COMPANION_TAIL:
        score += 15
    window = data[idx:min(len(data), idx + 0x120)]
    for phrase in (b"\x04\x01\x60\x00", b"\x04\x17\x60\x00", b"\x33\x03\x20\x3F", b"\x34\x3D\x00\x20"):
        if phrase in window:
            score += 5
    return min(score, 100)


def _append_nearby_phrases(data: bytes, base_addr: int, block_idx: int, block_addr: int,
                           slot_char_ids: dict[int, int], hits: list[dict], limit: int = 18) -> None:
    owner = _owner_name(block_addr, slot_char_ids)
    slot = _slot_cid(block_addr, slot_char_ids)
    window_start = max(0, block_idx - 0x80)
    window_end = min(len(data), block_idx + 0x180)
    added = 0
    seen: set[tuple[int, str]] = set()
    window = data[window_start:window_end]
    for name, phrase in PHRASES:
        pos = 0
        while added < limit:
            j = window.find(phrase, pos)
            if j < 0:
                break
            pos = j + 1
            local = window_start + j
            addr = base_addr + local
            key = (addr, name)
            if key in seen:
                continue
            seen.add(key)
            hits.append({
                "kind": "phrase",
                "block": block_addr,
                "addr": addr,
                "owner": owner,
                "slot": slot,
                "entry": name,
                "raw": phrase.hex(" ").upper(),
                "target": f"{addr - block_addr:+#x}",
                "guess": "nearby command phrase",
                "score": 40,
                "ctx": _fmt_context(data, local, mark_len=len(phrase)),
                "editable": True,
                "typ": f"raw{len(phrase)}",
            })
            added += 1


def _append_selector_block(data: bytes, base_addr: int, idx: int, slot_char_ids: dict[int, int],
                           hits: list[dict]) -> None:
    block_addr = base_addr + idx
    owner_base = _owning_chr_tbl(block_addr)
    if owner_base is None:
        return
    cnt = _selector_count(data, idx)
    if cnt < 2:
        return
    owner = _owner_name(block_addr, slot_char_ids)
    slot = _slot_cid(block_addr, slot_char_ids)
    score = _selector_score(data, idx, cnt)
    kind = "selector-chain" if cnt == 3 else "selector-loose"
    guess = "Confirmed Ryu selector shape" if block_addr == 0x908C7680 else "Ryu-like selector candidate"

    hits.append({
        "kind": kind,
        "block": block_addr,
        "addr": block_addr,
        "owner": owner,
        "slot": slot,
        "entry": "block",
        "raw": data[idx:idx + 16].hex(" ").upper(),
        "target": "",
        "guess": guess,
        "score": score,
        "ctx": _fmt_context(data, idx, mark_len=16),
        "editable": False,
        "typ": "raw16",
    })

    for n, off in enumerate((0, 4, 8), start=1):
        raw = _u32be(data, idx + off)
        if raw is None:
            continue
        addr = block_addr + off
        as_local = owner_base + raw
        entry_guess = RYU_KNOWN_TARGETS.get(raw, "")
        if not entry_guess and data[idx + off] == 0x00 and data[idx + off + 1] == 0x03:
            entry_guess = "selector word"
        hits.append({
            "kind": "selector",
            "block": block_addr,
            "addr": addr,
            "owner": owner,
            "slot": slot,
            "entry": f"word {n}",
            "raw": f"0x{raw:08X}",
            "target": f"0x{as_local:08X}" if 0 <= raw <= 0x100000 else "",
            "guess": entry_guess,
            "score": score,
            "ctx": _fmt_context(data, idx + off),
            "editable": True,
            "typ": "u32-selector",
        })




def _flat_selector_raw(words: list[int]) -> str:
    if not words:
        return "?"
    if all(w == words[0] for w in words):
        return f"0x{words[0]:08X}"
    return "default"


def _flat_selector_target(owner_base: int | None, words: list[int]) -> str:
    if owner_base is None or not words:
        return ""
    if not all(w == words[0] for w in words):
        return "default"
    raw = words[0]
    selector_base = _runtime_chr_tbl_base_for_owner_base(owner_base) or owner_base
    if 0 <= raw <= 0x100000:
        return f"0x{selector_base + raw:08X}"
    return ""


def _selector_word_to_move_name(owner_base: int | None, raw_word: int, slot_char_ids: dict[int, int]) -> str | None:
    if owner_base is None:
        return None

    cid = slot_char_ids.get(owner_base)
    char_name = CHAR_ID_TO_KEY.get(cid, "") if cid is not None else ""

    # First handle known Ryu native selector words without needing a chr_tbl pass.
    if cid == 12 and raw_word in RYU_KNOWN_TARGETS:
        return RYU_KNOWN_TARGETS.get(raw_word)

    chr_tbl_base = _runtime_chr_tbl_base_for_owner_base(owner_base)
    if chr_tbl_base is None:
        return None

    data = _read_mem_region_raw(chr_tbl_base, MOVE_PRESET_SLOT_SIZE)
    if not data:
        return None

    best: tuple[int, str] | None = None
    for table_index in range(MOVE_PRESET_CHR_TBL_NUM_ENTRIES - 1):
        off = table_index * 4
        entry = _u32be(data, off)
        if entry is None:
            continue
        if entry in (0, 0xFFFFFFFF):
            continue
        if entry % 4 != 0 or entry < MOVE_PRESET_DATA_START_OFF:
            continue
        if entry >= len(data):
            continue

        for delta in MOVE_PRESET_ENTRY_OFFSETS:
            if (entry + delta) != raw_word:
                continue
            aid = _move_preset_anim_id_after_hdr(data, entry)
            name, _source, is_named = _resolve_move_preset_entry_name(table_index, aid, cid, char_name)
            if not name:
                continue
            suffix = "" if delta == 0 else f" +0x{delta:02X}"
            # Prefer named exact matches, then named offset matches, then fallback exact.
            rank = 0
            if is_named and delta == 0:
                rank = 0
            elif is_named:
                rank = 1
            elif delta == 0:
                rank = 2
            else:
                rank = 3
            candidate = (rank, f"{name}{suffix}")
            if best is None or candidate[0] < best[0]:
                best = candidate

    return best[1] if best else None


def _flat_selector_entry_label(owner_base: int | None, words: list[int], slot_char_ids: dict[int, int], fallback: str) -> str:
    if not words:
        return fallback
    names = []
    for w in words:
        nm = _selector_word_to_move_name(owner_base, w, slot_char_ids)
        names.append(nm or f"0x{w:08X}")
    if all(n == names[0] for n in names):
        return names[0]
    return "default"

def _append_chun_selector_graft_table(data: bytes, base_addr: int, idx: int,
                                      slot_char_ids: dict[int, int], hits: list[dict],
                                      source: str) -> None:
    block_addr = base_addr + idx
    owner_base = _owning_chr_tbl(block_addr)
    if owner_base is None:
        return
    if idx < 0 or idx + len(CHUN_SELECTOR_GRAFT_BLOCK) > len(data):
        return

    owner = _owner_name(block_addr, slot_char_ids)
    slot = _slot_cid(block_addr, slot_char_ids)
    current_block = data[idx:idx + len(CHUN_SELECTOR_GRAFT_BLOCK)]

    words: list[int] = []
    for off in CHUN_SELECTOR_WORD_OFFSETS:
        raw = _u32be(data, idx + off)
        default_raw = _u32be(CHUN_SELECTOR_GRAFT_BLOCK, off)
        if raw is None or default_raw is None:
            continue
        # If this is the untouched original Chun block, show the selector words
        # that will be installed by the graft. Installed/fallback rows show live bytes.
        words.append(raw if source != "original" else default_raw)

    if len(words) != len(CHUN_SELECTOR_WORD_OFFSETS):
        return

    first_lane_addr = block_addr + CHUN_SELECTOR_WORD_OFFSETS[0]
    entry = _flat_selector_entry_label(owner_base, words, slot_char_ids, "Chun assist")
    raw_txt = _flat_selector_raw(words)
    target_txt = _flat_selector_target(owner_base, words)

    hits.append({
        "kind": "chun-selector-word",
        "block": block_addr,
        "addr": first_lane_addr,
        "owner": owner,
        "slot": slot,
        "entry": entry,
        "raw": raw_txt,
        "target": target_txt,
        "guess": f"double-click to change Chun assist; table 0x{block_addr:08X}; source {source}",
        "score": 100,
        "ctx": _fmt_context(data, idx, mark_len=len(CHUN_SELECTOR_GRAFT_BLOCK)),
        "editable": True,
        "typ": "u32-chun-selector",
        "source": source,
        "selector_words": words,
        "table_raw": current_block.hex(" ").upper(),
    })

def _scan_chun_selector_graft_tables(data: bytes, base_addr: int,
                                     slot_char_ids: dict[int, int], hits: list[dict]) -> None:
    patterns = [
        ("graft", CHUN_SELECTOR_GRAFT_BLOCK),
        ("original", CHUN_SELECTOR_ORIGINAL_BLOCK),
    ]
    wrapper_sig = STATE_WRAPPER_PREFIX + b"\x00\x00\x01\x12\x01\x3C"
    seen: set[int] = set()

    for source, pattern in patterns:
        pos = 0
        while True:
            idx = data.find(pattern, pos)
            if idx < 0:
                break
            pos = idx + 1
            if idx in seen:
                continue
            if _slot_cid(base_addr + idx, slot_char_ids) != "0x0D":
                continue
            seen.add(idx)
            _append_chun_selector_graft_table(data, base_addr, idx, slot_char_ids, hits, source)

    # Fallback for a graft whose selector words have already been edited: find the
    # live 0112 wrapper and step back to the selector table start.
    pos = 0
    while True:
        wrapper_idx = data.find(wrapper_sig, pos)
        if wrapper_idx < 0:
            break
        pos = wrapper_idx + 1
        idx = wrapper_idx - CHUN_SELECTOR_GRAFT_DELTA_TO_WRAPPER
        if idx < 0 or idx + len(CHUN_SELECTOR_GRAFT_BLOCK) > len(data):
            continue
        if data[idx:idx + 4] != b"\x0F\x06\x00\x27":
            continue
        if idx in seen:
            continue
        if _slot_cid(base_addr + idx, slot_char_ids) != "0x0D":
            continue
        seen.add(idx)
        _append_chun_selector_graft_table(data, base_addr, idx, slot_char_ids, hits, "wrapper-fallback")


def _looks_like_ryu_selector_graft_shape(data: bytes, idx: int) -> bool:
    if idx < 0 or idx + len(RYU_SELECTOR_GRAFT_BLOCK) > len(data):
        return False
    checks = [
        (0x00, bytes.fromhex("0F 06 00 27")),
        (0x04, bytes.fromhex("01 50 00 60")),
        (0x08, bytes.fromhex("00 00 44 B4")),
        (0x0C, bytes.fromhex("00 00 00 03")),
        (0x1C, bytes.fromhex("37 32 20 3F")),
        (0x20, bytes.fromhex("00 00 00 02")),
        (0x24, bytes.fromhex("37 33 20 3F")),
        (0x28, bytes.fromhex("00 00 00 04")),
    ]
    return all(data[idx + off:idx + off + len(sig)] == sig for off, sig in checks)


def _append_ryu_selector_graft_table(data: bytes, base_addr: int, idx: int,
                                     slot_char_ids: dict[int, int], hits: list[dict],
                                     source: str) -> None:
    block_addr = base_addr + idx
    owner_base = _owning_chr_tbl(block_addr)
    if owner_base is None:
        return
    if idx < 0 or idx + len(RYU_SELECTOR_GRAFT_BLOCK) > len(data):
        return

    owner = _owner_name(block_addr, slot_char_ids)
    slot = _slot_cid(block_addr, slot_char_ids)
    current_block = data[idx:idx + len(RYU_SELECTOR_GRAFT_BLOCK)]

    words: list[int] = []
    for off in RYU_SELECTOR_WORD_OFFSETS:
        raw = _u32be(data, idx + off)
        if raw is None:
            continue
        words.append(raw)

    if len(words) != len(RYU_SELECTOR_WORD_OFFSETS):
        return

    first_lane_addr = block_addr + RYU_SELECTOR_WORD_OFFSETS[0]
    entry = _flat_selector_entry_label(owner_base, words, slot_char_ids, "Ryu assist")
    raw_txt = _flat_selector_raw(words)
    target_txt = _flat_selector_target(owner_base, words)

    hits.append({
        "kind": "ryu-selector-word",
        "block": block_addr,
        "addr": first_lane_addr,
        "owner": owner,
        "slot": slot,
        "entry": entry,
        "raw": raw_txt,
        "target": target_txt,
        "guess": f"double-click to change Ryu assist; table 0x{block_addr:08X}; source {source}",
        "score": 100,
        "ctx": _fmt_context(data, idx, mark_len=len(RYU_SELECTOR_GRAFT_BLOCK)),
        "editable": True,
        "typ": "u32-ryu-selector-graft",
        "source": source,
        "selector_words": words,
        "table_raw": current_block.hex(" ").upper(),
    })

def _scan_ryu_selector_graft_tables(data: bytes, base_addr: int,
                                    slot_char_ids: dict[int, int], hits: list[dict]) -> None:
    seen: set[int] = set()

    pos = 0
    while True:
        idx = data.find(RYU_SELECTOR_GRAFT_BLOCK, pos)
        if idx < 0:
            break
        pos = idx + 1
        if idx in seen:
            continue
        if _slot_cid(base_addr + idx, slot_char_ids) != "0x0C":
            continue
        seen.add(idx)
        _append_ryu_selector_graft_table(data, base_addr, idx, slot_char_ids, hits, "graft")

    # Fallback for already-mutated selector words: match the fixed setup/tail
    # bytes and ignore the three lane dwords.
    pos = 0
    while True:
        idx = data.find(b"\x0F\x06\x00\x27", pos)
        if idx < 0:
            break
        pos = idx + 1
        if idx in seen:
            continue
        if _slot_cid(base_addr + idx, slot_char_ids) != "0x0C":
            continue
        if not _looks_like_ryu_selector_graft_shape(data, idx):
            continue
        seen.add(idx)
        _append_ryu_selector_graft_table(data, base_addr, idx, slot_char_ids, hits, "shape-fallback")



def _is_known_chun_or_ryu_slot(addr: int, slot_char_ids: dict[int, int]) -> bool:
    base = _owning_chr_tbl(addr)
    if base is None:
        return False
    return slot_char_ids.get(base) in (12, 13)


def _looks_like_generic_native_selector_shape(data: bytes, idx: int) -> bool:
    if idx < 0 or idx + GENERIC_SELECTOR_TABLE_LEN > len(data):
        return False
    if data[idx:idx + 0x10] != GENERIC_SELECTOR_SETUP_PREFIX:
        return False
    if data[idx + 0x1C:idx + 0x20] != SELECTOR_TAIL:
        return False
    if data[idx + 0x24:idx + 0x28] != SELECTOR_COMPANION_TAIL:
        return False

    # Require three plausible local selector words. Most known words are slot-local
    # offsets and sit below the 0x90000 character window.
    for off in GENERIC_SELECTOR_WORD_OFFSETS:
        raw = _u32be(data, idx + off)
        if raw is None or raw <= 0 or raw > 0x90000:
            return False
    return True


def _looks_like_generic_graft_candidate_shape(data: bytes, idx: int) -> bool:
    if idx < 0 or idx + GENERIC_SELECTOR_TABLE_LEN > len(data):
        return False
    if data[idx:idx + 4] != b"\x0F\x06\x00\x27":
        return False
    if data[idx + 4:idx + 8] != SELECTOR_TAIL:
        return False
    if data[idx + 0x0C:idx + 0x10] != SELECTOR_COMPANION_TAIL:
        return False

    # Chun's confirmed graft site had three 01 3C rows after 37 33. Keep the
    # requirement soft enough for other characters: at least two 01 3C rows in
    # the next 0x28 bytes.
    rows = 0
    for off in (0x14, 0x1C, 0x24):
        if data[idx + off:idx + off + 4] == b"\x01\x3C\x00\x00":
            rows += 1
    return rows >= 2


def _build_generic_selector_graft_block(words: tuple[int, int, int], arg32: bytes | None = None,
                                        arg33: bytes | None = None) -> bytes:
    if arg32 is None or len(arg32) != 4:
        arg32 = GENERIC_SELECTOR_DEFAULT_ARG_32
    if arg33 is None or len(arg33) != 4:
        arg33 = GENERIC_SELECTOR_DEFAULT_ARG_33
    out = bytearray()
    out.extend(GENERIC_SELECTOR_SETUP_PREFIX)
    for word in words:
        out.extend(struct.pack(">I", word & 0xFFFFFFFF))
    out.extend(SELECTOR_TAIL)
    out.extend(arg32)
    out.extend(SELECTOR_COMPANION_TAIL)
    out.extend(arg33)
    return bytes(out)


def _append_generic_selector_table(data: bytes, base_addr: int, idx: int,
                                   slot_char_ids: dict[int, int], hits: list[dict],
                                   source: str) -> None:
    block_addr = base_addr + idx
    owner_base = _owning_chr_tbl(block_addr)
    if owner_base is None:
        return
    if idx < 0 or idx + GENERIC_SELECTOR_TABLE_LEN > len(data):
        return

    owner = _owner_name(block_addr, slot_char_ids)
    slot = _slot_cid(block_addr, slot_char_ids)
    current_block = data[idx:idx + GENERIC_SELECTOR_TABLE_LEN]
    is_native = source in ("native", "installed")
    label = "native/installed selector" if is_native else "graft candidate"
    kind = "generic-native-table" if is_native else "generic-graft-table"
    score = 85 if is_native else 75
    if slot not in ("?", "0x0C", "0x0D"):
        score += 10

    hits.append({
        "kind": kind,
        "block": block_addr,
        "addr": block_addr,
        "owner": owner,
        "slot": slot,
        "entry": label,
        "raw": current_block.hex(" ").upper(),
        "target": "",
        "guess": "experimental generic selector candidate; double-click a lane to install/update with preset or manual word",
        "score": score,
        "ctx": _fmt_context(data, idx, mark_len=GENERIC_SELECTOR_TABLE_LEN),
        "editable": False,
        "typ": "raw-window",
        "source": source,
    })

    for lane_index, off in enumerate(GENERIC_SELECTOR_WORD_OFFSETS, start=1):
        lane_addr = block_addr + off
        raw = _u32be(data, idx + off) if is_native else None
        display_raw = raw if raw is not None else 0
        target_addr = owner_base + display_raw if 0 < display_raw <= 0x100000 else 0
        note = "native words present" if is_native else "no words yet; chosen word installs graft"
        hits.append({
            "kind": "generic-selector-word",
            "block": block_addr,
            "addr": lane_addr,
            "owner": owner,
            "slot": slot,
            "entry": f"lane {lane_index}",
            "raw": f"0x{display_raw:08X}",
            "target": f"0x{target_addr:08X}" if target_addr else "",
            "guess": f"double-click: generic selector word; {note}; source {source}",
            "score": score,
            "ctx": _fmt_context(data, idx + off, mark_len=4),
            "editable": True,
            "typ": "u32-generic-selector",
            "source": source,
        })


def _scan_generic_selector_candidates(data: bytes, base_addr: int,
                                      slot_char_ids: dict[int, int], hits: list[dict]) -> None:
    seen: set[int] = set()

    # Native/installed full selector tables for non-Ryu/non-Chun characters.
    pos = 0
    while True:
        idx = data.find(b"\x0F\x06\x00\x27", pos)
        if idx < 0:
            break
        pos = idx + 1
        if idx in seen:
            continue
        block_addr = base_addr + idx
        if _is_known_chun_or_ryu_slot(block_addr, slot_char_ids):
            continue
        if _looks_like_generic_native_selector_shape(data, idx):
            seen.add(idx)
            _append_generic_selector_table(data, base_addr, idx, slot_char_ids, hits, "native")
            continue
        if _looks_like_generic_graft_candidate_shape(data, idx):
            seen.add(idx)
            _append_generic_selector_table(data, base_addr, idx, slot_char_ids, hits, "graft-candidate")


def _read_mem_region_raw(start_addr: int, size: int) -> bytes:
    if rbytes is None:
        return b""
    out = bytearray()
    off = 0
    while off < size:
        n = min(SCAN_BLOCK, size - off)
        try:
            chunk = rbytes(start_addr + off, n) or b""
        except Exception:
            return b""
        if len(chunk) != n:
            return b""
        out.extend(chunk)
        off += n
    return bytes(out)


def _looks_like_chr_tbl_at(data: bytes, idx: int) -> bool:
    """Validate a chr_tbl start inside a local memory blob."""
    if idx < 0 or idx + 0xB0C > len(data):
        return False
    try:
        if _u32be(data, idx) != 0x3600:
            return False
        if _u32be(data, idx + 0xB00) != 0xFFFFFFFF:
            return False
    except Exception:
        return False
    return data[idx + 0xB04:idx + 0xB0C] == b"chr_act\n"


def _find_runtime_chr_tbl_base_for_region(region_base: int) -> int | None:
    """Find the actual live chr_tbl base inside one broad character slot region.

    _CHR_TBL_BASES are stable ownership windows, but the actual chr_tbl
    can sit deeper in the region after character load. Selector words are
    relative to this actual chr_tbl base, not always the ownership window.
    """
    if rbytes is None:
        return None

    # Include bytes before region_base because a chr_tbl label lives at base-0x18
    # when the table starts exactly at the region base.
    next_bases = [b for b in _CHR_TBL_BASES if b > region_base]
    slot_end = min(region_base + MOVE_PRESET_SLOT_SIZE, min(next_bases) if next_bases else region_base + MOVE_PRESET_SLOT_SIZE)

    scan_start = max(SCAN_START, region_base - 0x40)
    scan_size = max(0, slot_end - scan_start)
    data = _read_mem_region_raw(scan_start, scan_size)
    if not data:
        return None

    label = b"chr_tbl\n"
    pos = 0
    candidates: list[int] = []
    while True:
        j = data.find(label, pos)
        if j < 0:
            break
        pos = j + 1
        tbl_idx = j + 0x18
        if not _looks_like_chr_tbl_at(data, tbl_idx):
            continue
        cand = scan_start + tbl_idx
        if region_base - 0x100 <= cand < slot_end:
            candidates.append(cand)

    if not candidates:
        # Very old/static layouts can still have the table exactly at the
        # ownership base with no readable label in this pass.
        base_idx = region_base - scan_start
        if _looks_like_chr_tbl_at(data, base_idx):
            return region_base
        return None

    # Prefer the first validated table in this ownership window.
    candidates.sort()
    return candidates[0]


def _find_runtime_chr_tbl_bases_for_region(region_base: int) -> list[int]:
    """Find every plausible live chr_tbl base inside one broad character slot.

    Some loaded slots expose more than one valid chr_tbl-shaped table, and some
    slots do not satisfy the strict chr_tbl label path during assist state. The
    preset picker only needs valid chr_tbl entry offsets, so collect every
    validated candidate and let the picker harvest from all of them.
    """
    if rbytes is None:
        return []

    next_bases = [b for b in _CHR_TBL_BASES if b > region_base]
    slot_end = min(region_base + MOVE_PRESET_SLOT_SIZE, min(next_bases) if next_bases else region_base + MOVE_PRESET_SLOT_SIZE)

    scan_start = max(SCAN_START, region_base - 0x40)
    scan_size = max(0, slot_end - scan_start)
    data = _read_mem_region_raw(scan_start, scan_size)
    if not data:
        return []

    candidates: list[int] = []

    def add_candidate(tbl_idx: int) -> None:
        if not _looks_like_chr_tbl_at(data, tbl_idx):
            return
        cand = scan_start + tbl_idx
        if region_base - 0x100 <= cand < slot_end:
            candidates.append(cand)

    # Preferred path: the normal chr_tbl label sits 0x18 bytes before the table.
    label = b"chr_tbl\n"
    pos = 0
    while True:
        j = data.find(label, pos)
        if j < 0:
            break
        pos = j + 1
        add_candidate(j + 0x18)

    # Backup path: scan the slot for the actual table shape, not just the label.
    # This fixes cases where the selector lane is live but the label path misses
    # the loaded chr_tbl for that slot.
    scan_limit = len(data) - 0xB0C
    for tbl_idx in range(0, max(0, scan_limit), 4):
        try:
            if _u32be(data, tbl_idx) != MOVE_PRESET_DATA_START_OFF:
                continue
            if _u32be(data, tbl_idx + 0xB00) != 0xFFFFFFFF:
                continue
            if data[tbl_idx + 0xB04:tbl_idx + 0xB0C] != b"chr_act\n":
                continue
        except Exception:
            continue
        add_candidate(tbl_idx)

    # Very old/static layouts can still have the table exactly at the ownership
    # base with no readable label in this pass.
    add_candidate(region_base - scan_start)

    out: list[int] = []
    seen: set[int] = set()
    for cand in sorted(candidates):
        if cand in seen:
            continue
        seen.add(cand)
        out.append(cand)
    return out


def _runtime_chr_tbl_base_for_owner_base(owner_base: int | None) -> int | None:
    if owner_base is None:
        return None
    bases = _find_runtime_chr_tbl_bases_for_region(owner_base)
    return bases[0] if bases else None


def _selector_base_for_address(addr: int) -> int | None:
    owner_base = _owning_chr_tbl(addr)
    if owner_base is None:
        return None
    return _runtime_chr_tbl_base_for_owner_base(owner_base)


def _move_preset_anim_id_after_hdr(data: bytes, off: int) -> int | None:
    if off < 0 or off >= len(data):
        return None
    end = min(off + MOVE_PRESET_LOOKAHEAD, len(data) - 4 + 1)
    start = off
    for p in range(start, end):
        if p + len(MOVE_PRESET_ANIM_HDR) <= len(data) and data[p:p + len(MOVE_PRESET_ANIM_HDR)] == MOVE_PRESET_ANIM_HDR:
            q0 = p + len(MOVE_PRESET_ANIM_HDR)
            q1 = min(q0 + MOVE_PRESET_LOOKAHEAD, len(data) - 4 + 1)
            for q in range(q0, q1):
                op = data[q + 2]
                fps = data[q + 3]
                if fps == 0x3C and op in (0x01, 0x04):
                    aid = (data[q] << 8) | data[q + 1]
                    if 1 <= aid <= 0x0500:
                        return aid
        op = data[p + 2]
        fps = data[p + 3]
        if fps == 0x3C and op in (0x01, 0x04):
            aid = (data[p] << 8) | data[p + 1]
            if 1 <= aid <= 0x0500:
                return aid
    return None


def _move_preset_name(aid: int | None, char_id: int | None = None, char_name: str | None = None) -> str:
    if aid is None:
        return "move_????"
    name = _lookup_move_name_safe(char_id, char_name, aid)
    if name:
        return name
    low = aid & 0xFF
    if low in MOVE_PRESET_NORMAL_LABELS:
        return MOVE_PRESET_NORMAL_LABELS[low]
    return f"anim_{aid:04X}"


def _append_assist_move_preset_row(base: int, move_abs: int, aid: int | None,
                                   source: str, slot_char_ids: dict[int, int],
                                   data: bytes, hits: list[dict]) -> None:
    cid = slot_char_ids.get(base)
    char_name = CHAR_ID_TO_KEY.get(cid, "") if cid is not None else ""
    owner = _owner_name(move_abs, slot_char_ids)
    slot = _slot_cid(move_abs, slot_char_ids)
    word = move_abs - base
    if word <= 0 or word > 0x100000:
        return
    name, name_source, is_named = _resolve_move_preset_entry_name(aid or 0, aid, cid, char_name)
    if _is_filtered_assist_preset_name(name):
        return
    low = (aid & 0xFF) if aid is not None else None
    is_normal = aid is not None and aid >= 0x0100 and low in MOVE_PRESET_NORMAL_IDS
    kind_label = "normal" if is_normal else "move"
    local = move_abs - base
    score = 88 if kind_label == "normal" else 72
    if is_named:
        score += 10
    if cid in (12, 13):
        score += 8
    raw = f"0x{word:08X}"
    hits.append({
        "kind": "assist-move-preset",
        "block": move_abs,
        "addr": move_abs,
        "owner": owner,
        "slot": slot,
        "entry": name,
        "raw": raw,
        "target": f"0x{move_abs:08X}",
        "guess": f"double-click: point graft lanes to {name}; base word from {source}",
        "score": score,
        "ctx": _fmt_context(data, local, mark_len=4) if 0 <= local < len(data) else "",
        "editable": True,
        "typ": "assist-move-preset",
        "selector_word": word,
        "move_abs": move_abs,
        "move_id": aid if aid is not None else 0,
        "table_index": aid if aid is not None else 0,
        "move_name": name,
        "move_named": is_named,
        "name_source": name_source,
        "owner_base": base,
        "char_id": cid if cid is not None else 0,
        "source": source,
    })


def _resolve_move_preset_entry_name(table_index: int, aid: int | None,
                                    char_id: int | None = None,
                                    char_name: str | None = None) -> tuple[str, str, bool]:
    """Return display name, source, and whether it is a real named preset."""
    csv_name = _assist_csv_name(char_id, table_index, aid)
    if csv_name:
        return csv_name, "csv", True

    # Global universal normals are table-indexed as 0x100 + normal id.
    if 0x100 <= table_index <= 0x10F:
        low = table_index & 0xFF
        if low in MOVE_PRESET_NORMAL_LABELS:
            return MOVE_PRESET_NORMAL_LABELS[low], "normal", True

    # Try the project move map with character context. This matches how the
    # frame-data window resolves pretty names through move_id_map/fd_utils.
    mapped = _lookup_move_name_safe(char_id, char_name, table_index)
    if mapped and _is_named_preset_name(mapped):
        return mapped, "move_id_map", True

    if aid is not None:
        mapped = _lookup_move_name_safe(char_id, char_name, aid)
        if mapped and _is_named_preset_name(mapped):
            return mapped, "move_id_map", True

        aid_name = _move_preset_name(aid, char_id, char_name)
        if _is_named_preset_name(aid_name):
            return aid_name, "anim", True
        return f"move 0x{table_index:03X} / anim 0x{aid:04X}", "fallback", False

    return f"move 0x{table_index:03X}", "fallback", False


def _move_preset_entry_name(table_index: int, aid: int | None,
                            char_id: int | None = None,
                            char_name: str | None = None) -> str:
    return _resolve_move_preset_entry_name(table_index, aid, char_id, char_name)[0]


def _is_preset_worthy_table_index(table_index: int, aid: int | None, char_id: int | None = None) -> bool:
    """Keep attack/move rows, not idle/block/KO/system action spam."""
    # Filter out the noisy 369-400 band from assist presets entirely.
    if 369 <= table_index <= 400:
        return False

    char_name = CHAR_ID_TO_KEY.get(char_id, "") if char_id is not None else ""
    _name, _source, is_named = _resolve_move_preset_entry_name(table_index, aid, char_id, char_name)
    if _is_filtered_assist_preset_name(_name):
        return False
    if is_named:
        return True

    # User-facing assist preset bands. Keep unnamed rows too, but the picker
    # pushes unnamed rows to the bottom instead of the priority sections.
    if 304 <= table_index <= 368:
        return True
    if 510 <= table_index <= 520:
        return True
    if 256 <= table_index <= 270:
        return True

    # Keep the broader known move-ish table ranges after the priority bands so
    # oddball character entries still show in the picker, but below named rows.
    if 0x100 <= table_index <= 0x10F:
        return True
    if 0x130 <= table_index <= 0x18F:
        return True
    if 0x200 <= table_index <= 0x230:
        return True

    if 0x1C0 <= table_index <= 0x1FF:
        return aid is not None and aid >= 0x0100

    if aid is not None and aid >= 0x0100:
        low = aid & 0xFF
        if low in MOVE_PRESET_NORMAL_IDS:
            return True
        name = _move_preset_name(aid, char_id, char_name)
        if _is_named_preset_name(name):
            return True

    return False


def _scan_assist_move_presets(slot_char_ids: dict[int, int], hits: list[dict]) -> None:
    # Dedicated full-slot pass. Do this outside the 0x40000 MEM2 chunk scan so
    # chr_tbl entries can resolve into the whole character script region.
    #
    # Critical correction: _CHR_TBL_BASES are broad ownership windows, not
    # always the actual live chr_tbl base. The selector word must be relative
    # to the actual live chr_tbl base found by the chr_tbl/chr_act table shape.
    # Harvest all validated table candidates in the slot so one missed label
    # does not make the preset picker empty.
    seen: set[tuple[int, int]] = set()
    for owner_base in _CHR_TBL_BASES:
        cid = slot_char_ids.get(owner_base)
        if cid is None:
            continue

        chr_tbl_bases = _find_runtime_chr_tbl_bases_for_region(owner_base)
        if not chr_tbl_bases:
            continue

        for chr_tbl_base in chr_tbl_bases:
            data = _read_mem_region_raw(chr_tbl_base, MOVE_PRESET_SLOT_SIZE)
            if not data:
                continue

            for table_index in range(MOVE_PRESET_CHR_TBL_NUM_ENTRIES - 1):
                off = table_index * 4
                if off + 4 > len(data):
                    break
                entry = _u32be(data, off)
                if entry is None:
                    continue
                if entry in (0, 0xFFFFFFFF):
                    continue
                if entry % 4 != 0 or entry < MOVE_PRESET_DATA_START_OFF:
                    continue
                if entry >= len(data):
                    continue

                aid = _move_preset_anim_id_after_hdr(data, entry)
                if not _is_preset_worthy_table_index(table_index, aid, cid):
                    continue

                move_abs = chr_tbl_base + entry
                key = (chr_tbl_base, table_index)
                if key in seen:
                    continue
                seen.add(key)

                char_name = CHAR_ID_TO_KEY.get(cid, "") if cid is not None else ""
                name, name_source, is_named = _resolve_move_preset_entry_name(table_index, aid, cid, char_name)
                if _is_filtered_assist_preset_name(name):
                    continue
                low = table_index & 0xFF
                is_normal = 0x100 <= table_index <= 0x10F and low in MOVE_PRESET_NORMAL_IDS
                kind_label = "normal" if is_normal else "move"
                owner = _owner_name(move_abs, slot_char_ids)
                slot = _slot_cid(move_abs, slot_char_ids)
                score = 95 if is_normal else 80
                if is_named:
                    score += 10
                if cid in (12, 13):
                    score += 5

                hits.append({
                    "kind": "assist-move-preset",
                    "block": move_abs,
                    "addr": move_abs,
                    "owner": owner,
                    "slot": slot,
                    "entry": name,
                    "raw": f"0x{entry:08X}",
                    "target": f"0x{move_abs:08X}",
                    "guess": f"point graft lanes to {name}; chr_tbl[{table_index}] @ 0x{chr_tbl_base:08X}",
                    "score": score,
                    "ctx": _fmt_context(data, entry, mark_len=4) if 0 <= entry < len(data) else "",
                    "editable": True,
                    "typ": "assist-move-preset",
                    "selector_word": entry,
                    "move_abs": move_abs,
                    "move_id": aid if aid is not None else table_index,
                    "table_index": table_index,
                    "move_name": name,
                    "move_named": is_named,
                    "name_source": name_source,
                    # owner_base is the broad slot window used by graft lanes.
                    # chr_tbl_base is the real selector base used by the VM.
                    "owner_base": owner_base,
                    "chr_tbl_base": chr_tbl_base,
                    "char_id": cid if cid is not None else 0,
                    "source": f"chr_tbl+{name_source}",
                })

def _append_state_wrapper(data: bytes, base_addr: int, idx: int, slot_char_ids: dict[int, int],
                          hits: list[dict]) -> None:
    block_addr = base_addr + idx
    owner_base = _owning_chr_tbl(block_addr)
    if owner_base is None:
        return
    if idx + STATE_WRAPPER_LEN > len(data):
        return
    if data[idx:idx + len(STATE_WRAPPER_PREFIX)] != STATE_WRAPPER_PREFIX:
        return
    if data[idx + STATE_MARKER_OFF:idx + STATE_MARKER_OFF + 2] != b"\x01\x3C":
        return

    state_id = _u16be(data, idx + STATE_ID_OFF)
    if state_id is None:
        return
    owner = _owner_name(block_addr, slot_char_ids)
    slot = _slot_cid(block_addr, slot_char_ids)
    score = 85
    if state_id in (0x01A1, 0x01A8, 0x01AE, 0x0112):
        score = 95

    hits.append({
        "kind": "state-wrapper",
        "block": block_addr,
        "addr": block_addr,
        "owner": owner,
        "slot": slot,
        "entry": "block",
        "raw": data[idx:idx + STATE_WRAPPER_LEN].hex(" ").upper(),
        "target": "",
        "guess": "assist/state-call wrapper candidate",
        "score": score,
        "ctx": _fmt_context(data, idx, mark_len=STATE_WRAPPER_LEN),
        "editable": False,
        "typ": f"raw{STATE_WRAPPER_LEN}",
    })
    hits.append({
        "kind": "state-id",
        "block": block_addr,
        "addr": block_addr + STATE_ID_OFF,
        "owner": owner,
        "slot": slot,
        "entry": "StateID",
        "raw": f"0x{state_id:04X}",
        "target": "",
        "guess": "editable state call",
        "score": score,
        "ctx": _fmt_context(data, idx + STATE_ID_OFF, mark_len=2),
        "editable": True,
        "typ": "u16-state",
    })



def _append_confirmed_wrapper_neighborhood(data: bytes, base_addr: int, block_addr: int, state_addr: int,
                                           state_id: int, label: str, slot_char_ids: dict[int, int],
                                           hits: list[dict]) -> None:
    """Focused editable poke zone around a known live state wrapper."""
    window_before = 0x30
    window_after = 0x70

    start_addr = block_addr - window_before
    end_addr = block_addr + window_after
    data_start = base_addr
    data_end = base_addr + len(data)
    if start_addr < data_start or end_addr > data_end:
        return

    block_idx = block_addr - base_addr
    state_idx = state_addr - base_addr
    owner = _owner_name(block_addr, slot_char_ids)
    slot = _slot_cid(block_addr, slot_char_ids)
    blob = data[start_addr - base_addr:end_addr - base_addr]

    hits.append({
        "kind": "confirmed-wrapper",
        "block": block_addr,
        "addr": block_addr,
        "owner": owner,
        "slot": slot,
        "entry": label,
        "raw": blob.hex(" ").upper(),
        "target": "",
        "guess": f"CONFIRMED live wrapper neighborhood, StateID 0x{state_id:04X}",
        "score": 100,
        "ctx": _fmt_context(data, block_idx, mark_len=STATE_WRAPPER_LEN),
        "editable": False,
        "typ": "raw-window",
    })

    hits.append({
        "kind": "confirmed-state-id",
        "block": block_addr,
        "addr": state_addr,
        "owner": owner,
        "slot": slot,
        "entry": "StateID",
        "raw": f"0x{state_id:04X}",
        "target": "",
        "guess": "known live animation/state call; changes Chun anim, not X/Y translation",
        "score": 100,
        "ctx": _fmt_context(data, state_idx, mark_len=2),
        "editable": True,
        "typ": "u16-state",
    })

    poke_points = [
        (block_addr - 0x10, 4, "pre -0x10 dword"),
        (block_addr - 0x0C, 4, "pre -0x0C dword"),
        (block_addr - 0x08, 4, "pre -0x08 dword"),
        (block_addr - 0x04, 4, "pre -0x04 dword"),
        (block_addr - 0x0A, 2, "pre -0x0A u16"),
        (block_addr - 0x06, 2, "pre -0x06 u16"),
        (block_addr - 0x04, 2, "pre -0x04 u16"),
        (block_addr - 0x02, 2, "pre -0x02 u16"),
        (block_addr + 0x34, 4, "post +0x34 cmd"),
        (block_addr + 0x3C, 4, "post +0x3C cmd"),
        (block_addr + 0x40, 4, "post +0x40 cmd"),
        (block_addr + 0x4C, 4, "post +0x4C cmd"),
    ]

    for addr, size, name in poke_points:
        if addr < data_start or addr + size > data_end:
            continue
        idx = addr - base_addr
        raw = data[idx:idx + size].hex(" ").upper()
        hits.append({
            "kind": "wrapper-poke",
            "block": block_addr,
            "addr": addr,
            "owner": owner,
            "slot": slot,
            "entry": name,
            "raw": raw,
            "target": f"{addr - block_addr:+#x}",
            "guess": "focused poke near confirmed Chun live wrapper",
            "score": 100,
            "ctx": _fmt_context(data, idx, mark_len=size),
            "editable": True,
            "typ": f"raw{size}",
        })

def _looks_like_tensho_descriptor(data: bytes, idx: int) -> bool:
    if idx < 0 or idx + TENSHO_DESC_STRIDE > len(data):
        return False
    desc_id = _u32be(data, idx)
    group = _u32be(data, idx + 0x04)
    if desc_id not in TENSHO_DESC_IDS or group != TENSHO_DESC_GROUP:
        return False

    # Strong shape checks from the observed records, but keep them soft enough
    # that an active match does not get rejected if one metadata field mutates.
    score = 0
    for off in (0x10, 0x14, 0x18):
        if data[idx + off:idx + off + 4] == b"\x3F\x80\x00\x00":
            score += 1
    if _u32be(data, idx + 0x54) == TENSHO_DESC_STRIDE:
        score += 1
    if _u32be(data, idx + 0xC0) == TENSHO_DESC_STRIDE:
        score += 1
    if _u32be(data, idx + 0xCC) is not None:
        score += 1
    return score >= 4


def _append_tensho_descriptor(data: bytes, base_addr: int, idx: int, slot_char_ids: dict[int, int],
                              hits: list[dict]) -> None:
    block_addr = base_addr + idx
    desc_id = _u32be(data, idx)
    group = _u32be(data, idx + 0x04)
    if desc_id is None or group is None:
        return

    local_index = _u32be(data, idx + 0xCC)
    size_a = _u32be(data, idx + 0x54)
    size_b = _u32be(data, idx + 0xC0)
    prev_delta = _u32be(data, idx + 0x58)
    owner = _owner_name(block_addr, slot_char_ids)
    slot = _slot_cid(block_addr, slot_char_ids)

    score = 100 if desc_id in TENSHO_DESC_IDS and group == TENSHO_DESC_GROUP else 80
    hits.append({
        "kind": "tensho-desc",
        "block": block_addr,
        "addr": block_addr,
        "owner": owner,
        "slot": slot,
        "entry": f"desc 0x{desc_id:04X}",
        "raw": data[idx:idx + 0x60].hex(" ").upper(),
        "target": f"idx {local_index}" if local_index is not None else "",
        "guess": f"Tensho 0xD0 descriptor, group 0x{group:04X}, sizes {size_a}/{size_b}, prev 0x{prev_delta or 0:08X}",
        "score": score,
        "ctx": _fmt_context(data, idx, mark_len=16),
        "editable": False,
        "typ": "raw-window",
    })

    for off in TENSHO_FLOAT_OFFS:
        v = _f32be(data, idx + off)
        raw = data[idx + off:idx + off + 4].hex(" ").upper()
        hits.append({
            "kind": "tensho-float",
            "block": block_addr,
            "addr": block_addr + off,
            "owner": owner,
            "slot": slot,
            "entry": f"+0x{off:02X} {TENSHO_FLOAT_NAMES.get(off, 'float')}",
            "raw": _fmt_f32(v),
            "target": raw,
            "guess": "editable big-endian f32; likely attach/position candidate",
            "score": score,
            "ctx": _fmt_context(data, idx + off, mark_len=4),
            "editable": True,
            "typ": "f32",
        })

    # Also expose the ID/group fields as raw u32 in case the route binds by descriptor ID.
    for off, name in ((0x00, "DescID"), (0x04, "GroupID"), (0x54, "SizeA"), (0xC0, "SizeB"), (0xCC, "LocalIndex")):
        raw_val = _u32be(data, idx + off)
        if raw_val is None:
            continue
        hits.append({
            "kind": "tensho-meta",
            "block": block_addr,
            "addr": block_addr + off,
            "owner": owner,
            "slot": slot,
            "entry": f"+0x{off:02X} {name}",
            "raw": f"0x{raw_val:08X}",
            "target": f"{block_addr + off - block_addr:+#x}",
            "guess": "descriptor metadata, edit only after float tests",
            "score": score - 5,
            "ctx": _fmt_context(data, idx + off, mark_len=4),
            "editable": True if off in (0x00, 0x04) else False,
            "typ": "raw4",
        })


def _scan_tensho_descriptors(data: bytes, base_addr: int, slot_char_ids: dict[int, int], hits: list[dict]) -> None:
    # Scan for 0x0112/0x0113/0x0114 records with shared group 0x011F.
    seen_starts: set[int] = set()
    for desc_id in TENSHO_DESC_IDS:
        sig = struct.pack(">II", desc_id, TENSHO_DESC_GROUP)
        pos = 0
        while True:
            idx = data.find(sig, pos)
            if idx < 0:
                break
            pos = idx + 1
            if idx in seen_starts:
                continue
            if not _looks_like_tensho_descriptor(data, idx):
                continue
            seen_starts.add(idx)
            _append_tensho_descriptor(data, base_addr, idx, slot_char_ids, hits)


def _scan_block(data: bytes, base_addr: int, slot_char_ids: dict[int, int], hits: list[dict]) -> None:
    # Keep the main table focused: only confirmed graft tables and their lanes.
    # Move/normal presets are harvested on demand from the lane chooser.
    _scan_chun_selector_graft_tables(data, base_addr, slot_char_ids, hits)
    _scan_ryu_selector_graft_tables(data, base_addr, slot_char_ids, hits)


def _run_scan(progress_cb, done_cb):
    if rbytes is None:
        done_cb([])
        return
    slot_char_ids = _read_slot_char_ids()
    hits: list[dict] = []
    total = SCAN_END - SCAN_START
    addr = SCAN_START
    prev_tail = b""
    prev_base = addr
    while addr < SCAN_END:
        sz = min(SCAN_BLOCK, SCAN_END - addr)
        try:
            chunk = rbytes(addr, sz) or b""
        except Exception:
            chunk = b""
        if chunk:
            if prev_tail:
                data = prev_tail + chunk
                scan_base = prev_base
            else:
                data = chunk
                scan_base = addr
            _scan_block(data, scan_base, slot_char_ids, hits)
            keep = min(OVERLAP, len(chunk))
            prev_tail = chunk[-keep:]
            prev_base = addr + sz - keep
        progress_cb((addr - SCAN_START + sz) / total * 100.0)
        addr += sz

    # Move presets are harvested on demand from the selector-lane chooser.

    seen = set()
    uniq = []
    for h in hits:
        k = (h["kind"], h["addr"], h["block"], h["entry"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(h)

    def sort_key(h: dict):
        priority = 0 if h["block"] == 0x908C7680 else 1
        kind_order = {
            "ryu-selector-word": 0,
            "chun-selector-word": 1,
            "chun-graft-table": 2,
            "ryu-graft-table": 3,
            "generic-native-table": 4,
            "generic-graft-table": 5,
            "generic-selector-word": 6,
            "assist-move-preset": 7,
            "selector-chain": 8,
            "selector-loose": 5,
            "selector": 6,
            "confirmed-wrapper": 7,
            "confirmed-state-id": 8,
            "wrapper-poke": 9,
            "state-wrapper": 10,
            "state-id": 11,
            "phrase": 12,
        }.get(h["kind"], 13)
        return (priority, h["block"], kind_order, h["addr"], h["entry"])

    uniq.sort(key=sort_key)
    done_cb(uniq)


class AssistScannerWindow:
    def __init__(self, master):
        self.root = tk.Toplevel(master)
        self.root.title("Assist Scanner - Active Assist Picker")
        self.root.geometry("1420x700")
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self._scanning = False
        self._hit_by_iid: dict[str, dict] = {}
        self._sort_col = None
        self._sort_asc = True
        self._chun_selector_test_index = 0
        self._last_chun_selector_addr = None
        self._last_chun_selector_test = None
        self._build()
        self._start()

    def _build(self):
        top = ttk.Frame(self.root)
        top.pack(side="top", fill="x", padx=8, pady=6)
        ttk.Label(
            top,
            text=(
                "Shows one active assist selector per loaded Ryu/Chun table. "
                "Double-click a row to choose a preset assist or custom raw U32."
            ),
        ).pack(side="left")
        self._scan_btn = ttk.Button(top, text="Rescan", command=self._start)
        self._scan_btn.pack(side="right")

        self._route_restore_btn = ttk.Button(top, text="Restore Chun Original", command=self._restore_chun_selector_block)
        self._route_restore_btn.pack(side="right", padx=(0, 8))

        self._ryu_restore_btn = ttk.Button(top, text="Restore Ryu Selector", command=self._restore_ryu_selector_block)
        self._ryu_restore_btn.pack(side="right", padx=(0, 8))

        self._dump_slots_btn = ttk.Button(top, text="Dump Char Slots", command=self._dump_char_slots)
        self._dump_slots_btn.pack(side="right", padx=(0, 8))

        self._prog = tk.DoubleVar()
        ttk.Progressbar(self.root, variable=self._prog, maximum=100).pack(fill="x", padx=8, pady=(0, 4))
        self._status = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self._status, anchor="w").pack(fill="x", padx=8)

        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=8, pady=6)
        self._tree = ttk.Treeview(frame, columns=COL_IDS, show="headings", height=28)
        widths = {
            "owner": 230, "slot": 60, "entry": 300,
            "address": 130, "raw": 240, "target": 130, "guess": 420,
        }
        for col_id, header in COLS:
            self._tree.heading(col_id, text=header, command=lambda c=col_id: self._sort_by(c))
            self._tree.column(col_id, width=widths.get(col_id, 80), anchor="center")
        for c in ("owner", "entry", "guess"):
            if c in COL_IDS:
                self._tree.column(c, anchor="w")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        self._tree.bind("<Double-Button-1>", self._on_double_click)
        self._tree.bind("<Button-3>", self._on_right_click)

    def _start(self):
        if self._scanning:
            return
        self._scanning = True
        self._scan_btn.config(state="disabled")
        self._prog.set(0)
        self._hit_by_iid.clear()
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        self._status.set("Scanning MEM2 for active assist selectors...")
        threading.Thread(target=_run_scan, args=(self._on_prog, self._on_done), daemon=True).start()

    def _on_prog(self, pct: float):
        try:
            self.root.after(0, lambda: self._prog.set(pct))
        except Exception:
            pass

    def _on_done(self, hits: list[dict]):
        def _f():
            for h in hits:
                def _val(col_id: str) -> str:
                    if col_id == "block":
                        return f"0x{h['block']:08X}"
                    if col_id == "address":
                        return f"0x{h['addr']:08X}"
                    return str(h.get(col_id, ""))
                iid = self._tree.insert("", "end", values=tuple(_val(c) for c in COL_IDS))
                self._hit_by_iid[iid] = h
            self._scanning = False
            self._scan_btn.config(state="normal")
            self._prog.set(100)
            chun_rows = len([h for h in hits if h["kind"] == "chun-selector-word"])
            ryu_rows = len([h for h in hits if h["kind"] == "ryu-selector-word"])
            self._status.set(
                f"Done - {ryu_rows} Ryu active selector(s), {chun_rows} Chun active selector(s). Double-click a row to change assist."
            )
        try:
            self.root.after(0, _f)
        except Exception:
            pass

    def _sort_by(self, col_id: str):
        if self._sort_col == col_id:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col_id
            self._sort_asc = True
        headers = dict(COLS)
        for cid, header in COLS:
            arrow = (" up" if self._sort_asc else " down") if cid == col_id else ""
            self._tree.heading(cid, text=header + arrow)
        items = [(self._tree.set(iid, col_id), iid) for iid in self._tree.get_children("")]

        def key(v):
            s = str(v)
            try:
                if s.startswith("0x"):
                    return (0, int(s, 16))
                if s.startswith("+") or s.startswith("-"):
                    return (0, int(s, 0))
                return (0, float(s))
            except Exception:
                return (1, s.lower())

        items.sort(key=lambda x: key(x[0]), reverse=not self._sort_asc)
        for n, (_, iid) in enumerate(items):
            self._tree.move(iid, "", n)

    def _col_index(self, event) -> int:
        col = self._tree.identify_column(event.x)
        return int(col[1:]) - 1 if col else -1

    def _choose_ryu_preset_or_manual(self, addr: int, current: str) -> int | None:
        result: dict[str, int | None] = {"value": None}
        dlg = tk.Toplevel(self.root)
        dlg.title("Choose Ryu Selector")
        dlg.geometry("380x260")
        dlg.transient(self.root)
        dlg.grab_set()
        ttk.Label(
            dlg,
            text=(
                f"Address: 0x{addr:08X}\n"
                f"Current: {current}\n\n"
                "Choose a confirmed Ryu preset, or Manual for a raw U32."
            ),
            justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 8))
        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill="x", padx=12, pady=4)

        def set_value(v: int):
            result["value"] = v
            dlg.destroy()

        for label, value in RYU_PRESETS:
            ttk.Button(btn_frame, text=f"{label}  0x{value:08X}", command=lambda v=value: set_value(v)).pack(fill="x", pady=3)

        def manual():
            text = simpledialog.askstring(
                "Manual selector",
                "Enter raw U32 value. Examples:\n"
                "0x0003110C = Hadouken\n"
                "0x0003146C = Shoryu\n"
                "0x00031CDC = Tatsu",
                parent=dlg,
                initialvalue=current,
            )
            if text is None:
                return
            try:
                cleaned = text.strip().replace(" ", "")
                value = int(cleaned, 16) if cleaned.lower().startswith("0x") else int(cleaned, 16)
            except ValueError:
                messagebox.showerror("Invalid", f"{text!r} is not a u32 value.", parent=dlg)
                return
            if not (0 <= value <= 0xFFFFFFFF):
                messagebox.showerror("Out of range", "Value must be 0-0xFFFFFFFF.", parent=dlg)
                return
            set_value(value)

        ttk.Button(btn_frame, text="Manual raw U32", command=manual).pack(fill="x", pady=(10, 3))
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(fill="x", pady=3)
        self.root.wait_window(dlg)
        return result["value"]

    def _manual_value(self, h: dict) -> bytes | None:
        typ = str(h.get("typ", ""))
        addr = int(h["addr"])
        current = str(h["raw"])
        if typ == "u16-state":
            text = simpledialog.askstring(
                "Edit StateID",
                f"Address: 0x{addr:08X}\nCurrent: {current}\n\nNew state ID u16:",
                parent=self.root,
                initialvalue=current,
            )
            if text is None:
                return None
            try:
                val = int(text.strip(), 16) if text.strip().lower().startswith("0x") else int(text.strip(), 16)
            except ValueError:
                messagebox.showerror("Invalid", f"{text!r} is not a u16 value.", parent=self.root)
                return None
            if not (0 <= val <= 0xFFFF):
                messagebox.showerror("Out of range", "Value must be 0-0xFFFF.", parent=self.root)
                return None
            return struct.pack(">H", val)
        if typ == "f32":
            text = simpledialog.askstring(
                "Edit float",
                f"Address: 0x{addr:08X}\nCurrent: {current}\n\n"
                "New big-endian f32 value. Use decimal like 1.25 or hex bytes like 0x3FA00000:",
                parent=self.root,
                initialvalue=current,
            )
            if text is None:
                return None
            cleaned = text.strip().replace(" ", "")
            try:
                if cleaned.lower().startswith("0x") and len(cleaned) == 10:
                    return bytes.fromhex(cleaned[2:])
                val = float(cleaned)
            except ValueError:
                messagebox.showerror("Invalid", f"{text!r} is not a float or raw f32 hex.", parent=self.root)
                return None
            return struct.pack(">f", val)
        if typ.startswith("raw"):
            n = int(typ[3:] or "4")
            text = simpledialog.askstring(
                "Edit raw bytes",
                f"Address: 0x{addr:08X}\nCurrent: {current}\nType: {typ}\n\nNew exact bytes:",
                parent=self.root,
                initialvalue=current,
            )
            if text is None:
                return None
            try:
                cleaned = text.replace(" ", "").replace("_", "")
                if cleaned.lower().startswith("0x"):
                    cleaned = cleaned[2:]
                payload = bytes.fromhex(cleaned)
            except ValueError:
                messagebox.showerror("Invalid", f"{text!r} is not valid hex bytes.", parent=self.root)
                return None
            if len(payload) != n:
                messagebox.showerror("Wrong length", f"Expected exactly {n} byte(s).", parent=self.root)
                return None
            return payload
        return None

    def _write_many(self, writes: dict[int, bytes], label: str) -> bool:
        if wbytes is None:
            messagebox.showerror("Write failed", "dolphin_io.wbytes unavailable.", parent=self.root)
            return False
        failures = []
        for addr, payload in writes.items():
            try:
                ok = bool(wbytes(addr, payload))
            except Exception as e:
                failures.append(f"0x{addr:08X}: {e}")
                continue
            if not ok:
                failures.append(f"0x{addr:08X}: write returned false")
        if failures:
            messagebox.showerror("Write failed", "\n".join(failures), parent=self.root)
            return False
        self._status.set(label)
        return True

    def _dump_char_slots(self):
        if rbytes is None:
            messagebox.showerror("Dump failed", "dolphin_io.rbytes unavailable.", parent=self.root)
            return

        import os
        import time

        def read_region(start_addr: int, size: int, label: str) -> bytes:
            out = bytearray()
            off = 0
            while off < size:
                n = min(SCAN_BLOCK, size - off)
                addr = start_addr + off
                try:
                    chunk = rbytes(addr, n)
                except Exception as e:
                    raise RuntimeError(f"{label}: read failed at 0x{addr:08X}: {e}") from e

                if not chunk:
                    raise RuntimeError(f"{label}: empty read at 0x{addr:08X}")

                if len(chunk) != n:
                    raise RuntimeError(
                        f"{label}: short read at 0x{addr:08X}; got 0x{len(chunk):X}, expected 0x{n:X}"
                    )

                out.extend(chunk)
                off += n

                try:
                    self._status.set(f"Dumping {label}: 0x{off:06X}/0x{size:06X}")
                    self.root.update_idletasks()
                except Exception:
                    pass

            return bytes(out)

        try:
            bases = list(_CHR_TBL_BASES)
            if not bases:
                messagebox.showerror("Dump failed", "_CHR_TBL_BASES is empty.", parent=self.root)
                return

            # Fixed raw window per character-table slot. This intentionally captures
            # more than the currently known selector area so you can search it elsewhere.
            slot_size = 0x90000

            stamp = time.strftime("%Y%m%d_%H%M%S")
            out_dir = os.path.join(os.getcwd(), f"assist_char_slot_dump_{stamp}")
            os.makedirs(out_dir, exist_ok=True)

            index_lines = [
                "Assist Scanner character slot raw dump",
                f"Created: {stamp}",
                f"Slot dump size: 0x{slot_size:08X} bytes",
                "",
                "Individual slot dumps:",
            ]

            for i, base in enumerate(bases):
                label = f"slot {i} @ 0x{base:08X}"
                data = read_region(base, slot_size, label)
                filename = f"char_slot_{i}_base_0x{base:08X}_size_0x{slot_size:08X}.bin"
                path = os.path.join(out_dir, filename)
                with open(path, "wb") as f:
                    f.write(data)
                index_lines.append(
                    f"slot {i}: base=0x{base:08X}, size=0x{slot_size:08X}, file={filename}"
                )

            combined_start = min(bases)
            combined_end = max(bases) + slot_size
            combined_size = combined_end - combined_start
            combined_label = f"combined 0x{combined_start:08X}-0x{combined_end - 1:08X}"
            combined_data = read_region(combined_start, combined_size, combined_label)
            combined_filename = (
                f"char_slots_combined_0x{combined_start:08X}_"
                f"to_0x{combined_end - 1:08X}_size_0x{combined_size:08X}.bin"
            )
            combined_path = os.path.join(out_dir, combined_filename)
            with open(combined_path, "wb") as f:
                f.write(combined_data)

            index_lines.extend([
                "",
                "Combined contiguous dump:",
                f"start=0x{combined_start:08X}",
                f"end=0x{combined_end - 1:08X}",
                f"size=0x{combined_size:08X}",
                f"file={combined_filename}",
                "",
                "Known slot bases:",
                *[f"0x{base:08X}" for base in bases],
                "",
            ])

            index_path = os.path.join(out_dir, "README_dump_index.txt")
            with open(index_path, "w", encoding="utf-8") as f:
                f.write("\n".join(index_lines))

            self._status.set(f"Dump complete: {out_dir}")
            messagebox.showinfo(
                "Dump complete",
                (
                    f"Dumped {len(bases)} character slot window(s) plus one combined raw dump.\n\n"
                    f"Saved to:\n{out_dir}"
                ),
                parent=self.root,
            )

        except Exception as e:
            self._status.set("Dump failed")
            messagebox.showerror("Dump failed", str(e), parent=self.root)

    def _read_memory_region(self, start_addr: int, size: int, label: str = "region") -> bytes:
        if rbytes is None:
            raise RuntimeError("dolphin_io.rbytes unavailable.")

        out = bytearray()
        off = 0
        while off < size:
            n = min(SCAN_BLOCK, size - off)
            addr = start_addr + off
            try:
                chunk = rbytes(addr, n)
            except Exception as e:
                raise RuntimeError(f"{label}: read failed at 0x{addr:08X}: {e}") from e

            if not chunk:
                raise RuntimeError(f"{label}: empty read at 0x{addr:08X}")

            if len(chunk) != n:
                raise RuntimeError(
                    f"{label}: short read at 0x{addr:08X}; got 0x{len(chunk):X}, expected 0x{n:X}"
                )

            out.extend(chunk)
            off += n

        return bytes(out)

    def _chun_selector_candidate_score(self, base: int, idx: int, data: bytes,
                                       slot_char_ids: dict[int, int], source: str) -> int:
        addr = base + idx
        score = 0

        cid = slot_char_ids.get(base)
        if cid == 13:
            score += 100
        elif cid is not None:
            score -= 100

        if source == "graft":
            score += 40
        elif source == "original":
            score += 30
        else:
            score += 10

        wrapper_idx = idx + CHUN_SELECTOR_GRAFT_DELTA_TO_WRAPPER
        wrapper_sig = STATE_WRAPPER_PREFIX + b"\x00\x00\x01\x12\x01\x3C"
        if 0 <= wrapper_idx and wrapper_idx + len(wrapper_sig) <= len(data):
            if data[wrapper_idx:wrapper_idx + len(wrapper_sig)] == wrapper_sig:
                score += 80

        # Prefer candidates that have the confirmed pre-wrapper setup nearby.
        if idx >= 0 and idx + 0x50 <= len(data):
            window = data[idx:idx + 0x50]
            if b"\x04\x17\x60\x00" in window:
                score += 10
            if b"\x04\x01\x60\x00" in window:
                score += 10

        # Small deterministic tie-breaker: lower slot/base first, then lower address.
        score -= (_slot_index_for_base(base) or 0)
        score -= (addr & 0xFF) // 0x10
        return score

    def _find_chun_selector_graft_addr(self) -> tuple[int, str]:
        if rbytes is None:
            raise RuntimeError("dolphin_io.rbytes unavailable.")

        slot_char_ids = _read_slot_char_ids()
        patterns = [
            ("graft", CHUN_SELECTOR_GRAFT_BLOCK),
            ("original", CHUN_SELECTOR_ORIGINAL_BLOCK),
        ]
        wrapper_sig = STATE_WRAPPER_PREFIX + b"\x00\x00\x01\x12\x01\x3C"

        candidates: list[tuple[int, int, str]] = []

        for base in _CHR_TBL_BASES:
            try:
                data = self._read_memory_region(base, 0x90000, f"slot @ 0x{base:08X}")
            except Exception:
                continue

            for source, pattern in patterns:
                pos = 0
                while True:
                    idx = data.find(pattern, pos)
                    if idx < 0:
                        break
                    pos = idx + 1
                    score = self._chun_selector_candidate_score(base, idx, data, slot_char_ids, source)
                    candidates.append((score, base + idx, source))

            # Fallback for already-mutated grafts: find the live 0112 wrapper and
            # step back 0x48 to the selector slot.
            pos = 0
            while True:
                wrapper_idx = data.find(wrapper_sig, pos)
                if wrapper_idx < 0:
                    break
                pos = wrapper_idx + 1
                idx = wrapper_idx - CHUN_SELECTOR_GRAFT_DELTA_TO_WRAPPER
                if idx < 0 or idx + len(CHUN_SELECTOR_GRAFT_BLOCK) > len(data):
                    continue
                if data[idx:idx + 4] != b"\x0F\x06\x00\x27":
                    continue
                score = self._chun_selector_candidate_score(base, idx, data, slot_char_ids, "wrapper-fallback")
                candidates.append((score, base + idx, "wrapper-fallback"))

        if not candidates:
            raise RuntimeError(
                "Could not find the Chun assist selector slot. Load Chun, call the assist once, then try again."
            )

        candidates.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        _, addr, source = candidates[0]
        return addr, source

    def _install_chun_selector_graft(self):
        try:
            addr, source = self._find_chun_selector_graft_addr()
        except Exception as e:
            self._status.set("Chun selector install failed")
            messagebox.showerror("Install Chun Selector failed", str(e), parent=self.root)
            return

        ok = self._write_many(
            {addr: CHUN_SELECTOR_GRAFT_BLOCK},
            f"Installed Chun selector graft at 0x{addr:08X}."
        )
        if not ok:
            return

        self._last_chun_selector_addr = addr
        selector_addrs = [addr + off for off in CHUN_SELECTOR_WORD_OFFSETS]
        messagebox.showinfo(
            "Chun selector installed",
            (
                f"Installed graft at 0x{addr:08X} using {source} match.\n\n"
                f"Selector lanes:\n"
                f"0x{selector_addrs[0]:08X}\n"
                f"0x{selector_addrs[1]:08X}\n"
                f"0x{selector_addrs[2]:08X}\n\n"
                "Use Next Chun Payload to force all three lanes."
            ),
            parent=self.root,
        )

    def _restore_chun_selector_block(self):
        try:
            addr, source = self._find_chun_selector_graft_addr()
        except Exception as e:
            self._status.set("Chun selector restore failed")
            messagebox.showerror("Restore Chun Selector failed", str(e), parent=self.root)
            return

        ok = self._write_many(
            {addr: CHUN_SELECTOR_ORIGINAL_BLOCK},
            f"Restored original Chun assist block at 0x{addr:08X}."
        )
        if not ok:
            return

        self._last_chun_selector_addr = addr
        self._last_chun_selector_test = None
        messagebox.showinfo(
            "Chun selector restored",
            f"Restored original block at 0x{addr:08X} using {source} match.",
            parent=self.root,
        )

    def _apply_next_chun_selector_payload(self):
        if not CHUN_SELECTOR_PAYLOADS:
            return

        try:
            addr, source = self._find_chun_selector_graft_addr()
        except Exception as e:
            self._status.set("Chun payload apply failed")
            messagebox.showerror("Next Chun Payload failed", str(e), parent=self.root)
            return

        # Ensure the confirmed graft is installed first. This is harmless if it is
        # already installed, and it avoids applying lane words to the original block.
        writes: dict[int, bytes] = {addr: CHUN_SELECTOR_GRAFT_BLOCK}

        name, payload = CHUN_SELECTOR_PAYLOADS[self._chun_selector_test_index]
        for off in CHUN_SELECTOR_WORD_OFFSETS:
            writes[addr + off] = payload

        ok = self._write_many(
            writes,
            f"Applied Chun selector payload {payload.hex(' ').upper()} at 0x{addr:08X}."
        )
        if not ok:
            return

        self._last_chun_selector_addr = addr
        self._last_chun_selector_test = name
        self._chun_selector_test_index = (self._chun_selector_test_index + 1) % len(CHUN_SELECTOR_PAYLOADS)

        details = "\n".join(
            f"0x{addr + off:08X} <- {payload.hex(' ').upper()}"
            for off in CHUN_SELECTOR_WORD_OFFSETS
        )
        messagebox.showinfo(
            "Chun selector payload applied",
            (
                f"{name}\n\n"
                f"Graft address: 0x{addr:08X} ({source})\n\n"
                f"{details}\n\n"
                "Test Chun assist now. Press Next Chun Payload for the next forced route."
            ),
            parent=self.root,
        )

    def _collect_move_preset_rows_for_base(self, owner_base: int) -> list[dict]:
        """Harvest move/normal presets for one loaded character slot without showing rows."""
        if owner_base is None:
            return []
        slot_char_ids = _read_slot_char_ids()
        temp_hits: list[dict] = []
        try:
            _scan_assist_move_presets(slot_char_ids, temp_hits)
        except Exception:
            return []

        rows = [
            h for h in temp_hits
            if int(h.get("owner_base", 0)) == int(owner_base)
            and not _is_filtered_assist_preset_name(str(h.get("move_name", "")))
        ]

        def band_priority(table_index: int, named: bool) -> int:
            # Named rows keep the requested assist order. Unnamed rows go to
            # the bottom, even if their table index is inside a priority band.
            if not named:
                return 4
            if 304 <= table_index <= 368:
                return 0
            if 510 <= table_index <= 520:
                return 1
            if 256 <= table_index <= 270:
                return 2
            return 3

        def row_key(row: dict) -> tuple:
            table_index = int(row.get("table_index", 0))
            name = str(row.get("move_name", ""))
            word = int(row.get("selector_word", 0))
            named = bool(row.get("move_named")) and _is_named_preset_name(name)
            return (band_priority(table_index, named), table_index, name.lower(), word)

        return sorted(rows, key=row_key)

    def _choose_loaded_move_preset_word(self, owner_base: int, parent: tk.Toplevel) -> tuple[int, str] | None:
        rows = self._collect_move_preset_rows_for_base(owner_base)
        if not rows:
            messagebox.showerror(
                "Move presets unavailable",
                f"No move/normal presets were harvested for slot base 0x{owner_base:08X}.",
                parent=parent,
            )
            return None

        result: dict[str, object] = {"value": None, "label": ""}
        dlg = tk.Toplevel(parent)
        dlg.title("Choose loaded move preset")
        dlg.geometry("760x520")
        dlg.transient(parent)
        dlg.grab_set()

        top = ttk.Frame(dlg)
        top.pack(fill="x", padx=10, pady=(10, 6))
        ttk.Label(
            top,
            text=(
                f"Slot base: 0x{owner_base:08X}\n"
                "Pick a loaded move/normal. Named presets are ordered: 304-368, then 510-520, then 256-270, then named leftovers. Entries 369-400, idle, and landing are filtered out. Unnamed entries are at the bottom."
            ),
            justify="left",
        ).pack(anchor="w")

        controls = ttk.Frame(dlg)
        controls.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Label(controls, text="Filter:").pack(side="left")
        filter_var = tk.StringVar()
        filter_entry = ttk.Entry(controls, textvariable=filter_var, width=28)
        filter_entry.pack(side="left", padx=(4, 12))
        ttk.Label(controls, text="Entry offset:").pack(side="left")
        offset_var = tk.StringVar(value="+0x00")
        offset_box = ttk.Combobox(
            controls,
            textvariable=offset_var,
            values=[f"+0x{delta:02X}" for delta in MOVE_PRESET_ENTRY_OFFSETS],
            width=8,
            state="readonly",
        )
        offset_box.pack(side="left")

        frame = ttk.Frame(dlg)
        frame.pack(fill="both", expand=True, padx=10, pady=(0, 8))
        cols = ("name", "table", "id", "word", "addr", "source")
        tree = ttk.Treeview(frame, columns=cols, show="headings", height=16)
        tree.heading("name", text="Move")
        tree.heading("table", text="Table")
        tree.heading("id", text="AnimID")
        tree.heading("word", text="Base Word")
        tree.heading("addr", text="Move Address")
        tree.heading("source", text="Source")
        tree.column("name", width=250, anchor="w")
        tree.column("table", width=70, anchor="center")
        tree.column("id", width=80, anchor="center")
        tree.column("word", width=105, anchor="center")
        tree.column("addr", width=115, anchor="center")
        tree.column("source", width=90, anchor="center")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        row_by_iid: dict[str, dict] = {}

        def populate() -> None:
            needle = filter_var.get().strip().lower()
            for iid in tree.get_children():
                tree.delete(iid)
            row_by_iid.clear()
            for row in rows:
                name = str(row.get("move_name", "move"))
                aid = int(row.get("move_id", 0))
                word = int(row.get("selector_word", 0))
                move_abs = int(row.get("move_abs", row.get("addr", 0)))
                source = str(row.get("source", ""))
                table_index = int(row.get("table_index", 0))
                named = bool(row.get("move_named")) and _is_named_preset_name(name)
                display_name = name if named else f"{name} (unnamed)"
                hay = f"{name} {aid:04X} {table_index:03X} {word:08X} {source}".lower()
                if needle and needle not in hay:
                    continue
                iid = tree.insert(
                    "",
                    "end",
                    values=(
                        display_name,
                        f"{table_index}",
                        f"0x{aid:04X}" if aid else "?",
                        f"0x{word:08X}",
                        f"0x{move_abs:08X}",
                        source,
                    ),
                )
                row_by_iid[iid] = row

        def parse_offset() -> int:
            text = offset_var.get().strip().replace("+", "")
            try:
                return int(text, 16)
            except Exception:
                return 0

        def choose_selected() -> None:
            sel = tree.selection()
            if not sel:
                messagebox.showerror("No move selected", "Select a move preset first.", parent=dlg)
                return
            row = row_by_iid.get(sel[0])
            if not row:
                return
            delta = parse_offset()
            base_word = int(row.get("selector_word", 0))
            value = (base_word + delta) & 0xFFFFFFFF
            name = str(row.get("move_name", "move"))
            result["value"] = value
            result["label"] = f"{name} +0x{delta:02X}"
            dlg.destroy()

        def on_filter(*_args) -> None:
            populate()

        filter_var.trace_add("write", on_filter)
        tree.bind("<Double-Button-1>", lambda _event: choose_selected())
        populate()
        first = tree.get_children()
        if first:
            tree.selection_set(first[0])
            tree.focus(first[0])

        bottom = ttk.Frame(dlg)
        bottom.pack(fill="x", padx=10, pady=(0, 10))
        ttk.Button(bottom, text="Use selected move", command=choose_selected).pack(side="right", padx=(8, 0))
        ttk.Button(bottom, text="Cancel", command=dlg.destroy).pack(side="right")
        filter_entry.focus_set()

        parent.wait_window(dlg)
        if result["value"] is None:
            return None
        return int(result["value"]), str(result["label"])


    def _choose_chun_selector_value(self, addr: int, current: str) -> tuple[int, bool] | None:
        result: dict[str, object] = {"value": None, "apply_all": True}
        dlg = tk.Toplevel(self.root)
        dlg.title("Choose Chun selector word")
        dlg.geometry("430x260")
        dlg.transient(self.root)
        dlg.grab_set()

        apply_all_var = tk.BooleanVar(value=True)

        ttk.Label(
            dlg,
            text=(
                f"Address: 0x{addr:08X}\n"
                f"Current: {current}\n\n"
                "Choose a loaded preset or enter a custom selector word. "
                "The confirmed Chun graft is installed before writing."
            ),
            justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        ttk.Label(
            dlg,
            text="This writes all three Chun selector lanes together.",
            justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 8))

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill="x", padx=12, pady=4)

        def set_value(v: int):
            result["value"] = v
            result["apply_all"] = True
            dlg.destroy()

        def choose_loaded_move():
            owner_base = _owning_chr_tbl(addr)
            if owner_base is None:
                messagebox.showerror("No owner", "Could not resolve the character slot for this selector lane.", parent=dlg)
                return
            picked = self._choose_loaded_move_preset_word(owner_base, dlg)
            if picked is None:
                return
            value, _label = picked
            apply_all_var.set(True)
            set_value(value)

        def manual():
            text = simpledialog.askstring(
                "Custom Chun selector word",
                "Enter raw U32 selector word. Examples:\n"
                "0x000378AC\n"
                "00057480\n"
                "00 03 C4 AC",
                parent=dlg,
                initialvalue=current,
            )
            if text is None:
                return
            cleaned = text.strip().replace(" ", "").replace("_", "")
            if cleaned.lower().startswith("0x"):
                cleaned = cleaned[2:]
            try:
                value = int(cleaned, 16)
            except ValueError:
                messagebox.showerror("Invalid", f"{text!r} is not a u32 value.", parent=dlg)
                return
            if not (0 <= value <= 0xFFFFFFFF):
                messagebox.showerror("Out of range", "Value must be 0-0xFFFFFFFF.", parent=dlg)
                return
            set_value(value)

        ttk.Button(btn_frame, text="Preset assist / loaded move", command=choose_loaded_move).pack(fill="x", pady=3)
        ttk.Button(btn_frame, text="Custom raw U32", command=manual).pack(fill="x", pady=3)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(fill="x", pady=3)
        self.root.wait_window(dlg)

        if result["value"] is None:
            return None
        return int(result["value"]), bool(result["apply_all"])

    def _selector_target_for_display(self, addr: int, raw_val: int) -> str:
        selector_base = _selector_base_for_address(addr)
        if selector_base is None or raw_val > 0x100000:
            return ""
        return f"0x{selector_base + raw_val:08X}"

    def _apply_chun_selector_word(self, h: dict) -> None:
        graft_addr = int(h["block"])
        lane_addr = int(h["addr"])
        current = str(h.get("raw", "0x00000000"))

        choice = self._choose_chun_selector_value(lane_addr, current)
        if choice is None:
            return
        raw_val, apply_all = choice
        payload = struct.pack(">I", raw_val)

        writes: dict[int, bytes] = {graft_addr: CHUN_SELECTOR_GRAFT_BLOCK}
        if apply_all:
            for off in CHUN_SELECTOR_WORD_OFFSETS:
                writes[graft_addr + off] = payload
        else:
            writes[lane_addr] = payload

        ok = self._write_many(
            writes,
            f"Installed Chun graft at 0x{graft_addr:08X} and wrote selector 0x{raw_val:08X}."
        )
        if not ok:
            return

        block_bytes = bytearray(CHUN_SELECTOR_GRAFT_BLOCK)
        for off in CHUN_SELECTOR_WORD_OFFSETS:
            if apply_all or graft_addr + off == lane_addr:
                block_bytes[off:off + 4] = payload

        for iid, row in self._hit_by_iid.items():
            if int(row.get("block", -1)) != graft_addr:
                continue
            if row.get("kind") == "chun-graft-table":
                hx = bytes(block_bytes).hex(" ").upper()
                self._tree.set(iid, "raw", hx)
                row["raw"] = hx
                row["guess"] = "confirmed Chun assist selector graft table; installed/edited"
                self._tree.set(iid, "guess", row["guess"])
                continue
            if row.get("kind") != "chun-selector-word":
                continue
            row_addr = int(row["addr"])
            off = row_addr - graft_addr
            if off not in CHUN_SELECTOR_WORD_OFFSETS:
                continue
            if apply_all or row_addr == lane_addr:
                display_val = raw_val
            else:
                default_payload = CHUN_SELECTOR_RESET_WORDS.get(off)
                display_val = struct.unpack(">I", default_payload)[0] if default_payload else 0
            row["raw"] = f"0x{display_val:08X}"
            row["target"] = self._selector_target_for_display(row_addr, display_val)
            row["entry"] = _flat_selector_entry_label(_owning_chr_tbl(row_addr), [display_val], _read_slot_char_ids(), "Chun assist")
            row["guess"] = f"double-click to change Chun assist; table 0x{graft_addr:08X}"
            self._tree.set(iid, "entry", row["entry"])
            self._tree.set(iid, "raw", row["raw"])
            self._tree.set(iid, "target", row["target"])
            self._tree.set(iid, "guess", row["guess"])

        self._last_chun_selector_addr = graft_addr
        self._last_chun_selector_test = f"0x{raw_val:08X}" + (" all lanes" if apply_all else f" at 0x{lane_addr:08X}")
        self._status.set(
            f"Chun graft installed at 0x{graft_addr:08X}; wrote 0x{raw_val:08X}"
            + (" to all lanes." if apply_all else f" to 0x{lane_addr:08X}.")
        )


    def _ryu_selector_candidate_score(self, base: int, idx: int, data: bytes,
                                      slot_char_ids: dict[int, int], source: str) -> int:
        addr = base + idx
        score = 0

        cid = slot_char_ids.get(base)
        if cid == 12:
            score += 100
        elif cid is not None:
            score -= 100

        if source == "graft":
            score += 40
        else:
            score += 20

        if _looks_like_ryu_selector_graft_shape(data, idx):
            score += 80

        score -= (_slot_index_for_base(base) or 0)
        score -= (addr & 0xFF) // 0x10
        return score

    def _find_ryu_selector_graft_addr(self) -> tuple[int, str]:
        if rbytes is None:
            raise RuntimeError("dolphin_io.rbytes unavailable.")

        slot_char_ids = _read_slot_char_ids()
        candidates: list[tuple[int, int, str]] = []

        for base in _CHR_TBL_BASES:
            try:
                data = self._read_memory_region(base, 0x90000, f"slot @ 0x{base:08X}")
            except Exception:
                continue

            pos = 0
            while True:
                idx = data.find(RYU_SELECTOR_GRAFT_BLOCK, pos)
                if idx < 0:
                    break
                pos = idx + 1
                score = self._ryu_selector_candidate_score(base, idx, data, slot_char_ids, "graft")
                candidates.append((score, base + idx, "graft"))

            pos = 0
            while True:
                idx = data.find(b"\x0F\x06\x00\x27", pos)
                if idx < 0:
                    break
                pos = idx + 1
                if not _looks_like_ryu_selector_graft_shape(data, idx):
                    continue
                score = self._ryu_selector_candidate_score(base, idx, data, slot_char_ids, "shape-fallback")
                candidates.append((score, base + idx, "shape-fallback"))

        if not candidates:
            raise RuntimeError(
                "Could not find the Ryu assist selector table. Load Ryu, call the assist once, then try again."
            )

        candidates.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        _, addr, source = candidates[0]
        return addr, source

    def _restore_ryu_selector_block(self):
        try:
            addr, source = self._find_ryu_selector_graft_addr()
        except Exception as e:
            self._status.set("Ryu selector restore failed")
            messagebox.showerror("Restore Ryu Selector failed", str(e), parent=self.root)
            return

        ok = self._write_many(
            {addr: RYU_SELECTOR_GRAFT_BLOCK},
            f"Restored Ryu selector table at 0x{addr:08X}."
        )
        if not ok:
            return

        self._status.set(f"Restored Ryu selector table at 0x{addr:08X} using {source} match.")
        messagebox.showinfo(
            "Ryu selector restored",
            f"Restored Ryu selector table at 0x{addr:08X} using {source} match.",
            parent=self.root,
        )


    def _choose_ryu_graft_selector_value(self, addr: int, current: str) -> tuple[int, bool] | None:
        result: dict[str, object] = {"value": None, "apply_all": True}
        dlg = tk.Toplevel(self.root)
        dlg.title("Choose Ryu selector word")
        dlg.geometry("430x260")
        dlg.transient(self.root)
        dlg.grab_set()

        apply_all_var = tk.BooleanVar(value=True)

        ttk.Label(
            dlg,
            text=(
                f"Address: 0x{addr:08X}\n"
                f"Current: {current}\n\n"
                "Choose a loaded preset or enter a custom selector word. "
                "The Ryu selector setup is restored before writing."
            ),
            justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        ttk.Label(
            dlg,
            text="This writes all three Ryu selector lanes together.",
            justify="left",
        ).pack(anchor="w", padx=12, pady=(0, 8))

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill="x", padx=12, pady=4)

        def set_value(v: int):
            result["value"] = v
            result["apply_all"] = True
            dlg.destroy()

        def choose_loaded_move():
            owner_base = _owning_chr_tbl(addr)
            if owner_base is None:
                messagebox.showerror("No owner", "Could not resolve the character slot for this selector lane.", parent=dlg)
                return
            picked = self._choose_loaded_move_preset_word(owner_base, dlg)
            if picked is None:
                return
            value, _label = picked
            apply_all_var.set(True)
            set_value(value)

        def manual():
            text = simpledialog.askstring(
                "Custom Ryu selector word",
                "Enter raw U32 selector word. Examples:\n"
                "0x0003110C\n"
                "0003146C\n"
                "00 03 1C DC",
                parent=dlg,
                initialvalue=current,
            )
            if text is None:
                return
            cleaned = text.strip().replace(" ", "").replace("_", "")
            if cleaned.lower().startswith("0x"):
                cleaned = cleaned[2:]
            try:
                value = int(cleaned, 16)
            except ValueError:
                messagebox.showerror("Invalid", f"{text!r} is not a u32 value.", parent=dlg)
                return
            if not (0 <= value <= 0xFFFFFFFF):
                messagebox.showerror("Out of range", "Value must be 0-0xFFFFFFFF.", parent=dlg)
                return
            set_value(value)

        ttk.Button(btn_frame, text="Preset assist / loaded move", command=choose_loaded_move).pack(fill="x", pady=3)
        ttk.Button(btn_frame, text="Custom raw U32", command=manual).pack(fill="x", pady=3)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(fill="x", pady=3)
        self.root.wait_window(dlg)

        if result["value"] is None:
            return None
        return int(result["value"]), bool(result["apply_all"])

    def _apply_ryu_selector_word(self, h: dict) -> None:
        graft_addr = int(h["block"])
        lane_addr = int(h["addr"])
        current = str(h.get("raw", "0x00000000"))

        choice = self._choose_ryu_graft_selector_value(lane_addr, current)
        if choice is None:
            return
        raw_val, apply_all = choice
        payload = struct.pack(">I", raw_val)

        writes: dict[int, bytes] = {graft_addr: RYU_SELECTOR_GRAFT_BLOCK}
        if apply_all:
            for off in RYU_SELECTOR_WORD_OFFSETS:
                writes[graft_addr + off] = payload
        else:
            writes[lane_addr] = payload

        ok = self._write_many(
            writes,
            f"Restored Ryu selector table at 0x{graft_addr:08X} and wrote selector 0x{raw_val:08X}."
        )
        if not ok:
            return

        block_bytes = bytearray(RYU_SELECTOR_GRAFT_BLOCK)
        for off in RYU_SELECTOR_WORD_OFFSETS:
            if apply_all or graft_addr + off == lane_addr:
                block_bytes[off:off + 4] = payload

        for iid, row in self._hit_by_iid.items():
            if int(row.get("block", -1)) != graft_addr:
                continue
            if row.get("kind") == "ryu-graft-table":
                hx = bytes(block_bytes).hex(" ").upper()
                self._tree.set(iid, "raw", hx)
                row["raw"] = hx
                row["guess"] = "confirmed Ryu assist selector table; installed/edited"
                self._tree.set(iid, "guess", row["guess"])
                continue
            if row.get("kind") != "ryu-selector-word":
                continue
            row_addr = int(row["addr"])
            off = row_addr - graft_addr
            if off not in RYU_SELECTOR_WORD_OFFSETS:
                continue
            if apply_all or row_addr == lane_addr:
                display_val = raw_val
            else:
                default_payload = RYU_SELECTOR_RESET_WORDS.get(off)
                display_val = struct.unpack(">I", default_payload)[0] if default_payload else 0
            row["raw"] = f"0x{display_val:08X}"
            row["target"] = self._selector_target_for_display(row_addr, display_val)
            row["entry"] = _flat_selector_entry_label(_owning_chr_tbl(row_addr), [display_val], _read_slot_char_ids(), "Ryu assist")
            row["guess"] = f"double-click to change Ryu assist; table 0x{graft_addr:08X}"
            self._tree.set(iid, "entry", row["entry"])
            self._tree.set(iid, "raw", row["raw"])
            self._tree.set(iid, "target", row["target"])
            self._tree.set(iid, "guess", row["guess"])

        self._status.set(
            f"Ryu selector table restored at 0x{graft_addr:08X}; wrote 0x{raw_val:08X}"
            + (" to all lanes." if apply_all else f" to 0x{lane_addr:08X}.")
        )


    def _choose_generic_selector_value(self, addr: int, current: str, source: str) -> tuple[int, bool] | None:
        result: dict[str, object] = {"value": None, "apply_all": True}
        dlg = tk.Toplevel(self.root)
        dlg.title("Choose generic selector word")
        dlg.geometry("450x280")
        dlg.transient(self.root)
        dlg.grab_set()

        # For a graft candidate, there are no valid selector words in the block yet.
        # Applying the chosen value to all lanes gives the graft safe/consistent words.
        apply_all_var = tk.BooleanVar(value=True)

        ttk.Label(
            dlg,
            text=(
                f"Address: 0x{addr:08X}\n"
                f"Current: {current}\n"
                f"Source: {source}\n\n"
                "Choose a loaded preset or enter a custom selector word. "
                "The generic selector setup is installed before writing."
            ),
            justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        ttk.Checkbutton(
            dlg,
            text="Apply this word to all three generic selector lanes",
            variable=apply_all_var,
        ).pack(anchor="w", padx=12, pady=(0, 8))

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill="x", padx=12, pady=4)

        def set_value(v: int):
            result["value"] = v
            result["apply_all"] = bool(apply_all_var.get())
            dlg.destroy()

        def choose_loaded_move():
            owner_base = _owning_chr_tbl(addr)
            if owner_base is None:
                messagebox.showerror("No owner", "Could not resolve the character slot for this selector lane.", parent=dlg)
                return
            picked = self._choose_loaded_move_preset_word(owner_base, dlg)
            if picked is None:
                return
            value, _label = picked
            apply_all_var.set(True)
            set_value(value)

        def manual():
            text = simpledialog.askstring(
                "Custom generic selector word",
                "Enter raw U32 selector word. Examples:\n"
                "0x0003D620\n"
                "0003110C\n"
                "00 03 C4 AC",
                parent=dlg,
                initialvalue=current,
            )
            if text is None:
                return
            cleaned = text.strip().replace(" ", "").replace("_", "")
            if cleaned.lower().startswith("0x"):
                cleaned = cleaned[2:]
            try:
                value = int(cleaned, 16)
            except ValueError:
                messagebox.showerror("Invalid", f"{text!r} is not a u32 value.", parent=dlg)
                return
            if not (0 <= value <= 0xFFFFFFFF):
                messagebox.showerror("Out of range", "Value must be 0-0xFFFFFFFF.", parent=dlg)
                return
            set_value(value)

        ttk.Button(btn_frame, text="Preset assist / loaded move", command=choose_loaded_move).pack(fill="x", pady=3)
        ttk.Button(btn_frame, text="Custom raw U32", command=manual).pack(fill="x", pady=3)
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(fill="x", pady=3)
        self.root.wait_window(dlg)

        if result["value"] is None:
            return None
        return int(result["value"]), bool(result["apply_all"])

    def _read_generic_block_for_graft(self, graft_addr: int) -> bytes:
        if rbytes is None:
            raise RuntimeError("dolphin_io.rbytes unavailable.")
        data = rbytes(graft_addr, GENERIC_SELECTOR_TABLE_LEN) or b""
        if len(data) != GENERIC_SELECTOR_TABLE_LEN:
            raise RuntimeError(
                f"Could not read generic graft block at 0x{graft_addr:08X}; got 0x{len(data):X} byte(s)."
            )
        return data

    def _generic_block_args_and_words(self, graft_addr: int, source: str,
                                      raw_val: int, apply_all: bool,
                                      lane_addr: int) -> bytes:
        try:
            current = self._read_generic_block_for_graft(graft_addr)
        except Exception:
            current = b""

        words = [raw_val, raw_val, raw_val]
        arg32 = GENERIC_SELECTOR_DEFAULT_ARG_32
        arg33 = GENERIC_SELECTOR_DEFAULT_ARG_33

        if len(current) == GENERIC_SELECTOR_TABLE_LEN and current.startswith(GENERIC_SELECTOR_SETUP_PREFIX):
            # Already native/installed. Preserve existing words unless applying all.
            for i, off in enumerate(GENERIC_SELECTOR_WORD_OFFSETS):
                old = struct.unpack(">I", current[off:off + 4])[0]
                words[i] = raw_val if (apply_all or graft_addr + off == lane_addr) else old
            arg32 = current[0x20:0x24]
            arg33 = current[0x28:0x2C]
        elif len(current) == GENERIC_SELECTOR_TABLE_LEN and current[0:4] == b"\x0F\x06\x00\x27" and current[4:8] == SELECTOR_TAIL:
            # Graft candidate. Preserve the original 37 32 / 37 33 args, and use
            # the chosen word for all lanes unless the user unchecks apply-all.
            # If apply-all was unchecked, still fill uninitialized lanes with the
            # chosen value because there are no valid selector words yet.
            words = [raw_val, raw_val, raw_val]
            arg32 = current[0x08:0x0C]
            arg33 = current[0x10:0x14]

        return _build_generic_selector_graft_block(tuple(words), arg32, arg33)

    def _apply_generic_selector_word(self, h: dict) -> None:
        graft_addr = int(h["block"])
        lane_addr = int(h["addr"])
        source = str(h.get("source", "unknown"))
        current = str(h.get("raw", "0x00000000"))

        choice = self._choose_generic_selector_value(lane_addr, current, source)
        if choice is None:
            return
        raw_val, apply_all = choice

        try:
            block = self._generic_block_args_and_words(graft_addr, source, raw_val, apply_all, lane_addr)
        except Exception as e:
            messagebox.showerror("Generic graft failed", str(e), parent=self.root)
            return

        ok = self._write_many(
            {graft_addr: block},
            f"Installed generic selector table at 0x{graft_addr:08X} and wrote selector 0x{raw_val:08X}."
        )
        if not ok:
            return

        for iid, row in self._hit_by_iid.items():
            if int(row.get("block", -1)) != graft_addr:
                continue
            if row.get("kind") in ("generic-native-table", "generic-graft-table"):
                hx = block.hex(" ").upper()
                self._tree.set(iid, "raw", hx)
                row["raw"] = hx
                row["guess"] = "experimental generic selector table; installed/edited"
                self._tree.set(iid, "guess", row["guess"])
                row["source"] = "installed"
                continue
            if row.get("kind") != "generic-selector-word":
                continue
            row_addr = int(row["addr"])
            off = row_addr - graft_addr
            if off not in GENERIC_SELECTOR_WORD_OFFSETS:
                continue
            display_val = struct.unpack(">I", block[off:off + 4])[0]
            row["raw"] = f"0x{display_val:08X}"
            row["target"] = self._selector_target_for_display(row_addr, display_val)
            row["guess"] = "double-click: generic selector word; installed/edited"
            row["source"] = "installed"
            self._tree.set(iid, "raw", row["raw"])
            self._tree.set(iid, "target", row["target"])
            self._tree.set(iid, "guess", row["guess"])

        self._status.set(
            f"Generic selector table installed at 0x{graft_addr:08X}; wrote 0x{raw_val:08X}"
            + (" to all lanes." if apply_all else f" using lane 0x{lane_addr:08X}.")
        )


    def _find_generic_selector_graft_addr_for_base(self, owner_base: int) -> tuple[int, str]:
        data = self._read_memory_region(owner_base, 0x90000, f"slot @ 0x{owner_base:08X}")
        candidates: list[tuple[int, int, str]] = []

        pos = 0
        while True:
            idx = data.find(b"\x0F\x06\x00\x27", pos)
            if idx < 0:
                break
            pos = idx + 1
            if _looks_like_generic_native_selector_shape(data, idx):
                candidates.append((100, owner_base + idx, "native"))
            elif _looks_like_generic_graft_candidate_shape(data, idx):
                candidates.append((80, owner_base + idx, "graft-candidate"))

        if not candidates:
            raise RuntimeError(
                f"No generic selector/graft candidate found in slot base 0x{owner_base:08X}. "
                "For this character, find the assist block first or use a confirmed Chun/Ryu table."
            )

        candidates.sort(key=lambda x: (x[0], -x[1]), reverse=True)
        _, addr, source = candidates[0]
        return addr, source

    def _choose_assist_move_preset_word(self, h: dict) -> tuple[int, bool, str] | None:
        move_name = str(h.get("move_name", "move"))
        move_abs = int(h.get("move_abs", h.get("addr", 0)))
        base_word = int(h.get("selector_word", 0))
        current = f"0x{base_word:08X}"

        result: dict[str, object] = {"value": None, "apply_all": True, "label": ""}
        dlg = tk.Toplevel(self.root)
        dlg.title("Choose assist move entry")
        dlg.geometry("500x430")
        dlg.transient(self.root)
        dlg.grab_set()

        apply_all_var = tk.BooleanVar(value=True)

        ttk.Label(
            dlg,
            text=(
                f"Move: {move_name}\n"
                f"Move address: 0x{move_abs:08X}\n"
                f"Base selector word: {current}\n\n"
                "Pick which entry point to write into the graft lanes. "
                "Base is fastest; +0x70/+0x90 are useful Ryu-style continuation probes."
            ),
            justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        ttk.Checkbutton(
            dlg,
            text="Apply this word to all three selector lanes",
            variable=apply_all_var,
        ).pack(anchor="w", padx=12, pady=(0, 8))

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill="x", padx=12, pady=4)

        def set_value(v: int, label: str):
            result["value"] = v
            result["label"] = label
            result["apply_all"] = bool(apply_all_var.get())
            dlg.destroy()

        for delta in MOVE_PRESET_ENTRY_OFFSETS:
            v = (base_word + delta) & 0xFFFFFFFF
            ttk.Button(
                btn_frame,
                text=f"{move_name} +0x{delta:02X}  0x{v:08X}",
                command=lambda vv=v, dd=delta: set_value(vv, f"{move_name} +0x{dd:02X}"),
            ).pack(fill="x", pady=2)

        def manual():
            text = simpledialog.askstring(
                "Manual selector word",
                "Enter raw U32 selector word. Examples:\n"
                "0x000378AC\n"
                "00057480\n"
                "00 03 C4 AC",
                parent=dlg,
                initialvalue=current,
            )
            if text is None:
                return
            cleaned = text.strip().replace(" ", "").replace("_", "")
            if cleaned.lower().startswith("0x"):
                cleaned = cleaned[2:]
            try:
                value = int(cleaned, 16)
            except ValueError:
                messagebox.showerror("Invalid", f"{text!r} is not a u32 value.", parent=dlg)
                return
            if not (0 <= value <= 0xFFFFFFFF):
                messagebox.showerror("Out of range", "Value must be 0-0xFFFFFFFF.", parent=dlg)
                return
            set_value(value, "manual")

        ttk.Button(btn_frame, text="Manual raw U32", command=manual).pack(fill="x", pady=(10, 3))
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(fill="x", pady=3)
        self.root.wait_window(dlg)

        if result["value"] is None:
            return None
        return int(result["value"]), bool(result["apply_all"]), str(result["label"])

    def _apply_assist_move_preset(self, h: dict) -> None:
        choice = self._choose_assist_move_preset_word(h)
        if choice is None:
            return

        raw_val, apply_all, label = choice
        owner_base = int(h.get("owner_base", 0))
        char_id = int(h.get("char_id", 0))
        move_name = str(h.get("move_name", "move"))
        payload = struct.pack(">I", raw_val)

        try:
            if char_id == 13:
                graft_addr, source = self._find_chun_selector_graft_addr()
                writes: dict[int, bytes] = {graft_addr: CHUN_SELECTOR_GRAFT_BLOCK}
                lane_offsets = CHUN_SELECTOR_WORD_OFFSETS
                table_name = "Chun"
            elif char_id == 12:
                graft_addr, source = self._find_ryu_selector_graft_addr()
                writes = {graft_addr: RYU_SELECTOR_GRAFT_BLOCK}
                lane_offsets = RYU_SELECTOR_WORD_OFFSETS
                table_name = "Ryu"
            else:
                graft_addr, source = self._find_generic_selector_graft_addr_for_base(owner_base)
                block = self._generic_block_args_and_words(
                    graft_addr,
                    source,
                    raw_val,
                    True,
                    graft_addr + GENERIC_SELECTOR_WORD_OFFSETS[0],
                )
                writes = {graft_addr: block}
                lane_offsets = GENERIC_SELECTOR_WORD_OFFSETS
                table_name = "generic"
        except Exception as e:
            self._status.set("Assist move preset failed")
            messagebox.showerror("Assist move preset failed", str(e), parent=self.root)
            return

        if char_id in (12, 13):
            if apply_all:
                for off in lane_offsets:
                    writes[graft_addr + off] = payload
            else:
                writes[graft_addr + lane_offsets[0]] = payload

        ok = self._write_many(
            writes,
            f"Applied {move_name} selector 0x{raw_val:08X} to {table_name} graft at 0x{graft_addr:08X}."
        )
        if not ok:
            return

        lane_lines = []
        if char_id in (12, 13):
            target_offsets = lane_offsets if apply_all else (lane_offsets[0],)
            lane_lines = [f"0x{graft_addr + off:08X} <- {payload.hex(' ').upper()}" for off in target_offsets]
        else:
            lane_lines = [f"0x{graft_addr + off:08X} <- {payload.hex(' ').upper()}" for off in lane_offsets]

        messagebox.showinfo(
            "Assist move preset applied",
            (
                f"{label}\n"
                f"Move: {move_name}\n"
                f"Selector word: 0x{raw_val:08X}\n"
                f"Table: {table_name} at 0x{graft_addr:08X} ({source})\n\n"
                + "\n".join(lane_lines)
                + "\n\nTest assist now. If it stalls, retry the same move with +0x10, +0x20, +0x70, or +0x90."
            ),
            parent=self.root,
        )

        self._status.set(
            f"Applied assist move preset {move_name} ({label}) as 0x{raw_val:08X}."
        )

    def _on_double_click(self, event):
        iid = self._tree.identify_row(event.y)
        col_idx = self._col_index(event)
        if not iid or col_idx < 0:
            return
        col_id = COL_IDS[col_idx]
        h = self._hit_by_iid.get(iid)
        if not h or not h.get("editable"):
            return
        addr = int(h["addr"])
        typ = str(h.get("typ", ""))

        if typ == "u32-chun-selector":
            self._apply_chun_selector_word(h)
            return

        if typ == "u32-ryu-selector-graft":
            self._apply_ryu_selector_word(h)
            return

        if typ == "u32-generic-selector":
            self._apply_generic_selector_word(h)
            return

        if typ == "assist-move-preset":
            self._apply_assist_move_preset(h)
            return

        if wbytes is None:
            messagebox.showerror("Write failed", "dolphin_io.wbytes unavailable.", parent=self.root)
            return

        if typ == "u32-selector":
            val = self._choose_ryu_preset_or_manual(addr, str(h["raw"]))
            if val is None:
                return
            payload = struct.pack(">I", val)
        else:
            payload = self._manual_value(h)
            if payload is None:
                return
            val = None

        try:
            ok = bool(wbytes(addr, payload))
        except Exception as e:
            messagebox.showerror("Write failed", str(e), parent=self.root)
            return
        if not ok:
            messagebox.showerror("Write failed", "Could not write to Dolphin.", parent=self.root)
            return

        if typ == "u32-selector":
            raw_val = struct.unpack(">I", payload)[0]
            owner_base = _owning_chr_tbl(addr)
            as_local = owner_base + raw_val if owner_base is not None and raw_val <= 0x100000 else 0
            guess = RYU_KNOWN_TARGETS.get(raw_val, "manual selector")
            self._tree.set(iid, "raw", f"0x{raw_val:08X}")
            self._tree.set(iid, "target", f"0x{as_local:08X}" if as_local else "")
            self._tree.set(iid, "guess", guess)
            h["raw"] = f"0x{raw_val:08X}"
            h["target"] = f"0x{as_local:08X}" if as_local else ""
            h["guess"] = guess
            self._status.set(f"Wrote selector 0x{raw_val:08X} to 0x{addr:08X}")
        elif typ == "u16-state":
            state = struct.unpack(">H", payload)[0]
            self._tree.set(iid, "raw", f"0x{state:04X}")
            h["raw"] = f"0x{state:04X}"
            self._status.set(f"Wrote StateID 0x{state:04X} to 0x{addr:08X}")
        elif typ == "f32":
            fval = struct.unpack(">f", payload)[0]
            hx = payload.hex(" ").upper()
            self._tree.set(iid, "raw", _fmt_f32(fval))
            self._tree.set(iid, "target", hx)
            h["raw"] = _fmt_f32(fval)
            h["target"] = hx
            self._status.set(f"Wrote f32 {_fmt_f32(fval)} ({hx}) to 0x{addr:08X}")
        else:
            hx = payload.hex(" ").upper()
            self._tree.set(iid, "raw", hx)
            h["raw"] = hx
            self._status.set(f"Wrote {len(payload)} byte(s) to 0x{addr:08X}")

    def _on_right_click(self, event):
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        h = self._hit_by_iid.get(iid)
        if not h:
            return
        addr = int(h["addr"])
        block = int(h["block"])
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"Copy address 0x{addr:08X}", command=lambda: self._copy(f"0x{addr:08X}"))
        menu.add_command(label=f"Copy block 0x{block:08X}", command=lambda: self._copy(f"0x{block:08X}"))
        menu.add_command(label=f"Go to address 0x{addr:08X}", command=lambda: self._show_address_info(addr))
        menu.add_command(label=f"Go to block 0x{block:08X}", command=lambda: self._show_address_info(block))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._status.set(f"Copied {text}")

    def _show_address_info(self, addr: int):
        if rbytes is None:
            messagebox.showerror("Address", "dolphin_io.rbytes unavailable", parent=self.root)
            return
        line_size = 16
        line_base = addr & ~(line_size - 1)
        start = max(SCAN_START, line_base - 8 * line_size)
        size = 17 * line_size
        try:
            data = rbytes(start, size) or b""
        except Exception as e:
            messagebox.showerror("Address", f"Read failed: {e}", parent=self.root)
            return
        dlg = tk.Toplevel(self.root)
        dlg.title(f"Assist bytes @ 0x{addr:08X}")
        dlg.geometry("840x460")
        txt = tk.Text(dlg, wrap="none", font=("Consolas", 10), bg="#101214", fg="#E8E8E8")
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        current_line = (line_base - start) // line_size
        for i in range(17):
            off = i * line_size
            chunk = data[off:off + line_size]
            a = start + off
            hx = " ".join(f"{b:02X}" for b in chunk)
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            prefix = ">>" if i == current_line else "  "
            txt.insert("end", f"{prefix} 0x{a:08X}: {hx:<47} {asc}\n")
        txt.config(state="disabled")


_inst = None


def open_assist_scanner_window():
    def _c(master):
        global _inst
        if _inst:
            try:
                _inst.root.lift()
                return
            except Exception:
                pass
        _inst = AssistScannerWindow(master)
    tk_call(_c)
