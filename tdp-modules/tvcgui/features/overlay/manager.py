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
from tvcgui.features.frame_data.attack_property_runtime import resolve_live_attack_definition
from tvcgui.runtime.realtime_sampler import RealtimeCombatSampler
from tvcgui.features.overlay.damage_scaling import annotate_damage_scaling_payload
from tvcgui.features.overlay.hitstun_scaling import annotate_hitstun_scaling_payload

if TYPE_CHECKING:
    from tvcgui.features.training.mission_manager import MissionManager

HUD_OVERLAY_DATA_FILE = user_data_path("overlay", "hud_overlay_data.json")
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





def _attack_property_move_quality(move: dict | None) -> tuple[int, int, int]:
    """Prefer the populated owner when one action ID has duplicate scan rows."""
    if not isinstance(move, dict):
        return (0, 0, 0)
    segments = [row for row in (move.get("hit_segments") or []) if isinstance(row, dict)]
    populated = sum(
        1 for key in (
            "attack_property", "damage", "active_start", "active_end",
            "hit_reaction", "kb_type", "hitstun", "blockstun",
        )
        if move.get(key) is not None
    )
    source_bonus = 1 if str(move.get("source") or "") == "table" else 0
    return (len(segments), populated, source_bonus)


def _bridge_attack_property_annotations(slot_payload: dict, snap: dict) -> None:
    """Forward profiler annotations into the subprocess payload.

    The telemetry scheduler annotates the main-process fighter snapshot, but
    the overlay subprocess only sees fields serialized here. Static move
    definitions have no runtime actor address, so dropping display/source
    metadata makes valid properties look inactive.
    """
    for key, value in (snap or {}).items():
        if key.startswith("attack_property_") and key not in {
            "attack_property",
            "attack_property_label",
        }:
            slot_payload[key] = value


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

    def __init__(
        self,
        move_map: dict,
        global_map: dict,
        realtime_sampler: RealtimeCombatSampler | None = None,
    ) -> None:
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

        # Realtime Dolphin reads belong to a permanent scheduler outside the
        # overlay. HudOverlayManager only reads cached packets from it.
        self._realtime_sampler = realtime_sampler or RealtimeCombatSampler()
        self._owns_realtime_sampler = realtime_sampler is None

    def _set_input_sampler_targets(self, render_snap_by_slot: dict) -> None:
        self._realtime_sampler.set_targets(render_snap_by_slot)

    def _input_snapshot_for_slot(
        self,
        slot_label: str,
        base: int,
    ) -> tuple[dict, list[dict]]:
        return self._realtime_sampler.snapshot_for_slot(slot_label, base)

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
        self._realtime_sampler.add_listener(listener)

    def remove_input_sample_listener(self, listener) -> None:
        self._realtime_sampler.remove_listener(listener)

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
            attack_property_b = None
            attack_property_label = ""
            matched_move = None
            native_definition = None
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
                        candidates = [
                            mv for mv in slot_data.get("moves", [])
                            if isinstance(mv, dict) and mv.get("id") == cur_anim
                        ]
                        if candidates:
                            matched_move = max(candidates, key=_attack_property_move_quality)
                            active_start = matched_move.get("active_start")
                            active_end = matched_move.get("active_end")
                        break

            # Attack Property is native-only. Resolve the current action from
            # the fighter's character table and never fill this card from the
            # frame-data profile or scanner tree.
            if cur_anim is not None:
                try:
                    tree_root = int((slot_tree_row or {}).get("chr_tbl_abs") or 0)
                except Exception:
                    tree_root = 0
                native_definition = resolve_live_attack_definition(
                    int(snap.get("base") or 0),
                    int(cur_anim),
                    chr_tbl_abs=tree_root or None,
                )
                if native_definition.get("status") == "OK":
                    attack_property = native_definition.get("property_a")
                    attack_property_b = native_definition.get("property_b")
                    attack_property_label = _overlay_attack_property_label(attack_property)

            partner_slot = mission_var.get("partner_slot")

            slot_payload = {
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

            # Preserve all attack-property annotations produced by the
            # background profiler. The earlier serializer copied only raw
            # actor fields, which dropped the move-definition active/source
            # flags and made the HUD show only the detected action.
            _bridge_attack_property_annotations(slot_payload, snap)

            # Native script is the authoritative Attack Property source.
            # Always overwrite any older pool/resolver/profile annotation for
            # the current action so this badge stays a clean native harvest.
            existing_attack_source = str(slot_payload.get("attack_property_display_source") or "")
            existing_attack_phases = [
                row for row in (slot_payload.get("attack_property_phases") or [])
                if isinstance(row, dict)
            ]
            projectile_only_profiler_result = bool(
                slot_payload.get("attack_property_projectiles")
                and not existing_attack_phases
                and existing_attack_source in {
                    "live_attack_actor", "live_attack_actor_latched",
                    "live_projectile_actor", "live_projectile_latched",
                }
            )
            if (
                not projectile_only_profiler_result
                and isinstance(native_definition, dict)
                and native_definition.get("status") == "OK"
                and _known_attack_property(attack_property)
            ):
                native_phases = [
                    dict(phase)
                    for phase in (native_definition.get("phases") or [])
                    if isinstance(phase, dict)
                ]
                projectile_rows = [
                    row for row in (slot_payload.get("attack_property_projectiles") or [])
                    if isinstance(row, dict)
                ]
                has_projectiles = bool(projectile_rows)
                has_live_projectiles = any(bool(row.get("projectile_live")) for row in projectile_rows)
                slot_payload.update({
                    "attack_property_display_active": True,
                    "attack_property_display_source": (
                        "native_script_and_live_attack_actor"
                        if has_live_projectiles
                        else ("native_script_and_last_attack_actor" if has_projectiles else "move_definition")
                    ),
                    "attack_property_packet_state": (
                        "CURRENT MOVE + LIVE ATTACK ACTOR"
                        if has_live_projectiles
                        else ("CURRENT MOVE + LAST ATTACK ACTOR" if has_projectiles else "CURRENT MOVE")
                    ),
                    "attack_property_packet_source": "move_definition",
                    "attack_property_packet_action_id": int(cur_anim or 0),
                    "attack_property_packet_action_name": str(mv_label_display or mv_label or ""),
                    "attack_property_live_actor": 0,
                    "attack_property_live_a": int(attack_property) & 0xFF,
                    "attack_property_live_b": int(attack_property_b or 0) & 0xFFFFFFFF,
                    "attack_property_definition_status": "OK",
                    "attack_property_definition_action_id": int(cur_anim or 0),
                    "attack_property_definition_action_source": "native_action_script",
                    "attack_property_phase_count": len(native_phases),
                    "attack_property_phases": native_phases,
                })

            payload[slot_label] = slot_payload

        annotate_damage_scaling_payload(payload, render_snap_by_slot)
        annotate_hitstun_scaling_payload(payload, render_snap_by_slot)
        payload["_punish_trainer"] = dict(punish_overlay or {})
        payload["_timing_engine"] = dict(timing_payload or {})

        self._queue_payload(payload)

    def check_proc(self) -> None:
        """Poll the subprocess handle; update active flag if it has exited."""
        if self._proc and self._proc.poll() is not None:
            self._proc = None
            self._active = False
    def close(self) -> None:
        if self._owns_realtime_sampler:
            self._realtime_sampler.close()
        with self._payload_condition:
            self._payload_writer_stop = True
            self._payload_condition.notify_all()
        if self._payload_writer_thread.is_alive():
            self._payload_writer_thread.join(timeout=1.0)

