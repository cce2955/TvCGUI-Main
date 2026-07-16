from __future__ import annotations

import importlib.util
import sys
import types
from collections import deque
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tvcgui" / "features" / "training" / "action_recorder_window.py"
MODULE_TEXT = MODULE_PATH.read_text(encoding="utf-8")


def _load_module():
    packages = [
        "tvcgui",
        "tvcgui.core",
        "tvcgui.features",
        "tvcgui.features.combat",
        "tvcgui.features.frame_data",
        "tvcgui.features.training",
        "tvcgui.platform",
    ]
    for name in packages:
        sys.modules.setdefault(name, types.ModuleType(name))

    tk_host = types.ModuleType("tvcgui.core.tk_host")
    tk_host.tk_call = lambda fn: None
    sys.modules[tk_host.__name__] = tk_host

    bus_path = ROOT / "tvcgui" / "core" / "action_event_bus.py"
    bus_spec = importlib.util.spec_from_file_location("tvcgui.core.action_event_bus", bus_path)
    bus_module = importlib.util.module_from_spec(bus_spec)
    sys.modules[bus_spec.name] = bus_module
    assert bus_spec.loader is not None
    bus_spec.loader.exec_module(bus_module)

    moves = types.ModuleType("tvcgui.features.combat.moves")
    moves.CHAR_ID_CORRECTION = {"Ryu": 12}
    moves.move_label_for = lambda action, char_id, move_map, global_map: move_map.get(char_id, {}).get(action, global_map.get(action, f"FLAG_{action}"))
    sys.modules[moves.__name__] = moves

    widgets = types.ModuleType("tvcgui.features.frame_data.widgets")
    widgets.apply_titlebar_icon = lambda *args, **kwargs: None
    sys.modules[widgets.__name__] = widgets

    dolphin = types.ModuleType("tvcgui.platform.dolphin")
    dolphin.rd32 = lambda addr: 0
    sys.modules[dolphin.__name__] = dolphin

    spec = importlib.util.spec_from_file_location("tvcgui.features.training.action_recorder_window", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_recorder_has_clean_transition_and_attempt_views():
    assert 'self.event_tabs.add(transitions_tab, text="Transitions")' in MODULE_TEXT
    assert 'self.event_tabs.add(attempts_tab, text="Attempts")' in MODULE_TEXT
    assert 'text="Capture rejected attempts"' in MODULE_TEXT
    assert "self.transition_records" in MODULE_TEXT
    assert "self.attempt_records" in MODULE_TEXT


def test_sampler_is_faster_but_ui_refresh_is_throttled():
    assert "POLL_MS = 8" in MODULE_TEXT
    assert "UI_REFRESH_SEC = 1.0 / 30.0" in MODULE_TEXT
    assert "self._command_buffer" in MODULE_TEXT
    assert "def _best_buffer_evidence" in MODULE_TEXT


def test_context_filter_rejects_air_row_while_grounded():
    module = _load_module()
    assert module.action_environment(0x0100, "5A") == "ground"
    assert module.action_environment(0x0109, "j.A") == "air"
    assert module.normal_candidate_matches_context(0x0100, "5A", 0x0100, "5A")
    assert not module.normal_candidate_matches_context(0x0100, "5A", 0x0109, "j.A")
    assert module.normal_candidate_matches_context(0x010A, "j.B", 0x010B, "j.C")
    assert not module.normal_candidate_matches_context(0x010A, "j.B", 0x0102, "5C")


def test_recognized_rows_keep_only_current_environment(monkeypatch):
    module = _load_module()
    recorder = module.ActionRecorderWindow.__new__(module.ActionRecorderWindow)
    recorder._edge_serial = 0
    recorder._last_normal_signature = None
    recorder._last_special_signature = None
    recorder._label = lambda action, char_id: {0x100: "5A", 0x109: "j.A"}.get(action, f"0x{action:04X}")
    rows = [
        {"target": 0x100, "row": 0x9000, "index": 0, "direction": 0, "buttons": 0x80},
        {"target": 0x109, "row": 0x9018, "index": 1, "direction": 0, "buttons": 0x80},
    ]
    monkeypatch.setattr(module, "_recognized_normal_actions", lambda *args: rows)
    found = recorder._recognized_targets(0x92400000, 0x100, 12, 0, 0x80, 0, 0xFFFFFFFF, 10.0)
    assert set(found) == {0x100}


def test_accepted_target_consumes_sibling_candidates_from_same_edge():
    module = _load_module()
    recorder = module.ActionRecorderWindow.__new__(module.ActionRecorderWindow)
    recorder._pending_commands = {
        (7, 0x100): {"edge_id": 7, "target": 0x100, "time": 1.0},
        (7, 0x109): {"edge_id": 7, "target": 0x109, "time": 1.0},
        (8, 0x101): {"edge_id": 8, "target": 0x101, "time": 2.0},
    }
    pending = recorder._pop_pending_for_target(0x100)
    assert pending is not None
    assert set(recorder._pending_commands) == {(8, 0x101)}


def test_rolling_buffer_prefers_exact_target_evidence():
    module = _load_module()
    recorder = module.ActionRecorderWindow.__new__(module.ActionRecorderWindow)
    recorder._command_buffer = deque(
        [
            {"time": 9.8, "pressed": 0x80, "recognized": {}},
            {"time": 9.9, "pressed": 0x20, "recognized": {0x130: {"kind": "special command candidate", "detail": "raw 0x30"}}},
        ],
        maxlen=96,
    )
    sample, evidence = recorder._best_buffer_evidence(0x130, 10.0)
    assert sample is not None
    assert evidence is not None
    assert evidence["kind"] == "special command candidate"


def test_no_em_dashes_added():
    assert "—" not in MODULE_TEXT


def test_raw_probe_reads_full_recomp_packet_and_has_own_view():
    assert "OFF_RAW_SPECIAL_METADATA = 0x2110" in MODULE_TEXT
    assert "OFF_RAW_SPECIAL_PARAM = 0x2114" in MODULE_TEXT
    assert 'self.event_tabs.add(probe_tab, text="Raw Probe")' in MODULE_TEXT
    assert "self.probe_records" in MODULE_TEXT
    assert "def _best_raw_packet" in MODULE_TEXT


def test_action_to_command_index_bridge_matches_recomp_mapping():
    module = _load_module()
    assert module.command_index_for_action(0x130) == 0x30
    assert module.command_index_for_action(0x131) == 0x31
    assert module.command_index_for_action(0x161) == 0x61
    assert module.command_index_for_action(0x0101) is None
    assert module.command_index_for_action(0x0001) is None


def test_raw_packet_exact_target_match_uses_cooked_or_raw_index():
    module = _load_module()
    assert module._packet_matches_target({"cooked": 0x30, "raw": 0xFFFFFFFF}, 0x130)
    assert module._packet_matches_target({"cooked": 0, "raw": 0x32}, 0x132)
    assert not module._packet_matches_target({"cooked": 0x31, "raw": 0xFFFFFFFF}, 0x130)


def test_best_raw_packet_prefers_exact_index_over_newer_unmatched_packet():
    module = _load_module()
    recorder = module.ActionRecorderWindow.__new__(module.ActionRecorderWindow)
    recorder._command_buffer = deque(
        [
            {
                "time": 9.80,
                "pressed": 0x80,
                "cooked": 0x30,
                "raw": 0xFFFFFFFF,
                "cooked_flags": 1,
                "raw_flags": 0,
                "raw_metadata": 0x1234,
                "raw_param_word": 0,
            },
            {
                "time": 9.95,
                "pressed": 0x40,
                "cooked": 0x31,
                "raw": 0xFFFFFFFF,
                "cooked_flags": 1,
                "raw_flags": 0,
                "raw_metadata": 0x5678,
                "raw_param_word": 0,
            },
        ],
        maxlen=160,
    )
    sample, exact, age_ms = recorder._best_raw_packet(0x130, 10.0)
    assert sample is not None
    assert exact is True
    assert sample["cooked"] == 0x30
    assert 199.0 <= age_ms <= 201.0


def test_changed_raw_packet_is_logged_without_polluting_transition_records():
    module = _load_module()
    recorder = module.ActionRecorderWindow.__new__(module.ActionRecorderWindow)
    recorder._command_buffer = deque(maxlen=160)
    recorder._last_raw_packet_signature = None
    recorder.recording = True
    captured = []
    recorder._append_probe_record = lambda **kwargs: captured.append(kwargs)
    recorder._capture_command_sample(
        now=10.0,
        action=0x102,
        char_id=12,
        held=0x6,
        pressed=0x80,
        cooked_flags=1,
        cooked=0x30,
        raw_flags=2,
        raw=0x30,
        raw_metadata=0x12345678,
        raw_param_word=0x3F800000,
        raw_param_float=1.0,
        mailbox_raw=0,
        mailbox_target=None,
        recognized={},
    )
    assert len(captured) == 1
    assert captured[0]["raw_metadata"] == 0x12345678
    assert len(recorder._command_buffer) == 1
