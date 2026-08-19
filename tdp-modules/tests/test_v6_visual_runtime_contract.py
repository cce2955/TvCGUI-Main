from pathlib import Path

HUD = Path(__file__).resolve().parents[1] / "tvcgui" / "features" / "overlay" / "hud_renderer.py"


def _source() -> str:
    return HUD.read_text(encoding="utf-8")


def test_overlay_owns_vertical_gradient_helper_before_v6_calls():
    source = _source()
    definition = source.index("def _draw_vertical_gradient(")
    first_use = source.index("_draw_vertical_gradient(")
    assert definition < first_use
    assert "pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)" in source


def test_all_v6_gradient_consumers_use_overlay_local_helper():
    source = _source()
    assert source.count("def _draw_vertical_gradient(") == 1
    # Health, meter, DMG SCALE and STUN all use the same local primitive.
    assert source.count("_draw_vertical_gradient(") >= 10


def test_meter_rail_uses_team_resolved_value_not_only_point_snapshot():
    source = _source()
    assert "def _compact_team_meter_value(*snaps" in source
    assert "team_meter_target = _compact_team_meter_value(point, partner, slots.get(first_label), slots.get(second_label))" in source
    assert "meter_value=team_meter_target" in source
