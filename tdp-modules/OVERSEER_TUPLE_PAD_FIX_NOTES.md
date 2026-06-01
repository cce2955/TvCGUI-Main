# Overseer tuple padding crash fix

The Overseer window was creating the top frames with tuple `pady` values in the `tk.Frame(...)` constructor.

Tk accepts tuple padding in `.pack(...)`, but the frame widget option itself expects a single screen distance. On Windows/Tk this can raise a TclError after the Toplevel is created but before any widgets are packed, leaving a completely blank dark window.

Fixes:
- Move tuple padding from `tk.Frame(..., pady=(...))` into `.pack(..., pady=(...))`.
- Add exception printing to `tk_host.py` so future Tk construction errors are visible in the console instead of silently leaving blank windows.
