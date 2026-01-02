# fd_dialogs.py
#
# Small Tk dialogs used by the frame editor.

import tkinter as tk
from tkinter import ttk


class ReplaceMoveDialog(tk.Toplevel):
    """
    Dialog to select another move and how to replace:

    - mode 'anim'  -> replace animation ID only
    - mode 'block' -> aggressive block clone (Y2-style)
    """

    def __init__(self, parent, all_moves, current_mv):
        super().__init__(parent)
        self.title("Replace Move")
        self.result = None

        self.all_moves = [m for m in all_moves if m is not current_mv]
        self.current_mv = current_mv

        cur_id = current_mv.get("id")
        cur_name = current_mv.get("move_name") or "?"
        tk.Label(
            self,
            text=f"Current: {cur_name} [0x{(cur_id or 0):04X}]",
            font=("Arial", 10, "bold"),
        ).pack(anchor="w", padx=8, pady=(6, 4))

        frame = tk.Frame(self)
        frame.pack(fill="both", expand=True, padx=8, pady=4)

        self.listbox = tk.Listbox(frame, height=14, exportselection=False, font=("Courier", 9))
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
            text="Replace animation only (01 ?? 01 3C)",
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
            mv = self._candidates[sel[0]]
            self.result = (mv, self.mode_var.get())
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.destroy()
