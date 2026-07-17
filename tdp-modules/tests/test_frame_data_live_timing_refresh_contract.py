from __future__ import annotations

from pathlib import Path


def test_frame_data_workbench_refreshes_live_observations_without_rebuild():
    source = Path("tvcgui/features/frame_data/workbench.py").read_text(encoding="utf-8")
    assert "_schedule_live_timing_observation_refresh" in source
    assert "_refresh_live_timing_observations" in source
    assert "change_log=changes" in source
    assert "self.tree.set(" in source
    assert "self.root.after(750" in source
    assert "self._rebuild_tree_with_moves" not in source[source.index("def _refresh_live_timing_observations"):source.index("def _queue_auto_profile_build")]


def test_frame_data_close_cancels_live_observation_callback():
    source = Path("tvcgui/features/frame_data/workbench.py").read_text(encoding="utf-8")
    close_block = source[source.index("def _on_close"):source.index("def _reset_optional_probe_session")]
    assert "_timing_observation_after_id" in close_block
    assert "after_cancel" in close_block
