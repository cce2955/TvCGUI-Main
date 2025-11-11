# move_writer.py
#
# Write move data back to TvC memory.
# This is the module the editable frame data GUI expects.
#
# NOTE:
# - Must live in the same environment as dolphin_io
# - mv dicts are expected to come directly from scan_normals_all
# - Every write is defensive: if the address is missing, it logs and returns False

from dolphin_io import wd8, wd32, wdf32, wbytes  # type: ignore
import struct

# ============================================================
# OFFSET CONSTANTS (must match how scan_normals_all finds blocks)
# ============================================================

# Meter: base = mv["meter_addr"]
# actual meter byte is at base + 24
METER_VALUE_OFFSET = 24

# Active frames: base = mv["active_addr"]
# active_start at base + 8, active_end at base + 16
ACTIVE_START_OFFSET = 8
ACTIVE_END_OFFSET = 16

# Damage: base = mv["damage_addr"]
# damage is 3 bytes at base + 5, +6, +7
DAMAGE_VALUE_OFFSET = 5

# Attack property: base = mv["atkprop_addr"]
# property byte at base + 15
ATKPROP_VALUE_OFFSET = 15

# Knockback: base = mv["knockback_addr"]
# kb0 at +1, kb1 at +2, trajectory at +12
KNOCKBACK_KB0_OFFSET = 1
KNOCKBACK_KB1_OFFSET = 2
KNOCKBACK_TRAJ_OFFSET = 12

# Stuns: base = mv["stun_addr"]
# hitstun at +15, blockstun at +31, hitstop at +38
STUN_HITSTUN_OFFSET = 15
STUN_BLOCKSTUN_OFFSET = 31
STUN_HITSTOP_OFFSET = 38

# Hitbox size: taken directly off the move base address mv["abs"]
HITBOX_X_OFFSET = 0x40
HITBOX_Y_OFFSET = 0x48


def _has_key(mv: dict, key: str) -> bool:
    if key not in mv or mv[key] is None:
        print(f"[move_writer] move has no '{key}', cannot write")
        return False
    return True


def write_damage(mv: dict, new_damage: int) -> bool:
    """Write a 3-byte damage value to the move's damage block."""
    if not _has_key(mv, "damage_addr"):
        return False

    addr = mv["damage_addr"] + DAMAGE_VALUE_OFFSET
    try:
        val = int(new_damage) & 0xFFFFFF
        b0 = (val >> 16) & 0xFF
        b1 = (val >> 8) & 0xFF
        b2 = val & 0xFF

        ok = wd8(addr, b0) and wd8(addr + 1, b1) and wd8(addr + 2, b2)
        if ok:
            print(f"[move_writer] wrote damage {new_damage} to {addr:08X}")
        return ok
    except Exception as e:
        print(f"[move_writer] write_damage failed: {e}")
        return False


def write_meter(mv: dict, new_meter: int) -> bool:
    """Write meter cost (1 byte)."""
    if not _has_key(mv, "meter_addr"):
        return False

    addr = mv["meter_addr"] + METER_VALUE_OFFSET
    try:
        val = int(new_meter) & 0xFF
        ok = wd8(addr, val)
        if ok:
            print(f"[move_writer] wrote meter {new_meter} to {addr:08X}")
        return ok
    except Exception as e:
        print(f"[move_writer] write_meter failed: {e}")
        return False


def write_active_frames(mv: dict, new_start: int, new_end: int) -> bool:
    """
    Write active frame start/end.
    In TvC data these are usually stored as (value - 1).
    """
    if not _has_key(mv, "active_addr"):
        return False

    try:
        addr_start = mv["active_addr"] + ACTIVE_START_OFFSET
        addr_end = mv["active_addr"] + ACTIVE_END_OFFSET

        v_start = (int(new_start) - 1) & 0xFF
        v_end = (int(new_end) - 1) & 0xFF

        ok = wd8(addr_start, v_start) and wd8(addr_end, v_end)
        if ok:
            print(f"[move_writer] wrote active {new_start}-{new_end} to {addr_start:08X}/{addr_end:08X}")
        return ok
    except Exception as e:
        print(f"[move_writer] write_active_frames failed: {e}")
        return False


def write_hitstun(mv: dict, new_hitstun: int) -> bool:
    """Write hitstun byte."""
    if not _has_key(mv, "stun_addr"):
        return False

    addr = mv["stun_addr"] + STUN_HITSTUN_OFFSET
    try:
        val = int(new_hitstun) & 0xFF
        ok = wd8(addr, val)
        if ok:
            print(f"[move_writer] wrote hitstun {new_hitstun} to {addr:08X}")
        return ok
    except Exception as e:
        print(f"[move_writer] write_hitstun failed: {e}")
        return False


def write_blockstun(mv: dict, new_blockstun: int) -> bool:
    """Write blockstun byte."""
    if not _has_key(mv, "stun_addr"):
        return False

    addr = mv["stun_addr"] + STUN_BLOCKSTUN_OFFSET
    try:
        val = int(new_blockstun) & 0xFF
        ok = wd8(addr, val)
        if ok:
            print(f"[move_writer] wrote blockstun {new_blockstun} to {addr:08X}")
        return ok
    except Exception as e:
        print(f"[move_writer] write_blockstun failed: {e}")
        return False


def write_hitstop(mv: dict, new_hitstop: int) -> bool:
    """Write hitstop byte."""
    if not _has_key(mv, "stun_addr"):
        return False

    addr = mv["stun_addr"] + STUN_HITSTOP_OFFSET
    try:
        val = int(new_hitstop) & 0xFF
        ok = wd8(addr, val)
        if ok:
            print(f"[move_writer] wrote hitstop {new_hitstop} to {addr:08X}")
        return ok
    except Exception as e:
        print(f"[move_writer] write_hitstop failed: {e}")
        return False


def write_knockback(mv: dict, kb0=None, kb1=None, traj=None) -> bool:
    """Write knockback fields if present."""
    if not _has_key(mv, "knockback_addr"):
        return False

    base = mv["knockback_addr"]
    ok = True
    try:
        if kb0 is not None:
            val = int(kb0) & 0xFF
            ok = ok and wd8(base + KNOCKBACK_KB0_OFFSET, val)
            print(f"[move_writer] wrote kb0 {kb0} to {(base + KNOCKBACK_KB0_OFFSET):08X}")
        if kb1 is not None:
            val = int(kb1) & 0xFF
            ok = ok and wd8(base + KNOCKBACK_KB1_OFFSET, val)
            print(f"[move_writer] wrote kb1 {kb1} to {(base + KNOCKBACK_KB1_OFFSET):08X}")
        if traj is not None:
            val = int(traj) & 0xFF
            ok = ok and wd8(base + KNOCKBACK_TRAJ_OFFSET, val)
            print(f"[move_writer] wrote traj {traj} to {(base + KNOCKBACK_TRAJ_OFFSET):08X}")
        return ok
    except Exception as e:
        print(f"[move_writer] write_knockback failed: {e}")
        return False


def write_hitbox_size(mv: dict, hb_x=None, hb_y=None) -> bool:
    """Write hitbox x/y (floats) relative to mv['abs']."""
    if not _has_key(mv, "abs"):
        return False

    base = mv["abs"]
    ok = True
    try:
        if hb_x is not None:
            ok = ok and wdf32(base + HITBOX_X_OFFSET, float(hb_x))
            print(f"[move_writer] wrote HBx {hb_x} to {(base + HITBOX_X_OFFSET):08X}")
        if hb_y is not None:
            ok = ok and wdf32(base + HITBOX_Y_OFFSET, float(hb_y))
            print(f"[move_writer] wrote HBy {hb_y} to {(base + HITBOX_Y_OFFSET):08X}")
        return ok
    except Exception as e:
        print(f"[move_writer] write_hitbox_size failed: {e}")
        return False


def write_attack_property(mv: dict, new_prop: int) -> bool:
    """Write attack property byte."""
    if not _has_key(mv, "atkprop_addr"):
        return False

    addr = mv["atkprop_addr"] + ATKPROP_VALUE_OFFSET
    try:
        val = int(new_prop) & 0xFF
        ok = wd8(addr, val)
        if ok:
            print(f"[move_writer] wrote atkprop {new_prop} to {addr:08X}")
        return ok
    except Exception as e:
        print(f"[move_writer] write_attack_property failed: {e}")
        return False
