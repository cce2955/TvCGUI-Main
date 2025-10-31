# tvc_experiments/bbq_trace_watch.py
# Always-on Baroque/red-life tracer for P1-C1.
# A = base+0x0B (u16), B = base+0x0F (u16)
# red_raw = (B - A) & 0xFFFF
# red_hp  = red_raw / 8.0
# Prints every tick; can also CSV-log.

import time, argparse, csv, os, struct
import dolphin_memory_engine as dme

def hook():
    while not dme.is_hooked():
        try:
            dme.hook()
        except Exception:
            pass
        time.sleep(0.1)

def rb(addr, n):
    try:
        b = dme.read_bytes(addr, n)
        if not b or len(b) != n: return None
        return b
    except Exception:
        return None

def ru16_be(addr):
    b = rb(addr, 2)
    return None if b is None else struct.unpack(">H", b)[0]

def ru32_be(addr):
    b = rb(addr, 4)
    return None if b is None else struct.unpack(">I", b)[0]

def rf32_be(addr):
    b = rb(addr, 4)
    if b is None: return None
    f = struct.unpack(">f", b)[0]
    return f if abs(f) < 1e38 else None

def main():
    ap = argparse.ArgumentParser("Always-on Baroque tracer")
    ap.add_argument("--base", type=lambda x:int(x,0), default=0x9246B9E0, help="fighter base (P1-C1)")
    ap.add_argument("--ofsA", type=lambda x:int(x,0), default=0x0B, help="offset of A (u16)")
    ap.add_argument("--ofsB", type=lambda x:int(x,0), default=0x0F, help="offset of B (u16)")
    ap.add_argument("--hp_addr", type=lambda x:int(x,0), default=None, help="optional current HP address (u32 or u16)")
    ap.add_argument("--hp_u16", action="store_true", help="interpret HP as u16 (default u32)")
    ap.add_argument("--hp_max", type=int, default=50000, help="Max HP for percent (Ryu 50000)")
    ap.add_argument("--flag_addr", type=lambda x:int(x,0), default=None, help="optional 'Baroque ready' flag addr (u8/u32 ok)")
    ap.add_argument("--hz", type=float, default=30.0, help="poll rate")
    ap.add_argument("--csv", type=str, default="", help="CSV path to append")
    ap.add_argument("--clear", action="store_true", help="clear CSV if exists")
    args = ap.parse_args()

    A_addr = args.base + args.ofsA
    B_addr = args.base + args.ofsB

    hook()
    print(f"[BBQ] hooked. base=0x{args.base:08X}  A=+0x{args.ofsA:X} (0x{A_addr:08X})  B=+0x{args.ofsB:X} (0x{B_addr:08X})  @ {args.hz} Hz")

    writer = None
    fh = None
    if args.csv:
        if args.clear and os.path.exists(args.csv):
            try: os.remove(args.csv)
            except Exception: pass
        fh = open(args.csv, "a", newline="", encoding="utf-8")
        writer = csv.writer(fh)
        if os.stat(args.csv).st_size == 0:
            writer.writerow(["t","A","B","delta_raw","red_hp","red_pct_of_max","hp","hp_pct","flag","A_addr","B_addr"])

    interval = 1.0 / max(1e-6, args.hz)
    try:
        while True:
            t0 = time.time()

            A = ru16_be(A_addr)
            B = ru16_be(B_addr)
            if A is None or B is None:
                print(f"{time.strftime('%H:%M:%S')}  A=---  B=---  (read fail)")
            else:
                # wrap-safe delta and 8:1 raw->HP
                d_raw = (B - A) & 0xFFFF
                red_hp = d_raw / 8.0
                red_pct = (red_hp / args.hp_max * 100.0) if args.hp_max > 0 else float("nan")

                hp = None
                hp_pct = float("nan")
                if args.hp_addr is not None:
                    if args.hp_u16:
                        hp = ru16_be(args.hp_addr)
                    else:
                        hp = ru32_be(args.hp_addr)
                    if isinstance(hp, int) and args.hp_max > 0:
                        hp_pct = max(0.0, min(100.0, hp / args.hp_max * 100.0))

                flag_val = None
                if args.flag_addr is not None:
                    # Try u32 then fall back to u8 for robustness
                    fv = ru32_be(args.flag_addr)
                    if fv is None:
                        b = rb(args.flag_addr, 1)
                        flag_val = None if b is None else b[0]
                    else:
                        flag_val = fv

                line = (f"{time.strftime('%H:%M:%S')}  "
                        f"A={A:5d}  B={B:5d}  Δraw={d_raw:5d}  "
                        f"redHP={red_hp:8.2f}  red%={red_pct:6.3f}  "
                        f"HP={(hp if hp is not None else '-----'):>5}  HP%={hp_pct:6.3f}  "
                        f"FLAG={(flag_val if flag_val is not None else '-'):>10}")
                print(line)

                if writer:
                    writer.writerow([
                        f"{t0:.6f}", A, B, d_raw, f"{red_hp:.3f}", f"{red_pct:.3f}",
                        (hp if hp is not None else ""), (f"{hp_pct:.3f}" if isinstance(hp_pct,float) else ""),
                        (flag_val if flag_val is not None else ""),
                        f"0x{A_addr:08X}", f"0x{B_addr:08X}",
                    ])

            dt = time.time() - t0
            if interval > dt:
                time.sleep(interval - dt)
    except KeyboardInterrupt:
        pass
    finally:
        if fh: fh.close()

if __name__ == "__main__":
    main()
