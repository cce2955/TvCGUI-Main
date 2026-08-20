from tvcgui.runtime.mission_events import (
    EVENT_BLOCKSTUN_BEGIN,
    EVENT_BLOCKSTUN_END,
    EVENT_INPUT,
    EVENT_JUMP,
    MissionEventStream,
)


def packet(**overrides):
    base = {
        "seq": 1,
        "sample_ns": 100,
        "base": 0x90000000,
        "char_id": 1,
        "action_id": 0x100,
        "action_frame": 1,
        "held": 0,
        "pressed": 0,
        "released": 0,
        "current_hp": 50000,
        "hitstun_remaining": 0,
        "blockstun_remaining": 0,
        "combo_count": 0,
    }
    base.update(overrides)
    return base


def test_blockstun_begin_and_end_are_native_events():
    stream = MissionEventStream()
    stream.publish_sample("P2-C1", packet())
    stream.publish_sample("P2-C1", packet(seq=2, sample_ns=200, blockstun_remaining=18))
    stream.publish_sample("P2-C1", packet(seq=3, sample_ns=300, blockstun_remaining=0))
    kinds = [event.kind for event in stream.snapshot()]
    assert EVENT_BLOCKSTUN_BEGIN in kinds
    assert EVENT_BLOCKSTUN_END in kinds
    begin = next(event for event in stream.snapshot() if event.kind == EVENT_BLOCKSTUN_BEGIN)
    assert begin.blockstun_remaining == 18


def test_native_jump_transition_is_emitted_separately_from_attack_actions():
    stream = MissionEventStream()
    stream.publish_sample("P1-C1", packet(action_id=0x102, action_frame=4))
    stream.publish_sample(
        "P1-C1",
        packet(
            seq=2,
            sample_ns=200,
            action_id=21,
            action_frame=0,
            held=0x05,
            pressed=0x05,
        ),
    )
    events = list(stream.snapshot())
    kinds = [event.kind for event in events]
    assert EVENT_INPUT in kinds
    assert EVENT_JUMP in kinds
    jump = next(event for event in events if event.kind == EVENT_JUMP)
    assert jump.action_id == 21
    assert jump.held & 0x0F == 0x05


def test_air_untech_clock_drives_reaction_begin_and_end():
    from tvcgui.runtime.mission_events import EVENT_HITSTUN_BEGIN, EVENT_HITSTUN_END

    stream = MissionEventStream()
    stream.publish_sample("P2-C1", packet(action_id=10, hitstun_remaining=0, untech_remaining=0))
    stream.publish_sample(
        "P2-C1",
        packet(seq=2, sample_ns=200, action_id=10, hitstun_remaining=0, untech_remaining=14),
    )
    stream.publish_sample(
        "P2-C1",
        packet(seq=3, sample_ns=300, action_id=10, hitstun_remaining=0, untech_remaining=0),
    )
    events = list(stream.snapshot())
    begin = next(event for event in events if event.kind == EVENT_HITSTUN_BEGIN)
    end = next(event for event in events if event.kind == EVENT_HITSTUN_END)
    assert begin.untech_remaining == 14
    assert end.untech_remaining == 0


def test_wall_bounce_reaction_bridges_native_hitstun_zero():
    from tvcgui.runtime.mission_events import (
        EVENT_HITSTUN_END,
        EVENT_REACTION_BEGIN,
        EVENT_REACTION_END,
    )

    stream = MissionEventStream()
    # Establish ordinary hitstun, then transition into native Wall Bounce
    # Interim (70) on the exact sample where the regular stun clock reaches 0.
    stream.publish_sample(
        "P2-C1",
        packet(action_id=64, hitstun_remaining=8, untech_remaining=0),
    )
    stream.publish_sample(
        "P2-C1",
        packet(seq=2, sample_ns=200, action_id=70, hitstun_remaining=0, untech_remaining=0),
    )
    # Wakeup/recovery leaves the unavailable reaction family.
    stream.publish_sample(
        "P2-C1",
        packet(seq=3, sample_ns=300, action_id=103, hitstun_remaining=0, untech_remaining=0),
    )

    events = list(stream.snapshot())
    kinds = [event.kind for event in events]
    assert EVENT_REACTION_BEGIN in kinds
    assert EVENT_HITSTUN_END in kinds
    assert EVENT_REACTION_END in kinds
    assert kinds.index(EVENT_REACTION_BEGIN) < kinds.index(EVENT_HITSTUN_END)


def test_secondary_reaction_timer_can_hold_reaction_without_stun_bar():
    from tvcgui.runtime.mission_events import EVENT_REACTION_BEGIN, EVENT_REACTION_END

    stream = MissionEventStream()
    stream.publish_sample("P2-C1", packet(action_id=10, reaction_timer_remaining=0))
    stream.publish_sample(
        "P2-C1",
        packet(seq=2, sample_ns=200, action_id=10, reaction_timer_remaining=12),
    )
    stream.publish_sample(
        "P2-C1",
        packet(seq=3, sample_ns=300, action_id=10, reaction_timer_remaining=0),
    )
    events = list(stream.snapshot())
    begin = next(event for event in events if event.kind == EVENT_REACTION_BEGIN)
    end = next(event for event in events if event.kind == EVENT_REACTION_END)
    assert begin.reaction_timer_remaining == 12
    assert end.reaction_timer_remaining == 0


def test_recovery_idle_emits_when_unavailable_state_clears_even_if_action_is_already_idle():
    from tvcgui.runtime.mission_events import EVENT_RECOVERY_IDLE

    stream = MissionEventStream()
    # Some reactions briefly expose universal idle action 1 while a native
    # recovery clock is still populated. Do not call that recovered yet.
    stream.publish_sample(
        "P2-C1",
        packet(action_id=1, hitstun_remaining=5, blockstun_remaining=0),
    )
    stream.publish_sample(
        "P2-C1",
        packet(seq=2, sample_ns=200, action_id=1, hitstun_remaining=0, blockstun_remaining=0),
    )
    events = list(stream.snapshot())
    idle = [event for event in events if event.kind == EVENT_RECOVERY_IDLE]
    assert len(idle) == 1
    assert idle[0].action_id == 1


def test_recovery_idle_waits_for_blockstun_and_special_reaction_to_clear():
    from tvcgui.runtime.mission_events import EVENT_RECOVERY_IDLE

    stream = MissionEventStream()
    stream.publish_sample(
        "P2-C1",
        packet(action_id=1, blockstun_remaining=7),
    )
    stream.publish_sample(
        "P2-C1",
        packet(seq=2, sample_ns=200, action_id=70, blockstun_remaining=0),
    )
    stream.publish_sample(
        "P2-C1",
        packet(seq=3, sample_ns=300, action_id=1, blockstun_remaining=0),
    )
    events = list(stream.snapshot())
    idle = [event for event in events if event.kind == EVENT_RECOVERY_IDLE]
    assert len(idle) == 1
    assert idle[0].sequence == max(event.sequence for event in events)
