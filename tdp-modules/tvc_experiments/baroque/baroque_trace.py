#!/usr/bin/env python3
# tvc_experiments/baroque_trace.py  (absolute-or-relative flag watcher)
from __future__ import annotations
import argparse, time, re
from typing import List, Tuple, Optional
import dolphin_io as dio

# Fallbacks if constants.py isn't present here
try:
    from constants import MEM1_LO, MEM1_HI, MEM2_LO, MEM2_HI
except Exception:
    MEM1_LO, MEM1_HI = 0x80000000, 0x81800000
    MEM2_LO, MEM2_HI = 0x90000000, 0x94000000

MANAGERS = {"P1C1":0x803C9FCC,"P1C2":0x803C9FDC,"P2C1":0x803C9FD4,"P2C2":0x803C9FE4}

def in_ram(a:int)->bool:
    return (MEM1_LO<=a<MEM1_HI) or (MEM2_LO<=a<MEM2_HI)

def rbytes(a:int,n:int)->bytes:
    b = dio.rbytes(a,n)
    return b if b else b""

def ru32(a:int)->Optional[int]:
    v = dio.rd32(a)
    return v if isinstance(v,int) else None

def resolve_base(slot:str)->int:
    m = MANAGERS[slot]
    p1 = ru32(m)
    if not p1 or not in_ram(p1): raise RuntimeError(f"mgr {hex(m)} -> {p1}")
    p2 = ru32(p1)
    return p2 if p2 and in_ram(p2) else p1

def parse_rel(expr:str)->int:
    s = expr.strip().upper().replace(" ","")
    if "+" in s:
        h,t = s.split("+",1)
        base = int(h,16)
        add  = int(t,16 if t.startswith("0X") or re.fullmatch(r"[0-9A-F]+",t) else 10)
        return base+add
    return int(s,16 if s.startswith("0X") or re.fullmatch(r"[0-9A-F]+",s) else 10)

def parse_rel_ranges(arg:str)->List[Tuple[int,int]]:
    out=[]
    if not arg: return out
    for part in arg.split(","):
        part=part.strip()
        if not part: continue
        if ":" not in part: raise ValueError(f"bad rel '{part}' (OFF:LEN)")
        off,len_s = part.split(":",1)
        out.append((parse_rel(off), int(len_s,16 if len_s.lower().startswith("0x") else 16)))
    return out

def parse_abs_ranges(arg:str)->List[Tuple[int,int]]:
    out=[]
    if not arg: return out
    for part in arg.split(","):
        part=part.strip()
        if not part: continue
        if ":" not in part: raise ValueError(f"bad abs '{part}' (ADDR:LEN)")
        a,len_s = part.split(":",1)
        a_i = int(a,16 if a.lower().startswith("0x") else 16)
        out.append((a_i, int(len_s,16 if len_s.lower().startswith("0x") else 16)))
    return out

def hexdump(buf:bytes, start:int, width:int=16)->str:
    lines=[]
    for i in range(0,len(buf),width):
        chunk=buf[i:i+width]
        hexs=" ".join(f"{b:02X}" for b in chunk)
        lines.append(f"{start+i:08X}: {hexs}")
    return "\n".join(lines)

def echo_watch(addr:int,size:int,need_zero:bool,poll:float,echo_every:float)->Tuple[bytes,bytes]:
    last_echo=0.0
    # Optional re-arm phase (wait for 00..00 before edge)
    if need_zero:
        while True:
            now=rbytes(addr,size)
            if len(now)==size and all(b==0 for b in now):
                print(f"[WAIT] re-arm ok @ {hex(addr)} {now.hex().upper()}")
                break
            t=time.time()
            if t-last_echo>=echo_every:
                print(f"[WAIT] re-arm @ {hex(addr)} now={now.hex().upper() if now else '??'}")
                last_echo=t
            time.sleep(poll)
    # Rising edge (!= 00..00)
    last_echo=0.0
    prev=b"\x00"*size
    while True:
        cur=rbytes(addr,size)
        if len(cur)==size and any(b!=0 for b in cur):
            return prev,cur
        t=time.time()
        if t-last_echo>=echo_every:
            print(f"[WAIT] edge   @ {hex(addr)} now={cur.hex().upper() if cur else '??'}")
            last_echo=t
        prev=cur if len(cur)==size else prev
        time.sleep(poll)

def main():
    ap=argparse.ArgumentParser()
    # Relative mode (fighter base + offset)
    ap.add_argument("--slot",choices=["P1C1","P1C2","P2C1","P2P2","P2C2"],help="slot for relative mode")
    ap.add_argument("--flag-off",default="CBA0+0x0B", help="relative flag offset (e.g. CBA0+0x0B)")
    # Absolute mode (row + byte index) — use this for 0x9246CBA0 + 11
    ap.add_argument("--flag-abs",type=lambda s:int(s,16),help="absolute row address (e.g. 0x9246CBA0)")
    ap.add_argument("--flag-index",type=int,default=0,help="byte index within that row (0-based)")
    # Common
    ap.add_argument("--flag-size",type=int,default=1,help="1 or 2 bytes to watch")
    ap.add_argument("--poll",type=float,default=0.02)
    ap.add_argument("--echo-interval",type=float,default=0.25)
    ap.add_argument("--posthold",type=float,default=0.02)
    ap.add_argument("--ranges",default="CBA0:0x80,BA50:0x60,BB00:0x40,BB20:0x40,B9F0:0x40")
    ap.add_argument("--abs",default="")
    ap.add_argument("--once",action="store_true")
    ap.add_argument("--no-rearm-wait",action="store_true")
    args=ap.parse_args()

    dio.hook()

    # Decide addressing mode
    if args.flag_abs is not None:
        flag_addr = args.flag_abs + args.flag_index
        base = None
        mode = "ABS"
    else:
        if not args.slot:
            raise SystemExit("Relative mode needs --slot. For absolute, use --flag-abs and --flag-index.")
        base = resolve_base(args.slot)
        rel  = parse_rel(args.flag_off)
        flag_addr = base + rel
        mode = "REL"

    if not in_ram(flag_addr):
        raise RuntimeError(f"flag out of RAM: {hex(flag_addr)}")

    rel_ranges = parse_rel_ranges(args.ranges) if base is not None else []
    abs_ranges = parse_abs_ranges(args.abs)

    print(f"[TRACE] mode={mode}")
    if base is not None:
        print(f"[TRACE] base={hex(base)} flag=base+{hex(parse_rel(args.flag_off))} -> {hex(flag_addr)} size={args.flag_size}")
    else:
        print(f"[TRACE] flag=row {hex(args.flag_abs)} + index {args.flag_index} -> {hex(flag_addr)} size={args.flag_size}")

    if rel_ranges:
        print("[TRACE] rel dumps:", ", ".join(f"+{hex(o)}:{hex(l)}" for o,l in rel_ranges))
    if abs_ranges:
        print("[TRACE] abs dumps:", ", ".join(f"{hex(a)}:{hex(l)}" for a,l in abs_ranges))

    trig=0
    while True:
        prev,cur = echo_watch(flag_addr,args.flag_size,need_zero=not args.no_rearm_wait,
                              poll=args.poll,echo_every=args.echo_interval)
        trig+=1
        print(f"\n[EDGE] #{trig} {hex(flag_addr)} {prev.hex().upper()} -> {cur.hex().upper()}")
        time.sleep(args.posthold)

        if base is not None:
            for off,ln in rel_ranges:
                addr=base+off
                buf=rbytes(addr,ln)
                print(f"\n[REL] base+{hex(off)} @ {hex(addr)} len={hex(ln)}")
                print(hexdump(buf,addr))
        for a,ln in abs_ranges:
            buf=rbytes(a,ln)
            print(f"\n[ABS] {hex(a)} len={hex(ln)}")
            print(hexdump(buf,a))

        if args.once: break

if __name__=="__main__":
    main()
