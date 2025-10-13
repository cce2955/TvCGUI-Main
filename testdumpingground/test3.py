# tvc_hud_poller_with_char_names.py
# Requires: pip install dolphin-memory-engine
# Full HUD poller with character names at offset +0x14

import time, math, struct
import dolphin_memory_engine as dme

# ---------------- ANSI Color Codes ----------------
class Colors:
    # Player 1 - Blue theme
    P1_BRIGHT = '\033[96m'      # Bright cyan
    P1_NORMAL = '\033[94m'      # Blue
    
    # Player 2 - Red theme  
    P2_BRIGHT = '\033[91m'      # Bright red
    P2_NORMAL = '\033[31m'      # Red
    
    # Status colors
    GREEN = '\033[92m'          # Good HP
    YELLOW = '\033[93m'         # Medium HP
    RED = '\033[91m'            # Low HP
    PURPLE = '\033[95m'         # Meter
    
    # UI
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    RESET = '\033[0m'
    DIM = '\033[2m'

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
OFF_CHAR_ID = 0x14  # Character ID (word)

# Position: X confirmed at +0xF0; Y is auto-detected among neighbors
POSX_OFF   = 0xF0
POSY_CANDS = [0xF4, 0xEC, 0xE8, 0xF8, 0xFC]

# Super meter: primary is local; secondary seen in mirrored bank (+0x9380)
METER_OFF_PRIMARY   = 0x4C
METER_OFF_SECONDARY = 0x9380 + 0x4C

# Animation/State flags (for future use)
OFF_ANIM_STATE = 0x58      # Animation state/ID
OFF_ANIM_FLAG = 0x5C       # Animation completion flag
OFF_ACTION_STATE = 0x60    # Action state machine
OFF_SUB_ACTION = 0x64      # Sub-action/combo counter
OFF_HIT_STATE = 0x00       # Hit/damage/stun state
OFF_TEAM_FLAG = 0x30       # Team/side flag
OFF_FRAME_COUNTER = 0x6C   # Frame counter/timer

# ---------------- Character Name Mapping ----------------
CHAR_NAMES = {
    1: "Ken the Eagle",
    2: "Casshan",
    3: "Tekkaman",
    4: "Polimar",
    5: "Yatterman-1",
    6: "Doronjo",
    7: "Ippatsuman",
    8: "Jun the Swan",
    10: "Karas",
    12: "Ryu",
    13: "Chun-Li",
    14: "Batsu",
    15: "Morrigan",
    16: "Alex",
    17: "Viewtiful Joe",
    18: "Volnutt",
    19: "Roll",
    20: "Saki",
    21: "Soki",
    26: "Tekkaman Blade",
    27: "Joe the Condor",
    28: "Yatterman-2",
    29: "Zero",
    30: "Frank West",
    # Potential boss/special characters (fill in as discovered):
    # 0: "???",
    # 9: "???",
    # 11: "???",
    # 22: "PTX-40A?",
    # 23: "Gold Lightan?",
    # 24: "???",
    # 25: "???",
}

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
BAD_PTRS = {0x00000000, 0x80520000}

# ---------------- Dolphin hook & safe reads ----------------
def hook():
    while not dme.is_hooked():
        try:
            dme.hook()
        except Exception:
            pass
        time.sleep(0.2)

def rd8(addr):
    try:
        return dme.read_byte(addr)
    except Exception:
        return None

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
        self.last_good = {}
        self.last_posy = {}

    def _probe_indirections(self, slot_val):
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
                return lg[0], False
            return None, False

        mh = rd32(s + OFF_MAX_HP); ch = rd32(s + OFF_CUR_HP); ax = rd32(s + OFF_AUX_HP)
        if looks_like_hp(mh, ch, ax):
            self.last_good[slot_addr] = (s, now + LAST_GOOD_TTL)
            return s, True

        a = self._probe_indirections(s)
        if a:
            self.last_good[slot_addr] = (a, now + LAST_GOOD_TTL)
            return a, True

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
    
    # Get character ID and name
    char_id = rd32(base + OFF_CHAR_ID)
    if char_id is not None:
        char_name = CHAR_NAMES.get(char_id, f"Unknown_ID_{char_id}")
    else:
        char_name = "???"
    
    x = rdf32(base + POSX_OFF)
    y = rdf32(base + posy_off) if posy_off is not None else None
    
    pkt = {
        "ptr": base,
        "char_id": char_id,
        "char_name": char_name,
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
    if not blk:
        return f"{Colors.DIM}{label}[--------] n/a{Colors.RESET}"
    
    # Determine player color
    if label.startswith("P1"):
        player_color = Colors.P1_BRIGHT
        label_color = Colors.P1_NORMAL
    else:
        player_color = Colors.P2_BRIGHT
        label_color = Colors.P2_NORMAL
    
    # HP percentage color
    pct_val = blk['pct']
    if pct_val is not None:
        if pct_val > 0.66:
            hp_color = Colors.GREEN
        elif pct_val > 0.33:
            hp_color = Colors.YELLOW
        else:
            hp_color = Colors.RED
        pct_str = f"{hp_color}({pct_val*100:5.1f}%){Colors.RESET}"
    else:
        pct_str = ""
    
    # Character name
    char = f" {player_color}{blk['char_name']:<16}{Colors.RESET}" if blk.get('char_name') else ""
    
    # Meter
    m = f" | {Colors.PURPLE}M:{blk['meter']}{Colors.RESET}" if "meter" in blk and blk["meter"] is not None else f" | {Colors.DIM}M:--{Colors.RESET}"
    
    # Position
    x = f" | X:{blk['x']:.3f}" if blk.get("x") is not None else f" | {Colors.DIM}X:--{Colors.RESET}"
    y = f" Y:{blk['y']:.3f}" if blk.get("y") is not None else f" {Colors.DIM}Y:--{Colors.RESET}"
    
    # HP display
    hp_display = f"{hp_color}{blk['cur']}/{blk['max']}{Colors.RESET}"
    
    return f"{label_color}{label}{Colors.RESET}[{Colors.DIM}{blk['ptr']:08X}{Colors.RESET}]{char} {hp_display} {pct_str}{m}{x}{y}"

# ---------------- Main ----------------
def main():
    print(f"{Colors.BOLD}{'='*80}")
    print("TvC HUD POLLER WITH CHARACTER NAMES")
    print(f"{'='*80}{Colors.RESET}")
    print("Waiting for Dolphin…")
    hook()
    print(f"{Colors.GREEN}Hooked!{Colors.RESET}")

    base0, _ = RESOLVER.resolve_base(PTR_P1_CHAR1)
    if not base0:
        print(f"{Colors.YELLOW}Hint: enter training or start a round so slots are populated.{Colors.RESET}")
    last_base_by_slot = {}

    print(f"\n{Colors.BOLD}{'='*80}")
    print("Polling HP/Meter/X/Y/Character… (Ctrl+C to quit)")
    print(f"{'='*80}{Colors.RESET}")
    print(f"{Colors.P1_BRIGHT}P1 = Blue{Colors.RESET} | {Colors.P2_BRIGHT}P2 = Red{Colors.RESET} | {Colors.GREEN}HP: Green>Yellow>Red{Colors.RESET} | {Colors.PURPLE}Meter = Purple{Colors.RESET}")
    print(f"{Colors.BOLD}{'='*80}{Colors.RESET}\n")
    
    try:
        while True:
            lines = []
            for name, slot in SLOTS:
                base, changed_now = RESOLVER.resolve_base(slot)

                if base and last_base_by_slot.get(slot) != base:
                    old = last_base_by_slot.get(slot)
                    last_base_by_slot[slot] = base
                    METER_CACHE.drop(base)
                    posy_off = RESOLVER.last_posy.get(base)
                    if posy_off is None:
                        print(f"{Colors.YELLOW}[{name}] Base changed -> {old and hex(old)} -> {hex(base)}; sampling Y…{Colors.RESET}")
                        posy_off = pick_posy_off_no_jump(base)
                        RESOLVER.last_posy[base] = posy_off
                        print(f"{Colors.GREEN}[{name}] Using Y offset +0x{posy_off:02X}{Colors.RESET}")

                posy = RESOLVER.last_posy.get(base, 0xF4) if base else 0xF4
                # Only read meter for C1 characters (meter is shared per player)
                want_meter = name.endswith("C1")
                blk  = read_block(base, posy, want_meter=want_meter)
                lines.append(fmt(name, blk))
            print(" | ".join(lines))
            time.sleep(INTERVAL)
    except KeyboardInterrupt:
        print(f"\n\n{Colors.GREEN}Shutting down gracefully...{Colors.RESET}")

if __name__ == "__main__":
    main()