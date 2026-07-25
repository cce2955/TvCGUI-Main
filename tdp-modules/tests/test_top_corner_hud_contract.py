from pathlib import Path


def test_compact_team_panels_are_flush_with_top_corners() -> None:
    source = Path(__file__).parents[1] / "tvcgui/features/overlay/hud_renderer.py"
    text = source.read_text(encoding="utf-8")
    start = text.index("def _draw_compact_team_panel")
    end = text.index("def draw_overlay", start)
    block = text[start:end]

    assert "margin_x = 0" in block
    assert "base_y = 0" in block
    assert "base_x = 0 if is_left else screen.get_width() - width" in block
    assert "slide_x" in block
