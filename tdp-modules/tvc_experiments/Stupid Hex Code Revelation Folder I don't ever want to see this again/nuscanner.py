#!/usr/bin/env python
#
# TvC "special move" farmer:
# - Uses scan_normals_all.scan_once() just like the move-control GUI.
# - Reuses the same control-pattern search (ID0 / XX).
# - ALSO pulls the house window (04 01 60 ... 0B F0 33 35 20 3F) per ABS.
# - Filters out basic normals so you mostly see specials/supers/donkey/etc.
#
# Run while in a match and doing the moves you care about:
#   (.venv) python farm_specials.py
#

import sys
import os
import json
import struct

ROOT = os.path.dirname(os.path.dirname(__file__))  # ...\tdp-modules
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dolphin_io import hook, rbytes, addr_in_ram
import scan_normals_all
from scan_normals_all import ANIM_MAP as SCAN_ANIM_MAP

LABELS_JSON = "move_labels.json"

# ---------------------------------------------------------------------
# Label helpers (same format as your move-control script)
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
# Control / house pattern helpers (copied from your scripts)
# ---------------------------------------------------------------------

def find_control_pattern(base: int, window_size: int = 0x80):
    """
    Same as move-control GUI:
    Pass 1: 00 00 00 00 01 XX 01 3C
    Pass 2: 01 XX 01 3C/3F
    Returns (id_off, id0).
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


# "House" search: look for 04 01 60 block and grab a small window around it.
MAGIC_HOUSE = b"\x04\x01\x60"

def find_house_window(base: int, window_size: int = 0x80, radius: int = 0x20):
    data = rbytes(base, window_size) or b""
    if not data:
        return b""
    idx = data.find(MAGIC_HOUSE)
    if idx == -1:
        return b""
    start = max(0, idx - radius)
    end   = min(len(data), idx + radius)
    return data[start:end]


# ---------------------------------------------------------------------
# Collect + filter moves
# ---------------------------------------------------------------------

# Generic “obviously normal” labels from your _generic section.
NORMAL_LABELS = {
    "5A", "5B", "5C",
    "2A", "2B", "2C",
    "3C", "6B", "6C",
    "j.A", "j.B", "j.C",
    "j.C Second", "j.A Second",
    "Landing",
}

# ID0s that are almost always special/super/assist-ish.
SPECIAL_ID0_HINTS = {
    0x14,       # donkey / dash-ish you already marked
    0x60, 0x61, # supers
    0x70,       # level 3
    0xA1, 0xA8, 0xA9, 0xAE, 0xBD, 0xBE,
}


def looks_special(label: str, id0: int, move_name: str) -> bool:
    """
    Try to avoid normals and keep the interesting stuff.
    Very conservative filter – you can loosen/tighten as needed.
    """
    lbl = (label or "").strip()

    # hard include by ID0
    if id0 in SPECIAL_ID0_HINTS:
        return True

    # labels like "ShinSho", "Tatsu Super", "donkey/dash-ish" etc
    if lbl and lbl not in NORMAL_LABELS:
        return True

    # if label empty but move_name isn't a simple j./number normal,
    # treat it as special-ish so you can discover & name it
    if not lbl:
        mn = (move_name or "").lower()
        if not mn:
            return False
        # obvious normals: start with "j." or digit like "5a", "2b", etc
        if mn[0] in "0123456789":
            return False
        if mn.startswith("j."):
            return False
        return True

    return False


def collect_specials(scan_data, label_db):
    rows = []

    for slot_info in scan_data:
        slot_label = slot_info.get("slot_label", "?")
        char_name  = slot_info.get("char_name", "").strip()

        for mv in slot_info.get("moves", []):
            base = mv.get("abs")
            anim_id = mv.get("id")
            if base is None or anim_id is None:
                continue
            if not addr_in_ram(base):
                continue

            id_off, id0 = find_control_pattern(base)
            if id_off is None or id0 is None:
                continue

            id0_hex = f"{id0:02X}"
            label   = get_move_label(label_db, char_name, id0_hex)

            move_name = SCAN_ANIM_MAP.get(anim_id, f"anim_{anim_id:02X}")

            if not looks_special(label, id0, move_name):
                continue

            ctrl_win  = control_window_bytes(base, id_off, radius=8)
            house_win = find_house_window(base, window_size=0x80, radius=0x20)

            rows.append({
                "slot": slot_label,
                "char": char_name,
                "anim": anim_id,
                "id0": id0,
                "label": label,
                "move_name": move_name,
                "base": base,
                "ctrl_win": ctrl_win,
                "house_win": house_win,
            })

    # sort a bit: by slot, then char, then anim ID
    rows.sort(key=lambda r: (r["slot"], _norm_char_key(r["char"]), r["anim"], r["id0"]))
    return rows


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
    rows = collect_specials(scan_data, label_db)

    print(f"Found {len(rows)} special-ish moves.\n")
    print("Slot  Char        Anim  ID0  Label / Move               ABS        ")
    print("----- ----------- ----- ---- --------------------------- ----------")
    for r in rows:
        slot = r["slot"]
        char = (r["char"] or "")[:11]
        anim_hex = f"{r['anim']:02X}"
        id0_hex  = f"{r['id0']:02X}"
        label = r["label"] or r["move_name"]
        label_disp = (label[:25] + "...") if len(label) > 28 else label
        print(f"{slot:5s} {char:11s} {anim_hex:>5s} {id0_hex:>4s} {label_disp:27s} 0x{r['base']:08X}")

  
if __name__ == "__main__":
    main()
