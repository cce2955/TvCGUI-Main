# Skeletal-Follow Hotfix

The skeletal-follow experiment malformed the `_nearest_opponent_direction` method: its body was accidentally placed inside the legacy-anchor finalizer.  That left the method missing at runtime and allowed the overlay/ruler pass to fail around missing-profile processing.

This hotfix restores the last known-good `hitboxesscaling.py` from the EXE-profile-bootstrap baseline.  It preserves:

- compact preview fast path / background missing-character scan;
- append-on-missing range profile learning;
- profile export/atomic-write support;
- four-slot ruler filters;
- ground-only ruler policy;
- current command dock and preview UI in the packaged source.

It intentionally disables the experimental skeletal attachment and returns the ruler to the known-good root-anchored implementation until skeletal position data can be read from a dedicated, verified transform source rather than the current hurtbox descriptor cache.

No profile JSON content was modified while making this package.
