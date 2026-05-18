# Cleanup notes

Behavior target: no scanner, assist, HUD, mission, overlay, or write logic changed.

What changed:

- Moved main-window presentation helpers out of `main.py` into `main_ui.py`.
  - `main.py` keeps the runtime loop, memory reads, mission flow, click handling, and scanner calls.
  - `main_ui.py` owns the drawing helpers, palette constants, normals preview rendering, quick-assist footer rendering, and polished fighter-card rendering.
- Moved assist scanner constants and process-local caches out of `assist_scanner_backend.py` into `assist_scanner_config.py`.
  - The backend still imports the same names, so existing function logic keeps using the same globals.
- Removed the duplicate `import pygame` in `main.py`.
- Removed the now-unused direct `math` import from `main.py`; UI math lives in `main_ui.py`.

Validation performed:

- Ran `python -m compileall .` successfully on the cleaned tree.

Files added:

- `main_ui.py`
- `assist_scanner_config.py`
- `CLEANUP_NOTES.md`
