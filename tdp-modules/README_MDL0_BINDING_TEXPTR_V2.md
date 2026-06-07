# TvC Extra Character Wheel Icons - MDL0 Binding TEX0 V2

This build keeps the previous safe rules:

- no 0x60 carousel row copies
- no thumbnail string edits
- no node/object/cursor/navigation row copies

The previous quicktry only patched the MDL0 material header TEX0 pointer at `material + 0x420`. The dump shows those header fields are real, but the visible bottom wheel did not respond to them.

This version also patches the MDL0 material-dictionary texture binding records that are directly named by the B27/B28/B29 material dictionary entries.

## Dump-derived binding fields

1015 live copy:

- `thumbnail_0622_B27`: binding pointer `0x92D21CA0`, stock `0x92D32FA0`, target `0x92D2EDA0` (`icon_fra`)
- `thumbnail_0622_B28`: binding pointer `0x92D22280`, stock `0x92D331E0`, target `0x92D33660` (`icon_tkb`)
- `thumbnail_0622_B29`: binding pointer `0x92D22860`, stock `0x92D33420`, target `0x92D33D20` (`icon_ya2`)

1016 mirrored live copy:

- `thumbnail_0622_B27`: binding pointer `0x932E5C60`, stock `0x932F70E0`, target `0x932F2EE0` (`icon_fra`)
- `thumbnail_0622_B28`: binding pointer `0x932E6240`, stock `0x932F7320`, target `0x932F77A0` (`icon_tkb`)
- `thumbnail_0622_B29`: binding pointer `0x932E6820`, stock `0x932F7560`, target `0x932F7E60` (`icon_ya2`)

The Extra Characters button applies both layers now:

1. existing material header `+0x420` pointer layer
2. new material-dictionary binding pointer layer

The standalone script `tvc_chrsel_mdl0_texptr_quicktry.py` was updated too, so the BATs audit/apply/restore both layers.
