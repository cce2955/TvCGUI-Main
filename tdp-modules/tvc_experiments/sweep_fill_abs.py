#!/usr/bin/env python3
# tvc_experiments/sweep_fill_abs.py
#
# Absolute, backup-first sweep writer for Dolphin RAM.
# - No pointer resolving. You pass --start and --len.
# - Modes: byte, u32, float (big-endian for PPC).
# - Writes one or more test values per offset, with a hold delay.
# - Can restore after each write or once at the end.

import argparse, os, time, struct
from pathlib import Path
import dolphin_memory_engine as dme

BACKUP_DIR = "tvc_backups_abs"
os.makedirs(BACKUP_DIR, exist_ok=True)

def hook():
    while not dme.is_hooked():
        try: dme.hook()
        except Exception: pass
        time.sleep(0.1)

def be_hex(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)

def backup_abs(start: int, length: int) -> str:
    data = dme.read_bytes(start, length)
    path = os.path.join(BACKUP_DIR, f"abs_{hex(start)}_{length}.bin")
    with open(path, "wb") as f: f.write(data)
    print(f"[OK] backup -> {path} ({hex(start)}..{hex(start+length-1)})")
    return path

def restore_abs_from_data(start: int, data: bytes):
    dme.write_bytes(start, data)
    print(f"[OK] restored region {hex(start)}..{hex(start+len(data)-1)}")

def parse_len(s: str) -> int:
    return int(s, 16) if s.startswith("0x") else int(s)

def parse_values(mode: str, s: str):
    # Accept comma-separated list
    parts = [p.strip() for p in s.split(",") if p.strip()]

    if mode == "byte":
        vals = []
        for p in parts:
            v = int(p, 16) if p.startswith("0x") else int(p)
            if not (0 <= v <= 0xFF):
                raise ValueError("byte values must be 0..255")
            vals.append(bytes([v]))
        return vals, 1

    if mode == "u32":
        vals = []
        for p in parts:
            v = int(p, 16) if p.startswith("0x") else int(p)
            if not (0 <= v <= 0xFFFFFFFF):
                raise ValueError("u32 values must be 0..0xFFFFFFFF")
            vals.append(struct.pack(">I", v))   # big-endian
        return vals, 4

    if mode == "float":
        vals = []
        for p in parts:
            v = float(p)
            vals.append(struct.pack(">f", v))   # big-endian
        return vals, 4

    raise ValueError("unknown mode")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True, help="absolute start addr (hex), e.g. 0x92477400")
    ap.add_argument("--len",   required=True, help="length in bytes (dec or hex), e.g. 0x60")
    ap.add_argument("--mode",  required=True, choices=["byte","u32","float"])
    ap.add_argument("--values", required=True,
                    help="comma list; byte: 0x00,0xFF ; u32: 0,0x3F800000 ; float: 0.0,1.0")
    ap.add_argument("--stride", help="override stride (default 1 for byte, 4 for u32/float)")
    ap.add_argument("--hold", default="0.30", help="seconds to wait after each write (default 0.30)")
    ap.add_argument("--revert-between", action="store_true",
                    help="restore original bytes after each write")
    ap.add_argument("--no-final-restore", action="store_true",
                    help="skip final region restore (keeps last write)")
    ap.add_argument("--begin", help="offset begin (hex or dec) relative to start, default 0")
    ap.add_argument("--end", help="offset end (hex or dec) inclusive, default len-1")
    args = ap.parse_args()

    hook()

    start = int(args.start, 16)
    total_len = parse_len(args.len)
    hold_s = float(args.hold)

    vals, default_stride = parse_values(args.mode, args.values)
    stride = int(args.stride, 16) if (args.stride and args.stride.startswith("0x")) else (int(args.stride) if args.stride else default_stride)

    begin = parse_len(args.begin) if args.begin else 0
    end   = parse_len(args.end)   if args.end   else (total_len - 1)

    if begin < 0 or end >= total_len or begin > end:
        raise SystemExit("Invalid --begin/--end window for the provided --len")

    # Backup entire region once
    original = dme.read_bytes(start, total_len)
    backup_path = os.path.join(BACKUP_DIR, f"abs_{hex(start)}_{total_len}.bin")
    with open(backup_path, "wb") as f: f.write(original)
    print(f"[INFO] region: {hex(start)}..{hex(start+total_len-1)}  (window {hex(begin)}..{hex(end)})")
    print(f"[OK] backup -> {backup_path}")

    # Sweep across the selected window
    off = begin
    try:
        while off <= end:
            # Read & keep local original chunk for this stride
            here = start + off
            orig_chunk = dme.read_bytes(here, stride)

            for v in vals:
                print(f"[SWEEP] +{hex(off)} @ {hex(here)}  write {be_hex(v)}  (mode={args.mode})")
                dme.write_bytes(here, v)
                time.sleep(hold_s)

                if args.revert_between:
                    dme.write_bytes(here, orig_chunk)
                    # small breath to observe the revert if needed
                    time.sleep(0.05)

            off += stride

    finally:
        if args.no_final_restore:
            print("[WARN] leaving changes in place (no final restore). Use your savestate/backup if needed.")
        else:
            dme.write_bytes(start, original)
            print(f"[OK] final restore -> {hex(start)}..{hex(start+total_len-1)}")

if __name__ == "__main__":
    main()
