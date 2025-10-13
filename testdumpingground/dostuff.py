# tvc_hud_gui.py
# GUI HUD for TvC using Tkinter (no extra deps beyond dolphin_memory_engine).
# - Displays P1/P2 C1/C2 panels with HP, meter, position, lastDmg
# - Logs recent hits with inferred attacker
# - Updates in place (no line-by-line spam)

import math, struct, time, threading, queue
import tkinter as tk
from tkinter import ttk
import dolphin_memory_engine as dme

# ================== Constants / Config ==================
# Player slots (US build observed)
PTR_P1_CHAR1 = 0x803C9FCC
PTR_P1_CHAR2 = 0x803C9FDC
PTR_P2_CHAR1 = 0x803C9FD4
PTR_P2_CHAR2 = 0x803C9FE4
SLOTS = [
    ("P1-C1", PTR_P1_CHAR1, 0),  # team 0
    ("P1-C2", PTR_P1_CHAR2, 0),
    ("P2-C1", PTR_P2_CHAR1, 1),  # team 1
    ("P2-C2", PTR_P2_CHAR2, 1),
]

# Fighter offsets
OFF_MAX_HP   = 0x24
OFF_CUR_HP   = 0x28
OFF_AUX_HP   = 0x2C
OFF_LAST_HIT = 0x40     # victim-side "last damage chunk"
OFF_CHAR_ID  = 0x14
POSX_OFF     = 0xF0
POSY_OFFS    = [0xF4, 0xEC, 0xE8, 0xF8, 0xFC]
METER_OFF_PRIMARY   = 0x4C
METER_OFF_SECONDARY = 0x9380 + 0x4C

# Validation
HP_MIN_MAX = 10_000
HP_MAX_MAX = 60_000
MEM1_LO, MEM1_HI = 0x80000000, 0x81800000
MEM2_LO, MEM2_HI = 0x90000000, 0x94000000
BAD_PTRS = {0x00000000, 0x80520000}

POLL_HZ = 30
POLL_DT = 1.0 / POLL_HZ

# Character names
CHAR_NAMES = {
    1: "Ken the Eagle", 2: "Casshan", 3: "Tekkaman", 4: "Polimar", 5: "Yatterman-1",
    6: "Doronjo", 7: "Ippatsuman", 8: "Jun the Swan", 10: "Karas", 12: "Ryu",
    13: "Chun-Li", 14: "Batsu", 15: "Morrigan", 16: "Alex", 17: "Viewtiful Joe",
    18: "Volnutt", 19: "Roll", 20: "Saki", 21: "Soki", 26: "Tekkaman Blade",
    27: "Joe the Condor", 28: "Yatterman-2", 29: "Zero", 30: "Frank West",
}

# ================== Safe reads ==================
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

# ================== Helpers ==================
def best_posy_off(base):
    # Quick sampler: pick the candidate with smallest variance
    samples = {off: [] for off in POSY_OFFS}
    end = time.time() + 0.5
    dt = 1/120
    while time.time() < end:
        for off in POSY_OFFS:
            samples[off].append(rdf32(base + off) or 0.0)
        time.sleep(dt)

    def variance(vs):
        if len(vs) < 2: return 1e9
        m = sum(vs)/len(vs)
        return sum((v-m)*(v-m) for v in vs)/max(1, len(vs)-1)

    best, best_score = None, None
    for off, series in samples.items():
        v = variance(series)
        score = 1/(1+v)
        if best_score is None or score > best_score:
            best, best_score = off, score
    return best or 0xF4

class MeterAddrCache:
    def __init__(self): self.addr_by_base = {}
    def drop(self, base): self.addr_by_base.pop(base, None)
    def get(self, base):
        if base in self.addr_by_base: return self.addr_by_base[base]
        for a in (base + METER_OFF_PRIMARY, base + METER_OFF_SECONDARY):
            v = rd32(a)
            if v in (50000, 0x0000C350) or (v is not None and 0 <= v <= 200_000):
                self.addr_by_base[base] = a
                return a
        self.addr_by_base[base] = base + METER_OFF_PRIMARY
        return self.addr_by_base[base]

METER_CACHE = MeterAddrCache()

class SlotResolver:
    def __init__(self):
        self.last_good = {}
        self.last_posy = {}
        self.ttl = 1.0
    def _probe(self, slot_val):
        for off in (0x10,0x18,0x1C,0x20):
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
            if lg and now < lg[1]: return lg[0], False
            return None, False
        mh = rd32(s + OFF_MAX_HP); ch = rd32(s + OFF_CUR_HP); ax = rd32(s + OFF_AUX_HP)
        if looks_like_hp(mh, ch, ax):
            self.last_good[slot_addr] = (s, now + self.ttl)
            return s, True
        a = self._probe(s)
        if a:
            self.last_good[slot_addr] = (a, now + self.ttl)
            return a, True
        lg = self.last_good.get(slot_addr)
        if lg and now < lg[1]: return lg[0], False
        return None, False

RESOLVER = SlotResolver()

# ================== Polling & state ==================
class FighterState:
    __slots__ = ("label","team","base","posy_off","char_id","name","max","cur","aux","meter","x","y","last_hit","prev_last_hit","last_hp")
    def __init__(self, label, team):
        self.label=label; self.team=team
        self.base=None; self.posy_off=None
        self.char_id=None; self.name="???"
        self.max=0; self.cur=0; self.aux=0
        self.meter=None; self.x=None; self.y=None
        self.last_hit=None; self.prev_last_hit=None
        self.last_hp=None

def read_block(base, posy_off, want_meter=True):
    if not base: return None
    max_hp = rd32(base + OFF_MAX_HP)
    cur_hp = rd32(base + OFF_CUR_HP)
    aux_hp = rd32(base + OFF_AUX_HP)
    if not looks_like_hp(max_hp, cur_hp, aux_hp): return None

    char_id = rd32(base + OFF_CHAR_ID)
    name = CHAR_NAMES.get(char_id, f"Unknown_ID_{char_id}") if char_id is not None else "???"
    x = rdf32(base + POSX_OFF)
    y = rdf32(base + (posy_off or 0xF4))
    last_hit = rd32(base + OFF_LAST_HIT)
    if last_hit is None or last_hit < 0 or last_hit > 200000: last_hit = None

    meter = None
    if want_meter:
        maddr = METER_CACHE.get(base)
        mv = rd32(maddr)
        if mv is not None and 0 <= mv <= 200_000:
            meter = mv

    return dict(max=max_hp, cur=cur_hp, aux=aux_hp, char_id=char_id, name=name, x=x, y=y, last_hit=last_hit, meter=meter)

def dist2(a, b):
    if a.x is None or a.y is None or b.x is None or b.y is None: return 9e9
    dx = (a.x - b.x); dy = (a.y - b.y)
    return dx*dx + dy*dy

EVT_UPDATE = "update"
EVT_HIT    = "hit"

class Poller(threading.Thread):
    def __init__(self, out_q):
        super().__init__(daemon=True)
        self.q = out_q
        self.running = True
        self.fighters = { lab: FighterState(lab, team) for (lab,_,team) in SLOTS }
        self.slot_map = { lab: addr for (lab,addr,_) in SLOTS }

    def run(self):
        hook()
        while self.running:
            # resolve bases
            for lab, slot_addr in self.slot_map.items():
                base, changed = RESOLVER.resolve_base(slot_addr)
                f = self.fighters[lab]
                if base and base != f.base:
                    f.base = base
                    f.posy_off = best_posy_off(base)
                    METER_CACHE.drop(base)
                    f.prev_last_hit = None
                    f.last_hp = None

            # read + hit detect
            for lab, f in self.fighters.items():
                if not f.base: continue
                blk = read_block(f.base, f.posy_off, want_meter=lab.endswith("C1"))
                if not blk: continue

                prev_last_hit = f.prev_last_hit
                prev_hp = f.last_hp
                was_hit = False
                dmg = None
                if blk["last_hit"] and blk["last_hit"] != prev_last_hit:
                    was_hit = True
                    dmg = blk["last_hit"]
                elif prev_hp is not None and blk["cur"] < prev_hp:
                    was_hit = True
                    dmg = prev_hp - blk["cur"]

                # commit values
                f.char_id = blk["char_id"]; f.name = blk["name"]
                f.max = blk["max"]; f.cur = blk["cur"]; f.aux = blk["aux"]
                f.x = blk["x"]; f.y = blk["y"]; f.meter = blk["meter"]
                f.last_hit = blk["last_hit"]; f.prev_last_hit = blk["last_hit"]
                f.last_hp = blk["cur"]

                if was_hit and dmg and dmg > 0:
                    opps = [o for o in self.fighters.values() if o.team != f.team and o.base]
                    if opps:
                        attacker = min(opps, key=lambda o: dist2(f, o))
                        self.q.put((EVT_HIT, {"ts": time.time(),"victim": f,"attacker": attacker,"dmg": int(dmg),"hp_from": int(prev_hp if prev_hp is not None else f.cur + dmg),"hp_to": int(f.cur),"dist2": dist2(f, attacker)
                        }))

            snap = {k: {
                "name": v.name, "team": v.team, "max": v.max, "cur": v.cur,
                "meter": v.meter, "x": v.x, "y": v.y, "last_hit": v.last_hit
            } for k,v in self.fighters.items()}
            self.q.put((EVT_UPDATE, snap))
            time.sleep(POLL_DT)

    def stop(self): self.running = False

# ================== GUI ==================
class HUD(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TvC HUD")
        self.geometry("1200x640")
        self.configure(bg="#0e0e10")

        # Global style
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#1a1b1e")
        style.configure("TLabel", background="#1a1b1e", foreground="#d0d0d0")
        style.configure("Title.TLabel", font=("Segoe UI", 12, "bold"), foreground="#ffffff")
        style.configure("HP.TLabel", font=("Consolas", 11, "bold"))
        style.configure("Mono.TLabel", font=("Consolas", 10))

        # Root grid: two rows -> panels (row 0) and log (row 1)
        root = ttk.Frame(self, padding=8)
        root.grid(row=0, column=0, sticky="nsew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        root.rowconfigure(1, weight=0)
        root.columnconfigure(0, weight=1)

        # Panels grid (2x2)
        panels = ttk.Frame(root)
        panels.grid(row=0, column=0, sticky="nsew")
        for c in (0,1): panels.columnconfigure(c, weight=1)
        for r in (0,1): panels.rowconfigure(r, weight=1)
        self.panels = {}

        def make_panel(parent, title, col, row, accent):
            f = ttk.Frame(parent, padding=10)
            f.grid(column=col, row=row, sticky="nsew", padx=6, pady=6)
            top   = ttk.Label(f, text=title, style="Title.TLabel"); top.pack(anchor="w")
            hp    = ttk.Label(f, text="HP: --/-- (---.-%)", style="HP.TLabel"); hp.pack(anchor="w", pady=(6,0))
            meter = ttk.Label(f, text="Meter: --", style="Mono.TLabel"); meter.pack(anchor="w")
            pos   = ttk.Label(f, text="Pos: X:--  Y:--", style="Mono.TLabel"); pos.pack(anchor="w")
            last  = ttk.Label(f, text="LastDmg: --", style="Mono.TLabel"); last.pack(anchor="w")
            bar   = tk.Frame(f, height=3, bg=accent); bar.pack(fill="x", pady=(8,0))
            return {"frame": f, "hp": hp, "meter": meter, "pos": pos, "last": last, "title": top}

        self.panels["P1-C1"] = make_panel(panels, "P1-C1", 0, 0, "#3aa0ff")
        self.panels["P1-C2"] = make_panel(panels, "P1-C2", 0, 1, "#3aa0ff")
        self.panels["P2-C1"] = make_panel(panels, "P2-C1", 1, 0, "#ff4d4f")
        self.panels["P2-C2"] = make_panel(panels, "P2-C2", 1, 1, "#ff4d4f")

        # === Recent Hits (bottom, single scrollable console) ===
        log_frame = ttk.Frame(root, padding=(2,8,2,2))
        log_frame.grid(row=1, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        ttk.Label(log_frame, text="Recent Hits", style="Title.TLabel").grid(row=0, column=0, sticky="w", padx=6, pady=(0,4))

        inner = ttk.Frame(log_frame)
        inner.grid(row=1, column=0, sticky="nsew")
        log_frame.rowconfigure(1, weight=1)
        inner.columnconfigure(0, weight=1)
        inner.rowconfigure(0, weight=1)

        yscroll = ttk.Scrollbar(inner, orient="vertical")
        self.log = tk.Text(inner, height=9, yscrollcommand=yscroll.set, bg="#101114", fg="#d0d0d0", insertbackground="#d0d0d0", font=("Consolas", 9), borderwidth=0, highlightthickness=0)
        yscroll.config(command=self.log.yview)
        self.log.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(state="disabled")

        # thread/poller
        self.q = queue.Queue()
        self.poller = Poller(self.q)
        self.after(10, self._start)

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _start(self):
        threading.Thread(target=hook, daemon=True).start()
        self.poller.start()
        self.after(50, self._drain_queue)

    def _drain_queue(self):
        try:
            while True:
                evt, payload = self.q.get_nowait()
                if evt == EVT_UPDATE:
                    self._apply_snapshot(payload)
                elif evt == EVT_HIT:
                    self._append_hit(payload)
        except queue.Empty:
            pass
        self.after(50, self._drain_queue)

    def _apply_snapshot(self, snap):
        for key, panel in self.panels.items():
            data = snap.get(key)
            if not data:
                panel["title"].configure(text=f"{key} — (waiting)")
                panel["hp"].configure(text="HP: --/-- (---.-%)", foreground="#9aa0a6")
                panel["meter"].configure(text="Meter: --")
                panel["pos"].configure(text="Pos: X:--  Y:--")
                panel["last"].configure(text="LastDmg: --")
                continue

            name = data["name"]
            cur, mx = data["cur"], data["max"]
            pct = (cur / mx * 100.0) if mx else 0.0
            panel["title"].configure(text=f"{key} — {name}")

            if mx and pct > 66: color="#50fa7b"
            elif mx and pct > 33: color="#f1fa8c"
            else: color="#ff5555"
            panel["hp"].configure(text=f"HP: {cur}/{mx} ({pct:5.1f}%)", foreground=color)

            m = data["meter"]
            panel["meter"].configure(text=f"Meter: {m if m is not None else '--'}")

            x = "--" if data["x"] is None else f"{data['x']:.3f}"
            y = "--" if data["y"] is None else f"{data['y']:.3f}"
            panel["pos"].configure(text=f"Pos: X:{x}  Y:{y}")

            lh = data["last_hit"]
            panel["last"].configure(text=f"LastDmg: {lh if lh else '--'}")

    def _append_hit(self, h):
        ts = h["ts"]
        vic = h["victim"]; atk = h["attacker"]
        line = (f"[{int(ts)}] HIT  victim={vic.label}({vic.name:<16}) "
                f"dmg={h['dmg']:4d}  hp:{h['hp_from']}->{h['hp_to']}  "
                f"attacker≈{atk.label}({atk.name})  dist2={h['dist2']:.3f}\n")
        self.log.configure(state="normal")
        self.log.insert("end", line)
        # keep log from growing forever
        max_lines = 500
        if int(self.log.index('end-1c').split('.')[0]) > max_lines:
            self.log.delete("1.0", "50.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def on_close(self):
        try: self.poller.stop()
        except Exception: pass
        self.destroy()

if __name__ == "__main__":
    app = HUD()
    app.mainloop()
