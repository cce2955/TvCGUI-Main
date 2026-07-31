from __future__ import annotations

import json

from tvcgui.features.training.attack_property_profiler import RuntimeAttackPropertyProfiler, _SlotMeta


def _events(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if line.startswith("[ATKPROP_JSON] "):
            rows.append(json.loads(line.split(" ", 1)[1]))
    return rows


def test_console_debug_emits_script_and_projectile_only_on_change(tmp_path, capsys):
    profiler = RuntimeAttackPropertyProfiler(
        path=tmp_path / "profiles.json",
        event_path=tmp_path / "events.csv",
        start_worker=False,
        emit_console=True,
        enable_resolver_hook=False,
    )
    meta = _SlotMeta(slot="P1-C1", base=0x91000000, char_id=1, name="Ryu", action_id=0x0130, action_name="Hado L")
    definitions = {
        "P1-C1": {
            "owner_name": "Ryu",
            "owner_base": 0x91000000,
            "owner_action_id": 0x0130,
            "owner_action_source": "fighter+0x1E8",
            "owner_action_name": "Hado L",
            "definition_chr_tbl": 0x90800000,
            "definition_move_root": 0x908B0000,
            "definition_scan_size": 0x100,
            "phases": [{
                "phase_index": 1,
                "script_offset": 0x20,
                "property_a_initial": 0x00040100,
                "property_a": 0x00,
                "property_a_initial_unknown_mask": 0x00040100,
                "property_b_initial": 0x10,
                "property_b": 0x14,
                "property_b_unknown_mask": 0,
                "result_clear_mask": 0x80042F00,
                "hit_result_raw": 0x00000400,
                "hit_reaction": 4,
                "property_a_addr": 0x908B0030,
                "property_b_addr": 0x908B0040,
                "hit_result_addr": 0x908B0060,
                "native_operations": [{
                    "offset": 0x20,
                    "address": 0x908B0030,
                    "operation_name": "SET",
                    "field_name": "A",
                    "field_id": 0x240,
                    "value": 0x00040100,
                }],
            }],
        }
    }
    projectiles = {
        "P1-C1": [{
            "projectile_live": True,
            "owner_base": 0x91000000,
            "owner_action_id": 0x0130,
            "owner_action_name": "Hado L",
            "projectile_index": 1,
            "projectile_id": 0x0130,
            "actor": 0x92001000,
            "linked": 0x92002000,
            "registry_source": "actor_table",
            "property_layout": "B80_A84",
            "raw_property_80": 0x10,
            "raw_property_84": 0x00040100,
            "property_a": 0x00040100,
            "property_b": 0x10,
            "property_a_initial_unknown_mask": 0x00040100,
            "property_b_unknown_mask": 0,
            "phase_property_a": 0,
            "phase_property_b": 0,
            "phase_property_b_unknown_mask": 0,
            "runtime_status_20": 1,
            "target": 0x93000000,
            "linked_owner": 0x91000000,
        }]
    }
    profiler._emit_native_property_debug(definitions, projectiles, {"P1-C1": meta}, frame=10)
    first = _events(capsys.readouterr().out)
    assert [row["event"] for row in first] == ["fighter_script", "projectile_native"]
    assert first[0]["phases"][0]["a_initial"] == "0x00040100"
    assert first[0]["phases"][0]["ops"][0]["field"] == "A"
    assert first[1]["state"] == "spawn"
    assert first[1]["raw_80"] == "0x00000010"
    assert first[1]["raw_84"] == "0x00040100"

    profiler._emit_native_property_debug(definitions, projectiles, {"P1-C1": meta}, frame=11)
    assert _events(capsys.readouterr().out) == []

    profiler._emit_native_property_debug({}, {}, {"P1-C1": meta}, frame=12)
    gone = _events(capsys.readouterr().out)
    assert gone == [{
        "actor": "0x92001000",
        "event": "projectile_native",
        "frame": 12,
        "linked": "0x92002000",
        "slot": "P1-C1",
        "state": "despawn",
    }]

    profiler._emit_native_property_debug(definitions, {}, {"P1-C1": meta}, frame=13)
    repeated = _events(capsys.readouterr().out)
    assert len(repeated) == 1
    assert repeated[0]["event"] == "fighter_script"
    profiler.flush()
