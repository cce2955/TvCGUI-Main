#!/usr/bin/env python3
# tvc_experiments/peek_fill_abs.py
# Absolute MEM dump/fill with backup (no pointer resolving).
import argparse, time, os, sys, struct
from pathlib import Path

import dolphin_memory_engine as dme

BACKUP_DIR = "tvc_backups_abs"
os.makedirs(BACKUP_DIR, exist_ok=True)

def hook():
    while not dme.is_hooked():
        try: dme.hook()
        except Exception: pass
        time.sleep(0.1)

def be_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)

def dump_abs(start: int, length: int):
    b = dme.read_bytes(start, length)
    print(be_hex(b))

def backup_abs(start: int, length: int) -> str:
    b = dme.read_bytes(start, length)
    name = f"abs_{hex(start)}_{length}.bin"
    path = os.path.join(BACKUP_DIR, name)
    with open(path, "wb") as f: f.write(b)
    print(f"[OK] backed up {hex(start)}..{hex(start+length-1)} -> {path}")
    return path

def restore_abs(path: str):
    base = None; length = None
    # filename format: abs_0x92477400_96.bin
    fname = os.path.basename(path)
    parts = fname.split("_")
    if len(parts) >= 3 and parts[0] == "abs":
        try: base = int(parts[1], 16)
        except Exception: base = None
        try:
            tail = parts[2]
            length = int(tail.split(".")[0])
        except Exception:
            length = None
    with open(path, "rb") as f: data = f.read()
    if base is None: raise SystemExit("Could not parse base from filename; write target manually.")
    if length is None: length = len(data)
    dme.write_bytes(base, data)
    print(f"[OK] restored {hex(base)}..{hex(base+len(data)-1)}")

def fill_abs(start: int, length: int, fill_byte: int, chunk: int = 0x40):
    backup_path = backup_abs(start, length)
    remaining = length; cur = start
    try:
        while remaining > 0:
            n = min(remaining, chunk)
            dme.write_bytes(cur, bytes([fill_byte]) * n)
            remaining -= n; cur += n
            time.sleep(0.005)
        print(f"[OK] wrote {length} bytes of {hex(fill_byte)} @ {hex(start)}..{hex(start+length-1)}")
        print(f"[NOTE] backup: {backup_path}")
    except Exception as e:
        print("[ERROR] fill failed, restoring backup:", e)
        dme.write_bytes(start, open(backup_path, "rb").read())

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--action", choices=["dump", "fill", "backup", "restore"], required=True)
    ap.add_argument("--start", help="absolute start addr hex, e.g. 0x92477400")
    ap.add_argument("--len",   help="length in bytes (dec or hex like 0x60)")
    ap.add_argument("--value", help="fill byte (hex), e.g. 0x00 or 0xFF")
    ap.add_argument("--backupfile", help="path to backup file for restore")
    ap.add_argument("--chunk", default="0x40", help="chunk size for fill")
    args = ap.parse_args()

    hook()

    if args.action == "restore":
        if not args.backupfile: raise SystemExit("--backupfile required for restore")
        restore_abs(args.backupfile); return

    if not args.start or not args.len:
        raise SystemExit("--start and --len required")

    start = int(args.start, 16)
    length = int(args.len, 16) if args.len.startswith("0x") else int(args.len)

    if args.action == "dump":
        print(f"{hex(start)}..{hex(start+length-1)}")
        dump_abs(start, length); return

    if args.action == "backup":
        backup_abs(start, length); return

    if args.action == "fill":
        if not args.value: raise SystemExit("--value required for fill")
        val = int(args.value, 16)
        chunk = int(args.chunk, 16) if args.chunk.startswith("0x") else int(args.chunk)
        fill_abs(start, length, val, chunk); return

if __name__ == "__main__":
    main()
