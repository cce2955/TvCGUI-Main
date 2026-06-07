# Extra Characters crashguard build

This build disables all writes to the bottom-wheel thumbnail/material layer.

Why: the previous full `0x60` B27/B28/B29 row-copy probe crashed when the cursor moved. That means those rows are live carousel/pane records and cannot be blindly copied from donor rows.

Extra Characters still patches:

- logical appended Yami slots
- selector count
- Yami clone/profile rows
- donor visual/profile append rows

It does **not** currently patch the wheel icon face. The icon may still display as the physical neighbor/fallback until the read-only dump identifies the smaller safe texture/material binding field.

To produce the next useful evidence:

1. Enter character select with Extra Characters ON.
2. Do not run the old BRRES material row patch.
3. Run `run_chrsel_thumbnail_readonly_dump.bat`.
4. Send back the `chrsel_thumbnail_probe_dump` folder.

Restart Dolphin after using the crashed full-row build so the corrupted live carousel rows are definitely gone.
