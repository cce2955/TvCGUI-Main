from __future__ import annotations

import importlib
import sys
import time
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MISSION_MANAGER = ROOT / "tvcgui" / "features" / "training" / "mission_manager.py"
MASTER_RENDERER = ROOT / "tvcgui" / "features" / "overlay" / "master_renderer.py"


def _module():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if "dolphin_memory_engine" not in sys.modules:
        dme = types.ModuleType("dolphin_memory_engine")
        dme.is_hooked = lambda: False
        dme.hook = lambda: None
        dme.un_hook = lambda: None
        dme.read_byte = lambda *_args, **_kwargs: 0
        dme.read_bytes = lambda *_args, **_kwargs: b""
        dme.write_byte = lambda *_args, **_kwargs: None
        dme.write_bytes = lambda *_args, **_kwargs: None
        sys.modules["dolphin_memory_engine"] = dme
    return importlib.import_module("tvcgui.features.training.mission_manager")


def _manager():
    module = _module()
    return module.MissionManager({}, {}, {}, lambda: [], lambda *_args: "")


def _action(serial: int, action_id: int, label: str, sample_ns: int) -> dict:
    return {
        "serial": serial,
        "frame": 10,
        "last_frame": 10,
        "sample_seq": serial,
        "sample_ns": sample_ns,
        "action_id": action_id,
        "char_id": 12,
        "labels": [label],
        "input_serial": serial,
        "consumed": False,
    }


def test_observed_action_does_not_inherit_expected_step_notation() -> None:
    manager = _manager()
    event = _action(1, 0x101, "5B", time.monotonic_ns())
    step = {"label": "j.B", "input": "j.B"}

    assert manager._action_event_matches_step(event, step, ["j.B"]) is False


def test_prediction_scans_multiple_native_actions_without_confirming_them() -> None:
    manager = _manager()
    now = time.monotonic_ns()
    manager._runtime["mission_action_events"] = [
        _action(1, 0x100, "5A", now),
        _action(2, 0x101, "5B", now + 1),
        _action(3, 0x10A, "j.B", now + 2),
    ]
    manager._mission_timing_by_action = {
        (12, 0x100): {"startup": 5, "active": 2, "hitstun": 12},
        (12, 0x101): {"startup": 8, "active": 3, "hitstun": 14},
        (12, 0x10A): {"startup": 6, "active": 4, "hitstun": 15},
    }
    steps = [
        {"label": "5A", "input": "5A"},
        {"label": "5B", "input": "5B"},
        {"label": "j.B", "input": "j.B"},
    ]

    predicted = manager._refresh_predicted_progress(steps, 0)

    assert predicted == 3
    assert manager._runtime["progress_index"] == 0
    assert len(manager._runtime["prediction_entries"]) == 3
    assert manager._runtime["prediction_entries"][2]["label"] == "j.B"


def test_prediction_stops_before_explicit_grace_step() -> None:
    manager = _manager()
    now = time.monotonic_ns()
    manager._runtime["mission_action_events"] = [
        _action(1, 0x100, "5A", now),
        _action(2, 0x101, "5B", now + 1),
    ]
    steps = [
        {"label": "5A", "input": "5A"},
        {"label": "5B", "input": "5B", "grace": 5},
    ]

    assert manager._refresh_predicted_progress(steps, 0) == 1


def test_renderer_snaps_progress_and_scroll_without_catchup_delay() -> None:
    source = MASTER_RENDERER.read_text(encoding="utf-8")
    assert "The panel must show the" in source
    assert "self._mission_progress_display = progress_target" in source
    assert "self._mission_scroll_pos = target" in source
    assert "duration = 0.34" not in source.split(
        "def _update_mission_scroll_state", 1
    )[1].split("def update_mission_animations", 1)[0]


def test_predicted_steps_are_distinct_from_confirmed_steps_in_renderer() -> None:
    source = MASTER_RENDERER.read_text(encoding="utf-8")
    assert 'data.get("confirmed_step_count", completed_count)' in source
    assert 'data.get(\n                    "predicted_step_count"' in source
    assert '"COMPLETE" if mission_done else "CONFIRMED"' in source
    assert 'ready = self.smallfont.render("READY"' in source
