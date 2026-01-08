# fd_write_helpers.py
#
# Small write helpers used by the GUI. Keeps fd_window.py cleaner.

from __future__ import annotations

from fd_patterns import SUPERBG_ON, SUPERBG_OFF

from typing import Optional
def write_hit_reaction_inline(mv: dict, val: int, WRITER_AVAILABLE: bool) -> bool:
    if not WRITER_AVAILABLE:
        return False

    # Prefer move_writer helper if present
    try:
        from move_writer import write_hit_reaction
        if write_hit_reaction(mv, val):
            return True
    except Exception:
        pass

    addr = mv.get("hit_reaction_addr")
    if not addr:
        return False

    try:
        from dolphin_io import wd8
        wd8(addr + 0, (val >> 16) & 0xFF)
        wd8(addr + 1, (val >> 8) & 0xFF)
        wd8(addr + 2, val & 0xFF)
        return True
    except Exception:
        return False

def write_speed_mod_inline(mv: dict, new_val: int, writer_available: bool) -> bool:
    """
    Writes the 'speed modifier' byte to its resolved absolute address.

    Requires:
      mv["speed_mod_addr"] = absolute address of the byte
    Sets:
      mv["speed_mod"] = new_val (0-255)
    """
    if not writer_available:
        return False

    addr = mv.get("speed_mod_addr")
    if not addr:
        return False

    try:
        from dolphin_io import wd8
    except Exception:
        return False

    try:
        b = int(new_val) & 0xFF
        ok = bool(wd8(addr, b))
        if ok:
            mv["speed_mod"] = b
        return ok
    except Exception as e:
        print(f"write_speed_mod_inline failed @0x{addr:08X}: {e}")
def write_speed_mod_inline(mv: dict, new_val: int, writer_available: bool) -> bool:
    """
    Writes the 'speed modifier' byte to its resolved absolute address.

    Requires:
      mv["speed_mod_addr"] = absolute address of the byte
    Sets:
      mv["speed_mod"] = new_val (0-255)
    """
    if not writer_available:
        return False

    addr = mv.get("speed_mod_addr")
    if not addr:
        return False

    try:
        from dolphin_io import wd8
    except Exception:
        return False

    try:
        b = int(new_val) & 0xFF
        ok = bool(wd8(addr, b))
        if ok:
            mv["speed_mod"] = b
        return ok
    except Exception as e:
        print(f"write_speed_mod_inline failed @0x{addr:08X}: {e}")
        return False


def write_active2_frames_inline(mv: dict, start: int, end: int, WRITER_AVAILABLE: bool) -> bool:
    if not WRITER_AVAILABLE:
        return False
    addr = mv.get("active2_addr")
    if not addr:
        return False
    try:
        from dolphin_io import wd8
        if not wd8(addr + 4, start):
            return False
        if not wd8(addr + 16, end):
            return False
        return True
    except Exception:
        return False


def write_anim_id_inline(mv: dict, new_anim_id: int, WRITER_AVAILABLE: bool) -> bool:
    if not WRITER_AVAILABLE:
        return False

    base = mv.get("abs")
    if not base:
        return False

    try:
        from dolphin_io import rbytes, wd8
    except ImportError:
        return False

    LOOKAHEAD = 0x80

    try:
        buf = rbytes(base, LOOKAHEAD)
    except Exception:
        return False

    target_off = None
    for i in range(0, len(buf) - 4):
        b0, b2, b3 = buf[i], buf[i + 2], buf[i + 3]
        if b0 == 0x01 and b3 == 0x3C and b2 == 0x01:
            target_off = i
            break

    if target_off is None:
        return False

    addr = base + target_off + 1
    new_hi = (new_anim_id >> 8) & 0xFF
    new_lo = new_anim_id & 0xFF

    try:
        return bool(wd8(addr, new_hi) and wd8(addr + 1, new_lo))
    except Exception:
        return False


def write_combo_kb_mod_inline(mv: dict, new_val: int, WRITER_AVAILABLE: bool) -> bool:
    if not WRITER_AVAILABLE:
        return False

    addr = mv.get("combo_kb_mod_addr")
    if not addr:
        return False

    try:
        from dolphin_io import wd8
        new_val = int(new_val) & 0xFF
        return bool(wd8(addr, new_val))
    except Exception:
        return False


def write_superbg_inline(mv: dict, enabled: bool, WRITER_AVAILABLE: bool) -> bool:
    if not WRITER_AVAILABLE:
        return False

    addr = mv.get("superbg_addr")
    if not addr:
        return False

    # User-verified toggle values are 0x01 <-> 0x04
    val = 0x04 if enabled else 0x01

    try:
        from dolphin_io import wd8
        ok = bool(wd8(addr, val))
        if ok:
            mv["superbg_val"] = val
        return ok
    except Exception:
        return False

def write_proj_dmg_inline(mv: dict, new_val: int, writer_available: bool) -> bool:
    """
    Writes projectile damage at mv["proj_tpl"] which points to the u32 word: 00 00 XX YY (big-endian).
    We keep the top halfword 0 and write XX YY from new_val (0..65535).
    """
    if not writer_available:
        return False

    addr = mv.get("proj_tpl")
    if addr is None:
        return False

    try:
        addr = int(addr, 16) if isinstance(addr, str) and addr.lower().startswith("0x") else int(addr)
    except Exception:
        return False

    try:
        from dolphin_io import wd8
    except Exception:
        return False

    try:
        v = int(new_val)
        if v < 0:
            v = 0
        if v > 0xFFFF:
            v = 0xFFFF

        hi = (v >> 8) & 0xFF
        lo = v & 0xFF

        # 00 00 XX YY
        if not wd8(addr + 0, 0x00):
            return False
        if not wd8(addr + 1, 0x00):
            return False
        if not wd8(addr + 2, hi):
            return False
        if not wd8(addr + 3, lo):
            return False

        mv["proj_dmg"] = v
        mv["proj_tpl"] = addr
        return True
    except Exception:
        return False
