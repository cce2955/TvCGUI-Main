"""Read-only attack contact research for TvCGUI.

This module never patches game code and never writes Dolphin memory. It combines
already-observed fighter snapshots, reaction telemetry, move definitions, and
spawned attack actor packets into contact records. Contacts are inferred from
victim HP, blockstun, hitstun, action, and last-hit transitions. Immediate
contact values are frozen at the boundary while later samples capture only the
reaction path.

The result is not a substitute for native register tracing. Internal resolver
pointers and exact helper return paths are deliberately omitted. Property B
routes are shown as inferred route families from the observed word.
"""
from __future__ import annotations

import atexit
import csv
import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

from tvcgui.core.paths import user_data_path
from tvcgui.runtime.deferred_work import DeferredWorkLoop

try:
    from tvcgui.features.training.attack_property_profiler import (
        decode_property_a,
        decode_property_b,
    )
except Exception:
    def decode_property_a(value: Any) -> dict:
        raw = int(value or 0) & 0xFFFFFFFF
        return {"text": f"0x{raw:08X}"}

    def decode_property_b(value: Any) -> dict:
        raw = int(value or 0) & 0xFFFFFFFF
        return {"text": f"0x{raw:08X}", "unknown_mask": raw}

try:
    from tvcgui.features.training.reaction_state_profiler import (
        AIR_RECOVERY_ACTIONS,
        BLOCK_ACTIONS,
        KNOCKDOWN_ACTIONS,
        REACTION_ACTION_NAMES,
    )
except Exception:
    BLOCK_ACTIONS = {48, 49, 50, 51, 52, 53}
    KNOCKDOWN_ACTIONS = {
        70, 73, 74, 75, 76, 77, 80, 81, 82, 83, 89, 90, 91, 92,
        93, 95, 96, 98, 102, 104, 105, 106, 108, 109, 132, 142, 161,
    }
    AIR_RECOVERY_ACTIONS = {126, 160, 165}
    REACTION_ACTION_NAMES = {}


RESEARCH_VERSION = 6
SAMPLING_MODE = "native_damage_pipeline_plus_snapshot_contact_correlation_v6"
MAX_CONTACT_HISTORY = 5000
MAX_SOURCE_HISTORY = 10000
MAX_VISIBLE_SOURCE_ATTACHMENTS = 24
RECENT_SOURCE_FRAMES = 24
POST_SAMPLE_FRAMES = 18
WRITE_INTERVAL_SECONDS = 0.75

CONTACT_CSV_FIELDS = [
    "version", "sampling_mode", "timestamp_utc", "sequence", "series_id", "series_hit_index", "frame",
    "terminal_frame", "terminal_reason", "attacker_slot", "attacker_name",
    "attacker_base", "attacker_char_id", "action_id", "action_name",
    "victim_slot", "victim_name", "victim_base", "victim_char_id",
    "source_kind", "source_confidence", "source_score", "source_age_frames",
    "native_capture_enabled", "resolver_hook_state", "resolver_hook_error",
    "property_a", "property_a_text", "property_a_class",
    "property_a_result_flags", "property_a_result_text",
    "property_a_result_ambiguous", "property_a_result_candidates",
    "property_a_final_candidates", "definition_phase_count",
    "matched_phase_indices", "property_b", "property_b_text",
    "property_b_route_inference", "phase_property_a", "phase_property_b",
    "runtime_status_20", "actor", "base_damage", "base_damage_known",
    "authored_damage", "authored_damage_known", "damage_calc_output",
    "damage_calc_output_known", "damage_calc_aux", "native_damage_calc_complete",
    "applied_damage", "resolved_damage", "resolved_damage_known", "resolved_aux",
    "native_damage_complete",
    "observed_hp_loss", "attributed_damage", "damage_attribution_source",
    "damage_attribution_confident", "contact_hp_delta", "last_hit_value",
    "same_frame_unattributed_damage",
    "damage_clamped_by_remaining_hp", "contact_evidence_kind",
    "state_only_contact_candidate", "coalesced_contacts_suspected", "coalesced_contact_count_estimate",
    "followthrough_damage_ignored", "final_damage", "chip_damage", "outcome",
    "trigger_reasons", "hp_before", "hp_after", "terminal_hp", "max_hp",
    "recoverable_before", "recoverable_after", "recoverable_terminal",
    "recoverable_delta", "red_health_generated", "attacker_meter_before",
    "attacker_meter_after", "attacker_meter_terminal", "victim_meter_before",
    "victim_meter_after", "victim_meter_terminal", "combo_before",
    "combo_after", "combo_terminal", "combo_scale_before", "combo_scale_after",
    "combo_scale_terminal", "team_correction", "baroque_active",
    "baroque_red_spent", "roll_power_flags", "puddle_stacks",
    "victim_action_before", "victim_action_after", "victim_action_terminal",
    "victim_action_path", "reaction_phase_before", "reaction_phase_after",
    "reaction_phase_terminal", "reaction_phase_path", "max_hitstun",
    "max_blockstun", "reaction_family_before", "reaction_family_after",
    "reaction_family_terminal", "position_x_before", "position_x_after",
    "position_x_terminal", "position_y_before", "position_y_after",
    "position_y_terminal", "height_before", "height_after", "height_terminal",
    "position_delta_x", "position_delta_y", "velocity_x_est",
    "velocity_y_est", "relative_x_contact", "relative_y_contact",
    "relative_x_terminal", "relative_y_terminal", "relative_offset_max_drift",
    "series_relative_offset_max_drift", "series_attacker_travel",
    "series_victim_travel", "series_motion_mismatch", "knockdown_observed",
    "wall_reaction_observed", "air_recovery_observed",
    "position_stabilized_observed", "series_position_stabilized_observed",
    "sample_count", "correlation_notes",
]

SOURCE_CSV_FIELDS = [
    "timestamp_utc", "source_sequence", "frame", "slot", "character", "base",
    "char_id", "action_id", "action_name", "source_kind", "packet_state",
    "native_capture_enabled", "resolver_hook_state", "resolver_hook_error",
    "property_a", "property_a_text", "property_a_class",
    "property_a_result_flags", "property_a_result_text",
    "property_a_result_ambiguous", "property_a_result_candidates",
    "property_a_final_candidates", "definition_phase_count",
    "matched_phase_indices", "property_b", "property_b_text",
    "property_b_route_inference", "phase_property_a", "phase_property_b",
    "runtime_status_20", "actor", "projectile_id", "allocation_epoch",
    "lifetime_key", "victim_slot_hint", "base_damage", "base_damage_known",
    "authored_damage", "authored_damage_known", "damage_calc_output",
    "damage_calc_output_known", "damage_calc_aux", "native_damage_calc_complete",
    "applied_damage", "resolved_damage", "resolved_damage_known", "resolved_aux",
    "native_damage_complete",
    "cleanup_candidate", "definition_status", "definition_source",
]

EXACT_B_PACKET_LABELS = {
    0x00000015: "Capture trigger packet",
    0x00400014: "Ground capture packet",
    0x00400055: "Stungun capture packet",
    0x00440054: "Ground throw capture packet",
    0x00401055: "Cinematic ground capture packet",
    0x00840054: "Airborne capture packet (correlated)",
    0x000C0014: "Hitgrab into capture-state packet",
    0x000C0054: "Hitgrab into capture-state packet",
    0x40080015: "Cinematic capture continuation packet (correlated)",
    0x40080055: "Cinematic capture + launch packet (correlated)",
    0x400C0055: "Level 3 cinematic capture packet",
    0x00000042: "Spawned strike/contact actor family",
    0x00040042: "Spawned strike/contact actor + special route",
}

B_ROUTE_BITS = (
    (0x00000001, "native result +0004"),
    (0x00000002, "spawned-attack core"),
    (0x00000004, "native low-bit route 0x4"),
    (0x00000008, "alternate contact route"),
    (0x00000010, "target-acquired/contact-lock"),
    (0x00000020, "sustained-contact route"),
    (0x00000040, "standard-strike baseline"),
    (0x00000080, "spawned-actor contact family"),
    (0x00000100, "initial actor phase"),
    (0x00040000, "special chip route"),
    (0x00080000, "capture/cinematic modifier"),
    (0x00100000, "persistent/paired actor route"),
    (0x00400000, "ground capture route core"),
    (0x01000000, "repeat-contact handling"),
    (0x40000000, "result propagation modifier"),
)

WALL_REACTION_ACTIONS = {70, 82}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except Exception:
        return float(default)
    if result != result or abs(result) > 1_000_000.0:
        return float(default)
    return result


def _hex32(value: Any) -> str:
    return f"0x{_safe_int(value) & 0xFFFFFFFF:08X}"


def _team(slot: Any) -> str:
    text = str(slot or "").upper()
    if text.startswith("P1"):
        return "P1"
    if text.startswith("P2"):
        return "P2"
    return ""


def _other_team(slot: Any) -> str:
    value = _team(slot)
    return "P2" if value == "P1" else "P1" if value == "P2" else ""


def _action_name(action_id: Any, fallback: Any = "") -> str:
    value = _safe_int(action_id)
    return str(fallback or REACTION_ACTION_NAMES.get(value) or f"Action {value}")


def _normalized_y(state: dict) -> float:
    """Return a team-neutral vertical coordinate.

    Fighter Y is mirrored by team in the snapshots used by TvCGUI, so absolute
    Y is the stable read-only comparison. Prefer the dedicated height signal
    when it is available.
    """
    height = abs(_safe_float(state.get("height"), 0.0))
    if height > 0.01:
        return height
    return abs(_safe_float(state.get("y"), 0.0))


def _phase_summary(
    phases: Any,
    *,
    property_a: int,
    property_b: int,
    phase_property_a: int = 0,
    phase_property_b: int = 0,
    live_packet: bool = False,
) -> dict:
    """Summarize static attack definitions without pretending one branch ran."""
    rows = [dict(row) for row in (phases or []) if isinstance(row, dict)]
    result_candidates: list[int] = []
    final_candidates: list[int] = []
    matched_indices: list[int] = []
    compact_rows: list[dict] = []
    for fallback_index, row in enumerate(rows, 1):
        index = _safe_int(row.get("phase_index"), fallback_index)
        phase_a = _safe_int(row.get("property_a")) & 0xFFFFFFFF
        phase_b = _safe_int(row.get("property_b")) & 0xFFFFFFFF
        result_raw = row.get("a_result_flags_raw", row.get("hit_result_raw"))
        if result_raw is not None:
            result_value = _safe_int(result_raw) & 0xFFFFFFFF
            if result_value and result_value not in result_candidates:
                result_candidates.append(result_value)
        final_value = _safe_int(row.get("property_a_final"), phase_a) & 0xFFFFFFFF
        if final_value not in final_candidates:
            final_candidates.append(final_value)
        if phase_property_a or phase_property_b:
            if phase_a == (phase_property_a & 0xFFFFFFFF) and phase_b == (phase_property_b & 0xFFFFFFFF):
                matched_indices.append(index)
        compact_rows.append({
            "phase_index": index,
            "property_a": phase_a,
            "property_b": phase_b,
            "property_a_initial": _safe_int(row.get("property_a_initial"), phase_a) & 0xFFFFFFFF,
            "property_a_final": final_value,
            "property_a_result_flags": (
                None if result_raw is None else _safe_int(result_raw) & 0xFFFFFFFF
            ),
            "property_a_result_code": (
                None if row.get("a_result_code") is None else _safe_int(row.get("a_result_code")) & 0xFFFFFFFF
            ),
            "property_a_addr": _safe_int(row.get("property_a_addr")),
            "property_b_addr": _safe_int(row.get("property_b_addr")),
            "result_addr": _safe_int(row.get("hit_result_addr")),
            "script_offset": _safe_int(row.get("script_offset")),
        })

    if live_packet and not result_candidates:
        embedded = property_a & 0xFFFFFF00
        if embedded:
            result_candidates.append(embedded)
    if not final_candidates and property_a:
        final_candidates.append(property_a & 0xFFFFFFFF)

    unique_result = result_candidates[0] if len(result_candidates) == 1 else 0
    result_text = str(decode_property_a(unique_result).get("text") or "") if unique_result else ""
    return {
        "property_a_class": property_a & 0xFF,
        "property_a_result_flags": unique_result,
        "property_a_result_text": result_text,
        "property_a_result_ambiguous": len(result_candidates) > 1,
        "property_a_result_candidates": result_candidates,
        "property_a_final_candidates": final_candidates,
        "definition_phase_count": len(rows),
        "matched_phase_indices": matched_indices,
        "definition_phases": compact_rows,
    }


def _json_clone(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value))
    except Exception:
        return value


def infer_property_b_routes(value: Any) -> str:
    raw = _safe_int(value) & 0xFFFFFFFF
    if raw == 0:
        return "none"
    parts: list[str] = []
    exact = EXACT_B_PACKET_LABELS.get(raw)
    if exact:
        parts.append(f"exact: {exact}")
    for bit, label in B_ROUTE_BITS:
        if raw & bit:
            parts.append(label)
    return "; ".join(dict.fromkeys(parts)) or f"unresolved B packet {_hex32(raw)}"


def _snap_value(snap: dict, *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = snap.get(key)
        if value is not None and value != "":
            return value
    return default


def _state_snapshot(slot: str, snap: dict, frame: int) -> dict:
    action_id = _safe_int(_snap_value(snap, "reaction_action_id", "mv_id_display", "attA", "attB"))
    reaction_family = _snap_value(snap, "reaction_family", default="0x00000000")
    if isinstance(reaction_family, str):
        try:
            reaction_family_value = int(reaction_family, 16)
        except Exception:
            reaction_family_value = 0
    else:
        reaction_family_value = _safe_int(reaction_family)
    cur = _safe_int(snap.get("cur"))
    aux = _safe_int(snap.get("aux"), cur)
    maximum = _safe_int(snap.get("max"))
    raw_height = abs(_safe_float(_snap_value(snap, "damage_height", default=0.0)))
    y = _safe_float(snap.get("y"), 0.0)
    height = raw_height if raw_height > 0.01 else abs(y)
    return {
        "frame": int(frame),
        "slot": str(slot),
        "team": _team(slot),
        "base": _safe_int(snap.get("base")),
        "char_id": _safe_int(snap.get("id")),
        "name": str(snap.get("name") or ""),
        "action_id": action_id,
        "action_name": _action_name(action_id, _snap_value(snap, "reaction_action_name", "mv_label_display", "mv_label")),
        "move_action_id": _safe_int(_snap_value(snap, "mv_id_display", "attA", "attB")),
        "move_name": str(_snap_value(snap, "mv_label_display", "mv_label", default="") or ""),
        "hp": cur,
        "aux": aux,
        "max_hp": maximum,
        "recoverable": max(0, aux - cur),
        "meter": _safe_int(snap.get("meter")),
        "last_hit": _safe_int(snap.get("last"), -1),
        "x": _safe_float(snap.get("x"), 0.0),
        "y": y,
        "height": height,
        "normalized_y": height,
        "airborne": bool(height > 0.01),
        "f062": _safe_int(snap.get("f062")),
        "f063": _safe_int(snap.get("f063")),
        "f064": _safe_int(snap.get("f064")),
        "f072": _safe_int(snap.get("f072")),
        "ctrl": _safe_int(snap.get("ctrl")),
        "reaction_phase": str(snap.get("reaction_phase") or "neutral"),
        "blockstun": _safe_int(snap.get("reaction_blockstun_remaining")),
        "hitstun": _safe_int(snap.get("reaction_hitstun_remaining")),
        "reaction_timer": _safe_int(snap.get("reaction_secondary_timer")),
        "reaction_family": reaction_family_value,
        "combo_count": _safe_int(snap.get("damage_combo_count")),
        "combo_scale": _safe_float(snap.get("damage_combo_scale"), 1.0),
        "point_active": bool(snap.get("damage_point_active", snap.get("damage_is_point", False))),
        "combo_lane_active": bool(snap.get("damage_combo_lane_active", False)),
        "team_correction": _safe_float(snap.get("damage_team_correction"), 1.0),
        "baroque_active": bool(snap.get("damage_baroque_active", False)),
        "baroque_red_spent": _safe_int(snap.get("damage_baroque_red_spent")),
        "roll_power_flags": _safe_int(snap.get("damage_roll_power_flags")),
        "puddle_stacks": _safe_int(snap.get("damage_roll_puddle_stacks")),
        "script_state_active": bool(snap.get("damage_script_state_active", False)),
    }


def _cleanup_actor(row: dict) -> bool:
    return bool(
        row.get("inactive_generic_actor")
        or (
            _safe_int(row.get("property_a")) == 0x00000009
            and _safe_int(row.get("property_b")) == 0x00000040
        )
    )


def _source_from_snap(slot: str, snap: dict, frame: int) -> list[dict]:
    sources: list[dict] = []
    resolver_hook_state = str(snap.get("attack_property_resolver_hook_state") or "UNKNOWN")
    resolver_hook_error = str(snap.get("attack_property_resolver_hook_error") or "")
    native_capture_enabled = resolver_hook_state not in {"DISABLED", "UNKNOWN"}
    projectiles = snap.get("attack_property_projectiles") or snap.get("attack_property_actors") or []
    for row in projectiles:
        if not isinstance(row, dict):
            continue
        prop_a = _safe_int(row.get("property_a")) & 0xFFFFFFFF
        prop_b = _safe_int(row.get("property_b")) & 0xFFFFFFFF
        cleanup = _cleanup_actor(row)
        action_id = _safe_int(row.get("projectile_action_id", row.get("projectile_id")))
        action_name = str(
            row.get("attack_actor_name")
            or row.get("projectile_action_name")
            or row.get("owner_action_name")
            or snap.get("mv_label_display")
            or snap.get("mv_label")
            or ""
        )
        phase_a = _safe_int(row.get("phase_property_a")) & 0xFFFFFFFF
        phase_b = _safe_int(row.get("phase_property_b")) & 0xFFFFFFFF
        phase_summary = _phase_summary(
            row.get("phases") or [],
            property_a=prop_a,
            property_b=prop_b,
            phase_property_a=phase_a,
            phase_property_b=phase_b,
            live_packet=True,
        )
        base_damage_raw = row.get("base_damage")
        base_damage_value = _safe_int(base_damage_raw) if base_damage_raw not in (None, "") else 0
        base_damage_known = base_damage_value > 0
        sources.append({
            "frame": int(frame),
            "slot": str(slot),
            "team": _team(slot),
            "base": _safe_int(snap.get("base")),
            "char_id": _safe_int(snap.get("id")),
            "character": str(snap.get("name") or ""),
            "action_id": action_id,
            "action_name": action_name,
            "source_kind": "cleanup_actor" if cleanup else "live_attack_actor",
            "packet_state": str(row.get("packet_state") or ("CLEANUP" if cleanup else "LIVE ATTACK ACTOR")),
            "native_capture_enabled": native_capture_enabled,
            "resolver_hook_state": resolver_hook_state,
            "resolver_hook_error": resolver_hook_error,
            "property_a": prop_a,
            "property_a_text": str(row.get("property_a_text") or decode_property_a(prop_a).get("text") or ""),
            **phase_summary,
            "property_b": prop_b,
            "property_b_text": str(row.get("property_b_text") or decode_property_b(prop_b).get("text") or ""),
            "property_b_route_inference": infer_property_b_routes(prop_b),
            "phase_property_a": phase_a,
            "phase_property_b": phase_b,
            "runtime_status_20": _safe_int(row.get("runtime_status_20")) & 0xFFFFFFFF,
            "actor": _safe_int(row.get("actor")),
            "projectile_id": _safe_int(row.get("projectile_id")),
            "allocation_epoch": _safe_int(row.get("allocation_epoch")),
            "lifetime_key": str(row.get("lifetime_key") or ""),
            "victim_slot_hint": str(row.get("victim_slot") or ""),
            "base_damage": base_damage_value if base_damage_known else None,
            "base_damage_known": base_damage_known,
            "authored_damage": base_damage_value if base_damage_known else None,
            "authored_damage_known": base_damage_known,
            "damage_calc_output": None,
            "damage_calc_output_known": False,
            "damage_calc_aux": None,
            "native_damage_calc_complete": False,
            "applied_damage": None,
            "resolved_damage": None,
            "resolved_damage_known": False,
            "resolved_aux": None,
            "native_damage_complete": False,
            "cleanup_candidate": cleanup,
            "definition_status": str(snap.get("attack_property_definition_status") or ""),
            "definition_source": str(snap.get("attack_property_definition_action_source") or ""),
        })

    display_active = bool(snap.get("attack_property_display_active"))
    prop_a_raw = snap.get("attack_property_live_a")
    prop_b_raw = snap.get("attack_property_live_b")
    if display_active and (prop_a_raw is not None or prop_b_raw is not None):
        prop_a = _safe_int(prop_a_raw) & 0xFFFFFFFF
        prop_b = _safe_int(prop_b_raw) & 0xFFFFFFFF
        packet_source = str(snap.get("attack_property_packet_source") or snap.get("attack_property_display_source") or "move_definition")
        cleanup = bool(prop_a == 0x00000009 and prop_b == 0x00000040 and "actor" in packet_source)
        phase_a = _safe_int(snap.get("attack_property_live_phase_a")) & 0xFFFFFFFF
        phase_b = _safe_int(snap.get("attack_property_live_phase_b")) & 0xFFFFFFFF
        definition_phases = snap.get("attack_property_phases") or []
        phase_summary = _phase_summary(
            definition_phases,
            property_a=prop_a,
            property_b=prop_b,
            phase_property_a=phase_a,
            phase_property_b=phase_b,
            live_packet="actor" in packet_source,
        )
        authored_raw = snap.get("attack_property_live_authored_damage", snap.get("attack_property_live_damage"))
        authored_value = _safe_int(authored_raw) if authored_raw not in (None, "") else 0
        calc_complete = bool(snap.get("attack_property_native_damage_calc_complete"))
        native_complete = bool(snap.get("attack_property_native_damage_complete"))
        authored_known = bool(authored_raw not in (None, "") and (calc_complete or native_complete or authored_value > 0))
        calc_raw = snap.get("attack_property_live_damage_calc_output")
        calc_known = bool(calc_complete and calc_raw not in (None, ""))
        calc_value = _safe_int(calc_raw) if calc_known else 0
        calc_aux_raw = snap.get("attack_property_live_damage_calc_aux")
        calc_aux = _safe_int(calc_aux_raw) if calc_complete and calc_aux_raw not in (None, "") else None
        applied_raw = snap.get("attack_property_live_applied_damage", snap.get("attack_property_live_resolved_damage"))
        resolved_raw = snap.get("attack_property_live_resolved_damage", applied_raw)
        resolved_known = bool(native_complete and resolved_raw not in (None, ""))
        resolved_value = _safe_int(resolved_raw) if resolved_known else 0
        applied_value = _safe_int(applied_raw) if native_complete and applied_raw not in (None, "") else None
        resolved_aux_raw = snap.get("attack_property_live_resolved_aux")
        resolved_aux = _safe_int(resolved_aux_raw) if calc_complete and resolved_aux_raw not in (None, "") else calc_aux
        sources.append({
            "frame": int(frame),
            "slot": str(slot),
            "team": _team(slot),
            "base": _safe_int(snap.get("base")),
            "char_id": _safe_int(snap.get("id")),
            "character": str(snap.get("name") or ""),
            "action_id": _safe_int(snap.get("attack_property_packet_action_id"), _safe_int(snap.get("mv_id_display"))),
            "action_name": str(snap.get("attack_property_packet_action_name") or snap.get("mv_label_display") or snap.get("mv_label") or ""),
            "source_kind": "cleanup_actor" if cleanup else packet_source,
            "packet_state": str(snap.get("attack_property_packet_state") or "CURRENT MOVE"),
            "native_capture_enabled": native_capture_enabled,
            "resolver_hook_state": resolver_hook_state,
            "resolver_hook_error": resolver_hook_error,
            "property_a": prop_a,
            "property_a_text": str(snap.get("attack_property_live_a_text") or decode_property_a(prop_a).get("text") or ""),
            **phase_summary,
            "property_b": prop_b,
            "property_b_text": str(snap.get("attack_property_live_b_text") or decode_property_b(prop_b).get("text") or ""),
            "property_b_route_inference": infer_property_b_routes(prop_b),
            "phase_property_a": phase_a,
            "phase_property_b": phase_b,
            "runtime_status_20": _safe_int(snap.get("attack_property_live_status20")) & 0xFFFFFFFF,
            "actor": _safe_int(snap.get("attack_property_live_actor")),
            "projectile_id": 0,
            "allocation_epoch": 0,
            "lifetime_key": "",
            "victim_slot_hint": str(snap.get("attack_property_live_victim_slot") or ""),
            "base_damage": authored_value if authored_known else None,
            "base_damage_known": authored_known,
            "authored_damage": authored_value if authored_known else None,
            "authored_damage_known": authored_known,
            "damage_calc_output": calc_value if calc_known else None,
            "damage_calc_output_known": calc_known,
            "damage_calc_aux": calc_aux,
            "native_damage_calc_complete": calc_complete,
            "applied_damage": applied_value,
            "resolved_damage": resolved_value if resolved_known else None,
            "resolved_damage_known": resolved_known,
            "resolved_aux": resolved_aux,
            "native_damage_complete": native_complete,
            "cleanup_candidate": cleanup,
            "definition_status": str(snap.get("attack_property_definition_status") or ""),
            "definition_source": str(snap.get("attack_property_definition_action_source") or ""),
        })

    # De-duplicate the display row when it mirrors a live actor already listed.
    unique: dict[tuple, dict] = {}
    for source in sources:
        key = (
            source.get("slot"), source.get("source_kind"), source.get("actor"),
            source.get("projectile_id"), source.get("allocation_epoch"), source.get("lifetime_key"),
            source.get("victim_slot_hint"),
            source.get("action_id"), source.get("property_a"), source.get("property_b"),
            source.get("phase_property_a"), source.get("phase_property_b"),
            source.get("cleanup_candidate"),
        )
        unique[key] = source
    return list(unique.values())


def _source_signature(source: dict) -> tuple:
    return (
        source.get("slot"), source.get("source_kind"), source.get("actor"),
        source.get("action_id"), source.get("property_a"), source.get("property_b"),
        source.get("phase_property_a"), source.get("phase_property_b"),
        source.get("runtime_status_20"), source.get("allocation_epoch"), source.get("lifetime_key"),
        source.get("victim_slot_hint"), source.get("cleanup_candidate"),
        source.get("property_a_result_flags"),
        source.get("authored_damage"), source.get("damage_calc_output"),
        source.get("damage_calc_aux"), source.get("native_damage_calc_complete"),
        source.get("applied_damage"), source.get("resolved_damage"),
        source.get("resolved_aux"), source.get("native_damage_complete"),
        tuple(source.get("property_a_result_candidates") or ()),
        tuple(source.get("property_a_final_candidates") or ()),
        tuple(source.get("matched_phase_indices") or ()),
    )


def _distance(a: dict, b: dict) -> float:
    dx = _safe_float(a.get("x")) - _safe_float(b.get("x"))
    dy = _safe_float(a.get("y")) - _safe_float(b.get("y"))
    return (dx * dx + dy * dy) ** 0.5


def _append_csv(path: Path, rows: list[dict], fields: list[str]) -> bool:
    if not rows:
        return True
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        exists = path.exists() and path.stat().st_size > 0
        with path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            if not exists:
                writer.writeheader()
            for row in rows:
                flat = {}
                for key in fields:
                    value = row.get(key)
                    if isinstance(value, (list, dict, tuple)):
                        value = json.dumps(value, separators=(",", ":"), sort_keys=True)
                    flat[key] = value
                writer.writerow(flat)
        return True
    except Exception:
        return False


class ReadOnlyAttackResearch:
    """Correlate attack sources and victim transitions without memory writes."""

    def __init__(self, *, runtime_dir: Optional[Path] = None, emit_console: bool = False) -> None:
        self._lock = threading.RLock()
        self._capture_enabled = False
        self._contacts: deque[dict] = deque(maxlen=MAX_CONTACT_HISTORY)
        self._sources: deque[dict] = deque(maxlen=MAX_SOURCE_HISTORY)
        self._recent_sources: dict[str, deque[dict]] = {}
        self._last_source_signature: dict[tuple, tuple] = {}
        self._last_contact_seq_by_victim: dict[str, int] = {}
        self._prev_states: dict[str, dict] = {}
        self._current_states: dict[str, dict] = {}
        self._pending: dict[int, dict] = {}
        self._sequence = 0
        self._series_sequence = 0
        self._source_sequence = 0
        self._contact_series: dict[tuple, dict] = {}
        self._observed_frames = 0
        self._last_frame = 0
        self._echo_suppressed_count = 0
        self._identity_reset_count = 0
        self._emit_console = bool(emit_console)
        self._closed = False
        self._pending_contact_rows: list[dict] = []
        self._pending_source_rows: list[dict] = []
        self._runtime_dir = Path(runtime_dir or user_data_path("runtime"))
        self.contact_csv_path = self._runtime_dir / "runtime_readonly_attack_contacts.csv"
        self.source_csv_path = self._runtime_dir / "runtime_readonly_attack_sources.csv"
        self.jsonl_path = self._runtime_dir / "runtime_readonly_attack_contacts.jsonl"
        self._writer = DeferredWorkLoop(
            self._flush_pending,
            interval=WRITE_INTERVAL_SECONDS,
            name="TvCReadOnlyAttackResearchWriter",
        )

    @property
    def capture_enabled(self) -> bool:
        with self._lock:
            return bool(self._capture_enabled)

    def set_capture_enabled(self, enabled: bool) -> bool:
        requested = bool(enabled)
        with self._lock:
            if self._closed:
                return False
            was_enabled = bool(self._capture_enabled)
            self._capture_enabled = requested
            if was_enabled and not requested:
                pending = list(self._pending.values())
                self._pending.clear()
                for record in pending:
                    self._finalize(record, reason="capture_disabled")
        return True

    def set_enabled(self, enabled: bool) -> bool:
        return self.set_capture_enabled(enabled)

    def _record_source(self, source: dict) -> None:
        slot = str(source.get("slot") or "")
        signature = _source_signature(source)
        identity = (
            slot,
            str(source.get("source_kind") or ""),
            _safe_int(source.get("actor")),
            _safe_int(source.get("projectile_id")),
            _safe_int(source.get("allocation_epoch")),
            str(source.get("lifetime_key") or ""),
            _safe_int(source.get("action_id")),
        )
        prior = self._last_source_signature.get(identity)
        if signature == prior:
            return
        self._last_source_signature[identity] = signature
        self._source_sequence += 1
        row = dict(source)
        row["source_sequence"] = self._source_sequence
        row["timestamp_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self._sources.append(row)
        self._pending_source_rows.append(dict(row))
        if self._emit_console:
            print("[ATKREAD_SOURCE] " + json.dumps(row, separators=(",", ":"), sort_keys=True), flush=True)

    def _remember_sources(self, slot: str, sources: list[dict], frame: int, capture: bool) -> None:
        queue = self._recent_sources.setdefault(str(slot), deque(maxlen=96))
        for source in sources:
            row = dict(source)
            row["frame"] = int(frame)
            queue.append(row)
            if capture:
                self._record_source(row)
        while queue and int(frame) - _safe_int(queue[0].get("frame")) > RECENT_SOURCE_FRAMES:
            queue.popleft()

    def _candidate_score(self, source: dict, attacker: dict, victim: dict, frame: int) -> tuple[float, list[str]]:
        age = max(0, int(frame) - _safe_int(source.get("frame")))
        score = max(0.0, 36.0 - age * 1.75)
        reasons = [f"source age {age}f"]
        kind = str(source.get("source_kind") or "")
        if source.get("cleanup_candidate"):
            return -1000.0, ["cleanup actor rejected"]
        if kind == "live_attack_actor":
            score += 34.0
            reasons.append("live attack actor")
        elif "move_definition" in kind or "script" in kind:
            score += 18.0
            reasons.append("current move definition")
        hint = str(source.get("victim_slot_hint") or "")
        if hint and hint == str(victim.get("slot") or ""):
            score += 90.0
            reasons.append("native victim hint matched")
        if _safe_int(source.get("action_id")) == _safe_int(attacker.get("move_action_id")):
            score += 12.0
            reasons.append("action ID matched")
        if _safe_int(source.get("property_a")) or _safe_int(source.get("property_b")):
            score += 10.0
        if attacker.get("point_active"):
            score += 5.0
        if str(attacker.get("reaction_phase") or "neutral") in {"neutral", ""}:
            score += 4.0
        dist = _distance(attacker, victim)
        if dist <= 2.5:
            score += 24.0
            reasons.append(f"distance {dist:.2f}")
        elif dist <= 6.0:
            score += 14.0
            reasons.append(f"distance {dist:.2f}")
        elif dist <= 12.0:
            score += 6.0
            reasons.append(f"distance {dist:.2f}")
        return score, reasons

    def _select_attacker(self, victim: dict, states: dict[str, dict], frame: int) -> tuple[Optional[dict], Optional[dict], float, list[str]]:
        enemy_team = _other_team(victim.get("slot"))
        best: tuple[float, Optional[dict], Optional[dict], list[str]] = (-10_000.0, None, None, [])
        for slot, attacker in states.items():
            if _team(slot) != enemy_team:
                continue
            for source in list(self._recent_sources.get(slot, ())):
                score, reasons = self._candidate_score(source, attacker, victim, frame)
                if score <= -999.0:
                    continue
                if score > best[0]:
                    best = (score, attacker, source, reasons)
        if best[1] is not None:
            return best[1], best[2], best[0], best[3]

        # Fallback only when no property source survived. Keep the record but
        # mark the source as low confidence rather than inventing A/B values.
        fallback: Optional[dict] = None
        fallback_distance = 1e9
        for slot, attacker in states.items():
            if _team(slot) != enemy_team:
                continue
            distance = _distance(attacker, victim)
            if distance < fallback_distance:
                fallback = attacker
                fallback_distance = distance
        if fallback is None:
            return None, None, 0.0, ["no opposing fighter resolved"]
        return fallback, None, max(1.0, 12.0 - fallback_distance), [f"closest opposing fighter at {fallback_distance:.2f}"]

    @staticmethod
    def _trigger(previous: dict, current: dict) -> tuple[bool, list[str], dict]:
        """Return concrete contact boundaries and suppress delayed state echoes.

        HP loss is always a contact boundary. A fresh blockstun edge is also a
        boundary because many blocked attacks deal no chip. Hitstun-only rises
        are accepted only when the victim's last-hit value changed. When the
        last-hit value is unchanged, the rise is treated as delayed reaction
        telemetry from the already-open contact rather than a second hit.
        """
        reasons: list[str] = []
        hp_loss = max(0, _safe_int(previous.get("hp")) - _safe_int(current.get("hp")))
        block_rise = _safe_int(current.get("blockstun")) - _safe_int(previous.get("blockstun"))
        hit_rise = _safe_int(current.get("hitstun")) - _safe_int(previous.get("hitstun"))
        prior_phase = str(previous.get("reaction_phase") or "neutral")
        current_phase = str(current.get("reaction_phase") or "neutral")
        prior_last = _safe_int(previous.get("last_hit"), -1)
        current_last = _safe_int(current.get("last_hit"), -1)
        last_changed = prior_last != current_last
        action_changed = _safe_int(previous.get("action_id")) != _safe_int(current.get("action_id"))
        family_changed = _safe_int(previous.get("reaction_family")) != _safe_int(current.get("reaction_family"))
        block_contact = bool(
            block_rise > 0
            and _safe_int(current.get("blockstun")) > 0
        )
        hit_state_active = bool(
            _safe_int(current.get("hitstun")) > 0
            or current_phase in {"hitstun", "knockdown"}
            or _safe_int(current.get("action_id")) in KNOCKDOWN_ACTIONS
        )
        state_contact = bool(
            hp_loss <= 0
            and not block_contact
            and last_changed
            and current_last > 0
            and hit_state_active
            and (
                hit_rise > 0
                or action_changed
                or family_changed
                or current_phase != prior_phase
            )
        )
        echo_candidate = bool(
            hp_loss <= 0
            and not block_contact
            and not last_changed
            and hit_state_active
            and (
                hit_rise > 0
                or current_phase != prior_phase
                or action_changed
            )
        )
        concrete = bool(hp_loss > 0 or block_contact or state_contact)
        metrics = {
            "hp_loss": hp_loss,
            "block_rise": block_rise,
            "hit_rise": hit_rise,
            "last_changed": last_changed,
            "action_changed": action_changed,
            "family_changed": family_changed,
            "block_contact": block_contact,
            "state_contact": state_contact,
            "echo_candidate": echo_candidate,
        }
        if not concrete:
            return False, [], metrics

        if hp_loss > 0:
            reasons.append(f"HP -{hp_loss}")
        if block_contact:
            reasons.append(f"blockstun +{block_rise}")
        if state_contact:
            reasons.append("last-hit changed without HP delta")
        if hit_rise > 0 and (hp_loss > 0 or state_contact):
            reasons.append(f"hitstun +{hit_rise}")
        if current_phase != prior_phase:
            reasons.append(f"reaction {prior_phase}->{current_phase}")
        if last_changed and action_changed and current_phase != "neutral":
            reasons.append("last-hit/action transition")
        return True, reasons, metrics

    def _assign_series(self, record: dict, attacker: dict, victim: dict, frame: int) -> None:
        source = record.get("source") or {}
        key = (
            str(record.get("attacker_slot") or ""),
            str(record.get("victim_slot") or ""),
            _safe_int(record.get("action_id")),
            str(record.get("source_kind") or ""),
            _safe_int(record.get("actor")),
            str(source.get("lifetime_key") or ""),
        )
        state = self._contact_series.get(key)
        if state is None or int(frame) - _safe_int(state.get("last_frame")) > 4:
            self._series_sequence += 1
            state = {
                "series_id": self._series_sequence,
                "last_frame": int(frame),
                "hit_count": 0,
                "points": deque(maxlen=48),
            }
            self._contact_series[key] = state
        state["last_frame"] = int(frame)
        state["hit_count"] = _safe_int(state.get("hit_count")) + 1

        attacker_x = _safe_float(attacker.get("x"))
        attacker_y = _normalized_y(attacker)
        victim_x = _safe_float(victim.get("x"))
        victim_y = _normalized_y(victim)
        rel_x = victim_x - attacker_x
        rel_y = victim_y - attacker_y
        points = state.setdefault("points", deque(maxlen=48))
        points.append((attacker_x, attacker_y, victim_x, victim_y, rel_x, rel_y))

        xs = [float(row[4]) for row in points]
        ys = [float(row[5]) for row in points]
        attacker_travel = 0.0
        victim_travel = 0.0
        motion_mismatch = 0.0
        for prior, current in zip(list(points), list(points)[1:]):
            adx = float(current[0]) - float(prior[0])
            ady = float(current[1]) - float(prior[1])
            vdx = float(current[2]) - float(prior[2])
            vdy = float(current[3]) - float(prior[3])
            attacker_travel += (adx * adx + ady * ady) ** 0.5
            victim_travel += (vdx * vdx + vdy * vdy) ** 0.5
            mdx = vdx - adx
            mdy = vdy - ady
            motion_mismatch += (mdx * mdx + mdy * mdy) ** 0.5

        x_drift = (max(xs) - min(xs)) if xs else 0.0
        y_drift = (max(ys) - min(ys)) if ys else 0.0
        max_drift = max(x_drift, y_drift)
        shared_travel = min(attacker_travel, victim_travel)
        mismatch_limit = max(0.12, shared_travel * 0.12)
        close_lock = bool(
            len(points) >= 3
            and float(sorted(abs(value) for value in xs)[len(xs) // 2]) <= 0.18
            and float(sorted(abs(value) for value in ys)[len(ys) // 2]) <= 1.25
            and x_drift <= 0.35
            and y_drift <= 2.75
            and shared_travel >= 1.25
            and motion_mismatch <= mismatch_limit
        )
        record.update({
            "series_id": _safe_int(state.get("series_id")),
            "series_hit_index": _safe_int(state.get("hit_count")),
            "relative_x_contact": rel_x,
            "relative_y_contact": rel_y,
            "series_relative_offset_max_drift": max_drift,
            "series_attacker_travel": attacker_travel,
            "series_victim_travel": victim_travel,
            "series_motion_mismatch": motion_mismatch,
            "series_position_stabilized_observed": close_lock,
        })

        # Remove dormant series so long sessions do not retain stale keys.
        for stale_key, stale in list(self._contact_series.items()):
            if int(frame) - _safe_int(stale.get("last_frame")) > 120:
                self._contact_series.pop(stale_key, None)

    def _new_contact(
        self,
        previous: dict,
        victim: dict,
        states: dict[str, dict],
        frame: int,
        now: float,
        reasons: list[str],
        metrics: dict,
    ) -> None:
        victim_slot = str(victim.get("slot") or "")
        prior_seq = self._last_contact_seq_by_victim.get(victim_slot)
        prior_record = self._pending.get(_safe_int(prior_seq)) if prior_seq else None

        # Any concrete new contact closes the previous contact before the new
        # frame is sampled. This prevents suffix-total damage and reaction paths.
        if prior_record is not None:
            self._pending.pop(_safe_int(prior_seq), None)
            self._finalize(prior_record, reason="next_contact")

        attacker, source, score, score_reasons = self._select_attacker(victim, states, frame)
        self._sequence += 1
        seq = self._sequence
        prop_a = _safe_int((source or {}).get("property_a")) & 0xFFFFFFFF
        prop_b = _safe_int((source or {}).get("property_b")) & 0xFFFFFFFF
        source_kind = str((source or {}).get("source_kind") or "unresolved_attacker")
        confidence = "high" if score >= 90 else "medium" if score >= 45 else "low"
        hp_loss = max(0, _safe_int(metrics.get("hp_loss")))
        last_hit_value = max(0, _safe_int(victim.get("last_hit")))
        authored_known = bool((source or {}).get("authored_damage_known", (source or {}).get("base_damage_known")))
        authored_damage = (
            _safe_int((source or {}).get("authored_damage", (source or {}).get("base_damage")))
            if authored_known else None
        )
        calc_complete = bool((source or {}).get("native_damage_calc_complete"))
        damage_calc_known = bool((source or {}).get("damage_calc_output_known") and calc_complete)
        damage_calc_output = _safe_int((source or {}).get("damage_calc_output")) if damage_calc_known else None
        damage_calc_aux = (source or {}).get("damage_calc_aux") if calc_complete else None
        native_complete = bool((source or {}).get("native_damage_complete"))
        resolved_known = bool((source or {}).get("resolved_damage_known") and native_complete)
        resolved_damage = _safe_int((source or {}).get("resolved_damage")) if resolved_known else None
        applied_damage = _safe_int((source or {}).get("applied_damage")) if native_complete else None
        resolved_aux = (source or {}).get("resolved_aux") if calc_complete else None
        single_hit_reference = resolved_damage if resolved_known else (last_hit_value if last_hit_value > 0 else None)
        unattributed = (
            max(0, hp_loss - int(single_hit_reference))
            if hp_loss > 0 and single_hit_reference is not None else 0
        )
        clamped_by_remaining_hp = (
            max(0, int(single_hit_reference) - hp_loss)
            if hp_loss > 0 and single_hit_reference is not None else 0
        )
        coalesced = bool(
            hp_loss > 0 and single_hit_reference is not None and hp_loss > int(single_hit_reference)
        )
        if resolved_known:
            attributed_damage = resolved_damage
            attribution_source = "native_resolved"
            attribution_confident = True
        elif last_hit_value > 0 and not coalesced:
            attributed_damage = last_hit_value
            attribution_source = "last_hit_single"
            attribution_confident = True
        elif hp_loss > 0 and not coalesced:
            attributed_damage = hp_loss
            attribution_source = "hp_delta_fallback"
            attribution_confident = True
        elif coalesced:
            attributed_damage = None
            attribution_source = "ambiguous_coalesced"
            attribution_confident = False
        else:
            attributed_damage = 0
            attribution_source = "state_only"
            attribution_confident = False
        state_only_candidate = bool(metrics.get("state_contact") and hp_loss <= 0)
        if hp_loss > 0:
            evidence_kind = "hp_delta"
        elif bool(metrics.get("block_contact")):
            evidence_kind = "blockstun_edge"
        else:
            evidence_kind = "last_hit_state_candidate"
        base_damage_known = authored_known
        base_damage = authored_damage
        attacker_slot = str((attacker or {}).get("slot") or "")
        attacker_previous = self._prev_states.get(attacker_slot, attacker or {})
        recoverable_before = _safe_int(previous.get("recoverable"))
        recoverable_after = _safe_int(victim.get("recoverable"))
        immediate_block = bool(
            _safe_int(victim.get("blockstun")) > 0
            or _safe_int(victim.get("action_id")) in BLOCK_ACTIONS
            or str(victim.get("reaction_phase") or "") == "blockstun"
        )
        immediate_hit = bool(
            _safe_int(victim.get("hitstun")) > 0
            or _safe_int(victim.get("action_id")) in KNOCKDOWN_ACTIONS
            or str(victim.get("reaction_phase") or "") in {"hitstun", "knockdown"}
        )
        if _safe_int(victim.get("hp")) <= 0 and hp_loss > 0:
            outcome = "KO"
        elif immediate_block:
            outcome = "chip" if hp_loss > 0 else "block"
        elif hp_loss > 0:
            outcome = "hit"
        elif state_only_candidate and _safe_int(previous.get("hp")) <= 1:
            outcome = "hit at HP floor"
        elif state_only_candidate and immediate_hit:
            outcome = "state-only hit candidate"
        else:
            outcome = "state contact candidate"

        record = {
            "version": RESEARCH_VERSION,
            "sampling_mode": SAMPLING_MODE,
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "sequence": seq,
            "frame": int(frame),
            "capture_monotonic": float(now),
            "attacker_slot": attacker_slot,
            "attacker_name": str((attacker or {}).get("name") or ""),
            "attacker_base": _safe_int((attacker or {}).get("base")),
            "attacker_char_id": _safe_int((attacker or {}).get("char_id")),
            "action_id": _safe_int((source or {}).get("action_id"), _safe_int((attacker or {}).get("move_action_id"))),
            "action_name": str((source or {}).get("action_name") or (attacker or {}).get("move_name") or ""),
            "victim_slot": victim_slot,
            "victim_name": str(victim.get("name") or ""),
            "victim_base": _safe_int(victim.get("base")),
            "victim_char_id": _safe_int(victim.get("char_id")),
            "source_kind": source_kind,
            "source_confidence": confidence,
            "source_score": round(float(score), 2),
            "source_age_frames": max(0, int(frame) - _safe_int((source or {}).get("frame"), frame)),
            "native_capture_enabled": bool((source or {}).get("native_capture_enabled")),
            "resolver_hook_state": str((source or {}).get("resolver_hook_state") or "UNKNOWN"),
            "resolver_hook_error": str((source or {}).get("resolver_hook_error") or ""),
            "source_score_reasons": score_reasons,
            "property_a": prop_a,
            "property_a_text": str((source or {}).get("property_a_text") or decode_property_a(prop_a).get("text") or ""),
            "property_a_class": _safe_int((source or {}).get("property_a_class"), prop_a & 0xFF),
            "property_a_result_flags": _safe_int((source or {}).get("property_a_result_flags")) & 0xFFFFFFFF,
            "property_a_result_text": str((source or {}).get("property_a_result_text") or ""),
            "property_a_result_ambiguous": bool((source or {}).get("property_a_result_ambiguous")),
            "property_a_result_candidates": list((source or {}).get("property_a_result_candidates") or []),
            "property_a_final_candidates": list((source or {}).get("property_a_final_candidates") or []),
            "definition_phase_count": _safe_int((source or {}).get("definition_phase_count")),
            "matched_phase_indices": list((source or {}).get("matched_phase_indices") or []),
            "phase_property_a": _safe_int((source or {}).get("phase_property_a")) & 0xFFFFFFFF,
            "phase_property_b": _safe_int((source or {}).get("phase_property_b")) & 0xFFFFFFFF,
            "property_b": prop_b,
            "property_b_text": str((source or {}).get("property_b_text") or decode_property_b(prop_b).get("text") or ""),
            "property_b_route_inference": infer_property_b_routes(prop_b),
            "runtime_status_20": _safe_int((source or {}).get("runtime_status_20")) & 0xFFFFFFFF,
            "actor": _safe_int((source or {}).get("actor")),
            "base_damage": base_damage,
            "base_damage_known": base_damage_known,
            "authored_damage": authored_damage,
            "authored_damage_known": authored_known,
            "damage_calc_output": damage_calc_output,
            "damage_calc_output_known": damage_calc_known,
            "damage_calc_aux": damage_calc_aux,
            "native_damage_calc_complete": calc_complete,
            "applied_damage": applied_damage,
            "resolved_damage": resolved_damage,
            "resolved_damage_known": resolved_known,
            "resolved_aux": resolved_aux,
            "native_damage_complete": native_complete,
            "observed_hp_loss": hp_loss,
            "attributed_damage": attributed_damage,
            "damage_attribution_source": attribution_source,
            "damage_attribution_confident": attribution_confident,
            "contact_hp_delta": hp_loss,
            "last_hit_value": last_hit_value,
            "same_frame_unattributed_damage": unattributed,
            "damage_clamped_by_remaining_hp": clamped_by_remaining_hp,
            "contact_evidence_kind": evidence_kind,
            "state_only_contact_candidate": state_only_candidate,
            "coalesced_contacts_suspected": coalesced,
            "coalesced_contact_count_estimate": 2 if coalesced else 1,
            "followthrough_damage_ignored": 0,
            "final_damage": hp_loss,
            "chip_damage": hp_loss if immediate_block else 0,
            "outcome": outcome,
            "trigger_reasons": reasons,
            "correlation_notes": "; ".join(score_reasons),
            "hp_before": _safe_int(previous.get("hp")),
            "hp_after": _safe_int(victim.get("hp")),
            "terminal_hp": _safe_int(victim.get("hp")),
            "max_hp": _safe_int(previous.get("max_hp")),
            "recoverable_before": recoverable_before,
            "recoverable_after": recoverable_after,
            "recoverable_terminal": recoverable_after,
            "recoverable_delta": recoverable_after - recoverable_before,
            "red_health_generated": max(0, recoverable_after - recoverable_before),
            "attacker_meter_before": _safe_int(attacker_previous.get("meter")),
            "attacker_meter_after": _safe_int((attacker or {}).get("meter")),
            "attacker_meter_terminal": _safe_int((attacker or {}).get("meter")),
            "victim_meter_before": _safe_int(previous.get("meter")),
            "victim_meter_after": _safe_int(victim.get("meter")),
            "victim_meter_terminal": _safe_int(victim.get("meter")),
            "combo_before": _safe_int(attacker_previous.get("combo_count")),
            "combo_after": _safe_int((attacker or {}).get("combo_count")),
            "combo_terminal": _safe_int((attacker or {}).get("combo_count")),
            "combo_scale_before": _safe_float(attacker_previous.get("combo_scale"), 1.0),
            "combo_scale_after": _safe_float((attacker or {}).get("combo_scale"), 1.0),
            "combo_scale_terminal": _safe_float((attacker or {}).get("combo_scale"), 1.0),
            "team_correction": _safe_float((attacker or {}).get("team_correction"), 1.0),
            "baroque_active": bool((attacker or {}).get("baroque_active")),
            "baroque_red_spent": _safe_int((attacker or {}).get("baroque_red_spent")),
            "roll_power_flags": _safe_int((attacker or {}).get("roll_power_flags")),
            "puddle_stacks": _safe_int((attacker or {}).get("puddle_stacks")),
            "victim_action_before": _safe_int(previous.get("action_id")),
            "victim_action_after": _safe_int(victim.get("action_id")),
            "victim_action_terminal": _safe_int(victim.get("action_id")),
            "reaction_phase_before": str(previous.get("reaction_phase") or "neutral"),
            "reaction_phase_after": str(victim.get("reaction_phase") or "neutral"),
            "reaction_phase_terminal": str(victim.get("reaction_phase") or "neutral"),
            "reaction_family_before": _safe_int(previous.get("reaction_family")),
            "reaction_family_after": _safe_int(victim.get("reaction_family")),
            "reaction_family_terminal": _safe_int(victim.get("reaction_family")),
            "position_x_before": _safe_float(previous.get("x")),
            "position_x_after": _safe_float(victim.get("x")),
            "position_x_terminal": _safe_float(victim.get("x")),
            "position_y_before": _normalized_y(previous),
            "position_y_after": _normalized_y(victim),
            "position_y_terminal": _normalized_y(victim),
            "height_before": _normalized_y(previous),
            "height_after": _normalized_y(victim),
            "height_terminal": _normalized_y(victim),
            "victim_before": dict(previous),
            "victim_immediate": dict(victim),
            "attacker_before": dict(attacker_previous or {}),
            "attacker_immediate": dict(attacker or {}),
            "source": dict(source or {}),
            "post_samples": [dict(victim)],
            "attacker_post_samples": [dict(attacker or {})],
            "post_complete": False,
            "_neutral_streak": 0,
        }
        self._assign_series(record, attacker or {}, victim, frame)
        self._contacts.append(record)
        self._pending[seq] = record
        self._last_contact_seq_by_victim[victim_slot] = seq
        if self._emit_console:
            print("[ATKREAD_JSON] " + json.dumps(record, separators=(",", ":"), sort_keys=True), flush=True)

    def _sample_pending(self, states: dict[str, dict], frame: int) -> None:
        complete: list[tuple[int, str]] = []
        for seq, record in list(self._pending.items()):
            victim_slot = str(record.get("victim_slot") or "")
            attacker_slot = str(record.get("attacker_slot") or "")
            state = states.get(victim_slot)
            attacker_state = states.get(attacker_slot)
            if not state or _safe_int(state.get("base")) != _safe_int(record.get("victim_base")):
                complete.append((seq, "victim_changed"))
                continue
            samples = record.setdefault("post_samples", [])
            if not samples or _safe_int(samples[-1].get("frame")) != int(frame):
                samples.append(dict(state))
            attacker_samples = record.setdefault("attacker_post_samples", [])
            if attacker_state and (
                not attacker_samples or _safe_int(attacker_samples[-1].get("frame")) != int(frame)
            ):
                attacker_samples.append(dict(attacker_state))

            neutral = bool(
                str(state.get("reaction_phase") or "neutral") == "neutral"
                and _safe_int(state.get("hitstun")) <= 0
                and _safe_int(state.get("blockstun")) <= 0
                and _safe_int(state.get("action_id")) not in KNOCKDOWN_ACTIONS
                and _safe_int(state.get("action_id")) not in BLOCK_ACTIONS
            )
            record["_neutral_streak"] = _safe_int(record.get("_neutral_streak")) + 1 if neutral else 0
            age = int(frame) - _safe_int(record.get("frame"))
            if age >= POST_SAMPLE_FRAMES:
                complete.append((seq, "observation_window"))
            elif age >= 2 and _safe_int(record.get("_neutral_streak")) >= 2:
                complete.append((seq, "reaction_complete"))

        for seq, reason in complete:
            record = self._pending.pop(seq, None)
            if record is not None:
                self._finalize(record, reason=reason)

    def _finalize(self, record: dict, *, reason: str = "observation_window") -> None:
        if bool(record.get("post_complete")):
            return
        samples = [row for row in (record.get("post_samples") or []) if isinstance(row, dict)]
        attacker_samples = [
            row for row in (record.get("attacker_post_samples") or []) if isinstance(row, dict)
        ]
        before = record.get("victim_before") or {}
        immediate = record.get("victim_immediate") or before
        terminal = samples[-1] if samples else immediate
        attacker_before = record.get("attacker_before") or {}
        attacker_immediate = record.get("attacker_immediate") or attacker_before
        attacker_terminal = attacker_samples[-1] if attacker_samples else attacker_immediate

        observed_hp_loss = max(0, _safe_int(record.get("observed_hp_loss", record.get("contact_hp_delta"))))
        attributed_raw = record.get("attributed_damage")
        contact_damage = (
            max(0, _safe_int(attributed_raw))
            if attributed_raw not in (None, "") else observed_hp_loss
        )
        immediate_hp = _safe_int(immediate.get("hp"), _safe_int(before.get("hp")))
        minimum_follow_hp = min(
            [_safe_int(row.get("hp"), immediate_hp) for row in samples] or [immediate_hp]
        )
        followthrough_damage = max(0, immediate_hp - minimum_follow_hp)
        max_block = max([_safe_int(row.get("blockstun")) for row in samples] or [0])
        max_hit = max([_safe_int(row.get("hitstun")) for row in samples] or [0])
        phases = [str(row.get("reaction_phase") or "neutral") for row in samples]
        actions = [_safe_int(row.get("action_id")) for row in samples]
        action_names = [_action_name(row.get("action_id"), row.get("action_name")) for row in samples]
        unique_phase_path: list[str] = []
        for phase in phases:
            if not unique_phase_path or unique_phase_path[-1] != phase:
                unique_phase_path.append(phase)
        unique_action_path: list[str] = []
        for aid, name in zip(actions, action_names):
            label = f"0x{aid:04X} {name}"
            if not unique_action_path or unique_action_path[-1] != label:
                unique_action_path.append(label)

        paired_points: list[tuple[float, float, float, float, float, float]] = []
        attacker_by_frame = {
            _safe_int(row.get("frame")): row for row in attacker_samples if isinstance(row, dict)
        }
        for victim_row in samples:
            attacker_row = attacker_by_frame.get(_safe_int(victim_row.get("frame")))
            if not attacker_row:
                continue
            attacker_x = _safe_float(attacker_row.get("x"))
            attacker_y = _normalized_y(attacker_row)
            victim_x = _safe_float(victim_row.get("x"))
            victim_y = _normalized_y(victim_row)
            paired_points.append((
                attacker_x,
                attacker_y,
                victim_x,
                victim_y,
                victim_x - attacker_x,
                victim_y - attacker_y,
            ))
        rel_x_terminal = (
            _safe_float(terminal.get("x")) - _safe_float(attacker_terminal.get("x"))
        )
        rel_y_terminal = _normalized_y(terminal) - _normalized_y(attacker_terminal)
        if paired_points:
            xs = [row[4] for row in paired_points]
            ys = [row[5] for row in paired_points]
            x_drift = max(xs) - min(xs)
            y_drift = max(ys) - min(ys)
            relative_drift = max(x_drift, y_drift)
            attacker_travel = 0.0
            victim_travel = 0.0
            motion_mismatch = 0.0
            for prior, current in zip(paired_points, paired_points[1:]):
                adx = current[0] - prior[0]
                ady = current[1] - prior[1]
                vdx = current[2] - prior[2]
                vdy = current[3] - prior[3]
                attacker_travel += (adx * adx + ady * ady) ** 0.5
                victim_travel += (vdx * vdx + vdy * vdy) ** 0.5
                mdx = vdx - adx
                mdy = vdy - ady
                motion_mismatch += (mdx * mdx + mdy * mdy) ** 0.5
            shared_travel = min(attacker_travel, victim_travel)
            mismatch_limit = max(0.12, shared_travel * 0.12)
            close_lock = bool(
                len(paired_points) >= 4
                and sorted(abs(value) for value in xs)[len(xs) // 2] <= 0.18
                and sorted(abs(value) for value in ys)[len(ys) // 2] <= 1.25
                and x_drift <= 0.35
                and y_drift <= 2.75
                and shared_travel >= 1.25
                and motion_mismatch <= mismatch_limit
            )
        else:
            relative_drift = 0.0
            close_lock = False
        series_lock = bool(record.get("series_position_stabilized_observed"))
        stabilized = bool(close_lock or series_lock)

        base_damage_known = bool(record.get("base_damage_known"))
        base_damage = _safe_int(record.get("base_damage")) if base_damage_known else 0
        terminal_frame = _safe_int(terminal.get("frame"), _safe_int(record.get("frame")))
        frame_span = max(1, _safe_int(immediate.get("frame")) - _safe_int(before.get("frame")))
        recoverable_before = _safe_int(before.get("recoverable"))
        recoverable_after = _safe_int(immediate.get("recoverable"))
        recoverable_terminal = _safe_int(terminal.get("recoverable"))
        record.update({
            "post_complete": True,
            "terminal_frame": terminal_frame,
            "terminal_reason": str(reason),
            "followthrough_damage_ignored": followthrough_damage,
            "final_damage": contact_damage,
            "observed_hp_loss": observed_hp_loss,
            "base_to_final_ratio": (
                contact_damage / float(base_damage)
                if base_damage_known and base_damage > 0 else None
            ),
            "terminal_hp": _safe_int(terminal.get("hp")),
            "recoverable_terminal": recoverable_terminal,
            "recoverable_delta": recoverable_after - recoverable_before,
            "red_health_generated": max(0, recoverable_after - recoverable_before),
            "attacker_meter_terminal": _safe_int(attacker_terminal.get("meter")),
            "victim_meter_terminal": _safe_int(terminal.get("meter")),
            "combo_terminal": _safe_int(attacker_terminal.get("combo_count")),
            "combo_scale_terminal": _safe_float(attacker_terminal.get("combo_scale"), 1.0),
            "victim_action_terminal": _safe_int(terminal.get("action_id")),
            "victim_action_path": " > ".join(unique_action_path),
            "reaction_phase_terminal": str(terminal.get("reaction_phase") or "neutral"),
            "reaction_phase_path": " > ".join(unique_phase_path),
            "max_hitstun": max_hit,
            "max_blockstun": max_block,
            "reaction_family_terminal": _safe_int(terminal.get("reaction_family")),
            "position_x_terminal": _safe_float(terminal.get("x")),
            "position_y_terminal": _normalized_y(terminal),
            "height_terminal": _normalized_y(terminal),
            "position_delta_x": _safe_float(immediate.get("x")) - _safe_float(before.get("x")),
            "position_delta_y": _normalized_y(immediate) - _normalized_y(before),
            "velocity_x_est": (
                _safe_float(immediate.get("x")) - _safe_float(before.get("x"))
            ) / frame_span,
            "velocity_y_est": (
                _normalized_y(immediate) - _normalized_y(before)
            ) / frame_span,
            "relative_x_terminal": rel_x_terminal,
            "relative_y_terminal": rel_y_terminal,
            "relative_offset_max_drift": relative_drift,
            "knockdown_observed": any(action in KNOCKDOWN_ACTIONS for action in actions),
            "wall_reaction_observed": any(action in WALL_REACTION_ACTIONS for action in actions),
            "air_recovery_observed": any(action in AIR_RECOVERY_ACTIONS for action in actions),
            "position_stabilized_observed": stabilized,
            "sample_count": len(samples),
        })
        record.pop("_neutral_streak", None)
        self._pending_contact_rows.append(_json_clone(record))
        self._writer.request()

    def _reset_slot_tracking(self, slot: str, *, reason: str) -> None:
        slot = str(slot or "")
        prior_seq = self._last_contact_seq_by_victim.pop(slot, None)
        if prior_seq:
            record = self._pending.pop(_safe_int(prior_seq), None)
            if record is not None:
                self._finalize(record, reason=reason)
        self._recent_sources.pop(slot, None)
        for identity in list(self._last_source_signature):
            if identity and str(identity[0]) == slot:
                self._last_source_signature.pop(identity, None)
        for key in list(self._contact_series):
            if slot in {str(key[0]), str(key[1])}:
                self._contact_series.pop(key, None)
        self._identity_reset_count += 1

    def _reset_all_tracking(self, *, reason: str) -> None:
        for record in list(self._pending.values()):
            self._finalize(record, reason=reason)
        self._pending.clear()
        self._last_contact_seq_by_victim.clear()
        self._recent_sources.clear()
        self._last_source_signature.clear()
        self._contact_series.clear()
        self._prev_states.clear()
        self._current_states.clear()

    def update(self, snaps: dict[str, dict], *, frame: int = 0, now: Optional[float] = None) -> bool:
        timestamp = time.monotonic() if now is None else float(now)
        current: dict[str, dict] = {}
        identity_changed_slots: set[str] = set()
        with self._lock:
            capture = bool(self._capture_enabled)
            prior_frame = int(self._last_frame)
            self._observed_frames += 1
            if prior_frame and int(frame) + 5 < prior_frame:
                self._reset_all_tracking(reason="frame_rewind")
            self._last_frame = int(frame)

            for slot, snap in (snaps or {}).items():
                if not isinstance(snap, dict) or not _safe_int(snap.get("base")):
                    continue
                slot_text = str(slot)
                state = _state_snapshot(slot_text, snap, int(frame))
                previous = self._prev_states.get(slot_text)
                identity_changed = False
                if previous:
                    prior_base = _safe_int(previous.get("base"))
                    current_base = _safe_int(state.get("base"))
                    prior_char = _safe_int(previous.get("char_id"))
                    current_char = _safe_int(state.get("char_id"))
                    prior_max = _safe_int(previous.get("max_hp"))
                    current_max = _safe_int(state.get("max_hp"))
                    identity_changed = bool(
                        prior_base != current_base
                        or (
                            prior_char > 0
                            and current_char > 0
                            and prior_char != current_char
                        )
                        or (
                            prior_max > 0
                            and current_max > 0
                            and prior_max != current_max
                        )
                    )
                if identity_changed:
                    self._reset_slot_tracking(slot_text, reason="fighter_identity_changed")
                    identity_changed_slots.add(slot_text)

                current[slot_text] = state
                sources = _source_from_snap(slot_text, snap, int(frame))
                self._remember_sources(slot_text, sources, int(frame), capture)

            if capture:
                for slot, victim in current.items():
                    if slot in identity_changed_slots:
                        continue
                    previous = self._prev_states.get(slot)
                    if not previous or _safe_int(previous.get("base")) != _safe_int(victim.get("base")):
                        continue
                    trigger, reasons, metrics = self._trigger(previous, victim)
                    if trigger:
                        self._new_contact(
                            previous,
                            victim,
                            current,
                            int(frame),
                            timestamp,
                            reasons,
                            metrics,
                        )
                    elif bool(metrics.get("echo_candidate")):
                        self._echo_suppressed_count += 1
                self._current_states = {key: dict(value) for key, value in current.items()}
                self._sample_pending(current, int(frame))
            else:
                self._current_states = {key: dict(value) for key, value in current.items()}
            self._prev_states = {key: dict(value) for key, value in current.items()}
        return True

    def _flush_pending(self) -> None:
        with self._lock:
            contacts = list(self._pending_contact_rows)
            sources = list(self._pending_source_rows)
            self._pending_contact_rows.clear()
            self._pending_source_rows.clear()
        if contacts:
            _append_csv(self.contact_csv_path, contacts, CONTACT_CSV_FIELDS)
            try:
                self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
                with self.jsonl_path.open("a", encoding="utf-8") as handle:
                    for row in contacts:
                        handle.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
            except Exception:
                pass
        if sources:
            _append_csv(self.source_csv_path, sources, SOURCE_CSV_FIELDS)

    def snapshot(self, *, limit: int = 750) -> dict:
        with self._lock:
            return {
                "version": RESEARCH_VERSION,
                "sampling_mode": SAMPLING_MODE,
                "read_only": True,
                "enabled": bool(self._capture_enabled),
                "status": "CAPTURING" if self._capture_enabled else "PAUSED",
                "contact_count": len(self._contacts),
                "source_count": len(self._sources),
                "pending_post_count": len(self._pending),
                "observed_frames": int(self._observed_frames),
                "last_frame": int(self._last_frame),
                "echo_suppressed_count": int(self._echo_suppressed_count),
                "identity_reset_count": int(self._identity_reset_count),
                "contacts": _json_clone(list(self._contacts)[-max(1, int(limit)):]),
                "sources": _json_clone(list(self._sources)[-max(1, int(limit) * 2):]),
                "contact_csv_path": str(self.contact_csv_path),
                "source_csv_path": str(self.source_csv_path),
                "jsonl_path": str(self.jsonl_path),
            }

    def clear_history(self) -> None:
        with self._lock:
            self._contacts.clear()
            self._sources.clear()
            self._pending.clear()
            self._last_source_signature.clear()
            self._last_contact_seq_by_victim.clear()
            self._contact_series.clear()
            self._echo_suppressed_count = 0
            self._identity_reset_count = 0

    def export_contacts_csv(self, path: str | os.PathLike[str]) -> bool:
        with self._lock:
            rows = _json_clone(list(self._contacts))
        target = Path(path)
        try:
            if target.exists():
                target.unlink()
        except Exception:
            pass
        return _append_csv(target, rows, CONTACT_CSV_FIELDS)

    def export_sources_csv(self, path: str | os.PathLike[str]) -> bool:
        with self._lock:
            rows = _json_clone(list(self._sources))
        target = Path(path)
        try:
            if target.exists():
                target.unlink()
        except Exception:
            pass
        return _append_csv(target, rows, SOURCE_CSV_FIELDS)

    def export_json(self, path: str | os.PathLike[str]) -> bool:
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(self.snapshot(limit=MAX_CONTACT_HISTORY), indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return True
        except Exception:
            return False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for record in list(self._pending.values()):
                self._finalize(record, reason="shutdown")
            self._pending.clear()
            self._capture_enabled = False
        self._writer.close(final_callback=self._flush_pending, timeout=1.5)


_SINGLETON: Optional[ReadOnlyAttackResearch] = None
_SINGLETON_LOCK = threading.Lock()


def get_attack_resolver_research() -> ReadOnlyAttackResearch:
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            emit = str(os.environ.get("TVC_ATTACK_RESEARCH_DEBUG", "0")).strip().lower() in {"1", "true", "yes", "on"}
            _SINGLETON = ReadOnlyAttackResearch(emit_console=emit)
        return _SINGLETON


def shutdown_attack_resolver_research() -> None:
    global _SINGLETON
    with _SINGLETON_LOCK:
        instance = _SINGLETON
        _SINGLETON = None
    if instance is not None:
        instance.close()


atexit.register(shutdown_attack_resolver_research)


__all__ = [
    "ReadOnlyAttackResearch",
    "get_attack_resolver_research",
    "shutdown_attack_resolver_research",
    "infer_property_b_routes",
    "CONTACT_CSV_FIELDS",
    "SOURCE_CSV_FIELDS",
]
