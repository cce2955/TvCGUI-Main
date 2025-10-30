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

def read_bytes(addr, n):
    data = dme.read_bytes(addr, n)
    if not data or len(data) != n:
        raise RuntimeError(f"Could not read memory at {addr:#x}")
    return data

def write_bytes(addr, data):
    ok = dme.write_bytes(addr, data)
    if ok is False:  # some builds return False on failure, None on success
        raise RuntimeError(f"Could not write memory at {addr:#x}")

def write_vec3(addr, x, y, z):
    # write X, Y, Z as big-endian floats contiguously
    write_bytes(addr + 0x00, be_f32(x))
    write_bytes(addr + 0x04, be_f32(y))
    write_bytes(addr + 0x08, be_f32(z))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="absolute start addr (hex), e.g. 0x92477400")
    ap.add_argument("--hold", type=float, default=0.35, help="seconds to hold each test value")
    args = ap.parse_args()

    base = int(args.base, 16)

    hook()  # ensure Dolphin is hooked before any read/write

    # backup the 12 bytes we’ll stomp (vec3 row)
    backup = read_bytes(base, 0x0C)

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

    try:
        for x, y, z, tag in tests:
            print(f"[VEC3] {tag} -> {base:#x}")
            write_vec3(base, x, y, z)
            time.sleep(args.hold)
            write_bytes(base, backup)  # restore row
            time.sleep(0.15)
    finally:
        # ensure restored even if interrupted
        try:
            write_bytes(base, backup)
        except Exception:
            pass

if __name__ == "__main__":
    main()
