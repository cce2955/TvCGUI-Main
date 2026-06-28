# fd_super_integration.py
#
# Generic super dispatch graph scanner for the Frame Data Workbench.
#
# This intentionally does not reuse the projectile scanner's 00/23 handling.
# Projectile rows should describe payloads/templates.  This module describes the
# caller side: 00/23 dispatch rows, their action selector, phase length, and
# child script link.

from __future__ import annotations

import struct
from typing import Any

from tvcgui.features.combat import projectile_scanner as P


SUPER_DISPATCH_COLUMNS = (
    "dispatch_group",
    "dispatch_confidence",
    "dispatch_super_proof",
    "dispatch_owner_proof",
    "dispatch_selector",
    "dispatch_variant",
    "dispatch_phase",
    "dispatch_child_link",
    "dispatch_child_target",
)

SUPER_DISPATCH_LABELS = {
    "dispatch_group": "Dispatch Group",
    "dispatch_confidence": "Confidence",
    "dispatch_super_proof": "Super Proof",
    "dispatch_owner_proof": "Owner Proof",
    "dispatch_selector": "Action Sel",
    "dispatch_variant": "Variant",
    "dispatch_phase": "Phase Len",
    "dispatch_child_link": "Child Link",
    "dispatch_child_target": "Child Target",
}

# col_name -> (hit-key, display label, type)
SUPER_DISPATCH_FIELD_INFO = {
    "dispatch_selector": ("selector", "Action Selector", "u32"),
    "dispatch_variant": ("variant", "Variant / Branch", "u32"),
    "dispatch_phase": ("phase", "Phase Length", "u32"),
    "dispatch_child_link": ("child_link", "Child Script Link", "u32"),
}

# Owned script/payload fields are displayed on the parent super dispatch row
# after the graph proves ownership.  These reuse the normal FD columns so the
# important hit fields remain visible/editable without opening a separate child
# row.  The backing address comes from the resolved child script, not from the
# 00/23 parent itself.
SUPER_OWNED_FIELD_INFO = {
    "damage": ("owned_damage", "Owned Damage", "u32"),
    "launch_profile": ("owned_launch_profile", "Owned Extra Launch", "u32"),
    "kb_unknown": ("owned_kb_unknown", "Owned Launch Adjust", "u32"),
    "kb_x": ("owned_kb_x", "Owned X Knockback", "f32"),
    "air_kb": ("owned_air_kb", "Owned Y Knockback", "f32"),
    "hitstun": ("owned_hitstun", "Owned Hitstun", "u8"),
    "blockstun": ("owned_blockstun", "Owned Blockstun", "u8"),
    "hitstop": ("owned_hitstop", "Owned Hitstop", "u8"),
    "attack_property": ("owned_attack_property", "Owned Attack Property", "u8"),
    "hit_reaction": ("owned_hit_reaction", "Owned Hit Reaction", "u24"),
}

SUPER_OWNED_COLUMNS = tuple(SUPER_OWNED_FIELD_INFO.keys())


# Payload discovery is intentionally heuristic. The dispatch graph tells us how
# a super is called; the payload scanner tells us what known field layouts exist
# nearby. A random character can therefore get:
#   Dispatch -> child target -> attached payload candidates -> known editable fields
# without hard-coding Ryu/Morrigan addresses.
_PAYLOAD_KEEP_MAX = 6
_CHILD_SCOUT_LEN = 0x100
_CHILD_PAYLOAD_SCAN_LEN = 0x900
_CHILD_BACKSCAN_LEN = 0x900
_CHILD_FORWARD_SCAN_LEN = 0x1200

_PAYLOAD_FIELD_ORDER = (
    ("dmg", "dmg"),
    ("kb_x", "kx"),
    ("kb_y", "ky"),
    ("radius", "rad"),
    ("fx", "fx"),
    ("spawn_origin", "org"),
    ("speed", "spd"),
    ("accel", "acc"),
    ("hitbox", "hb"),
    ("lifetime", "life"),
    ("super_lifetime", "life"),
    ("super_hit_count", "hits"),
    ("super_hit_interval", "int"),
    ("super_particle_fx", "fx"),
    ("super_spawn_bone", "bone"),
    ("super_speed", "spd"),
    ("super_accel", "force"),
    ("super_radius", "rad"),
    ("super_beam_width", "width"),
    ("super_beam_visual", "visual"),
    ("super_final_damage", "final"),
    ("ps_lifetime", "life"),
    ("ps_hit_count", "hits"),
    ("ps_emit_count", "emit"),
    ("ps_interval", "int"),
    ("ps_scale", "scale"),
    ("ps_particle_fx", "fx"),
    ("ps_projectile_id", "pid"),
    ("ps_spawn_bone", "bone"),
)

_SUPERISH_FMT_EXACT = {
    "super_struct", "super_struct_card", "super_struct_card2", "super_beam_card",
    "projectile_emitter", "morrigan_fs_missile",
}

# Unaligned 00/23 dispatch row layout.  This is the part that matched both
# Ryu/Shinkuu and Morrigan/Finishing Shower:
#
#   +0x00 u16  0x0023       dispatch/action row opcode
#   +0x02 u32  selector     action/animation selector, e.g. 0x60
#   +0x06 u32  variant      branch/variant, often 0x0E
#   +0x0A u32  phase        phase length/duration-ish, often 0x1E
#   +0x0E u32  param_a
#   +0x12 u32  param_b
#   +0x16 u32  child_link   script offset, e.g. 0x00041BE0
#
# Rows are commonly 0x1C apart.
_OPCODE_OFF = 0x00
_SELECTOR_OFF = 0x02
_VARIANT_OFF = 0x06
_PHASE_OFF = 0x0A
_PARAM_A_OFF = 0x0E
_PARAM_B_OFF = 0x12
_CHILD_LINK_OFF = 0x16
_ROW_SIZE = 0x1C

_SCAN_BLOCK = 0x40000
_SCRIPT_LINK_MIN = 0x00010000
_SCRIPT_LINK_MAX = 0x00090000

_DAMAGE_HDR = b"\x35\x10\x20\x3F"
_KB_HDRS = (b"\x35\x07\x00\x20", b"\x35\x09\x00\x20")
_STUN_HDR = (
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x54,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, None,
    0x04, 0x01, 0x60, 0x00, 0x00, 0x00, 0x02, 0x58,
    0x3F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, None,
    0x33, 0x32, 0x00, 0x20, 0x00, 0x00, None,
)
_ATKPROP_HDR = bytes([
    0x04, 0x01, 0x60, 0x00,
    0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00,
])
_HITREACTION_HDR = bytes([
    0x04, 0x17, 0x60, 0x00,
    0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00,
    0x80, 0x04, 0x2F, 0x00,
    0x04, 0x15, 0x60, 0x00,
    0x00, 0x00, 0x02, 0x40,
    0x3F, 0x00, 0x00, 0x00,
])


def super_key_for_char(char_name: str | None) -> str | None:
    return P._NAME_TO_KEY.get(str(char_name)) if char_name else None


def _u16(data: bytes, off: int) -> int:
    return struct.unpack_from(">H", data, off)[0]


def _u32(data: bytes, off: int) -> int:
    return struct.unpack_from(">I", data, off)[0]


def _f32(data: bytes, off: int) -> float:
    return struct.unpack_from(">f", data, off)[0]


def _match_pat(data: bytes, off: int, pat) -> bool:
    if off < 0 or off + len(pat) > len(data):
        return False
    for i, b in enumerate(pat):
        if b is not None and data[off + i] != int(b):
            return False
    return True


def _to_intish(value, default=None):
    if value in (None, "", "?"):
        return default
    if isinstance(value, int):
        return value
    try:
        s = str(value).strip()
        return int(s, 16) if s.lower().startswith("0x") else int(float(s))
    except Exception:
        return default


def _current_slot_ranges_for_key(key: str | None) -> list[tuple[int, int, int | None]]:
    """Return [(chr_tbl_base, end, slot_char_id)] for the requested character.

    Prefer live slot ownership, but fall back to all chr_tbl regions when live
    fighter IDs are unavailable so the scanner still works in offline-ish setups.
    """
    try:
        bases = list(P._current_chr_tbl_bases())
    except Exception:
        bases = list(getattr(P, "_CHR_TBL_BASES", []))
    if not bases:
        bases = [0x90896640, 0x908F1920, 0x909478E0, 0x9099D9C0]
    size = int(getattr(P, "_CHR_TBL_SLOT_SIZE", 0x80000) or 0x80000)

    slot_ids = {}
    try:
        slot_ids = dict(P._read_slot_char_ids())
    except Exception:
        slot_ids = {}

    out: list[tuple[int, int, int | None]] = []
    for base in bases:
        cid = slot_ids.get(int(base))
        owner_key = None
        try:
            owner_key = P._projectile_key_from_char_id(cid)
        except Exception:
            owner_key = None
        if key and owner_key and owner_key != key:
            continue
        script_start = int(base) & ~0xFFF
        out.append((script_start, script_start + size, cid))

    # If live slot IDs did not identify this key, scan all current regions.  The
    # final rows still carry the requested key/name so the UI has something to show.
    if not out:
        out = [((int(base) & ~0xFFF), (int(base) & ~0xFFF) + size, None) for base in bases]
    return out


def _script_base_for_slot(slot_base: int | None, row_addr: int | None = None, link: int | None = None) -> int:
    """Return the relocatable script base used by 00/23 dispatch links.

    The old prototype used a fixed 0x90893000 base, which only worked for one
    loaded slot.  The dumps show the correct base is the current character
    script bank, page-aligned from that slot's chr_tbl label/base.

    Examples from the same build:
      Ryu P2:        chr_tbl 0x908F7EA0 -> script base 0x908F7000
      Ippatsuman:    chr_tbl 0x9094DE60 -> script base 0x9094D000
      Karas P1:      chr_tbl 0x90896640 -> script base 0x90896000

    If the caller cannot provide a slot base, infer a page nearby from
    row_addr-link.  That keeps offline/debug rows usable without baking in an
    absolute address.
    """
    try:
        if slot_base:
            return int(slot_base) & ~0xFFF
    except Exception:
        pass
    try:
        if row_addr is not None and link is not None:
            return (int(row_addr) - int(link)) & ~0xFFF
    except Exception:
        pass
    return 0x90893000


def _link_target_for_addr(row_addr: int, link: int, slot_base: int | None = None) -> int:
    return _script_base_for_slot(slot_base, row_addr=row_addr, link=link) + int(link)


def _cached_rbytes(addr: int, size: int, read_cache: dict[tuple[int, int], bytes] | None = None) -> bytes:
    """Read Dolphin memory with a per-scan range cache.

    Child field sniffing reads a larger window, then scout text asks for a
    smaller prefix of the same window.  Reuse containing cached ranges instead
    of issuing another Dolphin read.
    """
    if P.rbytes is None:
        return b""
    addr = int(addr)
    size = int(size)
    key = (addr, size)
    if read_cache is not None:
        if key in read_cache:
            return read_cache[key]
        end = addr + size
        for (cached_addr, cached_size), cached_data in list(read_cache.items()):
            cached_end = int(cached_addr) + int(cached_size)
            if int(cached_addr) <= addr and end <= cached_end:
                rel = addr - int(cached_addr)
                data = bytes(cached_data[rel:rel + size])
                read_cache[key] = data
                return data
    try:
        data = P.rbytes(addr, size) or b""
    except Exception:
        data = b""
    if read_cache is not None:
        read_cache[key] = data
    return data


def _valid_dispatch_row(data: bytes, off: int, abs_addr: int, slot_start: int, slot_end: int) -> dict[str, Any] | None:
    if off < 0 or off + _ROW_SIZE > len(data):
        return None
    if data[off:off + 2] != b"\x00\x23":
        return None
    try:
        selector = _u32(data, off + _SELECTOR_OFF)
        variant = _u32(data, off + _VARIANT_OFF)
        phase = _u32(data, off + _PHASE_OFF)
        param_a = _u32(data, off + _PARAM_A_OFF)
        param_b = _u32(data, off + _PARAM_B_OFF)
        child_link = _u32(data, off + _CHILD_LINK_OFF)
    except Exception:
        return None

    # Keep this generic, but avoid random byte soup.  Confirmed rows commonly
    # use selector 0x30..0x70, variant 0x0E, phase 0x1E, and 00/04xxxx links.
    if not (0 <= selector <= 0x200):
        return None
    if not (0 <= variant <= 0x200):
        return None
    if not (0 <= phase <= 0x400):
        return None
    if not (_SCRIPT_LINK_MIN <= child_link <= _SCRIPT_LINK_MAX):
        return None
    child_target = _link_target_for_addr(abs_addr, child_link, slot_start)
    # Resolved target should at least land in MEM2 and, for normal slot-0 dumps,
    # usually near the same script bank.  Do not require same chr_tbl range yet;
    # Ryu/Morrigan links resolve before/around the discovered chr_tbl label.
    if not (0x90000000 <= child_target < 0x94000000):
        return None

    return {
        "addr": abs_addr,
        "opcode": 0x0023,
        "selector": selector,
        "variant": variant,
        "phase": phase,
        "param_a": param_a,
        "param_b": param_b,
        "child_link": child_link,
        "child_target": child_target,
        "slot_base": slot_start,
        "slot_end": slot_end,
    }


def _group_dispatch_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted(rows, key=lambda h: int(h.get("addr") or 0))
    groups: list[list[dict[str, Any]]] = []
    cur: list[dict[str, Any]] = []
    last_addr = None
    for h in rows:
        addr = int(h.get("addr") or 0)
        if cur and last_addr is not None and addr - last_addr not in (_ROW_SIZE, _ROW_SIZE * 2):
            groups.append(cur)
            cur = []
        cur.append(h)
        last_addr = addr
    if cur:
        groups.append(cur)

    out: list[dict[str, Any]] = []
    # Keep all well-formed 00/23 dispatch groups.  The important discovery path
    # is parent -> child offset -> forward field sniff, and several real super
    # payload parents use selectors below 0x60.  Filtering to only 0x60/0x61/0x70
    # keeps the high-level animation caller but hides the useful hit/emitter
    # parents, which is exactly what made Grand Slam show only a child row with
    # no visible 1360 damage.

    for gi, group in enumerate(groups, start=1):
        start = int(group[0].get("addr") or 0)
        end = int(group[-1].get("addr") or start)
        selectors = []
        for row in group:
            sel = int(row.get("selector") or 0)
            if sel not in selectors:
                selectors.append(sel)
        label = f"Super candidate {gi}"
        for idx, row in enumerate(group, start=1):
            row["dispatch_group"] = label
            row["dispatch_index"] = idx
            row["dispatch_count"] = len(group)
            row["dispatch_range_start"] = start
            row["dispatch_range_end"] = end
            row["selector_set"] = selectors
            out.append(row)
    return out




# ---------------------------------------------------------------------------
# Smart super identity from the move scanner
#
# The 00/23 table tells us "selector 0x60 calls a child script".  The move
# scanner already knows that id 0x160 is Shinkuu/Grand Slam/etc.  Use that
# relationship instead of promoting whichever payload happens to look large.
# This is what keeps Ippatsuman's Mound Blast from being mislabeled as Grand
# Slam just because it is a nearby projectile-like payload.
# ---------------------------------------------------------------------------
_SUPER_ID_MIN = 0x160
_SUPER_ID_MAX = 0x180
_EXTRA_SUPER_IDS = {0x154, 0x155, 0x156, 0x157, 0x158, 0x159, 0x15A, 0x15B, 0x15C, 0x15D, 0x15E, 0x15F}
_BAD_SUPER_NAME_BITS = (
    "assist", "tag", "taunt", "jump", "landing", "pre jump", "turn around",
    "crouch", "hitreaction", "push block", "standby", "leave",
)
_TOKEN_STOP = {
    "the", "and", "attack", "super", "final", "anim", "start", "end", "air",
    "ground", "second", "hit", "breaker", "summon",
}


def _clean_name(value) -> str:
    return str(value or "").strip()


def _move_label(mv: dict | None) -> str:
    if not isinstance(mv, dict):
        return ""
    for k in ("family_label", "pretty_name", "move_name"):
        text = _clean_name(mv.get(k))
        if text and text.lower() not in {"anim_--", "none", "-"}:
            return text
    return ""


def _is_super_id(aid) -> bool:
    try:
        aid = int(aid)
    except Exception:
        return False
    return (_SUPER_ID_MIN <= aid < _SUPER_ID_MAX) or aid in _EXTRA_SUPER_IDS


def _is_meaningful_super_move(mv: dict | None) -> bool:
    if not isinstance(mv, dict) or not _is_super_id(mv.get("id")):
        return False
    label = _move_label(mv)
    low = label.lower()
    if not label or low.startswith("anim_"):
        return False
    if any(bit in low for bit in _BAD_SUPER_NAME_BITS):
        return False
    return True


def _name_tokens(text: str) -> set[str]:
    import re
    out = set()
    for tok in re.findall(r"[A-Za-z0-9]+", str(text or "").lower()):
        if len(tok) < 3 or tok in _TOKEN_STOP:
            continue
        out.add(tok)
    return out


def _super_entries_by_selector(move_hits: list[dict[str, Any]] | None) -> dict[int, list[dict[str, Any]]]:
    entries: dict[int, list[dict[str, Any]]] = {}
    for mv in list(move_hits or []):
        if not _is_meaningful_super_move(mv):
            continue
        aid = int(mv.get("id") or 0)
        sel = aid & 0xFF
        label = _move_label(mv)
        addr = _to_intish(mv.get("abs"), 0) or 0
        entry = {
            "selector": sel,
            "id": aid,
            "name": label,
            "family": _clean_name(mv.get("family_label")) or label,
            "addr": addr,
            "post_link": mv.get("post_link"),
            "damage": mv.get("damage"),
            "tokens": _name_tokens(label),
            "raw": mv,
        }
        # De-duplicate exact same entry rows but keep alternate roots/copies.
        sig = (entry["id"], entry["addr"], entry["name"])
        bucket = entries.setdefault(sel, [])
        if not any((e.get("id"), e.get("addr"), e.get("name")) == sig for e in bucket):
            bucket.append(entry)
    for bucket in entries.values():
        bucket.sort(key=lambda e: (0 if e.get("damage") in (None, "", "?") else 1, int(e.get("addr") or 0)))
    return entries


def _selector_name(entries: list[dict[str, Any]] | None) -> str:
    names: list[str] = []
    for e in list(entries or []):
        name = _clean_name(e.get("family") or e.get("name"))
        if name and name not in names:
            names.append(name)
    return " / ".join(names[:3])


def _primary_super_entry(entries: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    entries = list(entries or [])
    if not entries:
        return None
    # Prefer the entry/root-looking row: named, no direct hit damage, earliest.
    return sorted(entries, key=lambda e: (0 if e.get("damage") in (None, "", "?") else 1, int(e.get("addr") or 0)))[0]





# ---------------------------------------------------------------------------
# projectilemap.json as the expected-damage map for ALL specials/projectiles
# ---------------------------------------------------------------------------
# The graph scanner is not only for 0x160+ supers.  The real dynamic route is:
#   00/23 parent -> child offset -> scan forward -> match known field packets.
# projectilemap.json is the project-maintained reference table for expected move damage,
# so the child sniffer uses it as a whitelist/label source for every special.
# Addresses are never stored; only damage/name signatures are used.

def _projectilemap_for_key(key: str | None) -> list[dict[str, Any]]:
    if not key:
        return []
    try:
        proj_map = P._load_map() or {}
    except Exception:
        proj_map = {}
    raw = list(proj_map.get(str(key), []) or [])
    out: list[dict[str, Any]] = []
    for e in raw:
        if not isinstance(e, dict):
            continue
        dmg = _to_intish(e.get("dmg"), None)
        name = _clean_name(e.get("move"))
        if dmg in (None, 0) or not name:
            continue
        out.append({
            "key": key,
            "move": name,
            "dmg": int(dmg),
            "tokens": _name_tokens(name),
            "raw": dict(e),
        })
    return out


def _projectilemap_damage_lookup(key: str | None) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = {}
    for e in _projectilemap_for_key(key):
        out.setdefault(int(e["dmg"]), []).append(e)
    return out



def _projectilemap_damage_forward_matches(data: bytes, scan_start: int, dmg_lookup: dict[int, list[dict[str, Any]]] | None) -> list[dict[str, Any]]:
    'Return projectilemap damage occurrences in child-forward order.\n\n    This is intentionally close to the selected manual workflow:\n      parent -> child target -> search forward for the known damage.\n\n    Scan the child window once instead of running ``bytes.find`` once per\n    projectilemap entry.  That keeps Rescan specials usable on large character\n    maps while still checking both TvC widths:\n      - u32: 00 00 05 50\n      - u16: 05 50\n    '
    if not data or not dmg_lookup:
        return []
    wanted = {int(k) for k in (dmg_lookup or {}).keys() if 1 <= int(k) <= 50000}
    if not wanted:
        return []
    matches: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    n = len(data)
    for idx in range(0, n - 1):
        # u16 damage can appear unaligned in compact/projectile payloads.
        try:
            v16 = struct.unpack_from(">H", data, idx)[0]
        except Exception:
            continue
        if v16 in wanted:
            sig = (idx, int(v16), "u16")
            if sig not in seen:
                seen.add(sig)
                names = _map_names_for_damage(int(v16), dmg_lookup)
                matches.append({
                    "offset": idx,
                    "addr": int(scan_start) + idx,
                    "value": int(v16),
                    "width": 2,
                    "type": "u16",
                    "names": list(names),
                    "label": " / ".join(names[:3]) if names else f"damage {int(v16)}",
                })
        if idx + 4 <= n:
            try:
                v32 = struct.unpack_from(">I", data, idx)[0]
            except Exception:
                continue
            if v32 in wanted:
                sig = (idx, int(v32), "u32")
                if sig not in seen:
                    seen.add(sig)
                    names = _map_names_for_damage(int(v32), dmg_lookup)
                    matches.append({
                        "offset": idx,
                        "addr": int(scan_start) + idx,
                        "value": int(v32),
                        "width": 4,
                        "type": "u32",
                        "names": list(names),
                        "label": " / ".join(names[:3]) if names else f"damage {int(v32)}",
                    })
    matches.sort(key=lambda m: (int(m.get("offset") or 0), 0 if m.get("type") == "u32" else 1, int(m.get("value") or 0)))
    return matches


def _map_names_for_damage(dmg: int, dmg_lookup: dict[int, list[dict[str, Any]]] | None) -> list[str]:
    names: list[str] = []
    for e in list((dmg_lookup or {}).get(int(dmg), []) or []):
        nm = _clean_name(e.get("move"))
        if nm and nm not in names:
            names.append(nm)
    return names


def _map_match_label(dmg: int, dmg_lookup: dict[int, list[dict[str, Any]]] | None) -> str:
    names = _map_names_for_damage(int(dmg), dmg_lookup)
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    return " / ".join(names[:3])

def _entry_super_proofs(entries: list[dict[str, Any]] | None) -> list[str]:
    """Return hard reasons that an action identity is a super.

    This is the gate that prevents payload shape from becoming the super
    signature.  A payload is only promoted when the owning action graph has a
    super marker: meter use, SuperBG/super-freeze, a super-range move id, a
    super/hyper kind, or an explicit move-map super label.
    """
    proofs: list[str] = []
    for e in list(entries or []):
        raw = e.get("raw") if isinstance(e, dict) else None
        if not isinstance(raw, dict):
            raw = {}
        aid = _to_intish(e.get("id"), None)
        if aid is not None and _is_super_id(aid):
            txt = f"move id 0x{int(aid):03X}"
            if txt not in proofs:
                proofs.append(txt)
        meter = _to_intish(raw.get("meter"), None)
        if meter not in (None, 0):
            txt = f"meter {int(meter)}"
            if txt not in proofs:
                proofs.append(txt)
        if raw.get("superbg_addr") not in (None, "", "?") or raw.get("superbg_val") not in (None, "", "?", 0):
            if "SuperBG" not in proofs:
                proofs.append("SuperBG")
        kind = str(raw.get("kind") or "").lower()
        if kind in {"super", "hyper"}:
            txt = f"kind {kind}"
            if txt not in proofs:
                proofs.append(txt)
        label = str(e.get("name") or e.get("family") or "").lower()
        if "super" in label and "move-map label" not in proofs:
            proofs.append("move-map label")
    return proofs[:6]


def _damage_addr_for_move(mv: dict[str, Any]) -> int | None:
    for k in ("damage_addr", "dmg_write_addr"):
        val = _to_intish(mv.get(k), None)
        if val:
            return int(val)
    # Fall back to the command start when the dumper has not split out the
    # exact write address yet.  This is still graph-owned, just less precise.
    val = _to_intish(mv.get("abs"), None)
    return int(val) if val else None

def _move_payloads_for_entries(entries: list[dict[str, Any]] | None, move_hits: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Find script-hit payloads owned by the named super action graph.

    The important rule is ownership, not payload shape:
      super move/root -> post/child link -> script hit payload

    This is why Ippatsuman's Grand Slam 1360 hit is promoted while Mound Blast
    remains a projectile payload.  Both can have damage.  Only the former is
    owned by the Grand Slam action graph.
    """
    primary = _primary_super_entry(entries)
    if not primary:
        return []
    root_addr = int(primary.get("addr") or 0)
    root_post = primary.get("post_link")
    if not root_addr and root_post is None:
        return []

    selected: list[tuple[int, dict[str, Any]]] = []
    for mv in list(move_hits or []):
        dmg = mv.get("damage")
        if dmg in (None, "", "?", 0):
            continue
        addr = _to_intish(mv.get("abs"), 0) or 0
        if not addr:
            continue
        delta = addr - root_addr if root_addr else 0
        same_post = (root_post is not None and mv.get("post_link") == root_post)
        near_after = (root_addr and 0 <= delta <= 0x800)
        very_near = (root_addr and abs(delta) <= 0x180)
        if not (same_post or near_after or very_near):
            continue

        nm = str(mv.get("move_name") or mv.get("pretty_name") or "").lower()
        penalty = 0
        # Generic state labels can be duplicate scan aliases on the same graph;
        # keep them as fallback but prefer unnamed/direct script rows.
        if any(bit in nm for bit in ("assist", "jump", "landing", "turn", "taunt")):
            penalty += 30
        if nm.startswith("anim_--"):
            penalty -= 5
        if not same_post:
            penalty += 15
        relation = "same post-link" if same_post else ("near super root" if near_after else "near super root window")
        score = penalty + (0 if same_post else abs(delta))
        label = _clean_name(primary.get("family") or primary.get("name")) or _move_label(mv) or "Script Hit"
        hit = {
            "addr": addr,
            "fmt": "script_hit_payload",
            "move": label,
            "key": mv.get("key"),
            "dmg": dmg,
            "damage_addr": _damage_addr_for_move(mv),
            "source_move_id": mv.get("id"),
            "source_move_name": mv.get("move_name") or mv.get("pretty_name"),
            "post_link": mv.get("post_link"),
            "owner": "super_graph",
            "owner_relation": relation,
            "owner_root": root_addr,
            "owner_post_link": root_post,
            "owner_proof": f"{relation} from {label}",
        }
        selected.append((score, hit))

    out: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int]] = set()
    for _score, hit in sorted(selected, key=lambda x: x[0]):
        sig = (int(hit.get("addr") or 0), int(hit.get("dmg") or 0), int(hit.get("post_link") or 0))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(hit)
        if len(out) >= 6:
            break
    return out

def _payload_matches_entries(payload: dict | None, entries: list[dict[str, Any]] | None) -> bool:
    if not isinstance(payload, dict) or not entries:
        return False
    p_tokens = _name_tokens(str(payload.get("move") or "") + " " + str(payload.get("cluster") or ""))
    if not p_tokens:
        return False
    e_tokens: set[str] = set()
    for e in entries:
        e_tokens |= set(e.get("tokens") or set())
        e_tokens |= _name_tokens(str(e.get("family") or "") + " " + str(e.get("name") or ""))
    return bool(p_tokens & e_tokens)


def _unique_payloads(items: list[dict[str, Any]], limit: int = _PAYLOAD_KEEP_MAX) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for h in items:
        addr = _to_intish(h.get("addr"), 0) or 0
        sig = (int(addr), str(h.get("fmt") or ""), str(h.get("move") or ""))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(dict(h))
        if len(out) >= limit:
            break
    return out

def _fmt_scalar(v) -> str:
    if v in (None, "", "?"):
        return ""
    try:
        if isinstance(v, float):
            return f"{v:g}"
        if isinstance(v, int):
            return str(v) if abs(v) <= 9999 else f"0x{v:X}"
        sv = str(v).strip()
        if not sv:
            return ""
        # Preserve existing range summaries from emitter rows.
        if ".." in sv:
            return sv
        fv = float(sv)
        return f"{fv:g}"
    except Exception:
        return str(v)


def _payload_is_superish(hit: dict | None) -> bool:
    if not isinstance(hit, dict):
        return False
    fmt = str(hit.get("fmt") or "").lower()
    move = str(hit.get("move") or "").lower()
    cluster = str(hit.get("cluster") or "").lower()
    role = str(hit.get("proj_role") or "").lower()
    if fmt in _SUPERISH_FMT_EXACT:
        return True
    try:
        if fmt in getattr(P, "PROJECTILE_SUPER_FMTS", set()):
            return True
    except Exception:
        pass
    if fmt.startswith("super_") or "super" in fmt:
        return True
    if "super" in move or "finishing shower" in move or "voltekka" in move or "machine gun sweep" in move:
        return True
    if "super" in cluster or role == "emitter":
        return True
    # Beam cards and compact projectile-super rows usually expose these fields.
    for k, _label in _PAYLOAD_FIELD_ORDER:
        if hit.get(k) not in (None, "", "?") and k.startswith(("super_", "ps_")):
            return True
    return False


def _payload_field_bits(hit: dict | None, max_bits: int = 9) -> list[str]:
    if not isinstance(hit, dict):
        return []
    bits: list[str] = []
    seen_labels: set[str] = set()
    for key, label in _PAYLOAD_FIELD_ORDER:
        value = hit.get(key)
        if value in (None, "", "?"):
            continue
        text = _fmt_scalar(value)
        if not text:
            continue
        # Let both radius lanes show, but avoid duplicate dmg/dmg aliases.
        sig = f"{label}:{text}"
        if sig in seen_labels:
            continue
        seen_labels.add(sig)
        bits.append(f"{label} {text}")
        if len(bits) >= max_bits:
            break
    return bits


def _payload_summary(hit: dict | None, max_bits: int = 7) -> str:
    if not isinstance(hit, dict):
        return ""
    addr = _to_intish(hit.get("addr"), 0) or 0
    fmt = str(hit.get("fmt") or "payload")
    move = str(hit.get("move") or "Payload").strip() or "Payload"
    bits = _payload_field_bits(hit, max_bits=max_bits)
    field_txt = " | ".join(bits)
    head = f"{move} @0x{addr:08X} {fmt}"
    dmg_addr = _to_intish(hit.get("damage_addr"), None)
    if dmg_addr and hit.get("dmg") not in (None, "", "?"):
        field_txt = (field_txt + " | " if field_txt else "") + f"dmg_addr 0x{int(dmg_addr):08X}"
    owner = str(hit.get("owner") or "").strip()
    if owner:
        head += f" [{owner}]"
    return f"{head}: {field_txt}" if field_txt else head


def _owner_payload_summary(items: list[dict[str, Any]], max_items: int = 3) -> str:
    owned = [h for h in list(items or []) if str(h.get("owner") or "") == "super_graph"]
    return " ; ".join(_payload_summary(h, max_bits=6) for h in owned[:max_items])


def _simple_payload(hit: dict) -> dict[str, Any]:
    keys = {
        "addr", "fmt", "move", "key", "cluster", "proj_role",
        "owner", "owner_relation", "owner_root", "owner_post_link", "owner_proof",
        "damage_addr", "dmg_write_addr", "source_move_id", "source_move_name", "post_link",
        "dmg", "kb_x", "kb_y", "radius", "fx", "spawn_origin", "speed", "accel", "hitbox", "lifetime",
        "super_lifetime", "super_hit_count", "super_hit_interval", "super_particle_fx",
        "super_spawn_bone", "super_speed", "super_accel", "super_radius", "super_beam_width",
        "super_beam_visual", "super_final_damage",
        "ps_lifetime", "ps_hit_count", "ps_emit_count", "ps_interval", "ps_scale",
        "ps_particle_fx", "ps_projectile_id", "ps_spawn_bone",
    }
    out = {k: hit.get(k) for k in keys if k in hit and hit.get(k) not in (None, "", "?")}
    # The projectile scanner historically calls this dmg_write_addr while the
    # super-owned script payload path calls it damage_addr.  Normalize it so
    # the parent/child tree can display and edit Damage from either source.
    if out.get("damage_addr") in (None, "", "?") and out.get("dmg_write_addr") not in (None, "", "?"):
        out["damage_addr"] = out.get("dmg_write_addr")
    return out


def _payload_rank_for_group(payload: dict, group: list[dict[str, Any]]) -> tuple[int, int, int]:
    addr = _to_intish(payload.get("addr"), 0) or 0
    if not addr:
        return (99, 0x7FFFFFFF, 0)
    fmt = str(payload.get("fmt") or "")
    move = str(payload.get("move") or "").lower()
    is_strong = fmt in _SUPERISH_FMT_EXACT or fmt in getattr(P, "PROJECTILE_SUPER_FMTS", set()) or "super" in fmt or "super" in move
    anchors: list[int] = []
    for row in group:
        for k in ("addr", "child_target"):
            try:
                a = int(row.get(k) or 0)
            except Exception:
                a = 0
            if a:
                anchors.append(a)
    if not anchors:
        return (2 if is_strong else 5, 0, addr)
    dist = min(abs(addr - a) for a in anchors)
    # Dispatch rows often live after/before payload cards by a few KB to ~0x8000.
    near_bucket = 0 if dist <= 0x1000 else 1 if dist <= 0x8000 else 2 if dist <= 0x20000 else 3
    strong_bucket = 0 if is_strong else 2
    return (strong_bucket + near_bucket, dist, addr)


def _scan_payload_candidates(char_name: str | None, payload_hits: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if payload_hits is not None:
        hits = [dict(h) for h in payload_hits]
    else:
        try:
            from . import projectile_integration as FPI
            hits = list(FPI.scan_projectiles_for_char(char_name, progress_cb=None, show_unknowns=False) or [])
        except Exception:
            hits = []
    out = []
    seen: set[tuple[int, str, str]] = set()
    for h in hits:
        if not _payload_is_superish(h):
            continue
        addr = _to_intish(h.get("addr"), 0) or 0
        key = (int(addr), str(h.get("fmt") or ""), str(h.get("move") or ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(h))
    return out


def _all_payload_candidates(char_name: str | None, payload_hits: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Return all payload/template rows, not just super-looking ones.

    The action graph is for every special/projectile, so ordinary projectile
    templates have to be eligible for ownership once the parent->child path
    proves them.  Filtering to only super-ish payloads is what hid Zero/Joe
    child-owned projectile rows.
    """
    if payload_hits is not None:
        hits = [dict(h) for h in payload_hits]
    else:
        try:
            from . import projectile_integration as FPI
            hits = list(FPI.scan_projectiles_for_char(char_name, progress_cb=None, show_unknowns=False) or [])
        except Exception:
            hits = []
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    for h in hits:
        addr = _to_intish(h.get("addr"), 0) or 0
        if not addr:
            continue
        sig = (int(addr), str(h.get("fmt") or ""), str(h.get("move") or ""))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(dict(h))
    return out


def _payload_damage_value(hit: dict | None) -> int | None:
    if not isinstance(hit, dict):
        return None
    for k in ("dmg", "damage"):
        val = _to_intish(hit.get(k), None)
        if val not in (None, 0):
            return int(val)
    return None


def _payload_damage_addr(hit: dict | None) -> int | None:
    if not isinstance(hit, dict):
        return None
    for k in ("damage_addr", "dmg_write_addr"):
        val = _to_intish(hit.get(k), None)
        if val:
            return int(val)
    return None


def _payload_in_child_forward_window(payload: dict, scan_start: int, scan_end: int) -> bool:
    'True when the child forward scan owns the payload row/address.\n\n    Prefer the payload record base, but also accept the exact damage write addr\n    because some scanner formats report the record base just before the child\n    entry while the field itself is what the operator searched forward to.\n    '
    addrs = []
    for k in ("addr", "damage_addr", "dmg_write_addr"):
        val = _to_intish(payload.get(k), None)
        if val:
            addrs.append(int(val))
    return any(int(scan_start) <= a < int(scan_end) for a in addrs)


def _attach_projectilemap_payloads_from_child(row: dict[str, Any], all_payloads: list[dict[str, Any]], dmg_lookup: dict[int, list[dict[str, Any]]] | None) -> list[dict[str, Any]]:
    """Attach normal projectile/special payload rows owned by this child.

    This is the missing piece for the 'all specials' workflow:
      parent 00/23 -> child offset -> forward window -> projectilemap damage

    The payload scanner already knows exact projectile-template field offsets.
    Once the child window proves ownership, reuse those exact rows instead of
    trying to rediscover damage by a loose raw integer search.
    """
    target = _to_intish(row.get("child_target"), 0) or 0
    if not target:
        return []
    scan_start = int(row.get("owned_scan_start") or target)
    scan_end = int(row.get("owned_scan_end") or (target + _CHILD_FORWARD_SCAN_LEN))
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, int, str]] = set()
    for h in list(all_payloads or []):
        dmg = _payload_damage_value(h)
        if dmg in (None, 0):
            continue
        names = _map_names_for_damage(int(dmg), dmg_lookup)
        if dmg_lookup and not names:
            continue
        if not _payload_in_child_forward_window(h, scan_start, scan_end):
            continue
        hh = _simple_payload(h)
        hh["owner"] = "action_graph"
        hh["owner_relation"] = "child forward owned payload"
        hh["owner_root"] = row.get("addr")
        hh["owner_post_link"] = row.get("child_link")
        hh["owner_proof"] = f"parent 0x{int(row.get('addr') or 0):08X} -> child 0x{int(target):08X} -> payload 0x{int(_to_intish(hh.get('addr'), 0) or 0):08X}"
        if names:
            hh["map_names"] = list(names)
            hh["move"] = " / ".join(names[:3])
            hh["owner_proof"] += "; projectilemap " + hh["move"]
        dmg_addr = _payload_damage_addr(hh) or _payload_damage_addr(h)
        if dmg_addr:
            hh["damage_addr"] = int(dmg_addr)
        sig = (int(_to_intish(hh.get("addr"), 0) or 0), int(dmg), str(hh.get("fmt") or ""))
        if sig in seen:
            continue
        seen.add(sig)
        out.append(hh)
    return out[:_PAYLOAD_KEEP_MAX]


def _scout_child_script(hit: dict[str, Any], read_cache: dict[tuple[int, int], bytes] | None = None) -> dict[str, Any]:
    target = _to_intish(hit.get("child_target"), 0) or 0
    if not target or P.rbytes is None:
        return {}
    data = _cached_rbytes(target, _CHILD_SCOUT_LEN, read_cache)
    if len(data) < 4:
        return {}
    smalls: list[str] = []
    links: list[str] = []
    f32s: list[str] = []
    for off in range(0, max(0, len(data) - 3), 2):
        try:
            val = _u32(data, off)
        except Exception:
            continue
        if 0 < val <= 0x80:
            # These are count/timing candidates only; not proven fields.
            txt = f"+0x{off:02X}={val}"
            if txt not in smalls:
                smalls.append(txt)
        if _SCRIPT_LINK_MIN <= val <= _SCRIPT_LINK_MAX:
            tgt = _link_target_for_addr(target + off, val, hit.get("slot_base"))
            txt = f"+0x{off:02X}->0x{tgt:08X}"
            if txt not in links:
                links.append(txt)
        try:
            fv = struct.unpack_from(">f", data, off)[0]
        except Exception:
            continue
        if fv != 0.0 and abs(fv) < 10000 and (abs(fv) >= 0.001) and off % 4 == 0:
            # Keep common-looking scalars; this is for scout text only.
            if len(f32s) < 6 and any(abs(fv - x) < 1e-6 for x in (0.01, 0.02, 0.03, 0.04, 0.05, 0.1, 0.5, 1.0, 2.0, 10.0, 20.0, 30.0, 40.0, 60.0, 100.0)):
                f32s.append(f"+0x{off:02X}={fv:g}f")
    return {
        "child_small_u32": smalls[:10],
        "child_links": links[:6],
        "child_f32": f32s[:6],
    }


def _scout_summary(hit: dict[str, Any]) -> str:
    bits = []
    scout = hit.get("child_scout") or {}
    if scout.get("child_links"):
        bits.append("links " + ", ".join(scout.get("child_links")[:3]))
    if scout.get("child_small_u32"):
        bits.append("small " + ", ".join(scout.get("child_small_u32")[:4]))
    if scout.get("child_f32"):
        bits.append("f32 " + ", ".join(scout.get("child_f32")[:3]))
    return " ; ".join(bits)




def _make_owned_field(col: str, addr: int, value, packet_addr: int, source: str, typ: str | None = None) -> dict[str, Any]:
    info = SUPER_OWNED_FIELD_INFO.get(col)
    key = info[0] if info else f"owned_{col}"
    label = info[1] if info else col
    typ = typ or (info[2] if info else "u32")
    return {
        "col": col,
        "key": key,
        "label": label,
        "type": typ,
        "addr": int(addr),
        "value": value,
        "packet_addr": int(packet_addr),
        "source": source,
    }


def _field_value_text(field: dict | None) -> str:
    if not isinstance(field, dict):
        return ""
    typ = str(field.get("type") or "")
    value = field.get("value")
    if value in (None, "", "?"):
        return ""
    try:
        if typ == "f32":
            return f"{float(value):g}"
        if typ == "u24":
            return f"0x{int(value) & 0xFFFFFF:06X}"
        if typ in {"u32", "u16", "u8"} and str(field.get("col") or "") in {"launch_profile", "kb_unknown", "attack_property", "hit_reaction"}:
            return f"0x{int(value):X}" if int(value) > 9 else str(int(value))
        return str(int(value))
    except Exception:
        return str(value)


def _add_owned_field(field_map: dict[str, dict[str, Any]], field: dict[str, Any]) -> None:
    col = str(field.get("col") or "")
    if not col:
        return
    # Prefer the first field per display column.  Later duplicate hits still go
    # into the payload list; the parent row stays readable instead of showing
    # every multi-hit duplicate in one cell.
    field_map.setdefault(col, field)


def _scan_owned_script_fields(row: dict[str, Any], super_name: str, super_proofs: list[str], dmg_lookup: dict[int, list[dict[str, Any]]] | None = None, read_cache: dict[tuple[int, int], bytes] | None = None) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Sniff editable hit fields from the resolved child script.

    This is the parent-owned route:
      00/23 parent -> child target -> known script packets -> fields.

    Important: several supers point at a child entry that sits *after* the
    hit packet/payload it owns.  Ippatsuman Grand Slam is the current proof:
    the parent/child graph is correct, but the 1360 damage packet is before
    the resolved entry.  So the sniffer scans a bounded owned window around
    the child target, not only forward from it.

    The returned fields are bundled onto the parent dispatch row.  The returned
    payloads are proof objects for the same child-owned script hit packets.
    """
    if P.rbytes is None:
        return {}, []
    target = _to_intish(row.get("child_target"), 0) or 0
    if not target:
        return {}, []

    # Simple owner sniff: start exactly at the child target and scan forward.
    # Validated workflow: parent row -> child offset -> target offset.
    # offset -> search forward for the hit field.  Do not broad-backscan here;
    # it pulls unrelated earlier hit packets into the parent and produced bogus
    # values like 65360.
    scan_start = int(target)
    scan_len = int(_CHILD_FORWARD_SCAN_LEN)
    data = _cached_rbytes(scan_start, scan_len, read_cache)
    if len(data) < 8:
        return {}, []
    try:
        row["owned_scan_start"] = scan_start
        row["owned_scan_end"] = scan_start + len(data)
    except Exception:
        pass

    field_map: dict[str, dict[str, Any]] = {}
    payloads: list[dict[str, Any]] = []
    seen_payloads: set[int] = set()

    # 35/10 damage packets.  Damage is the u32 at packet + 0x04.
    pos = 0
    while True:
        idx = data.find(_DAMAGE_HDR, pos)
        if idx < 0:
            break
        pos = idx + 1
        if idx + 16 > len(data):
            continue
        try:
            dmg = _u32(data, idx + 4)
        except Exception:
            continue
        if not (1 <= int(dmg) <= 50000):
            continue
        map_names = _map_names_for_damage(int(dmg), dmg_lookup)
        # If projectilemap has entries for this character, use it as the
        # expected-damage whitelist for ordinary specials.  Still allow
        # explicit super-proof rows through, because some supers have odd
        # split/final-hit damage not yet present in projectilemap.json.
        if dmg_lookup and not map_names and not super_proofs:
            continue
        cmd_addr = scan_start + idx
        dmg_addr = cmd_addr + 4
        src = "35/10 child hit"
        if map_names:
            src += "; projectilemap " + " / ".join(map_names[:3])
        field = _make_owned_field("damage", dmg_addr, int(dmg), cmd_addr, src, "u32")
        if map_names:
            field["map_names"] = list(map_names)
            field["map_proof"] = f"projectilemap dmg {int(dmg)} -> " + " / ".join(map_names[:3])
        _add_owned_field(field_map, field)
        if cmd_addr not in seen_payloads:
            seen_payloads.add(cmd_addr)
            payload_move = (" / ".join(map_names[:3]) if map_names else (super_name or "Script Hit"))
            payloads.append({
                "addr": cmd_addr,
                "fmt": "script_hit_payload",
                "move": payload_move,
                "dmg": int(dmg),
                "damage_addr": dmg_addr,
                "owner": "action_graph",
                "owner_relation": "child script hit command",
                "owner_root": row.get("addr"),
                "owner_post_link": row.get("child_link"),
                "owner_proof": f"parent 0x{int(row.get('addr') or 0):08X} -> child 0x{target:08X} -> 35/10 hit" + (f"; projectilemap {payload_move}" if map_names else ""),
                "map_names": list(map_names),
            })

    # 35/07 or 35/09 knockback packet.
    for idx in range(0, max(0, len(data) - 20)):
        if not any(data.startswith(hdr, idx) for hdr in _KB_HDRS):
            continue
        try:
            launch = _u32(data, idx + 4)
            unk = _u32(data, idx + 8)
            kx = _f32(data, idx + 12)
            ky = _f32(data, idx + 16)
        except Exception:
            continue
        if abs(kx) > 100000 or abs(ky) > 100000:
            continue
        cmd_addr = scan_start + idx
        _add_owned_field(field_map, _make_owned_field("launch_profile", cmd_addr + 4, int(launch), cmd_addr, "35/07/09 child KB", "u32"))
        _add_owned_field(field_map, _make_owned_field("kb_unknown", cmd_addr + 8, int(unk), cmd_addr, "35/07/09 child KB", "u32"))
        _add_owned_field(field_map, _make_owned_field("kb_x", cmd_addr + 12, float(kx), cmd_addr, "35/07/09 child KB", "f32"))
        _add_owned_field(field_map, _make_owned_field("air_kb", cmd_addr + 16, float(ky), cmd_addr, "35/07/09 child KB", "f32"))
        break

    # Combined hitstun/blockstun/hitstop packet.
    for idx in range(0, max(0, len(data) - len(_STUN_HDR))):
        if not _match_pat(data, idx, _STUN_HDR):
            continue
        cmd_addr = scan_start + idx
        _add_owned_field(field_map, _make_owned_field("hitstun", cmd_addr + 15, data[idx + 15], cmd_addr, "04/01 0x254 + 0x258 + 33/32 stun", "u8"))
        _add_owned_field(field_map, _make_owned_field("blockstun", cmd_addr + 31, data[idx + 31], cmd_addr, "04/01 0x254 + 0x258 + 33/32 stun", "u8"))
        _add_owned_field(field_map, _make_owned_field("hitstop", cmd_addr + 38, data[idx + 38], cmd_addr, "04/01 0x254 + 0x258 + 33/32 stun", "u8"))
        break

    # Attack property and hit reaction packets from the normal scanner.
    pos = data.find(_ATKPROP_HDR)
    if pos >= 0 and pos + len(_ATKPROP_HDR) < len(data):
        cmd_addr = scan_start + pos
        _add_owned_field(field_map, _make_owned_field("attack_property", cmd_addr + len(_ATKPROP_HDR), data[pos + len(_ATKPROP_HDR)], cmd_addr, "04/01 attack property", "u8"))

    pos = data.find(_HITREACTION_HDR)
    if pos >= 0 and pos + len(_HITREACTION_HDR) + 3 <= len(data):
        cmd_addr = scan_start + pos
        code_addr = cmd_addr + 28
        code = (data[pos + 28] << 16) | (data[pos + 29] << 8) | data[pos + 30]
        _add_owned_field(field_map, _make_owned_field("hit_reaction", code_addr, int(code), cmd_addr, "04/17 + 04/15 hit reaction", "u24"))

    # projectilemap raw damage scan.  Some action children do not expose the
    # damage through a clean 35/10 packet; they just contain the known damage
    # value in the payload stream.  This now searches both u32 and u16 forms,
    # because Grand Slam/Homerun-style payloads can be 16-bit damage values.
    if dmg_lookup:
        matches = _projectilemap_damage_forward_matches(data, scan_start, dmg_lookup)
        if matches:
            m = matches[0]
            # A projectilemap-owned forward hit is stronger than a generic
            # non-map packet value.  If the packet damage was also map-backed,
            # keep the packet because it gives the cleaner command source.
            existing = field_map.get("damage")
            existing_map = isinstance(existing, dict) and bool(existing.get("map_proof"))
            if (not existing) or (not existing_map):
                dmg_addr = int(m["addr"])
                dmg_i = int(m["value"])
                label = str(m.get("label") or f"damage {dmg_i}")
                typ = str(m.get("type") or "u32")
                field = _make_owned_field(
                    "damage",
                    dmg_addr,
                    dmg_i,
                    dmg_addr,
                    f"projectilemap child-forward {typ} match: {label}",
                    typ,
                )
                field["map_names"] = list(m.get("names") or [])
                field["map_proof"] = f"projectilemap dmg {dmg_i} -> {label}"
                # Replace generic/non-map damage rather than preserving a false
                # first hit like 0xFF50/65360.
                field_map["damage"] = field
                if dmg_addr not in seen_payloads:
                    seen_payloads.add(dmg_addr)
                    payloads.append({
                        "addr": dmg_addr,
                        "fmt": f"projectilemap_{typ}_damage_payload",
                        "move": label,
                        "dmg": dmg_i,
                        "damage_addr": dmg_addr,
                        "owner": "action_graph",
                        "owner_relation": f"child forward projectilemap {typ} match",
                        "owner_root": row.get("addr"),
                        "owner_post_link": row.get("child_link"),
                        "owner_proof": f"parent 0x{int(row.get('addr') or 0):08X} -> child 0x{target:08X} -> projectilemap {typ} dmg {dmg_i}",
                        "map_names": list(m.get("names") or []),
                    })

    return field_map, payloads


def _owned_field_summary(field_map: dict[str, dict[str, Any]], max_items: int = 8) -> str:
    order = ("damage", "kb_x", "air_kb", "hitstun", "blockstun", "hitstop", "attack_property", "hit_reaction", "launch_profile", "kb_unknown")
    bits: list[str] = []
    labels = {
        "damage": "dmg", "kb_x": "kx", "air_kb": "ky", "hitstun": "hs",
        "blockstun": "bs", "hitstop": "stop", "attack_property": "prop",
        "hit_reaction": "react", "launch_profile": "extra", "kb_unknown": "adj",
    }
    for col in order:
        field = field_map.get(col)
        if not field:
            continue
        bits.append(f"{labels.get(col, col)} {_field_value_text(field)} @0x{int(field.get('addr') or 0):08X}")
        if len(bits) >= max_items:
            break
    return " ; ".join(bits)


def _merge_payload_fields_into_owned_map(field_map: dict[str, dict[str, Any]], payloads: list[dict[str, Any]]) -> None:
    'Promote graph-owned payload field addresses into visible owned fields.\n\n    The child packet sniffer catches explicit 35/10-style script commands.  Some\n    scanner-owned hit payloads are discovered through the move scanner instead;\n    those arrive as payload candidates with a damage address but no entry in\n    owned_field_map.  Without this merge the tree shows only the child script row\n    and hides the useful Damage value the operator actually wants.\n    '
    for h in list(payloads or []):
        if str(h.get("owner") or "") not in {"super_graph", "action_graph"}:
            continue
        dmg = h.get("dmg")
        dmg_addr = _to_intish(h.get("damage_addr") or h.get("dmg_write_addr"), None)
        if "damage" not in field_map and dmg not in (None, "", "?") and dmg_addr:
            pkt = _to_intish(h.get("addr"), dmg_addr) or dmg_addr
            _add_owned_field(
                field_map,
                _make_owned_field(
                    "damage",
                    int(dmg_addr),
                    int(float(dmg)),
                    int(pkt),
                    str(h.get("owner_relation") or h.get("fmt") or "graph-owned payload"),
                    "u32",
                ),
            )
        # Keep payload scalar values visible in summaries even when do not
        # have proven write addresses for them yet.  They remain payload rows in
        # the tree, while address-backed values above become editable fields.

def _script_hit_payloads_from_child(row: dict[str, Any], super_name: str, super_proofs: list[str]) -> list[dict[str, Any]]:
    """Compatibility wrapper for older callers.

    The real sniffer now returns both owned fields and payload proof objects.
    This wrapper preserves the old payload-only return shape.
    """
    _fields, payloads = _scan_owned_script_fields(row, super_name, super_proofs, None)
    return payloads


def _attach_payloads_and_scouts(rows: list[dict[str, Any]], char_name: str | None, payload_hits: list[dict[str, Any]] | None = None, move_hits: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    rows = list(rows or [])
    if not rows:
        return rows
    # Payload hits are still scanned, but they no longer prove ownership by
    # themselves.  Scan them only once; the previous path could invoke the full
    # projectile scanner twice for every special/super refresh.
    all_payloads = _all_payload_candidates(char_name, payload_hits=payload_hits)
    payloads = [dict(h) for h in all_payloads if _payload_is_superish(h)]
    super_entries = _super_entries_by_selector(move_hits)
    map_key = super_key_for_char(char_name)
    dmg_lookup = _projectilemap_damage_lookup(map_key)
    read_cache: dict[tuple[int, int], bytes] = {}

    groups: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault(str(r.get("dispatch_group") or "Super candidate"), []).append(r)

    for _group_name, group_rows in groups.items():
        ranked_group_payloads = sorted(payloads, key=lambda h: _payload_rank_for_group(h, group_rows))
        for r in group_rows:
            sel = int(r.get("selector") or 0)
            entries = super_entries.get(sel, [])
            super_name = _selector_name(entries)
            super_proofs = _entry_super_proofs(entries)
            if super_name:
                r["super_name"] = super_name
                r["dispatch_group"] = f"{super_name} dispatch"
                r["super_entry_count"] = len(entries)
                primary = _primary_super_entry(entries)
                if primary:
                    r["super_entry_addr"] = primary.get("addr")
                    r["super_entry_post_link"] = primary.get("post_link")

            # Do the simple, relocatable ownership test for every dispatch row:
            # parent -> child offset -> forward sniff.  Do not promote payloads
            # merely because their name/damage is near a super root; that is how
            # unrelated rows got bundled before.
            owned_field_map, child_payloads = _scan_owned_script_fields(r, super_name, super_proofs, dmg_lookup, read_cache)
            child_owned_payloads = _attach_projectilemap_payloads_from_child(r, all_payloads, dmg_lookup)

            # If this is not a 0x160+ super, projectilemap can still name the
            # action from the owned damage field OR an owned projectile-template
            # payload.  This is the ALL-specials path.
            if not super_name:
                map_names: list[str] = []
                dmg_field = (owned_field_map or {}).get("damage")
                if isinstance(dmg_field, dict):
                    map_names.extend(list(dmg_field.get("map_names") or []))
                for ph in child_owned_payloads:
                    for nm in list(ph.get("map_names") or []):
                        if nm not in map_names:
                            map_names.append(nm)
                if map_names:
                    super_name = " / ".join(map_names[:3])
                    r["super_name"] = super_name
                    r["dispatch_group"] = f"{super_name} action"

            selected: list[dict[str, Any]] = []
            selected.extend(_simple_payload(h) for h in child_payloads)
            selected.extend(_simple_payload(h) for h in child_owned_payloads)

            selected = _unique_payloads(selected, limit=_PAYLOAD_KEEP_MAX)
            _merge_payload_fields_into_owned_map(owned_field_map, selected)
            payload_only: list[dict[str, Any]] = []
            if not selected:
                # Explicitly demote fallback payloads.  This is what keeps rows
                # such as Ippatsuman's Mound Blast from becoming the super.
                for h in ranked_group_payloads[:2]:
                    rank = _payload_rank_for_group(h, group_rows)
                    if rank[0] <= 3:
                        hh = _simple_payload(h)
                        hh["owner"] = "payload_only"
                        hh["owner_proof"] = "no super graph ownership proof"
                        payload_only.append(hh)

            has_map_field = bool((owned_field_map or {}).get("damage", {}).get("map_proof")) if isinstance((owned_field_map or {}).get("damage"), dict) else False
            has_map_payload = any(bool(h.get("map_names")) for h in selected)
            confidence = "confirmed" if (selected and super_proofs) else ("mapped" if (has_map_field or has_map_payload) else ("owned" if selected else ("likely" if super_name else "scout")))
            owner_proofs: list[str] = []
            for h in selected:
                proof = str(h.get("owner_proof") or h.get("owner_relation") or "").strip()
                if proof and proof not in owner_proofs:
                    owner_proofs.append(proof)
            dmg_field = (owned_field_map or {}).get("damage") if isinstance(owned_field_map, dict) else None
            if isinstance(dmg_field, dict) and dmg_field.get("map_proof") and str(dmg_field.get("map_proof")) not in owner_proofs:
                owner_proofs.insert(0, str(dmg_field.get("map_proof")))
            if not owner_proofs and payload_only:
                owner_proofs.append("payloads demoted: no owner graph proof")

            # Same-row display is the main point of the action graph view:
            # parent -> child -> projectilemap damage match becomes an owned
            # field on the PARENT row, not only a nested child/payload row.
            dmg_field = (owned_field_map or {}).get("damage") if isinstance(owned_field_map, dict) else None
            if isinstance(dmg_field, dict) and dmg_field.get("value") not in (None, "", "?"):
                try:
                    r["damage"] = int(dmg_field.get("value"))
                    r["owned_damage"] = int(dmg_field.get("value"))
                except Exception:
                    r["damage"] = dmg_field.get("value")
                    r["owned_damage"] = dmg_field.get("value")
                try:
                    r["damage_addr"] = int(dmg_field.get("addr") or 0) or None
                    r["owned_damage_addr"] = int(dmg_field.get("addr") or 0) or None
                except Exception:
                    pass
                map_names = list(dmg_field.get("map_names") or [])
                if map_names:
                    r["map_move_name"] = " / ".join(str(x) for x in map_names[:3])
                    # If the row did not already have a higher-level move name,
                    # put the projectilemap name directly on the dispatch row.
                    if not r.get("super_name"):
                        r["super_name"] = r["map_move_name"]
                        r["dispatch_group"] = f"{r['map_move_name']} action"

            payload_summary = " ; ".join(_payload_summary(h, max_bits=6) for h in selected[:3])
            payload_field_summary = " ; ".join(", ".join(_payload_field_bits(h, max_bits=10)) for h in selected[:3])
            owned_script_summary = _owned_field_summary(owned_field_map)
            if owned_script_summary:
                payload_field_summary = (owned_script_summary + (" ; " + payload_field_summary if payload_field_summary else ""))
            demoted_summary = " ; ".join(_payload_summary(h, max_bits=4) for h in payload_only[:2])
            r["owned_field_map"] = {k: dict(v) for k, v in owned_field_map.items()}
            r["owned_script_field_summary"] = owned_script_summary
            r["payload_candidates"] = [dict(h) for h in selected]
            r["payload_only_candidates"] = [dict(h) for h in payload_only]
            r["payload_count"] = len(selected)
            r["payload_summary"] = payload_summary
            r["payload_field_summary"] = payload_field_summary
            r["payload_only_summary"] = demoted_summary
            r["dispatch_confidence"] = confidence
            r["dispatch_super_proof"] = ", ".join(super_proofs)
            r["dispatch_owner_proof"] = "; ".join(owner_proofs[:4])
            r["child_scout"] = _scout_child_script(r, read_cache)
    return rows

def scan_supers_for_char(char_name: str | None, progress_cb=None, payload_hits: list[dict[str, Any]] | None = None, move_hits: list[dict[str, Any]] | None = None, attach_payloads: bool = True) -> list[dict[str, Any]]:
    key = super_key_for_char(char_name)
    if not key or P.rbytes is None:
        return []

    ranges = _current_slot_ranges_for_key(key)
    total = sum(max(0, end - start) for start, end, _cid in ranges) or 1
    done = 0
    hits: list[dict[str, Any]] = []
    seen: set[int] = set()

    for start, end, cid in ranges:
        addr = int(start)
        while addr < int(end):
            size = min(_SCAN_BLOCK, int(end) - addr)
            try:
                data = P.rbytes(addr, size) or b""
            except Exception:
                data = b""
            if data:
                pos = 0
                while True:
                    idx = data.find(b"\x00\x23", pos)
                    if idx < 0:
                        break
                    abs_addr = addr + idx
                    if abs_addr not in seen:
                        row = _valid_dispatch_row(data, idx, abs_addr, start, end)
                        if row:
                            row["key"] = key
                            row["char_id"] = cid
                            row["kind"] = "super dispatch"
                            row["fmt"] = "super_dispatch_0023"
                            row["move"] = "Super Dispatch"
                            hits.append(row)
                            seen.add(abs_addr)
                    pos = idx + 1
            done += size
            if progress_cb:
                try:
                    progress_cb(done / total * 100.0)
                except Exception:
                    pass
            addr += size

    grouped = _group_dispatch_rows(hits)
    if attach_payloads:
        grouped = _attach_payloads_and_scouts(grouped, char_name, payload_hits=payload_hits, move_hits=move_hits)
        # After the simple child-forward sniff, keep the useful rows:
        #   - named top-level super callers
        #   - dispatch rows that actually own sniffed fields/payloads
        # This keeps lower-selector hit parents visible without flooding the UI
        # with every unrelated 00/23 table.
        filtered = []
        for h in grouped:
            if h.get("super_name") or h.get("owned_field_map") or int(h.get("payload_count") or 0) > 0:
                filtered.append(h)
        grouped = filtered
    return grouped


def is_super_row(mv: dict | None) -> bool:
    return bool(isinstance(mv, dict) and mv.get("_row_type") == "super_dispatch")


def _owned_field_from_mv(mv: dict | None, col_name: str | None) -> dict[str, Any] | None:
    if not is_super_row(mv) or not col_name:
        return None
    hit = (mv or {}).get("_super_hit") or {}
    fmap = hit.get("owned_field_map") or {}
    field = fmap.get(col_name)
    return field if isinstance(field, dict) else None


def super_field_edit_info(col_name: str | None) -> tuple[str, str, str] | None:
    if not col_name:
        return None
    if col_name in SUPER_DISPATCH_FIELD_INFO:
        return SUPER_DISPATCH_FIELD_INFO[col_name]
    if col_name in SUPER_OWNED_FIELD_INFO:
        return SUPER_OWNED_FIELD_INFO[col_name]
    return None


def super_editable(col_name: str | None) -> bool:
    return bool(super_field_edit_info(col_name))


def super_group_for_col(col_name: str | None) -> tuple[str | None, tuple[str, ...]]:
    info = super_field_edit_info(col_name)
    if not col_name or not info:
        return (None, ())
    hit_key, _label, _typ = info
    return (f"super_dispatch:{hit_key}", (col_name,))


def column_for_super_group(group: str | None) -> str | None:
    if not group or not str(group).startswith("super_dispatch:"):
        return None
    hit_key = str(group).split(":", 1)[1]
    for col, (key, _label, _typ) in SUPER_DISPATCH_FIELD_INFO.items():
        if key == hit_key:
            return col
    for col, (key, _label, _typ) in SUPER_OWNED_FIELD_INFO.items():
        if key == hit_key:
            return col
    return None


def super_snapshot(mv: dict, group_key: str) -> dict[str, Any]:
    hit_key = str(group_key).split(":", 1)[1] if ":" in str(group_key) else group_key
    hit = mv.get("_super_hit") or {}
    for col, (key, _label, _typ) in SUPER_OWNED_FIELD_INFO.items():
        if key == hit_key:
            field = _owned_field_from_mv(mv, col) or {}
            return {"hit_key": hit_key, "value": field.get("value"), "field": dict(field)}
    return {"hit_key": hit_key, "value": hit.get(hit_key)}


def _fmt_u32(v) -> str:
    val = _to_intish(v)
    return "" if val is None else f"0x{int(val):08X}"


def _fmt_small(v) -> str:
    val = _to_intish(v)
    if val is None:
        return ""
    return str(int(val)) if int(val) <= 999 else f"0x{int(val):X}"


def format_super_value(mv: dict, col_name: str) -> str:
    hit = mv.get("_super_hit") or {}
    if col_name == "dispatch_group":
        return str(hit.get("dispatch_group") or "")
    if col_name == "dispatch_confidence":
        return str(hit.get("dispatch_confidence") or "")
    if col_name == "dispatch_super_proof":
        return str(hit.get("dispatch_super_proof") or "")
    if col_name == "dispatch_owner_proof":
        return str(hit.get("dispatch_owner_proof") or "")
    if col_name == "dispatch_selector":
        val = _to_intish(hit.get("selector"))
        return "" if val is None else f"0x{val:02X}"
    if col_name == "dispatch_variant":
        return _fmt_small(hit.get("variant"))
    if col_name == "dispatch_phase":
        return _fmt_small(hit.get("phase"))
    if col_name == "dispatch_child_link":
        return _fmt_u32(hit.get("child_link"))
    if col_name == "dispatch_child_target":
        return _fmt_u32(hit.get("child_target"))
    if col_name in SUPER_OWNED_FIELD_INFO:
        field = _owned_field_from_mv(mv, col_name)
        return _field_value_text(field)
    return ""


def super_edit_initial_value(mv: dict, col_name: str) -> str:
    info = super_field_edit_info(col_name)
    if not info:
        return format_super_value(mv, col_name)
    if col_name in SUPER_OWNED_FIELD_INFO:
        field = _owned_field_from_mv(mv, col_name) or {}
        val = field.get("value")
        return "" if val is None else (f"{float(val):g}" if field.get("type") == "f32" else str(int(val)))
    hit_key, _label, _typ = info
    raw = (mv.get("_super_hit") or {}).get(hit_key)
    val = _to_intish(raw)
    return "" if val is None else str(int(val))


def parse_super_input(col_name: str, text: str):
    info = super_field_edit_info(col_name)
    if not info:
        raise ValueError("not editable")
    _hit_key, _label, typ = info
    s = str(text).strip()
    if typ == "f32":
        return float(s)
    if typ in {"u8", "u16", "u24", "u32"}:
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    raise ValueError("not editable")


def super_field_addr(mv: dict, col_name: str | None) -> int | None:
    if not is_super_row(mv) or not col_name:
        return None
    hit = mv.get("_super_hit") or {}
    if col_name in SUPER_OWNED_FIELD_INFO:
        field = _owned_field_from_mv(mv, col_name)
        try:
            return int((field or {}).get("addr") or 0) or None
        except Exception:
            return None
    base = int(hit.get("addr") or mv.get("abs") or 0)
    if not base:
        return None
    info = SUPER_DISPATCH_FIELD_INFO.get(col_name)
    if not info:
        return None
    hit_key = info[0]
    offsets = {
        "selector": _SELECTOR_OFF,
        "variant": _VARIANT_OFF,
        "phase": _PHASE_OFF,
        "child_link": _CHILD_LINK_OFF,
    }
    off = offsets.get(hit_key)
    return None if off is None else base + off


def write_super_value(mv: dict, col_name: str, value) -> bool:
    if not is_super_row(mv) or not super_editable(col_name):
        return False
    addr = super_field_addr(mv, col_name)
    if not addr:
        return False

    hit = mv.get("_super_hit") or {}

    if col_name in SUPER_OWNED_FIELD_INFO:
        field = _owned_field_from_mv(mv, col_name) or {}
        typ = str(field.get("type") or SUPER_OWNED_FIELD_INFO[col_name][2])
        ok = False
        if typ == "f32":
            ok = bool(P._write_f32(int(addr), float(value)))
            new_val = float(value)
        elif typ == "u8":
            ival = int(value)
            if not (0 <= ival <= 0xFF):
                raise ValueError("Value must be 0-255")
            ok = bool(P.wbytes(int(addr), bytes([ival]))) if P.wbytes is not None else False
            new_val = int(ival)
        elif typ == "u16":
            ival = int(value)
            if not (0 <= ival <= 0xFFFF):
                raise ValueError("Value must be 0-65535")
            ok = bool(P._write_u16(int(addr), ival))
            new_val = int(ival)
        elif typ == "u24":
            ival = int(value)
            if not (0 <= ival <= 0xFFFFFF):
                raise ValueError("Value must be 0-16777215")
            raw = bytes([(ival >> 16) & 0xFF, (ival >> 8) & 0xFF, ival & 0xFF])
            ok = bool(P.wbytes(int(addr), raw)) if P.wbytes is not None else False
            new_val = int(ival)
        else:
            ival = int(value)
            if not (0 <= ival <= 0xFFFFFFFF):
                raise ValueError("Value must be 0-4294967295")
            ok = bool(P._write_u32(int(addr), ival))
            new_val = int(ival)
        if ok:
            field = dict(field)
            field["value"] = new_val
            fmap = dict(hit.get("owned_field_map") or {})
            fmap[col_name] = field
            hit["owned_field_map"] = fmap
            hit["owned_script_field_summary"] = _owned_field_summary(fmap)
            # Keep payload-field text fresh enough for the inspector/context.
            hit["payload_field_summary"] = hit.get("owned_script_field_summary") or hit.get("payload_field_summary")
            mv[col_name] = new_val
            mv[SUPER_OWNED_FIELD_INFO[col_name][0]] = new_val
            mv["_super_hit"] = hit
        return bool(ok)

    ival = int(value) & 0xFFFFFFFF
    ok = bool(P._write_u32(int(addr), ival))
    if ok:
        hit_key = SUPER_DISPATCH_FIELD_INFO[col_name][0]
        hit[hit_key] = ival
        if hit_key == "child_link":
            hit["child_target"] = _link_target_for_addr(int(hit.get("addr") or mv.get("abs") or 0), ival, hit.get("slot_base"))
        mv[hit_key] = ival
        mv["_super_hit"] = hit
    return ok


def apply_super_tree_value(tree, item: str, mv: dict, col_name: str) -> None:
    if not tree or not item:
        return
    try:
        if col_name in tree["columns"]:
            tree.set(item, col_name, format_super_value(mv, col_name))
        # Keep the compact row text fresh.
        if "link" in tree["columns"]:
            tree.set(item, "link", super_quick_summary(mv))
        if "context" in tree["columns"]:
            tree.set(item, "context", super_context_summary(mv))
    except Exception:
        pass


def super_row_from_hit(hit: dict, row_index: int = 0) -> dict[str, Any]:
    addr = int(hit.get("addr") or 0)
    index = int(hit.get("dispatch_index") or (row_index + 1))
    count = int(hit.get("dispatch_count") or 1)
    sel = int(hit.get("selector") or 0)
    super_name = str(hit.get("super_name") or "").strip()
    if not super_name:
        super_name = str(hit.get("map_move_name") or "").strip()
    name = f"{super_name} Dispatch {index}/{count} sel 0x{sel:02X}" if super_name else f"Action Dispatch {index}/{count} sel 0x{sel:02X}"
    out = {
        "_row_type": "super_dispatch",
        "_scan_index": 870000 + int(row_index),
        "kind": "super dispatch",
        "move_name": name,
        "pretty_name": name,
        "id": None,
        "abs": addr,
        "_dirty_key_addr": addr,
        "_super_hit": dict(hit),
        "link_label": super_quick_summary_from_hit(hit),
        "selector": sel,
        "variant": int(hit.get("variant") or 0),
        "phase": int(hit.get("phase") or 0),
        "child_link": int(hit.get("child_link") or 0),
        "child_target": int(hit.get("child_target") or 0),
        "payload_count": int(hit.get("payload_count") or 0),
        "payload_summary": str(hit.get("payload_summary") or ""),
        "payload_field_summary": str(hit.get("payload_field_summary") or ""),
        "payload_only_summary": str(hit.get("payload_only_summary") or ""),
        "dispatch_confidence": str(hit.get("dispatch_confidence") or ""),
        "dispatch_super_proof": str(hit.get("dispatch_super_proof") or ""),
        "dispatch_owner_proof": str(hit.get("dispatch_owner_proof") or ""),
        "super_name": str(hit.get("super_name") or ""),
        "super_entry_addr": hit.get("super_entry_addr"),
        "owned_script_field_summary": str(hit.get("owned_script_field_summary") or ""),
    }
    for _col in SUPER_OWNED_COLUMNS:
        _field = (hit.get("owned_field_map") or {}).get(_col) if isinstance(hit.get("owned_field_map"), dict) else None
        if isinstance(_field, dict) and _field.get("value") not in (None, "", "?"):
            out[_col] = _field.get("value")
            out[SUPER_OWNED_FIELD_INFO[_col][0]] = _field.get("value")
    # Hard fallback for same-row display.  Some patch paths place the
    # projectilemap-owned damage directly on the dispatch hit; mirror it into
    # the normal Damage column even if owned_field_map was not populated.
    if out.get("damage") in (None, "", "?") and hit.get("damage") not in (None, "", "?"):
        out["damage"] = hit.get("damage")
        out["owned_damage"] = hit.get("damage")
    return out


def super_quick_summary_from_hit(hit: dict) -> str:
    if not hit:
        return ""
    bits = [
        f"sel 0x{int(hit.get('selector') or 0):02X}",
        f"var {int(hit.get('variant') or 0)}",
        f"phase {int(hit.get('phase') or 0)}",
        f"link 0x{int(hit.get('child_link') or 0):08X}",
    ]
    # Put the parent-owned damage in the Link/summary cell too, so the row is
    # useful even when the Damage column is horizontally off-screen.
    try:
        dmg_field = (hit.get("owned_field_map") or {}).get("damage") if isinstance(hit.get("owned_field_map"), dict) else None
    except Exception:
        dmg_field = None
    if isinstance(dmg_field, dict) and dmg_field.get("value") not in (None, "", "?"):
        try:
            dmg_txt = str(int(dmg_field.get("value")))
        except Exception:
            dmg_txt = str(dmg_field.get("value"))
        try:
            addr_txt = f" @0x{int(dmg_field.get('addr') or 0):08X}" if int(dmg_field.get('addr') or 0) else ""
        except Exception:
            addr_txt = ""
        map_names = list(dmg_field.get("map_names") or [])
        name_txt = (" " + " / ".join(str(x) for x in map_names[:2])) if map_names else ""
        bits.append(f"dmg {dmg_txt}{addr_txt}{name_txt}")
    try:
        target = int(hit.get("child_target") or 0)
    except Exception:
        target = 0
    if target:
        bits.append(f"child 0x{target:08X}")
    owned = str(hit.get("owned_script_field_summary") or "").strip()
    if owned:
        bits.append(owned)
    conf = str(hit.get("dispatch_confidence") or "").strip()
    if conf:
        bits.append(conf)
    pc = int(hit.get("payload_count") or 0)
    if pc:
        bits.append(f"owned payloads {pc}")
    return " | ".join(bits)


def super_quick_summary(mv: dict | None) -> str:
    if not is_super_row(mv):
        return ""
    return super_quick_summary_from_hit((mv or {}).get("_super_hit") or {})


def super_context_summary(mv: dict | None) -> str:
    if not is_super_row(mv):
        return ""
    hit = (mv or {}).get("_super_hit") or {}
    target = int(hit.get("child_target") or 0)
    group = str(hit.get("dispatch_group") or "dispatch")
    bits = [f"{group}", f"target 0x{target:08X}", "00/23 caller row"]
    if hit.get("super_name"):
        bits.insert(0, str(hit.get("super_name")))
    try:
        entry_addr = int(hit.get("super_entry_addr") or 0)
    except Exception:
        entry_addr = 0
    if entry_addr:
        bits.append(f"entry 0x{entry_addr:08X}")
    proof = str(hit.get("dispatch_super_proof") or "").strip()
    if proof:
        bits.append("super proof " + proof)
    owner = str(hit.get("dispatch_owner_proof") or "").strip()
    if owner:
        bits.append("owner proof " + owner)
    payload = str(hit.get("payload_summary") or "").strip()
    owned_fields = str(hit.get("owned_script_field_summary") or "").strip()
    if owned_fields:
        bits.append("owned fields " + owned_fields)
    if payload:
        bits.append("owned payload " + payload)
    demoted = str(hit.get("payload_only_summary") or "").strip()
    if demoted:
        bits.append("payload-only " + demoted)
    scout = _scout_summary(hit)
    if scout:
        bits.append("child scout " + scout)
    return " | ".join(bits)
