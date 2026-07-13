# scan_worker.py
#
# Background worker so big MEM2 scans don't lag pygame.
# Generic: the operator pass in a scan function that returns the scan result.

import threading
import time


class ScanNormalsWorker(threading.Thread):
    """
    Background worker that calls a provided scan function whenever
    `request()` is signaled. The most recent result + timestamp
    can be retrieved via `get_latest()`.
    """
    def __init__(self, scan_func, full_scan_func=None, workbench_scan_func=None):
        """
        scan_func: callable that takes no args and returns a lightweight scan result.
        full_scan_func: optional callable for full dynamic profile-building scans.
        workbench_scan_func: optional callable that loads the full editable profile cache
            without falling through into a dynamic MEM2 discovery scan.
        """
        super().__init__(daemon=True)
        self._scan_func = scan_func
        self._full_scan_func = full_scan_func
        self._workbench_scan_func = workbench_scan_func
        self._want = threading.Event()
        self._lock = threading.Lock()
        self._last = None
        self._last_ts = 0.0
        self._busy = False
        self._request_count = 0
        self._want_full = False
        self._want_workbench = False
        self._full_kwargs = {}
        self._last_mode = "none"
        # Rich workbench/full results must not vanish just because the next
        # compact HUD refresh finishes before the main loop polls ``_last``.
        # The CSV exporter drains this completed-result queue independently of
        # the UI-facing latest-result slot.
        self._rich_results = []
        self._rich_generation = 0

    def run(self):
        while True:
            self._want.wait()
            self._want.clear()

            # If the module has no scan function, just idle
            if self._scan_func is None:
                continue

            try:
                with self._lock:
                    self._busy = True
                    want_full = bool(self._want_full)
                    want_workbench = bool(self._want_workbench)
                    full_kwargs = dict(self._full_kwargs or {})
                    self._want_full = False
                    self._want_workbench = False
                    self._full_kwargs = {}
                if want_full and self._full_scan_func is not None:
                    func = self._full_scan_func
                    mode = "full"
                    res = func(**full_kwargs) if full_kwargs else func()
                elif want_workbench and self._workbench_scan_func is not None:
                    func = self._workbench_scan_func
                    mode = "workbench"
                    res = func()
                else:
                    func = self._scan_func
                    mode = "cache"
                    res = func()
                now = time.time()
                with self._lock:
                    self._last = res
                    self._last_ts = now
                    self._last_mode = mode
                    # A cache result may immediately follow this one and
                    # replace ``_last``. Preserve every completed rich result
                    # until its observation/export consumer has drained it.
                    if mode in {"workbench", "full"}:
                        self._rich_generation += 1
                        self._rich_results.append((
                            int(self._rich_generation), res, now, mode
                        ))
                        if len(self._rich_results) > 8:
                            del self._rich_results[:-8]
            except Exception as e:
                print("scan worker failed:", e)
            finally:
                with self._lock:
                    self._busy = False

    def request(self, *, force_dynamic: bool = False, workbench: bool = False, **full_scan_kwargs):
        """Signal the worker to perform a scan.

        ``workbench=True`` loads only the full saved editable profile cache.
        It does not run the expensive dynamic discovery scan. ``force_dynamic=True``
        remains the explicit fallback when a fighter has no saved profile.
        Requests coalesce; dynamic work wins over workbench work, which wins over
        the tiny HUD preview scan.
        """
        with self._lock:
            self._request_count += 1
            if force_dynamic:
                self._want_full = True
            elif workbench:
                self._want_workbench = True
            if force_dynamic:
                # A no-argument manual full scan means "all" and must not be
                # narrowed by an earlier targeted request.
                if full_scan_kwargs:
                    if self._full_kwargs:
                        merged = dict(self._full_kwargs)
                        for key, value in full_scan_kwargs.items():
                            if key == "dynamic_char_ids":
                                old = set(merged.get(key) or ())
                                old.update(value or ())
                                merged[key] = tuple(sorted(old))
                            else:
                                merged[key] = value
                        self._full_kwargs = merged
                    else:
                        normalized = dict(full_scan_kwargs)
                        if "dynamic_char_ids" in normalized:
                            normalized["dynamic_char_ids"] = tuple(sorted(set(normalized.get("dynamic_char_ids") or ())))
                        self._full_kwargs = normalized
                else:
                    self._full_kwargs = {}
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

    def drain_completed_rich_results(self):
        """Return and clear completed workbench/full scan results.

        The regular ``get_latest`` API remains intentionally lightweight for
        the HUD.  This separate queue guarantees that a completed rich scan is
        still available to non-UI consumers such as the observation CSV even
        when a later compact cache refresh has already become the latest row.
        Each entry is ``(generation, result, timestamp, mode)``.
        """
        with self._lock:
            completed = list(self._rich_results)
            self._rich_results.clear()
            return completed

    def get_latest(self):
        """
        Return (result, timestamp) of the last completed scan.
        result may be None if no scan has completed yet.
        """
        with self._lock:
            return self._last, self._last_ts
