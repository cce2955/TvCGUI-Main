from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_main_launcher_opens_first_slot_without_picker():
    text = (ROOT / "tvcgui" / "features" / "frame_data" / "window.py").read_text(encoding="utf-8")
    assert 'slot_name(row) == "P1-C1"' in text
    assert "profiles=rows" in text
    assert "Choose the fighter profile to test" not in text
    assert "Open Cancel Lab\", command=launch" not in text


def test_cancel_lab_owns_slot_selector_and_reloads_moves():
    text = (ROOT / "tvcgui" / "features" / "frame_data" / "cancel_lab.py").read_text(encoding="utf-8")
    assert 'text="Fighter slot"' in text
    assert 'self.slot_combo.bind("<<ComboboxSelected>>", self._switch_slot_from_ui)' in text
    assert "self.profile_by_slot" in text
    assert "self.source_combo.configure(values=self.labels)" in text
    assert "self.target_combo.configure(values=self.labels)" in text
    assert "Cancel route disarmed before switching" in text


def test_open_cancel_lab_accepts_profiles_argument():
    module = ast.parse((ROOT / "tvcgui" / "features" / "frame_data" / "cancel_lab.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in ast.walk(module) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert "open_cancel_lab" in functions
    arg_names = [arg.arg for arg in functions["open_cancel_lab"].args.args]
    assert "profiles" in arg_names
