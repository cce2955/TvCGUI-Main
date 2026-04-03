from __future__ import annotations
import json, struct, threading, tkinter as tk
from tkinter import ttk, simpledialog, messagebox
from tk_host import tk_call

try:
    from dolphin_io import rbytes, wbytes
except Exception:
    rbytes = None
    wbytes = None
SUPER_STRUCT_SIG = b"\x00\x00\x0C\x00\x00\x00\x23\x00"
SUPER_VERIFY_A   = b"\x00\x00\x04\x00\x00\x00\xFF\xFF\xFF\xFF"
SUPER_VERIFY_B   = b"\x3F\x80\x00\x00"
SUPER_VERIFY_LOOK = 0x120
_SUPER_STRUCT_DMG_OFF = 0x09

# ---------------------------------------------------------------------------
# Scan parameters
# ---------------------------------------------------------------------------
# The suffix we search for in the template/template2 path.
# Full 12-byte signature:  00 00 XX YY  00 00 00 0C  <discriminator>
# We find _SUFFIX, then look 4 bytes back for the damage word.
_SUFFIX    = b"\x00\x00\x00\x0C"
SCAN_START = 0x90000000
SCAN_END   = 0x94000000
SCAN_BLOCK = 0x40000
PROJ_MAP_FILE = "projectilemap.json"
PROJ_IDS_FILE = "projectile_ids.json"

# ---------------------------------------------------------------------------
# Field offsets — corrected against MEM2 analysis notes
# ---------------------------------------------------------------------------
# All offsets are relative to the record base (the 00 00 XX YY word, i.e.
# 4 bytes before _SUFFIX).
#
# Notes-confirmed corrections vs. previous version:
#   type     0x050 (u16) → 0x051 (u8)   [notes: u8 @ +0x51]
#   lifetime 0x05A (u16) → 0x05B (u8)   [notes: u8 @ +0x5B]
#   aerial_kb_x/y renamed kb_x/kb_y — the notes confirm these are general
#     knockback components, not aerial-specific.
#
# Validation-gate constants (checked before any field is read):
#   u16 @ +0x42 must == 10
#   u32 @ +0x04 must == 0x0000000C      (already implied by _SUFFIX hit)
#   f32 @ +0x84 must == 1.0
#   f32 @ +0x8C must == 100.0
#   u16 @ +0x6E must == 1024
#
# Discriminator at u32 @ +0x08:
#   0xFFFFFFFF          → "template"
#   0x00000000 or 0x01  → "template2"
#   anything else       → "script(0xNN)"  where NN = high byte of c[0]

FIELD_OFFSETS = {
    "radius":   0x02C,  # f32 — hitbox radius (notes: consistently 1.0)
    "kb_x":     0x024,  # f32 — knockback X component  (was aerial_kb_x)
    "kb_y":     0x028,  # f32 — knockback Y component  (was aerial_kb_y)
    "c042":     0x042,  # u16 — validation constant, always 10
    "type":     0x051,  # u8  — type family: 3=Linear, 4=Physics  (CORRECTED +0x050→+0x051)
    "id":       0x052,  # u16 — projectile type ID
    "lifetime": 0x05B,  # u8  — active frames / lifetime           (CORRECTED +0x05A→+0x05B)
    "hb_size":  0x06E,  # u16 — hitbox size (validation constant: 1024)
    "speed":    0x080,  # f32 — speed scalar
    "accel":    0x084,  # f32 — validation constant, always 1.0
    "hitbox":   0x08C,  # f32 — hitbox radius (validation constant: 100.0)
    "arc":      0x090,  # f32 — arc/gravity (Roll-specific)
    "arc2":     0x094,  # f32 — arc modifier (Roll-specific)
    "vel2_x":   0x0D4,
    "vel2_y":   0x0D8,
    "vel2_s":   0x0DC,
    "u01": 0x10,
    "u02": 0x14,
    "u03": 0x18,
    "u04": 0x42,
    "u05": 0x48,
    "u06": 0x52,
    "u07": 0x5A,
    "u08": 0x68,
    "u09": 0x72,
}

# Validation-gate field addresses (relative to record base)
_VALID_C042    = 0x042   # u16 == 10
_VALID_ACCEL   = 0x084   # f32 == 1.0
_VALID_HITBOX  = 0x08C   # f32 == 100.0
_VALID_HBSIZE  = 0x06E   # u16 == 1024
_DISCRIMINATOR = 0x008   # u32: 0xFFFFFFFF=template, 0/1=template2

# ---------------------------------------------------------------------------
# Character / signature tables
# ---------------------------------------------------------------------------
_NAME_TO_KEY = {
    "Ryu": "RYU", "Chun-Li": "CHUN", "Jun the Swan": "JUN",
    "Ken the Eagle": "KEN", "Alex": "ALEX", "Batsu": "BATSU",
    "Frank West": "FRANK", "Volnutt": "VOLNUTT", "Morrigan": "MORRIGAN",
    "Roll": "ROLL", "Saki": "SAKI", "Viewtiful Joe": "VJOE",
    "Zero": "ZERO", "Casshan": "CASSHAN", "Doronjo": "DORONJO",
    "Ippatsuman": "IPPATSMAN", "Joe the Condor": "JOE",
    "Tekkaman": "TEKKAMAN", "Tekkaman Blade": "BLADE",
    "Yatterman-1": "YATTER1", "Yatterman-2": "YATTER2",
    "Gold Lightan": "LIGHTAN", "PTX-40A": "PTX",
}

CHAR_SIGS = {
    "KEN": [b"\x00\x00\x00\x09"],
    "RYU": [b"\x00\x04\x01\x02"],
    "JUN": [b"\x00\x04\x00\x82", b"\x00\x00\x01\x0C"],
}

CHAR_SIG_OFFSETS = {
    "KEN": "pre",
    "RYU": "c",
    "JUN": "c",
}

_SIG_C_TO_KEYS:   dict[bytes, list[str]] = {}
_SIG_PRE_TO_KEYS: dict[bytes, list[str]] = {}

for _k, _sigs in CHAR_SIGS.items():
    _target = _SIG_PRE_TO_KEYS if CHAR_SIG_OFFSETS.get(_k) == "pre" else _SIG_C_TO_KEYS
    for _s in _sigs:
        _target.setdefault(_s, []).append(_k)

# ---------------------------------------------------------------------------
# Script opcode table
# ---------------------------------------------------------------------------
SCRIPT_OPCODES: dict[bytes, dict] = {
    b"\x05\x2B": {
        "fmt_name":   "script(0x052B)",
        "dmg_offset": 4,
    },
}

def _dmg_write_offset(fmt: str) -> int:
    for info in SCRIPT_OPCODES.values():
        if info["fmt_name"] == fmt:
            return info["dmg_offset"]
    return 2  # default for template / template2

# ---------------------------------------------------------------------------
# chr_tbl param table — authoritative damage addresses for script-mode hits
#
# Each slot has a 16-byte parameter-entry table at a fixed offset from its
# chr_tbl_base.  The damage is a u32 big-endian at entry+0x00 (NOT u16 at +2).
#
#   chr_tbl_base + 0x25E0  →  u32 damage for the 2400-class entry (Spree/Attack)
#   chr_tbl_base + 0x2640  →  u32 damage for the 3200-class entry (Fall)
#
# Per-slot exact addresses (precomputed for fast lookup):
#   slot 0  chr_tbl 0x90896640  →  spree 0x90898C20  / fall 0x90898C80
#   slot 1  chr_tbl 0x908F1920  →  spree 0x908F3F00  / fall 0x908F3F60
#   slot 2  chr_tbl 0x909478E0  →  spree 0x90949EC0  / fall 0x90949F20
#   slot 3  chr_tbl 0x9099D9C0  →  spree 0x9099FFA0  / fall 0x909A0000
#
# Ownership: a hit address belongs to a slot when
#   chr_tbl_base <= hit_addr < chr_tbl_base + _CHR_TBL_SLOT_SIZE
# ---------------------------------------------------------------------------
_SCRIPT_DMG_OFFSETS: dict[int, int] = {
    2400: 0x25E0,   # u32 at entry start (Spree/Attack)
    3200: 0x2640,   # u32 at entry start (Fall)
}

_CHR_TBL_BASES = [
    0x90896640,   # slot 0
    0x908F1920,   # slot 1  (owns Frank West / Zombie addresses)
    0x909478E0,   # slot 2
    0x9099D9C0,   # slot 3
]
_CHR_TBL_SLOT_SIZE = 0x80000

# Frank West's character ID in the game's roster.
FRANK_CHAR_ID = 30   # 0x1E

# Fighter base addresses for each slot — char_id is read from base + 0x14.
# These are the same bases established in the slot/scanner work.
_FIGHTER_BASES = [
    0x9246B9C0,   # slot 0
    0x927EB9E0,   # slot 1
    0x92B6BA00,   # slot 2
    0x92EEBA20,   # slot 3
]
_CHAR_ID_OFF = 0x14   # offset within fighter base where char_id (u32 BE) lives

# chr_tbl_base → slot index, for mapping ownership back to fighter bases
_CHR_TBL_TO_SLOT = {
    0x90896640: 0,
    0x908F1920: 1,
    0x909478E0: 2,
    0x9099D9C0: 3,
}


def _read_slot_char_ids() -> dict[int, int]:
    """
    Read the live char_id for each slot from its fighter base + 0x14.
    Returns a dict of {chr_tbl_base: char_id}.
    Returns an empty dict if rbytes is unavailable.
    """
    result: dict[int, int] = {}
    if rbytes is None:
        return result
    for slot_idx, (chr_tbl_base, _, _) in enumerate(_CHR_TBL_RANGES):  # noqa
        fighter_base = _FIGHTER_BASES[slot_idx]
        try:
            b = rbytes(fighter_base + _CHAR_ID_OFF, 4)
            if b and len(b) == 4:
                cid = struct.unpack(">I", b)[0]
                print(f"[slot-char] slot={slot_idx} chr_tbl=0x{chr_tbl_base:08X} fighter=0x{fighter_base:08X} cid={cid}")
                result[chr_tbl_base] = cid
        except Exception:
            pass
    return result


def _resolve_script_dmg_addr(hit_addr: int, dmg: int) -> int | None:
    """
    For a script-cluster hit, return the authoritative u32 param-table address
    for this damage value within the owning slot's chr_tbl region.
    Returns None if no mapping exists or no slot owns hit_addr.
    """
    off = _SCRIPT_DMG_OFFSETS.get(dmg)
    if off is None:
        return None
    base = _owning_chr_tbl(hit_addr)
    if base is None:
        return None
    return base + off


def _write_u32(addr: int, val: int) -> bool:
    """Write a big-endian u32 — used for the zombie param-table entries."""
    if wbytes is None:
        return False
    try:
        return bool(wbytes(addr, struct.pack(">I", val)))
    except Exception as e:
        print(f"[proj_scanner] write u32 failed: {e}")
        return False

# ---------------------------------------------------------------------------
# Template format classification
#
# Uses the u32 discriminator at base+0x08, exactly as the notes specify:
#   0xFFFFFFFF          → "template"
#   0x00000000 / 0x01   → "template2"
#   anything else       → "script(0xNN)"
# ---------------------------------------------------------------------------
def _classify_discriminator(after4: bytes) -> str:
    """after4 = 4 bytes starting at base+0x08 (the 4 bytes after _SUFFIX)."""
    if len(after4) < 4:
        return "script(?)"
    disc = struct.unpack_from(">I", after4)[0]
    if disc == 0xFFFFFFFF:
        return "template"
    if disc in (0x00000000, 0x00000001):
        return "template2"
    return f"script(0x{(after4[0]):02X})"


# ---------------------------------------------------------------------------
# Validation gate for template / template2 records
#
# All five conditions from the notes must hold before any field is trusted.
# Returns True only if the record passes every check.
# ---------------------------------------------------------------------------
def _validate_template(data: bytes, base_off: int) -> bool:
    """
    base_off is the offset within `data` of the record base
    (the 00 00 XX YY word, i.e. 4 bytes before the _SUFFIX match).

    Relaxed checks:
      1. u16 @ +0x42 == 10
      2. f32 @ +0x84 == 1.0 OR 0.75
      3. f32 @ +0x8C == 100.0
      4. u16 @ +0x6E == 1024
      5. u32 @ +0x08 is a known discriminator (0xFFFFFFFF, 0, or 1)
    """
    end = len(data)

    def u16(off: int) -> int | None:
        o = base_off + off
        if o + 2 > end:
            return None
        return struct.unpack_from(">H", data, o)[0]

    def u32(off: int) -> int | None:
        o = base_off + off
        if o + 4 > end:
            return None
        return struct.unpack_from(">I", data, o)[0]

    def f32(off: int) -> float | None:
        o = base_off + off
        if o + 4 > end:
            return None
        return struct.unpack_from(">f", data, o)[0]

    if u16(_VALID_C042) != 10:
        return False

    accel = f32(_VALID_ACCEL)
    if accel is None:
        return False
    if not (abs(accel - 1.0) <= 1e-4 or abs(accel - 0.75) <= 1e-4):
        return False

    hb = f32(_VALID_HITBOX)
    if hb is None or abs(hb - 100.0) > 0.1:
        return False

    if u16(_VALID_HBSIZE) != 1024:
        return False

    disc = u32(_DISCRIMINATOR)
    if disc not in (0xFFFFFFFF, 0x00000000, 0x00000001):
        return False

    return True

    
# ---------------------------------------------------------------------------
# Clustering helpers
#
# Groups validated template records by (proj_id, type_family) first, then by
# (damage, speed, kb_x, kb_y) to identify tiers/variants — exactly the
# workflow described in the notes.
# ---------------------------------------------------------------------------
def _cluster_key(h: dict) -> tuple:
    """Primary cluster key: (proj_id, type_family)."""
    try:
        pid = int(h.get("id", 0))
    except (ValueError, TypeError):
        pid = 0
    try:
        tf = int(h.get("type", 0))
    except (ValueError, TypeError):
        tf = 0
    return (pid, tf)


def _tier_key(h: dict) -> tuple:
    """Secondary cluster key within a family: (damage, speed_rounded, kb_x_rounded, kb_y_rounded)."""
    def _f(v, decimals=1):
        try:
            return round(float(v), decimals)
        except (ValueError, TypeError):
            return 0.0
    return (h.get("dmg", 0), _f(h.get("speed")), _f(h.get("kb_x")), _f(h.get("kb_y")))


def _annotate_clusters(hits: list[dict]) -> None:
    """
    Tag each template/template2 hit with a 'cluster' string of the form
    'ID:0xNNNN TF:N tier:M/T' so the UI can show grouping without a
    separate column explosion.

    Script/opcode hits are tagged 'script' — no cluster analysis.
    """
    from collections import defaultdict

    # Separate template hits from opcode hits
    tmpl_hits  = [h for h in hits if h.get("fmt") in ("template", "template2")]
    other_hits = [h for h in hits if h not in tmpl_hits]

    # Group by primary key
    families: dict[tuple, list[dict]] = defaultdict(list)
    for h in tmpl_hits:
        families[_cluster_key(h)].append(h)

    for ck, members in families.items():
        pid, tf = ck
        # Group by tier within the family
        tiers: dict[tuple, list[dict]] = defaultdict(list)
        for h in members:
            tiers[_tier_key(h)].append(h)
        total_tiers = len(tiers)
        tier_num    = 0
        for tk_, tier_members in tiers.items():
            tier_num += 1
            label = f"ID:0x{pid:04X} TF:{tf} tier:{tier_num}/{total_tiers}"
            for h in tier_members:
                h["cluster"] = label

    for h in other_hits:
        h["cluster"] = "script"


# ---------------------------------------------------------------------------
# Helper lookup builders
# ---------------------------------------------------------------------------
def _keys_for_block(c_word: bytes, pre_word: bytes) -> list[str]:
    keys = set()
    keys.update(_SIG_C_TO_KEYS.get(bytes(c_word), []))
    keys.update(_SIG_PRE_TO_KEYS.get(bytes(pre_word), []))
    return list(keys)


def _load_map():
    try:
        with open(PROJ_MAP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[proj_scanner] {e}")
        return {}

def _load_ids():
    try:
        with open(PROJ_IDS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[proj_scanner] {e}")
        return {}
def _build_char_damage_map(proj_map):
    out = {}
    for key, moves in proj_map.items():
        dmg_map = {}
        for entry in moves:
            dmg = int(entry.get("dmg", 0))
            if dmg:
                dmg_map.setdefault(dmg, []).append(entry.get("move", "?"))
        out[key] = dmg_map
    return out


# Fill this with your live roster IDs.
# Example: FRANK is already known to be 30.
# Live roster char_id -> projectile-map key
CHAR_ID_TO_KEY = {
    1:  "RYU",
    2:  "CHUN",
    3:  "ALEX",
    4:  "ROLL",
    5:  "MORRIGAN",
    6:  "PTX",
    7:  "BATSU",
    8:  "SAKI",
    9:  "KEN",
    10: "JUN",
    11: "LIGHTAN",
    12: "TEKKAMAN",
    13: "DORONJO",
    14: "YATTER1",
    15: "CASSHAN",
    16: "ZERO",
    17: "YATTER2",
    18: "IPPATSMAN",
    19: "VJOE",
    20: "JOE",
    21: "BLADE",
    22: "VOLNUTT",
    30: "FRANK",
}
def _build_lookup(proj_map, active_keys):
    lookup = {}
    for key, moves in proj_map.items():
        if key not in active_keys:
            continue
        for entry in moves:
            dmg = int(entry.get("dmg", 0))
            if dmg:
                lookup.setdefault(dmg, []).append((key, entry.get("move", "?")))
    return lookup




# ---------------------------------------------------------------------------
# Memory helpers (live Dolphin reads)
# ---------------------------------------------------------------------------
def _read_u8(addr: int) -> str:
    if rbytes is None: return "?"
    try:
        b = rbytes(addr, 1)
        return str(b[0]) if b and len(b) == 1 else "?"
    except Exception:
        return "?"

def _read_u16(addr: int) -> str:
    if rbytes is None: return "?"
    try:
        b = rbytes(addr, 2)
        return str((b[0] << 8) | b[1]) if b and len(b) == 2 else "?"
    except Exception:
        return "?"

def _read_u16_hex(addr: int) -> str:
    if rbytes is None: return "?"
    try:
        b = rbytes(addr, 2)
        if b and len(b) == 2:
            return f"0x{(b[0] << 8) | b[1]:04X}"
    except Exception:
        pass
    return "?"

def _read_f32(addr: int) -> str:
    if rbytes is None: return "?"
    try:
        b = rbytes(addr, 4)
        if b and len(b) == 4:
            return f"{struct.unpack('>f', b)[0]:.4f}"
    except Exception:
        pass
    return "?"

def _write_u16(addr: int, val: int) -> bool:
    if wbytes is None: return False
    try:
        return bool(wbytes(addr, bytes([(val >> 8) & 0xFF, val & 0xFF])))
    except Exception as e:
        print(f"[proj_scanner] write u16 failed: {e}")
        return False

def _write_f32(addr: int, val: float) -> bool:
    if wbytes is None: return False
    try:
        return bool(wbytes(addr, struct.pack(">f", val)))
    except Exception as e:
        print(f"[proj_scanner] write f32 failed: {e}")
        return False

def _write_dmg(addr: int, new_dmg: int, fmt: str) -> bool:
    return _write_u16(addr + _dmg_write_offset(fmt), new_dmg)


# ---------------------------------------------------------------------------
# Blank fields for opcode-scan hits
# ---------------------------------------------------------------------------
_OPCODE_HIT_FIELDS = {
    "radius": "?", "speed": "?", "accel": "?", "kb_x": "?", "kb_y": "?",
    "arc": "?", "arc2": "?", "hitbox": "?", "type": "?", "id": "?",
    "spawn_x": "?",
    "spawn_y": "?",
    "lifetime": "?", "hb_size": "?",
    "vel2_x": "?", "vel2_y": "?", "vel2_s": "?",
    "u01": "?", "u02": "?", "u03": "?",
    "u04": "?", "u05": "?", "u06": "?",
    "u07": "?", "u08": "?", "u09": "?",
    "cluster": "script",
}


# ---------------------------------------------------------------------------
# Opcode-scan pass  (05 2B and any future SCRIPT_OPCODES entries)
# ---------------------------------------------------------------------------
def _scan_opcode_blocks(data: bytes, base_addr: int, hits: list, lookup: dict,
                        slot_char_ids: dict | None = None) -> None:
    frank_bases = {
        base for base, cid in (slot_char_ids or {}).items() if cid == FRANK_CHAR_ID
    }
    for sig, info in SCRIPT_OPCODES.items():
        fmt_name   = info["fmt_name"]
        dmg_offset = info["dmg_offset"]

        pos = 0
        while True:
            idx = data.find(sig, pos)
            if idx < 0:
                break
            pos = idx + 1

            if idx + dmg_offset + 2 > len(data):
                continue

            dmg = (data[idx + dmg_offset] << 8) | data[idx + dmg_offset + 1]
            if dmg < 500 or dmg > 20000:
                continue

            addr = base_addr + idx
            extra = {
                "preA":   _read_u8(addr - 2),
                "preB":   _read_u8(addr - 1),
                "opcode": _read_u16_hex(addr),
                "param1": _read_u16_hex(addr + 2),
                "param2": _read_u16_hex(addr + 4),
                "param3": _read_u16_hex(addr + 6),
                "f32_1":  _read_f32(addr + 8),
                "f32_2":  _read_f32(addr + 12),
                "f32_3":  _read_f32(addr + 16),
            }

            base_hit = {
                "addr": addr,
                "dmg":  dmg,
                "fmt":  fmt_name,
                "dmg_write_addr": _resolve_script_dmg_addr(addr, dmg) or (addr + dmg_offset),
                **_OPCODE_HIT_FIELDS,
                **extra,
            }

            if dmg in lookup:
                for key, mv in lookup[dmg]:
                    if (
                        key == "FRANK"
                        and "zombie" in str(mv).lower()
                        and _owning_chr_tbl(addr) in frank_bases
                        and not _is_frank_zombie_fall_label(mv)
                    ):
                        continue
                    hits.append({**base_hit, "key": key, "move": mv})
            else:
                hits.append({**base_hit, "key": "?", "move": "Unknown"})


# ---------------------------------------------------------------------------
# Suffix-scan pass  (template / template2 / script(0xNN))
# ---------------------------------------------------------------------------
def _is_super_like_block(data: bytes, base_off: int) -> bool:
    """
    Permissive secondary filter for super/script-like blocks.
    This does NOT try to enforce the normal projectile template rules.
    """
    end = len(data)

    def u16(off: int) -> int | None:
        o = base_off + off
        if o + 2 > end:
            return None
        return struct.unpack_from(">H", data, o)[0]

    def u32(off: int) -> int | None:
        o = base_off + off
        if o + 4 > end:
            return None
        return struct.unpack_from(">I", data, o)[0]

    def f32(off: int) -> float | None:
        o = base_off + off
        if o + 4 > end:
            return None
        return struct.unpack_from(">f", data, o)[0]

    disc = u32(_DISCRIMINATOR)
    if disc is None:
        return False

    # Let the regular template/template2 path own those.
    if disc in (0xFFFFFFFF, 0x00000000, 0x00000001):
        return False

    dmg = u16(0x02)
    if dmg is None or not (500 <= dmg <= 20000):
        return False

    plausible = 0

    hb = f32(_VALID_HITBOX)
    if hb is not None and 0.0 <= hb <= 300.0:
        plausible += 1

    accel = f32(_VALID_ACCEL)
    if accel is not None and -10.0 <= accel <= 10.0:
        plausible += 1

    hb_size = u16(_VALID_HBSIZE)
    if hb_size is not None and 0 <= hb_size <= 4096:
        plausible += 1

    c042 = u16(_VALID_C042)
    if c042 is not None and 0 <= c042 <= 64:
        plausible += 1

    return plausible >= 2

def _scan_suffix_blocks(data: bytes, base_addr: int, hits: list,
                         lookup: dict, id_map: dict,
                         slot_char_ids: dict | None = None) -> None:
    frank_bases = {
        base for base, cid in (slot_char_ids or {}).items() if cid == FRANK_CHAR_ID
    }
    pos = 0
    while True:
        idx = data.find(_SUFFIX, pos)
        if idx < 0:
            break
        pos = idx + 1

        if idx < 4:
            continue

        c = data[idx - 4:idx]
        if c[1]:
            continue

        dmg = (c[2] << 8) | c[3]
        if not dmg:
            continue

        base_off = idx - 4
        a = base_addr + base_off

        after4 = data[idx + 4:idx + 8] if idx + 8 <= len(data) else b""
        fmt = _classify_discriminator(after4)

        is_template_ok = False
        is_super_ok = False

        if fmt in ("template", "template2"):
            ok = _validate_template(data, base_off)
            if not ok:
                print(f"[gate] reject @ 0x{a:08X} fmt={fmt}")
                print(f"  +0x42={struct.unpack_from('>H', data, base_off + 0x42)[0] if base_off + 0x44 <= len(data) else 'OOB'}")
                print(f"  +0x6E={struct.unpack_from('>H', data, base_off + 0x6E)[0] if base_off + 0x70 <= len(data) else 'OOB'}")
                print(f"  +0x84={struct.unpack_from('>f', data, base_off + 0x84)[0] if base_off + 0x88 <= len(data) else 'OOB'}")
                print(f"  +0x8C={struct.unpack_from('>f', data, base_off + 0x8C)[0] if base_off + 0x90 <= len(data) else 'OOB'}")
                print(f"  +0x08=0x{struct.unpack_from('>I', data, base_off + 0x08)[0]:08X}" if base_off + 0x0C <= len(data) else "  +0x08=OOB")
                continue
            is_template_ok = True
        else:
            # New parallel permissive path for supers / script-like blocks
            if _owning_chr_tbl(a) is not None and _is_super_like_block(data, base_off):
                is_super_ok = True
                fmt = "super_like"
            else:
                continue

        pre8 = data[idx - 8:idx - 4] if idx >= 8 else b""
        sig_keys = _keys_for_block(c, pre8)

        if is_template_ok:
            fields = {
                "radius":   _read_f32(a + FIELD_OFFSETS["radius"]),
                "kb_x":     _read_f32(a + FIELD_OFFSETS["kb_x"]),
                "kb_y":     _read_f32(a + FIELD_OFFSETS["kb_y"]),
                "type":     _read_u8(a  + FIELD_OFFSETS["type"]),
                "id":       _read_u16(a + FIELD_OFFSETS["id"]),
                "lifetime": _read_u8(a  + FIELD_OFFSETS["lifetime"]),
                "hb_size":  _read_u16(a + FIELD_OFFSETS["hb_size"]),
                "speed":    _read_f32(a + FIELD_OFFSETS["speed"]),
                "accel":    _read_f32(a + FIELD_OFFSETS["accel"]),
                "hitbox":   _read_f32(a + FIELD_OFFSETS["hitbox"]),
                "arc":      _read_f32(a + FIELD_OFFSETS["arc"]),
                "arc2":     _read_f32(a + FIELD_OFFSETS["arc2"]),
                "vel2_x":   _read_f32(a + FIELD_OFFSETS["vel2_x"]),
                "vel2_y":   _read_f32(a + FIELD_OFFSETS["vel2_y"]),
                "vel2_s":   _read_f32(a + FIELD_OFFSETS["vel2_s"]),
                "u01":      _read_f32(a + FIELD_OFFSETS["u01"]),
                "u02":      _read_f32(a + FIELD_OFFSETS["u02"]),
                "u03":      _read_f32(a + FIELD_OFFSETS["u03"]),
                "u04":      _read_u16(a + FIELD_OFFSETS["u04"]),
                "u05":      _read_u16(a + FIELD_OFFSETS["u05"]),
                "u06":      _read_u16(a + FIELD_OFFSETS["u06"]),
                "u07":      _read_u16(a + FIELD_OFFSETS["u07"]),
                "u08":      _read_u16(a + FIELD_OFFSETS["u08"]),
                "u09":      _read_u16(a + FIELD_OFFSETS["u09"]),
                "preA": "?", "preB": "?",
                "opcode": "?", "param1": "?", "param2": "?", "param3": "?",
                "f32_1": "?", "f32_2": "?", "f32_3": "?",
                "cluster": "",
            }
        else:
            # super_like path: do not force all projectile fields to mean anything
            fields = {
                **_OPCODE_HIT_FIELDS,
                "speed":  _read_f32(a + FIELD_OFFSETS["speed"]),
                "accel":  _read_f32(a + FIELD_OFFSETS["accel"]),
                "hitbox": _read_f32(a + FIELD_OFFSETS["hitbox"]),
                "type":   _read_u8(a + FIELD_OFFSETS["type"]),
                "id":     _read_u16(a + FIELD_OFFSETS["id"]),
                "cluster": "super_like",
            }

        if dmg in lookup:
            matches = lookup[dmg]
            if len({k for k, _ in matches}) > 1:
                proj_id = fields.get("id")
                try:
                    pid_int = int(proj_id)
                except (TypeError, ValueError):
                    pid_int = None
                if pid_int is not None:
                    id_matches = [
                        (k, mv) for k, mv in matches
                        if id_map.get(k, {}).get(mv) == pid_int
                    ]
                    if id_matches:
                        matches = id_matches

            for key, mv in matches:
                if (
                    key == "FRANK"
                    and "zombie" in str(mv).lower()
                    and _owning_chr_tbl(a) in frank_bases
                    and not _is_frank_zombie_fall_label(mv)
                ):
                    continue
                hits.append({
                    "addr": a,
                    "key": key,
                    "move": mv,
                    "dmg": dmg,
                    "fmt": fmt,
                    "dmg_write_addr": _resolve_script_dmg_addr(a, dmg) or (a + 2),
                    **fields
                })

        elif dmg >= 500:
            hits.append({
                "addr": a,
                "key": "?",
                "move": "Unknown",
                "dmg": dmg,
                "fmt": fmt,
                "dmg_write_addr": _resolve_script_dmg_addr(a, dmg) or (a + 2),
                **fields
            })
# ---------------------------------------------------------------------------
# Zombie canonical block scanner
#
# The authoritative spawn-like signature for Zombie Spree/Attack/Fall is:
#   2C 11 02 3F  (block start marker)
#   followed within 0x40 bytes by:
#   04 01 02 3F 00 00 00 XX  (where XX is the variant byte)
#
# Only variants 0x36–0x3B are valid Zombie spawn variants per the notes.
# Anything outside this range is noise from other characters' scripts.
#
# Deduplication: one row per (chr_tbl_base, variant) across the full scan.
# ---------------------------------------------------------------------------
_ZOMBIE_BLOCK_SIG  = b"\x2C\x11\x02\x3F"
_ZOMBIE_INNER_SIG  = b"\x04\x01\x02\x3F\x00\x00\x00"
_ZOMBIE_INNER_LOOK = 0x40
_ZOMBIE_VARIANT_MIN = 0x36
_ZOMBIE_VARIANT_MAX = 0x3B

_ZOMBIE_VARIANT_NAMES: dict[int, str] = {
    0x36: "Zombie Spree (v0x36)",
    0x37: "Zombie Spree (v0x37)",
    0x38: "Zombie Spree (v0x38)",
    0x39: "Zombie Spree (v0x39)",
    0x3A: "Zombie Spree (v0x3A)",
    0x3B: "Zombie Spree (v0x3B)",
}

_FRANK_ZOMBIE_FALL_NAMES = {"Zombie Fall", "Zombie fall"}
_FRANK_ZOMBIE_FALL_OFF = 0x19BE
_FRANK_ZOMBIE_ATTACK_OFF = 0x0B14
_FRANK_ZOMBIE_SPREE_OFF  = 0x7C4C

_FRANK_ZOMBIE_SPREE_KBY_L = 0x12E
_FRANK_ZOMBIE_SPREE_KBY_M = 0x17E
_FRANK_ZOMBIE_SPREE_KBY_H = 0x1CE

_FRANK_ZOMBIE_SPREE_ARC_L   = 0x122
_FRANK_ZOMBIE_SPREE_ACCEL_L = 0x142

_FRANK_ZOMBIE_SPREE_ARC_M   = 0x172
_FRANK_ZOMBIE_SPREE_ACCEL_M = 0x192

_FRANK_ZOMBIE_SPREE_ARC_H   = 0x1C2
_FRANK_ZOMBIE_SPREE_ACCEL_H = 0x1E2

_FRANK_ZOMBIE_ATTACK_SPEED_A = 0x7E
_FRANK_ZOMBIE_ATTACK_SPEED_A = 0x7E
_FRANK_ZOMBIE_ATTACK_ACCEL_A = 0x92
_FRANK_ZOMBIE_ATTACK_SPAWN_X = 0x159
_FRANK_ZOMBIE_FALL_DMG_OFF = 0x04
_FRANK_ZOMBIE_FALL_SPAWN_Y_OFF = 0xA6
# Exact per-slot ownership ranges derived from chr_tbl analysis notes.
# Each tuple is (chr_tbl_base, move_data_start, max_referenced_addr + slack).
# Using tight bounds prevents cross-slot false positives.
_CHR_TBL_RANGES = [
    (0x90896640, 0x90896640, 0x908D2000),            # slot 0
    (0x908F1920, 0x908F1920, 0x9092B634 + 0x2000),   # slot 1
    (0x909478E0, 0x909478E0, 0x909BE310 + 0x2000),   # slot 2
    (0x9099D9C0, 0x9099D9C0, 0x909DECAC + 0x2000),   # slot 3
]

def _owning_chr_tbl(addr: int) -> int | None:
    """
    Dynamic ownership:
    assign the hit to the closest chr_tbl base within a sane forward window.
    This avoids hardcoding slot-specific end ranges that can miss valid data.
    """
    best_base = None
    best_dist = None

    for base in _CHR_TBL_BASES:
        if addr < base:
            continue
        dist = addr - base
        if dist > 0x90000:   # generous window; adjust if needed
            continue
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_base = base

    return best_base

def _key_for_hit_addr(addr: int, slot_char_ids: dict[int, int] | None) -> str | None:
    if not slot_char_ids:
        return None
    base = _owning_chr_tbl(addr)
    if base is None:
        return None
    cid = slot_char_ids.get(base)
    if cid is None:
        return None
    return CHAR_ID_TO_KEY.get(cid)
def _is_frank_zombie_move_label(move: str) -> bool:
    s = str(move or "").lower()
    return "zombie" in s

def _is_frank_zombie_fall_label(move: str) -> bool:
    return str(move or "") in _FRANK_ZOMBIE_FALL_NAMES

def _apply_frank_zombie_anchor(hits: list[dict]) -> list[dict]:
    """
    Frank-specific override:
      - if a real Zombie Fall row exists, use it as anchor
      - otherwise synthesize Zombie Fall from the owning chr_tbl base
      - derive Attack/Spree from Fall
    """
    anchored_rows: list[dict] = []
    anchor_bases: set[int] = set()
    fall_by_base: dict[int, dict] = {}
    zombie_base_seed: dict[int, dict] = {}

    for h in hits:
        if h.get("key") != "FRANK":
            continue

        base = _owning_chr_tbl(h.get("addr", 0))
        if base is None:
            continue

        if _is_frank_zombie_move_label(h.get("move", "")):
            zombie_base_seed.setdefault(base, h)

        if _is_frank_zombie_fall_label(h.get("move", "")):
            fall_by_base.setdefault(base, h)

    for base, seed in zombie_base_seed.items():
        fall_hit = fall_by_base.get(base)
        if fall_hit is None:
            fall_addr = base + _FRANK_ZOMBIE_FALL_OFF
            fall_hit = {
                **seed,
                "addr": fall_addr,
                "move": "Zombie Fall",
                "dmg": 3200,
                "fmt": "frank_zoml",
                "cluster": f"frank zombie anchor @ 0x{fall_addr:08X}",
                "dmg_write_addr": fall_addr + _FRANK_ZOMBIE_FALL_DMG_OFF,
            }

        anchor_bases.add(base)
        fall_addr   = int(fall_hit["addr"])
        attack_addr = fall_addr + _FRANK_ZOMBIE_ATTACK_OFF
        spree_addr  = fall_addr + _FRANK_ZOMBIE_SPREE_OFF - 4

        anchored_rows.append({
            **fall_hit,
            "addr": fall_addr,
            "move": "Zombie Fall",
            "dmg": 3200,
            "cluster": f"frank zombie anchor @ 0x{fall_addr:08X}",
            "dmg_write_addr": fall_addr + _FRANK_ZOMBIE_FALL_DMG_OFF,
            "spawn_y": _read_f32(fall_addr + _FRANK_ZOMBIE_FALL_SPAWN_Y_OFF),
        })

        anchored_rows.append({
            **fall_hit,
            "addr": attack_addr,
            "move": "Zombie Attack",
            "dmg": 2400,
            "cluster": f"frank zombie anchor @ 0x{fall_addr:08X}",
            "dmg_write_addr": base + _SCRIPT_DMG_OFFSETS[2400],
            "speed":   _read_f32(attack_addr + _FRANK_ZOMBIE_ATTACK_SPEED_A),
            "accel":   _read_f32(attack_addr + _FRANK_ZOMBIE_ATTACK_ACCEL_A),
            "spawn_x": _read_u8(attack_addr + _FRANK_ZOMBIE_ATTACK_SPAWN_X),
        })
        
        anchored_rows.append({
            **fall_hit,
            "addr": spree_addr,
            "move": "Zombie Spree L",
            "dmg": 2400,
            "cluster": f"frank zombie anchor @ 0x{fall_addr:08X}",
            "dmg_write_addr": base + _SCRIPT_DMG_OFFSETS[2400],
            "arc":   _read_f32(spree_addr + _FRANK_ZOMBIE_SPREE_ARC_L),
            "kb_y":  _read_f32(spree_addr + _FRANK_ZOMBIE_SPREE_KBY_L),
            "speed": _read_f32(spree_addr + _FRANK_ZOMBIE_SPREE_ACCEL_L),
        })

        anchored_rows.append({
            **fall_hit,
            "addr": spree_addr,
            "move": "Zombie Spree M",
            "dmg": 2400,
            "cluster": f"frank zombie anchor @ 0x{fall_addr:08X}",
            "dmg_write_addr": base + _SCRIPT_DMG_OFFSETS[2400],
            "arc":   _read_f32(spree_addr + _FRANK_ZOMBIE_SPREE_ARC_M),
            "kb_y":  _read_f32(spree_addr + _FRANK_ZOMBIE_SPREE_KBY_M),
            "speed": _read_f32(spree_addr + _FRANK_ZOMBIE_SPREE_ACCEL_M),
        })

        anchored_rows.append({
            **fall_hit,
            "addr": spree_addr,
            "move": "Zombie Spree H",
            "dmg": 2400,
            "cluster": f"frank zombie anchor @ 0x{fall_addr:08X}",
            "dmg_write_addr": base + _SCRIPT_DMG_OFFSETS[2400],
            "arc":   _read_f32(spree_addr + _FRANK_ZOMBIE_SPREE_ARC_H),
            "kb_y":  _read_f32(spree_addr + _FRANK_ZOMBIE_SPREE_KBY_H),
            "speed": _read_f32(spree_addr + _FRANK_ZOMBIE_SPREE_ACCEL_H),
        })

    if not anchor_bases:
        return hits

    kept: list[dict] = []
    for h in hits:
        if (
            h.get("key") == "FRANK"
            and _is_frank_zombie_move_label(h.get("move", ""))
            and _owning_chr_tbl(h.get("addr", 0)) in anchor_bases
        ):
            continue
        kept.append(h)

    kept.extend(anchored_rows)
    kept.sort(key=lambda x: (int(x.get("addr", 0)), str(x.get("move", ""))))
    return kept
# ---------------------------------------------------------------------------
# Main scan thread
# ---------------------------------------------------------------------------
def _scan_zombie_blocks(data: bytes, base_addr: int, hits: list,
                         lookup: dict, seen_variants: set,
                         slot_char_ids: dict | None = None) -> None:
    """
    Disabled for now.
    Raw zombie spree variants stay suppressed.
    """
    return
def _append_super_hit(hits: list, lookup: dict, char_damage_map: dict,
                      slot_char_ids: dict[int, int] | None,
                      addr: int, dmg, fmt: str, dmg_write_addr: int,
                      cluster: str, extra: dict | None = None):
    hit_base = {
        "addr": addr,
        "dmg": dmg,
        "fmt": fmt,
        "dmg_write_addr": dmg_write_addr,
        **_OPCODE_HIT_FIELDS,
        "cluster": cluster,
    }
    if extra:
        hit_base.update(extra)

    # Prefer slot-owned character resolution first.
    if isinstance(dmg, int):
        owner_base = _owning_chr_tbl(addr)
        cid = slot_char_ids.get(owner_base) if (slot_char_ids and owner_base is not None) else None
        slot_key = _key_for_hit_addr(addr, slot_char_ids)
        print(f"[super-attr] addr=0x{addr:08X} base={hex(owner_base) if owner_base else None} cid={cid} dmg={dmg} slot_key={slot_key}")
        if slot_key is not None:
            moves = char_damage_map.get(slot_key, {}).get(dmg, [])
            if moves:
                for mv in moves:
                    hits.append({**hit_base, "key": slot_key, "move": mv})
                return

        # Fallback to the old global lookup if slot mapping is missing.
        if dmg in lookup:
            for key, mv in lookup[dmg]:
                hits.append({**hit_base, "key": key, "move": mv})
            return

    hits.append({**hit_base, "key": "?", "move": "Super Struct Candidate"})
def _scan_super_struct_blocks(data: bytes, base_addr: int, hits: list,
                              lookup: dict, char_damage_map: dict,
                              slot_char_ids: dict[int, int] | None) -> None:
    # ── Pass 1: original sig  00 00 0C 00 00 00 23 00 ─────────────────────
    pos = 0
    while True:
        idx = data.find(SUPER_STRUCT_SIG, pos)
        if idx < 0:
            break
        pos = idx + 1

        block_addr = base_addr + idx
        if _owning_chr_tbl(block_addr) is None:
            continue

        window_end = min(idx + SUPER_VERIFY_LOOK, len(data))
        window = data[idx:window_end]
        if not (SUPER_VERIFY_A in window or SUPER_VERIFY_B in window):
            continue

        dmg_addr = block_addr + _SUPER_STRUCT_DMG_OFF
        dmg = "?"
        if rbytes is not None:
            try:
                b = rbytes(dmg_addr, 2)
                if b and len(b) == 2:
                    dmg = (b[0] << 8) | b[1]
            except Exception:
                pass

        _append_super_hit(
            hits, lookup, char_damage_map, slot_char_ids,
            block_addr, dmg, "super_struct", dmg_addr,
            f"super struct @ 0x{block_addr:08X}"
        )

    # ── Pass 2: wildcard sig  ?? 23 00 00 00 [dmg hi] [dmg lo] 00 00 00 00
    pos = 0
    while True:
        idx = data.find(b"\x23\x00\x00\x00", pos)
        if idx < 0:
            break
        pos = idx + 1

        block_addr = base_addr + idx
        if _owning_chr_tbl(block_addr) is None:
            continue

        dmg_off = idx + 4
        if dmg_off + 6 > len(data):
            continue

        dmg = (data[dmg_off] << 8) | data[dmg_off + 1]
        if not (2 <= dmg <= 20000):
            continue
        
        if data[dmg_off + 2 : dmg_off + 6] != b"\x00\x00\x00\x00":
            continue

        dmg_addr = base_addr + dmg_off

        _append_super_hit(
            hits, lookup, char_damage_map, slot_char_ids,
            block_addr, dmg, "super_struct", dmg_addr,
            f"super struct2 @ 0x{block_addr:08X}"
        )


    # ── Pass 3: alt card  01 23 00 00 [dmg hi] [dmg lo]
    pos = 0
    while True:
        idx = data.find(b"\x01\x23\x00\x00", pos)
        if idx < 0:
            break
        pos = idx + 1

        block_addr = base_addr + idx
        if _owning_chr_tbl(block_addr) is None:
            continue
        if idx + 8 > len(data):
            continue

        dmg = (data[idx + 4] << 8) | data[idx + 5]
        if not (500 <= dmg <= 20000):
            continue

        _append_super_hit(
            hits, lookup, char_damage_map, slot_char_ids,
            block_addr, dmg, "super_struct_card", base_addr + idx + 4,
            f"super card @ 0x{block_addr:08X}",
            {
                "opcode": _read_u16_hex(block_addr),
                "param1": _read_u16_hex(block_addr + 2),
                "param2": _read_u16_hex(block_addr + 4),
                "param3": _read_u16_hex(block_addr + 6),
            }
        )

    # ── Pass 4: shifted alt card  00 23 00 00 [dmg hi] [dmg lo] ...
    pos = 0
    while True:
        idx = data.find(b"\x00\x23\x00\x00", pos)
        if idx < 0:
            break
        pos = idx + 1

        block_addr = base_addr + idx
        if _owning_chr_tbl(block_addr) is None:
            continue
        if idx + 8 > len(data):
            continue
        dmg = (data[idx + 4] << 8) | data[idx + 5]
        if not (500 <= dmg <= 20000):
            continue

        _append_super_hit(
            hits, lookup, char_damage_map, slot_char_ids,
            block_addr, dmg, "super_struct_card2", base_addr + idx + 4,
            f"super card2 @ 0x{block_addr:08X}",
            {
                "opcode": _read_u16_hex(block_addr),
                "param1": _read_u16_hex(block_addr + 2),
                "param2": _read_u16_hex(block_addr + 4),
                "param3": _read_u16_hex(block_addr + 6),
            }
        )
 
def _run_scan(active_keys, progress_cb, done_cb, show_unknowns: bool = True):
    if rbytes is None:
        done_cb([]); return

    proj_map = _load_map()
    id_map   = _load_ids()
    lookup   = _build_lookup(proj_map, active_keys)
    char_damage_map = _build_char_damage_map(proj_map)

    # Read live char_id per slot so zombie block scanner can gate on Frank.
    slot_char_ids = _read_slot_char_ids()

    total = SCAN_END - SCAN_START
    hits  = []
    addr  = SCAN_START
    seen_zombie_variants: set = set()

    while addr < SCAN_END:
        sz = min(SCAN_BLOCK, SCAN_END - addr)
        try:
            data = rbytes(addr, sz)
        except Exception:
            data = b""

        if data:
            _scan_opcode_blocks(data, addr, hits, lookup, slot_char_ids)
            _scan_suffix_blocks(data, addr, hits, lookup, id_map, slot_char_ids)
            _scan_zombie_blocks(data, addr, hits, lookup, seen_zombie_variants, slot_char_ids)
            _scan_super_struct_blocks(data, addr, hits, lookup, char_damage_map, slot_char_ids)
        progress_cb((addr - SCAN_START + sz) / total * 100.0)
        addr += sz
    _annotate_clusters(hits)

    # Frank-specific zombie handling:
    # ignore Frank move-ID association except Zombie Fall,
    # then derive Attack/Spree from the discovered Fall row.
    hits = _apply_frank_zombie_anchor(hits)

    if not show_unknowns:
        hits = [h for h in hits if not (h.get("key") == "?" and h.get("move") == "Unknown")]

    _dump_hits(hits)
        

    
    done_cb(hits)


def _dump_hits(hits: list, context: int = 0x100):
    if rbytes is None or not hits:
        return
    super_hits = [
        h for h in hits
        if h.get("fmt") in ("super_struct", "super_struct_card", "super_struct_card2")
    ]
    if not super_hits:
        return
    try:
        with open("proj_dump.bin", "wb") as f:
            for h in super_hits:
                base = max(h["addr"] - context, SCAN_START)
                size = min(context * 2, SCAN_END - base)
                try:
                    data = rbytes(base, size)
                except Exception:
                    data = b""
                f.write(base.to_bytes(4, "big"))
                f.write(h["addr"].to_bytes(4, "big"))
                f.write(len(data).to_bytes(4, "big"))
                f.write(data)
        print(f"[proj_scanner] dumped {len(super_hits)} super_struct hit(s) to proj_dump.bin")
    except Exception as e:
        print(f"[proj_scanner] dump failed: {e}")
# ---------------------------------------------------------------------------
# Column definitions
# kb_x / kb_y replace aerial_kb_x / aerial_kb_y
# cluster column added
# ---------------------------------------------------------------------------
_COLS = [
    ("address",  "Address",   None,       False),
    ("char",     "Char",      None,       False),
    ("move",     "Move",      None,       False),
    ("dmg",      "Damage",    "dmg",      False),
    ("cluster",  "Cluster",   None,       False),
    ("radius",   "Radius",    "radius",   True),
    ("speed",    "Speed",     "speed",    True),
    ("accel",    "Accel",     "accel",    True),
    ("kb_x",     "KB X",      "kb_x",     True),
    ("kb_y",     "KB Y",      "kb_y",     True),
    ("arc",      "Arc",       "arc",      True),
    ("arc2",     "Arc2",      "arc2",     True),
    ("spawn_x",  "Spawn X",   "spawn_x",  False),
    ("spawn_y",  "Spawn Y",   "spawn_y",  True),
    ("hitbox",   "Hitbox",    "hitbox",   True),
    ("type",     "Type",      "type",     False),
    ("id",       "ID",        "id",       False),
    ("lifetime", "Lifetime",  "lifetime", False),
    ("hb_size",  "HB Size",   "hb_size",  False),
    ("fmt",      "Fmt",       None,       False),
    ("preA",     "PreA",      None,       False),
    ("preB",     "PreB",      None,       False),
    ("opcode",   "Opcode",    None,       False),
    ("param1",   "Param1",    None,       False),
    ("param2",   "Param2",    None,       False),
    ("param3",   "Param3",    None,       False),
    ("f32_1",    "F32+8",     None,       True),
    ("f32_2",    "F32+C",     None,       True),
    ("f32_3",    "F32+10",    None,       True),
    ("vel2_x",   "Vel2 X",    "vel2_x",   True),
    ("vel2_y",   "Vel2 Y",    "vel2_y",   True),
    ("vel2_s",   "Vel2 S",    "vel2_s",   True),
    ("u01",      "?? 01",     "u01",      True),
    ("u02",      "?? 02",     "u02",      True),
    ("u03",      "?? 03",     "u03",      True),
    ("u04",      "?? 04",     "u04",      False),
    ("u05",      "?? 05",     "u05",      False),
    ("u06",      "?? 06",     "u06",      False),
    ("u07",      "?? 07",     "u07",      False),
    ("u08",      "?? 08",     "u08",      False),
    ("u09",      "?? 09",     "u09",      False),
]
_COL_IDS    = [c[0] for c in _COLS]
_FMT_COL_IDX = _COL_IDS.index("fmt")


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
class ProjScannerWindow:
    def __init__(self, master, get_active_fn):
        self._get_active = get_active_fn
        self._scanning   = False
        self._keys: set  = set()
        self._addr_by_iid: dict[str, int] = {}
        self._dmg_write_by_iid: dict[str, int] = {}
        self._show_unknowns = tk.BooleanVar(value=True)

        self.root = tk.Toplevel(master)
        self.root.title("Projectile Definition Scanner")
        self.root.geometry("1100x560")
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        self._build()
        self._auto_scan()

    def _build(self):
        top = ttk.Frame(self.root)
        top.pack(side="top", fill="x", padx=8, pady=6)
        self._active_var = tk.StringVar(value="Active: --")
        ttk.Label(top, textvariable=self._active_var).pack(side="left")
        ttk.Checkbutton(
            top, text="Show unknowns",
            variable=self._show_unknowns,
            command=self._start,
        ).pack(side="right", padx=8)
        self._scan_btn = ttk.Button(top, text="Rescan", command=self._start)
        self._scan_btn.pack(side="right", padx=4)

        self._prog = tk.DoubleVar()
        ttk.Progressbar(self.root, variable=self._prog, maximum=100).pack(
            fill="x", padx=8, pady=(0, 4))
        self._status = tk.StringVar(value="Scanning...")
        ttk.Label(self.root, textvariable=self._status, anchor="w").pack(fill="x", padx=8)

        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=8, pady=6)

        self._tree = ttk.Treeview(frame, columns=_COL_IDS, show="headings", height=24)
        widths = {"address": 110, "char": 80, "move": 160, "dmg": 65,
                  "cluster": 220, "speed": 75, "accel": 75, "arc": 75}
        self._sort_col = None
        self._sort_asc = True

        col_map = {c[0]: c[1] for c in _COLS}
        for col_id, header, _, _ in _COLS:
            self._tree.heading(col_id, text=header,
                command=lambda c=col_id: self._sort_by(c))
            self._tree.column(col_id, width=widths.get(col_id, 65), anchor="center")
        self._tree.column("move",    anchor="w")
        self._tree.column("cluster", anchor="w")

        vsb = ttk.Scrollbar(frame, orient="vertical", command=self._tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self._tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")

        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)

        self._tree.bind("<Double-Button-1>", self._on_double_click)
        self._tree.bind("<Button-3>",        self._on_right_click)


        ttk.Label(self.root,
                  text="Double-click Dmg/Speed/Accel/Arc/Lifetime to edit. "
                       "Right-click to copy address. Cluster column groups by proj-ID + type family.",
                  foreground="gray").pack(anchor="w", padx=8, pady=(0, 4))

    def _auto_scan(self):
        names = self._get_active()
        self._keys = {_NAME_TO_KEY[n] for n in names if n in _NAME_TO_KEY}
        self._active_var.set(f"Active: {', '.join(sorted(names)) or 'none'}")
        self._start()

    def _start(self):
        if self._scanning:
            return
        names = self._get_active()
        self._keys = {_NAME_TO_KEY[n] for n in names if n in _NAME_TO_KEY}
        self._active_var.set(f"Active: {', '.join(n for n in names if n) or 'none'}")
        if not self._keys:
            self._status.set("No active characters with known projectiles.")
            return
        self._scanning = True
        self._scan_btn.config(state="disabled")
        self._prog.set(0)
        self._addr_by_iid.clear()
        self._dmg_write_by_iid.clear()
        for i in self._tree.get_children():
            self._tree.delete(i)
        self._status.set("Scanning MEM2...")
        threading.Thread(
            target=_run_scan,
            args=(set(self._keys), self._on_prog, self._on_done),
            kwargs={"show_unknowns": self._show_unknowns.get()},
            daemon=True,
        ).start()

    def _sort_by(self, col_id: str):
        if self._sort_col == col_id:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col_id
            self._sort_asc = True

        col_map = {c[0]: c[1] for c in _COLS}
        for cid, header, _, _ in _COLS:
            arrow = (" ▲" if self._sort_asc else " ▼") if cid == col_id else ""
            self._tree.heading(cid, text=col_map[cid] + arrow)

        items = [(self._tree.set(iid, col_id), iid)
                 for iid in self._tree.get_children("")]

        def sort_key(val):
            try:
                return (0, float(val))
            except Exception:
                return (1, str(val).lower())

        items.sort(key=lambda x: sort_key(x[0]), reverse=not self._sort_asc)
        for idx, (_, iid) in enumerate(items):
            self._tree.move(iid, "", idx)

    def _on_prog(self, pct: float):
        try:
            self.root.after(0, lambda: self._prog.set(pct))
        except Exception:
            pass

    def _on_done(self, hits: list):
        def _f():
            _TYPE_LABELS = {"3": "3:Linear", "4": "4:Physics",
                            3:   "3:Linear",   4: "4:Physics"}
            for h in hits:
                type_str = _TYPE_LABELS.get(h["type"], str(h["type"]))
                iid = self._tree.insert("", "end", values=(
                    f"0x{h['addr']:08X}",
                    h["key"], h["move"], h["dmg"],
                    h.get("cluster", ""),
                    h["radius"], h["speed"], h["accel"],
                    h["kb_x"], h["kb_y"],
                    h["arc"], h["arc2"],
                    h.get("spawn_x", "?"), h.get("spawn_y", "?"), h["hitbox"],
                    type_str, h["id"], h["lifetime"], h["hb_size"],
                    h.get("fmt", ""),
                    h.get("preA", "?"), h.get("preB", "?"),
                    h.get("opcode", "?"),
                    h.get("param1", "?"), h.get("param2", "?"), h.get("param3", "?"),
                    h.get("f32_1", "?"), h.get("f32_2", "?"), h.get("f32_3", "?"),
                    h["vel2_x"], h["vel2_y"], h["vel2_s"],
                    h["u01"], h["u02"], h["u03"],
                    h["u04"], h["u05"], h["u06"],
                    h["u07"], h["u08"], h["u09"],
                ))
                self._addr_by_iid[iid]      = h["addr"]
                self._dmg_write_by_iid[iid] = h.get("dmg_write_addr", h["addr"] + 2)
            self._scanning = False
            self._scan_btn.config(state="normal")
            self._prog.set(100)
            n_known = sum(1 for h in hits if h.get("key") != "?")
            n_unk   = sum(1 for h in hits if h.get("key") == "?" and h.get("move") == "Unknown")
            self._status.set(
                f"Done — {len(hits)} match(es): {n_known} attributed, {n_unk} unknown."
            )
        try:
            self.root.after(0, _f)
        except Exception:
            pass

    def _col_index(self, event) -> int:
        col = self._tree.identify_column(event.x)
        return int(col[1:]) - 1 if col else -1

    def _fmt_for_iid(self, iid: str) -> str:
        vals = self._tree.item(iid, "values")
        return str(vals[_FMT_COL_IDX]) if len(vals) > _FMT_COL_IDX else ""

    def _on_right_click(self, event):
        iid = self._tree.identify_row(event.y)
        if not iid:
            return
        addr = self._addr_by_iid.get(iid)
        if addr is None:
            return

        col_idx     = self._col_index(event)
        field_addr  = addr
        field_label = "base"
        if 0 <= col_idx < len(_COLS):
            col_id, header, fkey, _ = _COLS[col_idx]
            vals = self._tree.item(iid, "values")
            move_name = str(vals[2]) if len(vals) > 2 else ""

            if fkey == "dmg":
                field_addr = self._dmg_write_by_iid.get(iid, addr + _dmg_write_offset(self._fmt_for_iid(iid)))
                field_label = "dmg"
            elif move_name == "Zombie Fall" and fkey == "spawn_y":
                field_addr = addr + _FRANK_ZOMBIE_FALL_SPAWN_Y_OFF
                field_label = header

            elif move_name == "Zombie Attack" and fkey == "speed":
                field_addr = addr + _FRANK_ZOMBIE_ATTACK_SPEED_A
                field_label = header
            elif move_name == "Zombie Attack" and fkey == "accel":
                field_addr = addr + _FRANK_ZOMBIE_ATTACK_ACCEL_A
                field_label = header

            elif move_name == "Zombie Attack" and fkey == "spawn_x":
                field_addr = addr + _FRANK_ZOMBIE_ATTACK_SPAWN_X
                field_label = header
            elif move_name.startswith("Zombie Spree "):
                if fkey == "kb_y":
                    if move_name == "Zombie Spree L":
                        field_addr = addr + _FRANK_ZOMBIE_SPREE_KBY_L
                    elif move_name == "Zombie Spree M":
                        field_addr = addr + _FRANK_ZOMBIE_SPREE_KBY_M
                    else:
                        field_addr = addr + _FRANK_ZOMBIE_SPREE_KBY_H
                    field_label = header

                elif fkey == "speed":
                    if move_name == "Zombie Spree L":
                        field_addr = addr + _FRANK_ZOMBIE_SPREE_ACCEL_L
                    elif move_name == "Zombie Spree M":
                        field_addr = addr + _FRANK_ZOMBIE_SPREE_ACCEL_M
                    else:
                        field_addr = addr + _FRANK_ZOMBIE_SPREE_ACCEL_H
                    field_label = header

                elif fkey == "arc":
                    if move_name == "Zombie Spree L":
                        field_addr = addr + _FRANK_ZOMBIE_SPREE_ARC_L
                    elif move_name == "Zombie Spree M":
                        field_addr = addr + _FRANK_ZOMBIE_SPREE_ARC_M
                    else:
                        field_addr = addr + _FRANK_ZOMBIE_SPREE_ARC_H
                    field_label = header

                elif fkey and fkey in FIELD_OFFSETS:
                    field_addr  = addr + FIELD_OFFSETS[fkey]
                    field_label = header

            elif fkey and fkey in FIELD_OFFSETS:
                field_addr  = addr + FIELD_OFFSETS[fkey]
                field_label = header

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"Copy base address (0x{addr:08X})",
                         command=lambda: self._copy(f"0x{addr:08X}"))
        if field_addr != addr:
            menu.add_command(label=f"Copy {field_label} address (0x{field_addr:08X})",
                             command=lambda: self._copy(f"0x{field_addr:08X}"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy(self, text: str):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self._status.set(f"Copied {text}")

    def _on_double_click(self, event):
        col_idx = self._col_index(event)
        iid     = self._tree.identify_row(event.y)
        if not iid or col_idx < 0:
            return

        col_id, header, fkey, is_float = _COLS[col_idx]
        if col_id in ("address", "char", "move", "cluster", "fmt"):
            return

        addr = self._addr_by_iid.get(iid)
        if addr is None:
            return

        fmt = self._fmt_for_iid(iid)
        vals = self._tree.item(iid, "values")
        move_name = str(vals[2]) if len(vals) > 2 else ""

        if fkey == "dmg":
            write_addr = self._dmg_write_by_iid.get(iid, addr + _dmg_write_offset(fmt))
        elif move_name == "Zombie Fall" and fkey == "spawn_y":
            write_addr = addr + _FRANK_ZOMBIE_FALL_SPAWN_Y_OFF
        elif move_name == "Zombie Attack" and fkey == "speed":
            write_addr = addr + _FRANK_ZOMBIE_ATTACK_SPEED_A

        elif move_name == "Zombie Attack" and fkey == "accel":
            write_addr = addr + _FRANK_ZOMBIE_ATTACK_ACCEL_A

        elif move_name == "Zombie Attack" and fkey == "spawn_x":
            write_addr = addr + _FRANK_ZOMBIE_ATTACK_SPAWN_X
        elif move_name.startswith("Zombie Spree "):
            if fkey == "kb_y":
                if move_name == "Zombie Spree L":
                    write_addr = addr + _FRANK_ZOMBIE_SPREE_KBY_L
                elif move_name == "Zombie Spree M":
                    write_addr = addr + _FRANK_ZOMBIE_SPREE_KBY_M
                else:
                    write_addr = addr + _FRANK_ZOMBIE_SPREE_KBY_H

            elif fkey == "speed":
                if move_name == "Zombie Spree L":
                    write_addr = addr + _FRANK_ZOMBIE_SPREE_ACCEL_L
                elif move_name == "Zombie Spree M":
                    write_addr = addr + _FRANK_ZOMBIE_SPREE_ACCEL_M
                else:
                    write_addr = addr + _FRANK_ZOMBIE_SPREE_ACCEL_H

            elif fkey == "arc":
                if move_name == "Zombie Spree L":
                    write_addr = addr + _FRANK_ZOMBIE_SPREE_ARC_L
                elif move_name == "Zombie Spree M":
                    write_addr = addr + _FRANK_ZOMBIE_SPREE_ARC_M
                else:
                    write_addr = addr + _FRANK_ZOMBIE_SPREE_ARC_H

            elif fkey in FIELD_OFFSETS:
                write_addr = addr + FIELD_OFFSETS[fkey]
            else:
                return
            
        elif fkey in FIELD_OFFSETS:
            write_addr = addr + FIELD_OFFSETS[fkey]
        else:
            return

        vals    = self._tree.item(iid, "values")
        cur_val = vals[col_idx]

        new_val = simpledialog.askstring(
            f"Edit {header}",
            f"Move: {vals[2]}\nAddress: 0x{write_addr:08X}\nCurrent: {cur_val}\n\nNew value:",
            parent=self.root, initialvalue=str(cur_val),
        )
        if new_val is None:
            return
        new_val = new_val.strip()

        if is_float:
            try:
                fval = float(new_val)
            except ValueError:
                messagebox.showerror("Invalid", f"'{new_val}' is not a valid float.",
                                     parent=self.root)
                return
            if _write_f32(write_addr, fval):
                self._tree.set(iid, col_id, f"{fval:.4f}")
                self._status.set(f"Wrote {fval} to 0x{write_addr:08X}")
            else:
                messagebox.showerror("Write failed", "Could not write to Dolphin.",
                                     parent=self.root)
        else:
            try:
                ival = int(new_val, 16) if new_val.startswith("0x") else int(new_val)
            except ValueError:
                messagebox.showerror("Invalid", f"'{new_val}' is not a valid number.",
                                     parent=self.root)
                return
            if fkey == "dmg":
                if not (0 <= ival <= 0xFFFFFFFF):
                    messagebox.showerror("Out of range", "Damage must be 0–4294967295.",
                                         parent=self.root)
                    return
            else:
                if fkey == "spawn_x":
                    if not (0 <= ival <= 0xFF):
                        messagebox.showerror("Out of range", "Spawn X must be 0–255.",
                                             parent=self.root)
                        return
                elif not (0 <= ival <= 0xFFFF):
                    messagebox.showerror("Out of range", "Value must be 0–65535.",
                                         parent=self.root)
                    return

            if fkey == "dmg":
                resolved = self._dmg_write_by_iid.get(iid)
                fallback = addr + _dmg_write_offset(fmt)

                if fmt in ("super_struct", "super_struct_card", "super_struct_card2") and resolved is not None:
                    if not (0 <= ival <= 0xFFFF):
                        messagebox.showerror("Out of range", "Damage must be 0–65535.",
                                            parent=self.root)
                        return
                    ok = _write_u16(resolved, ival)

                elif resolved and resolved != fallback:
                    ok = _write_u32(resolved, ival)

                else:
                    ok = _write_dmg(addr, ival, fmt)
            elif fkey == "spawn_x":
                ok = bool(wbytes(write_addr, bytes([ival]))) if wbytes is not None else False
            else:
                ok = _write_u16(write_addr, ival)
            if ok:
                self._tree.set(iid, col_id, ival)
                self._status.set(f"Wrote {ival} to 0x{write_addr:08X}")
            else:
                messagebox.showerror("Write failed", "Could not write to Dolphin.",
                                     parent=self.root)


# ---------------------------------------------------------------------------
_inst = None

def open_proj_scanner_window(get_active_fn):
    def _c(master):
        global _inst
        if _inst:
            try:
                _inst.root.lift()
                return
            except Exception:
                pass
        _inst = ProjScannerWindow(master, get_active_fn)
    tk_call(_c)