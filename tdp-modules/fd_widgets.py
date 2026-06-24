# fd_widgets.py
#
# Small reusable Tk widgets/dialogs to keep fd_window slim.

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
from pathlib import Path
import os
import sys


class Tooltip:
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text or ""
        self.tip = None
        widget.bind("<Enter>", self._show, add=True)
        widget.bind("<Leave>", self._hide, add=True)

    def _show(self, _evt=None):
        if self.tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 10
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self.tip = tk.Toplevel(self.widget)
        self.tip.overrideredirect(True)
        self.tip.geometry(f"+{x}+{y}")
        frame = tk.Frame(self.tip, bg="#1e1e1e", bd=1, relief="solid")
        frame.pack(fill="both", expand=True)
        lbl = tk.Label(
            frame,
            text=self.text,
            bg="#1e1e1e",
            fg="#e8e8e8",
            justify="left",
            font=("Segoe UI", 9),
            padx=8,
            pady=6,
        )
        lbl.pack()

    def _hide(self, _evt=None):
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


_APP_ICON_IMAGE = None

def _candidate_icon_paths() -> list[Path]:
    paths: list[Path] = []
    try:
        here = Path(__file__).resolve().parent
    except Exception:
        here = Path.cwd()
    try:
        if getattr(sys, "frozen", False):
            root = Path(sys.executable).resolve().parent
        else:
            root = here
    except Exception:
        root = here

    # Preferred app logo locations first.
    for p in [
        root / "assets" / "icon.png",
        here / "assets" / "icon.png",
        root / "assets" / "portraits" / "Placeholder.png",
        here / "assets" / "portraits" / "Placeholder.png",
        here / "app_title_icon.png",
        root / "app_title_icon.png",
    ]:
        if p not in paths:
            paths.append(p)
    return paths


def apply_titlebar_icon(win, parent=None):
    """Apply the preferred bundled app icon/logo to window title bars."""
    global _APP_ICON_IMAGE
    try:
        if _APP_ICON_IMAGE is None:
            for icon_path in _candidate_icon_paths():
                try:
                    if icon_path.exists():
                        _APP_ICON_IMAGE = tk.PhotoImage(file=str(icon_path))
                        break
                except Exception:
                    continue
        if _APP_ICON_IMAGE is not None:
            win.iconphoto(True, _APP_ICON_IMAGE)
    except Exception:
        try:
            if parent is not None:
                img = getattr(parent, "_app_title_icon_image", None)
                if img is not None:
                    win.iconphoto(True, img)
        except Exception:
            pass
    try:
        if _APP_ICON_IMAGE is not None:
            setattr(win, "_app_title_icon_image", _APP_ICON_IMAGE)
    except Exception:
        pass


def configure_light_dialog(dlg, parent=None, *, width: int | None = None, height: int | None = None, resizable=(False, False)):
    apply_titlebar_icon(dlg, parent)
    try:
        dlg.configure(bg="#F3F4F7")
    except Exception:
        pass
    try:
        dlg.resizable(bool(resizable[0]), bool(resizable[1]))
    except Exception:
        pass
    # Do not force a fixed geometry before the dialog's widgets exist; that can
    # clip OK/Cancel rows on systems with different DPI/text metrics.  We keep
    # the desired size as a minimum and finalize after layout instead.
    try:
        if width or height:
            dlg.minsize(int(width or 0), int(height or 0))
    except Exception:
        pass
    try:
        dlg.transient(parent)
    except Exception:
        pass
    try:
        dlg.grab_set()
    except Exception:
        pass
    return dlg




def finalize_dialog_geometry(dlg, min_w: int = 0, min_h: int = 0, pad_w: int = 18, pad_h: int = 18):
    """Resize light dialogs after widgets are packed so buttons never end up clipped."""
    try:
        dlg.update_idletasks()
        req_w = int(dlg.winfo_reqwidth()) + int(max(0, pad_w))
        req_h = int(dlg.winfo_reqheight()) + int(max(0, pad_h))
        final_w = max(int(min_w or 0), req_w)
        final_h = max(int(min_h or 0), req_h)
        if final_w > 0 and final_h > 0:
            dlg.geometry(f"{final_w}x{final_h}")
            try:
                dlg.minsize(final_w, final_h)
            except Exception:
                pass
    except Exception:
        pass


_NATIVE_MESSAGEBOX_FUNCS = {
    "showerror": messagebox.showerror,
    "showwarning": messagebox.showwarning,
    "showinfo": messagebox.showinfo,
    "askyesno": messagebox.askyesno,
    "askokcancel": messagebox.askokcancel,
    "askquestion": messagebox.askquestion,
}


def _resolve_dialog_parent(parent=None):
    try:
        if parent is not None and bool(parent.winfo_exists()):
            return parent
    except Exception:
        pass
    try:
        root = tk._default_root
        if root is not None and bool(root.winfo_exists()):
            return root
    except Exception:
        pass
    return None


def _styled_notice(title, message, *, parent=None, kind="info", buttons=("OK",), default=None):
    """Modal app-styled substitute for Tk's plain native message boxes."""
    host = _resolve_dialog_parent(parent)
    try:
        dlg = tk.Toplevel(host) if host is not None else tk.Toplevel()
    except Exception:
        # Keep the app functional if a modal cannot be constructed during shutdown.
        native = _NATIVE_MESSAGEBOX_FUNCS.get("showerror" if kind == "error" else "showwarning" if kind == "warning" else "showinfo")
        return native(title, message, parent=host) if native else None
    dlg.title(str(title or "TvC Mapping"))
    apply_titlebar_icon(dlg, host)
    dlg.configure(bg="#F3F4F7")
    dlg.resizable(False, False)
    if host is not None:
        try:
            dlg.transient(host)
        except Exception:
            pass

    result = {"value": None}
    palette = {
        "error": ("#A13E4A", "#FFF2F3", "Error"),
        "warning": ("#A56E20", "#FFF7E4", "Warning"),
        "info": ("#356A9C", "#EEF7FF", "Notice"),
        "question": ("#4A668A", "#F0F6FF", "Confirm"),
    }
    rail, rail_text, eyebrow = palette.get(str(kind), palette["info"])

    outer = tk.Frame(dlg, bg="#F3F4F7")
    outer.pack(fill="both", expand=True)
    tk.Frame(outer, bg=rail, height=5).pack(fill="x")
    body = tk.Frame(outer, bg="#F3F4F7")
    body.pack(fill="both", expand=True, padx=18, pady=14)

    tk.Label(body, text=eyebrow.upper(), bg="#F3F4F7", fg=rail, font=("Segoe UI Semibold", 8)).pack(anchor="w")
    tk.Label(body, text=str(title or "TvC Mapping"), bg="#F3F4F7", fg="#111722", font=("Segoe UI Semibold", 13)).pack(anchor="w", pady=(2, 8))
    tk.Label(body, text=str(message or ""), bg="#F3F4F7", fg="#4B5666", font=("Segoe UI", 10), wraplength=520, justify="left").pack(anchor="w")

    btn_row = tk.Frame(body, bg="#F3F4F7")
    btn_row.pack(fill="x", pady=(16, 0))

    def close_with(value):
        result["value"] = value
        try:
            dlg.destroy()
        except Exception:
            pass

    button_values = tuple(buttons or ("OK",))
    for label in reversed(button_values):
        is_primary = label in {"OK", "Yes"}
        btn = ttk.Button(btn_row, text=label, command=lambda v=label: close_with(v))
        btn.pack(side="right", padx=(6, 0))
        if is_primary:
            try:
                btn.focus_set()
            except Exception:
                pass
    try:
        dlg.protocol("WM_DELETE_WINDOW", lambda: close_with("No" if "No" in button_values else "Cancel" if "Cancel" in button_values else "OK"))
        dlg.bind("<Escape>", lambda _e: close_with("No" if "No" in button_values else "Cancel" if "Cancel" in button_values else "OK"))
        if "Yes" in button_values:
            dlg.bind("<Return>", lambda _e: close_with("Yes"))
        else:
            dlg.bind("<Return>", lambda _e: close_with("OK"))
    except Exception:
        pass
    finalize_dialog_geometry(dlg, 420, 180, pad_w=28, pad_h=24)
    try:
        dlg.grab_set()
        dlg.wait_window()
    except Exception:
        pass
    return result["value"]


def _styled_showerror(title=None, message=None, **kwargs):
    _styled_notice(title, message, parent=kwargs.get("parent"), kind="error")
    return "ok"


def _styled_showwarning(title=None, message=None, **kwargs):
    _styled_notice(title, message, parent=kwargs.get("parent"), kind="warning")
    return "ok"


def _styled_showinfo(title=None, message=None, **kwargs):
    _styled_notice(title, message, parent=kwargs.get("parent"), kind="info")
    return "ok"


def _styled_askyesno(title=None, message=None, **kwargs):
    return _styled_notice(title, message, parent=kwargs.get("parent"), kind="question", buttons=("Yes", "No")) == "Yes"


def _styled_askokcancel(title=None, message=None, **kwargs):
    return _styled_notice(title, message, parent=kwargs.get("parent"), kind="question", buttons=("OK", "Cancel")) == "OK"


def _styled_askquestion(title=None, message=None, **kwargs):
    return "yes" if _styled_askyesno(title, message, **kwargs) else "no"


def install_styled_messageboxes():
    """Patch tkinter.messagebox once; all existing module imports share this object."""
    if getattr(messagebox, "_tvc_mapping_styled", False):
        return
    try:
        messagebox.showerror = _styled_showerror
        messagebox.showwarning = _styled_showwarning
        messagebox.showinfo = _styled_showinfo
        messagebox.askyesno = _styled_askyesno
        messagebox.askokcancel = _styled_askokcancel
        messagebox.askquestion = _styled_askquestion
        messagebox._tvc_mapping_styled = True
    except Exception:
        pass


install_styled_messageboxes()

def build_dialog_shell(dlg, *, title_text: str, help_text: str = "", current_text: str = "", address: int | None = None, wrap: int = 500):
    outer = tk.Frame(dlg, bg="#F3F4F7")
    outer.pack(fill="both", expand=True, padx=12, pady=10)
    tk.Label(outer, text=title_text, bg="#F3F4F7", fg="#111111", font=("Segoe UI", 13, "bold")).pack(pady=(0, 8))
    if help_text:
        tk.Label(outer, text=help_text, bg="#F3F4F7", fg="#6E747E", wraplength=wrap, justify="left").pack(anchor="w", pady=(0, 6))
    if current_text:
        tk.Label(outer, text=current_text, bg="#F3F4F7", fg="#1E5EFF", font=("Segoe UI", 10)).pack(pady=(0, 8))
    if address is not None:
        tk.Label(outer, text=f"Address: 0x{int(address):08X}", bg="#F3F4F7", fg="#6E747E", font=("Segoe UI", 9)).pack(anchor="w", pady=(0, 8))
    return outer


def make_list_picker(parent, *, items: list[str], height: int = 12):
    frame = tk.Frame(parent, bg="#F3F4F7")
    frame.pack(fill="both", expand=True, pady=(6, 8))
    list_frame = tk.Frame(frame, bg="#FFFFFF", bd=1, relief="solid")
    list_frame.pack(fill="both", expand=True)
    sb = tk.Scrollbar(list_frame)
    sb.pack(side="right", fill="y")
    lb = tk.Listbox(list_frame, yscrollcommand=sb.set, height=height, activestyle="none", bd=0, highlightthickness=0, font=("Segoe UI", 10))
    lb.pack(side="left", fill="both", expand=True)
    sb.config(command=lb.yview)
    for line in items:
        lb.insert("end", line)
    return frame, lb


FIELD_HELP = {
    "move": (
        "Move / animation ID. Double-click to replace the animation or open manual animation ID tools. "
        "This changes which move record or animation the row points at; it is separate from damage, stun, and physics values."
    ),
    "kind": "Scanner classification for this row. It is display-only and helps separate normals, specials, supers, and unknown rows.",
    "hits": "Detected per-hit bundles for this move. Expand a multi-hit row to view and edit each hit separately.",
    "link": "Display-only family link. Shows when separate table sections appear to be pieces of one player-facing move, such as Tatsu Start / Spin / End.",
    "damage": "Base damage dealt by this move or hit. Higher values increase raw damage; it does not change hitstun or knockback by itself.",
    "meter": "Meter gain/base meter value attached to the hit. This changes resource behavior, not damage or hit reaction.",
    "startup": "First active frame. Lower values make the hitbox become active sooner; this also rewrites the start of the active range.",
    "active": "Main active frame range, shown as start-end. Extending the end keeps the hit active longer; moving the start changes startup.",
    "active2": "Second active window when the move has one. Use this for moves with a later/reappearing hitbox instead of changing the main active range.",
    "hitstun": "Frames the opponent stays in hit reaction after getting hit. More hitstun usually gives more combo time.",
    "blockstun": "Frames the opponent stays locked after blocking. More blockstun improves block advantage and pressure.",
    "hitstop": (
        "UNVERIFIED LABEL: this field is currently called Hitstop, but that has not been confirmed in-game yet. "
        "It may control impact freeze/pause frames or another hit-timing behavior. Treat it as experimental: change one value at a time and verify the result in-game."
    ),
    "hit_spark": "Hit spark/effect number. User-tested on Ryu 5A: changing this can change the spark type and sometimes the spark location.",
    "stretch_part": "Which body/limb slot the reach stretch packet targets. Change this to test what part gets stretched.",
    "stretch_len": "Reach length scale. Higher values can create longer Dhalsim-style limb reach when the stretch packet is active.",
    "stretch_width": "Reach width/side scale for the stretch packet. Use this to test the second stretch axis.",
    "stretch_height": "Reach height/depth scale for the stretch packet. Use this to test the third stretch axis.",
    "stretch_time": "Stretch timing/slot value. This may change when or how the stretch packet applies.",
    "post_link": "Dangerous post-animation script link. Changing it can freeze the character after the animation; use small tests and restore if needed.",
    "kb_type": (
        "Knockback style number. 9 means normal knockback. Other values are experimental and may route to different knockback behavior."
    ),
    "launch_profile": (
        "Extra Launch. 0 means normal knockback. Any value above 0 turns on extra launch behavior and makes Launch Adjust matter."
    ),
    "kb_unknown": (
        "Launch Adjust. Mostly ignored when Extra Launch is 0. When Extra Launch is above 0, this can change launch speed, direction, curve, or how KB X / Arc are interpreted."
    ),
    "kb_x": "Grounded or standing horizontal knockback. Larger positive values push/launch farther; zero removes most horizontal push.",
    "air_kb": "Airborne or vertical arc value. Higher values usually give a higher/longer relaunch arc; negative values can drive downward arcs.",
    "speed_mod": "Move speed byte. 100 / 0x64 is the normal baseline; lower/higher values can slow down or speed up the move behavior.",
    "invuln": "Protection-phase candidates. [C] = this exact action is runtime-confirmed invulnerable; [H] = same +0x58-bit-0 plus phase-setup topology as the confirmed references, but untested; [M]/[L] are weaker evidence. The scanner suppresses only the exact normal bootstrap (clear +0x1218 -> 2f -> action handoff). 999 is retained as raw event-held data and is not displayed as a frame count.",
    "attack_property": "Attack property byte. Use the known-value dropdown for confirmed properties; manual entry is for testing unknown bytes.",
    "hit_reaction": "The reaction state/animation chosen on hit, such as standing hitstun, low hitstun, overhead hitstun, knockdown, crumple, or special reactions.",
    "hit_result_flags": "OTG toggle written to fighter +0x240 after the 0x80042F00 clear mask. User-verified: 0x00000000 = OTG off, 0x00004000 = OTG on. Values 0x00004100 and above enter reaction/knockdown families and are kept as manual/custom testing values.",
    "superbg": "Super background flag. Toggles whether the super-style background effect is enabled for this move when the signature is found.",
    "abs": "Absolute move-table address for this row. This is for lookup/debugging and address tools; it is not directly edited as frame data.",
    "combo_kb_mod": "Combo knockback modifier byte when the signature is found. Useful for testing combo scaling/knockback behavior.",
    "proj_dmg": "Projectile damage value when a linked projectile template is found. Use this for projectile hits instead of the main damage cell.",
    "proj_ps_card_type": "Compact projectile-super card type. 0x0023 and 0x0123 are the card families found on Volnutt, Morrigan, Tekkaman, and Casshan projectile supers. Display-only.",
    "proj_ps_lifetime": "Projectile-super card life/active time. Higher values keep this card active longer.",
    "proj_ps_hit_count": "Projectile-super card hit count or emission limit. This can cap how many hits this card emits.",
    "proj_ps_mode": "Projectile-super card mode/style word. Case-by-case; useful for farming unknown card behavior.",
    "proj_ps_emit_count": "Secondary emit/count value for compact projectile-super cards. Often controls how many pulses or pieces are emitted.",
    "proj_ps_interval": "Hit interval/spacing for compact projectile-super cards when present.",
    "proj_ps_offset_x": "Spawn or motion X value for compact projectile-super cards. On known cards this behaves like source offset or travel component.",
    "proj_ps_offset_y": "Spawn or motion Y value for compact projectile-super cards. On known cards this behaves like source offset or travel component.",
    "proj_ps_scale": "Scale/radius-style value for compact projectile-super cards, often 1.0 on stock cards.",
    "proj_ps_particle_fx": "Particle/effect id for compact projectile-super cards. Farm nearby values to build friendly dropdown names.",
    "proj_ps_projectile_id": "Projectile/object id for compact projectile-super cards.",
    "proj_ps_spawn_bone": "Spawn bone/source selector for compact projectile-super cards when present.",
    "hb_main": "Primary hitbox radius. Bigger values increase reach/coverage; smaller values shrink the detected hit area.",
    "hb": "All detected hitbox radius candidates for the row. r0 is usually the main candidate, but some moves expose several radii.",
}


def get_field_help(field: str, fallback: str = "") -> str:
    return FIELD_HELP.get(field, fallback)


HIT_RESULT_FLAG_PRESETS = [
    ("Manual / keep typed value", None),
    ("OTG off / stock simple hit (0x00000000)", 0x00000000),
    ("OTG on / clean toggle (0x00004000)", 0x00004000),
]


def _parse_u32_text(raw: str) -> int:
    raw = (raw or "").strip().replace("_", "")
    if not raw:
        return 0
    return int(raw, 16) if raw.lower().startswith("0x") else int(raw, 10)


def ask_hit_result_flags_with_presets(
    parent,
    *,
    title: str = "Edit Hit Result Flags",
    help_text: str = "",
    initialvalue: int = 0,
    address: int | None = None,
) -> int | None:
    """Hit-result flag editor with a clickable list on top and manual entry below."""
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    configure_light_dialog(dlg, parent, width=560, height=470)

    result = {"value": None}
    outer = build_dialog_shell(
        dlg,
        title_text="Hit Result Flags",
        help_text=help_text or "Choose a known OTG / reaction flag from the list, or type a manual hex / decimal value below.",
        current_text=f"Current: 0x{int(initialvalue) & 0xFFFFFFFF:08X}",
        address=address,
        wrap=520,
    )

    tk.Label(outer, text="Common values:", bg="#F3F4F7", fg="#111111", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 4))
    picker_frame, listbox = make_list_picker(outer, items=[f"0x{int(v or 0) & 0xFFFFFFFF:08X}: {label}" for label, v in HIT_RESULT_FLAG_PRESETS if v is not None], height=6)
    value_var = tk.StringVar(master=dlg, value=f"0x{int(initialvalue) & 0xFFFFFFFF:08X}")

    def set_manual(v: int):
        value_var.set(f"0x{int(v) & 0xFFFFFFFF:08X}")

    known_vals = [v for _label, v in HIT_RESULT_FLAG_PRESETS if v is not None]

    def on_select(_evt=None):
        sel = listbox.curselection()
        if sel:
            set_manual(known_vals[int(sel[0])])

    listbox.bind("<<ListboxSelect>>", on_select)

    tk.Label(outer, text="Manual entry:", bg="#F3F4F7", fg="#111111", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(6, 2))
    entry_row = tk.Frame(outer, bg="#F3F4F7")
    entry_row.pack(anchor="w", fill="x")
    ent = ttk.Entry(entry_row, textvariable=value_var, width=18)
    ent.pack(side="left")
    ttk.Button(entry_row, text="OTG off", command=lambda: set_manual(0x00000000)).pack(side="left", padx=(8, 0))
    ttk.Button(entry_row, text="OTG on", command=lambda: set_manual(0x00004000)).pack(side="left", padx=(6, 0))
    ttk.Button(entry_row, text="Current", command=lambda: set_manual(int(initialvalue) & 0xFFFFFFFF)).pack(side="left", padx=(6, 0))

    btns = tk.Frame(outer, bg="#F3F4F7")
    btns.pack(fill="x", pady=(12, 0))

    def apply_value():
        try:
            val = _parse_u32_text(value_var.get()) & 0xFFFFFFFF
        except Exception:
            messagebox.showerror(title, "Invalid integer value. Use decimal or hex like 0x4000.", parent=dlg)
            return
        result["value"] = val
        dlg.destroy()

    ttk.Button(btns, text="OK", command=apply_value).pack(side="right", padx=(6, 0))
    ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right")
    finalize_dialog_geometry(dlg, 560, 470)
    finalize_dialog_geometry(dlg, 500, 260)
    finalize_dialog_geometry(dlg, 500, 240)
    ent.focus_set(); ent.selection_range(0, "end")
    dlg.bind("<Return>", lambda _e: apply_value())
    dlg.bind("<Escape>", lambda _e: dlg.destroy())
    try:
        parent.wait_window(dlg)
    except Exception:
        pass
    return result["value"]


def ask_integer_with_help(
    parent,
    *,
    title: str,
    prompt: str,
    help_text: str,
    initialvalue: int = 0,
    minvalue: int | None = None,
    maxvalue: int | None = None,
    address: int | None = None,
) -> int | None:
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    configure_light_dialog(dlg, parent, width=500, height=260)
    result = {"value": None}
    outer = build_dialog_shell(
        dlg,
        title_text=prompt,
        help_text=help_text,
        current_text=f"Current: {int(initialvalue)}",
        address=address,
        wrap=450,
    )
    tk.Label(outer, text="Manual entry:", bg="#F3F4F7", fg="#111111", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(8, 2))
    row = tk.Frame(outer, bg="#F3F4F7")
    row.pack(anchor="w", fill="x")
    var = tk.StringVar(master=dlg, value=str(int(initialvalue)))
    ent = ttk.Entry(row, textvariable=var, width=16)
    ent.pack(side="left")
    bounds = []
    if minvalue is not None:
        bounds.append(f"min {minvalue}")
    if maxvalue is not None:
        bounds.append(f"max {maxvalue}")
    if bounds:
        tk.Label(outer, text="Allowed: " + ", ".join(bounds), bg="#F3F4F7", fg="#6E747E", font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 0))
    btns = tk.Frame(outer, bg="#F3F4F7")
    btns.pack(fill="x", pady=(14, 0))
    def apply_value():
        try:
            raw = (var.get() or "").strip()
            val = int(raw, 16) if raw.lower().startswith("0x") else int(raw, 10)
        except Exception:
            messagebox.showerror(title, "Invalid integer value.", parent=dlg)
            return
        if minvalue is not None and val < minvalue:
            messagebox.showerror(title, f"Value must be at least {minvalue}.", parent=dlg); return
        if maxvalue is not None and val > maxvalue:
            messagebox.showerror(title, f"Value must be no more than {maxvalue}.", parent=dlg); return
        result["value"] = val
        dlg.destroy()
    ttk.Button(btns, text="OK", command=apply_value).pack(side="right", padx=(6, 0))
    ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right")
    ent.focus_set(); ent.selection_range(0, "end")
    dlg.bind("<Return>", lambda _e: apply_value())
    dlg.bind("<Escape>", lambda _e: dlg.destroy())
    try:
        parent.wait_window(dlg)
    except Exception:
        pass
    return result["value"]


def ask_float_with_help(
    parent,
    *,
    title: str,
    prompt: str,
    help_text: str,
    initialvalue: float = 0.0,
    minvalue: float | None = None,
    maxvalue: float | None = None,
) -> float | None:
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    configure_light_dialog(dlg, parent, width=500, height=240)
    result = {"value": None}
    outer = build_dialog_shell(
        dlg,
        title_text=prompt,
        help_text=help_text,
        current_text=f"Current: {float(initialvalue):.6g}",
        wrap=450,
    )
    tk.Label(outer, text="Manual entry:", bg="#F3F4F7", fg="#111111", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(8, 2))
    row = tk.Frame(outer, bg="#F3F4F7")
    row.pack(anchor="w", fill="x")
    var = tk.StringVar(master=dlg, value=f"{float(initialvalue):.6g}")
    ent = ttk.Entry(row, textvariable=var, width=16)
    ent.pack(side="left")
    btns = tk.Frame(outer, bg="#F3F4F7")
    btns.pack(fill="x", pady=(14, 0))
    def apply_value():
        try:
            val = float((var.get() or "").strip())
        except Exception:
            messagebox.showerror(title, "Invalid number value.", parent=dlg); return
        if minvalue is not None and val < minvalue:
            messagebox.showerror(title, f"Value must be at least {minvalue}.", parent=dlg); return
        if maxvalue is not None and val > maxvalue:
            messagebox.showerror(title, f"Value must be no more than {maxvalue}.", parent=dlg); return
        result["value"] = val
        dlg.destroy()
    ttk.Button(btns, text="OK", command=apply_value).pack(side="right", padx=(6, 0))
    ttk.Button(btns, text="Cancel", command=dlg.destroy).pack(side="right")
    ent.focus_set(); ent.selection_range(0, "end")
    dlg.bind("<Return>", lambda _e: apply_value())
    dlg.bind("<Escape>", lambda _e: dlg.destroy())
    try:
        parent.wait_window(dlg)
    except Exception:
        pass
    return result["value"]


class ManualAnimIDDialog(simpledialog.Dialog):
    def __init__(self, parent, cur_hi=None, cur_lo=None):
        self.cur_hi = cur_hi
        self.cur_lo = cur_lo
        self.result = None
        self.parent = parent
        super().__init__(parent, title="Manual Anim ID (HI / LO)")

    def body(self, master):
        try:
            configure_light_dialog(self, self.parent, width=360, height=210)
        except Exception:
            pass
        master.configure(bg="#F3F4F7")
        ttk.Label(master, text="High byte (HI):").grid(row=0, column=0, sticky="e", padx=6, pady=4)
        ttk.Label(master, text="Low byte (LO):").grid(row=1, column=0, sticky="e", padx=6, pady=4)

        self.hi_var = tk.StringVar(value=f"{self.cur_hi:02X}" if self.cur_hi is not None else "")
        self.lo_var = tk.StringVar(value=f"{self.cur_lo:02X}" if self.cur_lo is not None else "")

        self.hi_entry = ttk.Entry(master, width=6, textvariable=self.hi_var)
        self.lo_entry = ttk.Entry(master, width=6, textvariable=self.lo_var)

        self.hi_entry.grid(row=0, column=1, padx=6, pady=4)
        self.lo_entry.grid(row=1, column=1, padx=6, pady=4)

        ttk.Label(master, text="Hex (00-FF)").grid(row=0, column=2, rowspan=2, padx=6)

        return self.hi_entry

    def validate(self):
        try:
            hi = int(self.hi_var.get(), 16)
            lo = int(self.lo_var.get(), 16)
            if not (0 <= hi <= 0xFF and 0 <= lo <= 0xFF):
                raise ValueError
            self.result = (hi, lo)
            return True
        except Exception:
            messagebox.showerror("Invalid Input", "HI and LO must be hex bytes (00-FF).")
            return False


    def buttonbox(self):
        box = tk.Frame(self, bg="#F3F4F7")
        ttk.Button(box, text="OK", width=8, command=self.ok, default="active").pack(side="right", padx=6, pady=8)
        ttk.Button(box, text="Cancel", width=8, command=self.cancel).pack(side="right", padx=6, pady=8)
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        box.pack(fill="x")
        finalize_dialog_geometry(self, 360, 210)
