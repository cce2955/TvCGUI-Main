# editable_frame_data_gui.py
#
# Frame Data Editor window for TvC HUD.
#
# Features:
#   - Edits main frame data (damage, meter, startup, active, hitstun, etc.)
#   - Active 2 (inline active) editing support
#   - Hitbox radius discovery + editing based on scanned float candidates
#   - Knockback + hit reaction editors with named presets
#   - Right-click context menu to copy / inspect relevant memory addresses
#
# The editor is read-only if move_writer is not available.

import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import threading
import math

from move_id_map import lookup_move_name
from moves import CHAR_ID_CORRECTION

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
    # Caller still gets a fully functional viewer; only writes are disabled.
    print("WARNING: move_writer not found, editor will be read-only")

try:
    from dolphin_io import rdf32
except ImportError:
    rdf32 = None

try:
    from scan_normals_all import ANIM_MAP as _ANIM_MAP_FOR_GUI
except Exception:
    _ANIM_MAP_FOR_GUI = {}

# Max scan window (in bytes) when looking for hitbox float values
HB_SCAN_MAX = 0x600
FALLBACK_HB_OFFSET = 0x21C
MIN_REAL_RADIUS = 5.0

# Knockback trajectory descriptions (angle codes → labels)
KB_TRAJ_MAP = {
    0xBD: "Up Forward KB",
    0xBE: "Down Forward KB",
    0xBC: "Up KB (Spiral)",
    0xC4: "Up Pop (j.L/j.M)",
}

# Hit reaction type descriptions (bitfields → behavior summary)
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
    """Format a knockback trajectory byte as hex plus a short label."""
    if val is None:
        return ""
    desc = KB_TRAJ_MAP.get(val, "Unknown")
    return f"0x{val:02X} ({desc})"


def _fmt_hit_reaction(val):
    """Format a hit reaction bitfield as hex plus a short label."""
    if val is None:
        return ""
    desc = HIT_REACTION_MAP.get(val, "Unknown")
    return f"0x{val:06X} ({desc})"


def _parse_hit_reaction_input(s: str):
    """
    Parse a hit reaction value from user input.

    Accepts:
        - Hex with 0x prefix (e.g. "0x800080")
        - Hex without prefix (e.g. "800080")
        - Decimal (e.g. "524288")
    Returns:
        int or None on failure.
    """
    s = s.strip()
    if not s:
        return None
    # Try hex first
    try:
        return int(s, 16) if s.lower().startswith("0x") else int(s, 16)
    except ValueError:
        pass
    # Fallback to decimal
    try:
        return int(s, 10)
    except ValueError:
        pass
    return None


def _write_hit_reaction(mv, val) -> bool:
    """
    Write hit reaction bitfield to memory for a given move.

    Tries a dedicated move_writer helper first if present, falling back to
    a direct byte write using addresses from scan_normals_all.
    """
    if not WRITER_AVAILABLE:
        return False

    # 1. Prefer move_writer's helper if available.
    try:
        from move_writer import write_hit_reaction
        if write_hit_reaction(mv, val):
            return True
    except Exception:
        # Helper failed or is missing; use inline write instead.
        pass

    # 2. Direct write from the annotated address.
    addr = mv.get("hit_reaction_addr")
    if not addr:
        return False

    try:
        from dolphin_io import wd8
        # Hit reaction is stored as three bytes: XX YY ZZ
        wd8(addr + 0, (val >> 16) & 0xFF)
        wd8(addr + 1, (val >> 8) & 0xFF)
        wd8(addr + 2, val & 0xFF)
        return True
    except Exception as e:
        print(f"Inline hit reaction write failed: {e}")
        return False


def _write_active2_frames(mv, start, end) -> bool:
    """
    Write Active 2 (inline active) frame window to memory for a given move.

    start / end are frame indices as integers. The underlying pattern expects:
        - start at pattern_base + 4
        - end   at pattern_base + 16
    """
    if not WRITER_AVAILABLE:
        return False

    addr = mv.get("active2_addr")
    if not addr:
        return False

    try:
        from dolphin_io import wd8
        if not wd8(addr + 4, start):
            return False
        if not wd8(addr + 16, end):
            return False
        return True
    except Exception as e:
        print(f"Failed to write active2 frames: {e}")
        return False


def _scan_hitbox_house(move_abs: int):
    """
    Scan a move's data block for plausible hitbox radius floats.

    Returns:
        List of (offset, float_value) tuples for each candidate radius.
    """
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
    """
    Choose a representative hitbox radius from a list of candidates.

    Priority:
        1) Very large values (>= 400.0) are assumed to be explicit radius values.
        2) Otherwise, pick the largest value within a realistic range.
        3) Fall back to the last candidate in range if nothing stands out.
    """
    if not cands:
        return None, None

    # Obvious "long poke" cases.
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

    # No best candidate; fall back to the last radius in range.
    for off, val in reversed(cands):
        if MIN_REAL_RADIUS <= val <= MAX_REAL_RADIUS:
            return off, val

    # As a last resort, return the last scanned float.
    return cands[-1] if cands else (None, None)


def _format_candidate_list(cands: list[tuple[int, float]], max_show: int = 4) -> str:
    """
    Compact textual summary of the first few hitbox radius candidates.

    Example: 'r0=12.0 r1=8.0 r2=16.0 …'
    """
    parts = []
    for idx, (_off, val) in enumerate(cands[:max_show]):
        parts.append(f"r{idx}={val:.1f}")
    if len(cands) > max_show:
        parts.append("…")
    return " ".join(parts)


def _fmt_stun(v):
    """
    Convert internal stun byte into a more readable value where known.

    Some common values are mapped to their in-game frame equivalents.
    """
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
    """
    Inverse of _fmt_stun: map a friendly frame count back to the raw byte.

    Returns:
        int or None if the input is empty/invalid.
    """
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


def _pretty_move_name(aid, char_name=None):
    """
    Produce a human-readable label for a move, given an animation ID.

    Resolution order:
      1) lookup_move_name(aid, char_id)
      2) Same lookup with repaired high bits (0x100/0x200/0x300)
      3) Fallback to scanner's ANIM_MAP
      4) 'anim_XXXX' placeholder
    """
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

    # Some data sources truncate IDs. Try reattaching common high bits.
    if aid < 0x100:
        for high in (0x100, 0x200, 0x300):
            name = lookup_move_name(aid + high, char_id)
            if name:
                return name

    name = _ANIM_MAP_FOR_GUI.get(aid)
    if name:
        return name

    return f"anim_{aid:04X}"


class EditableFrameDataWindow:
    """
    Tkinter-based frame data editor for a single character slot.

    The window lists all discovered moves for a slot and allows inline
    editing of frame data where backing addresses are known and the
    move_writer module is present.
    """

    def __init__(self, slot_label, target_slot):
        self.slot_label = slot_label
        self.target_slot = target_slot

        cname = self.target_slot.get("char_name")

        # Sort moves primarily by ID cluster (>=0x100 first), then by ID,
        # and finally by absolute address as a tie-breaker.
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

        # Deduplicate moves that share the same named animation label.
        seen_named = set()
        deduped = []
        for mv in moves_sorted:
            aid = mv.get("id")
            if aid is None:
                deduped.append(mv)
                continue
            name = _pretty_move_name(aid, cname)

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
        """Construct the Tk UI components and populate the move table."""
        cname = self.target_slot.get("char_name", "—")
        self.root = tk.Tk()
        self.root.title(f"Frame Data Editor: {self.slot_label} ({cname})")
        self.root.geometry("1350x720")

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
            "kb", "hit_reaction",
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
            ("active2", "Active 2"),
            ("hitstun", "HS"),
            ("blockstun", "BS"),
            ("hitstop", "Stop"),
            ("hb_main", "Hitbox"),
            ("hb", "Hitbox cand."),
            ("kb", "Knockback"),
            ("hit_reaction", "Hit Reaction"),
            ("abs", "Address"),
        ]
        for c, txt in headers:
            self.tree.heading(c, text=txt)

        # Generic column widths; tuned to keep one line per entry on a 720p-ish window.
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
        self.tree.column("hit_reaction", width=240, anchor="w")
        self.tree.column("abs", width=100, anchor="w")

        cname = self.target_slot.get("char_name", "—")

        # Populate rows with move data and derived fields.
        for mv in self.moves:
            aid = mv.get("id")
            move_name = _pretty_move_name(aid, cname)

            if aid is not None:
                move_name = f"{move_name} [0x{aid:04X}]"

            # startup/active from main table
            a_s = mv.get("active_start")
            a_e = mv.get("active_end")
            startup_txt = "" if a_s is None else str(a_s)
            if a_s is not None and a_e is not None:
                active_txt = f"{a_s}-{a_e}"
            else:
                active_txt = ""

            # Active 2 (inline)
            a2_s = mv.get("active2_start")
            a2_e = mv.get("active2_end")
            if a2_s is None and a2_e is None:
                active2_txt = ""
            elif a2_e is None:
                active2_txt = str(a2_s)
            else:
                active2_txt = f"{a2_s}-{a2_e}"

            # Hitbox candidates
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

            # Knockback summary
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
                    startup_txt,
                    active_txt,
                    active2_txt,
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

            # Capture original values for the "Reset to original" button.
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
                }

        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)

    def _reset_all_moves(self):
        """Restore all editable fields to the values captured on window creation."""
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
                    failed_writes.append(f"damage @ 0x{abs_addr:08X}")

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

            # active2 frames
            if orig["active2_start"] is not None and orig["active2_end"] is not None:
                success = _write_active2_frames(mv, orig["active2_start"], orig["active2_end"])
                if success:
                    self.tree.set(
                        item_id,
                        "active2",
                        f"{orig['active2_start']}-{orig['active2_end']}",
                    )
                    mv["active2_start"] = orig["active2_start"]
                    mv["active2_end"] = orig["active2_end"]
                    reset_count += 1
                else:
                    failed_writes.append(f"active2 @ 0x{abs_addr:08X}")

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

            # hitbox
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
                    failed_writes.append(f"hitbox @ 0x{abs_addr:08X}")

            mv["hb_candidates"] = orig_cands
            self.tree.set(item_id, "hb", _format_candidate_list(orig_cands))

        msg = f"Reset complete: {reset_count} writes successful"
        if failed_writes:
            msg += f"\n\nFailed writes:\n" + "\n".join(failed_writes[:10])
        messagebox.showinfo("Reset", msg)

    def _on_double_click(self, event):
        """Route double-clicks on table cells to the appropriate editor."""
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
        elif col_name == "hit_reaction":
            self._edit_hit_reaction(item, mv, current_val)

    def _on_right_click(self, event):
        """Show context menu with address helpers and raw data view."""
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

        # Map columns to their backing address keys.
        addr_map = {
            "damage": ("damage_addr", "Damage"),
            "meter": ("meter_addr", "Meter"),
            "active": ("active_addr", "Active"),
            "active2": ("active2_addr", "Active 2"),
            "hitstun": ("stun_addr", "Stun"),
            "blockstun": ("stun_addr", "Stun"),
            "hitstop": ("stun_addr", "Stun"),
            "kb": ("knockback_addr", "Knockback"),
            "hb_main": ("hb_off", "Hitbox"),
            "hb": ("hb_off", "Hitbox"),
            "abs": ("abs", "Move"),
        }

        if col_name in addr_map:
            addr_key, label = addr_map[col_name]
            addr = mv.get(addr_key)

            if addr_key == "hb_off":
                # Hitbox offset is relative to the move base address.
                move_abs = mv.get("abs")
                if move_abs and addr is not None:
                    addr = move_abs + addr

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
        """Copy an address to the clipboard in 0xXXXXXXXX format."""
        self.root.clipboard_clear()
        self.root.clipboard_append(f"0x{addr:08X}")
        messagebox.showinfo("Copied", f"0x{addr:08X} copied to clipboard")

    def _show_address_info(self, addr, title):
        """
        Hex-dump a small window of memory at addr in a modal dialog.

        Intended mostly for quick inspection while reverse-engineering.
        """
        try:
            from dolphin_io import rbytes
            data = rbytes(addr, 32)
            if not data:
                messagebox.showerror("Error", f"Failed to read memory at 0x{addr:08X}")
                return

            dlg = tk.Toplevel(self.root)
            dlg.title(title)
            dlg.geometry("500x400")

            txt = tk.Text(dlg, wrap="none", font=("Courier", 10))
            txt.pack(fill="both", expand=True, padx=5, pady=5)

            txt.insert("end", f"Memory at 0x{addr:08X}:\n\n")
            for i in range(0, len(data), 16):
                line_addr = addr + i
                hex_part = " ".join(f"{b:02X}" for b in data[i:i+16])
                ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in data[i:i+16])
                txt.insert("end", f"{line_addr:08X}: {hex_part:<48} {ascii_part}\n")

            txt.config(state="disabled")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to read memory: {e}")

    def _show_raw_data(self, mv):
        """Show the raw move dict (including all *_addr fields) in a scrollable view."""
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

    # ========== EDITORS ==========

    def _edit_active2(self, item, mv, current):
        """Dialog editor for Active 2 (inline active) frame window."""
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
            if _write_active2_frames(mv, s, e):
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
        new_val = simpledialog.askinteger(
            "Edit Damage",
            "New damage:",
            initialvalue=cur,
            minvalue=0,
            maxvalue=999999,
        )
        if new_val is not None and write_damage(mv, new_val):
            self.tree.set(item, "damage", str(new_val))
            mv["damage"] = new_val

    def _edit_meter(self, item, mv, current):
        try:
            cur = int(current) if current else 0
        except ValueError:
            cur = 0
        new_val = simpledialog.askinteger(
            "Edit Meter",
            "New meter:",
            initialvalue=cur,
            minvalue=0,
            maxvalue=255,
        )
        if new_val is not None and write_meter(mv, new_val):
            self.tree.set(item, "meter", str(new_val))
            mv["meter"] = new_val

    def _edit_startup(self, item, mv, current):
        try:
            cur = int(current) if current else 1
        except ValueError:
            cur = 1
        new_val = simpledialog.askinteger(
            "Edit Startup",
            "New startup frame:",
            initialvalue=cur,
            minvalue=1,
            maxvalue=255,
        )
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
        new_val = simpledialog.askinteger(
            "Edit Hitstun",
            "New hitstun:",
            initialvalue=cur,
            minvalue=0,
            maxvalue=255,
        )
        if new_val is not None and write_hitstun(mv, new_val):
            self.tree.set(item, "hitstun", _fmt_stun(new_val))
            mv["hitstun"] = new_val

    def _edit_blockstun(self, item, mv, current):
        cur = _unfmt_stun(current) if current else 0
        new_val = simpledialog.askinteger(
            "Edit Blockstun",
            "New blockstun:",
            initialvalue=cur,
            minvalue=0,
            maxvalue=255,
        )
        if new_val is not None and write_blockstun(mv, new_val):
            self.tree.set(item, "blockstun", _fmt_stun(new_val))
            mv["blockstun"] = new_val

    def _edit_hitstop(self, item, mv, current):
        try:
            cur = int(current) if current else 0
        except ValueError:
            cur = 0
        new_val = simpledialog.askinteger(
            "Edit Hitstop",
            "New hitstop:",
            initialvalue=cur,
            minvalue=0,
            maxvalue=255,
        )
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
                self.tree.set(item, "kb", f"K0:{k0} K1:{k1} {_fmt_kb_traj(t)}")
                mv["kb0"] = k0
                mv["kb1"] = k1
                mv["kb_traj"] = t
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).pack(pady=10)

    def _edit_hit_reaction(self, item, mv, current):
        """Dialog editor for the hit reaction bitfield with a curated preset list."""
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

        # Expanded common set: full list of known reaction codes.
        common_vals = [
            0x000000,
            0x000001,
            0x000002,
            0x000003,
            0x000004,
            0x000008,
            0x000010,
            0x000040,
            0x000041,
            0x000042,
            0x000080,
            0x000082,
            0x000083,
            0x000400,
            0x000800,
            0x000848,
            0x002010,
            0x003010,
            0x004200,
            0x800080,
            0x800002,
            0x800008,
            0x800020,
            0x800082,
            0x001001,
            0x001003,
        ]

        common = []
        for val in common_vals:
            desc = HIT_REACTION_MAP.get(val, "Unknown")
            common.append((val, desc))

        for val, desc in common:
            listbox.insert("end", f"0x{val:06X}: {desc}")

        listbox.pack(fill="both", expand=True)

        selected_val = tk.IntVar(value=cur_hr or 0)

        tk.Label(dlg, text="Or enter hex/decimal value:", font=("Arial", 10)).pack(anchor="w", padx=10, pady=(10, 0))
        hex_entry = tk.Entry(dlg, width=20)
        hex_entry.insert(0, f"0x{cur_hr:06X}" if cur_hr is not None else "0x000000")
        hex_entry.pack(anchor="w", padx=10)

        def on_select(_evt):
            sel = listbox.curselection()
            if sel:
                val, _ = common[sel[0]]
                selected_val.set(val)
                hex_entry.delete(0, tk.END)
                hex_entry.insert(0, f"0x{val:06X}")

        listbox.bind("<<ListboxSelect>>", on_select)

        def on_ok():
            val = _parse_hit_reaction_input(hex_entry.get())
            if val is None:
                val = selected_val.get()

            if _write_hit_reaction(mv, val):
                self.tree.set(item, "hit_reaction", _fmt_hit_reaction(val))
                mv["hit_reaction"] = val
            dlg.destroy()

        tk.Button(dlg, text="OK", command=on_ok).pack(pady=10)

    def _edit_hitbox_main(self, item, mv, current):
        """
        Direct editor for a move's primary hitbox radius.

        If no primary exists yet but candidates are present, the first
        candidate is used as the starting point.
        """
        cur_r = mv.get("hb_r")
        if cur_r is None:
            cands = mv.get("hb_candidates") or []
            if cands:
                cur_r = cands[0][1]
                mv["hb_off"] = cands[0][0]
        if cur_r is None:
            cur_r = 0.0

        new_val = simpledialog.askfloat(
            "Edit Hitbox",
            "New radius:",
            initialvalue=cur_r,
            minvalue=0.0,
        )
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
        """
        Entry point for hitbox editing. Uses a simple layout for a few candidates
        and a scrollable layout when the list grows large.
        """
        cands = mv.get("hb_candidates") or []

        if not cands:
            return self._edit_hitbox_main(item, mv, current)

        if len(cands) <= 6:
            return self._edit_hitbox_simple(item, mv, cands)
        else:
            return self._edit_hitbox_scrollable(item, mv, cands)

    def _edit_hitbox_simple(self, item, mv, cands):
        """Small fixed-size hitbox editor for up to ~6 radius entries."""
        dlg = tk.Toplevel(self.root)
        dlg.title("Edit Hitbox Values")
        dlg.transient(self.root)
        dlg.grab_set()

        tk.Label(
            dlg,
            text="Edit each radius below. r0 is usually the main one.",
        ).grid(row=0, column=0, columnspan=3, padx=6, pady=4, sticky="w")

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
        """Scrollable hitbox editor for moves with many radius candidates."""
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

        tk.Label(
            inner,
            text="Edit each radius below. r0 is usually the main one.",
            anchor="w",
            justify="left",
        ).grid(row=0, column=0, columnspan=3, padx=6, pady=4, sticky="w")

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
        """Start the Tk mainloop for this editor window."""
        self.root.mainloop()


def open_editable_frame_data_window(slot_label, scan_data):
    """
    Helper to spawn a frame data editor for a given slot in a background thread.

    slot_label:
        The HUD slot label, e.g. "P1-C1".
    scan_data:
        Full scan_normals_all output; we pick the first matching slot entry.
    """
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
