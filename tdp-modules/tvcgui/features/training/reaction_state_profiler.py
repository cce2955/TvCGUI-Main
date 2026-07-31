"""Read-only profiler for TvC hitstun, knockdown, and wake-up transitions.

The recomp confirms that the ordinary victim timing cluster is not two copies
of the same hitstun timer:

* ``fighter + 0x1204`` is resolved blockstun.
* ``fighter + 0x1210`` is resolved hitstun and the live countdown.
* ``fighter + 0x1228`` is a separate countdown copied from hitstun only for
  reaction-family value ``0x300`` at ``fighter + 0x4480``.

This profiler records complete reaction sequences rather than one CSV row per
countdown tick. It is observation-only and never writes Dolphin memory.
"""
from __future__ import annotations

import csv
import json
import os
import struct
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    from tvcgui.platform.dolphin import rbytes
except Exception:
    def rbytes(_address: int, _size: int):
        return b""

from tvcgui.core.paths import user_data_path
from tvcgui.runtime.deferred_work import DeferredWorkLoop

PROFILE_VERSION = 1
PROFILE_FILE = "runtime_reaction_profiles.json"
EVENT_FILE = "runtime_reaction_events.csv"
SAMPLING_MODE = "fighter_reaction_state_machine_v1"

ACTION_FRAME_OFF = 0x01D8
ACTION_ID_OFF = 0x01E8
BLOCK_START = 0x11C4
BLOCK_SIZE = 0x70
REACTION_FAMILY_START = 0x4480
REACTION_FAMILY_SIZE = 0x0C

OFF_COMBO_COUNT = 0x11C4
OFF_BLOCKSTUN = 0x1204
OFF_HITSTUN = 0x1210
OFF_TIMER_1218 = 0x1218
OFF_TIMER_121C = 0x121C
OFF_TIMER_1220 = 0x1220
OFF_TIMER_1224 = 0x1224
OFF_REACTION_TIMER = 0x1228
OFF_TIMER_122C = 0x122C
OFF_REACTION_FAMILY = 0x4480
OFF_REACTION_STACK = 0x4484
OFF_REACTION_MODE = 0x4488

QUIET_FRAMES_TO_CLOSE = 3
WRITE_INTERVAL_SEC = 0.75
MAX_SEQUENCE_HISTORY = 10000

# System action IDs already identified by the project's universal action map.
REACTION_ACTION_NAMES = {
    48: "Block",
    49: "Crouching Block",
    50: "Air Block",
    51: "Pushblock",
    52: "Air Pushblock",
    53: "Crouching Pushblock",
    55: "Puddle Slip Start",
    56: "Puddle Slip Start",
    57: "Crouching Hit Reaction",
    58: "Crouching Turn",
    59: "Crouching Rising Reaction",
    60: "Sweep Reaction",
    61: "Stunned",
    62: "Stumble",
    64: "Standing Hitstun",
    65: "Overhead Hitstun",
    66: "Low Hitstun",
    67: "Air T-Pose Hit",
    69: "Airborne KO",
    70: "Wall Bounce Interim",
    73: "Knockdown Face Up",
    74: "Knockdown Face Down",
    75: "Crumple",
    76: "Crumple",
    77: "Airborne Bounce",
    79: "Stagger",
    80: "Hard Knockdown",
    81: "Bounce Launch",
    82: "Bounce Off",
    83: "Spiral",
    84: "Giant Stun",
    85: "Puddle Slip",
    86: "Swept",
    87: "Unknown Reaction 87",
    88: "Camera Spiral",
    89: "Spiral Knockdown",
    90: "Hard Knockdown",
    91: "Soft Knockdown",
    92: "Hard Knockdown",
    93: "Snapback",
    94: "Back Turn",
    95: "Forced Roll",
    96: "Soft Knockdown",
    97: "Launched",
    98: "Hard Knockdown",
    99: "Aerial T-Pose",
    101: "Air Heavy Hitstun",
    102: "Knockdown Face Up",
    103: "Wakeup Transition",
    104: "Knockdown Face Down",
    105: "OTG Face Up",
    106: "OTG Face Down",
    108: "Air Knockdown Bounce",
    109: "Air Knockdown Bounce Face Down",
    113: "Get Up Face Up",
    115: "Forward Roll",
    116: "Backward Roll",
    119: "Get Up Face Down",
    124: "Stagger 124",
    126: "Breakaway",
    128: "Heavy Reaction",
    129: "Heavy Overhead Reaction",
    130: "Stance Swap Reaction",
    132: "Knockdown To Stun",
    133: "Backroll",
    142: "Face-Up Bounce",
    154: "KO",
    155: "Slow-Motion KO",
    158: "Forward Slow-Motion KO",
    159: "Invisible KO Transition",
    160: "Air Recovery",
    161: "Hard Knockdown",
    165: "Breakaway Recovery",
}

BLOCK_ACTIONS = {48, 49, 50, 51, 52, 53}
KNOCKDOWN_ACTIONS = {
    70, 73, 74, 75, 76, 77, 80, 81, 82, 83, 89, 90, 91, 92,
    93, 95, 96, 98, 102, 104, 105, 106, 108, 109, 132, 142, 161,
}
WAKEUP_ACTIONS = {103, 113, 115, 116, 119, 133}
AIR_RECOVERY_ACTIONS = {126, 160, 165}
KO_ACTIONS = {69, 154, 155, 158, 159}
REACTION_ACTIONS = set(REACTION_ACTION_NAMES)

CSV_FIELDS = [
    "timestamp_utc", "start_frame", "end_frame", "duration_frames",
    "slot", "team", "fighter_base", "char_id", "character_name",
    "sequence_kind", "start_action_id", "start_action_name",
    "end_action_id", "end_action_name", "action_path", "phase_path",
    "max_combo_count", "max_blockstun", "max_hitstun",
    "max_reaction_timer", "max_timer_1218", "max_timer_121c",
    "hitstun_decrement_frames", "hitstun_hold_frames",
    "entered_knockdown", "entered_wakeup", "entered_air_recovery",
    "knockdown_start_offset", "wakeup_start_offset",
    "air_recovery_start_offset", "recovery_offset",
    "reaction_family_path", "reaction_stack_path", "reaction_mode_path",
    "f062_path", "f063_path", "f064_path", "f072_path", "notes",
]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _u32be(blob: bytes, offset: int) -> int:
    if not isinstance(blob, (bytes, bytearray)) or offset < 0 or offset + 4 > len(blob):
        return 0
    return struct.unpack_from(">I", blob, offset)[0]


def _s32(value: int) -> int:
    raw = int(value) & 0xFFFFFFFF
    return raw - 0x100000000 if raw & 0x80000000 else raw


def _hex32(value: int) -> str:
    return f"0x{int(value) & 0xFFFFFFFF:08X}"


def _profile_path() -> Path:
    return Path(user_data_path("runtime")) / PROFILE_FILE


def _event_path() -> Path:
    return Path(user_data_path("runtime")) / EVENT_FILE


def _empty_doc() -> dict:
    return {
        "version": PROFILE_VERSION,
        "sampling_mode": SAMPLING_MODE,
        "updated_utc": "",
        "sequences": [],
        "signatures": {},
    }


def _read_doc(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_doc()
    if not isinstance(doc, dict):
        return _empty_doc()
    doc.setdefault("sequences", [])
    doc.setdefault("signatures", {})
    doc["version"] = PROFILE_VERSION
    doc["sampling_mode"] = SAMPLING_MODE
    return doc


def _write_json_atomic(path: Path, doc: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(doc)
        payload["version"] = PROFILE_VERSION
        payload["sampling_mode"] = SAMPLING_MODE
        payload["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temp_name, path)
        finally:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except Exception:
                pass
        return True
    except Exception:
        return False


def _append_csv(path: Path, rows: list[dict]) -> bool:
    if not rows:
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = path.exists() and path.stat().st_size > 0
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
            if not exists:
                writer.writeheader()
            writer.writerows(rows)
        return True
    except Exception:
        return False


def action_name(action_id: int) -> str:
    return REACTION_ACTION_NAMES.get(int(action_id), f"Action {int(action_id)}")


def phase_for(action_id: int, blockstun: int, hitstun: int) -> str:
    action = int(action_id)
    if action in KO_ACTIONS:
        return "ko"
    if action in AIR_RECOVERY_ACTIONS:
        return "air_recovery"
    if action in WAKEUP_ACTIONS:
        return "wakeup"
    if action in KNOCKDOWN_ACTIONS:
        return "knockdown"
    if int(blockstun) > 0 or action in BLOCK_ACTIONS:
        return "blockstun"
    if int(hitstun) > 0 or action in REACTION_ACTIONS:
        return "hitstun"
    return "neutral"


def is_reaction_active(state: "ReactionState") -> bool:
    return bool(
        state.blockstun > 0
        or state.hitstun > 0
        or state.reaction_timer > 0
        or state.action_id in REACTION_ACTIONS
    )


@dataclass(frozen=True)
class ReactionState:
    base: int
    char_id: int
    name: str
    action_id: int
    action_frame: int
    combo_count: int
    blockstun: int
    hitstun: int
    timer_1218: int
    timer_121c: int
    timer_1220: int
    timer_1224: int
    reaction_timer: int
    timer_122c: int
    reaction_family: int
    reaction_stack: int
    reaction_mode: int
    f062: int
    f063: int
    f064: int
    f072: int

    @property
    def phase(self) -> str:
        return phase_for(self.action_id, self.blockstun, self.hitstun)


@dataclass
class ReactionSequence:
    slot: str
    team: str
    base: int
    char_id: int
    name: str
    start_frame: int
    start_action_id: int
    sequence_kind: str
    last_frame: int
    quiet_frames: int = 0
    max_combo_count: int = 0
    max_blockstun: int = 0
    max_hitstun: int = 0
    max_reaction_timer: int = 0
    max_timer_1218: int = 0
    max_timer_121c: int = 0
    hitstun_decrement_frames: int = 0
    hitstun_hold_frames: int = 0
    knockdown_start: Optional[int] = None
    wakeup_start: Optional[int] = None
    air_recovery_start: Optional[int] = None
    actions: list[int] = field(default_factory=list)
    phases: list[str] = field(default_factory=list)
    reaction_families: list[int] = field(default_factory=list)
    reaction_stacks: list[int] = field(default_factory=list)
    reaction_modes: list[int] = field(default_factory=list)
    f062_values: list[int] = field(default_factory=list)
    f063_values: list[int] = field(default_factory=list)
    f064_values: list[int] = field(default_factory=list)
    f072_values: list[int] = field(default_factory=list)


class RuntimeReactionStateProfiler:
    """Capture complete hitstun, knockdown, wake-up, and recovery paths."""

    def __init__(
        self,
        path: Optional[Path] = None,
        event_path: Optional[Path] = None,
        *,
        read_block: Optional[Callable[[int, int], bytes]] = None,
        emit_console: bool = False,
    ) -> None:
        self.path = Path(path or _profile_path())
        self.event_path = Path(event_path or _event_path())
        self._read_block = read_block or rbytes
        self.emit_console = bool(emit_console)
        self.doc = _read_doc(self.path)
        self._previous: Dict[str, ReactionState] = {}
        self._active: Dict[str, ReactionSequence] = {}
        self._last_sequence: Dict[str, dict] = {}
        self._pending_csv: list[dict] = []
        self._dirty = False
        self._lock = threading.RLock()
        self._writer = DeferredWorkLoop(
            lambda: self._write_pending(force=False),
            interval=WRITE_INTERVAL_SEC,
            name="TvCReactionWriter",
        )

    @staticmethod
    def _team(slot: str, snap: dict) -> str:
        team = str(snap.get("teamtag") or "").upper()
        if team in {"P1", "P2"}:
            return team
        return "P1" if str(slot).upper().startswith("P1") else "P2"

    @staticmethod
    def _append_unique(values: list, value: Any) -> None:
        if not values or values[-1] != value:
            values.append(value)

    def _state_from_snapshot(self, snap: dict) -> Optional[ReactionState]:
        base = _safe_int(snap.get("base"), 0)
        if base <= 0:
            return None
        try:
            timing = self._read_block(base + BLOCK_START, BLOCK_SIZE) or b""
            family = self._read_block(base + REACTION_FAMILY_START, REACTION_FAMILY_SIZE) or b""
        except Exception:
            return None
        if len(timing) < BLOCK_SIZE or len(family) < REACTION_FAMILY_SIZE:
            return None

        def timing_u32(offset: int) -> int:
            return _u32be(timing, offset - BLOCK_START)

        action_id = _safe_int(
            snap.get("timing_action_id")
            or snap.get("mv_id_display")
            or snap.get("attA")
            or snap.get("attB"),
            0,
        ) & 0xFFFF
        action_frame = _safe_int(snap.get("move_frame") or snap.get("action_frame"), 0)
        return ReactionState(
            base=base,
            char_id=_safe_int(snap.get("id") or snap.get("csv_char_id"), 0),
            name=str(snap.get("name") or ""),
            action_id=action_id,
            action_frame=action_frame,
            combo_count=max(0, timing_u32(OFF_COMBO_COUNT)),
            blockstun=max(0, _s32(timing_u32(OFF_BLOCKSTUN))),
            hitstun=max(0, _s32(timing_u32(OFF_HITSTUN))),
            timer_1218=max(0, _s32(timing_u32(OFF_TIMER_1218))),
            timer_121c=max(0, _s32(timing_u32(OFF_TIMER_121C))),
            timer_1220=timing_u32(OFF_TIMER_1220),
            timer_1224=timing_u32(OFF_TIMER_1224),
            reaction_timer=max(0, _s32(timing_u32(OFF_REACTION_TIMER))),
            timer_122c=timing_u32(OFF_TIMER_122C),
            reaction_family=_u32be(family, OFF_REACTION_FAMILY - REACTION_FAMILY_START),
            reaction_stack=_u32be(family, OFF_REACTION_STACK - REACTION_FAMILY_START),
            reaction_mode=_u32be(family, OFF_REACTION_MODE - REACTION_FAMILY_START),
            f062=_safe_int(snap.get("f062"), -1),
            f063=_safe_int(snap.get("f063"), -1),
            f064=_safe_int(snap.get("f064"), -1),
            f072=_safe_int(snap.get("f072"), -1),
        )

    def _start_sequence(self, slot: str, snap: dict, state: ReactionState, frame: int) -> ReactionSequence:
        kind = "block" if state.phase == "blockstun" else "hit"
        seq = ReactionSequence(
            slot=str(slot),
            team=self._team(str(slot), snap),
            base=state.base,
            char_id=state.char_id,
            name=state.name,
            start_frame=int(frame),
            start_action_id=state.action_id,
            sequence_kind=kind,
            last_frame=int(frame),
        )
        self._active[str(slot)] = seq
        return seq

    def _update_sequence(
        self,
        seq: ReactionSequence,
        state: ReactionState,
        previous: Optional[ReactionState],
        frame: int,
    ) -> None:
        seq.last_frame = int(frame)
        seq.max_combo_count = max(seq.max_combo_count, state.combo_count)
        seq.max_blockstun = max(seq.max_blockstun, state.blockstun)
        seq.max_hitstun = max(seq.max_hitstun, state.hitstun)
        seq.max_reaction_timer = max(seq.max_reaction_timer, state.reaction_timer)
        seq.max_timer_1218 = max(seq.max_timer_1218, state.timer_1218)
        seq.max_timer_121c = max(seq.max_timer_121c, state.timer_121c)

        self._append_unique(seq.actions, state.action_id)
        self._append_unique(seq.phases, state.phase)
        self._append_unique(seq.reaction_families, state.reaction_family)
        self._append_unique(seq.reaction_stacks, state.reaction_stack)
        self._append_unique(seq.reaction_modes, state.reaction_mode)
        self._append_unique(seq.f062_values, state.f062)
        self._append_unique(seq.f063_values, state.f063)
        self._append_unique(seq.f064_values, state.f064)
        self._append_unique(seq.f072_values, state.f072)

        if state.phase == "knockdown" and seq.knockdown_start is None:
            seq.knockdown_start = int(frame)
        if state.phase == "wakeup" and seq.wakeup_start is None:
            seq.wakeup_start = int(frame)
        if state.phase == "air_recovery" and seq.air_recovery_start is None:
            seq.air_recovery_start = int(frame)

        if previous is not None and previous.hitstun > 0:
            if state.hitstun == previous.hitstun - 1:
                seq.hitstun_decrement_frames += 1
            elif state.hitstun == previous.hitstun:
                seq.hitstun_hold_frames += 1

    def _serialize(self, seq: ReactionSequence, end_state: ReactionState, frame: int) -> dict:
        def path_int(values: list[int]) -> str:
            return " > ".join(str(int(value)) for value in values)

        def path_hex(values: list[int]) -> str:
            return " > ".join(_hex32(value) for value in values)

        action_path = " > ".join(
            f"{action} {action_name(action)}" for action in seq.actions
        )
        recovery_offset = int(frame) - int(seq.start_frame)
        notes: list[str] = []
        if seq.max_reaction_timer:
            notes.append("+0x1228 secondary reaction timer observed")
        if seq.hitstun_hold_frames:
            notes.append("+0x1210 held instead of decrementing on one or more sampled frames")
        if seq.char_id in {11, 22, 23, 24, 25}:
            notes.append("giant/boss character uses explicit hitstun update branch")

        return {
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "start_frame": int(seq.start_frame),
            "end_frame": int(frame),
            "duration_frames": max(0, int(frame) - int(seq.start_frame)),
            "slot": seq.slot,
            "team": seq.team,
            "fighter_base": _hex32(seq.base),
            "char_id": seq.char_id,
            "character_name": seq.name,
            "sequence_kind": seq.sequence_kind,
            "start_action_id": seq.start_action_id,
            "start_action_name": action_name(seq.start_action_id),
            "end_action_id": end_state.action_id,
            "end_action_name": action_name(end_state.action_id),
            "action_path": action_path,
            "phase_path": " > ".join(seq.phases),
            "max_combo_count": seq.max_combo_count,
            "max_blockstun": seq.max_blockstun,
            "max_hitstun": seq.max_hitstun,
            "max_reaction_timer": seq.max_reaction_timer,
            "max_timer_1218": seq.max_timer_1218,
            "max_timer_121c": seq.max_timer_121c,
            "hitstun_decrement_frames": seq.hitstun_decrement_frames,
            "hitstun_hold_frames": seq.hitstun_hold_frames,
            "entered_knockdown": seq.knockdown_start is not None,
            "entered_wakeup": seq.wakeup_start is not None,
            "entered_air_recovery": seq.air_recovery_start is not None,
            "knockdown_start_offset": "" if seq.knockdown_start is None else seq.knockdown_start - seq.start_frame,
            "wakeup_start_offset": "" if seq.wakeup_start is None else seq.wakeup_start - seq.start_frame,
            "air_recovery_start_offset": "" if seq.air_recovery_start is None else seq.air_recovery_start - seq.start_frame,
            "recovery_offset": recovery_offset,
            "reaction_family_path": path_hex(seq.reaction_families),
            "reaction_stack_path": path_int(seq.reaction_stacks),
            "reaction_mode_path": path_int(seq.reaction_modes),
            "f062_path": path_int(seq.f062_values),
            "f063_path": path_int(seq.f063_values),
            "f064_path": path_int(seq.f064_values),
            "f072_path": path_int(seq.f072_values),
            "notes": "; ".join(notes),
        }

    def _record(self, row: dict) -> None:
        with self._lock:
            sequences = self.doc.setdefault("sequences", [])
            sequences.append(dict(row))
            if len(sequences) > MAX_SEQUENCE_HISTORY:
                del sequences[:-MAX_SEQUENCE_HISTORY]

            signature = "|".join([
                str(row.get("char_id") or 0),
                str(row.get("sequence_kind") or ""),
                str(row.get("action_path") or ""),
                str(row.get("max_hitstun") or 0),
                str(row.get("max_blockstun") or 0),
                str(row.get("max_reaction_timer") or 0),
            ])
            item = self.doc.setdefault("signatures", {}).setdefault(signature, {
                "count": 0,
                "char_id": row.get("char_id"),
                "character_name": row.get("character_name"),
                "sequence_kind": row.get("sequence_kind"),
                "action_path": row.get("action_path"),
                "phase_path": row.get("phase_path"),
                "max_hitstun": row.get("max_hitstun"),
                "max_blockstun": row.get("max_blockstun"),
                "max_reaction_timer": row.get("max_reaction_timer"),
                "min_duration_frames": row.get("duration_frames"),
                "max_duration_frames": row.get("duration_frames"),
            })
            item["count"] = _safe_int(item.get("count"), 0) + 1
            duration = _safe_int(row.get("duration_frames"), 0)
            item["min_duration_frames"] = min(_safe_int(item.get("min_duration_frames"), duration), duration)
            item["max_duration_frames"] = max(_safe_int(item.get("max_duration_frames"), duration), duration)
            self._pending_csv.append(dict(row))
            self._dirty = True
        self._writer.request()
        if self.emit_console:
            print(
                f"[reaction] {row.get('slot')} {row.get('character_name')} "
                f"{row.get('phase_path')} {row.get('duration_frames')}f: "
                f"{row.get('action_path')}",
                flush=True,
            )

    def _annotate(self, snap: dict, state: ReactionState, seq: Optional[ReactionSequence]) -> None:
        snap["reaction_phase"] = state.phase
        snap["reaction_action_id"] = state.action_id
        snap["reaction_action_name"] = action_name(state.action_id)
        snap["reaction_blockstun_remaining"] = state.blockstun
        snap["reaction_hitstun_remaining"] = state.hitstun
        snap["reaction_secondary_timer"] = state.reaction_timer
        snap["reaction_family"] = _hex32(state.reaction_family)
        snap["reaction_sequence_active"] = seq is not None
        snap["reaction_sequence_age"] = 0 if seq is None else max(0, seq.last_frame - seq.start_frame)
        latest = self._last_sequence.get(str(snap.get("slot_label") or ""))
        if latest:
            snap["reaction_last_path"] = str(latest.get("action_path") or "")
            snap["reaction_last_duration"] = _safe_int(latest.get("duration_frames"), 0)

    def update(self, snaps: dict[str, dict], *, frame: int = 0, now: Optional[float] = None) -> bool:
        del now
        changed = False
        live_slots: set[str] = set()
        for slot, snap in (snaps or {}).items():
            if not isinstance(snap, dict):
                continue
            key = str(slot)
            live_slots.add(key)
            snap["slot_label"] = key
            state = self._state_from_snapshot(snap)
            if state is None:
                continue
            previous = self._previous.get(key)
            if previous is not None and previous.base != state.base:
                self._active.pop(key, None)
                previous = None
            self._previous[key] = state

            seq = self._active.get(key)
            active_now = is_reaction_active(state)
            if seq is None and active_now:
                seq = self._start_sequence(key, snap, state, int(frame))

            if seq is not None:
                self._update_sequence(seq, state, previous, int(frame))
                if active_now:
                    seq.quiet_frames = 0
                else:
                    seq.quiet_frames += 1
                    if seq.quiet_frames >= QUIET_FRAMES_TO_CLOSE:
                        row = self._serialize(seq, state, int(frame))
                        self._record(row)
                        self._last_sequence[key] = row
                        self._active.pop(key, None)
                        seq = None
                        changed = True
            self._annotate(snap, state, seq)

        for key in list(self._previous):
            if key not in live_slots:
                self._previous.pop(key, None)
                self._active.pop(key, None)
        return changed

    def _write_pending(self, *, force: bool) -> bool:
        del force
        with self._lock:
            if not self._dirty and not self._pending_csv:
                return True
            doc = json.loads(json.dumps(self.doc))
            rows = list(self._pending_csv)
        json_ok = _write_json_atomic(self.path, doc)
        csv_ok = _append_csv(self.event_path, rows)
        if json_ok and csv_ok:
            with self._lock:
                del self._pending_csv[:len(rows)]
                self._dirty = False
            return True
        return False

    def flush(self) -> bool:
        return self._write_pending(force=True)

    def close(self) -> None:
        try:
            self.flush()
        finally:
            self._writer.close()


__all__ = [
    "RuntimeReactionStateProfiler",
    "ReactionState",
    "REACTION_ACTION_NAMES",
    "phase_for",
    "is_reaction_active",
    "OFF_BLOCKSTUN",
    "OFF_HITSTUN",
    "OFF_REACTION_TIMER",
]
