# Extra Characters: 10 appended entries, 3 Yami IDs

This build keeps the proven 10-entry roster-table expansion from the retail-clone test, but changes three of the appended entries into the hidden Yami IDs.

The Extra Characters button writes only the select-wheel roster table/count:

- count: `0x1B -> 0x25`
- slot `0x1B -> 0x17` Yami 1
- slot `0x1C -> 0x18` Yami 2
- slot `0x1D -> 0x19` Yami 3
- slot `0x1E -> 0x01` Ken clone
- slot `0x1F -> 0x08` Jun clone
- slot `0x20 -> 0x02` Casshan clone
- slot `0x21 -> 0x03` Tekkaman clone
- slot `0x22 -> 0x04` Polimar clone
- slot `0x23 -> 0x05` Yatterman-1 clone
- slot `0x24 -> 0x1E` Frank West clone

It intentionally does not apply BRRES, MDL0, chrsel.seq material-index, scratch-string, resource-path, or thumbnail-row edits.

Test flow:

1. Restart Dolphin clean.
2. Open this build.
3. Turn Extra Characters ON.
4. Enter character select.
5. Scroll into the 10 appended entries.

Expected behavior: the wheel still has 10 appended entries; the first three appended entries should select the Yami IDs, and the remaining seven should behave as ordinary retail clones.
