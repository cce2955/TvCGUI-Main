# fighter.py
# Reading a single fighter snapshot and distance math. :contentReference[oaicite:7]{index=7}

from dolphin_io import rd32, rd8, rdf32
from resolver import looks_like_hp
from constants import (
    OFF_MAX_HP, OFF_CUR_HP, OFF_AUX_HP, OFF_CHAR_ID, OFF_LAST_HIT,
    CTRL_WORD_OFF, FLAG_062, FLAG_063, FLAG_064, FLAG_072,
    POSX_OFF, CHAR_NAMES,
)
from config import WIRE_OFFSETS
from moves import read_attack_ids

def read_fighter(base, y_off):
    """
    Snapshot one fighter into a dict: hp, x/y, flags, active move IDs, etc.
    Returns None if the struct doesn't look valid. :contentReference[oaicite:8]{index=8}
    """
    if not base:
        return None

    max_hp = rd32(base + OFF_MAX_HP)
    cur_hp = rd32(base + OFF_CUR_HP)
    aux_hp = rd32(base + OFF_AUX_HP)
    if not looks_like_hp(max_hp, curhp=cur_hp, auxhp=aux_hp):
        return None

    cid  = rd32(base + OFF_CHAR_ID)
    name = CHAR_NAMES.get(cid, f"ID_{cid}") if cid is not None else "???"

    x = rdf32(base + POSX_OFF)
    y = rdf32(base + y_off) if y_off is not None else None

    last = rd32(base + OFF_LAST_HIT)
    if last is None or last < 0 or last > 200_000:
        last = None

    ctrl_word = rd32(base + CTRL_WORD_OFF)

    f062 = rd8(base + FLAG_062)
    f063 = rd8(base + FLAG_063)
    f064 = rd8(base + FLAG_064)
    f072 = rd8(base + FLAG_072)

    attA, attB = read_attack_ids(base)

    # "wires" dump
    wires = []
    for off in WIRE_OFFSETS:
        b = rd8(base + off)
        wires.append((off, b))

    return {
        "base": base,
        "max": max_hp,
        "cur": cur_hp,
        "aux": aux_hp,
        "id": cid,
        "name": name,
        "x": x,
        "y": y,
        "last": last,
        "ctrl": ctrl_word,
        "f062": f062,
        "f063": f063,
        "f064": f064,
        "f072": f072,
        "attA": attA,
        "attB": attB,
        "wires": wires,
    }

def dist2(a, b):
    """
    Squared distance between two fighter snapshots (for nearest-attacker calc). :contentReference[oaicite:9]{index=9}
    """
    if a is None or b is None:
        return float("inf")
    ax, ay = a.get("x"), a.get("y")
    bx, by = b.get("x"), b.get("y")
    if None in (ax, ay, bx, by):
        return float("inf")
    dx = ax - bx
    dy = ay - by
    return dx * dx + dy * dy
