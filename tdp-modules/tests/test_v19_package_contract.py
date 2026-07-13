from __future__ import annotations

import json
import unittest
from pathlib import Path

from tests.v19_contract_helpers import APP_DIR, path, read, sha256


class V19PackageContractTests(unittest.TestCase):
    def test_primary_entrypoint_exists(self):
        self.assertTrue(path("main.py").is_file())

    def test_launcher_exists(self):
        self.assertTrue(path("launcher.py").is_file())

    def test_regression_batch_exists(self):
        self.assertTrue(path("run_regression_tests.bat").is_file())

    def test_regression_runner_exists(self):
        self.assertTrue(path("run_regression_tests.py").is_file())

    def test_baseline_manifest_exists(self):
        self.assertTrue(path("test_contract_baseline.json").is_file())

    def test_baseline_manifest_is_valid_json(self):
        data = json.loads(path("test_contract_baseline.json").read_text(encoding="utf-8"))
        self.assertIsInstance(data, dict)

    def test_no_pyc_files_are_checked_in(self):
        pyc = [item for item in APP_DIR.rglob("*.pyc") if item.is_file()]
        self.assertEqual(pyc, [])

    def test_no_pycache_directories_are_checked_in(self):
        caches = [item for item in APP_DIR.rglob("__pycache__") if item.is_dir()]
        self.assertEqual(caches, [])

    def test_no_gecko_files_were_added(self):
        gecko = [item for item in APP_DIR.rglob("*.gct") if item.is_file()]
        self.assertEqual(gecko, [])

    def test_no_dolphin_configuration_is_packaged(self):
        forbidden = []
        for item in APP_DIR.rglob("*"):
            if not item.is_file():
                continue
            low = item.name.lower()
            if low in {"dolphin.ini", "gfx.ini", "wiimote.ini", "gamecube.ini"}:
                forbidden.append(item)
        self.assertEqual(forbidden, [])

    def test_advantage_primary_and_duplicate_match(self):
        self.assertEqual(sha256("tvcgui/ui/advantage_window.py"), sha256("tdp-modules/tvcgui/ui/advantage_window.py"))

    def test_ko_primary_and_duplicate_match(self):
        self.assertEqual(sha256("tvcgui/runtime/ko_control.py"), sha256("tdp-modules/tvcgui/runtime/ko_control.py"))

    def test_advantage_observed_data_is_not_empty(self):
        doc = json.loads(path("data/frame_data/observed_block_advantage_profiles.json").read_text(encoding="utf-8"))
        self.assertTrue(doc.get("profiles"))

    def test_mission_files_are_present(self):
        missions = list(path("missions").glob("*.json"))
        self.assertGreaterEqual(len(missions), 25)

    def test_runtime_package_init_exists(self):
        self.assertTrue(path("tvcgui/runtime/__init__.py").is_file())

    def test_ui_package_init_exists(self):
        self.assertTrue(path("tvcgui/ui/__init__.py").is_file())

    def test_overlay_renderer_exists(self):
        self.assertTrue(path("tvcgui/features/overlay/hud_renderer.py").is_file())

    def test_master_renderer_exists(self):
        self.assertTrue(path("tvcgui/features/overlay/master_renderer.py").is_file())

    def test_mission_manager_exists(self):
        self.assertTrue(path("tvcgui/features/training/mission_manager.py").is_file())

    def test_test_suite_has_multiple_v19_modules(self):
        modules = list(path("tests").glob("test_v19_*.py"))
        self.assertGreaterEqual(len(modules), 6)

    def test_runner_disables_bytecode_writes(self):
        self.assertIn("sys.dont_write_bytecode = True", read("run_regression_tests.py"))

    def test_batch_disables_bytecode_writes(self):
        self.assertIn("PYTHONDONTWRITEBYTECODE", read("run_regression_tests.bat"))


if __name__ == "__main__":
    unittest.main()
