"""
TvC Bone Boundary Finder  v2
=============================
Pattern scan + reactive address cross-reference.
Stride 0x40 now confirmed as best candidate from first run.
Bug fixed: bounds check before reading reactive addresses.

Requires: dolphin_memory_engine
"""

import struct
import sys
from collections import defaultdict

try:
    import dolphin_memory_engine as dme
except ImportError:
    sys.exit("[ERROR] dolphin_memory_engine not installed.")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

SCAN_START = 0x924773C0
SCAN_END   = 0x92477600   # extended slightly to capture 924774FE safely

REACTIVE = [
    0x9247742E,
    0x92477454,
    0x924774BC,
    0x924774C0,
    0x924774D0,
    0x924774FE,
]

STRIDE_CANDIDATES = [0x10, 0x14, 0x18, 0x1C, 0x20, 0x24, 0x28, 0x30, 0x40, 0x48, 0x60, 0x80]

FLOAT_ONE  = 0x3F800000
FLOAT_ZERO = 0x00000000

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def read_region():
    size = SCAN_END - SCAN_START
    data = dme.read_bytes(SCAN_START, size)
    print(f"[OK] Read 0x{size:X} bytes from 0x{SCAN_START:08X}–0x{SCAN_END:08X}")
    return bytearray(data)

def u32_at(data, offset):
    if offset < 0 or offset + 4 > len(data):
        return None
    return struct.unpack_from('>I', data, offset)[0]

def f32_at(data, offset):
    raw = u32_at(data, offset)
    if raw is None:
        return None
    return struct.unpack('>f', struct.pack('>I', raw))[0]

def is_valid_float(raw):
    if raw is None:
        return False
    exp = (raw >> 23) & 0xFF
    if exp == 0xFF:
        return False
    if exp == 0x00 and (raw & 0x7FFFFF) != 0:
        return False
    val = struct.unpack('>f', struct.pack('>I', raw))[0]
    return abs(val) < 1e6

def fmt_raw(raw, fval):
    """Safe formatter — handles None."""
    if raw is None:
        return "raw=N/A (out of range)"
    return f"raw=0x{raw:08X}  float={fval:10.4f}" if fval is not None else f"raw=0x{raw:08X}  float=N/A"

# ─────────────────────────────────────────────
# 1. PATTERN SCAN
# ─────────────────────────────────────────────

def pattern_scan(data):
    print("\n" + "="*60)
    print("  PATTERN SCAN — float cluster + identity quat signatures")
    print("="*60)

    size = len(data)
    hits = []

    for off in range(0, size - 32, 4):
        score = 0
        notes = []
        floats = [u32_at(data, off + i*4) for i in range(8)]
        valid_count = sum(1 for f in floats if is_valid_float(f))
        score += valid_count

        ones = floats.count(FLOAT_ONE)
        if ones == 1:
            score += 3
            idx = floats.index(FLOAT_ONE)
            notes.append(f"1.0@+{idx*4:#04x}")
        elif ones > 1:
            score += 1
            notes.append(f"{ones}x 1.0")

        if floats[:3] == [0, 0, 0] and floats[3] == FLOAT_ONE:
            score += 5
            notes.append("IDENTITY QUAT")

        if (SCAN_START + off) % 0x40 == 0:
            score += 2
            notes.append("0x40-aligned")
        elif (SCAN_START + off) % 0x20 == 0:
            score += 1

        if score >= 8:
            hits.append((off, score, notes))

    hits.sort(key=lambda x: -x[1])

    print(f"\n  Top candidates:\n")
    print(f"  {'Address':<14} {'Score':>5}  Notes")
    print(f"  {'-'*55}")
    for off, score, notes in hits[:30]:
        addr = SCAN_START + off
        print(f"  0x{addr:08X}    {score:>4}   {', '.join(notes)}")

    if len(hits) >= 2:
        print("\n  Stride gaps between top hits:")
        top_addrs = sorted(set(SCAN_START + h[0] for h in hits[:20]))
        gaps = [top_addrs[i+1] - top_addrs[i] for i in range(len(top_addrs)-1)]
        gap_counts = defaultdict(int)
        for g in gaps:
            gap_counts[g] += 1
        for gap, count in sorted(gap_counts.items(), key=lambda x: -x[1])[:8]:
            print(f"    0x{gap:04X} ({gap:3d} bytes)  x{count}")

    return hits

# ─────────────────────────────────────────────
# 2. REACTIVE XREF
# ─────────────────────────────────────────────

def reactive_xref(data):
    print("\n" + "="*60)
    print("  REACTIVE XREF — stride alignment of hot addresses")
    print("="*60)
    print(f"\n  Reactive: {[hex(r) for r in REACTIVE]}\n")

    results = []

    for stride in STRIDE_CANDIDATES:
        record_offsets = []
        base_addrs     = []
        valid = True

        for raddr in REACTIVE:
            rel = raddr - SCAN_START
            if rel < 0 or rel >= len(data):
                valid = False
                break
            rec_idx      = rel // stride
            rec_base_abs = SCAN_START + rec_idx * stride
            within       = raddr - rec_base_abs
            record_offsets.append(within)
            base_addrs.append(rec_base_abs)

        if not valid:
            continue

        unique_offsets = set(record_offsets)
        score = stride - len(unique_offsets)
        results.append((stride, record_offsets, base_addrs, unique_offsets, score))

    results.sort(key=lambda x: -x[4])

    print(f"  {'Stride':<10} {'Within-record offsets':<45} unique")
    print(f"  {'-'*65}")
    for stride, rec_offs, base_addrs, unique_offs, score in results:
        offs_str   = '  '.join(f"+0x{o:02X}" for o in rec_offs)
        unique_str = '{' + ', '.join(f"0x{o:02X}" for o in sorted(unique_offs)) + '}'
        print(f"  0x{stride:02X} ({stride:2d}b)  [{offs_str}]")
        print(f"              unique: {unique_str}  score={score}")
        print(f"              bases:  {[hex(b) for b in base_addrs]}")
        print()

    best_stride, _, _, _, _ = results[0]
    print(f"\n  BEST STRIDE: 0x{best_stride:02X} ({best_stride} bytes)")

    # Detailed reactive dump for best stride
    print(f"\n  Reactive field values (stride=0x{best_stride:02X}):")
    print(f"  {'Address':<14} {'rec_base':<14} {'offset':<8} {'raw':>12}  {'float':>10}  notes")
    print(f"  {'-'*72}")
    for raddr in REACTIVE:
        rel = raddr - SCAN_START
        if rel < 0 or rel >= len(data):
            print(f"  0x{raddr:08X}  OUT OF RANGE")
            continue
        rec_idx      = rel // best_stride
        rec_base_abs = SCAN_START + rec_idx * best_stride
        within       = raddr - rec_base_abs
        data_off     = rec_base_abs - SCAN_START + within
        raw          = u32_at(data, data_off)
        fval         = f32_at(data, data_off)

        # annotate
        note = ""
        if raw == FLOAT_ONE:           note = "<-- 1.0 (identity)"
        elif raw == FLOAT_ZERO:        note = "<-- 0.0"
        elif raw == 0x80000000:        note = "<-- -0.0 (sign flag?)"
        elif raw is not None and (raw >> 16) == 0x0015: note = "<-- 0x0015 index field"
        elif fval is not None and abs(fval) <= 1.0 and is_valid_float(raw):
            note = "<-- plausible rotation component"

        raw_str  = f"0x{raw:08X}" if raw  is not None else "N/A"
        fval_str = f"{fval:10.4f}" if fval is not None else "       N/A"
        print(f"  0x{raddr:08X}  0x{rec_base_abs:08X}  +0x{within:02X}   {raw_str}  {fval_str}  {note}")

    return results[0]

# ─────────────────────────────────────────────
# 3. FULL RECORD MAP for confirmed stride
# ─────────────────────────────────────────────

def print_record_map(data, stride):
    print("\n" + "="*60)
    print(f"  RECORD MAP  stride=0x{stride:02X} ({stride} bytes)")
    print("="*60)

    words_per_rec = stride // 4
    addr  = SCAN_START
    rec   = 0

    while addr + stride <= SCAN_END:
        off = addr - SCAN_START
        print(f"\n  ── rec[{rec:02d}]  base=0x{addr:08X} ──")
        for i in range(words_per_rec):
            field_off = i * 4
            raw  = u32_at(data, off + field_off)
            fval = f32_at(data, off + field_off)

            if raw is None:
                tag = "OUT_OF_RANGE"
            elif raw == FLOAT_ONE:
                tag = f"[1.0]"
            elif raw == FLOAT_ZERO:
                tag = f"[0.0]"
            elif raw == 0x80000000:
                tag = f"[-0.0 / sign]"
            elif is_valid_float(raw):
                tag = f"[{fval:.4f}]"
            else:
                tag = f"[non-float: 0x{raw:08X}]"

            # flag if this address is reactive
            abs_addr = addr + field_off
            reactive_flag = " <-- REACTIVE" if abs_addr in REACTIVE else ""

            raw_str = f"0x{raw:08X}" if raw is not None else "N/A"
            print(f"    +0x{field_off:02X}  {raw_str:<12}  {tag:<22}{reactive_flag}")

        rec  += 1
        addr += stride

# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

def run():
    try:
        dme.hook()
        print("[OK] Hooked to Dolphin.")
    except Exception as e:
        sys.exit(f"[ERROR] Could not hook: {e}")

    data = read_region()

    pattern_scan(data)
    best = reactive_xref(data)
    print_record_map(data, best[0])

    print("\n" + "="*60)
    print("  INTERPRETATION GUIDE")
    print("="*60)
    print("  Look for this pattern at the start of each record:")
    print("    +0x00  0.0  (qX)")
    print("    +0x04  0.0  (qY)")
    print("    +0x08  0.0  (qZ)")
    print("    +0x0C  1.0  (qW  <-- identity)")
    print("  Translation fields typically follow at +0x10/14/18")
    print("  Non-float values (like 0x0015XXXX) are index/flag fields")
    print("  Reactive addresses that are floats = transform fields")
    print("  Reactive addresses that are non-float = hierarchy/index fields")

    input("\nPress Enter to exit.")

if __name__ == "__main__":
    run()