#!/usr/bin/env python

import os
import sys
import struct
import tkinter as tk
from tkinter import ttk, messagebox

# Allow imports from project root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from dolphin_io import hook, rbytes, addr_in_ram
    import scan_normals_all
except Exception as e:
    print("Import failure:", e)
    sys.exit(1)


# ================================================================
# Memory Constants
# ================================================================
MEM1_START = 0x80000000
MEM1_END   = 0x81800000

MEM2_START = 0x90000000
MEM2_END   = 0x94000000

CHUNK_SIZE = 0x4000  # 16 KB chunks for scanning; good balance of speed / safety


# ================================================================
# Slot Range Builder
# ================================================================
def build_slot_ranges(scan_data):
    """
    Uses scan_normals_all output to infer memory region per slot.
    Each slot gets: (char_name, lo, hi) with ±0x4000 padding.
    We clamp to MEM1/MEM2 bounds.
    """
    ranges = {}
    for slot_info in scan_data:
        slot = slot_info.get("slot_label", "?")
        char = slot_info.get("char_name", "")
        bases = [mv.get("abs") for mv in slot_info.get("moves", []) if mv.get("abs")]
        if not bases:
            continue
        lo = min(bases) - 0x4000
        hi = max(bases) + 0x4000

        # Clamp into MEM1/MEM2 overall region
        if lo < MEM1_START:
            lo = MEM1_START
        if hi > MEM2_END:
            hi = MEM2_END

        ranges[slot] = (char, lo, hi)
    return ranges


# ================================================================
# Pattern Matching: Caller Pattern 01 XX 01 3C
# ================================================================
def scan_slots_for_callers(slot_ranges):
    """
    For each slot range (per-character region), scan for:
        01 ?? 01 3C

    slot_ranges: dict:
        slot -> (char_name, lo, hi)

    Returns dict:
        {
          slot_label: [
              {
                "slot": slot_label,
                "char": char_name,
                "abs": absolute address of match,
                "id0": the XX byte,
                "bytes": hex window around match
              },
              ...
          ],
        }

    Sort order: Slot -> Char -> ID0 -> ABS
    """
    results = {slot_label: [] for slot_label in slot_ranges.keys()}

    for slot_label, (char_name, lo, hi) in slot_ranges.items():
        print(f"Scanning callers in {slot_label} [{lo:08X}, {hi:08X})...")
        addr = lo
        # We overlap chunks by 3 bytes so a 4-byte pattern can't straddle the boundary undetected
        while addr < hi:
            size = min(CHUNK_SIZE, hi - addr)
            chunk = rbytes(addr, size) or b""
            n = len(chunk)
            if n >= 4:
                for i in range(n - 3):
                    if (
                        chunk[i] == 0x01 and
                        chunk[i + 2] == 0x01 and
                        chunk[i + 3] == 0x3C
                    ):
                        id_byte = chunk[i + 1]
                        hit_addr = addr + i
                        # Pull a context window around the match
                        ctx_start = max(hit_addr - 8, lo)
                        ctx_size = 32
                        ctx = rbytes(ctx_start, ctx_size) or b""
                        hexw = " ".join(f"{b:02X}" for b in ctx)

                        results[slot_label].append({
                            "slot": slot_label,
                            "char": char_name,
                            "abs": hit_addr,
                            "id0": id_byte,
                            "bytes": hexw,
                        })

            # overlap by 3 bytes for safety
            addr += max(1, CHUNK_SIZE - 3)

    # Sort each slot's list by Slot -> Char -> ID0 -> ABS
    for slot_label, lst in results.items():
        lst.sort(key=lambda x: (x["slot"], x["char"], x["id0"], x["abs"]))

    return results


# ================================================================
# House Blocks: 04 01 60
# ================================================================
MAGIC_HOUSE = b"\x04\x01\x60"

def scan_house_blocks(slot_ranges):
    """
    Scans, per slot range, for 04 01 60 blocks (house patterns).

    Returns list of:
    {
      "slot": slot_label,
      "char": char_name,
      "abs": addr,
      "bytes": hex string
    }
    """
    results = []

    for slot_label, (char_name, lo, hi) in slot_ranges.items():
        print(f"Scanning house blocks in {slot_label} [{lo:08X}, {hi:08X})...")
        addr = lo
        # Overlap chunks a bit in case 04 01 60 is near boundary
        while addr < hi:
            size = min(CHUNK_SIZE, hi - addr)
            chunk = rbytes(addr, size) or b""
            n = len(chunk)
            if n >= 3:
                idx = 0
                while True:
                    pos = chunk.find(MAGIC_HOUSE, idx)
                    if pos == -1:
                        break
                    hit_addr = addr + pos
                    ctx = rbytes(hit_addr, 0x30) or b""
                    hexw = " ".join(f"{b:02X}" for b in ctx)
                    results.append({
                        "slot": slot_label,
                        "char": char_name,
                        "abs": hit_addr,
                        "bytes": hexw,
                    })
                    idx = pos + 1
            addr += max(1, CHUNK_SIZE - 3)

    results.sort(key=lambda h: (h["slot"], h["char"], h["abs"]))
    return results


# ================================================================
# GUI
# ================================================================
class MultiTabGUI:
    def __init__(self, root, callers, scan_data, houses):
        self.root = root
        self.root.title("TvC Multi Analyzer - Caller Blocks / scan_normals_all / House Blocks")

        nb = ttk.Notebook(root)
        nb.pack(fill="both", expand=True)

        tab_callers = ttk.Frame(nb)
        tab_scan    = ttk.Frame(nb)
        tab_house   = ttk.Frame(nb)

        nb.add(tab_callers, text="Caller Blocks (01 XX 01 3C)")
        nb.add(tab_scan,    text="scan_normals_all")
        nb.add(tab_house,   text="House Blocks (04 01 60)")

        # --------------------------------
        # TAB 1: Caller Blocks
        # --------------------------------
        cols = ("slot", "char", "abs", "id0", "bytes")
        tr = ttk.Treeview(tab_callers, columns=cols, show="headings", height=28)
        tr.pack(fill="both", expand=True)

        for c in cols:
            tr.heading(c, text=c)
        tr.column("slot",  width=80)
        tr.column("char",  width=140)
        tr.column("abs",   width=120)
        tr.column("id0",   width=50)
        tr.column("bytes", width=800)

        for slot_label, entries in callers.items():
            for e in entries:
                tr.insert(
                    "",
                    "end",
                    values=(
                        e["slot"],
                        e["char"],
                        f"0x{e['abs']:08X}",
                        f"{e['id0']:02X}",
                        e["bytes"],
                    ),
                )

        # --------------------------------
        # TAB 2: scan_normals_all
        # --------------------------------
        cols2 = ("slot", "char", "anim", "abs")
        tr2 = ttk.Treeview(tab_scan, columns=cols2, show="headings", height=28)
        tr2.pack(fill="both", expand=True)

        for c in cols2:
            tr2.heading(c, text=c)
        tr2.column("slot", width=80)
        tr2.column("char", width=140)
        tr2.column("anim", width=60)
        tr2.column("abs",  width=120)

        for slot_info in scan_data:
            slot = slot_info.get("slot_label", "?")
            char = slot_info.get("char_name", "")
            for mv in slot_info.get("moves", []):
                base = mv.get("abs")
                anim = mv.get("id")
                if base is None or anim is None:
                    continue
                tr2.insert(
                    "",
                    "end",
                    values=(
                        slot,
                        char,
                        f"{anim:02X}",
                        f"0x{base:08X}",
                    ),
                )

        # --------------------------------
        # TAB 3: House Blocks
        # --------------------------------
        cols3 = ("slot", "char", "abs", "bytes")
        tr3 = ttk.Treeview(tab_house, columns=cols3, show="headings", height=28)
        tr3.pack(fill="both", expand=True)

        for c in cols3:
            tr3.heading(c, text=c)
        tr3.column("slot",  width=80)
        tr3.column("char",  width=140)
        tr3.column("abs",   width=120)
        tr3.column("bytes", width=800)

        for h in houses:
            tr3.insert(
                "",
                "end",
                values=(
                    h["slot"],
                    h["char"],
                    f"0x{h['abs']:08X}",
                    h["bytes"],
                ),
            )


# ================================================================
# Main
# ================================================================
def main():
    print("Hooking Dolphin...")
    hook()
    print("Hooked. Running scan_normals_all.scan_once()...")

    try:
        scan_data = scan_normals_all.scan_once()
    except Exception as e:
        print("scan_once failed:", e)
        sys.exit(1)

    slot_ranges = build_slot_ranges(scan_data)
    print("Slot ranges built:")
    for s, (ch, lo, hi) in slot_ranges.items():
        print(f"  {s}: {ch}  [{lo:08X}, {hi:08X})")

    print("Scanning for caller blocks (01 XX 01 3C) within slot ranges...")
    callers = scan_slots_for_callers(slot_ranges)

    print("Scanning for house blocks (04 01 60) within slot ranges...")
    houses = scan_house_blocks(slot_ranges)

    root = tk.Tk()
    MultiTabGUI(root, callers, scan_data, houses)
    root.mainloop()


if __name__ == "__main__":
    main()
