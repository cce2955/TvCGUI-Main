# Adaptive Range Ruler Calibration

The range profile now keeps a small per-move `calibration` block inside
`hitbox_range_profiles.json`.

- A hit that occurs while the ruler predicted `OUT OF RANGE` adds one horizontal
  calibration pip.
- Three separate predictions of `TOUCHING` that do not produce HP damage remove
  one pip.
- One pip is `0.055` world units. The correction is limited to +/-12 pips.
- The HUD label shows `CAL +N` or `CAL -N` whenever a move has a learned offset.

Resolved hits are detected from the defender HP edge while that move's active
window or short post-active grace window is armed. This avoids enabling the
expensive global resolver-contact scan during ordinary play.

## Root-anchor note

Range profiles now use schema 5 and anchor attack reach to the fighter root.
The retained on-screen ruler is reconstructed from the current root after the
move, while hit/whiff calibration during the move uses that action's captured
start root. This keeps movement compensation without rendering a stale start
location.
