# bonescan.py
# TvC structured-float ("bones"/constraint) block scanner
#
# Goal:
#   Scan memory and surface blocks that look like structured float slabs with
#   padding/regularity (vec/quats, matrices, constraints, anchors, etc.).
#
# Design:
#   - Deterministic, structural only
#   - No semantic interpretation
#   - Absolute OR base-relative ranges (base is optional)
#   - Static blocks are valid
#   - Can be used incrementally (UI) or as a standalone scan pass (CLI)
#
# IMPORTANT:
#   - If you pass fighter_base positionally, this class supports it for backwards compat.
#   - If you want to avoid base entirely, use absolute_start/absolute_end.
#
# Examples:
#   UI/base-relative:
#       scanner = BoneScanner(anchor, start_off=0x3000, scan_len=0x9000)
#
#   Absolute (no base):
#       scanner = BoneScanner(absolute_start=0x92477000, absolute_end=0x92478000)
#
#   Run full pass (one-shot):
#       results = scanner.run_full()
#
#   Incremental:
#       scanner.step(budget_blocks=256); scanner.results

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple, Dict
import struct
import math


# ============================================================
# Result container
# ============================================================

@dataclass
class BoneResult:
    addr: int
    float_count: int
    zero_count: int
    change_count: int
    pattern_hits: int
    score: float
    sample: Tuple[float, ...]


# ============================================================
# Low-level helpers
# ============================================================

def _be_u32(b: bytes) -> int:
    return struct.unpack(">I", b)[0]


def _be_f32(b: bytes) -> float:
    return struct.unpack(">f", b)[0]


def _finite(x: float) -> bool:
    return math.isfinite(x)


def _plausible_float(x: float, *, abs_max: float) -> bool:
    if not _finite(x):
        return False
    ax = abs(x)
    return ax == 0.0 or ax <= abs_max


# ============================================================
# Block analysis
# ============================================================

def analyze_block(
    data: bytes,
    *,
    sample_words: int = 16,      # number of 32-bit words sampled for floats/zeros (default 0x40 bytes)
    plausible_abs_max: float = 16.0,
) -> Tuple[int, int, Tuple[float, ...]]:
    """
    Analyze a raw block and return:
      (plausible_float_count, zero_word_count, float_sample_tuple)

    - Evaluates first sample_words*4 bytes (default 0x40)
    """
    floats: list[float] = []
    plausible = 0
    zeros = 0

    limit = min(len(data), sample_words * 4)
    for off in range(0, limit, 4):
        word = _be_u32(data[off:off + 4])
        if word == 0:
            zeros += 1

        f = _be_f32(data[off:off + 4])
        floats.append(f)
        if _plausible_float(f, abs_max=plausible_abs_max):
            plausible += 1

    return plausible, zeros, tuple(floats)


def quad_pattern_hits(
    data: bytes,
    *,
    max_quads: int = 16,         # check first N quads (N*16 bytes)
    require_plausible_in_quad: int = 0,  # 0 = purely structural, >0 = also require plausible floats in quad
    plausible_abs_max: float = 16.0,
) -> int:
    """
    Structural pattern score for "quad-ish layouts with padding zeros".

    We add points when:
      - a quad (4 words) contains 1+ zeros (common padding/ununsed slots)
      - a quad contains 2+ zeros (stronger padding signal)
    Optional:
      - require_plausible_in_quad: if >0, count the quad only if at least that many floats in the quad are plausible.
        (kept off by default, because you want to find the same regions your CLI found, including static-ish blocks)
    """
    hits = 0
    if not data:
        return 0

    words = [_be_u32(data[i:i + 4]) for i in range(0, len(data) - 3, 4)]
    quad_limit = min(max_quads * 4, len(words) // 4 * 4)

    for i in range(0, quad_limit, 4):
        quad = words[i:i + 4]
        if len(quad) < 4:
            break

        if require_plausible_in_quad > 0:
            quad_bytes = data[i * 4 : i * 4 + 16]
            ok, _, _ = analyze_block(
                quad_bytes,
                sample_words=4,
                plausible_abs_max=plausible_abs_max,
            )
            if ok < require_plausible_in_quad:
                continue

        z = quad.count(0)
        if z >= 1:
            hits += 1
        if z >= 2:
            hits += 1

    return hits


# ============================================================
# Scanner
# ============================================================

class BoneScanner:
    """
    Incremental structured-memory scanner.

    Supports:
      - Absolute ranges: absolute_start + absolute_end
      - Base-relative ranges: base + start_off + scan_len

    Backwards compat:
      - BoneScanner(fighter_base, ...) positional base is allowed.
    """

    def __init__(
        self,
        base: int | None = None,
        *,
        # base-relative
        start_off: int = 0x3000,
        scan_len: int = 0x5000,
        # absolute
        absolute_start: int | None = None,
        absolute_end: int | None = None,
        # scan params
        align: int = 0x10,
        block_len: int = 0x60,
        max_results: int = 256,
        # heuristics
        plausible_abs_max: float = 16.0,
        sample_words: int = 16,                 # 16 words -> 0x40 bytes
        max_pattern_quads: int = 16,            # 16 quads -> 0x100 bytes if block allows
        pattern_require_plausible_in_quad: int = 0,
        # score weights (tuned to match your CLI behavior: float/zeros/quads are dominant; changes are minor)
        w_floats: float = 1.5,
        w_zeros: float = 0.75,
        w_quads: float = 2.0,
        w_changes: float = 0.5,
    ):
        # ------------------------
        # Resolve bounds
        # ------------------------

        if absolute_start is not None or absolute_end is not None:
            if absolute_start is None or absolute_end is None:
                raise ValueError("absolute_start and absolute_end must be provided together")
            self.start = int(absolute_start)
            self.end = int(absolute_end)
            self.base = None
        else:
            if base is None:
                raise ValueError("Provide either (base) for base-relative scan or (absolute_start, absolute_end)")
            self.base = int(base)
            self.start = self.base + int(start_off)
            self.end = self.start + int(scan_len)

        if self.end <= self.start:
            raise ValueError("Invalid scan range (end <= start)")

        # ------------------------
        # Params
        # ------------------------

        self.align = int(align)
        self.block_len = int(block_len)
        self.max_results = int(max_results)

        self.plausible_abs_max = float(plausible_abs_max)
        self.sample_words = int(sample_words)
        self.max_pattern_quads = int(max_pattern_quads)
        self.pattern_require_plausible_in_quad = int(pattern_require_plausible_in_quad)

        self.w_floats = float(w_floats)
        self.w_zeros = float(w_zeros)
        self.w_quads = float(w_quads)
        self.w_changes = float(w_changes)

        # ------------------------
        # State
        # ------------------------

        self._cursor = self.start

        self._prev: Dict[int, bytes] = {}
        self._changes: Dict[int, int] = {}
        self._float_ok: Dict[int, int] = {}
        self._zeros: Dict[int, int] = {}
        self._patterns: Dict[int, int] = {}
        self._samples: Dict[int, Tuple[float, ...]] = {}

        self.results: List[BoneResult] = []

    # --------------------------------------------------------

    def set_bounds_absolute(self, start: int, end: int) -> None:
        """
        Override bounds at runtime (useful in UI without rebuilding object).
        """
        s = int(start)
        e = int(end)
        if e <= s:
            raise ValueError("Invalid scan range (end <= start)")
        self.start = s
        self.end = e
        self.base = None
        self._cursor = self.start

    def set_bounds_relative(self, base: int, *, start_off: int, scan_len: int) -> None:
        """
        Override bounds at runtime using base-relative rule.
        """
        b = int(base)
        s = b + int(start_off)
        e = s + int(scan_len)
        if e <= s:
            raise ValueError("Invalid scan range (end <= start)")
        self.base = b
        self.start = s
        self.end = e
        self._cursor = self.start

    # --------------------------------------------------------

    def _read(self, addr: int, n: int) -> Optional[bytes]:
        try:
            from dolphin_io import rbytes
            return rbytes(addr, n)
        except Exception:
            return None

    # --------------------------------------------------------

    def _score(self, addr: int) -> float:
        fc = self._float_ok.get(addr, 0)
        zc = self._zeros.get(addr, 0)
        ph = self._patterns.get(addr, 0)
        cc = self._changes.get(addr, 0)
        return (fc * self.w_floats) + (zc * self.w_zeros) + (ph * self.w_quads) + (cc * self.w_changes)

    # --------------------------------------------------------

    def _rebuild_results(self) -> None:
        out: list[BoneResult] = []

        addrs = self._float_ok.keys() | self._zeros.keys() | self._patterns.keys() | self._changes.keys()
        for addr in addrs:
            score = self._score(addr)
            if score <= 0:
                continue

            out.append(
                BoneResult(
                    addr=addr,
                    float_count=self._float_ok.get(addr, 0),
                    zero_count=self._zeros.get(addr, 0),
                    change_count=self._changes.get(addr, 0),
                    pattern_hits=self._patterns.get(addr, 0),
                    score=score,
                    sample=self._samples.get(addr, ())[:8],
                )
            )

        out.sort(key=lambda r: (r.score, r.pattern_hits, r.float_count, r.zero_count, r.change_count), reverse=True)
        self.results = out[: self.max_results]

    # --------------------------------------------------------

    def step(self, *, budget_blocks: int = 128) -> None:
        """
        Incremental scan pass.
        """
        if self._cursor >= self.end:
            self._cursor = self.start

        processed = 0

        while processed < budget_blocks and self._cursor < self.end:
            addr = self._cursor
            self._cursor += self.align
            processed += 1

            data = self._read(addr, self.block_len)
            if not data or len(data) < 0x20:
                continue

            prev = self._prev.get(addr)
            if prev is not None and prev != data:
                self._changes[addr] = self._changes.get(addr, 0) + 1
            self._prev[addr] = data

            fc, zc, floats = analyze_block(
                data,
                sample_words=self.sample_words,
                plausible_abs_max=self.plausible_abs_max,
            )
            # keep max seen (helps stabilize ranking across frames)
            self._float_ok[addr] = max(self._float_ok.get(addr, 0), fc)
            self._zeros[addr] = max(self._zeros.get(addr, 0), zc)
            self._samples[addr] = floats

            ph = quad_pattern_hits(
                data,
                max_quads=self.max_pattern_quads,
                require_plausible_in_quad=self.pattern_require_plausible_in_quad,
                plausible_abs_max=self.plausible_abs_max,
            )
            if ph:
                self._patterns[addr] = max(self._patterns.get(addr, 0), ph)

        self._rebuild_results()

    # --------------------------------------------------------

    def run_full(self, *, passes: int = 1) -> List[BoneResult]:
        """
        One-shot scan across the full range.
        passes>1 lets you accumulate change_count across multiple sweeps.
        """
        p = max(1, int(passes))
        for _ in range(p):
            self._cursor = self.start
            # number of aligned addresses in range (ceil)
            total = (self.end - self.start + (self.align - 1)) // self.align
            # process in big chunks without blowing UI
            remaining = total
            while remaining > 0:
                chunk = min(4096, remaining)
                self.step(budget_blocks=chunk)
                remaining -= chunk
        return self.results
