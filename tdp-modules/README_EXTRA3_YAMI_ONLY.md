Extra Characters button - 3 Yami only
=====================================

This build removes the seven non-Yami appended retail clone entries from the
previous Extra10 experiment.

The Extra Characters button now uses only the proven roster-table/count path:

- stock roster count 0x1B -> 0x1E
- stock 27-character order is preserved, but shifted where needed
- three Yami entries are inserted:
  - after Gold Lightan -> Yami 3
  - after Zero        -> Yami 2
  - after Frank West  -> Yami 1

No extra Ken/Jun/Casshan/Tekkaman/Polimar/Yatterman/Frank retail clone tail is
appended. No BRRES, MDL0, chrsel.seq material, visual scratch, or resource-path
edits are applied by this button.

Clean test:
1. Restart Dolphin.
2. Open this build.
3. Turn Extra Characters ON.
4. Enter character select.
5. Confirm the only new entries are the three Yami insertions.
