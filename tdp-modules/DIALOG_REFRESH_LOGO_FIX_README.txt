Dialog refresh + logo/titlebar fix

What changed
- Updated the remaining old-style editor dialogs that were still using the older compact/plain layout:
  - Edit Active Frames
  - Edit KB Style
  - Edit Extra Launch
  - Edit Launch Adjust
  - Edit KB X
  - Edit Arc
- Those dialogs now use the same lighter modal style direction as the newer hit-reaction style dialogs:
  - title + help text at top
  - current value/address context
  - suggested values section when applicable
  - manual entry section below
  - OK / Cancel aligned at the bottom
- Improved titlebar icon selection:
  - first tries assets/icon.png (the main app/logo icon)
  - then assets/portraits/Placeholder.png
  - then falls back to app_title_icon.png
- Applied titlebar icon usage to the main Frame Data window and its subdialogs.
- Removed the old hardcoded-feather-only behavior so runtime can use the real assets-folder logo when present.

Notes
- No new scans or Dolphin reads were added.
- This pass is primarily a dialog refresh / titlebar icon pass.
