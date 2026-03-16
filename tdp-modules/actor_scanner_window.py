#!/usr/bin/env python3
from __future__ import annotations
import struct
import threading
import tkinter as tk
from tkinter import ttk

from dolphin_io import hook, rbytes

SCAN_START = 0x90000000
SCAN_END   = 0x94000000
SCAN_BLOCK = 0x20000

SIG = b"\x05\x2B"


def read_u16_be(data, off):
    return (data[off] << 8) | data[off+1]

def read_u24_be(data, off):
    return (data[off] << 16) | (data[off+1] << 8) | data[off+2]
def scan_for_entries():

    results = []

    addr = SCAN_START

    while addr < SCAN_END:

        data = rbytes(addr, SCAN_BLOCK)

        if data:

            pos = 0

            while True:

                i = data.find(SIG, pos)

                if i < 0:
                    break

                pos = i + 1

                if i + 4 >= len(data):
                    continue

                dmg = read_u24_be(data, i+3)

                
                if dmg < 500 or dmg > 20000:
                    continue        
                results.append({
                    "addr": addr + i,
                    "damage": dmg
                })

        addr += SCAN_BLOCK

    return results


class ActorScannerWindow:

    def __init__(self, root):

        self.root = root
        self.root.title("Actor / Projectile Scanner")
        self.root.geometry("900x500")

        frame = ttk.Frame(root)
        frame.pack(fill="both", expand=True)

        columns = ("addr","damage")

        self.tree = ttk.Treeview(
            frame,
            columns=columns,
            show="headings"
        )

        self.sort_column = None
        self.sort_reverse = False

        self.tree.heading(
            "addr",
            text="Address",
            command=lambda: self.sort_by("addr")
        )

        self.tree.heading(
            "damage",
            text="Damage",
            command=lambda: self.sort_by("damage")
        )

        self.tree.column("addr", width=160, anchor="center")
        self.tree.column("damage", width=100, anchor="center")

        self.tree.pack(fill="both", expand=True)

        self.tree.bind("<Button-3>", self.right_click)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="Copy", command=self.copy_selected)

        self.status = tk.StringVar(value="Scanning...")
        ttk.Label(root,textvariable=self.status).pack(anchor="w", padx=8)

        threading.Thread(target=self.scan_thread, daemon=True).start()

    def right_click(self, event):

        item = self.tree.identify_row(event.y)

        if item:
            self.tree.selection_set(item)
            self.menu.tk_popup(event.x_root, event.y_root)


    def copy_selected(self):

        sel = self.tree.selection()

        if not sel:
            return

        item = sel[0]

        values = self.tree.item(item, "values")

        text = "\t".join(str(v) for v in values)

        self.root.clipboard_clear()
        self.root.clipboard_append(text)
    def scan_thread(self):

        hits = scan_for_entries()

        self.root.after(0, lambda: self.populate(hits))


    def populate(self, hits):

        for i in self.tree.get_children():
            self.tree.delete(i)

        for h in hits:

            self.tree.insert(
                "",
                "end",
                values=(
                    f"0x{h['addr']:08X}",
                    h["damage"]
                )
            )

        self.status.set(f"Found {len(hits)} entries")


    def sort_by(self, col):

        rows = [(self.tree.set(k, col), k) for k in self.tree.get_children("")]

        if col == "addr":
            rows.sort(key=lambda t: int(t[0], 16), reverse=self.sort_reverse)
        else:
            rows.sort(key=lambda t: int(t[0]), reverse=self.sort_reverse)

        for index, (_, k) in enumerate(rows):
            self.tree.move(k, "", index)

        self.sort_reverse = not self.sort_reverse


def main():

    print("Hooking Dolphin...")
    hook()

    root = tk.Tk()

    ActorScannerWindow(root)

    root.mainloop()


if __name__ == "__main__":
    main()