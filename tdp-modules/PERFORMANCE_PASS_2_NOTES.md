Performance pass 2

Focus:
- Remove dynamic frame-data scans from automatic HUD refreshes.
- Debounce automatic frame-data scan requests so character/round churn cannot queue repeated scans.
- Keep manual/full frame-data scans available for creating or refreshing profiles.
- Reduce idle quick-assist persistence frequency from every 30 frames to every 60 frames.

Details:
- main.py now constructs ScanNormalsWorker with scan_normals_all.scan_once(cache_only=True).
- scan_normals_all.scan_once(cache_only=True) only uses the profile fast path. If a profile is missing, it returns an empty/cache-miss row instead of falling through into the heavy dynamic scanner.
- Manual scans still call scan_normals_all.scan_once() with cache_only=False.
- Auto scan requests wait for a stable team signature and obey a minimum interval.

Environment knobs:
- TVC_FD_AUTOSCAN=0 disables automatic cached frame-data refreshes.
- TVC_FD_AUTOSCAN_DEBOUNCE_SEC controls how long the team must be stable before auto refresh.
- TVC_FD_AUTOSCAN_MIN_INTERVAL_SEC controls the minimum time between auto refresh requests.
- TVC_PERF_LOG=0 disables perf logs.
