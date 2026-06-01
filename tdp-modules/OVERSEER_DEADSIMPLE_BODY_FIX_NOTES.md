# Overseer dead-simple body fix

The Overseer body was still blank on the user's Windows/Tk setup even after the first body-render guard.

This build replaces the Overseer body with a deliberately plain layout:

- Header and buttons at the top.
- A visible body-status label immediately under the header.
- A direct `tk.Text` dashboard filling the rest of the window.
- Fallback text is inserted before the first runtime-state refresh.
- Runtime refresh errors are printed inside the text area with a traceback.
- The refresh period is 1500 ms to avoid dashboard churn.

No gameplay/runtime hack behavior was changed.
