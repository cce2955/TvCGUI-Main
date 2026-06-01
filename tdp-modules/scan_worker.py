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
    def __init__(self, scan_func, full_scan_func=None):
        """
        scan_func: callable that takes no args and returns a lightweight scan result.
        full_scan_func: optional callable for full dynamic profile-building scans.
        """
        super().__init__(daemon=True)
        self._scan_func = scan_func
        self._full_scan_func = full_scan_func
        self._want = threading.Event()
        self._lock = threading.Lock()
        self._last = None
        self._last_ts = 0.0
        self._busy = False
        self._request_count = 0
        self._want_full = False
        self._last_mode = "none"

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
                    want_full = bool(self._want_full)
                    self._want_full = False
                func = self._full_scan_func if (want_full and self._full_scan_func is not None) else self._scan_func
                mode = "full" if (want_full and self._full_scan_func is not None) else "cache"
                res = func()
                now = time.time()
                with self._lock:
                    self._last = res
                    self._last_ts = now
                    self._last_mode = mode
            except Exception as e:
                print("scan worker failed:", e)
            finally:
                with self._lock:
                    self._busy = False

    def request(self, *, force_dynamic: bool = False):
        """Signal the worker to perform a scan.

        force_dynamic=True uses full_scan_func when one was provided. Requests
        coalesce, and a pending full scan wins over a pending cache scan.
        """
        with self._lock:
            self._request_count += 1
            if force_dynamic:
                self._want_full = True
        self._want.set()

    def is_busy(self):
        """Return True while the scan function is currently running."""
        with self._lock:
            return bool(self._busy)

    def request_count(self):
        """Return how many scan requests have been queued since start."""
        with self._lock:
            return int(self._request_count)

    def last_mode(self):
        """Return the mode of the last completed scan: cache, full, or none."""
        with self._lock:
            return str(self._last_mode)

    def get_latest(self):
        """
        Return (result, timestamp) of the last completed scan.
        result may be None if no scan has completed yet.
        """
        with self._lock:
            return self._last, self._last_ts
