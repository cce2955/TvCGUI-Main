import os, time, math, struct, csv
import pygame
import dolphin_memory_engine as dme

### ================= USER TUNABLES ================= ###
POLL_HZ         = 60
INTERVAL        = 1.0 / POLL_HZ
MAX_EVENTS      = 20      # bottom log lines
HP_MIN_MAX      = 10_000
HP_MAX_MAX      = 60_000
SAMPLE_SECS     = 1.5
SAMPLE_DT       = 1.0 / 120
COMBO_MIN_DMG   = 10
MAX_DIST2       = 100.0

CHARPAIR_CSV    = "move_id_map_charpair.csv"
GENERIC_CSV     = "move_id_map_charagnostic.csv"

FONT_SIZE_PANEL = 17   # fighter boxes
FONT_SIZE_FEED  = 14   # activity + feed

WIN_W, WIN_H    = 1280, 720

# slots/pointers
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

# offsets inside fighter struct
OFF_MAX_HP   = 0x24
OFF_CUR_HP   = 0x28
OFF_AUX_HP   = 0x2C
OFF_LAST_HIT = 0x40
OFF_CHAR_ID  = 0x14
POSX_OFF     = 0xF0
POSY_CANDS   = [0xF4, 0xEC, 0xE8, 0xF8, 0xFC]

# wires / state bytes
OFF_FLAG_062 = 0x062
OFF_FLAG_063 = 0x063
OFF_FLAG_064 = 0x064
OFF_CTRLWORD = 0x070      # 32-bit control/lock word
OFF_GATE_072 = 0x072      # primary move gate byte

# motion/momentum info
VEL_OFFS = [0xBA58, 0xBA5C, 0xBA60, 0xBA64, 0xBA68]
OFF_IMPACT_BBA0 = 0xBBA0  # float spike on hit/block

# meter
METER_OFF_PRIMARY   = 0x4C
METER_OFF_SECONDARY = 0x9380 + 0x4C

# attack id words
ATT_ID_OFF_PRIMARY = 0x1E8
ATT_ID_OFF_SECOND  = 0x1EC

# Dolphin RAM ranges
MEM1_LO, MEM1_HI = 0x80000000, 0x81800000
MEM2_LO, MEM2_HI = 0x90000000, 0x94000000
BAD_PTRS = {0x00000000, 0x80520000}
INDIR_PROBES = [0x10, 0x18, 0x1C, 0x20]
LAST_GOOD_TTL = 1.0

CHAR_NAMES = {
    1:"Ken the Eagle",2:"Casshan",3:"Tekkaman",4:"Polimar",5:"Yatterman-1",
    6:"Doronjo",7:"Ippatsuman",8:"Jun the Swan",10:"Karas",12:"Ryu",
    13:"Chun-Li",14:"Batsu",15:"Morrigan",16:"Alex",17:"Viewtiful Joe",
    18:"Volnutt",19:"Roll",20:"Saki",21:"Soki",26:"Tekkaman Blade",
    27:"Joe the Condor",28:"Yatterman-2",29:"Zero",30:"Frank West",
}

### ======== dolphin io helpers ======== ###
def hook_blocking():
    print("GUI HUD: waiting for Dolphin…")
    while not dme.is_hooked():
        try: dme.hook()
        except Exception: pass
        time.sleep(0.2)
    print("GUI HUD: hooked Dolphin.")

def addr_in_ram(a):
    if a is None: return False
    return (MEM1_LO <= a < MEM1_HI) or (MEM2_LO <= a < MEM2_HI)

def rd32(addr):
    try: return dme.read_word(addr)
    except Exception: return None

def rd8(addr):
    try: return dme.read_byte(addr)
    except Exception: return None

def rdf32(addr):
    try:
        w = dme.read_word(addr)
        if w is None: return None
        f = struct.unpack(">f", struct.pack(">I", w))[0]
        if not math.isfinite(f): return None
        if abs(f) > 1e8: return None
        return f
    except Exception:
        return None

def looks_like_hp(maxhp, curhp, auxhp):
    if maxhp is None or curhp is None: return False
    if not (HP_MIN_MAX <= maxhp <= HP_MAX_MAX): return False
    if not (0 <= curhp <= maxhp): return False
    if auxhp is not None and not (0 <= auxhp <= maxhp): return False
    return True

class SlotResolver:
    def __init__(self):
        self.last_good = {}  # slot_addr -> (base, ttl)

    def _probe_indirect(self, slot_val):
        for off in INDIR_PROBES:
            a = rd32(slot_val+off)
            if addr_in_ram(a) and a not in BAD_PTRS:
                mh = rd32(a+OFF_MAX_HP)
                ch = rd32(a+OFF_CUR_HP)
                ax = rd32(a+OFF_AUX_HP)
                if looks_like_hp(mh,ch,ax):
                    return a
        return None

    def resolve_base(self, slot_addr):
        now=time.time()
        raw = rd32(slot_addr)
        if (not addr_in_ram(raw)) or (raw in BAD_PTRS):
            lg=self.last_good.get(slot_addr)
            if lg and now<lg[1]:
                return lg[0], False
            return None, False

        mh = rd32(raw+OFF_MAX_HP)
        ch = rd32(raw+OFF_CUR_HP)
        ax = rd32(raw+OFF_AUX_HP)
        if looks_like_hp(mh,ch,ax):
            self.last_good[slot_addr]=(raw, now+LAST_GOOD_TTL)
            return raw, True

        cand=self._probe_indirect(raw)
        if cand:
            self.last_good[slot_addr]=(cand, now+LAST_GOOD_TTL)
            return cand, True

        lg=self.last_good.get(slot_addr)
        if lg and now<lg[1]:
            return lg[0], False
        return None, False

RESOLVER = SlotResolver()

class MeterAddrCache:
    def __init__(self):
        self.addr_by_base={}
    def drop(self, base):
        if base in self.addr_by_base:
            del self.addr_by_base[base]
    def get(self, base):
        if base in self.addr_by_base:
            return self.addr_by_base[base]
        for cand in (base+METER_OFF_PRIMARY, base+METER_OFF_SECONDARY):
            v=rd32(cand)
            if v in (50000,0xC350) or (v is not None and 0<=v<=200_000):
                self.addr_by_base[base]=cand
                return cand
        self.addr_by_base[base]=base+METER_OFF_PRIMARY
        return self.addr_by_base[base]

METER_CACHE = MeterAddrCache()

def read_meter(base):
    if not base: return None
    addr=METER_CACHE.get(base)
    v=rd32(addr)
    if v is None or v<0 or v>200_000: return None
    return v

### ======== position sampler ======== ###
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
    xs=[]
    ys={off:[] for off in POSY_CANDS}
    end=time.time()+SAMPLE_SECS
    while time.time()<end:
        x=rdf32(base+POSX_OFF); xs.append(x if x is not None else 0.0)
        for off in POSY_CANDS:
            y=rdf32(base+off); ys[off].append(y if y is not None else 0.0)
        time.sleep(SAMPLE_DT)

    if len(xs)<10:
        return 0xF4

    best_score=None
    best_off=None
    for off,series in ys.items():
        if len(series)<10: continue
        s=abs(_slope(series))
        v=_variance(series)
        r=abs(_corr(series,xs))
        score=(0.6*(1/(1+v)))+(0.3*(1/(1+s)))+(0.1*(1/(1+r)))
        if best_score is None or score>best_score:
            best_score=score
            best_off=off
    return best_off or 0xF4

### ======== reads ======== ###
def read_vel_cluster(base):
    vals=[]
    for off in VEL_OFFS:
        vals.append(rdf32(base+off))
    return vals

def safe_fmt_f(v):
    return f"{v:.2f}" if (v is not None) else "--"

def read_fighter_block(base, y_off):
    max_hp=rd32(base+OFF_MAX_HP)
    cur_hp=rd32(base+OFF_CUR_HP)
    aux_hp=rd32(base+OFF_AUX_HP)
    if not looks_like_hp(max_hp,cur_hp,aux_hp):
        return None

    cid=rd32(base+OFF_CHAR_ID)
    name=CHAR_NAMES.get(cid, f"ID_{cid}") if cid is not None else "???"

    x=rdf32(base+POSX_OFF)
    y=rdf32(base+y_off) if y_off is not None else None

    last_dmg=rd32(base+OFF_LAST_HIT)
    if last_dmg is None or last_dmg<0 or last_dmg>200000:
        last_dmg=None

    f062=rd8(base+OFF_FLAG_062)
    f063=rd8(base+OFF_FLAG_063)
    f064=rd8(base+OFF_FLAG_064)

    gate072 = rd8(base+OFF_GATE_072)
    ctrlword= rd32(base+OFF_CTRLWORD)
    impact  = rdf32(base+OFF_IMPACT_BBA0)

    vel_list= read_vel_cluster(base)

    aid_primary=rd32(base+ATT_ID_OFF_PRIMARY)
    aid_second =rd32(base+ATT_ID_OFF_SECOND)

    return {
        "base":base,
        "cid":cid,
        "name":name,
        "max_hp":max_hp,
        "cur_hp":cur_hp,
        "aux_hp":aux_hp,
        "x":x,
        "y":y,
        "last_dmg":last_dmg,
        "f062":f062,
        "f063":f063,
        "f064":f064,
        "gate072":gate072,
        "ctrlword":ctrlword,
        "impact":impact,
        "vels":vel_list,
        "atk_id":aid_primary,
        "atk_sub":aid_second
    }

def dist2(a,b):
    if not a or not b: return float("inf")
    ax,ay=a.get("x"),a.get("y")
    bx,by=b.get("x"),b.get("y")
    if None in (ax,ay,bx,by): return float("inf")
    dx=ax-bx; dy=ay-by
    return dx*dx+dy*dy

### ======== flag decoders & frame adv tracking ======== ###
def decode_flag_062(val, role_hint):
    if val is None: return "?"
    if val == 160: return "BASE"
    if val == 32:  return "MOVE/CROUCH"
    if val == 0:   return "ACTIVE"
    if val == 168 and role_hint=="victim": return "PREHIT_WARN"
    if val == 40:  return "IMPACT"
    if val == 8:
        if role_hint=="victim":
            return "BLOCKSTUN"
        return "STUN_TAIL"
    return f"UNK({val})"

def decode_flag_063(val, role_hint):
    if val is None: return "?"
    if val == 1:   return "NEUTRAL"
    if val == 0:   return "LOCKED/ACTIVE"
    if val == 6:   return "PREHIT_LOCK"
    if val == 4:   return "HIT_PUSH"
    if val == 16 and role_hint=="attacker": return "BLOCK_PUSH"
    if val == 17 and role_hint=="attacker": return "ATKR_READY"
    if val == 168 and role_hint=="victim":  return "DEF_READY"
    return f"UNK({val})"

GLOBAL_FRAME=0
def current_frame_index():
    return GLOBAL_FRAME

class InteractionTracker:
    def __init__(self):
        self.active={}  # (att_base,vic_base)->st dict

    def start_if_new(self, t_now, att_slot, att_blk, vic_slot, vic_blk, dmg):
        if (not att_blk) or (not vic_blk):
            return None
        k=(att_blk["base"], vic_blk["base"])
        if k not in self.active:
            self.active[k]={
                "t_impact":t_now,
                "att_slot":att_slot,
                "vic_slot":vic_slot,
                "att_base":att_blk["base"],
                "vic_base":vic_blk["base"],
                "att_char":att_blk["name"],
                "vic_char":vic_blk["name"],
                "att_char_id":att_blk["cid"],
                "atk_id":att_blk["atk_id"],
                "dmg":dmg,
                "kind":None,  # "HIT" or "BLOCK"
                "att_ready_frame":None,
                "vic_ready_frame":None,
                "frame0":current_frame_index(),
                "done":False,
                "adv_frames":None,
            }
        else:
            st=self.active[k]
            st["atk_id"]=att_blk["atk_id"]
            st["dmg"]=max(st["dmg"], dmg)
        return self.active[k]

    def classify_kind(self, att_blk, vic_blk, st):
        if st["kind"] is not None: return
        # victim 062 == 8 => BLOCKSTUN, so BLOCK
        if vic_blk and (vic_blk["f062"] == 8):
            st["kind"]="BLOCK"
        else:
            st["kind"]="HIT"

    def update_recovery(self, st, att_blk, vic_blk):
        fr = current_frame_index()

        # attacker free? 063==17 or 1
        if st["att_ready_frame"] is None and att_blk:
            att63 = att_blk["f063"]
            if att63 in (17,1):
                st["att_ready_frame"]=fr

        # victim free?
        # BLOCK: victim 063 in (168,1)
        # HIT:   victim 063 == 1
        if st["vic_ready_frame"] is None and vic_blk:
            vic63 = vic_blk["f063"]
            if st["kind"]=="BLOCK":
                if vic63 in (168,1):
                    st["vic_ready_frame"]=fr
            else:
                if vic63 in (1,):
                    st["vic_ready_frame"]=fr

        # if both known compute advantage and mark done
        if (st["att_ready_frame"] is not None
            and st["vic_ready_frame"] is not None
            and not st["done"]):
            a0 = st["att_ready_frame"]
            v0 = st["vic_ready_frame"]
            st["adv_frames"] = (v0 - a0)
            st["done"]=True

    def garbage_collect(self):
        kill=[]
        nowfr=current_frame_index()
        for k,st in self.active.items():
            if st["done"]:
                if nowfr - st["frame0"] > POLL_HZ*2:
                    kill.append(k)
            else:
                if nowfr - st["frame0"] > POLL_HZ*2:
                    kill.append(k)
        for k in kill:
            del self.active[k]

    def get_recent(self):
        arr=list(self.active.values())
        arr.sort(key=lambda s: s["t_impact"], reverse=True)
        return arr

INTERACTIONS=InteractionTracker()

### ======== move label map ======== ###
def _parse_int_safe(v):
    if v is None or v=="": return None
    try: return int(v)
    except:
        try: return int(v,0)
        except:
            try: return int(float(v))
            except:
                return None

def load_generic_map(path):
    out={}
    if not os.path.exists(path):
        print(f"(Map) {path} missing, continuing.")
        return out
    try:
        with open(path, newline='', encoding='utf-8') as fh:
            peek=fh.readline()
        with open(path, newline='', encoding='utf-8') as fh2:
            fh2.seek(0)
            dr=csv.DictReader(fh2)
            if dr.fieldnames and 'atk_id_dec' in dr.fieldnames:
                for row in dr:
                    aid=_parse_int_safe(row.get('atk_id_dec') or row.get('atk_id_hex'))
                    if aid is None: continue
                    label=(row.get('generic_label') or row.get('top_label') or
                           row.get('examples') or "").strip()
                    if not label:
                        for k,v in row.items():
                            if k not in ("atk_id_dec","atk_id_hex","char_id") and v:
                                vv=v.strip()
                                if vv:
                                    label=vv
                                    break
                    if label:
                        out[aid]=label
            else:
                fh2.seek(0)
                rr=csv.reader(fh2)
                for row in rr:
                    if (not row) or row[0].startswith("#"): continue
                    if len(row)<3: continue
                    aid=_parse_int_safe(row[0])
                    if aid is None: continue
                    lab=row[2].strip()
                    if lab:
                        out[aid]=lab
    except Exception as e:
        print("(Map) parse fail:", e)
    print(f"(Map) loaded {len(out)} char-agnostic labels")
    return out

def load_pair_map(path):
    out={}
    if not os.path.exists(path):
        print(f"(MapPairs) no {path}, continuing.")
        return out
    try:
        with open(path, newline='', encoding='utf-8') as fh:
            dr=csv.DictReader(fh)
            for row in dr:
                aid=_parse_int_safe(row.get("atk_id_dec") or row.get("atk_id_hex"))
                cid=_parse_int_safe(row.get("char_id"))
                if aid is None or cid is None: continue
                label=(row.get("generic_label") or row.get("top_label")
                       or row.get("examples") or "").strip()
                if not label: continue
                out[(aid,cid)] = label
        print(f"(MapPairs) loaded {len(out)} labels")
    except Exception as e:
        print("(MapPairs) parse fail:", e)
    return out

GENERIC_MAP={}
PAIR_MAP   ={}

def move_label_for(aid,cid):
    if aid is None: return "FLAG_NONE"
    if cid is not None and (aid,cid) in PAIR_MAP:
        return PAIR_MAP[(aid,cid)]
    if aid in GENERIC_MAP:
        return GENERIC_MAP[aid]
    return f"FLAG_{aid}"

### ======== drawing helpers ======== ###
def draw_fighter_panel(
    screen, font, rect,
    slot_name, blk, meter_val,
    recent_attacker_base,
    recent_victim_base
):
    x,y,w,h = rect
    pygame.draw.rect(screen, (30,30,30), rect, border_radius=8)
    pygame.draw.rect(screen, (80,80,80), rect, 2, border_radius=8)

    if not blk:
        lines = [
            f"{slot_name}",
            "(n/a)"
        ]
        yy=y+8
        for L in lines:
            surf=font.render(L,True,(200,200,200))
            screen.blit(surf,(x+8,yy))
            yy+=font.get_linesize()
        return

    role_hint = None
    if blk["base"] == recent_attacker_base:
        role_hint="attacker"
    elif blk["base"] == recent_victim_base:
        role_hint="victim"

    hp_cur = blk["cur_hp"]; hp_max=blk["max_hp"]
    hp_pct = (hp_cur/hp_max) if hp_max else 0.0
    hp_col = (0,255,0) if hp_pct>0.66 else ((255,255,0) if hp_pct>0.33 else (255,0,0))

    meter_str = str(meter_val) if meter_val is not None else "--"

    # trim vel list to 3 floats for readability
    v_short = blk["vels"][:3] if blk["vels"] else []
    v_show  = "[" + ",".join(safe_fmt_f(v) for v in v_short) + "]"

    f062raw = blk["f062"]; desc062=decode_flag_062(f062raw, role_hint)
    f063raw = blk["f063"]; desc063=decode_flag_063(f063raw, role_hint)
    f064raw = blk["f064"]; desc064=f"UNK({f064raw})" if f064raw is not None else "?"

    g72 = blk["gate072"]
    cw  = blk["ctrlword"]
    imp = blk["impact"]
    atk_id = blk["atk_id"]
    mv_label = move_label_for(atk_id, blk["cid"])

    # build lines in groups
    lineA = f"{slot_name} {blk['name']} @0x{blk['base']:08X}"
    lineB = f"HP {hp_cur}/{hp_max}  Meter:{meter_str}"
    lineC = f"Pos X:{safe_fmt_f(blk['x'])} Y:{safe_fmt_f(blk['y'])}  LastDmg:{blk['last_dmg'] if blk['last_dmg'] is not None else '--'}"
    lineD = f"MoveID:{atk_id} {mv_label}"
    lineE = f"Vel {v_show}"
    lineF = f"062:{f062raw} {desc062}  063:{f063raw} {desc063}  064:{f064raw} {desc064}"
    lineG = f"072:{g72 if g72 is not None else '--'}  ctrl:0x{cw:08X}" if cw is not None else f"072:{g72 if g72 is not None else '--'}  ctrl:--"
    lineH = f"impact:{safe_fmt_f(imp)}"

    yy=y+8
    for L in (lineA,lineB,lineC,lineD,lineE,lineF,lineG,lineH):
        # Render hp in color for the HP portion in lineB
        if L is lineB:
            # split "HP cur/max  Meter:val"
            # color only the "HP cur/max"
            hp_part=f"HP {hp_cur}/{hp_max}"
            rest   =f"  Meter:{meter_str}"
            surf_hp=font.render(hp_part,True,hp_col)
            screen.blit(surf_hp,(x+8,yy))
            sx = x+8+surf_hp.get_width()
            surf_rest=font.render(rest,True,(220,220,220))
            screen.blit(surf_rest,(sx,yy))
        else:
            surf=font.render(L,True,(220,220,220))
            screen.blit(surf,(x+8,yy))
        yy+=font.get_linesize()

def draw_activity_bar(screen, font, rect, interactions):
    x,y,w,h = rect
    pygame.draw.rect(screen,(20,20,20),rect)
    pygame.draw.rect(screen,(60,60,60),rect,2)

    yy=y+4
    # just show a few most recent
    for st in interactions[:6]:
        kind = st["kind"] or "??"
        atk  = st["att_char"]
        vic  = st["vic_char"]
        aid  = st["atk_id"]
        mvn  = move_label_for(aid, st["att_char_id"])
        dmg  = st["dmg"] if st["dmg"] is not None else "--"

        # advantage
        if st["adv_frames"] is None:
            adv_txt="adv:..."
        else:
            sign="+" if st["adv_frames"]>0 else ""
            tag = "OH" if kind=="HIT" else "OB"
            adv_txt=f"adv:{sign}{st['adv_frames']} {tag}"

        text = f"{atk} {mvn} -> {vic} | {kind} dmg={dmg} | {adv_txt}"
        surf = font.render(text,True,(240,240,240))
        screen.blit(surf,(x+8,yy))
        yy+=font.get_linesize()
        if yy>y+h-font.get_linesize():
            break

def draw_event_feed(screen, font, rect, interactions):
    x,y,w,h=rect
    pygame.draw.rect(screen,(10,10,10),rect)
    pygame.draw.rect(screen,(80,80,80),rect,2)

    yy=y+4
    for st in interactions[:MAX_EVENTS]:
        kind = st["kind"] or "??"
        atk  = st["att_char"]
        vic  = st["vic_char"]
        aid  = st["atk_id"]
        mvn  = move_label_for(aid, st["att_char_id"])
        dmg  = st["dmg"] if st["dmg"] is not None else "--"

        if st["adv_frames"] is None:
            adv_txt="adv:..."
        else:
            sign="+" if st["adv_frames"]>0 else ""
            tag = "OH" if kind=="HIT" else "OB"
            adv_txt=f"adv:{sign}{st['adv_frames']} {tag}"

        line = f"{atk} {mvn} -> {vic} [{kind}] dmg={dmg} {adv_txt}"

        surf=font.render(line,True,(200,200,200))
        screen.blit(surf,(x+8,yy))
        yy+=font.get_linesize()
        if yy>y+h-font.get_linesize():
            break

### ======== main loop ======== ###
def main():
    global GLOBAL_FRAME, GENERIC_MAP, PAIR_MAP

    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption("TvC Live HUD / Frame Probe")
    clock = pygame.time.Clock()
    font_panel = pygame.font.SysFont("Consolas", FONT_SIZE_PANEL)
    font_feed  = pygame.font.SysFont("Consolas", FONT_SIZE_FEED)

    hook_blocking()

    GENERIC_MAP = load_generic_map(GENERIC_CSV)
    PAIR_MAP    = load_pair_map(CHARPAIR_CSV)

    last_base_by_slot = {}
    y_off_by_base     = {}
    meter_val_cache   = {"P1":0,"P2":0}
    p1_c1_base=None
    p2_c1_base=None

    prev_hp   = {}
    prev_last = {}

    running=True
    while running:
        GLOBAL_FRAME += 1
        now=time.time()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running=False

        # resolve each slot
        resolved=[]
        for sl,ptr in SLOTS:
            base,changed = RESOLVER.resolve_base(ptr)
            resolved.append((sl,ptr,base,changed))

        # update bases -> y offset + meter cache
        for sl,ptr,base,changed in resolved:
            if base and last_base_by_slot.get(ptr)!=base:
                last_base_by_slot[ptr]=base
                METER_CACHE.drop(base)
                y_off_by_base[base]=pick_posy_off_no_jump(base)

        for sl,ptr,base,_ in resolved:
            if sl=="P1-C1" and base: p1_c1_base=base
            if sl=="P2-C1" and base: p2_c1_base=base

        m1=read_meter(p1_c1_base)
        m2=read_meter(p2_c1_base)
        if m1 is not None: meter_val_cache["P1"]=m1
        if m2 is not None: meter_val_cache["P2"]=m2

        # read fighter blocks
        slot_info={}
        for sl,ptr,base,_ in resolved:
            if base:
                yoff=y_off_by_base.get(base,0xF4)
                blk=read_fighter_block(base,yoff)
                slot_info[sl]=blk
            else:
                slot_info[sl]=None

        # detect hit/block events (hp drop or new last_dmg)
        for sl,ptr,base,_ in resolved:
            blk=slot_info.get(sl)
            if not blk or not base: continue

            cur_hp=blk["cur_hp"]
            lastd =blk["last_dmg"]

            old_hp=prev_hp.get(base,cur_hp)
            old_ld=prev_last.get(base,lastd)

            took=False
            dmg_amt=None

            # last_dmg flip method
            if (lastd is not None and old_ld is not None
                and lastd>0 and lastd!=old_ld):
                took=True
                dmg_amt=lastd

            # hp drop method
            if not took and (old_hp is not None and cur_hp is not None and cur_hp<old_hp):
                diff=old_hp-cur_hp
                if diff>=COMBO_MIN_DMG:
                    took=True
                    dmg_amt=diff

            if took and dmg_amt:
                # figure attacker by closest of opposite team
                opps = ["P2-C1","P2-C2"] if sl.startswith("P1") else ["P1-C1","P1-C2"]
                best_blk=None
                best_lbl=None
                best_d2=float("inf")
                for osl in opps:
                    oblk=slot_info.get(osl)
                    if not oblk: continue
                    dd=dist2(blk,oblk)
                    if dd<best_d2:
                        best_d2=dd
                        best_blk=oblk
                        best_lbl=osl
                if MAX_DIST2 is not None and best_d2>MAX_DIST2:
                    best_blk=None
                    best_lbl=None

                st=None
                if best_blk:
                    st=INTERACTIONS.start_if_new(
                        now,
                        best_lbl, best_blk,
                        sl, blk,
                        dmg_amt
                    )
                    if st:
                        INTERACTIONS.classify_kind(best_blk, blk, st)

            prev_hp[base]=cur_hp
            prev_last[base]=lastd

        # update frame advantage tracking
        for st in list(INTERACTIONS.active.values()):
            att_blk=None
            vic_blk=None
            for b in slot_info.values():
                if not b: continue
                if b["base"]==st["att_base"]: att_blk=b
                if b["base"]==st["vic_base"]: vic_blk=b
            if att_blk or vic_blk:
                INTERACTIONS.update_recovery(st, att_blk, vic_blk)

        INTERACTIONS.garbage_collect()

        # draw
        screen.fill((0,0,0))

        # top layout:
        panel_w = WIN_W//2 - 16
        panel_h = 180  # taller now
        p_rects = {
            "P1-C1": pygame.Rect(8,8,panel_w,panel_h),
            "P1-C2": pygame.Rect(8+panel_w+16,8,panel_w,panel_h),
            "P2-C1": pygame.Rect(8,8+panel_h+12,panel_w,panel_h),
            "P2-C2": pygame.Rect(8+panel_w+16,8+panel_h+12,panel_w,panel_h),
        }

        recent_list = INTERACTIONS.get_recent()
        recent_attacker_base = recent_list[0]["att_base"] if recent_list else None
        recent_victim_base   = recent_list[0]["vic_base"] if recent_list else None

        for sl,rect in p_rects.items():
            blk=slot_info.get(sl)
            meter_val = meter_val_cache["P1" if sl.startswith("P1") else "P2"]
            draw_fighter_panel(
                screen, font_panel, rect,
                sl, blk, meter_val,
                recent_attacker_base,
                recent_victim_base
            )

        mid_rect  = pygame.Rect(8, 8+panel_h*2+24, WIN_W-16, 80)
        feed_rect = pygame.Rect(8, mid_rect.bottom+12, WIN_W-16, WIN_H-(mid_rect.bottom+20))

        draw_activity_bar(screen, font_feed, mid_rect, recent_list)
        draw_event_feed(screen, font_feed, feed_rect, recent_list)

        pygame.display.flip()
        # tick at POLL_HZ
        pygame.time.delay(int(INTERVAL*1000))
        GLOBAL_FRAME += 0  # (we already ++ at top each loop)

    pygame.quit()

if __name__=="__main__":
    main()