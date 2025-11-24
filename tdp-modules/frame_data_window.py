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

# Legacy Tk-based viewer (copied from main.py)

def _fmt_stun(v):
    if v is None:
        return ""
    # keep same formatting logic you already had
    if v == 0:
        return "0"
    return str(v)

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

    # widths
    tree.column("move", width=180, anchor="w")
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
    tree.column("abs", width=100, anchor="center")

    # populate rows
    moves = target_slot.get("moves", [])
    for mv in moves:
        move_name = mv.get("move_name", mv.get("move", ""))

        a_s = mv.get("startup")
        a_e = mv.get("active_end")
        if a_s is not None and a_e is not None:
            active_txt = f"{a_s}-{a_e}"
        elif a_e is not None:
            active_txt = str(a_e)
        else:
            active_txt = ""

        # format HB (same logic you had)
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

        tree.insert(
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
                f"0x{mv.get('abs', 0):08X}" if mv.get("abs") else "",
            ),
        )

    root.mainloop()


def open_frame_data_window(slot_label, scan_data):
    """
    Public entry point used by main.py.

    Uses the editable editor when available; otherwise falls back
    to the legacy Tk viewer above.
    """
    if HAVE_EDITABLE_GUI and open_editable_frame_data_window is not None:
        open_editable_frame_data_window(slot_label, scan_data)
        return

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

    t = threading.Thread(
        target=_open_frame_data_window_thread,
        args=(slot_label, target),
        daemon=True,
    )
    t.start()
