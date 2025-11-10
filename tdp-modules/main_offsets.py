# anim_offsets.py
#
# Offsets for the character you poked with tvc_sigtool.py
# All of these were computed from the base address below.

BASE_ANIM_ADDR = 0x908AF004  # 5A

# name -> absolute address
ABS_MOVES = {
    "5A": 0x908AF004,
    "5B": 0x908AF434,
    "5C": 0x908AF848,

    "2A": 0x908AFD28,
    "2B": 0x908B02F8,
    "2C": 0x908B0848,

    "6C": 0x908B102C,
    "3C": 0x908B159C,

    "j.A": 0x908B19F4,
    "j.B": 0x908B1F54,
    "j.B (second hit)": 0x908B21C8,
    "j.C": 0x908B249C,

    "6B (hit 1)": 0x908B2994,
    "6B (hit 2)": 0x908B2C2C,

    # specials
    "Tatsu L": 0x908B49C4,
    "Tatsu M (first hit)": 0x908B4E7C,
    "Tatsu M (second/third)": 0x908B5198,
    "Tatsu H (first hit)": 0x908B5670,
    "Tatsu H (last three)": 0x908B5994,

    # air tatsu
    "Tatsu L (air)": 0x908B90AD,
    "Tatsu L (air second)": 0x908B933C,
    "Tatsu M (air first)": 0x908B9730,
    "Tatsu M (air 2nd/3rd)": 0x908B99D0,
    "Tatsu H (air first)": 0x908B9D88,
    "Tatsu H (air rest)": 0x908BA0B8,

    # shoryu
    "Shoryu L": 0x908B5E48,
    "Shoryu M (second hit)": 0x908B662C,
    "Shoryu M (first hit)": 0x908B6814,
    "Shoryu H (first hit)": 0x908B6EF4,
    "Shoryu H (second hit)": 0x908B7094,

    # donkey / dash
    "Donkey L": 0x908B76A4,
    "Donkey M": 0x908B7C18,
    "Donkey H": 0x908B8194,

    # supers
    "Tatsu Super": 0x908BD260,
    # you had: python tvc_sigtool.py poke --addr 4 --bytes ... ← looks like a typo / relative
    # we will ignore that one because it’s not in this region
}

# derived: absolute -> name
ABS_TO_NAME = {addr: name for name, addr in ABS_MOVES.items()}

# derived: offset -> name
OFFSETS = {addr - BASE_ANIM_ADDR: name for name, addr in ABS_MOVES.items()}


def name_for_addr(addr: int) -> str:
    """Return human name for an absolute address, or a placeholder."""
    return ABS_TO_NAME.get(addr, f"move_at_{addr:08X}")


def name_for_offset(off: int) -> str:
    """Return human name for an offset from BASE_ANIM_ADDR."""
    return OFFSETS.get(off, f"anim_{off:04X}")


def addr_for_offset(off: int) -> int:
    """Return absolute address = BASE + offset."""
    return BASE_ANIM_ADDR + off


def generate_poke_cmds(bytes_str: str = "43 FF 00 00") -> str:
    """
    Utility: spit out the same tvc_sigtool.py poke commands you typed,
    but using this table.
    """
    lines = []
    for name, addr in ABS_MOVES.items():
        lines.append(
            f'python tvc_sigtool.py poke --addr 0x{addr:08X} --bytes "{bytes_str}"   # {name}'
        )
    return "\n".join(lines)
