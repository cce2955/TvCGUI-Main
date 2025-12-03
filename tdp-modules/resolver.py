# resolver.py
#
# Minimal SlotResolver:
#   - Reads the raw slot pointer from SLOTS
#   - Accepts it if it lies in valid RAM
#   - No HP checks, no indirections, no TTL caching
#
# We keep pick_posy_off_no_jump for stable Y-offset selection.

import time, math
from dolphin_io import rd32, rdf32, addr_in_ram
from constants import POSX_OFF, POSY_CANDS


def _variance(vals):
    n = len(vals)
    if n < 2:
        return 0.0
    m = sum(vals) / n
    return sum((v - m) * (v - m) for v in vals) / (n - 1)


def _slope(vals):
    if len(vals) < 2:
        return 0.0
    return (vals[-1] - vals[0]) / (len(vals) - 1)


def _corr(a, b):
    n = len(a)
    if n < 2 or n != len(b):
        return 0.0
    ma = sum(a) / n
    mb = sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) * (x - ma) for x in a))
    db = math.sqrt(sum((y - mb) * (y - mb) for y in b))
    if da == 0 or db == 0:
        return 0.0
    return num / (da * db)


def pick_posy_off_no_jump(base):
    """
    Sample x and candidate y offsets for ~1 sec, choose the y offset
    that looks the most 'ground stable' relative to x. This avoids jumpy Y.
    """
    xs = []
    ys = {off: [] for off in POSY_CANDS}
    end = time.time() + 1.0
    while time.time() < end:
        x = rdf32(base + POSX_OFF)
        xs.append(x if x is not None else 0.0)
        for off in POSY_CANDS:
            y = rdf32(base + off)
            ys[off].append(y if y is not None else 0.0)
        time.sleep(1.0 / 120.0)

    if len(xs) < 10:
        return 0xF4  # fallback

    best_score = None
    best_off = None
    for off, series in ys.items():
        if len(series) < 10:
            continue
        s = abs(_slope(series))
        v = _variance(series)
        r = abs(_corr(series, xs))
        score = (0.6 * (1 / (1 + v))) + (0.3 * (1 / (1 + s))) + (0.1 * (1 / (1 + r)))
        if best_score is None or score > best_score:
            best_score = score
            best_off = off

    return best_off or 0xF4

def looks_like_hp(*args, **kwargs):
    return True

class SlotResolver:
    """
    Resolve each slot pointer (P1-C1, etc.) to an actual fighter base struct.

    Minimal behavior:
      - Read 32-bit pointer at slot_addr
      - If it's inside RAM, accept it and return (base, True)
      - If it's not, return (None, True)

    We deliberately do *not* gate on HP or any other heuristics here.
    """

    def __init__(self):
        pass

    def resolve_base(self, slot_addr):
        """
        Returns (base_addr or None, changed_this_frame: bool).

        For now, changed_this_frame is always True, which is fine for
        the HUD usage: it just means "recompute stuff when the value
        changes or disappears".
        """
        s = rd32(slot_addr)
        if s is None:
            return None, True

        if addr_in_ram(s):
            return s, True

        return None, True


RESOLVER = SlotResolver()
