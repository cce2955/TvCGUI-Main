from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MISSION_MANAGER = (ROOT / "tvcgui/features/training/mission_manager.py").read_text(encoding="utf-8")


def test_progress_edges_publish_synchronously():
    assert "progress_changed = progress_signature != self._last_overlay_progress_signature" in MISSION_MANAGER
    assert "if progress_changed or force:" in MISSION_MANAGER
    assert "self._write_overlay_payload_file(publish_payload)" in MISSION_MANAGER


def test_async_writer_cannot_overwrite_newer_progress():
    assert 'publish_seq < int(self._last_overlay_file_seq or 0)' in MISSION_MANAGER
    assert 'packet.pop("__publish_seq", 0)' in MISSION_MANAGER
    assert "self._overlay_file_lock = threading.RLock()" in MISSION_MANAGER
