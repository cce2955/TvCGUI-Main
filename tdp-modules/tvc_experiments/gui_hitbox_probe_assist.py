# gui_hitbox_probe.py
#
# - load ryu.bin (text or raw)
# - scan MEM2 for the first 0x40 bytes (your working method)
# - when found:
#     tail = match + 0x10
#     latch tail
# - every tick:
#     read (tail - 0xB0), expect FF FF FF FE
#     if matches -> keep tail
#     else -> drop tail and rescan
#
# So editing 5A etc. won't break it; only if the whole table shifts will it rescan.

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

# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------
RYU_BIN_PATH = "ryu.bin"

# your slice: start 0x908AF004, tail 0x908AF014
RYU_SAMPLE_TAIL_REL = 0x10

# your scan length that actually worked
SCAN_MATCH_LEN = 0x40

# from your screenshot:
# 0x908AEF64: FF FF FF FE ...
# 0x908AF014: tail
# tail - anchor = 0xB0
ANCHOR_REL_FROM_TAIL = -0xB0
ANCHOR_BYTES = b"\xFF\xFF\xFF\xFE"

# offsets you want to display
RYU_FIELDS = [
    ("5A",      -0x10),
    ("5B",       0x420),
    ("5C",       0x834),
    ("Tatsu L",  0x59B0),
]

HEX_PAIR_RE = re.compile(r"^[0-9A-Fa-f]{2}$")

# ------------------------------------------------------------
# helpers
# ------------------------------------------------------------
def read_mem2() -> bytes:
    if not HAVE_DOLPHIN:
        return b""
    return rbytes(MEM2_LO, MEM2_HI - MEM2_LO)

def _try_parse_text_hexdump(data: str) -> bytes | None:
    out = bytearray()
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        # strip "0x908AF004:" part
        line = re.sub(r"^(0x)?[0-9A-Fa-f]+:\s*", "", line)
        for p in line.split():
            if HEX_PAIR_RE.match(p):
                out.append(int(p, 16))
    return bytes(out) if out else None

def load_ryu_sample(path: str) -> bytes | None:
    if not os.path.exists(path):
        print("[gui] ryu.bin not found")
        return None
    with open(path, "rb") as f:
        raw = f.read()
    # try text
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError:
        print(f"[gui] loaded ryu.bin as raw bytes ({len(raw)} bytes)")
        return raw
    parsed = _try_parse_text_hexdump(txt)
    if parsed:
        print(f"[gui] loaded ryu.bin as text hexdump -> {len(parsed)} bytes")
        return parsed
    print(f"[gui] loaded ryu.bin as text but no bytes parsed, using raw ({len(raw)} bytes)")
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

# ------------------------------------------------------------
# GUI
# ------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TvC Ryu probe (anchor-based)")
        self.geometry("650x300")

        frame = ttk.LabelFrame(self, text="Ryu fields")
        frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.rows = []
        for name, off in RYU_FIELDS:
            row = ttk.Frame(frame); row.pack(fill=tk.X, pady=2)
            tk.Label(row, text=name, width=10).pack(side=tk.LEFT)
            addr_var = tk.StringVar(value="—")
            val_var = tk.StringVar(value="—")
            tk.Label(row, textvariable=addr_var, width=16).pack(side=tk.LEFT)
            tk.Label(row, textvariable=val_var, width=10).pack(side=tk.LEFT)
            self.rows.append((name, off, addr_var, val_var))

        self.ryu_sample = load_ryu_sample(RYU_BIN_PATH)
        self.latched_tail = None
        self.mem_cache = None

        self.after(400, self.tick)

    # check if anchor still present at latched address
    def anchor_ok(self) -> bool:
        if self.latched_tail is None or self.mem_cache is None:
            return False
        anchor_abs = self.latched_tail + ANCHOR_REL_FROM_TAIL
        mem_off = anchor_abs - MEM2_LO
        if mem_off < 0 or mem_off + 4 > len(self.mem_cache):
            return False
        return self.mem_cache[mem_off:mem_off+4] == ANCHOR_BYTES

    def tick(self):
        # refresh mem
        self.mem_cache = read_mem2() if HAVE_DOLPHIN else None

        # if we have a latched tail, check anchor
        if self.latched_tail is not None and self.mem_cache is not None:
            if not self.anchor_ok():
                # anchor moved -> table moved -> drop and rescan
                print("[gui] anchor moved, rescanning…")
                self.latched_tail = None

        # if no latch, scan
        if self.latched_tail is None and self.ryu_sample is not None and self.mem_cache is not None:
            off = find_sample_in_mem(self.mem_cache, self.ryu_sample, SCAN_MATCH_LEN)
            if off is not None:
                tail_off = off + RYU_SAMPLE_TAIL_REL
                self.latched_tail = MEM2_LO + tail_off
                print(f"[gui] Ryu tail found at 0x{self.latched_tail:08X}")

        # display
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
