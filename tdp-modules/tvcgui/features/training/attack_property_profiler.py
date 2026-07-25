"""Read-only profiler for live attack packet property words.

TvC allocates attack actors from a fixed 128-entry pool. Each fighter owns a
native linked list of its currently active attack actors. Sampling those lists
captures hit packets before they return to the free pool without scanning the
entire pool on every poll.

The important native fields are:

* actor +0x20: runtime collision/status word
* actor +0x30: owner fighter pointer
* actor +0x34: current victim fighter pointer
* actor +0x80: property A, passed to damage as r6
* actor +0x84: property B, passed to damage as r5
* actor +0x8C: base damage value used by the hit packet
* actor +0x35C: phase replacement for property A
* actor +0x360: phase replacement for property B

The profiler never writes to Dolphin memory. It records raw values first so
unknown bits can be named from repeated controlled captures instead of guesses.
"""
from __future__ import annotations

import csv
import json
import os
import struct
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

try:
    from tvcgui.platform.dolphin import addr_in_ram, rd32, rbytes
except Exception:
    def addr_in_ram(_addr: int) -> bool:
        return False

    def rd32(_addr: int):
        return None

    def rbytes(_addr: int, _size: int):
        return b""

from tvcgui.core.constants import CHAR_NAMES
from tvcgui.core.paths import user_data_path
from tvcgui.runtime.deferred_work import DeferredWorkLoop

PROFILE_VERSION = 1
PROFILE_FILE = "runtime_attack_property_profiles.json"
EVENT_FILE = "runtime_attack_property_events.csv"
SAMPLING_MODE = "native_owner_attack_lists_v1"

# The native owner list is a circular sentinel list embedded in each fighter.
OWNER_LIST_OFFSET = 0x4350
LIST_NEXT_OFFSET = 0x08
LIST_NODE_OFFSET = 0x04
MAX_ACTORS_PER_OWNER = 128

# Fighter metadata read directly by the sampler.
FIGHTER_META_START = 0x0014
FIGHTER_CHAR_ID = 0x0014
FIGHTER_ACTION_FRAME = 0x01D8
FIGHTER_ACTION_ID = 0x01E8
FIGHTER_META_END = FIGHTER_ACTION_ID + 4
FIGHTER_META_SIZE = FIGHTER_META_END - FIGHTER_META_START

# Attack actor range read in one coherent block. The list node starts at +0x04.
ACTOR_BLOCK_START = 0x0004
ACTOR_BLOCK_END = 0x0364
ACTOR_BLOCK_SIZE = ACTOR_BLOCK_END - ACTOR_BLOCK_START

OFF_LIST_PREV = 0x0008
OFF_LIST_NEXT = 0x000C
OFF_OBJECT_FLAGS_10 = 0x0010
OFF_OBJECT_FLAGS_14 = 0x0014
OFF_OBJECT_FLAGS_18 = 0x0018
OFF_OBJECT_FLAGS_1C = 0x001C
OFF_RUNTIME_STATUS_20 = 0x0020
OFF_OBJECT_FLAGS_24 = 0x0024
OFF_OBJECT_FLAGS_28 = 0x0028
OFF_OBJECT_FLAGS_2C = 0x002C
OFF_OWNER = 0x0030
OFF_VICTIM = 0x0034
OFF_PROPERTY_A = 0x0080
OFF_PROPERTY_B = 0x0084
OFF_BASE_DAMAGE = 0x008C
OFF_PHASE_PROPERTY_A = 0x035C
OFF_PHASE_PROPERTY_B = 0x0360

DEFAULT_POLL_HZ = 240.0
MAX_EVENT_HISTORY = 20000
WRITE_INTERVAL_SEC = 0.75

PROPERTY_A_GUARD_BITS = {
    0x08: "Mid",
    0x10: "High",
    0x20: "Low",
}
PROPERTY_A_STRENGTH_BITS = {
    0x01: "Light",
    0x02: "Medium",
    0x04: "Heavy",
}

# Only bits with confirmed native branch behavior receive firm names here.
# Unresolved bits remain visible in the raw hexadecimal value.
PROPERTY_B_CONFIRMED_BITS = {
    0x00000001: "Damage exception route A",
    0x00000004: "Grab family",
    0x00000008: "Special resolver route",
    0x00004000: "Immediate handling route",
    0x00010000: "Damage mode 3/reset route",
    0x00040000: "One-eighth block-damage route B",
    0x00080000: "Grab modifier route",
    0x00100000: "Paired special condition",
    0x40000000: "Result flag 0x1",
}
PROPERTY_B_OBSERVED_BITS = {
    0x00000002: "Init/default bit",
    0x00000010: "Cleanup-managed bit",
    0x00000040: "Common normal-hit bit",
    0x00000100: "Default collision family",
    0x00000200: "Conditional mode bit",
}

CSV_FIELDS = [
    "timestamp_utc",
    "gui_frame",
    "actor",
    "owner_slot",
    "owner_base",
    "owner_char_id",
    "owner_name",
    "owner_action_id",
    "owner_action_name",
    "owner_action_frame",
    "victim_slot",
    "victim_base",
    "victim_char_id",
    "victim_name",
    "property_a",
    "property_a_text",
    "property_b",
    "property_b_text",
    "runtime_status_20",
    "phase_property_a",
    "phase_property_b",
    "base_damage",
    "object_flags_10",
    "object_flags_14",
    "object_flags_18",
    "object_flags_1c",
    "object_flags_24",
    "object_flags_28",
    "object_flags_2c",
]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _u32be(blob: bytes, absolute_offset: int, *, block_start: int = 0) -> int:
    rel = int(absolute_offset) - int(block_start)
    if rel < 0 or rel + 4 > len(blob):
        return 0
    return struct.unpack_from(">I", blob, rel)[0]


def _f32be(blob: bytes, absolute_offset: int, *, block_start: int = 0) -> Optional[float]:
    rel = int(absolute_offset) - int(block_start)
    if rel < 0 or rel + 4 > len(blob):
        return None
    try:
        value = struct.unpack_from(">f", blob, rel)[0]
    except Exception:
        return None
    if value != value or abs(value) > 100000.0:
        return None
    return float(value)


def _decode_action_frame(value: Optional[float]) -> int:
    if value is None or value < 0.0 or value > 4000.0:
        return 0
    return max(0, int(round(value - 1.0)))


def _hex32(value: Any) -> str:
    return f"0x{_safe_int(value) & 0xFFFFFFFF:08X}"


def decode_property_a(value: Any) -> dict:
    """Decode the proven packed guard-height and strength fields."""
    raw = _safe_int(value) & 0xFFFFFFFF
    low = raw & 0x3F
    guard_mask = low & 0x38
    strength_mask = low & 0x07

    guards = [label for bit, label in PROPERTY_A_GUARD_BITS.items() if guard_mask & bit]
    strengths = [label for bit, label in PROPERTY_A_STRENGTH_BITS.items() if strength_mask & bit]

    if not guards and strength_mask:
        guard_text = "Unblockable"
    elif guards:
        guard_text = "/".join(guards)
    else:
        guard_text = "No guard category"

    if strengths:
        strength_text = "/".join(strengths) + " Hit"
    else:
        strength_text = "No strength tier"

    high_flags = raw & ~0x3F
    text_parts = [guard_text, strength_text]
    if high_flags:
        text_parts.append(f"A flags {_hex32(high_flags)}")
    return {
        "raw": raw,
        "low_byte": raw & 0xFF,
        "guard_mask": guard_mask,
        "guard": guard_text,
        "strength_mask": strength_mask,
        "strength": strength_text,
        "high_flags": high_flags,
        "text": ", ".join(text_parts),
    }


def decode_property_b(value: Any) -> dict:
    """Decode confirmed property B branches while preserving unknown bits."""
    raw = _safe_int(value) & 0xFFFFFFFF
    labels: list[str] = []
    known_mask = 0
    for bit, label in PROPERTY_B_CONFIRMED_BITS.items():
        if raw & bit:
            labels.append(label)
            known_mask |= bit
    observed: list[str] = []
    for bit, label in PROPERTY_B_OBSERVED_BITS.items():
        if raw & bit:
            observed.append(label)
            known_mask |= bit
    unknown = raw & ~known_mask
    text_parts = list(labels)
    text_parts.extend(observed)
    if unknown:
        text_parts.append(f"Unknown B bits {_hex32(unknown)}")
    if not text_parts:
        text_parts.append("No property B bits")
    return {
        "raw": raw,
        "confirmed": labels,
        "observed": observed,
        "unknown_mask": unknown,
        "text": ", ".join(text_parts),
    }


def default_profile_path() -> Path:
    return Path(user_data_path("runtime")) / PROFILE_FILE


def default_event_path() -> Path:
    return Path(user_data_path("runtime")) / EVENT_FILE


def _empty_doc() -> dict:
    return {
        "version": PROFILE_VERSION,
        "sampling_mode": SAMPLING_MODE,
        "updated_utc": "",
        "signatures": {},
        "events": [],
    }


def _read_doc(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_doc()
    if not isinstance(raw, dict):
        return _empty_doc()
    raw["version"] = PROFILE_VERSION
    raw["sampling_mode"] = SAMPLING_MODE
    if not isinstance(raw.get("signatures"), dict):
        raw["signatures"] = {}
    if not isinstance(raw.get("events"), list):
        raw["events"] = []
    raw.setdefault("updated_utc", "")
    return raw


def _write_json_atomic(path: Path, doc: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(doc)
        payload["version"] = PROFILE_VERSION
        payload["sampling_mode"] = SAMPLING_MODE
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


@dataclass(frozen=True)
class _SlotMeta:
    slot: str
    base: int
    char_id: int = 0
    name: str = ""
    action_id: int = 0
    action_name: str = ""


@dataclass(frozen=True)
class _FighterLiveMeta:
    slot: str
    base: int
    char_id: int
    name: str
    action_id: int
    action_name: str
    action_frame: int


class RuntimeAttackPropertyProfiler:
    """Capture every distinct live attack-packet property transition."""

    def __init__(
        self,
        path: Optional[Path] = None,
        event_path: Optional[Path] = None,
        *,
        read_u32: Optional[Callable[[int], Optional[int]]] = None,
        read_block: Optional[Callable[[int, int], bytes]] = None,
        is_valid_addr: Optional[Callable[[int], bool]] = None,
        poll_hz: float = DEFAULT_POLL_HZ,
        start_worker: bool = True,
        emit_console: bool = True,
    ):
        self.path = Path(path or default_profile_path())
        self.event_path = Path(event_path or default_event_path())
        self.doc = _read_doc(self.path)
        self._read_u32 = read_u32 or rd32
        self._read_block = read_block or rbytes
        self._is_valid_addr = is_valid_addr or addr_in_ram
        self._poll_interval = 1.0 / max(60.0, float(poll_hz or DEFAULT_POLL_HZ))
        self._idle_poll_interval = 1.0 / 60.0
        self._emit_console = bool(emit_console)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._slot_meta: Dict[str, _SlotMeta] = {}
        self._last_actor_signatures: Dict[int, tuple] = {}
        self._active_by_slot: Dict[str, list[dict]] = {}
        self._pending_csv: list[dict] = []
        self._dirty = False
        self._change_serial = 0
        self._changed_pending = False
        self._gui_frame = 0
        self._last_write = 0.0
        self._thread: Optional[threading.Thread] = None
        if start_worker:
            self._thread = threading.Thread(
                target=self._worker_loop,
                name="TvCAttackPropertySampler",
                daemon=True,
            )
            self._thread.start()
        self._writer = DeferredWorkLoop(
            lambda: self._write_pending(force=False),
            interval=WRITE_INTERVAL_SEC,
            name="TvCAttackPropertyWriter",
        )

    def _read_word(self, address: int) -> Optional[int]:
        try:
            value = self._read_u32(int(address))
        except Exception:
            value = None
        if value is None:
            return None
        return int(value) & 0xFFFFFFFF

    def _read_bytes(self, address: int, size: int) -> bytes:
        try:
            blob = self._read_block(int(address), int(size))
        except Exception:
            return b""
        return bytes(blob or b"")

    def _valid(self, address: int) -> bool:
        try:
            return bool(address and self._is_valid_addr(int(address)))
        except Exception:
            return False

    def _fighter_live_meta(self, fallback: _SlotMeta) -> _FighterLiveMeta:
        blob = self._read_bytes(fallback.base + FIGHTER_META_START, FIGHTER_META_SIZE)
        char_id = fallback.char_id
        action_id = fallback.action_id
        action_frame = 0
        if len(blob) == FIGHTER_META_SIZE:
            char_id = _u32be(blob, FIGHTER_CHAR_ID, block_start=FIGHTER_META_START) or char_id
            action_id = _u32be(blob, FIGHTER_ACTION_ID, block_start=FIGHTER_META_START) or action_id
            action_frame = _decode_action_frame(
                _f32be(blob, FIGHTER_ACTION_FRAME, block_start=FIGHTER_META_START)
            )
        name = CHAR_NAMES.get(char_id, fallback.name or f"ID_{char_id}")
        action_name = fallback.action_name if not fallback.action_id or fallback.action_id == action_id else ""
        return _FighterLiveMeta(
            slot=fallback.slot,
            base=fallback.base,
            char_id=char_id,
            name=name,
            action_id=action_id,
            action_name=action_name,
            action_frame=action_frame,
        )

    def _read_actor(self, actor: int, owner: _FighterLiveMeta, pointer_meta: dict[int, _FighterLiveMeta]) -> Optional[dict]:
        blob = self._read_bytes(actor + ACTOR_BLOCK_START, ACTOR_BLOCK_SIZE)
        if len(blob) != ACTOR_BLOCK_SIZE:
            return None

        owner_base = _u32be(blob, OFF_OWNER, block_start=ACTOR_BLOCK_START)
        victim_base = _u32be(blob, OFF_VICTIM, block_start=ACTOR_BLOCK_START)
        next_node = _u32be(blob, OFF_LIST_NEXT, block_start=ACTOR_BLOCK_START)
        owner_meta = pointer_meta.get(owner_base, owner)
        victim_meta = pointer_meta.get(victim_base)
        property_a = _u32be(blob, OFF_PROPERTY_A, block_start=ACTOR_BLOCK_START)
        property_b = _u32be(blob, OFF_PROPERTY_B, block_start=ACTOR_BLOCK_START)
        phase_a = _u32be(blob, OFF_PHASE_PROPERTY_A, block_start=ACTOR_BLOCK_START)
        phase_b = _u32be(blob, OFF_PHASE_PROPERTY_B, block_start=ACTOR_BLOCK_START)
        status20 = _u32be(blob, OFF_RUNTIME_STATUS_20, block_start=ACTOR_BLOCK_START)
        damage = _u32be(blob, OFF_BASE_DAMAGE, block_start=ACTOR_BLOCK_START)
        decoded_a = decode_property_a(property_a)
        decoded_b = decode_property_b(property_b)

        return {
            "actor": actor,
            "next_node": next_node,
            "owner_slot": owner_meta.slot,
            "owner_base": owner_base or owner_meta.base,
            "owner_char_id": owner_meta.char_id,
            "owner_name": owner_meta.name,
            "owner_action_id": owner_meta.action_id,
            "owner_action_name": owner_meta.action_name,
            "owner_action_frame": owner_meta.action_frame,
            "victim_slot": victim_meta.slot if victim_meta else "",
            "victim_base": victim_base,
            "victim_char_id": victim_meta.char_id if victim_meta else 0,
            "victim_name": victim_meta.name if victim_meta else "",
            "property_a": property_a,
            "property_a_text": decoded_a["text"],
            "property_a_guard_mask": decoded_a["guard_mask"],
            "property_a_strength_mask": decoded_a["strength_mask"],
            "property_a_high_flags": decoded_a["high_flags"],
            "property_b": property_b,
            "property_b_text": decoded_b["text"],
            "property_b_unknown_mask": decoded_b["unknown_mask"],
            "runtime_status_20": status20,
            "phase_property_a": phase_a,
            "phase_property_b": phase_b,
            "base_damage": damage,
            "object_flags_10": _u32be(blob, OFF_OBJECT_FLAGS_10, block_start=ACTOR_BLOCK_START),
            "object_flags_14": _u32be(blob, OFF_OBJECT_FLAGS_14, block_start=ACTOR_BLOCK_START),
            "object_flags_18": _u32be(blob, OFF_OBJECT_FLAGS_18, block_start=ACTOR_BLOCK_START),
            "object_flags_1c": _u32be(blob, OFF_OBJECT_FLAGS_1C, block_start=ACTOR_BLOCK_START),
            "object_flags_24": _u32be(blob, OFF_OBJECT_FLAGS_24, block_start=ACTOR_BLOCK_START),
            "object_flags_28": _u32be(blob, OFF_OBJECT_FLAGS_28, block_start=ACTOR_BLOCK_START),
            "object_flags_2c": _u32be(blob, OFF_OBJECT_FLAGS_2C, block_start=ACTOR_BLOCK_START),
        }

    def _enumerate_owner_actors(
        self,
        owner: _FighterLiveMeta,
        pointer_meta: dict[int, _FighterLiveMeta],
        *,
        first_node: Optional[int] = None,
    ) -> list[dict]:
        sentinel = owner.base + OWNER_LIST_OFFSET
        node = first_node if first_node is not None else self._read_word(sentinel + LIST_NEXT_OFFSET)
        if node is None or node == sentinel:
            return []

        out: list[dict] = []
        seen: set[int] = set()
        while node and node != sentinel and len(out) < MAX_ACTORS_PER_OWNER:
            if node in seen or not self._valid(node):
                break
            seen.add(node)
            actor = int(node) - LIST_NODE_OFFSET
            if not self._valid(actor):
                break
            record = self._read_actor(actor, owner, pointer_meta)
            if record is None:
                break
            out.append(record)
            next_node = _safe_int(record.get("next_node"), 0)
            if next_node == node:
                break
            node = next_node
        return out

    @staticmethod
    def _signature(record: dict) -> tuple:
        return (
            _safe_int(record.get("owner_base")),
            _safe_int(record.get("owner_char_id")),
            _safe_int(record.get("owner_action_id")),
            _safe_int(record.get("victim_base")),
            _safe_int(record.get("property_a")),
            _safe_int(record.get("property_b")),
            _safe_int(record.get("runtime_status_20")),
            _safe_int(record.get("phase_property_a")),
            _safe_int(record.get("phase_property_b")),
            _safe_int(record.get("base_damage")),
            _safe_int(record.get("object_flags_10")),
            _safe_int(record.get("object_flags_14")),
            _safe_int(record.get("object_flags_18")),
            _safe_int(record.get("object_flags_1c")),
            _safe_int(record.get("object_flags_24")),
            _safe_int(record.get("object_flags_28")),
            _safe_int(record.get("object_flags_2c")),
        )

    @staticmethod
    def _signature_key(record: dict) -> str:
        fields = (
            _safe_int(record.get("owner_char_id")),
            _safe_int(record.get("owner_action_id")),
            _safe_int(record.get("property_a")),
            _safe_int(record.get("property_b")),
            _safe_int(record.get("runtime_status_20")),
            _safe_int(record.get("phase_property_a")),
            _safe_int(record.get("phase_property_b")),
            _safe_int(record.get("base_damage")),
        )
        return ":".join(f"{value:X}" for value in fields)

    def _record_transition_locked(self, record: dict, *, gui_frame: int) -> None:
        now_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        event = dict(record)
        event.pop("next_node", None)
        event["timestamp_utc"] = now_utc
        event["gui_frame"] = int(gui_frame)

        key = self._signature_key(record)
        signatures = self.doc.setdefault("signatures", {})
        aggregate = signatures.get(key)
        if not isinstance(aggregate, dict):
            aggregate = dict(event)
            aggregate["first_seen_utc"] = now_utc
            aggregate["observations"] = 0
            aggregate["actors"] = []
            aggregate["owner_slots"] = []
            aggregate["victim_slots"] = []
        aggregate["last_seen_utc"] = now_utc
        aggregate["observations"] = _safe_int(aggregate.get("observations")) + 1
        actors = {str(value) for value in aggregate.get("actors") or []}
        actors.add(_hex32(record.get("actor")))
        aggregate["actors"] = sorted(actors)
        owner_slots = {str(value) for value in aggregate.get("owner_slots") or [] if str(value)}
        if record.get("owner_slot"):
            owner_slots.add(str(record["owner_slot"]))
        aggregate["owner_slots"] = sorted(owner_slots)
        victim_slots = {str(value) for value in aggregate.get("victim_slots") or [] if str(value)}
        if record.get("victim_slot"):
            victim_slots.add(str(record["victim_slot"]))
        aggregate["victim_slots"] = sorted(victim_slots)
        signatures[key] = aggregate

        history = self.doc.setdefault("events", [])
        history.append(event)
        if len(history) > MAX_EVENT_HISTORY:
            del history[:-MAX_EVENT_HISTORY]
        self._pending_csv.append(event)
        self._dirty = True
        self._change_serial += 1
        self._changed_pending = True

        if self._emit_console:
            owner_label = str(record.get("owner_slot") or "?")
            owner_name = str(record.get("owner_name") or "?")
            action = _safe_int(record.get("owner_action_id"))
            action_frame = _safe_int(record.get("owner_action_frame"))
            print(
                f"[attack flags] {owner_label} {owner_name} "
                f"action 0x{action:04X} f{action_frame} "
                f"A={_hex32(record.get('property_a'))} "
                f"B={_hex32(record.get('property_b'))} "
                f"S20={_hex32(record.get('runtime_status_20'))} "
                f"DMG={_safe_int(record.get('base_damage'))}",
                flush=True,
            )

    def sample_once(self) -> int:
        with self._lock:
            slots = dict(self._slot_meta)
            gui_frame = self._gui_frame

        unique: dict[int, _SlotMeta] = {}
        for meta in slots.values():
            if meta.base and self._valid(meta.base):
                unique.setdefault(meta.base, meta)

        # Read only the four list heads while no attacks are active. The larger
        # fighter metadata block is read only for owners with a live actor.
        first_nodes: dict[int, int] = {}
        for base in unique:
            sentinel = base + OWNER_LIST_OFFSET
            node = self._read_word(sentinel + LIST_NEXT_OFFSET)
            if node is not None and node != sentinel:
                first_nodes[base] = node

        pointer_meta: dict[int, _FighterLiveMeta] = {
            base: _FighterLiveMeta(
                slot=meta.slot,
                base=meta.base,
                char_id=meta.char_id,
                name=CHAR_NAMES.get(meta.char_id, meta.name or f"ID_{meta.char_id}"),
                action_id=meta.action_id,
                action_name=meta.action_name,
                action_frame=0,
            )
            for base, meta in unique.items()
        }
        for base in first_nodes:
            pointer_meta[base] = self._fighter_live_meta(unique[base])

        active_records: list[dict] = []
        active_by_slot: Dict[str, list[dict]] = {}
        for base, first_node in first_nodes.items():
            live = pointer_meta[base]
            records = self._enumerate_owner_actors(
                live,
                pointer_meta,
                first_node=first_node,
            )
            active_records.extend(records)
            active_by_slot[live.slot] = records

        active_actors = {_safe_int(record.get("actor")) for record in active_records}
        transitions = 0
        with self._lock:
            for record in active_records:
                actor = _safe_int(record.get("actor"))
                signature = self._signature(record)
                if self._last_actor_signatures.get(actor) == signature:
                    continue
                self._last_actor_signatures[actor] = signature
                self._record_transition_locked(record, gui_frame=gui_frame)
                transitions += 1
            for actor in list(self._last_actor_signatures):
                if actor not in active_actors:
                    self._last_actor_signatures.pop(actor, None)
            self._active_by_slot = active_by_slot
        return transitions

    def _worker_loop(self) -> None:
        deadline = time.perf_counter()
        while not self._stop.is_set():
            self.sample_once()
            with self._lock:
                active = any(bool(rows) for rows in self._active_by_slot.values())
            deadline += self._poll_interval if active else self._idle_poll_interval
            delay = deadline - time.perf_counter()
            if delay <= 0:
                deadline = time.perf_counter()
                delay = 0.0005
            self._stop.wait(delay)

    def _write_pending(self, *, force: bool = False) -> bool:
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_write < WRITE_INTERVAL_SEC:
                return True
            dirty = self._dirty
            change_serial = self._change_serial
            pending = list(self._pending_csv)
            if not dirty and not pending:
                self._last_write = now
                return True
            doc_copy = json.loads(json.dumps(self.doc))

        json_ok = True
        csv_ok = True
        if dirty:
            json_ok = _write_json_atomic(self.path, doc_copy)
        if pending:
            try:
                self.event_path.parent.mkdir(parents=True, exist_ok=True)
                needs_header = not self.event_path.exists() or self.event_path.stat().st_size == 0
                with self.event_path.open("a", encoding="utf-8", newline="") as handle:
                    writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
                    if needs_header:
                        writer.writeheader()
                    for row in pending:
                        formatted = dict(row)
                        for key in (
                            "actor", "owner_base", "victim_base", "property_a", "property_b",
                            "runtime_status_20", "phase_property_a", "phase_property_b",
                            "object_flags_10", "object_flags_14", "object_flags_18",
                            "object_flags_1c", "object_flags_24", "object_flags_28",
                            "object_flags_2c",
                        ):
                            formatted[key] = _hex32(formatted.get(key))
                        formatted["owner_action_id"] = f"0x{_safe_int(formatted.get('owner_action_id')):04X}"
                        writer.writerow(formatted)
            except Exception:
                csv_ok = False

        with self._lock:
            if json_ok and self._change_serial == change_serial:
                self._dirty = False
            if csv_ok and pending:
                del self._pending_csv[:len(pending)]
            self._last_write = now
        return bool(json_ok and csv_ok)

    def update(self, snaps: dict[str, dict], *, frame: int = 0, now: Optional[float] = None) -> bool:
        del now
        metas: Dict[str, _SlotMeta] = {}
        for slot, snap in (snaps or {}).items():
            if not isinstance(snap, dict):
                continue
            base = _safe_int(snap.get("base"), 0)
            if not base:
                continue
            action_id = 0
            for key in ("attA", "attB", "timing_action_id", "move_id"):
                action_id = _safe_int(snap.get(key), 0)
                if action_id:
                    break
            metas[str(slot)] = _SlotMeta(
                slot=str(slot),
                base=base,
                char_id=_safe_int(snap.get("id"), 0),
                name=str(snap.get("name") or ""),
                action_id=action_id,
                action_name=str(snap.get("mv_label_display") or snap.get("mv_label") or ""),
            )

        with self._lock:
            self._slot_meta = metas
            self._gui_frame = int(frame)
            active_copy = {slot: [dict(row) for row in rows] for slot, rows in self._active_by_slot.items()}
            changed = bool(self._changed_pending)
            self._changed_pending = False

        for slot, snap in (snaps or {}).items():
            if not isinstance(snap, dict):
                continue
            rows = active_copy.get(str(slot), [])
            snap["attack_property_packet_count"] = len(rows)
            if rows:
                latest = rows[-1]
                snap["attack_property_live_actor"] = _safe_int(latest.get("actor"))
                snap["attack_property_live_a"] = _safe_int(latest.get("property_a"))
                snap["attack_property_live_b"] = _safe_int(latest.get("property_b"))
                snap["attack_property_live_status20"] = _safe_int(latest.get("runtime_status_20"))
                snap["attack_property_live_damage"] = _safe_int(latest.get("base_damage"))
                snap["attack_property_live_a_text"] = str(latest.get("property_a_text") or "")
                snap["attack_property_live_b_text"] = str(latest.get("property_b_text") or "")
                snap["attack_property_live_b_unknown"] = _safe_int(latest.get("property_b_unknown_mask"))
                snap["attack_property_live_phase_a"] = _safe_int(latest.get("phase_property_a"))
                snap["attack_property_live_phase_b"] = _safe_int(latest.get("phase_property_b"))
                snap["attack_property_live_victim_slot"] = str(latest.get("victim_slot") or "")
                snap["attack_property_live_action_frame"] = _safe_int(latest.get("owner_action_frame"))
            else:
                snap["attack_property_live_actor"] = None
                snap["attack_property_live_a"] = None
                snap["attack_property_live_b"] = None
                snap["attack_property_live_status20"] = None
                snap["attack_property_live_damage"] = None
                snap["attack_property_live_a_text"] = ""
                snap["attack_property_live_b_text"] = ""
                snap["attack_property_live_b_unknown"] = None
                snap["attack_property_live_phase_a"] = None
                snap["attack_property_live_phase_b"] = None
                snap["attack_property_live_victim_slot"] = ""
                snap["attack_property_live_action_frame"] = None

        if changed:
            self._writer.request()
        return changed

    def flush(self) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
        result = {"ok": True}
        self._writer.close(
            final_callback=lambda: result.__setitem__("ok", self._write_pending(force=True)),
            timeout=1.5,
        )
        return bool(result["ok"])


__all__ = [
    "RuntimeAttackPropertyProfiler",
    "decode_property_a",
    "decode_property_b",
    "default_profile_path",
    "default_event_path",
]
