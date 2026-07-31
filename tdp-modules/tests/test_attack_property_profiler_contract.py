from __future__ import annotations

import importlib
import importlib.util
import struct
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROFILER = ROOT / "tvcgui" / "features" / "training" / "attack_property_profiler.py"
PATTERNS = ROOT / "tvcgui" / "features" / "frame_data" / "patterns.py"
MANAGER = ROOT / "tvcgui" / "features" / "overlay" / "manager.py"
MAIN = ROOT / "main.py"
DUMPER = ROOT / "tvcgui" / "features" / "frame_data" / "dumper.py"


def _load_module(path: Path, name: str):
    if path == PROFILER:
        return importlib.import_module("tvcgui.features.training.attack_property_profiler")
    if path == PATTERNS:
        return importlib.import_module("tvcgui.features.frame_data.patterns")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class SparseMemory:
    def __init__(self):
        self.data: dict[int, int] = {}

    def read(self, address: int, size: int) -> bytes:
        return bytes(self.data.get(int(address) + index, 0) for index in range(int(size)))

    def write(self, address: int, payload: bytes) -> bool:
        for index, value in enumerate(bytes(payload)):
            self.data[int(address) + index] = value
        return True

    def read_u32(self, address: int) -> int:
        return struct.unpack(">I", self.read(address, 4))[0]

    def write_u32(self, address: int, value: int) -> None:
        self.write(address, struct.pack(">I", int(value) & 0xFFFFFFFF))


def _put_fighter(memory: SparseMemory, module, base: int, char_id: int, action_id: int) -> None:
    memory.write_u32(base + module.FIGHTER_CHAR_ID, char_id)
    memory.write(base + module.FIGHTER_ACTION_FRAME, struct.pack(">f", 6.0))
    memory.write_u32(base + module.FIGHTER_ACTION_ID, action_id)
    memory.write_u32(base + module.FIGHTER_POINT_ACTIVE, 1)
    memory.write_u32(base + module.FIGHTER_COMBO_LANE_ACTIVE, 1)


def _publish_event(
    memory: SparseMemory,
    module,
    sequence: int,
    *,
    attacker: int,
    defender: int,
    action_id: int,
    prop_a: int,
    prop_b: int,
    damage: int,
    calc_output: int | None = None,
    calc_aux: int = 0,
    applied: int | None = None,
) -> None:
    entry = (
        module.RESOLVER_MAILBOX_ADDR
        + module.RESOLVER_MAILBOX_HEADER_SIZE
        + (sequence & (module.RESOLVER_RING_COUNT - 1)) * module.RESOLVER_ENTRY_SIZE
    )
    calc_value = damage if calc_output is None else calc_output
    applied_value = calc_value if applied is None else applied
    values = {
        module.RES_OFF_SEQUENCE: sequence,
        module.RES_OFF_ATTACKER: attacker,
        module.RES_OFF_DEFENDER: defender,
        module.RES_OFF_PROPERTY_B: prop_b,
        module.RES_OFF_PROPERTY_A: prop_a,
        module.RES_OFF_RESULT_PTR_A: 0x81230000,
        module.RES_OFF_RESULT_PTR_B: 0x81230004,
        module.RES_OFF_ROUTE_ARG: 1,
        module.RES_OFF_PACKET: 0x91A6B7D8,
        module.RES_OFF_CALLER_LR: module.RESOLVER_COLLISION_CALLER_LR,
        module.RES_OFF_AUTHORED_DAMAGE: damage,
        module.RES_OFF_PHASE_A: 0x24,
        module.RES_OFF_PHASE_B: 0x00040001,
        module.RES_OFF_RUNTIME_STATUS: 0x08,
        module.RES_OFF_PACKET_OWNER: attacker,
        module.RES_OFF_ACTION_ID: action_id,
        module.RES_OFF_DAMAGE_CALC_OUTPUT: calc_value,
        module.RES_OFF_DAMAGE_CALC_AUX: calc_aux,
        module.RES_OFF_CALC_COMPLETION_SEQUENCE: sequence,
        module.RES_OFF_APPLIED_DAMAGE: applied_value,
        module.RES_OFF_APPLICATION_SEQUENCE: sequence,
    }
    for offset, value in values.items():
        memory.write_u32(entry + offset, value)
    memory.write_u32(module.RESOLVER_MAILBOX_ADDR, sequence)


def test_property_a_decoder_splits_guard_and_strength():
    module = _load_module(PROFILER, "attack_property_profiler_decode")
    assert module.decode_property_a(0x09)["text"] == "Mid, Light Hit"
    assert module.decode_property_a(0x12)["text"] == "High, Medium Hit"
    assert module.decode_property_a(0x24)["text"] == "Low, Heavy Hit"
    assert module.decode_property_a(0x04)["text"] == "Unblockable, Heavy Hit"


def test_property_b_decoder_names_code_proven_routes():
    module = _load_module(PROFILER, "attack_property_profiler_b_decode")
    assert "Chip / result +0x0004" in module.decode_property_b(0x00000001)["confirmed"]
    assert "One-eighth chip route B" in module.decode_property_b(0x00040000)["confirmed"]
    assert "Pre-resolved collision route" in module.decode_property_b(0x00004000)["confirmed"]
    correlated = module.decode_property_b(0x01000040)
    assert correlated["correlated"] == ["Repeated-contact family (correlated)"]
    assert correlated["unknown_mask"] == 0


def test_native_combo_lane_decoder_is_exact():
    module = _load_module(PROFILER, "attack_property_profiler_lane_decode")
    active = module.decode_combo_scaling_lane(True)
    reserve = module.decode_combo_scaling_lane(False)
    assert active["loss_per_hit"] == 0.05
    assert active["floor"] == 0.35
    assert reserve["loss_per_hit"] == 0.03
    assert reserve["floor"] == 0.43


def test_frame_data_formatter_uses_packed_decoder():
    module = _load_module(PATTERNS, "attack_property_patterns_decode")
    assert module.fmt_attack_property(0x09) == "0x09 Mid, Light Hit"
    assert module.fmt_attack_property(0x04) == "0x04 Unblockable, Heavy Hit"


def test_resolver_stub_preserves_all_original_instructions_and_returns():
    module = _load_module(PROFILER, "attack_property_resolver_stub")
    entry_words = module.resolver_stub_words()
    exit_words = module.resolver_exit_stub_words()
    return_words = module.resolver_collision_return_stub_words()
    apply_words = module.resolver_apply_stub_words()
    assert entry_words[0] == module.RESOLVER_HOOK_ORIGINAL == 0x9421FF80
    assert exit_words[-2] == module.RESOLVER_EXIT_HOOK_ORIGINAL == 0x39610060
    assert return_words[-2] == module.RESOLVER_COLLISION_RETURN_HOOK_ORIGINAL == 0x38600005
    assert apply_words[-2] == module.RESOLVER_APPLY_HOOK_ORIGINAL == 0x80010014
    assert module.resolver_hook_word() == module._ppc_branch(module.RESOLVER_HOOK_ADDR, module.RESOLVER_CAVE_ADDR)
    assert module.resolver_exit_hook_word() == module._ppc_branch(module.RESOLVER_EXIT_HOOK_ADDR, module.RESOLVER_EXIT_CAVE_ADDR)
    assert module.resolver_collision_return_hook_word() == module._ppc_branch(module.RESOLVER_COLLISION_RETURN_HOOK_ADDR, module.RESOLVER_COLLISION_RETURN_CAVE_ADDR)
    assert module.resolver_apply_hook_word() == module._ppc_branch(module.RESOLVER_APPLY_HOOK_ADDR, module.RESOLVER_APPLY_CAVE_ADDR)
    assert module.RESOLVER_CAVE_ADDR + len(module.resolver_stub_bytes()) <= module.RESOLVER_EXIT_CAVE_ADDR
    assert module.RESOLVER_EXIT_CAVE_ADDR + len(module.resolver_exit_stub_bytes()) <= module.RESOLVER_COLLISION_RETURN_CAVE_ADDR
    assert module.RESOLVER_COLLISION_RETURN_CAVE_ADDR + len(module.resolver_collision_return_stub_bytes()) <= module.RESOLVER_APPLY_CAVE_ADDR
    assert module.RESOLVER_APPLY_CAVE_ADDR + len(module.resolver_apply_stub_bytes()) <= module.RESOLVER_MAILBOX_ADDR


def test_profiler_installs_hook_and_captures_each_resolver_event(tmp_path: Path):
    module = _load_module(PROFILER, "attack_property_resolver_capture")
    memory = SparseMemory()
    memory.write_u32(module.RESOLVER_HOOK_ADDR, module.RESOLVER_HOOK_ORIGINAL)
    memory.write_u32(module.RESOLVER_EXIT_HOOK_ADDR, module.RESOLVER_EXIT_HOOK_ORIGINAL)
    memory.write_u32(module.RESOLVER_COLLISION_RETURN_HOOK_ADDR, module.RESOLVER_COLLISION_RETURN_HOOK_ORIGINAL)
    memory.write_u32(module.RESOLVER_APPLY_HOOK_ADDR, module.RESOLVER_APPLY_HOOK_ORIGINAL)

    fighter = 0x9246B9C0
    victim = 0x927EB9E0
    _put_fighter(memory, module, fighter, 0, 0x0101)
    _put_fighter(memory, module, victim, 1, 0x0000)

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
    snaps = {
        "P1-C1": {
            "base": fighter,
            "id": 0,
            "name": "Ryu",
            "attA": 0x0101,
            "mv_label": "5A",
            "damage_point_active": True,
            "damage_combo_lane_active": True,
        },
        "P2-C1": {"base": victim, "id": 1, "name": "Ken"},
    }
    profiler.update(snaps, frame=123)

    assert profiler.sample_once() == 0
    assert memory.read_u32(module.RESOLVER_HOOK_ADDR) == module.resolver_hook_word()
    assert memory.read_u32(module.RESOLVER_EXIT_HOOK_ADDR) == module.resolver_exit_hook_word()
    assert memory.read_u32(module.RESOLVER_COLLISION_RETURN_HOOK_ADDR) == module.resolver_collision_return_hook_word()
    assert memory.read_u32(module.RESOLVER_APPLY_HOOK_ADDR) == module.resolver_apply_hook_word()
    assert memory.read(module.RESOLVER_CAVE_ADDR, len(module.resolver_stub_bytes())) == module.resolver_stub_bytes()
    assert memory.read(module.RESOLVER_EXIT_CAVE_ADDR, len(module.resolver_exit_stub_bytes())) == module.resolver_exit_stub_bytes()
    assert memory.read(module.RESOLVER_COLLISION_RETURN_CAVE_ADDR, len(module.resolver_collision_return_stub_bytes())) == module.resolver_collision_return_stub_bytes()
    assert memory.read(module.RESOLVER_APPLY_CAVE_ADDR, len(module.resolver_apply_stub_bytes())) == module.resolver_apply_stub_bytes()

    _publish_event(memory, module, 1, attacker=fighter, defender=victim, action_id=0x0101, prop_a=0x24, prop_b=0x40, damage=1800, calc_output=1300, calc_aux=153, applied=1224)
    assert profiler.sample_once() == 1
    _publish_event(memory, module, 2, attacker=fighter, defender=victim, action_id=0x0101, prop_a=0x24, prop_b=0x40, damage=1800, calc_output=1800, applied=1800)
    assert profiler.sample_once() == 1
    assert len(profiler.doc["events"]) == 2
    assert profiler.doc["events"][0]["authored_damage"] == 1800
    assert profiler.doc["events"][0]["damage_calc_output"] == 1300
    assert profiler.doc["events"][0]["applied_damage"] == 1224
    assert profiler.doc["events"][0]["resolved_damage"] == 1224
    assert profiler.doc["events"][0]["resolved_aux"] == 153

    profiler.update(snaps, frame=124)
    attack = snaps["P1-C1"]
    assert attack["attack_property_packet_state"] == "CONTACT"
    assert attack["attack_property_packet_source"] == "resolver_mailbox"
    assert attack["attack_property_event_sequence"] == 2
    assert attack["attack_property_packet_action_name"] == "5A"
    assert attack["attack_property_live_a"] == 0x24
    assert attack["attack_property_live_b"] == 0x40
    assert attack["attack_property_live_damage"] == 1800
    assert attack["attack_property_live_authored_damage"] == 1800
    assert attack["attack_property_live_damage_calc_output"] == 1800
    assert attack["attack_property_native_damage_calc_complete"] is True
    assert attack["attack_property_live_applied_damage"] == 1800
    assert attack["attack_property_live_resolved_damage"] == 1800
    assert attack["attack_property_native_damage_complete"] is True
    assert attack["attack_property_live_victim_slot"] == "P2-C1"
    assert attack["attack_property_resolver_hook_state"] == "READY"
    assert attack["attack_property_live_scaling_floor"] == 0.35

    assert profiler.flush()
    assert memory.read_u32(module.RESOLVER_HOOK_ADDR) == module.RESOLVER_HOOK_ORIGINAL
    assert memory.read_u32(module.RESOLVER_EXIT_HOOK_ADDR) == module.RESOLVER_EXIT_HOOK_ORIGINAL
    assert memory.read_u32(module.RESOLVER_COLLISION_RETURN_HOOK_ADDR) == module.RESOLVER_COLLISION_RETURN_HOOK_ORIGINAL
    assert memory.read_u32(module.RESOLVER_APPLY_HOOK_ADDR) == module.RESOLVER_APPLY_HOOK_ORIGINAL
    assert (tmp_path / "profiles.json").exists()
    assert (tmp_path / "events.csv").exists()


def test_profiler_refuses_to_overwrite_unexpected_resolver_code(tmp_path: Path):
    module = _load_module(PROFILER, "attack_property_resolver_mismatch")
    memory = SparseMemory()
    memory.write_u32(module.RESOLVER_HOOK_ADDR, 0x60000000)
    memory.write_u32(module.RESOLVER_EXIT_HOOK_ADDR, module.RESOLVER_EXIT_HOOK_ORIGINAL)
    memory.write_u32(module.RESOLVER_COLLISION_RETURN_HOOK_ADDR, module.RESOLVER_COLLISION_RETURN_HOOK_ORIGINAL)
    memory.write_u32(module.RESOLVER_APPLY_HOOK_ADDR, module.RESOLVER_APPLY_HOOK_ORIGINAL)
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
    profiler.update({"P1-C1": {"base": 0x9246B9C0, "id": 0, "name": "Ryu"}}, frame=1)
    assert profiler.sample_once() == 0
    assert profiler._resolver_hook_state == "MISMATCH"
    assert memory.read_u32(module.RESOLVER_HOOK_ADDR) == 0x60000000
    assert profiler.flush()


def test_main_arms_and_flushes_profiler():
    source = MAIN.read_text(encoding="utf-8")
    assert "RuntimeAttackPropertyProfiler" in source
    assert "runtime_attack_property_profiler.update" in source
    assert "runtime_attack_property_profiler.flush" in source


def test_overlay_short_label_is_structural():
    source = MANAGER.read_text(encoding="utf-8")
    assert "def _known_attack_property" in source
    assert "guard = packed & 0x38" in source
    assert "strength = packed & 0x07" in source


def test_false_raw_command_labels_are_removed():
    source = DUMPER.read_text(encoding="utf-8")
    assert "flag clear/mask op" not in source
    assert "flag add/or op" not in source


def test_profiler_uses_low_overhead_capture_rate():
    module = _load_module(PROFILER, "attack_property_profiler_poll_rate")
    assert module.DEFAULT_POLL_HZ == 240.0


def test_dual_use_44a4_is_documented_without_losing_baroque_scan():
    scanner = (ROOT / "tvcgui" / "tools" / "scanners" / "normal_scanner.py").read_text(encoding="utf-8")
    protection = (ROOT / "tvcgui" / "features" / "training" / "protection_profiler.py").read_text(encoding="utf-8")
    assert '"baroque": {0x444C, 0x44A4}' in scanner
    assert "dual-purpose" in scanner
    assert "shared by Baroque and proration" in protection


def test_move_definition_fallback_changes_with_current_action(tmp_path: Path, monkeypatch):
    module = _load_module(PROFILER, "attack_property_move_definition_fallback")
    properties = {0x0106: 0x0C, 0x0101: 0x09}
    monkeypatch.setattr(
        module,
        "resolve_live_attack_definition",
        lambda _base, action, **_kwargs: (
            {
                "status": "OK",
                "action_id": int(action),
                "property_a": properties[int(action)],
                "property_b": 0x40,
                "phase_count": 1,
                "phases": [{"property_a": properties[int(action)], "property_b": 0x40}],
            }
            if int(action) in properties
            else {"status": "NO_PROPERTY_COMMAND", "action_id": int(action)}
        ),
    )
    profiler = module.RuntimeAttackPropertyProfiler(
        path=tmp_path / "profiles.json",
        event_path=tmp_path / "events.csv",
        read_u32=lambda _address: None,
        read_block=lambda _address, _size: b"",
        write_block=lambda _address, _payload: False,
        is_valid_addr=lambda address: bool(address),
        start_worker=False,
        emit_console=False,
        enable_resolver_hook=False,
    )
    snaps = {
        "P1-C1": {
            "base": 0x9246B9C0,
            "id": 12,
            "name": "Ryu",
            "attA": 0x0106,
            "mv_label": "Tatsu H",
            "damage_point_active": True,
            "damage_combo_lane_active": True,
        }
    }
    profiler.update(snaps, frame=10)
    first = snaps["P1-C1"]
    assert first["attack_property_display_active"] is True
    assert first["attack_property_display_source"] == "move_definition"
    assert first["attack_property_packet_state"] == "CURRENT MOVE"
    assert first["attack_property_packet_action_name"] == "Tatsu H"
    assert first["attack_property_live_a"] == 0x0C

    first["attA"] = 0x0101
    first["mv_label"] = "5A"
    profiler.update(snaps, frame=11)
    assert first["attack_property_packet_action_name"] == "5A"
    assert first["attack_property_live_a"] == 0x09
    second_sequence = first["attack_property_event_sequence"]

    first["attA"] = 0x0001
    first["mv_label"] = "Idle"
    profiler.update(snaps, frame=12)
    assert first["attack_property_display_source"] == "move_definition_latched"
    assert first["attack_property_packet_state"] == "LAST MOVE"
    assert first["attack_property_packet_action_name"] == "5A"

    first["attA"] = 0x0101
    first["mv_label"] = "5A"
    profiler.update(snaps, frame=13)
    assert first["attack_property_display_source"] == "move_definition"
    assert first["attack_property_packet_state"] == "CURRENT MOVE"
    assert first["attack_property_event_sequence"] > second_sequence
    assert profiler.flush()


def test_move_definition_exports_every_native_phase(tmp_path: Path, monkeypatch):
    module = _load_module(PROFILER, "attack_property_all_native_phases")
    monkeypatch.setattr(
        module,
        "resolve_live_attack_definition",
        lambda _base, action, **_kwargs: {
            "status": "OK",
            "action_id": int(action),
            "property_a": 0x12,
            "property_b": 0x40,
            "phase_count": 3,
            "phases": [
                {"property_a": 0x12, "property_b": 0x40, "script_offset": 0x20},
                {"property_a": 0x12, "property_b": 0x40040, "script_offset": 0x90},
                {"property_a": 0x24, "property_b": 0x08, "script_offset": 0x120},
            ],
        },
    )
    profiler = module.RuntimeAttackPropertyProfiler(
        path=tmp_path / "profiles.json",
        event_path=tmp_path / "events.csv",
        read_u32=lambda _address: None,
        read_block=lambda _address, _size: b"",
        write_block=lambda _address, _payload: False,
        is_valid_addr=lambda address: bool(address),
        start_worker=False,
        emit_console=False,
        enable_resolver_hook=False,
    )
    snaps = {
        "P1-C1": {
            "base": 0x9246B9C0,
            "id": 12,
            "name": "Ryu",
            "attA": 0x010C,
            "mv_label": "6B",
            "damage_point_active": True,
            "damage_combo_lane_active": True,
        }
    }

    profiler.update(snaps, frame=20)

    attack = snaps["P1-C1"]
    assert attack["attack_property_phase_count"] == 3
    assert [row["property_a"] for row in attack["attack_property_phases"]] == [0x12, 0x12, 0x24]
    assert attack["attack_property_phases"][1]["property_b"] == 0x40040
    assert attack["attack_property_phases"][2]["property_b_text"]
    assert profiler.flush()
