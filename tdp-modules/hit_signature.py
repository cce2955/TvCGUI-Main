# hit_signature.py
# Mines (+rel_off, stride) cells that flip on true HITs but rarely otherwise.

from __future__ import annotations
from typing import Dict, List, Tuple, Optional
from collections import defaultdict

from dolphin_io import rd32, rbytes
from config import (
    NONHIT_SAMPLE_PERIOD,
    HIT_SIG_TOPN,
    HIT_SIG_SCAN_LO,
    HIT_SIG_SCAN_HI,
    HIT_SIG_STRIDES,
)

# Types
RelStride = Tuple[int, int]  # (rel_off, stride)


class HitSignatureTracker:
    """
    Tracks memory cells (+rel_off, stride) that flip on real HITs.
    Usage pattern per frame:
      1) snapshot_now(slot_label, base)  -- for all active slots (victim/attackers)
      2) on_true_hit(t, slot_label, base) -- when you confirm a HIT for a victim
      3) background_nonhit_sample(t, slots_with_bases) -- occasionally, for non-hit diffs
      4) write_summary_csv() -- periodically persist aggregate stats
      5) top_lines() -- for HUD display
    """

    def __init__(self, event_writer=None, summary_writer=None) -> None:
        # Rolling per-slot snapshots
        #   prev_snap[slot][stride] -> List[int|None]
        #   curr_snap[slot][stride] -> List[int|None]
        self.prev_snap: Dict[str, Dict[int, List[Optional[int]]]] = {}
        self.curr_snap: Dict[str, Dict[int, List[Optional[int]]]] = {}

        # Aggregated hit/nonhit flips for each (rel_off, stride)
        self.hist: Dict[RelStride, Dict[str, int]] = defaultdict(lambda: {"hit": 0, "nonhit": 0})

        # CSV writers (if provided by caller)
        self._event_writer = event_writer
        self._summary_writer = summary_writer

        # Timers
        self._last_nonhit_sample: float = 0.0
        self._last_top_lines: List[str] = []

    # --------------------- low-level span readers ---------------------

    def _read_span(self, base: int, stride: int) -> List[Optional[int]]:
        """
        Read [HIT_SIG_SCAN_LO, HIT_SIG_SCAN_HI) at a given stride, returning a list of ints.
        For stride=4 we use rd32 (fewer syscalls); for 1/2 we use a single rbytes read.
        """
        lo, hi = HIT_SIG_SCAN_LO, HIT_SIG_SCAN_HI

        if stride == 4:
            out: List[Optional[int]] = []
            for rel in range(lo, hi, 4):
                v = rd32(base + rel)
                out.append(None if v is None else (v & 0xFFFFFFFF))
            return out

        raw = rbytes(base + lo, hi - lo)
        if not raw:
            return []

        if stride == 2:
            if len(raw) < 2:
                return []
            # big-endian 16-bit slices
            return [ (raw[i] << 8) | raw[i + 1] for i in range(0, len(raw) - (len(raw) % 2), 2) ]

        # stride == 1
        return list(raw)

    # --------------------- snapshotting & diffs ---------------------

    def snapshot_now(self, slot_label: str, base: int) -> None:
        """Capture current snapshot for all configured strides for a slot."""
        if not base:
            return
        snap: Dict[int, List[Optional[int]]] = {}
        for s in HIT_SIG_STRIDES:
            snap[s] = self._read_span(base, s)
        # move current -> prev, then set new current
        self.prev_snap[slot_label] = self.curr_snap.get(slot_label, snap)
        self.curr_snap[slot_label] = snap

    @staticmethod
    def _diff_indices(arrA: List[Optional[int]], arrB: List[Optional[int]], stride: int) -> List[Tuple[int, int, int, int]]:
        """
        Return a list of diffs as tuples: (rel_off, stride, pre_val, hit_val)
        between two snapshots at the same stride.
        """
        diffs: List[Tuple[int, int, int, int]] = []
        if not arrA or not arrB:
            return diffs
        n = min(len(arrA), len(arrB))
        for i in range(n):
            a = arrA[i]
            b = arrB[i]
            if a is None or b is None:
                continue
            if a != b:
                rel = HIT_SIG_SCAN_LO + i * stride
                diffs.append((rel, stride, int(a), int(b)))
        return diffs

    def _emit_hit_rows(
        self,
        t: float,
        slot: str,
        base_hex: str,
        diffs: List[Tuple[int, int, int, int]],
        post_vals_by_relstride: Dict[RelStride, int],
    ) -> None:
        """Write detailed per-hit diff rows (pre/hit/post) to CSV if writer provided."""
        if not self._event_writer:
            return
        for (rel, stride, pre_val, hit_val) in diffs:
            post_val = post_vals_by_relstride.get((rel, stride))
            self._event_writer.writerow([
                f"{t:.6f}", "HIT", slot, base_hex,
                f"0x{rel:03X}", stride,
                pre_val, hit_val, (post_val if post_val is not None else "")
            ])

    # --------------------- event hooks ---------------------

    def on_true_hit(self, t: float, slot_label: str, base: int) -> None:
        """
        Call this immediately when you confirm a real HIT for a victim.
        Compares prev vs current snapshots at all strides; records flips as "hit".
        Also takes a single "post" snapshot to include in event CSV.
        """
        pre = self.prev_snap.get(slot_label)
        hit = self.curr_snap.get(slot_label)
        if not pre or not hit:
            return

        diffs: List[Tuple[int, int, int, int]] = []
        for s in HIT_SIG_STRIDES:
            diffs.extend(self._diff_indices(pre.get(s), hit.get(s), s))

        # one-pass "post" snapshot (single read per stride)
        post: Dict[RelStride, int] = {}
        for s in HIT_SIG_STRIDES:
            arr = self._read_span(base, s)
            if arr:
                for i, v in enumerate(arr):
                    rel = HIT_SIG_SCAN_LO + i * s
                    post[(rel, s)] = v

        # record counts and emit detailed rows
        for (rel, stride, _, _) in diffs:
            self.hist[(rel, stride)]["hit"] += 1
        self._emit_hit_rows(t, slot_label, f"0x{base:08X}", diffs, post)

    def background_nonhit_sample(self, t: float, slots_with_bases: List[Tuple[str, int]]) -> None:
        """
        Occasionally sample diffs between consecutive snapshots during non-hit frames.
        This accumulates "nonhit" flips, acting as a false-positive baseline.
        """
        if (t - self._last_nonhit_sample) < NONHIT_SAMPLE_PERIOD:
            return
        self._last_nonhit_sample = t

        for slot_label, base in slots_with_bases:
            pre = self.prev_snap.get(slot_label)
            cur = self.curr_snap.get(slot_label)
            if not pre or not cur:
                continue
            for s in HIT_SIG_STRIDES:
                diffs = self._diff_indices(pre.get(s), cur.get(s), s)
                for (rel, stride, _, _) in diffs:
                    self.hist[(rel, stride)]["nonhit"] += 1

    # --------------------- reporting ---------------------

    def top_lines(self, topn: int = HIT_SIG_TOPN) -> List[str]:
        """
        Produce compact HUD lines sorted by lift (hit / (nonhit+1)).
        """
        items: List[Tuple[float, int, int, int, int]] = []
        for (rel, stride), c in self.hist.items():
            hit = c["hit"]
            nh = c["nonhit"]
            if hit == 0:
                continue
            lift = hit / (nh + 1.0)
            items.append((lift, hit, nh, rel, stride))

        items.sort(reverse=True)
        out: List[str] = []
        if items:
            out.append("HIT signature (lift  +off  s  hit/nonhit)")
            for (lift, hit, nh, rel, stride) in items[:topn]:
                out.append(f"  {lift:5.2f}  +0x{rel:03X}  s{stride}  {hit}/{nh}")

        self._last_top_lines = out
        return out

    def write_summary_csv(self) -> None:
        """
        Append a sorted snapshot of (rel_off_hex, stride, hit_flips, nonhit_flips, lift)
        to the provided summary_writer (if any).
        """
        if not self._summary_writer:
            return

        rows: List[Tuple[float, int, int, int, int]] = []
        for (rel, stride), c in self.hist.items():
            hit = c["hit"]
            nh = c["nonhit"]
            if hit == 0 and nh == 0:
                continue
            lift = hit / (nh + 1.0)
            rows.append((lift, rel, stride, hit, nh))

        rows.sort(reverse=True)
        for (lift, rel, stride, hit, nh) in rows:
            self._summary_writer.writerow([f"0x{rel:03X}", stride, hit, nh, f"{lift:.4f}"])
