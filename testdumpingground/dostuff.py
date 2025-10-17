# tvc_hud_and_collision.py
# pip install dolphin-memory-engine
#
# Focused build: HUD + HIT/COMBO logger + Hit-frame signature miner.
# - Ignores jump/attack_start/block/whiff for now (HIT-only).
# - Static CLI HUD with 20 Hz redraw; memory polled at 60 Hz for responsiveness.
# - Per-run log folder (./logs/YYYYMMDD_HHMMSS) so CSVs don’t overwrite.
#
# CSVs written into the run folder:
#   collisions.csv            (per-hit rows, unchanged schema)
#   combos.csv                (combo summaries)
#   hit_sig_events.csv        (per-hit flipped cells: pre/hit/post)
#   hit_signature_summary.csv (aggregate: rel_off, stride, hit_flips, nonhit_flips, lift)

import time, math, struct, csv, os, sys
import dolphin_memory_engine as dme
from collections import defaultdict, deque

# ========================= Config =========================
# Output toggles
SHOW_HUD            = True      # HUD panel (static)

# Polling / timing
POLL_HZ             = 60
INTERVAL            = 1.0 / POLL_HZ
HUD_REFRESH_HZ      = 20
HUD_REFRESH_INTERVAL= 1.0 / HUD_REFRESH_HZ

# HIT / COMBO
COMBO_TIMEOUT       = 0.60
MIN_HIT_DAMAGE      = 10
MAX_DIST2           = 100.0     # None to disable distance sanity check
METER_DELTA_MIN     = 5

# HUD thresholds
HP_MIN_MAX          = 10_000
HP_MAX_MAX          = 60_000
HP_GREEN            = 0.66
HP_YELLOW           = 0.33

# Auto Y sampling
SAMPLE_SECS         = 1.5
SAMPLE_DT           = 1.0 / 120

# Inputs / CSV names (actual files are placed into LOGDIR at runtime)
HIT_CSV             = "collisions.csv"
COMBO_CSV           = "combos.csv"
CHAR_AGNOSTIC_CSV   = "move_id_map_charagnostic.csv"

# Attack id offset
ATT_ID_OFF_PRIMARY  = 0x1E8
ATT_ID_OFF_SECOND   = 0x1EC

# ======== Hit Signature Miner (HIT-only) ========
HIT_SIG_PRE_FRAMES    = 1     # frames before hit to use as "pre"
HIT_SIG_POST_FRAMES   = 2     # frames after hit for "post" (single snapshot a few ms later)
NONHIT_SAMPLE_PERIOD  = 0.25  # seconds between background non-hit samples
HIT_SIG_TOPN          = 12    # show top-N on HUD (under the meter line)
HIT_SIG_SCAN_LO       = 0x000
HIT_SIG_SCAN_HI       = 0x600
HIT_SIG_STRIDES       = (1, 2, 4)
HIT_SIG_EVENT_CSV     = "hit_sig_events.csv"
HIT_SIG_SUMMARY_CSV   = "hit_signature_summary.csv"

# ========================= Colors =========================
class Colors:
    P1_BRIGHT = '\033[96m'; P1_NORMAL = '\033[94m'
    P2_BRIGHT = '\033[91m'; P2_NORMAL = '\033[31m'
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; PURPLE = '\033[95m'
    BOLD = '\033[1m'; UNDERLINE = '\033[4m'; RESET = '\033[0m'; DIM = '\033[2m'

def _enable_windows_ansi():
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            h = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                kernel32.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        except Exception:
            pass

_enable_windows_ansi()

# ========================= Slots / Offsets =========================
# (US build pointers)
PTR_P1_CHAR1 = 0x803C9FCC
PTR_P1_CHAR2 = 0x803C9FDC
PTR_P2_CHAR1 = 0x803C9FD4
PTR_P2_CHAR2 = 0x803C9FE4
SLOTS = [("P1-C1", PTR_P1_CHAR1), ("P1-C2", PTR_P1_CHAR2),
         ("P2-C1", PTR_P2_CHAR1), ("P2-C2", PTR_P2_CHAR2)]

# Fighter object layout
OFF_MAX_HP   = 0x24
OFF_CUR_HP   = 0x28
OFF_AUX_HP   = 0x2C
OFF_LAST_HIT = 0x40   # victim "last damage chunk"
OFF_CHAR_ID  = 0x14

# Positions
POSX_OFF     = 0xF0
POSY_CANDS   = [0xF4, 0xEC, 0xE8, 0xF8, 0xFC]

# Meter
METER_OFF_PRIMARY   = 0x4C
METER_OFF_SECONDARY = 0x9380 + 0x4C   # mirrored bank

# RAM ranges
MEM1_LO, MEM1_HI = 0x80000000, 0x81800000
MEM2_LO, MEM2_HI = 0x90000000, 0x94000000
BAD_PTRS = {0x00000000, 0x80520000}
INDIR_PROBES = [0x10,0x18,0x1C,0x20]
LAST_GOOD_TTL = 1.0

# ========================= Character Names =========================
CHAR_NAMES = {
    1:"Ken the Eagle",2:"Casshan",3:"Tekkaman",4:"Polimar",5:"Yatterman-1",
    6:"Doronjo",7:"Ippatsuman",8:"Jun the Swan",10:"Karas",12:"Ryu",
    13:"Chun-Li",14:"Batsu",15:"Morrigan",16:"Alex",17:"Viewtiful Joe",
    18:"Volnutt",19:"Roll",20:"Saki",21:"Soki",26:"Tekkaman Blade",
    27:"Joe the Condor",28:"Yatterman-2",29:"Zero",30:"Frank West",
}

# ========================= Mapping loader =========================
def load_charagnostic_csv(path=CHAR_AGNOSTIC_CSV):
    mapping = {}
    try:
        if not os.path.exists(path):
            print(f"(Mapping) no mapping CSV found at '{path}' — continuing without mapped names.")
            return mapping
        with open(path, newline='', encoding='utf-8') as fh:
            rdr = csv.DictReader(fh)
            for r in rdr:
                try:
                    aid = int(r.get('atk_id_dec') or r.get('atk_id') or "")
                except Exception:
                    continue
                label = (r.get('generic_label') or r.get('top_label') or "").strip()
                if not label:
                    label = (r.get('top_label') or "").strip()
                mapping[aid] = label
        print(f"(Mapping) loaded {len(mapping)} entries from {path}")
    except Exception as e:
        print(f"(Mapping) error loading {path}: {e}")
    return mapping

CHAR_AGNOSTIC_MAP = load_charagnostic_csv()

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

def rbytes(addr, n):
    try:
        b = dme.read_bytes(addr, n)
        return None if b is None else bytes(b)
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

# ========================= Resolver / Y-Auto =========================
def _variance(vals):
    n=len(vals)
    if n<2: return 0.0
    m=sum(vals)/n
    return sum((v-m)*(v-m) for v in vals)/(n-1)

def _slope(vals):
    if len(vals)<2: return 0.0
    return (vals[-1]-vals[0])/(len(vals)-1)

def _corr(a,b):
    n=len(a)
    if n<2 or n!=len(b): return 0.0
    ma=sum(a)/n; mb=sum(b)/n
    num=sum((x-ma)*(y-mb) for x,y in zip(a,b))
    da=math.sqrt(sum((x-ma)*(x-ma) for x in a))
    db=math.sqrt(sum((y-mb)*(y-mb) for y in b))
    if da==0 or db==0: return 0.0
    return num/(da*db)

def pick_posy_off_no_jump(base):
    xs=[]; ys={off:[] for off in POSY_CANDS}
    end=time.time()+SAMPLE_SECS
    while time.time()<end:
        x=rdf32(base+POSX_OFF); xs.append(x if x is not None else 0.0)
        for off in POSY_CANDS:
            y=rdf32(base+off); ys[off].append(y if y is not None else 0.0)
        time.sleep(SAMPLE_DT)
    if len(xs)<10: return 0xF4
    best, best_off=None, None
    for off, series in ys.items():
        if len(series)<10: continue
        s=abs(_slope(series)); v=_variance(series); r=abs(_corr(series,xs))
        score=(0.6*(1/(1+v)))+(0.3*(1/(1+s)))+(0.1*(1/(1+r)))
        if best is None or score>best: best, best_off=score, off
    return best_off or 0xF4

class SlotResolver:
    def __init__(self):
        self.last_good = {}   # slot_addr -> (base, ttl)

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

# ========================= Meter cache =========================
class MeterAddrCache:
    def __init__(self): self.addr_by_base={}
    def drop(self, base): self.addr_by_base.pop(base, None)
    def get(self, base):
        if base in self.addr_by_base: return self.addr_by_base[base]
        for a in (base+METER_OFF_PRIMARY, base+METER_OFF_SECONDARY):
            v=rd32(a)
            if v in (50000, 0xC350) or (v is not None and 0<=v<=200_000):
                self.addr_by_base[base]=a; return a
        self.addr_by_base[base]=base+METER_OFF_PRIMARY
        return self.addr_by_base[base]

METER_CACHE = MeterAddrCache()

def read_meter(base):
    if not base: return None
    a=METER_CACHE.get(base); v=rd32(a)
    if v is None or v<0 or v>200_000: return None
    return v

# ========================= Fighter read =========================
def read_fighter(base, posy_off):
    if not base:
        return None
    max_hp = rd32(base + OFF_MAX_HP)
    cur_hp = rd32(base + OFF_CUR_HP)
    aux_hp = rd32(base + OFF_AUX_HP)
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
        "max": max_hp,
        "cur": cur_hp,
        "aux": aux_hp,
        "id": cid,
        "name": name,
        "x": x,
        "y": y,
        "last": last,
    }

def dist2(a,b):
    if a is None or b is None: return float("inf")
    ax,ay=a.get("x"),a.get("y"); bx,by=b.get("x"),b.get("y")
    if None in (ax,ay,bx,by): return float("inf")
    dx=ax-bx; dy=ay-by
    return dx*dx+dy*dy

# ========================= Combo state =========================
class ComboState:
    def __init__(self):
        self.active=False
        self.victim_base=None
        self.victim_label=None
        self.victim_name=None
        self.attacker_label=None
        self.attacker_name=None
        self.attacker_move=None
        self.team_guess=None
        self.hits=0; self.total=0
        self.hp_start=0; self.hp_end=0
        self.start_t=0.0; self.last_t=0.0

    def begin(self, t, victim_base, victim_label, victim_name, hp_before,
              attacker_label, attacker_name, attacker_move, team_guess):
        self.active=True
        self.victim_base=victim_base
        self.victim_label=victim_label
        self.victim_name=victim_name
        self.attacker_label=attacker_label
        self.attacker_name=attacker_name
        self.attacker_move=attacker_move
        self.team_guess=team_guess
        self.hits=0; self.total=0
        self.hp_start=hp_before; self.hp_end=hp_before
        self.start_t=t; self.last_t=t

    def add_hit(self, t, dmg, hp_after):
        self.hits+=1; self.total+=dmg
        self.hp_end=hp_after; self.last_t=t

    def expired(self, t): return self.active and (t-self.last_t) > COMBO_TIMEOUT

    def end(self):
        self.active=False
        return {
            "t0": self.start_t, "t1": self.last_t, "dur": self.last_t-self.start_t,
            "victim_label": self.victim_label, "victim_name": self.victim_name,
            "attacker_label": self.attacker_label, "attacker_name": self.attacker_name,
            "attacker_move": self.attacker_move, "team_guess": self.team_guess,
            "hits": self.hits, "total": self.total,
            "hp_start": self.hp_start, "hp_end": self.hp_end
        }

# ========================= HUD formatting =========================
def fmt_line(label, blk, meter=None):
    if not blk:
        return f"{Colors.DIM}{label}[--------] n/a{Colors.RESET}"
    player_color = Colors.P1_BRIGHT if label.startswith("P1") else Colors.P2_BRIGHT
    label_color  = Colors.P1_NORMAL if label.startswith("P1") else Colors.P2_NORMAL

    pct = (blk["cur"] / blk["max"]) if blk["max"] else None
    if pct is None: hp_color = Colors.DIM; pct_str=""
    else:
        if   pct > HP_GREEN: hp_color=Colors.GREEN
        elif pct > HP_YELLOW: hp_color=Colors.YELLOW
        else: hp_color=Colors.RED
        pct_str=f"{hp_color}({pct*100:5.1f}%){Colors.RESET}"

    char=f" {player_color}{blk['name']:<16}{Colors.RESET}"
    m  = f" | {Colors.PURPLE}M:{meter}{Colors.RESET}" if meter is not None else f" | {Colors.DIM}M:--{Colors.RESET}"
    x  = f" | X:{blk['x']:.3f}" if blk.get("x") is not None else f" | {Colors.DIM}X:--{Colors.RESET}"
    y  = f" Y:{blk['y']:.3f}" if blk.get("y") is not None else f" {Colors.DIM}Y:--{Colors.RESET}"
    last = blk.get("last")
    dmg_str = f" | lastDmg:{last:5d}" if last else f" | {Colors.DIM}lastDmg:--{Colors.RESET}"
    hp_display=f"{hp_color}{blk['cur']}/{blk['max']}{Colors.RESET}"

    return f"{label_color}{label}{Colors.RESET}[{Colors.DIM}{blk['base']:08X}{Colors.RESET}]{char} {hp_display} {pct_str}{m}{x}{y}{dmg_str}"

# ========================= Attack-id helper =========================
def read_attack_ids(base):
    if not base: return (None,None)
    a = rd32(base + ATT_ID_OFF_PRIMARY)
    b = rd32(base + ATT_ID_OFF_SECOND)
    try: ai = int(a) if a is not None else None
    except: ai = None
    try: bi = int(b) if b is not None else None
    except: bi = None
    return ai, bi

# ========================= Hit Signature Miner =========================
class HitSignatureTracker:
    """
    Mines (+offset, stride) that flip on real HITs but rarely during non-hit frames.
    Keeps rolling per-slot snapshots each frame.
    """
    def __init__(self, event_writer=None, summary_writer=None):
        self.prev_snap  = {}   # slot -> {stride: [vals]}
        self.curr_snap  = {}   # slot -> {stride: [vals]}
        self.hist = defaultdict(lambda: {"hit":0, "nonhit":0})
        self._event_writer   = event_writer
        self._summary_writer = summary_writer
        self._last_nonhit_sample = 0.0
        self._last_top_lines = []

    def _read_span(self, base, stride):
        if stride == 4:
            out=[]
            for rel in range(HIT_SIG_SCAN_LO, HIT_SIG_SCAN_HI, 4):
                v = rd32(base + rel)
                out.append(None if v is None else (v & 0xFFFFFFFF))
            return out
        raw = rbytes(base + HIT_SIG_SCAN_LO, HIT_SIG_SCAN_HI - HIT_SIG_SCAN_LO)
        if not raw: return []
        if stride == 2:
            if len(raw) < 2: return []
            return [ (raw[i]<<8) | raw[i+1] for i in range(0, len(raw)-(len(raw)%2), 2) ]
        else:
            return list(raw)

    def snapshot_now(self, slot_label, base):
        if not base: return
        snap = {}
        for s in HIT_SIG_STRIDES:
            snap[s] = self._read_span(base, s)
        self.prev_snap[slot_label] = self.curr_snap.get(slot_label, snap)
        self.curr_snap[slot_label] = snap

    def _diff_indices(self, arrA, arrB, stride):
        diffs=[]
        if not arrA or not arrB: return diffs
        n=min(len(arrA), len(arrB))
        for i in range(n):
            a=arrA[i]; b=arrB[i]
            if a is None or b is None: continue
            if a!=b:
                rel = HIT_SIG_SCAN_LO + i*stride
                diffs.append((rel, stride, a, b))
        return diffs

    def _emit_hit_rows(self, t, slot, base_hex, diffs, post_vals_by_relstride):
        if not self._event_writer: return
        for (rel,stride,pre_val,hit_val) in diffs:
            post_val = post_vals_by_relstride.get((rel,stride))
            self._event_writer.writerow([
                f"{t:.6f}", "HIT", slot, base_hex,
                f"0x{rel:03X}", stride,
                pre_val, hit_val, (post_val if post_val is not None else "")
            ])

    def on_true_hit(self, t, slot_label, base):
        pre  = self.prev_snap.get(slot_label)
        hit  = self.curr_snap.get(slot_label)
        if not pre or not hit: return
        diffs=[]
        for s in HIT_SIG_STRIDES:
            diffs.extend(self._diff_indices(pre.get(s), hit.get(s), s))
        # simple post snapshot (single read)
        post={}
        for s in HIT_SIG_STRIDES:
            arr = self._read_span(base, s)
            if arr:
                for i,v in enumerate(arr):
                    rel = HIT_SIG_SCAN_LO + i*s
                    post[(rel,s)] = v
        for (rel,stride,_,_) in diffs:
            self.hist[(rel,stride)]["hit"] += 1
        self._emit_hit_rows(t, slot_label, f"0x{base:08X}", diffs, post)

    def background_nonhit_sample(self, t, slots_with_bases):
        if (t - self._last_nonhit_sample) < NONHIT_SAMPLE_PERIOD:
            return
        self._last_nonhit_sample = t
        for slot_label, base in slots_with_bases:
            pre  = self.prev_snap.get(slot_label)
            cur  = self.curr_snap.get(slot_label)
            if not pre or not cur: continue
            for s in HIT_SIG_STRIDES:
                diffs = self._diff_indices(pre.get(s), cur.get(s), s)
                for (rel,stride,_,_) in diffs:
                    self.hist[(rel,stride)]["nonhit"] += 1

    def top_lines(self, topn=HIT_SIG_TOPN):
        items=[]
        for (rel,stride), c in self.hist.items():
            hit = c["hit"]; nh = c["nonhit"]
            if hit == 0: continue
            lift = hit / (nh + 1.0)
            items.append((lift, hit, nh, rel, stride))
        items.sort(reverse=True)
        out=[]
        if items:
            out.append("HIT signature (lift  +off  s  hit/nonhit)")
            for (lift, hit, nh, rel, stride) in items[:topn]:
                out.append(f"  {lift:5.2f}  +0x{rel:03X}  s{stride}  {hit}/{nh}")
        self._last_top_lines = out
        return out

    def write_summary_csv(self):
        if not self._summary_writer: return
        rows=[]
        for (rel,stride), c in self.hist.items():
            hit = c["hit"]; nh = c["nonhit"]
            if hit == 0 and nh == 0: continue
            lift = hit / (nh + 1.0)
            rows.append((lift, rel, stride, hit, nh))
        rows.sort(reverse=True)
        for (lift, rel, stride, hit, nh) in rows:
            self._summary_writer.writerow([f"0x{rel:03X}", stride, hit, nh, f"{lift:.4f}"])

# ========================= Static Screen Helpers =========================
EVENTS = deque(maxlen=12)

def _trim(s, n=120):
    if s is None: return ""
    s = str(s)
    return s if len(s) <= n else s[:n-3] + "..."

def log_event(msg):
    if not msg: return
    EVENTS.append(_trim(msg))

def render_screen(hud_lines, meter_summary, extra_lines):
    sys.stdout.write("\033[H\033[2J")
    sys.stdout.write(Colors.BOLD + "TvC HUD  (static)  |  P1 vs P2  |  C1/C2 status\n" + Colors.RESET)
    for ln in hud_lines:
        sys.stdout.write(ln + "\n")
    sys.stdout.write(meter_summary + "\n")
    for ln in (extra_lines or []):
        sys.stdout.write(Colors.DIM + _trim(ln) + Colors.RESET + "\n")
    sys.stdout.write("-" * 100 + "\n")
    sys.stdout.write(Colors.BOLD + "Events (latest first):" + Colors.RESET + "\n")
    for ln in reversed(EVENTS):
        sys.stdout.write(_trim(ln) + "\n")
    sys.stdout.flush()

# ========================= Main =========================
def main():
    print("HUD + HIT logger + Hit-signature miner: waiting for Dolphin…")
    hook()
    print("Hooked.")

    # Per-run logs folder
    LOGDIR = os.path.join("logs", time.strftime("%Y%m%d_%H%M%S"))
    os.makedirs(LOGDIR, exist_ok=True)

    # CSV writers (opened in LOGDIR)
    hit_path = os.path.join(LOGDIR, HIT_CSV)
    combo_path = os.path.join(LOGDIR, COMBO_CSV)
    hs_evt_path = os.path.join(LOGDIR, HIT_SIG_EVENT_CSV)
    hs_sum_path = os.path.join(LOGDIR, HIT_SIG_SUMMARY_CSV)

    hit_new = not os.path.exists(hit_path)
    fh = open(hit_path, "a", newline=""); hitw = csv.writer(fh)
    if hit_new:
        hitw.writerow(["t","victim_label","victim_char","dmg","hp_before","hp_after",
                       "team_guess","attacker_label","attacker_char","attacker_id_dec","attacker_id_hex",
                       "attacker_move","dist2","victim_ptr_hex"])

    combo_new = not os.path.exists(combo_path)
    fc = open(combo_path, "a", newline=""); combow = csv.writer(fc)
    if combo_new:
        combow.writerow(["t_start","t_end","dur","victim_label","victim_char",
                         "attacker_label","attacker_char","attacker_move","team_guess",
                         "hits","total","hp_start","hp_end"])

    hs_evt_new = not os.path.exists(hs_evt_path)
    hs_fh = open(hs_evt_path, "a", newline=""); hs_w = csv.writer(hs_fh)
    if hs_evt_new:
        hs_w.writerow(["t","label","slot","base_hex","rel_off_hex","stride","pre","hit","post"])

    hs_sum_new = True
    hss_fh = open(hs_sum_path, "w", newline=""); hss_w = csv.writer(hss_fh)
    hss_w.writerow(["rel_off_hex","stride","hit_flips","nonhit_flips","lift"])

    # Construct miners
    HITSIG = HitSignatureTracker(event_writer=hs_w, summary_writer=hss_w)

    last_base_by_slot={}
    y_off_by_base={}
    meter_prev = {"P1":0,"P2":0}
    meter_now  = {"P1":0,"P2":0}
    p1_c1_base=None; p2_c1_base=None

    prev_hp  = {}
    prev_last= {}
    combos   = {}

    # Static HUD init
    if SHOW_HUD:
        sys.stdout.write("\033[?25l")  # hide cursor
        sys.stdout.flush()
    _last_hud = 0.0

    try:
        while True:
            # Resolve bases
            resolved=[]
            for name, slot in SLOTS:
                base, changed = RESOLVER.resolve_base(slot)
                resolved.append((name, slot, base, changed))

            # Base changes & pick Y
            for name, slot, base, changed in resolved:
                if base and last_base_by_slot.get(slot) != base:
                    last_base_by_slot[slot]=base
                    METER_CACHE.drop(base)
                    y_off_by_base[base]=pick_posy_off_no_jump(base)

            # Identify team leaders
            for name, slot, base, _ in resolved:
                if name=="P1-C1" and base: p1_c1_base=base
                if name=="P2-C1" and base: p2_c1_base=base

            # Meters
            meter_prev["P1"]=meter_now["P1"]; meter_prev["P2"]=meter_now["P2"]
            m1=read_meter(p1_c1_base); m2=read_meter(p2_c1_base)
            if m1 is not None: meter_now["P1"]=m1
            if m2 is not None: meter_now["P2"]=m2

            # Fighters + per-frame snapshots for signature miner
            info={}
            for name, slot, base, _ in resolved:
                if base:
                    y=y_off_by_base.get(base, 0xF4)
                    info[name]=read_fighter(base, y)
                    # snapshot for hit miner
                    HITSIG.snapshot_now(name, base)
                else:
                    info[name]=None

            t=time.time()

            # HIT detection (unchanged core logic)
            for name, slot, base, _ in resolved:
                fi = info.get(name)
                if not fi or not base: continue

                cur=fi["cur"]; last=fi["last"]
                hp_prev  = prev_hp.get(base)
                last_prev= prev_last.get(base)

                took=False; dmg=None

                # Primary: +0x40 changes (victim last-chunk)
                if last is not None and last_prev is not None and last>0 and last!=last_prev:
                    took=True; dmg=last

                # Fallback: HP drop
                if (not took) and hp_prev is not None and cur is not None and cur < hp_prev:
                    took=True; dmg = hp_prev - cur

                if took and dmg and dmg >= MIN_HIT_DAMAGE:
                    # Team guess via meter delta
                    dP1=(meter_now["P1"] or 0) - (meter_prev["P1"] or 0)
                    dP2=(meter_now["P2"] or 0) - (meter_prev["P2"] or 0)
                    team_guess = None
                    if abs(dP1 - dP2) >= METER_DELTA_MIN:
                        team_guess = "P1" if dP1>dP2 else "P2"

                    # nearest opponent as attacker guess
                    opp = ["P2-C1","P2-C2"] if name.startswith("P1") else ["P1-C1","P1-C2"]
                    cand=[info.get(k) for k in opp if info.get(k)]
                    best=None; best_label=None; d2=float("inf")
                    if cand:
                        best=min(cand, key=lambda o: dist2(fi,o))
                        d2=dist2(fi,best)
                        if (MAX_DIST2 is not None) and (d2 > MAX_DIST2):
                            best=None; d2=-1.0
                    if best:
                        for k,v in info.items():
                            if v is best: best_label=k; break

                    # attack id (if available)
                    attacker_base = best["base"] if best else None
                    atk_id = None; atk_sub = None; atk_name = ""
                    if attacker_base:
                        atk_id, atk_sub = read_attack_ids(attacker_base)
                        if isinstance(atk_id, int):
                            atk_name = CHAR_AGNOSTIC_MAP.get(int(atk_id)) or ""

                    # log hit row
                    hp_before = hp_prev if hp_prev is not None else (fi["cur"]+dmg)
                    hp_after  = fi["cur"]

                    hitw.writerow([f"{t:.6f}", name, fi["name"], int(dmg),
                                   int(hp_before), int(hp_after), team_guess or "",
                                   best_label or "", (best and best["name"]) or "",
                                   (int(atk_id) if atk_id is not None else ""),
                                   (hex(int(atk_id)) if atk_id is not None else ""),
                                   (atk_name or ""), f"{d2 if d2!=float('inf') else ''}", f"0x{base:08X}"])
                    fh.flush()

                    # Event log line
                    log_event(f"[{t:10.3f}] HIT  victim={name:<5}({fi['name']:<12}) "
                              f"dmg={int(dmg):5d} hp:{int(hp_before)}->{int(hp_after)} "
                              f"team={team_guess or '--'} attacker≈{(best_label or '--'):<5}"
                              f"({(best and best['name']) or '--'}) d2={(d2 if d2!=float('inf') else -1):.3f} "
                              f"atk_id={(atk_id or '--')} atk_sub={(atk_sub or '--')}")

                    # Combo state
                    st = combos.get(base)
                    if st is None or not st.active:
                        st = ComboState()
                        st.begin(t, base, name, fi["name"], int(hp_before),
                                 best_label, (best and best["name"]), "", team_guess)
                        combos[base]=st
                    st.add_hit(t, int(dmg), int(hp_after))

                    # Feed hit to signature miner
                    HITSIG.on_true_hit(t, name, base)

                # update prevs
                if cur is not None: prev_hp[base]=cur
                prev_last[base]=last

            # End expired combos
            for vb, st in list(combos.items()):
                if st.expired(t):
                    summary=st.end()
                    combow.writerow([f"{summary['t0']:.6f}", f"{summary['t1']:.6f}", f"{summary['dur']:.6f}",
                                     summary['victim_label'], summary['victim_name'],
                                     summary['attacker_label'] or "", summary['attacker_name'] or "",
                                     summary.get('attacker_move') or "", summary['team_guess'] or "",
                                     summary['hits'], summary['total'],
                                     summary['hp_start'], summary['hp_end']])
                    fc.flush()
                    log_event(f"[{summary['t0']:10.3f}→{summary['t1']:10.3f}] COMBO "
                              f"{(summary['attacker_label'] or '--')}({(summary['attacker_name'] or '--')}) "
                              f"→ {summary['victim_label']}({summary['victim_name']})  "
                              f"hits={summary['hits']} total={summary['total']} "
                              f"hp:{summary['hp_start']}→{summary['hp_end']} team={summary['team_guess'] or '--'}")
                    del combos[vb]

            # Background non-hit sampling & signature summary
            slots_with_bases = [(nm, info[nm]["base"]) for nm,_ in SLOTS if info.get(nm)]
            HITSIG.background_nonhit_sample(t, slots_with_bases)
            # Periodically snapshot summary CSV (every ~5s)
            if int(t*10) % 50 == 0:
                HITSIG.write_summary_csv()

            # Static HUD render (20 Hz)
            if SHOW_HUD and (t - _last_hud) >= HUD_REFRESH_INTERVAL:
                _last_hud = t
                lines=[]
                mP1 = meter_now["P1"] if info.get("P1-C1") else None
                mP2 = meter_now["P2"] if info.get("P2-C1") else None
                for nm,_ in SLOTS:
                    blk=info.get(nm)
                    meter = mP1 if nm=="P1-C1" else (mP2 if nm=="P2-C1" else None)
                    lines.append(fmt_line(nm, blk, meter))
                meter_summary = (f"{Colors.PURPLE}Meters{Colors.RESET}  "
                                 f"P1:{meter_now['P1'] or 0:>6} (Δ{(meter_now['P1']-meter_prev['P1']) or 0:+})  "
                                 f"P2:{meter_now['P2'] or 0:>6} (Δ{(meter_now['P2']-meter_prev['P2']) or 0:+})")
                # Add hit-signature top lines to HUD
                sig_lines = HITSIG.top_lines(HIT_SIG_TOPN)
                render_screen(lines, meter_summary, sig_lines)

            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        pass
    finally:
        # Close CSVs
        for fh_ in (fh, fc, hs_fh, hss_fh):
            try:
                if fh_: fh_.close()
            except Exception:
                pass
        # Show cursor again
        if SHOW_HUD:
            sys.stdout.write("\033[?25h")
            sys.stdout.flush()

if __name__ == "__main__":
    main()
