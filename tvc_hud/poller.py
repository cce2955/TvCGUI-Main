# tvc_hud/poller.py
import time, math, struct, csv, os, threading, collections
import dolphin_memory_engine as dme
from .constants import (
    SLOTS, OFF_MAX_HP, OFF_CUR_HP, OFF_AUX_HP, OFF_LAST_HIT, OFF_CHAR_ID,
    POSX_OFF, POSY_OFFS, METER_OFF_PRIMARY, METER_OFF_SECONDARY,
    HP_MIN_MAX, HP_MAX_MAX, MEM1_LO, MEM1_HI, MEM2_LO, MEM2_HI, BAD_PTRS,
    POLL_DT, EVT_UPDATE, EVT_HIT, CHAR_NAMES
)
from .mapping import MappingDB

MIN_HIT_DAMAGE   = 10
COMBO_TIMEOUT_S  = 0.60
MAX_DIST2        = 100.0
METER_DELTA_MIN  = 5

# ---------- Dolphin helpers ----------
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

# ---------- choose a stable Y offset ----------
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
    xs=[]; ys={off:[] for off in POSY_OFFS}
    end=time.time()+1.2
    while time.time()<end:
        x=rdf32(base+POSX_OFF); xs.append(x if x is not None else 0.0)
        for off in POSY_OFFS:
            y=rdf32(base+off); ys[off].append(y if y is not None else 0.0)
        time.sleep(1.0/120)
    if len(xs)<10: return POSY_OFFS[0]
    best, best_off=None, None
    for off, series in ys.items():
        if len(series)<10: continue
        s=abs(_slope(series)); v=_variance(series); r=abs(_corr(series,xs))
        score=(0.6*(1/(1+v)))+(0.3*(1/(1+s)))+(0.1*(1/(1+r)))
        if best is None or score>best: best, best_off=score, off
    return best_off or POSY_OFFS[0]

# ---------- slot resolver ----------
INDIR_PROBES   = [0x10,0x18,0x1C,0x20]
LAST_GOOD_TTL  = 1.0

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

# ---------- meter cache ----------
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

# ---------- structures ----------
class SlotInfo:
    __slots__=("label","base","name","cur","max","meter","x","y","last_hit","char_id")
    def __init__(self, label):
        self.label=label; self.base=None; self.name="--"
        self.cur=None; self.max=None; self.meter=None
        self.x=None; self.y=None; self.last_hit=None
        self.char_id=None

class ComboState:
    def __init__(self):
        self.active=False; self.victim_base=None
        self.hits=0; self.total=0; self.hp_start=0; self.hp_end=0
        self.start_t=0.0; self.last_t=0.0
        self.attacker_name=None; self.attacker_label=None
        self.victim_name=None; self.victim_label=None
        self.team_guess=None

    def begin(self, t, vb, vlabel, vname, hp_from, att_label, att_name, team):
        self.active=True; self.victim_base=vb
        self.hits=0; self.total=0; self.hp_start=hp_from; self.hp_end=hp_from
        self.start_t=t; self.last_t=t
        self.attacker_label=att_label; self.attacker_name=att_name
        self.victim_label=vlabel; self.victim_name=vname
        self.team_guess=team

    def add(self, t, dmg, hp_to): self.hits+=1; self.total+=dmg; self.hp_end=hp_to; self.last_t=t
    def expired(self,t): return self.active and (t-self.last_t)>COMBO_TIMEOUT_S
    def finish(self):
        self.active=False
        return dict(t0=self.start_t,t1=self.last_t,dur=self.last_t-self.start_t,
                    victim_label=self.victim_label,victim_name=self.victim_name,
                    attacker_label=self.attacker_label,attacker_name=self.attacker_name,
                    team_guess=self.team_guess,hits=self.hits,total=self.total,
                    hp_start=self.hp_start,hp_end=self.hp_end)

# ---------- main poller ----------
class Poller(threading.Thread):
    def __init__(self, q):
        super().__init__(daemon=True)
        self.q = q
        self.stop_flag=False
        self.resolver = SlotResolver()
        self.y_off_by_base = {}
        self.prev_hp  = {}
        self.prev_last= {}
        self.combos   = {}
        self.mapping  = MappingDB()  # <-- reads your CSV
        # last 5 attacks per slot label
        self.last_attacks = {lbl: collections.deque(maxlen=5) for (lbl,_,_) in SLOTS}

    def stop(self): self.stop_flag=True

    def run(self):
        hook()
        last_base_by_slot={}
        p1_base=None; p2_base=None
        meter_prev = {"P1":0,"P2":0}
        meter_now  = {"P1":0,"P2":0}

        while not self.stop_flag:
            # resolve slots
            resolved=[]
            for label, slot, _team in SLOTS:
                base, changed = self.resolver.resolve_base(slot)
                resolved.append((label, slot, base, changed))
                if base and (last_base_by_slot.get(slot)!=base):
                    last_base_by_slot[slot]=base
                    METER_CACHE.drop(base)
                    self.y_off_by_base[base]=pick_posy_off_no_jump(base)

            for name, slot, base, _ in resolved:
                if name=="P1-C1" and base: p1_base=base
                if name=="P2-C1" and base: p2_base=base

            meter_prev["P1"]=meter_now["P1"]; meter_prev["P2"]=meter_now["P2"]
            m1=read_meter(p1_base); m2=read_meter(p2_base)
            if m1 is not None: meter_now["P1"]=m1
            if m2 is not None: meter_now["P2"]=m2

            # snapshot
            snap={}
            for label, slot, base, _ in resolved:
                if not base:
                    snap[label]=None; continue
                yoff = self.y_off_by_base.get(base)
                if yoff is None: yoff = pick_posy_off_no_jump(base); self.y_off_by_base[base]=yoff

                max_hp=rd32(base+OFF_MAX_HP); cur_hp=rd32(base+OFF_CUR_HP); aux_hp=rd32(base+OFF_AUX_HP)
                if not looks_like_hp(max_hp, cur_hp, aux_hp):
                    snap[label]=None; continue

                cid=rd32(base+OFF_CHAR_ID); name=CHAR_NAMES.get(cid, f"ID_{cid}") if cid is not None else "--"
                x=rdf32(base+POSX_OFF); y=rdf32(base+yoff)
                last=rd32(base+OFF_LAST_HIT); last = last if (last is not None and 0 <= last <= 200_000) else None
                meter = read_meter(base) if label in ("P1-C1","P2-C1") else None
                snap[label]=dict(
                    base=base, ptr=f"0x{base:08X}", name=name, cur=cur_hp, max=max_hp,
                    meter=meter, x=x, y=y, last_hit=last, id=cid,
                    last_attacks=list(self.last_attacks[label])  # copy for UI
                )

            self.q.put((EVT_UPDATE, snap))

            # detect hits
            t=time.time()
            for label, slot, base, _ in resolved:
                if not base: continue
                info = snap.get(label)
                if not info: continue

                cur = info["cur"]; last = info["last_hit"]
                hp_prev  = self.prev_hp.get(base)
                last_prev= self.prev_last.get(base)

                took=False; dmg=None

                if last is not None and last_prev is not None and last>0 and last!=last_prev:
                    took=True; dmg=last
                if (not took) and hp_prev is not None and cur is not None and cur < hp_prev:
                    took=True; dmg = hp_prev - cur

                if took and dmg and dmg >= MIN_HIT_DAMAGE:
                    # guess team by meter delta
                    dP1=(meter_now["P1"] or 0) - (meter_prev["P1"] or 0)
                    dP2=(meter_now["P2"] or 0) - (meter_prev["P2"] or 0)
                    team_guess=None
                    if abs(dP1-dP2) >= METER_DELTA_MIN:
                        team_guess = "P1" if dP1>dP2 else "P2"

                    # find nearest opponent as attacker
                    opp = ["P2-C1","P2-C2"] if label.startswith("P1") else ["P1-C1","P1-C2"]
                    cand=[snap.get(k) for k in opp if snap.get(k)]
                    best=None; best_label=None; d2=float("inf")
                    def _dist2(a,b):
                        if not a or not b: return float("inf")
                        ax,ay=a["x"],a["y"]; bx,by=b["x"],b["y"]
                        if None in (ax,ay,bx,by): return float("inf")
                        dx=ax-bx; dy=ay-by; return dx*dx+dy*dy
                    if cand:
                        best=min(cand, key=lambda o: _dist2(info,o)); d2=_dist2(info,best)
                        if d2>MAX_DIST2: best=None; d2=-1.0
                    if best:
                        for k,v in snap.items():
                            if v is best: best_label=k; break

                    hp_from = hp_prev if hp_prev is not None else (cur + dmg)
                    hp_to   = cur

                    atk_id=None; atk_sub=None; atk_name=None; attacker_cid=None
                    if best:
                        attacker_base = best["base"]
                        a = rd32(attacker_base + 0x1E8)  # OFF_ATK_ID
                        b = rd32(attacker_base + 0x1EC)  # OFF_ATK_SUB
                        atk_id = int(a) if a is not None else None
                        atk_sub= int(b) if b is not None else None
                        attacker_cid = best.get("id")
                        # mapping lookup
                        atk_name = self.mapping.lookup(atk_id, attacker_cid) or ("(attack)" if atk_id is not None else "--")
                        # push into per-slot history
                        if atk_name and atk_name != "--":
                            self.last_attacks[best_label].appendleft(atk_name)

                    # queue hit event (UI shows ptrs too)
                    self.q.put((EVT_HIT, dict(
                        ts=t,
                        victim=dict(label=label, name=info["name"], ptr=info["ptr"], base=info["base"]),
                        attacker=dict(label=(best_label or "--"), name=(best["name"] if best else "--"),
                                      ptr=(best["ptr"] if best else "--"),
                                      base=(best["base"] if best else 0)),
                        dmg=int(dmg), hp_from=int(hp_from), hp_to=int(hp_to),
                        dist2=d2, atk_id=atk_id, atk_sub=atk_sub, atk_name=atk_name,
                        team_guess=team_guess
                    )))

                    # handle combo bookkeeping keyed by victim base
                    st = self.combos.get(base)
                    if st is None or not st.active:
                        st = ComboState()
                        st.begin(t, base, label, info["name"], int(hp_from),
                                 best_label, (best and best["name"]), team_guess)
                        self.combos[base]=st
                    st.add(t, int(dmg), int(hp_to))

                if cur is not None: self.prev_hp[base]=cur
                self.prev_last[base]=last

            # end expired combos (send as log lines onto "Combos" tab)
            for vb, st in list(self.combos.items()):
                if st.expired(t):
                    summary = st.finish()
                    # ship as a synthetic EVT_HIT with a marker the UI recognizes
                    self.q.put(("combo_summary", summary))
                    del self.combos[vb]

            time.sleep(POLL_DT)
