from __future__ import annotations

import importlib
import json
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


def test_resolved_hitstun_counter_confirms_cinematic_reaction() -> None:
    manager = _manager()
    snaps = {
        "P1-C1": {"teamtag": "P1"},
        "P2-C1": {
            "teamtag": "P2",
            "attA": 0,
            "timing_hitstun_remaining": 17,
        },
    }

    assert manager._opponent_in_hitstun("P1-C1", snaps) is True


def test_cinematic_command_chain_keeps_level3_move_as_hit_owner() -> None:
    manager = _manager()
    manager._runtime["mission_action_events"] = [
        {
            "serial": 1,
            "frame": 10,
            "last_frame": 10,
            "sample_seq": 100,
            "action_id": 0x170,
            "char_id": 12,
            "labels": ["Shin Sho"],
            "input_serial": 7,
            "command_serial": 3,
            "consumed": False,
        },
        {
            "serial": 2,
            "frame": 60,
            "last_frame": 75,
            "sample_seq": 200,
            "action_id": 0x200,
            "char_id": 12,
            "labels": ["Cinematic Phase"],
            "input_serial": 9,
            "command_serial": 3,
            "consumed": False,
        },
    ]

    assert manager._candidate_action_serials_for_hit(75) == [1, 2]
    assert manager._pending_command_chain_active(1, 0) is True


def test_new_attack_command_ends_old_pending_chain() -> None:
    manager = _manager()
    manager._runtime["mission_action_events"] = [
        {
            "serial": 1,
            "frame": 10,
            "last_frame": 10,
            "action_id": 0x170,
            "char_id": 13,
            "labels": ["Shichei"],
            "input_serial": 7,
            "command_serial": 3,
            "consumed": False,
        },
        {
            "serial": 2,
            "frame": 80,
            "last_frame": 80,
            "action_id": 0x100,
            "char_id": 13,
            "labels": ["5A"],
            "input_serial": 10,
            "command_serial": 4,
            "consumed": False,
        },
    ]

    assert manager._pending_command_chain_active(1, 0) is False


def test_doronjo_22x_then_taunt_does_not_open_selector(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(
        module,
        "build_overlay_payload",
        lambda _name: {
            "missions": [{"mission_id": "doronjo_004"}],
            "active_mission_id": "doronjo_004",
        },
    )
    manager = _manager()
    manager.active_slot = "P1-C1"
    snap = {
        "base": 0x90000000,
        "name": "Doronjo",
        "teamtag": "P1",
        "damage_point_active": True,
    }
    manager._render_snap_by_slot = {"P1-C1": snap}
    samples = [
        {"seq": 1, "held": 0x08, "pressed": 0x08, "released": 0},
        {"seq": 2, "held": 0x00, "pressed": 0x00, "released": 0x08},
        # Second down is part of 22C, so the C edge invalidates the menu gesture.
        {"seq": 3, "held": 0x28, "pressed": 0x28, "released": 0},
        {"seq": 4, "held": 0x00, "pressed": 0x00, "released": 0x28},
        {"seq": 5, "held": 0x0C00, "pressed": 0x0C00, "released": 0},
    ]
    manager.set_input_sample_provider(lambda *_args: ({}, samples))

    manager._update_selector_from_inputs({"P1-C1": snap}, 10.0)

    assert manager.selector_open is False


def test_saki_load_ammo_catalog_uses_22x() -> None:
    catalog = importlib.import_module(
        "tvcgui.features.training.wiki_input_catalog"
    )

    entries = dict(catalog.WIKI_INPUT_CATALOG["saki omokane"])
    assert entries["special ammo"] == "22X"
    assert entries["load ammo"] == "22X"
    assert entries["load ammo a"] == "22X"
    assert entries["load ammo b"] == "22X"
    assert entries["load ammo c"] == "22X"
    assert entries["ammo load a"] == "22X"
    assert entries["ammo load b"] == "22X"
    assert entries["ammo load c"] == "22X"
    assert entries["ammo load"] == "22X"
    assert catalog.infer_wiki_input_notation("Saki", ["Load Ammo"]) == "22X"
    assert catalog.infer_wiki_input_notation("Saki", ["Load Ammo A"]) == "22A"
    assert catalog.infer_wiki_input_notation("Saki", ["Load Ammo B"]) == "22B"
    assert catalog.infer_wiki_input_notation("Saki", ["Load Ammo C"]) == "22C"


def test_saki_missions_keep_their_required_c_strength() -> None:
    data = json.loads((ROOT / "missions" / "saki.json").read_text(encoding="utf-8"))
    missions = {mission["mission_id"]: mission for mission in data["missions"]}

    assert missions["saki_002"]["steps"][0]["label"] == "Load Ammo C"
    assert missions["saki_008"]["steps"][1]["label"] == "Load Ammo C"


def _cinematic_payload(character: str, mission_id: str, label: str) -> dict:
    return {
        "active": True,
        "slot": "P1-C1",
        "point_slot": "P1-C1",
        "character": character,
        "active_mission_id": mission_id,
        "active_mission_steps": [{"label": label}],
        "active_mission_goal": {},
        "active_mission_setup_meter_refill": False,
    }


def _prepare_pending_confirm_manager(
    monkeypatch,
    *,
    character: str,
    char_id: int,
    mission_id: str,
    expected_label: str,
    current_label: str,
    same_command: bool,
):
    manager = _manager()
    manager.active_slot = "P1-C1"
    manager._mission_owner.update({
        "slot": "P1-C1",
        "teamtag": "P1",
        "base": 0x90000000,
        "char_id": char_id,
        "name": character,
    })
    manager._frame_idx = 80
    manager._runtime.update({
        "slot": "P1-C1",
        "mission_id": mission_id,
        "progress_index": 0,
        "pending_step_index": 0,
        "pending_labels": [expected_label],
        "pending_anim": 0x170,
        "pending_started_frame": 10,
        "pending_input_serial": 7,
        "pending_action_serial": 1,
        "pending_label_confirmed": True,
        "pending_arm_source": "label",
        "last_actual_hitstun": True,
        "last_seen_label": expected_label,
        "last_seen_anim": 0x170,
    })
    command_serial = 3
    manager._runtime["mission_action_events"] = [
        {
            "serial": 1,
            "frame": 10,
            "last_frame": 18,
            "sample_seq": 100,
            "action_id": 0x170,
            "char_id": char_id,
            "labels": [expected_label],
            "input_serial": 7,
            "command_serial": command_serial,
            "consumed": False,
        },
        {
            "serial": 2,
            "frame": 60,
            "last_frame": 80,
            "sample_seq": 200,
            "action_id": 0x200,
            "char_id": char_id,
            "labels": [current_label],
            "input_serial": 9,
            "command_serial": command_serial if same_command else command_serial + 1,
            "consumed": False,
        },
    ]
    manager._runtime["mission_hit_events"] = [
        {
            "serial": 1,
            "frame": 80,
            "last_frame": 80,
            "sample_seq": 201,
            "hp_seen": True,
            "combo_seen": True,
            "damage": 100,
            "owner_action_serial": 2,
            "candidate_action_serials": [1, 2],
            "consumed": False,
        }
    ]
    snaps = {
        "P1-C1": {
            "base": 0x90000000,
            "name": character,
            "teamtag": "P1",
            "csv_char_id": char_id,
            "mv_label": current_label,
            "mv_id_display": 0x200,
            "cur": 40000,
            "meter": 50000,
        },
        "P2-C1": {
            "base": 0x91000000,
            "name": "Dummy",
            "teamtag": "P2",
            "cur": 39900,
            "attA": 60,
            "timing_hitstun_remaining": 12,
        },
    }
    manager._render_snap_by_slot = snaps
    monkeypatch.setattr(manager, "_record_mission_input_stream", lambda *_args: {
        "pressed_buttons": 0,
        "fresh_attack": False,
        "baroque": False,
    })
    monkeypatch.setattr(
        manager,
        "_record_opponent_realtime_stream",
        lambda *_args: ([], False, True),
    )
    monkeypatch.setattr(manager, "_opponent_in_hitstun", lambda *_args: True)
    monkeypatch.setattr(manager, "_opponent_in_megacrash", lambda *_args: False)
    monkeypatch.setattr(manager, "_global_combo_count", lambda: 1)
    monkeypatch.setattr(manager, "_opponent_damage_this_frame", lambda *_args: [])
    monkeypatch.setattr(manager, "_step_input_matches", lambda *_args: False)
    return manager, snaps


def test_shin_sho_confirms_from_later_cinematic_phase(monkeypatch) -> None:
    manager, snaps = _prepare_pending_confirm_manager(
        monkeypatch,
        character="Ryu",
        char_id=12,
        mission_id="ryu_008",
        expected_label="Shin Sho",
        current_label="Shin Sho Cinematic",
        same_command=True,
    )

    result = manager._augment_payload_with_runtime(
        _cinematic_payload("Ryu", "ryu_008", "Shin Sho"),
        snaps,
    )

    assert result["just_cleared"] is True
    assert result["completed_step_count"] == 1


def test_shichei_confirms_from_later_cinematic_phase(monkeypatch) -> None:
    manager, snaps = _prepare_pending_confirm_manager(
        monkeypatch,
        character="Chun-Li",
        char_id=13,
        mission_id="chun_007",
        expected_label="Shichei",
        current_label="Shichei",
        same_command=True,
    )

    result = manager._augment_payload_with_runtime(
        _cinematic_payload("Chun-Li", "chun_007", "Shichei"),
        snaps,
    )

    assert result["just_cleared"] is True
    assert result["completed_step_count"] == 1


def test_delayed_move_hit_survives_new_trigger_command(monkeypatch) -> None:
    manager, snaps = _prepare_pending_confirm_manager(
        monkeypatch,
        character="Casshan",
        char_id=2,
        mission_id="casshan_009",
        expected_label="Scrap Android",
        current_label="5A",
        same_command=False,
    )

    result = manager._augment_payload_with_runtime(
        _cinematic_payload("Casshan", "casshan_009", "Scrap Android"),
        snaps,
    )

    assert result["just_cleared"] is True
    assert result["completed_step_count"] == 1


def test_unmapped_cinematic_phase_is_recorded_for_command_chain() -> None:
    manager = _manager()
    manager._mission_owner.update({"char_id": 12, "name": "Ryu"})
    manager._runtime["mission_attack_command_serial"] = 9

    manager._record_mission_action_sample(
        {
            "action_id": 0x7A10,
            "char_id": 12,
            "sample_ns": 1000,
        },
        frame_idx=40,
        sample_seq=400,
    )

    events = manager._runtime["mission_action_events"]
    assert len(events) == 1
    assert events[0]["labels"] == []
    assert events[0]["phase_only"] is True
    assert events[0]["command_serial"] == 9


def test_generic_delayed_confirm_window_survives_new_trigger_command() -> None:
    manager = _manager()
    manager._runtime.update({
        "pending_action_serial": 1,
        "pending_delayed_confirm_until_frame": 240,
        "mission_action_events": [
            {
                "serial": 1,
                "frame": 10,
                "last_frame": 10,
                "sample_seq": 100,
                "action_id": 0x170,
                "char_id": 99,
                "labels": ["Stored Attack"],
                "command_serial": 1,
                "consumed": False,
            },
            {
                "serial": 2,
                "frame": 200,
                "last_frame": 214,
                "sample_seq": 200,
                "action_id": 0x180,
                "char_id": 99,
                "labels": ["Trigger Attack"],
                "command_serial": 2,
                "consumed": False,
            },
        ],
    })

    candidates = manager._candidate_action_serials_for_hit(214, sample_seq=201)

    assert 1 in candidates
    assert 2 in candidates


def test_generic_delayed_confirm_window_expires_cleanly() -> None:
    manager = _manager()
    manager._runtime.update({
        "pending_action_serial": 1,
        "pending_delayed_confirm_until_frame": 100,
        "mission_action_events": [
            {
                "serial": 1,
                "frame": 10,
                "last_frame": 10,
                "sample_seq": 100,
                "action_id": 0x170,
                "char_id": 99,
                "labels": ["Stored Attack"],
                "command_serial": 1,
                "consumed": False,
            },
            {
                "serial": 2,
                "frame": 200,
                "last_frame": 214,
                "sample_seq": 200,
                "action_id": 0x180,
                "char_id": 99,
                "labels": ["Trigger Attack"],
                "command_serial": 2,
                "consumed": False,
            },
        ],
    })

    candidates = manager._candidate_action_serials_for_hit(214, sample_seq=201)

    assert 1 not in candidates
    assert 2 in candidates


def test_selector_never_opens_during_live_combo(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(
        module,
        "build_overlay_payload",
        lambda _name: {
            "missions": [{"mission_id": "doronjo_004"}],
            "active_mission_id": "doronjo_004",
        },
    )
    manager = _manager()
    manager.active_slot = "P1-C1"
    snap = {
        "base": 0x90000000,
        "name": "Doronjo",
        "teamtag": "P1",
        "damage_point_active": True,
    }
    opponent = {
        "base": 0x91000000,
        "name": "Dummy",
        "teamtag": "P2",
        "attA": 0,
    }
    manager._render_snap_by_slot = {"P1-C1": snap, "P2-C1": opponent}
    monkeypatch.setattr(manager, "_global_combo_count", lambda: 2)
    samples = [
        {"seq": 1, "held": 0x08, "pressed": 0x08, "released": 0},
        {"seq": 2, "held": 0x00, "pressed": 0x00, "released": 0x08},
        {"seq": 3, "held": 0x08, "pressed": 0x08, "released": 0},
        {"seq": 4, "held": 0x00, "pressed": 0x00, "released": 0x08},
        {"seq": 5, "held": 0x0C00, "pressed": 0x0C00, "released": 0},
    ]
    manager.set_input_sample_provider(lambda *_args: ({}, samples))

    manager._update_selector_from_inputs(
        {"P1-C1": snap, "P2-C1": opponent},
        10.0,
    )

    assert manager.selector_open is False



def test_clean_selector_gesture_reaffirms_new_native_point_after_tag(monkeypatch) -> None:
    module = _module()
    monkeypatch.setattr(
        module,
        "build_overlay_payload",
        lambda name: {
            "missions": [{"mission_id": f"{name.lower()}_001"}],
            "active_mission_id": f"{name.lower()}_001",
        },
    )
    manager = _manager()
    manager.active_slot = "P1-C1"
    manager._mission_owner.update({
        "slot": "P1-C1",
        "teamtag": "P1",
        "base": 0x90000000,
        "char_id": 12,
        "name": "Ryu",
    })
    snaps = {
        "P1-C1": {
            "base": 0x90000000,
            "name": "Ryu",
            "teamtag": "P1",
            "csv_char_id": 12,
            "damage_point_active": False,
        },
        "P1-C2": {
            "base": 0x90090000,
            "name": "Ken the Eagle",
            "teamtag": "P1",
            "csv_char_id": 1,
            "damage_point_active": True,
        },
        "P2-C1": {
            "base": 0x91000000,
            "name": "Dummy",
            "teamtag": "P2",
            "attA": 0,
            "timing_hitstun_remaining": 0,
        },
    }
    manager._render_snap_by_slot = snaps
    samples = [
        {"seq": 1, "held": 0x08, "pressed": 0x08, "released": 0},
        {"seq": 2, "held": 0x00, "pressed": 0x00, "released": 0x08},
        {"seq": 3, "held": 0x08, "pressed": 0x08, "released": 0},
        {"seq": 4, "held": 0x00, "pressed": 0x00, "released": 0x08},
        {"seq": 5, "held": 0x0C00, "pressed": 0x0C00, "released": 0},
    ]
    manager.set_input_sample_provider(lambda slot, *_args: ({}, samples if slot == "P1-C2" else []))
    monkeypatch.setattr(manager, "_global_combo_count", lambda: 0)

    manager._update_selector_from_inputs(snaps, 10.0)

    assert manager.selector_open is True
    assert manager.active_slot == "P1-C2"
    assert manager._mission_owner["slot"] == "P1-C2"
    assert manager._mission_owner["name"] == "Ken the Eagle"


def test_whiffed_level3_does_not_complete_without_fresh_hitstun(monkeypatch) -> None:
    manager, snaps = _prepare_pending_confirm_manager(
        monkeypatch,
        character="Ryu",
        char_id=12,
        mission_id="ryu_008",
        expected_label="Shin Sho",
        current_label="Shin Sho Cinematic",
        same_command=True,
    )
    manager._runtime["mission_hit_events"] = []
    manager._runtime["last_actual_hitstun"] = False
    monkeypatch.setattr(manager, "_opponent_in_hitstun", lambda *_args: False)
    monkeypatch.setattr(
        manager,
        "_record_opponent_realtime_stream",
        lambda *_args: ([], False, False),
    )
    snaps["P2-C1"]["timing_hitstun_remaining"] = 0
    snaps["P2-C1"]["attA"] = 0

    result = manager._augment_payload_with_runtime(
        _cinematic_payload("Ryu", "ryu_008", "Shin Sho"),
        snaps,
    )

    assert result.get("just_cleared") is not True
    assert result["completed_step_count"] == 0

def test_scrap_android_uses_generic_delayed_confirm_metadata() -> None:
    data = json.loads((ROOT / "missions" / "casshan.json").read_text(encoding="utf-8"))
    mission = next(m for m in data["missions"] if m["mission_id"] == "casshan_009")

    assert mission["steps"][0]["label"] == "Scrap Android"
    assert mission["steps"][0]["delayed_confirm_frames"] == 180
