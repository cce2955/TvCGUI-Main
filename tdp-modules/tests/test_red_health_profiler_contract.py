from __future__ import annotations

import csv
import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE_NAME = "tvcgui.features.training.red_health_profiler"
MAIN = ROOT / "main.py"
CONSTANTS = ROOT / "tvcgui" / "core" / "constants.py"
CONFIG = ROOT / "tvcgui" / "core" / "config.py"


def _module():
    return importlib.import_module(MODULE_NAME)


def test_single_damage_request_generates_native_forty_percent_red_with_truncation():
    module = _module()
    assert module.predict_normal_red_health(1000) == {
        "damage": 1000,
        "current_loss": 1000,
        "auxiliary_loss": 600,
        "red_generated": 400,
    }
    assert module.predict_normal_red_health(1)["red_generated"] == 1
    assert module.predict_normal_red_health(3)["auxiliary_loss"] == 1
    assert module.predict_normal_red_health(3)["red_generated"] == 2


def test_ten_percent_current_only_cost_is_nonlethal_and_fully_recoverable():
    module = _module()
    normal = module.predict_current_only_cost(48000, 48000)
    assert normal["requested_cost"] == 4800
    assert normal["current_after"] == 43200
    assert normal["red_generated"] == 4800

    near_ko = module.predict_current_only_cost(48000, 1000)
    assert near_ko["current_after"] == 1
    assert near_ko["actual_cost"] == 999


def test_reserve_recovery_request_uses_point_zero_one_percent_of_max():
    module = _module()
    assert module.predict_reserve_recovery_step(48000) == 4
    assert module.predict_reserve_recovery_step(46000) == 4
    assert module.predict_reserve_recovery_step(44000) == 4


def test_runtime_profiler_classifies_damage_current_only_cost_and_baroque(tmp_path: Path):
    module = _module()
    attacker_base = 0x9246B9C0
    victim_base = 0x927EB9E0
    actor = 0x91A6B774
    values = {
        attacker_base + module.POINT_FLAG_OFFSET: 1,
        attacker_base + module.BAROQUE_ACTIVE_OFFSET: 0,
        attacker_base + module.BAROQUE_RED_SPENT_OFFSET: 0,
        victim_base + module.POINT_FLAG_OFFSET: 1,
        victim_base + module.BAROQUE_ACTIVE_OFFSET: 0,
        victim_base + module.BAROQUE_RED_SPENT_OFFSET: 0,
        actor + 0x34: victim_base,
    }

    def read_u32(address: int):
        return values.get(address, 0)

    profiler = module.RuntimeRedHealthProfiler(
        path=tmp_path / "profiles.json",
        event_path=tmp_path / "events.csv",
        read_u32=read_u32,
        read_block=lambda _address, _size: b"",
        emit_console=False,
    )
    snaps = {
        "P1-C1": {
            "base": attacker_base,
            "id": 12,
            "name": "Ryu",
            "max": 48000,
            "cur": 48000,
            "aux": 48000,
            "attA": 0x0102,
            "mv_label": "5C",
            "attack_property_live_actor": actor,
            "attack_property_live_damage": 1000,
            "attack_property_live_a": 0x0C,
            "attack_property_live_b": 0x40,
        },
        "P2-C1": {
            "base": victim_base,
            "id": 1,
            "name": "Chun-Li",
            "max": 10000,
            "cur": 10000,
            "aux": 10000,
        },
    }
    assert profiler.update(snaps, frame=10, now=10.0) is False

    snaps["P2-C1"]["cur"] = 9000
    snaps["P2-C1"]["aux"] = 9400
    assert profiler.update(snaps, frame=11, now=11.0) is True
    damage = profiler.doc["events"][-1]
    assert damage["event_kind"] == "normal_damage_red_generation"
    assert damage["damage_observed"] == 1000
    assert damage["aux_loss_observed"] == 600
    assert damage["red_generated_observed"] == 400
    assert damage["predicted_red_generated"] == 400
    assert damage["prediction_match"] is True
    assert damage["attacker_slot"] == "P1-C1"

    snaps["P2-C1"]["cur"] = 8000
    assert profiler.update(snaps, frame=12, now=12.0) is True
    cost = profiler.doc["events"][-1]
    assert cost["event_kind"] == "ten_percent_current_only_cost"
    assert cost["red_after"] == 1400

    values[victim_base + module.BAROQUE_ACTIVE_OFFSET] = 1
    values[victim_base + module.BAROQUE_RED_SPENT_OFFSET] = 1400
    snaps["P2-C1"]["aux"] = 8000
    assert profiler.update(snaps, frame=13, now=13.0) is True
    baroque = profiler.doc["events"][-1]
    assert baroque["event_kind"] == "baroque_consume"
    assert baroque["red_before"] == 1400
    assert baroque["red_after"] == 0
    assert baroque["prediction_match"] is True

    assert profiler.flush()
    with (tmp_path / "events.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 3


def test_runtime_profiler_marks_inactive_recoverable_heal_as_reserve_recovery(tmp_path: Path):
    module = _module()
    base = 0x92B6BA00
    values = {
        base + module.POINT_FLAG_OFFSET: 0,
        base + module.BAROQUE_ACTIVE_OFFSET: 0,
        base + module.BAROQUE_RED_SPENT_OFFSET: 0,
    }
    profiler = module.RuntimeRedHealthProfiler(
        path=tmp_path / "profiles.json",
        event_path=tmp_path / "events.csv",
        read_u32=lambda address: values.get(address, 0),
        read_block=lambda _address, _size: b"",
        emit_console=False,
    )
    snap = {
        "P1-C2": {
            "base": base,
            "id": 13,
            "name": "Roll",
            "max": 48000,
            "cur": 40000,
            "aux": 42000,
        }
    }
    assert profiler.update(snap, frame=1, now=1.0) is False
    snap["P1-C2"]["cur"] = 40004
    assert profiler.update(snap, frame=2, now=2.0) is True
    event = profiler.doc["events"][-1]
    assert event["event_kind"] == "reserve_recovery"
    assert event["predicted_reserve_step"] == 4
    assert event["prediction_match"] is True


def test_main_arms_updates_and_flushes_red_health_profiler():
    source = MAIN.read_text(encoding="utf-8")
    assert "RuntimeRedHealthProfiler" in source
    assert "runtime_red_health_profiler.update" in source
    assert "runtime_red_health_profiler.flush" in source
    assert "runtime_red_health_events.csv" in source


def test_health_offsets_are_documented_as_full_big_endian_words():
    constants = CONSTANTS.read_text(encoding="utf-8")
    config = CONFIG.read_text(encoding="utf-8")
    assert "recoverable-health" in constants
    assert "queued current-HP delta" in config
    assert "pooled “red life”" not in config


def test_inactive_healing_after_red_is_gone_raises_current_and_ceiling_together(tmp_path: Path):
    module = _module()
    base = 0x92B6BA00
    values = {
        base + module.POINT_FLAG_OFFSET: 0,
        base + module.BAROQUE_ACTIVE_OFFSET: 0,
        base + module.BAROQUE_RED_SPENT_OFFSET: 0,
    }
    profiler = module.RuntimeRedHealthProfiler(
        path=tmp_path / "profiles.json",
        event_path=tmp_path / "events.csv",
        read_u32=lambda address: values.get(address, 0),
        read_block=lambda _address, _size: b"",
        emit_console=False,
    )
    snap = {
        "P1-C2": {
            "base": base,
            "id": 13,
            "name": "Roll",
            "max": 48000,
            "cur": 42000,
            "aux": 42000,
        }
    }
    assert profiler.update(snap, frame=1, now=1.0) is False
    snap["P1-C2"]["cur"] = 42004
    snap["P1-C2"]["aux"] = 42004
    assert profiler.update(snap, frame=2, now=2.0) is True
    event = profiler.doc["events"][-1]
    assert event["event_kind"] == "reserve_full_recovery"
    assert event["predicted_reserve_step"] == 4
    assert event["prediction_match"] is True
