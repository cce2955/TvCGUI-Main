# Ruler auto-learn: missing moves only

`hitbox_range_profiles.json` is live training data.

- A valid `character_id:move_id` entry is authoritative: the ruler reads it directly and does not read that move’s live attack descriptors.
- A missing **grounded normal** (`A/B/C`, `2A`, `5B`, `6C`, etc.) starts a tiny per-move collector.
- The collector reads only that fighter’s three attack descriptors during labelled active frames, keeps the farthest valid active sample, then writes one entry at recovery/state exit.
- Air normals remain excluded. Specials/projectiles/supers are not auto-created. Existing saved non-normal profiles are still usable.
- The profile file is written as full schema-5 JSON: `{schema, attacks, bodies}`. It writes to a temporary sibling file, flushes, re-parses that JSON, and only then atomically replaces the prior file.
- The exporter keeps the old file intact on an error, prints `export FAILED`, and retains the change in memory for retry. On success it prints `export OK` with the exact destination and attack/body counts.
- Frozen EXEs use a persistent copy beside `TvCGUI.exe`; PyInstaller’s temporary one-file extraction folder is only the first-run seed source. Existing user profile data is never replaced by the seed during an upgrade.
- `TVC_RANGE_PROFILE_FILE` can override the destination for a portable install or test.
- No full Frame Data/MEM2 normal scan is launched.

Both `TvCGUI.spec` and `TvCGUI_onefile.spec` now package `hitbox_range_profiles.json` plus the normal-preview cache.
