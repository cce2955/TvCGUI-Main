"""Stability contracts for Character Select background maintenance."""
from __future__ import annotations

import inspect
import unittest

import tvcgui.features.character_select.runtime as runtime


class CharacterSelectStabilityGuardTests(unittest.TestCase):
    def test_background_service_is_no_faster_than_ten_hz(self) -> None:
        self.assertGreaterEqual(runtime._CHAR_TEST_SERVICE_MIN_INTERVAL_SEC, 0.1)

    def test_speculative_mixed_giant_writer_is_not_in_service_loop(self) -> None:
        source = inspect.getsource(runtime._tick_roster_actions)
        self.assertNotIn("_tick_mixed_giant_partner_unlock()", source)

    def test_yami_icon_only_routes_remain_active(self) -> None:
        source = inspect.getsource(runtime._tick_roster_actions)
        self.assertIn("_tick_yami_hover_icon_id_route()", source)
        self.assertIn("_tick_yami_dol_icon_tag_route()", source)
        self.assertEqual(runtime.YAMI_HOVER_DISPLAY_PROFILE_IDS, {})


if __name__ == "__main__":
    unittest.main()
