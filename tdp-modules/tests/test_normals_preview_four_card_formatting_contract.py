from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ROOT / "tvcgui" / "ui" / "normal_preview.py"


def test_preview_file_parses() -> None:
    ast.parse(PREVIEW.read_text(encoding="utf-8"), filename=str(PREVIEW))


def test_all_four_fighters_remain_visible() -> None:
    text = PREVIEW.read_text(encoding="utf-8")
    assert 'preview_layout = "wide"' in text
    assert 'render_slots = list(slots)' in text
    assert 'columns = 4' in text
    assert 'preview_layout = "team"' not in text
    assert 'preview_layout = "focus"' not in text


def test_table_hierarchy_and_alignment_are_intentional() -> None:
    text = PREVIEW.read_text(encoding="utf-8")
    assert 'header_x = header_cell.x + 7' in text
    assert 'value_x = col_left + col_w - val_s.get_width() - cell_pad' in text
    assert 'metric_weights = (0.13, 0.22, 0.14, 0.14, 0.16, 0.21)' in text
    assert 'name_x = card.x + 10 + slot_s.get_width() + 9' in text
    assert 'row_font_size = 12 if row_h >= 15 else (11 if row_h >= 12 else 10)' in text


def test_air_section_and_rows_are_quiet() -> None:
    text = PREVIEW.read_text(encoding="utf-8")
    assert 'air_font = _preview_font(9, bold=True)' in text
    assert 'row_fill = (8, 18, 29) if mi % 2 == 0 else (5, 13, 23)' in text


def test_no_em_dashes() -> None:
    assert "—" not in PREVIEW.read_text(encoding="utf-8")
