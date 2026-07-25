"""
hud_overlay_manager.py
----------------------
Owns the hud_overlay subprocess and the per-frame data file it reads.

Public surface:
    mgr = HudOverlayManager(move_map, global_map)
    mgr.write_data(render_snap_by_slot, last_scan_normals, mission_mgr)
    mgr.check_proc()   # call each frame; detects if the proc died
    # subprocess is launched/stopped by master_overlay; this manager
    # only owns the data file write.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING

from tvcgui.core.paths import user_data_path
from tvcgui.features.frame_data.attack_property_runtime import resolve_live_attack_property
from tvcgui.runtime import input_monitor
from tvcgui.features.overlay.damage_scaling import annotate_damage_scaling_payload

if TYPE_CHECKING:
    from tvcgui.features.training.mission_manager import MissionManager

HUD_OVERLAY_DATA_FILE = user_data_path("overlay", "hud_overlay_data.json")
INPUT_SAMPLER_HZ = 240.0
INPUT_SAMPLE_QUEUE_LIMIT = 128

ATTACK_PROPERTY_SHORT_LABELS = {
    0x01: "UNBLK",
    0x02: "UNBLK",
    0x04: "UNBLK",
    0x09: "MID",
    0x0A: "MID",
    0x0C: "MID",
    0x11: "OVERHEAD",
    0x12: "OVERHEAD",
    0x14: "OVERHEAD",
    0x21: "LOW",
    0x22: "LOW",
    0x24: "LOW",
}


def _known_attack_property(value) -> bool:
    try:
        packed = int(value) & 0xFF
    except Exception:
        return False
    guard = packed & 0x38
    strength = packed & 0x07
    return guard in (0x00, 0x08, 0x10, 0x20) and strength in (0x01, 0x02, 0x04)


def _overlay_attack_property_label(value) -> str:
    try:
        packed = int(value) & 0xFF
    except Exception:
        return ""
    if not _known_attack_property(packed):
        return ""
    guard = packed & 0x38
    if guard == 0x10:
        return "OVERHEAD"
    if guard == 0x20:
        return "LOW"
    if guard == 0x08:
        return "MID"
    return "UNBLK"



def _extract_move_attack_property(value, depth: int = 0):
    """Read the first hit property's byte from the exact tree move row."""
    if depth > 7:
        return None
    if isinstance(value, dict):
        direct = value.get("attack_property")
        try:
            parsed = int(direct) & 0xFF
        except Exception:
            parsed = None
        if _known_attack_property(parsed):
            return parsed
        for key in ("hit_segments", "damage_segments", "segments", "hits", "phases", "owned_fields"):
            child = value.get(key)
            found = _extract_move_attack_property(child, depth + 1)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for child in value:
            found = _extract_move_attack_property(child, depth + 1)
            if found is not None:
                return found
    return None


class HudOverlayManager:
    """
    Writes hud_overlay_data.json each frame so the hud_overlay subprocess
    (parented to Dolphin) can render the transparent HUD.

    Parameters
    ----------
    move_map : dict
        Per-character anim-ID -> label mapping.
    global_map : dict
        Global anim-ID -> label mapping.
    """

    def __init__(self, move_map: dict, global_map: dict) -> None:
        self._move_map = move_map
        self._global_map = global_map

        self._proc: subprocess.Popen | None = None
        self._active: bool = False
        self._last_serialized: str = ""
        self._payload_condition = threading.Condition()
        self._pending_payload: dict | None = None
        self._payload_writer_stop = False
        self._payload_writer_thread = threading.Thread(
            target=self._payload_writer_loop,
            name="TvCOverlayPayloadWriter",
            daemon=True,
        )
        self._payload_writer_thread.start()

        # The GUI and transparent overlay run independently, and the GUI can be
        # busy for more than one game frame. Poll the live input packet on a
        # dedicated high-frequency thread so short taps and neutral separators
        # survive even when the normal render loop is late.
        self._input_sample_seq: int = 0
        self._input_samples_by_slot: dict[str, list[dict]] = {}
        self._input_sampler_targets: dict[str, int] = {}
        self._input_latest_by_slot: dict[str, dict] = {}
        self._input_raw_state_by_slot: dict[str, tuple[int, int, int, int, int, int]] = {}
        self._input_sample_listeners: list = []
        self._input_lock = threading.RLock()
        self._input_sampler_stop = threading.Event()
        self._input_sampler_thread = threading.Thread(
            target=self._input_sampler_loop,
            name="TvCInputSampler",
            daemon=True,
        )
        self._input_sampler_thread.start()

    def _set_input_sampler_targets(self, render_snap_by_slot: dict) -> None:
        targets: dict[str, int] = {}
        for slot_label, snap in (render_snap_by_slot or {}).items():
            if not isinstance(snap, dict):
                continue
            try:
                base = int(snap.get("base") or 0)
            except Exception:
                base = 0
            if base:
                targets[str(slot_label)] = base
        with self._input_lock:
            self._input_sampler_targets = targets

    def _queue_input_sample(self, slot_label: str, packet: dict) -> None:
        held = int((packet or {}).get("held", 0) or 0) & 0xFFFF
        raw_pressed = int((packet or {}).get("pressed", 0) or 0) & 0xFFFF
        raw_released = int((packet or {}).get("released", 0) or 0) & 0xFFFF
        action_id = int((packet or {}).get("action_id", 0) or 0) & 0x7FFF
        action_frame = max(0, int((packet or {}).get("action_frame", 0) or 0))
        current_hp = int((packet or {}).get("current_hp", 0) or 0)
        queued_sample = None
        listeners = []

        with self._input_lock:
            previous = self._input_raw_state_by_slot.get(slot_label)
            if previous is None:
                previous_held = held
                previous_pressed = 0
                previous_released = 0
                previous_action = action_id
                previous_action_frame = action_frame
                previous_hp = current_hp
            else:
                (
                    previous_held, previous_pressed, previous_released,
                    previous_action, previous_action_frame, previous_hp,
                ) = previous

            fresh_pressed = raw_pressed & ~int(previous_pressed)
            fresh_released = raw_released & ~int(previous_released)
            held_changed = previous is None or held != int(previous_held)
            action_changed = previous is None or action_id != int(previous_action)
            action_frame_changed = previous is None or action_frame != int(previous_action_frame)
            hp_changed = previous is None or current_hp != int(previous_hp)

            self._input_raw_state_by_slot[slot_label] = (
                held, raw_pressed, raw_released, action_id, action_frame, current_hp,
            )
            self._input_latest_by_slot[slot_label] = {
                **dict(packet or {}),
                "held": held,
                "pressed": fresh_pressed,
                "released": fresh_released,
                "action_frame": action_frame,
            }

            meaningful_change = bool(
                held_changed
                or fresh_pressed
                or fresh_released
                or action_changed
                or hp_changed
            )
            if not meaningful_change and not action_frame_changed:
                return

            if meaningful_change:
                self._input_sample_seq += 1
            queued_sample = {
                "seq": self._input_sample_seq if meaningful_change else 0,
                "slot": str(slot_label),
                "base": int((packet or {}).get("base", 0) or 0),
                "held": held,
                "pressed": fresh_pressed,
                "released": fresh_released,
                "char_id": int((packet or {}).get("char_id", 0) or 0),
                "action_id": action_id,
                "action_frame": action_frame,
                "current_hp": current_hp,
                "sample_ns": time.monotonic_ns(),
            }
            # The mission queue keeps only meaningful edges. Action-frame-only
            # samples go straight to realtime listeners, avoiding 60 duplicate
            # route events per second per fighter.
            if meaningful_change:
                queue = self._input_samples_by_slot.setdefault(slot_label, [])
                queue.append(queued_sample)
                del queue[:-INPUT_SAMPLE_QUEUE_LIMIT]
            listeners = list(self._input_sample_listeners)

        # Realtime listeners must never wait for the GUI frame. They are kept
        # tiny and run outside the sampler lock so one forced action cannot
        # block input collection for the other slots.
        for listener in listeners:
            try:
                listener(str(slot_label), dict(queued_sample))
            except Exception:
                continue

    def _input_sampler_loop(self) -> None:
        interval = 1.0 / max(60.0, float(INPUT_SAMPLER_HZ))
        next_tick = time.perf_counter()
        while not self._input_sampler_stop.is_set():
            with self._input_lock:
                targets = dict(self._input_sampler_targets)
            for slot_label, base in targets.items():
                try:
                    packet = input_monitor.read_overlay_input_packet(slot_label, base)
                except Exception:
                    continue
                if packet:
                    self._queue_input_sample(slot_label, packet)

            next_tick += interval
            delay = next_tick - time.perf_counter()
            if delay <= 0.0:
                next_tick = time.perf_counter()
                delay = 0.001
            self._input_sampler_stop.wait(delay)

    def _input_snapshot_for_slot(self, slot_label: str, base: int) -> tuple[dict, list[dict]]:
        with self._input_lock:
            packet = dict(self._input_latest_by_slot.get(slot_label) or {})
            samples = list(self._input_samples_by_slot.get(slot_label) or [])
        if not packet:
            try:
                packet = input_monitor.read_overlay_input_packet(slot_label, base)
            except Exception:
                packet = {}
            if packet:
                self._queue_input_sample(slot_label, packet)
                with self._input_lock:
                    packet = dict(self._input_latest_by_slot.get(slot_label) or packet)
                    samples = list(self._input_samples_by_slot.get(slot_label) or [])
        return packet, samples

    def _queue_payload(self, payload: dict) -> None:
        with self._payload_condition:
            self._pending_payload = payload
            self._payload_condition.notify()

    def _payload_writer_loop(self) -> None:
        while True:
            with self._payload_condition:
                while self._pending_payload is None and not self._payload_writer_stop:
                    self._payload_condition.wait(timeout=0.25)
                if self._payload_writer_stop and self._pending_payload is None:
                    return
                payload = self._pending_payload
                self._pending_payload = None
            try:
                serialized = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
                if serialized == self._last_serialized and os.path.isfile(HUD_OVERLAY_DATA_FILE):
                    continue
                os.makedirs(os.path.dirname(HUD_OVERLAY_DATA_FILE), exist_ok=True)
                tmp = f"{HUD_OVERLAY_DATA_FILE}.tmp"
                with open(tmp, "w", encoding="utf-8") as handle:
                    handle.write(serialized)
                os.replace(tmp, HUD_OVERLAY_DATA_FILE)
                self._last_serialized = serialized
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active

    def prime_input_sampler_targets(self, render_snap_by_slot: dict) -> None:
        """Point the 240 Hz input sampler at the current live fighter objects."""
        self._set_input_sampler_targets(render_snap_by_slot)

    def add_input_sample_listener(self, listener) -> None:
        if not callable(listener):
            return
        with self._input_lock:
            if listener not in self._input_sample_listeners:
                self._input_sample_listeners.append(listener)

    def remove_input_sample_listener(self, listener) -> None:
        with self._input_lock:
            self._input_sample_listeners = [
                item for item in self._input_sample_listeners if item is not listener
            ]

    def mission_input_bundle(self, slot_label: str, fighter_base: int = 0) -> tuple[dict, list[dict]]:
        """Return the latest packet plus the sampler's ordered edge queue."""
        return self._input_snapshot_for_slot(str(slot_label), int(fighter_base or 0))

    def write_data(
        self,
        render_snap_by_slot: dict,
        last_scan_normals,
        mission_mgr: "MissionManager",
        punish_overlay: dict | None = None,
        timing_payload: dict | None = None,
    ) -> None:
        """
        Build and write hud_overlay_data.json from the current frame's
        fighter snapshots, scan-normals data, and mission state.
        """
        payload: dict = {}
        self._set_input_sampler_targets(render_snap_by_slot)

        mission_active_slot = mission_mgr.active_slot

        # Compute VAR state once for the active mission slot
        mission_var: dict = {}
        if mission_active_slot:
            try:
                mission_var = mission_mgr.var_state(mission_active_slot, render_snap_by_slot)
            except Exception:
                mission_var = {}

        for slot_label, snap in render_snap_by_slot.items():
            # The main loop already produced the authoritative final label.
            # Do not re-read the profiler or rebuild the name in this sink.
            snap = dict(snap or {})
            cur_anim = snap.get("attA") or snap.get("attB")
            mv_label = snap.get("mv_label")
            mv_label_display = (
                snap.get("final_move_label")
                or snap.get("mv_label_display")
                or mv_label
            )
            active_start = None
            active_end = None
            attack_property = None
            attack_property_label = ""
            input_packet, input_samples = self._input_snapshot_for_slot(
                slot_label,
                int(snap.get("base") or 0),
            )
            input_held = int(input_packet.get("held", 0) or 0) & 0xFFFF
            input_pressed = int(input_packet.get("pressed", 0) or 0) & 0xFFFF
            input_released = int(input_packet.get("released", 0) or 0) & 0xFFFF

            slot_tree_row = None
            if last_scan_normals and cur_anim is not None:
                for slot_data in last_scan_normals:
                    if slot_data.get("slot_label") == slot_label:
                        slot_tree_row = slot_data
                        for mv in slot_data.get("moves", []):
                            if mv.get("id") == cur_anim:
                                active_start = mv.get("active_start")
                                active_end = mv.get("active_end")
                                attack_property = _extract_move_attack_property(mv)
                                attack_property_label = _overlay_attack_property_label(attack_property)
                                break
                        break

            # The compact preview can omit lazily discovered property packets.
            # Resolve the exact live action through the same character-table and
            # packet locator used by the frame-data tree. This is cached by live
            # table root plus action ID, so normal frames perform no extra scan.
            if cur_anim is not None and not attack_property_label:
                try:
                    tree_root = int((slot_tree_row or {}).get("chr_tbl_abs") or 0)
                except Exception:
                    tree_root = 0
                attack_property = resolve_live_attack_property(
                    int(snap.get("base") or 0),
                    int(cur_anim),
                    chr_tbl_abs=tree_root or None,
                )
                attack_property_label = _overlay_attack_property_label(attack_property)

            partner_slot = mission_var.get("partner_slot")

            payload[slot_label] = {
                "name":                   snap.get("name"),
                "cur":                    snap.get("cur"),
                "max":                    snap.get("max"),
                "meter":                  snap.get("meter"),
                "mv_id_display":          cur_anim,
                "mv_id_label_display":    snap.get("mv_id_label_display"),
                "mv_label":               mv_label,
                "mv_label_display":       mv_label_display,
                "final_move_label":       mv_label_display,
                "mv_label_base":          snap.get("mv_label_base") or snap.get("mv_label"),
                "mv_label_aliases":       list(snap.get("mv_label_aliases") or []),
                "move_level":             snap.get("move_level"),
                "move_level_total":       snap.get("move_level_total"),
                "move_level_label":       snap.get("move_level_label"),
                "move_level_source":      snap.get("move_level_source"),
                "move_level_confidence":  snap.get("move_level_confidence"),
                "move_level_projectile_id": snap.get("move_level_projectile_id"),
                "profile_history_label":    snap.get("profile_history_label"),
                "profile_history_action_id": snap.get("profile_history_action_id"),
                "profile_history_projectile_id": snap.get("profile_history_projectile_id"),
                "profile_history_static_addr": snap.get("profile_history_static_addr"),
                "profile_history_age":      snap.get("profile_history_age"),
                "profile_history_seen_wall_time": snap.get("profile_history_seen_wall_time"),
                "profile_history_token":    snap.get("profile_history_token"),
                "profile_live_label":       snap.get("profile_live_label"),
                "profile_resolved_label":   snap.get("profile_resolved_label"),
                "profile_live_active":      bool(snap.get("profile_live_active", False)),
                "move_label_source":        snap.get("move_label_source"),
                "profile_label_override":   snap.get("profile_label_override", False),
                "baroque_ready_local":    snap.get("baroque_ready_local", False),
                "baroque_red_pct_max":    snap.get("baroque_red_pct_max", 0.0),
                "baroque_cancel_raw":     snap.get("baroque_cancel_raw", False),
                "baroque_cancel_latched": snap.get("baroque_cancel_latched", False),
                "baroque_cancel_frames":  snap.get("baroque_cancel_latch_frames", 0),
                "input_previous":         input_packet.get("previous", 0),
                "input_held":             input_held,
                "input_pressed":          input_pressed,
                "input_released":         input_released,
                "input_samples":          input_samples,
                "input_text":             input_packet.get("held_text", "5"),
                "input_pressed_text":     input_packet.get("pressed_text", "none"),
                "input_released_text":    input_packet.get("released_text", "none"),
                "active_start":           active_start,
                "active_end":             active_end,
                "attack_property":        attack_property,
                "attack_property_label":  attack_property_label,
                "attack_property_packet_count": snap.get("attack_property_packet_count", 0),
                "attack_property_live_actor": snap.get("attack_property_live_actor"),
                "attack_property_live_a": snap.get("attack_property_live_a"),
                "attack_property_live_b": snap.get("attack_property_live_b"),
                "attack_property_live_a_text": snap.get("attack_property_live_a_text", ""),
                "attack_property_live_b_text": snap.get("attack_property_live_b_text", ""),
                "attack_property_live_b_unknown": snap.get("attack_property_live_b_unknown"),
                "attack_property_live_status20": snap.get("attack_property_live_status20"),
                "attack_property_live_damage": snap.get("attack_property_live_damage"),
                "attack_property_live_phase_a": snap.get("attack_property_live_phase_a"),
                "attack_property_live_phase_b": snap.get("attack_property_live_phase_b"),
                "attack_property_live_victim_slot": snap.get("attack_property_live_victim_slot", ""),
                "attack_property_live_action_frame": snap.get("attack_property_live_action_frame"),
                "recoverable_hp":          snap.get("recoverable_hp", 0),
                "recoverable_ceiling":     snap.get("recoverable_ceiling", snap.get("aux")),
                "recoverable_pct_max":     snap.get("recoverable_pct_max", 0.0),
                "red_health_current":      snap.get("red_health_current", snap.get("cur")),
                "red_health_aux":          snap.get("red_health_aux", snap.get("aux")),
                "red_health_recoverable":  snap.get("red_health_recoverable", snap.get("recoverable_hp", 0)),
                "red_health_pct_max":      snap.get("red_health_pct_max", snap.get("recoverable_pct_max", 0.0)),
                "red_health_pending_current": snap.get("red_health_pending_current", 0),
                "red_health_pending_aux":  snap.get("red_health_pending_aux", 0),
                "red_health_heal_sync":    snap.get("red_health_heal_sync", 0),
                "red_health_point":        snap.get("red_health_point", False),
                "red_health_baroque":      snap.get("red_health_baroque", False),
                "red_health_red_spent":    snap.get("red_health_red_spent", 0),
                "red_health_last_event":   snap.get("red_health_last_event", ""),
                "red_health_last_current_delta": snap.get("red_health_last_current_delta", 0),
                "red_health_last_aux_delta": snap.get("red_health_last_aux_delta", 0),
                "red_health_last_red_delta": snap.get("red_health_last_red_delta", 0),
                "red_health_last_predicted": snap.get("red_health_last_predicted"),
                "red_health_last_match":   snap.get("red_health_last_match"),
                "red_health_last_attacker": snap.get("red_health_last_attacker", ""),
                "red_health_last_move":    snap.get("red_health_last_move", ""),
                "meter_profile_current":   snap.get("meter_profile_current", snap.get("meter")),
                "meter_profile_last_delta": snap.get("meter_profile_last_delta", 0),
                "meter_profile_last_kind": snap.get("meter_profile_last_kind", ""),
                "meter_profile_last_role": snap.get("meter_profile_last_role", ""),
                "meter_profile_last_source": snap.get("meter_profile_last_source", ""),
                "meter_profile_last_move": snap.get("meter_profile_last_move", ""),
                "meter_profile_last_predicted": snap.get("meter_profile_last_predicted"),
                "meter_profile_last_difference": snap.get("meter_profile_last_difference"),
                "meter_profile_last_match": snap.get("meter_profile_last_match"),
                "meter_profile_last_base_damage": snap.get("meter_profile_last_base_damage", 0),
                "meter_profile_last_property_a": snap.get("meter_profile_last_property_a", ""),
                "meter_profile_last_property_b": snap.get("meter_profile_last_property_b", ""),
                "mission_target":         slot_label == mission_active_slot,
                "mission_var_partner":    slot_label == partner_slot,
                "mission_wrong_ready":    bool(
                    mission_var.get("wrong_character_ready", False)
                    and slot_label == partner_slot
                ),
                "mission_var_ready":      bool(
                    mission_var.get("partner_airborne", False)
                    and slot_label == partner_slot
                ),
                "mission_varing":         bool(
                    mission_var.get("partner_varing", False)
                    and slot_label == partner_slot
                ),
                "timing_action_frame":     int(snap.get("timing_action_frame") or 0),
                "timing_blockstun":        int(snap.get("timing_blockstun") or 0),
                "timing_hitstun_total":    int(snap.get("timing_hitstun_total") or 0),
                "timing_hitstun_remaining": int(snap.get("timing_hitstun_remaining") or 0),
                "timing_hitstop_active":   int(snap.get("timing_hitstop_active") or 0),
                "timing_hitstop_pending":  int(snap.get("timing_hitstop_pending") or 0),
                "timing_hitstop":          int(snap.get("timing_hitstop") or 0),
            }

        annotate_damage_scaling_payload(payload, render_snap_by_slot)
        payload["_punish_trainer"] = dict(punish_overlay or {})
        payload["_timing_engine"] = dict(timing_payload or {})

        self._queue_payload(payload)

    def check_proc(self) -> None:
        """Poll the subprocess handle; update active flag if it has exited."""
        if self._proc and self._proc.poll() is not None:
            self._proc = None
            self._active = False
    def close(self) -> None:
        self._input_sampler_stop.set()
        if self._input_sampler_thread.is_alive():
            self._input_sampler_thread.join(timeout=1.0)
        with self._payload_condition:
            self._payload_writer_stop = True
            self._payload_condition.notify_all()
        if self._payload_writer_thread.is_alive():
            self._payload_writer_thread.join(timeout=1.0)

