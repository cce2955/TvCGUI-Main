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
    "proj_cluster", "proj_fmt", "proj_emit_count", "proj_id", "proj_type",
    "proj_radius", "proj_fx", "proj_life", "proj_spawn_origin", "proj_speed", "proj_accel",
    "proj_kb_y", "proj_hitbox", "proj_arc", "proj_arc2",
    "proj_super_hit_react", "proj_super_life", "proj_super_air_kb_y",
    "proj_super_speed", "proj_super_accel", "proj_super_speed_2",
    "proj_super_accel_b", "proj_super_accel_c",
    "proj_multihit_cap", "proj_super_radius",
    "proj_ps_card_type", "proj_ps_lifetime", "proj_ps_hit_count",
    "proj_ps_mode", "proj_ps_emit_count", "proj_ps_interval",
    "proj_ps_offset_x", "proj_ps_offset_y", "proj_ps_scale",
    "proj_ps_particle_fx", "proj_ps_projectile_id", "proj_ps_spawn_bone",
    "proj_super_lifetime", "proj_super_hit_count", "proj_super_hit_interval",
    "proj_super_particle_fx", "proj_super_spawn_bone", "proj_super_hit_source",
    "proj_super_beam_scale", "proj_super_beam_width", "proj_super_beam_speed",
    "proj_super_beam_force", "proj_super_hit_radius", "proj_super_beam_visual",
    "proj_final_damage", "proj_final_lifetime", "proj_final_particle_fx", "proj_final_spawn_bone",
)

PROJECTILE_LABELS = {
    "proj_cluster": "Projectile Cluster",
    "proj_fmt": "Projectile Fmt",
    "proj_emit_count": "Emitter Count",
    "proj_id": "Projectile ID",
    "proj_type": "Projectile Type",
    "proj_radius": "Proj Radius",
    "proj_fx": "Projectile FX",
    "proj_life": "Proj Life",
    "proj_spawn_origin": "Spawn Origin",
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
    "proj_multihit_cap": "Unknown D8",
    "proj_super_radius": "Hit Radius",
    "proj_ps_card_type": "Card Type",
    "proj_ps_lifetime": "Active Time",
    "proj_ps_hit_count": "Hits",
    "proj_ps_mode": "Mode",
    "proj_ps_emit_count": "Emit Limit",
    "proj_ps_interval": "Interval",
    "proj_ps_offset_x": "Spawn X",
    "proj_ps_offset_y": "Spawn Y",
    "proj_ps_scale": "Scale",
    "proj_ps_particle_fx": "FX",
    "proj_ps_projectile_id": "Proj ID",
    "proj_ps_spawn_bone": "Bone",
    "proj_super_lifetime": "Lifetime",
    "proj_super_hit_count": "Hit Count",
    "proj_super_hit_interval": "Hit Interval",
    "proj_super_particle_fx": "Particle FX",
    "proj_super_spawn_bone": "Spawn Bone",
    "proj_super_hit_source": "Hit Source",
    "proj_super_beam_scale": "Beam Scale",
    "proj_super_beam_width": "Beam Width",
    "proj_super_beam_speed": "Beam Speed",
    "proj_super_beam_force": "Beam Force",
    "proj_super_hit_radius": "Hit Radius",
    "proj_super_beam_visual": "Beam Visual",
    "proj_final_damage": "Final Damage",
    "proj_final_lifetime": "Final Lifetime",
    "proj_final_particle_fx": "Final FX",
    "proj_final_spawn_bone": "Final Bone",
}

# col_name -> (hit-key, user label, type)
# type: f32, u8, u16, u32, dmg, static
PROJECTILE_FIELD_INFO = {
    "damage": ("dmg", "Projectile Damage", "dmg"),
    "proj_radius": ("radius", "Projectile Radius", "f32"),
    "proj_fx": ("fx", "Projectile FX", "u32"),
    "proj_life": ("lifetime", "Projectile Lifetime", "u16"),
    "proj_spawn_origin": ("spawn_origin", "Spawn Origin", "u8"),
    "proj_speed": ("speed", "Projectile Speed", "f32"),
    "proj_accel": ("accel", "Projectile Accel", "f32"),
    "kb_x": ("kb_x", "Projectile KB X", "f32"),
    "air_kb": ("kb_y", "Projectile KB Y", "f32"),
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
    "proj_multihit_cap": ("super_multihit_cap", "Unknown D8", "u32"),
    "proj_super_radius": ("super_radius", "Hit Radius", "f32"),
    "proj_ps_card_type": ("ps_card_type", "Card Type", "static"),
    "proj_ps_lifetime": ("ps_lifetime", "Active Time", "u16"),
    "proj_ps_hit_count": ("ps_hit_count", "Hits", "u16"),
    "proj_ps_mode": ("ps_mode", "Mode", "u16"),
    "proj_ps_emit_count": ("ps_emit_count", "Emit Limit", "u16"),
    "proj_ps_interval": ("ps_interval", "Interval", "u16"),
    "proj_ps_offset_x": ("ps_offset_x", "Spawn X", "f32"),
    "proj_ps_offset_y": ("ps_offset_y", "Spawn Y", "f32"),
    "proj_ps_scale": ("ps_scale", "Scale", "f32"),
    "proj_ps_particle_fx": ("ps_particle_fx", "FX", "u16"),
    "proj_ps_projectile_id": ("ps_projectile_id", "Proj ID", "u16"),
    "proj_ps_spawn_bone": ("ps_spawn_bone", "Bone", "u16"),
    "proj_super_lifetime": ("super_lifetime", "Lifetime", "u32"),
    "proj_super_hit_count": ("super_hit_count", "Hit Count", "u32"),
    "proj_super_hit_interval": ("super_hit_interval", "Hit Interval", "u32"),
    "proj_super_particle_fx": ("super_particle_fx", "Particle FX", "u32"),
    "proj_super_spawn_bone": ("super_spawn_bone", "Spawn Bone", "u32"),
    "proj_super_hit_source": ("super_hit_source", "Hit Source", "u32"),
    "proj_super_beam_scale": ("super_air_kb_y", "Beam Scale", "f32"),
    "proj_super_beam_width": ("super_beam_width", "Beam Width", "f32"),
    "proj_super_beam_speed": ("super_speed", "Beam Speed", "f32"),
    "proj_super_beam_force": ("super_accel", "Beam Force", "f32"),
    "proj_super_hit_radius": ("super_radius", "Hit Radius", "f32"),
    "proj_super_beam_visual": ("super_beam_visual", "Beam Visual", "u32"),
    "proj_final_damage": ("super_final_damage", "Final Damage", "u32"),
    "proj_final_lifetime": ("super_final_lifetime", "Final Lifetime", "u32"),
    "proj_final_particle_fx": ("super_final_particle_fx", "Final Particle FX", "u32"),
    "proj_final_spawn_bone": ("super_final_spawn_bone", "Final Spawn Bone", "u32"),
}

PROJECTILE_STATIC_COLUMNS = {"proj_cluster", "proj_fmt", "proj_emit_count"}


def projectile_key_for_char(char_name: str | None) -> str | None:
    if not char_name:
        return None
    return P._NAME_TO_KEY.get(str(char_name))





# ---------------------------------------------------------------------------
# Projectile-super emitter grouping
# ---------------------------------------------------------------------------
# Compact 00/23 cards are individual bullets/hit cards.  For barrage supers
# like Finishing Shower, users usually want the emitter/barrage first, then
# the per-bullet cards later.  Emitter rows are synthetic UI rows: edits are
# bulk-applied to the child projectile cards they summarize.

_EMITTER_FMT = "projectile_emitter"
_MORRIGAN_FS_MISSILE_FMT = "morrigan_fs_missile"


def _is_morrigan_finishing_shower_missile_hit(hit: dict | None) -> bool:
    if not isinstance(hit, dict):
        return False
    if str(hit.get("key") or "").upper() != "MORRIGAN":
        return False
    move = str(hit.get("move") or "").lower()
    if "finishing shower" not in move or "missile" not in move:
        return False
    return str(hit.get("fmt") or "") == _MORRIGAN_FS_MISSILE_FMT or _to_intish(hit.get("dmg")) == 800


def _is_projectile_emitter_hit(hit: dict | None) -> bool:
    return bool(isinstance(hit, dict) and str(hit.get("fmt") or "") == _EMITTER_FMT)


def _projectile_emitter_name_for_hit(hit: dict | None) -> str | None:
    if not isinstance(hit, dict):
        return None
    move = str(hit.get("move") or "").strip()
    low = move.lower()
    key = str(hit.get("key") or "").upper()
    if not move:
        return None

    if "finishing shower" in low:
        return "Finishing Shower Emitter"
    if "machine gun sweep" in low:
        return "Machine Gun Sweep Emitter"
    if "brutal ax" in low or "brutal axe" in low:
        return "Brutal Ax Emitter"
    if "voltekka" in low:
        return "Voltekka Emitter"
    if "disco ball" in low:
        return "Disco Ball Emitter"
    if key == "CASSHAN" and "projectile super" in low:
        return "Casshan Projectile Super Emitter"
    if key == "TEKKAMAN" and "projectile card" in low:
        return "Voltekka Emitter"
    return None




def _is_morrigan_finishing_shower_real_template(hit: dict | None) -> bool:
    if not isinstance(hit, dict):
        return False
    if str(hit.get("key") or "").upper() != "MORRIGAN":
        return False
    if "finishing shower" not in str(hit.get("move") or "").lower():
        return False
    fmt = str(hit.get("fmt") or "")
    if fmt == _MORRIGAN_FS_MISSILE_FMT:
        return True
    if fmt not in {"template", "template2"}:
        return False
    try:
        return int(hit.get("dmg") or 0) == 800
    except Exception:
        return False


def _is_morrigan_finishing_shower_false_0023(hit: dict | None) -> bool:
    """Old scanner false-positive guard.

    The user confirmed the live per-missile damage is at 0x908E2906, which is
    a normal projectile-template damage word at base+0x02.  The earlier
    0x908E2B72 00/23 rows are bytes embedded later in that same local region;
    direct pokes there did not affect Finishing Shower.  Once the real 800-dmg
    template row is present, suppress those bogus compact-card rows so the
    emitter does not bulk-write dead/noisy addresses.
    """
    if not isinstance(hit, dict):
        return False
    if str(hit.get("key") or "").upper() != "MORRIGAN":
        return False
    if "finishing shower" not in str(hit.get("move") or "").lower():
        return False
    if str(hit.get("fmt") or "") not in getattr(P, "PROJECTILE_SUPER_FMTS", set()):
        return False
    try:
        dmg = int(hit.get("dmg") or 0)
    except Exception:
        return False
    return dmg in {48, 49, 50, 51, 52, 53, 54, 55, 56, 96, 97, 112}


def _prefer_real_morrigan_finishing_shower_templates(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop bogus 00/23 Finishing Shower rows once real 800-dmg templates exist."""
    hits = list(hits or [])
    if not any(_is_morrigan_finishing_shower_real_template(h) for h in hits):
        return hits
    return [h for h in hits if not _is_morrigan_finishing_shower_false_0023(h)]

def _values_for_hits(hits: list[dict[str, Any]], key: str) -> list[Any]:
    vals = []
    for h in hits or []:
        v = h.get(key)
        if v in (None, "", "?"):
            continue
        vals.append(v)
    return vals


def _num_or_none(v):
    try:
        if isinstance(v, str) and not v.strip():
            return None
        return float(v)
    except Exception:
        return None


def _common_or_first(vals: list[Any]):
    if not vals:
        return None
    norm = [str(v) for v in vals]
    if len(set(norm)) == 1:
        return vals[0]
    # Mixed bullet tables still need a writable initial value.  Use the first
    # value and expose the range/count in the quick summary.
    return vals[0]


def _range_summary(vals: list[Any]) -> str:
    if not vals:
        return ""
    nums = [_num_or_none(v) for v in vals]
    if all(n is not None for n in nums):
        ns = [float(n) for n in nums]
        lo, hi = min(ns), max(ns)
        if abs(lo - hi) < 1e-9:
            if abs(lo - int(lo)) < 1e-9:
                return str(int(lo))
            return f"{lo:g}"
        if abs(lo - int(lo)) < 1e-9 and abs(hi - int(hi)) < 1e-9:
            return f"{int(lo)}..{int(hi)}"
        return f"{lo:g}..{hi:g}"
    uniq = []
    for v in vals:
        sv = str(v)
        if sv not in uniq:
            uniq.append(sv)
    return uniq[0] if len(uniq) == 1 else f"{uniq[0]}..{uniq[-1]}"


def _aggregate_emitter_hit(name: str, peers: list[dict[str, Any]], row_index: int = 0) -> dict[str, Any]:
    peers = list(peers or [])
    if name == "Finishing Shower Emitter" and any(_is_morrigan_finishing_shower_real_template(h) for h in peers):
        peers = [h for h in peers if _is_morrigan_finishing_shower_real_template(h)]
    peers.sort(key=lambda h: int(h.get("addr") or 0xFFFFFFFF))
    first = peers[0] if peers else {}
    addr = int(first.get("addr") or 0)
    out: dict[str, Any] = {
        "addr": addr,
        "fmt": _EMITTER_FMT,
        "move": name,
        "key": first.get("key") or "?",
        "cluster": f"emitter group: {len(peers)} card(s)",
        "proj_role": "emitter",
        "_emitter_peer_hits": [dict(h) for h in peers],
        "_emitter_row_index": int(row_index),
    }

    # Aggregate display values.  Most edits bulk-apply to peers, so keep a sane
    # current value even when the child cards are mixed.
    map_keys = {
        "dmg": ("dmg",),
        "kb_x": ("kb_x",),
        "kb_y": ("kb_y",),
        "ps_lifetime": ("ps_lifetime",),
        "ps_hit_count": ("ps_hit_count",),
        "ps_mode": ("ps_mode",),
        "ps_emit_count": ("ps_emit_count",),
        "ps_interval": ("ps_interval",),
        "ps_offset_x": ("ps_offset_x",),
        "ps_offset_y": ("ps_offset_y",),
        "ps_scale": ("ps_scale",),
        "ps_particle_fx": ("ps_particle_fx",),
        "ps_projectile_id": ("ps_projectile_id",),
        "ps_spawn_bone": ("ps_spawn_bone",),
        # For emitter rows, show compact-card offset fields under the friendly
        # Speed/Accel/Hitbox chips when no normal template speed exists.
        "spawn_origin": ("spawn_origin", "ps_spawn_bone"),
        "speed": ("speed", "ps_offset_x"),
        "accel": ("accel", "ps_offset_y"),
        "hitbox": ("hitbox", "ps_scale"),
        "lifetime": ("lifetime", "ps_lifetime"),
        "radius": ("radius", "ps_scale"),
    }
    for out_key, peer_keys in map_keys.items():
        vals = []
        for peer_key in peer_keys:
            vals = _values_for_hits(peers, peer_key)
            if vals:
                break
        out[out_key] = _common_or_first(vals)
        out[f"_{out_key}_range"] = _range_summary(vals)

    out["dmg_write_addr"] = first.get("dmg_write_addr") or first.get("damage_addr") or 0
    out["emitter_count"] = len(peers)
    return out


def with_projectile_emitters(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return hits plus synthetic emitter/bulk-edit rows.

    The emitter rows are inserted in the same list as physical projectile cards
    so the existing Treeview/editor pipeline can handle them without a separate
    window.  Physical card rows are kept; this adds control, it does not remove
    low-level editing.
    """
    hits = list(hits or [])
    groups: dict[str, list[dict[str, Any]]] = {}
    order: list[str] = []
    for h in hits:
        name = _projectile_emitter_name_for_hit(h)
        if not name:
            continue
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(h)

    emitters = []
    for i, name in enumerate(order):
        peers = groups.get(name) or []
        if len(peers) < 2 and "Emitter" not in name:
            continue
        emitters.append(_aggregate_emitter_hit(name, peers, i))

    return emitters + hits

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
        if fmt in {"super_struct", "super_struct_card", "super_beam_card"}:
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
    uniq = _prefer_real_morrigan_finishing_shower_templates(uniq)
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
    if _is_projectile_emitter_hit(hit):
        if col_name == "proj_emit_count":
            return _fmt_int(hit.get("emitter_count"))
        if col_name == "damage":
            return str(hit.get("_dmg_range") or _fmt_int(mv.get("damage", hit.get("dmg"))) or "")
        emitter_range_cols = {
            "proj_ps_lifetime": "_ps_lifetime_range",
            "proj_ps_hit_count": "_ps_hit_count_range",
            "proj_ps_emit_count": "_ps_emit_count_range",
            "proj_ps_interval": "_ps_interval_range",
            "proj_ps_offset_x": "_ps_offset_x_range",
            "proj_ps_offset_y": "_ps_offset_y_range",
            "proj_ps_scale": "_ps_scale_range",
            "proj_ps_particle_fx": "_ps_particle_fx_range",
            "proj_ps_projectile_id": "_ps_projectile_id_range",
            "proj_ps_spawn_bone": "_ps_spawn_bone_range",
            "proj_life": "_lifetime_range",
            "proj_spawn_origin": "_spawn_origin_range",
            "proj_speed": "_speed_range",
            "proj_accel": "_accel_range",
            "proj_hitbox": "_hitbox_range",
            "proj_radius": "_radius_range",
            "proj_fx": "_fx_range",
            "kb_x": "_kb_x_range",
            "air_kb": "_kb_y_range",
            "proj_kb_y": "_kb_y_range",
        }
        if col_name in emitter_range_cols:
            return str(hit.get(emitter_range_cols[col_name]) or "")
        if col_name == "proj_fmt":
            return "emitter"
        if col_name == "proj_cluster":
            return str(hit.get("cluster") or "")
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
    if col_name == "proj_fx":
        return _fmt_int(hit.get("fx"))
    if col_name == "proj_life":
        return _fmt_int(hit.get("lifetime"))
    if col_name == "proj_spawn_origin":
        return _fmt_int(hit.get("spawn_origin"))
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
    if col_name == "proj_ps_card_type":
        v = _to_intish(hit.get("ps_card_type"))
        return "" if v is None else f"0x{int(v):04X}"
    if col_name == "proj_ps_lifetime":
        return _fmt_int(hit.get("ps_lifetime"))
    if col_name == "proj_ps_hit_count":
        return _fmt_int(hit.get("ps_hit_count"))
    if col_name == "proj_ps_mode":
        return _fmt_int(hit.get("ps_mode"))
    if col_name == "proj_ps_emit_count":
        return _fmt_int(hit.get("ps_emit_count"))
    if col_name == "proj_ps_interval":
        return _fmt_int(hit.get("ps_interval"))
    if col_name == "proj_ps_offset_x":
        return _fmt_float(hit.get("ps_offset_x"))
    if col_name == "proj_ps_offset_y":
        return _fmt_float(hit.get("ps_offset_y"))
    if col_name == "proj_ps_scale":
        return _fmt_float(hit.get("ps_scale"))
    if col_name == "proj_ps_particle_fx":
        return _fmt_int(hit.get("ps_particle_fx"))
    if col_name == "proj_ps_projectile_id":
        return _fmt_int(hit.get("ps_projectile_id"))
    if col_name == "proj_ps_spawn_bone":
        return _fmt_int(hit.get("ps_spawn_bone"))
    if col_name == "proj_super_lifetime":
        return _fmt_int(hit.get("super_lifetime"))
    if col_name == "proj_super_hit_count":
        return _fmt_int(hit.get("super_hit_count"))
    if col_name == "proj_super_hit_interval":
        return _fmt_int(hit.get("super_hit_interval"))
    if col_name == "proj_super_particle_fx":
        return _fmt_int(hit.get("super_particle_fx"))
    if col_name == "proj_super_spawn_bone":
        return _fmt_int(hit.get("super_spawn_bone"))
    if col_name == "proj_super_hit_source":
        return _fmt_int(hit.get("super_hit_source"))
    if col_name == "proj_super_beam_scale":
        return _fmt_float(hit.get("super_air_kb_y"))
    if col_name == "proj_super_beam_width":
        return _fmt_float(hit.get("super_beam_width"))
    if col_name == "proj_super_beam_speed":
        return _fmt_float(hit.get("super_speed"))
    if col_name == "proj_super_beam_force":
        return _fmt_float(hit.get("super_accel"))
    if col_name == "proj_super_hit_radius":
        return _fmt_float(hit.get("super_radius"))
    if col_name == "proj_super_beam_visual":
        return _fmt_int(hit.get("super_beam_visual"))
    if col_name == "proj_final_damage":
        return _fmt_int(hit.get("super_final_damage"))
    if col_name == "proj_final_lifetime":
        return _fmt_int(hit.get("super_final_lifetime"))
    if col_name == "proj_final_particle_fx":
        return _fmt_int(hit.get("super_final_particle_fx"))
    if col_name == "proj_final_spawn_bone":
        return _fmt_int(hit.get("super_final_spawn_bone"))
    return ""



def projectile_edit_initial_value(mv: dict, col_name: str) -> str:
    """Parse-safe initial value for projectile edit dialogs.

    Emitter rows deliberately display ranges such as 48..112 when child cards
    differ.  That is useful in the grid, but it is not a valid value to write.
    Use the aggregate's first/current scalar as the dialog default.
    """
    hit = (mv or {}).get("_proj_hit") or {}
    info = PROJECTILE_FIELD_INFO.get(col_name)
    if not info:
        return format_projectile_value(mv, col_name)
    hit_key, _label, typ = info

    if col_name == "damage":
        raw = (mv or {}).get("damage", hit.get("dmg"))
    else:
        raw = hit.get(hit_key)

    if raw in (None, "", "?"):
        return format_projectile_value(mv, col_name)
    try:
        if typ == "f32":
            return _fmt_float(raw)
        if typ in ("u8", "u16", "u32", "dmg"):
            return _fmt_int(raw)
    except Exception:
        pass
    return str(raw)



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

    if _is_projectile_emitter_hit(hit):
        bits.append(f"emitter {int(hit.get('emitter_count') or 0)} cards")
        for col, label in [
            ("damage", "dmg"),
            ("kb_x", "kx"),
            ("air_kb", "ky"),
            ("proj_ps_lifetime", "life"),
            ("proj_ps_hit_count", "hits"),
            ("proj_ps_interval", "int"),
            ("proj_ps_scale", "scale"),
            ("proj_ps_particle_fx", "fx"),
            ("proj_speed", "spd"),
            ("proj_spawn_origin", "org"),
            ("proj_accel", "acc"),
        ]:
            val = _summary_value(mv, col)
            if val:
                bits.append(f"{label} {val}")
        return " | ".join(bits)

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
        ("kb_x", "kx"),
        ("air_kb", "ky"),
        ("proj_radius", "rad"),
        ("proj_fx", "fx"),
        ("proj_life", "life"),
        ("proj_spawn_origin", "org"),
        ("proj_speed", "spd"),
        ("proj_accel", "acc"),
        ("proj_hitbox", "hb"),
    ]
    for col, label in compact_cols:
        val = _summary_value(mv, col)
        if val:
            bits.append(f"{label} {val}")

    # Compact 00/23 / 01/23 projectile-super card fields.
    ps_cols = [
        ("proj_ps_lifetime", "life"),
        ("proj_ps_hit_count", "hits"),
        ("proj_ps_emit_count", "emit"),
        ("proj_ps_interval", "int"),
        ("proj_ps_particle_fx", "fx"),
        ("proj_ps_projectile_id", "pid"),
        ("proj_ps_spawn_bone", "bone"),
    ]
    ps_bits = []
    for col, label in ps_cols:
        val = _summary_value(mv, col)
        if val:
            ps_bits.append(f"{label} {val}")
    if ps_bits:
        bits.append("proj-super " + " ".join(ps_bits))

    # Super/projectile-probe fields. Put these only when present so ordinary
    # Hadoukens do not become noisy, but Shinkuu-style rows are readable.
    super_cols = [
        ("proj_super_lifetime", "life"),
        ("proj_super_hit_count", "hits"),
        ("proj_super_hit_interval", "int"),
        ("proj_super_particle_fx", "fx"),
        ("proj_super_spawn_bone", "bone"),
        ("proj_super_beam_speed", "spd"),
        ("proj_super_beam_force", "force"),
        ("proj_super_hit_radius", "rad"),
        ("proj_final_damage", "finalDmg"),
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
    is_emitter = _is_projectile_emitter_hit(hit)
    out = {
        "_row_type": "projectile",
        "_scan_index": (880000 if is_emitter else 900000) + int(row_index),
        "kind": "projectile emitter" if is_emitter else "projectile",
        "move_name": move,
        "pretty_name": move,
        "id": None,
        "abs": addr,
        "damage": dmg,
        "damage_addr": hit.get("dmg_write_addr") or (addr + 2 if addr else None),
        "_dirty_key_addr": (f"emitter:{move}" if is_emitter else (hit.get("dmg_write_addr") or addr)),
        "_proj_hit": dict(hit),
        "_proj_char_key": key,
        "link_label": str(hit.get("cluster") or hit.get("fmt") or "projectile"),
    }
    return out


def is_projectile_row(mv: dict | None) -> bool:
    return bool(isinstance(mv, dict) and mv.get("_row_type") == "projectile")


def is_projectile_emitter_row(mv: dict | None) -> bool:
    return bool(is_projectile_row(mv) and _is_projectile_emitter_hit((mv or {}).get("_proj_hit") or {}))


def is_projectile_super_card(mv: dict | None) -> bool:
    """True for compact 00/23 or 01/23 projectile-super cards.

    These are not normal projectile-template rows and not Shinkuu-style
    super-beam cards. They need their own quick strip/sidebar priority.
    """
    if not is_projectile_row(mv):
        return False
    try:
        return _fmt_for_mv(mv) in getattr(P, "PROJECTILE_SUPER_FMTS", set())
    except Exception:
        return False


def is_super_beam_card(mv: dict | None) -> bool:
    if not is_projectile_row(mv):
        return False
    try:
        return _fmt_for_mv(mv) == "super_beam_card"
    except Exception:
        return False


def is_projectile_column(col_name: str | None) -> bool:
    return bool(col_name == "damage" or col_name == "kb_x" or col_name == "air_kb" or (col_name and col_name.startswith("proj_")))


def projectile_editable(col_name: str | None) -> bool:
    if not col_name:
        return False
    info = PROJECTILE_FIELD_INFO.get(col_name)
    return bool(info and info[2] != "static")


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
    if _is_projectile_emitter_hit(hit):
        return None
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

    if _is_morrigan_finishing_shower_missile_hit(hit):
        # User-confirmed live Morrigan Finishing Shower missile block.
        # Record base is the 0x00000103 word; damage is the u16 at +0x06.
        # Generic template offsets were four bytes off and mislabeled lifetime.
        fs_offsets = {
            "kb_x": 0x28,
            "kb_y": 0x2C,
            "radius": 0x30,
            "fx": 0x34,
            "spawn_origin": 0x5F,
            "speed": 0x90,
            # User-confirmed second radius/hitbox-like field.  Keep it on
            # proj_hitbox instead of pretending it is another emitter control.
            "hitbox": 0xD8,
        }
        off = fs_offsets.get(hit_key)
        if off is not None:
            return addr + off
        # Do not fall through to generic template offsets for this custom block;
        # that was how +0x5B got mislabeled as lifetime instead of origin.
        return None

    if move_name == "Zombie Fall" and hit_key == "spawn_y":
        return addr + P._FRANK_ZOMBIE_FALL_SPAWN_Y_OFF
    if move_name == "Zombie Attack" and hit_key == "speed":
        return addr + P._FRANK_ZOMBIE_ATTACK_SPEED_A
    if move_name == "Zombie Attack" and hit_key == "accel":
        return addr + P._FRANK_ZOMBIE_ATTACK_ACCEL_A
    if move_name == "Zombie Attack" and hit_key == "spawn_x":
        return addr + P._FRANK_ZOMBIE_ATTACK_SPAWN_X

    if hit_key in getattr(P, "_PROJECTILE_SUPER_FIELD_OFFSETS", {}) and fmt in getattr(P, "PROJECTILE_SUPER_FMTS", set()):
        return addr + P._PROJECTILE_SUPER_FIELD_OFFSETS[hit_key][0]

    if hit_key in P._SUPER_FIELD_OFFSETS and fmt in ("super_struct", "super_struct_card", "super_struct_card2", "super_beam_card"):
        return P._super_ex_base(addr, fmt) + P._SUPER_FIELD_OFFSETS[hit_key][0]
    if hit_key == "hitbox" and fmt in ("super_struct", "super_struct_card", "super_struct_card2", "super_beam_card"):
        return P._super_ex_base(addr, fmt) + P._SUPER_EX_OFFSETS["ex03c"]
    if hit_key in P._SUPER_EX_OFFSETS and fmt in ("super_struct", "super_struct_card", "super_struct_card2", "super_beam_card"):
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



def _write_projectile_emitter_value(mv: dict, col_name: str, value) -> bool:
    """Bulk-edit all physical projectile cards summarized by an emitter row."""
    hit = mv.get("_proj_hit") or {}
    peers = list(hit.get("_emitter_peer_hits") or [])
    if not peers:
        return False

    if col_name in {"proj_emit_count", "proj_fmt", "proj_cluster", "proj_ps_card_type"}:
        return False

    emitter_col_alias = {
        "proj_speed": "proj_ps_offset_x",
        "proj_accel": "proj_ps_offset_y",
        "proj_hitbox": "proj_ps_scale",
        "proj_radius": "proj_ps_scale",
        "proj_life": "proj_ps_lifetime",
        "proj_spawn_origin": "proj_ps_spawn_bone",
    }

    wrote = False
    new_peers = []
    for peer in peers:
        temp = projectile_row_from_hit(dict(peer), 0)
        write_col = col_name
        # Compact 00/23 cards do not have normal-template Speed/Accel/Hitbox
        # fields, but those are the words users expect on an emitter row.
        if _fmt_for_mv(temp) in getattr(P, "PROJECTILE_SUPER_FMTS", set()):
            write_col = emitter_col_alias.get(col_name, col_name)
        # Do not let a nested synthetic row recurse; peers are physical cards.
        if _is_projectile_emitter_hit(temp.get("_proj_hit") or {}):
            continue
        try:
            if projectile_editable(write_col) and write_projectile_value(temp, write_col, value):
                wrote = True
                new_peers.append(dict(temp.get("_proj_hit") or peer))
            else:
                new_peers.append(dict(peer))
        except Exception:
            new_peers.append(dict(peer))

    if wrote:
        name = str(hit.get("move") or mv.get("move_name") or "Projectile Emitter")
        refreshed = _aggregate_emitter_hit(name, new_peers, int(hit.get("_emitter_row_index") or 0))
        hit.clear()
        hit.update(refreshed)
        mv["_proj_hit"] = hit
        mv["damage"] = _to_intish(hit.get("dmg"), mv.get("damage"))
    return bool(wrote)

def write_projectile_value(mv: dict, col_name: str, value) -> bool:
    if not is_projectile_row(mv):
        return False
    info = PROJECTILE_FIELD_INFO.get(col_name)
    if not info:
        return False
    hit_key, _label, typ = info
    hit = mv.get("_proj_hit") or {}

    if _is_projectile_emitter_hit(hit):
        return _write_projectile_emitter_value(mv, col_name, value)

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

        # Super cards use confirmed dedicated damage slots. Keep those strict.
        if fmt == "super_beam_card" and resolved is not None:
            ok = P._write_u32(int(resolved), ival)
        elif fmt in getattr(P, "PROJECTILE_SUPER_FMTS", set()) and resolved is not None:
            if not (0 <= ival <= 0xFFFF):
                raise ValueError("This projectile-super damage field must be 0-65535")
            ok = P._write_u16(int(resolved), ival)
        elif fmt in ("super_struct", "super_struct_card", "super_struct_card2") and resolved is not None:
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
    if _is_projectile_emitter_hit(hit):
        for peer in hit.get("_emitter_peer_hits") or []:
            try:
                a = int(peer.get("addr") or 0)
            except Exception:
                a = 0
            if a:
                out.add(a)
        return out
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
