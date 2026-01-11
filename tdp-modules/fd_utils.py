# fd_utils.py
#
# Small, pure helpers and a few thin I/O wrappers used by the FD editor UI.
# Keep this file UI-free.

from __future__ import annotations

import math
from typing import Any, Iterable, Callable

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

# ============================================================
# Projectile quick resolver (ProjDmg / ProjTpl)
#
# Minimal heuristic:
#   00 00 XX YY 00 00 00 0C FF FF FF FF
#
# We scan a region for the suffix:
#   00 00 00 0C FF FF FF FF
# then take the 4 bytes immediately before it as the candidate u32.
# The low halfword (XXYY) is treated as damage (big-endian).
#
# proj_tpl is set to the absolute address of the candidate damage word.
# ============================================================

_PROJ_SUFFIX = b"\x00\x00\x00\x0C\xFF\xFF\xFF\xFF"


def resolve_projectile_fields_for_move(
    mv: dict,
    *,
    region_abs: int | None = None,
    region_size: int = 0x1400,
    rbytes_func: Callable[[int, int], bytes] | None = None,
) -> bool:
    """
    Populate:
      - mv["proj_dmg"]: int (low 16 bits of the candidate u32)
      - mv["proj_tpl"]: int (absolute address of the candidate u32)

    IMPORTANT:
      If multiple candidates exist (strength slices), choose the candidate whose
      anchor address is closest to mv["abs"] (or region_abs).
    """
    if mv.get("proj_dmg") is not None and mv.get("proj_tpl") is not None:
        return True

    base = int(region_abs) if region_abs is not None else int(mv.get("abs") or 0)
    if not base:
        return False

    size = int(region_size or 0)
    if size <= 0:
        return False
    size = max(0x200, min(size, 0x6000))

    if rbytes_func is None:
        try:
            from dolphin_io import rbytes as rbytes_func  # type: ignore
        except Exception:
            return False

    try:
        buf = rbytes_func(base, size)
    except Exception:
        return False

    if not buf or len(buf) < 12:
        return False

    # collect all candidates
    cands: list[tuple[int, int, int]] = []  # (addr_damage, dmg16, addr_marker)
    pos = 0
    while True:
        j = buf.find(_PROJ_SUFFIX, pos)
        if j < 0:
            break
        pos = j + 1

        if j < 4:
            continue

        cand = buf[j - 4 : j]  # expected 00 00 XX YY
        if len(cand) != 4:
            continue
        if cand[0] != 0x00 or cand[1] != 0x00:
            continue

        u32 = int.from_bytes(cand, "big", signed=False)
        dmg16 = int(u32 & 0xFFFF)
        addr_damage = base + (j - 4)
        addr_marker = base + j
        cands.append((addr_damage, dmg16, addr_marker))

    if not cands:
        return False

    prefer = int(mv.get("abs") or base)
    addr_damage, dmg16, addr_marker = min(cands, key=lambda t: abs(t[0] - prefer))

    mv["proj_dmg"] = dmg16
    mv["proj_tpl"] = addr_damage
    mv["proj_marker"] = addr_marker
    return True


def _try_lookup_move_name(char_name: str | None, anim_id: int) -> str | None:
    """
    
    This helper tries the safe variants without ever throwing.
    """
    # Preferred: (char_name, anim_id)
    if char_name:
        try:
            s = lookup_move_name(char_name, anim_id)
            if s:
                return s
        except TypeError:
            pass
        except Exception:
            pass

    # Fallback: (anim_id) or (anim_id, char_id)
    try:
        s = lookup_move_name(anim_id)
        if s:
            return s
    except TypeError:
        pass
    except Exception:
        pass

    if char_name:
        # If someone passed char_name but lookup expects an int char_id, try deriving.
        char_id = None
        try:
            # CHAR_ID_CORRECTION is not guaranteed to map names; guard it
            char_id = CHAR_ID_CORRECTION.get(char_name)  # type: ignore[arg-type]
        except Exception:
            char_id = None
        if char_id is not None:
            try:
                s = lookup_move_name(anim_id, char_id)
                if s:
                    return s
            except Exception:
                pass

    return None


def pretty_move_name(anim_id: int | None, char_name: str | None = None) -> str:
    if anim_id is None:
        return "anim_--"

    anim_id_i = int(anim_id)

    name = _try_lookup_move_name(char_name, anim_id_i)
    if name:
        return name

    # small-ID fallback probing
    if anim_id_i < 0x100:
        for high in (0x100, 0x200, 0x300):
            name = _try_lookup_move_name(char_name, anim_id_i + high)
            if name:
                return name

    name = _ANIM_MAP_FOR_GUI.get(anim_id_i)
    if name:
        return name

    return f"anim_{anim_id_i:04X}"


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


def fmt_speed_mod_ui(v: int | None) -> str:
    if v is None:
        return ""
    vv = int(v) & 0xFF
    return f"{vv} (0x{vv:02X})"


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

    try:
        if s.lower().startswith("0x"):
            return int(s, 16)
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
