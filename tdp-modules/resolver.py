# resolver.py
# SlotResolver and Y-offset sampler. :contentReference[oaicite:4]{index=4}

import time, math
from dolphin_io import rd32, rdf32, addr_in_ram
from constants import (
    OFF_MAX_HP, OFF_CUR_HP, OFF_AUX_HP,
    POSX_OFF, POSY_CANDS,
    BAD_PTRS, INDIR_PROBES, LAST_GOOD_TTL,
)
# looks_like_hp is used by resolver and fighter

def looks_like_hp(maxhp, curhp, auxhp):
    if maxhp is None or curhp is None:
        return False
    if not (10_000 <= maxhp <= 60_000):
        return False
    if not (0 <= curhp <= maxhp):
        return False
    if auxhp is not None and not (0 <= auxhp <= maxhp):
        return False
    return True

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

class SlotResolver:
    """
    Resolve each slot pointer (P1-C1, etc.) to an actual fighter base struct.
    Caches last_good for stability. Uses probes if first-level pointer isn't HP-looking.
    """

    def __init__(self):
        # slot_addr -> (base, ttl_expiration)
        self.last_good = {}

    def _probe(self, slot_val):
        # Try following known indirections.
        for off in INDIR_PROBES:
            a = rd32(slot_val + off)
            if addr_in_ram(a) and a not in BAD_PTRS:
                mh = rd32(a + OFF_MAX_HP)
                ch = rd32(a + OFF_CUR_HP)
                ax = rd32(a + OFF_AUX_HP)
                if looks_like_hp(mh, ch, ax):
                    return a
        return None

    def resolve_base(self, slot_addr):
        """
        Returns (base_addr or None, changed_this_frame: bool).
        changed_this_frame is True if we found a new base vs last_known.
        """
        now = time.time()
        s = rd32(slot_addr)

        # If pointer is junk, maybe reuse last good
        if not addr_in_ram(s) or s in BAD_PTRS:
            lg = self.last_good.get(slot_addr)
            if lg and now < lg[1]:
                return lg[0], False
            return None, False

        # Check if s looks like a fighter struct
        mh = rd32(s + OFF_MAX_HP)
        ch = rd32(s + OFF_CUR_HP)
        ax = rd32(s + OFF_AUX_HP)
        if looks_like_hp(mh, ch, ax):
            self.last_good[slot_addr] = (s, now + LAST_GOOD_TTL)
            return s, True

        # Try indirect children
        a = self._probe(s)
        if a:
            self.last_good[slot_addr] = (a, now + LAST_GOOD_TTL)
            return a, True

        # Fall back on cache ttl if we can't confirm new
        lg = self.last_good.get(slot_addr)
        if lg and now < lg[1]:
            return lg[0], False

        return None, False

RESOLVER = SlotResolver()
