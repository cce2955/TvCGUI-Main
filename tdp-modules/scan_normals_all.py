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

# ── chr_tbl pointer-table constants (from MEM2 analysis) ────────────────────
# The pointer to the chr_tbl block lives at fighter_base + 0x1E0.
CHR_TBL_PTR_OFF       = 0x1E0
# "chr_tbl\n" is stored at chr_tbl_base - 0x18.
CHR_TBL_LABEL_REL     = -0x18
# The table is always exactly 705 u32 entries = 0xB04 bytes.
CHR_TBL_LEN_BYTES     = 0xB04       # 705 * 4
CHR_TBL_NUM_ENTRIES   = 705
# entry[704] must be 0xFFFFFFFF (sentinel).
CHR_TBL_SENTINEL_REL  = 0xB00
# Immediately after the table: "chr_act\n".
CHR_ACT_LABEL_REL     = 0xB04
# entry[0] value observed in every slot: 0x3600.
CHR_TBL_ENTRY0_VAL    = 0x3600
# Move data start = chr_tbl_base + entry[0].
MOVE_DATA_START_OFF   = 0x3600

# Slot-stride facts: each slot base is 0x380020 bytes after the previous.
SLOT_STRIDE = 0x380020

# Slot-ID fields embedded inside each fighter base.
SLOT_ID_OFF_A = 0x04
SLOT_ID_OFF_B = 0x08

# How far to scan inside the fighter-base struct when +0x1E0 fails.
FIGHTER_BASE_SCAN_RANGE = 0x400

# How far to backtrack (in bytes) when a scan lands mid-block.
BACKTRACK_MAX = 0x20000

# ── move-record scanning ─────────────────────────────────────────────────────
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

# Normal animation ID mapping
ANIM_MAP = {
    0x00: "5A", 0x01: "5B", 0x02: "5C",
    0x03: "2A", 0x04: "2B", 0x05: "2C",
    0x06: "6C", 0x08: "3C",
    0x09: "j.A", 0x0A: "j.B", 0x0B: "j.C",
    0x0E: "6B",
}
NORMAL_IDS = set(ANIM_MAP.keys())

# Meter defaults
DEFAULT_METER = {
    0x00: 0x32, 0x03: 0x32, 0x09: 0x32,
    0x01: 0x64, 0x04: 0x64, 0x0A: 0x64,
    0x02: 0x96, 0x05: 0x96, 0x0B: 0x96,
    0x06: 0x96, 0x08: 0x96, 0x0E: 0x96,
}
SPECIAL_DEFAULT_METER = 0xC8

# ── dynamic block patterns ───────────────────────────────────────────────────
ACTIVE_HDR = [
    0x20, 0x35, 0x01, 0x20,
    0x3F, 0x00, 0x00, 0x00,
]
ACTIVE_TOTAL_LEN = 20

INLINE_ACTIVE_HDR = [
    0x3F, 0x00, 0x00, 0x00,  # 0–3
    None,                    # start frame
    0x11, 0x16, 0x20, 0x00,
    0x11, 0x22, 0x60, 0x00,
    0x00, 0x00, 0x00,
    None,                    # end frame
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

KNOCKBACK_HDR = [
    0x35, None, None, 0x20,
    0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00,
]
KNOCKBACK_TOTAL_LEN = 20

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


# ============================================================
# Low-level helpers
# ============================================================

def rd_u32_be(buf: bytes, off: int) -> int:
    """Read a big-endian u32 from a byte buffer at offset."""
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
    """Convert an absolute MEM2 address to an offset within a loaded buffer."""
    return abs_addr - mem_base


# ============================================================
# chr_tbl pointer-table validation and resolution
# ============================================================

def validate_chr_tbl(buf: bytes, mem_base: int, cand_abs: int) -> bool:
    """
    Validate that cand_abs is the true base of a chr_tbl block.

    All five conditions must hold (from MEM2 analysis notes):
      1. cand_abs is 0x20-aligned.
      2. "chr_tbl\\n" appears at cand_abs - 0x18.
      3. entry[0] == 0x3600.
      4. entry[704] == 0xFFFFFFFF  (sentinel at +0xB00).
      5. "chr_act\\n" immediately follows the table at +0xB04.
    """
    if cand_abs % 0x20 != 0:
        return False

    cand_off = abs_to_file_off(cand_abs, mem_base)

    # Label check at -0x18
    label_off = cand_off + CHR_TBL_LABEL_REL   # cand_off - 0x18
    if label_off < 0 or label_off + 8 > len(buf):
        return False
    if buf[label_off:label_off + 8] != b"chr_tbl\n":
        return False

    # entry[0] == 0x3600
    if cand_off + 4 > len(buf):
        return False
    if rd_u32_be(buf, cand_off) != CHR_TBL_ENTRY0_VAL:
        return False

    # sentinel entry[704] == 0xFFFFFFFF
    sent_off = cand_off + CHR_TBL_SENTINEL_REL   # +0xB00
    if sent_off + 4 > len(buf):
        return False
    if rd_u32_be(buf, sent_off) != 0xFFFFFFFF:
        return False

    # "chr_act\n" at +0xB04
    act_off = cand_off + CHR_ACT_LABEL_REL       # +0xB04
    if act_off + 8 > len(buf):
        return False
    if buf[act_off:act_off + 8] != b"chr_act\n":
        return False

    return True


def resolve_chr_tbl(buf: bytes, mem_base: int, fighter_base_abs: int) -> Optional[int]:
    """
    Primary path: read the pointer stored at fighter_base + 0x1E0,
    validate it as a chr_tbl base. Returns absolute address or None.

    Falls back to scanning the first FIGHTER_BASE_SCAN_RANGE bytes of
    the fighter struct for any valid MEM2 pointer that passes validation.
    """
    fb_off = abs_to_file_off(fighter_base_abs, mem_base)
    if fb_off < 0 or fb_off >= len(buf):
        return None

    # Primary: +0x1E0
    ptr_off = fb_off + CHR_TBL_PTR_OFF
    if ptr_off + 4 <= len(buf):
        cand = rd_u32_be(buf, ptr_off)
        if is_mem2_addr(cand) and validate_chr_tbl(buf, mem_base, cand):
            return cand

    # Fallback: scan struct for any MEM2 pointer that validates
    for delta in range(0, FIGHTER_BASE_SCAN_RANGE, 4):
        off = fb_off + delta
        if off + 4 > len(buf):
            break
        cand = rd_u32_be(buf, off)
        if is_mem2_addr(cand) and validate_chr_tbl(buf, mem_base, cand):
            return cand

    return None


def backtrack_to_chr_tbl(buf: bytes, mem_base: int, any_abs: int) -> Optional[int]:
    """
    Given an address that landed somewhere inside a chr_tbl block (e.g. a
    mid-struct pattern match), walk backwards in 4-byte steps until a valid
    chr_tbl base is found.  Used as a last-resort recovery after a global scan
    lands on a move-record header instead of the table start.
    """
    start = (any_abs // 4) * 4   # align down to 4-byte boundary
    for back in range(0, BACKTRACK_MAX, 4):
        cand = start - back
        if cand < mem_base:
            break
        if validate_chr_tbl(buf, mem_base, cand):
            return cand
    return None


def global_scan_chr_tbl(buf: bytes, mem_base: int,
                         fighter_base_abs: Optional[int] = None) -> Optional[int]:
    """
    Last-resort global scan: find every occurrence of b"chr_tbl\\n" in the
    buffer, convert to candidate base (hit + 0x18), validate, and return the
    best match (closest to fighter_base_abs if known).
    """
    label = b"chr_tbl\n"
    best: Optional[int] = None
    best_dist: int = 0x7FFFFFFF

    p = 0
    while True:
        idx = buf.find(label, p)
        if idx == -1:
            break
        cand_abs = mem_base + idx + 0x18
        if validate_chr_tbl(buf, mem_base, cand_abs):
            if fighter_base_abs is not None:
                dist = abs(cand_abs - fighter_base_abs)
                if dist < best_dist:
                    best_dist = dist
                    best = cand_abs
            else:
                best = cand_abs   # return first valid hit if no proximity hint
                break
        p = idx + 1

    return best


# ============================================================
# chr_tbl move-table parser
# ============================================================

def parse_chr_tbl(buf: bytes, mem_base: int, chr_tbl_abs: int) -> List[int]:
    """
    Parse the 705-entry chr_tbl offset table.

    Returns a list of absolute addresses for every non-null, non-sentinel
    entry.  The table is strictly bounded: exactly 705 u32 entries, ending
    with 0xFFFFFFFF at index 704.  Entry values are offsets relative to
    chr_tbl_abs.  This function enforces all hard bounds and ignores any
    value that looks structurally invalid (zero, already-sentinel, unaligned,
    or below MOVE_DATA_START_OFF).
    """
    tbl_off = abs_to_file_off(chr_tbl_abs, mem_base)
    if tbl_off < 0 or tbl_off + CHR_TBL_LEN_BYTES > len(buf):
        return []

    moves: List[int] = []
    for i in range(CHR_TBL_NUM_ENTRIES):
        entry = rd_u32_be(buf, tbl_off + i * 4)

        # Sentinel must be at index 704; stop unconditionally here.
        if i == CHR_TBL_NUM_ENTRIES - 1:
            # (sentinel already validated in validate_chr_tbl, so just stop)
            break

        # Skip null, sentinel-like, unaligned, or below move data start.
        if entry == 0 or entry == 0xFFFFFFFF:
            continue
        if entry % 4 != 0:
            continue
        if entry < MOVE_DATA_START_OFF:
            continue

        move_abs = chr_tbl_abs + entry
        if is_mem2_addr(move_abs):
            moves.append(move_abs)

    return moves


# ============================================================
# Slot model
# ============================================================

def read_slots_from_constants() -> List[Tuple[str, int, Optional[int], str]]:
    """Read the four fighter base addresses from the SLOTS constant table."""
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
    """
    Optional sanity check: confirm that the slot-ID fields embedded in the
    fighter struct (+0x04 and +0x08) match expected_slot.
    """
    fb_off = abs_to_file_off(fighter_base_abs, mem_base)
    if fb_off < 0 or fb_off + 0x0C > len(buf):
        return True   # can't verify → don't reject
    id_a = rd_u32_be(buf, fb_off + SLOT_ID_OFF_A)
    id_b = rd_u32_be(buf, fb_off + SLOT_ID_OFF_B)
    return (id_a == expected_slot) and (id_b == expected_slot)


# ============================================================
# Move-record scanning utilities  (unchanged in logic)
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
# Data block parsers  (unchanged)
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


def parse_knockback(buf: bytes, pos: int) -> Optional[Tuple[int, int, int]]:
    if pos + KNOCKBACK_TOTAL_LEN > len(buf):
        return None
    return (buf[pos + 1], buf[pos + 2], buf[pos + 12])


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


# ============================================================
# Move anchor collection  (region-based, uses chr_tbl addresses)
# ============================================================

def collect_move_anchors(buf: bytes, base_abs: int,
                          tbl_move_addrs: Optional[List[int]] = None
                          ) -> List[Dict[str, Any]]:
    """
    Collect move anchors by scanning the region buffer [base_abs, base_abs+len(buf)).

    If tbl_move_addrs is provided (absolute addresses from parse_chr_tbl),
    those are seeded directly as canonical anchors before pattern scanning.
    Pattern scanning still runs over the same buffer to catch anything the
    table doesn't cover (e.g. dynamic super entries), but table-seeded addrs
    are never discarded.
    """
    moves: List[Dict[str, Any]] = []
    seen_abs: set = set()

    def add_mv(kind: str, abs_addr: int, aid: Optional[int]) -> None:
        if abs_addr in seen_abs:
            return
        seen_abs.add(abs_addr)
        moves.append({"kind": kind, "abs": abs_addr, "id": aid})

    # ── Seed from chr_tbl offset table (highest priority) ─────────────────
    if tbl_move_addrs:
        for mv_abs in tbl_move_addrs:
            # Infer kind/id by peeking at the bytes at that address.
            off = mv_abs - base_abs
            if 0 <= off < len(buf) - 4:
                aid = get_anim_id_after_hdr_strict(buf, off)
                if aid is None:
                    # Try a short local scan for an anim4 token
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
                kind = ("normal"
                        if (aid and (aid & 0xFF) in NORMAL_IDS)
                        else "special")
                add_mv(kind, mv_abs, aid)
            else:
                add_mv("special", mv_abs, None)

    # ── Pattern scan over the full region buffer ───────────────────────────
    i = 0
    while i < len(buf):
        if match_bytes(buf, i, SUPER_END_HDR):
            add_mv("super", base_abs + i, None)
            i += len(SUPER_END_HDR)
            continue

        if match_bytes(buf, i, AIR_HDR):
            s0 = i + AIR_HDR_LEN
            s1 = min(s0 + LOOKAHEAD_AFTER_HDR, len(buf))
            p = s0
            while p < s1:
                if match_bytes(buf, p, ANIM_HDR):
                    aid  = get_anim_id_after_hdr_strict(buf, p)
                    kind = ("normal" if (aid and (aid & 0xFF) in NORMAL_IDS)
                            else "special")
                    add_mv(kind, base_abs + p, aid)
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
                    aid  = get_anim_id_after_hdr_strict(buf, p)
                    kind = ("normal" if (aid and (aid & 0xFF) in NORMAL_IDS)
                            else "special")
                    add_mv(kind, base_abs + p, aid)
                    p += len(ANIM_HDR)
                    continue
                p += 1
            i += CMD_HDR_LEN
            continue

        if match_bytes(buf, i, ANIM_HDR):
            aid  = get_anim_id_after_hdr_strict(buf, i)
            kind = ("normal" if (aid and (aid & 0xFF) in NORMAL_IDS)
                    else "special")
            add_mv(kind, base_abs + i, aid)
            i += len(ANIM_HDR)
            continue

        if i + 4 <= len(buf):
            if (buf[i] == 0x01 and buf[i + 2] == 0x01 and buf[i + 3] == 0x3C):
                lo = buf[i + 1]
                if 0x01 <= lo <= 0x1E:
                    add_mv("special", base_abs + i, 0x0100 | lo)
                    i += 4
                    continue

        i += 1

    # Pass 1B: raw anim4 sweep
    for pos, aid in find_strict_anim4(buf):
        if not looks_like_real_move_anchor(buf, pos):
            continue
        kind = ("normal" if (aid & 0xFF) in NORMAL_IDS else "special")
        add_mv(kind, base_abs + pos, aid)

    return moves


# ============================================================
# Block collection for a region  (unchanged)
# ============================================================

def collect_blocks(buf: bytes, base_abs: int) -> Dict[str, Any]:
    METER_HDR = [
        0x34, 0x04, 0x00, 0x20,
        0x00, 0x00, 0x00, 0x03,
        0x00, 0x00, 0x00, 0x00,
        0x36, 0x43, 0x00, 0x20,
        0x00, 0x00, 0x00,
        0x36, 0x43, 0x00, 0x20,
        0x00, 0x00, 0x00,
    ]
    METER_TOTAL_LEN = len(METER_HDR) + 5

    meters: List[Tuple[int, int]] = []
    active_blocks: List[Tuple[int, Tuple[int, int]]] = []
    inline_active_blocks: List[Tuple[int, Tuple[int, int]]] = []
    dmg_blocks: List[Tuple[int, Tuple[int, int]]] = []
    atkprop_blocks: List[Tuple[int, int]] = []
    hitreact_blocks: List[Tuple[int, int]] = []
    kb_blocks: List[Tuple[int, Tuple[int, int, int]]] = []
    stun_blocks: List[Tuple[int, Tuple[int, int, int]]] = []

    p = 0
    while p < len(buf):
        if match_bytes(buf, p, METER_HDR) and p + METER_TOTAL_LEN <= len(buf):
            meters.append((base_abs + p, buf[p + len(METER_HDR)]))
            p += len(METER_HDR)
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
# Field attachment  (unchanged logic, receives same dicts)
# ============================================================

def attach_move_fields(moves: List[Dict[str, Any]],
                       buf: bytes, base_abs: int,
                       blocks: Dict[str, Any]) -> None:
    meters               = blocks["meters"]
    active_blocks        = blocks["active_blocks"]
    inline_active_blocks = blocks["inline_active_blocks"]
    dmg_blocks           = blocks["dmg_blocks"]
    atkprop_blocks       = blocks["atkprop_blocks"]
    hitreact_blocks      = blocks["hitreact_blocks"]
    kb_blocks            = blocks["kb_blocks"]
    stun_blocks          = blocks["stun_blocks"]

    for mv in moves:
        aid     = mv.get("id")
        mv_abs  = mv.get("abs") or 0
        aid_low = (aid & 0xFF) if aid is not None else None

        mv["meter"] = (DEFAULT_METER.get(aid_low)
                       if mv.get("kind") == "normal"
                       else SPECIAL_DEFAULT_METER)

        mblk = pick_best_block(mv_abs, meters)
        mv["meter_addr"] = None
        if mblk:
            mv["meter"] = mblk[1]
            mv["meter_addr"] = mblk[0]

        mv["active_start"] = mv["active_end"] = mv["active_addr"] = None
        ablk = pick_best_block(mv_abs, active_blocks)
        if ablk:
            mv["active_start"], mv["active_end"] = ablk[1]
            mv["active_addr"] = ablk[0]

        mv["active2_start"] = mv["active2_end"] = mv["active2_addr"] = None
        rel        = mv_abs - base_abs
        inline_off = rel + INLINE_ACTIVE_OFF
        if 0 <= inline_off < len(buf) - INLINE_ACTIVE_LEN:
            a2 = parse_inline_active(buf, inline_off)
            if a2:
                mv["active2_start"], mv["active2_end"] = a2
                mv["active2_addr"] = base_abs + inline_off
        if mv["active2_start"] is None:
            a2blk = pick_best_block(mv_abs, inline_active_blocks)
            if a2blk:
                mv["active2_start"], mv["active2_end"] = a2blk[1]
                mv["active2_addr"] = a2blk[0]

        mv["damage"] = mv["damage_flag"] = mv["damage_addr"] = None
        dblk = pick_best_block(mv_abs, dmg_blocks)
        if dblk:
            mv["damage"], mv["damage_flag"] = dblk[1]
            mv["damage_addr"] = dblk[0]

        mv["attack_property"] = mv["atkprop_addr"] = None
        apblk = pick_best_block(mv_abs, atkprop_blocks)
        if apblk:
            mv["attack_property"] = apblk[1]
            mv["atkprop_addr"]    = apblk[0]

        mv["hit_reaction"] = mv["hit_reaction_addr"] = None
        hrblk = pick_best_block(mv_abs, hitreact_blocks)
        if hrblk:
            mv["hit_reaction"]      = hrblk[1]
            mv["hit_reaction_addr"] = hrblk[0] + HITREACTION_CODE_OFF

        mv["kb0"] = mv["kb1"] = mv["kb_traj"] = mv["knockback_addr"] = None
        kbblk = pick_best_block(mv_abs, kb_blocks)
        if kbblk:
            mv["kb0"], mv["kb1"], mv["kb_traj"] = kbblk[1]
            mv["knockback_addr"] = kbblk[0]

        mv["hitstun"] = mv["blockstun"] = mv["hitstop"] = mv["stun_addr"] = None
        sblk = pick_best_block(mv_abs, stun_blocks)
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
        a_end    = mv.get("active_end")
        recovery = max(0, total_frames - a_end) if a_end else 12
        hs = mv.get("hitstun") or 0
        bs = mv.get("blockstun") or 0
        mv["adv_hit"]   = hs - recovery
        mv["adv_block"] = bs - recovery

        if aid is None:
            mv["move_name"] = "anim_--"
        else:
            name = lookup_move_name(aid)
            if not name:
                name = ANIM_MAP.get(aid & 0xFF)
            mv["move_name"] = name if name else f"anim_{aid:04X}"


# ============================================================
# Sorting / merging helpers
# ============================================================

def sort_key(m: Dict[str, Any]) -> Tuple[int, int, int]:
    aid     = m.get("id")
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
# Per-slot scan region derived from chr_tbl boundaries
# ============================================================

def slot_scan_region(chr_tbl_abs: int,
                     buf: bytes,
                     mem_base: int) -> Tuple[int, int]:
    """
    Derive the scan region for a slot directly from its chr_tbl base.

    The move data starts at chr_tbl_abs + MOVE_DATA_START_OFF (= +0x3600).
    The furthest move address is chr_tbl_abs + max_offset, where max_offset is
    the largest non-sentinel entry in the table.

    We add a small pad on the high end for inline blocks that live just past
    the last move record.
    """
    PAD = 0x2000

    tbl_off = abs_to_file_off(chr_tbl_abs, mem_base)
    if tbl_off < 0:
        # fallback: generic window
        return (chr_tbl_abs, chr_tbl_abs + 0x80000)

    max_offset = 0
    for i in range(CHR_TBL_NUM_ENTRIES - 1):   # skip sentinel at 704
        entry = rd_u32_be(buf, tbl_off + i * 4)
        if entry in (0, 0xFFFFFFFF):
            continue
        if entry % 4 != 0 or entry < MOVE_DATA_START_OFF:
            continue
        if entry > max_offset:
            max_offset = entry

    region_start = chr_tbl_abs                          # include chr_tbl header
    region_end   = chr_tbl_abs + max_offset + PAD
    region_end   = min(region_end, mem_base + len(buf)) # clamp to buffer

    return (region_start, region_end)


# ============================================================
# MAIN SCAN  —  pointer-table driven
# ============================================================

def scan_once():
    hook()

    slots_info = read_slots_from_constants()

    # Load the full MEM2 range that covers all fighter bases and their move data.
    # The four fighter bases span from 0x9246B9C0 to 0x92EEBA20+struct_size,
    # and their move data can reach up to ~0x909DECAC (per the analysis table).
    # We load a conservative window that covers all of it comfortably.
    MEM_BASE = MEM2_LO                              # 0x90000000
    MEM_SIZE = MEM2_HI - MEM2_LO                   # full 64 MiB
    mem = rbytes(MEM_BASE, MEM_SIZE)

    result: List[Dict[str, Any]] = []
    for _ in range(4):
        result.append({"slot_label": "", "char_name": "", "moves": [],
                        "chr_tbl_abs": None, "tbl_move_count": 0})

    for slot_idx, (slot_label, fighter_base_abs, cid, cname) in enumerate(slots_info):
        if not fighter_base_abs or not is_mem2_addr(fighter_base_abs):
            result[slot_idx].update({"slot_label": slot_label, "char_name": cname})
            continue

        # ── Step 1: resolve chr_tbl via fighter_base + 0x1E0 ─────────────
        chr_tbl_abs = resolve_chr_tbl(mem, MEM_BASE, fighter_base_abs)

        # ── Step 2: fallback – global scan anchored near fighter_base ─────
        if chr_tbl_abs is None:
            chr_tbl_abs = global_scan_chr_tbl(mem, MEM_BASE, fighter_base_abs)

        if chr_tbl_abs is None:
            # Completely failed to find a chr_tbl for this slot.
            result[slot_idx].update({"slot_label": slot_label, "char_name": cname})
            continue

        # ── Step 3: optional slot-ID sanity check ─────────────────────────
        verify_slot_id(mem, MEM_BASE, fighter_base_abs, slot_idx)
        # (we don't abort on mismatch — just trust the pointer chain)

        # ── Step 4: parse the 705-entry offset table → absolute addresses ─
        tbl_move_addrs = parse_chr_tbl(mem, MEM_BASE, chr_tbl_abs)

        # ── Step 5: derive tight scan region from chr_tbl bounds ──────────
        region_start, region_end = slot_scan_region(chr_tbl_abs, mem, MEM_BASE)

        start_off = abs_to_file_off(region_start, MEM_BASE)
        end_off   = abs_to_file_off(region_end,   MEM_BASE)
        start_off = max(0, start_off)
        end_off   = min(len(mem), end_off)

        buf_slice = mem[start_off:end_off]
        slice_base_abs = MEM_BASE + start_off

        # ── Step 6: collect move anchors, seeding from tbl_move_addrs ─────
        # Filter table addresses to those that fall within our slice.
        in_slice = [a for a in tbl_move_addrs
                    if slice_base_abs <= a < slice_base_abs + len(buf_slice)]

        moves = collect_move_anchors(buf_slice, slice_base_abs,
                                     tbl_move_addrs=in_slice)

        # ── Step 7: collect and attach data blocks ─────────────────────────
        blocks = collect_blocks(buf_slice, slice_base_abs)
        attach_move_fields(moves, buf_slice, slice_base_abs, blocks)

        moves_sorted = sorted(moves, key=sort_key)

        result[slot_idx] = {
            "slot_label":    slot_label,
            "char_name":     cname,
            "moves":         moves_sorted,
            "chr_tbl_abs":   chr_tbl_abs,
            "tbl_move_count": len(tbl_move_addrs),
        }

    return result