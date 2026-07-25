from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_native_research_values_integrate_into_compact_team_panels() -> None:
    text = _read("tvcgui/features/overlay/hud_renderer.py")
    assert "show_damage_inline" in text
    assert "show_meter_inline" in text
    assert "show_red_inline" in text
    assert "show_attack_inline" in text
    assert "def _draw_compact_damage_scaling_rows" in text
    assert "point_red_text" in text
    assert "meter_profile_delta" in text


def test_floating_dock_is_only_fallback_when_core_hud_is_hidden() -> None:
    text = _read("tvcgui/features/overlay/hud_renderer.py")
    block = text.split("def draw_overlay", 1)[1].split("class HudRenderer", 1)[0]
    assert "else:\n        _draw_research_panels" in block
    assert "_draw_compact_team_panel" in block


def test_research_buttons_are_independent_toggles() -> None:
    text = _read("main.py")
    assert "show_damage_badge = not show_damage_badge" in text
    assert "show_meter_panel = not show_meter_panel" in text
    assert "show_red_health_panel = not show_red_health_panel" in text
    assert "show_attack_property_panel = not show_attack_property_panel" in text
    assert "Research buttons select one integrated HUD data row" not in text


def test_old_multi_panel_config_migrates_to_core_set() -> None:
    text = _read("main.py")
    assert 'defaults_version < 3' in text
    assert '_apply_hud_info_set("CORE")' in text
    assert 'show_attack_property_panel = False' in text
    assert "if sum(1 for _flag in _research_flags if _flag) > 1" not in text
