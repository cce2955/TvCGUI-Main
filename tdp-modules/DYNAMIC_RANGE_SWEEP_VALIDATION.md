# Dynamic Range Sweep Validation

## Verified statically

- `main.py`, `master_overlay.py`, `hud_overlay.py`, and `hitboxesscaling.py` compile with `py_compile`.
- The persistent profile exporter serializes the complete `attacks` map, so optional `dynamic_sweep` blocks are included in the atomic JSON export unchanged.
- Existing shipped `hitbox_range_profiles.json`, `frame_data_profiles.json`, and `frame_data_preview_profiles.json` were not modified.
- The command dock owns the only Dynamic switch; the legend remains explanatory only.

## Runtime behavior to validate in Dolphin

1. Turn `Ruler` and `Dynamic` on.
2. Use an existing static-profile normal which lacks `dynamic_sweep`.
3. Console should print one `saved dynamic sweep` line on recovery, followed by `export OK`.
4. Repeat the move: there should be no second capture/export line.
5. The normal static line/tip must remain in place; purple/green ghost circles are additive and represent all recorded active frames.
6. Use a vertical or advancing multi-hit normal and confirm the sweep follows its recorded root path rather than the current root every frame.
