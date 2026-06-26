# Normal Preview Fast Path

## What changed

- The Dolphin-parented hitbox/ruler overlay no longer calls the full dynamic normal scanner whenever a character ID changes.
- The main HUD's automatic refresh reads a compact normal-only snapshot instead of rebuilding the full workbench profile cache.
- Missing characters remain visibly unprofiled until an explicit Frame Data scan is requested; normal play never queues a dynamic scan.
- The range-ruler profile path is read-only. Live matches cannot replace attack reach, body envelope, or calibration data.

## Snapshot

`frame_data_preview_profiles.json` contains 26 character snapshots and 310 normal rows. It is a display/index file derived from the existing frame-data cache; it is not a live learner.

## Preserved source profiles

- `hitbox_range_profiles.json`: `3d70471815f0170f6745974e793b204989cedcc7d9561de7b013130e08e1b7dd`
- `frame_data_profiles.json`: `f8bdcb06e0ae4d876968e9c93ed4dee5acdd237531c20758765cd26685a02633`
- `animation_frames.json`: `533cff4adb5d966cecf0e334b43a55f1ab905922ec965052d0473053e7042203`

The two profile JSON files above were copied without modification into this build.

## Expected behavior

When a new cached character appears, the normal card and hitbox frame gates should update after the brief 0.35-second character-settle debounce instead of waiting for a full scan. An unprofiled character should say `No saved normal profile`; it should not stall the overlay or rewrite range data.
