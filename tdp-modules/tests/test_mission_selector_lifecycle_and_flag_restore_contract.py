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


def _gesture(start_seq: int) -> list[dict]:
    return [
        {"seq": start_seq, "held": 0x08, "pressed": 0x08, "released": 0},
        {"seq": start_seq + 1, "held": 0, "pressed": 0, "released": 0x08},
        {"seq": start_seq + 2, "held": 0x08, "pressed": 0x08, "released": 0},
        {"seq": start_seq + 3, "held": 0, "pressed": 0, "released": 0x08},
        {"seq": start_seq + 4, "held": 0x0C00, "pressed": 0x0C00, "released": 0},
    ]


def test_selector_reopens_after_selecting_a_mission(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(
        module,
        "build_overlay_payload",
        lambda name: {
            "character": name,
            "missions": [{"mission_id": "alex_002"}],
            "active_mission_id": "alex_002",
        },
    )
    monkeypatch.setattr(module, "load_progress", lambda: {})
    monkeypatch.setattr(module, "save_progress", lambda _progress: None)
    monkeypatch.setattr(module, "set_selected_mission_id", lambda progress, *_args: progress)

    manager = _manager()
    manager.active_slot = "P1-C1"
    snap = {
        "base": 0x90000000,
        "name": "Alex",
        "teamtag": "P1",
        "damage_point_active": True,
    }
    manager._render_snap_by_slot = {"P1-C1": snap}
    samples = _gesture(1)
    manager.set_input_sample_provider(lambda *_args: ({}, list(samples)))

    manager._update_selector_from_inputs({"P1-C1": snap}, 10.0)
    assert manager.selector_open is True

    samples.extend([
        {"seq": 6, "held": 0, "pressed": 0, "released": 0x0C00},
        {"seq": 7, "held": 0x0C00, "pressed": 0x0C00, "released": 0},
    ])
    manager._update_selector_from_inputs({"P1-C1": snap}, 10.1)
    assert manager.selector_open is False

    samples.extend([
        {"seq": 8, "held": 0, "pressed": 0, "released": 0x0C00},
        *_gesture(9),
    ])
    manager._update_selector_from_inputs({"P1-C1": snap}, 10.2)
    assert manager.selector_open is True


def test_selector_after_tag_opens_new_point_characters_missions(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(
        module,
        "build_overlay_payload",
        lambda name: {
            "character": name,
            "missions": [{"mission_id": "casshan_001" if name == "Casshan" else "alex_001"}],
            "active_mission_id": "casshan_001" if name == "Casshan" else "alex_001",
        },
    )

    manager = _manager()
    manager.active_slot = "P1-C1"
    alex = {
        "base": 0x90000000,
        "name": "Alex",
        "teamtag": "P1",
        "csv_char_id": 4,
        "damage_point_active": False,
    }
    casshan = {
        "base": 0x90010000,
        "name": "Casshan",
        "teamtag": "P1",
        "csv_char_id": 2,
        "damage_point_active": True,
    }
    snaps = {"P1-C1": alex, "P1-C2": casshan}
    manager._render_snap_by_slot = snaps
    requested: list[str] = []

    def provider(slot: str, _base: int):
        requested.append(slot)
        return {}, _gesture(20) if slot == "P1-C2" else []

    manager.set_input_sample_provider(provider)
    manager._update_selector_from_inputs(snaps, 20.0)

    assert requested == ["P1-C2"]
    assert manager.selector_open is True
    assert manager.active_slot == "P1-C2"
    assert manager._mission_owner["name"] == "Casshan"


def test_completed_setup_flag_restores_and_forces_cpu_idle() -> None:
    manager = _manager()
    values = {"CpuAction": 7}
    writes: list[tuple[str, int]] = []
    manager._read_debug_flag = lambda name: values.get(name, 0)

    def write(name: str, value: int) -> bool:
        values[name] = value
        writes.append((name, value))
        return True

    manager._write_debug_flag = write
    payload = {
        "active": True,
        "slot": "P1-C1",
        "character": "Alex",
        "active_mission_id": "alex_002",
        "active_mission_setup_debug_flags": {"CpuAction": 2},
    }

    manager._sync_debug_overrides(payload)
    assert values["CpuAction"] == 2

    manager._sync_debug_overrides({**payload, "just_cleared": True})
    assert values["CpuAction"] == 0
    assert manager._setup_state["completed_mission_key"] == (
        "P1-C1", "Alex", "alex_002"
    )

    writes_before = list(writes)
    manager._sync_debug_overrides(payload)
    assert writes == writes_before
    assert values["CpuAction"] == 0

    manager.active_slot = "P1-C1"
    manager._prepare_selected_mission("Alex", "alex_002")
    manager._sync_debug_overrides(payload)
    assert values["CpuAction"] == 2


def test_mission_without_cpu_action_forces_idle_once() -> None:
    manager = _manager()
    values = {"CpuAction": 5}
    writes: list[tuple[str, int]] = []
    manager._read_debug_flag = lambda name: values.get(name, 0)

    def write(name: str, value: int) -> bool:
        values[name] = value
        writes.append((name, value))
        return True

    manager._write_debug_flag = write
    payload = {
        "active": True,
        "slot": "P1-C1",
        "character": "Alex",
        "active_mission_id": "alex_001",
        "active_mission_setup_debug_flags": {},
    }

    manager._sync_debug_overrides(payload)
    manager._sync_debug_overrides(payload)

    assert values["CpuAction"] == 0
    assert writes.count(("CpuAction", 0)) == 1
