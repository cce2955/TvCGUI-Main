# tvc_sigtool.py
import argparse, re, sys, csv, json, time

# --- Dolphin hook ---
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

def write_bytes(dme, addr: int, data: bytes):
    if hasattr(dme, "write_bytes"):
        return dme.write_bytes(addr, data)
    return _dme.write_bytes(addr, data)

# memory ranges
MEM1 = (0x80000000, 0x81800000)
MEM2 = (0x90000000, 0x94000000)

def ranges_for(mem: str):
    if mem == "mem1":
        return [MEM1]
    if mem == "mem2":
        return [MEM2]
    return [MEM1, MEM2]

def page_align_up(addr: int, pagesz: int = 0x1000) -> int:
    return ((addr // pagesz) + 1) * pagesz

# ---------- FIXED PATTERN BUILDER ----------
def compile_wildcard_hex_pattern(hexpat: str) -> re.Pattern:
    """
    "AA BB ?? CC" -> b"\\xAA\\xBB.\\xCC" as a regex.
    Every real byte must be escaped so 0x3F (aka '?') doesn't break regex.
    """
    tokens = hexpat.strip().split()
    parts = []
    for tok in tokens:
        if tok == "??":
            parts.append(b".")
        else:
            bval = bytes([int(tok, 16)])
            parts.append(re.escape(bval))
    pattern_bytes = b"".join(parts)
    return re.compile(pattern_bytes, re.S)
# -------------------------------------------

def chunk_scan(d, ranges, regex: re.Pattern, chunk: int, overlap: int, limit: int):
    hits = []
    for start, end in ranges:
        pos = start
        tail = b""
        while pos < end:
            n = min(chunk, end - pos)
            try:
                buf = tail + read_bytes(d, pos, n)
            except Exception:
                # unreadable page, skip to next
                pos = page_align_up(pos)
                tail = b""
                continue
            base = pos - len(tail)

            i = 0
            while True:
                m = regex.search(buf, i)
                if not m:
                    break
                hit_addr = base + m.start()
                print(f"hit @{hex(hit_addr)}")
                hits.append(hit_addr)
                if len(hits) >= limit:
                    return hits
                i = m.start() + 1

            tail = buf[-overlap:] if len(buf) >= overlap else buf
            pos += n
    return hits

def write_csv(path: str, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)
    print(f"[csv] wrote {path}")

# ========== hitbox parse helpers ==========
def _parse_hitbox_block(buf: bytes):
    if len(buf) < 0x50:
        return None
    import struct
    def f(off): return struct.unpack(">f", buf[off:off+4])[0]
    def u(off): return struct.unpack(">I", buf[off:off+4])[0]
    marker = buf[0x10:0x14]
    res = {
        "unknown_A": f(0x00),
        "unknown_B": f(0x04),
        "unknown_C": f(0x08),
        "radius_1": f(0x0C),
        "marker": marker.hex(),
        "subid_1": u(0x14),
        "unknown_F": f(0x18),
        "radius_2": f(0x2C),
        "subid_2": u(0x34),
    }
    return res

def _looks_like_hitbox(buf: bytes):
    return len(buf) >= 0x14 and buf[0x10:0x14] == b"\x35\x0D\x20\x3F"

# -------- commands --------
def cmd_scan_generic(args):
    d = hook()
    # support --hitbox-mode: auto-use full header
    pattern = args.pattern
    if getattr(args, "hitbox_mode", False):
        pattern = "FF FF FF FE 35 0D 20 3F"
    pat = compile_wildcard_hex_pattern(pattern)
    hits = chunk_scan(d, ranges_for(args.mem), pat, args.chunk, args.overlap, args.limit)

    rows = [[hex(a), a] for a in hits]
    if args.csv:
        # write even if empty so you know scan ran
        write_csv(args.csv, ["addr_hex", "addr_int"], rows)
    # still print a little feedback
    if not hits:
        print("[scan] no hits")

def cmd_scan_control(args):
    d = hook()
    pat = compile_wildcard_hex_pattern("00 04 ?? 60")
    hits = chunk_scan(d, ranges_for(args.mem), pat, args.chunk, args.overlap, args.limit)
    rows = []
    for a in hits:
        region = next((r for r in ranges_for(args.mem) if r[0] <= a < r[1]), None)
        if region:
            s = max(region[0], a - args.context)
            e = min(region[1], a + args.context)
            try:
                buf = read_bytes(d, s, e - s)
                print(f"@ {hex(a)}")
                print(" ".join(f"{b:02X}" for b in buf))
                print()
            except Exception:
                pass
        rows.append([hex(a), a])
    if args.csv:
        write_csv(args.csv, ["addr_hex", "addr_int"], rows)

def cmd_poke(args):
    d = hook()
    addr = int(args.addr, 0)
    data = bytes.fromhex(args.bytes.replace(" ", ""))
    write_bytes(d, addr, data)
    rb = read_bytes(d, addr, len(data))
    print(f"[poke] wrote {len(data)} byte(s) to {hex(addr)}")
    print(f"[poke] readback: {rb.hex()}")

def cmd_dump(args):
    d = hook()
    addr = int(args.addr, 0)
    size = int(args.size, 0)
    buf = read_bytes(d, addr, size)
    off = 0
    while off < len(buf):
        line = buf[off:off+0x10]
        print(f"{addr+off:08X}  " + " ".join(f"{b:02X}" for b in line))
        off += 0x10

def cmd_scan_active(args):
    d = hook()
    pat = compile_wildcard_hex_pattern("20 35 01 20 3F 00 00 00 ?? 00 00 00 3F 00 00 00 ?? 07 01 60")
    chunk_scan(d, ranges_for(args.mem), pat, args.chunk, args.overlap, args.limit)

def cmd_scan_damage(args):
    d = hook()
    pat = compile_wildcard_hex_pattern("35 10 20 3F 00 ?? ?? ?? 00 00 00 3F 00 00 00 ??")
    hits = chunk_scan(d, ranges_for(args.mem), pat, args.chunk, args.overlap, args.limit)
    if args.csv:
        write_csv(args.csv, ["addr_hex", "addr_int"], [[hex(a), a] for a in hits])

def cmd_read(args):
    d = hook()
    addr = int(args.addr, 0)
    size = int(args.size, 0)
    data = read_bytes(d, addr, size)
    print(data.hex())
    if args.out:
        with open(args.out, "wb") as f:
            f.write(data)
        print(f"[read] wrote {len(data)} bytes to {args.out}")

def cmd_capture_hitbox(args):
    d = hook()
    addr = int(args.addr, 0)
    size = int(args.size, 0)
    buf = read_bytes(d, addr, size)
    hitbox_part = buf[:0x50]
    rec = {
        "ts": time.time(),
        "player": args.player,
        "char_id": int(args.char_id, 0),
        "attack_id": int(args.attack_id, 0),
        "addr": hex(addr),
        "raw": buf.hex()
    }
    if _looks_like_hitbox(hitbox_part):
        rec["parsed"] = _parse_hitbox_block(hitbox_part)
    else:
        rec["parsed"] = None
    outpath = args.out or "hitbox_capture_log.jsonl"
    with open(outpath, "a") as f:
        f.write(json.dumps(rec) + "\n")
    print(f"[capture-hitbox] wrote entry to {outpath}")

def cmd_copy(args):
    d = hook()
    src = int(args.src, 0)
    dst = int(args.dst, 0)
    size = int(args.size, 0)
    data = read_bytes(d, src, size)
    write_bytes(d, dst, data)
    rb = read_bytes(d, dst, size)
    print(f"[copy] copied {size} bytes from {hex(src)} to {hex(dst)}")
    if data == rb:
        print("[copy] verified OK")
    else:
        print("[copy] WARNING: verification mismatch")

def build_cli():
    ap = argparse.ArgumentParser(description="TvC RAM signature tool (Dolphin)")
    sp = ap.add_subparsers(dest="cmd", required=True)

    def add_common(p):
        p.add_argument("--mem", choices=["mem1", "mem2", "both"], default="mem2")
        p.add_argument("--chunk", type=lambda s: int(s, 0), default=0x40000)
        p.add_argument("--overlap", type=int, default=64)
        p.add_argument("--limit", type=int, default=5000)
        p.add_argument("--csv", default=None)

    # scan-generic
    p = sp.add_parser("scan-generic", help="scan arbitrary hex pattern (?? = wildcard)")
    add_common(p)
    p.add_argument("--pattern", required=False, default="35 0D 20 3F")
    p.add_argument("--hitbox-mode", action="store_true", help="use FF FF FF FE 35 0D 20 3F")
    p.set_defaults(run=cmd_scan_generic)

    # scan-control
    p2 = sp.add_parser("scan-control", help="scan 00 04 ?? 60")
    add_common(p2)
    p2.add_argument("--context", type=lambda s: int(s, 0), default=0x60)
    p2.set_defaults(run=cmd_scan_control)

    # scan-active
    p3 = sp.add_parser("scan-active", help="scan active-frame pattern")
    add_common(p3)
    p3.set_defaults(run=cmd_scan_active)

    # scan-damage
    p4 = sp.add_parser("scan-damage", help="scan damage pattern")
    add_common(p4)
    p4.set_defaults(run=cmd_scan_damage)

    # poke
    p5 = sp.add_parser("poke", help="write bytes")
    p5.add_argument("--addr", required=True)
    p5.add_argument("--bytes", required=True)
    p5.set_defaults(run=cmd_poke)

    # dump
    p6 = sp.add_parser("dump", help="dump RAM")
    p6.add_argument("--addr", required=True)
    p6.add_argument("--size", required=True)
    p6.set_defaults(run=cmd_dump)

    # read
    p7 = sp.add_parser("read", help="read bytes and print hex")
    p7.add_argument("--addr", required=True)
    p7.add_argument("--size", required=True)
    p7.add_argument("--out", default=None)
    p7.set_defaults(run=cmd_read)

    # capture-hitbox
    p8 = sp.add_parser("capture-hitbox", help="capture hitbox block at addr and tag with ids")
    p8.add_argument("--addr", required=True)
    p8.add_argument("--size", default="0x80")
    p8.add_argument("--player", type=int, default=1)
    p8.add_argument("--char-id", required=True)
    p8.add_argument("--attack-id", required=True)
    p8.add_argument("--out", default=None)
    p8.set_defaults(run=cmd_capture_hitbox)

    # copy
    p9 = sp.add_parser("copy", help="copy a region of memory")
    p9.add_argument("--src", required=True, help="source address (hex, e.g. 0x908AFCE0)")
    p9.add_argument("--dst", required=True, help="destination address (hex, e.g. 0x908B5200)")
    p9.add_argument("--size", required=True, help="bytes to copy (hex, e.g. 0x50)")
    p9.set_defaults(run=cmd_copy)

    return ap

def main():
    ap = build_cli()
    args = ap.parse_args()
    args.run(args)

if __name__ == "__main__":
    main()
