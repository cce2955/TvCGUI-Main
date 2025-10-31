# tvc_experiments/bbq_watch.py
# Monitors two fields you called out under fighter base @ 0x9246B9E0:
#   +0x34/0x36 (pair A) and +0x48/0x50 (pair B).
# Prints multiple interpretations and (optional) CSV log.

import time, struct, argparse, csv
import dolphin_memory_engine as dme

BASE = 0x9246B9E0
PAIR_A_OFS = (0x34, 0x36)
PAIR_B_OFS = (0x48, 0x50)

def hook():
    while not dme.is_hooked():
        try: dme.hook()
        except Exception: pass
        time.sleep(0.1)

def rbytes(addr, n):
    try:
        b = dme.read_bytes(addr, n)
        return b if b and len(b) == n else None
    except Exception:
        return None

def to_u16_be(b):  return struct.unpack(">H", b)[0]
def to_u32_be(b):  return struct.unpack(">I", b)[0]
def to_f32_be(b):  return struct.unpack(">f", b)[0]

def decode_all(addr):
    b2 = rbytes(addr, 2)
    b4 = rbytes(addr, 4)
    out = {"addr": addr, "ok2": b2 is not None, "ok4": b4 is not None}

    if b2:
        out["u16"] = to_u16_be(b2)
        out["hex2"] = " ".join(f"{x:02X}" for x in b2)
        # Show ASCII for the two bytes individually (mostly for quick sanity)
        out["asc2"] = "".join(chr(x) if 32 <= x <= 126 else "." for x in b2)

    if b4:
        out["u32"]  = to_u32_be(b4)
        try:
            f = to_f32_be(b4)
            out["f32"] = f if abs(f) < 1e38 else None
        except Exception:
            out["f32"] = None
        out["hex4"] = " ".join(f"{x:02X}" for x in b4)
        out["asc4"] = "".join(chr(x) if 32 <= x <= 126 else "." for x in b4)

    return out

def fmt_row(tag, d):
    h2  = d.get("hex2", "--")
    u16 = d.get("u16", "--")
    h4  = d.get("hex4", "--")
    u32 = d.get("u32", "--")
    f32 = d.get("f32", None)
    f32s = f"{f32:.6f}" if isinstance(f32, float) else "NaN"
    a2  = d.get("asc2", "--")
    a4  = d.get("asc4", "--")
    return (f"{tag} @{d['addr']:08X}  "
            f"2B[{h2}] u16={u16:<6} asc2='{a2}'  "
            f"4B[{h4}] u32={u32:<10} f32={f32s:<12} asc4='{a4}'")

def main():
    ap = argparse.ArgumentParser(description="Watch two BBQ-adjacent fields with multi-decoders.")
    ap.add_argument("--base", type=lambda x:int(x,0), default=BASE, help="fighter base (default 0x9246B9E0)")
    ap.add_argument("--hz", type=float, default=30.0, help="poll rate")
    ap.add_argument("--logcsv", type=str, default="", help="optional CSV path to append")
    args = ap.parse_args()

    hook()
    print(f"[BBQ] hooked. base=0x{args.base:08X}  A(+34/+36)  B(+48/+50)  @ {args.hz} Hz")
    addrA = args.base + PAIR_A_OFS[0]
    addrB = args.base + PAIR_B_OFS[0]

    writer = None
    if args.logcsv:
        newfile = False
        try:
            newfile = not os.path.exists(args.logcsv)
        except Exception:
            pass
        fh = open(args.logcsv, "a", newline="", encoding="utf-8")
        writer = csv.writer(fh)
        if newfile:
            writer.writerow(["t","addr","hex2","u16","hex4","u32","f32","ascii2","ascii4","tag"])

    interval = 1.0 / max(1e-6, args.hz)
    last_print = 0

    try:
        while True:
            t = time.time()

            da = decode_all(addrA)
            db = decode_all(addrB)

            # single-line, low-noise output (use your console's scrollback)
            print(f"{time.strftime('%H:%M:%S')}  "
                  f"{fmt_row('A', da)}  ||  {fmt_row('B', db)}")

            if writer:
                for tag, d in (("A", da), ("B", db)):
                    writer.writerow([
                        f"{t:.6f}", f"0x{d['addr']:08X}",
                        d.get("hex2",""), d.get("u16",""),
                        d.get("hex4",""), d.get("u32",""),
                        d.get("f32",""),   d.get("asc2",""), d.get("asc4",""),
                        tag,
                    ])

            # pacing
            sleep_left = interval - (time.time() - t)
            if sleep_left > 0:
                time.sleep(sleep_left)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
