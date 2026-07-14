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


class V19MissionStepSheenContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()

    @staticmethod
    def _metallic_row(width: int = 320, height: int = 42) -> pygame.Surface:
        surf = pygame.Surface((width, height), pygame.SRCALPHA)
        for strip_y in range(height):
            frac = strip_y / max(height - 1, 1)
            mid = max(0.0, min(1.0, 1.0 - abs(frac - 0.45) * 2.2))
            color = (
                int(18 + mid * 20),
                int(60 + mid * 60),
                int(110 + mid * 100),
                210,
            )
            pygame.draw.line(surf, color, (0, strip_y), (width, strip_y))
        return surf

    @staticmethod
    def _rgb_energy(surface: pygame.Surface, left: int, right: int) -> float:
        pixels = pygame.surfarray.array3d(surface)
        region = pixels[max(0, left):max(left + 1, right), :, :]
        return float(region.mean())

    def test_completion_wipe_helper_exists(self):
        source = function_source(MASTER, "_draw_mission_completion_wipe")
        self.assertIn("bright sheen and particle trail", source)

    def test_completion_ease_is_monotonic_and_clamped(self):
        self.assertEqual(mission_hud._mission_completion_ease(-2.0), 0.0)
        self.assertEqual(mission_hud._mission_completion_ease(2.0), 1.0)
        self.assertLess(
            mission_hud._mission_completion_ease(0.25),
            mission_hud._mission_completion_ease(0.75),
        )

    def test_zero_progress_leaves_row_unchanged(self):
        row = self._metallic_row()
        before = pygame.image.tostring(row, "RGBA")
        mission_hud._draw_mission_completion_wipe(row, 0.0, (90, 170, 255))
        self.assertEqual(before, pygame.image.tostring(row, "RGBA"))

    def test_mid_wipe_darkens_completed_side_first(self):
        row = self._metallic_row()
        mission_hud._draw_mission_completion_wipe(row, 0.45, (90, 170, 255))
        self.assertLess(self._rgb_energy(row, 20, 100), self._rgb_energy(row, 285, 315))

    def test_wipe_edge_has_an_explicit_bright_sheen(self):
        row = self._metallic_row()
        progress = 0.45
        mission_hud._draw_mission_completion_wipe(row, progress, (90, 170, 255))
        edge = int(round(row.get_width() * mission_hud._mission_completion_ease(progress)))
        edge_energy = self._rgb_energy(row, edge - 12, edge + 12)
        dark_energy = self._rgb_energy(row, edge - 150, edge - 110)
        self.assertGreater(edge_energy, dark_energy + 120.0)

    def test_sheen_is_deliberately_high_visibility(self):
        row = self._metallic_row()
        mission_hud._draw_mission_completion_wipe(row, 0.45, (90, 170, 255))
        pixels = pygame.surfarray.array3d(row)
        brightest_neutral = int(pixels.min(axis=2).max())
        self.assertGreaterEqual(brightest_neutral, 180)

    def test_particle_trail_brightens_area_behind_ridge(self):
        row = self._metallic_row()
        progress = 0.45
        mission_hud._draw_mission_completion_wipe(row, progress, (90, 170, 255))
        edge = int(round(row.get_width() * mission_hud._mission_completion_ease(progress)))
        trail_energy = self._rgb_energy(row, edge - 100, edge - 45)
        settled_dark_energy = self._rgb_energy(row, edge - 150, edge - 110)
        self.assertGreater(trail_energy, settled_dark_energy + 70.0)

    def test_particle_trail_uses_multiple_points(self):
        source = function_source(MASTER, "_draw_mission_completion_wipe")
        self.assertIn("particle_count = 11", source)
        self.assertIn("for particle_index in range(particle_count)", source)
        self.assertIn("pygame.draw.circle(polish", source)

    def test_particles_include_short_streaks(self):
        source = function_source(MASTER, "_draw_mission_completion_wipe")
        self.assertIn("streak = max(2, int(8 * life))", source)
        self.assertIn("(px - streak, py)", source)

    def test_particle_motion_is_bound_to_wipe_progress(self):
        source = function_source(MASTER, "_draw_mission_completion_wipe")
        self.assertIn("progress * 18.0", source)
        self.assertNotIn("time.time()", source)

    def test_broad_glow_precedes_bright_core(self):
        source = function_source(MASTER, "_draw_mission_completion_wipe")
        broad = source.index("Broad colored glow")
        core = source.index("core_w =")
        ridge = source.index("(210, 235, 255, 145)")
        self.assertLess(broad, core)
        self.assertLess(core, ridge)

    def test_settled_completion_is_fully_dark(self):
        row = self._metallic_row()
        mission_hud._draw_mission_completion_wipe(row, 1.0, (90, 170, 255))
        left = self._rgb_energy(row, 10, 80)
        right = self._rgb_energy(row, 240, 310)
        self.assertLess(abs(left - right), 2.0)
        self.assertLess(left, 35.0)

    def test_settled_completion_has_no_lingering_sheen_or_particles(self):
        almost = self._metallic_row()
        final = self._metallic_row()
        mission_hud._draw_mission_completion_wipe(almost, 0.995, (90, 170, 255))
        mission_hud._draw_mission_completion_wipe(final, 1.0, (90, 170, 255))
        self.assertEqual(pygame.image.tostring(almost, "RGBA"), pygame.image.tostring(final, "RGBA"))

    def test_wipe_uses_multiple_diagonal_polygons(self):
        source = function_source(MASTER, "_draw_mission_completion_wipe")
        self.assertGreaterEqual(source.count("pygame.draw.polygon"), 2)
        self.assertIn("slant =", source)

    def test_wipe_uses_additive_polish_blend(self):
        source = function_source(MASTER, "_draw_mission_completion_wipe")
        self.assertIn("pygame.BLEND_RGBA_ADD", source)

    def test_completed_row_keeps_metallic_base_under_wipe(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("The wipe darkens the row body", source)
        self.assertIn("_draw_mission_completion_wipe(", source)

    def test_completion_progress_follows_step_animation(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("1.0 - max(0.0, min(1.0, t))", source)

    def test_completed_text_dims_with_same_completion_ease(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertIn("move_color = (224, 231, 241) if not is_done else", source)
        self.assertLess(source.index("_draw_mission_completion_wipe("), source.index("row_layer.blit(done"))

    def test_old_rectangular_completion_sweep_is_removed(self):
        source = function_source(MASTER, "_draw_active_mission_panel")
        self.assertNotIn("sweep.fill((255, 255, 255, int(70 * sweep_t)))", source)

    def test_duplicate_master_renderer_matches_primary(self):
        self.assertEqual(read(MASTER), read(DUPLICATE))


if __name__ == "__main__":
    unittest.main()
