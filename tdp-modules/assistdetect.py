from __future__ import annotations

import os
import sys
import time
import math
import struct
from collections import Counter
from typing import Dict, List, Tuple, Optional

try:
    from dolphin_io import hook, rbytes
except Exception as e:
    print(f"Failed to import dolphin_io: {e}")
    sys.exit(1)

try:
    from constants import MEM2_LO, MEM2_HI
except Exception:
    MEM2_LO = 0x90000000
    MEM2_HI = 0x94000000


ASSIST_TABLE_SIG = bytes([
    0x34, 0x32, 0x3F, 0x00, 0x00, 0x00, 0x00, 0x02,
    0x20, 0x00, 0x00, 0x00, 0x3E, 0xD7, 0x0A, 0x3D,
    0x00, 0x00, 0x00, 0x00, 0x34, 0x32, 0x3F, 0x00,
    0x00, 0x00, 0x00, 0x03, 0x20, 0x00, 0x00, 0x00,
])

PHASE_REC_HDR = b"\x04\x01\x02\x3F"

VALIDATION_MARKERS = [
    b"\x04\x17\x60\x00",
    b"\x04\x01\x60\x00",
    b"\x41\x20\x2D\x13",
    b"\x11\x16\x20\x00",
    b"\x33\x03\x20\x3F",
    b"\x34\x41\x00\x20",
    b"\x33\x38\x00\x20",
    b"\x34\x3D\x00\x20",
]

CHUN_PHASE_LABELS = {
    0x00000013: "FlyIn",
    0x0000010E: "Attack",
}

DEFAULT_SCAN_LEN = 0x1200000
DEFAULT_CLUSTER_GAP = 0x400
DEFAULT_BLOCK_READ = 0x1000
DEFAULT_CONTEXT = 0x200


def rd_u16_be(buf: bytes, off: int) -> Optional[int]:
    if off < 0 or off + 2 > len(buf):
        return None
    return (buf[off] << 8) | buf[off + 1]
def assign_assist_block_to_slot(candidate_addr: int, slot_bases: dict[str, int | None], max_dist: int = 0x400000):
    best_slot = None
    best_dist = None

    for slot, base in slot_bases.items():
        if not base:
            continue
        d = abs(candidate_addr - base)
        if best_dist is None or d < best_dist:
            best_dist = d
            best_slot = slot

    if best_dist is None or best_dist > max_dist:
        return None, None

    return best_slot, best_dist

def rd_u32_be(buf: bytes, off: int) -> Optional[int]:
    if off < 0 or off + 4 > len(buf):
        return None
    return (
        (buf[off] << 24)
        | (buf[off + 1] << 16)
        | (buf[off + 2] << 8)
        | buf[off + 3]
    )


def safe_rbytes(addr: int, size: int) -> bytes:
    if size <= 0:
        return b""
    if addr < MEM2_LO:
        size -= (MEM2_LO - addr)
        addr = MEM2_LO
    if addr >= MEM2_HI or size <= 0:
        return b""
    size = min(size, MEM2_HI - addr)
    try:
        return rbytes(addr, size)
    except Exception:
        return b""


def hexdump_lines(buf: bytes, start_addr: int, width: int = 16) -> List[str]:
    out: List[str] = []
    for i in range(0, len(buf), width):
        chunk = buf[i:i + width]
        hex_part = " ".join(f"{b:02X}" for b in chunk)
        out.append(f"{start_addr + i:08X}  {hex_part}")
    return out


def scan_for_sig(base: int, scan_len: int) -> List[int]:
    buf = safe_rbytes(base, scan_len)
    if not buf:
        return []
    hits: List[int] = []
    pos = 0
    while True:
        i = buf.find(ASSIST_TABLE_SIG, pos)
        if i < 0:
            break
        hits.append(base + i)
        pos = i + 1
    return hits


def validation_score(addr: int) -> Tuple[int, List[str]]:
    buf = safe_rbytes(addr, 0x600)
    if not buf:
        return 0, []

    found: List[str] = []
    score = 0

    for marker in VALIDATION_MARKERS:
        if marker in buf:
            score += 1
            found.append(marker.hex(" ").upper())

    phase_count = 0
    pos = 0
    while True:
        i = buf.find(PHASE_REC_HDR, pos)
        if i < 0:
            break
        if i + 10 <= len(buf):
            phase_count += 1
        pos = i + 1

    if phase_count:
        score += min(phase_count, 4)
        found.append(f"PHASE_REC x{phase_count}")

    repeated_3432 = buf.count(b"\x34\x32\x3F\x00")
    if repeated_3432 >= 2:
        score += min(repeated_3432, 4)
        found.append(f"34 32 3F 00 x{repeated_3432}")

    return score, found


def cluster_hits(hits: List[int], gap: int = DEFAULT_CLUSTER_GAP) -> List[List[int]]:
    if not hits:
        return []
    hits = sorted(hits)
    clusters: List[List[int]] = [[hits[0]]]
    for h in hits[1:]:
        if h - clusters[-1][-1] <= gap:
            clusters[-1].append(h)
        else:
            clusters.append([h])
    return clusters


def parse_phase_records(block_addr: int, buf: bytes) -> List[Dict[str, object]]:
    records: List[Dict[str, object]] = []
    pos = 0
    while True:
        i = buf.find(PHASE_REC_HDR, pos)
        if i < 0:
            break
        if i + 10 <= len(buf):
            phase = rd_u32_be(buf, i + 4)
            anim = rd_u16_be(buf, i + 8)
            label = CHUN_PHASE_LABELS.get(phase, "")
            records.append({
                "off": i,
                "addr": block_addr + i,
                "phase": phase,
                "anim": anim,
                "label": label,
            })
        pos = i + 1
    return records


def summarize_cluster(cluster: List[int]) -> Dict[str, object]:
    start = max(MEM2_LO, cluster[0] - DEFAULT_CONTEXT)
    end = min(MEM2_HI, cluster[-1] + DEFAULT_BLOCK_READ)
    buf = safe_rbytes(start, end - start)

    score, markers = validation_score(cluster[0])
    phase_records = parse_phase_records(start, buf)

    phase_counter = Counter()
    anim_counter = Counter()
    for rec in phase_records:
        phase = rec["phase"]
        anim = rec["anim"]
        if phase is not None:
            phase_counter[phase] += 1
        if anim is not None:
            anim_counter[anim] += 1

    return {
        "cluster_start": cluster[0],
        "cluster_end": cluster[-1],
        "cluster_hits": list(cluster),
        "score": score,
        "markers": markers,
        "phase_records": phase_records,
        "phase_counter": phase_counter,
        "anim_counter": anim_counter,
        "block_addr": start,
        "block_buf": buf,
    }


def print_cluster_summary(info: Dict[str, object], index: int) -> None:
    print("=" * 100)
    print(
        f"[cluster {index}] "
        f"range=0x{info['cluster_start']:08X}..0x{info['cluster_end']:08X} "
        f"hits={len(info['cluster_hits'])} score={info['score']}"
    )

    markers = info["markers"]
    if markers:
        print("markers:")
        for m in markers:
            print(f"  - {m}")

    phase_counter: Counter = info["phase_counter"]
    anim_counter: Counter = info["anim_counter"]

    if phase_counter:
        print("phase histogram:")
        for phase, count in phase_counter.most_common():
            label = CHUN_PHASE_LABELS.get(phase, "")
            suffix = f" ({label})" if label else ""
            print(f"  0x{phase:08X}: {count}{suffix}")

    if anim_counter:
        print("anim histogram:")
        for anim, count in anim_counter.most_common():
            print(f"  0x{anim:04X}: {count}")

    phase_records = info["phase_records"]
    if phase_records:
        print("phase records:")
        for rec in phase_records[:48]:
            phase = rec["phase"]
            anim = rec["anim"]
            label = rec["label"]
            lbl = f" {label}" if label else ""
            print(
                f"  0x{rec['addr']:08X}  "
                f"phase=0x{phase:08X}  anim=0x{anim:04X}{lbl}"
            )


def dump_cluster(info: Dict[str, object], out_dir: str, index: int) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"assist_cluster_{index:02d}_0x{info['cluster_start']:08X}.txt")
    buf: bytes = info["block_buf"]
    start_addr: int = info["block_addr"]
    phase_records = info["phase_records"]

    with open(path, "w", encoding="utf-8") as f:
        f.write(
            f"cluster {index}\n"
            f"range=0x{info['cluster_start']:08X}..0x{info['cluster_end']:08X}\n"
            f"score={info['score']}\n\n"
        )

        f.write("markers:\n")
        for m in info["markers"]:
            f.write(f"  {m}\n")
        f.write("\n")

        f.write("phase records:\n")
        for rec in phase_records:
            phase = rec["phase"]
            anim = rec["anim"]
            label = rec["label"]
            lbl = f" {label}" if label else ""
            f.write(
                f"  0x{rec['addr']:08X}  phase=0x{phase:08X}  anim=0x{anim:04X}{lbl}\n"
            )
        f.write("\n")

        f.write("hexdump:\n")
        for line in hexdump_lines(buf, start_addr):
            f.write(line + "\n")

    return path


def pick_best_clusters(hits: List[int]) -> List[Dict[str, object]]:
    clusters = cluster_hits(hits)
    infos = [summarize_cluster(c) for c in clusters]
    infos.sort(
        key=lambda x: (
            int(x["score"]),
            len(x["phase_records"]),
            len(x["cluster_hits"]),
        ),
        reverse=True,
    )
    return infos


def main() -> None:
    print("Assist standalone detector")
    print(f"MEM2 range: 0x{MEM2_LO:08X} .. 0x{MEM2_HI:08X}")
    print("Hooking Dolphin...")
    hook()
    print("Hooked.")

    print(f"Scanning 0x{DEFAULT_SCAN_LEN:X} bytes from 0x{MEM2_LO:08X} for assist-table signature...")
    hits = scan_for_sig(MEM2_LO, DEFAULT_SCAN_LEN)
    print(f"raw hits: {len(hits)}")

    if not hits:
        print("No hits found.")
        return

    infos = pick_best_clusters(hits)

    print()
    print("Top candidates:")
    for i, info in enumerate(infos[:10], start=1):
        print_cluster_summary(info, i)

    # ===== SLOT ASSIGNMENT (FIXED) =====
    slot_bases = {
        "P1": 0x9246B9C0,
        "P2": 0x92B6BA00,
        "P3": 0x927EB9E0,
        "P4": 0x92EEBA20,
    }

    print()
    print("Slot assignment:")
    for i, info in enumerate(infos[:10], start=1):
        addr = info["cluster_start"]

        slot, dist = assign_assist_block_to_slot(addr, slot_bases, max_dist=0x3000000)

        if slot:
            print(f"  cluster {i}: 0x{addr:08X} -> {slot} (dist=0x{dist:X})")
        else:
            print(f"  cluster {i}: 0x{addr:08X} -> (no slot)")

    out_dir = os.path.join(os.getcwd(), "assist_detector_dumps")
    print()
    print(f"Writing dumps to: {out_dir}")
    for i, info in enumerate(infos[:10], start=1):
        path = dump_cluster(info, out_dir, i)
        print(f"  wrote {path}")

    print()
    print("Done.")
    print("For Chun specifically, watch for:")
    print("  phase=0x00000013  -> FlyIn")
    print("  phase=0x0000010E  -> Attack")


if __name__ == "__main__":
    main()