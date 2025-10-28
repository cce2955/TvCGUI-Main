# dolphin_io.py
#
# Thin wrapper around dolphin_memory_engine so we can safely
# read Dolphin's emulated Wii memory (MEM1 + MEM2).
#
# Exposes:
#   hook()               - block until Dolphin is hooked
#   addr_in_ram(a)       - True if address in MEM1 or MEM2
#   rbytes(addr, size)   - bulk read, returns bytes
#   rd8(addr)            - read 8-bit unsigned, returns int 0..255 or None
#   rd32(addr)           - read 32-bit BE unsigned
#   rdf32(addr)          - read 32-bit BE float

import time
import math
import struct
import dolphin_memory_engine as dme
from constants import MEM1_LO, MEM1_HI, MEM2_LO, MEM2_HI


def hook():
    """Keep trying to hook Dolphin until it succeeds."""
    while not dme.is_hooked():
        try:
            dme.hook()
        except Exception:
            pass
        time.sleep(0.2)


def addr_in_ram(a):
    """Return True if 'a' is inside MEM1 or MEM2."""
    if a is None:
        return False
    return (MEM1_LO <= a < MEM1_HI) or (MEM2_LO <= a < MEM2_HI)


def _clamp_read_range(addr, size):
    """
    Return (ok, clamped_addr, clamped_size) for a safe read in MEM1 or MEM2.
    """
    if size <= 0:
        return False, addr, 0

    # MEM1
    if MEM1_LO <= addr < MEM1_HI:
        hi_allowed = min(addr + size, MEM1_HI)
        return True, addr, hi_allowed - addr

    # MEM2
    if MEM2_LO <= addr < MEM2_HI:
        hi_allowed = min(addr + size, MEM2_HI)
        return True, addr, hi_allowed - addr

    return False, addr, 0


def rbytes(addr, size):
    """
    Bulk read 'size' bytes starting at 'addr'.
    Returns b"" on failure.
    """
    ok, base, span = _clamp_read_range(addr, size)
    if not ok or span <= 0:
        return b""

    try:
        data = dme.read_bytes(base, span)
        if not data:
            return b""
        return data
    except Exception:
        return b""


def rd8(addr):
    """Read 1 byte from 'addr' -> int(0..255) or None."""
    if not addr_in_ram(addr):
        return None
    try:
        b = dme.read_bytes(addr, 1)
        if not b or len(b) != 1:
            return None
        return b[0]
    except Exception:
        return None


def rd32(addr):
    """Read big-endian u32 from 'addr' or None."""
    if not addr_in_ram(addr):
        return None
    try:
        b = dme.read_bytes(addr, 4)
        if not b or len(b) != 4:
            return None
        return struct.unpack(">I", b)[0]
    except Exception:
        return None


def rdf32(addr):
    """Read big-endian float32 from 'addr' or None."""
    if not addr_in_ram(addr):
        return None
    try:
        b = dme.read_bytes(addr, 4)
        if not b or len(b) != 4:
            return None

        raw_u32 = struct.unpack(">I", b)[0]
        f = struct.unpack(">f", struct.pack(">I", raw_u32))[0]

        # sanity filter
        if not math.isfinite(f) or abs(f) > 1e8:
            return None
        return f
    except Exception:
        return None
