# fd_window.py

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

import fd_utils as U
import fd_tree
from fd_editors import FDCellEditorsMixin

from fd_patterns import (
    find_combo_kb_mod_addr,
    find_superbg_addr,
    find_speed_mod_addr,   # NEW
    SUPERBG_ON,
)

from fd_write_helpers import (
    write_hit_reaction_inline,
    write_active2_frames_inline,
    write_combo_kb_mod_inline,
    write_superbg_inline,
    write_speed_mod_inline,  # NEW
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
        fd_tree.build_tree_widget(self)   # fd_tree must include the new speed_mod column (see note below)
        fd_tree.populate_tree(self)       # fd_tree must also populate speed_mod

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

        speed_txt = self.tree.set(item_id, "speed_mod").strip()
        if speed_txt:
            tags.add("combo_hot")  # reuse existing accent

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

            # speed mod (NEW)
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

            # (existing reset code stays the same...) --------------

            # Speed mod (NEW)
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

        # Existing routes (damage/meter/etc.) are in FDCellEditorsMixin.
        if col_name == "speed_mod":
            self._edit_speed_mod(item, mv, current_val)
        else:
            self._route_standard_edit(col_name, item, mv, current_val)

        self._apply_row_tags(item, mv)
        self._set_status_for_item(item, mv)

    # Keep standard routing in one place to avoid duplicating if/elif ladders.
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
            "speed_mod": ("speed_mod_addr", "Speed Mod"),  # NEW
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
