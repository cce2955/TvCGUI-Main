from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
SCAN_WORKER = ROOT / "tvcgui" / "tools" / "scanners" / "normal_scan_worker.py"
ATTACK = ROOT / "tvcgui" / "features" / "training" / "attack_property_profiler.py"
METER = ROOT / "tvcgui" / "features" / "training" / "meter_generation_profiler.py"
RED = ROOT / "tvcgui" / "features" / "training" / "red_health_profiler.py"


def test_heavy_scans_are_process_isolated_and_lower_priority():
    source = SCAN_WORKER.read_text(encoding="utf-8")
    assert "multiprocessing.get_context(\"spawn\")" in source
    assert "_scan_process_entry" in source
    assert "_lower_child_priority" in source
    assert "preview_only=True" in source


def test_auto_profile_bootstrap_is_one_character_per_chunk():
    source = MAIN.read_text(encoding="utf-8")
    assert "target_ids = all_target_ids[:1]" in source
    assert "retain_rich=False" in source


def test_runtime_telemetry_uses_background_scheduler():
    source = MAIN.read_text(encoding="utf-8")
    assert "RuntimeProfilerScheduler" in source
    assert "runtime_profiler_scheduler.submit" in source
    assert "runtime_profiler_scheduler.apply_latest" in source


def test_profiler_disk_writes_are_deferred():
    for path in (ATTACK, METER, RED):
        source = path.read_text(encoding="utf-8")
        assert "DeferredWorkLoop" in source
    assert "self._write_pending(force=False)\n        return changed" not in ATTACK.read_text(encoding="utf-8")
    assert "writer.writerow({key: event.get(key, \"\") for key in CSV_FIELDS})" not in METER.read_text(encoding="utf-8").split("def update", 1)[0]
    assert "writer.writerow({key: event.get(key, \"\") for key in CSV_FIELDS})" not in RED.read_text(encoding="utf-8").split("def update", 1)[0]


def test_mission_megacrash_runs_before_timing_and_telemetry():
    source = MAIN.read_text(encoding="utf-8")
    mission = source.index("mission_mgr.update")
    megacrash = source.index("mission_megacrash_rt.sync", mission)
    timing = source.index("TIMING_ENGINE.update", mission)
    telemetry = source.index("runtime_profiler_scheduler.submit", mission)
    assert mission < megacrash < timing < telemetry


def test_large_workbench_prewarm_respects_disabled_default():
    source = MAIN.read_text(encoding="utf-8")
    assert "FD_WORKBENCH_PREWARM_ENABLED and not fd_workbench_prewarmed" in source


def test_overlay_payload_serialization_runs_on_latest_only_writer_thread():
    manager = ROOT / "tvcgui" / "features" / "overlay" / "manager.py"
    source = manager.read_text(encoding="utf-8")
    assert "TvCOverlayPayloadWriter" in source
    assert "self._pending_payload = payload" in source
    assert "self._queue_payload(payload)" in source
    assert "hud_mgr.close()" in MAIN.read_text(encoding="utf-8")


def test_mission_payload_writer_is_latest_only_and_nonblocking():
    manager = ROOT / "tvcgui" / "features" / "training" / "mission_manager.py"
    source = manager.read_text(encoding="utf-8")
    assert "TvCMissionOverlayWriter" in source
    assert "self._pending_overlay_payload = dict(payload or {})" in source
    assert "self._queue_overlay_payload(payload)" in source
    assert "mission_mgr.close()" in MAIN.read_text(encoding="utf-8")
