from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
COMPONENTS = ROOT / "tvcgui" / "ui" / "components.py"
PREVIEW = ROOT / "tvcgui" / "ui" / "normal_preview.py"


def test_modified_files_parse() -> None:
    for path in (MAIN, COMPONENTS, PREVIEW):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_tools_drawer_uses_animated_progress() -> None:
    main = MAIN.read_text(encoding="utf-8")
    components = COMPONENTS.read_text(encoding="utf-8")
    assert "dock_tools_progress = 0.0" in main
    assert "dock_anim_dt" in main
    assert "tools_progress=dock_tools_progress" in main
    assert "def _dock_smoothstep" in components
    assert "tools_progress: float | None = None" in components
    assert "closed_height + (open_height - closed_height)" in components
    assert "screen.set_clip(dock_rect)" in components


def test_normals_preview_uses_proportional_ui_typography() -> None:
    text = PREVIEW.read_text(encoding="utf-8")
    assert 'pygame.font.SysFont("Segoe UI"' in text
    assert "move_row_font" in text
    assert "data_row_font" in text
    assert 'header_labels = ("Move",) + metric_headers' in text
    assert "value_x = col_left + col_w - val_s.get_width() - cell_pad" in text
    assert "full spreadsheet grid is unnecessary" in text


def test_no_em_dashes_added() -> None:
    for path in (MAIN, COMPONENTS, PREVIEW):
        assert "—" not in path.read_text(encoding="utf-8")
