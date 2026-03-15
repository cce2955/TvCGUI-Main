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

_SUFFIX = b"\x00\x00\x00\x0C"
SCAN_START = 0x90800000
SCAN_END   = 0x90A00000
SCAN_BLOCK = 0x40000
PROJ_MAP_FILE = "projectilemap.json"

# Offsets relative to damage_addr for the three unknown fields after the FF block
# +0x74 = start of FF block, fields follow after
FIELD_OFFSETS = {
    "speed": 0x80,  # confirmed speed (float)
    "accel": 0x84,  # confirmed acceleration (float)
    "arc":   0x90,  # confirmed arc/gravity (float)
    # unknowns — numbered for investigation
    "u01": 0x10,   # float: -3.0 on Roll Splash
    "u02": 0x14,   # float:  9.0 on Roll Splash
    "u03": 0x18,   # float:  1.0 on Roll Splash
    "u04": 0x42,   # u16:   10  on Roll Splash
    "u05": 0x48,   # u16: 1001  on Roll Splash
    "u06": 0x52,   # u8:    30  on Roll Splash (puddle lifetime?)
    "u07": 0x5A,   # u8:   215  on Roll Splash
    "u08": 0x68,   # u16: 1024  on Roll Splash
    "u09": 0x72,   # u16:   50  on Roll Splash
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

def _load_map():
    try:
        with open(PROJ_MAP_FILE, encoding="utf-8") as f:
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

def _has_strength_prefix(name: str) -> bool:
    return bool(_re.search(r'\b[LMHlmh]\b', name) or
                _re.search(r'\([LMHlmh]\)', name) or
                name.rstrip().endswith((' L', ' M', ' H')))

def _apply_tiers(hits):
    from collections import Counter
    counts = Counter((h["key"], h["move"]) for h in hits)
    seen = {}
    result = []
    for h in hits:
        group = (h["key"], h["move"])
        total = counts[group]
        if total == 1:
            result.append(h); continue
        idx = seen.get(group, 0)
        seen[group] = idx + 1
        name = h["move"]
        if _has_strength_prefix(name):
            if total == 2:
                new_name = ("j." + name) if idx == 1 else name
            elif total >= 3:
                if idx == 0: new_name = name
                elif idx == 1: new_name = name + " Assist"
                else: new_name = "j." + name
            else:
                new_name = name
        else:
            tiers = ["L", "M", "H", "Assist", "j.L", "j.M", "j.H"]
            tier  = tiers[idx] if idx < len(tiers) else str(idx)
            new_name = f"{name} {tier}"
        result.append(dict(h, move=new_name))
    return result

def _run_scan(active_keys, slot_order, progress_cb, done_cb):
    if rbytes is None:
        done_cb([]); return
    lookup = _build_lookup(_load_map(), active_keys)
    if not lookup:
        done_cb([]); return
    total = SCAN_END - SCAN_START
    hits  = []
    addr  = SCAN_START
    while addr < SCAN_END:
        sz = min(SCAN_BLOCK, SCAN_END - addr)
        try: data = rbytes(addr, sz)
        except: data = b""
        if data:
            pos = 0
            while True:
                idx = data.find(_SUFFIX, pos)
                if idx < 0: break
                pos = idx + 1
                if idx < 4: continue
                c = data[idx-4:idx]
                if c[0] or c[1]: continue
                dmg = (c[2] << 8) | c[3]
                if not dmg or dmg not in lookup: continue
                a = addr + idx - 4
                fields = {
                    "speed": _read_f32(a + FIELD_OFFSETS["speed"]),
                    "accel": _read_f32(a + FIELD_OFFSETS["accel"]),
                    "arc":   _read_f32(a + FIELD_OFFSETS["arc"]),
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
                matches = lookup[dmg]
                # For shared damage values, pick char that comes first in slot order
                keys_in_matches = {k for k, _ in matches}
                if len(keys_in_matches) > 1:
                    for slot_key in slot_order:
                        if slot_key in keys_in_matches:
                            matches = [(k, mv) for k, mv in matches if k == slot_key]
                            break
                for key, mv in matches:
                    hits.append({"addr": a, "key": key, "move": mv, "dmg": dmg, **fields})
        progress_cb((addr - SCAN_START + sz) / total * 100.0)
        addr += sz
    done_cb(_apply_tiers(hits))

def _write_dmg(addr: int, new_dmg: int) -> bool:
    if wbytes is None:
        return False
    try:
        return bool(wbytes(addr + 2, bytes([(new_dmg >> 8) & 0xFF, new_dmg & 0xFF])))
    except Exception as e:
        print(f"[proj_scanner] write dmg failed: {e}")
        return False

# column index -> (label, field_key, addr_offset, is_float)
# (col_id, header, field_key, is_float)
_COLS = [
    ("address", "Address",  None,    False),
    ("char",    "Char",     None,    False),
    ("move",    "Move",     None,    False),
    ("dmg",     "Damage",   "dmg",   False),
    ("speed",   "Speed",    "speed", True),
    ("accel",   "Accel",    "accel", True),
    ("arc",     "Arc",      "arc",   True),
    ("u01",     "?? 01",    "u01",   True),
    ("u02",     "?? 02",    "u02",   True),
    ("u03",     "?? 03",    "u03",   True),
    ("u04",     "?? 04",        "u04",   False),
    ("u05",     "Spawn Ctrl",   "u05",   False),
    ("u06",     "Hitbox Reach", "u06",   False),
    ("u07",     "?? 07",        "u07",   False),
    ("u08",     "?? 08",        "u08",   False),
    ("u09",     "?? 09",        "u09",   False),
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
        for col_id, header, _, _ in _COLS:
            self._tree.heading(col_id, text=header)
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
        names = self._get_active()  # ordered list by slot
        self._keys = {_NAME_TO_KEY[n] for n in names if n in _NAME_TO_KEY}
        slot_order = [_NAME_TO_KEY[n] for n in names if n in _NAME_TO_KEY]
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
            args=(set(self._keys), slot_order, self._on_prog, self._on_done),
            daemon=True).start()

    def _on_prog(self, pct):
        try: self.root.after(0, lambda: self._prog.set(pct))
        except: pass

    def _on_done(self, hits):
        def _f():
            for h in hits:
                iid = self._tree.insert("", "end", values=(
                    f"0x{h['addr']:08X}", h["key"], h["move"], h["dmg"],
                    h["speed"], h["accel"], h["arc"],
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

        if fkey == "dmg":
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