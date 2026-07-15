from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

try:
    from tvcgui.core.tk_host import tk_call
except Exception:
    tk_call = None

try:
    from tvcgui.features.frame_data.widgets import apply_titlebar_icon
except Exception:
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
_FIELD = '#111827'
_GOOD = '#7CE0B5'


def _clean(value: Any) -> str:
    return ' '.join(str(value or '').replace('\r', ' ').replace('\n', ' ').split())[:128]


def _label(parent: tk.Misc, text: str, *, muted: bool = False, bold: bool = False, size: int = 9, color: str | None = None) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        bg=parent.cget('bg'),
        fg=color or (_MUTED if muted else _TEXT),
        font=('Segoe UI', size, 'bold' if bold else 'normal'),
        anchor='w',
        justify='left',
    )


def _button(parent: tk.Misc, text: str, command: Callable[[], None] | None = None, *, width: int | None = None, primary: bool = False) -> tk.Button:
    return tk.Button(
        parent,
        text=text,
        command=command,
        width=width or 0,
        bg='#2C5E91' if primary else _PANEL_2,
        fg='#FFFFFF' if primary else _TEXT,
        activebackground='#3C73AB' if primary else '#315A87',
        activeforeground='#FFFFFF',
        relief='flat',
        bd=0,
        padx=10,
        pady=6,
        font=('Segoe UI', 9, 'bold' if primary else 'normal'),
        highlightthickness=1,
        highlightbackground='#6094C7' if primary else _BORDER,
        highlightcolor=_ACCENT,
        cursor='hand2',
    )


def _card(parent: tk.Misc, *, alt: bool = False) -> tk.Frame:
    outer = tk.Frame(parent, bg=_BORDER, padx=1, pady=1)
    inner = tk.Frame(outer, bg=_PANEL_ALT if alt else _PANEL, padx=13, pady=11)
    inner.pack(fill='both', expand=True)
    outer.inner = inner  # type: ignore[attr-defined]
    return outer


def _configure_combo_style(win: tk.Misc) -> None:
    try:
        style = ttk.Style(win)
        style.theme_use('clam')
        style.configure(
            'Punish.TCombobox',
            fieldbackground=_FIELD,
            background=_PANEL_2,
            foreground=_TEXT,
            arrowcolor=_TEXT,
            bordercolor=_BORDER,
            lightcolor=_BORDER,
            darkcolor=_BORDER,
            padding=5,
            font=('Segoe UI', 9),
        )
        style.map(
            'Punish.TCombobox',
            fieldbackground=[('readonly', _FIELD)],
            foreground=[('readonly', _TEXT)],
            selectbackground=[('readonly', '#315A87')],
            selectforeground=[('readonly', _TEXT)],
        )
    except Exception:
        pass


def _clamp_cooldown(value: Any) -> float:
    try:
        value = float(value)
    except Exception:
        value = 3.0
    return round(max(0.25, min(30.0, value)), 2)


def open_punish_training_window(
    state: dict,
    save_func: Callable[[dict], None] | None = None,
    roster_provider: Callable[[], dict] | None = None,
) -> None:
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

        win = tk.Toplevel(tk_root) if tk_root is not None else tk.Toplevel()
        _OPEN_WINDOW = win
        win.title('Punish Trainer')
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
        _label(h, 'PUNISH TRAINER', bold=True, size=12, color=_ACCENT).pack(anchor='w')
        _label(
            h,
            'Choose a fighter slot and move, then use a fixed cooldown or a fresh random interval for every repetition.',
            muted=True,
        ).pack(anchor='w', pady=(4, 0))

        slot_var = tk.StringVar(value=str(state.get('target_slot') or 'P2-C1'))
        move_var = tk.StringVar(value=_clean(state.get('move_label')) or 'Choose move')
        cooldown_var = tk.StringVar(value=f"{_clamp_cooldown(state.get('cooldown_sec')):g}")
        random_interval_var = tk.BooleanVar(value=bool(state.get('random_interval', False)))
        random_min_var = tk.StringVar(value=f"{_clamp_cooldown(state.get('random_min_sec') or 5.0):g}")
        random_max_var = tk.StringVar(value=f"{_clamp_cooldown(state.get('random_max_sec') or 10.0):g}")
        countdown_var = tk.BooleanVar(value=bool(state.get('show_countdown', False)))
        enabled_var = tk.BooleanVar(value=bool(state.get('enabled', False)))
        live_var = tk.StringVar(value='')
        status_var = tk.StringVar(value=_clean(state.get('last_status')))

        settings_card = _card(root)
        settings_card.pack(fill='x', pady=(0, 10))
        settings = settings_card.inner  # type: ignore[attr-defined]
        settings.grid_columnconfigure(1, weight=1)

        _label(settings, 'Fighter slot', bold=True, size=10).grid(row=0, column=0, sticky='w', padx=(0, 10), pady=(0, 9))
        slot_combo = ttk.Combobox(
            settings,
            textvariable=slot_var,
            values=['P1-C1', 'P1-C2', 'P2-C1', 'P2-C2'],
            state='readonly',
            style='Punish.TCombobox',
            width=16,
        )
        slot_combo.grid(row=0, column=1, sticky='ew', pady=(0, 9))

        _label(settings, 'Move', bold=True, size=10).grid(row=1, column=0, sticky='w', padx=(0, 10), pady=(0, 9))
        move_combo = ttk.Combobox(
            settings,
            textvariable=move_var,
            values=['Choose move'],
            state='readonly',
            style='Punish.TCombobox',
            width=46,
        )
        move_combo.grid(row=1, column=1, sticky='ew', pady=(0, 9))

        _label(settings, 'Fixed cooldown', bold=True, size=10).grid(row=2, column=0, sticky='w', padx=(0, 10), pady=(0, 9))
        cooldown_row = tk.Frame(settings, bg=_PANEL)
        cooldown_row.grid(row=2, column=1, sticky='w', pady=(0, 9))
        cooldown_spin = tk.Spinbox(
            cooldown_row,
            textvariable=cooldown_var,
            width=7,
            from_=0.25,
            to=30.0,
            increment=0.25,
            bg=_FIELD,
            fg=_TEXT,
            disabledbackground='#202838',
            disabledforeground='#748297',
            insertbackground=_TEXT,
            buttonbackground=_PANEL_2,
            relief='flat',
            bd=1,
            highlightthickness=1,
            highlightbackground=_BORDER,
            highlightcolor=_ACCENT,
            font=('Segoe UI', 9),
        )
        cooldown_spin.pack(side='left')
        _label(cooldown_row, 'seconds after neutral', muted=True).pack(side='left', padx=(8, 0))

        random_check = tk.Checkbutton(
            settings,
            text='Use a random interval range',
            variable=random_interval_var,
            bg=_PANEL,
            fg=_TEXT,
            activebackground=_PANEL,
            activeforeground=_TEXT,
            selectcolor=_FIELD,
            font=('Segoe UI', 9, 'bold'),
            anchor='w',
            cursor='hand2',
        )
        random_check.grid(row=3, column=1, sticky='w', pady=(0, 9))

        _label(settings, 'Random range', bold=True, size=10).grid(row=4, column=0, sticky='w', padx=(0, 10), pady=(0, 9))
        range_row = tk.Frame(settings, bg=_PANEL)
        range_row.grid(row=4, column=1, sticky='w', pady=(0, 9))
        random_min_spin = tk.Spinbox(
            range_row,
            textvariable=random_min_var,
            width=7,
            from_=0.25,
            to=30.0,
            increment=0.25,
            bg=_FIELD,
            fg=_TEXT,
            disabledbackground='#202838',
            disabledforeground='#748297',
            insertbackground=_TEXT,
            buttonbackground=_PANEL_2,
            relief='flat',
            bd=1,
            highlightthickness=1,
            highlightbackground=_BORDER,
            highlightcolor=_ACCENT,
            font=('Segoe UI', 9),
        )
        random_min_spin.pack(side='left')
        _label(range_row, 'to', muted=True).pack(side='left', padx=7)
        random_max_spin = tk.Spinbox(
            range_row,
            textvariable=random_max_var,
            width=7,
            from_=0.25,
            to=30.0,
            increment=0.25,
            bg=_FIELD,
            fg=_TEXT,
            disabledbackground='#202838',
            disabledforeground='#748297',
            insertbackground=_TEXT,
            buttonbackground=_PANEL_2,
            relief='flat',
            bd=1,
            highlightthickness=1,
            highlightbackground=_BORDER,
            highlightcolor=_ACCENT,
            font=('Segoe UI', 9),
        )
        random_max_spin.pack(side='left')
        _label(range_row, 'seconds', muted=True).pack(side='left', padx=(8, 0))

        countdown_check = tk.Checkbutton(
            settings,
            text='Show an on-screen countdown before the move',
            variable=countdown_var,
            bg=_PANEL,
            fg=_TEXT,
            activebackground=_PANEL,
            activeforeground=_TEXT,
            selectcolor=_FIELD,
            font=('Segoe UI', 9, 'bold'),
            anchor='w',
            cursor='hand2',
        )
        countdown_check.grid(row=5, column=1, sticky='w')

        live_card = _card(root, alt=True)
        live_card.pack(fill='x', pady=(0, 10))
        live = live_card.inner  # type: ignore[attr-defined]
        _label(live, 'LIVE TARGET', bold=True, size=9, color=_GOOD).pack(anchor='w')
        _label(live, '', muted=False).pack_forget()
        live_text = _label(live, '', muted=True)
        live_text.pack(anchor='w', pady=(4, 0))

        controls = tk.Frame(root, bg=_BG)
        controls.pack(fill='x')
        arm_btn = _button(controls, '', primary=True)
        arm_btn.pack(side='left', fill='x', expand=True)
        test_btn = _button(controls, 'Perform Once', width=14)
        test_btn.pack(side='left', padx=(8, 0))
        reset_btn = _button(controls, 'Reset', width=8)
        reset_btn.pack(side='left', padx=(8, 0))

        status = _label(root, '', muted=True)
        status.pack(fill='x', pady=(9, 0))

        roster: dict[str, dict[str, Any]] = {}
        move_lookup: dict[str, int] = {}

        def _read_roster() -> dict:
            if roster_provider is None:
                return {}
            try:
                data = roster_provider()
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

        def _selected_entry() -> dict[str, Any]:
            return roster.get(slot_var.get()) or {}

        def _refresh_arm_button() -> None:
            if bool(enabled_var.get()):
                arm_btn.configure(
                    text='●  TRAINER ARMED\n    CLICK TO DISABLE',
                    bg='#176B4C',
                    fg='#F3FFF9',
                    activebackground='#1D805C',
                    highlightbackground='#53C992',
                )
            else:
                arm_btn.configure(
                    text='○  TRAINER OFF\n    CLICK TO ARM',
                    bg='#2C5E91',
                    fg='#FFFFFF',
                    activebackground='#3C73AB',
                    highlightbackground='#6094C7',
                )

        def _refresh_timing_controls() -> None:
            random_on = bool(random_interval_var.get())
            try:
                cooldown_spin.configure(state='disabled' if random_on else 'normal')
                random_min_spin.configure(state='normal' if random_on else 'disabled')
                random_max_spin.configure(state='normal' if random_on else 'disabled')
            except Exception:
                pass

        def _refresh_live() -> None:
            entry = _selected_entry()
            slot = slot_var.get()
            name = _clean(entry.get('name')) or 'Waiting'
            base = int(entry.get('base') or 0)
            base_text = f'0x{base:08X}' if base else 'not live'
            live_var.set(f'{slot}  |  {name}  |  {base_text}')
            live_text.configure(text=live_var.get())

        def _refresh_moves(*, preserve: bool = True) -> None:
            nonlocal move_lookup
            entry = _selected_entry()
            options = ['Choose move']
            move_lookup = {}
            for item in entry.get('moves') or []:
                if not isinstance(item, dict):
                    continue
                display = _clean(item.get('display'))
                try:
                    action_id = int(item.get('action_id')) & 0xFFFF
                except Exception:
                    continue
                if not display:
                    continue
                options.append(display)
                move_lookup[display] = action_id
            move_combo.configure(values=options)
            current = move_var.get()
            if not preserve or current not in options:
                move_var.set('Choose move')
            _refresh_live()

        def _apply(*, reset_runtime: bool = True) -> None:
            selected = move_var.get()
            state['enabled'] = bool(enabled_var.get())
            state['target_slot'] = slot_var.get()
            state['move_label'] = '' if selected == 'Choose move' else selected
            state['action_id'] = int(move_lookup.get(selected) or 0)
            state['cooldown_sec'] = _clamp_cooldown(cooldown_var.get())
            random_min = _clamp_cooldown(random_min_var.get())
            random_max = _clamp_cooldown(random_max_var.get())
            if random_min > random_max:
                random_min, random_max = random_max, random_min
            state['random_interval'] = bool(random_interval_var.get())
            state['random_min_sec'] = random_min
            state['random_max_sec'] = random_max
            state['show_countdown'] = bool(countdown_var.get())
            cooldown_var.set(f"{state['cooldown_sec']:g}")
            random_min_var.set(f"{random_min:g}")
            random_max_var.set(f"{random_max:g}")
            if reset_runtime:
                state['phase'] = 'off'
                state['next_fire_at'] = 0.0
                state['request_attempts'] = 0
            if save_func is not None:
                save_func(state)
            _refresh_arm_button()
            _refresh_timing_controls()
            _refresh_live()

        def _toggle() -> None:
            if not enabled_var.get() and move_var.get() == 'Choose move':
                status_var.set('Choose a move before arming the trainer.')
                status.configure(text=status_var.get())
                return
            enabled_var.set(not bool(enabled_var.get()))
            _apply()

        def _manual_test() -> None:
            if move_var.get() == 'Choose move':
                status_var.set('Choose a move first.')
                status.configure(text=status_var.get())
                return
            _apply(reset_runtime=False)
            state['manual_test_requested'] = True
            status_var.set('One move queued. Return the selected fighter to neutral.')
            status.configure(text=status_var.get())

        def _reset() -> None:
            enabled_var.set(False)
            slot_var.set('P2-C1')
            move_var.set('Choose move')
            cooldown_var.set('3')
            random_interval_var.set(False)
            random_min_var.set('5')
            random_max_var.set('10')
            countdown_var.set(False)
            _refresh_timing_controls()
            _refresh_moves(preserve=False)
            _apply()
            status_var.set('Defaults restored.')
            status.configure(text=status_var.get())

        def _slot_changed(_event=None) -> None:
            nonlocal roster
            roster = _read_roster()
            _refresh_moves(preserve=False)
            _apply()

        def _move_changed(_event=None) -> None:
            _apply()

        def _timing_mode_changed() -> None:
            _refresh_timing_controls()
            _apply()

        def _countdown_changed() -> None:
            _apply(reset_runtime=False)

        arm_btn.configure(command=_toggle)
        test_btn.configure(command=_manual_test)
        reset_btn.configure(command=_reset)
        random_check.configure(command=_timing_mode_changed)
        countdown_check.configure(command=_countdown_changed)
        slot_combo.bind('<<ComboboxSelected>>', _slot_changed)
        move_combo.bind('<<ComboboxSelected>>', _move_changed)
        cooldown_spin.bind('<FocusOut>', lambda _event: _apply())
        cooldown_spin.bind('<Return>', lambda _event: _apply())
        random_min_spin.bind('<FocusOut>', lambda _event: _apply())
        random_min_spin.bind('<Return>', lambda _event: _apply())
        random_max_spin.bind('<FocusOut>', lambda _event: _apply())
        random_max_spin.bind('<Return>', lambda _event: _apply())

        roster = _read_roster()
        _refresh_moves(preserve=True)
        _refresh_timing_controls()
        _refresh_arm_button()
        _refresh_live()
        status.configure(text=status_var.get())

        def _poll() -> None:
            nonlocal roster
            try:
                if not win.winfo_exists():
                    return
                roster = _read_roster()
                _refresh_moves(preserve=True)
                runtime_status = _clean(state.get('last_status'))
                if runtime_status:
                    status.configure(text=runtime_status)
                enabled_var.set(bool(state.get('enabled', False)))
                _refresh_arm_button()
                win.after(200, _poll)
            except Exception:
                return

        win.after(200, _poll)
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
