# tvc_gui_hud.py (patched)
#
# Realtime GUI HUD for Tatsunoko vs Capcom labbing.
# - Poll Dolphin memory (dolphin-memory-engine)
# - Render per-slot panels w/ HP, meter, pos, state, wires, etc
# - Track last N hits w/ move names (from mapping CSVs)
# - Draw tiny per-frame activity timelines (gate_072, ctrl spikes, impact spikes)
#
# Requirements:
#   pip install dolphin-memory-engine pygame openpyxl
#
# Files expected in same folder:
#   move_id_map_charagnostic.csv (generic atk_id -> label)
#   move_id_map_charpair.csv     ((atk_id,char_id)->label) [optional, can be empty]
#
# NOTE:
#   This is meant to replace watcher.py's print HUD. We'll still write collisions.csv,
#   but the main experience is now the window, not stdout spam.

import time, math, struct, csv, os, sys
import pygame
import dolphin_memory_engine as dme

# ========================= Tunables =========================
POLL_HZ         = 60
INTERVAL        = 1.0 / POLL_HZ

COMBO_TIMEOUT   = 0.60
MIN_HIT_DAMAGE  = 10
MAX_DIST2       = 100.0
METER_DELTA_MIN = 5

HP_MIN_MAX      = 10_000
HP_MAX_MAX      = 60_000

# how many recent hits to display in side panel
HIT_BUFFER_MAX  = 10

# how many frames of history for activity strip
HISTORY_FRAMES  = 120

# CSV logging
HIT_CSV             = "collisions.csv"
CHAR_AGNOSTIC_CSV   = "move_id_map_charagnostic.csv"
PAIR_MAPPING_CSV    = "move_id_map_charpair.csv"

# ========================= Slots / layout =========================
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

# Fighter struct offsets
OFF_MAX_HP       = 0x24
OFF_CUR_HP       = 0x28
OFF_AUX_HP       = 0x2C
OFF_LAST_HIT     = 0x40
OFF_CHAR_ID      = 0x14

POSX_OFF         = 0xF0
POSY_CANDS       = [0xF4, 0xEC, 0xE8, 0xF8, 0xFC]

METER_OFF_PRIMARY   = 0x4C
METER_OFF_SECONDARY = 0x9380 + 0x4C

# wires / control flags / "hot zone"
OFF_POOLED_LIFE = 0x02E  # pooled life byte
OFF_CTRL_WORD   = 0x070  # 32-bit volatile ctrl/state flags
OFF_WIRE_052    = 0x052  # byte (startup-ish)
OFF_WIRE_058    = 0x058  # byte (active-ish)
OFF_WIRE_05B    = 0x05B  # byte  (movement/air/assist/crouch/etc from your mapping)
OFF_GATE_072    = 0x072  # byte  (master move gate: startup+active+recovery window)

# impact region float
OFF_IMPACT_BBA0 = 0xBBA0

# velocity cluster (anim-rate / velocity scalars etc)
VEL_FLOAT_OFFS = [0xBA58, 0xBA5C, 0xBA60, 0xBA64, 0xBA68]

# Attack ID offsets
ATT_ID_OFF_PRIMARY  = 0x1E8
ATT_ID_OFF_SECOND   = 0x1EC

# RAM ranges
MEM1_LO, MEM1_HI = 0x80000000, 0x81800000
MEM2_LO, MEM2_HI = 0x90000000, 0x94000000
BAD_PTRS = {0x00000000, 0x80520000}
INDIR_PROBES = [0x10, 0x18, 0x1C, 0x20]
LAST_GOOD_TTL = 1.0

# ========================= Character IDs -> Names =========================
CHAR_NAMES = {
    1:"Ken the Eagle",2:"Casshan",3:"Tekkaman",4:"Polimar",5:"Yatterman-1",
    6:"Doronjo",7:"Ippatsuman",8:"Jun the Swan",10:"Karas",12:"Ryu",
    13:"Chun-Li",14:"Batsu",15:"Morrigan",16:"Alex",17:"Viewtiful Joe",
    18:"Volnutt",19:"Roll",20:"Saki",21:"Soki",26:"Tekkaman Blade",
    27:"Joe the Condor",28:"Yatterman-2",29:"Zero",30:"Frank West",
}

# ========================= Dolphin mem helpers =========================
def hook_blocking():
    print("GUI HUD: waiting for Dolphin…")
    while not dme.is_hooked():
        try:
            dme.hook()
        except Exception:
            pass
        time.sleep(0.2)
    print("GUI HUD: hooked Dolphin.")

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
        if not math.isfinite(f): return None
        if abs(f) > 1e8: return None
        return f
    except Exception:
        return None

def rdu8(addr):
    try:
        return dme.read_byte(addr)
    except Exception:
        return None

def addr_in_ram(a):
    if a is None:
        return False
    return (MEM1_LO <= a < MEM1_HI) or (MEM2_LO <= a < MEM2_HI)

def looks_like_hp(maxhp, curhp, auxhp):
    if maxhp is None or curhp is None: return False
    if not (HP_MIN_MAX <= maxhp <= HP_MAX_MAX): return False
    if not (0 <= curhp <= maxhp): return False
    if auxhp is not None and not (0 <= auxhp <= maxhp): return False
    return True

# ========================= Slot resolver =========================
class SlotResolver:
    def __init__(self):
        self.last_good = {}  # slot_ptr -> (base_ptr, ttl)

    def _probe_indirect(self, slot_val):
        for off in INDIR_PROBES:
            a = rd32(slot_val+off)
            if addr_in_ram(a) and a not in BAD_PTRS:
                if looks_like_hp(rd32(a+OFF_MAX_HP), rd32(a+OFF_CUR_HP), rd32(a+OFF_AUX_HP)):
                    return a
        return None

    def resolve_base(self, slot_addr):
        now = time.time()
        slot_val = rd32(slot_addr)

        if not addr_in_ram(slot_val) or slot_val in BAD_PTRS:
            lg = self.last_good.get(slot_addr)
            if lg and now < lg[1]:
                return lg[0], False
            return None, False

        mh = rd32(slot_val+OFF_MAX_HP)
        ch = rd32(slot_val+OFF_CUR_HP)
        ax = rd32(slot_val+OFF_AUX_HP)

        if looks_like_hp(mh,ch,ax):
            self.last_good[slot_addr] = (slot_val, now+LAST_GOOD_TTL)
            return slot_val, True

        a = self._probe_indirect(slot_val)
        if a:
            self.last_good[slot_addr] = (a, now+LAST_GOOD_TTL)
            return a, True

        lg = self.last_good.get(slot_addr)
        if lg and now < lg[1]:
            return lg[0], False

        return None, False

RESOLVER = SlotResolver()

# ========================= y-offset chooser =========================
SAMPLE_SECS = 1.2
SAMPLE_DT   = 1.0/120
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
    if len(xs)<10: return 0xF4
    best_score=None; best_off=None
    for off,series in ys.items():
        if len(series)<10: continue
        s=abs(_slope(series)); v=_variance(series); r=abs(_corr(series,xs))
        score=(0.6*(1/(1+v)))+(0.3*(1/(1+s)))+(0.1*(1/(1+r)))
        if best_score is None or score>best_score:
            best_score=score; best_off=off
    return best_off or 0xF4

# ========================= meter cache =========================
class MeterAddrCache:
    def __init__(self):
        self.addr_by_base={}

    def drop(self,base):
        if base in self.addr_by_base:
            del self.addr_by_base[base]

    def get(self,base):
        if base in self.addr_by_base:
            return self.addr_by_base[base]
        for cand in (base+METER_OFF_PRIMARY, base+METER_OFF_SECONDARY):
            v = rd32(cand)
            if v in (50000,0xC350) or (v is not None and 0<=v<=200_000):
                self.addr_by_base[base]=cand
                return cand
        self.addr_by_base[base]=base+METER_OFF_PRIMARY
        return self.addr_by_base[base]

METER_CACHE = MeterAddrCache()

def read_meter(base):
    if not base: return None
    a=METER_CACHE.get(base)
    v=rd32(a)
    if v is None or v<0 or v>200_000: return None
    return v

# ========================= misc helpers =========================
def dist2(blk_a, blk_b):
    if blk_a is None or blk_b is None:
        return float("inf")
    ax,ay = blk_a.get("x"), blk_a.get("y")
    bx,by = blk_b.get("x"), blk_b.get("y")
    if None in (ax,ay,bx,by):
        return float("inf")
    dx=ax-bx
    dy=ay-by
    return dx*dx+dy*dy

def read_attack_ids(base):
    if not base:
        return (None,None)
    a=rd32(base+ATT_ID_OFF_PRIMARY)
    b=rd32(base+ATT_ID_OFF_SECOND)
    ai = int(a) if (a is not None and isinstance(a,int)) else None
    bi = int(b) if (b is not None and isinstance(b,int)) else None
    return ai,bi

def decode_state_05B(val):
    if val is None:
        return "?"
    v=int(val)
    if v == 0:
        return "AIRBORNE"  # includes neutral jump & any launched/knockdown float/etc
    if v == 1:
        return "IDLE"
    if v == 2:
        return "FWD_AIR"
    if v == 4:
        return "BACK_AIR"
    if v == 3:
        return "WALK_FWD"
    if v == 5:
        return "WALK_BACK"
    if v == 16:
        return "CROUCH"
    if v == 8:
        return "ASSIST_RDY"
    if v == 9:
        return "ASSIST_TAG"
    return f"UNK({v})"

# ========================= attack label maps =========================
def _parse_int_safe(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except:
        try:
            return int(v, 0)
        except:
            try:
                return int(float(v))
            except:
                return None

def load_charagnostic_csv(path):
    out={}
    if not os.path.exists(path):
        print(f"(Map) no {path}, continuing.")
        return out
    with open(path, newline='', encoding='utf-8') as fh:
        lines = fh.read().splitlines()
    header_like = "atk_id" in lines[0] or "atk_id_dec" in lines[0]
    if header_like:
        rdr = csv.DictReader(lines)
        for r in rdr:
            aid = _parse_int_safe(r.get("atk_id_dec") or r.get("atk_id") or r.get("atk_id_hex"))
            if aid is None: continue
            label = (r.get("generic_label") or r.get("top_label") or "").strip()
            if not label:
                label = (r.get("examples") or "").strip()
            if label:
                out[int(aid)]=label
    else:
        for line in lines:
            line=line.strip()
            if not line or line.startswith("#"):
                continue
            parts=[p.strip() for p in line.split(",")]
            if len(parts)<3:
                continue
            aid_dec=_parse_int_safe(parts[0])
            lab = parts[2].strip().strip('"')
            if aid_dec is not None and lab:
                out[int(aid_dec)]=lab
    print(f"(Map) loaded {len(out)} char-agnostic labels")
    return out

def load_pair_map(path):
    out={}
    if not os.path.exists(path):
        print(f"(MapPairs) no {path}, continuing.")
        return out
    with open(path, newline='', encoding='utf-8') as fh:
        rdr = csv.DictReader(fh)
        for r in rdr:
            aid=_parse_int_safe(r.get("atk_id_dec") or r.get("atk_id_hex"))
            cid=_parse_int_safe(r.get("char_id"))
            lab=(r.get("generic_label") or r.get("top_label") or "").strip()
            if aid is None or cid is None or not lab:
                continue
            out[(int(aid),int(cid))]=lab
    print(f"(MapPairs) loaded {len(out)} (atk_id,char_id) labels")
    return out

def lookup_move_name(atk_id, char_id, pair_map, generic_map):
    if atk_id is None:
        return ""
    if char_id is not None and (atk_id,char_id) in pair_map:
        return pair_map[(atk_id,char_id)]
    if atk_id in generic_map:
        return generic_map[atk_id]
    return f"FLAG_{atk_id}"

# ========================= fighter snapshot =========================
def read_fighter_snapshot(base, y_off):
    if not base:
        return None
    max_hp=rd32(base+OFF_MAX_HP)
    cur_hp=rd32(base+OFF_CUR_HP)
    aux_hp=rd32(base+OFF_AUX_HP)
    if not looks_like_hp(max_hp,cur_hp,aux_hp):
        return None

    char_id = rd32(base+OFF_CHAR_ID)
    name    = CHAR_NAMES.get(char_id, f"ID_{char_id}") if char_id is not None else "???"

    x = rdf32(base+POSX_OFF)
    y = rdf32(base+y_off) if y_off is not None else None

    pooled_life = rdu8(base+OFF_POOLED_LIFE)
    wire_052    = rdu8(base+OFF_WIRE_052)
    wire_058    = rdu8(base+OFF_WIRE_058)
    wire_05B    = rdu8(base+OFF_WIRE_05B)
    gate_072    = rdu8(base+OFF_GATE_072)

    ctrl_word   = rd32(base+OFF_CTRL_WORD)

    impact_val  = rdf32(base+OFF_IMPACT_BBA0)

    vel_vals=[]
    for off in VEL_FLOAT_OFFS:
        vel_vals.append(rdf32(base+off))

    last_hit_amt = rd32(base+OFF_LAST_HIT)
    if last_hit_amt is None or last_hit_amt<0 or last_hit_amt>200_000:
        last_hit_amt=None

    return {
        "base":base,
        "char_id":char_id,
        "name":name,
        "max_hp":max_hp,
        "cur_hp":cur_hp,
        "aux_hp":aux_hp,
        "x":x, "y":y,
        "pooled_life":pooled_life,
        "wire_052":wire_052,
        "wire_058":wire_058,
        "wire_05B":wire_05B,
        "wire_05B_label":decode_state_05B(wire_05B),
        "gate_072":gate_072,
        "ctrl_word":ctrl_word,
        "impact":impact_val,
        "vels":vel_vals,
        "last_hit_amt":last_hit_amt,
    }

# ========================= combo state =========================
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
        self.hits=0
        self.total=0
        self.hp_start=0
        self.hp_end=0
        self.start_t=0.0
        self.last_t=0.0
    def begin(self,t,victim_base,victim_label,victim_name,hp_before,
              attacker_label,attacker_name,attacker_move,team_guess):
        self.active=True
        self.victim_base=victim_base
        self.victim_label=victim_label
        self.victim_name=victim_name
        self.attacker_label=attacker_label
        self.attacker_name=attacker_name
        self.attacker_move=attacker_move
        self.team_guess=team_guess
        self.hits=0
        self.total=0
        self.hp_start=hp_before
        self.hp_end=hp_before
        self.start_t=t
        self.last_t=t
    def add_hit(self,t,dmg,hp_after):
        self.hits+=1
        self.total+=dmg
        self.hp_end=hp_after
        self.last_t=t
    def expired(self,t_now):
        return self.active and (t_now-self.last_t)>COMBO_TIMEOUT
    def end(self):
        self.active=False
        return {
            "t0":self.start_t,"t1":self.last_t,"dur":self.last_t-self.start_t,
            "victim_label":self.victim_label,"victim_name":self.victim_name,
            "attacker_label":self.attacker_label,"attacker_name":self.attacker_name,
            "attacker_move":self.attacker_move,"team_guess":self.team_guess,
            "hits":self.hits,"total":self.total,
            "hp_start":self.hp_start,"hp_end":self.hp_end,
        }

# ========================= GUI init =========================
pygame.init()
FONT_SMALL  = pygame.font.SysFont("consolas", 14)
FONT_MED    = pygame.font.SysFont("consolas", 16, bold=True)
FONT_BIG    = pygame.font.SysFont("consolas", 18, bold=True)

C_BG      = (10,10,10)
C_PANEL   = (30,30,40)
C_TEXT    = (220,220,220)
C_DIM     = (100,100,100)
C_BAR_HP  = (80,200,80)
C_BAR_LO  = (220,180,40)
C_BAR_CRIT= (200,60,60)
C_BLUE    = (80,120,255)
C_RED     = (255,60,60)
C_WHITE   = (235,235,235)
C_OUTLINE = (80,80,100)
C_YELLOW  = (240,220,120)

SCREEN_W = 1000
SCREEN_H = 700
screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
pygame.display.set_caption("TvC Live HUD")

def draw_text(surf, txt, font, color, x,y):
    if txt is None: txt = ""
    img = font.render(str(txt), True, color)
    surf.blit(img,(x,y))

def draw_bar(surf, x,y,w,h, ratio, col_good, col_mid, col_bad):
    if ratio is None:
        ratio = 0.0
    else:
        ratio = max(0.0,min(1.0,ratio))
    if   ratio>0.66: col=col_good
    elif ratio>0.33: col=col_mid
    else:            col=col_bad
    pygame.draw.rect(surf, (40,40,40),(x,y,w,h))
    pygame.draw.rect(surf, col,(x,y,int(w*ratio),h))
    pygame.draw.rect(surf, C_OUTLINE,(x,y,w,h),1)

def _fmt_vel(v):
    if v is None:
        return "--"
    try:
        return f"{v:.2f}"
    except Exception:
        return "--"

def draw_panel_for_slot(surf, rect, slot_name, snap, meter_val):
    x,y,w,h = rect
    pygame.draw.rect(surf, C_PANEL, rect, border_radius=6)
    pygame.draw.rect(surf, C_OUTLINE, rect, 1, border_radius=6)

    draw_text(surf, slot_name, FONT_BIG, C_YELLOW, x+8, y+6)

    if snap is None:
        draw_text(surf, "NO DATA", FONT_MED, C_DIM, x+8, y+28)
        return

    # row 1: char / hp
    nm = f"{snap['name']} (ID {snap['char_id']})"
    draw_text(surf, nm, FONT_MED, C_TEXT, x+8, y+28)

    hp_cur = snap['cur_hp']
    hp_max = snap['max_hp']
    hp_str = f"{hp_cur}/{hp_max}"
    draw_text(surf, "HP:", FONT_SMALL, C_DIM, x+8, y+50)
    draw_text(surf, hp_str, FONT_SMALL, C_TEXT, x+38, y+50)

    ratio = (hp_cur/hp_max) if (hp_cur and hp_max) else 0
    draw_bar(surf, x+8, y+66, w-16, 10, ratio, C_BAR_HP, C_BAR_LO, C_BAR_CRIT)

    # row 2: meter/pos
    meter_txt = "--" if meter_val is None else str(meter_val)
    draw_text(surf, f"Meter:{meter_txt}", FONT_SMALL, C_TEXT, x+8, y+86)
    if snap['x'] is not None and snap['y'] is not None:
        pos_txt = f"Pos X:{snap['x']:.2f} Y:{snap['y']:.2f}"
    else:
        pos_txt = "Pos X:-- Y:--"
    draw_text(surf, pos_txt, FONT_SMALL, C_TEXT, x+150, y+86)

    # row 3: state / pooled / ctrl
    st_txt  = f"State:{snap['wire_05B_label']}"
    pool_txt= f"Pooled:{snap['pooled_life']}"
    ctrl    = snap['ctrl_word']
    ctrl_txt= f"ctrl:{'0x%08X'%ctrl if ctrl is not None else '--'}"
    draw_text(surf, st_txt,   FONT_SMALL, C_TEXT, x+8,   y+106)
    draw_text(surf, pool_txt, FONT_SMALL, C_TEXT, x+150, y+106)
    draw_text(surf, ctrl_txt, FONT_SMALL, C_TEXT, x+270, y+106)

    # row 4: wires/gate/impact
    gate = snap['gate_072']
    gate_txt = f"gate_072:{gate}"
    wire_052 = snap['wire_052']
    wire_058 = snap['wire_058']
    wtxt = f"w052:{wire_052} w058:{wire_058}"
    impact = snap['impact']
    if impact is not None:
        itxt   = f"impact:{impact:.2f}"
    else:
        itxt   = f"impact:--"
    draw_text(surf, gate_txt, FONT_SMALL, C_TEXT, x+8, y+126)
    draw_text(surf, wtxt,     FONT_SMALL, C_TEXT, x+150,y+126)
    draw_text(surf, itxt,     FONT_SMALL, C_TEXT, x+330,y+126)

    # row 5: vel floats (first 3)
    vels = snap['vels']
    v0 = _fmt_vel(vels[0] if len(vels)>0 else None)
    v1 = _fmt_vel(vels[1] if len(vels)>1 else None)
    v2 = _fmt_vel(vels[2] if len(vels)>2 else None)
    vtxt = f"vel0:{v0} vel1:{v1} vel2:{v2}"
    draw_text(surf, vtxt, FONT_SMALL, C_TEXT, x+8, y+146)

    # row 6: last hit amt
    lha = snap['last_hit_amt']
    last_dmg_txt = f"lastDmg:{lha}" if lha else "lastDmg:--"
    draw_text(surf, last_dmg_txt, FONT_SMALL, C_TEXT, x+8, y+166)

    # ptr
    base_txt=f"ptr:0x{snap['base']:08X}"
    draw_text(surf, base_txt, FONT_SMALL, C_DIM, x+8, y+186)

def draw_hits_list(surf, rect, hit_buf):
    x,y,w,h = rect
    pygame.draw.rect(surf, C_PANEL, rect, border_radius=6)
    pygame.draw.rect(surf, C_OUTLINE, rect, 1, border_radius=6)
    draw_text(surf, "RECENT HITS", FONT_BIG, C_YELLOW, x+8, y+6)

    line_y = y+32
    now_t = time.time()
    for hit in reversed(hit_buf[-HIT_BUFFER_MAX:]):
        dt = now_t - hit["t"]
        line = (
            f"{dt:4.1f}s "
            f"{hit['att_name']} {hit['move_name']}({hit['atk_id']}) "
            f"dmg={hit['dmg']} "
            f"air={hit['air_flag']} "
            f"vs {hit['vict_name']}"
        )
        draw_text(surf, line, FONT_SMALL, C_TEXT, x+8, line_y)
        line_y += 16
        if line_y > y+h-20:
            break

def draw_timeline_strip(surf, rect, hist):
    x,y,w,h = rect
    pygame.draw.rect(surf, C_PANEL, rect, border_radius=6)
    pygame.draw.rect(surf, C_OUTLINE, rect,1,border_radius=6)
    draw_text(surf, "ACTIVITY (P1-C1 / P2-C1)", FONT_MED, C_YELLOW, x+8, y+4)

    row_h = (h-24)//2

    def draw_row(row_hist, rx,ry,rw,rh):
        pygame.draw.rect(surf, (20,20,20), (rx,ry,rw,rh))
        N=len(row_hist)
        for pix in range(rw):
            idx = N - rw + pix
            if idx<0 or idx>=N:
                continue
            fr = row_hist[idx]
            gate = fr.get("gate") or 0
            ctrl = fr.get("ctrl") or 0
            imp  = fr.get("impact") or 0.0

            col = (30,30,40)
            if gate != 0:
                col = C_BLUE
            if (ctrl & 0x4000) or (ctrl & 0x20000000) or (ctrl & 0x20004000):
                col = C_RED
            if imp and imp>10.0:
                col = C_WHITE

            surf.fill(col, (rx+pix, ry, 1, rh))

        pygame.draw.rect(surf, C_OUTLINE, (rx,ry,rw,rh),1)

    draw_row(hist.get("P1-C1",[]), x+8, y+24, w-16, row_h-4)
    draw_row(hist.get("P2-C1",[]), x+8, y+24+row_h, w-16, row_h-4)

# ========================= main loop =========================
def main():
    hook_blocking()

    generic_map = load_charagnostic_csv(CHAR_AGNOSTIC_CSV)
    pair_map    = load_pair_map(PAIR_MAPPING_CSV)

    hit_new = not os.path.exists(HIT_CSV)
    hit_f = open(HIT_CSV,"a",newline="",encoding="utf-8")
    hit_w = csv.writer(hit_f)
    if hit_new:
        hit_w.writerow([
            "t",
            "victim_label","victim_char",
            "dmg","hp_before","hp_after",
            "team_guess",
            "attacker_label","attacker_char","attacker_char_id",
            "attacker_id_dec","attacker_id_hex","attacker_move_name",
            "is_air",
            "dist2",
            "victim_ptr_hex",
            "gate_072_att","wire_052_att","wire_058_att","ctrl_word_att_hex","impact_att",
            "gate_072_vic","wire_052_vic","wire_058_vic","ctrl_word_vic_hex","impact_vic",
        ])

    last_base_by_slot = {}
    y_off_by_base     = {}
    meter_prev = {"P1":0, "P2":0}
    meter_now  = {"P1":0, "P2":0}
    p1_c1_base = None
    p2_c1_base = None

    prev_hp   = {}
    prev_last = {}
    combos    = {}

    recent_hits = []
    history     = { "P1-C1":[], "P2-C1":[] }

    running=True
    clock=pygame.time.Clock()

    while running:
        # ====== events ======
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running=False

        # ====== resolve slots ======
        resolved=[]
        for label, slot_ptr in SLOTS:
            base, changed = RESOLVER.resolve_base(slot_ptr)
            resolved.append((label,slot_ptr,base,changed))

        # y offset update if base changed
        for label, slot_ptr, base, changed in resolved:
            if base and last_base_by_slot.get(slot_ptr)!=base:
                last_base_by_slot[slot_ptr]=base
                METER_CACHE.drop(base)
                y_off_by_base[base] = pick_posy_off_no_jump(base)

        # point chars for meter
        for label, _, base, _ in resolved:
            if label=="P1-C1" and base: p1_c1_base=base
            if label=="P2-C1" and base: p2_c1_base=base

        # read meters
        meter_prev["P1"]=meter_now["P1"]
        meter_prev["P2"]=meter_now["P2"]
        m1=read_meter(p1_c1_base)
        m2=read_meter(p2_c1_base)
        if m1 is not None: meter_now["P1"]=m1
        if m2 is not None: meter_now["P2"]=m2

        # snapshots
        slot_snaps={}
        for label, slot_ptr, base, _ in resolved:
            if base:
                y_off=y_off_by_base.get(base,0xF4)
                snap=read_fighter_snapshot(base,y_off)
                slot_snaps[label]=snap
            else:
                slot_snaps[label]=None

        now=time.time()

        # expire combos
        for victim_base, st in list(combos.items()):
            if st.expired(now):
                combos.pop(victim_base,None)

        # detect hits
        for label, slot_ptr, base, _ in resolved:
            snap = slot_snaps.get(label)
            if not snap or not base:
                continue

            cur_hp  = snap["cur_hp"]
            lastval = snap["last_hit_amt"]
            hp_prev = prev_hp.get(base)
            lv_prev = prev_last.get(base)

            took_hit=False
            dmg_amt=None

            # method1: watch +0x40 pulses
            if (lastval is not None and lv_prev is not None
                and lastval>0 and lastval!=lv_prev):
                took_hit=True
                dmg_amt=lastval

            # fallback: hp drop
            if (not took_hit) and hp_prev is not None and cur_hp is not None and cur_hp < hp_prev:
                took_hit=True
                dmg_amt=hp_prev-cur_hp

            if took_hit and dmg_amt and dmg_amt>=MIN_HIT_DAMAGE:
                # guess team from meter delta
                dP1=(meter_now["P1"] or 0)-(meter_prev["P1"] or 0)
                dP2=(meter_now["P2"] or 0)-(meter_prev["P2"] or 0)
                team_guess=None
                if abs(dP1-dP2)>=METER_DELTA_MIN:
                    team_guess="P1" if dP1>dP2 else "P2"

                # find attacker by proximity
                opps = ["P2-C1","P2-C2"] if label.startswith("P1") else ["P1-C1","P1-C2"]
                best_att=None
                best_att_label=None
                best_d2=float("inf")
                for olab in opps:
                    blk=slot_snaps.get(olab)
                    if not blk: continue
                    dd=dist2(snap,blk)
                    if dd<best_d2:
                        best_d2=dd
                        best_att=blk
                        best_att_label=olab
                if MAX_DIST2 is not None and best_d2>MAX_DIST2:
                    best_att=None
                    best_att_label=None
                    best_d2=-1.0

                # attack ID -> move name
                atk_id=None
                atk_sub=None
                move_name=""
                is_air=""
                if best_att:
                    atk_id, atk_sub = read_attack_ids(best_att["base"])
                    move_name = lookup_move_name(atk_id, best_att["char_id"], pair_map, generic_map)

                    stlab = best_att["wire_05B_label"] or ""
                    if "AIR" in stlab or "AIRBORNE" in stlab:
                        is_air="YES"
                    else:
                        is_air=""

                hp_before = hp_prev if hp_prev is not None else (snap["cur_hp"]+dmg_amt)
                hp_after  = snap["cur_hp"]

                vic_gate   = snap["gate_072"]
                vic_w052   = snap["wire_052"]
                vic_w058   = snap["wire_058"]
                vic_ctrl   = snap["ctrl_word"]
                vic_ctrl_h = ("0x%08X"%vic_ctrl) if vic_ctrl is not None else ""
                vic_imp    = snap["impact"]

                if best_att:
                    att_gate   = best_att["gate_072"]
                    att_w052   = best_att["wire_052"]
                    att_w058   = best_att["wire_058"]
                    att_ctrl   = best_att["ctrl_word"]
                    att_ctrl_h = ("0x%08X"%att_ctrl) if att_ctrl is not None else ""
                    att_imp    = best_att["impact"]
                else:
                    att_gate=att_w052=att_w058=att_ctrl_h=att_imp=""

                hit_w.writerow([
                    f"{now:.6f}",
                    label,
                    snap["name"],
                    int(dmg_amt),
                    int(hp_before),
                    int(hp_after),
                    team_guess or "",
                    best_att_label or "",
                    (best_att and best_att["name"]) or "",
                    (best_att and best_att["char_id"]) or "",
                    (atk_id if atk_id is not None else ""),
                    (hex(atk_id) if atk_id is not None else ""),
                    move_name,
                    is_air,
                    (f"{best_d2}" if best_d2!=float("inf") else ""),
                    f"0x{base:08X}",
                    att_gate,
                    att_w052,
                    att_w058,
                    att_ctrl_h,
                    att_imp,
                    vic_gate,
                    vic_w052,
                    vic_w058,
                    vic_ctrl_h,
                    vic_imp,
                ])
                hit_f.flush()

                # combo handling
                st = combos.get(base)
                if st is None or not st.active:
                    st=ComboState()
                    st.begin(
                        now,
                        base,
                        label,
                        snap["name"],
                        int(hp_before),
                        best_att_label,
                        (best_att and best_att["name"]),
                        move_name,
                        team_guess,
                    )
                    combos[base]=st
                st.add_hit(now,int(dmg_amt),int(hp_after))

                # GUI recent hits
                recent_hits.append({
                    "t": now,
                    "att_name": (best_att and best_att["name"]) or "--",
                    "vict_name": snap["name"],
                    "atk_id": atk_id if atk_id is not None else -1,
                    "move_name": move_name,
                    "dmg": int(dmg_amt),
                    "air_flag": is_air,
                })
                if len(recent_hits)>HIT_BUFFER_MAX*3:
                    recent_hits = recent_hits[-HIT_BUFFER_MAX*3:]

            # update prev snapshots
            if cur_hp is not None:
                prev_hp[base]=cur_hp
            prev_last[base]=lastval

        # timeline history update (P1-C1, P2-C1)
        for main_slot in ("P1-C1","P2-C1"):
            arr = history.setdefault(main_slot,[])
            snap = slot_snaps.get(main_slot)
            if snap:
                frame = {
                    "gate": snap["gate_072"] or 0,
                    "ctrl": snap["ctrl_word"] or 0,
                    "impact": snap["impact"] or 0.0,
                }
            else:
                frame = {"gate":0,"ctrl":0,"impact":0.0}
            arr.append(frame)
            if len(arr)>HISTORY_FRAMES:
                del arr[0:len(arr)-HISTORY_FRAMES]

        # ===== render =====
        screen.fill(C_BG)

        panel_w = 480
        panel_h = 210
        pad_x   = 10
        pad_y   = 10

        panels = {
            "P1-C1": (pad_x, pad_y, panel_w, panel_h),
            "P1-C2": (pad_x+panel_w+pad_x, pad_y, panel_w, panel_h),
            "P2-C1": (pad_x, pad_y+panel_h+pad_y, panel_w, panel_h),
            "P2-C2": (pad_x+panel_w+pad_x, pad_y+panel_h+pad_y, panel_w, panel_h),
        }

        team_meter = {
            "P1": meter_now["P1"],
            "P2": meter_now["P2"],
        }

        for sl, r in panels.items():
            snap = slot_snaps.get(sl)
            mval = team_meter["P1"] if sl.startswith("P1") else team_meter["P2"]
            draw_panel_for_slot(screen, r, sl, snap, mval)

        hits_rect = (
            pad_x,
            pad_y+panel_h*2+pad_y*2,
            panel_w*2+pad_x,
            180
        )
        draw_hits_list(screen, hits_rect, recent_hits)

        timeline_rect = (
            pad_x,
            hits_rect[1]+hits_rect[3]+pad_y,
            panel_w*2+pad_x,
            150
        )
        draw_timeline_strip(screen, timeline_rect, history)

        pygame.display.flip()
        clock.tick(POLL_HZ)

    hit_f.close()
    pygame.quit()
    sys.exit()

if __name__=="__main__":
    main()