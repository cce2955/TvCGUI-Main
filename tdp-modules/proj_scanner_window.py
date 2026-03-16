# proj_scanner_window.py
from __future__ import annotations
import json, struct, threading, tkinter as tk
from tkinter import ttk, simpledialog, messagebox
from tk_host import tk_call

try:
    from dolphin_io import rbytes, wbytes
except Exception:
    rbytes = None
    wbytes = None

# Full 12-byte signature — damage word + 0C sentinel + FF FF FF FF guard.
# This cuts ~318 false positives vs the old 8-byte suffix.
# Pattern:  00 00 XX YY  00 00 00 0C  FF FF FF FF
# We search for the 8-byte tail and verify the 4 bytes before it.
_SUFFIX    = b"\x00\x00\x00\x0C"
SCAN_START = 0x90000000
SCAN_END   = 0x94000000
SCAN_BLOCK = 0x40000
PROJ_MAP_FILE = "projectilemap.json"
PROJ_IDS_FILE = "projectile_ids.json"

FIELD_OFFSETS = {
    # Hitbox radius for projectile (exponential effect when increased)
    "radius":   0x02C,  # f32 — projectile hitbox radius (was vel_s)
    # speed2 = universal speed field, present in ALL block types
    "aerial_kb_x": 0x024,  # f32 — aerial knockback X
    # Misc u16 fields
    "c042":     0x042,  # u16 — always 10
    "type":     0x050,  # u8  — 3=linear, 4=physics
    "id":       0x052,  # u16 — projectile type ID
    "lifetime": 0x05A,  # u8  — active frames / lifetime
    "hb_size":  0x06E,  # u16 — hitbox size
    # Speed block (template format only)
    "speed":    0x080,  # f32 — speed scalar (template blocks)
    "accel":    0x084,  # f32 — always 1.0
    "hitbox":   0x08C,  # f32 — hitbox radius (100.0 standard)
    "arc":      0x090,  # f32 — arc/gravity (Roll only)
    "arc2":     0x094,  # f32 — arc modifier (Roll only)
    # Velocity triple 2
    "vel2_x":   0x0D4,
    "vel2_y":   0x0D8,
    "vel2_s":   0x0DC,
    # Old unknowns
    "u01": 0x10,
    "u02": 0x14,
    "u03": 0x18,
    "u04": 0x42,
    "u05": 0x48,
    "u06": 0x52,
    "u07": 0x5A,
    "u08": 0x68,
    "u09": 0x72,
}

import re as _re

_NAME_TO_KEY = {
    "Ryu": "RYU", "Chun-Li": "CHUN", "Jun the Swan": "JUN",
    "Ken the Eagle": "KEN", "Alex": "ALEX", "Batsu": "BATSU",
    "Frank West": "FRANK", "Volnutt": "VOLNUTT", "Morrigan": "MORRIGAN",
    "Roll": "ROLL", "Saki": "SAKI", "Viewtiful Joe": "VJOE",
    "Zero": "ZERO", "Casshan": "CASSHAN", "Doronjo": "DORONJO",
    "Ippatsuman": "IPPATSMAN", "Joe the Condor": "JOE",
    "Tekkaman": "TEKKAMAN", "Tekkaman Blade": "BLADE",
    "Yatterman-1": "YATTER1", "Yatterman-2": "YATTER2",
    "Gold Lightan": "LIGHTAN", "PTX-40A": "PTX",
}

# Per-character byte signatures found at hit_addr - 8 (4 bytes).
# Each entry: json_key -> list of known 4-byte signatures at that offset.
# Extend as more characters are confirmed.
CHAR_SIGS = {
    "KEN": [
        b"\x00\x00\x00\x09",  # hit-8: unique to Ken
    ],
    "RYU": [
        b"\x00\x04\x01\x02",  # c word: 00 04 01 02
    ],
    "JUN": [
        b"\x00\x04\x00\x82",  # c word: 00 04 00 82 (Yoyo template)
        b"\x00\x00\x01\x0C",  # hit-8 for Bingo script blocks
    ],
}

# Sig source: "c" = 4 bytes at hit-4 (already parsed as c[])
#             "pre" = 4 bytes at hit-8
CHAR_SIG_OFFSETS = {
    "KEN": "pre",   # Ken's sig is at hit-8
    "RYU": "c",     # Ryu's sig is the c word itself
    "JUN": "c",     # Jun's sig is the c word itself
}

_SIG_C_TO_KEYS: dict[bytes, list[str]] = {}    # c word (hit-4)
_SIG_PRE_TO_KEYS: dict[bytes, list[str]] = {}  # pre word (hit-8)

for _k, _sigs in CHAR_SIGS.items():
    _target = _SIG_PRE_TO_KEYS if CHAR_SIG_OFFSETS.get(_k) == "pre" else _SIG_C_TO_KEYS
    for _s in _sigs:
        _target.setdefault(_s, []).append(_k)
_ACTOR_SIG = b"\x05\x2B"

def _scan_actor_blocks(data, base_addr, hits, lookup):

    pos = 0

    while True:

        idx = data.find(_ACTOR_SIG, pos)

        if idx < 0:
            break

        pos = idx + 1

        if idx + 6 >= len(data):
            continue

        dmg = (data[idx+3] << 16) | (data[idx+4] << 8) | data[idx+5]

        if dmg < 500 or dmg > 20000:
            continue

        addr = base_addr + idx

        # --------------------------------
        # match actor damage to damage map
        # --------------------------------

        if dmg in lookup:

            for key, mv in lookup[dmg]:

                hits.append({
                    "addr": addr,
                    "key": key,
                    "move": mv,
                    "dmg": dmg,
                    "fmt": "actor",

                    "radius": "?",
                    "speed": "?",
                    "accel": "?",
                    "aerial_kb_x": "?",
                    "arc": "?",
                    "arc2": "?",
                    "hitbox": "?",
                    "type": "?",
                    "id": "?",
                    "lifetime": "?",
                    "hb_size": "?",
                    "vel2_x": "?",
                    "vel2_y": "?",
                    "vel2_s": "?",
                    "u01": "?",
                    "u02": "?",
                    "u03": "?",
                    "u04": "?",
                    "u05": "?",
                    "u06": "?",
                    "u07": "?",
                    "u08": "?",
                    "u09": "?",
                })

        else:

            hits.append({
                "addr": addr,
                "key": "?",
                "move": "Actor",
                "dmg": dmg,
                "fmt": "actor",

                "radius": "?",
                "speed": "?",
                "accel": "?",
                "aerial_kb_x": "?",
                "arc": "?",
                "arc2": "?",
                "hitbox": "?",
                "type": "?",
                "id": "?",
                "lifetime": "?",
                "hb_size": "?",
                "vel2_x": "?",
                "vel2_y": "?",
                "vel2_s": "?",
                "u01": "?",
                "u02": "?",
                "u03": "?",
                "u04": "?",
                "u05": "?",
                "u06": "?",
                "u07": "?",
                "u08": "?",
                "u09": "?",
            })

def _keys_for_block(c_word: bytes, pre_word: bytes) -> list[str]:
    """Return candidate json keys given the c word and pre word."""
    keys = set()
    keys.update(_SIG_C_TO_KEYS.get(bytes(c_word), []))
    keys.update(_SIG_PRE_TO_KEYS.get(bytes(pre_word), []))
    return list(keys)
    try:
        with open(PROJ_MAP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[proj_scanner] {e}")
        return {}

def _load_map():
    try:
        with open(PROJ_MAP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[proj_scanner] {e}")
        return {}

def _load_ids():
    try:
        with open(PROJ_IDS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[proj_scanner] {e}")
        return {}

def _build_lookup(proj_map, active_keys):
    lookup = {}
    for key, moves in proj_map.items():
        if key not in active_keys:
            continue
        for entry in moves:
            dmg = int(entry.get("dmg", 0))
            if dmg:
                lookup.setdefault(dmg, []).append((key, entry.get("move", "?")))
    return lookup

def _read_u16(addr: int) -> str:
    if rbytes is None:
        return "?"
    try:
        b = rbytes(addr, 2)
        if b and len(b) == 2:
            return str((b[0] << 8) | b[1])
    except Exception:
        pass
    return "?"

def _write_u16(addr: int, val: int) -> bool:
    if wbytes is None:
        return False
    try:
        return bool(wbytes(addr, bytes([(val >> 8) & 0xFF, val & 0xFF])))
    except Exception as e:
        print(f"[proj_scanner] write u16 failed: {e}")
        return False

def _read_f32(addr: int) -> str:
    """Read a big-endian float, return formatted string or '?'"""
    if rbytes is None:
        return "?"
    try:
        b = rbytes(addr, 4)
        if b and len(b) == 4:
            v = struct.unpack(">f", b)[0]
            return f"{v:.4f}"
    except Exception:
        pass
    return "?"

def _write_f32(addr: int, val: float) -> bool:
    if wbytes is None:
        return False
    try:
        return bool(wbytes(addr, struct.pack(">f", val)))
    except Exception as e:
        print(f"[proj_scanner] write f32 failed: {e}")
        return False

def _run_scan(active_keys, progress_cb, done_cb):
    if rbytes is None:
        done_cb([]); return
    proj_map = _load_map()
    id_map   = _load_ids()
    lookup   = _build_lookup(proj_map, active_keys)
    # build a set of ALL known damages across all chars for unknown detection
    all_known_dmgs = set()
    for moves in proj_map.values():
        for e in moves:
            d = int(e.get("dmg", 0))
            if d: all_known_dmgs.add(d)

    total = SCAN_END - SCAN_START
    hits  = []
    addr  = SCAN_START
    while addr < SCAN_END:
        sz = min(SCAN_BLOCK, SCAN_END - addr)
        try: data = rbytes(addr, sz)
        except: data = b""
        if data:
            _scan_actor_blocks(data, addr, hits, lookup)
            pos = 0
            while True:
                idx = data.find(_SUFFIX, pos)
                if idx < 0: break
                pos = idx + 1
                if idx < 4: continue
                c = data[idx-4:idx]
                if c[1]: continue
                dmg = (c[2] << 8) | c[3]
                if not dmg: continue
                # Determine format by what follows the 0C sentinel
                after = data[idx+4:idx+8] if idx+8 <= len(data) else b''
                if after == b'\xFF\xFF\xFF\xFF':
                    fmt = "template"
                elif c[0] == 0x00:
                    fmt = "template2"
                else:
                    fmt = f"script(0x{c[0]:02X})"
                a = addr + idx - 4
                # Read 4 bytes before c word for pre-signature
                pre8 = data[idx-8:idx-4] if idx >= 8 else b''
                sig_keys = _keys_for_block(c, pre8)

                fields = {
                    "radius":   _read_f32(a + FIELD_OFFSETS["radius"]),
                    "aerial_kb_x": _read_f32(a + FIELD_OFFSETS["aerial_kb_x"]),
                    "type":     _read_u16(a + FIELD_OFFSETS["type"]),
                    "id":       _read_u16(a + FIELD_OFFSETS["id"]),
                    "lifetime": _read_u16(a + FIELD_OFFSETS["lifetime"]),
                    "hb_size":  _read_u16(a + FIELD_OFFSETS["hb_size"]),
                    "speed":    _read_f32(a + FIELD_OFFSETS["speed"]),
                    "accel":    _read_f32(a + FIELD_OFFSETS["accel"]),
                    "hitbox":   _read_f32(a + FIELD_OFFSETS["hitbox"]),
                    "arc":      _read_f32(a + FIELD_OFFSETS["arc"]),
                    "arc2":     _read_f32(a + FIELD_OFFSETS["arc2"]),
                    "vel2_x":   _read_f32(a + FIELD_OFFSETS["vel2_x"]),
                    "vel2_y":   _read_f32(a + FIELD_OFFSETS["vel2_y"]),
                    "vel2_s":   _read_f32(a + FIELD_OFFSETS["vel2_s"]),
                    "u01":   _read_f32(a + FIELD_OFFSETS["u01"]),
                    "u02":   _read_f32(a + FIELD_OFFSETS["u02"]),
                    "u03":   _read_f32(a + FIELD_OFFSETS["u03"]),
                    "u04":   _read_u16(a + FIELD_OFFSETS["u04"]),
                    "u05":   _read_u16(a + FIELD_OFFSETS["u05"]),
                    "u06":   _read_u16(a + FIELD_OFFSETS["u06"]),
                    "u07":   _read_u16(a + FIELD_OFFSETS["u07"]),
                    "u08":   _read_u16(a + FIELD_OFFSETS["u08"]),
                    "u09":   _read_u16(a + FIELD_OFFSETS["u09"]),
                }
                # ------------------------------------------------
                # ACTOR SUMMONS
                # ------------------------------------------------
                if fmt == "actor":

                    if dmg in lookup:

                        for key, mv in lookup[dmg]:

                            hits.append({
                                "addr": a,
                                "key": key,
                                "move": mv,
                                "dmg": dmg,
                                "fmt": "actor",
                                "radius": "?",
                                "speed": "?",
                                "accel": "?",
                                "aerial_kb_x": "?",
                                "arc": "?",
                                "arc2": "?",
                                "hitbox": "?",
                                "type": "?",
                                "id": "?",
                                "lifetime": "?",
                                "hb_size": "?",
                                "vel2_x": "?",
                                "vel2_y": "?",
                                "vel2_s": "?",
                                "u01": "?",
                                "u02": "?",
                                "u03": "?",
                                "u04": "?",
                                "u05": "?",
                                "u06": "?",
                                "u07": "?",
                                "u08": "?",
                                "u09": "?",
                            })

                    else:

                        hits.append({
                            "addr": a,
                            "key": "?",
                            "move": "Actor",
                            "dmg": dmg,
                            "fmt": "actor",
                            "radius": "?",
                            "speed": "?",
                            "accel": "?",
                            "aerial_kb_x": "?",
                            "arc": "?",
                            "arc2": "?",
                            "hitbox": "?",
                            "type": "?",
                            "id": "?",
                            "lifetime": "?",
                            "hb_size": "?",
                            "vel2_x": "?",
                            "vel2_y": "?",
                            "vel2_s": "?",
                            "u01": "?",
                            "u02": "?",
                            "u03": "?",
                            "u04": "?",
                            "u05": "?",
                            "u06": "?",
                            "u07": "?",
                            "u08": "?",
                            "u09": "?",
                        })
                # ------------------------------------------------
                # NORMAL PROJECTILES
                # ------------------------------------------------
                elif dmg in lookup:

                    matches = lookup[dmg]

                    if len({k for k,_ in matches}) > 1:

                        proj_id = fields.get("id")

                        if proj_id is not None:
                            try:
                                proj_id_int = int(proj_id)
                            except:
                                proj_id_int = None

                            if proj_id_int is not None:
                                id_matches = [
                                    (k,mv) for k,mv in matches
                                    if id_map.get(k,{}).get(mv) == proj_id_int
                                ]

                                if id_matches:
                                    matches = id_matches

                    for key, mv in matches:
                        hits.append({
                            "addr": a,
                            "key": key,
                            "move": mv,
                            "dmg": dmg,
                            "fmt": fmt,
                            **fields
                        })

                # ------------------------------------------------
                # UNKNOWN
                # ------------------------------------------------
                elif dmg >= 500:

                    hits.append({
                        "addr": a,
                        "key": "?",
                        "move": "Unknown",
                        "dmg": dmg,
                        "fmt": fmt,
                        **fields
                    })
        progress_cb((addr - SCAN_START + sz) / total * 100.0)
        addr += sz

    # dump context around each hit
    _dump_hits(hits)

    done_cb(hits)


def _dump_hits(hits: list, context: int = 0x100):
    """Write addr-0x100 .. addr+0x100 for each hit to proj_dump.bin."""
    if rbytes is None or not hits:
        return
    try:
        with open("proj_dump.bin", "wb") as f:
            for h in hits:
                base = max(h["addr"] - context, SCAN_START)
                size = min(context * 2, SCAN_END - base)
                try:
                    data = rbytes(base, size)
                except Exception:
                    data = b""
                # write a small header: base_addr (4 bytes BE), hit_addr (4 bytes BE), size (4 bytes BE)
                f.write(base.to_bytes(4, "big"))
                f.write(h["addr"].to_bytes(4, "big"))
                f.write(len(data).to_bytes(4, "big"))
                f.write(data)
        print(f"[proj_scanner] dumped {len(hits)} context block(s) to proj_dump.bin")
    except Exception as e:
        print(f"[proj_scanner] dump failed: {e}")

def _write_dmg(addr: int, new_dmg: int) -> bool:
    if wbytes is None:
        return False
    try:
        return bool(wbytes(addr + 2, bytes([(new_dmg >> 8) & 0xFF, new_dmg & 0xFF])))
    except Exception as e:
        print(f"[proj_scanner] write dmg failed: {e}")
        return False
def _write_actor_dmg(addr: int, new_dmg: int) -> bool:
    if wbytes is None:
        return False
    try:
        # actor format is: 05 2B ?? 00 HI LO
        # preserve the leading 00, only write HI/LO
        return bool(wbytes(addr + 4, bytes([
            (new_dmg >> 8) & 0xFF,
            new_dmg & 0xFF
        ])))
    except Exception as e:
        print(f"[proj_scanner] write actor dmg failed: {e}")
        return False
# column index -> (label, field_key, addr_offset, is_float)
# (col_id, header, field_key, is_float)
_COLS = [
    ("address",      "Address",   None,          False),
    ("char",         "Char",      None,          False),
    ("move",         "Move",      None,          False),
    ("dmg",          "Damage",    "dmg",         False),
    ("radius",       "Radius",    "radius",      True),
    ("speed",        "Speed",     "speed",       True),
    ("accel",        "Accel",     "accel",       True),
    ("aerial_kb_x",  "Air KB X",  "aerial_kb_x", True),
    ("arc",          "Arc",       "arc",         True),
    ("arc2",         "Arc2",      "arc2",        True),
    ("hitbox",       "Hitbox",    "hitbox",      True),
    ("type",         "Type",      "type",        False),
    ("id",           "ID",        "id",          False),
    ("lifetime",     "Lifetime",  "lifetime",    False),
    ("hb_size",      "HB Size",   "hb_size",     False),
    ("fmt",          "Fmt",       None,          False),
    ("vel2_x",       "Vel2 X",    "vel2_x",      True),
    ("vel2_y",       "Vel2 Y",    "vel2_y",      True),
    ("vel2_s",       "Vel2 S",    "vel2_s",      True),
    ("u01",          "?? 01",     "u01",         True),
    ("u02",          "?? 02",     "u02",         True),
    ("u03",          "?? 03",     "u03",         True),
    ("u04",          "?? 04",     "u04",         False),
    ("u05",          "?? 05",     "u05",         False),
    ("u06",          "?? 06",     "u06",         False),
    ("u07",          "?? 07",     "u07",         False),
    ("u08",          "?? 08",     "u08",         False),
    ("u09",          "?? 09",     "u09",         False),
]
_COL_IDS = [c[0] for c in _COLS]

class ProjScannerWindow:
    def __init__(self, master, get_active_fn):
        self._get_active = get_active_fn
        self._scanning   = False
        self._keys: set  = set()
        self._addr_by_iid: dict[str, int] = {}

        self.root = tk.Toplevel(master)
        self.root.title("Projectile Definition Scanner")
        self.root.geometry("1000x560")
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        self._build()
        self._auto_scan()

    def _build(self):
        top = ttk.Frame(self.root)
        top.pack(side="top", fill="x", padx=8, pady=6)
        self._active_var = tk.StringVar(value="Active: --")
        ttk.Label(top, textvariable=self._active_var).pack(side="left")
        self._scan_btn = ttk.Button(top, text="Rescan", command=self._start)
        self._scan_btn.pack(side="right", padx=4)

        self._prog = tk.DoubleVar()
        ttk.Progressbar(self.root, variable=self._prog, maximum=100).pack(
            fill="x", padx=8, pady=(0, 4))
        self._status = tk.StringVar(value="Scanning...")
        ttk.Label(self.root, textvariable=self._status, anchor="w").pack(fill="x", padx=8)

        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=8, pady=6)

        self._tree = ttk.Treeview(frame, columns=_COL_IDS, show="headings", height=24)
        widths = {"address": 110, "char": 80, "move": 180, "dmg": 65,
                  "speed": 75, "accel": 75, "arc": 75}
        self._sort_col = None
        self._sort_asc = True
        for col_id, header, _, _ in _COLS:
            self._tree.heading(col_id, text=header,
                command=lambda c=col_id: self._sort_by(c))
            w = widths.get(col_id, 65)
            self._tree.column(col_id, width=w, anchor="center")
        self._tree.column("move", anchor="w")

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        self._tree.bind("<Double-Button-1>", self._on_double_click)
        self._tree.bind("<Button-3>",        self._on_right_click)

        ttk.Label(self.root,
                  text="Double-click Dmg/Speed/Accel/Arc/Lifetime to edit. Right-click to copy address.",
                  foreground="gray").pack(anchor="w", padx=8, pady=(0, 4))

    def _auto_scan(self):
        names = self._get_active()
        self._keys = {_NAME_TO_KEY[n] for n in names if n in _NAME_TO_KEY}
        self._active_var.set(f"Active: {', '.join(sorted(names)) or 'none'}")
        self._start()

    def _start(self):
        if self._scanning: return
        names = self._get_active()
        self._keys = {_NAME_TO_KEY[n] for n in names if n in _NAME_TO_KEY}
        self._active_var.set(f"Active: {', '.join(n for n in names if n) or 'none'}")
        if not self._keys:
            self._status.set("No active characters with known projectiles.")
            return
        self._scanning = True
        self._scan_btn.config(state="disabled")
        self._prog.set(0)
        self._addr_by_iid.clear()
        for i in self._tree.get_children(): self._tree.delete(i)
        self._status.set("Scanning MEM2...")
        threading.Thread(target=_run_scan,
            args=(set(self._keys), self._on_prog, self._on_done),
            daemon=True).start()

    def _sort_by(self, col_id):
        # Toggle direction if same column, else reset to ascending
        if self._sort_col == col_id:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col_id
            self._sort_asc = True

        # Update heading arrows
        col_map = {c[0]: c[1] for c in _COLS}
        for cid, header, _, _ in _COLS:
            if cid == col_id:
                arrow = " ▲" if self._sort_asc else " ▼"
                self._tree.heading(cid, text=header + arrow)
            else:
                self._tree.heading(cid, text=col_map[cid])

        # Get all rows
        items = [(self._tree.set(iid, col_id), iid)
                 for iid in self._tree.get_children("")]

        # Try numeric sort, fall back to string
        def sort_key(val):
            try: return (0, float(val))
            except: return (1, val.lower())

        items.sort(key=lambda x: sort_key(x[0]), reverse=not self._sort_asc)
        for idx, (_, iid) in enumerate(items):
            self._tree.move(iid, "", idx)

    def _on_prog(self, pct):
        try: self.root.after(0, lambda: self._prog.set(pct))
        except: pass

    def _on_done(self, hits):
        def _f():
            for h in hits:
                _TYPE_LABELS = {"3": "3:Linear", "4": "4:Physics", 3: "3:Linear", 4: "4:Physics"}
                type_val = h["type"]
                type_str = _TYPE_LABELS.get(type_val, str(type_val) if type_val is not None else "")
                iid = self._tree.insert("", "end", values=(
                    f"0x{h['addr']:08X}", h["key"], h["move"], h["dmg"],
                    h["radius"], h["speed"], h["accel"], h["aerial_kb_x"],
                    h["arc"], h["arc2"], h["hitbox"],
                    type_str, h["id"], h["lifetime"], h["hb_size"],
                    h.get("fmt",""),
                    h["vel2_x"], h["vel2_y"], h["vel2_s"],
                    h["u01"], h["u02"], h["u03"],
                    h["u04"], h["u05"], h["u06"],
                    h["u07"], h["u08"], h["u09"],
                ))
                self._addr_by_iid[iid] = h["addr"]
            self._scanning = False
            self._scan_btn.config(state="normal")
            self._prog.set(100)
            self._status.set(f"Done — {len(hits)} match(es) found. Double-click to edit.")
        try: self.root.after(0, _f)
        except: pass

    def _col_index(self, event) -> int:
        col = self._tree.identify_column(event.x)
        return int(col[1:]) - 1 if col else -1

    def _on_right_click(self, event):
        iid = self._tree.identify_row(event.y)
        if not iid: return
        addr = self._addr_by_iid.get(iid)
        if addr is None: return

        col_idx = self._col_index(event)
        field_addr = addr
        field_label = "base"
        if 0 <= col_idx < len(_COLS):
            col_id, header, fkey, _ = _COLS[col_idx]
            if fkey and fkey != "dmg" and fkey in FIELD_OFFSETS:
                field_addr = addr + FIELD_OFFSETS[fkey]
                field_label = header
            elif fkey == "dmg":
                vals = self._tree.item(iid, "values")
                fmt_val = str(vals[15]) if len(vals) > 15 else ""
                if fmt_val == "actor":
                    field_addr = addr + 4
                else:
                    field_addr = addr + 2
                field_label = "dmg"
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"Copy base address (0x{addr:08X})",
                         command=lambda: self._copy(f"0x{addr:08X}"))
        if field_addr != addr:
            menu.add_command(label=f"Copy {field_label} address (0x{field_addr:08X})",
                             command=lambda: self._copy(f"0x{field_addr:08X}"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._status.set(f"Copied {text}")

    def _on_double_click(self, event):
        col_idx = self._col_index(event)
        iid     = self._tree.identify_row(event.y)
        if not iid or col_idx < 0: return

        col_id, header, fkey, is_float = _COLS[col_idx]
        if col_id in ("address", "char", "move"): return

        addr = self._addr_by_iid.get(iid)
        if addr is None: return

        vals = self._tree.item(iid, "values")
        fmt_val = str(vals[15]) if len(vals) > 15 else ""

        if fkey == "dmg":
            if fmt_val == "actor":
                write_addr = addr + 4
            else:
                write_addr = addr + 2
        elif fkey in FIELD_OFFSETS:
            write_addr = addr + FIELD_OFFSETS[fkey]
        else:
            return

        vals    = self._tree.item(iid, "values")
        cur_val = vals[col_idx]

        new_val = simpledialog.askstring(
            f"Edit {header}",
            f"Move: {vals[2]}\nAddress: 0x{write_addr:08X}\nCurrent: {cur_val}\n\nNew value:",
            parent=self.root, initialvalue=str(cur_val),
        )
        if new_val is None: return
        new_val = new_val.strip()

        if is_float:
            try:
                fval = float(new_val)
            except ValueError:
                messagebox.showerror("Invalid", f"'{new_val}' is not a valid float.", parent=self.root)
                return
            if _write_f32(write_addr, fval):
                self._tree.set(iid, col_id, f"{fval:.4f}")
                self._status.set(f"Wrote {fval} to 0x{write_addr:08X}")
            else:
                messagebox.showerror("Write failed", "Could not write to Dolphin.", parent=self.root)
        else:
            try:
                ival = int(new_val, 16) if new_val.startswith("0x") else int(new_val)
            except ValueError:
                messagebox.showerror("Invalid", f"'{new_val}' is not a valid number.", parent=self.root)
                return
            if not (0 <= ival <= 0xFFFF):
                messagebox.showerror("Out of range", "Value must be 0–65535.", parent=self.root)
                return
            if fkey == "dmg":
                if fmt_val == "actor":
                    ok = _write_actor_dmg(addr, ival)
                else:
                    ok = _write_dmg(addr, ival)
            else:
                ok = _write_u16(write_addr, ival)
            if ok:
                self._tree.set(iid, col_id, ival)
                self._status.set(f"Wrote {ival} to 0x{write_addr:08X}")
            else:
                messagebox.showerror("Write failed", "Could not write to Dolphin.", parent=self.root)

_inst = None

def open_proj_scanner_window(get_active_fn):
    def _c(master):
        global _inst
        if _inst:
            try: _inst.root.lift(); return
            except: pass
        _inst = ProjScannerWindow(master, get_active_fn)
    tk_call(_c)