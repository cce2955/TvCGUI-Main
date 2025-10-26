# fighter.py
# Snapshot a single fighter's runtime struct (HP, position, states, etc.)
# and expose small debug "wire" windows for HUD/Inspector.

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
    last_hit is a running damage tracker / timer-ish int that can spike weird.
    Clamp insane values so we don't spew garbage into HUD.
    """
    if raw_last_hit is None:
        return None
    if raw_last_hit < 0 or raw_last_hit > 200_000:
        return None
    return raw_last_hit


def _collect_wire_bytes(base_addr, offsets_list):
    """
    Helper: read rd8(base+off) for each given offset.
    Returns list[(off:int, byte_val:int|None)].
    """
    out = []
    for off in offsets_list:
        b = rd8(base_addr + off)
        out.append((off, b))
    return out


def read_fighter(base, y_off):
    """
    Snapshot one fighter into a dict of:
      - health (max/cur/aux)
      - pooled HP (0x02A) and mystery byte (0x02B) for debug
      - character ID/name
      - world position X/Y
      - control word + main state bytes (0x062,0x063,0x064,0x072)
      - current move IDs (attA/attB from moves.read_attack_ids)
      - debug wire dumps:
            "wires_hp":   bytes around HP block (0x020-0x03F)
            "wires_main": bytes in the classic control window (0x050-0x08F)

    Returns None if the struct doesn't validate as a live fighter.
    """

    if not base:
        return None

    # --- core health ---
    max_hp = rd32(base + OFF_MAX_HP)
    cur_hp = rd32(base + OFF_CUR_HP)
    aux_hp = rd32(base + OFF_AUX_HP)

    # Reject bogus / uninitialized structs.
    if not looks_like_hp(max_hp, curhp=cur_hp, auxhp=aux_hp):
        return None

    # NEW: pooled HP and mystery 0x02B byte
    # 0x02A: "pooled" survivable HP (red life+current etc.)
    # 0x02B: unknown decrementing timer/phase byte you described
    pooled_byte = rd8(base + 0x02A)
    mystery_2B  = rd8(base + 0x02B)

    # --- identity ---
    cid  = rd32(base + OFF_CHAR_ID)
    name = CHAR_NAMES.get(cid, f"ID_{cid}") if cid is not None else "???"

    # --- position ---
    x = rdf32(base + POSX_OFF)
    y = rdf32(base + y_off) if y_off is not None else None

    # --- hit / damage info ---
    raw_last  = rd32(base + OFF_LAST_HIT)
    last_hit  = _safe_last_hit(raw_last)

    # --- control / state words ---
    ctrl_word = rd32(base + CTRL_WORD_OFF)

    f062 = rd8(base + FLAG_062)
    f063 = rd8(base + FLAG_063)
    f064 = rd8(base + FLAG_064)
    f072 = rd8(base + FLAG_072)

    # --- current attack IDs (engine seems to track two slots) ---
    attA, attB = read_attack_ids(base)

    # --- byte spy windows for HUD Inspector ---
    wires_hp   = _collect_wire_bytes(base, HEALTH_WIRE_OFFSETS)  # 0x020..0x03F
    wires_main = _collect_wire_bytes(base, WIRE_OFFSETS)         # 0x050..0x08F

    # --- assemble snapshot dict ---
    snap = {
        "base": base,

        # health
        "max": max_hp,
        "cur": cur_hp,
        "aux": aux_hp,

        # pooled HP-style "total life" byte and mystery decay byte
        # We'll surface both in HUD.
        "hp_pool_byte": pooled_byte,   # offset 0x02A
        "mystery_2B":   mystery_2B,    # offset 0x02B

        # char identity
        "id": cid,
        "name": name,

        # position
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
        "wires_hp":   wires_hp,
        "wires_main": wires_main,
    }

    return snap


def dist2(a, b):
    """
    Squared distance between 2 fighter snapshots.
    Used to pick 'nearest attacker' on hit.
    If we can't get sane X/Y for either fighter, treat as inf distance.
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
