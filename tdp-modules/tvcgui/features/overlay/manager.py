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
from typing import TYPE_CHECKING

from tvcgui.core.paths import user_data_path
from tvcgui.runtime import input_monitor

if TYPE_CHECKING:
    from tvcgui.features.training.mission_manager import MissionManager

HUD_OVERLAY_DATA_FILE = user_data_path("overlay", "hud_overlay_data.json")


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
            try:
                input_packet = input_monitor.read_overlay_input_packet(
                    slot_label,
                    int(snap.get("base") or 0),
                )
            except Exception:
                input_packet = {}

            if last_scan_normals and cur_anim is not None:
                for slot_data in last_scan_normals:
                    if slot_data.get("slot_label") == slot_label:
                        for mv in slot_data.get("moves", []):
                            if mv.get("id") == cur_anim:
                                active_start = mv.get("active_start")
                                active_end = mv.get("active_end")
                                break
                        break

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
                "input_held":             input_packet.get("held", 0),
                "input_pressed":          input_packet.get("pressed", 0),
                "input_released":         input_packet.get("released", 0),
                "input_text":             input_packet.get("held_text", "5"),
                "input_pressed_text":     input_packet.get("pressed_text", "none"),
                "input_released_text":    input_packet.get("released_text", "none"),
                "active_start":           active_start,
                "active_end":             active_end,
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
            os.makedirs(os.path.dirname(HUD_OVERLAY_DATA_FILE), exist_ok=True)
            with open(HUD_OVERLAY_DATA_FILE, "w") as f:
                json.dump(payload, f)
        except Exception:
            pass

    def check_proc(self) -> None:
        """Poll the subprocess handle; update active flag if it has exited."""
        if self._proc and self._proc.poll() is not None:
            self._proc = None
            self._active = False