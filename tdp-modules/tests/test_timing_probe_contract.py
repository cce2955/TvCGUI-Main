from pathlib import Path

from tvcgui.features.training.timing_probe_window import (
    FighterTimingState,
    TimingCapture,
    TimingSample,
    classify_contact,
    summarize_capture,
)


def _state(**updates):
    data = dict(
        slot="P2-C1",
        base=0x927EBA00,
        char_id=1,
        action_id=1,
        action_frame=0,
        frame_a=0,
        frame_b=0,
        resolved_stun=0,
        stun_remaining=0,
        freeze_a=0,
        freeze_b=0,
        state_063=0,
        hp=10000,
    )
    data.update(updates)
    return FighterTimingState(**data)


def test_contact_classifier_uses_live_hit_and_block_states():
    previous = _state(hp=10000)
    assert classify_contact(previous, _state(state_063=16)) == "block"
    assert classify_contact(previous, _state(state_063=4)) == "hit"
    assert classify_contact(previous, _state(hp=9900)) == "hit"
    assert classify_contact(previous, _state(resolved_stun=12, stun_remaining=12)) is None


def test_summary_names_all_four_candidate_offsets_for_both_roles():
    attacker = _state(slot="P1-C1", freeze_a=4, freeze_b=5, frame_a=6, frame_b=8)
    defender = _state(slot="P2-C1", resolved_stun=12, stun_remaining=11, freeze_a=4, freeze_b=5, state_063=4)
    capture = TimingCapture(
        index=1,
        timestamp="12:00:00",
        expected="hit",
        observed="hit",
        result="MATCH",
        attacker_slot="P1-C1",
        defender_slot="P2-C1",
        char_id=1,
        action_id=0x100,
        action_name="5A",
        contact_ms=83.3,
        notes="test",
        samples=[TimingSample(0.0, attacker, defender)],
    )
    text = summarize_capture(capture)
    for token in ("+1210", "+1228", "+211C", "+2120", "ATTACKER", "DEFENDER"):
        assert token in text


def test_main_gui_has_separate_timing_monitor_button_and_window():
    root = Path(__file__).resolve().parents[1]
    main_text = (root / "main.py").read_text(encoding="utf-8")
    components = (root / "tvcgui" / "ui" / "components.py").read_text(encoding="utf-8")
    assert "open_timing_probe_window" in main_text
    assert "timing_probe_btn_rect" in main_text
    assert '("timing_probe", 124)' in components
    assert '"Timing Monitor"' in components


def test_probe_is_observation_only():
    source = Path(__file__).resolve().parents[1] / "tvcgui" / "features" / "training" / "timing_probe_window.py"
    text = source.read_text(encoding="utf-8")
    assert "OFF_RESOLVED_STUN = 0x1210" in text
    assert "OFF_STUN_REMAINING = 0x1228" in text
    assert "OFF_FREEZE_A = 0x211C" in text
    assert "OFF_FREEZE_B = 0x2120" in text
    assert "wd32" not in text
    assert "wbytes" not in text


def test_bilateral_freeze_edge_detects_contact_without_f063():
    from tvcgui.features.training.timing_probe_window import bilateral_impact_started

    previous_attacker = _state(slot="P1-C1", freeze_a=0, freeze_b=0)
    previous_defender = _state(slot="P2-C1", freeze_a=0, freeze_b=0)
    current_attacker = _state(slot="P1-C1", freeze_a=0, freeze_b=6)
    current_defender = _state(slot="P2-C1", freeze_a=0, freeze_b=6)
    assert bilateral_impact_started(previous_attacker, current_attacker, previous_defender, current_defender)


def test_shared_freeze_without_hp_loss_resolves_as_block_for_controlled_5a():
    from tvcgui.features.training.timing_probe_window import resolve_impact_contact

    attacker = _state(slot="P1-C1", freeze_a=3, freeze_b=6)
    defender = _state(slot="P2-C1", freeze_a=4, freeze_b=6, hp=10000)
    samples = [TimingSample(70.1, attacker, defender), TimingSample(111.8, attacker, defender)]
    assert resolve_impact_contact(samples, "impact") == "block"


def test_shared_freeze_with_hp_loss_resolves_as_hit():
    from tvcgui.features.training.timing_probe_window import resolve_impact_contact

    attacker = _state(slot="P1-C1", freeze_a=3, freeze_b=6)
    before = _state(slot="P2-C1", freeze_a=0, freeze_b=0, hp=10000)
    after = _state(slot="P2-C1", freeze_a=4, freeze_b=6, hp=9900)
    samples = [TimingSample(0.0, attacker, before), TimingSample(70.1, attacker, after)]
    assert resolve_impact_contact(samples, "impact") == "hit"


def _scan_state(offset_values=None, **updates):
    from tvcgui.features.training.timing_probe_window import STUN_SCAN_OFFSETS

    values = [0] * len(STUN_SCAN_OFFSETS)
    for offset, value in (offset_values or {}).items():
        values[STUN_SCAN_OFFSETS.index(offset)] = value
    updates["stun_scan"] = tuple(values)
    return _state(**updates)


def _capture(index, observed, samples):
    return TimingCapture(
        index=index,
        timestamp="12:00:00",
        expected=observed,
        observed=observed,
        result="MATCH",
        attacker_slot="P1-C1",
        defender_slot="P2-C1",
        char_id=1,
        action_id=0x100,
        action_name="5A",
        contact_ms=None if observed == "whiff" else 10.0,
        notes="",
        samples=samples,
    )


def test_blockstun_scan_covers_entire_1200_through_1240_neighborhood():
    from tvcgui.features.training.timing_probe_window import STUN_SCAN_OFFSETS

    assert STUN_SCAN_OFFSETS[0] == 0x1200
    assert STUN_SCAN_OFFSETS[-1] == 0x1240
    assert len(STUN_SCAN_OFFSETS) == 17
    assert all(b - a == 4 for a, b in zip(STUN_SCAN_OFFSETS, STUN_SCAN_OFFSETS[1:]))


def test_block_specific_countdown_ranks_as_best_candidate():
    from tvcgui.features.training.timing_probe_window import rank_blockstun_candidates

    candidate = 0x121C
    whiff = _capture(
        1,
        "whiff",
        [TimingSample(0.0, _scan_state(slot="P1-C1"), _scan_state(slot="P2-C1"))],
    )
    hit = _capture(
        2,
        "hit",
        [
            TimingSample(0.0, _scan_state(slot="P1-C1"), _scan_state(slot="P2-C1", offset_values={0x1210: 12})),
            TimingSample(20.0, _scan_state(slot="P1-C1"), _scan_state(slot="P2-C1", offset_values={0x1210: 8})),
        ],
    )
    block = _capture(
        3,
        "block",
        [
            TimingSample(0.0, _scan_state(slot="P1-C1"), _scan_state(slot="P2-C1")),
            TimingSample(10.0, _scan_state(slot="P1-C1"), _scan_state(slot="P2-C1", offset_values={candidate: 10})),
            TimingSample(18.0, _scan_state(slot="P1-C1"), _scan_state(slot="P2-C1", offset_values={candidate: 9})),
            TimingSample(26.0, _scan_state(slot="P1-C1"), _scan_state(slot="P2-C1", offset_values={candidate: 8})),
            TimingSample(40.0, _scan_state(slot="P1-C1"), _scan_state(slot="P2-C1", offset_values={candidate: 0})),
        ],
    )
    ranked = rank_blockstun_candidates([whiff, hit, block])
    assert ranked[0]["offset"] == candidate
    assert ranked[0]["block_max"] == 10
    assert ranked[0]["hit_max"] == 0
    assert ranked[0]["countdown"] >= 3


def test_blockstun_scan_summary_reports_missing_baselines_and_ranked_offsets():
    from tvcgui.features.training.timing_probe_window import summarize_blockstun_scan

    whiff = _capture(
        1,
        "whiff",
        [TimingSample(0.0, _scan_state(slot="P1-C1"), _scan_state(slot="P2-C1"))],
    )
    partial = summarize_blockstun_scan([whiff])
    assert "Missing baselines: HIT, BLOCK" in partial

    hit = _capture(
        2,
        "hit",
        [TimingSample(10.0, _scan_state(slot="P1-C1"), _scan_state(slot="P2-C1", offset_values={0x1210: 12}))],
    )
    block = _capture(
        3,
        "block",
        [
            TimingSample(10.0, _scan_state(slot="P1-C1"), _scan_state(slot="P2-C1", offset_values={0x121C: 7})),
            TimingSample(18.0, _scan_state(slot="P1-C1"), _scan_state(slot="P2-C1", offset_values={0x121C: 6})),
        ],
    )
    complete = summarize_blockstun_scan([whiff, hit, block])
    assert "Best current candidate" in complete
    assert "+0x121C" in complete
    assert "WHIFF #1" in complete
    assert "HIT #2" in complete
    assert "BLOCK #3" in complete


def test_timing_probe_ui_exposes_blockstun_scan_tab_and_copy_action():
    source = Path(__file__).resolve().parents[1] / "tvcgui" / "features" / "training" / "timing_probe_window.py"
    text = source.read_text(encoding="utf-8")
    assert 'text="Blockstun Field Scan"' in text
    assert 'text="0x1200 Neighborhood"' in text
    assert 'text="Copy Scan"' in text
    assert "STUN_SCAN_OFFSETS = tuple(range(0x1200, 0x1244, 4))" in text
