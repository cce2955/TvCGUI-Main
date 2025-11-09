# scan_normals_all.py
#
# Scan MEM2 for ALL character tables and list every "normal-like" move
# we can recognize (the ones that end in 01 XX 01 3C) per table.
#
# Based on your fast scan that worked.

from dolphin_io import hook, rbytes
from constants import MEM2_LO, MEM2_HI

# tail marker
TAIL_PATTERN = b"\x00\x00\x00\x38\x01\x33\x00\x00"

# plain animation header
ANIM_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x01, 0xE8,
    0x3F, 0x00, 0x00, 0x00,
]

# command-normal wrapper
CMD_HDR = [
    0x04, 0x03, 0x60, 0x00, 0x00, 0x00, 0x13, 0xCC,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x08,
    0x01, 0x34, 0x00, 0x00, 0x00,
]
CMD_HDR_LEN = len(CMD_HDR)

# air / multi wrapper
AIR_HDR = [
    0x33, 0x33, 0x20, 0x00, 0x01, 0x34, 0x00, 0x00, 0x00,
]
AIR_HDR_LEN = len(AIR_HDR)

# after a real anim header we expect this
ANIM_ID_PATTERN = [0x01, None, 0x01, 0x3C]
LOOKAHEAD_AFTER_HDR = 0x80

# how big the per-table window is
TABLE_BACK = 0x200
TABLE_FALLBACK_LEN = 0x8000  # 32K

# all IDs we currently understand (you just mapped these)
ANIM_MAP = {
    0x00: "5A / light",
    0x01: "5B / medium",
    0x02: "5C / heavy",
    0x03: "2A / cr.L",
    0x04: "2B / cr.M",
    0x05: "2C / cr.H",       # your Ryu data showed this
    0x06: "6C",
    0x08: "3C / alt",
    0x09: "j.A",
    0x0A: "j.B",
    0x0B: "j.C",
    0x0E: "6B",
    0x14: "dash/donkey-ish",  # from your Ryu dump
}
NORMAL_IDS = set(ANIM_MAP.keys())


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


def get_anim_id_after_hdr(buf: bytes, hdr_pos: int) -> int | None:
    start = hdr_pos + len(ANIM_HDR)
    end = min(start + LOOKAHEAD_AFTER_HDR, len(buf))
    for p in range(start, end - len(ANIM_ID_PATTERN) + 1):
        if match_bytes(buf, p, ANIM_ID_PATTERN):
            return buf[p + 1]
    return None


def main():
    print("[scan-all] hook…")
    hook()
    mem = read_mem2()
    print(f"[scan-all] MEM2 = {len(mem)} bytes")

    tail_offs = find_all_tails(mem)
    tail_offs.sort()
    print(f"[scan-all] found {len(tail_offs)} tail markers")

    tail_abs = [MEM2_LO + o for o in tail_offs]

    # table_results[i] = list of (anim_id, anim_abs)
    table_results: list[list[tuple[int, int]]] = []

    for idx, tail_off in enumerate(tail_offs):
        tail_abs_addr = MEM2_LO + tail_off

        start_off = max(0, tail_off - TABLE_BACK)
        if idx + 1 < len(tail_offs):
            end_off = tail_offs[idx + 1]
        else:
            end_off = min(len(mem), tail_off + TABLE_FALLBACK_LEN)

        buf = mem[start_off:end_off]
        base_abs = MEM2_LO + start_off

        moves_here: list[tuple[int, int]] = []

        i = 0
        while i < len(buf):
            # 1) air wrapper
            if match_bytes(buf, i, AIR_HDR):
                search_start = i + AIR_HDR_LEN
                search_end = min(search_start + LOOKAHEAD_AFTER_HDR, len(buf))
                for p in range(search_start, search_end):
                    if match_bytes(buf, p, ANIM_HDR):
                        anim_id = get_anim_id_after_hdr(buf, p)
                        if anim_id is not None and anim_id in NORMAL_IDS:
                            abs_addr = base_abs + p
                            moves_here.append((anim_id, abs_addr))
                        break
                i += AIR_HDR_LEN
                continue

            # 2) cmd wrapper
            if match_bytes(buf, i, CMD_HDR):
                search_start = i + CMD_HDR_LEN + 3
                search_end = min(search_start + LOOKAHEAD_AFTER_HDR, len(buf))
                for p in range(search_start, search_end):
                    if match_bytes(buf, p, ANIM_HDR):
                        anim_id = get_anim_id_after_hdr(buf, p)
                        if anim_id is not None and anim_id in NORMAL_IDS:
                            abs_addr = base_abs + p
                            moves_here.append((anim_id, abs_addr))
                        break
                i += CMD_HDR_LEN
                continue

            # 3) plain anim header
            if match_bytes(buf, i, ANIM_HDR):
                anim_id = get_anim_id_after_hdr(buf, i)
                if anim_id is not None and anim_id in NORMAL_IDS:
                    abs_addr = base_abs + i
                    moves_here.append((anim_id, abs_addr))
                i += len(ANIM_HDR)
                continue

            i += 1

        table_results.append(moves_here)

    # print nicely
    print("\n=== normals per detected table ===")
    for idx, tail in enumerate(tail_abs):
        moves_here = table_results[idx]
        if not moves_here:
            print(f"Table {idx:3d} @ 0x{tail:08X}: (no normals)")
            continue
        print(f"Table {idx:3d} @ 0x{tail:08X}:")
        # group by anim id
        by_id: dict[int, list[int]] = {}
        for anim_id, addr in moves_here:
            by_id.setdefault(anim_id, []).append(addr)
        for anim_id, addrs in sorted(by_id.items()):
            name = ANIM_MAP.get(anim_id, f"anim_{anim_id:02X}")
            for a in addrs:
                print(f"  {name:15s} @ 0x{a:08X}")


if __name__ == "__main__":
    main()
