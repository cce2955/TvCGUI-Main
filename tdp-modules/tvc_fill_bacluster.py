#!/usr/bin/env python3
"""
tvc_fill_bacluster.py

Inspect / backup / patch the fighter float cluster region
(fighter_base + 0xBA40 .. +0xBA9F) for Tatsunoko vs Capcom via Dolphin RAM I/O.

Modes:
  --action info     : dump region in hex + sanity (HP/X/Y)
  --action live     : repeat info every interval
  --action backup   : save region bytes to tvc_backups/
  --action restore  : write a previous backup back into RAM
  --action fill     : blanket-fill region with a byte
  --action scan     : resolve all 4 slot bases and sanity-check

Examples:
  python tvc_fill_bacluster.py --slot P1C1 --action info
  python tvc_fill_bacluster.py --slot P1C1 --action fill --value 0x00
  python tvc_fill_bacluster.py --action scan
"""

from __future__ import annotations
import argparse, os, sys, time, struct
from typing import Optional, Tuple

# Ensure Dolphin is hooked before any reads.
import dolphin_io as dio
dio.hook()

# -----------------------------
# Constants
# -----------------------------
MANAGERS = {
    "P1C1": 0x803C9FCC,
    "P1C2": 0x803C9FDC,
    "P2C1": 0x803C9FD4,
    "P2C2": 0x803C9FE4,
}

REGION_OFF = 0xBA40
REGION_LEN = 0x60  # BA40..BA9F (96 bytes)
BACKUP_DIR = "tvc_backups"
INTERVAL = 1.0
CHUNK_SIZE_DEFAULT = 0x40
os.makedirs(BACKUP_DIR, exist_ok=True)

HEX = lambda x: f"0x{x:08X}" if isinstance(x, int) else str(x)

# -----------------------------
# Memory I/O Adapter
# -----------------------------
class MemIOAdapter:
    """Unify different dolphin_io APIs (read32/read/write OR read_u32/read_bytes/write_bytes)."""

    def __init__(self):
        self.impl = None
        self._detect()

    def _detect(self):
        try:
            import dolphin_io as _dio  # type: ignore
        except Exception:
            self.impl = None
            return

        cands = []

        if all(hasattr(_dio, n) for n in ("read32", "read", "write")):
            cands.append((
                lambda a: _dio.read32(a),
                lambda a, n: _dio.read(a, n),
                lambda a, b: _dio.write(a, b),
                "read32/read/write",
            ))

        if all(hasattr(_dio, n) for n in ("read_u32", "read_bytes", "write_bytes")):
            cands.append((
                lambda a: _dio.read_u32(a),
                lambda a, n: _dio.read_bytes(a, n),
                lambda a, b: _dio.write_bytes(a, b),
                "read_u32/read_bytes/write_bytes",
            ))

        if hasattr(_dio, "read") and hasattr(_dio, "write"):
            def _read32_via_read(a):
                b = _dio.read(a, 4)
                if not b or len(b) != 4:
                    return None
                return int.from_bytes(b, "big")
            cands.append((
                _read32_via_read,
                lambda a, n: _dio.read(a, n),
                lambda a, b: _dio.write(a, b),
                "read(via32)/write",
            ))

        # Pick the first candidate that returns a real int (not None) on a probe
        probe_addr = list(MANAGERS.values())[0]
        for r32, rby, wby, name in cands:
            try:
                probe = r32(probe_addr)
                if probe is None:
                    continue
                self.impl = {"read32": r32, "read": rby, "write": wby, "desc": name}
                print(f"[INFO] dolphin_io adapter detected: {name}")
                return
            except Exception:
                continue

        self.impl = None

    def _require(self):
        if not self.impl:
            raise RuntimeError(
                "dolphin_io adapter not found or incompatible.\n"
                "Expose one of: (read32, read, write) or (read_u32, read_bytes, write_bytes)."
            )

    def read_u32(self, addr: int) -> Optional[int]:
        self._require()
        return self.impl["read32"](addr)

    def read_bytes(self, addr: int, n: int) -> bytes:
        self._require()
        return self.impl["read"](addr, n) or b""

    def write_bytes(self, addr: int, data: bytes) -> None:
        self._require()
        self.impl["write"](addr, data)


mem = MemIOAdapter()

# -----------------------------
# Core helpers
# -----------------------------
def _plausible_ptr(x: int) -> bool:
    # MEM1+MEM2 common TvC ranges (Dolphin mirrors). Loosened but finite.
    return (
        0x80000000 <= x <= 0x93FFFFFF or
        0x02000000 <= x <= 0x7FFFFFFF
    )

def resolve_fighter_base(slot_key: str) -> int:
    if slot_key not in MANAGERS:
        raise ValueError(f"Unknown slot: {slot_key}")
    mgr_addr = MANAGERS[slot_key]

    p1 = mem.read_u32(mgr_addr)
    if p1 is None:
        raise RuntimeError(f"Manager {HEX(mgr_addr)} unreadable (not hooked or wrong address).")

    if _plausible_ptr(p1):
        p2 = mem.read_u32(p1)
        if p2 is not None and _plausible_ptr(p2):
            return p2
        return p1

    return p1  # may still be useful for debugging

def backup_region(slot_key: str, fighter_base: int) -> str:
    start = fighter_base + REGION_OFF
    data = mem.read_bytes(start, REGION_LEN)
    fname = os.path.join(BACKUP_DIR, f"{slot_key}_{HEX(fighter_base)}_{HEX(start)}_{REGION_LEN}.bin")
    with open(fname, "wb") as f:
        f.write(data)
    print(f"[OK] Backed up {HEX(start)}..{HEX(start+REGION_LEN-1)} -> {fname}")
    return fname

def restore_from_backup(path: str) -> None:
    name = os.path.basename(path)
    # Expect ..._{0xBASE}_{0xSTART}_{LEN}.bin ; parse start from the 3rd token
    parts = name.split("_")
    start_addr = None
    if len(parts) >= 3:
        try:
            start_addr = int(parts[2], 16)
        except Exception:
            pass
    if start_addr is None:
        raise RuntimeError("Could not parse start address from backup filename.")

    with open(path, "rb") as f:
        data = f.read()
    mem.write_bytes(start_addr, data)
    print(f"[OK] Restored {HEX(start_addr)}..{HEX(start_addr+len(data)-1)} from {path}")

def fill_region(start: int, length: int, fill_byte: int, chunk_size: int = CHUNK_SIZE_DEFAULT, guard_byte: Optional[int] = None):
    if guard_byte is not None:
        cur0 = mem.read_bytes(start, 1)
        if not cur0:
            raise RuntimeError("Guard check failed: could not read first byte.")
        if cur0[0] != guard_byte:
            raise RuntimeError(f"Guard check failed: {HEX(cur0[0])} != {HEX(guard_byte)}.")

    left, cur = length, start
    while left > 0:
        n = min(left, chunk_size)
        mem.write_bytes(cur, bytes([fill_byte]) * n)
        cur += n
        left -= n
        time.sleep(0.005)
    print(f"[OK] Wrote {length} bytes of {HEX(fill_byte)} to {HEX(start)}..{HEX(start+length-1)}")

def read_region_hex(start: int, length: int) -> str:
    data = mem.read_bytes(start, length)
    return " ".join(f"{b:02X}" for b in data)

def sanity_check_fighter(base: int) -> Tuple[bool, dict]:
    r = {}
    try:
        max_hp = mem.read_u32(base + 0x24)
        cur_hp = mem.read_u32(base + 0x28)
        x_raw = mem.read_bytes(base + 0xF0, 4)
        y_raw = mem.read_bytes(base + 0xF4, 4)
        if len(x_raw) == 4 and len(y_raw) == 4:
            x = struct.unpack(">f", x_raw)[0]
            y = struct.unpack(">f", y_raw)[0]
        else:
            x = y = None
        r.update({"max_hp": max_hp, "cur_hp": cur_hp, "x": x, "y": y})
        sane = (max_hp is not None and 1000 <= max_hp <= 100000 and
                cur_hp is not None and 0 <= cur_hp <= max_hp)
        return sane, r
    except Exception as e:
        return False, {"error": str(e)}

# -----------------------------
# CLI / Orchestrator
# -----------------------------
def cmd_info(slot: str, base: int, live: bool = False, interval: float = 1.0):
    start = base + REGION_OFF
    title = f"{slot} @ {HEX(base)} -> region {HEX(start)}..{HEX(start+REGION_LEN-1)}"
    if not live:
        print(title)
        print("DATA (hex):")
        print(read_region_hex(start, REGION_LEN))
        sane, rep = sanity_check_fighter(base)
        print("Sanity:", sane, rep)
        return

    print("Entering live read mode. Ctrl-C to exit.")
    try:
        while True:
            print("---")
            print(time.strftime("%H:%M:%S ") + title)
            print(read_region_hex(start, REGION_LEN))
            sane, rep = sanity_check_fighter(base)
            print("Sanity:", sane, rep)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("[INFO] Live mode exited.")

def cmd_scan_all():
    print("Scanning managers -> fighter bases:\n")
    for slot in MANAGERS:
        try:
            fb = resolve_fighter_base(slot)
            sane, rep = sanity_check_fighter(fb)
            print(f"{slot}: base={HEX(fb)} sane={sane} report={rep}")
        except Exception as e:
            print(f"{slot}: failed: {e}")

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--slot", choices=list(MANAGERS.keys()), help="which slot to target")
    p.add_argument("--action", choices=["backup", "fill", "restore", "info", "scan", "live"], required=True)
    p.add_argument("--value", help="byte to fill (hex), e.g. 0xFF", default="0xFF")
    p.add_argument("--guard", help="guard byte (hex) - only write if first byte matches", default=None)
    p.add_argument("--backup", help="backup file to restore from")
    p.add_argument("--fighter_base", help="override resolved fighter_base (hex)")
    p.add_argument("--chunk", help="chunk size (hex or dec)", default=hex(CHUNK_SIZE_DEFAULT))
    p.add_argument("--live-interval", help="seconds between live reads", default=str(INTERVAL))
    args = p.parse_args()

    if args.action == "scan":
        cmd_scan_all()
        return

    if args.action in ("info", "live", "backup", "restore", "fill") and not args.slot:
        p.error("--slot is required for this action")

    # Resolve base
    if args.fighter_base:
        fighter_base = int(args.fighter_base, 16)
    else:
        fighter_base = resolve_fighter_base(args.slot)

    start = fighter_base + REGION_OFF

    if args.action == "info":
        cmd_info(args.slot, fighter_base, live=False)
        return

    if args.action == "live":
        interval = float(args.live_interval)
        cmd_info(args.slot, fighter_base, live=True, interval=interval)
        return

    if args.action == "backup":
        backup_region(args.slot, fighter_base)
        return

    if args.action == "restore":
        if not args.backup:
            p.error("--backup <path> required for restore")
        restore_from_backup(args.backup)
        return

    if args.action == "fill":
        val = int(args.value, 16)
        guard = int(args.guard, 16) if args.guard else None
        chunk = int(args.chunk, 16) if isinstance(args.chunk, str) and args.chunk.startswith("0x") else int(args.chunk)
        bfile = backup_region(args.slot, fighter_base)
        try:
            fill_region(start, REGION_LEN, val, chunk_size=chunk, guard_byte=guard)
        except Exception as e:
            print("[ERROR] fill failed:", e)
            print("[INFO] Restoring from backup.")
            restore_from_backup(bfile)
            raise
        print("[OK] Fill completed. Backup file:", bfile)
        return

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("[FATAL]", e)
        sys.exit(1)
