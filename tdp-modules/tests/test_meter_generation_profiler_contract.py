from __future__ import annotations

import csv
import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MODULE_NAME = "tvcgui.features.training.meter_generation_profiler"
MAIN = ROOT / "main.py"
METER = ROOT / "tvcgui" / "features" / "combat" / "meter.py"


def _module():
    return importlib.import_module(MODULE_NAME)


def test_native_hit_formula_keeps_all_rounding_stages():
    module = _module()
    result = module.predict_native_hit_meter(2080, 0x0C, 0x00000040)
    assert result["adjusted_damage"] == 2080
    assert result["pre_quarter_gain"] == 741
    assert result["shared_pool"] == 185
    assert result["requested_delta"] == 185
    assert result["final_delta"] == 240

    victim = module.predict_native_hit_meter(2080, 0x0C, 0x00000040, role="victim")
    assert victim["requested_delta"] == 138
    assert victim["final_delta"] == 179


def test_light_tier_and_packet_flag_suppress_hit_meter():
    module = _module()
    light = module.predict_native_hit_meter(520, 0x09, 0x00000040)
    assert light["shared_pool"] == 46
    assert light["final_delta"] == 0
    assert light["blocked"] is True

    no_attacker_meter = module.predict_native_hit_meter(2080, 0x0C, 0x00010040)
    assert no_attacker_meter["final_delta"] == 0
    assert no_attacker_meter["blocked"] is True

    victim = module.predict_native_hit_meter(2080, 0x0C, 0x00010040, role="victim")
    assert victim["final_delta"] == 179


def test_baroque_halves_pre_quarter_meter_pool():
    module = _module()
    normal = module.predict_native_hit_meter(2080, 0x0C, 0x40)
    baroque = module.predict_native_hit_meter(2080, 0x0C, 0x40, baroque_active=True)
    assert normal["pre_quarter_gain"] == 741
    assert baroque["pre_quarter_gain"] == 370
    assert normal["final_delta"] == 240
    assert baroque["final_delta"] == 119


def test_runtime_profiler_attributes_attacker_and_victim_awards(tmp_path: Path):
    module = _module()
    p1_base = 0x9246B9C0
    p2_base = 0x927EB9E0
    actor = 0x91A6B774
    values = {
        module.TEAM_METER_ADDR["P1"]: 10000,
        module.TEAM_METER_ADDR["P2"]: 20000,
        p1_base + module.POINT_FLAG_OFFSET: 1,
        p2_base + module.POINT_FLAG_OFFSET: 1,
        actor + 0x34: p2_base,
    }

    def read_u32(address: int):
        return values.get(address, 0)

    profiler = module.RuntimeMeterGenerationProfiler(
        path=tmp_path / "profiles.json",
        event_path=tmp_path / "events.csv",
        read_u32=read_u32,
        read_block=lambda _address, _size: b"",
        emit_console=False,
    )
    snaps = {
        "P1-C1": {
            "base": p1_base,
            "id": 12,
            "name": "Ryu",
            "cur": 48000,
            "attA": 0x0102,
            "mv_label": "5C",
            "attack_property_live_actor": actor,
            "attack_property_live_damage": 2080,
            "attack_property_live_a": 0x0C,
            "attack_property_live_b": 0x40,
        },
        "P2-C1": {
            "base": p2_base,
            "id": 13,
            "name": "Chun-Li",
            "cur": 44000,
        },
    }
    assert profiler.update(snaps, frame=10, now=10.0) is False

    values[module.TEAM_METER_ADDR["P1"]] += 240
    values[module.TEAM_METER_ADDR["P2"]] += 179
    snaps["P2-C1"]["cur"] = 41920
    assert profiler.update(snaps, frame=11, now=11.0) is True

    events = profiler.doc["events"][-2:]
    by_team = {row["team"]: row for row in events}
    assert by_team["P1"]["source_role"] == "attacker"
    assert by_team["P1"]["predicted_final_delta"] == 240
    assert by_team["P1"]["prediction_match"] is True
    assert by_team["P2"]["source_role"] == "victim"
    assert by_team["P2"]["source_slot"] == "P1-C1"
    assert by_team["P2"]["predicted_final_delta"] == 179
    assert by_team["P2"]["prediction_match"] is True

    assert profiler.flush()
    with (tmp_path / "events.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2


def test_main_arms_updates_and_flushes_meter_profiler():
    source = MAIN.read_text(encoding="utf-8")
    assert "RuntimeMeterGenerationProfiler" in source
    assert "runtime_meter_generation_profiler.update" in source
    assert "runtime_meter_generation_profiler.flush" in source
    assert "runtime_meter_generation_events.csv" in source


def test_meter_reader_documents_u32_and_five_bar_cap():
    source = METER.read_text(encoding="utf-8")
    assert "big-endian u32" in source
    assert "5 full bars" in source
    assert "u16" not in source
