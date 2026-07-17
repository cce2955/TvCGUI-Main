from __future__ import annotations

from tvcgui.features.combat.timing_engine import TimingEngine, TimingResult


def _snap(slot, team, action, hp=10000, **timing):
    return {
        "base": 0x92400000 if team == "P1" else 0x92800000,
        "teamtag": team,
        "slotname": slot,
        "csv_char_id": 12 if team == "P1" else 4,
        "name": "Ryu" if team == "P1" else "Polimar",
        "mv_id_display": action,
        "mv_label": {0x102: "5C", 0x001: "idle", 0x00A: "crouching", 0x030: "blockstun"}.get(action, f"0x{action:04X}"),
        "cur": hp,
        "x": 0.0 if team == "P1" else 10.0,
        "y": 0.0,
        "timing_action_frame": timing.get("action_frame", 0),
        "timing_blockstun": timing.get("blockstun", 0),
        "timing_hitstun_total": timing.get("hitstun_total", 0),
        "timing_hitstun_remaining": timing.get("hitstun_remaining", 0),
        "timing_hitstop_active": timing.get("active_hitstop", 0),
        "timing_hitstop_pending": timing.get("pending_hitstop", 0),
    }


def test_decoded_offsets_are_centralized():
    import tvcgui.features.combat.timing_engine as module

    assert module.OFF_BLOCKSTUN_REMAINING == 0x1204
    assert module.OFF_HITSTUN_TOTAL == 0x1210
    assert module.OFF_HITSTUN_REMAINING == 0x1228
    assert module.OFF_ACTIVE_HITSTOP == 0x211C
    assert module.OFF_PENDING_HITSTOP == 0x2120


def test_block_result_keeps_stun_but_withholds_unproven_advantage():
    engine = TimingEngine()
    frames = [
        {"P1-C1": _snap("P1-C1", "P1", 0x102), "P2-C1": _snap("P2-C1", "P2", 0x001)},
        {"P1-C1": _snap("P1-C1", "P1", 0x102, pending_hitstop=12), "P2-C1": _snap("P2-C1", "P2", 0x030, blockstun=15, pending_hitstop=12)},
        {"P1-C1": _snap("P1-C1", "P1", 0x102, active_hitstop=8), "P2-C1": _snap("P2-C1", "P2", 0x030, blockstun=14, active_hitstop=8)},
        # This lower action ID is only a recovery/stance transition.  It must
        # not be interpreted as proof that the attacker is actionable.
        {"P1-C1": _snap("P1-C1", "P1", 0x00A), "P2-C1": _snap("P2-C1", "P2", 0x030, blockstun=2)},
        {"P1-C1": _snap("P1-C1", "P1", 0x001), "P2-C1": _snap("P2-C1", "P2", 0x001, blockstun=0)},
    ]
    results = []
    for index, frame in enumerate(frames):
        results.extend(engine.update(index, frame))
    assert len(results) == 1
    result = results[0]
    assert result.kind == "block"
    assert result.blockstun == 15
    assert result.hitstop == 12
    assert result.block_advantage is None
    assert result.attacker_ready_frames is None
    assert result.clean
    assert "withheld" in result.notes


def test_cancelled_block_keeps_stun_but_does_not_claim_advantage():
    engine = TimingEngine()
    frames = [
        {"P1-C1": _snap("P1-C1", "P1", 0x102), "P2-C1": _snap("P2-C1", "P2", 0x001)},
        {"P1-C1": _snap("P1-C1", "P1", 0x102), "P2-C1": _snap("P2-C1", "P2", 0x030, blockstun=10)},
        {"P1-C1": _snap("P1-C1", "P1", 0x130), "P2-C1": _snap("P2-C1", "P2", 0x030, blockstun=4)},
        {"P1-C1": _snap("P1-C1", "P1", 0x130), "P2-C1": _snap("P2-C1", "P2", 0x001, blockstun=0)},
    ]
    results = []
    for index, frame in enumerate(frames):
        results.extend(engine.update(index, frame))
    assert len(results) == 1
    result = results[0]
    assert result.cancelled
    assert result.blockstun == 10
    assert result.block_advantage is None
    assert not result.clean


def test_result_serializes_for_overlay_payload():
    result = TimingResult(
        sequence=4,
        timestamp="12:00:00",
        frame_idx=20,
        kind="block",
        attacker_slot="P1-C1",
        defender_slot="P2-C1",
        attacker_name="Ryu",
        defender_name="Polimar",
        char_id=12,
        action_id=0x102,
        action_name="5C",
        blockstun=15,
        hitstop=12,
        block_advantage=None,
        notes="block advantage withheld until attacker actionability is decoded",
    )
    assert result.to_dict()["block_advantage"] is None
