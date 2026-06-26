# One-file EXE: missing-profile bootstrap

The build batch was invoking the correct `TvCGUI_onefile.spec`; the blocker was
runtime behavior:

1. `main.py` deliberately kept compact-preview cache misses read-only, so no
   full dynamic normal scan was ever requested for a new fighter.
2. In a one-file build, `frame_data_preview_profiles.json` existed in
   `_MEIPASS`, but the preview loader looked only beside `TvCGUI.exe`. A fresh
   EXE therefore ignored its bundled compact preview seed.
3. A dynamic full scan saved `frame_data_profiles.json` but did not update the
   compact preview snapshot, so later launches could miss again.

This build changes that flow:

- The compact preview loader merges the bundled seed with the writable snapshot
  beside `TvCGUI.exe`.
- A cache miss for the current stable roster queues exactly one background dynamic scan after a 0.75s debounce. The
  worker dynamically scans only the missing character IDs; existing compact
  entries remain cache-only.
- A successful dynamic scan writes both the full workbench profile and a compact
  normal-only preview record. The next launch uses the fast path.
- Range-ruler learning remains separate: once frame data exists, only a missing
  grounded move samples its three hitbox descriptors and writes its own range
  record.

Console markers:

- `[fd profile] auto-build missing preview: ...`
- `[fd preview] saved compact preview for ...`
- `[range profile] learned ...`

Set `TVC_FD_BUILD_MISSING_PROFILES=0` to turn the bootstrap scan off.
