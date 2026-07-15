from __future__ import annotations

import unittest

from tests.v19_contract_helpers import read


class V19PerformanceContractTests(unittest.TestCase):
    def test_runtime_csv_export_defaults_off(self):
        source = read("main.py")
        self.assertIn('os.environ.get("TVC_FD_AUTO_EXPORT", "0")', source)

    def test_runtime_exporter_creation_is_opt_in(self):
        source = read("main.py")
        self.assertIn(
            "if FrameDataSpreadsheetExporter is not None and FD_AUTO_EXPORT_ENABLED:",
            source,
        )

    def test_disabled_exporter_cannot_queue_workbench_csv_scan(self):
        source = read("main.py")
        self.assertIn("if frame_data_exporter is not None and scan_worker", source)
        self.assertIn("if scan_worker and frame_data_exporter is not None", source)


if __name__ == "__main__":
    unittest.main()
