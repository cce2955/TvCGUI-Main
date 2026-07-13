from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

from tests.v19_contract_helpers import path, read


class V19RegressionRunnerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runner_path = path("run_regression_tests.py")
        spec = importlib.util.spec_from_file_location("v19_regression_runner", runner_path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        cls.runner = module

    def test_runner_discovers_test_modules(self):
        suite = self.runner.discover_suite()
        self.assertGreater(len(self.runner.flatten_ids(suite)), 100)

    def test_runner_has_exact_test_id_manifest_support(self):
        self.assertIn("required_test_ids", read("run_regression_tests.py"))

    def test_runner_has_protected_file_hash_support(self):
        self.assertIn("protected_hashes", read("run_regression_tests.py"))

    def test_runner_compiles_all_active_python(self):
        self.assertIn("compile_active_python", read("run_regression_tests.py"))

    def test_runner_checks_cache_artifacts(self):
        self.assertIn("find_cache_artifacts", read("run_regression_tests.py"))

    def test_runner_protects_advantage_module(self):
        self.assertIn("tvcgui/ui/advantage_window.py", self.runner.PROTECTED_FILES)

    def test_runner_protects_ko_module(self):
        self.assertIn("tvcgui/runtime/ko_control.py", self.runner.PROTECTED_FILES)

    def test_runner_protects_hud_renderer(self):
        self.assertIn("tvcgui/features/overlay/hud_renderer.py", self.runner.PROTECTED_FILES)

    def test_runner_protects_observed_advantage_data(self):
        self.assertIn("data/frame_data/observed_block_advantage_profiles.json", self.runner.PROTECTED_FILES)

    def test_runner_requires_advantage_duplicate(self):
        self.assertIn("tdp-modules/tvcgui/ui/advantage_window.py", self.runner.REQUIRED_FILES)

    def test_runner_requires_ko_duplicate(self):
        self.assertIn("tdp-modules/tvcgui/runtime/ko_control.py", self.runner.REQUIRED_FILES)

    def test_baseline_has_more_than_100_tests(self):
        baseline = json.loads(path("test_contract_baseline.json").read_text(encoding="utf-8"))
        self.assertGreater(len(baseline.get("required_test_ids") or []), 100)

    def test_baseline_has_protected_hashes(self):
        baseline = json.loads(path("test_contract_baseline.json").read_text(encoding="utf-8"))
        self.assertTrue(baseline.get("protected_hashes"))

    def test_baseline_requires_v19_advantage_group(self):
        baseline = json.loads(path("test_contract_baseline.json").read_text(encoding="utf-8"))
        prefixes = baseline.get("required_prefixes") or []
        self.assertTrue(any("V19Advantage" in prefix for prefix in prefixes))

    def test_baseline_requires_v19_ko_group(self):
        baseline = json.loads(path("test_contract_baseline.json").read_text(encoding="utf-8"))
        prefixes = baseline.get("required_prefixes") or []
        self.assertTrue(any("V19KoControl" in prefix for prefix in prefixes))

    def test_runner_protects_its_own_python(self):
        self.assertIn("run_regression_tests.py", self.runner.PROTECTED_FILES)

    def test_runner_protects_its_batch_launcher(self):
        self.assertIn("run_regression_tests.bat", self.runner.PROTECTED_FILES)

    def test_runner_protects_v19_test_sources(self):
        self.assertIn("tests/test_v19_advantage_click_contract.py", self.runner.PROTECTED_FILES)
        self.assertIn("tests/test_v19_ko_control_contract.py", self.runner.PROTECTED_FILES)

    def test_check_baseline_detects_missing_required_test(self):
        suite = self.runner.discover_suite()
        ids = self.runner.flatten_ids(suite)
        baseline = {
            "minimum_tests": 0,
            "required_test_ids": ["tests.missing.Contract.test_missing"],
            "required_prefixes": [],
            "critical_modules": [],
            "required_files": [],
            "protected_hashes": {},
        }
        problems = self.runner.check_baseline(ids, baseline)
        self.assertTrue(any("required test missing" in problem for problem in problems))

    def test_check_baseline_detects_test_count_regression(self):
        baseline = {
            "minimum_tests": 9999,
            "required_test_ids": [],
            "required_prefixes": [],
            "critical_modules": [],
            "required_files": [],
            "protected_hashes": {},
        }
        problems = self.runner.check_baseline([], baseline)
        self.assertTrue(any("test count regressed" in problem for problem in problems))

    def test_check_baseline_detects_missing_required_file(self):
        baseline = {
            "minimum_tests": 0,
            "required_test_ids": [],
            "required_prefixes": [],
            "critical_modules": [],
            "required_files": ["missing_v19_file.py"],
            "protected_hashes": {},
        }
        problems = self.runner.check_baseline([], baseline)
        self.assertTrue(any("required file missing" in problem for problem in problems))

    def test_check_baseline_detects_protected_hash_change(self):
        baseline = {
            "minimum_tests": 0,
            "required_test_ids": [],
            "required_prefixes": [],
            "critical_modules": [],
            "required_files": [],
            "protected_hashes": {"main.py": "0" * 64},
        }
        problems = self.runner.check_baseline([], baseline)
        self.assertTrue(any("protected file changed" in problem for problem in problems))

    def test_full_active_compile_has_no_failures(self):
        self.assertEqual(self.runner.compile_active_python(), [])

    def test_clean_tree_has_no_cache_artifacts(self):
        self.assertEqual(self.runner.find_cache_artifacts(), [])

    def test_batch_checks_parent_project_venv(self):
        batch = read("run_regression_tests.bat").lower().replace("/", "\\")
        self.assertIn("..\\.venv\\scripts\\python.exe", batch)

    def test_batch_prints_selected_interpreter(self):
        self.assertIn("[regression] Using interpreter:", read("run_regression_tests.bat"))

    def test_runner_reports_discovery_failures_before_baseline(self):
        source = read("run_regression_tests.py")
        self.assertIn("discovery_failures(suite)", source)
        self.assertLess(source.index("import_failures = discovery_failures(suite)"), source.index("baseline_problems = check_baseline"))

    def test_runner_installs_offline_dolphin_stub(self):
        self.assertIn("install_offline_dolphin_stub", read("run_regression_tests.py"))

    def test_runner_has_pygame_interpreter_preflight(self):
        source = read("run_regression_tests.py")
        self.assertIn("dependency_problems", source)
        self.assertIn("pygame is unavailable", source)


if __name__ == "__main__":
    unittest.main()
