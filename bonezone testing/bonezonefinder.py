"""
TvC Bone Finder  v3  ,  Full MEM2 scanner
==========================================
Scans all of MEM2 (0x90000000–0x93FFFFFF) in chunks looking for
regions that match the confirmed 0x40-stride 3x4 float matrix layout.

Signature fingerprint (must match ALL of these to count as a bone):
  - At least 8 of the 12 float fields (+0x00–+0x2B) are valid IEEE floats
  - At least one field equals 1.0 (3F800000) , rotation identity hint
  - +0x30 onward contains at least one non-float (metadata separator)
  - Record is 0x40-aligned

Output:
  - Lists all matching bone record clusters
  - Groups records within 0x200 bytes of each other (likely same skeleton)
  - Saves full results to mem2_bones.txt

Requires: dolphin_memory_engine
"""

import struct
import sys
import os
from collections import defaultdict

try:
    import dolphin_memory_engine as dme
except ImportError:
    sys.exit("[ERROR] dolphin_memory_engine not installed.")

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

MEM2_START   = 0x90000000
MEM2_END     = 0x93FFFFFF

CHUNK_SIZE   = 0x10000      # 64KB per read , safe for DME
BONE_STRIDE  = 0x40
FLOAT_ONE    = 0x3F800000
FLOAT_ZERO   = 0x00000000

# How close two records need to be to count as the same cluster
CLUSTER_GAP  = 0x200

# Minimum valid floats in the matrix zone (+0x00–+0x2B) to count as a bone
MIN_VALID_FLOATS = 8

# Output file
OUT_FILE = "mem2_bones.txt"

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def u32_be(data, off):
    if off + 4 > len(data):
        return None
    return struct.unpack_from('>I', data, off)[0]

def f32_be(data, off):
    raw = u32_be(data, off)
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

def is_bone_record(data, off):
    """
    Returns True if the 0x40 bytes at `off` look like a 3x4 bone matrix.
    """
    if off + BONE_STRIDE > len(data):
        return False

    # Check matrix zone: +0x00 to +0x2B (12 floats)
    raws = [u32_be(data, off + i*4) for i in range(12)]
    valid_count = sum(1 for r in raws if is_valid_float(r))
    if valid_count < MIN_VALID_FLOATS:
        return False

    # Must contain at least one 1.0
    if FLOAT_ONE not in raws:
        return False

    # Metadata zone +0x30–+0x3C should have at least one non-float
    meta_raws = [u32_be(data, off + 0x30 + i*4) for i in range(4)]
    non_float_count = sum(1 for r in meta_raws if r is not None and not is_valid_float(r))
    if non_float_count < 1:
        return False

    return True

# ─────────────────────────────────────────────
# CLUSTER GROUPING
# ─────────────────────────────────────────────

def cluster_records(records):
    """Group records that are within CLUSTER_GAP of each other."""
    if not records:
        return []
    records = sorted(records)
    clusters = []
    current = [records[0]]
    for addr in records[1:]:
        if addr - current[-1] <= CLUSTER_GAP:
            current.append(addr)
        else:
            clusters.append(current)
            current = [addr]
    clusters.append(current)
    return clusters

# ─────────────────────────────────────────────
# MAIN SCAN
# ─────────────────────────────────────────────

def scan():
    try:
        dme.hook()
        print("[OK] Hooked to Dolphin.")
    except Exception as e:
        sys.exit(f"[ERROR] Could not hook: {e}")

    total_bytes = MEM2_END - MEM2_START
    total_chunks = (total_bytes + CHUNK_SIZE - 1) // CHUNK_SIZE

    print(f"\nScanning MEM2: 0x{MEM2_START:08X}–0x{MEM2_END:08X}")
    print(f"Chunk size: 0x{CHUNK_SIZE:X}  Total chunks: {total_chunks}")
    print(f"Looking for 0x40-stride 3x4 float matrix records...\n")

    all_hits = []
    errors   = 0

    for chunk_idx in range(total_chunks):
        chunk_start = MEM2_START + chunk_idx * CHUNK_SIZE
        chunk_end   = min(chunk_start + CHUNK_SIZE, MEM2_END)
        read_size   = chunk_end - chunk_start

        # Progress indicator
        pct = (chunk_idx / total_chunks) * 100
        sys.stdout.write(f"\r  [{pct:5.1f}%]  0x{chunk_start:08X}  hits={len(all_hits)}  errors={errors}   ")
        sys.stdout.flush()

        try:
            data = dme.read_bytes(chunk_start, read_size)
        except Exception:
            errors += 1
            continue

        # Walk chunk at 0x40 alignment
        # Find the first 0x40-aligned offset within this chunk
        align_offset = (0x40 - (chunk_start % 0x40)) % 0x40

        off = align_offset
        while off + BONE_STRIDE <= len(data):
            abs_addr = chunk_start + off
            if is_bone_record(data, off):
                all_hits.append(abs_addr)
            off += BONE_STRIDE

    print(f"\n\n[SCAN COMPLETE]  {len(all_hits)} bone records found  ({errors} unreadable chunks)")

    # Cluster the results
    clusters = cluster_records(all_hits)
    print(f"[CLUSTERS]  {len(clusters)} skeleton-like groups\n")

    # ── Print and save results ──
    lines = []
    lines.append(f"TvC MEM2 Bone Scan Results")
    lines.append(f"Scan range: 0x{MEM2_START:08X}–0x{MEM2_END:08X}")
    lines.append(f"Total bone records: {len(all_hits)}")
    lines.append(f"Clusters: {len(clusters)}")
    lines.append("=" * 60)

    for ci, cluster in enumerate(clusters):
        span_start = cluster[0]
        span_end   = cluster[-1]
        span_size  = span_end - span_start + BONE_STRIDE
        header = (f"\nCluster [{ci:03d}]  "
                  f"0x{span_start:08X}–0x{span_end:08X}  "
                  f"({len(cluster)} records, 0x{span_size:X} bytes)")
        lines.append(header)

        for addr in cluster:
            lines.append(f"  0x{addr:08X}")

    # Write file
    with open(OUT_FILE, 'w') as f:
        f.write('\n'.join(lines))
    print('\n'.join(lines[:60]))   # preview first 60 lines to console
    if len(lines) > 60:
        print(f"  ... ({len(lines)-60} more lines , see {OUT_FILE})")

    print(f"\n[SAVED] Full results → {os.path.abspath(OUT_FILE)}")
    input("\nPress Enter to exit.")

if __name__ == "__main__":
    scan()