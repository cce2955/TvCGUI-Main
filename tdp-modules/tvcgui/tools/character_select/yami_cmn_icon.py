"""Patch only the three appended Yami/Solo thumbnail materials to icon_cmn.

This is intentionally a tiny, guarded character-select patch:
- it requires the character-select resource signature;
- it resolves the live ``icon_cmn`` TEX0 object by its name in both loaded BRRES
  copies, rather than hard-coding a guessed texture pointer;
- it changes only the six B27/B28/B29 texture-binding pointers and their six
  material-header mirrors;
- it writes a restore snapshot before changing anything.

``icon_random0`` is not present in the captured 1015/1022 BRRES resources, so
this module deliberately exposes CMN only. ``icon_none`` is an empty frame,
not a random-character thumbnail.
"""
from __future__ import annotations

import datetime as _dt
import json
import struct
import sys
from pathlib import Path
from typing import Any, Iterable

# Lazy runtime dependency keeps the parser/test helpers runnable without Dolphin.
dio = None


def _runtime_io():
    global dio
    if dio is not None:
        return dio
    try:
        from tvcgui.platform import dolphin as runtime_dio
    except Exception as exc:  # pragma: no cover - live Dolphin only
        raise RuntimeError(
            f"Could not import dolphin_io with {sys.executable!r}: {exc!r}. "
            "Run with the same Python environment as the TvC GUI."
        ) from exc
    dio = runtime_dio
    return dio


# Captured character-select copies. The script aborts rather than writes if a
# reload moved these resources or if the target slots do not currently point to
# valid TEX0 objects.
RESOURCE_WINDOWS: tuple[tuple[str, int, int], ...] = (
    ("1015", 0x92D00000, 0x50000),
    ("1022", 0x932E0000, 0x40000),
)
CHRSEL_SIGNATURE_ADDR = 0x90818400
CHRSEL_SIGNATURE = b"fpack/menu/001/0000.fpk\x00"
TARGET_NAME = b"icon_cmn\x00"
BACKUP_DIR = Path("chrsel_yami_cmn_icon_backups")

# Only permitted output fields. B27/B28/B29 in both loaded BRRES copies.
# Binding fields are the direct thumbnail lookup pointers. Header mirrors are
# required as well because prior material-only tests showed the game can read
# either layer depending on focus / redraw path.
PATCH_FIELDS: tuple[tuple[str, str, int], ...] = (
    ("1015", "B27 binding", 0x92D21CA0),
    ("1015", "B28 binding", 0x92D22280),
    ("1015", "B29 binding", 0x92D22860),
    ("1022", "B27 binding", 0x932E5C60),
    ("1022", "B28 binding", 0x932E6240),
    ("1022", "B29 binding", 0x932E6820),
    ("1015", "B27 material", 0x92D23420),
    ("1015", "B28 material", 0x92D23A00),
    ("1015", "B29 material", 0x92D23FE0),
    ("1022", "B27 material", 0x932E73E0),
    ("1022", "B28 material", 0x932E79C0),
    # B27/B28/B29 material records are 0x5E0 bytes apart in 1022.
    # 0x932E7FE0 is the following GX/display-list data, not a TEX0 pointer.
    ("1022", "B29 material", 0x932E7FA0),
)


def be32(raw: bytes, off: int = 0) -> int | None:
    if off < 0 or off + 4 > len(raw):
        return None
    return struct.unpack(">I", raw[off:off + 4])[0]


def i32(raw: bytes, off: int = 0) -> int | None:
    if off < 0 or off + 4 > len(raw):
        return None
    return struct.unpack(">i", raw[off:off + 4])[0]


def hx(value: int | None) -> str:
    return "<none>" if value is None else f"0x{int(value) & 0xFFFFFFFF:08X}"


def parse_tex0_name(blob: bytes, blob_base: int, tex0_addr: int) -> str | None:
    """Return a TEX0 object's resource name using its relative name pointer."""
    off = int(tex0_addr) - int(blob_base)
    if off < 0 or off + 0x18 > len(blob) or blob[off:off + 4] != b"TEX0":
        return None
    rel = i32(blob, off + 0x14)
    if rel is None:
        return None
    name_off = off + rel
    if name_off < 0 or name_off >= len(blob):
        return None
    end = blob.find(b"\x00", name_off, min(len(blob), name_off + 96))
    if end < 0:
        return None
    try:
        return blob[name_off:end].decode("ascii")
    except UnicodeDecodeError:
        return None


def find_named_tex0(blob: bytes, blob_base: int, target_name: bytes = TARGET_NAME) -> int | None:
    """Find the single TEX0 whose relative name pointer resolves to target_name."""
    expected = bytes(target_name).rstrip(b"\x00").decode("ascii", "strict")
    found: list[int] = []
    pos = 0
    while True:
        off = blob.find(b"TEX0", pos)
        if off < 0:
            break
        pos = off + 4
        if off % 4:
            continue
        addr = int(blob_base) + off
        if parse_tex0_name(blob, blob_base, addr) == expected:
            found.append(addr)
    return found[0] if len(found) == 1 else None


def _rbytes(addr: int, size: int) -> bytes:
    raw = _runtime_io().rbytes(int(addr), int(size))
    return bytes(raw or b"")


def _rd32(addr: int) -> int | None:
    return be32(_rbytes(addr, 4))


def _wr32(addr: int, value: int) -> bool:
    runtime = _runtime_io()
    payload = struct.pack(">I", int(value) & 0xFFFFFFFF)
    try:
        if hasattr(runtime, "wd32"):
            runtime.wd32(int(addr), int(value) & 0xFFFFFFFF)
            return _rd32(addr) == (int(value) & 0xFFFFFFFF)
    except Exception:
        pass
    try:
        ok = bool(runtime.wbytes(int(addr), payload))
    except Exception:
        return False
    return ok and _rd32(addr) == (int(value) & 0xFFFFFFFF)


def _live_tex0_name(ptr: int, windows: dict[str, tuple[int, bytes]]) -> str | None:
    for _name, (base, blob) in windows.items():
        result = parse_tex0_name(blob, base, ptr)
        if result is not None:
            return result
    return None


def _load_windows() -> dict[str, tuple[int, bytes]]:
    windows: dict[str, tuple[int, bytes]] = {}
    for name, base, size in RESOURCE_WINDOWS:
        blob = _rbytes(base, size)
        if len(blob) != size:
            raise RuntimeError(f"Could not read complete {name} resource window at {hx(base)}")
        windows[name] = (base, blob)
    return windows


def resolve_targets(windows: dict[str, tuple[int, bytes]]) -> dict[str, int]:
    targets: dict[str, int] = {}
    for name, (base, blob) in windows.items():
        ptr = find_named_tex0(blob, base)
        if ptr is None:
            raise RuntimeError(f"Could not uniquely resolve icon_cmn TEX0 in {name}")
        targets[name] = ptr
    return targets


def character_select_is_live() -> bool:
    return _rbytes(CHRSEL_SIGNATURE_ADDR, len(CHRSEL_SIGNATURE)) == CHRSEL_SIGNATURE


def snapshot_fields(fields: Iterable[tuple[str, str, int]] = PATCH_FIELDS) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for copy_name, label, addr in fields:
        rows.append({
            "copy": copy_name,
            "label": label,
            "addr": hx(addr),
            "value": hx(_rd32(addr)),
        })
    return rows


def _write_backup(payload: dict[str, Any]) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = BACKUP_DIR / f"{stamp}_cmn_icon_backup.json"
    out.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    (BACKUP_DIR / "latest.json").write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return out


def apply() -> int:
    _runtime_io().hook()
    if not character_select_is_live():
        print("[abort] Character-select signature not present. Open Character Select first.")
        return 2

    windows = _load_windows()
    targets = resolve_targets(windows)
    print("[target] icon_cmn:", ", ".join(f"{name}={hx(ptr)}" for name, ptr in targets.items()))

    before = snapshot_fields()
    invalid: list[str] = []
    for row in before:
        ptr_text = row["value"]
        ptr = int(ptr_text, 16) if isinstance(ptr_text, str) and ptr_text.startswith("0x") else None
        current_name = _live_tex0_name(ptr or 0, windows)
        row["current_tex0"] = current_name or "<invalid>"
        if current_name is None:
            invalid.append(f"{row['copy']} {row['label']} @ {row['addr']} = {ptr_text}")
    if invalid:
        print("[abort] One or more allowed fields do not point to loaded TEX0 objects:")
        for item in invalid:
            print("  ", item)
        print("[safe] No writes were issued.")
        return 3

    backup = _write_backup({
        "kind": "TvC Yami/Solo CMN icon patch",
        "target": "icon_cmn",
        "targets": {name: hx(ptr) for name, ptr in targets.items()},
        "fields_before": before,
    })
    print(f"[backup] {backup}")

    wrote = 0
    failed = 0
    for row in before:
        copy_name = str(row["copy"])
        label = str(row["label"])
        addr = int(str(row["addr"]), 16)
        wanted = targets[copy_name]
        current = _rd32(addr)
        if current == wanted:
            print(f"[ok]    {copy_name} {label}: already icon_cmn")
            continue
        if _wr32(addr, wanted):
            wrote += 1
            print(f"[write] {copy_name} {label}: {hx(current)} -> {hx(wanted)}")
        else:
            failed += 1
            print(f"[fail]  {copy_name} {label}: {hx(current)} -> {hx(wanted)}")

    print(f"[done] CMN icon fields changed={wrote}, failures={failed}")
    return 0 if failed == 0 else 4


def restore() -> int:
    _runtime_io().hook()
    backup_path = BACKUP_DIR / "latest.json"
    if not backup_path.exists():
        print(f"[abort] No backup found at {backup_path}")
        return 2
    try:
        backup = json.loads(backup_path.read_text(encoding="utf-8"))
        fields = list(backup["fields_before"])
        targets = {str(k): int(str(v), 16) for k, v in dict(backup["targets"]).items()}
    except Exception as exc:
        print(f"[abort] Invalid backup: {exc!r}")
        return 3

    wrote = 0
    failed = 0
    for row in fields:
        copy_name = str(row["copy"])
        label = str(row["label"])
        addr = int(str(row["addr"]), 16)
        original = int(str(row["value"]), 16)
        cmn_ptr = int(targets[copy_name])
        current = _rd32(addr)
        if current == original:
            print(f"[ok]    {copy_name} {label}: already restored")
            continue
        if current != cmn_ptr:
            failed += 1
            print(f"[skip]  {copy_name} {label}: current {hx(current)} is not CMN target")
            continue
        if _wr32(addr, original):
            wrote += 1
            print(f"[write] {copy_name} {label}: {hx(current)} -> {hx(original)}")
        else:
            failed += 1
            print(f"[fail]  {copy_name} {label}")
    print(f"[done] restored={wrote}, failures={failed}")
    return 0 if failed == 0 else 4


def audit() -> int:
    _runtime_io().hook()
    if not character_select_is_live():
        print("[warn] Character-select signature not present; reporting pointers only.")
    windows = _load_windows()
    targets = resolve_targets(windows)
    print("[target] icon_cmn:", ", ".join(f"{name}={hx(ptr)}" for name, ptr in targets.items()))
    bad = 0
    for copy_name, label, addr in PATCH_FIELDS:
        ptr = _rd32(addr)
        name = _live_tex0_name(ptr or 0, windows)
        state = "cmn" if ptr == targets.get(copy_name) else "other"
        print(f"[{state}] {copy_name} {label:12s} {hx(addr)} -> {hx(ptr)} {name or '<invalid>'}")
        if name is None:
            bad += 1
    return 0 if bad == 0 else 3


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    mode = argv[0].strip().lower() if argv else "audit"
    if mode in {"apply", "on", "cmn"}:
        return apply()
    if mode in {"restore", "off", "undo"}:
        return restore()
    if mode in {"audit", "check"}:
        return audit()
    print("Usage: tvc_chrsel_yami_cmn_icon.py [audit|apply|restore]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
