# tvc_hud_and_collision_with_labeler_prompt_none.py
# pip install dolphin-memory-engine
# HUD + Collision logger; prompts when exact (atk_id, char_id) pair missing,
# and also prompts when atk_id cannot be read (treats unknown atk as key -1).

import time, math, struct, csv, os
import dolphin_memory_engine as dme

# ========================= Config =========================
SHOW_HUD            = True
SHOW_HITS           = True
SHOW_COMBOS         = True

POLL_HZ             = 60
INTERVAL            = 1.0 / POLL_HZ
COMBO_TIMEOUT       = 0.60
MIN_HIT_DAMAGE      = 10
MAX_DIST2           = 100.0
METER_DELTA_MIN     = 5

HIT_CSV             = "collisions.csv"
COMBO_CSV           = "combos.csv"
MAPPING_CSV         = "move_id_map_charagnostic.csv"   # your mapping
UNMATCHED_LOG       = "unmatched_moves.csv"

# ========================= Colors =========================
class Colors:
    DIM = '\033[2m'; RESET = '\033[0m'
    P1_BRIGHT = '\033[96m'; P2_BRIGHT = '\033[91m'
    P1_NORMAL = '\033[94m'; P2_NORMAL = '\033[31m'
    GREEN = '\033[92m'; YELLOW = '\033[93m'; RED = '\033[91m'; PURPLE = '\033[95m'

# ========================= Offsets / Layout =========================
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

# Offsets for probing attack id/sub (from your logs)
OFF_ATK_ID   = 0x1E8
OFF_ATK_SUB  = 0x1EC

MEM1_LO, MEM1_HI = 0x80000000, 0x81800000
MEM2_LO, MEM2_HI = 0x90000000, 0x94000000
BAD_PTRS = {0x00000000, 0x80520000}
INDIR_PROBES = [0x10,0x18,0x1C,0x20]
LAST_GOOD_TTL = 1.0

# Optional char name map for display
CHAR_NAMES = {
    1:"Ken the Eagle",12:"Ryu",13:"Chun-Li",16:"Alex",15:"Morrigan",21:"Soki",
}

# ========================= Dolphin I/O helpers =========================
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
    end=time.time()+1.5
    while time.time()<end:
        x=rdf32(base+POSX_OFF); xs.append(x if x is not None else 0.0)
        for off in POSY_CANDS:
            y=rdf32(base+off); ys[off].append(y if y is not None else 0.0)
        time.sleep(1.0/120)
    if len(xs)<10: return 0xF4
    best, best_off=None, None
    for off, series in ys.items():
        if len(series)<10: continue
        v = sum((x - (sum(series)/len(series)))**2 for x in series) / (len(series)-1)
        score = 1.0/(1.0 + v)
        if best is None or score > best:
            best, best_off = score, off
    return best_off or 0xF4

class SlotResolver:
    def __init__(self):
        self.last_good = {}

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

# ========================= Mapping loader + writer =========================
def _parse_int_safe(v):
    if v is None or v == "": return None
    try: return int(v)
    except:
        try: return int(v, 0)
        except:
            try: return int(float(v))
            except: return None

def load_mapping(csv_path):
    mapping = {}
    if not os.path.exists(csv_path):
        return mapping
    with open(csv_path, newline='', encoding='utf-8') as fh:
        rdr = csv.DictReader(fh)
        for r in rdr:
            aid = _parse_int_safe(r.get('atk_id_dec') or r.get('atk_id_hex'))
            cid = _parse_int_safe(r.get('char_id')) if r.get('char_id') else None
            mapping[(aid, cid)] = r
    return mapping

def append_mapping_row(csv_path, aid, cid, label, confirmed="yes"):
    header = ['atk_id_dec','atk_id_hex','generic_label','top_label','examples','confirmed','char_id']
    row = {
        'atk_id_dec': str(aid) if aid is not None else "",
        'atk_id_hex': (hex(aid) if (aid is not None and aid >= 0) else "NONE"),
        'generic_label': label,
        'top_label': label,
        'examples': label,
        'confirmed': confirmed,
        'char_id': str(cid) if cid is not None else ""
    }
    exists = os.path.exists(csv_path)
    with open(csv_path, 'a', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        if not exists:
            w.writeheader()
        w.writerow(row)

# in-memory mapping (populated at start)
MAPPING = {}

# keep set of prompted keys during runtime to avoid repeated prompts
PROMPTED_KEYS = set()

# interactive prompt (blocking)
def interactive_label(atk_key, char_id, suggested=None, context=None):
    """
    atk_key: integer attack id, or -1 for unknown/missing attack id
    char_id: attacker char id (may be None)
    """
    key = (atk_key, char_id)
    if key in PROMPTED_KEYS:
        return None
    PROMPTED_KEYS.add(key)

    print("\n*** Unmapped attack detected ***")
    if atk_key == -1:
        print(f" atk_id = <UNKNOWN>   char_id = {char_id}")
    else:
        print(f" atk_id = {atk_key} ({hex(atk_key)})  char_id = {char_id}")
    if suggested:
        print(f" suggestion (generic): {suggested}")
    if context:
        print(" context:", context)
    ans = input("Type label to save, ENTER to skip, or 'ignore' to mark as flag: ").strip()
    if ans == "":
        with open(UNMATCHED_LOG, "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow([f"{time.time():.6f}", atk_key, char_id, suggested or "", context or ""])
        print("Skipped — logged to", UNMATCHED_LOG)
        return None
    if ans.lower() == "ignore":
        label = f"FLAG_{('NONE' if atk_key==-1 else atk_key)}"
        append_mapping_row(MAPPING_CSV, atk_key, char_id, label, confirmed="yes")
        MAPPING[(atk_key, char_id)] = {'generic_label': label}
        print("Marked as ignored flag and saved.")
        return label
    append_mapping_row(MAPPING_CSV, atk_key, char_id, ans, confirmed="yes")
    MAPPING[(atk_key, char_id)] = {'generic_label': ans}
    print("Saved label:", ans)
    return ans

# ========================= Meter / fighter reads =========================
class MeterAddrCache:
    def __init__(self): self.addr_by_base={}
    def drop(self, base): self.addr_by_base.pop(base, None)
    def get(self, base):
        if base in self.addr_by_base: return self.addr_by_base[base]
        for a in (base+METER_OFF_PRIMARY, base+METER_OFF_SECONDARY):
            v = rd32(a)
            if v is not None and 0 <= v <= 200_000:
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
    if not base: return None
    max_hp=rd32(base+OFF_MAX_HP); cur_hp=rd32(base+OFF_CUR_HP); aux_hp=rd32(base+OFF_AUX_HP)
    if not looks_like_hp(max_hp,cur_hp,aux_hp): return None
    cid=rd32(base+OFF_CHAR_ID)
    name=CHAR_NAMES.get(cid, f"ID_{cid}") if cid is not None else "???"
    x=rdf32(base+POSX_OFF)
    y=rdf32(base+posy_off) if posy_off is not None else None
    last=rd32(base+OFF_LAST_HIT)
    if last is None or last<0 or last>200_000: last=None
    return {"base":base,"max":max_hp,"cur":cur_hp,"aux":aux_hp,"id":cid,"name":name,"x":x,"y":y,"last":last}

def dist2(a,b):
    if a is None or b is None: return float("inf")
    ax,ay=a.get("x"),a.get("y"); bx,by=b.get("x"),b.get("y")
    if None in (ax,ay,bx,by): return float("inf")
    dx=ax-bx; dy=ay-by
    return dx*dx+dy*dy

# ========================= Main loop =========================
def main():
    global MAPPING
    print("HUD + Collision logger: waiting for Dolphin…")
    hook()
    print("Hooked.")

    # load mapping into memory
    MAPPING = load_mapping(MAPPING_CSV)
    print(f"Loaded mapping entries: {len(MAPPING)} (from {MAPPING_CSV})")

    # open CSVs
    hit_new = not os.path.exists(HIT_CSV)
    fh = open(HIT_CSV, "a", newline="", encoding='utf-8'); hitw = csv.writer(fh)
    if hit_new:
        hitw.writerow(["t","victim_label","victim_char","dmg","hp_before","hp_after",
                       "team_guess","attacker_label","attacker_char","dist2","victim_ptr_hex","atk_id","atk_sub","atk_name"])

    combo_new = not os.path.exists(COMBO_CSV)
    fc = open(COMBO_CSV, "a", newline="", encoding='utf-8'); combow = csv.writer(fc)
    if combo_new:
        combow.writerow(["t_start","t_end","dur","victim_label","victim_char",
                         "attacker_label","attacker_char","team_guess",
                         "hits","total","hp_start","hp_end"])

    last_base_by_slot={}
    y_off_by_base={}
    meter_prev = {"P1":0,"P2":0}
    meter_now  = {"P1":0,"P2":0}
    p1_c1_base=None; p2_c1_base=None

    prev_hp  = {}
    prev_last= {}
    combos   = {}

    try:
        while True:
            resolved=[]
            for name, slot in SLOTS:
                base, changed = RESOLVER.resolve_base(slot)
                resolved.append((name, slot, base, changed))

            for name, slot, base, changed in resolved:
                if base and last_base_by_slot.get(slot) != base:
                    last_base_by_slot[slot]=base
                    METER_CACHE.drop(base)
                    y_off_by_base[base]=pick_posy_off_no_jump(base)

            for name, slot, base, _ in resolved:
                if name=="P1-C1" and base: p1_c1_base=base
                if name=="P2-C1" and base: p2_c1_base=base

            meter_prev["P1"]=meter_now["P1"]; meter_prev["P2"]=meter_now["P2"]
            m1=read_meter(p1_c1_base); m2=read_meter(p2_c1_base)
            if m1 is not None: meter_now["P1"]=m1
            if m2 is not None: meter_now["P2"]=m2

            info={}
            for name, slot, base, _ in resolved:
                if base:
                    y=y_off_by_base.get(base, 0xF4)
                    info[name]=read_fighter(base, y)
                else:
                    info[name]=None

            t=time.time()

            # expire combos
            for vb, st in list(combos.items()):
                if st.expired(t):
                    summary=st.end()
                    combow.writerow([f"{summary['t0']:.6f}", f"{summary['t1']:.6f}", f"{summary['dur']:.6f}",
                                     summary['victim_label'], summary['victim_name'],
                                     summary['attacker_label'] or "", summary['attacker_name'] or "",
                                     summary['team_guess'] or "", summary['hits'], summary['total'],
                                     summary['hp_start'], summary['hp_end']])
                    fc.flush()
                    del combos[vb]

            # detect hits per victim
            for name, slot, base, _ in resolved:
                fi = info.get(name)
                if not fi or not base: continue

                cur=fi["cur"]; last=fi["last"]
                hp_prev  = prev_hp.get(base)
                last_prev= prev_last.get(base)

                took=False; dmg=None

                # primary: last-chunk
                if last is not None and last_prev is not None and last>0 and last!=last_prev:
                    took=True; dmg=last

                # fallback: hp drop
                if (not took) and hp_prev is not None and cur is not None and cur < hp_prev:
                    took=True; dmg = hp_prev - cur

                if took and dmg and dmg >= MIN_HIT_DAMAGE:
                    # meter-based team guess
                    dP1=(meter_now["P1"] or 0) - (meter_prev["P1"] or 0)
                    dP2=(meter_now["P2"] or 0) - (meter_prev["P2"] or 0)
                    team_guess = None
                    if abs(dP1 - dP2) >= METER_DELTA_MIN:
                        team_guess = "P1" if dP1>dP2 else "P2"

                    # nearest opponent
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

                    # Try to read attack id & attacker char (if we have a best candidate)
                    atk_id = None; atk_sub = None; atk_name = None; attacker_char_id = None
                    if best:
                        try:
                            attacker_base = best.get("base")
                            if attacker_base:
                                a = rd32(attacker_base + OFF_ATK_ID)
                                b = rd32(attacker_base + OFF_ATK_SUB)
                                atk_id = int(a) if a is not None else None
                                atk_sub = int(b) if b is not None else None
                                attacker_char_id = rd32(attacker_base + OFF_CHAR_ID)
                        except Exception:
                            pass

                    # KEY DECISION: if we couldn't read atk_id use -1 as sentinel for "unknown atk"
                    atk_key = atk_id if atk_id is not None else -1

                    # Lookup: exact pair MUST exist to suppress prompt.
                    mapped = MAPPING.get((atk_key, attacker_char_id))
                    if mapped:
                        atk_name = mapped.get('generic_label')
                    else:
                        # show generic suggestion if present (atk_key >=0 generic)
                        suggested = None
                        if atk_key != -1:
                            gm = MAPPING.get((atk_key, None))
                            if gm: suggested = gm.get('generic_label')
                        # prepare context and prompt
                        context = {'victim': fi['name'], 'attacker_name': (best and best.get('name')), 'dmg': int(dmg), 'hp_before': int(hp_before)}
                        label = interactive_label(atk_key, attacker_char_id, suggested=suggested, context=context)
                        if label:
                            atk_name = label
                        # check mapping again in case prompt saved it
                        mapped2 = MAPPING.get((atk_key, attacker_char_id))
                        if mapped2:
                            atk_name = mapped2.get('generic_label')

                    if SHOW_HITS:
                        print(f"[{t:10.3f}] HIT  victim={name:<5}({fi['name']:<16}) "
                              f"dmg={int(dmg):5d}  hp:{int(hp_before)}->{int(hp_after)}  "
                              f"team={team_guess or '--'}  attacker≈{(best_label or '--'):<5}"
                              f"({(best and best['name']) or '--'})  dist2={(d2 if d2!=float('inf') else -1):.3f} "
                              f"atk_id={(atk_id if atk_id is not None else '--')} atk_sub={(atk_sub if atk_sub is not None else '--')} atk_name={(atk_name or '--')}")

                    # CSV write (store atk_key value; -1 means unknown id)
                    hitw.writerow([f"{t:.6f}", name, fi["name"], int(dmg),
                                   int(hp_before), int(hp_after), team_guess or "",
                                   best_label or "", (best and best["name"]) or "",
                                   f"{d2 if d2!=float('inf') else ''}", f"0x{base:08X}",
                                   (atk_id if atk_id is not None else ""), (atk_sub if atk_sub is not None else ""), (atk_name or "")])
                    fh.flush()

                    # combo handling (kept minimal)
                    st = combos.get(base)
                    if st is None or not st.active:
                        st = ComboState()
                        st.begin(t, base, name, fi["name"], int(hp_before),
                                 best_label, (best and best["name"]), team_guess)
                        combos[base]=st
                    st.add_hit(t, int(dmg), int(hp_after))

                # persist previous values
                if cur is not None: prev_hp[base]=cur
                prev_last[base]=last

            # HUD (simple)
            if SHOW_HUD:
                lines=[]
                for nm,_ in SLOTS:
                    blk=info.get(nm)
                    if not blk:
                        lines.append(f"{Colors.DIM}{nm}[--------]{Colors.RESET}")
                    else:
                        pct = blk['cur']/blk['max'] if blk['max'] else 0
                        lines.append(f"{nm}[0x{blk['base']:08X}] {blk['name']} {blk['cur']}/{blk['max']} ({pct*100:4.1f}%)")
                print(" | ".join(lines))

            time.sleep(INTERVAL)

    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        try: fh.close()
        except Exception: pass
        try: fc.close()
        except Exception: pass

# minimal ComboState
class ComboState:
    def __init__(self):
        self.active=False; self.victim_base=None; self.victim_label=None; self.victim_name=None
        self.attacker_label=None; self.attacker_name=None; self.team_guess=None
        self.hits=0; self.total=0; self.hp_start=0; self.hp_end=0; self.start_t=0.0; self.last_t=0.0
    def begin(self, t, victim_base, victim_label, victim_name, hp_before, attacker_label, attacker_name, team_guess):
        self.active=True; self.victim_base=victim_base; self.victim_label=victim_label; self.victim_name=victim_name
        self.attacker_label=attacker_label; self.attacker_name=attacker_name; self.team_guess=team_guess
        self.hits=0; self.total=0; self.hp_start=hp_before; self.hp_end=hp_before; self.start_t=t; self.last_t=t
    def add_hit(self, t, dmg, hp_after): self.hits+=1; self.total+=dmg; self.hp_end=hp_after; self.last_t=t
    def expired(self, t): return self.active and (t-self.last_t) > COMBO_TIMEOUT
    def end(self): self.active=False; return {"t0":self.start_t,"t1":self.last_t,"dur":self.last_t-self.start_t,"victim_label":self.victim_label,"victim_name":self.victim_name,"attacker_label":self.attacker_label,"attacker_name":self.attacker_name,"team_guess":self.team_guess,"hits":self.hits,"total":self.total,"hp_start":self.hp_start,"hp_end":self.hp_end}

if __name__ == "__main__":
    main()
