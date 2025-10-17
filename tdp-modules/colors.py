# colors.py
# ANSI color codes and Windows-safe enablement for TvC HUD.

import os

class Colors:
    # Player 1 - Blue theme
    P1_BRIGHT = '\033[96m'      # Bright cyan
    P1_NORMAL = '\033[94m'      # Blue

    # Player 2 - Red theme
    P2_BRIGHT = '\033[91m'      # Bright red
    P2_NORMAL = '\033[31m'      # Red

    # Status / general
    GREEN  = '\033[92m'         # Good HP
    YELLOW = '\033[93m'         # Medium HP
    RED    = '\033[91m'         # Low HP
    PURPLE = '\033[95m'         # Meter
    BOLD   = '\033[1m'
    UNDERLINE = '\033[4m'
    RESET  = '\033[0m'
    DIM    = '\033[2m'


def enable_windows_ansi():
    """
    Enables ANSI escape codes on Windows terminals.
    Called automatically on import.
    """
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            h = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
            mode = ctypes.c_uint32()
            if kernel32.GetConsoleMode(h, ctypes.byref(mode)):
                kernel32.SetConsoleMode(h, mode.value | 0x0004)
        except Exception:
            pass


# Enable ANSI color support immediately when imported
enable_windows_ansi()
