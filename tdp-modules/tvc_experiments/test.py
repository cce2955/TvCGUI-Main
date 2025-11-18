#!/usr/bin/env python

import os
import sys
import json
import struct

# Force Python to use the real dolphin_io in tdp-modules, not the local one
ROOT = os.path.dirname(os.path.dirname(__file__))  # ...\tdp-modules
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from dolphin_io import hook, rbytes, rd8, rd32, addr_in_ram
import scan_normals_all

# ---------------------------------------------------------------------
# Struct / house info
# ---------------------------------------------------------------------

BASE_BACK  = 0x14               # offset from 04 01 60 block back to struct base
MAGIC      = b"\x04\x01\x60"    # start of the house control header
TAIL_RANGE = 0x40               # how far we read for display window

LABELS_JSON = "move_labels.json"

# These are the ID0s you just added for Ryu (plus supers)
INTERESTING_ID0 = {
    0x14, 0x20, 0x25, 0x28, 0x29, 0x30, 0x31,
    0x33, 0x35, 0x36, 0x39, 0x3A, 0x40, 0x41,
    0x60, 0x61, 0x70,
}

# ---------------------------------------------------------------------
# Label helpers (same format as your move-control GUI)
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
# Control pattern helpers (ID0 / XX in the front of the move)
# ---------------------------------------------------------------------

def find_control_pattern_bytes(data: bytes):
    """
    Scan a small window of bytes for the control pattern and return (offset, id0).

    Pass 1 (strict, grounded normals):
        00 00 00 00 01 XX 01 3C

    Pass 2 (air / variants):
        01 XX 01 3C
        01 XX 01 3F
    """
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
                if 0x00 <= id0 <= 0xFF:
                    return i + 5, id0

    # Pass 2: looser 01 XX 01 3C/3F
    for i in range(n - 3):
        if data[i] == 0x01 and data[i + 2] == 0x01 and data[i + 3] in (0x3C, 0x3F):
            id0 = data[i + 1]
            if 0x00 <= id0 <= 0xFF:
                return i + 1, id0

    return None, None


def classify_anim(anim):
    if 0x60 <= anim <= 0x7F:
        return "ground"
    if 0xA0 <= anim <= 0xBF:
        return "air"
    return "other"

# ---------------------------------------------------------------------
# Main scanner
# ---------------------------------------------------------------------

def scan_live(label_db, min_radius=0x18, filter_id0=None):
    """
    Scan each ABS from scan_normals_all and interpret houses directly from RAM.
    Additionally:
      - find ID0 in the move header
      - look up label via move_labels.json
      - optionally filter to a set of ID0 values (e.g. your Ryu 5A IDs)
    """
    scan = scan_normals_all.scan_once()
    results = []

    for slot in scan:
        slot_label = slot.get("slot_label", "?")
        char_name  = slot.get("char_name", "").strip()

        for mv in slot["moves"]:
            base = mv["abs"]
            if not addr_in_ram(base):
                continue

            # read ~0x80 bytes from the ABS start (for control pattern + house)
            block = rbytes(base, 0x80)
            if not block or len(block) < 0x20:
                continue

            # find ID0 / control pattern in the front of the move
            id_off, id0 = find_control_pattern_bytes(block)
            if id_off is None:
                continue

            if filter_id0 is not None and id0 not in filter_id0:
                # not one of the interesting ID0s you care about
                continue

            id0_hex = f"{id0:02X}"
            label   = get_move_label(label_db, char_name, id0_hex)

            # search for the FIRST 04 01 60 block inside this ABS
            idx = block.find(MAGIC)
            if idx == -1 or idx < BASE_BACK:
                continue

            struct_base = base + idx - BASE_BACK
            if struct_base < 0:
                continue

            # re-read clean from struct_base for the house header
            hdr = rbytes(struct_base, TAIL_RANGE)
            if not hdr or len(hdr) < 0x20:
                continue

            # parse struct fields (these are still a bit janky, but we keep them)
            radius   = struct.unpack(">I", hdr[0x00:0x04])[0]
            x_offset = struct.unpack(">f", hdr[0x04:0x08])[0]
            y_const  = struct.unpack(">I", hdr[0x08:0x0C])[0]
            flags    = struct.unpack(">I", hdr[0x10:0x14])[0]

            # animation ID sits 15 bytes AFTER the 04 01 60 in the original block
            anim_off = idx + 15
            if anim_off >= len(block):
                continue
            anim_id = block[anim_off]

            if radius < min_radius:
                continue

            results.append({
                "slot":   slot_label,
                "char":   char_name,
                "abs":    base,
                "anim":   anim_id,
                "kind":   classify_anim(anim_id),
                "id0":    id0,
                "label":  label,
                "radius": radius,
                "x":      x_offset,
                "y":      y_const,
                "flags":  flags,
                "window": " ".join(f"{b:02X}" for b in hdr[:0x30]),
            })

    return results


def main():
    print("Hooking Dolphin…")
    hook()
    print("Scanning live RAM…")

    label_db = load_label_db()
    results = scan_live(label_db, min_radius=0x18, filter_id0=INTERESTING_ID0)

    print(f"Found {len(results)} interesting houses.\n")
    print("Slot Char        ABS         anim kind   ID0 label      radius   x_off     y  flags     window")
    print("------------------------------------------------------------------------------------------------------")
    for r in results:
        slot  = r["slot"]
        char  = (r["char"] or "")[:10]
        anim  = f"{r['anim']:02X}"
        kind  = r["kind"]
        id0   = f"{r['id0']:02X}"
        label = (r["label"] or "")[:8]
        print(
            f"{slot:4s} {char:10s} 0x{r['abs']:08X}  {anim:2s}  {kind:<6s}  "
            f"{id0:2s} {label:8s}  {r['radius']:6d}  {r['x']:+.3f}  {r['y']:6d}  "
            f"{r['flags']:08X}  {r['window']}"
        )


if __name__ == "__main__":
    main()
