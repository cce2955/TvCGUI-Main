# ryu_autoassign_upscan_full.py
#
# 1. find ryu in MEM2 via tables/ryu.bin
# 2. for each move in tables/ryu.moves:
#    - scan upward
#    - try 3 cases (in order):
#       A) air/multi wrapper (33 33 20 00 01 34 00 00 00 ...)
#       B) cmd-normal wrapper (04 03 60 ...)
#       C) plain anim header (04 01 60 ...)
#    - once we get to the real anim header, look for 01 XX 01 3C
#    - map XX to a human name

import os, re
from dolphin_io import hook, rbytes
from constants import MEM2_LO, MEM2_HI

TABLE_FILE = "tables/ryu.bin"
MOVES_FILE = "tables/ryu.moves"

SAMPLE_TAIL_REL = 0x10
SCAN_MATCH_LEN = 0x40

# plain header
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

# air / multi-route wrapper (the one you just pasted)
# we only match the beginning so it’s not too brittle
AIR_HDR = [
    0x33, 0x33, 0x20, 0x00, 0x01, 0x34, 0x00, 0x00, 0x00,
]
AIR_HDR_LEN = len(AIR_HDR)

ANIM_ID_PATTERN = [0x01, None, 0x01, 0x3C]

SCAN_UP = 0x600
SCAN_FWD = 0x40
LOOKAHEAD_AFTER_HDR = 0x80  # give air wrapper a bit more room

HEX_PAIR_RE = re.compile(r"^[0-9A-Fa-f]{2}$")

# expanded map from your note
ANIM_MAP = {
    0x00: "5A / light",
    0x01: "5B / medium",
    0x02: "5C / heavy",
    0x03: "2A / cr.L",
    0x04: "2B / cr.M",
    0x05: "2C",
    0x06: "6C",
    0x08: "3C",
    0x09: "j.A",
    0x0A: "j.B",
    0x0B: "j.C",
    0x0E: "6B",
}


def read_mem2() -> bytes:
    return rbytes(MEM2_LO, MEM2_HI - MEM2_LO)


def _try_parse_text_hexdump(data: str) -> bytes | None:
    out = bytearray()
    for line in data.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^(0x)?[0-9A-Fa-f]+:\s*", "", line)
        for p in line.split():
            if HEX_PAIR_RE.match(p):
                out.append(int(p, 16))
    return bytes(out) if out else None


def load_table_sample(path: str) -> bytes:
    raw = open(path, "rb").read()
    try:
        txt = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    parsed = _try_parse_text_hexdump(txt)
    return parsed if parsed else raw


def load_moves(path: str):
    moves = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, off = line.split("=", 1)
            name = name.strip()
            off = off.strip()
            if off.lower().startswith("-0x"):
                rel = -int(off[3:], 16)
            elif off.lower().startswith("0x"):
                rel = int(off, 16)
            else:
                rel = int(off)
            moves.append((name, rel))
    return moves


def find_sample_in_mem(mem: bytes, sample: bytes, match_len: int) -> int | None:
    if match_len > len(sample):
        match_len = len(sample)
    sig = sample[:match_len]
    off = mem.find(sig)
    return off if off != -1 else None


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


def scan_after_anim_hdr(buf: bytes, anim_hdr_pos: int):
    start = anim_hdr_pos + len(ANIM_HDR)
    end = min(start + LOOKAHEAD_AFTER_HDR, len(buf))
    for p in range(start, end - len(ANIM_ID_PATTERN) + 1):
        if match_bytes(buf, p, ANIM_ID_PATTERN):
            return buf[p + 1]
    return None


def main():
    print("[upscan_full] hooking dolphin…")
    hook()
    mem = read_mem2()
    print(f"[upscan_full] MEM2: {len(mem)} bytes")

    sample = load_table_sample(TABLE_FILE)
    m_off = find_sample_in_mem(mem, sample, SCAN_MATCH_LEN)
    if m_off is None:
        print("[upscan_full] cannot find ryu.bin in MEM2")
        return
    ryu_tail_off = m_off + SAMPLE_TAIL_REL
    ryu_tail_abs = MEM2_LO + ryu_tail_off
    print(f"[upscan_full] Ryu tail @ 0x{ryu_tail_abs:08X}")

    moves = load_moves(MOVES_FILE)
    print(f"[upscan_full] loaded {len(moves)} moves")

    for name, rel in moves:
        hit_abs = ryu_tail_abs + rel

        start_abs = hit_abs - SCAN_UP
        if start_abs < MEM2_LO:
            start_abs = MEM2_LO
        end_abs = hit_abs + SCAN_FWD
        if end_abs > MEM2_HI:
            end_abs = MEM2_HI

        start_off = start_abs - MEM2_LO
        end_off = end_abs - MEM2_LO
        buf = mem[start_off:end_off]
        hit_idx = hit_abs - start_abs

        done = False

        for pos in range(hit_idx, -1, -1):
            # 1) AIR/MULTI WRAPPER
            if match_bytes(buf, pos, AIR_HDR):
                air_abs = start_abs + pos
                # look forward from here for real anim header
                search_start = pos + AIR_HDR_LEN
                search_end = min(search_start + LOOKAHEAD_AFTER_HDR, len(buf))
                found_inner = False
                for p2 in range(search_start, search_end):
                    if match_bytes(buf, p2, ANIM_HDR):
                        anim_id = scan_after_anim_hdr(buf, p2)
                        if anim_id is not None:
                            label = ANIM_MAP.get(anim_id, f"anim_{anim_id:02X}")
                            print(f"{name:28s} @ 0x{hit_abs:08X} -> AIR wrapper @ 0x{air_abs:08X} -> {label}")
                        else:
                            print(f"{name:28s} @ 0x{hit_abs:08X} -> AIR wrapper @ 0x{air_abs:08X} but no anim-id")
                        found_inner = True
                        done = True
                        break
                if not found_inner:
                    print(f"{name:28s} @ 0x{hit_abs:08X} -> AIR wrapper @ 0x{air_abs:08X} but no inner anim")
                done = True
                break

            # 2) CMD WRAPPER
            if match_bytes(buf, pos, CMD_HDR):
                cmd_abs = start_abs + pos
                search_start = pos + CMD_HDR_LEN + 3  # skip pointer-ish thing
                search_end = min(search_start + LOOKAHEAD_AFTER_HDR, len(buf))
                found_inner = False
                for p2 in range(search_start, search_end):
                    if match_bytes(buf, p2, ANIM_HDR):
                        anim_id = scan_after_anim_hdr(buf, p2)
                        if anim_id is not None:
                            label = ANIM_MAP.get(anim_id, f"anim_{anim_id:02X}")
                            print(f"{name:28s} @ 0x{hit_abs:08X} -> CMD wrapper @ 0x{cmd_abs:08X} -> {label}")
                        else:
                            print(f"{name:28s} @ 0x{hit_abs:08X} -> CMD wrapper @ 0x{cmd_abs:08X} but no anim-id")
                        found_inner = True
                        done = True
                        break
                if not found_inner:
                    print(f"{name:28s} @ 0x{hit_abs:08X} -> CMD wrapper @ 0x{cmd_abs:08X} but no inner anim")
                done = True
                break

            # 3) PLAIN ANIM HEADER
            if match_bytes(buf, pos, ANIM_HDR):
                hdr_abs = start_abs + pos
                anim_id = scan_after_anim_hdr(buf, pos)
                if anim_id is not None:
                    label = ANIM_MAP.get(anim_id, f"anim_{anim_id:02X}")
                    print(f"{name:28s} @ 0x{hit_abs:08X} -> anim hdr @ 0x{hdr_abs:08X} -> {label}")
                else:
                    print(f"{name:28s} @ 0x{hit_abs:08X} -> anim hdr @ 0x{hdr_abs:08X} but no anim-id")
                done = True
                break

        if not done:
            print(f"{name:28s} @ 0x{hit_abs:08X} -> none found in 0x{SCAN_UP:X} upscan")


if __name__ == "__main__":
    main()
