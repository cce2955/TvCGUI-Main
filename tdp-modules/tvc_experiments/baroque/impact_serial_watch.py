#!/usr/bin/env python3
# Watches Baroque readiness + nearby serials with safe formatting and edge logging.
# Defaults match your current session addresses.

import argparse
import time
import os
import sys
import math
import struct

# Use dolphin_memory_engine directly to avoid local wrapper drift.
import dolphin_memory_engine as dme

# --- helpers ---------------------------------------------------------------

MEM1_LO, MEM1_HI = 0x80000000, 0x81800000
MEM2_LO, MEM2_HI = 0x90000000, 0x94000000

def hook():
    while not dme.is_hooked():
        try:
            dme.hook()
        except Exception:
            pass
        time.sleep(0.2)

def in_ram(a):
    return (MEM1_LO <= a < MEM1_HI) or (MEM2_LO <= a < MEM2_HI)

def rbytes(addr, n):
    if not in_ram(addr) or n <= 0:
        return b""
    try:
        b = dme.read_bytes(addr, n)
        return b if b else b""
    except Exception:
        return b""

def rd8(addr):
    b = rbytes(addr, 1)
    return b[0] if len(b) == 1 else None

def rdu16_be(addr):
    b = rbytes(addr, 2)
    if len(b) != 2:
        return None
    return (b[0] << 8) | b[1]

def rdu32_be(addr):
    b = rbytes(addr, 4)
    if len(b) != 4:
        return None
    return struct.unpack(">I", b)[0]

def rdf32_be(addr):
    b = rbytes(addr, 4)
    if len(b) != 4:
        return None
    try:
        u = struct.unpack(">I", b)[0]
        f = struct.unpack(">f", struct.pack(">I", u))[0]
        if not math.isfinite(f) or abs(f) > 1e9:
            return None
        return f
    except Exception:
        return None

def hexdump_row(addr, length=0x20):
    data = rbytes(addr, length)
    if not data:
        return f"{addr:08X}: <unreadable>"
    out = []
    for i in range(0, len(data), 0x10):
        chunk = data[i:i+0x10]
        hexs = " ".join(f"{b:02X}" for b in chunk)
        out.append(f"{addr+i:08X}: {hexs}")
    return "\n".join(out)

def now_hms():
    return time.strftime("%H:%M:%S")

def maybe_clear(enable=True):
    if not enable:
        return
    os.system("cls" if os.name == "nt" else "clear")

# --- main ------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Watch Baroque gate + nearby serials (HP, combo, timer, scale).")
    ap.add_argument("--base", type=lambda x:int(x,0), required=True, help="fighter base (e.g. 0x9246B9C0)")
    # Absolute addresses with your discovered defaults:
    ap.add_argument("--flag_addr", type=lambda x:int(x,0), default=0x9246CBAB, help="Baroque gate byte (!=0 is ready)")
    ap.add_argument("--timer_u16", type=lambda x:int(x,0), default=0x9246CB97, help="u16 timer @ row 0x9246CB90 idx 7..8")
    ap.add_argument("--scale_f32", type=lambda x:int(x,0), default=0x9246CB99, help="scaling float @ row 0x9246CB90 idx 9..11")
    # Relative offsets (from fighter base):
    ap.add_argument("--hp_off",   type=lambda x:int(x,0), default=0x28,   help="current HP offset (word)")
    ap.add_argument("--cc_off",   type=lambda x:int(x,0), default=0xB9F0, help="internal combo counter offset (word)")
    # Edge snapshot row (your Baroque row area):
    ap.add_argument("--row_addr", type=lambda x:int(x,0), default=0x9246CBA0, help="row to snapshot (0x20 bytes) on edge")
    ap.add_argument("--interval", type=float, default=1/30, help="poll seconds (default ~30Hz)")
    ap.add_argument("--clear", action="store_true", help="clear screen and redraw header each tick")
    ap.add_argument("--log", type=str, default="", help="optional CSV log file for edges")
    args = ap.parse_args()

    hook()

    # Prepare log
    csv_f = None
    if args.log:
        csv_f = open(args.log, "a", encoding="utf-8")
        if csv_f.tell() == 0:
            csv_f.write("time,flag_u8,timer_u16,scale_f32,hp,combo,row_addr,row_hex\n")

    prev_flag = None
    prev_line = None

    header = "time      flag  timer   scale      hp     combo"
    fmt    = "{t}  {flg:3d}  {tim:5d}  {scl:8}  {hp:6}  {cc:6}"

    while True:
        # Reads (safe formatting for None)
        flag = rd8(args.flag_addr)
        tim  = rdu16_be(args.timer_u16)
        scl  = rdf32_be(args.scale_f32)
        hp   = rdu32_be(args.base + args.hp_off)
        cc   = rdu32_be(args.base + args.cc_off)

        scl_str = f"{scl:.6f}" if scl is not None else "NaN"
        hp_str  = f"{hp}"       if hp  is not None else "NaN"
        cc_str  = f"{cc}"       if cc  is not None else "NaN"
        flg_str = str(flag)     if flag is not None else "NaN"
        tim_str = str(tim)      if tim  is not None else "NaN"

        # Optional clear
        if args.clear:
            maybe_clear(True)
            print(header)

        line = fmt.format(
            t=now_hms(),
            flg=flag if flag is not None else 0,
            tim=tim  if tim  is not None else 0,
            scl=scl_str,
            hp=hp_str,
            cc=cc_str
        )

        # Print only if changed from previous line (keeps it readable if not clearing)
        if args.clear or line != prev_line:
            print(line)
            prev_line = line

        # Edge detect on the Baroque flag byte
        if flag is not None and prev_flag is not None and flag != prev_flag:
            # Snapshot the nearby 0x20 row for context
            row_dump = hexdump_row(args.row_addr, 0x20)
            # Print a minimal edge banner
            print(f"\n[EDGE] {now_hms()}  {args.flag_addr:#010x}  {prev_flag} -> {flag}\n{row_dump}\n")

            # Log to CSV if requested
            if csv_f is not None:
                # Flatten the hexdump into a single space-separated line
                flat_row = " | ".join(row_dump.splitlines())
                csv_f.write(f"{now_hms()},{flag},{tim if tim is not None else ''},{scl if scl is not None else ''},{hp if hp is not None else ''},{cc if cc is not None else ''},{hex(args.row_addr)},{flat_row}\n")
                csv_f.flush()

        prev_flag = flag
        time.sleep(args.interval)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
