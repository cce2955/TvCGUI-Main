from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MASTER = (ROOT / "tvcgui/features/overlay/master_renderer.py").read_text(encoding="utf-8")
HUD = (ROOT / "tvcgui/features/overlay/hud_renderer.py").read_text(encoding="utf-8")
COMP = (ROOT / "tvcgui/ui/components.py").read_text(encoding="utf-8")


def test_damage_and_hitstun_default_to_core_on():
    assert "show_damage_badge: bool = True" in MASTER
    assert "show_untech_panel: bool = True" in MASTER
    assert 'if self.control.hud_info_set == "CORE":' in MASTER
    assert "self.control.show_damage_badge = True" in MASTER
    assert "self.control.show_untech_panel = True" in MASTER


def test_compact_renderer_guarantees_core_telemetry():
    assert 'core_telemetry = hud_info_set == "CORE"' in HUD
    assert "show_damage_inline = bool(core_telemetry" in HUD
    assert "show_untech_inline = bool(core_telemetry" in HUD


def test_gui_names_core_components_explicitly():
    assert '"Damage: ON" if show_damage_badge else "Damage: OFF"' in COMP
    assert '"Hitstun: ON" if show_untech_panel else "Hitstun: OFF"' in COMP
