from __future__ import annotations

import os
import sys
import types
import unittest

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.modules.setdefault("win32con", types.SimpleNamespace())
sys.modules.setdefault("win32gui", types.SimpleNamespace())

import pygame

from tests.v19_contract_helpers import function_source, read
from tvcgui.features.overlay import master_renderer as mission_hud

MASTER = "tvcgui/features/overlay/master_renderer.py"
DUPLICATE = "tdp-modules/tvcgui/features/overlay/master_renderer.py"


class V19MissionRedesignContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        pygame.font.init()

    @staticmethod
    def _steps() -> list[dict]:
        return [
            {"display": "5A", "input": "A", "color": "blue"},
            {"display": "5B", "input": "B", "color": "yellow"},
            {"display": "2B", "input": "2 B", "color": "yellow"},
            {"display": "5C", "input": "C", "color": "green"},
            {"display": "6C", "input": "6 C", "color": "green"},
            {"display": "Hado L", "input": "2 3 6 A", "color": "blue"},
        ]

    @classmethod
    def _overlay(cls, intro: float = 1.0) -> mission_hud.MasterOverlay:
        overlay = mission_hud.MasterOverlay()
        overlay.w = 1280
        overlay.h = 720
        overlay.screen = pygame.Surface((1280, 720), pygame.SRCALPHA)
        overlay.font = pygame.font.SysFont("consolas", 22, bold=True)
        overlay.smallfont = pygame.font.SysFont("consolas", 16)
        overlay.mission_active = True
        overlay.mission_slot = "P1-C1"
        overlay._mission_intro_phase = intro
        return overlay

    @classmethod
    def _draw_panel(
        cls,
        intro: float = 1.0,
        completed: int = 1,
        current: int = 1,
    ) -> mission_hud.MasterOverlay:
        overlay = cls._overlay(intro)
        steps = cls._steps()
        data = {
            "active_mission_id": "ryu-c1",
            "active_mission_name": "Challenge",
            "active_mission_notes": "Complete the sequence cleanly.",
            "selector_hint": "Down, Down, Taunt: Open Mission",
            "active_mission_steps": steps,
            "completed_step_count": completed,
            "current_step_index": current,
        }
        for index in range(len(steps)):
            overlay.step_anim[index] = 0.0 if index < completed else (1.0 if index == current else 0.0)
        overlay._draw_active_mission_panel(
            data=data,
            character="Ryu",
            theme_color=(76, 139, 228),
            mission_name="Challenge",
            mission_notes="Complete the sequence cleanly.",
            steps=steps,
            selector_hint="Down, Down, Taunt: Open Mission",
            goal_progress_type=None,
            goal_target_state=None,
            goal_current_frames=0,
            goal_needed_frames=0,
            goal_timer_active=False,
        )
        return overlay

    def test_intro_ease_is_clamped_and_monotonic(self):
        self.assertEqual(mission_hud._mission_intro_ease(-1.0), 0.0)
        self.assertEqual(mission_hud._mission_intro_ease(2.0), 1.0)
        self.assertLess(mission_hud._mission_intro_ease(0.2), mission_hud._mission_intro_ease(0.8))

    def test_panel_intro_reaches_full_opacity(self):
        self.assertEqual(mission_hud._mission_panel_intro_progress(0.0), 0.0)
        self.assertEqual(mission_hud._mission_panel_intro_progress(1.0), 1.0)

    def test_rows_enter_in_staggered_order(self):
        first = mission_hud._mission_row_intro_progress(0.20, 0)
        second = mission_hud._mission_row_intro_progress(0.20, 1)
        third = mission_hud._mission_row_intro_progress(0.20, 2)
        self.assertGreater(first, second)
        self.assertGreater(second, third)

    def test_all_rows_settle_after_intro(self):
        for row in range(6):
            self.assertEqual(mission_hud._mission_row_intro_progress(2.0, row), 1.0)

    def test_complete_plate_progress_locks_and_sheens(self):
        alpha, lock, sheen = mission_hud._mission_complete_plate_progress(0.65)
        self.assertGreater(alpha, 0.9)
        self.assertGreater(lock, 0.9)
        self.assertGreater(sheen, 0.0)
        self.assertLess(sheen, 1.0)

    def test_complete_plate_exits_cleanly(self):
        alpha, _lock, _sheen = mission_hud._mission_complete_plate_progress(2.80)
        self.assertEqual(alpha, 0.0)

    def test_panel_uses_wider_responsive_layout(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("panel_w = max(560", source)
        self.assertIn("int(self.w * 0.48)", source)
        self.assertIn(", 820)", source)

    def test_panel_uses_flat_angular_body(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("panel_points = [", source)
        self.assertIn("pygame.draw.polygon(panel", source)
        self.assertNotIn("_draw_vertical_gradient", source)
        self.assertNotIn("scan_period", source)

    def test_header_has_segmented_progress_strip(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("segment_count = max(1, total_steps)", source)
        self.assertIn("for seg in range(segment_count)", source)
        self.assertIn('progress_label = self.smallfont.render("COMPLETED"', source)

    def test_completed_challenge_gets_done_badge(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("mission_done = total_steps > 0", source)
        self.assertIn('done_text = self.smallfont.render("DONE"', source)

    def test_completed_steps_keep_visible_done_chip(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn('done = self.smallfont.render("DONE"', source)
        self.assertIn("readable afterward so completed work is still obvious", source)

    def test_current_step_gets_current_chip(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn('current = self.smallfont.render("CURRENT"', source)
        self.assertIn("is_current = idx == current_index and not mission_done", source)

    def test_input_notation_is_preserved_on_redesigned_rows(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("self._render_mission_input_notation(input_text, accent)", source)

    def test_rows_use_slide_and_fade_together(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("row_shift = int(round((1.0 - row_intro) * 32.0))", source)
        self.assertIn("row_layer.set_alpha(int(255 * row_intro))", source)

    def test_activation_restarts_intro_sequence(self):
        overlay = mission_hud.MasterOverlay()
        overlay.mission_active = True
        overlay.mission_slot = "P1-C1"
        overlay.mission_overlay_data = {
            "active_mission_id": "mission-a",
            "active_mission_steps": self._steps(),
            "completed_step_count": 0,
            "current_step_index": 0,
        }
        overlay.update_mission_animations(0.016)
        self.assertEqual(overlay._mission_intro_generation, 1)
        self.assertEqual(overlay._mission_intro_phase, 0.0)
        overlay.update_mission_animations(0.10)
        self.assertGreater(overlay._mission_intro_phase, 0.0)

    def test_changing_mission_restarts_intro_sequence(self):
        overlay = mission_hud.MasterOverlay()
        overlay.mission_active = True
        overlay.mission_slot = "P1-C1"
        overlay.mission_overlay_data = {
            "active_mission_id": "mission-a",
            "active_mission_steps": self._steps(),
            "completed_step_count": 0,
            "current_step_index": 0,
        }
        overlay.update_mission_animations(0.016)
        overlay.update_mission_animations(0.10)
        overlay.mission_overlay_data["active_mission_id"] = "mission-b"
        overlay.update_mission_animations(0.016)
        self.assertEqual(overlay._mission_intro_generation, 2)
        self.assertEqual(overlay._mission_intro_phase, 0.0)

    def test_disabling_mission_resets_intro_state(self):
        overlay = mission_hud.MasterOverlay()
        overlay.mission_active = False
        overlay._mission_intro_phase = 1.0
        overlay._mission_intro_was_active = True
        overlay.update_mission_animations(0.016)
        self.assertFalse(overlay._mission_intro_was_active)
        self.assertEqual(overlay._mission_intro_phase, 0.0)

    def test_rendered_panel_is_wider_and_buttons_stay_inside(self):
        overlay = self._draw_panel()
        self.assertIsNotNone(overlay.mission_panel_rect)
        self.assertGreaterEqual(overlay.mission_panel_rect.width, 560)
        self.assertLessEqual(overlay.mission_panel_rect.width, 820)
        self.assertTrue(overlay.mission_panel_rect.contains(overlay.mission_toggle_rect))
        self.assertTrue(overlay.mission_panel_rect.contains(overlay.mission_hint_rect))

    def test_intro_render_has_less_visible_content_than_settled_render(self):
        early = self._draw_panel(intro=0.18)
        settled = self._draw_panel(intro=1.0)
        early_alpha = pygame.surfarray.array_alpha(early.screen)
        settled_alpha = pygame.surfarray.array_alpha(settled.screen)
        self.assertLess(int(early_alpha.sum()), int(settled_alpha.sum()))

    def test_completed_row_render_retains_green_confirmation_pixels(self):
        overlay = self._draw_panel(completed=1, current=1)
        pixels = pygame.surfarray.array3d(overlay.screen)
        green_pixels = (pixels[:, :, 1] > pixels[:, :, 0] + 25) & (pixels[:, :, 1] > pixels[:, :, 2] + 5)
        self.assertGreater(int(green_pixels.sum()), 20)

    def test_completion_graphic_is_centered_and_angular(self):
        source = function_source(MASTER, "draw_celebration")
        self.assertIn('title_font.render("MISSION COMPLETE"', source)
        self.assertIn("plate_x = (self.w - plate_w) // 2", source)
        self.assertIn("body = [", source)
        self.assertIn("Side brackets slide inward", source)

    def test_completion_graphic_uses_flat_low_gloss_palette(self):
        source = function_source(MASTER, "draw_celebration")
        self.assertIn("(9, 18, 34", source)
        self.assertIn("(48, 67, 98", source)
        self.assertNotIn("ring_radius", source)
        self.assertNotIn("draw_lightning", source)

    def test_completion_graphic_has_one_additive_sheen_pass(self):
        source = function_source(MASTER, "draw_celebration")
        self.assertIn("One explicit sheen pass", source)
        self.assertIn("pygame.BLEND_RGBA_ADD", source)

    def test_completion_particles_do_not_use_old_gold_palette(self):
        source = function_source(MASTER, "_trigger_celebration")
        self.assertIn("palette = (", source)
        self.assertNotIn("(255, 225, 90)", source)
        self.assertNotIn("(255, 220, 60)", source)

    def test_mission_selector_still_marks_completed_challenges(self):
        source = function_source(MASTER, "draw_mission_overlay")
        self.assertIn('suffix = " [done]" if completed else ""', source)

    def test_primary_and_duplicate_master_renderers_match(self):
        self.assertEqual(read(MASTER), read(DUPLICATE))


if __name__ == "__main__":
    unittest.main()
