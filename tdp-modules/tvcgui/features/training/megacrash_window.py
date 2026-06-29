from __future__ import annotations

import re
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

try:
    from tvcgui.core.tk_host import tk_call
except Exception:  # pragma: no cover
    tk_call = None

try:
    from tvcgui.features.frame_data.widgets import apply_titlebar_icon
except Exception:  # pragma: no cover
    def apply_titlebar_icon(_win, _parent=None):
        return None


_OPEN_WINDOW: tk.Toplevel | None = None
_SCOPE_ANY = "any"

# The trainer's Reset button restores all five user-facing controls.
# It deliberately leaves the armed/off toggle alone so reset is a quick
# "start this setup over" action rather than a surprise disarm.
_DEFAULT_SCOPE = _SCOPE_ANY
_DEFAULT_LABEL = ""
_DEFAULT_OCCURRENCE = 1
_DEFAULT_CHANCE = 100
_DEFAULT_DELAY_FRAMES = 5
_DEFAULT_COOLDOWN_SEC = 3.0

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

# Dropdown sections deliberately mirror the readable Frame Data order rather
# than the raw CSV/alphabetical order.  Section entries are display-only: the
# selected value is always a real move label (or Any label), never a heading.
_LABEL_SECTION_ORDER = ("normal", "special", "super", "projectile", "other")
_LABEL_SECTION_TITLES = {
    "normal": "NORMALS",
    "special": "SPECIALS",
    "super": "SUPERS",
    "projectile": "PROJECTILES",
    "other": "OTHER",
}
_LABEL_SECTION_HEADERS = {
    f"──── {title} ────"
    for title in _LABEL_SECTION_TITLES.values()
}
_NORMAL_LABEL_ORDER = {
    "5a": 0, "2a": 1, "5b": 2, "2b": 3, "6b": 4,
    "5c": 5, "2c": 6, "6c": 7, "4c": 8, "3c": 9,
    "ja": 10, "jb": 11, "jc": 12,
}
_SUPER_WORDS = (
    "super", "hyper", "shinkuu", "shinku", "shin shoryu",
    "shinsho", "shin sho", "voltekka", "level 3", "lv3",
)
_PROJECTILE_WORDS = (
    "projectile", "fireball", "shot", "bullet", "missile",
    "beam", "laser", "bomb", "mine", "grenade", "orb",
    "soul fist", "hadouken", "hadoken", "hado", "hadou",
    "kikoken", "charge shot",
)
_SPECIAL_WORDS = (
    "special", "tatsu", "shoryu", "donkey",
    "spinning bird", "lightning legs", "tensho", "hazanshu",
    "shadow blade", "vector drain", "uppercut", "dive kick",
    "command grab",
)


def _compact_label_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _clean_label(value).casefold())


def _label_section_for(value: Any) -> str:
    """Classify a label for display only; matching continues to use the raw label."""
    label = _clean_label(value)
    compact = _compact_label_key(label)
    if compact in _NORMAL_LABEL_ORDER:
        return "normal"

    low = label.casefold()
    if any(word in low for word in _SUPER_WORDS):
        return "super"
    if any(word in low for word in _PROJECTILE_WORDS):
        return "projectile"
    if any(word in low for word in _SPECIAL_WORDS):
        return "special"
    return "other"


def _label_section_header(section: str) -> str:
    return f"──── {_LABEL_SECTION_TITLES[section]} ────"


def _is_label_section_header(value: Any) -> bool:
    return str(value or "") in _LABEL_SECTION_HEADERS


def _normal_label_sort_key(label: str) -> tuple:
    compact = _compact_label_key(label)
    return (_NORMAL_LABEL_ORDER.get(compact, 99), label.casefold(), label)


def _ordered_label_choices(labels: list[str]) -> list[str]:
    """Build the trainer dropdown in Frame Data category order.

    Any label stays at the top.  Real labels are grouped under headings in the
    requested order: normals, specials, supers, projectiles, then all unknown
    or system rows at the end.
    """
    buckets: dict[str, list[str]] = {section: [] for section in _LABEL_SECTION_ORDER}
    seen: set[str] = set()
    for raw in labels or []:
        label = _clean_label(raw)
        key = label.casefold()
        if not label or key in seen:
            continue
        seen.add(key)
        buckets[_label_section_for(label)].append(label)

    buckets["normal"].sort(key=_normal_label_sort_key)
    for section in ("special", "super", "projectile", "other"):
        buckets[section].sort(key=lambda label: (label.casefold(), label))

    options = ["Any label"]
    for section in _LABEL_SECTION_ORDER:
        rows = buckets[section]
        if rows:
            options.append(_label_section_header(section))
            options.extend(rows)
    return options


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
    out = _clamp_float(value, _DEFAULT_COOLDOWN_SEC, 0.0, 60.0)
    return f"{out:g}"


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
    chance = _clamp_int(state.get("chance", _DEFAULT_CHANCE), _DEFAULT_CHANCE, 0, 100)
    delay = _clamp_int(state.get("delay_frames", _DEFAULT_DELAY_FRAMES), _DEFAULT_DELAY_FRAMES, 0, 300)
    occurrence = _ordinal(state.get("target_occurrence", _DEFAULT_OCCURRENCE))
    cooldown = _format_seconds(state.get("cooldown_sec", _DEFAULT_COOLDOWN_SEC))
    return (
        f"{'ARMED' if enabled else 'OFF'}  •  "
        f"{_scope_summary(state, scope_display)}  •  "
        f"{_label_summary(state.get('target_label', ''))}  •  "
        f"{occurrence} occurrence  •  {chance}% chance  •  +{delay}f delay  •  {cooldown}s cooldown"
    )


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


def _field(parent: tk.Misc, textvariable: tk.Variable, *, width: int = 9, **spin_kw) -> tk.Spinbox:
    return tk.Spinbox(
        parent,
        textvariable=textvariable,
        width=width,
        bg=_FIELD,
        fg=_TEXT,
        insertbackground=_TEXT,
        buttonbackground=_PANEL_2,
        relief="flat",
        bd=1,
        highlightthickness=1,
        highlightbackground=_BORDER,
        highlightcolor=_ACCENT,
        font=("Segoe UI", 9),
        **spin_kw,
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
        labels: list[str] = []
        seen_labels: set[str] = set()
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
    """Open the unified five-setting Megacrash trainer."""

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
        win.minsize(700, 540)
        apply_titlebar_icon(win, root)
        _configure_combo_style(win)
        try:
            win.attributes("-topmost", True)
            win.after(300, lambda: win.attributes("-topmost", False))
        except Exception:
            pass

        enabled_var = tk.BooleanVar(value=bool(state.get("enabled", False)))
        chance_var = tk.StringVar(value=str(_clamp_int(state.get("chance", _DEFAULT_CHANCE), _DEFAULT_CHANCE, 0, 100)))
        delay_var = tk.StringVar(value=str(_clamp_int(state.get("delay_frames", _DEFAULT_DELAY_FRAMES), _DEFAULT_DELAY_FRAMES, 0, 300)))
        cooldown_var = tk.StringVar(value=_format_seconds(state.get("cooldown_sec", _DEFAULT_COOLDOWN_SEC)))
        scope_var = tk.StringVar(value=_clean_scope(state.get("attacker_scope", _DEFAULT_SCOPE)))
        label_var = tk.StringVar(value=_clean_label(state.get("target_label", _DEFAULT_LABEL)))
        occurrence_var = tk.StringVar(value=str(_clamp_int(state.get("target_occurrence", _DEFAULT_OCCURRENCE), _DEFAULT_OCCURRENCE, 1, 99)))
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
            "One route only: when the selected occurrence lands, the trainer rolls the chance; a success forces Megacrash after the selected delay. Each crash then observes the selected cooldown. Arm it, then set the five items below.",
            muted=True,
        ).pack(fill="x", pady=(4, 12))

        settings_card = _card(root_frame, alt=True)
        settings_card.pack(fill="x", pady=(0, 10))
        settings = settings_card.inner  # type: ignore[attr-defined]
        _label(settings, "1. Character and label", bold=True, size=10).grid(row=0, column=0, sticky="w", columnspan=4)
        _label(settings, "Character", muted=True).grid(row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 6))

        scope_choices = ["Any active point"] + [entry["display"] for entry in roster]
        display_to_scope = {"Any active point": _SCOPE_ANY}
        display_to_scope.update({entry["display"]: entry["scope"] for entry in roster})
        scope_to_display = {value: key for key, value in display_to_scope.items()}
        scope_display_var = tk.StringVar(value=scope_to_display.get(scope_var.get(), "Any active point"))
        scope_combo = ttk.Combobox(settings, textvariable=scope_display_var, values=scope_choices, state="readonly", style="Megacrash.TCombobox", width=28)
        scope_combo.grid(row=1, column=1, sticky="ew", pady=(8, 6))

        _label(settings, "Label", muted=True).grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(0, 10))
        label_display_var = tk.StringVar(value="Any label")
        label_combo = ttk.Combobox(settings, textvariable=label_display_var, values=["Any label"], state="readonly", style="Megacrash.TCombobox", width=42)
        label_combo.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(0, 10))
        refresh_btn = _button(settings, "Refresh roster", width=12)
        refresh_btn.grid(row=2, column=3, sticky="e", padx=(8, 0), pady=(0, 10))

        _label(settings, "2. Occurrence", bold=True, size=10).grid(row=3, column=0, sticky="w", padx=(0, 8), pady=(4, 5))
        occurrence_row = tk.Frame(settings, bg=_PANEL_ALT)
        occurrence_row.grid(row=3, column=1, sticky="w", pady=(4, 5))
        occurrence_spin = _field(occurrence_row, occurrence_var, width=5, from_=1, to=99, increment=1)
        occurrence_spin.pack(side="left")
        occurrence_desc = _label(occurrence_row, "matching hit of the selected label in this combo", muted=True)
        occurrence_desc.pack(side="left", padx=(8, 0))

        _label(settings, "3. Chance", bold=True, size=10).grid(row=4, column=0, sticky="w", padx=(0, 8), pady=(5, 5))
        chance_row = tk.Frame(settings, bg=_PANEL_ALT)
        chance_row.grid(row=4, column=1, sticky="w", pady=(5, 5))
        chance_spin = _field(chance_row, chance_var, width=7, from_=0, to=100, increment=1)
        chance_spin.pack(side="left")
        _label(chance_row, "% chance the crash occurs", muted=True).pack(side="left", padx=(8, 0))

        _label(settings, "4. Crash delay", bold=True, size=10).grid(row=5, column=0, sticky="w", padx=(0, 8), pady=(5, 0))
        delay_row = tk.Frame(settings, bg=_PANEL_ALT)
        delay_row.grid(row=5, column=1, sticky="w", pady=(5, 0))
        delay_spin = _field(delay_row, delay_var, width=7, from_=0, to=300, increment=1)
        delay_spin.pack(side="left")
        _label(delay_row, "frames after a successful roll", muted=True).pack(side="left", padx=(8, 0))

        _label(settings, "5. Cooldown", bold=True, size=10).grid(row=6, column=0, sticky="w", padx=(0, 8), pady=(5, 0))
        cooldown_row = tk.Frame(settings, bg=_PANEL_ALT)
        cooldown_row.grid(row=6, column=1, sticky="w", pady=(5, 0))
        cooldown_spin = _field(cooldown_row, cooldown_var, width=7, from_=0.0, to=60.0, increment=0.25)
        cooldown_spin.pack(side="left")
        _label(cooldown_row, "seconds after each forced crash", muted=True).pack(side="left", padx=(8, 0))

        settings.grid_columnconfigure(1, weight=1)
        settings.grid_columnconfigure(2, weight=1)

        live_card = _card(root_frame)
        live_card.pack(fill="x", pady=(0, 10))
        live = live_card.inner  # type: ignore[attr-defined]
        current_lbl = _label(live, "", muted=True)
        current_lbl.configure(textvariable=target_status_var)
        current_lbl.pack(fill="x")
        match_lbl = _label(live, "", muted=True, color=_GOOD)
        match_lbl.configure(textvariable=match_status_var)
        match_lbl.pack(fill="x", pady=(4, 0))

        status = _label(root_frame, "", muted=True)
        status.configure(textvariable=status_var)
        status.pack(fill="x", pady=(0, 10))

        def _raw_labels_for_scope(scope: str) -> list[str]:
            if scope == _SCOPE_ANY:
                return [label for entry in roster for label in entry.get("labels", [])]
            entry = scope_to_entry.get(scope) or {}
            return list(entry.get("labels") or [])

        def _label_options_for_scope(scope: str) -> list[str]:
            return _ordered_label_choices(_raw_labels_for_scope(scope))

        def _refresh_label_choices(*, preserve: bool = True) -> None:
            scope = _clean_scope(scope_var.get())
            options = _label_options_for_scope(scope)
            current = _clean_label(label_var.get()) if preserve else ""
            try:
                label_combo.configure(values=options)
            except Exception:
                pass
            if current and current in options and not _is_label_section_header(current):
                label_display_var.set(current)
            else:
                label_display_var.set("Any label")
                if not preserve:
                    label_var.set("")

        def _refresh_target_copy() -> None:
            scope = _clean_scope(scope_var.get())
            label = _clean_label(label_var.get())
            occurrence = _ordinal(occurrence_var.get())
            target_status_var.set(
                f"Route: {_scope_summary({'attacker_scope': scope}, scope_display)}  •  "
                f"{_label_summary(label)}  •  {occurrence} matching hit in combo"
            )
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
                state.setdefault("victim_reaction_latches", {}).clear()
                state.setdefault("opening_counter_acks", {}).clear()
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
                _clean_scope(state.get("attacker_scope", _SCOPE_ANY)),
                _clean_label(state.get("target_label", "")),
                _clamp_int(state.get("target_occurrence", _DEFAULT_OCCURRENCE), _DEFAULT_OCCURRENCE, 1, 99),
                _clamp_int(state.get("chance", _DEFAULT_CHANCE), _DEFAULT_CHANCE, 0, 100),
                _clamp_int(state.get("delay_frames", _DEFAULT_DELAY_FRAMES), _DEFAULT_DELAY_FRAMES, 0, 300),
                _clamp_float(state.get("cooldown_sec", _DEFAULT_COOLDOWN_SEC), _DEFAULT_COOLDOWN_SEC, 0.0, 60.0),
            )
            new_scope = _clean_scope(scope_var.get())
            selected_label = label_display_var.get()
            new_label = "" if selected_label == "Any label" or _is_label_section_header(selected_label) else _clean_label(selected_label)
            state["enabled"] = bool(enabled_var.get())
            # Compatibility fields are intentionally normalized, not exposed.
            state["mode"] = "combined"
            state["cooldown_sec"] = _clamp_float(cooldown_var.get(), _DEFAULT_COOLDOWN_SEC, 0.0, 60.0)
            state["chance"] = _clamp_int(chance_var.get(), _DEFAULT_CHANCE, 0, 100)
            state["delay_frames"] = _clamp_int(delay_var.get(), _DEFAULT_DELAY_FRAMES, 0, 300)
            state["attacker_scope"] = new_scope
            state["target_label"] = new_label
            state["target_occurrence"] = _clamp_int(occurrence_var.get(), _DEFAULT_OCCURRENCE, 1, 99)
            chance_var.set(str(state["chance"]))
            delay_var.set(str(state["delay_frames"]))
            cooldown_var.set(_format_seconds(state["cooldown_sec"]))
            occurrence_var.set(str(state["target_occurrence"]))
            label_var.set(new_label)

            new_sig = (
                bool(state.get("enabled", False)),
                state["attacker_scope"], state["target_label"], state["target_occurrence"],
                state["chance"], state["delay_frames"], state["cooldown_sec"],
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
                    bg="#176B4C", fg="#F3FFF9", activebackground="#1D805C",
                    activeforeground="#FFFFFF", highlightbackground="#53C992",
                )
            else:
                enabled_btn.configure(
                    text="○  MEGACRASH OFF\n    CLICK TO ARM",
                    bg="#2B3443", fg="#D7E1EE", activebackground="#3A495C",
                    activeforeground="#FFFFFF", highlightbackground="#5A6B80",
                )

        def _toggle_enabled() -> None:
            enabled_var.set(not bool(enabled_var.get()))
            _apply(reset_counts=True)

        def _reset_to_defaults() -> None:
            """Restore all five trainer settings immediately, preserving armed state."""
            scope_var.set(_DEFAULT_SCOPE)
            scope_display_var.set("Any active point")
            label_var.set(_DEFAULT_LABEL)
            label_display_var.set("Any label")
            occurrence_var.set(str(_DEFAULT_OCCURRENCE))
            chance_var.set(str(_DEFAULT_CHANCE))
            delay_var.set(str(_DEFAULT_DELAY_FRAMES))
            cooldown_var.set(_format_seconds(_DEFAULT_COOLDOWN_SEC))
            _refresh_label_choices(preserve=False)
            _apply(reset_counts=True)
            status_var.set("Defaults restored: Any active point • Any label • 1st occurrence • 100% • +5f • 3s cooldown.")

        def _on_scope_selected(_evt=None) -> None:
            scope_var.set(display_to_scope.get(scope_display_var.get(), _SCOPE_ANY))
            _refresh_label_choices(preserve=False)
            _apply(reset_counts=True)

        def _on_label_selected(_evt=None) -> None:
            # Native ttk comboboxes cannot make individual rows disabled.
            # Treat category separators as display-only and snap back to the
            # current real label if one is clicked or reached by keyboard.
            if _is_label_section_header(label_display_var.get()):
                label_display_var.set(_label_summary(label_var.get()))
                return
            _apply(reset_counts=True)

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
            status_var.set("Roster and labels refreshed from the live character slots.")

        refresh_btn.configure(command=_refresh_roster)
        scope_combo.bind("<<ComboboxSelected>>", _on_scope_selected)
        label_combo.bind("<<ComboboxSelected>>", _on_label_selected)
        enabled_btn.configure(command=_toggle_enabled)
        for widget in (occurrence_spin, chance_spin, delay_spin, cooldown_spin):
            widget.configure(command=lambda: _apply(reset_counts=True))
            widget.bind("<Return>", lambda _e: _apply(reset_counts=True))
            widget.bind("<FocusOut>", lambda _e: _apply(reset_counts=True))
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
        _button(buttons, "Reset defaults", _reset_to_defaults, width=14).pack(side="left", padx=(8, 0))
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
            req_h = max(540, int(win.winfo_reqheight()) + 12)
            screen_h = int(win.winfo_screenheight())
            win.geometry(f"{req_w}x{min(req_h, max(540, screen_h - 70))}")
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
