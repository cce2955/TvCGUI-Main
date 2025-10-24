# colors.py
# Centralized ANSI color/style helpers (with Windows support).
# Safe to import everywhere.

try:
    # Enables ANSI on Windows terminals if colorama is installed.
    import colorama
    colorama.just_fix_windows_console()
except Exception:
    pass

class Colors:
    # Core styles
    RESET     = '\033[0m'
    BOLD      = '\033[1m'
    DIM       = '\033[2m'
    UNDERLINE = '\033[4m'

    # 8-color base (normal)
    BLACK   = '\033[30m'
    RED     = '\033[31m'
    GREEN   = '\033[32m'
    YELLOW  = '\033[33m'
    BLUE    = '\033[34m'
    MAGENTA = '\033[35m'
    CYAN    = '\033[36m'
    WHITE   = '\033[37m'

    # 8-color bright
    BRIGHT_BLACK   = '\033[90m'   # gray
    BRIGHT_RED     = '\033[91m'
    BRIGHT_GREEN   = '\033[92m'
    BRIGHT_YELLOW  = '\033[93m'
    BRIGHT_BLUE    = '\033[94m'
    BRIGHT_MAGENTA = '\033[95m'
    BRIGHT_CYAN    = '\033[96m'
    BRIGHT_WHITE   = '\033[97m'

    # Aliases used around the HUD code
    PURPLE = BRIGHT_MAGENTA  # matches prior usage

    # Player-themed aliases (kept for compatibility with your prints)
    P1_BRIGHT = BRIGHT_CYAN
    P1_NORMAL = BLUE
    P2_BRIGHT = BRIGHT_RED
    P2_NORMAL = RED