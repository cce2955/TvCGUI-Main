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


def test_jump_cancel_rows_are_free_passes_even_without_live_action_label() -> None:
    manager = _manager()
    manager.active_slot = "P1-C1"
    manager._frame_idx = 100
    manager._runtime.update({
        "slot": "P1-C1",
        "mission_id": "test_jump_cancel",
        "progress_index": 1,
        "last_actual_hitstun": True,
    })
    manager._global_combo_count = lambda: 1
    manager._opponent_in_hitstun = lambda *_args: True
    manager._opponent_in_megacrash = lambda *_args: False
    manager._opponent_damage_this_frame = lambda *_args: []

    payload = {
        "active": True,
        "slot": "P1-C1",
        "character": "Chun-Li",
        "active_mission_id": "test_jump_cancel",
        "active_mission_steps": [
            {"label": "j.C"},
            {"label": "Jump Cancel", "pass": True},
            {"label": "j.B"},
        ],
        "active_mission_goal": {},
    }
    snaps = {
        "P1-C1": {
            "base": 0x90000000,
            "name": "Chun-Li",
            "teamtag": "P1",
            "mv_label": "j.C",
            "mv_id_display": 0x010B,
            "inputs": {},
        },
        "P2-C1": {
            "base": 0x91000000,
            "name": "Ryu",
            "teamtag": "P2",
            "attA": 1,
            "cur": 40000,
        },
    }
    manager._render_snap_by_slot = snaps

    result = manager._augment_payload_with_runtime(payload, snaps)

    assert manager._runtime["progress_index"] == 2
    assert result["completed_step_count"] == 2
    assert result["current_step_index"] == 2
    assert result["current_step_label"] == "j.B"


def test_jump_cancel_notation_is_recognized_without_requiring_pass_flag() -> None:
    manager = _manager()

    assert manager._step_is_jump_cancel({"label": "Jump Cancel"})
    assert manager._step_is_jump_cancel({"input": "7 / 8 / 9"})
    assert not manager._step_is_jump_cancel({"label": "Air Dash A", "pass": True})


def test_consecutive_jump_cancel_rows_are_all_skipped() -> None:
    manager = _manager()
    steps = [
        {"label": "Jump Cancel"},
        {"label": "Jump Cancel", "pass": True},
        {"label": "j.B"},
    ]

    progress, advanced = manager._consume_jump_cancel_free_passes(steps, 0)

    assert progress == 2
    assert advanced == 2
    assert manager._runtime["progress_index"] == 2
