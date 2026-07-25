"""Read-only runtime profiler for TvC team meter changes.

The native hit path derives a shared meter pool from attack base damage, then
awards that pool to the attacker and, for eligible attacks, 75 percent to the
victim. The final team meter writer applies a separate 1.3 positive-gain
multiplier before clamping the result to the team cap.

This profiler records observed team meter deltas together with the most recent
live attack packet and a prediction that follows the confirmed native rounding
stages. It never writes to Dolphin memory.
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
PROFILE_FILE = "runtime_meter_generation_profiles.json"
EVENT_FILE = "runtime_meter_generation_events.csv"
SAMPLING_MODE = "team_meter_delta_with_native_hit_prediction_v1"

TEAM_METER_ADDR = {
    "P1": 0x9246BA0C,
    "P2": 0x927EBA2C,
}
TEAM_METER_CAP_ADDR = {
    "P1": 0x9246BA08,
    "P2": 0x927EBA28,
}

POINT_FLAG_OFFSET = 0x44A0
BAROQUE_ACTIVE_OFFSET = 0x44BC
CHARACTER_STATE_OFFSET = 0x45FC
ZERO_STATE_OFFSET = 0x4604
METER_SUPPRESS_FLAGS_A_OFFSET = 0x007C
METER_SUPPRESS_FLAGS_B_OFFSET = 0x0080
VICTIM_DAMAGE_MULT_OFFSET = 0x11C0

METER_SUPPRESS_FLAGS_A_MASK = 0x00080000
METER_SUPPRESS_FLAGS_B_MASK = 0x00040000
ATTACKER_PACKET_NO_METER_MASK = 0x00010000
LIGHT_TIER_MASK = 0x00000001
FIGHTER_STATUS_NO_METER_MASK = 0x02000000

ZERO_ID = 29
SOKI_ID = 21

DEFAULT_CAP = 50000
BAR_SIZE = 10000
RECENT_ATTACK_WINDOW_FRAMES = 8
MAX_EVENT_HISTORY = 20000
WRITE_INTERVAL_SEC = 0.75

CSV_FIELDS = [
    "timestamp_utc",
    "gui_frame",
    "team",
    "meter_before",
    "meter_after",
    "meter_delta",
    "bar_delta",
    "event_kind",
    "source_role",
    "source_slot",
    "source_char_id",
    "source_name",
    "source_action_id",
    "source_action_name",
    "source_action_frame",
    "victim_slot",
    "victim_char_id",
    "victim_name",
    "base_damage",
    "property_a",
    "property_b",
    "victim_damage_multiplier",
    "baroque_active",
    "fighter_status_58",
    "meter_flags_7c",
    "meter_flags_80",
    "zero_state_4604",
    "soki_state_45fc",
    "predicted_shared_pool",
    "predicted_request",
    "predicted_final_delta",
    "prediction_difference",
    "prediction_match",
    "prediction_confidence",
    "hp_loss_p1",
    "hp_loss_p2",
    "notes",
]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return float(default)
    return parsed if math.isfinite(parsed) else float(default)


def _f32(value: float) -> float:
    return struct.unpack(">f", struct.pack(">f", float(value)))[0]


def _trunc_f32(value: float) -> int:
    return math.trunc(_f32(value))


def _u32be(blob: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(blob):
        return 0
    return struct.unpack_from(">I", blob, offset)[0]


def _f32be(blob: bytes, offset: int, default: float = 0.0) -> float:
    if offset < 0 or offset + 4 > len(blob):
        return float(default)
    try:
        value = struct.unpack_from(">f", blob, offset)[0]
    except Exception:
        return float(default)
    if not math.isfinite(value) or abs(value) > 100000.0:
        return float(default)
    return float(value)


def _hex32(value: Any) -> str:
    return f"0x{_safe_int(value) & 0xFFFFFFFF:08X}"


def predict_native_hit_meter(
    base_damage: int,
    property_a: int,
    property_b: int,
    *,
    victim_damage_multiplier: float = 1.0,
    baroque_active: bool = False,
    role: str = "attacker",
    fighter_status_58: int = 0,
    meter_flags_7c: int = 0,
    meter_flags_80: int = 0,
    attacker_char_id: int = 0,
    zero_state_4604: int = 1,
    soki_state_45fc: int = 0,
) -> dict:
    """Emulate the confirmed normal hit meter path and truncation stages."""
    damage = max(0, _safe_int(base_damage))
    prop_a = _safe_int(property_a) & 0xFFFFFFFF
    prop_b = _safe_int(property_b) & 0xFFFFFFFF
    victim_mult = _safe_float(victim_damage_multiplier, 1.0)
    if victim_mult <= 0.0 or victim_mult > 8.0:
        victim_mult = 1.0

    adjusted_damage = _trunc_f32(_f32(float(damage)) * _f32(victim_mult))
    stage_0475 = _f32(_f32(0.4749999940395355) * _f32(float(adjusted_damage)))
    pre_quarter = _trunc_f32(_f32(stage_0475) * _f32(0.75))
    if baroque_active:
        pre_quarter = math.trunc(pre_quarter / 2)
    shared_pool = math.trunc((pre_quarter * 25) / 100)

    no_light_meter = bool(prop_a & LIGHT_TIER_MASK)
    no_packet_meter = bool(prop_b & ATTACKER_PACKET_NO_METER_MASK)
    fighter_status_block = bool(_safe_int(fighter_status_58) & FIGHTER_STATUS_NO_METER_MASK)
    writer_block = bool(
        (_safe_int(meter_flags_7c) & METER_SUPPRESS_FLAGS_A_MASK)
        or (_safe_int(meter_flags_80) & METER_SUPPRESS_FLAGS_B_MASK)
    )
    zero_block = bool(attacker_char_id == ZERO_ID and _safe_int(zero_state_4604) <= 0)
    soki_block = bool(attacker_char_id == SOKI_ID and (_safe_int(soki_state_45fc) & 1))

    role_key = str(role or "attacker").lower()
    if role_key == "victim":
        request = 0 if no_light_meter else _trunc_f32(_f32(float(shared_pool)) * _f32(0.75))
        native_gate_block = no_light_meter
    else:
        request = shared_pool
        native_gate_block = (
            no_light_meter
            or no_packet_meter
            or fighter_status_block
            or zero_block
            or soki_block
        )
        if native_gate_block:
            request = 0

    if request > 0 and writer_block:
        final_delta = 0
    elif request > 0:
        final_delta = _trunc_f32(_f32(float(request)) * _f32(1.2999999523162842))
    else:
        final_delta = request

    reasons: list[str] = []
    if no_light_meter:
        reasons.append("property A bit 0 suppresses hit meter")
    if role_key != "victim" and no_packet_meter:
        reasons.append("property B 0x00010000 suppresses attacker meter")
    if role_key != "victim" and fighter_status_block:
        reasons.append("fighter +0x58 suppresses attacker meter")
    if role_key != "victim" and zero_block:
        reasons.append("Zero character-state gate suppresses attacker meter")
    if role_key != "victim" and soki_block:
        reasons.append("Soki character-state gate suppresses attacker meter")
    if writer_block:
        reasons.append("team meter writer suppression flag is active")

    return {
        "adjusted_damage": adjusted_damage,
        "pre_quarter_gain": pre_quarter,
        "shared_pool": shared_pool,
        "requested_delta": request,
        "final_delta": final_delta,
        "role": role_key,
        "blocked": bool(native_gate_block or writer_block),
        "reasons": reasons,
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
    victim_slot: str = ""
    victim_base: int = 0


class RuntimeMeterGenerationProfiler:
    """Record team meter transitions and native hit-meter predictions."""

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
        self._previous_meter: Dict[str, int] = {}
        self._previous_hp: Dict[str, int] = {}
        self._recent_attack: Dict[str, _AttackMemory] = {}
        self._last_event_by_team: Dict[str, dict] = {}
        self._pending_csv: list[dict] = []
        self._change_serial = 0
        self._writer = DeferredWorkLoop(
            lambda: self._write_pending(force=False),
            interval=WRITE_INTERVAL_SEC,
            name="TvCMeterProfileWriter",
        )


    def _annotate_snapshots(self, snaps: dict[str, dict], meter_now: dict[str, int]) -> None:
        for slot, snap in (snaps or {}).items():
            if not isinstance(snap, dict):
                continue
            team = self._team_of_slot(str(slot))
            current = meter_now.get(team, _safe_int(snap.get("meter"), 0))
            snap["meter_profile_current"] = int(current)
            event = self._last_event_by_team.get(team)
            if not isinstance(event, dict):
                snap["meter_profile_last_delta"] = 0
                snap["meter_profile_last_kind"] = ""
                snap["meter_profile_last_role"] = ""
                snap["meter_profile_last_source"] = ""
                snap["meter_profile_last_move"] = ""
                snap["meter_profile_last_predicted"] = None
                snap["meter_profile_last_difference"] = None
                snap["meter_profile_last_match"] = None
                snap["meter_profile_last_base_damage"] = 0
                snap["meter_profile_last_property_a"] = ""
                snap["meter_profile_last_property_b"] = ""
                continue
            snap["meter_profile_last_delta"] = _safe_int(event.get("meter_delta"), 0)
            snap["meter_profile_last_kind"] = str(event.get("event_kind") or "")
            snap["meter_profile_last_role"] = str(event.get("source_role") or "")
            snap["meter_profile_last_source"] = str(event.get("source_name") or event.get("source_slot") or "")
            snap["meter_profile_last_move"] = str(event.get("source_action_name") or "")
            predicted = event.get("predicted_final_delta")
            difference = event.get("prediction_difference")
            snap["meter_profile_last_predicted"] = None if predicted in (None, "") else _safe_int(predicted)
            snap["meter_profile_last_difference"] = None if difference in (None, "") else _safe_int(difference)
            snap["meter_profile_last_match"] = event.get("prediction_match")
            snap["meter_profile_last_base_damage"] = _safe_int(event.get("base_damage"), 0)
            snap["meter_profile_last_property_a"] = str(event.get("property_a") or "")
            snap["meter_profile_last_property_b"] = str(event.get("property_b") or "")

    def _u32(self, address: int, default: int = 0) -> int:
        try:
            value = self._read_u32(int(address))
        except Exception:
            return int(default)
        return _safe_int(value, default) & 0xFFFFFFFF

    def _fighter_state(self, base: int) -> dict:
        if not base:
            return {
                "point": False,
                "baroque": False,
                "status_58": 0,
                "flags_7c": 0,
                "flags_80": 0,
                "zero_state": 1,
                "soki_state": 0,
                "victim_mult": 1.0,
            }
        start = 0x58
        end = VICTIM_DAMAGE_MULT_OFFSET + 4
        try:
            blob = bytes(self._read_block(base + start, end - start) or b"")
        except Exception:
            blob = b""
        if len(blob) != end - start:
            return {
                "point": bool(self._u32(base + POINT_FLAG_OFFSET)),
                "baroque": bool(self._u32(base + BAROQUE_ACTIVE_OFFSET)),
                "status_58": self._u32(base + 0x58),
                "flags_7c": self._u32(base + METER_SUPPRESS_FLAGS_A_OFFSET),
                "flags_80": self._u32(base + METER_SUPPRESS_FLAGS_B_OFFSET),
                "zero_state": self._u32(base + ZERO_STATE_OFFSET, 1),
                "soki_state": self._u32(base + CHARACTER_STATE_OFFSET),
                "victim_mult": 1.0,
            }
        rel = lambda absolute: int(absolute) - start
        victim_mult = _f32be(blob, rel(VICTIM_DAMAGE_MULT_OFFSET), 1.0)
        if victim_mult <= 0.0 or victim_mult > 8.0:
            victim_mult = 1.0
        return {
            "point": bool(_u32be(blob, rel(POINT_FLAG_OFFSET))),
            "baroque": bool(_u32be(blob, rel(BAROQUE_ACTIVE_OFFSET))),
            "status_58": _u32be(blob, rel(0x58)),
            "flags_7c": _u32be(blob, rel(METER_SUPPRESS_FLAGS_A_OFFSET)),
            "flags_80": _u32be(blob, rel(METER_SUPPRESS_FLAGS_B_OFFSET)),
            "zero_state": _u32be(blob, rel(ZERO_STATE_OFFSET)),
            "soki_state": _u32be(blob, rel(CHARACTER_STATE_OFFSET)),
            "victim_mult": victim_mult,
        }

    @staticmethod
    def _action_id(snap: dict) -> int:
        for key in ("attA", "attB", "timing_action_id", "move_id", "mv_id_display"):
            value = _safe_int(snap.get(key), 0)
            if value:
                return value
        return 0

    def _remember_attacks(self, snaps: dict[str, dict], frame: int) -> None:
        base_to_slot = {
            _safe_int(snap.get("base")): str(slot)
            for slot, snap in (snaps or {}).items()
            if isinstance(snap, dict) and _safe_int(snap.get("base"))
        }
        for slot, snap in (snaps or {}).items():
            if not isinstance(snap, dict):
                continue
            actor = _safe_int(snap.get("attack_property_live_actor"), 0)
            damage = _safe_int(snap.get("attack_property_live_damage"), 0)
            prop_a = _safe_int(snap.get("attack_property_live_a"), 0)
            prop_b = _safe_int(snap.get("attack_property_live_b"), 0)
            if not actor or (not damage and not prop_a and not prop_b):
                continue
            victim_base = 0
            try:
                victim_base = self._u32(actor + 0x34)
            except Exception:
                victim_base = 0
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
                victim_slot=base_to_slot.get(victim_base, ""),
                victim_base=victim_base,
            )

    @staticmethod
    def _team_of_slot(slot: str) -> str:
        return "P1" if str(slot).startswith("P1") else "P2"

    def _select_attack(
        self,
        attack_team: str,
        frame: int,
    ) -> Optional[_AttackMemory]:
        recent = [
            item for item in self._recent_attack.values()
            if self._team_of_slot(item.slot) == attack_team
            and 0 <= int(frame) - int(item.frame) <= RECENT_ATTACK_WINDOW_FRAMES
        ]
        if not recent:
            return None
        recent.sort(key=lambda item: item.frame, reverse=True)
        return recent[0]

    def _point_slot(self, team: str, snaps: dict[str, dict]) -> str:
        candidates: list[str] = []
        for slot, snap in (snaps or {}).items():
            if not str(slot).startswith(team) or not isinstance(snap, dict):
                continue
            base = _safe_int(snap.get("base"), 0)
            if not base:
                continue
            if bool(self._u32(base + POINT_FLAG_OFFSET)):
                return str(slot)
            candidates.append(str(slot))
        return sorted(candidates)[0] if candidates else ""

    def _append_event(self, event: dict) -> None:
        with self._lock:
            events = self.doc.setdefault("events", [])
            events.append(dict(event))
            if len(events) > MAX_EVENT_HISTORY:
                del events[:-MAX_EVENT_HISTORY]
            signature = "|".join([
                str(event.get("team") or ""),
                str(event.get("event_kind") or ""),
                str(event.get("source_role") or ""),
                str(event.get("source_char_id") or 0),
                str(event.get("source_action_id") or 0),
                str(event.get("base_damage") or 0),
                str(event.get("property_a") or ""),
                str(event.get("property_b") or ""),
                str(event.get("meter_delta") or 0),
            ])
            row = self.doc.setdefault("signatures", {}).setdefault(signature, {
                "count": 0,
                "team": event.get("team"),
                "event_kind": event.get("event_kind"),
                "source_role": event.get("source_role"),
                "source_char_id": event.get("source_char_id"),
                "source_name": event.get("source_name"),
                "source_action_id": event.get("source_action_id"),
                "source_action_name": event.get("source_action_name"),
                "base_damage": event.get("base_damage"),
                "property_a": event.get("property_a"),
                "property_b": event.get("property_b"),
                "meter_delta": event.get("meter_delta"),
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

        hp_loss_by_team = {"P1": 0, "P2": 0}
        slot_hp_loss: dict[str, int] = {}
        for slot, snap in (snaps or {}).items():
            if not isinstance(snap, dict):
                continue
            hp = _safe_int(snap.get("cur"), 0)
            previous = self._previous_hp.get(str(slot), hp)
            loss = max(0, previous - hp)
            slot_hp_loss[str(slot)] = loss
            hp_loss_by_team[self._team_of_slot(str(slot))] += loss
            self._previous_hp[str(slot)] = hp

        meter_now: dict[str, int] = {}
        for team in ("P1", "P2"):
            direct = self._u32(TEAM_METER_ADDR[team], -1)
            if direct == 0xFFFFFFFF:
                direct = -1
            if direct < 0:
                direct = next(
                    (_safe_int(snap.get("meter"), -1) for slot, snap in (snaps or {}).items()
                     if str(slot).startswith(team) and isinstance(snap, dict)),
                    -1,
                )
            if direct >= 0:
                meter_now[team] = direct

        if not self._previous_meter:
            self._previous_meter.update(meter_now)
            self._annotate_snapshots(snaps, meter_now)
            return False

        changed = False
        for team, current in meter_now.items():
            previous = self._previous_meter.get(team, current)
            delta = int(current) - int(previous)
            self._previous_meter[team] = int(current)
            if delta == 0:
                continue
            changed = True
            own_loss = hp_loss_by_team.get(team, 0)
            opponent = "P2" if team == "P1" else "P1"
            opponent_loss = hp_loss_by_team.get(opponent, 0)
            if delta < 0:
                role = "spend_or_drain"
                attack_team = team
            elif own_loss > 0 and opponent_loss <= 0:
                role = "victim"
                attack_team = opponent
            elif opponent_loss > 0:
                role = "attacker"
                attack_team = team
            else:
                role = "unknown"
                attack_team = team
            attack = self._select_attack(attack_team, int(frame))

            source_slot = attack.slot if attack else self._point_slot(attack_team, snaps)
            source_snap = (snaps or {}).get(source_slot) if source_slot else None
            source_snap = source_snap if isinstance(source_snap, dict) else {}
            source_base = _safe_int(source_snap.get("base"), 0)
            source_state = self._fighter_state(source_base)

            victim_slot = attack.victim_slot if attack else ""
            if not victim_slot and role == "attacker":
                victim_slot = self._point_slot(opponent, snaps)
            elif not victim_slot and role == "victim":
                victim_slot = self._point_slot(team, snaps)
            victim_snap = (snaps or {}).get(victim_slot) if victim_slot else None
            victim_snap = victim_snap if isinstance(victim_snap, dict) else {}
            victim_base = _safe_int(victim_snap.get("base"), 0)
            victim_state = self._fighter_state(victim_base)

            prediction = None
            confidence = "none"
            notes: list[str] = []
            if attack and delta > 0 and role in {"attacker", "victim"}:
                attacker_state = source_state
                attacker_char_id = attack.char_id
                prediction = predict_native_hit_meter(
                    attack.base_damage,
                    attack.property_a,
                    attack.property_b,
                    victim_damage_multiplier=victim_state.get("victim_mult", 1.0),
                    baroque_active=bool(attacker_state.get("baroque")),
                    role=role,
                    fighter_status_58=attacker_state.get("status_58", 0),
                    meter_flags_7c=attacker_state.get("flags_7c", 0),
                    meter_flags_80=attacker_state.get("flags_80", 0),
                    attacker_char_id=attacker_char_id,
                    zero_state_4604=attacker_state.get("zero_state", 1),
                    soki_state_45fc=attacker_state.get("soki_state", 0),
                )
                confidence = "high" if opponent_loss > 0 or own_loss > 0 else "medium"
                if attack.frame != int(frame):
                    notes.append(f"attack packet retained from {int(frame) - attack.frame} frame(s) earlier")
            elif delta > 0:
                notes.append("positive delta had no recent attributable attack packet")
            else:
                notes.append("negative delta is a meter spend or scripted drain")

            predicted_final = prediction.get("final_delta") if prediction else None
            difference = (delta - int(predicted_final)) if predicted_final is not None else None
            match = (difference == 0) if difference is not None else None
            if prediction and prediction.get("reasons"):
                notes.extend(prediction["reasons"])

            event = {
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_value)),
                "gui_frame": int(frame),
                "team": team,
                "meter_before": int(previous),
                "meter_after": int(current),
                "meter_delta": int(delta),
                "bar_delta": float(delta) / float(BAR_SIZE),
                "event_kind": "gain" if delta > 0 else "spend_or_drain",
                "source_role": role,
                "source_slot": source_slot,
                "source_char_id": attack.char_id if attack else _safe_int(source_snap.get("id"), 0),
                "source_name": attack.name if attack else str(source_snap.get("name") or ""),
                "source_action_id": attack.action_id if attack else self._action_id(source_snap),
                "source_action_name": attack.action_name if attack else str(source_snap.get("mv_label") or ""),
                "source_action_frame": attack.action_frame if attack else 0,
                "victim_slot": victim_slot,
                "victim_char_id": _safe_int(victim_snap.get("id"), 0),
                "victim_name": str(victim_snap.get("name") or ""),
                "base_damage": attack.base_damage if attack else 0,
                "property_a": _hex32(attack.property_a) if attack else "",
                "property_b": _hex32(attack.property_b) if attack else "",
                "victim_damage_multiplier": victim_state.get("victim_mult", 1.0),
                "baroque_active": bool(source_state.get("baroque")),
                "fighter_status_58": _hex32(source_state.get("status_58", 0)),
                "meter_flags_7c": _hex32(source_state.get("flags_7c", 0)),
                "meter_flags_80": _hex32(source_state.get("flags_80", 0)),
                "zero_state_4604": source_state.get("zero_state", 0),
                "soki_state_45fc": source_state.get("soki_state", 0),
                "predicted_shared_pool": prediction.get("shared_pool") if prediction else "",
                "predicted_request": prediction.get("requested_delta") if prediction else "",
                "predicted_final_delta": predicted_final if predicted_final is not None else "",
                "prediction_difference": difference if difference is not None else "",
                "prediction_match": match if match is not None else "",
                "prediction_confidence": confidence,
                "hp_loss_p1": hp_loss_by_team.get("P1", 0),
                "hp_loss_p2": hp_loss_by_team.get("P2", 0),
                "notes": "; ".join(notes),
            }
            self._append_event(event)
            self._last_event_by_team[team] = dict(event)
            if self.emit_console:
                predicted_text = "?" if predicted_final is None else str(predicted_final)
                print(
                    f"[meter profile] {team} {previous}->{current} ({delta:+d}) "
                    f"role={role} move={event['source_action_name'] or event['source_action_id']} "
                    f"pred={predicted_text}",
                    flush=True,
                )

        self._annotate_snapshots(snaps, meter_now)
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
    "RuntimeMeterGenerationProfiler",
    "predict_native_hit_meter",
    "default_profile_path",
    "default_event_path",
]
