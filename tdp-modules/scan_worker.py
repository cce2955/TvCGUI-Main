# scan_worker.py
#
# Background worker so big MEM2 scans don't lag pygame.
# Generic: you pass in a scan function that returns the scan result.

import threading
import time


class ScanNormalsWorker(threading.Thread):
    """
    Background worker that calls a provided scan function whenever
    `request()` is signaled. The most recent result + timestamp
    can be retrieved via `get_latest()`.
    """
    def __init__(self, scan_func):
        """
        scan_func: callable that takes no args and returns a scan result.
        """
        super().__init__(daemon=True)
        self._scan_func = scan_func
        self._want = threading.Event()
        self._lock = threading.Lock()
        self._last = None
        self._last_ts = 0.0
        self._busy = False
        self._request_count = 0

    def run(self):
        while True:
            self._want.wait()
            self._want.clear()

            # If we have no scan function, just idle
            if self._scan_func is None:
                continue

            try:
                with self._lock:
                    self._busy = True
                res = self._scan_func()
                now = time.time()
                with self._lock:
                    self._last = res
                    self._last_ts = now
            except Exception as e:
                print("scan worker failed:", e)
            finally:
                with self._lock:
                    self._busy = False

    def request(self):
        """Signal the worker to perform a scan."""
        with self._lock:
            self._request_count += 1
        self._want.set()

    def is_busy(self):
        """Return True while the scan function is currently running."""
        with self._lock:
            return bool(self._busy)

    def request_count(self):
        """Return how many scan requests have been queued since start."""
        with self._lock:
            return int(self._request_count)

    def get_latest(self):
        """
        Return (result, timestamp) of the last completed scan.
        result may be None if no scan has completed yet.
        """
        with self._lock:
            return self._last, self._last_ts
