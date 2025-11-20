#!/usr/bin/env python

#
# TvC Caller Pattern Labeler + Unmanaged Scanner + Anim/Full Classifier
# MODIFIED: Anim and Full classes are now independent entries in the GUI
# Only the first address of each class is shown per group
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
    from constants import MEM1_LO, MEM1_HI, MEM2_LO, MEM2_HI
except Exception as e:
    print("Import failure in caller pattern labeler:", e)
    sys.exit(1)

CALLER_LABELS_JSON = os.path.join(ROOT, "caller_labels.json")
CALLER_CLASSES_JSON = os.path.join(ROOT, "caller_classes.json")

PATTERN_LEN = 4  # 01 XX 01 3C base length we chunk with


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


def get_caller_label(label_db, char_name: str, id0_hex: str, class_suffix: str = "") -> str:
    """
    Per-character label, then _generic fallback.
    Supports class suffix for Anim/Full variants.
    """
    if not label_db:
        return ""

    generic = label_db.get("_generic", {})
    ckey = _norm_char_key(char_name)
    per_char = label_db.get(ckey, {}) if ckey else {}

    if class_suffix:
        key_with_class_up = f"{id0_hex.upper()}|{class_suffix}"
        key_with_class_lo = f"{id0_hex.lower()}|{class_suffix}"

        result = (
            per_char.get(key_with_class_up)
            or per_char.get(key_with_class_lo)
            or generic.get(key_with_class_up)
            or generic.get(key_with_class_lo)
        )
        if result:
            return result

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
# Class DB helpers (Anim/Full/?)
# ------------------------------------------------------------

def load_class_db(path: str = CALLER_CLASSES_JSON):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_class_db(db, path: str = CALLER_CLASSES_JSON):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(db, f, indent=2, sort_keys=True)
    except Exception as e:
        print("Failed to save caller class db:", e)


def make_class_key(slot: str, char_name: str, id0: int, class_suffix: str = "") -> str:
    """
    Key format: "<slot>|<normalized_char>|<ID0_hex>|<class>".
    Example: "P1-C1|ryu|0D|Anim" or "P1-C1|ryu|0D|Full"
    For UNMANAGED, char_name can be "".
    """
    ckey = _norm_char_key(char_name)
    base = f"{slot}|{ckey}|{id0:02X}"
    if class_suffix:
        return f"{base}|{class_suffix}"
    return base


def resolve_class(counts: dict) -> str:
    """
    Resolve group-level class from per-hit counts.
    - If only Anims -> 'Anim'
    - If only Fulls -> 'Full'
    - If mixture -> 'Mixed'
    - If no Anim/Full info -> '?'
    """
    anim = counts.get("Anim", 0)
    full = counts.get("Full", 0)
    if anim == 0 and full == 0:
        return "?"
    if anim > 0 and full == 0:
        return "Anim"
    if full > 0 and anim == 0:
        return "Full"
    return "Mixed"


# ------------------------------------------------------------
# Slot range builder (from scan_normals_all)
# ------------------------------------------------------------

def build_slot_ranges(scan_data, padding: int = 0x4000):
    """
    Build approximate address ranges per slot from scan_normals_all.scan_once().
    """
    per_slot_addrs = {}
    per_slot_char = {}

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
        slot_ranges[slot_label] = {
            "char": per_slot_char.get(slot_label, ""),
            "lo": lo,
            "hi": hi,
        }

    return slot_ranges


# ------------------------------------------------------------
# Shared pattern matchers + classifier
# ------------------------------------------------------------

def _pattern_match_anim(buf: bytes, i: int) -> bool:
    # 01 XX 01 3C
    return (
        buf[i] == 0x01 and
        buf[i + 2] == 0x01 and
        buf[i + 3] == 0x3C
    )


def _pattern_match_fulltrip(buf: bytes, i: int) -> bool:
    """
    Full caller header:

      Form A (no padding):  01 XX 04 ...
      Form B (padded):      00 01 XX 04 ...

    We match either one here; the scan loops will inspect the bytes to
    decide where the opcode lives and which byte is the ID.
    """
    # Form A: 01 XX 04
    if buf[i] == 0x01 and i + 2 < len(buf) and buf[i + 2] == 0x04:
        return True

    # Form B: 00 01 XX 04
    if (
        buf[i] == 0x00
        and i + 3 < len(buf)
        and buf[i + 1] == 0x01
        and buf[i + 3] == 0x04
    ):
        return True

    return False


def classify_caller_addr(addr: int) -> str:
    """
    Heuristic Anim/Full classifier for a single 01 XX 01 3C hit.

    We look at bytes near the 01 XX 01 3C pattern:

    - If we see the "helper tail"   0B F0 33 35 20 3F   shortly after,
      treat as Full.

    - If we see nearby 04 01 60 (house marker), treat as Full.

    - If we see FF FF FF FE just before, treat as Full.

    Otherwise default to Anim.

    (The separate 01 XX 04 scanner runs in the main loops and
     directly tags those hits as Full.)
    """
    start = addr - 0x10
    size = 0x40
    ctx = rbytes(start, size) or b""
    if len(ctx) < 20:
        return "?"

    pattern_idx = addr - start
    if pattern_idx < 0 or pattern_idx + PATTERN_LEN > len(ctx):
        return "?"

    after = ctx[pattern_idx + PATTERN_LEN: pattern_idx + PATTERN_LEN + 24]
    before = ctx[max(0, pattern_idx - 16): pattern_idx]

    if b"\x0B\xF0\x33\x35\x20\x3F" in after:
        return "Full"

    if b"\x04\x01\x60" in ctx:
        return "Full"

    if b"\xFF\xFF\xFF\xFE" in before:
        return "Full"

    return "Anim"


# ------------------------------------------------------------
# Scan per-slot ranges (managed groups)
# ------------------------------------------------------------

def scan_slot_ranges_for_callers(slot_ranges, label_db, class_db):
    """
    Scan each slot's [lo, hi) range for:

        01 XX 01 3C  (Anim stub)
        01 XX 04     (Full variant header)

    Returns grouped entries per (slot,char,ID0).
    """
    groups = {}
    total_hits = 0  # only counting 01 XX 01 3C here; 01 XX 04 is extra

    for slot_label, info in slot_ranges.items():
        char_name = info.get("char", "")
        lo = info.get("lo")
        hi = info.get("hi")

        if lo is None or hi is None or lo >= hi:
            continue

        print(
            f"Scanning {slot_label} ({char_name}) range "
            f"[0x{lo:08X}, 0x{hi:08X}) for 01 XX 01 3C + 01 XX 04..."
        )

        addr = lo
        tail = b""

        while addr < hi:
            chunk_size = 0x800
            remaining = hi - addr
            size = chunk_size if remaining > chunk_size else remaining
            if size <= 0:
                break

            data = rbytes(addr, size)
            if not data:
                addr += size
                tail = b""
                continue

            buf = tail + data
            base_for_buf = addr - len(tail)
            n = len(buf)
            i = 0

            while i <= n - PATTERN_LEN:
                handled = False

                # 01 XX 01 3C
                if _pattern_match_anim(buf, i):
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
                            "addr_classes": [],
                            "class_counts": {},
                            "class_hint": None,
                            "class_override": None,
                            "class": None,
                            "label": label,
                            "bytes": None,
                        }
                        groups[key] = g

                    cls_hit = classify_caller_addr(hit_addr)
                    if cls_hit not in ("Anim", "Full", "?"):
                        cls_hit = "?"

                    g["addresses"].append(hit_addr)
                    g["addr_classes"].append(cls_hit)
                    g["class_counts"][cls_hit] = g["class_counts"].get(cls_hit, 0) + 1

                    if g["bytes"] is None:
                        ctx_start = hit_addr - 8
                        ctx_size = 32
                        ctx = rbytes(ctx_start, ctx_size) or b""
                        g["bytes"] = " ".join(f"{b:02X}" for b in ctx)

                    handled = True

                # 01 XX 04 (full variant header; used by jump normals etc.)
                # 00 01 XX 04 (full variant header; used by jump normals etc.)
               # Full variant header; used by jump normals etc.
                if _pattern_match_fulltrip(buf, i):
                    # Decide which form we hit:
                    if buf[i] == 0x01:
                        # Form A: 01 XX 04 ...
                        id0_full = buf[i + 1]
                        hit_addr_full = base_for_buf + i        # opcode at i
                    else:
                        # Form B: 00 01 XX 04 ...
                        id0_full = buf[i + 2]
                        hit_addr_full = base_for_buf + i + 1    # opcode at i+1

                    key_full = (slot_label, char_name, id0_full)
                    g_full = groups.get(key_full)
                    if g_full is None:
                        id0_hex_full = f"{id0_full:02X}"
                        label_full = get_caller_label(label_db, char_name, id0_hex_full)
                        g_full = {
                            "slot": slot_label,
                            "char": char_name,
                            "id0": id0_full,
                            "addresses": [],
                            "addr_classes": [],
                            "class_counts": {},
                            "class_hint": None,
                            "class_override": None,
                            "class": None,
                            "label": label_full,
                            "bytes": None,
                        }
                        groups[key_full] = g_full

                    g_full["addresses"].append(hit_addr_full)
                    g_full["addr_classes"].append("Full")
                    g_full["class_counts"]["Full"] = g_full["class_counts"].get("Full", 0) + 1

                    if g_full["bytes"] is None:
                        ctx_start = hit_addr_full - 8
                        ctx_size = 32
                        ctx = rbytes(ctx_start, ctx_size) or b""
                        g_full["bytes"] = " ".join(f"{b:02X}" for b in ctx)

                    handled = True


                i += 1 if handled else 1

            if len(buf) >= PATTERN_LEN - 1:
                tail = buf[-(PATTERN_LEN - 1):]
            else:
                tail = buf

            addr += size

    groups_list = list(groups.values())
    for g in groups_list:
        g["class_hint"] = resolve_class(g.get("class_counts", {}))
        key = make_class_key(g["slot"], g["char"], g["id0"])
        override = class_db.get(key)
        g["class_override"] = override
        if override:
            g["class"] = override
        else:
            g["class"] = g["class_hint"] or "?"

    groups_list.sort(
        key=lambda g: (g["slot"], _norm_char_key(g["char"]), g["id0"])
    )

    return groups_list, total_hits


# ------------------------------------------------------------
# Scan full MEM1+MEM2 for unmanaged hits
# ------------------------------------------------------------

def scan_unmanaged_for_callers(slot_ranges, label_db, class_db):
    """
    Global scan over MEM1 and MEM2 for:
      01 XX 01 3C and 01 XX 04 / 00 01 XX 04,
    excluding any hit that lies inside a known slot range.
    """
    managed_ranges = []
    for info in slot_ranges.values():
        lo = info.get("lo")
        hi = info.get("hi")
        if lo is not None and hi is not None and lo < hi:
            managed_ranges.append((lo, hi))

    def in_managed(a):
        for lo, hi in managed_ranges:
            if lo <= a < hi:
                return True
        return False

    mem_ranges = [
        (MEM1_LO, MEM1_HI),
        (MEM2_LO, MEM2_HI),
    ]

    groups = {}
    total_hits = 0  # only counting 01 XX 01 3C here

    print("Global unmanaged scan over MEM1+MEM2 (skipping slot ranges)...")

    for (glo, ghi) in mem_ranges:
        addr = glo
        tail = b""

        while addr < ghi:
            chunk_size = 0x1000
            remaining = ghi - addr
            size = chunk_size if remaining > chunk_size else remaining
            if size <= 0:
                break

            data = rbytes(addr, size)
            if not data:
                addr += size
                tail = b""
                continue

            buf = tail + data
            base_for_buf = addr - len(tail)
            n = len(buf)
            i = 0

            while i <= n - PATTERN_LEN:
                handled = False

                # 01 XX 01 3C
                if _pattern_match_anim(buf, i):
                    id0 = buf[i + 1]
                    hit_addr = base_for_buf + i

                    if in_managed(hit_addr):
                        i += 1
                        continue

                    total_hits += 1

                    key = ("UNMANAGED", "", id0)
                    g = groups.get(key)
                    if g is None:
                        id0_hex = f"{id0:02X}"
                        label = get_caller_label(label_db, "", id0_hex)
                        g = {
                            "slot": "UNMANAGED",
                            "char": "",
                            "id0": id0,
                            "addresses": [],
                            "addr_classes": [],
                            "class_counts": {},
                            "class_hint": None,
                            "class_override": None,
                            "class": None,
                            "label": label,
                            "bytes": None,
                        }
                        groups[key] = g

                    cls_hit = classify_caller_addr(hit_addr)
                    if cls_hit not in ("Anim", "Full", "?"):
                        cls_hit = "?"

                    g["addresses"].append(hit_addr)
                    g["addr_classes"].append(cls_hit)
                    g["class_counts"][cls_hit] = g["class_counts"].get(cls_hit, 0) + 1

                    if g["bytes"] is None:
                        ctx_start = hit_addr - 8
                        ctx_size = 32
                        ctx = rbytes(ctx_start, ctx_size) or b""
                        g["bytes"] = " ".join(f"{b:02X}" for b in ctx)

                    handled = True

                # 01 XX 04 or 00 01 XX 04
                if _pattern_match_fulltrip(buf, i):
                    if buf[i] == 0x01:
                        # Form A: 01 XX 04 ...
                        id0_full = buf[i + 1]
                        hit_addr_full = base_for_buf + i
                    else:
                        # Form B: 00 01 XX 04 ...
                        id0_full = buf[i + 2]
                        hit_addr_full = base_for_buf + i + 1

                    if in_managed(hit_addr_full):
                        i += 1
                        continue

                    key_full = ("UNMANAGED", "", id0_full)
                    g_full = groups.get(key_full)
                    if g_full is None:
                        id0_hex_full = f"{id0_full:02X}"
                        label_full = get_caller_label(label_db, "", id0_hex_full)
                        g_full = {
                            "slot": "UNMANAGED",
                            "char": "",
                            "id0": id0_full,
                            "addresses": [],
                            "addr_classes": [],
                            "class_counts": {},
                            "class_hint": None,
                            "class_override": None,
                            "class": None,
                            "label": label_full,
                            "bytes": None,
                        }
                        groups[key_full] = g_full

                    g_full["addresses"].append(hit_addr_full)
                    g_full["addr_classes"].append("Full")
                    g_full["class_counts"]["Full"] = g_full["class_counts"].get("Full", 0) + 1

                    if g_full["bytes"] is None:
                        ctx_start = hit_addr_full - 8
                        ctx_size = 32
                        ctx = rbytes(ctx_start, ctx_size) or b""
                        g_full["bytes"] = " ".join(f"{b:02X}" for b in ctx)

                    handled = True

                i += 1 if handled else 1

            if len(buf) >= PATTERN_LEN - 1:
                tail = buf[-(PATTERN_LEN - 1):]
            else:
                tail = buf

            addr += size

    groups_list = list(groups.values())
    for g in groups_list:
        g["class_hint"] = resolve_class(g.get("class_counts", {}))
        key = make_class_key(g["slot"], g["char"], g["id0"])
        override = class_db.get(key)
        g["class_override"] = override
        if override:
            g["class"] = override
        else:
            g["class"] = g["class_hint"] or "?"

    groups_list.sort(
        key=lambda g: (g["slot"], _norm_char_key(g["char"]), g["id0"])
    )
    return groups_list, total_hits


# ------------------------------------------------------------
# GUI with split Anim/Full display
# ------------------------------------------------------------

class CallerLabelGUI:
    def __init__(self, root, groups, label_db, class_db, slot_ranges):
        self.root = root
        self.root.title("TvC Caller Pattern Labeler (Split Anim/Full)")

        self.groups = groups
        self.label_db = label_db
        self.class_db = class_db
        self.slot_ranges = slot_ranges

        self.display_items = []

        main = ttk.Frame(root, padding=8)
        main.pack(fill="both", expand=True)

        top = ttk.Frame(main)
        top.grid(row=0, column=0, sticky="ew")

        ttk.Label(
            top,
            text=(
                "Only first address of each class shown. "
                "Label and override independently."
            ),
        ).grid(row=0, column=0, sticky="w")

        cols = ("slot", "char", "id0", "cls", "addr", "label", "bytes")
        self.tree = ttk.Treeview(main, columns=cols, show="headings", height=26)
        self.tree.grid(row=1, column=0, sticky="nsew", pady=(4, 4))

        vsb = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        vsb.grid(row=1, column=1, sticky="ns", pady=(4, 4))

        self.tree.heading("slot", text="Slot")
        self.tree.heading("char", text="Char")
        self.tree.heading("id0", text="ID0")
        self.tree.heading("cls", text="Class")
        self.tree.heading("addr", text="Address (first)")
        self.tree.heading("label", text="Label")
        self.tree.heading("bytes", text="Context bytes")

        self.tree.column("slot", width=80, anchor="w")
        self.tree.column("char", width=140, anchor="w")
        self.tree.column("id0", width=70, anchor="center")
        self.tree.column("cls", width=60, anchor="center")
        self.tree.column("addr", width=100, anchor="w")
        self.tree.column("label", width=280, anchor="w")
        self.tree.column("bytes", width=420, anchor="w")

        bottom = ttk.Frame(main, padding=(0, 4, 0, 0))
        bottom.grid(row=2, column=0, columnspan=2, sticky="ew")

        ttk.Label(bottom, text="Selected:").grid(row=0, column=0, sticky="e")
        self.sel_info_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.sel_info_var, width=60).grid(
            row=0, column=1, columnspan=4, sticky="w", padx=(4, 12)
        )

        ttk.Label(bottom, text="Label:").grid(row=1, column=0, sticky="e")
        self.label_entry = ttk.Entry(bottom, width=50)
        self.label_entry.grid(row=1, column=1, columnspan=3, sticky="w", padx=(4, 12))

        self.save_label_btn = ttk.Button(bottom, text="Save Label", command=self.on_save_label)
        self.save_label_btn.grid(row=1, column=4, padx=(4, 4))

        ttk.Label(bottom, text="Override Class:").grid(row=2, column=0, sticky="e")
        self.class_entry = ttk.Entry(bottom, width=10)
        self.class_entry.grid(row=2, column=1, sticky="w", padx=(4, 4))

        self.save_class_btn = ttk.Button(bottom, text="Save Class Override", command=self.on_save_class)
        self.save_class_btn.grid(row=2, column=2, padx=(4, 4))

        self.rescan_btn = ttk.Button(bottom, text="Rescan slots", command=self.on_rescan_slots)
        self.rescan_btn.grid(row=3, column=0, padx=(4, 4), pady=(4, 0), sticky="w")

        self.unmanaged_btn = ttk.Button(
            bottom,
            text="Scan unmanaged (global)",
            command=self.on_scan_unmanaged,
        )
        self.unmanaged_btn.grid(row=3, column=1, padx=(4, 0), pady=(4, 0), sticky="w")

        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)

        self._populate_tree()
        self.tree.bind("<<TreeviewSelect>>", self.on_select)

    def _populate_tree(self):
        # Clear existing rows
        self.tree.delete(*self.tree.get_children())
        self.display_items = []

        # Build a flat list of rows: one for Anim, one for Full (if present)
        rows = []

        for g in self.groups:
            slot = g["slot"]
            char = g["char"]
            id0 = g["id0"]
            id0_str = f"{id0:02X}"
            bytes_str = g.get("bytes", "") or ""

            addresses = g["addresses"]
            addr_classes = g.get("addr_classes", [])

            first_anim = None
            first_full = None

            for addr, cls in zip(addresses, addr_classes):
                if cls == "Anim" and first_anim is None:
                    first_anim = addr
                elif cls == "Full" and first_full is None:
                    first_full = addr

                if first_anim is not None and first_full is not None:
                    break

            if first_anim is not None:
                rows.append({
                    "slot": slot,
                    "char": char,
                    "id0": id0,
                    "id0_str": id0_str,
                    "cls": "Anim",
                    "addr": first_anim,
                    "bytes": bytes_str,
                    "group": g,
                })

            if first_full is not None:
                rows.append({
                    "slot": slot,
                    "char": char,
                    "id0": id0,
                    "id0_str": id0_str,
                    "cls": "Full",
                    "addr": first_full,
                    "bytes": bytes_str,
                    "group": g,
                })

        # Sort: by slot, then Anim before Full, then char, then ID0
        def cls_order(c):
            if c == "Anim":
                return 0
            if c == "Full":
                return 1
            return 2

        rows.sort(
            key=lambda r: (
                r["slot"],
                cls_order(r["cls"]),
                _norm_char_key(r["char"]),
                r["id0"],
            )
        )

        # Insert sorted rows into the tree and rebuild display_items
        for r in rows:
            slot = r["slot"]
            char = r["char"]
            id0_str = r["id0_str"]
            cls = r["cls"]
            addr = r["addr"]
            bytes_str = r["bytes"]
            g = r["group"]

            label = get_caller_label(self.label_db, char, id0_str, cls)

            self.tree.insert(
                "",
                "end",
                values=(
                    slot,
                    char,
                    id0_str,
                    cls,
                    f"0x{addr:08X}",
                    label,
                    bytes_str,
                ),
            )

            self.display_items.append(
                {"group": g, "class": cls, "address": addr}
            )

    def _get_selected_display_item(self):
        sel = self.tree.selection()
        if not sel:
            return None
        iid = sel[0]
        idx = self.tree.index(iid)
        if idx < 0 or idx >= len(self.display_items):
            return None
        return self.display_items[idx]

    def on_select(self, event):
        item = self._get_selected_display_item()
        if not item:
            self.sel_info_var.set("")
            self.label_entry.delete(0, tk.END)
            self.class_entry.delete(0, tk.END)
            return

        g = item["group"]
        cls = item["class"]
        addr = item["address"]

        info_str = (
            f"{g['slot']} | {g['char']} | ID0={g['id0']:02X} | "
            f"Class={cls} | Addr=0x{addr:08X}"
        )
        self.sel_info_var.set(info_str)

        id0_str = f"{g['id0']:02X}"
        label = get_caller_label(self.label_db, g["char"], id0_str, cls)
        self.label_entry.delete(0, tk.END)
        self.label_entry.insert(0, label)

        key = make_class_key(g["slot"], g["char"], g["id0"], cls)
        override = self.class_db.get(key)
        self.class_entry.delete(0, tk.END)
        if override:
            self.class_entry.insert(0, override)
        else:
            self.class_entry.insert(0, cls)

    def on_save_label(self):
        item = self._get_selected_display_item()
        if not item:
            messagebox.showerror("No selection", "Select a caller entry first.")
            return

        g = item["group"]
        cls = item["class"]
        char_name = (g["char"] or "").strip()
        label_txt = self.label_entry.get().strip()
        id0_hex = f"{g['id0']:02X}"

        if char_name:
            ckey = _norm_char_key(char_name)
        else:
            ckey = "_generic"

        self.label_db.setdefault(ckey, {})
        label_key = f"{id0_hex}|{cls}"
        self.label_db[ckey][label_key] = label_txt
        save_label_db(self.label_db)

        self._populate_tree()
        messagebox.showinfo(
            "Saved label",
            f"Saved label for {char_name if char_name else '(generic)'} "
            f"ID0={id0_hex} Class={cls}: \"{label_txt}\"",
        )

    def on_save_class(self):
        item = self._get_selected_display_item()
        if not item:
            messagebox.showerror("No selection", "Select a caller entry first.")
            return

        g = item["group"]
        cls = item["class"]

        raw_cls = self.class_entry.get().strip()
        if not raw_cls:
            messagebox.showerror("Class empty", "Enter a class override: Anim, Full, or ?.")
            return

        new_cls_lower = raw_cls.lower()
        if new_cls_lower == "anim":
            new_cls = "Anim"
        elif new_cls_lower == "full":
            new_cls = "Full"
        elif new_cls_lower == "?":
            new_cls = "?"
        elif new_cls_lower == "mixed":
            new_cls = "Mixed"
        else:
            messagebox.showerror("Bad class", "Class must be: Anim, Full, Mixed, or ?")
            return

        key = make_class_key(g["slot"], g["char"], g["id0"], cls)
        self.class_db[key] = new_cls
        save_class_db(self.class_db)

        self._populate_tree()
        messagebox.showinfo(
            "Saved class override",
            f"Saved class override for "
            f"{g['slot']} {g['char'] or '(UNMANAGED)'} "
            f"ID0={g['id0']:02X} Class={cls}: \"{new_cls}\"",
        )

    def on_rescan_slots(self):
        try:
            print("Rescanning slot ranges...")
            hook()
            scan_data = scan_normals_all.scan_once()
            self.slot_ranges = build_slot_ranges(scan_data)
            self.label_db = load_label_db()
            self.class_db = load_class_db()

            managed_groups, total_hits = scan_slot_ranges_for_callers(
                self.slot_ranges, self.label_db, self.class_db
            )
            self.groups = managed_groups
            self._populate_tree()
            messagebox.showinfo(
                "Rescan complete",
                f"Slot scan hits (01 XX 01 3C): {total_hits}\n"
                f"Grouped entries: {len(self.groups)}",
            )
        except Exception as e:
            messagebox.showerror("Rescan failed", str(e))

    def on_scan_unmanaged(self):
        try:
            print("Scanning unmanaged caller patterns...")
            hook()
            self.label_db = load_label_db()
            self.class_db = load_class_db()

            unmanaged_groups, total_hits = scan_unmanaged_for_callers(
                self.slot_ranges, self.label_db, self.class_db
            )

            self.groups.extend(unmanaged_groups)
            self.groups.sort(
                key=lambda g: (g["slot"], _norm_char_key(g["char"]), g["id0"])
            )
            self._populate_tree()

            messagebox.showinfo(
                "Unmanaged scan complete",
                f"Global unmanaged hits (01 XX 01 3C outside slot ranges): {total_hits}\n"
                f"New UNMANAGED groups: {len(unmanaged_groups)}",
            )
        except Exception as e:
            messagebox.showerror("Unmanaged scan failed", str(e))


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
    class_db = load_class_db()

    print("Scanning per-slot ranges for caller blocks...")
    groups, total_hits = scan_slot_ranges_for_callers(slot_ranges, label_db, class_db)

    print(f"Total raw 01 XX 01 3C hits in slot ranges: {total_hits}")
    print(f"Grouped caller entries (slot,char,ID0): {len(groups)}")

    root = tk.Tk()
    CallerLabelGUI(root, groups, label_db, class_db, slot_ranges)
    root.mainloop()


if __name__ == "__main__":
    main()
