#!/usr/bin/env python3
# baroque_watch.py
# Poll a specific byte (by "Nth byte in row" semantics) and dump a small panel
# continuously + on any edge. Works with your experiment dolphin_io or dme.

import time, struct, math, sys

# Try your experiment dolphin_io first, then fall back to dolphin_memory_engine
try:
    import dolphin_io as dio
    HAVE_DIO = True
except Exception:
    HAVE_DIO = False
    import dolphin_memory_engine as dme

MANAGERS = {
    "P1C1": 0x803C9FCC,
    "P1C2": 0x803C9FDC,
    "P2C1": 0x803C9FD4,
    "P2C2": 0x803C9FE4,
}

def hook():
    if HAVE_DIO and hasattr(dio, "hook"):
        dio.hook()
    else:
        while True:
            try:
                if dme.is_hooked():
                    return
                dme.hook()
            except Exception:
                pass
            time.sleep(0.2)

def rbytes(addr, n):
    if HAVE_DIO:
        if hasattr(dio, "read"):   return dio.read(addr, n)
        if hasattr(dio, "rbytes"): return dio.rbytes(addr, n)
        return b""
    else:
        try:
            return dme.read_bytes(addr, n)
        except Exception:
            return b""

def ru8(addr):
    b = rbytes(addr, 1)
    return b[0] if len(b)==1 else None

def ru16(addr):
    b = rbytes(addr, 2)
    return struct.unpack(">H", b)[0] if len(b)==2 else None

def ru32(addr):
    b = rbytes(addr, 4)
    return struct.unpack(">I", b)[0] if len(b)==4 else None

def rf32(addr):
    b = rbytes(addr, 4)
    if len(b)!=4: return None
    u = struct.unpack(">I", b)[0]
    f = struct.unpack(">f", struct.pack(">I", u))[0]
    if not math.isfinite(f) or abs(f)>1e8: return None
    return f

def plausible_ptr(x:int)->bool:
    return 0x90000000 <= x <= 0x93FFFFFF or 0x92000000 <= x <= 0x92FFFFFF

def resolve_fighter_base(slot:str)->int:
    mgr = MANAGERS[slot]
    p1 = ru32(mgr)
    if p1 and (0x80000000 <= p1 <= 0x8FFFFFFF):
        p2 = ru32(p1)
        if p2 and plausible_ptr(p2): return p2
    if p1 and plausible_ptr(p1):
        return p1
    raise RuntimeError(f"Could not resolve fighter_base for {slot}: p1={hex(p1) if p1 else p1}")

def hexbytes(bs:bytes)->str:
    return " ".join(f"{x:02X}" for x in bs)

def dump_row(addr:int, length:int)->str:
    bs = rbytes(addr, length)
    if not bs: return "(unreadable)"
    out=[]
    for i in range(0,len(bs),16):
        chunk=bs[i:i+16]
        out.append(f"{addr+i:08X}: {hexbytes(chunk)}")
    return "\n".join(out)

def main():
    import argparse
    p = argparse.ArgumentParser()
    # Mode: base-relative row or absolute row
    p.add_argument("--mode", choices=["base", "abs"], default="base")
    p.add_argument("--slot", choices=list(MANAGERS.keys()), default="P1C1",
                   help="slot (when --mode base)")
    p.add_argument("--row-off", default="0xCBA0",
                   help="row offset from fighter_base (hex), default 0xCBA0")
    p.add_argument("--row-abs", default="", help="absolute row address (hex) when --mode abs")
    p.add_argument("--byte-index", type=int, default=12,
                   help="1-based index into the row (e.g. 12 => 12th byte).")
    p.add_argument("--width", type=int, default=1, choices=[1,2],
                   help="number of bytes to read at the target (1 or 2).")
    p.add_argument("--poll", type=float, default=0.030, help="seconds between polls")
    p.add_argument("--echo-every", type=float, default=0.25,
                   help="periodic echo interval seconds (value + small panel).")
    p.add_argument("--row-len", type=int, default=0x20, help="row length for dump")
    # extra absolute counters at 0x9246CB80: byte 16 and 32 (1-based)
    p.add_argument("--hit-abs", default="0x9246CB80", help="absolute row for counters (hex)")
    p.add_argument("--hit-idx1", type=int, default=16, help="1-based index for current hit counter")
    p.add_argument("--hit-idx2", type=int, default=32, help="1-based index for airborne hits counter")
    args = p.parse_args()

    hook()

    # Resolve base/row
    if args.mode == "base":
        base = resolve_fighter_base(args.slot)
        row_off = int(args.row_off, 16)
        row_addr = base + row_off
        where = f"{args.slot} base={hex(base)} row={hex(row_addr)} (off {hex(row_off)})"
    else:
        row_addr = int(args.row_abs, 16)
        base = None
        where = f"ABS row={hex(row_addr)}"

    # Target byte address by “Nth in row” semantics (1-based)
    if args.byte_index <= 0 or args.byte_index > args.row_len:
        print(f"[FATAL] byte-index {args.byte_index} out of 1..{args.row_len}")
        sys.exit(1)
    target_addr = row_addr + (args.byte_index - 1)

    print(f"[WATCH] {where}  target={hex(target_addr)} width={args.width}  poll={args.poll}s echo={args.echo_every}s")
    print(f"[NOTE] Counting bytes 1..{args.row_len} left-to-right in hex dump of row {hex(row_addr)}")

    # Always show an initial row snapshot and initial panel
    print("\n[ROW INIT]")
    print(dump_row(row_addr, args.row_len))

    def read_target():
        if args.width == 1:  return ru8(target_addr)
        else:                return ru16(target_addr)

    # “panel” values: red life / hp / combo / assist / counters
    def panel():
        vals = {}
        if base:
            vals["red_life(+0x2C)"]   = ru32(base+0x2C)
            vals["cur_hp(+0x28)"]     = ru32(base+0x28)
            vals["max_hp(+0x24)"]     = ru32(base+0x24)
            vals["combo(+0xB9F0)"]    = ru32(base+0xB9F0)
            vals["assist_ok(+0xBBE0)"]= ru8(base+0xBBE0)
        # absolute counters at 0x9246CB80
        try:
            hit_row = int(args.hit_abs,16)
            b1 = ru8(hit_row + (args.hit_idx1-1))
            b2 = ru8(hit_row + (args.hit_idx2-1))
            vals[f"hit_cnt({hex(hit_row)} idx{args.hit_idx1})"] = b1
            vals[f"air_cnt({hex(hit_row)} idx{args.hit_idx2})"] = b2
        except Exception:
            pass
        return vals

    last_val = read_target()
    last_panel = panel()
    last_row_echo = 0.0
    last_echo = time.time()

    # Print initial panel
    print("\n[PANEL INIT]")
    print(f"  target={hex(target_addr)} value={last_val}")
    for k,v in last_panel.items():
        print(f"  {k:<24} {v}")

    while True:
        v = read_target()
        changed = (v != last_val)

        t = time.time()
        # periodic echo even if no change
        if (t - last_echo) >= args.echo_every:
            print(f"\n[ECHO] {time.strftime('%H:%M:%S')}  target={hex(target_addr)} value={v}")
            cur_panel = panel()
            for k in sorted(cur_panel.keys()):
                print(f"  {k:<24} {cur_panel[k]}")
            # also print the row once every echo
            print("[ROW]")
            print(dump_row(row_addr, args.row_len))
            last_echo = t
            last_row_echo = t
            last_panel = cur_panel

        # edge on any change (zero->nonzero, nonzero->zero, or any other change)
        if changed:
            print(f"\n[EDGE] {time.strftime('%H:%M:%S')}  {hex(target_addr)}  {last_val} -> {v}")
            cur_panel = panel()
            # diff-style print
            for k in sorted(cur_panel.keys()):
                pv = last_panel.get(k)
                cv = cur_panel[k]
                if pv != cv:
                    print(f"  {k:<24} {pv} -> {cv}")
                else:
                    print(f"  {k:<24} {cv} (no change)")
            # fresh row snapshot at the moment of the edge
            print("[ROW @ EDGE]")
            print(dump_row(row_addr, args.row_len))
            last_panel = cur_panel
            last_val = v

        time.sleep(args.poll)

if __name__ == "__main__":
    main()
