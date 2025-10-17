# meter.py
# Meter address cache and reader.

from typing import Dict, Optional

from constants import METER_OFF_PRIMARY, METER_OFF_SECONDARY
from dolphin_io import rd32

class MeterAddrCache:
    """
    Caches which address (primary vs mirrored bank) actually holds meter
    for a given fighter base. Falls back gracefully if values look odd.
    """
    def __init__(self) -> None:
        self.addr_by_base: Dict[int, int] = {}

    def drop(self, base: int) -> None:
        """Invalidate cached address for this base (call when base changes)."""
        self.addr_by_base.pop(base, None)

    def get(self, base: int) -> int:
        """Return the chosen meter address for this base (and cache it)."""
        if base in self.addr_by_base:
            return self.addr_by_base[base]

        # Probe primary, then mirrored bank
        for a in (base + METER_OFF_PRIMARY, base + METER_OFF_SECONDARY):
            v = rd32(a)
            # Known full meter constants: 50000 (decimal) == 0xC350
            if v in (50000, 0xC350) or (v is not None and 0 <= v <= 200_000):
                self.addr_by_base[base] = a
                return a

        # If nothing looked right, still cache primary to avoid thrashing
        self.addr_by_base[base] = base + METER_OFF_PRIMARY
        return self.addr_by_base[base]


_CACHE = MeterAddrCache()

def read_meter(base: Optional[int]) -> Optional[int]:
    """Read meter value for a fighter base. Returns None if invalid."""
    if not base:
        return None
    addr = _CACHE.get(base)
    v = rd32(addr)
    if v is None or v < 0 or v > 200_000:
        return None
    return v

def drop_meter_cache_for_base(base: int) -> None:
    """Expose cache invalidation (use when a slot’s base changes)."""
    _CACHE.drop(base)
