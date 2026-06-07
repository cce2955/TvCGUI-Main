"""
Read-only character select thumbnail probe.

This does NOT write Dolphin memory. It dumps the donor rows and the appended
B27/B28/B29 rows so the actual safe texture/material binding offset can be
compared instead of copying the whole 0x60 live row.
"""
from __future__ import annotations

import json
import struct
import time
from pathlib import Path

import dolphin_io as dio

ROW_SIZE = 0x60
OUT_DIR = Path("chrsel_thumbnail_probe_dump")

ROWS = {
    "B10_tekkaman_blade_donor": 0x9083B768,
    "B12_yatterman2_donor": 0x9083B828,
    "B15_frank_donor": 0x9083B948,
    "B27_extra_slot_0x1B": 0x9083BDC8,
    "B28_extra_slot_0x1C": 0x9083BE28,
    "B29_extra_slot_0x1D": 0x9083BE88,
    "mirror_B27_extra_slot_0x1B": 0x9083C970,
    "mirror_B28_extra_slot_0x1C": 0x9083C9D0,
    "mirror_B29_extra_slot_0x1D": 0x9083CA30,
}

STRING_POOLS = {
    "pool_1015_names_textures": (0x92D37940, 0x500),
    "pool_1022_names_textures": (0x932FBB00, 0x500),
}

OBJECT_PTRS = {
    "object_A_B27_plus64": 0x80C1DEE4,
    "object_A_B28_plus64": 0x80C1DFB4,
    "object_A_B29_plus64": 0x80C1E084,
    "object_B_B27_plus64": 0x80CC0A84,
    "object_B_B28_plus64": 0x80CC0B54,
    "object_B_B29_plus64": 0x80CC0C24,
}

KNOWN_STRING_ADDRS = {
    0x92D3808A: "1015.thumbnail_0622_B27",
    0x92D380CA: "1015.thumbnail_0622_B28",
    0x92D3810A: "1015.thumbnail_0622_B29",
    0x92D381C0: "1015.icon_alx",
    0x92D38220: "1015.icon_fra",
    0x92D382A0: "1015.icon_none",
    0x92D38350: "1015.icon_tkb",
    0x92D38380: "1015.icon_ya2",
    0x932FC202: "1022.thumbnail_0622_B27",
    0x932FC242: "1022.thumbnail_0622_B28",
    0x932FC282: "1022.thumbnail_0622_B29",
    0x932FC338: "1022.icon_alx",
    0x932FC398: "1022.icon_fra",
    0x932FC418: "1022.icon_none",
    0x932FC4C8: "1022.icon_tkb",
    0x932FC4F8: "1022.icon_ya2",
}


def hex_bytes(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def cstr(data: bytes, max_len: int = 96) -> str:
    data = data[:max_len]
    z = data.find(b"\x00")
    if z >= 0:
        data = data[:z]
    try:
        return data.decode("ascii", errors="replace")
    except Exception:
        return repr(data)


def u32be(data: bytes, off: int) -> int | None:
    if off < 0 or off + 4 > len(data):
        return None
    return struct.unpack(">I", data[off:off+4])[0]


def row_summary(name: str, addr: int, data: bytes) -> dict:
    words = []
    pointers = []
    for off in range(0, min(len(data), ROW_SIZE), 4):
        val = u32be(data, off)
        words.append({"off": f"+0x{off:02X}", "value": f"0x{val:08X}" if val is not None else None})
        if val in KNOWN_STRING_ADDRS:
            pointers.append({"off": f"+0x{off:02X}", "value": f"0x{val:08X}", "label": KNOWN_STRING_ADDRS[val]})
    return {
        "name": name,
        "addr": f"0x{addr:08X}",
        "ascii_at_0": cstr(data),
        "hex": hex_bytes(data),
        "u32_words": words,
        "known_string_pointers_inside_row": pointers,
    }


def main() -> None:
    print("Hooking Dolphin...")
    dio.hook()
    OUT_DIR.mkdir(exist_ok=True)

    now = time.strftime("%Y%m%d_%H%M%S")
    meta: dict = {"created": now, "note": "read-only dump; no memory writes performed", "rows": {}, "pools": {}, "object_ptrs": {}}

    for name, addr in ROWS.items():
        data = dio.rbytes(addr, ROW_SIZE)
        (OUT_DIR / f"{now}_{name}_0x{addr:08X}.bin").write_bytes(data)
        meta["rows"][name] = row_summary(name, addr, data)

    for name, (addr, size) in STRING_POOLS.items():
        data = dio.rbytes(addr, size)
        (OUT_DIR / f"{now}_{name}_0x{addr:08X}.bin").write_bytes(data)
        textish = []
        cur = bytearray()
        start = None
        for i, b in enumerate(data):
            if 0x20 <= b <= 0x7E:
                if start is None:
                    start = i
                cur.append(b)
            else:
                if start is not None and len(cur) >= 4:
                    textish.append({"addr": f"0x{addr+start:08X}", "text": cur.decode("ascii", errors="replace")})
                cur = bytearray()
                start = None
        meta["pools"][name] = {"addr": f"0x{addr:08X}", "size": size, "strings": textish}

    for name, addr in OBJECT_PTRS.items():
        data = dio.rbytes(addr, 4)
        val = u32be(data, 0) if len(data) == 4 else None
        meta["object_ptrs"][name] = {
            "addr": f"0x{addr:08X}",
            "value": f"0x{val:08X}" if val is not None else None,
            "known": KNOWN_STRING_ADDRS.get(val, "") if val is not None else "",
        }

    out = OUT_DIR / f"{now}_chrsel_thumbnail_probe.json"
    out.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Wrote {out}")
    print("Send the JSON plus the .bin files from chrsel_thumbnail_probe_dump if the icon layer still needs to be solved.")


if __name__ == "__main__":
    main()
