# fd_dialogs.py (only change is the radio label text)
# Full file as-is with the updated label.

from __future__ import annotations

import tkinter as tk
from tkinter import ttk


class ReplaceMoveDialog(tk.Toplevel):
    def __init__(self, parent, all_moves):
        super().__init__(parent)
        self.title("Replace Move")
        self.geometry("620x480")
        self.resizable(True, True)

        self.result = None
        self.all_moves = list(all_moves or [])

        label = tk.Label(self, text="Choose the move to copy FROM (source):")
        label.pack(anchor="w", padx=8, pady=(8, 2))

        frame = tk.Frame(self)
        frame.pack(fill="both", expand=True, padx=8, pady=4)

        self.listbox = tk.Listbox(frame, height=16)
        self.listbox.pack(side="left", fill="both", expand=True)

        sb = ttk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        sb.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=sb.set)

        self._candidates = []
        for mv in self.all_moves:
            aid = mv.get("id")
            name = mv.get("move_name") or "anim_--"
            abs_addr = mv.get("abs") or 0
            label = f"{name:28} [0x{(aid or 0):04X}] @ 0x{abs_addr:08X}"
            self.listbox.insert("end", label)
            self._candidates.append(mv)

        if self._candidates:
            self.listbox.selection_set(0)

        mode_frame = ttk.LabelFrame(self, text="Mode")
        mode_frame.pack(fill="x", padx=8, pady=4)

        self.mode_var = tk.StringVar(value="anim")

        ttk.Radiobutton(
            mode_frame,
            text="Replace animation only (prefers 04 01 02 3F record; falls back to 01 ?? 01 3C)",
            variable=self.mode_var,
            value="anim",
        ).pack(anchor="w", padx=4, pady=2)

        ttk.Radiobutton(
            mode_frame,
            text="Replace entire block (aggressive clone)",
            variable=self.mode_var,
            value="block",
        ).pack(anchor="w", padx=4, pady=2)

        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", padx=8, pady=8)

        ttk.Button(btn_frame, text="OK", command=self._on_ok).pack(side="right", padx=4)
        ttk.Button(btn_frame, text="Cancel", command=self._on_cancel).pack(side="right", padx=4)

        self.bind("<Return>", lambda e: self._on_ok())
        self.bind("<Escape>", lambda e: self._on_cancel())

        self.transient(parent)
        self.grab_set()
        self.listbox.focus_set()

    def _on_ok(self):
        sel = self.listbox.curselection()
        if not sel:
            self.result = None
        else:
            idx = int(sel[0])
            src = self._candidates[idx]
            mode = self.mode_var.get()
            self.result = (src, mode)
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()
