#!/usr/bin/env python3
import argparse, time, struct
import dolphin_memory_engine as dme

def hook():
    if not dme.is_hooked():
        while True:
            try:
                dme.hook()
                if dme.is_hooked(): break
            except Exception: pass
            time.sleep(0.2)

def be_f32(x): return struct.pack(">f", x)
def rd(addr, n): 
    b = dme.read_bytes(addr, n)
    if not b or len(b)!=n: raise RuntimeError(f"read fail {addr:#x}")
    return b
def wr(addr,b):
    ok=dme.write_bytes(addr,b)
    if ok is False: raise RuntimeError(f"write fail {addr:#x}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", required=True, help="row address (hex), e.g. 0x9247742C")
    ap.add_argument("--mode", choices=["zero","scale"], default="zero")
    ap.add_argument("--scale", type=float, default=0.5, help="used when --mode scale")
    ap.add_argument("--hold", type=float, default=0.5)
    args = ap.parse_args()

    addr = int(args.addr,16)
    hook()
    backup = rd(addr,12)

    try:
        if args.mode == "zero":
            wr(addr, be_f32(0.0)+be_f32(0.0)+be_f32(0.0))
        else:
            x,y,z = struct.unpack(">fff", backup)
            wr(addr, be_f32(x*args.scale)+be_f32(y*args.scale)+be_f32(z*args.scale))
        time.sleep(args.hold)
    finally:
        try: wr(addr, backup)
        except: pass

if __name__ == "__main__":
    main()
