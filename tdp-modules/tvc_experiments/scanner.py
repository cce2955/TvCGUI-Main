#!/usr/bin/env python

#
# TvC move-control explorer (grouped clones version + house info):
# - Uses scan_normals_all.scan_once() to get hitbox records.
# - For each move record, looks for control pattern near the front (ID0).
# - Also decodes the "house" struct (04 01 60 ...) like scanner.py.
# - Groups all clones of the same logical move (slot+char+anim_id+ID0).
# - Shows Slot, Move, Anim (hex), ABS (all candidate addresses), ID0, Label,
#   control pattern window, and house window (0B F0 / 33 35 20 3F etc).
# - Editing ID0 still writes to *all* ABSs in that group.
# - Labels are stored in move_labels.json, keyed by ID0 (with _generic + per-char).
#

import sys
import json
import os
import struct
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
# Label persistence / helpers
# ---------------------------------------------------------------------

def load_label_db(path: str = LABELS_JSON):
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


def save_label_db(db, path: str = LABELS_JSON):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, sort_keys=True)
    except Exception as e:
        print("Failed to save label db:", e)


def _norm_char_key(name: str) -> str:
    name = (name or "").strip().lower()
    for ch in (" ", "-", "_", "."):
        name = name.replace(ch, "")
    return name


def get_move_label(label_db, char_name: str, id0_hex: str) -> str:
    """
    Look up label with per-character override, then _generic.
    label_db format example:
      {
        "_generic": {"00": "5A", ...},
        "ryu": {"60": "Shinkuu Hadouken", ...}
      }
    """
    generic = label_db.get("_generic", {})
    per_char_raw = {k: v for k, v in label_db.items() if k != "_generic"}

    ckey = _norm_char_key(char_name)
    per_char = per_char_raw.get(ckey, {}) if ckey else {}
    return per_char.get(id0_hex) or generic.get(id0_hex, "")


# ---------------------------------------------------------------------
# Pattern helpers (ID0)
# ---------------------------------------------------------------------

def find_control_pattern(base: int, window_size: int = 0x120):
    """
    Scan the first window_size bytes at 'base' for control patterns.

    Pass 1 (strict, grounded normals):
        00 00 00 00 01 XX 01 3C

    Pass 2 (air / variants):
        01 XX 01 3C
        01 XX 01 3F

    Returns (id_off, id0_byte) where id_off is the offset (from base)
    of the ID byte itself. If not found, returns (None, None).
    """
    data = rbytes(base, window_size) or b""
    n = len(data)
    if n < 4:
        return None, None

    # Pass 1: original strict pattern
    if n >= 8:
        for i in range(n - 7):
            if (
                data[i]     == 0x00 and
                data[i + 1] == 0x00 and
                data[i + 2] == 0x00 and
                data[i + 3] == 0x00 and
                data[i + 4] == 0x01 and
                data[i + 6] == 0x01 and
                data[i + 7] == 0x3C
            ):
                id0 = data[i + 5]
                if 0x00 <= id0 <= 0xBE:
                    id_off = i + 5  # ID byte position
                    return id_off, id0

    # Pass 2: looser 01 XX 01 3C/3F (air normals etc.)
    for i in range(n - 3):
        if data[i] == 0x01 and data[i + 2] == 0x01 and data[i + 3] in (0x3C, 0x3F):
            id0 = data[i + 1]
            if 0x00 <= id0 <= 0xBE:
                id_off = i + 1  # ID byte position
                return id_off, id0

    return None, None


def control_window_bytes(base: int, id_off: int, radius: int = 8):
    """
    Return a small window of bytes around the ID byte at base+id_off.
    """
    if id_off is None:
        return b""
    start = max(0, id_off - radius)
    size = radius * 2 + 1  # ID byte roughly in the middle
    return rbytes(base + start, size) or b""


# ---------------------------------------------------------------------
# House struct helpers (04 01 60 ... like scanner.py)
# ---------------------------------------------------------------------

MAGIC_HOUSE = b"\x04\x01\x60"
BASE_BACK = 0x14       # offset from 04 01 60 block back to struct base
TAIL_RANGE = 0x40      # bytes we pull for the 'house' window


def decode_house(base: int):
    """
    Try to interpret a 'house' struct for this ABS, similar to scanner.py.

    Returns:
      {
        "radius": int,
        "x": float,
        "y": int,
        "flags": int,
        "window": "hex bytes..."
      }
    or None if no valid house is found.
    """
    block = rbytes(base, 0x80) or b""
    if len(block) < 0x20:
        return None

    idx = block.find(MAGIC_HOUSE)
    if idx == -1 or idx < BASE_BACK:
        return None

    struct_base = base + idx - BASE_BACK
    hdr = rbytes(struct_base, TAIL_RANGE) or b""
    if len(hdr) < 0x20:
        return None

    try:
        radius = struct.unpack(">I", hdr[0x00:0x04])[0]
        x_offset = struct.unpack(">f", hdr[0x04:0x08])[0]
        y_const = struct.unpack(">I", hdr[0x08:0x0C])[0]
        flags = struct.unpack(">I", hdr[0x10:0x14])[0]
    except Exception:
        return None

    window_hex = " ".join(f"{b:02X}" for b in hdr[:0x30])
    return {
        "radius": radius,
        "x": x_offset,
        "y": y_const,
        "flags": flags,
        "window": window_hex,
    }


# ---------------------------------------------------------------------
# Collect moves from scan_normals_all (with grouping + house info)
# ---------------------------------------------------------------------

def collect_moves_from_scan(scan_data, label_db):
    """
    Flatten scan_normals_all.scan_once() output into a list of grouped moves:

      {
        'slot', 'move_name', 'char_name',
        'anim_id', 'id0',
        'records': [
            {
              'base', 'pat_off',
              'ctrl_window',   # around ID0 pattern, if any
              'house_window',  # house header window, if any
            },
            ...
        ],
        'label',
        'house_window'  # canonical for the group (first non-empty)
      }

    We group by (slot_label, char_name, anim_id, id0) so duplicates
    (like Chun's four 5A entries) become one row with multiple ABSes.

    IMPORTANT CHANGE:
    - A move is included if it has *either*:
        - an ID0 control pattern, or
        - a house struct (04 01 60 ...).
    So your scanner-style house hits now appear in this list.
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

            # ID0 pattern (control)
            id_off, id0 = find_control_pattern(base)

            # House struct (same logic as scanner.py)
            house = decode_house(base)

            # If we find neither, skip this move; nothing interesting.
            if id_off is None and house is None:
                continue

            # Control window (for ID0 pattern)
            ctrl_bytes = control_window_bytes(base, id_off, radius=8) if id_off is not None else b""
            ctrl_hex = " ".join(f"{b:02X}" for b in ctrl_bytes) if ctrl_bytes else ""

            # House window
            house_hex = house["window"] if house is not None else ""

            # Label keyed by ID0 (if present)
            if id0 is not None:
                id0_hex = f"{id0:02X}"
                label = get_move_label(label_db, char_name, id0_hex)
            else:
                label = ""

            move_name = SCAN_ANIM_MAP.get(anim_id, f"anim_{anim_id:02X}")

            key = (slot_label, char_name, anim_id, id0)

            if key not in grouped:
                grouped[key] = {
                    "slot": slot_label,
                    "move_name": move_name,
                    "char_name": char_name,
                    "anim_id": anim_id,
                    "id0": id0,
                    "records": [],
                    "label": label,
                    "ctrl_window": "",   # filled from first record with data
                    "house_window": "",  # filled from first record with data
                }

            rec = {
                "base": base,
                "pat_off": id_off,
                "ctrl_window": ctrl_hex,
                "house_window": house_hex,
            }
            grouped[key]["records"].append(rec)

            # Only set these if we don't already have a non-empty one
            if ctrl_hex and not grouped[key]["ctrl_window"]:
                grouped[key]["ctrl_window"] = ctrl_hex
            if house_hex and not grouped[key]["house_window"]:
                grouped[key]["house_window"] = house_hex

    # Flatten into list and sort
    moves = []
    for g in grouped.values():
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
        self.root.title("TvC Move Control / Label Editor (grouped clones + house)")

        self.moves = moves
        self.label_db = label_db  # {char_key/_generic: {id0_hex: label}}

        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            top,
            text=(
                "All moves from scan_normals_all, grouped by slot/char/anim/ID0. "
                "ABS lists every matching record; editing ID0 writes to all of them. "
                "House column shows 04 01 60 blocks (0B F0 33 35 20 3F etc)."
            ),
        ).grid(row=0, column=0, sticky="w")

        cols = ("slot", "move", "anim", "abs", "id0", "label", "ctrl", "house")
        self.tree = ttk.Treeview(main, columns=cols, show="headings", height=24)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(4, 4))

        vsb = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.grid(row=1, column=1, sticky="ns", pady=(4, 4))

        self.tree.heading("slot", text="Slot")
        self.tree.heading("move", text="Move")
        self.tree.heading("anim", text="Anim")
        self.tree.heading("abs", text="ABS (all clones)")
        self.tree.heading("id0", text="ID0 (XX)")
        self.tree.heading("label", text="Label")
        self.tree.heading("ctrl", text="control window (ID0)")
        self.tree.heading("house", text="house window (04 01 60)")

        self.tree.column("slot", width=60, anchor="w")
        self.tree.column("move", width=140, anchor="w")
        self.tree.column("anim", width=60, anchor="center")
        self.tree.column("abs", width=260, anchor="w")
        self.tree.column("id0", width=60, anchor="center")
        self.tree.column("label", width=260, anchor="w")
        self.tree.column("ctrl", width=320, anchor="w")
        self.tree.column("house", width=320, anchor="w")

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
            bottom,
            text="Write ID0 to all clones + Save Label",
            command=self.on_write,
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
            anim_id = mv.get("anim_id", 0)

            bases = [rec["base"] for rec in mv["records"]]
            abs_str = ", ".join(f"0x{b:08X}" for b in bases)

            ctrl_hex = mv.get("ctrl_window", "") or ""
            house_hex = mv.get("house_window", "") or ""

            id0_str = "--" if id0 is None else f"{id0:02X}"
            anim_str = f"{anim_id:02X}"

            self.tree.insert(
                "",
                "end",
                values=(slot, move_name, anim_str, abs_str, id0_str, label, ctrl_hex, house_hex),
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

        id0 = mv.get("id0")
        self.id0_entry.delete(0, tk.END)
        if id0 is not None:
            self.id0_entry.insert(0, f"{id0:02X}")
        else:
            self.id0_entry.insert(0, "")

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

        if not id0_txt:
            messagebox.showerror("ID0 empty", "ID0 must be a hex byte (00..FF).")
            return

        try:
            new_id0 = int(id0_txt, 16) & 0xFF
        except ValueError:
            messagebox.showerror("Parse error", "ID0 must be a hex byte (00..FF).")
            return

        # fan-out write: update every clone record
        try:
            for rec in records:
                base = rec["base"]
                id_off = rec["pat_off"]  # offset of ID byte (may be None if no control pattern)

                if id_off is None:
                    # no control pattern for this clone; skip writing ID0
                    continue

                if not addr_in_ram(base):
                    raise RuntimeError(f"ABS 0x{base:08X} not in MEM1/MEM2.")

                id0_addr = base + id_off
                if not wd8(id0_addr, new_id0):
                    raise RuntimeError(f"wd8 failed at 0x{id0_addr:08X}")
        except Exception as e:
            messagebox.showerror("Write error", str(e))
            return

        # update in-memory record
        mv["id0"] = new_id0
        mv["label"] = label_txt

        # store label if we have a char name (per-char entry, not _generic)
        if char_name:
            ckey = _norm_char_key(char_name)
            self.label_db.setdefault(ckey, {})
            self.label_db[ckey][f"{new_id0:02X}"] = label_txt
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
            f"Wrote ID0={new_id0:02X} to clones with control patterns ({len(records)} entries; "
            f"some clones may be house-only and skipped).\n"
            f"ABS list: {abs_str}\n"
            f"Label saved.",
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
    print(f"Found {len(moves)} grouped moves with control and/or house patterns.")

    root = tk.Tk()
    MoveControlGUI(root, moves, label_db)
    root.mainloop()


if __name__ == "__main__":
    main()
