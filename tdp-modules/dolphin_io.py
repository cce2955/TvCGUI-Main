# dolphin_io.py
# Dolphin hook + safe memory reads. :contentReference[oaicite:3]{index=3}

import time, math, struct
import dolphin_memory_engine as dme
from constants import MEM1_LO, MEM1_HI, MEM2_LO, MEM2_HI

def hook():
    """Block until dolphin_memory_engine attaches."""
    while not dme.is_hooked():
        try:
            dme.hook()
        except Exception:
            pass
        time.sleep(0.2)

def rd32(addr):
    """Read 32-bit unsigned int. Returns None on failure."""
    try:
        return dme.read_word(addr)
    except Exception:
        return None

def rd8(addr):
    """Read 8-bit unsigned int. Returns None on failure."""
    try:
        return dme.read_byte(addr)
    except Exception:
        return None

def rdf32(addr):
    """Read 32-bit float (big-endian). Returns None if invalid/unreasonable."""
    try:
        w = dme.read_word(addr)
        if w is None:
            return None
        f = struct.unpack(">f", struct.pack(">I", w))[0]
        if not math.isfinite(f) or abs(f) > 1e8:
            return None
        return f
    except Exception:
        return None

def addr_in_ram(a):
    """True if address lies within MEM1 or MEM2."""
    if a is None:
        return False
    return (MEM1_LO <= a < MEM1_HI) or (MEM2_LO <= a < MEM2_HI)
