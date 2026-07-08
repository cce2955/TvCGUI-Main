"""Runtime reverse profiler for engine-resolved TvC frame data.

This module only learns rows that the static scanner has already identified as
using the engine's ``-2/-2`` stun resolver.  Direct script-backed rows are
never sampled, overwritten, or made read-only by this path.

The profiler is observation-only:
* hit/block stun comes from the victim's live resolved counters;
* whiff recovery comes from the attacker's action-frame counter after the
  resolved last-active frame;
* all results are stored outside the static fast-profile cache;
* no Dolphin memory is ever written.
"""
from __future__ import annotations

import json
import math
import os
import struct
import tempfile
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Deque, Dict, Iterable, Optional

try:
    from tvcgui.platform.dolphin import rd32
except Exception:
    # Unit tests and offline profile viewers do not need a live Dolphin hook.
    def rd32(_addr: int):
        return None

from tvcgui.core.paths import user_data_path
from tvcgui.core.constants import (
    RUNTIME_RESOLVED_STUN_OFF,
    RUNTIME_STUN_REMAINING_OFF,
    RUNTIME_IMPACT_FREEZE_OFF,
)

RUNTIME_STUN_PROFILE_VERSION = 2
RUNTIME_STUN_PROFILE_FILE = "runtime_stun_profiles.json"

# A move can still be the contact owner for a short period after its visible
# active state flips. Keeping this window small avoids attributing a contact to
# an old attack while still covering one-frame handoffs.
ATTACK_HISTORY_FRAMES = 12
PENDING_RESULT_FRAMES = 6

# These are ordinary public player actions in the live move field. System
# states such as idle/block/pushblock are below this range in the captures.
MIN_PLAYER_ACTION_ID = 0x0100

# Same counter used by the hitbox overlay. It is a big-endian float where 2.0
# means action frame 1, hence the -1 bias.
ACTION_COUNTER_OFF = 0x01D8
ACTION_COUNTER_FRAME_BIAS = -1.0
ACTION_COUNTER_MAX = 600.0
MAX_RECOVERY_FRAMES = 360


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _profile_dir() -> Path:
    """Use the persistent organized runtime-data directory."""
    return Path(user_data_path("runtime"))


def default_profile_path() -> Path:
    return _profile_dir() / RUNTIME_STUN_PROFILE_FILE


def _empty_doc() -> dict:
    return {
        "version": RUNTIME_STUN_PROFILE_VERSION,
        "updated_utc": "",
        "moves": {},
    }


def _read_doc(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_doc()
    if not isinstance(raw, dict):
        return _empty_doc()
    moves = raw.get("moves")
    if not isinstance(moves, dict):
        raw["moves"] = {}
    raw.setdefault("version", RUNTIME_STUN_PROFILE_VERSION)
    raw.setdefault("updated_utc", "")
    return raw


def _write_doc_atomic(path: Path, doc: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = dict(doc)
        doc["version"] = RUNTIME_STUN_PROFILE_VERSION
        doc["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(tmp_name, path)
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except Exception:
                pass
        return True
    except Exception:
        return False


def _bucket_mode(bucket: Any) -> tuple[Optional[int], int]:
    """Return ``(modal value, total samples)`` from one persisted bucket."""
    if not isinstance(bucket, dict):
        return None, 0
    samples_raw = bucket.get("samples")
    if not isinstance(samples_raw, dict):
        return None, 0
    parsed: list[tuple[int, int]] = []
    for raw_value, raw_count in samples_raw.items():
        value = _safe_int(raw_value, -1)
        count = _safe_int(raw_count, 0)
        if value > 0 and count > 0:
            parsed.append((value, count))
    if not parsed:
        return None, 0
    # Stable tie-break: most frequent, then the lower value. A tie should not
    # silently claim that a later capture is more authoritative.
    parsed.sort(key=lambda item: (-item[1], item[0]))
    total = sum(c for _, c in parsed)
    return parsed[0][0], total


def _is_engine_resolver_row(mv: dict) -> bool:
    """True only for a scanner-proven ``-2/-2`` engine-resolved normal row."""
    source = str(mv.get("stun_source") or "")
    return bool(mv.get("runtime_profile_eligible")) or source == "engine_default_hit_level"


def _runtime_value_text(value: Any, source: Any) -> str:
    if value is None:
        return ""
    try:
        text = str(int(value))
    except Exception:
        return ""
    return f"{text} [R]" if str(source or "") == "runtime_observed" else text


def apply_runtime_stun_observations(moves: Iterable[dict], char_id: Any, *, path: Optional[Path] = None) -> None:
    """Overlay observed values only onto engine-resolved normal rows.

    Static/direct signature rows are deliberately skipped even if an old JSON
    file happens to contain a record with the same character/action key. This
    keeps Ryu/Chun-style direct data authoritative and confines reverse
    profiling to the Polimar/Morrigan-style engine resolver path.
    """
    try:
        cid = int(char_id)
    except Exception:
        return
    profile_path = Path(path or default_profile_path())
    doc = _read_doc(profile_path)
    all_moves = doc.get("moves") or {}
    if not isinstance(all_moves, dict):
        return

    for mv in moves or []:
        if not isinstance(mv, dict):
            continue
        if not _is_engine_resolver_row(mv):
            continue
        # Preserve eligibility after the static source is replaced by [R]. The
        # main HUD uses this marker to decide which live actions may be learned.
        mv["runtime_profile_eligible"] = True
        try:
            action_id = int(mv.get("id"))
        except Exception:
            continue
        record = all_moves.get(f"{cid}:{action_id}")
        if not isinstance(record, dict):
            continue

        hit_value, hit_count = _bucket_mode(record.get("hit"))
        block_value, block_count = _bucket_mode(record.get("block"))
        recovery_value, recovery_count = _bucket_mode(record.get("recovery"))
        hit_bucket = record.get("hit") if isinstance(record.get("hit"), dict) else {}
        hit_freeze, hit_freeze_count = _bucket_mode({"samples": hit_bucket.get("freeze_samples") or {}})

        runtime_info = {
            "hitstun": hit_value,
            "hitstun_samples": hit_count,
            "blockstun": block_value,
            "blockstun_samples": block_count,
            "hitstop": hit_freeze,
            "hitstop_samples": hit_freeze_count,
            "recovery": recovery_value,
            "recovery_samples": recovery_count,
            "path": str(profile_path),
        }
        mv["runtime_stun"] = runtime_info

        if hit_value is not None:
            mv["hitstun"] = int(hit_value)
            mv["hitstun_source"] = "runtime_observed"
            mv["hitstun_addr"] = None

        if block_value is not None:
            mv["blockstun"] = int(block_value)
            mv["blockstun_source"] = "runtime_observed"
            mv["blockstun_addr"] = None

        # Retain a direct/static hitstop packet if the scanner found one. Most
        # engine rows leave it blank, in which case the real contact freeze is
        # useful to show but remains non-editable.
        if hit_freeze is not None and (mv.get("hitstop") is None or str(mv.get("hitstop_source") or "") == "runtime_observed"):
            mv["hitstop"] = int(hit_freeze)
            mv["hitstop_source"] = "runtime_observed"
            mv["hitstop_addr"] = None

        if recovery_value is not None:
            mv["recovery"] = int(recovery_value)
            mv["recovery_source"] = "runtime_observed"
            mv["recovery_addr"] = None

        # Recompute only if the module now have a measured whiff recovery. This is the
        # usual frame-data convention: post-last-active attacker frames.
        if recovery_value is not None:
            try:
                hs = mv.get("hitstun")
                if hs is not None:
                    mv["adv_hit"] = int(hs) - int(recovery_value)
                    mv["adv_hit_derived"] = int(hs) - int(recovery_value)
            except Exception:
                pass
            try:
                bs = mv.get("blockstun")
                if bs is not None:
                    mv["adv_block"] = int(bs) - int(recovery_value)
                    mv["adv_block_derived"] = int(bs) - int(recovery_value)
            except Exception:
                pass

        if hit_value is not None or block_value is not None or recovery_value is not None:
            mv["stun_source"] = "runtime_observed"
            # The old -2/-2 packet requests a resolver. It is not an editable
            # address for the resolved number.
            mv["stun_addr"] = None


@dataclass
class _AttackCandidate:
    slot: str
    team: str
    char_id: int
    char_name: str
    action_id: int
    move_label: str
    x: Optional[float]
    frame: int
    active_end: Optional[int] = None


@dataclass
class _TargetState:
    assigned: int = 0
    remaining: int = 0
    freeze: int = 0
    hp: int = 0


@dataclass
class _PendingContact:
    target_slot: str
    attacker: Optional[_AttackCandidate]
    assigned: int
    freeze: int
    started_frame: int
    outcome: Optional[str] = None


@dataclass
class _RecoveryTrace:
    slot: str
    team: str
    char_id: int
    char_name: str
    action_id: int
    move_label: str
    active_end: int
    started_frame: int
    last_action_frame: int = 0
    saw_active: bool = False
    contaminated: bool = False


class RuntimeStunProfiler:
    """Continuously learn engine-resolved stun and whiff recovery.

    ``set_engine_move_targets`` is the gate that makes this fail closed: until
    the static scanner marks an exact ``character/action`` pair as engine
    resolved, the profiler records nothing for it.
    """

    def __init__(
        self,
        *,
        path: Optional[Path] = None,
        read_u32: Optional[Callable[[int], Optional[int]]] = None,
    ) -> None:
        self.path = Path(path or default_profile_path())
        self._read_u32 = read_u32 or rd32
        self._doc = _read_doc(self.path)
        self._prev_target: Dict[str, _TargetState] = {}
        self._recent_attacks: Deque[_AttackCandidate] = deque(maxlen=96)
        self._pending: Dict[str, _PendingContact] = {}
        self._recovery_traces: Dict[str, _RecoveryTrace] = {}
        self._engine_targets: Dict[tuple[int, int], dict] = {}
        self._last_save_frame = -9999
        self.last_event: str = ""
        self.total_events = 0

    def set_engine_move_targets(self, targets: Any) -> None:
        """Replace the exact action set allowed to be reverse-profiled.

        ``targets`` may be a ``{(char_id, action_id): {active_end: ...}}``
        mapping or an iterable of dictionaries carrying ``char_id``,
        ``action_id`` and optional ``active_end`` fields.
        """
        parsed: Dict[tuple[int, int], dict] = {}
        source = targets.items() if isinstance(targets, dict) else (targets or [])
        try:
            iterator = iter(source)
        except Exception:
            iterator = iter(())
        for item in iterator:
            key = None
            meta: dict = {}
            if isinstance(targets, dict):
                try:
                    raw_key, raw_meta = item
                    if isinstance(raw_key, tuple) and len(raw_key) == 2:
                        key = (int(raw_key[0]), int(raw_key[1]))
                    elif isinstance(raw_key, str) and ":" in raw_key:
                        a, b = raw_key.split(":", 1)
                        key = (int(a), int(b))
                    meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
                except Exception:
                    continue
            elif isinstance(item, dict):
                try:
                    key = (int(item.get("char_id")), int(item.get("action_id")))
                except Exception:
                    continue
                meta = dict(item)
            if not key or key[0] <= 0 or key[1] < MIN_PLAYER_ACTION_ID:
                continue
            active_end = _safe_int(meta.get("active_end"), 0)
            meta["active_end"] = active_end if active_end > 0 else None
            parsed[key] = meta
        self._engine_targets = parsed

        # A changed roster/profile must not let an old in-progress action be
        # written after it has lost eligibility.
        for slot, trace in list(self._recovery_traces.items()):
            if (trace.char_id, trace.action_id) not in self._engine_targets:
                self._recovery_traces.pop(slot, None)

    @staticmethod
    def _valid_runtime_counter(value: Any) -> int:
        value_i = _safe_int(value, 0)
        return value_i if 0 < value_i < 1000 else 0

    @staticmethod
    def _action_id(snap: dict) -> int:
        # Same priority as main.py's live move label path.
        raw = snap.get("attA") or snap.get("attB")
        return _safe_int(raw, 0)

    @staticmethod
    def _team(snap: dict) -> str:
        return str(snap.get("teamtag") or "")

    @staticmethod
    def _classify(snap: dict, prev: Optional[_TargetState]) -> Optional[str]:
        f063 = _safe_int(snap.get("f063"), -1)
        if f063 == 16:
            return "block"
        if f063 == 4:
            return "hit"
        cur_hp = _safe_int(snap.get("cur"), 0)
        if prev is not None and cur_hp > 0 and prev.hp > cur_hp:
            return "hit"
        return None

    @staticmethod
    def _decode_action_frame(raw: Any) -> Optional[int]:
        try:
            packed = struct.pack(">I", int(raw) & 0xFFFFFFFF)
            value = struct.unpack(">f", packed)[0]
        except Exception:
            return None
        if not math.isfinite(value) or value < 0.0 or value > ACTION_COUNTER_MAX:
            return None
        return max(0, int(round(value + ACTION_COUNTER_FRAME_BIAS)))

    def _read_action_frame(self, snap: dict) -> Optional[int]:
        base = _safe_int(snap.get("base"), 0)
        if not base:
            return None
        return self._decode_action_frame(self._read_u32(base + ACTION_COUNTER_OFF))

    def _target_meta(self, char_id: int, action_id: int) -> Optional[dict]:
        return self._engine_targets.get((int(char_id), int(action_id)))

    def _record_attack_candidates(self, snaps: Dict[str, dict], frame: int) -> None:
        # Keep only actions the scanner has explicitly marked engine-resolved.
        for slot, snap in snaps.items():
            if not isinstance(snap, dict):
                continue
            action_id = self._action_id(snap)
            if action_id < MIN_PLAYER_ACTION_ID:
                continue
            char_id = _safe_int(snap.get("id"), 0)
            if char_id <= 0:
                continue
            meta = self._target_meta(char_id, action_id)
            if meta is None:
                continue
            x_raw = snap.get("x")
            try:
                x = float(x_raw) if x_raw is not None else None
            except Exception:
                x = None
            active_end = _safe_int(meta.get("active_end"), 0)
            cand = _AttackCandidate(
                slot=str(slot),
                team=self._team(snap),
                char_id=char_id,
                char_name=str(snap.get("name") or ""),
                action_id=action_id,
                move_label=str(snap.get("mv_label") or ""),
                x=x,
                frame=frame,
                active_end=active_end if active_end > 0 else None,
            )
            if self._recent_attacks:
                tail = self._recent_attacks[-1]
                if tail.slot == cand.slot and tail.action_id == cand.action_id and tail.frame == frame:
                    continue
            self._recent_attacks.append(cand)

        min_frame = frame - ATTACK_HISTORY_FRAMES
        while self._recent_attacks and self._recent_attacks[0].frame < min_frame:
            self._recent_attacks.popleft()

    def _pick_attacker(self, target_slot: str, target_snap: dict, frame: int) -> Optional[_AttackCandidate]:
        target_team = self._team(target_snap)
        target_x_raw = target_snap.get("x")
        try:
            target_x = float(target_x_raw) if target_x_raw is not None else None
        except Exception:
            target_x = None

        candidates = []
        for cand in self._recent_attacks:
            if cand.slot == target_slot:
                continue
            if target_team and cand.team and cand.team == target_team:
                continue
            age = max(0, frame - cand.frame)
            if age > ATTACK_HISTORY_FRAMES:
                continue
            if target_x is not None and cand.x is not None:
                dist = abs(cand.x - target_x)
            else:
                dist = 999999.0
            # Newest action first, nearest attacker next. Live interactions are
            # normally point blank, which avoids donating a hit to a stale assist.
            candidates.append((age, dist, -cand.frame, cand))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        return candidates[0][3]

    def _record_for(self, *, char_id: int, char_name: str, action_id: int, move_label: str) -> dict:
        moves = self._doc.setdefault("moves", {})
        key = f"{char_id}:{action_id}"
        rec = moves.setdefault(
            key,
            {
                "char_id": int(char_id),
                "char_name": str(char_name or ""),
                "action_id": int(action_id),
                "move_label": str(move_label or ""),
                "hit": {"samples": {}},
                "block": {"samples": {}},
                "recovery": {"samples": {}},
            },
        )
        rec["char_name"] = str(char_name or rec.get("char_name") or "")
        rec["move_label"] = str(move_label or rec.get("move_label") or "")
        rec.setdefault("hit", {"samples": {}})
        rec.setdefault("block", {"samples": {}})
        rec.setdefault("recovery", {"samples": {}})
        return rec

    def _save_if_due(self, frame: int, *, force: bool = False) -> bool:
        if force or frame - self._last_save_frame >= 8:
            if _write_doc_atomic(self.path, self._doc):
                self._last_save_frame = frame
                return True
        return False

    def _mark_trace_contact(self, attacker: Optional[_AttackCandidate]) -> None:
        if attacker is None:
            return
        trace = self._recovery_traces.get(attacker.slot)
        if trace is None:
            return
        if trace.char_id == attacker.char_id and trace.action_id == attacker.action_id:
            trace.contaminated = True

    def _persist_observation(self, pending: _PendingContact, outcome: str, frame: int) -> bool:
        attacker = pending.attacker
        if attacker is None or outcome not in {"hit", "block"}:
            return False
        if attacker.action_id < MIN_PLAYER_ACTION_ID or attacker.char_id <= 0:
            return False
        if self._target_meta(attacker.char_id, attacker.action_id) is None:
            return False

        self._mark_trace_contact(attacker)
        rec = self._record_for(
            char_id=attacker.char_id,
            char_name=attacker.char_name,
            action_id=attacker.action_id,
            move_label=attacker.move_label,
        )
        rec["last_frame"] = int(frame)
        rec["last_freeze"] = int(pending.freeze)

        bucket = rec.setdefault(outcome, {"samples": {}})
        samples = bucket.setdefault("samples", {})
        value_key = str(int(pending.assigned))
        samples[value_key] = _safe_int(samples.get(value_key), 0) + 1
        freeze_samples = bucket.setdefault("freeze_samples", {})
        freeze_key = str(int(pending.freeze))
        freeze_samples[freeze_key] = _safe_int(freeze_samples.get(freeze_key), 0) + 1
        bucket["last_value"] = int(pending.assigned)
        bucket["last_freeze"] = int(pending.freeze)
        bucket["last_frame"] = int(frame)

        self.total_events += 1
        self.last_event = (
            f"{attacker.char_name or attacker.char_id} 0x{attacker.action_id:04X} "
            f"{outcome}={pending.assigned} freeze={pending.freeze}"
        )
        self._save_if_due(frame)
        return True

    def _persist_recovery(self, trace: _RecoveryTrace, recovery: int, frame: int) -> bool:
        if recovery <= 0 or recovery > MAX_RECOVERY_FRAMES:
            return False
        if self._target_meta(trace.char_id, trace.action_id) is None:
            return False
        rec = self._record_for(
            char_id=trace.char_id,
            char_name=trace.char_name,
            action_id=trace.action_id,
            move_label=trace.move_label,
        )
        bucket = rec.setdefault("recovery", {"samples": {}})
        samples = bucket.setdefault("samples", {})
        key = str(int(recovery))
        samples[key] = _safe_int(samples.get(key), 0) + 1
        bucket["last_value"] = int(recovery)
        bucket["last_frame"] = int(frame)
        rec["last_frame"] = int(frame)
        rec["last_recovery"] = int(recovery)

        self.total_events += 1
        self.last_event = (
            f"{trace.char_name or trace.char_id} 0x{trace.action_id:04X} "
            f"whiff_recovery={recovery}"
        )
        self._save_if_due(frame)
        return True

    def _finish_recovery_trace(
        self,
        slot: str,
        trace: _RecoveryTrace,
        *,
        next_action_id: int,
        frame: int,
    ) -> bool:
        self._recovery_traces.pop(slot, None)
        # A cancel/chain into another public action is not whiff recovery.
        if next_action_id >= MIN_PLAYER_ACTION_ID:
            return False
        if trace.contaminated or not trace.saw_active:
            return False
        if trace.last_action_frame <= trace.active_end:
            return False
        recovery = int(trace.last_action_frame) - int(trace.active_end)
        return self._persist_recovery(trace, recovery, frame)

    def _update_recovery_traces(self, snaps: Dict[str, dict], frame: int) -> bool:
        """Learn whiff recovery from action-frame end -> passive-state entry."""
        changed = False
        live_slots = set()
        for slot_raw, snap in (snaps or {}).items():
            if not isinstance(snap, dict):
                continue
            slot = str(slot_raw)
            live_slots.add(slot)
            char_id = _safe_int(snap.get("id"), 0)
            action_id = self._action_id(snap)
            action_frame = self._read_action_frame(snap)
            snap["runtime_action_frame"] = action_frame
            trace = self._recovery_traces.get(slot)

            # Existing trace remains valid only while it is the same exact
            # action. A frame-counter reset on the same action ID is treated as
            # an ambiguous loop/cancel and deliberately skipped.
            if trace is not None:
                same_action = trace.char_id == char_id and trace.action_id == action_id
                reset = (
                    same_action
                    and action_frame is not None
                    and trace.last_action_frame > 0
                    and action_frame + 2 < trace.last_action_frame
                )
                if not same_action or reset:
                    if not reset:
                        changed = self._finish_recovery_trace(
                            slot, trace, next_action_id=action_id, frame=frame
                        ) or changed
                    else:
                        self._recovery_traces.pop(slot, None)
                    trace = None

            meta = self._target_meta(char_id, action_id) if action_id >= MIN_PLAYER_ACTION_ID else None
            if meta is None:
                continue
            active_end = _safe_int(meta.get("active_end"), 0)
            if active_end <= 0:
                # Stun may still be observable, but without a resolved last
                # active frame there is no honest way to derive recovery.
                continue

            if trace is None:
                trace = _RecoveryTrace(
                    slot=slot,
                    team=self._team(snap),
                    char_id=char_id,
                    char_name=str(snap.get("name") or ""),
                    action_id=action_id,
                    move_label=str(snap.get("mv_label") or ""),
                    active_end=active_end,
                    started_frame=frame,
                )
                self._recovery_traces[slot] = trace

            if action_frame is not None:
                trace.last_action_frame = max(int(trace.last_action_frame), int(action_frame))
                if int(action_frame) >= int(trace.active_end):
                    trace.saw_active = True

        # Match ends/character swaps can otherwise leave stale traces. Do not
        # turn disappearance into a recovery sample: it could be a KO/tag/menu.
        for slot in list(self._recovery_traces):
            if slot not in live_slots:
                self._recovery_traces.pop(slot, None)
        return changed

    def _finalize_pending(self, target_slot: str, pending: _PendingContact, outcome: Optional[str], frame: int) -> bool:
        changed = False
        if outcome:
            changed = self._persist_observation(pending, outcome, frame)
        self._pending.pop(target_slot, None)
        return changed

    def update(self, snaps: Dict[str, dict], *, frame: int, now: Optional[float] = None) -> bool:
        """Sample current fighters. Returns True when an observation lands."""
        del now  # Kept in the API so callers can pass their normal HUD timing.
        changed = False
        self._record_attack_candidates(snaps, frame)
        changed = self._update_recovery_traces(snaps, frame) or changed

        live_slots = set()
        for slot, snap in (snaps or {}).items():
            if not isinstance(snap, dict):
                continue
            base = _safe_int(snap.get("base"), 0)
            if not base:
                continue
            live_slots.add(str(slot))

            assigned = self._valid_runtime_counter(self._read_u32(base + RUNTIME_RESOLVED_STUN_OFF))
            remaining = self._valid_runtime_counter(self._read_u32(base + RUNTIME_STUN_REMAINING_OFF))
            freeze = self._valid_runtime_counter(self._read_u32(base + RUNTIME_IMPACT_FREEZE_OFF))
            hp = _safe_int(snap.get("cur"), 0)
            current = _TargetState(assigned=assigned, remaining=remaining, freeze=freeze, hp=hp)
            previous = self._prev_target.get(str(slot))

            # Expose raw live counters for the HUD/debug inspector even before
            # a contact has a clean attacker attribution.
            snap["runtime_stun_assigned"] = assigned
            snap["runtime_stun_remaining"] = remaining
            snap["runtime_hitstop_remaining"] = freeze

            is_new_contact = bool(
                assigned > 0
                and (
                    previous is None
                    or previous.assigned <= 0
                    or assigned > previous.assigned
                    or remaining > (previous.remaining + 1)
                )
            )

            outcome = self._classify(snap, previous)
            if is_new_contact:
                pending = _PendingContact(
                    target_slot=str(slot),
                    attacker=self._pick_attacker(str(slot), snap, frame),
                    assigned=assigned,
                    freeze=freeze,
                    started_frame=frame,
                    outcome=outcome,
                )
                self._pending[str(slot)] = pending

            pending = self._pending.get(str(slot))
            if pending is not None:
                if outcome and pending.outcome is None:
                    pending.outcome = outcome
                if pending.outcome:
                    changed = self._finalize_pending(str(slot), pending, pending.outcome, frame) or changed
                elif frame - pending.started_frame >= PENDING_RESULT_FRAMES:
                    # Do not guess an outcome. A future confirmed hit/block can
                    # still profile the same move cleanly.
                    self._finalize_pending(str(slot), pending, None, frame)

            self._prev_target[str(slot)] = current

        # Match ends/character swaps can otherwise leave a stale pending contact.
        for slot in list(self._prev_target):
            if slot not in live_slots:
                self._prev_target.pop(slot, None)
                self._pending.pop(slot, None)

        return changed

    def flush(self) -> bool:
        """Write any accumulated evidence, useful during orderly shutdown."""
        return _write_doc_atomic(self.path, self._doc)
