from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tvcgui.features.frame_data.attack_property_runtime import (
    LIVE_PROJECTILE_ACTOR_TABLE,
    collect_live_projectile_properties,
    clear_attack_property_runtime_cache,
    resolve_live_attack_definition,
)
from tvcgui.features.training import attack_property_profiler as profiler_module


class SparseMemory:
    def __init__(self):
        self.data: dict[int, int] = {}

    def read(self, address: int, size: int) -> bytes:
        return bytes(self.data.get(int(address) + index, 0) for index in range(int(size)))

    def write(self, address: int, payload: bytes) -> None:
        for index, value in enumerate(bytes(payload)):
            self.data[int(address) + index] = value

    def read_u32(self, address: int) -> int:
        return struct.unpack(">I", self.read(address, 4))[0]

    def write_u32(self, address: int, value: int) -> None:
        self.write(address, struct.pack(">I", int(value) & 0xFFFFFFFF))


def _script_op(operation: int, field_id: int, value: int) -> bytes:
    return bytes((0x04, int(operation) & 0xFF, 0x60, 0x00)) + struct.pack(">III", field_id, 0x3F000000, value)


def _script_set(field_id: int, value: int) -> bytes:
    return _script_op(0x01, field_id, value)


def _build_ryu_donkey_fixture(memory: SparseMemory):
    fighter = 0x9246B9C0
    table = 0x90896640
    action = 0x013B
    move_root = table + 0x21A94
    next_root = move_root + 0x57C

    memory.write_u32(fighter + 0x01E0, table)
    memory.write_u32(fighter + 0x01E8, action)
    memory.write_u32(table + action * 4, move_root - table)
    memory.write_u32(table + (action + 1) * 4, next_root - table)

    memory.write(move_root + 0x34, _script_set(0x240, 0x0C))
    memory.write(move_root + 0x44, _script_set(0x244, 0x40))

    # Deliberately place a different pair after the next action root. A fixed
    # oversized scan would steal this pair from the following move.
    memory.write(next_root + 0x20, _script_set(0x240, 0x09))
    memory.write(next_root + 0x30, _script_set(0x244, 0x00040001))
    return fighter, table, action, move_root, next_root


def test_recomp_route_reads_240_244_and_stops_at_next_action():
    clear_attack_property_runtime_cache()
    memory = SparseMemory()
    fighter, table, action, move_root, next_root = _build_ryu_donkey_fixture(memory)

    result = resolve_live_attack_definition(
        fighter,
        action,
        read_u32=memory.read_u32,
        read_block=memory.read,
    )

    assert result["status"] == "OK"
    assert result["chr_tbl"] == table
    assert result["move_root"] == move_root
    assert result["scan_size"] == next_root - move_root
    assert result["property_a"] == 0x0C
    assert result["property_b"] == 0x40
    assert result["phase_count"] == 1


def test_profiler_prefers_live_fighter_action_over_stale_snapshot(tmp_path: Path):
    clear_attack_property_runtime_cache()
    memory = SparseMemory()
    fighter, _table, action, _move_root, _next_root = _build_ryu_donkey_fixture(memory)

    profiler = profiler_module.RuntimeAttackPropertyProfiler(
        path=tmp_path / "profiles.json",
        event_path=tmp_path / "events.csv",
        read_u32=memory.read_u32,
        read_block=memory.read,
        write_block=lambda _address, _payload: False,
        is_valid_addr=lambda address: bool(address),
        start_worker=False,
        emit_console=False,
        enable_resolver_hook=False,
    )
    snaps = {
        "P1-C1": {
            "base": fighter,
            "id": 0,
            "name": "Ryu",
            "attA": 0x0001,
            "mv_id_display": 0x0001,
            "mv_label_display": "Donkey H",
            "damage_point_active": True,
            "damage_combo_lane_active": True,
        }
    }

    profiler.update(snaps, frame=100)
    row = snaps["P1-C1"]
    assert row["attack_property_display_active"] is True
    assert row["attack_property_display_source"] == "move_definition"
    assert row["attack_property_packet_action_id"] == action
    assert row["attack_property_live_a"] == 0x0C
    assert row["attack_property_live_b"] == 0x40
    assert row["attack_property_definition_status"] == "OK"
    assert row["attack_property_definition_action_source"] == "fighter+0x1E8"


def test_runtime_actor_offsets_follow_recomp_b_then_a_order():
    assert profiler_module.OFF_PROPERTY_B == 0x0080
    assert profiler_module.OFF_PROPERTY_A == 0x0084


def test_recomp_route_replays_clear_or_and_b_mutations():
    clear_attack_property_runtime_cache()
    memory = SparseMemory()
    fighter, _table, action, move_root, _next_root = _build_ryu_donkey_fixture(memory)

    memory.write(move_root + 0x54, _script_op(0x17, 0x240, 0x00000038))
    memory.write(move_root + 0x64, _script_op(0x17, 0x240, 0x80042F00))
    memory.write(move_root + 0x74, _script_op(0x15, 0x240, 0x80000200))
    memory.write(move_root + 0x84, _script_op(0x15, 0x244, 0x00040000))

    result = resolve_live_attack_definition(
        fighter,
        action,
        read_u32=memory.read_u32,
        read_block=memory.read,
    )

    assert result["status"] == "OK"
    assert result["phase_count"] == 1
    phase = result["phases"][0]
    assert phase["property_a"] == 0x0C
    assert phase["property_a_initial"] == 0x0C
    assert phase["property_b_initial"] == 0x40
    assert phase["property_b"] == 0x00040040
    assert phase["result_clear_mask"] == 0x80042F00
    assert phase["hit_result_raw"] == 0x80000200
    assert phase["hit_reaction"] == 0x800002
    assert [row["operation_name"] for row in phase["operations"]] == [
        "SET", "SET", "CLEAR", "CLEAR", "OR", "OR",
    ]


def test_recomp_route_preserves_identical_multihit_phases():
    clear_attack_property_runtime_cache()
    memory = SparseMemory()
    fighter = 0x9246B9C0
    table = 0x90896640
    action = 0x010A
    move_root = table + 0x19000
    next_root = move_root + 0x200

    memory.write_u32(fighter + 0x01E0, table)
    memory.write_u32(fighter + 0x01E8, action)
    memory.write_u32(table + action * 4, move_root - table)
    memory.write_u32(table + (action + 1) * 4, next_root - table)
    memory.write(move_root + 0x20, _script_set(0x240, 0x12))
    memory.write(move_root + 0x30, _script_set(0x244, 0x40))
    memory.write(move_root + 0x80, _script_set(0x240, 0x12))
    memory.write(move_root + 0x90, _script_set(0x244, 0x40))

    result = resolve_live_attack_definition(
        fighter,
        action,
        read_u32=memory.read_u32,
        read_block=memory.read,
    )

    assert result["status"] == "OK"
    assert result["phase_count"] == 2
    assert [phase["property_a"] for phase in result["phases"]] == [0x12, 0x12]
    assert [phase["property_b"] for phase in result["phases"]] == [0x40, 0x40]
    assert [phase["phase_index"] for phase in result["phases"]] == [1, 2]


def test_live_projectile_registry_reads_native_b_then_a():
    memory = SparseMemory()
    fighter = 0x9246B9C0
    actor = 0x91B159B4
    linked = 0x91A6B774
    memory.write_u32(LIVE_PROJECTILE_ACTOR_TABLE, actor)
    memory.write_u32(actor + 0x130, fighter)
    memory.write_u32(actor + 0x134, 0x0031)
    memory.write_u32(actor + 0x13C, linked)
    memory.write_u32(fighter + 0x01E8, 0x0162)
    memory.write_u32(linked + 0x080, 0x01000040)  # Property B
    memory.write_u32(linked + 0x084, 0x0000000C)  # Property A: mid heavy
    memory.write_u32(linked + 0x35C, 0x00000014)
    memory.write_u32(linked + 0x360, 0x00040040)

    rows = collect_live_projectile_properties(
        {"P1-C1": fighter},
        read_u32=memory.read_u32,
        read_block=memory.read,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["owner_slot"] == "P1-C1"
    assert row["owner_action_id"] == 0x0162
    assert row["projectile_id"] == 0x31
    assert row["property_b"] == 0x01000040
    assert row["property_a"] == 0x0C
    assert row["phase_property_a"] == 0x14
    assert row["phase_property_b"] == 0x00040040
    assert row["property_layout"] == "B80_A84"
    assert row["registry_source"] == "actor_table"
    assert row["raw_property_80"] == 0x01000040
    assert row["raw_property_84"] == 0x0C


def test_live_projectile_registry_has_guarded_reverse_layout_fallback():
    memory = SparseMemory()
    fighter = 0x9246B9C0
    actor = 0x91B159B4
    linked = 0x91A6B774
    memory.write_u32(LIVE_PROJECTILE_ACTOR_TABLE, actor)
    memory.write_u32(actor + 0x130, fighter)
    memory.write_u32(actor + 0x134, 0x0032)
    memory.write_u32(actor + 0x13C, linked)
    memory.write_u32(linked + 0x080, 0x00000014)  # Alternate actor family: A at +80
    memory.write_u32(linked + 0x084, 0x01000040)  # B at +84

    rows = collect_live_projectile_properties(
        {"P1-C1": fighter},
        read_u32=memory.read_u32,
        read_block=memory.read,
    )
    assert len(rows) == 1
    assert rows[0]["property_a"] == 0x14
    assert rows[0]["property_b"] == 0x01000040
    assert rows[0]["property_layout"] == "A80_B84_FALLBACK"


def test_projectile_only_action_replaces_stale_fighter_definition(tmp_path: Path, monkeypatch):
    fighter = 0x9246B9C0
    state = {"action": 0x0161, "projectiles": []}

    def fake_definition(_base, action, **_kwargs):
        if int(action) == 0x0161:
            return {
                "status": "OK",
                "property_a": 0x0C,
                "property_b": 0x01000040,
                "phase_count": 1,
                "phases": [{"property_a": 0x0C, "property_b": 0x01000040}],
            }
        return {"status": "NO_PROPERTY_COMMAND", "action_id": int(action)}

    monkeypatch.setattr(profiler_module, "resolve_live_attack_definition", fake_definition)
    monkeypatch.setattr(
        profiler_module,
        "collect_live_projectile_properties",
        lambda _owners, **_kwargs: list(state["projectiles"]),
    )

    profiler = profiler_module.RuntimeAttackPropertyProfiler(
        path=tmp_path / "profiles.json",
        event_path=tmp_path / "events.csv",
        read_u32=lambda address: state["action"] if address == fighter + 0x01E8 else 0,
        read_block=lambda _address, size: bytes(size),
        write_block=lambda _address, _payload: False,
        is_valid_addr=lambda address: bool(address),
        start_worker=False,
        emit_console=False,
        enable_resolver_hook=False,
    )
    snaps = {
        "P1-C1": {
            "base": fighter, "id": 0, "name": "Ryu",
            "attA": 0x0161, "mv_id_display": 0x0161,
            "mv_label_display": "Tatsu Super",
        }
    }
    profiler.update(snaps, frame=1)
    assert snaps["P1-C1"]["attack_property_packet_action_name"] == "Tatsu Super"

    state["action"] = 0x0162
    state["projectiles"] = [{
        "owner_slot": "P1-C1", "owner_base": fighter,
        "owner_action_id": 0x0162, "projectile_id": 0x31,
        "actor": 0x91B159B4, "linked": 0x91A6B774,
        "property_a": 0x0C, "property_b": 0x01000040,
        "phase_property_a": 0, "phase_property_b": 0,
    }]
    snaps["P1-C1"].update({
        "attA": 0x0162, "mv_id_display": 0x0162,
        "mv_label_display": "Shinkuu",
    })
    profiler.update(snaps, frame=2)
    row = snaps["P1-C1"]
    assert row["attack_property_display_source"] == "live_projectile_actor"
    assert row["attack_property_packet_state"] == "LIVE PROJECTILE"
    assert row["attack_property_packet_action_name"] == "Shinkuu"
    assert row["attack_property_phases"] == []
    assert row["attack_property_projectile_count"] == 1
    assert row["attack_property_projectiles"][0]["property_a"] == 0x0C
    assert row["attack_property_live_b"] == 0x01000040

    state["projectiles"] = []
    state["action"] = 0x0001
    snaps["P1-C1"].update({
        "attA": 0x0001, "mv_id_display": 0x0001,
        "mv_label_display": "Idle",
    })
    profiler.update(snaps, frame=3)
    assert snaps["P1-C1"]["attack_property_display_active"] is True
    assert snaps["P1-C1"]["attack_property_display_source"] == "live_projectile_latched"
    assert snaps["P1-C1"]["attack_property_packet_state"] == "LAST PROJECTILE"
    assert snaps["P1-C1"]["attack_property_packet_action_name"] == "Shinkuu"
    assert snaps["P1-C1"]["attack_property_projectiles"][0]["projectile_live"] is False

    profiler.update(snaps, frame=123)
    assert snaps["P1-C1"]["attack_property_display_active"] is False
    assert snaps["P1-C1"]["attack_property_packet_action_name"] == ""
