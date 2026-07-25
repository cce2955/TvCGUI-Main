from __future__ import annotations

import math
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from tvcgui.features.combat import projectile_scanner


_LEVEL_RE = re.compile(r"(?:level|lvl|lv|l)\s*[-_:]?\s*([1-9][0-9]*)", re.IGNORECASE)
_TIER_RE = re.compile(r"(?:tier|charge)\s*[-_:]?\s*([1-9][0-9]*)", re.IGNORECASE)
_LEVEL_SUFFIX_RE = re.compile(
    r"\s*(?:\(|\[)?\s*(?:level|lvl|lv|l|tier|charge)\s*[-_:]?\s*[1-9][0-9]*\s*(?:\)|\])?\s*$",
    re.IGNORECASE,
)
_STRENGTH_SUFFIX_RE = re.compile(
    r"\s*(?:\(|\[)?\s*(?:strength\s*)?(a|b|c|d|l|m|h)\s*(?:\)|\])?\s*$",
    re.IGNORECASE,
)
_STRENGTH_RANK = {
    "a": 1, "l": 1,
    "b": 2, "m": 2,
    "c": 3, "h": 3,
    "d": 4,
}


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except Exception:
        return float(default)
    if not math.isfinite(out):
        return float(default)
    return out


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _name_token(value: Any) -> str:
    return "".join(ch for ch in _norm(value) if ch.isalnum())


def _family_name(value: Any) -> str:
    """Return the projectile family name without a level/strength suffix."""
    text = str(value or "").strip()
    text = _LEVEL_SUFFIX_RE.sub("", text)
    text = re.sub(r"\b(?:level|lvl|lv|l|tier)\s*[1-9][0-9]*\b", "", text, flags=re.IGNORECASE)
    text = _STRENGTH_SUFFIX_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip(" -_/()[]")
    return text


def _explicit_level(value: Any) -> int | None:
    text = str(value or "")
    match = _LEVEL_RE.search(text) or _TIER_RE.search(text)
    if not match:
        return None
    try:
        return max(1, int(match.group(1)))
    except Exception:
        return None


def _explicit_variant_rank(value: Any) -> int | None:
    level = _explicit_level(value)
    if level is not None:
        return level
    match = _STRENGTH_SUFFIX_RE.search(str(value or ""))
    if not match:
        return None
    return _STRENGTH_RANK.get(match.group(1).lower())


def _label_has_variant(value: Any) -> bool:
    return _explicit_variant_rank(value) is not None


def _definition_id(hit: dict[str, Any]) -> int:
    for key in ("id", "projectile_id", "ps_projectile_id", "proj_id"):
        value = _int(hit.get(key), 0)
        if value > 0:
            return value & 0xFFFF
    return 0


def _definition_damage(hit: dict[str, Any]) -> int:
    for key in ("dmg", "damage", "super_damage", "ps_damage"):
        value = _int(hit.get(key), 0)
        if value:
            return value
    return 0


def _variant_signature(hit: dict[str, Any]) -> tuple[Any, ...]:
    return (
        _definition_damage(hit),
        round(_float(hit.get("speed")), 5),
        round(_float(hit.get("kb_x")), 5),
        round(_float(hit.get("kb_y")), 5),
        _int(hit.get("lifetime"), 0),
        round(_float(hit.get("arc")), 5),
        round(_float(hit.get("arc2")), 5),
        round(_float(hit.get("radius")), 5),
    )


def _variant_sort_key(hit: dict[str, Any]) -> tuple[Any, ...]:
    explicit = _explicit_variant_rank(hit.get("move"))
    if explicit is not None:
        return (0, explicit, _definition_damage(hit), _int(hit.get("addr"), 0))
    damage = _definition_damage(hit)
    speed = abs(_float(hit.get("speed")))
    lifetime = _int(hit.get("lifetime"), 0)
    radius = abs(_float(hit.get("radius")))
    kb_mag = abs(_float(hit.get("kb_x"))) + abs(_float(hit.get("kb_y")))
    return (1, damage, speed, lifetime, radius, kb_mag, _definition_id(hit), _int(hit.get("addr"), 0))


def _label_with_level(base_label: str, level: int, total: int) -> str:
    """Append only the mini-profiler's numeric projectile variant."""
    base = str(base_label or "").strip()
    if not base:
        return str(level)
    if _label_has_variant(base):
        return base
    return f"{base} {level}"


def _level_capable_family(snap: dict[str, Any], definition: dict[str, Any]) -> bool:
    """Reject ordinary L/M/H projectile variants while keeping charge levels."""
    char_name = _norm(snap.get("name") or snap.get("char_name"))
    text = " ".join([
        _norm(snap.get("mv_label")),
        _norm(definition.get("move")),
        _norm(definition.get("proj_aliases")),
    ])
    if _explicit_level(text) is not None:
        return True
    if any(token in text.split() for token in ("charge", "charged", "charging", "hold", "held")):
        return True
    compact = _name_token(text)
    if char_name == "zero" and any(token in compact for token in ("hyperzeroblaster", "hyperblaster")):
        return True
    if char_name in {"megaman volnutt", "mega man volnutt", "volnutt"} and "megabuster" in compact:
        return True
    if char_name in {"yatterman 2", "yatterman2"} and "charge" in compact:
        return True
    return False


def _correlated_move_label(
    char_key: str | None,
    projectile_id: int,
    damage: int,
    action_id: int,
    static_addr: int = 0,
) -> str | None:
    """Return the profiler's strongest learned move label for this live projectile.

    The Profile Monitor already records the exact action label against live
    projectile evidence. Reuse that evidence here so the HUD and main GUI do
    not collapse charged variants back to the generic move-family label.
    """
    if not char_key or projectile_id <= 0:
        return None
    try:
        payload = projectile_scanner._load_projectile_correlations()  # type: ignore[attr-defined]
    except Exception:
        return None
    char = ((payload.get("characters") or {}).get(str(char_key)) or {})
    observations = char.get("observations") or {}
    candidates: list[tuple[int, int, str]] = []
    for obs in observations.values():
        if _int(obs.get("projectile_id"), 0) != projectile_id:
            continue
        obs_damage = _int(obs.get("damage"), 0)
        if damage and obs_damage not in (0, damage):
            continue
        obs_static = _int(obs.get("static_addr"), 0)
        for move in (obs.get("moves") or {}).values():
            label = str(move.get("move_label") or "").strip()
            if not label:
                continue
            move_action = _int(move.get("action_id"), 0)
            count = _int(move.get("count"), 0)
            score = count
            if action_id and move_action == action_id:
                score += 100000
            elif action_id and move_action:
                score -= 10000
            if static_addr and obs_static == static_addr:
                score += 25000
            if damage and obs_damage == damage:
                score += 5000
            # Prefer labels that carry an explicit level/strength over a bare
            # family label when the evidence score is otherwise equal.
            specificity = 0
            low = label.lower()
            if _explicit_level(label) is not None:
                specificity += 1000
            if any(token in low for token in ("charge", "charged")):
                specificity += 250
            if re.search(r"(?:^|\s)(?:a|b|c|l|m|h)$", low):
                specificity += 200
            candidates.append((score, specificity, label))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], -item[1], item[2].lower()))
    return candidates[0][2]


@dataclass
class _SlotState:
    token: tuple[Any, ...] | None = None
    definitions: list[dict[str, Any]] = field(default_factory=list)
    scanning: bool = False
    scan_complete: bool = False
    scan_generation: int = 0
    live_match: dict[str, Any] | None = None
    live_seen_at: float = 0.0
    live_action_id: int = 0
    debug_token: tuple[Any, ...] | None = None


class ProjectileMoveLevelDetector:
    """Detect charge/level variants from the projectile that actually spawned.

    Action IDs often identify only the move family. This runs a small slot-owned
    projectile profiler, identifies which differing projectile actually spawned,
    and appends its 1/2/3/4 variant number to the existing move label.
    """

    POLL_SECONDS = 0.055
    LATCH_SECONDS = 2.40

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._slots: dict[str, _SlotState] = {}
        self._latest_live: list[dict[str, Any]] = []
        self._poll_running = False
        self._last_poll_start = 0.0

    def reset(self) -> None:
        with self._lock:
            self._slots.clear()
            self._latest_live = []
            self._poll_running = False
            self._last_poll_start = 0.0

    def _character_info(self, snap: dict[str, Any]) -> tuple[int, str, str | None]:
        cid = 0
        for key in ("id", "char_id", "csv_char_id"):
            cid = _int(snap.get(key), 0)
            if cid:
                break
        name = str(snap.get("name") or snap.get("char_name") or "Unknown")
        try:
            key = projectile_scanner._projectile_key_from_char_id(cid)  # type: ignore[attr-defined]
        except Exception:
            key = None
        if not key:
            try:
                key = projectile_scanner._NAME_TO_KEY.get(name)  # type: ignore[attr-defined]
            except Exception:
                key = None
        return cid, name, key

    def _schedule_scan(self, slot: str, snap: dict[str, Any], state: _SlotState) -> None:
        cid, _name, key = self._character_info(snap)
        base = _int(snap.get("base"), 0)
        token = (base, cid, str(key or ""))
        if state.token == token and (state.scanning or state.scan_complete):
            return
        state.token = token
        state.definitions = []
        state.scan_complete = False
        state.live_match = None
        state.live_seen_at = 0.0
        state.live_action_id = 0
        state.scan_generation += 1
        generation = state.scan_generation
        if not key or not base:
            state.scanning = False
            state.scan_complete = True
            return
        state.scanning = True

        def worker() -> None:
            result: list[dict[str, Any]] = []
            try:
                projectile_scanner._run_monitor_slot_scan(  # type: ignore[attr-defined]
                    slot,
                    str(key),
                    base,
                    cid,
                    lambda _pct: None,
                    lambda hits: result.extend(list(hits or [])),
                )
                prepared = self._prepare_definitions(result)
            except Exception:
                prepared = []
            with self._lock:
                current = self._slots.get(slot)
                if current is None or current.scan_generation != generation:
                    return
                current.definitions = prepared
                current.scanning = False
                current.scan_complete = True

        threading.Thread(target=worker, name=f"projectile-level-scan-{slot}", daemon=True).start()

    def _prepare_definitions(self, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        seen: set[tuple[int, str, tuple[Any, ...]]] = set()
        for raw in hits:
            hit = dict(raw or {})
            pid = _definition_id(hit)
            if pid <= 0:
                continue
            fmt = str(hit.get("fmt") or "")
            # Live actors correlate to projectile-style definitions. Super beam
            # cards without a projectile ID are intentionally excluded.
            sig = _variant_signature(hit)
            marker = (pid, fmt, sig)
            if marker in seen:
                continue
            seen.add(marker)
            hit["_auto_pid"] = pid
            hit["_auto_damage"] = _definition_damage(hit)
            hit["_auto_family"] = _family_name(hit.get("move"))
            hit["_auto_family_token"] = _name_token(hit["_auto_family"])
            hit["_auto_signature"] = sig
            rows.append(hit)

        # A charged move can spawn a different projectile ID for every charge
        # strength. Group by the normalized projectile family, not by projectile
        # ID. The old PID grouping made every Zero/Volnutt/Yatterman-2 variant a
        # one-item family, so the GUI could never append 1/2/3/4.
        by_family: dict[str, list[dict[str, Any]]] = {}
        for hit in rows:
            pid = _int(hit.get("_auto_pid"), 0)
            family_token = str(hit.get("_auto_family_token") or "")
            if family_token.startswith("projectile0x") or family_token.startswith("projectile"):
                family_token = ""
            # Unnamed rows stay isolated by PID. Named profiler rows can span
            # multiple PIDs and become one numbered projectile family.
            group_key = family_token or f"pid:{pid}"
            hit["_auto_group_key"] = group_key
            by_family.setdefault(group_key, []).append(hit)

        for family_rows in by_family.values():
            unique_variants: dict[tuple[Any, ...], dict[str, Any]] = {}
            for hit in family_rows:
                identity = (
                    _int(hit.get("_auto_pid"), 0),
                    tuple(hit.get("_auto_signature") or ()),
                )
                unique_variants.setdefault(identity, hit)
            ordered = sorted(unique_variants.values(), key=_variant_sort_key)
            rank_by_identity: dict[tuple[Any, ...], int] = {}
            explicit_ranks = [_explicit_variant_rank(hit.get("move")) for hit in ordered]
            explicit_ranks = [rank for rank in explicit_ranks if rank is not None]
            used_ranks = set(explicit_ranks)
            next_rank = 1
            for hit in ordered:
                identity = (
                    _int(hit.get("_auto_pid"), 0),
                    tuple(hit.get("_auto_signature") or ()),
                )
                explicit = _explicit_variant_rank(hit.get("move"))
                if explicit is not None:
                    rank = int(explicit)
                else:
                    while next_rank in used_ranks:
                        next_rank += 1
                    rank = next_rank
                    used_ranks.add(rank)
                    next_rank += 1
                rank_by_identity[identity] = rank
            total = max([len(ordered), *used_ranks], default=len(ordered))
            for hit in family_rows:
                identity = (
                    _int(hit.get("_auto_pid"), 0),
                    tuple(hit.get("_auto_signature") or ()),
                )
                hit["auto_level"] = rank_by_identity.get(identity, 1)
                hit["auto_level_total"] = max(1, total)
        return rows

    def _start_live_poll(self, now: float) -> None:
        if self._poll_running or (now - self._last_poll_start) < self.POLL_SECONDS:
            return
        self._poll_running = True
        self._last_poll_start = now

        def worker() -> None:
            try:
                records = list(projectile_scanner._collect_live_projectiles())  # type: ignore[attr-defined]
            except Exception:
                records = []
            with self._lock:
                self._latest_live = records
                self._poll_running = False

        threading.Thread(target=worker, name="projectile-level-live-poll", daemon=True).start()

    def _correlated_static_address(
        self,
        char_key: str | None,
        projectile_id: int,
        damage: int,
        action_id: int,
    ) -> int:
        if not char_key:
            return 0
        try:
            payload = projectile_scanner._load_projectile_correlations()  # type: ignore[attr-defined]
        except Exception:
            return 0
        char = ((payload.get("characters") or {}).get(str(char_key)) or {})
        observations = char.get("observations") or {}
        best: tuple[int, int] | None = None
        for obs in observations.values():
            if _int(obs.get("projectile_id"), 0) != projectile_id:
                continue
            obs_damage = _int(obs.get("damage"), 0)
            if damage and obs_damage not in (0, damage):
                continue
            score = 0
            for move in (obs.get("moves") or {}).values():
                if action_id and _int(move.get("action_id"), 0) == action_id:
                    score += 1000
                score += _int(move.get("count"), 0)
            static_addr = _int(obs.get("static_addr"), 0)
            candidate = (score, static_addr)
            if static_addr and (best is None or candidate > best):
                best = candidate
        return best[1] if best else 0

    def _match_definition(
        self,
        state: _SlotState,
        record: dict[str, Any],
        snap: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        definitions = list(state.definitions)
        if not definitions:
            return None, "definitions pending"
        pid = _int(record.get("projectile_id"), 0)
        damage = _int(record.get("damage"), 0)
        action_id = _int(record.get("owner_action_id"), 0) or _int(snap.get("mv_id_display"), 0)
        _cid, _name, char_key = self._character_info(snap)
        correlated_addr = self._correlated_static_address(char_key, pid, damage, action_id)
        if correlated_addr:
            exact_addr = [hit for hit in definitions if _int(hit.get("addr"), 0) == correlated_addr]
            if len(exact_addr) == 1:
                return exact_addr[0], "saved live correlation"

        candidates = [hit for hit in definitions if _int(hit.get("_auto_pid"), 0) == pid]
        if not candidates:
            candidates = [hit for hit in definitions if damage and _int(hit.get("_auto_damage"), 0) == damage]
            if len(candidates) == 1:
                return candidates[0], "unique slot-owned damage"
            return None, "no matching projectile definition"

        current_token = _name_token(snap.get("mv_label"))
        if current_token:
            name_matches = [
                hit for hit in candidates
                if hit.get("_auto_family_token")
                and (
                    str(hit.get("_auto_family_token")) in current_token
                    or current_token in str(hit.get("_auto_family_token"))
                )
            ]
            if name_matches:
                candidates = name_matches

        if damage:
            damage_matches = [hit for hit in candidates if _int(hit.get("_auto_damage"), 0) == damage]
            if len(damage_matches) == 1:
                return damage_matches[0], "exact projectile ID and damage"
            if damage_matches:
                candidates = damage_matches

        if len(candidates) == 1:
            return candidates[0], "exact projectile ID"

        # Last resort for variants with identical damage: compare live movement
        # magnitude against the static speed ordering. This is intentionally low
        # confidence because the units are not guaranteed to be identical.
        velocity = record.get("velocity") or (0.0, 0.0, 0.0)
        live_speed = math.sqrt(sum(_float(v) ** 2 for v in velocity[:3]))
        if live_speed > 0.00001:
            ranked = sorted(
                candidates,
                key=lambda hit: abs(abs(_float(hit.get("speed"))) - live_speed),
            )
            if ranked:
                return ranked[0], "projectile ID plus nearest speed"
        return None, "ambiguous projectile variant"

    def _select_live_match(
        self,
        slot: str,
        snap: dict[str, Any],
        state: _SlotState,
        records: list[dict[str, Any]],
        now: float,
    ) -> None:
        _cid, _name, char_key = self._character_info(snap)
        base = _int(snap.get("base"), 0)
        owned = [record for record in records if _int(record.get("owner_pointer"), 0) == base]
        if not owned:
            return
        action_id = _int(snap.get("mv_id_display"), 0)
        action_owned = [
            record for record in owned
            if not action_id or _int(record.get("owner_action_id"), 0) in (0, action_id)
        ]
        candidates = action_owned or owned
        best: tuple[int, dict[str, Any], dict[str, Any], str] | None = None
        for record in candidates:
            definition, evidence = self._match_definition(state, record, snap)
            if definition is None:
                continue
            total = _int(definition.get("auto_level_total"), 1)
            level = _int(definition.get("auto_level"), 1)
            score = 0
            if "correlation" in evidence:
                score += 100
            if "ID and damage" in evidence:
                score += 80
            elif "ID" in evidence:
                score += 60
            if total > 1:
                score += 25
            score += level
            item = (score, record, definition, evidence)
            if best is None or item[0] > best[0]:
                best = item
        if best is None:
            return
        _score, record, definition, evidence = best
        level = max(1, _int(definition.get("auto_level"), 1))
        total = max(level, _int(definition.get("auto_level_total"), 1))
        if total <= 1:
            return
        state.live_match = {
            "level": level,
            "total": total,
            "projectile_id": _int(record.get("projectile_id"), 0),
            "damage": _int(record.get("damage"), 0),
            "static_addr": _int(definition.get("addr"), 0),
            "definition_name": str(definition.get("move") or ""),
            "evidence": evidence,
            "actor": _int(record.get("actor"), 0),
            "char_key": char_key,
        }
        state.live_seen_at = now
        state.live_action_id = action_id or _int(record.get("owner_action_id"), 0)

    def _decorate_one(self, slot: str, snap: dict[str, Any], state: _SlotState, now: float) -> None:
        """Apply the mini-profiler number to the existing GUI/overlay label."""
        action_id = _int(snap.get("mv_id_display"), 0)
        match = state.live_match
        if not match:
            return
        age = now - state.live_seen_at
        if age > self.LATCH_SECONDS:
            state.live_match = None
            return
        if state.live_action_id and action_id and action_id != state.live_action_id and age > 0.25:
            state.live_match = None
            return

        level = max(1, _int(match.get("level"), 1))
        total = max(level, _int(match.get("total"), 1))
        if total <= 1:
            return

        base_label = str(snap.get("mv_label_base") or snap.get("mv_label") or "").strip()
        definition_label = str(match.get("definition_name") or "").strip()
        family_label = _family_name(definition_label)
        if not base_label or _norm(base_label) in {"unknown", "action", "move", "projectile"}:
            base_label = family_label or base_label

        display = _label_with_level(base_label, level, total)
        aliases = [
            base_label, display, definition_label, family_label,
            f"{base_label} {level}".strip(),
            f"{base_label} level {level}".strip(),
            f"{base_label} lv{level}".strip(),
            f"{base_label} l{level}".strip(),
            f"{base_label} L{level}".strip(),
        ]
        deduped: list[str] = []
        seen: set[str] = set()
        for alias in aliases:
            alias = str(alias or "").strip()
            key = _norm(alias)
            if alias and key not in seen:
                seen.add(key)
                deduped.append(alias)

        snap["mv_label_base"] = base_label
        snap["mv_label_display"] = display
        snap["mv_label_aliases"] = deduped
        snap["move_level"] = level
        snap["move_level_total"] = total
        snap["move_level_label"] = str(level)
        snap["move_level_source"] = "projectile_mini_profiler"
        snap["move_level_confidence"] = str(match.get("evidence") or "live projectile correlation")
        snap["move_level_projectile_id"] = _int(match.get("projectile_id"), 0)
        snap["move_level_projectile_damage"] = _int(match.get("damage"), 0)
        snap["move_level_static_addr"] = _int(match.get("static_addr"), 0)
        snap["move_level_actor"] = _int(match.get("actor"), 0)

        debug_token = (action_id, snap["move_level_projectile_id"], level, display)
        if getattr(state, "debug_token", None) != debug_token:
            state.debug_token = debug_token
            print(
                f"[projectile variant] {slot} action={action_id} "
                f"pid={snap['move_level_projectile_id']} -> {display} "
                f"({level}/{total}, {snap['move_level_confidence']})",
                flush=True,
            )

    def update(self, snapshots: dict[str, dict[str, Any]] | None) -> None:
        if not isinstance(snapshots, dict):
            return
        now = time.monotonic()
        with self._lock:
            for slot, snap in snapshots.items():
                if not isinstance(snap, dict):
                    continue
                state = self._slots.setdefault(str(slot), _SlotState())
                self._schedule_scan(str(slot), snap, state)
            self._start_live_poll(now)
            records = list(self._latest_live)
            for slot, snap in snapshots.items():
                if not isinstance(snap, dict):
                    continue
                state = self._slots.setdefault(str(slot), _SlotState())
                self._select_live_match(str(slot), snap, state, records, now)
                self._decorate_one(str(slot), snap, state, now)

    def decorate(self, snapshots: dict[str, dict[str, Any]] | None) -> None:
        if not isinstance(snapshots, dict):
            return
        now = time.monotonic()
        with self._lock:
            states_by_base = {
                _int((snap or {}).get("base"), 0): self._slots.get(str(slot))
                for slot, snap in snapshots.items()
                if isinstance(snap, dict)
            }
            for slot, snap in snapshots.items():
                if not isinstance(snap, dict):
                    continue
                state = self._slots.get(str(slot)) or states_by_base.get(_int(snap.get("base"), 0))
                if state is not None:
                    self._decorate_one(str(slot), snap, state, now)


PROJECTILE_LEVEL_DETECTOR = ProjectileMoveLevelDetector()


__all__ = ["ProjectileMoveLevelDetector", "PROJECTILE_LEVEL_DETECTOR"]
