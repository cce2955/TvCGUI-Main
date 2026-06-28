"""Live Dolphin smoke diagnostics for TvC Continuo.

This is not a replacement for unit tests. The unittest suite is deterministic
and uses fake memory. This script is for checking the real emulator boundary:
hook, basic MEM2 reads, slot pointer reads, chr_tbl resolution, move ID lookup,
and an optional safe write-echo that writes one byte back to its current value.

Default mode is read-only:
    python run_dolphin_smoke_tests.py

Optional write echo, still intended to be non-mutating:
    python run_dolphin_smoke_tests.py --write-echo 0x90000000
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any


def _parse_int(text: str) -> int:
    s = str(text).strip().replace("_", "")
    if s.lower().startswith("0x"):
        return int(s, 16)
    return int(s, 10)


def _fmt_addr(value: int | None) -> str:
    if value is None:
        return "None"
    return f"0x{int(value) & 0xFFFFFFFF:08X}"


def _status(ok: bool, label: str, detail: str = "") -> bool:
    prefix = "PASS" if ok else "FAIL"
    suffix = f" - {detail}" if detail else ""
    print(f"[{prefix}] {label}{suffix}")
    return ok


def _read_slot_rows(dolphin_io: Any, constants: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, ptr_addr, team in constants.SLOTS:
        base = dolphin_io.rd32(ptr_addr) or 0
        char_id = dolphin_io.rd32(base + constants.OFF_CHAR_ID) if base else None
        rows.append({
            "label": label,
            "team": team,
            "ptr_addr": ptr_addr,
            "base": base,
            "char_id": char_id,
            "name": constants.CHAR_NAMES.get(char_id, f"ID_{char_id}" if char_id is not None else "unloaded"),
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run live Dolphin smoke diagnostics.")
    parser.add_argument(
        "--write-echo",
        metavar="ADDR",
        help="optional: read one byte at ADDR, write the same byte back, then read it again",
    )
    parser.add_argument(
        "--skip-hook",
        action="store_true",
        help="skip dolphin_io.hook(); useful only when testing import/read failures",
    )
    args = parser.parse_args(argv)

    ok_all = True

    try:
        import tvcgui.platform.dolphin as dolphin_io
        import tvcgui.core.constants as constants
        import tvcgui.features.combat.move_id_map as move_id_map
        import tvcgui.tools.scanners.normal_scanner as scan_normals_all
    except Exception as exc:
        _status(False, "imports", repr(exc))
        return 1

    _status(True, "imports", "platform.dolphin/core.constants/combat.move_id_map/scanners.normal_scanner")

    if not args.skip_hook:
        print("[Dolphin smoke] Hooking Dolphin...")
        try:
            dolphin_io.hook()
            _status(True, "hook returned")
        except KeyboardInterrupt:
            print("[Dolphin smoke] interrupted while waiting for Dolphin")
            return 130
        except Exception as exc:
            _status(False, "hook", repr(exc))
            return 1

    if hasattr(dolphin_io, "mem2_latch_info"):
        try:
            info = dolphin_io.mem2_latch_info()
            latched = bool(info.get("latched"))
            detail = f"latched={latched} pid={info.get('pid')} host_base={_fmt_addr(info.get('host_base'))}"
            # On non-Windows, the latch is expected to be false/unavailable, so
            # report it as information rather than a hard failure.
            _status(True, "MEM2 latch info", detail)
        except Exception as exc:
            ok_all = False
            _status(False, "MEM2 latch info", repr(exc))

    try:
        probe = dolphin_io.rbytes(getattr(dolphin_io, "EXPECT_EA", 0x9246B9C0), 32)
        ok = bool(probe and len(probe) == 32)
        ok_all = _status(ok, "MEM2 sentinel read", f"len={len(probe) if probe else 0}") and ok_all
    except Exception as exc:
        ok_all = False
        _status(False, "MEM2 sentinel read", repr(exc))

    try:
        print("[Dolphin smoke] Slot pointers:")
        rows = _read_slot_rows(dolphin_io, constants)
        loaded_rows = [row for row in rows if row["base"] and constants.MEM2_LO <= int(row["base"]) < constants.MEM2_HI]
        for row in rows:
            print(
                f"  {row['label']:<5} ptr={_fmt_addr(row['ptr_addr'])} "
                f"base={_fmt_addr(row['base'])} char_id={row['char_id']} name={row['name']}"
            )
        ok_all = _status(bool(loaded_rows), "at least one loaded MEM2 fighter slot", f"loaded={len(loaded_rows)}") and ok_all
    except Exception as exc:
        rows = []
        loaded_rows = []
        ok_all = False
        _status(False, "slot pointer reads", repr(exc))

    try:
        self_checks = {
            256: move_id_map.lookup_move_name(256),
            257: move_id_map.lookup_move_name(257),
            430: move_id_map.lookup_move_name(430),
        }
        ok = self_checks.get(256) == "5A" and self_checks.get(257) == "5B" and self_checks.get(430) == "assist standby"
        ok_all = _status(ok, "move ID lookup", repr(self_checks)) and ok_all
    except Exception as exc:
        ok_all = False
        _status(False, "move ID lookup", repr(exc))

    for row in loaded_rows:
        label = str(row["label"])
        base = int(row["base"])
        try:
            chr_tbl = scan_normals_all.resolve_chr_tbl_from_live_memory(base)
            if chr_tbl:
                tbl_buf = scan_normals_all.read_and_validate_chr_tbl(chr_tbl)
                moves = scan_normals_all.parse_chr_tbl(tbl_buf or b"", chr_tbl - scan_normals_all.CHR_TBL_READ_PAD_BEFORE, chr_tbl) if tbl_buf else []
                _status(True, f"{label} chr_tbl", f"chr_tbl={_fmt_addr(chr_tbl)} entries={len(moves)}")
            else:
                ok_all = False
                _status(False, f"{label} chr_tbl", f"base={_fmt_addr(base)}")
        except Exception as exc:
            ok_all = False
            _status(False, f"{label} chr_tbl", repr(exc))

    if args.write_echo:
        try:
            addr = _parse_int(args.write_echo)
            before = dolphin_io.rd8(addr)
            if before is None:
                ok_all = False
                _status(False, "write echo read-before", f"addr={_fmt_addr(addr)}")
            else:
                wrote = bool(dolphin_io.wd8(addr, before))
                time.sleep(0.02)
                after = dolphin_io.rd8(addr)
                ok = wrote and after == before
                ok_all = _status(ok, "write echo", f"addr={_fmt_addr(addr)} value=0x{before:02X} after={after}") and ok_all
        except Exception as exc:
            ok_all = False
            _status(False, "write echo", repr(exc))
    else:
        print("[Dolphin smoke] Write echo skipped. Add --write-echo 0xADDRESS to test one safe write-back.")

    print("[Dolphin smoke] Result:", "PASS" if ok_all else "FAIL")
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
