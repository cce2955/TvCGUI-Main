# anim_offsets.py
#
# Built from the poke list you gave.
# Base address:
BASE_ANIM_ADDR = 0x908AF004  # 5A

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

    # Tatsus (ground)
    "Tatsu L": 0x908B49C4,
    "Tatsu M (first hit)": 0x908B4E7C,
    "Tatsu M (second/third)": 0x908B5198,
    "Tatsu H (first hit)": 0x908B5670,
    "Tatsu H (last three)": 0x908B5994,

    # Shoryu
    "Shoryu L": 0x908B5E48,
    "Shoryu M (second hit)": 0x908B662C,
    "Shoryu M (first hit)": 0x908B6814,
    "Shoryu H (first hit)": 0x908B6EF4,
    "Shoryu H (second hit)": 0x908B7094,

    # Donkey / dash-ish
    "Donkey L": 0x908B76A4,
    "Donkey M": 0x908B7C18,
    "Donkey H": 0x908B8194,

    # Air tatsu block
    "Tatsu L (air)": 0x908B90AD,
    "Tatsu L (air second)": 0x908B933C,
    "Tatsu M (air first)": 0x908B9730,
    "Tatsu M (air 2nd/3rd)": 0x908B99D0,
    "Tatsu H (air first)": 0x908B9D88,
    "Tatsu H (air rest)": 0x908BA0B8,

    # Super
    "Tatsu Super": 0x908BD260,
}

ABS_TO_NAME = {addr: name for name, addr in ABS_MOVES.items()}
OFFSETS = {addr - BASE_ANIM_ADDR: name for name, addr in ABS_MOVES.items()}


def name_for_addr(addr: int) -> str:
    return ABS_TO_NAME.get(addr, f"move_at_{addr:08X}")


def name_for_offset(off: int) -> str:
    return OFFSETS.get(off, f"anim_{off:04X}")


def addr_for_offset(off: int) -> int:
    return BASE_ANIM_ADDR + off


def generate_poke_cmds(bytes_str: str = "43 FF 00 00") -> str:
    lines = []
    for name, addr in ABS_MOVES.items():
        lines.append(f'python tvc_sigtool.py poke --addr 0x{addr:08X} --bytes "{bytes_str}"   # {name}')
    return "\n".join(lines)
