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

if TYPE_CHECKING:
    from tvcgui.features.training.mission_manager import MissionManager

HUD_OVERLAY_DATA_FILE = user_data_path("overlay", "hud_overlay_data.json")
INPUT_SAMPLER_HZ = 240.0
INPUT_SAMPLE_QUEUE_LIMIT = 128

ATTACK_PROPERTY_SHORT_LABELS = {
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


def _overlay_attack_property_label(value) -> str:
    try:
        return ATTACK_PROPERTY_SHORT_LABELS.get(int(value) & 0xFF, "")
    except Exception:
        return ""


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
        if parsed in ATTACK_PROPERTY_SHORT_LABELS:
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

        # The GUI and transparent overlay run independently, and the GUI can be
        # busy for more than one game frame. Poll the live input packet on a
        # dedicated high-frequency thread so short taps and neutral separators
        # survive even when the normal render loop is late.
        self._input_sample_seq: int = 0
        self._input_samples_by_slot: dict[str, list[dict]] = {}
        self._input_sampler_targets: dict[str, int] = {}
        self._input_latest_by_slot: dict[str, dict] = {}
        self._input_raw_state_by_slot: dict[str, tuple[int, int, int]] = {}
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

        with self._input_lock:
            previous = self._input_raw_state_by_slot.get(slot_label)
            if previous is None:
                previous_held = held
                previous_pressed = 0
                previous_released = 0
            else:
                previous_held, previous_pressed, previous_released = previous

            fresh_pressed = raw_pressed & ~int(previous_pressed)
            fresh_released = raw_released & ~int(previous_released)
            held_changed = previous is None or held != int(previous_held)

            self._input_raw_state_by_slot[slot_label] = (
                held,
                raw_pressed,
                raw_released,
            )
            self._input_latest_by_slot[slot_label] = {
                **dict(packet or {}),
                "held": held,
                "pressed": fresh_pressed,
                "released": fresh_released,
            }

            # Exact repeats from the same game-frame edge are ignored. A real
            # repeated tap includes a release/neutral transition, which changes
            # held or clears the raw edge before it appears again.
            if not held_changed and not fresh_pressed and not fresh_released:
                return

            self._input_sample_seq += 1
            queue = self._input_samples_by_slot.setdefault(slot_label, [])
            queue.append({
                "seq": self._input_sample_seq,
                "held": held,
                "pressed": fresh_pressed,
                "released": fresh_released,
                "sample_ns": time.monotonic_ns(),
            })
            del queue[:-INPUT_SAMPLE_QUEUE_LIMIT]

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

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active

    def write_data(
        self,
        render_snap_by_slot: dict,
        last_scan_normals,
        mission_mgr: "MissionManager",
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
            cur_anim = snap.get("attA") or snap.get("attB")
            mv_label = snap.get("mv_label")
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
                "mv_label":               mv_label,
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
            }

        try:
            serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            if serialized == self._last_serialized and os.path.isfile(HUD_OVERLAY_DATA_FILE):
                return
            os.makedirs(os.path.dirname(HUD_OVERLAY_DATA_FILE), exist_ok=True)
            tmp = f"{HUD_OVERLAY_DATA_FILE}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(serialized)
            os.replace(tmp, HUD_OVERLAY_DATA_FILE)
            self._last_serialized = serialized
        except Exception:
            pass

    def check_proc(self) -> None:
        """Poll the subprocess handle; update active flag if it has exited."""
        if self._proc and self._proc.poll() is not None:
            self._proc = None
            self._active = False