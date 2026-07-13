from __future__ import annotations

import threading
import time
from typing import Optional

from tvcgui.features.frame_data.patterns import ATTACK_PROPERTY_VALUES, find_attack_property_addr
from tvcgui.platform.dolphin import rbytes
from tvcgui.tools.scanners.normal_scanner import (
    CHR_TBL_READ_PAD_BEFORE,
    parse_chr_tbl_entries,
    read_and_validate_chr_tbl,
    resolve_chr_tbl_from_live_memory,
)

_LOCK = threading.RLock()
_TABLE_ENTRIES_BY_ROOT: dict[int, dict[int, int]] = {}
_PROPERTY_BY_ACTION: dict[tuple[int, int], int] = {}
_FAILED_UNTIL: dict[tuple[int, int], float] = {}


def _known_property(value) -> Optional[int]:
    try:
        parsed = int(value) & 0xFF
    except Exception:
        return None
    return parsed if parsed in ATTACK_PROPERTY_VALUES else None


def _table_entries(chr_tbl_abs: int) -> dict[int, int]:
    root = int(chr_tbl_abs or 0)
    if not root:
        return {}
    with _LOCK:
        cached = _TABLE_ENTRIES_BY_ROOT.get(root)
        if cached is not None:
            return cached

    buf = read_and_validate_chr_tbl(root)
    if not buf:
        return {}
    start = root - CHR_TBL_READ_PAD_BEFORE
    entries = {
        int(action_id): int(move_abs)
        for action_id, move_abs in parse_chr_tbl_entries(buf, start, root)
    }
    with _LOCK:
        _TABLE_ENTRIES_BY_ROOT[root] = entries
    return entries


def resolve_live_attack_property(
    fighter_base_abs: int,
    action_id: int,
    *,
    chr_tbl_abs: int | None = None,
) -> Optional[int]:
    """Resolve the exact live action's attack-property byte using the tree path.

    The action ID is looked up in the fighter's live character table, then the
    same packet locator used by the frame-data tree reads that action root. Only
    successful known properties are cached. Transient failures retry shortly.
    """
    try:
        base = int(fighter_base_abs or 0)
        action = int(action_id)
    except Exception:
        return None
    if not base or action < 0:
        return None

    root = int(chr_tbl_abs or 0)
    if not root:
        try:
            root = int(resolve_chr_tbl_from_live_memory(base) or 0)
        except Exception:
            root = 0
    if not root:
        return None

    key = (root, action)
    with _LOCK:
        known = _PROPERTY_BY_ACTION.get(key)
        if known is not None:
            return known
        if time.monotonic() < float(_FAILED_UNTIL.get(key, 0.0) or 0.0):
            return None

    move_abs = _table_entries(root).get(action)
    if not move_abs:
        with _LOCK:
            _FAILED_UNTIL[key] = time.monotonic() + 0.50
        return None

    try:
        _addr, value, _context = find_attack_property_addr(move_abs, rbytes)
    except Exception:
        value = None
    parsed = _known_property(value)
    with _LOCK:
        if parsed is not None:
            _PROPERTY_BY_ACTION[key] = parsed
            _FAILED_UNTIL.pop(key, None)
        else:
            _FAILED_UNTIL[key] = time.monotonic() + 0.50
    return parsed


def clear_attack_property_runtime_cache() -> None:
    with _LOCK:
        _TABLE_ENTRIES_BY_ROOT.clear()
        _PROPERTY_BY_ACTION.clear()
        _FAILED_UNTIL.clear()
