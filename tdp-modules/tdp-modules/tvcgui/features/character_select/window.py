from __future__ import annotations

import tkinter as tk
from tkinter import ttk
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
        wraplength=820,
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
    return tk.Frame(parent, bg=_CARD, bd=0, highlightthickness=1, highlightbackground="#2c3345")


def _make_scroll_frame(win: tk.Toplevel) -> tk.Frame:
    outer = tk.Frame(win, bg=_BG)
    outer.pack(fill="both", expand=True)

    canvas = tk.Canvas(outer, bg=_BG, highlightthickness=0)
    yscroll = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=yscroll.set)

    yscroll.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    content = tk.Frame(canvas, bg=_BG)
    window_id = canvas.create_window((0, 0), window=content, anchor="nw")

    def _update_scrollregion(_event: tk.Event | None = None) -> None:
        canvas.configure(scrollregion=canvas.bbox("all"))

    def _fit_width(event: tk.Event) -> None:
        canvas.itemconfigure(window_id, width=event.width)

    def _mousewheel(event: tk.Event) -> None:
        if getattr(event, "num", None) == 4:
            delta = -1
        elif getattr(event, "num", None) == 5:
            delta = 1
        else:
            delta = int(-1 * (event.delta / 120))
        canvas.yview_scroll(delta, "units")

    content.bind("<Configure>", _update_scrollregion)
    canvas.bind("<Configure>", _fit_width)
    canvas.bind_all("<MouseWheel>", _mousewheel)
    canvas.bind_all("<Button-4>", _mousewheel)
    canvas.bind_all("<Button-5>", _mousewheel)
    return content


def _get_clone_choices() -> list[str]:
    try:
        state = runtime.get_roster_patch_state()
        clone_slots = list(state.get("clone_slots") or [])
    except Exception:
        clone_slots = []
    if not clone_slots:
        clone_slots = [
            "Yami 1 clone slot 0x1B (ID 0x17)",
            "Yami 2 clone slot 0x1C (ID 0x18)",
            "Yami 3 clone slot 0x1D (ID 0x19)",
        ]
    return clone_slots


def _format_snapshot(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return "No selector snapshot yet."
    lines: list[str] = []
    lines.append(f"hover index: {snapshot.get('hover_index', '')}")
    for item in snapshot.get("fields", []) or []:
        lines.append(f"{item.get('addr', '')}  {item.get('label', '')}: {item.get('value', '')}  {item.get('display', '')}")
    lines.append("")
    lines.append("near Yami rows:")
    for item in snapshot.get("table", []) or []:
        slot = str(item.get("slot", ""))
        if slot in {"0x1A", "0x1B", "0x1C", "0x1D"}:
            lines.append(f"{slot}  {item.get('addr', '')}  {item.get('char_label', '')}")
    return "\n".join(lines)


def _format_visual_table(snapshot: dict[str, Any]) -> str:
    if not snapshot:
        return "No visual table snapshot yet."
    lines: list[str] = []
    lines.append(f"visual index table: {snapshot.get('index_table_addr', '')}")
    lines.append("index values:")
    lines.append(" ".join(snapshot.get("index_table") or []))
    lines.append("")
    lines.append(f"char ID table: {snapshot.get('char_table_addr', '')}")
    lines.append("char values:")
    lines.append(" ".join(snapshot.get("char_table") or []))
    lines.append("")
    lines.append(f"append index addr: {snapshot.get('append_index_addr', '')}")
    lines.append(f"append char addr: {snapshot.get('append_char_addr', '')}")
    return "\n".join(lines)


def _parse_slot(text: str) -> int:
    import re
    m = re.search(r"slot\s+0x([0-9A-Fa-f]+)", text or "")
    if m:
        return int(m.group(1), 16)
    return 0x1B


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
    win.title("Char test - Yami visual table")
    win.geometry("900x720")
    win.configure(bg=_BG)
    win.minsize(720, 520)

    def _on_close() -> None:
        global _WIN
        _WIN = None
        try:
            win.destroy()
        except Exception:
            pass

    win.protocol("WM_DELETE_WINDOW", _on_close)

    root = _make_scroll_frame(win)
    body = tk.Frame(root, bg=_BG)
    body.pack(fill="both", expand=True, padx=12, pady=12)

    header = tk.Frame(body, bg=_BG)
    header.pack(fill="x", pady=(0, 10))
    tk.Label(header, text="Char test", bg=_BG, fg=_TEXT, font=("Segoe UI", 15, "bold")).pack(side="left")
    tk.Label(header, text="Yami visual table probe", bg=_BG, fg=_ACCENT, font=("Segoe UI", 10, "bold")).pack(side="left", padx=(10, 0))

    status_var = tk.StringVar(value="Ready.")
    clone_choices = _get_clone_choices()
    clone_slot_var = tk.StringVar(value=clone_choices[0])

    main_card = _card(body)
    main_card.pack(fill="x", pady=(0, 10))
    _label(main_card, "Goal", bold=True).pack(fill="x", padx=12, pady=(12, 4))
    _label(
        main_card,
        "Yami 1/2/3 already exist logically. This tests the separate static visual table that appears to decide which face/icon rows exist. The Frank visual append keeps Yami char IDs but uses Frank visual indices for the new visual rows.",
        muted=True,
    ).pack(fill="x", padx=12, pady=(0, 10))

    setup_grid = tk.Frame(main_card, bg=_CARD)
    setup_grid.pack(fill="x", padx=12, pady=(0, 10))
    _label(setup_grid, "Force target", bold=True).grid(row=0, column=0, sticky="w", padx=(0, 10), pady=4)
    ttk.Combobox(setup_grid, textvariable=clone_slot_var, values=clone_choices, state="readonly", width=48).grid(row=0, column=1, sticky="ew", pady=4)
    setup_grid.columnconfigure(1, weight=1)

    def _set_status(text: str) -> None:
        status_var.set(text)

    def _restore() -> None:
        runtime.queue_roster_restore()
        _set_status("Restore queued.")

    def _snapshot() -> None:
        runtime.queue_roster_snapshot()
        _set_status("Snapshot queued.")

    def _shell_attempt() -> None:
        runtime.queue_yami_shell_attempt("zero")
        _set_status("Shell attempt queued.")

    def _visual_snapshot() -> None:
        runtime.queue_yami_visual_table_snapshot()
        _set_status("Visual table snapshot queued.")

    def _frank_append() -> None:
        runtime.queue_yami_visual_table_frank_append()
        _set_status("Frank visual rows queued.")

    def _native_append() -> None:
        runtime.queue_yami_visual_table_native_append()
        _set_status("Native Yami visual rows queued.")

    def _force_yami() -> None:
        slot = _parse_slot(clone_slot_var.get())
        target = {0x1B: 0x17, 0x1C: 0x18, 0x1D: 0x19}.get(slot, 0x17)
        runtime.queue_yami_force_hover(slot=slot, target=target)
        _set_status(f"Force target queued for slot 0x{slot:02X}.")

    button_card = _card(body)
    button_card.pack(fill="x", pady=(0, 10))
    _label(button_card, "Actions", bold=True).pack(fill="x", padx=12, pady=(12, 4))
    btns = tk.Frame(button_card, bg=_CARD)
    btns.pack(fill="x", padx=12, pady=(0, 12))
    buttons = [
        ("Snapshot", _snapshot),
        ("Visual table snapshot", _visual_snapshot),
        ("Shell attempt", _shell_attempt),
        ("Frank visual rows", _frank_append),
        ("Native Yami rows", _native_append),
        ("Force target", _force_yami),
        ("Restore", _restore),
    ]
    for i, (text, cmd) in enumerate(buttons):
        _button(btns, text, cmd).grid(row=i // 3, column=i % 3, sticky="ew", padx=4, pady=4)
    for c in range(3):
        btns.columnconfigure(c, weight=1)

    status_card = _card(body)
    status_card.pack(fill="both", expand=True, pady=(0, 10))
    _label(status_card, "State", bold=True).pack(fill="x", padx=12, pady=(12, 4))
    tk.Label(status_card, textvariable=status_var, bg=_CARD, fg=_ACCENT, font=("Segoe UI", 10, "bold"), anchor="w").pack(fill="x", padx=12, pady=(0, 6))
    state_text = tk.Text(status_card, height=20, bg="#11151e", fg=_TEXT, insertbackground=_TEXT, relief="flat", wrap="word")
    state_text.pack(fill="both", expand=True, padx=12, pady=(0, 12))

    def _refresh() -> None:
        try:
            state = runtime.get_roster_patch_state()
            lines: list[str] = []
            lines.append(f"last action: {state.get('last_action', '')}")
            lines.append(f"last error: {state.get('last_error', '')}")
            lines.append(f"patches: {state.get('patches', 0)}  failed: {state.get('failed', 0)}  queued: {state.get('queued', 0)}")
            lines.append(f"visual table mode: {state.get('visual_table_patch_mode', '')}")
            lines.append("")
            lines.append("selector:")
            lines.append(_format_snapshot(state.get("last_snapshot") or {}))
            lines.append("")
            lines.append("visual table:")
            lines.append(_format_visual_table(state.get("visual_table_snapshot") or {}))
            state_text.delete("1.0", "end")
            state_text.insert("1.0", "\n".join(lines))
        except Exception as exc:
            state_text.delete("1.0", "end")
            state_text.insert("1.0", repr(exc))
        try:
            win.after(500, _refresh)
        except Exception:
            pass

    _refresh()


def open_char_test_window(master: tk.Misc | None = None) -> None:
    if tk_call is not None:
        tk_call(lambda root: _show_char_test_window(master or root))
        return
    _show_char_test_window(master)


def show_char_test_window(master: tk.Misc | None = None) -> None:
    if tk_call is not None:
        tk_call(lambda root: _show_char_test_window(master or root))
        return
    _show_char_test_window(master)
