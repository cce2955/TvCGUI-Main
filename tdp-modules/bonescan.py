# bonescan.py

from collections import defaultdict
import struct
import time

from dolphin_io import rbytes

PAGE_SIZE = 0x100
SCAN_RADIUS = 0x2000      # ± around fighter base
FLOAT_MIN = -5.0
FLOAT_MAX = 5.0


def _f32(b):
    return struct.unpack(">f", b)[0]


class BoneScanResult:
    __slots__ = ("addr", "float_count", "change_count", "score")

    def __init__(self, addr, floats, changes):
        self.addr = addr
        self.float_count = floats
        self.change_count = changes
        self.score = floats + changes * 0.25


class BoneScanner:
    def __init__(self, fighter_base):
        self.base = fighter_base
        self._prev = {}
        self.results = []

    def _scan_page(self, addr):
        try:
            data = rbytes(addr, PAGE_SIZE)
        except Exception:
            return None

        floats = 0
        for i in range(0, PAGE_SIZE - 4, 4):
            try:
                f = _f32(data[i:i+4])
            except Exception:
                continue
            if FLOAT_MIN < f < FLOAT_MAX:
                floats += 1

        prev = self._prev.get(addr)
        changes = 0
        if prev:
            for a, b in zip(prev, data):
                if a != b:
                    changes += 1

        self._prev[addr] = data
        return floats, changes

    def step(self):
        """Run ONE incremental scan pass."""
        self.results.clear()

        start = self.base - SCAN_RADIUS
        end   = self.base + SCAN_RADIUS

        for addr in range(start, end, PAGE_SIZE):
            r = self._scan_page(addr)
            if not r:
                continue

            floats, changes = r
            if floats < 32 and changes < 4:
                continue

            self.results.append(BoneScanResult(addr, floats, changes))

        self.results.sort(key=lambda r: r.score, reverse=True)
