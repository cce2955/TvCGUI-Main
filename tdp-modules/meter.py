# meter.py
#
# Super meter lives at fixed MEM2 addresses, NOT relative to the fighter
# struct base.
#
# Confirmed from memory dump:
#   9246ba00 row: 00 00 08 e8 00 00 1f 44 00 00 00 c3 [50] ...
#   0xC350 = 50000 = 1 stock of TvC meter
#   Value is a big-endian u16 at 0x9246BA0C (high byte c3, low byte 50)
#
# P2 stride is estimated at +0x40; confirm with a P2 dump if needed.

from dolphin_io import rd32

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