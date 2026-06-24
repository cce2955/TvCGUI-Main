#!/usr/bin/env python3
"""
TvC character-select extra-slot icon patcher.

Purpose
-------
Patch the appended bottom-wheel thumbnail rows B27/B28/B29 by copying the
already-working donor thumbnail material rows:

    B27 <- B15  Frank West
    B28 <- B10  Tekkaman Blade
    B29 <- B12  Yatterman-2

Important: the Bxx suffixes here are decimal row suffixes from the resource
names, not hex. Example: B27 is row index 27 decimal == 0x1B.

Run this from the TvCGUI-Main folder while Dolphin is hooked/in character select
or immediately before entering character select, using the same Python env that
has dolphin_io available.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

try:
    from dolphin_io import hook, rbytes, wbytes
except Exception as e:  # pragma: no cover - runtime environment only
    print(f"[fatal] Failed to import dolphin_io hook/rbytes/wbytes: {e!r}")
    print("        Run from the TvCGUI-Main environment/.venv where dolphin_io exists.")
    sys.exit(1)


# Validated live wheel-row table.
ROW_BASE_DEFAULT = 0x9083B3A8
ROW_SIZE_DEFAULT = 0x60

# Known second resource pools / string names. These are not directly rewritten
# by the default patch; they are logged/audited so the configuration can verify the expected
# resource set is live.
KNOWN_STRINGS = {
    0x92D379BE: "1015.thumbnail_0622_B00",
    0x92D3808A: "1015.thumbnail_0622_B27",
    0x92D380CA: "1015.thumbnail_0622_B28",
    0x92D3810A: "1015.thumbnail_0622_B29",
    0x92D381C0: "1015.icon_alx",
    0x92D38220: "1015.icon_fra",
    0x92D38350: "1015.icon_tkb",
    0x92D38380: "1015.icon_ya2",
    0x92D382A0: "1015.icon_none",
    0x932FBB36: "1022.thumbnail_0622_B00",
    0x932FC202: "1022.thumbnail_0622_B27",
    0x932FC242: "1022.thumbnail_0622_B28",
    0x932FC282: "1022.thumbnail_0622_B29",
    0x932FC338: "1022.icon_alx",
    0x932FC398: "1022.icon_fra",
    0x932FC4C8: "1022.icon_tkb",
    0x932FC4F8: "1022.icon_ya2",
    0x932FC418: "1022.icon_none",
}


@dataclass(frozen=True)
class RowCopy:
    source_b_suffix: int  # decimal suffix from thumbnail_0622_Bxx
    target_b_suffix: int  # decimal suffix from thumbnail_0622_Bxx
    label: str


DEFAULT_COPIES: tuple[RowCopy, ...] = (
    RowCopy(15, 27, "Frank donor -> extra slot 0x1B / thumbnail_0622_B27"),
    RowCopy(10, 28, "Tekkaman Blade donor -> extra slot 0x1C / thumbnail_0622_B28"),
    RowCopy(12, 29, "Yatterman-2 donor -> extra slot 0x1D / thumbnail_0622_B29"),
)


def hx(v: int) -> str:
    return f"0x{v:08X}"


def row_addr(base: int, row_size: int, b_suffix_decimal: int) -> int:
    return base + (b_suffix_decimal * row_size)


def is_bad_row(raw: bytes) -> bool:
    if len(raw) == 0:
        return True
    # Empty/unmapped reads sometimes return all 00 or all FF depending on backend.
    return all(b == 0x00 for b in raw) or all(b == 0xFF for b in raw)


def read_exact(addr: int, size: int) -> bytes:
    raw = rbytes(addr, size)
    if raw is None:
        return b""
    return bytes(raw)


def write_exact(addr: int, raw: bytes) -> None:
    if not raw:
        raise ValueError("refusing to write empty byte buffer")
    wbytes(addr, raw)


def u32be(raw: bytes, off: int) -> Optional[int]:
    if off < 0 or off + 4 > len(raw):
        return None
    return (raw[off] << 24) | (raw[off + 1] << 16) | (raw[off + 2] << 8) | raw[off + 3]


def iter_u32be(raw: bytes) -> Iterable[tuple[int, int]]:
    for off in range(0, max(0, len(raw) - 3), 4):
        v = u32be(raw, off)
        if v is not None:
            yield off, v


def summarize_row(raw: bytes) -> dict:
    refs = []
    for off, v in iter_u32be(raw):
        if v in KNOWN_STRINGS:
            refs.append({"off": off, "value": hx(v), "name": KNOWN_STRINGS[v]})
        elif 0x92D30000 <= v <= 0x93310000:
            refs.append({"off": off, "value": hx(v), "name": "resource-pool-ish"})
        elif 0x90000000 <= v <= 0x94000000:
            refs.append({"off": off, "value": hx(v), "name": "mem2-ish"})
    return {
        "len": len(raw),
        "head": raw[:16].hex(" "),
        "tail": raw[-16:].hex(" ") if raw else "",
        "refs": refs,
    }


def make_backup_dir(root: Path) -> Path:
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = root / "chrsel_icon_patch_backups" / stamp
    out.mkdir(parents=True, exist_ok=False)
    return out


def backup_targets(base: int, row_size: int, backup_dir: Path) -> dict:
    manifest = {
        "row_base": hx(base),
        "row_size": hx(row_size),
        "targets": [],
    }
    for cp in DEFAULT_COPIES:
        addr = row_addr(base, row_size, cp.target_b_suffix)
        raw = read_exact(addr, row_size)
        name = f"B{cp.target_b_suffix:02d}_{hx(addr)}.bin"
        (backup_dir / name).write_bytes(raw)
        manifest["targets"].append({
            "b_suffix_decimal": cp.target_b_suffix,
            "addr": hx(addr),
            "file": name,
            "summary": summarize_row(raw),
        })
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def audit_rows(base: int, row_size: int) -> dict:
    rows = []
    wanted = sorted({10, 12, 15, 27, 28, 29})
    for b_suffix in wanted:
        addr = row_addr(base, row_size, b_suffix)
        raw = read_exact(addr, row_size)
        rows.append({
            "name": f"thumbnail_0622_B{b_suffix:02d}",
            "b_suffix_decimal": b_suffix,
            "slot_index_hex": hx(b_suffix),
            "addr": hx(addr),
            "bad_or_empty": is_bad_row(raw),
            "summary": summarize_row(raw),
        })
    return {
        "row_base": hx(base),
        "row_size": hx(row_size),
        "rows": rows,
    }


def print_audit(audit: dict) -> None:
    print(f"[audit] row_base={audit['row_base']} row_size={audit['row_size']}")
    for row in audit["rows"]:
        print(f"  {row['name']} @ {row['addr']} index={row['slot_index_hex']} empty={row['bad_or_empty']}")
        refs = row["summary"].get("refs", [])
        if refs:
            for ref in refs[:10]:
                print(f"    +{ref['off']:02X}: {ref['value']} {ref['name']}")
        else:
            print(f"    head: {row['summary']['head']}")


def apply_copy_patch(
    base: int,
    row_size: int,
    *,
    backup_root: Path,
    dry_run: bool,
    copy_offset: int,
    copy_size: Optional[int],
    force: bool,
) -> None:
    if copy_offset < 0 or copy_offset >= row_size:
        raise ValueError(f"copy_offset must be within row, got {copy_offset:#x}")
    if copy_size is None:
        copy_size = row_size - copy_offset
    if copy_size <= 0 or copy_offset + copy_size > row_size:
        raise ValueError(f"copy window out of row bounds: offset={copy_offset:#x}, size={copy_size:#x}")

    print("[hook] Connecting to Dolphin...")
    hook()

    audit = audit_rows(base, row_size)
    print_audit(audit)

    # Validate all source and target rows are readable before touching anything.
    for cp in DEFAULT_COPIES:
        saddr = row_addr(base, row_size, cp.source_b_suffix)
        taddr = row_addr(base, row_size, cp.target_b_suffix)
        sraw = read_exact(saddr, row_size)
        traw = read_exact(taddr, row_size)
        if len(sraw) != row_size or is_bad_row(sraw):
            msg = f"source B{cp.source_b_suffix:02d} at {hx(saddr)} is unreadable/empty"
            if not force:
                raise RuntimeError(msg + "; use --force only for a validated row")
            print(f"[warn] {msg}")
        if len(traw) != row_size or is_bad_row(traw):
            msg = f"target B{cp.target_b_suffix:02d} at {hx(taddr)} is unreadable/empty"
            if not force:
                raise RuntimeError(msg + "; use --force only for a validated row")
            print(f"[warn] {msg}")

    backup_dir = make_backup_dir(backup_root)
    backup_targets(base, row_size, backup_dir)
    print(f"[backup] Saved original B27/B28/B29 rows to: {backup_dir}")

    for cp in DEFAULT_COPIES:
        saddr = row_addr(base, row_size, cp.source_b_suffix)
        taddr = row_addr(base, row_size, cp.target_b_suffix)
        sraw = read_exact(saddr, row_size)
        traw = bytearray(read_exact(taddr, row_size))
        patched = bytes(sraw) if copy_offset == 0 and copy_size == row_size else bytes(
            traw[:copy_offset] + sraw[copy_offset:copy_offset + copy_size] + traw[copy_offset + copy_size:]
        )

        print(
            f"[copy] {cp.label}: "
            f"B{cp.source_b_suffix:02d}@{hx(saddr)} -> B{cp.target_b_suffix:02d}@{hx(taddr)} "
            f"window=+{copy_offset:#x}/0x{copy_size:X}"
        )
        if not dry_run:
            write_exact(taddr, patched)

    if dry_run:
        print("[dry-run] No writes performed.")
    else:
        print("[done] Wrote donor thumbnail material rows into B27/B28/B29.")
        print("       Leave/re-enter character select, or toggle extras OFF then ON before entering.")


def restore_from_backup(backup_dir: Path, base: int, row_size: int, dry_run: bool) -> None:
    manifest_path = backup_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"missing manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    print("[hook] Connecting to Dolphin...")
    hook()

    for target in manifest.get("targets", []):
        suffix = int(target["b_suffix_decimal"])
        addr = row_addr(base, row_size, suffix)
        raw = (backup_dir / target["file"]).read_bytes()
        if len(raw) != row_size:
            raise RuntimeError(f"backup row {target['file']} length {len(raw)} != row_size {row_size}")
        print(f"[restore] B{suffix:02d} -> {hx(addr)} from {target['file']}")
        if not dry_run:
            write_exact(addr, raw)

    print("[dry-run] No writes performed." if dry_run else "[done] Restored rows from backup.")


def parse_int(s: str) -> int:
    return int(s, 0)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Patch TvC char-select extra slot thumbnail rows B27/B28/B29 using donor material rows."
    )
    ap.add_argument("--row-base", type=parse_int, default=ROW_BASE_DEFAULT,
                    help=f"base address for thumbnail_0622_B00 row table, default {hx(ROW_BASE_DEFAULT)}")
    ap.add_argument("--row-size", type=parse_int, default=ROW_SIZE_DEFAULT,
                    help=f"row size, default {ROW_SIZE_DEFAULT:#x}")
    ap.add_argument("--audit", action="store_true",
                    help="only print B10/B12/B15/B27/B28/B29 row summaries")
    ap.add_argument("--apply", action="store_true",
                    help="apply donor row copy patch")
    ap.add_argument("--restore", type=Path,
                    help="restore B27/B28/B29 from a backup folder created by this script")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be written, but do not write")
    ap.add_argument("--force", action="store_true",
                    help="allow patching even if validation sees an empty/unusual row")
    ap.add_argument("--backup-root", type=Path, default=Path.cwd(),
                    help="folder where chrsel_icon_patch_backups will be created")
    ap.add_argument("--copy-offset", type=parse_int, default=0,
                    help="advanced: row-relative offset to start copying; default full row")
    ap.add_argument("--copy-size", type=parse_int, default=None,
                    help="advanced: number of bytes to copy; default through end of row")

    args = ap.parse_args(argv)

    try:
        if args.restore:
            restore_from_backup(args.restore, args.row_base, args.row_size, args.dry_run)
            return 0

        print("[hook] Connecting to Dolphin...")
        hook()

        if args.audit and not args.apply:
            print_audit(audit_rows(args.row_base, args.row_size))
            return 0

        # Default behavior is apply, because this script exists to do the patch.
        apply_copy_patch(
            args.row_base,
            args.row_size,
            backup_root=args.backup_root,
            dry_run=args.dry_run,
            copy_offset=args.copy_offset,
            copy_size=args.copy_size,
            force=args.force,
        )
        return 0
    except KeyboardInterrupt:
        print("\n[cancelled]")
        return 130
    except Exception as e:
        print(f"[fatal] {e!r}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
