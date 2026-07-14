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


class V19MissionScrollContractTests(unittest.TestCase):
    def _overlay(self) -> mission_hud.MasterOverlay:
        return mission_hud.MasterOverlay()

    def test_scroll_target_keeps_current_step_two_rows_from_top(self):
        self.assertEqual(mission_hud._mission_scroll_target(5, 12, 6), 3)

    def test_scroll_target_clamps_at_start(self):
        self.assertEqual(mission_hud._mission_scroll_target(0, 12, 6), 0)
        self.assertEqual(mission_hud._mission_scroll_target(1, 12, 6), 0)

    def test_scroll_target_clamps_at_end(self):
        self.assertEqual(mission_hud._mission_scroll_target(99, 12, 6), 6)

    def test_short_mission_never_scrolls(self):
        self.assertEqual(mission_hud._mission_scroll_target(5, 6, 6), 0)

    def test_scroll_ease_is_clamped_and_monotonic(self):
        self.assertEqual(mission_hud._mission_scroll_ease(-1.0), 0.0)
        self.assertEqual(mission_hud._mission_scroll_ease(2.0), 1.0)
        self.assertLess(mission_hud._mission_scroll_ease(0.25), mission_hud._mission_scroll_ease(0.75))

    def test_initial_mission_snaps_to_correct_view(self):
        overlay = self._overlay()
        overlay._update_mission_scroll_state(0.0, 5, 12, "ryu-1")
        self.assertEqual(overlay._mission_scroll_pos, 3.0)
        self.assertEqual(overlay._mission_scroll_target, 3.0)

    def test_advancing_step_starts_smooth_scroll(self):
        overlay = self._overlay()
        overlay._update_mission_scroll_state(0.0, 5, 12, "ryu-1")
        overlay._update_mission_scroll_state(0.0, 6, 12, "ryu-1")
        self.assertEqual(overlay._mission_scroll_from, 3.0)
        self.assertEqual(overlay._mission_scroll_target, 4.0)
        self.assertEqual(overlay._mission_scroll_phase, 0.0)

    def test_half_duration_places_scroll_between_rows(self):
        overlay = self._overlay()
        overlay._update_mission_scroll_state(0.0, 5, 12, "ryu-1")
        overlay._update_mission_scroll_state(0.0, 6, 12, "ryu-1")
        overlay._update_mission_scroll_state(0.17, 6, 12, "ryu-1")
        self.assertGreater(overlay._mission_scroll_pos, 3.0)
        self.assertLess(overlay._mission_scroll_pos, 4.0)

    def test_full_duration_locks_exactly_to_target(self):
        overlay = self._overlay()
        overlay._update_mission_scroll_state(0.0, 5, 12, "ryu-1")
        overlay._update_mission_scroll_state(0.0, 6, 12, "ryu-1")
        overlay._update_mission_scroll_state(0.34, 6, 12, "ryu-1")
        self.assertEqual(overlay._mission_scroll_pos, 4.0)
        self.assertEqual(overlay._mission_scroll_phase, 1.0)

    def test_scroll_does_not_overshoot_target(self):
        overlay = self._overlay()
        overlay._update_mission_scroll_state(0.0, 5, 12, "ryu-1")
        overlay._update_mission_scroll_state(0.0, 6, 12, "ryu-1")
        for _ in range(20):
            overlay._update_mission_scroll_state(0.05, 6, 12, "ryu-1")
        self.assertEqual(overlay._mission_scroll_pos, 4.0)

    def test_new_mission_resets_without_dragging_old_scroll(self):
        overlay = self._overlay()
        overlay._update_mission_scroll_state(0.0, 8, 12, "ryu-1")
        overlay._update_mission_scroll_state(0.0, 3, 12, "ryu-2")
        self.assertEqual(overlay._mission_scroll_pos, 1.0)
        self.assertEqual(overlay._mission_scroll_phase, 1.0)

    def test_show_all_animates_viewport_back_to_top(self):
        overlay = self._overlay()
        overlay._update_mission_scroll_state(0.0, 8, 12, "ryu-1")
        overlay.mission_show_all = True
        overlay._update_mission_scroll_state(0.0, 8, 12, "ryu-1")
        self.assertEqual(overlay._mission_scroll_target, 0.0)
        self.assertEqual(overlay._mission_scroll_phase, 0.0)

    def test_update_loop_drives_scroll_state(self):
        source = function_source(MASTER, "update_mission_animations")
        self.assertIn("self._update_mission_scroll_state(dt, current_idx, len(steps), mission_id)", source)

    def test_draw_uses_fractional_scroll_position(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("(idx - scroll_pos) * (row_h + row_gap)", source)

    def test_draw_includes_one_extra_row_for_transition(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("visible_start + visible_limit + 1", source)

    def test_scroll_rows_are_clipped_to_six_row_viewport(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("panel.set_clip(pygame.Rect", source)
        self.assertIn("visible_rows = visible_limit", source)
        self.assertIn("panel.set_clip(old_clip)", source)

    def test_panel_height_stays_stable_during_fractional_scroll(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("visible_rows * row_h + max(0, visible_rows - 1) * row_gap", source)
        self.assertNotIn("+ len(visible_steps) * (row_h + row_gap)", source)

    def test_footer_tracks_nearest_visible_slice(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("footer_start = min(max_start, max(0, int(round(scroll_pos))))", source)
        self.assertIn('f"Showing {footer_start + 1}-{footer_end} of {total_steps}"', source)

    def test_old_instant_slice_jump_is_removed(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertNotIn("visible_start = min(max(0, current_step_index - 2), max_start)", source)


if __name__ == "__main__":
    unittest.main()
