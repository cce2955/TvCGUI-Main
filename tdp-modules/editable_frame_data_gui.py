# editable_frame_data_gui.py
#
# Editable Tk GUI for frame data with live memory writing.
# Will try to import writer from either `move_writer` (preferred) or `writer`.

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import threading

# Try both module names so the GUI does not break if the file is named differently
WRITER_AVAILABLE = False
try:
    from move_writer import (
        write_damage, write_meter, write_active_frames,
        write_hitstun, write_blockstun, write_hitstop,
        write_knockback, write_hitbox_size, write_attack_property,
    )
    WRITER_AVAILABLE = True
except ImportError:
    try:
        from writer import (
            write_damage, write_meter, write_active_frames,
            write_hitstun, write_blockstun, write_hitstop,
            write_knockback, write_hitbox_size, write_attack_property,
        )
        WRITER_AVAILABLE = True
    except ImportError:
        print("WARNING: no move_writer/writer module found, editing disabled")

# ANIM map is optional
try:
    from scan_normals_all import ANIM_MAP as _ANIM_MAP_FOR_GUI
except ImportError:
    _ANIM_MAP_FOR_GUI = {}


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


class EditableFrameDataWindow:
    def __init__(self, slot_label, target_slot):
        self.slot_label = slot_label
        self.target_slot = target_slot
        moves_sorted = sorted(
            target_slot.get("moves", []),
            key=lambda m: (
                m.get("id") is None,
                m.get("id", 0xFFFF),
                m.get("abs", 0xFFFFFFFF),
            ),
        )

        # dedupe by named animation
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
        self._build_window()

    def _build_window(self):
        cname = self.target_slot.get("char_name", "—")
        self.root = tk.Tk()
        self.root.title(f"Frame Data Editor: {self.slot_label} ({cname})")
        self.root.geometry("1200x700")

        info_frame = tk.Frame(self.root)
        info_frame.pack(side="top", fill="x", padx=5, pady=5)

        if WRITER_AVAILABLE:
            info_text = (
                "Double-click to edit. Changes write straight to Dolphin memory. "
                "Right-click a row for options."
            )
            info_fg = "blue"
        else:
            info_text = "WARNING: move_writer module not found. Editing disabled!"
            info_fg = "red"

        info_label = tk.Label(info_frame, text=info_text, fg=info_fg, font=("Arial", 9))
        info_label.pack()

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
        for c, txt in headers:
            self.tree.heading(c, text=txt)

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
        self.tree.column("hb", width=100, anchor="center")
        self.tree.column("kb", width=110, anchor="center")
        self.tree.column("abs", width=95, anchor="w")

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

            hb_x = mv.get("hb_x")
            hb_y = mv.get("hb_y")
            if hb_x is not None or hb_y is not None:
                if hb_x is None:
                    hb_txt = f"-x{hb_y:.1f}"
                elif hb_y is None:
                    hb_txt = f"{hb_x:.1f}x-"
                else:
                    hb_txt = f"{hb_x:.1f}x{hb_y:.1f}"
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

    # ---------------------------------------------------------
    # Event handlers
    # ---------------------------------------------------------

    def _on_double_click(self, event):
        if not WRITER_AVAILABLE:
            messagebox.showerror("Error", "Writer module not available. Cannot edit.")
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
        else:
            messagebox.showinfo("Info", f"Column '{col_name}' is not editable yet.")

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

    # ---------------------------------------------------------
    # Edit helpers
    # ---------------------------------------------------------

    def _edit_damage(self, item, mv, current):
        try:
            cur_int = int(current) if current else 0
        except ValueError:
            cur_int = 0

        new_val = simpledialog.askinteger(
            "Edit Damage",
            f"Enter new damage value (current {cur_int}):",
            initialvalue=cur_int,
            minvalue=0,
            maxvalue=999999,
        )
        if new_val is not None and write_damage(mv, new_val):
            self.tree.set(item, "damage", str(new_val))
            mv["damage"] = new_val

    def _edit_meter(self, item, mv, current):
        try:
            cur_int = int(current) if current else 0
        except ValueError:
            cur_int = 0

        new_val = simpledialog.askinteger(
            "Edit Meter",
            f"Enter new meter cost (current {cur_int}):",
            initialvalue=cur_int,
            minvalue=0,
            maxvalue=255,
        )
        if new_val is not None and write_meter(mv, new_val):
            self.tree.set(item, "meter", str(new_val))
            mv["meter"] = new_val

    def _edit_startup(self, item, mv, current):
        try:
            cur_int = int(current) if current else 1
        except ValueError:
            cur_int = 1

        new_val = simpledialog.askinteger(
            "Edit Startup",
            f"Enter new startup frame (current {cur_int}):",
            initialvalue=cur_int,
            minvalue=1,
            maxvalue=255,
        )
        if new_val is not None:
            a_e = mv.get("active_end", new_val + 1)
            if write_active_frames(mv, new_val, a_e):
                self.tree.set(item, "startup", str(new_val))
                self.tree.set(item, "active", f"{new_val}-{a_e}")
                mv["active_start"] = new_val
                mv["active_end"] = a_e

    def _edit_active(self, item, mv, current):
        current = current.strip()
        if "-" in current:
            parts = current.split("-")
            try:
                cur_start = int(parts[0])
                cur_end = int(parts[1])
            except ValueError:
                cur_start, cur_end = 1, 2
        else:
            cur_start = mv.get("active_start", 1)
            cur_end = mv.get("active_end", cur_start + 1)

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Active Frames")
        dlg.geometry("280x160")

        tk.Label(dlg, text="Active Start:").pack(pady=3)
        sv = tk.IntVar(value=cur_start)
        tk.Entry(dlg, textvariable=sv).pack()

        tk.Label(dlg, text="Active End:").pack(pady=3)
        ev = tk.IntVar(value=cur_end)
        tk.Entry(dlg, textvariable=ev).pack()

        def on_ok():
            new_start = sv.get()
            new_end = ev.get()
            if write_active_frames(mv, new_start, new_end):
                self.tree.set(item, "startup", str(new_start))
                self.tree.set(item, "active", f"{new_start}-{new_end}")
                mv["active_start"] = new_start
                mv["active_end"] = new_end
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).pack(pady=8)

    def _edit_hitstun(self, item, mv, current):
        cur_int = _unfmt_stun(current) if current else 0
        new_val = simpledialog.askinteger(
            "Edit Hitstun",
            f"Enter new hitstun (current {current}):",
            initialvalue=cur_int,
            minvalue=0,
            maxvalue=255,
        )
        if new_val is not None and write_hitstun(mv, new_val):
            self.tree.set(item, "hitstun", _fmt_stun(new_val))
            mv["hitstun"] = new_val

    def _edit_blockstun(self, item, mv, current):
        cur_int = _unfmt_stun(current) if current else 0
        new_val = simpledialog.askinteger(
            "Edit Blockstun",
            f"Enter new blockstun (current {current}):",
            initialvalue=cur_int,
            minvalue=0,
            maxvalue=255,
        )
        if new_val is not None and write_blockstun(mv, new_val):
            self.tree.set(item, "blockstun", _fmt_stun(new_val))
            mv["blockstun"] = new_val

    def _edit_hitstop(self, item, mv, current):
        try:
            cur_int = int(current) if current else 0
        except ValueError:
            cur_int = 0
        new_val = simpledialog.askinteger(
            "Edit Hitstop",
            f"Enter new hitstop (current {cur_int}):",
            initialvalue=cur_int,
            minvalue=0,
            maxvalue=255,
        )
        if new_val is not None and write_hitstop(mv, new_val):
            self.tree.set(item, "hitstop", str(new_val))
            mv["hitstop"] = new_val

    def _edit_hitbox(self, item, mv, current):
        current = current.strip()
        if "x" in current:
            parts = current.split("x")
            try:
                cur_x = float(parts[0]) if parts[0] and parts[0] != "-" else 0.0
                cur_y = float(parts[1]) if parts[1] and parts[1] != "-" else 0.0
            except ValueError:
                cur_x, cur_y = 0.0, 0.0
        else:
            cur_x = mv.get("hb_x", 0.0) or 0.0
            cur_y = mv.get("hb_y", 0.0) or 0.0

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Hitbox")
        dlg.geometry("280x160")

        tk.Label(dlg, text="Hitbox X:").pack(pady=3)
        xv = tk.DoubleVar(value=cur_x)
        tk.Entry(dlg, textvariable=xv).pack()

        tk.Label(dlg, text="Hitbox Y:").pack(pady=3)
        yv = tk.DoubleVar(value=cur_y)
        tk.Entry(dlg, textvariable=yv).pack()

        def on_ok():
            new_x = xv.get()
            new_y = yv.get()
            if write_hitbox_size(mv, new_x, new_y):
                self.tree.set(item, "hb", f"{new_x:.1f}x{new_y:.1f}")
                mv["hb_x"] = new_x
                mv["hb_y"] = new_y
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).pack(pady=8)

    def _edit_knockback(self, item, mv, current):
        cur_kb0 = mv.get("kb0", 0) or 0
        cur_kb1 = mv.get("kb1", 0) or 0
        cur_traj = mv.get("kb_traj", 0) or 0

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Knockback")
        dlg.geometry("280x200")

        tk.Label(dlg, text="Knockback 0:").pack(pady=3)
        k0v = tk.IntVar(value=cur_kb0)
        tk.Entry(dlg, textvariable=k0v).pack()

        tk.Label(dlg, text="Knockback 1:").pack(pady=3)
        k1v = tk.IntVar(value=cur_kb1)
        tk.Entry(dlg, textvariable=k1v).pack()

        tk.Label(dlg, text="Trajectory:").pack(pady=3)
        tv = tk.IntVar(value=cur_traj)
        tk.Entry(dlg, textvariable=tv).pack()

        def on_ok():
            new_k0 = k0v.get()
            new_k1 = k1v.get()
            new_t = tv.get()
            if write_knockback(mv, new_k0, new_k1, new_t):
                bits = []
                bits.append(f"K0:{new_k0}")
                bits.append(f"K1:{new_k1}")
                bits.append(f"T:{new_t}")
                self.tree.set(item, "kb", " ".join(bits))
                mv["kb0"] = new_k0
                mv["kb1"] = new_k1
                mv["kb_traj"] = new_t
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).pack(pady=8)

    def _copy_address(self, mv):
        addr = mv.get("abs", 0)
        self.root.clipboard_clear()
        self.root.clipboard_append(f"0x{addr:08X}")
        messagebox.showinfo("Copied", f"Copied 0x{addr:08X}")

    def _show_raw_data(self, mv):
        dlg = tk.Toplevel(self.root)
        dlg.title("Raw Move Data")
        dlg.geometry("460x520")

        txt = tk.Text(dlg, wrap="word")
        txt.pack(fill="both", expand=True)

        lines = [f"{k}: {v}" for k, v in mv.items()]
        txt.insert("1.0", "\n".join(lines))
        txt.config(state="disabled")

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
