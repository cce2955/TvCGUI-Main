import struct
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dolphin_io import hook, rbytes, rd32
from constants import MEM2_LO, MEM2_HI, SLOTS, CHAR_NAMES
from move_id_map import lookup_move_name


# ============================================================
# CONFIG CONSTANTS
# ============================================================

CLUSTER_GAP   = 0x4000
CLUSTER_PAD_BACK = 0x400
LOOKAHEAD_AFTER_HDR = 0x80

CHR_TBL_PTR_OFF       = 0x1E0
CHR_TBL_LABEL_REL     = -0x18
# Most characters use a 0xB04-byte chr_tbl, but a few do not:
#   Volnutt starts entry0 at 0x3610 instead of 0x3600.
#   Soki's chr_tbl is longer; chr_act appears at 0x14C4 instead of 0xB04.
# Keep the old constants for default expectations, but validate/parse flexibly.
CHR_TBL_LEN_BYTES     = 0xB04
CHR_TBL_NUM_ENTRIES   = 705
CHR_TBL_SENTINEL_REL  = 0xB00
CHR_ACT_LABEL_REL     = 0xB04
CHR_TBL_ENTRY0_VAL    = 0x3600
CHR_TBL_ENTRY0_ALT_VALS = {0x3600, 0x3610}
CHR_TBL_SCAN_MAX_BYTES = 0x2000
MOVE_DATA_START_OFF   = 0x3600
CHR_TBL_MAX_MOVE_OFFSET = 0x90000

SLOT_STRIDE = 0x380020

SLOT_ID_OFF_A = 0x04
SLOT_ID_OFF_B = 0x08

FIGHTER_BASE_SCAN_RANGE = 0x400
BACKTRACK_MAX = 0x20000

ANIM_HDR = [
    0x04, 0x01, 0x60, 0x00,
    0x00, 0x00, 0x01, 0xE8,
    0x3F, 0x00, 0x00, 0x00,
]

CMD_HDR = [
    0x04, 0x03, 0x60, 0x00,
    0x00, 0x00, 0x13, 0xCC,
    0x3F, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x08,
    0x01, 0x34, 0x00, 0x00, 0x00,
]
CMD_HDR_LEN = len(CMD_HDR)

AIR_HDR = [
    0x33, 0x33, 0x20, 0x00,
    0x01, 0x34, 0x00, 0x00, 0x00,
]
AIR_HDR_LEN = len(AIR_HDR)

SUPER_END_HDR = [
    0x04, 0x01, 0x60, 0x00,
    0x00, 0x00, 0x12, 0x18, 0x3F,
]

ANIM_MAP = {
    0x00: "5A", 0x01: "5B", 0x02: "5C",
    0x03: "2A", 0x04: "2B", 0x05: "2C",
    0x06: "6C", 0x08: "3C",
    0x09: "j.A", 0x0A: "j.B", 0x0B: "j.C",
    0x0E: "6B",
}

# 0x0E can be a real 6B for some characters, but it is also easy to pick up
# from non-normal script/system records. Treat it as optional until a
# character-specific lookup confirms it.
OPTIONAL_NORMAL_IDS = {0x0E}
CORE_NORMAL_IDS = set(ANIM_MAP.keys()) - OPTIONAL_NORMAL_IDS
NORMAL_IDS = set(ANIM_MAP.keys())

DEFAULT_METER = {
    0x00: 0x32, 0x03: 0x32, 0x09: 0x32,
    0x01: 0x64, 0x04: 0x64, 0x0A: 0x64,
    0x02: 0x96, 0x05: 0x96, 0x0B: 0x96,
    0x06: 0x96, 0x08: 0x96, 0x0E: 0x96,
}
SPECIAL_DEFAULT_METER = 0xC8

ACTIVE_HDR = [
    0x20, 0x35, 0x01, 0x20,
    0x3F, 0x00, 0x00, 0x00,
]
ACTIVE_TOTAL_LEN = 20

INLINE_ACTIVE_HDR = [
    0x3F, 0x00, 0x00, 0x00,
    None,
    0x11, 0x16, 0x20, 0x00,
    0x11, 0x22, 0x60, 0x00,
    0x00, 0x00, 0x00,
    None,
]
INLINE_ACTIVE_LEN = 17
INLINE_ACTIVE_OFF = 0xB0

DAMAGE_HDR     = [0x35, 0x10, 0x20, 0x3F, 0x00]
DAMAGE_TOTAL_LEN = 16

ATKPROP_HDR = [
    0x04, 0x01, 0x60, 0x00,
    0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00,
]
ATKPROP_TOTAL_LEN = 17

HITREACTION_HDR = [
    0x04, 0x17, 0x60, 0x00,
    0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00,
    0x80, 0x04, 0x2F, 0x00,
    0x04, 0x15, 0x60, 0x00,
    0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00,
]
HITREACTION_TOTAL_LEN = len(HITREACTION_HDR)
HITREACTION_CODE_OFF  = 28

# KB/launch packet immediately after the hit-reaction block.
# Confirmed on Ryu:
#   +0x00 = 35 07/09 00 20 packet header
#   +0x04 = launch/trajectory profile (u32)
#   +0x08 = unused/unknown u32, usually 0
#   +0x0C = KB X (f32) for grounded/standing hits
#   +0x10 = Air KB / relaunch arc (f32)
#
# Do not accept the old loose 35 ?? ?? 20 family here; 35 0D packets tested as
# hitbox/collision-ish and did not affect knockback.
KNOCKBACK_TOTAL_LEN = 20
KNOCKBACK_VALID_TYPES = {0x07, 0x09}
KNOCKBACK_TYPE_OFF = 1
KNOCKBACK_PROFILE_OFF = 4
KNOCKBACK_UNKNOWN_OFF = 8
KNOCKBACK_X_OFF = 12
KNOCKBACK_AIR_OFF = 16

STUN_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x54,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, None,
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x58,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, None,
    0x33, 0x32, 0x00, 0x20, 0x00, 0x00, 0x00, None,
    0x04, 0x15, 0x60,
]
STUN_TOTAL_LEN = 43

PAIR_RANGE = 0x600

HITBOX_OFF_X = 0x40
HITBOX_OFF_Y = 0x48

FIGHTER_READ_SIZE = 0x400
CHR_TBL_READ_PAD_BEFORE = 0x18
CHR_TBL_READ_SIZE = CHR_TBL_READ_PAD_BEFORE + CHR_TBL_SCAN_MAX_BYTES + 0x20
SLOT_REGION_PAD = 0x2000


# ============================================================
# Low-level helpers
# ============================================================

def rd_u32_be(buf: bytes, off: int) -> int:
    return struct.unpack_from(">I", buf, off)[0]


def rd_f32_be(buf: bytes, off: int) -> float:
    return struct.unpack_from(">f", buf, off)[0]


def match_bytes(buf: bytes, pos: int, pat: Sequence[Optional[int]]) -> bool:
    L = len(pat)
    if pos < 0 or pos + L > len(buf):
        return False
    for i, b in enumerate(pat):
        if b is None:
            continue
        if buf[pos + i] != b:
            return False
    return True


def is_mem2_addr(v: int) -> bool:
    return MEM2_LO <= v < MEM2_HI


def abs_to_file_off(abs_addr: int, mem_base: int) -> int:
    return abs_addr - mem_base


def safe_rbytes(addr: int, size: int) -> bytes:
    if size <= 0:
        return b""
    if addr < MEM2_LO:
        size -= (MEM2_LO - addr)
        addr = MEM2_LO
    if addr >= MEM2_HI or size <= 0:
        return b""
    max_size = MEM2_HI - addr
    size = min(size, max_size)
    return rbytes(addr, size)


# ============================================================
# chr_tbl validation / resolution
# ============================================================

def _find_chr_act_rel(buf: bytes, cand_off: int) -> Optional[int]:
    """Find chr_act after chr_tbl and return its table-relative offset.

    Standard cast members put chr_act at +0xB04, but Soki has a longer table
    with chr_act at +0x14C4. Scanning for the label keeps validation strict
    enough without rejecting non-standard characters.
    """
    search_start = cand_off + 4
    search_end = min(len(buf), cand_off + CHR_TBL_SCAN_MAX_BYTES)
    pos = buf.find(b"chr_act\n", search_start, search_end)
    if pos < 0:
        return None
    return pos - cand_off


def validate_chr_tbl(buf: bytes, mem_base: int, cand_abs: int) -> bool:
    if cand_abs % 0x20 != 0:
        return False

    cand_off = abs_to_file_off(cand_abs, mem_base)

    label_off = cand_off + CHR_TBL_LABEL_REL
    if label_off < 0 or label_off + 8 > len(buf):
        return False
    if buf[label_off:label_off + 8] != b"chr_tbl\n":
        return False

    if cand_off + 4 > len(buf):
        return False

    entry0 = rd_u32_be(buf, cand_off)
    if entry0 not in CHR_TBL_ENTRY0_ALT_VALS:
        return False

    act_rel = _find_chr_act_rel(buf, cand_off)
    if act_rel is None:
        return False

    # The table should terminate with 0xFFFFFFFF immediately before chr_act.
    sent_off = cand_off + act_rel - 4
    if sent_off < cand_off or sent_off + 4 > len(buf):
        return False
    if rd_u32_be(buf, sent_off) != 0xFFFFFFFF:
        return False

    return True

def resolve_chr_tbl(buf: bytes, mem_base: int, fighter_base_abs: int) -> Optional[int]:
    fb_off = abs_to_file_off(fighter_base_abs, mem_base)
    if fb_off < 0 or fb_off >= len(buf):
        return None

    ptr_off = fb_off + CHR_TBL_PTR_OFF
    if ptr_off + 4 <= len(buf):
        cand = rd_u32_be(buf, ptr_off)
        if is_mem2_addr(cand):
            return cand

    for delta in range(0, FIGHTER_BASE_SCAN_RANGE, 4):
        off = fb_off + delta
        if off + 4 > len(buf):
            break
        cand = rd_u32_be(buf, off)
        if is_mem2_addr(cand):
            return cand

    return None


def read_and_validate_chr_tbl(chr_tbl_abs: int) -> Optional[bytes]:
    start = chr_tbl_abs - CHR_TBL_READ_PAD_BEFORE
    buf = safe_rbytes(start, CHR_TBL_READ_SIZE)
    if not buf:
        return None
    if not validate_chr_tbl(buf, start, chr_tbl_abs):
        return None
    return buf


def resolve_chr_tbl_from_live_memory(fighter_base_abs: int) -> Optional[int]:
    fighter_buf = safe_rbytes(fighter_base_abs, FIGHTER_READ_SIZE)
    if not fighter_buf:
        return None

    cand = resolve_chr_tbl(fighter_buf, fighter_base_abs, fighter_base_abs)
    if cand is not None:
        if read_and_validate_chr_tbl(cand) is not None:
            return cand

    ptr_off = CHR_TBL_PTR_OFF
    if ptr_off + 4 <= len(fighter_buf):
        raw = rd_u32_be(fighter_buf, ptr_off)
        if is_mem2_addr(raw):
            if read_and_validate_chr_tbl(raw) is not None:
                return raw

    for delta in range(0, FIGHTER_BASE_SCAN_RANGE, 4):
        off = delta
        if off + 4 > len(fighter_buf):
            break
        raw = rd_u32_be(fighter_buf, off)
        if is_mem2_addr(raw):
            if read_and_validate_chr_tbl(raw) is not None:
                return raw

    return None


def parse_chr_tbl(buf: bytes, mem_base: int, chr_tbl_abs: int) -> List[int]:
    tbl_off = abs_to_file_off(chr_tbl_abs, mem_base)
    if tbl_off < 0 or tbl_off + 4 > len(buf):
        return []

    act_rel = _find_chr_act_rel(buf, tbl_off)
    if act_rel is None:
        # Safe fallback: bounded scan. Do not assume the old 705-entry size.
        act_rel = min(CHR_TBL_SCAN_MAX_BYTES, len(buf) - tbl_off)

    moves: List[int] = []
    seen_moves: set = set()
    entry_count = max(0, act_rel // 4)

    for i in range(entry_count):
        off = tbl_off + i * 4
        if off + 4 > len(buf):
            break

        entry = rd_u32_be(buf, off)

        if entry == 0xFFFFFFFF:
            break
        if entry == 0:
            continue
        if entry % 4 != 0:
            continue
        if entry < MOVE_DATA_START_OFF:
            continue
        if entry > CHR_TBL_MAX_MOVE_OFFSET:
            continue

        move_abs = chr_tbl_abs + entry
        if is_mem2_addr(move_abs) and move_abs not in seen_moves:
            seen_moves.add(move_abs)
            moves.append(move_abs)

    return moves

# ============================================================
# Slot model
# ============================================================

def read_slots_from_constants() -> List[Tuple[str, int, Optional[int], str]]:
    out: List[Tuple[str, int, Optional[int], str]] = []
    for label, ptr, _tag in SLOTS:
        base = rd32(ptr) or 0
        cid: Optional[int] = None
        cname = ","
        if base:
            cid = rd32(base + 0x14)
            if cid is not None:
                cname = CHAR_NAMES.get(cid, f"ID_{cid}")
        out.append((label, base, cid, cname))
    return out


def verify_slot_id(buf: bytes, mem_base: int,
                   fighter_base_abs: int, expected_slot: int) -> bool:
    fb_off = abs_to_file_off(fighter_base_abs, mem_base)
    if fb_off < 0 or fb_off + 0x0C > len(buf):
        return True
    id_a = rd_u32_be(buf, fb_off + SLOT_ID_OFF_A)
    id_b = rd_u32_be(buf, fb_off + SLOT_ID_OFF_B)
    return (id_a == expected_slot) and (id_b == expected_slot)


# ============================================================
# Move-record scanning utilities
# ============================================================

def get_anim_id_after_hdr_strict(buf: bytes, hdr_pos: int) -> Optional[int]:
    start = hdr_pos + len(ANIM_HDR)
    end   = min(start + LOOKAHEAD_AFTER_HDR, len(buf))
    for p in range(start, end - 4 + 1):
        hi  = buf[p]
        lo  = buf[p + 1]
        op  = buf[p + 2]
        fps = buf[p + 3]
        if fps == 0x3C and op in (0x01, 0x04):
            aid = (hi << 8) | lo
            if 1 <= aid <= 0x0500:
                return aid
    return None


def find_strict_anim4(buf: bytes) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    for p in range(0, len(buf) - 4 + 1):
        op  = buf[p + 2]
        fps = buf[p + 3]
        if fps != 0x3C:
            continue
        if op not in (0x01, 0x04):
            continue
        hi  = buf[p]
        lo  = buf[p + 1]
        aid = (hi << 8) | lo
        if 1 <= aid <= 0x0500:
            out.append((p, aid))
    return out


def looks_like_real_move_anchor(buf: bytes, pos: int) -> bool:
    back = max(0, pos - 0x40)
    fwd  = min(len(buf), pos + 0x200)

    max_anim = len(buf) - len(ANIM_HDR)
    for p in range(back, min(pos + 1, max_anim + 1)):
        if match_bytes(buf, p, ANIM_HDR):
            return True
    for p in range(pos, min(fwd, max_anim + 1)):
        if match_bytes(buf, p, ANIM_HDR):
            return True

    lo = max(0, pos - 0x200)
    hi = min(len(buf), pos + 0x600)
    for p in range(lo, hi):
        if match_bytes(buf, p, ACTIVE_HDR):
            return True
        if match_bytes(buf, p, STUN_HDR):
            return True
        if match_bytes(buf, p, DAMAGE_HDR):
            return True

    return False


# ============================================================
# Data block parsers
# ============================================================

def parse_active_frames(buf: bytes, pos: int) -> Optional[Tuple[int, int]]:
    if pos + ACTIVE_TOTAL_LEN > len(buf):
        return None
    if not match_bytes(buf, pos, ACTIVE_HDR):
        return None
    return (buf[pos + 8] + 1, buf[pos + 16] + 1)


def parse_inline_active(buf: bytes, pos: int) -> Optional[Tuple[int, int]]:
    if pos + INLINE_ACTIVE_LEN > len(buf):
        return None
    for i, b in enumerate(INLINE_ACTIVE_HDR):
        if b is None:
            continue
        if buf[pos + i] != b:
            return None
    s = buf[pos + 4]
    e = buf[pos + 16]
    if s == 0:
        return None
    return (s, max(e, s))


def parse_damage(buf: bytes, pos: int) -> Optional[Tuple[int, int]]:
    if pos + DAMAGE_TOTAL_LEN > len(buf):
        return None
    if not match_bytes(buf, pos, DAMAGE_HDR):
        return None
    d0, d1, d2 = buf[pos + 5], buf[pos + 6], buf[pos + 7]
    flag = buf[pos + 15]
    return ((d0 << 16) | (d1 << 8) | d2, flag)


def parse_atkprop(buf: bytes, pos: int) -> Optional[int]:
    if pos + ATKPROP_TOTAL_LEN > len(buf):
        return None
    if not match_bytes(buf, pos, ATKPROP_HDR):
        return None
    return buf[pos + len(ATKPROP_HDR)]


def parse_hitreaction(buf: bytes, pos: int) -> Optional[int]:
    if pos + HITREACTION_TOTAL_LEN > len(buf):
        return None
    if not match_bytes(buf, pos, HITREACTION_HDR):
        return None
    x, y, z = buf[pos + 28], buf[pos + 29], buf[pos + 30]
    return (x << 16) | (y << 8) | z


def parse_knockback(buf: bytes, pos: int) -> Optional[Dict[str, Any]]:
    if pos + KNOCKBACK_TOTAL_LEN > len(buf):
        return None
    if buf[pos] != 0x35:
        return None
    if buf[pos + 1] not in KNOCKBACK_VALID_TYPES:
        return None
    if buf[pos + 2] != 0x00 or buf[pos + 3] != 0x20:
        return None

    # The profile/unknown words can be non-zero on launcher-style packets.
    # KB X and Air KB are big-endian floats.
    try:
        return {
            "kb_type": int(buf[pos + KNOCKBACK_TYPE_OFF]),
            "launch_profile": rd_u32_be(buf, pos + KNOCKBACK_PROFILE_OFF),
            "kb_unknown": rd_u32_be(buf, pos + KNOCKBACK_UNKNOWN_OFF),
            "kb_x": rd_f32_be(buf, pos + KNOCKBACK_X_OFF),
            "air_kb": rd_f32_be(buf, pos + KNOCKBACK_AIR_OFF),
        }
    except Exception:
        return None


def parse_stun(buf: bytes, pos: int) -> Optional[Tuple[int, int, int]]:
    if pos + STUN_TOTAL_LEN > len(buf):
        return None
    if not match_bytes(buf, pos, STUN_HDR):
        return None
    return (buf[pos + 15], buf[pos + 31], buf[pos + 38])


def pick_best_block(mv_abs: int, blocks: List[Tuple[int, Any]],
                    rng: int = PAIR_RANGE) -> Optional[Tuple[int, Any]]:
    best: Optional[Tuple[int, Any]] = None
    best_dist: Optional[int] = None
    for addr, data in blocks:
        if addr >= mv_abs:
            d = addr - mv_abs
            if d <= rng and (best_dist is None or d < best_dist):
                best, best_dist = (addr, data), d
    if best:
        return best
    for addr, data in blocks:
        d = abs(addr - mv_abs)
        if d <= rng and (best_dist is None or d < best_dist):
            best, best_dist = (addr, data), d
    return best

def _advance_block_cursor(mv_abs: int,
                          blocks: List[Tuple[int, Any]],
                          idx: int,
                          rng: int = PAIR_RANGE) -> int:
    n = len(blocks)
    while idx + 1 < n and blocks[idx + 1][0] <= mv_abs:
        idx += 1
    return idx


def pick_best_block_from_idx(mv_abs: int,
                             blocks: List[Tuple[int, Any]],
                             idx: int,
                             rng: int = PAIR_RANGE) -> Tuple[Optional[Tuple[int, Any]], int]:
    if not blocks:
        return None, 0

    idx = _advance_block_cursor(mv_abs, blocks, idx, rng)

    best: Optional[Tuple[int, Any]] = None
    best_dist: Optional[int] = None

    for cand_idx in (idx - 1, idx, idx + 1, idx + 2):
        if 0 <= cand_idx < len(blocks):
            addr, data = blocks[cand_idx]
            d = abs(addr - mv_abs)
            if d <= rng and (best_dist is None or d < best_dist):
                best = (addr, data)
                best_dist = d

    return best, idx
# ============================================================
# Move anchor collection
# ============================================================

def collect_move_anchors(buf: bytes, base_abs: int,
                         tbl_move_addrs: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    moves: List[Dict[str, Any]] = []
    seen_abs: set = set()
    seen_normal_key: Dict[int, int] = {}

    # Duplicates happen because the same normal can be found through the
    # chr_tbl pass, AIR/CMD lookahead, direct ANIM_HDR scan, and strict fallback.
    # Keep exactly one normal per displayed normal ID, and prefer the most
    # authoritative source.
    source_rank = {
        "table": 100,
        "anim_hdr": 80,
        "air_hdr": 70,
        "cmd_hdr": 70,
        "strict": 40,
        "legacy_special": 20,
        "unknown": 0,
    }

    def normal_key(kind: str, aid: Optional[int]) -> Optional[int]:
        if kind != "normal" or aid is None:
            return None
        low = aid & 0xFF
        return low if low in NORMAL_IDS else None

    def add_mv(kind: str, abs_addr: int, aid: Optional[int], source: str = "unknown") -> None:
        nkey = normal_key(kind, aid)

        if nkey is not None:
            existing_idx = seen_normal_key.get(nkey)
            if existing_idx is not None:
                old_mv = moves[existing_idx]
                old_rank = source_rank.get(old_mv.get("source", "unknown"), 0)
                new_rank = source_rank.get(source, 0)
                old_abs = old_mv.get("abs") or 0

                # Higher rank wins. Ties go to the earlier address, which is
                # usually the real block start instead of an interior fragment.
                if new_rank > old_rank or (new_rank == old_rank and abs_addr < old_abs):
                    seen_abs.discard(old_abs)
                    seen_abs.add(abs_addr)
                    moves[existing_idx] = {
                        "kind": kind,
                        "abs": abs_addr,
                        "id": aid,
                        "source": source,
                    }
                return

            if abs_addr in seen_abs:
                return
            seen_abs.add(abs_addr)
            seen_normal_key[nkey] = len(moves)
            moves.append({"kind": kind, "abs": abs_addr, "id": aid, "source": source})
            return

        if abs_addr in seen_abs:
            return
        seen_abs.add(abs_addr)
        moves.append({"kind": kind, "abs": abs_addr, "id": aid, "source": source})

    if tbl_move_addrs:
        for mv_abs in tbl_move_addrs:
            off = mv_abs - base_abs
            if 0 <= off < len(buf) - 4:
                aid = get_anim_id_after_hdr_strict(buf, off)
                if aid is None:
                    window = min(off + LOOKAHEAD_AFTER_HDR, len(buf))
                    for p in range(off, window - 4 + 1):
                        op  = buf[p + 2]
                        fps = buf[p + 3]
                        if fps == 0x3C and op in (0x01, 0x04):
                            hi, lo = buf[p], buf[p + 1]
                            candidate = (hi << 8) | lo
                            if 1 <= candidate <= 0x0500:
                                aid = candidate
                                break
                kind = "normal" if (aid and (aid & 0xFF) in NORMAL_IDS) else "special"
                add_mv(kind, mv_abs, aid, "table")
            else:
                add_mv("special", mv_abs, None, "table")

    i = 0
    while i < len(buf):
        if match_bytes(buf, i, SUPER_END_HDR):
            add_mv("super", base_abs + i, None, "super_end")
            i += len(SUPER_END_HDR)
            continue

        if match_bytes(buf, i, AIR_HDR):
            s0 = i + AIR_HDR_LEN
            s1 = min(s0 + LOOKAHEAD_AFTER_HDR, len(buf))
            p = s0
            while p < s1:
                if match_bytes(buf, p, ANIM_HDR):
                    aid = get_anim_id_after_hdr_strict(buf, p)
                    kind = "normal" if (aid and (aid & 0xFF) in NORMAL_IDS) else "special"
                    add_mv(kind, base_abs + p, aid, "air_hdr")
                    p += len(ANIM_HDR)
                    continue
                p += 1
            i += AIR_HDR_LEN
            continue

        if match_bytes(buf, i, CMD_HDR):
            s0 = i + CMD_HDR_LEN + 3
            s1 = min(s0 + LOOKAHEAD_AFTER_HDR, len(buf))
            p = s0
            while p < s1:
                if match_bytes(buf, p, ANIM_HDR):
                    aid = get_anim_id_after_hdr_strict(buf, p)
                    kind = "normal" if (aid and (aid & 0xFF) in NORMAL_IDS) else "special"
                    add_mv(kind, base_abs + p, aid, "cmd_hdr")
                    p += len(ANIM_HDR)
                    continue
                p += 1
            i += CMD_HDR_LEN
            continue

        if match_bytes(buf, i, ANIM_HDR):
            aid = get_anim_id_after_hdr_strict(buf, i)
            kind = "normal" if (aid and (aid & 0xFF) in NORMAL_IDS) else "special"
            add_mv(kind, base_abs + i, aid, "anim_hdr")
            i += len(ANIM_HDR)
            continue

        if i + 4 <= len(buf):
            if (buf[i] == 0x01 and buf[i + 2] == 0x01 and buf[i + 3] == 0x3C):
                lo = buf[i + 1]
                if 0x01 <= lo <= 0x1E:
                    add_mv("special", base_abs + i, 0x0100 | lo, "legacy_special")
                    i += 4
                    continue

        i += 1

    for pos, aid in find_strict_anim4(buf):
        if not looks_like_real_move_anchor(buf, pos):
            continue
        kind = "normal" if (aid & 0xFF) in NORMAL_IDS else "special"
        add_mv(kind, base_abs + pos, aid, "strict")

    return moves


# ============================================================
# Block collection
# ============================================================

def collect_blocks(buf: bytes, base_abs: int) -> Dict[str, Any]:
    # Meter packet confirmed from Ryu 5A in MEM2:
    #   34 04 00 20 00 00 00 03 00 00 00 00
    #   36 43 00 20 00 00 00 XX 00 00 00 04
    #                         ^^ meter value byte
    # Older code expected a second 36 43 00 20 segment and therefore missed
    # this real packet.  Store meter_addr as the direct editable value byte.
    METER_PREFIX = bytes([
        0x34, 0x04, 0x00, 0x20,
        0x00, 0x00, 0x00, 0x03,
        0x00, 0x00, 0x00, 0x00,
        0x36, 0x43, 0x00, 0x20,
        0x00, 0x00, 0x00,
    ])
    METER_VALUE_OFFSET = 0x13
    METER_SUFFIX_OFFSET = 0x14
    METER_SUFFIX = bytes([0x00, 0x00, 0x00, 0x04])
    METER_TOTAL_LEN = METER_SUFFIX_OFFSET + len(METER_SUFFIX)

    meters: List[Tuple[int, int]] = []
    active_blocks: List[Tuple[int, Tuple[int, int]]] = []
    inline_active_blocks: List[Tuple[int, Tuple[int, int]]] = []
    dmg_blocks: List[Tuple[int, Tuple[int, int]]] = []
    atkprop_blocks: List[Tuple[int, int]] = []
    hitreact_blocks: List[Tuple[int, int]] = []
    kb_blocks: List[Tuple[int, Dict[str, Any]]] = []
    stun_blocks: List[Tuple[int, Tuple[int, int, int]]] = []

    p = 0
    while p < len(buf):
        if (
            p + METER_TOTAL_LEN <= len(buf)
            and buf[p:p + len(METER_PREFIX)] == METER_PREFIX
            and buf[p + METER_SUFFIX_OFFSET:p + METER_TOTAL_LEN] == METER_SUFFIX
        ):
            value_off = p + METER_VALUE_OFFSET
            meters.append((base_abs + value_off, buf[value_off]))
            p += METER_TOTAL_LEN
            continue
        p += 1

    p = 0
    while p < len(buf):
        af = parse_active_frames(buf, p)
        if af:
            active_blocks.append((base_abs + p, af))
            p += ACTIVE_TOTAL_LEN
            continue
        p += 1

    p = 0
    while p < len(buf):
        af = parse_inline_active(buf, p)
        if af:
            inline_active_blocks.append((base_abs + p, af))
            p += INLINE_ACTIVE_LEN
            continue
        p += 1

    p = 0
    while p < len(buf):
        d = parse_damage(buf, p)
        if d:
            dmg_blocks.append((base_abs + p, d))
            p += DAMAGE_TOTAL_LEN
            continue
        p += 1

    p = 0
    while p < len(buf):
        d = parse_atkprop(buf, p)
        if d is not None:
            atkprop_blocks.append((base_abs + p, d))
            p += ATKPROP_TOTAL_LEN
            continue
        p += 1

    p = 0
    while p < len(buf):
        d = parse_hitreaction(buf, p)
        if d is not None:
            hitreact_blocks.append((base_abs + p, d))
            p += HITREACTION_TOTAL_LEN
            continue
        p += 1

    p = 0
    while p < len(buf):
        d = parse_knockback(buf, p)
        if d:
            kb_blocks.append((base_abs + p, d))
            p += KNOCKBACK_TOTAL_LEN
            continue
        p += 1

    p = 0
    while p < len(buf):
        d = parse_stun(buf, p)
        if d:
            stun_blocks.append((base_abs + p, d))
            p += STUN_TOTAL_LEN
            continue
        p += 1

    return {
        "meters": meters,
        "active_blocks": active_blocks,
        "inline_active_blocks": inline_active_blocks,
        "dmg_blocks": dmg_blocks,
        "atkprop_blocks": atkprop_blocks,
        "hitreact_blocks": hitreact_blocks,
        "kb_blocks": kb_blocks,
        "stun_blocks": stun_blocks,
    }
# ============================================================
# Field attachment
# ============================================================

def attach_move_fields(moves: List[Dict[str, Any]],
                       buf: bytes, base_abs: int,
                       blocks: Dict[str, Any],
                       char_id: Optional[int] = None) -> None:
    meters               = sorted(blocks["meters"], key=lambda x: x[0])
    active_blocks        = sorted(blocks["active_blocks"], key=lambda x: x[0])
    inline_active_blocks = sorted(blocks["inline_active_blocks"], key=lambda x: x[0])
    dmg_blocks           = sorted(blocks["dmg_blocks"], key=lambda x: x[0])
    atkprop_blocks       = sorted(blocks["atkprop_blocks"], key=lambda x: x[0])
    hitreact_blocks      = sorted(blocks["hitreact_blocks"], key=lambda x: x[0])
    kb_blocks            = sorted(blocks["kb_blocks"], key=lambda x: x[0])
    stun_blocks          = sorted(blocks["stun_blocks"], key=lambda x: x[0])

    meter_idx = 0
    active_idx = 0
    inline_idx = 0
    dmg_idx = 0
    atkprop_idx = 0
    hitreact_idx = 0
    kb_idx = 0
    stun_idx = 0

    moves.sort(key=lambda m: m.get("abs") or 0)

    for mv in moves:
        aid     = mv.get("id")
        mv_abs  = mv.get("abs") or 0
        aid_low = (aid & 0xFF) if aid is not None else None

        mv["meter"] = DEFAULT_METER.get(aid_low) if mv.get("kind") == "normal" else SPECIAL_DEFAULT_METER

        mblk, meter_idx = pick_best_block_from_idx(mv_abs, meters, meter_idx)
        mv["meter_addr"] = None
        if mblk:
            mv["meter"] = mblk[1]
            mv["meter_addr"] = mblk[0]

        mv["active_start"] = mv["active_end"] = mv["active_addr"] = None
        ablk, active_idx = pick_best_block_from_idx(mv_abs, active_blocks, active_idx)
        if ablk:
            mv["active_start"], mv["active_end"] = ablk[1]
            mv["active_addr"] = ablk[0]

        mv["active2_start"] = mv["active2_end"] = mv["active2_addr"] = None
        rel = mv_abs - base_abs
        inline_off = rel + INLINE_ACTIVE_OFF
        if 0 <= inline_off < len(buf) - INLINE_ACTIVE_LEN:
            a2 = parse_inline_active(buf, inline_off)
            if a2:
                mv["active2_start"], mv["active2_end"] = a2
                mv["active2_addr"] = base_abs + inline_off
        if mv["active2_start"] is None:
            a2blk, inline_idx = pick_best_block_from_idx(mv_abs, inline_active_blocks, inline_idx)
            if a2blk:
                mv["active2_start"], mv["active2_end"] = a2blk[1]
                mv["active2_addr"] = a2blk[0]

        mv["damage"] = mv["damage_flag"] = mv["damage_addr"] = None
        dblk, dmg_idx = pick_best_block_from_idx(mv_abs, dmg_blocks, dmg_idx)
        if dblk:
            mv["damage"], mv["damage_flag"] = dblk[1]
            mv["damage_addr"] = dblk[0]

        mv["attack_property"] = mv["atkprop_addr"] = None
        apblk, atkprop_idx = pick_best_block_from_idx(mv_abs, atkprop_blocks, atkprop_idx)
        if apblk:
            mv["attack_property"] = apblk[1]
            mv["atkprop_addr"] = apblk[0]

        mv["hit_reaction"] = mv["hit_reaction_addr"] = None
        hrblk, hitreact_idx = pick_best_block_from_idx(mv_abs, hitreact_blocks, hitreact_idx)
        if hrblk:
            mv["hit_reaction"] = hrblk[1]
            mv["hit_reaction_addr"] = hrblk[0] + HITREACTION_CODE_OFF

        mv["kb0"] = mv["kb1"] = mv["kb_traj"] = None
        mv["kb_type"] = mv["launch_profile"] = mv["kb_unknown"] = None
        mv["kb_x"] = mv["air_kb"] = mv["knockback_addr"] = None
        kbblk, kb_idx = pick_best_block_from_idx(mv_abs, kb_blocks, kb_idx)
        if kbblk:
            kb = kbblk[1]
            mv["knockback_addr"] = kbblk[0]
            mv["kb_type"] = kb.get("kb_type")
            mv["launch_profile"] = kb.get("launch_profile")
            mv["kb_unknown"] = kb.get("kb_unknown")
            mv["kb_x"] = kb.get("kb_x")
            mv["air_kb"] = kb.get("air_kb")

            # Legacy keys retained so old row-quality/tag logic does not break.
            mv["kb0"] = mv["launch_profile"]
            mv["kb1"] = mv["kb_type"]
            mv["kb_traj"] = None

        mv["hitstun"] = mv["blockstun"] = mv["hitstop"] = mv["stun_addr"] = None
        sblk, stun_idx = pick_best_block_from_idx(mv_abs, stun_blocks, stun_idx)
        if sblk:
            mv["hitstun"], mv["blockstun"], mv["hitstop"] = sblk[1]
            mv["stun_addr"] = sblk[0]

        mv["hb_x"] = mv["hb_y"] = None
        off_x = rel + HITBOX_OFF_X
        off_y = rel + HITBOX_OFF_Y
        if off_x + 4 <= len(buf):
            try:
                mv["hb_x"] = rd_f32_be(buf, off_x)
            except Exception:
                pass
        if off_y + 4 <= len(buf):
            try:
                mv["hb_y"] = rd_f32_be(buf, off_y)
            except Exception:
                pass

        total_frames = mv.get("speed") or 0x3C
        a_end = mv.get("active_end")
        recovery = max(0, total_frames - a_end) if a_end else 12
        hs = mv.get("hitstun") or 0
        bs = mv.get("blockstun") or 0
        mv["adv_hit"] = hs - recovery
        mv["adv_block"] = bs - recovery

        mv["move_name_source"] = "none"
        mv["normal_confirmed"] = False
        if aid is None:
            mv["move_name"] = "anim_--"
        else:
            name = None
            try:
                if char_id is not None:
                    name = lookup_move_name(aid, char_id)
                else:
                    name = lookup_move_name(aid)
            except TypeError:
                # Older move_id_map builds only accept aid.
                try:
                    name = lookup_move_name(aid)
                except Exception:
                    name = None
            except Exception:
                name = None

            if name:
                mv["move_name"] = name
                mv["move_name_source"] = "lookup"
            else:
                low = aid & 0xFF
                # Do not auto-name optional normals from raw ANIM_MAP fallback.
                # Otherwise non-normal 0x010E records appear as 6B for everyone.
                fallback = None if low in OPTIONAL_NORMAL_IDS else ANIM_MAP.get(low)
                mv["move_name"] = fallback if fallback else f"anim_{aid:04X}"
                mv["move_name_source"] = "anim_map" if fallback else "anim"

            low = aid & 0xFF
            if low in CORE_NORMAL_IDS:
                mv["normal_confirmed"] = True
            elif low in OPTIONAL_NORMAL_IDS:
                mv["normal_confirmed"] = (
                    str(mv.get("move_name_source") or "") == "lookup"
                    and str(mv.get("move_name") or "").strip().lower().replace(" ", "") == "6b"
                )



def move_quality_score(mv: Dict[str, Any]) -> Tuple[int, int, int, int, int, int]:
    """
    Score duplicate move records after fields are attached.

    Higher is better. This lets a populated duplicate, such as 2A's
    Tier3/Tier4 row, replace an empty parent anchor.
    """
    damage_ok = 1 if mv.get("damage") is not None else 0
    active_ok = 1 if mv.get("active_start") is not None and mv.get("active_end") is not None else 0
    stun_ok = 1 if mv.get("hitstun") is not None or mv.get("blockstun") is not None else 0
    kb_ok = 1 if mv.get("kb0") is not None or mv.get("kb1") is not None or mv.get("kb_traj") is not None else 0
    meter_ok = 1 if mv.get("meter_addr") is not None else 0

    source_rank = {
        "table": 100,
        "anim_hdr": 80,
        "air_hdr": 70,
        "cmd_hdr": 70,
        "strict": 40,
        "legacy_special": 20,
        "unknown": 0,
    }.get(mv.get("source", "unknown"), 0)

    # Primary value is actual usable frame-data completeness.
    # Source rank is only a tie-breaker now.
    return (
        damage_ok + active_ok + stun_ok + kb_ok + meter_ok,
        damage_ok,
        active_ok,
        stun_ok,
        kb_ok,
        source_rank,
    )


def collapse_duplicate_normals_by_quality(moves: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    best_by_key: Dict[int, Dict[str, Any]] = {}
    extras: List[Dict[str, Any]] = []

    for mv in moves:
        aid = mv.get("id")
        low = (aid & 0xFF) if aid is not None else None

        if aid is not None and low in NORMAL_IDS:
            if low in OPTIONAL_NORMAL_IDS and not bool(mv.get("normal_confirmed")):
                # Keep the row available as a special/system record, but do not
                # let it occupy the normal slot as a fake 6B.
                mv["kind"] = "special"
                extras.append(mv)
                continue

            old = best_by_key.get(low)
            if old is None or move_quality_score(mv) > move_quality_score(old):
                best_by_key[low] = mv
            continue

        extras.append(mv)

    for mv in best_by_key.values():
        mv["kind"] = "normal"

    return extras + list(best_by_key.values())

# ============================================================
# Sorting helpers
# ============================================================

def sort_key(m: Dict[str, Any]) -> Tuple[int, int, int]:
    aid      = m.get("id")
    abs_addr = m.get("abs") or 0
    if aid is None:
        return (2, 0xFFFF, abs_addr)
    if aid >= 0x0100:
        return (0, aid, abs_addr)
    return (1, aid, abs_addr)


def merge_by_abs(existing: List[Dict[str, Any]],
                 extra: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_abs: Dict[int, Dict[str, Any]] = {}
    for mv in existing:
        a = mv.get("abs")
        if a is not None and a not in by_abs:
            by_abs[a] = mv
    for mv in extra:
        a = mv.get("abs")
        if a is not None and a not in by_abs:
            by_abs[a] = mv
    return list(by_abs.values())


def count_special_like(moves_list: List[Dict[str, Any]]) -> int:
    return sum(1 for mv in moves_list if mv.get("kind") in ("special", "super"))


# ============================================================
# Per-slot region from chr_tbl bytes
# ============================================================

def slot_scan_region_from_tbl(tbl_buf: bytes,
                              tbl_mem_base: int,
                              chr_tbl_abs: int) -> Tuple[int, int]:
    tbl_off = abs_to_file_off(chr_tbl_abs, tbl_mem_base)
    if tbl_off < 0:
        return (chr_tbl_abs, chr_tbl_abs + 0x80000)

    act_rel = _find_chr_act_rel(tbl_buf, tbl_off)
    if act_rel is None:
        act_rel = min(CHR_TBL_SCAN_MAX_BYTES, len(tbl_buf) - tbl_off)

    max_offset = 0
    entry_count = max(0, act_rel // 4)

    for i in range(entry_count):
        off = tbl_off + i * 4
        if off + 4 > len(tbl_buf):
            break
        entry = rd_u32_be(tbl_buf, off)
        if entry == 0xFFFFFFFF:
            break
        if entry == 0:
            continue
        if entry % 4 != 0 or entry < MOVE_DATA_START_OFF:
            continue
        if entry > CHR_TBL_MAX_MOVE_OFFSET:
            continue
        if entry > max_offset:
            max_offset = entry

    region_start = chr_tbl_abs
    region_end = chr_tbl_abs + max_offset + SLOT_REGION_PAD
    region_end = min(region_end, MEM2_HI)

    return (region_start, region_end)

# ============================================================
# MAIN SCAN
# ============================================================

def scan_once():
    hook()

    slots_info = read_slots_from_constants()

    result: List[Dict[str, Any]] = []
    for _ in range(4):
        result.append({
            "slot_label": "",
            "char_name": "",
            "moves": [],
            "chr_tbl_abs": None,
            "tbl_move_count": 0,
        })

    for slot_idx, (slot_label, fighter_base_abs, cid, cname) in enumerate(slots_info):
        result[slot_idx]["slot_label"] = slot_label
        result[slot_idx]["char_name"] = cname

        if not fighter_base_abs or not is_mem2_addr(fighter_base_abs):
            continue

        fighter_buf = safe_rbytes(fighter_base_abs, FIGHTER_READ_SIZE)
        if not fighter_buf:
            continue

        verify_slot_id(fighter_buf, fighter_base_abs, fighter_base_abs, slot_idx)

        chr_tbl_abs = resolve_chr_tbl_from_live_memory(fighter_base_abs)
        if chr_tbl_abs is None:
            continue

        tbl_start = chr_tbl_abs - CHR_TBL_READ_PAD_BEFORE
        tbl_buf = safe_rbytes(tbl_start, CHR_TBL_READ_SIZE)
        if not tbl_buf:
            continue
        if not validate_chr_tbl(tbl_buf, tbl_start, chr_tbl_abs):
            continue

        tbl_move_addrs = parse_chr_tbl(tbl_buf, tbl_start, chr_tbl_abs)
        region_start, region_end = slot_scan_region_from_tbl(tbl_buf, tbl_start, chr_tbl_abs)

        region_buf = safe_rbytes(region_start, region_end - region_start)
        if not region_buf:
            continue

        in_slice = [a for a in tbl_move_addrs if region_start <= a < region_end]

        moves = collect_move_anchors(region_buf, region_start, tbl_move_addrs=in_slice)
        blocks = collect_blocks(region_buf, region_start)
        attach_move_fields(moves, region_buf, region_start, blocks, char_id=cid)
        moves = collapse_duplicate_normals_by_quality(moves)

        result[slot_idx] = {
            "slot_label": slot_label,
            "char_name": cname,
            "moves": sorted(moves, key=sort_key),
            "chr_tbl_abs": chr_tbl_abs,
            "tbl_move_count": len(tbl_move_addrs),
        }

    return result