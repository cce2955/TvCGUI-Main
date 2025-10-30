#!/usr/bin/env python3
import argparse, struct, time
import dolphin_memory_engine as dme

def hook():
    if not dme.is_hooked():
        while True:
            try:
                dme.hook()
                if dme.is_hooked():
                    break
            except Exception:
                pass
            time.sleep(0.2)

def be_f32(x): return struct.pack(">f", x)
def rd(addr, n):
    b = dme.read_bytes(addr, n)
    if not b or len(b) != n:
        raise RuntimeError(f"read fail @ {addr:#x}")
    return b
def wr(addr, b):
    ok = dme.write_bytes(addr, b)
    if ok is False:
        raise RuntimeError(f"write fail @ {addr:#x}")

def read_vec3(addr):
    b = rd(addr, 12)
    x,y,z = struct.unpack(">fff", b)
    return (x,y,z)

def write_vec3(addr, x,y,z):
    wr(addr+0x00, be_f32(x))
    wr(addr+0x04, be_f32(y))
    wr(addr+0x08, be_f32(z))

def pulse_row(addr, x,y,z, hold=0.30, rest=None, rest_hold=0.15):
    if rest is None:
        rest = rd(addr, 12)
    write_vec3(addr, x,y,z)
    time.sleep(hold)
    wr(addr, rest)
    time.sleep(rest_hold)
    return rest

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="start of BA-cluster (hex), e.g. 0x92477400")
    ap.add_argument("--offs", default="0x00,0x0C,0x18,0x2C,0x54,0x58,0x5C", help="comma hex list")
    ap.add_argument("--hold", type=float, default=0.35)
    args = ap.parse_args()

    base = int(args.base, 16)
    offs = [int(s,16) for s in args.offs.split(",")]

    hook()

    print(f"[PROBE] base={base:#x}")
    for off in offs:
        row = base + off
        try:
            x,y,z = read_vec3(row)
            print(f"[READ] +{off:#04x} @ {row:#x}  ->  {x: .4f} {y: .4f} {z: .4f}")
        except Exception as e:
            print(f"[READ] +{off:#04x} @ {row:#x}  ->  <fail> {e}")

    print("\n[PULSE] axis pulses per row")
    tests = [(1.0,0.0,0.0,"X+"), (-1.0,0.0,0.0,"X-"),
             (0.0,1.0,0.0,"Y+"), (0.0,-1.0,0.0,"Y-"),
             (0.0,0.0,1.0,"Z+"), (0.0,0.0,-1.0,"Z-")]

    for off in offs:
        row = base + off
        try:
            backup = rd(row, 12)
        except Exception as e:
            print(f"[SKIP] +{off:#04x} read fail: {e}")
            continue
        print(f"\n-- Row +{off:#04x} @ {row:#x} --")
        for (x,y,z,label) in tests:
            print(f"[HOLD] {label}")
            try:
                backup = pulse_row(row, x,y,z, hold=args.hold, rest=backup)
            except Exception as e:
                print(f"[ERR] {e}")
                # try restore once
                try: wr(row, backup)
                except: pass

if __name__ == "__main__":
    main()
