from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_components_use_calm_horizontal_sweeps():
    text = (ROOT / "tvcgui/ui/components.py").read_text(encoding="utf-8")
    assert "def _draw_horizontal_gradient" in text
    assert "One slim rail is enough" in text
    assert "for scan_y in range" not in text


def test_main_workspace_has_no_broadcast_scanline_wall():
    text = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "_draw_horizontal_gradient" in text
    assert "for _broadcast_y in range" not in text
    assert "moving scanline" not in text


def test_normals_preview_is_minimal_and_not_rainbow_headed():
    text = (ROOT / "tvcgui/ui/normal_preview.py").read_text(encoding="utf-8")
    assert "GUI_APP_ACCENT" in text
    assert "header_accents =" not in text
    assert "for card_scan_y in range" not in text
    assert "sweep_x = row.x" not in text
