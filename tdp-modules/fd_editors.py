# fd_editors.py
#
# All "edit X" handlers live here as a mixin to keep fd_window small.

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox

from fd_dialogs import ReplaceMoveDialog

from fd_patterns import (
    find_combo_kb_mod_addr,
    find_hit_result_flags_addr,
    find_superbg_addr,
    SUPERBG_ON,
)

from fd_write_helpers import (
    write_hit_reaction_inline,
    write_active2_frames_inline,
    write_anim_id_inline,
    write_combo_kb_mod_inline,
    write_superbg_inline,
    write_proj_dmg_inline,
    write_u32_field_inline,
    write_f32_field_inline,

)

import fd_utils as U
from fd_widgets import ManualAnimIDDialog, get_field_help, ask_integer_with_help, ask_float_with_help, ask_hit_result_flags_with_presets, apply_titlebar_icon, configure_light_dialog, build_dialog_shell, make_list_picker, finalize_dialog_geometry


class FDCellEditorsMixin:
    '\n    Requires the host class to define:\n      - self.root, self.tree\n      - self.moves, self.target_slot\n      - self._apply_row_tags(item, mv), self._set_status_for_item(item, mv)\n      - self._show_move_edit_menu(event, item, mv) (or use provided)\n      - self._clone_move_block_y2(new_mv, mv) if the operator use full replace mode\n    '

    def _notify_fd_cell_changed(self, item, mv, col_name: str) -> None:
        cb = getattr(self, "_after_cell_write", None)
        if callable(cb):
            try:
                cb(item, mv, col_name)
            except Exception:
                pass

    # ----- Active2 (inline) -----

    def _edit_active2(self, item, mv, current: str):
        cur_s, cur_e = U.ensure_range_pair(
            current,
            default_s=mv.get("active2_start", 1) or 1,
            default_e=mv.get("active2_end", mv.get("active2_start", 1) or 1) or 1,
        )

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Active 2 Frames")
        configure_light_dialog(dlg, self.root, width=440, height=260)
        addr = mv.get("active2_addr")
        outer = build_dialog_shell(
            dlg,
            title_text="Active 2 Frames",
            help_text=get_field_help("active2"),
            current_text=f"Current: {cur_s}-{cur_e}",
            address=addr if addr else None,
            wrap=400,
        )
        tk.Label(outer, text="Manual entry:", bg="#F3F4F7", fg="#111111", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 2))
        row1 = tk.Frame(outer, bg="#F3F4F7"); row1.pack(anchor="w", pady=3)
        tk.Label(row1, text="Start frame:", bg="#F3F4F7", fg="#111111", font=("Segoe UI", 10)).pack(side="left")
        sv = tk.IntVar(value=cur_s)
        ttk.Entry(row1, textvariable=sv, width=10).pack(side="left", padx=(8,0))
        row2 = tk.Frame(outer, bg="#F3F4F7"); row2.pack(anchor="w", pady=3)
        tk.Label(row2, text="End frame:", bg="#F3F4F7", fg="#111111", font=("Segoe UI", 10)).pack(side="left")
        ev = tk.IntVar(value=cur_e)
        ttk.Entry(row2, textvariable=ev, width=10).pack(side="left", padx=(11,0))

        def on_ok():
            s = int(sv.get())
            e = int(ev.get())
            if e < s:
                e = s
            if write_active2_frames_inline(mv, s, e, U.WRITER_AVAILABLE):
                self.tree.set(item, "active2", f"{s}-{e}")
                mv["active2_start"] = s
                mv["active2_end"] = e
                self._notify_fd_cell_changed(item, mv, "active2")
            else:
                messagebox.showerror("Error", "Failed to write Active 2 frames")
            dlg.destroy()

        btns = tk.Frame(outer, bg="#F3F4F7")
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="OK", command=on_ok).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right")
        finalize_dialog_geometry(dlg, 540, 440)
        finalize_dialog_geometry(dlg, 440, 260)

    # ----- Combo KB Mod -----

    def _ensure_combo_kb_mod(self, mv) -> None:
        if mv.get("combo_kb_mod_addr") is not None:
            return
        move_abs = mv.get("abs")
        if not move_abs:
            return
        try:
            from dolphin_io import rbytes
            addr, cur, sig = find_combo_kb_mod_addr(move_abs, rbytes)
        except Exception:
            addr, cur, sig = (None, None, None)
        if addr:
            mv["combo_kb_mod_addr"] = addr
            mv["combo_kb_mod"] = cur
            mv["combo_kb_sig"] = sig

    def _edit_combo_kb_mod(self, item, mv, current: str):
        self._ensure_combo_kb_mod(mv)
        addr = mv.get("combo_kb_mod_addr")
        if not addr:
            messagebox.showerror(
                "Combo KB Mod",
                "Signature not found for this move.\nTry Refresh visible, or this move may not have the pattern.",
            )
            return

        cur_val = mv.get("combo_kb_mod")
        if cur_val is None:
            try:
                cur_val = int(str(current).split()[0])
            except Exception:
                cur_val = 0

        new_val = ask_integer_with_help(
            self.root,
            title="Edit Combo KB Mod",
            prompt="New combo KB mod byte (0-255)",
            help_text=get_field_help("combo_kb_mod"),
            initialvalue=int(cur_val),
            minvalue=0,
            maxvalue=255,
            address=int(addr),
        )
        if new_val is None:
            return

        if write_combo_kb_mod_inline(mv, int(new_val), U.WRITER_AVAILABLE):
            mv["combo_kb_mod"] = int(new_val)
            self.tree.set(item, "combo_kb_mod", f"{new_val} (0x{new_val:02X})")
            self._notify_fd_cell_changed(item, mv, "combo_kb_mod")
        else:
            messagebox.showerror("Combo KB Mod", "Failed to write Combo KB Mod byte.")


    def _ensure_hit_result_flags(self, mv) -> None:
        """Resolve the OTG / hit-result packet only for the row being edited.

        First-open profile rendering deliberately avoids this loose 0x900-byte
        scan for every move.  Keeping this fallback here preserves the old
        edit behavior without making the whole workbench wait on Dolphin.
        """
        if not isinstance(mv, dict) or mv.get("hit_result_addr") is not None:
            return
        try:
            move_abs = int(mv.get("abs") or 0)
        except Exception:
            move_abs = 0
        if not move_abs:
            return
        try:
            from dolphin_io import rbytes
            pkt, addr, value, clear_mask, ctx = find_hit_result_flags_addr(move_abs, rbytes)
        except Exception:
            pkt = addr = value = clear_mask = ctx = None
        if addr is not None:
            mv["hit_result_packet_addr"] = pkt
            mv["hit_result_addr"] = addr
            mv["hit_result_flags"] = value
            mv["hit_result_clear_mask"] = clear_mask
            mv["hit_result_sig"] = ctx

    def _edit_hit_result_flags(self, item, mv, current: str):
        self._ensure_hit_result_flags(mv)
        addr = mv.get("hit_result_addr")
        if not addr:
            messagebox.showerror(
                "Hit Result Flags",
                "Hit-result flag slot not found for this move.\nTry Refresh visible, or this move may not use the 0x80042F00/+0x240 pattern.",
            )
            return

        cur_val = mv.get("hit_result_flags")
        if cur_val is None:
            try:
                raw = str(current or "").split()[0]
                cur_val = int(raw, 16) if raw.lower().startswith("0x") else int(raw, 10)
            except Exception:
                cur_val = 0

        new_val = ask_hit_result_flags_with_presets(
            self.root,
            title="Edit Hit Result Flags",
            help_text=get_field_help("hit_result_flags"),
            initialvalue=int(cur_val),
            address=int(addr),
        )
        if new_val is None:
            return

        if write_u32_field_inline(mv, "hit_result_addr", "hit_result_flags", int(new_val)):
            self.tree.set(item, "hit_result_flags", U.fmt_hit_result_flags_ui(mv))
            self._notify_fd_cell_changed(item, mv, "hit_result_flags")
        else:
            messagebox.showerror("Hit Result Flags", "Failed to write hit-result flags.")

    # ----- Simple scalar editors (damage/meter/hitstop) -----

    def _edit_damage(self, item, mv, current: str):
        cur = U.ensure_int(current, 0)
        new_val = ask_integer_with_help(
            self.root,
            title="Edit Damage",
            prompt="New damage:",
            help_text=get_field_help("damage"),
            initialvalue=cur,
            minvalue=0,
            maxvalue=999999,
            address=mv.get("damage_addr"),
        )
        if new_val is not None and U.WRITER_AVAILABLE and U.write_damage(mv, new_val):
            self.tree.set(item, "damage", str(new_val))
            mv["damage"] = new_val
            self._notify_fd_cell_changed(item, mv, "damage")
    def _edit_proj_dmg(self, item, mv, current: str):
        # make sure the module has proj_tpl/proj_dmg populated
        if mv.get("proj_tpl") is None:
            try:
                import fd_utils as U
                U.resolve_projectile_fields_for_move(mv, region_abs=mv.get("abs"))
            except Exception:
                pass

        addr = mv.get("proj_tpl")
        if not addr:
            messagebox.showerror(
                "Projectile Damage",
                "Projectile signature not found for this move.\n"
                "Try Refresh visible.",
            )
            return

        cur_val = mv.get("proj_dmg")
        if cur_val is None:
            try:
                cur_val = int((current or "0").strip())
            except Exception:
                cur_val = 0

        new_val = ask_integer_with_help(
            self.root,
            title="Edit Projectile Damage",
            prompt="New projectile damage (0-65535)",
            help_text=get_field_help("proj_dmg"),
            initialvalue=int(cur_val),
            minvalue=0,
            maxvalue=65535,
            address=int(addr),
        )
        if new_val is None:
            return

        import fd_utils as U
        if write_proj_dmg_inline(mv, int(new_val), U.WRITER_AVAILABLE):
            self.tree.set(item, "proj_dmg", str(int(new_val)))
            # keep tpl formatted (optional but nice)
            self.tree.set(item, "proj_tpl", f"0x{int(mv.get('proj_tpl')):08X}")
            self._notify_fd_cell_changed(item, mv, "proj_dmg")
        else:
            messagebox.showerror("Projectile Damage", "Failed to write projectile damage.")

    def _edit_meter(self, item, mv, current: str):
        cur = U.ensure_int(current, 0)
        new_val = ask_integer_with_help(
            self.root,
            title="Edit Meter",
            prompt="New meter:",
            help_text=get_field_help("meter"),
            initialvalue=cur,
            minvalue=0,
            maxvalue=255,
            address=mv.get("meter_addr"),
        )
        if new_val is not None and U.WRITER_AVAILABLE and U.write_meter(mv, new_val):
            self.tree.set(item, "meter", str(new_val))
            mv["meter"] = new_val
            self._notify_fd_cell_changed(item, mv, "meter")

    def _edit_hitstop(self, item, mv, current: str):
        if str(mv.get("hitstop_source") or "") == "runtime_observed":
            try:
                messagebox.showinfo(
                    "Runtime-observed hitstop",
                    "This value was captured from the live contact. It has no verified move-table address yet, so it is intentionally read-only.",
                    parent=self.root,
                )
            except Exception:
                pass
            return
        cur = U.ensure_int(current, 0)
        new_val = ask_integer_with_help(
            self.root,
            title="Edit Hitstop (Unverified)",
            prompt="New hitstop value (unverified):",
            help_text=get_field_help("hitstop"),
            initialvalue=cur,
            minvalue=0,
            maxvalue=255,
            address=(int(mv.get("stun_addr")) + 38) if mv.get("stun_addr") else None,
        )
        if new_val is not None and U.WRITER_AVAILABLE and U.write_hitstop(mv, new_val):
            self.tree.set(item, "hitstop", str(new_val))
            mv["hitstop"] = new_val
            self._notify_fd_cell_changed(item, mv, "hitstop")

    # ----- Active frames (startup/active) -----

    def _edit_startup(self, item, mv, current: str):
        cur = U.ensure_int(current, 1) or 1
        new_val = ask_integer_with_help(
            self.root,
            title="Edit Startup",
            prompt="New startup frame:",
            help_text=get_field_help("startup"),
            initialvalue=cur,
            minvalue=1,
            maxvalue=255,
            address=mv.get("active_addr"),
        )
        if new_val is None:
            return
        end = mv.get("active_end", new_val)
        if end is None or end < new_val:
            end = new_val
        if U.WRITER_AVAILABLE and U.write_active_frames(mv, new_val, end):
            self.tree.set(item, "startup", str(new_val))
            self.tree.set(item, "active", f"{new_val}-{end}")
            mv["active_start"] = new_val
            mv["active_end"] = end
            self._notify_fd_cell_changed(item, mv, "startup")

    def _edit_active(self, item, mv, current: str):
        cur_s, cur_e = U.ensure_range_pair(
            current,
            default_s=mv.get("active_start", 1) or 1,
            default_e=mv.get("active_end", mv.get("active_start", 1) or 1) or 1,
        )

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Active Frames")
        configure_light_dialog(dlg, self.root, width=460, height=280)
        outer = build_dialog_shell(
            dlg,
            title_text="Active Frames",
            help_text=get_field_help("active"),
            current_text=f"Current: {cur_s}-{cur_e}",
            address=mv.get("active_addr"),
            wrap=420,
        )

        tk.Label(outer, text="Manual entry:", bg="#F3F4F7", fg="#111111", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 2))
        row1 = tk.Frame(outer, bg="#F3F4F7"); row1.pack(anchor="w", pady=3)
        tk.Label(row1, text="Active start:", bg="#F3F4F7", fg="#111111", font=("Segoe UI", 10)).pack(side="left")
        sv = tk.IntVar(value=cur_s)
        ttk.Entry(row1, textvariable=sv, width=10).pack(side="left", padx=(8, 0))
        row2 = tk.Frame(outer, bg="#F3F4F7"); row2.pack(anchor="w", pady=3)
        tk.Label(row2, text="Active end:", bg="#F3F4F7", fg="#111111", font=("Segoe UI", 10)).pack(side="left")
        ev = tk.IntVar(value=cur_e)
        ttk.Entry(row2, textvariable=ev, width=10).pack(side="left", padx=(12, 0))

        def on_ok():
            s = int(sv.get())
            e = int(ev.get())
            if e < s:
                e = s
            if U.WRITER_AVAILABLE and U.write_active_frames(mv, s, e):
                self.tree.set(item, "startup", str(s))
                self.tree.set(item, "active", f"{s}-{e}")
                mv["active_start"] = s
                mv["active_end"] = e
                self._notify_fd_cell_changed(item, mv, "active")
            dlg.destroy()

        btns = tk.Frame(outer, bg="#F3F4F7")
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="OK", command=on_ok).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right")
        finalize_dialog_geometry(dlg, 460, 280)

    # ----- Stun fields -----

    def _edit_hitstun(self, item, mv, current: str):
        if str(mv.get("hitstun_source") or "") == "runtime_observed":
            try:
                messagebox.showinfo(
                    "Runtime-observed hitstun",
                    "This value was captured from the victim's live resolver. "
                    "It has no verified move-table address yet, so it is intentionally read-only.",
                    parent=self.root,
                )
            except Exception:
                pass
            return
        cur = U.unfmt_stun(current) if current else 0
        new_val = ask_integer_with_help(
            self.root,
            title="Edit Hitstun",
            prompt="New hitstun:",
            help_text=get_field_help("hitstun"),
            initialvalue=cur,
            minvalue=0,
            maxvalue=255,
            address=(int(mv.get("stun_addr")) + 15) if mv.get("stun_addr") else None,
        )
        if new_val is not None and U.WRITER_AVAILABLE and U.write_hitstun(mv, new_val):
            self.tree.set(item, "hitstun", U.fmt_stun(new_val))
            mv["hitstun"] = new_val
            self._notify_fd_cell_changed(item, mv, "hitstun")

    def _edit_blockstun(self, item, mv, current: str):
        if str(mv.get("blockstun_source") or "") == "runtime_observed":
            try:
                messagebox.showinfo(
                    "Runtime-observed blockstun",
                    "This value was captured from the victim's live resolver. "
                    "It has no verified move-table address yet, so it is intentionally read-only.",
                    parent=self.root,
                )
            except Exception:
                pass
            return
        cur = U.unfmt_stun(current) if current else 0
        new_val = ask_integer_with_help(
            self.root,
            title="Edit Blockstun",
            prompt="New blockstun:",
            help_text=get_field_help("blockstun"),
            initialvalue=cur,
            minvalue=0,
            maxvalue=255,
            address=(int(mv.get("stun_addr")) + 31) if mv.get("stun_addr") else None,
        )
        if new_val is not None and U.WRITER_AVAILABLE and U.write_blockstun(mv, new_val):
            self.tree.set(item, "blockstun", U.fmt_stun(new_val))
            mv["blockstun"] = new_val
            self._notify_fd_cell_changed(item, mv, "blockstun")

    # ----- Knockback / launch packet -----

    def _refresh_kb_cells(self, item, mv) -> None:
        """Refresh the split hit/launch columns if this tree build has them."""
        try:
            cols = set(self.tree["columns"])
        except Exception:
            cols = set()
        updates = {
            "hitstun": U.fmt_stun(mv.get("hitstun")),
            "blockstun": U.fmt_stun(mv.get("blockstun")),
            "hitstop": U.fmt_stun(mv.get("hitstop")),
            "kb_type": U.fmt_kb_type_ui(mv),
            "launch_profile": U.fmt_launch_profile_ui(mv),
            "kb_unknown": U.fmt_kb_unknown_ui(mv),
            "ground_kb": U.fmt_ground_kb_ui(mv),
            "ground_kb_y": U.fmt_ground_kb_y_ui(mv),
            "kb_x": U.fmt_kb_x_ui(mv),
            "air_kb": U.fmt_air_kb_ui(mv),
            "kb": U.fmt_knockback_packet_ui(mv),
        }
        for col, val in updates.items():
            if col in cols:
                try:
                    self.tree.set(item, col, val)
                except Exception:
                    pass

    def _parse_int_for_fd(self, txt: str) -> int:
        txt = (txt or "").strip()
        if not txt:
            return 0
        return int(txt, 16) if txt.lower().startswith("0x") else int(txt, 10)

    def _edit_kb_type(self, item, mv, current: str):
        cur = mv.get("kb_type")
        current_text = current if current else ("9" if cur is None else str(int(cur) & 0xFF))
        self._edit_single_kb_field(
            item,
            mv,
            title="Edit KB Style",
            label="KB Style",
            column="kb_type",
            mv_key="kb_type",
            writer_key="kb_type",
            current_text=current_text,
            help_text=(
                "Plain decimal 35/xx packet type byte at packet +1. Normal stock hit-physics uses 9, meaning 35/09. "
                "Changing this is raw/full-control behavior and may route the packet through a different 35 handler."
            ),
            suggestions=[("9 normal", 9), ("7 alt", 7)],
            parser=lambda txt: self._parse_int_for_fd(txt) & 0xFF,
            formatter=lambda v: str(int(v) & 0xFF),
        )

    def _edit_single_kb_field(
        self,
        item,
        mv,
        *,
        title: str,
        label: str,
        column: str,
        mv_key: str,
        writer_key: str,
        current_text: str,
        help_text: str,
        suggestions: list[tuple[str, object]],
        parser,
        formatter,
    ):
        if mv.get("knockback_addr") is None:
            messagebox.showerror(title, "KB/launch packet not found for this row.")
            return

        dlg = tk.Toplevel(self.root)
        dlg.title(title)
        configure_light_dialog(dlg, self.root, width=520, height=320)
        outer = build_dialog_shell(
            dlg,
            title_text=label,
            help_text=help_text,
            current_text=f"Current: {current_text}",
            address=mv.get("knockback_addr"),
            wrap=470,
        )

        var = tk.StringVar(value=current_text)
        if suggestions:
            tk.Label(outer, text="Suggested values:", bg="#F3F4F7", fg="#111111", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 4))
            sug_wrap = tk.Frame(outer, bg="#FFFFFF", bd=1, relief="solid")
            sug_wrap.pack(fill="x", pady=(0, 8))
            sug_inner = tk.Frame(sug_wrap, bg="#FFFFFF")
            sug_inner.pack(fill="x", padx=6, pady=6)
            for i, (txt, val) in enumerate(suggestions):
                ttk.Button(sug_inner, text=txt, command=lambda v=val: var.set(formatter(v))).grid(row=0, column=i, padx=4, pady=2, sticky="ew")
                try:
                    sug_inner.grid_columnconfigure(i, weight=1)
                except Exception:
                    pass

        tk.Label(outer, text="Manual entry:", bg="#F3F4F7", fg="#111111", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 2))
        entry_row = tk.Frame(outer, bg="#F3F4F7")
        entry_row.pack(anchor="w", fill="x")
        ent = ttk.Entry(entry_row, textvariable=var, width=18)
        ent.pack(side="left")
        ent.focus_set()
        ent.selection_range(0, "end")

        btns = tk.Frame(outer, bg="#F3F4F7")
        btns.pack(fill="x", pady=(12, 0))

        def on_ok():
            try:
                value = parser(var.get())
            except Exception:
                messagebox.showerror(title, "Invalid value.")
                return

            kwargs = {writer_key: value}
            if U.WRITER_AVAILABLE and U.write_knockback(mv, **kwargs):
                mv[mv_key] = value
                self.tree.set(item, column, formatter(value))
                self._notify_fd_cell_changed(item, mv, column)
                try:
                    self._apply_row_tags(item, mv)
                    self._set_status_for_item(item, mv)
                except Exception:
                    pass
                dlg.destroy()
            else:
                messagebox.showerror(title, "Failed to write value.")

        ttk.Button(btns, text="OK", command=on_ok).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right")
        dlg.bind("<Return>", lambda _e: on_ok())
        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        finalize_dialog_geometry(dlg, 520, 320)

    def _edit_launch_profile(self, item, mv, current: str):
        cur = mv.get("launch_profile")
        current_text = current if current else ("0" if cur is None else str(int(cur) & 0xFFFFFFFF))
        self._edit_single_kb_field(
            item,
            mv,
            title="Edit Extra Launch",
            label="Extra Launch",
            column="launch_profile",
            mv_key="launch_profile",
            writer_key="launch_profile",
            current_text=current_text,
            help_text=(
                "0 means normal knockback. Any value above 0 turns on extra launch behavior "
                "and makes Launch Adjust matter."
            ),
            suggestions=[("0", 0), ("1", 1), ("2", 2), ("3", 3)],
            parser=lambda txt: self._parse_int_for_fd(txt) & 0xFFFFFFFF,
            formatter=lambda v: str(int(v) & 0xFFFFFFFF),
        )

    def _edit_kb_unknown(self, item, mv, current: str):
        cur = mv.get("kb_unknown")
        current_text = current if current else ("0" if cur is None else str(int(cur) & 0xFFFFFFFF))
        self._edit_single_kb_field(
            item,
            mv,
            title="Edit Launch Adjust",
            label="Launch Adjust",
            column="kb_unknown",
            mv_key="kb_unknown",
            writer_key="kb_unknown",
            current_text=current_text,
            help_text=(
                "Mostly ignored when Extra Launch is 0. When Extra Launch is above 0, "
                "this changes launch speed, direction, or how the air KB and hit push/pull packets are interpreted."
            ),
            suggestions=[("0", 0), ("1", 1), ("2", 2), ("3", 3), ("10", 10)],
            parser=lambda txt: self._parse_int_for_fd(txt) & 0xFFFFFFFF,
            formatter=lambda v: str(int(v) & 0xFFFFFFFF),
        )

    def _edit_ground_kb(self, item, mv, current: str):
        """Edit the confirmed signed 35 0C +0x08 hit Push/Pull X scalar."""
        addr = mv.get("ground_kb_addr")
        if addr is None:
            messagebox.showerror("Edit Hit Push/Pull X", "35 0C Hit Push/Pull X packet not found for this row.")
            return
        cur = mv.get("ground_kb")
        new_val = ask_float_with_help(
            self.root,
            title="Edit Hit Push/Pull X",
            prompt="Hit Push/Pull X (35/0C +8):",
            help_text=(
                "35 0C packet, signed float at +0x08. Confirmed hit spacing control: Ryu Tatsu Super uses negative values "
                "to push its first hit away and positive values to pull/vacuum later hits inward. The scanner shows this only "
                "when the packet belongs to the row's own post-hit bundle."
            ),
            initialvalue=float(cur or 0.0),
        )
        if new_val is not None and U.WRITER_AVAILABLE and U.write_ground_knockback(mv, float(new_val)):
            mv["ground_kb"] = float(new_val)
            self.tree.set(item, "ground_kb", U.fmt_ground_kb_ui(mv))
            self._notify_fd_cell_changed(item, mv, "ground_kb")

    def _edit_ground_kb_y(self, item, mv, current: str):
        """Edit the unclassified 35 0C +0x0C Push/Pull Aux scalar."""
        addr = mv.get("ground_kb_y_addr")
        if addr is None:
            messagebox.showerror("Edit Hit Push/Pull Aux", "35 0C Hit Push/Pull Aux packet not found for this row.")
            return
        cur = mv.get("ground_kb_y")
        new_val = ask_float_with_help(
            self.root,
            title="Edit Hit Push/Pull Aux",
            prompt="Hit Push/Pull Aux (35/0C +C):",
            help_text=(
                "35 0C packet, float at +0x0C. This changes with the same local hit-spacing packet as Push/Pull X, but its "
                "semantic role is not confirmed. It is exposed separately for isolated testing without classifying it as a Y vector."
            ),
            initialvalue=float(cur or 0.0),
        )
        if new_val is not None and U.WRITER_AVAILABLE and U.write_ground_knockback_y(mv, float(new_val)):
            mv["ground_kb_y"] = float(new_val)
            self.tree.set(item, "ground_kb_y", U.fmt_ground_kb_y_ui(mv))
            self._notify_fd_cell_changed(item, mv, "ground_kb_y")

    def _edit_kb_x(self, item, mv, current: str):
        cur = mv.get("kb_x")
        current_text = current if current else ("" if cur is None else f"{float(cur):.6g}")
        self._edit_single_kb_field(
            item,
            mv,
            title="Edit Air KB X",
            label="Air KB X",
            column="kb_x",
            mv_key="kb_x",
            writer_key="kb_x",
            current_text=current_text,
            help_text=(
                "35 07/09 airborne knockback X scalar. It controls horizontal carry while the opponent is airborne and is separate from the optional per-hit 35/0C Push/Pull packet."
            ),
            suggestions=[("0", 0.0), ("6", 6.0), ("10", 10.0)],
            parser=lambda txt: float((txt or "0").strip()),
            formatter=lambda v: U._fmt_float_trim(v),
        )

    def _edit_air_kb(self, item, mv, current: str):
        cur = mv.get("air_kb")
        current_text = current if current else ("" if cur is None else f"{float(cur):.6g}")
        self._edit_single_kb_field(
            item,
            mv,
            title="Edit Air KB Y",
            label="Air KB Y",
            column="air_kb",
            mv_key="air_kb",
            writer_key="air_kb",
            current_text=current_text,
            help_text=(
                "35 07/09 vertical airborne displacement / launch component. Ryu 6C validation results: values above default pop the target up and allow descent during normal hitstun; values below default retain the target near hit height longer. This is not the hitstun timer."
            ),
            suggestions=[("0.05", 0.05), ("0.08", 0.08), ("17", 17.0), ("50", 50.0), ("-0.16", -0.16)],
            parser=lambda txt: float((txt or "0").strip()),
            formatter=lambda v: U._fmt_float_trim(v),
        )


    # ----- Hit FX / reach / script-link editors -----

    def _edit_u32_memory_field(self, item, mv, *, title: str, col: str, addr_key: str, value_key: str, help_key: str, default: int = 0, maxvalue: int = 0xFFFFFFFF):
        addr = mv.get(addr_key)
        if addr is None:
            messagebox.showerror(title, "Address not found for this row. Try Refresh visible first.")
            return
        cur = mv.get(value_key)
        try:
            cur_i = int(cur) & 0xFFFFFFFF if cur is not None else int(default)
        except Exception:
            cur_i = int(default)
        new_val = ask_integer_with_help(
            self.root,
            title=title,
            prompt="New value:",
            help_text=get_field_help(help_key),
            initialvalue=cur_i,
            minvalue=0,
            maxvalue=maxvalue,
            address=int(addr),
        )
        if new_val is None:
            return
        if write_u32_field_inline(mv, addr_key, value_key, int(new_val)):
            self.tree.set(item, col, str(int(new_val) & 0xFFFFFFFF))
            self._notify_fd_cell_changed(item, mv, col)
            try:
                self._apply_row_tags(item, mv)
                self._set_status_for_item(item, mv)
            except Exception:
                pass
        else:
            messagebox.showerror(title, "Failed to write value.")

    def _edit_f32_memory_field(self, item, mv, *, title: str, col: str, addr_key: str, value_key: str, help_key: str, default: float = 1.0):
        addr = mv.get(addr_key)
        if addr is None:
            messagebox.showerror(title, "Address not found for this row. Try Refresh visible first.")
            return
        cur = mv.get(value_key)
        try:
            cur_f = float(cur) if cur is not None else float(default)
        except Exception:
            cur_f = float(default)
        new_val = ask_float_with_help(
            self.root,
            title=title,
            prompt="New value:",
            help_text=get_field_help(help_key),
            initialvalue=cur_f,
        )
        if new_val is None:
            return
        if write_f32_field_inline(mv, addr_key, value_key, float(new_val)):
            self.tree.set(item, col, U._fmt_float_trim(float(new_val)))
            self._notify_fd_cell_changed(item, mv, col)
            try:
                self._apply_row_tags(item, mv)
                self._set_status_for_item(item, mv)
            except Exception:
                pass
        else:
            messagebox.showerror(title, "Failed to write value.")

    def _edit_hit_spark(self, item, mv, current: str):
        self._edit_u32_memory_field(
            item, mv,
            title="Edit Hit Spark",
            col="hit_spark",
            addr_key="hit_spark_addr",
            value_key="hit_spark",
            help_key="hit_spark",
            default=0,
            maxvalue=0xFFFFFFFF,
        )

    def _edit_stretch_part(self, item, mv, current: str):
        self._edit_u32_memory_field(
            item, mv,
            title="Edit Stretch Part",
            col="stretch_part",
            addr_key="stretch_part_addr",
            value_key="stretch_part",
            help_key="stretch_part",
            default=0,
            maxvalue=0xFFFFFFFF,
        )

    def _edit_stretch_len(self, item, mv, current: str):
        self._edit_f32_memory_field(
            item, mv,
            title="Edit Reach Length",
            col="stretch_len",
            addr_key="stretch_len_addr",
            value_key="stretch_len",
            help_key="stretch_len",
            default=1.0,
        )

    def _edit_stretch_width(self, item, mv, current: str):
        self._edit_f32_memory_field(
            item, mv,
            title="Edit Reach Width",
            col="stretch_width",
            addr_key="stretch_width_addr",
            value_key="stretch_width",
            help_key="stretch_width",
            default=1.0,
        )

    def _edit_stretch_height(self, item, mv, current: str):
        self._edit_f32_memory_field(
            item, mv,
            title="Edit Reach Height",
            col="stretch_height",
            addr_key="stretch_height_addr",
            value_key="stretch_height",
            help_key="stretch_height",
            default=1.0,
        )

    def _edit_stretch_time(self, item, mv, current: str):
        self._edit_u32_memory_field(
            item, mv,
            title="Edit Stretch Timing",
            col="stretch_time",
            addr_key="stretch_time_addr",
            value_key="stretch_time",
            help_key="stretch_time",
            default=0,
            maxvalue=0xFFFFFFFF,
        )

    def _edit_post_link(self, item, mv, current: str):
        self._edit_u32_memory_field(
            item, mv,
            title="Edit Post-Animation Link",
            col="post_link",
            addr_key="post_link_addr",
            value_key="post_link",
            help_key="post_link",
            default=0,
            maxvalue=0xFFFFFFFF,
        )

    def _edit_knockback(self, item, mv, _current: str):
        'Edit each hit physics field independently.\n\n        This intentionally does not use whole-move presets. Each suggested button only\n        fills one value, so the operator can mix/piece-meal fields without copying a move.\n        '
        # The legacy combined editor can touch several fields at once. Capture
        # their pre-edit values so Reset changed can restore only the fields
        # this dialog actually changed.
        begin = getattr(self, "_begin_edit_snapshot", None)
        if callable(begin):
            for _col in ("kb_type", "launch_profile", "kb_unknown", "kb_x", "air_kb", "hitstun", "blockstun", "hitstop", "hit_spark", "stretch_part", "stretch_len", "stretch_width", "stretch_height", "stretch_time", "post_link"):
                try:
                    begin(item, mv, _col)
                except Exception:
                    pass

        cur_type = mv.get("kb_type")
        cur_profile = mv.get("launch_profile")
        cur_unknown = mv.get("kb_unknown")
        cur_ground = mv.get("ground_kb")
        cur_ground_y = mv.get("ground_kb_y")
        cur_x = mv.get("kb_x")
        cur_air = mv.get("air_kb")
        cur_hs = mv.get("hitstun")
        cur_bs = mv.get("blockstun")
        cur_stop = mv.get("hitstop")
        cur_spark = mv.get("hit_spark")
        cur_stretch_part = mv.get("stretch_part")
        cur_stretch_len = mv.get("stretch_len")
        cur_stretch_width = mv.get("stretch_width")
        cur_stretch_height = mv.get("stretch_height")
        cur_stretch_time = mv.get("stretch_time")
        cur_post_link = mv.get("post_link")

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Hit Physics Fields")
        apply_titlebar_icon(dlg, self.root)
        dlg.geometry("820x860")
        dlg.transient(self.root)

        tk.Label(dlg, text="Hit Physics Fields", font=("Arial", 12, "bold")).pack(pady=5)

        addr = mv.get("knockback_addr")
        ground_kb_addr = mv.get("ground_kb_addr")
        ground_kb_y_addr = mv.get("ground_kb_y_addr")
        stun_addr = mv.get("stun_addr")
        spark_addr = mv.get("hit_spark_addr")
        stretch_addr = mv.get("stretch_packet_addr")
        post_link_addr = mv.get("post_link_addr")
        info_lines = []
        if addr:
            info_lines.append(
                f"Launch data: 0x{addr:08X} | Style +1 | Extra Launch +4 | Launch Adjust +8 | Air KB X +C | Air KB Y +10"
            )
        else:
            info_lines.append("Launch data: not found for this row")
        if ground_kb_addr:
            mode = mv.get("ground_kb_mode")
            mode_text = "?" if mode is None else str(int(mode))
            x_text = f"Hit Push/Pull X: 0x{int(ground_kb_addr):08X} | 35/0C +8 | mode {mode_text}"
            aux_text = f"Push/Pull Aux: 0x{int(ground_kb_y_addr):08X} | 35/0C +C" if ground_kb_y_addr else "Push/Pull Aux: address not found"
            info_lines.append(f"{x_text} | {aux_text}")
            variants = mv.get("push_pull_packets") or []
            if isinstance(variants, list) and len(variants) > 1:
                extra = ", ".join(
                    f"0x{int(v.get('packet_addr') or 0):08X} (mode {v.get('mode')})"
                    for v in variants[1:] if isinstance(v, dict)
                )
                if extra:
                    info_lines.append(f"Additional local 35/0C packet(s): {extra}")
        else:
            info_lines.append("Hit Push/Pull (35/0C): not found for this row")
        if stun_addr:
            info_lines.append(
                f"Stun packet: 0x{stun_addr:08X} | Hitstun +F | Blockstun +1F | Hitstop +26"
            )
        else:
            info_lines.append("Stun packet: not found for this row")
        if spark_addr:
            info_lines.append(f"Hit Spark: 0x{spark_addr:08X} | 35/05 second word")
        if stretch_addr:
            info_lines.append(f"Limb Stretch: 0x{stretch_addr:08X} | Part +4 | Reach Length +8 | Width +C | Height +10 | Timing +14")
        if post_link_addr:
            info_lines.append(f"Post Link: 0x{post_link_addr:08X} | dangerous script continuation")
        tk.Label(dlg, text="\n".join(info_lines), fg="gray", font=("Arial", 9), justify="left").pack(pady=(0, 4))

        body = tk.Frame(dlg)
        body.pack(fill="both", expand=True, padx=12, pady=4)

        def _parse_int(txt: str) -> int:
            txt = (txt or "").strip()
            if not txt:
                return 0
            return int(txt, 16) if txt.lower().startswith("0x") else int(txt, 10)

        def _parse_float(txt: str, fallback):
            txt = (txt or "").strip()
            if not txt:
                return fallback
            return float(txt)

        def _entry_row(parent, row, label, value, help_text, suggestions, parser_kind="text"):
            tk.Label(parent, text=label, width=20, anchor="w").grid(row=row, column=0, sticky="w", pady=4)
            var = tk.StringVar(value=value)
            ent = tk.Entry(parent, textvariable=var, width=16)
            ent.grid(row=row, column=1, sticky="w", pady=4)
            tk.Label(parent, text=help_text, fg="gray", anchor="w", justify="left", wraplength=310).grid(
                row=row, column=2, sticky="w", padx=(8, 0), pady=4
            )
            sug_frame = tk.Frame(parent)
            sug_frame.grid(row=row, column=3, sticky="w", padx=(8, 0), pady=4)
            for idx, (txt, val) in enumerate(suggestions):
                def _fmt(v=val):
                    if parser_kind in ("hex8", "hex32", "int"):
                        return str(int(v))
                    if parser_kind == "float":
                        return f"{float(v):.6g}"
                    return str(v)
                tk.Button(sug_frame, text=txt, width=10, command=lambda v=_fmt: var.set(v())).grid(
                    row=0, column=idx, padx=2
                )
            return var

        kb_box = tk.LabelFrame(body, text="Launch and knockback")
        kb_box.pack(fill="x", pady=(0, 8))

        type_v = _entry_row(
            kb_box,
            0,
            "KB Style",
            str(int(cur_type if cur_type is not None else 9) & 0xFF),
            "Decimal style byte. 9 means normal knockback.",
            [("9", 9), ("7", 7)],
            parser_kind="int",
        )
        prof_v = _entry_row(
            kb_box,
            1,
            "Extra Launch",
            str(int(cur_profile or 0) & 0xFFFFFFFF),
            "0 = normal knockback. Any value above 0 turns on extra launch behavior and makes Launch Adjust matter.",
            [("0", 0), ("1", 1), ("2", 2), ("3", 3)],
            parser_kind="int",
        )
        unk_v = _entry_row(
            kb_box,
            2,
            "Launch Adjust",
            str(int(cur_unknown or 0) & 0xFFFFFFFF),
            "Mostly ignored when Extra Launch is 0. When Extra Launch is above 0, this changes speed/direction/curve.",
            [("0", 0), ("1", 1), ("2", 2), ("3", 3), ("10", 10)],
            parser_kind="int",
        )
        ground_v = _entry_row(
            kb_box,
            3,
            "Hit Push/Pull X",
            "" if cur_ground is None else f"{float(cur_ground):.6g}",
            "Confirmed signed 35/0C +8 hit-spacing control. Ryu Tatsu Super uses negative values to push away and positive values to pull/vacuum inward.",
            [("0", 0.0), ("0.38", 0.38), ("0.7", 0.7), ("1.0", 1.0)],
            parser_kind="float",
        )
        ground_y_v = _entry_row(
            kb_box,
            4,
            "Hit Push/Pull Aux",
            "" if cur_ground_y is None else f"{float(cur_ground_y):.6g}",
            "35/0C +C companion scalar in the same local hit Push/Pull packet. Exposed for testing; semantic role is not yet confirmed.",
            [("0", 0.0), ("0.38", 0.38), ("0.7", 0.7), ("1.0", 1.0)],
            parser_kind="float",
        )
        x_v = _entry_row(
            kb_box,
            5,
            "Air KB X",
            "" if cur_x is None else f"{float(cur_x):.6g}",
            "35/07 or 35/09 airborne knockback X scalar. It controls horizontal carry while the opponent is airborne and is separate from the optional local 35/0C Hit Push/Pull packet.",
            [("0", 0.0), ("6", 6.0), ("10", 10.0)],
            parser_kind="float",
        )
        air_v = _entry_row(
            kb_box,
            6,
            "Air KB Y",
            "" if cur_air is None else f"{float(cur_air):.6g}",
            "35/07 or 35/09 vertical airborne displacement / launch scalar. Higher values pop the target up and let them fall during the normal hitstun; lower values keep the target closer to hit height longer. This is not the hitstun timer.",
            [("0.05", 0.05), ("0.08", 0.08), ("17", 17.0), ("50", 50.0), ("-0.16", -0.16)],
            parser_kind="float",
        )

        stun_box = tk.LabelFrame(body, text="Stun timing")
        stun_box.pack(fill="x", pady=(0, 8))

        hs_v = _entry_row(
            stun_box,
            0,
            "Hitstun",
            "" if cur_hs is None else str(int(cur_hs)),
            "Frames the opponent stays in hit reaction before they can recover/act.",
            [("10", 10), ("17", 17), ("21", 21), ("30", 30)],
            parser_kind="int",
        )
        bs_v = _entry_row(
            stun_box,
            1,
            "Blockstun",
            "" if cur_bs is None else str(int(cur_bs)),
            "Frames the opponent stays locked after blocking. Changes block advantage.",
            [("9", 9), ("10", 10), ("15", 15), ("21", 21)],
            parser_kind="int",
        )
        stop_v = _entry_row(
            stun_box,
            2,
            "Hitstop",
            "" if cur_stop is None else str(int(cur_stop)),
            "Freeze frames on impact. Higher values make hits feel heavier without extending hitstun the same way.",
            [("6", 6), ("8", 8), ("10", 10), ("12", 12)],
            parser_kind="int",
        )

        fx_box = tk.LabelFrame(body, text="Hit FX and reach")
        fx_box.pack(fill="x", pady=(0, 8))

        spark_v = _entry_row(
            fx_box,
            0,
            "Hit Spark",
            "" if cur_spark is None else str(int(cur_spark) & 0xFFFFFFFF),
            "Changes the impact spark/effect. Validated: can also move the spark location.",
            [("0", 0), ("1", 1), ("2", 2), ("3", 3), ("10", 10)],
            parser_kind="int",
        )
        part_v = _entry_row(
            fx_box,
            1,
            "Stretch Part",
            "" if cur_stretch_part is None else str(int(cur_stretch_part) & 0xFFFFFFFF),
            "Which limb/body slot gets the reach stretch. Ryu 5A default observed as 8.",
            [("0", 0), ("1", 1), ("5", 5), ("8", 8), ("10", 10)],
            parser_kind="int",
        )
        reach_v = _entry_row(
            fx_box,
            2,
            "Reach Length",
            "" if cur_stretch_len is None else f"{float(cur_stretch_len):.6g}",
            "Main limb stretch/reach length. Higher values can create Dhalsim-style extended limbs.",
            [("1.0", 1.0), ("1.5", 1.5), ("2.0", 2.0), ("3.0", 3.0)],
            parser_kind="float",
        )
        width_v = _entry_row(
            fx_box,
            3,
            "Reach Width",
            "" if cur_stretch_width is None else f"{float(cur_stretch_width):.6g}",
            "Second stretch axis/component.",
            [("1.0", 1.0), ("1.5", 1.5), ("2.0", 2.0), ("3.0", 3.0)],
            parser_kind="float",
        )
        height_v = _entry_row(
            fx_box,
            4,
            "Reach Height",
            "" if cur_stretch_height is None else f"{float(cur_stretch_height):.6g}",
            "Third stretch axis/component.",
            [("1.0", 1.0), ("1.5", 1.5), ("2.0", 2.0), ("3.0", 3.0)],
            parser_kind="float",
        )
        timing_v = _entry_row(
            fx_box,
            5,
            "Stretch Timing",
            "" if cur_stretch_time is None else str(int(cur_stretch_time) & 0xFFFFFFFF),
            "Timing/slot value for the reach stretch. Ryu 5A default observed as 5.",
            [("0", 0), ("1", 1), ("4", 4), ("5", 5), ("8", 8)],
            parser_kind="int",
        )
        post_v = _entry_row(
            fx_box,
            6,
            "Post Link",
            "" if cur_post_link is None else str(int(cur_post_link) & 0xFFFFFFFF),
            "Dangerous script continuation. Wrong values can freeze the character after animation.",
            [("keep", int(cur_post_link or 0))],
            parser_kind="int",
        )

        tk.Label(
            dlg,
            text=(
                "Each suggestion only fills that one field. Manual decimal, hex, and float values still work. "
                "Hit Reaction is separate. 35/0C is an optional local Hit Push/Pull packet: +8 is confirmed signed horizontal spacing, while +C remains an exposed Aux scalar. Air KB X/Y belong to 35/07 or 35/09 packets."
            ),
            fg="gray",
            wraplength=720,
            justify="left",
        ).pack(anchor="w", padx=12, pady=(2, 0))

        def on_ok():
            try:
                kt = _parse_int(type_v.get()) & 0xFF
                prof = _parse_int(prof_v.get()) & 0xFFFFFFFF
                unk = _parse_int(unk_v.get()) & 0xFFFFFFFF
                ground = _parse_float(ground_v.get(), cur_ground if cur_ground is not None else 0.0)
                ground_y = _parse_float(ground_y_v.get(), cur_ground_y if cur_ground_y is not None else 0.0)
                kx = _parse_float(x_v.get(), cur_x or 0.0)
                air = _parse_float(air_v.get(), cur_air or 0.0)
                hs = _parse_int(hs_v.get()) & 0xFF if hs_v.get().strip() else cur_hs
                bs = _parse_int(bs_v.get()) & 0xFF if bs_v.get().strip() else cur_bs
                stop = _parse_int(stop_v.get()) & 0xFF if stop_v.get().strip() else cur_stop
                spark = _parse_int(spark_v.get()) & 0xFFFFFFFF if spark_v.get().strip() else cur_spark
                stretch_part = _parse_int(part_v.get()) & 0xFFFFFFFF if part_v.get().strip() else cur_stretch_part
                stretch_len = _parse_float(reach_v.get(), cur_stretch_len if cur_stretch_len is not None else 1.0)
                stretch_width = _parse_float(width_v.get(), cur_stretch_width if cur_stretch_width is not None else 1.0)
                stretch_height = _parse_float(height_v.get(), cur_stretch_height if cur_stretch_height is not None else 1.0)
                stretch_time = _parse_int(timing_v.get()) & 0xFFFFFFFF if timing_v.get().strip() else cur_stretch_time
                post_link = _parse_int(post_v.get()) & 0xFFFFFFFF if post_v.get().strip() else cur_post_link
            except Exception:
                messagebox.showerror("Error", "Invalid hit physics value")
                return

            ok = True
            wrote_any = False

            if mv.get("knockback_addr") is not None:
                wrote_any = True
                ok = ok and U.write_knockback(
                    mv,
                    kb_type=kt,
                    launch_profile=prof,
                    kb_x=kx,
                    air_kb=air,
                    kb_unknown=unk,
                )
                if ok:
                    mv["kb_type"] = kt
                    mv["launch_profile"] = prof
                    mv["kb_unknown"] = unk
                    mv["kb_x"] = kx
                    mv["air_kb"] = air
                    mv["kb0"] = prof
                    mv["kb_traj"] = None

            if mv.get("ground_kb_addr") is not None:
                wrote_any = True
                ok = ok and U.write_ground_knockback(mv, ground)
                if ok:
                    mv["ground_kb"] = ground
            if mv.get("ground_kb_y_addr") is not None:
                wrote_any = True
                ok = ok and U.write_ground_knockback_y(mv, ground_y)
                if ok:
                    mv["ground_kb_y"] = ground_y

            if mv.get("stun_addr") is not None:
                wrote_any = True
                if hs is not None:
                    ok = ok and U.write_hitstun(mv, hs)
                    if ok:
                        mv["hitstun"] = hs
                if bs is not None:
                    ok = ok and U.write_blockstun(mv, bs)
                    if ok:
                        mv["blockstun"] = bs
                if stop is not None:
                    ok = ok and U.write_hitstop(mv, stop)
                    if ok:
                        mv["hitstop"] = stop

            if mv.get("hit_spark_addr") is not None and spark is not None:
                wrote_any = True
                ok = ok and write_u32_field_inline(mv, "hit_spark_addr", "hit_spark", spark)

            if mv.get("stretch_packet_addr") is not None:
                wrote_any = True
                if stretch_part is not None:
                    ok = ok and write_u32_field_inline(mv, "stretch_part_addr", "stretch_part", stretch_part)
                ok = ok and write_f32_field_inline(mv, "stretch_len_addr", "stretch_len", stretch_len)
                ok = ok and write_f32_field_inline(mv, "stretch_width_addr", "stretch_width", stretch_width)
                ok = ok and write_f32_field_inline(mv, "stretch_height_addr", "stretch_height", stretch_height)
                if stretch_time is not None:
                    ok = ok and write_u32_field_inline(mv, "stretch_time_addr", "stretch_time", stretch_time)

            if mv.get("post_link_addr") is not None and post_link is not None:
                wrote_any = True
                ok = ok and write_u32_field_inline(mv, "post_link_addr", "post_link", post_link)

            if not wrote_any:
                messagebox.showerror("Error", "No editable KB, stun, FX, reach, or link packet was found for this row.")
                return
            if not ok:
                messagebox.showerror("Error", "At least one hit physics write failed.")
                return

            self._refresh_kb_cells(item, mv)
            self._notify_fd_cell_changed(item, mv, "kb_type")
            self._notify_fd_cell_changed(item, mv, "launch_profile")
            self._notify_fd_cell_changed(item, mv, "kb_unknown")
            self._notify_fd_cell_changed(item, mv, "ground_kb")
            self._notify_fd_cell_changed(item, mv, "ground_kb_y")
            self._notify_fd_cell_changed(item, mv, "kb_x")
            self._notify_fd_cell_changed(item, mv, "air_kb")
            self._notify_fd_cell_changed(item, mv, "hitstun")
            self._notify_fd_cell_changed(item, mv, "blockstun")
            self._notify_fd_cell_changed(item, mv, "hitstop")
            for _col in ("hit_spark", "stretch_part", "stretch_len", "stretch_width", "stretch_height", "stretch_time", "post_link"):
                if _col in self.tree["columns"]:
                    try:
                        if _col == "hit_spark":
                            self.tree.set(item, _col, U.fmt_hit_spark_ui(mv))
                        elif _col == "stretch_part":
                            self.tree.set(item, _col, U.fmt_stretch_part_ui(mv))
                        elif _col == "stretch_len":
                            self.tree.set(item, _col, U.fmt_stretch_len_ui(mv))
                        elif _col == "stretch_width":
                            self.tree.set(item, _col, U.fmt_stretch_width_ui(mv))
                        elif _col == "stretch_height":
                            self.tree.set(item, _col, U.fmt_stretch_height_ui(mv))
                        elif _col == "stretch_time":
                            self.tree.set(item, _col, U.fmt_stretch_time_ui(mv))
                        elif _col == "post_link":
                            self.tree.set(item, _col, U.fmt_post_link_ui(mv))
                    except Exception:
                        pass
                self._notify_fd_cell_changed(item, mv, _col)
            self._apply_row_tags(item, mv)
            dlg.destroy()

        bottom = tk.Frame(dlg)
        bottom.pack(fill="x", pady=10)
        tk.Button(bottom, text="OK", width=10, command=on_ok).pack(side="left", padx=(280, 6))
        tk.Button(bottom, text="Cancel", width=10, command=dlg.destroy).pack(side="left")

    # ----- Hit reaction -----

    def _edit_hit_reaction(self, item, mv, _current: str):
        cur_hr = mv.get("hit_reaction")

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Hit Reaction")
        configure_light_dialog(dlg, self.root, width=540, height=440)
        outer = build_dialog_shell(
            dlg,
            title_text="Hit Reaction Type",
            help_text=get_field_help("hit_reaction"),
            current_text=f"Current: {U.fmt_hit_reaction_ui(cur_hr)}" if cur_hr is not None else "",
            wrap=500,
        )

        tk.Label(outer, text="Common Reactions:", bg="#F3F4F7", fg="#111111", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(8, 4))

        common_vals = [
            0x000000, 0x000001, 0x000002, 0x000003, 0x000004,
            0x000008, 0x000010, 0x000040, 0x000041, 0x000042,
            0x000080, 0x000082, 0x000083, 0x000400, 0x000800,
            0x000848, 0x002010, 0x003010, 0x004200, 0x800080,
            0x800002, 0x800008, 0x800020, 0x800082, 0x001001,
            0x001003,
        ]
        common = [(v, U.HIT_REACTION_MAP.get(v, "Unknown")) for v in common_vals]
        _frame, listbox = make_list_picker(outer, items=[f"0x{val:06X}: {desc}" for val, desc in common], height=10)

        selected_val = tk.IntVar(value=cur_hr or 0)
        tk.Label(outer, text="Manual entry:", bg="#F3F4F7", fg="#111111", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(8, 2))
        entry_row = tk.Frame(outer, bg="#F3F4F7")
        entry_row.pack(anchor="w", fill="x")
        hex_var = tk.StringVar(master=dlg, value=f"0x{cur_hr:06X}" if cur_hr is not None else "0x000000")
        hex_entry = ttk.Entry(entry_row, textvariable=hex_var, width=18)
        hex_entry.pack(side="left")

        def on_select(_evt=None):
            sel = listbox.curselection()
            if not sel:
                return
            val, _desc = common[sel[0]]
            selected_val.set(val)
            hex_var.set(f"0x{val:06X}")

        listbox.bind("<<ListboxSelect>>", on_select)

        def on_ok():
            val = U.parse_hit_reaction_input(hex_var.get())
            if val is None:
                val = int(selected_val.get())

            if write_hit_reaction_inline(mv, val, U.WRITER_AVAILABLE):
                self.tree.set(item, "hit_reaction", U.fmt_hit_reaction(val))
                mv["hit_reaction"] = val
                self._notify_fd_cell_changed(item, mv, "hit_reaction")
            dlg.destroy()

        btns = tk.Frame(outer, bg="#F3F4F7")
        btns.pack(fill="x", pady=(12, 0))
        ttk.Button(btns, text="OK", command=on_ok).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right")
        finalize_dialog_geometry(dlg, 440, 260)

    # ----- Hitbox -----

    def _edit_hitbox_main(self, item, mv, _current: str):
        cur_r = mv.get("hb_r")
        if cur_r is None:
            cands = mv.get("hb_candidates") or []
            if cands:
                cur_r = cands[0][1]
                mv["hb_off"] = cands[0][0]
        if cur_r is None:
            cur_r = 0.0

        new_val = ask_float_with_help(
            self.root,
            title="Edit Hitbox",
            prompt="New radius:",
            help_text=get_field_help("hb_main"),
            initialvalue=float(cur_r),
            minvalue=0.0,
        )
        if new_val is None:
            return

        if mv.get("hb_off") is None:
            mv["hb_off"] = U.FALLBACK_HB_OFFSET

        if U.WRITER_AVAILABLE:
            U.write_hitbox_radius(mv, float(new_val))

        mv["hb_r"] = float(new_val)

        cands = mv.get("hb_candidates") or []
        if cands:
            off0 = mv["hb_off"]
            new_cands = []
            replaced = False
            for off, val in cands:
                if off == off0 and not replaced:
                    new_cands.append((off, float(new_val)))
                    replaced = True
                else:
                    new_cands.append((off, val))
            mv["hb_candidates"] = new_cands
        else:
            mv["hb_candidates"] = [(mv["hb_off"], float(new_val))]

        self.tree.set(item, "hb_main", f"{float(new_val):.1f}")
        self.tree.set(item, "hb", U.format_candidate_list(mv["hb_candidates"]))
        self._notify_fd_cell_changed(item, mv, "hb")

    def _edit_hitbox(self, item, mv, _current: str):
        cands = mv.get("hb_candidates") or []
        if not cands:
            return self._edit_hitbox_main(item, mv, "")

        if len(cands) <= 6:
            return self._edit_hitbox_simple(item, mv, cands)
        return self._edit_hitbox_scrollable(item, mv, cands)

    def _edit_hitbox_simple(self, item, mv, cands):
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Hitbox Values")
        apply_titlebar_icon(dlg, self.root)
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(
            dlg,
            text="Edit each radius below. r0 is usually the main one. " + get_field_help("hb"),
            wraplength=360,
            justify="left",
        ).grid(row=0, column=0, columnspan=3, padx=6, pady=4, sticky="w")

        entries = []
        row = 1
        for idx, (off, val) in enumerate(cands):
            tk.Label(dlg, text=f"r{idx}:").grid(row=row, column=0, padx=6, pady=2, sticky="e")
            e = tk.Entry(dlg, width=10)
            e.insert(0, f"{val:.1f}")
            e.grid(row=row, column=1, padx=4, pady=2, sticky="w")
            tk.Label(dlg, text=f"off=0x{off:04X}").grid(row=row, column=2, padx=4, pady=2, sticky="w")
            entries.append((idx, off, e))
            row += 1

        def on_ok():
            new_cands = []
            for idx2, off2, entry in entries:
                txt = (entry.get() or "").strip()
                try:
                    fval = float(txt)
                except Exception:
                    fval = cands[idx2][1]

                mv["hb_off"] = off2
                if U.WRITER_AVAILABLE:
                    U.write_hitbox_radius(mv, float(fval))
                new_cands.append((off2, float(fval)))

            mv["hb_candidates"] = new_cands
            sel_off, sel_val = U.select_primary_hitbox(new_cands)
            mv["hb_off"] = sel_off
            mv["hb_r"] = sel_val

            self.tree.set(item, "hb_main", f"{sel_val:.1f}" if sel_val is not None else "")
            self.tree.set(item, "hb", U.format_candidate_list(new_cands))
            self._notify_fd_cell_changed(item, mv, "hb")
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).grid(row=row, column=0, columnspan=3, pady=6)

    def _edit_hitbox_scrollable(self, item, mv, cands):
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Hitbox Values")
        apply_titlebar_icon(dlg, self.root)
        dlg.transient(self.root)
        dlg.grab_set()
        dlg.geometry("400x500")

        canvas = tk.Canvas(dlg)
        vsb = tk.Scrollbar(dlg, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas)

        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        tk.Label(
            inner,
            text="Edit each radius below. r0 is usually the main one. " + get_field_help("hb"),
            anchor="w",
            justify="left",
            wraplength=360,
        ).grid(row=0, column=0, columnspan=3, padx=6, pady=4, sticky="w")

        entries = []
        row = 1
        for idx, (off, val) in enumerate(cands):
            tk.Label(inner, text=f"r{idx}:").grid(row=row, column=0, padx=6, pady=2, sticky="e")
            e = tk.Entry(inner, width=10)
            e.insert(0, f"{val:.1f}")
            e.grid(row=row, column=1, padx=4, pady=2, sticky="w")
            tk.Label(inner, text=f"off=0x{off:04X}").grid(row=row, column=2, padx=4, pady=2, sticky="w")
            entries.append((idx, off, e))
            row += 1

        def on_ok():
            new_cands = []
            for idx2, off2, entry in entries:
                txt = (entry.get() or "").strip()
                try:
                    fval = float(txt)
                except Exception:
                    fval = cands[idx2][1]

                mv["hb_off"] = off2
                if U.WRITER_AVAILABLE:
                    U.write_hitbox_radius(mv, float(fval))
                new_cands.append((off2, float(fval)))

            mv["hb_candidates"] = new_cands
            sel_off, sel_val = U.select_primary_hitbox(new_cands)
            mv["hb_off"] = sel_off
            mv["hb_r"] = sel_val

            self.tree.set(item, "hb_main", f"{sel_val:.1f}" if sel_val is not None else "")
            self.tree.set(item, "hb", U.format_candidate_list(new_cands))
            dlg.destroy()

        tk.Button(inner, text="OK", command=on_ok).grid(row=row, column=0, columnspan=3, pady=8)

    # ----- Move replacement menu -----

    def _edit_move_replacement(self, item, mv):
        if not U.WRITER_AVAILABLE:
            messagebox.showerror("Error", "Writer unavailable")
            return

        # ReplaceMoveDialog has had multiple signatures across revisions.
        # Try the newest first, then gracefully fall back.
        try:
            dlg = ReplaceMoveDialog(self.root, self.moves, mv)   # (parent, all_moves, current_mv)
        except TypeError:
            try:
                dlg = ReplaceMoveDialog(self.root, self.moves)   # (parent, all_moves)
            except TypeError:
                dlg = ReplaceMoveDialog(self.root, mv)           # (parent, current_mv) fallback

        # Some dialogs are true Toplevels, others might destroy themselves during __init__.
        try:
            if hasattr(dlg, "winfo_exists") and dlg.winfo_exists():
                self.root.wait_window(dlg)
        except tk.TclError:
            pass

        res = getattr(dlg, "result", None)
        if not res:
            return

        # Normalize result into (new_mv, mode)
        mode = "anim"
        new_mv = None

        # Expected modern format: (mv_dict, "anim"|"block")
        if isinstance(res, tuple) and len(res) == 2 and isinstance(res[1], str):
            new_mv, mode = res[0], res[1]
        else:
            # Older formats: just the mv dict, or (mv_dict, something-non-string)
            if isinstance(res, tuple) and len(res) >= 1:
                new_mv = res[0]
            else:
                new_mv = res

        if not isinstance(new_mv, dict):
            messagebox.showerror("Error", "Replace dialog returned an unexpected result type.")
            return

        new_id = new_mv.get("id")
        if new_id is None:
            messagebox.showerror("Error", "Selected move has no ID")
            return

        ok = False
        if mode == "anim":
            ok = self._write_anim_id(mv, int(new_id))
        else:
            # Full-block swap implementation.
            ok = self._clone_move_block_y2(new_mv, mv)

        if not ok:
            messagebox.showerror("Error", "Failed to write replacement to Dolphin.\nCheck console for details.")
            return

        mv["animation_id"] = int(new_id) & 0xFFFF
        mv["animation_label"] = new_mv.get("move_name") or U.pretty_move_name(int(new_id), self.target_slot.get("char_name", "-"))
        self._notify_fd_cell_changed(item, mv, "move")

    # ----- Anim ID write/read helpers (deduped: only one _edit_anim_manual) -----

    def _animation_context(self, mv):
        slot = self.target_slot if isinstance(getattr(self, "target_slot", None), dict) else {}
        return (
            slot.get("char_name") or (mv or {}).get("char_name") or (mv or {}).get("animation_char_key"),
            slot.get("char_id") or (mv or {}).get("char_id"),
        )

    def _resolve_anim_binding(self, mv):
        try:
            from dolphin_io import rbytes
            from mot_runtime import get_current_animation_id, locate_loaded_mot
        except ImportError:
            return None, None, "none"
        char_name, char_id = self._animation_context(mv)
        current, reason = get_current_animation_id(
            mv, char_name=char_name, char_id=char_id, rbytes=rbytes,
        )
        if current is None:
            return None, None, reason
        loaded, _source = locate_loaded_mot(
            char_name, char_id, mv, rbytes=rbytes,
        )
        if loaded is None:
            return None, None, "none"
        try:
            source_action = int(mv.get("id")) & 0xFFFF
        except Exception:
            return None, None, "none"
        return loaded.table_addr + source_action * 4, int(current), "mot_table"

    def _read_anim_id_hi_lo(self, mv):
        _addr, current, _kind = self._resolve_anim_binding(mv)
        if current is None:
            return (None, None)
        return ((int(current) >> 8) & 0xFF, int(current) & 0xFF)

    def _write_anim_id_manual(self, mv, hi: int, lo: int) -> bool:
        return self._write_anim_id(mv, ((int(hi) & 0xFF) << 8) | (int(lo) & 0xFF))

    def _write_anim_id(self, mv, new_anim_id: int) -> bool:
        if not U.WRITER_AVAILABLE:
            return False
        try:
            from dolphin_io import rbytes, wd32
            from mot_runtime import write_animation_only
        except ImportError:
            return False
        char_name, char_id = self._animation_context(mv)
        ok, info = write_animation_only(
            mv,
            int(new_anim_id),
            char_name=char_name,
            char_id=char_id,
            rbytes=rbytes,
            wd32=wd32,
        )
        if ok:
            print(
                f"_write_anim_id: MOT slot @0x{int(info['table_slot']):08X} "
                f"action 0x{int(info['source_action']):04X} -> clip 0x{int(info['target_action']):04X}"
            )
            return True
        print(f"_write_anim_id: animation-only MOT write failed: {info.get('reason', 'unknown')}")
        return False

    def _edit_anim_manual(self, item, mv):
        if not U.WRITER_AVAILABLE:
            messagebox.showerror("Error", "Writer unavailable")
            return

        cur_hi, cur_lo = self._read_anim_id_hi_lo(mv)

        # If ManualAnimIDDialog subclasses simpledialog.Dialog, it likely blocks
        # (and may destroy itself) during __init__. So the module must not blindly wait again.
        dlg = ManualAnimIDDialog(self.root, cur_hi=cur_hi, cur_lo=cur_lo)

        # Only wait if the widget still exists (covers both Dialog-style and Toplevel-style impls).
        try:
            if hasattr(dlg, "winfo_exists") and dlg.winfo_exists():
                self.root.wait_window(dlg)
        except tk.TclError:
            # Dialog was already destroyed/closed during construction.
            pass

        if not getattr(dlg, "result", None):
            return

        hi, lo = dlg.result
        ok = self._write_anim_id_manual(mv, int(hi), int(lo))
        if not ok:
            messagebox.showerror("Error", "Failed to write anim bytes")
            return

        new_id = ((hi & 0xFF) << 8) | (lo & 0xFF)
        mv["animation_id"] = new_id
        mv["animation_label"] = U.pretty_move_name(new_id, self.target_slot.get("char_name", "-"))
        self._notify_fd_cell_changed(item, mv, "move")
    def _show_move_edit_menu(self, event, item, mv):
        if not U.WRITER_AVAILABLE:
            messagebox.showerror("Error", "Writer unavailable")
            return

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Replace with known move...", command=lambda: self._edit_move_replacement(item, mv))
        menu.add_command(label="Manual Anim ID (HI / LO)...", command=lambda: self._edit_anim_manual(item, mv))

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ----- SuperBG toggle -----

    def _ensure_superbg(self, mv) -> None:
        if mv.get("superbg_addr") is not None:
            return
        move_abs = mv.get("abs")
        if not move_abs:
            return
        try:
            from dolphin_io import rbytes, rd8
            saddr, sval = find_superbg_addr(move_abs, rbytes, rd8)
        except Exception:
            saddr, sval = (None, None)
        if saddr:
            mv["superbg_addr"] = saddr
            mv["superbg_val"] = sval

    def _toggle_superbg(self, item, mv):
        self._ensure_superbg(mv)
        if not mv.get("superbg_addr"):
            messagebox.showerror("Error", "SuperBG pattern not found for this move")
            return

        cur = mv.get("superbg_val")
        is_on = (cur == SUPERBG_ON)
        new_on = not is_on

        if write_superbg_inline(mv, new_on, U.WRITER_AVAILABLE):
            self.tree.set(item, "superbg", U.fmt_superbg(mv.get("superbg_val")))
            self._notify_fd_cell_changed(item, mv, "superbg")
        else:
            messagebox.showerror("Error", "Failed to write SuperBG")
