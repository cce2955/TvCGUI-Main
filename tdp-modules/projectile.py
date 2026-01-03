# projectile_backtrace.py
#
# Work backwards from a live projectile instance to its static definition block.
#
# Requirements:
#   - dolphin_io.py provides: hook(), rd32(), rdf32()
#   - Uses your documented fighter manager pointers to auto-resolve fighter_base
#
# Usage:
#   python projectile_backtrace.py
#   python projectile_backtrace.py --slot p1c1
#   python projectile_backtrace.py --slot p1c1 --family 0xA5 --variant 0xA0
#   python projectile_backtrace.py --instance 0x9189ED20
#
# Notes:
#   - All addresses are EFFECTIVE (guest virtual).
#   - This script prints candidate "definition" pointers and evidence.

from __future__ import annotations

import argparse
import math
from typing import Optional, List, Tuple, Dict

try:
    from dolphin_io import hook, rd32, rdf32
except Exception:
    hook = None
    rd32 = None
    rdf32 = None


MEM2_BASE = 0x90000000
MEM2_END = 0x94000000

# Your static manager pointers
MANAGERS = {
    "p1c1": 0x803C9FCC,
    "p1c2": 0x803C9FDC,
    "p2c1": 0x803C9FD4,
    "p2c2": 0x803C9FE4,
}

# You’ve observed “move data” / actor blocks living around here.
# Keep as tunables.
DEF_REGION_MIN = 0x90800000
DEF_REGION_MAX = 0x90A80000

DEFAULT_FAMILY = 0xA5
DEFAULT_VARIANT = 0xA0


def _u32(x: int) -> int:
    return int(x) & 0xFFFFFFFF


def is_mem2_ptr(x: int) -> bool:
    x = _u32(x)
    return MEM2_BASE <= x < MEM2_END


def in_def_region(x: int) -> bool:
    x = _u32(x)
    return DEF_REGION_MIN <= x < DEF_REGION_MAX


def _safe_u32(addr: int) -> Optional[int]:
    if rd32 is None:
        return None
    try:
        v = rd32(addr)
        if v is None:
            return None
        return _u32(v)
    except Exception:
        return None


def _safe_f32(addr: int) -> Optional[float]:
    if rdf32 is None:
        return None
    try:
        v = rdf32(addr)
        if v is None:
            return None
        f = float(v)
        if not math.isfinite(f):
            return None
        return f
    except Exception:
        return None


def read_bytes_u8(addr: int, n: int) -> Optional[bytes]:
    # No raw byte reader in your dolphin_io; emulate with rd32 reads.
    # This is good enough for signature tests.
    out = bytearray()
    for off in range(0, n, 4):
        w = _safe_u32(addr + off)
        if w is None:
            return None
        out.extend(w.to_bytes(4, byteorder="big", signed=False))
    return bytes(out[:n])


def fighter_base_sanity(base: int) -> bool:
    # Matches your sanity checks
    max_hp = _safe_u32(base + 0x24)
    cur_hp = _safe_u32(base + 0x28)
    x = _safe_f32(base + 0xF0)
    y = _safe_f32(base + 0xF4)
    if max_hp is None or cur_hp is None or x is None or y is None:
        return False
    if not (10000 <= max_hp <= 60000):
        return False
    if not (0 <= cur_hp <= max_hp):
        return False
    if not (-50000.0 <= x <= 50000.0 and -50000.0 <= y <= 50000.0):
        return False
    return True


def resolve_fighter_base_from_manager(manager_ptr_addr: int) -> Optional[int]:
    """
    Given a static manager pointer address (e.g., 0x803C9FCC),
    brute-walk 1–2 pointer layers and sanity-check until we hit fighter_base.
    """
    mgr = _safe_u32(manager_ptr_addr)
    if mgr is None:
        return None

    # 1-layer candidates
    for off1 in range(0x00, 0x100, 4):
        p1 = _safe_u32(mgr + off1)
        if p1 is None:
            continue
        if is_mem2_ptr(p1) and fighter_base_sanity(p1):
            return p1

        # 2-layer candidates
        if is_mem2_ptr(p1):
            for off2 in range(0x00, 0x100, 4):
                p2 = _safe_u32(p1 + off2)
                if p2 is None:
                    continue
                if is_mem2_ptr(p2) and fighter_base_sanity(p2):
                    return p2

    return None


def scan_projectile_instances(owner_ptr: int, start: int, end: int, step: int) -> List[Tuple[int, int, int]]:
    """
    Return list of (instance_addr, life, collider_ptr) for loose matches.
    """
    OWNER_OFF = 0x70
    LIFE_OFF = 0x94
    COL_OFF = 0x68

    out: List[Tuple[int, int, int]] = []
    for addr in range(start, end, step):
        o = _safe_u32(addr + OWNER_OFF)
        if o != _u32(owner_ptr):
            continue
        life = _safe_u32(addr + LIFE_OFF) or 0
        col = _safe_u32(addr + COL_OFF) or 0
        out.append((addr, life, col))
    return out


def find_behavior_triple(buf: bytes, family: int, variant: int) -> List[int]:
    """
    Find occurrences of ?? family variant in buf.
    Returns offsets in buf.
    """
    fam = family & 0xFF
    var = variant & 0xFF
    hits: List[int] = []
    for i in range(0, len(buf) - 3):
        if buf[i + 1] == fam and buf[i + 2] == var:
            # buf[i] is mode byte (wildcard)
            hits.append(i)
    return hits


def find_hitbox_markers(buf: bytes) -> List[int]:
    """
    Look for known hitbox delimiters.
    You confirmed 35 0D 20 3F. Projectiles may reuse it, but some actors use other tags.
    We search a small set.
    """
    patterns = [
        bytes([0x35, 0x0D, 0x20, 0x3F]),
        bytes([0x33, 0x0D, 0x20, 0x3F]),
        bytes([0x37, 0x0D, 0x20, 0x3F]),
    ]
    hits: List[int] = []
    for pat in patterns:
        start = 0
        while True:
            j = buf.find(pat, start)
            if j < 0:
                break
            hits.append(j)
            start = j + 1
    hits.sort()
    return hits


def backtrace_definition_pointers(instance_addr: int, family: int, variant: int) -> None:
    """
    Scan instance memory for pointers into the definition region and score them by evidence.
    """
    print(f"[bt] instance=0x{instance_addr:08X}")

    # Read first 0x200 bytes of instance as words and treat each word as potential pointer.
    ptrs: List[Tuple[int, int]] = []  # (inst_off, ptr_value)
    for off in range(0x00, 0x200, 4):
        v = _safe_u32(instance_addr + off)
        if v is None:
            continue
        if in_def_region(v):
            ptrs.append((off, v))

    if not ptrs:
        print("[bt] no pointers into DEF region found in instance header window (0x200).")
        print("[bt] next move: widen instance scan to 0x600 or scan collider component too.")
        return

    print(f"[bt] def_ptr candidates in instance header: {len(ptrs)}")
    for inst_off, p in ptrs[:64]:
        print(f"  inst+0x{inst_off:03X} -> 0x{p:08X}")

    # For each pointer, pull a chunk and look for behavior triple + hitbox markers
    print("[bt] probing each candidate for (?? family variant) and hitbox markers...")
    for inst_off, p in ptrs:
        chunk = read_bytes_u8(p, 0x300)
        if chunk is None:
            continue

        triple_hits = find_behavior_triple(chunk, family, variant)
        marker_hits = find_hitbox_markers(chunk)

        score = 0
        reasons: List[str] = []
        if triple_hits:
            score += 50
            reasons.append(f"triple@{','.join(hex(x) for x in triple_hits[:3])}")
        if marker_hits:
            score += 25
            reasons.append(f"marker@{','.join(hex(x) for x in marker_hits[:3])}")

        # Also, many config blocks have lots of sane floats; quick probe:
        fprobe = []
        for o in (0x10, 0x14, 0x18, 0x44, 0x48, 0x4C, 0x90):
            f = _safe_f32(p + o)
            if f is not None and 0.0001 <= abs(f) <= 5000.0:
                fprobe.append((o, f))
        if len(fprobe) >= 4:
            score += 10
            reasons.append("float_dense")

        if score >= 50:
            print(f"[def] 0x{p:08X} score={score} via inst+0x{inst_off:03X} reasons={' '.join(reasons)}")
            if triple_hits:
                th = triple_hits[0]
                mode = chunk[th]  # wildcard byte
                print(f"      triple bytes: {mode:02X} {family:02X} {variant:02X} at def+0x{th:X}")
            if marker_hits:
                mh = marker_hits[0]
                # Your normal rule: radius at marker+0x44. Try reading that float.
                rad = _safe_f32(p + mh + 0x44)
                print(f"      marker at def+0x{mh:X} -> radius@+0x44 = {rad!r}")

    print("[bt] done.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=["p1c1", "p1c2", "p2c1", "p2c2"], default="p1c1")
    ap.add_argument("--owner", default=None, help="Owner fighter_base (hex). Overrides slot.")
    ap.add_argument("--instance", default=None, help="Projectile instance addr (hex). If set, skip scan.")
    ap.add_argument("--family", default=f"0x{DEFAULT_FAMILY:02X}", help="Projectile family byte (hex), default 0xA5.")
    ap.add_argument("--variant", default=f"0x{DEFAULT_VARIANT:02X}", help="Projectile variant byte (hex), default 0xA0.")
    ap.add_argument("--start", default="0x90000000")
    ap.add_argument("--end", default="0x94000000")
    ap.add_argument("--step", type=int, default=4)
    args = ap.parse_args()

    if hook is None or rd32 is None:
        print("[error] dolphin_io import failed (need hook() and rd32()).")
        return 2

    family = int(args.family, 16)
    variant = int(args.variant, 16)

    print("[info] hooking dolphin...")
    hook()
    print("[info] hooked")

    if args.owner is not None:
        owner = int(args.owner, 16)
        print(f"[info] owner override: 0x{owner:08X}")
    else:
        mgr_addr = MANAGERS[args.slot]
        owner = resolve_fighter_base_from_manager(mgr_addr) or 0
        print(f"[info] slot={args.slot} manager_ptr=0x{mgr_addr:08X} -> fighter_base=0x{owner:08X}")

    if owner == 0:
        print("[error] could not resolve fighter_base. Try --owner 0x9246B9C0 (or known base).")
        return 1

    if args.instance is not None:
        inst = int(args.instance, 16)
        backtrace_definition_pointers(inst, family, variant)
        return 0

    start = int(args.start, 16)
    end = int(args.end, 16)

    print("[scan] searching for projectile instances owned by fighter_base...")
    insts = scan_projectile_instances(owner, start, end, args.step)
    if not insts:
        print("[scan] none found. Spawn a projectile and rerun.")
        return 1

    # Prefer “life small nonzero” first
    insts.sort(key=lambda t: (0 if (t[1] != 0 and t[1] < 0x1000) else 1, -t[1]))
    print(f"[scan] found {len(insts)} instances (showing top 8):")
    for i, (a, life, col) in enumerate(insts[:8]):
        print(f"  [{i}] inst=0x{a:08X} life=0x{life:08X} collider=0x{col:08X}")

    best = insts[0][0]
    print(f"[scan] using best instance: 0x{best:08X}")
    backtrace_definition_pointers(best, family, variant)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
