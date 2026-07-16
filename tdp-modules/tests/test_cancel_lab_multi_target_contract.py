from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path

from tvcgui.features.frame_data.cancel_lab import CancelLabWindow


ROOT = Path(__file__).resolve().parents[1]


class _Var:
    def __init__(self, value: str):
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


def _fake_lab(source_id: int = 0x102, target_id: int = 0x100):
    lab = CancelLabWindow.__new__(CancelLabWindow)
    lab.window = None
    lab.target_rules = []
    lab.source_var = _Var("source")
    lab.target_var = _Var("target")
    lab.move_by_label = {
        "source": {"id": source_id},
        "target": {"id": target_id},
        "other": {"id": 0x101},
    }
    lab.moves = list(lab.move_by_label.values())
    lab.char_name = "Test"
    lab.last_result = ""
    lab._selected_move = lambda var: lab.move_by_label.get(var.get())
    lab._label_for_action_id = lambda action_id: next(
        (label for label, move in lab.move_by_label.items() if int(move["id"]) == int(action_id)),
        "",
    )
    lab._target_kind_for_id = lambda action_id: "normal"
    lab._refresh_target_tree = lambda select_target_id=None: None
    lab._announce = lambda text: setattr(lab, "last_result", text)
    lab._log = lambda text: None
    return lab


def test_target_list_adds_multiple_unique_routes():
    lab = _fake_lab()
    assert lab.add_target_rule(announce=False)
    lab.target_var.set("other")
    assert lab.add_target_rule(announce=False)
    assert lab._target_rule_ids() == [0x100, 0x101]
    assert not lab.add_target_rule(announce=False)
    assert lab._target_rule_ids() == [0x100, 0x101]


def test_source_cannot_be_added_as_its_own_target():
    lab = _fake_lab()
    lab.target_var.set("source")
    assert not lab.add_target_rule(announce=False)
    assert lab.target_rules == []


def test_runtime_arms_all_manual_targets_but_limits_auto_probe():
    text = (ROOT / "tvcgui" / "features" / "frame_data" / "cancel_lab.py").read_text(encoding="utf-8")
    assert "self.armed_targets = tuple(dict(rule) for rule in self.target_rules)" in text
    assert 'for rule in target_rules:' in text
    assert 'requesting one of {len(target_rules)} armed manual cancels' in text
    assert 'Auto timing probe requires exactly one target' in text
    assert 'text="Add target"' in text
    assert 'text="Remove selected"' in text
    assert 'text="Clear targets"' in text


def test_accepted_route_saves_the_actual_consumed_target():
    text = (ROOT / "tvcgui" / "features" / "frame_data" / "cancel_lab.py").read_text(encoding="utf-8")
    assert "save_window_to_profile(automatic=True, tested_target_id=target_id)" in text
    assert "tested_target_id: int | None = None" in text
