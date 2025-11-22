#!/usr/bin/env python

#
# TvC Full Caller Probe (Ryu only, IDs 0x30–0x40)
#

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:
    from dolphin_io import hook, rbytes, addr_in_ram
    import scan_normals_all
    from constants import MEM1_LO, MEM1_HI, MEM2_LO, MEM2_HI
except Exception as e:
    print("Import failure in caller_full_ryu_probe:", e)
    sys.exit(1)

TARGET_CHAR = "ryu"      # normalized name (lowercase, no spaces)
ID_MIN = 0x30            # inclusive
ID_MAX = 0x40            # inclusive
PATTERN_LEN = 4


def _norm(name: str) -> str:
    name = (name or "").strip().lower()
    for ch in (" ", "-", "_", "."):
        name = name.replace(ch, "")
    return name


def build_slot_ranges(scan_data, padding: int = 0x4000):
    per_slot_addrs = {}
    per_slot_char = {}

    for slot_info in scan_data:
        slot_label = slot_info.get("slot_label", "?")
        char_name = (slot_info.get("char_name") or "").strip()

        if slot_label not in per_slot_char:
            per_slot_char[slot_label] = char_name

        for mv in slot_info.get("moves", []):
            base = mv.get("abs")
            if base is None:
                continue
            if not addr_in_ram(base):
                continue
            per_slot_addrs.setdefault(slot_label, []).append(base)

    slot_ranges = {}
    for slot_label, addrs in per_slot_addrs.items():
        if not addrs:
            continue
        lo = min(addrs) - padding
        hi = max(addrs) + padding
        slot_ranges[slot_label] = {
            "char": per_slot_char.get(slot_label, ""),
            "lo": lo,
            "hi": hi,
        }

    return slot_ranges


def _match_full(buf: bytes, i: int):
    """
    Detect full caller header at buf[i].

    Returns (ok, form, id_val, opcode_offset_in_buf):

      form 'A' : 01 XX 04 ...
      form 'B' : 00 01 XX 04 ...
    """
    n = len(buf)

    # Form A: 01 XX 04 ...
    if i + 2 < n and buf[i] == 0x01 and buf[i + 2] == 0x04:
        return True, "A", buf[i + 1], i

    # Form B: 00 01 XX 04 ...
    if (
        i + 3 < n
        and buf[i] == 0x00
        and buf[i + 1] == 0x01
        and buf[i + 3] == 0x04
    ):
        return True, "B", buf[i + 2], i + 1

    return False, "", 0, 0


def scan_ryu_full_callers(ryu_ranges):
    hits = []

    for slot_label, info in ryu_ranges.items():
        char_name = info.get("char", "")
        lo = info.get("lo")
        hi = info.get("hi")

        print(
            f"Scanning {slot_label} ({char_name}) "
            f"[0x{lo:08X}, 0x{hi:08X}) for full callers ID 0x{ID_MIN:02X}–0x{ID_MAX:02X}..."
        )

        addr = lo
        tail = b""

        while addr < hi:
            chunk_size = 0x1000
            remaining = hi - addr
            size = chunk_size if remaining > chunk_size else remaining
            if size <= 0:
                break

            data = rbytes(addr, size)
            if not data:
                addr += size
                tail = b""
                continue

            buf = tail + data
            base_for_buf = addr - len(tail)
            n = len(buf)
            i = 0

            while i <= n - PATTERN_LEN:
                ok, form, id_val, op_off = _match_full(buf, i)
                if ok and ID_MIN <= id_val <= ID_MAX:
                    hit_addr = base_for_buf + op_off
                    ctx_start = hit_addr - 0x10
                    ctx_size = 0x40
                    ctx = rbytes(ctx_start, ctx_size) or b""
                    ctx_str = " ".join(f"{b:02X}" for b in ctx)

                    hits.append(
                        {
                            "slot": slot_label,
                            "char": char_name,
                            "addr": hit_addr,
                            "id": id_val,
                            "form": form,
                            "ctx": ctx_str,
                        }
                    )

                    print(
                        f"{slot_label} ({char_name})  "
                        f"ID=0x{id_val:02X} form={form} addr=0x{hit_addr:08X}"
                    )

                i += 1

            if len(buf) >= PATTERN_LEN - 1:
                tail = buf[-(PATTERN_LEN - 1):]
            else:
                tail = buf

            addr += size

    return hits


def main():
    print("Hooking Dolphin...")
    hook()
    print("Hooked. Running scan_normals_all.scan_once()...")

    try:
        scan_data = scan_normals_all.scan_once()
    except Exception as e:
        print("scan_normals_all.scan_once() failed:", e)
        sys.exit(1)

    slot_ranges = build_slot_ranges(scan_data)

    # Filter to only Ryu slots
    ryu_ranges = {
        slot_label: info
        for slot_label, info in slot_ranges.items()
        if _norm(info.get("char", "")) == TARGET_CHAR
    }

    if not ryu_ranges:
        print("No Ryu slots found. Are you in a match with Ryu loaded?")
        sys.exit(1)

    print("Ryu slot ranges:")
    for slot_label, info in ryu_ranges.items():
        lo = info.get("lo")
        hi = info.get("hi")
        print(f"  {slot_label}: {info.get('char','')} [0x{lo:08X}, 0x{hi:08X})")

    print()
    hits = scan_ryu_full_callers(ryu_ranges)

    print()
    print(f"Total Ryu full-call hits in ID range 0x{ID_MIN:02X}–0x{ID_MAX:02X}: {len(hits)}")
    print("Sorted by ID then address:")
    for h in sorted(hits, key=lambda x: (x["id"], x["addr"])):
        print(
            f"{h['slot']} ({h['char']})  ID=0x{h['id']:02X} form={h['form']} "
            f"addr=0x{h['addr']:08X}"
        )
        # If you want context uncomment:
        # print("  ", h["ctx"])


if __name__ == "__main__":
    main()
