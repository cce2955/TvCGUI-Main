# fd_patterns.py
#
# Pattern scanners / address discovery for move blocks.

from __future__ import annotations

# ---- Combo-only KB/Vacuum modifier pattern ----
COMBO_KB_SIG_A = bytes([0x01, 0xAC, 0x3D, 0x00, 0x00, 0x00])  # then XX at +6
COMBO_KB_SIG_B = bytes([0x01, 0xAC, 0x3F, 0x00, 0x00, 0x00])  # then XX at +6
COMBO_KB_SCAN_MAX = 0x200  # scan first 0x200 bytes of the move block

# ---- SuperBG pattern ----
SUPERBG_ON = 0x04
SUPERBG_OFF = 0x00
SUPERBG_MARKER = 0x60
SUPERBG_LOOKAHEAD = 0x80


def find_anim_hdr_offset(buf: bytes) -> int | None:
    """
    Find first occurrence of animation header:
        01 ?? 01 3C
    Returns offset or None.
    """
    if not buf or len(buf) < 4:
        return None
    for i in range(0, len(buf) - 4):
        if buf[i] == 0x01 and buf[i + 2] == 0x01 and buf[i + 3] == 0x3C:
            return i
    return None


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
      ... 01 ?? 01 3C ... then later: 04 XX 60
      - 0x04 is a fixed marker
      - XX is the toggled value (commonly 0x01 or 0x04)
      - 0x60 is a trailing marker

    We return the address of XX (the middle byte).
    """
    if not move_abs:
        return None, None

    try:
        buf = rbytes_func(move_abs, SUPERBG_LOOKAHEAD)
    except Exception:
        return None, None

    if not buf or len(buf) < 16:
        return None, None

    anim_off = find_anim_hdr_offset(buf)
    if anim_off is None:
        return None, None

    # scan forward after the anim header for the triplet 04 ?? 60
    start = anim_off + 4
    end = min(len(buf) - 2, anim_off + SUPERBG_LOOKAHEAD)

    for i in range(start, end):
        if buf[i] == 0x04 and buf[i + 2] == SUPERBG_MARKER:
            addr = move_abs + i + 1  # middle byte (XX)
            try:
                cur = rd8_func(addr)
            except Exception:
                cur = None
            return addr, cur

    return None, None
