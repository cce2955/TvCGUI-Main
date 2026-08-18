"""
launcher.py
-----------
Single entry point for the TvCGUI frozen EXE.
"""

import multiprocessing
import sys


def main():
    mode = "main"

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
        import main as hud_main
        hud_main.main()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()