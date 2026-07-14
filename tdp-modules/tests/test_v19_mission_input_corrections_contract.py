from __future__ import annotations

import json
import os
import sys
import types
import unittest
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

if "dolphin_memory_engine" not in sys.modules:
    sys.modules["dolphin_memory_engine"] = types.SimpleNamespace(
        is_hooked=lambda: False,
        hook=lambda: None,
        read_bytes=lambda _addr, size: b"\0" * int(size),
        write_bytes=lambda _addr, _data: None,
    )

from tests.v19_contract_helpers import APP_DIR, function_source, read
from tvcgui.features.training.mission_manager import (
    MISSION_INPUT_A,
    MISSION_INPUT_B,
    MISSION_INPUT_C,
    MISSION_INPUT_P,
    MISSION_INPUT_TAUNT,
    MissionManager,
)
from tvcgui.features.training.mission_mode import _normalize_step_input_notation
from tvcgui.features.training.wiki_input_catalog import infer_wiki_input_notation


class V19MissionInputCorrectionContractTests(unittest.TestCase):
    def setUp(self):
        self.manager = MissionManager({}, {}, {}, lambda: [], lambda *_args: "")

    @staticmethod
    def _step_inputs(filename: str) -> dict[str, set[str]]:
        data = json.loads((APP_DIR / "missions" / filename).read_text(encoding="utf-8"))
        found: dict[str, set[str]] = {}
        for mission in data.get("missions", []):
            for step in mission.get("steps", []):
                if not isinstance(step, dict):
                    continue
                labels: list[str] = []
                if step.get("label"):
                    labels.append(str(step["label"]))
                if isinstance(step.get("labels"), list):
                    labels.extend(str(value) for value in step["labels"])
                for label in labels:
                    found.setdefault(label, set()).add(str(step.get("input") or ""))
        return found

    def _set_events(self, events: list[dict]) -> None:
        self.manager._runtime["mission_input_events"] = events
        self.manager._runtime["mission_input_consumed_serial"] = 0
        self.manager._runtime["mission_step_start_serial"] = 0
        self.manager._runtime["mission_input_match_serial"] = 0

    @staticmethod
    def _event(serial: int, token: str, direction: int, pressed: int = 0, held: int = 0) -> dict:
        return {
            "serial": serial,
            "frame": serial,
            "token": token,
            "direction": direction,
            "pressed": pressed,
            "held_buttons": held,
        }

    def test_explicit_generic_x_is_not_replaced_by_step_strength(self):
        self.assertEqual(
            _normalize_step_input_notation(
                "236X",
                ["Roll Swing B"],
                "Roll Swing B",
                expand_strength_variants=False,
            ),
            "236X",
        )

    def test_inferred_generic_x_can_still_expand_to_step_strength(self):
        self.assertEqual(
            _normalize_step_input_notation(
                "236X",
                ["Roll Swing B"],
                "Roll Swing B",
                expand_strength_variants=True,
            ),
            "236B",
        )

    def test_alex_inputs_are_exact(self):
        found = self._step_inputs("alex.json")
        self.assertEqual(found["Stungun Near"], {"421L+M"})
        self.assertEqual(found["Slash Elbow M"], {"[4]6X"})
        self.assertEqual(found["Wild Stomp C"], {"[2]8X"})
        self.assertEqual(found["Hyperbomb"], {"360"})

    def test_frank_and_blade_j2c_are_exact(self):
        self.assertEqual(self._step_inputs("frank_west.json")["j.2C"], {"j.2C"})
        self.assertEqual(self._step_inputs("tekkaman_blade.json")["j.2C"], {"j.2C"})

    def test_zero_dp_inputs_are_generic_623x(self):
        found = self._step_inputs("zero.json")
        for label in ("DP A", "DP C", "Air DP C"):
            self.assertEqual(found[label], {"623X"})

    def test_jun_and_ken_command_corrections_are_exact(self):
        jun = self._step_inputs("jun_the_swan.json")
        ken = self._step_inputs("ken_the_eagle.json")
        self.assertEqual(jun["Lightning Kick B"], {"623X"})
        self.assertEqual(ken["Bird Shoot B"], {"623X"})
        self.assertEqual(ken["Air Bird Smash"], {"XX"})
        self.assertEqual(ken["Random Flight A"], {"421X"})

    def test_tekkaman_windmill_and_megacrash_inputs_are_exact(self):
        found = self._step_inputs("tekkaman.json")
        self.assertEqual(found["Windmill B Air"], {"[2]8X"})
        self.assertEqual(found["Megacrash"], {"ABCP"})

    def test_yatterman_one_inputs_are_exact(self):
        found = self._step_inputs("yatterman_1.json")
        self.assertEqual(found["yatter shock C"], {"[4]6X"})
        self.assertEqual(found["yatter hop B"], {"B"})

    def test_polimar_inputs_are_exact(self):
        found = self._step_inputs("polimar.json")
        self.assertEqual(found["One Handed Vacuum Spin"], {"360"})
        self.assertEqual(found["Illusion Fist"], {"236XX"})

    def test_karas_inputs_are_exact(self):
        found = self._step_inputs("karas.json")
        expected = {
            "Tobimizuchi": "63214X",
            "Ukifune": "236",
            "Kasha C": "[4]6X",
            "Kasha Overhead": "6X",
            "Yoinagi": "46C",
        }
        for label, notation in expected.items():
            self.assertEqual(found[label], {notation})

    def test_doronjo_inputs_are_exact(self):
        found = self._step_inputs("doronjo.json")
        self.assertEqual(found["Breakdance"], {"2C"})
        self.assertEqual(found["Pummel A"], {"236A"})
        self.assertEqual(found["Taunt"], {"TAUNT(T)"})

    def test_roll_inputs_are_exact(self):
        found = self._step_inputs("roll.json")
        self.assertEqual(found["Roll Swing B"], {"236X"})
        self.assertEqual(found["Bucket B"], {"623X"})

    def test_yatterman_two_inputs_are_exact(self):
        found = self._step_inputs("yatterman_2.json")
        self.assertEqual(found["Yatter Step"], {"236X"})
        self.assertEqual(found["Yatter Step B"], {"B"})
        self.assertEqual(found["Yatter Step C"], {"C"})
        self.assertEqual(found["Omochama"], {"623XX"})
        self.assertEqual(found["This Week's Special Robots"], {"214XX"})

    def test_joe_inputs_are_exact(self):
        found = self._step_inputs("Joe_the_condor.json")
        self.assertEqual(found["Wild Lasso A"], {"46A"})
        self.assertEqual(found["Cactus Bunker A"], {"63214X"})

    def test_non_giant_ground_throw_inputs_are_exact(self):
        self.assertEqual(self._step_inputs("soki.json")["Back Throw"], {"4C"})
        self.assertEqual(self._step_inputs("saki.json")["Forward Throw"], {"6C"})
        yatter = self._step_inputs("yatterman_2.json")
        self.assertEqual(yatter["Forward Throw"], {"6C / 4C"})
        self.assertEqual(yatter["Back Throw"], {"6C / 4C"})

    def test_soki_oni_tactics_is_only_623xx(self):
        self.assertEqual(self._step_inputs("soki.json")["Oni Tactics"], {"623XX"})
        self.assertEqual(infer_wiki_input_notation("Soki", ["Oni Tactics"]), "623XX")

    def test_421_light_medium_chord_matches_a_b_bits(self):
        ab = MISSION_INPUT_A | MISSION_INPUT_B
        self._set_events([
            self._event(1, "4", 2),
            self._event(2, "2", 8),
            self._event(3, "1", 10),
            self._event(4, "5AB", 0, ab, ab),
        ])
        self.assertTrue(self.manager._command_input_matches("421L+M", 4))

    def test_bare_xx_matches_two_simultaneous_attack_buttons(self):
        ab = MISSION_INPUT_A | MISSION_INPUT_B
        self._set_events([self._event(1, "5AB", 0, ab, ab)])
        self.assertTrue(self.manager._command_input_matches("XX", 1))

    def test_abcp_matches_megacrash_chord(self):
        chord = MISSION_INPUT_A | MISSION_INPUT_B | MISSION_INPUT_C | MISSION_INPUT_P
        self._set_events([self._event(1, "5ABCP", 0, chord, chord)])
        self.assertTrue(self.manager._command_input_matches("ABCP", 1))

    def test_neutral_strength_only_matches_neutral_button(self):
        self._set_events([self._event(1, "5B", 0, MISSION_INPUT_B, MISSION_INPUT_B)])
        self.assertTrue(self.manager._command_input_matches("B", 1))

    def test_taunt_notation_matches_taunt_bits(self):
        self._set_events([
            self._event(1, "5T", 0, MISSION_INPUT_TAUNT, MISSION_INPUT_TAUNT)
        ])
        self.assertTrue(self.manager._command_input_matches("TAUNT(T)", 1))

    def test_360_matches_complete_cardinal_rotation(self):
        self._set_events([
            self._event(1, "6", 1),
            self._event(2, "2", 8),
            self._event(3, "4", 2),
            self._event(4, "8", 4),
        ])
        self.assertTrue(self.manager._command_input_matches("360", 4))

    def test_360_rejects_incomplete_rotation(self):
        self._set_events([
            self._event(1, "6", 1),
            self._event(2, "2", 8),
            self._event(3, "4", 2),
        ])
        self.assertFalse(self.manager._command_input_matches("360", 3))

    def test_megacrash_step_uses_opponent_state_edge(self):
        self.assertTrue(self.manager._megacrash_step_matches(["Megacrash"], True, False))
        self.assertFalse(self.manager._megacrash_step_matches(["Megacrash"], True, True))
        self.assertFalse(self.manager._megacrash_step_matches(["5C"], True, False))

    def test_dedicated_megacrash_match_is_in_route_advance_path(self):
        source = function_source(
            "tvcgui/features/training/mission_manager.py",
            "_augment_payload_with_runtime",
        )
        self.assertIn("dedicated_megacrash_match", source)
        self.assertIn("or dedicated_megacrash_match", source)

    def test_renderer_has_explicit_charge_capsule(self):
        source = function_source(
            "tvcgui/features/overlay/master_renderer.py",
            "_mission_charge_direction_chip",
        )
        self.assertIn('render("HOLD"', source)
        self.assertIn("_mission_direction_icon", source)

    def test_renderer_has_360_chip(self):
        source = function_source(
            "tvcgui/features/overlay/master_renderer.py",
            "_mission_rotation_chip",
        )
        self.assertIn('render("360"', source)
        self.assertIn("pygame.draw.arc", source)

    def test_renderer_has_jump_down_c_layout(self):
        source = function_source(
            "tvcgui/features/overlay/master_renderer.py",
            "_render_mission_input_notation",
        )
        self.assertIn('re.fullmatch(r"J\\.?2C"', source)
        self.assertIn('_mission_direction_icon("2"', source)

    def test_renderer_has_taunt_word_and_t_chip(self):
        source = function_source(
            "tvcgui/features/overlay/master_renderer.py",
            "_render_mission_input_notation",
        )
        self.assertIn('{"TAUNT", "TAUNT(T)", "T"}', source)
        self.assertIn('render("TAUNT"', source)

    def test_runner_protects_mission_data_and_notation_modules(self):
        runner = read("run_regression_tests.py")
        self.assertIn('"tvcgui/features/training/mission_mode.py"', runner)
        self.assertIn('"tvcgui/features/training/wiki_input_catalog.py"', runner)
        self.assertIn('(APP_DIR / "missions").glob("*.json")', runner)

    def test_runner_synchronizes_master_renderer_mirror(self):
        runner = read("run_regression_tests.py")
        self.assertIn(
            '("tvcgui/features/overlay/master_renderer.py", "tdp-modules/tvcgui/features/overlay/master_renderer.py")',
            runner,
        )


if __name__ == "__main__":
    unittest.main()
