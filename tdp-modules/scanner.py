#!/usr/bin/env python3
"""
scan_structured_blocks.py

Purpose:
  Scan a memory range and print addresses that look like
  structured float slabs with padding (the block you showed).

No UI. No animation logic. No heuristics beyond structure.
"""

from __future__ import annotations
import struct
import math
import argparse
from typing import Optional

# ------------------------------------------------------------
# Dolphin hook
# ------------------------------------------------------------

def rbytes(addr: int, size: int) -> Optional[bytes]:
    try:
        from dolphin_io import rbytes as _rb
        return _rb(addr, size)
    except Exception:
        return None


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------

def be_u32(b: bytes) -> int:
    return struct.unpack(">I", b)[0]


def be_f32(b: bytes) -> float:
    return struct.unpack(">f", b)[0]


def finite(x: float) -> bool:
    return math.isfinite(x)


def plausible_float(x: float) -> bool:
    if not finite(x):
        return False
    ax = abs(x)
    return ax == 0.0 or ax <= 16.0


# ------------------------------------------------------------
# Core detection logic
# ------------------------------------------------------------

def analyze_block(data: bytes) -> dict:
    floats = []
    float_ok = 0
    zero_words = 0

    for off in range(0, min(len(data), 0x40), 4):
        word = be_u32(data[off:off + 4])
        if word == 0:
            zero_words += 1

        f = be_f32(data[off:off + 4])
        floats.append(f)
        if plausible_float(f):
            float_ok += 1

    quad_hits = 0
    words = [be_u32(data[i:i+4]) for i in range(0, len(data)-3, 4)]
    for i in range(0, len(words)-3, 4):
        quad = words[i:i+4]
        z = quad.count(0)
        if z >= 1:
            quad_hits += 1
        if z >= 2:
            quad_hits += 1

    score = (
        float_ok * 1.5 +
        zero_words * 0.75 +
        quad_hits * 2.0
    )

    return {
        "float_ok": float_ok,
        "zeros": zero_words,
        "quad_hits": quad_hits,
        "score": score,
        "sample": floats[:8],
    }


# ------------------------------------------------------------
# CLI scan
# ------------------------------------------------------------

def scan_range(start: int, end: int, align: int, block_len: int, min_score: float):
    addr = start
    hits = []

    while addr < end:
        data = rbytes(addr, block_len)
        if data and len(data) >= 0x20:
            info = analyze_block(data)
            if info["score"] >= min_score:
                hits.append((addr, info))
        addr += align

    return hits


# ------------------------------------------------------------
# Main
# ------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Structured float slab scanner")
    ap.add_argument("--start", required=True, type=lambda x: int(x, 16), help="start address (hex)")
    ap.add_argument("--end", required=True, type=lambda x: int(x, 16), help="end address (hex)")
    ap.add_argument("--align", default=0x10, type=lambda x: int(x, 16), help="alignment (hex)")
    ap.add_argument("--block", default=0x60, type=lambda x: int(x, 16), help="block length (hex)")
    ap.add_argument("--min-score", default=20.0, type=float, help="minimum score to print")

    args = ap.parse_args()

    print(f"Scanning 0x{args.start:08X} .. 0x{args.end:08X}")
    print(f"align=0x{args.align:X} block=0x{args.block:X} min_score={args.min_score}")
    print("-" * 80)

    hits = scan_range(
        args.start,
        args.end,
        args.align,
        args.block,
        args.min_score,
    )

    for addr, info in sorted(hits, key=lambda x: x[1]["score"], reverse=True):
        samp = ", ".join(f"{v:+.3f}" for v in info["sample"])
        print(
            f"0x{addr:08X}  "
            f"score={info['score']:.2f}  "
            f"floats={info['float_ok']:02d}  "
            f"zeros={info['zeros']:02d}  "
            f"quads={info['quad_hits']:02d}  "
            f"[ {samp} ]"
        )

    print("-" * 80)
    print(f"{len(hits)} candidates")


if __name__ == "__main__":
    main()
