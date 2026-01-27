# bonescan.py
# TvC "bones"/constraint field scanner
#
# Goal:
#   Given a fighter_base, scan a region AFTER the base for "bone-ish" float blocks
#   (arm/hand constraints, anchors, pole vectors, etc.). Rank candidates by:
#     - how many plausible floats are present
#     - how often they change across ticks
#     - whether they match common vec/quat-ish layouts (float quads w/ padding zeros)
#
# Notes from your observation:
#   Example region looks like it's *after* fighter_base (not inside it).
#   Pattern expectation: quads (big-endian) with padding zeros in between:
#     WW XX YY ZZ ?? 00 00 00 ?? 00 00 00  (repeating / grouped)
#
# This module does not "interpret" a specific bone table; it finds candidates and ranks them.

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple
import struct
import math
import time


@dataclass
class BoneResult:
    addr: int
    float_count: int
    change_count: int
    score: float
    pattern_hits: int
    sample: Tuple[float, ...]


def _be_f32(b: bytes) -> float:
    return struct.unpack(">f", b)[0]


def _is_finite(x: float) -> bool:
    return math.isfinite(x)


def _looks_like_bone_float(x: float) -> bool:
    # Heuristic: most of these constraint values you showed are around [-1..+1] or small.
    # Allow a bit wider for weights/scale and occasional offsets.
    if not _is_finite(x):
        return False
    ax = abs(x)
    if ax == 0.0:
        return True
    if ax <= 8.0:
        return True
    # Sometimes you get bigger pulls (or uninitialized junk) — keep tight to reduce noise.
    return False


def _count_plausible_floats(block: bytes, max_floats: int) -> Tuple[int, Tuple[float, ...]]:
    floats = []
    good = 0
    for i in range(max_floats):
        off = i * 4
        if off + 4 > len(block):
            break
        v = _be_f32(block[off : off + 4])
        floats.append(v)
        if _looks_like_bone_float(v):
            good += 1
    return good, tuple(floats)


def _pattern_hits_quads_with_padding(block: bytes) -> int:
    """
    Detect common "quad float + padding zeros" patterns.
    We don't require exact, we just add points when we see:
      - groups of 4 floats where at least 3 are plausible
      - and/or a 0x00000000 word in expected padding slots
    """
    hits = 0
    # Look for 0 words too (padding markers)
    words = [struct.unpack(">I", block[i : i + 4])[0] for i in range(0, len(block) - 3, 4)]

    # Sliding over float-quads
    for q in range(0, min(len(words), 16), 4):
        quad_bytes = block[q * 4 : q * 4 + 16]
        good, vals = _count_plausible_floats(quad_bytes, 4)
        if good >= 3:
            hits += 2
        # If any of the quad words are exactly 0, that's common for padding / unused W.
        if words[q + 0] == 0 or words[q + 1] == 0 or words[q + 2] == 0 or words[q + 3] == 0:
            hits += 1

    # Padding zeros in the next 16 bytes often show up as 0 words
    zero_words = sum(1 for w in words[:24] if w == 0)
    if zero_words >= 6:
        hits += 2
    elif zero_words >= 3:
        hits += 1

    return hits


class BoneScanner:
    """
    A lightweight incremental scanner.

    You call:
      scanner = BoneScanner(fighter_base)
      scanner.step()
    and read:
      scanner.results  (sorted by score desc)

    It samples memory repeatedly and tracks which aligned blocks change over time.
    """

    def __init__(
        self,
        fighter_base: int,
        *,
        # You said "after the fighter base we're scanning"
        # Default scan: base + 0x3000 .. base + 0x8000 (tune as needed)
        start_off: int = 0x3000,
        scan_len: int = 0x5000,
        align: int = 0x10,
        block_len: int = 0x60,
        max_results: int = 256,
    ):
        self.base = int(fighter_base)
        self.start = self.base + int(start_off)
        self.end = self.start + int(scan_len)
        self.align = int(align)
        self.block_len = int(block_len)
        self.max_results = int(max_results)

        self._prev_blocks: dict[int, bytes] = {}
        self._change_counts: dict[int, int] = {}
        self._float_counts: dict[int, int] = {}
        self._pattern_counts: dict[int, int] = {}
        self._last_samples: dict[int, Tuple[float, ...]] = {}

        self.results: List[BoneResult] = []
        self._cursor = self.start

    def _read(self, addr: int, n: int) -> Optional[bytes]:
        try:
            from dolphin_io import rbytes
        except Exception:
            return None
        try:
            return rbytes(addr, n)
        except Exception:
            return None

    def _score_addr(self, addr: int) -> float:
        cc = self._change_counts.get(addr, 0)
        fc = self._float_counts.get(addr, 0)
        ph = self._pattern_counts.get(addr, 0)

        # Weight changes highest (we want "live" constraints),
        # then float plausibility count, then pattern hits.
        return (cc * 3.0) + (fc * 0.75) + (ph * 1.25)

    def _update_results(self) -> None:
        items = []
        for addr in self._change_counts.keys() | self._float_counts.keys() | self._pattern_counts.keys():
            score = self._score_addr(addr)
            if score <= 0:
                continue
            fc = self._float_counts.get(addr, 0)
            cc = self._change_counts.get(addr, 0)
            ph = self._pattern_counts.get(addr, 0)
            sample = self._last_samples.get(addr, ())
            items.append(
                BoneResult(
                    addr=addr,
                    float_count=fc,
                    change_count=cc,
                    score=score,
                    pattern_hits=ph,
                    sample=sample[:8],
                )
            )
        items.sort(key=lambda r: (r.score, r.change_count, r.float_count, r.pattern_hits), reverse=True)
        self.results = items[: self.max_results]

    def step(self, *, budget_blocks: int = 128) -> None:
        """
        Scan a chunk of the region each call to keep UI responsive.
        """
        # Wrap cursor
        if self._cursor >= self.end:
            self._cursor = self.start

        blocks_done = 0
        while blocks_done < budget_blocks and self._cursor < self.end:
            addr = self._cursor
            self._cursor += self.align
            blocks_done += 1

            data = self._read(addr, self.block_len)
            if not data or len(data) < 0x20:
                continue

            prev = self._prev_blocks.get(addr)
            if prev is not None and prev != data:
                self._change_counts[addr] = self._change_counts.get(addr, 0) + 1
            self._prev_blocks[addr] = data

            # Float plausibility
            good, floats = _count_plausible_floats(data[:0x40], 16)
            self._float_counts[addr] = max(self._float_counts.get(addr, 0), good)
            self._last_samples[addr] = floats

            # Pattern heuristic
            ph = _pattern_hits_quads_with_padding(data[:0x60])
            if ph:
                self._pattern_counts[addr] = max(self._pattern_counts.get(addr, 0), ph)

        self._update_results()
