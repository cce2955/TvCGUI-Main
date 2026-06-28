from __future__ import annotations
import time
import unittest

from tvcgui.tools.scanners.normal_scan_worker import ScanNormalsWorker


def wait_for(worker, expected_mode, timeout=1.0):
    end = time.time() + timeout
    while time.time() < end:
        result, _ts = worker.get_latest()
        if result is not None and worker.last_mode() == expected_mode:
            return result
        time.sleep(0.005)
    raise AssertionError(f"worker did not finish {expected_mode!r}")


class ScanWorkerContractTests(unittest.TestCase):
    def test_preview_request_runs_preview_function(self):
        worker = ScanNormalsWorker(lambda: ["preview"])
        worker.start()
        worker.request()
        self.assertEqual(wait_for(worker, "cache"), ["preview"])

    def test_workbench_request_runs_workbench_function(self):
        worker = ScanNormalsWorker(lambda: ["preview"], workbench_scan_func=lambda: ["workbench"])
        worker.start()
        worker.request(workbench=True)
        self.assertEqual(wait_for(worker, "workbench"), ["workbench"])

    def test_full_request_runs_full_function(self):
        worker = ScanNormalsWorker(
            lambda: ["preview"],
            workbench_scan_func=lambda: ["workbench"],
            full_scan_func=lambda **kw: ["full", kw],
        )
        worker.start()
        worker.request(force_dynamic=True, dynamic_char_ids=(17,))
        result = wait_for(worker, "full")
        self.assertEqual(result[0], "full")
        self.assertEqual(result[1]["dynamic_char_ids"], (17,))

    def test_full_request_wins_over_workbench_when_coalesced_before_start(self):
        worker = ScanNormalsWorker(
            lambda: ["preview"],
            workbench_scan_func=lambda: ["workbench"],
            full_scan_func=lambda: ["full"],
        )
        worker.request()
        worker.request(workbench=True)
        worker.request(force_dynamic=True)
        worker.start()
        self.assertEqual(wait_for(worker, "full"), ["full"])

    def test_request_count_tracks_requests(self):
        worker = ScanNormalsWorker(lambda: ["preview"])
        worker.request()
        worker.request()
        self.assertEqual(worker.request_count(), 2)

    def test_last_mode_starts_as_none(self):
        worker = ScanNormalsWorker(lambda: ["preview"])
        self.assertEqual(worker.last_mode(), "none")
