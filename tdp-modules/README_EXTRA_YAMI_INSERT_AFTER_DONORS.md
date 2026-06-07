# Extra Characters - Yami inserted after donor positions

This build keeps the same Extra Characters button but changes the roster-table strategy.

Instead of adding all extras at the end, it rewrites the character-select roster table in visual order:

- Gold Lightan -> Yami 3 -> Tekkaman Blade
- Zero -> Yami 2 -> Frank West
- Frank West -> Yami 1 -> PTX-40A

The roster count becomes 0x1E (30 entries). This does not touch BRRES, MDL0, chrsel.seq material rows, scratch portrait fields, or resource path strings.

Test clean from a Dolphin restart.
