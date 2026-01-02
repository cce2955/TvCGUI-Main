# fd_window.py
#
# Main Tk window logic for the frame data editor.

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import threading
import math

from move_id_map import lookup_move_name
from moves import CHAR_ID_CORRECTION

from fd_dialogs import ReplaceMoveDialog
from fd_format import (
    fmt_kb_traj,
    fmt_hit_reaction,
    parse_hit_reaction_input,
    fmt_stun,
    unfmt_stun,
    HIT_REACTION_MAP,
)
from fd_patterns import (
    find_combo_kb_mod_addr,
    find_superbg_addr,
    SUPERBG_ON,
)
from fd_write_helpers import (
    write_hit_reaction_inline,
    write_active2_frames_inline,
    write_anim_id_inline,
    write_combo_kb_mod_inline,
    write_superbg_inline,
)

WRITER_AVAILABLE = False
try:
    from move_writer import (
        write_damage,
        write_meter,
        write_active_frames,
        write_hitstun,
        write_blockstun,
        write_hitstop,
        write_knockback,
        write_hitbox_radius,
        write_attack_property,
    )
    WRITER_AVAILABLE = True
except ImportError:
    print("WARNING: move_writer not found, editor will be read-only")

try:
    from dolphin_io import rdf32
except ImportError:
    rdf32 = None

try:
    from scan_normals_all import ANIM_MAP as _ANIM_MAP_FOR_GUI
except Exception:
    _ANIM_MAP_FOR_GUI = {}

HB_SCAN_MAX = 0x600
FALLBACK_HB_OFFSET = 0x21C
MIN_REAL_RADIUS = 5.0


def _pretty_move_name(aid, char_name=None):
    if aid is None:
        return "anim_--"

    char_id = None
    if char_name:
        try:
            char_id = CHAR_ID_CORRECTION.get(char_name, None)
        except Exception:
            char_id = None

    name = lookup_move_name(aid, char_id)
    if name:
        return name

    if aid < 0x100:
        for high in (0x100, 0x200, 0x300):
            name = lookup_move_name(aid + high, char_id)
            if name:
                return name

    name = _ANIM_MAP_FOR_GUI.get(aid)
    if name:
        return name

    return f"anim_{aid:04X}"


def _scan_hitbox_house(move_abs: int):
    if rdf32 is None or not move_abs:
        return []
    candidates: list[tuple[int, float]] = []
    for off in range(0, HB_SCAN_MAX, 4):
        try:
            f = rdf32(move_abs + off)
        except Exception:
            continue
        if f is None or not isinstance(f, (int, float)) or not math.isfinite(f):
            continue
        if abs(f) < 1e-6:
            continue
        candidates.append((off, float(f)))
    return candidates


def _select_primary_from_candidates(cands: list[tuple[int, float]]):
    if not cands:
        return None, None

    for off, val in cands:
        if val >= 400.0:
            return off, val

    MAX_REAL_RADIUS = 42.0
    best_off, best_val = None, -1.0
    for off, val in cands:
        if MIN_REAL_RADIUS <= val <= MAX_REAL_RADIUS and val > best_val:
            best_off, best_val = off, val

    if best_off is not None:
        return best_off, best_val

    for off, val in reversed(cands):
        if MIN_REAL_RADIUS <= val <= MAX_REAL_RADIUS:
            return off, val

    return cands[-1] if cands else (None, None)


def _format_candidate_list(cands: list[tuple[int, float]], max_show: int = 4) -> str:
    parts = []
    for idx, (_off, val) in enumerate(cands[:max_show]):
        parts.append(f"r{idx}={val:.1f}")
    if len(cands) > max_show:
        parts.append("…")
    return " ".join(parts)


def _fmt_superbg(v):
    if v is None:
        return ""
    return "ON" if int(v) == 0x04 else "OFF"


class EditableFrameDataWindow:
    def __init__(self, slot_label, target_slot):
        self.slot_label = slot_label
        self.target_slot = target_slot

        cname = self.target_slot.get("char_name")

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

        self._build()

    def _clone_move_block_y2(self, src_mv, dst_mv) -> bool:
        if not WRITER_AVAILABLE:
            return False

        src_abs = src_mv.get("abs")
        dst_abs = dst_mv.get("abs")
        if not src_abs or not dst_abs:
            return False

        try:
            from dolphin_io import rbytes, wd8
        except ImportError:
            return False

        src_next = self.next_abs_map.get(src_abs)
        if src_next and src_next > src_abs:
            size = src_next - src_abs
        else:
            size = 0x200

        dst_next = self.next_abs_map.get(dst_abs)
        if dst_next and dst_next > dst_abs:
            max_size = dst_next - dst_abs
            size = min(size, max_size)

        if size <= 0:
            return False

        try:
            data = rbytes(src_abs, size)
        except Exception:
            return False

        try:
            for i, b in enumerate(data):
                if not wd8(dst_abs + i, b):
                    return False
            return True
        except Exception:
            return False

    def _edit_move_replacement(self, item, mv):
        if not WRITER_AVAILABLE:
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
            ok = write_anim_id_inline(mv, new_id, WRITER_AVAILABLE)
        else:
            ok = self._clone_move_block_y2(new_mv, mv)

        if not ok:
            messagebox.showerror("Error", "Failed to write replacement to Dolphin.")
            return

        mv["id"] = new_id
        mv["move_name"] = new_mv.get("move_name") or mv.get("move_name")

        cname = self.target_slot.get("char_name", "—")
        pretty = _pretty_move_name(new_id, cname)
        dup_idx = mv.get("dup_index")
        if dup_idx is not None:
            pretty = f"{pretty} (Tier{dup_idx + 1})"
        pretty = f"{pretty} [0x{new_id:04X}]"
        self.tree.set(item, "move", pretty)

    def _build(self):
        cname = self.target_slot.get("char_name", "—")
        self.root = tk.Tk()
        self.root.title(f"Frame Data Editor: {self.slot_label} ({cname})")
        self.root.geometry("1520x720")

        top = tk.Frame(self.root)
        top.pack(side="top", fill="x", padx=5, pady=5)

        if WRITER_AVAILABLE:
            tk.Label(
                top,
                text="Double-click to edit. Right-click for address. Writes to Dolphin.",
                fg="blue",
            ).pack(side="left")
        else:
            tk.Label(
                top,
                text="WARNING: move_writer not found. Editing disabled!",
                fg="red",
            ).pack(side="left")

        tk.Button(top, text="Reset to original", command=self._reset_all_moves).pack(side="right", padx=4)

        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=5, pady=5)

        cols = (
            "move", "kind", "damage", "meter",
            "startup", "active", "active2",
            "hitstun", "blockstun", "hitstop",
            "hb_main", "hb",
            "kb", "combo_kb_mod", "hit_reaction",
            "superbg",
            "abs",
        )
        self.tree = ttk.Treeview(frame, columns=cols, show="tree headings", height=30)

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        self.tree.heading("#0", text="")
        self.tree.column("#0", width=24, stretch=False, anchor="w")

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
            ("hb_main", "Hitbox"),
            ("hb", "Hitbox cand."),
            ("kb", "Knockback"),
            ("combo_kb_mod", "Combo KB Mod"),
            ("hit_reaction", "Hit Reaction"),
            ("superbg", "SuperBG"),
            ("abs", "Address"),
        ]
        for c, txt in headers:
            self.tree.heading(c, text=txt)

        self.tree.column("move", width=200, anchor="w")
        self.tree.column("kind", width=60, anchor="w")
        self.tree.column("damage", width=65, anchor="center")
        self.tree.column("meter", width=55, anchor="center")
        self.tree.column("startup", width=55, anchor="center")
        self.tree.column("active", width=90, anchor="center")
        self.tree.column("active2", width=90, anchor="center")
        self.tree.column("hitstun", width=45, anchor="center")
        self.tree.column("blockstun", width=45, anchor="center")
        self.tree.column("hitstop", width=50, anchor="center")
        self.tree.column("hb_main", width=70, anchor="center")
        self.tree.column("hb", width=220, anchor="w")
        self.tree.column("kb", width=160, anchor="center")
        self.tree.column("combo_kb_mod", width=120, anchor="center")
        self.tree.column("hit_reaction", width=240, anchor="w")
        self.tree.column("superbg", width=70, anchor="center")
        self.tree.column("abs", width=100, anchor="w")

        cname = self.target_slot.get("char_name", "—")

        def _insert_move_row(mv, parent=""):
            aid = mv.get("id")
            move_name = _pretty_move_name(aid, cname)

            if aid is not None:
                dup_idx = mv.get("dup_index")
                if dup_idx is not None:
                    move_name = f"{move_name} (Tier{dup_idx + 1})"
                move_name = f"{move_name} [0x{aid:04X}]"

            a_s = mv.get("active_start")
            a_e = mv.get("active_end")
            startup_txt = "" if a_s is None else str(a_s)
            active_txt = f"{a_s}-{a_e}" if (a_s is not None and a_e is not None) else ""

            a2_s = mv.get("active2_start")
            a2_e = mv.get("active2_end")
            if a2_s is None and a2_e is None:
                active2_txt = ""
            elif a2_s is None:
                active2_txt = str(a2_e)
            elif a2_e is None:
                active2_txt = str(a2_s)
            else:
                active2_txt = f"{a2_s}-{a2_e}"

            move_abs = mv.get("abs")

            hb_cands = []
            hb_off = None
            hb_val = None
            hb_txt = ""
            hb_main_txt = ""
            if move_abs:
                hb_cands = _scan_hitbox_house(move_abs)
                hb_off, hb_val = _select_primary_from_candidates(hb_cands)
                if hb_val is not None:
                    hb_main_txt = f"{hb_val:.1f}"
                if hb_cands:
                    hb_txt = _format_candidate_list(hb_cands)

            mv["hb_candidates"] = hb_cands
            mv["hb_off"] = hb_off
            mv["hb_r"] = hb_val

            kb0 = mv.get("kb0")
            kb1 = mv.get("kb1")
            kb_traj = mv.get("kb_traj")
            kb_parts = []
            if kb0 is not None:
                kb_parts.append(f"K0:{kb0}")
            if kb1 is not None:
                kb_parts.append(f"K1:{kb1}")
            if kb_traj is not None:
                kb_parts.append(fmt_kb_traj(kb_traj))
            kb_txt = " ".join(kb_parts)

            # Combo KB Mod discovery
            combo_txt = ""
            if move_abs and mv.get("combo_kb_mod_addr") is None:
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
                combo_txt = f"{v} (0x{v:02X})" if v is not None else "?"

            # SuperBG discovery
            superbg_txt = ""
            if move_abs and mv.get("superbg_addr") is None:
                try:
                    from dolphin_io import rbytes, rd8
                    saddr, sval = find_superbg_addr(move_abs, rbytes, rd8)
                except Exception:
                    saddr, sval = (None, None)
                if saddr:
                    mv["superbg_addr"] = saddr
                    mv["superbg_val"] = sval
            if mv.get("superbg_addr"):
                superbg_txt = _fmt_superbg(mv.get("superbg_val"))

            hr_txt = fmt_hit_reaction(mv.get("hit_reaction"))

            item_id = self.tree.insert(
                parent,
                "end",
                text="",
                values=(
                    move_name,
                    mv.get("kind", ""),
                    "" if mv.get("damage") is None else str(mv.get("damage")),
                    "" if mv.get("meter") is None else str(mv.get("meter")),
                    startup_txt,
                    active_txt,
                    active2_txt,
                    fmt_stun(mv.get("hitstun")),
                    fmt_stun(mv.get("blockstun")),
                    "" if mv.get("hitstop") is None else str(mv.get("hitstop")),
                    hb_main_txt,
                    hb_txt,
                    kb_txt,
                    combo_txt,
                    hr_txt,
                    superbg_txt,
                    f"0x{mv.get('abs', 0):08X}" if mv.get("abs") else "",
                ),
            )
            self.move_to_tree_item[item_id] = mv

            abs_key = mv.get("abs")
            if abs_key:
                self.original_moves[abs_key] = {
                    "damage": mv.get("damage"),
                    "meter": mv.get("meter"),
                    "active_start": mv.get("active_start"),
                    "active_end": mv.get("active_end"),
                    "active2_start": a2_s,
                    "active2_end": a2_e,
                    "hitstun": mv.get("hitstun"),
                    "blockstun": mv.get("blockstun"),
                    "hitstop": mv.get("hitstop"),
                    "kb0": mv.get("kb0"),
                    "kb1": mv.get("kb1"),
                    "kb_traj": mv.get("kb_traj"),
                    "hit_reaction": mv.get("hit_reaction"),
                    "hb_off": hb_off,
                    "hb_r": hb_val,
                    "hb_candidates": hb_cands,
                    "combo_kb_mod": mv.get("combo_kb_mod"),
                    "combo_kb_mod_addr": mv.get("combo_kb_mod_addr"),
                    "superbg_addr": mv.get("superbg_addr"),
                    "superbg_val": mv.get("superbg_val"),
                }

            return item_id

        groups = []
        index_by_id = {}

        for mv in self.moves:
            aid = mv.get("id")
            if aid is None:
                groups.append((None, [mv]))
                continue
            if aid in index_by_id:
                groups[index_by_id[aid]][1].append(mv)
            else:
                index_by_id[aid] = len(groups)
                groups.append((aid, [mv]))

        for aid, mv_list in groups:
            if aid is None or len(mv_list) == 1:
                _insert_move_row(mv_list[0], parent="")
                continue

            parent_item = _insert_move_row(mv_list[0], parent="")
            self.tree.item(parent_item, open=False)
            for mv in mv_list[1:]:
                _insert_move_row(mv, parent=parent_item)

        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)

    def _reset_all_moves(self):
        if not WRITER_AVAILABLE:
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

            if orig["damage"] is not None:
                if write_damage(mv, orig["damage"]):
                    self.tree.set(item_id, "damage", str(orig["damage"]))
                    mv["damage"] = orig["damage"]
                    reset_count += 1
                else:
                    failed_writes.append(f"damage @ 0x{abs_addr:08X}")

            if orig["meter"] is not None:
                if write_meter(mv, orig["meter"]):
                    self.tree.set(item_id, "meter", str(orig["meter"]))
                    mv["meter"] = orig["meter"]
                    reset_count += 1
                else:
                    failed_writes.append(f"meter @ 0x{abs_addr:08X}")

            if orig["active_start"] is not None and orig["active_end"] is not None:
                if write_active_frames(mv, orig["active_start"], orig["active_end"]):
                    self.tree.set(item_id, "startup", str(orig["active_start"]))
                    self.tree.set(item_id, "active", f"{orig['active_start']}-{orig['active_end']}")
                    mv["active_start"] = orig["active_start"]
                    mv["active_end"] = orig["active_end"]
                    reset_count += 1
                else:
                    failed_writes.append(f"active @ 0x{abs_addr:08X}")

            if orig["active2_start"] is not None and orig["active2_end"] is not None:
                if write_active2_frames_inline(mv, orig["active2_start"], orig["active2_end"], WRITER_AVAILABLE):
                    self.tree.set(item_id, "active2", f"{orig['active2_start']}-{orig['active2_end']}")
                    mv["active2_start"] = orig["active2_start"]
                    mv["active2_end"] = orig["active2_end"]
                    reset_count += 1
                else:
                    failed_writes.append(f"active2 @ 0x{abs_addr:08X}")

            if orig["hitstun"] is not None:
                if write_hitstun(mv, orig["hitstun"]):
                    self.tree.set(item_id, "hitstun", fmt_stun(orig["hitstun"]))
                    mv["hitstun"] = orig["hitstun"]
                    reset_count += 1
                else:
                    failed_writes.append(f"hitstun @ 0x{abs_addr:08X}")

            if orig["blockstun"] is not None:
                if write_blockstun(mv, orig["blockstun"]):
                    self.tree.set(item_id, "blockstun", fmt_stun(orig["blockstun"]))
                    mv["blockstun"] = orig["blockstun"]
                    reset_count += 1
                else:
                    failed_writes.append(f"blockstun @ 0x{abs_addr:08X}")

            if orig["hitstop"] is not None:
                if write_hitstop(mv, orig["hitstop"]):
                    self.tree.set(item_id, "hitstop", str(orig["hitstop"]))
                    mv["hitstop"] = orig["hitstop"]
                    reset_count += 1
                else:
                    failed_writes.append(f"hitstop @ 0x{abs_addr:08X}")

            if (orig["kb0"] is not None) or (orig["kb1"] is not None) or (orig["kb_traj"] is not None):
                if write_knockback(mv, orig["kb0"], orig["kb1"], orig["kb_traj"]):
                    parts = []
                    if orig["kb0"] is not None:
                        parts.append(f"K0:{orig['kb0']}")
                    if orig["kb1"] is not None:
                        parts.append(f"K1:{orig['kb1']}")
                    if orig["kb_traj"] is not None:
                        parts.append(fmt_kb_traj(orig["kb_traj"]))
                    self.tree.set(item_id, "kb", " ".join(parts))
                    mv["kb0"] = orig["kb0"]
                    mv["kb1"] = orig["kb1"]
                    mv["kb_traj"] = orig["kb_traj"]
                    reset_count += 1
                else:
                    failed_writes.append(f"knockback @ 0x{abs_addr:08X}")

            if orig.get("combo_kb_mod_addr") and orig.get("combo_kb_mod") is not None:
                mv["combo_kb_mod_addr"] = orig["combo_kb_mod_addr"]
                if write_combo_kb_mod_inline(mv, orig["combo_kb_mod"], WRITER_AVAILABLE):
                    mv["combo_kb_mod"] = orig["combo_kb_mod"]
                    self.tree.set(item_id, "combo_kb_mod", f"{orig['combo_kb_mod']} (0x{orig['combo_kb_mod']:02X})")
                    reset_count += 1
                else:
                    failed_writes.append(f"combo_kb_mod @ 0x{abs_addr:08X}")

            if orig.get("hit_reaction") is not None:
                if write_hit_reaction_inline(mv, orig["hit_reaction"], WRITER_AVAILABLE):
                    self.tree.set(item_id, "hit_reaction", fmt_hit_reaction(orig["hit_reaction"]))
                    mv["hit_reaction"] = orig["hit_reaction"]
                    reset_count += 1
                else:
                    failed_writes.append(f"hit_reaction @ 0x{abs_addr:08X}")

            orig_val = orig.get("hb_r")
            orig_off = orig.get("hb_off")
            orig_cands = orig.get("hb_candidates") or []

            if orig_val is not None and orig_off is not None:
                mv["hb_off"] = orig_off
                mv["hb_r"] = orig_val
                if write_hitbox_radius(mv, orig_val):
                    self.tree.set(item_id, "hb_main", f"{orig_val:.1f}")
                    reset_count += 1
                else:
                    failed_writes.append(f"hitbox @ 0x{abs_addr:08X}")

            mv["hb_candidates"] = orig_cands
            self.tree.set(item_id, "hb", _format_candidate_list(orig_cands))

            # SuperBG restore
            if orig.get("superbg_addr") and orig.get("superbg_val") is not None:
                mv["superbg_addr"] = orig["superbg_addr"]
                mv["superbg_val"] = orig["superbg_val"]
                if write_superbg_inline(mv, (orig["superbg_val"] == SUPERBG_ON), WRITER_AVAILABLE):
                    self.tree.set(item_id, "superbg", _fmt_superbg(orig["superbg_val"]))
                    reset_count += 1
                else:
                    failed_writes.append(f"superbg @ 0x{abs_addr:08X}")

        msg = f"Reset complete: {reset_count} writes successful"
        if failed_writes:
            msg += "\n\nFailed writes:\n" + "\n".join(failed_writes[:10])
        messagebox.showinfo("Reset", msg)

    def _on_double_click(self, event):
        if not WRITER_AVAILABLE:
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
            self._edit_move_replacement(item, mv)
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

    def _toggle_superbg(self, item, mv):
        if mv.get("superbg_addr") is None:
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

        if not mv.get("superbg_addr"):
            messagebox.showerror("Error", "SuperBG pattern not found for this move")
            return

        cur = mv.get("superbg_val")
        is_on = (cur == SUPERBG_ON)
        new_on = not is_on

        if write_superbg_inline(mv, new_on, WRITER_AVAILABLE):
            self.tree.set(item, "superbg", _fmt_superbg(mv.get("superbg_val")))
        else:
            messagebox.showerror("Error", "Failed to write SuperBG")

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

            if addr:
                menu.add_command(
                    label=f"Copy {label} Address (0x{addr:08X})",
                    command=lambda: self._copy_address(addr),
                )
                menu.add_command(
                    label=f"Go to {label} Address",
                    command=lambda: self._show_address_info(addr, f"{label} @ 0x{addr:08X}"),
                )
            else:
                menu.add_command(label=f"No {label} Address", state="disabled")

        menu.add_separator()
        menu.add_command(
            label="View Raw Move Data",
            command=lambda: self._show_raw_data(mv),
        )

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_address(self, addr):
        self.root.clipboard_clear()
        self.root.clipboard_append(f"0x{addr:08X}")
        messagebox.showinfo("Copied", f"0x{addr:08X} copied to clipboard")

    def _show_address_info(self, addr, title):
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
        dlg.geometry("640x420")

        txt = tk.Text(dlg, wrap="none", font=("Courier", 10))
        txt.pack(fill="both", expand=True, padx=5, pady=5)

        txt.insert("end", "Legend: '>>' = line containing the selected address; 16 bytes per line.\n\n")

        for i in range(total_lines):
            off = i * LINE_SIZE
            chunk = data[off:off + LINE_SIZE]
            line_addr = start + off
            hex_part = " ".join(f"{b:02X}" for b in chunk)
            ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)

            prefix = ">>" if i == current_line_index else "  "
            line = f"{prefix} 0x{line_addr:08X}: {hex_part:<47} {ascii_part}\n"
            txt.insert("end", line)

        txt.config(state="disabled")

    def _show_raw_data(self, mv):
        dlg = tk.Toplevel(self.root)
        dlg.title("Raw Move Data")
        dlg.geometry("600x500")

        frame = tk.Frame(dlg)
        frame.pack(fill="both", expand=True, padx=5, pady=5)

        txt = tk.Text(frame, wrap="word", font=("Courier", 10))
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

    # ===== Editors =====

    def _edit_active2(self, item, mv, current):
        current = current.strip()
        if "-" in current:
            parts = current.split("-")
            try:
                cur_s = int(parts[0])
                cur_e = int(parts[1])
            except ValueError:
                cur_s, cur_e = 1, 1
        else:
            cur_s = mv.get("active2_start", 1)
            cur_e = mv.get("active2_end", cur_s)

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
            s = sv.get()
            e = ev.get()
            if e < s:
                e = s
            if write_active2_frames_inline(mv, s, e, WRITER_AVAILABLE):
                self.tree.set(item, "active2", f"{s}-{e}")
                mv["active2_start"] = s
                mv["active2_end"] = e
                messagebox.showinfo("Success", f"Active 2 updated to {s}-{e}")
            else:
                messagebox.showerror("Error", "Failed to write Active 2 frames")
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok, font=("Arial", 10)).pack(pady=10)

    def _edit_damage(self, item, mv, current):
        try:
            cur = int(current) if current else 0
        except ValueError:
            cur = 0
        new_val = simpledialog.askinteger("Edit Damage", "New damage:", initialvalue=cur, minvalue=0, maxvalue=999999)
        if new_val is not None and write_damage(mv, new_val):
            self.tree.set(item, "damage", str(new_val))
            mv["damage"] = new_val

    def _edit_meter(self, item, mv, current):
        try:
            cur = int(current) if current else 0
        except ValueError:
            cur = 0
        new_val = simpledialog.askinteger("Edit Meter", "New meter:", initialvalue=cur, minvalue=0, maxvalue=255)
        if new_val is not None and write_meter(mv, new_val):
            self.tree.set(item, "meter", str(new_val))
            mv["meter"] = new_val

    def _edit_startup(self, item, mv, current):
        try:
            cur = int(current) if current else 1
        except ValueError:
            cur = 1
        new_val = simpledialog.askinteger("Edit Startup", "New startup frame:", initialvalue=cur, minvalue=1, maxvalue=255)
        if new_val is not None:
            end = mv.get("active_end", new_val)
            if end < new_val:
                end = new_val
            if write_active_frames(mv, new_val, end):
                self.tree.set(item, "startup", str(new_val))
                self.tree.set(item, "active", f"{new_val}-{end}")
                mv["active_start"] = new_val
                mv["active_end"] = end

    def _edit_active(self, item, mv, current):
        current = current.strip()
        if "-" in current:
            parts = current.split("-")
            try:
                cur_s = int(parts[0])
                cur_e = int(parts[1])
            except ValueError:
                cur_s, cur_e = 1, 1
        else:
            cur_s = mv.get("active_start", 1)
            cur_e = mv.get("active_end", cur_s)

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
            s = sv.get()
            e = ev.get()
            if e < s:
                e = s
            if write_active_frames(mv, s, e):
                self.tree.set(item, "startup", str(s))
                self.tree.set(item, "active", f"{s}-{e}")
                mv["active_start"] = s
                mv["active_end"] = e
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).pack(pady=8)

    def _edit_hitstun(self, item, mv, current):
        cur = unfmt_stun(current) if current else 0
        new_val = simpledialog.askinteger("Edit Hitstun", "New hitstun:", initialvalue=cur, minvalue=0, maxvalue=255)
        if new_val is not None and write_hitstun(mv, new_val):
            self.tree.set(item, "hitstun", fmt_stun(new_val))
            mv["hitstun"] = new_val

    def _edit_blockstun(self, item, mv, current):
        cur = unfmt_stun(current) if current else 0
        new_val = simpledialog.askinteger("Edit Blockstun", "New blockstun:", initialvalue=cur, minvalue=0, maxvalue=255)
        if new_val is not None and write_blockstun(mv, new_val):
            self.tree.set(item, "blockstun", fmt_stun(new_val))
            mv["blockstun"] = new_val

    def _edit_hitstop(self, item, mv, current):
        try:
            cur = int(current) if current else 0
        except ValueError:
            cur = 0
        new_val = simpledialog.askinteger("Edit Hitstop", "New hitstop:", initialvalue=cur, minvalue=0, maxvalue=255)
        if new_val is not None and write_hitstop(mv, new_val):
            self.tree.set(item, "hitstop", str(new_val))
            mv["hitstop"] = new_val

    def _edit_knockback(self, item, mv, current):
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
                k0 = k0v.get()
                k1 = k1v.get()
                t_str = tv.get().strip()
                t = int(t_str, 16) if t_str.lower().startswith("0x") else int(t_str, 16)
            except (ValueError, AttributeError):
                messagebox.showerror("Error", "Invalid knockback values")
                return

            if write_knockback(mv, k0, k1, t):
                self.tree.set(item, "kb", f"K0:{k0} K1:{k1} {fmt_kb_traj(t)}")
                mv["kb0"] = k0
                mv["kb1"] = k1
                mv["kb_traj"] = t
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).pack(pady=10)

    def _edit_combo_kb_mod(self, item, mv, current):
        move_abs = mv.get("abs")
        if move_abs and not mv.get("combo_kb_mod_addr"):
            try:
                from dolphin_io import rbytes
                addr, cur, sig = find_combo_kb_mod_addr(move_abs, rbytes)
            except Exception:
                addr, cur, sig = (None, None, None)
            if addr:
                mv["combo_kb_mod_addr"] = addr
                mv["combo_kb_mod"] = cur
                mv["combo_kb_sig"] = sig

        cur_val = mv.get("combo_kb_mod")
        if cur_val is None:
            try:
                if current and "0x" in current:
                    cur_val = int(current.split("0x")[-1].strip(") "), 16) & 0xFF
                elif current and current.strip().isdigit():
                    cur_val = int(current.strip()) & 0xFF
            except Exception:
                cur_val = 0
        if cur_val is None:
            cur_val = 0

        addr = mv.get("combo_kb_mod_addr")
        sig = mv.get("combo_kb_sig")
        sig_txt = f"0x{sig:02X}" if sig is not None else "?"

        prompt = (
            "New Combo KB Mod (0-255):\n\n"
            "Likely affects combo-only pull/vacuum/knockback scaling.\n"
            "Suggested safe band: 0-160.\n\n"
            f"Signature byte: {sig_txt}\n"
            f"Address: {('0x%08X' % addr) if addr else 'not found'}"
        )
        new_val = simpledialog.askinteger("Edit Combo KB Mod", prompt, initialvalue=int(cur_val), minvalue=0, maxvalue=255)
        if new_val is None:
            return

        if not addr:
            messagebox.showerror("Error", "Pattern not found for Combo KB Mod")
            return

        if write_combo_kb_mod_inline(mv, new_val, WRITER_AVAILABLE):
            mv["combo_kb_mod"] = new_val & 0xFF
            self.tree.set(item, "combo_kb_mod", f"{mv['combo_kb_mod']} (0x{mv['combo_kb_mod']:02X})")
        else:
            messagebox.showerror("Error", "Failed to write Combo KB Mod.")

    def _edit_hit_reaction(self, item, mv, current):
        cur_hr = mv.get("hit_reaction")

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Hit Reaction")
        dlg.geometry("520x420")

        tk.Label(dlg, text="Hit Reaction Type", font=("Arial", 12, "bold")).pack(pady=5)

        if cur_hr is not None:
            tk.Label(dlg, text=f"Current: {fmt_hit_reaction(cur_hr)}", fg="blue", font=("Arial", 10)).pack(pady=3)

        tk.Label(dlg, text="Common Reactions:", font=("Arial", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 5))

        frame = tk.Frame(dlg)
        frame.pack(fill="both", expand=True, padx=10, pady=5)

        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")

        listbox = tk.Listbox(frame, yscrollcommand=scrollbar.set)
        scrollbar.config(command=listbox.yview)

        common_vals = sorted(HIT_REACTION_MAP.keys())

        common = []
        for val in common_vals:
            common.append((val, HIT_REACTION_MAP.get(val, "Unknown")))

        for val, desc in common:
            listbox.insert("end", f"0x{val:06X}: {desc}")

        listbox.pack(fill="both", expand=True)

        tk.Label(dlg, text="Or enter hex/decimal value:", font=("Arial", 10)).pack(anchor="w", padx=10, pady=(10, 0))
        hex_entry = tk.Entry(dlg, width=20)
        hex_entry.insert(0, f"0x{cur_hr:06X}" if cur_hr is not None else "0x000000")
        hex_entry.pack(anchor="w", padx=10)

        def on_select(_evt):
            sel = listbox.curselection()
            if sel:
                val, _ = common[sel[0]]
                hex_entry.delete(0, tk.END)
                hex_entry.insert(0, f"0x{val:06X}")

        listbox.bind("<<ListboxSelect>>", on_select)

        def on_ok():
            val = parse_hit_reaction_input(hex_entry.get())
            if val is None:
                dlg.destroy()
                return
            if write_hit_reaction_inline(mv, val, WRITER_AVAILABLE):
                self.tree.set(item, "hit_reaction", fmt_hit_reaction(val))
                mv["hit_reaction"] = val
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).pack(pady=10)

    def _edit_hitbox_main(self, item, mv, current):
        cur_r = mv.get("hb_r")
        if cur_r is None:
            cands = mv.get("hb_candidates") or []
            if cands:
                cur_r = cands[0][1]
                mv["hb_off"] = cands[0][0]
        if cur_r is None:
            cur_r = 0.0

        new_val = simpledialog.askfloat("Edit Hitbox", "New radius:", initialvalue=cur_r, minvalue=0.0)
        if new_val is None:
            return

        if mv.get("hb_off") is None:
            mv["hb_off"] = FALLBACK_HB_OFFSET
        if WRITER_AVAILABLE:
            write_hitbox_radius(mv, new_val)

        mv["hb_r"] = new_val
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

        self.tree.set(item, "hb_main", f"{new_val:.1f}")
        self.tree.set(item, "hb", _format_candidate_list(mv["hb_candidates"]))

    def _edit_hitbox(self, item, mv, current):
        cands = mv.get("hb_candidates") or []
        if not cands:
            return self._edit_hitbox_main(item, mv, current)
        if len(cands) <= 6:
            return self._edit_hitbox_simple(item, mv, cands)
        return self._edit_hitbox_scrollable(item, mv, cands)

    def _edit_hitbox_simple(self, item, mv, cands):
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Hitbox Values")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="Edit each radius below. r0 is usually the main one.").grid(row=0, column=0, columnspan=3, padx=6, pady=4, sticky="w")

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
                txt = entry.get().strip()
                try:
                    fval = float(txt)
                except ValueError:
                    fval = cands[idx2][1]
                mv["hb_off"] = off2
                if WRITER_AVAILABLE:
                    write_hitbox_radius(mv, fval)
                new_cands.append((off2, float(fval)))

            mv["hb_candidates"] = new_cands
            sel_off, sel_val = _select_primary_from_candidates(new_cands)
            mv["hb_off"] = sel_off
            mv["hb_r"] = sel_val

            self.tree.set(item, "hb_main", f"{sel_val:.1f}" if sel_val is not None else "")
            self.tree.set(item, "hb", _format_candidate_list(new_cands))
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

        tk.Label(inner, text="Edit each radius below. r0 is usually the main one.", anchor="w", justify="left").grid(row=0, column=0, columnspan=3, padx=6, pady=4, sticky="w")

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
                txt = entry.get().strip()
                try:
                    fval = float(txt)
                except ValueError:
                    fval = cands[idx2][1]
                mv["hb_off"] = off2
                if WRITER_AVAILABLE:
                    write_hitbox_radius(mv, fval)
                new_cands.append((off2, float(fval)))

            mv["hb_candidates"] = new_cands
            sel_off, sel_val = _select_primary_from_candidates(new_cands)
            mv["hb_off"] = sel_off
            mv["hb_r"] = sel_val

            self.tree.set(item, "hb_main", f"{sel_val:.1f}" if sel_val is not None else "")
            self.tree.set(item, "hb", _format_candidate_list(new_cands))
            dlg.destroy()

        tk.Button(inner, text="OK", command=on_ok).grid(row=row, column=0, columnspan=3, pady=8)

    def show(self):
        self.root.mainloop()


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

    def run():
        win = EditableFrameDataWindow(slot_label, target)
        win.show()

    t = threading.Thread(target=run, daemon=True)
    t.start()
