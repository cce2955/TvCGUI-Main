from __future__ import annotations

import tkinter as tk
from typing import Any

from . import runtime

try:
    from tvcgui.core.tk_host import tk_call
except Exception:  # pragma: no cover
    tk_call = None

_WIN: tk.Toplevel | None = None
_BG = "#151821"
_CARD = "#1f2430"
_TEXT = "#f2f5ff"
_MUTED = "#aeb6c8"
_ACCENT = "#8fa8ff"


def _label(parent: tk.Misc, text: str, *, bold: bool = False, muted: bool = False, wrap: int = 820) -> tk.Label:
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
        padx=10,
        pady=8,
        font=("Segoe UI", 10, "bold"),
    )


def _card(parent: tk.Misc) -> tk.Frame:
    return tk.Frame(parent, bg=_CARD, highlightthickness=1, highlightbackground="#2c3345")


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
    win.title("Stage IDs")
    win.geometry("880x540")
    win.minsize(700, 430)
    win.configure(bg=_BG)

    def _close() -> None:
        global _WIN
        _WIN = None
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", _close)

    body = tk.Frame(win, bg=_BG, padx=14, pady=14)
    body.pack(fill="both", expand=True)
    _label(body, "Stage IDs", bold=True).pack(fill="x")
    _label(
        body,
        "Direct raw cursor control only. No probe gate, FPK patching, stage-table scan, or Yami handoff is running in this build. Grab reads the current raw ID. Apply, Previous, and Next write one raw ID and stop.",
        muted=True,
        wrap=850,
    ).pack(fill="x", pady=(4, 12))

    action_card = _card(body)
    action_card.pack(fill="x", pady=(0, 10))
    _label(action_card, "Raw stage ID", bold=True).pack(fill="x", padx=12, pady=(12, 4))
    _label(
        action_card,
        "Use this while the in-game stage carousel is on screen. The stable cursor mirror is always written. When the carousel runtime is present, its matching mirror is written too. Nothing loops afterward.",
        muted=True,
        wrap=820,
    ).pack(fill="x", padx=12, pady=(0, 10))

    code_row = tk.Frame(action_card, bg=_CARD)
    code_row.pack(fill="x", padx=12, pady=(0, 8))
    _label(code_row, "ID", bold=True, wrap=100).grid(row=0, column=0, sticky="w", padx=(0, 8))
    code_var = tk.StringVar(value=str(runtime.RAW_CODE_DEFAULT))
    code_box = tk.Entry(
        code_row,
        textvariable=code_var,
        width=14,
        justify="center",
        bg="#11151e",
        fg=_TEXT,
        insertbackground=_TEXT,
        relief="flat",
        font=("Segoe UI", 11, "bold"),
    )
    code_box.grid(row=0, column=1, sticky="w")
    _label(
        code_row,
        "Decimal or 0x hexadecimal. This is uncapped through 0xFFFFFFFF.",
        muted=True,
        wrap=540,
    ).grid(row=0, column=2, sticky="w", padx=(10, 0))
    code_row.grid_columnconfigure(2, weight=1)

    status_var = tk.StringVar(value="Ready.")

    def _state_message(state: dict[str, Any]) -> str:
        if state.get("worker_busy"):
            return str(state.get("last_action") or "Stage ID operation running…")
        return str(state.get("last_error") or state.get("last_action") or "Ready.")

    def _current_code() -> int | None:
        raw = code_var.get().strip()
        try:
            value = int(raw, 0) if raw.lower().startswith(("0x", "+0x")) else int(raw, 10)
        except Exception:
            status_var.set("Enter a whole unsigned 32-bit ID in decimal or 0x hexadecimal.")
            return None
        if not runtime.TEST_CODE_MIN <= value <= runtime.TEST_CODE_MAX:
            status_var.set("ID must be from 0 through 0xFFFFFFFF.")
            return None
        return value

    def _grab() -> None:
        status_var.set(_state_message(runtime.request_stage_probe()))

    def _apply() -> None:
        code = _current_code()
        if code is None:
            return
        status_var.set(_state_message(runtime.request_stage_selection(code, label=f"raw stage ID {code}")))

    def _step(delta: int) -> None:
        code = _current_code()
        if code is None:
            return
        target = code + delta
        if target < runtime.TEST_CODE_MIN:
            status_var.set("Already at ID 0.")
            return
        if target > runtime.TEST_CODE_MAX:
            status_var.set("Already at ID 0xFFFFFFFF.")
            return
        code_var.set(str(target))
        _apply()

    actions = tk.Frame(action_card, bg=_CARD)
    actions.pack(fill="x", padx=12, pady=(0, 12))
    _button(actions, "Grab live ID", _grab).grid(row=0, column=0, sticky="ew", padx=(0, 6))
    _button(actions, "Previous", lambda: _step(-1)).grid(row=0, column=1, sticky="ew", padx=6)
    _button(actions, "Apply ID", _apply).grid(row=0, column=2, sticky="ew", padx=6)
    _button(actions, "Next", lambda: _step(1)).grid(row=0, column=3, sticky="ew", padx=(6, 0))
    for idx in range(4):
        actions.grid_columnconfigure(idx, weight=1)

    state_card = _card(body)
    state_card.pack(fill="both", expand=True)
    _label(state_card, "Live state", bold=True).pack(fill="x", padx=12, pady=(12, 4))
    tk.Label(
        state_card,
        textvariable=status_var,
        bg=_CARD,
        fg=_ACCENT,
        font=("Segoe UI", 10, "bold"),
        anchor="w",
        justify="left",
        wraplength=820,
    ).pack(fill="x", padx=12, pady=(0, 6))
    text = tk.Text(state_card, height=12, bg="#11151e", fg=_TEXT, insertbackground=_TEXT, relief="flat", wrap="word")
    text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    last_read_signature: tuple[Any, ...] | None = None

    def _refresh() -> None:
        nonlocal last_read_signature
        state = runtime.get_stage_probe_state()
        read = state.get("last_read") or {}
        signature = (
            read.get("global_code"),
            read.get("runtime_code"),
            read.get("runtime_live"),
            read.get("runtime"),
        )
        if read.get("global_code") is not None and signature != last_read_signature:
            code_var.set(str(int(read["global_code"])))
            last_read_signature = signature
        last_write = state.get("last_write") or {}
        addresses = state.get("addresses") or {}
        lines = [
            f"Global raw cursor: {read.get('global_code', '--')}",
            f"Live carousel cursor: {read.get('runtime_code', '--') if read.get('runtime_live') else 'not currently resolved'}",
            f"Global mirror: {addresses.get('global_selection', '--')}",
            f"Carousel root: {addresses.get('runtime_root', '--')}",
            f"Carousel mirror: {addresses.get('runtime_selection', '--')}",
            "",
            "The global raw cursor is the direct control. A missing carousel mirror does not block Apply.",
            "Open the normal in-game stage carousel before expecting the highlighted tile to move.",
            "",
            f"Last action: {state.get('last_action') or '--'}",
            f"Last error: {state.get('last_error') or '--'}",
        ]
        if last_write:
            lines.extend([
                "",
                "Last write:",
                f"  target={last_write.get('target', '--')} verified={last_write.get('verified', False)}",
                f"  global {last_write.get('before_global', '--')} -> {last_write.get('after_global', '--')}",
                f"  runtime {last_write.get('before_runtime', '--')} -> {last_write.get('after_runtime', '--')}",
            ])
        text.delete("1.0", "end")
        text.insert("1.0", "\n".join(lines))
        status_var.set(_state_message(state))
        try:
            win.after(200, _refresh)
        except Exception:
            pass

    _refresh()


def open_stage_select_window(master: tk.Misc | None = None) -> None:
    if tk_call is not None:
        tk_call(lambda root: _show(master or root))
        return
    _show(master)


def open_stage_test_window(master: tk.Misc | None = None) -> None:
    open_stage_select_window(master)
