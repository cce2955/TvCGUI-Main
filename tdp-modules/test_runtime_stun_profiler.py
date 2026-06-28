from pathlib import Path
import struct
import tempfile

from tvcgui.features.training.stun_profiler import (
    ACTION_COUNTER_OFF,
    RuntimeStunProfiler,
    apply_runtime_stun_observations,
)
from tvcgui.core.constants import RUNTIME_RESOLVED_STUN_OFF, RUNTIME_STUN_REMAINING_OFF, RUNTIME_IMPACT_FREEZE_OFF


def _f32_word(value: float) -> int:
    return struct.unpack(">I", struct.pack(">f", float(value)))[0]


def test_observes_and_overlays_runtime_hitstun_only_for_engine_targets():
    values = {}

    def reader(addr):
        return values.get(addr, 0)

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "runtime_stun_profiles.json"
        profiler = RuntimeStunProfiler(path=path, read_u32=reader)
        profiler.set_engine_move_targets({(15, 0x0104): {"active_end": 9}})
        attacker_base = 0x90000000
        target_base = 0x90010000
        values[attacker_base + ACTION_COUNTER_OFF] = _f32_word(2.0)
        snaps = {
            "P1-C1": {
                "base": attacker_base, "teamtag": "P1", "id": 15,
                "name": "Morrigan", "attA": 0x0104, "f063": 6,
                "cur": 10000, "x": 0.0, "mv_label": "2B",
            },
            "P2-C1": {
                "base": target_base, "teamtag": "P2", "id": 12,
                "name": "Ryu", "attA": 1, "f063": 17,
                "cur": 10000, "x": 1.0, "mv_label": "idle",
            },
        }
        profiler.update(snaps, frame=1)
        values[target_base + RUNTIME_RESOLVED_STUN_OFF] = 17
        values[target_base + RUNTIME_STUN_REMAINING_OFF] = 17
        values[target_base + RUNTIME_IMPACT_FREEZE_OFF] = 9
        snaps["P2-C1"].update({"f063": 4, "cur": 9800})
        assert profiler.update(snaps, frame=2)
        profiler.flush()

        moves = [{
            "id": 0x0104, "hitstun": 17, "blockstun": 12,
            "stun_source": "engine_default_hit_level", "stun_addr": None,
        }]
        apply_runtime_stun_observations(moves, 15, path=path)
        assert moves[0]["hitstun"] == 17
        assert moves[0]["hitstun_source"] == "runtime_observed"
        assert moves[0]["hitstop"] == 9
        assert moves[0]["hitstop_source"] == "runtime_observed"
        assert moves[0]["stun_addr"] is None
        assert moves[0]["runtime_profile_eligible"] is True


def test_observes_whiff_recovery_and_never_overlays_direct_rows():
    values = {}

    def reader(addr):
        return values.get(addr, 0)

    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "runtime_stun_profiles.json"
        profiler = RuntimeStunProfiler(path=path, read_u32=reader)
        profiler.set_engine_move_targets({(15, 0x0100): {"active_end": 8}})
        base = 0x90000000
        snap = {
            "base": base, "teamtag": "P1", "id": 15, "name": "Morrigan",
            "attA": 0x0100, "f063": 6, "cur": 10000, "x": 0.0,
            "mv_label": "5A",
        }

        # Frame 8 active, then action frame 15 is the final frame before idle:
        # recovery is 15 - 8 = 7.
        values[base + ACTION_COUNTER_OFF] = _f32_word(9.0)  # displayed action frame 8
        assert not profiler.update({"P1-C1": snap}, frame=1)
        values[base + ACTION_COUNTER_OFF] = _f32_word(16.0)  # displayed action frame 15
        assert not profiler.update({"P1-C1": snap}, frame=2)
        snap["attA"] = 1
        values[base + ACTION_COUNTER_OFF] = _f32_word(2.0)
        assert profiler.update({"P1-C1": snap}, frame=3)
        profiler.flush()

        engine = [{
            "id": 0x0100, "hitstun": 12, "blockstun": 9,
            "stun_source": "engine_default_hit_level",
        }]
        apply_runtime_stun_observations(engine, 15, path=path)
        assert engine[0]["recovery"] == 7
        assert engine[0]["recovery_source"] == "runtime_observed"
        assert engine[0]["adv_hit"] == 5

        direct = [{
            "id": 0x0100, "hitstun": 99, "blockstun": 99,
            "stun_source": "owned_direct_override",
        }]
        apply_runtime_stun_observations(direct, 15, path=path)
        assert direct[0]["hitstun"] == 99
        assert direct[0].get("recovery") is None
