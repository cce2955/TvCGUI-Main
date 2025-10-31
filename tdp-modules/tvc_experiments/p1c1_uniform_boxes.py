# tvc_experiments/p1c1_uniform_boxes.py
# Uniformize all hitboxes found under P1-C1's fighter base by scanning for a 16-byte signature.
# Signature (with wildcards at X1/X2 nibs):
#   FF FF FF FE 35 0D 20 3F 00 00 00 ?? 00 00 00 3F
#
# Usage examples:
#   # Preview (no writes), auto-resolve P1-C1 base from manager, scan 0x6000 bytes:
#   python p1c1_uniform_boxes.py --scan 0x6000
#
#   # Actually write: set X1 and X2 to 0x0100 (Q8.8 = +1.0), keep centers at 0:
#   python p1c1_uniform_boxes.py --scan 0x8000 --uniform 0x0100 --write
#
#   # If you already know fighter base:
#   python p1c1_uniform_boxes.py --base 0x9246B9C0 --scan 0x6000 --write
#
# Revert:
#   python p1c1_uniform_boxes.py --revert backup_p1c1_uniform_boxes.bin

import argparse, time, os, struct, json
import dolphin_memory_engine as dme

P1C1_MGR = 0x803C9FCC  # provided
# We don't hardcode the inner offsets; allow a flexible chain:
DEFAULT_CHAIN = []     # e.g., ["0x0","0x14"] if needed; empty means mgr itself points at base.

SIG = bytes([
    0xFF,0xFF,0xFF,0xFE, 0x35,0x0D,0x20,0x3F,
    0x00,0x00,0x00,0x00, 0x00,0x00,0x00,0x3F
])
# Wildcards at byte index 11 (X1 low?) and index 12 (X2 high?) per your note.
WILDCARD_IDXS = {11, 12}

def hook():
    while not dme.is_hooked():
        try: dme.hook()
        except Exception:
            pass
        time.sleep(0.05)

def read_u32(a):
    b=dme.read_bytes(a,4)
    if not b: return 0
    return struct.unpack(">I", b)[0]

def resolve_base(mgr=P1C1_MGR, chain_hex=None):
    ptr = read_u32(mgr)
    if not chain_hex:
        return ptr
    cur = ptr
    for ofs_s in chain_hex:
        ofs = int(ofs_s, 0)
        cur = read_u32(cur + ofs)
    return cur

def scan_matches(start, size):
    end = start + size
    step = 1
    sig = SIG
    matches = []
    # precompute which bytes to compare
    cmp_idx = [i for i in range(len(sig)) if i not in WILDCARD_IDXS]
    pos = start
    chunk = 0x2000
    while pos < end:
        read_len = min(chunk, end - pos)
        buf = dme.read_bytes(pos, read_len)
        if not buf:
            pos += read_len
            continue
        # naive scan
        blen = len(buf)
        i = 0
        while i + 16 <= blen:
            piece = buf[i:i+16]
            ok = True
            for j in cmp_idx:
                if piece[j] != sig[j]:
                    ok = False
                    break
            if ok:
                matches.append(pos + i)
                i += 16  # skip forward to avoid refinding same header
            else:
                i += step
        pos += read_len
    return matches

def make_backup(back_path, writes):
    # writes = [(addr, orig_bytes, new_bytes), ...]
    meta = {
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(writes),
        "items": [{"addr": hex(a), "len": len(ob)} for (a,ob,nb) in writes]
    }
    with open(back_path, "wb") as fh:
        for (addr, orig, new) in writes:
            fh.write(struct.pack(">I", addr))
            fh.write(struct.pack(">H", len(orig)))
            fh.write(orig)
    with open(back_path + ".json", "w", encoding="utf-8") as jf:
        json.dump(meta, jf, indent=2)
    return back_path

def revert_backup(back_path):
    data = open(back_path, "rb").read()
    off = 0
    restored = 0
    while off + 6 <= len(data):
        addr = struct.unpack(">I", data[off:off+4])[0]; off+=4
        ln   = struct.unpack(">H", data[off:off+2])[0]; off+=2
        blob = data[off:off+ln]; off+=ln
        dme.write_bytes(addr, blob)
        restored += 1
    return restored

def main():
    ap = argparse.ArgumentParser(description="Uniformize P1-C1 hitboxes by signature scan under fighter base.")
    ap.add_argument("--mgr", type=lambda x:int(x,0), default=P1C1_MGR, help="P1-C1 manager addr")
    ap.add_argument("--chain", type=str, default=",".join(DEFAULT_CHAIN), help="comma-separated pointer offsets (hex) to follow from mgr to base")
    ap.add_argument("--base", type=lambda x:int(x,0), help="override fighter base (skip manager resolution)")
    ap.add_argument("--scan", type=lambda x:int(x,0), default=0x6000, help="bytes to scan from base")
    ap.add_argument("--uniform", type=lambda x:int(x,0), default=0x0100, help="Q8.8 value to write into the two size/loc bytes (X1/X2)")
    ap.add_argument("--write", action="store_true", help="apply writes (default is dry-run)")
    ap.add_argument("--backup", type=str, default="backup_p1c1_uniform_boxes.bin", help="backup path")
    ap.add_argument("--revert", type=str, help="revert from a backup file")
    args = ap.parse_args()

    hook()

    if args.revert:
        restored = revert_backup(args.revert)
        print(f"[revert] restored {restored} entries from {args.revert}")
        return

    if args.base is None:
        chain = [s for s in args.chain.split(",") if s.strip()] if args.chain else []
        base = resolve_base(args.mgr, chain)
    else:
        base = args.base

    if base == 0 or base is None:
        print("[err] failed to resolve fighter base")
        return

    print(f"[info] base=0x{base:08X}, scanning {args.scan:#x} bytes")

    matches = scan_matches(base, args.scan)
    if not matches:
        print("[info] no signature matches found")
        return

    print(f"[info] found {len(matches)} candidate hitbox headers")
    # For each 16-byte header, patch wildcard bytes [11] and [12] to uniform value.
    # We'll treat them as unsigned bytes of Q8.8 high/low halves; in practice, you reported they encode size/location.
    # We'll write the low byte of 'uniform' to index 11, and the high byte to index 12 (and report both).
    u = args.uniform & 0xFFFF
    u_hi = (u >> 8) & 0xFF
    u_lo = u & 0xFF

    writes = []
    for hdr in matches:
        orig = dme.read_bytes(hdr, 16)
        if not orig or len(orig) != 16:
            continue
        newb = bytearray(orig)
        # write low @11, high @12 — if this ends up swapped, you’ll see it immediately and can flip below.
        newb[11] = u_lo
        newb[12] = u_hi
        if args.write:
            dme.write_bytes(hdr, bytes(newb))
        writes.append((hdr, bytes(orig), bytes(newb)))
        print(f"  [hitbox] 0x{hdr:08X}: "
              f"{orig.hex(' ')}  ->  "
              f"{bytes(newb).hex(' ')}  | set X1/X2={u:#06x} (lo={u_lo:#04x}, hi={u_hi:#04x})")

    if args.write and writes:
        back_path = make_backup(args.backup, writes)
        print(f"[write] patched {len(writes)} headers; backup -> {back_path} (+ .json)")

    print("[done]")

if __name__ == "__main__":
    main()
