#!/usr/bin/env python3
from dolphin_io import hook, rbytes

SCAN_START = 0x90000000
SCAN_END   = 0x94000000
SCAN_BLOCK = 0x20000

SIG = b"\x2B\x00\x00\x09\x60"

def scan():

    addr = SCAN_START

    while addr < SCAN_END:

        data = rbytes(addr, SCAN_BLOCK)

        if data:

            pos = 0

            while True:

                i = data.find(SIG, pos)

                if i < 0:
                    break

                pos = i + 1

                hit = addr + i

                print(f"found projectile signature @ 0x{hit:08X}")

        addr += SCAN_BLOCK


def main():

    print("Hooking Dolphin...")
    hook()

    print("Scanning MEM2...")

    scan()


if __name__ == "__main__":
    main()