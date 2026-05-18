from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest import mock

import mission_mode


class MissionModeTests(unittest.TestCase):
    def test_loads_steps_goal_and_setup_debug_flags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missions_dir = os.path.join(temp_dir, "missions")
            os.makedirs(missions_dir, exist_ok=True)
            progress_path = os.path.join(temp_dir, "mission_progress.json")
            with open(os.path.join(missions_dir, "test_char.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "character": "Test Char",
                        "missions": [
                            {
                                "mission_id": "test_001",
                                "name": "Basic Chain",
                                "notes": "test notes",
                                "setup_debug_flags": {"P1Meter": 1, "BaroquePct": "2"},
                                "steps": [
                                    {"label": "5A"},
                                    {"labels": ["5B", "2B"], "grace": 3, "pass": True},
                                ],
                            },
                            {
                                "mission_id": "test_002",
                                "name": "Damage Goal",
                                "goal": {"type": "combo_damage", "damage": 10000},
                            },
                        ],
                    },
                    f,
                )

            with mock.patch.object(mission_mode, "MISSIONS_DIR", missions_dir), mock.patch.object(
                mission_mode, "MISSION_PROGRESS_FILE", progress_path
            ):
                payload = mission_mode.build_overlay_payload("Test Char")

        self.assertEqual("Test Char", payload["character"])
        self.assertEqual(2, payload["mission_count"])
        self.assertEqual("test_001", payload["active_mission_id"])
        self.assertEqual({"P1Meter": 1, "BaroquePct": 2}, payload["active_mission_setup_debug_flags"])
        self.assertEqual(["5A"], payload["active_mission_steps"][0]["labels"])
        self.assertEqual("blue", payload["active_mission_steps"][0]["color"])
        self.assertEqual(["5B", "2B"], payload["active_mission_steps"][1]["labels"])
        self.assertEqual(3, payload["active_mission_steps"][1]["grace"])
        self.assertTrue(payload["active_mission_steps"][1]["pass"])

    def test_selected_mission_overrides_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            missions_dir = os.path.join(temp_dir, "missions")
            os.makedirs(missions_dir, exist_ok=True)
            progress_path = os.path.join(temp_dir, "mission_progress.json")
            with open(os.path.join(missions_dir, "ryu.json"), "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "character": "Ryu",
                        "missions": [
                            {"mission_id": "ryu_001", "name": "One", "steps": [{"label": "5A"}]},
                            {"mission_id": "ryu_002", "name": "Two", "steps": [{"label": "5B"}]},
                        ],
                    },
                    f,
                )

            progress = mission_mode.set_selected_mission_id({}, "Ryu", "ryu_002")
            with open(progress_path, "w", encoding="utf-8") as f:
                json.dump(progress, f)

            with mock.patch.object(mission_mode, "MISSIONS_DIR", missions_dir), mock.patch.object(
                mission_mode, "MISSION_PROGRESS_FILE", progress_path
            ):
                payload = mission_mode.build_overlay_payload("Ryu")

        self.assertEqual("ryu_002", payload["active_mission_id"])
        self.assertEqual("ryu_002", payload["selected_mission_id"])
        selected = [m for m in payload["missions"] if m["selected"]]
        self.assertEqual(["ryu_002"], [m["mission_id"] for m in selected])

    def test_legacy_progress_shape_is_preserved(self) -> None:
        progress = {"ryu": {"ryu_001": True, "selected_mission_id": "ryu_002"}}
        self.assertTrue(mission_mode.is_mission_complete(progress, "Ryu", "ryu_001"))
        updated = mission_mode.mark_mission_complete(progress, "Ryu", "ryu_003")
        self.assertTrue(updated["ryu"]["completed"]["ryu_001"])
        self.assertTrue(updated["ryu"]["completed"]["ryu_003"])
        self.assertEqual("ryu_002", updated["ryu"]["selected_mission_id"])


if __name__ == "__main__":
    unittest.main()
