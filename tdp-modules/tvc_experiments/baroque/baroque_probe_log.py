#!/usr/bin/env python3
# tvc_experiments/baroque_probe_log.py
#
# Edge-triggered Baroque watcher with durable event logs.
# - Watches absolute Baroque-ready byte (default 0x9246CBAB).
# - On each edge (00->nonzero by default), snapshots selected fighter_base ranges,
#   computes byte diffs, and writes a detailed .log + a compact .csv line.
#
# Console output is minimal: just the two file paths on start, and a one-line
# counter update per event. Everything else goes to disk.

import argparse, time, os, csv, struct, datetime
import dolphin_memory_engine as dme

MANAGERS = {
    "P1C1": 0x803C9FCC,
    "P1C2": 0x803C9FDC,
    "P2C1": 0x803C9FD4,
    "P2C2": 0x803C9FE4,
}

DEFAULT_WATCH_ABS = 0x9246CBAB
DEFAULT_RANGES = [(0x000,0x400), (0xB800,0xCC00)]  # health/control + late “B*/CBA*” blocks
DEFAULT_PANEL_ROW = 0x9246CBA0  

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
    p2 = rd32(p1) if p1 else None
    # Prefer deepest pointer that looks like RAM (MEM1/MEM2 envelope)
    for cand in (p2, p1):
        if cand and 0x80000000 <= cand <= 0x93FFFFFF:
            return cand
    return None

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
        length = max(0, hi - lo)
        slabs.append((lo, rbytes(start, length)))
    return slabs

def diff_slabs(before, after, max_changes_to_keep=1024):
    out = []
    amap = {off:data for off,data in after}
    total = 0
    for off0,data0 in before:
        data1 = amap.get(off0, b"")
        changed = []
        L = min(len(data0), len(data1))
        for i in range(L):
            if data0[i] != data1[i]:
                changed.append((i, data0[i], data1[i]))
                total += 1
                if total >= max_changes_to_keep:
                    break
        if changed:
            out.append((off0, changed))
        if total >= max_changes_to_keep:
            break
    return out, total

def hexrow(addr, count=0x20):
    b = rbytes(addr, count)
    if not b: return ["<unreadable>"]
    l1 = " ".join(f"{x:02X}" for x in b[:16]).ljust(16*3 - 1)
    l2 = " ".join(f"{x:02X}" for x in b[16:32]).ljust(16*3 - 1) if len(b) >= 32 else ""
    hdr1 = f"{addr:08X}: {l1}"
    hdr2 = f"{addr+16:08X}: {l2}" if l2 else ""
    return [hdr1] + ([hdr2] if hdr2 else [])

def ensure_dir(p):
    os.makedirs(p, exist_ok=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=list(MANAGERS.keys()), required=True)
    ap.add_argument("--watch", default=hex(DEFAULT_WATCH_ABS),
                    help="absolute addr for Baroque-ready byte (e.g. 0x9246CBAB)")
    ap.add_argument("--edge", choices=["rising","falling","both"], default="rising")
    ap.add_argument("--hz", type=float, default=60.0)
    ap.add_argument("--ranges", default=",".join([f"0x{a:X}-0x{b:X}" for a,b in DEFAULT_RANGES]),
                    help="comma-separated fighter_base-relative ranges (hex), inclusive-exclusive")
    ap.add_argument("--panel-row", default=hex(DEFAULT_PANEL_ROW),
                    help="absolute row to include in logs as a 32-byte hex dump (e.g. 0x9246CBA0)")
    ap.add_argument("--min-gap", type=float, default=0.20, help="edge debounce seconds")
    ap.add_argument("--logdir", default="tvc_logs", help="directory for logs")
    ap.add_argument("--tag", default="", help="optional tag appended to filenames")
    ap.add_argument("--max-diff", type=int, default=4096, help="cap # of byte diffs saved per event")
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

    # Prepare logs
    ensure_dir(args.logdir)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{args.tag}" if args.tag else ""
    log_path = os.path.join(args.logdir, f"baroque_events_{args.slot}{tag}_{ts}.log")
    csv_path = os.path.join(args.logdir, f"baroque_events_{args.slot}{tag}_{ts}.csv")

    with open(log_path, "w", encoding="utf-8") as lf, open(csv_path, "w", newline="") as cf:
        csvw = csv.writer(cf)
        csvw.writerow([
            "time","edge","watch_addr","prev","now",
            "slot","fighter_base",
            "cur_hp","max_hp","red_life","hp_mod10",
            "diff_total","top_offsets"  # top_offsets = first few slab offsets that changed
        ])

        print(f"[LOG] events -> {log_path}")
        print(f"[LOG] csv    -> {csv_path}")

        last = rd8(watch_addr)
        if last is None:
            print(f"[FATAL] cannot read watch byte @ {hex(watch_addr)}")
            return

        # Baseline snapshot
        pre = dump_region(base, ranges)
        last_edge_ts = 0.0
        event_count = 0

        while True:
            v = rd8(watch_addr)
            if v is None:
                time.sleep(interval)
                continue

            edge = None
            if args.edge in ("rising","both") and last == 0x00 and v != 0x00:
                edge = "rising"
            elif args.edge in ("falling","both") and last != 0x00 and v == 0x00:
                edge = "falling"

            now = time.time()
            if edge and (now - last_edge_ts) >= args.min_gap:
                last_edge_ts = now
                event_count += 1

                # Read context
                maxhp, curhp, red = read_hp_block(base)
                post = dump_region(base, ranges)
                diffs, diff_total = diff_slabs(pre, post, max_changes_to_keep=args.max_diff)

                # Build human-readable block
                tstr = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
                lf.write(f"[EDGE] {tstr}  {edge}  {hex(watch_addr)}  {last}->{v}\n")
                lf.write(f"  slot={args.slot} base={hex(base)}  HP={curhp}/{maxhp}  Red={red}  HP%10={'YES' if (curhp is not None and curhp % 10 == 0) else 'NO'}\n")
                lf.write(f"  watch-row ({hex(panel_row)}):\n")
                for line in hexrow(panel_row, 0x20):
                    lf.write(f"    {line}\n")

                if not diffs:
                    lf.write("  [DIFF] no bytes changed in selected ranges\n\n")
                else:
                    # Summarize which slabs changed
                    offsets_sorted = sorted(((off, len(ch)) for off,ch in diffs), key=lambda x: x[1], reverse=True)
                    top_offsets = ", ".join(f"+0x{off:X}({n})" for off,n in offsets_sorted[:8])
                    lf.write(f"  [DIFF] total_changes={diff_total}  slabs={len(diffs)}  top={top_offsets}\n")
                    # Print first N diffs per slab (compact)
                    for off, changes in diffs[:6]:
                        lf.write(f"    [SLAB +0x{off:X}] changed={len(changes)}\n")
                        shown = 0
                        for i, o, n in changes:
                            if shown >= 24:
                                lf.write("      ...\n")
                                break
                            lf.write(f"      +0x{off+i:04X}  {o:02X}->{n:02X}\n")
                            shown += 1
                    lf.write("\n")

                # CSV one-liner
                hp_mod10 = (curhp % 10 == 0) if (curhp is not None) else ""
                top_offsets_csv = ";".join(f"+0x{off:X}" for off,_ in offsets_sorted[:8]) if diffs else ""
                csvw.writerow([
                    tstr, edge, f"0x{watch_addr:X}", last, v,
                    args.slot, f"0x{base:X}",
                    curhp if curhp is not None else "", maxhp if maxhp is not None else "", red if red is not None else "", hp_mod10,
                    diff_total, top_offsets_csv
                ])
                cf.flush()
                lf.flush()

                # Prepare baseline for next edge
                pre = post

                print(f"[EVT #{event_count}] {edge} {last}->{v}  diffs={diff_total}")

            last = v
            time.sleep(interval)

if __name__ == "__main__":
    main()
