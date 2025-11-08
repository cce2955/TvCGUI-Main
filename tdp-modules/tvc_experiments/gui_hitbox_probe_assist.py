# gui_hitbox_probe.py
#
# Purpose:
# - load ryu.bin from the same directory
# - ryu.bin can be EITHER:
#     1) raw bytes (ideal), OR
#     2) text hexdump with addresses like:
#        0x908AF004: 41 A0 00 00 ...
# - we normalize it to a bytes object
# - we scan MEM2 for the first N bytes of that normalized sample
# - when found, we say: tail = match_offset + 0x10 (because sample started at 0x908AF004 and real tail is at 0x908AF014)
# - then we read Ryu’s 5A etc. from tail-relative offsets
#
# Notes:
# - if your file is the text hexdump you pasted, this will now work
# - if match is too short / too common, increase MATCH_LEN

import os
import re
import tkinter as tk
from tkinter import ttk

print("[gui] starting…")

HAVE_DOLPHIN = True
try:
    from dolphin_io import hook, rbytes, rd32
    import dolphin_memory_engine as dme
    from constants import SLOTS, MEM2_LO, MEM2_HI, CHAR_NAMES
    print("[gui] dolphin modules imported")
except Exception as e:
    print("[gui] dolphin import FAILED:", e)
    HAVE_DOLPHIN = False
    SLOTS = []
    MEM2_LO = 0x90000000
    MEM2_HI = 0x94000000
    CHAR_NAMES = {}

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------

RYU_BIN_PATH = "ryu.bin"
# your sample started at 0x908AF004, tail was at 0x908AF014
RYU_SAMPLE_TAIL_REL = 0x10

# how many leading bytes to use to match
# if this still matches the wrong place, bump to 0x300 or 0x400
MATCH_LEN = 0x40


# fields relative to tail
RYU_FIELDS = [
    ("5A",      -0x10),
    ("5B",       0x420),
    ("5C",       0x834),
    ("Tatsu L",  0x59B0),
]

# we still show tail clusters for reference
TAIL = b"\x00\x00\x00\x38\x01\x33\x00\x00"
CLUSTER_GAP = 0x4000


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------

def write_bytes(addr: int, data: bytes):
    if not HAVE_DOLPHIN:
        return
    dme.write_bytes(addr, data)

def read_mem2() -> bytes:
    if not HAVE_DOLPHIN:
        return b""
    return rbytes(MEM2_LO, MEM2_HI - MEM2_LO)

def find_tail_clusters(mem: bytes):
    hits = []
    off = 0
    while True:
        i = mem.find(TAIL, off)
        if i == -1:
            break
        hits.append(i)
        off = i + 1

    if not hits:
        return []

    clusters = []
    cur = [hits[0]]
    for h in hits[1:]:
        if h - cur[-1] <= CLUSTER_GAP:
            cur.append(h)
        else:
            clusters.append(cur)
            cur = [h]
    clusters.append(cur)
    return clusters

def read_slot_chars():
    info = []
    for slotname, ptr_addr, teamtag in SLOTS:
        base = 0
        cid = None
        name = "—"
        if HAVE_DOLPHIN:
            try:
                base = rd32(ptr_addr)
            except Exception:
                base = 0
            if base:
                try:
                    cid = rd32(base + 0x14)
                except Exception:
                    cid = None
        if cid is not None:
            name = CHAR_NAMES.get(cid, f"ID_{cid}")
        info.append((slotname, base, cid, name))
    return info

# ---------------------------------------------------------------------
# ryu.bin loader that can handle text-hexdump-with-addresses
# ---------------------------------------------------------------------

HEX_PAIR_RE = re.compile(r"^[0-9A-Fa-f]{2}$")

def _try_parse_text_hexdump(data: str) -> bytes | None:
    """
    Try to parse a text hexdump like:
      0x908AF004: 41 A0 00 00 ...
    Returns bytes or None if it doesn't look like text.
    """
    out = bytearray()
    lines = data.splitlines()
    any_line_valid = False

    for line in lines:
        line = line.strip()
        if not line:
            continue
        # remove address prefix if present
        # patterns like:
        # 0x908AF004:
        # 908AF004:
        # 0x908AF004 :
        line = re.sub(r"^(0x)?[0-9A-Fa-f]+:\s*", "", line)
        # now line should be like: "41 A0 00 00 ..."
        parts = line.split()
        row_had_bytes = False
        for p in parts:
            # filter out non-hex pairs
            if HEX_PAIR_RE.match(p):
                out.append(int(p, 16))
                row_had_bytes = True
        if row_had_bytes:
            any_line_valid = True

    if not any_line_valid:
        return None
    return bytes(out)

def load_ryu_sample(path: str) -> bytes | None:
    if not os.path.exists(path):
        print(f"[gui] ryu.bin not found: {path}")
        return None

    with open(path, "rb") as f:
        raw = f.read()

    # try to decode as text
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # not text, assume raw bytes
        print(f"[gui] loaded ryu.bin as raw bytes ({len(raw)} bytes)")
        return raw

    # try to parse text-hexdump
    parsed = _try_parse_text_hexdump(text)
    if parsed is not None and len(parsed) > 0:
        print(f"[gui] loaded ryu.bin as text hexdump -> {len(parsed)} bytes")
        return parsed

    # fallback: use raw as-is
    print(f"[gui] loaded ryu.bin as text but no bytes found, using raw bytes ({len(raw)} bytes)")
    return raw

def find_sample_in_mem(mem: bytes, sample: bytes, match_len: int) -> int | None:
    if not sample:
        return None
    if match_len > len(sample):
        match_len = len(sample)
    sig = sample[:match_len]
    off = mem.find(sig)
    if off == -1:
        return None
    return off


# ---------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TvC Ryu table probe (ryu.bin aware)")
        self.geometry("760x480")

        self.tree = ttk.Treeview(
            self,
            columns=("slot","live_char","tail_cluster","base"),
            show="headings",
            height=7
        )
        for c, w in (
            ("slot", 90),
            ("live_char", 130),
            ("tail_cluster", 170),
            ("base", 160),
        ):
            self.tree.heading(c, text=c)
            self.tree.column(c, width=w, stretch=True)
        self.tree.pack(fill=tk.BOTH, expand=True, pady=4)

        self.ryu_frame = ttk.LabelFrame(self, text="Ryu fields (matched from ryu.bin)")
        self.ryu_frame.pack(fill=tk.X, pady=4)

        self.ryu_rows = []
        for name, off in RYU_FIELDS:
            row = ttk.Frame(self.ryu_frame); row.pack(fill=tk.X, pady=1)
            tk.Label(row, text=name, width=12).pack(side=tk.LEFT)

            addr_var = tk.StringVar(value="—")
            val_var = tk.StringVar(value="—")
            tk.Label(row, textvariable=addr_var, width=18).pack(side=tk.LEFT)
            tk.Label(row, textvariable=val_var, width=12).pack(side=tk.LEFT)

            def make_bump(delta, addr_var=addr_var, val_var=val_var):
                def _do():
                    if not HAVE_DOLPHIN:
                        return
                    txt = addr_var.get()
                    if txt == "—":
                        return
                    addr = int(txt, 16)
                    raw = rbytes(addr, 2)
                    if not raw or len(raw) != 2:
                        return
                    hi, lo = raw[0], raw[1]
                    value = (hi << 8) | lo
                    step = int(delta * 255)
                    value = (value + step) & 0xFFFF
                    new_hi = (value >> 8) & 0xFF
                    new_lo = value & 0xFF
                    write_bytes(addr, bytes([new_hi, new_lo]))
                    val_var.set(f"{new_hi:02X} {new_lo:02X}")
                return _do

            tk.Button(row, text="+0.10", command=make_bump(0.10)).pack(side=tk.LEFT, padx=2)
            tk.Button(row, text="-0.10", command=make_bump(-0.10)).pack(side=tk.LEFT, padx=2)

            self.ryu_rows.append((name, off, addr_var, val_var))

        # state
        self.saved_clusters = None
        self.saved_mem = None

        # load the sample right now
        self.ryu_sample = load_ryu_sample(RYU_BIN_PATH)

        self.after(400, self.update_data)

    def update_data(self):
        # clear table
        for iid in self.tree.get_children():
            self.tree.delete(iid)

        slots = read_slot_chars()
        mem = read_mem2() if HAVE_DOLPHIN else b""

        # keep a frozen copy of clusters for display
        if self.saved_clusters is None and mem:
            clusters = find_tail_clusters(mem)
            self.saved_clusters = clusters
            self.saved_mem = mem
            print(f"[gui] found {len(clusters)} tail clusters, frozen")
        else:
            clusters = self.saved_clusters or []
            if mem:
                self.saved_mem = mem

        # display slots
        for idx, (slotname, base, cid, live_name) in enumerate(slots):
            tail_addr = None
            if self.saved_clusters and idx < len(self.saved_clusters):
                mem_off = self.saved_clusters[idx][0]
                tail_addr = MEM2_LO + mem_off
            self.tree.insert(
                "",
                tk.END,
                values=(
                    slotname,
                    live_name,
                    f"0x{tail_addr:08X}" if tail_addr else "—",
                    f"0x{base:08X}" if base else "—",
                ),
            )

        # now try to locate Ryu's table by file match
        ryu_tail_abs = None
        if self.ryu_sample is not None and self.saved_mem is not None:
            match_off = find_sample_in_mem(self.saved_mem, self.ryu_sample, MATCH_LEN)
            if match_off is not None:
                tail_mem_off = match_off + RYU_SAMPLE_TAIL_REL
                ryu_tail_abs = MEM2_LO + tail_mem_off

        # update Ryu rows
        for name, rel_off, addr_var, val_var in self.ryu_rows:
            if ryu_tail_abs and HAVE_DOLPHIN:
                addr = ryu_tail_abs + rel_off
                addr_var.set(f"0x{addr:08X}")
                raw = rbytes(addr, 2)
                if raw and len(raw) == 2:
                    val_var.set(f"{raw[0]:02X} {raw[1]:02X}")
                else:
                    val_var.set("ERR")
            else:
                addr_var.set("—")
                val_var.set("—")

        self.after(500, self.update_data)


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
