#!/usr/bin/env python3
# tvc_sigtool.py - tiny TvC RAM helper (scan / dump / poke)

import argparse, re, sys, csv

# ------------ PDME hook ------------
_dme = None
try:
    import dolphin_memory_engine as _dme
except Exception:
    try:
        import PyDolphinMemoryEngine as _dme
    except Exception:
        _dme = None

if _dme is None:
    raise RuntimeError("Could not import dolphin_memory_engine / PyDolphinMemoryEngine")

def hook():
    if hasattr(_dme, "hook"):
        _dme.hook()
        return _dme
    if hasattr(_dme, "DolphinMemoryEngine"):
        d = _dme.DolphinMemoryEngine()
        d.hook()
        return d
    raise RuntimeError("Unsupported PDME API")

def read_bytes(dme, addr: int, size: int) -> bytes:
    if hasattr(dme, "read_bytes"):
        return dme.read_bytes(addr, size)
    return _dme.read_bytes(addr, size)

def write_bytes(dme, addr: int, data: bytes) -> None:
    if hasattr(dme, "write_bytes"):
        dme.write_bytes(addr, data)
        return
    _dme.write_bytes(addr, data)

# ------------ memory ranges ------------
MEM1 = (0x80000000, 0x81800000)
MEM2 = (0x90000000, 0x94000000)

def ranges_for(mem: str):
    if mem == "mem1":
        return [MEM1]
    if mem == "mem2":
        return [MEM2]
    return [MEM1, MEM2]

def page_align_up(a: int, pagesz: int = 0x1000) -> int:
    return ((a // pagesz) + 1) * pagesz

# ------------ helpers ------------
def compile_pattern(pat: str) -> re.Pattern:
    # "AA BB ?? CC" -> b"\xAA\xBB.\xCC"
    bs = bytearray()
    for tok in pat.strip().split():
        if tok == "??":
            bs.extend(b".")
        else:
            bs.append(int(tok, 16))
    return re.compile(bytes(bs), re.S)

def write_csv(path: str, header, rows):
    with open(path, "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(header)
        for r in rows:
            w.writerow(r)
    print(f"[csv] wrote {path}")

# ------------ commands ------------

def cmd_scan_generic(args):
    d = hook()
    regex = compile_pattern(args.pattern)
    rows = []
    hits = 0
    for start, end in ranges_for(args.mem):
        pos, tail = start, b""
        while pos < end and hits < args.limit:
            n = min(args.chunk, end - pos)
            try:
                buf = tail + read_bytes(d, pos, n)
            except Exception:
                pos = page_align_up(pos)
                tail = b""
                continue
            base = pos - len(tail)
            i = 0
            while hits < args.limit:
                m = regex.search(buf, i)
                if not m:
                    break
                addr = base + m.start()
                print(f"hit @{hex(addr)}")
                rows.append([hex(addr), addr])
                hits += 1
                i = m.start() + 1
            tail = buf[-args.overlap:] if len(buf) >= args.overlap else buf
            pos += n
    if args.csv and rows:
        write_csv(args.csv, ["addr_hex", "addr_int"], rows)

def cmd_dump(args):
    d = hook()
    addr = int(args.addr, 0)
    size = int(args.size, 0)
    buf = read_bytes(d, addr, size)
    # pretty hex
    base = addr
    for off in range(0, len(buf), 0x10):
        chunk = buf[off:off+0x10]
        hexes = " ".join(f"{b:02X}" for b in chunk)
        print(f"{base+off:08X}  {hexes}")
    # no csv here; this is just for eyeballing

def cmd_poke(args):
    d = hook()
    addr = int(args.addr, 0)
    data = bytes.fromhex(args.bytes.replace(" ", ""))
    write_bytes(d, addr, data)
    print(f"[poke] wrote {len(data)} byte(s) to {hex(addr)}")

# ------------ cli ------------
def build_cli():
    ap = argparse.ArgumentParser(description="TvC RAM sig + poke helper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--mem", choices=["mem1", "mem2", "both"], default="mem2")
        p.add_argument("--chunk", type=lambda s: int(s, 0), default=0x40000)
        p.add_argument("--overlap", type=int, default=64)
        p.add_argument("--limit", type=int, default=2000)
        p.add_argument("--csv", default=None)

    # scan-generic
    p1 = sub.add_parser("scan-generic", help="scan RAM for hex pattern with ??")
    add_common(p1)
    p1.add_argument("--pattern", required=True, help='e.g. "04 01 60 00 00 00 ?? ?? 3F 00 00 00"')
    p1.set_defaults(run=cmd_scan_generic)

    # dump
    p2 = sub.add_parser("dump", help="dump bytes around an address")
    p2.add_argument("--addr", required=True, help="address, e.g. 0x908aee10")
    p2.add_argument("--size", default="0x100", help="how many bytes to read")
    p2.set_defaults(run=cmd_dump)

    # poke
    p3 = sub.add_parser("poke", help="write raw bytes at address")
    p3.add_argument("--addr", required=True)
    p3.add_argument("--bytes", required=True)
    p3.set_defaults(run=cmd_poke)

    return ap

def main():
    ap = build_cli()
    args = ap.parse_args()
    args.run(args)

if __name__ == "__main__":
    main()
