# tvc_experiments/hitbox_scan.py
# Scan Dolphin RAM for a hitbox signature and decode nearby fields.
# Assumes Wii big-endian floats in RAM.

import time, struct, argparse
import dolphin_memory_engine as dme

# Search window (MEM2 range where you've been working: 0x9246xxxx etc.)
SEARCH_START = 0x80000000
SEARCH_END   = 0x99000000
CHUNK = 0x4000  # 16 KiB per read

# Signature with mask:
# Note claims: FF FF FF FE 35 0D 20 3F 00 00 00 X1 00 00 00 3F
# In RAM (big-endian), expect 3F **on the left** of the 4-byte float.
# We'll look for:
#   FF FF FF FE 35 0D 20 3F  3F 00 00 00  ??  3F 00 00 00
# Where '??' is a wildcard byte (X1).
SIG = bytes.fromhex("FF FF FF FE 35 0D 20 3F")
# followed by float 0.5 (big-endian) 3F 00 00 00, then 1 wildcard byte, then another 0.5
TAIL = bytes.fromhex("3F 00 00 00")
# We'll implement a flexible matcher: header, then be-float, then 1 wildcard, then be-float

def hook():
    while not dme.is_hooked():
        try: dme.hook()
        except Exception:
            pass
        time.sleep(0.1)

def find_matches(buf):
    hits = []
    n = len(buf)
    i = 0
    # We’re seeking: SIG + 4 bytes(be-float) + 1 wildcard + 4 bytes(be-float)
    # But the note only guaranteed specific bytes at two places.
    # We’ll require the header, then 3F 00 00 00, then any one byte, then 3F 00 00 00.
    while True:
        i = buf.find(SIG, i)
        if i < 0:
            break
        j = i + len(SIG)
        if j + 4 + 1 + 4 <= n:
            be_half1 = buf[j:j+4]
            wild     = buf[j+4]
            be_half2 = buf[j+5:j+9]
            if be_half1 == TAIL and be_half2 == TAIL:
                hits.append((i, wild))
        i += 1
    return hits

def be_f32(b): return struct.unpack(">f", b)[0]
def be_u32(b): return struct.unpack(">I", b)[0]

def main():
    ap = argparse.ArgumentParser(description="Scan RAM for TvC hitbox signature.")
    ap.add_argument("--start", type=lambda x:int(x,0), default=SEARCH_START)
    ap.add_argument("--end",   type=lambda x:int(x,0), default=SEARCH_END)
    ap.add_argument("--context", type=int, default=0x40, help="bytes of context to decode around match")
    args = ap.parse_args()

    hook()
    print(f"[hitbox-scan] hooked. scanning 0x{args.start:08X}..0x{args.end:08X}")

    base = args.start
    total = 0
    while base < args.end:
        size = min(CHUNK, args.end - base)
        try:
            buf = dme.read_bytes(base, size)
        except Exception:
            buf = None
        if not buf or len(buf) != size:
            base += size
            continue

        hits = find_matches(buf)
        for off, wild in hits:
            addr = base + off
            print(f"\n== MATCH @ 0x{addr:08X}  (wild=0x{wild:02X}) ==")

            # Dump nearby 32-bit big-endian numbers every 4 bytes for quick eyeballing
            ctx_start = max(0, off - args.context)
            ctx_end   = min(len(buf), off + args.context)
            ctx_addr0 = base + ctx_start
            ctx = buf[ctx_start:ctx_end]

            # Pretty hex dump aligned on 4-byte boundaries
            for k in range(0, len(ctx), 16):
                line = ctx[k:k+16]
                addr_line = ctx_addr0 + k
                hexs = " ".join(f"{b:02X}" for b in line)
                print(f"  {addr_line:08X}: {hexs}")

            # Try to decode the two floats immediately after SIG as sanity
            j = off + len(SIG)
            try:
                f1 = be_f32(buf[j:j+4])
                f2 = be_f32(buf[j+5:j+9])
                print(f"  decoded f1={f1:.6f}  f2={f2:.6f}  (expect ~0.5 if this is the right form)")
            except Exception:
                pass

            # Also decode a few be32s around
            for rel in (-8, -4, 0, +4, +8, +12, +16, +20):
                p = off + rel
                if 0 <= p <= len(buf)-4:
                    raw = buf[p:p+4]
                    u32 = be_u32(raw)
                    f32 = be_f32(raw)
                    print(f"    @rel {rel:+3d}  be32=0x{u32:08X}  f32={f32:.6f}")

        total += len(hits)
        base += size

    print(f"\n[hitbox-scan] done. matches: {total}")

if __name__ == "__main__":
    main()
