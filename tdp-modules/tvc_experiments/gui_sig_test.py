# gui_sig_test.py
#
# Small sandbox to test TvC move signatures in memory.
# - scans MEM2 for the usual tail pattern 00 00 00 38 01 33 00 00
# - lists all tails on the left
# - when you click one, it looks around that area and tells you which
#   of our known signatures are present
# - you can also type an arbitrary address and scan
#
# Drop this next to your other scripts.

import os
import re
import tkinter as tk
from tkinter import ttk, messagebox

print("[sigtest] starting…")

HAVE_DOLPHIN = True
try:
    from dolphin_io import hook, rbytes
    import dolphin_memory_engine as dme
    from constants import MEM2_LO, MEM2_HI
    print("[sigtest] dolphin modules imported")
except Exception as e:
    print("[sigtest] dolphin import FAILED:", e)
    HAVE_DOLPHIN = False
    MEM2_LO = 0x90000000
    MEM2_HI = 0x94000000

# ---------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------
TAIL_PATTERN = b"\x00\x00\x00\x38\x01\x33\x00\x00"

# how big of a window we look at around an address when testing sigs
SCAN_BACK = 0x80
SCAN_FWD = 0x120

# signatures you pasted, trimmed to their recognizable cores
SIGS = {
    "anim_start": [
        0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x01, 0xE8,
        0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, None,
        0x01, 0x3C, 0x00, 0x00, 0x00, 0x00, 0x16, 0x10,
    ],
    "attack_property": [
        0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x40,
        0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, None,
        0x04, 0x01, 0x60,
    ],
    "stuns": [
        0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x54,
        0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, None,
        0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x58,
    ],
    "active_frames": [
        0x20, 0x35, 0x01, 0x20, 0x3F, 0x00, 0x00, 0x00,
    ],
    "hit_reaction": [
        0x04, 0x17, 0x60, 0x00, 0x00, 0x00, 0x02, 0x40,
        0x3F, 0x00, 0x00, 0x00,
    ],
    # you can add the long “button can be altered…” later as another entry
}


# ---------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------
def read_mem2() -> bytes:
    if not HAVE_DOLPHIN:
        return b""
    return rbytes(MEM2_LO, MEM2_HI - MEM2_LO)


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


def find_sig(buf: bytes, sig: list[int | None]) -> bool:
    L = len(sig)
    if len(buf) < L:
        return False
    for i in range(0, len(buf) - L + 1):
        ok = True
        for j, sb in enumerate(sig):
            if sb is None:
                continue
            if buf[i + j] != sb:
                ok = False
                break
        if ok:
            return True
    return False


def hexdump_block(base_addr: int, data: bytes) -> str:
    out_lines = []
    addr = base_addr
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        hexbytes = " ".join(f"{b:02X}" for b in chunk)
        out_lines.append(f"0x{addr:08X}: {hexbytes}")
        addr += len(chunk)
    return "\n".join(out_lines)


# ---------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------
class SigApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TvC SIG TEST")
        self.geometry("1000x600")

        # left: list of tails + controls
        left = ttk.Frame(self)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=4)

        ctl = ttk.Frame(left); ctl.pack(fill=tk.X)
        ttk.Button(ctl, text="Reload MEM2", command=self.reload_mem).pack(side=tk.LEFT, padx=2)
        ttk.Label(ctl, text="Scan addr:").pack(side=tk.LEFT, padx=2)
        self.addr_entry = tk.Entry(ctl, width=12)
        self.addr_entry.pack(side=tk.LEFT, padx=2)
        ttk.Button(ctl, text="Scan", command=self.scan_manual).pack(side=tk.LEFT, padx=2)

        ttk.Label(left, text="Tail list:").pack(anchor="w")
        self.tail_list = tk.Listbox(left, width=28)
        self.tail_list.pack(fill=tk.Y, expand=True)
        self.tail_list.bind("<<ListboxSelect>>", self.on_tail_select)

        # right: results
        right = ttk.Frame(self)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=4)

        self.info_label = ttk.Label(right, text="No tail selected")
        self.info_label.pack(anchor="w")

        self.sig_label = ttk.Label(right, text="Signatures: —", foreground="blue")
        self.sig_label.pack(anchor="w", pady=4)

        self.hex_text = tk.Text(right, wrap="none")
        self.hex_text.pack(fill=tk.BOTH, expand=True)

        # state
        self.mem_cache = None
        self.tails = []

        # initial load
        self.reload_mem()

    # --------------------------------------------------
    def reload_mem(self):
        self.mem_cache = read_mem2()
        if not self.mem_cache:
            messagebox.showerror("mem", "Failed to read MEM2")
            return
        self.tails = find_all_tails(self.mem_cache, TAIL_PATTERN)
        self.tail_list.delete(0, tk.END)
        for off in self.tails:
            abs_addr = MEM2_LO + off
            self.tail_list.insert(tk.END, f"0x{abs_addr:08X}")
        self.info_label.config(text=f"Loaded MEM2 ({len(self.mem_cache)} bytes), {len(self.tails)} tails found")

    # --------------------------------------------------
    def on_tail_select(self, event):
        sel = self.tail_list.curselection()
        if not sel:
            return
        idx = sel[0]
        tail_off = self.tails[idx]
        tail_abs = MEM2_LO + tail_off
        self.scan_around_addr(tail_abs)

    # --------------------------------------------------
    def scan_manual(self):
        txt = self.addr_entry.get().strip()
        if not txt:
            return
        try:
            if txt.lower().startswith("0x"):
                addr = int(txt, 16)
            else:
                addr = int(txt)
        except ValueError:
            messagebox.showerror("addr", "Not a valid address")
            return
        self.scan_around_addr(addr)

    # --------------------------------------------------
    def scan_around_addr(self, addr: int):
        """look a bit around addr and show signatures and hex"""
        if self.mem_cache is None:
            return

        start_abs = addr - SCAN_BACK
        end_abs = addr + SCAN_FWD
        if start_abs < MEM2_LO:
            start_abs = MEM2_LO
        if end_abs > MEM2_HI:
            end_abs = MEM2_HI

        start_off = start_abs - MEM2_LO
        end_off = end_abs - MEM2_LO

        buf = self.mem_cache[start_off:end_off]

        found = []
        for name, sig in SIGS.items():
            if find_sig(buf, sig):
                found.append(name)

        self.info_label.config(text=f"Scan @ 0x{addr:08X} (window {len(buf)} bytes)")
        if found:
            self.sig_label.config(text="Signatures: " + ", ".join(found))
        else:
            self.sig_label.config(text="Signatures: —")

        # show hex
        self.hex_text.delete("1.0", tk.END)
        self.hex_text.insert("1.0", hexdump_block(start_abs, buf))


if __name__ == "__main__":
    if HAVE_DOLPHIN:
        print("[sigtest] hooking dolphin…")
        try:
            hook()
            print("[sigtest] hooked.")
        except Exception as e:
            print("[sigtest] hook FAILED:", e)
    else:
        print("[sigtest] running without dolphin")

    app = SigApp()
    app.mainloop()
