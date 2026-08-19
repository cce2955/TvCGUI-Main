from pathlib import Path

HUD = Path(__file__).resolve().parents[1] / "tvcgui" / "features" / "overlay" / "hud_renderer.py"


def _body(name: str, next_name: str) -> str:
    source = HUD.read_text(encoding="utf-8")
    start = source.index(f"def {name}(")
    end = source.index(f"\ndef {next_name}", start)
    return source[start:end]


def test_damage_scaling_is_one_shared_scale_rail_with_two_slot_cells():
    body = _body("_draw_compact_damage_scaling_rows", "_realtime_hs_contact_clock")
    assert 'font_sm.render("DMG SCALE"' in body
    assert 'active_indices = [i for i, item in enumerate(cells) if item["deviated"]]' in body
    assert 'compact_w = max(font_sm.size("C2 100.0%")' in body
    assert 'badge = "C1" if slot_label.endswith("C1") else "C2"' in body
    assert 'font_sm.render("SCALE"' not in body


def test_stun_is_one_shared_rail_with_hit_and_block_cells():
    body = _body("_draw_compact_untech_scaling_row", "_research_dock_active_panel")
    assert 'font_sm.render("STUN"' in body
    assert 'hit_active = bool(' in body
    assert 'block_active = block_remaining > 0' in body
    assert 'if hit_active and block_active:' in body
    assert 'if hit_active:' in body
    assert 'font_sm.render("STUN CLOCKS"' not in body


def test_panel_height_accounts_for_only_two_metric_rails():
    source = HUD.read_text(encoding="utf-8")
    assert 'damage_scale_height = compact_metric_rail_h' in source
    assert 'untech_scale_height = compact_metric_rail_h' in source
    assert 'damage_scale_row_h * 2' not in source
    assert 'untech_row_h * 2' not in source
