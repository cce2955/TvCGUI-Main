from __future__ import annotations

from pathlib import Path

from tvcgui.features.frame_data import cancel_windows as FCW
from tvcgui.features.frame_data.cancel_lab import CancelLabWindow

ROOT = Path(__file__).resolve().parents[1]


def test_profile_storage_round_trip(tmp_path, monkeypatch):
    profile_path = tmp_path / "custom_cancel_windows.json"
    monkeypatch.setattr(FCW, "PROFILE_PATH", str(profile_path))
    monkeypatch.setattr(FCW, "_CACHE", None)
    monkeypatch.setattr(FCW, "_CACHE_MTIME", None)

    saved = FCW.set_cancel_profile_rule(
        "Ryu",
        0x102,
        8,
        30,
        [0x130, 0x135, 0x160, 0x130],
    )
    assert saved is not None
    rules = FCW.get_cancel_profile("Ryu")
    assert rules == [
        {
            "source_id": 0x102,
            "earliest": 8,
            "latest": 30,
            "targets": [0x130, 0x135, 0x160],
            "updated_at": saved["updated_at"],
            "source": "Live Cancel Lab",
        }
    ]

    FCW.set_cancel_profile_rule("Ryu", 0x100, 5, 0, [0x101])
    assert [row["source_id"] for row in FCW.get_cancel_profile("Ryu")] == [0x100, 0x102]
    assert FCW.remove_cancel_profile_rule("Ryu", 0x100)
    assert [row["source_id"] for row in FCW.get_cancel_profile("Ryu")] == [0x102]
    assert FCW.clear_cancel_profile("Ryu")
    assert FCW.get_cancel_profile("Ryu") == []


def test_profile_runtime_activates_the_matching_source_snapshot():
    lab = CancelLabWindow.__new__(CancelLabWindow)
    lab.armed_profile_rules = (
        {
            "source_id": 0x100,
            "earliest": 5,
            "latest": 18,
            "targets": ({"target_id": 0x101, "kind": "normal", "label": "5B"},),
        },
        {
            "source_id": 0x102,
            "earliest": 8,
            "latest": 0,
            "targets": (
                {"target_id": 0x130, "kind": "special", "label": "Hado L"},
                {"target_id": 0x160, "kind": "super", "label": "Shinkuu"},
            ),
        },
    )
    lab.was_in_source = False
    lab.request_pending = False
    lab.armed_source_id = 0
    lab.armed_targets = ()
    lab.armed_target_id = 0
    lab.armed_target_kind = "other"
    lab.armed_earliest = 0
    lab.armed_latest = 0
    lab.completed_for_source = True
    lab.pulses_this_source = 99
    lab._last_trigger_signatures = {1: ("old",)}
    lab._active_profile_source_id = 0
    lab._mark_active_profile_source = lambda source_id: setattr(lab, "_active_profile_source_id", int(source_id))

    lab._activate_profile_rule_for_action(0x102)

    assert lab.armed_source_id == 0x102
    assert [row["target_id"] for row in lab.armed_targets] == [0x130, 0x160]
    assert lab.armed_earliest == 8
    assert lab.armed_latest == 0
    assert lab.completed_for_source is False
    assert lab.pulses_this_source == 0
    assert lab._last_trigger_signatures == {}
    assert lab._active_profile_source_id == 0x102


def test_profile_ui_and_runtime_contracts_exist():
    text = (ROOT / "tvcgui" / "features" / "frame_data" / "cancel_lab.py").read_text(encoding="utf-8")
    windows = (ROOT / "tvcgui" / "features" / "frame_data" / "cancel_windows.py").read_text(encoding="utf-8")
    assert 'text="MULTI-SOURCE CANCEL RULES"' in text
    assert 'text="Save current source rule"' in text
    assert 'textvariable=self.profile_arm_button_text' in text
    assert 'def toggle_profile_arm' in text
    assert 'def _load_profile_rule_into_editor' in text
    assert 'def _activate_profile_rule_for_action' in text
    assert 'def _mark_active_profile_source' in text
    assert 'self._activate_profile_rule_for_action(current_action)' in text
    assert 'def get_cancel_profile' in windows
    assert 'def set_cancel_profile_rule' in windows


def test_source_picker_auto_loads_existing_rule_or_starts_a_new_one():
    text = (ROOT / "tvcgui" / "features" / "frame_data" / "cancel_lab.py").read_text(encoding="utf-8")
    block = text.split("def _on_source_changed", 1)[1].split("def _rule_for_target_id", 1)[0]
    assert "self._profile_rule_for_source(source_id)" in block
    assert "self._load_profile_rule_into_editor(rule, announce=False)" in block
    assert "New source rule" in block


def test_multi_source_copy_is_explicit_in_the_ui():
    text = (ROOT / "tvcgui" / "features" / "frame_data" / "cancel_lab.py").read_text(encoding="utf-8")
    assert "Build the source and targets above" in text
    assert "Arm all source rules" in text
    assert "ALL RULES ARMED" in text
