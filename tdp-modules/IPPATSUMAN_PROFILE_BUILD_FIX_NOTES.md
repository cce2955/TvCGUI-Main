Changed:
- main.py
- scan_worker.py

Why:
- The performance pass made auto frame-data refresh cache-only.
- That kept gameplay smooth, but a character with no saved profile, like Ippatsuman, could show an empty Normals Preview forever.
- The attached dump showed profile_cache_miss=true, profile_fast_path=false, tbl_move_count=251, and moves=0. That means the character table was found, but no cached normals profile existed yet.

What changed:
- Auto refresh stays cache-only for already-profiled characters.
- If a loaded slot reports profile_cache_miss, the app now queues one debounced full dynamic profile build in the background.
- The scan worker now supports both cache scans and full dynamic scans.
- Clicking Frame Data on a cache-miss slot also queues a full profile build.
- Empty Normals Preview cards now say Building profile instead of No normals returned when the real issue is a missing cache profile.

Environment:
- TVC_FD_BUILD_MISSING_PROFILES=0 disables the one-time missing-profile builder.
- TVC_FD_MISSING_PROFILE_BUILD_DELAY_SEC controls the delay before building a missing profile.
- TVC_FD_MISSING_PROFILE_BUILD_MIN_INTERVAL_SEC controls the minimum interval between full missing-profile builds.

No assist logic changed.
No Tool State scrollbar/main dock changes were removed.
