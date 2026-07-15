from __future__ import annotations

import unittest

from tests.v19_contract_helpers import function_source, read
from tvcgui.features.training import mission_manager

MANAGER = "tvcgui/features/training/mission_manager.py"
MANAGER_DUPLICATE = "tdp-modules/tvcgui/features/training/mission_manager.py"
MASTER = "tvcgui/features/overlay/master_renderer.py"


class V19MissionCompactSelectorContractTests(unittest.TestCase):
    def test_exact_down_from_neutral_is_accepted(self):
        self.assertTrue(mission_manager._mission_selector_down_rising(0x08, 0x00, 0x00))

    def test_down_forward_from_neutral_is_accepted(self):
        self.assertTrue(mission_manager._mission_selector_down_rising(0x09, 0x00, 0x00))

    def test_down_back_from_neutral_is_accepted(self):
        self.assertTrue(mission_manager._mission_selector_down_rising(0x0A, 0x00, 0x00))

    def test_side_and_up_inputs_do_not_count_as_down(self):
        for direction in (0x01, 0x02, 0x04, 0x05, 0x06):
            self.assertFalse(mission_manager._mission_selector_down_rising(direction, 0x00, 0x00))

    def test_held_down_does_not_repeat_without_a_new_press(self):
        self.assertFalse(mission_manager._mission_selector_down_rising(0x08, 0x08, 0x00))

    def test_pressed_down_retriggers_even_when_direction_packet_stays_down(self):
        self.assertTrue(mission_manager._mission_selector_down_rising(0x08, 0x08, 0x08))

    def test_selector_repeat_window_is_more_forgiving(self):
        self.assertGreaterEqual(mission_manager.MISSION_SELECTOR_REPEAT_WINDOW, 1.25)
        self.assertLessEqual(mission_manager.MISSION_SELECTOR_REPEAT_WINDOW, 1.50)

    def test_selector_path_uses_forgiving_down_helper(self):
        source = function_source(MANAGER, "_update_selector_from_inputs")
        self.assertIn("_mission_selector_down_rising(direction, previous_direction, pressed)", source)
        self.assertIn("MISSION_SELECTOR_REPEAT_WINDOW", source)
        self.assertNotIn("direction == 0x08", source)

    def test_compact_panel_has_smaller_width_cap(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("panel_w = max(520, min(int(self.w * 0.43), 760))", source)
        self.assertNotIn("min(int(self.w * 0.48), 820)", source)

    def test_compact_panel_reduces_vertical_spacing(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("pad = 9", source)
        self.assertIn("row_gap = 3", source)
        self.assertIn("challenge_h = 48", source)
        self.assertIn("row_h = max(27", source)

    def test_primary_and_nested_mission_managers_match(self):
        self.assertEqual(read(MANAGER), read(MANAGER_DUPLICATE))


if __name__ == "__main__":
    unittest.main()
