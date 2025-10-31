# tvc_experiments/bbq_pool_probe.py
# Estimates Baroque red-life pool using biased u16 mirrors at base+0x0B / +0x0F.
# Learns a scale factor K on Baroque edges to convert raw -> HP units.

import time, struct, argparse, statistics
import dolphin_memory_engine as dme

DEFAULT_BASE = 0x9246B9E0

OFF_A      = 0x0B  # idx 11–12
OFF_B      = 0x0F  # idx 15–16
OFF_HP_MAX = 0x24  # u32
OFF_HP_CUR = 0x28  # u32
OFF_RED    = 0x2C  # u32 (optional cross-check)
OFF_BAR    = 0xCBA8  # two-byte “baroque ready” window for edge detection

BIAS = 0x8000

def hook():
    while not dme.is_hooked():
        try: dme.hook()
        except Exception: pass
        time.sleep(0.1)

def rb(addr, n):
    try:
        b = dme.read_bytes(addr, n)
        return b if b and len(b) == n else None
    except Exception:
        return None

def rd8(a):
    b = rb(a,1);  return b[0] if b else None

def ru16(a):
    b = rb(a,2);  return struct.unpack(">H", b)[0] if b else None

def ru32(a):
    b = rb(a,4);  return struct.unpack(">I", b)[0] if b else None

def signed_from_biased(u):
    if u is None: return None
    return int(u) - BIAS

def pct(n,d):
    if not n or not d: return None
    return 100.0 * n / d

def main():
    ap = argparse.ArgumentParser(description="Baroque pool probe with auto scale factor")
    ap.add_argument("--base", type=lambda x:int(x,0), default=DEFAULT_BASE)
    ap.add_argument("--hz", type=float, default=60.0)
    args = ap.parse_args()

    hook()
    base = args.base
    addrA   = base + OFF_A
    addrB   = base + OFF_B
    addrMAX = base + OFF_HP_MAX
    addrCUR = base + OFF_HP_CUR
    addrRED = base + OFF_RED
    addrBAR = base + OFF_BAR

    # Edge trackers
    prev_bar0 = rd8(addrBAR) or 0
    prev_bar1 = rd8(addrBAR+1) or 0

    # Scale history (we’ll median this)
    scales = []

    interval = max(1.0 / max(args.hz, 1e-6), 0.001)
    print(f"[BBQ] base=0x{base:08X} A=+0x0B B=+0x0F | HPmax @+0x24, HPcur @+0x28")

    try:
        while True:
            t0 = time.time()

            A_u16 = ru16(addrA)
            B_u16 = ru16(addrB)
            sa = signed_from_biased(A_u16)
            sb = signed_from_biased(B_u16)
            red_raw = None
            if sa is not None and sb is not None:
                red_raw = max(sb - sa, 0)

            hp_max = ru32(addrMAX)
            hp_cur = ru32(addrCUR)
            red_u32 = ru32(addrRED)

            # Current best K
            K = None
            if scales:
                try:    K = statistics.median(scales)
                except: K = None

            red_hp = (red_raw * K) if (red_raw is not None and K) else None
            red_pct = pct(red_hp, hp_max) if (red_hp and hp_max) else None

            # Print line
            def fmt(v, fmtstr="{:d}"):
                return fmtstr.format(v) if isinstance(v, int) else ("{:.3f}".format(v) if isinstance(v, float) else "NaN")

            print(
                f"{time.strftime('%H:%M:%S')}  "
                f"A={A_u16 or 0:5d} (sa={sa if sa is not None else 0:+6d})  "
                f"B={B_u16 or 0:5d} (sb={sb if sb is not None else 0:+6d})  "
                f"rawΔ={fmt(red_raw)}  "
                f"HP {fmt(hp_cur)}/{fmt(hp_max)}  "
                f"K={fmt(K,'{:.6f}') if K else 'NaN':>10}  "
                f"red≈{fmt(red_hp,'{:.1f}') if red_hp is not None else 'NaN':>8}  "
                f"({fmt(red_pct,'{:.2f}')+'%' if red_pct is not None else 'NaN':>7})  "
                f"[RED u32={fmt(red_u32) if red_u32 is not None else 'NaN'}]"
            )

            # Detect Baroque window change (consume or spawn pool) to calibrate K
            bar0 = rd8(addrBAR) or 0
            bar1 = rd8(addrBAR+1) or 0
            if (bar0,bar1) != (prev_bar0, prev_bar1):
                # Sample pre/post quickly around the edge to estimate ΔHP and Δraw
                pre_hp = hp_cur
                pre_sa, pre_sb = sa, sb
                time.sleep(0.050)
                post_hp = ru32(addrCUR)
                A2 = ru16(addrA); B2 = ru16(addrB)
                sa2 = signed_from_biased(A2)
                sb2 = signed_from_biased(B2)

                d_hp = None
                d_raw = None
                if (pre_hp is not None) and (post_hp is not None):
                    d_hp = abs(post_hp - pre_hp)
                if (pre_sa is not None) and (pre_sb is not None) and (sa2 is not None) and (sb2 is not None):
                    pre_raw = max(pre_sb - pre_sa, 0)
                    post_raw = max(sb2 - sa2, 0)
                    d_raw = abs(post_raw - pre_raw)

                if d_hp and d_raw:
                    k_est = d_hp / d_raw if d_raw != 0 else None
                    if k_est and 0 < k_est < 1e6:
                        scales.append(k_est)
                        if len(scales) > 25:
                            scales.pop(0)
                        print(f"[EDGE] BAROQUE_BYTES {prev_bar0:02X},{prev_bar1:02X} -> {bar0:02X},{bar1:02X} | "
                              f"ΔHP={d_hp}  Δraw={d_raw}  K_est={k_est:.6f}  K_med={statistics.median(scales):.6f}",
                              flush=True)
                prev_bar0, prev_bar1 = bar0, bar1

            # pace
            dt = time.time() - t0
            if interval > dt:
                time.sleep(interval - dt)

    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
