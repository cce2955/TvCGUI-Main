# gui_hitbox_probe.py
#
# Data-driven version:
# - character table snapshot: ryu.bin
# - character move list:      ryu.moves
# change those two filenames to do Chun, Ken, etc.

import os, re, tkinter as tk
from tkinter import ttk

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
# CONFIG: change these two to switch character
# ------------------------------------------------------------------
TABLE_FILE = "ryu.bin"
MOVES_FILE = "ryu.moves"

# in your dump: sample at 0x908AF004, tail at 0x908AF014
SAMPLE_TAIL_REL = 0x10
SCAN_MATCH_LEN = 0x40

# anchor: tail - 0xB0 = FF FF FF FE
ANCHOR_REL_FROM_TAIL = -0xB0
ANCHOR_BYTES = b"\xFF\xFF\xFF\xFE"

HEX_PAIR_RE = re.compile(r"^[0-9A-Fa-f]{2}$")

# ------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------
def read_mem2() -> bytes:
    if not HAVE_DOLPHIN:
        return b""
    return rbytes(MEM2_LO, MEM2_HI - MEM2_LO)

def parse_hex_or_int(s: str) -> int:
    s = s.strip()
    # allow negative hex like -0x10
    if s.startswith("-0x") or s.startswith("-0X"):
        return -int(s[3:], 16)
    if s.startswith("0x") or s.startswith("0X"):
        return int(s, 16)
    return int(s, 0)

def load_moves(path: str):
    moves = []
    if not os.path.exists(path):
        print(f"[gui] moves file not found: {path}")
        return moves
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            name, off = line.split("=", 1)
            name = name.strip()
            off = off.strip()
            try:
                rel = parse_hex_or_int(off)
            except Exception:
                print(f"[gui] bad offset in {path}: {line}")
                continue
            moves.append((name, rel))
    print(f"[gui] loaded {len(moves)} moves from {path}")
    return moves

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
    if not os.path.exists(path):
        print(f"[gui] table file not found: {path}")
        return None
    raw = open(path, "rb").read()
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError:
        print(f"[gui] loaded {path} as raw bytes ({len(raw)} bytes)")
        return raw
    parsed = _try_parse_text_hexdump(txt)
    if parsed:
        print(f"[gui] loaded {path} as text hexdump -> {len(parsed)} bytes")
        return parsed
    print(f"[gui] loaded {path} as text but no bytes parsed, using raw ({len(raw)} bytes)")
    return raw

def find_sample_in_mem(mem: bytes, sample: bytes, match_len: int) -> int | None:
    if not sample:
        return None
    if match_len > len(sample):
        match_len = len(sample)
    sig = sample[:match_len]
    off = mem.find(sig)
    return off if off != -1 else None

def read2(addr: int):
    if not HAVE_DOLPHIN:
        return None
    raw = rbytes(addr, 2)
    if not raw or len(raw) != 2:
        return None
    return raw

# ------------------------------------------------------------------
# GUI
# ------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"TvC table probe ({TABLE_FILE} / {MOVES_FILE})")
        self.geometry("850x500")

        outer = ttk.Frame(self); outer.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(outer)
        scroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        self.inner = ttk.Frame(canvas)
        self.inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0,0), window=self.inner, anchor="nw")
        canvas.configure(yscrollcommand=scroll.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.table_sample = load_table_sample(TABLE_FILE)
        self.move_defs = load_moves(MOVES_FILE)

        self.rows = []
        for name, rel in self.move_defs:
            row = ttk.Frame(self.inner); row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=name, width=30, anchor="w").pack(side=tk.LEFT)
            addr_var = tk.StringVar(value="—")
            val_var = tk.StringVar(value="—")
            tk.Label(row, textvariable=addr_var, width=14).pack(side=tk.LEFT)
            tk.Label(row, textvariable=val_var, width=10).pack(side=tk.LEFT)
            self.rows.append((name, rel, addr_var, val_var))

        self.latched_tail = None
        self.mem_cache = None

        self.after(400, self.tick)

    def anchor_ok(self) -> bool:
        if self.latched_tail is None or self.mem_cache is None:
            return False
        anchor_abs = self.latched_tail + ANCHOR_REL_FROM_TAIL
        off = anchor_abs - MEM2_LO
        if off < 0 or off + 4 > len(self.mem_cache):
            return False
        return self.mem_cache[off:off+4] == ANCHOR_BYTES

    def tick(self):
        self.mem_cache = read_mem2() if HAVE_DOLPHIN else None

        # check anchor
        if self.latched_tail is not None and self.mem_cache is not None:
            if not self.anchor_ok():
                print("[gui] anchor moved -> rescanning")
                self.latched_tail = None

        # scan if needed
        if self.latched_tail is None and self.table_sample is not None and self.mem_cache is not None:
            off = find_sample_in_mem(self.mem_cache, self.table_sample, SCAN_MATCH_LEN)
            if off is not None:
                tail_off = off + SAMPLE_TAIL_REL
                self.latched_tail = MEM2_LO + tail_off
                print(f"[gui] tail found at 0x{self.latched_tail:08X}")

        # update display
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
