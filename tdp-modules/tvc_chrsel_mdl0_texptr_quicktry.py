from __future__ import annotations

import sys
import struct

try:
    import dolphin_io
except Exception as exc:
    print(f"[FAIL] Could not import dolphin_io.py from this folder: {exc!r}")
    raise SystemExit(1)

HEADER_FIELD_OFF = 0x420
MAT_SIZE = 0x5E0

# mat_addr, mat_index, stock_tex0, target_tex0, label
HEADER_ROWS = (
    # Material headers: real, but previous test showed these alone are not enough.
    (0x92D23000, 0x1B, 0x92D2F6A0, 0x92D2EDA0, "1015 header B27 / slot 0x1B -> icon_fra"),
    (0x92D235E0, 0x1C, 0x92D33D20, 0x92D33660, "1015 header B28 / slot 0x1C -> icon_tkb"),
    (0x92D23BC0, 0x1D, 0x92D343E0, 0x92D33D20, "1015 header B29 / slot 0x1D -> icon_ya2"),
    (0x932E6FC0, 0x1B, 0x932F37E0, 0x932F2EE0, "1016 header B27 / slot 0x1B -> icon_fra"),
    (0x932E75A0, 0x1C, 0x932F7E60, 0x932F77A0, "1016 header B28 / slot 0x1C -> icon_tkb"),
    (0x932E7B80, 0x1D, 0x932F8520, 0x932F7E60, "1016 header B29 / slot 0x1D -> icon_ya2"),
)

# ptr_addr, stock_tex0, target_tex0, label
BINDING_ROWS = (
    # Dump-corrected MDL0 material dictionary texture-binding records.
    # 1015 material dict entries 28/29/30 = thumbnail_0622_B27/B28/B29, pointer at binding +0x2C.
    (0x92D21CA0, 0x92D32FA0, 0x92D2EDA0, "1015 binding B27 / slot 0x1B -> icon_fra"),
    (0x92D22280, 0x92D331E0, 0x92D33660, "1015 binding B28 / slot 0x1C -> icon_tkb"),
    (0x92D22860, 0x92D33420, 0x92D33D20, "1015 binding B29 / slot 0x1D -> icon_ya2"),
    # 1016 mirror entries use the same names, but the live pointer lands at binding +0x6C.
    (0x932E5C60, 0x932F70E0, 0x932F2EE0, "1016 binding B27 / slot 0x1B -> icon_fra"),
    (0x932E6240, 0x932F7320, 0x932F77A0, "1016 binding B28 / slot 0x1C -> icon_tkb"),
    (0x932E6820, 0x932F7560, 0x932F7E60, "1016 binding B29 / slot 0x1D -> icon_ya2"),
)


def rd32(addr: int) -> int | None:
    data = dolphin_io.rbytes(addr, 4)
    if not data or len(data) != 4:
        return None
    return struct.unpack(">I", data)[0]


def wr32(addr: int, value: int) -> bool:
    if hasattr(dolphin_io, "wd32") and dolphin_io.wd32 is not None:
        try:
            dolphin_io.wd32(addr, value & 0xFFFFFFFF)
            return True
        except Exception:
            pass
    return bool(dolphin_io.wbytes(addr, struct.pack(">I", value & 0xFFFFFFFF)))


def fmt(v: int | None) -> str:
    return "<read-fail>" if v is None else f"0x{v:08X}"


def audit() -> int:
    print("[audit] MDL0 thumbnail TEX0 pointer quicktry v2")
    bad = 0
    print("[header +0x420 layer]")
    for mat_addr, mat_index, stock, target, label in HEADER_ROWS:
        size = rd32(mat_addr)
        idx = rd32(mat_addr + 0x0C)
        cur = rd32(mat_addr + HEADER_FIELD_OFF)
        ok_struct = size == MAT_SIZE and idx == mat_index
        state = "target" if cur == target else "stock" if cur == stock else "other"
        print(
            f"  {label}: mat=0x{mat_addr:08X} size={fmt(size)} idx={fmt(idx)} "
            f"ptr@+420={fmt(cur)} state={state} struct_ok={ok_struct}"
        )
        if not ok_struct or state == "other":
            bad += 1
    print("[dict binding layer]")
    for ptr_addr, stock, target, label in BINDING_ROWS:
        cur = rd32(ptr_addr)
        state = "target" if cur == target else "stock" if cur == stock else "other"
        print(f"  {label}: ptr=0x{ptr_addr:08X} cur={fmt(cur)} state={state}")
        if state == "other":
            bad += 1
    return bad


def apply_restore(apply: bool) -> int:
    action = "apply" if apply else "restore"
    print(f"[{action}] hooking Dolphin...")
    dolphin_io.hook()
    print(f"[{action}] hooked")
    bad = 0
    wrote = 0

    for mat_addr, mat_index, stock, target, label in HEADER_ROWS:
        wanted = target if apply else stock
        allowed = stock if apply else target
        size = rd32(mat_addr)
        idx = rd32(mat_addr + 0x0C)
        cur = rd32(mat_addr + HEADER_FIELD_OFF)
        if size != MAT_SIZE or idx != mat_index:
            print(f"  [SKIP] {label}: header sanity failed size={fmt(size)} idx={fmt(idx)}")
            bad += 1
            continue
        if cur == wanted:
            print(f"  [OK]    {label}: already {fmt(wanted)}")
            continue
        if cur != allowed:
            print(f"  [SKIP]  {label}: unexpected current pointer {fmt(cur)}; expected {fmt(allowed)}")
            bad += 1
            continue
        if wr32(mat_addr + HEADER_FIELD_OFF, wanted):
            print(f"  [WRITE] {label}: {fmt(cur)} -> {fmt(wanted)}")
            wrote += 1
        else:
            print(f"  [FAIL]  {label}: write failed")
            bad += 1

    for ptr_addr, stock, target, label in BINDING_ROWS:
        wanted = target if apply else stock
        allowed = stock if apply else target
        cur = rd32(ptr_addr)
        if cur == wanted:
            print(f"  [OK]    {label}: already {fmt(wanted)}")
            continue
        if cur != allowed:
            print(f"  [SKIP]  {label}: unexpected current pointer {fmt(cur)}; expected {fmt(allowed)}")
            bad += 1
            continue
        if wr32(ptr_addr, wanted):
            print(f"  [WRITE] {label}: {fmt(cur)} -> {fmt(wanted)}")
            wrote += 1
        else:
            print(f"  [FAIL]  {label}: write failed")
            bad += 1

    print(f"[{action}] wrote={wrote} problems={bad}")
    print("[post-audit]")
    bad += audit()
    return 0 if bad == 0 else 2


def main() -> int:
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "apply"
    if mode in {"apply", "on", "patch"}:
        return apply_restore(True)
    if mode in {"restore", "off", "undo"}:
        return apply_restore(False)
    if mode in {"audit", "check"}:
        dolphin_io.hook()
        return audit()
    print("Usage: python tvc_chrsel_mdl0_texptr_quicktry.py [apply|restore|audit]")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
