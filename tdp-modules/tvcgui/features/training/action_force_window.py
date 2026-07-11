from __future__ import annotations

import tkinter as tk
from typing import Any

from tvcgui.runtime import action_force

try:
    from tvcgui.core.tk_host import tk_call
except Exception:
    tk_call = None

_WIN: tk.Toplevel | None = None
_BG = "#151821"
_CARD = "#1f2430"
_TEXT = "#f2f5ff"
_MUTED = "#aeb6c8"
_ACCENT = "#8fa8ff"
_GOOD = "#9ad8b8"
_BAD = "#ff9aa6"


def _label(parent: tk.Misc, text: str, *, bold: bool = False, muted: bool = False, wrap: int = 520) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        bg=parent.cget("bg"),
        fg=_MUTED if muted else _TEXT,
        anchor="w",
        justify="left",
        wraplength=wrap,
        font=("Segoe UI", 10, "bold" if bold else "normal"),
    )


def _button(parent: tk.Misc, text: str, command: Any) -> tk.Button:
    return tk.Button(
        parent,
        text=text,
        command=command,
        bg="#2b3142",
        fg=_TEXT,
        activebackground="#394058",
        activeforeground=_TEXT,
        relief="flat",
        padx=12,
        pady=8,
        font=("Segoe UI", 10, "bold"),
    )


def _show(master: tk.Misc | None = None) -> None:
    global _WIN
    if _WIN is not None:
        try:
            if _WIN.winfo_exists():
                _WIN.deiconify()
                _WIN.lift()
                return
        except Exception:
            _WIN = None

    win = tk.Toplevel(master) if master is not None else tk.Toplevel()
    _WIN = win
    win.title("Action Force")
    win.geometry("560x430")
    win.minsize(500, 360)
    win.configure(bg=_BG)

    def _close() -> None:
        global _WIN
        _WIN = None
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", _close)

    body = tk.Frame(win, bg=_BG, padx=14, pady=14)
    body.pack(fill="both", expand=True)
    _label(body, "Action Force", bold=True, wrap=530).pack(fill="x")
    _label(
        body,
        "This targets the action and visual-controller layer instead of pad input. Visual Controller writes the 0x0616 animation-slot cluster from the real 5A dumps along with the action bundle.",
        muted=True,
        wrap=530,
    ).pack(fill="x", pady=(4, 12))

    card = tk.Frame(body, bg=_CARD, highlightthickness=1, highlightbackground="#2c3345")
    card.pack(fill="both", expand=True)

    status_var = tk.StringVar(value="Ready.")
    buttons: list[tk.Button] = []

    def _run(fn: Any, fallback: str) -> None:
        state = fn()
        status_var.set(str(state.get("last_action") or fallback))

    buttons.append(_button(card, "5A Visual Controller 24F", lambda: _run(action_force.request_5a_visual_controller, "Queued 5A visual controller.")))
    buttons.append(_button(card, "5A Kick Bundle Once", lambda: _run(action_force.request_5a_kick, "Queued 5A kick.")))
    buttons.append(_button(card, "5A Request Only", lambda: _run(action_force.request_5a_request, "Queued 5A request.")))
    buttons.append(_button(card, "5A Request + Echo", lambda: _run(action_force.request_5a_request_echo, "Queued 5A request echo.")))
    buttons.append(_button(card, "5A Timeline Bundle 24F", lambda: _run(action_force.request_5a_timeline, "Queued 5A timeline.")))
    buttons.append(_button(card, "5B Request + Echo", lambda: _run(action_force.request_5b_request, "Queued 5B request.")))
    buttons.append(_button(card, "Hado Request + Echo", lambda: _run(action_force.request_hado_request, "Queued Hado request.")))
    buttons.append(_button(card, "Cancel", lambda: _run(action_force.cancel_action_force, "Cancel requested.")))

    for idx, btn in enumerate(buttons):
        btn.pack(fill="x", padx=12, pady=(12 if idx == 0 else 6, 0))

    status_label = tk.Label(
        card,
        textvariable=status_var,
        bg=_CARD,
        fg=_ACCENT,
        font=("Segoe UI", 10, "bold"),
        anchor="w",
        justify="left",
        wraplength=500,
    )
    status_label.pack(fill="x", padx=12, pady=(14, 12))

    def _refresh() -> None:
        state = action_force.get_action_force_state()
        msg = str(state.get("last_error") or state.get("last_action") or "Ready.")
        status_var.set(msg)
        try:
            active = bool(state.get("active"))
            for btn in buttons[:-1]:
                btn.configure(state="disabled" if active else "normal")
            buttons[-1].configure(state="normal" if active else "disabled")
            status_label.configure(fg=_ACCENT if active else (_GOOD if state.get("last_ok") else (_BAD if state.get("last_error") else _ACCENT)))
        except Exception:
            pass
        try:
            win.after(100, _refresh)
        except Exception:
            pass

    _refresh()


def open_action_force_window(master: tk.Misc | None = None) -> None:
    if tk_call is not None:
        tk_call(lambda root: _show(master or root))
        return
    _show(master)
