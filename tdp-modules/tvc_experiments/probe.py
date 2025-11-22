#!/usr/bin/env python3
"""
Marker-region struct slicer for TvC dumps.

- Treats any 01 xx 01 or 01 xx 04 as a marker.
- Defines regions as [marker, next_marker).
- Slices each region into fixed-size structs ("houses").
- Dumps a summary to stdout; you can adapt to CSV/JSON as needed.

Usage:
    python probe.py dump.bin > regions.txt
"""

import argparse
import os
from dataclasses import dataclass
from typing import List, Iterable


# ---------------------------------------------------------------------------
# Config – tweak these based on what you’ve seen in the dumps
# ---------------------------------------------------------------------------

# Size of a single "house" / struct in bytes.
# Change this if your house size is different (0x20, 0x24, 0x28, etc).
DEFAULT_STRUCT_SIZE = 0x20

# Minimum number of non-zero bytes in a chunk to consider it a "real" struct.
MIN_NONZERO_BYTES = 4

# Align region starts to this many bytes (4 is sane for PPC floats/words).
ALIGNMENT = 4


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class Marker:
    offset: int       # byte offset in the dump
    move_id: int      # the middle xx byte in 01 xx 01 / 01 xx 04
    kind: int         # last byte: 0x01 or 0x04

    def label(self) -> str:
        return f"01 {self.move_id:02X} {self.kind:02X}"


@dataclass
class Region:
    marker: Marker
    start: int        # start of region (after marker)
    end: int          # end of region (exclusive)


@dataclass
class StructChunk:
    offset: int       # absolute offset
    size: int
    nonzero_count: int
    bytes_view: bytes


# ---------------------------------------------------------------------------
# Marker scanning
# ---------------------------------------------------------------------------

def find_markers(data: bytes) -> List[Marker]:
    """
    Find all 01 xx 01 and 01 xx 04 markers in the dump.
    Returns a list of Marker instances sorted by offset.
    """
    markers: List[Marker] = []
    length = len(data)

    i = 0
    while i <= length - 3:
        b0 = data[i]
        if b0 == 0x01:
            b1 = data[i + 1]
            b2 = data[i + 2]
            if b2 in (0x01, 0x04):
                markers.append(Marker(offset=i, move_id=b1, kind=b2))
                # Advance by 3 (markers are dense enough that overlapping is unlikely)
                i += 3
                continue
        i += 1

    markers.sort(key=lambda m: m.offset)
    return markers


def build_regions(markers: List[Marker], data_len: int) -> List[Region]:
    """
    Build [marker, next_marker) regions for all markers.

    Region start is just after the 3-byte marker itself.
    Region end is the next marker's offset, or end-of-data.
    """
    regions: List[Region] = []

    for idx, m in enumerate(markers):
        raw_start = m.offset + 3  # skip the marker bytes
        if idx + 1 < len(markers):
            raw_end = markers[idx + 1].offset
        else:
            raw_end = data_len

        # Clamp and ignore obviously broken spans
        raw_start = max(raw_start, 0)
        raw_end = max(raw_end, raw_start)

        regions.append(Region(marker=m, start=raw_start, end=raw_end))

    return regions


# ---------------------------------------------------------------------------
# Struct slicing
# ---------------------------------------------------------------------------

def align_up(value: int, alignment: int) -> int:
    if alignment <= 1:
        return value
    rem = value % alignment
    if rem == 0:
        return value
    return value + (alignment - rem)


def slice_region_into_structs(
    data: bytes,
    region: Region,
    struct_size: int,
    min_nonzero: int,
    alignment: int,
) -> List[StructChunk]:
    """
    Slice [region.start, region.end) into fixed-size structs, aligned to `alignment`.

    Returns only those chunks with >= min_nonzero non-zero bytes.
    """
    chunks: List[StructChunk] = []
    start = align_up(region.start, alignment)
    end = region.end

    if struct_size <= 0:
        return chunks

    # Don't bother if region is too small
    if end - start < struct_size:
        return chunks

    offset = start
    while offset + struct_size <= end:
        chunk = data[offset:offset + struct_size]
        nonzeros = sum(1 for b in chunk if b != 0)
        if nonzeros >= min_nonzero:
            chunks.append(StructChunk(
                offset=offset,
                size=struct_size,
                nonzero_count=nonzeros,
                bytes_view=chunk,
            ))
        offset += struct_size

    return chunks


# ---------------------------------------------------------------------------
# Pretty-print helpers
# ---------------------------------------------------------------------------

def format_struct_line(chunk: StructChunk, base: int = 0) -> str:
    rel = chunk.offset - base
    return (
        f"    struct @ 0x{chunk.offset:08X} (rel +0x{rel:04X}), "
        f"size=0x{chunk.size:X}, nonzero={chunk.nonzero_count}"
    )


def dump_regions_with_structs(
    data: bytes,
    regions: List[Region],
    struct_size: int,
    min_nonzero: int,
    alignment: int,
) -> None:
    for idx, region in enumerate(regions):
        m = region.marker
        print(f"[Region {idx:03d}] marker={m.label()} @ 0x{m.offset:08X}")
        print(f"  move_id: 0x{m.move_id:02X}")
        print(f"  kind:    0x{m.kind:02X}  ({'ground/primary' if m.kind == 0x01 else 'alt/air/special?'} )")
        print(f"  span:    0x{region.start:08X} - 0x{region.end:08X} "
              f"(len=0x{(region.end - region.start):X})")

        structs = slice_region_into_structs(
            data,
            region,
            struct_size=struct_size,
            min_nonzero=min_nonzero,
            alignment=alignment,
        )
        print(f"  structs: {len(structs)} (size=0x{struct_size:X})")

        for s in structs:
            print(format_struct_line(s, base=region.start))

        print()  # blank line between regions


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Iterable[str] = None) -> None:
    parser = argparse.ArgumentParser(
        description="Identify fixed-size structs inside 01 xx 01 / 01 xx 04 marker regions."
    )
    parser.add_argument("dump", help="Path to binary dump file")
    parser.add_argument(
        "--struct-size",
        type=lambda s: int(s, 0),
        default=DEFAULT_STRUCT_SIZE,
        help=f"Struct size in bytes (decimal or 0xHEX). Default: 0x{DEFAULT_STRUCT_SIZE:X}",
    )
    parser.add_argument(
        "--min-nonzero",
        type=int,
        default=MIN_NONZERO_BYTES,
        help=f"Minimum non-zero bytes in a chunk to keep it (default {MIN_NONZERO_BYTES})",
    )
    parser.add_argument(
        "--no-align",
        action="store_true",
        help="Disable 4-byte alignment of region starts",
    )

    args = parser.parse_args(list(argv) if argv is not None else None)

    path = args.dump
    if not os.path.isfile(path):
        raise SystemExit(f"Dump file not found: {path}")

    with open(path, "rb") as f:
        data = f.read()

    markers = find_markers(data)
    if not markers:
        print("No markers found (01 xx 01 / 01 xx 04).")
        return

    regions = build_regions(markers, len(data))

    # Use local config instead of mutating globals
    min_nonzero = args.min_nonzero
    alignment = 1 if args.no_align else ALIGNMENT

    dump_regions_with_structs(
        data,
        regions,
        struct_size=args.struct_size,
        min_nonzero=min_nonzero,
        alignment=alignment,
    )


if __name__ == "__main__":
    main()
