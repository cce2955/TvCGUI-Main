from __future__ import annotations

import json
import importlib
import os
import sys
import types
import unittest


APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MISSIONS_DIR = os.path.join(APP_DIR, "missions")


def _load_mission_manager_with_dolphin_stub():
    old = sys.modules.get("dolphin_io")
    fake = types.ModuleType("dolphin_io")
    fake.rd8 = lambda addr: 0
    fake.wd8 = lambda addr, value: True
    sys.modules["dolphin_io"] = fake
    sys.modules.pop("mission_manager", None)
    try:
        return importlib.import_module("mission_manager")
    finally:
        sys.modules.pop("mission_manager", None)
        if old is not None:
            sys.modules["dolphin_io"] = old
        else:
            sys.modules.pop("dolphin_io", None)


class MissionDataTests(unittest.TestCase):
    def _mission_files(self) -> list[str]:
        return sorted(
            os.path.join(MISSIONS_DIR, name)
            for name in os.listdir(MISSIONS_DIR)
            if name.lower().endswith(".json")
        )

    def _load_all_missions(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for path in self._mission_files():
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            character = str(payload.get("character") or os.path.basename(path)).strip()
            missions = payload.get("missions")
            self.assertIsInstance(missions, list, path)
            out[character] = missions
        return out

    def test_every_mission_has_unique_id_and_playable_goal(self) -> None:
        all_ids: set[str] = set()
        for character, missions in self._load_all_missions().items():
            char_ids: set[str] = set()
            for mission in missions:
                with self.subTest(character=character, mission=mission.get("mission_id")):
                    mission_id = str(mission.get("mission_id", "")).strip()
                    self.assertTrue(mission_id)
                    self.assertNotIn(mission_id, char_ids)
                    self.assertNotIn(mission_id, all_ids)
                    char_ids.add(mission_id)
                    all_ids.add(mission_id)

                    steps = mission.get("steps") or []
                    goal = mission.get("goal") or {}
                    self.assertTrue(steps or goal)

                    if steps:
                        self.assertIsInstance(steps, list)
                        for idx, step in enumerate(steps):
                            self.assertIsInstance(step, dict)
                            labels = step.get("labels")
                            label = step.get("label")
                            has_label = bool(str(label or "").strip())
                            has_labels = isinstance(labels, list) and any(
                                str(item or "").strip() for item in labels
                            )
                            self.assertTrue(has_label or has_labels, f"step {idx}")

                            if "grace" in step:
                                self.assertIsInstance(step["grace"], int)
                                self.assertGreaterEqual(step["grace"], 0)

                            if "pass" in step:
                                self.assertIsInstance(step["pass"], bool)

    def test_meter_refill_missions_exist_in_data(self) -> None:
        mission_manager = _load_mission_manager_with_dolphin_stub()

        mission_ids = {
            str(mission.get("mission_id", "")).strip()
            for missions in self._load_all_missions().values()
            for mission in missions
        }
        missing = sorted(mission_manager.MISSION_METER_REFILL_MISSIONS - mission_ids)
        self.assertEqual([], missing)

    def test_alex_flash_chop_loop_stays_meter_refill_enabled(self) -> None:
        mission_manager = _load_mission_manager_with_dolphin_stub()

        self.assertIn("alex_017", mission_manager.MISSION_METER_REFILL_MISSIONS)


if __name__ == "__main__":
    unittest.main()
