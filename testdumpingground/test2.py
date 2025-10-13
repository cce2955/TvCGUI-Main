# tvc_hud_poller_xy_autoy_resilient.py
# Requires: pip install dolphin-memory-engine
# Robust HUD poller with auto Y-pick and volatile-pointer handling.
# this is the same as test.py but with an extra checker, for some reason....the
#  pointer just leaves occasionally, checking into why, for now the code will just
# sometimes not work lol

import time, math, struct
import dolphin_memory_engine as dme

# ---------------- Slots (static addresses observed in US build) ----------------
PTR_P1_CHAR1 = 0x803C9FCC
PTR_P1_CHAR2 = 0x803C9FDC
PTR_P2_CHAR1 = 0x803C9FD4
PTR_P2_CHAR2 = 0x803C9FE4
SLOTS = [
    ("P1-C1", PTR_P1_CHAR1),
    ("P1-C2", PTR_P1_CHAR2),
    ("P2-C1", PTR_P2_CHAR1),
    ("P2-C2", PTR_P2_CHAR2),
]

# ---------------- Known offsets within the fighter object ----------------
OFF_MAX_HP = 0x24
OFF_CUR_HP = 0x28
OFF_AUX_HP = 0x2C

# Position: X confirmed at +0xF0; Y is auto-detected among neighbors
POSX_OFF   = 0xF0
POSY_CANDS = [0xF4, 0xEC, 0xE8, 0xF8, 0xFC]

# Super meter: primary is local; secondary seen in mirrored bank (+0x9380)
METER_OFF_PRIMARY   = 0x4C
METER_OFF_SECONDARY = 0x9380 + 0x4C

# ---------------- Heuristics / timing ----------------
HP_MIN_MAX  = 10_000
HP_MAX_MAX  = 60_000
POLL_HZ     = 20
INTERVAL    = 1.0 / POLL_HZ

SAMPLE_HZ   = 60
SAMPLE_DT   = 1.0 / SAMPLE_HZ
SAMPLE_SECS = 2.0

# Resilience: how long (seconds) to keep using last-good base after a miss
LAST_GOOD_TTL = 1.0

# If a slot points to a manager/control object, try these inner offsets
INDIR_PROBES = [0x10, 0x18, 0x1C, 0x20]

# Address ranges (MEM1/MEM2) for sanity checking
MEM1_LO, MEM1_HI = 0x80000000, 0x81800000
MEM2_LO, MEM2_HI = 0x90000000, 0x94000000
BAD_PTRS = {0x00000000, 0x80520000}  # observed “dead” values in your logs

# ---------------- Dolphin hook & safe reads ----------------
def hook():
    while not dme.is_hooked():
        try:
            dme.hook()
        except Exception:
            pass
        time.sleep(0.2)

def rd32(addr):
    try:
        return dme.read_word(addr)
    except Exception:
        return None

def rdf32(addr):
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

# ---------------- Validation helpers ----------------
def addr_in_ram(a):
    if a is None: return False
    return (MEM1_LO <= a < MEM1_HI) or (MEM2_LO <= a < MEM2_HI)

def looks_like_hp(maxhp, curhp, auxhp):
    if maxhp is None or curhp is None: return False
    if not (HP_MIN_MAX <= maxhp <= HP_MAX_MAX): return False
    if not (0 <= curhp <= maxhp): return False
    if auxhp is not None and not (0 <= auxhp <= maxhp): return False
    return True

# ---------------- Meter cache ----------------
class MeterAddrCache:
    def __init__(self): self.addr_by_base = {}
    def drop(self, base):
        if base in self.addr_by_base:
            del self.addr_by_base[base]
    def get(self, base):
        if base in self.addr_by_base: return self.addr_by_base[base]
        cands = [base + METER_OFF_PRIMARY, base + METER_OFF_SECONDARY]
        # prefer canonical-looking values first (50,000 or close range)
        for a in cands:
            v = rd32(a)
            if v in (50000, 0x0000C350):
                self.addr_by_base[base] = a
                return a
        for a in cands:
            v = rd32(a)
            if v is not None and 0 <= v <= 200_000:
                self.addr_by_base[base] = a
                return a
        # fallback
        self.addr_by_base[base] = base + METER_OFF_PRIMARY
        return self.addr_by_base[base]

METER_CACHE = MeterAddrCache()

# ---------------- Auto Y-pick (no jump) ----------------
def variance(vals):
    n = len(vals)
    if n < 2: return 0.0
    m = sum(vals)/n
    return sum((v-m)*(v-m) for v in vals) / (n-1)

def slope(vals):
    if len(vals) < 2: return 0.0
    return (vals[-1] - vals[0]) / (len(vals)-1)

def corr(a, b):
    n = len(a)
    if n < 2 or n != len(b): return 0.0
    ma = sum(a)/n; mb = sum(b)/n
    num = sum((x-ma)*(y-mb) for x,y in zip(a,b))
    da = math.sqrt(sum((x-ma)*(x-ma) for x in a))
    db = math.sqrt(sum((y-mb)*(y-mb) for y in b))
    if da == 0 or db == 0: return 0.0
    return num/(da*db)

def pick_posy_off_no_jump(base):
    xs = []
    ys = {off: [] for off in POSY_CANDS}
    end = time.time() + SAMPLE_SECS
    while time.time() < end:
        x = rdf32(base + POSX_OFF);  xs.append(x if x is not None else 0.0)
        for off in POSY_CANDS:
            y = rdf32(base + off);    ys[off].append(y if y is not None else 0.0)
        time.sleep(SAMPLE_DT)
    if len(xs) < 10:
        return 0xF4
    best_score, best_off = None, None
    for off, series in ys.items():
        if len(series) < 10: continue
        s = abs(slope(series)); v = variance(series); r = abs(corr(series, xs))
        score = (0.6*(1/(1+v))) + (0.3*(1/(1+s))) + (0.1*(1/(1+r)))
        if best_score is None or score > best_score:
            best_score, best_off = score, off
    return best_off or 0xF4

# ---------------- Volatile pointer resolver ----------------
class SlotResolver:
    def __init__(self):
        self.last_good = {}      # slot_addr -> (base_addr, expiry_time)
        self.last_posy = {}      # base_addr -> posy_off (so base change triggers re-pick)

    def _probe_indirections(self, slot_val):
        # try common inner pointers to reach the HP-bearing object
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
        # 1) live read of slot value
        s = rd32(slot_addr)
        # Quick rejection
        if not addr_in_ram(s) or s in BAD_PTRS:
            # 2) try last-good within TTL
            lg = self.last_good.get(slot_addr)
            if lg and now < lg[1]:
                return lg[0], False
            return None, False

        # 3) direct HP check
        mh = rd32(s + OFF_MAX_HP); ch = rd32(s + OFF_CUR_HP); ax = rd32(s + OFF_AUX_HP)
        if looks_like_hp(mh, ch, ax):
            self.last_good[slot_addr] = (s, now + LAST_GOOD_TTL)
            return s, True

        # 4) try one level of indirection (control->fighter, etc.)
        a = self._probe_indirections(s)
        if a:
            self.last_good[slot_addr] = (a, now + LAST_GOOD_TTL)
            return a, True

        # 5) fall back to last-good within TTL
        lg = self.last_good.get(slot_addr)
        if lg and now < lg[1]:
            return lg[0], False

        return None, False

RESOLVER = SlotResolver()

# ---------------- Read + format ----------------
def read_block(base, posy_off, want_meter=True):
    if not base: return None
    max_hp = rd32(base + OFF_MAX_HP)
    cur_hp = rd32(base + OFF_CUR_HP)
    aux_hp = rd32(base + OFF_AUX_HP)
    if not looks_like_hp(max_hp, cur_hp, aux_hp):
        return None
    x = rdf32(base + POSX_OFF)
    y = rdf32(base + posy_off) if posy_off is not None else None
    pkt = {
        "ptr": base,
        "max": max_hp, "cur": cur_hp, "aux": aux_hp,
        "pct": (cur_hp / float(max_hp)) if max_hp else None,
        "x": x, "y": y
    }
    if want_meter:
        maddr = METER_CACHE.get(base)
        mv = rd32(maddr)
        if mv is not None and 0 <= mv <= 200_000:
            pkt["meter"] = mv
    return pkt

def fmt(label, blk):
    if not blk: return f"{label}[--------] n/a"
    pct = f" ({blk['pct']*100:5.1f}%)" if blk['pct'] is not None else ""
    m   = f" | M:{blk['meter']}" if "meter" in blk and blk["meter"] is not None else " | M:--"
    x   = f" | X:{blk['x']:.3f}" if blk.get("x") is not None else " | X:--"
    y   = f" Y:{blk['y']:.3f}"   if blk.get("y") is not None else " Y:--"
    return f"{label}[{blk['ptr']:08X}] {blk['cur']}/{blk['max']}{pct}{m}{x}{y}"

# ---------------- Main ----------------
def main():
    print("Waiting for Dolphin…")
    hook()
    print("Hooked!")

    # Warm-up: ensure P1-C1 resolves at least once (user feedback)
    base0, _ = RESOLVER.resolve_base(PTR_P1_CHAR1)
    if not base0:
        print("Hint: enter training or start a round so slots are populated.")
    last_base_by_slot = {}

    print("Polling HP/Meter/X/Y… (Ctrl+C to quit)")
    try:
        while True:
            lines = []
            for name, slot in SLOTS:
                base, changed_now = RESOLVER.resolve_base(slot)

                # If base changed, drop stale meter cache and (re)pick Y for this base
                if base and last_base_by_slot.get(slot) != base:
                    old = last_base_by_slot.get(slot)
                    last_base_by_slot[slot] = base
                    METER_CACHE.drop(base)
                    # compute Y once per new base
                    posy_off = RESOLVER.last_posy.get(base)
                    if posy_off is None:
                        print(f"[{name}] Base changed -> {old and hex(old)} -> {hex(base)}; sampling Y…")
                        posy_off = pick_posy_off_no_jump(base)
                        RESOLVER.last_posy[base] = posy_off
                        print(f"[{name}] Using Y offset +0x{posy_off:02X}")
                # If no base (temporarily), try to keep using previous base’s posy (handled by resolver)

                # Choose posy for this base if known, else default
                posy = RESOLVER.last_posy.get(base, 0xF4) if base else 0xF4
                blk  = read_block(base, posy, want_meter=True)
                lines.append(fmt(name, blk))
            print(" | ".join(lines))
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
