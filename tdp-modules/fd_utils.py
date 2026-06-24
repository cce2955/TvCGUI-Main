# fd_utils.py
#
# Small, pure helpers and thin The implementation/O wrappers for the FD editor UI.
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
        write_ground_knockback,
        write_ground_knockback_y,
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
# Scan a region for the suffix:
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
    "\n    This helper tries the safe variants without ever throwing.\n\n    Prefer the character-specific table before the generic/global table.  The\n    CSV intentionally reuses IDs across characters, and also reuses low IDs such\n    as 0x010C for both global air-normal helpers and character specials.  If the module\n    fall back to generic first, Ryu's internal Hado row becomes j.B (Second),\n    Chun rows can become unrelated global normals, and family sorting gets\n    pulled out of order.\n    "
    if char_name:
        char_id = None
        try:
            char_id = CHAR_ID_CORRECTION.get(char_name)  # type: ignore[arg-type]
        except Exception:
            char_id = None
        if char_id is not None:
            try:
                s = lookup_move_name(anim_id, char_id)
                if s:
                    return s
            except TypeError:
                pass
            except Exception:
                pass

        # Compatibility for any older local lookup helper that accepted
        # (char_name, anim_id).  Current move_id_map.py wants (anim_id, char_id),
        # so this stays after the known-good path.
        try:
            s = lookup_move_name(char_name, anim_id)
            if s:
                return s
        except TypeError:
            pass
        except Exception:
            pass

    # Fallback: global/generic lookup.
    try:
        s = lookup_move_name(anim_id)
        if s:
            return s
    except TypeError:
        pass
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




def _fmt_float_trim(v: Any) -> str:
    if v is None:
        return ""
    try:
        f = float(v)
    except Exception:
        return str(v)
    if not math.isfinite(f):
        return str(v)
    txt = f"{f:.3f}".rstrip("0").rstrip(".")
    if txt == "-0":
        txt = "0"
    return txt


def fmt_knockback_packet_ui(mv: dict | None) -> str:
    if not mv:
        return ""
    if mv.get("knockback_addr") is None:
        return ""
    parts: list[str] = []
    kt = mv.get("kb_type")
    if kt is not None:
        try:
            parts.append(f"Type {int(kt) & 0xFF}")
        except Exception:
            parts.append(f"Type {kt}")
    prof = mv.get("launch_profile")
    if prof is not None:
        try:
            if int(prof) != 0:
                parts.append(f"Extra Launch {int(prof) & 0xFFFFFFFF}")
        except Exception:
            parts.append(f"Extra Launch {prof}")
    unk = mv.get("kb_unknown")
    try:
        if unk not in (None, 0):
            parts.append(f"Mod {int(unk) & 0xFFFFFFFF}")
    except Exception:
        if unk:
            parts.append(f"Mod {unk}")
    if mv.get("kb_x") is not None:
        parts.append(f"AirKBX{_fmt_float_trim(mv.get('kb_x'))}")
    if mv.get("air_kb") is not None:
        parts.append(f"AirKBY{_fmt_float_trim(mv.get('air_kb'))}")
    return " ".join(parts)



def fmt_kb_type_ui(mv: dict | None) -> str:
    if not mv or mv.get("knockback_addr") is None:
        return ""
    kt = mv.get("kb_type")
    if kt is None:
        return ""
    try:
        return str(int(kt) & 0xFF)
    except Exception:
        return str(kt)


def fmt_launch_profile_ui(mv: dict | None) -> str:
    if not mv or mv.get("knockback_addr") is None:
        return ""
    prof = mv.get("launch_profile")
    if prof is None:
        return ""
    try:
        return str(int(prof) & 0xFFFFFFFF)
    except Exception:
        return str(prof)


def fmt_kb_unknown_ui(mv: dict | None) -> str:
    if not mv or mv.get("knockback_addr") is None:
        return ""
    unk = mv.get("kb_unknown")
    if unk is None:
        return ""
    try:
        return str(int(unk) & 0xFFFFFFFF)
    except Exception:
        return str(unk)


def fmt_ground_kb_ui(mv: dict | None) -> str:
    """Format 35/0C +0x08, the confirmed signed hit Push/Pull X scalar."""
    if not mv or mv.get("ground_kb_addr") is None:
        return ""
    return _fmt_float_trim(mv.get("ground_kb"))


def fmt_ground_kb_y_ui(mv: dict | None) -> str:
    """Format 35/0C +0x0C, the unclassified Push/Pull Aux scalar."""
    if not mv or mv.get("ground_kb_y_addr") is None:
        return ""
    return _fmt_float_trim(mv.get("ground_kb_y"))


def fmt_kb_x_ui(mv: dict | None) -> str:
    if not mv or mv.get("knockback_addr") is None:
        return ""
    return _fmt_float_trim(mv.get("kb_x"))


def fmt_air_kb_ui(mv: dict | None) -> str:
    if not mv or mv.get("knockback_addr") is None:
        return ""
    return _fmt_float_trim(mv.get("air_kb"))




def fmt_u32_decimal_ui(v: Any) -> str:
    if v is None:
        return ""
    try:
        return str(int(v) & 0xFFFFFFFF)
    except Exception:
        return str(v)


def fmt_hit_spark_ui(mv: dict | None) -> str:
    if not mv or mv.get("hit_spark_addr") is None:
        return ""
    return fmt_u32_decimal_ui(mv.get("hit_spark"))


def fmt_stretch_part_ui(mv: dict | None) -> str:
    if not mv or mv.get("stretch_packet_addr") is None:
        return ""
    return fmt_u32_decimal_ui(mv.get("stretch_part"))


def fmt_stretch_len_ui(mv: dict | None) -> str:
    if not mv or mv.get("stretch_packet_addr") is None:
        return ""
    return _fmt_float_trim(mv.get("stretch_len"))


def fmt_stretch_width_ui(mv: dict | None) -> str:
    if not mv or mv.get("stretch_packet_addr") is None:
        return ""
    return _fmt_float_trim(mv.get("stretch_width"))


def fmt_stretch_height_ui(mv: dict | None) -> str:
    if not mv or mv.get("stretch_packet_addr") is None:
        return ""
    return _fmt_float_trim(mv.get("stretch_height"))


def fmt_stretch_time_ui(mv: dict | None) -> str:
    if not mv or mv.get("stretch_packet_addr") is None:
        return ""
    return fmt_u32_decimal_ui(mv.get("stretch_time"))


def fmt_post_link_ui(mv: dict | None) -> str:
    if not mv or mv.get("post_link_addr") is None:
        return ""
    return fmt_u32_decimal_ui(mv.get("post_link"))

def fmt_hit_reaction_ui(val: int | None) -> str:
    if val is None:
        return ""
    desc = HIT_REACTION_MAP.get(val, "Unknown")
    return f"0x{val:06X} ({desc})"

# ============================================================
# Projectile strength-slice resolver (A6 F0 anchors)
#
# Dumps show repeating anchor bytes "A6 F0" inside the move block.
# Treat each occurrence as a "strength slice" in ascending order:
#   slice[0] -> L
#   slice[1] -> M
#   slice[2] -> H/C
#
# Store:
#   mv["proj_slices"] = [abs_addr0, abs_addr1, ...]
#   mv["proj_slice"]  = selected abs addr for this row's strength
# ============================================================

_A6F0 = b"\xA6\xF0"


def infer_strength_index_from_name(move_name: str) -> int | None:
    """
    Returns:
      0 for Light (L)
      1 for Medium (M)
      2 for Heavy/Capcom (H/C)
    """
    if not move_name:
        return None
    s = move_name.lower()

    # Strongest match: explicit token at end or surrounded by punctuation.
    # Keep this intentionally simple.
    if " hado" in s or "kiko" in s or "hado" in s or "kiko" in s or "kik" in s:
        pass  # allow parsing suffix below
    else:
        # Only apply to obvious projectile specials; expand if needed.
        return None

    # Light
    if " l" in s or s.endswith(" l") or s.endswith("l]") or s.endswith("l"):
        return 0
    # Medium
    if " m" in s or s.endswith(" m") or s.endswith("m]") or s.endswith("m"):
        return 1
    # Heavy / Capcom "C"
    if " h" in s or s.endswith(" h") or s.endswith("h]") or s.endswith("h"):
        return 2
    if " c" in s or s.endswith(" c") or s.endswith("c]") or s.endswith("c"):
        return 2

    return None


def resolve_projectile_strength_slices_for_move(
    mv: dict,
    *,
    region_abs: int | None = None,
    region_size: int = 0x1400,
    rbytes_func: Callable[[int, int], bytes] | None = None,
    strength_index: int | None = None,
    move_name_for_strength: str | None = None,
) -> bool:
    """
    Populate:
      - mv["proj_slices"]: list[int] of absolute addresses where "A6 F0" occurs
      - mv["proj_slice"]:  selected slice for this mv based on strength_index

    strength_index:
      0=L, 1=M, 2=H/C
    """
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

    if not buf or len(buf) < 2:
        return False

    # Collect all A6F0 occurrences (absolute addresses), in ascending order.
    slices: list[int] = []
    pos = 0
    while True:
        j = buf.find(_A6F0, pos)
        if j < 0:
            break
        slices.append(base + j)
        pos = j + 1  # allow overlaps (doesn't matter here)

    if not slices:
        return False

    mv["proj_slices"] = slices

    # Determine which slice to bind to this mv row
    idx = strength_index
    if idx is None and move_name_for_strength:
        idx = infer_strength_index_from_name(move_name_for_strength)

    if idx is not None:
        if 0 <= idx < len(slices):
            mv["proj_slice"] = slices[idx]
        else:
            # If do not have enough slices, clamp to last.
            mv["proj_slice"] = slices[-1]
    else:
        # No strength requested: leave mv["proj_slice"] unset, but keep list.
        mv.pop("proj_slice", None)

    return True

def resolve_projectile_radius_for_move(
    mv: dict,
    *,
    region_abs: int | None = None,
    rbytes_func: Callable[[int, int], bytes] | None = None,
) -> bool:
    """
    Populate:
      - mv["proj_radius"]:      float
      - mv["proj_radius_addr"]: int (absolute address of the radius float)

    Scans from region_abs (or mv["abs"]) for the projectile template
    radius signature defined in fd_patterns.
    """
    if mv.get("proj_radius") is not None:
        return True

    base = int(region_abs) if region_abs is not None else int(mv.get("abs") or 0)
    if not base:
        return False

    if rbytes_func is None:
        try:
            from dolphin_io import rbytes as rbytes_func  # type: ignore
        except Exception:
            return False

    try:
        from fd_patterns import find_projectile_radius_addr
        addr, r = find_projectile_radius_addr(base, rbytes_func)
    except Exception:
        return False

    if addr is None or r is None:
        return False

    mv["proj_radius_addr"] = addr
    mv["proj_radius"] = r
    return True


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

HIT_RESULT_OTG_OFF = 0x00000000
HIT_RESULT_OTG_ON = 0x00004000
HIT_RESULT_REACTION_FAMILY_MIN = 0x00004100


def fmt_hit_result_flags_ui(value_or_mv) -> str:
    """Format the verified +0x240 hit-result flag slot for the FD grid."""
    if isinstance(value_or_mv, dict):
        v = value_or_mv.get("hit_result_flags")
    else:
        v = value_or_mv
    if v is None or v == "":
        return ""
    try:
        vv = int(v) & 0xFFFFFFFF
    except Exception:
        return str(v)

    if vv == HIT_RESULT_OTG_OFF:
        label = "OTG off"
    elif vv == HIT_RESULT_OTG_ON:
        label = "OTG on"
    elif vv >= HIT_RESULT_REACTION_FAMILY_MIN and (vv & HIT_RESULT_OTG_ON):
        label = "OTG+reaction"
    elif vv & HIT_RESULT_OTG_ON:
        label = "OTG on?"
    else:
        label = "custom"
    return f"0x{vv:08X} {label}"
