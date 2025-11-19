#!/usr/bin/env python
#
# TvC House / Control Explorer GUI (with orphan house scan)
#
# Pass 1: use scan_normals_all.scan_once() to get normal moves.
#         For each move, try to find:
#             - control pattern (ID0) near ABS
#             - 04 01 60 "house" block near ABS
#
# Pass 2: for each slot, compute a "house region" based on the house
#         addresses found in pass 1, then scan that region directly
#         for *additional* 04 01 60 blocks that aren't attached to
#         any move. These become "orphan house" rows.
#
# GUI shows everything:
#   - Slot, Char, Anim, Move name, ABS, ID0, Label
#   - ctrl offset/window (where present)
#   - house offset/window (for attached + orphan houses)
#

import sys
import os
import json
import tkinter as tk
from tkinter import ttk, messagebox

# ---------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(__file__))  # ...\tdp-modules
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dolphin_io import hook, rbytes, wd8, addr_in_ram  # type: ignore
import scan_normals_all  # type: ignore
from scan_normals_all import ANIM_MAP as SCAN_ANIM_MAP  # type: ignore

LABELS_JSON = "move_labels.json"

MAGIC_HOUSE = b"\x04\x01\x60"

# ---------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------

def load_label_db(path: str = LABELS_JSON):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
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
    generic = label_db.get("_generic", {})
    per_char_raw = {k: v for k, v in label_db.items() if k != "_generic"}
    ckey = _norm_char_key(char_name)
    per_char = per_char_raw.get(ckey, {}) if ckey else {}
    return per_char.get(id0_hex) or generic.get(id0_hex, "")

# ---------------------------------------------------------------------
# Control / house helpers
# ---------------------------------------------------------------------

def find_control_pattern(base: int, window_size: int = 0x80):
    """
    Control pattern as in your earlier scripts.

    Pass 1 (strict grounded):
        00 00 00 00 01 XX 01 3C

    Pass 2 (air / variants):
        01 XX 01 3C
        01 XX 01 3F

    Returns (id_off, id0) relative to base, or (None, None).
    """
    data = rbytes(base, window_size) or b""
    n = len(data)
    if n < 4:
        return None, None

    # strict grounded pattern
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
                    return i + 5, id0

    # looser air / variants
    for i in range(n - 3):
        if data[i] == 0x01 and data[i + 2] == 0x01 and data[i + 3] in (0x3C, 0x3F):
            id0 = data[i + 1]
            if 0x00 <= id0 <= 0xBE:
                return i + 1, id0

    return None, None


def control_window_bytes(base: int, id_off: int, radius: int = 8):
    if id_off is None:
        return b""
    start = max(0, id_off - radius)
    size = radius * 2 + 1
    return rbytes(base + start, size) or b""


def find_house_near_base(base: int, search_back: int = 0x40,
                         search_forward: int = 0x200,
                         window_radius: int = 0x40):
    """
    Look for 04 01 60 in [base - search_back, base + search_forward).

    Returns (house_off, window_bytes) or (None, b"").
    """
    if not addr_in_ram(base):
        return None, b""

    start = base - search_back
    if start < 0:
        start = 0
    size = search_back + search_forward

    data = rbytes(start, size) or b""
    if not data:
        return None, b""

    idx = data.find(MAGIC_HOUSE)
    if idx == -1:
        return None, b""

    # Offset from base
    house_off = (start + idx) - base

    w_start = max(0, idx - window_radius)
    w_end   = min(len(data), idx + window_radius)
    window  = data[w_start:w_end]

    return house_off, window


def scan_region_for_houses(start_addr: int, end_addr: int,
                           known_house_addrs: set,
                           window_radius: int = 0x40):
    """
    Scan [start_addr, end_addr) for all 04 01 60 blocks.

    Returns list of (house_addr, window_bytes) for any address that
    is NOT already in known_house_addrs.
    """
    if end_addr <= start_addr:
        return []

    if not addr_in_ram(start_addr):
        return []

    size = end_addr - start_addr
    data = rbytes(start_addr, size) or b""
    if not data:
        return []

    results = []
    idx = 0
    while True:
        idx = data.find(MAGIC_HOUSE, idx)
        if idx == -1:
            break

        house_addr = start_addr + idx
        if house_addr not in known_house_addrs:
            w_start = max(0, idx - window_radius)
            w_end   = min(len(data), idx + window_radius)
            window  = data[w_start:w_end]
            results.append((house_addr, window))

        idx += 1  # move forward so we can find overlapping hits if any

    return results

# ---------------------------------------------------------------------
# Collection logic
# ---------------------------------------------------------------------

def collect_moves_and_houses(scan_data, label_db):
    """
    Pass 1:
      - Flatten scan_normals_all output into rows with:
        slot / char / anim / move_name / base / id0 / label /
        ctrl_off / house_off / ctrl_window / house_window.

      - Also compute per-slot house address bands for a 2nd pass.
    """
    rows = []

    # For building per-slot regions
    slot_house_addrs = {}   # slot -> set of house addresses
    slot_chars       = {}   # slot -> representative char name

    total_moves = 0

    for slot_info in scan_data:
        slot_label = slot_info.get("slot_label", "?")
        char_name  = (slot_info.get("char_name") or "").strip()

        if slot_label not in slot_chars and char_name:
            slot_chars[slot_label] = char_name

        for mv in slot_info.get("moves", []):
            total_moves += 1
            base    = mv.get("abs")
            anim_id = mv.get("id")
            if base is None or anim_id is None:
                continue
            if not addr_in_ram(base):
                continue

            # Control pattern / ID0
            ctrl_off, id0 = find_control_pattern(base)
            if id0 is not None:
                id0_hex = f"{id0:02X}"
                label = get_move_label(label_db, char_name, id0_hex)
            else:
                label = ""

            ctrl_win = control_window_bytes(base, ctrl_off, radius=8) if ctrl_off is not None else b""

            # House near this base
            house_off, house_win = find_house_near_base(base)

            if house_off is not None:
                house_addr = base + house_off
                slot_house_addrs.setdefault(slot_label, set()).add(house_addr)

            move_name = SCAN_ANIM_MAP.get(anim_id, f"anim_{anim_id:02X}")

            rows.append(
                {
                    "slot":         slot_label,
                    "char":         char_name,
                    "anim":         anim_id,
                    "move_name":    move_name,
                    "base":         base,
                    "id0":          id0,
                    "label":        label,
                    "ctrl_off":     ctrl_off,
                    "house_off":    house_off,
                    "ctrl_window":  ctrl_win,
                    "house_window": house_win,
                    "is_orphan":    False,
                }
            )

    print(f"DEBUG: total moves from scan_normals_all: {total_moves}")
    print(f"DEBUG: pass 1 rows: {len(rows)}")

    return rows, slot_house_addrs, slot_chars


def add_orphan_houses(rows, slot_house_addrs, slot_chars):
    """
    Pass 2:
      For each slot, use min/max house addresses to define a region,
      expand it a bit, scan directly for *all* 04 01 60 blocks, and
      add "orphan" rows for any house not attached to a move.
    """
    # Build lookup of already-known house addresses
    known_house_addrs = set()
    for r in rows:
        house_off = r.get("house_off")
        if house_off is not None:
            known_house_addrs.add(r["base"] + house_off)

    # For each slot that has any attached houses, scan region
    for slot_label, addr_set in slot_house_addrs.items():
        if not addr_set:
            continue

        base_min = min(addr_set)
        base_max = max(addr_set)

        # Expand the band to catch nearby orphan cores.
        # You can tweak these margins if you want more/less noise.
        margin = 0x800
        start_addr = max(0, base_min - margin)
        end_addr   = base_max + margin

        print(
            f"DEBUG: slot {slot_label} house region "
            f"[0x{start_addr:08X}, 0x{end_addr:08X}) "
            f"from {len(addr_set)} attached houses"
        )

        orphans = scan_region_for_houses(
            start_addr, end_addr, known_house_addrs, window_radius=0x40
        )

        char_name = slot_chars.get(slot_label, "")

        for house_addr, window in orphans:
            rows.append(
                {
                    "slot":         slot_label,
                    "char":         char_name,
                    "anim":         None,
                    "move_name":    "(orphan house)",
                    "base":         house_addr,   # here ABS is house_addr
                    "id0":          None,
                    "label":        "",
                    "ctrl_off":     None,
                    "house_off":    0,            # ABS is already the house
                    "ctrl_window":  b"",
                    "house_window": window,
                    "is_orphan":    True,
                }
            )
            known_house_addrs.add(house_addr)

        print(f"DEBUG: slot {slot_label} orphan houses added: {len(orphans)}")

    # Sort again including new rows
    rows.sort(
        key=lambda r: (
            r["slot"],
            _norm_char_key(r["char"]),
            -1 if r["anim"] is None else r["anim"],
            r["base"],
        )
    )
    return rows

# ---------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------

class HouseExplorerGUI:
    def __init__(self, root, rows, label_db):
        self.root = root
        self.root.title("TvC House / Control Explorer (with orphans)")

        self.rows = rows
        self.label_db = label_db

        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(
            top,
            text=(
                "All moves from scan_normals_all plus orphan 04 01 60 blocks "
                "near each slot's house region. Orphans have no ID0/anim yet "
                "but give you ABS + house window for Tatsu/Shoryu/etc hunting."
            ),
        ).grid(row=0, column=0, sticky="w")

        cols = (
            "slot", "char", "anim", "move",
            "abs", "id0", "label",
            "ctrl_off", "house_off",
            "ctrl", "house",
            "orphan",
        )

        self.tree = ttk.Treeview(main, columns=cols, show="headings", height=28)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(4, 4))

        vsb = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.grid(row=1, column=1, sticky="ns", pady=(4, 4))

        # headings
        self.tree.heading("slot",  text="Slot")
        self.tree.heading("char",  text="Char")
        self.tree.heading("anim",  text="Anim")
        self.tree.heading("move",  text="Move name")
        self.tree.heading("abs",   text="ABS")
        self.tree.heading("id0",   text="ID0 (XX)")
        self.tree.heading("label", text="Label")
        self.tree.heading("ctrl_off",  text="ctrl off")
        self.tree.heading("house_off", text="house off")
        self.tree.heading("ctrl",  text="control window (ID0)")
        self.tree.heading("house", text="house window (04 01 60)")
        self.tree.heading("orphan", text="orphan?")

        # columns
        self.tree.column("slot",  width=60,  anchor="w")
        self.tree.column("char",  width=110, anchor="w")
        self.tree.column("anim",  width=50,  anchor="center")
        self.tree.column("move",  width=140, anchor="w")
        self.tree.column("abs",   width=110, anchor="w")
        self.tree.column("id0",   width=60,  anchor="center")
        self.tree.column("label", width=220, anchor="w")
        self.tree.column("ctrl_off",  width=70, anchor="center")
        self.tree.column("house_off", width=80, anchor="center")
        self.tree.column("ctrl",  width=320, anchor="w")
        self.tree.column("house", width=320, anchor="w")
        self.tree.column("orphan", width=60, anchor="center")

        bottom = ttk.Frame(main, padding=(0, 4, 0, 0))
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew")

        ttk.Label(bottom, text="Selected ABS:").grid(row=0, column=0, sticky="e")
        self.sel_abs_var = tk.StringVar(value="0x00000000")
        ttk.Label(bottom, textvariable=self.sel_abs_var, width=18).grid(
            row=0, column=1, sticky="w", padx=(4, 12)
        )

        ttk.Label(bottom, text="Char:").grid(row=0, column=2, sticky="e")
        self.sel_char_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.sel_char_var, width=12).grid(
            row=0, column=3, sticky="w", padx=(4, 12)
        )

        ttk.Label(bottom, text="Anim:").grid(row=0, column=4, sticky="e")
        self.sel_anim_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.sel_anim_var, width=6).grid(
            row=0, column=5, sticky="w", padx=(4, 12)
        )

        ttk.Label(bottom, text="ID0 (hex):").grid(row=0, column=6, sticky="e")
        self.id0_entry = ttk.Entry(bottom, width=4)
        self.id0_entry.grid(row=0, column=7, sticky="w", padx=(2, 12))

        ttk.Label(bottom, text="Label:").grid(row=0, column=8, sticky="e")
        self.label_entry = ttk.Entry(bottom, width=40)
        self.label_entry.grid(row=0, column=9, sticky="w", padx=(2, 8))

        self.write_btn = ttk.Button(
            bottom,
            text="Write ID0 + Save Label",
            command=self.on_write,
        )
        self.write_btn.grid(row=0, column=10, padx=(8, 4))

        self.reload_btn = ttk.Button(bottom, text="Reload scan", command=self.on_reload_scan)
        self.reload_btn.grid(row=0, column=11, padx=(4, 0))

        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)

        self._populate_tree()
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

    # ------------------------------------------------------

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        for row in self.rows:
            slot      = row["slot"]
            char      = row["char"] or ""
            anim      = row["anim"]
            anim_hex  = "--" if anim is None else f"{anim:02X}"
            move_name = row["move_name"]
            base      = row["base"]

            id0       = row["id0"]
            id0_str   = "--" if id0 is None else f"{id0:02X}"
            label     = row.get("label", "") or ""

            ctrl_off  = row.get("ctrl_off")
            house_off = row.get("house_off")

            ctrl_win  = row.get("ctrl_window") or b""
            house_win = row.get("house_window") or b""

            ctrl_hex  = " ".join(f"{b:02X}" for b in ctrl_win)
            house_hex = " ".join(f"{b:02X}" for b in house_win)

            ctrl_off_str  = "--" if ctrl_off is None else f"+0x{ctrl_off:X}"
            house_off_str = "--" if house_off is None else f"+0x{house_off:X}"

            orphan_str = "yes" if row.get("is_orphan") else ""

            self.tree.insert(
                "",
                "end",
                values=(
                    slot,
                    char,
                    anim_hex,
                    move_name,
                    f"0x{base:08X}",
                    id0_str,
                    label,
                    ctrl_off_str,
                    house_off_str,
                    ctrl_hex,
                    house_hex,
                    orphan_str,
                ),
            )

    def _get_selected_index(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        return self.tree.index(iid)

    def _get_selected_row(self):
        idx = self._get_selected_index()
        if idx is None or idx < 0 or idx >= len(self.rows):
            return None
        return self.rows[idx]

    def on_select(self, event):
        row = self._get_selected_row()
        if not row:
            self.sel_abs_var.set("0x00000000")
            self.sel_char_var.set("")
            self.sel_anim_var.set("")
            self.id0_entry.delete(0, tk.END)
            self.label_entry.delete(0, tk.END)
            return

        self.sel_abs_var.set(f"0x{row['base']:08X}")
        self.sel_char_var.set(row.get("char", "") or "")
        anim = row.get("anim")
        self.sel_anim_var.set("--" if anim is None else f"{anim:02X}")

        id0 = row.get("id0")
        self.id0_entry.delete(0, tk.END)
        if id0 is not None:
            self.id0_entry.insert(0, f"{id0:02X}")

        label = row.get("label", "") or ""
        self.label_entry.delete(0, tk.END)
        self.label_entry.insert(0, label)

    def on_write(self):
        row = self._get_selected_row()
        if not row:
            messagebox.showerror("No selection", "Select a move first.")
            return

        if row.get("is_orphan"):
            messagebox.showerror(
                "Orphan block",
                "This row is an orphan 04 01 60 block with no control pattern. "
                "There's no safe ID0 byte to write here.",
            )
            return

        base    = row["base"]
        ctrl_off = row.get("ctrl_off")

        if ctrl_off is None:
            messagebox.showerror(
                "No control byte",
                "This row has no detected control pattern / ID0. "
                "The GUI cannot safely write an ID0 here.",
            )
            return

        id0_txt   = self.id0_entry.get().strip()
        label_txt = self.label_entry.get().strip()
        char_name = (row.get("char") or "").strip()

        try:
            new_id0 = int(id0_txt, 16) & 0xFF
        except ValueError:
            messagebox.showerror("Parse error", "ID0 must be a hex byte (00..FF).")
            return

        try:
            if not addr_in_ram(base):
                raise RuntimeError(f"ABS 0x{base:08X} not in MEM1/MEM2.")
            id0_addr = base + ctrl_off
            if not wd8(id0_addr, new_id0):
                raise RuntimeError(f"wd8 failed at 0x{id0_addr:08X}")
        except Exception as e:
            messagebox.showerror("Write error", str(e))
            return

        # Update row + label db
        row["id0"] = new_id0
        row["label"] = label_txt

        if char_name:
            ckey = _norm_char_key(char_name)
            self.label_db.setdefault(ckey, {})
            self.label_db[ckey][f"{new_id0:02X}"] = label_txt
            save_label_db(self.label_db)

        # Refresh tree row
        idx = self._get_selected_index()
        if idx is not None:
            self._populate_tree()
            children = self.tree.get_children()
            if 0 <= idx < len(children):
                self.tree.selection_set(children[idx])
                self.tree.see(children[idx])

        messagebox.showinfo(
            "Done",
            f"Wrote ID0={new_id0:02X} at ABS 0x{base:08X} (offset +0x{ctrl_off:X}) "
            f"and saved label.",
        )

    def on_reload_scan(self):
        messagebox.showinfo(
            "Reload",
            "Close and re-run this script to rescan.",
        )

# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    print("Hooking Dolphin...")
    hook()
    print("Hooked. Running scan_normals_all.scan_once() for metadata...")

    try:
        scan_data = scan_normals_all.scan_once()
    except Exception as e:
        print("scan_once failed:", e)
        sys.exit(1)

    label_db = load_label_db()

    # Pass 1: regular moves + attached houses
    rows, slot_house_addrs, slot_chars = collect_moves_and_houses(scan_data, label_db)

    # Pass 2: orphan houses inside each slot's house region
    rows = add_orphan_houses(rows, slot_house_addrs, slot_chars)
    print(f"Total rows for GUI (moves + orphans): {len(rows)}")

    root = tk.Tk()
    HouseExplorerGUI(root, rows, label_db)
    root.mainloop()


if __name__ == "__main__":
    main()
