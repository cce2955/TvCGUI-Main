from __future__ import annotations

import tkinter as tk
from typing import Any, Callable

try:
    from tvcgui.core.tk_host import tk_call
except Exception:  # pragma: no cover
    tk_call = None

try:
    from tvcgui.platform.dolphin import rd32, wd32
except Exception:  # pragma: no cover
    rd32 = None
    wd32 = None

try:
    from tvcgui.features.training.win_counter_gate import (
        is_win_counter_runtime_active,
        win_counter_block_message,
    )
except Exception:  # pragma: no cover
    def is_win_counter_runtime_active() -> bool:
        return False

    def win_counter_block_message() -> str:
        return "Win Counter writes are currently unavailable."

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
DIGIT_TEXTURES: dict[int, tuple[int, int]] = {
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

# Digit positions are ones, tens, hundreds.
# VS = active in-fight top HUD copy (vs_1_* / vs_2_*).
# HUD = result/arcade win_* copy.
# SVM = SVM-prefixed copy observed near the same result/HUD resources.
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

_TEXTURE_TO_DIGIT = {pair: digit for digit, pair in DIGIT_TEXTURES.items()}


def _clamp_count(value: Any) -> int:
    try:
        n = int(round(float(value)))
    except Exception:
        n = 0
    return max(0, min(999, n))


def _count_digits(value: Any) -> tuple[int, int, int]:
    n = _clamp_count(value)
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


def _write_pair(addr_a: int, addr_b: int, digit: int) -> bool:
    if wd32 is None:
        return False
    a, b = DIGIT_TEXTURES[int(digit)]
    ok_a = bool(wd32(int(addr_a), int(a)))
    ok_b = bool(wd32(int(addr_b), int(b)))
    return bool(ok_a and ok_b)


def apply_win_count(player: str, value: Any, *, use_vs: bool = True, use_hud: bool = True, use_svm: bool = True) -> bool:
    if not is_win_counter_runtime_active():
        return False
    player = str(player or "P1").upper()
    if player not in WIN_DIGIT_BANKS:
        player = "P1"

    digits = _count_digits(value)
    ok_any = False
    for bank_name in _selected_banks(use_vs, use_hud, use_svm):
        for idx, (addr_a, addr_b) in enumerate(WIN_DIGIT_BANKS[player].get(bank_name, ())) :
            if _write_pair(addr_a, addr_b, digits[idx]):
                ok_any = True
    return ok_any


def read_win_count(player: str, *, bank_name: str = "HUD") -> int | None:
    if rd32 is None:
        return None

    player = str(player or "P1").upper()
    bank_name = str(bank_name or "HUD").upper()
    bank = WIN_DIGIT_BANKS.get(player, {}).get(bank_name)
    if not bank:
        return None

    digits: list[int] = []
    for addr_a, addr_b in bank:
        try:
            pair = (int(rd32(addr_a)), int(rd32(addr_b)))
        except Exception:
            return None
        digit = _TEXTURE_TO_DIGIT.get(pair)
        if digit is None:
            return None
        digits.append(int(digit))

    return digits[0] + digits[1] * 10 + digits[2] * 100


def _label(parent: tk.Misc, text: str, *, muted: bool = False, bold: bool = False, size: int = 9) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        bg=parent.cget("bg"),
        fg=_MUTED if muted else _TEXT,
        font=("Segoe UI", size, "bold" if bold else "normal"),
        anchor="w",
        justify="left",
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


def _field(parent: tk.Misc, textvariable: tk.Variable, *, width: int = 8):
    return tk.Spinbox(
        parent,
        textvariable=textvariable,
        from_=0,
        to=999,
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


def _card(parent: tk.Misc) -> tk.Frame:
    outer = tk.Frame(parent, bg=_BORDER, padx=1, pady=1)
    inner = tk.Frame(outer, bg=_PANEL, padx=12, pady=10)
    inner.pack(fill="both", expand=True)
    outer.inner = inner  # type: ignore[attr-defined]
    return outer


def open_win_counter_window() -> None:
    """Open the live win-counter texture override window."""

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
        win.title("Win Counter")
        win.geometry("720x460")
        win.minsize(680, 430)
        win.configure(bg=_BG)

        try:
            win.attributes("-topmost", True)
            win.after(300, lambda: win.attributes("-topmost", False))
        except Exception:
            pass

        p1_var = tk.StringVar(value="0")
        p2_var = tk.StringVar(value="0")
        vs_var = tk.BooleanVar(value=True)
        hud_var = tk.BooleanVar(value=True)
        svm_var = tk.BooleanVar(value=False)
        freeze_var = tk.BooleanVar(value=False)
        status_var = tk.StringVar(value="Ready. This writes live win digit texture pairs, not the upstream score counter.")
        readout_var = tk.StringVar(value="Current VS: P1 -- | P2 --    Result HUD: P1 -- | P2 --")

        last_applied = {"P1": 0, "P2": 0}

        root_frame = tk.Frame(win, bg=_BG, padx=16, pady=14)
        root_frame.pack(fill="both", expand=True)

        header = tk.Frame(root_frame, bg=_BG)
        header.pack(fill="x")
        _label(header, "Win Counter", bold=True, size=14).pack(side="left")

        freeze_cb = tk.Checkbutton(
            header,
            text="Freeze writes",
            variable=freeze_var,
            bg=_BG,
            fg=_TEXT,
            activebackground=_BG,
            activeforeground=_TEXT,
            selectcolor=_FIELD,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            bd=0,
        )
        freeze_cb.pack(side="right")

        desc = _label(
            root_frame,
            "Uses Score_0.tpl through Score_9.tpl texture pairs. VS is the active in-fight top HUD; HUD/SVM are result-screen copies.",
            muted=True,
        )
        desc.pack(fill="x", pady=(4, 10))

        opts = tk.Frame(root_frame, bg=_BG)
        opts.pack(fill="x", pady=(0, 10))

        def _mk_check(text: str, var: tk.BooleanVar) -> tk.Checkbutton:
            return tk.Checkbutton(
                opts,
                text=text,
                variable=var,
                bg=_BG,
                fg=_TEXT,
                activebackground=_BG,
                activeforeground=_TEXT,
                selectcolor=_FIELD,
                font=("Segoe UI", 9),
                relief="flat",
                bd=0,
            )

        _mk_check("Write VS/in-fight copy", vs_var).pack(side="left")
        _mk_check("Write result HUD copy", hud_var).pack(side="left", padx=(18, 0))
        _mk_check("Write SVM copy too", svm_var).pack(side="left", padx=(18, 0))

        cards = tk.Frame(root_frame, bg=_BG)
        cards.pack(fill="x", pady=(0, 10))
        cards.grid_columnconfigure(0, weight=1)
        cards.grid_columnconfigure(1, weight=1)

        def update_readout() -> None:
            p1_vs = read_win_count("P1", bank_name="VS")
            p2_vs = read_win_count("P2", bank_name="VS")
            p1_hud = read_win_count("P1", bank_name="HUD")
            p2_hud = read_win_count("P2", bank_name="HUD")
            p1_vs_s = "--" if p1_vs is None else str(p1_vs)
            p2_vs_s = "--" if p2_vs is None else str(p2_vs)
            p1_hud_s = "--" if p1_hud is None else str(p1_hud)
            p2_hud_s = "--" if p2_hud is None else str(p2_hud)
            readout_var.set(f"Current VS: P1 {p1_vs_s} | P2 {p2_vs_s}    Result HUD: P1 {p1_hud_s} | P2 {p2_hud_s}")

        def write_player(player: str, value: Any, *, announce: bool = True) -> None:
            value_i = _clamp_count(value)
            ok = apply_win_count(player, value_i, use_vs=bool(vs_var.get()), use_hud=bool(hud_var.get()), use_svm=bool(svm_var.get()))
            last_applied[player] = value_i
            if announce:
                status_var.set(
                    f"{player} win digits set to {value_i}"
                    if ok else
                    win_counter_block_message()
                )
                update_readout()

        def make_player_card(parent: tk.Misc, player: str, var: tk.StringVar) -> tk.Frame:
            card = _card(parent)
            inner = card.inner  # type: ignore[attr-defined]
            _label(inner, f"{player} WINS", bold=True, size=11).grid(row=0, column=0, sticky="w", columnspan=8)
            _label(inner, "Texture override value", muted=True).grid(row=1, column=0, sticky="w", columnspan=8, pady=(2, 8))
            _field(inner, var, width=7).grid(row=2, column=0, sticky="w")
            _button(inner, "Apply", lambda: write_player(player, var.get()), width=8).grid(row=2, column=1, padx=(8, 0), sticky="w")

            presets = (0, 1, 2, 3, 5, 10, 99)
            for idx, preset in enumerate(presets):
                def _cmd(v=preset) -> None:
                    var.set(str(v))
                    write_player(player, v)
                _button(inner, str(preset), _cmd, width=4).grid(row=3, column=idx, padx=(0 if idx == 0 else 5, 0), pady=(10, 0), sticky="w")
            return card

        make_player_card(cards, "P1", p1_var).grid(row=0, column=0, sticky="ew", padx=(0, 6))
        make_player_card(cards, "P2", p2_var).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        mid = tk.Frame(root_frame, bg=_BG)
        mid.pack(fill="x", pady=(0, 10))
        _button(mid, "Apply both", lambda: (write_player("P1", p1_var.get(), announce=False), write_player("P2", p2_var.get(), announce=False), status_var.set(f"P1={_clamp_count(p1_var.get())}, P2={_clamp_count(p2_var.get())} written"), update_readout()), width=12).pack(side="left")
        _button(mid, "Refresh readout", update_readout, width=14).pack(side="left", padx=(8, 0))
        _label(mid, "Freeze repeats the last applied P1/P2 values about 10 times per second.", muted=True).pack(side="left", padx=(12, 0))

        readout = tk.Label(root_frame, textvariable=readout_var, bg=_BG, fg=_GOOD, anchor="w", justify="left", font=("Segoe UI", 9, "bold"))
        readout.pack(fill="x", pady=(2, 4))

        status = tk.Label(root_frame, textvariable=status_var, bg=_BG, fg=_MUTED, anchor="w", justify="left", wraplength=560, font=("Segoe UI", 9))
        status.pack(fill="x", pady=(2, 0))

        addr_card = _card(root_frame)
        addr_card.pack(fill="x", pady=(10, 0))
        addr_inner = addr_card.inner  # type: ignore[attr-defined]
        _label(addr_inner, "Confirmed live digit texture pairs", bold=True, size=10).pack(fill="x")
        _label(
            addr_inner,
            "VS P1: 0x80BEBB80/BB84, 0x80BEBDA0/BDA4, 0x80BEBFC0/BFC4    VS P2: 0x80BEC940/C944, 0x80BECB60/CB64, 0x80BECD80/CD84",
            muted=True,
        ).pack(fill="x", pady=(4, 0))

        def freeze_tick() -> None:
            try:
                if freeze_var.get() and is_win_counter_runtime_active():
                    apply_win_count("P1", last_applied.get("P1", _clamp_count(p1_var.get())), use_vs=bool(vs_var.get()), use_hud=bool(hud_var.get()), use_svm=bool(svm_var.get()))
                    apply_win_count("P2", last_applied.get("P2", _clamp_count(p2_var.get())), use_vs=bool(vs_var.get()), use_hud=bool(hud_var.get()), use_svm=bool(svm_var.get()))
                elif freeze_var.get():
                    status_var.set(win_counter_block_message())
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

    # Fallback for direct/manual runs.
    root = tk.Tk()
    root.withdraw()
    _show(root)
    root.mainloop()
