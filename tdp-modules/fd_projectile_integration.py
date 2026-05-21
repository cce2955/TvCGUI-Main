# fd_projectile_integration.py
#
# Bridge between the Frame Data Workbench and the older standalone
# projectile scanner.  The scanner/window still exists as a legacy module,
# but the workbench can now display and edit projectile records in the same
# table as normal frame-data rows.

from __future__ import annotations

import struct
from typing import Any

import proj_scanner_window as P


PROJECTILE_COLUMNS = (
    "proj_cluster", "proj_fmt", "proj_id", "proj_type",
    "proj_radius", "proj_life", "proj_speed", "proj_accel",
    "proj_kb_y", "proj_hitbox", "proj_arc", "proj_arc2",
    "proj_super_hit_react", "proj_super_life", "proj_super_air_kb_y",
    "proj_super_speed", "proj_super_accel", "proj_super_speed_2",
    "proj_super_accel_b", "proj_super_accel_c",
    "proj_multihit_cap", "proj_super_radius",
)

PROJECTILE_LABELS = {
    "proj_cluster": "Projectile Cluster",
    "proj_fmt": "Projectile Fmt",
    "proj_id": "Projectile ID",
    "proj_type": "Projectile Type",
    "proj_radius": "Proj Radius",
    "proj_life": "Proj Life",
    "proj_speed": "Proj Speed",
    "proj_accel": "Proj Accel",
    "proj_kb_y": "Proj KB Y",
    "proj_hitbox": "Proj Hitbox",
    "proj_arc": "Proj Arc",
    "proj_arc2": "Proj Arc 2",
    "proj_super_hit_react": "Proj HitReact",
    "proj_super_life": "Super Life",
    "proj_super_air_kb_y": "Super Air KB Y",
    "proj_super_speed": "Super Speed",
    "proj_super_accel": "Super Accel",
    "proj_super_speed_2": "Super Speed 2",
    "proj_super_accel_b": "Super Accel B",
    "proj_super_accel_c": "Super Accel C",
    "proj_multihit_cap": "MultiHit Cap",
    "proj_super_radius": "Super Radius",
}

# col_name -> (hit-key, user label, type)
# type: f32, u8, u16, u32, dmg, static
PROJECTILE_FIELD_INFO = {
    "damage": ("dmg", "Projectile Damage", "dmg"),
    "proj_radius": ("radius", "Projectile Radius", "f32"),
    "proj_life": ("lifetime", "Projectile Lifetime", "u16"),
    "proj_speed": ("speed", "Projectile Speed", "f32"),
    "proj_accel": ("accel", "Projectile Accel", "f32"),
    "kb_x": ("kb_x", "Projectile KB X", "f32"),
    "proj_kb_y": ("kb_y", "Projectile KB Y", "f32"),
    "proj_hitbox": ("hitbox", "Projectile Hitbox", "f32"),
    "proj_arc": ("arc", "Projectile Arc", "f32"),
    "proj_arc2": ("arc2", "Projectile Arc 2", "f32"),
    "proj_type": ("type", "Projectile Type", "u8"),
    "proj_id": ("id", "Projectile ID", "u16"),
    "proj_super_hit_react": ("super_hit_react", "Projectile Hit Reaction", "u16"),
    "proj_super_life": ("super_life", "Super Life", "u16"),
    "proj_super_air_kb_y": ("super_air_kb_y", "Super Air KB Y", "f32"),
    "proj_super_speed": ("super_speed", "Super Speed", "f32"),
    "proj_super_accel": ("super_accel", "Super Accel", "f32"),
    "proj_super_speed_2": ("super_speed_2", "Super Speed 2", "f32"),
    "proj_super_accel_b": ("super_accel_b", "Super Accel B", "f32"),
    "proj_super_accel_c": ("super_accel_c", "Super Accel C", "f32"),
    "proj_multihit_cap": ("super_multihit_cap", "MultiHit Cap", "u32"),
    "proj_super_radius": ("super_radius", "Super Radius", "f32"),
}

PROJECTILE_STATIC_COLUMNS = {"proj_cluster", "proj_fmt"}


def projectile_key_for_char(char_name: str | None) -> str | None:
    if not char_name:
        return None
    return P._NAME_TO_KEY.get(str(char_name))




def _move_primary_rank(name: str) -> tuple[int, str]:
    low = str(name or "").strip().lower()
    if not low:
        return (9, "")
    if low in {"unknown", "signature match", "super struct candidate"}:
        return (8, low)
    if "assist" in low:
        return (5, low)
    return (0, low)


def _merge_projectile_aliases(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse multiple names for the exact same physical projectile record.

    Ryu's H Hadouken record, for example, can match both the command name and
    the assist damage alias.  Showing two editable rows for the same address is
    confusing, so keep one row and preserve the alternate label in proj_aliases.
    """
    groups: dict[tuple[int, str, int, str, str], list[dict[str, Any]]] = {}
    ordered_keys: list[tuple[int, str, int, str, str]] = []
    for h in hits:
        key = (
            int(h.get("addr") or 0),
            str(h.get("fmt") or ""),
            int(h.get("dmg_write_addr") or 0),
            str(h.get("key") or ""),
            str(h.get("dmg") or ""),
        )
        if key not in groups:
            groups[key] = []
            ordered_keys.append(key)
        groups[key].append(h)

    merged: list[dict[str, Any]] = []
    for key in ordered_keys:
        rows = groups[key]
        if len(rows) == 1:
            merged.append(rows[0])
            continue
        primary = sorted(rows, key=lambda h: _move_primary_rank(str(h.get("move") or "")))[0]
        out = dict(primary)
        names: list[str] = []
        for row in rows:
            name = str(row.get("move") or "").strip()
            if name and name not in names:
                names.append(name)
        primary_name = str(out.get("move") or "").strip()
        aliases = [n for n in names if n != primary_name]
        if aliases:
            out["proj_aliases"] = ", ".join(aliases)
            # Make the alias visible in the compact core view without creating
            # an extra row that edits the same physical bytes.
            out["move"] = f"{primary_name} / {' / '.join(aliases)}"
        merged.append(out)
    return merged


def _annotate_template_roles(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add a human-readable primary/copy note to repeated projectile templates."""
    template_groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
    for h in hits:
        fmt = str(h.get("fmt") or "")
        if fmt not in {"template", "template2"}:
            continue
        gkey = (
            str(h.get("key") or ""),
            str(h.get("id") or ""),
            str(h.get("type") or ""),
            str(h.get("tier") or ""),
            str(h.get("dmg") or ""),
        )
        template_groups.setdefault(gkey, []).append(h)

    for rows in template_groups.values():
        rows.sort(key=lambda h: int(h.get("addr") or 0xFFFFFFFF))

        # Damage for a visible projectile row may have more than one physical
        # backing address: the template/card bytes themselves, an authoritative
        # param-table copy, and sometimes a copy/alt projectile record.  Keep the
        # peer set on every row so one user edit can update the bytes the game is
        # most likely to read without forcing duplicate rows into the UI.
        peers = []
        for p in rows:
            try:
                base_addr = int(p.get("addr") or 0)
            except Exception:
                base_addr = 0
            try:
                dmg_addr = int(p.get("dmg_write_addr") or 0)
            except Exception:
                dmg_addr = 0
            if base_addr:
                peers.append({
                    "addr": base_addr,
                    "fmt": str(p.get("fmt") or ""),
                    "dmg_write_addr": dmg_addr,
                })

        for idx, h in enumerate(rows):
            if len(rows) > 1:
                role = "primary" if idx == 0 else "copy/alt"
                h["proj_role"] = role
                cluster = str(h.get("cluster") or h.get("fmt") or "").strip()
                if role not in cluster:
                    h["cluster"] = f"{cluster} {role}".strip()
            if peers:
                h["proj_damage_peers"] = [dict(p) for p in peers]
    return hits


def scan_projectiles_for_char(char_name: str | None, progress_cb=None, show_unknowns: bool = False) -> list[dict[str, Any]]:
    """Synchronous scan for one character key using the existing scanner path."""
    key = projectile_key_for_char(char_name)
    if not key:
        return []
    out: list[dict[str, Any]] = []

    def _progress(pct):
        if progress_cb:
            try:
                progress_cb(float(pct))
            except Exception:
                pass

    def _done(hits):
        out.extend(list(hits or []))

    P._run_scan({key}, _progress, _done, show_unknowns=show_unknowns)

    # The old scanner can return duplicated labels for the same physical record
    # when damage signatures overlap. Keep a stable set so the workbench does
    # not show duplicates every refresh. Also drop shifted super_struct_card2
    # shadows when a canonical super_struct row points at the same writable
    # damage address.
    canonical_super_writes = set()
    for h in out:
        fmt = str(h.get("fmt") or "")
        if fmt in {"super_struct", "super_struct_card"}:
            canonical_super_writes.add((
                int(h.get("dmg_write_addr") or 0),
                str(h.get("key") or ""),
                str(h.get("move") or ""),
                int(h.get("dmg") or 0) if str(h.get("dmg") or "").isdigit() else str(h.get("dmg") or ""),
            ))

    seen = set()
    uniq = []
    for h in out:
        fmt = str(h.get("fmt") or "")
        shadow_key = (
            int(h.get("dmg_write_addr") or 0),
            str(h.get("key") or ""),
            str(h.get("move") or ""),
            int(h.get("dmg") or 0) if str(h.get("dmg") or "").isdigit() else str(h.get("dmg") or ""),
        )
        if fmt == "super_struct_card2" and shadow_key in canonical_super_writes:
            continue
        sig = (
            int(h.get("addr") or 0),
            str(h.get("key") or ""),
            str(h.get("move") or ""),
            fmt,
            int(h.get("dmg") or 0) if str(h.get("dmg") or "").isdigit() else str(h.get("dmg") or ""),
        )
        if sig in seen:
            continue
        seen.add(sig)
        uniq.append(h)

    uniq = _merge_projectile_aliases(uniq)
    uniq = _annotate_template_roles(uniq)
    return uniq


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


def _to_floatish(value, default=None):
    if value in (None, "", "?"):
        return default
    if isinstance(value, float):
        return value
    try:
        return float(str(value).strip())
    except Exception:
        return default


def _fmt_float(value) -> str:
    val = _to_floatish(value)
    if val is None:
        return ""
    # Keep the old projectile table precision but trim obvious integer noise in
    # chips/table cells.
    text = f"{float(val):.4f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _fmt_int(value) -> str:
    val = _to_intish(value)
    return "" if val is None else str(int(val))


def _fmt_hex_or_int(value) -> str:
    val = _to_intish(value)
    if val is None:
        return ""
    return f"0x{int(val):04X}"


def format_projectile_value(mv: dict, col_name: str) -> str:
    hit = mv.get("_proj_hit") or {}
    if col_name == "damage":
        return _fmt_int(mv.get("damage", hit.get("dmg")))
    if col_name == "kb_x":
        return _fmt_float(hit.get("kb_x"))
    if col_name == "air_kb":
        return _fmt_float(hit.get("kb_y"))
    if col_name == "proj_cluster":
        return str(hit.get("cluster") or "")
    if col_name == "proj_fmt":
        return str(hit.get("fmt") or "")
    if col_name == "proj_id":
        return _fmt_hex_or_int(hit.get("id"))
    if col_name == "proj_type":
        return _fmt_int(hit.get("type"))
    if col_name == "proj_radius":
        return _fmt_float(hit.get("radius"))
    if col_name == "proj_life":
        return _fmt_int(hit.get("lifetime"))
    if col_name == "proj_speed":
        return _fmt_float(hit.get("speed"))
    if col_name == "proj_accel":
        return _fmt_float(hit.get("accel"))
    if col_name == "proj_kb_y":
        return _fmt_float(hit.get("kb_y"))
    if col_name == "proj_hitbox":
        return _fmt_float(hit.get("hitbox"))
    if col_name == "proj_arc":
        return _fmt_float(hit.get("arc"))
    if col_name == "proj_arc2":
        return _fmt_float(hit.get("arc2"))
    if col_name == "proj_super_hit_react":
        return _fmt_int(hit.get("super_hit_react"))
    if col_name == "proj_super_life":
        return _fmt_int(hit.get("super_life"))
    if col_name == "proj_super_air_kb_y":
        return _fmt_float(hit.get("super_air_kb_y"))
    if col_name == "proj_super_speed":
        return _fmt_float(hit.get("super_speed"))
    if col_name == "proj_super_accel":
        return _fmt_float(hit.get("super_accel"))
    if col_name == "proj_super_speed_2":
        return _fmt_float(hit.get("super_speed_2"))
    if col_name == "proj_super_accel_b":
        return _fmt_float(hit.get("super_accel_b"))
    if col_name == "proj_super_accel_c":
        return _fmt_float(hit.get("super_accel_c"))
    if col_name == "proj_multihit_cap":
        return _fmt_int(hit.get("super_multihit_cap"))
    if col_name == "proj_super_radius":
        return _fmt_float(hit.get("super_radius"))
    return ""



def _summary_value(mv: dict, col_name: str) -> str:
    try:
        return str(format_projectile_value(mv, col_name) or "").strip()
    except Exception:
        return ""


def projectile_quick_summary(mv: dict | None) -> str:
    """Compact display-only summary for the main Frame view.

    Projectile rows have their most useful fields split across far-right raw
    columns. This string keeps ID/type/life/speed/hitbox/super probes readable
    near the Move/Link columns without changing the backing write handlers.
    """
    if not is_projectile_row(mv):
        return ""
    hit = mv.get("_proj_hit") or {}
    bits: list[str] = []

    pid = _summary_value(mv, "proj_id")
    ptype = _summary_value(mv, "proj_type")
    tier = str(hit.get("tier") or "").strip()
    total = str(hit.get("tier_total") or "").strip()
    id_bits = []
    if pid:
        id_bits.append(f"ID {pid}")
    if ptype:
        id_bits.append(f"T{ptype}")
    if tier:
        id_bits.append(f"tier {tier}/{total}" if total else f"tier {tier}")
    if id_bits:
        bits.append(" ".join(id_bits))

    # Normal projectile template fields.
    compact_cols = [
        ("proj_radius", "rad"),
        ("proj_life", "life"),
        ("proj_speed", "spd"),
        ("proj_accel", "acc"),
        ("proj_hitbox", "hb"),
    ]
    for col, label in compact_cols:
        val = _summary_value(mv, col)
        if val:
            bits.append(f"{label} {val}")

    # Super/projectile-probe fields. Put these only when present so ordinary
    # Hadoukens do not become noisy, but Shinkuu-style rows are readable.
    super_cols = [
        ("proj_super_hit_react", "react"),
        ("proj_super_life", "sLife"),
        ("proj_super_speed", "sSpd"),
        ("proj_super_accel", "sAcc"),
        ("proj_super_radius", "sRad"),
        ("proj_multihit_cap", "cap"),
    ]
    super_bits = []
    for col, label in super_cols:
        val = _summary_value(mv, col)
        if val:
            super_bits.append(f"{label} {val}")
    if super_bits:
        bits.append("super " + " ".join(super_bits))

    return " | ".join(bits)

def projectile_row_from_hit(hit: dict, row_index: int = 0) -> dict[str, Any]:
    addr = int(hit.get("addr") or 0)
    move = str(hit.get("move") or "Projectile")
    key = str(hit.get("key") or "?")
    dmg = _to_intish(hit.get("dmg"), 0) or 0
    out = {
        "_row_type": "projectile",
        "_scan_index": 900000 + int(row_index),
        "kind": "projectile",
        "move_name": move,
        "pretty_name": move,
        "id": None,
        "abs": addr,
        "damage": dmg,
        "damage_addr": hit.get("dmg_write_addr") or (addr + 2 if addr else None),
        "_dirty_key_addr": hit.get("dmg_write_addr") or addr,
        "_proj_hit": dict(hit),
        "_proj_char_key": key,
        "link_label": str(hit.get("cluster") or hit.get("fmt") or "projectile"),
    }
    return out


def is_projectile_row(mv: dict | None) -> bool:
    return bool(isinstance(mv, dict) and mv.get("_row_type") == "projectile")


def is_projectile_column(col_name: str | None) -> bool:
    return bool(col_name == "damage" or col_name == "kb_x" or col_name == "air_kb" or (col_name and col_name.startswith("proj_")))


def projectile_editable(col_name: str | None) -> bool:
    if not col_name:
        return False
    return col_name in PROJECTILE_FIELD_INFO


def projectile_group_for_col(col_name: str | None) -> tuple[str | None, tuple[str, ...]]:
    if not col_name or not projectile_editable(col_name):
        return (None, ())
    hit_key, _label, _typ = PROJECTILE_FIELD_INFO[col_name]
    # Some projectile values are intentionally displayed in the legacy frame
    # columns so the combined table is readable in core view.
    display_cols = {
        "dmg": ("damage",),
        "kb_x": ("kb_x",),
        "kb_y": ("air_kb", "proj_kb_y"),
    }.get(hit_key, (col_name,))
    return (f"projectile:{hit_key}", tuple(display_cols))


def projectile_snapshot(mv: dict, group_key: str) -> dict[str, Any]:
    hit_key = group_key.split(":", 1)[1] if ":" in str(group_key) else group_key
    hit = mv.get("_proj_hit") or {}
    return {
        "hit_key": hit_key,
        "value": hit.get(hit_key),
        "damage": mv.get("damage"),
        "damage_addr": mv.get("damage_addr"),
    }


def _fmt_for_mv(mv: dict) -> str:
    return str((mv.get("_proj_hit") or {}).get("fmt") or "")


def projectile_field_addr(mv: dict, col_name: str | None) -> int | None:
    if not is_projectile_row(mv) or not col_name:
        return None
    hit = mv.get("_proj_hit") or {}
    addr = int(hit.get("addr") or mv.get("abs") or 0)
    if not addr:
        return None
    fmt = _fmt_for_mv(mv)
    move_name = str(hit.get("move") or mv.get("move_name") or "")

    info = PROJECTILE_FIELD_INFO.get(col_name)
    if not info:
        return None
    hit_key, _label, _typ = info

    if hit_key == "dmg":
        return int(hit.get("dmg_write_addr") or mv.get("damage_addr") or (addr + P._dmg_write_offset(fmt)))

    if move_name == "Zombie Fall" and hit_key == "spawn_y":
        return addr + P._FRANK_ZOMBIE_FALL_SPAWN_Y_OFF
    if move_name == "Zombie Attack" and hit_key == "speed":
        return addr + P._FRANK_ZOMBIE_ATTACK_SPEED_A
    if move_name == "Zombie Attack" and hit_key == "accel":
        return addr + P._FRANK_ZOMBIE_ATTACK_ACCEL_A
    if move_name == "Zombie Attack" and hit_key == "spawn_x":
        return addr + P._FRANK_ZOMBIE_ATTACK_SPAWN_X

    if hit_key in P._SUPER_FIELD_OFFSETS and fmt in ("super_struct", "super_struct_card", "super_struct_card2"):
        return P._super_ex_base(addr, fmt) + P._SUPER_FIELD_OFFSETS[hit_key][0]
    if hit_key == "hitbox" and fmt in ("super_struct", "super_struct_card", "super_struct_card2"):
        return P._super_ex_base(addr, fmt) + P._SUPER_EX_OFFSETS["ex03c"]
    if hit_key in P._SUPER_EX_OFFSETS and fmt in ("super_struct", "super_struct_card", "super_struct_card2"):
        return P._super_ex_base(addr, fmt) + P._SUPER_EX_OFFSETS[hit_key]

    if move_name.startswith("Zombie Spree "):
        if hit_key == "kb_y":
            if move_name == "Zombie Spree L":
                return addr + P._FRANK_ZOMBIE_SPREE_KBY_L
            if move_name == "Zombie Spree M":
                return addr + P._FRANK_ZOMBIE_SPREE_KBY_M
            return addr + P._FRANK_ZOMBIE_SPREE_KBY_H
        if hit_key == "speed":
            if move_name == "Zombie Spree L":
                return addr + P._FRANK_ZOMBIE_SPREE_ACCEL_L
            if move_name == "Zombie Spree M":
                return addr + P._FRANK_ZOMBIE_SPREE_ACCEL_M
            return addr + P._FRANK_ZOMBIE_SPREE_ACCEL_H
        if hit_key == "arc":
            if move_name == "Zombie Spree L":
                return addr + P._FRANK_ZOMBIE_SPREE_ARC_L
            if move_name == "Zombie Spree M":
                return addr + P._FRANK_ZOMBIE_SPREE_ARC_M
            return addr + P._FRANK_ZOMBIE_SPREE_ARC_H

    if hit_key in P.FIELD_OFFSETS:
        return addr + P.FIELD_OFFSETS[hit_key]
    return None


def parse_projectile_input(col_name: str, text: str):
    info = PROJECTILE_FIELD_INFO.get(col_name)
    if not info:
        raise ValueError("not editable")
    _hit_key, _label, typ = info
    s = str(text).strip()
    if typ == "f32":
        return float(s)
    if typ in ("u8", "u16", "u32", "dmg"):
        return int(s, 16) if s.lower().startswith("0x") else int(s)
    raise ValueError("not editable")


def write_projectile_value(mv: dict, col_name: str, value) -> bool:
    if not is_projectile_row(mv):
        return False
    info = PROJECTILE_FIELD_INFO.get(col_name)
    if not info:
        return False
    hit_key, _label, typ = info
    hit = mv.get("_proj_hit") or {}
    addr = projectile_field_addr(mv, col_name)
    if not addr:
        return False
    fmt = _fmt_for_mv(mv)

    ok = False
    if typ == "f32":
        ok = P._write_f32(int(addr), float(value))
        if ok:
            hit[hit_key] = float(value)
    elif typ == "u8":
        ival = int(value)
        if not (0 <= ival <= 0xFF):
            raise ValueError("Value must be 0-255")
        ok = bool(P.wbytes(int(addr), bytes([ival]))) if P.wbytes is not None else False
        if ok:
            hit[hit_key] = int(ival)
    elif typ == "u16":
        ival = int(value)
        if not (0 <= ival <= 0xFFFF):
            raise ValueError("Value must be 0-65535")
        ok = P._write_u16(int(addr), ival)
        if ok:
            hit[hit_key] = int(ival)
    elif typ == "u32":
        ival = int(value)
        if not (0 <= ival <= 0xFFFFFFFF):
            raise ValueError("Value must be 0-4294967295")
        ok = P._write_u32(int(addr), ival)
        if ok:
            hit[hit_key] = int(ival)
    elif typ == "dmg":
        ival = int(value)
        if not (0 <= ival <= 0xFFFFFFFF):
            raise ValueError("Damage must be 0-4294967295")

        def _add_target(targets, addr, size):
            try:
                addr_i = int(addr or 0)
            except Exception:
                addr_i = 0
            if not addr_i:
                return
            key = (addr_i, str(size))
            if key not in {(a, s) for a, s in targets}:
                targets.append(key)

        resolved = hit.get("dmg_write_addr") or mv.get("damage_addr")
        base_addr = int(hit.get("addr") or mv.get("abs") or 0)
        fallback = base_addr + P._dmg_write_offset(fmt) if base_addr else 0

        # Super cards use a confirmed 16-bit damage slot. Keep that strict.
        if fmt in ("super_struct", "super_struct_card", "super_struct_card2") and resolved is not None:
            if not (0 <= ival <= 0xFFFF):
                raise ValueError("This super-struct damage field must be 0-65535")
            ok = P._write_u16(int(resolved), ival)
        else:
            targets = []

            peers = hit.get("proj_damage_peers") or []
            if not peers:
                peers = [{"addr": base_addr, "fmt": fmt, "dmg_write_addr": resolved}]

            for peer in peers:
                try:
                    peer_base = int(peer.get("addr") or 0)
                except Exception:
                    peer_base = 0
                peer_fmt = str(peer.get("fmt") or fmt)
                try:
                    peer_resolved = int(peer.get("dmg_write_addr") or 0)
                except Exception:
                    peer_resolved = 0
                peer_fallback = peer_base + P._dmg_write_offset(peer_fmt) if peer_base else 0

                # Template/script records expose damage in-place as a 16-bit
                # field, but some characters also have a 32-bit param-table copy
                # that earlier builds preferred. Write both when both exist.
                _add_target(targets, peer_fallback, "u16")
                if peer_resolved and peer_resolved != peer_fallback:
                    _add_target(targets, peer_resolved, "u32")

            wrote = False
            for addr_i, size in targets:
                if size == "u16":
                    if not (0 <= ival <= 0xFFFF):
                        continue
                    wrote = bool(P._write_u16(int(addr_i), ival)) or wrote
                elif size == "u32":
                    wrote = bool(P._write_u32(int(addr_i), ival)) or wrote
            ok = wrote

        if ok:
            hit[hit_key] = int(ival)
            hit["dmg"] = int(ival)
            mv["damage"] = int(ival)

    if ok:
        mv["_proj_hit"] = hit
    return bool(ok)


def projectile_damage_peer_base_addrs(mv: dict) -> set[int]:
    """Physical projectile base addresses that share a damage edit."""
    if not is_projectile_row(mv):
        return set()
    hit = mv.get("_proj_hit") or {}
    out: set[int] = set()
    for peer in hit.get("proj_damage_peers") or []:
        try:
            addr = int(peer.get("addr") or 0)
        except Exception:
            addr = 0
        if addr:
            out.add(addr)
    try:
        addr = int(hit.get("addr") or mv.get("abs") or 0)
    except Exception:
        addr = 0
    if addr:
        out.add(addr)
    return out


def apply_projectile_tree_value(tree, item_id: str, mv: dict, col_name: str) -> None:
    """Update every display column affected by a projectile edit."""
    try:
        if "context" in tree["columns"]:
            tree.set(item_id, "context", projectile_quick_summary(mv))
    except Exception:
        pass
    if col_name == "damage":
        tree.set(item_id, "damage", format_projectile_value(mv, "damage"))
    elif col_name == "kb_x":
        tree.set(item_id, "kb_x", format_projectile_value(mv, "kb_x"))
    elif col_name == "air_kb" or col_name == "proj_kb_y":
        if "air_kb" in tree["columns"]:
            tree.set(item_id, "air_kb", format_projectile_value(mv, "air_kb"))
        if "proj_kb_y" in tree["columns"]:
            tree.set(item_id, "proj_kb_y", format_projectile_value(mv, "proj_kb_y"))
    elif col_name in tree["columns"]:
        tree.set(item_id, col_name, format_projectile_value(mv, col_name))


def column_for_projectile_group(group_key: str) -> str | None:
    if not str(group_key).startswith("projectile:"):
        return None
    hit_key = str(group_key).split(":", 1)[1]
    for col, (hk, _label, _typ) in PROJECTILE_FIELD_INFO.items():
        if hk == hit_key:
            return col
    return None
