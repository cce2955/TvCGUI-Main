from __future__ import annotations

import os
import sys
import types
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.modules.setdefault("win32con", types.SimpleNamespace())
sys.modules.setdefault("win32gui", types.SimpleNamespace())

from tests.v19_contract_helpers import function_source
from tvcgui.features.overlay import master_renderer as mission_hud

MASTER = "tvcgui/features/overlay/master_renderer.py"


class V19MissionTransitionContractTests(unittest.TestCase):
    @staticmethod
    def _payload(
        character: str,
        mission_id: str,
        mission_name: str,
        slot: str = "P1-C1",
    ) -> dict:
        return {
            "character": character,
            "slot": slot,
            "active_mission_id": mission_id,
            "active_mission_name": mission_name,
            "active_mission_notes": f"{character} transition test hint.",
            "active_mission_steps": [
                {"display": "5A", "input": "A", "color": "blue"},
                {"display": "5B", "input": "B", "color": "yellow"},
                {"display": "5C", "input": "C", "color": "green"},
            ],
            "completed_step_count": 0,
            "current_step_index": 0,
            "selector_open": False,
        }

    @staticmethod
    def _overlay() -> mission_hud.MasterOverlay:
        overlay = mission_hud.MasterOverlay()
        overlay.mission_active = True
        overlay.mission_slot = "P1-C1"
        return overlay

    @staticmethod
    def _advance_frames(overlay: mission_hud.MasterOverlay, frames: int) -> None:
        for _ in range(max(0, int(frames))):
            overlay._update_mission_transition(1.0 / mission_hud.TARGET_FPS)

    def test_transition_budget_is_exactly_120_frames(self):
        overlay = self._overlay()
        out_frames = round(overlay._mission_transition_out_duration * mission_hud.TARGET_FPS)
        hold_frames = round(overlay._mission_transition_hold_duration * mission_hud.TARGET_FPS)
        in_frames = round(overlay._mission_transition_in_duration * mission_hud.TARGET_FPS)
        self.assertEqual((out_frames, hold_frames, in_frames), (48, 12, 60))
        self.assertEqual(out_frames + hold_frames + in_frames, 120)

    def test_first_payload_becomes_visible_without_transition(self):
        overlay = self._overlay()
        ryu = self._payload("Ryu", "ryu_001", "Basic")
        overlay._stage_mission_overlay_payload(ryu)
        self.assertEqual(overlay._mission_transition_state, "idle")
        self.assertEqual(overlay._mission_visible_data, ryu)
        self.assertEqual(overlay._mission_display_payload(), ryu)

    def test_character_change_stages_old_content_out_and_new_content_pending(self):
        overlay = self._overlay()
        ryu = self._payload("Ryu", "ryu_001", "Basic")
        chun = self._payload("Chun-Li", "chun_li_001", "Basic")
        overlay._stage_mission_overlay_payload(ryu)
        overlay._stage_mission_overlay_payload(chun)
        self.assertEqual(overlay._mission_transition_state, "out")
        self.assertEqual(overlay._mission_transition_phase, 0.0)
        self.assertEqual(overlay._mission_transition_old_data, ryu)
        self.assertEqual(overlay._mission_transition_new_data, chun)
        self.assertEqual(overlay._mission_display_payload(), ryu)

    def test_any_mission_identity_change_starts_a_handoff(self):
        overlay = self._overlay()
        first = self._payload("Ryu", "ryu_001", "Basic")
        renamed = self._payload("Ryu", "ryu_001", "Revised Basic")
        overlay._stage_mission_overlay_payload(first)
        overlay._stage_mission_overlay_payload(renamed)
        self.assertEqual(overlay._mission_transition_state, "out")

    def test_outgoing_content_reaches_empty_shell_after_48_frames(self):
        overlay = self._overlay()
        ryu = self._payload("Ryu", "ryu_001", "Basic")
        chun = self._payload("Chun-Li", "chun_li_001", "Basic")
        overlay._stage_mission_overlay_payload(ryu)
        overlay._stage_mission_overlay_payload(chun)
        self._advance_frames(overlay, 48)
        self.assertEqual(overlay._mission_transition_state, "hold")
        self.assertEqual(overlay._mission_transition_phase, 0.0)
        self.assertEqual(overlay._mission_display_payload(), ryu)

    def test_empty_shell_holds_for_12_frames_before_swapping_payload(self):
        overlay = self._overlay()
        ryu = self._payload("Ryu", "ryu_001", "Basic")
        chun = self._payload("Chun-Li", "chun_li_001", "Basic")
        overlay._stage_mission_overlay_payload(ryu)
        overlay._stage_mission_overlay_payload(chun)
        self._advance_frames(overlay, 48)
        self._advance_frames(overlay, 11)
        self.assertEqual(overlay._mission_transition_state, "hold")
        self.assertEqual(overlay._mission_display_payload(), ryu)
        self._advance_frames(overlay, 1)
        self.assertEqual(overlay._mission_transition_state, "in")
        self.assertEqual(overlay._mission_transition_phase, 0.0)
        self.assertEqual(overlay._mission_display_payload(), chun)

    def test_incoming_content_finishes_after_60_frames(self):
        overlay = self._overlay()
        ryu = self._payload("Ryu", "ryu_001", "Basic")
        chun = self._payload("Chun-Li", "chun_li_001", "Basic")
        overlay._stage_mission_overlay_payload(ryu)
        overlay._stage_mission_overlay_payload(chun)
        self._advance_frames(overlay, 48 + 12 + 60)
        self.assertEqual(overlay._mission_transition_state, "idle")
        self.assertEqual(overlay._mission_transition_phase, 1.0)
        self.assertEqual(overlay._mission_display_payload(), chun)

    def test_rapid_changes_replace_pending_destination_instead_of_stacking(self):
        overlay = self._overlay()
        ryu = self._payload("Ryu", "ryu_001", "Basic")
        chun = self._payload("Chun-Li", "chun_li_001", "Basic")
        zero = self._payload("Zero", "zero_001", "Basic")
        overlay._stage_mission_overlay_payload(ryu)
        overlay._stage_mission_overlay_payload(chun)
        self._advance_frames(overlay, 10)
        overlay._stage_mission_overlay_payload(zero)
        self.assertEqual(overlay._mission_transition_state, "out")
        self.assertEqual(overlay._mission_transition_new_data, zero)
        self._advance_frames(overlay, 38 + 12 + 60)
        self.assertEqual(overlay._mission_transition_state, "idle")
        self.assertEqual(overlay._mission_display_payload(), zero)

    def test_entry_elements_cascade_top_to_bottom(self):
        first = mission_hud._mission_sequence_progress(0.20, 0)
        second = mission_hud._mission_sequence_progress(0.20, 1)
        third = mission_hud._mission_sequence_progress(0.20, 2)
        self.assertGreater(first, second)
        self.assertGreater(second, third)

    def test_exit_elements_cascade_top_to_bottom(self):
        first = mission_hud._mission_element_exit_progress(0.20, 0)
        second = mission_hud._mission_element_exit_progress(0.20, 1)
        third = mission_hud._mission_element_exit_progress(0.20, 2)
        self.assertGreater(first, second)
        self.assertGreater(second, third)

    def test_panel_shell_is_not_faded_by_transition_state(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("The panel shell never fades during a mission-to-mission handoff", source)
        self.assertIn("panel.set_alpha(int(255 * panel_intro))", source)
        self.assertNotIn("panel.set_alpha(int(255 * panel_intro *", source)

    def test_major_panel_elements_use_one_sequential_order(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        for token in (
            "character_chip_vis",
            "progress_chip_vis",
            "meta_vis",
            "strip_vis",
            "mission_chip_vis",
            "button_order",
            "instruction_order",
            "note_order",
            "row_sequence_base",
            "row_order",
            "footer_order",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
