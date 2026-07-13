from __future__ import annotations

import time
import threading
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
    win.geometry("780x650")
    win.minsize(700, 560)
    win.configure(bg=_BG)

    closed = False
    sampler_stop = threading.Event()

    def _close() -> None:
        nonlocal closed
        global _WIN
        closed = True
        sampler_stop.set()
        _WIN = None
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", _close)

    body = tk.Frame(win, bg=_BG, padx=14, pady=14)
    body.pack(fill="both", expand=True)
    _label(body, "Live Input Monitor", bold=True, size=13).pack(fill="x")
    _label(
        body,
        "Read-only monitor. Polls the fighter input packet from emulated RAM. It does not enable debugging, install Gecko codes, edit Dolphin configuration, create breakpoints, or inject inputs.",
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

    history: deque[str] = deque(maxlen=32)
    sample_lock = threading.RLock()
    sample_queue: deque[dict[str, int]] = deque(maxlen=192)
    sampler_state: dict[str, Any] = {
        "slot": slot_var.get(),
        "raw": None,
        "latest": {},
        "seq": 0,
    }
    last_action_id: int | None = None
    last_slot = slot_var.get()
    last_non_neutral_at = 0.0
    next_detail_refresh = 0.0

    def _reset_sampler(slot: str) -> None:
        with sample_lock:
            sampler_state["slot"] = str(slot)
            sampler_state["raw"] = None
            sampler_state["latest"] = {}
            sample_queue.clear()

    def _clear_history(*_args: Any) -> None:
        nonlocal last_action_id, last_slot, last_non_neutral_at
        history.clear()
        history_var.set("")
        last_action_id = None
        last_slot = slot_var.get()
        last_non_neutral_at = 0.0
        _reset_sampler(last_slot)

    slot_var.trace_add("write", _clear_history)

    def _hex(value: Any) -> str:
        return f"0x{int(value or 0) & 0xFFFFFFFF:08X}"

    def _sampler_loop() -> None:
        interval = 1.0 / 240.0
        next_tick = time.perf_counter()
        while not sampler_stop.is_set():
            with sample_lock:
                selected_slot = str(sampler_state.get("slot") or "P1-C1")
            try:
                packet = input_monitor.read_overlay_input_packet(selected_slot)
            except Exception:
                packet = {}

            held = int(packet.get("held", 0) or 0) & 0xFFFF
            raw_pressed = int(packet.get("pressed", 0) or 0) & 0xFFFF
            raw_released = int(packet.get("released", 0) or 0) & 0xFFFF

            with sample_lock:
                if selected_slot != str(sampler_state.get("slot") or ""):
                    continue
                previous = sampler_state.get("raw")
                if previous is None:
                    previous_held = held
                    previous_pressed = 0
                    previous_released = 0
                else:
                    previous_held, previous_pressed, previous_released = previous

                fresh_pressed = raw_pressed & ~int(previous_pressed)
                fresh_released = raw_released & ~int(previous_released)
                held_changed = previous is None or held != int(previous_held)

                sampler_state["raw"] = (held, raw_pressed, raw_released)
                sampler_state["latest"] = {
                    **dict(packet or {}),
                    "held": held,
                    "pressed": fresh_pressed,
                    "released": fresh_released,
                }

                # Ignore repeated reads of the same one-frame edge. A genuine
                # second tap has a neutral/release transition before it returns.
                if held_changed or fresh_pressed or fresh_released:
                    sampler_state["seq"] = int(sampler_state.get("seq", 0) or 0) + 1
                    sample_queue.append({
                        "seq": int(sampler_state["seq"]),
                        "held": held,
                        "pressed": fresh_pressed,
                        "released": fresh_released,
                    })

            next_tick += interval
            delay = next_tick - time.perf_counter()
            if delay <= 0.0:
                next_tick = time.perf_counter()
                delay = 0.001
            sampler_stop.wait(delay)

    sampler_thread = threading.Thread(
        target=_sampler_loop,
        name="TvCInputMonitorSampler",
        daemon=True,
    )
    sampler_thread.start()

    def _record_sample(sample: dict[str, int]) -> None:
        nonlocal last_non_neutral_at
        held = int(sample.get("held", 0) or 0) & 0xFFFF
        notation = input_monitor.format_input_word(held, neutral_label=False)
        now = time.monotonic()
        if notation:
            # Do not compare with history[-1]. Identical tokens separated by a
            # real neutral/release sample are separate taps and must both appear.
            history.append(notation)
            last_non_neutral_at = now
        elif history and now - last_non_neutral_at > 0.12:
            if history[-1] != "·":
                history.append("·")
        history_var.set("  ".join(history))

    def _record_action(snapshot: dict[str, Any]) -> None:
        nonlocal last_action_id
        action_id = int(snapshot.get("action_id", 0) or 0)
        if action_id == last_action_id:
            return
        action_name = str(snapshot.get("action_name") or "")
        if action_id >= 0x100 and action_name:
            marker = f"[{action_name}]"
            if not history or history[-1] != marker:
                history.append(marker)
                history_var.set("  ".join(history))
        last_action_id = action_id

    def _update_fast_input_ui(packet: dict[str, Any]) -> None:
        if not packet:
            return
        if not packet.get("connected", True):
            status_var.set("Waiting for a live fighter pointer...")
            return
        status_var.set("Live, 240 Hz input capture")
        held = int(packet.get("held", 0) or 0) & 0xFFFF
        pressed = int(packet.get("pressed", 0) or 0) & 0xFFFF
        released = int(packet.get("released", 0) or 0) & 0xFFFF
        held_var.set(input_monitor.format_input_word(held))
        pressed_var.set(f"Pressed: {input_monitor.format_button_edges(pressed)}")
        released_var.set(f"Released: {input_monitor.format_button_edges(released)}")
        raw_vars["previous"].set(_hex(packet.get("previous")))
        raw_vars["held"].set(f"{_hex(held)}  {input_monitor.format_input_word(held)}")
        raw_vars["pressed"].set(f"{_hex(pressed)}  {input_monitor.format_button_edges(pressed)}")
        raw_vars["released"].set(f"{_hex(released)}  {input_monitor.format_button_edges(released)}")

    def _refresh_details(snapshot: dict[str, Any]) -> None:
        if not snapshot.get("connected"):
            identity_var.set(f"{slot_var.get()} pointer {_hex(snapshot.get('pointer_address'))}")
            action_var.set("Action: --")
            return

        action_id = int(snapshot.get("action_id", 0))
        action_name = str(snapshot.get("action_name") or "unknown")
        action_var.set(f"Action: 0x{action_id:03X}  {action_name}")
        identity_var.set(
            f"{snapshot.get('slot')}  {snapshot.get('char_name')}  "
            f"fighter {_hex(snapshot.get('base'))}"
        )
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
        _record_action(snapshot)

    def _refresh() -> None:
        nonlocal next_detail_refresh
        if closed:
            return

        with sample_lock:
            latest = dict(sampler_state.get("latest") or {})
            pending = list(sample_queue)
            sample_queue.clear()

        for sample in pending:
            _record_sample(sample)
        _update_fast_input_ui(latest)

        now = time.monotonic()
        if now >= next_detail_refresh:
            next_detail_refresh = now + 0.25
            try:
                snapshot = input_monitor.read_input_snapshot(slot_var.get())
            except Exception as exc:
                snapshot = {"connected": False, "error": repr(exc)}
            if not snapshot.get("connected"):
                if not latest.get("connected"):
                    status_var.set(str(snapshot.get("error") or "Waiting for Dolphin..."))
                    held_var.set("--")
            _refresh_details(snapshot)

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
