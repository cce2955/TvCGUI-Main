# tvc_experiments/field_probe.py
# Watch selected fields at fighter_base and decode them multiple ways.
# Requires local dolphin_io.py (rd8, rbytes, hook, addr_in_ram).

import argparse, time, csv, os, struct, string
import dolphin_io as dio

PRINTABLE = set(bytes(string.printable, 'ascii'))

def is_printable(bs):
    return all(b in PRINTABLE and b not in b"\r\n\t\x0b\x0c" for b in bs)

def hexbytes(bs):
    return " ".join(f"{b:02X}" for b in bs)

def decodes_2(bs):
    """Return dict of interpretations for 2-byte windows."""
    if len(bs) != 2: return {}
    u16be = struct.unpack(">H", bs)[0]
    u16le = struct.unpack("<H", bs)[0]
    s16be = struct.unpack(">h", bs)[0]
    s16le = struct.unpack("<h", bs)[0]
    asc   = bs.decode('ascii', errors='ignore') if is_printable(bs) else ""
    return {
        "u16be": u16be, "u16le": u16le,
        "s16be": s16be, "s16le": s16le,
        "ascii": asc
    }

def decodes_4(bs):
    """Return dict of interpretations for 4-byte windows."""
    if len(bs) != 4: return {}
    u32be = struct.unpack(">I", bs)[0]
    u32le = struct.unpack("<I", bs)[0]
    s32be = struct.unpack(">i", bs)[0]
    s32le = struct.unpack("<i", bs)[0]
    f32be = struct.unpack(">f", bs)[0]
    f32le = struct.unpack("<f", bs)[0]
    asc   = bs.decode('ascii', errors='ignore') if is_printable(bs) else ""
    return {
        "u32be": u32be, "u32le": u32le,
        "s32be": s32be, "s32le": s32le,
        "f32be": f32be, "f32le": f32le,
        "ascii": asc,
    }

def parse_fields(spec):
    """
    spec example: +0x34:2,+0x36:2,+0x48:2,+0x50:2,+0x34:4,+0x48:4
    returns list of (offset, size)
    """
    out = []
    for part in spec.split(","):
        part = part.strip()
        if not part: continue
        if ":" not in part: raise ValueError
        off_s, sz_s = part.split(":")
        off = int(off_s, 16) if off_s.startswith(("0x","+0x")) else int(off_s)
        sz  = int(sz_s, 16) if sz_s.startswith("0x") else int(sz_s)
        if sz not in (1,2,4,8): raise ValueError("only 1/2/4/8 supported")
        out.append((off, sz))
    return out

def read_at(base, off, size):
    b = dio.rbytes(base + off, size)
    return b if b and len(b) == size else None

def main():
    ap = argparse.ArgumentParser(description="Field probe with multi-interpret decode")
    ap.add_argument("--base", required=True, type=lambda x:int(x,16), help="fighter_base (hex), e.g. 0x9246B9E0")
    ap.add_argument("--fields", default="+0x34:2,+0x36:2,+0x48:2,+0x50:2,+0x34:4,+0x48:4",
                    help="comma list of +offset:size (hex ok). Defaults target your noted slots and their 4B pairs.")
    ap.add_argument("--hz", type=float, default=30.0, help="poll rate (default 30Hz)")
    ap.add_argument("--out", default=None, help="CSV path (auto if omitted)")
    args = ap.parse_args()

    dio.hook()

    base = args.base
    if not dio.addr_in_ram(base):
        raise SystemExit(f"Base {hex(base)} not in MEM1/MEM2")

    fields = parse_fields(args.fields)

    if args.out:
        csv_path = args.out
    else:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        csv_path = os.path.join(os.getcwd(), f"field_probe_{stamp}.csv")

    fcsv = open(csv_path, "w", newline="")
    wr = csv.writer(fcsv)
    # CSV header
    wr.writerow(["time","base","off","size","raw_hex",
                 "u16be","u16le","s16be","s16le",
                 "u32be","u32le","s32be","s32le","f32be","f32le","ascii"])

    # Prime previous snapshots
    prev = {}
    for off, sz in fields:
        b = read_at(base, off, sz) or b""
        prev[(off,sz)] = b

    per = 1.0/max(args.hz, 1.0)
    print(f"[START] base={hex(base)} fields={', '.join(f'+0x{off:X}:{sz}' for off,sz in fields)} @ {args.hz:.1f}Hz")
    try:
        while True:
            any_change = False
            lines = []
            ts = time.strftime("%H:%M:%S")

            for off, sz in fields:
                b = read_at(base, off, sz)
                if b is None:  # transient read miss
                    continue
                if b != prev[(off,sz)]:
                    any_change = True
                    prev[(off,sz)] = b
                    h = hexbytes(b)
                    # decode
                    d2 = decodes_2(b) if sz==2 else {}
                    d4 = decodes_4(b) if sz==4 else {}
                    row = {
                        "time": ts, "base": f"0x{base:08X}",
                        "off": f"+0x{off:04X}", "size": sz, "raw_hex": h,
                        "u16be": d2.get("u16be",""),
                        "u16le": d2.get("u16le",""),
                        "s16be": d2.get("s16be",""),
                        "s16le": d2.get("s16le",""),
                        "u32be": d4.get("u32be",""),
                        "u32le": d4.get("u32le",""),
                        "s32be": d4.get("s32be",""),
                        "s32le": d4.get("s32le",""),
                        "f32be": f"{d4['f32be']:.6f}" if "f32be" in d4 else "",
                        "f32le": f"{d4['f32le']:.6f}" if "f32le" in d4 else "",
                        "ascii": d2.get("ascii","") if sz==2 else d4.get("ascii","")
                    }
                    wr.writerow([row[k] for k in ["time","base","off","size","raw_hex",
                                                  "u16be","u16le","s16be","s16le",
                                                  "u32be","u32le","s32be","s32le","f32be","f32le","ascii"]])
                    lines.append(f"{row['time']} {row['off']} sz={sz} {h} "
                                 f"u16be={row['u16be']} s16be={row['s16be']} "
                                 f"{'u32be='+str(row['u32be']) if row['u32be']!='' else ''} "
                                 f"{'f32be='+row['f32be'] if row['f32be']!='' else ''} "
                                 f"{'ascii='+repr(row['ascii']) if row['ascii'] else ''}")

            if any_change:
                fcsv.flush()
                # Compact console burst
                for ln in lines:
                    print("[CHG]", ln)

            time.sleep(per)
    finally:
        fcsv.close()

if __name__ == "__main__":
    main()
