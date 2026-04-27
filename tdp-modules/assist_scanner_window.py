from __future__ import annotations

import struct
import threading
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox

from tk_host import tk_call

try:
    from dolphin_io import rbytes, wbytes
except Exception:
    rbytes = None
    wbytes = None

SCAN_START = 0x90000000
SCAN_END = 0x94000000
SCAN_BLOCK = 0x40000

_CHR_TBL_BASES = [
    0x90896640,
    0x908F1920,
    0x909478E0,
    0x9099D9C0,
]
_FIGHTER_BASES = [
    0x9246B9C0,
    0x927EB9E0,
    0x92B6BA00,
    0x92EEBA20,
]
_CHAR_ID_OFF = 0x14

CHAR_ID_TO_KEY = {
    1: "Ken the Eagle", 2: "Casshan", 3: "Tekkaman", 4: "Polimar",
    5: "Yatterman-1", 6: "Doronjo", 7: "Ippatsuman", 8: "Jun the Swan",
    10: "Karas", 11: "Gold Lightan", 12: "Ryu", 13: "Chun-Li",
    14: "Batsu", 15: "Morrigan", 16: "Alex", 17: "Viewtiful Joe",
    18: "Volnutt", 19: "Roll", 20: "Saki", 21: "Soki", 22: "PTX-40A",
    23: "Yami", 24: "Yami", 25: "Yami", 26: "Tekkaman Blade",
    27: "Joe the Condor", 28: "Yatterman-2", 29: "Zero", 30: "Frank West",
}

# User-confirmed Ryu assist selector block:
#   0x908C7680: 00 03 11 0C 00 03 14 6C 00 03 1C DC 37 32 20 3F ...
# Confirmed effects when editing selector words:
#   0003110C = Hadouken assist path
#   0003146C = Shoryu assist path
#   00031CDC = Tatsu assist path
RYU_KNOWN_TARGETS = {
    0x0003110C: "Ryu Hadouken",
    0x0003146C: "Ryu Shoryu",
    0x00031CDC: "Ryu Tatsu",
}

RYU_PRESETS = [
    ("Hadouken", 0x0003110C),
    ("Shoryu",   0x0003146C),
    ("Tatsu",    0x00031CDC),
]

# The strict "take Ryu as bible" shape:
#   local_ref0 local_ref1 local_ref2 37 32 20 3F
# where each local_ref is 00 03 xx xx.
BIBLE_TAIL = b"\x37\x32\x20\x3F"
COMPANION_TAIL = b"\x37\x33\x20\x3F"

COLS = [
    ("kind", "Kind"),
    ("block", "Block"),
    ("owner", "Owner"),
    ("slot", "SlotCID"),
    ("entry", "Entry"),
    ("address", "Address"),
    ("raw", "RawU32"),
    ("target", "Resolved"),
    ("guess", "Guess"),
    ("score", "Score"),
    ("ctx", "Context"),
]
COL_IDS = [c[0] for c in COLS]


def _owning_chr_tbl(addr: int) -> int | None:
    best_base = None
    best_dist = None
    for base in _CHR_TBL_BASES:
        if addr < base:
            continue
        dist = addr - base
        if dist > 0x90000:
            continue
        if best_dist is None or dist < best_dist:
            best_base = base
            best_dist = dist
    return best_base


def _slot_index_for_base(base: int | None) -> int | None:
    if base is None:
        return None
    try:
        return _CHR_TBL_BASES.index(base)
    except ValueError:
        return None


def _read_slot_char_ids() -> dict[int, int]:
    result: dict[int, int] = {}
    if rbytes is None:
        return result
    for idx, chr_base in enumerate(_CHR_TBL_BASES):
        fighter_base = _FIGHTER_BASES[idx]
        try:
            b = rbytes(fighter_base + _CHAR_ID_OFF, 4)
            if b and len(b) == 4:
                result[chr_base] = struct.unpack(">I", b)[0]
        except Exception:
            pass
    return result


def _owner_name(addr: int, slot_char_ids: dict[int, int]) -> str:
    base = _owning_chr_tbl(addr)
    if base is None:
        return "?"
    cid = slot_char_ids.get(base)
    name = CHAR_ID_TO_KEY.get(cid, "?") if cid is not None else "?"
    idx = _slot_index_for_base(base)
    slot = f"S{idx}" if idx is not None else "S?"
    return f"{slot} {name} @0x{base:08X}"


def _slot_cid(addr: int, slot_char_ids: dict[int, int]) -> str:
    base = _owning_chr_tbl(addr)
    if base is None:
        return "?"
    cid = slot_char_ids.get(base)
    if cid is None:
        return "?"
    return f"0x{cid:02X}"


def _fmt_context(data: bytes, local: int, radius: int = 20) -> str:
    start = max(0, local - radius)
    end = min(len(data), local + radius + 24)
    parts: list[str] = []
    for i in range(start, end):
        b = data[i]
        if i == local:
            parts.append(f"[{b:02X}")
        elif i == local + 3:
            parts.append(f"{b:02X}]")
        else:
            parts.append(f"{b:02X}")
    return " ".join(parts)


def _u32be(data: bytes, off: int) -> int | None:
    if off < 0 or off + 4 > len(data):
        return None
    return struct.unpack_from(">I", data, off)[0]


def _looks_like_bible_block(data: bytes, idx: int) -> bool:
    # idx points at first local ref. Need 3 x u32 + 37 32 20 3F.
    if idx < 0 or idx + 16 > len(data):
        return False
    if data[idx + 12:idx + 16] != BIBLE_TAIL:
        return False
    # Ryu bible form: 00 03 xx xx repeated 3 times.
    for off in (0, 4, 8):
        if data[idx + off] != 0x00 or data[idx + off + 1] != 0x03:
            return False
    return True


def _score_block(data: bytes, idx: int) -> int:
    score = 50
    # Companion 37 33 nearby is a strong match to Ryu area.
    if idx + 24 <= len(data) and data[idx + 20:idx + 24] == COMPANION_TAIL:
        score += 25
    window = data[idx: min(len(data), idx + 0x120)]
    for phrase in (b"\x04\x01\x60\x00", b"\x04\x17\x60\x00", b"\x33\x03\x20\x3F", b"\x04\x0C\x02\x3F"):
        if phrase in window:
            score += 5
    return min(score, 100)


def _append_block_hits(data: bytes, base_addr: int, idx: int, slot_char_ids: dict[int, int], hits: list[dict]) -> None:
    block_addr = base_addr + idx
    owner_base = _owning_chr_tbl(block_addr)
    if owner_base is None:
        return
    owner = _owner_name(block_addr, slot_char_ids)
    score = _score_block(data, idx)

    # Add a header row for the block.
    hits.append({
        "kind": "bible-block",
        "block": block_addr,
        "addr": block_addr,
        "owner": owner,
        "slot": _slot_cid(block_addr, slot_char_ids),
        "entry": "block",
        "raw": data[idx:idx + 16].hex(" ").upper(),
        "target": "",
        "guess": "Ryu-shape assist selector candidate",
        "score": score,
        "ctx": _fmt_context(data, idx),
        "editable": False,
        "typ": "raw16",
    })

    for n, off in enumerate((0, 4, 8), start=1):
        raw = _u32be(data, idx + off)
        if raw is None:
            continue
        addr = block_addr + off
        resolved = owner_base + raw
        guess = RYU_KNOWN_TARGETS.get(raw, "")
        if not guess and 0x90000000 <= resolved < 0x94000000:
            guess = "local ref"
        hits.append({
            "kind": "selector",
            "block": block_addr,
            "addr": addr,
            "owner": owner,
            "slot": _slot_cid(block_addr, slot_char_ids),
            "entry": f"target {n}",
            "raw": f"0x{raw:08X}",
            "target": f"0x{resolved:08X}",
            "guess": guess,
            "score": score,
            "ctx": _fmt_context(data, idx + off),
            "editable": True,
            "typ": "u32",
        })


def _scan_block(data: bytes, base_addr: int, slot_char_ids: dict[int, int], hits: list[dict]) -> None:
    pos = 0
    while True:
        idx = data.find(BIBLE_TAIL, pos)
        if idx < 0:
            break
        pos = idx + 1
        start = idx - 12
        if _looks_like_bible_block(data, start):
            _append_block_hits(data, base_addr, start, slot_char_ids, hits)


def _run_scan(progress_cb, done_cb):
    if rbytes is None:
        done_cb([])
        return
    slot_char_ids = _read_slot_char_ids()
    hits: list[dict] = []
    total = SCAN_END - SCAN_START
    addr = SCAN_START
    while addr < SCAN_END:
        sz = min(SCAN_BLOCK, SCAN_END - addr)
        try:
            data = rbytes(addr, sz) or b""
        except Exception:
            data = b""
        if data:
            _scan_block(data, addr, slot_char_ids, hits)
        progress_cb((addr - SCAN_START + sz) / total * 100.0)
        addr += sz

    seen = set()
    uniq = []
    for h in hits:
        k = (h["kind"], h["addr"], h["block"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(h)
    uniq.sort(key=lambda h: (0 if h["block"] == 0x908C7680 else 1, h["block"], h["addr"]))
    done_cb(uniq)


class AssistScannerWindow:
    def __init__(self, master):
        self.root = tk.Toplevel(master)
        self.root.title("Assist Scanner - Ryu Bible")
        self.root.geometry("1360x650")
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)
        self._scanning = False
        self._hit_by_iid: dict[str, dict] = {}
        self._sort_col = None
        self._sort_asc = True
        self._build()
        self._start()

    def _build(self):
        top = ttk.Frame(self.root)
        top.pack(side="top", fill="x", padx=8, pady=6)
        ttk.Label(
            top,
            text="Scans Ryu assist selector shape: 00 03 xx xx / 00 03 xx xx / 00 03 xx xx / 37 32 20 3F. Double-click RawU32 selector rows to poke."
        ).pack(side="left")
        self._scan_btn = ttk.Button(top, text="Rescan", command=self._start)
        self._scan_btn.pack(side="right")

        self._prog = tk.DoubleVar()
        ttk.Progressbar(self.root, variable=self._prog, maximum=100).pack(fill="x", padx=8, pady=(0, 4))
        self._status = tk.StringVar(value="Ready")
        ttk.Label(self.root, textvariable=self._status, anchor="w").pack(fill="x", padx=8)

        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=8, pady=6)
        self._tree = ttk.Treeview(frame, columns=COL_IDS, show="headings", height=26)
        widths = {
            "kind": 95, "block": 110, "owner": 210, "slot": 70, "entry": 80,
            "address": 110, "raw": 130, "target": 120, "guess": 190,
            "score": 70, "ctx": 650,
        }
        for col_id, header in COLS:
            self._tree.heading(col_id, text=header, command=lambda c=col_id: self._sort_by(c))
            self._tree.column(col_id, width=widths.get(col_id, 80), anchor="center")
        for c in ("owner", "guess", "ctx"):
            self._tree.column(c, anchor="w")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        self._tree.bind("<Double-Button-1>", self._on_double_click)
        self._tree.bind("<Button-3>", self._on_right_click)

    def _start(self):
        if self._scanning:
            return
        self._scanning = True
        self._scan_btn.config(state="disabled")
        self._prog.set(0)
        self._hit_by_iid.clear()
        for iid in self._tree.get_children():
            self._tree.delete(iid)
        self._status.set("Scanning MEM2 for Ryu-bible assist selector candidates...")
        threading.Thread(target=_run_scan, args=(self._on_prog, self._on_done), daemon=True).start()

    def _on_prog(self, pct: float):
        try:
            self.root.after(0, lambda: self._prog.set(pct))
        except Exception:
            pass

    def _on_done(self, hits: list[dict]):
        def _f():
            for h in hits:
                iid = self._tree.insert("", "end", values=(
                    h["kind"],
                    f"0x{h['block']:08X}",
                    h["owner"],
                    h["slot"],
                    h["entry"],
                    f"0x{h['addr']:08X}",
                    h["raw"],
                    h["target"],
                    h["guess"],
                    h["score"],
                    h["ctx"],
                ))
                self._hit_by_iid[iid] = h
            self._scanning = False
            self._scan_btn.config(state="normal")
            self._prog.set(100)
            blocks = len({h["block"] for h in hits if h["kind"] == "bible-block"})
            self._status.set(f"Done - {blocks} bible-shaped block(s), {len(hits)} row(s). Confirmed Ryu block should be 0x908C7680.")
        try:
            self.root.after(0, _f)
        except Exception:
            pass

    def _sort_by(self, col_id: str):
        if self._sort_col == col_id:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col_id
            self._sort_asc = True
        headers = dict(COLS)
        for cid, header in COLS:
            arrow = (" up" if self._sort_asc else " down") if cid == col_id else ""
            self._tree.heading(cid, text=header + arrow)
        items = [(self._tree.set(iid, col_id), iid) for iid in self._tree.get_children("")]
        def key(v):
            s = str(v)
            try:
                if s.startswith("0x"):
                    return (0, int(s, 16))
                return (0, float(s))
            except Exception:
                return (1, s.lower())
        items.sort(key=lambda x: key(x[0]), reverse=not self._sort_asc)
        for n, (_, iid) in enumerate(items):
            self._tree.move(iid, "", n)

    def _col_index(self, event) -> int:
        col = self._tree.identify_column(event.x)
        return int(col[1:]) - 1 if col else -1

    def _choose_ryu_preset_or_manual(self, addr: int, current: str) -> int | None:
        result = {"value": None}
        dlg = tk.Toplevel(self.root)
        dlg.title("Choose Ryu Assist Selector")
        dlg.geometry("360x250")
        dlg.transient(self.root)
        dlg.grab_set()

        ttk.Label(
            dlg,
            text=(
                f"Address: 0x{addr:08X}\n"
                f"Current: {current}\n\n"
                "Choose a preset, or use Manual for a raw U32."
            ),
            justify="left",
        ).pack(anchor="w", padx=12, pady=(12, 8))

        btn_frame = ttk.Frame(dlg)
        btn_frame.pack(fill="x", padx=12, pady=4)

        def set_value(v: int):
            result["value"] = v
            dlg.destroy()

        for label, value in RYU_PRESETS:
            ttk.Button(
                btn_frame,
                text=f"{label}  0x{value:08X}",
                command=lambda v=value: set_value(v),
            ).pack(fill="x", pady=3)

        def manual():
            text = simpledialog.askstring(
                "Manual Ryu Selector",
                "Enter raw U32 value. Examples:\n"
                "0x0003110C = Hadouken\n"
                "0x0003146C = Shoryu\n"
                "0x00031CDC = Tatsu",
                parent=dlg,
                initialvalue=current,
            )
            if text is None:
                return
            try:
                cleaned = text.strip().replace(" ", "")
                value = int(cleaned, 16) if cleaned.lower().startswith("0x") else int(
                    cleaned,
                    16 if all(c in "0123456789abcdefABCDEF" for c in cleaned) and len(cleaned) > 6 else 10,
                )
            except ValueError:
                messagebox.showerror("Invalid", f"{text!r} is not a u32 value.", parent=dlg)
                return
            if not (0 <= value <= 0xFFFFFFFF):
                messagebox.showerror("Out of range", "Value must be 0-0xFFFFFFFF.", parent=dlg)
                return
            set_value(value)

        ttk.Button(btn_frame, text="Manual raw U32", command=manual).pack(fill="x", pady=(10, 3))
        ttk.Button(btn_frame, text="Cancel", command=dlg.destroy).pack(fill="x", pady=3)

        self.root.wait_window(dlg)
        return result["value"]

    def _on_double_click(self, event):
        iid = self._tree.identify_row(event.y)
        col_idx = self._col_index(event)
        if not iid or col_idx < 0:
            return
        col_id = COL_IDS[col_idx]
        if col_id != "raw":
            return
        h = self._hit_by_iid.get(iid)
        if not h or not h.get("editable"):
            return
        addr = int(h["addr"])
        cur = str(h["raw"])

        val = self._choose_ryu_preset_or_manual(addr, cur)
        if val is None:
            return

        if wbytes is None:
            messagebox.showerror("Write failed", "dolphin_io.wbytes unavailable.", parent=self.root)
            return
        try:
            ok = bool(wbytes(addr, struct.pack(">I", val)))
        except Exception as e:
            messagebox.showerror("Write failed", str(e), parent=self.root)
            return
        if not ok:
            messagebox.showerror("Write failed", "Could not write to Dolphin.", parent=self.root)
            return
        owner_base = _owning_chr_tbl(addr)
        resolved = (owner_base + val) if owner_base is not None else 0
        guess = RYU_KNOWN_TARGETS.get(val, "manual/local ref")
        self._tree.set(iid, "raw", f"0x{val:08X}")
        self._tree.set(iid, "target", f"0x{resolved:08X}" if resolved else "?")
        self._tree.set(iid, "guess", guess)
        h["raw"] = f"0x{val:08X}"
        h["target"] = f"0x{resolved:08X}" if resolved else "?"
        h["guess"] = guess
        self._status.set(f"Wrote {guess} / 0x{val:08X} to 0x{addr:08X}")

    def _on_right_click(self, event):
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        h = self._hit_by_iid.get(iid)
        if not h:
            return
        addr = int(h["addr"])
        block = int(h["block"])
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"Copy address 0x{addr:08X}", command=lambda: self._copy(f"0x{addr:08X}"))
        menu.add_command(label=f"Copy block 0x{block:08X}", command=lambda: self._copy(f"0x{block:08X}"))
        menu.add_command(label=f"Go to address 0x{addr:08X}", command=lambda: self._show_address_info(addr))
        menu.add_command(label=f"Go to block 0x{block:08X}", command=lambda: self._show_address_info(block))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._status.set(f"Copied {text}")

    def _show_address_info(self, addr: int):
        if rbytes is None:
            messagebox.showerror("Address", "dolphin_io.rbytes unavailable", parent=self.root)
            return
        line_size = 16
        line_base = addr & ~(line_size - 1)
        start = max(SCAN_START, line_base - 8 * line_size)
        size = 17 * line_size
        try:
            data = rbytes(start, size) or b""
        except Exception as e:
            messagebox.showerror("Address", f"Read failed: {e}", parent=self.root)
            return
        dlg = tk.Toplevel(self.root)
        dlg.title(f"Assist bytes @ 0x{addr:08X}")
        dlg.geometry("820x460")
        txt = tk.Text(dlg, wrap="none", font=("Consolas", 10), bg="#101214", fg="#E8E8E8")
        txt.pack(fill="both", expand=True, padx=8, pady=8)
        current_line = (line_base - start) // line_size
        for i in range(17):
            off = i * line_size
            chunk = data[off:off + line_size]
            a = start + off
            hx = " ".join(f"{b:02X}" for b in chunk)
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            prefix = ">>" if i == current_line else "  "
            txt.insert("end", f"{prefix} 0x{a:08X}: {hx:<47} {asc}\n")
        txt.config(state="disabled")


_inst = None


def open_assist_scanner_window():
    def _c(master):
        global _inst
        if _inst:
            try:
                _inst.root.lift()
                return
            except Exception:
                pass
        _inst = AssistScannerWindow(master)
    tk_call(_c)
