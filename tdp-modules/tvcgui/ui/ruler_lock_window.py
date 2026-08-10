from __future__ import annotations

import json
import os
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable

from tvcgui.core.paths import data_path, user_data_path
from tvcgui.core.tk_host import tk_call
from tvcgui.core.constants import CHAR_NAMES

_WIN: tk.Toplevel | None = None

_SLOT_LABELS = {
    "P1": "P1-C1",
    "P2": "P1-C2",
    "P3": "P2-C1",
    "P4": "P2-C2",
}
_PANEL_TO_RAW = {value: key for key, value in _SLOT_LABELS.items()}


def _profile_path() -> str:
    writable = user_data_path("hitboxes", "hitbox_range_profiles.json")
    if os.path.exists(writable):
        return writable
    return data_path("hitboxes", "hitbox_range_profiles.json")


def _load_profiles() -> dict[str, dict[str, Any]]:
    try:
        with open(_profile_path(), "r", encoding="utf-8") as f:
            payload = json.load(f)
        attacks = payload.get("attacks") if isinstance(payload, dict) else None
        if isinstance(attacks, dict):
            return {str(k): dict(v) for k, v in attacks.items() if isinstance(v, dict)}
    except Exception:
        pass
    return {}


def _profile_sort_key(item: tuple[str, dict[str, Any]]) -> tuple[str, int, str]:
    key, profile = item
    label = str(profile.get("move_name") or "").strip().lower()
    try:
        move_key = int(profile.get("move_key") or profile.get("move_id") or key.split(":", 1)[1])
    except Exception:
        move_key = 0
    return label, move_key, key


def open_ruler_lock_window(
    roster: dict[str, dict[str, Any]],
    locks: dict[str, str],
    on_change: Callable[[dict[str, str]], None],
) -> None:
    """Open a small move picker for per-slot ruler display locks."""

    roster_copy = {str(k): dict(v) for k, v in (roster or {}).items() if isinstance(v, dict)}
    lock_state = {str(k): str(v) for k, v in (locks or {}).items() if str(v).strip()}

    def _open(root: tk.Tk) -> None:
        global _WIN
        if _WIN is not None:
            try:
                if _WIN.winfo_exists():
                    _WIN.deiconify()
                    _WIN.lift()
                    _WIN.focus_force()
                    return
            except Exception:
                _WIN = None

        win = tk.Toplevel(root)
        _WIN = win
        win.title("Continuo Ruler Lock")
        win.geometry("560x470")
        win.minsize(500, 400)
        win.configure(bg="#0b111b")

        style = ttk.Style(win)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Ruler.TFrame", background="#0b111b")
        style.configure("Ruler.TLabel", background="#0b111b", foreground="#dfe7f2")
        style.configure("RulerMuted.TLabel", background="#0b111b", foreground="#8f9bad")
        style.configure("Ruler.TButton", padding=(9, 5))
        style.configure("Ruler.TCombobox", padding=3)

        outer = ttk.Frame(win, style="Ruler.TFrame", padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="RULER MOVE LOCK", style="Ruler.TLabel", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        ttk.Label(
            outer,
            text=(
                "Lock a saved move to one fighter slot so its ruler stays visible while you move around. "
                "Unlock returns that slot to the normal automatic ruler behavior."
            ),
            style="RulerMuted.TLabel",
            wraplength=520,
            justify="left",
        ).pack(anchor="w", pady=(4, 12))

        top = ttk.Frame(outer, style="Ruler.TFrame")
        top.pack(fill="x")
        ttk.Label(top, text="Source", style="Ruler.TLabel").pack(side="left")

        slot_var = tk.StringVar(value="P1-C1")
        slot_values = list(_PANEL_TO_RAW)
        slot_box = ttk.Combobox(top, textvariable=slot_var, values=slot_values, state="readonly", width=8, style="Ruler.TCombobox")
        slot_box.pack(side="left", padx=(8, 12))

        char_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=char_var, style="RulerMuted.TLabel").pack(side="left")

        status_var = tk.StringVar(value="")
        status = ttk.Label(outer, textvariable=status_var, style="RulerMuted.TLabel", wraplength=520, justify="left")
        status.pack(fill="x", pady=(8, 6))

        list_frame = ttk.Frame(outer, style="Ruler.TFrame")
        list_frame.pack(fill="both", expand=True)
        move_list = tk.Listbox(
            list_frame,
            bg="#111a28",
            fg="#dfe7f2",
            selectbackground="#274e78",
            selectforeground="#ffffff",
            highlightthickness=1,
            highlightbackground="#31445d",
            relief="flat",
            font=("Consolas", 10),
            exportselection=False,
        )
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=move_list.yview)
        move_list.configure(yscrollcommand=scroll.set)
        move_list.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        row_keys: list[str] = []
        profiles: dict[str, dict[str, Any]] = {}

        def emit() -> None:
            try:
                on_change(dict(lock_state))
            except Exception as exc:
                status_var.set(f"Could not save ruler lock: {exc}")

        def refresh(*_args: Any) -> None:
            nonlocal profiles, row_keys
            profiles = _load_profiles()
            slot = _PANEL_TO_RAW.get(str(slot_var.get() or "P1-C1"), "P1")
            info = roster_copy.get(slot) or {}
            try:
                char_id = int(info.get("char_id") or 0)
            except Exception:
                char_id = 0
            char_name = str(info.get("name") or CHAR_NAMES.get(char_id) or "Unknown")
            char_var.set(f"{_SLOT_LABELS.get(slot, slot)}  |  {char_name}  |  ID {char_id or '?'}")

            rows = []
            if char_id > 0:
                for key, profile in profiles.items():
                    try:
                        if int(profile.get("char_id") or key.split(":", 1)[0]) != char_id:
                            continue
                    except Exception:
                        continue
                    rows.append((key, profile))
            rows.sort(key=_profile_sort_key)

            move_list.delete(0, "end")
            row_keys = []
            locked_key = str(lock_state.get(slot) or "")
            locked_index = None
            for index, (key, profile) in enumerate(rows):
                try:
                    move_key = int(profile.get("move_key") or profile.get("move_id") or key.split(":", 1)[1])
                except Exception:
                    move_key = 0
                label = str(profile.get("move_name") or f"Action 0x{move_key:04X}").strip()
                posture = str(profile.get("posture") or "").strip().lower()
                suffix = f"  [{posture}]" if posture else ""
                move_list.insert("end", f"0x{move_key:04X}  {label}{suffix}")
                row_keys.append(key)
                if key == locked_key:
                    locked_index = index

            if locked_index is not None:
                move_list.selection_set(locked_index)
                move_list.see(locked_index)
                locked_profile = profiles.get(locked_key) or {}
                locked_name = str(locked_profile.get("move_name") or locked_key)
                status_var.set(f"Locked: {locked_name}")
            elif locked_key:
                status_var.set("This slot has a saved lock that does not match the current character. Unlock it or choose a new move.")
            elif rows:
                status_var.set("Automatic ruler behavior is active. Select a move and click Lock Selected to freeze it.")
            else:
                status_var.set("No saved range profiles are available for this fighter yet. Perform a move with the ruler enabled, then click Refresh.")

        def lock_selected() -> None:
            slot = _PANEL_TO_RAW.get(str(slot_var.get() or "P1-C1"), "P1")
            selection = move_list.curselection()
            if not selection:
                status_var.set("Select a saved move first.")
                return
            idx = int(selection[0])
            if idx < 0 or idx >= len(row_keys):
                return
            lock_state[slot] = row_keys[idx]
            emit()
            refresh()

        def unlock_slot() -> None:
            slot = _PANEL_TO_RAW.get(str(slot_var.get() or "P1-C1"), "P1")
            lock_state.pop(slot, None)
            emit()
            refresh()

        def unlock_all() -> None:
            lock_state.clear()
            emit()
            refresh()

        buttons = ttk.Frame(outer, style="Ruler.TFrame")
        buttons.pack(fill="x", pady=(10, 0))
        ttk.Button(buttons, text="Lock Selected", command=lock_selected, style="Ruler.TButton").pack(side="left")
        ttk.Button(buttons, text="Unlock Slot", command=unlock_slot, style="Ruler.TButton").pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Unlock All", command=unlock_all, style="Ruler.TButton").pack(side="left", padx=(6, 0))
        ttk.Button(buttons, text="Refresh", command=refresh, style="Ruler.TButton").pack(side="right")

        slot_box.bind("<<ComboboxSelected>>", refresh)
        move_list.bind("<Double-Button-1>", lambda _event: lock_selected())

        def close() -> None:
            global _WIN
            try:
                win.destroy()
            finally:
                _WIN = None

        win.protocol("WM_DELETE_WINDOW", close)
        refresh()

    tk_call(_open)
