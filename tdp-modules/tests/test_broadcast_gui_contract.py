from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
COMPONENTS = ROOT / "tvcgui" / "ui" / "components.py"
NORMALS = ROOT / "tvcgui" / "ui" / "normal_preview.py"
LAYOUT = ROOT / "tvcgui" / "core" / "layout.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_broadcast_files_parse() -> None:
    for path in (MAIN, COMPONENTS, NORMALS, LAYOUT):
        ast.parse(_text(path), filename=str(path))


def test_normals_preview_has_broadcast_header_and_readable_rows() -> None:
    src = _text(NORMALS)
    assert '"NORMALS PREVIEW"' in src
    assert '"LIVE FRAME DATA"' in src
    assert "header_accents =" in src
    assert "row_h = max(10" in src
    assert "columns = 4" in src
    assert "columns = 2" in src
    assert "card_y = top + grid_row" in src


def test_main_layout_reserves_lower_workspace() -> None:
    src = _text(LAYOUT)
    assert "desired_workspace_h = max(330" in src
    assert '"scan": workspace_rect.copy()' in src
    assert '"events": workspace_rect.copy()' in src
    assert "panel_h = max(126" in src


def test_main_window_uses_broadcast_palette() -> None:
    components = _text(COMPONENTS)
    main = _text(MAIN)
    assert "GUI_APP_ACCENT = (45, 194, 255)" in components
    assert '("scan", "NORMALS", GUI_APP_ACCENT)' in components
    assert "Broadcast keyline and corner marker" in components
    assert "_broadcast_y" in main


def test_changed_files_add_no_em_dash() -> None:
    for path in (MAIN, COMPONENTS, NORMALS, LAYOUT):
        assert "—" not in _text(path)
