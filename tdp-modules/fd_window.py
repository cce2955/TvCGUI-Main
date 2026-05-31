from __future__ import annotations
import json
import os
import sys
import struct
import threading
from datetime import datetime

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

import fd_utils as U
import fd_tree
import fd_projectile_integration as FPI
import fd_super_integration as FSI
try:
    import char_dumper
except Exception as _char_dumper_import_error:
    char_dumper = None
from bonescan import BoneScanner
from config import INTERVAL

from fd_editors import FDCellEditorsMixin
from fd_widgets import get_field_help, ask_integer_with_help

from fd_patterns import (
    find_superbg_addr,
    find_speed_mod_addr,
    find_attack_property_addr,
    find_hit_spark_addr,
    find_limb_stretch_packet,
    find_post_animation_link_addr,
    fmt_attack_property,
    parse_attack_property,
    ATTACK_PROPERTY_VALUES,
    SUPERBG_ON,
)

from fd_write_helpers import (
    write_hit_reaction_inline,
    write_active2_frames_inline,
    write_superbg_inline,
    write_speed_mod_inline,
    write_combo_kb_mod_inline,
    write_proj_dmg_inline,
    write_u32_field_inline,
    write_f32_field_inline,
)

from tk_host import tk_call


class EditableFrameDataWindow(FDCellEditorsMixin):
    def __init__(self, master, slot_label, target_slot):
        self.master = master
        self.slot_label = slot_label
        self.target_slot = target_slot
        self._profile_fast_path = bool((target_slot or {}).get("profile_fast_path"))
        self._profile_key = (target_slot or {}).get("profile_key")
        self._sort_state = {}  # col_name -> ascending bool
        self._assist_tables = None
        self._assist_table_count = ""

        # Preserve the raw scan order for optional view sorting.
        # Tag each move dict with a stable scan index so we can return to the scanner order.
        moves_scanned = list(target_slot.get("moves", []) or [])
        for i, mv in enumerate(moves_scanned):
            try:
                mv.setdefault("_scan_index", i)
            except Exception:
                pass

        def _mv_sort_key_notation(m):
            return self._notation_rank(m)


        def _mv_sort_key_abs(m):
            a = m.get("abs")
            if a is None:
                return (1, 0xFFFFFFFF, m.get("_scan_index", 0))
            return (0, int(a), m.get("_scan_index", 0))
        moves_sorted = sorted(moves_scanned, key=_mv_sort_key_notation)

        self._moves_notation = moves_sorted

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

        # Right-side selected-move inspector. fd_tree builds these widgets; the
        # window owns refresh/edit routing so the inspector can reuse the same
        # write-safe edit handlers as the grid.
        self._inspector_value_vars: dict[str, tk.StringVar] = {}
        self._inspector_value_widgets: dict[str, ttk.Label] = {}
        self._inspector_buttons: dict[str, ttk.Button] = {}
        self._inspector_title_var: tk.StringVar | None = None
        self._inspector_subtitle_var: tk.StringVar | None = None
        self._inspector_hint_var: tk.StringVar | None = None

        # Session edits. These do not change write behavior; they only let the
        # UI reflect edits immediately and reset only the values touched in this
        # editor session instead of walking the whole move list.
        self._dirty_cells: dict[tuple, dict] = {}
        self._pending_edit_snapshots: dict[tuple, dict] = {}
        self._dirty_row_items: set[str] = set()
        self._changed_count_var: tk.StringVar | None = None
        self._suppress_dirty_tracking = False

        # Combined projectile-table support.  Projectile records are scanned in
        # the background and appended into the same Treeview as frame-data rows.
        self._projectile_hits: list[dict] = []
        self._projectile_scanning = False
        self._projectile_status_var: tk.StringVar | None = None
        self._super_hits: list[dict] = []
        self._super_scanning = False
        self._super_status_var: tk.StringVar | None = None
        self._char_dumping = False

        # Performance mode.  Opening the frame-data editor should create the
        # window first, then load rows/scans after Tk has painted.  Heavy
        # optional probes are lazy by default and can still be populated by the
        # Refresh button.
        self._initial_tree_loaded = False
        self._initial_load_running = False
        self._fd_eager_deep_probe = False
        self._auto_scans_enabled = False

        # Shareable frame-data patch config support. A saved patch stores only
        # changed values for this character and can be merged into the same JSON
        # file as other characters. Loading applies only the section matching
        # the currently open character window.
        self._last_patch_config_path: str | None = None

        self._build()
    def _reset_to_original_grouping(self):
        # Clear sort state so arrows do not lie
        self._sort_state.clear()

        # Reset headers (remove ASC/DESC)
        if self.tree:
            for c in self.tree["columns"]:
                base = self.tree.heading(c, "text").split(" ")[0]
                self.tree.heading(c, text=base)

        # Rebuild in original notation order
        self.sort_by_notation_order()

    def _move_order_text(self, mv):
        parts = []
        for key in ("pretty_name", "move_name", "name", "family_link_label", "family_label"):
            val = mv.get(key)
            if val:
                parts.append(str(val))
        aid = mv.get("id")
        if aid is not None:
            try:
                parts.append(str(U.pretty_move_name(aid, self.target_slot.get("char_name"))))
            except Exception:
                pass
        return " ".join(parts).lower()

    def _row_name_candidates(self, mv):
        """Names worth using for user-facing ordering.

        The scanner can see the same numeric animation ID through different
        lookup tables.  For ordering, prefer the label carried by the scanned row
        before family/link text, otherwise a reused special ID can drag j.B/j.C
        rows down into a special family.
        """
        out = []
        for key in ("move_name", "pretty_name", "name", "label", "_hit_parent_label"):
            val = mv.get(key)
            if val:
                out.append(str(val))
        try:
            aid = mv.get("id")
            if aid is not None:
                out.append(str(U.pretty_move_name(aid, self.target_slot.get("char_name"))))
        except Exception:
            pass
        return out

    @staticmethod
    def _compact_notation_text(text):
        return (
            str(text).lower()
            .replace(" ", "")
            .replace("_", "")
            .replace("-", "")
            .replace(".", "")
            .replace("[", " ")
            .replace("]", " ")
        )

    def _normal_order_index(self, mv):
        checks = [
            ("5a", 0),
            ("2a", 1),
            ("5b", 2),
            ("2b", 3),
            ("6b", 4),
            ("5c", 5),
            ("2c", 6),
            ("6c", 7),
            ("4c", 8),
            ("3c", 9),
            ("ja", 10),
            ("jb", 11),
            ("jc", 12),
        ]

        # First pass: only the row's own names.  This prevents family/link labels
        # from making a special helper look like a normal, or vice versa.
        for text in self._row_name_candidates(mv):
            low = str(text).lower()
            if any(word in low for word in ("hado", "tatsu", "shoryu", "donkey", "super", "assist")):
                continue
            tokens = set()
            for raw in low.replace("[", " ").replace("]", " ").replace("(", " ").replace(")", " ").split():
                tokens.add(raw.strip().lower().replace(".", ""))
            compact = self._compact_notation_text(text)
            for token, idx in checks:
                if token in tokens or compact.startswith(token):
                    return idx

        # Fallback: full order text.  This catches rows that only carry one
        # display label, but still keeps specials out of the normal bucket.
        text = self._move_order_text(mv)
        if not any(word in text for word in ("hado", "tatsu", "shoryu", "donkey", "super", "assist")):
            tokens = set()
            for raw in text.replace("[", " ").replace("]", " ").replace("(", " ").replace(")", " ").split():
                tokens.add(raw.strip().lower().replace(".", ""))
            compact = self._compact_notation_text(text)
            for token, idx in checks:
                if token in tokens or compact.startswith(token):
                    return idx
        return None

    def _is_super_order_row(self, mv):
        aid = mv.get("id")
        text = self._move_order_text(mv)
        if "throw" in text or "thrown" in text or "taunt" in text:
            return False
        if mv.get("kind") == "super":
            return True
        if any(word in text for word in ("super", "hyper", "shinku", "shin shoryu", "shin sho")):
            return True
        try:
            # Raw high animation IDs include throws/reactions on some characters,
            # so only use this as a weak fallback when the scanner already calls
            # the row super-like.
            if aid is not None and int(aid) >= 0x160 and str(mv.get("kind") or "").lower() in {"super", "hyper"}:
                return True
        except Exception:
            pass
        return False

    def _is_taunt_order_row(self, mv):
        return "taunt" in self._move_order_text(mv)

    def _is_special_order_row(self, mv):
        if self._is_super_order_row(mv) or self._is_taunt_order_row(mv):
            return False
        text = self._move_order_text(mv)
        if mv.get("family_label") in {"Tatsu", "Hado", "Shoryu", "Donkey"}:
            return True
        if any(word in text for word in ("tatsu", "hado", "shoryu", "donkey")):
            return True
        aid = mv.get("id")
        try:
            if aid is not None and 0x130 <= int(aid) < 0x160:
                # Throws live near this range for some chars; keep them in the
                # catch-all bucket unless the name/family says special.
                if "throw" not in text and "thrown" not in text:
                    return True
        except Exception:
            pass
        return mv.get("kind") == "special" and "throw" not in text and "thrown" not in text

    def _is_named_order_row(self, mv):
        text = self._move_order_text(mv)
        if "anim_" in text or "filler" in text:
            return False
        src = str(mv.get("move_name_source") or "").lower()
        if src == "lookup":
            return True
        # A linked helper row should travel with its named family rather than
        # falling below every named special. The family header sort uses the best
        # member rank, so this mainly helps when the helper is selected/sorted by
        # explicit order directly.
        if mv.get("family_linkable") and mv.get("family_group_label"):
            return True
        return False

    def _family_name_for_order(self, mv):
        text = " ".join(
            str(v) for v in (
                mv.get("family_group_label"),
                mv.get("family_label"),
                mv.get("family_link_label"),
                mv.get("move_name"),
                mv.get("pretty_name"),
                mv.get("name"),
            ) if v
        ).lower()
        return text

    def _strength_order_index(self, mv):
        text = self._family_name_for_order(mv)
        for idx, token in enumerate((" l", " a", " m", " b", " h", " c")):
            if text.endswith(token) or f"{token} " in text:
                return idx // 2
        st = str(mv.get("family_strength") or mv.get("family_strength_guess") or "").upper()
        if st in {"L", "A"}:
            return 0
        if st in {"M", "B"}:
            return 1
        if st in {"H", "C"}:
            return 2
        return 9

    def _context_order_index(self, mv):
        text = self._family_name_for_order(mv)
        context = str(mv.get("family_context") or "").lower()
        if context == "assist" or "assist" in text:
            return 2
        if context == "air" or text.startswith("air ") or " air " in text:
            return 1
        return 0

    def _family_word_order(self, mv):
        """Readable special-family order before falling back to raw IDs.

        This keeps Ryu-style output as Hado, Tatsu, Shoryu, Donkey, then other
        named families, instead of alphabetizing Donkey before Hado.  Other
        characters mostly sort by their command IDs, but this word order makes
        linked helper sections stable when their command wrapper is missing.
        """
        text = self._family_name_for_order(mv)
        order = [
            ("hado", 0),
            ("hadou", 0),
            ("kikoken", 0),
            ("soul fist", 0),
            ("tatsu", 1),
            ("sbk", 1),
            ("spinning bird", 1),
            ("legs", 1),
            ("lightning", 1),
            ("shoryu", 2),
            ("tensho", 2),
            ("rising", 2),
            ("donkey", 3),
            ("bird run", 3),
            ("bird shoot", 4),
            ("eagle rush", 5),
        ]
        for word, rank in order:
            if word in text:
                return rank
        return 50

    def _order_anim_id(self, mv, *, prefer_command: bool = False):
        try:
            aid = int(mv.get("id")) if mv.get("id") is not None else None
        except Exception:
            aid = None
        if aid is None:
            return 0xFFFF
        if prefer_command:
            if 0x130 <= aid < 0x180:
                return aid
            # Internal/helper sections sort after their human command wrappers.
            return 0xF000 + aid
        return aid

    def _special_order_index(self, mv):
        return (
            self._family_word_order(mv),
            self._context_order_index(mv),
            self._strength_order_index(mv),
            self._order_anim_id(mv, prefer_command=True),
        )

    def _super_order_index(self, mv):
        text = self._family_name_for_order(mv)
        if any(word in text for word in ("shinkuu", "shinku", "air shinkuu", "air shinku")):
            base = 0
        elif "tatsu super" in text or ("tatsu" in text and "super" in text):
            base = 1
        elif "shin sho" in text or "shinsho" in text or "shin shoryu" in text:
            base = 2
        else:
            base = 20
        return (
            base,
            self._context_order_index(mv),
            self._strength_order_index(mv),
            self._order_anim_id(mv, prefer_command=True),
        )

    def _family_group_sort_key(self, members):
        """Sort a linked family as a family, not by its lowest stray child.

        A linked special can contain reused/nearby rows whose names look like
        j.B or j.C.  Those child labels should stay inside the family if the
        linker says so, but they must not pull Hado/Tatsu above j.C in the main
        workbench order.
        """
        members = list(members or [])
        if not members:
            return (9, 0xFFFFFFFF)

        special_members = [m for m in members if self._is_special_order_row(m)]
        super_members = [m for m in members if self._is_super_order_row(m)]
        taunt_members = [m for m in members if self._is_taunt_order_row(m)]

        if super_members and not special_members:
            candidates = [m for m in super_members if self._normal_order_index(m) is None] or super_members
            return min((2, 0 if self._is_named_order_row(m) else 1, *self._super_order_index(m), int(m.get("_scan_index") or 0), int(m.get("abs") or 0xFFFFFFFF)) for m in candidates)

        if special_members:
            candidates = [m for m in special_members if self._normal_order_index(m) is None] or special_members
            command_named = [
                m for m in candidates
                if self._is_named_order_row(m)
                and 0x130 <= self._order_anim_id(m) < 0x180
            ]
            if command_named:
                candidates = command_named
            return min((1, 0 if self._is_named_order_row(m) else 1, *self._special_order_index(m), int(m.get("_scan_index") or 0), int(m.get("abs") or 0xFFFFFFFF)) for m in candidates)

        if taunt_members:
            return min((3, 0 if self._is_named_order_row(m) else 1, self._order_anim_id(m), int(m.get("_scan_index") or 0), int(m.get("abs") or 0xFFFFFFFF)) for m in taunt_members)

        ranks = [self._explicit_notation(m) for m in members]
        return min(ranks) if ranks else (9, 0xFFFFFFFF)

    def _explicit_notation(self, mv):
        """Initial workbench order.

        User-facing order is not raw address order.  Keep core normals first in
        the requested fighting-game notation order, then specials, supers,
        taunt, and finally scouting/unknown rows.
        """
        normal_idx = self._normal_order_index(mv)
        aid = mv.get("id")
        try:
            aid_i = int(aid) if aid is not None else 0xFFFF
        except Exception:
            aid_i = 0xFFFF
        scan_i = int(mv.get("_scan_index") or 0)
        abs_i = int(mv.get("abs") or 0xFFFFFFFF)

        named_rank = 0 if self._is_named_order_row(mv) else 1

        if normal_idx is not None:
            return (0, normal_idx, aid_i, abs_i)
        if self._is_special_order_row(mv):
            return (1, named_rank, *self._special_order_index(mv), scan_i, abs_i)
        if self._is_super_order_row(mv):
            return (2, named_rank, *self._super_order_index(mv), scan_i, abs_i)
        if self._is_taunt_order_row(mv):
            return (3, named_rank, aid_i, scan_i, abs_i)
        return (4, named_rank, aid_i, scan_i, abs_i)

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
        self._changed_count_var = tk.StringVar(master=self.root, value="Changed: 0")
        self._projectile_status_var = tk.StringVar(master=self.root, value="Projectiles: queued")
        self._super_status_var = tk.StringVar(master=self.root, value="Supers: queued")

        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        fd_tree.configure_styles(self.root)
        fd_tree.build_top_bar(self)
        # Reset Order / Show Bones now live in the two-row top action bar so
        # they remain visible instead of being pushed onto a clipped gray strip.
        fd_tree.build_tree_widget(self)

        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        status = ttk.Frame(self.root, style="Status.TFrame")
        status.pack(side="bottom", fill="x")
        ttk.Label(status, textvariable=self._status_var, style="Status.TLabel").pack(side="left", padx=8, pady=4)

        # Paint the window before any heavy tree population or memory scanning.
        # Tk does not draw the Toplevel until control returns to the event loop,
        # so doing populate_tree() synchronously here made the Frame Data button
        # feel frozen for 20-30 seconds.
        try:
            self._status_var.set("Opening frame-data window... loading rows after paint")
            if self._projectile_status_var is not None:
                self._projectile_status_var.set("Projectiles: lazy; open Projectiles view or Rescan projectiles")
            if self._super_status_var is not None:
                self._super_status_var.set("Specials: lazy; open Supers view or Rescan specials")
            self.root.after(25, self._initial_populate_tree)
        except Exception:
            self._initial_populate_tree()
    def _initial_populate_tree(self):
        if self._initial_load_running or self._initial_tree_loaded:
            return
        self._initial_load_running = True
        try:
            if self._status_var is not None:
                src = "profile cache" if self._profile_fast_path else "scanner snapshot"
                self._status_var.set(f"Loading frame rows from {src}... optional probes are lazy")
            fd_tree.populate_tree(self)
            self._initial_tree_loaded = True
            if self._status_var is not None:
                src = "profile cache" if self._profile_fast_path else "scanner snapshot"
                self._status_var.set(f"Frame rows loaded from {src}. Projectiles/specials scan only when their view or rescan button is used.")
        except Exception as e:
            if self._status_var is not None:
                self._status_var.set(f"Frame rows failed to populate: {e}")
        finally:
            self._initial_load_running = False

    def _ensure_initial_tree_loaded(self):
        if not self._initial_tree_loaded:
            self._initial_populate_tree()

    def _notation_rank(self, mv):
        return self._explicit_notation(mv)



    # ---------- Combined projectile table ----------

    def _start_projectile_scan(self, auto: bool = False):
        try:
            self._ensure_initial_tree_loaded()
        except Exception:
            pass
        if auto and not self._auto_scans_enabled:
            if self._projectile_status_var is not None:
                self._projectile_status_var.set("Projectiles: lazy; click Projectiles or Rescan projectiles")
            return
        if auto and getattr(self, "_projectile_hits", None):
            if self._projectile_status_var is not None:
                self._projectile_status_var.set(f"Projectiles: {len(self._projectile_hits)} cached")
            return
        if self._projectile_scanning:
            if self._projectile_status_var is not None:
                self._projectile_status_var.set("Projectiles: scanning...")
            return

        cname = self.target_slot.get("char_name", "-")
        if not FPI.projectile_key_for_char(cname):
            if self._projectile_status_var is not None:
                self._projectile_status_var.set("Projectiles: no map for this character")
            return

        self._projectile_scanning = True
        if self._projectile_status_var is not None:
            self._projectile_status_var.set("Projectiles: scanning MEM2...")
        if self._status_var is not None:
            self._status_var.set("Scanning projectile definitions for this character...")

        def _progress(pct: float):
            try:
                if self.root and self._projectile_status_var is not None:
                    self.root.after(0, lambda p=pct: self._projectile_status_var.set(f"Projectiles: scanning {p:0.0f}%"))
            except Exception:
                pass

        def _worker():
            try:
                hits = FPI.scan_projectiles_for_char(cname, progress_cb=_progress, show_unknowns=False)
                err = None
            except Exception as e:
                hits = []
                err = e

            def _done():
                self._projectile_scanning = False
                if err is not None:
                    if self._projectile_status_var is not None:
                        self._projectile_status_var.set("Projectiles: scan failed")
                    if self._status_var is not None:
                        self._status_var.set(f"Projectile scan failed: {err}")
                    return

                self._projectile_hits = list(hits or [])
                try:
                    fd_tree.populate_projectile_rows(self, replace=True)
                except Exception as e2:
                    if self._status_var is not None:
                        self._status_var.set(f"Projectile rows failed to populate: {e2}")
                count = len(self._projectile_hits)
                if self._projectile_status_var is not None:
                    self._projectile_status_var.set(f"Projectiles: {count} loaded" if count else "Projectiles: none found")
                if self._status_var is not None:
                    self._status_var.set(f"Loaded {count} projectile record(s) into the frame-data table")

            try:
                self.root.after(0, _done)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _start_super_scan(self, auto: bool = False):
        try:
            self._ensure_initial_tree_loaded()
        except Exception:
            pass
        if auto and not self._auto_scans_enabled:
            if self._super_status_var is not None:
                self._super_status_var.set("Specials: lazy; click Supers or Rescan specials")
            return
        if auto and getattr(self, "_super_hits", None):
            if self._super_status_var is not None:
                self._super_status_var.set(f"Specials: {len(self._super_hits)} cached")
            return
        if self._super_scanning:
            if self._super_status_var is not None:
                self._super_status_var.set("Specials: scanning...")
            return

        cname = self.target_slot.get("char_name", "-")
        if not FSI.super_key_for_char(cname):
            if self._super_status_var is not None:
                self._super_status_var.set("Specials: no key for this character")
            return

        self._super_scanning = True
        if self._super_status_var is not None:
            self._super_status_var.set("Specials: scanning 00/23 callers...")
        if self._status_var is not None:
            self._status_var.set("Scanning action graph: dispatch rows, child links, and projectilemap matches...")

        def _progress(pct: float):
            try:
                if self.root and self._super_status_var is not None:
                    self.root.after(0, lambda p=pct: self._super_status_var.set(f"Supers: scanning {p:0.0f}%"))
            except Exception:
                pass

        # Reuse already-scanned projectile/payload rows when available so the
        # Super Finder can attach known beam/projectile payload fields without
        # forcing a duplicate projectile scan. If this snapshot is empty, the
        # super scanner falls back to its own payload pass.
        payload_snapshot = [dict(h) for h in list(getattr(self, "_projectile_hits", []) or [])]
        # Important perf rule: do not let the action/special scanner trigger a
        # full projectile scan as a fallback.  If projectiles have not been
        # scanned yet, pass an empty list and rely on the child-forward
        # projectilemap damage sniff.  Users can run Rescan projectiles when
        # they want template/payload fields attached too.

        def _worker():
            try:
                hits = FSI.scan_supers_for_char(
                    cname,
                    progress_cb=_progress,
                    payload_hits=payload_snapshot,
                    move_hits=list(getattr(self, "_moves_scanned", []) or getattr(self, "moves", []) or []),
                    attach_payloads=True,
                )
                err = None
            except Exception as e:
                hits = []
                err = e

            def _done():
                self._super_scanning = False
                if err is not None:
                    if self._super_status_var is not None:
                        self._super_status_var.set("Specials: scan failed")
                    if self._status_var is not None:
                        self._status_var.set(f"Super scan failed: {err}")
                    return

                self._super_hits = list(hits or [])
                try:
                    fd_tree.populate_super_rows(self, replace=True)
                except Exception as e2:
                    if self._status_var is not None:
                        self._status_var.set(f"Super rows failed to populate: {e2}")
                count = len(self._super_hits)
                payload_count = 0
                try:
                    payload_count = sum(int((h or {}).get("payload_count") or 0) for h in self._super_hits)
                except Exception:
                    payload_count = 0
                if self._super_status_var is not None:
                    self._super_status_var.set(f"Supers: {count} dispatch row(s), {payload_count} payload link(s)" if count else "Supers: none found")
                if self._status_var is not None:
                    self._status_var.set(f"Loaded {count} super dispatch row(s) with {payload_count} attached payload candidate(s)")

            try:
                self.root.after(0, _done)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()


    def _dump_character_data(self):
        """Read-only one-button character dump for move/projectile/super farming."""
        if self._char_dumping:
            if self._status_var is not None:
                self._status_var.set("Character dump already running...")
            return
        if char_dumper is None:
            messagebox.showerror(
                "Dump character",
                "char_dumper.py could not be imported. Make sure it is next to fd_window.py."
            )
            return

        self._char_dumping = True
        cname = self.target_slot.get("char_name", "-")
        if self._status_var is not None:
            self._status_var.set(f"Dumping {cname}: moves, command hits, projectiles, super candidates...")

        # Snapshot the Python objects before the worker starts. The dump helper
        # still reads live Dolphin memory, but it does not touch Tk widgets.
        target_snapshot = dict(self.target_slot or {})
        moves_snapshot = [dict(mv) for mv in list(self.moves or [])]
        target_snapshot["moves"] = moves_snapshot
        projectile_snapshot = [dict(h) for h in list(getattr(self, "_projectile_hits", []) or [])]

        def _worker():
            try:
                outdir = char_dumper.dump_character(
                    target_snapshot,
                    moves_snapshot,
                    projectile_snapshot,
                )
                err = None
            except Exception as e:
                outdir = ""
                err = e

            def _done():
                self._char_dumping = False
                if err is not None:
                    if self._status_var is not None:
                        self._status_var.set(f"Character dump failed: {err}")
                    messagebox.showerror("Dump character", f"Character dump failed:\n{err}")
                    return
                if self._status_var is not None:
                    self._status_var.set(f"Character dump written: {outdir}")
                messagebox.showinfo(
                    "Dump character",
                    "Character dump written:\n" + str(outdir) +
                    "\n\nFiles include character_dump.txt, character_dump.json, and raw chunk bins."
                )

            try:
                if self.root:
                    self.root.after(0, _done)
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()


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
                    arrow = "ASC" if asc else "DESC"
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
                absolute_start=0x92477400,
                absolute_end=0x94477500,
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

    # ---------- Inspector / display column helpers ----------

    def _tree_display_columns(self) -> list[str]:
        if not self.tree:
            return []
        all_cols = list(self.tree["columns"])
        try:
            display = self.tree["displaycolumns"]
        except Exception:
            return all_cols
        if not display or display == "#all" or display == ("#all",):
            return all_cols
        if isinstance(display, str):
            return [display]
        return list(display)

    def _resolve_tree_column_name(self, column: str) -> str | None:
        """Resolve Tk's #N display column back to the real data column.

        This matters because the workbench now defaults to a core column view
        using Treeview.displaycolumns. identify_column() reports the visible
        column index, not the original full-column index.
        """
        if not self.tree or not column or column == "#0":
            return None
        try:
            idx = int(str(column).lstrip("#")) - 1
        except Exception:
            return None
        display_cols = self._tree_display_columns()
        if idx < 0 or idx >= len(display_cols):
            return None
        return display_cols[idx]

    def _heading_text_for_col(self, col_name: str) -> str:
        labels = {
            "move": "Move",
            "kind": "Kind",
            "hits": "Hits",
            "damage": "Dmg",
            "meter": "Meter",
            "startup": "Start",
            "active": "Active",
            "active2": "Active 2",
            "hitstun": "HS",
            "blockstun": "BS",
            "hitstop": "Stop",
            "hit_spark": "Hit Spark",
            "stretch_part": "Stretch Part",
            "stretch_len": "Reach Length",
            "stretch_width": "Reach Width",
            "stretch_height": "Reach Height",
            "stretch_time": "Stretch Timing",
            "post_link": "Post Link",
            "kb_type": "KB Style",
            "launch_profile": "Extra Launch",
            "kb_unknown": "Launch Adjust",
            "kb_x": "KB X",
            "air_kb": "Arc",
            "speed_mod": "Speed",
            "invuln": "Invuln",
            "attack_property": "Attack Property",
            "hit_reaction": "Hit Reaction",
            "superbg": "SuperBG",
            **FPI.PROJECTILE_LABELS,
            "abs": "Address",
        }
        return labels.get(col_name, col_name)

    def _refresh_quick_panel(self, item_id: str | None = None, mv: dict | None = None):
        """Update the compact selection strip above the table.

        This replaces the always-visible Details column. The grid stays
        readable, while projectile/super-specific values are visible without
        horizontal scrolling or digging through the inspector.
        """
        frame = getattr(self, "_quick_chips_frame", None)
        if frame is None:
            return
        try:
            for child in list(frame.winfo_children()):
                child.destroy()
        except Exception:
            pass

        title_var = getattr(self, "_quick_title_var", None)
        sub_var = getattr(self, "_quick_subtitle_var", None)

        if not item_id or not mv or not getattr(self, "tree", None):
            try:
                if title_var is not None:
                    title_var.set("Selection details")
                if sub_var is not None:
                    sub_var.set("Select a move, projectile, or super row to see the values that matter here.")
            except Exception:
                pass
            return

        def _tree_val(col: str) -> str:
            try:
                val = self.tree.set(item_id, col)
            except Exception:
                val = ""
            return str(val or "").strip()

        def _clean_move_text(text: str) -> str:
            return str(text or "").strip()

        def _present(value) -> bool:
            s = str(value or "").strip().lower()
            return bool(s and s not in {"-", "not found", "none", "0x00000000"})

        move_txt = _clean_move_text(_tree_val("move") or mv.get("pretty_name") or mv.get("move_name") or "Selected row")
        kind = str(mv.get("kind") or _tree_val("kind") or "").strip()
        try:
            if title_var is not None:
                title_var.set(move_txt)
        except Exception:
            pass

        chips: list[tuple[str, str, str, str | None]] = []

        def add(label: str, value, *, important: bool = False, col: str | None = None):
            text = str(value or "").strip()
            if _present(text):
                chips.append((label, text, "important" if important else "normal", col))

        if FSI.is_super_row(mv):
            hit = mv.get("_super_hit") or {}
            try:
                if sub_var is not None:
                    sub_var.set("Super dispatch row | 00/23 caller | advanced")
            except Exception:
                pass
            add("Selector", FSI.format_super_value(mv, "dispatch_selector"), important=True, col="dispatch_selector")
            add("Variant", FSI.format_super_value(mv, "dispatch_variant"), col="dispatch_variant")
            add("Phase", FSI.format_super_value(mv, "dispatch_phase"), important=True, col="dispatch_phase")
            add("Child Link", FSI.format_super_value(mv, "dispatch_child_link"), col="dispatch_child_link")
            add("Target", FSI.format_super_value(mv, "dispatch_child_target"))
            add("Group", FSI.format_super_value(mv, "dispatch_group"))
            add("Confidence", FSI.format_super_value(mv, "dispatch_confidence"), important=True)
            add("Super Proof", FSI.format_super_value(mv, "dispatch_super_proof"), important=True)
            add("Owner Proof", FSI.format_super_value(mv, "dispatch_owner_proof"), important=bool(hit.get("dispatch_owner_proof")))
            for _label, _col in (
                ("Damage", "damage"),
                ("KB X", "kb_x"),
                ("KB Y", "air_kb"),
                ("Hitstun", "hitstun"),
                ("Blockstun", "blockstun"),
                ("Hitstop", "hitstop"),
                ("Attack Property", "attack_property"),
                ("Hit Reaction", "hit_reaction"),
                ("Extra Launch", "launch_profile"),
                ("Launch Adjust", "kb_unknown"),
            ):
                add(_label, FSI.format_super_value(mv, _col), important=_col in {"damage", "kb_x", "air_kb"}, col=_col)
            add("Owned Payloads", str(hit.get("payload_count") or ""), important=bool(hit.get("payload_count")))
            add("Owned Payload Summary", str(hit.get("payload_summary") or ""))
            add("Owned Script Fields", str(hit.get("owned_script_field_summary") or ""))
            add("Payload Fields", str(hit.get("payload_field_summary") or ""))
            add("Payload-Only Scout", str(hit.get("payload_only_summary") or ""))
            try:
                scout = FSI._scout_summary(hit)
            except Exception:
                scout = ""
            add("Child Scout", scout)
        elif FPI.is_projectile_row(mv):
            hit = mv.get("_proj_hit") or {}
            role = str(hit.get("proj_role") or "").strip()
            tier = str(hit.get("tier") or "").strip()
            total = str(hit.get("tier_total") or "").strip()
            tier_txt = f"{tier}/{total}" if tier and total else tier
            fmt = FPI.format_projectile_value(mv, "proj_fmt")
            is_emitter = bool(getattr(FPI, "is_projectile_emitter_row", lambda _mv: False)(mv))
            is_ps = bool(getattr(FPI, "is_projectile_super_card", lambda _mv: False)(mv))
            try:
                if sub_var is not None:
                    sub = "Projectile emitter" if is_emitter else ("Projectile-super card" if is_ps else "Projectile record")
                    if fmt:
                        sub += f" | {fmt}"
                    if role:
                        sub += f" | {role}"
                    sub_var.set(sub)
            except Exception:
                pass
            add("Damage", FPI.format_projectile_value(mv, "damage"), important=True, col="damage")

            if is_emitter:
                add("Cards", FPI.format_projectile_value(mv, "proj_emit_count"), important=True, col="proj_emit_count")
                add("KB X", FPI.format_projectile_value(mv, "kb_x"), important=True, col="kb_x")
                add("KB Y", FPI.format_projectile_value(mv, "air_kb") or FPI.format_projectile_value(mv, "proj_kb_y"), important=True, col="air_kb")
                add("Life", FPI.format_projectile_value(mv, "proj_ps_lifetime") or FPI.format_projectile_value(mv, "proj_life"), important=True, col="proj_ps_lifetime")
                add("Origin", FPI.format_projectile_value(mv, "proj_spawn_origin"), important=True, col="proj_spawn_origin")
                add("Hits", FPI.format_projectile_value(mv, "proj_ps_hit_count"), col="proj_ps_hit_count")
                add("Interval", FPI.format_projectile_value(mv, "proj_ps_interval"), col="proj_ps_interval")
                add("Speed", FPI.format_projectile_value(mv, "proj_speed"), important=True, col="proj_speed")
                add("Accel", FPI.format_projectile_value(mv, "proj_accel"), col="proj_accel")
                add("Scale", FPI.format_projectile_value(mv, "proj_ps_scale"), important=True, col="proj_ps_scale")
                add("FX", FPI.format_projectile_value(mv, "proj_ps_particle_fx"), col="proj_ps_particle_fx")
                add("Proj ID", FPI.format_projectile_value(mv, "proj_ps_projectile_id"), col="proj_ps_projectile_id")
                add("Bone", FPI.format_projectile_value(mv, "proj_ps_spawn_bone"), col="proj_ps_spawn_bone")
            elif is_ps:
                add("Card", FPI.format_projectile_value(mv, "proj_ps_card_type"), col="proj_ps_card_type")
                add("Life", FPI.format_projectile_value(mv, "proj_ps_lifetime"), important=True, col="proj_ps_lifetime")
                add("Hits", FPI.format_projectile_value(mv, "proj_ps_hit_count"), important=True, col="proj_ps_hit_count")
                add("Emit", FPI.format_projectile_value(mv, "proj_ps_emit_count"), col="proj_ps_emit_count")
                add("Interval", FPI.format_projectile_value(mv, "proj_ps_interval"), col="proj_ps_interval")
                add("Mode", FPI.format_projectile_value(mv, "proj_ps_mode"), col="proj_ps_mode")
                add("Spawn X", FPI.format_projectile_value(mv, "proj_ps_offset_x"), col="proj_ps_offset_x")
                add("Spawn Y", FPI.format_projectile_value(mv, "proj_ps_offset_y"), col="proj_ps_offset_y")
                add("Scale", FPI.format_projectile_value(mv, "proj_ps_scale"), important=True, col="proj_ps_scale")
                add("FX", FPI.format_projectile_value(mv, "proj_ps_particle_fx"), col="proj_ps_particle_fx")
                add("Proj ID", FPI.format_projectile_value(mv, "proj_ps_projectile_id"), col="proj_ps_projectile_id")
                add("Bone", FPI.format_projectile_value(mv, "proj_ps_spawn_bone"), col="proj_ps_spawn_bone")
            else:
                add("ID", FPI.format_projectile_value(mv, "proj_id"))
                add("Type", FPI.format_projectile_value(mv, "proj_type"))
                add("Tier", tier_txt)
                add("Life", FPI.format_projectile_value(mv, "proj_life"))
                add("Origin", FPI.format_projectile_value(mv, "proj_spawn_origin"), important=True, col="proj_spawn_origin")
                add("Speed", FPI.format_projectile_value(mv, "proj_speed"), important=True, col="proj_speed")
                add("Accel", FPI.format_projectile_value(mv, "proj_accel"), col="proj_accel")
                add("Radius", FPI.format_projectile_value(mv, "proj_radius"), important=True, col="proj_radius")
                add("FX", FPI.format_projectile_value(mv, "proj_fx"), important=True, col="proj_fx")
                add("Hitbox", FPI.format_projectile_value(mv, "proj_hitbox"), col="proj_hitbox")
                kx = FPI.format_projectile_value(mv, "kb_x")
                ky = FPI.format_projectile_value(mv, "proj_kb_y") or FPI.format_projectile_value(mv, "air_kb")
                add("KB X", kx, important=True, col="kb_x")
                add("KB Y", ky, important=True, col="air_kb")
                add("Arc", FPI.format_projectile_value(mv, "proj_arc"), col="proj_arc")
                add("Arc 2", FPI.format_projectile_value(mv, "proj_arc2"))

            # Shinkuu/Kikosho/Disco Ball-style super-beam cards keep their
            # separate controls. Compact 00/23 cards do not use these fields.
            add("Lifetime", FPI.format_projectile_value(mv, "proj_super_lifetime"), important=True, col="proj_super_lifetime")
            add("Hit Count", FPI.format_projectile_value(mv, "proj_super_hit_count"), important=True, col="proj_super_hit_count")
            add("Interval", FPI.format_projectile_value(mv, "proj_super_hit_interval"), col="proj_super_hit_interval")
            add("FX", FPI.format_projectile_value(mv, "proj_super_particle_fx"), col="proj_super_particle_fx")
            add("Spawn Bone", FPI.format_projectile_value(mv, "proj_super_spawn_bone"), col="proj_super_spawn_bone")
            add("Hit Source", FPI.format_projectile_value(mv, "proj_super_hit_source"), col="proj_super_hit_source")
            add("Beam Speed", FPI.format_projectile_value(mv, "proj_super_beam_speed"), important=True, col="proj_super_beam_speed")
            add("Beam Force", FPI.format_projectile_value(mv, "proj_super_beam_force"), col="proj_super_beam_force")
            add("Hit Radius", FPI.format_projectile_value(mv, "proj_super_hit_radius"), important=True, col="proj_super_hit_radius")
            add("Visual", FPI.format_projectile_value(mv, "proj_super_beam_visual"), col="proj_super_beam_visual")
            add("Final Dmg", FPI.format_projectile_value(mv, "proj_final_damage"), important=True, col="proj_final_damage")
            add("Final FX", FPI.format_projectile_value(mv, "proj_final_particle_fx"), col="proj_final_particle_fx")
        else:
            try:
                if sub_var is not None:
                    parts = []
                    if kind:
                        parts.append(f"{kind}")
                    aid = mv.get("id")
                    if aid is not None:
                        parts.append(f"Anim 0x{int(aid):04X}")
                    abs_addr = mv.get("abs")
                    if abs_addr:
                        parts.append(f"0x{int(abs_addr):08X}")
                    sub_var.set(" | ".join(parts) if parts else "Frame-data row")
            except Exception:
                pass
            for label, col, important in [
                ("Damage", "damage", True),
                ("Meter", "meter", False),
                ("Start", "startup", False),
                ("Active", "active", True),
                ("HS", "hitstun", False),
                ("BS", "blockstun", False),
                ("Stop", "hitstop", False),
                ("Spark", "hit_spark", False),
                ("Reach", "stretch_len", True),
                ("Post Link", "post_link", False),
                ("KB Style", "kb_type", False),
                ("Extra Launch", "launch_profile", False),
                ("Launch Adj", "kb_unknown", False),
                ("KB X", "kb_x", True),
                ("Arc", "air_kb", True),
                ("Speed", "speed_mod", False),
                ("Property", "attack_property", False),
                ("React", "hit_reaction", False),
                ("Invuln", "invuln", True),
                ("SuperBG", "superbg", False),
            ]:
                add(label, _tree_val(col), important=important, col=col)

        if not chips:
            try:
                ttk.Label(frame, text="No parsed values for this row yet.", style="CardMuted.TLabel").pack(anchor="w")
            except Exception:
                pass
            return

        try:
            # A small chip grid reads better than one long packed text string.
            # Keep labels muted and values boxed so projectiles/supers feel like
            # a compact stat card instead of another spreadsheet row.
            max_cols = 6
            for idx, (label, value, flavor, col) in enumerate(chips[:30]):
                cell = ttk.Frame(frame, style="Card.TFrame")
                cell.grid(row=idx // max_cols, column=idx % max_cols, sticky="ew", padx=(0, 8), pady=(0, 5))
                lab = ttk.Label(cell, text=label, style="CardMuted.TLabel")
                lab.pack(anchor="w")
                value_style = "QuickImportant.TLabel" if flavor == "important" else "QuickValue.TLabel"
                val = ttk.Label(cell, text=value, style=value_style, anchor="center")
                val.pack(fill="x")
                if col and hasattr(self, "_edit_selected_column") and col not in {"kind", "hits", "link", "invuln", "abs"}:
                    try:
                        cell.configure(cursor="hand2")
                        lab.configure(cursor="hand2")
                        val.configure(cursor="hand2")
                        cell.bind("<Button-1>", lambda _e, c=col: self._edit_selected_column(c))
                        lab.bind("<Button-1>", lambda _e, c=col: self._edit_selected_column(c))
                        val.bind("<Button-1>", lambda _e, c=col: self._edit_selected_column(c))
                    except Exception:
                        pass
            for col_i in range(max_cols):
                frame.grid_columnconfigure(col_i, weight=1)
        except Exception:
            pass

    def _refresh_inspector(self, item_id: str | None = None, mv: dict | None = None):
        if not getattr(self, "_inspector_value_vars", None):
            return

        if not item_id or not mv or not self.tree:
            if self._inspector_title_var is not None:
                self._inspector_title_var.set("Select a move")
            if self._inspector_subtitle_var is not None:
                self._inspector_subtitle_var.set("Use the inspector for normal edits without parsing the whole grid.")
            if self._inspector_hint_var is not None:
                self._inspector_hint_var.set("Click any value chip to edit it. Address copies to the clipboard.")
            for var in self._inspector_value_vars.values():
                try:
                    var.set("-")
                except Exception:
                    pass
            for col, btn in getattr(self, "_inspector_buttons", {}).items():
                try:
                    btn.configure(state=("disabled" if col != "abs" else "disabled"))
                except Exception:
                    pass
            self._refresh_quick_panel(None, None)
            return

        move_txt = self.tree.set(item_id, "move") or "Selected move"
        kind = mv.get("kind") or self.tree.set(item_id, "kind") or "-"
        aid = mv.get("id")
        abs_addr = mv.get("abs")

        if self._inspector_title_var is not None:
            self._inspector_title_var.set(move_txt)

        parts = [f"Kind: {kind}"]
        if aid is not None:
            parts.append(f"Anim: 0x{int(aid):04X}")
        if abs_addr:
            parts.append(f"Address: 0x{int(abs_addr):08X}")
        if self._inspector_subtitle_var is not None:
            self._inspector_subtitle_var.set(" | ".join(parts))
        if self._inspector_hint_var is not None:
            changed = sum(1 for snap in self._dirty_cells.values() if snap.get("item_id") == item_id)
            if changed:
                self._inspector_hint_var.set(f"Changed values on this move: {changed}. Click a changed chip to edit again, or use Reset changed.")
            elif FSI.is_super_row(mv):
                self._inspector_hint_var.set("Super dispatch rows are the caller layer. Selector/link are dangerous; phase length is the safest timing poke.")
            elif FPI.is_projectile_emitter_row(mv):
                self._inspector_hint_var.set("Emitter rows bulk-edit the projectile cards spawned by this barrage. Count is display-only; damage/life/speed/scale/FX edits apply to the grouped cards.")
            elif FPI.is_projectile_row(mv):
                self._inspector_hint_var.set("Projectile/super values are promoted to the top here. Compact projectile-super cards use Life, Hits, Emit, Interval, FX, Proj ID, Bone, Spawn X/Y, and Scale.")
            else:
                self._inspector_hint_var.set("Click any value chip to edit it. Address copies to the clipboard.")

        all_cols = set(self.tree["columns"])
        for col, var in self._inspector_value_vars.items():
            try:
                value = self.tree.set(item_id, col) if col in all_cols else ""
            except Exception:
                value = ""
            if value is None or str(value).strip() == "":
                value = "not found"
            try:
                var.set(str(value))
            except Exception:
                pass

        writer_ok = bool(U.WRITER_AVAILABLE)
        for col, btn in getattr(self, "_inspector_buttons", {}).items():
            try:
                state_ok = writer_ok
                if col == "move" and mv.get("_hit_segment_index") is not None:
                    state_ok = False
                btn.configure(state=("normal" if state_ok else "disabled"))
            except Exception:
                pass

        for col, widget in getattr(self, "_inspector_value_widgets", {}).items():
            try:
                if col in {"kind", "hits", "link", "invuln"} or col in FPI.PROJECTILE_STATIC_COLUMNS or (col.startswith("proj_") and FPI.is_projectile_row(mv) and not FPI.projectile_editable(col)) or (col.startswith("proj_") and not FPI.is_projectile_row(mv)) or (col == "abs" and not abs_addr):
                    widget.configure(cursor="", style="ValueStatic.TLabel")
                else:
                    self._configure_inspector_chip_style(widget, col, hover=False)
            except Exception:
                pass

        self._refresh_quick_panel(item_id, mv)
        self._apply_inspector_context_layout(mv)

    def _apply_inspector_context_layout(self, mv: dict | None):
        """Reorder/hide inspector cards so selected-row-specific data is not buried.

        Projectile and projectile-super rows used to require scrolling down to
        the bottom of the sidebar. This keeps their useful cards at the top and
        hides empty normal-frame cards for projectile rows.
        """
        sections = list(getattr(self, "_inspector_sections", []) or [])
        if not sections:
            return

        by_title = {title: (card, tuple(fields or ())) for title, card, fields in sections}
        default_order = [
            "Move link", "Impact", "Timing", "Stun and pressure",
            "Launch and knockback controls", "Hit FX and reach",
            "Dangerous script links", "Flags and lookup",
            "Super dispatch", "Projectile emitter", "Projectile super", "Projectile data", "Super beam", "Final hit", "Projectile super probes",
        ]

        def _field_has_value(col: str) -> bool:
            var = getattr(self, "_inspector_value_vars", {}).get(col)
            try:
                val = str(var.get() if var is not None else "").strip().lower()
            except Exception:
                val = ""
            return bool(val and val not in {"-", "not found", "none"})

        def _section_has(title: str, *, skip: set[str] | None = None) -> bool:
            fields = by_title.get(title, (None, ()))[1]
            skip = skip or set()
            return any(_field_has_value(c) for c in fields if c not in skip)

        if FSI.is_super_row(mv):
            order = ["Move link", "Super dispatch", "Dangerous script links", "Flags and lookup"]
        elif FPI.is_projectile_row(mv):
            emitter_has = _section_has("Projectile emitter")
            ps_has = _section_has("Projectile super")
            # Do not let proj_fmt alone pull the generic projectile card to the
            # top for compact 00/23 super cards. That was the confusing
            # not-found wall in the sidebar.
            projectile_has = _section_has("Projectile data", skip={"proj_fmt"})
            beam_has = _section_has("Super beam")
            final_has = _section_has("Final hit")
            probe_has = _section_has("Projectile super probes")
            order = ["Move link"]
            if emitter_has and FPI.is_projectile_emitter_row(mv):
                order.append("Projectile emitter")
            if ps_has and not FPI.is_projectile_emitter_row(mv):
                order.append("Projectile super")
            if beam_has:
                order.append("Super beam")
            if final_has:
                order.append("Final hit")
            if projectile_has:
                order.append("Projectile data")
            if probe_has:
                order.append("Projectile super probes")
            # Keep normal editing fallbacks available but below the actual
            # projectile/super card controls.
            order.extend(["Impact", "Launch and knockback controls", "Hit FX and reach", "Dangerous script links", "Flags and lookup"])
        else:
            order = list(default_order)
            # Non-projectile rows should not waste vertical space on empty
            # projectile cards unless scanner data actually exists on the row.
            for title in ["Super dispatch", "Projectile emitter", "Projectile super", "Projectile data", "Super beam", "Final hit", "Projectile super probes"]:
                if title in order and not _section_has(title):
                    order.remove(title)

        seen = set(order)
        for title, card, _fields in sections:
            if title not in seen:
                try:
                    card.pack_forget()
                except Exception:
                    pass

        for title in order:
            pair = by_title.get(title)
            if not pair:
                continue
            card, _fields = pair
            try:
                card.pack_forget()
                card.pack(fill="x", pady=(0, 10))
            except Exception:
                pass

        try:
            if getattr(self, "_inspector_canvas", None) is not None:
                self._inspector_canvas.yview_moveto(0.0)
        except Exception:
            pass

    # ---------- Edit tracking / friendly reset helpers ----------

    def _dirty_group_for_col(self, col_name: str | None):
        """Return (group_key, tree columns) for a user-editable field."""
        if not col_name:
            return (None, ())
        groups = {
            "move": ("move", ("move",)),
            "damage": ("damage", ("damage",)),
            "meter": ("meter", ("meter",)),
            "startup": ("active", ("startup", "active")),
            "active": ("active", ("startup", "active")),
            "active2": ("active2", ("active2",)),
            "hitstun": ("hitstun", ("hitstun",)),
            "blockstun": ("blockstun", ("blockstun",)),
            "hitstop": ("hitstop", ("hitstop",)),
            "hit_spark": ("hit_spark", ("hit_spark",)),
            "stretch_part": ("stretch_part", ("stretch_part",)),
            "stretch_len": ("stretch_len", ("stretch_len",)),
            "stretch_width": ("stretch_width", ("stretch_width",)),
            "stretch_height": ("stretch_height", ("stretch_height",)),
            "stretch_time": ("stretch_time", ("stretch_time",)),
            "post_link": ("post_link", ("post_link",)),
            "kb_type": ("kb_type", ("kb_type",)),
            "launch_profile": ("launch_profile", ("launch_profile",)),
            "kb_unknown": ("kb_unknown", ("kb_unknown",)),
            "kb_x": ("kb_x", ("kb_x",)),
            "air_kb": ("air_kb", ("air_kb",)),
            "speed_mod": ("speed_mod", ("speed_mod",)),
            "attack_property": ("attack_property", ("attack_property",)),
            "hit_reaction": ("hit_reaction", ("hit_reaction",)),
            "superbg": ("superbg", ("superbg",)),
            # Older/hidden editors are still tracked if routed from legacy builds.
            "combo_kb_mod": ("combo_kb_mod", ("combo_kb_mod",)),
            "proj_dmg": ("proj_dmg", ("proj_dmg", "proj_tpl")),
            "hb_main": ("hb", ("hb_main", "hb")),
            "hb": ("hb", ("hb_main", "hb")),
        }
        if groups.get(col_name):
            return groups.get(col_name, (None, ()))
        return FPI.projectile_group_for_col(col_name)

    def _dirty_group_for_cell(self, mv: dict | None, col_name: str | None):
        """Return dirty tracking group for a specific row/column pair.

        Projectile rows reuse core columns such as Damage, KB X, and Arc. For
        those rows, reset/save/load must treat the value as a projectile field,
        not as a normal move-table scalar.
        """
        if FSI.is_super_row(mv) and FSI.super_editable(col_name):
            return FSI.super_group_for_col(col_name)
        if FPI.is_projectile_row(mv) and FPI.projectile_editable(col_name):
            return FPI.projectile_group_for_col(col_name)
        return self._dirty_group_for_col(col_name)

    def _dirty_key(self, item_id: str, mv: dict, group_key: str):
        abs_addr = mv.get("_dirty_key_addr") or mv.get("abs")
        if abs_addr:
            try:
                key_addr = int(abs_addr)
            except Exception:
                # Synthetic projectile-emitter rows use stable string keys like
                # "emitter:Finishing Shower Emitter".  Keep those valid for
                # dirty tracking instead of crashing int(abs_addr).
                key_addr = str(abs_addr)
        else:
            key_addr = id(mv)
        return (key_addr, str(group_key))

    def _mv_snapshot_for_group(self, mv: dict, group_key: str) -> dict:
        keys_by_group = {
            "move": ("id", "move_name"),
            "damage": ("damage",),
            "meter": ("meter",),
            "active": ("active_start", "active_end", "active_addr"),
            "active2": ("active2_start", "active2_end", "active2_addr"),
            "hitstun": ("hitstun", "stun_addr"),
            "blockstun": ("blockstun", "stun_addr"),
            "hitstop": ("hitstop", "stun_addr"),
            "hit_spark": ("hit_spark", "hit_spark_addr"),
            "stretch_part": ("stretch_part", "stretch_part_addr"),
            "stretch_len": ("stretch_len", "stretch_len_addr"),
            "stretch_width": ("stretch_width", "stretch_width_addr"),
            "stretch_height": ("stretch_height", "stretch_height_addr"),
            "stretch_time": ("stretch_time", "stretch_time_addr"),
            "post_link": ("post_link", "post_link_addr"),
            "launch_profile": ("launch_profile", "knockback_addr"),
            "kb_unknown": ("kb_unknown", "knockback_addr"),
            "kb_x": ("kb_x", "knockback_addr"),
            "air_kb": ("air_kb", "knockback_addr"),
            "speed_mod": ("speed_mod", "speed_mod_addr", "speed_mod_sig"),
            "attack_property": ("attack_property", "attack_property_addr", "attack_property_sig"),
            "hit_reaction": ("hit_reaction", "hit_reaction_addr"),
            "superbg": ("superbg_val", "superbg_addr"),
            "combo_kb_mod": ("combo_kb_mod", "combo_kb_mod_addr"),
            "proj_dmg": ("proj_dmg", "proj_tpl"),
            "hb": ("hb_r", "hb_off", "hb_candidates"),
        }
        if str(group_key).startswith("super_dispatch:"):
            return FSI.super_snapshot(mv, group_key)
        if str(group_key).startswith("projectile:"):
            return FPI.projectile_snapshot(mv, group_key)

        out = {}
        for k in keys_by_group.get(group_key, (group_key,)):
            val = mv.get(k)
            if isinstance(val, list):
                val = list(val)
            elif isinstance(val, tuple):
                val = tuple(val)
            elif isinstance(val, dict):
                val = dict(val)
            out[k] = val
        return out

    def _begin_edit_snapshot(self, item_id: str, mv: dict, col_name: str | None):
        if self._suppress_dirty_tracking or not self.tree:
            return
        group_key, cols = self._dirty_group_for_cell(mv, col_name)
        if not group_key:
            return
        key = self._dirty_key(item_id, mv, group_key)
        if key in self._dirty_cells:
            return
        tree_cols = set(self.tree["columns"])
        values = {}
        for c in cols:
            if c in tree_cols:
                try:
                    values[c] = self.tree.set(item_id, c)
                except Exception:
                    values[c] = ""
        self._pending_edit_snapshots[key] = {
            "key": key,
            "item_id": item_id,
            "mv": mv,
            "group": group_key,
            "cols": tuple(c for c in cols if c in tree_cols),
            "values": values,
            "mv_values": self._mv_snapshot_for_group(mv, group_key),
        }

    def _current_values_for_cols(self, item_id: str, cols) -> dict:
        out = {}
        if not self.tree:
            return out
        tree_cols = set(self.tree["columns"])
        for c in cols:
            if c not in tree_cols:
                continue
            try:
                out[c] = self.tree.set(item_id, c)
            except Exception:
                out[c] = ""
        return out

    def _after_cell_write(self, item_id: str, mv: dict, col_name: str | None = None):
        """Called after an editor writes and updates the row.

        It refreshes the inspector immediately and tracks the original value for
        reset-changed. Editors that close later (custom Toplevels) call this from
        their OK handler, which fixes the stale side-panel problem.
        """
        if self._suppress_dirty_tracking or not self.tree:
            return
        group_key, _cols = self._dirty_group_for_cell(mv, col_name)
        if group_key:
            key = self._dirty_key(item_id, mv, group_key)
            snap = self._dirty_cells.get(key) or self._pending_edit_snapshots.pop(key, None)
            if snap:
                current = self._current_values_for_cols(item_id, snap.get("cols", ()))
                if current != snap.get("values", {}):
                    snap["item_id"] = item_id
                    snap["mv"] = mv
                    self._dirty_cells[key] = snap
                else:
                    self._dirty_cells.pop(key, None)
        self._update_dirty_ui(item_id, mv)

    def _update_dirty_ui(self, item_id: str | None = None, mv: dict | None = None):
        # Recompute row set from tracked cells so reset/edit/cancel states stay honest.
        self._dirty_row_items = {snap.get("item_id") for snap in self._dirty_cells.values() if snap.get("item_id")}
        if self._changed_count_var is not None:
            count = len(self._dirty_cells)
            label = "Changed: 0" if count == 0 else f"Changed: {count}"
            self._changed_count_var.set(label)

        # Keep the main Normals Preview in sync with this workbench. This does
        # not write anything; it only publishes the current dirty entries so
        # main.py can overlay them onto last_scan_normals immediately.
        try:
            import fd_patch_runtime
            payload = self._build_patch_character_payload() if self._dirty_cells else None
            entries = (payload or {}).get("changes") if isinstance(payload, dict) else []
            # Normals Preview displays move-table data, not projectile rows.
            # Keep projectile edits in save/load/reset, but do not overlay them
            # onto normal move scan dictionaries.
            entries = [e for e in (entries or []) if not str(e.get("group") or "").startswith("projectile:")]
            fd_patch_runtime.set_live_entries_for_character(self._patch_char_key(), entries or [])
        except Exception:
            pass

        if self.tree:
            targets = set(self._dirty_row_items)
            if item_id:
                targets.add(item_id)
            for row in list(targets):
                try:
                    mv2 = self.move_to_tree_item.get(row) or mv or {}
                    self._apply_row_tags(row, mv2)
                except Exception:
                    pass
            try:
                sel = self.tree.selection()
                if sel:
                    cur_item = sel[0]
                    self._refresh_inspector(cur_item, self.move_to_tree_item.get(cur_item))
            except Exception:
                pass
        if item_id and mv:
            try:
                self._set_status_for_item(item_id, mv)
            except Exception:
                pass

    def _is_col_dirty(self, item_id: str | None, col_name: str) -> bool:
        if not item_id:
            return False
        mv = self.move_to_tree_item.get(item_id) if self.move_to_tree_item else None
        if not mv:
            return False
        group_key, _cols = self._dirty_group_for_cell(mv, col_name)
        if not group_key:
            return False
        return self._dirty_key(item_id, mv, group_key) in self._dirty_cells

    def _configure_inspector_chip_style(self, widget, col_name: str, hover: bool = False):
        try:
            sel = self.tree.selection() if self.tree else ()
            item_id = sel[0] if sel else None
            changed = self._is_col_dirty(item_id, col_name)
            mv = None
            try:
                sel = self.tree.selection() if self.tree else ()
                mv = self.move_to_tree_item.get(sel[0]) if sel else None
            except Exception:
                mv = None
            if col_name in {"kind", "hits", "link"} or col_name in FPI.PROJECTILE_STATIC_COLUMNS or (col_name.startswith("proj_") and not FPI.is_projectile_row(mv)) or (col_name.startswith("dispatch_") and not FSI.super_editable(col_name)) or (col_name.startswith("dispatch_") and not FSI.is_super_row(mv)) :
                widget.configure(cursor="", style="ValueStatic.TLabel")
            elif changed:
                widget.configure(cursor="hand2", style="ValueChangedHover.TLabel" if hover else "ValueChanged.TLabel")
            else:
                widget.configure(cursor="hand2", style="ValueChipHover.TLabel" if hover else "ValueChip.TLabel")
        except Exception:
            pass

    def _copy_selected_address(self):
        if not self.tree:
            return
        sel = self.tree.selection()
        if not sel:
            self._status_var.set("Select a move first")
            return
        item = sel[0]
        mv = self.move_to_tree_item.get(item) or {}
        addr = mv.get("abs")
        if not addr:
            raw = self.tree.set(item, "abs") or ""
            try:
                addr = int(raw, 16) if raw.lower().startswith("0x") else int(raw)
            except Exception:
                addr = None
        if not addr:
            messagebox.showerror("Address", "No address available for the selected move.")
            return
        self._copy_address(int(addr))

    def _edit_selected_column(self, col_name: str):
        if not self.tree:
            return
        sel = self.tree.selection()
        if not sel:
            self._status_var.set("Select a move first")
            return
        item = sel[0]
        mv = self.move_to_tree_item.get(item)
        if not mv:
            return

        if col_name == "abs":
            self._copy_selected_address()
            return
        if col_name.startswith("dispatch_") and not FSI.is_super_row(mv):
            self._status_var.set("Dispatch fields are only editable on super dispatch rows.")
            return
        if FSI.is_super_row(mv) and not FSI.super_editable(col_name):
            self._status_var.set("That super dispatch field is display-only.")
            return
        if col_name.startswith("proj_") and not FPI.is_projectile_row(mv):
            self._status_var.set("Projectile fields are only editable on projectile rows.")
            return
        if FPI.is_projectile_row(mv) and not FPI.projectile_editable(col_name):
            self._status_var.set("That projectile field is display-only.")
            return
        if col_name in {"hits", "link"}:
            if col_name == "hits":
                self._status_var.set("Expand a multi-hit move to view and edit each detected hit bundle separately.")
            else:
                self._status_var.set("Link is display-only. It groups related move-table sections.")
            return
        if col_name == "move" and mv.get("_hit_segment_index") is not None:
            self._status_var.set("Hit rows edit hit data only. Use the parent move row to replace the animation.")
            return

        self._begin_edit_snapshot(item, mv, col_name)

        if not U.WRITER_AVAILABLE and not FPI.is_projectile_row(mv) and not FSI.is_super_row(mv):
            messagebox.showerror("Error", "Writer unavailable")
            return

        current_val = ""
        try:
            if col_name in self.tree["columns"]:
                current_val = self.tree.set(item, col_name)
        except Exception:
            current_val = ""

        if col_name == "move":
            class _PopupEvent:
                pass
            event = _PopupEvent()
            try:
                event.x_root = self.root.winfo_pointerx()
                event.y_root = self.root.winfo_pointery()
            except Exception:
                event.x_root = 0
                event.y_root = 0
            self._show_move_edit_menu(event, item, mv)
        elif col_name == "speed_mod":
            self._edit_speed_mod(item, mv, current_val)
        elif col_name == "attack_property":
            self._edit_attack_property(item, mv, current_val)
        else:
            self._route_standard_edit(col_name, item, mv, current_val)

        self._apply_row_tags(item, mv)
        self._set_status_for_item(item, mv)
        self._refresh_inspector(item, mv)

    # ---------- Row tagging / status ----------

    def _apply_row_tags(self, item_id: str, mv: dict):
        # Preserve structural tags, rebuild dynamic styling every time so reset
        # and toggles immediately remove stale highlight state.
        existing = set(self.tree.item(item_id, "tags") or ())
        structural_tags = {
            "row_even", "row_odd",
            "group_parent", "child_row", "grandchild_row",
            "special_row", "super_row",
            "projectile_row", "projectile_header",
            "family_header", "family_header_normal", "family_header_special", "family_header_super", "family_header_other",
            "family_linked",
        }
        tags = {t for t in existing if t in structural_tags}

        kb_cols = ("launch_profile", "kb_unknown", "kb_x", "air_kb")
        if any((self.tree.set(item_id, c) or "").strip() for c in kb_cols if c in self.tree["columns"]):
            tags.add("kb_hot")

        speed_txt = self.tree.set(item_id, "speed_mod").strip()
        if speed_txt:
            tags.add("combo_hot")

        property_txt = self.tree.set(item_id, "attack_property").strip()
        if property_txt:
            tags.add("property_hot")

        super_txt = self.tree.set(item_id, "superbg").strip()
        if super_txt == "ON":
            tags.add("super_on")

        abs_txt = self.tree.set(item_id, "abs").strip()
        if not abs_txt:
            tags.add("missing_addr")

        if FSI.is_super_row(mv):
            tags.add("super_row")
        if FPI.is_projectile_row(mv):
            tags.add("projectile_row")

        if item_id in getattr(self, "_dirty_row_items", set()):
            tags.add("edited_row")

        ordered = [
            t for t in (
                "row_even", "row_odd",
                "child_row", "grandchild_row",
                "special_row", "super_row",
                "group_parent",
                "family_header", "family_header_normal", "family_header_special", "family_header_super", "family_header_other",
                "projectile_header", "family_linked", "projectile_row",
                "kb_hot", "combo_hot", "property_hot", "super_on", "missing_addr",
                "edited_row",
            )
            if t in tags
        ]
        self.tree.item(item_id, tags=tuple(ordered))

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
        try:
            current_view = str(getattr(self, "_fd_view_mode", "frame") or "frame")
            if FSI.is_super_row(mv) and current_view == "frame":
                s.append("Tip: Supers view moves dispatch fields next to the move name")
            if FPI.is_projectile_row(mv) and current_view == "frame":
                s.append("Tip: Projectiles view moves projectile fields next to the move name")
            elif current_view == "frame" and (kind in {"super", "hyper"} or str(move_txt or "").lower().find("super") >= 0):
                s.append("Tip: Supers view moves super/probe fields next to the move name")
        except Exception:
            pass
        self._status_var.set(" | ".join(s) if s else "Ready")

    def _on_select(self, _evt=None):
        sel = self.tree.selection()
        if not sel:
            self._last_selected_item_id = None
            self._status_var.set("Ready")
            self._refresh_inspector(None, None)
            return
        item = sel[0]
        # Tk can fire duplicate selection events while focus/scrolling changes.
        # Rebuilding the quick strip and sidebar for the same row again makes
        # the menu feel sluggish, so skip duplicate event-driven refreshes.
        if _evt is not None and getattr(self, "_last_selected_item_id", None) == item:
            return
        self._last_selected_item_id = item
        mv = self.move_to_tree_item.get(item)
        if not mv:
            self._status_var.set("Ready")
            self._refresh_inspector(None, None)
            return
        self._set_status_for_item(item, mv)
        self._refresh_inspector(item, mv)

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
        ordered = sorted(
            self._moves_scanned,
            key=lambda mv: self._explicit_notation(mv)
    )
        self._rebuild_tree_with_moves(ordered, "explicit order")


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

        # Header direction labels. Avoid icon glyphs so the UI stays ASCII-only.
        for c in tree["columns"]:
            base = self._heading_text_for_col(c)
            suffix = " [ASC]" if c == col_name and asc else " [DESC]" if c == col_name else ""
            tree.heading(c, text=f"{base}{suffix}")

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

        # Header direction labels. Avoid icon glyphs so the UI stays ASCII-only.
        for c in tree["columns"]:
            base = self._heading_text_for_col(c)
            suffix = " [ASC]" if c == col_name and asc else " [DESC]" if c == col_name else ""
            tree.heading(c, text=f"{base}{suffix}")

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
        # === COLLECT REGION HITS (FD band learning) ===
        addr_hits = []

        if mv.get("abs"):
            addr_hits.append(mv["abs"])

        if mv.get("speed_mod_addr"):
            addr_hits.append(mv["speed_mod_addr"])

        if mv.get("superbg_addr"):
            addr_hits.append(mv["superbg_addr"])

        if mv.get("damage_addr"):
            addr_hits.append(mv["damage_addr"])

        if mv.get("active_addr"):
            addr_hits.append(mv["active_addr"])

        # global collector
        try:
            from fd_window import FD_REGION_HITS
            FD_REGION_HITS.extend(addr_hits)
        except Exception:
            pass
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

        new_val = ask_integer_with_help(
            self.root,
            title="Edit Speed Modifier",
            prompt="New speed modifier byte (0-255)",
            help_text=get_field_help("speed_mod"),
            initialvalue=int(cur_val),
            minvalue=0,
            maxvalue=255,
            address=int(addr),
        )
        if new_val is None:
            return

        if write_speed_mod_inline(mv, int(new_val), U.WRITER_AVAILABLE):
            mv["speed_mod"] = int(new_val)
            self.tree.set(item, "speed_mod", U.fmt_speed_mod_ui(new_val))
            self._after_cell_write(item, mv, "speed_mod")
        else:
            messagebox.showerror("Speed Modifier", "Failed to write speed modifier byte.")

    def _ensure_attack_property(self, mv) -> None:
        if mv.get("attack_property_addr") is not None:
            return
        move_abs = mv.get("abs")
        if not move_abs:
            return
        try:
            from dolphin_io import rbytes
            addr, cur, sig = find_attack_property_addr(move_abs, rbytes)
        except Exception:
            addr, cur, sig = (None, None, None)
        if addr:
            mv["attack_property_addr"] = addr
            mv["attack_property"] = cur
            mv["attack_property_sig"] = sig

    def _write_attack_property_inline(self, mv, value: int) -> bool:
        if not U.WRITER_AVAILABLE:
            return False
        addr = mv.get("attack_property_addr")
        if not addr:
            return False
        try:
            from dolphin_io import wbytes
            wbytes(int(addr), bytes([int(value) & 0xFF]))
            return True
        except Exception:
            pass
        try:
            from dolphin_io import wr8
            wr8(int(addr), int(value) & 0xFF)
            return True
        except Exception:
            return False

    def _choose_attack_property_value(self, addr: int, cur_val: int) -> int | None:
        """Modal picker for attack property.

        Most edits should be click-select from the known guide values. Manual
        byte entry remains available for testing unknown/experimental values.
        """
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Attack Property")
        dlg.resizable(False, False)
        dlg.transient(self.root)
        dlg.grab_set()

        result = {"value": None}

        body = ttk.Frame(dlg, padding=12)
        body.pack(fill="both", expand=True)

        ttk.Label(
            body,
            text=f"Attack Property @ 0x{addr:08X}",
        ).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            body,
            text=get_field_help("attack_property"),
            justify="left",
            wraplength=390,
        ).pack(anchor="w", pady=(0, 8))

        known_options = [
            f"0x{k:02X}  {v}"
            for k, v in sorted(ATTACK_PROPERTY_VALUES.items())
        ]
        current_text = fmt_attack_property(cur_val)
        if current_text not in known_options:
            known_options.insert(0, current_text)

        ttk.Label(body, text="Known values:").pack(anchor="w")
        choice_var = tk.StringVar(master=dlg, value=current_text)
        combo = ttk.Combobox(
            body,
            textvariable=choice_var,
            values=known_options,
            width=34,
            state="readonly",
        )
        combo.pack(fill="x", pady=(2, 10))

        manual_on = tk.BooleanVar(master=dlg, value=False)
        manual_row = ttk.Frame(body)
        manual_row.pack(fill="x", pady=(0, 10))

        chk = ttk.Checkbutton(
            manual_row,
            text="Manual byte:",
            variable=manual_on,
        )
        chk.pack(side="left")

        manual_var = tk.StringVar(master=dlg, value=f"0x{int(cur_val) & 0xFF:02X}")
        manual_ent = ttk.Entry(manual_row, textvariable=manual_var, width=10)
        manual_ent.pack(side="left", padx=(6, 0))

        hint = ttk.Label(
            body,
            text="Use manual only for unknown/test values. Examples: 0x09, 0x21, 24",
            style="Muted.Top.TLabel" if self.root else None,
        )
        hint.pack(anchor="w", pady=(0, 10))

        buttons = ttk.Frame(body)
        buttons.pack(fill="x")

        def apply_value():
            if manual_on.get():
                val = parse_attack_property(manual_var.get())
            else:
                val = parse_attack_property(choice_var.get())
            if val is None:
                messagebox.showerror("Attack Property", "Invalid value. Use values like 0x09, 0x21, or 24.", parent=dlg)
                return
            result["value"] = int(val) & 0xFF
            dlg.destroy()

        def cancel():
            result["value"] = None
            dlg.destroy()

        ttk.Button(buttons, text="Apply", command=apply_value).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Cancel", command=cancel).pack(side="right")

        combo.focus_set()
        dlg.bind("<Return>", lambda _e: apply_value())
        dlg.bind("<Escape>", lambda _e: cancel())

        try:
            self.root.wait_window(dlg)
        except Exception:
            pass

        return result["value"]

    def _edit_attack_property(self, item, mv, current: str):
        self._ensure_attack_property(mv)
        addr = mv.get("attack_property_addr")
        if not addr:
            messagebox.showerror(
                "Attack Property",
                "Signature not found for this move.\nTry Refresh visible, or this move may not have the attack-property pattern.",
            )
            return

        cur_val = mv.get("attack_property")
        if cur_val is None:
            cur_val = parse_attack_property(current) or 0x09

        new_val = self._choose_attack_property_value(int(addr), int(cur_val) & 0xFF)
        if new_val is None:
            return

        if self._write_attack_property_inline(mv, int(new_val)):
            mv["attack_property"] = int(new_val) & 0xFF
            self.tree.set(item, "attack_property", fmt_attack_property(new_val))
            self._after_cell_write(item, mv, "attack_property")
        else:
            messagebox.showerror("Attack Property", "Failed to write attack property byte.")

    # ---------- Refresh visible ----------

    def _refresh_visible(self):
        # Manual refresh is the one place we intentionally run heavier optional
        # probes.  Normal open/edit paths stay fast and update cells in place.
        self._fd_eager_deep_probe = True
        refreshed = 0
        for item_id in self._all_item_ids:
            if item_id in self._detached:
                continue
            mv = self.move_to_tree_item.get(item_id)
            if not mv:
                continue
            mv = self.move_to_tree_item.get(item_id)
            if not mv:
                continue
            move_abs = mv.get("abs")
            if not move_abs:
                continue

            # === COLLECT + APPEND REGION HITS ===
            try:
                import os
                base_dir = os.path.dirname(__file__)
                path = os.path.join(base_dir, "fd_region_hits.txt")

                hits = []

                if mv.get("abs"):
                    hits.append(mv["abs"])

                if mv.get("speed_mod_addr"):
                    hits.append(mv["speed_mod_addr"])

                if mv.get("attack_property_addr"):
                    hits.append(mv["attack_property_addr"])

                if mv.get("superbg_addr"):
                    hits.append(mv["superbg_addr"])

                if mv.get("damage_addr"):
                    hits.append(mv["damage_addr"])

                if mv.get("active_addr"):
                    hits.append(mv["active_addr"])


                if hits:
                    print(f"[FD] writing {len(hits)} hits -> {path}")
                    with open(path, "a") as f:
                        for h in hits:
                            f.write(f"{h}\n")

            except Exception as e:
                print("[FD] write failed:", e)
                        # === COLLECT + APPEND REGION HITS ===
            try:
                hits = []

                if mv.get("abs"):
                    hits.append(mv["abs"])

                if mv.get("speed_mod_addr"):
                    hits.append(mv["speed_mod_addr"])

                if mv.get("attack_property_addr"):
                    hits.append(mv["attack_property_addr"])

                if mv.get("superbg_addr"):
                    hits.append(mv["superbg_addr"])

                if mv.get("damage_addr"):
                    hits.append(mv["damage_addr"])

                if mv.get("active_addr"):
                    hits.append(mv["active_addr"])


                if hits:
                    print("WRITING REGION HITS")
                    with open("fd_region_hits.txt", "a") as f:
                        for h in hits:
                            f.write(f"{h}\n")
            except Exception:
                pass

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

            # attack property (lazy resolve)
            if mv.get("attack_property_addr") is None:
                try:
                    from dolphin_io import rbytes
                    ap_addr, ap_val, ap_sig = find_attack_property_addr(move_abs, rbytes)
                except Exception:
                    ap_addr, ap_val, ap_sig = (None, None, None)
                if ap_addr:
                    mv["attack_property_addr"] = ap_addr
                    mv["attack_property"] = ap_val
                    mv["attack_property_sig"] = ap_sig
            if mv.get("attack_property_addr"):
                self.tree.set(item_id, "attack_property", fmt_attack_property(mv.get("attack_property")))

            # projectile radius (lazy resolve)
            if mv.get("proj_radius") is None:
                try:
                    U.resolve_projectile_radius_for_move(mv, region_abs=move_abs)
                    self.tree.set(
                        item_id,
                        "proj_radius",
                        f"{mv['proj_radius']:.2f}" if mv.get("proj_radius") else "",
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

        self._fd_eager_deep_probe = False
        self._status_var.set(f"Refreshed {refreshed} visible rows")
        try:
            sel = self.tree.selection()
            if sel:
                mv = self.move_to_tree_item.get(sel[0])
                self._refresh_inspector(sel[0], mv)
        except Exception:
            pass

    # ---------- Shareable patch config ----------

    def _patch_default_dir(self) -> str:
        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
        path = os.path.join(base_dir, "fd_patches")
        try:
            os.makedirs(path, exist_ok=True)
        except Exception:
            path = base_dir
        return path

    def _patch_char_key(self) -> str:
        return str(self.target_slot.get("char_name") or self.slot_label or "Unknown")

    def _patch_json_value(self, value):
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, (list, tuple)):
            return [self._patch_json_value(v) for v in value]
        if isinstance(value, dict):
            return {str(k): self._patch_json_value(v) for k, v in value.items()}
        try:
            return int(value)
        except Exception:
            try:
                return float(value)
            except Exception:
                return str(value)

    def _patch_hex(self, value) -> str | None:
        if value in (None, ""):
            return None
        try:
            return f"0x{int(value):08X}"
        except Exception:
            return None

    def _patch_value_from_values(self, values: dict, group: str):
        try:
            if str(group).startswith("projectile:"):
                return values.get("value")
            if group == "move":
                return int(values.get("id")) if values.get("id") is not None else None
            if group == "damage":
                return int(values.get("damage")) if values.get("damage") is not None else None
            if group == "meter":
                return int(values.get("meter")) if values.get("meter") is not None else None
            if group == "active":
                s = values.get("active_start")
                e = values.get("active_end")
                if s is None or e is None:
                    return None
                return {"start": int(s), "end": int(e)}
            if group == "active2":
                s = values.get("active2_start")
                e = values.get("active2_end")
                if s is None or e is None:
                    return None
                return {"start": int(s), "end": int(e)}
            if group in ("hitstun", "blockstun", "hitstop", "hit_spark", "stretch_part", "stretch_time", "post_link", "launch_profile", "kb_unknown", "speed_mod", "attack_property", "hit_reaction", "combo_kb_mod", "proj_dmg"):
                v = values.get(group)
                return int(v) if v is not None else None
            if group in ("kb_x", "air_kb", "stretch_len", "stretch_width", "stretch_height"):
                v = values.get(group)
                return float(v) if v is not None else None
            if group == "superbg":
                v = values.get("superbg_val")
                if v is None:
                    return None
                raw = int(v) & 0xFF
                return {"enabled": bool(raw == SUPERBG_ON), "raw": raw}
            if group == "hb":
                v = values.get("hb_r")
                return float(v) if v is not None else None
        except Exception:
            return None
        return None

    def _patch_value_for_group(self, mv: dict, group: str):
        if str(group).startswith("super_dispatch:"):
            hit_key = str(group).split(":", 1)[1]
            return (mv.get("_super_hit") or {}).get(hit_key)
        if str(group).startswith("projectile:"):
            hit_key = str(group).split(":", 1)[1]
            return (mv.get("_proj_hit") or {}).get(hit_key)
        return self._patch_value_from_values(mv or {}, group)

    def _patch_display_values(self, item_id: str, group: str) -> dict:
        out = {}
        if not self.tree or not item_id:
            return out
        if str(group).startswith("super_dispatch:"):
            _col = FSI.column_for_super_group(group)
            _g, cols = self._dirty_group_for_col(_col)
        elif str(group).startswith("projectile:"):
            _col = FPI.column_for_projectile_group(group)
            _g, cols = self._dirty_group_for_col(_col)
        else:
            _g, cols = self._dirty_group_for_col(group)
        tree_cols = set(self.tree["columns"])
        for c in cols:
            if c in tree_cols:
                try:
                    out[c] = self.tree.set(item_id, c)
                except Exception:
                    pass
        return out

    def _patch_address_map(self, mv: dict) -> dict:
        keys = (
            "abs", "damage_addr", "meter_addr", "active_addr", "active2_addr",
            "stun_addr", "knockback_addr", "speed_mod_addr", "attack_property_addr",
            "hit_reaction_addr", "superbg_addr", "combo_kb_mod_addr", "hit_spark_addr", "stretch_part_addr", "stretch_len_addr", "stretch_width_addr", "stretch_height_addr", "stretch_time_addr", "post_link_addr", "proj_tpl", "hb_off",
        )
        out = {}
        base = mv.get("abs")
        for key in keys:
            val = mv.get(key)
            if val in (None, ""):
                continue
            if key == "hb_off":
                try:
                    out[key] = int(val)
                except Exception:
                    out[key] = self._patch_json_value(val)
                continue
            hx = self._patch_hex(val)
            if hx:
                out[key] = hx
                if base and key != "abs":
                    try:
                        out[f"{key}_rel"] = int(val) - int(base)
                    except Exception:
                        pass
            else:
                out[key] = self._patch_json_value(val)
        if FSI.is_super_row(mv):
            try:
                out["super_dispatch_row"] = True
                out["dispatch_fmt"] = str((mv.get("_super_hit") or {}).get("fmt") or "")
                out["dispatch_key"] = str((mv.get("_super_hit") or {}).get("key") or "")
            except Exception:
                pass
        if FPI.is_projectile_row(mv):
            try:
                out["projectile_row"] = True
                out["proj_fmt"] = str((mv.get("_proj_hit") or {}).get("fmt") or "")
                out["proj_move"] = str((mv.get("_proj_hit") or {}).get("move") or mv.get("move_name") or "")
                out["proj_key"] = str((mv.get("_proj_hit") or {}).get("key") or "")
            except Exception:
                pass
        return out

    def _patch_entry_from_dirty_snapshot(self, snap: dict) -> dict | None:
        item_id = snap.get("item_id")
        mv = snap.get("mv") or {}
        group = str(snap.get("group") or "")
        if not item_id or not group:
            return None

        value = self._patch_value_for_group(mv, group)
        if value is None:
            return None

        old_values = snap.get("mv_values") or {}
        original = self._patch_value_from_values(old_values, group)

        # If this row also had its animation changed, every other entry for the
        # same row must still target the original row when the patch is applied
        # to a clean session. Exact abs usually wins, but this keeps the config
        # usable even when addresses are not the preferred match path.
        row_original_move_id = None
        for _other in self._dirty_cells.values():
            if _other.get("item_id") == item_id and _other.get("group") == "move":
                try:
                    row_original_move_id = int((_other.get("mv_values") or {}).get("id"))
                except Exception:
                    row_original_move_id = None
                break

        selector_move_id = row_original_move_id if row_original_move_id is not None else (old_values.get("id") if group == "move" else mv.get("id"))
        try:
            selector_move_id = int(selector_move_id) if selector_move_id is not None else None
        except Exception:
            selector_move_id = None

        try:
            move_label = self.tree.set(item_id, "move") if self.tree else ""
        except Exception:
            move_label = ""

        entry = {
            "group": group,
            "value": self._patch_json_value(value),
            "original": self._patch_json_value(original),
            "display": self._patch_display_values(item_id, group),
            "selector": {
                "character": self._patch_char_key(),
                "move_label": move_label,
                "move_id": selector_move_id,
                "current_move_id": int(mv.get("id")) if mv.get("id") is not None else None,
                "kind": mv.get("kind"),
                "segment_index": mv.get("_hit_segment_index"),
                "parent_abs": self._patch_hex(mv.get("_hit_parent_abs")),
                "tier": mv.get("dup_index"),
                "scan_index": mv.get("_scan_index"),
                "abs": self._patch_hex(mv.get("abs")),
                "projectile": bool(FPI.is_projectile_row(mv)),
                "projectile_move": str((mv.get("_proj_hit") or {}).get("move") or mv.get("move_name") or "") if FPI.is_projectile_row(mv) else None,
                "projectile_fmt": str((mv.get("_proj_hit") or {}).get("fmt") or "") if FPI.is_projectile_row(mv) else None,
            },
            "addresses": self._patch_address_map(mv),
        }
        return entry

    def _build_patch_character_payload(self) -> dict | None:
        if not self._dirty_cells:
            return None

        changes = []
        for _key, snap in sorted(
            self._dirty_cells.items(),
            key=lambda kv: (
                int((kv[1].get("mv") or {}).get("abs") or 0),
                str(kv[1].get("group") or ""),
            ),
        ):
            entry = self._patch_entry_from_dirty_snapshot(snap)
            if entry:
                changes.append(entry)

        if not changes:
            return None

        return {
            "character": self._patch_char_key(),
            "slot_label": self.slot_label,
            "change_count": len(changes),
            "changes": changes,
        }

    def _new_patch_document(self, title: str) -> dict:
        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        return {
            "schema": "tvc_continuo.frame_data_patch.v1",
            "title": title or "Untitled TvC frame-data patch",
            "created_by": "TvC Continuo Frame Data Workbench",
            "created_at": now,
            "updated_at": now,
            "characters": {},
        }

    def _read_patch_document(self, path: str) -> dict | None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except Exception as e:
            messagebox.showerror("Load patch", f"Could not read patch file:\n{e}")
            return None

        if not isinstance(data, dict):
            messagebox.showerror("Load patch", "Patch file is not a JSON object.")
            return None
        if data.get("schema") != "tvc_continuo.frame_data_patch.v1":
            messagebox.showerror(
                "Load patch",
                "This does not look like a TvC frame-data patch config.\nExpected schema: tvc_continuo.frame_data_patch.v1",
            )
            return None
        if not isinstance(data.get("characters"), dict):
            data["characters"] = {}
        return data

    def _save_fd_patch_config(self):
        if not self._dirty_cells:
            self._status_var.set("No changed values to save as a patch")
            return

        payload = self._build_patch_character_payload()
        if not payload:
            self._status_var.set("No exportable changed values found")
            return

        initial_name = f"{self._patch_char_key().replace(' ', '_').lower()}_frame_patch.json"
        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save or merge frame-data patch",
            initialdir=self._patch_default_dir(),
            initialfile=initial_name,
            defaultextension=".json",
            filetypes=(("TvC frame-data patch", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return

        doc = None
        if os.path.exists(path):
            doc = self._read_patch_document(path)
            if doc is None:
                return
        else:
            default_title = os.path.splitext(os.path.basename(path))[0].replace("_", " ").strip() or "TvC frame-data patch"
            title = simpledialog.askstring(
                "Patch title",
                "Patch name:",
                initialvalue=default_title,
                parent=self.root,
            )
            doc = self._new_patch_document(title or default_title)

        now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        doc["updated_at"] = now
        doc.setdefault("characters", {})[self._patch_char_key()] = payload
        doc["total_change_count"] = sum(
            int((char_data or {}).get("change_count") or len((char_data or {}).get("changes") or []))
            for char_data in (doc.get("characters") or {}).values()
            if isinstance(char_data, dict)
        )

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(doc, f, indent=2, sort_keys=True)
        except Exception as e:
            messagebox.showerror("Save patch", f"Could not save patch file:\n{e}")
            return

        self._last_patch_config_path = path
        char_count = len(doc.get("characters") or {})
        msg = f"Saved patch: {payload['change_count']} change(s) for {self._patch_char_key()} | characters in file: {char_count}"
        self._status_var.set(msg)
        messagebox.showinfo("Save patch", msg)

    def _patch_character_section(self, doc: dict) -> tuple[str | None, dict | None]:
        chars = doc.get("characters") or {}
        key = self._patch_char_key()
        if key in chars and isinstance(chars[key], dict):
            return key, chars[key]
        key_l = key.strip().lower()
        for k, v in chars.items():
            if str(k).strip().lower() == key_l and isinstance(v, dict):
                return str(k), v
        return None, None

    def _parse_patch_abs(self, value) -> int | None:
        if value in (None, ""):
            return None
        try:
            if isinstance(value, str):
                txt = value.strip()
                return int(txt, 16) if txt.lower().startswith("0x") else int(txt, 10)
            return int(value)
        except Exception:
            return None

    def _find_patch_target(self, entry: dict) -> tuple[str | None, dict | None, str]:
        selector = entry.get("selector") or {}
        wanted_abs = self._parse_patch_abs(selector.get("abs") or (entry.get("addresses") or {}).get("abs"))
        wanted_id = selector.get("move_id")
        wanted_kind = selector.get("kind")
        wanted_tier = selector.get("tier")
        wanted_scan = selector.get("scan_index")
        wanted_projectile = bool(selector.get("projectile"))
        wanted_projectile_move = str(selector.get("projectile_move") or "")
        wanted_projectile_fmt = str(selector.get("projectile_fmt") or "")

        try:
            wanted_id = int(wanted_id) if wanted_id is not None else None
        except Exception:
            wanted_id = None
        try:
            wanted_tier = int(wanted_tier) if wanted_tier is not None else None
        except Exception:
            wanted_tier = None
        try:
            wanted_scan = int(wanted_scan) if wanted_scan is not None else None
        except Exception:
            wanted_scan = None

        # Exact absolute move-table address is the safest match for shareable mods
        # on the same build, so prefer it when available.
        if wanted_abs is not None:
            for item_id, mv in (self.move_to_tree_item or {}).items():
                try:
                    if wanted_projectile and not FPI.is_projectile_row(mv):
                        continue
                    if int(mv.get("abs") or -1) == wanted_abs:
                        if wanted_projectile:
                            hit = mv.get("_proj_hit") or {}
                            if wanted_projectile_move and str(hit.get("move") or mv.get("move_name") or "") != wanted_projectile_move:
                                continue
                            if wanted_projectile_fmt and str(hit.get("fmt") or "") != wanted_projectile_fmt:
                                continue
                        return item_id, mv, "abs"
                except Exception:
                    pass

        candidates = []
        for item_id, mv in (self.move_to_tree_item or {}).items():
            if wanted_projectile and not FPI.is_projectile_row(mv):
                continue
            if wanted_id is not None:
                try:
                    if int(mv.get("id")) != wanted_id:
                        continue
                except Exception:
                    continue
            if wanted_kind and mv.get("kind") != wanted_kind:
                continue
            candidates.append((item_id, mv))

        if wanted_tier is not None:
            for item_id, mv in candidates:
                try:
                    if int(mv.get("dup_index")) == wanted_tier:
                        return item_id, mv, "move_id+tier"
                except Exception:
                    pass

        if wanted_scan is not None:
            for item_id, mv in candidates:
                try:
                    if int(mv.get("_scan_index")) == wanted_scan:
                        return item_id, mv, "move_id+scan_index"
                except Exception:
                    pass

        if candidates:
            return candidates[0][0], candidates[0][1], "move_id"

        return None, None, "not found"

    def _ensure_superbg_for_patch(self, mv: dict) -> bool:
        if mv.get("superbg_addr") is not None:
            return True
        move_abs = mv.get("abs")
        if not move_abs:
            return False
        try:
            from dolphin_io import rbytes, rd8
            saddr, sval = find_superbg_addr(move_abs, rbytes, rd8)
        except Exception:
            saddr, sval = (None, None)
        if saddr:
            mv["superbg_addr"] = saddr
            mv["superbg_val"] = sval
            return True
        return False

    def _patch_bool_enabled(self, value) -> bool:
        if isinstance(value, dict):
            if "enabled" in value:
                return bool(value.get("enabled"))
            if "raw" in value:
                try:
                    return int(value.get("raw")) == SUPERBG_ON
                except Exception:
                    return False
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "on", "yes", "enabled"}
        try:
            return int(value) == SUPERBG_ON
        except Exception:
            return bool(value)

    def _apply_patch_tree_update(self, item_id: str, mv: dict, group: str):
        if not self.tree:
            return
        if str(group).startswith("super_dispatch:"):
            col = FSI.column_for_super_group(group)
            if col:
                FSI.apply_super_tree_value(self.tree, item_id, mv, col)
            return
        if str(group).startswith("projectile:"):
            col = FPI.column_for_projectile_group(group)
            if col:
                FPI.apply_projectile_tree_value(self.tree, item_id, mv, col)
            return
        if group == "move":
            aid = mv.get("id")
            if aid is not None:
                cname = self.target_slot.get("char_name", "-")
                pretty = U.pretty_move_name(int(aid), cname)
                dup_idx = mv.get("dup_index")
                if dup_idx is not None:
                    pretty = f"{pretty} (Tier{dup_idx + 1})"
                self.tree.set(item_id, "move", f"{pretty} [0x{int(aid):04X}]")
        elif group == "damage":
            self.tree.set(item_id, "damage", str(int(mv.get("damage") or 0)))
        elif group == "meter":
            self.tree.set(item_id, "meter", str(int(mv.get("meter") or 0)))
        elif group == "active":
            s = int(mv.get("active_start") or 1)
            e = int(mv.get("active_end") or s)
            self.tree.set(item_id, "startup", str(s))
            self.tree.set(item_id, "active", f"{s}-{e}")
        elif group == "active2":
            s = int(mv.get("active2_start") or 1)
            e = int(mv.get("active2_end") or s)
            self.tree.set(item_id, "active2", f"{s}-{e}")
        elif group == "hitstun":
            self.tree.set(item_id, "hitstun", U.fmt_stun(mv.get("hitstun")))
        elif group == "blockstun":
            self.tree.set(item_id, "blockstun", U.fmt_stun(mv.get("blockstun")))
        elif group == "hitstop":
            self.tree.set(item_id, "hitstop", U.fmt_stun(mv.get("hitstop")))
        elif group == "hit_spark":
            self.tree.set(item_id, "hit_spark", U.fmt_hit_spark_ui(mv))
        elif group == "stretch_part":
            self.tree.set(item_id, "stretch_part", U.fmt_stretch_part_ui(mv))
        elif group == "stretch_len":
            self.tree.set(item_id, "stretch_len", U.fmt_stretch_len_ui(mv))
        elif group == "stretch_width":
            self.tree.set(item_id, "stretch_width", U.fmt_stretch_width_ui(mv))
        elif group == "stretch_height":
            self.tree.set(item_id, "stretch_height", U.fmt_stretch_height_ui(mv))
        elif group == "stretch_time":
            self.tree.set(item_id, "stretch_time", U.fmt_stretch_time_ui(mv))
        elif group == "post_link":
            self.tree.set(item_id, "post_link", U.fmt_post_link_ui(mv))
        elif group == "launch_profile":
            self.tree.set(item_id, "launch_profile", U.fmt_launch_profile_ui(mv))
        elif group == "kb_unknown":
            self.tree.set(item_id, "kb_unknown", U.fmt_kb_unknown_ui(mv))
        elif group == "kb_x":
            self.tree.set(item_id, "kb_x", U.fmt_kb_x_ui(mv))
        elif group == "air_kb":
            self.tree.set(item_id, "air_kb", U.fmt_air_kb_ui(mv))
        elif group == "speed_mod":
            self.tree.set(item_id, "speed_mod", U.fmt_speed_mod_ui(mv.get("speed_mod")))
        elif group == "attack_property":
            self.tree.set(item_id, "attack_property", fmt_attack_property(mv.get("attack_property")))
        elif group == "hit_reaction":
            self.tree.set(item_id, "hit_reaction", U.fmt_hit_reaction(mv.get("hit_reaction")))
        elif group == "superbg":
            self.tree.set(item_id, "superbg", U.fmt_superbg(mv.get("superbg_val")))
        elif group == "combo_kb_mod" and "combo_kb_mod" in self.tree["columns"]:
            val = int(mv.get("combo_kb_mod") or 0) & 0xFF
            self.tree.set(item_id, "combo_kb_mod", f"{val} (0x{val:02X})")
        elif group == "proj_dmg" and "proj_dmg" in self.tree["columns"]:
            self.tree.set(item_id, "proj_dmg", str(int(mv.get("proj_dmg") or 0)))
        elif group == "hb":
            if "hb_main" in self.tree["columns"]:
                self.tree.set(item_id, "hb_main", f"{float(mv.get('hb_r') or 0.0):.1f}")
            if "hb" in self.tree["columns"]:
                self.tree.set(item_id, "hb", U.format_candidate_list(mv.get("hb_candidates") or []))

    def _apply_patch_change(self, item_id: str, mv: dict, entry: dict) -> tuple[bool, str]:
        group = str(entry.get("group") or "")
        value = entry.get("value")
        if not group:
            return False, "missing group"

        try:
            if str(group).startswith("super_dispatch:"):
                col = FSI.column_for_super_group(group)
                if not col:
                    return False, f"unsupported super dispatch group {group}"
                if not FSI.write_super_value(mv, col, value):
                    return False, "write failed"

            elif str(group).startswith("projectile:"):
                col = FPI.column_for_projectile_group(group)
                if not col:
                    return False, f"unsupported projectile group {group}"
                if not FPI.write_projectile_value(mv, col, value):
                    return False, "write failed"

            elif group == "move":
                new_id = int(value)
                if not self._write_anim_id(mv, new_id):
                    return False, "write failed"
                mv["id"] = new_id

            elif group == "damage":
                new_val = int(value)
                if not U.write_damage(mv, new_val):
                    return False, "write failed"
                mv["damage"] = new_val

            elif group == "meter":
                new_val = int(value)
                if not U.write_meter(mv, new_val):
                    return False, "write failed"
                mv["meter"] = new_val

            elif group == "active":
                s = int((value or {}).get("start"))
                e = int((value or {}).get("end"))
                if e < s:
                    e = s
                if not U.write_active_frames(mv, s, e):
                    return False, "write failed"
                mv["active_start"] = s
                mv["active_end"] = e

            elif group == "active2":
                s = int((value or {}).get("start"))
                e = int((value or {}).get("end"))
                if e < s:
                    e = s
                if not write_active2_frames_inline(mv, s, e, U.WRITER_AVAILABLE):
                    return False, "write failed"
                mv["active2_start"] = s
                mv["active2_end"] = e

            elif group == "hitstun":
                new_val = int(value)
                if not U.write_hitstun(mv, new_val):
                    return False, "write failed"
                mv["hitstun"] = new_val

            elif group == "blockstun":
                new_val = int(value)
                if not U.write_blockstun(mv, new_val):
                    return False, "write failed"
                mv["blockstun"] = new_val

            elif group == "hitstop":
                new_val = int(value)
                if not U.write_hitstop(mv, new_val):
                    return False, "write failed"
                mv["hitstop"] = new_val

            elif group in ("hit_spark", "stretch_part", "stretch_time", "post_link"):
                mapping = {
                    "hit_spark": ("hit_spark_addr", "hit_spark"),
                    "stretch_part": ("stretch_part_addr", "stretch_part"),
                    "stretch_time": ("stretch_time_addr", "stretch_time"),
                    "post_link": ("post_link_addr", "post_link"),
                }
                addr_key, val_key = mapping[group]
                if not write_u32_field_inline(mv, addr_key, val_key, int(value)):
                    return False, "write failed"

            elif group in ("stretch_len", "stretch_width", "stretch_height"):
                mapping = {
                    "stretch_len": ("stretch_len_addr", "stretch_len"),
                    "stretch_width": ("stretch_width_addr", "stretch_width"),
                    "stretch_height": ("stretch_height_addr", "stretch_height"),
                }
                addr_key, val_key = mapping[group]
                if not write_f32_field_inline(mv, addr_key, val_key, float(value)):
                    return False, "write failed"

            elif group == "launch_profile":
                new_val = int(value) & 0xFFFFFFFF
                if not U.write_knockback(mv, launch_profile=new_val):
                    return False, "write failed"
                mv["launch_profile"] = new_val

            elif group == "kb_unknown":
                new_val = int(value) & 0xFFFFFFFF
                if not U.write_knockback(mv, kb_unknown=new_val):
                    return False, "write failed"
                mv["kb_unknown"] = new_val

            elif group == "kb_x":
                new_val = float(value)
                if not U.write_knockback(mv, kb_x=new_val):
                    return False, "write failed"
                mv["kb_x"] = new_val

            elif group == "air_kb":
                new_val = float(value)
                if not U.write_knockback(mv, air_kb=new_val):
                    return False, "write failed"
                mv["air_kb"] = new_val

            elif group == "speed_mod":
                self._ensure_speed_mod(mv)
                new_val = int(value) & 0xFF
                if not write_speed_mod_inline(mv, new_val, U.WRITER_AVAILABLE):
                    return False, "write failed"
                mv["speed_mod"] = new_val

            elif group == "attack_property":
                self._ensure_attack_property(mv)
                new_val = int(value) & 0xFF
                if not self._write_attack_property_inline(mv, new_val):
                    return False, "write failed"
                mv["attack_property"] = new_val

            elif group == "hit_reaction":
                new_val = int(value) & 0xFFFFFFFF
                if not write_hit_reaction_inline(mv, new_val, U.WRITER_AVAILABLE):
                    return False, "write failed"
                mv["hit_reaction"] = new_val

            elif group == "superbg":
                self._ensure_superbg_for_patch(mv)
                enabled = self._patch_bool_enabled(value)
                if not write_superbg_inline(mv, enabled, U.WRITER_AVAILABLE):
                    return False, "write failed"

            elif group == "combo_kb_mod":
                new_val = int(value) & 0xFF
                if not write_combo_kb_mod_inline(mv, new_val, U.WRITER_AVAILABLE):
                    return False, "write failed"
                mv["combo_kb_mod"] = new_val

            elif group == "proj_dmg":
                new_val = int(value) & 0xFFFF
                if not write_proj_dmg_inline(mv, new_val, U.WRITER_AVAILABLE):
                    return False, "write failed"
                mv["proj_dmg"] = new_val

            elif group == "hb":
                new_val = float(value)
                if not U.write_hitbox_radius(mv, new_val):
                    return False, "write failed"
                mv["hb_r"] = new_val

            else:
                return False, f"unsupported group {group}"

            self._apply_patch_tree_update(item_id, mv, group)
            return True, "ok"
        except Exception as e:
            return False, str(e)

    def _load_fd_patch_config(self):
        if not U.WRITER_AVAILABLE:
            messagebox.showerror("Load patch", "Writer unavailable")
            return

        path = filedialog.askopenfilename(
            parent=self.root,
            title="Load frame-data patch",
            initialdir=self._patch_default_dir(),
            filetypes=(("TvC frame-data patch", "*.json"), ("All files", "*.*")),
        )
        if not path:
            return

        doc = self._read_patch_document(path)
        if doc is None:
            return

        char_key, char_data = self._patch_character_section(doc)
        if not char_data:
            available = ", ".join(sorted(str(k) for k in (doc.get("characters") or {}).keys())) or "none"
            messagebox.showerror(
                "Load patch",
                f"This patch has no section for {self._patch_char_key()}.\nAvailable characters: {available}",
            )
            return

        changes = char_data.get("changes") or []
        if not isinstance(changes, list) or not changes:
            self._status_var.set(f"Patch section for {char_key} has no changes")
            return

        applied = 0
        skipped = 0
        failures = []
        touched = set()

        # Apply animation swaps last. That preserves ID-based matching for
        # other entries in the same row when a patch is applied without relying
        # on exact absolute addresses.
        ordered_changes = sorted(
            changes,
            key=lambda e: 1 if isinstance(e, dict) and str(e.get("group") or "") == "move" else 0,
        )

        for entry in ordered_changes:
            if not isinstance(entry, dict):
                skipped += 1
                continue
            item_id, mv, match_kind = self._find_patch_target(entry)
            if not item_id or not mv:
                skipped += 1
                selector = entry.get("selector") or {}
                label = selector.get("move_label") or selector.get("abs") or entry.get("group") or "unknown"
                failures.append(f"not found: {label}")
                continue

            group = str(entry.get("group") or "")
            self._begin_edit_snapshot(item_id, mv, group)
            ok, reason = self._apply_patch_change(item_id, mv, entry)
            if ok:
                applied += 1
                touched.add(item_id)
                self._after_cell_write(item_id, mv, group)
            else:
                skipped += 1
                selector = entry.get("selector") or {}
                label = selector.get("move_label") or selector.get("abs") or group or "unknown"
                failures.append(f"{label}: {reason}")

        for item_id in touched:
            try:
                self._apply_row_tags(item_id, self.move_to_tree_item.get(item_id) or {})
            except Exception:
                pass

        try:
            sel = self.tree.selection()
            if sel:
                self._refresh_inspector(sel[0], self.move_to_tree_item.get(sel[0]))
        except Exception:
            pass

        self._last_patch_config_path = path
        msg = f"Loaded patch for {char_key}: applied {applied}, skipped {skipped}"
        self._status_var.set(msg)
        if failures:
            messagebox.showwarning("Load patch", msg + "\n\n" + "\n".join(failures[:10]))
        else:
            messagebox.showinfo("Load patch", msg)

    # ---------- Reset to original ----------

    def _reset_all_moves(self):
        if not U.WRITER_AVAILABLE:
            messagebox.showerror("Error", "Writer unavailable")
            return

        if not self._dirty_cells:
            self._status_var.set("No changed values to reset")
            return

        reset_count = 0
        failed_writes = []
        touched_items = set()

        # Only touch fields changed during this editor session. This avoids the
        # old full-list reset pass that walked every scanned move.
        for key, snap in list(self._dirty_cells.items()):
            item_id = snap.get("item_id")
            mv = snap.get("mv") or {}
            group = snap.get("group")
            old = snap.get("mv_values") or {}
            abs_addr = mv.get("abs")
            ok = False

            try:
                self._suppress_dirty_tracking = True

                if str(group).startswith("super_dispatch:"):
                    col = FSI.column_for_super_group(group)
                    val = old.get("value")
                    if col and val is not None and FSI.write_super_value(mv, col, val):
                        FSI.apply_super_tree_value(self.tree, item_id, mv, col)
                        ok = True

                elif str(group).startswith("projectile:"):
                    col = FPI.column_for_projectile_group(group)
                    val = old.get("value")
                    if col and val is not None and FPI.write_projectile_value(mv, col, val):
                        FPI.apply_projectile_tree_value(self.tree, item_id, mv, col)
                        ok = True

                elif group == "move":
                    old_id = old.get("id")
                    if old_id is not None and self._write_anim_id(mv, int(old_id)):
                        mv["id"] = int(old_id)
                        if "move_name" in old:
                            mv["move_name"] = old.get("move_name")
                        cname = self.target_slot.get("char_name", "-")
                        pretty = U.pretty_move_name(int(old_id), cname)
                        dup_idx = mv.get("dup_index")
                        if dup_idx is not None:
                            pretty = f"{pretty} (Tier{dup_idx + 1})"
                        self.tree.set(item_id, "move", f"{pretty} [0x{int(old_id):04X}]")
                        ok = True

                elif group == "damage":
                    val = old.get("damage")
                    if val is not None and U.write_damage(mv, int(val)):
                        mv["damage"] = int(val)
                        self.tree.set(item_id, "damage", str(int(val)))
                        ok = True

                elif group == "meter":
                    val = old.get("meter")
                    if val is not None and U.write_meter(mv, int(val)):
                        mv["meter"] = int(val)
                        self.tree.set(item_id, "meter", str(int(val)))
                        ok = True

                elif group == "active":
                    s = old.get("active_start")
                    e = old.get("active_end")
                    if s is not None and e is not None and U.write_active_frames(mv, int(s), int(e)):
                        mv["active_start"] = int(s)
                        mv["active_end"] = int(e)
                        self.tree.set(item_id, "startup", str(int(s)))
                        self.tree.set(item_id, "active", f"{int(s)}-{int(e)}")
                        ok = True

                elif group == "active2":
                    s = old.get("active2_start")
                    e = old.get("active2_end")
                    if s is not None and e is not None:
                        mv["active2_addr"] = old.get("active2_addr")
                        if write_active2_frames_inline(mv, int(s), int(e), U.WRITER_AVAILABLE):
                            mv["active2_start"] = int(s)
                            mv["active2_end"] = int(e)
                            self.tree.set(item_id, "active2", f"{int(s)}-{int(e)}")
                            ok = True

                elif group == "hitstun":
                    val = old.get("hitstun")
                    if val is not None and U.write_hitstun(mv, int(val)):
                        mv["hitstun"] = int(val)
                        self.tree.set(item_id, "hitstun", U.fmt_stun(int(val)))
                        ok = True

                elif group == "blockstun":
                    val = old.get("blockstun")
                    if val is not None and U.write_blockstun(mv, int(val)):
                        mv["blockstun"] = int(val)
                        self.tree.set(item_id, "blockstun", U.fmt_stun(int(val)))
                        ok = True

                elif group == "hitstop":
                    val = old.get("hitstop")
                    if val is not None and U.write_hitstop(mv, int(val)):
                        mv["hitstop"] = int(val)
                        self.tree.set(item_id, "hitstop", U.fmt_stun(int(val)))
                        ok = True

                elif group in ("hit_spark", "stretch_part", "stretch_time", "post_link"):
                    mapping = {
                        "hit_spark": ("hit_spark_addr", "hit_spark", U.fmt_hit_spark_ui),
                        "stretch_part": ("stretch_part_addr", "stretch_part", U.fmt_stretch_part_ui),
                        "stretch_time": ("stretch_time_addr", "stretch_time", U.fmt_stretch_time_ui),
                        "post_link": ("post_link_addr", "post_link", U.fmt_post_link_ui),
                    }
                    addr_key, val_key, fmt_func = mapping[group]
                    val = old.get(val_key)
                    mv[addr_key] = old.get(addr_key)
                    if val is not None and write_u32_field_inline(mv, addr_key, val_key, int(val)):
                        self.tree.set(item_id, group, fmt_func(mv))
                        ok = True

                elif group in ("stretch_len", "stretch_width", "stretch_height"):
                    mapping = {
                        "stretch_len": ("stretch_len_addr", "stretch_len", U.fmt_stretch_len_ui),
                        "stretch_width": ("stretch_width_addr", "stretch_width", U.fmt_stretch_width_ui),
                        "stretch_height": ("stretch_height_addr", "stretch_height", U.fmt_stretch_height_ui),
                    }
                    addr_key, val_key, fmt_func = mapping[group]
                    val = old.get(val_key)
                    mv[addr_key] = old.get(addr_key)
                    if val is not None and write_f32_field_inline(mv, addr_key, val_key, float(val)):
                        self.tree.set(item_id, group, fmt_func(mv))
                        ok = True

                elif group in ("launch_profile", "kb_unknown", "kb_x", "air_kb"):
                    kwargs = {}
                    col_for_group = {
                        "launch_profile": "launch_profile",
                        "kb_unknown": "kb_unknown",
                        "kb_x": "kb_x",
                        "air_kb": "air_kb",
                    }[group]
                    val = old.get(col_for_group)
                    if val is not None:
                        kwargs[col_for_group] = val
                        if U.write_knockback(mv, **kwargs):
                            mv[col_for_group] = val
                            if group == "launch_profile":
                                self.tree.set(item_id, "launch_profile", U.fmt_launch_profile_ui(mv))
                            elif group == "kb_unknown":
                                self.tree.set(item_id, "kb_unknown", U.fmt_kb_unknown_ui(mv))
                            elif group == "kb_x":
                                self.tree.set(item_id, "kb_x", U.fmt_kb_x_ui(mv))
                            elif group == "air_kb":
                                self.tree.set(item_id, "air_kb", U.fmt_air_kb_ui(mv))
                            ok = True

                elif group == "speed_mod":
                    val = old.get("speed_mod")
                    mv["speed_mod_addr"] = old.get("speed_mod_addr")
                    if val is not None and write_speed_mod_inline(mv, int(val), U.WRITER_AVAILABLE):
                        mv["speed_mod"] = int(val)
                        self.tree.set(item_id, "speed_mod", U.fmt_speed_mod_ui(int(val)))
                        ok = True

                elif group == "attack_property":
                    val = old.get("attack_property")
                    mv["attack_property_addr"] = old.get("attack_property_addr")
                    if val is not None and self._write_attack_property_inline(mv, int(val)):
                        mv["attack_property"] = int(val) & 0xFF
                        self.tree.set(item_id, "attack_property", fmt_attack_property(int(val)))
                        ok = True

                elif group == "hit_reaction":
                    val = old.get("hit_reaction")
                    if val is not None and write_hit_reaction_inline(mv, int(val), U.WRITER_AVAILABLE):
                        mv["hit_reaction"] = int(val)
                        self.tree.set(item_id, "hit_reaction", U.fmt_hit_reaction(int(val)))
                        ok = True

                elif group == "superbg":
                    val = old.get("superbg_val")
                    mv["superbg_addr"] = old.get("superbg_addr")
                    if val is not None:
                        if write_superbg_inline(mv, bool(val == SUPERBG_ON), U.WRITER_AVAILABLE):
                            self.tree.set(item_id, "superbg", U.fmt_superbg(mv.get("superbg_val")))
                            ok = True

                elif group == "combo_kb_mod":
                    val = old.get("combo_kb_mod")
                    mv["combo_kb_mod_addr"] = old.get("combo_kb_mod_addr")
                    if val is not None and write_combo_kb_mod_inline(mv, int(val), U.WRITER_AVAILABLE):
                        mv["combo_kb_mod"] = int(val)
                        if "combo_kb_mod" in self.tree["columns"]:
                            self.tree.set(item_id, "combo_kb_mod", f"{int(val)} (0x{int(val):02X})")
                        ok = True

                elif group == "proj_dmg":
                    val = old.get("proj_dmg")
                    mv["proj_tpl"] = old.get("proj_tpl")
                    if val is not None and write_proj_dmg_inline(mv, int(val), U.WRITER_AVAILABLE):
                        mv["proj_dmg"] = int(val)
                        if "proj_dmg" in self.tree["columns"]:
                            self.tree.set(item_id, "proj_dmg", str(int(val)))
                        ok = True

                elif group == "hb":
                    val = old.get("hb_r")
                    if val is not None:
                        mv["hb_off"] = old.get("hb_off")
                        if U.write_hitbox_radius(mv, float(val)):
                            mv["hb_r"] = float(val)
                            mv["hb_candidates"] = old.get("hb_candidates")
                            if "hb_main" in self.tree["columns"]:
                                self.tree.set(item_id, "hb_main", f"{float(val):.1f}")
                            if "hb" in self.tree["columns"]:
                                self.tree.set(item_id, "hb", U.format_candidate_list(mv.get("hb_candidates") or []))
                            ok = True

            except Exception as e:
                ok = False
                failed_writes.append(f"{group} @ {('0x%08X' % abs_addr) if abs_addr else 'unknown'} ({e})")
            finally:
                self._suppress_dirty_tracking = False

            if item_id:
                touched_items.add(item_id)

            if ok:
                self._dirty_cells.pop(key, None)
                reset_count += 1
            elif group:
                failed_writes.append(f"{group} @ {('0x%08X' % abs_addr) if abs_addr else 'unknown'}")

        self._update_dirty_ui()
        for row in touched_items:
            try:
                self._apply_row_tags(row, self.move_to_tree_item.get(row) or {})
            except Exception:
                pass
        try:
            sel = self.tree.selection()
            if sel:
                self._refresh_inspector(sel[0], self.move_to_tree_item.get(sel[0]))
        except Exception:
            pass

        msg = f"Reset changed values: {reset_count} write(s) restored"
        if failed_writes:
            msg += " | failed: " + ", ".join(failed_writes[:6])
        self._status_var.set(msg)
        if failed_writes:
            messagebox.showwarning("Reset changed", msg)

    # ---------- Double-click routing ----------

    def _on_double_click(self, event):
        region = self.tree.identify_region(event.x, event.y)
        if region != "cell":
            return

        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if not item or not column:
            return

        col_name = self._resolve_tree_column_name(column)
        if not col_name:
            return
        mv = self.move_to_tree_item.get(item)
        if not mv:
            return

        if not U.WRITER_AVAILABLE and not FPI.is_projectile_row(mv) and not FSI.is_super_row(mv):
            messagebox.showerror("Error", "Writer unavailable")
            return

        current_val = self.tree.set(item, col_name)
        if col_name.startswith("dispatch_") and not FSI.is_super_row(mv):
            self._status_var.set("Dispatch fields are only editable on super dispatch rows.")
            return
        if FSI.is_super_row(mv) and not FSI.super_editable(col_name):
            self._status_var.set("That super dispatch field is display-only.")
            return
        if col_name.startswith("proj_") and not FPI.is_projectile_row(mv):
            self._status_var.set("Projectile fields are only editable on projectile rows.")
            return
        if FPI.is_projectile_row(mv) and not FPI.projectile_editable(col_name):
            self._status_var.set("That projectile field is display-only.")
            return
        if col_name in {"hits", "link"}:
            if col_name == "hits":
                self._status_var.set("Expand a multi-hit move to view and edit each detected hit bundle separately.")
            else:
                self._status_var.set("Link is display-only. It groups related move-table sections.")
            return
        if col_name == "move" and mv.get("_hit_segment_index") is not None:
            self._status_var.set("Hit rows edit hit data only. Use the parent move row to replace the animation.")
            return
        self._begin_edit_snapshot(item, mv, col_name)

        if col_name == "move":
            self._show_move_edit_menu(event, item, mv)
            self._apply_row_tags(item, mv)
            self._set_status_for_item(item, mv)
            return

        if col_name == "speed_mod":
            self._edit_speed_mod(item, mv, current_val)
        elif col_name == "attack_property":
            self._edit_attack_property(item, mv, current_val)
        else:
            self._route_standard_edit(col_name, item, mv, current_val)

        self._apply_row_tags(item, mv)
        self._set_status_for_item(item, mv)
        self._refresh_inspector(item, mv)


    def _edit_super_dispatch_cell(self, col_name: str, item: str, mv: dict, current_val: str) -> None:
        info = getattr(FSI, "super_field_edit_info", lambda c: FSI.SUPER_DISPATCH_FIELD_INFO.get(c))(col_name)
        if not info:
            self._status_var.set("That super field is display-only.")
            return
        _hit_key, label, typ = info
        addr = FSI.super_field_addr(mv, col_name)
        addr_txt = f"0x{int(addr):08X}" if addr else "not found"
        display_val = current_val or FSI.format_super_value(mv, col_name)
        initial_val = FSI.super_edit_initial_value(mv, col_name)
        if col_name.startswith("dispatch_"):
            note = "Dispatch rows are the super caller layer. Phase length is the safest poke; selector/link are dangerous."
            title = f"Edit {label}"
        else:
            note = "This is a super-owned field sniffed by parent -> child graph ownership. It writes the child script/payload field, not the 00/23 parent row."
            title = f"Edit {label}"
        prompt = (
            f"Row: {mv.get('move_name') or 'Super Dispatch'}\n"
            f"Field: {label}\n"
            f"Address: {addr_txt}\n"
            f"Type: {typ}\n"
            f"Current: {display_val}\n\n"
            f"{note}\n\n"
            "New value:"
        )
        new_val = simpledialog.askstring(
            title,
            prompt,
            parent=self.root,
            initialvalue=str(initial_val or "0"),
        )
        if new_val is None:
            return
        try:
            parsed = FSI.parse_super_input(col_name, new_val)
        except Exception as e:
            messagebox.showerror("Invalid", f"Invalid {label}: {e}", parent=self.root)
            return
        try:
            ok = FSI.write_super_value(mv, col_name, parsed)
        except Exception as e:
            messagebox.showerror("Write failed", str(e), parent=self.root)
            return
        if not ok:
            messagebox.showerror("Write failed", "Could not write super value to Dolphin.", parent=self.root)
            return
        FSI.apply_super_tree_value(self.tree, item, mv, col_name)
        self._notify_fd_cell_changed(item, mv, col_name)
        if self._status_var is not None:
            self._status_var.set(f"Wrote {label} to {addr_txt}")


    def _edit_projectile_cell(self, col_name: str, item: str, mv: dict, current_val: str) -> None:
        info = FPI.PROJECTILE_FIELD_INFO.get(col_name)
        if not info:
            self._status_var.set("That projectile field is display-only.")
            return
        _hit_key, label, typ = info
        addr = FPI.projectile_field_addr(mv, col_name)
        if FPI.is_projectile_emitter_row(mv):
            peer_count = int(((mv.get("_proj_hit") or {}).get("emitter_count") or 0))
            addr_txt = f"bulk group: {peer_count} card(s)"
        else:
            addr_txt = f"0x{int(addr):08X}" if addr else "not found"
        display_val = current_val or FPI.format_projectile_value(mv, col_name)
        initial_val = FPI.projectile_edit_initial_value(mv, col_name)
        prompt = (
            f"Move: {mv.get('move_name') or 'Projectile'}\n"
            f"Field: {label}\n"
            f"Address: {addr_txt}\n"
            f"Current: {display_val}\n\n"
            "New value:"
        )
        new_val = simpledialog.askstring(
            f"Edit {label}",
            prompt,
            parent=self.root,
            initialvalue=str(initial_val or "0"),
        )
        if new_val is None:
            return
        try:
            parsed = FPI.parse_projectile_input(col_name, new_val)
        except Exception as e:
            messagebox.showerror("Invalid", f"Invalid {label}: {e}", parent=self.root)
            return
        try:
            ok = FPI.write_projectile_value(mv, col_name, parsed)
        except Exception as e:
            messagebox.showerror("Write failed", str(e), parent=self.root)
            return
        if not ok:
            messagebox.showerror("Write failed", "Could not write projectile value to Dolphin.", parent=self.root)
            return
        FPI.apply_projectile_tree_value(self.tree, item, mv, col_name)

        # Emitter rows bulk-write child projectile cards. Keep the physical rows
        # visually in sync so the user does not see stale bullet/card values
        # underneath the emitter after a successful edit.
        if FPI.is_projectile_emitter_row(mv):
            try:
                peer_addrs = FPI.projectile_damage_peer_base_addrs(mv)
            except Exception:
                peer_addrs = set()
            emitter_col_alias = {
                "proj_speed": "proj_ps_offset_x",
                "proj_accel": "proj_ps_offset_y",
                "proj_hitbox": "proj_ps_scale",
                "proj_radius": "proj_ps_scale",
                "proj_life": "proj_ps_lifetime",
            }
            if peer_addrs:
                for other_item, other_mv in list((self.move_to_tree_item or {}).items()):
                    if other_item == item or not FPI.is_projectile_row(other_mv) or FPI.is_projectile_emitter_row(other_mv):
                        continue
                    other_hit = other_mv.get("_proj_hit") or {}
                    try:
                        other_addr = int(other_hit.get("addr") or other_mv.get("abs") or 0)
                    except Exception:
                        other_addr = 0
                    if other_addr not in peer_addrs:
                        continue
                    actual_col = col_name
                    try:
                        if str(other_hit.get("fmt") or "") in getattr(FPI.P, "PROJECTILE_SUPER_FMTS", set()):
                            actual_col = emitter_col_alias.get(col_name, col_name)
                    except Exception:
                        actual_col = emitter_col_alias.get(col_name, col_name)
                    info2 = FPI.PROJECTILE_FIELD_INFO.get(actual_col)
                    if info2:
                        hit_key2 = info2[0]
                        other_hit[hit_key2] = parsed
                        if hit_key2 == "dmg":
                            other_hit["dmg"] = int(parsed)
                            other_mv["damage"] = int(parsed)
                        other_mv["_proj_hit"] = other_hit
                    try:
                        FPI.apply_projectile_tree_value(self.tree, other_item, other_mv, actual_col)
                        self._apply_row_tags(other_item, other_mv)
                    except Exception:
                        pass

        # If this projectile has copy/alt records behind the same visible move,
        # keep their rows visually in sync. The actual memory write already
        # updated the peer addresses; this just prevents stale duplicate rows.
        if col_name == "damage" and not FPI.is_projectile_emitter_row(mv):
            try:
                peer_addrs = FPI.projectile_damage_peer_base_addrs(mv)
            except Exception:
                peer_addrs = set()
            if peer_addrs:
                for other_item, other_mv in list((self.move_to_tree_item or {}).items()):
                    if other_item == item or not FPI.is_projectile_row(other_mv):
                        continue
                    other_hit = other_mv.get("_proj_hit") or {}
                    try:
                        other_addr = int(other_hit.get("addr") or other_mv.get("abs") or 0)
                    except Exception:
                        other_addr = 0
                    if other_addr not in peer_addrs:
                        continue
                    other_hit["dmg"] = int(parsed)
                    other_mv["_proj_hit"] = other_hit
                    other_mv["damage"] = int(parsed)
                    try:
                        FPI.apply_projectile_tree_value(self.tree, other_item, other_mv, col_name)
                        self._apply_row_tags(other_item, other_mv)
                    except Exception:
                        pass

        self._notify_fd_cell_changed(item, mv, col_name)
        if self._status_var is not None:
            self._status_var.set(f"Wrote {label} to {addr_txt}")

    def _route_standard_edit(self, col_name: str, item: str, mv: dict, current_val: str) -> None:
        if FSI.is_super_row(mv):
            self._edit_super_dispatch_cell(col_name, item, mv, current_val)
            return
        if FPI.is_projectile_row(mv):
            self._edit_projectile_cell(col_name, item, mv, current_val)
            return
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
        elif col_name == "hitstop":
            self._edit_hitstop(item, mv, current_val)
        elif col_name == "hit_spark":
            self._edit_hit_spark(item, mv, current_val)
        elif col_name == "stretch_part":
            self._edit_stretch_part(item, mv, current_val)
        elif col_name == "stretch_len":
            self._edit_stretch_len(item, mv, current_val)
        elif col_name == "stretch_width":
            self._edit_stretch_width(item, mv, current_val)
        elif col_name == "stretch_height":
            self._edit_stretch_height(item, mv, current_val)
        elif col_name == "stretch_time":
            self._edit_stretch_time(item, mv, current_val)
        elif col_name == "post_link":
            self._edit_post_link(item, mv, current_val)
        elif col_name == "kb_type":
            self._edit_kb_type(item, mv, current_val)
        elif col_name == "launch_profile":
            self._edit_launch_profile(item, mv, current_val)
        elif col_name == "kb_unknown":
            self._edit_kb_unknown(item, mv, current_val)
        elif col_name == "kb_x":
            self._edit_kb_x(item, mv, current_val)
        elif col_name == "air_kb":
            self._edit_air_kb(item, mv, current_val)
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

        col_name = self._resolve_tree_column_name(column)
        if not col_name:
            return

        menu = tk.Menu(self.root, tearoff=0)

        addr_map = {
            "damage": ("damage_addr", "Damage"),
            "meter": ("meter_addr", "Meter"),
            "active": ("active_addr", "Active"),
            "active2": ("active2_addr", "Active 2"),
            "hitstun": ("stun_addr", "Hitstun", 15),
            "blockstun": ("stun_addr", "Blockstun", 31),
            "hitstop": ("stun_addr", "Hitstop", 38),
            "hit_spark": ("hit_spark_addr", "Hit Spark"),
            "stretch_part": ("stretch_part_addr", "Stretch Part"),
            "stretch_len": ("stretch_len_addr", "Reach Length"),
            "stretch_width": ("stretch_width_addr", "Reach Width"),
            "stretch_height": ("stretch_height_addr", "Reach Height"),
            "stretch_time": ("stretch_time_addr", "Stretch Timing"),
            "post_link": ("post_link_addr", "Post-Animation Link"),
            "kb_type": ("knockback_addr", "KB Style", 1),
            "launch_profile": ("knockback_addr", "Extra Launch", 4),
            "kb_unknown": ("knockback_addr", "Launch Adjust", 8),
            "kb_x": ("knockback_addr", "KB X", 12),
            "air_kb": ("knockback_addr", "Arc", 16),
            "speed_mod": ("speed_mod_addr", "Speed Mod"),
            "attack_property": ("attack_property_addr", "Attack Property"),
            "superbg": ("superbg_addr", "SuperBG"),
            "abs": ("abs", "Move"),
        }

        if col_name in addr_map:
            addr_info = addr_map[col_name]
            if len(addr_info) == 3:
                addr_key, label, addr_offset = addr_info
            else:
                addr_key, label = addr_info
                addr_offset = 0
            addr = mv.get(addr_key)
            if addr and addr_offset:
                addr = int(addr) + int(addr_offset)

            if addr_key == "speed_mod_addr" and not addr:
                self._ensure_speed_mod(mv)
                addr = mv.get("speed_mod_addr")
                if addr:
                    self.tree.set(item, "speed_mod", U.fmt_speed_mod_ui(mv.get("speed_mod")))

            if addr_key == "attack_property_addr" and not addr:
                self._ensure_attack_property(mv)
                addr = mv.get("attack_property_addr")
                if addr:
                    self.tree.set(item, "attack_property", fmt_attack_property(mv.get("attack_property")))

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