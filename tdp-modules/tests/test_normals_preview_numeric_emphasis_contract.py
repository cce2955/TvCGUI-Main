from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREVIEW = ROOT / "tvcgui" / "ui" / "normal_preview.py"


def test_preview_file_parses() -> None:
    ast.parse(PREVIEW.read_text(encoding="utf-8"), filename=str(PREVIEW))


def test_numeric_values_use_bold_larger_type() -> None:
    text = PREVIEW.read_text(encoding="utf-8")
    assert "numeric_font_size = 13 if row_h >= 16 else (12 if row_h >= 13 else 11)" in text
    assert "data_row_font = _preview_font(numeric_font_size, bold=True)" in text


def test_metric_colors_and_lanes_support_scanning() -> None:
    text = PREVIEW.read_text(encoding="utf-8")
    assert "metric_value_colors = (" in text
    assert "metric_lane_colors = (" in text
    assert "lane_alpha = 13 if lane_i in {0, 4, 5} else 6" in text


def test_key_values_receive_restrained_emphasis() -> None:
    text = PREVIEW.read_text(encoding="utf-8")
    assert "fastest_startup = min(startup_candidates)" in text
    assert "highest_damage = max(damage_candidates)" in text
    assert "key_value = int(startup) == int(fastest_startup)" in text
    assert "key_value = int(damage) == int(highest_damage)" in text
    assert "key_value = int(adv_block) > 0" in text


def test_four_card_layout_and_right_alignment_remain() -> None:
    text = PREVIEW.read_text(encoding="utf-8")
    assert "columns = 4" in text
    assert "value_x = col_left + col_w - val_s.get_width() - cell_pad" in text


def test_no_em_dashes() -> None:
    assert "—" not in PREVIEW.read_text(encoding="utf-8")
