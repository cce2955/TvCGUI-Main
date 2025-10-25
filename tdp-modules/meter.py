# meter.py
# Reads super meter with caching of which mirror is valid. :contentReference[oaicite:5]{index=5}

from dolphin_io import rd32
from constants import METER_OFF_PRIMARY, METER_OFF_SECONDARY

class MeterAddrCache:
    def __init__(self):
        self.addr_by_base = {}

    def drop(self, base):
        self.addr_by_base.pop(base, None)

    def get(self, base):
        # Return cached address or detect best bank.
        if base in self.addr_by_base:
            return self.addr_by_base[base]

        for a in (base + METER_OFF_PRIMARY, base + METER_OFF_SECONDARY):
            v = rd32(a)
            if v in (50000, 0xC350) or (v is not None and 0 <= v <= 200_000):
                self.addr_by_base[base] = a
                return a

        # Fallback to primary
        self.addr_by_base[base] = base + METER_OFF_PRIMARY
        return self.addr_by_base[base]

METER_CACHE = MeterAddrCache()

def read_meter(base):
    if not base:
        return None
    addr = METER_CACHE.get(base)
    v = rd32(addr)
    if v is None or v < 0 or v > 200_000:
        return None
    return v
