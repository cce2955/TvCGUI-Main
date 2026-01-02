# fd_editors.py
#
# All "edit X" handlers live here as a mixin to keep fd_window small.

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox

from fd_dialogs import ReplaceMoveDialog

from fd_patterns import (
    find_combo_kb_mod_addr,
    find_superbg_addr,
    SUPERBG_ON,
)

from fd_write_helpers import (
    write_hit_reaction_inline,
    write_active2_frames_inline,
    write_anim_id_inline,          # if you still use this elsewhere
    write_combo_kb_mod_inline,
    write_superbg_inline,
)

import fd_utils as U
from fd_widgets import ManualAnimIDDialog


class FDCellEditorsMixin:
    """
    Requires the host class to define:
      - self.root, self.tree
      - self.moves, self.target_slot
      - self._apply_row_tags(item, mv), self._set_status_for_item(item, mv)
      - self._show_move_edit_menu(event, item, mv) (or use provided)
      - self._clone_move_block_y2(new_mv, mv) if you use full replace mode
    """

    # ----- Active2 (inline) -----

    def _edit_active2(self, item, mv, current: str):
        cur_s, cur_e = U.ensure_range_pair(
            current,
            default_s=mv.get("active2_start", 1) or 1,
            default_e=mv.get("active2_end", mv.get("active2_start", 1) or 1) or 1,
        )

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Active 2 Frames")
        dlg.geometry("320x180")

        tk.Label(dlg, text="Active 2 Start Frame:", font=("Arial", 10)).pack(pady=3)
        sv = tk.IntVar(value=cur_s)
        tk.Entry(dlg, textvariable=sv, font=("Arial", 10)).pack()

        tk.Label(dlg, text="Active 2 End Frame:", font=("Arial", 10)).pack(pady=3)
        ev = tk.IntVar(value=cur_e)
        tk.Entry(dlg, textvariable=ev, font=("Arial", 10)).pack()

        addr = mv.get("active2_addr")
        if addr:
            tk.Label(dlg, text=f"Address: 0x{addr:08X}", fg="gray", font=("Arial", 9)).pack(pady=5)
        else:
            tk.Label(dlg, text="No address found", fg="red", font=("Arial", 9)).pack(pady=5)

        def on_ok():
            s = int(sv.get())
            e = int(ev.get())
            if e < s:
                e = s
            if write_active2_frames_inline(mv, s, e, U.WRITER_AVAILABLE):
                self.tree.set(item, "active2", f"{s}-{e}")
                mv["active2_start"] = s
                mv["active2_end"] = e
            else:
                messagebox.showerror("Error", "Failed to write Active 2 frames")
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok, font=("Arial", 10)).pack(pady=10)

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

        new_val = simpledialog.askinteger(
            "Edit Combo KB Mod",
            f"New combo KB mod byte (0-255)\nAddr: 0x{addr:08X}",
            initialvalue=int(cur_val),
            minvalue=0,
            maxvalue=255,
        )
        if new_val is None:
            return

        if write_combo_kb_mod_inline(mv, int(new_val), U.WRITER_AVAILABLE):
            mv["combo_kb_mod"] = int(new_val)
            self.tree.set(item, "combo_kb_mod", f"{new_val} (0x{new_val:02X})")
        else:
            messagebox.showerror("Combo KB Mod", "Failed to write Combo KB Mod byte.")

    # ----- Simple scalar editors (damage/meter/hitstop) -----

    def _edit_damage(self, item, mv, current: str):
        cur = U.ensure_int(current, 0)
        new_val = simpledialog.askinteger("Edit Damage", "New damage:", initialvalue=cur, minvalue=0, maxvalue=999999)
        if new_val is not None and U.WRITER_AVAILABLE and U.write_damage(mv, new_val):
            self.tree.set(item, "damage", str(new_val))
            mv["damage"] = new_val

    def _edit_meter(self, item, mv, current: str):
        cur = U.ensure_int(current, 0)
        new_val = simpledialog.askinteger("Edit Meter", "New meter:", initialvalue=cur, minvalue=0, maxvalue=255)
        if new_val is not None and U.WRITER_AVAILABLE and U.write_meter(mv, new_val):
            self.tree.set(item, "meter", str(new_val))
            mv["meter"] = new_val

    def _edit_hitstop(self, item, mv, current: str):
        cur = U.ensure_int(current, 0)
        new_val = simpledialog.askinteger("Edit Hitstop", "New hitstop:", initialvalue=cur, minvalue=0, maxvalue=255)
        if new_val is not None and U.WRITER_AVAILABLE and U.write_hitstop(mv, new_val):
            self.tree.set(item, "hitstop", str(new_val))
            mv["hitstop"] = new_val

    # ----- Active frames (startup/active) -----

    def _edit_startup(self, item, mv, current: str):
        cur = U.ensure_int(current, 1) or 1
        new_val = simpledialog.askinteger("Edit Startup", "New startup frame:", initialvalue=cur, minvalue=1, maxvalue=255)
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

    def _edit_active(self, item, mv, current: str):
        cur_s, cur_e = U.ensure_range_pair(
            current,
            default_s=mv.get("active_start", 1) or 1,
            default_e=mv.get("active_end", mv.get("active_start", 1) or 1) or 1,
        )

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Active Frames")
        dlg.geometry("260x150")

        tk.Label(dlg, text="Active Start:").pack(pady=3)
        sv = tk.IntVar(value=cur_s)
        tk.Entry(dlg, textvariable=sv).pack()

        tk.Label(dlg, text="Active End:").pack(pady=3)
        ev = tk.IntVar(value=cur_e)
        tk.Entry(dlg, textvariable=ev).pack()

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
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).pack(pady=8)

    # ----- Stun fields -----

    def _edit_hitstun(self, item, mv, current: str):
        cur = U.unfmt_stun(current) if current else 0
        new_val = simpledialog.askinteger("Edit Hitstun", "New hitstun:", initialvalue=cur, minvalue=0, maxvalue=255)
        if new_val is not None and U.WRITER_AVAILABLE and U.write_hitstun(mv, new_val):
            self.tree.set(item, "hitstun", U.fmt_stun(new_val))
            mv["hitstun"] = new_val

    def _edit_blockstun(self, item, mv, current: str):
        cur = U.unfmt_stun(current) if current else 0
        new_val = simpledialog.askinteger("Edit Blockstun", "New blockstun:", initialvalue=cur, minvalue=0, maxvalue=255)
        if new_val is not None and U.WRITER_AVAILABLE and U.write_blockstun(mv, new_val):
            self.tree.set(item, "blockstun", U.fmt_stun(new_val))
            mv["blockstun"] = new_val

    # ----- Knockback -----

    def _edit_knockback(self, item, mv, _current: str):
        cur_k0 = mv.get("kb0", 0) or 0
        cur_k1 = mv.get("kb1", 0) or 0
        cur_t = mv.get("kb_traj", 0) or 0

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Knockback")
        dlg.geometry("400x300")

        tk.Label(dlg, text="Knockback Editor", font=("Arial", 12, "bold")).pack(pady=5)

        tk.Label(dlg, text="Knockback 0 (Vertical Distance):", justify="left").pack(anchor="w", padx=10)
        k0v = tk.IntVar(value=cur_k0)
        tk.Entry(dlg, textvariable=k0v, width=10).pack(anchor="w", padx=10)

        tk.Label(dlg, text="Knockback 1 (Horizontal Distance):", justify="left").pack(anchor="w", padx=10, pady=(10, 0))
        k1v = tk.IntVar(value=cur_k1)
        tk.Entry(dlg, textvariable=k1v, width=10).pack(anchor="w", padx=10)

        tk.Label(dlg, text="Trajectory (Angle):", justify="left").pack(anchor="w", padx=10, pady=(10, 0))
        tk.Label(
            dlg,
            text="Common: 0xBD=Up Forward, 0xBE=Down Forward, 0xBC=Up, 0xC4=Pop",
            font=("Arial", 9),
            fg="gray",
            justify="left",
        ).pack(anchor="w", padx=10)
        tv = tk.StringVar(value=f"0x{cur_t:02X}")
        tk.Entry(dlg, textvariable=tv, width=10).pack(anchor="w", padx=10)

        def on_ok():
            try:
                k0 = int(k0v.get())
                k1 = int(k1v.get())
                t_str = (tv.get() or "").strip()
                t = int(t_str, 16) if t_str.lower().startswith("0x") else int(t_str, 16)
            except Exception:
                messagebox.showerror("Error", "Invalid knockback values")
                return

            if U.WRITER_AVAILABLE and U.write_knockback(mv, k0, k1, t):
                self.tree.set(item, "kb", f"K0:{k0} K1:{k1} {U.fmt_kb_traj_ui(t)}")
                mv["kb0"] = k0
                mv["kb1"] = k1
                mv["kb_traj"] = t
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).pack(pady=10)

    # ----- Hit reaction -----

    def _edit_hit_reaction(self, item, mv, _current: str):
        cur_hr = mv.get("hit_reaction")

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Hit Reaction")
        dlg.geometry("520x420")

        tk.Label(dlg, text="Hit Reaction Type", font=("Arial", 12, "bold")).pack(pady=5)

        if cur_hr is not None:
            tk.Label(dlg, text=f"Current: {U.fmt_hit_reaction_ui(cur_hr)}", fg="blue", font=("Arial", 10)).pack(pady=3)

        tk.Label(dlg, text="Common Reactions:", font=("Arial", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 5))

        frame = tk.Frame(dlg)
        frame.pack(fill="both", expand=True, padx=10, pady=5)

        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")

        listbox = tk.Listbox(frame, yscrollcommand=scrollbar.set)
        scrollbar.config(command=listbox.yview)

        common_vals = [
            0x000000, 0x000001, 0x000002, 0x000003, 0x000004,
            0x000008, 0x000010, 0x000040, 0x000041, 0x000042,
            0x000080, 0x000082, 0x000083, 0x000400, 0x000800,
            0x000848, 0x002010, 0x003010, 0x004200, 0x800080,
            0x800002, 0x800008, 0x800020, 0x800082, 0x001001,
            0x001003,
        ]
        common = [(v, U.HIT_REACTION_MAP.get(v, "Unknown")) for v in common_vals]

        for val, desc in common:
            listbox.insert("end", f"0x{val:06X}: {desc}")

        listbox.pack(fill="both", expand=True)

        selected_val = tk.IntVar(value=cur_hr or 0)

        tk.Label(dlg, text="Or enter hex/decimal value:", font=("Arial", 10)).pack(anchor="w", padx=10, pady=(10, 0))
        hex_entry = tk.Entry(dlg, width=20)
        hex_entry.insert(0, f"0x{cur_hr:06X}" if cur_hr is not None else "0x000000")
        hex_entry.pack(anchor="w", padx=10)

        def on_select(_evt):
            sel = listbox.curselection()
            if not sel:
                return
            val, _desc = common[sel[0]]
            selected_val.set(val)
            hex_entry.delete(0, tk.END)
            hex_entry.insert(0, f"0x{val:06X}")

        listbox.bind("<<ListboxSelect>>", on_select)

        def on_ok():
            val = U.parse_hit_reaction_input(hex_entry.get())
            if val is None:
                val = int(selected_val.get())

            if write_hit_reaction_inline(mv, val, U.WRITER_AVAILABLE):
                self.tree.set(item, "hit_reaction", U.fmt_hit_reaction(val))
                mv["hit_reaction"] = val
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).pack(pady=10)

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

        new_val = simpledialog.askfloat("Edit Hitbox", "New radius:", initialvalue=float(cur_r), minvalue=0.0)
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
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="Edit each radius below. r0 is usually the main one.").grid(
            row=0, column=0, columnspan=3, padx=6, pady=4, sticky="w"
        )

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
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).grid(row=row, column=0, columnspan=3, pady=6)

    def _edit_hitbox_scrollable(self, item, mv, cands):
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Hitbox Values")
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

        tk.Label(inner, text="Edit each radius below. r0 is usually the main one.", anchor="w", justify="left").grid(
            row=0, column=0, columnspan=3, padx=6, pady=4, sticky="w"
        )

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

        dlg = ReplaceMoveDialog(self.root, self.moves, mv)
        self.root.wait_window(dlg)
        if not dlg.result:
            return

        new_mv, mode = dlg.result
        new_id = new_mv.get("id")
        if new_id is None:
            messagebox.showerror("Error", "Selected move has no ID")
            return

        ok = False
        if mode == "anim":
            ok = self._write_anim_id(mv, new_id)
        else:
            # This is your existing full-block swap implementation.
            ok = self._clone_move_block_y2(new_mv, mv)

        if not ok:
            messagebox.showerror("Error", "Failed to write replacement to Dolphin.\nCheck console for details.")
            return

        mv["id"] = new_id
        mv["move_name"] = new_mv.get("move_name") or mv.get("move_name")

        cname = self.target_slot.get("char_name", "-")
        pretty = U.pretty_move_name(new_id, cname)
        dup_idx = mv.get("dup_index")
        if dup_idx is not None:
            pretty = f"{pretty} (Tier{dup_idx + 1})"
        pretty = f"{pretty} [0x{new_id:04X}]"
        self.tree.set(item, "move", pretty)

    # ----- Anim ID write/read helpers (deduped: only one _edit_anim_manual) -----

    def _read_anim_id_hi_lo(self, mv):
        base = mv.get("abs")
        if not base:
            return (None, None)
        try:
            from dolphin_io import rbytes
        except ImportError:
            return (None, None)

        LOOKAHEAD = 0x80
        try:
            buf = rbytes(base, LOOKAHEAD)
        except Exception:
            return (None, None)

        for i in range(0, len(buf) - 4):
            if buf[i + 2] == 0x01 and buf[i + 3] == 0x3C:
                return (buf[i], buf[i + 1])
        return (None, None)

    def _write_anim_id_manual(self, mv, hi: int, lo: int) -> bool:
        if not U.WRITER_AVAILABLE:
            return False
        base = mv.get("abs")
        if not base:
            return False
        try:
            from dolphin_io import rbytes, wd8
        except ImportError:
            return False

        LOOKAHEAD = 0x80
        try:
            buf = rbytes(base, LOOKAHEAD)
        except Exception as e:
            print(f"_write_anim_id_manual read failed @0x{base:08X}: {e}")
            return False

        target_off = None
        for i in range(0, len(buf) - 4):
            if buf[i + 2] == 0x01 and buf[i + 3] == 0x3C:
                target_off = i
                break
        if target_off is None:
            print("_write_anim_id_manual: pattern ?? ?? 01 3C not found")
            return False

        addr = base + target_off
        try:
            ok = bool(wd8(addr, hi) and wd8(addr + 1, lo))
            return ok
        except Exception as e:
            print(f"_write_anim_id_manual write failed: {e}")
            return False

    def _write_anim_id(self, mv, new_anim_id: int) -> bool:
        if not U.WRITER_AVAILABLE:
            return False
        base = mv.get("abs")
        if not base:
            return False
        try:
            from dolphin_io import rbytes, wd8
        except ImportError:
            return False

        LOOKAHEAD = 0x80
        try:
            buf = rbytes(base, LOOKAHEAD)
        except Exception as e:
            print(f"_write_anim_id read failed @0x{base:08X}: {e}")
            return False

        target_off = None
        for i in range(0, len(buf) - 4):
            b0, b1, b2, b3 = buf[i], buf[i + 1], buf[i + 2], buf[i + 3]
            if b0 == 0x01 and b3 == 0x3C and b2 == 0x01:
                target_off = i
                break
        if target_off is None:
            print(f"_write_anim_id: pattern 01 ?? 01 3C not found for move @0x{base:08X}")
            return False

        addr = base + target_off
        new_hi = (new_anim_id >> 8) & 0xFF
        new_lo = new_anim_id & 0xFF
        try:
            return bool(wd8(addr, new_hi) and wd8(addr + 1, new_lo))
        except Exception as e:
            print(f"_write_anim_id write failed: {e}")
            return False

    def _edit_anim_manual(self, item, mv):
        if not U.WRITER_AVAILABLE:
            messagebox.showerror("Error", "Writer unavailable")
            return

        cur_hi, cur_lo = self._read_anim_id_hi_lo(mv)
        dlg = ManualAnimIDDialog(self.root, cur_hi=cur_hi, cur_lo=cur_lo)
        self.root.wait_window(dlg)
        if not dlg.result:
            return

        hi, lo = dlg.result
        ok = self._write_anim_id_manual(mv, int(hi), int(lo))
        if not ok:
            messagebox.showerror("Error", "Failed to write anim bytes")
            return

        new_id = ((hi & 0xFF) << 8) | (lo & 0xFF)
        mv["id"] = new_id

        cname = self.target_slot.get("char_name", "-")
        pretty = U.pretty_move_name(new_id, cname)
        dup_idx = mv.get("dup_index")
        if dup_idx is not None:
            pretty = f"{pretty} (Tier{dup_idx + 1})"
        pretty = f"{pretty} [0x{new_id:04X}]"
        self.tree.set(item, "move", pretty)

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
        else:
            messagebox.showerror("Error", "Failed to write SuperBG")
