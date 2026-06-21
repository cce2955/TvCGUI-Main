Dialog autosize fix

What changed
- Fixed clipped OK / Cancel rows in the lighter editor subwindows.
- Light dialogs no longer force a too-small fixed geometry before widgets are laid out.
- Added a shared finalize_dialog_geometry() pass that resizes each dialog after layout based on its real requested size, with a minimum size floor.
- Applied to:
  - integer editor dialogs (e.g. Edit Damage)
  - float editor dialogs
  - hit-result flag dialog
  - active2 editor
  - hit-reaction editor
  - manual anim ID dialog
  - replace move dialog

Why this happened
- Fixed-size modal windows can clip button rows on systems with different DPI scaling or font metrics.
- The new autosize pass sizes the dialog after the content exists, so the buttons stay visible.
