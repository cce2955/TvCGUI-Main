#!/usr/bin/env python3
import argparse, struct, time
import dolphin_memory_engine as dme

def be_f32(x): return struct.pack(">f", x)
def write_f32(addr, x): dme.write_bytes(addr, be_f32(x))
def read_bytes(addr, n): return dme.read_bytes(addr, n)

def write_vec3(addr, x, y, z):
    dme.write_bytes(addr + 0x00, be_f32(x))
    dme.write_bytes(addr + 0x04, be_f32(y))
    dme.write_bytes(addr + 0x08, be_f32(z))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="absolute start addr (hex), e.g. 0x92477400")
    ap.add_argument("--hold", type=float, default=0.25)
    args = ap.parse_args()

    base = int(args.base, 16)
    # backup 0x0..0x0B (vec3 region)
    bk = read_bytes(base, 0x0C)

    tests = [
        (1.0, 0.0, 0.0, "X+"),
        (-1.0, 0.0, 0.0, "X-"),
        (0.0, 1.0, 0.0, "Y+"),
        (0.0, -1.0, 0.0, "Y-"),
        (0.0, 0.0, 1.0, "Z+"),
        (0.0, 0.0, -1.0, "Z-"),
        (1.0, 1.0, 0.0, "XY+"),
        (0.5, 0.5, 0.5, "XYZ 0.5"),
        (2.0, 0.0, 0.0, "X++"),
    ]

    for x,y,z, tag in tests:
        print(f"[VEC3] {tag} -> {hex(base)}")
        write_vec3(base, x, y, z)
        time.sleep(args.hold)
        dme.write_bytes(base, bk)
        time.sleep(0.15)

if __name__ == "__main__":
    main()
