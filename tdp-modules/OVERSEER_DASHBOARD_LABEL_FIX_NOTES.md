# Overseer dashboard label fix

The prior Overseer builds could show the header/status label while the main body
appeared blank. This build removes the Tk Text widget and renders the dashboard
through a plain Label, using the same widget family that was already visible on
Windows.

Changes:
- The body now renders through a Label + StringVar, not a Text widget.
- The window performs one direct refresh immediately after opening.
- The placeholder no longer says it is waiting for runtime state after refresh.
- Refresh failures explain that this is an Overseer wiring/runtime-state issue,
  not Dolphin being unhooked.
- The dashboard text explicitly explains it is reading main.py runtime state.
