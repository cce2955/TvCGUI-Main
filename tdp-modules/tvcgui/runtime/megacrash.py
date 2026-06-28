"""Extracted runtime module from :mod:`main`.

This module deliberately preserves the original function names and behavior so
`main.py` can remain a compatibility-oriented entry point while the subsystem
has a focused home.
"""
from __future__ import annotations

import csv
import json
import random
import time

from app_paths import resource_path
from constants import ATT_ID_OFF_PRIMARY
from dolphin_io import addr_in_ram, rd8, wd32
from fighter import dist2

try:
    import runtime_patch_manager as runtime_pm
except Exception:
    runtime_pm = None

TARGET_FPS = 60

# Megacrash training mode. The old one-click global poke proved that writing
# the live action/move-id field to 448 can force Megacrash. The trainer keeps
# that same write primitive, but only pulses it on point characters during
# hitstun when the opponent advances to a new combo label.
MEGACRASH_MOVE_ID = 448
MEGACRASH_TRAINER_CONFIG_FILE = "megacrash_trainer.json"
MEGACRASH_TRAINER_DEFAULT_CHANCE = 0
MEGACRASH_TRAINER_DEFAULT_MODE = "percent"
MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES = 0
MEGACRASH_TRAINER_MAX_DELAY_FRAMES = 300
MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC = 2.0
MEGACRASH_TRAINER_MAX_COOLDOWN_SEC = 60.0
MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL = ""
MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE = "any"
MEGACRASH_TRAINER_DEFAULT_TARGET_OCCURRENCE = 1

# Verified from the 2026-06-21 Shinkuu dump set. This global byte rose
# 08 -> 15 -> 19 -> 20 across Shinkuu hits and reset to 00 after the combo.
# Unlike the earlier per-fighter candidates, this source covers all point slots
# and gives one true hit-edge stream for the active combo.
MEGACRASH_GLOBAL_COMBO_COUNTER_ADDR = 0x809BDDB3
MEGACRASH_GLOBAL_COMBO_COUNTER_LABEL = "Global 0x809BDDB3"

# Legacy candidate offsets remain documented for comparison only. Runtime no
# longer relies on them, because the fixed global counter is the authoritative
# source for repeated labels and multi-hit moves.
MEGACRASH_COMBO_COUNTER_OFFSETS_BY_SLOT = {
    "P1-C1": (0x11C7, 0x20DB),
    "P2-C1": (0x11E7,),
}
MEGACRASH_TRAINER_MAX_TARGET_OCCURRENCE = 99
MEGACRASH_TRAINER_PULSE_SEC = 0.08
MEGACRASH_TRAINER_WRITE_OFFSETS = (ATT_ID_OFF_PRIMARY,)
MEGACRASH_TRAINER_CHANCE_PRESETS = (0, 5, 10, 15, 20, 25, 33, 50, 75, 100)
MEGACRASH_SUPPORT_STATE_IDS = {420, 424, 425, 426, 427, 428, 430, 431, 432, 433, 0x01A1, 0x01A8, 0x01AE}
# Megacrash needs its own victim-state family. The old REACTION_STATES subset
# covered mostly grounded reactions, so airborne hit/relaunch states never armed
# the trainer. Keep the broader set local to Megacrash; do not alter generic
# logging/mission state behavior.
MEGACRASH_REACTION_STATES = {
    48, 49, 50, 52, 53, 60, 61, 62, 64, 65, 66, 73, 74, 75, 76, 79, 80,
    81, 82, 83, 88, 89, 90, 91, 92, 94, 95, 96, 97, 98, 101, 102, 105,
    106, 142, 449,
    4562, 4565, 4568, 4571, 4573, 4608, 4609, 4610, 4611, 4613, 4614,
    4615, 4616, 4617, 4618, 4619, 4620, 4621, 4622, 4623, 4625, 4631,
}


def _u32be_bytes(value: int) -> bytes:
    value = int(value) & 0xFFFFFFFF
    return bytes([
        (value >> 24) & 0xFF,
        (value >> 16) & 0xFF,
        (value >> 8) & 0xFF,
        value & 0xFF,
    ])


def _clamp_megacrash_chance(value) -> int:
    try:
        value = int(round(float(value)))
    except Exception:
        value = MEGACRASH_TRAINER_DEFAULT_CHANCE
    return max(0, min(100, value))


def _clamp_megacrash_delay_frames(value) -> int:
    try:
        value = int(round(float(value)))
    except Exception:
        value = MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES
    return max(0, min(MEGACRASH_TRAINER_MAX_DELAY_FRAMES, value))


def _clamp_megacrash_cooldown_sec(value) -> float:
    try:
        value = float(value)
    except Exception:
        value = MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC
    value = max(0.0, min(MEGACRASH_TRAINER_MAX_COOLDOWN_SEC, value))
    return round(value, 2)


def _clamp_megacrash_target_occurrence(value) -> int:
    try:
        value = int(round(float(value)))
    except Exception:
        value = MEGACRASH_TRAINER_DEFAULT_TARGET_OCCURRENCE
    return max(1, min(MEGACRASH_TRAINER_MAX_TARGET_OCCURRENCE, value))


def _clean_megacrash_attacker_scope(value) -> str:
    value = str(value or "").strip()
    if not value or value.lower() in {"any", "all", "*"}:
        return MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE
    return value[:64]


def _clean_megacrash_target_label(value) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    return text[:96]


def _megacrash_target_tokens(value) -> list[str]:
    text = _clean_megacrash_target_label(value)
    if not text or text.strip().lower() in {"*", "any", "all"}:
        return []
    raw = text.replace(";", ",").replace("|", ",").split(",")
    return [part.strip() for part in raw if part.strip()]


def _megacrash_norm_label(value) -> str:
    text = str(value or "").replace("\u00a0", " ").replace("_", " ").replace("-", " ").strip().casefold()
    while "  " in text:
        text = text.replace("  ", " ")
    return text


def _megacrash_tight_label(value) -> str:
    text = _megacrash_norm_label(value)
    return "".join(ch for ch in text if ch.isalnum())


_MEGACRASH_LABEL_ID_CACHE: dict[str, set[int]] | None = None


def _megacrash_label_id_cache() -> dict[str, set[int]]:
    """Map normalized move labels/aliases from the CSV to their move IDs.

    This lets the trainer target labels with spaces like "Knee A" even if the
    live HUD snapshot is carrying the move as an ID/fallback label for a frame.
    "5A" and other compact labels still work the same way.
    """
    global _MEGACRASH_LABEL_ID_CACHE
    if _MEGACRASH_LABEL_ID_CACHE is not None:
        return _MEGACRASH_LABEL_ID_CACHE

    out: dict[str, set[int]] = {}

    def add(label, mid) -> None:
        try:
            mid_i = int(mid)
        except Exception:
            return
        norm = _megacrash_norm_label(label)
        tight = _megacrash_tight_label(label)
        for key in (norm, tight):
            if key:
                out.setdefault(key, set()).add(mid_i)

    csv_path = resource_path("move_id_map_charagnostic.csv")
    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row:
                    continue
                first = str(row[0] or "").strip()
                if not first or first.startswith("#"):
                    continue
                try:
                    mid = int(float(first))
                except Exception:
                    continue
                # Primary label plus legacy/example label columns.  This is
                # intentionally broad because several specials have display
                # aliases that differ from the canonical column.
                for idx in (2, 3, 4, 5):
                    if idx < len(row):
                        add(row[idx], mid)
    except Exception as e:
        print(f"[megacrash trainer] label alias cache unavailable: {e!r}")

    _MEGACRASH_LABEL_ID_CACHE = out
    return out


_MEGACRASH_LABEL_OPTIONS_CACHE: dict[int, list[str]] | None = None

def _megacrash_label_options_for_char(char_id: int | None) -> list[str]:
    """Return known move labels for one roster character plus universal rows.

    The GUI uses this for a readonly dropdown. Matching still goes through the
    normal alias/id resolver, so a selected friendly label remains resilient to
    a live HUD fallback name or temporary raw-ID display.
    """
    global _MEGACRASH_LABEL_OPTIONS_CACHE
    if _MEGACRASH_LABEL_OPTIONS_CACHE is None:
        catalog: dict[int, set[str]] = {}
        csv_path = resource_path("move_id_map_charagnostic.csv")
        try:
            with open(csv_path, newline="", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row:
                        continue
                    first = str(row[0] or "").strip()
                    if not first or first.startswith("#") or len(row) < 3:
                        continue
                    try:
                        owner = int(str(row[-1]).strip())
                    except Exception:
                        continue
                    label = _clean_megacrash_target_label(row[2])
                    low = label.casefold()
                    if not label or low in {"anim_--", "unknown", "idle"}:
                        continue
                    catalog.setdefault(owner, set()).add(label)
        except Exception as e:
            print(f"[megacrash trainer] label option catalog unavailable: {e!r}")
        _MEGACRASH_LABEL_OPTIONS_CACHE = {
            key: sorted(values, key=lambda s: (s.casefold(), s))
            for key, values in catalog.items()
        }

    labels: set[str] = set(_MEGACRASH_LABEL_OPTIONS_CACHE.get(100, []))
    try:
        cid = int(char_id or 0)
    except Exception:
        cid = 0
    if cid:
        labels.update(_MEGACRASH_LABEL_OPTIONS_CACHE.get(cid, []))
    return sorted(labels, key=lambda s: (s.casefold(), s))


def _megacrash_roster_context(snaps: dict) -> list[dict]:
    """Snapshot roster slots for the readonly Megacrash source/label pickers."""
    out: list[dict] = []
    for slot in ("P1-C1", "P1-C2", "P2-C1", "P2-C2"):
        snap = (snaps or {}).get(slot) or {}
        if not isinstance(snap, dict):
            continue
        try:
            char_id = int(snap.get("id") or snap.get("csv_char_id") or snap.get("char_id") or 0)
        except Exception:
            char_id = 0
        name = str(snap.get("name") or snap.get("char_name") or "Unknown").strip() or "Unknown"
        out.append({
            "scope": f"slot:{slot}",
            "slot": slot,
            "name": name,
            "char_id": char_id,
            "labels": _megacrash_label_options_for_char(char_id),
        })
    return out


def _megacrash_label_matches(target_label, atk_label, atk_id) -> bool:
    tokens = _megacrash_target_tokens(target_label)
    if not tokens:
        return True

    label = str(atk_label or "").strip()
    candidates = set()
    if label:
        candidates.update({
            label.casefold(),
            _megacrash_norm_label(label),
            _megacrash_tight_label(label),
        })

    try:
        mid = int(atk_id) if atk_id is not None else None
    except Exception:
        mid = None
    if mid is not None:
        candidates.update({
            str(mid).casefold(),
            f"0x{mid:04x}",
            f"0x{mid:x}",
            f"{mid:04x}",
            f"{mid:x}",
        })

    alias_cache = _megacrash_label_id_cache()
    for token in tokens:
        token_norm = _megacrash_norm_label(token)
        token_tight = _megacrash_tight_label(token)
        if token.casefold() in candidates or token_norm in candidates or token_tight in candidates:
            return True
        if mid is not None:
            alias_ids = set()
            if token_norm:
                alias_ids.update(alias_cache.get(token_norm, set()))
            if token_tight:
                alias_ids.update(alias_cache.get(token_tight, set()))
            if mid in alias_ids:
                return True
    return False


def _megacrash_target_summary(value) -> str:
    text = _clean_megacrash_target_label(value)
    if not text or text.lower() in {"*", "any", "all"}:
        return "Any label"
    if len(text) > 28:
        return f"Label {text[:25]}..."
    return f"Label {text}"


def _normalize_megacrash_mode(value) -> str:
    value = str(value or "").strip().lower()
    if value in {"target", "targeted", "delay", "delayed"}:
        return "targeted"
    return "percent"


def _megacrash_mode_summary(state: dict) -> str:
    mode = _normalize_megacrash_mode(state.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE))
    cd = _clamp_megacrash_cooldown_sec(state.get("cooldown_sec", MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC))
    target_txt = _megacrash_target_summary(state.get("target_label", MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL))
    scope = _clean_megacrash_attacker_scope(state.get("attacker_scope", MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE))
    source_txt = "any point" if scope == MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE else scope.replace("slot:", "")
    nth = _clamp_megacrash_target_occurrence(state.get("target_occurrence", MEGACRASH_TRAINER_DEFAULT_TARGET_OCCURRENCE))
    cd_txt = f" cd {cd:g}s"
    trigger_txt = f"{source_txt} {target_txt} #{nth}"
    if mode == "targeted":
        return f"{trigger_txt} +{_clamp_megacrash_delay_frames(state.get('delay_frames', 0))}f{cd_txt}"
    return f"{trigger_txt} in combo • roll {_clamp_megacrash_chance(state.get('chance', MEGACRASH_TRAINER_DEFAULT_CHANCE))}%{cd_txt}"


def _load_megacrash_trainer_config() -> dict:
    cfg = {
        # Safety rule: Megacrash never auto-enables on app startup.
        # Persist trainer settings, but require an explicit ON click
        # every run so an exported build or stale JSON cannot force bursts by
        # surprise.
        "enabled": False,
        "mode": MEGACRASH_TRAINER_DEFAULT_MODE,
        "chance": MEGACRASH_TRAINER_DEFAULT_CHANCE,
        "delay_frames": MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES,
        "cooldown_sec": MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC,
        "target_label": MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL,
        "attacker_scope": MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE,
        "target_occurrence": MEGACRASH_TRAINER_DEFAULT_TARGET_OCCURRENCE,
    }
    try:
        with open(MEGACRASH_TRAINER_CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            # Intentionally ignore raw["enabled"].  The trainer is always OFF
            # at startup, even if the previous session exited while ON.
            cfg["mode"] = _normalize_megacrash_mode(raw.get("mode", cfg["mode"]))
            # Startup safety: Megacrash always launches OFF with a 0% random roll,
            # even if an old config was saved at 25/50/100%.
            cfg["chance"] = MEGACRASH_TRAINER_DEFAULT_CHANCE
            cfg["delay_frames"] = _clamp_megacrash_delay_frames(raw.get("delay_frames", cfg["delay_frames"]))
            cfg["cooldown_sec"] = _clamp_megacrash_cooldown_sec(raw.get("cooldown_sec", cfg["cooldown_sec"]))
            cfg["target_label"] = _clean_megacrash_target_label(raw.get("target_label", cfg["target_label"]))
            cfg["attacker_scope"] = _clean_megacrash_attacker_scope(raw.get("attacker_scope", cfg["attacker_scope"]))
            cfg["target_occurrence"] = _clamp_megacrash_target_occurrence(raw.get("target_occurrence", cfg["target_occurrence"]))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[megacrash trainer] config load failed: {e!r}")
    return cfg


def _save_megacrash_trainer_config(state: dict) -> None:
    try:
        save_src = state
        if isinstance(state, dict) and state.get("mission_override_active"):
            saved = state.get("mission_saved_settings")
            if isinstance(saved, dict) and saved:
                save_src = saved
        payload = {
            # Do not persist an enabled state. Megacrash must default OFF on
            # every launch, while the rest of the trainer settings persist.
            # Mission-scoped overrides are not persisted; save the selected
            # pre-mission settings if the trainer window is opened mid-trial.
            "enabled": False,
            "mode": _normalize_megacrash_mode(save_src.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE)),
            "chance": _clamp_megacrash_chance(save_src.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE)),
            "delay_frames": _clamp_megacrash_delay_frames(save_src.get("delay_frames", MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES)),
            "cooldown_sec": _clamp_megacrash_cooldown_sec(save_src.get("cooldown_sec", MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC)),
            "target_label": _clean_megacrash_target_label(save_src.get("target_label", MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL)),
            "attacker_scope": _clean_megacrash_attacker_scope(save_src.get("attacker_scope", MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE)),
            "target_occurrence": _clamp_megacrash_target_occurrence(save_src.get("target_occurrence", MEGACRASH_TRAINER_DEFAULT_TARGET_OCCURRENCE)),
        }
        with open(MEGACRASH_TRAINER_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        print(f"[megacrash trainer] config save failed: {e!r}")


def _extract_mission_megacrash_setup(payload: dict) -> dict:
    if not isinstance(payload, dict) or not payload.get("active"):
        return {}

    raw = (
        payload.get("active_mission_setup_megacrash_trainer")
        or payload.get("active_mission_megacrash_trainer")
        or payload.get("setup_megacrash_trainer")
        or {}
    )

    if not isinstance(raw, dict):
        return {}

    out = dict(raw)
    out["enabled"] = bool(out.get("enabled", True))
    out["mode"] = _normalize_megacrash_mode(out.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE))
    out["chance"] = _clamp_megacrash_chance(out.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE))
    out["delay_frames"] = _clamp_megacrash_delay_frames(out.get("delay_frames", MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES))
    out["cooldown_sec"] = _clamp_megacrash_cooldown_sec(out.get("cooldown_sec", MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC))
    out["target_label"] = _clean_megacrash_target_label(out.get("target_label", MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL))
    out["attacker_scope"] = _clean_megacrash_attacker_scope(out.get("attacker_scope", MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE))
    out["target_occurrence"] = _clamp_megacrash_target_occurrence(out.get("target_occurrence", MEGACRASH_TRAINER_DEFAULT_TARGET_OCCURRENCE))
    return out


def _clear_megacrash_runtime_state(state: dict) -> None:
    try:
        state.setdefault("last_combo_keys", {}).clear()
        state.setdefault("pulses", {}).clear()
        state.setdefault("scheduled_triggers", {}).clear()
        state.setdefault("match_occurrences", {}).clear()
        state.setdefault("combo_counter_probes", {}).clear()
        state["global_combo_counter_probe"] = {"last": None, "seen_zero": False}
        state["occurrence_counter"] = 0
        state["live_combo_counter"] = 0
        state["live_combo_counter_source"] = ""
        state["cooldown_until"] = 0.0
    except Exception:
        pass


def _sync_mission_megacrash_trainer(state: dict, payload: dict) -> dict:
    'Apply mission-scoped Megacrash Trainer setup, then restore operator settings.\n\n    Mission JSON can provide setup_megacrash_trainer.  This lets trials that\n    need a controlled burst turn the trainer on only while that mission is the\n    active mission.  It never persists enabled=True and it restores the selected\n    normal Megacrash settings when the mission changes or mission mode is off.\n    '
    if not isinstance(state, dict):
        state = _load_megacrash_trainer_config()

    setup = _extract_mission_megacrash_setup(payload)
    mission_key = None
    if setup:
        mission_key = (
            payload.get("slot"),
            payload.get("character"),
            payload.get("active_mission_id"),
        )

    current_key = state.get("mission_override_key")

    if not setup:
        if current_key is not None:
            saved = state.pop("mission_saved_settings", {}) or {}
            for key, value in saved.items():
                state[key] = value
            state.pop("mission_override_key", None)
            state.pop("mission_override_name", None)
            state["mission_override_active"] = False
            _clear_megacrash_runtime_state(state)
            print("[megacrash trainer] mission override restored saved settings")
        return state

    if current_key != mission_key:
        saved = {
            "enabled": bool(state.get("enabled", False)),
            "mode": _normalize_megacrash_mode(state.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE)),
            "chance": _clamp_megacrash_chance(state.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE)),
            "delay_frames": _clamp_megacrash_delay_frames(state.get("delay_frames", MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES)),
            "cooldown_sec": _clamp_megacrash_cooldown_sec(state.get("cooldown_sec", MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC)),
            "target_label": _clean_megacrash_target_label(state.get("target_label", MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL)),
            "attacker_scope": _clean_megacrash_attacker_scope(state.get("attacker_scope", MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE)),
            "target_occurrence": _clamp_megacrash_target_occurrence(state.get("target_occurrence", MEGACRASH_TRAINER_DEFAULT_TARGET_OCCURRENCE)),
        }
        state["mission_saved_settings"] = saved
        state["mission_override_key"] = mission_key
        state["mission_override_name"] = str(payload.get("active_mission_name") or payload.get("active_mission_id") or "mission")
        _clear_megacrash_runtime_state(state)
        print(
            "[megacrash trainer] mission override "
            f"{payload.get('active_mission_id')}: "
            f"{setup.get('mode')} label={setup.get('target_label') or 'any'} "
            f"+{setup.get('delay_frames')}f cd={setup.get('cooldown_sec')}s"
        )

    state["mission_override_active"] = True
    state["enabled"] = bool(setup.get("enabled", True))
    state["mode"] = _normalize_megacrash_mode(setup.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE))
    state["chance"] = _clamp_megacrash_chance(setup.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE))
    state["delay_frames"] = _clamp_megacrash_delay_frames(setup.get("delay_frames", MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES))
    state["cooldown_sec"] = _clamp_megacrash_cooldown_sec(setup.get("cooldown_sec", MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC))
    state["target_label"] = _clean_megacrash_target_label(setup.get("target_label", MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL))
    state["attacker_scope"] = _clean_megacrash_attacker_scope(setup.get("attacker_scope", MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE))
    state["target_occurrence"] = _clamp_megacrash_target_occurrence(setup.get("target_occurrence", MEGACRASH_TRAINER_DEFAULT_TARGET_OCCURRENCE))
    return state


def _cycle_megacrash_chance(current: int) -> int:
    cur = _clamp_megacrash_chance(current)
    presets = list(MEGACRASH_TRAINER_CHANCE_PRESETS)
    for value in presets:
        if value > cur:
            return value
    return presets[0]


def _snap_action_id(snap: dict) -> int | None:
    if not isinstance(snap, dict):
        return None
    for key in ("mv_id_display", "attA", "attB", "move_id", "cur_anim", "current_anim"):
        try:
            value = snap.get(key)
            if value is not None:
                return int(value)
        except Exception:
            pass
    return None


def _snap_primary_action_id(snap: dict) -> int | None:
    """Return the live primary move/action word only.

    The trainer writes Megacrash through ATT_ID_OFF_PRIMARY (base+0x1E8).
    Using the display id here is unsafe because display id falls back from
    attA to attB; attB can mirror/stale a reaction value and make the
    attacking point look like a victim.
    """
    if not isinstance(snap, dict):
        return None
    try:
        value = snap.get("attA")
        return int(value) if value is not None else None
    except Exception:
        return None


def _snap_is_hitstun_primary(snap: dict) -> bool:
    """Megacrash victim gate: grounded + airborne/relaunch reaction families."""
    mid = _snap_primary_action_id(snap)
    return bool(mid in MEGACRASH_REACTION_STATES if mid is not None else False)


def _megacrash_attacker_scope_matches(state: dict, atk_slot: str, atk_snap: dict) -> bool:
    scope = _clean_megacrash_attacker_scope((state or {}).get("attacker_scope", MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE))
    if scope == MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE:
        return True
    expected = scope.replace("slot:", "", 1)
    live_slot = str((atk_snap or {}).get("slotname") or (atk_snap or {}).get("slot_label") or atk_slot or "")
    return bool(expected and live_slot == expected)


def _opponent_teamtag(teamtag: str) -> str:
    return "P2" if str(teamtag) == "P1" else "P1"


def _snap_move_label(snap: dict) -> str:
    if not isinstance(snap, dict):
        return ""
    label = str(snap.get("mv_label") or "").strip()
    if label:
        return label
    mid = _snap_action_id(snap)
    return f"0x{mid:04X}" if mid is not None else ""


def _is_support_or_assist_snap(snap: dict) -> bool:
    if not isinstance(snap, dict):
        return True
    label = str(snap.get("mv_label") or "").strip().lower()
    mid = _snap_action_id(snap)
    ko_state = bool(("ko" in label) or ((snap.get("cur") or 0) <= 0))
    return bool(
        ko_state
        or (mid in MEGACRASH_SUPPORT_STATE_IDS if mid is not None else False)
        or ("assist" in label)
        or ("tag out" in label)
        or ("tag in taunt" in label)
    )


def _team_point_slot_for_megacrash(teamtag: str, snaps: dict) -> str | None:
    """Return the team's point slot for trainer purposes.

    Normal matches are C1 point / C2 assist. If C1 is visibly in a support/tag/KO
    state while C2 is not, treat C2 as the point so swapped teams still work.
    This intentionally keeps assists from being selected when they get clipped.
    """
    c1_key = f"{teamtag}-C1"
    c2_key = f"{teamtag}-C2"
    c1 = snaps.get(c1_key)
    c2 = snaps.get(c2_key)
    if c1 and not _is_support_or_assist_snap(c1):
        return c1_key
    if c2 and not _is_support_or_assist_snap(c2):
        return c2_key
    if c1:
        return c1_key
    if c2:
        return c2_key
    return None


def _nearest_opponent_snap(vic_snap: dict, snaps: dict) -> dict | None:
    if not isinstance(vic_snap, dict):
        return None
    vic_team = vic_snap.get("teamtag")
    candidates = [s for s in snaps.values() if isinstance(s, dict) and s.get("teamtag") != vic_team]
    if not candidates:
        return None

    best_snap = None
    best_d2 = None
    for cand in candidates:
        try:
            d2v = dist2(vic_snap, cand)
        except Exception:
            d2v = None
        if d2v is None:
            continue
        if best_d2 is None or d2v < best_d2:
            best_d2 = d2v
            best_snap = cand
    return best_snap or candidates[0]


def _megacrash_cooldown_remaining(state: dict, now: float) -> float:
    try:
        cooldown_until = float(state.get("cooldown_until", 0.0) or 0.0)
    except Exception:
        cooldown_until = 0.0
    return max(0.0, cooldown_until - float(now))


def _megacrash_occurrence_key(victim_base: int | str | None) -> str:
    """Stable key for one victim's current hitstun/combo sequence."""
    try:
        return str(int(victim_base or 0))
    except Exception:
        return str(victim_base or "")


def _megacrash_combo_occurrences(state: dict) -> dict:
    """Return the per-victim combo occurrence map, repairing old runtime state."""
    current = state.get("match_occurrences") if isinstance(state, dict) else None
    if not isinstance(current, dict):
        current = {}
        try:
            state["match_occurrences"] = current
        except Exception:
            pass
    return current


def _megacrash_refresh_occurrence_display(state: dict) -> None:
    """Keep the legacy UI count meaningful: show the most recent live combo count."""
    try:
        entries = _megacrash_combo_occurrences(state).values()
        counts = []
        for entry in entries:
            if isinstance(entry, dict):
                counts.append(max(0, int(entry.get("count", 0) or 0)))
        state["occurrence_counter"] = max(counts) if counts else 0
    except Exception:
        try:
            state["occurrence_counter"] = 0
        except Exception:
            pass


def _megacrash_combo_counter_probes(state: dict) -> dict:
    """Per-victim records for the verified live combo-counter byte."""
    current = state.get("combo_counter_probes") if isinstance(state, dict) else None
    if not isinstance(current, dict):
        current = {}
        try:
            state["combo_counter_probes"] = current
        except Exception:
            pass
    return current


def _megacrash_global_combo_probe(state: dict) -> dict:
    """Return the single global combo-counter probe record."""
    probe = state.get("global_combo_counter_probe") if isinstance(state, dict) else None
    if not isinstance(probe, dict):
        probe = {"last": None, "seen_zero": False}
        try:
            state["global_combo_counter_probe"] = probe
        except Exception:
            pass
    return probe


def _megacrash_reset_occurrences_for_global_combo_end(state: dict) -> None:
    """The global byte returning to zero is the authoritative combo boundary."""
    try:
        state.setdefault("match_occurrences", {}).clear()
        state.setdefault("last_combo_keys", {}).clear()
        state["occurrence_counter"] = 0
        state["last_matching_label"] = ""
        state["last_matching_combo_base"] = ""
    except Exception:
        pass
    _megacrash_refresh_occurrence_display(state)


def _megacrash_read_global_combo_counter(state: dict, *, consume_only: bool = False) -> dict | None:
    """Read the fixed global hit counter and expose a delta-based hit event.

    A rising counter means one or more actual hits occurred.  ``delta`` is
    deliberately preserved: multi-hit labels such as Shinkuu contribute every
    hit, while a label that reappears after another label continues adding to
    its existing per-combo total.  The first read while already mid-combo is
    only a baseline; it does not retroactively count unknown earlier hits.
    """
    probe = _megacrash_global_combo_probe(state)
    try:
        raw = rd8(MEGACRASH_GLOBAL_COMBO_COUNTER_ADDR)
    except Exception:
        raw = None
    if raw is None:
        return None
    try:
        current = int(raw) & 0xFF
    except Exception:
        return None
    try:
        previous = int(probe.get("last")) & 0xFF if probe.get("last") is not None else None
    except Exception:
        previous = None

    reset = False
    fresh = False
    delta = 0
    baseline = previous is None
    if previous is None:
        # The initial read only arms the tracker. If it is zero, the next rise
        # belongs to a fresh combo; if it is already nonzero, avoid assigning
        # earlier unseen hits to whichever label happens to be on screen.
        probe["seen_zero"] = bool(current == 0)
    elif current == 0:
        reset = bool(previous != 0)
        probe["seen_zero"] = True
    elif previous == 0:
        # First observed rise after a known reset. Tick gaps can legitimately
        # skip from 0 to N, so count all N hits on the active label.
        fresh = True
        delta = int(current)
        probe["seen_zero"] = False
    elif current > previous:
        fresh = True
        delta = int(current - previous)
        probe["seen_zero"] = False
    elif current < previous:
        # A nonzero drop is treated as a new counter sequence/wrap. Do not
        # carry the old combo's selected-label total into this new sequence.
        reset = True
        fresh = True
        delta = int(current)
        probe["seen_zero"] = False

    probe.update({"last": current, "address": MEGACRASH_GLOBAL_COMBO_COUNTER_ADDR})
    state["live_combo_counter"] = current
    state["live_combo_counter_source"] = MEGACRASH_GLOBAL_COMBO_COUNTER_LABEL
    if reset:
        _megacrash_reset_occurrences_for_global_combo_end(state)

    return {
        "fresh": False if consume_only else bool(fresh),
        "delta": 0 if consume_only else max(0, int(delta)),
        "value": current,
        "reset": bool(reset),
        "baseline": bool(baseline),
        "source": MEGACRASH_GLOBAL_COMBO_COUNTER_LABEL,
    }


def _megacrash_counter_offsets_for_slot(slot: str) -> tuple[int, ...]:
    return tuple(MEGACRASH_COMBO_COUNTER_OFFSETS_BY_SLOT.get(str(slot or ""), ()))


def _megacrash_read_live_combo_counter(
    state: dict, victim_base: int, attacker_slot: str, attacker_snap: dict, *, consume_only: bool = False
) -> dict | None:
    """Read the confirmed combo byte and report whether a new hit occurred.

    A repeated move label (2A -> 2A -> 2A) can now produce distinct events
    because the byte advances 1 -> 2 -> 3. Unknown slot layouts return None
    and therefore retain the prior label-change behavior.
    """
    offsets = _megacrash_counter_offsets_for_slot(attacker_slot)
    if not offsets:
        return None
    try:
        attacker_base = int((attacker_snap or {}).get("base") or 0)
    except Exception:
        attacker_base = 0
    if not attacker_base:
        return None

    key = _megacrash_occurrence_key(victim_base)
    probes = _megacrash_combo_counter_probes(state)
    probe = probes.get(key)
    if (not isinstance(probe, dict)
            or int(probe.get("attacker_base") or 0) != attacker_base
            or str(probe.get("attacker_slot") or "") != str(attacker_slot)):
        probe = {"attacker_base": attacker_base, "attacker_slot": str(attacker_slot), "offset": int(offsets[0]), "last": None}
        probes[key] = probe

    try:
        offset = int(probe.get("offset") or offsets[0])
    except Exception:
        offset = int(offsets[0])
    if offset not in offsets:
        offset = int(offsets[0])
    try:
        raw = rd8(attacker_base + offset)
    except Exception:
        raw = None
    if raw is None:
        return None
    try:
        current = int(raw) & 0xFF
    except Exception:
        return None
    try:
        previous = int(probe.get("last")) & 0xFF if probe.get("last") is not None else None
    except Exception:
        previous = None

    fresh = bool(current > 0 and (previous is None or current > previous))
    probe.update({"attacker_base": attacker_base, "attacker_slot": str(attacker_slot), "offset": offset, "last": current})
    state["live_combo_counter"] = current
    state["live_combo_counter_source"] = f"{attacker_slot} +0x{offset:X}"
    return {"fresh": False if consume_only else fresh, "value": current, "offset": offset, "slot": str(attacker_slot)}


def _megacrash_prime_live_combo_counters(state: dict, snaps: dict) -> None:
    """Consume global counter movement during cooldown without arming a hit."""
    _megacrash_read_global_combo_counter(state, consume_only=True)


def _megacrash_clear_finished_combo_occurrences(state: dict, snaps: dict) -> None:
    """A combo count belongs to one victim hitstun sequence and dies when it ends."""
    occurrences = _megacrash_combo_occurrences(state)
    live_bases: set[str] = set()
    for _snap in (snaps or {}).values():
        if not isinstance(_snap, dict):
            continue
        try:
            base = int(_snap.get("base") or 0)
        except Exception:
            base = 0
        # Megacrash itself temporarily replaces the victim reaction state. Keep
        # the combo record alive through that short pulse so the same combo
        # cannot re-arm immediately after the burst.
        if base and (_snap_is_hitstun_primary(_snap) or _snap_primary_action_id(_snap) == MEGACRASH_MOVE_ID):
            live_bases.add(_megacrash_occurrence_key(base))
    probes = _megacrash_combo_counter_probes(state)
    for key in list(occurrences):
        if str(key) not in live_bases:
            occurrences.pop(key, None)
    for key in list(probes):
        if str(key) not in live_bases:
            probes.pop(key, None)
    if not live_bases:
        state["live_combo_counter"] = 0
        state["live_combo_counter_source"] = ""
    _megacrash_refresh_occurrence_display(state)


def _megacrash_combo_key_for_attacker(atk_slot: str, atk_snap: dict) -> tuple | None:
    atk_label = _snap_move_label(atk_snap)
    atk_id = _snap_action_id(atk_snap)
    if not atk_label and atk_id is None:
        return None
    return (
        str(atk_snap.get("base") or atk_slot),
        int(atk_id) if atk_id is not None else -1,
        str(atk_label).strip().lower(),
    )


def _megacrash_mark_visible_combo_keys(snaps: dict, last_keys: dict) -> None:
    """Consume current labels during cooldown without rolling on stale labels later."""
    for teamtag in ("P1", "P2"):
        vic_slot = _team_point_slot_for_megacrash(teamtag, snaps)
        if not vic_slot:
            continue
        vic_snap = snaps.get(vic_slot)
        if not isinstance(vic_snap, dict):
            continue
        try:
            base = int(vic_snap.get("base") or 0)
        except Exception:
            base = 0
        if not base:
            continue
        if not _snap_is_hitstun_primary(vic_snap):
            last_keys.pop(base, None)
            continue

        atk_slot = _team_point_slot_for_megacrash(_opponent_teamtag(teamtag), snaps)
        if not atk_slot:
            continue
        atk_snap = snaps.get(atk_slot)
        if not isinstance(atk_snap, dict) or _is_support_or_assist_snap(atk_snap):
            continue
        atk_primary = _snap_primary_action_id(atk_snap)
        if _snap_is_hitstun_primary(atk_snap) or atk_primary == MEGACRASH_MOVE_ID:
            continue
        combo_key = _megacrash_combo_key_for_attacker(atk_slot, atk_snap)
        if combo_key is not None:
            last_keys[base] = combo_key


def _start_megacrash_trainer_pulse(state: dict, vic_snap: dict, now: float, reason: str = "") -> bool:
    # Absolute safety gate.  No caller, stale schedule, or old pulse is allowed
    # to write Megacrash unless the trainer is currently enabled.  In random
    # mode, 0% is also a hard no-op.
    if not isinstance(state, dict) or not bool(state.get("enabled", False)):
        try:
            state.setdefault("pulses", {}).clear()
            state.setdefault("scheduled_triggers", {}).clear()
            state["cooldown_until"] = 0.0
        except Exception:
            pass
        return False

    mode = _normalize_megacrash_mode(state.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE))
    chance = _clamp_megacrash_chance(state.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE))
    if mode == "percent" and chance <= 0:
        try:
            state.setdefault("pulses", {}).clear()
            state.setdefault("scheduled_triggers", {}).clear()
            state["cooldown_until"] = 0.0
        except Exception:
            pass
        return False

    base = 0
    try:
        base = int(vic_snap.get("base") or 0)
    except Exception:
        base = 0
    if not base:
        return False

    pulses = state.setdefault("pulses", {})
    wrote_any = False
    pulse_entries = []
    for off in MEGACRASH_TRAINER_WRITE_OFFSETS:
        addr = base + int(off)
        if not addr_in_ram(addr):
            continue
        if runtime_pm is not None:
            ok_write = runtime_pm.write_u32(addr, MEGACRASH_MOVE_ID, key="megacrash:start", dirty=False, force=True)
        else:
            ok_write = wd32(addr, MEGACRASH_MOVE_ID)
        if ok_write:
            wrote_any = True
            pulse_entries.append(addr)

    if wrote_any:
        slot = str(vic_snap.get("slotname") or vic_snap.get("slot_label") or "?")
        cooldown_sec = _clamp_megacrash_cooldown_sec(state.get("cooldown_sec", MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC))
        state["cooldown_until"] = now + cooldown_sec if cooldown_sec > 0.0 else 0.0
        try:
            state.setdefault("scheduled_triggers", {}).clear()
        except Exception:
            pass
        pulses[base] = {
            "slot": slot,
            "addrs": pulse_entries,
            "end": now + MEGACRASH_TRAINER_PULSE_SEC,
            "reason": reason,
        }
        state["last_trigger"] = {
            "slot": slot,
            "time": now,
            "reason": reason,
        }
        state["trigger_count"] = int(state.get("trigger_count", 0) or 0) + 1
        print(f"[megacrash trainer] trigger {slot}: {reason}")
    return wrote_any


def _tick_megacrash_trainer(state: dict, snaps: dict, now: float, frame_idx: int | None = None) -> dict:
    if not isinstance(state, dict):
        state = {}

    state.setdefault("enabled", False)
    state["mode"] = _normalize_megacrash_mode(state.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE))
    state["chance"] = _clamp_megacrash_chance(state.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE))
    state["delay_frames"] = _clamp_megacrash_delay_frames(state.get("delay_frames", MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES))
    state["cooldown_sec"] = _clamp_megacrash_cooldown_sec(state.get("cooldown_sec", MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC))
    state["target_label"] = _clean_megacrash_target_label(state.get("target_label", MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL))
    state["attacker_scope"] = _clean_megacrash_attacker_scope(state.get("attacker_scope", MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE))
    state["target_occurrence"] = _clamp_megacrash_target_occurrence(state.get("target_occurrence", MEGACRASH_TRAINER_DEFAULT_TARGET_OCCURRENCE))
    state.setdefault("occurrence_counter", 0)
    state.setdefault("match_occurrences", {})
    state.setdefault("combo_counter_probes", {})
    state.setdefault("global_combo_counter_probe", {"last": None, "seen_zero": False})
    state.setdefault("live_combo_counter", 0)
    state.setdefault("live_combo_counter_source", "")
    pulses = state.setdefault("pulses", {})
    last_keys = state.setdefault("last_combo_keys", {})
    scheduled = state.setdefault("scheduled_triggers", {})
    if frame_idx is None:
        try:
            frame_idx = int(round(now * TARGET_FPS))
        except Exception:
            frame_idx = 0

    # Absolute OFF gate comes before pulse replay.  The old order replayed an
    # already-started pulse for a few frames even after the trainer was turned
    # off.  OFF now means no writes this frame, period.
    if not bool(state.get("enabled", False)):
        try:
            pulses.clear()
            scheduled.clear()
            last_keys.clear()
            state.setdefault("match_occurrences", {}).clear()
            state.setdefault("combo_counter_probes", {}).clear()
            state["global_combo_counter_probe"] = {"last": None, "seen_zero": False}
            state["occurrence_counter"] = 0
            state["live_combo_counter"] = 0
            state["live_combo_counter_source"] = ""
            state["cooldown_until"] = 0.0
        except Exception:
            pass
        return state

    mode = _normalize_megacrash_mode(state.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE))
    chance = state["chance"]
    if mode == "percent" and chance <= 0:
        try:
            pulses.clear()
            scheduled.clear()
            state["cooldown_until"] = 0.0
        except Exception:
            pass
        return state

    snaps = snaps or {}
    snaps_by_base = {}
    for _slot, _snap in list(snaps.items()):
        if not isinstance(_snap, dict):
            continue
        try:
            _base = int(_snap.get("base") or 0)
        except Exception:
            _base = 0
        if _base:
            snaps_by_base[_base] = _snap

    # Read once per tick. The returned event belongs to the next valid
    # point-vs-point pair below; it is deliberately consumed even if the hit's
    # label is not the selected one, so a later matching label receives
    # only its own hits rather than an accumulated jump from unrelated labels.
    global_combo_event = _megacrash_read_global_combo_counter(state)
    global_combo_event_used = False

    # Keep the Megacrash poke pinned only until the game visibly accepts 448,
    # then release immediately.  This prevents the trainer from manufacturing
    # a permanent-looking Megacrash hitbox before the real burst animation owns
    # the state.
    for base, pulse in list(pulses.items()):
        try:
            base_i = int(base)
        except Exception:
            base_i = 0
        try:
            end_ts = float(pulse.get("end", 0.0) or 0.0)
        except Exception:
            end_ts = 0.0

        live_snap = snaps_by_base.get(base_i)
        live_primary = _snap_primary_action_id(live_snap) if live_snap else None
        if now >= end_ts or live_primary == MEGACRASH_MOVE_ID:
            pulses.pop(base, None)
            continue

        for addr in list(pulse.get("addrs") or []):
            try:
                addr = int(addr)
            except Exception:
                addr = 0
            if addr and addr_in_ram(addr):
                if runtime_pm is not None:
                    runtime_pm.write_u32(addr, MEGACRASH_MOVE_ID, key="megacrash:pulse", dirty=False, force=True)
                else:
                    wd32(addr, MEGACRASH_MOVE_ID)

    cooldown_remaining = _megacrash_cooldown_remaining(state, now)
    if cooldown_remaining > 0.0:
        # While cooling down, do not roll or fire scheduled bursts.  Consume the
        # currently visible combo labels so the trainer waits for a truly new
        # attacker label after the cooldown expires.
        scheduled.clear()
        _megacrash_mark_visible_combo_keys(snaps, last_keys)
        _megacrash_prime_live_combo_counters(state, snaps)
        _megacrash_clear_finished_combo_occurrences(state, snaps)
        return state

    # Process targeted delayed-burst schedules. A schedule is tied to the victim
    # point base and only fires if that same point is still in primary hitstun.
    for base, pending in list(scheduled.items()):
        try:
            base_i = int(base)
        except Exception:
            base_i = 0
        if not base_i:
            scheduled.pop(base, None)
            continue
        live_snap = snaps_by_base.get(base_i)
        if not isinstance(live_snap, dict):
            scheduled.pop(base, None)
            continue
        if _snap_primary_action_id(live_snap) == MEGACRASH_MOVE_ID:
            scheduled.pop(base, None)
            continue
        if not _snap_is_hitstun_primary(live_snap):
            scheduled.pop(base, None)
            continue

        try:
            fire_frame = int(pending.get("fire_frame", 0) or 0)
        except Exception:
            fire_frame = 0
        try:
            fire_time = float(pending.get("fire_time", 0.0) or 0.0)
        except Exception:
            fire_time = 0.0

        due = bool(frame_idx >= fire_frame if fire_frame else now >= fire_time)
        if not due:
            continue

        reason = str(pending.get("reason") or "targeted delayed label")
        _start_megacrash_trainer_pulse(state, live_snap, now, reason=reason)
        scheduled.pop(base, None)

    # Trainer logic is team-point vs team-point.  Assists/projectiles are not
    # allowed to become the attacker key or the victim target.  A roll happens
    # once for the current attacker label while the point victim stays in
    # hitstun; the same label cannot roll again until the attacker label changes
    # or the victim leaves hitstun and starts a new hitstun sequence.
    for teamtag in ("P1", "P2"):
        vic_slot = _team_point_slot_for_megacrash(teamtag, snaps)
        if not vic_slot:
            continue
        vic_snap = snaps.get(vic_slot)
        if not isinstance(vic_snap, dict):
            continue
        if _is_support_or_assist_snap(vic_snap):
            continue

        try:
            base = int(vic_snap.get("base") or 0)
        except Exception:
            base = 0
        if not base:
            continue

        if base in pulses or str(base) in pulses:
            continue

        if _snap_primary_action_id(vic_snap) == MEGACRASH_MOVE_ID:
            last_keys.pop(base, None)
            continue

        if not _snap_is_hitstun_primary(vic_snap):
            last_keys.pop(base, None)
            _megacrash_combo_occurrences(state).pop(_megacrash_occurrence_key(base), None)
            _megacrash_combo_counter_probes(state).pop(_megacrash_occurrence_key(base), None)
            _megacrash_refresh_occurrence_display(state)
            continue

        atk_team = _opponent_teamtag(teamtag)
        atk_slot = _team_point_slot_for_megacrash(atk_team, snaps)
        if not atk_slot:
            continue
        atk_snap = snaps.get(atk_slot)
        if not isinstance(atk_snap, dict):
            continue
        if _is_support_or_assist_snap(atk_snap):
            continue

        atk_primary = _snap_primary_action_id(atk_snap)
        if _snap_is_hitstun_primary(atk_snap) or atk_primary == MEGACRASH_MOVE_ID:
            # Do not let a simultaneously hitstunned point roll against the
            # other victim. This was the path that could make both point chars
            # burst from one clean hit.
            continue

        atk_label = _snap_move_label(atk_snap)
        atk_id = _snap_action_id(atk_snap)
        if not atk_label and atk_id is None:
            continue
        if str(atk_label).strip().lower() == "megacrash":
            continue

        combo_key = _megacrash_combo_key_for_attacker(atk_slot, atk_snap)
        if combo_key is None:
            continue

        # Global counter path: every rising edge is a real hit. Consume it on
        # the first valid point-vs-point pair, *before* filtering the chosen
        # character/label. That is what lets 2A -> 2B -> 2A keep 2A's old total
        # while preventing the 2B hits from being misattributed to the later 2A.
        using_global_counter = bool(
            isinstance(global_combo_event, dict)
            and bool(global_combo_event.get("fresh", False))
            and not global_combo_event_used
        )
        if isinstance(global_combo_event, dict) and bool(global_combo_event.get("fresh", False)) and global_combo_event_used:
            # One global hit stream can only belong to one point-vs-point pair.
            continue

        if using_global_counter:
            global_combo_event_used = True
            hit_delta = max(1, int(global_combo_event.get("delta", 1) or 1))
            last_keys[base] = tuple(combo_key) + ("global_combo_counter", int(global_combo_event.get("value", 0) or 0))
            if not _megacrash_attacker_scope_matches(state, atk_slot, atk_snap):
                continue
            if not _megacrash_label_matches(state.get("target_label", ""), atk_label, atk_id):
                continue
        else:
            # Read failure fallback: still allow the previous label-change path
            # rather than making the trainer dead if the global byte is absent.
            if isinstance(global_combo_event, dict):
                continue
            if not _megacrash_attacker_scope_matches(state, atk_slot, atk_snap):
                continue
            if not _megacrash_label_matches(state.get("target_label", ""), atk_label, atk_id):
                continue
            if last_keys.get(base) == combo_key:
                continue
            last_keys[base] = combo_key
            hit_delta = 1

        # Count matching *hits* inside this victim's current combo only.
        # The count starts at global counter reset / combo end. Each selected
        # label contributes every one of its hit increments; other labels are
        # consumed but do not change this selected-label total. Once the Nth
        # matching hit is reached, that combo is consumed.
        occurrence_target = _clamp_megacrash_target_occurrence(state.get("target_occurrence", 1))
        occurrence_key = _megacrash_occurrence_key(base)
        occurrences = _megacrash_combo_occurrences(state)
        combo_occurrence = occurrences.get(occurrence_key)
        if not isinstance(combo_occurrence, dict):
            combo_occurrence = {"count": 0, "triggered": False}
            occurrences[occurrence_key] = combo_occurrence

        if bool(combo_occurrence.get("triggered", False)):
            # This combo already reached its selected trigger point. Wait for
            # hitstun to end before a fresh combo can arm another burst.
            continue

        occurrence_counter = max(0, int(combo_occurrence.get("count", 0) or 0)) + max(1, int(hit_delta or 1))
        combo_occurrence.update({
            "count": occurrence_counter,
            "attacker": str(atk_slot),
            "label": str(atk_label or atk_id),
            "last_combo_key": tuple(combo_key),
            "last_hit_delta": max(1, int(hit_delta or 1)),
            "counter_source": str((global_combo_event or {}).get("source") or "label fallback"),
        })
        state["last_matching_label"] = str(atk_label or atk_id)
        state["last_matching_combo_base"] = occurrence_key
        _megacrash_refresh_occurrence_display(state)
        if occurrence_counter < occurrence_target:
            continue

        # The selected Nth event has been reached. Mark it consumed before
        # scheduling/rolling so a long combo cannot keep producing repeats.
        combo_occurrence["triggered"] = True
        combo_occurrence["triggered_at"] = occurrence_counter
        _megacrash_refresh_occurrence_display(state)

        if mode == "targeted":
            delay_frames = _clamp_megacrash_delay_frames(state.get("delay_frames", 0))
            fire_frame = int(frame_idx or 0) + delay_frames
            scheduled[base] = {
                "slot": str(vic_snap.get("slotname") or vic_slot),
                "attacker": str(atk_slot),
                "label": str(atk_label or atk_id),
                "fire_frame": fire_frame,
                "fire_time": now + (delay_frames / float(TARGET_FPS)),
                "reason": f"{atk_slot} {atk_label or atk_id} matching-hit #{occurrence_counter} in combo +{delay_frames}f",
            }
            state["roll_count"] = int(state.get("roll_count", 0) or 0) + 1
            if int(state.get("roll_count", 0) or 0) % 20 == 1:
                print(f"[megacrash trainer] schedule {vic_slot}: {atk_slot} {atk_label or atk_id} +{delay_frames}f")
            continue

        # Use random.random()*100 and a strict < comparison so 0% can never
        # pass, while 100% still always passes.
        roll = random.random() * 100.0
        state["roll_count"] = int(state.get("roll_count", 0) or 0) + 1
        if chance > 0 and roll < float(chance):
            reason = f"{atk_slot} {atk_label or atk_id} nth-in-combo roll {roll:.1f}<{chance}%"
            _start_megacrash_trainer_pulse(state, vic_snap, now, reason=reason)
        else:
            if frame_idx_mod := int(state.get("roll_count", 0) or 0):
                if frame_idx_mod % 20 == 0:
                    print(f"[megacrash trainer] roll skip {atk_slot} {atk_label or atk_id}: {roll:.1f}>={chance}%")

    _megacrash_clear_finished_combo_occurrences(state, snaps)
    return state

__all__ = [
    'MEGACRASH_MOVE_ID',
    'MEGACRASH_TRAINER_CONFIG_FILE',
    'MEGACRASH_TRAINER_DEFAULT_CHANCE',
    'MEGACRASH_TRAINER_DEFAULT_MODE',
    'MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES',
    'MEGACRASH_TRAINER_MAX_DELAY_FRAMES',
    'MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC',
    'MEGACRASH_TRAINER_MAX_COOLDOWN_SEC',
    'MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL',
    'MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE',
    'MEGACRASH_TRAINER_DEFAULT_TARGET_OCCURRENCE',
    'MEGACRASH_GLOBAL_COMBO_COUNTER_ADDR',
    'MEGACRASH_GLOBAL_COMBO_COUNTER_LABEL',
    'MEGACRASH_COMBO_COUNTER_OFFSETS_BY_SLOT',
    'MEGACRASH_TRAINER_MAX_TARGET_OCCURRENCE',
    'MEGACRASH_TRAINER_PULSE_SEC',
    'MEGACRASH_TRAINER_WRITE_OFFSETS',
    'MEGACRASH_TRAINER_CHANCE_PRESETS',
    'MEGACRASH_SUPPORT_STATE_IDS',
    'MEGACRASH_REACTION_STATES',
    '_u32be_bytes',
    '_clamp_megacrash_chance',
    '_clamp_megacrash_delay_frames',
    '_clamp_megacrash_cooldown_sec',
    '_clamp_megacrash_target_occurrence',
    '_clean_megacrash_attacker_scope',
    '_clean_megacrash_target_label',
    '_megacrash_target_tokens',
    '_megacrash_norm_label',
    '_megacrash_tight_label',
    '_MEGACRASH_LABEL_ID_CACHE',
    '_megacrash_label_id_cache',
    '_MEGACRASH_LABEL_OPTIONS_CACHE',
    '_megacrash_label_options_for_char',
    '_megacrash_roster_context',
    '_megacrash_label_matches',
    '_megacrash_target_summary',
    '_normalize_megacrash_mode',
    '_megacrash_mode_summary',
    '_load_megacrash_trainer_config',
    '_save_megacrash_trainer_config',
    '_extract_mission_megacrash_setup',
    '_clear_megacrash_runtime_state',
    '_sync_mission_megacrash_trainer',
    '_cycle_megacrash_chance',
    '_snap_action_id',
    '_snap_primary_action_id',
    '_snap_is_hitstun_primary',
    '_megacrash_attacker_scope_matches',
    '_opponent_teamtag',
    '_snap_move_label',
    '_is_support_or_assist_snap',
    '_team_point_slot_for_megacrash',
    '_nearest_opponent_snap',
    '_megacrash_cooldown_remaining',
    '_megacrash_occurrence_key',
    '_megacrash_combo_occurrences',
    '_megacrash_refresh_occurrence_display',
    '_megacrash_combo_counter_probes',
    '_megacrash_global_combo_probe',
    '_megacrash_reset_occurrences_for_global_combo_end',
    '_megacrash_read_global_combo_counter',
    '_megacrash_counter_offsets_for_slot',
    '_megacrash_read_live_combo_counter',
    '_megacrash_prime_live_combo_counters',
    '_megacrash_clear_finished_combo_occurrences',
    '_megacrash_combo_key_for_attacker',
    '_megacrash_mark_visible_combo_keys',
    '_start_megacrash_trainer_pulse',
    '_tick_megacrash_trainer'
]
