from __future__ import annotations

import re
import threading
from typing import Any

try:
    from dolphin_io import rbytes, wd32, wbytes
except Exception:  # pragma: no cover
    rbytes = None
    wd32 = None
    wbytes = None

_LOCK = threading.RLock()

# Source-side selector lane confirmed by staged select-screen dumps.
# Do not touch loader strings or 0x804E98A8/AC. The working lane is the
# select-wheel roster table, before chr/<tag> request rows are built.
ROSTER_TABLE_BASE = 0x809BD0C4

# Live wheel table captured from the staged hover/select dumps.
# Address = 0x809BD0C4 + slot * 4, value = character id.
ROSTER_SLOT_TABLE: tuple[tuple[int, int, str], ...] = (
    (0x00, 0x01, "Ken the Eagle"),
    (0x01, 0x08, "Jun the Swan"),
    (0x02, 0x02, "Casshan"),
    (0x03, 0x03, "Tekkaman"),
    (0x04, 0x04, "Polimar"),
    (0x05, 0x05, "Yatterman-1"),
    (0x06, 0x0A, "Karas"),
    (0x07, 0x06, "Doronjo"),
    (0x08, 0x07, "Ippatsuman"),
    (0x09, 0x0B, "Gold Lightan"),
    (0x0A, 0x1A, "Tekkaman Blade"),
    (0x0B, 0x1B, "Joe the Condor"),
    (0x0C, 0x1C, "Yatterman-2"),
    (0x0D, 0x63, "Random"),
    (0x0E, 0x1D, "Zero"),
    (0x0F, 0x1E, "Frank West"),
    (0x10, 0x16, "PTX-40A"),
    (0x11, 0x11, "Viewtiful Joe"),
    (0x12, 0x14, "Saki"),
    (0x13, 0x13, "Roll"),
    (0x14, 0x15, "Soki"),
    (0x15, 0x12, "Volnutt"),
    (0x16, 0x0E, "Batsu"),
    (0x17, 0x10, "Alex"),
    (0x18, 0x0F, "Morrigan"),
    (0x19, 0x0D, "Chun-Li"),
    (0x1A, 0x0C, "Ryu"),
    # Experimental appended logical clone slots. These do not replace visible slots;
    # they are written after the stock 0x00..0x1A table and require the count bump.
    (0x1B, 0x17, "Yami 1 clone"),
    (0x1C, 0x18, "Yami 2 clone"),
    (0x1D, 0x19, "Yami 3 clone"),
)

CHAR_ID_TO_NAME: dict[int, str] = {
    0x01: "Ken the Eagle",
    0x02: "Casshan",
    0x03: "Tekkaman",
    0x04: "Polimar",
    0x05: "Yatterman-1",
    0x06: "Doronjo",
    0x07: "Ippatsuman",
    0x08: "Jun the Swan",
    0x0A: "Karas",
    0x0B: "Gold Lightan",
    0x0C: "Ryu",
    0x0D: "Chun-Li",
    0x0E: "Batsu",
    0x0F: "Morrigan",
    0x10: "Alex",
    0x11: "Viewtiful Joe",
    0x12: "Volnutt",
    0x13: "Roll",
    0x14: "Saki",
    0x15: "Soki",
    0x16: "PTX-40A",
    # Hidden / non-wheel in-game entries requested for roster-table swizzle tests.
    # Decimal IDs 23, 24, 25 = hex 0x17, 0x18, 0x19.
    0x17: "Yami 1",
    0x18: "Yami 2",
    0x19: "Yami 3",
    0x1A: "Tekkaman Blade",
    0x1B: "Joe the Condor",
    0x1C: "Yatterman-2",
    0x1D: "Zero",
    0x1E: "Frank West",
    0x63: "Random",
}

ROSTER_SELECTOR_ADDRS: tuple[tuple[int, str], ...] = (
    (0x809BCEA0, "cursor index A"),
    (0x809BCF2C, "cursor index B"),
    (0x809BCF1C, "hover char id A"),
    (0x809BCFC0, "hover char id B"),
    (0x809BD090, "selected/locked char id A"),
    (0x809BD098, "selected/locked or pending char id B"),
)

# Experimental logical append points for real extra Yami slots.
# The staged dumps show 0x809BD0C0 holds 0x1B, matching the 27 stock wheel slots.
# Writing 0x1E here and in the mirrored selector count fields is the first safe
# test for whether the wheel can walk past Ryu into slots 0x1B..0x1D.
ROSTER_COUNT_ADDRS: tuple[tuple[int, str], ...] = (
    (0x809BCEA4, "selector count A"),
    (0x809BCF3C, "selector count B"),
    (0x809BD0C0, "roster table count"),
)

YAMI_CLONE_SLOTS: tuple[tuple[int, int, str], ...] = (
    (0x1B, 0x17, "Yami 1"),
    (0x1C, 0x18, "Yami 2"),
    (0x1D, 0x19, "Yami 3"),
)

YAMI_CLONE_COUNT = 0x1E

# Experimental visual-shell alias. The loaded chrsel resource table has a
# hidden/locked silhouette label, but not select_yami/name_yami labels.
# If appended hidden IDs resolve through the silhouette slot, aliasing these
# strings to Zero gives the new Yami logical slots a cloned visible shell.
# This is string-table aliasing only; Yami remains the roster-table character ID.
VISUAL_ALIAS_STRINGS: tuple[tuple[int, bytes, bytes, str], ...] = (
    (0x930DEF50, b"select_sil", b"select_zer", "select_sil -> select_zer"),
    (0x930DE9E4, b"name_sil", b"name_zer", "name_sil -> name_zer"),
)

# Cursor/hover mirrors. Force-hover is intentionally separate from installing
# clone table/count because it is a stronger live-state nudge.
CURSOR_INDEX_ADDRS: tuple[int, ...] = (0x809BCEA0, 0x809BCF2C)
HOVER_CHAR_ID_ADDRS: tuple[int, ...] = (0x809BCF1C, 0x809BCFC0)

_SLOT_TO_ID = {slot: cid for slot, cid, _name in ROSTER_SLOT_TABLE}
_SLOT_TO_DEFAULT_NAME = {slot: name for slot, _cid, name in ROSTER_SLOT_TABLE}
_NAME_TO_ID = {name.lower(): cid for cid, name in CHAR_ID_TO_NAME.items()}
_NAME_TO_SLOT = {name.lower(): slot for slot, _cid, name in ROSTER_SLOT_TABLE}

_ROSTER_QUEUE: list[dict[str, Any]] = []
_ROSTER_ORIGINALS: dict[int, int] = {}
_ROSTER_BYTE_ORIGINALS: dict[int, bytes] = {}
_ROSTER_STATE: dict[str, Any] = {
    "last_error": "",
    "last_action": "",
    "last_snapshot": {},
    "queued": 0,
    "patches": 0,
    "restored": 0,
    "failed": 0,
    "restore_available": False,
    "clone_table_installed": False,
    "clone_count_installed": False,
    "last_clone_slot": "",
    "visual_alias_installed": False,
    "byte_restore_available": False,
}

_INT_RE = re.compile(r"0x[0-9a-fA-F]+|\b\d+\b")


def _fmt_hex(value: int, width: int = 2) -> str:
    return f"0x{int(value) & ((1 << (width * 4)) - 1):0{width}X}"


def _char_name(char_id: int | None) -> str:
    if char_id is None:
        return "unknown"
    return CHAR_ID_TO_NAME.get(int(char_id) & 0xFFFFFFFF, f"unknown {_fmt_hex(int(char_id) & 0xFFFFFFFF, 2)}")


def _char_label(char_id: int | None) -> str:
    if char_id is None:
        return "unknown"
    return f"{_char_name(char_id)} (ID {_fmt_hex(int(char_id) & 0xFF, 2)})"


def _slot_label(slot: int, char_id: int | None = None) -> str:
    slot_i = int(slot) & 0xFF
    default_id = _SLOT_TO_ID.get(slot_i)
    cid = int(default_id if char_id is None else char_id) & 0xFFFFFFFF
    default_name = _SLOT_TO_DEFAULT_NAME.get(slot_i, f"slot {_fmt_hex(slot_i, 2)}")
    return f"{default_name} slot {_fmt_hex(slot_i, 2)} (ID {_fmt_hex(cid & 0xFF, 2)})"


def _parse_first_int(text: str, default: int = 0) -> int:
    m = _INT_RE.search(str(text))
    if not m:
        return int(default) & 0xFFFFFFFF
    token = m.group(0)
    return int(token, 16 if token.lower().startswith("0x") else 10) & 0xFFFFFFFF


def _parse_int_value(value: Any, default: int = 0) -> int:
    try:
        text = str(value).strip().lower()
        if ":" in text:
            text = text.split(":", 1)[0].strip()
        return _parse_first_int(text, default)
    except Exception:
        return int(default) & 0xFFFFFFFF


def _parse_slot_value(value: Any, default: int = 0x1A) -> int:
    text = str(value).strip()
    lower = text.lower()

    # Preferred UI format contains "slot 0xNN".
    m = re.search(r"\bslot\s*(0x[0-9a-fA-F]+|\d+)\b", text, flags=re.IGNORECASE)
    if m:
        token = m.group(1)
        return int(token, 16 if token.lower().startswith("0x") else 10) & 0xFF

    # Support old format: "0x1A : Ryu wheel slot".
    m = re.match(r"\s*(0x[0-9a-fA-F]+|\d+)\b", text)
    if m:
        token = m.group(1)
        return int(token, 16 if token.lower().startswith("0x") else 10) & 0xFF

    # Support plain character names.
    for name, slot in _NAME_TO_SLOT.items():
        if name in lower:
            return int(slot) & 0xFF

    return int(default) & 0xFF


def _parse_char_id_value(value: Any, default: int = 0x0D) -> int:
    text = str(value).strip()
    lower = text.lower()

    # Preferred UI format contains "ID 0xNN".
    m = re.search(r"\bID\s*(0x[0-9a-fA-F]+|\d+)\b", text, flags=re.IGNORECASE)
    if m:
        token = m.group(1)
        return int(token, 16 if token.lower().startswith("0x") else 10) & 0xFFFFFFFF

    # Support old format: "0x0D : Chun-Li".
    m = re.match(r"\s*(0x[0-9a-fA-F]+|\d+)\b", text)
    if m:
        token = m.group(1)
        return int(token, 16 if token.lower().startswith("0x") else 10) & 0xFFFFFFFF

    # Support plain character names.
    for name, cid in _NAME_TO_ID.items():
        if name in lower:
            return int(cid) & 0xFFFFFFFF

    return int(default) & 0xFFFFFFFF


def _roster_addr_for_slot(slot_index: int) -> int:
    return int(ROSTER_TABLE_BASE + (int(slot_index) & 0xFF) * 4)


def _safe_read(addr: int, size: int) -> bytes:
    if rbytes is None:
        return b""
    try:
        data = rbytes(int(addr), int(size))
    except Exception:
        return b""
    if not data:
        return b""
    return bytes(data)


def _safe_read_u32be(addr: int) -> int | None:
    data = _safe_read(int(addr), 4)
    if not data or len(data) < 4:
        return None
    try:
        return int.from_bytes(data[:4], "big")
    except Exception:
        return None


def _safe_write_u32be(addr: int, value: int) -> bool:
    if wd32 is None:
        return False
    try:
        wd32(int(addr), int(value) & 0xFFFFFFFF)
        return True
    except Exception as e:
        with _LOCK:
            _ROSTER_STATE["last_error"] = repr(e)
        return False


def _safe_write_bytes(addr: int, data: bytes) -> bool:
    if wbytes is None:
        return False
    try:
        wbytes(int(addr), bytes(data))
        return True
    except Exception as e:
        with _LOCK:
            _ROSTER_STATE["last_error"] = repr(e)
        return False


def _read_roster_selector_snapshot() -> dict[str, Any]:
    fields: list[dict[str, Any]] = []
    for addr, label in ROSTER_SELECTOR_ADDRS:
        value = _safe_read_u32be(addr)
        item = {
            "addr": f"0x{addr:08X}",
            "label": label,
            "value": (f"0x{value:08X}" if value is not None else ""),
            "display": "",
        }
        if value is not None and "char id" in label:
            item["display"] = _char_label(value)
        elif value is not None and "selected" in label:
            item["display"] = _char_label(value)
        elif value is not None and "cursor index" in label:
            slot = int(value) & 0xFF
            cid = _SLOT_TO_ID.get(slot)
            item["display"] = _slot_label(slot, cid) if cid is not None else f"slot {_fmt_hex(slot, 2)}"
        fields.append(item)

    counts: list[dict[str, Any]] = []
    for addr, label in ROSTER_COUNT_ADDRS:
        value = _safe_read_u32be(addr)
        counts.append({
            "addr": f"0x{addr:08X}",
            "label": label,
            "value": (f"0x{int(value):08X}" if value is not None else ""),
            "is_clone_count": bool(value == YAMI_CLONE_COUNT),
        })

    table: list[dict[str, Any]] = []
    for slot, default_cid, default_name in ROSTER_SLOT_TABLE:
        addr = _roster_addr_for_slot(slot)
        value = _safe_read_u32be(addr)
        table.append({
            "slot": f"0x{slot:02X}",
            "slot_i": slot,
            "addr": f"0x{addr:08X}",
            "default_name": default_name,
            "default_char_id": f"0x{default_cid:02X}",
            "default_label": _char_label(default_cid),
            "char_id": (f"0x{value & 0xFF:02X}" if value is not None else ""),
            "char_label": (_char_label(value) if value is not None else ""),
            "patched": bool(value is not None and int(value) != int(default_cid)),
        })

    hover_idx = _safe_read_u32be(0x809BCEA0)
    hover_slot = int(hover_idx) if hover_idx is not None else None
    hover_slot_addr = _roster_addr_for_slot(hover_slot) if hover_slot is not None and 0 <= hover_slot <= 0x40 else 0
    hover_slot_value = _safe_read_u32be(hover_slot_addr) if hover_slot_addr else None
    hover_default_id = _SLOT_TO_ID.get(hover_slot or -1)

    return {
        "fields": fields,
        "counts": counts,
        "table": table,
        "hover_index": (f"0x{int(hover_idx):02X}" if hover_idx is not None else ""),
        "hover_slot_addr": (f"0x{hover_slot_addr:08X}" if hover_slot_addr else ""),
        "hover_slot_default": (_char_label(hover_default_id) if hover_default_id is not None else ""),
        "hover_slot_value": (f"0x{int(hover_slot_value) & 0xFF:02X}" if hover_slot_value is not None else ""),
        "hover_slot_label": (_char_label(hover_slot_value) if hover_slot_value is not None else ""),
        "roster_base": f"0x{ROSTER_TABLE_BASE:08X}",
    }


def get_roster_slot_choices() -> list[str]:
    return [f"{name} slot 0x{slot:02X} (ID 0x{cid:02X})" for slot, cid, name in ROSTER_SLOT_TABLE]


def get_roster_char_choices() -> list[str]:
    # Visible wheel characters first, in roster-table order. Then append any
    # known non-wheel / hidden IDs so they can be swiped into existing slots.
    seen: set[int] = set()
    out: list[str] = []
    for _slot, cid, _name in ROSTER_SLOT_TABLE:
        if cid in seen:
            continue
        seen.add(cid)
        out.append(_char_label(cid))
    for cid in sorted(CHAR_ID_TO_NAME):
        if cid in seen:
            continue
        seen.add(cid)
        out.append(_char_label(cid))
    return out


def queue_roster_snapshot() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "snapshot"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "selector snapshot queued"
    return {"ok": True, "queued": True}


def queue_roster_patch_slot(slot_index: Any = 0x1A, target_char_id: Any = 0x0D) -> dict[str, Any]:
    slot = _parse_slot_value(slot_index, 0x1A) & 0xFF
    target = _parse_char_id_value(target_char_id, 0x0D) & 0xFFFFFFFF
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "patch_slot", "slot": slot, "target": target})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = f"patch queued {_slot_label(slot)} -> {_char_label(target)}"
    return {"ok": True, "queued": True, "slot": f"0x{slot:02X}", "slot_label": _slot_label(slot), "target": f"0x{target:08X}", "target_label": _char_label(target)}


def queue_roster_patch_current_hover(target_char_id: Any = 0x0D) -> dict[str, Any]:
    target = _parse_char_id_value(target_char_id, 0x0D) & 0xFFFFFFFF
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "patch_current", "target": target})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = f"patch current hover queued -> {_char_label(target)}"
    return {"ok": True, "queued": True, "target": f"0x{target:08X}", "target_label": _char_label(target)}


def queue_yami_clone_table() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_clone_table"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Yami clone table install queued"
    return {"ok": True, "queued": True}


def queue_yami_clone_count() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_clone_count"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Yami clone count bump queued"
    return {"ok": True, "queued": True}


def queue_yami_clone_install_all() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_clone_all"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Yami clone table + count queued"
    return {"ok": True, "queued": True}


def queue_yami_visual_alias() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_visual_alias"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Yami Zero visual alias queued"
    return {"ok": True, "queued": True}


def queue_yami_shell_attempt() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_shell_attempt"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "Yami shell attempt queued"
    return {"ok": True, "queued": True}


def queue_yami_force_hover(slot_index: Any = 0x1B) -> dict[str, Any]:
    slot = _parse_slot_value(slot_index, 0x1B) & 0xFF
    cid = _SLOT_TO_ID.get(slot, 0x17)
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "yami_force_hover", "slot": slot, "target": cid})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = f"Yami force-hover queued {_slot_label(slot, cid)}"
    return {"ok": True, "queued": True, "slot": f"0x{slot:02X}", "target_label": _char_label(cid)}


def queue_roster_restore() -> dict[str, Any]:
    with _LOCK:
        _ROSTER_QUEUE.append({"op": "restore"})
        _ROSTER_STATE["queued"] = len(_ROSTER_QUEUE)
        _ROSTER_STATE["last_action"] = "restore queued"
    return {"ok": True, "queued": True}


def _remember_original(addr: int, value: int | None = None) -> int | None:
    addr_i = int(addr) & 0xFFFFFFFF
    if value is None:
        value = _safe_read_u32be(addr_i)
    if value is None:
        return None
    if addr_i not in _ROSTER_ORIGINALS:
        _ROSTER_ORIGINALS[addr_i] = int(value) & 0xFFFFFFFF
    return int(value) & 0xFFFFFFFF


def _write_saved(addr: int, value: int) -> bool:
    original = _remember_original(addr)
    if original is None:
        return False
    return _safe_write_u32be(addr, value)


def _remember_original_bytes(addr: int, size: int) -> bytes | None:
    addr_i = int(addr) & 0xFFFFFFFF
    data = _safe_read(addr_i, int(size))
    if not data or len(data) < int(size):
        return None
    if addr_i not in _ROSTER_BYTE_ORIGINALS:
        _ROSTER_BYTE_ORIGINALS[addr_i] = bytes(data[: int(size)])
    return bytes(data[: int(size)])


def _write_bytes_saved(addr: int, data: bytes, expected: bytes | None = None) -> bool:
    addr_i = int(addr) & 0xFFFFFFFF
    payload = bytes(data)
    original = _remember_original_bytes(addr_i, len(payload))
    if original is None:
        return False
    if expected is not None and not original.startswith(bytes(expected)):
        with _LOCK:
            _ROSTER_STATE["last_error"] = (
                f"visual alias expected {bytes(expected)!r} at 0x{addr_i:08X}, "
                f"found {original!r}"
            )
        return False
    return _safe_write_bytes(addr_i, payload)


def _install_yami_clone_table() -> tuple[int, int]:
    wrote = 0
    failed = 0
    for slot, cid, _name in YAMI_CLONE_SLOTS:
        addr = _roster_addr_for_slot(slot)
        if _write_saved(addr, cid):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["clone_table_installed"] = failed == 0
    return wrote, failed


def _install_yami_clone_count() -> tuple[int, int]:
    wrote = 0
    failed = 0
    for addr, _label in ROSTER_COUNT_ADDRS:
        if _write_saved(addr, YAMI_CLONE_COUNT):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["clone_count_installed"] = failed == 0
    return wrote, failed


def _install_yami_visual_alias() -> tuple[int, int]:
    wrote = 0
    failed = 0
    for addr, expected, replacement, _label in VISUAL_ALIAS_STRINGS:
        if _write_bytes_saved(addr, replacement, expected=expected):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["visual_alias_installed"] = failed == 0
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
    return wrote, failed


def _force_yami_hover(slot: int, target: int) -> tuple[int, int]:
    wrote = 0
    failed = 0
    for addr in CURSOR_INDEX_ADDRS:
        if _write_saved(addr, int(slot) & 0xFF):
            wrote += 1
        else:
            failed += 1
    for addr in HOVER_CHAR_ID_ADDRS:
        if _write_saved(addr, int(target) & 0xFFFFFFFF):
            wrote += 1
        else:
            failed += 1
    with _LOCK:
        _ROSTER_STATE["last_clone_slot"] = _slot_label(slot, target)
    return wrote, failed


def _do_restore() -> dict[str, int]:
    restored = 0
    failed = 0

    byte_originals = dict(_ROSTER_BYTE_ORIGINALS)
    for addr, data in byte_originals.items():
        if _safe_write_bytes(int(addr), bytes(data)):
            restored += 1
        else:
            failed += 1

    originals = dict(_ROSTER_ORIGINALS)
    for addr, value in originals.items():
        if _safe_write_u32be(int(addr), int(value)):
            restored += 1
        else:
            failed += 1

    if failed == 0:
        _ROSTER_ORIGINALS.clear()
        _ROSTER_BYTE_ORIGINALS.clear()
        with _LOCK:
            _ROSTER_STATE["clone_table_installed"] = False
            _ROSTER_STATE["clone_count_installed"] = False
            _ROSTER_STATE["visual_alias_installed"] = False
            _ROSTER_STATE["byte_restore_available"] = False
            _ROSTER_STATE["last_clone_slot"] = ""
    with _LOCK:
        _ROSTER_STATE["restored"] = int(_ROSTER_STATE.get("restored", 0) or 0) + restored
        _ROSTER_STATE["failed"] = int(_ROSTER_STATE.get("failed", 0) or 0) + failed
        _ROSTER_STATE["restore_available"] = bool(_ROSTER_ORIGINALS or _ROSTER_BYTE_ORIGINALS)
        _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
        _ROSTER_STATE["last_action"] = f"restore done restored={restored} failed={failed}"
        _ROSTER_STATE["last_error"] = "" if failed == 0 else f"restore failed for {failed} address(es)"
        try:
            _ROSTER_STATE["last_snapshot"] = _read_roster_selector_snapshot()
        except Exception:
            pass
    return {"restored": restored, "failed": failed}


def _tick_roster_actions() -> None:
    with _LOCK:
        actions = list(_ROSTER_QUEUE)
        _ROSTER_QUEUE.clear()
        _ROSTER_STATE["queued"] = 0
    if not actions:
        return

    for action in actions:
        op = str(action.get("op") or "")
        try:
            if op == "snapshot":
                snap = _read_roster_selector_snapshot()
                with _LOCK:
                    _ROSTER_STATE["last_snapshot"] = snap
                    _ROSTER_STATE["last_action"] = "selector snapshot captured"
                    _ROSTER_STATE["last_error"] = ""
                continue

            if op == "restore":
                _do_restore()
                continue

            if op in (
                "yami_clone_table",
                "yami_clone_count",
                "yami_clone_all",
                "yami_force_hover",
                "yami_visual_alias",
                "yami_shell_attempt",
            ):
                wrote = 0
                failed = 0
                if op in ("yami_clone_table", "yami_clone_all", "yami_shell_attempt"):
                    w, f = _install_yami_clone_table()
                    wrote += w
                    failed += f
                if op in ("yami_clone_count", "yami_clone_all", "yami_shell_attempt"):
                    w, f = _install_yami_clone_count()
                    wrote += w
                    failed += f
                if op in ("yami_visual_alias", "yami_shell_attempt"):
                    w, f = _install_yami_visual_alias()
                    wrote += w
                    failed += f
                if op == "yami_force_hover":
                    slot = int(action.get("slot", 0x1B)) & 0xFF
                    target = int(action.get("target", _SLOT_TO_ID.get(slot, 0x17))) & 0xFFFFFFFF
                    w, f = _force_yami_hover(slot, target)
                    wrote += w
                    failed += f
                snap = _read_roster_selector_snapshot()
                with _LOCK:
                    _ROSTER_STATE["patches"] = int(_ROSTER_STATE.get("patches", 0) or 0) + wrote
                    _ROSTER_STATE["failed"] = int(_ROSTER_STATE.get("failed", 0) or 0) + failed
                    _ROSTER_STATE["restore_available"] = bool(_ROSTER_ORIGINALS or _ROSTER_BYTE_ORIGINALS)
                    _ROSTER_STATE["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
                    _ROSTER_STATE["last_action"] = f"{op} done wrote={wrote} failed={failed}"
                    _ROSTER_STATE["last_error"] = "" if failed == 0 else f"{op} failed for {failed} write(s)"
                    _ROSTER_STATE["last_snapshot"] = snap
                continue

            if op == "patch_current":
                idx = _safe_read_u32be(0x809BCEA0)
                if idx is None or not (0 <= int(idx) <= 0x40):
                    raise RuntimeError("could not read sane current hover index")
                slot = int(idx) & 0xFF
                target = int(action.get("target", 0x0D)) & 0xFFFFFFFF
                addr = _roster_addr_for_slot(slot)
            elif op == "patch_slot":
                slot = int(action.get("slot", 0x1A)) & 0xFF
                target = int(action.get("target", 0x0D)) & 0xFFFFFFFF
                addr = _roster_addr_for_slot(slot)
            else:
                continue

            original = _safe_read_u32be(addr)
            if original is None:
                raise RuntimeError(f"read failed at 0x{addr:08X}")
            if addr not in _ROSTER_ORIGINALS:
                _ROSTER_ORIGINALS[addr] = int(original)
            if not _safe_write_u32be(addr, target):
                raise RuntimeError(f"write failed at 0x{addr:08X}")

            snap = _read_roster_selector_snapshot()
            with _LOCK:
                _ROSTER_STATE["patches"] = int(_ROSTER_STATE.get("patches", 0) or 0) + 1
                _ROSTER_STATE["restore_available"] = bool(_ROSTER_ORIGINALS)
                _ROSTER_STATE["last_action"] = (
                    f"patched {_slot_label(slot, original)} at 0x{addr:08X}: "
                    f"{_char_label(original)} -> {_char_label(target)}"
                )
                _ROSTER_STATE["last_error"] = ""
                _ROSTER_STATE["last_snapshot"] = snap
        except Exception as e:
            with _LOCK:
                _ROSTER_STATE["failed"] = int(_ROSTER_STATE.get("failed", 0) or 0) + 1
                _ROSTER_STATE["last_error"] = repr(e)
                _ROSTER_STATE["last_action"] = f"{op} failed"


def get_roster_patch_state() -> dict[str, Any]:
    with _LOCK:
        state = dict(_ROSTER_STATE)
        state["restore_available"] = bool(_ROSTER_ORIGINALS or _ROSTER_BYTE_ORIGINALS)
        state["byte_restore_available"] = bool(_ROSTER_BYTE_ORIGINALS)
        state["originals"] = {
            f"0x{k:08X}": _char_label(v) for k, v in _ROSTER_ORIGINALS.items()
        }
        state["byte_originals"] = {
            f"0x{k:08X}": bytes(v).hex(" ") for k, v in _ROSTER_BYTE_ORIGINALS.items()
        }
        state["roster_slots"] = get_roster_slot_choices()
        state["target_chars"] = get_roster_char_choices()
        state["roster_base"] = f"0x{ROSTER_TABLE_BASE:08X}"
        state["clone_slots"] = [
            f"{name} clone slot 0x{slot:02X} (ID 0x{cid:02X})"
            for slot, cid, name in YAMI_CLONE_SLOTS
        ]
        state["clone_count"] = f"0x{YAMI_CLONE_COUNT:02X}"
        state["count_addrs"] = {f"0x{addr:08X}": label for addr, label in ROSTER_COUNT_ADDRS}
        state["visual_alias_strings"] = [
            {"addr": f"0x{addr:08X}", "from": old.decode("ascii"), "to": new.decode("ascii"), "label": label}
            for addr, old, new, label in VISUAL_ALIAS_STRINGS
        ]
    return state


def tick_char_test() -> None:
    _tick_roster_actions()


def start_char_test(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {"ok": False, "disabled": True, "error": "Only roster table patch is enabled in this build."}


def stop_char_test() -> dict[str, Any]:
    return {"ok": True, "running": False}


def restore_char_test() -> dict[str, Any]:
    result = _do_restore()
    return {"ok": result.get("failed", 0) == 0, "roster_restore": result}


def get_char_test_state() -> dict[str, Any]:
    return {
        "running": False,
        "mode": "roster_table_patch_only",
        "samples": 0,
        "changes": 0,
        "last_error": "",
        "roster_patch": get_roster_patch_state(),
    }
