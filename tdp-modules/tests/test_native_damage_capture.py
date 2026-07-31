from __future__ import annotations

from tvcgui.features.training.attack_resolver_readonly import (
    ReadOnlyAttackResearch,
    _source_from_snap,
)


def test_snapshot_source_preserves_native_damage_fields():
    source = _source_from_snap(
        "P1-C1",
        {
            "base": 0x9246B9C0,
            "id": 0,
            "name": "Ryu",
            "attack_property_display_active": True,
            "attack_property_packet_source": "resolver_mailbox",
            "attack_property_packet_state": "CONTACT",
            "attack_property_live_a": 0x24,
            "attack_property_live_b": 0x40,
            "attack_property_live_authored_damage": 1800,
            "attack_property_live_damage_calc_output": 1300,
            "attack_property_live_damage_calc_aux": 153,
            "attack_property_native_damage_calc_complete": True,
            "attack_property_live_applied_damage": 1224,
            "attack_property_live_resolved_damage": 1224,
            "attack_property_live_resolved_aux": 153,
            "attack_property_native_damage_complete": True,
            "attack_property_resolver_hook_state": "READY",
            "attack_property_resolver_hook_error": "",
        },
        100,
    )[0]
    assert source["authored_damage"] == 1800
    assert source["damage_calc_output"] == 1300
    assert source["damage_calc_aux"] == 153
    assert source["native_damage_calc_complete"] is True
    assert source["applied_damage"] == 1224
    assert source["resolved_damage"] == 1224
    assert source["resolved_aux"] == 153
    assert source["resolved_damage_known"] is True
    assert source["native_damage_complete"] is True
    assert source["native_capture_enabled"] is True
    assert source["resolver_hook_state"] == "READY"
    assert source["resolver_hook_error"] == ""


def test_native_resolved_damage_wins_over_coalesced_hp_delta(tmp_path):
    research = ReadOnlyAttackResearch(runtime_dir=tmp_path)
    attacker = {
        "slot": "P1-C1", "base": 0x9246B9C0, "char_id": 0, "name": "Ryu",
        "meter": 0, "combo_count": 2, "combo_scale": 0.97,
    }
    source = {
        "frame": 100,
        "source_kind": "resolver_mailbox",
        "property_a": 0x24,
        "property_b": 0x40,
        "authored_damage": 1800,
        "authored_damage_known": True,
        "base_damage": 1800,
        "base_damage_known": True,
        "damage_calc_output": 1300,
        "damage_calc_output_known": True,
        "damage_calc_aux": 153,
        "native_damage_calc_complete": True,
        "applied_damage": 1224,
        "resolved_damage": 1224,
        "resolved_damage_known": True,
        "resolved_aux": 153,
        "native_damage_complete": True,
        "native_capture_enabled": True,
        "resolver_hook_state": "READY",
        "resolver_hook_error": "",
    }
    research._select_attacker = lambda _victim, _states, _frame: (attacker, source, 100.0, ["native resolver"])
    previous = {
        "slot": "P2-C1", "base": 0x927EB9E0, "char_id": 1, "name": "Ken",
        "hp": 10000, "max_hp": 10000, "recoverable": 0, "meter": 0,
        "action_id": 0, "reaction_phase": "neutral", "frame": 99,
    }
    victim = {
        **previous,
        "hp": 7408,
        "last_hit": 1224,
        "action_id": 70,
        "reaction_phase": "hitstun",
        "hitstun": 20,
        "frame": 100,
    }
    research._new_contact(previous, victim, {"P1-C1": attacker, "P2-C1": victim}, 100, 1.0, ["hp_drop"], {"hp_loss": 2592})
    record = research._pending[1]
    assert record["observed_hp_loss"] == 2592
    assert record["damage_calc_output"] == 1300
    assert record["applied_damage"] == 1224
    assert record["attributed_damage"] == 1224
    assert record["damage_attribution_source"] == "native_resolved"
    assert record["damage_attribution_confident"] is True
    assert record["same_frame_unattributed_damage"] == 1368
    assert record["coalesced_contacts_suspected"] is True
    assert record["native_capture_enabled"] is True
    assert record["resolver_hook_state"] == "READY"
    assert record["resolver_hook_error"] == ""
    research.close()


def test_profiler_defers_collision_event_until_applied_damage_arrives(tmp_path):
    import struct
    from tvcgui.features.training import attack_property_profiler as module

    class Memory:
        def __init__(self):
            self.data = {}

        def read(self, address, size):
            return bytes(self.data.get(address + i, 0) for i in range(size))

        def write(self, address, payload):
            for i, value in enumerate(bytes(payload)):
                self.data[address + i] = value
            return True

        def read_u32(self, address):
            return struct.unpack(">I", self.read(address, 4))[0]

        def write_u32(self, address, value):
            self.write(address, struct.pack(">I", value & 0xFFFFFFFF))

    memory = Memory()
    for address, original in (
        (module.RESOLVER_HOOK_ADDR, module.RESOLVER_HOOK_ORIGINAL),
        (module.RESOLVER_EXIT_HOOK_ADDR, module.RESOLVER_EXIT_HOOK_ORIGINAL),
        (module.RESOLVER_COLLISION_RETURN_HOOK_ADDR, module.RESOLVER_COLLISION_RETURN_HOOK_ORIGINAL),
        (module.RESOLVER_APPLY_HOOK_ADDR, module.RESOLVER_APPLY_HOOK_ORIGINAL),
    ):
        memory.write_u32(address, original)

    attacker = 0x9246B9C0
    defender = 0x927EB9E0
    for base, char_id, action in ((attacker, 0, 0x101), (defender, 1, 0)):
        memory.write_u32(base + module.FIGHTER_CHAR_ID, char_id)
        memory.write(base + module.FIGHTER_ACTION_FRAME, struct.pack(">f", 6.0))
        memory.write_u32(base + module.FIGHTER_ACTION_ID, action)
        memory.write_u32(base + module.FIGHTER_POINT_ACTIVE, 1)
        memory.write_u32(base + module.FIGHTER_COMBO_LANE_ACTIVE, 1)

    profiler = module.RuntimeAttackPropertyProfiler(
        path=tmp_path / "profiles.json",
        event_path=tmp_path / "events.csv",
        read_u32=memory.read_u32,
        read_block=memory.read,
        write_block=memory.write,
        is_valid_addr=lambda address: bool(address),
        start_worker=False,
        emit_console=False,
        enable_resolver_hook=True,
    )
    profiler.update({
        "P1-C1": {"base": attacker, "id": 0, "name": "Ryu", "attA": 0x101, "mv_label": "5A"},
        "P2-C1": {"base": defender, "id": 1, "name": "Ken"},
    }, frame=1)
    assert profiler.sample_once() == 0

    sequence = 1
    entry = module.RESOLVER_MAILBOX_ADDR + module.RESOLVER_MAILBOX_HEADER_SIZE + sequence * module.RESOLVER_ENTRY_SIZE
    values = {
        module.RES_OFF_SEQUENCE: sequence,
        module.RES_OFF_ATTACKER: attacker,
        module.RES_OFF_DEFENDER: defender,
        module.RES_OFF_PROPERTY_A: 0x24,
        module.RES_OFF_PROPERTY_B: 0x40,
        module.RES_OFF_PACKET: 0x91A6B7D8,
        module.RES_OFF_CALLER_LR: module.RESOLVER_COLLISION_CALLER_LR,
        module.RES_OFF_AUTHORED_DAMAGE: 1800,
        module.RES_OFF_PACKET_OWNER: attacker,
        module.RES_OFF_ACTION_ID: 0x101,
        module.RES_OFF_DAMAGE_CALC_OUTPUT: 1300,
        module.RES_OFF_DAMAGE_CALC_AUX: 153,
        module.RES_OFF_CALC_COMPLETION_SEQUENCE: sequence,
    }
    for offset, value in values.items():
        memory.write_u32(entry + offset, value)
    memory.write_u32(module.RESOLVER_MAILBOX_ADDR, sequence)

    assert profiler.sample_once() == 0
    assert profiler.doc["events"] == []

    memory.write_u32(entry + module.RES_OFF_APPLIED_DAMAGE, 1224)
    memory.write_u32(entry + module.RES_OFF_APPLICATION_SEQUENCE, sequence)
    assert profiler.sample_once() == 1
    event = profiler.doc["events"][0]
    assert event["damage_calc_output"] == 1300
    assert event["applied_damage"] == 1224
    assert event["resolved_damage"] == 1224
    profiler.flush()


def test_main_enables_native_capture_by_default():
    from pathlib import Path

    main_text = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
    assert 'os.environ.get("TVC_NATIVE_ATTACK_CAPTURE", "1")' in main_text


def test_disabled_hook_is_visible_in_source_export():
    source = _source_from_snap(
        "P1-C1",
        {
            "base": 0x9246B9C0,
            "id": 0,
            "name": "Ryu",
            "attack_property_display_active": True,
            "attack_property_packet_source": "move_definition",
            "attack_property_packet_state": "CURRENT MOVE",
            "attack_property_live_a": 0x09,
            "attack_property_live_b": 0x40,
            "attack_property_resolver_hook_state": "DISABLED",
            "attack_property_resolver_hook_error": "",
        },
        100,
    )[0]
    assert source["native_capture_enabled"] is False
    assert source["resolver_hook_state"] == "DISABLED"
