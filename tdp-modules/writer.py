# move_writer.py
#
# Write move data back to TvC memory.
# Based on the patterns from scan_normals_all.py

from dolphin_io import wd8, wd32, wdf32, wbytes
import struct

# ============================================================
# OFFSET CONSTANTS (from scan_normals_all.py patterns)
# ============================================================

# METER: relative to meter block start
# Pattern: [0x34, 0x04, 0x00, 0x20, 0x00, 0x00, 0x00, 0x03...]
# Meter value is at offset +len(METER_HDR) = +24
METER_VALUE_OFFSET = 24

# ACTIVE FRAMES: relative to active block start
# Pattern: [0x20, 0x35, 0x01, 0x20, 0x3F, 0x00, 0x00, 0x00]
# active_start at offset +8, active_end at offset +16
ACTIVE_START_OFFSET = 8
ACTIVE_END_OFFSET = 16

# DAMAGE: relative to damage block start
# Pattern: [0x35, 0x10, 0x20, 0x3F, 0x00]
# Damage is 3 bytes at offset +5, +6, +7
DAMAGE_VALUE_OFFSET = 5

# ATTACK PROPERTY: relative to atkprop block start
# Pattern: [0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x40, 0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00]
# Value at offset +15
ATKPROP_VALUE_OFFSET = 15

# KNOCKBACK: relative to knockback block start
# Pattern: [0x35, None, None, 0x20, ...]
# kb0 at +1, kb1 at +2, trajectory at +12
KNOCKBACK_KB0_OFFSET = 1
KNOCKBACK_KB1_OFFSET = 2
KNOCKBACK_TRAJ_OFFSET = 12

# STUNS: relative to stun block start
# Pattern: long pattern, see scan_normals_all.py
# hitstun at +15, blockstun at +31, hitstop at +38
STUN_HITSTUN_OFFSET = 15
STUN_BLOCKSTUN_OFFSET = 31
STUN_HITSTOP_OFFSET = 38

# HITBOX: relative to move start (mv["abs"])
HITBOX_X_OFFSET = 0x40
HITBOX_Y_OFFSET = 0x48

# ANIMATION SPEED: this is trickier, stored separately
# We'll need to find the speed block address for the specific anim_id


# ============================================================
# WRITE FUNCTIONS
# ============================================================

def write_damage(mv, new_damage):
    """
    Write damage value (3-byte int) to memory.
    mv must have 'damage_addr' key pointing to the damage block.
    """
    if "damage_addr" not in mv:
        print(f"  No damage_addr for this move")
        return False
    
    addr = mv["damage_addr"] + DAMAGE_VALUE_OFFSET
    try:
        val = int(new_damage) & 0xFFFFFF
        b0 = (val >> 16) & 0xFF
        b1 = (val >> 8) & 0xFF
        b2 = val & 0xFF
        
        success = wd8(addr, b0) and wd8(addr + 1, b1) and wd8(addr + 2, b2)
        if success:
            print(f"  Wrote damage {new_damage} to {addr:08X}")
        return success
    except Exception as e:
        print(f"  write_damage failed: {e}")
        return False


def write_meter(mv, new_meter):
    """Write meter cost to memory."""
    if "meter_addr" not in mv:
        print(f"  No meter_addr for this move")
        return False
    
    addr = mv["meter_addr"] + METER_VALUE_OFFSET
    try:
        val = int(new_meter) & 0xFF
        success = wd8(addr, val)
        if success:
            print(f"  Wrote meter {new_meter} to {addr:08X}")
        return success
    except Exception as e:
        print(f"  write_meter failed: {e}")
        return False


def write_active_frames(mv, new_start, new_end):
    """Write active frame start/end to memory."""
    if "active_addr" not in mv:
        print(f"  No active_addr for this move")
        return False
    
    try:
        addr_start = mv["active_addr"] + ACTIVE_START_OFFSET
        addr_end = mv["active_addr"] + ACTIVE_END_OFFSET
        
        # These are stored as (value - 1) in memory
        val_start = (int(new_start) - 1) & 0xFF
        val_end = (int(new_end) - 1) & 0xFF
        
        success = wd8(addr_start, val_start) and wd8(addr_end, val_end)
        if success:
            print(f"  Wrote active frames {new_start}-{new_end} to {addr_start:08X}")
        return success
    except Exception as e:
        print(f"  write_active_frames failed: {e}")
        return False


def write_hitstun(mv, new_hitstun):
    """Write hitstun value to memory."""
    if "stun_addr" not in mv:
        print(f"  No stun_addr for this move")
        return False
    
    addr = mv["stun_addr"] + STUN_HITSTUN_OFFSET
    try:
        val = int(new_hitstun) & 0xFF
        success = wd8(addr, val)
        if success:
            print(f"  Wrote hitstun {new_hitstun} to {addr:08X}")
        return success
    except Exception as e:
        print(f"  write_hitstun failed: {e}")
        return False


def write_blockstun(mv, new_blockstun):
    """Write blockstun value to memory."""
    if "stun_addr" not in mv:
        print(f"  No stun_addr for this move")
        return False
    
    addr = mv["stun_addr"] + STUN_BLOCKSTUN_OFFSET
    try:
        val = int(new_blockstun) & 0xFF
        success = wd8(addr, val)
        if success:
            print(f"  Wrote blockstun {new_blockstun} to {addr:08X}")
        return success
    except Exception as e:
        print(f"  write_blockstun failed: {e}")
        return False


def write_hitstop(mv, new_hitstop):
    """Write hitstop value to memory."""
    if "stun_addr" not in mv:
        print(f"  No stun_addr for this move")
        return False
    
    addr = mv["stun_addr"] + STUN_HITSTOP_OFFSET
    try:
        val = int(new_hitstop) & 0xFF
        success = wd8(addr, val)
        if success:
            print(f"  Wrote hitstop {new_hitstop} to {addr:08X}")
        return success
    except Exception as e:
        print(f"  write_hitstop failed: {e}")
        return False


def write_knockback(mv, kb0=None, kb1=None, traj=None):
    """Write knockback values to memory."""
    if "knockback_addr" not in mv:
        print(f"  No knockback_addr for this move")
        return False
    
    try:
        success = True
        base = mv["knockback_addr"]
        
        if kb0 is not None:
            addr = base + KNOCKBACK_KB0_OFFSET
            val = int(kb0) & 0xFF
            success = success and wd8(addr, val)
            if success:
                print(f"  Wrote kb0 {kb0} to {addr:08X}")
        
        if kb1 is not None:
            addr = base + KNOCKBACK_KB1_OFFSET
            val = int(kb1) & 0xFF
            success = success and wd8(addr, val)
            if success:
                print(f"  Wrote kb1 {kb1} to {addr:08X}")
        
        if traj is not None:
            addr = base + KNOCKBACK_TRAJ_OFFSET
            val = int(traj) & 0xFF
            success = success and wd8(addr, val)
            if success:
                print(f"  Wrote trajectory {traj} to {addr:08X}")
        
        return success
    except Exception as e:
        print(f"  write_knockback failed: {e}")
        return False


def write_hitbox_size(mv, hb_x=None, hb_y=None):
    """Write hitbox sizes (floats) to memory."""
    try:
        success = True
        base = mv["abs"]
        
        if hb_x is not None:
            addr = base + HITBOX_X_OFFSET
            success = success and wdf32(addr, float(hb_x))
            if success:
                print(f"  Wrote hitbox X {hb_x} to {addr:08X}")
        
        if hb_y is not None:
            addr = base + HITBOX_Y_OFFSET
            success = success and wdf32(addr, float(hb_y))
            if success:
                print(f"  Wrote hitbox Y {hb_y} to {addr:08X}")
        
        return success
    except Exception as e:
        print(f"  write_hitbox_size failed: {e}")
        return False


def write_attack_property(mv, new_prop):
    """Write attack property byte to memory."""
    if "atkprop_addr" not in mv:
        print(f"  No atkprop_addr for this move")
        return False
    
    addr = mv["atkprop_addr"] + ATKPROP_VALUE_OFFSET
    try:
        val = int(new_prop) & 0xFF
        success = wd8(addr, val)
        if success:
            print(f"  Wrote attack property {new_prop} to {addr:08X}")
        return success
    except Exception as e:
        print(f"  write_attack_property failed: {e}")
        return False