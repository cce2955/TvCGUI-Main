# resolver.py
# Slot -> fighter base resolver and Y-offset auto-selection.

import time
import math

from constants import (
    SLOTS,  # (not used here directly, but kept for parity)
    OFF_MAX_HP, OFF_CUR_HP, OFF_AUX_HP,
    POSX_OFF, POSY_CANDS,
    BAD_PTRS, INDIR_PROBES,
)
from config import SAMPLE_SECS, SAMPLE_DT, HP_MIN_MAX, HP_MAX_MAX
from dolphin_io import rd32, rdf32, addr_in_ram

# Prefer LAST_GOOD_TTL from constants; fall back to 1.0 if missing.
try:
    from constants import LAST_GOOD_TTL
except Exception:
    LAST_GOOD_TTL = 1.0


def looks_like_hp(maxhp, curhp, auxhp):
    """Heuristic check that a struct at a candidate base 'looks' like a fighter HP block."""
    if maxhp is None or curhp is None:
        return False
    if not (HP_MIN_MAX <= maxhp <= HP_MAX_MAX):
        return False
    if not (0 <= curhp <= maxhp):
        return False
    if auxhp is not None and not (0 <= auxhp <= maxhp):
        return False
    return True


# ---------------------- Y-offset auto picker (no jump required) ----------------------

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
    Sample several candidate Y offsets for a short window and prefer the one that:
    - moves sensibly like a position component (low variance/slope noise),
    - correlates reasonably to X motion (slightly helpful to reject timers).
    """
    xs = []
    ys = {off: [] for off in POSY_CANDS}
    end = time.time() + SAMPLE_SECS

    while time.time() < end:
        x = rdf32(base + POSX_OFF)
        xs.append(x if x is not None else 0.0)
        for off in POSY_CANDS:
            y = rdf32(base + off)
            ys[off].append(y if y is not None else 0.0)
        time.sleep(SAMPLE_DT)

    if len(xs) < 10:
        return POSY_CANDS[0]  # default to 0xF4

    best = None
    best_off = POSY_CANDS[0]
    for off, series in ys.items():
        if len(series) < 10:
            continue
        s = abs(_slope(series))
        v = _variance(series)
        r = abs(_corr(series, xs))
        # Favor low noise/low slope and slight correlation to X (not too strong)
        score = (0.6 * (1 / (1 + v))) + (0.3 * (1 / (1 + s))) + (0.1 * (1 / (1 + r)))
        if best is None or score > best:
            best, best_off = score, off
    return best_off


# ---------------------- Slot resolver ----------------------

class SlotResolver:
    """
    Resolves a slot address (PTR_Px_Cy) to an actual fighter 'base' by:
    1) Checking if slot points to a struct that passes looks_like_hp().
    2) If not, probing a few indirection fields in the slot.
    3) Caching a last-known-good base for a short TTL to smooth brief read failures.
    """
    def __init__(self, ttl=LAST_GOOD_TTL):
        self.last_good = {}   # slot_addr -> (base, expiry_time)
        self.ttl = float(ttl)

    def _probe(self, slot_val):
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
        now = time.time()
        s = rd32(slot_addr)

        if not addr_in_ram(s) or s in BAD_PTRS:
            lg = self.last_good.get(slot_addr)
            if lg and now < lg[1]:
                return lg[0], False  # use cached
            return None, False

        mh = rd32(s + OFF_MAX_HP)
        ch = rd32(s + OFF_CUR_HP)
        ax = rd32(s + OFF_AUX_HP)

        if looks_like_hp(mh, ch, ax):
            self.last_good[slot_addr] = (s, now + self.ttl)
            return s, True

        a = self._probe(s)
        if a:
            self.last_good[slot_addr] = (a, now + self.ttl)
            return a, True

        lg = self.last_good.get(slot_addr)
        if lg and now < lg[1]:
            return lg[0], False

        return None, False
