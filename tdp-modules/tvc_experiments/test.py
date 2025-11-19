#!/usr/bin/env python

#
# TvC Caller Pattern Labeler
#
# - Uses scan_normals_all.scan_once() to discover which slots/chars are active
#   and to build approximate address ranges per slot (P1-C1, P1-C2, etc.).
# - Within each slot range, scans RAM for the animation-call pattern:
#
#       01 XX 01 3C
#
#   where XX is the “ID” byte you care about.
#
# - Groups hits by (slot, character, ID0) = C-choice grouping:
#       ( "P1-C1", "Ryu", 0x0D ) -> list of hit addresses
#
# - Shows them in a Tk GUI:
#       Slot | Char | ID0 | Count | Addresses | Label | Bytes
#
# - Labels are stored in caller_labels.json at the tdp-modules root:
#       {
#         "_generic": { "0D": "Light Shoryu" },
#         "ryu":      { "0D": "Light Shoryu" }
#       }
#
#   You edit the Label column via a text box; it persists across runs.
#

import os
import sys
import json
import tkinter as tk
from tkinter import ttk, messagebox

# ------------------------------------------------------------
# Import from tdp-modules root
# ------------------------------------------------------------

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from dolphin_io import hook, rbytes, addr_in_ram
    import scan_normals_all
except Exception as e:
    print("Import failure in caller pattern labeler:", e)
    sys.exit(1)

CALLER_LABELS_JSON = os.path.join(ROOT, "caller_labels.json")

PATTERN_LEN = 4  # 01 XX 01 3C


# ------------------------------------------------------------
# Label DB helpers
# ------------------------------------------------------------

def load_label_db(path: str = CALLER_LABELS_JSON):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_label_db(db, path: str = CALLER_LABELS_JSON):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, sort_keys=True)
    except Exception as e:
        print("Failed to save caller label db:", e)


def _norm_char_key(name: str) -> str:
    name = (name or "").strip().lower()
    for ch in (" ", "-", "_", "."):
        name = name.replace(ch, "")
    return name


def get_caller_label(label_db, char_name: str, id0_hex: str) -> str:
    """
    Per-character label, then _generic fallback.

    label_db format:
      {
        "_generic": {"0D": "Light Shoryu", ...},
        "ryu":      {"0D": "Light Shoryu", ...}
      }
    """
    if not label_db:
        return ""

    generic = label_db.get("_generic", {})
    ckey = _norm_char_key(char_name)
    per_char = label_db.get(ckey, {}) if ckey else {}

    # Try uppercase then lowercase
    key_up = id0_hex.upper()
    key_lo = id0_hex.lower()

    return (
        per_char.get(key_up)
        or per_char.get(key_lo)
        or generic.get(key_up)
        or generic.get(key_lo)
        or ""
    )


# ------------------------------------------------------------
# Slot range builder (from scan_normals_all)
# ------------------------------------------------------------

def build_slot_ranges(scan_data, padding: int = 0x4000):
    """
    Build approximate address ranges per slot from scan_normals_all.scan_once():

      {
        "P1-C1": {"char": "Ryu", "lo": 0x908AAC30, "hi": 0x90908CEC},
        ...
      }

    We just use min/max of the 'abs' fields per slot, padded a bit.
    """
    per_slot_addrs = {}   # slot_label -> [abs, abs, ...]
    per_slot_char = {}    # slot_label -> char_name

    for slot_info in scan_data:
        slot_label = slot_info.get("slot_label", "?")
        char_name = (slot_info.get("char_name") or "").strip()

        if slot_label not in per_slot_char:
            per_slot_char[slot_label] = char_name

        for mv in slot_info.get("moves", []):
            base = mv.get("abs")
            if base is None:
                continue
            if not addr_in_ram(base):
                continue
            per_slot_addrs.setdefault(slot_label, []).append(base)

    slot_ranges = {}
    for slot_label, addrs in per_slot_addrs.items():
        if not addrs:
            continue
        lo = min(addrs) - padding
        hi = max(addrs) + padding
        # We rely on dolphin_io.rbytes clamping to MEM1/MEM2 for edges.
        slot_ranges[slot_label] = {
            "char": per_slot_char.get(slot_label, ""),
            "lo": lo,
            "hi": hi,
        }

    return slot_ranges


# ------------------------------------------------------------
# Scan per-slot ranges for 01 XX 01 3C
# ------------------------------------------------------------

def scan_slot_ranges_for_callers(slot_ranges, label_db):
    """
    Scan each slot's [lo, hi) range for the pattern:

        01 XX 01 3C

    where XX is the byte we treat as the caller ID.

    Returns:
      groups: list of dicts, each:
        {
          "slot": "P1-C1",
          "char": "Ryu",
          "id0":  0x0D,
          "addresses": [0x..., 0x..., ...],
          "label": "Light Shoryu",   # from caller_labels.json, may be ""
          "bytes": "hex context sample",
        }
      total_hits: total raw pattern hits before grouping
    """
    groups = {}
    total_hits = 0

    # pattern: 01 XX 01 3C with wildcard at index 1
    def is_match(buf, i):
        return (
            buf[i] == 0x01 and
            buf[i + 2] == 0x01 and
            buf[i + 3] == 0x3C
        )

    for slot_label, info in slot_ranges.items():
        char_name = info.get("char", "")
        lo = info.get("lo")
        hi = info.get("hi")

        if lo is None or hi is None or lo >= hi:
            continue

        print(f"Scanning {slot_label} ({char_name}) range "
              f"[0x{lo:08X}, 0x{hi:08X}) for 01 XX 01 3C...")

        addr = lo
        tail = b""

        while addr < hi:
            # Small-ish chunk for responsiveness
            chunk_size = 0x400
            remaining = hi - addr
            size = chunk_size if remaining > chunk_size else remaining
            if size <= 0:
                break

            data = rbytes(addr, size)
            if not data:
                # If rbytes fails, move on
                addr += size
                tail = b""
                continue

            buf = tail + data
            base_for_buf = addr - len(tail)

            i = 0
            n = len(buf)
            # Need at least 4 bytes for the pattern
            while i <= n - PATTERN_LEN:
                if is_match(buf, i):
                    id0 = buf[i + 1]
                    hit_addr = base_for_buf + i
                    total_hits += 1

                    key = (slot_label, char_name, id0)
                    g = groups.get(key)
                    if g is None:
                        id0_hex = f"{id0:02X}"
                        label = get_caller_label(label_db, char_name, id0_hex)
                        g = {
                            "slot": slot_label,
                            "char": char_name,
                            "id0": id0,
                            "addresses": [],
                            "label": label,
                            "bytes": None,
                        }
                        groups[key] = g

                    g["addresses"].append(hit_addr)

                    # Capture a context sample (first time for this group)
                    if g["bytes"] is None:
                        # try to grab a little before and after
                        ctx_start = hit_addr - 8
                        ctx_size = 32
                        ctx = rbytes(ctx_start, ctx_size) or b""
                        g["bytes"] = " ".join(f"{b:02X}" for b in ctx)

                    # Advance by 1 byte; overlapping matches are unlikely but cheap to allow
                    i += 1
                else:
                    i += 1

            # Preserve last 3 bytes as tail to catch patterns across chunk boundaries
            if len(buf) >= PATTERN_LEN - 1:
                tail = buf[-(PATTERN_LEN - 1):]
            else:
                tail = buf

            addr += size

    # Flatten and sort
    groups_list = list(groups.values())
    groups_list.sort(
        key=lambda g: (g["slot"], _norm_char_key(g["char"]), g["id0"])
    )

    return groups_list, total_hits


# ------------------------------------------------------------
# GUI
# ------------------------------------------------------------

class CallerLabelGUI:
    def __init__(self, root, groups, label_db):
        self.root = root
        self.root.title("TvC Caller Pattern Labeler (01 XX 01 3C by slot/char/ID0)")

        self.groups = groups  # list of group dicts
        self.label_db = label_db

        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main)
        top.grid(row=0, column=0, sticky="ew")

        ttk.Label(
            top,
            text=(
                "Animation caller blocks found via 01 XX 01 3C pattern, grouped by (Slot, Char, ID0).\n"
                "Edit labels per character + ID0; labels are stored in caller_labels.json at the tdp-modules root."
            ),
        ).grid(row=0, column=0, sticky="w")

        cols = ("slot", "char", "id0", "count", "addrs", "label", "bytes")
        self.tree = ttk.Treeview(main, columns=cols, show="headings", height=26)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(4, 4))

        vsb = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.grid(row=1, column=1, sticky="ns", pady=(4, 4))

        self.tree.heading("slot", text="Slot")
        self.tree.heading("char", text="Char")
        self.tree.heading("id0", text="ID0 (hex)")
        self.tree.heading("count", text="#Hits")
        self.tree.heading("addrs", text="Addresses (caller pattern)")
        self.tree.heading("label", text="Label")
        self.tree.heading("bytes", text="Context bytes (sample)")

        self.tree.column("slot", width=70, anchor="w")
        self.tree.column("char", width=140, anchor="w")
        self.tree.column("id0", width=70, anchor="center")
        self.tree.column("count", width=60, anchor="center")
        self.tree.column("addrs", width=260, anchor="w")
        self.tree.column("label", width=260, anchor="w")
        self.tree.column("bytes", width=420, anchor="w")

        bottom = ttk.Frame(main, padding=(0, 4, 0, 0))
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew")

        ttk.Label(bottom, text="Selected Char:").grid(row=0, column=0, sticky="e")
        self.sel_char_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.sel_char_var, width=16).grid(
            row=0, column=1, sticky="w", padx=(4, 12)
        )

        ttk.Label(bottom, text="ID0 (hex):").grid(row=0, column=2, sticky="e")
        self.sel_id0_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.sel_id0_var, width=6).grid(
            row=0, column=3, sticky="w", padx=(4, 12)
        )

        ttk.Label(bottom, text="Hits:").grid(row=0, column=4, sticky="e")
        self.sel_count_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.sel_count_var, width=6).grid(
            row=0, column=5, sticky="w", padx=(4, 12)
        )

        ttk.Label(bottom, text="Addresses:").grid(row=1, column=0, sticky="e")
        self.sel_addrs_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.sel_addrs_var, width=80).grid(
            row=1, column=1, columnspan=5, sticky="w", padx=(4, 12)
        )

        ttk.Label(bottom, text="Label:").grid(row=2, column=0, sticky="e")
        self.label_entry = ttk.Entry(bottom, width=60)
        self.label_entry.grid(row=2, column=1, columnspan=4, sticky="w", padx=(4, 12))

        self.save_btn = ttk.Button(bottom, text="Save Label", command=self.on_save_label)
        self.save_btn.grid(row=2, column=5, padx=(4, 4))

        self.rescan_btn = ttk.Button(bottom, text="Rescan", command=self.on_rescan)
        self.rescan_btn.grid(row=2, column=6, padx=(4, 0))

        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)

        self._populate_tree()
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

    # ---------------- internal helpers ----------------

    def _populate_tree(self):
        self.tree.delete(*self.tree.get_children())
        for g in self.groups:
            slot = g["slot"]
            char = g["char"]
            id0 = g["id0"]
            id0_str = f"{id0:02X}"
            count = len(g["addresses"])
            label = g.get("label", "") or ""
            bytes_str = g.get("bytes", "") or ""

            # Show up to a few addresses; you can still scroll the full string
            addr_str = ", ".join(f"0x{a:08X}" for a in g["addresses"])

            self.tree.insert(
                "",
                "end",
                values=(slot, char, id0_str, count, addr_str, label, bytes_str),
            )

    def _get_selected_index(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        return self.tree.index(iid)

    def _get_selected_group(self):
        idx = self._get_selected_index()
        if idx is None or idx < 0 or idx >= len(self.groups):
            return None
        return self.groups[idx]

    # ---------------- event handlers ----------------

    def on_select(self, event):
        g = self._get_selected_group()
        if not g:
            self.sel_char_var.set("")
            self.sel_id0_var.set("")
            self.sel_count_var.set("")
            self.sel_addrs_var.set("")
            self.label_entry.delete(0, tk.END)
            return

        self.sel_char_var.set(g["char"])
        self.sel_id0_var.set(f"{g['id0']:02X}")
        self.sel_count_var.set(str(len(g["addresses"])))
        self.sel_addrs_var.set(
            ", ".join(f"0x{a:08X}" for a in g["addresses"])
        )

        self.label_entry.delete(0, tk.END)
        self.label_entry.insert(0, g.get("label", "") or "")

    def on_save_label(self):
        g = self._get_selected_group()
        if not g:
            messagebox.showerror("No selection", "Select a caller group first.")
            return

        char_name = (g["char"] or "").strip()
        if not char_name:
            messagebox.showerror("No character", "Selected entry has no character name.")
            return

        label_txt = self.label_entry.get().strip()
        id0_hex = f"{g['id0']:02X}"

        # Update in-memory group
        g["label"] = label_txt

        # Update label DB
        ckey = _norm_char_key(char_name)
        self.label_db.setdefault(ckey, {})
        self.label_db[ckey][id0_hex] = label_txt
        save_label_db(self.label_db)

        # Refresh tree row to show updated label
        idx = self._get_selected_index()
        if idx is not None:
            self._populate_tree()
            children = self.tree.get_children()
            if 0 <= idx < len(children):
                self.tree.selection_set(children[idx])
                self.tree.see(children[idx])

        messagebox.showinfo(
            "Saved",
            f"Saved label for {char_name}, ID0={id0_hex}: \"{label_txt}\"",
        )

    def on_rescan(self):
        try:
            print("Rescanning caller patterns...")
            hook()
            scan_data = scan_normals_all.scan_once()
            slot_ranges = build_slot_ranges(scan_data)
            new_label_db = load_label_db()
            new_groups, total_hits = scan_slot_ranges_for_callers(slot_ranges, new_label_db)

            self.groups = new_groups
            self.label_db = new_label_db
            self._populate_tree()

            messagebox.showinfo(
                "Rescan complete",
                f"Total raw 01 XX 01 3C hits: {total_hits}\n"
                f"Grouped entries: {len(self.groups)}",
            )
        except Exception as e:
            messagebox.showerror("Rescan failed", str(e))


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    print("Hooking Dolphin...")
    hook()
    print("Hooked. Running scan_normals_all.scan_once() to build slot ranges...")

    try:
        scan_data = scan_normals_all.scan_once()
    except Exception as e:
        print("scan_normals_all.scan_once() failed:", e)
        sys.exit(1)

    slot_ranges = build_slot_ranges(scan_data)
    if not slot_ranges:
        print("No slot ranges found; are you in a match?")
        sys.exit(1)

    print("Slot ranges built:")
    for slot_label, info in slot_ranges.items():
        char_name = info.get("char", "")
        lo = info.get("lo")
        hi = info.get("hi")
        print(f"  {slot_label}: {char_name}  [0x{lo:08X}, 0x{hi:08X})")

    label_db = load_label_db()

    print("Scanning per-slot ranges for caller blocks (01 XX 01 3C)...")
    groups, total_hits = scan_slot_ranges_for_callers(slot_ranges, label_db)

    print(f"Total raw 01 XX 01 3C hits: {total_hits}")
    print(f"Grouped caller entries (slot,char,ID0): {len(groups)}")

    root = tk.Tk()
    CallerLabelGUI(root, groups, label_db)
    root.mainloop()


if __name__ == "__main__":
    main()
