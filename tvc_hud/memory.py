import time, math, struct
import dolphin_memory_engine as dme
from .constants import *

# -------- Hook & safe reads ----------
def hook():
    while not dme.is_hooked():
        try: dme.hook()
        except Exception: pass
        time.sleep(0.2)

def rd32(addr):
    try: return dme.read_word(addr)
    except Exception: return None

def rdf32(addr):
    try:
        w = dme.read_word(addr)
        if w is None: return None
        f = struct.unpack(">f", struct.pack(">I", w))[0]
        if not math.isfinite(f) or abs(f) > 1e8: return None
        return f
    except Exception:
        return None

def addr_in_ram(a):
    if a is None: return False
    return (MEM1_LO <= a < MEM1_HI) or (MEM2_LO <= a < MEM2_HI)

def looks_like_hp(maxhp, curhp, auxhp):
    if maxhp is None or curhp is None: return False
    if not (HP_MIN_MAX <= maxhp <= HP_MAX_MAX): return False
    if not (0 <= curhp <= maxhp): return False
    if auxhp is not None and not (0 <= auxhp <= maxhp): return False
    return True

# -------- Helpers ----------
def best_posy_off(base):
    samples = {off: [] for off in POSY_OFFS}
    end = time.time() + 0.5
    dt = 1/120
    while time.time() < end:
        for off in POSY_OFFS:
            samples[off].append(rdf32(base + off) or 0.0)
        time.sleep(dt)

    def variance(vs):
        if len(vs) < 2: return 1e9
        m = sum(vs)/len(vs)
        return sum((v-m)*(v-m) for v in vs)/max(1, len(vs)-1)

    best, best_score = None, None
    for off, series in samples.items():
        s = 1/(1+variance(series))
        if best_score is None or s > best_score:
            best, best_score = off, s
    return best or 0xF4

class MeterAddrCache:
    def __init__(self): self.addr_by_base = {}
    def drop(self, base): self.addr_by_base.pop(base, None)
    def get(self, base):
        if base in self.addr_by_base: return self.addr_by_base[base]
        for a in (base + METER_OFF_PRIMARY, base + METER_OFF_SECONDARY):
            v = rd32(a)
            if v in (50000, 0x0000C350) or (v is not None and 0 <= v <= 200_000):
                self.addr_by_base[base] = a
                return a
        self.addr_by_base[base] = base + METER_OFF_PRIMARY
        return self.addr_by_base[base]

METER_CACHE = MeterAddrCache()

class SlotResolver:
    def __init__(self):
        self.last_good = {}
        self.ttl = 1.0
    def _probe(self, slot_val):
        for off in (0x10,0x18,0x1C,0x20):
            a = rd32(slot_val + off)
            if addr_in_ram(a) and a not in BAD_PTRS:
                mh = rd32(a + OFF_MAX_HP)
                ch = rd32(a + OFF_CUR_HP)
                ax = rd32(a + OFF_AUX_HP)
                if looks_like_hp(mh, ch, ax): return a
        return None
    def resolve_base(self, slot_addr):
        now = time.time()
        s = rd32(slot_addr)
        if not addr_in_ram(s) or s in BAD_PTRS:
            lg = self.last_good.get(slot_addr)
            if lg and now < lg[1]: return lg[0], False
            return None, False
        mh = rd32(s + OFF_MAX_HP); ch = rd32(s + OFF_CUR_HP); ax = rd32(s + OFF_AUX_HP)
        if looks_like_hp(mh, ch, ax):
            self.last_good[slot_addr] = (s, now + self.ttl); return s, True
        a = self._probe(s)
        if a:
            self.last_good[slot_addr] = (a, now + self.ttl); return a, True
        lg = self.last_good.get(slot_addr)
        if lg and now < lg[1]: return lg[0], False
        return None, False

RESOLVER = SlotResolver()

# -------- High-level read --------
def read_fighter_block(base, posy_off, want_meter=True):
    if not base: return None
    max_hp = rd32(base + OFF_MAX_HP)
    cur_hp = rd32(base + OFF_CUR_HP)
    aux_hp = rd32(base + OFF_AUX_HP)
    if not looks_like_hp(max_hp, cur_hp, aux_hp): return None

    char_id = rd32(base + OFF_CHAR_ID)
    x = rdf32(base + POSX_OFF)
    y = rdf32(base + (posy_off or 0xF4))
    last_hit = rd32(base + OFF_LAST_HIT)
    if last_hit is None or last_hit < 0 or last_hit > 200000: last_hit = None

    meter = None
    if want_meter:
        maddr = METER_CACHE.get(base)
        mv = rd32(maddr)
        if mv is not None and 0 <= mv <= 200_000:
            meter = mv

    return dict(max=max_hp, cur=cur_hp, aux=aux_hp,
                char_id=char_id, x=x, y=y, last_hit=last_hit, meter=meter)
