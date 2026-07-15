from __future__ import annotations

import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple


GROUND_NORMAL_LADDER: Tuple[str, ...] = (
    "5A",
    "2A",
    "5B",
    "2B",
    "5C",
    "2C",
)
GROUND_NORMAL_RANK = {name: index for index, name in enumerate(GROUND_NORMAL_LADDER)}
TERMINAL_COMMAND_NORMALS = {"3C", "6B"}
NORMAL_EXCEPTIONS = {
    "5C": {"3C"},
}

# These IDs are shared by the standard TvC action layout and are a more
# reliable notation source than scanner labels such as "Second" or helper rows.
ACTION_NOTATION = {
    0x0100: "5A",
    0x0101: "5B",
    0x0102: "5C",
    0x0103: "2A",
    0x0104: "2B",
    0x0105: "2C",
    0x0106: "6C",
    0x0108: "3C",
    0x0109: "j.A",
    0x010A: "j.B",
    0x010B: "j.C",
    0x010E: "6B",
}


_STATUS_ORDER = {"ALLOWED": 0, "ELIGIBLE": 1, "BLOCKED": 2, "UNKNOWN": 3}


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except Exception:
        return None


def display_name(move: Dict[str, Any], char_name: str = "") -> str:
    for key in ("move_name", "pretty_name", "name", "label", "family_group_label", "family_label"):
        value = str(move.get(key) or "").strip()
        if value:
            return value
    aid = _as_int(move.get("id"))
    if aid is not None:
        notation = ACTION_NOTATION.get(aid)
        if notation:
            return notation
        return f"Action 0x{aid:04X}"
    address = _as_int(move.get("abs"))
    return f"Move 0x{address:08X}" if address else "Unknown move"


def _name_text(move: Dict[str, Any]) -> str:
    values = []
    for key in (
        "move_name",
        "pretty_name",
        "name",
        "label",
        "family_group_label",
        "family_label",
        "family_link_label",
    ):
        value = move.get(key)
        if value:
            values.append(str(value))
    return " ".join(values)


def notation_for_move(move: Dict[str, Any]) -> str:
    aid = _as_int(move.get("id"))
    if aid in ACTION_NOTATION:
        return ACTION_NOTATION[aid]

    text = _name_text(move)
    low = text.lower()
    compact = re.sub(r"[\s_.\-]+", "", low)

    air_match = re.search(r"(?:^|[^a-z0-9])j[.\s_-]*([abc])(?:$|[^a-z0-9])", low)
    if air_match:
        return f"j.{air_match.group(1).upper()}"

    ground_match = re.search(r"(?:^|[^0-9])([23456])\s*[.\s_-]*([abc])(?:$|[^a-z0-9])", low)
    if ground_match:
        return f"{ground_match.group(1)}{ground_match.group(2).upper()}"

    for token in ("5a", "2a", "5b", "2b", "5c", "2c", "3c", "6b", "6c", "4c"):
        if compact.startswith(token):
            return token.upper()
    return ""


def move_kind(move: Dict[str, Any]) -> str:
    kind = str(move.get("kind") or "").strip().lower()
    text = _name_text(move).lower()
    aid = _as_int(move.get("id"))

    # Standard normal IDs win over noisy helper labels and scanner kinds.
    if aid in ACTION_NOTATION:
        return "normal"
    if "throw" in text or "thrown" in text or "assist" in text or "taunt" in text:
        return "other"
    if kind in {"super", "hyper"} or any(token in text for token in (" super", "hyper", "shinku", "shin sho", "shin shoryu")):
        return "super"
    if kind == "special":
        return "special"
    if aid is not None and 0x0160 <= aid < 0x0180:
        return "super"
    if aid is not None and 0x0130 <= aid < 0x0160:
        return "special"
    if notation_for_move(move):
        return "normal"
    if any(token in text for token in ("hado", "tatsu", "shoryu", "denko", "bird run", "bird shoot", "eagle rush")):
        return "special"
    return "other"


def _candidate_score(move: Dict[str, Any]) -> Tuple[int, int, int, int]:
    source = str(move.get("move_name_source") or "").strip().lower()
    named = 0 if source == "lookup" else 1
    kind = move_kind(move)
    kind_score = {"normal": 0, "special": 1, "super": 2}.get(kind, 9)
    scan_index = _as_int(move.get("_scan_index")) or 0
    address = _as_int(move.get("abs")) or 0xFFFFFFFF
    return named, kind_score, scan_index, address


def canonical_moves(moves: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return one user-facing row per action ID for the mapper.

    The normal scanner can emit many helper and duplicate rows. The mapper is
    about selectable actions, so only named normal, special, and super roots are
    retained, with the best row chosen for each action ID.
    """
    chosen: Dict[Tuple[Any, ...], Dict[str, Any]] = {}
    for move in moves or []:
        if not isinstance(move, dict):
            continue
        kind = move_kind(move)
        if kind not in {"normal", "special", "super"}:
            continue
        aid = _as_int(move.get("id"))
        address = _as_int(move.get("abs"))
        if aid is None or not address:
            continue

        name = display_name(move)
        low_name = name.lower()
        notation = notation_for_move(move)
        is_named = bool(notation) or (
            "anim_" not in low_name
            and "filler" not in low_name
            and "unknown" not in low_name
            and not low_name.startswith("action 0x")
        )
        if not is_named:
            continue
        if any(token in low_name for token in ("(second)", " tier", "hit 1", "hit 2", "hit 3", "projectile definitions", "action graph")):
            continue
        # Named command roots normally live in the standard normal block or in
        # the 0x130+ special/super command ranges. Low-ID special rows are
        # usually helper actions and would make the mapper unreadable.
        if kind in {"special", "super"} and aid < 0x0130:
            continue

        key = (aid,)
        current = chosen.get(key)
        if current is None or _candidate_score(move) < _candidate_score(current):
            chosen[key] = move

    return sorted(
        chosen.values(),
        key=lambda move: (
            {"normal": 0, "special": 1, "super": 2}.get(move_kind(move), 9),
            GROUND_NORMAL_RANK.get(notation_for_move(move), 100),
            _as_int(move.get("id")) or 0xFFFF,
            _as_int(move.get("abs")) or 0xFFFFFFFF,
        ),
    )


def direct_route_targets(source: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    targets: Dict[int, Dict[str, Any]] = {}
    for probe in source.get("cancel_windows") or []:
        if str(probe.get("kind") or "").strip().lower() != "normal":
            continue
        target_id = _as_int(probe.get("target_id"))
        if target_id is None:
            continue
        existing = targets.get(target_id)
        if existing is None:
            targets[target_id] = probe
            continue
        old_addr = _as_int(existing.get("addr")) or 0xFFFFFFFF
        new_addr = _as_int(probe.get("addr")) or 0xFFFFFFFF
        if new_addr < old_addr:
            targets[target_id] = probe
    return targets


def classify_cancel(source: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
    source_id = _as_int(source.get("id"))
    target_id = _as_int(target.get("id"))
    source_notation = notation_for_move(source)
    target_notation = notation_for_move(target)
    source_kind = move_kind(source)
    target_kind = move_kind(target)
    routes = direct_route_targets(source)
    route = routes.get(target_id) if target_id is not None else None

    base = {
        "status": "UNKNOWN",
        "reason": "Current mapper does not model this source move yet.",
        "evidence_addr": None,
        "branch_addr": None,
        "source_notation": source_notation,
        "target_notation": target_notation,
        "source_kind": source_kind,
        "target_kind": target_kind,
    }

    if source_id is not None and target_id == source_id:
        base.update(status="BLOCKED", reason="Same action, chain rules require a later destination.")
        return base

    if route is not None:
        base.update(
            status="ALLOWED",
            reason="Direct command-normal route found in the source move script.",
            evidence_addr=_as_int(route.get("addr")),
            branch_addr=_as_int(route.get("branch_addr")),
        )
        return base

    modeled_source = source_notation in GROUND_NORMAL_RANK or source_notation in TERMINAL_COMMAND_NORMALS
    if not modeled_source or source_kind != "normal":
        return base

    if target_kind == "special":
        base.update(
            status="ELIGIBLE",
            reason=(
                "Special-cancel category is available, but this target's ground/air, "
                "command, and character-state requirements are not decoded yet."
            ),
        )
        return base
    if target_kind == "super":
        base.update(
            status="ELIGIBLE",
            reason=(
                "Super-cancel category is available, but this target's ground/air, "
                "command, meter, and character-state requirements are not decoded yet."
            ),
        )
        return base
    if target_kind != "normal":
        base.update(status="UNKNOWN", reason="Destination is outside the current normal, special, and super model.")
        return base

    if source_notation in TERMINAL_COMMAND_NORMALS:
        base.update(status="BLOCKED", reason=f"{source_notation} is terminal for normal chains in the current model.")
        return base

    source_rank = GROUND_NORMAL_RANK.get(source_notation)
    target_rank = GROUND_NORMAL_RANK.get(target_notation)
    if source_rank is not None and target_rank is not None:
        if target_rank > source_rank:
            base.update(
                status="ALLOWED",
                reason=f"Ground chain rank advances from {source_notation} to {target_notation}.",
            )
        else:
            base.update(
                status="BLOCKED",
                reason=f"Ground chain rank does not advance from {source_notation} to {target_notation}.",
            )
        return base

    if target_notation in NORMAL_EXCEPTIONS.get(source_notation, set()):
        base.update(
            status="ALLOWED",
            reason=f"Known command-normal exception: {source_notation} may route to {target_notation}.",
        )
        return base

    base.update(
        status="BLOCKED",
        reason="No normal-chain rank step or direct command route exists in the current model.",
    )
    return base


def build_cancel_rows(
    source: Dict[str, Any],
    moves: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for target in canonical_moves(moves):
        result = classify_cancel(source, target)
        result.update(
            target=target,
            target_id=_as_int(target.get("id")),
            target_addr=_as_int(target.get("abs")),
            target_name=display_name(target),
        )
        rows.append(result)
    return sorted(
        rows,
        key=lambda row: (
            _STATUS_ORDER.get(str(row.get("status") or "UNKNOWN"), 9),
            {"normal": 0, "special": 1, "super": 2}.get(str(row.get("target_kind") or ""), 9),
            GROUND_NORMAL_RANK.get(str(row.get("target_notation") or ""), 100),
            row.get("target_id") if row.get("target_id") is not None else 0xFFFF,
            row.get("target_addr") if row.get("target_addr") is not None else 0xFFFFFFFF,
        ),
    )
