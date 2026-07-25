from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUD = ROOT / "tvcgui" / "features" / "overlay" / "hud_renderer.py"


def test_red_health_uses_existing_hp_bars_and_labels() -> None:
    text = HUD.read_text(encoding="utf-8")
    assert "if show_red_inline and point_recoverable > 0" in text
    assert "if show_red_inline and partner_recoverable > 0" in text
    assert "if show_red_inline else None" in text
    assert "recoverable_color = (180, 58, 82)" in text


def test_meter_generation_uses_existing_meter_header() -> None:
    text = HUD.read_text(encoding="utf-8")
    assert 'meter_profile_delta = _panel_int(point.get("meter_profile_last_delta"), 0)' in text
    assert "if show_meter_inline and meter_profile_delta" in text
    assert 'font_sm.render(f"{meter_profile_delta:+d}"' in text
    assert 'meter_match = point.get("meter_profile_last_match")' in text


def test_damage_scaling_draws_c1_and_c2_deviation_gauges_inside_info_column() -> None:
    text = HUD.read_text(encoding="utf-8")
    assert "def _draw_compact_damage_scaling_rows" in text
    assert "damage_scale_y" in text
    assert "damage_scale_layout_extra" in text
    assert "base_x = gauge_x + gauge_w // 2" in text
    assert "percent / 200.0" in text
    assert "info_right," in text
    assert 'value_text = f"{percent:.1f}%"' in text


def test_only_attack_properties_adds_research_history_row() -> None:
    text = HUD.read_text(encoding="utf-8")
    assert 'research_mode = "attack" if show_attack_inline else None' in text
    assert "if show_attack_inline:" in text
    assert '            "attack",' in text


def test_core_defaults_meter_and_red_health_on_with_damage_scaling_off() -> None:
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    master = (ROOT / "tvcgui" / "features" / "overlay" / "master_renderer.py").read_text(encoding="utf-8")
    assert "show_damage_badge     = False" in main
    assert "show_meter_panel      = True" in main
    assert "show_red_health_panel = True" in main
    assert '"native_hud_defaults_v": 3' in main
    assert "defaults_version < 3" in main
    assert "show_damage_badge: bool = False" in master
    assert "show_meter_panel: bool = True" in master
    assert "show_red_health_panel: bool = True" in master
