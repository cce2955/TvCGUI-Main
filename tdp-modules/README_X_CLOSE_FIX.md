# X Close Fix

This build keeps the working EXTRA3 Yami-only roster behavior, and fixes main.py not exiting when the window title-bar X is clicked.

Cause:
- main.py had pygame.event.get() disabled by default to avoid a Python 3.13 / pygame 2.6.1 crash path.
- With full event reads disabled, the loop only pumped SDL events and synthesized mouse clicks from mouse state.
- That made normal buttons work, but the window-close event was never consumed.

Fix:
- The safe path now drains only window lifecycle events: QUIT, WINDOWCLOSE, VIDEORESIZE, WINDOWRESIZED, WINDOWSIZECHANGED.
- Mouse clicks still use the existing safe mouse-state fallback.
- The X button and Alt+F4 should now break the main loop and run the normal pygame.quit() shutdown.
