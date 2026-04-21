# projectile.py
#
# Projectile instance -> template block resolver (heuristic)
#
# Why this exists:
#   - Live projectile instances often do NOT keep a direct pointer back to the per-character
#     template block (e.g. Ryu hadou template at 0x908D08F0).
#   - Instance headers may contain pointers to other actor-definition structs or pool memory.
#   - We therefore:
#       1) validate instances harder (avoid "HEAD", random pointers, etc.)
#       2) for each pointer candidate, attempt to locate a nearby template block by scanning
#          for: segment header 00 00 00 04, delimiter markers, slice repetition, physics cluster
#
# Usage:
#   python projectile.py --owner 0x9246B9C0
#
# Notes:
#   - This tool reads EFFECTIVE (guest virtual) addresses.
#   - MEM2 range is assumed 0x90000000..0x94000000 (from your constants.py).
#
from __future__ import annotations

import argparse
import struct
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

from dolphin_io import hook, rbytes, rd32
from constants import MEM2_LO, MEM2_HI


SEG_HDR = 0x00000004
SEG_HDR_BE = b"\x00\x00\x00\x04"
FF8 = b"\xFF" * 8
FF4 = b"\xFF" * 4

# Physics cluster heuristic:
#   [f32 speed-ish][f32 accel-ish][u32 0][f32 cap-ish]
def be_u32(b: bytes) -> int:
    return struct.unpack(">I", b)[0]

def be_f32(b: bytes) -> float:
    return struct.unpack(">f", b)[0]

def is_mem2_ptr(x: int) -> bool:
    return MEM2_LO <= x < MEM2_HI

def is_plausible_u32(x: int) -> bool:
    return 0 <= x <= 0xFFFFFFFF

def is_finite(f: float) -> bool:
    return math.isfinite(f)

def plausible_speed(f: float) -> bool:
    if not is_finite(f):
        return False
    af = abs(f)
    return 0.25 <= af <= 2000.0

def plausible_accel(f: float) -> bool:
    if not is_finite(f):
        return False
    af = abs(f)
    return 0.0 <= af <= 2000.0

def plausible_cap(f: float) -> bool:
    if not is_finite(f):
        return False
    af = abs(f)
    return 0.25 <= af <= 20000.0

@dataclass
class PhysCluster:
    ea: int
    speed: float
    accel: float
    cap: float

@dataclass
class TemplateBlock:
    block_ea: int
    score: int
    why: str
    phys: List[PhysCluster]

@dataclass
class InstCandidate:
    inst_ea: int
    life_u32: int
    collider_u32: int
    ptrs: List[int]

def read_u32(ea: int) -> int:
    return rd32(ea)

def read_bytes(ea: int, n: int) -> bytes:
    return rbytes(ea, n) or b""

def find_phys_clusters_in_window(base_ea: int, data: bytes) -> List[PhysCluster]:
    out: List[PhysCluster] = []
    # aligned scan
    for off in range(0, len(data) - 16, 4):
        w0 = data[off:off+4]
        w1 = data[off+4:off+8]
        w2 = data[off+8:off+12]
        w3 = data[off+12:off+16]
        if w2 != b"\x00\x00\x00\x00":
            continue
        speed = be_f32(w0)
        accel = be_f32(w1)
        cap = be_f32(w3)
        if not (plausible_speed(speed) and plausible_accel(accel) and plausible_cap(cap)):
            continue
        out.append(PhysCluster(ea=base_ea + off, speed=speed, accel=accel, cap=cap))
    return out

def score_template_block(block_ea: int) -> Optional[TemplateBlock]:
    """
    Evaluate whether block_ea looks like a projectile template block.
    Returns a scored object if it seems plausible, else None.
    """
    # Read enough to see delimiters + multiple slices
    data = read_bytes(block_ea, 0x2000)
    if len(data) < 0x200:
        return None

    # Must start with 00 00 00 04 somewhere very near the start
    if data[:4] != SEG_HDR_BE:
        return None

    score = 0
    why_parts: List[str] = []

    # Look for delimiters
    has_ff8 = (FF8 in data[:0x400])
    has_ff4 = (FF4 in data[:0x400])
    if has_ff8:
        score += 4
        why_parts.append("has FF*8 delimiter near start")
    if has_ff4:
        score += 2
        why_parts.append("has FF*4 word near start")

    # Find physics clusters in block
    phys = find_phys_clusters_in_window(block_ea, data)
    if len(phys) >= 3:
        score += 6
        why_parts.append(f"has {len(phys)} physics clusters in 0x2000 window")
    elif len(phys) >= 1:
        score += 2
        why_parts.append(f"has {len(phys)} physics cluster(s) in 0x2000 window")
    else:
        # No physics clusters means not useful for your workflow
        return None

    # Slice repetition check: do physics clusters show a dominant stride?
    # This helps distinguish “random float region” from “template slices.”
    if len(phys) >= 3:
        addrs = sorted(p.ea for p in phys)
        gaps = {}
        for i in range(1, len(addrs)):
            g = addrs[i] - addrs[i-1]
            if 0x40 <= g <= 0x800:
                gaps[g] = gaps.get(g, 0) + 1
        if gaps:
            best_g = max(gaps.items(), key=lambda kv: kv[1])[0]
            score += 3
            why_parts.append(f"dominant phys gap ~0x{best_g:X}")

    return TemplateBlock(
        block_ea=block_ea,
        score=score,
        why="; ".join(why_parts) if why_parts else "ok",
        phys=phys[:12],
    )

def scan_back_for_block_start(ea: int, back: int = 0x8000) -> List[int]:
    """
    From an arbitrary EA, scan backward for SEG_HDR_BE occurrences that could be block starts.
    Return candidate block start EAs (descending proximity).
    """
    start = max(MEM2_LO, ea - back)
    data = read_bytes(start, ea - start + 4)
    out: List[int] = []
    if not data:
        return out
    idx = 0
    while True:
        j = data.find(SEG_HDR_BE, idx)
        if j < 0:
            break
        out.append(start + j)
        idx = j + 1
    # closest last
    out.sort(key=lambda x: abs(ea - x))
    return out[:24]

def ptr_candidates_from_instance(inst_ea: int, header_size: int = 0x200) -> List[int]:
    """
    Scan the instance header region for dwords that look like MEM2 pointers.
    """
    b = read_bytes(inst_ea, header_size)
    if len(b) < 0x40:
        return []
    ptrs: List[int] = []
    for off in range(0, len(b) - 4, 4):
        x = be_u32(b[off:off+4])
        if is_mem2_ptr(x):
            ptrs.append(x)
    # de-dupe, preserve order
    seen = set()
    out = []
    for p in ptrs:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out

def is_valid_instance(inst_ea: int, owner_ea: int) -> bool:
    """
    scan was picking up garbage. This filters it.
    We don't know the full instance struct, so use conservative checks:
      - inst aligned
      - inst in MEM2
      - owner pointer appears somewhere early in header
      - life field looks like a small integer in common cases OR at least not ASCII/magic
    """
    if (inst_ea & 0x3) != 0:
        return False
    if not is_mem2_ptr(inst_ea):
        return False

    hdr = read_bytes(inst_ea, 0x100)
    if len(hdr) < 0x40:
        return False

    owner_be = struct.pack(">I", owner_ea)
    if owner_be not in hdr:
        # Not owned by this fighter (or ownership isn't stored plainly here)
        return False

    # Reject obvious non-instances: "HEAD" etc found in output
    if b"HEAD" in hdr or b"HEAP" in hdr:
        return False

    return True

def find_instances_owned_by(owner_ea: int, max_results: int = 32) -> List[int]:
    """
    Very rough instance scan: look for owner pointer in MEM2 and treat those locations as candidate inst bases.
    This is intentionally conservative and may miss things; it is meant to avoid garbage.
    """
    owner_be = struct.pack(">I", owner_ea)
    hits: List[int] = []

    step = 0x200000
    ea = MEM2_LO
    while ea < MEM2_HI and len(hits) < max_results * 50:
        data = read_bytes(ea, min(step, MEM2_HI - ea))
        if not data:
            ea += step
            continue
        idx = 0
        while True:
            j = data.find(owner_be, idx)
            if j < 0:
                break
            # Guess an instance base somewhere before this field.
            # We'll try a few common back offsets and keep those that validate.
            field_ea = ea + j
            for back in (0x10, 0x20, 0x40, 0x80, 0x100):
                inst_ea = field_ea - back
                if is_valid_instance(inst_ea, owner_ea):
                    hits.append(inst_ea)
            idx = j + 1
        ea += step

    # de-dupe, keep within MEM2
    uniq = []
    seen = set()
    for x in hits:
        if is_mem2_ptr(x) and x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq[:max_results]

def resolve_template_from_instance(inst_ea: int) -> List[TemplateBlock]:
    """
    Try to locate the per-character template block corresponding to a live instance.
    Returns a list of plausible TemplateBlock hits, best-first.
    """
    ptrs = ptr_candidates_from_instance(inst_ea, 0x200)

    blocks: List[TemplateBlock] = []

    # For each pointer candidate, scan backward for nearby SEG_HDR_BE and score those blocks.
    for p in ptrs:
        for bstart in scan_back_for_block_start(p, back=0x20000):
            tb = score_template_block(bstart)
            if tb:
                blocks.append(tb)

    # Also scan around the instance itself; sometimes the copied slice sits near the instance pool.
    for bstart in scan_back_for_block_start(inst_ea, back=0x20000):
        tb = score_template_block(bstart)
        if tb:
            blocks.append(tb)

    # De-dupe by block_ea, keep best score
    best_by_ea = {}
    for b in blocks:
        prev = best_by_ea.get(b.block_ea)
        if prev is None or b.score > prev.score:
            best_by_ea[b.block_ea] = b

    out = list(best_by_ea.values())
    out.sort(key=lambda x: (-x.score, x.block_ea))
    return out[:12]

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", type=lambda s: int(s, 16), required=True, help="fighter_base EA (hex), e.g. 0x9246B9C0")
    ap.add_argument("--max", type=int, default=8, help="max instances to test")
    args = ap.parse_args()

    print("[info] hooking dolphin...")
    hook()
    print("[info] hooked")
    owner = args.owner
    print(f"[info] owner override: 0x{owner:08X}")

    print("[scan] searching for instances owned by fighter_base (conservative)...")
    insts = find_instances_owned_by(owner, max_results=max(args.max, 8))
    print(f"[scan] found {len(insts)} instances (showing top {min(len(insts), args.max)}):")
    for i, inst in enumerate(insts[:args.max]):
        # print a couple nearby words for sanity
        w0 = read_u32(inst)
        w1 = read_u32(inst + 4)
        print(f"  [{i}] inst=0x{inst:08X} w0=0x{w0:08X} w1=0x{w1:08X}")

    if not insts:
        print("[scan] none found. Either owner pointer isn't stored plainly in header, or offsets differ in this build.")
        return 2

    for inst in insts[:args.max]:
        print(f"[bt] instance=0x{inst:08X}")
        ptrs = ptr_candidates_from_instance(inst, 0x200)
        print(f"[bt] ptr candidates in header: {len(ptrs)}")
        if ptrs:
            print("  " + " ".join(f"0x{x:08X}" for x in ptrs[:16]))

        blocks = resolve_template_from_instance(inst)
        if not blocks:
            print("  [bt] no plausible template blocks near instance/ptrs")
            continue

        print(f"  [bt] template candidates (best-first): {len(blocks)}")
        for bi, b in enumerate(blocks[:6]):
            phys0 = b.phys[0] if b.phys else None
            extra = ""
            if phys0:
                extra = f" phys0@0x{phys0.ea:08X} speed={phys0.speed:.3f} accel={phys0.accel:.3f} cap={phys0.cap:.3f}"
            print(f"    [{bi}] block=0x{b.block_ea:08X} score={b.score} {b.why}{extra}")

    return 0

if __name__ == "__main__":
    raise SystemExit(main())
