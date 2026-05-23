# fd_widgets.py
#
# Small reusable Tk widgets/dialogs to keep fd_window slim.

from __future__ import annotations

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox


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
    "hitstop": "Freeze frames on impact. Higher hitstop makes hits feel heavier but is not the same as hitstun.",
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
    "invuln": "Display-only startup-protection probe. This flags 0x70-family packets seen on Shoryuken-style startup and Jun 6B; it is not editable yet.",
    "attack_property": "Attack property byte. Use the known-value dropdown for confirmed properties; manual entry is for testing unknown bytes.",
    "hit_reaction": "The reaction state/animation chosen on hit, such as standing hitstun, low hitstun, overhead hitstun, knockdown, crumple, or special reactions.",
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
    """Small integer editor with a per-field explanation.

    This replaces bare simpledialog.askinteger calls so every editable field can
    explain what changing the value actually affects.
    """
    dlg = tk.Toplevel(parent)
    dlg.title(title)
    dlg.resizable(False, False)
    dlg.transient(parent)
    dlg.grab_set()

    result = {"value": None}

    body = ttk.Frame(dlg, padding=12)
    body.pack(fill="both", expand=True)

    ttk.Label(body, text=prompt, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 4))
    if help_text:
        ttk.Label(body, text=help_text, justify="left", wraplength=390).pack(anchor="w", pady=(0, 8))
    if address is not None:
        ttk.Label(body, text=f"Address: 0x{int(address):08X}").pack(anchor="w", pady=(0, 8))

    row = ttk.Frame(body)
    row.pack(fill="x", pady=(0, 10))
    ttk.Label(row, text="Value:").pack(side="left")
    var = tk.StringVar(master=dlg, value=str(int(initialvalue)))
    ent = ttk.Entry(row, textvariable=var, width=14)
    ent.pack(side="left", padx=(8, 0))

    bounds = []
    if minvalue is not None:
        bounds.append(f"min {minvalue}")
    if maxvalue is not None:
        bounds.append(f"max {maxvalue}")
    if bounds:
        ttk.Label(body, text="Allowed: " + ", ".join(bounds)).pack(anchor="w", pady=(0, 10))

    buttons = ttk.Frame(body)
    buttons.pack(fill="x")

    def apply_value():
        try:
            raw = (var.get() or "").strip()
            val = int(raw, 16) if raw.lower().startswith("0x") else int(raw, 10)
        except Exception:
            messagebox.showerror(title, "Invalid integer value.", parent=dlg)
            return
        if minvalue is not None and val < minvalue:
            messagebox.showerror(title, f"Value must be at least {minvalue}.", parent=dlg)
            return
        if maxvalue is not None and val > maxvalue:
            messagebox.showerror(title, f"Value must be no more than {maxvalue}.", parent=dlg)
            return
        result["value"] = val
        dlg.destroy()

    def cancel():
        result["value"] = None
        dlg.destroy()

    ttk.Button(buttons, text="OK", command=apply_value).pack(side="right", padx=(6, 0))
    ttk.Button(buttons, text="Cancel", command=cancel).pack(side="right")

    ent.focus_set()
    ent.selection_range(0, "end")
    dlg.bind("<Return>", lambda _e: apply_value())
    dlg.bind("<Escape>", lambda _e: cancel())

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
    dlg.resizable(False, False)
    dlg.transient(parent)
    dlg.grab_set()

    result = {"value": None}

    body = ttk.Frame(dlg, padding=12)
    body.pack(fill="both", expand=True)

    ttk.Label(body, text=prompt, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 4))
    if help_text:
        ttk.Label(body, text=help_text, justify="left", wraplength=390).pack(anchor="w", pady=(0, 8))

    row = ttk.Frame(body)
    row.pack(fill="x", pady=(0, 10))
    ttk.Label(row, text="Value:").pack(side="left")
    var = tk.StringVar(master=dlg, value=f"{float(initialvalue):.6g}")
    ent = ttk.Entry(row, textvariable=var, width=14)
    ent.pack(side="left", padx=(8, 0))

    buttons = ttk.Frame(body)
    buttons.pack(fill="x")

    def apply_value():
        try:
            val = float((var.get() or "").strip())
        except Exception:
            messagebox.showerror(title, "Invalid number value.", parent=dlg)
            return
        if minvalue is not None and val < minvalue:
            messagebox.showerror(title, f"Value must be at least {minvalue}.", parent=dlg)
            return
        if maxvalue is not None and val > maxvalue:
            messagebox.showerror(title, f"Value must be no more than {maxvalue}.", parent=dlg)
            return
        result["value"] = val
        dlg.destroy()

    def cancel():
        result["value"] = None
        dlg.destroy()

    ttk.Button(buttons, text="OK", command=apply_value).pack(side="right", padx=(6, 0))
    ttk.Button(buttons, text="Cancel", command=cancel).pack(side="right")

    ent.focus_set()
    ent.selection_range(0, "end")
    dlg.bind("<Return>", lambda _e: apply_value())
    dlg.bind("<Escape>", lambda _e: cancel())

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
        super().__init__(parent, title="Manual Anim ID (HI / LO)")

    def body(self, master):
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
