from __future__ import annotations

import unittest

from tests.v19_contract_helpers import function_source, read

HUD = "tvcgui/features/overlay/hud_renderer.py"
MASTER = "tvcgui/features/overlay/master_renderer.py"
COMPONENTS = "tvcgui/ui/components.py"
MAIN = "main.py"
MISSION = "tvcgui/features/training/mission_manager.py"


class V19HudFeatureContractTests(unittest.TestCase):
    def test_full_health_numbers_are_rendered(self):
        self.assertIn('hp_str   = f"{int(hp_cur)}/{int(hp_max)}"', read(HUD))

    def test_primary_health_path_does_not_use_compact_short_number(self):
        source = function_source(HUD, "_draw_slot_row")
        self.assertNotIn("_compact_short_number", source)

    def test_four_visible_fighter_slots_are_preserved(self):
        source = read("tvcgui/core/constants.py")
        for label in ("P1-C1", "P1-C2", "P2-C1", "P2-C2"):
            self.assertIn(label, source)

    def test_baroque_badge_accepts_owner_label(self):
        source = function_source(HUD, "_draw_compact_baroque_badge")
        self.assertIn("owner_label", source)

    def test_baroque_badge_displays_owner(self):
        source = function_source(HUD, "_draw_compact_baroque_badge")
        self.assertIn('badge_label = f"{owner} BBQ" if owner else "BBQ"', source)

    def test_baroque_compact_badge_uses_two_decimals(self):
        source = function_source(HUD, "_draw_compact_baroque_badge")
        self.assertIn("{pct:.2f}%", source)

    def test_baroque_entry_and_exit_animation_state_exists(self):
        source = function_source(HUD, "_update_compact_baroque_anim")
        self.assertIn('slot_anim["baroque_fade_direction"] = 1', source)
        self.assertIn('slot_anim["baroque_fade_direction"] = -1', source)

    def test_baroque_wipe_uses_rainbow_color(self):
        source = function_source(HUD, "_draw_compact_baroque_badge")
        self.assertIn("_compact_rainbow_color", source)
        self.assertIn("wipe_alpha", source)

    def test_baroque_event_chips_remain_rainbow(self):
        self.assertIn('"rainbow": bool(item.get("rainbow", False) or label.upper() == "BBQ")', read(HUD))

    def test_input_history_state_exists(self):
        self.assertIn('"input_history": []', read(HUD))

    def test_input_history_keeps_recent_entries(self):
        self.assertIn("del input_history[:-12]", read(HUD))

    def test_input_history_renderer_exists(self):
        self.assertIn("def _draw_compact_input_history", read(HUD))

    def test_input_history_is_drawn_in_team_panel(self):
        self.assertIn("_draw_compact_input_history(", read(HUD))

    def test_direction_hold_specs_exist(self):
        self.assertIn("_DIRECTION_HOLD_SPECS", read(HUD))

    def test_button_hold_state_exists(self):
        self.assertIn('"button_hold_active": {}', read(HUD))

    def test_charge_tokens_support_hold(self):
        self.assertIn("HOLD", read(MASTER))

    def test_charge_tokens_support_charge(self):
        self.assertIn("CHARGE", read(MASTER))

    def test_charged_direction_icon_exists(self):
        source = function_source(MASTER, "_mission_direction_icon")
        self.assertIn("charged", source)

    def test_mission_mode_title_is_preserved(self):
        self.assertIn("Mission Mode", read(MASTER))

    def test_mission_manager_is_still_integrated(self):
        self.assertIn("MissionManager", read(MAIN))
        self.assertIn("mission_mgr", read(MAIN))

    def test_mission_manager_file_is_substantial(self):
        self.assertGreater(len(read(MISSION).splitlines()), 1500)

    def test_hitbox_master_toggle_is_preserved(self):
        self.assertIn('"Hitboxes: ON" if hb_on else "Hitboxes: OFF"', read(COMPONENTS))

    def test_hurtbox_master_toggle_is_preserved(self):
        self.assertIn('"Hurtboxes: ON" if hurt_on else "Hurtboxes: OFF"', read(COMPONENTS))

    def test_hitbox_slot_controls_are_preserved(self):
        self.assertIn("hitbox_slots", read(COMPONENTS))

    def test_hurtbox_slot_controls_are_preserved(self):
        self.assertIn("hurtbox_slots", read(COMPONENTS))

    def test_horizontal_ruler_control_is_preserved(self):
        self.assertIn("Horizontal", read(COMPONENTS))

    def test_vertical_ruler_control_is_preserved(self):
        self.assertIn("Vertical", read(COMPONENTS))

    def test_stage_control_button_is_preserved(self):
        self.assertIn('"Stage Control"', read(COMPONENTS))

    def test_normals_preview_tab_is_preserved(self):
        self.assertIn('("scan", "Normals Preview", GUI_APP_ACCENT)', read(COMPONENTS))

    def test_advantage_tab_is_preserved(self):
        self.assertIn('("advantage", "Advantage", GUI_APP_ACCENT)', read(COMPONENTS))

    def test_frame_data_preview_profiles_are_preserved(self):
        self.assertIn("frame_data_preview_profiles.json", read("tvcgui/tools/scanners/normal_scanner.py"))

    def test_edge_column_particle_state_exists(self):
        source = read(HUD)
        self.assertIn("_team_panel_fx_columns", source)
        self.assertIn('"column": random.choice(("left", "right"))', source)
        self.assertIn('team_anim.setdefault("sparks", [])', source)

    def test_v19_glass_button_language_is_preserved(self):
        self.assertIn("draw_glass_button", read(COMPONENTS))

    def test_v19_gradient_panel_language_is_preserved(self):
        self.assertIn("_draw_vertical_gradient", read(COMPONENTS))

    def test_interaction_ribbon_tracks_animation_age(self):
        source = read(HUD)
        self.assertIn('"age": 0.0', source)
        self.assertIn('_interaction_ribbon["age"] = age', source)

    def test_interaction_publish_restarts_animation_sequence(self):
        source = function_source(HUD, "_publish_interaction")
        self.assertIn('"life": 1.0', source)
        self.assertIn('"age": 0.0', source)

    def test_interaction_panel_slides_into_position(self):
        source = function_source(HUD, "_draw_live_interaction_ribbon")
        self.assertIn('panel_progress = _compact_smoothstep(age / 0.22)', source)
        self.assertIn('(1.0 - panel_progress) * -34 * scale', source)
        self.assertIn('(1.0 - panel_progress) * -10 * scale', source)

    def test_interaction_accent_slice_has_delayed_reveal(self):
        source = function_source(HUD, "_draw_live_interaction_ribbon")
        self.assertIn('slice_progress = _compact_smoothstep((age - 0.12) / 0.18)', source)
        self.assertIn('slice_width = max(1, int(width * 0.40 * slice_progress))', source)
        self.assertIn('card.set_clip(pygame.Rect(0, 0, slice_width, height))', source)

    def test_interaction_title_separator_is_animated_separately(self):
        source = function_source(HUD, "_draw_live_interaction_ribbon")
        self.assertIn('title.partition("  |  ")', source)
        self.assertIn('divider_progress = _compact_smoothstep((age - 0.27) / 0.16)', source)
        self.assertIn('separator_h = max(0, int(separator_target_h * divider_progress))', source)

    def test_interaction_right_title_waits_for_divider(self):
        source = function_source(HUD, "_draw_live_interaction_ribbon")
        self.assertIn('title_right_s.set_alpha(int(255 * fade * divider_progress))', source)
        self.assertIn('(1.0 - divider_progress) * 8 * scale', source)

    def test_interaction_detail_waits_for_divider(self):
        source = function_source(HUD, "_draw_live_interaction_ribbon")
        self.assertIn('detail_s.set_alpha(int(240 * fade * divider_progress))', source)
        self.assertIn('(1.0 - divider_progress) * 6 * scale', source)

    def test_interaction_sheen_runs_after_divider(self):
        source = function_source(HUD, "_draw_live_interaction_ribbon")
        self.assertIn('sheen_progress = max(0.0, min(1.0, (age - 0.43) / 0.34))', source)
        self.assertIn('math.sin(math.pi * sheen_progress)', source)
        self.assertIn('special_flags=pygame.BLEND_RGBA_ADD', source)

    def test_interaction_animation_stages_are_ordered(self):
        source = function_source(HUD, "_draw_live_interaction_ribbon")
        panel = source.index('panel_progress =')
        accent_slice = source.index('slice_progress =')
        divider = source.index('divider_progress =')
        sheen = source.index('sheen_progress =')
        self.assertLess(panel, accent_slice)
        self.assertLess(accent_slice, divider)
        self.assertLess(divider, sheen)


if __name__ == "__main__":
    unittest.main()
