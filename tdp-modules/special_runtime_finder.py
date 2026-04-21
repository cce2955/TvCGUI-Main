#!/usr/bin/env python3
#
# special_runtime_finder.py
#
# General special finder for TvC:
#   Scan MEM2 for animation calls of the form:
#       01 XX 01 3C
#   where 0x01 <= XX <= 0x1E.
#
# Returns a dict: { anim_id (int): [addresses...] }
# Intended for integration into the frame data HUD.

from dolphin_io import rbytes
from constants import MEM2_LO, MEM2_HI

ANIM_MIN = 0x01
ANIM_MAX = 0x1E

_SPECIAL_CACHE = None


def scan_special_anims():
    """
    Scan MEM2 once for 01 XX 01 3C with XX in [ANIM_MIN, ANIM_MAX].
    Returns a dict: {anim_id: [addr0, addr1, ...]}.
    """
    size = MEM2_HI - MEM2_LO
    mem = rbytes(MEM2_LO, size)
    if not mem:
        return {}

    hits_by_id = {aid: [] for aid in range(ANIM_MIN, ANIM_MAX + 1)}

    # linear scan: look for 01 ?? 01 3C
    limit = len(mem) - 3
    i = 0
    while i < limit:
        if mem[i] == 0x01 and mem[i + 2] == 0x01 and mem[i + 3] == 0x3C:
            anim_lo = mem[i + 1]
            if ANIM_MIN <= anim_lo <= ANIM_MAX:
                addr = MEM2_LO + i
                hits_by_id[anim_lo].append(addr)
        i += 1

    # drop empty IDs
    hits_by_id = {aid: addrs for aid, addrs in hits_by_id.items() if addrs}
    return hits_by_id


def get_special_anims(force_rescan: bool = False):
    """
    Cached accessor for the specials scan.

    force_rescan=True will rescan MEM2; otherwise we reuse the
    previous result to avoid heavy work every frame.
    """
    global _SPECIAL_CACHE
    if _SPECIAL_CACHE is None or force_rescan:
        _SPECIAL_CACHE = scan_special_anims()
    return _SPECIAL_CACHE or {}
