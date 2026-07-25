from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MISSION_MANAGER = ROOT / "tvcgui" / "features" / "training" / "mission_manager.py"


def _manager_class():
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
    return importlib.import_module(
        "tvcgui.features.training.mission_manager"
    ).MissionManager


def _manager():
    cls = _manager_class()
    return cls({}, {}, {}, lambda: [], lambda *_args: "")


def _input(serial: int, frame: int, button_mask: int, token: str) -> dict:
    return {
        "serial": serial,
        "frame": frame,
        "token": token,
        "direction": 0,
        "pressed": button_mask,
        "held_buttons": button_mask,
    }


def _hit(serial: int, frame: int) -> dict:
    return {
        "serial": serial,
        "frame": frame,
        "last_frame": frame,
        "hp_seen": True,
        "combo_seen": True,
        "damage": 100,
        "consumed": False,
    }


def test_fast_normal_chain_drains_every_ready_step_in_one_update() -> None:
    manager = _manager()
    manager._runtime["mission_input_events"] = [
        _input(1, 10, 0x80, "5A"),
        _input(2, 11, 0x40, "5B"),
        _input(3, 12, 0x20, "5C"),
    ]
    manager._runtime["mission_input_serial"] = 3
    manager._runtime["mission_hit_events"] = [
        _hit(1, 13),
        _hit(2, 14),
        _hit(3, 15),
    ]

    progress, advanced = manager._drain_buffered_normal_steps(
        [{"label": "5A"}, {"label": "5B"}, {"label": "5C"}],
        0,
        15,
    )

    assert (progress, advanced) == (3, 3)
    assert manager._runtime["mission_input_consumed_serial"] == 3
    assert all(event["consumed"] for event in manager._runtime["mission_hit_events"])


def test_repeated_normals_claim_oldest_input_and_hit_first() -> None:
    manager = _manager()
    manager._runtime["mission_input_events"] = [
        _input(1, 10, 0x80, "5A"),
        _input(2, 20, 0x80, "5A"),
    ]
    manager._runtime["mission_input_serial"] = 2
    manager._runtime["mission_hit_events"] = [_hit(1, 13), _hit(2, 23)]

    progress, advanced = manager._drain_buffered_normal_steps(
        [{"label": "5A"}, {"label": "5A"}],
        0,
        23,
    )

    assert (progress, advanced) == (2, 2)
    assert manager._runtime["mission_input_consumed_serial"] == 2


def test_single_step_completion_does_not_discard_later_buffered_inputs() -> None:
    source = MISSION_MANAGER.read_text(encoding="utf-8")
    consume_block = source.split(
        "if progress_index > starting_progress_index:", 1
    )[1].split("if not partner_var_matched:", 1)[0]
    assert 'self._runtime.get("mission_input_serial"' not in consume_block
    assert 'self._runtime.get("mission_input_consumed_serial"' in consume_block


def test_every_changed_overlay_payload_publishes_without_time_throttle() -> None:
    source = MISSION_MANAGER.read_text(encoding="utf-8")
    assert "if serialized == self._last_overlay_serialized" in source
    assert "Do not time-throttle mission UI changes" in source
    assert "and not progress_changed" not in source
    assert "self._last_overlay_progress_signature = progress_signature" in source


def test_hitstun_exit_edge_overrides_recent_hit_and_buffer_latches() -> None:
    source = MISSION_MANAGER.read_text(encoding="utf-8")
    assert "realtime_hitstun_exit" in source
    assert "previous_actual_hitstun and not actual_hitstun_now" in source
    assert "and hitstun_exit_edge" in source
    assert "and not explicit_reset_grace" in source
    assert "or recent_hit_evidence" not in source


def test_selector_and_route_have_independent_high_frequency_cursors() -> None:
    manager = _manager()
    samples = [
        {"seq": 1, "held": 0x08, "pressed": 0x08, "released": 0},
        {"seq": 2, "held": 0, "pressed": 0, "released": 0x08},
        {"seq": 3, "held": 0x80, "pressed": 0x80, "released": 0},
    ]
    manager.set_input_sample_provider(lambda *_args: ({}, samples))
    snap = {"base": 0x90000000}

    selector_packets = manager._input_packets_for_slot(
        "P1-C1", snap, consumer="selector"
    )
    mission_packets = manager._input_packets_for_slot(
        "P1-C1", snap, consumer="mission"
    )

    assert [packet["seq"] for packet in selector_packets] == [1, 2, 3]
    assert [packet["seq"] for packet in mission_packets] == [1, 2, 3]


def test_same_frame_repeated_buttons_survive_distinct_sampler_sequences() -> None:
    manager = _manager()
    manager._record_mission_input(
        {"held": 0x80, "pressed": 0x80}, 10, sample_seq=1
    )
    manager._record_mission_input(
        {"held": 0, "pressed": 0}, 10, sample_seq=2
    )
    manager._record_mission_input(
        {"held": 0x80, "pressed": 0x80}, 10, sample_seq=3
    )

    attacks = [
        event for event in manager._runtime["mission_input_events"]
        if event.get("token") == "5A"
    ]
    assert len(attacks) == 2
    assert [event.get("sample_seq") for event in attacks] == [1, 3]


def test_native_action_queue_distinguishes_neutral_air_normals() -> None:
    manager = _manager()
    manager._runtime["mission_action_events"] = [
        {
            "serial": 1,
            "frame": 10,
            "sample_seq": 100,
            "action_id": 0x100,
            "labels": ["5A"],
            "input_serial": 1,
            "consumed": False,
        },
        {
            "serial": 2,
            "frame": 10,
            "sample_seq": 110,
            "action_id": 0x10A,
            "labels": ["j.B"],
            "input_serial": 2,
            "consumed": False,
        },
        {
            "serial": 3,
            "frame": 10,
            "sample_seq": 120,
            "action_id": 0x10B,
            "labels": ["j.C"],
            "input_serial": 3,
            "consumed": False,
        },
    ]
    manager._runtime["mission_input_events"] = [
        _input(1, 10, 0x80, "5A"),
        _input(2, 10, 0x40, "5B"),
        _input(3, 10, 0x20, "5C"),
    ]
    manager._runtime["mission_hit_events"] = [
        {**_hit(1, 10), "sample_seq": 105, "owner_action_serial": 1},
        {**_hit(2, 10), "sample_seq": 115, "owner_action_serial": 2},
        {**_hit(3, 10), "sample_seq": 125, "owner_action_serial": 3},
    ]

    progress, advanced = manager._drain_buffered_action_steps(
        [{"label": "5A"}, {"label": "j.B"}, {"label": "j.C"}],
        0,
        10,
    )

    assert (progress, advanced) == (3, 3)
    assert all(event["consumed"] for event in manager._runtime["mission_action_events"])
    assert all(event["consumed"] for event in manager._runtime["mission_hit_events"])


def test_hit_cannot_be_donated_from_wrong_native_action() -> None:
    manager = _manager()
    manager._runtime["mission_action_events"] = [
        {
            "serial": 1,
            "frame": 10,
            "sample_seq": 100,
            "action_id": 0x101,
            "labels": ["5B"],
            "input_serial": 1,
            "consumed": False,
        },
        {
            "serial": 2,
            "frame": 10,
            "sample_seq": 110,
            "action_id": 0x100,
            "labels": ["5A"],
            "input_serial": 2,
            "consumed": False,
        },
    ]
    manager._runtime["mission_hit_events"] = [
        {**_hit(1, 10), "sample_seq": 105, "owner_action_serial": 1},
    ]

    progress, advanced = manager._drain_buffered_action_steps(
        [{"label": "5A"}], 0, 10
    )

    assert (progress, advanced) == (0, 0)
