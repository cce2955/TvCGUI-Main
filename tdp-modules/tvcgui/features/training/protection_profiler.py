"""Runtime observation for invulnerability and guard-point windows.

The runtime profiler uses four fighter fields that can be sampled without
patching the game:

* +0x1218, a fixed-point collision-exclusion countdown (0x100 per frame)
* +0x0058 bit 0x00400000, an attack-state prerequisite checked by the native eligibility resolver
* +0x0244 bits 0x02000000/0x04000000/0x08000000, accepted Mid/High/Low attacks
* +0x444C, a Baroque lockout value used by the full activation resolver
* +0x44A4, the move-local Baroque permission state

This module records the exact action frames where those fields are live and
stores the observations outside the static scanner cache. It never writes to
Dolphin memory.
"""
from __future__ import annotations

import json
import os
import struct
import tempfile
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

try:
    from tvcgui.platform.dolphin import rd32, rbytes
except Exception:
    def rd32(_addr: int):
        return None

    def rbytes(_addr: int, _size: int):
        return b""

from tvcgui.core.paths import user_data_path
from tvcgui.runtime.deferred_work import DeferredWorkLoop

PROFILE_VERSION = 11
PROFILE_FILE = "runtime_protection_profiles.json"

OFF_CUR_HP = 0x0028
OFF_ACTION_FRAME_FLOAT = 0x01D8
OFF_STATE_FLAGS = 0x0058
OFF_RESULT_FLAGS = 0x005C
OFF_GUARD_MASK = 0x0244
OFF_COLLISION_EXCLUSION = 0x1218
OFF_BAROQUE_CANCEL_TIMER = 0x444C
OFF_BAROQUE_PERMISSION = 0x44A4

BAROQUE_CANCEL_GATE_BIT = 0x00400000
GUARD_ENABLE_BIT = BAROQUE_CANCEL_GATE_BIT
GUARD_MID_BIT = 0x02000000
GUARD_HIGH_BIT = 0x04000000
GUARD_LOW_BIT = 0x08000000
GUARD_HEIGHT_MASK = GUARD_MID_BIT | GUARD_HIGH_BIT | GUARD_LOW_BIT
GUARD_TRIGGER_BIT = 0x00000080

# Two distinct native protection mechanisms share these fighter fields.
#
# +0x244 height bits plus +0x58/0x00400000 route a hit through the
# guard/counter resolver. Cactus Bunker uses that path and changes into its
# counter response.
#
# +0x244/0x00040000 suppresses hitstun while the move continues. If
# +0x58/0x00100000 is also present the hit is damage-negated (Casshan Knee).
# Without that no-damage bit the fighter still loses HP (Condor Battering Ram).
STUN_BYPASS_BIT = 0x00040000
NO_DAMAGE_GUARD_BIT = 0x00100000
GENERIC_PROTECTION_MARKER = 0x00000001

CONFIRMED_COUNTER_ACTIONS = {
    (27, 0x0139),  # Joe the Condor, Cactus Bunker A
    (27, 0x013A),  # Joe the Condor, Cactus Bunker B
    (27, 0x013B),  # Joe the Condor, Cactus Bunker C
}
CONFIRMED_GUARD_ACTIONS = {
    (2, 0x0136),   # Casshan, Knee A
    (2, 0x0137),   # Casshan, Knee B
    (2, 0x0138),   # Casshan, Knee C
}
CONFIRMED_ARMOR_ACTIONS = {
    (27, 0x0136),  # Joe the Condor, Battering Ram A
    (27, 0x0137),  # Joe the Condor, Battering Ram B
    (27, 0x0138),  # Joe the Condor, Battering Ram C
}

MIN_PLAYER_ACTION_ID = 0x0100
MAX_PLAYER_ACTION_ID = 0x1FFF
MAX_TRACKED_FRAME = 600
MAX_FINITE_EXCLUSION = 180

# The GUI loop is close to 60 Hz but is not phase-locked to Dolphin. Protection
# is therefore sampled by a small dedicated reader at four probes per expected
# game frame. Each probe reads one contiguous fighter block, so action id,
# action frame, armor flags, guard mask and exclusion timer all come from the
# same ReadProcessMemory snapshot. Duplicate probes inside one action frame are
# OR-merged. Skipped action frames are recorded and are never interpolated.
FRAME_SYNC_POLL_HZ = 360.0
FRAME_SYNC_POLL_INTERVAL = 1.0 / FRAME_SYNC_POLL_HZ
SAMPLING_MODE = "action_frame_synced_v11_baroque_permission_gate"

OFF_CHAR_ID = 0x0014
OFF_ACTION_ID = 0x01E8
OFF_BAROQUE_ACTIVE_PHASE = 0x44BC

# Reading one 17 KB fighter block at 240 Hz was needlessly expensive and could
# miss short-lived Baroque timers when Dolphin or the GUI was busy.  The sampler
# now reads three small regions and verifies the action/frame did not change
# between the first and last core read.  This keeps the observation coherent
# while cutting the transferred memory by more than 90 percent.
CORE_BLOCK_START = OFF_CHAR_ID
CORE_BLOCK_END = max(OFF_GUARD_MASK, OFF_ACTION_ID, OFF_ACTION_FRAME_FLOAT, OFF_RESULT_FLAGS) + 4
CORE_BLOCK_SIZE = CORE_BLOCK_END - CORE_BLOCK_START
INV_BLOCK_START = OFF_COLLISION_EXCLUSION
INV_BLOCK_SIZE = 4
BAROQUE_BLOCK_START = OFF_BAROQUE_CANCEL_TIMER
BAROQUE_BLOCK_END = max(OFF_BAROQUE_PERMISSION, OFF_BAROQUE_ACTIVE_PHASE) + 4
BAROQUE_BLOCK_SIZE = BAROQUE_BLOCK_END - BAROQUE_BLOCK_START


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _read_u32(addr: int) -> Optional[int]:
    try:
        value = rd32(int(addr))
    except Exception:
        value = None
    if value is None:
        return None
    return int(value) & 0xFFFFFFFF


def _decode_action_frame(word: Any) -> int:
    try:
        value = struct.unpack(">f", struct.pack(">I", int(word) & 0xFFFFFFFF))[0]
    except Exception:
        return 0
    if value != value or value < 0.0 or value > 4000.0:
        return 0
    return max(0, int(round(value - 1.0)))


def default_profile_path() -> Path:
    return Path(user_data_path("runtime")) / PROFILE_FILE


def _empty_doc() -> dict:
    return {"version": PROFILE_VERSION, "updated_utc": "", "moves": {}}


def _read_doc(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_doc()
    if not isinstance(raw, dict):
        return _empty_doc()
    if not isinstance(raw.get("moves"), dict):
        raw["moves"] = {}
    raw["version"] = PROFILE_VERSION
    raw.setdefault("updated_utc", "")
    return raw


def _write_doc_atomic(path: Path, doc: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(doc)
        payload["version"] = PROFILE_VERSION
        payload["updated_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
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


def _ranges(frames: Iterable[Any]) -> list[tuple[int, int]]:
    values = sorted({max(0, _safe_int(v)) for v in frames if 0 < _safe_int(v) <= MAX_TRACKED_FRAME})
    if not values:
        return []
    out: list[tuple[int, int]] = []
    start = prev = values[0]
    for value in values[1:]:
        if value == prev + 1:
            prev = value
            continue
        out.append((start, prev))
        start = prev = value
    out.append((start, prev))
    return out


def _baroque_permission_frames(
    values: dict[Any, Any],
    *,
    first_frame: int = 0,
    last_frame: int = 0,
) -> set[int]:
    """Convert sampled permission values into a continuous native window.

    The permission field is state-like. A missed poll between two nonzero
    samples does not represent a real closed frame, so internal gaps are filled
    only when both surrounding observations remain nonzero.
    """
    observed = sorted(
        (frame_no, _safe_int(raw_value))
        for raw_frame, raw_value in (values or {}).items()
        if 0 < (frame_no := _safe_int(raw_frame)) <= MAX_TRACKED_FRAME
    )
    if not observed:
        return set()

    out: set[int] = set()
    for index, (frame_no, value) in enumerate(observed):
        if value == 0:
            continue
        out.add(frame_no)
        if index + 1 >= len(observed):
            continue
        next_frame, next_value = observed[index + 1]
        if next_value == 0 or next_frame <= frame_no + 1:
            continue
        out.update(range(frame_no + 1, next_frame))

    lower = max(1, _safe_int(first_frame, 0))
    upper = min(MAX_TRACKED_FRAME, _safe_int(last_frame, 0))
    if lower and upper and lower <= upper:
        out = {frame_no for frame_no in out if lower <= frame_no <= upper}
    return out


def _baroque_frames_from_record(record: dict) -> set[int]:
    """Read Baroque frames from current and compatible runtime records."""
    direct = {
        _safe_int(value)
        for value in record.get("baroque_frames") or []
        if 0 < _safe_int(value) <= MAX_TRACKED_FRAME
    }
    mode = str(record.get("sampling_mode") or "")
    first_frame = _safe_int(record.get("capture_first_frame"), 0)
    last_frame = _safe_int(record.get("capture_last_frame"), 0)

    if mode == "action_frame_synced_v10_native_baroque_gate":
        permission_values = (
            record.get("baroque_aux_values")
            if isinstance(record.get("baroque_aux_values"), dict)
            else {}
        )
    else:
        permission_values = (
            record.get("baroque_permission_values")
            if isinstance(record.get("baroque_permission_values"), dict)
            else {}
        )

    derived = _baroque_permission_frames(
        permission_values,
        first_frame=first_frame,
        last_frame=last_frame,
    )
    return direct | derived


def _range_text(start: int, end: int) -> str:
    return f"{start}f" if start == end else f"{start}-{end}f"


def guard_mask_names(mask: Any) -> list[str]:
    value = _safe_int(mask) & GUARD_HEIGHT_MASK
    names: list[str] = []
    if value & GUARD_HIGH_BIT:
        names.append("High")
    if value & GUARD_MID_BIT:
        names.append("Mid")
    if value & GUARD_LOW_BIT:
        names.append("Low")
    return names


def guard_mask_short(mask: Any) -> str:
    value = _safe_int(mask) & GUARD_HEIGHT_MASK
    parts: list[str] = []
    if value & GUARD_HIGH_BIT:
        parts.append("H")
    if value & GUARD_MID_BIT:
        parts.append("M")
    if value & GUARD_LOW_BIT:
        parts.append("L")
    if parts:
        return "+".join(parts)
    return "ALL" if (_safe_int(mask) & GENERIC_PROTECTION_MARKER) else "?"


def _guard_segments(frame_masks: dict[Any, Any]) -> list[tuple[int, int, int]]:
    pairs: list[tuple[int, int]] = []
    for raw_frame, raw_mask in (frame_masks or {}).items():
        frame_no = _safe_int(raw_frame)
        raw_value = _safe_int(raw_mask)
        mask = raw_value & (GUARD_HEIGHT_MASK | GENERIC_PROTECTION_MARKER)
        if 0 < frame_no <= MAX_TRACKED_FRAME and mask:
            pairs.append((frame_no, mask))
    pairs.sort()
    if not pairs:
        return []
    out: list[tuple[int, int, int]] = []
    start = prev = pairs[0][0]
    mask = pairs[0][1]
    for frame_no, next_mask in pairs[1:]:
        if frame_no == prev + 1 and next_mask == mask:
            prev = frame_no
            continue
        out.append((start, prev, mask))
        start = prev = frame_no
        mask = next_mask
    out.append((start, prev, mask))
    return out


def _format_guard_segments(segments: Iterable[tuple[int, int, int]], suffix: str = "[R]") -> str:
    parts: list[str] = []
    for start, end, mask in segments:
        names = guard_mask_names(mask)
        if mask & GENERIC_PROTECTION_MARKER and not (mask & GUARD_HEIGHT_MASK):
            label = "Guard point (no damage)"
        else:
            label = "All-height guard point" if (mask & GUARD_HEIGHT_MASK) == GUARD_HEIGHT_MASK else "Guard point " + " + ".join(names)
        parts.append(f"{label} {_range_text(start, end)} {suffix}".strip())
    return " / ".join(parts)


def _format_counter_segments(segments: Iterable[tuple[int, int, int]], suffix: str = "[R]") -> str:
    parts: list[str] = []
    for start, end, mask in segments:
        names = guard_mask_names(mask)
        label = "All-height counter" if (mask & GUARD_HEIGHT_MASK) == GUARD_HEIGHT_MASK else "Counter " + " + ".join(names)
        if not names and not (mask & GUARD_HEIGHT_MASK):
            label = "Counter"
        parts.append(f"{label} {_range_text(start, end)} {suffix}".strip())
    return " / ".join(parts)


def _format_armor_segments(segments: Iterable[tuple[int, int, int]], suffix: str = "[R]") -> str:
    parts: list[str] = []
    for start, end, mask in segments:
        names = guard_mask_names(mask)
        if mask & GENERIC_PROTECTION_MARKER and not (mask & GUARD_HEIGHT_MASK):
            label = "Armor (takes damage)"
        else:
            label = "All-height armor" if (mask & GUARD_HEIGHT_MASK) == GUARD_HEIGHT_MASK else "Armor " + " + ".join(names)
        parts.append(f"{label} {_range_text(start, end)} {suffix}".strip())
    return " / ".join(parts)


def _format_baroque_ranges(ranges: Iterable[tuple[int, int]], suffix: str = "[R]") -> str:
    return " / ".join(
        f"Baroque cancel {_range_text(start, end)} {suffix}".strip()
        for start, end in ranges
    )


def is_confirmed_counter_action(char_id: Any, action_id: Any) -> bool:
    return (_safe_int(char_id), _safe_int(action_id)) in CONFIRMED_COUNTER_ACTIONS


def is_confirmed_guard_action(char_id: Any, action_id: Any) -> bool:
    return (_safe_int(char_id), _safe_int(action_id)) in CONFIRMED_GUARD_ACTIONS


def is_confirmed_armor_action(char_id: Any, action_id: Any) -> bool:
    return (_safe_int(char_id), _safe_int(action_id)) in CONFIRMED_ARMOR_ACTIONS


@dataclass
class _Track:
    slot: str
    char_id: int
    action_id: int
    action_name: str = ""
    last_frame: int = 0
    first_frame: int = 0
    sampled_frames: set[int] = field(default_factory=set)
    missed_frames: set[int] = field(default_factory=set)
    sample_counts: dict[int, int] = field(default_factory=dict)
    invuln_frames: set[int] = field(default_factory=set)
    guard_frames: dict[int, int] = field(default_factory=dict)
    guard_trigger_frames: set[int] = field(default_factory=set)
    counter_frames: dict[int, int] = field(default_factory=dict)
    counter_trigger_frames: set[int] = field(default_factory=set)
    armor_frames: dict[int, int] = field(default_factory=dict)
    armor_trigger_frames: set[int] = field(default_factory=set)
    armor_damage_frames: set[int] = field(default_factory=set)
    baroque_frames: set[int] = field(default_factory=set)
    baroque_timer_values: dict[int, int] = field(default_factory=dict)
    baroque_permission_values: dict[int, int] = field(default_factory=dict)
    baroque_aux_values: dict[int, int] = field(default_factory=dict)
    last_hp: int = 0


@dataclass
class _SlotMeta:
    base: int
    action_name: str = ""


@dataclass
class _LiveProtection:
    char_id: int = 0
    action_id: int = 0
    action_frame: int = 0
    invuln_active: bool = False
    invuln_remaining: int = 0
    guard_active: bool = False
    guard_mask: int = 0
    counter_active: bool = False
    counter_mask: int = 0
    armor_active: bool = False
    armor_mask: int = 0
    baroque_active: bool = False
    baroque_remaining: int = 0


def _segment_u32(block: bytes, segment_start: int, absolute_offset: int) -> Optional[int]:
    rel = int(absolute_offset) - int(segment_start)
    if rel < 0 or rel + 4 > len(block):
        return None
    try:
        return struct.unpack_from(">I", block, rel)[0]
    except Exception:
        return None


class RuntimeProtectionProfiler:
    """Collect game-frame keyed protection observations.

    ``update`` only publishes current fighter bases and decorates the HUD
    snapshots. A daemon reader samples those bases independently of the GUI
    clock. The worker is intentionally read-only.
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        *,
        read_block: Optional[Callable[[int, int], bytes]] = None,
        poll_hz: float = FRAME_SYNC_POLL_HZ,
        start_worker: bool = True,
    ):
        self.path = Path(path or default_profile_path())
        self.doc = _read_doc(self.path)
        self.tracks: Dict[str, _Track] = {}
        self.dirty = False
        self._change_serial = 0
        self._read_block = read_block or rbytes
        self._poll_interval = 1.0 / max(60.0, float(poll_hz or FRAME_SYNC_POLL_HZ))
        self._idle_poll_interval = 1.0 / 60.0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._slot_meta: Dict[str, _SlotMeta] = {}
        self._live: Dict[str, _LiveProtection] = {}
        self._changed_pending = False
        self._writer = DeferredWorkLoop(
            lambda: self._write_pending(force=False),
            interval=0.75,
            name="TvCProtectionWriter",
        )
        self._thread: Optional[threading.Thread] = None
        if start_worker:
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="TvCProtectionFrameSampler",
                daemon=True,
            )
            self._thread.start()

    def _write_pending(self, *, force: bool) -> bool:
        with self._lock:
            if not self.dirty and not force:
                return False
            serial = int(self._change_serial)
            doc_copy = json.loads(json.dumps(self.doc))
        ok = _write_doc_atomic(self.path, doc_copy)
        if ok:
            with self._lock:
                if self._change_serial == serial:
                    self.dirty = False
        return bool(ok)

    def _finalize_locked(
        self,
        slot: str,
        *,
        reason: str = "unknown",
        completed: bool = False,
        next_action_id: int = 0,
        interrupted_by_baroque: bool = False,
    ) -> bool:
        track = self.tracks.pop(str(slot), None)
        self._live.pop(str(slot), None)
        if track is None or not track.sampled_frames:
            return False

        key = f"{track.char_id}:{track.action_id}"
        moves = self.doc.setdefault("moves", {})
        record = moves.get(key)
        if not isinstance(record, dict):
            record = {}

        # v1/v2 were GUI-clock samples and could leave false isolated boundary
        # frames. The first frame-synchronised capture replaces that legacy
        # evidence rather than unioning it forever. Later v3 observations merge.
        same_mode = str(record.get("sampling_mode") or "") == SAMPLING_MODE
        if same_mode:
            old_inv = {_safe_int(v) for v in record.get("invuln_frames") or []}
            old_guard = record.get("guard_frames") if isinstance(record.get("guard_frames"), dict) else {}
            old_triggers = {_safe_int(v) for v in record.get("guard_trigger_frames") or []}
            old_counter = record.get("counter_frames") if isinstance(record.get("counter_frames"), dict) else {}
            old_counter_triggers = {_safe_int(v) for v in record.get("counter_trigger_frames") or []}
            old_armor = record.get("armor_frames") if isinstance(record.get("armor_frames"), dict) else {}
            old_armor_triggers = {_safe_int(v) for v in record.get("armor_trigger_frames") or []}
            old_armor_damage = {_safe_int(v) for v in record.get("armor_damage_frames") or []}
            old_baroque = {_safe_int(v) for v in record.get("baroque_frames") or []}
            old_baroque_values = record.get("baroque_timer_values") if isinstance(record.get("baroque_timer_values"), dict) else {}
            old_baroque_permissions = record.get("baroque_permission_values") if isinstance(record.get("baroque_permission_values"), dict) else {}
            old_baroque_aux = record.get("baroque_aux_values") if isinstance(record.get("baroque_aux_values"), dict) else {}
            observations = _safe_int(record.get("observations"), 0)
            complete_observations = _safe_int(record.get("complete_observations"), 0)
        else:
            old_inv = set()
            old_guard = {}
            old_triggers = set()
            old_counter = {}
            old_counter_triggers = set()
            old_armor = {}
            old_armor_triggers = set()
            old_armor_damage = set()
            old_baroque = set()
            old_baroque_values = {}
            old_baroque_permissions = {}
            old_baroque_aux = {}
            observations = 0
            complete_observations = 0

        old_inv.update(track.invuln_frames)
        old_inv = {v for v in old_inv if 0 < v <= MAX_TRACKED_FRAME}

        merged_guard = {str(_safe_int(k)): _safe_int(v) & (GUARD_HEIGHT_MASK | GENERIC_PROTECTION_MARKER) for k, v in old_guard.items() if _safe_int(k) > 0}
        for frame_no, mask in track.guard_frames.items():
            merged_guard[str(int(frame_no))] = int(mask) & (GUARD_HEIGHT_MASK | GENERIC_PROTECTION_MARKER)
        old_triggers.update(track.guard_trigger_frames)
        old_triggers = {v for v in old_triggers if 0 < v <= MAX_TRACKED_FRAME}

        merged_counter = {str(_safe_int(k)): _safe_int(v) & (GUARD_HEIGHT_MASK | GENERIC_PROTECTION_MARKER) for k, v in old_counter.items() if _safe_int(k) > 0}
        for frame_no, mask in track.counter_frames.items():
            merged_counter[str(int(frame_no))] = int(mask) & (GUARD_HEIGHT_MASK | GENERIC_PROTECTION_MARKER)
        old_counter_triggers.update(track.counter_trigger_frames)
        old_counter_triggers = {v for v in old_counter_triggers if 0 < v <= MAX_TRACKED_FRAME}

        merged_armor = {str(_safe_int(k)): _safe_int(v) & (GUARD_HEIGHT_MASK | GENERIC_PROTECTION_MARKER) for k, v in old_armor.items() if _safe_int(k) > 0}
        for frame_no, mask in track.armor_frames.items():
            merged_armor[str(int(frame_no))] = int(mask) & (GUARD_HEIGHT_MASK | GENERIC_PROTECTION_MARKER)
        old_armor_triggers.update(track.armor_trigger_frames)
        old_armor_triggers = {v for v in old_armor_triggers if 0 < v <= MAX_TRACKED_FRAME}
        old_armor_damage.update(track.armor_damage_frames)
        old_armor_damage = {v for v in old_armor_damage if 0 < v <= MAX_TRACKED_FRAME}

        old_baroque.update(track.baroque_frames)
        old_baroque = {v for v in old_baroque if 0 < v <= MAX_TRACKED_FRAME}
        merged_baroque_values = {
            str(_safe_int(k)): max(0, _safe_int(v))
            for k, v in old_baroque_values.items()
            if 0 < _safe_int(k) <= MAX_TRACKED_FRAME
        }
        for frame_no, remaining in track.baroque_timer_values.items():
            frame_key = str(int(frame_no))
            merged_baroque_values[frame_key] = int(remaining) & 0xFFFFFFFF
        merged_baroque_permissions = {
            str(_safe_int(k)): _safe_int(v) & 0xFFFFFFFF
            for k, v in old_baroque_permissions.items()
            if 0 < _safe_int(k) <= MAX_TRACKED_FRAME
        }
        for frame_no, permission in track.baroque_permission_values.items():
            merged_baroque_permissions[str(int(frame_no))] = int(permission) & 0xFFFFFFFF
        merged_baroque_aux = {
            str(_safe_int(k)): _safe_int(v) & 0xFFFFFFFF
            for k, v in old_baroque_aux.items()
            if 0 < _safe_int(k) <= MAX_TRACKED_FRAME
        }
        for frame_no, value in track.baroque_aux_values.items():
            merged_baroque_aux[str(int(frame_no))] = int(value) & 0xFFFFFFFF

        sampled = sorted(v for v in track.sampled_frames if 0 < v <= MAX_TRACKED_FRAME)
        missed = sorted(v for v in track.missed_frames if 0 < v <= MAX_TRACKED_FRAME)
        counts = {str(k): int(v) for k, v in sorted(track.sample_counts.items()) if 0 < int(k) <= MAX_TRACKED_FRAME}

        record.update({
            "char_id": int(track.char_id),
            "action_id": int(track.action_id),
            "action_name": str(track.action_name or record.get("action_name") or ""),
            "sampling_mode": SAMPLING_MODE,
            "poll_hz": round(1.0 / self._poll_interval, 2),
            "sampled_frames": sampled,
            "missed_frames": missed,
            "samples_per_frame": counts,
            "capture_first_frame": int(sampled[0]) if sampled else 0,
            "capture_last_frame": int(sampled[-1]) if sampled else 0,
            "complete_between_first_last": not bool(missed),
            "invuln_frames": sorted(old_inv),
            "guard_frames": dict(sorted(merged_guard.items(), key=lambda item: _safe_int(item[0]))),
            "guard_trigger_frames": sorted(old_triggers),
            "counter_frames": dict(sorted(merged_counter.items(), key=lambda item: _safe_int(item[0]))),
            "counter_trigger_frames": sorted(old_counter_triggers),
            "armor_frames": dict(sorted(merged_armor.items(), key=lambda item: _safe_int(item[0]))),
            "armor_trigger_frames": sorted(old_armor_triggers),
            "armor_damage_frames": sorted(old_armor_damage),
            "baroque_frames": sorted(old_baroque),
            "baroque_timer_values": dict(sorted(merged_baroque_values.items(), key=lambda item: _safe_int(item[0]))),
            "baroque_permission_values": dict(sorted(merged_baroque_permissions.items(), key=lambda item: _safe_int(item[0]))),
            "baroque_aux_values": dict(sorted(merged_baroque_aux.items(), key=lambda item: _safe_int(item[0]))),
            "observations": observations + 1,
            "complete_observations": complete_observations + (1 if completed else 0),
            "capture_completed": bool(complete_observations + (1 if completed else 0)),
            "last_capture_complete": bool(completed),
            "last_capture_end_reason": str(reason or "unknown"),
            "last_capture_next_action_id": int(next_action_id or 0),
            "last_capture_interrupted_by_baroque": bool(interrupted_by_baroque),
            "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        moves[key] = record
        self.dirty = True
        self._change_serial += 1
        self._writer.request()
        self._changed_pending = True
        return True

    def _sample_slot(self, slot: str, meta: _SlotMeta) -> None:
        base = int(meta.base)

        def _read_segment(start: int, size: int) -> bytes:
            try:
                blob = self._read_block(base + int(start), int(size))
            except Exception:
                return b""
            if not blob or len(blob) < int(size):
                return b""
            return bytes(blob[: int(size)])

        # Read the small core twice around the two remote fields. If the action
        # or action frame changed during the sequence, discard this probe and
        # retry on the next 360 Hz pass instead of combining two game frames.
        core = _read_segment(CORE_BLOCK_START, CORE_BLOCK_SIZE)
        if not core:
            return
        inv_block = _read_segment(INV_BLOCK_START, INV_BLOCK_SIZE)
        baroque_block = _read_segment(BAROQUE_BLOCK_START, BAROQUE_BLOCK_SIZE)
        core_verify = _read_segment(CORE_BLOCK_START, CORE_BLOCK_SIZE)
        if not core_verify:
            return

        char_id = _safe_int(_segment_u32(core, CORE_BLOCK_START, OFF_CHAR_ID), 0)
        action_id = _safe_int(_segment_u32(core, CORE_BLOCK_START, OFF_ACTION_ID), 0)
        action_frame = _decode_action_frame(_segment_u32(core, CORE_BLOCK_START, OFF_ACTION_FRAME_FLOAT) or 0)
        verify_char = _safe_int(_segment_u32(core_verify, CORE_BLOCK_START, OFF_CHAR_ID), 0)
        verify_action = _safe_int(_segment_u32(core_verify, CORE_BLOCK_START, OFF_ACTION_ID), 0)
        verify_frame = _decode_action_frame(_segment_u32(core_verify, CORE_BLOCK_START, OFF_ACTION_FRAME_FLOAT) or 0)
        if (char_id, action_id, action_frame) != (verify_char, verify_action, verify_frame):
            return

        current_hp = _safe_int(_segment_u32(core, CORE_BLOCK_START, OFF_CUR_HP), 0)
        inv_raw = _segment_u32(inv_block, INV_BLOCK_START, OFF_COLLISION_EXCLUSION) or 0
        flag58 = _segment_u32(core, CORE_BLOCK_START, OFF_STATE_FLAGS) or 0
        mask244 = _segment_u32(core, CORE_BLOCK_START, OFF_GUARD_MASK) or 0
        result5c = _segment_u32(core, CORE_BLOCK_START, OFF_RESULT_FLAGS) or 0
        baroque_timer = _safe_int(
            _segment_u32(baroque_block, BAROQUE_BLOCK_START, OFF_BAROQUE_CANCEL_TIMER), 0
        )
        baroque_permission = _safe_int(
            _segment_u32(baroque_block, BAROQUE_BLOCK_START, OFF_BAROQUE_PERMISSION), 0
        )
        baroque_active_phase = _safe_int(
            _segment_u32(baroque_block, BAROQUE_BLOCK_START, OFF_BAROQUE_ACTIVE_PHASE), 0
        )

        valid = bool(
            char_id > 0
            and MIN_PLAYER_ACTION_ID <= action_id <= MAX_PLAYER_ACTION_ID
            and 0 < action_frame <= MAX_TRACKED_FRAME
        )

        with self._lock:
            if not valid:
                previous = self.tracks.get(slot)
                completed = bool(
                    previous is not None
                    and char_id > 0
                    and not baroque_active_phase
                )
                self._finalize_locked(
                    slot,
                    reason="left_attack_action" if char_id > 0 else "fighter_unavailable",
                    completed=completed,
                    next_action_id=action_id,
                    interrupted_by_baroque=bool(baroque_active_phase),
                )
                return

            track = self.tracks.get(slot)
            action_changed = bool(track is not None and track.action_id != action_id)
            char_changed = bool(track is not None and track.char_id != char_id)
            frame_reset = bool(
                track is not None
                and track.last_frame > 0
                and action_frame + 1 < track.last_frame
            )
            if track is None or char_changed or action_changed or frame_reset:
                if track is not None:
                    natural_transition = bool(frame_reset and not char_changed and not baroque_active_phase)
                    self._finalize_locked(
                        slot,
                        reason=(
                            "character_change" if char_changed
                            else "action_transition" if action_changed
                            else "action_frame_reset"
                        ),
                        completed=natural_transition,
                        next_action_id=action_id,
                        interrupted_by_baroque=bool(baroque_active_phase),
                    )
                track = _Track(
                    slot=slot,
                    char_id=char_id,
                    action_id=action_id,
                    action_name=str(meta.action_name or ""),
                    first_frame=action_frame,
                )
                self.tracks[slot] = track

            if meta.action_name:
                track.action_name = str(meta.action_name)
            if track.last_frame > 0 and action_frame > track.last_frame + 1:
                track.missed_frames.update(range(track.last_frame + 1, action_frame))
            track.last_frame = max(track.last_frame, action_frame)
            if track.first_frame <= 0:
                track.first_frame = action_frame
            track.sampled_frames.add(action_frame)
            track.sample_counts[action_frame] = int(track.sample_counts.get(action_frame, 0)) + 1

            remaining = (int(inv_raw) + 0xFF) // 0x100 if inv_raw else 0
            invuln_active = bool(0 < remaining <= MAX_FINITE_EXCLUSION)
            if invuln_active:
                track.invuln_frames.add(action_frame)

            height_mask = int(mask244) & GUARD_HEIGHT_MASK
            resolver_enabled = bool(int(flag58) & GUARD_ENABLE_BIT)
            stun_bypass = bool(int(mask244) & STUN_BYPASS_BIT)
            no_damage = bool(int(flag58) & NO_DAMAGE_GUARD_BIT)
            counter_action = is_confirmed_counter_action(char_id, action_id)
            guard_action = is_confirmed_guard_action(char_id, action_id)
            armor_action = is_confirmed_armor_action(char_id, action_id)

            counter_active = bool(resolver_enabled and height_mask and counter_action)
            guard_active = bool(
                resolver_enabled
                and (
                    (height_mask and not counter_action)
                    or (stun_bypass and (no_damage or guard_action))
                )
            )
            armor_active = bool(
                resolver_enabled
                and stun_bypass
                and not guard_active
                and (armor_action or not no_damage)
            )

            guard_marker = height_mask or GENERIC_PROTECTION_MARKER
            armor_marker = height_mask or GENERIC_PROTECTION_MARKER
            counter_marker = height_mask or GENERIC_PROTECTION_MARKER
            if guard_active:
                track.guard_frames[action_frame] = int(track.guard_frames.get(action_frame, 0)) | guard_marker
            if counter_active:
                track.counter_frames[action_frame] = int(track.counter_frames.get(action_frame, 0)) | counter_marker
            if armor_active:
                track.armor_frames[action_frame] = int(track.armor_frames.get(action_frame, 0)) | armor_marker
                if track.last_hp > 0 and current_hp > 0 and current_hp < track.last_hp:
                    track.armor_damage_frames.add(action_frame)
            if resolver_enabled and (int(result5c) & GUARD_TRIGGER_BIT):
                if counter_active:
                    track.counter_trigger_frames.add(action_frame)
                elif guard_active:
                    track.guard_trigger_frames.add(action_frame)
                elif armor_active:
                    track.armor_trigger_frames.add(action_frame)

            # +0x44A4 is the move-local Baroque permission state. The full
            # activation resolver also checks attack state, lockouts, health,
            # input, and other global restrictions, but those do not define
            # the move's cancel window. Record the permission state directly.
            baroque_gate_value = int(flag58) & BAROQUE_CANCEL_GATE_BIT
            track.baroque_timer_values[action_frame] = int(baroque_timer) & 0xFFFFFFFF
            track.baroque_permission_values[action_frame] = int(baroque_permission) & 0xFFFFFFFF
            track.baroque_aux_values[action_frame] = int(baroque_gate_value)
            baroque_active = bool(int(baroque_permission) != 0)
            if baroque_active:
                track.baroque_frames.add(action_frame)

            if current_hp > 0:
                track.last_hp = current_hp

            self._live[slot] = _LiveProtection(
                char_id=char_id,
                action_id=action_id,
                action_frame=action_frame,
                invuln_active=invuln_active,
                invuln_remaining=int(remaining) if invuln_active else 0,
                guard_active=guard_active,
                guard_mask=guard_marker if guard_active else 0,
                counter_active=counter_active,
                counter_mask=counter_marker if counter_active else 0,
                armor_active=armor_active,
                armor_mask=armor_marker if armor_active else 0,
                baroque_active=baroque_active,
                baroque_remaining=0,
            )

    def sample_once(self) -> None:
        with self._lock:
            metas = dict(self._slot_meta)
        for slot, meta in metas.items():
            self._sample_slot(str(slot), meta)
        with self._lock:
            for slot in list(self.tracks):
                if slot not in metas:
                    self._finalize_locked(slot, reason="slot_unavailable", completed=False)

    def _worker_loop(self) -> None:
        deadline = time.perf_counter()
        while not self._stop.is_set():
            self.sample_once()
            with self._lock:
                active = bool(self.tracks)
            deadline += self._poll_interval if active else self._idle_poll_interval
            delay = deadline - time.perf_counter()
            if delay <= 0:
                deadline = time.perf_counter()
                delay = 0.0005
            self._stop.wait(delay)

    def update(self, snaps: dict[str, dict], *, frame: int = 0, now: Optional[float] = None) -> bool:
        del frame, now
        metas: Dict[str, _SlotMeta] = {}
        for slot, snap in (snaps or {}).items():
            if not isinstance(snap, dict):
                continue
            base = _safe_int(snap.get("base"), 0)
            if base:
                metas[str(slot)] = _SlotMeta(
                    base=base,
                    action_name=str(snap.get("mv_label_display") or snap.get("mv_label") or ""),
                )

        with self._lock:
            self._slot_meta = metas
            live_copy = dict(self._live)
            changed = bool(self._changed_pending)
            self._changed_pending = False
            dirty = bool(self.dirty)
            doc_copy = self.doc if dirty else None

        # Publish only a coherent worker sample matching the snapshot's current
        # fighter base/action. Rendering never performs another protection read.
        for slot, snap in (snaps or {}).items():
            if not isinstance(snap, dict):
                continue
            live = live_copy.get(str(slot))
            current_action = 0
            for key in ("attA", "attB", "timing_action_id", "move_id"):
                current_action = _safe_int(snap.get(key), 0)
                if current_action:
                    break
            if live is None or (current_action and live.action_id != current_action):
                snap["protection_invuln_active"] = False
                snap["protection_invuln_remaining"] = 0
                snap["protection_guard_active"] = False
                snap["protection_guard_mask"] = 0
                snap["protection_guard_text"] = ""
                snap["protection_counter_active"] = False
                snap["protection_counter_mask"] = 0
                snap["protection_counter_text"] = ""
                snap["protection_armor_active"] = False
                snap["protection_armor_mask"] = 0
                snap["protection_armor_text"] = ""
                snap["baroque_cancel_active"] = False
                snap["baroque_cancel_remaining"] = 0
                continue
            snap["protection_sampled_action_frame"] = int(live.action_frame)
            snap["protection_sampling_mode"] = SAMPLING_MODE
            snap["protection_invuln_active"] = bool(live.invuln_active)
            snap["protection_invuln_remaining"] = int(live.invuln_remaining)
            snap["protection_guard_active"] = bool(live.guard_active)
            snap["protection_guard_mask"] = int(live.guard_mask)
            snap["protection_guard_text"] = guard_mask_short(live.guard_mask) if live.guard_active else ""
            snap["protection_counter_active"] = bool(live.counter_active)
            snap["protection_counter_mask"] = int(live.counter_mask)
            snap["protection_counter_text"] = guard_mask_short(live.counter_mask) if live.counter_active else ""
            snap["protection_armor_active"] = bool(live.armor_active)
            snap["protection_armor_mask"] = int(live.armor_mask)
            snap["protection_armor_text"] = guard_mask_short(live.armor_mask) if live.armor_active else ""
            snap["baroque_cancel_active"] = bool(live.baroque_active)
            snap["baroque_cancel_remaining"] = int(live.baroque_remaining)

        if dirty and doc_copy is not None:
            self._writer.request()
        return changed

    def flush(self) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        with self._lock:
            for slot in list(self.tracks):
                self._finalize_locked(slot, reason="shutdown", completed=False)
        result = {"ok": True}
        self._writer.close(
            final_callback=lambda: result.__setitem__("ok", self._write_pending(force=True)),
            timeout=1.5,
        )
        return bool(result["ok"])


def _baroque_capture_is_complete(record: dict, mv: dict) -> bool:
    """Return whether at least one full native action transition was observed.

    A move is complete when the game leaves that action without entering the
    active Baroque phase. Static MOT length is deliberately ignored because
    actions commonly transition before the final motion frame.
    """
    del mv
    if bool(record.get("capture_completed")):
        return True
    return _safe_int(record.get("complete_observations"), 0) > 0


def _clear_runtime_protection_overlay(mv: dict) -> None:
    """Remove stale runtime fields before applying the current profile version."""
    runtime_sources = (
        str(mv.get("counter_source") or "").startswith("runtime_observed")
        or str(mv.get("guard_point_source") or "").startswith("runtime_observed")
        or str(mv.get("armor_source") or "").startswith("runtime_observed")
        or str(mv.get("baroque_source") or "").startswith("runtime_observed")
        or str(mv.get("invuln_confidence") or "") == "runtime_observed"
        or isinstance(mv.get("runtime_protection"), dict)
    )
    mv.pop("runtime_protection", None)

    if str(mv.get("invuln_confidence") or "") == "runtime_observed":
        for key in ("invuln", "invuln_ranges", "invuln_confidence", "invuln_kind", "invuln_addr", "invuln_frames"):
            mv.pop(key, None)
    if str(mv.get("counter_source") or "").startswith("runtime_observed"):
        for key in ("counter", "counter_segments", "counter_source"):
            mv.pop(key, None)
    if str(mv.get("guard_point_source") or "").startswith("runtime_observed"):
        for key in ("guard_point", "guard_point_segments", "guard_point_source"):
            mv.pop(key, None)
    if str(mv.get("armor_source") or "").startswith("runtime_observed"):
        for key in ("armor", "armor_kind", "armor_mask", "armor_segments", "armor_mask_segments", "armor_source"):
            mv.pop(key, None)
    if str(mv.get("baroque_source") or "").startswith("runtime_observed"):
        for key in ("baroque_probe", "baroque_ranges", "baroque_timer_values", "baroque_permission_values", "baroque_aux_values", "baroque_source"):
            mv.pop(key, None)
    if runtime_sources:
        mv.pop("armor_probe", None)


def apply_runtime_protection_observations(moves: Iterable[dict], char_id: Any, *, path: Optional[Path] = None) -> None:
    """Overlay exact observed protection windows onto scanned move rows."""
    cid = _safe_int(char_id, 0)
    if cid <= 0:
        return
    doc = _read_doc(Path(path or default_profile_path()))
    records = doc.get("moves") if isinstance(doc.get("moves"), dict) else {}
    for mv in moves or []:
        if not isinstance(mv, dict):
            continue
        _clear_runtime_protection_overlay(mv)
        action_id = _safe_int(mv.get("id"), 0)
        if action_id <= 0:
            continue
        record = records.get(f"{cid}:{action_id}")
        if not isinstance(record, dict):
            continue
        record_mode = str(record.get("sampling_mode") or "")
        compatible_modes = {
            SAMPLING_MODE,
            "action_frame_synced_v9_action_transition",
            "action_frame_synced_v10_native_baroque_gate",
        }
        if record_mode not in compatible_modes:
            # Older GUI-clock profiles are not reliable enough for Baroque.
            continue

        inv_ranges = _ranges(record.get("invuln_frames") or [])
        guard_segments = _guard_segments(record.get("guard_frames") or {})
        guard_trigger_ranges = _ranges(record.get("guard_trigger_frames") or [])
        counter_segments = _guard_segments(record.get("counter_frames") or {})
        counter_trigger_ranges = _ranges(record.get("counter_trigger_frames") or [])
        armor_mask_segments = _guard_segments(record.get("armor_frames") or {})
        armor_trigger_ranges = _ranges(record.get("armor_trigger_frames") or [])
        armor_damage_ranges = _ranges(record.get("armor_damage_frames") or [])
        armor_segments = [(start, end) for start, end, _mask in armor_mask_segments]
        baroque_ranges = _ranges(_baroque_frames_from_record(record))
        baroque_timer_values = record.get("baroque_timer_values") if isinstance(record.get("baroque_timer_values"), dict) else {}
        baroque_permission_values = record.get("baroque_permission_values") if isinstance(record.get("baroque_permission_values"), dict) else {}
        baroque_aux_values = record.get("baroque_aux_values") if isinstance(record.get("baroque_aux_values"), dict) else {}
        baroque_profiled = _baroque_capture_is_complete(record, mv)

        runtime_info = {
            "path": str(Path(path or default_profile_path())),
            "invuln_ranges": inv_ranges,
            "guard_segments": guard_segments,
            "guard_trigger_ranges": guard_trigger_ranges,
            "counter_segments": counter_segments,
            "counter_trigger_ranges": counter_trigger_ranges,
            "armor_segments": armor_segments,
            "armor_mask_segments": armor_mask_segments,
            "armor_trigger_ranges": armor_trigger_ranges,
            "armor_damage_ranges": armor_damage_ranges,
            "baroque_ranges": baroque_ranges,
            "baroque_timer_values": dict(baroque_timer_values),
            "baroque_permission_values": dict(baroque_permission_values),
            "baroque_aux_values": dict(baroque_aux_values),
            "baroque_profiled": bool(baroque_profiled),
            "capture_first_frame": _safe_int(record.get("capture_first_frame"), 0),
            "capture_last_frame": _safe_int(record.get("capture_last_frame"), 0),
            "observations": _safe_int(record.get("observations"), 0),
            "sampling_mode": record_mode,
            "poll_hz": record.get("poll_hz"),
            "sampled_ranges": _ranges(record.get("sampled_frames") or []),
            "missed_ranges": _ranges(record.get("missed_frames") or []),
            "complete_between_first_last": bool(record.get("complete_between_first_last")),
        }
        mv["runtime_protection"] = runtime_info

        if inv_ranges:
            labels = [f"Untargetable {_range_text(start, end)} [R]" for start, end in inv_ranges]
            mv["invuln"] = " / ".join(labels)
            mv["invuln_ranges"] = [[start, end] for start, end in inv_ranges]
            mv["invuln_confidence"] = "runtime_observed"
            mv["invuln_kind"] = "collision_exclusion"
            mv["invuln_addr"] = None
            if len(inv_ranges) == 1 and inv_ranges[0][0] == 1:
                mv["invuln_frames"] = int(inv_ranges[0][1])
            else:
                mv["invuln_frames"] = 0

        summaries: list[str] = []

        if counter_segments:
            summary = _format_counter_segments(counter_segments, "[R]")
            if counter_trigger_ranges:
                trigger_text = ", ".join(_range_text(start, end) for start, end in counter_trigger_ranges)
                summary += f" / counter triggered {trigger_text}"
            mv["counter"] = summary
            mv["counter_segments"] = [[start, end, mask] for start, end, mask in counter_segments]
            mv["counter_source"] = "runtime_observed"
            summaries.append(summary)
        else:
            mv.pop("counter", None)
            mv.pop("counter_segments", None)
            mv.pop("counter_source", None)

        if guard_segments:
            summary = _format_guard_segments(guard_segments, "[R]")
            if guard_trigger_ranges:
                trigger_text = ", ".join(_range_text(start, end) for start, end in guard_trigger_ranges)
                summary += f" / blocked contact {trigger_text}"
            mv["guard_point"] = summary
            mv["guard_point_segments"] = [[start, end, mask] for start, end, mask in guard_segments]
            mv["guard_point_source"] = "runtime_observed"
            summaries.append(summary)
        else:
            mv["guard_point"] = ""
            mv["guard_point_segments"] = []
            mv["guard_point_source"] = ""

        if armor_mask_segments:
            summary = _format_armor_segments(armor_mask_segments, "[R]")
            if armor_damage_ranges:
                damage_text = ", ".join(_range_text(start, end) for start, end in armor_damage_ranges)
                summary += f" / damage taken {damage_text}"
            elif armor_trigger_ranges:
                trigger_text = ", ".join(_range_text(start, end) for start, end in armor_trigger_ranges)
                summary += f" / contact {trigger_text}"
            combined_mask = 0
            for _start, _end, mask in armor_mask_segments:
                combined_mask |= int(mask) & (GUARD_HEIGHT_MASK | GENERIC_PROTECTION_MARKER)
            mv["armor"] = summary
            mv["armor_kind"] = "takes_damage_no_hitstun"
            mv["armor_mask"] = combined_mask
            mv["armor_segments"] = [[start, end] for start, end, _mask in armor_mask_segments]
            mv["armor_mask_segments"] = [[start, end, mask] for start, end, mask in armor_mask_segments]
            mv["armor_source"] = "runtime_observed"
            summaries.append(summary)
        else:
            mv.pop("armor", None)
            mv.pop("armor_kind", None)
            mv.pop("armor_mask", None)
            mv.pop("armor_segments", None)
            mv.pop("armor_mask_segments", None)
            mv.pop("armor_source", None)

        if summaries:
            mv["armor_probe"] = " / ".join(summaries)

        if baroque_ranges:
            mv["baroque_probe"] = _format_baroque_ranges(baroque_ranges, "[R]")
            mv["baroque_ranges"] = [[start, end] for start, end in baroque_ranges]
            mv["baroque_timer_values"] = dict(baroque_timer_values)
            mv["baroque_permission_values"] = dict(baroque_permission_values)
            mv["baroque_aux_values"] = dict(baroque_aux_values)
            mv["baroque_source"] = "runtime_observed"
        elif baroque_profiled:
            mv["baroque_probe"] = "none"
            mv["baroque_ranges"] = []
            mv["baroque_timer_values"] = {}
            mv["baroque_permission_values"] = dict(baroque_permission_values)
            mv["baroque_aux_values"] = dict(baroque_aux_values)
            mv["baroque_source"] = "runtime_observed_none"
        else:
            mv["baroque_probe"] = ""
            mv["baroque_ranges"] = []
            mv["baroque_timer_values"] = {}
            mv["baroque_permission_values"] = dict(baroque_permission_values)
            mv["baroque_aux_values"] = dict(baroque_aux_values)
            mv["baroque_source"] = ""

        if not inv_ranges and not guard_segments and not counter_segments and not armor_segments:
            continue

