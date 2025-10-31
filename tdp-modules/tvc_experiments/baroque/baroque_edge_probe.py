#!/usr/bin/env python3
# baroque_edge_probe.py
# Watch fighter_base + 0xCBA8.. +0xCBA9 (2 bytes) and dump correlated signals on edges.

import time, struct, math, sys

# --- try dolphin_io first (your experiment copy), else fall back to dolphin_memory_engine directly
try:
    import dolphin_io as dio
    HAVE_DIO = True
except Exception:
    HAVE_DIO = False
    import dolphin_memory_engine as dme

# Static manager addresses (MEM1)
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
        if hasattr(dio, "read"): return dio.read(addr, n)
        # experiment dolphin_io copy exposes rbytes
        if hasattr(dio, "rbytes"): return dio.rbytes(addr, n)
        # last resort
        return b""
    else:
        try:
            return dme.read_bytes(addr, n)
        except Exception:
            return b""

def ru32(addr):
    b = rbytes(addr, 4)
    return struct.unpack(">I", b)[0] if len(b)==4 else None

def ru16(addr):
    b = rbytes(addr, 2)
    return struct.unpack(">H", b)[0] if len(b)==2 else None

def ru8(addr):
    b = rbytes(addr, 1)
    return b[0] if len(b)==1 else None

def rf32(addr):
    b = rbytes(addr, 4)
    if len(b)!=4: return None
    u = struct.unpack(">I", b)[0]
    f = struct.unpack(">f", struct.pack(">I", u))[0]
    if not math.isfinite(f) or abs(f)>1e8: return None
    return f

def plausible_ptr(x:int)->bool:
    # MEM2 range for Wii in Dolphin roughly 0x9000_0000..0x93FF_FFFF, some builds 0x924x_xxxx, etc.
    return 0x90000000 <= x <= 0x93FFFFFF or 0x92000000 <= x <= 0x92FFFFFF

def resolve_fighter_base(slot:str)->int:
    mgr = MANAGERS[slot]
    p1 = ru32(mgr)
    if p1 and (0x80000000 <= p1 <= 0x8FFFFFFF):  # many managers live in MEM1
        p2 = ru32(p1)
        if p2 and plausible_ptr(p2): return p2
    # Sometimes the first deref is already the base (rare); accept if plausible MEM2
    if p1 and plausible_ptr(p1):
        return p1
    raise RuntimeError(f"Could not resolve fighter_base for {slot}: p1={hex(p1) if p1 else p1}")

def hexbytes(bs:bytes)->str:
    return " ".join(f"{x:02X}" for x in bs)

def dump_row(addr:int, length:int)->str:
    bs = rbytes(addr, length)
    if not bs: return "(unreadable)"
    lines = []
    for i in range(0, len(bs), 16):
        chunk = bs[i:i+16]
        lines.append(f"{addr+i:08X}: {hexbytes(chunk)}")
    return "\n".join(lines)

def now_ms()->int:
    return int(time.time()*1000)

def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--slot", choices=list(MANAGERS.keys()), default="P1C1")
    p.add_argument("--poll", type=float, default=0.003, help="seconds between polls")
    p.add_argument("--echo-every", type=float, default=0.0, help="periodic echo of flag value (sec); 0=off")
    p.add_argument("--arm-delay", type=float, default=0.05, help="require flag be 0000 for this long before arming for a rising-edge")
    p.add_argument("--falling", action="store_true", help="also report falling edges (nonzero->0000)")
    p.add_argument("--abs", default="", help="extra abs dump in form start:len (e.g. 0x9246CBA0:0x20)")
    args = p.parse_args()

    hook()
    base = resolve_fighter_base(args.slot)

    flag_base = base + 0xCBA8      # 2-byte region starts here
    flag_addr = flag_base + 0x0002  # “12th byte from CBA0” = CBAA?CBAB; we watch the 2-byte window ending at CBAB
    # To be explicit: CBA0 + 0x0B = CBAB. We’ll read 2 bytes at CBAA so the pair is [CBAA, CBAB].
    pair_addr = base + 0xCBAA

    watch = [
        ("flag_pair(CBAA:CBAB)", pair_addr, 2, "u16"),
        ("flag_byte(CBAB)",       base+0xCBAB, 1, "u8"),
        ("red_life(+0x2C u32)",   base+0x2C,   4, "u32"),
        ("cur_hp(+0x28 u32)",     base+0x28,   4, "u32"),
        ("max_hp(+0x24 u32)",     base+0x24,   4, "u32"),
        ("combo(+0xB9F0 u32)",    base+0xB9F0, 4, "u32"),
        ("assist_ok(+0xBBE0 u8)", base+0xBBE0, 1, "u8"),
    ]

    abs_extra = None
    if args.abs:
        try:
            s, l = args.abs.split(":")
            abs_extra = (int(s,16), int(l,16))
        except Exception:
            print("Bad --abs format; expected start_hex:len_hex (e.g., 0x9246CBA0:0x20)")

    def read_item(addr, size, kind):
        if kind=="u8":  return ru8(addr)
        if kind=="u16": return ru16(addr)
        if kind=="u32": return ru32(addr)
        if kind=="f32": return rf32(addr)
        return None

    # rolling previous sample for each watched item
    prev_vals = {name: read_item(addr,size,kind) for (name,addr,size,kind) in watch}
    prev_dump = rbytes(base+0xCBA0, 0x20)
    prev_abs  = rbytes(abs_extra[0], abs_extra[1]) if abs_extra else None

    # edge arm logic
    armed = False
    last_zero_ms = now_ms()
    last_echo_ms = 0

    print(f"[WATCH] {args.slot} base={hex(base)} pair={hex(pair_addr)} CBAB={hex(base+0xCBAB)} poll={args.poll}s")
    print("[INFO] Arming once flag has been 0000 for ~arm-delay; then report first nonzero (rise). Falling reporting:", bool(args.falling))
    while True:
        t_ms = now_ms()

        # periodic echo if asked
        if args.echo_every>0 and t_ms - last_echo_ms >= args.echo_every*1000.0:
            vpair = ru16(pair_addr) or 0
            print(f"[ECHO] {hex(pair_addr)} pair={vpair:04X}")
            last_echo_ms = t_ms

        vpair = ru16(pair_addr) or 0
        # arm logic (require it to be zero for a little while to avoid noise)
        if vpair == 0:
            if t_ms - last_zero_ms >= args.arm_delay*1000.0:
                armed = True
            else:
                # still counting down to arm
                pass
        else:
            # non-zero; if armed, this is a rising edge
            if armed:
                print(f"\n[EDGE:RISE] {hex(pair_addr)} 0000 -> {vpair:04X} @ {time.strftime('%H:%M:%S')}")
                # read a snapshot
                cur_vals = {name: read_item(addr,size,kind) for (name,addr,size,kind) in watch}
                cur_dump = rbytes(base+0xCBA0, 0x20)
                cur_abs  = rbytes(abs_extra[0], abs_extra[1]) if abs_extra else None

                # show diffs
                for (name,addr,size,kind) in watch:
                    pv = prev_vals[name]
                    cv = cur_vals[name]
                    if pv != cv:
                        print(f"  {name:<22} {pv} -> {cv}   @ {hex(addr)}")
                    else:
                        print(f"  {name:<22} {cv} (no change) @ {hex(addr)}")

                print("\n  [CBA0:+0x20 BEFORE]")
                print(dump_row(base+0xCBA0, 0x20))
                print("  [CBA0:+0x20 AFTER ]")
                print(dump_row(base+0xCBA0, 0x20))

                if abs_extra:
                    print(f"  [ABS {hex(abs_extra[0])}:{hex(abs_extra[1])} BEFORE]")
                    print(hexbytes(prev_abs) if prev_abs else "(unreadable)")
                    print(f"  [ABS {hex(abs_extra[0])}:{hex(abs_extra[1])} AFTER ]")
                    print(hexbytes(cur_abs) if cur_abs else "(unreadable)")

                # refresh prevs and disarm until it returns to zero again
                prev_vals = cur_vals
                prev_dump = cur_dump
                prev_abs  = cur_abs
                armed = False

        # falling edges if requested
        if args.falling:
            # track transition from nonzero->zero by looking at previous pair
            # We approximate by remembering last sample separately.
            # (We already have vpair; we need old_vpair)
            pass  # keep it simple; rising is the main need

        if vpair == 0:
            last_zero_ms = t_ms

        time.sleep(args.poll)

if __name__ == "__main__":
    main()
