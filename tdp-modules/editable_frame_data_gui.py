# editable_frame_data_gui.py
#
# Frame Data Editor window for TvC HUD
# - discovers per-move hitbox radius by scanning the move block
# - lets you edit damage, meter, startup/active, stuns, knockback, radius
# - auto-fixes active so end >= start
# - has a "Reset to original" button that replays all original values
# - can be launched from main.py via open_editable_frame_data_window(...)
#
# requires:
#   - dolphin_io with rdf32 (read float) available
#   - move_writer.py in the same folder

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import threading
import math

# ------------------------------------------------------------
# imports from writer (we just bail to read-only if missing)
# ------------------------------------------------------------
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

# ------------------------------------------------------------
# dolphin float reader
# ------------------------------------------------------------
try:
    from dolphin_io import rdf32
except ImportError:
    rdf32 = None

# optional anim map (nice names)
try:
    from scan_normals_all import ANIM_MAP as _ANIM_MAP_FOR_GUI
except Exception:
    _ANIM_MAP_FOR_GUI = {}

# ------------------------------------------------------------
# hitbox scan settings
# ------------------------------------------------------------
HB_SCAN_MAX = 0x600          # how far to look from move base for anchors
HB_AFTER_ANCHOR_SCAN = 0x200  # how far after each anchor to look for a real float
FALLBACK_HB_OFFSET = 0x21C    # Ryu 5A proved this
MIN_REAL_RADIUS = 5.0         # we decided anything smaller is probably bone scale


def _probe_hitbox_radius(move_abs: int):
    """
    Pattern-based hitbox radius finder.

    layout you showed:
        ... ff ff ff fe (shows up as None in rdf32)
        ... bunch of 3F 00 00 00
        ... later: 41 A0 00 00 (20.0) or similar

    we:
      - collect ALL anchors (places where rdf32(...) is None)
      - for each anchor, scan forward and pick the first float >= MIN_REAL_RADIUS
      - allow >= 400.0 to win immediately (for your poke tests)
      - if everything fails, fall back to 0x21C and no value
    """
    if rdf32 is None or not move_abs:
        return None, None

    anchors: list[int] = []

    # 1) collect anchors
    for off in range(0, HB_SCAN_MAX, 4):
        try:
            f = rdf32(move_abs + off)
        except Exception:
            continue
        if f is None:
            anchors.append(off)

    # 2) try each anchor
    for anchor_off in anchors:
        start_after = anchor_off + 4
        end_after = anchor_off + HB_AFTER_ANCHOR_SCAN
        for off in range(start_after, end_after, 4):
            try:
                f = rdf32(move_abs + off)
            except Exception:
                continue
            if f is None:
                continue
            if not isinstance(f, (int, float)):
                continue
            if not math.isfinite(f):
                continue

            # you poked it to a huge value
            if f >= 400.0:
                return off, float(f)

            # real-looking radius: we ignore bone-size floats (<5.0)
            if MIN_REAL_RADIUS <= f <= 300.0:
                return off, float(f)

            # else ignore and keep scanning

    # 3) no good hitboxes found: fallback
    return FALLBACK_HB_OFFSET, None


# ------------------------------------------------------------
# stun helpers
# ------------------------------------------------------------
def _fmt_stun(v):
    if v is None:
        return ""
    if v == 0x0C:
        return "10"
    if v == 0x0F:
        return "15"
    if v == 0x11:
        return "17"
    if v == 0x15:
        return "21"
    return str(v)


def _unfmt_stun(s):
    s = s.strip()
    if not s:
        return None
    try:
        val = int(s)
    except ValueError:
        return None
    if val == 10:
        return 0x0C
    if val == 15:
        return 0x0F
    if val == 17:
        return 0x11
    if val == 21:
        return 0x15
    return val


# ------------------------------------------------------------
# main window
# ------------------------------------------------------------
class EditableFrameDataWindow:
    def __init__(self, slot_label, target_slot):
        self.slot_label = slot_label
        self.target_slot = target_slot

        # sort+dedupe like your HUD
        moves_sorted = sorted(
            target_slot.get("moves", []),
            key=lambda m: (
                m.get("id") is None,
                m.get("id", 0xFFFF),
                m.get("abs", 0xFFFFFFFF),
            ),
        )
        seen_named = set()
        deduped = []
        for mv in moves_sorted:
            aid = mv.get("id")
            if aid is None:
                deduped.append(mv)
                continue
            name = _ANIM_MAP_FOR_GUI.get(aid, f"anim_{aid:02X}")
            if not name.startswith("anim_") and "?" not in name:
                if aid in seen_named:
                    continue
                seen_named.add(aid)
            deduped.append(mv)

        self.moves = deduped
        self.move_to_tree_item: dict[str, dict] = {}

        # stash originals for reset
        self.original_moves: dict[int, dict] = {}
        for mv in self.moves:
            key = mv.get("abs")
            if not key:
                continue
            self.original_moves[key] = {
                "damage": mv.get("damage"),
                "meter": mv.get("meter"),
                "active_start": mv.get("active_start"),
                "active_end": mv.get("active_end"),
                "hitstun": mv.get("hitstun"),
                "blockstun": mv.get("blockstun"),
                "hitstop": mv.get("hitstop"),
                "kb0": mv.get("kb0"),
                "kb1": mv.get("kb1"),
                "kb_traj": mv.get("kb_traj"),
                "hb_off": mv.get("hb_off"),
                "hb_r": mv.get("hb_r"),
            }

        self._build()

    def _build(self):
        cname = self.target_slot.get("char_name", "—")
        self.root = tk.Tk()
        self.root.title(f"Frame Data Editor: {self.slot_label} ({cname})")
        self.root.geometry("1200x700")

        top = tk.Frame(self.root)
        top.pack(side="top", fill="x", padx=5, pady=5)

        if WRITER_AVAILABLE:
            tk.Label(top, text="Double-click to edit. Writes straight to Dolphin.", fg="blue").pack(side="left")
        else:
            tk.Label(top, text="WARNING: move_writer not found. Editing disabled!", fg="red").pack(side="left")

        # reset button
        reset_btn = tk.Button(top, text="Reset to original", command=self._reset_all_moves)
        reset_btn.pack(side="right", padx=4)

        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=5, pady=5)

        cols = (
            "move", "kind", "damage", "meter",
            "startup", "active", "hitstun", "blockstun", "hitstop",
            "advH", "advB", "hb", "kb", "abs",
        )
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", height=30)
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)

        headers = [
            ("move", "Move"),
            ("kind", "Kind"),
            ("damage", "Dmg"),
            ("meter", "Meter"),
            ("startup", "Start"),
            ("active", "Active"),
            ("hitstun", "HS"),
            ("blockstun", "BS"),
            ("hitstop", "Stop"),
            ("advH", "advH"),
            ("advB", "advB"),
            ("hb", "Hitbox"),
            ("kb", "Knockback"),
            ("abs", "Address"),
        ]
        for c, t in headers:
            self.tree.heading(c, text=t)

        self.tree.column("move", width=200, anchor="w")
        self.tree.column("kind", width=60, anchor="w")
        self.tree.column("damage", width=65, anchor="center")
        self.tree.column("meter", width=55, anchor="center")
        self.tree.column("startup", width=55, anchor="center")
        self.tree.column("active", width=90, anchor="center")
        self.tree.column("hitstun", width=45, anchor="center")
        self.tree.column("blockstun", width=45, anchor="center")
        self.tree.column("hitstop", width=50, anchor="center")
        self.tree.column("advH", width=55, anchor="center")
        self.tree.column("advB", width=55, anchor="center")
        self.tree.column("hb", width=95, anchor="center")
        self.tree.column("kb", width=110, anchor="center")
        self.tree.column("abs", width=90, anchor="w")

        # fill rows
        for mv in self.moves:
            aid = mv.get("id")
            move_name = _ANIM_MAP_FOR_GUI.get(aid, f"anim_{aid:02X}") if aid is not None else "anim_--"

            a_s = mv.get("active_start")
            a_e = mv.get("active_end")
            if a_s is not None and a_e is not None:
                active_txt = f"{a_s}-{a_e}"
            elif a_e is not None:
                active_txt = str(a_e)
            else:
                active_txt = ""

            # hitbox discovery
            hb_txt = ""
            move_abs = mv.get("abs")
            if move_abs:
                try:
                    off, val = _probe_hitbox_radius(move_abs)
                except Exception:
                    off, val = (None, None)

                if off is not None:
                    mv["hb_off"] = off
                    mv["hb_r"] = val
                    if val is not None:
                        hb_txt = f"r={val:.1f}"
                    else:
                        hb_txt = ""
            else:
                hb_txt = ""

            # knockback text
            kb0 = mv.get("kb0")
            kb1 = mv.get("kb1")
            kb_traj = mv.get("kb_traj")
            kb_parts = []
            if kb0 is not None:
                kb_parts.append(f"K0:{kb0}")
            if kb1 is not None:
                kb_parts.append(f"K1:{kb1}")
            if kb_traj is not None:
                kb_parts.append(f"T:{kb_traj}")
            kb_txt = " ".join(kb_parts)

            item_id = self.tree.insert(
                "",
                "end",
                values=(
                    move_name,
                    mv.get("kind", ""),
                    "" if mv.get("damage") is None else str(mv.get("damage")),
                    "" if mv.get("meter") is None else str(mv.get("meter")),
                    "" if a_s is None else str(a_s),
                    active_txt,
                    _fmt_stun(mv.get("hitstun")),
                    _fmt_stun(mv.get("blockstun")),
                    "" if mv.get("hitstop") is None else str(mv.get("hitstop")),
                    "" if mv.get("adv_hit") is None else f"{mv.get('adv_hit'):+d}",
                    "" if mv.get("adv_block") is None else f"{mv.get('adv_block'):+d}",
                    hb_txt,
                    kb_txt,
                    f"0x{mv.get('abs', 0):08X}" if mv.get("abs") else "",
                ),
            )
            self.move_to_tree_item[item_id] = mv

        # bindings
        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)

    # --------------------------------------------------------
    # Reset logic
    # --------------------------------------------------------
    def _reset_all_moves(self):
        if not WRITER_AVAILABLE:
            messagebox.showerror("Error", "Writer unavailable")
            return

        for item_id, mv in self.move_to_tree_item.items():
            abs_addr = mv.get("abs")
            if not abs_addr:
                continue
            orig = self.original_moves.get(abs_addr)
            if not orig:
                continue

            # damage
            if orig["damage"] is not None:
                if write_damage(mv, orig["damage"]):
                    self.tree.set(item_id, "damage", str(orig["damage"]))
                    mv["damage"] = orig["damage"]

            # meter
            if orig["meter"] is not None:
                if write_meter(mv, orig["meter"]):
                    self.tree.set(item_id, "meter", str(orig["meter"]))
                    mv["meter"] = orig["meter"]

            # active / startup
            if orig["active_start"] is not None and orig["active_end"] is not None:
                if write_active_frames(mv, orig["active_start"], orig["active_end"]):
                    self.tree.set(item_id, "startup", str(orig["active_start"]))
                    self.tree.set(item_id, "active", f"{orig['active_start']}-{orig['active_end']}")
                    mv["active_start"] = orig["active_start"]
                    mv["active_end"] = orig["active_end"]

            # stuns
            if orig["hitstun"] is not None:
                if write_hitstun(mv, orig["hitstun"]):
                    self.tree.set(item_id, "hitstun", _fmt_stun(orig["hitstun"]))
                    mv["hitstun"] = orig["hitstun"]

            if orig["blockstun"] is not None:
                if write_blockstun(mv, orig["blockstun"]):
                    self.tree.set(item_id, "blockstun", _fmt_stun(orig["blockstun"]))
                    mv["blockstun"] = orig["blockstun"]

            if orig["hitstop"] is not None:
                if write_hitstop(mv, orig["hitstop"]):
                    self.tree.set(item_id, "hitstop", str(orig["hitstop"]))
                    mv["hitstop"] = orig["hitstop"]

            # knockback
            if (orig["kb0"] is not None) or (orig["kb1"] is not None) or (orig["kb_traj"] is not None):
                if write_knockback(mv, orig["kb0"], orig["kb1"], orig["kb_traj"]):
                    parts = []
                    if orig["kb0"] is not None: parts.append(f"K0:{orig['kb0']}")
                    if orig["kb1"] is not None: parts.append(f"K1:{orig['kb1']}")
                    if orig["kb_traj"] is not None: parts.append(f"T:{orig['kb_traj']}")
                    self.tree.set(item_id, "kb", " ".join(parts))
                    mv["kb0"] = orig["kb0"]
                    mv["kb1"] = orig["kb1"]
                    mv["kb_traj"] = orig["kb_traj"]

            # hitbox radius
            if orig["hb_off"] is not None and orig["hb_r"] is not None:
                mv["hb_off"] = orig["hb_off"]
                if write_hitbox_radius(mv, orig["hb_r"]):
                    self.tree.set(item_id, "hb", f"r={orig['hb_r']:.1f}")
                    mv["hb_r"] = orig["hb_r"]

        messagebox.showinfo("Reset", "All moves restored to the values from when you opened this window.")

    # --------------------------------------------------------
    # Cell editing
    # --------------------------------------------------------
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

        if col_name == "damage":
            self._edit_damage(item, mv, current_val)
        elif col_name == "meter":
            self._edit_meter(item, mv, current_val)
        elif col_name == "startup":
            self._edit_startup(item, mv, current_val)
        elif col_name == "active":
            self._edit_active(item, mv, current_val)
        elif col_name == "hitstun":
            self._edit_hitstun(item, mv, current_val)
        elif col_name == "blockstun":
            self._edit_blockstun(item, mv, current_val)
        elif col_name == "hitstop":
            self._edit_hitstop(item, mv, current_val)
        elif col_name == "hb":
            self._edit_hitbox(item, mv, current_val)
        elif col_name == "kb":
            self._edit_knockback(item, mv, current_val)

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
        cur = _unfmt_stun(current) if current else 0
        new_val = simpledialog.askinteger("Edit Hitstun", "New hitstun:", initialvalue=cur, minvalue=0, maxvalue=255)
        if new_val is not None and write_hitstun(mv, new_val):
            self.tree.set(item, "hitstun", _fmt_stun(new_val))
            mv["hitstun"] = new_val

    def _edit_blockstun(self, item, mv, current):
        cur = _unfmt_stun(current) if current else 0
        new_val = simpledialog.askinteger("Edit Blockstun", "New blockstun:", initialvalue=cur, minvalue=0, maxvalue=255)
        if new_val is not None and write_blockstun(mv, new_val):
            self.tree.set(item, "blockstun", _fmt_stun(new_val))
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

    def _edit_hitbox(self, item, mv, current):
        current = current.strip()
        if current.startswith("r="):
            try:
                cur_r = float(current[2:])
            except ValueError:
                cur_r = mv.get("hb_r", 0.0) or 0.0
        else:
            cur_r = mv.get("hb_r", 0.0) or 0.0

        new_val = simpledialog.askfloat("Edit Hitbox Radius", "New radius:", initialvalue=cur_r, minvalue=0.0)
        if new_val is not None and write_hitbox_radius(mv, new_val):
            self.tree.set(item, "hb", f"r={new_val:.1f}")
            mv["hb_r"] = new_val

    def _edit_knockback(self, item, mv, current):
        cur_k0 = mv.get("kb0", 0) or 0
        cur_k1 = mv.get("kb1", 0) or 0
        cur_t  = mv.get("kb_traj", 0) or 0

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Knockback")
        dlg.geometry("280x200")

        tk.Label(dlg, text="Knockback 0:").pack(pady=3)
        k0v = tk.IntVar(value=cur_k0)
        tk.Entry(dlg, textvariable=k0v).pack()

        tk.Label(dlg, text="Knockback 1:").pack(pady=3)
        k1v = tk.IntVar(value=cur_k1)
        tk.Entry(dlg, textvariable=k1v).pack()

        tk.Label(dlg, text="Trajectory:").pack(pady=3)
        tv = tk.IntVar(value=cur_t)
        tk.Entry(dlg, textvariable=tv).pack()

        def on_ok():
            if write_knockback(mv, k0v.get(), k1v.get(), tv.get()):
                self.tree.set(item, "kb", f"K0:{k0v.get()} K1:{k1v.get()} T:{tv.get()}")
                mv["kb0"] = k0v.get()
                mv["kb1"] = k1v.get()
                mv["kb_traj"] = tv.get()
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).pack(pady=8)

    # --------------------------------------------------------
    # right click
    # --------------------------------------------------------
    def _on_right_click(self, event):
        item = self.tree.identify_row(event.y)
        if not item:
            return
        mv = self.move_to_tree_item.get(item)
        if not mv:
            return
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(
            label=f"Copy Address (0x{mv.get('abs', 0):08X})",
            command=lambda: self._copy_address(mv),
        )
        menu.add_command(
            label="View Raw Data",
            command=lambda: self._show_raw_data(mv),
        )
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_address(self, mv):
        addr = mv.get("abs", 0)
        self.root.clipboard_clear()
        self.root.clipboard_append(f"0x{addr:08X}")
        messagebox.showinfo("Copied", f"0x{addr:08X} copied")

    def _show_raw_data(self, mv):
        dlg = tk.Toplevel(self.root)
        dlg.title("Raw Move Data")
        txt = tk.Text(dlg, wrap="word")
        txt.pack(fill="both", expand=True)
        for k, v in mv.items():
            txt.insert("end", f"{k}: {v}\n")
        txt.config(state="disabled")

    def show(self):
        self.root.mainloop()


# ------------------------------------------------------------
# entrypoint for main.py
# ------------------------------------------------------------
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
