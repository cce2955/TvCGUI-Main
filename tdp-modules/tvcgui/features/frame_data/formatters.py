# fd_format.py
#
# Formatting + parsing helpers and curated maps.

from __future__ import annotations

KB_TRAJ_MAP = {
    0xBD: "Up Forward KB",
    0xBE: "Down Forward KB",
    0xBC: "Up KB (Spiral)",
    0xC4: "Up Pop (j.L/j.M)",
}

HIT_REACTION_MAP = {
    0x000000: "Stay on ground",
    0x000001: "Ground/Air > KB",
    0x000002: "Ground/Air > KD",
    0x000003: "Ground/Air > Spiral KD",
    0x000004: "Sweep",
    0x000008: "Stagger",
    0x000010: "Ground > Stay Ground, Air > KB",
    0x000040: "Ground > Stay Ground, Air > KB, OTG > Stay OTG",
    0x000041: "Ground/Air > KB, OTG > Stay OTG",
    0x000042: "Ground/Air > KD, OTG > Stay OTG",
    0x000080: "Ground > Stay Ground, Air > KB",
    0x000082: "Ground/Air > KD",
    0x000083: "Ground/Air > Spiral KD",
    0x000400: "Launcher",
    0x000800: "Ground > Stay Ground, Air > Soft KD",
    0x000848: "Ground > Stagger, Air > Soft KD",
    0x002010: "Ground > Stay Ground, Air > KB",
    0x003010: "Ground > Stay Ground, Air > KB",
    0x004200: "Ground/Air > KD",
    0x800080: "Ground > Crumple, Air > KB",
    0x800002: "Ground/Air > KD, Wall > Wallbounce",
    0x800008: "Alex Flash Chop",
    0x800020: "Snap Back",
    0x800082: "Ground/Air > KD, Wall > Wallbounce",
    0x001001: "Wonky: Friender/Zombies grab if KD near ground",
    0x001003: "Wonky variant",
}


def fmt_kb_traj(val):
    if val is None:
        return ""
    desc = KB_TRAJ_MAP.get(val, "Unknown")
    return f"0x{val:02X} ({desc})"


def fmt_hit_reaction(val):
    if val is None:
        return ""
    desc = HIT_REACTION_MAP.get(val, "Unknown")
    return f"0x{val:06X} ({desc})"


def parse_hit_reaction_input(s: str):
    s = s.strip()
    if not s:
        return None
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s, 16)
    except ValueError:
        pass
    try:
        return int(s, 10)
    except ValueError:
        return None


def fmt_stun(v):
    if v is None:
        return ""
    if v == 0x0C:
        return "10"
    if v == 0x0F:
        return "15"
    if v == 0x11:
        return "17"
    if v == 0x15:
        return "21"
    return str(v)


def unfmt_stun(s):
    s = s.strip()
    if not s:
        return None
    try:
        val = int(s)
    except ValueError:
        return None
    if val == 10:
        return 0x0C
    if val == 15:
        return 0x0F
    if val == 17:
        return 0x11
    if val == 21:
        return 0x15
    return val
