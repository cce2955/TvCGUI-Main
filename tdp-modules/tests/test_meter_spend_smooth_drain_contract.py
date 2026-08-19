from pathlib import Path

HUD = Path(__file__).resolve().parents[1] / "tvcgui" / "features" / "overlay" / "hud_renderer.py"


def _source() -> str:
    return HUD.read_text(encoding="utf-8")


def test_team_meter_gains_snap_but_spends_drain_smoothly():
    source = _source()
    assert 'if meter_target_f >= meter_display:' in source
    assert 'team_anim["meter_display_value"] = meter_target_f' in source
    assert 'team_anim["meter_drain_speed"] = max(60000.0, spend_amount / 0.18)' in source
    assert 'team_anim["meter_display_value"] = _approach(meter_display, meter_target_f, drain_speed, dt)' in source


def test_meter_drain_crosses_multiple_stocks_instead_of_popping_pips():
    source = _source()
    assert 'spend_amount = max(0.0, meter_display - meter_target_f)' in source
    assert 'meter_drain_target' in source
    assert 'meter_drain_speed' in source
