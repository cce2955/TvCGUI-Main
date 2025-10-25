import time, math, struct, csv, os, pygame
import dolphin_memory_engine as dme

#################### CONFIG ####################
POLL_HZ         = 60
INTERVAL        = 1.0 / POLL_HZ
COMBO_TIMEOUT   = 0.60
MIN_HIT_DAMAGE  = 10
MAX_DIST2       = 100.0
METER_DELTA_MIN = 5

# bytes to watch (0x050..0x08F)
WIRE_OFFSETS = list(range(0x050, 0x090))

# WINDOW / LAYOUT (classic 2x2 top grid + bottom stack)
SCREEN_W = 1280
SCREEN_H = 800

PANEL_W  = SCREEN_W//2 - 20  # two panels per row
PANEL_H  = 150               # each fighter panel height (like your old screenshot)
ROW1_Y   = 10
ROW2_Y   = ROW1_Y + PANEL_H + 10

ACTIVITY_H = 40
LOG_H      = 160
INSP_H     = 220

# panel stack Y coords
STACK_TOP_Y = ROW2_Y + PANEL_H + 10   # activity bar starts here

FONT_MAIN_SIZE   = 16  # monospace main
FONT_SMALL_SIZE  = 14

# CSV paths
HIT_CSV             = "collisions.csv"
PAIR_MAPPING_CSV    = "move_id_map_charpair.csv"
GENERIC_MAPPING_CSV = "move_id_map_charagnostic.csv"

#################### COLORS ####################
def rgb(r,g,b): return (r,g,b)
COL_BG      = rgb(10,10,10)
COL_PANEL   = rgb(20,20,20)
COL_BORDER  = rgb(100,100,100)
COL_TEXT    = rgb(220,220,220)
COL_DIM     = rgb(140,140,140)
COL_GOOD    = rgb(100,220,100)
COL_WARN    = rgb(230,200,70)
COL_BAD     = rgb(230,80,80)
COL_ACCENT  = rgb(190,120,255)

#################### GAME POINTERS / OFFSETS ####################
PTR_P1_CHAR1 = 0x803C9FCC
PTR_P1_CHAR2 = 0x803C9FDC
PTR_P2_CHAR1 = 0x803C9FD4
PTR_P2_CHAR2 = 0x803C9FE4
SLOTS = [
    ("P1-C1", PTR_P1_CHAR1, "P1"),
    ("P1-C2", PTR_P1_CHAR2, "P1"),
    ("P2-C1", PTR_P2_CHAR1, "P2"),
    ("P2-C2", PTR_P2_CHAR2, "P2"),
]

OFF_MAX_HP   = 0x24
OFF_CUR_HP   = 0x28
OFF_AUX_HP   = 0x2C
OFF_LAST_HIT = 0x40
OFF_CHAR_ID  = 0x14

POSX_OFF     = 0xF0
POSY_CANDS   = [0xF4, 0xEC, 0xE8, 0xF8, 0xFC]

METER_OFF_PRIMARY   = 0x4C
METER_OFF_SECONDARY = 0x9380 + 0x4C

ATT_ID_OFF_PRIMARY  = 0x1E8
ATT_ID_OFF_SECOND   = 0x1EC

CTRL_WORD_OFF       = 0x70
FLAG_062            = 0x062
FLAG_063            = 0x063
FLAG_064            = 0x064
FLAG_072            = 0x072

MEM1_LO, MEM1_HI = 0x80000000, 0x81800000
MEM2_LO, MEM2_HI = 0x90000000, 0x94000000
BAD_PTRS = {0x00000000, 0x80520000}
INDIR_PROBES = [0x10,0x18,0x1C,0x20]
LAST_GOOD_TTL = 1.0

#################### CHARACTER NAMES ####################
CHAR_NAMES = {
    1:"Ken the Eagle",2:"Casshan",3:"Tekkaman",4:"Polimar",5:"Yatterman-1",
    6:"Doronjo",7:"Ippatsuman",8:"Jun the Swan",10:"Karas",12:"Ryu",
    13:"Chun-Li",14:"Batsu",15:"Morrigan",16:"Alex",17:"Viewtiful Joe",
    18:"Volnutt",19:"Roll",20:"Saki",21:"Soki",26:"Tekkaman Blade",
    27:"Joe the Condor",28:"Yatterman-2",29:"Zero",30:"Frank West",
}

#################### GLOBAL / RUNTIME STATE ####################
GENERIC_MAP = {}
PAIR_MAP    = {}
event_log   = []
MAX_LOG_LINES = 60

#################### DOLPHIN READ HELPERS ####################
def hook():
    while not dme.is_hooked():
        try:
            dme.hook()
        except:
            pass
        time.sleep(0.2)

def rd32(addr):
    try: return dme.read_word(addr)
    except: return None

def rd8(addr):
    try: return dme.read_byte(addr)
    except: return None

def rdf32(addr):
    try:
        w = dme.read_word(addr)
        if w is None:
            return None
        f = struct.unpack(">f", struct.pack(">I", w))[0]
        if not math.isfinite(f) or abs(f) > 1e8:
            return None
        return f
    except:
        return None

def addr_in_ram(a):
    if a is None: return False
    return (MEM1_LO <= a < MEM1_HI) or (MEM2_LO <= a < MEM2_HI)

def looks_like_hp(maxhp,curhp,auxhp):
    if maxhp is None or curhp is None: return False
    if not (10_000 <= maxhp <= 60_000): return False
    if not (0 <= curhp <= maxhp): return False
    if auxhp is not None and not (0 <= auxhp <= maxhp): return False
    return True

#################### RESOLVER / Y PICK ####################
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
    end=time.time()+1.0
    while time.time()<end:
        x=rdf32(base+POSX_OFF); xs.append(x if x is not None else 0.0)
        for off in POSY_CANDS:
            y=rdf32(base+off); ys[off].append(y if y is not None else 0.0)
        time.sleep(1.0/120.0)
    if len(xs)<10: return 0xF4
    best_score=None; best_off=None
    for off,series in ys.items():
        if len(series)<10: continue
        s=abs(_slope(series))
        v=_variance(series)
        r=abs(_corr(series,xs))
        score=(0.6*(1/(1+v)))+(0.3*(1/(1+s)))+(0.1*(1/(1+r)))
        if best_score is None or score>best_score:
            best_score=score; best_off=off
    return best_off or 0xF4

class SlotResolver:
    def __init__(self):
        self.last_good={}  # slot_addr -> (base, ttl)
    def _probe(self, slot_val):
        for off in INDIR_PROBES:
            a=rd32(slot_val+off)
            if addr_in_ram(a) and a not in BAD_PTRS:
                if looks_like_hp(rd32(a+OFF_MAX_HP), rd32(a+OFF_CUR_HP), rd32(a+OFF_AUX_HP)):
                    return a
        return None
    def resolve_base(self, slot_addr):
        now=time.time()
        s=rd32(slot_addr)
        if not addr_in_ram(s) or s in BAD_PTRS:
            lg=self.last_good.get(slot_addr)
            if lg and now<lg[1]:
                return lg[0], False
            return None, False
        mh=rd32(s+OFF_MAX_HP); ch=rd32(s+OFF_CUR_HP); ax=rd32(s+OFF_AUX_HP)
        if looks_like_hp(mh,ch,ax):
            self.last_good[slot_addr]=(s,now+LAST_GOOD_TTL); return s, True
        a=self._probe(s)
        if a:
            self.last_good[slot_addr]=(a,now+LAST_GOOD_TTL); return a, True
        lg=self.last_good.get(slot_addr)
        if lg and now<lg[1]:
            return lg[0], False
        return None, False

RESOLVER = SlotResolver()

#################### METER CACHE ####################
class MeterAddrCache:
    def __init__(self):
        self.addr_by_base={}
    def drop(self,base): self.addr_by_base.pop(base,None)
    def get(self,base):
        if base in self.addr_by_base: return self.addr_by_base[base]
        for a in (base+METER_OFF_PRIMARY, base+METER_OFF_SECONDARY):
            v=rd32(a)
            if v in (50000,0xC350) or (v is not None and 0<=v<=200_000):
                self.addr_by_base[base]=a; return a
        self.addr_by_base[base]=base+METER_OFF_PRIMARY
        return self.addr_by_base[base]

METER_CACHE = MeterAddrCache()

def read_meter(base):
    if not base: return None
    a=METER_CACHE.get(base)
    v=rd32(a)
    if v is None or v<0 or v>200_000:
        return None
    return v

#################### ATTACK / STATE DECODE ####################
def load_generic_map(path=GENERIC_MAPPING_CSV):
    mp={}
    if not os.path.exists(path):
        print("(Map) no",path)
        return mp
    try:
        with open(path, newline='', encoding='utf-8') as fh:
            rdr=csv.reader(fh)
            for row in rdr:
                if not row or row[0].startswith("#"): continue
                try:
                    aid=int(row[0])
                except:
                    continue
                if len(row)>=3 and row[2].strip():
                    mp[aid]=row[2].strip()
                else:
                    mp[aid]=f"FLAG_{aid}"
    except Exception as e:
        print("(Map) err:",e)
    print(f"(Map) loaded {len(mp)} char-agnostic labels")
    return mp

def load_pair_map(path=PAIR_MAPPING_CSV):
    mp={}
    if not os.path.exists(path):
        print("(MapPairs) no",path,", continuing.")
        return mp
    try:
        with open(path, newline='', encoding='utf-8') as fh:
            rdr=csv.DictReader(fh)
            for r in rdr:
                try:
                    aid=int(r.get('atk_id_dec') or r.get('atk_id_hex'),0)
                    cid=int(r.get('char_id'))
                except:
                    continue
                lab=(r.get('generic_label') or r.get('top_label') or r.get('examples') or "").strip()
                if not lab: lab=f"FLAG_{aid}"
                mp[(aid,cid)] = lab
    except Exception as e:
        print("(MapPairs) err:",e)
    print(f"(MapPairs) loaded {len(mp)} exact labels")
    return mp

def move_label_for(aid, cid):
    if aid == 48: return "BLOCK"
    if aid == 51: return "PUSHBLOCK"
    if aid is None: return "FLAG_NONE"
    if cid is not None and (aid,cid) in PAIR_MAP:
        return PAIR_MAP[(aid,cid)]
    if aid in GENERIC_MAP:
        return GENERIC_MAP[aid]
    return f"FLAG_{aid}"

def decode_flag_062(val):
    if val is None: return ("?", "UNK")
    if val == 160:  return ("160","IDLE_BASE")
    if val == 168:  return ("168","ENGAGED")
    if val == 32:   return ("32","ACTIVE_MOVE")
    if val == 0:    return ("0","ATTACK_ACTIVE")
    if val == 40:   return ("40","IMPACTED")
    if val == 8:    return ("8","STUN_LOCK")
    return (str(val), f"UNK({val})")

def decode_flag_063(val):
    """
    Decode per-character action / stun / cancel state byte at +0x063.

    Returns (raw_str, meaning_str) where raw_str is just the number as text
    and meaning_str is our best label for that state.
    """

    if val is None:
        return ("?", "UNK")

    # --- neutral / ready states ---
    if val == 1:
        # idle / totally free
        return ("1", "NEUTRAL")
    if val == 17:
        # attacker regained control after offense
        return ("17", "ATKR_READY")
    if val == 168:
        # defender regained control after being hit / blockstunned
        return ("168", "DEF_READY")

    # --- basic grounded attack flow ---
    if val == 0:
        # you called this STARTUP originally (locked in action)
        # we were also using "LOCKED_ACTIVE" here before. We'll keep STARTUP for now.
        return ("0", "STARTUP")
    if val == 32:
        # startup / early active animation
        return ("32", "STARTUP")
    if val == 6:
        # attacker: 2f before hit, committed
        return ("6", "HIT_COMMIT")
    if val == 34:
        # buffer next normal (A~B, B~C, etc.)
        return ("34", "CHAIN_BUFFER")
    if val == 36:
        # hit confirmed, pushback applying to attacker
        return ("36", "HIT_RESOLVE")
    if val in (37, 5):
        # recovery but not yet neutral
        return (str(val), "RECOVERY")

    # --- hit / block stun, victim side ---
    if val == 4:
        # you: "pushback + hitstun / blockstun shove"
        # (victim locked, getting pushed)
        return ("4", "HITSTUN_PUSH")
    if val == 16:
        # block push on attacker in block sequence
        return ("16", "BLOCK_PUSH")

    # --- aerial states / cancels ---
    if val == 65:
        # jump cancel / air cancel window after launcher
        return ("65", "AIR_CANCEL")
    if val == 64:
        return ("64", "AIR_ASCEND_ATK")   # air normal during rise
    if val == 192:
        return ("192", "AIR_DESC_ATK")    # air normal during fall / post-peak attack
    if val == 193:
        return ("193", "FALLING")         # generic falling state (no hit yet / landing soon)
    if val == 70:
        return ("70", "AIR_PREHIT")       # pre-impact check vs target (air vs grounded?)
    if val == 68:
        return ("68", "AIR_IMPACT")       # air hit actually connected (impact frame)
    if val == 197:
        return ("197", "KB_GROUNDED")     # grounded knockback / shove from air hit
    if val == 196:
        return ("196", "KB_VERTICAL")     # vertical knockback calc beginning
    if val == 198:
        return ("198", "KB_VERTICAL_PEAK")# vertical knockback really applied / airborne pop
    if val == 96:
        return ("96", "AIR_CHAIN_BUF1")   # first air chain buffer state
    if val == 224:
        return ("224", "AIR_CHAIN_BUF2")  # second air chain hop
    if val == 230:
        return ("230", "AIR_CHAIN_BUF3")  # third air chain hop
    if val == 194:
        return ("194", "AIR_CHAIN_END")   # tail before settling into descend atk (192)

    # --- fallback ---
    return (str(val), f"UNK({val})")

#################### SNAPSHOT ####################
def read_attack_ids(base):
    if not base: return (None,None)
    a = rd32(base+ATT_ID_OFF_PRIMARY)
    b = rd32(base+ATT_ID_OFF_SECOND)
    try: a=int(a) if a is not None else None
    except: a=None
    try: b=int(b) if b is not None else None
    except: b=None
    return a,b

def read_fighter(base, y_off):
    if not base: return None
    max_hp=rd32(base+OFF_MAX_HP)
    cur_hp=rd32(base+OFF_CUR_HP)
    aux_hp=rd32(base+OFF_AUX_HP)
    if not looks_like_hp(max_hp,curhp=cur_hp,auxhp=aux_hp):
        return None
    cid=rd32(base+OFF_CHAR_ID)
    name=CHAR_NAMES.get(cid,f"ID_{cid}") if cid is not None else "???"
    x=rdf32(base+POSX_OFF)
    y=rdf32(base+y_off) if y_off is not None else None
    last=rd32(base+OFF_LAST_HIT)
    if last is None or last<0 or last>200_000:
        last=None
    ctrl_word = rd32(base+CTRL_WORD_OFF)

    f062 = rd8(base+FLAG_062)
    f063 = rd8(base+FLAG_063)
    f064 = rd8(base+FLAG_064)
    f072 = rd8(base+FLAG_072)

    attA,attB = read_attack_ids(base)

    # wires for inspector
    wires=[]
    for off in WIRE_OFFSETS:
        b = rd8(base+off)
        wires.append((off,b))

    return {
        "base":base,
        "max":max_hp,
        "cur":cur_hp,
        "aux":aux_hp,
        "id":cid,
        "name":name,
        "x":x,
        "y":y,
        "last":last,
        "ctrl":ctrl_word,
        "f062":f062,
        "f063":f063,
        "f064":f064,
        "f072":f072,
        "attA":attA,
        "attB":attB,
        "wires":wires,
    }

def dist2(a,b):
    if a is None or b is None: return float("inf")
    ax,ay=a.get("x"),a.get("y")
    bx,by=b.get("x"),b.get("y")
    if None in (ax,ay,bx,by): return float("inf")
    dx=ax-bx; dy=ay-by
    return dx*dx+dy*dy

#################### FRAME ADVANTAGE ####################
class AdvantageTracker:
    """
    Track frame advantage using flag 0x62 (f062).

    Rules:
      - We consider an "interaction" between (atk_base, vic_base).
      - While interaction is active, we watch each side's f062.
      - f062 == 160 means "IDLE_BASE" (fully recovered / neutral).
      - The first frame each side reaches 160 gets recorded.
      - When both sides have an idle_frame, we finalize plus_frames:
            plus_frames = vic_idle_frame - atk_idle_frame
        Negative => attacker recovers first (attacker is plus).
        Positive => victim recovers first (attacker is minus).
    """

    def __init__(self):
        # map (atk_base, vic_base) -> state dict
        self.pairs = {}
        self.last_result = []

    def _get_pair_state(self, atk_base, vic_base):
        key = (atk_base, vic_base)
        st = self.pairs.get(key)
        if st is None:
            st = {
                "active": False,
                "contact_frame": None,
                "last_touch_frame": None,

                "atk_idle_frame": None,  # first frame attacker f062 == 160
                "vic_idle_frame": None,  # first frame victim   f062 == 160

                "done": False,
                "plus_frames": None,
            }
            self.pairs[key] = st
        return st

    def start_contact(self, atk_base, vic_base, frame_idx):
        """
        Call this when we KNOW contact happened (HP dropped etc.).
        This either starts or refreshes the window.
        """
        st = self._get_pair_state(atk_base, vic_base)

        # new or reset interaction
        if (not st["active"]) or st["done"]:
            st["active"]            = True
            st["contact_frame"]     = frame_idx
            st["last_touch_frame"]  = frame_idx

            st["atk_idle_frame"]    = None
            st["vic_idle_frame"]    = None

            st["done"]              = False
            st["plus_frames"]       = None
        else:
            # already in contact, just refresh
            st["last_touch_frame"]  = frame_idx
    def get_latest_adv(self, atk_base, vic_base):
        """
        Return the last finalized plus_frames for this pair, or None.
        plus_frames = vic_idle_frame - atk_idle_frame
          >0  means victim took longer to return to idle_base (attacker recovers first, attacker is +)
          <0  means attacker took longer (victim is +)
        """
        st = self.pairs.get((atk_base, vic_base))
        if not st:
            return None
        return st.get("plus_frames")
    def update_pair(self, atk_snap, vic_snap, d2_val, frame_idx):
        if atk_snap is None or vic_snap is None:
            return

        atk_b = atk_snap["base"]
        vic_b = vic_snap["base"]

        st = self._get_pair_state(atk_b, vic_b)

        # ----- 1. figure out if they're "interacting" this frame -----
        close_enough = False
        if d2_val is not None and d2_val != float("inf"):
            close_enough = (d2_val <= MAX_DIST2)

        atk_f62 = atk_snap["f062"]
        vic_f62 = vic_snap["f062"]

        # non-160 means somebody is active / in stun / in block etc
        atk_busy = (atk_f62 is not None and atk_f62 != 160)
        vic_busy = (vic_f62 is not None and vic_f62 != 160)

        interacting = close_enough or atk_busy or vic_busy

        if interacting:
            # if not already active, bootstrap this like a new "contact"
            if (not st["active"]) or st["done"]:
                st["active"]            = True
                st["contact_frame"]     = frame_idx
                st["last_touch_frame"]  = frame_idx

                st["atk_idle_frame"]    = None
                st["vic_idle_frame"]    = None

                st["done"]              = False
                st["plus_frames"]       = None
            else:
                st["last_touch_frame"]  = frame_idx

        # ----- 2. if active, log first idle frames -----
        if st["active"] and (not st["done"]):
            if st["atk_idle_frame"] is None and atk_f62 == 160:
                st["atk_idle_frame"] = frame_idx
            if st["vic_idle_frame"] is None and vic_f62 == 160:
                st["vic_idle_frame"] = frame_idx

            if (st["atk_idle_frame"] is not None) and (st["vic_idle_frame"] is not None):
                st["plus_frames"] = (st["vic_idle_frame"] - st["atk_idle_frame"])
                st["done"] = True

        # ----- 3. timeout if stale -----
        if st["active"] and st["last_touch_frame"] is not None:
            if (frame_idx - st["last_touch_frame"]) > 30:
                st["active"] = False
                # keep plus_frames as historical
ADV_TRACK = AdvantageTracker()

#################### EVENT LOGGING ####################
def log_hit_line(data):
    s = (f"HIT {data['victim_label']}({data['victim_char']}) "
         f"dmg={data['dmg']} hp:{data['hp_before']}->{data['hp_after']} "
         f"from {data['attacker_label']} "
         f"moveID={data['attacker_id_dec']} '{data['attacker_move']}' "
         f"d2={data['dist2']:.3f}")
    event_log.append(s)
    if len(event_log)>200:
        del event_log[0:len(event_log)-200]

#################### DRAW HELPERS ####################
def hp_color(pct):
    if pct is None: return COL_TEXT
    if pct>0.66: return COL_GOOD
    if pct>0.33: return COL_GOOD  # green-ish in your old HUD
    return COL_GOOD               # you always showed HP green before

def draw_panel_classic(surface, rect, snap, meter_val, font, smallfont, header_label):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    if not snap:
        # header
        surface.blit(
            font.render(f"{header_label} ---", True, COL_TEXT),
            (rect.x+6, rect.y+4)
        )
        return

    # header line: "P1-C1 Ryu @0x9246B9C0"
    hdr = f"{header_label} {snap['name']} @{snap['base']:08X}"
    surface.blit(font.render(hdr, True, COL_TEXT),(rect.x+6, rect.y+4))

    # HP line
    cur_hp=snap["cur"]; max_hp=snap["max"]
    meter_str = str(meter_val) if meter_val is not None else "--"
    pct = (cur_hp/max_hp) if (max_hp and max_hp>0) else None
    hp_line = f"HP {cur_hp}/{max_hp}    Meter:{meter_str}"
    surface.blit(
        font.render(hp_line, True, hp_color(pct)),
        (rect.x+6, rect.y+24)
    )

    # Pos / LastDmg
    lastdmg = snap["last"] if snap["last"] is not None else 0
    pos_line = f"Pos X:{snap['x']:.2f} Y:{(snap['y'] or 0.0):.2f}   LastDmg:{lastdmg}"
    surface.blit(
        font.render(pos_line, True, COL_TEXT),
        (rect.x+6, rect.y+44)
    )

    # MoveID / sub / attack label
    atk_id = snap["attA"]; sub_id=snap["attB"]
    labelA = move_label_for(atk_id, snap["id"])
    mv_line = f"MoveID:{atk_id} {labelA}   sub:{sub_id}"
    surface.blit(font.render(mv_line, True, COL_TEXT),(rect.x+6, rect.y+64))

    # Flags / ctrl
    f062_val,f062_desc = decode_flag_062(snap["f062"])
    f063_val,f063_desc = decode_flag_063(snap["f063"])
    f064_val = snap["f064"] if snap["f064"] is not None else 0
    f072_val = snap["f072"] if snap["f072"] is not None else 0
    ctrl_hex = f"0x{(snap['ctrl'] or 0):08X}"

    # first flags row like your old HUD:
    row1 = f"062:{f062_val} {f062_desc}   063:{f063_val} {f063_desc}   064:{f064_val} UNK({f064_val})"
    surface.blit(font.render(row1, True, COL_TEXT),(rect.x+6, rect.y+84))

    # second flags row:
    row2 = f"072:{f072_val}   ctrl:{ctrl_hex}"
    surface.blit(font.render(row2, True, COL_TEXT),(rect.x+6, rect.y+104))

    # impact line? (in old HUD you had an "impact" float placeholder)
    # we'll approximate impact as distance to nearest enemy (negated for P2 just for parity):
    surface.blit(font.render("impact:--", True, COL_TEXT),(rect.x+6, rect.y+124))

def draw_activity(surface, rect, font, adv_line):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)
    txt = "Activity / Frame Advantage"
    surface.blit(font.render(txt, True, COL_TEXT),(rect.x+6, rect.y+4))

    if adv_line:
        surface.blit(font.render(adv_line, True, COL_TEXT),(rect.x+6, rect.y+20))

def draw_event_log(surface, rect, font, smallfont):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    title = "Event Log (latest at bottom)"
    surface.blit(font.render(title, True, COL_TEXT),(rect.x+6, rect.y+4))

    # lines to display
    lines = event_log[-MAX_LOG_LINES:]
    y = rect.y+24
    max_w = rect.w-12
    for line in lines[-12:]:
        # simple wrap for long lines
        words=line.split(' ')
        curr=""
        for w in words:
            test = curr + (" " if curr else "") + w
            if font.size(test)[0] > max_w and curr:
                surface.blit(smallfont.render(curr, True, COL_TEXT),(rect.x+6,y))
                y += smallfont.get_height()
                curr=w
            else:
                curr=test
        if curr:
            surface.blit(smallfont.render(curr, True, COL_TEXT),(rect.x+6,y))
            y += smallfont.get_height()

def draw_inspector(surface, rect, font, smallfont, snaps):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    title = "Inspector (0x050-0x08F wires, per character)"
    surface.blit(font.render(title, True, COL_TEXT),(rect.x+6, rect.y+4))

    # 4 columns equally
    col_w = rect.w//4
    base_y = rect.y+24
    max_h  = rect.h-28

    order = ["P1-C1","P1-C2","P2-C1","P2-C2"]
    for i,slot in enumerate(order):
        subr_x = rect.x + i*col_w
        subr_y = base_y
        subr_w = col_w
        # header
        snap = snaps.get(slot)
        if not snap:
            header_txt = f"{slot} [---]"
            surface.blit(
                smallfont.render(header_txt, True, COL_DIM),
                (subr_x+4, subr_y)
            )
            continue

        cid = snap["id"]
        header_txt = f"{slot} {snap['name']} (ID:{cid})"
        surface.blit(
            smallfont.render(header_txt, True, COL_TEXT),
            (subr_x+4, subr_y)
        )
        line_y = subr_y + smallfont.get_height()+2

        # decoded rows
        f062_val,f062_desc = decode_flag_062(snap["f062"])
        f063_val,f063_desc = decode_flag_063(snap["f063"])
        ctrl_hex = f"0x{(snap['ctrl'] or 0):08X}"
        f064_val = snap["f064"] if snap["f064"] is not None else 0
        f072_val = snap["f072"] if snap["f072"] is not None else 0

        info_lines = [
            f"ctrl:{ctrl_hex}",
            f"062:{f062_val} {f062_desc}",
            f"063:{f063_val} {f063_desc}",
            f"064:{f064_val} 072:{f072_val}",
        ]

        for ln in info_lines:
            surface.blit(
                smallfont.render(ln, True, COL_ACCENT),
                (subr_x+4, line_y)
            )
            line_y += smallfont.get_height()+2

        # wires dump
        # "050:160 051:0 ..." wrapped inside this column width
        chunks=[]
        for off,b in snap["wires"]:
            if off < 0x050 or off >= 0x090:
                continue
            val = "--" if b is None else str(b)
            chunks.append(f"{off:03X}:{val}")
        blob = " ".join(chunks)

        # word-wrap blob
        words = blob.split(" ")
        curr=""
        for w in words:
            test = curr+(" " if curr else "")+w
            if smallfont.size(test)[0] > (subr_w-8) and curr:
                surface.blit(
                    smallfont.render(curr, True, COL_TEXT),
                    (subr_x+4, line_y)
                )
                line_y += smallfont.get_height()+2
                curr=w
            else:
                curr=test
        if curr:
            surface.blit(
                smallfont.render(curr, True, COL_TEXT),
                (subr_x+4, line_y)
            )
            line_y += smallfont.get_height()+2

#################### MAIN LOOP ####################
def main():
    global GENERIC_MAP, PAIR_MAP

    print("GUI HUD: waiting for Dolphin…")
    hook()
    print("GUI HUD: hooked Dolphin.")

    GENERIC_MAP = load_generic_map(GENERIC_MAPPING_CSV)
    PAIR_MAP    = load_pair_map(PAIR_MAPPING_CSV)

    pygame.init()
    try:
        font      = pygame.font.SysFont("consolas", FONT_MAIN_SIZE)
        smallfont = pygame.font.SysFont("consolas", FONT_SMALL_SIZE)
    except:
        font      = pygame.font.Font(None, FONT_MAIN_SIZE)
        smallfont = pygame.font.Font(None, FONT_SMALL_SIZE)

    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    pygame.display.set_caption("TvC Live HUD / Frame Probe")
    clock = pygame.time.Clock()

    last_base_by_slot={}
    y_off_by_base={}
    prev_hp = {}

    frame_idx = 0  # <-- NEW: global frame counter inside this run

    running=True
    while running:
        frame_t0 = time.time()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running=False

        # resolve bases
        resolved=[]
        for slotname, ptr, teamtag in SLOTS:
            base, changed = RESOLVER.resolve_base(ptr)
            if base and last_base_by_slot.get(ptr) != base:
                last_base_by_slot[ptr]=base
                METER_CACHE.drop(base)
                y_off_by_base[base]=pick_posy_off_no_jump(base)
            resolved.append((slotname, teamtag, base))

        # choose C1s for meter
        p1c1_base = next((b for n,t,b in resolved if n=="P1-C1" and b), None)
        p2c1_base = next((b for n,t,b in resolved if n=="P2-C1" and b), None)

        meter_p1 = read_meter(p1c1_base)
        meter_p2 = read_meter(p2c1_base)

        # read snaps
        snaps={}
        for slotname, teamtag, base in resolved:
            if base:
                yoff=y_off_by_base.get(base,0xF4)
                s = read_fighter(base,yoff)
                if s:
                    s["teamtag"]=teamtag
                    s["slotname"]=slotname
                    snaps[slotname]=s

        # detect hits by HP drop (damage)
        for slotname,v_snap in snaps.items():
            base=v_snap["base"]
            hp_now=v_snap["cur"]
            hp_prev=prev_hp.get(base, hp_now)
            prev_hp[base]=hp_now

            dmg = hp_prev - hp_now
            if dmg >= MIN_HIT_DAMAGE:
                # find nearest opposite-team attacker
                vic_team = v_snap["teamtag"]
                attackers=[s for s in snaps.values() if s["teamtag"]!=vic_team]
                if not attackers:
                    continue
                best_d2=None
                atk_snap=None
                for c in attackers:
                    d2v = dist2(v_snap,c)
                    if best_d2 is None or d2v<best_d2:
                        best_d2=d2v; atk_snap=c
                if atk_snap is None:
                    continue

                atk_id   = atk_snap["attA"]
                atk_hex  = f"0x{atk_id:X}" if atk_id is not None else "NONE"
                mv_label = move_label_for(atk_id, atk_snap["id"])

                # log line to HUD log
                hit_row = {
                    "t": time.time(),
                    "victim_label": v_snap["slotname"],
                    "victim_char": v_snap["name"],
                    "dmg": dmg,
                    "hp_before": hp_prev,
                    "hp_after": hp_now,
                    "attacker_label": atk_snap["slotname"],
                    "attacker_char": atk_snap["name"],
                    "attacker_id_dec": atk_id,
                    "attacker_id_hex": atk_hex,
                    "attacker_move": mv_label,
                    "dist2": best_d2 if best_d2 is not None else -1.0,
                }
                log_hit_line(hit_row)

                # START/REFRESH contact window for advantage calc (hit-confirm)
                ADV_TRACK.start_contact(atk_snap["base"], v_snap["base"], frame_idx)

                # write CSV (unchanged except for naming)
                newcsv = not os.path.exists(HIT_CSV)
                with open(HIT_CSV, "a", newline="", encoding="utf-8") as fh:
                    w=csv.writer(fh)
                    if newcsv:
                        w.writerow([
                            "t","victim_label","victim_char","dmg",
                            "hp_before","hp_after",
                            "attacker_label","attacker_char","attacker_char_id",
                            "attacker_id_dec","attacker_id_hex","attacker_move",
                            "dist2",
                            "atk_flag062","atk_flag063",
                            "vic_flag062","vic_flag063",
                            "atk_ctrl","vic_ctrl",
                        ])
                    w.writerow([
                        f"{hit_row['t']:.6f}",
                        hit_row["victim_label"], hit_row["victim_char"],
                        dmg, hp_prev, hp_now,
                        hit_row["attacker_label"], hit_row["attacker_char"],
                        atk_snap["id"],
                        atk_id, atk_hex, mv_label,
                        0.0 if best_d2 is None else f"{best_d2:.3f}",
                        atk_snap["f062"], atk_snap["f063"],
                        v_snap["f062"], v_snap["f063"],
                        f"0x{(atk_snap['ctrl'] or 0):08X}",
                        f"0x{(v_snap['ctrl'] or 0):08X}",
                    ])

        # per-frame advantage update for all cross-team pairs
        pairs = [
            ("P1-C1","P2-C1"), ("P1-C1","P2-C2"),
            ("P1-C2","P2-C1"), ("P1-C2","P2-C2"),
            ("P2-C1","P1-C1"), ("P2-C1","P1-C2"),
            ("P2-C2","P1-C1"), ("P2-C2","P1-C2"),
        ]
        for atk_slot, vic_slot in pairs:
            atk_snap = snaps.get(atk_slot)
            vic_snap = snaps.get(vic_slot)
            if atk_snap and vic_snap:
                d2_val = dist2(atk_snap, vic_snap)
                ADV_TRACK.update_pair(atk_snap, vic_snap, d2_val, frame_idx)

        # build advantage string for HUD
        adv_line = ""
        if "P1-C1" in snaps and "P2-C1" in snaps:
            adv_val = ADV_TRACK.get_latest_adv(snaps["P1-C1"]["base"],
                                               snaps["P2-C1"]["base"])
            if adv_val is not None:
                # adv_val = vic_idle - atk_idle
                # positive means attacker recovered FIRST (attacker has the turn)
                # we want to *show that exactly*, not negate it
                adv_line = f"P1 vs P2 frame adv ~ {adv_val:+.1f}f"

        # ----------- RENDER (same as you already have) -----------
        screen.fill(COL_BG)

        r_p1c1 = pygame.Rect(10, ROW1_Y, PANEL_W, PANEL_H)
        r_p2c1 = pygame.Rect(10+PANEL_W+20, ROW1_Y, PANEL_W, PANEL_H)
        draw_panel_classic(screen, r_p1c1, snaps.get("P1-C1"), meter_p1, font, smallfont, "P1-C1")
        draw_panel_classic(screen, r_p2c1, snaps.get("P2-C1"), meter_p2, font, smallfont, "P2-C1")

        r_p1c2 = pygame.Rect(10, ROW2_Y, PANEL_W, PANEL_H)
        r_p2c2 = pygame.Rect(10+PANEL_W+20, ROW2_Y, PANEL_W, PANEL_H)
        draw_panel_classic(screen, r_p1c2, snaps.get("P1-C2"), None, font, smallfont, "P1-C2")
        draw_panel_classic(screen, r_p2c2, snaps.get("P2-C2"), None, font, smallfont, "P2-C2")

        act_rect  = pygame.Rect(10, STACK_TOP_Y, SCREEN_W-20, ACTIVITY_H)
        draw_activity(screen, act_rect, font, adv_line)

        log_rect  = pygame.Rect(10, act_rect.bottom+10, SCREEN_W-20, LOG_H)
        draw_event_log(screen, log_rect, font, smallfont)

        insp_rect = pygame.Rect(10, log_rect.bottom+10, SCREEN_W-20, INSP_H)
        draw_inspector(screen, insp_rect, font, smallfont, snaps)

        pygame.display.flip()

        # pacing
        frame_elapsed = time.time() - frame_t0
        sleep_left = INTERVAL - frame_elapsed
        if sleep_left>0:
            time.sleep(sleep_left)

        clock.tick()
        frame_idx += 1   # <--- advance the frame counter every loop
    pygame.quit()

if __name__=="__main__":
    main()