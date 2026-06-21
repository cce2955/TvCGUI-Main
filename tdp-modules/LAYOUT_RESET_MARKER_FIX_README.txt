Layout reset + modified marker fix

What changed
- Edited-row indicator is now visible in the Move column as a leading dot:
  ● Move name
  The old tree-gutter-only dot could be hidden behind expand/collapse arrows on grouped rows.
- The small tree-gutter dot is still retained where space permits.
- Added a top-toolbar Reset layout button.
  - restores the workbench to the exact view state it had when this window opened
  - restores first-load column widths, view, density, sash split, sorting/profile order, and cleared filters
  - does NOT reset any frame-data/patch edits; Reset all still owns that.
- Added Reset layout to the Tools menu too.

Notes
- The startup layout is captured after initial preferences and sash placement have settled.
- No new scans or Dolphin reads were added.
