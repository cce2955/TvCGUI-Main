# Overseer real visibility fix

This pass fixes the Overseer text body being present but visually empty on Windows/Tk.

Changes:

- Keeps the tk.Text widget in normal state instead of disabled state, because disabled text can render invisible under some Windows/Tk themes.
- Blocks keyboard editing while preserving selection/copy.
- Inserts a useful static dashboard guide immediately before any runtime refresh.
- Adds a guaranteed-visible left-aligned action row under the header, because right-packed header buttons can render offscreen when the window opens near the monitor edge.
- Refresh now runs on after_idle and after timer.

No gameplay/runtime behavior changed.
