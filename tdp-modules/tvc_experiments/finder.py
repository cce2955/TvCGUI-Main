#!/usr/bin/env python3
#
# tatsu_phase_locator.py
#
# Find all occurrences of:
#   01 11 01 3C  (tatsu start)
#   01 12 01 3C  (tatsu mid)
#   01 13 01 3C  (tatsu end)
#
# Then, for each 11-site, pair it with the nearest 12 and 13 in memory.
# This should reveal the three L/M/H triplets without assuming they are
# tightly packed in the same small script window.

from dolphin_io import hook, rbytes
from constants import MEM2_LO, MEM2_HI

START_ID = 0x11
MID_ID   = 0x12
END_ID   = 0x13


def anim_pattern(anim_id: int) -> bytes:
    return bytes([0x01, anim_id & 0xFF, 0x01, 0x3C])


def find_all(mem: bytes, pat: bytes):
    addrs = []
    idx = 0
    while True:
        idx = mem.find(pat, idx)
        if idx == -1:
            break
        addrs.append(MEM2_LO + idx)
        idx += 1
    return addrs


def nearest(target, candidates):
    if not candidates:
        return None
    return min(candidates, key=lambda a: abs(a - target))


def main():
    print("Hooking Dolphin...")
    hook()

    print("Reading MEM2...")
    size = MEM2_HI - MEM2_LO
    mem = rbytes(MEM2_LO, size)
    if not mem:
        print("Failed to read MEM2.")
        return

    pat11 = anim_pattern(START_ID)
    pat12 = anim_pattern(MID_ID)
    pat13 = anim_pattern(END_ID)

    addrs11 = find_all(mem, pat11)
    addrs12 = find_all(mem, pat12)
    addrs13 = find_all(mem, pat13)

    print(f"Found {len(addrs11)} x 01 11 01 3C")
    print(f"Found {len(addrs12)} x 01 12 01 3C")
    print(f"Found {len(addrs13)} x 01 13 01 3C\n")

    if not addrs11:
        print("No 11-patterns found, stopping.")
        return

    print("Nearest triplets for each 11-site:\n")
    for i, a11 in enumerate(addrs11, 1):
        n12 = nearest(a11, addrs12)
        n13 = nearest(a11, addrs13)
        print(f"11[{i}]: 0x{a11:08X}")
        if n12 is not None:
            print(f"   nearest 12: 0x{n12:08X}  (delta {n12 - a11:+#x})")
        else:
            print("   nearest 12: none")
        if n13 is not None:
            print(f"   nearest 13: 0x{n13:08X}  (delta {n13 - a11:+#x})")
        else:
            print("   nearest 13: none")
        print()


if __name__ == "__main__":
    main()
