# gui_hitbox_probe.py
#

#
# This script expects to be run from that root (or adjust TABLE_DIR).

import os
import re
import tkinter as tk
from tkinter import ttk, messagebox

print("[gui] starting…")

HAVE_DOLPHIN = True
try:
    from dolphin_io import hook, rbytes
    import dolphin_memory_engine as dme
    from constants import MEM2_LO, MEM2_HI
    print("[gui] dolphin modules imported")
except Exception as e:
    print("[gui] dolphin import FAILED:", e)
    HAVE_DOLPHIN = False
    MEM2_LO = 0x90000000
    MEM2_HI = 0x94000000

# ------------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------------
TABLE_DIR = os.path.join(os.path.dirname(__file__), "tables")

# all tables we capture start 0x10 before tail
SAMPLE_TAIL_REL = 0x10
SCAN_MATCH_LEN = 0x40

ANCHOR_REL_FROM_TAIL = -0xB0
ANCHOR_BYTES = b"\xFF\xFF\xFF\xFE"

CAPTURE_SIZE = 0x2000  # 8KB

TAIL_PATTERN = b"\x00\x00\x00\x38\x01\x33\x00\x00"

HEX_PAIR_RE = re.compile(r"^[0-9A-Fa-f]{2}$")


# ------------------------------------------------------------------
# low-level helpers
# ------------------------------------------------------------------
def read_mem2() -> bytes:
    if not HAVE_DOLPHIN:
        return b""
    # reads whole MEM2 (0x40_00000 on Wii, ~64MB) — we will try to do this sparingly
    return rbytes(MEM2_LO, MEM2_HI - MEM2_LO)


def read2(addr: int):
    if not HAVE_DOLPHIN:
        return None
    raw = rbytes(addr, 2)
    if not raw or len(raw) != 2:
        return None
    return raw


def write_hexdump(filename: str, base_addr: int, data: bytes):
    with open(filename, "w", encoding="utf-8") as f:
        addr = base_addr
        for i in range(0, len(data), 16):
            chunk = data[i:i+16]
            hexbytes = " ".join(f"{b:02X}" for b in chunk)
            f.write(f"0x{addr:08X}: {hexbytes}\n")
            addr += len(chunk)


# ------------------------------------------------------------------
# parsers
# ------------------------------------------------------------------
def _try_parse_text_hexdump(data: str) -> bytes | None:
    out = bytearray()
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^(0x)?[0-9A-Fa-f]+:\s*", "", line)
        for p in line.split():
            if HEX_PAIR_RE.match(p):
                out.append(int(p, 16))
    return bytes(out) if out else None


def load_table_sample(path: str) -> bytes | None:
    with open(path, "rb") as f:
        raw = f.read()
    # maybe text
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError:
        # raw binary
        return raw
    parsed = _try_parse_text_hexdump(txt)
    if parsed:
        return parsed
    return raw


def parse_hex_or_int(s: str) -> int:
    s = s.strip()
    if s.startswith("-0x") or s.startswith("-0X"):
        return -int(s[3:], 16)
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16)
    return int(s, 0)


def load_moves_file(path: str):
    moves = []
    if not os.path.exists(path):
        return moves
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, off = line.split("=", 1)
            name = name.strip()
            off = off.strip()
            try:
                rel = parse_hex_or_int(off)
            except Exception:
                continue
            moves.append((name, rel))
    return moves


# ------------------------------------------------------------------
# matching
# ------------------------------------------------------------------
def find_sample_in_mem(mem: bytes, sample: bytes, match_len: int) -> int | None:
    if not sample:
        return None
    if match_len > len(sample):
        match_len = len(sample)
    sig = sample[:match_len]
    off = mem.find(sig)
    return off if off != -1 else None


def find_all_tails(mem: bytes, pattern: bytes) -> list[int]:
    offs = []
    off = 0
    while True:
        idx = mem.find(pattern, off)
        if idx == -1:
            break
        offs.append(idx)
        off = idx + 1
    return offs


# ------------------------------------------------------------------
# GUI
# ------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TvC table probe (multi-character)")
        self.geometry("950x540")

        # top bar
        top = ttk.Frame(self); top.pack(fill=tk.X, padx=6, pady=4)
        ttk.Label(top, text="Capture to:").pack(side=tk.LEFT)
        self.capture_name = tk.StringVar(value="capture.txt")
        ttk.Entry(top, textvariable=self.capture_name, width=30).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Capture current table", command=self.capture_current).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="Capture prev table", command=self.capture_prev).pack(side=tk.LEFT, padx=4)
        # show which character matched
        self.matched_label = ttk.Label(top, text="no match")
        self.matched_label.pack(side=tk.LEFT, padx=8)

        # scrollable move area
        outer = ttk.Frame(self); outer.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(outer)
        scr = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self.inner = ttk.Frame(canvas)
        self.inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=scr.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scr.pack(side=tk.RIGHT, fill=tk.Y)

        # load all table samples in advance
        self.table_samples = self.load_all_table_samples(TABLE_DIR)  # list of (name, sample)
        print(f"[gui] loaded {len(self.table_samples)} table snapshots from {TABLE_DIR}")

        # runtime state
        self.rows = []              # GUI rows
        self.current_moves = []     # moves for current matched char
        self.current_char = None    # name like "ryu" or "chun"
        self.latched_tail = None    # absolute MEM2 addr
        self.mem_cache = None
        self.tail_list = []

        # first UI build (empty)
        self.rebuild_rows([])

        self.after(400, self.tick)

    # ------------------------------------------------------
    # load all bins from tables/
    # ------------------------------------------------------
    def load_all_table_samples(self, directory: str):
        samples = []
        if not os.path.isdir(directory):
            return samples
        for fname in os.listdir(directory):
            if not fname.lower().endswith(".bin") and not fname.lower().endswith(".txt"):
                continue
            path = os.path.join(directory, fname)
            try:
                sample = load_table_sample(path)
            except Exception as e:
                print(f"[gui] failed to load {path}: {e}")
                continue
            base, _ = os.path.splitext(fname)
            samples.append((base, sample))
        # try longer samples first — more specific
        samples.sort(key=lambda t: len(t[1]), reverse=True)
        return samples

    # ------------------------------------------------------
    # rebuild move rows (when we switch character)
    # ------------------------------------------------------
    def rebuild_rows(self, moves: list[tuple[str, int]]):
        # clear
        for child in self.inner.winfo_children():
            child.destroy()
        self.rows = []
        for name, rel in moves:
            row = ttk.Frame(self.inner); row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=name, width=32, anchor="w").pack(side=tk.LEFT)
            addr_var = tk.StringVar(value="—")
            val_var = tk.StringVar(value="—")
            tk.Label(row, textvariable=addr_var, width=14).pack(side=tk.LEFT)
            tk.Label(row, textvariable=val_var, width=10).pack(side=tk.LEFT)
            self.rows.append((name, rel, addr_var, val_var))

    # ------------------------------------------------------
    # anchor check
    # ------------------------------------------------------
    def anchor_ok(self) -> bool:
        if self.latched_tail is None or self.mem_cache is None:
            return False
        anchor_abs = self.latched_tail + ANCHOR_REL_FROM_TAIL
        off = anchor_abs - MEM2_LO
        if off < 0 or off + 4 > len(self.mem_cache):
            return False
        return self.mem_cache[off:off+4] == ANCHOR_BYTES

    # ------------------------------------------------------
    # capture current (ASCII)
    # ------------------------------------------------------
    def capture_current(self):
        if self.latched_tail is None or self.mem_cache is None:
            messagebox.showerror("Capture", "No table latched to capture.")
            return
        start_abs = self.latched_tail - SAMPLE_TAIL_REL
        start_off = start_abs - MEM2_LO
        if start_off < 0:
            start_off = 0
            start_abs = MEM2_LO
        end_off = min(start_off + CAPTURE_SIZE, len(self.mem_cache))
        blob = self.mem_cache[start_off:end_off]
        outname = self.capture_name.get().strip() or "capture.txt"
        if not outname.lower().endswith(".txt"):
            outname += ".txt"
        try:
            write_hexdump(outname, start_abs, blob)
            messagebox.showinfo("Capture", f"Captured {len(blob)} bytes (hexdump) to {outname}")
        except Exception as e:
            messagebox.showerror("Capture", f"Failed to write {outname}: {e}")

    # ------------------------------------------------------
    # capture previous (ASCII) using tail list
    # ------------------------------------------------------
    def capture_prev(self):
        if self.latched_tail is None or self.mem_cache is None or not self.tail_list:
            messagebox.showerror("Capture", "No table / tail info to capture from.")
            return

        cur_off = self.latched_tail - MEM2_LO
        prev_off = None
        for off in self.tail_list:
            if off < cur_off:
                prev_off = off
            else:
                break

        if prev_off is None:
            messagebox.showerror("Capture", "No previous tail found.")
            return

        start_off = max(0, prev_off - SAMPLE_TAIL_REL)
        start_abs = MEM2_LO + start_off
        end_off = min(start_off + CAPTURE_SIZE, len(self.mem_cache))
        blob = self.mem_cache[start_off:end_off]

        outname = self.capture_name.get().strip() or "capture_prev.txt"
        if not outname.lower().endswith(".txt"):
            outname += ".txt"
        try:
            write_hexdump(outname, start_abs, blob)
            messagebox.showinfo("Capture", f"Captured {len(blob)} bytes (hexdump) to {outname}")
        except Exception as e:
            messagebox.showerror("Capture", f"Failed to write {outname}: {e}")

    # ------------------------------------------------------
    # periodic tick
    # ------------------------------------------------------
    def tick(self):
        # if we don't have a latch OR we lost the anchor -> rescan = read full MEM2
        need_full_read = False
        if self.latched_tail is None:
            need_full_read = True

        # if we do have a latch, check anchor using current cache
        if self.latched_tail is not None:
            if self.mem_cache is None:
                need_full_read = True
            else:
                if not self.anchor_ok():
                    # table moved (tag) -> we need to rescan
                    print("[gui] anchor moved -> rescanning")
                    self.latched_tail = None
                    self.current_char = None
                    self.rebuild_rows([])
                    self.matched_label.config(text="no match")
                    need_full_read = True

        if need_full_read:
            self.mem_cache = read_mem2() if HAVE_DOLPHIN else None

        # if we refreshed mem, also rebuild tail list
        if self.mem_cache is not None and need_full_read:
            tails = find_all_tails(self.mem_cache, TAIL_PATTERN)
            tails.sort()
            self.tail_list = tails

        # if we still have no latch, try every table sample
        if self.latched_tail is None and self.mem_cache is not None:
            for base, sample in self.table_samples:
                off = find_sample_in_mem(self.mem_cache, sample, SCAN_MATCH_LEN)
                if off is not None:
                    tail_off = off + SAMPLE_TAIL_REL
                    self.latched_tail = MEM2_LO + tail_off
                    self.current_char = base
                    print(f"[gui] matched {base} at tail 0x{self.latched_tail:08X}")
                    # load its moves
                    moves_path = os.path.join(TABLE_DIR, f"{base}.moves")
                    self.current_moves = load_moves_file(moves_path)
                    self.rebuild_rows(self.current_moves)
                    self.matched_label.config(text=f"matched: {base}")
                    break

        # update rows
        for name, rel, addr_var, val_var in self.rows:
            if self.latched_tail and HAVE_DOLPHIN:
                addr = self.latched_tail + rel
                addr_var.set(f"0x{addr:08X}")
                raw = read2(addr)
                if raw:
                    val_var.set(f"{raw[0]:02X} {raw[1]:02X}")
                else:
                    val_var.set("ERR")
            else:
                addr_var.set("—")
                val_var.set("—")

        self.after(500, self.tick)


if __name__ == "__main__":
    if HAVE_DOLPHIN:
        print("[gui] hooking dolphin…")
        try:
            hook()
            print("[gui] hooked.")
        except Exception as e:
            print("[gui] hook FAILED:", e)
    else:
        print("[gui] running without dolphin")

    app = App()
    app.mainloop()
