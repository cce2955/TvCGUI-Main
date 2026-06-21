Subwindow + titlebar icon pass

What changed
- Removed the [N] / [S] / [U] / [P] / [H] text badges from the Move column.
- All common editor subwindows now apply the bundled feather icon to the title bar instead of falling back to the default Python icon.
- Added the bundled icon file directly into the build: app_title_icon.png
- Reworked the shared picker/editor dialogs to use a cleaner light modal layout inspired by the hit-reaction window:
  - title + help text at top
  - clickable list at top when applicable
  - manual entry section at the bottom
  - OK / Cancel actions along the bottom
- Updated these common editors:
  - Hit Result Flags
  - Integer edit dialogs
  - Float edit dialogs
  - Manual Anim ID dialog
  - Active 2 editor
  - Hit Reaction editor
  - Replace Move dialog now also gets the feather titlebar icon and the same modal setup hook

Notes
- This pass is mainly a dialog/UI pass. No new scans or Dolphin reads were added.
- Child-hit branch markers remain; only the type badges were removed.
