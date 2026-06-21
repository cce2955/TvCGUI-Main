Surface + reset UX pass

What changed
- Impact / Timing / related inspector sections now use richer row surfaces instead of plain form-like rectangles.
- Inspector value chips use flatter display tiles with hover and changed states that match the newer quick-panel tiles.
- Alternating row surfaces inside inspector cards help break up long sections.
- Added a visible toolbar action: Reset all.
  - This clears every changed value in the current patch session back to the original/default cached values.
- Added the same Reset all changed values action to the Tools menu and command palette.
- Tuned the overall blue surfaces so the window feels less sterile:
  - slightly warmer/layered top chrome
  - more contrast between workbench bar, command bar, cards, and body surfaces
  - kept subtle layering instead of a heavy faux gradient

Notes
- No new scans or Dolphin reads were added.
- Undo / Redo still work as before.
- Reset all routes to the existing reset-changed logic; it is just easier to reach now.
