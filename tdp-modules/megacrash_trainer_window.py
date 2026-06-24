from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

try:
    from tk_host import tk_call
except Exception:  # pragma: no cover
    tk_call = None

try:
    from fd_widgets import apply_titlebar_icon
except Exception:  # pragma: no cover
    def apply_titlebar_icon(_win, _parent=None):
        return None


_OPEN_WINDOW: tk.Toplevel | None = None

_CHANCE_PRESETS = (0, 5, 10, 15, 20, 25, 33, 50, 75, 100)
_MODE_PERCENT = "percent"
_MODE_TARGETED = "targeted"
_SCOPE_ANY = "any"
_LABEL_ANY = ""

_BG = "#0D1018"
_PANEL = "#171D2A"
_PANEL_ALT = "#1B2639"
_PANEL_2 = "#243550"
_BORDER = "#344963"
_TEXT = "#ECF4FF"
_MUTED = "#A9B8CC"
_ACCENT = "#75B8FF"
_ACTIVE = "#315A87"
_FIELD = "#111827"
_GOOD = "#7CE0B5"
_WARN = "#F2CC85"


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        out = int(round(float(value)))
    except Exception:
        out = int(default)
    return max(int(low), min(int(high), out))


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        out = float(value)
    except Exception:
        out = float(default)
    return round(max(float(low), min(float(high), out)), 2)


def _format_seconds(value: Any) -> str:
    return f"{_clamp_float(value, 2.0, 0.0, 60.0):g}"


def _clean_mode(value: Any) -> str:
    value = str(value or "").strip().lower()
    return _MODE_TARGETED if value in {"target", "targeted", "delay", "delayed"} else _MODE_PERCENT


def _clean_scope(value: Any) -> str:
    value = str(value or "").strip()
    return value if value and value.lower() != _SCOPE_ANY else _SCOPE_ANY


def _clean_label(value: Any) -> str:
    value = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    while "  " in value:
        value = value.replace("  ", " ")
    return value[:96]


def _ordinal(value: Any) -> str:
    n = _clamp_int(value, 1, 1, 99)
    if 10 <= (n % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _scope_summary(state: dict, scope_display: dict[str, str] | None = None) -> str:
    scope = _clean_scope(state.get("attacker_scope", _SCOPE_ANY))
    if scope == _SCOPE_ANY:
        return "Any active point"
    if scope_display and scope in scope_display:
        return scope_display[scope]
    return scope.replace("slot:", "")


def _label_summary(value: Any) -> str:
    text = _clean_label(value)
    return text or "Any label"


def _state_label(state: dict, scope_display: dict[str, str] | None = None) -> str:
    enabled = bool(state.get("enabled", False))
    mode = _clean_mode(state.get("mode", _MODE_PERCENT))
    chance = _clamp_int(state.get("chance", 0), 0, 0, 100)
    delay = _clamp_int(state.get("delay_frames", 0), 0, 0, 300)
    cooldown = _format_seconds(state.get("cooldown_sec", 2.0))
    occ = _ordinal(state.get("target_occurrence", 1))
    mode_txt = f"Targeted +{delay}f" if mode == _MODE_TARGETED else f"Random {chance}%"
    return f"{'ON' if enabled else 'OFF'}  •  {_scope_summary(state, scope_display)}  •  {_label_summary(state.get('target_label', ''))}  •  {occ} matching hit in combo  •  {mode_txt}  •  cooldown {cooldown}s"


def _label(parent: tk.Misc, text: str, *, muted: bool = False, bold: bool = False, size: int = 9, color: str | None = None) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        bg=parent.cget("bg"),
        fg=color or (_MUTED if muted else _TEXT),
        font=("Segoe UI", size, "bold" if bold else "normal"),
        anchor="w",
        justify="left",
    )


def _button(parent: tk.Misc, text: str, command: Callable[[], None] | None = None, *, width: int | None = None, primary: bool = False) -> tk.Button:
    return tk.Button(
        parent,
        text=text,
        command=command,
        width=width or 0,
        bg="#2C5E91" if primary else _PANEL_2,
        fg="#FFFFFF" if primary else _TEXT,
        activebackground="#3C73AB" if primary else _ACTIVE,
        activeforeground="#FFFFFF",
        relief="flat",
        bd=0,
        padx=10,
        pady=5,
        font=("Segoe UI", 9, "bold" if primary else "normal"),
        highlightthickness=1,
        highlightbackground="#6094C7" if primary else _BORDER,
        highlightcolor=_ACCENT,
        cursor="hand2",
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


def _card(parent: tk.Misc, *, alt: bool = False) -> tk.Frame:
    outer = tk.Frame(parent, bg=_BORDER, padx=1, pady=1)
    inner = tk.Frame(outer, bg=_PANEL_ALT if alt else _PANEL, padx=13, pady=11)
    inner.pack(fill="both", expand=True)
    outer.inner = inner  # type: ignore[attr-defined]
    return outer


def _configure_combo_style(win: tk.Misc) -> None:
    try:
        style = ttk.Style(win)
        style.theme_use("clam")
        style.configure(
            "Megacrash.TCombobox",
            fieldbackground=_FIELD,
            background=_PANEL_2,
            foreground=_TEXT,
            arrowcolor=_TEXT,
            bordercolor=_BORDER,
            lightcolor=_BORDER,
            darkcolor=_BORDER,
            padding=4,
            font=("Segoe UI", 9),
        )
        style.map(
            "Megacrash.TCombobox",
            fieldbackground=[("readonly", _FIELD)],
            foreground=[("readonly", _TEXT)],
            selectbackground=[("readonly", _ACTIVE)],
            selectforeground=[("readonly", _TEXT)],
        )
    except Exception:
        pass


def _normalize_roster_context(roster_context: Any) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for raw in list(roster_context or []):
        if not isinstance(raw, dict):
            continue
        scope = _clean_scope(raw.get("scope") or raw.get("slot") or raw.get("slot_label"))
        if scope == _SCOPE_ANY:
            continue
        if not scope.startswith("slot:"):
            scope = f"slot:{scope}"
        if scope in seen:
            continue
        seen.add(scope)
        slot = str(raw.get("slot") or raw.get("slot_label") or scope.replace("slot:", "")).strip()
        name = str(raw.get("name") or raw.get("char_name") or "Unknown").strip() or "Unknown"
        try:
            char_id = int(raw.get("char_id") or raw.get("id") or 0)
        except Exception:
            char_id = 0
        labels = []
        seen_labels = set()
        for label in list(raw.get("labels") or []):
            label = _clean_label(label)
            key = label.casefold()
            if label and key not in seen_labels:
                labels.append(label)
                seen_labels.add(key)
        out.append({
            "scope": scope,
            "slot": slot,
            "name": name,
            "char_id": char_id,
            "labels": labels,
            "display": f"{slot} — {name}",
        })
    return out


def open_megacrash_trainer_window(
    state: dict,
    save_func: Callable[[dict], None] | None = None,
    roster_context: list[dict] | Callable[[], list[dict]] | None = None,
) -> None:
    """Open Megacrash controls with roster-scoped label selection."""

    def _show(root: tk.Tk) -> None:
        global _OPEN_WINDOW

        try:
            if _OPEN_WINDOW is not None and bool(_OPEN_WINDOW.winfo_exists()):
                _OPEN_WINDOW.lift()
                _OPEN_WINDOW.focus_force()
                return
        except Exception:
            _OPEN_WINDOW = None

        def _read_roster_context() -> list[dict]:
            try:
                raw = roster_context() if callable(roster_context) else roster_context
            except Exception:
                raw = []
            return _normalize_roster_context(raw)

        roster = _read_roster_context()
        scope_to_entry = {entry["scope"]: entry for entry in roster}
        scope_display = {_SCOPE_ANY: "Any active point"}
        scope_display.update({entry["scope"]: entry["display"] for entry in roster})

        win = tk.Toplevel(root)
        _OPEN_WINDOW = win
        win.title("Megacrash Trainer")
        win.configure(bg=_BG)
        win.minsize(690, 620)
        apply_titlebar_icon(win, root)
        _configure_combo_style(win)

        try:
            win.attributes("-topmost", True)
            win.after(300, lambda: win.attributes("-topmost", False))
        except Exception:
            pass

        enabled_var = tk.BooleanVar(value=bool(state.get("enabled", False)))
        mode_var = tk.StringVar(value=_clean_mode(state.get("mode", _MODE_PERCENT)))
        chance_var = tk.StringVar(value=str(_clamp_int(state.get("chance", 0), 0, 0, 100)))
        delay_var = tk.StringVar(value=str(_clamp_int(state.get("delay_frames", 0), 0, 0, 300)))
        cooldown_var = tk.StringVar(value=_format_seconds(state.get("cooldown_sec", 2.0)))
        scope_var = tk.StringVar(value=_clean_scope(state.get("attacker_scope", _SCOPE_ANY)))
        label_var = tk.StringVar(value=_clean_label(state.get("target_label", "")))
        occurrence_var = tk.StringVar(value=str(_clamp_int(state.get("target_occurrence", 1), 1, 1, 99)))
        status_var = tk.StringVar(value=_state_label(state, scope_display))
        target_status_var = tk.StringVar(value="")
        match_status_var = tk.StringVar(value="")

        if scope_var.get() not in scope_display:
            scope_var.set(_SCOPE_ANY)

        root_frame = tk.Frame(win, bg=_BG, padx=16, pady=14)
        root_frame.pack(fill="both", expand=True)

        header = tk.Frame(root_frame, bg=_BG)
        header.pack(fill="x")
        _label(header, "Megacrash Trainer", bold=True, size=15).pack(side="left")

        # This is intentionally a large, stateful control instead of a tiny title-bar
        # checkbox. Megacrash has an immediate gameplay effect, so its armed state
        # needs to be impossible to miss at a glance.
        enabled_btn = tk.Button(
            header,
            text="",
            width=18,
            height=2,
            bg="#263445",
            fg="#D9E6F4",
            activebackground="#34485F",
            activeforeground="#FFFFFF",
            relief="flat",
            bd=0,
            padx=12,
            pady=4,
            font=("Segoe UI", 9, "bold"),
            highlightthickness=1,
            highlightbackground="#48627F",
            highlightcolor=_ACCENT,
            cursor="hand2",
            takefocus=True,
        )
        enabled_btn.pack(side="right")

        _label(
            root_frame,
            "Point-vs-point trainer. Airborne and grounded hit-reaction states are both eligible. The selected roster slot must be the active point when its matching label appears.",
            muted=True,
        ).pack(fill="x", pady=(4, 12))

        target_card = _card(root_frame, alt=True)
        target_card.pack(fill="x", pady=(0, 10))
        target = target_card.inner  # type: ignore[attr-defined]
        _label(target, "Trigger source", bold=True, size=10).grid(row=0, column=0, sticky="w", columnspan=4)
        _label(
            target,
            "Choose which roster character can arm the burst, then choose one of that character’s known labels. Labels are selectable only; no free typing.",
            muted=True,
        ).grid(row=1, column=0, sticky="w", columnspan=4, pady=(2, 9))

        _label(target, "Attacker", muted=True).grid(row=2, column=0, sticky="w", padx=(0, 8))
        scope_choices = ["Any active point"] + [entry["display"] for entry in roster]
        display_to_scope = {"Any active point": _SCOPE_ANY}
        display_to_scope.update({entry["display"]: entry["scope"] for entry in roster})
        scope_to_display = {value: key for key, value in display_to_scope.items()}
        scope_display_var = tk.StringVar(value=scope_to_display.get(scope_var.get(), "Any active point"))
        scope_combo = ttk.Combobox(target, textvariable=scope_display_var, values=scope_choices, state="readonly", style="Megacrash.TCombobox", width=28)
        scope_combo.grid(row=2, column=1, sticky="ew", pady=(0, 6))

        _label(target, "Label", muted=True).grid(row=3, column=0, sticky="w", padx=(0, 8))
        label_display_var = tk.StringVar(value="Any label")
        label_combo = ttk.Combobox(target, textvariable=label_display_var, values=["Any label"], state="readonly", style="Megacrash.TCombobox", width=40)
        label_combo.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(0, 6))
        refresh_btn = _button(target, "Refresh roster", width=12)
        refresh_btn.grid(row=3, column=3, sticky="e", padx=(8, 0), pady=(0, 6))

        _label(target, "Trigger on", muted=True).grid(row=4, column=0, sticky="w", padx=(0, 8))
        occurrence_row = tk.Frame(target, bg=_PANEL_ALT)
        occurrence_row.grid(row=4, column=1, sticky="w")
        occurrence_spin = _field(occurrence_row, occurrence_var, width=5, spin=True, from_=1, to=99, increment=1)
        occurrence_spin.pack(side="left")
        _label(occurrence_row, "matching hit count in this combo", muted=True).pack(side="left", padx=(8, 0))

        current_lbl = _label(target, "", muted=True)
        current_lbl.configure(textvariable=target_status_var)
        current_lbl.grid(row=5, column=0, sticky="w", columnspan=4, pady=(8, 0))
        match_lbl = _label(target, "", muted=True, color=_GOOD)
        match_lbl.configure(textvariable=match_status_var)
        match_lbl.grid(row=6, column=0, sticky="w", columnspan=4, pady=(3, 0))
        target.grid_columnconfigure(1, weight=1)
        target.grid_columnconfigure(2, weight=1)

        mode_wrap = tk.Frame(root_frame, bg=_BG)
        mode_wrap.pack(fill="x", pady=(0, 10))

        left_card = _card(mode_wrap)
        left_card.pack(side="left", fill="both", expand=True, padx=(0, 6))
        left = left_card.inner  # type: ignore[attr-defined]
        _radio(left, "Random roll", mode_var, _MODE_PERCENT, lambda: _apply()).pack(anchor="w")
        _label(left, "Roll once when the selected label reaches the chosen matching-hit count in the same combo. Multi-hit labels add every hit; other labels do not reset this total.", muted=True).pack(anchor="w", pady=(2, 8))
        chance_row = tk.Frame(left, bg=_PANEL)
        chance_row.pack(fill="x")
        _label(chance_row, "Chance", muted=True).pack(side="left")
        chance_spin = _field(chance_row, chance_var, width=7, spin=True, from_=0, to=100, increment=1)
        chance_spin.pack(side="left", padx=(8, 4))
        _label(chance_row, "%", muted=True).pack(side="left")
        presets = tk.Frame(left, bg=_PANEL)
        presets.pack(fill="x", pady=(8, 0))
        for idx, preset in enumerate(_CHANCE_PRESETS):
            _button(presets, str(preset), lambda v=preset: (chance_var.set(str(v)), mode_var.set(_MODE_PERCENT), _apply()), width=3).grid(row=idx // 5, column=idx % 5, padx=(0, 4), pady=(0, 4), sticky="w")

        right_card = _card(mode_wrap)
        right_card.pack(side="left", fill="both", expand=True, padx=(6, 0))
        right = right_card.inner  # type: ignore[attr-defined]
        _radio(right, "Targeted delay", mode_var, _MODE_TARGETED, lambda: _apply()).pack(anchor="w")
        _label(right, "Force a burst after the selected label reaches the chosen matching-hit count in the same combo.", muted=True).pack(anchor="w", pady=(2, 8))
        delay_row = tk.Frame(right, bg=_PANEL)
        delay_row.pack(fill="x")
        _label(delay_row, "Delay", muted=True).pack(side="left")
        delay_spin = _field(delay_row, delay_var, width=7, spin=True, from_=0, to=300, increment=1)
        delay_spin.pack(side="left", padx=(8, 4))
        _label(delay_row, "frames", muted=True).pack(side="left")
        _label(right, "Cancels if the victim leaves any supported hit-reaction state first.", muted=True).pack(anchor="w", pady=(10, 0))

        bottom_card = _card(root_frame)
        bottom_card.pack(fill="x", pady=(0, 10))
        bottom = bottom_card.inner  # type: ignore[attr-defined]
        cd_row = tk.Frame(bottom, bg=_PANEL)
        cd_row.pack(fill="x")
        _label(cd_row, "Cooldown", bold=True, size=10).pack(side="left")
        cooldown_spin = _field(cd_row, cooldown_var, width=7, spin=True, from_=0.0, to=60.0, increment=0.25)
        cooldown_spin.pack(side="left", padx=(12, 4))
        _label(cd_row, "seconds after each forced burst", muted=True).pack(side="left")

        status = _label(root_frame, "", muted=True)
        status.configure(textvariable=status_var)
        status.pack(fill="x", pady=(0, 10))

        def _label_options_for_scope(scope: str) -> list[str]:
            if scope == _SCOPE_ANY:
                return ["Any label"]
            entry = scope_to_entry.get(scope) or {}
            labels = list(entry.get("labels") or [])
            return ["Any label"] + labels

        def _refresh_label_choices(*, preserve: bool = True) -> None:
            scope = _clean_scope(scope_var.get())
            options = _label_options_for_scope(scope)
            current = _clean_label(label_var.get()) if preserve else ""
            try:
                label_combo.configure(values=options)
            except Exception:
                pass
            if current and current in options:
                label_display_var.set(current)
            else:
                label_display_var.set("Any label")
                if not preserve:
                    label_var.set("")

        def _refresh_target_copy() -> None:
            scope = _clean_scope(scope_var.get())
            label = _clean_label(label_var.get())
            target_status_var.set(f"Armed source: {_scope_summary({'attacker_scope': scope}, scope_display)}  •  label: {_label_summary(label)}  •  count resets when the global combo counter returns to 0")
            count = _clamp_int(state.get("occurrence_counter", 0), 0, 0, 999)
            wanted = _clamp_int(occurrence_var.get(), 1, 1, 99)
            game_count = _clamp_int(state.get("live_combo_counter", 0), 0, 0, 255)
            game_source = str(state.get("live_combo_counter_source", "") or "")
            if game_source:
                match_status_var.set(f"Game combo count: {game_count} ({game_source})  •  Matching hits: {count} / {wanted}")
            else:
                match_status_var.set(f"Matching hits in current combo: {count} / {wanted}  •  waiting for verified counter")

        def _reset_runtime_counts() -> None:
            try:
                state.setdefault("last_combo_keys", {}).clear()
                state.setdefault("pulses", {}).clear()
                state.setdefault("scheduled_triggers", {}).clear()
                state.setdefault("match_occurrences", {}).clear()
                state.setdefault("combo_counter_probes", {}).clear()
                state["global_combo_counter_probe"] = {"last": None, "seen_zero": False}
                state["occurrence_counter"] = 0
                state["live_combo_counter"] = 0
                state["live_combo_counter_source"] = ""
                state["cooldown_until"] = 0.0
            except Exception:
                pass

        def _apply(*, reset_counts: bool = False) -> None:
            old_sig = (
                bool(state.get("enabled", False)),
                _clean_mode(state.get("mode", _MODE_PERCENT)),
                _clean_scope(state.get("attacker_scope", _SCOPE_ANY)),
                _clean_label(state.get("target_label", "")),
                _clamp_int(state.get("target_occurrence", 1), 1, 1, 99),
            )
            new_scope = _clean_scope(scope_var.get())
            new_label = "" if label_display_var.get() == "Any label" else _clean_label(label_display_var.get())
            state["enabled"] = bool(enabled_var.get())
            state["mode"] = _clean_mode(mode_var.get())
            state["chance"] = _clamp_int(chance_var.get(), 0, 0, 100)
            state["delay_frames"] = _clamp_int(delay_var.get(), 0, 0, 300)
            state["cooldown_sec"] = _clamp_float(cooldown_var.get(), 2.0, 0.0, 60.0)
            state["attacker_scope"] = new_scope
            state["target_label"] = new_label
            state["target_occurrence"] = _clamp_int(occurrence_var.get(), 1, 1, 99)
            chance_var.set(str(state["chance"]))
            delay_var.set(str(state["delay_frames"]))
            cooldown_var.set(_format_seconds(state["cooldown_sec"]))
            occurrence_var.set(str(state["target_occurrence"]))
            label_var.set(new_label)

            new_sig = (
                bool(state.get("enabled", False)),
                state["mode"], state["attacker_scope"], state["target_label"], state["target_occurrence"],
            )
            if reset_counts or old_sig != new_sig or not state["enabled"]:
                _reset_runtime_counts()
            if save_func is not None:
                save_func(state)
            _refresh_target_copy()
            status_var.set(_state_label(state, scope_display))
            _refresh_enabled_control()

        def _refresh_enabled_control() -> None:
            armed = bool(enabled_var.get())
            if armed:
                enabled_btn.configure(
                    text="●  MEGACRASH ARMED\n    CLICK TO DISABLE",
                    bg="#176B4C",
                    fg="#F3FFF9",
                    activebackground="#1D805C",
                    activeforeground="#FFFFFF",
                    highlightbackground="#53C992",
                )
            else:
                enabled_btn.configure(
                    text="○  MEGACRASH OFF\n    CLICK TO ARM",
                    bg="#2B3443",
                    fg="#D7E1EE",
                    activebackground="#3A495C",
                    activeforeground="#FFFFFF",
                    highlightbackground="#5A6B80",
                )

        def _toggle_enabled() -> None:
            enabled_var.set(not bool(enabled_var.get()))
            _apply(reset_counts=True)

        def _arm_targeted_for_trigger_change() -> None:
            'A new trigger source is an intentional deterministic setup.\n\n            Source changes should never leave the trainer silently using the\n            random-roll mode from a prior experiment. Random mode remains\n            available whenever the operator explicitly clicks its radio/preset.\n            '
            mode_var.set(_MODE_TARGETED)
            _apply(reset_counts=True)

        def _on_scope_selected(_evt=None) -> None:
            scope_var.set(display_to_scope.get(scope_display_var.get(), _SCOPE_ANY))
            _refresh_label_choices(preserve=False)
            _arm_targeted_for_trigger_change()

        def _on_label_selected(_evt=None) -> None:
            _arm_targeted_for_trigger_change()

        def _refresh_roster() -> None:
            nonlocal roster, scope_to_entry, scope_display, scope_choices, display_to_scope, scope_to_display
            current_scope = _clean_scope(scope_var.get())
            roster = _read_roster_context()
            scope_to_entry = {entry["scope"]: entry for entry in roster}
            scope_display = {_SCOPE_ANY: "Any active point"}
            scope_display.update({entry["scope"]: entry["display"] for entry in roster})
            scope_choices = ["Any active point"] + [entry["display"] for entry in roster]
            display_to_scope = {"Any active point": _SCOPE_ANY}
            display_to_scope.update({entry["display"]: entry["scope"] for entry in roster})
            scope_to_display = {value: key for key, value in display_to_scope.items()}
            if current_scope not in scope_to_display:
                current_scope = _SCOPE_ANY
            scope_var.set(current_scope)
            scope_display_var.set(scope_to_display.get(current_scope, "Any active point"))
            try:
                scope_combo.configure(values=scope_choices)
            except Exception:
                pass
            _refresh_label_choices(preserve=True)
            _apply(reset_counts=True)
            status_var.set("Roster and label dropdowns refreshed from the live character slots.")

        refresh_btn.configure(command=_refresh_roster)
        scope_combo.bind("<<ComboboxSelected>>", _on_scope_selected)
        label_combo.bind("<<ComboboxSelected>>", _on_label_selected)
        enabled_btn.configure(command=_toggle_enabled)
        chance_spin.configure(command=lambda: _apply())
        delay_spin.configure(command=lambda: _apply())
        cooldown_spin.configure(command=lambda: _apply())
        occurrence_spin.configure(command=_arm_targeted_for_trigger_change)
        for widget in (chance_spin, delay_spin, cooldown_spin):
            widget.bind("<Return>", lambda _e: _apply())
            widget.bind("<FocusOut>", lambda _e: _apply())
        occurrence_spin.bind("<Return>", lambda _e: _arm_targeted_for_trigger_change())
        occurrence_spin.bind("<FocusOut>", lambda _e: _arm_targeted_for_trigger_change())
        win.bind("<Return>", lambda _e: _apply())
        win.bind("<Escape>", lambda _e: win.destroy())

        _refresh_label_choices(preserve=True)
        if label_var.get() and label_var.get() in _label_options_for_scope(scope_var.get()):
            label_display_var.set(label_var.get())
        _refresh_target_copy()
        _refresh_enabled_control()

        def _refresh_live_counter_copy() -> None:
            try:
                if not bool(win.winfo_exists()):
                    return
                _refresh_target_copy()
                win.after(120, _refresh_live_counter_copy)
            except Exception:
                pass

        win.after(120, _refresh_live_counter_copy)

        buttons = tk.Frame(root_frame, bg=_BG)
        buttons.pack(fill="x", side="bottom")
        _button(buttons, "Apply", _apply, width=10, primary=True).pack(side="left")
        _button(buttons, "Reset combo count", lambda: (_reset_runtime_counts(), _apply()), width=15).pack(side="left", padx=(8, 0))
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
        try:
            win.update_idletasks()
            req_w = max(700, int(win.winfo_reqwidth()) + 12)
            req_h = max(650, int(win.winfo_reqheight()) + 12)
            screen_h = int(win.winfo_screenheight())
            win.geometry(f"{req_w}x{min(req_h, max(650, screen_h - 70))}")
        except Exception:
            pass
        scope_combo.focus_set()
        win.focus_force()

    if tk_call is not None:
        tk_call(_show)
    else:  # pragma: no cover
        root = tk.Tk()
        root.withdraw()
        _show(root)
        root.mainloop()
