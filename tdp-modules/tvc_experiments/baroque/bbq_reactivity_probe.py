# tvc_experiments/bbq_reactivity_probe.py
# Refactor: hex-friendly args, CSV logging, safe dir creation, clearer output.
# Focus: P1-C1 base defaults to 0x9246B9E0, A at ofsA, B at ofsB (u16 BE), HP at hpofs.

import argparse, csv, os, time, struct, sys
import dolphin_memory_engine as dme

def parse_int(x: str) -> int:
    return int(x, 0)

def hook():
    while not dme.is_hooked():
        try:
            dme.hook()
        except Exception:
            pass
        time.sleep(0.05)

def rb(addr: int, n: int) -> bytes | None:
    try:
        b = dme.read_bytes(addr, n)
        return b if b and len(b) == n else None
    except Exception:
        return None

def ru16_be(addr: int) -> int | None:
    b = rb(addr, 2)
    return struct.unpack(">H", b)[0] if b else None

def wu16_be(addr: int, val: int) -> bool:
    v = val & 0xFFFF
    try:
        dme.write_bytes(addr, struct.pack(">H", v))
        return True
    except Exception:
        return False

def ensure_csv(path: str):
    if not path:
        return None, None
    pdir = os.path.dirname(path)
    if pdir and not os.path.exists(pdir):
        os.makedirs(pdir, exist_ok=True)
    newfile = not os.path.exists(path)
    fh = open(path, "a", newline="", encoding="utf-8")
    wr = csv.writer(fh)
    if newfile:
        wr.writerow([
            "t_epoch","time","base_hex","ofsA_hex","ofsB_hex","hpofs_hex",
            "addrA","addrB","addrHP",
            "A","B","HP","d_raw","d_signed","delta_applied","poke_target"
        ])
    return fh, wr

def fmt_time():
    return time.strftime("%H:%M:%S")

def main():
    ap = argparse.ArgumentParser(description="Probe Baroque-adjacent A/B u16s and their reactivity; optional poke + CSV log.")
    ap.add_argument("--base",  type=parse_int, default=0x9246B9E0, help="fighter base (e.g. 0x9246B9E0)")
    ap.add_argument("--ofsA",  type=parse_int, default=0x0B,      help="byte index for A (u16 BE) from base")
    ap.add_argument("--ofsB",  type=parse_int, default=0x0F,      help="byte index for B (u16 BE) from base")
    ap.add_argument("--hpofs", type=parse_int, default=0x0E,      help="byte index for HP (u16 BE) from base (default 0x0E => base+0x0E)")
    ap.add_argument("--hz",    type=float,     default=15.0,      help="poll rate")
    ap.add_argument("--poke",  choices=["none","A","B","both"], default="none", help="actively poke A/B by delta once at start")
    ap.add_argument("--delta", type=parse_int, default=0x10,      help="u16 delta to add/sub when poking (hex or dec)")
    ap.add_argument("--csv",   type=str,       default="",         help="optional CSV path to append (dirs auto-created)")
    ap.add_argument("--once",  action="store_true",               help="run one poll iteration (after optional poke) then exit")
    ap.add_argument("--quiet", action="store_true",               help="suppress stdout (CSV only)")
    ap.add_argument("--maxhp", type=parse_int, default=50000,     help="nominal full HP for scaling (informational only)")
    args = ap.parse_args()

    base  = args.base
    addrA = base + args.ofsA
    addrB = base + args.ofsB
    addrH = base + args.hpofs

    hook()

    fh, wr = ensure_csv(args.csv)

    def emit(a, b, hp, d_raw, d_signed, delta_applied="", poke_target=""):
        ts = time.time()
        tstr = fmt_time()
        if not args.quiet:
            # Human line
            line = (f"{tstr}  A={a if a is not None else 'NaN':>5}  "
                    f"B={b if b is not None else 'NaN':>5}  "
                    f"Δraw={d_raw if d_raw is not None else 'NaN':>6}  "
                    f"Δs={d_signed if d_signed is not None else 'NaN':>6}  "
                    f"HP={hp if hp is not None else 'NaN'}  "
                    f"[poke={poke_target or '-'} Δ={delta_applied or '-'}]")
            print(line, flush=True)
        if wr:
            wr.writerow([
                f"{ts:.6f}", tstr,
                f"0x{base:08X}", f"0x{args.ofsA:X}", f"0x{args.ofsB:X}", f"0x{args.hpofs:X}",
                f"0x{addrA:08X}", f"0x{addrB:08X}", f"0x{addrH:08X}",
                a if a is not None else "", b if b is not None else "", hp if hp is not None else "",
                d_raw if d_raw is not None else "", d_signed if d_signed is not None else "",
                delta_applied, poke_target
            ])

    # One optional poke at start
    if args.poke != "none":
        a0 = ru16_be(addrA)
        b0 = ru16_be(addrB)
        hp0 = ru16_be(addrH)
        # Signed delta convenience for display (engine stores u16; we wrap)
        delt = args.delta & 0xFFFF

        if args.poke in ("A", "both") and a0 is not None:
            wu16_be(addrA, (a0 + delt) & 0xFFFF)
            a1 = ru16_be(addrA)
            emit(a1, b0, hp0,
                 (a1 - b0) & 0xFFFF if (a1 is not None and b0 is not None) else None,
                 ((a1 or 0) - (b0 or 0)),
                 delta_applied=f"+{delt}", poke_target="A")

        if args.poke in ("B", "both"):
            # refresh b0 in case A poke influenced it
            b0 = ru16_be(addrB)
            if b0 is not None:
                wu16_be(addrB, (b0 + delt) & 0xFFFF)
                b1 = ru16_be(addrB)
                a_now = ru16_be(addrA)
                hp_now = ru16_be(addrH)
                emit(a_now, b1, hp_now,
                     ( (a_now or 0) - (b1 or 0) ) & 0xFFFF if (a_now is not None and b1 is not None) else None,
                     ((a_now or 0) - (b1 or 0)),
                     delta_applied=f"+{delt}", poke_target="B")

    # Passive loop
    interval = 1.0 / max(1e-6, args.hz)
    try:
        prevA = prevB = prevH = None
        while True:
            t0 = time.time()
            A = ru16_be(addrA)
            B = ru16_be(addrB)
            H = ru16_be(addrH)

            d_raw = ((A or 0) - (B or 0)) & 0xFFFF if (A is not None and B is not None) else None
            d_s   = ((A or 0) - (B or 0)) if (A is not None and B is not None) else None

            emit(A, B, H, d_raw, d_s)

            if args.once:
                break

            sleep_left = interval - (time.time() - t0)
            if sleep_left > 0:
                time.sleep(sleep_left)
    finally:
        if fh:
            fh.close()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
