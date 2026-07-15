from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

from tvcgui.features.frame_data import cancel_windows as CW

ROOT = pathlib.Path(__file__).resolve().parents[1]


class CustomCancelWindowContractTests(unittest.TestCase):
    def setUp(self):
        self._old_path = CW.PROFILE_PATH
        self._old_cache = CW._CACHE
        self._old_mtime = CW._CACHE_MTIME
        self.tempdir = tempfile.TemporaryDirectory()
        CW.PROFILE_PATH = str(pathlib.Path(self.tempdir.name) / "custom_cancel_windows.json")
        CW._CACHE = None
        CW._CACHE_MTIME = None

    def tearDown(self):
        CW.PROFILE_PATH = self._old_path
        CW._CACHE = self._old_cache
        CW._CACHE_MTIME = self._old_mtime
        self.tempdir.cleanup()

    def test_parse_and_format(self):
        self.assertEqual(CW.parse_window_text("8+"), (8, 0))
        self.assertEqual(CW.parse_window_text("8-20"), (8, 20))
        self.assertEqual(CW.format_window((8, 0)), "8+")
        self.assertEqual(CW.format_window((8, 20)), "8-20")
        self.assertIsNone(CW.parse_window_text("clear"))

    def test_persists_source_window_and_tested_target(self):
        saved = CW.set_window("Casshan", 0x0102, 8, 0, source="test", tested_target_id=0x0101)
        self.assertEqual(CW.format_window(saved), "8+")
        loaded = CW.get_window("Casshan", 0x0102)
        self.assertEqual(loaded["tested_targets"], [0x0101])
        raw = json.loads(pathlib.Path(CW.PROFILE_PATH).read_text(encoding="utf-8"))
        self.assertIn("casshan", raw["characters"])

    def test_frame_data_ui_exposes_editable_cancel_window(self):
        tree = (ROOT / "tvcgui" / "features" / "frame_data" / "tree.py").read_text(encoding="utf-8")
        workbench = (ROOT / "tvcgui" / "features" / "frame_data" / "workbench.py").read_text(encoding="utf-8")
        lab = (ROOT / "tvcgui" / "features" / "frame_data" / "cancel_lab.py").read_text(encoding="utf-8")
        self.assertIn('"custom_cancel_window"', tree)
        self.assertIn("CUSTOM CANCEL", workbench)
        self.assertIn("_edit_custom_cancel_window", workbench)
        self.assertIn("Save window to Frame Data", lab)
        self.assertIn("Manual input mode waits for TvC to recognize", lab)


if __name__ == "__main__":
    unittest.main()
