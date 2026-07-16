from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
PREVIEW = ROOT / "tvcgui" / "ui" / "normal_preview.py"


def test_condensed_preview_files_parse() -> None:
    ast.parse(MAIN.read_text(encoding="utf-8"), filename=str(MAIN))
    ast.parse(PREVIEW.read_text(encoding="utf-8"), filename=str(PREVIEW))


def test_preview_has_four_responsive_modes() -> None:
    text = PREVIEW.read_text(encoding="utf-8")
    assert 'preview_layout = "wide"' in text
    assert 'preview_layout = "team"' in text
    assert 'preview_layout = "focus"' in text
    assert 'preview_layout = "essentials"' in text
    assert "_normal_preview_essential_choices" in text


def test_compact_preview_has_slot_and_view_controls() -> None:
    text = PREVIEW.read_text(encoding="utf-8")
    assert 'f"__slot__:{slot_label}"' in text
    assert 'f"__view__:{view_key}"' in text
    assert '"Full Table"' in text
    assert '"Essentials"' in text


def test_main_tracks_compact_preview_state() -> None:
    text = MAIN.read_text(encoding="utf-8")
    assert 'normal_preview_focus_slot = "P1-C1"' in text
    assert 'normal_preview_compact_view = "full"' in text
    assert "focus_slot=normal_preview_focus_slot" in text
    assert "compact_view=normal_preview_compact_view" in text
    assert 'startswith("__slot__:")' in text
    assert 'startswith("__view__:")' in text


def test_modified_files_do_not_add_em_dashes() -> None:
    assert "—" not in MAIN.read_text(encoding="utf-8")
    assert "—" not in PREVIEW.read_text(encoding="utf-8")
