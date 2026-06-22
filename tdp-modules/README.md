# Frame Data — Unmapped Static Signals Scout

Adds a read-only discovery tool for data the Frame Data workbench does not yet decode.

## What it does

- Scans loaded `chr_tbl` move-entry bodies for structured `04 xx 60` / `04 xx 67` field-operation packets.
- Displays only raw evidence: entry address, packet address, target offset, sub-operation, operand bits, float interpretation when safe, and surrounding bytes.
- Groups identical signatures and ranks them by how many loaded move entries use them (`unique`, `rare`, `shared`, `common`).
- Lets you inspect a selected signature's exact move occurrences and copy them for comparison.
- Scans in a background thread and never writes Dolphin memory.

The tool deliberately does **not** label any packet as invulnerability, armor, hurtbox state, etc. It is meant to expose candidates before we know what they mean.

## UI

Frame Data > **Tools**:

- `Scout selected move’s unmapped signals`
- `Scout profile’s unmapped signals`

A move-row right-click menu also has `Scout unmapped static signals`.

## Files

Replace/add these files in the GUI source folder:

- `fd_unknowns.py` — new scanner and dialog
- `fd_window.py` — opens the scout from selected/all loaded profile rows
- `fd_tree.py` — Tools menu entries

## Smoke test

`python test_fd_unknowns.py` was run against a synthetic `04 15 60 ... +0x5C ... 0x8` packet. It verifies repeated packets are retained and duplicate move entries are not scanned twice.

## Invulnerability placement and timeline

The verified `+0x1218` startup-invulnerability signature is now a first-class timing field:

- **Frame table:** `Invul` sits directly after `HS` in Frame, All, and Super views.
- **Selection strip:** `Invuln` sits directly after `HS`.
- **Right inspector:** `Hitstun → Invuln → Blockstun` are grouped together, with an editable Invuln headline tile.
- **Frame timeline:** a separate teal `INVULN` lane marks frames `1–N` when the exact signature is found. It stays independent from the startup/active lane so overlaps are visible.

For a move with several matching phases, the timeline shows the longest matching phase as the visual envelope and keeps all matching frame values in the summary. The values are never added together.
