#!/usr/bin/env python
"""
Standalone Ryu ground-normal scanner

Scans a small region near 0x908AEDE8 for patterns based on
Ryu's *ground* global move IDs only (5A,5B,5C,2A,2B,2C,6C,3C).

Depends only on: dolphin_io.hook, dolphin_io.rbytes
"""

import tkinter as tk
from tkinter import ttk, messagebox
from dolphin_io import hook, rbytes

# -------------------------------------------------------------------------
# RYU GROUND NORMALS  (decimal_id, hex_id, name)
# -------------------------------------------------------------------------

RYU_GROUND_NORMALS = [
    (256, 0x100, "5A"),
    (257, 0x101, "5B"),
    (258, 0x102, "5C"),
    (259, 0x103, "2A"),
    (260, 0x104, "2B"),
    (261, 0x105, "2C"),
    (262, 0x106, "6C"),
    (264, 0x108, "3C"),
]

# -------------------------------------------------------------------------
# Pattern computation
# -------------------------------------------------------------------------

def compute_patterns(global_hex_id):
    """
    Convert the move hex ID (example: 0x100) into marker bytes.
    Split into hi/lo bytes and build two patterns:

        hi lo 01   (anim caller)
        hi lo 04   (full caller / alt form)

    Example: 0x100 -> 01 00 01 and 01 00 04
    """
    hi = (global_hex_id >> 8) & 0xFF
    lo = global_hex_id & 0xFF

    pattern_ground = bytes([hi, lo, 0x01])
    pattern_full   = bytes([hi, lo, 0x04])
    return pattern_ground, pattern_full

# -------------------------------------------------------------------------
# Region scan
# -------------------------------------------------------------------------

def scan_region(base_addr, size):
    data = rbytes(base_addr, size)
    if not data:
        return {}

    hits = {}

    for global_id, hex_id, name in RYU_GROUND_NORMALS:
        pat1, pat2 = compute_patterns(hex_id)

        found = []
        for i in range(len(data) - 3):
            if data[i:i+3] == pat1 or data[i:i+3] == pat2:
                found.append(base_addr + i)

        if found:
            hits[name] = (hex_id, found)
    return hits

# -------------------------------------------------------------------------
# GUI
# -------------------------------------------------------------------------

class RyuGroundScanGUI:
    def __init__(self, root):
        self.root = root
        root.title("Ryu Ground Normals Scanner (Standalone)")

        frm = ttk.Frame(root, padding=8)
        frm.pack(fill="both", expand=True)

        ttk.Label(frm, text="Base address (hex):").grid(row=0, column=0, sticky="e")
        self.base_entry = ttk.Entry(frm, width=12)
        self.base_entry.grid(row=0, column=1, sticky="w")
        
        self.base_entry.insert(0, "0x908AEDE8")

        ttk.Label(frm, text="Size (hex):").grid(row=1, column=0, sticky="e")
        self.size_entry = ttk.Entry(frm, width=12)
        self.size_entry.grid(row=1, column=1, sticky="w")
        self.size_entry.insert(0, "0x2000")

        self.scan_btn = ttk.Button(frm, text="Scan Ryu ground normals", command=self.do_scan)
        self.scan_btn.grid(row=0, column=2, rowspan=2, padx=12)

        self.txt = tk.Text(frm, width=90, height=28, font=("Consolas", 10))
        self.txt.grid(row=2, column=0, columnspan=3, pady=(10,0))

        # hook Dolphin once at startup
        hook()

    def do_scan(self):
        self.txt.delete("1.0", tk.END)

        try:
            base = int(self.base_entry.get(), 16)
            size = int(self.size_entry.get(), 16)
        except Exception:
            messagebox.showerror("Bad input", "Enter valid hex numbers.")
            return

        self.txt.insert(tk.END, f"Scanning 0x{base:08X} - 0x{base+size:08X}\n")
        self.txt.insert(tk.END, "-"*80 + "\n")

        hits = scan_region(base, size)

        if not hits:
            self.txt.insert(tk.END, "No ground normal markers found.\n")
            return

        # Stable order: 5A, 5B, 5C, 2A, 2B, 2C, 6C, 3C
        for _, hex_id, move_name in RYU_GROUND_NORMALS:
            if move_name not in hits:
                continue
            _, addrs = hits[move_name]
            self.txt.insert(tk.END, f"{move_name}  (ID hex {hex_id:03X}):\n")
            for a in addrs:
                self.txt.insert(tk.END, f"   → 0x{a:08X}\n")
            self.txt.insert(tk.END, "\n")

# -------------------------------------------------------------------------
# Main
# -------------------------------------------------------------------------

if __name__ == "__main__":
    root = tk.Tk()
    RyuGroundScanGUI(root)
    root.mainloop()
