#!/usr/bin/env python
# assist_mem_gui.py
#
# Tiny standalone Tk GUI to watch/edit specific MEM2 regions in real time.
# Uses your dolphin_io wrapper (hook, rd8, wd8, wbytes).

import sys
import tkinter as tk
from tkinter import ttk, messagebox

try:
    from dolphin_io import hook, rd8, wd8, wbytes, addr_in_ram
except Exception as e:
    print("Failed to import dolphin_io:", e)
    sys.exit(1)

# ----------------------------------------------------------------------
# CONFIG: edit this list to track whatever you want
#   (label, address, length-in-bytes)
# ----------------------------------------------------------------------

WATCHES = [
    ("P1 Assist FlyIn Block", 0x909D6A20, 16),
    ("P1 Assist Script Chunk", 0x90984BC0, 16),
    # Add more rows here as needed
]

UPDATE_INTERVAL_MS = 250  # refresh rate for "Current" display


# ----------------------------------------------------------------------
# Low-level read/write helpers
# ----------------------------------------------------------------------

def read_bytes(addr, length):
    """Read 'length' bytes starting at addr as a list of ints."""
    data = []
    for i in range(length):
        b = rd8(addr + i)
        if b is None:
            # outside RAM or failed; pad with 00
            data.append(0)
        else:
            data.append(b)
    return data


def write_bytes(addr, byte_list):
    """
    Write a list of ints 0..255 starting at addr.
    Uses wbytes if contiguous, falls back to wd8 per-byte.
    """
    if not addr_in_ram(addr):
        raise RuntimeError(f"Address 0x{addr:08X} not in MEM1/MEM2")

    if not byte_list:
        return

    # Try wbytes first – it takes a bytes() object.
    try:
        ok = wbytes(addr, bytes(b & 0xFF for b in byte_list))
        if not ok:
            # fall back to per-byte if it reports False
            for i, b in enumerate(byte_list):
                wd8(addr + i, b & 0xFF)
    except TypeError:
        # Older dolphin_io without wbytes returning bool
        for i, b in enumerate(byte_list):
            wd8(addr + i, b & 0xFF)


# ----------------------------------------------------------------------
# GUI rows
# ----------------------------------------------------------------------

class WatchRow:
    def __init__(self, parent, name, addr, length):
        self.name = name
        self.addr = addr
        self.length = length

        self.frame = ttk.Frame(parent)

        self.label_name = ttk.Label(self.frame, text=name)
        self.label_addr = ttk.Label(self.frame, text=f"0x{addr:08X}")
        self.label_current = ttk.Label(
            self.frame,
            text="-" * max(1, (length * 3 - 1)),
            width=max(12, length * 3),
            anchor="w",
        )

        self.entry_new = ttk.Entry(self.frame, width=max(12, length * 3))
        self.button_write = ttk.Button(self.frame, text="Write", command=self.on_write)

        self.label_name.grid(row=0, column=0, sticky="w", padx=(2, 6))
        self.label_addr.grid(row=0, column=1, sticky="w", padx=(0, 6))
        self.label_current.grid(row=0, column=2, sticky="w", padx=(0, 6))
        self.entry_new.grid(row=0, column=3, sticky="w", padx=(0, 4))
        self.button_write.grid(row=0, column=4, sticky="w")

    def grid(self, **kwargs):
        self.frame.grid(**kwargs)

    def update_current(self):
        try:
            data = read_bytes(self.addr, self.length)
            hex_str = " ".join(f"{b:02X}" for b in data)
            self.label_current.config(text=hex_str)
        except Exception as e:
            self.label_current.config(text=f"<err: {e}>")

    def on_write(self):
        text = self.entry_new.get().strip()
        if not text:
            return

        try:
            tokens = text.replace(",", " ").split()
            if len(tokens) > self.length:
                raise ValueError(f"Too many bytes (got {len(tokens)}, max {self.length})")

            data_bytes = [int(tok, 16) for tok in tokens]
            write_bytes(self.addr, data_bytes)
            self.entry_new.delete(0, tk.END)
        except ValueError as ve:
            messagebox.showerror("Parse error", f"Could not parse hex: {ve}")
        except Exception as e:
            messagebox.showerror("Write error", f"Failed to write memory: {e}")


class MemEditorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("TvC MEM2 Assist Editor")

        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)

        header = ttk.Frame(main)
        header.grid(row=0, column=0, sticky="w")

        ttk.Label(header, text="Name", width=24).grid(row=0, column=0, padx=(2, 6), sticky="w")
        ttk.Label(header, text="Address", width=12).grid(row=0, column=1, padx=(0, 6), sticky="w")
        ttk.Label(header, text="Current (hex)", width=32).grid(row=0, column=2, padx=(0, 6), sticky="w")
        ttk.Label(header, text="New value (hex)", width=32).grid(row=0, column=3, padx=(0, 4), sticky="w")

        ttk.Separator(main, orient="horizontal").grid(row=1, column=0, sticky="ew", pady=4)

        body = ttk.Frame(main)
        body.grid(row=2, column=0, sticky="nsew")

        self.rows = []
        for i, (name, addr, length) in enumerate(WATCHES):
            row = WatchRow(body, name, addr, length)
            row.grid(row=i, column=0, sticky="w", pady=2)
            self.rows.append(row)

        main.rowconfigure(2, weight=1)
        main.columnconfigure(0, weight=1)

        self.schedule_update()

    def schedule_update(self):
        for row in self.rows:
            row.update_current()
        self.root.after(UPDATE_INTERVAL_MS, self.schedule_update)


def main():
    # Hook Dolphin once before opening the window
    try:
        hook()
    except Exception as e:
        print("Failed to hook Dolphin:", e)
        messagebox.showerror("Dolphin hook failed", str(e))
        return

    root = tk.Tk()
    MemEditorGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
