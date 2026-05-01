from __future__ import annotations

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
#   3732203F 00000003 3733203F 00000009
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
    "37 32 20 3F 00 00 00 03 "
    "37 33 20 3F 00 00 00 09"
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
    ("kind", "Kind"),
    ("block", "Block"),
    ("owner", "Owner"),
    ("slot", "SlotCID"),
    ("entry", "Entry"),
    ("address", "Address"),
    ("raw", "Raw"),
    ("target", "AsLocal"),
    ("guess", "Guess"),
    ("score", "Score"),
    ("ctx", "Context"),
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
    label = "installed graft" if source == "graft" else "original slot" if source == "original" else "live wrapper fallback"

    hits.append({
        "kind": "chun-graft-table",
        "block": block_addr,
        "addr": block_addr,
        "owner": owner,
        "slot": slot,
        "entry": label,
        "raw": current_block.hex(" ").upper(),
        "target": "",
        "guess": "confirmed Chun assist selector graft table; double-click a lane to install/update",
        "score": 100 if slot == "0x0D" else 90,
        "ctx": _fmt_context(data, idx, mark_len=len(CHUN_SELECTOR_GRAFT_BLOCK)),
        "editable": False,
        "typ": "raw-window",
    })

    for lane_index, off in enumerate(CHUN_SELECTOR_WORD_OFFSETS, start=1):
        raw = _u32be(data, idx + off)
        default_raw = _u32be(CHUN_SELECTOR_GRAFT_BLOCK, off)
        if raw is None or default_raw is None:
            continue
        lane_addr = block_addr + off
        display_raw = raw if source != "original" else default_raw
        target_addr = owner_base + display_raw if 0 <= display_raw <= 0x100000 else 0
        actual_note = "" if source != "original" else f" current bytes 0x{raw:08X};"
        hits.append({
            "kind": "chun-selector-word",
            "block": block_addr,
            "addr": lane_addr,
            "owner": owner,
            "slot": slot,
            "entry": f"lane {lane_index}",
            "raw": f"0x{display_raw:08X}",
            "target": f"0x{target_addr:08X}" if target_addr else "",
            "guess": f"double-click: install graft then write selector word;{actual_note} source {source}",
            "score": 100,
            "ctx": _fmt_context(data, idx + off, mark_len=4),
            "editable": True,
            "typ": "u32-chun-selector",
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
        seen.add(idx)
        _append_chun_selector_graft_table(data, base_addr, idx, slot_char_ids, hits, "wrapper-fallback")


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
    # Keep Ryu selector chains because they are known-good proof of the selector model.
    pos = 0
    while True:
        idx = data.find(SELECTOR_TAIL, pos)
        if idx < 0:
            break
        pos = idx + 1
        start = idx - 12
        if start >= 0 and _selector_count(data, start) >= 2:
            _append_selector_block(data, base_addr, start, slot_char_ids, hits)

    # Confirmed Chun path: only surface the graft table and its editable lanes.
    # The old Tensho descriptor/wrapper scans are intentionally not shown here.
    _scan_chun_selector_graft_tables(data, base_addr, slot_char_ids, hits)


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
            "chun-graft-table": 0,
            "chun-selector-word": 1,
            "selector-chain": 2,
            "selector-loose": 3,
            "selector": 4,
            "confirmed-wrapper": 5,
            "confirmed-state-id": 6,
            "wrapper-poke": 7,
            "state-wrapper": 8,
            "state-id": 9,
            "phrase": 10,
        }.get(h["kind"], 11)
        return (priority, h["block"], kind_order, h["addr"], h["entry"])

    uniq.sort(key=sort_key)
    done_cb(uniq)


class AssistScannerWindow:
    def __init__(self, master):
        self.root = tk.Toplevel(master)
        self.root.title("Assist Scanner - Ryu Selectors + Chun Assist Selector Graft")
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
                "Finds the confirmed Chun assist selector graft table plus Ryu selector proof blocks. "
                "Double-click a Chun lane to install the graft and write a preset or manual selector word."
            ),
        ).pack(side="left")
        self._scan_btn = ttk.Button(top, text="Rescan", command=self._start)
        self._scan_btn.pack(side="right")

        self._route_restore_btn = ttk.Button(top, text="Restore Chun Original", command=self._restore_chun_selector_block)
        self._route_restore_btn.pack(side="right", padx=(0, 8))

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
            "kind": 115, "block": 110, "owner": 210, "slot": 70, "entry": 105,
            "address": 110, "raw": 180, "target": 120, "guess": 220,
            "score": 70, "ctx": 680,
        }
        for col_id, header in COLS:
            self._tree.heading(col_id, text=header, command=lambda c=col_id: self._sort_by(c))
            self._tree.column(col_id, width=widths.get(col_id, 80), anchor="center")
        for c in ("owner", "guess", "ctx"):
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
        self._status.set("Scanning MEM2 for Chun graft table and Ryu selectors...")
        threading.Thread(target=_run_scan, args=(self._on_prog, self._on_done), daemon=True).start()

    def _on_prog(self, pct: float):
        try:
            self.root.after(0, lambda: self._prog.set(pct))
        except Exception:
            pass

    def _on_done(self, hits: list[dict]):
        def _f():
            for h in hits:
                iid = self._tree.insert("", "end", values=(
                    h["kind"],
                    f"0x{h['block']:08X}",
                    h["owner"],
                    h["slot"],
                    h["entry"],
                    f"0x{h['addr']:08X}",
                    h["raw"],
                    h["target"],
                    h["guess"],
                    h["score"],
                    h["ctx"],
                ))
                self._hit_by_iid[iid] = h
            self._scanning = False
            self._scan_btn.config(state="normal")
            self._prog.set(100)
            selector_blocks = len({h["block"] for h in hits if h["kind"] in ("selector-chain", "selector-loose")})
            chun_tables = len({h["block"] for h in hits if h["kind"] == "chun-graft-table"})
            self._status.set(
                f"Done - {chun_tables} Chun graft table(s), {selector_blocks} Ryu selector block(s), {len(hits)} row(s)."
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

    def _choose_chun_selector_value(self, addr: int, current: str) -> tuple[int, bool] | None:
        result: dict[str, object] = {"value": None, "apply_all": False}
        dlg = tk.Toplevel(self.root)
        dlg.title("Choose Chun selector word")
        dlg.geometry("440x360")
        dlg.transient(self.root)
        dlg.grab_set()

        apply_all_var = tk.BooleanVar(value=False)

        ttk.Label(
            dlg,
            text=(
                f"Address: 0x{addr:08X}\n"
                f"Current: {current}\n\n"
                "Choosing a value installs the confirmed graft first, then writes the selector word."
            ),
            justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        ttk.Checkbutton(
            dlg,
            text="Apply this word to all three Chun selector lanes",
            variable=apply_all_var,
        ).pack(anchor="w", padx=12, pady=(0, 8))

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill="x", padx=12, pady=4)

        def set_value(v: int):
            result["value"] = v
            result["apply_all"] = bool(apply_all_var.get())
            dlg.destroy()

        for label, value in CHUN_SELECTOR_WORD_PRESETS:
            ttk.Button(
                btn_frame,
                text=f"{label}  0x{value:08X}",
                command=lambda v=value: set_value(v),
            ).pack(fill="x", pady=3)

        def manual():
            text = simpledialog.askstring(
                "Manual Chun selector word",
                "Enter raw U32 selector word. Examples:\n"
                "0x0003D620\n"
                "0003BCC8\n"
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

        ttk.Button(btn_frame, text="Manual raw U32", command=manual).pack(fill="x", pady=(10, 3))
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(fill="x", pady=3)
        self.root.wait_window(dlg)

        if result["value"] is None:
            return None
        return int(result["value"]), bool(result["apply_all"])

    def _selector_target_for_display(self, addr: int, raw_val: int) -> str:
        owner_base = _owning_chr_tbl(addr)
        if owner_base is None or raw_val > 0x100000:
            return ""
        return f"0x{owner_base + raw_val:08X}"

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
            row["guess"] = "double-click: install graft then write selector word"
            self._tree.set(iid, "raw", row["raw"])
            self._tree.set(iid, "target", row["target"])
            self._tree.set(iid, "guess", row["guess"])

        self._last_chun_selector_addr = graft_addr
        self._last_chun_selector_test = f"0x{raw_val:08X}" + (" all lanes" if apply_all else f" at 0x{lane_addr:08X}")
        self._status.set(
            f"Chun graft installed at 0x{graft_addr:08X}; wrote 0x{raw_val:08X}"
            + (" to all lanes." if apply_all else f" to 0x{lane_addr:08X}.")
        )

    def _on_double_click(self, event):
        iid = self._tree.identify_row(event.y)
        col_idx = self._col_index(event)
        if not iid or col_idx < 0:
            return
        col_id = COL_IDS[col_idx]
        if col_id != "raw":
            return
        h = self._hit_by_iid.get(iid)
        if not h or not h.get("editable"):
            return
        addr = int(h["addr"])
        typ = str(h.get("typ", ""))

        if typ == "u32-chun-selector":
            self._apply_chun_selector_word(h)
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
