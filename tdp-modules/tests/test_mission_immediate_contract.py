from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


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


def test_down_down_taunt_opens_selector_from_one_sampler_batch(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(
        module,
        "build_overlay_payload",
        lambda _name: {
            "missions": [{"mission_id": "ryu_001"}],
            "active_mission_id": "ryu_001",
        },
    )
    manager = _manager()
    manager.active_slot = "P1-C1"
    snap = {"base": 0x90000000, "name": "Ryu", "teamtag": "P1"}
    manager._render_snap_by_slot = {"P1-C1": snap}
    samples = [
        {"seq": 1, "held": 0x08, "pressed": 0x08, "released": 0},
        {"seq": 2, "held": 0x00, "pressed": 0x00, "released": 0x08},
        {"seq": 3, "held": 0x08, "pressed": 0x08, "released": 0},
        {"seq": 4, "held": 0x00, "pressed": 0x00, "released": 0x08},
        {"seq": 5, "held": 0x0C00, "pressed": 0x0C00, "released": 0},
    ]
    manager.set_input_sample_provider(lambda *_args: ({}, samples))

    manager._update_selector_from_inputs({"P1-C1": snap}, 10.0)

    assert manager.selector_open is True


def test_no_grace_route_resets_on_exact_hitstun_exit() -> None:
    manager = _manager()
    manager.active_slot = "P1-C1"
    manager._frame_idx = 10
    manager._runtime.update({
        "slot": "P1-C1",
        "mission_id": "ryu_001",
        "progress_index": 1,
        "last_actual_hitstun": True,
    })
    payload = {
        "active": True,
        "slot": "P1-C1",
        "character": "Ryu",
        "active_mission_id": "ryu_001",
        "active_mission_steps": [
            {"label": "5A", "input": "5A"},
            {"label": "5B", "input": "5B"},
        ],
        "active_mission_goal": {},
    }
    snaps = {
        "P1-C1": {
            "base": 0x90000000,
            "name": "Ryu",
            "teamtag": "P1",
            "mv_label": "Idle",
            "mv_id_display": 0,
        },
        "P2-C1": {
            "base": 0x91000000,
            "name": "Chun-Li",
            "teamtag": "P2",
            "attA": 0,
            "cur": 40000,
        },
    }
    manager._render_snap_by_slot = snaps
    manager._health_damage_frame = 10

    result = manager._augment_payload_with_runtime(payload, snaps)

    assert result["completed_step_count"] == 0
    assert result["current_step_index"] == 0
    assert manager._runtime["progress_index"] == 0


def test_explicit_grace_is_the_only_hitstun_exit_exception() -> None:
    manager = _manager()
    manager.active_slot = "P1-C1"
    manager._frame_idx = 10
    manager._runtime.update({
        "slot": "P1-C1",
        "mission_id": "ryu_001",
        "progress_index": 1,
        "last_actual_hitstun": True,
        "reset_grace_frames": 2,
        "reset_grace_step_index": 1,
    })
    payload = {
        "active": True,
        "slot": "P1-C1",
        "character": "Ryu",
        "active_mission_id": "ryu_001",
        "active_mission_steps": [
            {"label": "5A", "input": "5A"},
            {"label": "5B", "input": "5B"},
        ],
        "active_mission_goal": {},
    }
    snaps = {
        "P1-C1": {
            "base": 0x90000000,
            "name": "Ryu",
            "teamtag": "P1",
            "mv_label": "Idle",
            "mv_id_display": 0,
        },
        "P2-C1": {
            "base": 0x91000000,
            "name": "Chun-Li",
            "teamtag": "P2",
            "attA": 0,
            "cur": 40000,
        },
    }
    manager._render_snap_by_slot = snaps
    manager._health_damage_frame = 10

    result = manager._augment_payload_with_runtime(payload, snaps)

    assert result["completed_step_count"] == 1
    assert manager._runtime["progress_index"] == 1
    assert manager._runtime["reset_grace_frames"] == 1


def test_expired_grace_resets_on_the_first_frame_after_window() -> None:
    manager = _manager()
    manager.active_slot = "P1-C1"
    manager._runtime.update({
        "slot": "P1-C1",
        "mission_id": "ryu_001",
        "progress_index": 1,
        "last_actual_hitstun": True,
        "reset_grace_frames": 2,
        "reset_grace_step_index": 1,
    })
    payload = {
        "active": True,
        "slot": "P1-C1",
        "character": "Ryu",
        "active_mission_id": "ryu_001",
        "active_mission_steps": [
            {"label": "5A", "input": "5A"},
            {"label": "5B", "input": "5B"},
        ],
        "active_mission_goal": {},
    }
    snaps = {
        "P1-C1": {
            "base": 0x90000000,
            "name": "Ryu",
            "teamtag": "P1",
            "mv_label": "Idle",
            "mv_id_display": 0,
        },
        "P2-C1": {
            "base": 0x91000000,
            "name": "Chun-Li",
            "teamtag": "P2",
            "attA": 0,
            "cur": 40000,
        },
    }
    manager._render_snap_by_slot = snaps

    for frame, expected_grace in ((10, 1), (11, 0)):
        manager._frame_idx = frame
        manager._health_damage_frame = frame
        result = manager._augment_payload_with_runtime(payload, snaps)
        assert result["completed_step_count"] == 1
        assert manager._runtime["reset_grace_frames"] == expected_grace

    manager._frame_idx = 12
    manager._health_damage_frame = 12
    result = manager._augment_payload_with_runtime(payload, snaps)

    assert result["completed_step_count"] == 0
    assert result["current_step_index"] == 0
    assert manager._runtime["progress_index"] == 0
