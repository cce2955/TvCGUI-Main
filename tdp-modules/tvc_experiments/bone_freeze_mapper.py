#!/usr/bin/env python3
# tvc_experiments/bone_freeze_mapper.py
#
# Cycles through fighter_base+0xBA40..+0xBA9F (or an absolute --start),
# freezing one float (default 1.0) for N frames, restoring, and moving on.
# Use this to identify which offsets steer which limb/joint (“floating fist” cluster).
#
# Examples:
#   python tvc_experiments/bone_freeze_mapper.py --slot P1C1
#   python tvc_experiments/bone_freeze_mapper.py --slot P1C1 --frames 45 --interval 0.02
#   python tvc_experiments/bone_freeze_mapper.py --start 0x92477400 --len 0x60 --value 1.0
#
# Notes:
# - Requires dolphin_io.py in PYTHONPATH (same one you used for the other experiments).
# - Writes are big-endian. We back up 4 bytes at each offset and restore after each hold.

import argparse
import struct
import time

import dolphin_io as dio  # your wrapper (hook, rbytes, rd32/rdf32, write alias to dme.write_bytes)

# Static “manager” pointers (not the structs).
MAN = {
    "P1C1": 0x803C9FCC,
    "P1C2": 0x803C9FDC,
    "P2C1": 0x803C9FD4,
    "P2C2": 0x803C9FE4,
}

REGION_OFF = 0xBA40
REGION_LEN = 0x60

def be_u32(x: int) -> bytes:
    return struct.pack(">I", x & 0xFFFFFFFF)

def be_f32(x: float) -> bytes:
    return struct.pack(">f", float(x))

def plausible_ptr(p: int) -> bool:
    # TvC Wii RAM (Dolphin): MEM1 ~0x8000_0000.., MEM2 ~0x9000_0000..
    return 0x8000_0000 <= (p or 0) <= 0x93FF_FFFF

def read_u32(addr: int) -> int | None:
    try:
        v = dio.rd32(addr)  # your wrapper returns int or None
        return v
    except Exception:
        return None

def read_bytes(addr: int, n: int) -> bytes:
    try:
        b = dio.rbytes(addr, n)
        return b or b""
    except Exception:
        return b""

def write_bytes(addr: int, data: bytes) -> None:
    # your dolphin_io.write is an alias to dme.write_bytes
    dio.write(addr, data)

def resolve_fighter_base(slot: str) -> int:
    mgr = MAN[slot]
    p = read_u32(mgr)
    if p is None or not plausible_ptr(p):
        raise RuntimeError(f"Manager @ {hex(mgr)} bad ptr: {p}")
    p2 = read_u32(p)
    if p2 and plausible_ptr(p2):
        return p2
    return p

def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--slot", choices=list(MAN.keys()), help="slot to resolve base from")
    g.add_argument("--start", help="absolute start address (hex), overrides slot")
    ap.add_argument("--len", default=hex(REGION_LEN), help="length to scan (hex or dec); default 0x60")
    ap.add_argument("--step", default="4", help="byte step; default 4")
    ap.add_argument("--value", default="1.0", help="float to freeze (e.g., 1.0) OR hex u32 like 0x3F800000")
    ap.add_argument("--frames", type=int, default=60, help="frames to hold each offset (write every frame); default 60")
    ap.add_argument("--interval", type=float, default=1/60, help="seconds between rewrites; default ~0.0167")
    ap.add_argument("--preview", action="store_true", help="print hexdump of target region before scanning")
    args = ap.parse_args()

    dio.hook()  # ensure we’re connected

    # Resolve start address
    if args.start:
        start = int(args.start, 16)
        base = None
        title = f"ABS {hex(start)}"
    else:
        base = resolve_fighter_base(args.slot)
        start = base + REGION_OFF
        title = f"{args.slot} base={hex(base)} start={hex(start)}"

    total_len = int(args.len, 16) if isinstance(args.len, str) and args.len.lower().startswith("0x") else int(args.len)
    step = int(args.step, 16) if isinstance(args.step, str) and args.step.lower().startswith("0x") else int(args.step)
    if total_len <= 0 or step <= 0:
        raise ValueError("len and step must be positive")

    # Parse value
    freeze_bytes: bytes
    if isinstance(args.value, str) and args.value.lower().startswith("0x"):
        freeze_bytes = be_u32(int(args.value, 16))
        mode = "u32"
    else:
        freeze_bytes = be_f32(float(args.value))
        mode = "f32"

    end = start + total_len
    print(f"[MAP] target {title} len={hex(total_len)} step={step} mode={mode} frames={args.frames} interval={args.interval}")

    # Optional preview
    if args.preview:
        raw = read_bytes(start, total_len)
        print("[PREVIEW]", " ".join(f"{b:02X}" for b in raw))

    # Walk offsets
    off = 0
    while start + off < end:
        addr = start + off

        # Backup 4 bytes at this offset
        backup = read_bytes(addr, 4)
        if len(backup) != 4:
            print(f"[SKIP] +0x{off:02X} @ {hex(addr)} (couldn't read 4 bytes)")
            off += step
            continue

        # Freeze loop
        print(f"[HOLD] +0x{off:02X} @ {hex(addr)} write {freeze_bytes.hex().upper()} ({mode})")
        try:
            for _ in range(args.frames):
                write_bytes(addr, freeze_bytes)
                time.sleep(args.interval)
        finally:
            # Restore original 4 bytes
            write_bytes(addr, backup)
            # small pause between probes
            time.sleep(args.interval * 2)

        off += step

    print("[DONE] sweep complete.")

if __name__ == "__main__":
    main()
