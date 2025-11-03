#!/usr/bin/env python3
# tvc_anim_scan.py – scan RAM for TvC animation/control rows
import argparse, re, sys

# ---- PDME hook ----
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
        _dme.hook(); return _dme
    if hasattr(_dme, "DolphinMemoryEngine"):
        d = _dme.DolphinMemoryEngine(); d.hook(); return d
    raise RuntimeError("Unsupported PDME API")

MEM1 = (0x80000000, 0x81800000)
MEM2 = (0x90000000, 0x94000000)

def ranges_for(mem):
    if mem == "mem1": return [MEM1]
    if mem == "mem2": return [MEM2]
    return [MEM1, MEM2]

def page_up(a, pagesz=0x1000):
    return ((a // pagesz) + 1) * pagesz

def compile_hex_pattern(hexpat: str) -> re.Pattern:
    # supports "AA BB ?? CC"
    bs = bytearray()
    for tok in hexpat.strip().split():
        if tok == "??":
            bs.extend(b".")
        else:
            bs.append(int(tok, 16))
    return re.compile(bytes(bs), re.S)

def scan(args):
    d = hook()
    pat = compile_hex_pattern(args.pattern)
    hits = []
    for start, end in ranges_for(args.mem):
        pos, tail = start, b""
        while pos < end and len(hits) < args.limit:
            n = min(args.chunk, end - pos)
            try:
                buf = tail + d.read_bytes(pos, n)
            except Exception:
                pos = page_up(pos); tail = b""; continue
            base = pos - len(tail)
            i = 0
            while len(hits) < args.limit:
                m = pat.search(buf, i)
                if not m: break
                hit_addr = base + m.start()
                hits.append(hit_addr)
                print(f"hit @{hex(hit_addr)}")
                i = m.start() + 1
            tail = buf[-args.overlap:] if len(buf) >= args.overlap else buf
            pos += n
    # csv
    if args.csv and hits:
        import csv
        with open(args.csv, "w", newline="") as fp:
            w = csv.writer(fp)
            w.writerow(["addr_hex","addr_int"])
            for h in hits:
                w.writerow([hex(h), h])
        print(f"[csv] wrote {args.csv}")
    print(f"[scan] pattern={args.pattern} hits={len(hits)}")

def main():
    ap = argparse.ArgumentParser(description="TvC animation/control scanner")
    ap.add_argument("--mem", choices=["mem1","mem2","both"], default="mem2")
    ap.add_argument("--chunk", type=lambda s: int(s,0), default=0x40000)
    ap.add_argument("--overlap", type=int, default=64)
    ap.add_argument("--limit", type=int, default=5000)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--pattern", required=True)
    args = ap.parse_args()
    scan(args)

if __name__ == "__main__":
    main()
