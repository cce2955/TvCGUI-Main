from pathlib import Path

HUD = Path(__file__).resolve().parents[1] / "tvcgui" / "features" / "overlay" / "hud_renderer.py"


def _body(name: str, next_name: str) -> str:
    source = HUD.read_text(encoding="utf-8")
    start = source.index(f"def {name}(")
    end = source.index(f"\ndef {next_name}", start)
    return source[start:end]


def test_scale_neutral_state_collapses_to_text_without_gauges():
    body = _body("_draw_compact_damage_scaling_rows", "_realtime_hs_contact_clock")
    assert 'if not active_indices:' in body
    neutral = body[body.index('if not active_indices:'):body.index('avail = max(40, content_right - content_x)')]
    assert 'pygame.draw.line(screen, (55, 66, 82)' not in neutral
    assert 'value_s = font_sm.render(item["value_text"]' in neutral


def test_single_damage_scaler_gets_wide_lane_and_neutral_partner_collapses():
    body = _body("_draw_compact_damage_scaling_rows", "_realtime_hs_contact_clock")
    assert 'if len(active_indices) == 1' in body
    assert 'wide_w = max(24, avail - gap - compact_w)' in body
    assert 'if not item["deviated"] and len(active_indices) == 1:' in body


def test_stun_lane_is_adaptive_and_neutral_becomes_ready():
    body = _body("_draw_compact_untech_scaling_row", "_research_dock_active_panel")
    assert 'if not hit_active and not block_active:' in body
    assert 'font_sm.render("READY"' in body
    assert 'if hit_active and block_active:' in body
    assert 'if hit_active:' in body
    assert 'draw_clock_cell(block_cell, "BS", "--"' in body


def test_state_is_badge_inside_action_ribbon_not_a_state_label_chip():
    body = _body("_draw_compact_info_strip", "_render_compact_text_chip")
    assert 'if action_kind == "STATE":' in body
    assert '_render_compact_text_chip(font_sm, value, action_color' in body
    assert '_draw_compact_stat_chip(screen, font_sm, x, y, "STATE"' not in body


def test_move_is_reserved_left_and_transient_telemetry_packs_from_right():
    body = _body("_draw_compact_info_strip", "_render_compact_text_chip")
    assert 'action_cap = max(72, int(strip_width * 0.52))' in body
    assert 'event_right = right' in body
    assert 'chip_x = event_right - width' in body
    assert 'if chip_x < min_left:' in body


def test_diagnostic_history_pipeline_remains_intact():
    source = HUD.read_text(encoding="utf-8")
    assert '_draw_compact_input_history(' in source
    input_body = _body('_draw_compact_input_history', '_draw_compact_move_history')
    assert '_draw_history_header_chip(screen, font_sm, "FRAMES"' in input_body
    assert '_draw_compact_move_history(' in source
    assert 'input_history_signature' in source
    assert 'move_history_signature' in source
