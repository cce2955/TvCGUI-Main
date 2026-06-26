# Clean Command Dock

## What changed

The default top strip now keeps only live-match visual controls visible:

- Overlay + its three live cards are one outlined group.
- Hitbox and Hurtbox controls are one outlined group.
- Raw slot filters are compact `1 2 | 3 4` chips. The divider is deliberate:
  - Team 1 raw fighters: P1 / P2
  - Team 2 raw fighters: P3 / P4
- Occasional tools are behind `Lab ▾`:
  - Win Score
  - Assist Scanner
  - Dump MEM
  - Extra Characters
  - KO Ctrl
  - Megacrash
  - Tool State

When the Lab drawer is closed, its second row becomes one contextual hover/help line instead of another permanent row of buttons.

## Ruler default

A transition from no active Hitbox slots to one or more active Hitbox slots writes only:

```json
"show_range_ruler": true
```

That is a visibility default, not profiling. It does not touch `hitbox_range_profiles.json` or `frame_data_profiles.json`.

The dock does **not** repeatedly force the ruler back on after that. A manual ruler-off choice in the overlay legend is retained during later unrelated filter writes.

## Guardrails

- Ground normals only; air normals continue to clear the runtime ruler.
- Saved range profiles remain read-only.
- No range sampling, calibration, max-tip update, or body-envelope update was reintroduced.
