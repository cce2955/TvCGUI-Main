# fd_window.py
from __future__ import annotations
import struct

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import fd_utils as U
import fd_tree
from bonescan import BoneScanner
from config import INTERVAL

from fd_editors import FDCellEditorsMixin

from fd_patterns import (
    find_combo_kb_mod_addr,
    find_superbg_addr,
    find_speed_mod_addr,
    SUPERBG_ON,
)

from fd_write_helpers import (
    write_hit_reaction_inline,
    write_active2_frames_inline,
    write_combo_kb_mod_inline,
    write_superbg_inline,
    write_speed_mod_inline,
)

from tk_host import tk_call


class EditableFrameDataWindow(FDCellEditorsMixin):
    def __init__(self, master, slot_label, target_slot):
        self.master = master
        self.slot_label = slot_label
        self.target_slot = target_slot
        self._sort_state = {}  # col_name -> ascending bool

        # Preserve the raw scan order for optional view sorting.
        # Tag each move dict with a stable scan index so we can return to the scanner order.
        moves_scanned = list(target_slot.get("moves", []) or [])
        for i, mv in enumerate(moves_scanned):
            try:
                mv.setdefault("_scan_index", i)
            except Exception:
                pass

        def _mv_sort_key_notation(m):
            aid = m.get("id")
            if aid is None:
                group = 2
                aid_val = 0xFFFF
            else:
                aid_val = aid
                group = 0 if aid >= 0x100 else 1
            return (group, aid_val, m.get("abs", 0xFFFFFFFF))

        def _mv_sort_key_abs(m):
            a = m.get("abs")
            if a is None:
                return (1, 0xFFFFFFFF, m.get("_scan_index", 0))
            return (0, int(a), m.get("_scan_index", 0))

        moves_sorted = sorted(moves_scanned, key=_mv_sort_key_notation)
        moves_abs = sorted(moves_scanned, key=_mv_sort_key_abs)

        # Dedup indexing per anim ID (Tier1/2/3...)
        id_counts = {}
        for mv in moves_sorted:
            aid = mv.get("id")
            if aid is None:
                continue
            id_counts[aid] = id_counts.get(aid, 0) + 1

        id_seen = {}
        for mv in moves_sorted:
            aid = mv.get("id")
            if aid is None:
                continue
            if id_counts.get(aid, 0) > 1:
                idx = id_seen.get(aid, 0)
                mv["dup_index"] = idx
                id_seen[aid] = idx + 1

        # Keep all orderings available for view toggles (do not mutate move dicts).
        self._moves_notation = moves_sorted
        self._moves_scanned = moves_scanned
        self._moves_abs = moves_abs

        self.moves = moves_sorted
        self.move_to_tree_item: dict[str, dict] = {}
        self.original_moves: dict[int, dict] = {}
        self.next_abs_map: dict[int, int] = {}

        abs_list = sorted({mv.get("abs") for mv in self.moves if mv.get("abs")})
        for i in range(len(abs_list) - 1):
            self.next_abs_map[abs_list[i]] = abs_list[i + 1]

        self._row_counter = 0
        self._all_item_ids: list[str] = []
        self._detached: set[str] = set()

        self._filter_var: tk.StringVar | None = None
        self._status_var: tk.StringVar | None = None
        self._writer_var: tk.StringVar | None = None

        # Per-column filter vars (populated in fd_tree.build_tree_widget)
        self._col_filter_vars: dict[str, tk.StringVar] = {}
        self._col_filter_after_id = None

        self.root: tk.Toplevel | None = None
        self.tree: ttk.Treeview | None = None

        self._build()
    def _reset_to_original_grouping(self):
        # Clear sort state so arrows don’t lie
        self._sort_state.clear()
        # Reset headers (remove ▲▼)
        for c in self.tree["columns"]:
            base = self.tree.heading(c, "text").split(" ")[0]
            self.tree.heading(c, text=base)

            # Rebuild in original notation order
        self.sort_by_notation_order()


    # ---------- UI build ----------

    def _build(self):
        cname = self.target_slot.get("char_name", "-")

        self.root = tk.Toplevel(self.master)
        self.root.title(f"Frame Data Editor: {self.slot_label} ({cname})")
        self.root.geometry("1700x820")
        self.root.minsize(1280, 640)

        self._filter_var = tk.StringVar(master=self.root)
        self._status_var = tk.StringVar(master=self.root, value="Ready")
        self._writer_var = tk.StringVar(
            master=self.root,
            value=("Writable (writes to Dolphin)" if U.WRITER_AVAILABLE else "Read-only (move_writer missing)"),
        )

        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        fd_tree.configure_styles(self.root)
        fd_tree.build_top_bar(self)
        bones_bar = ttk.Frame(self.root)
        bones_bar.pack(side="top", fill="x", padx=6, pady=(2, 4))
        ttk.Button(
            bones_bar,
            text="Reset Order",
            command=self._reset_to_original_grouping,
        ).pack(side="left", padx=6)



        ttk.Button(
            bones_bar,
            text="Show Bones",
            command=self._show_bones,
        ).pack(side="left")
        fd_tree.build_tree_widget(self)
        fd_tree.populate_tree(self)

        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        status = ttk.Frame(self.root, style="Status.TFrame")
        status.pack(side="bottom", fill="x")
        ttk.Label(status, textvariable=self._status_var, style="Status.TLabel").pack(side="left", padx=8, pady=4)


    def _show_bones(self):
        paused = False

        # Anchor = any address BEFORE the bones region
        anchor = self.target_slot.get("fighter_base")

        if not anchor:
            # fallback: derive anchor from move abs
            for mv in self.target_slot.get("moves", []):
                abs_addr = mv.get("abs")
                if abs_addr:
                    anchor = abs_addr & ~0xFFF
                    break

        if not anchor:
            messagebox.showerror("Bones", "No anchor address available for bonescan")
            return

        win = tk.Toplevel(self.root)
        win.title(f"Bones: {self.slot_label} @ 0x{anchor:08X}")
        win.geometry("980x520")


        top = ttk.Frame(win)
        top.pack(side="top", fill="x", padx=8, pady=(8, 4))

        ttk.Label(top, text="Scan start offset (hex):").pack(side="left")
        off_var = tk.StringVar(value="0x3000")
        ttk.Entry(top, textvariable=off_var, width=10).pack(side="left", padx=(6, 18))

        ttk.Label(top, text="Scan length (hex):").pack(side="left")
        len_var = tk.StringVar(value="0x5000")
        ttk.Entry(top, textvariable=len_var, width=10).pack(side="left", padx=(6, 18))

        ttk.Label(top, text="Budget blocks/tick:").pack(side="left")
        bud_var = tk.StringVar(value="128")
        ttk.Entry(top, textvariable=bud_var, width=6).pack(side="left", padx=(6, 18))

        apply_btn = ttk.Button(top, text="Apply")
        apply_btn.pack(side="left")

        tree = ttk.Treeview(
            win,
            columns=("addr", "float_ok", "changes", "pat", "score", "sample"),
            show="headings",
        )
        tree.heading("addr", text="Addr")
        tree.heading("float_ok", text="Plausible Floats")
        tree.heading("changes", text="Changes")
        tree.heading("pat", text="Pattern Hits")
        tree.heading("score", text="Score")
        tree.heading("sample", text="Sample (first 8 floats)")
        
        tree.column("addr", width=120, anchor="center")
        tree.column("float_ok", width=120, anchor="center")
        tree.column("changes", width=80, anchor="center")
        tree.column("pat", width=95, anchor="center")
        tree.column("score", width=80, anchor="center")
        tree.column("sample", width=430, anchor="w")
        # ---- sortable column headers with indicators ----
        _sort_state = {}       # col -> ascending bool
        _sort_active = None    # currently sorted column

        def sort_bones_column(tree, col):
            nonlocal _sort_active, paused   # <-- THIS IS THE FIX

            paused = True

            asc = _sort_state.get(col, True)
            _sort_state[col] = not asc
            _sort_active = col

            rows = [(tree.set(i, col), i) for i in tree.get_children("")]

            def cast(v):
                if col == "addr":
                    return int(v, 16)
                try:
                    return float(v)
                except Exception:
                    return v

            rows.sort(key=lambda x: cast(x[0]), reverse=not asc)

            for idx, (_, item) in enumerate(rows):
                tree.move(item, "", idx)

            # update header indicators
            for c in ("addr", "float_ok", "changes", "pat", "score", "sample"):
                base = tree.heading(c, "text").split(" ")[0]
                if c == col:
                    arrow = "▲" if asc else "▼"
                    tree.heading(c, text=f"{base} {arrow}")
                else:
                    tree.heading(c, text=base)


        for col in ("addr", "float_ok", "changes", "pat", "score", "sample"):
            tree.heading(
                col,
                text=tree.heading(col, "text"),
                command=lambda c=col: sort_bones_column(tree, c),
            )

        tree.pack(fill="both", expand=True, padx=8, pady=8)
        
        status = ttk.Label(win, text="", anchor="w")
        status.pack(side="bottom", fill="x", padx=8, pady=(0, 8))

        scanner = None
        def edit_bone_block(addr: int):
            try:
                from dolphin_io import rbytes, wbytes
            except Exception:
                messagebox.showerror("Bones", "dolphin_io write unavailable")
                return

            raw = rbytes(addr, 0x40)
            if not raw:
                messagebox.showerror("Bones", f"Failed to read 0x{addr:08X}")
                return

            floats = [struct.unpack(">f", raw[i:i+4])[0] for i in range(0, 32, 4)]

            dlg = tk.Toplevel(win)
            dlg.title(f"Edit Bones @ 0x{addr:08X}")
            dlg.geometry("360x320")

            entries = []

            for i, val in enumerate(floats):
                row = ttk.Frame(dlg)
                row.pack(fill="x", padx=8, pady=2)

                ttk.Label(row, text=f"f{i}", width=4).pack(side="left")
                e = ttk.Entry(row, width=12)
                e.insert(0, f"{val:.3f}")
                e.pack(side="left", padx=6)
                entries.append(e)

            def apply():
                out = bytearray(raw)
                for i, e in enumerate(entries):
                    try:
                        v = float(e.get())
                        out[i*4:i*4+4] = struct.pack(">f", v)
                    except Exception:
                        pass
                wbytes(addr, out)

            ttk.Button(dlg, text="Apply", command=apply).pack(pady=10)

        def on_double_click(evt):
            item = tree.identify_row(evt.y)
            col = tree.identify_column(evt.x)
            if not item:
                return

            values = tree.item(item, "values")
            if not values:
                return

            addr = int(values[0], 16)
            edit_bone_block(addr)

        tree.bind("<Double-Button-1>", on_double_click)
        def on_click(evt):
            nonlocal paused
            region = tree.identify_region(evt.x, evt.y)
            if region == "heading":
                paused = True
            elif region == "cell":
                paused = True

        tree.bind("<Button-1>", on_click)
        def resume():
            nonlocal paused
            paused = False

        ttk.Button(top, text="Resume Scan", command=resume).pack(side="left", padx=8)

        def _parse_hex(s: str, default: int) -> int:
            try:
                s = (s or "").strip().lower()
                if not s:
                    return default
                if s.startswith("0x"):
                    return int(s, 16)
                return int(s, 16)
            except Exception:
                return default

        def _parse_int(s: str, default: int) -> int:
            try:
                return int((s or "").strip())
            except Exception:
                return default

        def rebuild_scanner():
            nonlocal scanner
            start_off = _parse_hex(off_var.get(), 0x3000)
            scan_len = _parse_hex(len_var.get(), 0x5000)

            scanner = BoneScanner(
                anchor,
                start_off=start_off,
                scan_len=scan_len,
                align=0x10,
                block_len=0x60,
                max_results=256,
            )

            status.config(
                text=f"Scanning 0x{anchor+start_off:08X} .. 0x{anchor+start_off+scan_len:08X}"
            )

        def on_apply():
            rebuild_scanner()

        apply_btn.config(command=on_apply)
        rebuild_scanner()

        def tick():
            if not win.winfo_exists():
                return

            if paused or scanner is None:
                win.after(int(INTERVAL * 1000), tick)
                return

            budget = _parse_int(bud_var.get(), 128)
            prev_count = getattr(scanner, "_last_result_count", 0)
            scanner.step(budget_blocks=max(16, min(2048, budget)))

            if len(scanner.results) == prev_count:
                win.after(int(INTERVAL * 1000), tick)
                return

            scanner._last_result_count = len(scanner.results)

            tree.delete(*tree.get_children())
            for r in scanner.results[:96]:
                samp = ", ".join(f"{x:+.3f}" for x in (r.sample or ()))
                tree.insert(
                    "",
                    "end",
                    values=(
                        f"0x{r.addr:08X}",
                        r.float_count,
                        r.change_count,
                        r.pattern_hits,
                        f"{r.score:.2f}",
                        samp,
                    ),
                )

            win.after(int(INTERVAL * 1000), tick)

        tick()

    # ---------- Row tagging / status ----------

    def _apply_row_tags(self, item_id: str, mv: dict):
        tags = set(self.tree.item(item_id, "tags") or ())

        kb_txt = self.tree.set(item_id, "kb")
        if kb_txt.strip():
            tags.add("kb_hot")

        combo_txt = self.tree.set(item_id, "combo_kb_mod")
        if combo_txt.strip() and combo_txt.strip() != "?":
            tags.add("combo_hot")

        speed_txt = self.tree.set(item_id, "speed_mod").strip()
        if speed_txt:
            tags.add("combo_hot")

        super_txt = self.tree.set(item_id, "superbg").strip()
        if super_txt == "ON":
            tags.add("super_on")

        abs_txt = self.tree.set(item_id, "abs").strip()
        if not abs_txt:
            tags.add("missing_addr")

        self.tree.item(item_id, tags=tuple(tags))

    def _set_status_for_item(self, item_id: str, mv: dict):
        aid = mv.get("id")
        abs_addr = mv.get("abs")
        kind = mv.get("kind", "")
        move_txt = self.tree.set(item_id, "move")
        s = []
        if move_txt:
            s.append(move_txt)
        if kind:
            s.append(f"Kind={kind}")
        if aid is not None:
            s.append(f"Anim=0x{aid:04X}")
        if abs_addr:
            s.append(f"Abs=0x{abs_addr:08X}")
        self._status_var.set(" | ".join(s) if s else "Ready")

    def _on_select(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            self._status_var.set("Ready")
            return
        item = sel[0]
        mv = self.move_to_tree_item.get(item)
        if not mv:
            self._status_var.set("Ready")
            return
        self._set_status_for_item(item, mv)

    # ---------- Expand/collapse/filter ----------

    def _expand_all(self):
        for item in self.tree.get_children(""):
            self.tree.item(item, open=True)

    def _collapse_all(self):
        for item in self.tree.get_children(""):
            self.tree.item(item, open=False)

    def _rebuild_tree_with_moves(self, moves, label: str):
        """Rebuild the Treeview using a different ordering of the same move dicts."""
        if not self.tree:
            return

        # Preserve filter state
        try:
            global_q = self._filter_var.get() if self._filter_var is not None else ""
        except Exception:
            global_q = ""
        try:
            col_q = {k: v.get() for k, v in (self._col_filter_vars or {}).items()}
        except Exception:
            col_q = {}

        # Make it visually obvious we're doing work (and flush UI)
        try:
            if self.root:
                self.root.config(cursor="watch")
                self.root.update_idletasks()
        except Exception:
            pass

        # Swap move list + rebuild helper maps
        self.moves = list(moves)

        self.next_abs_map.clear()
        abs_list = sorted({mv.get("abs") for mv in self.moves if mv.get("abs")})
        for i in range(len(abs_list) - 1):
            self.next_abs_map[abs_list[i]] = abs_list[i + 1]

        # Clear the existing tree content
        for child in self.tree.get_children(""):
            self.tree.delete(child)

        self.move_to_tree_item.clear()
        self._all_item_ids.clear()
        self._detached.clear()
        self._row_counter = 0

        # Repopulate (this now reuses cached hitbox candidates in fd_tree.py)
        fd_tree.populate_tree(self)

        # Force top so you SEE the reorder immediately
        try:
            self.tree.yview_moveto(0.0)
        except Exception:
            pass

        # Restore filter state + reapply
        if self._filter_var is not None:
            self._filter_var.set(global_q)
        for k, val in col_q.items():
            if k in self._col_filter_vars:
                try:
                    self._col_filter_vars[k].set(val)
                except Exception:
                    pass

        self._apply_filter()

        try:
            if self._status_var is not None:
                self._status_var.set(f"Sorted: {label}")
        finally:
            try:
                if self.root:
                    self.root.config(cursor="")
                    self.root.update_idletasks()
            except Exception:
                pass

    def sort_by_notation_order(self):
        self._rebuild_tree_with_moves(self._moves_notation, "notation order")

    def sort_by_scanned_order(self):
        self._rebuild_tree_with_moves(self._moves_scanned, "scanned order")

    def sort_by_abs_order(self):
        self._rebuild_tree_with_moves(self._moves_abs, "abs order")
    def _sort_treeview_grouped(self, col_name: str):
        tree = self.tree
        if not tree:
            return

        asc = self._sort_state.get(col_name, True)
        self._sort_state[col_name] = not asc

        # Header arrows
        for c in tree["columns"]:
            base = tree.heading(c, "text").split(" ")[0]
            tree.heading(
                c,
                text=f"{base} {'▲' if c == col_name and asc else '▼' if c == col_name else ''}".strip()
            )

        parents = list(tree.get_children(""))

        def parent_key(item):
            v = tree.set(item, col_name)
            if not v:
                return ""
            return v.lower()

        parents.sort(key=parent_key, reverse=not asc)

        for idx, parent in enumerate(parents):
            tree.move(parent, "", idx)
    def _sort_treeview_only(self, col_name: str):
        tree = self.tree
        if not tree:
            return

        asc = self._sort_state.get(col_name, True)
        self._sort_state[col_name] = not asc

        # Header arrows
        for c in tree["columns"]:
            base = tree.heading(c, "text").split(" ")[0]
            tree.heading(
                c,
                text=f"{base} {'▲' if c == col_name and asc else '▼' if c == col_name else ''}".strip()
            )

        rows = []
        for item in tree.get_children(""):
            val = tree.set(item, col_name)
            rows.append((val, item))

        def key(v):
            """
            Return a comparable tuple:
            (type_rank, value)
            type_rank ensures all comparisons are valid.
            """
            if v is None or v == "":
                return (2, "")  # empty last

            # abs column is hex
            if col_name == "abs":
                try:
                    return (0, int(v, 16))
                except Exception:
                    return (2, "")

            # numeric
            try:
                return (0, float(v))
            except Exception:
                pass

            # string fallback
            return (1, str(v).lower())

        rows.sort(key=lambda x: key(x[0]), reverse=not asc)

        for idx, (_, item) in enumerate(rows):
            tree.move(item, "", idx)

    def _on_sort_column(self, col_name: str):
        # Toggle direction per column
        if col_name in ("move", "kind"):
            self._sort_treeview_grouped(col_name)
            return
        else:
            self._sort_treeview_only(col_name)
            return

    def _safe_detach(self, item_id: str) -> bool:
        if item_id in self._detached:
            return False
        try:
            self.tree.detach(item_id)
            self._detached.add(item_id)
            return True
        except Exception:
            return False

    def _reattach_all(self):
        if not self._detached:
            return
        for item_id in list(self._detached):
            parent = self.tree.parent(item_id)
            try:
                self.tree.reattach(item_id, parent, "end")
            except Exception:
                pass
            finally:
                self._detached.discard(item_id)

    def _clear_col_filters(self):
        if getattr(self, "_col_filter_vars", None):
            for _c, var in self._col_filter_vars.items():
                try:
                    var.set("")
                except Exception:
                    pass
        self._apply_filter()

    def _clear_filter(self):
        # Clears both global and per-column
        if self._filter_var is not None:
            self._filter_var.set("")
        self._clear_col_filters()

    def _apply_filter(self):
        global_q = (self._filter_var.get() or "").strip().lower()

        # Per-column filters (ANDed)
        col_filters: dict[str, str] = {}
        for col, var in (getattr(self, "_col_filter_vars", {}) or {}).items():
            v = (var.get() or "").strip().lower()
            if v:
                col_filters[col] = v

        self._reattach_all()

        if not global_q and not col_filters:
            self._status_var.set("Filter cleared")
            return

        keep: set[str] = set()

        # Global filter searches only these columns
        global_cols = ("move", "kind", "abs")

        for item_id in self._all_item_ids:
            # 1) Global filter check
            if global_q:
                hay_parts = []
                for c in global_cols:
                    try:
                        hay_parts.append((self.tree.set(item_id, c) or "").lower())
                    except Exception:
                        hay_parts.append("")
                hay = " ".join(hay_parts)
                if global_q not in hay:
                    continue

            # 2) Column filters check
            ok = True
            for c, needle in col_filters.items():
                try:
                    cell = (self.tree.set(item_id, c) or "").lower()
                except Exception:
                    cell = ""
                if needle not in cell:
                    ok = False
                    break
            if not ok:
                continue

            keep.add(item_id)
            parent = self.tree.parent(item_id)
            while parent:
                keep.add(parent)
                parent = self.tree.parent(parent)

        detached = 0
        for item_id in self._all_item_ids:
            if item_id not in keep:
                if self._safe_detach(item_id):
                    detached += 1

        parts = []
        if global_q:
            parts.append(f"q='{global_q}'")
        if col_filters:
            parts.append("cols=" + ", ".join(f"{k}:{v}" for k, v in col_filters.items()))
        self._status_var.set(f"Filter applied ({' | '.join(parts)}), hidden {detached}")

    # ---------- Speed modifier resolve + edit ----------

    def _ensure_speed_mod(self, mv) -> None:
        if mv.get("speed_mod_addr") is not None:
            return
        move_abs = mv.get("abs")
        if not move_abs:
            return
        try:
            from dolphin_io import rbytes
            addr, cur, sig = find_speed_mod_addr(move_abs, rbytes)
        except Exception:
            addr, cur, sig = (None, None, None)
        if addr:
            mv["speed_mod_addr"] = addr
            mv["speed_mod"] = cur
            mv["speed_mod_sig"] = sig

    def _edit_speed_mod(self, item, mv, current: str):
        self._ensure_speed_mod(mv)
        addr = mv.get("speed_mod_addr")
        if not addr:
            messagebox.showerror(
                "Speed Modifier",
                "Signature not found for this move.\nTry Refresh visible, or this move may not have the pattern.",
            )
            return

        cur_val = mv.get("speed_mod")
        if cur_val is None:
            try:
                cur_val = int(str(current).split()[0])
            except Exception:
                cur_val = 0

        new_val = simpledialog.askinteger(
            "Edit Speed Modifier",
            f"New speed modifier byte (0-255)\nAddr: 0x{addr:08X}",
            initialvalue=int(cur_val),
            minvalue=0,
            maxvalue=255,
        )
        if new_val is None:
            return

        if write_speed_mod_inline(mv, int(new_val), U.WRITER_AVAILABLE):
            mv["speed_mod"] = int(new_val)
            self.tree.set(item, "speed_mod", U.fmt_speed_mod_ui(new_val))
        else:
            messagebox.showerror("Speed Modifier", "Failed to write speed modifier byte.")

    # ---------- Refresh visible ----------

    def _refresh_visible(self):
        refreshed = 0
        for item_id in self._all_item_ids:
            if item_id in self._detached:
                continue
            mv = self.move_to_tree_item.get(item_id)
            if not mv:
                continue
            move_abs = mv.get("abs")
            if not move_abs:
                continue

            hb_cands = U.scan_hitbox_candidates(move_abs)
            hb_off, hb_val = U.select_primary_hitbox(hb_cands)
            mv["hb_candidates"] = hb_cands
            mv["hb_off"] = hb_off
            mv["hb_r"] = hb_val
            self.tree.set(item_id, "hb", U.format_candidate_list(hb_cands))
            self.tree.set(item_id, "hb_main", (f"{hb_val:.1f}" if hb_val is not None else ""))

            # combo kb mod (lazy resolve)
            if mv.get("combo_kb_mod_addr") is None:
                try:
                    from dolphin_io import rbytes
                    addr, cur, sig = find_combo_kb_mod_addr(move_abs, rbytes)
                except Exception:
                    addr, cur, sig = (None, None, None)
                if addr:
                    mv["combo_kb_mod_addr"] = addr
                    mv["combo_kb_mod"] = cur
                    mv["combo_kb_sig"] = sig
            if mv.get("combo_kb_mod_addr"):
                v = mv.get("combo_kb_mod")
                self.tree.set(item_id, "combo_kb_mod", f"{v} (0x{v:02X})" if v is not None else "?")

            # speed mod
            if mv.get("speed_mod_addr") is None:
                try:
                    from dolphin_io import rbytes
                    saddr, sval, ssig = find_speed_mod_addr(move_abs, rbytes)
                except Exception:
                    saddr, sval, ssig = (None, None, None)
                if saddr:
                    mv["speed_mod_addr"] = saddr
                    mv["speed_mod"] = sval
                    mv["speed_mod_sig"] = ssig
            if mv.get("speed_mod_addr"):
                self.tree.set(item_id, "speed_mod", U.fmt_speed_mod_ui(mv.get("speed_mod")))

            # projectile refresh
            if mv.get("proj_dmg") is None and mv.get("proj_tpl") is None:
                try:
                    U.resolve_projectile_fields_for_move(mv, region_abs=move_abs)
                    self.tree.set(item_id, "proj_dmg", mv.get("proj_dmg") or "")
                    self.tree.set(
                        item_id,
                        "proj_tpl",
                        f"0x{mv['proj_tpl']:08X}" if mv.get("proj_tpl") else ""
                    )
                except Exception:
                    pass

            # superbg (lazy resolve)
            if mv.get("superbg_addr") is None:
                try:
                    from dolphin_io import rbytes, rd8
                    saddr, sval = find_superbg_addr(move_abs, rbytes, rd8)
                except Exception:
                    saddr, sval = (None, None)
                if saddr:
                    mv["superbg_addr"] = saddr
                    mv["superbg_val"] = sval
            if mv.get("superbg_addr"):
                self.tree.set(item_id, "superbg", U.fmt_superbg(mv.get("superbg_val")))

            self._apply_row_tags(item_id, mv)
            refreshed += 1

        self._status_var.set(f"Refreshed {refreshed} visible rows")

    # ---------- Reset to original ----------

    def _reset_all_moves(self):
        if not U.WRITER_AVAILABLE:
            messagebox.showerror("Error", "Writer unavailable")
            return

        reset_count = 0
        failed_writes = []

        for item_id, mv in self.move_to_tree_item.items():
            abs_addr = mv.get("abs")
            if not abs_addr:
                continue
            orig = self.original_moves.get(abs_addr)
            if not orig:
                continue

            # Speed mod
            if orig.get("speed_mod_addr") and orig.get("speed_mod") is not None:
                mv["speed_mod_addr"] = orig["speed_mod_addr"]
                if write_speed_mod_inline(mv, orig["speed_mod"], U.WRITER_AVAILABLE):
                    mv["speed_mod"] = orig["speed_mod"]
                    self.tree.set(item_id, "speed_mod", U.fmt_speed_mod_ui(orig["speed_mod"]))
                    reset_count += 1
                else:
                    failed_writes.append(f"speed_mod @ 0x{abs_addr:08X}")

            self._apply_row_tags(item_id, mv)

        msg = f"Reset complete: {reset_count} writes successful"
        if failed_writes:
            msg += "\n\nFailed writes:\n" + "\n".join(failed_writes[:10])
        messagebox.showinfo("Reset", msg)

    # ---------- Double-click routing ----------

    def _on_double_click(self, event):
        if not U.WRITER_AVAILABLE:
            messagebox.showerror("Error", "Writer unavailable")
            return

        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return

        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not item or not column:
            return

        col_idx = int(column[1:]) - 1
        col_name = self.tree["columns"][col_idx]
        mv = self.move_to_tree_item.get(item)
        if not mv:
            return

        current_val = self.tree.set(item, col_name)

        if col_name == "move":
            self._show_move_edit_menu(event, item, mv)
            self._apply_row_tags(item, mv)
            self._set_status_for_item(item, mv)
            return

        if col_name == "speed_mod":
            self._edit_speed_mod(item, mv, current_val)
        else:
            self._route_standard_edit(col_name, item, mv, current_val)

        self._apply_row_tags(item, mv)
        self._set_status_for_item(item, mv)

    def _route_standard_edit(self, col_name: str, item: str, mv: dict, current_val: str) -> None:
        if col_name == "damage":
            self._edit_damage(item, mv, current_val)
        elif col_name == "meter":
            self._edit_meter(item, mv, current_val)
        elif col_name == "startup":
            self._edit_startup(item, mv, current_val)
        elif col_name == "active":
            self._edit_active(item, mv, current_val)
        elif col_name == "active2":
            self._edit_active2(item, mv, current_val)
        elif col_name == "hitstun":
            self._edit_hitstun(item, mv, current_val)
        elif col_name == "blockstun":
            self._edit_blockstun(item, mv, current_val)
        elif col_name == "proj_dmg":
            self._edit_proj_dmg(item, mv, current_val)
        elif col_name == "hitstop":
            self._edit_hitstop(item, mv, current_val)
        elif col_name == "hb_main":
            self._edit_hitbox_main(item, mv, current_val)
        elif col_name == "hb":
            self._edit_hitbox(item, mv, current_val)
        elif col_name == "kb":
            self._edit_knockback(item, mv, current_val)
        elif col_name == "combo_kb_mod":
            self._edit_combo_kb_mod(item, mv, current_val)
        elif col_name == "hit_reaction":
            self._edit_hit_reaction(item, mv, current_val)
        elif col_name == "superbg":
            self._toggle_superbg(item, mv)

    # ---------- Right-click tools ----------

    def _on_right_click(self, event):
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not item or not column:
            return

        mv = self.move_to_tree_item.get(item)
        if not mv:
            return

        col_idx = int(column[1:]) - 1
        col_name = self.tree["columns"][col_idx]

        menu = tk.Menu(self.root, tearoff=0)

        addr_map = {
            "damage": ("damage_addr", "Damage"),
            "meter": ("meter_addr", "Meter"),
            "active": ("active_addr", "Active"),
            "active2": ("active2_addr", "Active 2"),
            "hitstun": ("stun_addr", "Stun"),
            "blockstun": ("stun_addr", "Stun"),
            "hitstop": ("stun_addr", "Stun"),
            "kb": ("knockback_addr", "Knockback"),
            "combo_kb_mod": ("combo_kb_mod_addr", "Combo KB Mod"),
            "speed_mod": ("speed_mod_addr", "Speed Mod"),
            "superbg": ("superbg_addr", "SuperBG"),
            "hb_main": ("hb_off", "Hitbox"),
            "hb": ("hb_off", "Hitbox"),
            "abs": ("abs", "Move"),
        }

        if col_name in addr_map:
            addr_key, label = addr_map[col_name]
            addr = mv.get(addr_key)

            if addr_key == "hb_off":
                move_abs = mv.get("abs")
                if move_abs and addr is not None:
                    addr = move_abs + addr

            if addr_key == "speed_mod_addr" and not addr:
                self._ensure_speed_mod(mv)
                addr = mv.get("speed_mod_addr")
                if addr:
                    self.tree.set(item, "speed_mod", U.fmt_speed_mod_ui(mv.get("speed_mod")))

            if addr_key == "combo_kb_mod_addr" and not addr:
                move_abs = mv.get("abs")
                if move_abs:
                    try:
                        from dolphin_io import rbytes
                        daddr, cur, sig = find_combo_kb_mod_addr(move_abs, rbytes)
                    except Exception:
                        daddr, cur, sig = (None, None, None)
                    if daddr:
                        mv["combo_kb_mod_addr"] = daddr
                        mv["combo_kb_mod"] = cur
                        mv["combo_kb_sig"] = sig
                        addr = daddr
                        v = mv.get("combo_kb_mod")
                        self.tree.set(item, "combo_kb_mod", f"{v} (0x{v:02X})" if v is not None else "?")

            if addr_key == "superbg_addr" and not addr:
                move_abs = mv.get("abs")
                if move_abs:
                    try:
                        from dolphin_io import rbytes, rd8
                        saddr, sval = find_superbg_addr(move_abs, rbytes, rd8)
                    except Exception:
                        saddr, sval = (None, None)
                    if saddr:
                        mv["superbg_addr"] = saddr
                        mv["superbg_val"] = sval
                        addr = saddr
                        self.tree.set(item, "superbg", U.fmt_superbg(mv.get("superbg_val")))

            if addr:
                menu.add_command(label=f"Copy {label} Address (0x{addr:08X})", command=lambda: self._copy_address(addr))
                menu.add_command(label=f"Go to {label} Address", command=lambda: self._show_address_info(addr, f"{label} @ 0x{addr:08X}"))
            else:
                menu.add_command(label=f"No {label} Address", state="disabled")

        menu.add_separator()
        menu.add_command(label="View Raw Move Data", command=lambda: self._show_raw_data(mv))

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_address(self, addr: int):
        self.root.clipboard_clear()
        self.root.clipboard_append(f"0x{addr:08X}")
        messagebox.showinfo("Copied", f"0x{addr:08X} copied to clipboard")

    def _show_address_info(self, addr: int, title: str):
        LINE_SIZE = 16
        CONTEXT_LINES = 6

        try:
            from dolphin_io import rbytes
        except Exception:
            messagebox.showerror("Error", "dolphin_io.rbytes not available")
            return

        try:
            line_base = addr & ~(LINE_SIZE - 1)
            start = line_base - CONTEXT_LINES * LINE_SIZE
            if start < 0:
                start = 0
            total_lines = CONTEXT_LINES * 2 + 1
            length = total_lines * LINE_SIZE
            data = rbytes(start, length)
            if not data:
                messagebox.showerror("Error", f"Failed to read memory around 0x{addr:08X}")
                return
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read memory around 0x{addr:08X}:\n{e}")
            return

        current_line_index = (line_base - start) // LINE_SIZE

        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        dlg.geometry("700x460")

        txt = tk.Text(dlg, wrap="none", font=("Consolas", 10), bg="#0f1113", fg="#e8e8e8", insertbackground="#e8e8e8")
        txt.pack(fill="both", expand=True, padx=8, pady=8)

        txt.insert("end", "Legend: '>>' = line containing the selected address; 16 bytes per line.\n\n")

        for i in range(total_lines):
            off = i * LINE_SIZE
            chunk = data[off:off + LINE_SIZE]
            line_addr = start + off
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)

            prefix = ">>" if i == current_line_index else "  "
            txt.insert("end", f"{prefix} 0x{line_addr:08X}: {hex_part:<47} {ascii_part}\n")

        txt.config(state="disabled")

    def _show_raw_data(self, mv: dict):
        dlg = tk.Toplevel(self.root)
        dlg.title("Raw Move Data")
        dlg.geometry("680x560")

        frame = tk.Frame(dlg, bg="#101214")
        frame.pack(fill="both", expand=True, padx=8, pady=8)

        txt = tk.Text(frame, wrap="word", font=("Consolas", 10), bg="#0f1113", fg="#e8e8e8", insertbackground="#e8e8e8")
        vsb = tk.Scrollbar(frame, orient="vertical", command=txt.yview)
        txt.configure(yscrollcommand=vsb.set)
        txt.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        for k, v in sorted(mv.items()):
            if k.endswith("_addr") and v:
                txt.insert("end", f"{k}: 0x{v:08X}\n")
            else:
                txt.insert("end", f"{k}: {v}\n")

        txt.config(state="disabled")

    # ---------- Host integration ----------

    def show(self):
        return


def open_editable_frame_data_window(slot_label, scan_data):
    if not scan_data:
        return
    target = None
    for s in scan_data:
        if s.get("slot_label") == slot_label:
            target = s
            break
    if not target:
        return

    def create_window(master_root):
        EditableFrameDataWindow(master_root, slot_label, target)

    tk_call(create_window)
