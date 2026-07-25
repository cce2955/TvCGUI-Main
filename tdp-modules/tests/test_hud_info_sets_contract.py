from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
COMPONENTS = ROOT / "tvcgui" / "ui" / "components.py"
MASTER = ROOT / "tvcgui" / "features" / "overlay" / "master_renderer.py"
HUD = ROOT / "tvcgui" / "features" / "overlay" / "hud_renderer.py"


def test_damage_scaling_has_visible_branding() -> None:
    text = HUD.read_text(encoding="utf-8")
    assert 'font_sm.render("DMG SCALE"' in text
    assert "damage_scale_header_h" in text


def test_core_is_default_and_attack_properties_start_off() -> None:
    main = MAIN.read_text(encoding="utf-8")
    master = MASTER.read_text(encoding="utf-8")
    assert "show_attack_property_panel = False" in main
    assert 'defaults_version < 3' in main
    assert '_apply_hud_info_set("CORE")' in main
    assert "show_attack_property_panel: bool = False" in master
    assert 'hud_info_set          = "CORE"' in main
    assert 'hud_info_set: str = "CORE"' in master
    assert '"native_hud_defaults_v": 3' in main


def test_core_research_and_full_presets_are_available() -> None:
    main = MAIN.read_text(encoding="utf-8")
    components = COMPONENTS.read_text(encoding="utf-8")
    assert 'if normalized == "CORE"' in main
    core_block = main.split('if normalized == "CORE":', 1)[1].split('elif normalized == "RESEARCH":', 1)[0]
    assert 'show_meter_panel = True' in core_block
    assert 'show_red_health_panel = True' in core_block
    assert 'show_damage_badge = False' in core_block
    assert 'show_attack_property_panel = False' in core_block
    assert 'elif normalized == "RESEARCH"' in main
    assert 'show_attack_property_panel = True' in main
    assert '("info_set_btn", 108)' in components
    assert 'f"Set: {info_set_name.title()}"' in components
    assert '"CUSTOM": "CORE"' in main


def test_manual_toggles_mark_set_custom() -> None:
    main = MAIN.read_text(encoding="utf-8")
    assert "def _mark_hud_info_set_custom" in main
    assert main.count("_mark_hud_info_set_custom()") >= 8

def test_set_button_is_left_of_overlay_button() -> None:
    components = COMPONENTS.read_text(encoding="utf-8")
    set_pos = components.index('("info_set_btn", 108)')
    overlay_pos = components.index('("hud_btn", 100)')
    assert set_pos < overlay_pos

