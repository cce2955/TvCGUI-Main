from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.modules.setdefault("win32con", types.SimpleNamespace())
sys.modules.setdefault("win32gui", types.SimpleNamespace())

import pygame

from tests.v19_contract_helpers import function_source, read
from tvcgui.features.overlay import hud_renderer as hud

HUD = "tvcgui/features/overlay/hud_renderer.py"


class V19EventPolishContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.font.init()

    def setUp(self):
        self.old_anim_state = hud._anim_state
        self.old_display_slots = hud._display_slots
        self.old_ledgers = hud._combo_ledgers
        self.old_frame = hud._frame
        self.old_ribbon = dict(hud._interaction_ribbon)
        hud._anim_state = {
            "overlay_alpha": 0.0,
            "slots": {},
            "teams": {},
            "assembly_age": 0.0,
            "assembly_active": False,
            "match_reset_armed": False,
            "last_match_reset_frame": -9999,
        }
        hud._display_slots = {}
        hud._combo_ledgers = {"P1": {}, "P2": {}}
        hud._frame = 200
        hud._interaction_ribbon.update({
            "title": "",
            "detail": "",
            "color": (130, 175, 255),
            "stamp": "",
            "life": 0.0,
            "age": 0.0,
        })

    def tearDown(self):
        hud._anim_state = self.old_anim_state
        hud._display_slots = self.old_display_slots
        hud._combo_ledgers = self.old_ledgers
        hud._frame = self.old_frame
        hud._interaction_ribbon.clear()
        hud._interaction_ribbon.update(self.old_ribbon)

    @staticmethod
    def _slots() -> dict:
        return {
            "P1-C1": {"name": "Ryu", "cur": 50000, "max": 50000, "meter": 50000, "mv_id_display": 256, "mv_label": "5A", "baroque_red_pct_max": 0.0},
            "P1-C2": {"name": "Chun-Li", "cur": 48000, "max": 50000, "meter": 30000, "mv_id_display": 0, "mv_label": "Idle", "baroque_red_pct_max": 0.0},
            "P2-C1": {"name": "Alex", "cur": 50000, "max": 50000, "meter": 20000, "mv_id_display": 0, "mv_label": "Idle", "baroque_red_pct_max": 0.0},
            "P2-C2": {"name": "Batsu", "cur": 50000, "max": 50000, "meter": 20000, "mv_id_display": 0, "mv_label": "Idle", "baroque_red_pct_max": 0.0},
        }

    def _register_hits(self, count: int) -> dict:
        with (
            mock.patch.object(hud, "_get_active_slot", return_value="P1-C1"),
            mock.patch.object(hud, "_snap_name", side_effect=lambda slot: "Ryu" if slot == "P1-C1" else "Alex"),
            mock.patch.object(hud, "_snap_int", return_value=50000),
            mock.patch.object(hud, "_snap_float", return_value=0.0),
        ):
            for _ in range(count):
                hud._frame += 2
                hud._combo_register_damage("P2-C1", 100)
        return hud._combo_ledgers["P1"]

    def test_slot_state_tracks_health_trail(self):
        state = hud._get_slot_anim("P1-C1")
        self.assertIn("hp_trail_frac", state)
        self.assertIn("hp_trail_delay", state)

    def test_damage_sets_health_trail_delay(self):
        snap = self._slots()["P2-C1"]
        hud._display_slots["P2-C1"] = dict(snap)
        hud._compact_track_slot("P2-C1", snap)
        damaged = dict(snap, cur=42000)
        hud._display_slots["P2-C1"] = damaged
        with mock.patch.object(hud, "_trigger_impact_recoil"):
            hud._compact_track_slot("P2-C1", damaged)
        self.assertAlmostEqual(hud._get_slot_anim("P2-C1")["hp_trail_delay"], 0.16)

    def test_health_trail_draws_behind_current_health(self):
        plain = pygame.Surface((180, 20), pygame.SRCALPHA)
        trail = pygame.Surface((180, 20), pygame.SRCALPHA)
        hud._draw_compact_health(plain, 4, 5, 160, 8, 25000, 50000, False, 0.5, 0.5)
        hud._draw_compact_health(trail, 4, 5, 160, 8, 25000, 50000, False, 0.5, 0.8)
        self.assertNotEqual(pygame.image.tostring(plain, "RGBA"), pygame.image.tostring(trail, "RGBA"))

    def test_health_trail_has_a_bright_catchup_edge(self):
        source = function_source(HUD, "_draw_compact_health")
        self.assertIn("trail_color =", source)
        self.assertIn("(255, 198, 151)", source)

    def test_impact_recoil_scales_with_damage(self):
        hud._trigger_impact_recoil("P2", 500)
        light = hud._get_team_anim("P2")["impact_recoil_power"]
        hud._get_team_anim("P2")["impact_recoil_power"] = 0.0
        hud._trigger_impact_recoil("P2", 9000)
        heavy = hud._get_team_anim("P2")["impact_recoil_power"]
        self.assertGreater(heavy, light)
        self.assertLessEqual(heavy, 4.5)

    def test_damage_triggers_recoil_only_for_struck_team(self):
        snap = self._slots()["P2-C1"]
        hud._compact_track_slot("P2-C1", snap)
        damaged = dict(snap, cur=44000)
        with mock.patch.object(hud, "_trigger_impact_recoil") as recoil:
            hud._compact_track_slot("P2-C1", damaged)
        recoil.assert_called_once_with("P2", 6000)

    def test_recoil_finishes_quickly(self):
        hud._trigger_impact_recoil("P1", 5000)
        state = hud._get_team_anim("P1")
        hud._tick_team_panel_fx(state, 0.17)
        self.assertEqual(state["impact_recoil_age"], 1.0)
        self.assertEqual(state["impact_recoil_power"], 0.0)

    def test_recoil_is_outward_for_each_team(self):
        source = function_source(HUD, "_draw_compact_team_panel")
        self.assertIn('shake_dir = -1.0 if is_left else 1.0', source)
        self.assertIn('impact_x = shake_dir * float(team_anim.get("impact_recoil_power", 0.0)) * impact_curve', source)

    def test_meter_loss_starts_spend_sweep(self):
        snap = self._slots()["P1-C1"]
        hud._compact_track_slot("P1-C1", snap)
        spent = dict(snap, meter=25000)
        hud._compact_track_slot("P1-C1", spent)
        state = hud._get_slot_anim("P1-C1")
        self.assertEqual(state["meter_spend_sweep"], 1.0)
        self.assertEqual(state["meter_spend_amount"], 25000)

    def test_meter_gain_does_not_start_spend_sweep(self):
        snap = dict(self._slots()["P1-C1"], meter=10000)
        hud._compact_track_slot("P1-C1", snap)
        hud._compact_track_slot("P1-C1", dict(snap, meter=20000))
        self.assertEqual(hud._get_slot_anim("P1-C1")["meter_spend_sweep"], 0.0)

    def test_meter_spend_sweep_moves_right_to_left(self):
        source = function_source(HUD, "_draw_compact_meter")
        self.assertIn("rect.right + band_w - progress * (rect.width + band_w * 2)", source)
        self.assertIn("pygame.BLEND_RGBA_ADD", source)

    def test_meter_spend_sweep_changes_rendered_meter(self):
        plain = pygame.Surface((220, 30), pygame.SRCALPHA)
        spent = pygame.Surface((220, 30), pygame.SRCALPHA)
        hud._draw_compact_meter(plain, 5, 5, 180, 30000, 1.0, False, 0.0, 0)
        hud._draw_compact_meter(spent, 5, 5, 180, 30000, 1.0, False, 0.5, 20000)
        self.assertNotEqual(pygame.image.tostring(plain, "RGBA"), pygame.image.tostring(spent, "RGBA"))

    def test_combo_ten_hit_milestone_arms(self):
        ledger = self._register_hits(10)
        self.assertEqual(ledger["milestone_hit"], 10)
        self.assertEqual(ledger["milestone_sheen"], 1.0)
        self.assertEqual(ledger["milestone_scale"], 1.0)

    def test_combo_twenty_hit_milestone_arms(self):
        ledger = self._register_hits(20)
        self.assertEqual(ledger["milestone_hit"], 20)

    def test_combo_thirty_hit_milestone_arms(self):
        ledger = self._register_hits(30)
        self.assertEqual(ledger["milestone_hit"], 30)

    def test_non_milestone_hit_does_not_rearm_lock(self):
        ledger = self._register_hits(11)
        self.assertEqual(ledger["milestone_hit"], 10)

    def test_milestone_state_decays(self):
        ledger = self._register_hits(10)
        hud._tick_combo_ledgers(0.1)
        self.assertLess(ledger["milestone_sheen"], 1.0)
        self.assertLess(ledger["milestone_scale"], 1.0)

    def test_milestone_sheen_is_sharper_than_regular_hit_sheen(self):
        regular = pygame.Surface((220, 34), pygame.SRCALPHA)
        milestone = pygame.Surface((220, 34), pygame.SRCALPHA)
        hud._blit_combo_sheen(regular, 0.5, 1.0, heavy=False)
        hud._blit_combo_milestone(milestone, 0.5, 1.0)
        regular_energy = int(pygame.surfarray.array3d(regular).sum()) + int(pygame.surfarray.array_alpha(regular).sum())
        milestone_energy = int(pygame.surfarray.array3d(milestone).sum()) + int(pygame.surfarray.array_alpha(milestone).sum())
        self.assertGreater(milestone_energy, regular_energy)

    def test_milestone_card_uses_restrained_scale(self):
        source = function_source(HUD, "_draw_combo_ledger")
        self.assertIn("scale_factor = 1.0 + 0.075 * scale_envelope", source)
        self.assertIn("pygame.transform.smoothscale", source)

    def test_counter_stamp_classification(self):
        self.assertEqual(hud._interaction_stamp({"victim_was_attacking": True}, True), "COUNTER")

    def test_punish_stamp_classification(self):
        self.assertEqual(hud._interaction_stamp({"victim_was_committed": True}, True), "PUNISH")

    def test_reversal_stamp_has_priority(self):
        state = {"attacker_called_reversal": True, "victim_was_attacking": True, "victim_was_committed": True}
        self.assertEqual(hud._interaction_stamp(state, True), "REVERSAL")

    def test_block_never_gets_an_attack_stamp(self):
        state = {"attacker_called_reversal": True, "victim_was_attacking": True}
        self.assertEqual(hud._interaction_stamp(state, False), "")

    def test_publish_stores_stamp_in_ribbon(self):
        hud._display_slots.update({
            "P1-C1": {"name": "Ryu", "cur": 50000, "mv_label": "5A"},
            "P2-C1": {"name": "Alex", "cur": 48000, "mv_label": "Recovery"},
        })
        state = {
            "victim_hp_start": 50000,
            "attack_move": "5A",
            "attacker_name": "Ryu",
            "victim_name": "Alex",
            "victim_was_committed": True,
        }
        hud._publish_interaction("P1-C1", "P2-C1", state, 4)
        self.assertEqual(hud._interaction_ribbon["stamp"], "PUNISH")

    def test_stamp_plate_has_distinct_event_colors(self):
        source = function_source(HUD, "_draw_live_interaction_ribbon")
        self.assertIn('"COUNTER":', source)
        self.assertIn('"PUNISH":', source)
        self.assertIn('"REVERSAL":', source)
        self.assertIn("stamp_in = _compact_lock_ease", source)

    def test_tag_handoff_uses_opposed_horizontal_motion(self):
        source = function_source(HUD, "_draw_compact_team_panel")
        self.assertIn("incoming_row_dx = int(forward_sign * 18 * scale * swap_progress)", source)
        self.assertIn("outgoing_row_dx = int(-forward_sign * 14 * scale * swap_progress)", source)

    def test_tag_change_arms_lock_flash(self):
        slots = self._slots()
        for label in slots:
            hud._get_slot_anim(label)["present"] = True
        screen = pygame.Surface((1280, 720), pygame.SRCALPHA)
        font = pygame.font.Font(None, 24)
        font_sm = pygame.font.Font(None, 17)
        team = hud._get_team_anim("P1")
        team.update({"present": True, "current_point_label": "P1-C1", "alpha": 1.0, "entrance_active": False})
        with mock.patch.object(hud, "_get_active_slot", return_value="P1-C2"):
            hud._draw_compact_team_panel(screen, font, font_sm, "P1", slots, 1.0, 1.0, 1.0 / 60.0)
        self.assertTrue(team["tag_lock_pending"])
        self.assertGreater(team["swap_progress"], 0.0)

    def test_tag_lock_flash_fires_when_handoff_settles(self):
        source = function_source(HUD, "_draw_compact_team_panel")
        self.assertIn('float(team_anim.get("swap_progress", 0.0)) <= 0.08', source)
        self.assertIn('team_anim["tag_lock_flash"] = 1.0', source)

    def test_restart_arms_center_assembly(self):
        hud._restart_hud_entrance()
        self.assertTrue(hud._anim_state["assembly_active"])
        self.assertEqual(hud._anim_state["assembly_age"], 0.0)

    def test_center_assembly_renders_before_it_expires(self):
        hud._anim_state["assembly_active"] = True
        screen = pygame.Surface((1280, 720), pygame.SRCALPHA)
        hud._draw_match_assembly_spine(screen, 1.0, 0.20)
        self.assertGreater(pygame.mask.from_surface(screen).count(), 0)

    def test_center_assembly_expires_cleanly(self):
        hud._anim_state["assembly_active"] = True
        hud._anim_state["assembly_age"] = 0.70
        screen = pygame.Surface((1280, 720), pygame.SRCALPHA)
        hud._draw_match_assembly_spine(screen, 1.0, 0.03)
        self.assertFalse(hud._anim_state["assembly_active"])

    def test_low_health_arms_training_reset_detection(self):
        slots = self._slots()
        slots["P2-C1"] = dict(slots["P2-C1"], cur=10000)
        hud._maybe_restart_match_assembly(slots)
        self.assertTrue(hud._anim_state["match_reset_armed"])

    def test_full_health_after_damage_restarts_assembly(self):
        slots = self._slots()
        hud._anim_state["match_reset_armed"] = True
        hud._frame = 500
        with mock.patch.object(hud, "_restart_hud_entrance") as restart:
            hud._maybe_restart_match_assembly(slots)
        restart.assert_called_once_with()
        self.assertFalse(hud._anim_state["match_reset_armed"])

    def test_team_shell_waits_for_center_lock(self):
        source = function_source(HUD, "_draw_compact_team_panel")
        self.assertIn("shell_age = max(0.0, entrance_age - 0.12)", source)
        self.assertIn("shell_progress = _compact_lock_ease(shell_age / 0.48)", source)

    def test_draw_overlay_runs_all_event_polish_paths(self):
        slots = self._slots()
        for label in slots:
            hud._get_slot_anim(label)["present"] = True
        hud._restart_hud_entrance()
        screen = pygame.Surface((1280, 720), pygame.SRCALPHA)
        font = pygame.font.Font(None, 24)
        font_sm = pygame.font.Font(None, 17)
        for _ in range(80):
            screen.fill((0, 0, 0, 0))
            hud.draw_overlay(screen, font, font_sm, slots, 1.0, 1.0 / 60.0)
        self.assertGreater(pygame.mask.from_surface(screen).count(), 0)

    def test_charge_completion_effect_was_not_added(self):
        source = read(HUD)
        self.assertNotIn("charge_completion_lock", source)
        self.assertNotIn("charge_complete_sheen", source)

    def test_duplicate_renderer_matches_primary_after_event_polish(self):
        self.assertEqual(read(HUD), read("tdp-modules/tvcgui/features/overlay/hud_renderer.py"))


if __name__ == "__main__":
    unittest.main()
