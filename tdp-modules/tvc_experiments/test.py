#!/usr/bin/env python

#
# TvC move-control explorer (grouped clones version):
# - Uses scan_normals_all.scan_once() to get hitbox records.
# - For each move record, looks for 01 XX 01 3C pattern near the front.
# - Treats XX as "ID0" (control ID).
# - Groups all clones of the same logical move (slot+char+anim_id+ID0).
# - Shows Slot, Move, ABS (all candidate addresses), ID0, Label, and bytes
#   around the pattern for the first clone.
# - Editing ID0 writes to *all* ABSs in that group.
# - Labels are stored in move_labels.json keyed by (char_name, ID0).

import sys
import json
import os
import tkinter as tk
from tkinter import ttk, messagebox

LABELS_JSON = "move_labels.json"

try:
    from dolphin_io import hook, rbytes, rd8, wd8, addr_in_ram
    import scan_normals_all
    from scan_normals_all import ANIM_MAP as SCAN_ANIM_MAP
except Exception as e:
    print("Import failure:", e)
    sys.exit(1)


# ---------------------------------------------------------------------
# Label persistence
# ---------------------------------------------------------------------

def load_label_db(path=LABELS_JSON):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except Exception:
        return {}


def save_label_db(db, path=LABELS_JSON):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, sort_keys=True)
    except Exception as e:
        print("Failed to save label db:", e)


# ---------------------------------------------------------------------
# Pattern helpers
# ---------------------------------------------------------------------

def find_control_pattern(base, window_size=0x40):
    """
    Scan the first window_size bytes at 'base' for pattern:

        00 00 00 00 01 XX 01 3C

    Return (offset, id0_byte) or (None, None) if not found.
    """
    data = rbytes(base, window_size) or b""
    if len(data) < 8:
        return None, None

    for i in range(len(data) - 7):
        if (
            data[i] == 0x00
            and data[i + 1] == 0x00
            and data[i + 2] == 0x00
            and data[i + 3] == 0x00
            and data[i + 4] == 0x01
            and data[i + 6] == 0x01
            and data[i + 7] == 0x3C
        ):
            id0 = data[i + 5]
            return i, id0

    return None, None


def control_window_bytes(base, pat_off, radius=8):
    """
    Return a small window of bytes around base+pat_off for display.
    """
    if pat_off is None:
        return b""
    start = max(0, pat_off - radius)
    size = radius * 2 + 8
    return rbytes(base + start, size) or b""


# ---------------------------------------------------------------------
# Collect moves from scan_normals_all (with grouping)
# ---------------------------------------------------------------------

def collect_moves_from_scan(scan_data, label_db):
    """
    Flatten scan_normals_all.scan_once() output into a list of grouped moves:

      {
        'slot', 'move_name', 'char_name',
        'anim_id', 'id0',
        'records': [
            {'base', 'pat_off', 'ctrl_window'},
            ...
        ],
        'label'
      }

    We group by (slot_label, char_name, anim_id, id0) so duplicates
    (like Chun's four 5A entries) become one row with multiple ABSes.
    """
    grouped = {}

    for slot_info in scan_data:
        slot_label = slot_info.get("slot_label", "?")
        char_name = slot_info.get("char_name", "").strip()
        for mv in slot_info.get("moves", []):
            base = mv.get("abs")
            if base is None:
                continue
            anim_id = mv.get("id")
            if anim_id is None:
                continue

            pat_off, id0 = find_control_pattern(base)
            if pat_off is None:
                continue

            win_bytes = control_window_bytes(base, pat_off, radius=8)
            move_name = SCAN_ANIM_MAP.get(anim_id, f"anim_{anim_id:02X}")

            key = (slot_label, char_name, anim_id, id0)

            if key not in grouped:
                # look up stored label (per-char, per-ID0)
                id0_hex = f"{id0:02X}"
                label = ""
                if char_name:
                    char_key = char_name.lower()
                    if char_key in label_db and id0_hex in label_db[char_key]:
                        label = label_db[char_key][id0_hex]

                grouped[key] = {
                    "slot": slot_label,
                    "move_name": move_name,
                    "char_name": char_name,
                    "anim_id": anim_id,
                    "id0": id0,
                    "records": [],
                    "label": label,
                }

            grouped[key]["records"].append(
                {
                    "base": base,
                    "pat_off": pat_off,
                    "ctrl_window": win_bytes,
                }
            )

    # flatten into list and sort
    moves = []
    for g in grouped.values():
        # use ctrl_window from the first record just for display
        first_rec = g["records"][0]
        g["ctrl_window"] = first_rec["ctrl_window"]
        moves.append(g)

    moves.sort(
        key=lambda m: (
            m["slot"],
            m["move_name"],
            min(rec["base"] for rec in m["records"]),
        )
    )
    return moves


# ---------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------

class MoveControlGUI:
    def __init__(self, root, moves, label_db):
        self.root = root
        self.root.title("TvC Move Control / Label Editor (grouped clones)")

        self.moves = moves
        self.label_db = label_db  # {char_key: {id0_hex: label}}

        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            top,
            text=(
                "All moves from scan_normals_all, grouped by slot/char/anim/ID0. "
                "ABS lists every matching record; editing ID0 writes to all of them."
            ),
        ).grid(row=0, column=0, sticky="w")

        cols = ("slot", "move", "abs", "id0", "label", "ctrl")
        self.tree = ttk.Treeview(main, columns=cols, show="headings", height=24)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(4, 4))

        vsb = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.grid(row=1, column=1, sticky="ns", pady=(4, 4))

        self.tree.heading("slot", text="Slot")
        self.tree.heading("move", text="Move")
        self.tree.heading("abs", text="ABS (all clones)")
        self.tree.heading("id0", text="ID0 (XX)")
        self.tree.heading("label", text="Label")
        self.tree.heading("ctrl", text="control window (bytes around pattern)")

        self.tree.column("slot", width=60, anchor="w")
        self.tree.column("move", width=140, anchor="w")
        self.tree.column("abs", width=260, anchor="w")
        self.tree.column("id0", width=60, anchor="center")
        self.tree.column("label", width=260, anchor="w")
        self.tree.column("ctrl", width=400, anchor="w")

        bottom = ttk.Frame(main, padding=(0, 4, 0, 0))
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew")

        ttk.Label(bottom, text="Selected ABS:").grid(row=0, column=0, sticky="e")
        self.sel_abs_var = tk.StringVar(value="(none)")
        ttk.Label(bottom, textvariable=self.sel_abs_var, width=40).grid(
            row=0, column=1, sticky="w", padx=(4, 12)
        )

        ttk.Label(bottom, text="Char:").grid(row=0, column=2, sticky="e")
        self.sel_char_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.sel_char_var, width=14).grid(
            row=0, column=3, sticky="w", padx=(4, 12)
        )

        ttk.Label(bottom, text="ID0 (hex):").grid(row=0, column=4, sticky="e")
        self.id0_entry = ttk.Entry(bottom, width=4)
        self.id0_entry.grid(row=0, column=5, sticky="w", padx=(2, 12))

        ttk.Label(bottom, text="Label:").grid(row=0, column=6, sticky="e")
        self.label_entry = ttk.Entry(bottom, width=40)
        self.label_entry.grid(row=0, column=7, sticky="w", padx=(2, 8))

        self.write_btn = ttk.Button(
            bottom, text="Write ID0 to all clones + Save Label", command=self.on_write
        )
        self.write_btn.grid(row=0, column=8, padx=(8, 4))

        self.reload_btn = ttk.Button(bottom, text="Reload scan", command=self.on_reload_scan)
        self.reload_btn.grid(row=0, column=9, padx=(4, 0))

        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)

        self._populate_tree()
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

    # ------------------------------------------------------

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        for mv in self.moves:
            slot = mv["slot"]
            move_name = mv["move_name"]
            id0 = mv["id0"]
            label = mv.get("label", "") or ""

            bases = [rec["base"] for rec in mv["records"]]
            abs_str = ", ".join(f"0x{b:08X}" for b in bases)

            win = mv.get("ctrl_window") or b""
            ctrl_hex = " ".join(f"{b:02X}" for b in win)

            id0_str = "--" if id0 is None else f"{id0:02X}"

            self.tree.insert(
                "",
                "end",
                values=(slot, move_name, abs_str, id0_str, label, ctrl_hex),
            )

    def _get_selected_index(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        return self.tree.index(iid)

    def _get_selected_move(self):
        idx = self._get_selected_index()
        if idx is None or idx < 0 or idx >= len(self.moves):
            return None
        return self.moves[idx]

    def on_select(self, event):
        mv = self._get_selected_move()
        if not mv:
            self.sel_abs_var.set("(none)")
            self.sel_char_var.set("")
            self.id0_entry.delete(0, tk.END)
            self.label_entry.delete(0, tk.END)
            return

        bases = [rec["base"] for rec in mv["records"]]
        abs_str = ", ".join(f"0x{b:08X}" for b in bases)
        self.sel_abs_var.set(abs_str)

        char = mv.get("char_name", "")
        self.sel_char_var.set(char or "")

        id0 = mv.get("id0", 0)
        self.id0_entry.delete(0, tk.END)
        self.id0_entry.insert(0, f"{id0:02X}")

        label = mv.get("label", "") or ""
        self.label_entry.delete(0, tk.END)
        self.label_entry.insert(0, label)

    def on_write(self):
        mv = self._get_selected_move()
        if not mv:
            messagebox.showerror("No selection", "Select a move first.")
            return

        char_name = (mv.get("char_name") or "").strip()
        records = mv["records"]

        id0_txt = self.id0_entry.get().strip()
        label_txt = self.label_entry.get().strip()

        try:
            new_id0 = int(id0_txt, 16) & 0xFF
        except ValueError:
            messagebox.showerror("Parse error", "ID0 must be a hex byte (00..FF).")
            return

        # fan-out write: update every clone record
        try:
            for rec in records:
                base = rec["base"]
                pat_off = rec["pat_off"]

                if not addr_in_ram(base):
                    raise RuntimeError(f"ABS 0x{base:08X} not in MEM1/MEM2.")

                id0_addr = base + pat_off + 5
                if not wd8(id0_addr, new_id0):
                    raise RuntimeError(f"wd8 failed at 0x{id0_addr:08X}")
        except Exception as e:
            messagebox.showerror("Write error", str(e))
            return

        # update in-memory record
        mv["id0"] = new_id0
        mv["label"] = label_txt

        # store label if we have a char name
        if char_name:
            char_key = char_name.lower()
            self.label_db.setdefault(char_key, {})
            self.label_db[char_key][f"{new_id0:02X}"] = label_txt
            save_label_db(self.label_db)

        # refresh tree
        idx = self._get_selected_index()
        if idx is not None:
            self.tree.delete(*self.tree.get_children())
            self._populate_tree()
            children = self.tree.get_children()
            if 0 <= idx < len(children):
                self.tree.selection_set(children[idx])
                self.tree.see(children[idx])

        bases = [rec["base"] for rec in records]
        abs_str = ", ".join(f"0x{b:08X}" for b in bases)
        messagebox.showinfo(
            "Done",
            f"Wrote ID0={new_id0:02X} to {len(records)} clone(s): {abs_str}\n"
            f"and saved label.",
        )

    def on_reload_scan(self):
        messagebox.showinfo(
            "Reload",
            "Close and re-run this script to rescan. (Keeps move_labels.json.)",
        )


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    print("Hooking Dolphin...")
    hook()
    print("Hooked. Running scan_normals_all.scan_once()...")
    try:
        scan_data = scan_normals_all.scan_once()
    except Exception as e:
        print("scan_once failed:", e)
        sys.exit(1)

    label_db = load_label_db()
    moves = collect_moves_from_scan(scan_data, label_db)
    print(f"Found {len(moves)} grouped moves with 01 XX 01 3C pattern.")

    root = tk.Tk()
    MoveControlGUI(root, moves, label_db)
    root.mainloop()


if __name__ == "__main__":
    main()
