# dolphin_io.py
# Handles Dolphin hook and safe memory access utilities.
# All memory I/O goes through these helpers.

import time, math, struct
import dolphin_memory_engine as dme
from constants import MEM1_LO, MEM1_HI, MEM2_LO, MEM2_HI

# --------------------------------------------------------------------
# Hook into Dolphin (waits until emulator is ready)
# --------------------------------------------------------------------
def hook(poll_sec=0.2):
    """Blocks until dolphin_memory_engine successfully attaches."""
    while not dme.is_hooked():
        try:
            dme.hook()
        except Exception:
            pass
        time.sleep(poll_sec)

# --------------------------------------------------------------------
# Safe readers
# --------------------------------------------------------------------
def rd32(addr):
    """Read 32-bit unsigned int from guest memory. Returns None on failure."""
    try:
        return dme.read_word(addr)
    except Exception:
        return None


def rdf32(addr):
    """Read 32-bit float from guest memory. Returns None if invalid/out-of-range."""
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


def rbytes(addr, n):
    """Read raw bytes from guest memory."""
    try:
        b = dme.read_bytes(addr, n)
        return None if b is None else bytes(b)
    except Exception:
        return None


def addr_in_ram(a):
    """Return True if address lies within MEM1 or MEM2 ranges."""
    if a is None:
        return False
    return (MEM1_LO <= a < MEM1_HI) or (MEM2_LO <= a < MEM2_HI)
