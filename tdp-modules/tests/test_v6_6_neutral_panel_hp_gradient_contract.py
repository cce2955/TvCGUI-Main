from pathlib import Path

HUD = Path(__file__).resolve().parents[1] / "tvcgui" / "features" / "overlay" / "hud_renderer.py"


def _source() -> str:
    return HUD.read_text(encoding="utf-8")


def test_panel_shell_no_longer_applies_team_color_splash():
    source = _source()
    shell = source[source.index("def _cached_compact_panel_shell("):source.index("def _compact_baroque_inline_width", source.index("def _cached_compact_panel_shell("))]
    assert "_draw_compact_broadcast_splash(panel" not in shell
    assert "neutral black/charcoal" in shell


def test_hp_uses_fixed_position_color_map():
    source = _source()
    assert "def _health_bar_color_at(" in source
    assert "if p <= 0.10:" in source
    assert "if p <= 0.25:" in source
    assert "if p <= 0.40:" in source
    assert "return _lerp_color(red, yellow, (p - 0.10) / 0.15)" in source
    assert "return _lerp_color(yellow, green, (p - 0.25) / 0.15)" in source
    assert "return green" in source


def test_hp_fill_draws_horizontal_gradient_instead_of_threshold_swap():
    source = _source()
    health = source[source.index("def _draw_compact_health("):source.index("def _compact_short_number", source.index("def _draw_compact_health("))]
    assert "_draw_horizontal_health_gradient(screen, inner, fraction, is_dead)" in health
    assert "target_fraction <= 0.30" not in health
