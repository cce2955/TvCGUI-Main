# tvc_frame_scan.py
# quick-and-dirty: scrape every "04 01 60" block and dump raw bytes
import argparse, re, csv, os, sys

# try both PDME names
try:
    import dolphin_memory_engine as dme_mod
except Exception:
    try:
        import PyDolphinMemoryEngine as dme_mod
    except Exception:
        dme_mod = None

if dme_mod is None:
    raise RuntimeError("Could not import dolphin_memory_engine / PyDolphinMemoryEngine")

def hook():
    if hasattr(dme_mod, "hook"):
        dme_mod.hook()
        return dme_mod
    if hasattr(dme_mod, "DolphinMemoryEngine"):
        d = dme_mod.DolphinMemoryEngine()
        d.hook()
        return d
    raise RuntimeError("Unsupported PDME API")

MEM1 = (0x80000000, 0x81800000)
MEM2 = (0x90000000, 0x94000000)

PAT = re.compile(b"\x04\x01\x60", re.S)

def read_bytes(dme, addr, size):
    return dme.read_bytes(addr, size)

def page_align_up(a, page=0x1000):
    return ((a // page) + 1) * page

def scan_range(dme, start, end, chunk, overlap, grab, limit):
    hits = []
    pos = start
    tail = b""
    while pos < end:
        n = min(chunk, end - pos)
        try:
            buf = tail + read_bytes(dme, pos, n)
        except Exception:
            pos = page_align_up(pos)
            tail = b""
            continue

        base = pos - len(tail)
        i = 0
        while True:
            m = PAT.search(buf, i)
            if not m:
                break
            addr = base + m.start()
            # grab a slice after the match
            slice_start = addr
            slice_end = min(end, addr + grab)
            try:
                raw = read_bytes(dme, slice_start, slice_end - slice_start)
            except Exception:
                raw = b""
            hits.append((addr, raw))
            if len(hits) >= limit:
                return hits
            i = m.start() + 1

        tail = buf[-overlap:] if len(buf) >= overlap else buf
        pos += n

    return hits

def main():
    ap = argparse.ArgumentParser("TvC frame block scraper")
    ap.add_argument("--mem", choices=["mem1", "mem2", "both"], default="mem2")
    ap.add_argument("--chunk", type=lambda s: int(s, 0), default=0x40000)
    ap.add_argument("--overlap", type=int, default=64)
    ap.add_argument("--grab", type=lambda s: int(s, 0), default=0x80,
                    help="how many bytes to dump after 04 01 60")
    ap.add_argument("--limit", type=int, default=4000)
    ap.add_argument("--csv", default="frame_blocks.csv")
    args = ap.parse_args()

    dme = hook()

    ranges = []
    if args.mem in ("mem1", "both"):
        ranges.append(MEM1)
    if args.mem in ("mem2", "both"):
        ranges.append(MEM2)

    rows = []
    for start, end in ranges:
        hits = scan_range(dme, start, end, args.chunk, args.overlap, args.grab, args.limit)
        for addr, raw in hits:
            rows.append({
                "addr_hex": hex(addr),
                "addr_int": addr,
                "raw_hex": raw.hex()
            })

    with open(args.csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["addr_hex","addr_int","raw_hex"])
        w.writeheader()
        for r in rows:
            w.writerow(r)

    print(f"[scan] dumped {len(rows)} frame-like blocks to {args.csv}")

if __name__ == "__main__":
    main()