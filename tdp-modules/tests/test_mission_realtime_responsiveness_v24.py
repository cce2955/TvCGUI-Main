import sys
import types
import time

sys.modules.setdefault(
    "dolphin_memory_engine",
    types.SimpleNamespace(
        read_bytes=lambda *args, **kwargs: b"",
        write_bytes=lambda *args, **kwargs: None,
        is_hooked=lambda: False,
        hook=lambda: None,
        un_hook=lambda: None,
    ),
)


from tvcgui.features.overlay.manager import HudOverlayManager
from tvcgui.features.training.mission_manager import MissionManager
from tvcgui.runtime.mission_events import MissionEventStream
from tvcgui.runtime.realtime_sampler import RealtimeCombatSampler


class _FakeSampler:
    def __init__(self):
        self.listeners = []
        self.targets = {}

    def add_listener(self, listener):
        self.listeners.append(listener)

    def remove_listener(self, listener):
        self.listeners = [item for item in self.listeners if item != listener]

    def set_targets(self, targets):
        self.targets = dict(targets or {})

    def snapshot_for_slot(self, slot_label, fighter_base=0):
        return {}, []


class _FakeMissionManager:
    active_slot = None

    def __init__(self):
        self.provider = None

    def set_event_provider(self, provider):
        self.provider = provider


def _packet(**overrides):
    packet = {
        "base": 0x90000000,
        "char_id": 12,
        "action_id": 0,
        "action_frame": 0,
        "held": 0,
        "pressed": 0,
        "released": 0,
        "current_hp": 50000,
        "current_meter": 0,
        "blockstun_remaining": 0,
        "hitstun_remaining": 0,
        "untech_remaining": 0,
        "reaction_timer_remaining": 0,
        "impact_freeze_remaining": 0,
        "fighter_combo_count": 0,
        "decay_counter": 0,
        "state_flags_6c": 0,
        "combo_count": 0,
        "point_active": True,
    }
    packet.update(overrides)
    return packet


def test_overlay_manager_guarantees_mission_event_stream_wiring():
    sampler = _FakeSampler()
    hud = HudOverlayManager({}, {}, realtime_sampler=sampler)
    mission = _FakeMissionManager()
    try:
        assert any(getattr(listener, "__self__", None).__class__ is MissionEventStream for listener in sampler.listeners)
        hud.write_data({}, None, mission)
        assert callable(mission.provider)
    finally:
        hud.close()


def test_sampler_derives_button_press_from_held_transition_when_native_edge_is_missed():
    sampler = RealtimeCombatSampler(autostart=False)
    try:
        sampler.publish_packet("P1-C1", _packet(held=0, pressed=0))
        sampler.publish_packet("P1-C1", _packet(held=0x80, pressed=0))
        _latest, samples = sampler.snapshot_for_slot("P1-C1")
        assert samples
        assert int(samples[-1]["pressed"]) & 0x80
    finally:
        sampler.close()


def test_mission_runtime_advances_off_gui_thread():
    manager = MissionManager({}, {}, [], lambda: [], lambda *args, **kwargs: "")
    stream = MissionEventStream()
    try:
        stream.publish_sample("P1-C1", {**_packet(), "seq": 1, "sample_ns": 100})
        manager.set_event_provider(stream.events_since)
        floor = stream.latest_sequence
        key = manager._set_mission_realtime_context(
            route_slot="P1-C1",
            character_name="Ryu",
            mission_id="rt-test",
            steps=[{"label": "5A", "pass": True}],
            mission_goal={},
            event_floor=floor,
            char_id=12,
        )
        manager._last_overlay_payload = {
            "active": True,
            "active_mission_id": "rt-test",
            "completed_step_count": 0,
            "current_step_index": 0,
        }

        stream.publish_sample(
            "P1-C1",
            {**_packet(), "seq": 2, "sample_ns": 200, "action_id": 0x100, "action_frame": 1},
        )

        deadline = time.monotonic() + 0.075
        result = None
        while time.monotonic() < deadline:
            result = manager._realtime_result_for_key(key, [{"label": "5A", "pass": True}], floor)
            if result.cleared or result.progress_index >= 1:
                break
            time.sleep(0.001)

        assert result is not None
        assert result.cleared or result.progress_index >= 1
    finally:
        manager.close()
