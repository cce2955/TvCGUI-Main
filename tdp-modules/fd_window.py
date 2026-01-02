# fd_window.py
#
# Main Tk window logic for the frame data editor, modularized.
# This file is now mostly: state, event routing, filter/refresh/reset, right-click tools.

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox

import fd_utils as U
import fd_tree
from fd_editors import FDCellEditorsMixin

from fd_patterns import find_combo_kb_mod_addr, find_superbg_addr, SUPERBG_ON
from fd_write_helpers import (
    write_hit_reaction_inline,
    write_active2_frames_inline,
    write_combo_kb_mod_inline,
    write_superbg_inline,
)

from tk_host import tk_call


class EditableFrameDataWindow(FDCellEditorsMixin):
    def __init__(self, master, slot_label, target_slot):
        self.master = master
        self.slot_label = slot_label
        self.target_slot = target_slot

        def _mv_sort_key(m):
            aid = m.get("id")
            if aid is None:
                group = 2
                aid_val = 0xFFFF
            else:
                aid_val = aid
                group = 0 if aid >= 0x100 else 1
            return (group, aid_val, m.get("abs", 0xFFFFFFFF))

        moves_sorted = sorted(target_slot.get("moves", []), key=_mv_sort_key)

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

        self._filter_var = None
        self._status_var = None
        self._writer_var = None

        self.root: tk.Toplevel | None = None
        self.tree: ttk.Treeview | None = None

        self._build()

    # ---------- UI build ----------

    def _build(self):
        cname = self.target_slot.get("char_name", "-")

        self.root = tk.Toplevel(self.master)
        self.root.title(f"Frame Data Editor: {self.slot_label} ({cname})")
        self.root.geometry("1620x820")
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
        fd_tree.build_tree_widget(self)
        fd_tree.populate_tree(self)

        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        status = ttk.Frame(self.root, style="Status.TFrame")
        status.pack(side="bottom", fill="x")
        ttk.Label(status, textvariable=self._status_var, style="Status.TLabel").pack(side="left", padx=8, pady=4)

    # ---------- Row tagging / status ----------

    def _apply_row_tags(self, item_id: str, mv: dict):
        tags = set(self.tree.item(item_id, "tags") or ())

        kb_txt = self.tree.set(item_id, "kb")
        if kb_txt.strip():
            tags.add("kb_hot")

        combo_txt = self.tree.set(item_id, "combo_kb_mod")
        if combo_txt.strip() and combo_txt.strip() != "?":
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

    def _clear_filter(self):
        self._filter_var.set("")
        self._apply_filter()

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

    def _apply_filter(self):
        q = (self._filter_var.get() or "").strip().lower()

        self._reattach_all()

        if not q:
            self._status_var.set("Filter cleared")
            return

        keep: set[str] = set()

        for item_id in self._all_item_ids:
            text_move = (self.tree.set(item_id, "move") or "").lower()
            text_kind = (self.tree.set(item_id, "kind") or "").lower()
            text_abs = (self.tree.set(item_id, "abs") or "").lower()
            hay = " ".join([text_move, text_kind, text_abs])
            if q in hay:
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

        self._status_var.set(f"Filter applied: '{q}' (hidden {detached})")

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

            # Damage
            if orig["damage"] is not None:
                if U.write_damage(mv, orig["damage"]):
                    self.tree.set(item_id, "damage", str(orig["damage"]))
                    mv["damage"] = orig["damage"]
                    reset_count += 1
                else:
                    failed_writes.append(f"damage @ 0x{abs_addr:08X}")

            # Meter
            if orig["meter"] is not None:
                if U.write_meter(mv, orig["meter"]):
                    self.tree.set(item_id, "meter", str(orig["meter"]))
                    mv["meter"] = orig["meter"]
                    reset_count += 1
                else:
                    failed_writes.append(f"meter @ 0x{abs_addr:08X}")

            # Active
            if orig["active_start"] is not None and orig["active_end"] is not None:
                if U.write_active_frames(mv, orig["active_start"], orig["active_end"]):
                    self.tree.set(item_id, "startup", str(orig["active_start"]))
                    self.tree.set(item_id, "active", f"{orig['active_start']}-{orig['active_end']}")
                    mv["active_start"] = orig["active_start"]
                    mv["active_end"] = orig["active_end"]
                    reset_count += 1
                else:
                    failed_writes.append(f"active @ 0x{abs_addr:08X}")

            # Active2
            if orig["active2_start"] is not None and orig["active2_end"] is not None:
                if write_active2_frames_inline(mv, orig["active2_start"], orig["active2_end"], U.WRITER_AVAILABLE):
                    self.tree.set(item_id, "active2", f"{orig['active2_start']}-{orig['active2_end']}")
                    mv["active2_start"] = orig["active2_start"]
                    mv["active2_end"] = orig["active2_end"]
                    reset_count += 1
                else:
                    failed_writes.append(f"active2 @ 0x{abs_addr:08X}")

            # Hitstun / Blockstun / Hitstop
            if orig["hitstun"] is not None:
                if U.write_hitstun(mv, orig["hitstun"]):
                    self.tree.set(item_id, "hitstun", U.fmt_stun(orig["hitstun"]))
                    mv["hitstun"] = orig["hitstun"]
                    reset_count += 1
                else:
                    failed_writes.append(f"hitstun @ 0x{abs_addr:08X}")

            if orig["blockstun"] is not None:
                if U.write_blockstun(mv, orig["blockstun"]):
                    self.tree.set(item_id, "blockstun", U.fmt_stun(orig["blockstun"]))
                    mv["blockstun"] = orig["blockstun"]
                    reset_count += 1
                else:
                    failed_writes.append(f"blockstun @ 0x{abs_addr:08X}")

            if orig["hitstop"] is not None:
                if U.write_hitstop(mv, orig["hitstop"]):
                    self.tree.set(item_id, "hitstop", str(orig["hitstop"]))
                    mv["hitstop"] = orig["hitstop"]
                    reset_count += 1
                else:
                    failed_writes.append(f"hitstop @ 0x{abs_addr:08X}")

            # Knockback
            if (orig["kb0"] is not None) or (orig["kb1"] is not None) or (orig["kb_traj"] is not None):
                if U.write_knockback(mv, orig["kb0"], orig["kb1"], orig["kb_traj"]):
                    parts = []
                    if orig["kb0"] is not None:
                        parts.append(f"K0:{orig['kb0']}")
                    if orig["kb1"] is not None:
                        parts.append(f"K1:{orig['kb1']}")
                    if orig["kb_traj"] is not None:
                        parts.append(U.fmt_kb_traj(orig["kb_traj"]))
                    self.tree.set(item_id, "kb", " ".join(parts))
                    mv["kb0"] = orig["kb0"]
                    mv["kb1"] = orig["kb1"]
                    mv["kb_traj"] = orig["kb_traj"]
                    reset_count += 1
                else:
                    failed_writes.append(f"knockback @ 0x{abs_addr:08X}")

            # Combo KB mod
            if orig.get("combo_kb_mod_addr") and orig.get("combo_kb_mod") is not None:
                mv["combo_kb_mod_addr"] = orig["combo_kb_mod_addr"]
                if write_combo_kb_mod_inline(mv, orig["combo_kb_mod"], U.WRITER_AVAILABLE):
                    mv["combo_kb_mod"] = orig["combo_kb_mod"]
                    self.tree.set(item_id, "combo_kb_mod", f"{orig['combo_kb_mod']} (0x{orig['combo_kb_mod']:02X})")
                    reset_count += 1
                else:
                    failed_writes.append(f"combo_kb_mod @ 0x{abs_addr:08X}")

            # Hit reaction
            if orig.get("hit_reaction") is not None:
                if write_hit_reaction_inline(mv, orig["hit_reaction"], U.WRITER_AVAILABLE):
                    self.tree.set(item_id, "hit_reaction", U.fmt_hit_reaction(orig["hit_reaction"]))
                    mv["hit_reaction"] = orig["hit_reaction"]
                    reset_count += 1
                else:
                    failed_writes.append(f"hit_reaction @ 0x{abs_addr:08X}")

            # Hitbox
            orig_val = orig.get("hb_r")
            orig_off = orig.get("hb_off")
            orig_cands = orig.get("hb_candidates") or []
            if orig_val is not None and orig_off is not None:
                mv["hb_off"] = orig_off
                mv["hb_r"] = orig_val
                if U.write_hitbox_radius(mv, orig_val):
                    self.tree.set(item_id, "hb_main", f"{orig_val:.1f}")
                    reset_count += 1
                else:
                    failed_writes.append(f"hitbox @ 0x{abs_addr:08X}")
            mv["hb_candidates"] = orig_cands
            self.tree.set(item_id, "hb", U.format_candidate_list(orig_cands))

            # SuperBG
            if orig.get("superbg_addr") and orig.get("superbg_val") is not None:
                mv["superbg_addr"] = orig["superbg_addr"]
                mv["superbg_val"] = orig["superbg_val"]
                if write_superbg_inline(mv, (orig["superbg_val"] == SUPERBG_ON), U.WRITER_AVAILABLE):
                    self.tree.set(item_id, "superbg", U.fmt_superbg(orig["superbg_val"]))
                    reset_count += 1
                else:
                    failed_writes.append(f"superbg @ 0x{abs_addr:08X}")

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

        self._apply_row_tags(item, mv)
        self._set_status_for_item(item, mv)

    # ---------- Right-click address tools + raw view ----------

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
        except ImportError:
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
        # No mainloop here; tk_host owns the root.mainloop()
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
