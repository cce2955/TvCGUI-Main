# tvc_hud_and_collision.py
# pip install dolphin-memory-engine
# HUD (names/HP/meter/pos/lastDmg) + Collision/Combo logger in one loop.

import time, math, struct, csv, os
import dolphin_memory_engine as dme

# ========================= Config =========================
# Output toggles
SHOW_HUD            = True
SHOW_HITS           = True          # per-hit lines
SHOW_COMBOS         = True          # combo summary lines

# Logger/Heuristics
POLL_HZ             = 60
INTERVAL            = 1.0 / POLL_HZ
COMBO_TIMEOUT       = 0.60          # same victim: max gap between hits
MIN_HIT_DAMAGE      = 10            # ignore tiny blips
MAX_DIST2           = 100.0         # None to disable distance sanity check
METER_DELTA_MIN     = 5             # min meter change to trust team guess

# HUD thresholds
HP_MIN_MAX          = 10_000
HP_MAX_MAX          = 60_000
HP_GREEN            = 0.66
HP_YELLOW           = 0.33

# Auto Y sampling
SAMPLE_SECS         = 1.5
SAMPLE_DT           = 1.0 / 120

# CSV files
HIT_CSV             = "collisions.csv"
COMBO_CSV           = "combos.csv"

# ========================= Colors =========================
class Colors:
    P1_BRIGHT = '\033[96m'; P1_NORMAL = '\033[94m'
    P2_BRIGHT = '\033[91m'; P2_NORMAL = '\033[31m'
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; PURPLE = '\033[95m'
    BOLD = '\033[1m'; UNDERLINE = '\033[4m'; RESET = '\033[0m'; DIM = '\033[2m'

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
    if not base: return None
    max_hp=rd32(base+OFF_MAX_HP); cur_hp=rd32(base+OFF_CUR_HP); aux_hp=rd32(base+OFF_AUX_HP)
    if not looks_like_hp(max_hp,cur_hp,aux_hp): return None
    cid=rd32(base+OFF_CHAR_ID)
    name=CHAR_NAMES.get(cid, f"ID_{cid}") if cid is not None else "???"
    x=rdf32(base+POSX_OFF)
    y=rdf32(base+posy_off) if posy_off is not None else None
    last=rd32(base+OFF_LAST_HIT)
    if last is None or last<0 or last>200_000: last=None
    return {"base":base,"max":max_hp,"cur":cur_hp,"aux":aux_hp,"id":cid,"name":name,
            "x":x,"y":y,"last":last}

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
        self.team_guess=None
        self.hits=0; self.total=0
        self.hp_start=0; self.hp_end=0
        self.start_t=0.0; self.last_t=0.0

    def begin(self, t, victim_base, victim_label, victim_name, hp_before,
              attacker_label, attacker_name, team_guess):
        self.active=True
        self.victim_base=victim_base
        self.victim_label=victim_label
        self.victim_name=victim_name
        self.attacker_label=attacker_label
        self.attacker_name=attacker_name
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
            "team_guess": self.team_guess, "hits": self.hits, "total": self.total,
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

# ========================= Main =========================
def main():
    print("HUD + Collision logger: waiting for Dolphin…")
    hook()
    print("Hooked.")

    # CSV files
    hit_new = not os.path.exists(HIT_CSV)
    fh = open(HIT_CSV, "a", newline=""); hitw = csv.writer(fh)
    if hit_new:
        hitw.writerow(["t","victim_label","victim_char","dmg","hp_before","hp_after",
                       "team_guess","attacker_label","attacker_char","dist2","victim_ptr_hex"])

    combo_new = not os.path.exists(COMBO_CSV)
    fc = open(COMBO_CSV, "a", newline=""); combow = csv.writer(fc)
    if combo_new:
        combow.writerow(["t_start","t_end","dur","victim_label","victim_char",
                         "attacker_label","attacker_char","team_guess",
                         "hits","total","hp_start","hp_end"])

    last_base_by_slot={}
    y_off_by_base={}
    meter_prev = {"P1":0,"P2":0}
    meter_now  = {"P1":0,"P2":0}
    p1_c1_base=None; p2_c1_base=None

    prev_hp  = {}   # base -> last hp
    prev_last= {}   # base -> last (+0x40) value
    combos   = {}   # base -> ComboState

    try:
        while True:
            # Resolve bases
            resolved=[]
            for name, slot in SLOTS:
                base, changed = RESOLVER.resolve_base(slot)
                resolved.append((name, slot, base, changed))

            # Track base changes & pick Y
            for name, slot, base, changed in resolved:
                if base and last_base_by_slot.get(slot) != base:
                    last_base_by_slot[slot]=base
                    METER_CACHE.drop(base)
                    y_off_by_base[base]=pick_posy_off_no_jump(base)

            # Identify team C1 bases
            for name, slot, base, _ in resolved:
                if name=="P1-C1" and base: p1_c1_base=base
                if name=="P2-C1" and base: p2_c1_base=base

            # Read meters (C1 only)
            meter_prev["P1"]=meter_now["P1"]; meter_prev["P2"]=meter_now["P2"]
            m1=read_meter(p1_c1_base); m2=read_meter(p2_c1_base)
            if m1 is not None: meter_now["P1"]=m1
            if m2 is not None: meter_now["P2"]=m2

            # Read fighters
            info={}
            for name, slot, base, _ in resolved:
                if base:
                    y=y_off_by_base.get(base, 0xF4)
                    info[name]=read_fighter(base, y)
                else:
                    info[name]=None

            t=time.time()

            # End expired combos
            for vb, st in list(combos.items()):
                if st.expired(t):
                    summary=st.end()
                    if SHOW_COMBOS:
                        print(f"[{summary['t0']:10.3f}→{summary['t1']:10.3f}] COMBO "
                              f"{(summary['attacker_label'] or '--')}({(summary['attacker_name'] or '--')}) "
                              f"→ {summary['victim_label']}({summary['victim_name']})  "
                              f"hits={summary['hits']}  total={summary['total']}  "
                              f"hp:{summary['hp_start']}→{summary['hp_end']}  team={summary['team_guess'] or '--'}")
                    combow.writerow([f"{summary['t0']:.6f}", f"{summary['t1']:.6f}", f"{summary['dur']:.6f}",
                                     summary['victim_label'], summary['victim_name'],
                                     summary['attacker_label'] or "", summary['attacker_name'] or "",
                                     summary['team_guess'] or "", summary['hits'], summary['total'],
                                     summary['hp_start'], summary['hp_end']])
                    fc.flush()
                    del combos[vb]

            # Detect hits (per victim)
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

                    # Nearest opponent as attacker guess
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

                    hp_before = hp_prev if hp_prev is not None else (fi["cur"]+dmg)
                    hp_after  = fi["cur"]

                    if SHOW_HITS:
                        print(f"[{t:10.3f}] HIT  victim={name:<5}({fi['name']:<16}) "
                              f"dmg={int(dmg):5d}  hp:{int(hp_before)}->{int(hp_after)}  "
                              f"team={team_guess or '--'}  attacker≈{(best_label or '--'):<5}"
                              f"({(best and best['name']) or '--'})  dist2={(d2 if d2!=float('inf') else -1):.3f}")

                    # CSV row
                    hitw.writerow([f"{t:.6f}", name, fi["name"], int(dmg),
                                   int(hp_before), int(hp_after), team_guess or "",
                                   best_label or "", (best and best["name"]) or "",
                                   f"{d2 if d2!=float('inf') else ''}", f"0x{base:08X}"])
                    fh.flush()

                    # Combo state
                    st = combos.get(base)
                    if st is None or not st.active:
                        st = ComboState()
                        st.begin(t, base, name, fi["name"], int(hp_before),
                                 best_label, (best and best["name"]), team_guess)
                        combos[base]=st
                    st.add_hit(t, int(dmg), int(hp_after))

                # update prevs
                if cur is not None: prev_hp[base]=cur
                prev_last[base]=last

            # HUD line
            if SHOW_HUD:
                lines=[]
                # pre-read meters for C2 slots display: show only on C1
                mP1 = meter_now["P1"] if info.get("P1-C1") else None
                mP2 = meter_now["P2"] if info.get("P2-C1") else None
                for nm,_ in SLOTS:
                    blk=info.get(nm)
                    meter = mP1 if nm=="P1-C1" else (mP2 if nm=="P2-C1" else None)
                    lines.append(fmt_line(nm, blk, meter))
                print(" | ".join(lines))

            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        try: fh.close()
        except Exception: pass
        try: fc.close()
        except Exception: pass

if __name__ == "__main__":
    main()