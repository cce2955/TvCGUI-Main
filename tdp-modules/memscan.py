# memscan.py
# ASCII scanner + pointer backref helper for Wii RAM (MEM1/MEM2)
from __future__ import annotations
from typing import List, Tuple, Dict, Iterable, Optional
from dolphin_io import rbytes

# Wii memory regions (adjust if your build differs)
MEM1_START, MEM1_END = 0x80000000, 0x81800000   # ~24 MB
MEM2_START, MEM2_END = 0x90000000, 0x94000000   # up to 64 MB (not always used)

CHUNK = 64 * 1024  # read in chunks to avoid large I/O bursts

# Strings that look like file/sequence/label content
DEFAULT_PATTERNS = [
    b'.seq', b'.mot', b'.anm', b'.arc', b'.brres', b'.tpl', b'.pac',
    b'RYU', b'KEN', b'HADO', b'SHORYU', b'TATSU',
    b'5A', b'2A', b'5B', b'2B', b'5C', b'2C', b'6C',
    b'LIGHT', b'MED', b'HEAVY', b'JAB', b'NORM', b'SUPER', b'SPECIAL'
]

def _read_range(start: int, end: int) -> Iterable[Tuple[int, bytes]]:
    addr = start
    while addr < end:
        n = min(CHUNK, end - addr)
        data = rbytes(addr, n) or b""
        yield addr, data
        addr += n

def _ascii_runs(data: bytes, abs_base: int, min_len: int = 4) -> List[Tuple[int, bytes]]:
    out: List[Tuple[int, bytes]] = []
    s = -1
    for i, b in enumerate(data):
        if 32 <= b <= 126:  # printable
            if s < 0: s = i
        else:
            if s >= 0 and (i - s) >= min_len:
                out.append((abs_base + s, data[s:i]))
            s = -1
    if s >= 0 and (len(data) - s) >= min_len:
        out.append((abs_base + s, data[s:len(data)]))
    return out

def _filter_runs(runs: List[Tuple[int, bytes]], patterns: List[bytes]) -> List[Tuple[int, str]]:
    pats = [p.lower() for p in patterns]
    hits: List[Tuple[int, str]] = []
    for addr, raw in runs:
        low = raw.lower()
        if any(p in low for p in pats):
            hits.append((addr, raw.decode('ascii', errors='ignore')))
    return hits

def scan_global(patterns: Optional[List[bytes]] = None) -> List[Tuple[int, str]]:
    pats = patterns or DEFAULT_PATTERNS
    seen: Dict[str, int] = {}
    hits: List[Tuple[int, str]] = []
    for start, end in ((MEM1_START, MEM1_END), (MEM2_START, MEM2_END)):
        for base, chunk in _read_range(start, end):
            for addr, txt in _filter_runs(_ascii_runs(chunk, base), pats):
                if txt not in seen:
                    seen[txt] = addr
                    hits.append((addr, txt))
    return sorted(hits, key=lambda t: t[0])

def scan_local(centers: List[int], radius: int = 0x4000,
               patterns: Optional[List[bytes]] = None) -> List[Tuple[int, str]]:
    pats = patterns or DEFAULT_PATTERNS
    seen: Dict[str, int] = {}
    hits: List[Tuple[int, str]] = []
    for c in centers:
        if not c: 
            continue
        start = max(c - radius, MEM1_START)
        end   = min(c + radius, MEM2_END)  # allow spill into MEM2 just in case
        for base, chunk in _read_range(start, end):
            for addr, txt in _filter_runs(_ascii_runs(chunk, base), pats):
                if txt not in seen:
                    seen[txt] = addr
                    hits.append((addr, txt))
    return sorted(hits, key=lambda t: t[0])

def find_backrefs(target_addrs: List[int],
                  hay_regions: List[Tuple[int,int]] = [(MEM1_START, MEM1_END),
                                                       (MEM2_START, MEM2_END)]
                 ) -> Dict[int, List[int]]:
    """
    Scan memory for 32-bit big-endian pointers that equal any address in target_addrs.
    Returns {target_addr: [ref_addr, ...]}
    """
    targets = set(target_addrs)
    out: Dict[int, List[int]] = {t: [] for t in targets}
    for start, end in hay_regions:
        for base, chunk in _read_range(start, end):
            # big-endian 32-bit addresses
            for i in range(0, len(chunk) - 3, 4):
                val = int.from_bytes(chunk[i:i+4], 'big')
                if val in targets:
                    out[val].append(base + i)
    return out