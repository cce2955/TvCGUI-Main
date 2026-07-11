from __future__ import annotations

import time
import tkinter as tk
from collections import deque
from typing import Any

from tvcgui.runtime import input_monitor

try:
    from tvcgui.core.tk_host import tk_call
except Exception:
    tk_call = None

_WIN: tk.Toplevel | None = None
_BG = "#151821"
_CARD = "#1f2430"
_CARD_ALT = "#181d27"
_TEXT = "#f2f5ff"
_MUTED = "#aeb6c8"
_ACCENT = "#8fa8ff"
_GOOD = "#9ad8b8"
_BAD = "#ff9aa6"


def _label(
    parent: tk.Misc,
    text: str = "",
    *,
    bold: bool = False,
    muted: bool = False,
    wrap: int = 680,
    variable: tk.StringVar | None = None,
    size: int = 10,
) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        textvariable=variable,
        bg=parent.cget("bg"),
        fg=_MUTED if muted else _TEXT,
        anchor="w",
        justify="left",
        wraplength=wrap,
        font=("Segoe UI", size, "bold" if bold else "normal"),
    )


def _value_row(parent: tk.Misc, title: str, variable: tk.StringVar) -> tk.Frame:
    row = tk.Frame(parent, bg=parent.cget("bg"))
    row.pack(fill="x", padx=12, pady=2)
    tk.Label(
        row,
        text=title,
        width=18,
        bg=row.cget("bg"),
        fg=_MUTED,
        anchor="w",
        font=("Consolas", 9, "bold"),
    ).pack(side="left")
    tk.Label(
        row,
        textvariable=variable,
        bg=row.cget("bg"),
        fg=_TEXT,
        anchor="w",
        justify="left",
        font=("Consolas", 9),
    ).pack(side="left", fill="x", expand=True)
    return row


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
    win.title("Input Monitor")
    win.geometry("760x650")
    win.minsize(680, 560)
    win.configure(bg=_BG)

    closed = False

    def _close() -> None:
        nonlocal closed
        global _WIN
        closed = True
        _WIN = None
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", _close)

    body = tk.Frame(win, bg=_BG, padx=14, pady=14)
    body.pack(fill="both", expand=True)
    _label(body, "Live Input Monitor", bold=True, size=13).pack(fill="x")
    _label(
        body,
        "Reads the verified fighter input packet directly. It does not use Punish Training or write input values.",
        muted=True,
    ).pack(fill="x", pady=(3, 10))

    toolbar = tk.Frame(body, bg=_BG)
    toolbar.pack(fill="x", pady=(0, 8))
    _label(toolbar, "Slot", bold=True, wrap=100).pack(side="left", padx=(0, 8))
    slot_var = tk.StringVar(value="P1-C1")
    slot_menu = tk.OptionMenu(toolbar, slot_var, *input_monitor.available_slots())
    slot_menu.configure(
        bg="#2b3142",
        fg=_TEXT,
        activebackground="#394058",
        activeforeground=_TEXT,
        highlightthickness=0,
        relief="flat",
        font=("Segoe UI", 10, "bold"),
    )
    slot_menu["menu"].configure(bg="#2b3142", fg=_TEXT, activebackground="#394058", activeforeground=_TEXT)
    slot_menu.pack(side="left")

    status_var = tk.StringVar(value="Waiting for Dolphin...")
    _label(toolbar, variable=status_var, muted=True, wrap=500).pack(side="left", padx=(12, 0), fill="x", expand=True)

    live_card = tk.Frame(body, bg=_CARD, highlightthickness=1, highlightbackground="#2c3345")
    live_card.pack(fill="x")

    held_var = tk.StringVar(value="5")
    action_var = tk.StringVar(value="Action: --")
    identity_var = tk.StringVar(value="--")
    pressed_var = tk.StringVar(value="Pressed: none")
    released_var = tk.StringVar(value="Released: none")

    tk.Label(
        live_card,
        textvariable=held_var,
        bg=_CARD,
        fg=_ACCENT,
        anchor="center",
        font=("Consolas", 30, "bold"),
        pady=8,
    ).pack(fill="x", padx=12, pady=(8, 0))
    _label(live_card, variable=action_var, bold=True, wrap=700).pack(fill="x", padx=12)
    _label(live_card, variable=identity_var, muted=True, wrap=700).pack(fill="x", padx=12, pady=(2, 6))

    edge_row = tk.Frame(live_card, bg=_CARD)
    edge_row.pack(fill="x", padx=12, pady=(0, 10))
    _label(edge_row, variable=pressed_var, bold=True, wrap=330).pack(side="left", fill="x", expand=True)
    _label(edge_row, variable=released_var, muted=True, wrap=330).pack(side="left", fill="x", expand=True)

    history_card = tk.Frame(body, bg=_CARD_ALT, highlightthickness=1, highlightbackground="#2c3345")
    history_card.pack(fill="x", pady=(8, 0))
    _label(history_card, "Recent input stream", bold=True).pack(fill="x", padx=12, pady=(8, 2))
    history_var = tk.StringVar(value="")
    tk.Label(
        history_card,
        textvariable=history_var,
        bg=_CARD_ALT,
        fg=_GOOD,
        anchor="w",
        justify="left",
        wraplength=700,
        font=("Consolas", 12, "bold"),
        padx=12,
        pady=8,
    ).pack(fill="x")

    details = tk.Frame(body, bg=_CARD, highlightthickness=1, highlightbackground="#2c3345")
    details.pack(fill="x", pady=(8, 0))
    _label(details, "Runtime packet", bold=True).pack(fill="x", padx=12, pady=(8, 3))

    raw_vars = {name: tk.StringVar(value="00000000") for name in (
        "previous", "held", "pressed", "released", "repeat_a", "repeat_b", "software",
        "state", "commands", "source", "rule_table",
    )}
    _value_row(details, "+13C8 previous", raw_vars["previous"])
    _value_row(details, "+13CC held", raw_vars["held"])
    _value_row(details, "+13D0 pressed", raw_vars["pressed"])
    _value_row(details, "+13D4 released", raw_vars["released"])
    _value_row(details, "+13D8 repeat A", raw_vars["repeat_a"])
    _value_row(details, "+13DC repeat B", raw_vars["repeat_b"])
    _value_row(details, "+13E0 software", raw_vars["software"])
    _value_row(details, "+58 / +60 state", raw_vars["state"])
    _value_row(details, "command packet", raw_vars["commands"])
    _value_row(details, "P1 source", raw_vars["source"])
    _value_row(details, "+13E8 rule table", raw_vars["rule_table"])

    rule_card = tk.Frame(body, bg=_CARD_ALT, highlightthickness=1, highlightbackground="#2c3345")
    rule_card.pack(fill="both", expand=True, pady=(8, 0))
    _label(rule_card, "Rule associated with the current action", bold=True).pack(fill="x", padx=12, pady=(8, 3))
    rule_var = tk.StringVar(value="No normal-rule record for the current action.")
    _label(rule_card, variable=rule_var, muted=True, wrap=700).pack(fill="x", padx=12, pady=(0, 9))

    history: deque[str] = deque(maxlen=24)
    last_held_key: int | None = None
    last_action_id: int | None = None
    last_slot = slot_var.get()
    last_non_neutral_at = 0.0

    def _clear_history(*_args: Any) -> None:
        nonlocal last_held_key, last_action_id, last_slot
        history.clear()
        history_var.set("")
        last_held_key = None
        last_action_id = None
        last_slot = slot_var.get()

    slot_var.trace_add("write", _clear_history)

    def _hex(value: Any) -> str:
        return f"0x{int(value or 0) & 0xFFFFFFFF:08X}"

    def _record_input(snapshot: dict[str, Any]) -> None:
        nonlocal last_held_key, last_action_id, last_non_neutral_at, last_slot
        current_slot = str(snapshot.get("slot") or slot_var.get())
        if current_slot != last_slot:
            _clear_history()
            last_slot = current_slot

        held = int(snapshot.get("held", 0)) & 0xFF
        pressed = int(snapshot.get("pressed", 0)) & 0xFF
        relevant = held & input_monitor.KNOWN_INPUT_MASK
        event_key = relevant
        now = time.monotonic()

        if event_key != last_held_key:
            notation = input_monitor.format_input_word(held, neutral_label=False)
            if notation:
                history.append(notation)
                last_non_neutral_at = now
            elif history and now - last_non_neutral_at > 0.12:
                history.append("·")
            last_held_key = event_key
        elif pressed & (input_monitor.BUTTON_A | input_monitor.BUTTON_B | input_monitor.BUTTON_C | input_monitor.BUTTON_PARTNER):
            notation = input_monitor.format_input_word(held, neutral_label=False)
            if notation and (not history or history[-1] != notation):
                history.append(notation)
                last_non_neutral_at = now

        action_id = int(snapshot.get("action_id", 0))
        if action_id != last_action_id:
            action_name = str(snapshot.get("action_name") or "")
            if action_id >= 0x100 and action_name:
                marker = f"[{action_name}]"
                if not history or history[-1] != marker:
                    history.append(marker)
            last_action_id = action_id

        history_var.set("  ".join(history))

    def _refresh() -> None:
        if closed:
            return
        snapshot = input_monitor.read_input_snapshot(slot_var.get())
        if not snapshot.get("connected"):
            status_var.set(str(snapshot.get("error") or "Waiting for Dolphin..."))
            held_var.set("--")
            action_var.set("Action: --")
            identity_var.set(f"{slot_var.get()} pointer {_hex(snapshot.get('pointer_address'))}")
        else:
            status_var.set("Live")
            held_var.set(str(snapshot.get("held_text") or "5"))
            action_id = int(snapshot.get("action_id", 0))
            action_name = str(snapshot.get("action_name") or "unknown")
            action_var.set(f"Action: 0x{action_id:03X}  {action_name}")
            identity_var.set(
                f"{snapshot.get('slot')}  {snapshot.get('char_name')}  "
                f"fighter {_hex(snapshot.get('base'))}"
            )
            pressed_var.set(f"Pressed: {snapshot.get('pressed_text')}")
            released_var.set(f"Released: {snapshot.get('released_text')}")

            raw_vars["previous"].set(_hex(snapshot.get("previous")))
            raw_vars["held"].set(f"{_hex(snapshot.get('held'))}  {snapshot.get('held_text')}")
            raw_vars["pressed"].set(f"{_hex(snapshot.get('pressed'))}  {snapshot.get('pressed_text')}")
            raw_vars["released"].set(f"{_hex(snapshot.get('released'))}  {snapshot.get('released_text')}")
            raw_vars["repeat_a"].set(_hex(snapshot.get("repeat_a")))
            raw_vars["repeat_b"].set(_hex(snapshot.get("repeat_b")))
            raw_vars["software"].set(_hex(snapshot.get("software_flags")))
            raw_vars["state"].set(f"{_hex(snapshot.get('state_a'))} / {_hex(snapshot.get('state_b'))}")
            raw_vars["commands"].set(
                f"accepted={_hex(snapshot.get('accepted_command'))}  "
                f"pending={_hex(snapshot.get('pending_command_index'))}  "
                f"flags={_hex(snapshot.get('pending_command_flags'))}  "
                f"meta={_hex(snapshot.get('pending_command_meta'))}"
            )
            if snapshot.get("slot") == "P1-C1":
                raw_vars["source"].set(
                    f"status={_hex(snapshot.get('source_status'))}  "
                    f"decoded={_hex(snapshot.get('source_decoded'))}  "
                    f"raw={_hex(snapshot.get('source_raw'))}"
                )
            else:
                raw_vars["source"].set("P1-C1 source globals only")
            raw_vars["rule_table"].set(
                f"{_hex(snapshot.get('rule_table'))}  entries={int(snapshot.get('rule_count', 0))}"
            )

            action_rules = list(snapshot.get("current_action_rules") or [])
            if action_rules:
                rule_var.set("\n".join(input_monitor.format_rule(entry) for entry in action_rules[:4]))
            elif action_id >= 0x100:
                rule_var.set("No normal-rule record for this action. It may come from the special-command or another resolver path.")
            else:
                rule_var.set("No normal-rule record for the current movement/state action.")

            _record_input(snapshot)

        try:
            win.after(16, _refresh)
        except Exception:
            pass

    _refresh()


def open_input_spoof_window(master: tk.Misc | None = None) -> None:
    """Compatibility entry point retained for existing main-window wiring."""
    if tk_call is not None:
        tk_call(lambda root: _show(master or root))
        return
    _show(master)
