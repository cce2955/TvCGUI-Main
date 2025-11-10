# tvc_experiments/bbq_watch.py
# Continuous BBQ watcher that targets row 0x...B9E0 indices 11,12,15,16
# (i.e., +0x0B, +0x0C, +0x0F, +0x10), prints every tick, and logs CSV.
# Also computes a red-life estimate two ways: mirror-delta and HP-based.

import time, argparse, csv, os, struct
import dolphin_memory_engine as dme

def hook():
    while not dme.is_hooked():
        try: dme.hook()
        except Exception: pass
        time.sleep(0.10)

def rbytes(addr, n):
    try:
        b = dme.read_bytes(addr, n)
        return b if b and len(b) == n else None
    except Exception:
        return None

def ru16(addr):
    b = rbytes(addr, 2)
    return struct.unpack(">H", b)[0] if b else None

def ru32(addr):
    b = rbytes(addr, 4)
    return struct.unpack(">I", b)[0] if b else None

def parse_hex(x): return int(x, 0)

def main():
    ap = argparse.ArgumentParser(description="Continuous BBQ monitor on B9E0 row indices 11,12,15,16.")
    ap.add_argument("--row", type=parse_hex, default=0x9246B9E0, help="row base (default 0x9246B9E0 for P1-C1+0x20)")
    ap.add_argument("--hz", type=float, default=30.0, help="poll rate")
    ap.add_argument("--clear", action="store_true", help="clear screen on start")
    ap.add_argument("--logcsv", type=str, default="", help="optional CSV path to append")
    # optional HP taps so we can compute red-life by HP deltas too (adjust if you know better taps)
    ap.add_argument("--hp_cur_addr", type=parse_hex, default=0x9246B9C0+0x10, help="u32 current HP addr (best-known tap)")
    ap.add_argument("--hp_max_addr", type=parse_hex, default=0x9246B9C0+0x14, help="u32 max HP addr (best-known tap)")
    args = ap.parse_args()

    # The four u16s we’re watching 
    A_hi_addr = args.row + 0x0B  # index 11
    A_lo_addr = args.row + 0x0C  # index 12
    B_hi_addr = args.row + 0x0F  # index 15
    B_lo_addr = args.row + 0x10  # index 16

    hook()
    if args.clear:
        os.system("cls" if os.name == "nt" else "clear")

    interval = 1.0 / max(1e-6, args.hz)

    # CSV setup
    writer = None
    if args.logcsv:
        os.makedirs(os.path.dirname(args.logcsv), exist_ok=True)
        newfile = not os.path.exists(args.logcsv)
        f = open(args.logcsv, "a", newline="", encoding="utf-8")
        writer = csv.writer(f)
        if newfile:
            writer.writerow([
                "t","A_u16","B_u16","A_raw_hi","A_raw_lo","B_raw_hi","B_raw_lo",
                "mirror_delta","hp_cur","hp_max","hp_missing","red_est_hp"
            ])

    # sticky values so “0” doesn’t flash when the mirror idles
    last_nonzero_A = 0
    last_nonzero_B = 0
    last_nonzero_delta = 0
    last_hp_cur = None
    last_hp_max = None

    print("time       A     B   Δ(B-A) |  HPcur/HPmax  | red_est_hp | notes")
    try:
        while True:
            t0 = time.time()

            A_hi = ru16(A_hi_addr)
            A_lo = ru16(A_lo_addr)
            B_hi = ru16(B_hi_addr)
            B_lo = ru16(B_lo_addr)

            # Treat each pair as a single u16 per your findings (A and B are per-index u16 “mirrors”)
            # We’ll read the HI slots (idx 11 and 15) as the primary values; LO slots printed for context.
            A = A_hi if A_hi is not None else 0
            B = B_hi if B_hi is not None else 0

            # sticky hold (avoid flicker to zero if mirror stalls)
            if A: last_nonzero_A = A
            if B: last_nonzero_B = B
            d = (B - A) & 0xFFFF  # raw mirror delta as u16 space
            if d: last_nonzero_delta = d

            # HP readout for a second red-life estimate
            hp_cur = ru32(args.hp_cur_addr)
            hp_max = ru32(args.hp_max_addr)
            red_est_hp = None
            if isinstance(hp_cur, int) and isinstance(hp_max, int) and hp_max > 0:
                hp_missing = max(0, hp_max - hp_cur)
                # Many TvC builds use raw HP with different scaling; this stays raw as a comparable gauge.
                # If you later confirm scaling (e.g., /256), apply it in HUD, not here.
                red_est_hp = hp_missing
            else:
                hp_missing = None

            # format line
            ts = time.strftime("%H:%M:%S")
            a_disp = A if A else last_nonzero_A
            b_disp = B if B else last_nonzero_B
            d_disp = d if d else last_nonzero_delta

            hp_part = f"{hp_cur if isinstance(hp_cur,int) else 'NaN':>6}/{hp_max if isinstance(hp_max,int) else 'NaN':<6}"
            redhp   = f"{red_est_hp if isinstance(red_est_hp,int) else 'NaN':>10}"

            notes = []
            if (A_lo is not None and B_lo is not None) and (A_lo != 0x8000 or B_lo != 0x8000):
                notes.append(f"LO:{A_lo:5d}/{B_lo:5d}")
            if (A and B) and (A == B):
                notes.append("A==B")
            elif d_disp:
                notes.append("Δ>0")
            line = f"{ts}  {a_disp:5d} {b_disp:5d} {d_disp:6d} | {hp_part} | {redhp} | " + " ".join(notes)

            print(line)

            if writer:
                writer.writerow([
                    f"{t0:.6f}",
                    A if A is not None else "",
                    B if B is not None else "",
                    A_hi if A_hi is not None else "",
                    A_lo if A_lo is not None else "",
                    B_hi if B_hi is not None else "",
                    B_lo if B_lo is not None else "",
                    d,
                    hp_cur if hp_cur is not None else "",
                    hp_max if hp_max is not None else "",
                    (hp_missing if hp_missing is not None else ""),
                    (red_est_hp if red_est_hp is not None else "")
                ])

            # pacing
            dt = time.time() - t0
            remain = interval - dt
            if remain > 0:
                time.sleep(remain)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
