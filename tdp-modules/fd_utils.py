# fd_utils.py
#
# Small, pure helpers and a few thin I/O wrappers used by the FD editor UI.
# Keep this file UI-free.

from __future__ import annotations

import math
from typing import Any, Iterable

from move_id_map import lookup_move_name
from moves import CHAR_ID_CORRECTION

from fd_format import (
    fmt_kb_traj,
    fmt_hit_reaction,
    fmt_stun,
    unfmt_stun,
    HIT_REACTION_MAP,
)

try:
    from scan_normals_all import ANIM_MAP as _ANIM_MAP_FOR_GUI
except Exception:
    _ANIM_MAP_FOR_GUI = {}

try:
    from dolphin_io import rdf32
except Exception:
    rdf32 = None

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
    # UI will show read-only state; caller decides how to message.
    WRITER_AVAILABLE = False


HB_SCAN_MAX = 0x600
FALLBACK_HB_OFFSET = 0x21C
MIN_REAL_RADIUS = 5.0

KB_TRAJ_MAP = {
    0xBD: "Up Forward KB",
    0xBE: "Down Forward KB",
    0xBC: "Up KB (Spiral)",
    0xC4: "Up Pop (j.L/j.M)",
}


def pretty_move_name(anim_id: int | None, char_name: str | None = None) -> str:
    if anim_id is None:
        return "anim_--"

    char_id = None
    if char_name:
        try:
            char_id = CHAR_ID_CORRECTION.get(char_name, None)
        except Exception:
            char_id = None

    name = lookup_move_name(anim_id, char_id)
    if name:
        return name

    if anim_id < 0x100:
        for high in (0x100, 0x200, 0x300):
            name = lookup_move_name(anim_id + high, char_id)
            if name:
                return name

    name = _ANIM_MAP_FOR_GUI.get(anim_id)
    if name:
        return name

    return f"anim_{anim_id:04X}"


def scan_hitbox_candidates(move_abs: int) -> list[tuple[int, float]]:
    """
    Scan a move block for float-ish values that look like hitbox radii.

    Returns list of (offset, float_value). If dolphin_io.rdf32 is unavailable,
    returns [].
    """
    if rdf32 is None or not move_abs:
        return []
    out: list[tuple[int, float]] = []
    for off in range(0, HB_SCAN_MAX, 4):
        try:
            f = rdf32(move_abs + off)
        except Exception:
            continue
        if f is None or not isinstance(f, (int, float)) or not math.isfinite(f):
            continue
        if abs(float(f)) < 1e-6:
            continue
        out.append((off, float(f)))
    return out


def select_primary_hitbox(cands: list[tuple[int, float]]) -> tuple[int | None, float | None]:
    """
    Heuristic: prefer huge 400+ entries, else pick largest plausible radius.
    """
    if not cands:
        return (None, None)

    for off, val in cands:
        if val >= 400.0:
            return (off, val)

    MAX_REAL_RADIUS = 42.0
    best_off, best_val = None, -1.0
    for off, val in cands:
        if MIN_REAL_RADIUS <= val <= MAX_REAL_RADIUS and val > best_val:
            best_off, best_val = off, val

    if best_off is not None:
        return (best_off, best_val)

    for off, val in reversed(cands):
        if MIN_REAL_RADIUS <= val <= MAX_REAL_RADIUS:
            return (off, val)

    # last-resort: last candidate
    return cands[-1] if cands else (None, None)


def format_candidate_list(cands: list[tuple[int, float]], max_show: int = 4) -> str:
    parts: list[str] = []
    for idx, (_off, val) in enumerate(cands[:max_show]):
        parts.append(f"r{idx}={val:.1f}")
    if len(cands) > max_show:
        parts.append("...")
    return " ".join(parts)


def parse_hit_reaction_input(s: str) -> int | None:
    """
    Accepts:
      - Hex with 0x prefix: "0x800080"
      - Hex without prefix: "800080"
      - Decimal: "524288"
    """
    s = (s or "").strip()
    if not s:
        return None

    # Try hex first (both with and without 0x)
    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
        # if user typed only digits but intends decimal, they can still use decimal fallback
        return int(s, 16)
    except ValueError:
        pass

    try:
        return int(s, 10)
    except ValueError:
        return None


def fmt_superbg(v: Any) -> str:
    if v is None:
        return ""
    return "ON" if int(v) == 0x04 else "OFF"


def fmt_kb_traj_ui(val: int | None) -> str:
    if val is None:
        return ""
    desc = KB_TRAJ_MAP.get(val, "Unknown")
    return f"0x{val:02X} ({desc})"


def fmt_hit_reaction_ui(val: int | None) -> str:
    if val is None:
        return ""
    desc = HIT_REACTION_MAP.get(val, "Unknown")
    return f"0x{val:06X} ({desc})"


def ensure_int(s: str, default: int = 0) -> int:
    try:
        return int(s)
    except Exception:
        return default


def ensure_range_pair(text: str, default_s: int, default_e: int) -> tuple[int, int]:
    t = (text or "").strip()
    if "-" in t:
        a, b = t.split("-", 1)
        try:
            return int(a), int(b)
        except Exception:
            return default_s, default_e
    return default_s, default_e
