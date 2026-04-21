# fighter.py
#
# Snapshot a single fighter's runtime struct (HP, position, states, etc.)
# Block-aware version for reduced cross-process memory reads.

from __future__ import annotations

import struct
from typing import Optional

from dolphin_io import rd32, rd8, rdf32
from resolver import looks_like_hp
from constants import (
    OFF_MAX_HP,
    OFF_CUR_HP,
    OFF_AUX_HP,
    OFF_CHAR_ID,
    OFF_LAST_HIT,
    CTRL_WORD_OFF,
    FLAG_062,
    FLAG_063,
    FLAG_064,
    FLAG_072,
    POSX_OFF,
    CHAR_NAMES,
)
from config import WIRE_OFFSETS, HEALTH_WIRE_OFFSETS
from moves import read_attack_ids


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def _safe_last_hit(raw_last_hit):
    if raw_last_hit is None:
        return None
    if raw_last_hit < 0 or raw_last_hit > 200_000:
        return None
    return raw_last_hit


def _u8_from_block(block: Optional[bytes], off: int):
    if not block or off < 0 or off >= len(block):
        return None
    return block[off]


def _u32be_from_block(block: Optional[bytes], off: int):
    if not block or off < 0 or off + 4 > len(block):
        return None
    return (
        (block[off] << 24)
        | (block[off + 1] << 16)
        | (block[off + 2] << 8)
        | block[off + 3]
    )


def _f32be_from_block(block: Optional[bytes], off: int):
    if not block or off < 0 or off + 4 > len(block):
        return None
    return struct.unpack(">f", block[off:off + 4])[0]


def _collect_wire_bytes(base_addr: int, offsets_list, block: Optional[bytes] = None):
    out = []
    for off in offsets_list:
        b = _u8_from_block(block, off)
        if b is None:
            b = rd8(base_addr + off)
        out.append((off, b))
    return out


# ------------------------------------------------------------
# Main Snapshot
# ------------------------------------------------------------

def read_fighter(base: int, y_off: int, block: Optional[bytes] = None):
    if not base:
        return None

    # --- Core HP values ---
    max_hp = _u32be_from_block(block, OFF_MAX_HP)
    if max_hp is None:
        max_hp = rd32(base + OFF_MAX_HP)

    cur_hp = _u32be_from_block(block, OFF_CUR_HP)
    if cur_hp is None:
        cur_hp = rd32(base + OFF_CUR_HP)

    aux_hp = _u32be_from_block(block, OFF_AUX_HP)
    if aux_hp is None:
        aux_hp = rd32(base + OFF_AUX_HP)

    if not looks_like_hp(max_hp, curhp=cur_hp, auxhp=aux_hp):
        return None

    # --- Experimental health bytes ---
    pooled_byte = _u8_from_block(block, 0x02A)
    if pooled_byte is None:
        pooled_byte = rd8(base + 0x02A)

    mystery_2B = _u8_from_block(block, 0x02B)
    if mystery_2B is None:
        mystery_2B = rd8(base + 0x02B)

    # --- Identity ---
    cid = _u32be_from_block(block, OFF_CHAR_ID)
    if cid is None:
        cid = rd32(base + OFF_CHAR_ID)

    name = CHAR_NAMES.get(cid, f"ID_{cid}") if cid is not None else "???"

    # --- Position ---
    x = _f32be_from_block(block, POSX_OFF)
    if x is None:
        x = rdf32(base + POSX_OFF)

    y = None
    if y_off is not None:
        y = _f32be_from_block(block, y_off)
        if y is None:
            y = rdf32(base + y_off)

    # --- Last hit ---
    raw_last = _u32be_from_block(block, OFF_LAST_HIT)
    if raw_last is None:
        raw_last = rd32(base + OFF_LAST_HIT)

    last_hit = _safe_last_hit(raw_last)

    # --- Control / state ---
    ctrl_word = _u32be_from_block(block, CTRL_WORD_OFF)
    if ctrl_word is None:
        ctrl_word = rd32(base + CTRL_WORD_OFF)

    f062 = _u8_from_block(block, FLAG_062)
    if f062 is None:
        f062 = rd8(base + FLAG_062)

    f063 = _u8_from_block(block, FLAG_063)
    if f063 is None:
        f063 = rd8(base + FLAG_063)

    f064 = _u8_from_block(block, FLAG_064)
    if f064 is None:
        f064 = rd8(base + FLAG_064)

    f072 = _u8_from_block(block, FLAG_072)
    if f072 is None:
        f072 = rd8(base + FLAG_072)

    # --- Attack IDs (left untouched for now) ---
    attA, attB = read_attack_ids(base)

    # --- Wire windows ---
    wires_hp = _collect_wire_bytes(base, HEALTH_WIRE_OFFSETS, block)
    wires_main = _collect_wire_bytes(base, WIRE_OFFSETS, block)

    snap = {
        "base": base,
        "max": max_hp,
        "cur": cur_hp,
        "aux": aux_hp,
        "hp_pool_byte": pooled_byte,
        "mystery_2B": mystery_2B,
        "id": cid,
        "name": name,
        "x": x,
        "y": y,
        "last": last_hit,
        "ctrl": ctrl_word,
        "f062": f062,
        "f063": f063,
        "f064": f064,
        "f072": f072,
        "attA": attA,
        "attB": attB,
        "wires_hp": wires_hp,
        "wires_main": wires_main,
    }

    return snap


# ------------------------------------------------------------
# Distance helper
# ------------------------------------------------------------

def dist2(a, b):
    if a is None or b is None:
        return float("inf")

    ax, ay = a.get("x"), a.get("y")
    bx, by = b.get("x"), b.get("y")

    if None in (ax, ay, bx, by):
        return float("inf")

    dx = ax - bx
    dy = ay - by
    return dx * dx + dy * dy