# tvc_experiments/bbq_watch_tui.py
# Watch fighter base (default 0x9246B9E0) at byte indices 11–12 and 15–16.
# Shows u16 for the two windows, plus hex/ASCII and a 4B peek.
# Clear-screen HUD, low-noise output.

import time, struct, argparse, os
import dolphin_memory_engine as dme

DEFAULT_BASE = 0x9246B9E0

# Byte indices (decimal) relative to base
IDX_A = 11  # -> base + 0x0B
IDX_B = 15  # -> base + 0x0F

def hook():
    while not dme.is_hooked():
        try:
            dme.hook()
        except Exception:
            pass
        time.sleep(0.1)

def rbytes(addr, n):
    try:
        data = dme.read_bytes(addr, n)
        return data if data and len(data) == n else None
    except Exception:
        return None

def u16_be(b): return struct.unpack(">H", b)[0]
def u32_be(b): return struct.unpack(">I", b)[0]
def f32_be(b): return struct.unpack(">f", b)[0]

def read_hp(base):
    max_b = rbytes(base + 0x24, 4)
    cur_b = rbytes(base + 0x28, 4)
    if not max_b or not cur_b:
        return None, None
    return u32_be(max_b), u32_be(cur_b)

def decode_pair(base, idx):
    addr2 = base + idx
    b2 = rbytes(addr2, 2)
    b4 = rbytes(addr2, 4)  # context peek
    out = {"idx": idx, "addr2": addr2}

    if b2:
        out["hex2"] = " ".join(f"{x:02X}" for x in b2)
        out["u16"]  = u16_be(b2)
        out["asc2"] = "".join(chr(x) if 32 <= x <= 126 else "." for x in b2)
    else:
        out["hex2"] = "--"
        out["u16"] = None
        out["asc2"] = "--"

    if b4:
        out["hex4"] = " ".join(f"{x:02X}" for x in b4)
        out["u32"]  = u32_be(b4)
        try:
            f = f32_be(b4)
            out["f32"] = f if abs(f) < 1e38 else None
        except Exception:
            out["f32"] = None
        out["asc4"] = "".join(chr(x) if 32 <= x <= 126 else "." for x in b4)
    else:
        out["hex4"] = "--"
        out["u32"] = None
        out["f32"] = None
        out["asc4"] = "--"

    return out

def draw(base, hz):
    os.system("cls" if os.name == "nt" else "clear")
    ts = time.strftime("%H:%M:%S")

    max_hp, cur_hp = read_hp(base)
    print(f"[BBQ mini-HUD] {ts}  base=0x{base:08X}  watch idx (11–12) & (15–16)  @ {hz} Hz")
    if max_hp is not None:
        print(f"HP: {cur_hp}/{max_hp}\n")
    else:
        print("HP: unreadable (stale base?)\n")

    a = decode_pair(base, IDX_A)
    b = decode_pair(base, IDX_B)

    print("Window   ByteIdx     Addr        Hex2    U16     ASCII   |   Hex4 (peek)           U32         F32            ASCII")
    print("-------  ----------  ----------  ------  ------- ------- |   --------------------  ----------  -------------  -----")
    for tag, d in (("A(11–12)", a), ("B(15–16)", b)):
        u16s = str(d["u16"]) if d["u16"] is not None else "NaN"
        u32s = str(d["u32"]) if d["u32"] is not None else "NaN"
        f32s = f"{d['f32']:.6f}" if isinstance(d["f32"], float) else "NaN"
        print(f"{tag:<7}  {d['idx']:>10}  0x{d['addr2']:08X}  {d['hex2']:<6}  {u16s:>7}  {d['asc2']:<7} |   {d['hex4']:<20}  {u32s:>10}  {f32s:>13}  {d['asc4']:<5}")

def main():
    ap = argparse.ArgumentParser(description="Watch fighter_base byte indices 11–12 and 15–16 as u16 windows.")
    ap.add_argument("--base", type=lambda x:int(x,0), default=DEFAULT_BASE, help="fighter base (default 0x9246B9E0)")
    ap.add_argument("--hz", type=float, default=10.0, help="refresh rate")
    args = ap.parse_args()

    hook()
    interval = 1.0 / max(1e-6, args.hz)
    try:
        while True:
            t0 = time.time()
            draw(args.base, args.hz)
            dt = time.time() - t0
            if (sleep_left := interval - dt) > 0:
                time.sleep(sleep_left)
    except KeyboardInterrupt:
        pass

if __name__ == "__main__":
    main()
