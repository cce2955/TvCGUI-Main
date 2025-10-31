# tvc_experiments/p1c1_baroque_watch.py
import time, os, csv, argparse, struct, math
import dolphin_memory_engine as dme

BASE = 0x9246B9E0
IDX_A = 11
IDX_B = 15

def hook():
    while not dme.is_hooked():
        try: dme.hook()
        except Exception: pass
        time.sleep(0.10)

def rb(addr, n):
    try:
        b = dme.read_bytes(addr, n)
        return b if b and len(b)==n else None
    except Exception:
        return None

def ru16(addr):
    b = rb(addr, 2)
    return (struct.unpack(">H", b)[0], b) if b else (None, b)

def safe_pct(num, den):
    if not den or den == 0: return float("nan")
    return (num / den) * 100.0

def main():
    ap = argparse.ArgumentParser(description="Full Baroque mirror logger with byte view + wrap detection.")
    ap.add_argument("--base", type=lambda x:int(x,0), default=BASE)
    ap.add_argument("--hz", type=float, default=30.0)
    ap.add_argument("--hpmax", type=int, default=50000)
    ap.add_argument("--scale", type=float, default=8.0)
    ap.add_argument("--logcsv", type=str, default="tvc_experiments/p1c1_baroque_trace.csv")
    ap.add_argument("--clear", action="store_true")
    args = ap.parse_args()

    if args.clear:
        os.system("cls" if os.name=="nt" else "clear")

    hook()
    addrA = args.base + IDX_A
    addrB = args.base + IDX_B
    os.makedirs(os.path.dirname(args.logcsv), exist_ok=True)

    with open(args.logcsv,"w",newline="",encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "t","A_u16","A_hex","B_u16","B_hex",
            "Δraw","Δsigned","Δhp","red%HP","red%B","wrap?"
        ])

        print(f"[trace] writing → {args.logcsv}")
        prevA = prevB = None
        while True:
            t0 = time.time()
            A, Ab = ru16(addrA)
            B, Bb = ru16(addrB)

            if A is None or B is None:
                print(f"{time.strftime('%H:%M:%S')}  read fail")
                time.sleep(1/args.hz)
                continue

            delta_raw = (B - A) & 0xFFFF
            delta_s   = ((A - B + 0x8000) & 0xFFFF) - 0x8000
            delta_hp  = delta_raw / args.scale
            red_hp    = safe_pct(delta_hp, args.hpmax)
            red_B     = safe_pct(delta_hp, B/args.scale)

            wrap = ""
            if prevA is not None:
                if abs(A - prevA) > 60000 or abs(B - prevB) > 60000:
                    wrap = "WRAP"
            prevA, prevB = A, B

            print(f"{time.strftime('%H:%M:%S')}  A={A:5d}  B={B:5d}  Δraw={delta_raw:5d}  "
                  f"Δs={delta_s:6d}  Δhp={delta_hp:8.2f}  red%={red_hp:7.3f}  {wrap}")

            w.writerow([
                f"{t0:.6f}", A, " ".join(f"{x:02X}" for x in (Ab or b"")),
                B, " ".join(f"{x:02X}" for x in (Bb or b"")),
                delta_raw, delta_s, f"{delta_hp:.3f}",
                f"{red_hp:.3f}", f"{red_B:.3f}", wrap
            ])
            fh.flush()
            rem = (1/args.hz) - (time.time()-t0)
            if rem>0: time.sleep(rem)

if __name__ == "__main__":
    main()
