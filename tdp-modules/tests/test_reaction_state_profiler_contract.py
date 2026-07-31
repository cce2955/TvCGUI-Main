from __future__ import annotations

import json
import struct
from pathlib import Path

from tvcgui.features.training.reaction_state_profiler import (
    BLOCK_SIZE,
    BLOCK_START,
    OFF_BLOCKSTUN,
    OFF_COMBO_COUNT,
    OFF_HITSTUN,
    OFF_REACTION_TIMER,
    REACTION_FAMILY_SIZE,
    REACTION_FAMILY_START,
    RuntimeReactionStateProfiler,
    phase_for,
)


def _write_u32(blob: bytearray, base_offset: int, offset: int, value: int) -> None:
    struct.pack_into(">I", blob, offset - base_offset, int(value) & 0xFFFFFFFF)


def test_phase_classifier_covers_knockdown_wakeup_and_air_recovery():
    assert phase_for(64, 0, 17) == "hitstun"
    assert phase_for(73, 0, 5) == "knockdown"
    assert phase_for(113, 0, 0) == "wakeup"
    assert phase_for(160, 0, 0) == "air_recovery"
    assert phase_for(1, 0, 0) == "neutral"


def test_profiles_one_complete_hitstun_knockdown_wakeup_path(tmp_path: Path):
    base = 0x92400000
    timing = bytearray(BLOCK_SIZE)
    family = bytearray(REACTION_FAMILY_SIZE)

    def reader(address: int, size: int) -> bytes:
        if address == base + BLOCK_START and size == BLOCK_SIZE:
            return bytes(timing)
        if address == base + REACTION_FAMILY_START and size == REACTION_FAMILY_SIZE:
            return bytes(family)
        return b""

    profile = tmp_path / "profiles.json"
    events = tmp_path / "events.csv"
    profiler = RuntimeReactionStateProfiler(
        path=profile,
        event_path=events,
        read_block=reader,
    )
    snap = {
        "base": base,
        "teamtag": "P2",
        "id": 12,
        "name": "Ryu",
        "attA": 1,
        "f062": 0,
        "f063": 0,
        "f064": 0,
        "f072": 0,
    }

    profiler.update({"P2-C1": snap}, frame=0)

    _write_u32(timing, BLOCK_START, OFF_COMBO_COUNT, 1)
    _write_u32(timing, BLOCK_START, OFF_HITSTUN, 17)
    snap["attA"] = 64
    profiler.update({"P2-C1": snap}, frame=1)

    _write_u32(timing, BLOCK_START, OFF_HITSTUN, 16)
    profiler.update({"P2-C1": snap}, frame=2)

    _write_u32(timing, BLOCK_START, OFF_HITSTUN, 15)
    snap["attA"] = 73
    profiler.update({"P2-C1": snap}, frame=3)

    _write_u32(timing, BLOCK_START, OFF_HITSTUN, 0)
    snap["attA"] = 113
    profiler.update({"P2-C1": snap}, frame=4)

    snap["attA"] = 1
    for frame in (5, 6, 7):
        profiler.update({"P2-C1": snap}, frame=frame)

    profiler.close()
    doc = json.loads(profile.read_text(encoding="utf-8"))
    assert len(doc["sequences"]) == 1
    row = doc["sequences"][0]
    assert row["max_hitstun"] == 17
    assert row["entered_knockdown"] is True
    assert row["entered_wakeup"] is True
    assert row["action_path"].startswith("64 Standing Hitstun > 73 Knockdown Face Up")
    assert "113 Get Up Face Up" in row["action_path"]
    assert row["hitstun_decrement_frames"] >= 2
    assert events.exists()


def test_secondary_1228_timer_is_recorded_but_not_used_as_hitstun(tmp_path: Path):
    base = 0x92800000
    timing = bytearray(BLOCK_SIZE)
    family = bytearray(REACTION_FAMILY_SIZE)

    def reader(address: int, size: int) -> bytes:
        if address == base + BLOCK_START:
            return bytes(timing)
        if address == base + REACTION_FAMILY_START:
            return bytes(family)
        return b""

    profiler = RuntimeReactionStateProfiler(
        path=tmp_path / "profiles.json",
        event_path=tmp_path / "events.csv",
        read_block=reader,
    )
    snap = {"base": base, "teamtag": "P2", "id": 4, "name": "Polimar", "attA": 64}
    _write_u32(timing, BLOCK_START, OFF_HITSTUN, 12)
    _write_u32(timing, BLOCK_START, OFF_REACTION_TIMER, 12)
    profiler.update({"P2-C1": snap}, frame=1)
    _write_u32(timing, BLOCK_START, OFF_HITSTUN, 0)
    _write_u32(timing, BLOCK_START, OFF_REACTION_TIMER, 8)
    snap["attA"] = 1
    profiler.update({"P2-C1": snap}, frame=2)
    # The secondary timer keeps the research sequence open, but hitstun itself
    # remains the confirmed +0x1210 value and is not silently replaced by +0x1228.
    assert snap["reaction_hitstun_remaining"] == 0
    assert snap["reaction_secondary_timer"] == 8
    profiler.close()
