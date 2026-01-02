# frame_data_window.py
#
# Router for opening frame data window.
# Preferred order:
#   1) NEW modular FD stack (frame_data_gui -> fd_window)
#   2) OLD editable GUI (editable_frame_data_gui)
#   3) Legacy Tk viewer (implemented here)

# -----------------------------
# Prefer NEW modular FD
# -----------------------------
HAVE_NEW_FD = False
open_new_fd_window = None
NEW_FD_IMPORT_ERROR = None

try:
    # frame_data_gui.py is a thin entrypoint that exports:
    #   open_editable_frame_data_window(slot_label, scan_data)
    from frame_data_gui import open_editable_frame_data_window as open_new_fd_window
    HAVE_NEW_FD = True
except Exception as e:
    HAVE_NEW_FD = False
    open_new_fd_window = None
    NEW_FD_IMPORT_ERROR = e

# -----------------------------
# Fall back to OLD editable GUI
# -----------------------------
HAVE_OLD_EDITABLE = False
open_old_editable_window = None
OLD_EDITABLE_IMPORT_ERROR = None

try:
    from editable_frame_data_gui import open_editable_frame_data_window as open_old_editable_window
    HAVE_OLD_EDITABLE = True
except Exception as e:
    HAVE_OLD_EDITABLE = False
    open_old_editable_window = None
    OLD_EDITABLE_IMPORT_ERROR = e


def _fmt_stun(v):
    if v is None:
        return ""
    if v == 0:
        return "0"
    return str(v)


def _fmt_move_label(mv):
    """
    Always produce a clean move label:
        - use mv["move_name"] when available
        - fallback to anim_XXXX
        - always append hex ID [0xXXXX]
    """
    aid = mv.get("id")
    name = mv.get("move_name")

    if aid is None:
        return "anim_----"

    if not name or name.strip() == "" or name.startswith("anim_--"):
        name = f"anim_{aid:04X}"

    return f"{name} [0x{aid:04X}]"


def _open_legacy_viewer(slot_label, target_slot):
    """
    Legacy Tk-based frame data viewer as last resort.
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        print("tkinter not available")
        return

    cname = target_slot.get("char_name", "—")
    root = tk.Tk()
    root.title(f"Frame data: {slot_label} ({cname})")

    cols = (
        "move", "kind", "damage", "meter",
        "startup", "active", "hitstun", "blockstun", "hitstop",
        "advH", "advB",
        "hb",
        "abs",
    )

    frame = ttk.Frame(root)
    frame.pack(fill="both", expand=True)

    tree = ttk.Treeview(frame, columns=cols, show="headings", height=30)
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)

    tree.grid(row=0, column=0, sticky="nsew")
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
        ("hb", "HB"),
        ("abs", "ABS"),
    ]
    for col_id, txt in headers:
        tree.heading(col_id, text=txt)

    tree.column("move", width=220, anchor="w")
    tree.column("kind", width=60, anchor="w")
    tree.column("damage", width=65, anchor="center")
    tree.column("meter", width=55, anchor="center")
    tree.column("startup", width=55, anchor="center")
    tree.column("active", width=70, anchor="center")
    tree.column("hitstun", width=45, anchor="center")
    tree.column("blockstun", width=45, anchor="center")
    tree.column("hitstop", width=50, anchor="center")
    tree.column("advH", width=55, anchor="center")
    tree.column("advB", width=55, anchor="center")
    tree.column("hb", width=80, anchor="center")
    tree.column("abs", width=110, anchor="center")

    moves = target_slot.get("moves", [])
    moves_sorted = sorted(moves, key=lambda mv: (mv.get("id") is None, mv.get("id") or 0))

    for mv in moves_sorted:
        move_display = _fmt_move_label(mv)

        a_s = mv.get("startup")
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

        adv_hit = mv.get("adv_hit")
        adv_block = mv.get("adv_block")

        tree.insert(
            "",
            "end",
            values=(
                move_display,
                mv.get("kind", ""),
                "" if mv.get("damage") is None else str(mv.get("damage")),
                "" if mv.get("meter") is None else str(mv.get("meter")),
                "" if a_s is None else str(a_s),
                active_txt,
                _fmt_stun(mv.get("hitstun")),
                _fmt_stun(mv.get("blockstun")),
                "" if mv.get("hitstop") is None else str(mv.get("hitstop")),
                "" if adv_hit is None else f"{adv_hit:+d}",
                "" if adv_block is None else f"{adv_block:+d}",
                hb_txt,
                f"0x{mv.get('abs', 0):08X}" if mv.get("abs") else "",
            ),
        )

    root.mainloop()


def open_frame_data_window(slot_label, scan_data):
    """
    Public entry point used by main.py.
    Route to NEW modular FD first, then OLD editable, then legacy.
    """
    # 1) NEW modular FD
    if HAVE_NEW_FD and open_new_fd_window is not None:
        try:
            open_new_fd_window(slot_label, scan_data)
            return
        except Exception as e:
            print("[frame_data_window] NEW FD failed:", repr(e))
            if NEW_FD_IMPORT_ERROR is not None:
                print("[frame_data_window] NEW FD import error:", repr(NEW_FD_IMPORT_ERROR))

    # 2) OLD editable GUI
    if HAVE_OLD_EDITABLE and open_old_editable_window is not None:
        try:
            open_old_editable_window(slot_label, scan_data)
            return
        except Exception as e:
            print("[frame_data_window] OLD editable failed:", repr(e))
            if OLD_EDITABLE_IMPORT_ERROR is not None:
                print("[frame_data_window] OLD editable import error:", repr(OLD_EDITABLE_IMPORT_ERROR))

    # 3) Legacy viewer
    if not scan_data:
        return

    target = None
    for s in scan_data:
        if s.get("slot_label") == slot_label:
            target = s
            break
    if not target:
        return

    _open_legacy_viewer(slot_label, target)
