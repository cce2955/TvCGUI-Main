from __future__ import annotations

import os
import sys
import types
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

if "dolphin_memory_engine" not in sys.modules:
    sys.modules["dolphin_memory_engine"] = types.SimpleNamespace(
        is_hooked=lambda: False,
        hook=lambda: None,
        read_bytes=lambda _addr, size: b"\0" * int(size),
        write_bytes=lambda _addr, _data: None,
    )

from tests.v19_contract_helpers import function_source
from tvcgui.features.training.mission_manager import (
    MISSION_REACTION_STATES,
    MissionManager,
    _mission_combo_reset_tick,
    _mission_route_combo_live,
)


class V19MissionComboResetContractTests(unittest.TestCase):
    def test_global_combo_counter_alone_does_not_keep_route_alive(self):
        self.assertFalse(_mission_route_combo_live(True, False))

    def test_primary_reaction_state_keeps_route_alive(self):
        manager = MissionManager({}, {}, {}, lambda: [], lambda *_args: "")
        state = next(iter(MISSION_REACTION_STATES))
        snaps = {
            "P1-C1": {"teamtag": "P1", "attA": 0, "attB": 0},
            "P2-C1": {"teamtag": "P2", "attA": state, "attB": 0},
        }
        self.assertTrue(manager._opponent_in_hitstun("P1-C1", snaps))

    def test_stale_secondary_reaction_state_is_ignored(self):
        manager = MissionManager({}, {}, {}, lambda: [], lambda *_args: "")
        state = next(iter(MISSION_REACTION_STATES))
        snaps = {
            "P1-C1": {"teamtag": "P1", "attA": 0, "attB": 0},
            "P2-C1": {"teamtag": "P2", "attA": 0, "attB": state},
        }
        self.assertFalse(manager._opponent_in_hitstun("P1-C1", snaps))

    def test_actual_hitstun_keeps_route_alive(self):
        self.assertTrue(_mission_route_combo_live(False, True))

    def test_no_counter_and_no_hitstun_is_not_live(self):
        self.assertFalse(_mission_route_combo_live(False, False))

    def test_megacrash_burst_is_classified_as_reaction_state(self):
        self.assertIn(448, MISSION_REACTION_STATES)

    def test_megacrash_burst_keeps_route_alive_as_hitstun(self):
        manager = MissionManager({}, {}, {}, lambda: [], lambda *_args: "")
        snaps = {
            "P1-C1": {"teamtag": "P1", "attA": 315},
            "P2-C1": {"teamtag": "P2", "attA": 448},
        }
        self.assertTrue(manager._opponent_in_hitstun("P1-C1", snaps))

    def test_dedicated_megacrash_edge_gets_one_match_frame(self):
        self.assertTrue(_mission_route_combo_live(False, False, True))

    def test_progress_zero_never_triggers_combo_drop_reset(self):
        self.assertEqual(_mission_combo_reset_tick(0, False, 0, None), (False, 0))

    def test_dropped_combo_without_grace_resets_immediately(self):
        self.assertEqual(_mission_combo_reset_tick(3, False, 0, None), (True, 0))

    def test_live_combo_does_not_consume_grace(self):
        self.assertEqual(_mission_combo_reset_tick(3, True, 8, 3), (False, 8))

    def test_grace_is_only_consumed_after_combo_drops(self):
        self.assertEqual(_mission_combo_reset_tick(3, False, 8, 3), (False, 7))

    def test_grace_applies_only_to_the_step_it_was_defined_for(self):
        self.assertEqual(_mission_combo_reset_tick(4, False, 8, 3), (True, 0))

    def test_one_grace_frame_allows_exactly_one_dropped_frame(self):
        first = _mission_combo_reset_tick(2, False, 1, 2)
        self.assertEqual(first, (False, 0))
        self.assertEqual(_mission_combo_reset_tick(2, False, first[1], 2), (True, 0))

    def test_multi_frame_grace_allows_exact_declared_count(self):
        remaining = 3
        for expected in (2, 1, 0):
            reset, remaining = _mission_combo_reset_tick(2, False, remaining, 2)
            self.assertFalse(reset)
            self.assertEqual(remaining, expected)
        self.assertEqual(_mission_combo_reset_tick(2, False, remaining, 2), (True, 0))

    def test_reset_path_uses_strict_helper(self):
        source = function_source(
            "tvcgui/features/training/mission_manager.py",
            "_augment_payload_with_runtime",
        )
        self.assertIn("_mission_combo_reset_tick(", source)
        self.assertIn("_mission_route_combo_live(", source)

    def test_hidden_shell_timer_does_not_define_combo_liveness(self):
        source = function_source(
            "tvcgui/features/training/mission_manager.py",
            "_augment_payload_with_runtime",
        )
        liveness_start = source.index("opponent_real_combo_state = _mission_route_combo_live")
        liveness_end = source.index("opponent_in_combo_state =", liveness_start)
        liveness_block = source[liveness_start:liveness_end]
        self.assertNotIn("shell_release_grace", liveness_block)
        self.assertNotIn("opponent_hit_confirmed_this_frame", liveness_block)

    def test_shell_install_does_not_bypass_combo_drop_reset(self):
        source = function_source(
            "tvcgui/features/training/mission_manager.py",
            "_augment_payload_with_runtime",
        )
        self.assertNotIn('elif self._runtime.get("shell_installed")', source)

    def test_reset_grace_does_not_define_confirmable_combo_state(self):
        source = function_source(
            "tvcgui/features/training/mission_manager.py",
            "_augment_payload_with_runtime",
        )
        self.assertIn("opponent_in_combo_state = opponent_real_combo_state", source)
        self.assertNotIn("or reset_grace_active_now", source)

    def test_reset_path_clears_last_seen_state_for_clean_restarts(self):
        source = function_source(
            "tvcgui/features/training/mission_manager.py",
            "_augment_payload_with_runtime",
        )
        self.assertIn('"last_seen_label": ""', source)
        self.assertIn('"last_seen_anim": None', source)
        self.assertIn('"last_inputs": {}', source)

    def test_grace_keep_alive_only_does_not_consume_while_combo_is_live(self):
        source = function_source(
            "tvcgui/features/training/mission_manager.py",
            "_augment_payload_with_runtime",
        )
        self.assertNotIn("and grace_keep_alive_only and grace_step_index", source)


if __name__ == "__main__":
    unittest.main()
