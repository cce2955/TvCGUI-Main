import copy
import hashlib
import json
import os
import struct
import sys
import threading
import time
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

# Proven static startup-invulnerability phase signature.
#
#   04 01 60 00 00 00 12 18 3F 00 00 00 00 00 NN 00
#
# The signature is action-owned: Ryu Shoryu L/M/H use 6/10/13 and Jun 6B
# uses 20. Values 0-2 are ordinary phase housekeeping and are suppressed.
INVULN_SIGNATURE_HDR = bytes([
    0x04, 0x01, 0x60, 0x00,
    0x00, 0x00, 0x12, 0x18,
    0x3F, 0x00, 0x00, 0x00,
])
INVULN_SIGNATURE_MIN_FRAMES = 3
INVULN_SIGNATURE_ROOT_BACKTRACK = 8
INVULN_SIGNATURE_PROFILE_REVISION = 1

HITBOX_OFF_X = 0x40
HITBOX_OFF_Y = 0x48

FIGHTER_READ_SIZE = 0x400
CHR_TBL_READ_PAD_BEFORE = 0x18
CHR_TBL_READ_SIZE = CHR_TBL_READ_PAD_BEFORE + CHR_TBL_SCAN_MAX_BYTES + 0x20
SLOT_REGION_PAD = 0x2000

# ============================================================
# Frame-data profile cache
# ============================================================

# The dynamic scanner is still the source of truth.  This profile cache records
# the resolved per-character relative offsets after a successful dynamic scan,
# then future scans rebase those offsets against the live chr_tbl address and
# read only the exact packets/fields the editor needs.  Delete
# frame_data_profiles.json or set TVC_FD_PROFILE_CACHE=0 to force the legacy
# dynamic path.
PROFILE_CACHE_VERSION = 1
PROFILE_SCANNER_BUILD = "fd-profile-v2-invuln1218"
PROFILE_CACHE_FILENAME = "frame_data_profiles.json"
PROFILE_CACHE_ENABLED = str(os.environ.get("TVC_FD_PROFILE_CACHE", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _profile_module_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def _profile_is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _profile_exe_dir() -> str:
    if _profile_is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return _profile_module_dir()


def _profile_bundle_dir() -> Optional[str]:
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return os.path.abspath(str(base))
    return None


def _profile_default_cache_file() -> str:
    # Source/dev runs keep the cache next to scan_normals_all.py so the build
    # script can bundle the profiles you already generated.  PyInstaller onefile
    # runs use a writable cache next to TvCGUI.exe, while still reading the
    # bundled read-only seed profile from sys._MEIPASS.
    return os.path.join(_profile_exe_dir(), PROFILE_CACHE_FILENAME)


def _profile_default_bundled_file() -> Optional[str]:
    bundle_dir = _profile_bundle_dir()
    if bundle_dir:
        return os.path.join(bundle_dir, PROFILE_CACHE_FILENAME)
    source_file = os.path.join(_profile_module_dir(), PROFILE_CACHE_FILENAME)
    return source_file if os.path.exists(source_file) else None


PROFILE_CACHE_FILE = os.environ.get("TVC_FD_PROFILE_CACHE_FILE", _profile_default_cache_file())
PROFILE_BUNDLED_CACHE_FILE = os.environ.get("TVC_FD_PROFILE_BUNDLED_FILE", _profile_default_bundled_file() or "")
PROFILE_CACHE_LOCK_FILE = os.environ.get(
    "TVC_FD_PROFILE_CACHE_LOCK_FILE",
    PROFILE_CACHE_FILE + ".lock",
)
PROFILE_CACHE_SAVE_TIMEOUT_SEC = float(os.environ.get("TVC_FD_PROFILE_SAVE_TIMEOUT", "2.0") or "2.0")
PROFILE_CACHE_STALE_LOCK_SEC = float(os.environ.get("TVC_FD_PROFILE_STALE_LOCK_SEC", "30.0") or "30.0")

_PROFILE_CACHE_LOCK = threading.RLock()
_PROFILE_CACHE_DOC: Optional[Dict[str, Any]] = None
_PROFILE_SAVE_WARNED: set[str] = set()

_PROFILE_ADDRESS_KEYS = {
    "abs", "parent_abs", "addr",
    # Extra-profile rows carry several resolved script/dispatch addresses that
    # do not use the usual *_addr spelling. Keep their cached values relative
    # to chr_tbl too, so the profile survives a new match/slot allocation.
    "child_target", "slot_base", "slot_end",
    "dispatch_range_start", "dispatch_range_end",
    "super_entry_addr", "owned_scan_start", "owned_scan_end",
    "packet_addr", "owner_root", "owner_post_link",
}
_PROFILE_ADDRESS_SUFFIXES = ("_addr",)


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


def parse_chr_tbl_action_entries(buf: bytes, mem_base: int, chr_tbl_abs: int) -> List[Tuple[int, int]]:
    """Return canonical ``(action_id, script_root)`` pairs from chr_tbl.

    The normal move scanner deliberately deduplicates roots. The invuln
    signature needs the original table index as well, because Shoryu begins
    eight bytes before its action root and 6B carries the packet later in the
    action interval.
    """
    tbl_off = abs_to_file_off(chr_tbl_abs, mem_base)
    if tbl_off < 0 or tbl_off + 4 > len(buf):
        return []
    act_rel = _find_chr_act_rel(buf, tbl_off)
    if act_rel is None:
        act_rel = min(CHR_TBL_SCAN_MAX_BYTES, len(buf) - tbl_off)

    entries: List[Tuple[int, int]] = []
    for action_id in range(max(0, act_rel // 4)):
        off = tbl_off + action_id * 4
        if off + 4 > len(buf):
            break
        entry = rd_u32_be(buf, off)
        if entry == 0xFFFFFFFF:
            break
        if entry == 0 or entry % 4 != 0:
            continue
        if entry < MOVE_DATA_START_OFF or entry > CHR_TBL_MAX_MOVE_OFFSET:
            continue
        root = chr_tbl_abs + entry
        if is_mem2_addr(root):
            entries.append((int(action_id), int(root)))
    return entries

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
    """Parse the hitstun/blockstun/hitstop packet.

    The old parser required a trailing ``04 15 60`` after the hitstop byte.
    Ryu 6B's second hit in the supplied MEM2 dump has a valid stun packet but
    flows directly into the next command packet instead of that trailer.  The
    editable values are still at the same confirmed offsets, so validate the
    strong packet body and treat the trailer as optional.
    """
    min_len = 39  # last editable byte is pos + 38
    if pos + min_len > len(buf):
        return None

    loose_hdr = STUN_HDR[:39]
    if not match_bytes(buf, pos, loose_hdr):
        return None

    return (buf[pos + 15], buf[pos + 31], buf[pos + 38])


def _invuln_signature_frames(raw_value: int) -> Optional[int]:
    """Decode the exact +0x1218 payload: ``00 00 NN 00``."""
    try:
        raw = int(raw_value) & 0xFFFFFFFF
    except Exception:
        return None
    if raw & 0xFFFF00FF:
        return None
    frames = (raw >> 8) & 0xFF
    return frames if frames >= INVULN_SIGNATURE_MIN_FRAMES else None


def collect_invuln_signature_map(
    buf: bytes,
    base_abs: int,
    action_entries: Sequence[Tuple[int, int]],
) -> Tuple[Dict[int, List[Dict[str, Any]]], Dict[int, List[Dict[str, Any]]]]:
    """Scan each canonical action interval for the exact +0x1218 signature.

    The interval ends at the next distinct chr_tbl root. This prevents a row
    from inheriting a neighbor's terminal phase timer. Eight bytes of backtrack
    catch Shoryu's root-leading packet.
    """
    valid: List[Tuple[int, int]] = []
    for action_id, root in action_entries or ():
        try:
            aid, addr = int(action_id), int(root)
        except Exception:
            continue
        rel = addr - int(base_abs)
        if 0 <= rel < len(buf):
            valid.append((aid, addr))
    if not valid:
        return {}, {}

    roots = sorted({root for _aid, root in valid})
    by_root: Dict[int, List[Dict[str, Any]]] = {}
    for index, root in enumerate(roots):
        next_root = roots[index + 1] if index + 1 < len(roots) else int(base_abs) + len(buf)
        start_abs = max(int(base_abs), int(root) - INVULN_SIGNATURE_ROOT_BACKTRACK)
        end_abs = min(int(base_abs) + len(buf), max(start_abs, int(next_root)))
        start_rel = start_abs - int(base_abs)
        end_rel = end_abs - int(base_abs)
        hits: List[Dict[str, Any]] = []
        pos = start_rel
        while True:
            idx = buf.find(INVULN_SIGNATURE_HDR, pos, end_rel)
            if idx < 0:
                break
            pos = idx + 1
            if idx + 16 > end_rel:
                continue
            raw = rd_u32_be(buf, idx + 12)
            frames = _invuln_signature_frames(raw)
            if frames is None:
                continue
            hits.append({
                "packet_addr": int(base_abs) + idx,
                "value_addr": int(base_abs) + idx + 12,
                "raw_value": int(raw),
                "frames": int(frames),
                "action_root": int(root),
            })
        if hits:
            by_root[int(root)] = hits

    by_action: Dict[int, List[Dict[str, Any]]] = {}
    for action_id, root in valid:
        hits = by_root.get(int(root))
        if hits:
            by_action[int(action_id)] = [dict(hit, action_id=int(action_id)) for hit in hits]
    return by_action, by_root


def summarize_invuln_signatures(signatures: Sequence[Dict[str, Any]]) -> str:
    values: List[int] = []
    for signature in signatures or ():
        try:
            frames = int(signature.get("frames"))
        except Exception:
            continue
        if frames >= INVULN_SIGNATURE_MIN_FRAMES and frames not in values:
            values.append(frames)
    return " / ".join(f"{frames}f" for frames in values)


def apply_invuln_signatures_to_moves(
    moves: Sequence[Dict[str, Any]],
    by_action: Dict[int, List[Dict[str, Any]]],
    by_root: Dict[int, List[Dict[str, Any]]],
) -> None:
    """Attach action-owned signature hits without changing move discovery."""
    for mv in moves or ():
        hits: List[Dict[str, Any]] = []
        try:
            root_hits = by_root.get(int(mv.get("abs") or 0))
        except Exception:
            root_hits = None
        if root_hits:
            hits = [dict(hit) for hit in root_hits]
        if not hits:
            try:
                action_hits = by_action.get(int(mv.get("id")))
            except Exception:
                action_hits = None
            if action_hits:
                hits = [dict(hit) for hit in action_hits]

        mv["invuln_signatures"] = hits
        mv["invuln_signature_count"] = len(hits)
        mv["invuln"] = summarize_invuln_signatures(hits)
        mv["invuln_addr"] = int(hits[0]["value_addr"]) if hits else None


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


def next_any_anchor_boundary(moves: List[Dict[str, Any]],
                             mv_abs: int,
                             *,
                             min_gap: int = 0x80,
                             fallback_len: int = PAIR_RANGE) -> int:
    """Return the next scanner anchor after this row.

    This is intentionally tighter than _next_real_boundary_for_move.  It is used
    only for scalar field pairing, where borrowing a previous/neighbor packet is
    worse than showing a blank cell.  Ryu Tatsu exposed this: Spin and End rows
    were inheriting Start/previous-hit stun because the old nearest-neighbor
    matcher allowed backwards matches.
    """
    best: Optional[int] = None
    for row in moves:
        try:
            addr = int(row.get("abs") or 0)
        except Exception:
            continue
        if addr <= mv_abs + min_gap:
            continue
        if best is None or addr < best:
            best = addr
    return best if best is not None else mv_abs + fallback_len


def first_block_forward(blocks: List[Tuple[int, Any]],
                        start_addr: int,
                        end_addr: int,
                        *,
                        max_gap: int = PAIR_RANGE) -> Optional[Tuple[int, Any]]:
    if not blocks:
        return None
    limit = min(end_addr, start_addr + max_gap)
    for addr, data in blocks:
        if start_addr <= addr < limit:
            return (addr, data)
    return None


def pair_forward_hit_fields_for_move(mv: Dict[str, Any],
                                     moves: List[Dict[str, Any]],
                                     blocks: Dict[str, Any]) -> None:
    """Tighter scalar packet pairing for rows that are not full real anchors.

    The legacy matcher picked the nearest block by absolute distance.  That made
    nearby phased scripts look populated, but it also caused wrong writes: a
    Tatsu Spin row could point at Tatsu Start stun, and a Tatsu End row could
    point back at Spin damage.

    This pass pairs packets in the real script order:
        move anchor -> active -> damage -> attack/reaction -> KB -> stun
    and stops at the next scanner anchor.  If a section has no forward hit
    packet, it stays blank instead of borrowing the previous section's address.
    """
    try:
        mv_abs = int(mv.get("abs") or 0)
    except Exception:
        mv_abs = 0
    if not mv_abs:
        return

    boundary = next_any_anchor_boundary(moves, mv_abs, fallback_len=PAIR_RANGE)

    active_blocks = sorted(blocks.get("active_blocks") or [], key=lambda x: x[0])
    dmg_blocks = sorted(blocks.get("dmg_blocks") or [], key=lambda x: x[0])
    atkprop_blocks = sorted(blocks.get("atkprop_blocks") or [], key=lambda x: x[0])
    hitreact_blocks = sorted(blocks.get("hitreact_blocks") or [], key=lambda x: x[0])
    kb_blocks = sorted(blocks.get("kb_blocks") or [], key=lambda x: x[0])
    stun_blocks = sorted(blocks.get("stun_blocks") or [], key=lambda x: x[0])

    ablk = first_block_forward(active_blocks, mv_abs, boundary, max_gap=PAIR_RANGE)
    if ablk:
        mv["active_start"], mv["active_end"] = ablk[1]
        mv["active_addr"] = ablk[0]
    else:
        mv["active_start"] = mv["active_end"] = mv["active_addr"] = None

    data_start = ablk[0] if ablk else mv_abs
    dblk = first_block_forward(dmg_blocks, data_start, boundary, max_gap=HIT_SEGMENT_MAX_GAP)
    if dblk:
        mv["damage"], mv["damage_flag"] = dblk[1]
        mv["damage_addr"] = dblk[0]
    else:
        mv["damage"] = mv["damage_flag"] = mv["damage_addr"] = None
        # No concrete hit bundle for this row.  Clear dependent hit fields so
        # the UI does not offer writes to a neighbor's packet.
        mv["attack_property"] = mv["atkprop_addr"] = mv["attack_property_addr"] = None
        mv["hit_reaction"] = mv["hit_reaction_addr"] = None
        mv["kb0"] = mv["kb1"] = mv["kb_traj"] = None
        mv["kb_type"] = mv["launch_profile"] = mv["kb_unknown"] = None
        mv["kb_x"] = mv["air_kb"] = mv["knockback_addr"] = None
        mv["hitstun"] = mv["blockstun"] = mv["hitstop"] = mv["stun_addr"] = None
        return

    apblk = first_block_forward(atkprop_blocks, dblk[0], boundary, max_gap=HIT_SEGMENT_MAX_GAP)
    if apblk:
        mv["attack_property"] = apblk[1]
        mv["atkprop_addr"] = apblk[0]
        mv["attack_property_addr"] = apblk[0] + len(ATKPROP_HDR)
    else:
        mv["attack_property"] = mv["atkprop_addr"] = mv["attack_property_addr"] = None

    hrblk = first_block_forward(hitreact_blocks, dblk[0], boundary, max_gap=HIT_SEGMENT_MAX_GAP)
    if hrblk:
        mv["hit_reaction"] = hrblk[1]
        mv["hit_reaction_addr"] = hrblk[0] + HITREACTION_CODE_OFF
    else:
        mv["hit_reaction"] = mv["hit_reaction_addr"] = None

    kb_start = hrblk[0] if hrblk else dblk[0]
    kbblk = first_block_forward(kb_blocks, kb_start, boundary, max_gap=HIT_SEGMENT_MAX_GAP)
    if kbblk:
        kb = kbblk[1]
        mv["knockback_addr"] = kbblk[0]
        mv["kb_type"] = kb.get("kb_type")
        mv["launch_profile"] = kb.get("launch_profile")
        mv["kb_unknown"] = kb.get("kb_unknown")
        mv["kb_x"] = kb.get("kb_x")
        mv["air_kb"] = kb.get("air_kb")
        mv["kb0"] = mv["launch_profile"]
        mv["kb1"] = mv["kb_type"]
        mv["kb_traj"] = None
    else:
        mv["kb0"] = mv["kb1"] = mv["kb_traj"] = None
        mv["kb_type"] = mv["launch_profile"] = mv["kb_unknown"] = None
        mv["kb_x"] = mv["air_kb"] = mv["knockback_addr"] = None

    stun_start = kbblk[0] if kbblk else kb_start
    sblk = first_block_forward(stun_blocks, stun_start, boundary, max_gap=HIT_SEGMENT_MAX_GAP)
    if sblk:
        mv["hitstun"], mv["blockstun"], mv["hitstop"] = sblk[1]
        mv["stun_addr"] = sblk[0]
    else:
        mv["hitstun"] = mv["blockstun"] = mv["hitstop"] = mv["stun_addr"] = None



# ============================================================
# Multi-hit segment helpers
# ============================================================

HIT_SEGMENT_SCAN_MAX = 0x2400
HIT_SEGMENT_MAX_GAP = 0x900


def _first_block_after(blocks: List[Tuple[int, Any]],
                       start_addr: int,
                       end_addr: int,
                       *,
                       max_gap: int = HIT_SEGMENT_MAX_GAP) -> Optional[Tuple[int, Any]]:
    limit = min(end_addr, start_addr + max_gap)
    for addr, data in blocks:
        if start_addr <= addr < limit:
            return (addr, data)
    return None


def _next_real_boundary_for_move(moves: List[Dict[str, Any]], mv_abs: int) -> int:
    """Return the next real move boundary, ignoring interior table/legacy probes.

    6B exposed why this matters: the chr table can point at subcommands inside
    one move, and legacy 01 xx 01 3C patterns can appear inside the same script.
    Those should not cut the move before the second hit bundle is collected.
    """
    real_sources = {"anim_hdr", "air_hdr", "cmd_hdr", "super_end"}
    candidates: list[int] = []
    for row in moves:
        addr = row.get("abs") or 0
        if addr <= mv_abs + 0x80:
            continue
        if row.get("source") in real_sources:
            candidates.append(int(addr))
    if candidates:
        return min(candidates)
    return mv_abs + HIT_SEGMENT_SCAN_MAX


def collect_hit_segments_for_move(mv_abs: int,
                                  next_boundary_abs: int,
                                  blocks: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect per-hit bundles after a move anchor.

    Each hit is anchored by an active-frame packet and then paired forward to
    damage, property, reaction, KB, and stun packets before the next active
    packet or next real move boundary.  This keeps multi-hit normals such as
    Ryu 6B from being flattened into a single nearest-neighbor row.
    """
    scan_end = min(int(next_boundary_abs), int(mv_abs) + HIT_SEGMENT_SCAN_MAX)
    if scan_end <= mv_abs:
        return []

    active_blocks = sorted(blocks.get("active_blocks") or [], key=lambda x: x[0])
    dmg_blocks = sorted(blocks.get("dmg_blocks") or [], key=lambda x: x[0])
    atkprop_blocks = sorted(blocks.get("atkprop_blocks") or [], key=lambda x: x[0])
    hitreact_blocks = sorted(blocks.get("hitreact_blocks") or [], key=lambda x: x[0])
    kb_blocks = sorted(blocks.get("kb_blocks") or [], key=lambda x: x[0])
    stun_blocks = sorted(blocks.get("stun_blocks") or [], key=lambda x: x[0])

    actives = [(addr, data) for addr, data in active_blocks if mv_abs <= addr < scan_end]
    if not actives:
        return []

    segments: list[dict[str, Any]] = []
    for idx, (active_addr, active_data) in enumerate(actives):
        seg_end = scan_end
        if idx + 1 < len(actives):
            seg_end = min(seg_end, actives[idx + 1][0])

        dblk = _first_block_after(dmg_blocks, active_addr, seg_end)
        if not dblk:
            # Active packets without a nearby damage bundle are usually helper
            # script/collision timing, not a concrete editable hit.
            continue

        damage_addr, damage_data = dblk
        apblk = _first_block_after(atkprop_blocks, damage_addr, seg_end)
        hrblk = _first_block_after(hitreact_blocks, damage_addr, seg_end)
        kb_start = (hrblk[0] if hrblk else damage_addr)
        kbblk = _first_block_after(kb_blocks, kb_start, seg_end)
        stun_start = (kbblk[0] if kbblk else kb_start)
        sblk = _first_block_after(stun_blocks, stun_start, seg_end)

        seg: dict[str, Any] = {
            "hit_index": len(segments) + 1,
            "kind": "hit",
            "abs": active_addr,
            "active_addr": active_addr,
            "active_start": active_data[0],
            "active_end": active_data[1],
            "damage_addr": damage_addr,
            "damage": damage_data[0],
            "damage_flag": damage_data[1],
        }

        if apblk:
            seg["atkprop_addr"] = apblk[0]
            seg["attack_property_addr"] = apblk[0] + len(ATKPROP_HDR)
            seg["attack_property"] = apblk[1]
        if hrblk:
            seg["hit_reaction_addr"] = hrblk[0] + HITREACTION_CODE_OFF
            seg["hit_reaction"] = hrblk[1]
        if kbblk:
            kb = kbblk[1]
            seg["knockback_addr"] = kbblk[0]
            seg["kb_type"] = kb.get("kb_type")
            seg["launch_profile"] = kb.get("launch_profile")
            seg["kb_unknown"] = kb.get("kb_unknown")
            seg["kb_x"] = kb.get("kb_x")
            seg["air_kb"] = kb.get("air_kb")
            seg["kb0"] = seg.get("launch_profile")
            seg["kb1"] = seg.get("kb_type")
            seg["kb_traj"] = None
        if sblk:
            seg["stun_addr"] = sblk[0]
            seg["hitstun"] = sblk[1][0]
            seg["blockstun"] = sblk[1][1]
            seg["hitstop"] = sblk[1][2]

        segments.append(seg)

    return segments


def apply_hit_segment_to_move(mv: Dict[str, Any], seg: Dict[str, Any]) -> None:
    """Overlay a segment onto the legacy scalar fields for compatibility."""
    for key in (
        "damage", "damage_flag", "damage_addr",
        "active_start", "active_end", "active_addr",
        "attack_property", "atkprop_addr", "attack_property_addr",
        "hit_reaction", "hit_reaction_addr",
        "kb0", "kb1", "kb_traj", "kb_type", "launch_profile", "kb_unknown",
        "kb_x", "air_kb", "knockback_addr",
        "hitstun", "blockstun", "hitstop", "stun_addr",
    ):
        if key in seg:
            mv[key] = seg.get(key)
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

        mv["attack_property"] = mv["atkprop_addr"] = mv["attack_property_addr"] = None
        apblk, atkprop_idx = pick_best_block_from_idx(mv_abs, atkprop_blocks, atkprop_idx)
        if apblk:
            mv["attack_property"] = apblk[1]
            mv["atkprop_addr"] = apblk[0]
            mv["attack_property_addr"] = apblk[0] + len(ATKPROP_HDR)

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

        # Tighten scalar packet pairing for loose/strict script rows.  These
        # rows often sit inside phased specials; nearest-neighbor matching made
        # them inherit neighboring hit packets and caused misleading writes.
        if mv.get("source") == "legacy_special" or (mv.get("source") == "strict" and mv.get("kind") != "normal"):
            pair_forward_hit_fields_for_move(mv, moves, blocks)

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

        mv["invuln_probes"] = []
        mv["invuln_probe_count"] = 0
        mv["invuln"] = ""
        mv["invuln_addr"] = None

        mv["hit_segments"] = []
        mv["multi_hit_count"] = 0
        # Multi-hit bundles are meaningful for player move rows and unlabeled
        # hit-script helper rows.  Do not attach them to generic system states
        # such as landing/KO/knockdown just because those scripts happen to use
        # the same active/damage packet format.
        collect_segments_for_row = mv.get("source") in {"anim_hdr", "air_hdr", "cmd_hdr", "super_end"}
        if aid is not None:
            try:
                collect_segments_for_row = collect_segments_for_row and int(aid) >= 0x100
            except Exception:
                collect_segments_for_row = collect_segments_for_row and False
        if collect_segments_for_row:
            next_boundary = _next_real_boundary_for_move(moves, mv_abs)
            hit_segments = collect_hit_segments_for_move(mv_abs, next_boundary, blocks)
            if hit_segments:
                for seg in hit_segments:
                    seg["parent_abs"] = mv_abs
                    seg["parent_id"] = aid
                mv["hit_segments"] = hit_segments
                mv["multi_hit_count"] = len(hit_segments)
                apply_hit_segment_to_move(mv, hit_segments[0])

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

        # The exact +0x1218 startup signature is action-interval-owned and is
        # attached after the full chr_tbl map is available in scan_once().
        mv["invuln_signatures"] = []
        mv["invuln_signature_count"] = 0
        mv["invuln"] = ""
        mv["invuln_addr"] = None


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
# Frame-data profile helpers
# ============================================================

def _profile_safe_name(value: Any) -> str:
    text = str(value or "unknown").strip().lower()
    out = []
    for ch in text:
        if ch.isalnum():
            out.append(ch)
        elif ch in {" ", "-", "_", "."}:
            out.append("_")
    return "".join(out).strip("_") or "unknown"


def _profile_key(char_id: Optional[int], char_name: str) -> str:
    if char_id is not None:
        try:
            return f"id_{int(char_id):02d}_{_profile_safe_name(char_name)}"
        except Exception:
            pass
    return f"name_{_profile_safe_name(char_name)}"


def _empty_profile_doc() -> Dict[str, Any]:
    return {
        "version": PROFILE_CACHE_VERSION,
        "scanner_build": PROFILE_SCANNER_BUILD,
        "updated_at": None,
        "profiles": {},
    }


def _normalize_profile_doc(doc: Any) -> Dict[str, Any]:
    if not isinstance(doc, dict):
        doc = _empty_profile_doc()
    if int(doc.get("version") or PROFILE_CACHE_VERSION) != PROFILE_CACHE_VERSION:
        doc = _empty_profile_doc()
    if not isinstance(doc.get("profiles"), dict):
        doc["profiles"] = {}
    doc.setdefault("version", PROFILE_CACHE_VERSION)
    doc.setdefault("scanner_build", PROFILE_SCANNER_BUILD)
    doc.setdefault("updated_at", None)
    return doc


def _merge_profile_docs(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    merged = _normalize_profile_doc(copy.deepcopy(base))
    overlay = _normalize_profile_doc(overlay)
    profiles = merged.setdefault("profiles", {})
    for k, v in (overlay.get("profiles") or {}).items():
        profiles[str(k)] = v
    merged["version"] = PROFILE_CACHE_VERSION
    merged["scanner_build"] = overlay.get("scanner_build") or merged.get("scanner_build") or PROFILE_SCANNER_BUILD
    merged["updated_at"] = overlay.get("updated_at") or merged.get("updated_at")
    return merged


def _read_profile_doc_file(path: str) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return _normalize_profile_doc(json.load(f))
    except Exception:
        return None


def _profile_read_paths() -> List[str]:
    paths: List[str] = []
    for path in (PROFILE_BUNDLED_CACHE_FILE, PROFILE_CACHE_FILE):
        if not path:
            continue
        ap = os.path.abspath(path)
        if ap not in paths:
            paths.append(ap)
    return paths


def _read_profile_doc_uncached() -> Dict[str, Any]:
    # Merge the bundled seed profile first, then overlay the writable runtime
    # profile.  This makes exported onefile builds start fast immediately, while
    # still allowing newly discovered/changed profiles to persist next to the exe.
    doc = _empty_profile_doc()
    found_any = False
    for path in _profile_read_paths():
        part = _read_profile_doc_file(path)
        if part is None:
            continue
        doc = _merge_profile_docs(doc, part)
        found_any = True
    return _normalize_profile_doc(doc if found_any else _empty_profile_doc())


def _load_profile_doc() -> Dict[str, Any]:
    global _PROFILE_CACHE_DOC
    with _PROFILE_CACHE_LOCK:
        if _PROFILE_CACHE_DOC is not None:
            return _PROFILE_CACHE_DOC
        _PROFILE_CACHE_DOC = _read_profile_doc_uncached()
        return _PROFILE_CACHE_DOC


def _profile_warn_once(key: str, message: str) -> None:
    with _PROFILE_CACHE_LOCK:
        if key in _PROFILE_SAVE_WARNED:
            return
        _PROFILE_SAVE_WARNED.add(key)
    print(message)


def _acquire_profile_file_lock(timeout_sec: float = PROFILE_CACHE_SAVE_TIMEOUT_SEC) -> Optional[int]:
    # Windows can throw PermissionError when two app windows/processes try to
    # replace the same JSON cache at nearly the same time.  This lock file keeps
    # all patched processes serialized without adding any dependency.
    deadline = time.time() + max(0.05, float(timeout_sec or 0.05))
    lock_path = PROFILE_CACHE_LOCK_FILE
    try:
        os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    except Exception:
        pass

    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            try:
                payload = f"pid={os.getpid()} thread={threading.get_ident()} time={time.time():.6f}\n"
                os.write(fd, payload.encode("ascii", "replace"))
            except Exception:
                pass
            return fd
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(lock_path)
                if age > PROFILE_CACHE_STALE_LOCK_SEC:
                    os.unlink(lock_path)
                    continue
            except Exception:
                pass
            if time.time() >= deadline:
                return None
            time.sleep(0.025)
        except Exception:
            return None


def _release_profile_file_lock(fd: Optional[int]) -> None:
    if fd is None:
        return
    try:
        os.close(fd)
    except Exception:
        pass
    try:
        os.unlink(PROFILE_CACHE_LOCK_FILE)
    except Exception:
        pass


def _write_profile_doc_safely(doc: Dict[str, Any]) -> bool:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(PROFILE_CACHE_FILE)), exist_ok=True)
    except Exception:
        pass

    data = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    tmp = f"{PROFILE_CACHE_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"

    # Preferred path: write a complete temp file, then replace in one operation.
    # On Windows, os.replace can briefly fail if another reader has the file
    # open.  Retry instead of treating that as a scanner/editor failure.
    for attempt in range(10):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
            os.replace(tmp, PROFILE_CACHE_FILE)
            return True
        except PermissionError:
            time.sleep(0.035 * (attempt + 1))
        except OSError:
            time.sleep(0.025 * (attempt + 1))

    # Fallback: if replace is blocked but direct write is allowed, use it while
    # the cross-process cache lock is held.  If that also fails, the profile will
    # remain available in memory and the old scanner can rebuild later.
    for attempt in range(5):
        try:
            with open(PROFILE_CACHE_FILE, "w", encoding="utf-8") as f:
                f.write(data)
            try:
                if os.path.exists(tmp):
                    os.unlink(tmp)
            except Exception:
                pass
            return True
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
        except OSError:
            time.sleep(0.035 * (attempt + 1))

    try:
        if os.path.exists(tmp):
            os.unlink(tmp)
    except Exception:
        pass
    return False


def _save_profile_doc(doc: Dict[str, Any]) -> bool:
    global _PROFILE_CACHE_DOC
    with _PROFILE_CACHE_LOCK:
        doc = _normalize_profile_doc(doc)
        doc["version"] = PROFILE_CACHE_VERSION
        doc["scanner_build"] = PROFILE_SCANNER_BUILD
        doc["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

        fd = _acquire_profile_file_lock()
        if fd is None:
            # Do not stall the editor because the cache file is busy.  Keep the
            # profile in RAM and let the next scan try to persist it again.
            _PROFILE_CACHE_DOC = doc
            return False

        try:
            disk_doc = _read_profile_doc_uncached()
            merged = _normalize_profile_doc(disk_doc)
            disk_profiles = merged.setdefault("profiles", {})
            for k, v in (doc.get("profiles") or {}).items():
                disk_profiles[str(k)] = v
            merged["version"] = PROFILE_CACHE_VERSION
            merged["scanner_build"] = PROFILE_SCANNER_BUILD
            merged["updated_at"] = doc["updated_at"]
            ok = _write_profile_doc_safely(merged)
            if ok:
                _PROFILE_CACHE_DOC = merged
            else:
                _PROFILE_CACHE_DOC = doc
            return ok
        finally:
            _release_profile_file_lock(fd)


def _profile_cache_allowed(force_dynamic: bool = False) -> bool:
    return bool(PROFILE_CACHE_ENABLED) and not bool(force_dynamic)


def _profile_table_rels(tbl_move_addrs: List[int], chr_tbl_abs: int) -> List[int]:
    rels: List[int] = []
    for addr in tbl_move_addrs or []:
        try:
            rels.append(int(addr) - int(chr_tbl_abs))
        except Exception:
            continue
    return rels


def _profile_table_signature(tbl_move_addrs: List[int], chr_tbl_abs: int) -> str:
    rels = _profile_table_rels(tbl_move_addrs, chr_tbl_abs)
    payload = ",".join(f"{r:X}" for r in rels).encode("ascii", "ignore")
    return hashlib.sha1(payload).hexdigest()[:16]


def _is_profile_address_key(key: Any) -> bool:
    if not isinstance(key, str):
        return False
    if key in _PROFILE_ADDRESS_KEYS:
        return True
    return any(key.endswith(suf) for suf in _PROFILE_ADDRESS_SUFFIXES)


def _jsonable_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _relativize_profile_obj(obj: Any, chr_tbl_abs: int, parent_key: str | None = None) -> Any:
    if isinstance(obj, dict):
        return {str(k): _relativize_profile_obj(v, chr_tbl_abs, str(k)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_relativize_profile_obj(v, chr_tbl_abs, parent_key) for v in obj]
    if _is_profile_address_key(parent_key) and isinstance(obj, int):
        try:
            if is_mem2_addr(int(obj)):
                return int(obj) - int(chr_tbl_abs)
        except Exception:
            pass
    return _jsonable_scalar(obj)


def _rebase_profile_obj(obj: Any, chr_tbl_abs: int, parent_key: str | None = None) -> Any:
    if isinstance(obj, dict):
        return {str(k): _rebase_profile_obj(v, chr_tbl_abs, str(k)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_rebase_profile_obj(v, chr_tbl_abs, parent_key) for v in obj]
    if _is_profile_address_key(parent_key) and isinstance(obj, int):
        # Profile rows store all address-bearing fields relative to chr_tbl_abs.
        # Keep 0/None-ish addresses blank, but rebase plausible relative offsets.
        try:
            rel = int(obj)
            # slot_base is legitimately table-relative zero. Keep it so
            # cached 00/23 child links can still resolve after rebasing.
            if rel == 0 and parent_key not in ("abs", "slot_base"):
                return None
            if -0x100000 <= rel <= CHR_TBL_MAX_MOVE_OFFSET + SLOT_REGION_PAD + 0x10000:
                return int(chr_tbl_abs) + rel
        except Exception:
            pass
    return obj


def _collect_profile_addresses(obj: Any, parent_key: str | None = None, out: Optional[List[int]] = None) -> List[int]:
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            _collect_profile_addresses(v, str(k), out)
    elif isinstance(obj, list):
        for v in obj:
            _collect_profile_addresses(v, parent_key, out)
    elif _is_profile_address_key(parent_key) and isinstance(obj, int):
        try:
            if is_mem2_addr(int(obj)):
                out.append(int(obj))
        except Exception:
            pass
    return out


def _read_profile_window(chr_tbl_abs: int, moves: List[Dict[str, Any]]) -> Tuple[bytes, int]:
    max_addr = int(chr_tbl_abs)
    for mv in moves or []:
        try:
            a = int(mv.get("abs") or 0)
            if a:
                max_addr = max(max_addr, a + max(HITBOX_OFF_X, HITBOX_OFF_Y) + 4)
        except Exception:
            pass
        for addr in _collect_profile_addresses(mv):
            max_addr = max(max_addr, int(addr) + 0x80)
    size = max(0x4000, max_addr - int(chr_tbl_abs) + 0x100)
    size = min(size, CHR_TBL_MAX_MOVE_OFFSET + SLOT_REGION_PAD + 0x4000)
    return safe_rbytes(int(chr_tbl_abs), size), int(chr_tbl_abs)


def _profile_off(buf: bytes, base_abs: int, addr: Any, size: int = 1) -> Optional[int]:
    try:
        off = int(addr) - int(base_abs)
    except Exception:
        return None
    if off < 0 or off + size > len(buf):
        return None
    return off


def _profile_read_u8(buf: bytes, base_abs: int, addr: Any) -> Optional[int]:
    off = _profile_off(buf, base_abs, addr, 1)
    if off is None:
        return None
    return int(buf[off])


def _profile_read_u32(buf: bytes, base_abs: int, addr: Any) -> Optional[int]:
    """Read a cached profile's rebased big-endian u32 safely."""
    off = _profile_off(buf, base_abs, addr, 4)
    if off is None:
        return None
    try:
        return int(rd_u32_be(buf, off))
    except Exception:
        return None


def _profile_read_f32(buf: bytes, base_abs: int, addr: Any) -> Optional[float]:
    off = _profile_off(buf, base_abs, addr, 4)
    if off is None:
        return None
    try:
        return rd_f32_be(buf, off)
    except Exception:
        return None


def _profile_refresh_hit_segment(seg: Dict[str, Any], buf: bytes, base_abs: int) -> None:
    addr = seg.get("active_addr")
    off = _profile_off(buf, base_abs, addr, ACTIVE_TOTAL_LEN) if addr else None
    if off is not None:
        af = parse_active_frames(buf, off)
        if af:
            seg["active_start"], seg["active_end"] = af

    addr = seg.get("active2_addr")
    off = _profile_off(buf, base_abs, addr, INLINE_ACTIVE_LEN) if addr else None
    if off is not None:
        af = parse_inline_active(buf, off)
        if af:
            seg["active2_start"], seg["active2_end"] = af

    addr = seg.get("damage_addr")
    off = _profile_off(buf, base_abs, addr, DAMAGE_TOTAL_LEN) if addr else None
    if off is not None:
        dmg = parse_damage(buf, off)
        if dmg:
            seg["damage"], seg["damage_flag"] = dmg

    ap = None
    packet_addr = seg.get("atkprop_addr")
    off = _profile_off(buf, base_abs, packet_addr, ATKPROP_TOTAL_LEN) if packet_addr else None
    if off is not None:
        ap = parse_atkprop(buf, off)
        if ap is not None:
            seg["attack_property"] = ap
            seg["atkprop_addr"] = int(packet_addr)
            seg["attack_property_addr"] = int(packet_addr) + len(ATKPROP_HDR)
    if ap is None and seg.get("attack_property_addr"):
        ap = _profile_read_u8(buf, base_abs, seg.get("attack_property_addr"))
        if ap is not None:
            seg["attack_property"] = ap

    addr = seg.get("hit_reaction_addr")
    hr = _profile_read_u8(buf, base_abs, addr) if addr else None
    if hr is not None:
        seg["hit_reaction"] = hr

    addr = seg.get("knockback_addr")
    off = _profile_off(buf, base_abs, addr, KNOCKBACK_TOTAL_LEN) if addr else None
    if off is not None:
        kb = parse_knockback(buf, off)
        if kb:
            seg["kb_type"] = kb.get("kb_type")
            seg["launch_profile"] = kb.get("launch_profile")
            seg["kb_unknown"] = kb.get("kb_unknown")
            seg["kb_x"] = kb.get("kb_x")
            seg["air_kb"] = kb.get("air_kb")
            seg["kb0"] = seg["launch_profile"]
            seg["kb1"] = seg["kb_type"]
            seg["kb_traj"] = None

    addr = seg.get("stun_addr")
    off = _profile_off(buf, base_abs, addr, 39) if addr else None
    if off is not None:
        stun = parse_stun(buf, off)
        if stun:
            seg["hitstun"], seg["blockstun"], seg["hitstop"] = stun


def _profile_refresh_move(mv: Dict[str, Any], buf: bytes, base_abs: int, char_id: Optional[int]) -> None:
    aid = mv.get("id")
    try:
        aid_low = (int(aid) & 0xFF) if aid is not None else None
    except Exception:
        aid_low = None

    if mv.get("kind") == "normal":
        mv["meter"] = DEFAULT_METER.get(aid_low, mv.get("meter"))
    elif mv.get("meter") is None:
        mv["meter"] = SPECIAL_DEFAULT_METER

    b = _profile_read_u8(buf, base_abs, mv.get("meter_addr")) if mv.get("meter_addr") else None
    if b is not None:
        mv["meter"] = b

    addr = mv.get("active_addr")
    off = _profile_off(buf, base_abs, addr, ACTIVE_TOTAL_LEN) if addr else None
    if off is not None:
        af = parse_active_frames(buf, off)
        if af:
            mv["active_start"], mv["active_end"] = af

    addr = mv.get("active2_addr")
    off = _profile_off(buf, base_abs, addr, INLINE_ACTIVE_LEN) if addr else None
    if off is not None:
        af = parse_inline_active(buf, off)
        if af:
            mv["active2_start"], mv["active2_end"] = af

    addr = mv.get("damage_addr")
    off = _profile_off(buf, base_abs, addr, DAMAGE_TOTAL_LEN) if addr else None
    if off is not None:
        dmg = parse_damage(buf, off)
        if dmg:
            mv["damage"], mv["damage_flag"] = dmg

    ap = None
    packet_addr = mv.get("atkprop_addr")
    off = _profile_off(buf, base_abs, packet_addr, ATKPROP_TOTAL_LEN) if packet_addr else None
    if off is not None:
        ap = parse_atkprop(buf, off)
        if ap is not None:
            mv["attack_property"] = ap
            mv["atkprop_addr"] = int(packet_addr)
            mv["attack_property_addr"] = int(packet_addr) + len(ATKPROP_HDR)
    if ap is None and mv.get("attack_property_addr"):
        ap = _profile_read_u8(buf, base_abs, mv.get("attack_property_addr"))
        if ap is not None:
            mv["attack_property"] = ap

    hr = _profile_read_u8(buf, base_abs, mv.get("hit_reaction_addr")) if mv.get("hit_reaction_addr") else None
    if hr is not None:
        mv["hit_reaction"] = hr

    addr = mv.get("knockback_addr")
    off = _profile_off(buf, base_abs, addr, KNOCKBACK_TOTAL_LEN) if addr else None
    if off is not None:
        kb = parse_knockback(buf, off)
        if kb:
            mv["kb_type"] = kb.get("kb_type")
            mv["launch_profile"] = kb.get("launch_profile")
            mv["kb_unknown"] = kb.get("kb_unknown")
            mv["kb_x"] = kb.get("kb_x")
            mv["air_kb"] = kb.get("air_kb")
            mv["kb0"] = mv["launch_profile"]
            mv["kb1"] = mv["kb_type"]
            mv["kb_traj"] = None

    addr = mv.get("stun_addr")
    off = _profile_off(buf, base_abs, addr, 39) if addr else None
    if off is not None:
        stun = parse_stun(buf, off)
        if stun:
            mv["hitstun"], mv["blockstun"], mv["hitstop"] = stun

    try:
        mv_abs = int(mv.get("abs") or 0)
    except Exception:
        mv_abs = 0
    if mv_abs:
        hx = _profile_read_f32(buf, base_abs, mv_abs + HITBOX_OFF_X)
        hy = _profile_read_f32(buf, base_abs, mv_abs + HITBOX_OFF_Y)
        if hx is not None:
            mv["hb_x"] = hx
        if hy is not None:
            mv["hb_y"] = hy

    segs = mv.get("hit_segments")
    if isinstance(segs, list):
        for seg in segs:
            if isinstance(seg, dict):
                _profile_refresh_hit_segment(seg, buf, base_abs)
        mv["multi_hit_count"] = len([s for s in segs if isinstance(s, dict)])
        if segs and isinstance(segs[0], dict):
            try:
                apply_hit_segment_to_move(mv, segs[0])
            except Exception:
                pass

    signatures = mv.get("invuln_signatures")
    if isinstance(signatures, list):
        refreshed: List[Dict[str, Any]] = []
        for signature in signatures:
            if not isinstance(signature, dict):
                continue
            value_addr = signature.get("value_addr")
            raw = _profile_read_u32(buf, base_abs, value_addr) if value_addr else None
            frames = _invuln_signature_frames(raw) if raw is not None else None
            if frames is None:
                continue
            updated = dict(signature)
            updated["raw_value"] = int(raw)
            updated["frames"] = int(frames)
            refreshed.append(updated)
        mv["invuln_signatures"] = refreshed
        mv["invuln_signature_count"] = len(refreshed)
        mv["invuln"] = summarize_invuln_signatures(refreshed)
        mv["invuln_addr"] = int(refreshed[0]["value_addr"]) if refreshed else None

    total_frames = mv.get("speed") or 0x3C
    a_end = mv.get("active_end")
    recovery = max(0, total_frames - a_end) if a_end else 12
    hs = mv.get("hitstun") or 0
    bs = mv.get("blockstun") or 0
    mv["adv_hit"] = hs - recovery
    mv["adv_block"] = bs - recovery

    # Refresh names/optional-normal confirmation in case move_id_map was updated
    # after the profile was created.
    if aid is not None:
        name = None
        try:
            name = lookup_move_name(aid, char_id) if char_id is not None else lookup_move_name(aid)
        except TypeError:
            try:
                name = lookup_move_name(aid)
            except Exception:
                name = None
        except Exception:
            name = None
        if name:
            mv["move_name"] = name
            mv["move_name_source"] = "lookup"
        elif not mv.get("move_name"):
            fallback = None if aid_low in OPTIONAL_NORMAL_IDS else ANIM_MAP.get(aid_low)
            mv["move_name"] = fallback if fallback else f"anim_{int(aid):04X}"
            mv["move_name_source"] = "anim_map" if fallback else "anim"
        if aid_low in CORE_NORMAL_IDS:
            mv["normal_confirmed"] = True
        elif aid_low in OPTIONAL_NORMAL_IDS:
            mv["normal_confirmed"] = (
                str(mv.get("move_name_source") or "") == "lookup"
                and str(mv.get("move_name") or "").strip().lower().replace(" ", "") == "6b"
            )


def _load_profile_moves(
    char_id: Optional[int],
    char_name: str,
    chr_tbl_abs: int,
    tbl_move_addrs: List[int],
) -> Optional[List[Dict[str, Any]]]:
    if not PROFILE_CACHE_ENABLED:
        return None
    key = _profile_key(char_id, char_name)
    sig = _profile_table_signature(tbl_move_addrs, chr_tbl_abs)
    doc = _load_profile_doc()
    prof = (doc.get("profiles") or {}).get(key)
    if not isinstance(prof, dict):
        return None
    # Reject experimental Morrigan inherited-stun profiles from v28/v29.
    # Those builds resolved FF FF FF FE default markers into guessed HS/BS values,
    # but we are intentionally returning Morrigan to the old blank behavior.
    if "morrigan" in key.lower() and "morrigan-stun" in str(prof.get("scanner_build") or "").lower():
        return None
    if int(prof.get("version") or 0) != PROFILE_CACHE_VERSION:
        return None
    if str(prof.get("table_signature") or "") != sig:
        return None
    rows = prof.get("moves")
    if not isinstance(rows, list) or not rows:
        return None

    moves: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rebased = _rebase_profile_obj(copy.deepcopy(row), chr_tbl_abs)
        if isinstance(rebased, dict):
            rebased["_profile_fast_path"] = True
            moves.append(rebased)
    if not moves:
        return None

    try:
        buf, base_abs = _read_profile_window(chr_tbl_abs, moves)
        if not buf:
            return None
        for mv in moves:
            _profile_refresh_move(mv, buf, base_abs, char_id)
    except Exception as e:
        print(f"[fd profile] fast path failed for {char_name}: {e!r}")
        return None

    _profile_warn_once(
        f"fast:{key}",
        f"[fd profile] fast path for {char_name} ({key})",
    )
    return moves


def _save_profile_moves(
    char_id: Optional[int],
    char_name: str,
    chr_tbl_abs: int,
    tbl_move_addrs: List[int],
    moves: List[Dict[str, Any]],
) -> None:
    if not PROFILE_CACHE_ENABLED or not moves:
        return
    key = _profile_key(char_id, char_name)
    sig = _profile_table_signature(tbl_move_addrs, chr_tbl_abs)
    try:
        rows = [_relativize_profile_obj(copy.deepcopy(mv), chr_tbl_abs) for mv in moves]
        doc = _load_profile_doc()
        profiles = doc.setdefault("profiles", {})
        previous = profiles.get(key) if isinstance(profiles.get(key), dict) else {}
        # A normal-only rebuild must not throw away a projectile/special pass
        # that the user already profiled for this exact character table.
        keep_extras = str(previous.get("table_signature") or "") == sig
        profile = {
            "version": PROFILE_CACHE_VERSION,
            "scanner_build": PROFILE_SCANNER_BUILD,
            "char_id": char_id,
            "char_name": char_name,
            "key": key,
            "table_signature": sig,
            "table_move_count": len(tbl_move_addrs or []),
            "created_from": "dynamic_scan_once",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "invuln_signature_revision": INVULN_SIGNATURE_PROFILE_REVISION,
            "moves": rows,
        }
        if keep_extras:
            for extra_key in ("projectiles", "specials", "projectiles_profiled", "specials_profiled", "projectiles_profiled_at", "specials_profiled_at"):
                if extra_key in previous:
                    profile[extra_key] = copy.deepcopy(previous[extra_key])
        profiles[key] = profile
        if not _save_profile_doc(doc):
            _profile_warn_once(
                f"deferred:{key}",
                f"[fd profile] save deferred for {char_name}: cache file is busy; using in-memory profile for this run",
            )
        else:
            _profile_warn_once(
                f"saved:{key}",
                f"[fd profile] saved profile for {char_name} ({key})",
            )
    except Exception as e:
        _profile_warn_once(
            f"failed:{key}",
            f"[fd profile] save skipped for {char_name}: {e!r}",
        )


# ============================================================
# Optional projectile/special profile passes
# ============================================================


def _profile_extra_bundle(
    char_id: Optional[int],
    char_name: str,
    chr_tbl_abs: int,
    table_signature: str,
) -> Dict[str, Any]:
    """Return cached projectile/special scan rows rebased to the live table.

    These rows are deliberately *not* rescanned here.  The one-time discovery
    pass is user-triggered from the Frame Data workbench; subsequent openings
    only deserialize and rebase the proven records for the loaded character.
    """
    empty = {
        "projectiles": [],
        "specials": [],
        "projectiles_profiled": False,
        "specials_profiled": False,
    }
    if not PROFILE_CACHE_ENABLED:
        return empty
    key = _profile_key(char_id, char_name)
    prof = (_load_profile_doc().get("profiles") or {}).get(key)
    if not isinstance(prof, dict):
        return empty
    if int(prof.get("version") or 0) != PROFILE_CACHE_VERSION:
        return empty
    if str(prof.get("table_signature") or "") != str(table_signature or ""):
        return empty

    out = dict(empty)
    for cache_key, out_key in (("projectiles", "projectiles"), ("specials", "specials")):
        profiled_key = f"{cache_key}_profiled"
        out[profiled_key] = bool(prof.get(profiled_key))
        rows = prof.get(cache_key)
        if not isinstance(rows, list):
            continue
        rebased = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            value = _rebase_profile_obj(copy.deepcopy(row), chr_tbl_abs)
            if isinstance(value, dict):
                value["_profile_fast_path"] = True
                rebased.append(value)
        out[out_key] = rebased
    return out


def load_profile_extras(
    char_id: Optional[int],
    char_name: str,
    chr_tbl_abs: int,
    tbl_move_addrs: Optional[List[int]] = None,
    *,
    table_signature: Optional[str] = None,
) -> Dict[str, Any]:
    """Public extra-profile loader used by scan_once and the workbench."""
    sig = str(table_signature or _profile_table_signature(list(tbl_move_addrs or []), chr_tbl_abs))
    return _profile_extra_bundle(char_id, char_name, chr_tbl_abs, sig)


def save_profile_extras(
    char_id: Optional[int],
    char_name: str,
    chr_tbl_abs: int,
    tbl_move_addrs: Optional[List[int]] = None,
    *,
    table_signature: Optional[str] = None,
    projectile_hits: Optional[List[Dict[str, Any]]] = None,
    super_hits: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Persist completed projectile and/or special discovery passes.

    The normal move profile and each optional pass share one profile key and
    table signature.  Supplying only one list updates only that pass, which
    lets the UI build projectiles and specials independently without replacing
    the other section.
    """
    if not PROFILE_CACHE_ENABLED:
        return False
    key = _profile_key(char_id, char_name)
    sig = str(table_signature or _profile_table_signature(list(tbl_move_addrs or []), chr_tbl_abs))
    try:
        doc = _load_profile_doc()
        profiles = doc.setdefault("profiles", {})
        previous = profiles.get(key) if isinstance(profiles.get(key), dict) else {}
        if str(previous.get("table_signature") or "") not in ("", sig):
            previous = {}

        profile = copy.deepcopy(previous)
        profile.update({
            "version": PROFILE_CACHE_VERSION,
            "scanner_build": PROFILE_SCANNER_BUILD,
            "char_id": char_id,
            "char_name": char_name,
            "key": key,
            "table_signature": sig,
            "table_move_count": int(previous.get("table_move_count") or len(tbl_move_addrs or [])),
            "created_from": previous.get("created_from") or "frame_data_workbench",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        })

        if projectile_hits is not None:
            profile["projectiles"] = [
                _relativize_profile_obj(copy.deepcopy(row), chr_tbl_abs)
                for row in list(projectile_hits or []) if isinstance(row, dict)
            ]
            profile["projectiles_profiled"] = True
            profile["projectiles_profiled_at"] = profile["updated_at"]
        if super_hits is not None:
            profile["specials"] = [
                _relativize_profile_obj(copy.deepcopy(row), chr_tbl_abs)
                for row in list(super_hits or []) if isinstance(row, dict)
            ]
            profile["specials_profiled"] = True
            profile["specials_profiled_at"] = profile["updated_at"]

        profiles[key] = profile
        ok = _save_profile_doc(doc)
        if ok:
            _profile_warn_once(
                f"extras:{key}",
                f"[fd profile] saved optional passes for {char_name} ({key})",
            )
        return bool(ok)
    except Exception as e:
        _profile_warn_once(
            f"extras-failed:{key}",
            f"[fd profile] optional-pass save skipped for {char_name}: {e!r}",
        )
        return False


def _profile_needs_invuln_signature_upgrade(
    char_id: Optional[int], char_name: str, table_signature: str,
) -> bool:
    if not PROFILE_CACHE_ENABLED:
        return False
    try:
        profile = (_load_profile_doc().get("profiles") or {}).get(_profile_key(char_id, char_name))
        if not isinstance(profile, dict):
            return False
        if str(profile.get("table_signature") or "") != str(table_signature or ""):
            return False
        return int(profile.get("invuln_signature_revision") or 0) < INVULN_SIGNATURE_PROFILE_REVISION
    except Exception:
        return False


def _auto_upgrade_profile_invuln(
    moves: List[Dict[str, Any]],
    *,
    char_id: Optional[int],
    char_name: str,
    chr_tbl_abs: int,
    tbl_move_addrs: List[int],
    tbl_buf: bytes,
    tbl_base_abs: int,
    action_entries: Sequence[Tuple[int, int]],
) -> bool:
    """One-time lightweight profile upgrade for the +0x1218 signature.

    This does not rebuild move discovery, grouping, projectiles, or supers. It
    reads the loaded character script span once, adds the known values to the
    already-saved rows, then persists them so the next open is cache-only.
    """
    try:
        region_start, region_end = slot_scan_region_from_tbl(tbl_buf, tbl_base_abs, chr_tbl_abs)
        region_buf = safe_rbytes(region_start, region_end - region_start)
        if not region_buf:
            return False
        by_action, by_root = collect_invuln_signature_map(region_buf, region_start, action_entries)
        apply_invuln_signatures_to_moves(moves, by_action, by_root)
        _save_profile_moves(char_id, char_name, chr_tbl_abs, tbl_move_addrs, moves)
        return True
    except Exception as exc:
        _profile_warn_once(f"invuln-upgrade-failed:{_profile_key(char_id, char_name)}", f"[fd profile] invuln upgrade skipped for {char_name}: {exc!r}")
        return False


# ============================================================
# MAIN SCAN
# ============================================================

def scan_once(force_dynamic: bool = False, cache_only: bool = False):
    hook()

    slots_info = read_slots_from_constants()

    result: List[Dict[str, Any]] = []
    for _ in range(4):
        result.append({
            "slot_label": "",
            "char_name": "",
            "char_id": None,
            "moves": [],
            "chr_tbl_abs": None,
            "tbl_move_count": 0,
            "profile_table_signature": "",
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
        tbl_action_entries = parse_chr_tbl_action_entries(tbl_buf, tbl_start, chr_tbl_abs)

        table_signature = _profile_table_signature(tbl_move_addrs, chr_tbl_abs)
        if _profile_cache_allowed(force_dynamic):
            profiled_moves = _load_profile_moves(cid, cname, chr_tbl_abs, tbl_move_addrs)
            if profiled_moves is not None:
                if _profile_needs_invuln_signature_upgrade(cid, cname, table_signature):
                    _auto_upgrade_profile_invuln(
                        profiled_moves,
                        char_id=cid,
                        char_name=cname,
                        chr_tbl_abs=chr_tbl_abs,
                        tbl_move_addrs=tbl_move_addrs,
                        tbl_buf=tbl_buf,
                        tbl_base_abs=tbl_start,
                        action_entries=tbl_action_entries,
                    )
                extras = load_profile_extras(
                    cid, cname, chr_tbl_abs, tbl_move_addrs,
                    table_signature=table_signature,
                )
                result[slot_idx] = {
                    "slot_label": slot_label,
                    "char_name": cname,
                    "char_id": cid,
                    "moves": sorted(profiled_moves, key=sort_key),
                    "chr_tbl_abs": chr_tbl_abs,
                    "tbl_move_count": len(tbl_move_addrs),
                    "profile_table_signature": table_signature,
                    "profile_projectile_hits": list(extras.get("projectiles") or []),
                    "profile_super_hits": list(extras.get("specials") or []),
                    "profile_projectiles_profiled": bool(extras.get("projectiles_profiled")),
                    "profile_specials_profiled": bool(extras.get("specials_profiled")),
                    "profile_fast_path": True,
                    "profile_key": _profile_key(cid, cname),
                }
                continue

        # Auto/background HUD refreshes should never fall through into the full
        # dynamic scanner when a profile is missing.  The dynamic path can take
        # hundreds of milliseconds to seconds and the Python/GIL work is visible
        # as gameplay stutter even from a worker thread.  Manual frame-data scans
        # still call scan_once() with cache_only=False and remain the source of
        # truth for creating/updating profiles.
        if cache_only:
            result[slot_idx] = {
                "slot_label": slot_label,
                "char_name": cname,
                "char_id": cid,
                "moves": [],
                "chr_tbl_abs": chr_tbl_abs,
                "tbl_move_count": len(tbl_move_addrs),
                "profile_table_signature": table_signature,
                "profile_fast_path": False,
                "profile_cache_miss": True,
                "profile_key": _profile_key(cid, cname),
            }
            continue

        region_start, region_end = slot_scan_region_from_tbl(tbl_buf, tbl_start, chr_tbl_abs)

        region_buf = safe_rbytes(region_start, region_end - region_start)
        if not region_buf:
            continue

        in_slice = [a for a in tbl_move_addrs if region_start <= a < region_end]

        moves = collect_move_anchors(region_buf, region_start, tbl_move_addrs=in_slice)
        blocks = collect_blocks(region_buf, region_start)
        attach_move_fields(moves, region_buf, region_start, blocks, char_id=cid)
        invuln_by_action, invuln_by_root = collect_invuln_signature_map(
            region_buf, region_start, tbl_action_entries,
        )
        apply_invuln_signatures_to_moves(moves, invuln_by_action, invuln_by_root)
        moves = collapse_duplicate_normals_by_quality(moves)
        sorted_moves = sorted(moves, key=sort_key)
        _save_profile_moves(cid, cname, chr_tbl_abs, tbl_move_addrs, sorted_moves)

        extras = load_profile_extras(
            cid, cname, chr_tbl_abs, tbl_move_addrs,
            table_signature=table_signature,
        )
        result[slot_idx] = {
            "slot_label": slot_label,
            "char_name": cname,
            "char_id": cid,
            "moves": sorted_moves,
            "chr_tbl_abs": chr_tbl_abs,
            "tbl_move_count": len(tbl_move_addrs),
            "profile_table_signature": table_signature,
            "profile_projectile_hits": list(extras.get("projectiles") or []),
            "profile_super_hits": list(extras.get("specials") or []),
            "profile_projectiles_profiled": bool(extras.get("projectiles_profiled")),
            "profile_specials_profiled": bool(extras.get("specials_profiled")),
            "profile_fast_path": False,
            "profile_key": _profile_key(cid, cname),
        }

    return result