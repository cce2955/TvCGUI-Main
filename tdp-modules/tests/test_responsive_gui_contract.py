from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
COMPONENTS = ROOT / "tvcgui" / "ui" / "components.py"
CANCEL_LAB = ROOT / "tvcgui" / "features" / "frame_data" / "cancel_lab.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_redesigned_modules_parse() -> None:
    for path in (MAIN, COMPONENTS, CANCEL_LAB):
        ast.parse(_text(path), filename=str(path))


def test_main_gui_uses_dynamic_dock_height() -> None:
    main = _text(MAIN)
    components = _text(COMPONENTS)
    assert "get_top_dock_height" in main
    assert "top_ui_reserved = get_top_dock_height(w, bool(dock_tools_open))" in main
    assert "layout = compute_layout(w, max(240, h - top_ui_reserved), snaps)" in main
    assert "value.y += top_ui_reserved" in main
    assert "def _layout_command_section" in components
    assert "def _command_dock_layout" in components
    assert "def get_top_dock_height" in components


def test_resize_rebuilds_fonts_and_tabs_fit_width() -> None:
    main = _text(MAIN)
    components = _text(COMPONENTS)
    assert "def _responsive_fonts" in main
    assert main.count("font, smallfont = _responsive_fonts(screen.get_size())") >= 3
    assert "total_natural > available" in components
    assert "shared = max(58" in components


def test_cancel_lab_has_dark_responsive_layout() -> None:
    lab = _text(CANCEL_LAB)
    assert "CancelLab.Root.TFrame" in lab
    assert "CancelLab.Hero.TFrame" in lab
    assert "def _apply_responsive_layout" in lab
    assert 'mode = "wide" if width >= 900 else "stacked"' in lab
    assert "def _reflow_action_buttons" in lab
    assert "LIVE MONITOR" in lab
    assert "SESSION LOG" in lab
    assert "Save window to Frame Data" in lab


def test_modified_files_do_not_add_em_dashes() -> None:
    for path in (MAIN, COMPONENTS, CANCEL_LAB):
        assert "—" not in _text(path)
