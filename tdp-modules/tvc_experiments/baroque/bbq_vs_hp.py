# tvc_experiments/bbq_vs_hp.py
# Compare Baroque pool (two u16 mirrors) vs HP pool every tick.
# - Baroque mirrors: byte indices 11–12 (base+0x0B) and 15–16 (base+0x0F)
# - HP: max @ base+0x24 (u32), current @ base+0x28 (u32)
# - Red life (classic Baroque pool): base+0x2C (u32 word in this build)
#
# Prints a clear-screen mini HUD and can also CSV-log every sample.

import os, time, struct, argparse, csv
import dolphin_memory_engine as dme

DEFAULT_BASE = 0x9246B9E0

IDX_A = 11  # base + 0x0B
IDX_B = 15  # base + 0x0F

def hook():
    while not dme.is_hooked():
        try:
            dme.hook()
        except Exception:
            pass
        time.sleep(0.1)

def rbytes(addr, n):
    try:
        b = dme.read_bytes(addr, n)
        return b if b and len(b) == n else None
    except Exception:
        return None

def u16_be(b): return struct.unpack(">H", b)[0]
def u32_be(b): return struct.unpack(">I", b)[0]

def read_u16(addr):
    b = rbytes(addr, 2)
    return u16_be(b) if b else None

def read_u32(addr):
    b = rbytes(addr, 4)
    return u32_be(b) if b else None

def pct(n, d):
    if n is None or d is None or d == 0: return None
    return (float(n) / float(d)) * 100.0

def fmtv(v):
    return "NaN" if v is None else str(v)

def fmtf(v, places=2):
    return "NaN" if v is None else f"{v:.{places}f}"

def line():
    print("-" * 88)

def main():
    ap = argparse.ArgumentParser(description="Baroque pool vs HP watcher (clear-screen HUD + optional CSV).")
    ap.add_argument("--base", type=lambda x:int(x,0), default=DEFAULT_BASE, help="fighter base (default 0x9246B9E0)")
    ap.add_argument("--hz", type=float, default=10.0, help="refresh rate")
    ap.add_argument("--logcsv", type=str, default="", help="append CSV log (path)")
    args = ap.parse_args()

    hook()

    # Precompute addresses
    base = args.base
    addr_bar_a = base + 0x0B  # bytes 11–12
    addr_bar_b = base + 0x0F  # bytes 15–16
    addr_hp_max = base + 0x24
    addr_hp_cur = base + 0x28
    addr_red    = base + 0x2C  # classic red-life pool (word)

    writer = None
    if args.logcsv:
        os.makedirs(os.path.dirname(args.logcsv), exist_ok=True)
        newfile = not os.path.exists(args.logcsv)
        fh = open(args.logcsv, "a", newline="", encoding="utf-8")
        writer = csv.writer(fh)
        if newfile:
            writer.writerow([
                "time","base",
                "bar_a(u16)","bar_b(u16)","bar_sum(u32)",
                "hp_cur","hp_max","hp_pct",
                "red_life(u32)","red_pct","bar_sum_pct_of_max","bar_eq_red"
            ])

    interval = 1.0 / max(1e-6, args.hz)

    try:
        while True:
            t0 = time.time()
            ts = time.strftime("%H:%M:%S")

            # Reads
            bar_a = read_u16(addr_bar_a)
            bar_b = read_u16(addr_bar_b)
            bar_sum = None if (bar_a is None or bar_b is None) else (bar_a + bar_b)

            hp_max = read_u32(addr_hp_max)
            hp_cur = read_u32(addr_hp_cur)
            hp_pct = pct(hp_cur, hp_max)

            red = read_u32(addr_red)  # classic “red life” (recoverable)
            red_pct = pct(red, hp_max)

            # Compare: if bar_sum mirrors red-life after scaling, show equality test too
            # (Some builds mirror in smaller units; you’ll see equality or a stable scale factor.)
            bar_pct = pct(bar_sum, hp_max) if (bar_sum is not None) else None
            bar_eq_red = (bar_sum == red) if (bar_sum is not None and red is not None) else None

            # Screen
            os.system("cls" if os.name == "nt" else "clear")
            print(f"[BBQ vs HP] {ts}  base=0x{base:08X}  watch idx (11–12 @ +0x0B) & (15–16 @ +0x0F)  @ {args.hz} Hz")
            line()
            print(f"HP:     cur={fmtv(hp_cur):>7}   max={fmtv(hp_max):>7}   HP%={fmtf(hp_pct,1):>6}")
            print(f"RedLife (base+0x2C): {fmtv(red):>7}    Red% of Max={fmtf(red_pct,2):>6}")
            line()
            print("Baroque mirrors (u16 each):")
            print(f"  A (idx 11–12, addr 0x{addr_bar_a:08X}) = {fmtv(bar_a):>6}")
            print(f"  B (idx 15–16, addr 0x{addr_bar_b:08X}) = {fmtv(bar_b):>6}")
            print(f"  Sum                         (u32)      = {fmtv(bar_sum):>6}    Sum% of Max={fmtf(bar_pct,2):>6}")
            print(f"  Sum == RedLife? {bar_eq_red}")
            line()
            print("Notes:")
            print(" - If Sum% tracks Red% closely, these mirrors are directly proportional to red life (Baroque pool).")
            print(" - If Sum != RedLife but scales by a constant k, k = (bar_sum / red).")

            # CSV
            if writer:
                writer.writerow([
                    f"{t0:.6f}", f"0x{base:08X}",
                    "" if bar_a is None else bar_a,
                    "" if bar_b is None else bar_b,
                    "" if bar_sum is None else bar_sum,
                    "" if hp_cur is None else hp_cur,
                    "" if hp_max is None else hp_max,
                    "" if hp_pct is None else f"{hp_pct:.3f}",
                    "" if red is None else red,
                    "" if red_pct is None else f"{red_pct:.3f}",
                    "" if bar_pct is None else f"{bar_pct:.3f}",
                    "" if bar_eq_red is None else int(bar_eq_red),
                ])

            # pace
            dt = time.time() - t0
            if (sl := interval - dt) > 0:
                time.sleep(sl)

    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
