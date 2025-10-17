# hud.py
# Static CLI HUD + event log renderer for TvC modules.

import sys
from collections import deque
from typing import List, Optional

from colors import Colors

# ------------------------- Event Queue -------------------------
EVENTS = deque(maxlen=12)


def _trim(s: str, n: int = 120) -> str:
    if s is None:
        return ""
    s = str(s)
    return s if len(s) <= n else s[: n - 3] + "..."


def log_event(msg: str) -> None:
    """Add a message to the rolling event log."""
    if not msg:
        return
    EVENTS.append(_trim(msg))


# ------------------------- HUD helpers -------------------------
def fmt_line(label: str, blk: Optional[dict], meter: Optional[int] = None) -> str:
    """Format a single fighter line (used by main loop)."""
    if not blk:
        return f"{Colors.DIM}{label}[--------] n/a{Colors.RESET}"

    player_color = Colors.P1_BRIGHT if label.startswith("P1") else Colors.P2_BRIGHT
    label_color = Colors.P1_NORMAL if label.startswith("P1") else Colors.P2_NORMAL

    pct = (blk["cur"] / blk["max"]) if blk["max"] else None
    if pct is None:
        hp_color = Colors.DIM
        pct_str = ""
    else:
        if pct > 0.66:
            hp_color = Colors.GREEN
        elif pct > 0.33:
            hp_color = Colors.YELLOW
        else:
            hp_color = Colors.RED
        pct_str = f"{hp_color}({pct * 100:5.1f}%){Colors.RESET}"

    char = f" {player_color}{blk['name']:<16}{Colors.RESET}"
    m = (
        f" | {Colors.PURPLE}M:{meter}{Colors.RESET}"
        if meter is not None
        else f" | {Colors.DIM}M:--{Colors.RESET}"
    )
    x = (
        f" | X:{blk['x']:.3f}"
        if blk.get("x") is not None
        else f" | {Colors.DIM}X:--{Colors.RESET}"
    )
    y = (
        f" Y:{blk['y']:.3f}"
        if blk.get("y") is not None
        else f" {Colors.DIM}Y:--{Colors.RESET}"
    )
    last = blk.get("last")
    dmg_str = (
        f" | lastDmg:{last:5d}"
        if last
        else f" | {Colors.DIM}lastDmg:--{Colors.RESET}"
    )
    hp_display = f"{hp_color}{blk['cur']}/{blk['max']}{Colors.RESET}"

    return (
        f"{label_color}{label}{Colors.RESET}"
        f"[{Colors.DIM}{blk['base']:08X}{Colors.RESET}]"
        f"{char} {hp_display} {pct_str}{m}{x}{y}{dmg_str}"
    )


def render_screen(
    hud_lines: List[str],
    meter_summary: str,
    extra_lines: Optional[List[str]],
) -> None:
    """Clear and redraw static HUD panel + event log."""
    sys.stdout.write("\033[H\033[2J")  # clear screen
    sys.stdout.write(
        Colors.BOLD + "TvC HUD  (static)  |  P1 vs P2  |  C1/C2 status\n" + Colors.RESET
    )

    for ln in hud_lines:
        sys.stdout.write(ln + "\n")

    sys.stdout.write(meter_summary + "\n")

    for ln in (extra_lines or []):
        sys.stdout.write(Colors.DIM + _trim(ln) + Colors.RESET + "\n")

    sys.stdout.write("-" * 100 + "\n")
    sys.stdout.write(Colors.BOLD + "Events (latest first):" + Colors.RESET + "\n")

    for ln in reversed(EVENTS):
        sys.stdout.write(_trim(ln) + "\n")

    sys.stdout.flush()
