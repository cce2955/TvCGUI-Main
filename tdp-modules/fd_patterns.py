# fd_patterns.py
#
# Pattern scanners / address discovery for move blocks.

from __future__ import annotations
from typing import Callable, Optional, Tuple

# ---- Combo-only KB/Vacuum modifier pattern ----
COMBO_KB_SIG_A = bytes([0x01, 0xAC, 0x3D, 0x00, 0x00, 0x00])  # then XX at +6
COMBO_KB_SIG_B = bytes([0x01, 0xAC, 0x3F, 0x00, 0x00, 0x00])  # then XX at +6
COMBO_KB_SCAN_MAX = 0x200  # scan first 0x200 bytes of the move block

# ---- SuperBG pattern ----
SUPERBG_ON = 0x04
SUPERBG_OFF = 0x00
SUPERBG_MARKER = 0x60
SUPERBG_LOOKAHEAD = 0x80

# ---- Move "record header" anchors (anim / phase) ----
#
# Legacy header used by the editor historically:
#   01 ?? 01 3C
#
# Newly observed, more structured per-move record header (common in many blocks):
#   04 01 02 3F  [phase_u32]  [anim_u16]  ...
# Example:
#   04 01 02 3F  00 00 02 02  01 3C
#                 ^ phase      ^ anim id

LEGACY_ANIM_HDR_LEN = 4
LEGACY_ANIM_HDR_B0 = 0x01
LEGACY_ANIM_HDR_B2 = 0x01
LEGACY_ANIM_HDR_B3 = 0x3C

PHASE_REC_HDR = bytes([0x04, 0x01, 0x02, 0x3F])
PHASE_REC_HDR_LEN = 4
PHASE_REC_PHASE_LEN = 4
PHASE_REC_ANIM_LEN = 2
PHASE_REC_TOTAL_LEN = PHASE_REC_HDR_LEN + PHASE_REC_PHASE_LEN + PHASE_REC_ANIM_LEN
ASSIST_TABLE_SIG = bytes([
    0x34,0x32,0x3F,0x00,0x00,0x00,0x00,0x02,
    0x20,0x00,0x00,0x00,0x3E,0xD7,0x0A,0x3D,
    0x00,0x00,0x00,0x00,0x34,0x32,0x3F,0x00,
    0x00,0x00,0x00,0x03,0x20,0x00,0x00,0x00,
])
def find_assist_table_candidates(
    base: int,
    rbytes_func,
    *,
    scan_len: int = 0x200000  # scan a couple MB of MEM2
) -> list[int]:
    """
    Scan for assist-table candidates using a strong multi-record signature.

    Returns:
        list of absolute addresses where the signature starts
    """
    if not base:
        return []

    try:
        buf = rbytes_func(base, scan_len)
    except Exception:
        return []

    if not buf:
        return []

    hits = []
    sig = ASSIST_TABLE_SIG
    pos = 0

    while True:
        i = buf.find(sig, pos)
        if i < 0:
            break

        hits.append(base + i)
        pos = i + 1

    return hits

def validate_assist_table(addr: int, rbytes_func) -> bool:
    """
    Validate a candidate region by checking for nearby structural markers.
    """

    try:
        buf = rbytes_func(addr, 0x400)
    except Exception:
        return False

    if not buf:
        return False

    checks = 0

    # look for known structural motifs nearby
    if b"\x04\x17\x60\x00" in buf:
        checks += 1

    if b"\x04\x01\x60\x00" in buf:
        checks += 1

    if b"\x41\x20\x2D\x13" in buf:
        checks += 1

    if b"\x11\x16\x20\x00" in buf:
        checks += 1

    if b"\x33\x03\x20\x3F" in buf:
        checks += 1

    return checks >= 2

def find_assist_tables(base: int, rbytes_func) -> list[int]:
    raw = find_assist_table_candidates(base, rbytes_func)

    valid = []
    for addr in raw:
        if validate_assist_table(addr, rbytes_func):
            valid.append(addr)

    return valid
ATTACK_PROPERTY_VALUES = {
    0x04: "Unblockable",
    0x09: "Mid, Light Hit",
    0x0A: "Mid, Medium Hit",
    0x0C: "Mid, Heavy Hit",
    0x11: "High, Light Hit",
    0x12: "High, Medium Hit",
    0x14: "High, Heavy Hit",
    0x21: "Low, Light Hit",
    0x22: "Low, Medium Hit",
    0x24: "Low, Heavy Hit",
}


def fmt_attack_property(value: int | None) -> str:
    """Human-readable Attack Property cell text."""
    if value is None:
        return ""
    try:
        v = int(value) & 0xFF
    except Exception:
        return str(value)
    label = ATTACK_PROPERTY_VALUES.get(v, "Unknown")
    return f"0x{v:02X} {label}"


def parse_attack_property(text: str) -> int | None:
    """Parse a property byte from hex/decimal/list cell text."""
    s = str(text or "").strip()
    if not s:
        return None
    token = s.split()[0].strip().rstrip(",")
    try:
        if token.lower().startswith("0x"):
            return int(token, 16) & 0xFF
        # Most users will type the guide values as hex, e.g. 21/22/24.
        # Treat two hex-looking chars containing A-F as hex, otherwise decimal.
        if any(ch in token.lower() for ch in "abcdef"):
            return int(token, 16) & 0xFF
        return int(token, 10) & 0xFF
    except Exception:
        try:
            return int(token, 16) & 0xFF
        except Exception:
            return None


def _score_speed_candidate(buf: bytes, i: int, value_off: int, move_abs: int) -> int:
    score = 0
    # Your known 5A case is move_abs + 0x57; prefer this when it validates.
    if value_off == 0x57:
        score += 100
    # Values around normal speed are more likely than random script values.
    try:
        v = buf[value_off]
        if v == 0x40:
            score += 40
        elif 0x20 <= v <= 0x80:
            score += 20
    except Exception:
        pass
    # Full packet tail is stronger than 04 17 alone.
    if value_off + 4 <= len(buf) and buf[value_off + 1:value_off + 4] == b"\x04\x17\x60":
        score += 45
    elif value_off + 2 <= len(buf) and buf[value_off + 1:value_off + 3] == b"\x04\x17":
        score += 20
    # Prefer earlier move-definition packets over later script repeats.
    if i < 0x120:
        score += 20
    elif i < 0x240:
        score += 10
    return score


def find_speed_mod_addr(
    move_abs: int,
    rbytes: Callable[[int, int], bytes],
    *,
    scan_len: int = 0x800,
) -> Tuple[Optional[int], Optional[int], Optional[bytes]]:
    """
    Speed modifier locator.

    Strong packet shape:
        20 3F 00 00 00 XX 04 17 60 00
                         ^^ speed byte

    Older fallback shape:
        20 3F 00 00 00 XX 04 17

    This ranks candidates instead of returning the first lookalike packet,
    because move scripts can contain several similar 20 3F packets.
    """
    if not move_abs:
        return (None, None, None)

    try:
        buf = rbytes(move_abs, scan_len)
    except Exception:
        return (None, None, None)

    if not buf:
        return (None, None, None)

    anchor = b"\x20\x3F\x00\x00\x00"
    candidates: list[tuple[int, int, int, bytes]] = []

    start = 0
    while True:
        i = buf.find(anchor, start)
        if i < 0:
            break
        value_off = i + len(anchor)
        if value_off < len(buf):
            has_strong_tail = value_off + 4 <= len(buf) and buf[value_off + 1:value_off + 4] == b"\x04\x17\x60"
            has_loose_tail = value_off + 2 <= len(buf) and buf[value_off + 1:value_off + 3] == b"\x04\x17"
            if has_strong_tail or has_loose_tail:
                score = _score_speed_candidate(buf, i, value_off, move_abs)
                ctx = buf[max(0, i - 4):min(len(buf), value_off + 12)]
                candidates.append((score, move_abs + value_off, buf[value_off], ctx))
        start = i + 1

    if not candidates:
        return (None, None, None)

    candidates.sort(key=lambda row: (-row[0], row[1]))
    _score, addr, cur, ctx = candidates[0]
    return (addr, cur, ctx)


def find_attack_property_addr(
    move_abs: int,
    rbytes: Callable[[int, int], bytes],
    *,
    scan_len: int = 0x800,
) -> Tuple[Optional[int], Optional[int], Optional[bytes]]:
    """
    Attack Property locator.

    Guide pattern:
        04 01 60 00 00 00 02 40 3F 00 00 00 00 00 00 XX 04 01 60
                                               ^^ attack property byte

    XX values observed/known:
        04 unblockable
        09/0A/0C mid light/medium/heavy
        11/12/14 high light/medium/heavy
        21/22/24 low light/medium/heavy

    Returns:
      (absolute_addr_of_xx, current_value_byte, context_bytes)
    """
    if not move_abs:
        return (None, None, None)

    try:
        buf = rbytes(move_abs, scan_len)
    except Exception:
        return (None, None, None)

    if not buf:
        return (None, None, None)

    prefix = b"\x04\x01\x60\x00\x00\x00\x02\x40\x3F\x00\x00\x00\x00\x00\x00"
    tail = b"\x04\x01\x60"
    valid = set(ATTACK_PROPERTY_VALUES.keys())
    candidates: list[tuple[int, int, int, bytes]] = []

    start = 0
    while True:
        i = buf.find(prefix, start)
        if i < 0:
            break
        value_off = i + len(prefix)
        tail_off = value_off + 1
        if tail_off + len(tail) <= len(buf) and buf[tail_off:tail_off + len(tail)] == tail:
            v = buf[value_off]
            score = 100
            if v in valid:
                score += 80
            # Prefer early definition area over later script repeats.
            if i < 0x180:
                score += 25
            elif i < 0x300:
                score += 10
            ctx = buf[max(0, i - 4):min(len(buf), tail_off + len(tail) + 8)]
            candidates.append((score, move_abs + value_off, v, ctx))
        start = i + 1

    # Controlled fallback: same prefix semantics, but allow 04 ?? 60 as tail.
    # Useful if a character uses a different trailing subcommand index.
    if not candidates:
        start = 0
        while True:
            i = buf.find(prefix, start)
            if i < 0:
                break
            value_off = i + len(prefix)
            if value_off + 4 <= len(buf) and buf[value_off + 1] == 0x04 and buf[value_off + 3] == 0x60:
                v = buf[value_off]
                score = 50 + (80 if v in valid else 0)
                ctx = buf[max(0, i - 4):min(len(buf), value_off + 12)]
                candidates.append((score, move_abs + value_off, v, ctx))
            start = i + 1

    if not candidates:
        return (None, None, None)

    candidates.sort(key=lambda row: (-row[0], row[1]))
    _score, addr, cur, ctx = candidates[0]
    return (addr, cur, ctx)


def _find_legacy_anim_hdr_offset(buf: bytes) -> int | None:
    """Find first occurrence of legacy animation header: 01 ?? 01 3C."""
    if not buf or len(buf) < LEGACY_ANIM_HDR_LEN:
        return None
    for i in range(0, len(buf) - LEGACY_ANIM_HDR_LEN):
        if (
            buf[i] == LEGACY_ANIM_HDR_B0
            and buf[i + 2] == LEGACY_ANIM_HDR_B2
            and buf[i + 3] == LEGACY_ANIM_HDR_B3
        ):
            return i
    return None


def _find_phase_record_offset(buf: bytes) -> int | None:
    """
    Find first occurrence of the structured "phase record" header:
        04 01 02 3F [u32 phase] [u16 anim]

    Returns the offset of the record header (the 0x04 byte) or None.
    """
    if not buf or len(buf) < PHASE_REC_TOTAL_LEN:
        return None

    start = 0
    while True:
        i = buf.find(PHASE_REC_HDR, start)
        if i < 0:
            return None
        # Ensure we can read phase+anim.
        if i + PHASE_REC_TOTAL_LEN <= len(buf):
            return i
        start = i + 1

# ---- Projectile template radius pattern ----
#
# Observed across Ryu, Ken, Chun:
#   00 04 01 02  00 01 00  [XX XX]  ...  [radius float @ +0x2C from damage]
#
# Anchor: 00 04 01 02 00 01 00  (7 bytes)
# Damage u16 (big-endian) at anchor + 7
# Radius float (big-endian f32) at damage_addr + 0x2C

PROJ_TPL_SCAN = 0x2000
PROJ_ANCHOR = b"\x00\x04\x01\x02\x00\x01\x00"
PROJ_DMG_OFFSET = 7       # damage u16 relative to anchor start
PROJ_RADIUS_FROM_DMG = 0x2C  # radius f32 relative to damage addr


def find_projectile_radius_addr(
    base: int,
    rbytes: Callable[[int, int], bytes],
    *,
    scan_len: int = PROJ_TPL_SCAN,
) -> tuple[int | None, float | None]:
    """
    Scan from base for the projectile template radius.

    Anchor: 00 04 01 02 00 01 00
    Damage u16 at anchor+7, radius f32 at damage_addr+0x2C.

    Returns (absolute_addr_of_radius_float, radius_value) or (None, None).
    If multiple slices match, returns the first hit.
    """
    import struct
    import math

    if not base:
        return None, None

    try:
        buf = rbytes(base, scan_len)
    except Exception:
        return None, None

    if not buf or len(buf) < len(PROJ_ANCHOR) + PROJ_RADIUS_FROM_DMG + 4:
        return None, None

    pos = 0
    while True:
        i = buf.find(PROJ_ANCHOR, pos)
        if i < 0:
            break
        pos = i + 1

        dmg_off = i + PROJ_DMG_OFFSET
        radius_off = dmg_off + PROJ_RADIUS_FROM_DMG

        if radius_off + 4 > len(buf):
            continue

        try:
            r = struct.unpack(">f", buf[radius_off:radius_off + 4])[0]
        except Exception:
            continue

        if not math.isfinite(r) or r <= 0:
            continue

        return base + radius_off, r

    return None, None
def find_move_anim_anchor(buf: bytes) -> tuple[int | None, int, str]:
    """
    Return a best-effort anchor inside a move block that we can treat as the
    "start" of the move's definitional record.

    Preference order:
      1) Structured phase-record header: 04 01 02 3F ...
      2) Legacy anim header:            01 ?? 01 3C

    Returns:
      (anchor_offset, anchor_length, kind)
        - kind is one of: "phase_record", "legacy_anim", "none"
    """
    off = _find_phase_record_offset(buf)
    if off is not None:
        return off, PHASE_REC_TOTAL_LEN, "phase_record"

    off = _find_legacy_anim_hdr_offset(buf)
    if off is not None:
        return off, LEGACY_ANIM_HDR_LEN, "legacy_anim"

    return None, 0, "none"


def find_anim_u16_addr(
    move_abs: int,
    rbytes: Callable[[int, int], bytes],
    *,
    lookahead: int = 0x80,
) -> tuple[int | None, int | None, str]:
    """
    Resolve the absolute address of the move's animation ID (u16 big-endian).

    If a phase-record header is present, the anim ID lives at:
        move_abs + off + 8
    (after: 04 01 02 3F + u32 phase)

    If only the legacy header is present, the anim ID lives at:
        move_abs + off + 1
    (the ?? in 01 ?? ?? 3C is the u16 anim)

    Returns:
      (absolute_addr, current_anim_id, kind)
    """
    if not move_abs:
        return None, None, "none"

    try:
        buf = rbytes(move_abs, lookahead)
    except Exception:
        return None, None, "none"

    if not buf:
        return None, None, "none"

    off, _, kind = find_move_anim_anchor(buf)
    if off is None:
        return None, None, "none"

    if kind == "phase_record":
        addr = move_abs + off + (PHASE_REC_HDR_LEN + PHASE_REC_PHASE_LEN)
    else:
        addr = move_abs + off + 1

    try:
        # Read current anim id from the captured buffer when possible.
        if kind == "phase_record":
            rel = off + (PHASE_REC_HDR_LEN + PHASE_REC_PHASE_LEN)
        else:
            rel = off + 1
        cur = (buf[rel] << 8) | buf[rel + 1]
    except Exception:
        cur = None

    return addr, cur, kind


def find_combo_kb_mod_addr(move_abs: int, rbytes_func) -> tuple[int | None, int | None, int | None]:
    """
    Scan a move block for:
        01 AC 3D 00 00 00 XX ...
    or:
        01 AC 3F 00 00 00 XX ...

    Returns:
        (addr_of_xx, current_value, matched_sig_byte) or (None, None, None)
    """
    if not move_abs:
        return None, None, None

    try:
        buf = rbytes_func(move_abs, COMBO_KB_SCAN_MAX)
    except Exception:
        return None, None, None

    if not buf or len(buf) < 8:
        return None, None, None

    def _match_at(i: int, sig: bytes) -> bool:
        if i + 7 >= len(buf):
            return False
        return buf[i:i + 6] == sig

    for i in range(0, len(buf) - 7):
        if _match_at(i, COMBO_KB_SIG_A):
            xx = buf[i + 6]
            return move_abs + i + 6, xx, 0x3D
        if _match_at(i, COMBO_KB_SIG_B):
            xx = buf[i + 6]
            return move_abs + i + 6, xx, 0x3F

    return None, None, None


def find_superbg_addr(move_abs: int, rbytes_func, rd8_func) -> tuple[int | None, int | None]:
    """
    Find per-move SuperBG toggle.

    Pattern (based on user-verified bytes):
      ... (anchor) ... then later: 04 XX 60
      - 0x04 is a fixed marker
      - XX is the toggled value (commonly 0x01 or 0x04)
      - 0x60 is a trailing marker

    We return the address of XX (the middle byte).

    Anchor selection:
      prefers 04 01 02 3F record; falls back to 01 ?? 01 3C.
    """
    if not move_abs:
        return None, None

    try:
        buf = rbytes_func(move_abs, SUPERBG_LOOKAHEAD)
    except Exception:
        return None, None

    if not buf or len(buf) < 16:
        return None, None

    anchor_off, anchor_len, _kind = find_move_anim_anchor(buf)
    if anchor_off is None:
        return None, None

    # scan forward after the chosen anchor for the triplet 04 ?? 60
    start = anchor_off + max(anchor_len, 4)
    end = min(len(buf) - 2, anchor_off + SUPERBG_LOOKAHEAD)

    for i in range(start, end):
        if buf[i] == 0x04 and buf[i + 2] == SUPERBG_MARKER:
            addr = move_abs + i + 1  # middle byte (XX)
            try:
                cur = rd8_func(addr)
            except Exception:
                cur = None
            return addr, cur

    return None, None