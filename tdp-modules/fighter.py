# fighter.py
#
# Snapshot a single fighter's runtime struct (HP, position, states, etc.)
# and expose small debug "wire" windows for the HUD / Inspector.

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


def _safe_last_hit(raw_last_hit):
    """
    Normalize the 'last hit' value.

    The engine tracks last-hit damage in a field that can spike or drift.
    Filter out obviously bogus values so the HUD doesn't show garbage.
    """
    if raw_last_hit is None:
        return None
    if raw_last_hit < 0 or raw_last_hit > 200_000:
        return None
    return raw_last_hit


def _collect_wire_bytes(base_addr, offsets_list):
    """
    Read rd8(base + off) for each offset in offsets_list.

    Returns:
        list[(offset:int, value:int|None)]
    """
    out = []
    for off in offsets_list:
        b = rd8(base_addr + off)
        out.append((off, b))
    return out


def read_fighter(base, y_off):
    """
    Build a snapshot dict for a single fighter.

    The snapshot includes:
      - Health:
            max (OFF_MAX_HP)
            cur (OFF_CUR_HP)
            aux (OFF_AUX_HP)
            hp_pool_byte  (0x02A — pooled / red-life style total)
            mystery_2B    (0x02B — decremented "phase" byte under test)
      - Identity:
            id, name (resolved via CHAR_NAMES)
      - Position:
            x (POSX_OFF float)
            y (float at y_off, provided by resolver)
      - Hit / damage:
            last (sanitized OFF_LAST_HIT)
      - Control / state:
            ctrl (CTRL_WORD_OFF)
            f062, f063, f064, f072 (flag bytes)
      - Current attack IDs:
            attA, attB (from moves.read_attack_ids)
      - Debug wire windows:
            wires_hp   : bytes around HP block (HEALTH_WIRE_OFFSETS)
            wires_main : main control window (WIRE_OFFSETS)

    Returns:
        dict snapshot on success, or None if the struct fails basic HP validation.
    """
    if not base:
        return None

    # --- core health block ---
    max_hp = rd32(base + OFF_MAX_HP)
    cur_hp = rd32(base + OFF_CUR_HP)
    aux_hp = rd32(base + OFF_AUX_HP)

    # Reject bogus / uninitialized structs.
    if not looks_like_hp(max_hp, curhp=cur_hp, auxhp=aux_hp):
        return None

    # Extra health-related bytes for experimentation:
    #   0x02A: pooled "total life" (current + red) style byte.
    #   0x02B: odd decrementing byte that seems to behave like a timer/phase.
    pooled_byte = rd8(base + 0x02A)
    mystery_2B = rd8(base + 0x02B)

    # --- identity ---
    cid = rd32(base + OFF_CHAR_ID)
    name = CHAR_NAMES.get(cid, f"ID_{cid}") if cid is not None else "???"

    # --- position ---
    x = rdf32(base + POSX_OFF)
    y = rdf32(base + y_off) if y_off is not None else None

    # --- hit / damage info ---
    raw_last = rd32(base + OFF_LAST_HIT)
    last_hit = _safe_last_hit(raw_last)

    # --- control / state words ---
    ctrl_word = rd32(base + CTRL_WORD_OFF)

    f062 = rd8(base + FLAG_062)
    f063 = rd8(base + FLAG_063)
    f064 = rd8(base + FLAG_064)
    f072 = rd8(base + FLAG_072)

    # --- current attack IDs (engine seems to track two slots) ---
    attA, attB = read_attack_ids(base)

    # --- byte spy windows for HUD Inspector ---
    wires_hp = _collect_wire_bytes(base, HEALTH_WIRE_OFFSETS)
    wires_main = _collect_wire_bytes(base, WIRE_OFFSETS)

    # --- assemble snapshot dict ---
    snap = {
        "base": base,

        # health
        "max": max_hp,
        "cur": cur_hp,
        "aux": aux_hp,

        # pooled HP-style "total life" byte and experimental decay byte
        "hp_pool_byte": pooled_byte,  # offset 0x02A
        "mystery_2B": mystery_2B,     # offset 0x02B

        # char identity
        "id": cid,
        "name": name,

        # world position
        "x": x,
        "y": y,

        # last hit info
        "last": last_hit,

        # raw engine control words / states
        "ctrl": ctrl_word,
        "f062": f062,
        "f063": f063,
        "f064": f064,
        "f072": f072,

        # attack IDs
        "attA": attA,
        "attB": attB,

        # debug byte dumps for Inspector
        "wires_hp": wires_hp,
        "wires_main": wires_main,
    }

    return snap


def dist2(a, b):
    """
    Squared distance between two fighter snapshots.

    Used to pick a "nearest attacker" when multiple candidates
    are in range. If either snapshot is missing a usable X/Y,
    returns +inf so it loses any min-distance comparison.
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
