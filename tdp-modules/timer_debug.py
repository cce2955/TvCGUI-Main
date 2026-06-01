# timer_debug.py
#
# Battle timer debug helpers for TvC Continuo.
#
# Discovered from sequential dumps where visible timer went 99 -> 95:
# - 0x809BDD10 tracks the logic countdown as display - 1.
# - Time_10 and Time_00 are rendered by swapping texture pointer pairs.
#
# This module is intentionally tiny and safe to call from the 60 FPS HUD loop.

from __future__ import annotations

from dolphin_io import rd32, wd32

# ---------------------------------------------------------------------------
# Logic timer/state candidates
# ---------------------------------------------------------------------------

# Visible timer 99 matched raw 98, visible 95 matched raw 94.
TIMER_LOGIC_ADDR = 0x809BDD10

# Neighboring start/max constants observed around the live countdown.
TIMER_START_CONST_ADDRS = (
    0x809BDD0C,
    0x809BDD14,
    0x809BDD18,
)

# ---------------------------------------------------------------------------
# Visible digit texture selector addresses
# ---------------------------------------------------------------------------

# Time_10 = tens digit, Time_00 = ones digit.
TIME10_TEX_A_ADDR = 0x80BE4E80
TIME10_TEX_B_ADDR = 0x80BE4E84
TIME00_TEX_A_ADDR = 0x80BE50A0
TIME00_TEX_B_ADDR = 0x80BE50A4

# Texture pointer pairs for digit images, discovered from Time_00 as it changed
# 9 -> 8 -> 7 -> 6 -> 5.  The table advances by 0x3C0 per digit.
DIGIT_TEX_PAIRS: dict[int, tuple[int, int]] = {
    0: (0x921EEEE0, 0x921EEDE0),
    1: (0x921EF2A0, 0x921EF1A0),
    2: (0x921EF660, 0x921EF560),
    3: (0x921EFA20, 0x921EF920),
    4: (0x921EFDE0, 0x921EFCE0),
    5: (0x921F01A0, 0x921F00A0),
    6: (0x921F0560, 0x921F0460),
    7: (0x921F0920, 0x921F0820),
    8: (0x921F0CE0, 0x921F0BE0),
    9: (0x921F10A0, 0x921F0FA0),
}

DIGIT_BY_TEX_A = {pair[0]: digit for digit, pair in DIGIT_TEX_PAIRS.items()}
DIGIT_BY_PAIR = {pair: digit for digit, pair in DIGIT_TEX_PAIRS.items()}

TIMER_SET_ROWS: dict[str, int] = {
    "TimerSet99": 99,
    "TimerSet60": 60,
    "TimerSet30": 30,
    "TimerSet10": 10,
    "TimerSet00": 0,
}

TIMER_DEBUG_ACTION_ROWS = (
    "TimerFreeze",
    "TimerSet99",
    "TimerSet60",
    "TimerSet30",
    "TimerSet10",
    "TimerSet00",
    "TimerOnes+",
    "TimerTens+",
)


def _clamp_timer_display(value: int) -> int:
    try:
        value = int(value)
    except Exception:
        value = 99
    return max(0, min(99, value))


def display_to_logic_raw(display_value: int) -> int:
    # The observed timer storage is display - 1.  For display 00 there is no
    # clean negative raw value, so clamp raw at zero and still force the digits.
    display_value = _clamp_timer_display(display_value)
    return max(0, display_value - 1)


def logic_raw_to_display(raw_value: int | None) -> int | None:
    if raw_value is None:
        return None
    try:
        return _clamp_timer_display(int(raw_value) + 1)
    except Exception:
        return None


def _rd32_safe(addr: int) -> int | None:
    try:
        value = rd32(addr)
    except Exception:
        return None
    if value is None:
        return None
    try:
        return int(value) & 0xFFFFFFFF
    except Exception:
        return None


def _wd32_safe(addr: int, value: int) -> bool:
    try:
        return bool(wd32(addr, int(value) & 0xFFFFFFFF))
    except Exception:
        return False


def read_timer_raw() -> int | None:
    return _rd32_safe(TIMER_LOGIC_ADDR)


def read_digit_from_pair(addr_a: int, addr_b: int) -> int | None:
    tex_a = _rd32_safe(addr_a)
    tex_b = _rd32_safe(addr_b)
    if tex_a is None:
        return None
    if tex_b is not None:
        exact = DIGIT_BY_PAIR.get((tex_a, tex_b))
        if exact is not None:
            return exact
    return DIGIT_BY_TEX_A.get(tex_a)


def read_timer_display_digits() -> tuple[int | None, int | None]:
    tens = read_digit_from_pair(TIME10_TEX_A_ADDR, TIME10_TEX_B_ADDR)
    ones = read_digit_from_pair(TIME00_TEX_A_ADDR, TIME00_TEX_B_ADDR)
    return tens, ones


def read_timer_display_value() -> int | None:
    tens, ones = read_timer_display_digits()
    if tens is not None and ones is not None:
        return _clamp_timer_display((tens * 10) + ones)

    # Fallback to the logic value if the visible digit texture pair is not
    # currently mapped or the battle HUD is not loaded yet.
    return logic_raw_to_display(read_timer_raw())


def write_digit_pair(addr_a: int, addr_b: int, digit: int) -> bool:
    digit = max(0, min(9, int(digit)))
    tex_a, tex_b = DIGIT_TEX_PAIRS[digit]
    ok_a = _wd32_safe(addr_a, tex_a)
    ok_b = _wd32_safe(addr_b, tex_b)
    return ok_a and ok_b


def write_timer_digits(display_value: int) -> bool:
    display_value = _clamp_timer_display(display_value)
    tens, ones = divmod(display_value, 10)
    ok_tens = write_digit_pair(TIME10_TEX_A_ADDR, TIME10_TEX_B_ADDR, tens)
    ok_ones = write_digit_pair(TIME00_TEX_A_ADDR, TIME00_TEX_B_ADDR, ones)
    return ok_tens and ok_ones


def set_timer_display(display_value: int, *, write_logic: bool = True) -> bool:
    display_value = _clamp_timer_display(display_value)
    ok_digits = write_timer_digits(display_value)
    ok_logic = True
    if write_logic:
        ok_logic = _wd32_safe(TIMER_LOGIC_ADDR, display_to_logic_raw(display_value))
    return ok_digits and ok_logic


def tick_timer_freeze(raw_value: int | None) -> bool:
    if raw_value is None:
        return False
    ok_logic = _wd32_safe(TIMER_LOGIC_ADDR, int(raw_value) & 0xFFFFFFFF)

    display_value = logic_raw_to_display(raw_value)
    ok_digits = True
    if display_value is not None:
        ok_digits = write_timer_digits(display_value)

    return ok_logic and ok_digits


def apply_timer_debug_action(row_name: str) -> int | None:
    """
    Apply one debug row action.

    Returns the resulting visible display value when known.  main.py uses that
    to update the freeze raw when freeze is currently enabled.
    """
    if row_name in TIMER_SET_ROWS:
        display_value = TIMER_SET_ROWS[row_name]
        set_timer_display(display_value, write_logic=True)
        return display_value

    current = read_timer_display_value()
    if current is None:
        current = logic_raw_to_display(read_timer_raw())
    if current is None:
        current = 99

    current = _clamp_timer_display(current)

    if row_name == "TimerOnes+":
        tens, ones = divmod(current, 10)
        display_value = (tens * 10) + ((ones + 1) % 10)
        set_timer_display(display_value, write_logic=True)
        return display_value

    if row_name == "TimerTens+":
        tens, ones = divmod(current, 10)
        display_value = (((tens + 1) % 10) * 10) + ones
        set_timer_display(display_value, write_logic=True)
        return display_value

    return None


def read_timer_debug_values(
    freeze_enabled: bool = False,
    freeze_raw: int | None = None,
) -> list[tuple[str, int | str, int | None]]:
    """
    Rows for debug_panel.draw_debug_overlay.

    The third column is intentionally an int/None so the existing formatter works.
    Command rows use their target value as the displayed value.
    """
    display_value = read_timer_display_value()
    raw_value = read_timer_raw()

    rows: list[tuple[str, int | str, int | None]] = [
        ("TimerDisplay", "Time_10/Time_00", display_value),
        ("TimerLogicRaw", TIMER_LOGIC_ADDR, raw_value),
        ("TimerFreeze", "timer_freeze", 1 if freeze_enabled else 0),
        ("TimerSet99", "timer_set_99", 99),
        ("TimerSet60", "timer_set_60", 60),
        ("TimerSet30", "timer_set_30", 30),
        ("TimerSet10", "timer_set_10", 10),
        ("TimerSet00", "timer_set_00", 0),
        ("TimerOnes+", "timer_ones_plus", display_value),
        ("TimerTens+", "timer_tens_plus", display_value),
    ]

    if freeze_enabled and freeze_raw is not None:
        rows.append(("TimerFreezeRaw", TIMER_LOGIC_ADDR, int(freeze_raw) & 0xFFFFFFFF))

    return rows
