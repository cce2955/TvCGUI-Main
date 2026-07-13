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


class V19ComboSheenContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.font.init()

    def setUp(self):
        self.old_frame = hud._frame
        self.old_ledgers = hud._combo_ledgers
        hud._frame = 100
        hud._combo_ledgers = {"P1": {}, "P2": {}}

    def tearDown(self):
        hud._frame = self.old_frame
        hud._combo_ledgers = self.old_ledgers

    def _register_p1_hit(self, damage: int = 1000) -> dict:
        with (
            mock.patch.object(hud, "_get_active_slot", return_value="P1-C1"),
            mock.patch.object(hud, "_snap_name", side_effect=lambda slot: "Ryu" if slot == "P1-C1" else "Alex"),
            mock.patch.object(hud, "_snap_int", return_value=50000),
            mock.patch.object(hud, "_snap_float", return_value=0.0),
        ):
            hud._combo_register_damage("P2-C1", damage)
        return hud._combo_ledgers["P1"]

    def test_combo_state_tracks_per_hit_sheen(self):
        source = function_source(HUD, "_combo_register_damage")
        self.assertIn('"hit_sheen": 0.0', source)
        self.assertIn('ledger["hit_sheen"] = 1.0', source)

    def test_combo_state_tracks_final_confirmation_sheen(self):
        source = function_source(HUD, "_combo_register_damage")
        self.assertIn('"final_sheen": 0.0', source)
        self.assertIn('"final_confirmed": False', source)

    def test_each_hit_cancels_pending_final_confirmation(self):
        source = function_source(HUD, "_combo_register_damage")
        self.assertIn('ledger["final_sheen"] = 0.0', source)
        self.assertIn('ledger["final_confirmed"] = False', source)

    def test_register_damage_restarts_subtle_sheen(self):
        ledger = self._register_p1_hit()
        self.assertEqual(ledger["hit_sheen"], 1.0)
        self.assertFalse(ledger["final_confirmed"])
        self.assertEqual(ledger["final_sheen"], 0.0)

    def test_later_hit_restarts_subtle_sheen_and_clears_final(self):
        ledger = self._register_p1_hit()
        ledger["hit_sheen"] = 0.15
        ledger["final_confirmed"] = True
        ledger["final_sheen"] = 0.7
        hud._frame += 10
        ledger = self._register_p1_hit(750)
        self.assertEqual(ledger["hits"], 2)
        self.assertEqual(ledger["hit_sheen"], 1.0)
        self.assertFalse(ledger["final_confirmed"])
        self.assertEqual(ledger["final_sheen"], 0.0)

    def test_subtle_sheen_decays_quickly(self):
        ledger = self._register_p1_hit()
        hud._tick_combo_ledgers(0.1)
        self.assertAlmostEqual(ledger["hit_sheen"], 0.5, places=5)

    def test_combo_timeout_starts_heavy_confirmation(self):
        ledger = self._register_p1_hit()
        hud._frame = int(ledger["last_hit_frame"]) + 76
        hud._tick_combo_ledgers(0.0)
        self.assertTrue(ledger["final_confirmed"])
        self.assertEqual(ledger["final_sheen"], 1.0)

    def test_heavy_confirmation_holds_full_life(self):
        ledger = self._register_p1_hit()
        hud._frame = int(ledger["last_hit_frame"]) + 76
        hud._tick_combo_ledgers(0.1)
        self.assertTrue(ledger["final_confirmed"])
        self.assertGreater(ledger["final_sheen"], 0.0)
        self.assertEqual(ledger["life"], 1.0)

    def test_combo_fades_only_after_heavy_sheen_finishes(self):
        ledger = self._register_p1_hit()
        ledger["final_confirmed"] = True
        ledger["final_sheen"] = 0.0
        hud._frame = int(ledger["last_hit_frame"]) + 100
        hud._tick_combo_ledgers(0.1)
        self.assertLess(ledger["life"], 1.0)

    def test_draw_path_uses_subtle_sheen_for_each_hit(self):
        source = function_source(HUD, "_draw_combo_ledger")
        self.assertIn('hit_sheen = float(ledger.get("hit_sheen") or 0.0)', source)
        self.assertIn('_blit_combo_sheen(card, 1.0 - hit_sheen, fade, heavy=False)', source)

    def test_draw_path_uses_heavy_sheen_for_final_confirmation(self):
        source = function_source(HUD, "_draw_combo_ledger")
        self.assertIn('final_sheen = float(ledger.get("final_sheen") or 0.0)', source)
        self.assertIn('_blit_combo_sheen(card, 1.0 - final_sheen, fade, heavy=True)', source)

    def test_subtle_sheen_is_intentionally_low_alpha(self):
        source = function_source(HUD, "_blit_combo_sheen")
        self.assertIn('92 if heavy else 24', source)
        self.assertIn('148 if heavy else 38', source)
        self.assertIn('0.24 if heavy else 0.105', source)
        self.assertIn('sheen_rgb = 118 if heavy else 28', source)
        self.assertIn('core_rgb = 220 if heavy else 72', source)

    def test_heavy_sheen_has_border_confirmation(self):
        source = function_source(HUD, "_blit_combo_sheen")
        self.assertIn('if heavy:', source)
        self.assertIn('border_alpha = int(68 * fade * envelope)', source)
        self.assertIn('pygame.draw.rect(', source)

    def test_sheen_uses_additive_polish_blend(self):
        source = function_source(HUD, "_blit_combo_sheen")
        self.assertIn('special_flags=pygame.BLEND_RGBA_ADD', source)

    def test_heavy_sheen_is_visibly_stronger_than_subtle_sheen(self):
        subtle = pygame.Surface((220, 32), pygame.SRCALPHA)
        heavy = pygame.Surface((220, 32), pygame.SRCALPHA)
        hud._blit_combo_sheen(subtle, 0.5, 1.0, heavy=False)
        hud._blit_combo_sheen(heavy, 0.5, 1.0, heavy=True)
        subtle_energy = int(pygame.surfarray.array3d(subtle).sum()) + int(pygame.surfarray.array_alpha(subtle).sum())
        heavy_energy = int(pygame.surfarray.array3d(heavy).sum()) + int(pygame.surfarray.array_alpha(heavy).sum())
        self.assertGreater(heavy_energy, subtle_energy)

    def test_combo_card_renders_with_both_sheen_states(self):
        font = pygame.font.Font(None, 16)
        base = {
            "attacker_slot": "P1-C1",
            "victim_slot": "P2-C1",
            "hits": 4,
            "damage": 12345,
            "meter_start": 50000,
            "baroque_start": 0.0,
            "life": 1.0,
            "last_hit_frame": hud._frame,
        }
        with (
            mock.patch.object(hud, "_snap_int", return_value=52000),
            mock.patch.object(hud, "_snap_float", return_value=0.0),
        ):
            subtle_screen = pygame.Surface((400, 100), pygame.SRCALPHA)
            hud._combo_ledgers["P1"] = {**base, "hit_sheen": 0.5, "final_sheen": 0.0}
            hud._draw_combo_ledger(subtle_screen, font, "P1", 10, 10, 300, 1.0, True)

            heavy_screen = pygame.Surface((400, 100), pygame.SRCALPHA)
            hud._combo_ledgers["P1"] = {**base, "hit_sheen": 0.0, "final_sheen": 0.5}
            hud._draw_combo_ledger(heavy_screen, font, "P1", 10, 10, 300, 1.0, True)

        self.assertGreater(pygame.mask.from_surface(subtle_screen).count(), 0)
        self.assertGreater(pygame.mask.from_surface(heavy_screen).count(), 0)
        self.assertNotEqual(
            pygame.image.tostring(subtle_screen, "RGBA"),
            pygame.image.tostring(heavy_screen, "RGBA"),
        )

    def test_duplicate_renderer_contains_same_combo_sheen_contract(self):
        primary = read(HUD)
        duplicate = read("tdp-modules/tvcgui/features/overlay/hud_renderer.py")
        self.assertEqual(primary, duplicate)


if __name__ == "__main__":
    unittest.main()
