Top chrome + inline quick-tile pass

What changed
- Quick strip tiles now use a single-line compact format: Label: Value
  - Example: Damage: 800
  - This saves vertical space in the top quick panel.
- Softened the topmost workbench chrome:
  - flatter/smoother badge chips
  - less harsh outlines on the top bars
  - toolbar buttons and menus use softer surfaces and padding
  - reset button was tuned to match the rest of the top row better
- Overall goal: keep the blue theme, but reduce the hard square/form feel in the top rows.

Notes
- No behavior changes or new scans were added.
- This is a surface/layout pass on top of the previous reset/surface build.
