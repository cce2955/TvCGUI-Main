"""Frame-data slot identity guards.

The editable frame-data cache is per *live fighter table*, not merely per HUD
slot. A slot label such as ``P1-C1`` can contain Ryu in one match and Jun in
the next, and a character can receive a new table allocation after a rematch.

This module is intentionally dependency-free so the rules can be unit tested
without Dolphin, pygame, or Tk.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional


def _as_positive_int(value: Any) -> Optional[int]:
    try:
        out = int(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _first_positive(mapping: Mapping[str, Any] | None, keys: Iterable[str]) -> Optional[int]:
    if not isinstance(mapping, Mapping):
        return None
    for key in keys:
        value = _as_positive_int(mapping.get(key))
        if value is not None:
            return value
    return None


def row_for_slot(rows: Iterable[Mapping[str, Any]] | None, slot_label: str) -> Optional[Mapping[str, Any]]:
    """Return the single scan row for a HUD slot, if present."""
    label = str(slot_label or "")
    for row in rows or ():
        if isinstance(row, Mapping) and str(row.get("slot_label") or row.get("slot") or "") == label:
            return row
    return None


def binding_from_row(row: Mapping[str, Any] | None, *, source: str = "row") -> dict[str, Any]:
    """Build a normalized identity from a scan row."""
    row = row if isinstance(row, Mapping) else {}
    return {
        "slot_label": str(row.get("slot_label") or row.get("slot") or ""),
        "char_id": _first_positive(row, ("char_id", "csv_char_id", "id")),
        "chr_tbl_abs": _first_positive(row, ("chr_tbl_abs", "chr_tbl", "table_base")),
        "fighter_base_abs": _first_positive(row, ("fighter_base_abs", "fighter_base", "slot_base")),
        "profile_key": str(row.get("profile_key") or ""),
        "source": source,
    }


def binding_from_snapshot(slot_label: str, snap: Mapping[str, Any] | None) -> dict[str, Any]:
    """Build the immediate, HUD-side identity for a clicked Frame Data button."""
    snap = snap if isinstance(snap, Mapping) else {}
    return {
        "slot_label": str(slot_label or ""),
        "char_id": _first_positive(snap, ("id", "csv_char_id", "char_id")),
        "chr_tbl_abs": None,
        "fighter_base_abs": _first_positive(snap, ("base", "fighter_base_abs", "fighter_base")),
        "profile_key": "",
        "source": "hud",
    }


def live_binding(
    slot_label: str,
    preview_rows: Iterable[Mapping[str, Any]] | None,
    snap: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the current clicked-fighter identity.

    A preview row is considered live only when it agrees with the HUD's current
    character ID. Its table address then becomes the mandatory proof that an
    editable cache row belongs to this exact match allocation.

    If the preview has not refreshed yet, the returned binding is deliberately
    *unproven*. Callers must display/loading-refresh instead of opening an older
    same-slot workbench row.
    """
    hud = binding_from_snapshot(slot_label, snap)
    preview = binding_from_row(row_for_slot(preview_rows, slot_label), source="preview")

    if hud["char_id"] is None:
        return hud

    if preview["char_id"] != hud["char_id"]:
        return hud

    if preview["chr_tbl_abs"] is None:
        return hud

    return {
        "slot_label": str(slot_label or ""),
        "char_id": hud["char_id"],
        "chr_tbl_abs": preview["chr_tbl_abs"],
        "fighter_base_abs": preview["fighter_base_abs"],
        "profile_key": preview["profile_key"],
        "source": "preview",
    }


def is_live_proven(binding: Mapping[str, Any] | None) -> bool:
    """True only when a current preview verified the character table."""
    return bool(
        isinstance(binding, Mapping)
        and str(binding.get("source") or "") == "preview"
        and _as_positive_int(binding.get("char_id")) is not None
        and _as_positive_int(binding.get("chr_tbl_abs")) is not None
    )


def binding_matches(expected: Mapping[str, Any] | None, candidate_row: Mapping[str, Any] | None) -> bool:
    """Return whether an editable cache row belongs to the exact clicked fighter.

    Slot label and character ID must always match. A proven live table identity
    must match too. This intentionally rejects unknown/unproven identities:
    opening a loading shell is correct; opening Ryu's cached editor for Jun is
    not.
    """
    if not isinstance(expected, Mapping) or not isinstance(candidate_row, Mapping):
        return False

    actual = binding_from_row(candidate_row, source="candidate")
    if not str(expected.get("slot_label") or ""):
        return False
    if actual["slot_label"] != str(expected.get("slot_label") or ""):
        return False

    expected_char = _as_positive_int(expected.get("char_id"))
    if expected_char is None or actual["char_id"] != expected_char:
        return False

    expected_table = _as_positive_int(expected.get("chr_tbl_abs"))
    if expected_table is None:
        return False
    if actual["chr_tbl_abs"] != expected_table:
        return False

    # If both sides record the exact live fighter pointer, keep that additional
    # match boundary too. Do not require it because older cached rows did not
    # persist this field.
    expected_base = _as_positive_int(expected.get("fighter_base_abs"))
    actual_base = actual["fighter_base_abs"]
    if expected_base is not None and actual_base is not None and expected_base != actual_base:
        return False

    return True


def editable_row_for_live_slot(
    slot_label: str,
    workbench_rows: Iterable[Mapping[str, Any]] | None,
    preview_rows: Iterable[Mapping[str, Any]] | None,
    snap: Mapping[str, Any] | None,
) -> Optional[Mapping[str, Any]]:
    """Return a safe workbench row for a clicked slot or ``None``."""
    expected = live_binding(slot_label, preview_rows, snap)
    if not is_live_proven(expected):
        return None
    candidate = row_for_slot(workbench_rows, slot_label)
    return candidate if binding_matches(expected, candidate) else None
