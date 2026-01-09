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


def find_speed_mod_addr(
    move_abs: int,
    rbytes: Callable[[int, int], bytes],
    *,
    scan_len: int = 0x800,
) -> Tuple[Optional[int], Optional[int], Optional[bytes]]:
    """
    Speed modifier locator (tight pattern).

    Observed layout inside move block:
        ... 20 3F 00 00 00 XX 04 17 ...

    - Anchor: 20 3F 00 00 00
    - Value:  XX (1 byte) immediately after anchor
    - Tail:   04 17 immediately after XX

    Returns:
      (absolute_addr_of_value, current_value_byte, context_bytes)
    """
    if not move_abs:
        return (None, None, None)

    try:
        buf = rbytes(move_abs, scan_len)
    except Exception:
        return (None, None, None)

    if not buf:
        return (None, None, None)

    anchor = b"\x20\x3F\x00\x00\x00"  # 5 bytes
    tail = b"\x04\x17"                # 2 bytes

    start = 0
    while True:
        i = buf.find(anchor, start)
        if i < 0:
            break

        # Need: anchor(5) + value(1) + tail(2)
        value_off = i + len(anchor)
        tail_off = value_off + 1
        if tail_off + len(tail) <= len(buf):
            if buf[tail_off:tail_off + len(tail)] == tail:
                addr = move_abs + value_off
                cur = buf[value_off]
                ctx = buf[i:min(len(buf), i + 16)]
                return (addr, cur, ctx)

        start = i + 1

    return (None, None, None)


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
