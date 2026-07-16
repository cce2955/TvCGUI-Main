from __future__ import annotations

from collections import deque
from pathlib import Path

from tvcgui.core import action_event_bus as BUS
from tvcgui.features.frame_data import cancel_lab as CL
from tvcgui.features.training import action_recorder_window as AR

ROOT = Path(__file__).resolve().parents[1]


def test_event_bus_returns_only_newer_events():
    BUS.clear_action_events()
    first = BUS.publish_action_event("cancel_request", tool="cancel_lab", slot="P1-C1", source_id=0x100, target_id=0x101)
    cursor, events = BUS.action_events_since(0)
    assert cursor == first["sequence"]
    assert [event["event_type"] for event in events] == ["cancel_request"]
    second = BUS.publish_action_event("cancel_accepted", tool="cancel_lab", slot="P1-C1", source_id=0x100, target_id=0x101)
    cursor2, events2 = BUS.action_events_since(cursor)
    assert cursor2 == second["sequence"]
    assert [event["event_type"] for event in events2] == ["cancel_accepted"]


def test_cancel_lab_request_publishes_authoritative_profile_context(monkeypatch):
    captured = []
    monkeypatch.setattr(CL, "publish_action_event", lambda event_type, **payload: captured.append((event_type, payload)))
    lab = CL.CancelLabWindow.__new__(CL.CancelLabWindow)
    lab.slot_label = "P1-C1"
    lab.char_name = "Ryu"
    lab.armed_profile_rules = ({"source_id": 0x100},)
    lab.armed = True
    lab.armed_mode = "manual"
    lab.armed_earliest = 6
    lab.armed_latest = 25
    lab.request_pending = False
    lab.request_addr = 0
    lab.request_value = 0
    lab.request_source_id = 0
    lab.request_target_id = 0
    lab.request_source_frame = 0
    lab.request_started_at = 0.0
    lab.request_origin = ""
    lab.request_reason = ""

    lab._set_request_state(
        request_addr=0x92400200,
        encoded=CL.mailbox_value_for_action(0x139),
        source_id=0x138,
        target_id=0x139,
        frame=85,
        reason="Manual input recognized (special command candidate)",
    )

    assert lab.request_origin == "profile"
    assert captured and captured[0][0] == "cancel_request"
    payload = captured[0][1]
    assert payload["source_id"] == 0x138
    assert payload["target_id"] == 0x139
    assert payload["source_frame"] == 85
    assert payload["earliest"] == 6
    assert payload["latest"] == 25
    assert payload["profile"] is True


def test_recorder_prefers_profile_cancel_event_for_exact_transition():
    recorder = AR.ActionRecorderWindow.__new__(AR.ActionRecorderWindow)
    recorder.slot_label = "P1-C1"
    recorder._recent_cancel_events = deque(
        [
            {
                "event_type": "cancel_request",
                "tool": "cancel_lab",
                "slot": "P1-C1",
                "source_id": 0x138,
                "target_id": 0x139,
                "source_frame": 85,
                "origin": "profile",
                "monotonic": 10.0,
            }
        ],
        maxlen=AR.MAX_CANCEL_EVENTS,
    )
    event = recorder._best_cancel_lab_event(0x138, 0x139, 10.1)
    assert event is not None
    assert recorder._cancel_event_cause(event) == "Profile cancel"
    assert "source frame 85" in recorder._cancel_event_note(event, accepted_transition=True)


def test_bridge_files_have_no_em_dash():
    for relative in (
        "tvcgui/core/action_event_bus.py",
        "tvcgui/features/frame_data/cancel_lab.py",
        "tvcgui/features/training/action_recorder_window.py",
    ):
        assert "—" not in (ROOT / relative).read_text(encoding="utf-8")
