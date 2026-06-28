"""
launcher.py
-----------
Single entry point for the TvCGUI frozen EXE.

When run with no arguments (or --mode main), starts the HUD.
When run with --mode master_overlay, starts the master overlay.
When run with --mode hud_overlay, starts the hud overlay.

This lets PyInstaller build ONE exe that does everything.
"""

import sys


def main():
    mode = "main"

    # Parse --mode <name> from argv
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--mode" and i + 1 < len(args):
            mode = args[i + 1]
            break

    if mode == "master_overlay":
        from tvcgui.features.overlay import master_renderer
        master_renderer.main()

    elif mode == "hud_overlay":
        from tvcgui.features.overlay import hud_renderer
        hud_renderer.main()

    else:
        # Default: run the HUD
        import main as hud_main
        hud_main.main()


if __name__ == "__main__":
    main()
