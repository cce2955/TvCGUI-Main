from __future__ import annotations

import tkinter as tk
from typing import Any, Callable

try:
    from tk_host import tk_call
except Exception:  # pragma: no cover - fallback for direct/manual runs
    tk_call = None


_OPEN_WINDOW: tk.Toplevel | None = None

_CHANCE_PRESETS = (0, 5, 10, 15, 20, 25, 33, 50, 75, 100)
_MODE_PERCENT = "percent"
_MODE_TARGETED = "targeted"

_BG = "#0d1018"
_PANEL = "#171b27"
_PANEL_2 = "#202638"
_BORDER = "#31384d"
_TEXT = "#e9edf7"
_MUTED = "#aeb8cc"
_ACCENT = "#89a7ff"
_ACTIVE = "#2a3553"
_FIELD = "#0f1320"


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        out = int(round(float(value)))
    except Exception:
        out = int(default)
    return max(int(low), min(int(high), int(out)))


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        out = float(value)
    except Exception:
        out = float(default)
    out = max(float(low), min(float(high), out))
    return round(out, 2)


def _format_seconds(value: Any) -> str:
    value = _clamp_float(value, 2.0, 0.0, 60.0)
    return f"{value:g}"


def _clean_mode(value: Any) -> str:
    value = str(value or "").strip().lower()
    if value in {"target", "targeted", "delay", "delayed"}:
        return _MODE_TARGETED
    return _MODE_PERCENT


def _clean_label(value: Any) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    return text[:96]


def _label_summary(value: Any) -> str:
    text = _clean_label(value)
    if not text or text.lower() in {"*", "any", "all"}:
        return "Any opponent label"
    return f"Only: {text}"


def _state_label(state: dict) -> str:
    enabled = bool(state.get("enabled", False))
    mode = _clean_mode(state.get("mode", _MODE_PERCENT))
    chance = _clamp_int(state.get("chance", 25), 25, 0, 100)
    delay = _clamp_int(state.get("delay_frames", 0), 0, 0, 300)
    cooldown = _format_seconds(state.get("cooldown_sec", 2.0))
    label = _label_summary(state.get("target_label", ""))
    mode_txt = f"Targeted +{delay}f" if mode == _MODE_TARGETED else f"Random {chance}%"
    return f"{'ON' if enabled else 'OFF'} - {label} - {mode_txt} - cooldown {cooldown}s"


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


def _field(parent: tk.Misc, textvariable: tk.Variable, *, width: int = 9, spin: bool = False, **spin_kw):
    common = dict(
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
    if spin:
        return tk.Spinbox(parent, buttonbackground=_PANEL_2, **common, **spin_kw)
    return tk.Entry(parent, **common)


def _radio(parent: tk.Misc, text: str, variable: tk.StringVar, value: str, command: Callable[[], None]) -> tk.Radiobutton:
    return tk.Radiobutton(
        parent,
        text=text,
        variable=variable,
        value=value,
        command=command,
        bg=parent.cget("bg"),
        fg=_TEXT,
        activebackground=parent.cget("bg"),
        activeforeground=_TEXT,
        selectcolor=_FIELD,
        font=("Segoe UI", 9, "bold"),
        relief="flat",
        bd=0,
        anchor="w",
        justify="left",
    )


def _card(parent: tk.Misc) -> tk.Frame:
    outer = tk.Frame(parent, bg=_BORDER, padx=1, pady=1)
    inner = tk.Frame(outer, bg=_PANEL, padx=12, pady=10)
    inner.pack(fill="both", expand=True)
    outer.inner = inner  # type: ignore[attr-defined]
    return outer


def open_megacrash_trainer_window(
    state: dict,
    save_func: Callable[[dict], None] | None = None,
) -> None:
    """Open the Megacrash Trainer control window on the shared Tk host."""

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
        win.title("Megacrash Trainer")
        # Default height must show Trigger, both mode cards, Cooldown, status, and buttons.
        # The previous 455px height clipped the bottom controls on Windows.
        win.geometry("590x535")
        win.minsize(560, 500)
        win.configure(bg=_BG)

        try:
            win.attributes("-topmost", True)
            win.after(300, lambda: win.attributes("-topmost", False))
        except Exception:
            pass

        enabled_var = tk.BooleanVar(value=bool(state.get("enabled", False)))
        mode_var = tk.StringVar(value=_clean_mode(state.get("mode", _MODE_PERCENT)))
        chance_var = tk.StringVar(value=str(_clamp_int(state.get("chance", 25), 25, 0, 100)))
        delay_var = tk.StringVar(value=str(_clamp_int(state.get("delay_frames", 0), 0, 0, 300)))
        cooldown_var = tk.StringVar(value=_format_seconds(state.get("cooldown_sec", 2.0)))
        label_var = tk.StringVar(value=_clean_label(state.get("target_label", "")))
        target_status_var = tk.StringVar(value=f"Current trigger: {_label_summary(label_var.get())}")
        status_var = tk.StringVar(value=_state_label(state))

        root_frame = tk.Frame(win, bg=_BG, padx=16, pady=14)
        root_frame.pack(fill="both", expand=True)

        header = tk.Frame(root_frame, bg=_BG)
        header.pack(fill="x")
        _label(header, "Megacrash Trainer", bold=True, size=14).pack(side="left")
        enabled_cb = tk.Checkbutton(
            header,
            text="Enabled",
            variable=enabled_var,
            bg=_BG,
            fg=_TEXT,
            activebackground=_BG,
            activeforeground=_TEXT,
            selectcolor=_FIELD,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
            bd=0,
        )
        enabled_cb.pack(side="right")

        desc = _label(
            root_frame,
            "Point-vs-point only. Watches the attacker point label while the victim point is in hitstun.",
            muted=True,
        )
        desc.pack(fill="x", pady=(4, 12))

        target_card = _card(root_frame)
        target_card.pack(fill="x", pady=(0, 10))
        target = target_card.inner  # type: ignore[attr-defined]
        _label(target, "Trigger label", bold=True, size=10).grid(row=0, column=0, sticky="w", columnspan=4)
        _label(
            target,
            "Type the attacker label to watch, then press Enter or Apply label. Blank means any new opponent label.",
            muted=True,
        ).grid(row=1, column=0, sticky="w", columnspan=4, pady=(2, 8))
        label_entry = _field(target, label_var, width=34)
        label_entry.grid(row=2, column=0, sticky="ew", columnspan=2, pady=(0, 2))
        _button(target, "Apply label", lambda: _apply(), width=10).grid(row=2, column=2, padx=(8, 0), sticky="w")
        _button(target, "Clear to Any", lambda: (label_var.set(""), _apply()), width=10).grid(row=2, column=3, padx=(6, 0), sticky="w")
        current_lbl = _label(target, "", muted=True)
        current_lbl.configure(textvariable=target_status_var)
        current_lbl.grid(row=3, column=0, sticky="w", columnspan=4, pady=(5, 0))
        _label(target, "Examples: 5B, Knee A, Shinkuu Hadouken, 448, 0x01C0", muted=True).grid(row=4, column=0, sticky="w", columnspan=4, pady=(4, 0))
        target.grid_columnconfigure(0, weight=1)

        mode_wrap = tk.Frame(root_frame, bg=_BG)
        mode_wrap.pack(fill="x", pady=(0, 10))

        left_card = _card(mode_wrap)
        left_card.pack(side="left", fill="both", expand=True, padx=(0, 6))
        left = left_card.inner  # type: ignore[attr-defined]
        _radio(left, "Random roll", mode_var, _MODE_PERCENT, lambda: _apply()).pack(anchor="w")
        _label(left, "Roll once per new matching label.", muted=True).pack(anchor="w", pady=(2, 8))
        chance_row = tk.Frame(left, bg=_PANEL)
        chance_row.pack(fill="x")
        _label(chance_row, "Chance", muted=True).pack(side="left")
        chance_spin = _field(chance_row, chance_var, width=7, spin=True, from_=0, to=100, increment=1, command=lambda: _apply())
        chance_spin.pack(side="left", padx=(8, 4))
        _label(chance_row, "%", muted=True).pack(side="left")
        presets = tk.Frame(left, bg=_PANEL)
        presets.pack(fill="x", pady=(8, 0))
        for idx, preset in enumerate(_CHANCE_PRESETS):
            def _mk_set(v=preset):
                return lambda: (chance_var.set(str(v)), mode_var.set(_MODE_PERCENT), _apply())
            _button(presets, str(preset), _mk_set(), width=3).grid(row=idx // 5, column=idx % 5, padx=(0, 4), pady=(0, 4), sticky="w")

        right_card = _card(mode_wrap)
        right_card.pack(side="left", fill="both", expand=True, padx=(6, 0))
        right = right_card.inner  # type: ignore[attr-defined]
        _radio(right, "Targeted delay", mode_var, _MODE_TARGETED, lambda: _apply()).pack(anchor="w")
        _label(right, "Force burst after a matching label appears.", muted=True).pack(anchor="w", pady=(2, 8))
        delay_row = tk.Frame(right, bg=_PANEL)
        delay_row.pack(fill="x")
        _label(delay_row, "Delay", muted=True).pack(side="left")
        delay_spin = _field(delay_row, delay_var, width=7, spin=True, from_=0, to=300, increment=1, command=lambda: _apply())
        delay_spin.pack(side="left", padx=(8, 4))
        _label(delay_row, "frames", muted=True).pack(side="left")
        _label(right, "If hitstun ends before the delay, it cancels.", muted=True).pack(anchor="w", pady=(10, 0))

        bottom_card = _card(root_frame)
        bottom_card.pack(fill="x", pady=(0, 10))
        bottom = bottom_card.inner  # type: ignore[attr-defined]
        cd_row = tk.Frame(bottom, bg=_PANEL)
        cd_row.pack(fill="x")
        _label(cd_row, "Cooldown", bold=True, size=10).pack(side="left")
        cooldown_spin = _field(cd_row, cooldown_var, width=7, spin=True, from_=0.0, to=60.0, increment=0.25, command=lambda: _apply())
        cooldown_spin.pack(side="left", padx=(12, 4))
        _label(cd_row, "seconds after each forced burst", muted=True).pack(side="left")

        status = _label(root_frame, "", muted=True)
        status.configure(textvariable=status_var)
        status.pack(fill="x", pady=(0, 10))

        def _apply() -> None:
            old_mode = _clean_mode(state.get("mode", _MODE_PERCENT))
            old_enabled = bool(state.get("enabled", False))
            old_label = _clean_label(state.get("target_label", ""))
            new_mode = _clean_mode(mode_var.get())
            new_enabled = bool(enabled_var.get())
            new_label = _clean_label(label_var.get())

            state["enabled"] = new_enabled
            state["mode"] = new_mode
            state["chance"] = _clamp_int(chance_var.get(), 25, 0, 100)
            state["delay_frames"] = _clamp_int(delay_var.get(), 0, 0, 300)
            state["cooldown_sec"] = _clamp_float(cooldown_var.get(), 2.0, 0.0, 60.0)
            state["target_label"] = new_label
            chance_var.set(str(state["chance"]))
            delay_var.set(str(state["delay_frames"]))
            cooldown_var.set(_format_seconds(state["cooldown_sec"]))
            label_var.set(state["target_label"])

            if (not new_enabled) or (new_enabled != old_enabled) or (new_mode != old_mode) or (new_label != old_label):
                try:
                    state.setdefault("last_combo_keys", {}).clear()
                    state.setdefault("pulses", {}).clear()
                    state.setdefault("scheduled_triggers", {}).clear()
                    state["cooldown_until"] = 0.0
                except Exception:
                    pass

            if save_func is not None:
                save_func(state)
            target_status_var.set(f"Current trigger: {_label_summary(state.get('target_label', ''))}")
            status_var.set(_state_label(state))

        # Now that _apply exists, wire command-only widgets that were created above.
        enabled_cb.configure(command=_apply)
        label_entry.bind("<Return>", lambda _e: _apply())
        label_entry.bind("<FocusOut>", lambda _e: _apply())
        chance_spin.bind("<Return>", lambda _e: _apply())
        chance_spin.bind("<FocusOut>", lambda _e: _apply())
        delay_spin.bind("<Return>", lambda _e: _apply())
        delay_spin.bind("<FocusOut>", lambda _e: _apply())
        cooldown_spin.bind("<Return>", lambda _e: _apply())
        cooldown_spin.bind("<FocusOut>", lambda _e: _apply())
        win.bind("<Return>", lambda _e: _apply())
        win.bind("<Escape>", lambda _e: win.destroy())

        buttons = tk.Frame(root_frame, bg=_BG)
        buttons.pack(fill="x", side="bottom")
        _button(buttons, "Apply", _apply, width=10).pack(side="left")
        _button(buttons, "Close", lambda: (_apply(), win.destroy()), width=10).pack(side="right")

        def _on_close() -> None:
            global _OPEN_WINDOW
            try:
                _apply()
            except Exception:
                pass
            _OPEN_WINDOW = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)

        # Size to the actual requested content so no control starts hidden.
        # Keep this conservative so it still feels like the rest of the compact GUI.
        try:
            win.update_idletasks()
            screen_h = int(win.winfo_screenheight())
            req_w = max(590, int(win.winfo_reqwidth()) + 8)
            req_h = max(535, int(win.winfo_reqheight()) + 8)
            req_h = min(req_h, max(500, screen_h - 80))
            win.geometry(f"{req_w}x{req_h}")
        except Exception:
            pass

        label_entry.focus_set()
        win.focus_force()

    if tk_call is not None:
        tk_call(_show)
    else:  # pragma: no cover - direct fallback
        root = tk.Tk()
        root.withdraw()
        _show(root)
        root.mainloop()
