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


def test_native_action_event_keeps_hud_resolved_soki_slam_b_label() -> None:
    manager = _manager()
    manager._mission_owner.update({"char_id": 21, "name": "Soki"})

    manager._record_mission_action_sample(
        {
            "action_id": 305,
            "char_id": 21,
            "sample_ns": 1000,
        },
        frame_idx=20,
        sample_seq=80,
        snap={
            "csv_char_id": 21,
            "mv_id_display": 305,
            "mv_label": "Slam B",
            "mv_label_display": "Slam B",
        },
    )

    assert manager._runtime["mission_action_events"][-1]["labels"] == ["Slam B"]
    manager._runtime["mission_hit_events"] = [
        {
            "serial": 1,
            "frame": 21,
            "last_frame": 21,
            "sample_seq": 81,
            "hp_seen": True,
            "combo_seen": True,
            "damage": 800,
            "owner_action_serial": 1,
            "candidate_action_serials": [1],
            "consumed": False,
        }
    ]
    matched = manager._latest_matching_action_event(
        {"label": "Slam B"},
        ["Slam B"],
    )
    assert matched is not None
    assert matched["action_id"] == 305
    pair = manager._find_action_hit_pair_for_step(
        {"label": "Slam B"},
        ["Slam B"],
        21,
    )
    assert pair is not None
    assert pair[0]["labels"] == ["Slam B"]


def test_generic_powerbomb_accepts_all_strengths_and_native_start_phases() -> None:
    manager = _manager()

    for current in (
        "Powerbomb A",
        "Powerbomb B",
        "Powerbomb C",
        "Powerbomb Start/Whiff A",
        "Powerbomb Start/Whiff B",
        "Powerbomb Start/Whiff C",
        "Power Bomb Start/Whiff C",
    ):
        assert manager._mission_label_matches(current, ["Powerbomb"]), current

    for current in (
        "Air Powerbomb",
        "Air Powerbomb Start/Whiff",
        "Air Powerbomb A",
        "Air Powerbomb B",
        "Air Powerbomb C",
    ):
        assert manager._mission_label_matches(current, ["Air Powerbomb"]), current

    assert not manager._mission_label_matches("Powerbomb C", ["Air Powerbomb"])


def test_strength_specific_move_stays_strict() -> None:
    manager = _manager()

    assert manager._mission_label_matches("Slam B", ["Slam B"])
    assert manager._mission_label_matches("Slam B Start/Whiff", ["Slam B"])
    assert not manager._mission_label_matches("Slam A", ["Slam B"])
    assert not manager._mission_label_matches("Slam C", ["Slam B"])


def _air_powerbomb_payload() -> dict:
    return {
        "active": True,
        "slot": "P1-C1",
        "point_slot": "P1-C1",
        "character": "Alex",
        "active_mission_id": "alex_grace_test",
        "active_mission_steps": [
            {"label": "j.B (second hit)", "grace": 3},
            {"label": "Air Powerbomb"},
        ],
        "active_mission_goal": {},
        "active_mission_setup_meter_refill": False,
    }


def _prepare_air_powerbomb_manager(*, defender_hitstun: int):
    manager = _manager()
    manager.active_slot = "P1-C1"
    manager._frame_idx = 30
    manager._mission_owner.update({
        "slot": "P1-C1",
        "teamtag": "P1",
        "base": 0x90000000,
        "char_id": 16,
        "name": "Alex",
    })
    manager._runtime.update({
        "slot": "P1-C1",
        "mission_id": "alex_grace_test",
        "progress_index": 1,
        "reset_grace_frames": 3,
        "reset_grace_step_index": 1,
        "last_actual_hitstun": False,
        "last_seen_label": "Idle",
        "last_seen_anim": 0,
    })
    manager._runtime["mission_action_events"] = [
        {
            "serial": 1,
            "frame": 30,
            "last_frame": 30,
            "sample_seq": 100,
            "action_id": 325,
            "char_id": 16,
            "labels": ["Air Powerbomb Start/Whiff"],
            "input_serial": 1,
            "command_serial": 1,
            "consumed": False,
        }
    ]
    manager._runtime["mission_hit_events"] = [
        {
            "serial": 1,
            "frame": 30,
            "last_frame": 30,
            "hp_seen": True,
            "combo_seen": True,
            "damage": 200,
            "owner_action_serial": 1,
            "candidate_action_serials": [1],
            "consumed": False,
        }
    ]
    manager._record_mission_input_stream = lambda *_args, **_kwargs: {
        "held": 0,
        "pressed": 0,
        "direction": 0,
        "pressed_buttons": 0,
        "fresh_attack": False,
        "baroque": False,
    }
    manager._record_opponent_realtime_stream = lambda *_args, **_kwargs: (
        [],
        False,
        bool(defender_hitstun),
    )
    manager._global_combo_count = lambda: 1 if defender_hitstun else 0
    manager._health_damage_frame = 30
    manager._health_damage_events = []

    snaps = {
        "P1-C1": {
            "base": 0x90000000,
            "name": "Alex",
            "teamtag": "P1",
            "csv_char_id": 16,
            "mv_label": "Air Powerbomb Start/Whiff",
            "mv_id_display": 325,
            "cur": 40000,
            "meter": 0,
        },
        "P2-C1": {
            "base": 0x91000000,
            "name": "Dummy",
            "teamtag": "P2",
            "cur": 39800,
            "attA": 0,
            "timing_hitstun_remaining": defender_hitstun,
        },
    }
    manager._render_snap_by_slot = snaps
    return manager, snaps


def test_grace_keeps_route_alive_but_cannot_confirm_a_whiff() -> None:
    manager, snaps = _prepare_air_powerbomb_manager(defender_hitstun=0)

    result = manager._augment_payload_with_runtime(_air_powerbomb_payload(), snaps)

    assert result["completed_step_count"] == 1
    assert manager._runtime["progress_index"] == 1
    assert manager._runtime["reset_grace_frames"] == 2
    assert manager._runtime["mission_hit_events"][0]["consumed"] is False


def test_generic_air_powerbomb_completes_after_real_hitstun_confirmation() -> None:
    manager, snaps = _prepare_air_powerbomb_manager(defender_hitstun=8)

    result = manager._augment_payload_with_runtime(_air_powerbomb_payload(), snaps)

    assert result["just_cleared"] is True
    assert result["completed_step_count"] == 2


def test_final_hud_label_is_primary_even_when_native_ids_disagree() -> None:
    manager = _manager()
    manager._mission_owner.update({"char_id": 21, "name": "Soki"})

    manager._record_mission_action_sample(
        {
            "action_id": 0x4A1,
            "char_id": 21,
            "sample_ns": 1000,
        },
        frame_idx=20,
        sample_seq=80,
        snap={
            "csv_char_id": 21,
            "mv_id_display": 0x131,
            "attA": 0x131,
            "mv_label": "",
            "mv_label_display": "",
            "final_move_label": "Slam B",
        },
    )

    event = manager._runtime["mission_action_events"][-1]
    assert event["action_id"] == 0x4A1
    assert event["labels"][0] == "Slam B"
    assert manager._action_event_matches_step(event, {"label": "Slam B"}, ["Slam B"])


def test_snapshot_matching_uses_final_hud_label_before_raw_label() -> None:
    manager = _manager()
    snap = {
        "mv_label": "Unknown 0x04A1",
        "mv_label_display": "",
        "final_move_label": "Slam B",
    }

    assert manager._mission_snapshot_label_matches(snap, ["Slam B"])
    assert not manager._mission_snapshot_label_matches(snap, ["Slam A"])


def test_only_latest_240hz_packet_receives_current_hud_label() -> None:
    manager = _manager()
    manager._mission_owner.update({"char_id": 21, "name": "Soki"})
    packets = [
        {"seq": 80, "action_id": 0x410, "char_id": 21, "sample_ns": 1000},
        {"seq": 81, "action_id": 0x4A1, "char_id": 21, "sample_ns": 2000},
    ]
    manager._input_packets_for_slot = lambda *_args, **_kwargs: packets
    manager._record_mission_input = lambda *_args, **_kwargs: {
        "held": 0,
        "pressed": 0,
        "direction": 0,
        "pressed_buttons": 0,
        "fresh_attack": False,
        "baroque": False,
    }

    manager._record_mission_input_stream(
        "P1-C1",
        {
            "csv_char_id": 21,
            "mv_id_display": 0x131,
            "final_move_label": "Slam B",
        },
        frame_idx=20,
    )

    events = manager._runtime["mission_action_events"]
    assert len(events) == 2
    assert "Slam B" not in events[0]["labels"]
    assert events[1]["labels"][0] == "Slam B"


def test_soki_slam_b_clears_from_hud_label_with_different_native_action_id() -> None:
    manager = _manager()
    manager.active_slot = "P1-C1"
    manager._frame_idx = 30
    manager._mission_owner.update({
        "slot": "P1-C1",
        "teamtag": "P1",
        "base": 0x90000000,
        "char_id": 21,
        "name": "Soki",
    })
    manager._runtime.update({
        "slot": "P1-C1",
        "mission_id": "soki_label_primary_test",
        "progress_index": 0,
        "last_actual_hitstun": False,
        "last_seen_label": "6C",
        "last_seen_anim": 0x130,
    })
    manager._runtime["mission_action_events"] = [
        {
            "serial": 1,
            "frame": 30,
            "last_frame": 30,
            "sample_seq": 100,
            "action_id": 0x4A1,
            "char_id": 21,
            "labels": ["Slam B"],
            "input_serial": 1,
            "command_serial": 1,
            "consumed": False,
        }
    ]
    manager._runtime["mission_hit_events"] = [
        {
            "serial": 1,
            "frame": 30,
            "last_frame": 30,
            "sample_seq": 101,
            "hp_seen": True,
            "combo_seen": True,
            "damage": 500,
            "owner_action_serial": 1,
            "candidate_action_serials": [1],
            "consumed": False,
        }
    ]
    manager._record_mission_input_stream = lambda *_args, **_kwargs: {
        "held": 0,
        "pressed": 0,
        "direction": 0,
        "pressed_buttons": 0,
        "fresh_attack": False,
        "baroque": False,
    }
    manager._record_opponent_realtime_stream = lambda *_args, **_kwargs: (
        [],
        False,
        True,
    )
    manager._global_combo_count = lambda: 1
    manager._health_damage_frame = 30
    manager._health_damage_events = []

    snaps = {
        "P1-C1": {
            "base": 0x90000000,
            "name": "Soki",
            "teamtag": "P1",
            "csv_char_id": 21,
            "mv_label": "Unknown 0x04A1",
            "mv_label_display": "",
            "final_move_label": "Slam B",
            "mv_id_display": 0x131,
            "cur": 40000,
            "meter": 0,
        },
        "P2-C1": {
            "base": 0x91000000,
            "name": "Dummy",
            "teamtag": "P2",
            "cur": 39500,
            "attA": 0,
            "timing_hitstun_remaining": 8,
        },
    }
    manager._render_snap_by_slot = snaps
    payload = {
        "active": True,
        "slot": "P1-C1",
        "point_slot": "P1-C1",
        "character": "Soki",
        "active_mission_id": "soki_label_primary_test",
        "active_mission_steps": [{"label": "Slam B"}],
        "active_mission_goal": {},
        "active_mission_setup_meter_refill": False,
    }

    result = manager._augment_payload_with_runtime(payload, snaps)

    assert result["just_cleared"] is True
    assert result["completed_step_count"] == 1
