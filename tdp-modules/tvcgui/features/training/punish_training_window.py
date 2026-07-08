from __future__ import annotations

import tkinter as tk
import time
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

_BG = '#0D1018'
_PANEL = '#171D2A'
_PANEL_ALT = '#1B2639'
_PANEL_2 = '#243550'
_BORDER = '#344963'
_TEXT = '#ECF4FF'
_MUTED = '#A9B8CC'
_ACCENT = '#75B8FF'
_ACTIVE = '#315A87'
_FIELD = '#111827'
_GOOD = '#7CE0B5'

_NORMAL_ORDER = {
    '5a': 0, '2a': 1, '5b': 2, '2b': 3, '6b': 4,
    '5c': 5, '2c': 6, '6c': 7, '4c': 8, '3c': 9,
    'ja': 10, 'jb': 11, 'jc': 12,
}


def _clean(value: Any) -> str:
    text = str(value or '').replace('\r', ' ').replace('\n', ' ').strip()
    while '  ' in text:
        text = text.replace('  ', ' ')
    return text[:96]


def _compact(value: Any) -> str:
    return ''.join(ch for ch in _clean(value).casefold() if ch.isalnum())


def _label(parent: tk.Misc, text: str, *, muted: bool = False, bold: bool = False, size: int = 9, color: str | None = None) -> tk.Label:
    return tk.Label(
        parent, text=text, bg=parent.cget('bg'), fg=color or (_MUTED if muted else _TEXT),
        font=('Segoe UI', size, 'bold' if bold else 'normal'), anchor='w', justify='left',
    )


def _button(parent: tk.Misc, text: str, command: Callable[[], None] | None = None, *, width: int | None = None, primary: bool = False) -> tk.Button:
    return tk.Button(
        parent, text=text, command=command, width=width or 0,
        bg='#2C5E91' if primary else _PANEL_2,
        fg='#FFFFFF' if primary else _TEXT,
        activebackground='#3C73AB' if primary else _ACTIVE,
        activeforeground='#FFFFFF', relief='flat', bd=0, padx=10, pady=5,
        font=('Segoe UI', 9, 'bold' if primary else 'normal'),
        highlightthickness=1, highlightbackground='#6094C7' if primary else _BORDER,
        highlightcolor=_ACCENT, cursor='hand2',
    )


def _card(parent: tk.Misc, *, alt: bool = False) -> tk.Frame:
    outer = tk.Frame(parent, bg=_BORDER, padx=1, pady=1)
    inner = tk.Frame(outer, bg=_PANEL_ALT if alt else _PANEL, padx=13, pady=11)
    inner.pack(fill='both', expand=True)
    outer.inner = inner  # type: ignore[attr-defined]
    return outer


def _spin(parent: tk.Misc, variable: tk.Variable, *, width: int, **kwargs) -> tk.Spinbox:
    return tk.Spinbox(
        parent, textvariable=variable, width=width, bg=_FIELD, fg=_TEXT,
        insertbackground=_TEXT, buttonbackground=_PANEL_2, relief='flat', bd=1,
        highlightthickness=1, highlightbackground=_BORDER, highlightcolor=_ACCENT,
        font=('Segoe UI', 9), **kwargs,
    )


def _configure_combo_style(win: tk.Misc) -> None:
    try:
        style = ttk.Style(win)
        style.theme_use('clam')
        style.configure(
            'Punish.TCombobox', fieldbackground=_FIELD, background=_PANEL_2,
            foreground=_TEXT, arrowcolor=_TEXT, bordercolor=_BORDER,
            lightcolor=_BORDER, darkcolor=_BORDER, padding=4, font=('Segoe UI', 9),
        )
        style.map(
            'Punish.TCombobox', fieldbackground=[('readonly', _FIELD)],
            foreground=[('readonly', _TEXT)], selectbackground=[('readonly', _ACTIVE)],
            selectforeground=[('readonly', _TEXT)],
        )
    except Exception:
        pass


def _ordered_labels(labels: list[str], *, include_blank: bool) -> list[str]:
    seen: set[str] = set()
    clean = []
    for raw in labels or []:
        label = _clean(raw)
        if label and label.casefold() not in seen:
            seen.add(label.casefold())
            clean.append(label)
    clean.sort(key=lambda label: (_NORMAL_ORDER.get(_compact(label), 99), label.casefold(), label))
    return (['Choose move'] if include_blank else []) + clean


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        value = float(value)
    except Exception:
        value = default
    return round(max(low, min(high, value)), 2)


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        value = int(round(float(value)))
    except Exception:
        value = default
    return max(low, min(high, value))


def open_punish_training_window(state: dict, save_func: Callable[[dict], None] | None = None, roster_provider: Callable[[], dict] | None = None) -> None:
    def _open(tk_root: tk.Misc | None = None) -> None:
        global _OPEN_WINDOW
        if _OPEN_WINDOW is not None:
            try:
                if _OPEN_WINDOW.winfo_exists():
                    _OPEN_WINDOW.deiconify()
                    _OPEN_WINDOW.lift()
                    _OPEN_WINDOW.focus_force()
                    return
            except Exception:
                pass
            _OPEN_WINDOW = None

        # tk_call runs callbacks on the shared Tk root and passes that root
        # into the callback. Use it as the window parent so this works both
        # through the host pump and through the direct fallback below.
        win = tk.Toplevel(tk_root) if tk_root is not None else tk.Toplevel()
        _OPEN_WINDOW = win
        win.title('Punish Training')
        win.configure(bg=_BG)
        win.resizable(False, False)
        try:
            apply_titlebar_icon(win)
        except Exception:
            pass

        def _close() -> None:
            global _OPEN_WINDOW
            _OPEN_WINDOW = None
            try:
                win.destroy()
            except Exception:
                pass

        win.protocol('WM_DELETE_WINDOW', _close)
        _configure_combo_style(win)

        root = tk.Frame(win, bg=_BG, padx=14, pady=14)
        root.pack(fill='both', expand=True)

        header = _card(root, alt=True)
        header.pack(fill='x', pady=(0, 10))
        h = header.inner  # type: ignore[attr-defined]
        _label(h, 'PUNISH TRAINING', bold=True, size=12, color=_ACCENT).pack(anchor='w')
        _label(h, 'Force the active opponent to run a selected move on a timer, or immediately after they leave the blockstun or hitstun caused by your selected move.', muted=True).pack(anchor='w', pady=(4, 0))

        enabled_var = tk.BooleanVar(value=bool(state.get('enabled', False)))
        mode_var = tk.StringVar(value='Repeat every X seconds' if str(state.get('mode') or 'interval') != 'after_stun' else 'After stun release')
        team_var = tk.StringVar(value='P2' if str(state.get('opponent_team') or 'P2').upper() != 'P1' else 'P1')
        response_var = tk.StringVar(value=_clean(state.get('response_label')) or 'Choose move')
        source_var = tk.StringVar(value=_clean(state.get('source_label')) or 'Choose move')
        interval_var = tk.StringVar(value=f"{_clamp_float(state.get('interval_sec'), 3.0, 0.25, 60.0):g}")
        delay_var = tk.StringVar(value=str(_clamp_int(state.get('release_delay_frames'), 0, 0, 300)))
        live_var = tk.StringVar(value='')
        status_var = tk.StringVar(value='')

        settings_card = _card(root)
        settings_card.pack(fill='x', pady=(0, 10))
        settings = settings_card.inner  # type: ignore[attr-defined]
        settings.grid_columnconfigure(1, weight=1)
        settings.grid_columnconfigure(2, weight=1)

        _label(settings, 'Opponent team', bold=True, size=10).grid(row=0, column=0, sticky='w', padx=(0, 8), pady=(0, 8))
        team_combo = ttk.Combobox(settings, textvariable=team_var, values=['P2', 'P1'], state='readonly', style='Punish.TCombobox', width=10)
        team_combo.grid(row=0, column=1, sticky='w', pady=(0, 8))
        _label(settings, 'The active point on this team performs the response.', muted=True).grid(row=0, column=2, sticky='w', padx=(9, 0), pady=(0, 8))

        _label(settings, 'Mode', bold=True, size=10).grid(row=1, column=0, sticky='w', padx=(0, 8), pady=(0, 8))
        mode_combo = ttk.Combobox(settings, textvariable=mode_var, values=['Repeat every X seconds', 'After stun release'], state='readonly', style='Punish.TCombobox', width=25)
        mode_combo.grid(row=1, column=1, columnspan=2, sticky='ew', pady=(0, 8))

        _label(settings, 'Opponent response', bold=True, size=10).grid(row=2, column=0, sticky='w', padx=(0, 8), pady=(0, 8))
        response_combo = ttk.Combobox(settings, textvariable=response_var, values=['Choose move'], state='readonly', style='Punish.TCombobox', width=40)
        response_combo.grid(row=2, column=1, sticky='ew', pady=(0, 8))
        refresh_btn = _button(settings, 'Refresh roster', width=12)
        refresh_btn.grid(row=2, column=2, sticky='e', padx=(8, 0), pady=(0, 8))

        timer_row = tk.Frame(settings, bg=_PANEL)
        _label(timer_row, 'Interval', bold=True, size=10).pack(side='left')
        _spin(timer_row, interval_var, width=7, from_=0.25, to=60.0, increment=0.25).pack(side='left', padx=(10, 7))
        _label(timer_row, 'seconds between opponent responses. The timer waits for a neutral state before forcing the move.', muted=True).pack(side='left')

        release_row = tk.Frame(settings, bg=_PANEL)
        _label(release_row, 'Your source move', bold=True, size=10).grid(row=0, column=0, sticky='w')
        source_combo = ttk.Combobox(release_row, textvariable=source_var, values=['Choose move'], state='readonly', style='Punish.TCombobox', width=34)
        source_combo.grid(row=0, column=1, sticky='ew', padx=(10, 0))
        _label(release_row, 'Release delay', bold=True, size=10).grid(row=1, column=0, sticky='w', pady=(7, 0))
        delay_frame = tk.Frame(release_row, bg=_PANEL)
        delay_frame.grid(row=1, column=1, sticky='w', padx=(10, 0), pady=(7, 0))
        _spin(delay_frame, delay_var, width=7, from_=0, to=300, increment=1).pack(side='left')
        _label(delay_frame, 'frames after the opponent leaves hitstun or blockstun', muted=True).pack(side='left', padx=(7, 0))
        release_row.grid_columnconfigure(1, weight=1)

        controls = _card(root, alt=True)
        controls.pack(fill='x', pady=(0, 10))
        c = controls.inner  # type: ignore[attr-defined]
        enabled_btn = _button(c, '', width=28)
        enabled_btn.pack(side='left')
        test_btn = _button(c, 'Test selected move', width=18)
        test_btn.pack(side='left', padx=(8, 0))
        _button(c, 'Reset settings', width=14).pack(side='right')

        live = _card(root)
        live.pack(fill='x', pady=(0, 8))
        _label(live.inner, '', muted=True, color=_GOOD).configure(textvariable=live_var)
        live.inner.winfo_children()[-1].pack(fill='x')
        current = _label(root, '', muted=True)
        current.configure(textvariable=status_var)
        current.pack(fill='x')

        roster: dict = {}

        def _read_roster() -> dict:
            try:
                raw = roster_provider() if roster_provider is not None else {}
            except Exception as exc:
                status_var.set(f'Roster refresh failed: {exc!r}')
                return {}
            return raw if isinstance(raw, dict) else {}

        def _team_entry(team: str) -> dict:
            entry = roster.get(team) or {}
            return entry if isinstance(entry, dict) else {}

        def _apply(*, clear_runtime: bool = True) -> None:
            old_sig = (
                bool(state.get('enabled', False)), str(state.get('mode') or ''),
                str(state.get('opponent_team') or ''), _clean(state.get('response_label')),
                _clean(state.get('source_label')), str(state.get('interval_sec') or ''),
                str(state.get('release_delay_frames') or ''),
            )
            state['enabled'] = bool(enabled_var.get())
            state['mode'] = 'after_stun' if mode_var.get() == 'After stun release' else 'interval'
            state['opponent_team'] = 'P1' if team_var.get() == 'P1' else 'P2'
            state['response_label'] = '' if response_var.get() == 'Choose move' else _clean(response_var.get())
            state['source_label'] = '' if source_var.get() == 'Choose move' else _clean(source_var.get())
            state['interval_sec'] = _clamp_float(interval_var.get(), 3.0, 0.25, 60.0)
            state['release_delay_frames'] = _clamp_int(delay_var.get(), 0, 0, 300)
            interval_var.set(f"{state['interval_sec']:g}")
            delay_var.set(str(state['release_delay_frames']))
            new_sig = (
                bool(state['enabled']), state['mode'], state['opponent_team'], state['response_label'],
                state['source_label'], state['interval_sec'], state['release_delay_frames'],
            )
            if clear_runtime and old_sig != new_sig:
                state['pulses'] = {}
                state['scheduled'] = None
                state['release_watch'] = {}
                state['next_interval_at'] = 0.0
            if save_func is not None:
                save_func(state)
            _refresh_armed_button()
            _refresh_live_text()

        def _refresh_armed_button() -> None:
            if bool(enabled_var.get()):
                enabled_btn.configure(text='●  PUNISH TRAINER ARMED\n    CLICK TO DISABLE', bg='#176B4C', fg='#F3FFF9', activebackground='#1D805C', highlightbackground='#53C992')
            else:
                enabled_btn.configure(text='○  PUNISH TRAINER OFF\n    CLICK TO ARM', bg='#2B3443', fg='#D7E1EE', activebackground='#3A495C', highlightbackground='#5A6B80')

        def _refresh_live_text() -> None:
            opponent = _team_entry(team_var.get())
            source_team = 'P1' if team_var.get() == 'P2' else 'P2'
            source = _team_entry(source_team)
            opponent_text = f"Opponent: {opponent.get('slot') or 'waiting'}  {opponent.get('name') or 'Unknown'}"
            source_text = f"Source: {source.get('slot') or 'waiting'}  {source.get('name') or 'Unknown'}"
            if mode_var.get() == 'After stun release':
                live_var.set(f'{opponent_text}  |  {source_text}  |  responds when stun ends')
            else:
                live_var.set(f'{opponent_text}  |  {source_text}  |  repeats when neutral')
            response = _clean(state.get('response_label')) or 'Choose response'
            if mode_var.get() == 'After stun release':
                origin = _clean(state.get('source_label')) or 'Choose source'
                status_var.set(f"{'ARMED' if state.get('enabled') else 'OFF'}  |  {response} after {origin} leaves stun, +{state.get('release_delay_frames', 0)}f")
            else:
                status_var.set(f"{'ARMED' if state.get('enabled') else 'OFF'}  |  {response} every {state.get('interval_sec', 3)}s")

        def _refresh_choices(*, preserve: bool = True) -> None:
            opponent = _team_entry(team_var.get())
            source_team = 'P1' if team_var.get() == 'P2' else 'P2'
            source = _team_entry(source_team)
            response_options = _ordered_labels(list(opponent.get('labels') or []), include_blank=True)
            source_options = _ordered_labels(list(source.get('labels') or []), include_blank=True)
            response_combo.configure(values=response_options)
            source_combo.configure(values=source_options)
            if response_var.get() not in response_options:
                response_var.set('Choose move')
            if source_var.get() not in source_options:
                source_var.set('Choose move')
            _refresh_live_text()

        def _refresh_roster(*_args) -> None:
            nonlocal roster
            roster = _read_roster()
            _refresh_choices()
            _apply(clear_runtime=False)

        def _toggle() -> None:
            enabled_var.set(not bool(enabled_var.get()))
            _apply()

        def _manual_test() -> None:
            # Persist the visible selections before asking the runtime to bypass
            # the timer and issue one direct native-input attempt.
            if not bool(enabled_var.get()):
                enabled_var.set(True)
            _apply()
            state['manual_test_requested'] = True
            state['manual_test_requested_at'] = time.time()
            status_var.set('Manual test queued. Check the console for the one-line route result.')

        def _reset() -> None:
            mode_var.set('Repeat every X seconds')
            team_var.set('P2')
            response_var.set('Choose move')
            source_var.set('Choose move')
            interval_var.set('3')
            delay_var.set('0')
            _refresh_choices(preserve=False)
            _apply()
            status_var.set('Defaults restored. The trainer remains in its current armed or off state.')

        def _show_mode(*_args) -> None:
            if mode_var.get() == 'After stun release':
                timer_row.grid_forget()
                release_row.grid(row=3, column=0, columnspan=3, sticky='ew', pady=(2, 2))
            else:
                release_row.grid_forget()
                timer_row.grid(row=3, column=0, columnspan=3, sticky='w', pady=(2, 2))
            _apply()

        enabled_btn.configure(command=_toggle)
        test_btn.configure(command=_manual_test)
        c.winfo_children()[-1].configure(command=_reset)
        refresh_btn.configure(command=_refresh_roster)
        team_combo.bind('<<ComboboxSelected>>', _refresh_roster)
        mode_combo.bind('<<ComboboxSelected>>', _show_mode)
        # Tk passes an Event object to Combobox bindings. _apply uses a
        # keyword-only signature, so route the event through tiny wrappers.
        # Passing _apply directly silently leaves the response/source label
        # unchanged after the user picks it, which arms an empty trainer.
        response_combo.bind('<<ComboboxSelected>>', lambda _evt: _apply())
        source_combo.bind('<<ComboboxSelected>>', lambda _evt: _apply())

        roster = _read_roster()
        _refresh_choices()
        _show_mode()
        _refresh_armed_button()
        _refresh_live_text()

        def _poll_runtime_status() -> None:
            try:
                if not win.winfo_exists():
                    return
                runtime_status = _clean(state.get('last_status'))
                if runtime_status:
                    status_var.set(runtime_status)
                win.after(200, _poll_runtime_status)
            except Exception:
                return

        win.after(200, _poll_runtime_status)
        win.transient(None)
        win.lift()
        win.focus_force()

    if tk_call is not None:
        try:
            tk_call(_open)
            return
        except Exception:
            pass
    _open()


__all__ = ['open_punish_training_window']
