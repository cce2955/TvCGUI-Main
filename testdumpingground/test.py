# tvc_hud_poller_xy_autoy.py
# Requires: pip install dolphin-memory-engine
# Always-on HUD: HP, Meter, X (+0xF0), and auto-picked Y (no jump needed).
# Auto-Y heuristic (no input): sample ~2s; pick candidate near +0xF0 with:
#   - minimal slope/variance, and
#   - minimal correlation with X.
# Falls back to +0xF4 if sampling is inconclusive.

import time, math, struct
import dolphin_memory_engine as dme

# ---- Slots (unchanged) ----
PTR_P1_CHAR1 = 0x803C9FCC
PTR_P1_CHAR2 = 0x803C9FDC
PTR_P2_CHAR1 = 0x803C9FD4
PTR_P2_CHAR2 = 0x803C9FE4
SLOTS = [("P1-C1", PTR_P1_CHAR1),
         ("P1-C2", PTR_P1_CHAR2),
         ("P2-C1", PTR_P2_CHAR1),
         ("P2-C2", PTR_P2_CHAR2)]

# ---- Known offsets ----
OFF_MAX_HP = 0x24
OFF_CUR_HP = 0x28
OFF_AUX_HP = 0x2C

POSX_OFF   = 0xF0                  # confirmed by your scan
POSY_CANDS = [0xF4, 0xEC, 0xE8, 0xF8, 0xFC]  # neighbors to test

# Meter candidates (auto-detected & cached per base)
METER_OFF_PRIMARY   = 0x4C
METER_OFF_SECONDARY = 0x9380 + 0x4C

# ---- Heuristics / rates ----
HP_MIN_MAX   = 10_000
HP_MAX_MAX   = 60_000
POLL_HZ      = 20
INTERVAL     = 1.0 / POLL_HZ

SAMPLE_HZ    = 60
SAMPLE_DT    = 1.0 / SAMPLE_HZ
SAMPLE_SECS  = 2.0  # ~2 seconds is enough while natural play happens

# ---- Hook / safe reads ----
def hook():
    print("Waiting for Dolphin…")
    while not dme.is_hooked():
        dme.hook(); time.sleep(0.2)
    print("Hooked!")

def rd32(addr):
    try: return dme.read_word(addr)
    except Exception: return None

def rdf32(addr):
    try:
        w = dme.read_word(addr)
        f = struct.unpack(">f", struct.pack(">I", w))[0]
        if not math.isfinite(f) or abs(f) > 1e8: return None
        return f
    except Exception: return None

def looks_like_hp(maxhp, curhp, auxhp):
    if maxhp is None or curhp is None: return False
    if not (HP_MIN_MAX <= maxhp <= HP_MAX_MAX): return False
    if not (0 <= curhp <= maxhp): return False
    if auxhp is not None and not (0 <= auxhp <= maxhp): return False
    return True

# ---- Meter cache ----
class MeterAddrCache:
    def __init__(self): self.addr_by_base = {}
    def get(self, base):
        if base in self.addr_by_base: return self.addr_by_base[base]
        cands = [base + METER_OFF_PRIMARY, base + METER_OFF_SECONDARY]
        # prefer the canonical 50000 if seen
        for a in cands:
            v = rd32(a)
            if v in (50000, 0x0000C350): self.addr_by_base[base] = a; return a
        for a in cands:
            v = rd32(a)
            if v is not None and 0 <= v <= 200_000: self.addr_by_base[base] = a; return a
        self.addr_by_base[base] = base + METER_OFF_PRIMARY
        return self.addr_by_base[base]

METER_CACHE = MeterAddrCache()

# ---- Auto-pick Y with no jump (correlation vs X) ----
def variance(vals):
    n = len(vals)
    if n < 2: return 0.0
    m = sum(vals)/n
    return sum((v-m)*(v-m) for v in vals)/ (n-1)

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
    # Sample X and each Y candidate for ~2s at 60Hz
    xs = []
    ys = {off: [] for off in POSY_CANDS}
    end = time.time() + SAMPLE_SECS
    while time.time() < end:
        x = rdf32(base + POSX_OFF)
        if x is not None: xs.append(x)
        for off in POSY_CANDS:
            y = rdf32(base + off)
            if y is not None: ys[off].append(y)
        time.sleep(SAMPLE_DT)

    if len(xs) < 10:
        # Not enough data; default to common neighbor
        return 0xF4

    # Score each candidate: low slope/variance and low |corr| with X wins
    best_score = None
    best_off = None
    for off, series in ys.items():
        if len(series) < 10: 
            continue
        s = abs(slope(series))
        v = variance(series)
        r = abs(corr(series, xs))
        # positional Y: small slope/variance during horizontal walk; correlation ~0
        # combine with small weights; tweakable
        score = (0.6 * (1/(1+v))) + (0.3 * (1/(1+s))) + (0.1 * (1/(1+r)))
        if (best_score is None) or (score > best_score):
            best_score, best_off = score, off

    return best_off or 0xF4

# ---- Read block ----
def read_block(base, posy_off, want_meter=True):
    if not base: return None
    max_hp = rd32(base + OFF_MAX_HP)
    cur_hp = rd32(base + OFF_CUR_HP)
    aux_hp = rd32(base + OFF_AUX_HP)
    if not looks_like_hp(max_hp, curhp=cur_hp, auxhp=aux_hp):
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

# ---- Main ----
def main():
    hook()

    # Resolve P1 base and confirm HP
    base_p1 = None
    for _ in range(200):
        base_p1 = rd32(PTR_P1_CHAR1)
        if base_p1:
            mh = rd32(base_p1 + OFF_MAX_HP)
            ch = rd32(base_p1 + OFF_CUR_HP)
            ax = rd32(base_p1 + OFF_AUX_HP)
            if looks_like_hp(mh, ch, ax): break
        time.sleep(0.05)
    if not base_p1:
        print("Could not resolve P1 base/HP. Start a round and run again.")
        return

    # Auto-pick Y once, without requiring a jump
    print("Auto-picking Y (no input needed)…")
    posy_off = pick_posy_off_no_jump(base_p1)
    print(f"Using Y offset +0x{posy_off:02X} (fallbacks to +0xF4 if sampling was weak)")

    print("\nPolling HP/Meter/X/Y… (Ctrl+C to quit)")
    try:
        while True:
            lines = []
            for name, slot in SLOTS:
                base = rd32(slot)
                blk  = read_block(base, posy_off, want_meter=True)
                lines.append(fmt(name, blk))
            print(" | ".join(lines))
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()