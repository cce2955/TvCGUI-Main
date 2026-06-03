from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any

import char_test_runtime as runtime

try:
    from tk_host import tk_call
except Exception:  # pragma: no cover
    tk_call = None

_WIN: tk.Toplevel | None = None
_BG = "#151821"
_CARD = "#1f2430"
_TEXT = "#f2f5ff"
_MUTED = "#aeb6c8"
_ACCENT = "#7788ff"


def _label(parent: tk.Misc, text: str, *, bold: bool = False, muted: bool = False) -> tk.Label:
    return tk.Label(
        parent,
        text=text,
        bg=_CARD,
        fg=(_MUTED if muted else _TEXT),
        font=("Segoe UI", 10, "bold" if bold else "normal"),
        anchor="w",
        justify="left",
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
        pady=6,
        font=("Segoe UI", 10, "bold"),
    )


def _card(parent: tk.Misc) -> tk.Frame:
    return tk.Frame(parent, bg=_CARD, bd=0, highlightthickness=1, highlightbackground="#2c3345")


def _get_choices() -> tuple[list[str], list[str]]:
    try:
        state = runtime.get_roster_patch_state()
        slots = list(state.get("roster_slots") or [])
        chars = list(state.get("target_chars") or [])
    except Exception:
        slots = []
        chars = []
    if not slots:
        slots = [
            "Ryu slot 0x1A (ID 0x0C)",
            "Chun-Li slot 0x19 (ID 0x0D)",
            "Ken the Eagle slot 0x00 (ID 0x01)",
        ]
    if not chars:
        chars = ["Chun-Li (ID 0x0D)", "Ryu (ID 0x0C)", "Ken the Eagle (ID 0x01)"]
    return slots, chars


def _format_snapshot(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return "No selector snapshot yet."

    lines: list[str] = []
    lines.append(f"roster base: {snapshot.get('roster_base', '')}")
    lines.append(
        "hover: "
        f"index {snapshot.get('hover_index', '')}    "
        f"slot addr {snapshot.get('hover_slot_addr', '')}    "
        f"default {snapshot.get('hover_slot_default', '')}    "
        f"current {snapshot.get('hover_slot_label', '')}"
    )
    lines.append("")
    lines.append("Selector fields:")
    for item in snapshot.get("fields", []) or []:
        display = str(item.get("display") or "")
        suffix = f"    {display}" if display else ""
        lines.append(f"{item.get('addr', '')}  {item.get('label', '')}: {item.get('value', '')}{suffix}")

    lines.append("")
    lines.append("Roster table:")
    for item in snapshot.get("table", []) or []:
        marker = "patched" if item.get("patched") else ""
        lines.append(
            f"{item.get('slot', '')}  {item.get('addr', '')}  "
            f"wheel={item.get('default_label', '')}  "
            f"current={item.get('char_label', '')}  {marker}"
        )
    return "\n".join(lines)


def _show_char_test_window(master: tk.Misc | None = None) -> None:
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
    win.title("Char test - roster table patch")
    win.geometry("880x720")
    win.configure(bg=_BG)
    win.minsize(780, 600)

    def _on_close() -> None:
        global _WIN
        _WIN = None
        try:
            win.destroy()
        except Exception:
            pass

    win.protocol("WM_DELETE_WINDOW", _on_close)

    root = tk.Frame(win, bg=_BG)
    root.pack(fill="both", expand=True, padx=12, pady=12)

    header = tk.Frame(root, bg=_BG)
    header.pack(fill="x", pady=(0, 10))
    tk.Label(header, text="Char test", bg=_BG, fg=_TEXT, font=("Segoe UI", 15, "bold")).pack(side="left")
    tk.Label(header, text="Roster table patch only", bg=_BG, fg=_ACCENT, font=("Segoe UI", 10, "bold")).pack(side="left", padx=(10, 0))

    info = _card(root)
    info.pack(fill="x", pady=(0, 10))
    _label(info, "Working lane", bold=True).pack(fill="x", padx=12, pady=(12, 2))
    _label(
        info,
        "Patch the select-wheel roster table before the loader builds chr/<tag> request rows. No trace, no upstream poke, no loader-string swap.",
        muted=True,
    ).pack(fill="x", padx=12, pady=(0, 10))
    _label(info, "0x809BD0C4 + wheel_slot * 4 = character", bold=True).pack(fill="x", padx=12, pady=(0, 2))
    _label(info, "The dropdowns now cover the full observed roster and show character names with IDs.", muted=True).pack(fill="x", padx=12, pady=(0, 12))

    form = _card(root)
    form.pack(fill="x", pady=(0, 10))
    _label(form, "Patch controls", bold=True).pack(fill="x", padx=12, pady=(12, 6))

    grid = tk.Frame(form, bg=_CARD)
    grid.pack(fill="x", padx=12, pady=(0, 8))
    slot_choices, char_choices = _get_choices()
    default_slot = next((s for s in slot_choices if "Ryu" in s and "slot 0x1A" in s), slot_choices[-1])
    default_target = next((c for c in char_choices if c.startswith("Chun-Li ")), char_choices[0])
    slot_var = tk.StringVar(value=default_slot)
    target_var = tk.StringVar(value=default_target)

    _label(grid, "Wheel slot to replace", bold=True).grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
    slot_box = ttk.Combobox(
        grid,
        textvariable=slot_var,
        values=slot_choices,
        width=46,
        state="readonly",
    )
    slot_box.grid(row=0, column=1, sticky="ew", pady=4)

    _label(grid, "Replacement character", bold=True).grid(row=1, column=0, sticky="w", padx=(0, 10), pady=4)
    target_box = ttk.Combobox(
        grid,
        textvariable=target_var,
        values=char_choices,
        width=46,
        state="readonly",
    )
    target_box.grid(row=1, column=1, sticky="ew", pady=4)
    grid.columnconfigure(1, weight=1)

    status_var = tk.StringVar(value="Ready.")
    buttons = tk.Frame(form, bg=_CARD)
    buttons.pack(fill="x", padx=12, pady=(0, 12))

    def _snapshot() -> None:
        runtime.queue_roster_snapshot()
        status_var.set("Selector snapshot queued.")

    def _patch_slot() -> None:
        result = runtime.queue_roster_patch_slot(slot_var.get(), target_var.get())
        status_var.set(f"Patch queued: {result.get('slot_label')} -> {result.get('target_label')}")

    def _patch_current_hover() -> None:
        result = runtime.queue_roster_patch_current_hover(target_var.get())
        status_var.set(f"Current-hover patch queued -> {result.get('target_label')}")

    def _restore() -> None:
        runtime.queue_roster_restore()
        status_var.set("Restore queued.")

    _button(buttons, "Snapshot selector", _snapshot).pack(side="left")
    _button(buttons, "Patch selected slot", _patch_slot).pack(side="left", padx=(8, 0))
    _button(buttons, "Patch current hover", _patch_current_hover).pack(side="left", padx=(8, 0))
    _button(buttons, "Restore roster", _restore).pack(side="right")

    _label(
        form,
        "Patch selected slot uses the dropdown slot. Patch current hover reads the live cursor slot and uses only the replacement character dropdown.",
        muted=True,
    ).pack(fill="x", padx=12, pady=(0, 12))

    state_card = _card(root)
    state_card.pack(fill="both", expand=True)
    _label(state_card, "State", bold=True).pack(fill="x", padx=12, pady=(12, 4))

    state_text = tk.Text(
        state_card,
        height=20,
        bg="#121620",
        fg=_TEXT,
        insertbackground=_TEXT,
        relief="flat",
        wrap="none",
        font=("Consolas", 9),
    )
    state_text.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=(0, 12))
    yscroll = ttk.Scrollbar(state_card, orient="vertical", command=state_text.yview)
    yscroll.pack(side="right", fill="y", padx=(0, 12), pady=(0, 12))
    state_text.configure(yscrollcommand=yscroll.set)

    bottom = tk.Frame(root, bg=_BG)
    bottom.pack(fill="x", pady=(10, 0))
    tk.Label(bottom, textvariable=status_var, bg=_BG, fg=_MUTED, anchor="w", justify="left").pack(side="left", fill="x", expand=True)

    def _refresh() -> None:
        try:
            state = runtime.get_char_test_state()
            roster = state.get("roster_patch") or {}
            lines = [
                f"queued: {roster.get('queued', 0)}    patches: {roster.get('patches', 0)}    restored: {roster.get('restored', 0)}    failed: {roster.get('failed', 0)}",
                f"restore available: {roster.get('restore_available')}    last action: {roster.get('last_action') or ''}",
                f"error: {roster.get('last_error') or ''}",
                "",
                "Originals:",
                str(roster.get("originals") or {}),
                "",
                "Snapshot:",
                _format_snapshot(roster.get("last_snapshot") or {}),
            ]
            state_text.configure(state="normal")
            state_text.delete("1.0", "end")
            state_text.insert("1.0", "\n".join(lines))
            state_text.configure(state="disabled")
        except Exception as e:
            status_var.set(f"Refresh failed: {e!r}")
        try:
            win.after(500, _refresh)
        except Exception:
            pass

    _refresh()


def open_char_test_window() -> None:
    if tk_call is not None:
        tk_call(_show_char_test_window)
        return
    _show_char_test_window(None)
