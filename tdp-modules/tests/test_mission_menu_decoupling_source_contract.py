from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_main_wires_standalone_menu_interpreter_to_realtime_sampler() -> None:
    source = read("main.py")
    assert "MissionMenuInputInterpreter" in source
    assert "realtime_sampler.add_listener(mission_menu_input.on_sample)" in source
    assert "mission_mgr.set_menu_input_interpreter(mission_menu_input)" in source


def test_mission_manager_consumes_commands_not_raw_gesture_edges() -> None:
    source = read("tvcgui/features/training/mission_manager.py")
    start = source.index("    def _consume_menu_input_commands")
    end = source.index("    # ------------------------------------------------------------------\n    # Team / slot helpers", start)
    block = source[start:end]
    assert "drain_commands" in block
    assert "MISSION_MENU_OPEN" in block
    assert "MISSION_MENU_SELECT" in block
    assert "held =" not in block
    assert "pressed =" not in block
    assert "down_rising" not in block
    assert "taunt_rising" not in block


def test_realtime_packet_contains_native_point_flag() -> None:
    input_source = read("tvcgui/runtime/input_monitor.py")
    sampler_source = read("tvcgui/runtime/realtime_sampler.py")
    assert "point_active = bool(_read_u32(base + 0x44A0))" in input_source
    assert '"point_active": point_active' in input_source
    assert '"point_active": point_active' in sampler_source


def test_completed_mission_reasserts_idle() -> None:
    source = read("tvcgui/features/training/mission_manager.py")
    assert "Completion owns CpuAction" in source
    assert "idle is the permanent" in source
