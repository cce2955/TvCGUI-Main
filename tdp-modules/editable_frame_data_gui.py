# editable_frame_data_gui.py
#
# Tk window for editing scanned frame data.
# - per-move hitbox radius discovery (like your scan script, but tiny)
# - startup/active auto-fix
# - writes through move_writer
# - exposes open_editable_frame_data_window(...) for main.py

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import threading
import math

# ------------------------------------------------------------
# Writer: we try to import the move_writer you already have
# ------------------------------------------------------------
WRITER_AVAILABLE = False
try:
    from move_writer import (
        write_damage, write_meter, write_active_frames,
        write_hitstun, write_blockstun, write_hitstop,
        write_knockback, write_hitbox_radius, write_attack_property,
    )
    WRITER_AVAILABLE = True
except ImportError:
    print("WARNING: move_writer not found, editing disabled")

# ------------------------------------------------------------
# We also want to read floats from Dolphin to *discover* the HB offset
# ------------------------------------------------------------
try:
    from dolphin_io import rdf32
except ImportError:
    rdf32 = None

# Ryu 5A proved this one:
PRIMARY_HB_OFFSET = 0x21C
# but we’ll probe around it in case some tables shifted
CANDIDATE_HB_OFFSETS = [
    0x21C,
    0x218,
    0x220,
    0x214,
    0x224,
]

# optional anim map, if scan_normals_all is present
try:
    from scan_normals_all import ANIM_MAP as _ANIM_MAP_FOR_GUI
except Exception:
    _ANIM_MAP_FOR_GUI = {}


# ------------------------------------------------------------
# Helpers
# ------------------------------------------------------------


# how far from the move base we’re willing to look for the anchor
HB_SCAN_MAX = 0x200       # bytes
# how far after the anchor we’ll look for the real radius
HB_AFTER_ANCHOR_SCAN = 0x120  # bytes
# fallback if we never find the anchor (Ryu 5A proved this)
FALLBACK_HB_OFFSET = 0x21C

def _probe_hitbox_radius(move_abs: int):
    """
    Pattern-based probe for TvC hitbox radius.

    Memory pattern you showed:
      ... ff ff ff fe  (anchor)
      ... a bunch of 3f 00 00 00
      ... later: 41 a0 00 00 (20.0) or similar 41 xx 00 00

    rdf32(move_abs + off) == None  → very likely the ff ff ff fe dword
    Then we scan forward for a 'good' float.
    """
    if rdf32 is None or not move_abs:
        return None, None

    # 1) find the anchor (the ff ff ff fe, which our rdf32 gives as None)
    anchor_off = None
    for off in range(0, HB_SCAN_MAX, 4):
        try:
            f = rdf32(move_abs + off)
        except Exception:
            # bad read, keep going
            continue

        # this is the important trick: your dump shows ff ff ff fe everywhere here,
        # and earlier our code blew up exactly on that, so treat "None" as the anchor.
        if f is None:
            anchor_off = off
            break

    # no anchor? fall back to the known-good Ryu offset
    if anchor_off is None:
        return FALLBACK_HB_OFFSET, None

    # 2) from just after the anchor, walk forward and grab the first 'hitboxy' float
    start_after = anchor_off + 4
    end_after = anchor_off + HB_AFTER_ANCHOR_SCAN

    best_off = None
    best_val = None

    for off in range(start_after, end_after, 4):
        addr = move_abs + off
        try:
            f = rdf32(addr)
        except Exception:
            continue
        if f is None:
            # sometimes there are more markers, skip
            continue
        if not isinstance(f, (int, float)):
            continue
        if not math.isfinite(f):
            continue

        # 1) if you poked it to something big (43 FF 00 00 → big float) grab it immediately
        if f >= 400.0:
            return off, float(f)

        # 2) normal hitbox range — your normals are around 20.0–21.0, so let’s be generous
        if 1.5 <= f <= 200.0:
            best_off = off
            best_val = float(f)
            break

        # ignore 0.5 / 0.0 / tiny alignment floats

    if best_off is not None:
        return best_off, best_val

    # if we got here, we found the anchor but not the radius — fall back
    return FALLBACK_HB_OFFSET, None


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
# Main window class
# ------------------------------------------------------------
class EditableFrameDataWindow:
    def __init__(self, slot_label, target_slot):
        self.slot_label = slot_label
        self.target_slot = target_slot

        # sort + dedupe like your HUD
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
        self.move_to_tree_item = {}
        self._build()

    def _build(self):
        cname = self.target_slot.get("char_name", "—")
        self.root = tk.Tk()
        self.root.title(f"Frame Data Editor: {self.slot_label} ({cname})")
        self.root.geometry("1200x700")

        top = tk.Frame(self.root)
        top.pack(side="top", fill="x", padx=5, pady=5)
        if WRITER_AVAILABLE:
            tk.Label(top, text="Double-click to edit. Writes straight to Dolphin.", fg="blue").pack()
        else:
            tk.Label(top, text="WARNING: move_writer not found. Editing disabled!", fg="red").pack()

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

        # rows
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

            # dynamic HB discovery
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
                        # we at least know WHERE to write, just don’t show a number
                        hb_txt = ""
                else:
                    hb_txt = ""
            else:
                hb_txt = ""


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

        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)

    # -------------------------------------------------------- events / editors

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
# Entry point used by main.py
# ------------------------------------------------------------
def open_editable_frame_data_window(slot_label, scan_data):
    """Locate the slot in scan_data and open the Tk editor in a thread."""
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
