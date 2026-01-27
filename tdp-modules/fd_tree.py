# fd_tree.py
#
# Update: projectile display columns:
#   - proj_dmg  (ProjDmg)
#   - proj_tpl  (ProjTpl)
#
# This file owns tree column definitions + row population wiring.
# Projectile resolution itself is upstream; here we only display whatever
# mv carries (mv["proj_dmg"], mv["proj_tpl"]).

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import fd_utils as U
from fd_patterns import find_combo_kb_mod_addr, find_superbg_addr, find_speed_mod_addr
from fd_widgets import Tooltip


def configure_styles(root: tk.Toplevel) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    BG_MAIN = "#F6F7F9"
    BG_HEADER = "#E1E6ED"
    BG_SELECT = "#D6E6F5"

    TXT_MAIN = "#2F5D8C"
    TXT_MUTED = "#6B86A6"
    TXT_SELECT = "#1F3F66"

    BORDER = "#CBD3DE"

    style.configure("Top.TFrame", background=BG_MAIN, borderwidth=0)
    style.configure("Status.TFrame", background=BG_HEADER, borderwidth=1, relief="solid")

    style.configure("Top.TLabel", background=BG_MAIN, foreground=TXT_MAIN, font=("Segoe UI", 9))
    style.configure("Muted.Top.TLabel", background=BG_MAIN, foreground=TXT_MUTED, font=("Segoe UI", 9))
    style.configure("Status.TLabel", background=BG_HEADER, foreground=TXT_MAIN, font=("Segoe UI", 9))

    style.configure(
        "Treeview",
        background=BG_MAIN,
        fieldbackground=BG_MAIN,
        foreground=TXT_MAIN,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        rowheight=22,
        font=("Segoe UI", 9),
    )
    style.map("Treeview", background=[("selected", BG_SELECT)], foreground=[("selected", TXT_SELECT)])

    style.configure(
        "Treeview.Heading",
        background=BG_HEADER,
        foreground=TXT_MAIN,
        relief="solid",
        borderwidth=1,
        font=("Segoe UI Semibold", 9),
    )
    style.map("Treeview.Heading", background=[("active", BG_HEADER)])

    style.configure(
        "TButton",
        background=BG_HEADER,
        foreground=TXT_MAIN,
        bordercolor=BORDER,
        font=("Segoe UI", 9),
        padding=(8, 3),
    )
    style.map("TButton", background=[("active", "#DDE6F1")], foreground=[("active", TXT_SELECT)])


def build_top_bar(win) -> None:
    top = ttk.Frame(win.root, style="Top.TFrame")
    top.pack(side="top", fill="x", padx=8, pady=8)

    writer_lbl = ttk.Label(top, textvariable=win._writer_var, style="Top.TLabel")
    writer_lbl.pack(side="left")
    Tooltip(writer_lbl, "If move_writer is missing, this window is read-only.")

    filter_box = ttk.Frame(top, style="Top.TFrame")
    filter_box.pack(side="left", padx=18)

    ttk.Label(filter_box, text="Filter:", style="Top.TLabel").pack(side="left", padx=(0, 6))
    ent = ttk.Entry(filter_box, textvariable=win._filter_var, width=34)
    ent.pack(side="left")
    Tooltip(ent, "Type to filter visible rows by Move/Kind/Address. Press Enter to apply.")
    ent.bind("<Return>", lambda _e: win._apply_filter())

    ttk.Button(filter_box, text="Apply", command=win._apply_filter).pack(side="left", padx=6)
    ttk.Button(filter_box, text="Clear", command=win._clear_filter).pack(side="left")

    actions = ttk.Frame(top, style="Top.TFrame")
    actions.pack(side="right")

    ttk.Button(actions, text="Expand all", command=win._expand_all).pack(side="left", padx=4)
    ttk.Button(actions, text="Collapse all", command=win._collapse_all).pack(side="left", padx=4)
    ttk.Button(actions, text="Refresh visible", command=win._refresh_visible).pack(side="left", padx=4)
    ttk.Button(actions, text="Reset to original", command=win._reset_all_moves).pack(side="left", padx=4)


def build_tree_widget(win) -> ttk.Frame:
    hint = ttk.Label(
        win.root,
        text="Double-click a cell to edit. Right-click a cell for address tools. Grouped moves collapse under Tier1.",
        style="Muted.Top.TLabel",
    )
    hint.pack(side="top", fill="x", padx=10, pady=(0, 6))

    frame = ttk.Frame(win.root)
    frame.pack(fill="both", expand=True, padx=8, pady=8)

    cols = (
        "move", "kind",
        "damage", "proj_dmg", "proj_tpl",
        "meter",
        "startup", "active", "active2",
        "hitstun", "blockstun", "hitstop",
        "hb_main", "hb",
        "kb", "combo_kb_mod", "speed_mod", "hit_reaction",
        "superbg",
        "abs",
    )

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

    _filter_widths = {
        "move": 34,
        "kind": 10,
        "damage": 8,
        "proj_dmg": 8,
        "proj_tpl": 12,
        "meter": 8,
        "startup": 8,
        "active": 10,
        "active2": 10,
        "hitstun": 8,
        "blockstun": 8,
        "hitstop": 8,
        "hb_main": 8,
        "hb": 18,
        "kb": 16,
        "combo_kb_mod": 12,
        "speed_mod": 10,
        "hit_reaction": 16,
        "superbg": 10,
        "abs": 12,
    }

    # Short labels row 
    _filter_labels = {
        "move": "Move",
        "kind": "Kind",
        "damage": "Dmg",
        "proj_dmg": "ProjDmg",
        "proj_tpl": "ProjTpl",
        "meter": "Meter",
        "startup": "Start",
        "active": "Active",
        "active2": "Active2",
        "hitstun": "HS",
        "blockstun": "BS",
        "hitstop": "Stop",
        "hb_main": "Hitbox",
        "hb": "HB cand.",
        "kb": "Knockback",
        "combo_kb_mod": "ComboKB",
        "speed_mod": "Speed",
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

    # --- Column filter UI (label row above entry row; "invisible boxes") ---

    labels_row = ttk.Frame(frame)
    labels_row.grid(row=0, column=0, sticky="ew", padx=(0, 2), pady=(0, 1))

    filter_row = ttk.Frame(frame)
    filter_row.grid(row=1, column=0, sticky="ew", padx=(0, 2), pady=(0, 4))

    # Make the label row look like plain text over the same background
    # (optional: match your header color)
    try:
        s = ttk.Style(win.root)
        s.configure("FilterLabel.TLabel", background="#E1E6ED", foreground="#2F5D8C", font=("Segoe UI", 9))
        labels_row.configure(style="Top.TFrame")
        filter_row.configure(style="Top.TFrame")
    except Exception:
        pass

    # Configure columns so label "cells" and entry "cells" have identical widths.
    # ttk width is in characters. Use minsize to force consistent cell width.
    for i, c in enumerate(cols):
        w = _filter_widths.get(c, 10)
        labels_row.grid_columnconfigure(i, weight=0, minsize=w * 8)  # 8px per char approx
        filter_row.grid_columnconfigure(i, weight=0, minsize=w * 8)

    # Last column expands; it holds the clear button aligned right.
    labels_row.grid_columnconfigure(len(cols), weight=1)
    filter_row.grid_columnconfigure(len(cols), weight=1)

    for col_i, c in enumerate(cols):
        w = _filter_widths.get(c, 10)
        label_txt = _filter_labels.get(c, c)

        # "Invisible box": label with fixed width, no border, just text.
        lbl = ttk.Label(labels_row, text=label_txt, width=w, anchor="w", style="FilterLabel.TLabel")
        lbl.grid(row=0, column=col_i, sticky="w", padx=1, pady=0)

        var = tk.StringVar(master=win.root)
        win._col_filter_vars[c] = var

        ent = ttk.Entry(filter_row, textvariable=var, width=w)
        ent.grid(row=0, column=col_i, sticky="w", padx=1, pady=0)

        Tooltip(ent, f"Filter: {label_txt}. Case-insensitive substring. Leave blank to ignore.")
        ent.bind("<Return>", lambda _e: _schedule_apply_filters())

        def _make_trace(_var=var):
            def _trace_cb(*_args):
                _schedule_apply_filters()
            return _trace_cb

        var.trace_add("write", _make_trace())

    clear_btn = ttk.Button(labels_row, text="Clear col filter", command=win._clear_col_filters)
    clear_btn.grid(row=0, column=len(cols), sticky="e", padx=(8, 0))
    Tooltip(clear_btn, "Clear all per-column filters.")
   


    Tooltip(labels_row, "Type in any box to filter. Multiple boxes are AND'ed together.")

    # --- Tree ---
    win.tree = ttk.Treeview(frame, columns=cols, show="tree headings", height=30)

    vsb = ttk.Scrollbar(frame, orient="vertical", command=win.tree.yview)
    hsb = ttk.Scrollbar(frame, orient="horizontal", command=win.tree.xview)
    win.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    win.tree.grid(row=2, column=0, sticky="nsew")
    vsb.grid(row=2, column=1, sticky="ns")
    hsb.grid(row=3, column=0, sticky="ew")

    frame.rowconfigure(2, weight=1)
    frame.columnconfigure(0, weight=1)

    win.tree.heading("#0", text="")
    win.tree.column("#0", width=18, stretch=False, anchor="w")

    headers = [
        ("move", "Move"),
        ("kind", "Kind"),
        ("damage", "Dmg"),
        ("proj_dmg", "ProjDmg"),
        ("proj_tpl", "ProjTpl"),
        ("meter", "Meter"),
        ("startup", "Start"),
        ("active", "Active"),
        ("active2", "Active 2"),
        ("hitstun", "HS"),
        ("blockstun", "BS"),
        ("hitstop", "Stop"),
        ("hb_main", "Hitbox"),
        ("hb", "Hitbox cand."),
        ("kb", "Knockback"),
        ("combo_kb_mod", "Combo KB Mod"),
        ("speed_mod", "Speed Mod"),
        ("hit_reaction", "Hit Reaction"),
        ("superbg", "SuperBG"),
        ("abs", "Address"),
    ]
    for c, txt in headers:
        win.tree.heading(
            c,
            text=txt,
            command=lambda col=c: win._on_sort_column(col),
        )

    win.tree.column("move", width=260, anchor="w")
    win.tree.column("kind", width=70, anchor="w")

    win.tree.column("damage", width=70, anchor="center")
    win.tree.column("proj_dmg", width=70, anchor="center")
    win.tree.column("proj_tpl", width=120, anchor="w")

    win.tree.column("meter", width=60, anchor="center")
    win.tree.column("startup", width=60, anchor="center")
    win.tree.column("active", width=98, anchor="center")
    win.tree.column("active2", width=98, anchor="center")
    win.tree.column("hitstun", width=52, anchor="center")
    win.tree.column("blockstun", width=52, anchor="center")
    win.tree.column("hitstop", width=56, anchor="center")
    win.tree.column("hb_main", width=74, anchor="center")
    win.tree.column("hb", width=260, anchor="w")
    win.tree.column("kb", width=180, anchor="w")
    win.tree.column("combo_kb_mod", width=140, anchor="center")
    win.tree.column("speed_mod", width=120, anchor="center")
    win.tree.column("hit_reaction", width=280, anchor="w")
    win.tree.column("superbg", width=80, anchor="center")
    win.tree.column("abs", width=120, anchor="w")

    win.tree.tag_configure("row_even", background="#F7F9FC")
    win.tree.tag_configure("row_odd", background="#EEF2F7")
    win.tree.tag_configure("kb_hot", foreground="#3B6FA5")
    win.tree.tag_configure("combo_hot", foreground="#4C7FB8")
    win.tree.tag_configure("super_on", foreground="#3C8C6E")
    win.tree.tag_configure("missing_addr", foreground="#A65C5C")
    win.tree.tag_configure("group_parent", foreground="#5A4E2F")

    return frame
def populate_tree(win) -> None:
    cname = win.target_slot.get("char_name", "-")

    win.tree.delete(*win.tree.get_children())
    win._row_counter = 0
    win._all_item_ids = []
    win.move_to_tree_item = {}
    win.original_moves = {}

    def _fmt(v):
        return "" if v is None else str(v)

    def _fmt_proj_tpl(v):
        if v is None:
            return ""
        if isinstance(v, str):
            return v
        try:
            return f"0x{int(v):08X}"
        except Exception:
            return str(v)

    def _infer_strength_from_move_name(name):
        if not name:
            return None
        s = name.lower()
        if " l" in s or s.endswith("l"):
            return 1
        if " m" in s or s.endswith("m"):
            return 2
        if " h" in s or " c" in s or s.endswith("h") or s.endswith("c"):
            return 3
        return None

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

        if move_abs:
            if mv.get("proj_slices") is None:
                try:
                    U.resolve_projectile_strength_slices_for_move(
                        mv,
                        region_abs=move_abs,
                        region_size=0x1400,
                        move_name_for_strength=pretty,
                    )
                except Exception:
                    pass

            if mv.get("proj_dmg") is None and mv.get("proj_tpl") is None:
                try:
                    U.resolve_projectile_fields_for_move(
                        mv,
                        region_abs=move_abs,
                        region_size=0x1400,
                    )
                except Exception:
                    pass

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

        hb_main_txt = ""
        hb_txt = ""
        hb_cands = []
        hb_off = None
        hb_val = None

        if move_abs:
            try:
                hb_cands = U.scan_hitbox_candidates(move_abs)
                hb_off, hb_val = U.select_primary_hitbox(hb_cands)
                if hb_val is not None:
                    hb_main_txt = f"{hb_val:.1f}"
                if hb_cands:
                    hb_txt = U.format_candidate_list(hb_cands)
            except Exception:
                pass

        kb_parts = []
        if mv.get("kb0") is not None:
            kb_parts.append(f"K0:{mv['kb0']}")
        if mv.get("kb1") is not None:
            kb_parts.append(f"K1:{mv['kb1']}")
        if mv.get("kb_traj") is not None:
            kb_parts.append(U.fmt_kb_traj(mv["kb_traj"]))
        kb_txt = " ".join(kb_parts)

        combo_txt = ""
        if move_abs:
            if mv.get("combo_kb_mod_addr") is None:
                try:
                    from dolphin_io import rbytes
                    addr, cur, sig = find_combo_kb_mod_addr(move_abs, rbytes)
                    if addr:
                        mv["combo_kb_mod_addr"] = addr
                        mv["combo_kb_mod"] = cur
                except Exception:
                    pass
        if mv.get("combo_kb_mod_addr"):
            v = mv.get("combo_kb_mod")
            combo_txt = f"{v} (0x{v:02X})" if v is not None else "?"

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

        proj_dmg = mv.get("proj_dmg")
        if proj_dmg is None:
            proj_dmg = _infer_strength_from_move_name(pretty)

        proj_tpl = mv.get("proj_slice") or mv.get("proj_tpl")

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
                _fmt(proj_dmg),
                _fmt_proj_tpl(proj_tpl),
                _fmt(mv.get("meter")),
                startup_txt,
                active_txt,
                active2_txt,
                U.fmt_stun(mv.get("hitstun")),
                U.fmt_stun(mv.get("blockstun")),
                _fmt(mv.get("hitstop")),
                hb_main_txt,
                hb_txt,
                kb_txt,
                combo_txt,
                speed_txt,
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

        parent = insert_move_row(mv_list[0])
        win.tree.item(parent, open=False)
        win.tree.item(parent, tags=("group_parent",))

        for mv in mv_list[1:]:
            insert_move_row(mv, parent=parent)
