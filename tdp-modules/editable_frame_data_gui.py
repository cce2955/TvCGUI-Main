# editable_frame_data_gui.py
#
# Frame Data Editor window for TvC HUD
# - scans each move's memory block to collect ALL nonzero float-like hitbox values in the "house"
# - shows a single "likely" hitbox value
# - shows all candidates in a second column
# - lets you edit EVERY one of those values
# - supports reset-to-original
# - integrates Knockback Trajectory & Hit Reaction helpers
# - refactor: removed advH/advB columns

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import threading
import math

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
        write_attack_property,  # used as fallback for hit reaction
        # write_hit_reaction may or may not exist; we'll call via helper if present
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

# ------------------------------------------------------------
# Knockback trajectory / Hit reaction dictionaries + helpers
# ------------------------------------------------------------

# Knockback trajectory descriptions
KB_TRAJ_MAP = {
    0xBD: "Up Forward KB",
    0xBE: "Down Forward KB",
    0xBC: "Up KB (Spiral)",
    0xC4: "Up Pop (j.L/j.M)",
}

# Hit reaction type descriptions
HIT_REACTION_MAP = {
    0x000000: "Stay on ground",
    0x000001: "Ground/Air > KB",
    0x000002: "Ground/Air > KD",
    0x000003: "Ground/Air > Spiral KD",
    0x000004: "Sweep",
    0x000008: "Stagger",
    0x000010: "Ground > Stay Ground, Air > KB",
    0x000040: "Ground > Stay Ground, Air > KB, OTG > Stay OTG",
    0x000041: "Ground/Air > KB, OTG > Stay OTG",
    0x000042: "Ground/Air > KD, OTG > Stay OTG",
    0x000080: "Ground > Stay Ground, Air > KB",
    0x000082: "Ground/Air > KD",
    0x000083: "Ground/Air > Spiral KD",
    0x000400: "Launcher",
    0x000800: "Ground > Stay Ground, Air > Soft KD",
    0x000848: "Ground > Stagger, Air > Soft KD",
    0x002010: "Ground > Stay Ground, Air > KB",
    0x003010: "Ground > Stay Ground, Air > KB",
    0x004200: "Ground/Air > KD",
    0x800080: "Ground > Crumple, Air > KB",
    0x800002: "Ground/Air > KD, Wall > Wallbounce",
    0x800008: "Alex Flash Chop",
    0x800020: "Snap Back",
    0x800082: "Ground/Air > KD, Wall > Wallbounce",
    0x001001: "Wonky: Friender/Zombies grab if KD near ground",
    0x001003: "Wonky variant",
}

def _fmt_kb_traj(val):
    if val is None:
        return ""
    desc = KB_TRAJ_MAP.get(val, "Unknown")
    return f"0x{val:02X} ({desc})"

def _fmt_hit_reaction(val):
    if val is None:
        return ""
    desc = HIT_REACTION_MAP.get(val, "Unknown")
    return f"0x{val:06X} ({desc})"

def _parse_hit_reaction_input(s: str):
    s = s.strip()
    if not s:
        return None
    # hex (with or without 0x)
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s, 16)
    except ValueError:
        pass
    # decimal
    try:
        return int(s, 10)
    except ValueError:
        pass
    return None

def _write_hit_reaction(mv, val) -> bool:
    if not WRITER_AVAILABLE:
        return False
    # Try direct writer if present
    try:
        from move_writer import write_hit_reaction  # type: ignore
        return bool(write_hit_reaction(mv, val))
    except Exception:
        pass
    # Fallback property writer
    try:
        return bool(write_attack_property(mv, "hit_reaction", val))
    except Exception:
        return False

# ------------------------------------------------------------
# hitbox scan
# ------------------------------------------------------------
def _scan_hitbox_house(move_abs: int):
    """Scan move block for all valid floats."""
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
        if abs(f) < 1e-6:  # Skip near-zero
            continue
        candidates.append((off, float(f)))

    return candidates


def _select_primary_from_candidates(cands: list[tuple[int, float]]):
    """
    Extract radius from candidates.

    Heuristics (in order):
    1. Single large poke (>=400) = radius
    2. Largest value in realistic range [5.0, 42.0] = radius
       (bones cluster around 0.5-3.0, so bigger = hitbox)
    3. Last float in valid range as fallback
    """
    if not cands:
        return None, None

    # Check for big poke
    for off, val in cands:
        if val >= 400.0:
            return off, val

    # Largest realistic
    MAX_REAL_RADIUS = 42.0
    best_off, best_val = None, -1.0
    for off, val in cands:
        if MIN_REAL_RADIUS <= val <= MAX_REAL_RADIUS:
            if val > best_val:
                best_off, best_val = off, val

    if best_off is not None:
        return best_off, best_val

    # Last in range
    for off, val in reversed(cands):
        if MIN_REAL_RADIUS <= val <= MAX_REAL_RADIUS:
            return off, val

    # Absolute fallback
    return cands[-1] if cands else (None, None)


def _format_candidate_list(cands: list[tuple[int, float]], max_show: int = 4) -> str:
    parts = []
    for idx, (_off, val) in enumerate(cands[:max_show]):
        parts.append(f"r{idx}={val:.1f}")
    if len(cands) > max_show:
        parts.append("…")
    return " ".join(parts)

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
        self.original_moves: dict[int, dict] = {}

        self._build()

    def _build(self):
        cname = self.target_slot.get("char_name", "—")
        self.root = tk.Tk()
        self.root.title(f"Frame Data Editor: {self.slot_label} ({cname})")
        self.root.geometry("1350x720")

        top = tk.Frame(self.root)
        top.pack(side="top", fill="x", padx=5, pady=5)

        if WRITER_AVAILABLE:
            tk.Label(top, text="Double-click to edit. Writes straight to Dolphin.", fg="blue").pack(side="left")
        else:
            tk.Label(top, text="WARNING: move_writer not found. Editing disabled!", fg="red").pack(side="left")

        tk.Button(top, text="Reset to original", command=self._reset_all_moves).pack(side="right", padx=4)

        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Columns: advH/advB removed
        cols = (
            "move", "kind", "damage", "meter",
            "startup", "active", "hitstun", "blockstun", "hitstop",
            "hb_main",           # likely radius
            "hb",                # candidates summary
            "kb",
            "hit_reaction",
            "abs",
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
            ("hb_main", "Hitbox"),
            ("hb", "Hitbox cand."),
            ("kb", "Knockback"),
            ("hit_reaction", "Hit Reaction"),
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
        self.tree.column("hb_main", width=70, anchor="center")
        self.tree.column("hb", width=220, anchor="w")
        self.tree.column("kb", width=160, anchor="center")
        self.tree.column("hit_reaction", width=240, anchor="w")
        self.tree.column("abs", width=100, anchor="w")

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
                kb_parts.append(_fmt_kb_traj(kb_traj))
            kb_txt = " ".join(kb_parts)

            hr_txt = _fmt_hit_reaction(mv.get("hit_reaction"))

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
                    hb_main_txt,
                    hb_txt,
                    kb_txt,
                    hr_txt,
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
                }

        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)

    # -------------------------------------------------------- reset
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

            # damage
            if orig["damage"] is not None:
                success = write_damage(mv, orig["damage"])
                if success:
                    self.tree.set(item_id, "damage", str(orig["damage"]))
                    mv["damage"] = orig["damage"]
                    reset_count += 1
                else:
                    failed_writes.append(f"damage @ 0x{abs_addr:08X}: {mv.get('damage_addr')}")

            # meter
            if orig["meter"] is not None:
                success = write_meter(mv, orig["meter"])
                if success:
                    self.tree.set(item_id, "meter", str(orig["meter"]))
                    mv["meter"] = orig["meter"]
                    reset_count += 1
                else:
                    failed_writes.append(f"meter @ 0x{abs_addr:08X}")

            # active frames
            if orig["active_start"] is not None and orig["active_end"] is not None:
                success = write_active_frames(mv, orig["active_start"], orig["active_end"])
                if success:
                    self.tree.set(item_id, "startup", str(orig["active_start"]))
                    self.tree.set(item_id, "active", f"{orig['active_start']}-{orig['active_end']}")
                    mv["active_start"] = orig["active_start"]
                    mv["active_end"] = orig["active_end"]
                    reset_count += 1
                else:
                    failed_writes.append(f"active @ 0x{abs_addr:08X}")

            # hitstun
            if orig["hitstun"] is not None:
                success = write_hitstun(mv, orig["hitstun"])
                if success:
                    self.tree.set(item_id, "hitstun", _fmt_stun(orig["hitstun"]))
                    mv["hitstun"] = orig["hitstun"]
                    reset_count += 1
                else:
                    failed_writes.append(f"hitstun @ 0x{abs_addr:08X}")

            # blockstun
            if orig["blockstun"] is not None:
                success = write_blockstun(mv, orig["blockstun"])
                if success:
                    self.tree.set(item_id, "blockstun", _fmt_stun(orig["blockstun"]))
                    mv["blockstun"] = orig["blockstun"]
                    reset_count += 1
                else:
                    failed_writes.append(f"blockstun @ 0x{abs_addr:08X}")

            # hitstop
            if orig["hitstop"] is not None:
                success = write_hitstop(mv, orig["hitstop"])
                if success:
                    self.tree.set(item_id, "hitstop", str(orig["hitstop"]))
                    mv["hitstop"] = orig["hitstop"]
                    reset_count += 1
                else:
                    failed_writes.append(f"hitstop @ 0x{abs_addr:08X}")

            # knockback
            if (orig["kb0"] is not None) or (orig["kb1"] is not None) or (orig["kb_traj"] is not None):
                success = write_knockback(mv, orig["kb0"], orig["kb1"], orig["kb_traj"])
                if success:
                    parts = []
                    if orig["kb0"] is not None:
                        parts.append(f"K0:{orig['kb0']}")
                    if orig["kb1"] is not None:
                        parts.append(f"K1:{orig['kb1']}")
                    if orig["kb_traj"] is not None:
                        parts.append(_fmt_kb_traj(orig["kb_traj"]))
                    self.tree.set(item_id, "kb", " ".join(parts))
                    mv["kb0"] = orig["kb0"]
                    mv["kb1"] = orig["kb1"]
                    mv["kb_traj"] = orig["kb_traj"]
                    reset_count += 1
                else:
                    failed_writes.append(f"knockback @ 0x{abs_addr:08X}")

            # hit reaction
            if orig.get("hit_reaction") is not None:
                if _write_hit_reaction(mv, orig["hit_reaction"]):
                    self.tree.set(item_id, "hit_reaction", _fmt_hit_reaction(orig["hit_reaction"]))
                    mv["hit_reaction"] = orig["hit_reaction"]
                    reset_count += 1
                else:
                    failed_writes.append(f"hit_reaction @ 0x{abs_addr:08X}")

            # hitbox - set offset BEFORE writing
            orig_val = orig.get("hb_r")
            orig_off = orig.get("hb_off")
            orig_cands = orig.get("hb_candidates") or []

            if orig_val is not None and orig_off is not None:
                mv["hb_off"] = orig_off
                mv["hb_r"] = orig_val
                success = write_hitbox_radius(mv, orig_val)
                if success:
                    self.tree.set(item_id, "hb_main", f"{orig_val:.1f}")
                    reset_count += 1
                else:
                    failed_writes.append(f"hitbox @ 0x{abs_addr:08X} (off=0x{orig_off:X})")

            # Restore candidate list for display
            mv["hb_candidates"] = orig_cands
            self.tree.set(item_id, "hb", _format_candidate_list(orig_cands))

        msg = f"Reset complete: {reset_count} writes successful"
        if failed_writes:
            msg += f"\n\nFailed writes:\n" + "\n".join(failed_writes[:10])
        messagebox.showinfo("Reset", msg)

    # -------------------------------------------------------- tree events
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
        elif col_name == "hb_main":
            self._edit_hitbox_main(item, mv, current_val)
        elif col_name == "hb":
            self._edit_hitbox(item, mv, current_val)
        elif col_name == "kb":
            self._edit_knockback(item, mv, current_val)
        elif col_name == "hit_reaction":
            self._edit_hit_reaction(item, mv, current_val)

    # -------------------------------------------------------- editors
    def _edit_hitbox_main(self, item, mv, current):
        # edit just the primary
        cur_r = mv.get("hb_r")
        if cur_r is None:
            # fall back to first candidate
            cands = mv.get("hb_candidates") or []
            if cands:
                cur_r = cands[0][1]
                mv["hb_off"] = cands[0][0]
        if cur_r is None:
            cur_r = 0.0

        new_val = simpledialog.askfloat("Edit Hitbox", "New radius:", initialvalue=cur_r, minvalue=0.0)
        if new_val is None:
            return

        # write at stored offset
        if mv.get("hb_off") is None:
            mv["hb_off"] = FALLBACK_HB_OFFSET
        if WRITER_AVAILABLE:
            write_hitbox_radius(mv, new_val)

        mv["hb_r"] = new_val
        # also update candidate list's matching element if present
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
        else:
            return self._edit_hitbox_scrollable(item, mv, cands)

    def _edit_hitbox_simple(self, item, mv, cands):
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Hitbox Values")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(dlg, text="Edit each radius below. r0 is usually the main one.").grid(
            row=0, column=0, columnspan=3, padx=6, pady=4, sticky="w"
        )

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

        tk.Label(inner, text="Edit each radius below. r0 is usually the main one.",
                 anchor="w", justify="left").grid(row=0, column=0, columnspan=3, padx=6, pady=4, sticky="w")

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
        new_val = simpledialog.askinteger("Edit Startup", "New startup frame:",
                                          initialvalue=cur, minvalue=1, maxvalue=255)
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

    # -------- Knockback editor
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
                self.tree.set(item, "kb", f"K0:{k0} K1:{k1} {_fmt_kb_traj(t)}")
                mv["kb0"] = k0
                mv["kb1"] = k1
                mv["kb_traj"] = t
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).pack(pady=10)

    # -------- Hit Reaction editor
    def _edit_hit_reaction(self, item, mv, current):
        cur_hr = mv.get("hit_reaction")

        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Hit Reaction")
        dlg.geometry("520x420")

        tk.Label(dlg, text="Hit Reaction Type", font=("Arial", 12, "bold")).pack(pady=5)

        if cur_hr is not None:
            tk.Label(
                dlg,
                text=f"Current: {_fmt_hit_reaction(cur_hr)}",
                fg="blue",
                font=("Arial", 10),
            ).pack(pady=3)

        tk.Label(dlg, text="Common Reactions:", font=("Arial", 10, "bold")).pack(anchor="w", padx=10, pady=(10, 5))

        frame = tk.Frame(dlg)
        frame.pack(fill="both", expand=True, padx=10, pady=5)

        scrollbar = tk.Scrollbar(frame)
        scrollbar.pack(side="right", fill="y")

        listbox = tk.Listbox(frame, yscrollcommand=scrollbar.set)
        scrollbar.config(command=listbox.yview)

        common = [
            (0x000000, "Stay on ground"),
            (0x000001, "Ground/Air > KB"),
            (0x000002, "Ground/Air > KD"),
            (0x000003, "Ground/Air > Spiral KD"),
            (0x000004, "Sweep"),
            (0x000008, "Stagger"),
            (0x000400, "Launcher"),
            (0x800080, "Ground > Crumple, Air > KB"),
            (0x800002, "Ground/Air > KD, Wall > Wallbounce"),
        ]
        for val, desc in common:
            listbox.insert("end", f"0x{val:06X}: {desc}")

        listbox.pack(fill="both", expand=True)

        selected_val = tk.IntVar(value=cur_hr or 0)

        def on_select(_evt):
            sel = listbox.curselection()
            if sel:
                val, _ = common[sel[0]]
                selected_val.set(val)

        listbox.bind("<<ListboxSelect>>", on_select)

        tk.Label(dlg, text="Or enter hex value:", font=("Arial", 10)).pack(anchor="w", padx=10, pady=(10, 0))
        hex_entry = tk.Entry(dlg, width=15)
        hex_entry.insert(0, f"0x{cur_hr:06X}" if cur_hr is not None else "0x000000")
        hex_entry.pack(anchor="w", padx=10)

        def on_ok():
            val = _parse_hit_reaction_input(hex_entry.get())
            if val is None:
                val = selected_val.get()

            if _write_hit_reaction(mv, val):
                self.tree.set(item, "hit_reaction", _fmt_hit_reaction(val))
                mv["hit_reaction"] = val
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).pack(pady=10)

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
            label="Edit Hitboxes…",
            command=lambda m=mv, i=item: self._edit_hitbox(i, m, self.tree.set(i, "hb")),
        )
        menu.add_command(
            label="Edit Knockback…",
            command=lambda m=mv, i=item: self._edit_knockback(i, m, self.tree.set(i, "kb")),
        )
        menu.add_command(
            label="Edit Hit Reaction…",
            command=lambda m=mv, i=item: self._edit_hit_reaction(i, m, self.tree.set(i, "hit_reaction")),
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
# entry point
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
