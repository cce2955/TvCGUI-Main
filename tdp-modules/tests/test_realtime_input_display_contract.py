from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAMPLER = ROOT / "tvcgui" / "runtime" / "realtime_sampler.py"
INPUT_MONITOR = ROOT / "tvcgui" / "runtime" / "input_monitor.py"
HUD = ROOT / "tvcgui" / "features" / "overlay" / "hud_renderer.py"
MANAGER = ROOT / "tvcgui" / "features" / "overlay" / "manager.py"


def test_realtime_sampler_remains_240_hz_with_60_hz_floor() -> None:
    source = SAMPLER.read_text(encoding="utf-8")
    assert "REALTIME_SAMPLER_HZ = 240.0" in source
    assert "self._hz = max(60.0, float(hz or REALTIME_SAMPLER_HZ))" in source
    assert "interval = 1.0 / self._hz" in source


def test_realtime_sampler_preserves_capture_timestamp_on_each_sample() -> None:
    source = SAMPLER.read_text(encoding="utf-8")
    assert '"sample_ns": int(packet.get("sample_ns", 0) or time.monotonic_ns())' in source
    assert '"sample_ns"' in source
    assert "time.monotonic_ns()" in source


def test_input_packet_primary_path_uses_one_contiguous_fighter_snapshot() -> None:
    source = INPUT_MONITOR.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "read_overlay_input_packet"
    )
    function_source = ast.get_source_segment(source, function) or ""

    assert "realtime_span_end = 0x44A4" in function_source
    assert "realtime_span_size = realtime_span_end - OFF_CHAR_ID" in function_source
    assert "realtime_blob = rbytes(base + OFF_CHAR_ID, realtime_span_size)" in function_source
    assert 'struct.unpack_from(">I", realtime_blob, int(offset) - OFF_CHAR_ID)[0]' in function_source
    # The old segmented reads remain only as the explicit safety fallback.
    assert "Fallback keeps the older segmented reads" in function_source


def test_realtime_input_bridge_is_bounded_and_written_off_sampler_callback() -> None:
    source = MANAGER.read_text(encoding="utf-8")
    assert "def _input_bridge_writer_loop" in source
    assert "self._input_bridge_condition.notify_all()" in source
    assert "del samples[:-96]" in source
    assert 'tmp = f"{HUD_REALTIME_INPUT_FILE}.tmp"' in source
    assert "os.replace(tmp, HUD_REALTIME_INPUT_FILE)" in source


def test_hud_reads_and_merges_realtime_sidecar_independently_of_full_payload() -> None:
    source = HUD.read_text(encoding="utf-8")
    assert 'REALTIME_INPUT_FILE = user_data_path("overlay", "hud_input_realtime.json")' in source
    assert "def read_realtime_input_data()" in source
    assert "def _merge_realtime_inputs(slots: dict, realtime_payload: dict)" in source
    assert source.count("_merge_realtime_inputs(new_slots, read_realtime_input_data())") >= 2


def test_input_history_uses_sample_timestamp_on_the_60_fps_display_clock() -> None:
    source = HUD.read_text(encoding="utf-8")
    assert "now_ns = time.monotonic_ns()" in source
    assert "game_frame_ns = 1_000_000_000.0 / 60.0" in source
    assert 'sample_ns = int(sample.get("sample_ns", 0) or 0)' in source
    assert "age_frames = int(round((now_ns - sample_ns) / game_frame_ns))" in source
    assert "sample_frame = int(_frame) - max(0, age_frames)" in source


def test_batched_input_samples_cannot_be_retimed_backwards() -> None:
    source = HUD.read_text(encoding="utf-8")
    assert 'last_timestamp_frame = int(slot_anim.get("last_input_timestamp_frame", -999999)' in source
    assert "sample_frame = max(last_timestamp_frame, sample_frame)" in source
    assert 'slot_anim["last_input_timestamp_frame"] = last_timestamp_frame' in source


def test_input_history_motion_uses_fast_display_response() -> None:
    source = HUD.read_text(encoding="utf-8")
    assert 'team_anim["input_history_slide"] = 0.28' in source
    assert 'team_anim["input_history_slide"] = _approach(float(team_anim.get("input_history_slide", 0.0)), 0.0, 22.0, dt)' in source
