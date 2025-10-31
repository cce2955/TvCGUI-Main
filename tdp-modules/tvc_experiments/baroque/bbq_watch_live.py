# tvc_experiments/bbq_watch_live.py
# Live baroque watcher: reads A/B (u16 BE) at indices 11-12 and 15-16 from fighter base.
# Computes red-life HP, percentages, rolling-median smoothing, BBQ snap flag, optional CSV log.

import time, argparse, csv, os, struct
from collections import deque
import dolphin_memory_engine as dme

IDX_A = 11  # decimal index from base
IDX_B = 15  # decimal index from base

def hook():
    while not dme.is_hooked():
        try: dme.hook()
        except: pass
        time.sleep(0.05)

def rbytes(addr, n):
    try:
        b = dme.read_bytes(addr, n)
        return b if b and len(b)==n else None
    except: return None

def read_u16_be(addr):
    b = rbytes(addr, 2)
    if not b: return None
    return struct.unpack(">H", b)[0]

def rolling_median(k):
    q = deque(maxlen=k)
    def push(x):
        q.append(x)
        s = sorted(q)
        n = len(s)
        if n==0: return float('nan')
        m = n//2
        return s[m] if n%2 else 0.5*(s[m-1]+s[m])
    return push

def main():
    ap = argparse.ArgumentParser(description="Live Baroque A/B watcher")
    ap.add_argument("--base", type=lambda x:int(x,0), default=0x9246B9E0, help="fighter base (default P1-C1)")
    ap.add_argument("--hpmax", type=float, required=True, help="character max HP (e.g. 44000)")
    ap.add_argument("--hz", type=float, default=30.0, help="poll rate")
    ap.add_argument("--smooth", type=int, default=9, help="rolling median window (odd,>=1)")
    ap.add_argument("--csv", type=str, default="", help="optional CSV output path")
    ap.add_argument("--clear", action="store_true", help="clear console on start")
    args = ap.parse_args()

    if args.clear:
        try: os.system("cls" if os.name=="nt" else "clear")
        except: pass

    hook()
    step_to_hp = args.hpmax/32.0
    med = rolling_median(max(1, args.smooth|1))

    addrA = args.base + IDX_A
    addrB = args.base + IDX_B

    writer = None
    if args.csv:
        new = not os.path.exists(args.csv)
        fh = open(args.csv, "a", newline="", encoding="utf-8")
        writer = csv.writer(fh)
        if new:
            writer.writerow(["t","A_u16","B_u16","red_hp","red_pct_hp","red_pct_B","d_steps_sm","bbq_snap"])

    prev_dhp = None
    prev_t   = None

    interval = 1.0 / max(1e-6, args.hz)
    print(f"[LIVE] base=0x{args.base:08X}  A(idx11-12)  B(idx15-16)  hpmax={args.hpmax}  @ {args.hz} Hz")

    try:
        while True:
            t0 = time.time()

            Au = read_u16_be(addrA)
            Bu = read_u16_be(addrB)
            if Au is None or Bu is None:
                print(f"{time.strftime('%H:%M:%S')}  read fail A={Au} B={Bu}")
                time.sleep(0.25); continue

            d_raw = (Bu - Au) & 0xFFFF
            d_sgn = (d_raw - 0x10000) if d_raw >= 0x8000 else d_raw
            d_hp  = d_sgn * step_to_hp

            # BBQ snap: rapid |Δ| drop across frames (only heuristic)
            bbq = 0
            if prev_dhp is not None and abs(prev_dhp) > 1e-3:
                if abs(d_hp) < 0.75*abs(prev_dhp):  # >25% drop
                    bbq = 1

            d_sm = med(d_sgn)

            pct_hp = (max(0.0, d_hp)/args.hpmax)*100.0
            pct_B  = (100.0*((Bu - Au)/Bu)) if Bu>0 else float('nan')

            print(f"{time.strftime('%H:%M:%S')}  "
                  f"A={Au:5d}  B={Bu:5d}  Δhp={d_hp:8.2f}  red%HP={pct_hp:6.3f}  red%B={(pct_B if pct_B==pct_B else float('nan')):6.3f}  "
                  f"smΔ={d_sm:7.2f}  BBQ?{bbq}")

            if writer:
                writer.writerow([
                    f"{t0:.6f}", Au, Bu,
                    f"{max(0.0,d_hp):.3f}", f"{max(0.0,pct_hp):.3f}",
                    f"{pct_B:.3f}" if pct_B==pct_B else "",
                    f"{d_sm:.3f}" if d_sm==d_sm else "",
                    bbq
                ])

            prev_dhp, prev_t = d_hp, t0
            dt = time.time()-t0
            if interval>dt:
                time.sleep(interval-dt)

    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
