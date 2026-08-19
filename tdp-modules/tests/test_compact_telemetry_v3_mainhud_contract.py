from pathlib import Path

HUD = Path(__file__).resolve().parents[1] / "tvcgui" / "features" / "overlay" / "hud_renderer.py"


def _body(name: str, next_name: str) -> str:
    source = HUD.read_text(encoding="utf-8")
    start = source.index(f"def {name}(")
    end = source.index(f"\ndef {next_name}", start)
    return source[start:end]


def test_meter_is_integrated_into_main_hud_not_detached_power_sidebar():
    body = _body("_draw_compact_team_panel", "draw_overlay")
    assert "_draw_compact_meter_rail(" in body
    assert "meter_rail_y =" in body
    assert "power_left" not in body
    assert "separator_x = power_left" not in body


def test_bbq_is_character_owned_and_embedded_beside_each_health_row():
    body = _body("_draw_compact_team_panel", "draw_overlay")
    assert "point_bbq_w = _compact_baroque_inline_width" in body
    assert "partner_bbq_w = _compact_baroque_inline_width" in body
    assert "point_bq_rect = pygame.Rect(point_bbq_x, top_hp_y" in body
    assert "partner_bq_rect = pygame.Rect(partner_bbq_x, bottom_hp_y" in body


def test_panel_spends_extra_screen_width_on_main_hud_and_history():
    body = _body("_draw_compact_team_panel", "draw_overlay")
    assert "responsive_cap" in body
    assert "int(screen.get_width() * 0.36)" in body
    assert "width = max(base_width, responsive_cap)" in body


def test_health_and_meter_numbers_use_right_anchored_columns():
    source = HUD.read_text(encoding="utf-8")
    meter = _body("_draw_compact_meter_rail", "_draw_compact_guard_chip")
    team = _body("_draw_compact_team_panel", "draw_overlay")
    assert "cursor_right = rail.right - pad" in meter
    assert "stock_x = cursor_right - stock.get_width()" in meter
    assert "point_value_right - hp_text.get_width() - red_text_w" in team
    assert "partner_value_right - partner_hp_text.get_width() - partner_red_text_w" in team


def test_requested_metric_names_are_present():
    source = HUD.read_text(encoding="utf-8")
    assert 'font_sm.render("DMG SCALE"' in source
    assert 'hit_label = f"AIR HS -{loss}" if loss > 0 else "AIR HS"' in source


def test_idle_metrics_dim_without_hiding_values():
    scale = _body("_draw_compact_damage_scaling_rows", "_realtime_hs_contact_clock")
    stun = _body("_draw_compact_untech_scaling_row", "_research_dock_active_panel")
    assert "idle_tint" in scale
    assert "idle_tint" in stun
    assert 'font_sm.render("READY"' in stun


def test_guard_uses_spare_action_width_not_input_history_width():
    body = _body("_draw_compact_team_panel", "draw_overlay")
    assert "info_right = _draw_compact_guard_chip" in body
    # Diagnostic input history keeps full panel runway.
    call = body[body.index("_draw_compact_input_history("):body.index("if show_attack_inline:")]
    assert "        right," in call
