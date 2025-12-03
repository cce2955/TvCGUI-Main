#!/usr/bin/env python3

import argparse
import time

from dolphin_io import hook, rd32, rd8, addr_in_ram
from constants import SLOTS, OFF_CHAR_ID, CHAR_NAMES


def read_slots_once():
    """
    Return a list of:
        (slotname, teamtag, slot_ptr_addr, raw_value, base, char_id,
         b26, b27, hp16)
    """
    results = []
    for slotname, ptr_addr, teamtag in SLOTS:
        raw = rd32(ptr_addr)
        if raw is not None and addr_in_ram(raw):
            base = raw
        else:
            base = None

        # Character ID
        char_id = rd32(base + OFF_CHAR_ID) if base else None

        # HP16 pair: +0x26 (high), +0x27 (low)
        if base:
            b26 = rd8(base + 0x26)   # high byte
            b27 = rd8(base + 0x27)   # low byte
        else:
            b26 = None
            b27 = None

        if b26 is not None and b27 is not None:
            hp16 = (b26 << 8) | b27
        else:
            hp16 = None

        results.append(
            (slotname, teamtag, ptr_addr, raw, base, char_id, b26, b27, hp16)
        )
    return results


def format_slot_line(slotname, teamtag, ptr_addr, raw, base,
                     char_id, b26, b27, hp16):

    ptr_str = f"0x{ptr_addr:08X}"
    raw_str = "None" if raw is None else f"0x{raw:08X}"
    base_str = "--" if base is None else f"0x{base:08X}"

    # Character ID display
    if char_id is None or char_id == 0:
        id_dec_str = "--"
        id_hex_str = "--"
        name_str = ""
    else:
        id_dec_str = str(char_id)
        id_hex_str = f"0x{char_id:08X}"
        name = CHAR_NAMES.get(char_id)
        name_str = f"  name={name}" if name else ""

    # HP16 bytes
    b26_str = "--" if b26 is None else f"{b26:02X}"
    b27_str = "--" if b27 is None else f"{b27:02X}"
    hp16_str = "--" if hp16 is None else f"{hp16} (0x{hp16:04X})"

    return (
        f"{slotname:6s} team={teamtag:>2}  "
        f"slot_ptr@{ptr_str}  raw={raw_str}  base={base_str}  "
        f"id={id_dec_str} ({id_hex_str}){name_str}  "
        f"+26={b26_str}  +27={b27_str}  HP16={hp16_str}"
    )


def run_once():
    rows = read_slots_once()
    print("=== TvC slot snapshot ===")
    for row in rows:
        print(format_slot_line(*row))


def run_loop(interval):
    print(f"=== TvC slot monitor (every {interval:.3f}s) ===")
    try:
        while True:
            rows = read_slots_once()
            print("\n--- snapshot @ {:.3f} ---".format(time.time()))
            for row in rows:
                print(format_slot_line(*row))
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nExiting slot monitor.")


def main():
    parser = argparse.ArgumentParser(
        description="Minimal CLI slot resolver: SLOTS, char IDs, and HP16."
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=0.5)

    args = parser.parse_args()

    print("slot_probe: waiting for Dolphin hook...")
    hook()
    print("slot_probe: hooked Dolphin.\n")

    if args.once:
        run_once()
    else:
        run_loop(args.interval)


if __name__ == "__main__":
    main()
