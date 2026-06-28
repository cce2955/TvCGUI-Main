"""Contracts for the current Extra Characters roster-only installer.

The old native visual-table append route was intentionally retired.  Character
Select presentation now comes from the DOL tag route and the live focus cache,
so this module verifies the button's current responsibility: install the
logical roster/count rows and leave visual routing to its dedicated services.
"""
from __future__ import annotations

import unittest

import char_test_runtime as ctr


class ExtraCharacterVisualProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.saved = {
            "table": ctr._install_extra_clone10_table,
            "count": ctr._install_extra_clone10_count,
            "state": dict(ctr._ROSTER_STATE),
        }
        self.calls: list[str] = []

    def tearDown(self) -> None:
        ctr._install_extra_clone10_table = self.saved["table"]
        ctr._install_extra_clone10_count = self.saved["count"]
        ctr._ROSTER_STATE.clear()
        ctr._ROSTER_STATE.update(self.saved["state"])

    def _installers(self, table_result: tuple[int, int], count_result: tuple[int, int]) -> None:
        def table() -> tuple[int, int]:
            self.calls.append("table")
            return table_result

        def count() -> tuple[int, int]:
            self.calls.append("count")
            return count_result

        ctr._install_extra_clone10_table = table
        ctr._install_extra_clone10_count = count

    def test_extra_button_runs_roster_table_before_count(self) -> None:
        self._installers((1, 0), (1, 0))
        self.assertEqual(ctr._install_extra_characters_on(), (2, 0))
        self.assertEqual(self.calls, ["table", "count"])

    def test_extra_button_aggregates_roster_write_counts(self) -> None:
        self._installers((3, 0), (2, 0))
        self.assertEqual(ctr._install_extra_characters_on(), (5, 0))

    def test_success_marks_extra_mode_enabled_and_requested(self) -> None:
        self._installers((0, 0), (0, 0))
        self.assertEqual(ctr._install_extra_characters_on(), (0, 0))
        self.assertTrue(ctr._ROSTER_STATE["extra_characters_enabled"])
        self.assertTrue(ctr._ROSTER_STATE["extra_characters_requested"])
        self.assertIn("3 Yami inserts", ctr._ROSTER_STATE["extra_characters_mode"])

    def test_failure_disables_extra_mode(self) -> None:
        self._installers((1, 0), (0, 1))
        self.assertEqual(ctr._install_extra_characters_on(), (1, 1))
        self.assertFalse(ctr._ROSTER_STATE["extra_characters_enabled"])
        self.assertFalse(ctr._ROSTER_STATE["extra_characters_requested"])
        self.assertIn("failed writes=1", ctr._ROSTER_STATE["last_error"])

    def test_button_resets_retired_visual_table_status(self) -> None:
        ctr._ROSTER_STATE["visual_table_patch_installed"] = True
        ctr._ROSTER_STATE["visual_table_patch_mode"] = "legacy"
        self._installers((0, 0), (0, 0))

        ctr._install_extra_characters_on()

        self.assertFalse(ctr._ROSTER_STATE["visual_table_patch_installed"])
        self.assertEqual(
            ctr._ROSTER_STATE["visual_table_patch_mode"],
            "not used by inserted roster-table mode",
        )

    def test_last_action_reports_the_combined_install_result(self) -> None:
        self._installers((2, 0), (3, 0))

        ctr._install_extra_characters_on()

        self.assertIn("wrote=5 failed=0", ctr._ROSTER_STATE["last_action"])
        self.assertIn("count=0x", ctr._ROSTER_STATE["last_action"])

    def test_button_resets_retired_thumbnail_status(self) -> None:
        ctr._ROSTER_STATE["thumbnail_alias_installed"] = True
        ctr._ROSTER_STATE["thumbnail_material_copy_installed"] = True
        self._installers((0, 0), (0, 0))

        ctr._install_extra_characters_on()

        self.assertFalse(ctr._ROSTER_STATE["thumbnail_alias_installed"])
        self.assertFalse(ctr._ROSTER_STATE["thumbnail_material_copy_installed"])
        self.assertEqual(
            ctr._ROSTER_STATE["thumbnail_alias_mode"],
            "not used by inserted roster-table mode",
        )


if __name__ == "__main__":
    unittest.main()
