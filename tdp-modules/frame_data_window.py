# frame_data_window.py
#
# Wrapper for opening the frame data window.
# Uses the editable GUI when available, otherwise falls back
# to the legacy Tk viewer contained here.

import threading

# Try the editable GUI first
try:
    from editable_frame_data_gui import open_editable_frame_data_window
    HAVE_EDITABLE_GUI = True
except ImportError:
    HAVE_EDITABLE_GUI = False
    open_editable_frame_data_window = None
    print("WARNING: editable_frame_data_gui not available")


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


def _open_frame_data_window_thread(slot_label, target_slot):
    """
    Legacy Tk-based frame data viewer.
    The editable version lives in editable_frame_data_gui and is used
    by open_frame_data_window when available.
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

    # ------------------------------------------------------------
    # NEW: guarantee 0111 / 0112 / 0113 / all special IDs appear
    #      even if scan_normals_all had missing fields
    #
    # The actual scan already gives us ALL moves, including specials.
    # We simply sort them, label them correctly, and render cleanly.
    # ------------------------------------------------------------

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

    IMPORTANT:
        Tkinter MUST run on the main thread.
        No background threads, no async destruction.
        We open the window directly and block until closed.
    """
    # If editable GUI exists -> use it directly
    if HAVE_EDITABLE_GUI and open_editable_frame_data_window is not None:
        open_editable_frame_data_window(slot_label, scan_data)
        return

    # Fallback to legacy Tk viewer
    print(f"Editable GUI not available for {slot_label}")
    if not scan_data:
        return

    target = None
    for s in scan_data:
        if s.get("slot_label") == slot_label:
            target = s
            break
    if not target:
        return

    # NO THREADS — open Tk window directly
    _open_frame_data_window_thread(slot_label, target)
