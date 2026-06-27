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
try:
    from runtime_stun_profiler import apply_runtime_stun_observations
except Exception:
    apply_runtime_stun_observations = None
try:
    from animation_frames import apply_animation_metadata
except Exception:
    def apply_animation_metadata(_moves, _char_name, _char_id=None):
        return None


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

# Launch packet immediately after the hit-reaction block.
# Confirmed on Ryu:
#   +0x00 = 35 07/09 00 20 packet header
#   +0x04 = launch/trajectory profile (u32)
#   +0x08 = launch adjustment word (u32)
#   +0x0C = Air KB X scalar (f32)
#   +0x10 = Air KB Y / vertical displacement scalar (f32)
#
# Optional 35 0C 00 20 hit-spacing packet. It is NOT the universal base
# launch path: many attacks omit it.
#   +0x04 = packet mode (u32)
#   +0x08 = signed Hit Push/Pull X (f32) -- confirmed from Ryu Tatsu Super:
#           negative pushes the victim away; positive pulls/vacuums inward.
#   +0x0C = Hit Push/Pull Aux (f32) -- exposed for testing; semantics unknown.
#
# This scanner only attaches a 35/0C packet when it belongs to the same
# post-hit bundle as a 35/07 or 35/09 launch packet and terminates in a valid
# stun packet.  That makes the finder dynamic without treating FD 35/0D data
# or unrelated script blobs as hit spacing.
#
# Do not accept the old loose 35 ?? ?? 20 family here; 35 0D belongs to the
# FD/hitbox-scaling family and does not affect knockback.
KNOCKBACK_TOTAL_LEN = 20
KNOCKBACK_VALID_TYPES = {0x07, 0x09}
KNOCKBACK_TYPE_OFF = 1
KNOCKBACK_PROFILE_OFF = 4
KNOCKBACK_UNKNOWN_OFF = 8
KNOCKBACK_X_OFF = 12
KNOCKBACK_AIR_OFF = 16

# Optional grounded/standing-push packet. Keep it separate from the 35 07/09
# base launch packet: the old scanner mislabeled 35 07/09 +0x0C as grounded pushback.
GROUND_KB_TOTAL_LEN = 16
GROUND_KB_TYPE = 0x0C
GROUND_KB_MODE_OFF = 4
GROUND_KB_VALUE_OFF = 8
GROUND_KB_AUX_OFF = 12

STUN_HDR = [
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x54,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, None,
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x58,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, None,
    0x33, 0x32, 0x00, 0x20, 0x00, 0x00, 0x00, None,
    0x04, 0x15, 0x60,
]
STUN_TOTAL_LEN = 43

# ``+0x254/+0x258 == -2`` is the engine-default stun sentinel.  It is not
# a literal "-2 frames" value: the hit resolver selects the normal hit-level
# defaults from the damage packet unless a later direct pair overrides them.
#
# The three values are established by the normal hit resolver and are only
# used when the owning action explicitly arms the -2/-2 sentinel.  Never use
# this as a generic fallback for specials, supers, or rows with no sentinel.
DEFAULT_NORMAL_STUN_BY_DAMAGE_FLAG = {
    0x04: (12, 9),
    0x08: (17, 12),
    0x0C: (21, 15),
}

PAIR_RANGE = 0x600

# Display-only +0x1218 phase/timer probe.
#
# This field is still not independently proven to be an invulnerability flag,
# but real startup phases use many durations, including 1f and 2f values. Do
# not hard-code a 3-120f whitelist. Keep every finite duration and reject it
# only when a trustworthy owner action has a known startup+active window that
# ends before the proposed duration. 999 is the known held-state sentinel used
# by KO/reaction/assist scripts, never a literal 999f duration.
INVULN_PROBE_HDR = bytes([0x04, 0x01, 0x60, 0x00])
INVULN_PROBE_FIELD = 0x1218
INVULN_PROBE_MARKER = 0x3F000000
INVULN_FIRST_HIT_RANGE = 0x380
INVULN_SCAN_RANGE = 0x700
# chr_tbl pointers commonly land just after the startup setup packet.  The
# preceding bytes belong to that same table-owned action, not the prior row.
INVULN_TABLE_PREAMBLE = 0x80
INVULN_HOLD_SENTINEL_FRAMES = 999

# A broad +0x1218 write is only a phase timer.  The normal-entry template
# explicitly clears the field and arms it for 2f before handing off to the
# next action; that is common bookkeeping, not invulnerability.  Protected
# phases such as Hurricane C and Ryu Shoryu pair the timer with a nearby +0x58
# state write whose low bit is set and use the dedicated 04 01 02 3F phase
# setup opcode.  That is strong structural evidence, but it is still not the
# same thing as an in-game collision test.
#
# Keep explicitly runtime-verified moves separate from structurally similar
# candidates.  [C] is used only for these known references; [H] means a
# different row has the same topology, not that it has already been proven.
INVULN_PHASE_STATE_FIELD = 0x0058
INVULN_ACTION_FIELD = 0x01E8
INVULN_BOOTSTRAP_CLEAR_TYPE = 0x02
INVULN_CONTEXT_LOOKBACK = 0x180
INVULN_BOOTSTRAP_LOOKBACK = 0x60
INVULN_BOOTSTRAP_LOOKAHEAD = 0x80
INVULN_PHASE_SETUP_HDR = bytes([0x04, 0x01, 0x02, 0x3F])

# Runtime-confirmed reference rows.  This is deliberately tiny: do not let a
# name match or a broad move family promote untested rows to "confirmed".
# Tuple = (character ID, action/animation ID).
# - Polimar 6C: validated invulnerability reference.
# - Ryu Shoryu L/M/H: validated startup-invulnerability references.
CONFIRMED_INVULN_ACTIONS = {
    (4, 0x0106),
    (12, 0x0136),
    (12, 0x0137),
    (12, 0x0138),
}

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
PROFILE_CACHE_VERSION = 7
PROFILE_SCANNER_BUILD = "fd-profile-v8-animation-mot-recovery"
# Kept separate from the global profile format version so existing projectile
# and super profiles remain intact.  A normal move profile simply refreshes
# once when this resolver changes.
STUN_RESOLVER_REVISION = 1
# Bump whenever the active-frame packet parser changes. Cached rows retain the
# old block address, so a rescan is required to remove a previously accepted
# false-positive packet instead of merely refreshing the scalar bytes.
ACTIVE_RESOLVER_REVISION = 2
PROFILE_CACHE_FILENAME = "frame_data_profiles.json"
PROFILE_CACHE_ENABLED = str(os.environ.get("TVC_FD_PROFILE_CACHE", "1")).strip().lower() not in {"0", "false", "no", "off"}

# Tiny read-only snapshot used by the always-on HUD and hitbox renderer. The
# workbench cache can be ~100 MB / thousands of rows per fighter; loading and
# refreshing that cache in the overlay is what made character changes stall.
PREVIEW_PROFILE_CACHE_VERSION = 1
PREVIEW_PROFILE_CACHE_FILENAME = "frame_data_preview_profiles.json"
PREVIEW_PROFILE_CACHE_ENABLED = str(os.environ.get("TVC_FD_PREVIEW_CACHE", "1")).strip().lower() not in {"0", "false", "no", "off"}


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
    # script can bundle the profiles the operator already generated.  PyInstaller onefile
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


def _preview_profile_default_file() -> str:
    # Source runs read beside this module; frozen builds prefer an external copy
    # beside the executable, mirroring the workbench cache behavior.
    return os.path.join(_profile_exe_dir(), PREVIEW_PROFILE_CACHE_FILENAME)


PREVIEW_PROFILE_CACHE_FILE = os.environ.get("TVC_FD_PREVIEW_PROFILE_FILE", _preview_profile_default_file())


def _preview_profile_bundled_file() -> str:
    """Return the packaged compact-preview seed, when one is available.

    One-file builds unpack the seed under ``_MEIPASS`` while the writable
    runtime cache lives beside ``TvCGUI.exe``.  The reader merges the seed
    first and then overlays the writable cache so a fresh EXE starts with the
    shipped previews immediately and can persist newly scanned characters.
    """
    override = os.environ.get("TVC_FD_PREVIEW_BUNDLED_FILE", "")
    if str(override or "").strip():
        return os.path.abspath(os.path.expanduser(str(override)))
    bundle_dir = _profile_bundle_dir()
    if bundle_dir:
        return os.path.join(bundle_dir, PREVIEW_PROFILE_CACHE_FILENAME)
    source_file = os.path.join(_profile_module_dir(), PREVIEW_PROFILE_CACHE_FILENAME)
    return source_file if os.path.exists(source_file) else ""


PREVIEW_PROFILE_BUNDLED_FILE = _preview_profile_bundled_file()
PROFILE_CACHE_LOCK_FILE = os.environ.get(
    "TVC_FD_PROFILE_CACHE_LOCK_FILE",
    PROFILE_CACHE_FILE + ".lock",
)
PROFILE_CACHE_SAVE_TIMEOUT_SEC = float(os.environ.get("TVC_FD_PROFILE_SAVE_TIMEOUT", "2.0") or "2.0")
PROFILE_CACHE_STALE_LOCK_SEC = float(os.environ.get("TVC_FD_PROFILE_STALE_LOCK_SEC", "30.0") or "30.0")

_PROFILE_CACHE_LOCK = threading.RLock()
_PROFILE_CACHE_DOC: Optional[Dict[str, Any]] = None
_PROFILE_SAVE_WARNED: set[str] = set()
_PREVIEW_PROFILE_LOCK = threading.RLock()
_PREVIEW_PROFILE_DOC: Optional[Dict[str, Any]] = None

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


def parse_chr_tbl_entries(buf: bytes, mem_base: int, chr_tbl_abs: int) -> List[Tuple[int, int]]:
    """Return ``(action_id, absolute_root)`` pairs from ``chr_tbl``.

    The table index is the action ID.  That distinction matters for specials:
    a table entry for 0x0136 can contain internal 0x0135/0x0136/0x0137/0x0138
    animation commands, so inferring ownership from the first nested command
    causes adjacent strength branches to donate timing values to one another.
    The table entry itself is the gameplay action owner.
    """
    tbl_off = abs_to_file_off(chr_tbl_abs, mem_base)
    if tbl_off < 0 or tbl_off + 4 > len(buf):
        return []

    act_rel = _find_chr_act_rel(buf, tbl_off)
    if act_rel is None:
        # Safe fallback: bounded scan. Do not assume the old 705-entry size.
        act_rel = min(CHR_TBL_SCAN_MAX_BYTES, len(buf) - tbl_off)

    entries: List[Tuple[int, int]] = []
    seen_moves: set[int] = set()
    entry_count = max(0, act_rel // 4)

    for action_id in range(entry_count):
        off = tbl_off + action_id * 4
        if off + 4 > len(buf):
            break

        entry = rd_u32_be(buf, off)
        if entry == 0xFFFFFFFF:
            break
        if entry == 0:
            continue
        if entry % 4 != 0:
            continue
        if entry < MOVE_DATA_START_OFF or entry > CHR_TBL_MAX_MOVE_OFFSET:
            continue

        move_abs = chr_tbl_abs + entry
        if is_mem2_addr(move_abs) and move_abs not in seen_moves:
            seen_moves.add(move_abs)
            entries.append((action_id, move_abs))

    return entries


def parse_chr_tbl(buf: bytes, mem_base: int, chr_tbl_abs: int) -> List[int]:
    """Compatibility wrapper returning only absolute action roots."""
    return [addr for _, addr in parse_chr_tbl_entries(buf, mem_base, chr_tbl_abs)]

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
    """Parse a finite active-frame packet.

    The command header alone is not enough.  A held/loop state can begin with
    the same ``20 35 01 20 3F`` bytes but store ``0x000003E7`` (999) in the
    end operand.  The old byte-only parser read its low byte (``0xE7``) and
    invented an active window of frames 1--232; Blade 4C exposed that exact
    false positive.  Normal finite frame operands are byte-sized values encoded
    as ``3F 00 00 XX`` for both start and end.
    """
    if pos + ACTIVE_TOTAL_LEN > len(buf):
        return None
    if not match_bytes(buf, pos, ACTIVE_HDR):
        return None
    # Both values must be byte-sized script literals.  This rejects held/999
    # control states while preserving every normal finite active packet.
    if buf[pos + 9:pos + 12] != b"\x00\x00\x00":
        return None
    if buf[pos + 13:pos + 16] != b"\x00\x00\x00":
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
    # Air KB X and Air KB Y are big-endian floats.
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


def parse_ground_knockback(buf: bytes, pos: int) -> Optional[Dict[str, Any]]:
    """Parse the optional 35 0C hit Push/Pull packet.

    Layout: ``35 0C 00 20 [mode u32] [Push/Pull X f32] [Push/Pull Aux f32]``.
    ``+0x08`` is confirmed as signed horizontal hit spacing: Ryu Tatsu Super
    uses negative values to push its first hit away and positive values to
    vacuum later hits inward.  ``+0x0C`` is intentionally exposed as Aux until
    its engine role is independently proven.  Legacy ``ground_kb`` field keys
    stay in place for cache/patch compatibility.
    """
    if pos + GROUND_KB_TOTAL_LEN > len(buf):
        return None
    if (
        buf[pos] != 0x35
        or buf[pos + 1] != GROUND_KB_TYPE
        or buf[pos + 2] != 0x00
        or buf[pos + 3] != 0x20
    ):
        return None
    try:
        return {
            "ground_kb_mode": rd_u32_be(buf, pos + GROUND_KB_MODE_OFF),
            "ground_kb": rd_f32_be(buf, pos + GROUND_KB_VALUE_OFF),
            "ground_kb_y": rd_f32_be(buf, pos + GROUND_KB_AUX_OFF),
            # Compatibility only: older cached/profile code may still inspect it.
            "ground_kb_aux": rd_f32_be(buf, pos + GROUND_KB_AUX_OFF),
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


def _invuln_frames_from_value(raw_value: int) -> int:
    try:
        raw = int(raw_value) & 0xFFFFFFFF
    except Exception:
        return 0
    if raw == 0:
        return 0
    # Signature values are stored like 0x00000D00 for 13f.
    if (raw & 0xFF) == 0:
        return (raw >> 8) & 0xFFFF
    return raw & 0xFFFF


def _raw_invuln_frames(raw_frames: Any) -> int:
    """Return the decoded duration without deciding whether it is protection."""
    try:
        return max(0, int(raw_frames or 0))
    except Exception:
        return 0


def _field_write_at(buf: bytes, pos: int) -> Optional[Dict[str, int]]:
    """Decode one ``04 xx 60 00 +field = value`` script operation."""
    if pos < 0 or pos + 16 > len(buf):
        return None
    if buf[pos] != 0x04 or buf[pos + 2:pos + 4] != b"\x60\x00":
        return None
    if rd_u32_be(buf, pos + 8) != INVULN_PROBE_MARKER:
        return None
    return {
        "addr_rel": pos,
        "op": int(buf[pos + 1]),
        "field": int(rd_u32_be(buf, pos + 4)),
        "value": int(rd_u32_be(buf, pos + 12)),
    }


def _last_field_write(
    buf: bytes,
    *,
    start: int,
    end: int,
    field: int,
) -> Optional[Dict[str, int]]:
    """Return the nearest matching field write before ``end`` in a local phase."""
    begin = max(0, int(start))
    finish = min(len(buf), int(end))
    last: Optional[Dict[str, int]] = None
    for pos in range(begin, max(begin, finish - 15)):
        op = _field_write_at(buf, pos)
        if op and int(op.get("field", -1)) == int(field):
            last = op
    return last


def _has_field_write(
    buf: bytes,
    *,
    start: int,
    end: int,
    field: int,
    value: Optional[int] = None,
    op_type: Optional[int] = None,
) -> bool:
    """Check a narrow script window for a specific field operation."""
    begin = max(0, int(start))
    finish = min(len(buf), int(end))
    for pos in range(begin, max(begin, finish - 15)):
        op = _field_write_at(buf, pos)
        if not op or int(op.get("field", -1)) != int(field):
            continue
        if value is not None and int(op.get("value", -1)) != int(value):
            continue
        if op_type is not None and int(op.get("op", -1)) != int(op_type):
            continue
        return True
    return False


def _invuln_confidence_score(name: str) -> int:
    # "confirmed" is an external/runtime proof tier, intentionally above
    # every static-script confidence class.
    return {"confirmed": 4, "high": 3, "medium": 2, "low": 1, "bootstrap": 0, "none": 0}.get(str(name or "none"), 0)


def _is_runtime_confirmed_invuln_action(mv: Dict[str, Any], char_id: Optional[int]) -> bool:
    """Return true only for a specifically recorded in-game proof row."""
    try:
        key = (int(char_id), int(mv.get("id")))
    except Exception:
        return False
    return key in CONFIRMED_INVULN_ACTIONS


def _mark_runtime_confirmed_invuln(
    mv: Dict[str, Any],
    probes: List[Dict[str, Any]],
    *,
    char_id: Optional[int],
) -> None:
    """Promote the matching, structurally strong probe on a proven reference.

    A move-level runtime test confirms that its startup protection exists, but
    it should not blindly certify unrelated low-confidence helper packets in
    the same section.  Promote only the same high-topology finite/held phase
    that the scanner would otherwise render as [H].
    """
    if not _is_runtime_confirmed_invuln_action(mv, char_id):
        return
    for probe in probes or []:
        if str(probe.get("invuln_confidence") or "") != "high":
            continue
        label = str(probe.get("display_label") or "").strip()
        if not label:
            continue
        probe["invuln_confidence"] = "confirmed"
        probe["runtime_confirmed"] = True
        if str(probe.get("invuln_kind") or "") == "event_hold":
            # Held states remain raw/debug evidence only; never label them as a
            # confirmed number of invulnerable frames.
            probe["display_label"] = ""
        else:
            frames = int(probe.get("display_frames") or 0)
            probe["display_label"] = f"{frames}f [C]" if frames > 0 else ""


def _annotate_invuln_probe_context(
    buf: bytes,
    base_abs: int,
    mv: Dict[str, Any],
    probe: Dict[str, Any],
    *,
    timing_limit: Optional[int],
) -> None:
    """Classify one phase timer without pretending the timer alone is invuln.

    The important anti-noise rule is structural, not duration-based:
      clear +0x1218 -> arm 2f -> +0x1E8 action handoff
    is the generic normal bootstrap and is never displayed as invulnerability.

    A strong candidate has the same local phase shape as the protected
    reference scripts: an active phase setup instruction plus a preceding
    +0x58 write with bit 0 set.  This remains a confidence signal; exact
    collision semantics still need runtime validation unless the row is in
    the explicit runtime-confirmed reference table.
    """
    try:
        addr = int(probe.get("addr") or 0)
        pos = addr - int(base_abs)
    except Exception:
        pos = -1
    frames = _raw_invuln_frames(probe.get("frames"))
    if pos < 0 or pos >= len(buf):
        probe.update({
            "candidate_frames": 0,
            "display_frames": 0,
            "invuln_confidence": "none",
            "invuln_kind": "invalid",
            "display_label": "",
        })
        return

    local_start = max(0, pos - INVULN_CONTEXT_LOOKBACK)
    state58 = _last_field_write(
        buf, start=local_start, end=pos,
        field=INVULN_PHASE_STATE_FIELD,
    )
    state_value = int((state58 or {}).get("value") or 0)
    state_bit0 = bool(state58 and (state_value & 0x1))
    phase_setup_addr = buf.rfind(INVULN_PHASE_SETUP_HDR, local_start, pos)
    has_phase_setup = phase_setup_addr >= 0

    # Exact generic normal-entry shape.  A real 2f invul phase will not be
    # discarded just for being 2f; it must match this clear->arm->handoff chain.
    bootstrap_clear = _has_field_write(
        buf,
        start=max(0, pos - INVULN_BOOTSTRAP_LOOKBACK),
        end=pos,
        field=INVULN_PROBE_FIELD,
        value=0,
        op_type=INVULN_BOOTSTRAP_CLEAR_TYPE,
    )
    bootstrap_handoff = _has_field_write(
        buf,
        start=pos + 16,
        end=min(len(buf), pos + INVULN_BOOTSTRAP_LOOKAHEAD),
        field=INVULN_ACTION_FIELD,
        op_type=0x01,
    )
    is_bootstrap = bool(frames == 2 and bootstrap_clear and bootstrap_handoff)
    is_hold = bool(frames >= INVULN_HOLD_SENTINEL_FRAMES)
    timing_overrun = bool(
        not is_hold
        and timing_limit is not None
        and frames > int(timing_limit)
    )

    if is_bootstrap:
        confidence = "bootstrap"
        kind = "normal_entry_timer"
        display_frames = 0
        label = ""
    else:
        # The finite/held timer itself is retained.  The phase-state/topology
        # evidence determines confidence; it does not impose a magic 6/10/13f
        # whitelist and therefore preserves legitimate 1f/2f cases.
        if state_bit0 and has_phase_setup:
            confidence = "high"
        elif has_phase_setup or state_bit0:
            confidence = "medium"
        elif frames > 0:
            confidence = "low"
        else:
            confidence = "none"

        # A timer that outlasts a concrete action's active endpoint is weaker,
        # but not silently deleted: phase timing can belong to a parent/helper.
        if timing_overrun and confidence == "high":
            confidence = "medium"
        elif timing_overrun and confidence == "medium":
            confidence = "low"

        if is_hold:
            # 0x3E7 is an event-held state, not a frame count.  Keep the raw
            # probe for the inspector/debug path, but never promote it into
            # the public Invuln column as though it were evidence of a finite
            # startup duration.  This removes KO/reaction/throw noise while
            # preserving all finite candidate values.
            kind = "event_hold"
            display_frames = 0
            label = ""
        elif frames > 0:
            kind = "timed_phase"
            display_frames = frames
            label = f"{frames}f [{confidence[:1].upper()}]" if confidence != "none" else ""
        else:
            kind = "zero"
            display_frames = 0
            label = ""

    probe.update({
        "candidate_frames": display_frames,
        "display_frames": display_frames,
        "display_label": label,
        "invuln_confidence": confidence,
        "invuln_kind": kind,
        "candidate_limit": timing_limit,
        "timing_overrun": timing_overrun,
        "bootstrap": is_bootstrap,
        "phase_setup_addr": (base_abs + phase_setup_addr) if phase_setup_addr >= 0 else None,
        "phase_state58_addr": (base_abs + int(state58.get("addr_rel"))) if state58 else None,
        "phase_state58_value": state_value if state58 else None,
        "phase_state58_bit0": state_bit0,
    })


def _invuln_startup_active_limit(mv: Dict[str, Any]) -> Optional[int]:
    """Return a reliable startup+active endpoint for a concrete action root."""
    if str(mv.get("source") or "") not in {"anim_hdr", "air_hdr", "cmd_hdr"}:
        return None

    ends: List[int] = []

    def _add_range(start_value: Any, end_value: Any) -> None:
        try:
            start_frame = int(start_value)
            end_frame = int(end_value)
        except Exception:
            return
        if start_frame >= 1 and end_frame >= start_frame:
            ends.append(end_frame)

    _add_range(mv.get("active_start"), mv.get("active_end"))
    _add_range(mv.get("active2_start"), mv.get("active2_end"))
    for seg in mv.get("hit_segments") or []:
        if isinstance(seg, dict):
            _add_range(seg.get("active_start"), seg.get("active_end"))

    return max(ends) if ends else None


def apply_invuln_startup_active_gate(
    mv: Dict[str, Any],
    probes: List[Dict[str, Any]],
    *,
    buf: Optional[bytes] = None,
    base_abs: Optional[int] = None,
    char_id: Optional[int] = None,
) -> Optional[int]:
    """Attach phase-context confidence and a soft timing sanity penalty.

    The old hard timing cutoff erased valid helper/held phases.  Keep it as a
    confidence penalty only; the exact bootstrap pattern is what removes the
    ubiquitous normal 2f entries.
    """
    limit = _invuln_startup_active_limit(mv)
    for probe in probes or []:
        if buf is not None and base_abs is not None:
            _annotate_invuln_probe_context(
                buf, int(base_abs), mv, probe, timing_limit=limit,
            )
        else:
            frames = _raw_invuln_frames(probe.get("frames"))
            probe["candidate_frames"] = frames if 0 < frames < INVULN_HOLD_SENTINEL_FRAMES else 0
            probe["display_frames"] = probe["candidate_frames"]
            probe["invuln_confidence"] = "low" if probe["candidate_frames"] else "none"
            probe["display_label"] = f"{probe['candidate_frames']}f [L]" if probe["candidate_frames"] else ""

    _mark_runtime_confirmed_invuln(mv, probes, char_id=char_id)
    return limit


def _next_anim_boundary(buf: bytes, root_rel: int, scan_end: int) -> int:
    """Find the next ANIM command after this root as a safe local fallback.

    Dynamic scans can provide a stronger next-root boundary from the complete
    action list.  Profile refreshes do not always have that list, so this keeps
    a fixed-size probe from wandering into the next script and lending its
    phase packet to the previous move (Polimar 2C -> 6C was the proof case).
    """
    try:
        root_rel = int(root_rel)
    except Exception:
        return scan_end
    begin = max(0, root_rel + 1)
    nxt = buf.find(bytes(ANIM_HDR), begin, scan_end)
    return nxt if nxt >= 0 else scan_end


def collect_invuln_probes(
    buf: bytes,
    base_abs: int,
    mv_abs: int,
    *,
    first_range: int = INVULN_FIRST_HIT_RANGE,
    scan_range: int = INVULN_SCAN_RANGE,
    owner_start_abs: Optional[int] = None,
    owner_end_abs: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Return raw ``+0x1218`` phase/timer packets for one action section.

    The packet is retained as evidence because finite values can correlate with
    startup invulnerability. It is not proof by itself: 999 is a held-state
    sentinel used in KO, crumple, throw, and assist scripts. The caller applies
    the action-aware startup+active timing sanity check after normal frame
    fields have been attached.

    Scan is limited to the resolved owner window.  Table pointers may land a
    few bytes after their setup packet, so table rows receive a bounded prefix
    owned by *that* row.  The previous row's end is trimmed by the same prefix,
    which prevents Polimar 2C from borrowing 6C's pre-root timer.
    """
    rel = mv_abs - base_abs
    if rel < 0 or rel >= len(buf):
        return []

    try:
        owner_start_rel = int(owner_start_abs or 0) - int(base_abs)
    except Exception:
        owner_start_rel = 0
    start = max(0, owner_start_rel) if owner_start_rel > 0 else max(0, rel - 8)

    # The range is measured forward from the visible action root; the optional
    # table preamble is extra ownership context, not a reduction in the scan.
    scan_end = min(len(buf), rel + int(scan_range or INVULN_SCAN_RANGE))
    try:
        owner_end_rel = int(owner_end_abs or 0) - int(base_abs)
    except Exception:
        owner_end_rel = 0
    if owner_end_rel > start:
        scan_end = min(scan_end, owner_end_rel)
    else:
        scan_end = _next_anim_boundary(buf, rel, scan_end)

    # Keep the historical early-window safety check, but make it respect the
    # actual section boundary rather than a neighboring script.
    first_end = min(scan_end, start + int(first_range or INVULN_FIRST_HIT_RANGE))
    first = buf.find(INVULN_PROBE_HDR, start, first_end)
    if first < 0:
        return []

    probes: List[Dict[str, Any]] = []
    pos = start
    while True:
        idx = buf.find(INVULN_PROBE_HDR, pos, scan_end)
        if idx < 0:
            break
        pos = idx + 1
        if idx + 16 > len(buf):
            continue
        field = rd_u32_be(buf, idx + 4)
        marker = rd_u32_be(buf, idx + 8)
        value = rd_u32_be(buf, idx + 12)
        if marker != INVULN_PROBE_MARKER or field != INVULN_PROBE_FIELD:
            continue
        frames = _invuln_frames_from_value(value)
        probes.append({
            "addr": base_abs + idx,
            "field": field,
            "value": value,
            "frames": frames,
            "candidate_frames": _raw_invuln_frames(frames),
        })
    return probes


def summarize_invuln_probes(probes: List[Dict[str, Any]]) -> str:
    """Render non-bootstrap phases with concise confidence labels."""
    shown: List[Tuple[int, int, str]] = []
    seen: set[str] = set()
    for probe in probes or []:
        label = str(probe.get("display_label") or "").strip()
        if not label or label in seen:
            continue
        confidence = _invuln_confidence_score(str(probe.get("invuln_confidence") or "none"))
        try:
            frames = int(probe.get("display_frames") or 0)
        except Exception:
            frames = 0
        shown.append((confidence, frames, label))
        seen.add(label)
    if not shown:
        return ""
    shown.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return " / ".join(label for _, _, label in shown[:4])


def best_candidate_invuln_probe(probes: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Return the strongest displayed phase, preferring confidence over length."""
    best: Optional[Dict[str, Any]] = None
    best_key = (-1, -1)
    for probe in probes or []:
        if not str(probe.get("display_label") or "").strip():
            continue
        score = _invuln_confidence_score(str(probe.get("invuln_confidence") or "none"))
        try:
            frames = int(probe.get("display_frames") or 0)
        except Exception:
            frames = 0
        key = (score, frames)
        if key > best_key:
            best = probe
            best_key = key
    return best


def should_probe_invuln(mv: Dict[str, Any], char_id: Optional[int] = None) -> bool:
    """Probe every move for the +0x1218 startup-protection signature.

    This signature is specific enough that broad probing is more useful than the
    older hand-picked whitelist. Exact clear->2f->handoff normal bootstraps
    are suppressed, while all other finite durations and held phases receive
    an evidence-based confidence label.
    """
    return True


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


def _clear_push_pull_fields(row: Dict[str, Any]) -> None:
    """Clear the optional 35/0C hit-spacing fields without deleting legacy keys."""
    for key in (
        "ground_kb", "ground_kb_addr", "ground_kb_y", "ground_kb_y_addr",
        "ground_kb_packet_addr", "ground_kb_mode", "ground_kb_aux",
        "push_pull_packets",
    ):
        row[key] = None if key != "push_pull_packets" else []


def _push_pull_chain_reaches_stun(packet_addr: int,
                                   ground_kb_blocks: List[Tuple[int, Dict[str, Any]]],
                                   stun_blocks: List[Tuple[int, Any]],
                                   end_addr: int) -> bool:
    """Return True when a 35/0C chain ends in an owned stun packet.

    Some hit bundles place a mode-0 35/0C immediately before a mode-1 35/0C.
    The stun begins after the second packet, not directly after the first.  Walk
    only contiguous 16-byte 35/0C packets, then require a nearby stun packet.
    """
    g_addrs = {int(addr) for addr, _data in ground_kb_blocks}
    cursor = int(packet_addr)
    for _ in range(4):
        nxt = cursor + GROUND_KB_TOTAL_LEN
        if nxt in g_addrs and nxt < int(end_addr):
            cursor = nxt
            continue
        break

    lo = cursor + GROUND_KB_TOTAL_LEN
    hi = min(int(end_addr), lo + 0x40)
    return any(lo <= int(addr) < hi for addr, _data in stun_blocks)


def _owned_push_pull_blocks(ground_kb_blocks: List[Tuple[int, Dict[str, Any]]],
                            stun_blocks: List[Tuple[int, Any]],
                            *,
                            start_addr: int,
                            end_addr: int,
                            kb_addr: Optional[int] = None,
                            max_gap: int = 0x900) -> List[Tuple[int, Dict[str, Any]]]:
    """Find valid 35/0C hit Push/Pull packets inside one hit bundle.

    A packet must be after the bundle's base KB packet (when known), close to
    the hit bundle, and lead through its own contiguous 35/0C chain to a valid
    stun packet.  The result is sorted with mode 0 first: that is the tested
    Ryu Tatsu push/vacuum form.  Other modes are retained in metadata so they
    can be surfaced later without guessing their semantics.
    """
    start = int(start_addr)
    end = int(end_addr)
    if end <= start:
        return []
    if kb_addr is not None:
        start = max(start, int(kb_addr) + KNOCKBACK_TOTAL_LEN)
    limit = min(end, start + max(0, int(max_gap)))

    found: List[Tuple[int, Dict[str, Any]]] = []
    for addr, data in ground_kb_blocks:
        a = int(addr)
        if not (start <= a < limit):
            continue
        if not _push_pull_chain_reaches_stun(a, ground_kb_blocks, stun_blocks, end):
            continue
        found.append((a, data))

    found.sort(key=lambda item: (0 if int(item[1].get("ground_kb_mode") or 0) == 0 else 1, item[0]))
    return found


def _attach_push_pull(row: Dict[str, Any],
                      candidates: List[Tuple[int, Dict[str, Any]]]) -> None:
    """Attach the tested primary 35/0C packet and retain the full local chain."""
    _clear_push_pull_fields(row)
    if not candidates:
        return

    packet_addr, data = candidates[0]
    row["ground_kb_packet_addr"] = int(packet_addr)
    row["ground_kb_addr"] = int(packet_addr) + GROUND_KB_VALUE_OFF
    row["ground_kb_y_addr"] = int(packet_addr) + GROUND_KB_AUX_OFF
    row["ground_kb"] = data.get("ground_kb")
    row["ground_kb_y"] = data.get("ground_kb_y")
    row["ground_kb_mode"] = data.get("ground_kb_mode")
    row["ground_kb_aux"] = data.get("ground_kb_aux")
    row["push_pull_packets"] = [
        {
            "packet_addr": int(addr),
            "mode": data_item.get("ground_kb_mode"),
            "x": data_item.get("ground_kb"),
            "aux": data_item.get("ground_kb_y"),
        }
        for addr, data_item in candidates
    ]


def pair_explicit_ground_push_for_move(mv: Dict[str, Any],
                                        moves: List[Dict[str, Any]],
                                        blocks: Dict[str, Any]) -> None:
    """Attach the move-owned 35/0C hit Push/Pull packet dynamically.

    Prefer the first concrete hit segment, because multi-hit specials can have
    a different push/pull setting per hit.  When a row has no segment overlay,
    fall back to a bounded search from its own base KB packet.  The old version
    only accepted the immediately-adjacent 35/0C packet and could miss a
    valid mode chain or borrow a nearby action's packet.
    """
    _clear_push_pull_fields(mv)

    segments = mv.get("hit_segments") or []
    if isinstance(segments, list):
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            packet = seg.get("ground_kb_packet_addr")
            if packet is None:
                continue
            _attach_push_pull(mv, [
                (int(packet), {
                    "ground_kb_mode": seg.get("ground_kb_mode"),
                    "ground_kb": seg.get("ground_kb"),
                    "ground_kb_y": seg.get("ground_kb_y"),
                    "ground_kb_aux": seg.get("ground_kb_aux"),
                })
            ])
            # Preserve the segment's complete local candidate list when present.
            if isinstance(seg.get("push_pull_packets"), list):
                mv["push_pull_packets"] = list(seg.get("push_pull_packets") or [])
            return

    try:
        mv_abs = int(mv.get("abs") or 0)
    except Exception:
        mv_abs = 0
    if not mv_abs:
        return

    source = str(mv.get("source") or "")
    boundary = _next_invuln_action_boundary(moves, mv_abs, mv_source=source)
    if not boundary or boundary <= mv_abs:
        boundary = mv_abs + HIT_SEGMENT_MAX_GAP

    kb_blocks = sorted(blocks.get("kb_blocks") or [], key=lambda x: x[0])
    g_blocks = sorted(blocks.get("ground_kb_blocks") or [], key=lambda x: x[0])
    stuns = sorted(blocks.get("stun_blocks") or [], key=lambda x: x[0])
    kbblk = first_block_forward(kb_blocks, mv_abs, int(boundary), max_gap=HIT_SEGMENT_MAX_GAP)
    if not kbblk:
        return

    candidates = _owned_push_pull_blocks(
        g_blocks, stuns,
        start_addr=int(kbblk[0]),
        end_addr=int(boundary),
        kb_addr=int(kbblk[0]),
        max_gap=0x180,
    )
    _attach_push_pull(mv, candidates)


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
    ground_kb_blocks = sorted(blocks.get("ground_kb_blocks") or [], key=lambda x: x[0])
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
        mv["ground_kb"] = mv["ground_kb_addr"] = mv["ground_kb_y"] = mv["ground_kb_y_addr"] = mv["ground_kb_packet_addr"] = None
        mv["ground_kb_mode"] = mv["ground_kb_aux"] = None
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

    ground_start = kbblk[0] if kbblk else kb_start
    push_candidates = _owned_push_pull_blocks(
        ground_kb_blocks, stun_blocks,
        start_addr=ground_start,
        end_addr=boundary,
        kb_addr=(kbblk[0] if kbblk else None),
    )
    _attach_push_pull(mv, push_candidates)
    gblk = push_candidates[0] if push_candidates else None

    stun_start = gblk[0] if gblk else ground_start
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


def _signed_u32(raw: Any) -> int:
    try:
        value = int(raw) & 0xFFFFFFFF
    except Exception:
        return 0
    return value - 0x100000000 if value & 0x80000000 else value


def _find_owned_late_stun_pair(
    buf: bytes,
    base_abs: int,
    *,
    start_abs: int,
    end_abs: int,
) -> Optional[Dict[str, int]]:
    """Find a direct +0x254/+0x258 override inside one action owner.

    Some normal scripts first arm ``-2/-2`` and only write their true stun
    values after helper/control commands.  Morrigan j.B and j.C are concrete
    examples.  The ordinary STUN_HDR matcher intentionally stays strict, so
    resolve this alternate two-write form here without reclassifying arbitrary
    nearby field writes as editable standard packets.
    """
    try:
        first = max(0, int(start_abs) - int(base_abs))
        last = min(len(buf), int(end_abs) - int(base_abs))
    except Exception:
        return None
    if last - first < 32:
        return None

    for pos in range(first, last - 31):
        first_op = _field_write_at(buf, pos)
        if not first_op:
            continue
        if int(first_op.get("op", -1)) != 0x01 or int(first_op.get("field", -1)) != 0x0254:
            continue
        second_op = _field_write_at(buf, pos + 16)
        if not second_op:
            continue
        if int(second_op.get("op", -1)) != 0x01 or int(second_op.get("field", -1)) != 0x0258:
            continue

        hitstun = _signed_u32(first_op.get("value"))
        blockstun = _signed_u32(second_op.get("value"))
        # -2/-2 is the default sentinel and must be handled separately.
        # Frame counts are bytes in every confirmed direct packet.
        if hitstun < 0 or blockstun < 0 or hitstun > 0xFF or blockstun > 0xFF:
            continue
        return {
            "addr": int(base_abs) + pos,
            "hitstun": int(hitstun),
            "blockstun": int(blockstun),
        }
    return None


def _has_owned_default_stun_sentinel(
    buf: bytes,
    base_abs: int,
    *,
    start_abs: int,
    end_abs: int,
) -> bool:
    """Return true for an action-owned direct ``-2/-2`` initialization.

    The scan is bounded to the action and stops at the concrete hit packet, so
    a neighboring action's initialization cannot donate defaults to this row.
    """
    try:
        first = max(0, int(start_abs) - int(base_abs))
        last = min(len(buf), int(end_abs) - int(base_abs))
    except Exception:
        return False
    if last - first < 32:
        return False

    for pos in range(first, last - 31):
        first_op = _field_write_at(buf, pos)
        if not first_op or int(first_op.get("op", -1)) != 0x01 or int(first_op.get("field", -1)) != 0x0254:
            continue
        second_op = _field_write_at(buf, pos + 16)
        if not second_op or int(second_op.get("op", -1)) != 0x01 or int(second_op.get("field", -1)) != 0x0258:
            continue
        if _signed_u32(first_op.get("value")) == -2 and _signed_u32(second_op.get("value")) == -2:
            return True
    return False


def resolve_engine_default_stun(
    mv: Dict[str, Any],
    moves: List[Dict[str, Any]],
    buf: bytes,
    base_abs: int,
) -> None:
    """Resolve normal stun values that are delegated to the engine.

    Existing parsed STUN_HDR packets are left untouched.  This only fills a
    blank normal row when its *own* script contains the -2/-2 default sentinel:

      1. Prefer a later direct override in the same action.
      2. Otherwise map the normal damage hit-level (4/8/12) to the resolver's
         12/9, 17/12, or 21/15 default.

    The resolved defaults intentionally have no ``stun_addr`` because there is
    no standard packet to edit; this keeps the existing writer from patching a
    neighbor or a nonstandard field pair.
    """
    if mv.get("hitstun") is not None or mv.get("blockstun") is not None:
        return
    if str(mv.get("kind") or "") != "normal":
        return

    try:
        mv_abs = int(mv.get("abs") or 0)
    except Exception:
        return
    if not mv_abs:
        return

    # chr_tbl roots are gameplay-action owners.  The same owner boundary that
    # fixed Shoryu L/M/H prevents late values from the next normal leaking in.
    boundary = _next_invuln_action_boundary(moves, mv_abs, mv_source=mv.get("source"))
    if not boundary or boundary <= mv_abs:
        return
    boundary = min(int(boundary), mv_abs + HIT_SEGMENT_SCAN_MAX)

    try:
        damage_addr = int(mv.get("damage_addr") or 0)
    except Exception:
        damage_addr = 0
    if not damage_addr or damage_addr < mv_abs or damage_addr >= boundary:
        return

    # Nonstandard direct overrides can occur anywhere inside the owning action
    # after its initial data declarations.  They take precedence over the
    # generic engine default.  Do not start at the damage record: some scripts
    # declare damage before they arm the -2/-2 default sentinel.
    override = _find_owned_late_stun_pair(
        buf, base_abs, start_abs=mv_abs, end_abs=boundary,
    )
    if override is not None:
        mv["hitstun"] = int(override["hitstun"])
        mv["blockstun"] = int(override["blockstun"])
        mv["hitstop"] = None
        mv["stun_addr"] = None
        mv["stun_source"] = "owned_direct_override"
        mv["stun_source_addr"] = int(override["addr"])
        return

    # The -2/-2 sentinel can be declared before or after the initial damage
    # record, depending on the action layout, so keep this scan action-owned
    # rather than relying on textual packet order.
    if not _has_owned_default_stun_sentinel(
        buf, base_abs, start_abs=mv_abs, end_abs=boundary,
    ):
        return

    try:
        flag = int(mv.get("damage_flag") or 0) & 0x0F
    except Exception:
        flag = 0
    resolved = DEFAULT_NORMAL_STUN_BY_DAMAGE_FLAG.get(flag)
    if resolved is None:
        return

    mv["hitstun"], mv["blockstun"] = int(resolved[0]), int(resolved[1])
    mv["hitstop"] = None
    mv["stun_addr"] = None
    mv["stun_source"] = "engine_default_hit_level"
    # Runtime reverse profiling is opt-in only for this exact -2/-2 resolver
    # family. Direct/static signature rows never receive this marker.
    mv["runtime_profile_eligible"] = True
    mv["stun_default_level"] = int(flag)


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


def _next_invuln_action_boundary(
    moves: List[Dict[str, Any]],
    mv_abs: int,
    mv_source: Optional[str] = None,
) -> int:
    """Return the end of the action that owns a ``+0x1218`` phase.

    A chr_tbl root is authoritative: the next chr_tbl root is the next gameplay
    action, even when the current script contains nested 0x0135/6/7/8 animation
    commands.  That is what separates Ryu's Shoryu L/M/H 6/10/13f startup
    phases.  Non-table roots use the next explicit internal root as a local
    boundary so helper scripts cannot scan into the next branch.
    """
    try:
        here = int(mv_abs)
    except Exception:
        return 0

    rows: List[Tuple[int, str]] = []
    for row in moves or []:
        try:
            addr = int(row.get("abs") or 0)
        except Exception:
            continue
        if addr > here + 0x10:
            rows.append((addr, str(row.get("source") or "")))

    if str(mv_source or "") == "table":
        table_roots = [addr for addr, source in rows if source == "table"]
        if table_roots:
            return min(table_roots)
        # A stale/cache-only profile can lack neighboring table rows.  The
        # generic root fallback is still bounded and safer than a blind 0x700.

    root_sources = {"table", "anim_hdr", "air_hdr", "cmd_hdr", "strict", "legacy_special"}
    candidates = [addr for addr, source in rows if source in root_sources]
    return min(candidates) if candidates else here + INVULN_SCAN_RANGE


def _invuln_owner_window(
    moves: List[Dict[str, Any]],
    mv_abs: int,
    mv_source: Optional[str] = None,
) -> Tuple[Optional[int], Optional[int]]:
    """Return a bounded ownership window for phase packets.

    chr_tbl entries can point after the action's setup packet.  Give the current
    table row its small preamble, then reserve the same preamble before the next
    table row for that next action.  This makes the assignment exclusive.
    """
    try:
        here = int(mv_abs)
    except Exception:
        return (None, None)
    if str(mv_source or "") != "table":
        return (None, _next_invuln_action_boundary(moves, here, mv_source=mv_source))

    table_roots = sorted({
        int(row.get("abs") or 0)
        for row in (moves or [])
        if str(row.get("source") or "") == "table" and int(row.get("abs") or 0) > 0
    })
    if here not in table_roots:
        return (here - 8, _next_invuln_action_boundary(moves, here, mv_source="table"))
    idx = table_roots.index(here)
    prev_root = table_roots[idx - 1] if idx > 0 else None
    next_root = table_roots[idx + 1] if idx + 1 < len(table_roots) else None
    start = here - INVULN_TABLE_PREAMBLE
    if prev_root is not None:
        start = max(start, prev_root)
    end = here + INVULN_SCAN_RANGE
    if next_root is not None:
        reserved = next_root - INVULN_TABLE_PREAMBLE
        end = reserved if reserved > here else next_root
    return (start, end)


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
    ground_kb_blocks = sorted(blocks.get("ground_kb_blocks") or [], key=lambda x: x[0])
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
        ground_start = (kbblk[0] if kbblk else kb_start)
        push_candidates = _owned_push_pull_blocks(
            ground_kb_blocks, stun_blocks,
            start_addr=ground_start,
            end_addr=seg_end,
            kb_addr=(kbblk[0] if kbblk else None),
        )
        gblk = push_candidates[0] if push_candidates else None
        stun_start = (gblk[0] if gblk else ground_start)
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
        if push_candidates:
            _attach_push_pull(seg, push_candidates)
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
        "ground_kb", "ground_kb_addr", "ground_kb_y", "ground_kb_y_addr", "ground_kb_packet_addr", "ground_kb_mode", "ground_kb_aux", "push_pull_packets",
        "hitstun", "blockstun", "hitstop", "stun_addr",
    ):
        if key in seg:
            mv[key] = seg.get(key)
# ============================================================
# Move anchor collection
# ============================================================

def collect_move_anchors(
    buf: bytes,
    base_abs: int,
    tbl_move_addrs: Optional[List[int]] = None,
    *,
    tbl_move_entries: Optional[List[Tuple[int, int]]] = None,
) -> List[Dict[str, Any]]:
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

        # Table rows are the canonical gameplay-action owners.  Several games
        # intentionally expose both a base animation (0x0006) and a concrete
        # action variant (0x0106) with the same low byte.  Keep both here; the
        # post-parse quality collapse chooses the populated owner later.
        if source == "table":
            if abs_addr in seen_abs:
                return
            seen_abs.add(abs_addr)
            moves.append({"kind": kind, "abs": abs_addr, "id": aid, "source": source})
            return

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

    # chr_tbl index is the action ID.  Do not infer a table row's identity from
    # the first nested animation command: special wrappers commonly contain a
    # shared 0x0135 startup and 0x0136/7/8 child animations.  That old shortcut
    # made every strength borrow a neighbor's timing packet.
    if tbl_move_entries:
        for action_id, mv_abs in tbl_move_entries:
            aid = int(action_id)
            kind = "normal" if ((aid & 0xFF) in NORMAL_IDS) else "special"
            add_mv(kind, int(mv_abs), aid, "table")
    elif tbl_move_addrs:
        # Compatibility for callers that only have addresses.  Keep the old
        # best-effort fallback, but all live scans now pass tbl_move_entries.
        for mv_abs in tbl_move_addrs:
            off = mv_abs - base_abs
            if 0 <= off < len(buf) - 4:
                aid = get_anim_id_after_hdr_strict(buf, off)
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
    ground_kb_blocks: List[Tuple[int, Dict[str, Any]]] = []
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
        d = parse_ground_knockback(buf, p)
        if d:
            ground_kb_blocks.append((base_abs + p, d))
            p += GROUND_KB_TOTAL_LEN
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
        "ground_kb_blocks": ground_kb_blocks,
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
    ground_kb_blocks     = sorted(blocks.get("ground_kb_blocks") or [], key=lambda x: x[0])
    stun_blocks          = sorted(blocks["stun_blocks"], key=lambda x: x[0])

    meter_idx = 0
    active_idx = 0
    inline_idx = 0
    dmg_idx = 0
    atkprop_idx = 0
    hitreact_idx = 0
    kb_idx = 0
    ground_kb_idx = 0
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
        mv["ground_kb"] = mv["ground_kb_addr"] = mv["ground_kb_y"] = mv["ground_kb_y_addr"] = mv["ground_kb_packet_addr"] = None
        mv["ground_kb_mode"] = mv["ground_kb_aux"] = None
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

        gblk, ground_kb_idx = pick_best_block_from_idx(mv_abs, ground_kb_blocks, ground_kb_idx)
        if gblk:
            gkb = gblk[1]
            mv["ground_kb_packet_addr"] = gblk[0]
            mv["ground_kb_addr"] = gblk[0] + GROUND_KB_VALUE_OFF
            mv["ground_kb_y_addr"] = gblk[0] + GROUND_KB_AUX_OFF
            mv["ground_kb"] = gkb.get("ground_kb")
            mv["ground_kb_y"] = gkb.get("ground_kb_y")
            mv["ground_kb_mode"] = gkb.get("ground_kb_mode")
            mv["ground_kb_aux"] = gkb.get("ground_kb_aux")

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
        mv["invuln_frames"] = 0
        mv["invuln_addr"] = None
        mv["invuln_startup_active_limit"] = None
        mv["invuln_confidence"] = "none"
        mv["invuln_kind"] = ""

        mv["hit_segments"] = []
        mv["multi_hit_count"] = 0
        # Multi-hit bundles are meaningful for player move rows and unlabeled
        # hit-script helper rows.  Do not attach them to generic system states
        # such as landing/KO/knockdown just because those scripts happen to use
        # the same active/damage packet format.
        # Table, strict, and legacy-special roots are real gameplay owners too.
        # Let their concrete active/damage bundles create hit children; the
        # bundle parser already rejects helper records without a real hit.
        collect_segments_for_row = mv.get("source") in {
            "table", "anim_hdr", "air_hdr", "cmd_hdr", "super_end", "strict", "legacy_special",
        }
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

        # 35/0C is optional and must never borrow a neighboring move's packet.
        # Re-pair it from the move's own 35/07-or-09 bundle after all segment
        # overlays have finished.
        pair_explicit_ground_push_for_move(mv, moves, blocks)

        # Some characters deliberately leave the regular stun packet at -2/-2
        # and use the engine's normal hit-level resolver instead.  Resolve that
        # only after every ordinary/segment packet pairing attempt has had a
        # chance to populate the row.
        resolve_engine_default_stun(mv, moves, buf, base_abs)

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

        if should_probe_invuln(mv, char_id):
            owner_start, owner_end = _invuln_owner_window(
                moves, mv_abs, mv_source=mv.get("source"),
            )
            invuln_probes = collect_invuln_probes(
                buf, base_abs, mv_abs,
                owner_start_abs=owner_start,
                owner_end_abs=owner_end,
            )
            mv["invuln_probes"] = invuln_probes
            mv["invuln_probe_count"] = len(invuln_probes)
            mv["invuln_startup_active_limit"] = apply_invuln_startup_active_gate(mv, invuln_probes, buf=buf, base_abs=base_abs, char_id=char_id)
            mv["invuln"] = summarize_invuln_probes(invuln_probes)
            best_invuln = best_candidate_invuln_probe(invuln_probes)
            mv["invuln_frames"] = int((best_invuln or {}).get("candidate_frames") or 0)
            try:
                mv["invuln_addr"] = int((best_invuln or {}).get("addr") or 0) or None
            except Exception:
                mv["invuln_addr"] = None
            mv["invuln_confidence"] = str((best_invuln or {}).get("invuln_confidence") or "none")
            mv["invuln_kind"] = str((best_invuln or {}).get("invuln_kind") or "")


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


def _empty_preview_profile_doc() -> Dict[str, Any]:
    return {"version": PREVIEW_PROFILE_CACHE_VERSION, "profiles": {}}


def _normalize_preview_profile_doc(doc: Any) -> Dict[str, Any]:
    if not isinstance(doc, dict):
        return _empty_preview_profile_doc()
    if int(doc.get("version") or 0) != PREVIEW_PROFILE_CACHE_VERSION:
        return _empty_preview_profile_doc()
    profiles = doc.get("profiles")
    if not isinstance(profiles, dict):
        return _empty_preview_profile_doc()
    out = dict(doc)
    out["version"] = PREVIEW_PROFILE_CACHE_VERSION
    out["profiles"] = profiles
    return out


def _read_preview_profile_doc_file(path: str) -> Optional[Dict[str, Any]]:
    if not path:
        return None
    try:
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return _normalize_preview_profile_doc(json.load(f))
    except Exception:
        return None


def _merge_preview_profile_docs(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    merged = _normalize_preview_profile_doc(copy.deepcopy(base))
    over = _normalize_preview_profile_doc(overlay)
    profiles = merged.setdefault("profiles", {})
    for key, value in (over.get("profiles") or {}).items():
        if isinstance(value, dict):
            profiles[str(key)] = value
    # Runtime cache metadata should win when it exists, while a bundled seed
    # still remains valid on first launch.
    for key in ("generated_at", "source_profile_sha256"):
        if over.get(key) is not None:
            merged[key] = over.get(key)
    merged["version"] = PREVIEW_PROFILE_CACHE_VERSION
    return merged


def _preview_profile_read_paths() -> List[str]:
    paths: List[str] = []
    for path in (PREVIEW_PROFILE_BUNDLED_FILE, PREVIEW_PROFILE_CACHE_FILE):
        if not path:
            continue
        absolute = os.path.abspath(path)
        if absolute not in paths:
            paths.append(absolute)
    return paths


def _load_preview_profile_doc() -> Dict[str, Any]:
    """Load the compact normal-preview snapshot without touching workbench data.

    Fresh one-file EXEs have only the bundled seed in ``_MEIPASS``.  Learned
    scans are written beside the EXE, so the two documents are merged here
    instead of treating the first launch as a cache miss.
    """
    global _PREVIEW_PROFILE_DOC
    with _PREVIEW_PROFILE_LOCK:
        if _PREVIEW_PROFILE_DOC is not None:
            return _PREVIEW_PROFILE_DOC
        if not PREVIEW_PROFILE_CACHE_ENABLED:
            _PREVIEW_PROFILE_DOC = _empty_preview_profile_doc()
            return _PREVIEW_PROFILE_DOC
        doc = _empty_preview_profile_doc()
        found = False
        for path in _preview_profile_read_paths():
            part = _read_preview_profile_doc_file(path)
            if part is None:
                continue
            doc = _merge_preview_profile_docs(doc, part)
            found = True
        if not found:
            _profile_warn_once("preview-cache", "[fd preview] compact cache unavailable; a new roster can be auto-profiled once.")
        _PREVIEW_PROFILE_DOC = _normalize_preview_profile_doc(doc)
        return _PREVIEW_PROFILE_DOC


_PREVIEW_MOVE_FIELDS = {
    "active2_end", "active2_start", "active_end", "active_start",
    "adv_block", "adv_hit", "animation_char_key", "animation_duration_seconds",
    "animation_motion", "animation_total_frames", "blockstun", "damage",
    "damage_flag", "ground_kb", "ground_kb_y", "hitstop", "hitstun", "id",
    "invuln", "invuln_confidence", "invuln_frames", "invuln_kind", "kb_type",
    "kind", "launch_profile", "meter", "move_name", "move_name_source",
    "multi_hit_count", "normal_confirmed", "recovery", "recovery_source",
    "runtime_profile_eligible", "stun_source",
}


def _compact_preview_moves(moves: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Strip a dynamic profile down to the small normal-preview contract."""
    compact: List[Dict[str, Any]] = []
    for move in list(moves or []):
        if not isinstance(move, dict):
            continue
        if str(move.get("kind") or "").lower() != "normal":
            continue
        row = {key: copy.deepcopy(move[key]) for key in _PREVIEW_MOVE_FIELDS if key in move}
        if row.get("id") is None:
            continue
        row.setdefault("kind", "normal")
        compact.append(row)
    return compact


def _write_preview_profile_doc_safely(doc: Dict[str, Any]) -> bool:
    """Atomically persist the compact snapshot beside the executable."""
    target = os.path.abspath(PREVIEW_PROFILE_CACHE_FILE)
    parent = os.path.dirname(target) or os.getcwd()
    try:
        os.makedirs(parent, exist_ok=True)
    except Exception:
        pass
    data = json.dumps(doc, indent=2, sort_keys=True) + "\n"
    tmp = f"{target}.{os.getpid()}.{threading.get_ident()}.tmp"
    for attempt in range(8):
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            with open(tmp, "r", encoding="utf-8") as f:
                verify = _normalize_preview_profile_doc(json.load(f))
            if not isinstance(verify.get("profiles"), dict):
                raise ValueError("preview export verification failed")
            os.replace(tmp, target)
            return True
        except PermissionError:
            time.sleep(0.04 * (attempt + 1))
        except OSError:
            time.sleep(0.03 * (attempt + 1))
        except Exception:
            break
    try:
        if os.path.exists(tmp):
            os.unlink(tmp)
    except Exception:
        pass
    return False


def _save_preview_profile_moves(
    char_id: Optional[int],
    char_name: str,
    chr_tbl_abs: int,
    tbl_move_addrs: List[int],
    moves: List[Dict[str, Any]],
) -> bool:
    """Append/replace one dynamically scanned fighter in the compact snapshot."""
    global _PREVIEW_PROFILE_DOC
    if not PREVIEW_PROFILE_CACHE_ENABLED:
        return False
    rows = _compact_preview_moves(moves)
    if not rows:
        return False
    key = _profile_key(char_id, char_name)
    entry = {
        "char_id": char_id,
        "char_name": char_name,
        "table_signature": _profile_table_signature(tbl_move_addrs, chr_tbl_abs),
        "moves": rows,
    }
    with _PREVIEW_PROFILE_LOCK:
        doc = _load_preview_profile_doc()
        out = _normalize_preview_profile_doc(copy.deepcopy(doc))
        out.setdefault("profiles", {})[key] = entry
        out["generated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        if not _write_preview_profile_doc_safely(out):
            return False
        _PREVIEW_PROFILE_DOC = out
        return True

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
                # Include the full static startup-signature window.  Existing
                # profiles often predate invuln metadata, so their only stable
                # anchor is the canonical action root in ``abs``.
                max_addr = max(max_addr, a + max(HITBOX_OFF_X, HITBOX_OFF_Y, INVULN_SCAN_RANGE) + 0x20)
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
    off = _profile_off(buf, base_abs, addr, 4)
    if off is None:
        return None
    try:
        return rd_u32_be(buf, off)
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

    addr = seg.get("ground_kb_packet_addr")
    off = _profile_off(buf, base_abs, addr, GROUND_KB_TOTAL_LEN) if addr else None
    if off is not None:
        gkb = parse_ground_knockback(buf, off)
        if gkb:
            seg["ground_kb_addr"] = int(addr) + GROUND_KB_VALUE_OFF
            seg["ground_kb_y_addr"] = int(addr) + GROUND_KB_AUX_OFF
            seg["ground_kb"] = gkb.get("ground_kb")
            seg["ground_kb_y"] = gkb.get("ground_kb_y")
            seg["ground_kb_mode"] = gkb.get("ground_kb_mode")
            seg["ground_kb_aux"] = gkb.get("ground_kb_aux")

    addr = seg.get("stun_addr")
    off = _profile_off(buf, base_abs, addr, 39) if addr else None
    if off is not None:
        stun = parse_stun(buf, off)
        if stun:
            seg["hitstun"], seg["blockstun"], seg["hitstop"] = stun



# ============================================================
# Cached workbench write-target verification
# ============================================================
# Generic packet field offsets. Keep this local to the scanner so the cache
# verifier does not import the writer module (which owns Dolphin write I/O).
_PROFILE_ACTIVE_START_OFFSET = 8
_PROFILE_ACTIVE_END_OFFSET = 16
_PROFILE_DAMAGE_VALUE_OFFSET = 5
_PROFILE_STUN_HITSTUN_OFFSET = 15
_PROFILE_STUN_BLOCKSTUN_OFFSET = 31
_PROFILE_STUN_HITSTOP_OFFSET = 38

# A profile cache can rebase a structurally valid packet into the wrong action
# owner.  Header-only checks are not enough: a neighbouring move may contain a
# perfectly valid 35/10 damage packet.  Rebind the small set of generic editor
# targets inside the row's own action window before exposing them as writable.
#
# This runs only in the editable workbench cache path, never in the compact HUD
# preview path.  It keeps the editor instant while preventing stale/borrowed
# packet addresses from silently writing a different move.

def _clear_profile_write_target(mv: Dict[str, Any], field: str) -> None:
    """Remove an unsafe generic write target while retaining display values."""
    if field == "damage":
        mv["damage_addr"] = None
        mv["damage_value_addr"] = None
        mv["damage_write_verified"] = False
    elif field == "active":
        mv["active_addr"] = None
        mv["active_start_addr"] = None
        mv["active_end_addr"] = None
        mv["active_write_verified"] = False
    elif field == "stun":
        mv["stun_addr"] = None
        mv["hitstun_addr"] = None
        mv["blockstun_addr"] = None
        mv["hitstop_addr"] = None
        mv["stun_write_verified"] = False


def _profile_owner_bounds(mv: Dict[str, Any], moves: List[Dict[str, Any]]) -> Tuple[Optional[int], Optional[int]]:
    """Return the exclusive static-script ownership window for one row."""
    try:
        root = int(mv.get("abs") or 0)
    except Exception:
        return (None, None)
    if not root:
        return (None, None)
    start, end = _invuln_owner_window(moves, root, mv_source=mv.get("source"))
    if start is None:
        start = root
    if end is None or int(end) <= int(start):
        end = _next_invuln_action_boundary(moves, root, mv_source=mv.get("source"))
    try:
        start_i = int(start)
        end_i = int(end)
    except Exception:
        return (None, None)
    if end_i <= start_i:
        end_i = start_i + HIT_SEGMENT_SCAN_MAX
    # Keep malformed/helper rows local rather than allowing them to reach into
    # a later action just because a cache record was broad.
    return (start_i, min(end_i, start_i + HIT_SEGMENT_SCAN_MAX))


def _profile_owned_block(
    blocks: List[Tuple[int, Any]],
    start_abs: int,
    end_abs: int,
    *,
    preferred_from: Optional[int] = None,
    max_gap: int = HIT_SEGMENT_MAX_GAP,
) -> Optional[Tuple[int, Any]]:
    """Pick the first forward packet in one verified action owner window."""
    if not blocks:
        return None
    first = int(preferred_from if preferred_from is not None else start_abs)
    # The action root can be a few bytes after an initial active packet. Keep
    # the owner preamble visible, but do not borrow arbitrary old blocks.
    first = max(int(start_abs), first)
    limit = min(int(end_abs), first + int(max_gap))
    for addr, data in blocks:
        try:
            a = int(addr)
        except Exception:
            continue
        if first <= a < limit:
            return (a, data)
    return None


def _profile_rebind_generic_write_targets(
    mv: Dict[str, Any],
    moves: List[Dict[str, Any]],
    blocks: Dict[str, Any],
) -> None:
    """Reconcile Damage/Active/Stun targets against the current action owner.

    Only gameplay action roots (table/explicit action roots) receive automatic
    generic targets. Raw helper rows remain visible in Raw Data, but no longer
    advertise a borrowed packet as editable. Hit-segment rows retain their own
    resolved addresses through their parent build path.
    """
    # Start pessimistic; every enabled generic writer below has a current live
    # packet header plus an owner-window proof.
    _clear_profile_write_target(mv, "damage")
    _clear_profile_write_target(mv, "active")
    _clear_profile_write_target(mv, "stun")

    source = str(mv.get("source") or "")
    if source not in {"table", "anim_hdr", "air_hdr", "cmd_hdr", "super_end"}:
        return
    start, end = _profile_owner_bounds(mv, moves)
    if start is None or end is None:
        return

    active_blocks = sorted(list(blocks.get("active_blocks") or []), key=lambda x: int(x[0]))
    damage_blocks = sorted(list(blocks.get("dmg_blocks") or []), key=lambda x: int(x[0]))
    stun_blocks = sorted(list(blocks.get("stun_blocks") or []), key=lambda x: int(x[0]))

    # Action roots can be placed after their first active declaration. Search
    # the exclusive owner preamble first, then pair later packets in script order.
    ablk = _profile_owned_block(active_blocks, start, end, preferred_from=start, max_gap=HIT_SEGMENT_MAX_GAP)
    if ablk is not None:
        active_addr, active_values = ablk
        mv["active_addr"] = int(active_addr)
        mv["active_start_addr"] = int(active_addr) + _PROFILE_ACTIVE_START_OFFSET
        mv["active_end_addr"] = int(active_addr) + _PROFILE_ACTIVE_END_OFFSET
        mv["active_start"], mv["active_end"] = active_values
        mv["active_write_verified"] = True
        damage_start = int(active_addr)
    else:
        damage_start = int(start)

    dblk = _profile_owned_block(damage_blocks, start, end, preferred_from=damage_start, max_gap=HIT_SEGMENT_MAX_GAP)
    if dblk is None and damage_start != start:
        dblk = _profile_owned_block(damage_blocks, start, end, preferred_from=start, max_gap=HIT_SEGMENT_MAX_GAP)
    if dblk is not None:
        damage_addr, damage_values = dblk
        mv["damage_addr"] = int(damage_addr)
        mv["damage_value_addr"] = int(damage_addr) + _PROFILE_DAMAGE_VALUE_OFFSET
        mv["damage"], mv["damage_flag"] = damage_values
        mv["damage_write_verified"] = True
        stun_start = int(damage_addr)
    else:
        stun_start = int(start)

    sblk = _profile_owned_block(stun_blocks, start, end, preferred_from=stun_start, max_gap=HIT_SEGMENT_MAX_GAP)
    if sblk is None and stun_start != start:
        sblk = _profile_owned_block(stun_blocks, start, end, preferred_from=start, max_gap=HIT_SEGMENT_MAX_GAP)
    if sblk is not None:
        stun_addr, stun_values = sblk
        mv["stun_addr"] = int(stun_addr)
        mv["hitstun_addr"] = int(stun_addr) + _PROFILE_STUN_HITSTUN_OFFSET
        mv["blockstun_addr"] = int(stun_addr) + _PROFILE_STUN_BLOCKSTUN_OFFSET
        mv["hitstop_addr"] = int(stun_addr) + _PROFILE_STUN_HITSTOP_OFFSET
        mv["hitstun"], mv["blockstun"], mv["hitstop"] = stun_values
        mv["stun_write_verified"] = True

    # Save a compact reason for the UI/status bar and for debugging profile
    # mismatches without exposing raw cache internals to the main HUD.
    mv["generic_write_owner_start"] = int(start)
    mv["generic_write_owner_end"] = int(end)
    mv["generic_write_verified"] = bool(
        mv.get("damage_write_verified") or mv.get("active_write_verified") or mv.get("stun_write_verified")
    )


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

    addr = mv.get("ground_kb_packet_addr")
    off = _profile_off(buf, base_abs, addr, GROUND_KB_TOTAL_LEN) if addr else None
    if off is not None:
        gkb = parse_ground_knockback(buf, off)
        if gkb:
            mv["ground_kb_addr"] = int(addr) + GROUND_KB_VALUE_OFF
            mv["ground_kb_y_addr"] = int(addr) + GROUND_KB_AUX_OFF
            mv["ground_kb"] = gkb.get("ground_kb")
            mv["ground_kb_y"] = gkb.get("ground_kb_y")
            mv["ground_kb_mode"] = gkb.get("ground_kb_mode")
            mv["ground_kb_aux"] = gkb.get("ground_kb_aux")

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

    # Recompute the signature directly from the live static script window.
    # Do not depend on cached probes: older profile rows never stored them.
    try:
        mv_abs = int(mv.get("abs") or 0)
    except Exception:
        mv_abs = 0
    if mv_abs:
        try:
            probes = collect_invuln_probes(
                buf, base_abs, mv_abs,
                owner_start_abs=mv.get("_invuln_owner_start_abs"),
                owner_end_abs=mv.get("_invuln_owner_end_abs"),
            )
        except Exception:
            probes = []
        mv["invuln_probes"] = probes
        mv["invuln_probe_count"] = len(probes)
        mv["invuln_startup_active_limit"] = apply_invuln_startup_active_gate(mv, probes, buf=buf, base_abs=base_abs, char_id=char_id)
        mv["invuln"] = summarize_invuln_probes(probes)
        best_invuln = best_candidate_invuln_probe(probes)
        mv["invuln_frames"] = int((best_invuln or {}).get("candidate_frames") or 0)
        try:
            mv["invuln_addr"] = int((best_invuln or {}).get("addr") or 0) or None
        except Exception:
            mv["invuln_addr"] = None
        mv["invuln_confidence"] = str((best_invuln or {}).get("invuln_confidence") or "none")
        mv["invuln_kind"] = str((best_invuln or {}).get("invuln_kind") or "")
    else:
        mv["invuln_probes"] = []
        mv["invuln_probe_count"] = 0
        mv["invuln"] = ""
        mv["invuln_frames"] = 0
        mv["invuln_addr"] = None
        mv["invuln_startup_active_limit"] = None
        mv["invuln_confidence"] = "none"
        mv["invuln_kind"] = ""

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


def _load_preview_profile_moves(
    char_id: Optional[int],
    char_name: str,
    chr_tbl_abs: int,
    tbl_move_addrs: List[int],
) -> Optional[List[Dict[str, Any]]]:
    """Return saved normal-only rows without touching the full workbench cache.

    Preview rows are static training data. The live table signature still has to
    match so an unrelated/transitioning character can never inherit old rows.
    """
    if not PREVIEW_PROFILE_CACHE_ENABLED:
        return None
    key = _profile_key(char_id, char_name)
    sig = _profile_table_signature(tbl_move_addrs, chr_tbl_abs)
    doc = _load_preview_profile_doc()
    prof = (doc.get("profiles") or {}).get(key)
    if not isinstance(prof, dict):
        return None
    if str(prof.get("table_signature") or "") != sig:
        return None
    rows = prof.get("moves")
    if not isinstance(rows, list) or not rows:
        return None
    return [copy.deepcopy(row) for row in rows if isinstance(row, dict)]


def _load_profile_moves(
    char_id: Optional[int],
    char_name: str,
    chr_tbl_abs: int,
    tbl_move_addrs: List[int],
    *,
    tbl_move_entries: Optional[List[Tuple[int, int]]] = None,
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
    # but the module is intentionally returning Morrigan to the old blank behavior.
    if "morrigan" in key.lower() and "morrigan-stun" in str(prof.get("scanner_build") or "").lower():
        return None
    if int(prof.get("version") or 0) != PROFILE_CACHE_VERSION:
        return None
    if int(prof.get("stun_resolver_revision") or 0) != STUN_RESOLVER_REVISION:
        return None
    if int(prof.get("active_resolver_revision") or 0) != ACTIVE_RESOLVER_REVISION:
        return None
    if str(prof.get("table_signature") or "") != sig:
        return None
    rows = prof.get("moves")
    if not isinstance(rows, list) or not rows:
        return None

    # Existing profile files may have stored a table root with ``id=None``
    # because older code guessed from a nested animation command. Repair those
    # rows from the live chr_tbl before refreshing their phase packets.
    live_table_ids: Dict[int, int] = {
        int(addr): int(action_id)
        for action_id, addr in (tbl_move_entries or [])
    }

    moves: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rebased = _rebase_profile_obj(copy.deepcopy(row), chr_tbl_abs)
        if isinstance(rebased, dict):
            try:
                root_abs = int(rebased.get("abs") or 0)
            except Exception:
                root_abs = 0
            if str(rebased.get("source") or "") == "table" and root_abs in live_table_ids:
                action_id = live_table_ids[root_abs]
                rebased["id"] = action_id
                rebased["kind"] = "normal" if ((action_id & 0xFF) in NORMAL_IDS) else "special"
            rebased["_profile_fast_path"] = True
            moves.append(rebased)
    if not moves:
        return None

    try:
        buf, base_abs = _read_profile_window(chr_tbl_abs, moves)
        if not buf:
            return None
        for mv in moves:
            try:
                _owner_start, _owner_end = _invuln_owner_window(
                    moves, int(mv.get("abs") or 0),
                    mv_source=mv.get("source"),
                )
                mv["_invuln_owner_start_abs"] = _owner_start
                mv["_invuln_owner_end_abs"] = _owner_end
            except Exception:
                mv["_invuln_owner_end_abs"] = None
            _profile_refresh_move(mv, buf, base_abs, char_id)
            # This is a transient scan aid, not profile data.
            mv.pop("_invuln_owner_start_abs", None)
            mv.pop("_invuln_owner_end_abs", None)
        # Cache rows can have valid-looking packets assigned to a neighboring
        # action. Rebuild only the generic editable targets inside each row's
        # current live owner window before handing the workbench its snapshot.
        profile_blocks = collect_blocks(buf, base_abs)
        for mv in moves:
            _profile_rebind_generic_write_targets(mv, moves, profile_blocks)
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
        # that is already profiled for this exact character table.
        keep_extras = str(previous.get("table_signature") or "") == sig
        profile = {
            "version": PROFILE_CACHE_VERSION,
            "scanner_build": PROFILE_SCANNER_BUILD,
            "stun_resolver_revision": STUN_RESOLVER_REVISION,
            "active_resolver_revision": ACTIVE_RESOLVER_REVISION,
            "char_id": char_id,
            "char_name": char_name,
            "key": key,
            "table_signature": sig,
            "table_move_count": len(tbl_move_addrs or []),
            "created_from": "dynamic_scan_once",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
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
    'Return cached projectile/special scan rows rebased to the live table.\n\n    These rows are deliberately *not* rescanned here.  The one-time discovery\n    pass is operator-triggered from the Frame Data workbench; subsequent openings\n    only deserialize and rebase the proven records for the loaded character.\n    '
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


# ============================================================
# Runtime resolved-stun overlay
# ============================================================

def _apply_runtime_stun_overlay(moves: List[Dict[str, Any]], char_id: Optional[int]) -> None:
    """Overlay only evidence captured from the live victim resolver.

    This is deliberately post-scan and non-persistent with respect to the
    static frame-data cache.  A failed/empty runtime cache therefore cannot
    alter the normal static parser, and a good runtime observation never gains
    a fake static packet address.
    """
    if apply_runtime_stun_observations is None or not moves or char_id is None:
        return
    try:
        apply_runtime_stun_observations(moves, char_id)
    except Exception as e:
        _profile_warn_once("runtime-stun-overlay", f"[fd runtime stun] overlay skipped: {e!r}")


# ============================================================
# MAIN SCAN
# ============================================================

def scan_once(
    force_dynamic: bool = False,
    cache_only: bool = False,
    preview_only: bool = False,
    dynamic_char_ids: Optional[Sequence[int]] = None,
):
    hook()

    slots_info = read_slots_from_constants()
    # A roster bootstrap may ask for only the fighter IDs that missed the
    # compact preview cache.  Manual full scans pass no targets and retain the
    # existing all-slots behavior.
    dynamic_targets = {int(v) for v in (dynamic_char_ids or ()) if str(v).strip()}

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

        tbl_move_entries = parse_chr_tbl_entries(tbl_buf, tbl_start, chr_tbl_abs)
        tbl_move_addrs = [addr for _, addr in tbl_move_entries]

        table_signature = _profile_table_signature(tbl_move_addrs, chr_tbl_abs)

        # A targeted full bootstrap must not dynamically rescan teammates that
        # already have compact previews.  They remain cache-only for this pass.
        if force_dynamic and dynamic_targets and int(cid or 0) not in dynamic_targets:
            preview_moves = _load_preview_profile_moves(cid, cname, chr_tbl_abs, tbl_move_addrs)
            if preview_moves is not None:
                apply_animation_metadata(preview_moves, cname, cid)
                _apply_runtime_stun_overlay(preview_moves, cid)
                result[slot_idx] = {
                    "slot_label": slot_label,
                    "char_name": cname,
                    "char_id": cid,
                    "fighter_base_abs": fighter_base_abs,
                    "moves": sorted(preview_moves, key=sort_key),
                    "chr_tbl_abs": chr_tbl_abs,
                    "tbl_move_count": len(tbl_move_addrs),
                    "profile_table_signature": table_signature,
                    "profile_fast_path": True,
                    "profile_preview_fast_path": True,
                    "profile_key": _profile_key(cid, cname),
                }
            else:
                result[slot_idx] = {
                    "slot_label": slot_label,
                    "char_name": cname,
                    "char_id": cid,
                    "fighter_base_abs": fighter_base_abs,
                    "moves": [],
                    "chr_tbl_abs": chr_tbl_abs,
                    "tbl_move_count": len(tbl_move_addrs),
                    "profile_table_signature": table_signature,
                    "profile_fast_path": False,
                    "profile_preview_fast_path": True,
                    "profile_cache_miss": True,
                    "profile_key": _profile_key(cid, cname),
                }
            continue

        # The always-on HUD/overlay path is strictly read-only and compact. Do
        # not load the large workbench cache or scan dynamic action scripts just
        # because a fighter pointer changed.
        if preview_only and not force_dynamic:
            preview_moves = _load_preview_profile_moves(cid, cname, chr_tbl_abs, tbl_move_addrs)
            if preview_moves is not None:
                apply_animation_metadata(preview_moves, cname, cid)
                _apply_runtime_stun_overlay(preview_moves, cid)
                result[slot_idx] = {
                    "slot_label": slot_label,
                    "char_name": cname,
                    "char_id": cid,
                    "fighter_base_abs": fighter_base_abs,
                    "moves": sorted(preview_moves, key=sort_key),
                    "chr_tbl_abs": chr_tbl_abs,
                    "tbl_move_count": len(tbl_move_addrs),
                    "profile_table_signature": table_signature,
                    "profile_fast_path": True,
                    "profile_preview_fast_path": True,
                    "profile_key": _profile_key(cid, cname),
                }
            else:
                result[slot_idx] = {
                    "slot_label": slot_label,
                    "char_name": cname,
                    "char_id": cid,
                    "fighter_base_abs": fighter_base_abs,
                    "moves": [],
                    "chr_tbl_abs": chr_tbl_abs,
                    "tbl_move_count": len(tbl_move_addrs),
                    "profile_table_signature": table_signature,
                    "profile_fast_path": False,
                    "profile_preview_fast_path": True,
                    "profile_cache_miss": True,
                    "profile_key": _profile_key(cid, cname),
                }
            continue

        if _profile_cache_allowed(force_dynamic):
            profiled_moves = _load_profile_moves(
                cid, cname, chr_tbl_abs, tbl_move_addrs,
                tbl_move_entries=tbl_move_entries,
            )
            if profiled_moves is not None:
                # MOT total frames are static FPK data, so layer them onto both
                # cached and fresh SEQ rows after the live active window has
                # been refreshed. This keeps Recovery deterministic per action.
                apply_animation_metadata(profiled_moves, cname, cid)
                _apply_runtime_stun_overlay(profiled_moves, cid)
                extras = load_profile_extras(
                    cid, cname, chr_tbl_abs, tbl_move_addrs,
                    table_signature=table_signature,
                )
                result[slot_idx] = {
                    "slot_label": slot_label,
                    "char_name": cname,
                    "char_id": cid,
                    "fighter_base_abs": fighter_base_abs,
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
                    "fighter_base_abs": fighter_base_abs,
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

        in_entries = [
            (action_id, addr) for action_id, addr in tbl_move_entries
            if region_start <= addr < region_end
        ]
        moves = collect_move_anchors(
            region_buf, region_start,
            tbl_move_addrs=in_slice,
            tbl_move_entries=in_entries,
        )
        blocks = collect_blocks(region_buf, region_start)
        attach_move_fields(moves, region_buf, region_start, blocks, char_id=cid)
        # Join the SEQ action ID to chr/<character>/0000.mot.
        # Recovery = MOT total animation frames - final active frame.
        apply_animation_metadata(moves, cname, cid)
        moves = collapse_duplicate_normals_by_quality(moves)
        sorted_moves = sorted(moves, key=sort_key)
        _save_profile_moves(cid, cname, chr_tbl_abs, tbl_move_addrs, sorted_moves)
        if _save_preview_profile_moves(cid, cname, chr_tbl_abs, tbl_move_addrs, sorted_moves):
            _profile_warn_once(
                f"preview-saved:{_profile_key(cid, cname)}",
                f"[fd preview] saved compact preview for {cname} ({_profile_key(cid, cname)})",
            )
        else:
            _profile_warn_once(
                f"preview-save-deferred:{_profile_key(cid, cname)}",
                f"[fd preview] save deferred for {cname}; current run still has the dynamic result",
            )
        _apply_runtime_stun_overlay(sorted_moves, cid)

        extras = load_profile_extras(
            cid, cname, chr_tbl_abs, tbl_move_addrs,
            table_signature=table_signature,
        )
        result[slot_idx] = {
            "slot_label": slot_label,
            "char_name": cname,
            "char_id": cid,
                    "fighter_base_abs": fighter_base_abs,
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