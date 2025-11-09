# scan_anim_id_per_table.py
#
# Faster version:
# - read MEM2 once
# - find all tails
# - for each tail, scan ONLY that table’s region for anim blocks
# - group results by tail
#
# we still look for:
#   - plain anim header
#   - cmd wrapper
#   - air wrapper
#
# and we still hunt for anim-id 0x03 (2A)

from dolphin_io import hook, rbytes
from constants import MEM2_LO, MEM2_HI

TAIL_PATTERN = b"\x00\x00\x00\x38\x01\x33\x00\x00"

ANIM_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x01, 0xE8,
    0x3F, 0x00, 0x00, 0x00,
]

CMD_HDR = [
    0x04, 0x03, 0x60, 0x00, 0x00, 0x00, 0x13, 0xCC,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x08,
    0x01, 0x34, 0x00, 0x00, 0x00,
]
CMD_HDR_LEN = len(CMD_HDR)

AIR_HDR = [
    0x33, 0x33, 0x20, 0x00, 0x01, 0x34, 0x00, 0x00, 0x00,
]
AIR_HDR_LEN = len(AIR_HDR)

ANIM_ID_PATTERN = [0x01, None, 0x01, 0x3C]
LOOKAHEAD_AFTER_HDR = 0x80

# the anim we want to find in everyone (2A)
TARGET_ANIM_ID = 0x03

# how many bytes before tail to include (to catch header sitting just above)
TABLE_BACK = 0x200
# fallback table length if we don't know the next tail
TABLE_FALLBACK_LEN = 0x8000  # 32 KB is plenty for a character block


def read_mem2() -> bytes:
    return rbytes(MEM2_LO, MEM2_HI - MEM2_LO)


def find_all_tails(mem: bytes):
    offs = []
    off = 0
    while True:
        i = mem.find(TAIL_PATTERN, off)
        if i == -1:
            break
        offs.append(i)
        off = i + 1
    return offs


def match_bytes(buf: bytes, pos: int, pat: list[int | None]) -> bool:
    L = len(pat)
    if pos < 0 or pos + L > len(buf):
        return False
    for i, b in enumerate(pat):
        if b is None:
            continue
        if buf[pos + i] != b:
            return False
    return True


def scan_after_anim_hdr(buf: bytes, hdr_pos: int) -> int | None:
    start = hdr_pos + len(ANIM_HDR)
    end = min(start + LOOKAHEAD_AFTER_HDR, len(buf))
    for p in range(start, end - len(ANIM_ID_PATTERN) + 1):
        if match_bytes(buf, p, ANIM_ID_PATTERN):
            return buf[p + 1]
    return None


def main():
    print("[fast-scan] hook…")
    hook()
    mem = read_mem2()
    print(f"[fast-scan] MEM2 = {len(mem)} bytes")

    tail_offs = find_all_tails(mem)
    tail_offs.sort()
    print(f"[fast-scan] found {len(tail_offs)} tails")

    # convert to absolute, easier to print
    tail_abs = [MEM2_LO + o for o in tail_offs]

    results = []  # list of (tail_abs, anim_addr_abs)

    for idx, tail_off in enumerate(tail_offs):
        tail_abs_addr = MEM2_LO + tail_off

        # figure scan window for this table
        start_off = max(0, tail_off - TABLE_BACK)
        if idx + 1 < len(tail_offs):
            # stop at next tail
            end_off = tail_offs[idx + 1]
        else:
            # last one: use fallback
            end_off = min(len(mem), tail_off + TABLE_FALLBACK_LEN)

        buf = mem[start_off:end_off]
        base_abs = MEM2_LO + start_off

        # walk this table slice only
        i = 0
        found_for_this_tail = []
        while i < len(buf):
            # 1) air wrapper
            if match_bytes(buf, i, AIR_HDR):
                search_start = i + AIR_HDR_LEN
                search_end = min(search_start + LOOKAHEAD_AFTER_HDR, len(buf))
                for p in range(search_start, search_end):
                    if match_bytes(buf, p, ANIM_HDR):
                        anim_id = scan_after_anim_hdr(buf, p)
                        if anim_id == TARGET_ANIM_ID:
                            abs_addr = base_abs + p
                            found_for_this_tail.append(abs_addr)
                        break
                i += AIR_HDR_LEN
                continue

            # 2) cmd wrapper
            if match_bytes(buf, i, CMD_HDR):
                search_start = i + CMD_HDR_LEN + 3
                search_end = min(search_start + LOOKAHEAD_AFTER_HDR, len(buf))
                for p in range(search_start, search_end):
                    if match_bytes(buf, p, ANIM_HDR):
                        anim_id = scan_after_anim_hdr(buf, p)
                        if anim_id == TARGET_ANIM_ID:
                            abs_addr = base_abs + p
                            found_for_this_tail.append(abs_addr)
                        break
                i += CMD_HDR_LEN
                continue

            # 3) plain header
            if match_bytes(buf, i, ANIM_HDR):
                anim_id = scan_after_anim_hdr(buf, i)
                if anim_id == TARGET_ANIM_ID:
                    abs_addr = base_abs + i
                    found_for_this_tail.append(abs_addr)
                i += len(ANIM_HDR)
                continue

            i += 1

        results.append((tail_abs_addr, found_for_this_tail))

    # print
    print("\n=== results for anim 0x03 (2A) ===")
    for i, (t, hits) in enumerate(results):
        if hits:
            print(f"Table {i} @ 0x{t:08X}:")
            for h in hits:
                print(f"  2A anim @ 0x{h:08X}")
        else:
            print(f"Table {i} @ 0x{t:08X}: (no 2A found)")


if __name__ == "__main__":
    main()
