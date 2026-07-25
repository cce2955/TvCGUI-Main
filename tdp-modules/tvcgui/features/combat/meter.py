# meter.py
#
# Team super meter lives at fixed MEM2 addresses. The same value is also
# available as the team-owner field +0x4C.
#
# Confirmed from both team dumps:
#   P1 current meter: 0x9246BA0C
#   P2 current meter: 0x927EBA2C
#   50000 is the normal cap and represents 5 full bars.
#   One bar is 10000 units.
#   Values are stored as big-endian u32 integers.

from tvcgui.platform.dolphin import rd32

METER_ADDR_P1 = 0x9246BA0C  
METER_ADDR_P2 = 0x927EBA2C   
 
 
_METER_MAX = 200_000

_METER_ADDR_BY_TEAM = {
    "P1": METER_ADDR_P1,
    "P2": METER_ADDR_P2,
}


class MeterAddrCache:
    """Kept for API compatibility."""
    def __init__(self): pass
    def drop(self, base): pass
    def get(self, base): return METER_ADDR_P1


METER_CACHE = MeterAddrCache()


_debug_printed = set()

def read_meter(base, *, teamtag: str | None = None) -> int | None:
    if not base:
        return None

    addr = _METER_ADDR_BY_TEAM.get(teamtag, METER_ADDR_P1)
    v = rd32(addr)

    if teamtag not in _debug_printed:
        _debug_printed.add(teamtag)
        print(f"[meter] teamtag={teamtag} addr=0x{addr:08X} rd32={v!r}")

    if v is None or v > _METER_MAX:
        return None
    return v