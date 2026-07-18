"""Solo Character remains independent from mixed giant teams."""
from __future__ import annotations

import unittest

import tvcgui.features.character_select.runtime as runtime


class SoloGiantIndependenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = {
            "_update_extra_guard_state": runtime._update_extra_guard_state,
            "_solo_tail_empty_rows_present": runtime._solo_tail_empty_rows_present,
            "_solo_extra_empty_rows_present": runtime._solo_extra_empty_rows_present,
            "_install_solo_team_rows_only": runtime._install_solo_team_rows_only,
            "state": dict(runtime._ROSTER_STATE),
        }
        runtime._ROSTER_STATE["solo_team_requested"] = True
        runtime._ROSTER_STATE["extra_characters_requested"] = True
        runtime._update_extra_guard_state = lambda: {
            "active": True,
            "visual_rows_present": False,
            "patch_present": True,
            "solo_extra_rows_present": True,
        }
        runtime._solo_tail_empty_rows_present = lambda: False
        runtime._solo_extra_empty_rows_present = lambda: True

    def tearDown(self) -> None:
        runtime._update_extra_guard_state = self.saved["_update_extra_guard_state"]
        runtime._solo_tail_empty_rows_present = self.saved["_solo_tail_empty_rows_present"]
        runtime._solo_extra_empty_rows_present = self.saved["_solo_extra_empty_rows_present"]
        runtime._install_solo_team_rows_only = self.saved["_install_solo_team_rows_only"]
        runtime._ROSTER_STATE.clear()
        runtime._ROSTER_STATE.update(self.saved["state"])

    def test_solo_no_longer_has_a_giant_hover_handoff(self) -> None:
        self.assertFalse(hasattr(runtime, "_solo_hovered_giant_id"))

    def test_solo_always_installs_its_blank_partner_helper(self) -> None:
        calls: list[bool] = []

        def install(*, extra_requested: bool = False) -> tuple[int, int]:
            calls.append(bool(extra_requested))
            runtime._update_extra_guard_state = lambda: {
                "active": True,
                "visual_rows_present": True,
                "patch_present": True,
                "solo_extra_rows_present": True,
            }
            return 2, 0

        runtime._install_solo_team_rows_only = install
        runtime._tick_solo_team_request()

        self.assertEqual(calls, [True])
        self.assertTrue(runtime._ROSTER_STATE["solo_team_enabled"])
        self.assertFalse(runtime._ROSTER_STATE["solo_giant_native_active"])
        self.assertEqual(runtime._ROSTER_STATE["solo_giant_native_id"], "")


if __name__ == "__main__":
    unittest.main()
