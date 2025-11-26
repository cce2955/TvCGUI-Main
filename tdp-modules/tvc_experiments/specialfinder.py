#!/usr/bin/env python3
"""
Standalone GUI tool to dynamically locate move/special script animation blocks
such as Chun-Li's assist: 01 12 01 3C at 0x90984D52.

Features:
 - Input pattern (hex string)
 - Scan MEM2 for all matches
 - List addresses
 - Click address to open a 256-byte hex viewer around it

Requires dolphin_io.py in the SAME DIRECTORY.
"""

import tkinter as tk
from tkinter import ttk, messagebox
import threading
import time
import binascii

from dolphin_io import hook, rbytes

MEM2_START = 0x90000000
MEM2_END   = 0x94000000

SCAN_CHUNK = 0x2000    # 8KB per read


class PatternFinderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Special / Assist Script Finder")

        self.pattern_var = tk.StringVar(value="01 12 01 3C")

        frm = tk.Frame(root)
        frm.pack(padx=10, pady=10)

        tk.Label(frm, text="Search pattern (hex bytes):").grid(row=0, column=0, sticky="w")
        tk.Entry(frm, textvariable=self.pattern_var, width=30).grid(row=0, column=1, sticky="w")

        tk.Button(frm, text="Scan MEM2", command=self.scan).grid(row=0, column=2, padx=10)

        self.tree = ttk.Treeview(root, columns=("addr",), show="headings", height=20)
        self.tree.heading("addr", text="Matched Address")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)

        self.tree.bind("<Double-1>", self.open_hex_view)

        tk.Label(root, text="Double-click any address to open hex viewer.").pack(pady=4)

        self.status = tk.Label(root, text="Ready.")
        self.status.pack()

        threading.Thread(target=self._hook_dolphin, daemon=True).start()

    def _hook_dolphin(self):
        self.status.config(text="Hooking Dolphin…")
        hook()
        self.status.config(text="Dolphin hooked.")

    def scan(self):
        pat_raw = self.pattern_var.get().strip().replace(" ", "")
        if len(pat_raw) % 2 != 0:
            messagebox.showerror("Error", "Hex pattern must be even length.")
            return

        try:
            pattern = binascii.unhexlify(pat_raw)
        except Exception:
            messagebox.showerror("Error", "Invalid hex.")
            return

        self.tree.delete(*self.tree.get_children())
        self.status.config(text="Scanning…")

        threading.Thread(target=self._scan_worker, args=(pattern,), daemon=True).start()

    def _scan_worker(self, pattern):
        matches = []
        p_len = len(pattern)

        addr = MEM2_START

        while addr < MEM2_END:
            try:
                chunk = rbytes(addr, SCAN_CHUNK)
            except:
                addr += SCAN_CHUNK
                continue

            offset = chunk.find(pattern)
            while offset != -1:
                real_addr = addr + offset
                matches.append(real_addr)
                offset = chunk.find(pattern, offset + 1)

            addr += SCAN_CHUNK

        # populate tree
        for m in matches:
            self.tree.insert("", "end", values=(f"0x{m:X}",))

        if matches:
            self.status.config(text=f"Found {len(matches)} matches.")
        else:
            self.status.config(text="No matches found.")

    def open_hex_view(self, event):
        item = self.tree.focus()
        if not item:
            return

        addr_text = self.tree.item(item, "values")[0]
        try:
            addr = int(addr_text, 16)
        except:
            return

        self._open_hex_window(addr)

    def _open_hex_window(self, addr):
        win = tk.Toplevel(self.root)
        win.title(f"Hex Viewer @ 0x{addr:X}")

        text = tk.Text(win, width=80, height=20)
        text.pack()

        base = addr - 0x80
        if base < MEM2_START:
            base = MEM2_START

        size = 0x100

        try:
            data = rbytes(base, size)
        except:
            text.insert("end", "Could not read memory.")
            return

        # Format hex dump
        for i in range(0, size, 16):
            row_addr = base + i
            chunk = data[i:i+16]
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            text.insert("end", f"{row_addr:08X}  {hex_part}\n")

        text.config(state="disabled")


if __name__ == "__main__":
    root = tk.Tk()
    app = PatternFinderGUI(root)
    root.mainloop()
