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


def _put_u32(blob: bytearray, offset: int, value: int, *, start: int = 0) -> None:
    struct.pack_into(">I", blob, offset - start, value & 0xFFFFFFFF)


def test_property_a_decoder_splits_guard_and_strength():
    module = _load_module(PROFILER, "attack_property_profiler_decode")
    assert module.decode_property_a(0x09)["text"] == "Mid, Light Hit"
    assert module.decode_property_a(0x12)["text"] == "High, Medium Hit"
    assert module.decode_property_a(0x24)["text"] == "Low, Heavy Hit"
    assert module.decode_property_a(0x04)["text"] == "Unblockable, Heavy Hit"


def test_frame_data_formatter_uses_packed_decoder():
    module = _load_module(PATTERNS, "attack_property_patterns_decode")
    assert module.fmt_attack_property(0x09) == "0x09 Mid, Light Hit"
    assert module.fmt_attack_property(0x04) == "0x04 Unblockable, Heavy Hit"
    assert module.ATTACK_PROPERTY_VALUES[0x12] == "High, Medium Hit"
    assert module.ATTACK_PROPERTY_VALUES[0x02] == "Unblockable, Medium Hit"


def test_profiler_captures_active_native_actor_transition(tmp_path: Path):
    module = _load_module(PROFILER, "attack_property_profiler_capture")

    fighter = 0x9246B9C0
    victim = 0x92B6BA00
    sentinel = fighter + module.OWNER_LIST_OFFSET
    actor = 0x91A6B774
    node = actor + module.LIST_NODE_OFFSET

    words = {sentinel + module.LIST_NEXT_OFFSET: node}
    actor_blob = bytearray(module.ACTOR_BLOCK_SIZE)
    fighter_blob = bytearray(module.FIGHTER_META_SIZE)

    _put_u32(fighter_blob, module.FIGHTER_CHAR_ID, 12, start=module.FIGHTER_META_START)
    struct.pack_into(">f", fighter_blob, module.FIGHTER_ACTION_FRAME - module.FIGHTER_META_START, 6.0)
    _put_u32(fighter_blob, module.FIGHTER_ACTION_ID, 0x0101, start=module.FIGHTER_META_START)

    _put_u32(actor_blob, module.OFF_LIST_PREV, sentinel, start=module.ACTOR_BLOCK_START)
    _put_u32(actor_blob, module.OFF_LIST_NEXT, sentinel, start=module.ACTOR_BLOCK_START)
    _put_u32(actor_blob, module.OFF_OWNER, fighter, start=module.ACTOR_BLOCK_START)
    _put_u32(actor_blob, module.OFF_VICTIM, victim, start=module.ACTOR_BLOCK_START)
    _put_u32(actor_blob, module.OFF_PROPERTY_A, 0x09, start=module.ACTOR_BLOCK_START)
    _put_u32(actor_blob, module.OFF_PROPERTY_B, 0x40, start=module.ACTOR_BLOCK_START)
    _put_u32(actor_blob, module.OFF_RUNTIME_STATUS_20, 0x08, start=module.ACTOR_BLOCK_START)
    _put_u32(actor_blob, module.OFF_BASE_DAMAGE, 100, start=module.ACTOR_BLOCK_START)

    def read_u32(address: int):
        return words.get(address)

    def read_block(address: int, size: int) -> bytes:
        if address == fighter + module.FIGHTER_META_START and size == module.FIGHTER_META_SIZE:
            return bytes(fighter_blob)
        if address == actor + module.ACTOR_BLOCK_START and size == module.ACTOR_BLOCK_SIZE:
            return bytes(actor_blob)
        return b""

    profiler = module.RuntimeAttackPropertyProfiler(
        path=tmp_path / "profiles.json",
        event_path=tmp_path / "events.csv",
        read_u32=read_u32,
        read_block=read_block,
        is_valid_addr=lambda address: bool(address),
        start_worker=False,
        emit_console=False,
    )
    snaps = {
        "P1-C1": {"base": fighter, "id": 12, "name": "Ryu", "attA": 0x0101, "mv_label": "5A"},
        "P2-C1": {"base": victim, "id": 13, "name": "Chun-Li"},
    }
    profiler.update(snaps, frame=123)

    assert profiler.sample_once() == 1
    assert profiler.sample_once() == 0
    event = profiler.doc["events"][-1]
    assert event["owner_slot"] == "P1-C1"
    assert event["victim_slot"] == "P2-C1"
    assert event["owner_action_id"] == 0x0101
    assert event["owner_action_frame"] == 5
    assert event["property_a"] == 0x09
    assert event["property_b"] == 0x40
    assert event["runtime_status_20"] == 0x08
    assert event["base_damage"] == 100

    _put_u32(actor_blob, module.OFF_PROPERTY_B, 0x41, start=module.ACTOR_BLOCK_START)
    assert profiler.sample_once() == 1
    assert profiler.doc["events"][-1]["property_b"] == 0x41
    assert profiler.flush()
    assert (tmp_path / "profiles.json").exists()
    assert (tmp_path / "events.csv").exists()


def test_profiler_skips_empty_owner_list(tmp_path: Path):
    module = _load_module(PROFILER, "attack_property_profiler_empty")
    fighter = 0x9246B9C0
    sentinel = fighter + module.OWNER_LIST_OFFSET

    profiler = module.RuntimeAttackPropertyProfiler(
        path=tmp_path / "profiles.json",
        event_path=tmp_path / "events.csv",
        read_u32=lambda address: sentinel if address == sentinel + module.LIST_NEXT_OFFSET else None,
        read_block=lambda _address, _size: b"",
        is_valid_addr=lambda address: bool(address),
        start_worker=False,
        emit_console=False,
    )
    profiler.update({"P1-C1": {"base": fighter, "id": 12, "name": "Ryu"}}, frame=1)
    assert profiler.sample_once() == 0
    assert profiler.doc["events"] == []


def test_main_arms_and_flushes_profiler():
    source = MAIN.read_text(encoding="utf-8")
    assert "RuntimeAttackPropertyProfiler" in source
    assert "runtime_attack_property_profiler.update" in source
    assert "runtime_attack_property_profiler.flush" in source
    assert "runtime_attack_property_profiles.json" in source


def test_overlay_short_label_is_structural():
    source = MANAGER.read_text(encoding="utf-8")
    assert "def _known_attack_property" in source
    assert "guard = packed & 0x38" in source
    assert "strength = packed & 0x07" in source


def test_false_raw_command_labels_are_removed():
    source = DUMPER.read_text(encoding="utf-8")
    assert 'flag clear/mask op' not in source
    assert 'flag add/or op' not in source


def test_profiler_uses_low_overhead_capture_rate():
    module = _load_module(PROFILER, "attack_property_profiler_poll_rate")
    assert module.DEFAULT_POLL_HZ == 240.0
