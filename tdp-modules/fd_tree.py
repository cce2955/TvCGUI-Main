# fd_tree.py
#
# This file owns tree column definitions + row population wiring.
# The current layout keeps the dense grid for power editing, but adds a
# cleaner workbench shell, optional advanced filters, core/all column views,
# and a right-side selected-move inspector so users do not have to parse a
# giant spreadsheet for normal edits.

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import fd_utils as U
from fd_patterns import (
    find_superbg_addr,
    find_speed_mod_addr,
    find_attack_property_addr,
    fmt_attack_property,
)
from fd_widgets import Tooltip, get_field_help


FD_COLUMNS = (
    "move", "kind",
    "damage",
    "meter",
    "startup", "active", "active2",
    "hitstun", "blockstun", "hitstop",
    "launch_profile", "kb_unknown", "kb_x", "air_kb",
    "speed_mod", "attack_property", "hit_reaction",
    "superbg",
    "abs",
)

FD_CORE_COLUMNS = (
    "move",
    "damage", "meter",
    "startup", "active",
    "hitstun", "blockstun", "hitstop",
    "kb_x", "air_kb",
    "speed_mod", "attack_property", "hit_reaction",
    "abs",
)

FD_LABELS = {
    "move": "Move",
    "kind": "Kind",
    "damage": "Damage",
    "meter": "Meter",
    "startup": "Startup",
    "active": "Active",
    "active2": "Active 2",
    "hitstun": "Hitstun",
    "blockstun": "Blockstun",
    "hitstop": "Hitstop",
    "launch_profile": "Recovery",
    "kb_unknown": "KB U",
    "kb_x": "KB X",
    "air_kb": "Arc",
    "speed_mod": "Speed Mod",
    "attack_property": "Attack Property",
    "hit_reaction": "Hit Reaction",
    "superbg": "SuperBG",
    "abs": "Address",
}


def _display_columns(tree: ttk.Treeview) -> list[str]:
    all_cols = list(tree["columns"])
    display = tree["displaycolumns"]
    if not display or display == "#all" or display == ("#all",):
        return all_cols
    if isinstance(display, str):
        return [display]
    return list(display)


def configure_styles(root: tk.Toplevel) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    bg_main = "#101722"
    bg_panel = "#152033"
    bg_card = "#19263A"
    bg_header = "#21324A"
    bg_select = "#28466A"
    bg_entry = "#0F1724"
    txt_main = "#E8F1FF"
    txt_muted = "#91A7C1"
    txt_accent = "#8CBFFF"
    border = "#2C3E58"

    root.configure(bg=bg_main)

    style.configure("FD.TFrame", background=bg_main, borderwidth=0)
    style.configure("Top.TFrame", background=bg_main, borderwidth=0)
    style.configure("Hero.TFrame", background=bg_panel, borderwidth=1, relief="solid")
    style.configure("Card.TFrame", background=bg_card, borderwidth=1, relief="solid")
    style.configure("Inspector.TFrame", background=bg_panel, borderwidth=0)
    style.configure("Status.TFrame", background=bg_header, borderwidth=1, relief="solid")

    style.configure("Top.TLabel", background=bg_main, foreground=txt_main, font=("Segoe UI", 9))
    style.configure("HeroTitle.TLabel", background=bg_panel, foreground=txt_main, font=("Segoe UI Semibold", 13))
    style.configure("HeroSub.TLabel", background=bg_panel, foreground=txt_muted, font=("Segoe UI", 9))
    style.configure("Muted.Top.TLabel", background=bg_main, foreground=txt_muted, font=("Segoe UI", 9))
    style.configure("Card.TLabel", background=bg_card, foreground=txt_main, font=("Segoe UI", 9))
    style.configure("CardMuted.TLabel", background=bg_card, foreground=txt_muted, font=("Segoe UI", 9))
    style.configure("ValueChip.TLabel", background=bg_entry, foreground=txt_main, borderwidth=1, relief="solid", padding=(7, 3), font=("Segoe UI", 9))
    style.configure("ValueChipHover.TLabel", background="#1E3350", foreground=txt_main, borderwidth=1, relief="solid", padding=(7, 3), font=("Segoe UI", 9))
    style.configure("ValueChanged.TLabel", background="#2B2412", foreground="#FFE3A3", borderwidth=1, relief="solid", padding=(7, 3), font=("Segoe UI Semibold", 9))
    style.configure("ValueChangedHover.TLabel", background="#3A2E12", foreground="#FFE9B8", borderwidth=1, relief="solid", padding=(7, 3), font=("Segoe UI Semibold", 9))
    style.configure("ValueStatic.TLabel", background=bg_card, foreground=txt_main, padding=(7, 3), font=("Segoe UI", 9))
    style.configure("Section.TLabel", background=bg_card, foreground=txt_accent, font=("Segoe UI Semibold", 9))
    style.configure("InspectorTitle.TLabel", background=bg_panel, foreground=txt_main, font=("Segoe UI Semibold", 13))
    style.configure("InspectorSub.TLabel", background=bg_panel, foreground=txt_muted, font=("Segoe UI", 9))
    style.configure("Status.TLabel", background=bg_header, foreground=txt_main, font=("Segoe UI", 9))
    style.configure("FilterLabel.TLabel", background=bg_card, foreground=txt_muted, font=("Segoe UI", 8))

    style.configure(
        "Treeview",
        background="#121C2B",
        fieldbackground="#121C2B",
        foreground=txt_main,
        bordercolor=border,
        lightcolor=border,
        darkcolor=border,
        rowheight=24,
        font=("Segoe UI", 9),
    )
    style.map("Treeview", background=[("selected", bg_select)], foreground=[("selected", txt_main)])

    style.configure(
        "Treeview.Heading",
        background=bg_header,
        foreground=txt_main,
        relief="solid",
        borderwidth=1,
        font=("Segoe UI Semibold", 9),
    )
    style.map("Treeview.Heading", background=[("active", "#2A3F5C")])

    style.configure(
        "TButton",
        background=bg_header,
        foreground=txt_main,
        bordercolor=border,
        focusthickness=1,
        focuscolor="#3A567A",
        font=("Segoe UI", 9),
        padding=(8, 4),
    )
    style.map(
        "TButton",
        background=[("active", "#2B4260"), ("pressed", "#334D70")],
        foreground=[("active", txt_main)],
    )
    style.configure("Small.TButton", font=("Segoe UI", 8), padding=(6, 2))

    style.configure(
        "TEntry",
        fieldbackground=bg_entry,
        foreground=txt_main,
        bordercolor=border,
        lightcolor=border,
        darkcolor=border,
        insertcolor=txt_main,
        padding=(5, 3),
    )


def build_top_bar(win) -> None:
    cname = win.target_slot.get("char_name", "-")

    top = ttk.Frame(win.root, style="Top.TFrame")
    top.pack(side="top", fill="x", padx=10, pady=(10, 6))

    hero = ttk.Frame(top, style="Hero.TFrame", padding=(12, 8))
    hero.pack(side="left", fill="x", expand=True)

    ttk.Label(hero, text=f"Frame Data Workbench: {win.slot_label} ({cname})", style="HeroTitle.TLabel").pack(anchor="w")
    ttk.Label(
        hero,
        textvariable=win._writer_var,
        style="HeroSub.TLabel",
    ).pack(anchor="w", pady=(2, 0))

    controls = ttk.Frame(top, style="Top.TFrame")
    controls.pack(side="right", padx=(10, 0))

    search = ttk.Frame(controls, style="Top.TFrame")
    search.pack(side="top", fill="x")
    ttk.Label(search, text="Search", style="Top.TLabel").pack(side="left", padx=(0, 6))
    ent = ttk.Entry(search, textvariable=win._filter_var, width=30)
    ent.pack(side="left")
    Tooltip(ent, "Search visible move names, kinds, and addresses. Press Enter to apply.")
    ent.bind("<Return>", lambda _e: win._apply_filter())
    ttk.Button(search, text="Apply", command=win._apply_filter).pack(side="left", padx=(6, 0))
    ttk.Button(search, text="Clear", command=win._clear_filter).pack(side="left", padx=(6, 0))

    actions = ttk.Frame(controls, style="Top.TFrame")
    actions.pack(side="top", fill="x", pady=(6, 0))

    win._columns_btn_var = tk.StringVar(master=win.root, value="Show all columns")
    win._filter_panel_btn_var = tk.StringVar(master=win.root, value="Advanced filters")

    ttk.Button(actions, textvariable=win._columns_btn_var, command=lambda: getattr(win, "_toggle_core_columns", lambda: None)()).pack(side="left", padx=(0, 4))
    ttk.Button(actions, textvariable=win._filter_panel_btn_var, command=lambda: getattr(win, "_toggle_advanced_filters", lambda: None)()).pack(side="left", padx=4)
    ttk.Button(actions, text="Expand", command=win._expand_all).pack(side="left", padx=4)
    ttk.Button(actions, text="Collapse", command=win._collapse_all).pack(side="left", padx=4)
    ttk.Button(actions, text="Refresh", command=win._refresh_visible).pack(side="left", padx=4)
    ttk.Label(actions, textvariable=win._changed_count_var, style="Muted.Top.TLabel").pack(side="left", padx=(12, 4))
    ttk.Button(actions, text="Reset changed", command=win._reset_all_moves).pack(side="left", padx=4)


def _build_inspector(win, parent: ttk.Frame) -> None:
    win._inspector_value_vars = {}
    win._inspector_buttons = {}
    win._inspector_value_widgets = {}
    win._inspector_editable_cols = set()

    # The inspector can be taller than the available window height. Use a real
    # scrolling canvas and reserve the scrollbar column first so it remains
    # visible even when the right pane is narrow.
    parent.rowconfigure(0, weight=1)
    parent.columnconfigure(0, weight=1)
    parent.columnconfigure(1, weight=0)

    canvas = tk.Canvas(parent, bg="#152033", highlightthickness=0, bd=0)
    scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
    canvas.configure(yscrollcommand=scroll.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    win._inspector_canvas = canvas
    win._inspector_scrollbar = scroll

    inner = ttk.Frame(canvas, style="Inspector.TFrame", padding=(12, 12))
    win._inspector_inner = inner
    window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    def _on_configure(_evt=None):
        try:
            canvas.configure(scrollregion=canvas.bbox("all"))
            # Keep the inner frame fitted to the visible canvas so value chips
            # gain width instead of being clipped by an action-button column.
            canvas.itemconfigure(window_id, width=max(1, canvas.winfo_width()))
        except Exception:
            pass

    def _wheel(event):
        try:
            if getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            else:
                delta = -int(event.delta / 120) if event.delta else 0
            if delta:
                canvas.yview_scroll(delta, "units")
        except Exception:
            pass
        return "break"

    def _bind_wheel(_evt=None):
        try:
            canvas.bind_all("<MouseWheel>", _wheel)
            canvas.bind_all("<Button-4>", _wheel)
            canvas.bind_all("<Button-5>", _wheel)
        except Exception:
            pass

    def _unbind_wheel(_evt=None):
        try:
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")
        except Exception:
            pass

    inner.bind("<Configure>", _on_configure)
    canvas.bind("<Configure>", _on_configure)
    canvas.bind("<Enter>", _bind_wheel)
    inner.bind("<Enter>", _bind_wheel)
    canvas.bind("<Leave>", _unbind_wheel)

    def _value_click(col: str):
        if col == "kind":
            try:
                win._status_var.set("Kind is informational. It marks the row bucket, not a writable frame-data value.")
            except Exception:
                pass
            return
        if col == "abs":
            win._copy_selected_address()
            return
        if not U.WRITER_AVAILABLE:
            try:
                win._status_var.set("Frame-data writer is unavailable, so this value cannot be edited right now.")
            except Exception:
                pass
            return
        win._edit_selected_column(col)

    def _make_chip(parent_widget, col: str, var: tk.StringVar):
        editable = col not in {"kind"}
        style = "ValueChip.TLabel" if editable else "ValueStatic.TLabel"
        chip = ttk.Label(parent_widget, textvariable=var, style=style, anchor="w")
        chip.pack(side="left", fill="x", expand=True, padx=(4, 0))
        win._inspector_value_widgets[col] = chip

        if editable:
            chip.configure(cursor="hand2")
            chip.bind("<Button-1>", lambda _e, c=col: _value_click(c))
            chip.bind("<Return>", lambda _e, c=col: _value_click(c))
            chip.bind("<space>", lambda _e, c=col: _value_click(c))
            chip.bind("<Enter>", lambda _e, w=chip, c=col: getattr(win, "_configure_inspector_chip_style", lambda *_a, **_k: None)(w, c, True))
            chip.bind("<Leave>", lambda _e, w=chip, c=col: getattr(win, "_configure_inspector_chip_style", lambda *_a, **_k: None)(w, c, False))
            chip.configure(takefocus=True)
            win._inspector_editable_cols.add(col)
        return chip

    win._inspector_title_var = tk.StringVar(master=win.root, value="Select a move")
    win._inspector_subtitle_var = tk.StringVar(master=win.root, value="Use the inspector for normal edits without parsing the whole grid.")
    win._inspector_hint_var = tk.StringVar(master=win.root, value="Click any value chip to edit it. Changed values are highlighted until Reset changed restores them.")

    ttk.Label(inner, textvariable=win._inspector_title_var, style="InspectorTitle.TLabel", wraplength=320).pack(anchor="w")
    ttk.Label(inner, textvariable=win._inspector_subtitle_var, style="InspectorSub.TLabel", wraplength=320).pack(anchor="w", pady=(3, 10))

    move_card = ttk.Frame(inner, style="Card.TFrame", padding=(10, 8))
    move_card.pack(fill="x", pady=(0, 10))
    ttk.Label(move_card, text="Selected move", style="Section.TLabel").pack(anchor="w")
    mv_row = ttk.Frame(move_card, style="Card.TFrame")
    mv_row.pack(fill="x", pady=(6, 0))
    btn = ttk.Button(mv_row, text="Replace animation", style="Small.TButton", command=lambda: win._edit_selected_column("move"))
    btn.pack(side="left")
    win._inspector_buttons["move"] = btn
    ttk.Label(move_card, textvariable=win._inspector_hint_var, style="CardMuted.TLabel", wraplength=320).pack(anchor="w", pady=(8, 0))

    sections = [
        ("Impact", ["damage", "meter", "hitstop"]),
        ("Timing", ["startup", "active", "active2", "speed_mod"]),
        ("Stun and pressure", ["hitstun", "blockstun", "attack_property", "hit_reaction"]),
        ("Launch and physics", ["launch_profile", "kb_x", "air_kb", "kb_unknown"]),
        ("Flags and lookup", ["superbg", "kind", "abs"]),
    ]

    click_help = {
        "abs": "Click to copy this address.",
        "superbg": "Click to toggle or edit this flag.",
        "kind": "Informational only.",
    }

    for section_title, fields in sections:
        card = ttk.Frame(inner, style="Card.TFrame", padding=(10, 8))
        card.pack(fill="x", pady=(0, 10))
        ttk.Label(card, text=section_title, style="Section.TLabel").pack(anchor="w", pady=(0, 4))
        for col in fields:
            row = ttk.Frame(card, style="Card.TFrame")
            row.pack(fill="x", pady=2)

            label = ttk.Label(row, text=FD_LABELS.get(col, col), style="CardMuted.TLabel", width=14, anchor="w")
            label.pack(side="left")

            var = tk.StringVar(master=win.root, value="-")
            win._inspector_value_vars[col] = var
            chip = _make_chip(row, col, var)

            field_help = get_field_help(col, "")
            tip_bits = []
            if field_help:
                tip_bits.append(field_help)
            if col in click_help:
                tip_bits.append(click_help[col])
            elif col != "kind":
                tip_bits.append("Click this value to edit it.")
            tip_text = "\n\n".join(tip_bits)
            if tip_text:
                Tooltip(label, field_help or tip_text)
                Tooltip(chip, tip_text)

    try:
        win.root.after_idle(_on_configure)
    except Exception:
        pass

def build_tree_widget(win) -> ttk.Frame:
    body = ttk.Panedwindow(win.root, orient="horizontal")
    body.pack(fill="both", expand=True, padx=10, pady=(0, 8))

    left = ttk.Frame(body, style="FD.TFrame")
    right = ttk.Frame(body, style="Inspector.TFrame")
    body.add(left, weight=5)
    body.add(right, weight=2)

    guide = ttk.Frame(left, style="Card.TFrame", padding=(10, 7))
    guide.pack(fill="x", pady=(0, 8))
    ttk.Label(
        guide,
        text="Core view keeps the table readable. Select a row for the inspector, or switch to all columns when you need raw scouting data.",
        style="CardMuted.TLabel",
        wraplength=950,
    ).pack(anchor="w")

    frame = ttk.Frame(left, style="FD.TFrame")
    frame.pack(fill="both", expand=True)

    cols = FD_COLUMNS

    # Store per-column filter vars on the window so fd_window._apply_filter can read them.
    if not hasattr(win, "_col_filter_vars") or win._col_filter_vars is None:
        win._col_filter_vars = {}
    else:
        win._col_filter_vars.clear()

    win._col_filter_after_id = None

    def _schedule_apply_filters():
        try:
            if win._col_filter_after_id is not None:
                win.root.after_cancel(win._col_filter_after_id)
        except Exception:
            pass
        try:
            win._col_filter_after_id = win.root.after(80, win._apply_filter)
        except Exception:
            try:
                win._apply_filter()
            except Exception:
                pass

    filter_widths = {
        "move": 34,
        "kind": 10,
        "damage": 8,
        "meter": 8,
        "startup": 8,
        "active": 10,
        "active2": 10,
        "hitstun": 8,
        "blockstun": 8,
        "hitstop": 8,
        "launch_profile": 10,
        "kb_unknown": 10,
        "kb_x": 8,
        "air_kb": 8,
        "speed_mod": 10,
        "attack_property": 14,
        "hit_reaction": 16,
        "superbg": 10,
        "abs": 12,
    }

    filter_labels = {
        "move": "Move",
        "kind": "Kind",
        "damage": "Dmg",
        "meter": "Meter",
        "startup": "Start",
        "active": "Active",
        "active2": "Active2",
        "hitstun": "HS",
        "blockstun": "BS",
        "hitstop": "Stop",
        "launch_profile": "Recovery",
        "kb_unknown": "KB U",
        "kb_x": "KB X",
        "air_kb": "Arc",
        "speed_mod": "Speed",
        "attack_property": "Property",
        "hit_reaction": "HitReact",
        "superbg": "SuperBG",
        "abs": "Abs",
    }

    def _clear_col_filters():
        for _c, _v in win._col_filter_vars.items():
            try:
                _v.set("")
            except Exception:
                pass
        _schedule_apply_filters()

    win._clear_col_filters = _clear_col_filters

    filter_panel = ttk.Frame(frame, style="Card.TFrame", padding=(8, 8))
    win._filter_panel = filter_panel
    win._filter_panel_visible = False

    ttk.Label(
        filter_panel,
        text="Advanced column filters. Multiple boxes combine together.",
        style="CardMuted.TLabel",
    ).grid(row=0, column=0, columnspan=len(cols) + 1, sticky="w", pady=(0, 6))

    labels_row = ttk.Frame(filter_panel, style="Card.TFrame")
    labels_row.grid(row=1, column=0, sticky="ew", padx=(0, 2), pady=(0, 1))

    filter_row = ttk.Frame(filter_panel, style="Card.TFrame")
    filter_row.grid(row=2, column=0, sticky="ew", padx=(0, 2), pady=(0, 2))

    for i, c in enumerate(cols):
        w = filter_widths.get(c, 10)
        labels_row.grid_columnconfigure(i, weight=0, minsize=w * 8)
        filter_row.grid_columnconfigure(i, weight=0, minsize=w * 8)

    labels_row.grid_columnconfigure(len(cols), weight=1)
    filter_row.grid_columnconfigure(len(cols), weight=1)

    for col_i, c in enumerate(cols):
        w = filter_widths.get(c, 10)
        label_txt = filter_labels.get(c, c)

        lbl = ttk.Label(labels_row, text=label_txt, width=w, anchor="w", style="FilterLabel.TLabel")
        lbl.grid(row=0, column=col_i, sticky="w", padx=1, pady=0)

        var = tk.StringVar(master=win.root)
        win._col_filter_vars[c] = var

        ent = ttk.Entry(filter_row, textvariable=var, width=w)
        ent.grid(row=0, column=col_i, sticky="w", padx=1, pady=0)

        field_help = get_field_help(c, "")
        tip_text = f"Filter: {label_txt}. Case-insensitive substring. Leave blank to ignore."
        if field_help:
            tip_text += f"\n\n{field_help}"
        Tooltip(lbl, tip_text)
        Tooltip(ent, tip_text)
        ent.bind("<Return>", lambda _e: _schedule_apply_filters())

        def _make_trace(_var=var):
            def _trace_cb(*_args):
                _schedule_apply_filters()
            return _trace_cb

        var.trace_add("write", _make_trace())

    clear_btn = ttk.Button(labels_row, text="Clear column filters", command=win._clear_col_filters)
    clear_btn.grid(row=0, column=len(cols), sticky="e", padx=(8, 0))
    Tooltip(clear_btn, "Clear all per-column filters.")

    tree_wrap = ttk.Frame(frame, style="Card.TFrame", padding=(1, 1))
    tree_wrap.pack(fill="both", expand=True)

    def _toggle_advanced_filters():
        visible = bool(getattr(win, "_filter_panel_visible", False))
        if visible:
            filter_panel.pack_forget()
            win._filter_panel_visible = False
            if getattr(win, "_filter_panel_btn_var", None) is not None:
                win._filter_panel_btn_var.set("Advanced filters")
        else:
            filter_panel.pack(fill="x", padx=0, pady=(0, 8), before=tree_wrap)
            win._filter_panel_visible = True
            if getattr(win, "_filter_panel_btn_var", None) is not None:
                win._filter_panel_btn_var.set("Hide filters")

    win._toggle_advanced_filters = _toggle_advanced_filters

    # --- Tree ---
    win.tree = ttk.Treeview(tree_wrap, columns=cols, show="tree headings", height=30, selectmode="browse")

    vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=win.tree.yview)
    hsb = ttk.Scrollbar(tree_wrap, orient="horizontal", command=win.tree.xview)
    win.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    win.tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")

    tree_wrap.rowconfigure(0, weight=1)
    tree_wrap.columnconfigure(0, weight=1)

    win.tree.heading("#0", text="")
    win.tree.column("#0", width=18, stretch=False, anchor="w")

    headers = [
        ("move", "Move"),
        ("kind", "Kind"),
        ("damage", "Dmg"),
        ("meter", "Meter"),
        ("startup", "Start"),
        ("active", "Active"),
        ("active2", "Active 2"),
        ("hitstun", "HS"),
        ("blockstun", "BS"),
        ("hitstop", "Stop"),
        ("launch_profile", "Recovery"),
        ("kb_unknown", "KB U"),
        ("kb_x", "KB X"),
        ("air_kb", "Arc"),
        ("speed_mod", "Speed"),
        ("attack_property", "Attack Property"),
        ("hit_reaction", "Hit Reaction"),
        ("superbg", "SuperBG"),
        ("abs", "Address"),
    ]
    for c, txt in headers:
        win.tree.heading(c, text=txt, command=lambda col=c: win._on_sort_column(col))

    win.tree.column("move", width=280, anchor="w")
    win.tree.column("kind", width=80, anchor="w")
    win.tree.column("damage", width=76, anchor="center")
    win.tree.column("meter", width=70, anchor="center")
    win.tree.column("startup", width=70, anchor="center")
    win.tree.column("active", width=88, anchor="center")
    win.tree.column("active2", width=88, anchor="center")
    win.tree.column("hitstun", width=58, anchor="center")
    win.tree.column("blockstun", width=58, anchor="center")
    win.tree.column("hitstop", width=58, anchor="center")
    win.tree.column("launch_profile", width=92, anchor="center")
    win.tree.column("kb_unknown", width=86, anchor="center")
    win.tree.column("kb_x", width=72, anchor="center")
    win.tree.column("air_kb", width=72, anchor="center")
    win.tree.column("speed_mod", width=116, anchor="center")
    win.tree.column("attack_property", width=178, anchor="w")
    win.tree.column("hit_reaction", width=260, anchor="w")
    win.tree.column("superbg", width=78, anchor="center")
    win.tree.column("abs", width=124, anchor="w")

    win._fd_all_columns = tuple(cols)
    win._fd_core_columns = tuple(FD_CORE_COLUMNS)
    win._fd_showing_all_columns = False
    win.tree.configure(displaycolumns=win._fd_core_columns)

    def _toggle_core_columns():
        showing_all = bool(getattr(win, "_fd_showing_all_columns", False))
        if showing_all:
            win.tree.configure(displaycolumns=win._fd_core_columns)
            win._fd_showing_all_columns = False
            if getattr(win, "_columns_btn_var", None) is not None:
                win._columns_btn_var.set("Show all columns")
            win._status_var.set("Core columns visible")
        else:
            win.tree.configure(displaycolumns=win._fd_all_columns)
            win._fd_showing_all_columns = True
            if getattr(win, "_columns_btn_var", None) is not None:
                win._columns_btn_var.set("Core columns")
            win._status_var.set("All columns visible")

    win._toggle_core_columns = _toggle_core_columns

    def _update_hover_help(event):
        try:
            region = win.tree.identify_region(event.x, event.y)
            if region not in ("cell", "heading"):
                return
            column = win.tree.identify_column(event.x)
            if not column or column == "#0":
                return
            col_idx = int(column[1:]) - 1
            display_cols = _display_columns(win.tree)
            if col_idx < 0 or col_idx >= len(display_cols):
                return
            col_name = display_cols[col_idx]
            label = filter_labels.get(col_name, col_name)
            help_text = get_field_help(col_name, "")
            if help_text and getattr(win, "_status_var", None) is not None:
                win._status_var.set(f"{label}: {help_text}")
        except Exception:
            pass

    win.tree.bind("<Motion>", _update_hover_help, add=True)

    win.tree.tag_configure("row_even", background="#142033")
    win.tree.tag_configure("row_odd", background="#101A29")
    win.tree.tag_configure("kb_hot", foreground="#9FCCFF")
    win.tree.tag_configure("combo_hot", foreground="#B7D6FF")
    win.tree.tag_configure("property_hot", foreground="#D6C8FF")
    win.tree.tag_configure("super_on", foreground="#82E0B1")
    win.tree.tag_configure("missing_addr", foreground="#FF9A9A")
    win.tree.tag_configure("group_parent", foreground="#FFE3A3")
    win.tree.tag_configure("edited_row", background="#1E2D43")

    _build_inspector(win, right)

    return body

def populate_tree(win) -> None:
    cname = win.target_slot.get("char_name", "-")

    win.tree.delete(*win.tree.get_children())
    win._row_counter = 0
    win._all_item_ids = []
    win.move_to_tree_item = {}
    win.original_moves = {}

    def _fmt(v):
        return "" if v is None else str(v)


    def insert_move_row(mv, parent=""):
        aid = mv.get("id")
        move_abs = mv.get("abs")

        pretty = U.pretty_move_name(aid, cname)
        mv["pretty_name"] = pretty

        if aid is not None:
            dup = mv.get("dup_index")
            if dup is not None:
                pretty = f"{pretty} (Tier{dup + 1})"
            pretty = f"{pretty} [0x{aid:04X}]"

        # -------------------------
        # Resolve optional fields (SAFE)
        # -------------------------

        # -------------------------
        # Display formatting (ALWAYS RUNS)
        # -------------------------

        a_s = mv.get("active_start")
        a_e = mv.get("active_end")
        startup_txt = _fmt(a_s)
        active_txt = f"{a_s}-{a_e}" if a_s is not None and a_e is not None else ""

        a2_s = mv.get("active2_start")
        a2_e = mv.get("active2_end")
        if a2_s is not None and a2_e is not None:
            active2_txt = f"{a2_s}-{a2_e}"
        else:
            active2_txt = _fmt(a2_s or a2_e)

        launch_profile_txt = U.fmt_launch_profile_ui(mv)
        kb_unknown_txt = U.fmt_kb_unknown_ui(mv)
        kb_x_txt = U.fmt_kb_x_ui(mv)
        air_kb_txt = U.fmt_air_kb_ui(mv)
        hitstop_txt = U.fmt_stun(mv.get("hitstop"))

        speed_txt = ""
        if move_abs:
            if mv.get("speed_mod_addr") is None:
                try:
                    from dolphin_io import rbytes
                    saddr, sval, _ = find_speed_mod_addr(move_abs, rbytes)
                    if saddr:
                        mv["speed_mod_addr"] = saddr
                        mv["speed_mod"] = sval
                except Exception:
                    pass
        if mv.get("speed_mod_addr"):
            speed_txt = U.fmt_speed_mod_ui(mv.get("speed_mod"))

        attack_property_txt = ""
        if move_abs:
            if mv.get("attack_property_addr") is None:
                try:
                    from dolphin_io import rbytes
                    ap_addr, ap_val, _ = find_attack_property_addr(move_abs, rbytes)
                    if ap_addr:
                        mv["attack_property_addr"] = ap_addr
                        mv["attack_property"] = ap_val
                except Exception:
                    pass
        if mv.get("attack_property_addr"):
            attack_property_txt = fmt_attack_property(mv.get("attack_property"))

        superbg_txt = ""
        if move_abs:
            if mv.get("superbg_addr") is None:
                try:
                    from dolphin_io import rbytes, rd8
                    saddr, sval = find_superbg_addr(move_abs, rbytes, rd8)
                    if saddr:
                        mv["superbg_addr"] = saddr
                        mv["superbg_val"] = sval
                except Exception:
                    pass
        if mv.get("superbg_addr"):
            superbg_txt = U.fmt_superbg(mv.get("superbg_val"))

        hr_txt = U.fmt_hit_reaction(mv.get("hit_reaction"))

        # -------------------------
        # Insert row
        # -------------------------

        row_tag = "row_even" if (win._row_counter % 2 == 0) else "row_odd"
        win._row_counter += 1

        item_id = win.tree.insert(
            parent,
            "end",
            text="",
            tags=(row_tag,),
            values=(
                pretty,
                mv.get("kind", ""),
                _fmt(mv.get("damage")),
                _fmt(mv.get("meter")),
                startup_txt,
                active_txt,
                active2_txt,
                U.fmt_stun(mv.get("hitstun")),
                U.fmt_stun(mv.get("blockstun")),
                hitstop_txt,
                launch_profile_txt,
                kb_unknown_txt,
                kb_x_txt,
                air_kb_txt,
                speed_txt,
                attack_property_txt,
                hr_txt,
                superbg_txt,
                f"0x{move_abs:08X}" if move_abs else "",
            ),
        )

        win.move_to_tree_item[item_id] = mv
        win._all_item_ids.append(item_id)
        win._apply_row_tags(item_id, mv)

        return item_id

    # -------------------------
    # GROUPING (stable, no skips)
    # -------------------------

    def _move_quality(mv):
        score = 0
        if mv.get("damage") not in (None, "", 0):
            score += 100
        if mv.get("active_start") is not None and mv.get("active_end") is not None:
            score += 80
        if mv.get("hitstun") is not None:
            score += 40
        if mv.get("blockstun") is not None:
            score += 40
        if mv.get("knockback_addr") is not None:
            score += 25
        if mv.get("kind") == "normal":
            score += 10
        return score

    groups = {}
    order = []

    for mv in win.moves:
        aid = mv.get("id")
        if aid not in groups:
            groups[aid] = []
            order.append(aid)
        groups[aid].append(mv)

    for aid in order:
        mv_list = groups[aid]

        if len(mv_list) == 1:
            insert_move_row(mv_list[0])
            continue

        mv_list = sorted(
            mv_list,
            key=lambda mv: (
                -_move_quality(mv),
                0 if mv.get("kind") == "normal" else 1,
                mv.get("abs") or 0xFFFFFFFF,
            ),
        )

        parent = insert_move_row(mv_list[0])
        win.tree.item(parent, open=False)
        win.tree.item(parent, tags=("group_parent",))

        for mv in mv_list[1:]:
            insert_move_row(mv, parent=parent)

    # Populate the inspector immediately so the window opens with a useful
    # selected-row view instead of an empty side panel.
    try:
        if win._all_item_ids:
            first = win._all_item_ids[0]
            win.tree.selection_set(first)
            win.tree.focus(first)
            win.tree.see(first)
            win._on_select()
        else:
            win._refresh_inspector(None, None)
    except Exception:
        pass
