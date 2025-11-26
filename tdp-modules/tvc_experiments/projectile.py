#!/usr/bin/env python3
#
# projectile_mode_fuzzer.py
#
# Finds projectile behavior blocks in assist scripts and lets you fuzz
# the XX byte in XX A5 A0. It records what happens.

from dolphin_io import hook, rbytes, wd8
from constants import MEM2_LO, MEM2_HI
import time

TARGET_FAMILY = 0xA5   # second byte
TARGET_VARIANT = 0xA0  # third byte

def find_behavior_blocks():
    print("Scanning MEM2 for A5 A0 projectile family...")
    mem = rbytes(MEM2_LO, MEM2_HI - MEM2_LO)
    results = []
    start = 0

    while True:
        idx = mem.find(bytes([TARGET_FAMILY, TARGET_VARIANT]), start)
        if idx == -1:
            break

        addr = MEM2_LO + idx - 1
        first_byte = mem[idx - 1]

        # block structure check: XX A5 A0
        if addr >= MEM2_LO:
            results.append((addr, first_byte))

        start = idx + 1

    return results


def fuzz_block(addr):
    print(f"\nFuzzing block at {addr:08X}\n")

    for mode in range(0x00, 0x20):  # first 32 modes
        print(f"Writing mode {mode:02X} at {addr:08X}")
        wd8(addr, mode)
        time.sleep(0.25)  # enough to observe result
        # User observes what happens in-game.
    print("\nDone.")


if __name__ == "__main__":
    hook()
    blocks = find_behavior_blocks()

    if not blocks:
        print("No A5 A0 blocks found.")
    else:
        print("Found blocks:")
        for addr, mode in blocks:
            print(f"  {addr:08X}  current={mode:02X}")

        # Automatically fuzz the FIRST block found
        fuzz_block(blocks[0][0])
