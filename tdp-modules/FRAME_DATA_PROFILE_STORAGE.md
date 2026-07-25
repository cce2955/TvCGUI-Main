# Frame-data profile storage

The complete frame-data cache is stored one character per file:

```text
data/frame_data/frame_data_profiles/
    id_01_ken_the_eagle.json
    id_02_casshan.json
    ...
```

This replaces the legacy `data/frame_data/frame_data_profiles.json` roster file.
The old format eventually exceeded GitHub's 100 MiB per-file limit and rewrote
all fighter data whenever one character changed.

## Runtime behavior

- The scanner loads only the currently requested fighter profile.
- Saving a scan rewrites only that fighter's file.
- Bundled seed profiles and writable runtime profiles use the same precedence as
  before, with writable files overriding bundled files.
- Existing legacy cache files are still readable and are migrated into shards on
  first use. The old source is renamed with `.migrated.bak` after a successful
  migration.

## Manual migration

```bash
python -m tvcgui.tools.split_frame_data_profiles \
  data/frame_data/frame_data_profiles.json \
  --output data/frame_data/frame_data_profiles \
  --delete-source
```

## Git history cleanup

Deleting the legacy file in a new commit is not enough if an unpushed commit
already contains a version above 100 MiB. Rebuild the unpushed branch history
from the remote branch before pushing.
