#!/usr/bin/env python3
#
# probe_live.py — LIVE Dolphin marker/struct probe
#
# Scans live memory from a Dolphin instance, not from a .bin file.
#
# - Finds 01 xx 01 and 01 xx 04 markers in the target region
# - Builds regions = [marker, next_marker)
# - Slices each region into fixed-size structs
# - Prints all regions + structs to stdout
#
# Usage:
#   python probe_live.py 0xBASE 0xSIZE
#
# Example:
#   python probe_live.py 0x908AAC30 0x00008000
#
# (Typical Ryu P1-C1 spans ~0x908AAC30–0x90908CEC)
#

import struct
import sys
import time

from dolphin_io import hook, rd8

STRUCT_SIZE = 0x20         # house size
ALIGNMENT = 4              # align region starts
MIN_NONZERO = 6            # to filter useless blocks


# ---------------------------------------------------------------
# Memory helpers
# ---------------------------------------------------------------

def rd_block(addr, size):
    """Slow but safe rd8 loop until we implement fast block IO."""
    buf = bytearray(size)
    for i in range(size):
        b = rd8(addr + i)
        if b is None:
            b = 0
        buf[i] = b
    return bytes(buf)


def u16(buf, off):
    if off + 2 > len(buf): return None
    return struct.unpack_from(">H", buf, off)[0]


# ---------------------------------------------------------------
# Marker scanning
# ---------------------------------------------------------------

def find_markers(buf):
    markers = []
    n = len(buf)
    i = 0
    while i <= n - 3:
        if buf[i] == 0x01:
            m_id  = buf[i+1]
            kind  = buf[i+2]
            if kind in (0x01, 0x04):
                markers.append((i, m_id, kind))
                i += 3
                continue
        i += 1
    return markers


def build_regions(markers, total_len):
    regions = []
    for i, (off, m_id, kind) in enumerate(markers):
        start = off + 3
        end = markers[i+1][0] if i+1 < len(markers) else total_len
        regions.append((off, m_id, kind, start, end))
    return regions


# ---------------------------------------------------------------
# Struct slicing
# ---------------------------------------------------------------

def align_up(v, a):
    r = v % a
    return v if r == 0 else v + (a - r)


def slice_region(buf, start, end, struct_size):
    chunks = []
    pos = align_up(start, ALIGNMENT)
    while pos + struct_size <= end:
        chunk = buf[pos:pos+struct_size]
        nonzero = sum(1 for b in chunk if b != 0)
        if nonzero >= MIN_NONZERO:
            chunks.append((pos, chunk, nonzero))
        pos += struct_size
    return chunks


def hexdump(b):
    return " ".join(f"{x:02X}" for x in b)


# ---------------------------------------------------------------
# Main
# ---------------------------------------------------------------

def main():
    if len(sys.argv) < 3:
        print("Usage: python probe_live.py 0xBASE 0xSIZE")
        sys.exit(1)

    base = int(sys.argv[1], 0)
    size = int(sys.argv[2], 0)

    print("Hooking Dolphin…")
    hook()
    print("Hooked.")

    print(f"Reading 0x{size:X} bytes starting at 0x{base:08X}…")
    buf = rd_block(base, size)

    markers = find_markers(buf)
    if not markers:
        print("NO MARKERS FOUND.")
        return

    print(f"Found {len(markers)} markers.\n")

    regions = build_regions(markers, len(buf))

    for idx, (m_off, m_id, kind, r_start, r_end) in enumerate(regions):
        print(f"[Region {idx:03d}] marker=01 {m_id:02X} {kind:02X} @ +0x{m_off:04X}")
        print(f"   span: +0x{r_start:04X}–+0x{r_end:04X}  (len {r_end-r_start})")
        print(f"   move_id = 0x{m_id:02X}")
        print(f"   kind    = 0x{kind:02X} ({'normal/ground' if kind==1 else 'air/special'})")

        chunks = slice_region(buf, r_start, r_end, STRUCT_SIZE)
        print(f"   structs inside: {len(chunks)}")

        for c_off, chunk, nonzero in chunks:
            abs_addr = base + c_off
            print(f"      struct @ 0x{abs_addr:08X} (+0x{c_off:04X}), nz={nonzero}")
            print(f"        {hexdump(chunk)}")

        print()


if __name__ == "__main__":
    main()
