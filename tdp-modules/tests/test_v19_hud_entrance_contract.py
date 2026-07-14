from __future__ import annotations

import os
import sys
import types
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
sys.modules.setdefault("win32con", types.SimpleNamespace())
sys.modules.setdefault("win32gui", types.SimpleNamespace())

import pygame

from tests.v19_contract_helpers import function_source, read
from tvcgui.features.overlay import hud_renderer as hud

HUD = "tvcgui/features/overlay/hud_renderer.py"


class V19HudEntranceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.font.init()

    def setUp(self):
        self.old_anim_state = hud._anim_state
        self.old_display_slots = hud._display_slots
        hud._anim_state = {"overlay_alpha": 0.0, "slots": {}, "teams": {}}
        hud._display_slots = {}

    def tearDown(self):
        hud._anim_state = self.old_anim_state
        hud._display_slots = self.old_display_slots

    @staticmethod
    def _sample_slots() -> dict:
        return {
            "P1-C1": {"name": "Ryu", "cur": 50000, "max": 50000, "meter": 50000, "mv_id_display": 256, "baroque_red_pct_max": 0.0},
            "P1-C2": {"name": "Chun-Li", "cur": 48000, "max": 50000, "meter": 50000, "mv_id_display": 0, "baroque_red_pct_max": 0.0},
            "P2-C1": {"name": "Alex", "cur": 50000, "max": 50000, "meter": 20000, "mv_id_display": 0, "baroque_red_pct_max": 0.0},
            "P2-C2": {"name": "Batsu", "cur": 50000, "max": 50000, "meter": 20000, "mv_id_display": 0, "baroque_red_pct_max": 0.0},
        }

    def _render_sequence(self, frame_count: int) -> pygame.Surface:
        slots = self._sample_slots()
        for label in slots:
            hud._get_slot_anim(label)["present"] = True
        screen = pygame.Surface((1280, 720), pygame.SRCALPHA)
        font = pygame.font.Font(None, 24)
        font_sm = pygame.font.Font(None, 17)
        for _ in range(frame_count):
            screen.fill((0, 0, 0, 0))
            hud.draw_overlay(screen, font, font_sm, slots, 1.0, 1.0 / 60.0, None)
        return screen

    def test_lock_ease_starts_and_ends_exactly(self):
        self.assertEqual(hud._compact_lock_ease(0.0), 0.0)
        self.assertEqual(hud._compact_lock_ease(1.0), 1.0)

    def test_lock_ease_overshoot_is_restrained(self):
        values = [hud._compact_lock_ease(index / 100.0) for index in range(101)]
        self.assertGreater(max(values), 1.0)
        self.assertLess(max(values), 1.02)

    def test_stage_progress_waits_for_its_start(self):
        self.assertEqual(hud._hud_stage_progress(0.29, 0.30, 0.20), 0.0)
        self.assertGreater(hud._hud_stage_progress(0.40, 0.30, 0.20), 0.0)

    def test_stage_progress_settles_at_one(self):
        self.assertEqual(hud._hud_stage_progress(0.50, 0.30, 0.20), 1.0)

    def test_team_state_tracks_entrance_age(self):
        source = function_source(HUD, "_get_team_anim")
        self.assertIn('"entrance_age": 0.0', source)
        self.assertIn('"entrance_active": False', source)

    def test_team_shell_starts_beyond_the_outer_edge(self):
        source = function_source(HUD, "_draw_compact_team_panel")
        self.assertIn('team_anim["slide_x"] = -float(width + margin_x + 28) if is_left else float(width + margin_x + 28)', source)
        self.assertIn('shell_travel = float(width + margin_x + 28)', source)

    def test_team_shell_uses_lock_ease(self):
        source = function_source(HUD, "_draw_compact_team_panel")
        self.assertIn('shell_age = max(0.0, entrance_age - 0.12)', source)
        self.assertIn('shell_progress = _compact_lock_ease(shell_age / 0.48)', source)
        self.assertIn('start_x * (1.0 - shell_progress)', source)

    def test_right_team_has_a_small_deliberate_delay(self):
        source = function_source(HUD, "_draw_compact_team_panel")
        self.assertIn('team_anim["entrance_age"] = -0.07 if not is_left else 0.0', source)

    def test_content_uses_a_panel_sized_layer_not_a_full_screen_copy(self):
        source = function_source(HUD, "_draw_compact_team_panel")
        self.assertIn('screen = pygame.Surface((width, height + content_extra_h), pygame.SRCALPHA)', source)
        self.assertNotIn('pygame.Surface(root_screen.get_size()', source)

    def test_inner_elements_are_clipped_to_the_panel_shell(self):
        source = function_source(HUD, "_draw_compact_team_panel")
        self.assertIn('panel_clip = pygame.Rect(panel_x, panel_y, width, height)', source)
        self.assertIn('panel_clip,', source)

    def test_five_primary_content_bands_are_staged(self):
        source = function_source(HUD, "_draw_compact_team_panel")
        self.assertIn('stage_bands = (', source)
        self.assertEqual(source.count('(pygame.Rect(0,'), 5)

    def test_content_stage_start_times_are_ordered(self):
        source = function_source(HUD, "_draw_compact_team_panel")
        starts = [source.index(token) for token in ('0.30, 0.20', '0.45, 0.18', '0.58, 0.18', '0.71, 0.19', '0.86, 0.19')]
        self.assertEqual(starts, sorted(starts))

    def test_tag_and_combo_cards_arrive_after_main_panel_content(self):
        source = function_source(HUD, "_draw_compact_team_panel")
        self.assertIn('_hud_stage_progress(entrance_age, 1.00, 0.20)', source)
        self.assertIn('_hud_stage_progress(entrance_age, 1.10, 0.22)', source)

    def test_left_and_right_stages_slide_from_opposite_directions(self):
        layer = pygame.Surface((12, 6), pygame.SRCALPHA)
        layer.fill((255, 255, 255, 255))
        left = pygame.Surface((80, 20), pygame.SRCALPHA)
        right = pygame.Surface((80, 20), pygame.SRCALPHA)
        rect = pygame.Rect(0, 0, 12, 6)
        hud._blit_hud_stage(left, layer, rect, 34, 4, 0.6, True, 1.0)
        hud._blit_hud_stage(right, layer, rect, 34, 4, 0.6, False, 1.0)
        left_box = pygame.mask.from_surface(left).get_bounding_rects()[0]
        right_box = pygame.mask.from_surface(right).get_bounding_rects()[0]
        self.assertLess(left_box.x, 34)
        self.assertGreater(right_box.x, 34)

    def test_restart_resets_existing_team_entrances(self):
        team = hud._get_team_anim("P1")
        team.update({"present": True, "entrance_age": 1.2, "entrance_active": False, "alpha": 1.0})
        hud._restart_hud_entrance()
        self.assertFalse(team["present"])
        self.assertEqual(team["entrance_age"], 0.0)
        self.assertTrue(team["entrance_active"])
        self.assertEqual(team["alpha"], 0.0)

    def test_hud_renderer_restarts_after_visibility_returns(self):
        source = read(HUD)
        class_start = source.index("class HudRenderer:")
        draw_start = source.index("    def draw(self, screen: pygame.Surface, control=None) -> None:", class_start)
        draw_end = source.index("\n# ---------------------------------------------------------------------------", draw_start)
        draw_source = source[draw_start:draw_end]
        self.assertIn('self._hud_was_visible = False', draw_source)
        self.assertIn('if not self._hud_was_visible:', draw_source)
        self.assertIn('_restart_hud_entrance()', draw_source)

    def test_load_sequence_builds_instead_of_appearing_all_at_once(self):
        early = self._render_sequence(10)
        early_energy = int(pygame.surfarray.array_alpha(early).sum())
        middle = self._render_sequence(26)
        middle_energy = int(pygame.surfarray.array_alpha(middle).sum())
        final = self._render_sequence(80)
        final_energy = int(pygame.surfarray.array_alpha(final).sum())
        self.assertGreater(middle_energy, early_energy)
        self.assertGreater(final_energy, middle_energy)

    def test_final_hud_contains_both_team_panels(self):
        final = self._render_sequence(80)
        alpha = pygame.surfarray.array_alpha(final)
        self.assertGreater(int(alpha[:640, :].sum()), 0)
        self.assertGreater(int(alpha[640:, :].sum()), 0)

    def test_block_gradient_slides_then_locks_before_sheen(self):
        source = function_source(HUD, "_draw_live_interaction_ribbon")
        slide = source.index('slice_offset_x =')
        lock = source.index('if slice_lock_pulse > 0.001:')
        sheen = source.index('if sheen_alpha > 0.0:')
        self.assertLess(slide, lock)
        self.assertLess(lock, sheen)

    def test_duplicate_renderer_is_expected_to_match_primary(self):
        self.assertEqual(read(HUD), read("tdp-modules/tvcgui/features/overlay/hud_renderer.py"))


if __name__ == "__main__":
    unittest.main()
