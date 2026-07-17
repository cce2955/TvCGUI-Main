from __future__ import annotations

import json

from tvcgui.features.combat.timing_engine import TimingResult
import tvcgui.features.frame_data.timing_observations as observations


def _result(sequence, *, blockstun=15, advantage=-4):
    return TimingResult(
        sequence=sequence,
        timestamp="12:00:00",
        frame_idx=sequence,
        kind="block",
        attacker_slot="P1-C1",
        defender_slot="P2-C1",
        attacker_name="Ryu",
        defender_name="Polimar",
        char_id=12,
        action_id=0x102,
        action_name="5C",
        blockstun=blockstun,
        hitstop=12,
        block_advantage=advantage,
        attacker_ready_frames=19,
        defender_ready_frames=15,
        clean=True,
    )


def test_two_matching_clean_samples_fill_only_trusted_fields(tmp_path, monkeypatch):
    path = tmp_path / "timing_observations.json"
    monkeypatch.setattr(observations, "OBSERVATION_FILE", str(path))
    monkeypatch.setattr(observations, "_DOC", None)
    observations.record_timing_result(_result(1))
    observations.record_timing_result(_result(2))
    scan = [{"slot_label": "P1-C1", "char_id": 12, "moves": [{"id": 0x102, "move_name": "5C", "blockstun": None, "hitstop": None, "adv_block_observed": None}]}]
    changed = observations.apply_observations_to_scan_data(scan)
    move = scan[0]["moves"][0]
    assert changed == 2
    assert move["blockstun"] == 15
    assert move["hitstop"] == 12
    assert move["adv_block_observed"] is None
    assert move["blockstun_source"] == "live_timing_scan"


def test_existing_untrusted_advantage_samples_are_purged(tmp_path, monkeypatch):
    path = tmp_path / "timing_observations.json"
    path.write_text(json.dumps({
        "version": observations.OBSERVATION_VERSION,
        "characters": {"12": {"moves": {"258": {"fields": {
            "blockstun": {"samples": [15, 15], "value": 15, "matches": 2},
            "adv_block_observed": {"samples": [-2, -2], "value": -2, "matches": 2},
        }}}}},
    }))
    monkeypatch.setattr(observations, "OBSERVATION_FILE", str(path))
    monkeypatch.setattr(observations, "_DOC", None)
    move = observations.get_move_observations(12, 0x102)
    assert "blockstun" in move["fields"]
    assert "adv_block_observed" not in move["fields"]
    saved = json.loads(path.read_text())
    fields = saved["characters"]["12"]["moves"]["258"]["fields"]
    assert "adv_block_observed" not in fields


def test_live_scan_advantage_value_is_cleared_without_touching_manual_value(tmp_path, monkeypatch):
    path = tmp_path / "timing_observations.json"
    monkeypatch.setattr(observations, "OBSERVATION_FILE", str(path))
    monkeypatch.setattr(observations, "_DOC", None)
    scan = [{"slot_label": "P1-C1", "char_id": 12, "moves": [
        {"id": 0x102, "adv_block_observed": -2, "adv_block_observed_source": "live_timing_scan", "adv_block_observed_samples": 2},
        {"id": 0x101, "adv_block_observed": -7, "adv_block_observed_source": "manual"},
    ]}]
    observations.apply_observations_to_scan_data(scan)
    assert scan[0]["moves"][0]["adv_block_observed"] is None
    assert scan[0]["moves"][1]["adv_block_observed"] == -7


def test_existing_manual_values_are_not_overwritten(tmp_path, monkeypatch):
    path = tmp_path / "timing_observations.json"
    monkeypatch.setattr(observations, "OBSERVATION_FILE", str(path))
    monkeypatch.setattr(observations, "_DOC", None)
    observations.record_timing_result(_result(1))
    observations.record_timing_result(_result(2))
    scan = [{"slot_label": "P1-C1", "char_id": 12, "moves": [{"id": 0x102, "blockstun": 99}]}]
    observations.apply_observations_to_scan_data(scan)
    assert scan[0]["moves"][0]["blockstun"] == 99


def test_apply_reports_precise_live_changes(tmp_path, monkeypatch):
    path = tmp_path / "timing_observations.json"
    monkeypatch.setattr(observations, "OBSERVATION_FILE", str(path))
    monkeypatch.setattr(observations, "_DOC", None)
    observations.record_timing_result(_result(1))
    observations.record_timing_result(_result(2))
    scan = [{"slot_label": "P1-C1", "char_id": 12, "moves": [{"id": 0x102}]}]
    change_log = []
    changed = observations.apply_observations_to_scan_data(scan, change_log=change_log)
    assert changed == 2
    assert {row["field"] for row in change_log} == {"blockstun", "hitstop"}
    assert all(row["action_id"] == 0x102 for row in change_log)
    assert all(row["matches"] == 2 for row in change_log)
