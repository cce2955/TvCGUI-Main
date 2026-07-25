from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_all_four_research_modes_have_compact_tokens() -> None:
    text = _read("tvcgui/features/overlay/hud_renderer.py")
    for mode in ("damage", "meter", "red", "attack"):
        assert f'if mode == "{mode}"' in text
    for label in ("DMG MOD", "METER+", "RED HP", "ATK PROP"):
        assert label in text


def test_red_mode_uses_auxiliary_health_in_existing_bars() -> None:
    text = _read("tvcgui/features/overlay/hud_renderer.py")
    assert "point_auxiliary / max" in text
    assert "partner_auxiliary / max" in text
    assert "recoverable_color = (180, 58, 82)" in text


def test_research_row_expands_panel_instead_of_covering_match() -> None:
    text = _read("tvcgui/features/overlay/hud_renderer.py")
    assert "research_layout_extra = research_row_height + research_gap" in text
    assert "collapsed_height = max(154, int(166 * scale)) + research_layout_extra" in text
    assert "hold_y = research_y + research_layout_extra" in text
