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

# Chun selector / route flip tests. Button applies one set at a time.
#
# Research-mode test bank:
#   1) Strongest Ryu-style Chun candidate: compact selector bank at 0x909697B4.
#      This is the one to test pre-match if mid-match gives no reaction.
#   2) Two low16/vector probes that numerically brush the confirmed live wrapper.
#   3) Three route-bundle force tests from the 0x90959A20 family.
#
# Each button press restores every candidate address to baseline first, then
# applies exactly one set. That keeps each yay/nay isolated.
CHUN_ROUTE_BASELINE = {
    # Strongest Chun Ryu-style selector-shaped bank.
    0x909697B4: bytes.fromhex("00 03 BC C8"),
    0x909697BC: bytes.fromhex("00 03 C4 AC"),
    0x909697C4: bytes.fromhex("00 03 CC 90"),
    0x909697CC: bytes.fromhex("00 03 D6 20"),

    # Pure low16 vector candidate near 0x9094C7E0.
    0x9094C7E8: bytes.fromhex("00 00 D4 68"),

    # Route-bundle candidate near 0x90959A20.
    0x90959A24: bytes.fromhex("00 00 D4 0C"),
    0x90959A3C: bytes.fromhex("00 00 D4 F0"),
    0x90959A54: bytes.fromhex("00 00 D4 F0"),
    0x90959A6C: bytes.fromhex("00 00 D4 D0"),
}

CHUN_ROUTE_TESTS = [
    (
        "S1 PREMATCH: force 0x909697B4 bank all -> 0003D620",
        {
            0x909697B4: bytes.fromhex("00 03 D6 20"),
            0x909697BC: bytes.fromhex("00 03 D6 20"),
            0x909697C4: bytes.fromhex("00 03 D6 20"),
            0x909697CC: bytes.fromhex("00 03 D6 20"),
        },
    ),
    (
        "S2 PREMATCH: force 0x909697B4 bank all -> 0003CC90",
        {
            0x909697B4: bytes.fromhex("00 03 CC 90"),
            0x909697BC: bytes.fromhex("00 03 CC 90"),
            0x909697C4: bytes.fromhex("00 03 CC 90"),
            0x909697CC: bytes.fromhex("00 03 CC 90"),
        },
    ),
    (
        "S3 PREMATCH: force 0x909697B4 bank all -> 0003C4AC",
        {
            0x909697B4: bytes.fromhex("00 03 C4 AC"),
            0x909697BC: bytes.fromhex("00 03 C4 AC"),
            0x909697C4: bytes.fromhex("00 03 C4 AC"),
            0x909697CC: bytes.fromhex("00 03 C4 AC"),
        },
    ),
    (
        "S4 PREMATCH: force 0x909697B4 bank all -> 0003BCC8",
        {
            0x909697B4: bytes.fromhex("00 03 BC C8"),
            0x909697BC: bytes.fromhex("00 03 BC C8"),
            0x909697C4: bytes.fromhex("00 03 BC C8"),
            0x909697CC: bytes.fromhex("00 03 BC C8"),
        },
    ),
    (
        "V1 PREMATCH: 0x9094C7E8 D468 -> D230",
        {
            0x9094C7E8: bytes.fromhex("00 00 D2 30"),
        },
    ),
    (
        "V2 PREMATCH: 0x9094C7E8 D468 -> D594",
        {
            0x9094C7E8: bytes.fromhex("00 00 D5 94"),
        },
    ),
    (
        "R1 PREMATCH: force 0x90959A route bundle all -> D40C",
        {
            0x90959A24: bytes.fromhex("00 00 D4 0C"),
            0x90959A3C: bytes.fromhex("00 00 D4 0C"),
            0x90959A54: bytes.fromhex("00 00 D4 0C"),
            0x90959A6C: bytes.fromhex("00 00 D4 0C"),
        },
    ),
    (
        "R2 PREMATCH: force 0x90959A route bundle all -> D4D0",
        {
            0x90959A24: bytes.fromhex("00 00 D4 D0"),
            0x90959A3C: bytes.fromhex("00 00 D4 D0"),
            0x90959A54: bytes.fromhex("00 00 D4 D0"),
            0x90959A6C: bytes.fromhex("00 00 D4 D0"),
        },
    ),
    (
        "R3 PREMATCH: force 0x90959A route bundle all -> D4F0",
        {
            0x90959A24: bytes.fromhex("00 00 D4 F0"),
            0x90959A3C: bytes.fromhex("00 00 D4 F0"),
            0x90959A54: bytes.fromhex("00 00 D4 F0"),
            0x90959A6C: bytes.fromhex("00 00 D4 F0"),
        },
    ),
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

    # New targeted scan: live Tensho 0xD0 descriptors, exposing likely X/Y float fields.
    _scan_tensho_descriptors(data, base_addr, slot_char_ids, hits)

    # Keep only the confirmed Chun wrapper neighborhood. Do not broad-scan every
    # state wrapper or every nearby phrase anymore; that was noise for this pass.
    _append_confirmed_wrapper_neighborhood(
        data=data,
        base_addr=base_addr,
        block_addr=0x90984D40,
        state_addr=0x90984D52,
        state_id=0x0112,
        label="Chun confirmed Tensho assist wrapper",
        slot_char_ids=slot_char_ids,
        hits=hits,
    )


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
            "confirmed-wrapper": 0,
            "confirmed-state-id": 1,
            "wrapper-poke": 2,
            "tensho-desc": 3,
            "tensho-float": 4,
            "tensho-meta": 5,
            "selector-chain": 6,
            "selector-loose": 4,
            "state-wrapper": 5,
            "selector": 6,
            "state-id": 7,
            "phrase": 8,
        }.get(h["kind"], 9)
        return (priority, h["block"], kind_order, h["addr"], h["entry"])

    uniq.sort(key=sort_key)
    done_cb(uniq)


class AssistScannerWindow:
    def __init__(self, master):
        self.root = tk.Toplevel(master)
        self.root.title("Assist Scanner - Ryu Selectors + Chun Tensho Descriptor Tests")
        self.root.geometry("1420x700")
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self._scanning = False
        self._hit_by_iid: dict[str, dict] = {}
        self._sort_col = None
        self._sort_asc = True
        self._chun_route_test_index = 0
        self._last_chun_route_test = None
        self._build()
        self._start()

    def _build(self):
        top = ttk.Frame(self.root)
        top.pack(side="top", fill="x", padx=8, pady=6)
        ttk.Label(
            top,
            text=(
                "Scans confirmed Ryu selector chains plus targeted Chun/Tensho 0xD0 descriptors. "
                "Double-click editable Raw cells to poke selector words, StateID, or f32 descriptor offsets."
            ),
        ).pack(side="left")
        self._scan_btn = ttk.Button(top, text="Rescan", command=self._start)
        self._scan_btn.pack(side="right")

        self._route_restore_btn = ttk.Button(top, text="Restore Chun Research", command=self._restore_chun_route_baseline)
        self._route_restore_btn.pack(side="right", padx=(0, 8))

        self._route_next_btn = ttk.Button(top, text="Next Chun Research Set", command=self._apply_next_chun_route_test)
        self._route_next_btn.pack(side="right", padx=(0, 8))

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
        self._status.set("Scanning MEM2 for Ryu selectors and targeted Tensho descriptors...")
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
            wrapper_blocks = len({h["block"] for h in hits if h["kind"] == "state-wrapper"})
            self._status.set(
                f"Done - {selector_blocks} selector block(s), {wrapper_blocks} Tensho descriptor row source(s), {len(hits)} row(s)."
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

    def _restore_chun_route_baseline(self):
        ok = self._write_many(CHUN_ROUTE_BASELINE, "Restored Chun research candidate baseline.")
        if ok:
            self._last_chun_route_test = None

    def _apply_next_chun_route_test(self):
        if not CHUN_ROUTE_TESTS:
            return
        name, writes = CHUN_ROUTE_TESTS[self._chun_route_test_index]

        # Restore whole cluster first, then apply the current set.
        # This keeps each yay/nay test isolated.
        merged = dict(CHUN_ROUTE_BASELINE)
        merged.update(writes)

        ok = self._write_many(
            merged,
            f"Applied {name}. Test Chun assist, then press Next or Restore."
        )
        if not ok:
            return

        self._last_chun_route_test = name
        self._chun_route_test_index = (self._chun_route_test_index + 1) % len(CHUN_ROUTE_TESTS)
        details = "\n".join(f"0x{addr:08X} <- {payload.hex(' ').upper()}" for addr, payload in writes.items())
        messagebox.showinfo(
            "Chun route test applied",
            f"{name}\n\n{details}\n\nTest assist now. Press Next Chun Research Set for the next test, or Restore Chun Research to reset.",
            parent=self.root,
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
