# impact_serial_watch.py
import time, struct, argparse
import dolphin_memory_engine as dme

def ru16_be(a):
    b = dme.read_bytes(a, 2)
    return struct.unpack(">H", b)[0] if b and len(b)==2 else None

def rf32_be(a):
    b = dme.read_bytes(a, 4)
    return struct.unpack(">f", b)[0] if b and len(b)==4 else None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", type=lambda x:int(x,0), required=True,
                    help="fighter_base (e.g. 0x9246B9C0)")
    args = ap.parse_args()

    base = args.base
    addr_timer  = base + 0x11D7      # the u16/Q8.8 that climbs
    addr_scale  = base + 0x11D9      # damage scaler float
    addr_hp_cur = base + 0x28        # current HP (u32)
    addr_b9f0   = base + 0xB9F0      # internal combo counter

    dme.hook()
    prev_t = prev_s = prev_hp = prev_cc = None
    print("time  u16  q8.8  scale  hp  cc")

    while True:
        raw  = ru16_be(addr_timer)
        q88  = (raw/256.0) if raw is not None else None
        s    = rf32_be(addr_scale)
        hp_b = dme.read_bytes(addr_hp_cur,4)
        cc_b = dme.read_bytes(addr_b9f0,4)
        hp   = struct.unpack(">I", hp_b)[0] if hp_b and len(hp_b)==4 else None
        cc   = struct.unpack(">I", cc_b)[0] if cc_b and len(cc_b)==4 else None

        if (raw, s, hp, cc) != (prev_t, prev_s, prev_hp, prev_cc):
            print(f"{time.strftime('%H:%M:%S')}  {raw}  {q88:.2f}  {s:.2f if s is not None else 'NaN'}  {hp}  {cc}")
            prev_t, prev_s, prev_hp, prev_cc = raw, s, hp, cc
        time.sleep(1/30)

if __name__ == "__main__":
    main()
