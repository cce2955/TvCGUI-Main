from __future__ import annotations

import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

if "dolphin_memory_engine" not in sys.modules:
    sys.modules["dolphin_memory_engine"] = types.SimpleNamespace(
        is_hooked=lambda: False,
        hook=lambda: None,
        read_bytes=lambda _addr, size: b"\0" * int(size),
        write_bytes=lambda _addr, _data: None,
    )

from tests.v19_contract_helpers import APP_DIR, read
from tvcgui.runtime import megacrash


class V19MissionMegacrashContractTests(unittest.TestCase):
    @staticmethod
    def _joe_trial_four() -> dict:
        path = APP_DIR / "missions" / "Joe_the_condor.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        for mission in data.get("missions", []):
            if mission.get("mission_id") == "joe_the_condor_004":
                return mission
        raise AssertionError("Joe trial 4 missing")

    @classmethod
    def _payload(cls, setup: dict | None = None, mission_id: str = "joe_the_condor_004") -> dict:
        if setup is None:
            setup = dict(cls._joe_trial_four().get("setup_megacrash_trainer") or {})
        return {
            "active": True,
            "slot": "P1-C1",
            "character": "Joe the Condor",
            "active_mission_id": mission_id,
            "active_mission_name": "Combo Trial 004",
            "active_mission_steps": [
                {"labels": ["5A"], "display": "5A"},
                {"labels": ["5B"], "display": "5B"},
                {"labels": ["5C"], "display": "5C"},
                {"labels": ["Cactus Bunker A"], "display": "Cactus Bunker A"},
            ],
            "completed_step_count": 0,
            "active_mission_setup_megacrash_trainer": setup,
        }

    @staticmethod
    def _operator_state() -> dict:
        return {
            "enabled": False,
            "mode": "combined",
            "chance": 25,
            "delay_frames": 12,
            "cooldown_sec": 7.0,
            "target_label": "2A",
            "attacker_scope": "any",
            "target_occurrence": 2,
        }

    def test_condor_trial_four_has_forced_megacrash_setup(self):
        setup = self._joe_trial_four().get("setup_megacrash_trainer") or {}
        self.assertTrue(setup.get("enabled"))
        self.assertEqual(setup.get("target_label"), "5C")
        self.assertEqual(setup.get("delay_frames"), 5)
        self.assertEqual(setup.get("cooldown_sec"), 2.0)

    def test_mission_setup_extractor_reads_condor_flag(self):
        setup = megacrash._extract_mission_megacrash_setup(self._payload())
        self.assertTrue(setup["enabled"])
        self.assertEqual(setup["target_label"], "5C")
        self.assertEqual(setup["chance"], 100)
        self.assertEqual(setup["delay_frames"], 5)
        self.assertEqual(setup["target_occurrence"], 1)

    def test_sync_arms_targeted_mission_override(self):
        state = megacrash._sync_mission_megacrash_trainer(
            self._operator_state(), self._payload()
        )
        self.assertTrue(state["mission_override_active"])
        self.assertTrue(state["enabled"])
        self.assertEqual(state["target_label"], "5C")
        self.assertEqual(state["chance"], 100)
        self.assertEqual(state["delay_frames"], 5)
        self.assertEqual(state["cooldown_sec"], 2.0)
        self.assertEqual(state["target_occurrence"], 1)

    def test_sync_preserves_operator_settings_for_restore(self):
        original = self._operator_state()
        state = megacrash._sync_mission_megacrash_trainer(dict(original), self._payload())
        saved = state.get("mission_saved_settings") or {}
        for key, value in original.items():
            self.assertEqual(saved.get(key), value)

    def test_leaving_mission_restores_operator_settings(self):
        original = self._operator_state()
        state = megacrash._sync_mission_megacrash_trainer(dict(original), self._payload())
        restored = megacrash._sync_mission_megacrash_trainer(state, {"active": False})
        for key, value in original.items():
            self.assertEqual(restored.get(key), value)
        self.assertFalse(restored.get("mission_override_active"))
        self.assertNotIn("mission_override_key", restored)

    def test_switching_missions_keeps_original_operator_settings(self):
        original = self._operator_state()
        first = megacrash._sync_mission_megacrash_trainer(dict(original), self._payload())
        second_setup = {
            "enabled": True,
            "target_label": "5B",
            "delay_frames": 1,
            "cooldown_sec": 1.0,
        }
        switched = megacrash._sync_mission_megacrash_trainer(
            first,
            self._payload(second_setup, mission_id="casshan_008"),
        )
        restored = megacrash._sync_mission_megacrash_trainer(switched, {"active": False})
        for key, value in original.items():
            self.assertEqual(restored.get(key), value)

    def test_light_trigger_finds_target_step_index(self):
        payload = self._payload()
        setup = megacrash._extract_mission_megacrash_setup(payload)
        self.assertEqual(
            megacrash._mission_megacrash_target_step_index(payload, setup),
            2,
        )

    def test_light_trigger_targets_opposing_point_slot(self):
        snaps = {
            "P1-C1": {"slotname": "P1-C1", "teamtag": "P1", "base": 0x9246B9C0},
            "P2-C1": {"slotname": "P2-C1", "teamtag": "P2", "base": 0x927EB9E0},
        }
        self.assertEqual(
            megacrash._mission_megacrash_victim_slot(self._payload(), snaps),
            "P2-C1",
        )

    def test_light_trigger_arms_when_5c_step_completes(self):
        state = self._operator_state()
        payload = self._payload()
        payload["completed_step_count"] = 3
        state = megacrash._tick_mission_megacrash_light(
            state, payload, {}, now=10.0, frame_idx=600
        )
        light = state.get("mission_light") or {}
        self.assertEqual(light.get("fire_frame"), 605)
        self.assertTrue(light.get("triggered"))

    def test_light_trigger_fires_after_five_frames_without_full_trainer(self):
        state = self._operator_state()
        payload = self._payload()
        payload["completed_step_count"] = 3
        snaps = {
            "P1-C1": {
                "slotname": "P1-C1", "teamtag": "P1", "base": 0x9246B9C0,
                "attA": 0x123, "mv_label": "5C",
            },
            "P2-C1": {
                "slotname": "P2-C1", "teamtag": "P2", "base": 0x927EB9E0,
                "attA": 64, "mv_label": "Hitstun",
            },
        }
        writes: list[tuple[int, str]] = []

        def fake_write(victim: dict, key: str) -> list[int]:
            writes.append((int(victim.get("base") or 0), key))
            return [int(victim.get("base") or 0) + megacrash.ATT_ID_OFF_PRIMARY]

        with mock.patch.object(megacrash, "_mission_megacrash_write_action", side_effect=fake_write):
            state = megacrash._tick_mission_megacrash_light(
                state, payload, snaps, now=10.0, frame_idx=600
            )
            state = megacrash._tick_mission_megacrash_light(
                state, payload, snaps, now=10.1, frame_idx=605
            )
        self.assertEqual(writes[0][0], 0x927EB9E0)
        self.assertIn("mission-light-start", writes[0][1])
        self.assertIsNotNone((state.get("mission_light") or {}).get("pulse"))

    def test_light_trigger_retries_if_victim_snapshot_is_temporarily_missing(self):
        state = self._operator_state()
        payload = self._payload()
        payload["completed_step_count"] = 3
        state = megacrash._tick_mission_megacrash_light(
            state, payload, {}, now=10.0, frame_idx=600
        )
        state = megacrash._tick_mission_megacrash_light(
            state, payload, {}, now=10.1, frame_idx=605
        )
        self.assertEqual((state.get("mission_light") or {}).get("fire_frame"), 605)

    def test_light_trigger_rearms_after_mission_failure(self):
        state = self._operator_state()
        payload = self._payload()
        payload["completed_step_count"] = 3
        state = megacrash._tick_mission_megacrash_light(
            state, payload, {}, now=10.0, frame_idx=600
        )
        payload["completed_step_count"] = 0
        state = megacrash._tick_mission_megacrash_light(
            state, payload, {}, now=10.1, frame_idx=606
        )
        light = state.get("mission_light") or {}
        self.assertFalse(light.get("triggered"))
        self.assertNotIn("fire_frame", light)

    def test_casshan_burst_mission_uses_same_light_path(self):
        data = json.loads((APP_DIR / "missions" / "casshan.json").read_text(encoding="utf-8"))
        mission = next(
            item for item in data.get("missions", [])
            if item.get("setup_megacrash_trainer")
        )
        self.assertEqual(mission["setup_megacrash_trainer"]["target_label"], "5C")
        self.assertEqual(mission["setup_megacrash_trainer"]["delay_frames"], 5)

    def test_main_calls_light_mission_trigger_before_operator_trainer(self):
        source = read("main.py")
        light_pos = source.index("_tick_mission_megacrash_light(")
        trainer_pos = source.index("_tick_megacrash_trainer(", light_pos)
        self.assertLess(light_pos, trainer_pos)
        self.assertNotIn("_sync_mission_megacrash_trainer(", source)

    def test_primary_and_nested_megacrash_modules_match(self):
        self.assertEqual(
            read("tvcgui/runtime/megacrash.py"),
            read("tdp-modules/tvcgui/runtime/megacrash.py"),
        )

    def test_regression_runner_synchronizes_megacrash_mirror(self):
        runner = read("run_regression_tests.py")
        self.assertIn(
            '("tvcgui/runtime/megacrash.py", "tdp-modules/tvcgui/runtime/megacrash.py")',
            runner,
        )


if __name__ == "__main__":
    unittest.main()
