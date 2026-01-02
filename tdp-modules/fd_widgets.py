# fd_widgets.py
#
# Small reusable Tk widgets/dialogs to keep fd_window slim.

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox


class Tooltip:
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text or ""
        self.tip = None
        widget.bind("<Enter>", self._show, add=True)
        widget.bind("<Leave>", self._hide, add=True)

    def _show(self, _evt=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.geometry(f"+{x}+{y}")
        frame = tk.Frame(self.tip, bg="#1e1e1e", bd=1, relief="solid")
        frame.pack(fill="both", expand=True)
        lbl = tk.Label(
            frame,
            text=self.text,
            bg="#1e1e1e",
            fg="#e8e8e8",
            justify="left",
            font=("Segoe UI", 9),
            padx=8,
            pady=6,
        )
        lbl.pack()

    def _hide(self, _evt=None):
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


class ManualAnimIDDialog(simpledialog.Dialog):
    def __init__(self, parent, cur_hi=None, cur_lo=None):
        self.cur_hi = cur_hi
        self.cur_lo = cur_lo
        self.result = None
        super().__init__(parent, title="Manual Anim ID (HI / LO)")

    def body(self, master):
        ttk.Label(master, text="High byte (HI):").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        ttk.Label(master, text="Low byte (LO):").grid(row=1, column=0, sticky="e", padx=6, pady=4)

        self.hi_var = tk.StringVar(value=f"{self.cur_hi:02X}" if self.cur_hi is not None else "")
        self.lo_var = tk.StringVar(value=f"{self.cur_lo:02X}" if self.cur_lo is not None else "")

        self.hi_entry = ttk.Entry(master, width=6, textvariable=self.hi_var)
        self.lo_entry = ttk.Entry(master, width=6, textvariable=self.lo_var)

        self.hi_entry.grid(row=0, column=1, padx=6, pady=4)
        self.lo_entry.grid(row=1, column=1, padx=6, pady=4)

        ttk.Label(master, text="Hex (00-FF)").grid(row=0, column=2, rowspan=2, padx=6)

        return self.hi_entry

    def validate(self):
        try:
            hi = int(self.hi_var.get(), 16)
            lo = int(self.lo_var.get(), 16)
            if not (0 <= hi <= 0xFF and 0 <= lo <= 0xFF):
                raise ValueError
            self.result = (hi, lo)
            return True
        except Exception:
            messagebox.showerror("Invalid Input", "HI and LO must be hex bytes (00-FF).")
            return False
