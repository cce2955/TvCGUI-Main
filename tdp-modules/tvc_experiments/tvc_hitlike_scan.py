# tvc_hitlike_scan.py
#
# scan RAM for clusters of "hitbox-like" floats:
#   0x3F000000 (0.5)
#   0x3F800000 (1.0)
#   0x41200000 (10.0)
#   0x41A00000 (20.0)
# optionally more
#
# usage:
#   python tvc_hitlike_scan.py --mem mem2 --chunk 0x40000 --overlap 128 --limit 5000 --csv hitlike_mem2.csv
#   python tvc_hitlike_scan.py --mem both ...
#
import argparse, struct, csv, sys

# ----- PDME hook (same pattern as your old files) -----
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

def read_bytes(dme, addr, size):
    if hasattr(dme, "read_bytes"):
        return dme.read_bytes(addr, size)
    return _dme.read_bytes(addr, size)

# Wii RAM
MEM1 = (0x80000000, 0x81800000)
MEM2 = (0x90000000, 0x94000000)

def ranges_for(mem):
    if mem == "mem1": return [MEM1]
    if mem == "mem2": return [MEM2]
    return [MEM1, MEM2]

# ----- core scan -----

# floats we consider "hitboxy"
HITLIKE_U32 = {
    0x3F000000,  # 0.5
    0x3F800000,  # 1.0
    0x41200000,  # 10.0
    0x41A00000,  # 20.0
    0x00000000,  # a lot of your blocks had 0.0 spacers
}

def is_hitlike_u32(u):
    return u in HITLIKE_U32

def u32_at(buf, off):
    return struct.unpack_from(">I", buf, off)[0]  # big endian

def scan_hitlike(dme, mem, chunk, overlap, limit, window):
    hits = []
    for start, end in ranges_for(mem):
        pos = start
        tail = b""
        while pos < end:
            n = min(chunk, end - pos)
            try:
                buf = tail + read_bytes(dme, pos, n)
            except Exception:
                # skip unreadable page
                pos = ((pos // 0x1000) + 1) * 0x1000
                tail = b""
                continue

            base = pos - len(tail)
            buflen = len(buf)
            # scan 4-byte aligned
            i = 0
            while i + 4 <= buflen:
                u = u32_at(buf, i)
                if is_hitlike_u32(u):
                    # look ahead within window
                    cluster = [(base + i, u)]
                    j = i + 4
                    endj = min(buflen, i + window)
                    while j + 4 <= endj:
                        u2 = u32_at(buf, j)
                        if is_hitlike_u32(u2):
                            cluster.append((base + j, u2))
                        j += 4
                    # if we got at least 3 values, call it a candidate
                    if len(cluster) >= 3:
                        hits.append(cluster)
                        if len(hits) >= limit:
                            return hits
                        # skip a bit so we don't re-detect the same block
                        i += 4
                        continue
                i += 4

            tail = buf[-overlap:] if len(buf) >= overlap else buf
            pos += n
    return hits

def main():
    ap = argparse.ArgumentParser(description="scan RAM for hitbox-like float clusters")
    ap.add_argument("--mem", choices=["mem1", "mem2", "both"], default="mem2")
    ap.add_argument("--chunk", type=lambda s: int(s, 0), default=0x40000)
    ap.add_argument("--overlap", type=int, default=128)
    ap.add_argument("--limit", type=int, default=5000, help="max clusters")
    ap.add_argument("--window", type=lambda s: int(s, 0), default=0x40, help="bytes to look ahead inside a block")
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    dme = hook()
    clusters = scan_hitlike(dme, args.mem, args.chunk, args.overlap, args.limit, args.window)
    print(f"[scan] found {len(clusters)} hitbox-like clusters")

    if args.csv:
        with open(args.csv, "w", newline="") as f:
            w = csv.writer(f)
            # one row per float in cluster
            w.writerow(["cluster_id", "addr_hex", "addr_int", "u32_hex", "f32"])
            cid = 0
            for cluster in clusters:
                for addr, u in cluster:
                    try:
                        f32 = struct.unpack(">f", struct.pack(">I", u))[0]
                    except Exception:
                        f32 = ""
                    w.writerow([cid, hex(addr), addr, hex(u), f32])
                cid += 1
        print(f"[csv] wrote {args.csv}")

if __name__ == "__main__":
    main()

