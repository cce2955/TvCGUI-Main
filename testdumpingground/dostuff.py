# tvc_ownerprobe_final_autobyte.py
# pip install dolphin-memory-engine
#
# What this does (end-to-end):
#  - Static HUD (no spam scrolling)
#  - Ground-truth attacker: pointer proofs (direct/onehop/twohop/reverse)
#  - Auto-learns the "attacker ID" byte living under victim->(gate+0x020)
#      • Calibrates using rows where pointer proof succeeded
#      • Evaluates 16 lowbytes under two schemes:
#           - side_local: values {0,1} map to {opponent C1, opponent C2}
#           - global_0..3: values {0..3} map to {P1-C1,P1-C2,P2-C1,P2-C2}
#      • Locks the best scheme+byte once it passes accuracy & sample thresholds
#  - Runtime attacker selection:
#      • Use pointer proof if available
#      • Else use learned gate byte (zero inference/guessing)
#  - Writes CSVs to tvc_logs/<timestamp>_autobyte/
#
# Suggested flow for live testing:
#   1) Do a simple sequence where proofs are easy (normals/specials point blank)
#   2) Once you see "LOCKED gate-byte", start projectile/assist/tag sequences
#   3) Stop with Ctrl-C; we’ll dump clean CSVs + summary in the session folder
#
# Ctrl-C to stop.

import time, math, struct, csv, os, sys, json
import dolphin_memory_engine as dme

# ========================= Session folder =========================
RUN_ROOT = "tvc_logs"
RUN_NAME = time.strftime("%Y%m%d_%H%M%S") + "_autobyte"
SESSION_DIR = os.path.join(RUN_ROOT, RUN_NAME)
os.makedirs(SESSION_DIR, exist_ok=True)

# ========================= Config =========================
HUD_MODE        = "static"     # "static" | "off"
HUD_FPS         = 2
HUD_HEADER      = "TVC Owner Probe — FINAL (pointer-proof + auto gate-byte)"

POLL_HZ         = 60
INTERVAL        = 1.0 / POLL_HZ
COMBO_TIMEOUT   = 0.60
MIN_HIT_DAMAGE  = 10

# Victim scan knobs for proofs
SCAN_BYTES_VICTIM    = 0x800
SCAN_STEP            = 4
ONE_HOP_CHILD_OFFS   = (0x00, 0x10, 0x18, 0x1C, 0x20)
TWO_HOP_CHILD_OFFS   = (0x00, 0x04, 0x08, 0x10, 0x18, 0x1C, 0x20)
REVERSE_SCAN_RANGE   = 0x600
REVERSE_SCAN_STEP    = 4
REVERSE_CHILD_OFFS   = (0x00, 0x10, 0x18, 0x1C)

# Gateway under victim: we dump 16 dwords at gate+0x020
GATEWAY_PRIMARY      = 0x020
GATEWAY_CHILD_OFFS   = tuple(range(0x00, 0x40, 4))  # 16 * dword
LOWBYTE_COUNT        = 16  # 16 lowbytes (one per dword)

# Auto-learn thresholds
CALIB_MIN_SAMPLES    = 16   # proofs-backed rows required to consider locking
CALIB_MIN_ACCURACY   = 0.92 # 92%+ accuracy required to lock
CALIB_GRACE_SECS     = 2.0  # wait at least this long after first proof before locking

# CSVs
HIT_CSV       = os.path.join(SESSION_DIR, "collisions_ownerprobe.csv")
COMBO_CSV     = os.path.join(SESSION_DIR, "combos_ownerprobe.csv")
NOMATCH_CSV   = os.path.join(SESSION_DIR, "pointer_nomatch.csv")
GATE_DUMP_CSV = os.path.join(SESSION_DIR, "gate020_dump.csv")
SUMMARY_JSON  = os.path.join(SESSION_DIR, "autobyte_summary.json")

# ========================= Pointers / Layout =========================
PTR_P1_CHAR1 = 0x803C9FCC
PTR_P1_CHAR2 = 0x803C9FDC
PTR_P2_CHAR1 = 0x803C9FD4
PTR_P2_CHAR2 = 0x803C9FE4
SLOTS = [("P1-C1", PTR_P1_CHAR1), ("P1-C2", PTR_P1_CHAR2),
         ("P2-C1", PTR_P2_CHAR1), ("P2-C2", PTR_P2_CHAR2)]

OFF_MAX_HP   = 0x24
OFF_CUR_HP   = 0x28
OFF_AUX_HP   = 0x2C
OFF_LAST_HIT = 0x40
OFF_CHAR_ID  = 0x14

POSX_OFF     = 0xF0
POSY_CANDS   = [0xF4, 0xEC, 0xE8, 0xF8, 0xFC]

METER_OFF_PRIMARY   = 0x4C
METER_OFF_SECONDARY = 0x9380 + 0x4C

MEM1_LO, MEM1_HI = 0x80000000, 0x81800000
MEM2_LO, MEM2_HI = 0x90000000, 0x94000000
BAD_PTRS = {0x00000000, 0x80520000}
INDIR_PROBES = [0x10,0x18,0x1C,0x20]
LAST_GOOD_TTL = 1.0

CHAR_NAMES = {
    1:"Ken the Eagle",2:"Casshan",3:"Tekkaman",4:"Polimar",5:"Yatterman-1",
    6:"Doronjo",7:"Ippatsuman",8:"Jun the Swan",10:"Karas",12:"Ryu",
    13:"Chun-Li",14:"Batsu",15:"Morrigan",16:"Alex",17:"Viewtiful Joe",
    18:"Volnutt",19:"Roll",20:"Saki",21:"Soki",26:"Tekkaman Blade",
    27:"Joe the Condor",28:"Yatterman-2",29:"Zero",30:"Frank West",
}

# ========================= Colors / HUD =========================
class Colors:
    P1_BRIGHT = '\033[96m'; P1_NORMAL = '\033[94m'
    P2_BRIGHT = '\033[91m'; P2_NORMAL = '\033[31m'
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'
    PURPLE = '\033[95m'; BOLD = '\033[1m'; RESET = '\033[0m'; DIM = '\033[2m'

def fmt_line(label, blk, meter=None):
    if not blk:
        return f"{Colors.DIM}{label}[--------]{Colors.RESET}"
    player_color = Colors.P1_BRIGHT if label.startswith("P1") else Colors.P2_BRIGHT
    label_color  = Colors.P1_NORMAL if label.startswith("P1") else Colors.P2_NORMAL
    pct = (blk["cur"] / blk["max"]) if blk["max"] else None
    if pct is None: hp_color = Colors.DIM; pct_str=""
    else:
        if   pct > 0.66: hp_color=Colors.GREEN
        elif pct > 0.33: hp_color=Colors.YELLOW
        else: hp_color=Colors.RED
        pct_str=f"{hp_color}({pct*100:5.1f}%){Colors.RESET}"
    char=f" {player_color}{blk['name']:<16}{Colors.RESET}"
    m  = f" | {Colors.PURPLE}M:{meter}{Colors.RESET}" if meter is not None else f" | {Colors.DIM}M:--{Colors.RESET}"
    x  = f" | X:{blk['x']:.3f}" if blk.get("x") is not None else f" | {Colors.DIM}X:--{Colors.RESET}"
    y  = f" Y:{blk['y']:.3f}" if blk.get("y") is not None else f" {Colors.DIM}Y:--{Colors.RESET}"
    last = blk.get("last"); dmg_str = f" | lastDmg:{last:5d}" if last else f" | {Colors.DIM}lastDmg:--{Colors.RESET}"
    hp_display=f"{hp_color}{blk['cur']}/{blk['max']}{Colors.RESET}"
    return f"{label_color}{label}{Colors.RESET}[{Colors.DIM}{blk['base']:08X}{Colors.RESET}]{char} {hp_display} {pct_str}{m}{x}{y}{dmg_str}"

def hud_static_render(lines):
    sys.stdout.write("\033[2J\033[H")
    print(f"{Colors.BOLD}{HUD_HEADER}{Colors.RESET}    {SESSION_DIR}\n")
    for ln in lines: print(ln)
    sys.stdout.flush()

# ========================= Dolphin / IO =========================
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
    if not (10_000 <= maxhp <= 60_000): return False
    if not (0 <= curhp <= maxhp): return False
    if auxhp is not None and not (0 <= auxhp <= maxhp): return False
    return True

# ========================= Resolver / Y sampling =========================
def pick_posy_off_no_jump(base):
    xs=[]; ys={off:[] for off in POSY_CANDS}
    end=time.time()+1.0
    while time.time()<end:
        x=rdf32(base+POSX_OFF); xs.append(x if x is not None else 0.0)
        for off in POSY_CANDS:
            y=rdf32(base+off); ys[off].append(y if y is not None else 0.0)
        time.sleep(1.0/120)
    if len(xs)<10: return 0xF4
    best_off=None; best_var=1e18
    for off, series in ys.items():
        if len(series)<10: continue
        m=sum(series)/len(series)
        v=sum((z-m)*(z-m) for z in series)/(len(series)-1)
        if v < best_var: best_var=v; best_off=off
    return best_off or 0xF4

class SlotResolver:
    def __init__(self): self.last_good={}
    def _probe(self, slot_val):
        for off in INDIR_PROBES:
            a = rd32(slot_val+off)
            if addr_in_ram(a) and a not in BAD_PTRS:
                if looks_like_hp(rd32(a+OFF_MAX_HP), rd32(a+OFF_CUR_HP), rd32(a+OFF_AUX_HP)):
                    return a
        return None
    def resolve_base(self, slot_addr):
        now=time.time()
        s=rd32(slot_addr)
        if not addr_in_ram(s) or s in BAD_PTRS:
            lg=self.last_good.get(slot_addr)
            if lg and now<lg[1]: return lg[0], False
            return None, False
        mh=rd32(s+OFF_MAX_HP); ch=rd32(s+OFF_CUR_HP); ax=rd32(s+OFF_AUX_HP)
        if looks_like_hp(mh,ch,ax):
            self.last_good[slot_addr]=(s, now+LAST_GOOD_TTL); return s, True
        a=self._probe(s)
        if a:
            self.last_good[slot_addr]=(a, now+LAST_GOOD_TTL); return a, True
        lg=self.last_good.get(slot_addr)
        if lg and now<lg[1]: return lg[0], False
        return None, False

RESOLVER = SlotResolver()

class MeterAddrCache:
    def __init__(self): self.addr_by_base={}
    def drop(self, base): self.addr_by_base.pop(base, None)
    def get(self, base):
        if base in self.addr_by_base: return self.addr_by_base[base]
        for a in (base+METER_OFF_PRIMARY, base+METER_OFF_SECONDARY):
            v = rd32(a)
            if v in (50000, 0xC350) or (v is not None and 0<=v<=200_000):
                self.addr_by_base[base] = a; return a
        self.addr_by_base[base] = base + METER_OFF_PRIMARY
        return self.addr_by_base[base]

METER_CACHE = MeterAddrCache()
def read_meter(base):
    if not base: return None
    a = METER_CACHE.get(base); v = rd32(a)
    if v is None or v < 0 or v > 200_000: return None
    return v

def read_fighter(base, posy_off):
    if not base:
        return None
    max_hp = rd32(base + OFF_MAX_HP)
    cur_hp = rd32(base + OFF_CUR_HP)
    aux_hp = rd32(base + OFF_AUX_HP)

    # Validate the triplet (prevents false positives while scanning)
    if not looks_like_hp(max_hp, cur_hp, aux_hp):
        return None

    cid  = rd32(base + OFF_CHAR_ID)
    name = CHAR_NAMES.get(cid, f"ID_{cid}") if cid is not None else "???"
    x    = rdf32(base + POSX_OFF)
    y    = rdf32(base + posy_off) if posy_off is not None else None

    last = rd32(base + OFF_LAST_HIT)
    if last is None or last < 0 or last > 200_000:
        last = None

    return {
        "base": base,
        "max" : max_hp,
        "cur" : cur_hp,
        "aux" : aux_hp,
        "id"  : cid,
        "name": name,
        "x"   : x,
        "y"   : y,
        "last": last
    }

def dist2(a,b):
    if a is None or b is None: return float("inf")
    ax,ay=a.get("x"),a.get("y"); bx,by=b.get("x"),b.get("y")
    if None in (ax,ay,bx,by): return float("inf")
    dx=ax-bx; dy=ay-by
    return dx*dx+dy*dy

# ========================= Pointer Proofs (ground truth) =========================
def proof_direct(victim_base, opp_bases):
    for off in range(0, SCAN_BYTES_VICTIM, SCAN_STEP):
        w = rd32(victim_base + off)
        if w is None: continue
        for slot, obase in opp_bases.items():
            if obase and w == obase:
                return slot, f"direct victim+0x{off:03X}=opp"
    return "", ""

def proof_onehop(victim_base, opp_bases):
    for off in range(0, SCAN_BYTES_VICTIM, SCAN_STEP):
        w = rd32(victim_base + off)
        if w is None or not addr_in_ram(w) or w in BAD_PTRS: continue
        for child in ONE_HOP_CHILD_OFFS:
            p = rd32(w + child)
            if p is None: continue
            for slot, obase in opp_bases.items():
                if obase and p == obase:
                    return slot, f"onehop victim+0x{off:03X}->+0x{child:02X}=opp"
    return "", ""

def proof_twohop(victim_base, opp_bases):
    for off1 in range(0, SCAN_BYTES_VICTIM, SCAN_STEP):
        w1 = rd32(victim_base + off1)
        if w1 is None or not addr_in_ram(w1) or w1 in BAD_PTRS: continue
        for offA in TWO_HOP_CHILD_OFFS:
            a = rd32(w1 + offA)
            if a is None or not addr_in_ram(a) or a in BAD_PTRS: continue
            for offB in range(0, 0x100, SCAN_STEP):
                b = rd32(a + offB)
                if b is None: continue
                for slot, obase in opp_bases.items():
                    if obase and b == obase:
                        return slot, f"twohop victim+0x{off1:03X}->+0x{offA:02X}->+0x{offB:03X}=opp"
    return "", ""

def proof_reverse(victim_base, opp_bases):
    for slot, attacker_base in opp_bases.items():
        if not attacker_base: continue
        for off in range(0, REVERSE_SCAN_RANGE, REVERSE_SCAN_STEP):
            addr = attacker_base + (off - REVERSE_SCAN_RANGE//2)
            if not addr_in_ram(addr): continue
            w = rd32(addr)
            if w is None or not addr_in_ram(w) or w in BAD_PTRS: continue
            for child in REVERSE_CHILD_OFFS:
                c = rd32(w + child)
                if c is None: continue
                if c == victim_base:
                    return slot, f"reverse @att(0x{addr:08X})->+0x{child:02X}=victim"
    return "", ""

# ========================= Gate+0x020 dump & predictions =========================
def gate_dump16(victim_base):
    g = rd32(victim_base + GATEWAY_PRIMARY)
    if g is None or not addr_in_ram(g) or g in BAD_PTRS:
        return None, [], []
    dws=[]; lbs=[]
    for c in GATEWAY_CHILD_OFFS:
        w = rd32(g + c)
        dws.append( (w & 0xFFFFFFFF) if w is not None else None )
        lbs.append( (w & 0xFF) if (w is not None) else None )
    return g, dws, lbs

def side_opp_order(victim_label):
    return ["P2-C1","P2-C2"] if str(victim_label).startswith("P1") else ["P1-C1","P1-C2"]

GLOBAL_ORDER = ["P1-C1","P1-C2","P2-C1","P2-C2"]
GLOBAL_ID = {lab:i for i,lab in enumerate(GLOBAL_ORDER)}

# ========================= Auto-Learning gate byte =========================
class AutoByte:
    """
    Learns a lowbyte index (0..15) and scheme ('side' or 'global') that
    best predicts the true attacker as proven by pointer proofs.
    """
    def __init__(self):
        self.samples = []  # rows of {victim_label, attacker_truth_label, lowbytes:[16], t}
        self.first_proof_t = None
        self.locked = False
        self.scheme = None       # "side" or "global"
        self.low_idx = None      # 0..15
        self.accuracy = 0.0
        self.coverage = 0
        self.preview = {}

    def add_sample(self, t, victim_label, attacker_truth_label, lowbytes):
        if attacker_truth_label not in GLOBAL_ID:  # ignore when attacker truth missing
            return
        if self.first_proof_t is None:
            self.first_proof_t = t
        self.samples.append({
            "t": t,
            "victim_label": victim_label,
            "attacker_truth_label": attacker_truth_label,
            "lowbytes": list(lowbytes)
        })

    def _expected_side_id(self, victim_label, attacker_truth_label):
        opp = side_opp_order(victim_label)
        return opp.index(attacker_truth_label) if attacker_truth_label in opp else None

    def evaluate(self):
        """
        Evaluate all 16 lowbyte positions under both schemes.
        Return (scheme, idx, accuracy, coverage) for the best candidate.
        """
        if not self.samples:
            return None
        # Prepare arrays
        side_hits=[0]*LOWBYTE_COUNT; side_cov=[0]*LOWBYTE_COUNT
        glob_hits=[0]*LOWBYTE_COUNT; glob_cov=[0]*LOWBYTE_COUNT

        for row in self.samples:
            victim=row["victim_label"]; att=row["attacker_truth_label"]
            lbs=row["lowbytes"]
            # expected global id
            eg = GLOBAL_ID.get(att, None)
            # expected side id
            es = self._expected_side_id(victim, att)
            for i,lb in enumerate(lbs):
                if lb is None: continue
                # side
                if es is not None:
                    side_cov[i]+=1
                    if lb == es: side_hits[i]+=1
                # global
                if eg is not None:
                    glob_cov[i]+=1
                    if lb == eg: glob_hits[i]+=1

        # Find best
        def best_of(hits,cov,kind):
            best=(None,0.0,0)
            for i in range(LOWBYTE_COUNT):
                if cov[i]==0: continue
                acc = hits[i]/cov[i]
                if acc > best[1] or (abs(acc-best[1])<1e-9 and cov[i]>best[2]):
                    best=(i,acc,cov[i])
            return {"scheme":kind,"idx":best[0],"accuracy":best[1],"coverage":best[2]}

        best_side = best_of(side_hits,side_cov,"side")
        best_glob = best_of(glob_hits,glob_cov,"global")

        # choose the higher-accuracy (tie-breaker: higher coverage)
        cand = None
        if best_side["idx"] is not None and best_glob["idx"] is not None:
            if (best_side["accuracy"] > best_glob["accuracy"]) or \
               (abs(best_side["accuracy"] - best_glob["accuracy"]) < 1e-9 and best_side["coverage"] >= best_glob["coverage"]):
                cand = best_side
            else:
                cand = best_glob
        else:
            cand = best_side if best_side["idx"] is not None else best_glob

        self.preview = {"best_side":best_side, "best_global":best_glob, "chosen":cand}
        return cand

    def maybe_lock(self, now):
        if self.locked: return
        if self.first_proof_t is None: return
        # small grace period to gather a few rows
        if (now - self.first_proof_t) < CALIB_GRACE_SECS: return
        cand = self.evaluate()
        if not cand or cand["idx"] is None: return
        if cand["coverage"] >= CALIB_MIN_SAMPLES and cand["accuracy"] >= CALIB_MIN_ACCURACY:
            self.locked = True
            self.scheme = cand["scheme"]
            self.low_idx = int(cand["idx"])
            self.accuracy = float(cand["accuracy"])
            self.coverage = int(cand["coverage"])
            print(f"[LOCKED] gate-byte scheme={self.scheme} idx={self.low_idx}  acc={self.accuracy*100:.1f}%  n={self.coverage}")

    def predict(self, victim_label, lowbytes):
        """Return attacker slot label using locked scheme and a 16-lowbyte array; else ''."""
        if not self.locked or self.low_idx is None: return ""
        lb = lowbytes[self.low_idx]
        if lb is None: return ""
        if self.scheme == "side":
            opp = side_opp_order(victim_label)  # ["P2-C1","P2-C2"] OR ["P1-C1","P1-C2"]
            if lb in (0,1):
                return opp[int(lb)]
            return ""
        else:
            if lb in (0,1,2,3):
                return GLOBAL_ORDER[int(lb)]
            return ""

AUTOBYTE = AutoByte()

# ========================= Combo state =========================
class ComboState:
    def __init__(self):
        self.active=False
        self.victim_base=None; self.victim_label=None; self.victim_name=None
        self.attacker_label=None; self.attacker_name=None
        self.hits=0; self.total=0
        self.hp_start=0; self.hp_end=0
        self.start_t=0.0; self.last_t=0.0
    def begin(self, t, victim_base, victim_label, victim_name, hp_before, attacker_label, attacker_name):
        self.active=True
        self.victim_base=victim_base; self.victim_label=victim_label; self.victim_name=victim_name
        self.attacker_label=attacker_label; self.attacker_name=attacker_name
        self.hits=0; self.total=0; self.hp_start=hp_before; self.hp_end=hp_before
        self.start_t=t; self.last_t=t
    def add_hit(self, t, dmg, hp_after):
        self.hits+=1; self.total+=dmg; self.hp_end=hp_after; self.last_t=t
    def expired(self, t): return self.active and (t-self.last_t) > COMBO_TIMEOUT
    def end(self):
        self.active=False
        return {"t0": self.start_t, "t1": self.last_t, "dur": self.last_t-self.start_t,
                "victim_label": self.victim_label, "victim_name": self.victim_name,
                "attacker_label": self.attacker_label, "attacker_name": self.attacker_name,
                "hits": self.hits, "total": self.total, "hp_start": self.hp_start, "hp_end": self.hp_end}

# ========================= Main =========================
def main():
    print("FINAL (pointer-proof + auto gate-byte) — waiting for Dolphin…")
    hook(); print(f"Hooked. Log dir: {SESSION_DIR}")

    # CSV writers
    hits_new = not os.path.exists(HIT_CSV)
    f_hits = open(HIT_CSV, "a", newline="", encoding="utf-8"); hitw = csv.writer(f_hits)
    if hits_new:
        hitw.writerow([
            "t","victim_label","victim_char","dmg","hp_before","hp_after",
            "attacker_truth_label","attacker_truth_char","proof_mode","proof_details",
            "attacker_gate_label","gate_scheme","gate_low_idx",
            "learn_locked","learn_accuracy","learn_coverage",
            "dist2","victim_ptr_hex","victim_gate020_hex"
        ])

    combos_new = not os.path.exists(COMBO_CSV)
    f_combos = open(COMBO_CSV, "a", newline="", encoding="utf-8"); combow = csv.writer(f_combos)
    if combos_new:
        combow.writerow(["t_start","t_end","dur","victim_label","victim_char",
                         "attacker_label","attacker_char","hits","total","hp_start","hp_end"])

    nomatch_new = not os.path.exists(NOMATCH_CSV)
    f_nomatch = open(NOMATCH_CSV, "a", newline="", encoding="utf-8"); nmw = csv.writer(f_nomatch)
    if nomatch_new:
        nmw.writerow(["t","victim_label","victim_char","dmg","notes","victim_gate020","gate020_child_dump"])

    gate_new = not os.path.exists(GATE_DUMP_CSV)
    f_gate = open(GATE_DUMP_CSV, "a", newline="", encoding="utf-8"); gtw = csv.writer(f_gate)
    if gate_new:
        headers = ["t","victim_label","victim_char","attacker_truth_label","attacker_truth_char","proof_mode","victim_gate020_hex"]
        headers += [f"dword_{i:02d}" for i in range(16)]
        headers += [f"lowbyte_{i:02d}" for i in range(16)]
        gtw.writerow(headers)

    last_base_by_slot={}
    y_off_by_base={}
    info={}
    prev_hp  = {}
    prev_last= {}
    combos   = {}
    hud_last = 0.0

    try:
        while True:
            # Resolve bases
            resolved={}
            for name, slot in SLOTS:
                base, changed = RESOLVER.resolve_base(slot)
                resolved[name]=base
                if base and last_base_by_slot.get(slot) != base:
                    last_base_by_slot[slot]=base
                    y_off_by_base[base]=pick_posy_off_no_jump(base)

            # Read fighters
            for name, base in resolved.items():
                if base:
                    y=y_off_by_base.get(base, 0xF4)
                    info[name]=read_fighter(base, y)
                else:
                    info[name]=None

            t=time.time()

            for victim_label, vbase in resolved.items():
                fi = info.get(victim_label)
                if not fi or not vbase: continue
                cur=fi["cur"]; last=fi["last"]
                hp_prev  = prev_hp.get(vbase)
                last_prev= prev_last.get(vbase)

                took=False; dmg=None
                if last is not None and last_prev is not None and last>0 and last!=last_prev:
                    took=True; dmg=last
                if (not took) and hp_prev is not None and cur is not None and cur < hp_prev:
                    took=True; dmg = hp_prev - cur

                if took and dmg and dmg >= MIN_HIT_DAMAGE:
                    # Opponent slots (fixed order C1,C2) for truth proofs
                    opp_slots = ["P2-C1","P2-C2"] if victim_label.startswith("P1") else ["P1-C1","P1-C2"]
                    opp_bases = {s: resolved.get(s) for s in opp_slots}

                    # 1) Try pointer proof truth
                    attacker_truth_label=""; attacker_truth_char=""; proof_mode=""; details=""
                    for pf in (proof_direct, proof_onehop, proof_twohop, proof_reverse):
                        slot, det = pf(vbase, opp_bases)
                        if slot:
                            proof_mode = pf.__name__.replace("proof_","")
                            details = det
                            attacker_truth_label = slot
                            abase = resolved.get(slot)
                            attacker_truth_char = next((v["name"] for v in info.values() if v and v.get("base")==abase), "")
                            break

                    # 2) Dump gate+0x020
                    gaddr, dwords, lowbytes = gate_dump16(vbase)
                    gate_hex = f"0x{gaddr:08X}" if gaddr else ""

                    # 3) Feed learner if we have truth
                    if attacker_truth_label:
                        AUTOBYTE.add_sample(t, victim_label, attacker_truth_label, lowbytes)
                        AUTOBYTE.maybe_lock(t)

                    # 4) Choose attacker for output
                    attacker_gate_label = AUTOBYTE.predict(victim_label, lowbytes) if lowbytes else ""

                    # Prefer proofs; else gate if locked; else no label
                    final_label = attacker_truth_label or attacker_gate_label
                    final_char  = attacker_truth_char
                    if (not final_label) and attacker_gate_label:
                        # derive char name for gate-based label
                        ab = resolved.get(attacker_gate_label)
                        final_char = next((v["name"] for v in info.values() if v and v.get("base")==ab), "")

                    # Print feedback minimal (static HUD remains)
                    if attacker_truth_label:
                        print(f"[{t:10.3f}] HIT {victim_label}({fi['name']}) -{int(dmg)} ← {attacker_truth_label}({attacker_truth_char}) [proof:{proof_mode}]")
                    elif attacker_gate_label:
                        print(f"[{t:10.3f}] HIT {victim_label}({fi['name']}) -{int(dmg)} ← {attacker_gate_label} [gate:{AUTOBYTE.scheme}[{AUTOBYTE.low_idx}]]")
                    else:
                        print(f"[{t:10.3f}] HIT {victim_label}({fi['name']}) -{int(dmg)} ← (unknown)")

                    # hp before/after
                    hp_before = prev_hp.get(vbase) if prev_hp.get(vbase) is not None else (fi["cur"]+dmg)
                    hp_after  = fi["cur"]

                    # distance (informational) — only if we have a concrete label
                    d2 = float("inf")
                    if final_label:
                        ab = resolved.get(final_label)
                        for blk in info.values():
                            if blk and blk.get("base")==ab:
                                d2=dist2(fi, blk); break

                    # write hit row
                    hitw.writerow([
                        f"{t:.6f}", victim_label, fi["name"], int(dmg), int(hp_before), int(hp_after),
                        attacker_truth_label, attacker_truth_char, proof_mode, details,
                        attacker_gate_label, (AUTOBYTE.scheme or ""), (AUTOBYTE.low_idx if AUTOBYTE.low_idx is not None else ""),
                        ("yes" if AUTOBYTE.locked else "no"),
                        (f"{AUTOBYTE.accuracy:.4f}" if AUTOBYTE.accuracy else ""),
                        (AUTOBYTE.coverage or ""),
                        (f"{d2:.3f}" if math.isfinite(d2) else ""), f"0x{vbase:08X}", gate_hex
                    ])
                    f_hits.flush()

                    # write gate dump (always, for post mortem)
                    row = [f"{t:.6f}", victim_label, fi["name"], attacker_truth_label, attacker_truth_char, proof_mode, gate_hex]
                    row += [ (f"0x{w:08X}" if (w is not None) else "") for w in dwords ]
                    row += [ (str(lb) if (lb is not None) else "") for lb in lowbytes ]
                    gtw.writerow(row); f_gate.flush()

                    # if neither proof nor gate known, log as nomatch with raw dump
                    if (not attacker_truth_label) and (not attacker_gate_label):
                        nmw.writerow([f"{t:.6f}", victim_label, fi["name"], int(dmg),
                                      "no proof & gate not locked/usable",
                                      gate_hex,
                                      "[" + ",".join((f"0x{w:08X}" if (w is not None) else "----") for w in dwords) + "]"])
                        f_nomatch.flush()

                    # Combo tracked on the *final* label (proof preferred)
                    st = combos.get(vbase)
                    if st is None or not st.active:
                        st = ComboState()
                        st.begin(t, vbase, victim_label, fi["name"], int(hp_before),
                                 final_label or "", final_char or "")
                        combos[vbase]=st
                    st.add_hit(t, int(dmg), int(hp_after))

                # persist victim prevs
                if cur is not None: prev_hp[vbase]=cur
                prev_last[vbase]=last

            # expire combos
            now=time.time()
            for vb, st in list(combos.items()):
                if st.expired(now):
                    s=st.end()
                    combow.writerow([f"{s['t0']:.6f}", f"{s['t1']:.6f}", f"{s['dur']:.6f}",
                                     s['victim_label'], s['victim_name'],
                                     s['attacker_label'] or "", s['attacker_name'] or "",
                                     s['hits'], s['total'], s['hp_start'], s['hp_end']])
                    f_combos.flush()
                    del combos[vb]

            # static HUD
            if HUD_MODE == "static" and (now - hud_last) >= (1.0/max(1,HUD_FPS)):
                lines=[]
                mP1 = read_meter(resolved.get("P1-C1")) if resolved.get("P1-C1") else None
                mP2 = read_meter(resolved.get("P2-C1")) if resolved.get("P2-C1") else None
                for nm,_ in SLOTS:
                    blk=info.get(nm)
                    meter = mP1 if nm=="P1-C1" else (mP2 if nm=="P2-C1" else None)
                    lines.append(fmt_line(nm, blk, meter))
                hud_static_render(lines); hud_last=now

            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        print("\nStopping. Writing summary…")
    finally:
        # Save learner summary
        try:
            with open(SUMMARY_JSON,"w",encoding="utf-8") as fh:
                json.dump({
                    "locked": AUTOBYTE.locked,
                    "scheme": AUTOBYTE.scheme,
                    "low_idx": AUTOBYTE.low_idx,
                    "accuracy": AUTOBYTE.accuracy,
                    "coverage": AUTOBYTE.coverage,
                    "preview": AUTOBYTE.preview
                }, fh, indent=2)
        except Exception: pass

        # Close files
        for f in ("f_hits","f_combos","f_nomatch","f_gate"):
            try:
                obj = locals().get(f)
                if obj: obj.close()
            except: pass

        print("Done. Logs in:", SESSION_DIR)

if __name__ == "__main__":
    main()
