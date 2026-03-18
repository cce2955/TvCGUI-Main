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
# Pattern:  00 00 XX YY  00 00 00 0C  FF FF FF FF
# We search for the 8-byte tail and verify the 4 bytes before it.
_SUFFIX    = b"\x00\x00\x00\x0C"
SCAN_START = 0x90000000
SCAN_END   = 0x94000000
SCAN_BLOCK = 0x40000
PROJ_MAP_FILE = "projectilemap.json"
PROJ_IDS_FILE = "projectile_ids.json"

FIELD_OFFSETS = {
    "radius":      0x02C,  # f32 — projectile hitbox radius
    "aerial_kb_x": 0x024,  # f32 — aerial knockback X
    "c042":        0x042,  # u16 — always 10
    "type":        0x050,  # u8  — 3=linear, 4=physics
    "id":          0x052,  # u16 — projectile type ID
    "lifetime":    0x05A,  # u8  — active frames / lifetime
    "hb_size":     0x06E,  # u16 — hitbox size
    "speed":       0x080,  # f32 — speed scalar (template blocks)
    "accel":       0x084,  # f32 — always 1.0
    "hitbox":      0x08C,  # f32 — hitbox radius (100.0 standard)
    "arc":         0x090,  # f32 — arc/gravity (Roll only)
    "arc2":        0x094,  # f32 — arc modifier (Roll only)
    "vel2_x":      0x0D4,
    "vel2_y":      0x0D8,
    "vel2_s":      0x0DC,
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

CHAR_SIGS = {
    "KEN": [b"\x00\x00\x00\x09"],
    "RYU": [b"\x00\x04\x01\x02"],
    "JUN": [b"\x00\x04\x00\x82", b"\x00\x00\x01\x0C"],
}

CHAR_SIG_OFFSETS = {
    "KEN": "pre",
    "RYU": "c",
    "JUN": "c",
}

_SIG_C_TO_KEYS: dict[bytes, list[str]] = {}
_SIG_PRE_TO_KEYS: dict[bytes, list[str]] = {}

for _k, _sigs in CHAR_SIGS.items():
    _target = _SIG_PRE_TO_KEYS if CHAR_SIG_OFFSETS.get(_k) == "pre" else _SIG_C_TO_KEYS
    for _s in _sigs:
        _target.setdefault(_s, []).append(_k)

# ---------------------------------------------------------------------------
# Script opcode table
#
# Maps a 2-byte opcode (found at the scan hit position) to:
#   fmt_name   — the format string shown in the Fmt column
#   dmg_offset — byte offset from hit address to the 2-byte damage field
#
# The suffix-scan path produces "template" / "template2" / "script(0xNN)"
# names via _fmt_for_suffix_block(); those are NOT listed here because they
# don't have a leading opcode signature to search for.
# ---------------------------------------------------------------------------
SCRIPT_OPCODES: dict[bytes, dict] = {
    b"\x05\x2B": {
        "fmt_name":   "script(0x052B)",
        "dmg_offset": 4,   # damage is at hit+4..hit+5  (was "actor" logic)
    },
    # Add further opcodes here as they are identified, e.g.:
    # b"\x05\x2C": {"fmt_name": "script(0x052C)", "dmg_offset": 4},
}

# Convenience: the offset to use when writing damage for a given fmt string.
# Suffix-scan blocks always use offset 2; opcode-scan blocks vary per opcode.
def _dmg_write_offset(fmt: str) -> int:
    for info in SCRIPT_OPCODES.values():
        if info["fmt_name"] == fmt:
            return info["dmg_offset"]
    return 2  # default for template / template2 / script(0xNN) suffix blocks


def _keys_for_block(c_word: bytes, pre_word: bytes) -> list[str]:
    keys = set()
    keys.update(_SIG_C_TO_KEYS.get(bytes(c_word), []))
    keys.update(_SIG_PRE_TO_KEYS.get(bytes(pre_word), []))
    return list(keys)


def _fmt_for_suffix_block(c: bytes, after: bytes) -> str:
    """Derive the format label for a suffix-scan hit from its surrounding bytes."""
    if after == b'\xFF\xFF\xFF\xFF':
        return "template"
    if c[0] == 0x00:
        return "template2"
    return f"script(0x{c[0]:02X})"


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


# ---------------------------------------------------------------------------
# Memory helpers
# ---------------------------------------------------------------------------

def _read_u8(addr: int) -> str:
    if rbytes is None:
        return "?"
    try:
        b = rbytes(addr, 1)
        if b and len(b) == 1:
            return str(b[0])
    except Exception:
        pass
    return "?"

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

def _read_u16_hex(addr: int) -> str:
    if rbytes is None:
        return "?"
    try:
        b = rbytes(addr, 2)
        if b and len(b) == 2:
            v = (b[0] << 8) | b[1]
            return f"0x{v:04X}"
    except Exception:
        pass
    return "?"

def _read_f32(addr: int) -> str:
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

def _write_u16(addr: int, val: int) -> bool:
    if wbytes is None:
        return False
    try:
        return bool(wbytes(addr, bytes([(val >> 8) & 0xFF, val & 0xFF])))
    except Exception as e:
        print(f"[proj_scanner] write u16 failed: {e}")
        return False

def _write_f32(addr: int, val: float) -> bool:
    if wbytes is None:
        return False
    try:
        return bool(wbytes(addr, struct.pack(">f", val)))
    except Exception as e:
        print(f"[proj_scanner] write f32 failed: {e}")
        return False

def _write_dmg(addr: int, new_dmg: int, fmt: str) -> bool:
    """Write a damage value to the correct offset for the given format."""
    offset = _dmg_write_offset(fmt)
    return _write_u16(addr + offset, new_dmg)


# ---------------------------------------------------------------------------
# Blank fields template for opcode-scan hits (no suffix-block fields)
# ---------------------------------------------------------------------------
_OPCODE_HIT_FIELDS = {
    "radius": "?", "speed": "?", "accel": "?", "aerial_kb_x": "?",
    "arc": "?", "arc2": "?", "hitbox": "?", "type": "?", "id": "?",
    "lifetime": "?", "hb_size": "?",
    "vel2_x": "?", "vel2_y": "?", "vel2_s": "?",
    "u01": "?", "u02": "?", "u03": "?",
    "u04": "?", "u05": "?", "u06": "?",
    "u07": "?", "u08": "?", "u09": "?",
}


def _scan_opcode_blocks(data, base_addr, hits, lookup):
    """
    Search for all known SCRIPT_OPCODES signatures within a data block and
    emit hits.  This replaces the old _scan_actor_blocks / 'actor' special case.
    """
    for sig, info in SCRIPT_OPCODES.items():
        fmt_name   = info["fmt_name"]
        dmg_offset = info["dmg_offset"]

        pos = 0
        while True:
            idx = data.find(sig, pos)
            if idx < 0:
                break
            pos = idx + 1

            # Need at least dmg_offset+2 bytes after the opcode start
            if idx + dmg_offset + 2 > len(data):
                continue

            hi = data[idx + dmg_offset]
            lo = data[idx + dmg_offset + 1]
            dmg = (hi << 8) | lo

            if dmg < 500 or dmg > 20000:
                continue

            addr = base_addr + idx

            # Script-block debug fields (opcode context, not suffix-block fields)
            extra = {
                "preA":   _read_u8(addr - 2),
                "preB":   _read_u8(addr - 1),
                "opcode": _read_u16_hex(addr),
                "param1": _read_u16_hex(addr + 2),
                "param2": _read_u16_hex(addr + 4),
                "param3": _read_u16_hex(addr + 6),
                "f32_1":  _read_f32(addr + 8),
                "f32_2":  _read_f32(addr + 12),
                "f32_3":  _read_f32(addr + 16),
            }

            base_hit = {
                "addr": addr,
                "dmg":  dmg,
                "fmt":  fmt_name,
                **_OPCODE_HIT_FIELDS,
                **extra,
            }

            if dmg in lookup:
                for key, mv in lookup[dmg]:
                    hits.append({**base_hit, "key": key, "move": mv})
            else:
                hits.append({**base_hit, "key": "?", "move": "Unknown"})


def _run_scan(active_keys, progress_cb, done_cb):
    if rbytes is None:
        done_cb([]); return

    proj_map = _load_map()
    id_map   = _load_ids()
    lookup   = _build_lookup(proj_map, active_keys)

    total = SCAN_END - SCAN_START
    hits  = []
    addr  = SCAN_START

    while addr < SCAN_END:
        sz = min(SCAN_BLOCK, SCAN_END - addr)
        try:
            data = rbytes(addr, sz)
        except Exception:
            data = b""

        if data:
            # --- opcode-scan pass (all SCRIPT_OPCODES signatures) ---
            _scan_opcode_blocks(data, addr, hits, lookup)

            # --- suffix-scan pass (template / template2 / script(0xNN)) ---
            pos = 0
            while True:
                idx = data.find(_SUFFIX, pos)
                if idx < 0:
                    break
                pos = idx + 1
                if idx < 4:
                    continue

                c = data[idx - 4:idx]
                if c[1]:
                    continue
                dmg = (c[2] << 8) | c[3]
                if not dmg:
                    continue

                after = data[idx + 4:idx + 8] if idx + 8 <= len(data) else b''
                fmt   = _fmt_for_suffix_block(c, after)
                a     = addr + idx - 4

                pre8     = data[idx - 8:idx - 4] if idx >= 8 else b''
                sig_keys = _keys_for_block(c, pre8)

                fields = {
                    "radius":      _read_f32(a + FIELD_OFFSETS["radius"]),
                    "aerial_kb_x": _read_f32(a + FIELD_OFFSETS["aerial_kb_x"]),
                    "type":        _read_u16(a + FIELD_OFFSETS["type"]),
                    "id":          _read_u16(a + FIELD_OFFSETS["id"]),
                    "lifetime":    _read_u16(a + FIELD_OFFSETS["lifetime"]),
                    "hb_size":     _read_u16(a + FIELD_OFFSETS["hb_size"]),
                    "speed":       _read_f32(a + FIELD_OFFSETS["speed"]),
                    "accel":       _read_f32(a + FIELD_OFFSETS["accel"]),
                    "hitbox":      _read_f32(a + FIELD_OFFSETS["hitbox"]),
                    "arc":         _read_f32(a + FIELD_OFFSETS["arc"]),
                    "arc2":        _read_f32(a + FIELD_OFFSETS["arc2"]),
                    "vel2_x":      _read_f32(a + FIELD_OFFSETS["vel2_x"]),
                    "vel2_y":      _read_f32(a + FIELD_OFFSETS["vel2_y"]),
                    "vel2_s":      _read_f32(a + FIELD_OFFSETS["vel2_s"]),
                    "u01":         _read_f32(a + FIELD_OFFSETS["u01"]),
                    "u02":         _read_f32(a + FIELD_OFFSETS["u02"]),
                    "u03":         _read_f32(a + FIELD_OFFSETS["u03"]),
                    "u04":         _read_u16(a + FIELD_OFFSETS["u04"]),
                    "u05":         _read_u16(a + FIELD_OFFSETS["u05"]),
                    "u06":         _read_u16(a + FIELD_OFFSETS["u06"]),
                    "u07":         _read_u16(a + FIELD_OFFSETS["u07"]),
                    "u08":         _read_u16(a + FIELD_OFFSETS["u08"]),
                    "u09":         _read_u16(a + FIELD_OFFSETS["u09"]),
                    # opcode-context columns are N/A for suffix-scan hits
                    "preA": "?", "preB": "?",
                    "opcode": "?", "param1": "?", "param2": "?", "param3": "?",
                    "f32_1": "?", "f32_2": "?", "f32_3": "?",
                }

                if dmg in lookup:
                    matches = lookup[dmg]

                    # Disambiguate by projectile ID when multiple chars share a damage value
                    if len({k for k, _ in matches}) > 1:
                        proj_id = fields.get("id")
                        try:
                            proj_id_int = int(proj_id)
                        except (TypeError, ValueError):
                            proj_id_int = None

                        if proj_id_int is not None:
                            id_matches = [
                                (k, mv) for k, mv in matches
                                if id_map.get(k, {}).get(mv) == proj_id_int
                            ]
                            if id_matches:
                                matches = id_matches

                    for key, mv in matches:
                        hits.append({"addr": a, "key": key, "move": mv,
                                     "dmg": dmg, "fmt": fmt, **fields})

                elif dmg >= 500:
                    hits.append({"addr": a, "key": "?", "move": "Unknown",
                                 "dmg": dmg, "fmt": fmt, **fields})

        progress_cb((addr - SCAN_START + sz) / total * 100.0)
        addr += sz

    _dump_hits(hits)
    done_cb(hits)


def _dump_hits(hits: list, context: int = 0x100):
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
                f.write(base.to_bytes(4, "big"))
                f.write(h["addr"].to_bytes(4, "big"))
                f.write(len(data).to_bytes(4, "big"))
                f.write(data)
        print(f"[proj_scanner] dumped {len(hits)} context block(s) to proj_dump.bin")
    except Exception as e:
        print(f"[proj_scanner] dump failed: {e}")


# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------

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
    ("preA",         "PreA",      None,          False),
    ("preB",         "PreB",      None,          False),
    ("opcode",       "Opcode",    None,          False),
    ("param1",       "Param1",    None,          False),
    ("param2",       "Param2",    None,          False),
    ("param3",       "Param3",    None,          False),
    ("f32_1",        "F32+8",     None,          True),
    ("f32_2",        "F32+C",     None,          True),
    ("f32_3",        "F32+10",    None,          True),
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

# Index of "fmt" column — used to read the format string from a row
_FMT_COL_IDX = _COL_IDS.index("fmt")


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

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
        for i in self._tree.get_children():
            self._tree.delete(i)
        self._status.set("Scanning MEM2...")
        threading.Thread(target=_run_scan,
            args=(set(self._keys), self._on_prog, self._on_done),
            daemon=True).start()

    def _sort_by(self, col_id):
        if self._sort_col == col_id:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col_id
            self._sort_asc = True

        col_map = {c[0]: c[1] for c in _COLS}
        for cid, header, _, _ in _COLS:
            if cid == col_id:
                arrow = " ▲" if self._sort_asc else " ▼"
                self._tree.heading(cid, text=header + arrow)
            else:
                self._tree.heading(cid, text=col_map[cid])

        items = [(self._tree.set(iid, col_id), iid)
                 for iid in self._tree.get_children("")]

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
            _TYPE_LABELS = {"3": "3:Linear", "4": "4:Physics",
                            3: "3:Linear",   4: "4:Physics"}
            for h in hits:
                type_str = _TYPE_LABELS.get(h["type"], str(h["type"]) if h["type"] is not None else "")
                iid = self._tree.insert("", "end", values=(
                    f"0x{h['addr']:08X}", h["key"], h["move"], h["dmg"],
                    h["radius"], h["speed"], h["accel"], h["aerial_kb_x"],
                    h["arc"], h["arc2"], h["hitbox"],
                    type_str, h["id"], h["lifetime"], h["hb_size"],
                    h.get("fmt", ""),
                    h.get("preA", "?"), h.get("preB", "?"),
                    h.get("opcode", "?"),
                    h.get("param1", "?"), h.get("param2", "?"), h.get("param3", "?"),
                    h.get("f32_1", "?"), h.get("f32_2", "?"), h.get("f32_3", "?"),
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

    def _fmt_for_iid(self, iid: str) -> str:
        vals = self._tree.item(iid, "values")
        return str(vals[_FMT_COL_IDX]) if len(vals) > _FMT_COL_IDX else ""

    def _on_right_click(self, event):
        iid = self._tree.identify_row(event.y)
        if not iid: return
        addr = self._addr_by_iid.get(iid)
        if addr is None: return

        col_idx = self._col_index(event)
        field_addr  = addr
        field_label = "base"
        if 0 <= col_idx < len(_COLS):
            col_id, header, fkey, _ = _COLS[col_idx]
            if fkey == "dmg":
                fmt = self._fmt_for_iid(iid)
                field_addr  = addr + _dmg_write_offset(fmt)
                field_label = "dmg"
            elif fkey and fkey in FIELD_OFFSETS:
                field_addr  = addr + FIELD_OFFSETS[fkey]
                field_label = header

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

        fmt = self._fmt_for_iid(iid)

        if fkey == "dmg":
            write_addr = addr + _dmg_write_offset(fmt)
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
                ok = _write_dmg(addr, ival, fmt)
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