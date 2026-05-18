# move_writer.py
#
# Write move data back to TvC memory.
# Dynamic HB: if mv has "hb_off", use that; else fall back to 0x21C.
# Active frames: always force end >= start.

from dolphin_io import wd8, wdf32

# standard offsets we already know
# meter_addr is now stored as the direct editable value byte by scan_normals_all.collect_blocks.
METER_VALUE_OFFSET = 0
ACTIVE_START_OFFSET = 8
ACTIVE_END_OFFSET   = 16
DAMAGE_VALUE_OFFSET = 5
ATKPROP_VALUE_OFFSET = 15
KNOCKBACK_TYPE_OFFSET    = 1
KNOCKBACK_PROFILE_OFFSET = 4  # recovery/fall profile after hit reaction
KNOCKBACK_UNKNOWN_OFFSET = 8
KNOCKBACK_X_OFFSET       = 12
KNOCKBACK_AIR_OFFSET     = 16
STUN_HITSTUN_OFFSET   = 15
STUN_BLOCKSTUN_OFFSET = 31
STUN_HITSTOP_OFFSET   = 38

# fallback HB offset (confirmed on Ryu 5A)
FALLBACK_HB_OFFSET = 0x21C


def _has(mv: dict, key: str) -> bool:
    return key in mv and mv[key] is not None



def _wd32_be(addr: int, value: int) -> bool:
    """Write a 32-bit big-endian integer using wd8 only.

    Some unit-test stubs expose wd8/wdf32 but not wd32, so keep move_writer
    independent of dolphin_io.wd32.
    """
    val = int(value) & 0xFFFFFFFF
    return (
        wd8(addr, (val >> 24) & 0xFF)
        and wd8(addr + 1, (val >> 16) & 0xFF)
        and wd8(addr + 2, (val >> 8) & 0xFF)
        and wd8(addr + 3, val & 0xFF)
    )

def write_damage(mv: dict, new_damage: int) -> bool:
    if not _has(mv, "damage_addr"):
        return False
    addr = mv["damage_addr"] + DAMAGE_VALUE_OFFSET
    try:
        val = int(new_damage) & 0xFFFFFF
        b0 = (val >> 16) & 0xFF
        b1 = (val >> 8) & 0xFF
        b2 = val & 0xFF
        ok = wd8(addr, b0) and wd8(addr + 1, b1) and wd8(addr + 2, b2)
        if ok:
            print(f"[move_writer] wrote damage {new_damage} @ {addr:08X}")
        return ok
    except Exception as e:
        print("[move_writer] write_damage failed:", e)
        return False


def write_meter(mv: dict, new_meter: int) -> bool:
    if not _has(mv, "meter_addr"):
        return False
    addr = mv["meter_addr"] + METER_VALUE_OFFSET
    try:
        val = int(new_meter) & 0xFF
        ok = wd8(addr, val)
        if ok:
            print(f"[move_writer] wrote meter {new_meter} @ {addr:08X}")
        return ok
    except Exception as e:
        print("[move_writer] write_meter failed:", e)
        return False


def write_active_frames(mv: dict, new_start: int, new_end: int) -> bool:
    if not _has(mv, "active_addr"):
        return False
    try:
        s = int(new_start)
        e = int(new_end)
        if e < s:
            e = s

        addr_s = mv["active_addr"] + ACTIVE_START_OFFSET
        addr_e = mv["active_addr"] + ACTIVE_END_OFFSET

        ok = wd8(addr_s, (s - 1) & 0xFF)
        ok = ok and wd8(addr_e, (e - 1) & 0xFF)

        if ok:
            print(f"[move_writer] wrote active {s}-{e} @ {addr_s:08X}/{addr_e:08X}")
        return ok
    except Exception as e:
        print("[move_writer] write_active_frames failed:", e)
        return False


def write_hitstun(mv: dict, new_hitstun: int) -> bool:
    if not _has(mv, "stun_addr"):
        return False
    addr = mv["stun_addr"] + STUN_HITSTUN_OFFSET
    try:
        val = int(new_hitstun) & 0xFF
        ok = wd8(addr, val)
        if ok:
            print(f"[move_writer] wrote hitstun {new_hitstun} @ {addr:08X}")
        return ok
    except Exception as e:
        print("[move_writer] write_hitstun failed:", e)
        return False


def write_blockstun(mv: dict, new_blockstun: int) -> bool:
    if not _has(mv, "stun_addr"):
        return False
    addr = mv["stun_addr"] + STUN_BLOCKSTUN_OFFSET
    try:
        val = int(new_blockstun) & 0xFF
        ok = wd8(addr, val)
        if ok:
            print(f"[move_writer] wrote blockstun {new_blockstun} @ {addr:08X}")
        return ok
    except Exception as e:
        print("[move_writer] write_blockstun failed:", e)
        return False


def write_hitstop(mv: dict, new_hitstop: int) -> bool:
    if not _has(mv, "stun_addr"):
        return False
    addr = mv["stun_addr"] + STUN_HITSTOP_OFFSET
    try:
        val = int(new_hitstop) & 0xFF
        ok = wd8(addr, val)
        if ok:
            print(f"[move_writer] wrote hitstop {new_hitstop} @ {addr:08X}")
        return ok
    except Exception as e:
        print("[move_writer] write_hitstop failed:", e)
        return False


def write_knockback(
    mv: dict,
    launch_profile=None,
    kb_x=None,
    air_kb=None,
    kb_type=None,
    kb_unknown=None,
) -> bool:
    """Write the confirmed KB/launch packet.

    Packet layout:
      +0x01 u8  = packet type (normally 0x07 or 0x09)
      +0x04 u32 = recovery/fall profile after the hit reaction
      +0x08 u32 = unknown/unused word
      +0x0C f32 = KB X for grounded/standing hits
      +0x10 f32 = arc / Air KB / relaunch behavior
    """
    if not _has(mv, "knockback_addr"):
        return False
    base = mv["knockback_addr"]
    ok = True
    try:
        if kb_type is not None:
            ok = ok and wd8(base + KNOCKBACK_TYPE_OFFSET, int(kb_type) & 0xFF)
        if launch_profile is not None:
            ok = ok and _wd32_be(base + KNOCKBACK_PROFILE_OFFSET, int(launch_profile) & 0xFFFFFFFF)
        if kb_unknown is not None:
            ok = ok and _wd32_be(base + KNOCKBACK_UNKNOWN_OFFSET, int(kb_unknown) & 0xFFFFFFFF)
        if kb_x is not None:
            ok = ok and wdf32(base + KNOCKBACK_X_OFFSET, float(kb_x))
        if air_kb is not None:
            ok = ok and wdf32(base + KNOCKBACK_AIR_OFFSET, float(air_kb))
        if ok:
            print(f"[move_writer] wrote knockback packet @ {base:08X}")
        return ok
    except Exception as e:
        print("[move_writer] write_knockback failed:", e)
        return False


def write_hitbox_radius(mv: dict, radius: float) -> bool:
    if not _has(mv, "abs"):
        return False
    # per-move offset if we discovered it in the GUI
    off = mv.get("hb_off", FALLBACK_HB_OFFSET)
    addr = mv["abs"] + off
    try:
        r = float(radius)
        ok = wdf32(addr, r)
        if ok:
            print(f"[move_writer] wrote HB radius {r} @ {addr:08X} (off={off:#x})")
        return ok
    except Exception as e:
        print("[move_writer] write_hitbox_radius failed:", e)
        return False


def write_attack_property(mv: dict, new_prop: int) -> bool:
    if not _has(mv, "atkprop_addr"):
        return False
    addr = mv["atkprop_addr"] + ATKPROP_VALUE_OFFSET
    try:
        val = int(new_prop) & 0xFF
        ok = wd8(addr, val)
        if ok:
            print(f"[move_writer] wrote attack property {new_prop} @ {addr:08X}")
        return ok
    except Exception as e:
        print("[move_writer] write_attack_property failed:", e)
        return False
