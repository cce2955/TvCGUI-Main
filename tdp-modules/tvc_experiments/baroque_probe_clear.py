#!/usr/bin/env python3
# tvc_experiments/baroque_probe_clear.py
#
# Clear-screen status panel + edge-triggered diffs for Baroque.
# - Polls the absolute Baroque-ready byte (default 0x9246CBAB).
# - On each edge (00 -> nonzero, or falling if enabled) snapshots fighter_base ranges
#   and prints which bytes changed.
# - Every tick, clears screen and prints a compact panel with the exact values watched.
#
# Usage examples:
#   python tvc_experiments/baroque_probe_clear.py --slot P1C1
#   python tvc_experiments/baroque_probe_clear.py --slot P1C1 --hz 60 --edge rising \
#       --ranges 0x000-0x400,0xB800-0xCC00
#
# Notes:
# - Ranges are RELATIVE to fighter_base and inclusive-exclusive.
# - Uses ANSI clear; on old consoles pass --hard-cls to use os.system('cls'/'clear').

import argparse, time, os, struct, sys
import dolphin_memory_engine as dme

MANAGERS = {
    "P1C1": 0x803C9FCC,
    "P1C2": 0x803C9FDC,
    "P2C1": 0x803C9FD4,
    "P2C2": 0x803C9FE4,
}

DEFAULT_WATCH_ABS = 0x9246CBAB
DEFAULT_RANGES = [(0x000,0x400), (0xB800,0xCC00)]  # health/control + late “B*/CBA*” blocks

def hook_block():
    while not dme.is_hooked():
        try:
            dme.hook()
        except Exception:
            pass
        time.sleep(0.2)

def be_u32(b): return struct.unpack(">I", b)[0]
def rd32(addr):
    b = dme.read_bytes(addr, 4)
    if not b or len(b) != 4: return None
    return be_u32(b)

def rd8(addr):
    b = dme.read_bytes(addr, 1)
    if not b or len(b) != 1: return None
    return b[0]

def rbytes(addr, n):
    b = dme.read_bytes(addr, n)
    return b if b else b""

def resolve_fighter_base(slot):
    mgr = MANAGERS[slot]
    p1 = rd32(mgr)
    if not p1: return None
    # one-or-two hop chain; prefer the second hop if it looks like RAM
    p2 = rd32(p1) if p1 else None
    if p2 and (0x80000000 <= p2 <= 0x93FFFFFF):
        return p2
    return p1 if (0x80000000 <= (p1 or 0) <= 0x93FFFFFF) else None

def read_hp_block(base):
    maxhp = rd32(base + 0x24)
    curhp = rd32(base + 0x28)
    red   = rd32(base + 0x2C)
    return maxhp, curhp, red

def parse_ranges(spec):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part: continue
        a,b = part.split("-",1)
        out.append((int(a,16), int(b,16)))
    return out

def dump_region(base, ranges):
    slabs = []
    for (lo,hi) in ranges:
        start = base + lo
        length = hi - lo
        slabs.append((lo, rbytes(start, length)))
    return slabs

def diff_slabs(before, after):
    out = []
    amap = {off:data for off,data in after}
    for off0,data0 in before:
        data1 = amap.get(off0, b"")
        changed = []
        L = min(len(data0), len(data1))
        for i in range(L):
            if data0[i] != data1[i]:
                changed.append((i, data0[i], data1[i]))
        if changed:
            out.append((off0, changed))
    return out

def hexrow(addr, count=0x20):
    b = rbytes(addr, count)
    if not b: return ["<unreadable>"]
    # 2 rows of 16
    l1 = " ".join(f"{x:02X}" for x in b[:16]).ljust(16*3 - 1)
    l2 = " ".join(f"{x:02X}" for x in b[16:32]).ljust(16*3 - 1) if len(b) >= 32 else ""
    hdr1 = f"{addr:08X}: {l1}"
    hdr2 = f"{addr+16:08X}: {l2}" if l2 else ""
    return [hdr1] + ([hdr2] if hdr2 else [])

def cls(hard=False):
    if hard:
        os.system("cls" if os.name=="nt" else "clear")
    else:
        # ANSI clear + home
        sys.stdout.write("\x1b[2J\x1b[H")
        sys.stdout.flush()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=list(MANAGERS.keys()), required=True)
    ap.add_argument("--watch", default=hex(DEFAULT_WATCH_ABS), help="absolute addr (Baroque-ready byte), e.g. 0x9246CBAB")
    ap.add_argument("--edge", choices=["rising","falling","both"], default="rising")
    ap.add_argument("--hz", type=float, default=30.0)
    ap.add_argument("--ranges", default=",".join([f"0x{a:X}-0x{b:X}" for a,b in DEFAULT_RANGES]))
    ap.add_argument("--min-gap", type=float, default=0.25, help="edge debounce seconds")
    ap.add_argument("--panel-row", default="0x9246CBA0", help="row to print (base addr of 32-byte dump)")
    ap.add_argument("--hard-cls", action="store_true", help="use OS clear instead of ANSI")
    args = ap.parse_args()

    hook_block()

    base = resolve_fighter_base(args.slot)
    if not base:
        print(f"[FATAL] could not resolve fighter_base for {args.slot}")
        return

    watch_addr = int(args.watch, 16)
    ranges = parse_ranges(args.ranges)
    panel_row = int(args.panel_row, 16)
    interval = 1.0 / max(args.hz, 1.0)

    last = rd8(watch_addr)
    if last is None:
        print(f"[FATAL] cannot read watch byte @ {hex(watch_addr)}")
        return

    last_edge_ts = 0.0
    last_panel_print = 0.0

    # Initial pre-snapshots so first edge has a baseline
    pre = dump_region(base, ranges)

    while True:
        v = rd8(watch_addr)
        if v is None:
            time.sleep(interval)
            continue

        # Panel: what we are watching right now
        now = time.time()
        if now - last_panel_print >= max(0.1, interval):
            maxhp, curhp, red = read_hp_block(base)
            cls(args.hard_cls)
            print(f"[ECHO] {time.strftime('%H:%M:%S')}  watch={hex(watch_addr)} value={v}  slot={args.slot} base={hex(base)}")
            print(f"  HP: {curhp if curhp is not None else '??'} / {maxhp if maxhp is not None else '??'}"
                  f"  Red: {red if red is not None else '??'}"
                  f"  HP%10==0? {'YES' if (curhp is not None and curhp % 10 == 0) else 'NO'}")
            # also show f062/f063 quick peek, if readable
            f062 = rd8(base + 0x62); f063 = rd8(base + 0x63)
            print(f"  f062={f062 if f062 is not None else '??'}  f063={f063 if f063 is not None else '??'}")

            print("[ROW]")
            for line in hexrow(panel_row, 0x20):
                print(line)
            last_panel_print = now

        # Edge detection
        edge = None
        if args.edge in ("rising","both") and last == 0x00 and v != 0x00:
            edge = "rising"
        elif args.edge in ("falling","both") and last != 0x00 and v == 0x00:
            edge = "falling"

        if edge:
            if now - last_edge_ts >= args.min_gap:
                last_edge_ts = now
                # Snapshot just after the flip
                maxhp, curhp, red = read_hp_block(base)
                post = dump_region(base, ranges)
                diffs = diff_slabs(pre, post)

                print("")
                print(f"[EDGE] {time.strftime('%H:%M:%S')}  {hex(watch_addr)}  {last} -> {v}")
                print(f"  HP={curhp if curhp is not None else '??'}/{maxhp if maxhp is not None else '??'}  "
                      f"Red={red if red is not None else '??'}  HP%10==0? "
                      f"{'YES' if (curhp is not None and curhp % 10 == 0) else 'NO'}")

                if not diffs:
                    print("[DIFF] no bytes changed in selected ranges")
                else:
                    # Rank by # of changed bytes and print first few changes per slab
                    diffs_sorted = sorted(diffs, key=lambda t: len(t[1]), reverse=True)
                    for off, changes in diffs_sorted:
                        print(f"[SLAB] +0x{off:X}  changed={len(changes)}")
                        shown = 0
                        for i, o, n in changes:
                            if shown >= 24:
                                print("        ...")
                                break
                            abs_off = off + i
                            print(f"        +0x{abs_off:04X}  {o:02X}->{n:02X}")
                            shown += 1

                # Keep the most recent snapshot as "pre" for next edge
                pre = post
                print("")

        last = v
        time.sleep(interval)

if __name__ == "__main__":
    main()
