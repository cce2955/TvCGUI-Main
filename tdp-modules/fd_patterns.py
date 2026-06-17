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
    if (
        value_off + 4 <= len(buf)
        and buf[value_off + 1:value_off + 3] == b"\x04\x17"
        and buf[value_off + 3] in (0x60, 0x67)
    ):
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
        20 3F 00 00 00 XX 04 17 60/67 00
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
            has_strong_tail = (
                value_off + 4 <= len(buf)
                and buf[value_off + 1:value_off + 3] == b"\x04\x17"
                and buf[value_off + 3] in (0x60, 0x67)
            )
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

# ---- User-verified hit FX / limb stretch / post-animation link patterns ----
# These are Dolphin-memory command stream patterns, not DOL/static addresses.
# Ryu 5A examples:
#   35/05 packet at 0x908AF0AC; second payload word at +0x08 controls hit spark/anchor.
#   34/40 packet at 0x908AF0C8 controls limb stretch/reach scaling.
#   01/33 packet at 0x908AF018 links to the post-animation continuation script.

import struct as _struct
import math as _math

HIT_SPARK_SCAN_MAX = 0x800
HIT_SPARK_SIG = b"\x35\x05\x00\x20"
HIT_SPARK_VALUE_OFFSET = 0x08  # second payload word; low byte was user-verified at +0x0B

LIMB_STRETCH_SCAN_MAX = 0x900
LIMB_STRETCH_SIG = b"\x34\x40\x00\x20"

POST_ANIM_LINK_SCAN_MAX = 0x900
POST_ANIM_LINK_SIG = b"\x01\x33\x00\x00"


def _u32be(buf: bytes, off: int) -> int | None:
    try:
        if off < 0 or off + 4 > len(buf):
            return None
        return int.from_bytes(buf[off:off + 4], "big", signed=False)
    except Exception:
        return None


def _f32be(buf: bytes, off: int) -> float | None:
    try:
        if off < 0 or off + 4 > len(buf):
            return None
        f = _struct.unpack(">f", buf[off:off + 4])[0]
        if not _math.isfinite(f):
            return None
        return float(f)
    except Exception:
        return None


def find_hit_spark_addr(
    move_abs: int,
    rbytes: Callable[[int, int], bytes],
    *,
    scan_len: int = HIT_SPARK_SCAN_MAX,
) -> tuple[int | None, int | None, int | None, bytes | None]:
    """
    Find the user-verified 35/05 hit-spark/effect packet.

    Packet shape:
        35 05 00 20  [word0] [word1] [word2]

    User poke result:
        low byte of word1 changed hit spark and sometimes spark location.

    Returns:
        (packet_addr, value_addr_for_word1, word1_value, context_bytes)
    """
    if not move_abs:
        return None, None, None, None
    try:
        buf = rbytes(move_abs, scan_len)
    except Exception:
        return None, None, None, None
    if not buf or len(buf) < 0x14:
        return None, None, None, None

    hits: list[int] = []
    pos = 0
    while True:
        i = buf.find(HIT_SPARK_SIG, pos)
        if i < 0:
            break
        if i + 0x10 <= len(buf):
            hits.append(i)
        pos = i + 1

    if not hits:
        return None, None, None, None

    # Prefer the first 35/05 in the move definition.
    # Ryu 5A: first 35/05 at 0x908AF0AC; the low byte at +0x0B was user-verified as hit spark/location.
    i = hits[0]
    val_off = i + HIT_SPARK_VALUE_OFFSET
    val = _u32be(buf, val_off)
    ctx = buf[max(0, i - 8):min(len(buf), i + 0x18)]
    return move_abs + i, move_abs + val_off, val, ctx


def find_limb_stretch_packet(
    move_abs: int,
    rbytes: Callable[[int, int], bytes],
    *,
    scan_len: int = LIMB_STRETCH_SCAN_MAX,
) -> dict | None:
    """
    Find the user-verified 34/40 limb stretch / reach-scaling packet.

    Packet shape:
        34 40 00 20 [part] [scale1] [scale2] [scale3] [timing]

    Ryu 5A late packet at 0x908AF0C8 gives stretch/Dhalsim-limb behavior.
    If more than one 34/40 exists, prefer the one after a 35/05 packet; otherwise
    use the last sane candidate in the move block.
    """
    if not move_abs:
        return None
    try:
        buf = rbytes(move_abs, scan_len)
    except Exception:
        return None
    if not buf or len(buf) < 0x20:
        return None

    cands: list[int] = []
    pos = 0
    while True:
        i = buf.find(LIMB_STRETCH_SIG, pos)
        if i < 0:
            break
        if i + 0x18 <= len(buf):
            # Require three finite floats; this rejects many accidental matches.
            f1 = _f32be(buf, i + 0x08)
            f2 = _f32be(buf, i + 0x0C)
            f3 = _f32be(buf, i + 0x10)
            if f1 is not None and f2 is not None and f3 is not None:
                cands.append(i)
        pos = i + 1

    if not cands:
        return None

    spark_i = buf.find(HIT_SPARK_SIG)
    after_spark = [i for i in cands if spark_i >= 0 and i > spark_i]
    # Prefer the first 34/40 immediately after the 35/05 hit-FX packet.
    # Ryu 5A: 35/05 at 0x908AF0AC, limb stretch at 0x908AF0C8.
    i = after_spark[0] if after_spark else cands[-1]

    part = _u32be(buf, i + 0x04)
    s1 = _f32be(buf, i + 0x08)
    s2 = _f32be(buf, i + 0x0C)
    s3 = _f32be(buf, i + 0x10)
    timing = _u32be(buf, i + 0x14)

    return {
        "packet_addr": move_abs + i,
        "part_addr": move_abs + i + 0x04,
        "scale1_addr": move_abs + i + 0x08,
        "scale2_addr": move_abs + i + 0x0C,
        "scale3_addr": move_abs + i + 0x10,
        "timing_addr": move_abs + i + 0x14,
        "part": part,
        "scale1": s1,
        "scale2": s2,
        "scale3": s3,
        "timing": timing,
        "context": buf[max(0, i - 8):min(len(buf), i + 0x20)],
    }


def find_post_animation_link_addr(
    move_abs: int,
    rbytes: Callable[[int, int], bytes],
    *,
    scan_len: int = POST_ANIM_LINK_SCAN_MAX,
) -> tuple[int | None, int | None, int | None, bytes | None]:
    """
    Find the 01/33 post-animation continuation/link packet.

    User poke result: changing the value freezes Ryu after the animation.
    Treat as dangerous, but expose it for full control.

    Returns:
        (packet_addr, value_addr, u32_value, context_bytes)
    """
    if not move_abs:
        return None, None, None, None
    try:
        buf = rbytes(move_abs, scan_len)
    except Exception:
        return None, None, None, None
    if not buf or len(buf) < 8:
        return None, None, None, None

    hits: list[int] = []
    pos = 0
    while True:
        i = buf.find(POST_ANIM_LINK_SIG, pos)
        if i < 0:
            break
        if i + 8 <= len(buf):
            hits.append(i)
        pos = i + 1
    if not hits:
        return None, None, None, None

    # Ryu 5A has one. If multiple exist, the first 01/33 is the direct post-animation link.
    i = hits[0]
    val = _u32be(buf, i + 0x04)
    ctx = buf[max(0, i - 8):min(len(buf), i + 0x10)]
    return move_abs + i, move_abs + i + 0x04, val, ctx


# ---- Hit-result / OTG toggle flag pattern ----
# User-verified on Alex normals after the 0x80042F00 clear mask:
#   0x00000000 = OTG off
#   0x00004000 = OTG on
#   0x00004100+ = reaction/knockdown families. These remain manually editable,
#                 but the preset UI treats OTG as a clean off/on toggle.
HIT_RESULT_CLEAR_SIG = bytes.fromhex("04176000 00000240 3F000000")
HIT_RESULT_OR_SIG = bytes.fromhex("04156000 00000240 3F000000")
HIT_RESULT_CLEAR_MASK = 0x80042F00
HIT_RESULT_OTG_ON = 0x00004000
HIT_RESULT_SCAN_MAX = 0x900


def find_hit_result_flags_addr(
    move_abs: int,
    rbytes: Callable[[int, int], bytes],
    *,
    scan_len: int = HIT_RESULT_SCAN_MAX,
) -> tuple[int | None, int | None, int | None, int | None, bytes | None]:
    """
    Locate the post-hitbox hit-result flag slot for a move block.

    Returns:
        (packet_addr, value_addr, value, clear_mask, context_bytes)

    The preferred candidate is the OR packet immediately following the clear
    mask packet used by Alex Wild Stomp / 2A. This exact slot is where
    0x00004000 was verified as the clean OTG-on value; 0x00000000 is OTG off.
    """
    if not move_abs:
        return None, None, None, None, None
    try:
        buf = rbytes(move_abs, int(scan_len or HIT_RESULT_SCAN_MAX))
    except Exception:
        return None, None, None, None, None
    if not buf or len(buf) < 0x20:
        return None, None, None, None, None

    # Best case: clear-mask packet followed immediately by OR-to-+0x240 packet.
    pos = 0
    while True:
        i = buf.find(HIT_RESULT_CLEAR_SIG, pos)
        if i < 0:
            break
        pos = i + 1
        if i + 0x20 > len(buf):
            continue
        clear_mask = _u32be(buf, i + 0x0C)
        if clear_mask != HIT_RESULT_CLEAR_MASK:
            continue
        j = i + 0x10
        if buf[j:j + len(HIT_RESULT_OR_SIG)] != HIT_RESULT_OR_SIG:
            continue
        val = _u32be(buf, j + 0x0C)
        if val is None:
            continue
        ctx = buf[max(0, i - 0x10):min(len(buf), j + 0x20)]
        return move_abs + j, move_abs + j + 0x0C, int(val), int(clear_mask), ctx

    # Fallback: expose the first raw OR-to-+0x240 packet so unknown move blocks
    # can still be edited/scouted, but callers should treat this as lower
    # confidence because it may be a later/secondary result word.
    i = buf.find(HIT_RESULT_OR_SIG)
    if i >= 0 and i + 0x10 <= len(buf):
        val = _u32be(buf, i + 0x0C)
        if val is not None:
            ctx = buf[max(0, i - 0x10):min(len(buf), i + 0x20)]
            return move_abs + i, move_abs + i + 0x0C, int(val), None, ctx

    return None, None, None, None, None
