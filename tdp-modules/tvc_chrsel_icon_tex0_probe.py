"""Read-only resolver capture for TvC character-select icon textures.

Purpose
-------
Capture just enough of the loaded character-select BRRES resources to map
``icon_cmn`` and ``icon_random0`` to their live TEX0 objects.  It never writes
Dolphin memory.  The later icon patch will redirect only the six known B27/B28/B29
TEX0 binding pointers after this capture proves the target pointers.

Run while the character-select screen is open. Extra Characters may be enabled;
this script does not care whether the cursor is currently on a Yami slot.
"""
from __future__ import annotations

import datetime as _dt
import json
import struct
import sys
import zipfile
from pathlib import Path
from typing import Any, Iterable

# Imported lazily in ``main`` so the pure parsing/contract helpers can be
# regression-tested without a Dolphin memory-engine install.
dio = None


def _runtime_io():
    global dio
    if dio is not None:
        return dio
    try:
        import dolphin_io as runtime_dio
    except Exception as exc:  # pragma: no cover - runtime only
        raise RuntimeError(f"Could not import dolphin_io: {exc!r}") from exc
    dio = runtime_dio
    return dio

# Read-only capture windows surrounding the two loaded select-screen BRRES copies.
# These include known B27/B28/B29 material objects, their texture bindings, and
# the resource-name pools. They are deliberately much smaller than a full MEM2 dump.
RESOURCE_WINDOWS: tuple[tuple[str, int, int], ...] = (
    ("1015", 0x92D00000, 0x50000),
    ("1022", 0x932E0000, 0x40000),
)

# Known from prior select dumps. The eventual write feature may only touch these
# fields; this diagnostic merely records them.
B27_B29_BINDINGS: tuple[tuple[str, int], ...] = (
    ("1015_B27_binding_tex0", 0x92D21CA0),
    ("1015_B28_binding_tex0", 0x92D22280),
    ("1015_B29_binding_tex0", 0x92D22860),
    ("1022_B27_binding_tex0", 0x932E5C60),
    ("1022_B28_binding_tex0", 0x932E6240),
    ("1022_B29_binding_tex0", 0x932E6820),
)

B27_B29_MATERIAL_TEX0: tuple[tuple[str, int], ...] = (
    ("1015_B27_material_tex0", 0x92D23420),  # 0x92D23000 + 0x420
    ("1015_B28_material_tex0", 0x92D23A00),  # 0x92D235E0 + 0x420
    ("1015_B29_material_tex0", 0x92D23FE0),  # 0x92D23BC0 + 0x420
    ("1022_B27_material_tex0", 0x932E73E0),  # 0x932E6FC0 + 0x420
    ("1022_B28_material_tex0", 0x932E79C0),  # 0x932E75A0 + 0x420
    ("1022_B29_material_tex0", 0x932E7FE0),  # 0x932E7B80 + 0x420
)

TARGET_NAMES: tuple[bytes, ...] = (
    b"icon_cmn\x00",
    b"icon_random0\x00",
    b"icon_none\x00",
    b"icon_fra\x00",
    b"icon_tkb\x00",
    b"icon_ya2\x00",
)

CHRSEL_SIGNATURE_ADDR = 0x90818400
CHRSEL_SIGNATURE = b"fpack/menu/001/0000.fpk\x00"
OUT_DIR = Path("chrsel_icon_tex0_probe_dump")


def be32(raw: bytes, off: int = 0) -> int | None:
    if off < 0 or off + 4 > len(raw):
        return None
    return struct.unpack(">I", raw[off:off + 4])[0]


def hx(value: int | None) -> str:
    return "" if value is None else f"0x{value:08X}"


def rbytes_exact(addr: int, size: int) -> bytes:
    raw = _runtime_io().rbytes(int(addr), int(size))
    return bytes(raw or b"")


def find_all(blob: bytes, needle: bytes) -> list[int]:
    if not needle:
        return []
    out: list[int] = []
    pos = 0
    while True:
        pos = blob.find(needle, pos)
        if pos < 0:
            return out
        out.append(pos)
        pos += 1


def ascii_neighbors(blob: bytes, offset: int, radius: int = 96) -> str:
    start = max(0, int(offset) - int(radius))
    end = min(len(blob), int(offset) + int(radius))
    data = blob[start:end]
    return "".join(chr(x) if 0x20 <= x <= 0x7E else "." for x in data)


def find_u32_refs(blob: bytes, blob_base: int, absolute_target: int) -> list[int]:
    wanted = int(absolute_target & 0xFFFFFFFF).to_bytes(4, "big")
    hits: list[int] = []
    for off in range(0, max(0, len(blob) - 3), 4):
        if blob[off:off + 4] == wanted:
            hits.append(blob_base + off)
    return hits


def scan_bres_headers(blob: bytes, blob_base: int) -> list[dict[str, Any]]:
    headers: list[dict[str, Any]] = []
    for off in find_all(blob, b"bres"):
        # BRRES begins at a four-byte boundary. A valid header should have a
        # reasonable big-endian file size at +0x08. Keep this tolerant because
        # we are capturing loaded heap copies, not assuming a specific revision.
        if off % 4:
            continue
        size = be32(blob, off + 8)
        if size is None or size < 0x40 or size > len(blob) - off:
            continue
        headers.append({
            "addr": hx(blob_base + off),
            "file_size": hx(size),
            "head_hex": blob[off:off + min(0x20, len(blob) - off)].hex(" "),
        })
    return headers


def scan_resource_window(name: str, base: int, blob: bytes) -> dict[str, Any]:
    name_hits: dict[str, list[dict[str, Any]]] = {}
    for target in TARGET_NAMES:
        label = target.rstrip(b"\x00").decode("ascii", "replace")
        found: list[dict[str, Any]] = []
        for off in find_all(blob, target):
            absolute = base + off
            found.append({
                "addr": hx(absolute),
                "word_refs": [hx(v) for v in find_u32_refs(blob, base, absolute)],
                "context": ascii_neighbors(blob, off),
            })
        name_hits[label] = found
    return {
        "name": name,
        "base": hx(base),
        "bytes_read": len(blob),
        "bres_headers": scan_bres_headers(blob, base),
        "target_names": name_hits,
    }


def read_pointer_snapshot(rows: Iterable[tuple[str, int]]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for label, addr in rows:
        raw = rbytes_exact(addr, 4)
        out[label] = {"addr": hx(addr), "value": hx(be32(raw))}
    return out


def main() -> int:
    print("[hook] Connecting to Dolphin (read-only icon probe)...")
    try:
        _runtime_io().hook()
    except RuntimeError as exc:
        print(f"[fatal] {exc}")
        return 1
    print("[hook] connected")

    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = OUT_DIR / stamp
    out.mkdir(parents=True, exist_ok=False)

    signature = rbytes_exact(CHRSEL_SIGNATURE_ADDR, len(CHRSEL_SIGNATURE))
    meta: dict[str, Any] = {
        "created": stamp,
        "read_only": True,
        "character_select_signature_addr": hx(CHRSEL_SIGNATURE_ADDR),
        "character_select_signature_ok": signature == CHRSEL_SIGNATURE,
        "character_select_signature_hex": signature.hex(" "),
        "windows": {},
        "bindings_before": read_pointer_snapshot(B27_B29_BINDINGS),
        "material_tex0_before": read_pointer_snapshot(B27_B29_MATERIAL_TEX0),
        "target_names": [x.rstrip(b"\x00").decode("ascii") for x in TARGET_NAMES],
    }

    for name, base, size in RESOURCE_WINDOWS:
        print(f"[read] {name} {hx(base)} + {size:#x}")
        blob = rbytes_exact(base, size)
        (out / f"{name}_{hx(base)}_{len(blob):X}.bin").write_bytes(blob)
        meta["windows"][name] = scan_resource_window(name, base, blob)

    (out / "probe.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    zip_path = OUT_DIR / f"{stamp}_chrsel_icon_tex0_probe.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for child in out.iterdir():
            zf.write(child, arcname=child.name)

    print(f"[done] Wrote {zip_path}")
    if not meta["character_select_signature_ok"]:
        print("[warn] Character-select signature was not found. The files are still useful, but capture again while character select is visibly open if target names are missing.")
    else:
        print("[done] Character-select signature confirmed.")
    print("[safe] No Dolphin memory writes were issued.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
