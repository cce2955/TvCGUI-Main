from tvcgui.features.training.mission_runtime_v2 import MissionRuntimeV2
from tvcgui.runtime.mission_events import (
    EVENT_ACTION,
    EVENT_BLOCKSTUN_BEGIN,
    EVENT_COMBO_BEGIN,
    EVENT_COMBO_END,
    EVENT_DAMAGE,
    EVENT_INPUT,
    EVENT_JUMP,
    MissionEvent,
)


def ev(seq, kind, slot="P1-C1", ns=None, **kwargs):
    return MissionEvent(
        sequence=seq,
        sample_sequence=seq,
        timestamp_ns=seq * 10_000_000 if ns is None else ns,
        kind=kind,
        slot=slot,
        **kwargs,
    )


def resolver(event):
    return {
        0x102: ["5C"],
        0x120: ["Shoryu M"],
        0x130: ["Tatsu A"],
        0x131: ["Tatsu B"],
    }.get(event.action_id, [])


def matcher(candidate, expected):
    c = candidate.lower().replace(" ", "")
    return any(c == str(w).lower().replace(" ", "") for w in expected)


def configure(runtime, steps, goal=None, floor=0):
    runtime.configure(
        slot="P1-C1",
        character="Ryu",
        mission_id="ryu_test",
        raw_steps=steps,
        goal=goal or {},
        event_floor=floor,
        whiff_policy=lambda labels, step: bool(isinstance(step, dict) and step.get("whiff")),
    )


def consume(runtime, events, jump_cancel_checker=None):
    return runtime.consume(
        events,
        owner_slot="P1-C1",
        label_matcher=matcher,
        action_label_resolver=resolver,
        jump_cancel_checker=jump_cancel_checker,
        newest_sequence=max([e.sequence for e in events] or [0]),
    )


def test_damage_step_advances_only_after_damage_edge():
    runtime = MissionRuntimeV2()
    configure(runtime, [{"labels": ["5C"]}])
    result = consume(runtime, [ev(1, EVENT_ACTION, action_id=0x102)])
    assert result.progress_index == 0
    result = consume(runtime, [ev(2, EVENT_DAMAGE, slot="P2-C1", damage=1000)])
    assert result.cleared
    assert result.progress_index == 1


def test_jump_cancel_is_input_evidence_not_free_pass():
    runtime = MissionRuntimeV2()
    configure(runtime, [
        {"labels": ["5C"], "pass": True},
        {"labels": ["Jump Cancel"], "input": "7 / 8 / 9"},
        {"labels": ["Tatsu A"], "pass": True},
    ])
    result = consume(runtime, [ev(1, EVENT_ACTION, action_id=0x102)])
    assert result.progress_index == 1
    # Raw TvC direction 0x08 is down, not numpad 8. It must not arm JC.
    result = consume(runtime, [ev(2, EVENT_INPUT, held=0x08)])
    assert result.progress_index == 1
    # Raw 0x05 is up-forward (numpad 9). Input arms the JC but does not
    # complete it until TvC actually transitions into a native jump state.
    result = consume(runtime, [ev(3, EVENT_INPUT, held=0x05)])
    assert result.progress_index == 1
    result = consume(runtime, [ev(4, EVENT_JUMP, action_id=21)])
    assert result.progress_index == 2
    result = consume(runtime, [ev(5, EVENT_ACTION, action_id=0x130)])
    assert result.cleared


def test_jump_cancel_respects_known_direct_move_flag():
    runtime = MissionRuntimeV2()
    configure(runtime, [
        {"labels": ["5C"], "pass": True},
        {"labels": ["Jump Cancel"], "input": "7 / 8 / 9"},
    ])
    result = consume(runtime, [ev(1, EVENT_ACTION, action_id=0x102)])
    assert result.progress_index == 1
    result = consume(runtime, [ev(2, EVENT_INPUT, held=0x04)])
    assert result.progress_index == 1
    result = consume(
        runtime,
        [ev(3, EVENT_JUMP, action_id=20)],
        jump_cancel_checker=lambda action_id: False,
    )
    assert result.progress_index == 1


def test_rapid_buffered_actions_and_hits_all_advance_in_one_consume():
    runtime = MissionRuntimeV2()
    configure(runtime, [
        {"labels": ["5C"]},
        {"labels": ["Shoryu M"]},
        {"labels": ["Tatsu A"]},
    ])
    result = consume(runtime, [
        ev(1, EVENT_ACTION, action_id=0x102),
        ev(2, EVENT_DAMAGE, slot="P2-C1", damage=500),
        ev(3, EVENT_ACTION, action_id=0x120),
        ev(4, EVENT_DAMAGE, slot="P2-C1", damage=700),
        ev(5, EVENT_ACTION, action_id=0x130),
        ev(6, EVENT_DAMAGE, slot="P2-C1", damage=900),
    ])
    assert result.cleared
    assert result.progress_index == 3
    assert result.buffered_advances == 3


def test_damage_edge_can_precede_action_edge_in_same_sampler_instant():
    runtime = MissionRuntimeV2()
    configure(runtime, [{"labels": ["5C"]}])
    result = consume(runtime, [
        ev(1, EVENT_DAMAGE, slot="P2-C1", ns=100_000_000, damage=500),
        ev(2, EVENT_ACTION, ns=120_000_000, action_id=0x102),
    ])
    assert result.cleared


def test_combo_end_creates_hard_attempt_boundary():
    runtime = MissionRuntimeV2()
    configure(runtime, [
        {"labels": ["5C"]},
        {"labels": ["Shoryu M"]},
    ])
    result = consume(runtime, [
        ev(1, EVENT_ACTION, action_id=0x102),
        ev(2, EVENT_DAMAGE, slot="P2-C1", damage=500),
        ev(3, EVENT_COMBO_BEGIN, slot="P2-C1", combo_count=1),
        ev(4, EVENT_COMBO_END, slot="P2-C1", combo_count=0),
    ])
    assert result.progress_index == 0
    assert result.attempt_id == 2
    assert result.event_floor == 4
    assert result.restart_count == 1
    # An event at/below the old-attempt floor can never donate evidence.
    result = consume(runtime, [ev(4, EVENT_ACTION, action_id=0x120)])
    assert result.progress_index == 0


def test_grace_is_game_event_time_not_wall_time():
    runtime = MissionRuntimeV2()
    configure(runtime, [
        {"labels": ["5C"], "grace": 6},
        {"labels": ["Shoryu M"], "pass": True},
    ])
    result = consume(runtime, [
        ev(1, EVENT_ACTION, ns=100_000_000, action_id=0x102),
        ev(2, EVENT_DAMAGE, slot="P2-C1", ns=110_000_000, damage=500),
        ev(3, EVENT_COMBO_BEGIN, slot="P2-C1", ns=120_000_000, combo_count=1),
        ev(4, EVENT_COMBO_END, slot="P2-C1", ns=130_000_000, combo_count=0),
        # 6f grace is ~100ms, so this expected action is valid at +70ms.
        ev(5, EVENT_ACTION, ns=180_000_000, action_id=0x120),
    ])
    assert result.cleared
    assert result.attempt_id == 1


def test_combo_damage_goal_uses_damage_events():
    runtime = MissionRuntimeV2()
    configure(runtime, [], goal={"type": "combo_damage", "damage": 1500})
    result = consume(runtime, [
        ev(1, EVENT_COMBO_BEGIN, slot="P2-C1", combo_count=1),
        ev(2, EVENT_DAMAGE, slot="P2-C1", damage=800, combo_count=1),
        ev(3, EVENT_DAMAGE, slot="P2-C1", damage=750, combo_count=2),
    ])
    assert result.cleared
    assert result.goal_damage == 1550


def test_state_duration_goal_uses_native_blockstun_count():
    runtime = MissionRuntimeV2()
    configure(runtime, [], goal={"type": "state_duration", "target_state": "blockstun", "frames": 20})
    result = consume(runtime, [
        ev(1, EVENT_BLOCKSTUN_BEGIN, slot="P2-C1", blockstun_remaining=21),
    ])
    assert result.cleared
    assert result.goal_current_frames == 21


def test_legs_c_uses_direct_mash_strength_with_generic_action_label():
    runtime = MissionRuntimeV2()
    runtime.configure(
        slot="P1-C1",
        character="Chun-Li",
        mission_id="chun_legs_c",
        raw_steps=[{"labels": ["Legs C"], "input": "C C C"}],
        goal={},
        event_floor=0,
    )

    def legs_resolver(event):
        return ["Lightning Legs"] if event.action_id == 0x220 else []

    def legs_matcher(candidate, expected):
        c = candidate.lower().replace(" ", "")
        return any(c == str(w).lower().replace(" ", "") for w in expected)

    result = runtime.consume(
        [
            ev(1, EVENT_INPUT, ns=100_000_000, pressed=0x20, held=0x20),
            ev(2, EVENT_INPUT, ns=130_000_000, pressed=0x20, held=0x20),
            ev(3, EVENT_INPUT, ns=160_000_000, pressed=0x20, held=0x20),
            ev(4, EVENT_ACTION, ns=180_000_000, action_id=0x220),
            ev(5, EVENT_DAMAGE, slot="P2-C1", ns=190_000_000, damage=450),
        ],
        owner_slot="P1-C1",
        label_matcher=legs_matcher,
        action_label_resolver=legs_resolver,
        newest_sequence=5,
    )
    assert result.cleared


def test_legs_c_rejects_wrong_mash_strength():
    runtime = MissionRuntimeV2()
    runtime.configure(
        slot="P1-C1",
        character="Chun-Li",
        mission_id="chun_legs_c_wrong",
        raw_steps=[{"labels": ["Legs C"], "input": "C C C"}],
        goal={},
        event_floor=0,
    )

    result = runtime.consume(
        [
            ev(1, EVENT_INPUT, ns=100_000_000, pressed=0x40, held=0x40),
            ev(2, EVENT_INPUT, ns=130_000_000, pressed=0x40, held=0x40),
            ev(3, EVENT_INPUT, ns=160_000_000, pressed=0x40, held=0x40),
            ev(4, EVENT_ACTION, ns=180_000_000, action_id=0x220),
            ev(5, EVENT_DAMAGE, slot="P2-C1", ns=190_000_000, damage=450),
        ],
        owner_slot="P1-C1",
        label_matcher=matcher,
        action_label_resolver=lambda event: ["Lightning Legs"],
        newest_sequence=5,
    )
    assert not result.cleared
    assert result.progress_index == 0


def test_native_hitstun_end_resets_even_if_combo_counter_still_lags():
    runtime = MissionRuntimeV2()
    configure(runtime, [
        {"labels": ["5C"]},
        {"labels": ["Shoryu M"]},
    ])
    result = consume(runtime, [
        ev(1, EVENT_ACTION, action_id=0x102),
        ev(2, EVENT_DAMAGE, slot="P2-C1", damage=500),
        ev(3, EVENT_COMBO_BEGIN, slot="P2-C1", combo_count=1),
        ev(4, "HITSTUN_BEGIN", slot="P2-C1", hitstun_remaining=10, combo_count=1),
        # Combo count is deliberately still nonzero here. Native stun zero wins.
        ev(5, "HITSTUN_END", slot="P2-C1", hitstun_remaining=0, combo_count=1),
    ])
    assert result.progress_index == 0
    assert result.attempt_id == 2
    assert result.last_restart_reason == "reaction_end"


def test_special_reaction_keeps_route_alive_after_hitstun_bar_ends():
    from tvcgui.runtime.mission_events import (
        EVENT_HITSTUN_BEGIN,
        EVENT_HITSTUN_END,
        EVENT_REACTION_BEGIN,
        EVENT_REACTION_END,
    )

    runtime = MissionRuntimeV2()
    configure(runtime, [
        {"labels": ["5C"]},
        {"labels": ["Shoryu M"]},
    ])
    result = consume(runtime, [
        ev(1, EVENT_ACTION, action_id=0x102),
        ev(2, EVENT_DAMAGE, slot="P2-C1", damage=500),
        ev(3, EVENT_COMBO_BEGIN, slot="P2-C1", combo_count=1),
        ev(4, EVENT_HITSTUN_BEGIN, slot="P2-C1", hitstun_remaining=8, combo_count=1),
        # Wall bounce becomes active before regular hitstun reaches zero.
        ev(5, EVENT_REACTION_BEGIN, slot="P2-C1", action_id=70, combo_count=1),
        ev(6, EVENT_HITSTUN_END, slot="P2-C1", hitstun_remaining=0, combo_count=1),
    ])
    assert result.progress_index == 1
    assert result.attempt_id == 1
    assert result.last_restart_reason == ""

    result = consume(runtime, [
        ev(7, EVENT_REACTION_END, slot="P2-C1", action_id=103, combo_count=1),
    ])
    assert result.progress_index == 0
    assert result.attempt_id == 2
    assert result.last_restart_reason == "reaction_end"


def test_recovery_idle_is_final_route_reset_failsafe():
    from tvcgui.runtime.mission_events import EVENT_RECOVERY_IDLE

    runtime = MissionRuntimeV2()
    configure(runtime, [
        {"labels": ["5C"], "pass": True},
        {"labels": ["Shoryu M"], "pass": True},
    ])
    result = consume(runtime, [
        ev(1, EVENT_ACTION, action_id=0x102),
        # Even if a native reaction edge/timer was missed entirely, the victim
        # becoming universal idle proves the route is over.
        ev(2, EVENT_RECOVERY_IDLE, slot="P2-C1", action_id=1),
    ])
    assert result.progress_index == 0
    assert result.attempt_id == 2
    assert result.last_restart_reason == "recovery_idle"


def test_recovery_idle_respects_step_grace_before_resetting():
    from tvcgui.runtime.mission_events import EVENT_RECOVERY_IDLE

    runtime = MissionRuntimeV2()
    configure(runtime, [
        {"labels": ["5C"], "pass": True, "grace": 6},
        {"labels": ["Shoryu M"], "pass": True},
    ])
    result = consume(runtime, [
        ev(1, EVENT_ACTION, ns=100_000_000, action_id=0x102),
        ev(2, EVENT_RECOVERY_IDLE, slot="P2-C1", ns=120_000_000, action_id=1),
    ])
    assert result.progress_index == 1
    assert result.attempt_id == 1
    # A later required move inside grace cancels the pending restart by
    # advancing the route instead of throwing away the attempt.
    result = consume(runtime, [
        ev(3, EVENT_ACTION, ns=150_000_000, action_id=0x120),
    ])
    assert result.cleared
    assert result.attempt_id == 1
