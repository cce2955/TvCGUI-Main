from __future__ import annotations

import tkinter as tk
from typing import Any, Callable

try:
    from tk_host import tk_call
except Exception:  # pragma: no cover
    tk_call = None

try:
    from dolphin_io import rd8, rd32, wd8, wd32
except Exception:  # pragma: no cover
    rd8 = None
    rd32 = None
    wd8 = None
    wd32 = None

_OPEN_WINDOW: tk.Toplevel | None = None

_BG = "#0d1018"
_PANEL = "#171b27"
_PANEL_2 = "#202638"
_BORDER = "#31384d"
_TEXT = "#e9edf7"
_MUTED = "#aeb8cc"
_ACCENT = "#89a7ff"
_ACTIVE = "#2a3553"
_FIELD = "#0f1320"
_BAD = "#ff8a8a"
_GOOD = "#95e6c8"

# Shared arcade HUD score/stage/win digit texture table.
# Values are written as big-endian u32 pointers into the loaded texture bank.
SCORE_DIGIT_TEXTURES: dict[int, tuple[int, int]] = {
    0: (0x921D13C0, 0x921D1300),
    1: (0x921D1660, 0x921D15C0),
    2: (0x921D1920, 0x921D1860),
    3: (0x921D1BE0, 0x921D1B20),
    4: (0x921D1EA0, 0x921D1DE0),
    5: (0x921D2160, 0x921D20A0),
    6: (0x921D2420, 0x921D2360),
    7: (0x921D26E0, 0x921D2620),
    8: (0x921D29A0, 0x921D28E0),
    9: (0x921D2C60, 0x921D2BA0),
}

# Timer uses its own Time_0.tpl through Time_9.tpl texture bank.
TIMER_DIGIT_TEXTURES: dict[int, tuple[int, int]] = {
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

_SCORE_TEXTURE_TO_DIGIT = {pair: digit for digit, pair in SCORE_DIGIT_TEXTURES.items()}
_TIMER_TEXTURE_TO_DIGIT = {pair: digit for digit, pair in TIMER_DIGIT_TEXTURES.items()}

# Digit positions are ones, tens, hundreds.
# VS = active in-fight top HUD copy (vs_1_* / vs_2_*).
# HUD = result/arcade win_* copy.
# SVM = SVM-prefixed result copy.
WIN_DIGIT_BANKS: dict[str, dict[str, tuple[tuple[int, int], tuple[int, int], tuple[int, int]]]] = {
    "P1": {
        "VS": (
            (0x80BEBB80, 0x80BEBB84),
            (0x80BEBDA0, 0x80BEBDA4),
            (0x80BEBFC0, 0x80BEBFC4),
        ),
        "HUD": (
            (0x80BE9BC0, 0x80BE9BC4),
            (0x80BE9DE0, 0x80BE9DE4),
            (0x80BEA000, 0x80BEA004),
        ),
        "SVM": (
            (0x80BF1000, 0x80BF1004),
            (0x80BF1220, 0x80BF1224),
            (0x80BF1440, 0x80BF1444),
        ),
    },
    "P2": {
        "VS": (
            (0x80BEC940, 0x80BEC944),
            (0x80BECB60, 0x80BECB64),
            (0x80BECD80, 0x80BECD84),
        ),
        "HUD": (
            (0x80BEADC0, 0x80BEADC4),
            (0x80BEAFE0, 0x80BEAFE4),
            (0x80BEB200, 0x80BEB204),
        ),
        "SVM": (
            (0x80BF1DC0, 0x80BF1DC4),
            (0x80BF1FE0, 0x80BF1FE4),
            (0x80BF2200, 0x80BF2204),
        ),
    },
}

# Pane enable byte directly before each pane name. The active VS HUD disables
# the tens/hundreds pane when the real count is below 10/100, so texture-only
# writes made values like 21 appear as only 1. These flags fix that.

# VS HUD mode parent toggles. When a side is at 0/new hero, the game disables
# the win group and enables NewHero_* instead. Texture writes still land, but
# they are hidden until the parent mode is switched back to win_vs*.
VS_WIN_MODE_ENABLES: dict[str, dict[str, int]] = {
    "P1": {"WIN": 0x80BEB85B, "NEWHERO": 0x80BEB63B},
    "P2": {"WIN": 0x80BEC61B, "NEWHERO": 0x80BEC3FB},
}

# Upstream arcade/versus win counters. These are not the HUD, but they are
# useful for auto-incrementing a persistent visual streak after the game resets
# one side to NEW HERO. Use max mirror value for stability.
RAW_WIN_COUNTERS: dict[str, tuple[int, ...]] = {
    "P1": (0x803EB9FC, 0x803EBA08),
    "P2": (0x803EBA00, 0x803EBA04),
}

WIN_PANE_ENABLES: dict[str, dict[str, tuple[int, int, int]]] = {
    "P1": {
        "VS": (0x80BEBA7B, 0x80BEBC9B, 0x80BEBEBB),
        "HUD": (0x80BE9ABB, 0x80BE9CDB, 0x80BE9EFB),
        "SVM": (0x80BF0EFF, 0x80BF111F, 0x80BF133F),
    },
    "P2": {
        "VS": (0x80BEC83B, 0x80BECA5B, 0x80BECC7B),
        "HUD": (0x80BEACBB, 0x80BEAEDB, 0x80BEB0FB),
        "SVM": (0x80BF1CBF, 0x80BF1EDF, 0x80BF20FF),
    },
}

# Arcade score display panes. Order is right-to-left so index 0 is the rightmost
# visible digit. The two special suffix panes are the fixed trailing zeroes.
SCORE_DIGIT_BANKS: dict[str, tuple[tuple[int, int], ...]] = {
    "P1": (
        (0x80BE53C0, 0x80BE53C4),  # 1_00
        (0x80BE55E0, 0x80BE55E4),  # 1_01
        (0x80BE5800, 0x80BE5804),  # 1_1
        (0x80BE5A20, 0x80BE5A24),  # 1_10
        (0x80BE5C40, 0x80BE5C44),  # 1_100
        (0x80BE5E60, 0x80BE5E64),  # 1_1000
        (0x80BE6080, 0x80BE6084),  # 1_10000
        (0x80BE62A0, 0x80BE62A4),  # 1_100000
        (0x80BE64C0, 0x80BE64C4),  # 1_1000000
        (0x80BE66E0, 0x80BE66E4),  # 1_10000000
        (0x80BE6900, 0x80BE6904),  # 1_100000000
        (0x80BE6B20, 0x80BE6B24),  # 1_1000000000
        (0x80BE6D40, 0x80BE6D44),  # 1_10000000000
        (0x80BE6F60, 0x80BE6F64),  # 1_100000000000
    ),
    "P2": (
        (0x80BE7280, 0x80BE7284),  # 2_00
        (0x80BE74A0, 0x80BE74A4),  # 2_01
        (0x80BE76C0, 0x80BE76C4),  # 2_1
        (0x80BE78E0, 0x80BE78E4),  # 2_10
        (0x80BE7B00, 0x80BE7B04),  # 2_100
        (0x80BE7D20, 0x80BE7D24),  # 2_1000
        (0x80BE7F40, 0x80BE7F44),  # 2_10000
        (0x80BE8160, 0x80BE8164),  # 2_100000
        (0x80BE8380, 0x80BE8384),  # 2_1000000
        (0x80BE85A0, 0x80BE85A4),  # 2_10000000
        (0x80BE87C0, 0x80BE87C4),  # 2_100000000
        (0x80BE89E0, 0x80BE89E4),  # 2_1000000000
        (0x80BE8C00, 0x80BE8C04),  # 2_10000000000
        (0x80BE8E20, 0x80BE8E24),  # 2_100000000000
    ),
}

STAGE_DIGIT_PAIR = (0x80BE9780, 0x80BE9784)
TIMER_TENS_PAIR = (0x80BE4E80, 0x80BE4E84)
TIMER_ONES_PAIR = (0x80BE50A0, 0x80BE50A4)
TIMER_RAW_ADDR = 0x809BDD10
ARCADE_SCORE_RAW_ADDR = 0x803EB904


def _clamp_int(value: Any, lo: int, hi: int, default: int = 0) -> int:
    try:
        n = int(round(float(str(value).strip())))
    except Exception:
        n = int(default)
    return max(int(lo), min(int(hi), n))


def _clean_digits(value: Any, max_len: int) -> str:
    text = str(value or "").strip()
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        digits = "0"
    return digits[-max_len:]


def _count_digits(value: Any) -> tuple[int, int, int]:
    n = _clamp_int(value, 0, 999)
    return (n % 10, (n // 10) % 10, (n // 100) % 10)


def _selected_banks(use_vs: bool, use_hud: bool, use_svm: bool) -> tuple[str, ...]:
    out: list[str] = []
    if use_vs:
        out.append("VS")
    if use_hud:
        out.append("HUD")
    if use_svm:
        out.append("SVM")
    if not out:
        out.append("VS")
    return tuple(out)


def _write_u8(addr: int, value: int) -> bool:
    if wd8 is None:
        return False
    try:
        return bool(wd8(int(addr), int(value) & 0xFF))
    except Exception:
        return False




def _read_u32(addr: int) -> int | None:
    if rd32 is None:
        return None
    try:
        return int(rd32(int(addr)))
    except Exception:
        return None


def read_raw_win_count(player: str) -> int | None:
    player = str(player or "P1").upper()
    vals: list[int] = []
    for addr in RAW_WIN_COUNTERS.get(player, ()):
        value = _read_u32(addr)
        if value is None:
            continue
        if 0 <= value <= 999:
            vals.append(int(value))
    if not vals:
        return None
    return max(vals)


def _set_vs_win_mode(player: str, *, show_win: bool) -> None:
    player = str(player or "P1").upper()
    addrs = VS_WIN_MODE_ENABLES.get(player)
    if not addrs:
        return
    # Only the parent mode decides whether win digits or NEW HERO are visible.
    # The child digit panes can be enabled and still remain hidden under NewHero.
    _write_u8(addrs["WIN"], 1 if show_win else 0)
    _write_u8(addrs["NEWHERO"], 0 if show_win else 1)
def _write_pair(addr_a: int, addr_b: int, digit: int, table: dict[int, tuple[int, int]] = SCORE_DIGIT_TEXTURES) -> bool:
    if wd32 is None:
        return False
    a, b = table[int(digit)]
    try:
        ok_a = bool(wd32(int(addr_a), int(a)))
        ok_b = bool(wd32(int(addr_b), int(b)))
        return bool(ok_a and ok_b)
    except Exception:
        return False


def _read_digit_pair(addr_a: int, addr_b: int, texture_to_digit: dict[tuple[int, int], int]) -> int | None:
    if rd32 is None:
        return None
    try:
        pair = (int(rd32(int(addr_a))), int(rd32(int(addr_b))))
    except Exception:
        return None
    return texture_to_digit.get(pair)


def _set_win_pane_enables(player: str, bank_name: str, value: int) -> None:
    if str(bank_name or "").upper() == "VS":
        # Force the side out of NEW HERO mode and into the win display group.
        _set_vs_win_mode(player, show_win=True)

    flags = WIN_PANE_ENABLES.get(player, {}).get(bank_name)
    if not flags:
        return
    desired = (1, 1 if value >= 10 else 0, 1 if value >= 100 else 0)
    for addr, enabled in zip(flags, desired):
        _write_u8(addr, enabled)


def apply_win_count(player: str, value: Any, *, use_vs: bool = True, use_hud: bool = True, use_svm: bool = False) -> bool:
    player = str(player or "P1").upper()
    if player not in WIN_DIGIT_BANKS:
        player = "P1"

    value_i = _clamp_int(value, 0, 999)
    digits = _count_digits(value_i)
    ok_any = False
    for bank_name in _selected_banks(use_vs, use_hud, use_svm):
        _set_win_pane_enables(player, bank_name, value_i)
        for idx, (addr_a, addr_b) in enumerate(WIN_DIGIT_BANKS[player].get(bank_name, ())) :
            if _write_pair(addr_a, addr_b, digits[idx], SCORE_DIGIT_TEXTURES):
                ok_any = True
    return ok_any


def read_win_count(player: str, *, bank_name: str = "VS") -> int | None:
    player = str(player or "P1").upper()
    bank_name = str(bank_name or "VS").upper()
    bank = WIN_DIGIT_BANKS.get(player, {}).get(bank_name)
    if not bank:
        return None

    digits: list[int] = []
    for addr_a, addr_b in bank:
        digit = _read_digit_pair(addr_a, addr_b, _SCORE_TEXTURE_TO_DIGIT)
        if digit is None:
            return None
        digits.append(int(digit))

    return digits[0] + digits[1] * 10 + digits[2] * 100


def apply_score_display(player: str, score_text: Any) -> bool:
    player = str(player or "P1").upper()
    bank = SCORE_DIGIT_BANKS.get(player)
    if not bank:
        bank = SCORE_DIGIT_BANKS["P1"]
    digits = _clean_digits(score_text, len(bank))
    # Right-align into all available panes. Extra leading panes become zero.
    digits = digits.rjust(len(bank), "0")
    ok_any = False
    for (addr_a, addr_b), ch in zip(bank, reversed(digits)):
        if _write_pair(addr_a, addr_b, int(ch), SCORE_DIGIT_TEXTURES):
            ok_any = True
    return ok_any


def read_score_display(player: str) -> str | None:
    player = str(player or "P1").upper()
    bank = SCORE_DIGIT_BANKS.get(player)
    if not bank:
        return None
    digits: list[str] = []
    for addr_a, addr_b in reversed(bank):
        digit = _read_digit_pair(addr_a, addr_b, _SCORE_TEXTURE_TO_DIGIT)
        if digit is None:
            return None
        digits.append(str(digit))
    return "".join(digits).lstrip("0") or "0"


def apply_stage_display(value: Any) -> bool:
    digit = _clamp_int(value, 0, 9)
    return _write_pair(STAGE_DIGIT_PAIR[0], STAGE_DIGIT_PAIR[1], digit, SCORE_DIGIT_TEXTURES)


def read_stage_display() -> int | None:
    return _read_digit_pair(STAGE_DIGIT_PAIR[0], STAGE_DIGIT_PAIR[1], _SCORE_TEXTURE_TO_DIGIT)


def apply_timer_display(value: Any, *, write_raw: bool = False) -> bool:
    value_i = _clamp_int(value, 0, 99)
    tens = (value_i // 10) % 10
    ones = value_i % 10
    ok = False
    ok = _write_pair(TIMER_TENS_PAIR[0], TIMER_TENS_PAIR[1], tens, TIMER_DIGIT_TEXTURES) or ok
    ok = _write_pair(TIMER_ONES_PAIR[0], TIMER_ONES_PAIR[1], ones, TIMER_DIGIT_TEXTURES) or ok
    if write_raw and wd32 is not None:
        try:
            # Observed raw timer value is display - 1.
            wd32(TIMER_RAW_ADDR, max(0, value_i - 1))
        except Exception:
            pass
    return ok


def read_timer_display() -> int | None:
    tens = _read_digit_pair(TIMER_TENS_PAIR[0], TIMER_TENS_PAIR[1], _TIMER_TEXTURE_TO_DIGIT)
    ones = _read_digit_pair(TIMER_ONES_PAIR[0], TIMER_ONES_PAIR[1], _TIMER_TEXTURE_TO_DIGIT)
    if tens is None or ones is None:
        return None
    return tens * 10 + ones


def write_arcade_score_raw_from_display(score_text: Any) -> bool:
    if wd32 is None:
        return False
    digits = _clean_digits(score_text, 14)
    try:
        visible_value = int(digits)
        raw_value = max(0, min(0xFFFFFFFF, visible_value // 100))
        return bool(wd32(ARCADE_SCORE_RAW_ADDR, raw_value))
    except Exception:
        return False


def _label(
    parent: tk.Misc,
    text: str,
    *,
    muted: bool = False,
    bold: bool = False,
    size: int = 9,
    wraplength: int | None = None,
) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        bg=parent.cget("bg"),
        fg=_MUTED if muted else _TEXT,
        font=("Segoe UI", size, "bold" if bold else "normal"),
        anchor="w",
        justify="left",
        wraplength=wraplength or 0,
    )


def _button(parent: tk.Misc, text: str, command: Callable[[], None] | None = None, *, width: int | None = None) -> tk.Button:
    return tk.Button(
        parent,
        text=text,
        command=command,
        width=width or 0,
        bg=_PANEL_2,
        fg=_TEXT,
        activebackground=_ACTIVE,
        activeforeground=_TEXT,
        relief="flat",
        bd=0,
        padx=9,
        pady=4,
        font=("Segoe UI", 9),
        highlightthickness=1,
        highlightbackground=_BORDER,
        highlightcolor=_ACCENT,
    )


def _field(parent: tk.Misc, textvariable: tk.Variable, *, width: int = 8, max_value: int = 999):
    return tk.Spinbox(
        parent,
        textvariable=textvariable,
        from_=0,
        to=max_value,
        increment=1,
        width=width,
        bg=_FIELD,
        fg=_TEXT,
        insertbackground=_TEXT,
        relief="flat",
        bd=1,
        highlightthickness=1,
        highlightbackground=_BORDER,
        highlightcolor=_ACCENT,
        buttonbackground=_PANEL_2,
        font=("Segoe UI", 9),
    )


def _entry(parent: tk.Misc, textvariable: tk.Variable, *, width: int = 18):
    return tk.Entry(
        parent,
        textvariable=textvariable,
        width=width,
        bg=_FIELD,
        fg=_TEXT,
        insertbackground=_TEXT,
        relief="flat",
        bd=1,
        highlightthickness=1,
        highlightbackground=_BORDER,
        highlightcolor=_ACCENT,
        font=("Segoe UI", 9),
    )


def _check(parent: tk.Misc, text: str, var: tk.BooleanVar) -> tk.Checkbutton:
    return tk.Checkbutton(
        parent,
        text=text,
        variable=var,
        bg=parent.cget("bg"),
        fg=_TEXT,
        activebackground=parent.cget("bg"),
        activeforeground=_TEXT,
        selectcolor=_FIELD,
        font=("Segoe UI", 9),
        relief="flat",
        bd=0,
    )


def _card(parent: tk.Misc) -> tk.Frame:
    outer = tk.Frame(parent, bg=_BORDER, padx=1, pady=1)
    inner = tk.Frame(outer, bg=_PANEL, padx=12, pady=10)
    inner.pack(fill="both", expand=True)
    outer.inner = inner  # type: ignore[attr-defined]
    return outer


def open_hud_editor_window() -> None:
    """Open the live HUD texture override window."""

    def _show(root: tk.Tk) -> None:
        global _OPEN_WINDOW

        try:
            if _OPEN_WINDOW is not None and bool(_OPEN_WINDOW.winfo_exists()):
                _OPEN_WINDOW.lift()
                _OPEN_WINDOW.focus_force()
                return
        except Exception:
            _OPEN_WINDOW = None

        win = tk.Toplevel(root)
        _OPEN_WINDOW = win
        win.title("HUD Editor")
        win.geometry("1080x820")
        win.minsize(1040, 720)
        win.configure(bg=_BG)

        try:
            win.attributes("-topmost", True)
            win.after(300, lambda: win.attributes("-topmost", False))
        except Exception:
            pass

        p1_win_var = tk.StringVar(value="0")
        p2_win_var = tk.StringVar(value="0")
        p1_score_var = tk.StringVar(value="0")
        p2_score_var = tk.StringVar(value="0")
        stage_var = tk.StringVar(value="1")
        timer_var = tk.StringVar(value="99")
        p1_streak_var = tk.StringVar(value="0")
        p2_streak_var = tk.StringVar(value="0")

        vs_var = tk.BooleanVar(value=True)
        hud_var = tk.BooleanVar(value=True)
        svm_var = tk.BooleanVar(value=False)
        freeze_var = tk.BooleanVar(value=False)
        score_raw_var = tk.BooleanVar(value=False)
        timer_raw_var = tk.BooleanVar(value=False)
        p1_streak_enabled_var = tk.BooleanVar(value=False)
        p2_streak_enabled_var = tk.BooleanVar(value=False)
        p1_streak_auto_var = tk.BooleanVar(value=True)
        p2_streak_auto_var = tk.BooleanVar(value=True)
        status_var = tk.StringVar(value="Ready. Use Hold Visible Wins to keep a side's wins on-screen through NEW HERO resets.")
        readout_var = tk.StringVar(value="Current: --")

        last_applied = {
            "P1_WIN": 0,
            "P2_WIN": 0,
            "P1_SCORE": "0",
            "P2_SCORE": "0",
            "STAGE": 1,
            "TIMER": 99,
        }
        freeze_kinds: set[str] = set()
        streak_last_raw = {
            "P1": read_raw_win_count("P1"),
            "P2": read_raw_win_count("P2"),
        }

        root_frame = tk.Frame(win, bg=_BG, padx=16, pady=14)
        root_frame.pack(fill="both", expand=True)

        header = tk.Frame(root_frame, bg=_BG)
        header.pack(fill="x")
        _label(header, "HUD Editor", bold=True, size=14).pack(side="left")
        _check(header, "Freeze writes", freeze_var).pack(side="right")

        desc = _label(
            root_frame,
            "Edits live HUD texture selectors. Hold Visible Wins preserves a side's displayed wins through NEW HERO resets; Win Count is for direct manual HUD edits.",
            muted=True,
            wraplength=980,
        )
        desc.pack(fill="x", pady=(4, 10))

        opts = tk.Frame(root_frame, bg=_BG)
        opts.pack(fill="x", pady=(0, 10))
        _check(opts, "Write VS/in-fight wins", vs_var).pack(side="left")
        _check(opts, "Write result HUD wins", hud_var).pack(side="left", padx=(18, 0))
        _check(opts, "Write SVM wins too", svm_var).pack(side="left", padx=(18, 0))
        _check(opts, "Raw score too", score_raw_var).pack(side="left", padx=(26, 0))
        _check(opts, "Raw timer too", timer_raw_var).pack(side="left", padx=(18, 0))

        def update_readout() -> None:
            p1_vs = read_win_count("P1", bank_name="VS")
            p2_vs = read_win_count("P2", bank_name="VS")
            p1_hud = read_win_count("P1", bank_name="HUD")
            p2_hud = read_win_count("P2", bank_name="HUD")
            p1_score = read_score_display("P1")
            p2_score = read_score_display("P2")
            stage = read_stage_display()
            timer = read_timer_display()
            raw_p1 = read_raw_win_count("P1")
            raw_p2 = read_raw_win_count("P2")
            readout_var.set(
                f"VS wins P1 {p1_vs if p1_vs is not None else '--'} | P2 {p2_vs if p2_vs is not None else '--'}    "
                f"Raw P1 {raw_p1 if raw_p1 is not None else '--'} | P2 {raw_p2 if raw_p2 is not None else '--'}    "
                f"Result P1 {p1_hud if p1_hud is not None else '--'} | P2 {p2_hud if p2_hud is not None else '--'}    "
                f"Score P1 {p1_score or '--'} | P2 {p2_score or '--'}    "
                f"Stage {stage if stage is not None else '--'}    Timer {timer if timer is not None else '--'}"
            )

        def write_win(player: str, value: Any, *, announce: bool = True) -> None:
            value_i = _clamp_int(value, 0, 999)
            ok = apply_win_count(player, value_i, use_vs=bool(vs_var.get()), use_hud=bool(hud_var.get()), use_svm=bool(svm_var.get()))
            last_applied[f"{player}_WIN"] = value_i
            freeze_kinds.add(f"{player}_WIN")
            if announce:
                status_var.set(f"{player} wins set to {value_i}" if ok else f"{player} win write failed; confirm Dolphin is hooked")
                update_readout()

        def write_score(player: str, value: Any, *, announce: bool = True) -> None:
            value_s = _clean_digits(value, len(SCORE_DIGIT_BANKS.get(player, SCORE_DIGIT_BANKS["P1"])))
            ok = apply_score_display(player, value_s)
            if bool(score_raw_var.get()):
                ok = write_arcade_score_raw_from_display(value_s) or ok
            last_applied[f"{player}_SCORE"] = value_s
            freeze_kinds.add(f"{player}_SCORE")
            if announce:
                status_var.set(f"{player} score display set to {value_s}" if ok else f"{player} score write failed; confirm Dolphin is hooked")
                update_readout()

        def write_stage(value: Any, *, announce: bool = True) -> None:
            value_i = _clamp_int(value, 0, 9)
            ok = apply_stage_display(value_i)
            last_applied["STAGE"] = value_i
            freeze_kinds.add("STAGE")
            if announce:
                status_var.set(f"Stage digit set to {value_i}" if ok else "Stage write failed; confirm Dolphin is hooked")
                update_readout()

        def write_timer(value: Any, *, announce: bool = True) -> None:
            value_i = _clamp_int(value, 0, 99)
            ok = apply_timer_display(value_i, write_raw=bool(timer_raw_var.get()))
            last_applied["TIMER"] = value_i
            freeze_kinds.add("TIMER")
            if announce:
                status_var.set(f"Timer display set to {value_i:02d}" if ok else "Timer write failed; confirm Dolphin is hooked")
                update_readout()


        def _streak_controls_for(player: str):
            player = str(player or "P1").upper()
            if player == "P2":
                return p2_streak_var, p2_streak_enabled_var, p2_streak_auto_var
            return p1_streak_var, p1_streak_enabled_var, p1_streak_auto_var

        def capture_streak_from_vs(player: str) -> None:
            player = str(player or "P1").upper()
            var, _enabled_var, _auto_var = _streak_controls_for(player)
            current = read_win_count(player, bank_name="VS")
            if current is None:
                current = read_raw_win_count(player)
            if current is None:
                current = _clamp_int(var.get(), 0, 999)
            var.set(str(_clamp_int(current, 0, 999)))
            last_applied[f"{player}_WIN"] = _clamp_int(var.get(), 0, 999)
            streak_last_raw[player] = read_raw_win_count(player)
            status_var.set(f"Captured {player} visible wins as {var.get()}.")
            update_readout()

        def enable_streak(player: str) -> None:
            player = str(player or "P1").upper()
            var, enabled_var, _auto_var = _streak_controls_for(player)
            value_i = _clamp_int(var.get(), 0, 999)
            var.set(str(value_i))
            last_applied[f"{player}_WIN"] = value_i
            freeze_kinds.add(f"{player}_WIN")
            enabled_var.set(True)
            streak_last_raw[player] = read_raw_win_count(player)
            apply_win_count(player, value_i, use_vs=True, use_hud=bool(hud_var.get()), use_svm=bool(svm_var.get()))
            status_var.set(f"{player} visible wins held at {value_i}. It will ignore NEW HERO resets and add future raw {player} increments.")
            update_readout()

        def bump_streak(player: str, delta: int) -> None:
            player = str(player or "P1").upper()
            var, _enabled_var, _auto_var = _streak_controls_for(player)
            value_i = _clamp_int(var.get(), 0, 999)
            value_i = max(0, min(999, value_i + int(delta)))
            var.set(str(value_i))
            last_applied[f"{player}_WIN"] = value_i
            freeze_kinds.add(f"{player}_WIN")
            apply_win_count(player, value_i, use_vs=True, use_hud=bool(hud_var.get()), use_svm=bool(svm_var.get()))
            status_var.set(f"{player} held wins adjusted to {value_i}.")
            update_readout()

        def disable_streak(player: str) -> None:
            player = str(player or "P1").upper()
            _var, enabled_var, _auto_var = _streak_controls_for(player)
            enabled_var.set(False)
            status_var.set(f"{player} visible win hold released. The game can show NEW HERO normally again after the next HUD refresh.")
            update_readout()

        def apply_stored_streak(player: str) -> None:
            player = str(player or "P1").upper()
            var, _enabled_var, _auto_var = _streak_controls_for(player)
            value_i = _clamp_int(var.get(), 0, 999)
            var.set(str(value_i))
            last_applied[f"{player}_WIN"] = value_i
            freeze_kinds.add(f"{player}_WIN")
            apply_win_count(player, value_i, use_vs=True, use_hud=bool(hud_var.get()), use_svm=bool(svm_var.get()))
            status_var.set(f"Held {player} wins {value_i} written.")
            update_readout()

        def streak_tick_once() -> None:
            for player in ("P1", "P2"):
                var, enabled_var, auto_var = _streak_controls_for(player)
                if not bool(enabled_var.get()):
                    continue

                current_raw = read_raw_win_count(player)
                previous_raw = streak_last_raw.get(player)
                stored = _clamp_int(var.get(), 0, 999)

                if bool(auto_var.get()) and current_raw is not None and previous_raw is not None:
                    # Positive raw deltas mean this player won another match.
                    # Negative deltas are game resets/new-hero transitions and
                    # should not reduce the preserved visual streak.
                    if current_raw > previous_raw:
                        stored = max(0, min(999, stored + (current_raw - previous_raw)))
                        var.set(str(stored))
                        status_var.set(f"{player} held wins auto-incremented to {stored} from raw {previous_raw}->{current_raw}.")
                    streak_last_raw[player] = current_raw
                elif current_raw is not None:
                    streak_last_raw[player] = current_raw

                last_applied[f"{player}_WIN"] = stored
                freeze_kinds.add(f"{player}_WIN")
                apply_win_count(player, stored, use_vs=True, use_hud=bool(hud_var.get()), use_svm=bool(svm_var.get()))

        def make_win_card(parent: tk.Misc, player: str, var: tk.StringVar) -> tk.Frame:
            card = _card(parent)
            inner = card.inner  # type: ignore[attr-defined]
            _label(inner, f"{player} WINS", bold=True, size=11).grid(row=0, column=0, sticky="w", columnspan=8)
            _label(inner, "0-999. Tens/hundreds panes are enabled when needed.", muted=True).grid(row=1, column=0, sticky="w", columnspan=8, pady=(2, 8))
            _field(inner, var, width=7, max_value=999).grid(row=2, column=0, sticky="w")
            _button(inner, "Apply", lambda: write_win(player, var.get()), width=8).grid(row=2, column=1, padx=(8, 0), sticky="w")
            presets = (0, 1, 2, 3, 5, 10, 21, 99)
            for idx, preset in enumerate(presets):
                def _cmd(v=preset) -> None:
                    var.set(str(v))
                    write_win(player, v)
                _button(inner, str(preset), _cmd, width=4).grid(row=3, column=idx, padx=(0 if idx == 0 else 5, 0), pady=(10, 0), sticky="w")
            return card

        streak_card = _card(root_frame)
        streak_card.pack(fill="x", pady=(0, 10))
        streak_inner = streak_card.inner  # type: ignore[attr-defined]
        streak_inner.grid_columnconfigure(0, weight=1)
        streak_inner.grid_columnconfigure(1, weight=1)
        _label(streak_inner, "HOLD VISIBLE WINS", bold=True, size=11).grid(row=0, column=0, sticky="w", columnspan=2)
        _label(
            streak_inner,
            "Use this before the game resets a side to NEW HERO. Capture the current displayed wins, hold that value on-screen, force WIN instead of NEW HERO, then optionally add later real wins while ignoring resets.",
            muted=True,
            wraplength=980,
        ).grid(row=1, column=0, sticky="ew", columnspan=2, pady=(2, 10))

        def make_hold_card(parent: tk.Misc, col: int, player: str, var: tk.StringVar, enabled_var: tk.BooleanVar, auto_var: tk.BooleanVar) -> None:
            side_outer = tk.Frame(parent, bg=_BORDER, padx=1, pady=1)
            side = tk.Frame(side_outer, bg=_PANEL_2, padx=10, pady=8)
            side.pack(fill="both", expand=True)
            side.grid_columnconfigure(0, weight=0)
            side.grid_columnconfigure(1, weight=1)
            side.grid_columnconfigure(2, weight=0)
            side_outer.grid(row=2, column=col, sticky="ew", padx=(0, 6) if col == 0 else (6, 0))

            _label(side, f"{player} visible wins", bold=True, size=10).grid(row=0, column=0, sticky="w", columnspan=4)
            _check(side, f"Hold {player}", enabled_var).grid(row=1, column=0, sticky="w", pady=(6, 0))
            _check(side, "Auto add real wins", auto_var).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(6, 0))
            _label(side, "Held", muted=True).grid(row=1, column=2, sticky="e", padx=(8, 4), pady=(6, 0))
            _field(side, var, width=6, max_value=999).grid(row=1, column=3, sticky="w", pady=(6, 0))

            btns = tk.Frame(side, bg=_PANEL_2)
            btns.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(8, 0))
            _button(btns, "Capture", lambda p=player: capture_streak_from_vs(p), width=8).pack(side="left")
            _button(btns, "Hold", lambda p=player: enable_streak(p), width=7).pack(side="left", padx=(6, 0))
            _button(btns, "Release", lambda p=player: disable_streak(p), width=8).pack(side="left", padx=(6, 0))
            _button(btns, "-1", lambda p=player: bump_streak(p, -1), width=4).pack(side="left", padx=(10, 0))
            _button(btns, "+1", lambda p=player: bump_streak(p, 1), width=4).pack(side="left", padx=(5, 0))
            _button(btns, "Apply held", lambda p=player: apply_stored_streak(p), width=10).pack(side="left", padx=(8, 0))

        make_hold_card(streak_inner, 0, "P1", p1_streak_var, p1_streak_enabled_var, p1_streak_auto_var)
        make_hold_card(streak_inner, 1, "P2", p2_streak_var, p2_streak_enabled_var, p2_streak_auto_var)

        win_cards = tk.Frame(root_frame, bg=_BG)
        win_cards.pack(fill="x", pady=(0, 10))
        win_cards.grid_columnconfigure(0, weight=1)
        win_cards.grid_columnconfigure(1, weight=1)
        make_win_card(win_cards, "P1", p1_win_var).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        make_win_card(win_cards, "P2", p2_win_var).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        score_cards = tk.Frame(root_frame, bg=_BG)
        score_cards.pack(fill="x", pady=(0, 10))
        score_cards.grid_columnconfigure(0, weight=1)
        score_cards.grid_columnconfigure(1, weight=1)
        score_cards.grid_columnconfigure(2, weight=1)

        score_card = _card(score_cards)
        score_inner = score_card.inner  # type: ignore[attr-defined]
        _label(score_inner, "ARCADE SCORE", bold=True, size=11).grid(row=0, column=0, sticky="w", columnspan=8)
        _label(score_inner, "Texture-only display score. Right-aligned into score digit panes.", muted=True).grid(row=1, column=0, sticky="w", columnspan=8, pady=(2, 8))
        _label(score_inner, "P1", muted=True).grid(row=2, column=0, sticky="w")
        _entry(score_inner, p1_score_var, width=16).grid(row=2, column=1, sticky="w", padx=(6, 0))
        _button(score_inner, "Apply", lambda: write_score("P1", p1_score_var.get()), width=8).grid(row=2, column=2, sticky="w", padx=(8, 0))
        _label(score_inner, "P2", muted=True).grid(row=3, column=0, sticky="w", pady=(8, 0))
        _entry(score_inner, p2_score_var, width=16).grid(row=3, column=1, sticky="w", padx=(6, 0), pady=(8, 0))
        _button(score_inner, "Apply", lambda: write_score("P2", p2_score_var.get()), width=8).grid(row=3, column=2, sticky="w", padx=(8, 0), pady=(8, 0))
        _button(score_inner, "Apply both", lambda: (write_score("P1", p1_score_var.get(), announce=False), write_score("P2", p2_score_var.get(), announce=False), status_var.set("P1/P2 score display written"), update_readout()), width=12).grid(row=4, column=0, sticky="w", pady=(10, 0), columnspan=2)
        score_card.grid(row=0, column=0, columnspan=2, sticky="ew", padx=(0, 6))

        misc_card = _card(score_cards)
        misc_inner = misc_card.inner  # type: ignore[attr-defined]
        _label(misc_inner, "STAGE / TIMER", bold=True, size=11).grid(row=0, column=0, sticky="w", columnspan=4)
        _label(misc_inner, "Stage uses Score digit bank. Timer uses Time digit bank.", muted=True).grid(row=1, column=0, sticky="w", columnspan=4, pady=(2, 8))
        _label(misc_inner, "Stage", muted=True).grid(row=2, column=0, sticky="w")
        _field(misc_inner, stage_var, width=5, max_value=9).grid(row=2, column=1, sticky="w", padx=(6, 0))
        _button(misc_inner, "Apply", lambda: write_stage(stage_var.get()), width=8).grid(row=2, column=2, sticky="w", padx=(8, 0))
        _label(misc_inner, "Timer", muted=True).grid(row=3, column=0, sticky="w", pady=(8, 0))
        _field(misc_inner, timer_var, width=5, max_value=99).grid(row=3, column=1, sticky="w", padx=(6, 0), pady=(8, 0))
        _button(misc_inner, "Apply", lambda: write_timer(timer_var.get()), width=8).grid(row=3, column=2, sticky="w", padx=(8, 0), pady=(8, 0))
        for idx, val in enumerate((56, 60, 90, 99)):
            def _cmd(v=val) -> None:
                timer_var.set(str(v))
                write_timer(v)
            _button(misc_inner, str(val), _cmd, width=4).grid(row=4, column=idx, sticky="w", pady=(10, 0), padx=(0 if idx == 0 else 5, 0))
        misc_card.grid(row=0, column=2, sticky="nsew", padx=(6, 0))

        mid = tk.Frame(root_frame, bg=_BG)
        mid.pack(fill="x", pady=(0, 10))
        _button(mid, "Apply win both", lambda: (write_win("P1", p1_win_var.get(), announce=False), write_win("P2", p2_win_var.get(), announce=False), status_var.set(f"P1 wins={_clamp_int(p1_win_var.get(), 0, 999)}, P2 wins={_clamp_int(p2_win_var.get(), 0, 999)} written"), update_readout()), width=14).pack(side="left")
        _button(mid, "Refresh readout", update_readout, width=14).pack(side="left", padx=(8, 0))
        _label(mid, "Freeze repeats only fields you have applied from this window, about 10 times per second.", muted=True).pack(side="left", padx=(12, 0))

        readout = tk.Label(root_frame, textvariable=readout_var, bg=_BG, fg=_GOOD, anchor="w", justify="left", font=("Segoe UI", 9, "bold"))
        readout.pack(fill="x", pady=(2, 4))

        status = tk.Label(root_frame, textvariable=status_var, bg=_BG, fg=_MUTED, anchor="w", justify="left", wraplength=980, font=("Segoe UI", 9))
        status.pack(fill="x", pady=(2, 0))

        addr_card = _card(root_frame)
        addr_card.pack(fill="x", pady=(10, 0))
        addr_inner = addr_card.inner  # type: ignore[attr-defined]
        _label(addr_inner, "Confirmed live HUD layers", bold=True, size=10).pack(fill="x")
        _label(
            addr_inner,
            "VS wins P1: 0x80BEBB80/BDA0/BFC0, P2: 0x80BEC940/CB60/CD80. Score P1 starts 0x80BE53C0, P2 starts 0x80BE7280. Stage 0x80BE9780. Timer 0x80BE4E80/0x80BE50A0.",
            muted=True,
            wraplength=980,
        ).pack(fill="x", pady=(4, 0))

        def freeze_tick() -> None:
            try:
                if bool(p1_streak_enabled_var.get()) or bool(p2_streak_enabled_var.get()):
                    streak_tick_once()
                if freeze_var.get():
                    if "P1_WIN" in freeze_kinds and not bool(p1_streak_enabled_var.get()):
                        apply_win_count("P1", last_applied.get("P1_WIN", _clamp_int(p1_win_var.get(), 0, 999)), use_vs=bool(vs_var.get()), use_hud=bool(hud_var.get()), use_svm=bool(svm_var.get()))
                    if "P2_WIN" in freeze_kinds and not bool(p2_streak_enabled_var.get()):
                        apply_win_count("P2", last_applied.get("P2_WIN", _clamp_int(p2_win_var.get(), 0, 999)), use_vs=bool(vs_var.get()), use_hud=bool(hud_var.get()), use_svm=bool(svm_var.get()))
                    if "P1_SCORE" in freeze_kinds:
                        apply_score_display("P1", last_applied.get("P1_SCORE", p1_score_var.get()))
                    if "P2_SCORE" in freeze_kinds:
                        apply_score_display("P2", last_applied.get("P2_SCORE", p2_score_var.get()))
                    if "STAGE" in freeze_kinds:
                        apply_stage_display(last_applied.get("STAGE", _clamp_int(stage_var.get(), 0, 9)))
                    if "TIMER" in freeze_kinds:
                        apply_timer_display(last_applied.get("TIMER", _clamp_int(timer_var.get(), 0, 99)), write_raw=bool(timer_raw_var.get()))
            except Exception as e:
                status_var.set(f"Freeze write failed: {e!r}")
            try:
                if bool(win.winfo_exists()):
                    win.after(100, freeze_tick)
            except Exception:
                pass

        def on_close() -> None:
            global _OPEN_WINDOW
            freeze_var.set(False)
            _OPEN_WINDOW = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", on_close)
        update_readout()
        freeze_tick()

    if tk_call is not None:
        tk_call(_show)
        return

    root = tk.Tk()
    root.withdraw()
    _show(root)
    root.mainloop()
