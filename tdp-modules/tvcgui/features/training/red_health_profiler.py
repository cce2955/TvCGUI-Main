"""Read-only runtime profiler for TvC recoverable health behavior.

TvC stores max health, current health, and a recoverable-health ceiling in the
fighter object. Recoverable health is the positive difference between the
ceiling and current health. Native damage requests are queued through separate
current-health and ceiling deltas before the fighter update applies and clamps
them.

This profiler records live transitions for all four fighter slots. It never
writes to Dolphin memory.
"""
from __future__ import annotations

import csv
import json
import math
import os
import struct
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

try:
    from tvcgui.platform.dolphin import rd32, rbytes
except Exception:
    def rd32(_address: int):
        return None

    def rbytes(_address: int, _size: int):
        return b""

from tvcgui.core.paths import user_data_path
from tvcgui.runtime.deferred_work import DeferredWorkLoop

PROFILE_VERSION = 1
PROFILE_FILE = "runtime_red_health_profiles.json"
EVENT_FILE = "runtime_red_health_events.csv"
SAMPLING_MODE = "fighter_current_aux_transition_v1"

MAX_HP_OFFSET = 0x24
CURRENT_HP_OFFSET = 0x28
AUX_HP_OFFSET = 0x2C
PENDING_CURRENT_OFFSET = 0x30
PENDING_AUX_OFFSET = 0x34
HEAL_SYNC_MODE_OFFSET = 0x38
POINT_FLAG_OFFSET = 0x44A0
BAROQUE_ACTIVE_OFFSET = 0x44BC
BAROQUE_RED_SPENT_OFFSET = 0x454C

RECENT_ATTACK_WINDOW_FRAMES = 8
MAX_EVENT_HISTORY = 20000
WRITE_INTERVAL_SEC = 0.75
RESERVE_RECOVERY_RATE = 0.0001

CSV_FIELDS = [
    "timestamp_utc",
    "gui_frame",
    "slot",
    "team",
    "fighter_base",
    "char_id",
    "character_name",
    "point_before",
    "point_after",
    "event_kind",
    "max_hp_before",
    "max_hp_after",
    "current_before",
    "current_after",
    "current_delta",
    "aux_before",
    "aux_after",
    "aux_delta",
    "red_before",
    "red_after",
    "red_delta",
    "damage_observed",
    "aux_loss_observed",
    "red_generated_observed",
    "predicted_aux_loss",
    "predicted_red_generated",
    "prediction_difference",
    "prediction_match",
    "prediction_confidence",
    "predicted_reserve_step",
    "pending_current_delta",
    "pending_aux_delta",
    "heal_sync_mode",
    "baroque_before",
    "baroque_after",
    "red_spent_before",
    "red_spent_after",
    "attacker_slot",
    "attacker_char_id",
    "attacker_name",
    "attacker_action_id",
    "attacker_action_name",
    "attacker_action_frame",
    "attack_base_damage",
    "attack_property_a",
    "attack_property_b",
    "notes",
]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _f32(value: float) -> float:
    return struct.unpack(">f", struct.pack(">f", float(value)))[0]


def _hex32(value: Any) -> str:
    return f"0x{_safe_int(value) & 0xFFFFFFFF:08X}"


def _s32(value: Any) -> int:
    raw = _safe_int(value) & 0xFFFFFFFF
    return raw - 0x100000000 if raw & 0x80000000 else raw


def _u32be(blob: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(blob):
        return 0
    return struct.unpack_from(">I", blob, offset)[0]


def recoverable_health(current_hp: int, aux_hp: int) -> int:
    """Return the visible recoverable amount represented by aux minus current."""
    return max(0, _safe_int(aux_hp) - _safe_int(current_hp))


def predict_normal_red_health(damage: int) -> dict:
    """Predict one native damage request, including the integer truncation."""
    amount = max(0, _safe_int(damage))
    auxiliary_loss = math.floor((amount * 600) / 1000)
    red_generated = amount - auxiliary_loss
    return {
        "damage": amount,
        "current_loss": amount,
        "auxiliary_loss": auxiliary_loss,
        "red_generated": red_generated,
    }


def predict_current_only_cost(max_hp: int, current_hp: int) -> dict:
    """Predict the confirmed ten-percent, nonlethal current-only cost path."""
    maximum = max(0, _safe_int(max_hp))
    current = max(0, _safe_int(current_hp))
    requested = maximum // 10
    after = max(1, current - requested) if current > 0 else 0
    actual = max(0, current - after)
    return {
        "requested_cost": requested,
        "current_after": after,
        "actual_cost": actual,
        "red_generated": actual,
    }


def predict_reserve_recovery_step(max_hp: int) -> int:
    """Predict the native 0.01 percent max-health recovery request."""
    maximum = max(0, _safe_int(max_hp))
    return math.trunc(_f32(float(maximum)) * _f32(RESERVE_RECOVERY_RATE))


def default_profile_path() -> Path:
    return Path(user_data_path("runtime")) / PROFILE_FILE


def default_event_path() -> Path:
    return Path(user_data_path("runtime")) / EVENT_FILE


def _empty_doc() -> dict:
    return {
        "version": PROFILE_VERSION,
        "sampling_mode": SAMPLING_MODE,
        "updated_utc": "",
        "events": [],
        "signatures": {},
    }


def _read_doc(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _empty_doc()
    if not isinstance(value, dict):
        return _empty_doc()
    value.setdefault("events", [])
    value.setdefault("signatures", {})
    value["version"] = PROFILE_VERSION
    value["sampling_mode"] = SAMPLING_MODE
    return value


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


@dataclass
class _HealthState:
    base: int
    max_hp: int
    current_hp: int
    aux_hp: int
    pending_current: int
    pending_aux: int
    heal_sync_mode: int
    point: bool
    baroque: bool
    red_spent: int
    char_id: int
    name: str

    @property
    def red_hp(self) -> int:
        return recoverable_health(self.current_hp, self.aux_hp)


@dataclass
class _AttackMemory:
    slot: str = ""
    frame: int = 0
    char_id: int = 0
    name: str = ""
    action_id: int = 0
    action_name: str = ""
    action_frame: int = 0
    base_damage: int = 0
    property_a: int = 0
    property_b: int = 0
    victim_base: int = 0


class RuntimeRedHealthProfiler:
    """Record current-health, ceiling, Baroque, and recovery transitions."""

    def __init__(
        self,
        path: Optional[Path] = None,
        event_path: Optional[Path] = None,
        *,
        read_u32: Optional[Callable[[int], Optional[int]]] = None,
        read_block: Optional[Callable[[int, int], bytes]] = None,
        emit_console: bool = True,
    ):
        self.path = Path(path or default_profile_path())
        self.event_path = Path(event_path or default_event_path())
        self._read_u32 = read_u32 or rd32
        self._read_block = read_block or rbytes
        self.emit_console = bool(emit_console)
        self.doc = _read_doc(self.path)
        self._lock = threading.RLock()
        self._last_write = 0.0
        self._dirty = False
        self._previous: Dict[str, _HealthState] = {}
        self._recent_attack: Dict[str, _AttackMemory] = {}
        self._last_event_by_slot: Dict[str, dict] = {}
        self._pending_csv: list[dict] = []
        self._change_serial = 0
        self._writer = DeferredWorkLoop(
            lambda: self._write_pending(force=False),
            interval=WRITE_INTERVAL_SEC,
            name="TvCRedHealthWriter",
        )


    def _annotate_snapshot(self, snap: dict, state: _HealthState, event: Optional[dict] = None) -> None:
        snap["red_health_current"] = int(state.current_hp)
        snap["red_health_aux"] = int(state.aux_hp)
        snap["red_health_recoverable"] = int(state.red_hp)
        snap["red_health_pct_max"] = (float(state.red_hp) / float(state.max_hp) * 100.0) if state.max_hp else 0.0
        snap["red_health_pending_current"] = int(state.pending_current)
        snap["red_health_pending_aux"] = int(state.pending_aux)
        snap["red_health_heal_sync"] = int(state.heal_sync_mode)
        snap["red_health_point"] = bool(state.point)
        snap["red_health_baroque"] = bool(state.baroque)
        snap["red_health_red_spent"] = int(state.red_spent)
        latest = event if isinstance(event, dict) else self._last_event_by_slot.get(str(snap.get("slot_label") or ""))
        if not isinstance(latest, dict):
            return
        snap["red_health_last_event"] = str(latest.get("event_kind") or "")
        snap["red_health_last_current_delta"] = _safe_int(latest.get("current_delta"), 0)
        snap["red_health_last_aux_delta"] = _safe_int(latest.get("aux_delta"), 0)
        snap["red_health_last_red_delta"] = _safe_int(latest.get("red_delta"), 0)
        predicted = latest.get("predicted_red_generated")
        snap["red_health_last_predicted"] = None if predicted in (None, "") else _safe_int(predicted)
        snap["red_health_last_match"] = latest.get("prediction_match")
        snap["red_health_last_attacker"] = str(latest.get("attacker_name") or latest.get("attacker_slot") or "")
        snap["red_health_last_move"] = str(latest.get("attacker_action_name") or "")

    def _u32(self, address: int, default: int = 0) -> int:
        try:
            value = self._read_u32(int(address))
        except Exception:
            return int(default)
        return _safe_int(value, default) & 0xFFFFFFFF

    @staticmethod
    def _team_of_slot(slot: str) -> str:
        return "P1" if str(slot).startswith("P1") else "P2"

    @staticmethod
    def _action_id(snap: dict) -> int:
        for key in ("attA", "attB", "timing_action_id", "move_id", "mv_id_display"):
            value = _safe_int(snap.get(key), 0)
            if value:
                return value
        return 0

    def _read_extra_state(self, base: int) -> dict:
        pending_current = pending_aux = heal_sync = 0
        point = baroque = False
        red_spent = 0
        try:
            small = bytes(self._read_block(base + PENDING_CURRENT_OFFSET, 12) or b"")
        except Exception:
            small = b""
        if len(small) == 12:
            pending_current = _s32(_u32be(small, 0))
            pending_aux = _s32(_u32be(small, 4))
            heal_sync = _u32be(small, 8)
        else:
            pending_current = _s32(self._u32(base + PENDING_CURRENT_OFFSET))
            pending_aux = _s32(self._u32(base + PENDING_AUX_OFFSET))
            heal_sync = self._u32(base + HEAL_SYNC_MODE_OFFSET)

        span = BAROQUE_RED_SPENT_OFFSET - POINT_FLAG_OFFSET + 4
        try:
            large = bytes(self._read_block(base + POINT_FLAG_OFFSET, span) or b"")
        except Exception:
            large = b""
        if len(large) == span:
            point = bool(_u32be(large, 0))
            baroque = bool(_u32be(large, BAROQUE_ACTIVE_OFFSET - POINT_FLAG_OFFSET))
            red_spent = _u32be(large, BAROQUE_RED_SPENT_OFFSET - POINT_FLAG_OFFSET)
        else:
            point = bool(self._u32(base + POINT_FLAG_OFFSET))
            baroque = bool(self._u32(base + BAROQUE_ACTIVE_OFFSET))
            red_spent = self._u32(base + BAROQUE_RED_SPENT_OFFSET)
        return {
            "pending_current": pending_current,
            "pending_aux": pending_aux,
            "heal_sync": heal_sync,
            "point": point,
            "baroque": baroque,
            "red_spent": red_spent,
        }

    def _state_from_snap(self, snap: dict) -> Optional[_HealthState]:
        base = _safe_int(snap.get("base"), 0)
        maximum = _safe_int(snap.get("max"), 0)
        current = _safe_int(snap.get("cur"), 0)
        auxiliary = _safe_int(snap.get("aux"), 0)
        if not base or maximum <= 0:
            return None
        extra = self._read_extra_state(base)
        return _HealthState(
            base=base,
            max_hp=maximum,
            current_hp=current,
            aux_hp=auxiliary,
            pending_current=_safe_int(extra["pending_current"]),
            pending_aux=_safe_int(extra["pending_aux"]),
            heal_sync_mode=_safe_int(extra["heal_sync"]),
            point=bool(extra["point"]),
            baroque=bool(extra["baroque"]),
            red_spent=_safe_int(extra["red_spent"]),
            char_id=_safe_int(snap.get("id"), 0),
            name=str(snap.get("name") or ""),
        )

    def _remember_attacks(self, snaps: dict[str, dict], frame: int) -> None:
        for slot, snap in (snaps or {}).items():
            if not isinstance(snap, dict):
                continue
            actor = _safe_int(snap.get("attack_property_live_actor"), 0)
            damage = _safe_int(snap.get("attack_property_live_damage"), 0)
            prop_a = _safe_int(snap.get("attack_property_live_a"), 0)
            prop_b = _safe_int(snap.get("attack_property_live_b"), 0)
            if not actor or (not damage and not prop_a and not prop_b):
                continue
            self._recent_attack[str(slot)] = _AttackMemory(
                slot=str(slot),
                frame=int(frame),
                char_id=_safe_int(snap.get("id"), 0),
                name=str(snap.get("name") or ""),
                action_id=self._action_id(snap),
                action_name=str(snap.get("mv_label_display") or snap.get("mv_label") or ""),
                action_frame=_safe_int(snap.get("move_frame") or snap.get("action_frame"), 0),
                base_damage=damage,
                property_a=prop_a,
                property_b=prop_b,
                victim_base=self._u32(actor + 0x34),
            )

    def _select_attack(self, victim_base: int, victim_team: str, frame: int) -> Optional[_AttackMemory]:
        recent = [
            item for item in self._recent_attack.values()
            if 0 <= int(frame) - int(item.frame) <= RECENT_ATTACK_WINDOW_FRAMES
            and (
                item.victim_base == victim_base
                or self._team_of_slot(item.slot) != victim_team
            )
        ]
        if not recent:
            return None
        exact = [item for item in recent if item.victim_base == victim_base]
        pool = exact or recent
        pool.sort(key=lambda item: item.frame, reverse=True)
        return pool[0]

    @staticmethod
    def _transition_changed(before: _HealthState, after: _HealthState) -> bool:
        return any((
            before.max_hp != after.max_hp,
            before.current_hp != after.current_hp,
            before.aux_hp != after.aux_hp,
            before.point != after.point,
            before.baroque != after.baroque,
            before.red_spent != after.red_spent,
        ))

    @staticmethod
    def _classify(before: _HealthState, after: _HealthState) -> tuple[str, list[str]]:
        current_delta = after.current_hp - before.current_hp
        aux_delta = after.aux_hp - before.aux_hp
        red_delta = after.red_hp - before.red_hp
        notes: list[str] = []

        if after.max_hp != before.max_hp:
            return "fighter_reset_or_reloaded", ["maximum health changed"]

        if (
            before.red_hp > 0
            and after.aux_hp == after.current_hp
            and aux_delta < 0
            and (
                (not before.baroque and after.baroque)
                or after.red_spent != before.red_spent
                or after.red_spent == before.red_hp
            )
        ):
            return "baroque_consume", notes

        if current_delta < 0 and aux_delta == 0:
            cost = -current_delta
            expected = predict_current_only_cost(before.max_hp, before.current_hp)
            if cost == expected["actual_cost"] or cost == before.max_hp // 10:
                return "ten_percent_current_only_cost", notes
            return "current_only_damage_or_cost", notes

        if current_delta < 0 and aux_delta < 0:
            return "normal_damage_red_generation", notes

        if current_delta > 0 and aux_delta == 0:
            if red_delta < 0:
                return ("reserve_recovery" if not after.point else "recoverable_heal"), notes
            return "current_only_heal", notes

        if current_delta > 0 and aux_delta > 0:
            if after.current_hp == after.max_hp and after.aux_hp == after.max_hp:
                return ("reserve_full_recovery" if not after.point else "full_health_reset"), notes
            if current_delta == aux_delta:
                return ("reserve_full_recovery" if not after.point else "current_and_ceiling_heal"), notes
            return "mixed_heal", notes

        if current_delta == 0 and aux_delta < 0:
            if after.aux_hp == after.current_hp:
                return "recoverable_health_discarded", notes
            return "ceiling_loss", notes

        if current_delta == 0 and aux_delta > 0:
            return "recoverable_ceiling_gain", notes

        if before.point != after.point:
            return "point_status_change", notes

        if before.baroque != after.baroque:
            return "baroque_status_change", notes

        if before.red_spent != after.red_spent:
            return "baroque_spent_latch_change", notes

        return "unknown_health_transition", notes

    def _append_event(self, event: dict) -> None:
        with self._lock:
            events = self.doc.setdefault("events", [])
            events.append(dict(event))
            if len(events) > MAX_EVENT_HISTORY:
                del events[:-MAX_EVENT_HISTORY]
            signature = "|".join([
                str(event.get("slot") or ""),
                str(event.get("event_kind") or ""),
                str(event.get("char_id") or 0),
                str(event.get("attacker_char_id") or 0),
                str(event.get("attacker_action_id") or 0),
                str(event.get("damage_observed") or 0),
                str(event.get("aux_loss_observed") or 0),
                str(event.get("red_generated_observed") or 0),
            ])
            row = self.doc.setdefault("signatures", {}).setdefault(signature, {
                "count": 0,
                "slot": event.get("slot"),
                "event_kind": event.get("event_kind"),
                "char_id": event.get("char_id"),
                "character_name": event.get("character_name"),
                "attacker_char_id": event.get("attacker_char_id"),
                "attacker_name": event.get("attacker_name"),
                "attacker_action_id": event.get("attacker_action_id"),
                "attacker_action_name": event.get("attacker_action_name"),
                "damage_observed": event.get("damage_observed"),
                "aux_loss_observed": event.get("aux_loss_observed"),
                "red_generated_observed": event.get("red_generated_observed"),
                "prediction_match_count": 0,
            })
            row["count"] = _safe_int(row.get("count")) + 1
            if event.get("prediction_match") is True:
                row["prediction_match_count"] = _safe_int(row.get("prediction_match_count")) + 1
            self._dirty = True

            self._pending_csv.append(dict(event))
            self._change_serial += 1
        self._writer.request()

    def update(self, snaps: dict[str, dict], *, frame: int = 0, now: Optional[float] = None) -> bool:
        now_value = float(time.time() if now is None else now)
        self._remember_attacks(snaps, int(frame))
        changed = False

        for slot, snap in (snaps or {}).items():
            if not isinstance(snap, dict):
                continue
            after = self._state_from_snap(snap)
            if after is None:
                continue
            key = str(slot)
            snap["slot_label"] = key
            before = self._previous.get(key)
            self._previous[key] = after
            if before is None or before.base != after.base:
                self._last_event_by_slot.pop(key, None)
                self._annotate_snapshot(snap, after, None)
                continue
            self._annotate_snapshot(snap, after, self._last_event_by_slot.get(key))
            if not self._transition_changed(before, after):
                continue

            changed = True
            team = self._team_of_slot(key)
            current_delta = after.current_hp - before.current_hp
            aux_delta = after.aux_hp - before.aux_hp
            red_delta = after.red_hp - before.red_hp
            event_kind, notes = self._classify(before, after)
            attack = self._select_attack(after.base, team, int(frame))

            damage = max(0, -current_delta)
            aux_loss = max(0, -aux_delta)
            red_generated = max(0, red_delta)
            predicted_aux: Optional[int] = None
            predicted_red: Optional[int] = None
            difference: Optional[int] = None
            match: Optional[bool] = None
            confidence = "none"

            if event_kind == "normal_damage_red_generation":
                prediction = predict_normal_red_health(damage)
                predicted_aux = prediction["auxiliary_loss"]
                predicted_red = prediction["red_generated"]
                difference = red_generated - predicted_red
                match = difference == 0
                confidence = "high" if attack and attack.frame == int(frame) else "medium"
                if difference:
                    notes.append(
                        "difference can indicate multiple native damage requests between GUI samples"
                    )
            elif event_kind in {"ten_percent_current_only_cost", "current_only_damage_or_cost"}:
                predicted_red = damage
                difference = red_generated - predicted_red
                match = difference == 0
                confidence = "high" if event_kind == "ten_percent_current_only_cost" else "medium"
            elif event_kind == "baroque_consume":
                expected_spent = before.red_hp
                difference = after.red_spent - expected_spent
                match = difference == 0
                confidence = "high"
                predicted_red = -expected_spent
            elif event_kind in {"reserve_recovery", "reserve_full_recovery"}:
                step = predict_reserve_recovery_step(after.max_hp)
                if step > 0:
                    difference = current_delta % step
                    match = difference == 0
                    confidence = "high" if current_delta == step else "medium"
                else:
                    notes.append("predicted reserve recovery request rounded to zero")

            event = {
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_value)),
                "gui_frame": int(frame),
                "slot": key,
                "team": team,
                "fighter_base": f"0x{after.base:08X}",
                "char_id": after.char_id,
                "character_name": after.name,
                "point_before": before.point,
                "point_after": after.point,
                "event_kind": event_kind,
                "max_hp_before": before.max_hp,
                "max_hp_after": after.max_hp,
                "current_before": before.current_hp,
                "current_after": after.current_hp,
                "current_delta": current_delta,
                "aux_before": before.aux_hp,
                "aux_after": after.aux_hp,
                "aux_delta": aux_delta,
                "red_before": before.red_hp,
                "red_after": after.red_hp,
                "red_delta": red_delta,
                "damage_observed": damage,
                "aux_loss_observed": aux_loss,
                "red_generated_observed": red_generated,
                "predicted_aux_loss": predicted_aux if predicted_aux is not None else "",
                "predicted_red_generated": predicted_red if predicted_red is not None else "",
                "prediction_difference": difference if difference is not None else "",
                "prediction_match": match if match is not None else "",
                "prediction_confidence": confidence,
                "predicted_reserve_step": predict_reserve_recovery_step(after.max_hp),
                "pending_current_delta": after.pending_current,
                "pending_aux_delta": after.pending_aux,
                "heal_sync_mode": after.heal_sync_mode,
                "baroque_before": before.baroque,
                "baroque_after": after.baroque,
                "red_spent_before": before.red_spent,
                "red_spent_after": after.red_spent,
                "attacker_slot": attack.slot if attack else "",
                "attacker_char_id": attack.char_id if attack else "",
                "attacker_name": attack.name if attack else "",
                "attacker_action_id": attack.action_id if attack else "",
                "attacker_action_name": attack.action_name if attack else "",
                "attacker_action_frame": attack.action_frame if attack else "",
                "attack_base_damage": attack.base_damage if attack else "",
                "attack_property_a": _hex32(attack.property_a) if attack else "",
                "attack_property_b": _hex32(attack.property_b) if attack else "",
                "notes": "; ".join(notes),
            }
            self._append_event(event)
            self._last_event_by_slot[key] = dict(event)
            self._annotate_snapshot(snap, after, event)
            if self.emit_console:
                print(
                    f"[red health] {key} {event_kind} "
                    f"HP {before.current_hp}->{after.current_hp} "
                    f"AUX {before.aux_hp}->{after.aux_hp} "
                    f"RED {before.red_hp}->{after.red_hp}",
                    flush=True,
                )

        if changed:
            self._writer.request()
        return changed

    def _write_pending(self, *, force: bool, now: Optional[float] = None) -> bool:
        now_value = float(time.time() if now is None else now)
        with self._lock:
            if not self._dirty and not self._pending_csv and not force:
                return False
            if not force and (now_value - self._last_write) < WRITE_INTERVAL_SEC:
                return False
            dirty = bool(self._dirty)
            serial = int(self._change_serial)
            pending = list(self._pending_csv)
            doc = json.loads(json.dumps(self.doc)) if dirty else None

        json_ok = True
        csv_ok = True
        if dirty and doc is not None:
            json_ok = _write_json_atomic(self.path, doc)
        if pending:
            try:
                self.event_path.parent.mkdir(parents=True, exist_ok=True)
                exists = self.event_path.exists() and self.event_path.stat().st_size > 0
                with self.event_path.open("a", newline="", encoding="utf-8") as handle:
                    writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
                    if not exists:
                        writer.writeheader()
                    for event in pending:
                        writer.writerow({key: event.get(key, "") for key in CSV_FIELDS})
            except Exception:
                csv_ok = False

        with self._lock:
            if json_ok and self._change_serial == serial:
                self._dirty = False
            if csv_ok and pending:
                del self._pending_csv[:len(pending)]
            self._last_write = now_value
        return bool(json_ok and csv_ok)


    def flush(self) -> bool:
        result = {"ok": True}
        self._writer.close(
            final_callback=lambda: result.__setitem__("ok", self._write_pending(force=True)),
            timeout=1.5,
        )
        return bool(result["ok"])


__all__ = [
    "RuntimeRedHealthProfiler",
    "recoverable_health",
    "predict_normal_red_health",
    "predict_current_only_cost",
    "predict_reserve_recovery_step",
    "default_profile_path",
    "default_event_path",
]
