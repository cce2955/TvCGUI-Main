# fd_tree.py
#
# This file owns tree column definitions + row population wiring.
# The current layout keeps the dense grid for power editing, but adds a
# cleaner workbench shell, optional advanced filters, core/all column views,
# and a right-side selected-move inspector so users do not have to parse a
# giant spreadsheet for normal edits.

from __future__ import annotations

import re
import time
import tkinter as tk
from tkinter import ttk

from . import utils as U
from .patterns import (
    find_superbg_addr,
    find_speed_mod_addr,
    find_attack_property_addr,
    find_hit_spark_addr,
    find_limb_stretch_packet,
    find_post_animation_link_addr,
    fmt_attack_property,
    find_hit_result_flags_addr,
)
from .widgets import Tooltip, get_field_help
from .move_families import annotate_move_families
from . import projectile_integration as FPI
from . import super_integration as FSI


FD_COLUMNS = (
    "move", "kind", "hits", "link", "context",
    "damage",
    "meter",
    "startup", "active", "active2",
    "hitstun", "invuln", "blockstun", "hitstop",
    "adv_block_derived", "adv_block_observed",
    "hit_spark", "stretch_part", "stretch_len", "stretch_width", "stretch_height", "stretch_time", "post_link",
    "kb_type", "launch_profile", "kb_unknown", "kb_x", "air_kb",
    "speed_mod", "attack_property", "hit_reaction", "hit_result_flags",
    "superbg",
    # Projectile columns are hidden from the simplest frame-data view but live
    # in the same Treeview so users no longer need a separate projectile table.
    *FPI.PROJECTILE_COLUMNS,
    *FSI.SUPER_DISPATCH_COLUMNS,
    "abs",
)

FD_CORE_COLUMNS = (
    "move", "hits", "link",
    "damage", "meter",
    "startup", "active",
    "hitstun", "invuln", "blockstun", "hitstop",
    "adv_block_derived", "adv_block_observed",
    "hit_spark", "stretch_part", "stretch_len", "stretch_width", "stretch_height", "stretch_time", "post_link",
    "kb_type", "launch_profile", "kb_unknown", "kb_x", "air_kb",
    "speed_mod", "attack_property", "hit_reaction", "hit_result_flags",
    # Keep only the compact projectile basics in the frame view. Dedicated
    # projectile/super views move the heavy projectile columns up front so the
    # user does not have to horizontal-scroll across the raw scout table.
    "proj_speed", "proj_radius", "proj_life", "proj_fmt",
    "dispatch_selector", "dispatch_phase", "dispatch_child_link",
    "abs",
)

FD_PROJECTILE_COLUMNS_FOCUSED = (
    "move", "link", "proj_emit_count",
    "damage", "kb_x", "air_kb",
    "proj_fmt", "proj_id", "proj_type",
    "proj_radius", "proj_fx", "proj_life", "proj_spawn_origin", "proj_speed", "proj_accel",
    "proj_kb_y", "proj_hitbox", "proj_arc", "proj_arc2",
    "proj_ps_lifetime", "proj_ps_hit_count", "proj_ps_emit_count",
    "proj_ps_interval", "proj_ps_particle_fx", "proj_ps_projectile_id", "proj_ps_spawn_bone",
    "proj_super_lifetime", "proj_super_hit_count", "proj_super_hit_interval",
    "proj_super_particle_fx", "proj_super_spawn_bone",
    "proj_super_beam_speed", "proj_super_hit_radius",
    "abs",
)

FD_SUPER_COLUMNS_FOCUSED = (
    "move", "kind", "hits", "link",
    "dispatch_group", "dispatch_confidence", "dispatch_super_proof", "dispatch_owner_proof",
    "dispatch_selector", "dispatch_variant", "dispatch_phase", "dispatch_child_link", "dispatch_child_target",
    "proj_emit_count",
    "damage",
    "proj_ps_card_type", "proj_ps_lifetime", "proj_ps_hit_count",
    "proj_ps_mode", "proj_ps_emit_count", "proj_ps_interval",
    "proj_ps_offset_x", "proj_ps_offset_y", "proj_ps_scale",
    "proj_ps_particle_fx", "proj_ps_projectile_id", "proj_ps_spawn_bone",
    "proj_super_lifetime", "proj_super_hit_count", "proj_super_hit_interval",
    "proj_super_particle_fx", "proj_super_spawn_bone", "proj_super_hit_source",
    "proj_super_beam_scale", "proj_super_beam_width", "proj_super_beam_speed",
    "proj_super_beam_force", "proj_super_hit_radius", "proj_super_beam_visual",
    "proj_final_damage", "proj_final_lifetime", "proj_final_particle_fx", "proj_final_spawn_bone",
    "startup", "active", "hitstun", "invuln", "blockstun", "hitstop",
    "hit_spark", "stretch_part", "stretch_len", "stretch_width", "stretch_height", "stretch_time", "post_link",
    "kb_type", "launch_profile", "kb_unknown", "kb_x", "air_kb",
    "speed_mod", "attack_property", "hit_reaction", "superbg",
    "abs",
)

FD_LABELS = {
    "move": "Move",
    "kind": "Kind",
    "hits": "Hits",
    "link": "Link",
    "context": "Details",
    "damage": "Damage",
    "meter": "Meter",
    "startup": "Startup",
    "active": "Active",
    "active2": "Active 2",
    "hitstun": "Hitstun",
    "blockstun": "Blockstun",
    "hitstop": "Hitstop",
    "adv_block_derived": "Derived Block Adv",
    "adv_block_observed": "Observed Block Adv",
    "hit_spark": "Hit Spark",
    "stretch_part": "Stretch Part",
    "stretch_len": "Reach Length",
    "stretch_width": "Reach Width",
    "stretch_height": "Reach Height",
    "stretch_time": "Stretch Timing",
    "post_link": "Post Link",
    "kb_type": "KB Style",
    "launch_profile": "Extra Launch",
    "kb_unknown": "Launch Adjust",
    "kb_x": "KB X",
    "air_kb": "Arc",
    "speed_mod": "Speed Mod",
    "invuln": "Invuln",
    "attack_property": "Attack Property",
    "hit_reaction": "Hit Reaction",
    "hit_result_flags": "Hit Result",
    "superbg": "SuperBG",
    **FPI.PROJECTILE_LABELS,
    "abs": "Address",
    **FSI.SUPER_DISPATCH_LABELS,
}


def _display_columns(tree: ttk.Treeview) -> list[str]:
    all_cols = list(tree["columns"])
    display = tree["displaycolumns"]
    if not display or display == "#all" or display == ("#all",):
        return all_cols
    if isinstance(display, str):
        return [display]
    return list(display)


def _tree_depth(tree: ttk.Treeview, item_id: str) -> int:
    """Return the visible tree depth for indentation in the Move column."""
    depth = 0
    try:
        cur = item_id
        while cur:
            cur = tree.parent(cur)
            if cur:
                depth += 1
    except Exception:
        return 0
    return depth


def _indent_move_text(tree: ttk.Treeview, parent: str, text: str) -> str:
    """Indent child rows in the actual Move column, not just the tiny tree gutter."""
    if not parent:
        return text
    depth = max(1, _tree_depth(tree, parent) + 1)
    prefix = ("    " * max(0, depth - 1)) + ("↳ " if depth >= 1 else "")
    return prefix + text


def _decorate_move_text(tree: ttk.Treeview, parent: str, text: str, mv: dict | None) -> str:
    return _indent_move_text(tree, parent, text)


def _rank_bucket(win, mv: dict):
    """Best-effort bucket helper: 0 normals, 1 specials, 2 supers, 3 taunt, 4 other."""
    try:
        ranker = getattr(win, "_explicit_notation", None)
        if callable(ranker):
            return int(tuple(ranker(mv))[0])
    except Exception:
        pass
    kind = str((mv or {}).get("kind") or "").lower()
    if kind == "special":
        return 1
    if kind in {"super", "hyper"}:
        return 2
    if "taunt" in str((mv or {}).get("move_name") or "").lower():
        return 3
    return 4


def _header_tags_for_members(win, members: list[dict]) -> tuple[str, ...]:
    """Structural tags for linked-family header rows."""
    buckets = [_rank_bucket(win, mv) for mv in (members or [])]
    buckets = [b for b in buckets if b is not None]
    bucket = min(buckets) if buckets else 4
    if bucket == 1:
        return ("family_header", "family_header_special")
    if bucket == 2:
        return ("family_header", "family_header_super")
    if bucket == 0:
        return ("family_header", "family_header_normal")
    return ("family_header", "family_header_other")


def configure_styles(root: tk.Toplevel) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    bg_main = "#101722"
    bg_panel = "#152033"
    bg_card = "#19263A"
    bg_header = "#21324A"
    bg_select = "#28466A"
    bg_entry = "#0F1724"
    txt_main = "#E8F1FF"
    txt_muted = "#91A7C1"
    txt_accent = "#8CBFFF"
    border = "#2C3E58"

    root.configure(bg=bg_main)

    style.configure("FD.TFrame", background=bg_main, borderwidth=0)
    style.configure("Top.TFrame", background=bg_main, borderwidth=0)
    style.configure("Hero.TFrame", background=bg_panel, borderwidth=1, relief="solid")
    style.configure("Card.TFrame", background=bg_card, borderwidth=1, relief="solid")
    style.configure("Glass.TFrame", background="#182338", borderwidth=1, relief="solid")
    style.configure("GlassInner.TFrame", background="#182338", borderwidth=0)
    style.configure("Inspector.TFrame", background=bg_panel, borderwidth=0)
    style.configure("Status.TFrame", background=bg_header, borderwidth=1, relief="solid")

    style.configure("Top.TLabel", background=bg_main, foreground=txt_main, font=("Segoe UI", 9))
    style.configure("HeroTitle.TLabel", background=bg_panel, foreground=txt_main, font=("Segoe UI Semibold", 13))
    style.configure("HeroSub.TLabel", background=bg_panel, foreground=txt_muted, font=("Segoe UI", 9))
    style.configure("Muted.Top.TLabel", background=bg_main, foreground=txt_muted, font=("Segoe UI", 9))
    style.configure("GlassTitle.TLabel", background="#182338", foreground="#DDEBFF", font=("Segoe UI Semibold", 9))
    style.configure("GlassHint.TLabel", background="#182338", foreground=txt_muted, font=("Segoe UI", 8))
    style.configure("Glass.TLabel", background="#182338", foreground=txt_main, font=("Segoe UI", 9))
    style.configure("GlassMuted.TLabel", background="#182338", foreground=txt_muted, font=("Segoe UI", 8))
    style.configure("Card.TLabel", background=bg_card, foreground=txt_main, font=("Segoe UI", 9))
    style.configure("CardMuted.TLabel", background=bg_card, foreground=txt_muted, font=("Segoe UI", 9))
    style.configure("ValueChip.TLabel", background=bg_entry, foreground=txt_main, borderwidth=1, relief="solid", padding=(7, 3), font=("Segoe UI", 9))
    style.configure("ValueChipHover.TLabel", background="#1E3350", foreground=txt_main, borderwidth=1, relief="solid", padding=(7, 3), font=("Segoe UI", 9))
    style.configure("ValueChanged.TLabel", background="#2B2412", foreground="#FFE3A3", borderwidth=1, relief="solid", padding=(7, 3), font=("Segoe UI Semibold", 9))
    style.configure("ValueChangedHover.TLabel", background="#3A2E12", foreground="#FFE9B8", borderwidth=1, relief="solid", padding=(7, 3), font=("Segoe UI Semibold", 9))
    style.configure("ValueStatic.TLabel", background=bg_card, foreground=txt_main, padding=(7, 3), font=("Segoe UI", 9))
    # Selection-strip values are display tiles, not Entry lookalikes.  The old
    # outlined value labels made the cached summary read like an office form.
    # These use a quiet card surface, a thin color rail, and a larger number so
    # the row feels like a fighting-game stat readout instead.
    style.configure("QuickValue.TLabel", background=bg_entry, foreground=txt_main, borderwidth=1, relief="solid", padding=(7, 3), font=("Segoe UI", 9))
    style.configure("QuickImportant.TLabel", background="#173A5D", foreground="#ECF7FF", borderwidth=1, relief="solid", padding=(7, 3), font=("Segoe UI Semibold", 9))
    style.configure("QuickImportantHover.TLabel", background="#245178", foreground="#FFFFFF", borderwidth=1, relief="solid", padding=(7, 3), font=("Segoe UI Semibold", 9))

    quick_tile_specs = {
        "QuickTile": ("#16253A", "#9DB2CC", "#F0F6FF"),
        "QuickImportantTile": ("#173452", "#B8D8FF", "#FFFFFF"),
        "QuickOtgOnTile": ("#20354E", "#A9D9FF", "#FFFFFF"),
        "QuickOtgOffTile": ("#1C2D3C", "#9BB3BF", "#E8F5F7"),
        "QuickMissingTile": ("#172131", "#7E91AA", "#A8B7C9"),
    }
    for _base, (_bg, _label_fg, _value_fg) in quick_tile_specs.items():
        style.configure(f"{_base}.TFrame", background=_bg, borderwidth=0, relief="flat")
        style.configure(f"{_base}Label.TLabel", background=_bg, foreground=_label_fg, font=("Segoe UI Semibold", 8))
        style.configure(f"{_base}Value.TLabel", background=_bg, foreground=_value_fg, font=("Segoe UI Semibold", 11), padding=(0, 1, 0, 0))
    style.configure("QuickInlineColon.TLabel", background="#16253A", foreground="#8FA7C3", font=("Segoe UI Semibold", 8))
    style.configure("Section.TLabel", background=bg_card, foreground=txt_accent, font=("Segoe UI Semibold", 9))
    style.configure("InspectorTitle.TLabel", background=bg_panel, foreground=txt_main, font=("Segoe UI Semibold", 13))
    style.configure("InspectorSub.TLabel", background=bg_panel, foreground=txt_muted, font=("Segoe UI", 9))
    style.configure("Status.TLabel", background=bg_header, foreground=txt_main, font=("Segoe UI", 9))
    style.configure("FilterLabel.TLabel", background=bg_card, foreground=txt_muted, font=("Segoe UI", 8))

    style.configure(
        "Treeview",
        background="#121C2B",
        fieldbackground="#121C2B",
        foreground=txt_main,
        bordercolor=border,
        lightcolor=border,
        darkcolor=border,
        rowheight=24,
        font=("Segoe UI", 9),
    )
    style.map("Treeview", background=[("selected", "#355F95")], foreground=[("selected", "#FFFFFF")])

    style.configure(
        "Treeview.Heading",
        background=bg_header,
        foreground=txt_main,
        relief="solid",
        borderwidth=1,
        font=("Segoe UI Semibold", 9),
    )
    style.map("Treeview.Heading", background=[("active", "#2A3F5C")])

    style.configure(
        "TButton",
        background=bg_header,
        foreground=txt_main,
        bordercolor=border,
        focusthickness=1,
        focuscolor="#3A567A",
        font=("Segoe UI", 9),
        padding=(8, 4),
    )
    style.map(
        "TButton",
        background=[("active", "#2B4260"), ("pressed", "#334D70")],
        foreground=[("active", txt_main)],
    )
    style.configure("Small.TButton", font=("Segoe UI", 8), padding=(6, 2))
    style.configure(
        "Glass.TButton",
        background="#22324C",
        foreground=txt_main,
        bordercolor="#3A5070",
        lightcolor="#4E6B92",
        darkcolor="#101827",
        focusthickness=1,
        focuscolor="#6D95C7",
        font=("Segoe UI", 8),
        padding=(8, 4),
    )
    style.map(
        "Glass.TButton",
        background=[("active", "#2E4669"), ("pressed", "#39567E"), ("disabled", "#172235")],
        foreground=[("active", txt_main), ("disabled", "#65748A")],
        bordercolor=[("active", "#6D95C7")],
    )
    style.configure(
        "GlassPrimary.TButton",
        background="#24466E",
        foreground="#F0F7FF",
        bordercolor="#6D95C7",
        lightcolor="#7FA7D8",
        darkcolor="#152338",
        font=("Segoe UI Semibold", 8),
        padding=(8, 4),
    )
    style.map(
        "GlassPrimary.TButton",
        background=[("active", "#315D8E"), ("pressed", "#3B6DA5")],
        foreground=[("active", "#FFFFFF")],
    )

    # Polished workbench chrome. Major regions have a restrained card edge;
    # ordinary read-only data stays flat so the UI does not become a grid of
    # nested boxes.
    style.configure("WorkbenchBar.TFrame", background="#1B2940", borderwidth=0, relief="flat")
    style.configure("CommandBar.TFrame", background="#18263D", borderwidth=0, relief="flat")
    style.configure("WorkbenchTitle.TLabel", background="#1A2940", foreground="#F6FAFF", font=("Segoe UI Semibold", 13))
    style.configure("WorkbenchSubtitle.TLabel", background="#1A2940", foreground="#A6B8CC", font=("Segoe UI", 9))
    style.configure("CommandLabel.TLabel", background="#17253B", foreground="#A8BDD5", font=("Segoe UI Semibold", 8))
    style.configure("CommandMuted.TLabel", background="#17253B", foreground="#9AAFCA", font=("Segoe UI", 8))
    style.configure("SortBadge.TLabel", background="#203854", foreground="#CFE6FF", borderwidth=0, relief="flat", padding=(8, 4), font=("Segoe UI Semibold", 8))
    style.configure("Badge.TLabel", background="#24354D", foreground="#D6E7FF", borderwidth=0, relief="flat", padding=(8, 4), font=("Segoe UI Semibold", 8))
    style.configure("BadgeGood.TLabel", background="#21433B", foreground="#C9F5D8", borderwidth=0, relief="flat", padding=(8, 4), font=("Segoe UI Semibold", 8))
    style.configure("BadgeWarn.TLabel", background="#51402A", foreground="#FFE9B8", borderwidth=0, relief="flat", padding=(8, 4), font=("Segoe UI Semibold", 8))
    style.configure("Toolbar.TButton", background="#223650", foreground=txt_main, bordercolor="#2D4563", lightcolor="#385778", darkcolor="#182538", font=("Segoe UI", 8), padding=(10, 5))
    style.map("Toolbar.TButton", background=[("active", "#2D476A"), ("pressed", "#385778")])
    style.configure("ToolbarPrimary.TButton", background="#34679A", foreground="#FFFFFF", bordercolor="#5F8FC2", lightcolor="#76A4D6", darkcolor="#1F3C5D", font=("Segoe UI Semibold", 8), padding=(10, 5))
    style.map("ToolbarPrimary.TButton", background=[("active", "#3971AA"), ("pressed", "#24547F")])
    style.configure("CompareDelta.TFrame", background="#17263C", borderwidth=0)
    style.configure("CompareDeltaChip.TFrame", background="#1A2C44", borderwidth=1, relief="solid")
    style.configure("CompareDeltaLabel.TLabel", background="#1A2C44", foreground="#8FB0D1", font=("Segoe UI", 7))
    style.configure("CompareDeltaValue.TLabel", background="#1A2C44", foreground="#F3F8FF", font=("Segoe UI Semibold", 9))
    style.configure("CompareDeltaPos.TLabel", background="#1A2C44", foreground="#9FE3B1", font=("Segoe UI Semibold", 9))
    style.configure("CompareDeltaNeg.TLabel", background="#1A2C44", foreground="#FFB6B6", font=("Segoe UI Semibold", 9))
    style.configure("CompareDeltaNeutral.TLabel", background="#1A2C44", foreground="#C9D6E6", font=("Segoe UI Semibold", 9))
    style.configure("MoveTableHint.TLabel", background=bg_card, foreground="#8FA6C2", font=("Segoe UI", 8))
    style.configure("Toolbar.TMenubutton", background="#223650", foreground=txt_main, bordercolor="#2D4563", lightcolor="#385778", darkcolor="#182538", font=("Segoe UI", 8), padding=(10, 5))
    style.map("Toolbar.TMenubutton", background=[("active", "#2D476A"), ("pressed", "#385778")])
    style.configure("ToolbarPrimary.TMenubutton", background="#34679A", foreground="#FFFFFF", bordercolor="#5F8FC2", lightcolor="#76A4D6", darkcolor="#1F3C5D", font=("Segoe UI Semibold", 8), padding=(10, 5))
    style.map("ToolbarPrimary.TMenubutton", background=[("active", "#3971AA"), ("pressed", "#24547F")])
    style.configure("InspectorHero.TFrame", background="#17263B", borderwidth=1, relief="solid")
    style.configure("InspectorHeroTitle.TLabel", background="#17263B", foreground="#F5FAFF", font=("Segoe UI Semibold", 15))
    style.configure("InspectorHeroSub.TLabel", background="#17263B", foreground="#9FB4CC", font=("Segoe UI", 8))
    style.configure("InspectorHelp.TFrame", background="#13233A", borderwidth=0, relief="flat")
    style.configure("InspectorHelpTitle.TLabel", background="#13233A", foreground="#8CBFFF", font=("Segoe UI Semibold", 8))
    style.configure("InspectorHelpText.TLabel", background="#13233A", foreground="#BFD1E6", font=("Segoe UI", 8))
    style.configure("InspectorAction.TButton", background="#20314A", foreground=txt_main, bordercolor="#43627F", font=("Segoe UI Semibold", 8), padding=(8, 4))
    style.map("InspectorAction.TButton", background=[("active", "#2D476A"), ("pressed", "#385778")])
    style.configure("InspectorSection.TFrame", background="#132036", borderwidth=1, relief="solid")
    style.configure("InspectorRow.TFrame", background="#132036", borderwidth=0)
    style.configure("InspectorSection.TLabel", background="#132036", foreground="#8CBFFF", font=("Segoe UI Semibold", 8))
    style.configure("InspectorField.TLabel", background="#132036", foreground="#91A7C1", font=("Segoe UI", 8))
    style.configure("InspectorStatic.TLabel", background="#132036", foreground="#E8F1FF", font=("Segoe UI", 9), padding=(7, 3))
    style.configure("Inspector.Vertical.TScrollbar", background="#4A6A90", troughcolor="#0D1521", bordercolor="#2C3E58", arrowcolor="#E8F1FF", width=14)
    style.configure("InspectorValue.TLabel", background="#1A2A42", foreground="#EAF2FF", borderwidth=0, relief="flat", padding=(8, 5), font=("Segoe UI Semibold", 9))
    style.configure("InspectorValueHover.TLabel", background="#223A5A", foreground="#FFFFFF", borderwidth=0, relief="flat", padding=(8, 5), font=("Segoe UI Semibold", 9))
    style.configure("InspectorValueChanged.TLabel", background="#3A2D17", foreground="#FFE8AE", borderwidth=0, relief="flat", padding=(8, 5), font=("Segoe UI Semibold", 9))
    style.configure("InspectorValueChangedHover.TLabel", background="#4B3A1A", foreground="#FFF1C7", borderwidth=0, relief="flat", padding=(8, 5), font=("Segoe UI Semibold", 9))
    style.configure("InspectorReadOnly.TLabel", background="#18263A", foreground="#D6E6F9", borderwidth=0, relief="flat", padding=(8, 5), font=("Segoe UI", 9))
    style.configure("InspectorDataRow.TFrame", background="#132036", borderwidth=0)
    style.configure("InspectorDataRowAlt.TFrame", background="#16253C", borderwidth=0)
    style.configure("InspectorDataLabel.TLabel", background="#132036", foreground="#8FA7C3", font=("Segoe UI Semibold", 8))
    style.configure("InspectorDataLabelAlt.TLabel", background="#16253C", foreground="#8FA7C3", font=("Segoe UI Semibold", 8))
    style.configure("SurfaceReset.TButton", background="#6A4652", foreground="#FFF2F4", bordercolor="#8D6670", lightcolor="#9D7B84", darkcolor="#4B3038", font=("Segoe UI Semibold", 8), padding=(10, 5))
    style.map("SurfaceReset.TButton", background=[("active", "#734A56"), ("pressed", "#50303B")])
    style.configure("GradientHint.TLabel", background="#16243A", foreground="#AABDD3", font=("Segoe UI", 8))
    style.map("Inspector.Vertical.TScrollbar", background=[("active", "#6288B7"), ("pressed", "#7CA7D8")])
    style.configure("Timeline.TFrame", background="#0F1724", borderwidth=1, relief="solid")
    style.configure("TimelineTitle.TLabel", background="#0F1724", foreground="#AFC9E9", font=("Segoe UI Semibold", 8))
    style.configure("TimelineSub.TLabel", background="#0F1724", foreground="#7E98B8", font=("Segoe UI", 8))
    style.configure("Compare.TFrame", background="#132137", borderwidth=1, relief="solid")
    style.configure("CompareTitle.TLabel", background="#132137", foreground="#AFC9E9", font=("Segoe UI Semibold", 8))
    style.configure("CompareSub.TLabel", background="#132137", foreground="#C7D9F0", font=("Segoe UI", 8))
    style.configure("StatusMuted.TLabel", background=bg_header, foreground="#A8BDD5", font=("Segoe UI", 8))
    style.configure("Palette.TFrame", background="#101722")
    style.configure("PaletteTitle.TLabel", background="#101722", foreground="#F3F8FF", font=("Segoe UI Semibold", 13))
    style.configure("PaletteSub.TLabel", background="#101722", foreground="#9FB4CC", font=("Segoe UI", 8))

    style.configure(
        "TEntry",
        fieldbackground=bg_entry,
        foreground=txt_main,
        bordercolor=border,
        lightcolor=border,
        darkcolor=border,
        insertcolor=txt_main,
        padding=(5, 3),
    )


def build_top_bar(win) -> None:
    """Compact command bar with a clear primary workflow.

    The old top deck gave equal visual weight to every action. This version puts
    the current character/profile state first, keeps navigation on one line,
    and moves lower-frequency utilities into small menus.
    """
    cname = win.target_slot.get("char_name", "-")
    normal_count = len(getattr(win, "moves", []) or [])

    top = ttk.Frame(win.root, style="Top.TFrame")
    top.pack(side="top", fill="x", padx=12, pady=(10, 8))

    identity = ttk.Frame(top, style="WorkbenchBar.TFrame", padding=(12, 10))
    identity.pack(fill="x")
    left = ttk.Frame(identity, style="WorkbenchBar.TFrame")
    left.pack(side="left", fill="x", expand=True)
    ttk.Label(left, text="Frame Data", style="WorkbenchTitle.TLabel").pack(side="left")
    ttk.Label(left, text=f"{cname}  |  {win.slot_label}", style="WorkbenchSubtitle.TLabel").pack(side="left", padx=(12, 0), pady=(3, 0))

    chips = ttk.Frame(identity, style="WorkbenchBar.TFrame")
    chips.pack(side="right")
    writer_style = "BadgeGood.TLabel" if U.WRITER_AVAILABLE else "BadgeWarn.TLabel"
    ttk.Label(chips, textvariable=win._writer_var, style=writer_style).pack(side="left", padx=(6, 0))
    ttk.Label(chips, text=f"Normals {normal_count:,}", style="BadgeGood.TLabel").pack(side="left", padx=(6, 0))
    ttk.Label(chips, textvariable=win._projectile_status_var, style="Badge.TLabel").pack(side="left", padx=(6, 0))
    ttk.Label(chips, textvariable=win._super_status_var, style="Badge.TLabel").pack(side="left", padx=(6, 0))

    command = ttk.Frame(top, style="CommandBar.TFrame", padding=(10, 8))
    command.pack(fill="x", pady=(8, 0))

    search = ttk.Frame(command, style="CommandBar.TFrame")
    search.pack(side="left", fill="x", expand=True)
    ttk.Label(search, text="Search", style="CommandLabel.TLabel").pack(side="left", padx=(0, 6))
    ent = ttk.Entry(search, textvariable=win._filter_var, width=34)
    ent.pack(side="left", fill="x", expand=True)
    win._search_entry = ent
    Tooltip(ent, "Search move names, kinds, and addresses. Ctrl+F jumps here.")
    ent.bind("<Return>", lambda _e: win._apply_filter())
    ttk.Button(search, text="Clear", style="Toolbar.TButton", command=win._clear_filter).pack(side="left", padx=(6, 0))

    divider = ttk.Separator(command, orient="vertical")
    divider.pack(side="left", fill="y", padx=10)

    win._filter_panel_btn_var = tk.StringVar(master=win.root, value="Filters")
    nav = ttk.Frame(command, style="CommandBar.TFrame")
    nav.pack(side="left")
    for label, mode in (("Frame", "frame"), ("Projectiles", "projectile"), ("Supers", "super"), ("All", "all")):
        ttk.Button(nav, text=label, style="Toolbar.TButton", command=lambda m=mode: win._set_fd_view_mode(m)).pack(side="left", padx=(0 if label == "Frame" else 4, 0))
    ttk.Button(nav, textvariable=win._filter_panel_btn_var, style="Toolbar.TButton", command=lambda: getattr(win, "_toggle_advanced_filters", lambda: None)()).pack(side="left", padx=(6, 0))

    divider2 = ttk.Separator(command, orient="vertical")
    divider2.pack(side="left", fill="y", padx=10)

    menus = ttk.Frame(command, style="CommandBar.TFrame")
    menus.pack(side="left")

    density_btn = ttk.Menubutton(menus, text="Density", style="Toolbar.TMenubutton")
    win._density_button = density_btn
    density_menu = tk.Menu(density_btn, tearoff=False)
    for label, key in (("Compact", "compact"), ("Standard", "standard"), ("Detailed", "detailed")):
        density_menu.add_command(label=label, command=lambda k=key: win._set_table_density(k))
    density_btn["menu"] = density_menu
    density_btn.pack(side="left")
    win._density_menu = density_menu

    tools_btn = ttk.Menubutton(menus, text="Tools", style="Toolbar.TMenubutton")
    tools_menu = tk.Menu(tools_btn, tearoff=False)
    tools_menu.add_command(label="Refresh visible fields (F5)", command=win._refresh_visible)
    tools_menu.add_command(label="Show changed rows", command=win._toggle_changed_rows)
    tools_menu.add_command(label="Reset all changed values", command=win._reset_all_moves)
    tools_menu.add_separator()
    tools_menu.add_command(label="Expand all", command=win._expand_all)
    tools_menu.add_command(label="Collapse all", command=win._collapse_all)
    tools_menu.add_command(label="Reset move order", command=win._reset_to_original_grouping)
    tools_menu.add_command(label="Reset layout to clean default", command=win._reset_workbench_layout)
    tools_menu.add_separator()
    tools_menu.add_command(label="Dump character", command=lambda: getattr(win, "_dump_character_data", lambda: None)())
    tools_menu.add_command(label="Show bones", command=lambda: getattr(win, "_show_bones", lambda: None)())
    tools_menu.add_command(label="Command palette (Ctrl+P)", command=win._show_command_palette)
    tools_btn["menu"] = tools_menu
    tools_btn.pack(side="left", padx=(5, 0))
    win._tools_menu = tools_menu

    win._sort_status_var = tk.StringVar(master=win.root, value="Sort: profile order")
    ttk.Label(command, textvariable=win._sort_status_var, style="SortBadge.TLabel").pack(side="left", padx=(8, 0))

    # Profiles build automatically after the frame list has painted. Keeping
    # this toolbar clear also prevents stale/manual profile workflows.
    win._profile_menu = None

    ttk.Button(command, text="Save patch", style="ToolbarPrimary.TButton", command=win._save_fd_patch_config).pack(side="right")
    reset_layout = ttk.Button(command, text="Reset view", style="Toolbar.TButton", command=win._reset_workbench_layout)
    reset_layout.pack(side="right", padx=(6, 0))
    Tooltip(reset_layout, "Restore the clean Frame-data view: sane columns, widths, filters, order, and inspector split. Does not change patch edits.")
    reset_all = ttk.Button(command, text="Reset all", style="SurfaceReset.TButton", command=win._reset_all_moves)
    reset_all.pack(side="right", padx=(6, 0))
    Tooltip(reset_all, "Reset all changed values in this patch session back to their defaults/original cached values.")
    redo = ttk.Button(command, text="Redo", style="Toolbar.TButton", command=win._redo_last_change)
    redo.pack(side="right", padx=(6, 0))
    undo = ttk.Button(command, text="Undo", style="Toolbar.TButton", command=win._undo_last_change)
    undo.pack(side="right", padx=(6, 0))
    win._redo_button = redo
    win._undo_button = undo
    ttk.Label(command, textvariable=win._session_summary_var, style="CommandMuted.TLabel").pack(side="right", padx=(0, 8))


def _build_inspector(win, parent: ttk.Frame) -> None:
    win._inspector_value_vars = {}
    win._inspector_buttons = {}
    win._inspector_value_widgets = {}
    win._inspector_editable_cols = set()
    win._inspector_sections = []

    # The inspector can be taller than the available window height. Use a real
    # scrolling canvas and reserve the scrollbar column first so it remains
    # visible even when the right pane is narrow.
    parent.rowconfigure(0, weight=1)
    parent.columnconfigure(0, weight=1)
    parent.columnconfigure(1, weight=0)

    canvas = tk.Canvas(parent, bg="#132036", highlightthickness=0, bd=0, takefocus=True)
    # Use a dedicated visible scrollbar style. The default clam thumb can blend
    # into the inspector background on some Windows themes.
    scroll = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview, style="Inspector.Vertical.TScrollbar")
    canvas.configure(yscrollcommand=scroll.set)
    canvas.grid(row=0, column=0, sticky="nsew")
    scroll.grid(row=0, column=1, sticky="ns")
    win._inspector_canvas = canvas
    win._inspector_scrollbar = scroll

    inner = ttk.Frame(canvas, style="Inspector.TFrame", padding=(12, 12))
    win._inspector_inner = inner
    window_id = canvas.create_window((0, 0), window=inner, anchor="nw")

    # Tk can fire Canvas/inner <Configure> before every inspector card has
    # finished requesting its height.  Measuring canvas.bbox(window_id) at that
    # moment makes the scrollregion exactly the viewport height, which is why
    # the old scrollbar looked maxed out even with cards below the fold.
    _scroll_sync_after_id = None

    def _sync_scrollregion(_evt=None):
        """Measure the *requested* inspector height after layout settles.

        The requested height is authoritative here; Canvas bbox() alone can
        still reflect the pre-layout viewport during an idle resize.
        """
        nonlocal _scroll_sync_after_id
        _scroll_sync_after_id = None
        try:
            if not canvas.winfo_exists() or not inner.winfo_exists():
                return
            viewport_w = max(1, int(canvas.winfo_width()))
            viewport_h = max(1, int(canvas.winfo_height()))
            canvas.itemconfigure(window_id, width=viewport_w)
            # The inner frame contains all cards.  reqheight remains correct
            # after pack_forget()/pack() changes made by context layout.
            content_h = max(int(inner.winfo_reqheight()), int(inner.winfo_height()), viewport_h)
            bbox = canvas.bbox(window_id)
            if bbox:
                content_h = max(content_h, int(bbox[3] - bbox[1]))
            canvas.configure(scrollregion=(0, 0, viewport_w, content_h))
        except Exception:
            pass

    def _schedule_scrollregion(_evt=None):
        """Coalesce resize/repack storms into one post-layout measurement."""
        nonlocal _scroll_sync_after_id
        try:
            if _scroll_sync_after_id is not None:
                canvas.after_cancel(_scroll_sync_after_id)
        except Exception:
            pass
        try:
            _scroll_sync_after_id = canvas.after_idle(_sync_scrollregion)
        except Exception:
            _sync_scrollregion()

    # Other code repacks cards for projectile/super context.  Give it a small,
    # explicit hook so scroll limits are always refreshed after that operation.
    win._refresh_inspector_scrollregion = _schedule_scrollregion

    def _pointer_is_over_inspector(event) -> bool:
        """Use screen coordinates, not widget-master walking.

        Ttk children embedded in a Canvas can have surprising master chains on
        Windows; bounds testing is reliable for labels, buttons, and entries.
        """
        try:
            x = int(event.x_root)
            y = int(event.y_root)
            left = min(int(canvas.winfo_rootx()), int(scroll.winfo_rootx()))
            right = max(int(canvas.winfo_rootx() + canvas.winfo_width()), int(scroll.winfo_rootx() + scroll.winfo_width()))
            top = min(int(canvas.winfo_rooty()), int(scroll.winfo_rooty()))
            bottom = max(int(canvas.winfo_rooty() + canvas.winfo_height()), int(scroll.winfo_rooty() + scroll.winfo_height()))
            return left <= x < right and top <= y < bottom
        except Exception:
            return False

    def _wheel(event):
        # bind_all is necessary because the pointer is normally over a ttk Label
        # inside the Canvas window. Do not steal the wheel from the move table
        # or any other control in the workbench.
        if not _pointer_is_over_inspector(event):
            return None
        try:
            if getattr(event, "num", None) == 4:
                delta = -3
            elif getattr(event, "num", None) == 5:
                delta = 3
            else:
                raw = int(getattr(event, "delta", 0) or 0)
                if not raw:
                    return "break"
                steps = int(raw / 120)
                if not steps:
                    steps = 1 if raw > 0 else -1
                # Three small Canvas units per standard Windows notch gives a
                # normal inspector scroll speed without jumping whole cards.
                delta = -steps * 3
            canvas.yview_scroll(delta, "units")
        except Exception:
            pass
        return "break"

    # Keep these bindings installed for this workbench only. Unlike bind/unbind
    # on <Enter>/<Leave>, this survives moving over the inspector's nested
    # labels, buttons, and value chips. Returning None outside the inspector
    # lets the Treeview keep its normal wheel behavior.
    try:
        win._inspector_wheel_bind_ids = [
            ("<MouseWheel>", win.root.bind_all("<MouseWheel>", _wheel, add="+")),
            ("<Button-4>", win.root.bind_all("<Button-4>", _wheel, add="+")),
            ("<Button-5>", win.root.bind_all("<Button-5>", _wheel, add="+")),
        ]
    except Exception:
        win._inspector_wheel_bind_ids = []

    inner.bind("<Configure>", _schedule_scrollregion)
    canvas.bind("<Configure>", _schedule_scrollregion)
    canvas.bind("<Prior>", lambda _e: (canvas.yview_scroll(-1, "pages"), "break")[1])
    canvas.bind("<Next>", lambda _e: (canvas.yview_scroll(1, "pages"), "break")[1])
    canvas.bind("<Home>", lambda _e: (canvas.yview_moveto(0.0), "break")[1])
    canvas.bind("<End>", lambda _e: (canvas.yview_moveto(1.0), "break")[1])

    def _value_click(col: str):
        if col == "link":
            try:
                win._status_var.set("Link is display-only. It groups move-table sections that appear to belong to one human move.")
            except Exception:
                pass
            return
        if col == "kind":
            try:
                win._status_var.set("Kind is informational. It marks the row bucket, not a writable frame-data value.")
            except Exception:
                pass
            return
        if col == "hits":
            try:
                win._status_var.set("Hits shows detected per-hit bundles. Expand a multi-hit move to edit each hit separately.")
            except Exception:
                pass
            return
        if col == "invuln":
            try:
                win._status_var.set("Invuln is the proven +0x1218 startup-phase signature. It is display-only.")
            except Exception:
                pass
            return
        if col == "context":
            try:
                win._status_var.set("Details is raw scout summary text. The cleaner quick strip above the table is the normal way to read projectile/super values.")
            except Exception:
                pass
            return
        if col == "abs":
            win._copy_selected_address()
            return
        if not U.WRITER_AVAILABLE:
            try:
                win._status_var.set("Frame-data writer is unavailable, so this value cannot be edited right now.")
            except Exception:
                pass
            return
        win._edit_selected_column(col)

    def _make_chip(parent_widget, col: str, var: tk.StringVar):
        editable = col not in {"kind", "hits", "link", "invuln"}
        style = "InspectorValue.TLabel" if editable else "InspectorReadOnly.TLabel"
        chip = ttk.Label(parent_widget, textvariable=var, style=style, anchor="w")
        chip.pack(side="left", fill="x", expand=True, padx=(4, 0))
        win._inspector_value_widgets[col] = chip

        if editable:
            chip.configure(cursor="hand2")
            chip.bind("<Button-1>", lambda _e, c=col: _value_click(c))
            chip.bind("<Return>", lambda _e, c=col: _value_click(c))
            chip.bind("<space>", lambda _e, c=col: _value_click(c))
            chip.bind("<Enter>", lambda _e, w=chip, c=col: getattr(win, "_configure_inspector_chip_style", lambda *_a, **_k: None)(w, c, True))
            chip.bind("<Leave>", lambda _e, w=chip, c=col: getattr(win, "_configure_inspector_chip_style", lambda *_a, **_k: None)(w, c, False))
            chip.configure(takefocus=True)
            win._inspector_editable_cols.add(col)
        return chip

    win._inspector_title_var = tk.StringVar(master=win.root, value="Select a move")
    win._inspector_subtitle_var = tk.StringVar(master=win.root, value="Choose a move to inspect cached data. Selection never triggers a scan.")
    win._inspector_hint_var = tk.StringVar(master=win.root, value="Click a highlighted value to edit it. Read-only fields stay flat.")
    win._inspector_action_help_default = (
        "Pin Compare keeps this move as a read-only reference while you browse. "
        "Copy Address places this row's move-table anchor on the clipboard."
    )
    win._inspector_action_help_var = tk.StringVar(master=win.root, value=win._inspector_action_help_default)

    def _set_action_help(text: str | None = None):
        value = str(text or win._inspector_action_help_default)
        try:
            if win._inspector_action_help_var.get() != value:
                win._inspector_action_help_var.set(value)
        except Exception:
            pass

    # Let the rest of the workbench update the small permanent help box without
    # rebuilding inspector widgets. This is intentionally separate from the
    # below-buttons edit hint, which explains the selected move's field state.
    win._set_inspector_action_help = _set_action_help

    hero = ttk.Frame(inner, style="InspectorHero.TFrame", padding=(12, 10))
    hero.pack(fill="x", pady=(0, 10))
    ttk.Label(hero, textvariable=win._inspector_title_var, style="InspectorHeroTitle.TLabel", wraplength=390).pack(anchor="w")
    ttk.Label(hero, textvariable=win._inspector_subtitle_var, style="InspectorHeroSub.TLabel", wraplength=390).pack(anchor="w", pady=(2, 7))

    help_box = ttk.Frame(hero, style="InspectorHelp.TFrame", padding=(8, 4))
    help_box.pack(fill="x", pady=(0, 8))
    ttk.Label(help_box, textvariable=win._inspector_action_help_var, style="InspectorHelpText.TLabel", wraplength=360, justify="left").pack(anchor="w")

    hero_actions = ttk.Frame(hero, style="InspectorHero.TFrame")
    hero_actions.pack(fill="x")

    def _action_button(text: str, command, help_text: str, *, primary: bool = False):
        button = ttk.Button(
            hero_actions,
            text=text,
            style="ToolbarPrimary.TButton" if primary else "InspectorAction.TButton",
            command=command,
        )
        button.pack(side="left", padx=(0 if not hero_actions.winfo_children() else 5, 0))
        # The helper panel should hold the last action the user inspected.
        # Resetting on <Leave>/<FocusOut> made it jump back to the initial
        # Pin Compare text, which is both misleading and visually twitchy.
        button.bind("<Enter>", lambda _e, t=help_text: _set_action_help(t), add=True)
        button.bind("<FocusIn>", lambda _e, t=help_text: _set_action_help(t), add=True)
        Tooltip(button, help_text)
        return button

    btn = _action_button(
        "Replace anim",
        lambda: win._edit_selected_column("move"),
        "Replace animation changes this row's animation ID. It writes only the selected move's animation reference.",
        primary=True,
    )
    win._inspector_buttons["move"] = btn
    _action_button(
        "Refresh row",
        lambda: win._refresh_selected_row(),
        "Refresh Row reads optional loose fields for this selected move in the background. It does not rebuild or rescan the character profile.",
    )
    _action_button(
        "Pin compare",
        lambda: win._pin_current_move(),
        "Pin Compare locks this move as a read-only reference. Select another move and the Compare card shows cached damage, timing, and stun differences.",
    )
    _action_button(
        "Copy address",
        lambda: win._copy_selected_address(),
        "Copy Address puts this move's table anchor (for example 0x908AFBE6) on the clipboard. It is the row anchor, not every individual field's write address.",
    )
    ttk.Label(hero, textvariable=win._inspector_hint_var, style="InspectorHeroSub.TLabel", wraplength=390).pack(anchor="w", pady=(9, 0))

    # These headline values are not merely decorative. They are the fastest
    # edit targets for the four fields people change most often.
    win._headline_stat_widgets = {}
    stats = ttk.Frame(inner, style="InspectorSection.TFrame")
    stats.pack(fill="x", pady=(0, 10))
    for label, key in (("Startup", "startup"), ("Active", "active"), ("Hitstop", "hitstop"), ("Blockstun", "blockstun")):
        cell = ttk.Frame(stats, style="InspectorSection.TFrame")
        cell.pack(side="left", fill="x", expand=True, padx=(0 if label == "Startup" else 5, 0))
        ttk.Label(cell, text=label, style="InspectorField.TLabel").pack(anchor="w")
        var = tk.StringVar(master=win.root, value=" - ")
        win._headline_stat_vars[key] = var
        value = ttk.Label(cell, textvariable=var, style="QuickImportant.TLabel", anchor="center", cursor="hand2", takefocus=True)
        value.pack(fill="x", pady=(2, 0))
        value.bind("<Button-1>", lambda _e, c=key: _value_click(c))
        value.bind("<Return>", lambda _e, c=key: _value_click(c))
        value.bind("<space>", lambda _e, c=key: _value_click(c))
        value.bind("<Enter>", lambda _e, w=value: w.configure(style="QuickImportantHover.TLabel"))
        value.bind("<Leave>", lambda _e, w=value: w.configure(style="QuickImportant.TLabel"))
        win._headline_stat_widgets[key] = value
        field_help = get_field_help(key, "")
        Tooltip(value, (field_help + "\n\n" if field_help else "") + "Click to edit this value.")

    timeline = ttk.Frame(inner, style="Timeline.TFrame", padding=(10, 8))
    timeline.pack(fill="x", pady=(0, 10))
    ttk.Label(timeline, text="FRAME TIMELINE", style="TimelineTitle.TLabel").pack(anchor="w")
    win._timeline_summary_var = tk.StringVar(master=win.root, value="Select a move to draw its cached startup and active frames.")
    ttk.Label(timeline, textvariable=win._timeline_summary_var, style="TimelineSub.TLabel", wraplength=390).pack(anchor="w", pady=(2, 5))
    # Do not reuse the outer inspector Canvas variable here.  The inspector
    # scroll callbacks close over it; rebinding that name to the timeline Canvas
    # made scrollregion updates target the timeline instead of the sidebar.
    timeline_canvas = tk.Canvas(timeline, height=62, bg="#0F1724", highlightthickness=0, bd=0)
    timeline_canvas.pack(fill="x")
    win._timeline_canvas = timeline_canvas
    timeline_canvas.bind("<Configure>", lambda _e: win._refresh_frame_timeline())

    compare = ttk.Frame(inner, style="Compare.TFrame", padding=(10, 8))
    compare.pack(fill="x", pady=(0, 10))
    compare_head = ttk.Frame(compare, style="Compare.TFrame")
    compare_head.pack(fill="x")
    ttk.Label(compare_head, text="COMPARE", style="CompareTitle.TLabel").pack(side="left")
    ttk.Button(compare_head, text="Clear", style="Small.TButton", command=lambda: win._clear_pinned_move()).pack(side="right")
    win._compare_title_var = tk.StringVar(master=win.root, value="No pinned move")
    win._compare_summary_var = tk.StringVar(master=win.root, value="Pin a move to compare damage, timing, and stun as you browse.")
    ttk.Label(compare, textvariable=win._compare_title_var, style="CompareSub.TLabel").pack(anchor="w", pady=(4, 0))
    ttk.Label(compare, textvariable=win._compare_summary_var, style="CompareSub.TLabel", wraplength=390).pack(anchor="w", pady=(2, 6))
    delta_row = ttk.Frame(compare, style="CompareDelta.TFrame")
    delta_row.pack(fill="x")
    win._compare_delta_vars = {}
    for _label in ("Damage", "Startup", "Active", "Hitstop", "Blockstun"):
        key = _label.lower().replace(" ", "_")
        chip = ttk.Frame(delta_row, style="CompareDeltaChip.TFrame", padding=(7, 5))
        chip.pack(side="left", fill="x", expand=True, padx=(0 if not delta_row.winfo_children() else 5, 0))
        ttk.Label(chip, text=_label, style="CompareDeltaLabel.TLabel").pack(anchor="w")
        _var = tk.StringVar(master=win.root, value=" - ")
        win._compare_delta_vars[key] = _var
        ttk.Label(chip, textvariable=_var, style="CompareDeltaNeutral.TLabel").pack(anchor="w", pady=(1, 0))

    sections = [
        ("Move link", ["link"]),
        ("Impact", ["hits", "damage", "meter", "hitstop"]),
        ("Timing", ["startup", "active", "active2", "speed_mod"]),
        ("Stun and pressure", ["hitstun", "invuln", "blockstun", "attack_property", "hit_reaction", "hit_result_flags"]),
        ("Launch and knockback controls", ["kb_type", "launch_profile", "kb_unknown", "kb_x", "air_kb"]),
        ("Hit FX and reach", ["hit_spark", "stretch_part", "stretch_len", "stretch_width", "stretch_height", "stretch_time"]),
        ("Dangerous script links", ["post_link"]),
        ("Flags and lookup", ["superbg", "kind", "abs"]),
        ("Super dispatch", ["dispatch_group", "dispatch_selector", "dispatch_variant", "dispatch_phase", "dispatch_child_link", "dispatch_child_target"]),
        ("Projectile emitter", ["proj_emit_count", "damage", "kb_x", "air_kb", "proj_ps_lifetime", "proj_ps_hit_count", "proj_ps_emit_count", "proj_ps_interval", "proj_radius", "proj_speed", "proj_accel", "proj_spawn_origin", "proj_ps_scale", "proj_ps_particle_fx", "proj_ps_projectile_id", "proj_ps_spawn_bone"]),
        ("Projectile data", ["proj_fmt", "proj_id", "proj_type", "proj_radius", "proj_fx", "proj_life", "proj_spawn_origin", "proj_speed", "proj_accel", "proj_kb_y", "proj_hitbox", "proj_arc", "proj_arc2"]),
        ("Projectile super", ["proj_ps_card_type", "proj_ps_lifetime", "proj_ps_hit_count", "proj_ps_mode", "proj_ps_emit_count", "proj_ps_interval", "proj_ps_offset_x", "proj_ps_offset_y", "proj_ps_scale", "proj_ps_particle_fx", "proj_ps_projectile_id", "proj_ps_spawn_bone"]),
        ("Super beam", ["proj_super_lifetime", "proj_super_hit_count", "proj_super_hit_interval", "proj_super_particle_fx", "proj_super_spawn_bone", "proj_super_hit_source", "proj_super_beam_scale", "proj_super_beam_width", "proj_super_beam_speed", "proj_super_beam_force", "proj_super_hit_radius", "proj_super_beam_visual"]),
        ("Final hit", ["proj_final_damage", "proj_final_lifetime", "proj_final_particle_fx", "proj_final_spawn_bone"]),
        ("Projectile super probes", ["proj_super_hit_react", "proj_super_life", "proj_super_speed_2", "proj_super_accel_b", "proj_super_accel_c", "proj_multihit_cap"]),
    ]

    click_help = {
        "abs": "Click to copy this address.",
        "superbg": "Click to toggle or edit this flag.",
        "kind": "Informational only.",
        "link": "Display-only family/section link.",
        "context": "Display-only compact details summary.",
        "proj_fmt": "Display-only projectile record format.",
        "proj_emit_count": "Display-only number of physical projectile cards in this emitter group.",
        "dispatch_group": "Display-only group of adjacent 00/23 dispatch rows.",
        "dispatch_child_target": "Display-only resolved child script target.",
        "invuln": "Display-only +0x1218 startup-phase signature.",
    }

    for section_title, fields in sections:
        card = ttk.Frame(inner, style="InspectorSection.TFrame", padding=(10, 8))
        card.pack(fill="x", pady=(0, 7))
        try:
            win._inspector_sections.append((section_title, card, tuple(fields)))
        except Exception:
            pass
        ttk.Label(card, text=section_title.upper(), style="InspectorSection.TLabel").pack(anchor="w", pady=(0, 4))
        ttk.Separator(card, orient="horizontal").pack(fill="x", pady=(0, 4))
        for _row_index, col in enumerate(fields):
            _alt = bool(_row_index % 2)
            _row_style = "InspectorDataRowAlt.TFrame" if _alt else "InspectorDataRow.TFrame"
            _label_style = "InspectorDataLabelAlt.TLabel" if _alt else "InspectorDataLabel.TLabel"
            row = ttk.Frame(card, style=_row_style, padding=(8, 5))
            row.pack(fill="x", pady=2)

            label = ttk.Label(row, text=FD_LABELS.get(col, col), style=_label_style, width=16, anchor="w")
            label.pack(side="left")

            var = tk.StringVar(master=win.root, value="-")
            win._inspector_value_vars[col] = var
            chip = _make_chip(row, col, var)

            field_help = get_field_help(col, "")
            tip_bits = []
            if field_help:
                tip_bits.append(field_help)
            if col in click_help:
                tip_bits.append(click_help[col])
            elif col != "kind":
                tip_bits.append("Click this value to edit it.")
            tip_text = "\n\n".join(tip_bits)
            if tip_text:
                Tooltip(label, field_help or tip_text)
                Tooltip(chip, tip_text)

    try:
        # One idle pass catches the first layout; the short delayed pass catches
        # Windows theme/font settling after Toplevel creation.
        win.root.after_idle(_schedule_scrollregion)
        win.root.after(80, _schedule_scrollregion)
    except Exception:
        pass

def build_tree_widget(win) -> ttk.Frame:
    body = ttk.Panedwindow(win.root, orient="horizontal")
    body.pack(fill="both", expand=True, padx=10, pady=(0, 8))
    win._workbench_pane = body

    left = ttk.Frame(body, style="FD.TFrame")
    right = ttk.Frame(body, style="Inspector.TFrame")
    body.add(left, weight=1)
    body.add(right, weight=0)

    # Give the inspector a real starting width. ttk's default sash math can
    # collapse the right pane on first open, which makes the value chips look
    # broken until the user drags it by hand.
    try:
        body.paneconfigure(right, minsize=420)
    except Exception:
        pass

    def _set_initial_sash():
        try:
            total_w = body.winfo_width()
            if total_w <= 1:
                body.after(40, _set_initial_sash)
                return
            saved = (getattr(win, "_ui_prefs", {}) or {}).get("sash_pos")
            if saved is not None:
                try:
                    left_w = max(780, min(int(saved), total_w - 420))
                except Exception:
                    left_w = max(780, total_w - 470)
            else:
                left_w = max(780, total_w - 470)
            body.sashpos(0, left_w)
        except Exception:
            pass

    body.after_idle(_set_initial_sash)

    quick = ttk.Frame(left, style="Card.TFrame", padding=(10, 8))
    quick.pack(fill="x", pady=(0, 8))
    win._quick_panel = quick
    win._quick_title_var = tk.StringVar(master=win.root, value="Selection details")
    win._quick_subtitle_var = tk.StringVar(master=win.root, value="Select a move, projectile, or super row to see the values that matter here.")
    top_line = ttk.Frame(quick, style="Card.TFrame")
    top_line.pack(fill="x")
    ttk.Label(top_line, textvariable=win._quick_title_var, style="Section.TLabel").pack(side="left", anchor="w")
    ttk.Label(quick, textvariable=win._quick_subtitle_var, style="CardMuted.TLabel", wraplength=950).pack(anchor="w", pady=(3, 4))
    win._quick_chips_frame = ttk.Frame(quick, style="Card.TFrame")
    win._quick_chips_frame.pack(fill="x")

    # The selection strip is deliberately a fixed reusable pool.  Recreating
    # 20-30 chip cards on every TreeviewSelect was more expensive than reading
    # the saved profile itself.  fd_window only changes these StringVars and
    # hides unused slots now.
    win._quick_chip_slots = []

    def _quick_slot_click(slot):
        col = slot.get("col") if isinstance(slot, dict) else None
        if col and hasattr(win, "_edit_selected_column"):
            try:
                win._edit_selected_column(col)
            except Exception:
                pass

    for _slot_index in range(30):
        # A slim color rail makes the strip read as stats rather than a grid of
        # form fields.  The styles are switched in-place by _refresh_quick_panel
        # so selection changes stay cache-only and do not rebuild widgets.
        _cell = ttk.Frame(win._quick_chips_frame, style="QuickTile.TFrame")
        _cell.grid(row=_slot_index // 6, column=_slot_index % 6, sticky="ew", padx=(0, 8), pady=(0, 6))
        _rail = tk.Frame(_cell, width=3, background="#4D78A3", highlightthickness=0, bd=0)
        _rail.pack(side="left", fill="y")
        _content = ttk.Frame(_cell, style="QuickTile.TFrame", padding=(8, 4, 8, 4))
        _content.pack(side="left", fill="both", expand=True)
        _line = ttk.Frame(_content, style="QuickTile.TFrame")
        _line.pack(fill="x")
        _label_var = tk.StringVar(master=win.root, value="")
        _value_var = tk.StringVar(master=win.root, value="")
        _lab = ttk.Label(_line, textvariable=_label_var, style="QuickTileLabel.TLabel", anchor="w")
        _lab.pack(side="left")
        _colon = ttk.Label(_line, text=": ", style="QuickInlineColon.TLabel", anchor="w")
        _colon.pack(side="left")
        _val = ttk.Label(_line, textvariable=_value_var, style="QuickTileValue.TLabel", anchor="w")
        _val.pack(side="left", fill="x", expand=True)
        _slot = {
            "cell": _cell,
            "rail": _rail,
            "content": _content,
            "label_var": _label_var,
            "value_var": _value_var,
            "label_widget": _lab,
            "value_widget": _val,
            "label_value": None,
            "text_value": None,
            "style_state": None,
            "visible": False,
            "col": None,
        }
        _cell.bind("<Button-1>", lambda _e, _slot=_slot: _quick_slot_click(_slot))
        _rail.bind("<Button-1>", lambda _e, _slot=_slot: _quick_slot_click(_slot))
        _content.bind("<Button-1>", lambda _e, _slot=_slot: _quick_slot_click(_slot))
        _lab.bind("<Button-1>", lambda _e, _slot=_slot: _quick_slot_click(_slot))
        _val.bind("<Button-1>", lambda _e, _slot=_slot: _quick_slot_click(_slot))
        _cell.grid_remove()
        win._quick_chip_slots.append(_slot)
    for _col_index in range(6):
        win._quick_chips_frame.grid_columnconfigure(_col_index, weight=1)
    win._quick_empty_label = ttk.Label(
        win._quick_chips_frame,
        text="No parsed values for this row yet.",
        style="CardMuted.TLabel",
    )

    frame = ttk.Frame(left, style="FD.TFrame")
    frame.pack(fill="both", expand=True)

    cols = FD_COLUMNS

    # Store per-column filter vars on the window so fd_window._apply_filter can read them.
    if not hasattr(win, "_col_filter_vars") or win._col_filter_vars is None:
        win._col_filter_vars = {}
    else:
        win._col_filter_vars.clear()

    win._col_filter_after_id = None

    def _schedule_apply_filters():
        try:
            if win._col_filter_after_id is not None:
                win.root.after_cancel(win._col_filter_after_id)
        except Exception:
            pass
        try:
            win._col_filter_after_id = win.root.after(80, win._apply_filter)
        except Exception:
            try:
                win._apply_filter()
            except Exception:
                pass

    filter_widths = {
        "move": 34,
        "kind": 10,
        "hits": 8,
        "link": 18,
        "context": 34,
        "damage": 8,
        "meter": 8,
        "startup": 8,
        "active": 10,
        "active2": 10,
        "hitstun": 8,
        "blockstun": 8,
        "hitstop": 8,
        "hit_spark": 10,
        "stretch_part": 10,
        "stretch_len": 10,
        "stretch_width": 10,
        "stretch_height": 10,
        "stretch_time": 10,
        "post_link": 12,
        "kb_type": 8,
        "launch_profile": 12,
        "kb_unknown": 12,
        "kb_x": 8,
        "air_kb": 8,
        "speed_mod": 10,
        "attack_property": 14,
        "hit_reaction": 16,
        "hit_result_flags": 14,
        "superbg": 10,
        "proj_cluster": 16,
        "proj_fmt": 12,
        "proj_id": 10,
        "proj_type": 10,
        "proj_radius": 10,
        "proj_fx": 10,
        "proj_life": 10,
        "proj_spawn_origin": 12,
        "proj_speed": 10,
        "proj_accel": 10,
        "proj_kb_y": 10,
        "proj_hitbox": 10,
        "proj_arc": 10,
        "proj_arc2": 10,
        "proj_super_hit_react": 12,
        "proj_super_life": 10,
        "proj_super_air_kb_y": 12,
        "proj_super_speed": 12,
        "proj_super_accel": 12,
        "proj_super_speed_2": 12,
        "proj_super_accel_b": 12,
        "proj_super_accel_c": 12,
        "proj_multihit_cap": 12,
        "proj_super_radius": 12,
        "proj_ps_card_type": 10,
        "proj_ps_lifetime": 10,
        "proj_ps_hit_count": 10,
        "proj_ps_mode": 10,
        "proj_ps_emit_count": 10,
        "proj_ps_interval": 10,
        "proj_ps_offset_x": 10,
        "proj_ps_offset_y": 10,
        "proj_ps_scale": 10,
        "proj_ps_particle_fx": 10,
        "proj_ps_projectile_id": 10,
        "proj_ps_spawn_bone": 10,
        "abs": 12,
    }

    filter_labels = {
        "move": "Move",
        "kind": "Kind",
        "hits": "Hits",
        "link": "Link",
        "context": "Details",
        "damage": "Dmg",
        "meter": "Meter",
        "startup": "Start",
        "active": "Active",
        "active2": "Active2",
        "hitstun": "HS",
        "blockstun": "BS",
        "hitstop": "Stop",
        "hit_spark": "Spark",
        "stretch_part": "Part",
        "stretch_len": "ReachLen",
        "stretch_width": "ReachW",
        "stretch_height": "ReachH",
        "stretch_time": "Timing",
        "post_link": "PostLink",
        "kb_type": "Type",
        "launch_profile": "Extra",
        "kb_unknown": "Adjust",
        "kb_x": "KB X",
        "air_kb": "Arc",
        "speed_mod": "Speed",
        "attack_property": "Property",
        "hit_reaction": "HitReact",
        "hit_result_flags": "HitResult",
        "superbg": "SuperBG",
        "proj_cluster": "ProjGroup",
        "proj_fmt": "ProjFmt",
        "proj_id": "ProjID",
        "proj_type": "ProjType",
        "proj_radius": "PRadius",
        "proj_fx": "PFX",
        "proj_life": "PLife",
        "proj_spawn_origin": "Origin",
        "proj_speed": "PSpeed",
        "proj_accel": "PAccel",
        "proj_kb_y": "PKBY",
        "proj_hitbox": "PHitbox",
        "proj_arc": "PArc",
        "proj_arc2": "PArc2",
        "proj_super_hit_react": "PReact",
        "proj_super_life": "SLife",
        "proj_super_air_kb_y": "SAirY",
        "proj_super_speed": "SSpeed",
        "proj_super_accel": "SAccel",
        "proj_super_speed_2": "SSpeed2",
        "proj_super_accel_b": "SAccelB",
        "proj_super_accel_c": "SAccelC",
        "proj_multihit_cap": "HitCap",
        "proj_super_radius": "SRadius",
        "proj_ps_card_type": "PSCard",
        "proj_ps_lifetime": "PSLife",
        "proj_ps_hit_count": "PSHits",
        "proj_ps_mode": "PSMode",
        "proj_ps_emit_count": "PSEmit",
        "proj_ps_interval": "PSInt",
        "proj_ps_offset_x": "PSX",
        "proj_ps_offset_y": "PSY",
        "proj_ps_scale": "PSScale",
        "proj_ps_particle_fx": "PSFX",
        "proj_ps_projectile_id": "PSID",
        "proj_ps_spawn_bone": "PSBone",
        "abs": "Abs",
    }

    def _clear_col_filters():
        for _c, _v in win._col_filter_vars.items():
            try:
                _v.set("")
            except Exception:
                pass
        _schedule_apply_filters()

    win._clear_col_filters = _clear_col_filters

    win._filter_panel = None
    win._filter_panel_visible = False
    win._filter_panel_built = False

    def _ensure_filter_panel_built():
        # Build the large advanced-filter widget set only when the user opens it.
        # The normal frame view no longer pays the startup cost for 50+ entries
        # and tooltips that may never be used.
        if getattr(win, "_filter_panel_built", False) and getattr(win, "_filter_panel", None) is not None:
            return win._filter_panel

        filter_panel = ttk.Frame(frame, style="Card.TFrame", padding=(8, 8))
        win._filter_panel = filter_panel

        ttk.Label(
            filter_panel,
            text="Advanced column filters. Multiple boxes combine together.",
            style="CardMuted.TLabel",
        ).grid(row=0, column=0, columnspan=len(cols) + 1, sticky="w", pady=(0, 6))

        labels_row = ttk.Frame(filter_panel, style="Card.TFrame")
        labels_row.grid(row=1, column=0, sticky="ew", padx=(0, 2), pady=(0, 1))

        filter_row = ttk.Frame(filter_panel, style="Card.TFrame")
        filter_row.grid(row=2, column=0, sticky="ew", padx=(0, 2), pady=(0, 2))

        for i, c in enumerate(cols):
            w = filter_widths.get(c, 10)
            labels_row.grid_columnconfigure(i, weight=0, minsize=w * 8)
            filter_row.grid_columnconfigure(i, weight=0, minsize=w * 8)

        labels_row.grid_columnconfigure(len(cols), weight=1)
        filter_row.grid_columnconfigure(len(cols), weight=1)

        for col_i, c in enumerate(cols):
            w = filter_widths.get(c, 10)
            label_txt = filter_labels.get(c, c)

            lbl = ttk.Label(labels_row, text=label_txt, width=w, anchor="w", style="FilterLabel.TLabel")
            lbl.grid(row=0, column=col_i, sticky="w", padx=1, pady=0)

            var = tk.StringVar(master=win.root)
            win._col_filter_vars[c] = var

            ent = ttk.Entry(filter_row, textvariable=var, width=w)
            ent.grid(row=0, column=col_i, sticky="w", padx=1, pady=0)

            field_help = get_field_help(c, "")
            tip_text = f"Filter: {label_txt}. Case-insensitive substring. Leave blank to ignore."
            if field_help:
                tip_text += f"\n\n{field_help}"
            Tooltip(lbl, tip_text)
            Tooltip(ent, tip_text)
            ent.bind("<Return>", lambda _e: _schedule_apply_filters())

            def _make_trace(_var=var):
                def _trace_cb(*_args):
                    _schedule_apply_filters()
                return _trace_cb

            var.trace_add("write", _make_trace())

        clear_btn = ttk.Button(labels_row, text="Clear column filters", command=win._clear_col_filters)
        clear_btn.grid(row=0, column=len(cols), sticky="e", padx=(8, 0))
        Tooltip(clear_btn, "Clear all per-column filters.")

        win._filter_panel_built = True
        return filter_panel

    win._ensure_filter_panel_built = _ensure_filter_panel_built

    tree_wrap = ttk.Frame(frame, style="Card.TFrame", padding=(1, 1))
    tree_wrap.pack(fill="both", expand=True)

    def _toggle_advanced_filters():
        visible = bool(getattr(win, "_filter_panel_visible", False))
        panel = getattr(win, "_filter_panel", None)
        if visible:
            if panel is not None:
                panel.pack_forget()
            win._filter_panel_visible = False
            if getattr(win, "_filter_panel_btn_var", None) is not None:
                win._filter_panel_btn_var.set("Filters")
        else:
            panel = _ensure_filter_panel_built()
            panel.pack(fill="x", padx=0, pady=(0, 8), before=tree_wrap)
            win._filter_panel_visible = True
            if getattr(win, "_filter_panel_btn_var", None) is not None:
                win._filter_panel_btn_var.set("Hide filters")

    win._toggle_advanced_filters = _toggle_advanced_filters

    # --- Tree ---
    win.tree = ttk.Treeview(tree_wrap, columns=cols, show="tree headings", height=30, selectmode="browse")

    vsb = ttk.Scrollbar(tree_wrap, orient="vertical", command=win.tree.yview)
    hsb = ttk.Scrollbar(tree_wrap, orient="horizontal", command=win.tree.xview)
    win.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

    win.tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")

    tree_wrap.rowconfigure(0, weight=1)
    tree_wrap.columnconfigure(0, weight=1)

    win.tree.heading("#0", text="")
    win.tree.column("#0", width=34, stretch=False, anchor="center")

    headers = [
        ("move", "Move"),
        ("kind", "Kind"),
        ("hits", "Hits"),
        ("link", "Link"),
        ("context", "Details"),
        ("damage", "Dmg"),
        ("meter", "Meter"),
        ("startup", "Start"),
        ("active", "Active"),
        ("active2", "Active 2"),
        ("hitstun", "HS"),
        ("blockstun", "BS"),
        ("hitstop", "Stop"),
        ("adv_block_derived", "Der B"),
        ("adv_block_observed", "Obs B"),
        ("hit_spark", "Hit Spark"),
        ("stretch_part", "Stretch Part"),
        ("stretch_len", "Reach Length"),
        ("stretch_width", "Reach Width"),
        ("stretch_height", "Reach Height"),
        ("stretch_time", "Stretch Timing"),
        ("post_link", "Post Link"),
        ("kb_type", "KB Style"),
        ("launch_profile", "Extra Launch"),
        ("kb_unknown", "Launch Adjust"),
        ("kb_x", "KB X"),
        ("air_kb", "Arc"),
        ("speed_mod", "Speed"),
        ("attack_property", "Attack Property"),
        ("hit_reaction", "Hit Reaction"),
        ("hit_result_flags", "Hit Result"),
        ("superbg", "SuperBG"),
        ("proj_cluster", "Proj Group"),
        ("proj_fmt", "Proj Fmt"),
        ("proj_id", "Proj ID"),
        ("proj_type", "Proj Type"),
        ("proj_radius", "Proj Radius"),
        ("proj_fx", "Projectile FX"),
        ("proj_life", "Proj Life"),
        ("proj_speed", "Proj Speed"),
        ("proj_accel", "Proj Accel"),
        ("proj_kb_y", "Proj KB Y"),
        ("proj_hitbox", "Proj Hitbox"),
        ("proj_arc", "Proj Arc"),
        ("proj_arc2", "Proj Arc 2"),
        ("proj_super_hit_react", "Proj HitReact"),
        ("proj_super_life", "Super Life"),
        ("proj_super_air_kb_y", "Super Air KB Y"),
        ("proj_super_speed", "Super Speed"),
        ("proj_super_accel", "Super Accel"),
        ("proj_super_speed_2", "Super Speed 2"),
        ("proj_super_accel_b", "Super Accel B"),
        ("proj_super_accel_c", "Super Accel C"),
        ("proj_multihit_cap", "Unknown D8"),
        ("proj_super_radius", "Hit Radius"),
        ("proj_ps_card_type", "Card Type"),
        ("proj_ps_lifetime", "Active Time"),
        ("proj_ps_hit_count", "Hits"),
        ("proj_ps_mode", "Mode"),
        ("proj_ps_emit_count", "Emit Limit"),
        ("proj_ps_interval", "Interval"),
        ("proj_ps_offset_x", "Spawn X"),
        ("proj_ps_offset_y", "Spawn Y"),
        ("proj_ps_scale", "Scale"),
        ("proj_ps_particle_fx", "FX"),
        ("proj_ps_projectile_id", "Proj ID"),
        ("proj_ps_spawn_bone", "Bone"),
        ("proj_super_lifetime", "Lifetime"),
        ("proj_super_hit_count", "Hit Count"),
        ("proj_super_hit_interval", "Hit Interval"),
        ("proj_super_particle_fx", "Particle FX"),
        ("proj_super_spawn_bone", "Spawn Bone"),
        ("proj_super_hit_source", "Hit Source"),
        ("proj_super_beam_scale", "Beam Scale"),
        ("proj_super_beam_width", "Beam Width"),
        ("proj_super_beam_speed", "Beam Speed"),
        ("proj_super_beam_force", "Beam Force"),
        ("proj_super_hit_radius", "Hit Radius"),
        ("proj_super_beam_visual", "Beam Visual"),
        ("proj_final_damage", "Final Damage"),
        ("proj_final_lifetime", "Final Lifetime"),
        ("proj_final_particle_fx", "Final FX"),
        ("proj_final_spawn_bone", "Final Bone"),
        ("dispatch_group", "Dispatch Group"),
        ("dispatch_selector", "Action Sel"),
        ("dispatch_variant", "Variant"),
        ("dispatch_phase", "Phase Len"),
        ("dispatch_child_link", "Child Link"),
        ("dispatch_child_target", "Child Target"),
        ("abs", "Address"),
    ]
    for c, txt in headers:
        win.tree.heading(c, text=txt, command=lambda col=c: win._on_sort_column(col))

    win.tree.column("move", width=320, anchor="w")
    win.tree.column("kind", width=80, anchor="w")
    win.tree.column("hits", width=62, anchor="center")
    win.tree.column("link", width=210, anchor="w")
    win.tree.column("context", width=320, anchor="w")
    win.tree.column("damage", width=76, anchor="center")
    win.tree.column("meter", width=70, anchor="center")
    win.tree.column("startup", width=70, anchor="center")
    win.tree.column("active", width=88, anchor="center")
    win.tree.column("active2", width=88, anchor="center")
    win.tree.column("hitstun", width=58, anchor="center")
    win.tree.column("invuln", width=78, anchor="center")
    win.tree.column("blockstun", width=58, anchor="center")
    win.tree.column("hitstop", width=58, anchor="center")
    win.tree.column("adv_block_derived", width=78, anchor="center")
    win.tree.column("adv_block_observed", width=78, anchor="center")
    win.tree.column("hit_spark", width=86, anchor="center")
    win.tree.column("stretch_part", width=92, anchor="center")
    win.tree.column("stretch_len", width=96, anchor="center")
    win.tree.column("stretch_width", width=96, anchor="center")
    win.tree.column("stretch_height", width=96, anchor="center")
    win.tree.column("stretch_time", width=100, anchor="center")
    win.tree.column("post_link", width=100, anchor="center")
    win.tree.column("kb_type", width=72, anchor="center")
    win.tree.column("launch_profile", width=82, anchor="center")
    win.tree.column("kb_unknown", width=92, anchor="center")
    win.tree.column("kb_x", width=72, anchor="center")
    win.tree.column("air_kb", width=72, anchor="center")
    win.tree.column("speed_mod", width=116, anchor="center")
    win.tree.column("attack_property", width=178, anchor="w")
    win.tree.column("hit_reaction", width=260, anchor="w")
    win.tree.column("hit_result_flags", width=128, anchor="w")
    win.tree.column("superbg", width=78, anchor="center")
    win.tree.column("proj_cluster", width=170, anchor="w")
    win.tree.column("proj_fmt", width=110, anchor="w")
    win.tree.column("proj_id", width=78, anchor="center")
    win.tree.column("proj_type", width=78, anchor="center")
    win.tree.column("proj_radius", width=96, anchor="center")
    win.tree.column("proj_fx", width=82, anchor="center")
    win.tree.column("proj_life", width=82, anchor="center")
    win.tree.column("proj_spawn_origin", width=92, anchor="center")
    win.tree.column("proj_speed", width=92, anchor="center")
    win.tree.column("proj_accel", width=92, anchor="center")
    win.tree.column("proj_kb_y", width=92, anchor="center")
    win.tree.column("proj_hitbox", width=92, anchor="center")
    win.tree.column("proj_arc", width=82, anchor="center")
    win.tree.column("proj_arc2", width=82, anchor="center")
    win.tree.column("proj_super_hit_react", width=110, anchor="center")
    win.tree.column("proj_super_life", width=92, anchor="center")
    win.tree.column("proj_super_air_kb_y", width=114, anchor="center")
    win.tree.column("proj_super_speed", width=106, anchor="center")
    win.tree.column("proj_super_accel", width=106, anchor="center")
    win.tree.column("proj_super_speed_2", width=112, anchor="center")
    win.tree.column("proj_super_accel_b", width=112, anchor="center")
    win.tree.column("proj_super_accel_c", width=112, anchor="center")
    win.tree.column("proj_multihit_cap", width=106, anchor="center")
    win.tree.column("proj_super_radius", width=106, anchor="center")
    win.tree.column("proj_ps_card_type", width=86, anchor="center")
    win.tree.column("proj_ps_lifetime", width=88, anchor="center")
    win.tree.column("proj_ps_hit_count", width=88, anchor="center")
    win.tree.column("proj_ps_mode", width=88, anchor="center")
    win.tree.column("proj_ps_emit_count", width=88, anchor="center")
    win.tree.column("proj_ps_interval", width=88, anchor="center")
    win.tree.column("proj_ps_offset_x", width=88, anchor="center")
    win.tree.column("proj_ps_offset_y", width=88, anchor="center")
    win.tree.column("proj_ps_scale", width=88, anchor="center")
    win.tree.column("proj_ps_particle_fx", width=88, anchor="center")
    win.tree.column("proj_ps_projectile_id", width=88, anchor="center")
    win.tree.column("proj_ps_spawn_bone", width=88, anchor="center")
    win.tree.column("proj_super_lifetime", width=92, anchor="center")
    win.tree.column("proj_super_hit_count", width=92, anchor="center")
    win.tree.column("proj_super_hit_interval", width=96, anchor="center")
    win.tree.column("proj_super_particle_fx", width=96, anchor="center")
    win.tree.column("proj_super_spawn_bone", width=96, anchor="center")
    win.tree.column("proj_super_hit_source", width=104, anchor="center")
    win.tree.column("proj_super_beam_scale", width=96, anchor="center")
    win.tree.column("proj_super_beam_width", width=96, anchor="center")
    win.tree.column("proj_super_beam_speed", width=96, anchor="center")
    win.tree.column("proj_super_beam_force", width=96, anchor="center")
    win.tree.column("proj_super_hit_radius", width=96, anchor="center")
    win.tree.column("proj_super_beam_visual", width=98, anchor="center")
    win.tree.column("proj_final_damage", width=98, anchor="center")
    win.tree.column("proj_final_lifetime", width=98, anchor="center")
    win.tree.column("proj_final_particle_fx", width=92, anchor="center")
    win.tree.column("proj_final_spawn_bone", width=92, anchor="center")
    win.tree.column("dispatch_group", width=130, anchor="center")
    win.tree.column("dispatch_selector", width=90, anchor="center")
    win.tree.column("dispatch_variant", width=82, anchor="center")
    win.tree.column("dispatch_phase", width=86, anchor="center")
    win.tree.column("dispatch_child_link", width=112, anchor="center")
    win.tree.column("dispatch_child_target", width=120, anchor="center")
    win.tree.column("abs", width=124, anchor="w")

    # Save the current built-in widths before applying persisted user choices.
    # Reset layout uses this for a true first-load restoration.
    try:
        win._fd_builtin_column_widths = {c: int(win.tree.column(c, "width")) for c in cols}
    except Exception:
        win._fd_builtin_column_widths = {}

    # Restore a user-resized table layout after default column definitions are
    # in place. Unknown/deleted columns are ignored safely.
    try:
        for _col, _width in ((getattr(win, "_ui_prefs", {}) or {}).get("column_widths") or {}).items():
            if _col in cols:
                win.tree.column(_col, width=max(32, int(_width)))
    except Exception:
        pass

    win._fd_all_columns = tuple(cols)
    win._fd_core_columns = tuple(FD_CORE_COLUMNS)
    win._fd_projectile_columns = tuple(c for c in FD_PROJECTILE_COLUMNS_FOCUSED if c in cols)
    win._fd_super_columns = tuple(c for c in FD_SUPER_COLUMNS_FOCUSED if c in cols)
    win._fd_view_presets = {
        "frame": win._fd_core_columns,
        "projectile": win._fd_projectile_columns,
        "super": win._fd_super_columns,
        "all": win._fd_all_columns,
    }
    win._fd_view_mode = "all"

    def _set_fd_view_mode(mode="all"):
        mode = str(mode or "all").lower()
        if mode not in getattr(win, "_fd_view_presets", {}):
            mode = "all"
        columns = tuple(c for c in win._fd_view_presets.get(mode, win._fd_all_columns) if c in cols)
        if not columns:
            columns = win._fd_core_columns
            mode = "frame"
        win.tree.configure(displaycolumns=columns)
        win._fd_view_mode = mode
        try:
            win._ui_prefs["view_mode"] = mode
        except Exception:
            pass
        label_map = {
            "frame": "View: Frame",
            "projectile": "View: Projectiles",
            "super": "View: Supers",
            "all": "View: All",
        }
        if getattr(win, "_fd_view_var", None) is not None:
            win._fd_view_var.set(label_map.get(mode, "View: Frame"))
        messages = {
            "frame": "Frame view: normal frame-data columns are prioritized.",
            "projectile": "Projectile view: projectile damage, ID/type, speed, life, hitbox, and probe fields are moved next to the move name.",
            "super": "Super view: 00/23 dispatch rows, child links, beam cards, and projectile-super payloads are moved into the readable left side.",
            "all": "All columns visible for raw scouting data.",
        }
        try:
            win.tree.xview_moveto(0.0)
        except Exception:
            pass
        try:
            win._status_var.set(messages.get(mode, messages["frame"]))
        except Exception:
            pass

        # Views are display-only. Projectile/special discovery is an explicit
        # saved profile pass from the Character profile toolbar; do not start a
        # scanner here just because the user changed columns.

    def _toggle_core_columns():
        # Legacy shortcut kept for old callers: Frame <-> All.
        if getattr(win, "_fd_view_mode", "frame") == "all":
            _set_fd_view_mode("frame")
        else:
            _set_fd_view_mode("all")

    win._set_fd_view_mode = _set_fd_view_mode
    win._toggle_core_columns = _toggle_core_columns

    def _set_table_density(density="standard"):
        density = str(density or "standard").lower()
        presets = {
            "compact": (20, ("Segoe UI", 8)),
            "standard": (24, ("Segoe UI", 9)),
            "detailed": (29, ("Segoe UI", 10)),
        }
        if density not in presets:
            density = "standard"
        rowheight, font = presets[density]
        try:
            ttk.Style(win.root).configure("Treeview", rowheight=rowheight, font=font)
        except Exception:
            pass
        win._table_density = density
        try:
            win._ui_prefs["density"] = density
        except Exception:
            pass
        try:
            button = getattr(win, "_density_button", None)
            if button is not None:
                button.configure(text=f"Density: {density.title()}")
        except Exception:
            pass
        try:
            win._status_var.set(f"Table density: {density}")
        except Exception:
            pass

    win._set_table_density = _set_table_density
    _set_table_density((getattr(win, "_ui_prefs", {}) or {}).get("density", "standard"))
    _set_fd_view_mode((getattr(win, "_ui_prefs", {}) or {}).get("view_mode", "all"))

    def _update_hover_help(event):
        try:
            region = win.tree.identify_region(event.x, event.y)
            if region not in ("cell", "heading"):
                win._hover_help_key = None
                return
            column = win.tree.identify_column(event.x)
            if not column or column == "#0":
                win._hover_help_key = None
                return
            hover_key = (region, column)
            if getattr(win, "_hover_help_key", None) == hover_key:
                return
            win._hover_help_key = hover_key
            col_idx = int(column[1:]) - 1
            display_cols = _display_columns(win.tree)
            if col_idx < 0 or col_idx >= len(display_cols):
                return
            col_name = display_cols[col_idx]
            label = filter_labels.get(col_name, col_name)
            help_text = get_field_help(col_name, "")
            if help_text and getattr(win, "_status_var", None) is not None:
                win._status_var.set(f"{label}: {help_text}")
        except Exception:
            pass

    win.tree.bind("<Motion>", _update_hover_help, add=True)

    win.tree.tag_configure("row_even", background="#132034")
    win.tree.tag_configure("row_odd", background="#101A2C")

    # Section/header colors are deliberately brighter than normal rows. Tk's
    # Treeview does not support a true per-row gradient, so these use a
    # gradient-style stepped palette by bucket: normal -> special -> super.
    win.tree.tag_configure("family_header", background="#203554", foreground="#EAF4FF", font=("Segoe UI Semibold", 9))
    win.tree.tag_configure("family_header_normal", background="#243E61", foreground="#ECF7FF")
    win.tree.tag_configure("family_header_special", background="#28517B", foreground="#F1FAFF")
    win.tree.tag_configure("family_header_super", background="#315F91", foreground="#FFFFFF")
    win.tree.tag_configure("family_header_other", background="#203554", foreground="#DCEBFF")
    win.tree.tag_configure("projectile_header", background="#2B5C88", foreground="#F1FCFF", font=("Segoe UI Semibold", 9))
    win.tree.tag_configure("super_header", background="#3B4D8A", foreground="#F5F7FF", font=("Segoe UI Semibold", 9))

    # Child rows should read as belonging to their parent, while still keeping
    # enough contrast to edit individual records.
    win.tree.tag_configure("child_row", foreground="#D8EAFF")
    win.tree.tag_configure("grandchild_row", foreground="#BFD8F5")
    win.tree.tag_configure("special_row", background="#13253D", foreground="#D8EFFF")
    win.tree.tag_configure("super_row", background="#1B2E4C", foreground="#EFF7FF")
    win.tree.tag_configure("projectile_row", background="#163247", foreground="#D9F6FF")
    win.tree.tag_configure("super_row", background="#1F2D55", foreground="#E7ECFF")

    win.tree.tag_configure("kb_hot", foreground="#9FCCFF")
    win.tree.tag_configure("combo_hot", foreground="#B7D6FF")
    win.tree.tag_configure("property_hot", foreground="#D6C8FF")
    win.tree.tag_configure("super_on", foreground="#82E0B1")
    win.tree.tag_configure("missing_addr", foreground="#FF9A9A")
    win.tree.tag_configure("group_parent", foreground="#FFE3A3")
    win.tree.tag_configure("family_linked", foreground="#C9E2FF")
    win.tree.tag_configure("edited_row", background="#2A3E5D")

    _build_inspector(win, right)

    def _capture_initial_layout_snapshot():
        # Snapshot what *this* instance looked like when it opened, after saved
        # preferences and the initial sash have settled. Reset layout restores
        # this UI state without touching frame-data edits.
        try:
            pane = getattr(win, "_workbench_pane", None)
            sash = int(pane.sashpos(0)) if pane is not None else None
        except Exception:
            sash = None
        try:
            widths = {c: int(win.tree.column(c, "width")) for c in win.tree["columns"]}
        except Exception:
            widths = dict(getattr(win, "_fd_builtin_column_widths", {}) or {})
        win._initial_layout_snapshot = {
            "view_mode": str(getattr(win, "_fd_view_mode", "frame") or "frame"),
            "density": str(getattr(win, "_table_density", "standard") or "standard"),
            "column_widths": widths,
            "sash_pos": sash,
        }

    try:
        win.root.after(180, _capture_initial_layout_snapshot)
    except Exception:
        _capture_initial_layout_snapshot()

    return body


def _compact_row_context(mv: dict | None, attack_property_txt: str = "", hr_txt: str = "", invuln_txt: str = "") -> str:
    """Small left-side summary for rows whose useful fields would otherwise
    be far off-screen. This is display-only; edits still target the real columns.
    """
    if not isinstance(mv, dict):
        return ""
    try:
        if FSI.is_super_row(mv):
            return FSI.super_context_summary(mv)
        if FPI.is_projectile_row(mv):
            return FPI.projectile_quick_summary(mv)
    except Exception:
        return ""

    bits = []
    kind = str(mv.get("kind") or "").lower()
    if kind in {"super", "hyper"}:
        if mv.get("superbg_val") is not None:
            bits.append(f"SuperBG {U.fmt_superbg(mv.get('superbg_val'))}")
        if invuln_txt:
            bits.append(f"Invuln {invuln_txt}")
        if attack_property_txt:
            bits.append(attack_property_txt)
        if hr_txt:
            bits.append(hr_txt)
    elif invuln_txt:
        bits.append(f"Invuln {invuln_txt}")
    return " | ".join([b for b in bits if b])

def _populate_tree_sync(win) -> None:
    cname = win.target_slot.get("char_name", "-")
    try:
        annotate_move_families(win.moves, cname)
    except Exception:
        pass

    win.tree.delete(*win.tree.get_children())
    win._row_counter = 0
    win._all_item_ids = []
    win.move_to_tree_item = {}
    win.original_moves = {}

    def _fmt(v):
        return "" if v is None else str(v)

    def _fmt_adv(v):
        if v is None or v == "":
            return ""
        try:
            return f"{int(v):+d}"
        except Exception:
            return str(v)

    # Opening the workbench used to issue several separate Dolphin reads per
    # row while resolving optional fields such as speed, SuperBG, hit spark,
    # stretch, and post-link.  Cache reads for the duration of this population
    # pass so each move block is normally read once at the largest requested
    # size, then sliced for the smaller scanners.  Refresh/rebuild still gets
    # fresh memory because this cache is intentionally local to populate_tree().
    _fd_read_cache: dict[int, bytes] = {}
    _fd_rbytes_func = None

    def _fd_cached_rbytes(addr: int, size: int) -> bytes:
        nonlocal _fd_rbytes_func
        if _fd_rbytes_func is None:
            from tvcgui.platform.dolphin import rbytes as _real_rbytes
            _fd_rbytes_func = _real_rbytes
        try:
            addr_i = int(addr or 0)
            size_i = max(0, int(size or 0))
        except Exception:
            return b""
        cached = _fd_read_cache.get(addr_i)
        if cached is not None and len(cached) >= size_i:
            return cached[:size_i]
        data = _fd_rbytes_func(addr_i, size_i) or b""
        if cached is None or len(data) >= len(cached):
            _fd_read_cache[addr_i] = data
        return data

    # Fast open path: the normal scanner already provides damage/startup/active/
    # stun/KB.  These extra probe finders are expensive because they read and
    # pattern-scan around every move.  Leave them off during initial populate;
    # Refresh visible or a direct edit can resolve them lazily.
    deep_probe = bool(getattr(win, "_fd_eager_deep_probe", False))

    def _hit_count_text(mv):
        if mv.get("_hit_segment_index") is not None:
            try:
                return f"Hit {int(mv.get('_hit_segment_index'))}"
            except Exception:
                return "Hit"
        count = mv.get("multi_hit_count") or len(mv.get("hit_segments") or [])
        try:
            count = int(count or 0)
        except Exception:
            count = 0
        return f"{count} hits" if count > 1 else ("1" if count == 1 else "")

    def _segment_to_row(parent_mv, seg):
        child = dict(parent_mv)
        child.update(seg or {})
        idx = int((seg or {}).get("hit_index") or 1)
        child["kind"] = "hit"
        child["_hit_segment_index"] = idx
        child["_hit_parent_abs"] = parent_mv.get("abs")
        child["_hit_parent_label"] = parent_mv.get("pretty_name") or parent_mv.get("move_name")
        child["_dirty_key_addr"] = child.get("active_addr") or child.get("damage_addr") or child.get("abs")
        if child.get("family_link_label"):
            child["family_link_label"] = f"{child.get('family_link_label')} / Hit {idx}"
        child["id"] = parent_mv.get("id")
        child["move_name"] = parent_mv.get("move_name")
        child["pretty_name"] = parent_mv.get("pretty_name")
        child["hit_segments"] = []
        child["multi_hit_count"] = 0
        # Hit rows are for per-hit data only. Keep whole-move-only fields off
        # the child so users do not accidentally edit meter/speed/super flags
        # from a segment row.
        for key in (
            "meter", "meter_addr", "active2_start", "active2_end", "active2_addr",
            "speed_mod", "speed_mod_addr", "speed_mod_sig",
            "superbg_val", "superbg_addr",
        ):
            child[key] = None
        return child

    def _insert_hit_children(parent_item, mv):
        segments = mv.get("hit_segments") or []
        if not isinstance(segments, list) or len(segments) <= 1:
            return
        for seg in segments:
            insert_move_row(_segment_to_row(mv, seg), parent=parent_item)
        try:
            win.tree.item(parent_item, open=True)
            tags = set(win.tree.item(parent_item, "tags") or ())
            tags.add("group_parent")
            win.tree.item(parent_item, tags=tuple(tags))
        except Exception:
            pass

    def insert_move_row(mv, parent=""):
        aid = mv.get("id")
        move_abs = mv.get("abs")

        pretty = U.pretty_move_name(aid, cname)
        mv["pretty_name"] = pretty

        if mv.get("_hit_segment_index") is not None:
            try:
                idx = int(mv.get("_hit_segment_index"))
            except Exception:
                idx = 1
            parent_label = mv.get("_hit_parent_label") or pretty
            pretty = f"{parent_label} Hit {idx}"
        elif aid is not None:
            dup = mv.get("dup_index")
            if dup is not None:
                pretty = f"{pretty} (Tier{dup + 1})"
            pretty = f"{pretty} [0x{aid:04X}]"

        # -------------------------
        # Resolve optional fields (SAFE)
        # -------------------------

        # -------------------------
        # Display formatting (ALWAYS RUNS)
        # -------------------------

        a_s = mv.get("active_start")
        a_e = mv.get("active_end")
        startup_txt = _fmt(a_s)
        active_txt = f"{a_s}-{a_e}" if a_s is not None and a_e is not None else ""

        a2_s = mv.get("active2_start")
        a2_e = mv.get("active2_end")
        if a2_s is not None and a2_e is not None:
            active2_txt = f"{a2_s}-{a2_e}"
        else:
            active2_txt = _fmt(a2_s or a2_e)

        kb_type_txt = U.fmt_kb_type_ui(mv)
        launch_profile_txt = U.fmt_launch_profile_ui(mv)
        kb_unknown_txt = U.fmt_kb_unknown_ui(mv)
        kb_x_txt = U.fmt_kb_x_ui(mv)
        air_kb_txt = U.fmt_air_kb_ui(mv)
        hitstop_txt = U.fmt_stun(mv.get("hitstop"))
        adv_block_derived = mv.get("adv_block_derived")
        if adv_block_derived is None:
            adv_block_derived = mv.get("adv_block")
        adv_block_observed = mv.get("adv_block_observed")
        if adv_block_observed is None:
            adv_block_observed = mv.get("observed_adv_block")
        adv_block_derived_txt = _fmt_adv(adv_block_derived)
        adv_block_observed_txt = _fmt_adv(adv_block_observed)

        if move_abs and deep_probe:
            try:
                rbytes = _fd_cached_rbytes
                if mv.get("hit_spark_addr") is None:
                    _sp_pkt, _sp_addr, _sp_val, _sp_ctx = find_hit_spark_addr(move_abs, rbytes)
                    if _sp_addr:
                        mv["hit_spark_packet_addr"] = _sp_pkt
                        mv["hit_spark_addr"] = _sp_addr
                        mv["hit_spark"] = _sp_val
                        mv["hit_spark_sig"] = _sp_ctx
                if mv.get("stretch_packet_addr") is None:
                    _stretch = find_limb_stretch_packet(move_abs, rbytes)
                    if _stretch:
                        mv["stretch_packet_addr"] = _stretch.get("packet_addr")
                        mv["stretch_part_addr"] = _stretch.get("part_addr")
                        mv["stretch_len_addr"] = _stretch.get("scale1_addr")
                        mv["stretch_width_addr"] = _stretch.get("scale2_addr")
                        mv["stretch_height_addr"] = _stretch.get("scale3_addr")
                        mv["stretch_time_addr"] = _stretch.get("timing_addr")
                        mv["stretch_part"] = _stretch.get("part")
                        mv["stretch_len"] = _stretch.get("scale1")
                        mv["stretch_width"] = _stretch.get("scale2")
                        mv["stretch_height"] = _stretch.get("scale3")
                        mv["stretch_time"] = _stretch.get("timing")
                        mv["stretch_sig"] = _stretch.get("context")
                if mv.get("post_link_addr") is None:
                    _pl_pkt, _pl_addr, _pl_val, _pl_ctx = find_post_animation_link_addr(move_abs, rbytes)
                    if _pl_addr:
                        mv["post_link_packet_addr"] = _pl_pkt
                        mv["post_link_addr"] = _pl_addr
                        mv["post_link"] = _pl_val
                        mv["post_link_sig"] = _pl_ctx
            except Exception:
                pass

        hit_spark_txt = U.fmt_hit_spark_ui(mv)
        stretch_part_txt = U.fmt_stretch_part_ui(mv)
        stretch_len_txt = U.fmt_stretch_len_ui(mv)
        stretch_width_txt = U.fmt_stretch_width_ui(mv)
        stretch_height_txt = U.fmt_stretch_height_ui(mv)
        stretch_time_txt = U.fmt_stretch_time_ui(mv)
        post_link_txt = U.fmt_post_link_ui(mv)

        speed_txt = ""
        if move_abs and deep_probe:
            if mv.get("speed_mod_addr") is None:
                try:
                    rbytes = _fd_cached_rbytes
                    saddr, sval, _ = find_speed_mod_addr(move_abs, rbytes)
                    if saddr:
                        mv["speed_mod_addr"] = saddr
                        mv["speed_mod"] = sval
                except Exception:
                    pass
        if mv.get("speed_mod_addr"):
            speed_txt = U.fmt_speed_mod_ui(mv.get("speed_mod"))

        invuln_txt = str(mv.get("invuln") or "")

        attack_property_txt = ""
        if move_abs and deep_probe:
            if mv.get("attack_property_addr") is None:
                try:
                    rbytes = _fd_cached_rbytes
                    ap_addr, ap_val, _ = find_attack_property_addr(move_abs, rbytes)
                    if ap_addr:
                        mv["attack_property_addr"] = ap_addr
                        mv["attack_property"] = ap_val
                except Exception:
                    pass
        if mv.get("attack_property_addr"):
            attack_property_txt = fmt_attack_property(mv.get("attack_property"))

        superbg_txt = ""
        if move_abs and deep_probe:
            if mv.get("superbg_addr") is None:
                try:
                    from tvcgui.platform.dolphin import rd8
                    rbytes = _fd_cached_rbytes
                    saddr, sval = find_superbg_addr(move_abs, rbytes, rd8)
                    if saddr:
                        mv["superbg_addr"] = saddr
                        mv["superbg_val"] = sval
                except Exception:
                    pass
        if mv.get("superbg_addr"):
            superbg_txt = U.fmt_superbg(mv.get("superbg_val"))

        hr_txt = U.fmt_hit_reaction(mv.get("hit_reaction"))

        # Hit-result / OTG probing is another 0x900-byte pattern scan.  It was
        # accidentally left outside the deep-probe guard, so opening a profile
        # with ~1,400 rows could issue ~1,400 Dolphin reads before the window
        # painted.  Keep first-open cache-only.  The selected row / Refresh
        # path resolves this lazily in the background, and direct editing also
        # resolves it on demand.
        if move_abs and deep_probe and mv.get("hit_result_addr") is None:
            try:
                rbytes = _fd_cached_rbytes
                _pkt, _addr, _val, _mask, _ctx = find_hit_result_flags_addr(move_abs, rbytes)
                if _addr is not None:
                    mv["hit_result_packet_addr"] = _pkt
                    mv["hit_result_addr"] = _addr
                    mv["hit_result_flags"] = _val
                    mv["hit_result_clear_mask"] = _mask
                    mv["hit_result_sig"] = _ctx
            except Exception:
                pass
        hit_result_txt = U.fmt_hit_result_flags_ui(mv)

        # -------------------------
        # Insert row
        # -------------------------

        row_tag = "row_even" if (win._row_counter % 2 == 0) else "row_odd"
        win._row_counter += 1

        display_pretty = _decorate_move_text(win.tree, parent, pretty, mv)
        row_tags = [row_tag]
        if parent:
            row_tags.append("child_row")
            try:
                if _tree_depth(win.tree, parent) >= 1:
                    row_tags.append("grandchild_row")
            except Exception:
                pass
        bucket = _rank_bucket(win, mv)
        if bucket == 1:
            row_tags.append("special_row")
        elif bucket == 2:
            row_tags.append("super_row")

        item_id = win.tree.insert(
            parent,
            "end",
            text="",
            tags=tuple(row_tags),
            values=(
                display_pretty,
                mv.get("kind", ""),
                _hit_count_text(mv),
                _fmt(mv.get("family_link_label") or mv.get("link_label")),
                _fmt(_compact_row_context(mv, attack_property_txt=attack_property_txt, hr_txt=hr_txt, invuln_txt=invuln_txt)),
                _fmt(mv.get("damage")),
                _fmt(mv.get("meter")),
                startup_txt,
                active_txt,
                active2_txt,
                U.fmt_stun(mv.get("hitstun")),
                invuln_txt,
                U.fmt_stun(mv.get("blockstun")),
                hitstop_txt,
                adv_block_derived_txt,
                adv_block_observed_txt,
                hit_spark_txt,
                stretch_part_txt,
                stretch_len_txt,
                stretch_width_txt,
                stretch_height_txt,
                stretch_time_txt,
                post_link_txt,
                kb_type_txt,
                launch_profile_txt,
                kb_unknown_txt,
                kb_x_txt,
                air_kb_txt,
                speed_txt,
                attack_property_txt,
                hr_txt,
                hit_result_txt,
                superbg_txt,
                *("" for _ in FPI.PROJECTILE_COLUMNS),
                # FD_COLUMNS also contains the super-dispatch block between
                # projectile fields and abs. Leaving this padding out shifted
                # every normal row's Address into Dispatch Group, so the actual
                # Address column appeared empty in the All table view.
                *("" for _ in FSI.SUPER_DISPATCH_COLUMNS),
                f"0x{move_abs:08X}" if move_abs else "",
            ),
        )

        win.move_to_tree_item[item_id] = mv
        win._all_item_ids.append(item_id)
        win._apply_row_tags(item_id, mv)
        if mv.get("family_linkable"):
            try:
                tags = set(win.tree.item(item_id, "tags") or ())
                tags.add("family_linked")
                win.tree.item(item_id, tags=tuple(tags))
            except Exception:
                pass

        return item_id

    # -------------------------
    # GROUPING (stable, no skips)
    # -------------------------

    def _move_quality(mv):
        score = 0
        if mv.get("damage") not in (None, "", 0):
            score += 100
        if mv.get("active_start") is not None and mv.get("active_end") is not None:
            score += 80
        if mv.get("hitstun") is not None:
            score += 40
        if mv.get("blockstun") is not None:
            score += 40
        if mv.get("knockback_addr") is not None:
            score += 25
        if mv.get("kind") == "normal":
            score += 10
        return score

    def _insert_family_header(label, mv_list):
        vals = {c: "" for c in FD_COLUMNS}
        vals["move"] = label
        vals["kind"] = "linked"
        vals["hits"] = ""
        vals["link"] = f"{len(mv_list)} related sections"
        addrs = []
        for _mv in mv_list:
            try:
                if _mv.get("abs"):
                    addrs.append(int(_mv.get("abs")))
            except Exception:
                pass
        vals["abs"] = f"0x{min(addrs):08X}" if addrs else ""
        item_id = win.tree.insert(
            "",
            "end",
            text="",
            tags=_header_tags_for_members(win, mv_list),
            values=tuple(vals.get(c, "") for c in FD_COLUMNS),
        )
        win._all_item_ids.append(item_id)
        return item_id

    def _insert_id_groups(moves_for_group, parent=""):
        groups = {}
        order = []

        for mv in moves_for_group:
            aid = mv.get("id")
            if aid not in groups:
                groups[aid] = []
                order.append(aid)
            groups[aid].append(mv)

        for aid in order:
            mv_list = groups[aid]

            if len(mv_list) == 1:
                parent_item = insert_move_row(mv_list[0], parent=parent)
                _insert_hit_children(parent_item, mv_list[0])
                continue

            mv_list = sorted(
                mv_list,
                key=lambda mv: (
                    -_move_quality(mv),
                    0 if mv.get("kind") == "normal" else 1,
                    mv.get("abs") or 0xFFFFFFFF,
                ),
            )

            parent_item = insert_move_row(mv_list[0], parent=parent)
            _insert_hit_children(parent_item, mv_list[0])
            win.tree.item(parent_item, open=bool((mv_list[0].get("hit_segments") or [])[1:]))
            try:
                tags = set(win.tree.item(parent_item, "tags") or ())
                tags.add("group_parent")
                win.tree.item(parent_item, tags=tuple(tags))
            except Exception:
                win.tree.item(parent_item, tags=("group_parent",))

            for mv in mv_list[1:]:
                child_item = insert_move_row(mv, parent=parent_item)
                _insert_hit_children(child_item, mv)

    def _display_rank_for_mv(mv):
        try:
            ranker = getattr(win, "_explicit_notation", None)
            if callable(ranker):
                return ranker(mv)
        except Exception:
            pass
        try:
            return (1, int(mv.get("abs") or 0xFFFFFFFF))
        except Exception:
            return (1, 0xFFFFFFFF)

    def _phase_rank(mv):
        phase = str(mv.get("family_phase") or "").lower()
        return {"start": 0, "spin": 1, "end": 2, "entry": 3, "air entry": 3}.get(phase, 9)

    def _family_member_rank(mv):
        return (
            int(mv.get("family_chain_index") or 9999),
            _phase_rank(mv),
            mv.get("abs") or 0xFFFFFFFF,
        )

    # Family links are display-only.  They keep records such as Ryu's Tatsu
    # Start/Spin/End near each other without changing any write handlers.
    family_groups = {}
    family_order = []
    normal_moves = []

    for mv in win.moves:
        key = (mv.get("family_group_key") or mv.get("family_key")) if mv.get("family_linkable") else None
        if key:
            if key not in family_groups:
                family_groups[key] = []
                family_order.append(key)
            family_groups[key].append(mv)
        else:
            normal_moves.append(mv)

    def _family_sort_key(key):
        members = family_groups.get(key) or []
        try:
            group_ranker = getattr(win, "_family_group_sort_key", None)
            if callable(group_ranker):
                return group_ranker(members)
        except Exception:
            pass
        ranks = [_display_rank_for_mv(mv) for mv in members]
        return min(ranks) if ranks else (9, 0xFFFFFFFF)

    normal_groups = {}
    normal_order = []
    for mv in normal_moves:
        aid = mv.get("id")
        if aid not in normal_groups:
            normal_groups[aid] = []
            normal_order.append(aid)
        normal_groups[aid].append(mv)

    units = []
    for key in family_order:
        members = family_groups.get(key) or []
        if members:
            units.append(("family", key, _family_sort_key(key)))
    for aid in normal_order:
        members = normal_groups.get(aid) or []
        ranks = [_display_rank_for_mv(mv) for mv in members]
        units.append(("normal", aid, min(ranks) if ranks else (9, 0xFFFFFFFF)))

    for kind, key, _addr in sorted(units, key=lambda u: u[2]):
        if kind == "family":
            members = sorted(family_groups.get(key) or [], key=_family_member_rank)
            if not members:
                continue
            label = members[0].get("family_group_label") or members[0].get("family_label") or key
            suffix = "linked sections" if "linked" not in str(label).lower() else ""
            header_label = f"{label} {suffix}".strip()
            header = _insert_family_header(header_label, members)
            _insert_id_groups(members, parent=header)
            try:
                win.tree.item(header, open=True)
            except Exception:
                pass
        else:
            _insert_id_groups(normal_groups.get(key) or [], parent="")

    # Insert projectile records, when the asynchronous projectile scan has
    # already finished.  They belong after the core normal chain and before
    # specials/supers, so populate_projectile_rows() computes the correct root
    # insertion index instead of blindly appending. The workbench can also call
    # it later after the background projectile scan completes.
    try:
        populate_projectile_rows(win, replace=True)
        populate_super_rows(win, replace=True)
    except Exception:
        pass

    # Populate the inspector immediately so the window opens with a useful
    # selected-row view instead of an empty side panel.
    try:
        if win._all_item_ids:
            first = win._all_item_ids[0]
            win.tree.selection_set(first)
            win.tree.focus(first)
            win.tree.see(first)
            win._on_select()
        else:
            win._refresh_inspector(None, None)
    except Exception:
        pass


class _FrameTreePlan:
    """Small in-memory Treeview stand-in used to stage cached FD rows.

    The old population code does useful grouping/tag work, but calling the
    real Treeview for every row makes Tk spend a long uninterrupted time in
    Python/Tcl crossings.  This plan records the finished hierarchy first,
    then the real widget receives it in paint-friendly batches.
    """

    def __init__(self, columns):
        self._columns = tuple(columns or ())
        self._nodes: dict[str, dict] = {}
        self._roots: list[str] = []
        self._selection: tuple[str, ...] = ()
        self._focus = ""
        self._next_id = 0
        self._insert_order: list[str] = []

    def __getitem__(self, key):
        if key == "columns":
            return self._columns
        raise KeyError(key)

    def get_children(self, item=""):
        if not item:
            return tuple(self._roots)
        node = self._nodes.get(str(item))
        return tuple(node.get("children", ())) if node else ()

    def parent(self, item):
        node = self._nodes.get(str(item))
        return str(node.get("parent") or "") if node else ""

    def insert(self, parent, index, iid=None, **kwargs):
        parent = str(parent or "")
        if iid is None:
            self._next_id += 1
            iid = f"fd_stream_{self._next_id}"
        iid = str(iid)
        values = tuple(kwargs.get("values") or ())
        tags = tuple(kwargs.get("tags") or ())
        node = {
            "iid": iid,
            "parent": parent,
            "index": index,
            "text": kwargs.get("text", ""),
            "values": values,
            "tags": tags,
            "open": bool(kwargs.get("open", False)),
            "children": [],
        }
        self._nodes[iid] = node
        siblings = self._roots if not parent else self._nodes[parent]["children"]
        if index in ("end", "", None):
            siblings.append(iid)
        else:
            try:
                siblings.insert(max(0, int(index)), iid)
            except Exception:
                siblings.append(iid)
        self._insert_order.append(iid)
        return iid

    def delete(self, *items):
        for item in list(items):
            self._delete_one(str(item))

    def _delete_one(self, iid):
        node = self._nodes.get(iid)
        if not node:
            return
        for child in list(node.get("children") or ()):
            self._delete_one(child)
        parent = node.get("parent") or ""
        siblings = self._roots if not parent else self._nodes.get(parent, {}).get("children", [])
        try:
            siblings.remove(iid)
        except Exception:
            pass
        self._nodes.pop(iid, None)
        self._selection = tuple(x for x in self._selection if x != iid)

    def item(self, iid, option=None, **kwargs):
        node = self._nodes.get(str(iid))
        if node is None:
            return "" if option else {}
        for key, value in kwargs.items():
            if key == "tags":
                node["tags"] = tuple(value or ())
            elif key == "values":
                node["values"] = tuple(value or ())
            elif key == "open":
                node["open"] = bool(value)
            elif key == "text":
                node["text"] = value
        if option:
            return node.get(option, "")
        return {
            "text": node.get("text", ""),
            "values": node.get("values", ()),
            "tags": node.get("tags", ()),
            "open": node.get("open", False),
        }

    def set(self, iid, column, value=None):
        node = self._nodes.get(str(iid))
        if node is None:
            return ""
        try:
            idx = self._columns.index(column)
        except Exception:
            return ""
        values = list(node.get("values") or ())
        if len(values) <= idx:
            values.extend("" for _ in range(idx + 1 - len(values)))
        if value is None:
            return values[idx]
        values[idx] = value
        node["values"] = tuple(values)
        return ""

    def selection_set(self, items):
        if isinstance(items, (tuple, list)):
            self._selection = tuple(str(x) for x in items)
        else:
            self._selection = (str(items),)

    def selection(self):
        return self._selection

    def focus(self, item=None):
        if item is None:
            return self._focus
        self._focus = str(item)

    def see(self, _item):
        return None


def _build_stream_plan(win):
    """Reuse the mature synchronous grouping logic without touching Tk."""
    real_tree = win.tree
    plan = _FrameTreePlan(real_tree["columns"] if real_tree is not None else FD_COLUMNS)
    sentinel = object()
    old_on_select = win.__dict__.get("_on_select", sentinel)
    old_refresh = win.__dict__.get("_refresh_inspector", sentinel)
    # The legacy path ends by selecting a row and updating inspector controls.
    # It must not touch real widgets while the temporary plan is installed.
    win.tree = plan
    win._on_select = lambda *_a, **_k: None
    win._refresh_inspector = lambda *_a, **_k: None
    try:
        _populate_tree_sync(win)
    finally:
        win.tree = real_tree
        if old_on_select is sentinel:
            win.__dict__.pop("_on_select", None)
        else:
            win.__dict__["_on_select"] = old_on_select
        if old_refresh is sentinel:
            win.__dict__.pop("_refresh_inspector", None)
        else:
            win.__dict__["_refresh_inspector"] = old_refresh
    return plan


def _stream_tree_plan(win, plan: _FrameTreePlan, on_complete=None):
    """Insert a staged profile tree in short Tk batches instead of one freeze."""
    tree = getattr(win, "tree", None)
    root = getattr(win, "root", None)
    if tree is None or root is None:
        if callable(on_complete):
            on_complete(RuntimeError("Frame-data tree is unavailable"))
        return False

    # A new request invalidates any unfinished batch chain from a prior rebuild.
    generation = int(getattr(win, "_fd_stream_generation", 0) or 0) + 1
    win._fd_stream_generation = generation
    win._fd_stream_loading = True
    win._fd_stream_total = len(plan._nodes)
    win._fd_stream_inserted = 0

    try:
        roots = list(tree.get_children(""))
        if roots:
            tree.delete(*roots)
    except Exception:
        pass

    entries = [plan._nodes[iid] for iid in plan._insert_order if iid in plan._nodes]
    total = len(entries)
    cursor = 0

    # Keep a batch beneath a frame on ordinary machines.  The time cap protects
    # slower PCs while the count cap avoids scheduling thousands of tiny events.
    batch_limit = 60
    budget_seconds = 0.012

    def _finish(error=None):
        if int(getattr(win, "_fd_stream_generation", 0) or 0) != generation:
            return
        win._fd_stream_loading = False
        win._fd_stream_inserted = total
        try:
            if error is None:
                first = next((iid for iid in getattr(win, "_all_item_ids", []) if iid in getattr(win, "move_to_tree_item", {})), None)
                if first:
                    tree.selection_set(first)
                    tree.focus(first)
                    tree.see(first)
                    win._on_select()
                else:
                    win._refresh_inspector(None, None)
        except Exception:
            pass
        if callable(on_complete):
            try:
                on_complete(error)
            except Exception:
                pass

    def _batch():
        nonlocal cursor
        if int(getattr(win, "_fd_stream_generation", 0) or 0) != generation:
            return
        if not getattr(win, "root", None) or not getattr(win, "tree", None):
            return
        started = time.perf_counter()
        inserted_this_tick = 0
        try:
            while cursor < total and inserted_this_tick < batch_limit:
                node = entries[cursor]
                tree.insert(
                    node["parent"],
                    "end" if node["parent"] else node.get("index", "end"),
                    iid=node["iid"],
                    text=node.get("text", ""),
                    values=node.get("values", ()),
                    tags=node.get("tags", ()),
                    open=bool(node.get("open", False)),
                )
                cursor += 1
                inserted_this_tick += 1
                if (time.perf_counter() - started) >= budget_seconds:
                    break
            win._fd_stream_inserted = cursor
            if getattr(win, "_status_var", None) is not None:
                win._status_var.set(f"Loading saved profile rows… {cursor:,}/{total:,}")
        except Exception as exc:
            _finish(exc)
            return
        if cursor >= total:
            _finish(None)
        else:
            try:
                root.after(1, _batch)
            except Exception as exc:
                _finish(exc)

    try:
        root.after_idle(_batch)
    except Exception as exc:
        _finish(exc)
        return False
    return True


def populate_tree(win, *, stream: bool = False, on_complete=None):
    """Populate the grid.

    Normal rebuild callers keep the old synchronous behavior.  First-open
    profile loads opt into `stream=True`, which only consumes saved profile
    data and yields to Tk after small insertion batches.
    """
    if not stream:
        return _populate_tree_sync(win)
    try:
        plan = _build_stream_plan(win)
        return _stream_tree_plan(win, plan, on_complete=on_complete)
    except Exception as exc:
        if callable(on_complete):
            try:
                on_complete(exc)
            except Exception:
                pass
        return False


def _tree_rank_bucket(win, item_id: str):
    """Return the high-level notation bucket for a root Treeview item.

    Bucket layout is owned by EditableFrameDataWindow._explicit_notation:
    0 normals, 1 specials, 2 supers, 3 taunt, 4 everything else. Projectile
    rows are inserted between bucket 0 and bucket 1. Family headers do not map
    directly to a move dict, so rank them through their child move rows using
    the same family sort key used during the main population pass.
    """
    tree = getattr(win, "tree", None)
    if not tree:
        return None
    try:
        tags = set(tree.item(item_id, "tags") or ())
    except Exception:
        tags = set()
    if "projectile_header" in tags:
        return None

    ranker = getattr(win, "_explicit_notation", None)
    group_ranker = getattr(win, "_family_group_sort_key", None)

    def _bucket_from_rank(rank):
        try:
            return int(tuple(rank)[0])
        except Exception:
            return None

    mv = getattr(win, "move_to_tree_item", {}).get(item_id)
    if mv and callable(ranker):
        bucket = _bucket_from_rank(ranker(mv))
        if bucket is not None:
            return bucket

    child_mvs = []
    try:
        for child in tree.get_children(item_id):
            cmv = getattr(win, "move_to_tree_item", {}).get(child)
            if cmv:
                child_mvs.append(cmv)
    except Exception:
        child_mvs = []

    if child_mvs:
        if callable(group_ranker):
            bucket = _bucket_from_rank(group_ranker(child_mvs))
            if bucket is not None:
                return bucket
        if callable(ranker):
            buckets = [_bucket_from_rank(ranker(m)) for m in child_mvs]
            buckets = [b for b in buckets if b is not None]
            if buckets:
                return min(buckets)

    # Last fallback for unmapped headers: build a tiny fake row from visible
    # Treeview values and run it through the normal ranker.
    if callable(ranker):
        try:
            cols = list(tree["columns"] or [])
            vals = list(tree.item(item_id, "values") or [])
            row = {cols[i]: vals[i] for i in range(min(len(cols), len(vals)))}
            fake = {
                "move_name": row.get("move") or "",
                "pretty_name": row.get("move") or "",
                "kind": row.get("kind") or "",
            }
            bucket = _bucket_from_rank(ranker(fake))
            if bucket is not None:
                return bucket
        except Exception:
            pass
    return 4


def _projectile_root_insert_index(win):
    """Place projectile definitions after j.C/core normals, before specials."""
    tree = getattr(win, "tree", None)
    if not tree:
        return "end"
    try:
        children = list(tree.get_children(""))
    except Exception:
        return "end"

    for idx, item_id in enumerate(children):
        bucket = _tree_rank_bucket(win, item_id)
        if bucket is None:
            continue
        if bucket > 0:
            return idx
    return "end"


def populate_projectile_rows(win, replace: bool = True) -> None:
    """Insert current-character projectile records into the same FD Treeview."""
    if not getattr(win, "tree", None):
        return

    if replace:
        for iid in list(win.tree.get_children("")):
            try:
                tags = set(win.tree.item(iid, "tags") or ())
                if "projectile_header" in tags:
                    win.tree.delete(iid)
                    if iid in getattr(win, "_all_item_ids", []):
                        win._all_item_ids.remove(iid)
            except Exception:
                pass
        # Drop old projectile row map entries. They are child rows of the deleted
        # header, but clearing the stale dict entries keeps selection/right-click
        # routing honest after a rescan.
        try:
            win.move_to_tree_item = {
                iid: mv for iid, mv in (win.move_to_tree_item or {}).items()
                if not FPI.is_projectile_row(mv)
            }
        except Exception:
            pass

    hits = list(getattr(win, "_projectile_hits", []) or [])
    if not hits:
        return
    try:
        hits = FPI.with_projectile_emitters(hits)
    except Exception:
        pass

    vals = {c: "" for c in FD_COLUMNS}
    vals["move"] = "Projectile definitions"
    vals["kind"] = "projectile"
    vals["link"] = f"{len(hits)} scanned record(s)"
    addrs = []
    for h in hits:
        try:
            addrs.append(int(h.get("addr") or 0))
        except Exception:
            pass
    vals["abs"] = f"0x{min(a for a in addrs if a):08X}" if any(addrs) else ""
    header = win.tree.insert(
        "",
        _projectile_root_insert_index(win),
        text="",
        tags=("projectile_header", "family_header"),
        values=tuple(vals.get(c, "") for c in FD_COLUMNS),
    )
    try:
        win._all_item_ids.append(header)
    except Exception:
        pass

    def _proj_sort_key(h):
        move = str(h.get("move") or "")
        low = move.lower().strip()
        fmt = str(h.get("fmt") or "")
        try:
            addr = int(h.get("addr") or 0xFFFFFFFF)
        except Exception:
            addr = 0xFFFFFFFF

        unknown = 1 if (h.get("key") == "?" or low in {"unknown", "signature match", "super struct candidate"}) else 0
        if fmt == "projectile_emitter":
            fmt_rank = -1
            unknown = 0
        else:
            fmt_rank = 0 if fmt in ("template", "template2") else (1 if fmt.startswith("super") else 2)

        # Keep strength families readable: L/M/H or A/B/C should stay in that
        # order instead of alphabetically putting H before L/M. Prefer scanner
        # tier when available, then fall back to the move label.
        tier = str(h.get("tier") or "").strip()
        try:
            strength_rank = max(1, int(tier))
        except Exception:
            strength_rank = 9
            if re.search(r"(^|[ /_-])(l|light)([ /_-]|$)", low):
                strength_rank = 1
            elif re.search(r"(^|[ /_-])(m|medium)([ /_-]|$)", low):
                strength_rank = 2
            elif re.search(r"(^|[ /_-])(h|heavy)([ /_-]|$)", low):
                strength_rank = 3
            elif re.search(r"(^|[ /_-])(a)([ /_-]|$)", low):
                strength_rank = 1
            elif re.search(r"(^|[ /_-])(b)([ /_-]|$)", low):
                strength_rank = 2
            elif re.search(r"(^|[ /_-])(c)([ /_-]|$)", low):
                strength_rank = 3

        family = re.sub(r"\s*/\s*assist.*$", "", low)
        family = re.sub(r"(^|[ /_-])(l|m|h|a|b|c|light|medium|heavy)([ /_-]|$)", " ", family)
        family = re.sub(r"\s+", " ", family).strip() or low
        role_rank = 1 if str(h.get("proj_role") or "") == "copy/alt" else 0
        return (unknown, fmt_rank, family, strength_rank, role_rank, addr)

    for row_i, h in enumerate(sorted(hits, key=_proj_sort_key)):
        mv = FPI.projectile_row_from_hit(h, row_i)
        row = {c: "" for c in FD_COLUMNS}
        row["move"] = _indent_move_text(win.tree, header, f"{mv.get('move_name') or 'Projectile'}")
        row["kind"] = mv.get("kind") or "projectile"
        row["hits"] = "emit" if FPI.is_projectile_emitter_row(mv) else "proj"
        row["link"] = FPI.format_projectile_value(mv, "proj_cluster") or FPI.format_projectile_value(mv, "proj_fmt")
        row["context"] = FPI.projectile_quick_summary(mv)
        row["damage"] = FPI.format_projectile_value(mv, "damage")
        row["kb_x"] = FPI.format_projectile_value(mv, "kb_x")
        row["air_kb"] = FPI.format_projectile_value(mv, "air_kb")
        for c in FPI.PROJECTILE_COLUMNS:
            row[c] = FPI.format_projectile_value(mv, c)
        addr = mv.get("abs")
        row["abs"] = f"0x{int(addr):08X}" if addr else ""
        iid = win.tree.insert(
            header,
            "end",
            text="",
            tags=("row_even" if (row_i % 2 == 0) else "row_odd", "child_row", "projectile_row"),
            values=tuple(row.get(c, "") for c in FD_COLUMNS),
        )
        win.move_to_tree_item[iid] = mv
        try:
            win._all_item_ids.append(iid)
        except Exception:
            pass
        try:
            win._apply_row_tags(iid, mv)
        except Exception:
            pass

def populate_super_rows(win, replace: bool = True) -> None:
    """Insert generic 00/23 super dispatch rows into the same FD Treeview."""
    if not getattr(win, "tree", None):
        return

    if replace:
        for iid in list(win.tree.get_children("")):
            try:
                tags = set(win.tree.item(iid, "tags") or ())
                if "super_header" in tags:
                    win.tree.delete(iid)
                    if iid in getattr(win, "_all_item_ids", []):
                        win._all_item_ids.remove(iid)
            except Exception:
                pass
        try:
            win.move_to_tree_item = {
                iid: mv for iid, mv in (win.move_to_tree_item or {}).items()
                if not FSI.is_super_row(mv)
            }
        except Exception:
            pass

    hits = list(getattr(win, "_super_hits", []) or [])
    if not hits:
        return

    vals = {c: "" for c in FD_COLUMNS}
    vals["move"] = "Special/action graph finder"
    vals["kind"] = "action graph"
    vals["hits"] = "00/23"
    payload_total = 0
    try:
        payload_total = sum(int((h or {}).get("payload_count") or 0) for h in hits)
    except Exception:
        payload_total = 0
    vals["link"] = f"{len(hits)} dispatch row(s), {payload_total} payload candidate(s)"
    vals["context"] = "Dynamic graph: caller rows -> child targets -> projectilemap/packet-owned fields. This covers specials and supers; projectile rows remain payload-only."
    addrs = []
    for h in hits:
        try:
            addrs.append(int(h.get("addr") or 0))
        except Exception:
            pass
    vals["abs"] = f"0x{min(a for a in addrs if a):08X}" if any(addrs) else ""
    header = win.tree.insert(
        "",
        _projectile_root_insert_index(win),
        text="",
        tags=("super_header", "family_header"),
        values=tuple(vals.get(c, "") for c in FD_COLUMNS),
    )
    try:
        win._all_item_ids.append(header)
    except Exception:
        pass

    def _sort_key(h):
        return (str(h.get("dispatch_group") or ""), int(h.get("addr") or 0xFFFFFFFF))

    for row_i, h in enumerate(sorted(hits, key=_sort_key)):
        mv = FSI.super_row_from_hit(h, row_i)
        row = {c: "" for c in FD_COLUMNS}
        row["move"] = _indent_move_text(win.tree, header, f"{mv.get('move_name') or 'Super Dispatch'}")
        row["kind"] = mv.get("kind") or "super dispatch"
        row["hits"] = "call"
        row["link"] = FSI.super_quick_summary(mv)
        row["context"] = FSI.super_context_summary(mv)
        for c in FSI.SUPER_DISPATCH_COLUMNS:
            row[c] = FSI.format_super_value(mv, c)
        for c in getattr(FSI, "SUPER_OWNED_COLUMNS", ()):
            row[c] = FSI.format_super_value(mv, c)
        addr = mv.get("abs")
        row["abs"] = f"0x{int(addr):08X}" if addr else ""
        iid = win.tree.insert(
            header,
            "end",
            text="",
            tags=("row_even" if (row_i % 2 == 0) else "row_odd", "child_row", "super_row"),
            values=tuple(row.get(c, "") for c in FD_COLUMNS),
        )
        win.move_to_tree_item[iid] = mv
        try:
            win._all_item_ids.append(iid)
        except Exception:
            pass
        try:
            win._apply_row_tags(iid, mv)
        except Exception:
            pass

        # Make the graph visible without forcing the user to scroll into hidden
        # columns/sidebar: show the resolved child target and every sniffed
        # super-owned field as nested rows under the parent dispatch row.
        try:
            hit = mv.get("_super_hit") or {}
            child_target = int(hit.get("child_target") or 0)
            child_link = int(hit.get("child_link") or 0)
            child_parent = iid
            if child_target:
                drow = {c: "" for c in FD_COLUMNS}
                drow["move"] = _indent_move_text(win.tree, iid, f"-> child script 0x{child_target:08X}")
                drow["kind"] = "super child"
                drow["hits"] = "child"
                drow["link"] = f"link 0x{child_link:08X}"
                scout = str(hit.get("child_scout") or "")
                scan_start = hit.get("owned_scan_start")
                scan_end = hit.get("owned_scan_end")
                scan_txt = ""
                try:
                    if scan_start and scan_end:
                        scan_txt = f"owned scan 0x{int(scan_start):08X}-0x{int(scan_end):08X}"
                except Exception:
                    scan_txt = ""
                owned_fields_txt = str(hit.get("owned_script_field_summary") or "")
                payload_txt = str(hit.get("payload_summary") or "")
                drow["context"] = " | ".join(x for x in (scan_txt, owned_fields_txt, payload_txt, scout) if x)
                drow["abs"] = f"0x{child_target:08X}"
                ciid = win.tree.insert(
                    iid,
                    "end",
                    text="",
                    tags=("child_row", "super_child_row"),
                    values=tuple(drow.get(c, "") for c in FD_COLUMNS),
                )
                child_parent = ciid
                try:
                    win._all_item_ids.append(ciid)
                except Exception:
                    pass

            fmap = hit.get("owned_field_map") or {}
            order = ("damage", "kb_x", "air_kb", "hitstun", "blockstun", "hitstop", "attack_property", "hit_reaction", "launch_profile", "kb_unknown")
            labels = getattr(FSI, "SUPER_OWNED_FIELD_INFO", {})
            for field_col in order:
                field = fmap.get(field_col) if isinstance(fmap, dict) else None
                if not isinstance(field, dict):
                    continue
                frow = {c: "" for c in FD_COLUMNS}
                label = labels.get(field_col, (None, field_col, None))[1] if isinstance(labels, dict) else field_col
                try:
                    faddr = int(field.get("addr") or 0)
                except Exception:
                    faddr = 0
                frow["move"] = _indent_move_text(win.tree, child_parent, f"{label}")
                frow["kind"] = "owned field"
                frow["hits"] = "field"
                if field_col in FD_COLUMNS:
                    frow[field_col] = FSI.format_super_value(mv, field_col)
                frow["link"] = f"@0x{faddr:08X}" if faddr else ""
                src = str(field.get("source") or "")
                try:
                    pkt = int(field.get("packet_addr") or 0)
                except Exception:
                    pkt = 0
                frow["context"] = (src + (f" packet 0x{pkt:08X}" if pkt else "")).strip()
                frow["abs"] = f"0x{faddr:08X}" if faddr else ""
                fiid = win.tree.insert(
                    child_parent,
                    "end",
                    text="",
                    tags=("child_row", "super_owned_field_row"),
                    values=tuple(frow.get(c, "") for c in FD_COLUMNS),
                )
                # Map the row back to the parent mv so editing the value cell
                # still writes the owned child-script address.
                win.move_to_tree_item[fiid] = mv
                try:
                    win._all_item_ids.append(fiid)
                except Exception:
                    pass

            # Also show the owned payload candidates themselves.  These are the
            # rows that answer "where is the damage/field payload under this
            # child?" even when the address came from the move/payload scanner
            # rather than a literal 35/10 packet in the forward child bytes.
            for pi, payload in enumerate(list(hit.get("payload_candidates") or []), start=1):
                if not isinstance(payload, dict):
                    continue
                prow = {c: "" for c in FD_COLUMNS}
                try:
                    paddr = int(payload.get("addr") or 0)
                except Exception:
                    paddr = 0
                pname = str(payload.get("move") or payload.get("fmt") or "owned payload")
                pfmt = str(payload.get("fmt") or "payload")
                prow["move"] = _indent_move_text(win.tree, child_parent, f"owned payload {pi}: {pname}")
                prow["kind"] = pfmt
                prow["hits"] = "payload"
                prow["link"] = f"@0x{paddr:08X}" if paddr else ""
                if payload.get("dmg") not in (None, "", "?"):
                    prow["damage"] = str(payload.get("dmg"))
                if payload.get("kb_x") not in (None, "", "?"):
                    prow["kb_x"] = str(payload.get("kb_x"))
                if payload.get("kb_y") not in (None, "", "?"):
                    prow["air_kb"] = str(payload.get("kb_y"))
                # Reuse the normal projectile columns for graph-owned payloads
                # so attached templates show their actual editable-looking field
                # surface instead of just a name + damage.
                payload_to_tree = {
                    "radius": "proj_radius",
                    "fx": "proj_fx",
                    "spawn_origin": "proj_spawn_origin",
                    "speed": "proj_speed",
                    "accel": "proj_accel",
                    "hitbox": "proj_hitbox",
                    "lifetime": "proj_life",
                    "fmt": "proj_fmt",
                    "ps_lifetime": "proj_ps_lifetime",
                    "ps_hit_count": "proj_ps_hit_count",
                    "ps_emit_count": "proj_ps_emit_count",
                    "ps_interval": "proj_ps_interval",
                    "ps_scale": "proj_ps_scale",
                    "ps_particle_fx": "proj_ps_particle_fx",
                    "ps_projectile_id": "proj_ps_projectile_id",
                    "ps_spawn_bone": "proj_ps_spawn_bone",
                    "super_lifetime": "proj_super_lifetime",
                    "super_hit_count": "proj_super_hit_count",
                    "super_hit_interval": "proj_super_hit_interval",
                    "super_particle_fx": "proj_super_particle_fx",
                    "super_spawn_bone": "proj_super_spawn_bone",
                    "super_hit_source": "proj_super_hit_source",
                    "super_speed": "proj_super_beam_speed",
                    "super_accel": "proj_super_beam_force",
                    "super_radius": "proj_super_hit_radius",
                    "super_beam_width": "proj_super_beam_width",
                    "super_beam_visual": "proj_super_beam_visual",
                    "super_final_damage": "proj_final_damage",
                    "super_final_lifetime": "proj_final_lifetime",
                    "super_final_particle_fx": "proj_final_particle_fx",
                    "super_final_spawn_bone": "proj_final_spawn_bone",
                }
                for pk, col in payload_to_tree.items():
                    if col in FD_COLUMNS and payload.get(pk) not in (None, "", "?"):
                        prow[col] = str(payload.get(pk))
                if payload.get("damage_addr") not in (None, "", "?"):
                    try:
                        prow["context"] = f"damage @0x{int(payload.get('damage_addr')):08X}"
                    except Exception:
                        prow["context"] = f"damage @{payload.get('damage_addr')}"
                owner = str(payload.get("owner_proof") or payload.get("owner_relation") or "")
                if owner:
                    prow["context"] = (prow.get("context") + " | " if prow.get("context") else "") + owner
                prow["abs"] = f"0x{paddr:08X}" if paddr else ""
                piid = win.tree.insert(
                    child_parent,
                    "end",
                    text="",
                    tags=("child_row", "super_owned_payload_row"),
                    values=tuple(prow.get(c, "") for c in FD_COLUMNS),
                )
                win.move_to_tree_item[piid] = mv
                try:
                    win._all_item_ids.append(piid)
                except Exception:
                    pass
            try:
                win.tree.item(iid, open=True)
                if child_parent != iid:
                    win.tree.item(child_parent, open=True)
            except Exception:
                pass
        except Exception:
            pass
