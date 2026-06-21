"""
mission_manager.py
------------------
Owns all mission-mode state and logic previously scattered as nested
functions inside legacy_main().

Public surface used by the render loop:
    mgr = MissionManager(move_map, global_map, debug_flag_addrs, read_debug_flags_fn)
    mgr.update(snaps, render_snap_by_slot, frame_idx, now)
    mgr.write_overlay_data()
    mgr.write_mode_state()
    mgr.consume_select_command()
    mgr.consume_celebrate_ack()
    mgr.update_selector_from_inputs(snaps, now)
    mgr.active_slot          -> str | None
    mgr.selector_open        -> bool
    mgr.select_mission_delta(delta)
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from dolphin_io import rd8, wd8
from mission_mode import (
    build_overlay_payload,
    load_progress,
    save_progress,
    mark_mission_complete,
    set_selected_mission_id,
)

MISSION_MODE_FILE          = "mission_mode_state.json"
MISSION_OVERLAY_FILE       = "mission_overlay_data.json"
MISSION_SELECT_FILE        = "mission_select_command.json"
MISSION_CELEBRATE_ACK_FILE = "mission_celebrate_ack.json"

# ---------------------------------------------------------------------------
# Sets / constants (same values as in the old main)
# ---------------------------------------------------------------------------

# Verified crouch-hit reaction state IDs. They still help on frames where the
# reaction animation is visible, while the global counter remains the long-lived
# combo authority. 67 is confirmed crouching hitstun (Hit Low) and must be
# treated as valid hitstun rather than a reset/neutral state.
MISSION_CROUCH_REACTION_STATES = {51, 67}

# Verified global in-game combo counter. It rises for every confirmed hit and
# returns to 0 when that combo ends, including crouching, airborne, relaunch,
# and character-specific reaction animations. Treat this as the sustained
# combo-liveness authority; state IDs and HP drops are only confirmation aids.
MISSION_GLOBAL_COMBO_COUNTER_ADDR = 0x809BDDB3

MISSION_REACTION_STATES = {
    48, 49, 50, 52, 53, 60, 61, 62, 64, 65, 66, 73, 74, 75, 76, 79, 80,
    81, 82, 83, 88, 89, 90, 91, 92, 94, 95, 96, 97, 98, 101, 102, 105,
    106, 142, 449,
    4608, 4609, 4610, 4611, 4613, 4614, 4615, 4616, 4617, 4618, 4619,
    4620, 4621, 4622, 4623, 4625,
    4562, 4565, 4568, 4571, 4573, 4631,
} | MISSION_CROUCH_REACTION_STATES
MISSION_MEGACRASH_STATES = {448}

# Mission-only combo keep-alive. Do not add Megacrash to the normal
# trainer/victim reaction states, because that can make both point chars
# eligible to burst. For missions, Megacrash only means the scripted route
# should not reset while the forced burst/counter interaction is happening.
MISSION_COMBO_KEEPALIVE_STATES = set(MISSION_REACTION_STATES) | set(MISSION_MEGACRASH_STATES)

MISSION_BLOCKSTUN_STATES = {48, 49, 50, 51, 52, 53}
MISSION_IGNORE_LABELS = {"", "idle", "crouched", "crouching"}
MISSION_ASSIST_OFF_STATES = {430, 432, 433}
MISSION_AIRBORNE_LABEL_TOKENS = (
    "jump", "air ", "j.", "air dash", "air weapon switch",
    "air random flight",
)
MISSION_VAR_LABEL_TOKENS = ("weapon switch", "variable air raid", "var")

MISSION_REQUIRE_DAMAGE_CONFIRM = True

MISSION_NON_DAMAGE_CONFIRM_LABELS = {"baroque cancel"}

MISSION_WHIFF_CONFIRM_LABELS = {
    s.strip().lower() for s in {
        "Air Dash A", "Air Dash B", "Air Dash C",
        "Weapon Switch Neutral A", "Weapon Switch Neutral B", "Weapon Switch Neutral C",
        "Weapon Switch Forwards A", "Weapon Switch Forwards B", "Weapon Switch Forwards C",
        "Weapon Switch Backwards A", "Weapon Switch Backwards B", "Weapon Switch Backwards C",
        "Air Weapon Switch Forward A", "Air Weapon Switch Forward B", "Air Weapon Switch Forward C",
        "Air Weapon Switch Backwards A", "Air Weapon Switch Backwards B", "Air Weapon Switch Backwards C",
        "Air Weapon Switch Neutral A", "Air Weapon Switch Neutral B", "Air Weapon Switch Neutral C",
        "Roll A", "Roll B", "Roll C", "Random Flight A", "Random Flight B", "Random Flight C",
        "Air Random Flight A", "Air Random Flight B", "Air Random Flight C",
        "Zombie Spree A", "Zombie Spree B", "Zombie Spree C", "Megacrash", "voltekka air",
         "yatter run",
        "Clutch A", "Clutch B", "Clutch C",
        "Tree A", "Tree B", "Tree C",
        "Rock A", "Rock B", "Rock C",
        "Comfy", "LOAD SUPER ARMOR PIERCING SHELL",
        "Pummel A", "Pummel B", "Pummel C",
        "Cactus Bunker A", "Cactus Bunker B", "Cactus Bunker C",
        "Air Rock A", "Air Rock B", "Air Rock C",
        "Quick Upper B", "yatter step", "Omochama",
    }
}

MISSION_GENERIC_VAR_LABELS = {"VAR", "var"}

MISSION_SELF_VAR_MISSIONS = {"alex_011"}

MISSION_METER_REFILL_MISSIONS = {"ryu_008", "saki_009", "alex_017"}

DORONJO_DAMAGE_PASS: dict[str, set] = {
    "clutch a": {2}, "clutch b": {2}, "clutch c": {2},
    "pummel a": {880}, "pummel b": {880}, "pummel c": {880},
    "tree a": {1360}, "tree b": {1360}, "tree c": {1360},
    "rock a": {4480}, "rock b": {4480}, "rock c": {4480},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_mission_runtime(
    slot=None,
    mission_id=None,
    clear_seq=0,
    celebrate_token=0,
    celebrate_pending=False,
    celebrate_acked_token=0,
    
) -> dict:
    return {
        "slot": slot,
        "mission_id": mission_id,
        "progress_index": 0,
        "last_seen_label": "",
        "last_seen_anim": None,
        "last_seen_hitstun": False,
        "last_inputs": {},
        "hitstun_grace": 0,
        "global_combo_count": 0,
        "pending_step_index": None,
        "pending_labels": [],
        "pending_anim": None,
        "opponent_hp_by_base": {},
           "attacker_hp_by_base": {},
        "goal_state_frames": 0,
        "goal_combo_damage": 0,
        "goal_combo_hits": 0,
        "goal_failed": False,
        "goal_last_damage_frame": -1,
        "clear_seq": int(clear_seq),
        "celebrate_token": int(celebrate_token),
        "celebrate_pending": bool(celebrate_pending),
        "celebrate_acked_token": int(celebrate_acked_token),
        "saved_p1meter_flag": None,
        "saved_baroque_flag": None,
        "saved_meter_flag_mission": None,
        "reset_grace_frames": 0,
        "reset_grace_labels": [],
        "reset_grace_step_index": None,
        "shell_install_hold": 0,
        "post_install_hold_frames": 0,
        "shell_installed": False,
        "shell_release_grace": 0,
          "reset_grace_keeps_alive_only": False,
    }


# ---------------------------------------------------------------------------
# MissionManager
# ---------------------------------------------------------------------------

class MissionManager:
    """
    Owns all mission-mode state and the logic that drives it each frame.

    Parameters
    ----------
    move_map : dict
        Per-character animation-ID -> label mapping (from load_move_map).
    global_map : dict
        Global animation-ID -> label mapping.
    debug_flag_addrs : list | dict
        The DEBUG_FLAG_ADDRS structure from config.py.
    read_debug_flags_fn : callable
        The merged_debug_values() callable from the main module (returns
        list of (name, addr, value, …) rows).
    move_label_for_fn : callable
        move_label_for(anim_id, csv_char_id, move_map, global_map)
    """

    def __init__(
        self,
        move_map: dict,
        global_map: dict,
        debug_flag_addrs: Any,
        read_debug_flags_fn,
        move_label_for_fn,
    ) -> None:
        self._move_map = move_map
        self._global_map = global_map
        self._debug_flag_addrs = debug_flag_addrs
        self._read_debug_flags = read_debug_flags_fn
        self._move_label_for = move_label_for_fn

        # Core runtime state
        self._runtime: dict = _new_mission_runtime()
        self._active_slot: str | None = None

        # Selector state
        self._selector: dict = {
            "open": False,
            "selected_index": 0,
            "sequence": [],
            "last_crouch": False,
            "last_taunt_down": False,
            "opened_at": 0.0,
            "hint_until": 0.0,
        }

        # Debug override save/restore state
        self._setup_state: dict = {
            "mission_key": None,
            "saved_debug_values": {},
            "applied_debug_values": {},
        }

        # Frame index — updated each call to update()
        self._frame_idx: int = 0

        # Last overlay payload built by write_overlay_data().  main.py uses this
        # to sync mission-scoped helpers such as Megacrash Trainer without
        # rereading the JSON file we just wrote.
        self._last_overlay_payload: dict = self._build_empty_overlay_payload()

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def active_slot(self) -> str | None:
        return self._active_slot

    @active_slot.setter
    def active_slot(self, value: str | None) -> None:
        self._active_slot = value

    @property
    def selector_open(self) -> bool:
        return self._selector["open"]

    @property
    def last_overlay_payload(self) -> dict:
        return dict(self._last_overlay_payload or {})

    # ------------------------------------------------------------------
    # Public update entry point
    # ------------------------------------------------------------------

    def update(
        self,
        snaps: dict,
        render_snap_by_slot: dict,
        frame_idx: int,
        now: float,
    ) -> None:
        """Called once per frame from the render loop."""
        self._frame_idx = frame_idx
        self._now = now
        self._render_snap_by_slot = render_snap_by_slot
        self._update_selector_from_inputs(snaps, now)
        self.consume_select_command()
        self.consume_celebrate_ack()

    # ------------------------------------------------------------------
    # Selector navigation
    # ------------------------------------------------------------------

    def select_mission_delta(self, delta: int) -> None:
        """Advance the selected mission by delta steps (from HUD button or keyboard)."""
        if not self._active_slot:
            return

        snap = self._render_snap_by_slot.get(self._active_slot) if hasattr(self, "_render_snap_by_slot") else None
        character_name = snap.get("name") if snap else None
        if not character_name:
            return

        payload = build_overlay_payload(character_name)
        missions = payload.get("missions", [])
        if not missions:
            return

        active_id = payload.get("active_mission_id")
        idx = next(
            (i for i, m in enumerate(missions) if m.get("mission_id") == active_id),
            0,
        )
        new_idx = (idx + delta) % len(missions)
        new_id = missions[new_idx].get("mission_id")

        progress = load_progress()
        progress = set_selected_mission_id(progress, character_name, new_id)
        save_progress(progress)

        self._runtime = _new_mission_runtime(slot=self._active_slot)
        self.write_overlay_data()

    # ------------------------------------------------------------------
    # File consumers
    # ------------------------------------------------------------------

    def consume_select_command(self) -> None:
        try:
            with open(MISSION_SELECT_FILE, "r", encoding="utf-8") as f:
                cmd = json.load(f)
        except Exception:
            return

        try:
            os.remove(MISSION_SELECT_FILE)
        except Exception:
            pass

        if not isinstance(cmd, dict):
            return

        action = cmd.get("action")
        if action == "close":
            self._close_selector()
            return

        if action != "select":
            return

        slot = cmd.get("slot")
        mission_id = cmd.get("mission_id")

        if slot != self._active_slot or not mission_id:
            return

        snap = self._render_snap_by_slot.get(self._active_slot) if hasattr(self, "_render_snap_by_slot") else None
        character_name = snap.get("name") if snap else None
        if not character_name:
            return

        progress = load_progress()
        progress = set_selected_mission_id(progress, character_name, mission_id)
        save_progress(progress)

        self._runtime = _new_mission_runtime(slot=self._active_slot)
        self._close_selector()

    def consume_celebrate_ack(self) -> None:
        try:
            with open(MISSION_CELEBRATE_ACK_FILE, "r", encoding="utf-8") as f:
                ack = json.load(f)
        except Exception:
            return

        try:
            os.remove(MISSION_CELEBRATE_ACK_FILE)
        except Exception:
            pass

        if not isinstance(ack, dict):
            return

        ack_token = int(ack.get("celebrate_token", 0) or 0)
        current_token = int(self._runtime.get("celebrate_token", 0) or 0)

        if ack_token > 0 and ack_token == current_token:
            self._runtime["celebrate_acked_token"] = ack_token
            self._runtime["celebrate_pending"] = False

    # ------------------------------------------------------------------
    # File writers
    # ------------------------------------------------------------------

    def write_overlay_data(self, render_snap_by_slot: dict | None = None) -> None:
        snaps = render_snap_by_slot or getattr(self, "_render_snap_by_slot", {})

        payload = self._build_empty_overlay_payload()

        if self._active_slot:
            snap = snaps.get(self._active_slot)
            character_name = snap.get("name") if snap else None

            if character_name:
                payload = build_overlay_payload(character_name)
                payload["active"] = True
                payload["slot"] = self._active_slot
                payload = self._augment_payload_with_runtime(payload, snaps)
                payload["selector_open"] = bool(self._selector["open"])
                payload["selector_index"] = int(self._selector["selected_index"])
                payload["selector_hint"] = "Down, Down, Taunt: Open Mission Select"
                payload["selector_controls"] = "Down: Move  Taunt: Select  Mouse still works"
                payload["scanlines"] = True

        self._sync_debug_overrides(payload)
        self._last_overlay_payload = dict(payload or {})

        try:
            tmp = f"{MISSION_OVERLAY_FILE}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp, MISSION_OVERLAY_FILE)
        except Exception:
            pass

    def write_mode_state(self) -> None:
        if not self._active_slot:
            if self._runtime.get("saved_meter_flag_mission") in MISSION_METER_REFILL_MISSIONS:
                saved = self._runtime.get("saved_p1meter_flag")
                if saved is not None:
                    self._write_debug_flag("P1Meter", int(saved))
                saved_b = self._runtime.get("saved_baroque_flag")
                if saved_b is not None:
                    self._write_debug_flag("BaroquePct", int(saved_b))

        payload = {
            "active": bool(self._active_slot),
            "slot": self._active_slot,
        }
        try:
            with open(MISSION_MODE_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    def restore_debug_overrides(self) -> None:
        """Call at shutdown to restore any debug flags we overwrote."""
        self._restore_debug_overrides()

    # ------------------------------------------------------------------
    # Toggle active slot (called from click handler in main)
    # ------------------------------------------------------------------

    def toggle_active_slot(self, slot_label: str) -> None:
        self._active_slot = None if self._active_slot == slot_label else slot_label
        self._runtime = _new_mission_runtime(slot=self._active_slot)
        self.write_mode_state()
        self.write_overlay_data()

    # ------------------------------------------------------------------
    # Var state query (used by hud_overlay_manager)
    # ------------------------------------------------------------------

    def var_state(self, slot_label: str, snaps_dict: dict) -> dict:
        return self._mission_var_state(slot_label, snaps_dict)

    # ------------------------------------------------------------------
    # Debug flag helpers
    # ------------------------------------------------------------------

    def _resolve_debug_addr(self, name: str) -> int | None:
        try:
            for entry in self._read_debug_flags():
                if not entry or not isinstance(entry, (tuple, list)):
                    continue
                if entry[0] != name:
                    continue
                for item in entry[1:]:
                    if isinstance(item, int):
                        return item
        except Exception as e:
            print(f"[mission] merged lookup failed for {name!r}: {e!r}")

        try:
            dfa = self._debug_flag_addrs
            if isinstance(dfa, dict):
                entry = dfa.get(name)
                if isinstance(entry, int):
                    return entry
                if isinstance(entry, dict):
                    for key in ("addr", "address"):
                        if isinstance(entry.get(key), int):
                            return entry[key]
                if isinstance(entry, (tuple, list)):
                    for item in entry:
                        if isinstance(item, int):
                            return item
            elif isinstance(dfa, (list, tuple)):
                for entry in dfa:
                    if isinstance(entry, (tuple, list)) and len(entry) >= 2:
                        if entry[0] == name and isinstance(entry[1], int):
                            return entry[1]
        except Exception as e:
            print(f"[mission] static lookup failed for {name!r}: {e!r}")

        print(f"[mission] missing debug flag addr for {name!r}")
        return None

    def _read_debug_flag(self, name: str) -> int | None:
        addr = self._resolve_debug_addr(name)
        if not isinstance(addr, int):
            return None
        try:
            return rd8(addr)
        except Exception:
            return None

    def _write_debug_flag(self, name: str, value: int) -> bool:
        addr = self._resolve_debug_addr(name)
        if not isinstance(addr, int):
            print(f"[mission] missing debug flag addr for {name!r}")
            return False
        try:
            wd8(addr, max(0, min(255, int(value))))
            return True
        except Exception as e:
            print(f"[mission] write failed for {name!r}: {e!r}")
            return False

    # ------------------------------------------------------------------
    # Debug override sync/restore
    # ------------------------------------------------------------------

    def _extract_active_overrides(self, payload: dict) -> dict[str, int]:
        if not isinstance(payload, dict):
            return {}

        raw = (
            payload.get("active_mission_setup_debug_flags")
            or payload.get("active_mission_debug_flags")
        )
        if isinstance(raw, dict):
            out = {}
            for key, value in raw.items():
                try:
                    out[str(key)] = max(0, min(255, int(value)))
                except Exception:
                    pass
            return out

        active_id = payload.get("active_mission_id")
        for mission in (payload.get("missions") or []):
            if not isinstance(mission, dict):
                continue
            if mission.get("mission_id") != active_id:
                continue
            raw = mission.get("setup_debug_flags") or mission.get("debug_flags") or {}
            if not isinstance(raw, dict):
                return {}
            out = {}
            for key, value in raw.items():
                try:
                    out[str(key)] = max(0, min(255, int(value)))
                except Exception:
                    pass
            return out

        return {}

    def _restore_debug_overrides(self) -> None:
        for name, original_value in dict(self._setup_state.get("saved_debug_values") or {}).items():
            if isinstance(original_value, int):
                self._write_debug_flag(name, original_value)
                print(f"[mission restore] {name} -> {original_value}")
        self._setup_state["mission_key"] = None
        self._setup_state["saved_debug_values"] = {}
        self._setup_state["applied_debug_values"] = {}

    def _sync_debug_overrides(self, payload: dict) -> None:
        if not isinstance(payload, dict) or not payload.get("active"):
            if self._setup_state.get("mission_key") is not None:
                self._restore_debug_overrides()
            return

        overrides = self._extract_active_overrides(payload)
        mission_key = (
            payload.get("slot"),
            payload.get("character"),
            payload.get("active_mission_id"),
        )

        if not overrides:
            if self._setup_state.get("mission_key") is not None:
                self._restore_debug_overrides()
            return

        current_key = self._setup_state.get("mission_key")

        if current_key != mission_key:
            if current_key is not None:
                self._restore_debug_overrides()

            saved = {}
            applied = {}
            for name, wanted in overrides.items():
                cur = self._read_debug_flag(name)
                if isinstance(cur, int):
                    saved[name] = cur
                if self._write_debug_flag(name, wanted):
                    applied[name] = wanted
                    print(
                        f"[mission apply] mission={payload.get('active_mission_id')} "
                        f"{name}: {cur} -> {wanted}"
                    )

            if applied:
                self._setup_state["mission_key"] = mission_key
                self._setup_state["saved_debug_values"] = saved
                self._setup_state["applied_debug_values"] = applied
            else:
                self._setup_state["mission_key"] = None
                self._setup_state["saved_debug_values"] = {}
                self._setup_state["applied_debug_values"] = {}
            return

        for name, wanted in (self._setup_state.get("applied_debug_values") or {}).items():
            if self._read_debug_flag(name) != wanted:
                self._write_debug_flag(name, wanted)

    # ------------------------------------------------------------------
    # Selector helpers
    # ------------------------------------------------------------------

    def _close_selector(self) -> None:
        self._selector["open"] = False
        self._selector["sequence"] = []
        self._selector["opened_at"] = 0.0

    def _open_selector(self, character_name: str, now: float) -> None:
        payload = build_overlay_payload(character_name)
        missions = payload.get("missions", [])
        active_id = payload.get("active_mission_id")
        idx = next(
            (i for i, m in enumerate(missions) if m.get("mission_id") == active_id),
            0,
        )
        self._selector["open"] = True
        self._selector["selected_index"] = idx
        self._selector["opened_at"] = now
        self._selector["hint_until"] = now + 8.0
        self._selector["sequence"] = []

    def _label_is_crouch(self, label: str) -> bool:
        return (label or "").strip().lower() in {"crouched", "crouching"}

    def _label_is_taunt(self, label: str) -> bool:
        return (label or "").strip().lower() == "taunt"

    def _selector_source_slot(self, snaps_dict: dict) -> str | None:
        if not self._active_slot:
            return None

        active_snap = snaps_dict.get(self._active_slot) or self._render_snap_by_slot.get(self._active_slot)
        if not active_snap:
            return self._active_slot

        teamtag = active_snap.get("teamtag")
        if not teamtag:
            return self._active_slot

        team_slots = [s for s in (f"{teamtag}-C1", f"{teamtag}-C2") if s in snaps_dict]
        if not team_slots:
            return self._active_slot

        team_active = self._team_active_slot(self._active_slot, snaps_dict)
        if team_active in team_slots:
            return team_active

        return self._active_slot if self._active_slot in team_slots else team_slots[0]

    def _update_selector_from_inputs(self, snaps_dict: dict, now: float) -> None:
        if not self._active_slot:
            self._close_selector()
            return

        selector_slot = self._selector_source_slot(snaps_dict)
        if not selector_slot:
            self._close_selector()
            return

        snap = snaps_dict.get(selector_slot) or self._render_snap_by_slot.get(selector_slot)
        if not snap:
            self._close_selector()
            return

        character_name = snap.get("name")
        if not character_name:
            self._close_selector()
            return

        payload = build_overlay_payload(character_name)
        missions = payload.get("missions", [])
        if not missions:
            self._close_selector()
            return

        current_label = snap.get("mv_label") or ""
        crouch_now = self._label_is_crouch(current_label)
        taunt_now = self._label_is_taunt(current_label)
        crouch_rising = crouch_now and not self._selector["last_crouch"]
        taunt_rising = taunt_now and not self._selector["last_taunt_down"]

        if self._selector["open"]:
            if now - self._selector["opened_at"] > 8.0:
                self._close_selector()
            else:
                if crouch_rising:
                    self._selector["selected_index"] = (
                        int(self._selector.get("selected_index", 0)) + 1
                    ) % len(missions)
                    self._selector["opened_at"] = now
                    self._selector["hint_until"] = now + 8.0

                if taunt_rising:
                    idx = int(self._selector.get("selected_index", 0)) % len(missions)
                    mission_id = missions[idx].get("mission_id")
                    if mission_id:
                        progress = load_progress()
                        progress = set_selected_mission_id(progress, character_name, mission_id)
                        save_progress(progress)

                        self._active_slot = selector_slot
                        self._runtime = _new_mission_runtime(slot=self._active_slot)
                        self._close_selector()
                        self.write_mode_state()
                        self.write_overlay_data()
        else:
            if crouch_rising:
                seq = self._selector["sequence"]
                if not seq or (now - seq[-1]) <= 0.9:
                    seq.append(now)
                else:
                    seq[:] = [now]
                if len(seq) > 2:
                    del seq[:-2]

            if taunt_rising and len(self._selector["sequence"]) >= 2:
                if (self._selector["sequence"][-1] - self._selector["sequence"][-2]) <= 0.9:
                    self._active_slot = selector_slot
                    self._runtime = _new_mission_runtime(slot=self._active_slot)
                    self._open_selector(character_name, now)
                    self.write_mode_state()
                    self.write_overlay_data()

        self._selector["last_crouch"] = crouch_now
        self._selector["last_taunt_down"] = taunt_now

    # ------------------------------------------------------------------
    # Team / slot helpers
    # ------------------------------------------------------------------

    def _team_active_slot(self, slot_label: str, snaps_dict: dict) -> str | None:
        prefix = "P1" if slot_label.startswith("P1") else "P2"
        c1 = f"{prefix}-C1"
        c2 = f"{prefix}-C2"
        s1 = snaps_dict.get(c1)
        s2 = snaps_dict.get(c2)

        if s1 and not s2:
            return c1
        if s2 and not s1:
            return c2
        if not s1 and not s2:
            return None

        mv1 = s1.get("attA") or s1.get("attB") or 0
        mv2 = s2.get("attA") or s2.get("attB") or 0
        c1_off = mv1 in MISSION_ASSIST_OFF_STATES
        c2_off = mv2 in MISSION_ASSIST_OFF_STATES
        if c2_off and not c1_off:
            return c1
        if c1_off and not c2_off:
            return c2
        return c1

    def _partner_slot(self, slot_label: str) -> str | None:
        if slot_label.endswith("C1"):
            return slot_label[:-2] + "C2"
        if slot_label.endswith("C2"):
            return slot_label[:-2] + "C1"
        return None

    def _mission_var_state(self, slot_label: str, snaps_dict: dict) -> dict:
        partner_slot = self._partner_slot(slot_label)
        partner_snap = snaps_dict.get(partner_slot) if partner_slot else None
        target_snap = snaps_dict.get(slot_label)
        active_slot = self._team_active_slot(slot_label, snaps_dict)

        partner_exists = bool(
            isinstance(partner_snap, dict)
            and isinstance(target_snap, dict)
            and partner_snap.get("base") != target_snap.get("base")
        )
        wrong_character_ready = bool(
            partner_exists
            and active_slot == partner_slot
            and active_slot != slot_label
        )
        label = (partner_snap.get("mv_label") or "").strip().lower() if partner_snap else ""
        partner_airborne = wrong_character_ready and any(
            t in label for t in MISSION_AIRBORNE_LABEL_TOKENS
        )
        partner_varing = wrong_character_ready and any(
            t in label for t in MISSION_VAR_LABEL_TOKENS
        )
        return {
            "target_slot": slot_label,
            "partner_slot": partner_slot,
            "team_active_slot": active_slot,
            "partner_exists": partner_exists,
            "wrong_character_ready": wrong_character_ready,
            "partner_airborne": partner_airborne,
            "partner_varing": partner_varing,
            "var_ready": partner_airborne,
        }

    # ------------------------------------------------------------------
    # Opponent query helpers
    # ------------------------------------------------------------------

    def _opponent_in_state(self, slot_label: str, snaps_dict: dict, state_ids: set) -> bool:
        if not slot_label:
            return False

        my_team = "P1" if slot_label.startswith("P1") else "P2"

        for other_snap in snaps_dict.values():
            if not isinstance(other_snap, dict):
                continue
            if other_snap.get("teamtag") == my_team:
                continue

            att_a = other_snap.get("attA")
            att_b = other_snap.get("attB")

            if att_a in state_ids or att_b in state_ids:
                return True

        return False

    def _opponent_in_hitstun(self, slot_label: str, snaps_dict: dict) -> bool:
        return self._opponent_in_state(slot_label, snaps_dict, MISSION_REACTION_STATES)

    def _opponent_in_megacrash(self, slot_label: str, snaps_dict: dict) -> bool:
        return self._opponent_in_state(slot_label, snaps_dict, MISSION_MEGACRASH_STATES)

    def _global_combo_count(self) -> int | None:
        """Read TvC's game-wide combo count, or None if Dolphin is unavailable.

        This survives crouching/airborne hitstun action-ID changes and resets to
        zero only when the combo itself has actually ended.
        """
        try:
            value = rd8(MISSION_GLOBAL_COMBO_COUNTER_ADDR)
            return int(value) if value is not None else None
        except Exception:
            return None

    def _opponent_damage_this_frame(self, slot_label: str, snaps_dict: dict) -> list[int]:
        if not slot_label:
            return []
        my_team = "P1" if slot_label.startswith("P1") else "P2"
        hp_cache = self._runtime.setdefault("opponent_hp_by_base", {})
        live_bases: set = set()
        damage_values: list[int] = []

        for other_snap in snaps_dict.values():
            if not isinstance(other_snap, dict):
                continue
            if other_snap.get("teamtag") == my_team:
                continue
            base = other_snap.get("base")
            cur_hp = other_snap.get("cur")
            if not isinstance(base, int) or not isinstance(cur_hp, int):
                continue
            live_bases.add(base)
            prev = hp_cache.get(base)
            if isinstance(prev, int) and cur_hp < prev:
                damage_values.append(prev - cur_hp)
            hp_cache[base] = cur_hp

        for base in list(hp_cache):
            if base not in live_bases:
                del hp_cache[base]

        return damage_values



    # ------------------------------------------------------------------
    # Step predicate helpers
    # ------------------------------------------------------------------

    def _label_is_ignorable(self, label: str) -> bool:
        return (label or "").strip().lower() in MISSION_IGNORE_LABELS

    def _is_direction_input_key(self, key: str) -> bool:
        return any(t in (key or "").strip().lower() for t in (
            "up", "down", "left", "right", "dir", "stick", "analog", "xaxis", "yaxis"
        ))

    def _has_fresh_attack_input(self, current: dict, last: dict) -> bool:
        if not isinstance(current, dict) or not current:
            return False
        if not isinstance(last, dict):
            last = {}
        for key, cur_val in current.items():
            if self._is_direction_input_key(key):
                continue
            if int(cur_val or 0) != 0 and int(last.get(key, 0) or 0) == 0:
                return True
        return False

    def _step_has_non_damage_confirm(
        self, expected_labels: list[str], snap: dict, current_label: str
    ) -> bool:
        labels_norm = {str(x).strip().lower() for x in (expected_labels or []) if str(x).strip()}
        if not labels_norm:
            return False
        current_norm = (current_label or "").strip().lower()
        if "baroque cancel" in labels_norm:
            if current_norm == "baroque cancel":
                return True
            if snap.get("baroque_cancel_latched") or snap.get("baroque_cancel_raw"):
                return True
        if labels_norm & MISSION_NON_DAMAGE_CONFIRM_LABELS:
            if current_norm in labels_norm:
                return True
        return False

    def _step_allows_whiff_confirm(self, expected_labels: list[str], step: Any = None) -> bool:
        labels_norm = {str(x).strip().lower() for x in (expected_labels or []) if str(x).strip()}
        matched = labels_norm & MISSION_WHIFF_CONFIRM_LABELS
        explicit = isinstance(step, dict) and bool(
            step.get("whiff", False)
            or step.get("whiff_confirm", False)
            or step.get("allow_whiff", False)
        )
        if matched or explicit:
            print(
                f"[mission whiff confirm] labels={sorted(matched)!r} "
                f"explicit={explicit}"
            )
        return explicit or bool(matched)

    def _step_allows_zero_damage_confirm(
        self, character_name: str, expected_labels: list[str], current_label: str
    ) -> bool:
        if character_name != "Saki":
            return False
        return (current_label or "").strip().lower() == "load super armor piercing shell"

    def _saki_shell_release_label(self, label: str) -> bool:
        return (label or "").strip().lower() in {"5c", "j.c", "j.b"}

    def _is_saki_shell_label(self, label: str) -> bool:
        return (label or "").strip().lower() == "load super armor piercing shell"

    def _step_needs_reset_grace(self, character_name: str, expected_labels: list[str]) -> bool:
        if character_name != "Saki":
            return False
        labels_norm = {str(x).strip().lower() for x in (expected_labels or []) if str(x).strip()}
        return "j.c" in labels_norm

    def _reset_grace_accepts_label(self, character_name: str, label: str) -> bool:
        if character_name != "Saki":
            return False
        return (label or "").strip().lower() == "j.c"

    def _step_is_generic_partner_var(self, mission_id: str, expected_labels: list[str]) -> bool:
        if mission_id in MISSION_SELF_VAR_MISSIONS:
            return False
        labels_norm = {str(x).strip().lower() for x in (expected_labels or []) if str(x).strip()}
        return bool(labels_norm & MISSION_GENERIC_VAR_LABELS)

    def _partner_matches_generic_var(self, slot_label: str, snaps_dict: dict) -> bool:
        partner_slot = self._partner_slot(slot_label)
        if not partner_slot:
            return False
        me = snaps_dict.get(slot_label) or {}
        partner = snaps_dict.get(partner_slot) or {}
        if not me or not partner:
            return False
        my_base = me.get("base")
        partner_base = partner.get("base")
        if isinstance(my_base, int) and isinstance(partner_base, int) and my_base == partner_base:
            return False

        partner_label = (partner.get("mv_label") or "").strip().lower()
        partner_anim = partner.get("attA") or partner.get("attB")
        partner_csv = partner.get("csv_char_id")

        if partner_label in MISSION_GENERIC_VAR_LABELS:
            print(f"[mission generic var direct] slot={slot_label} partner={partner_slot} label={partner_label!r}")
            return True

        if partner_anim is not None:
            mapped = self._move_label_for(partner_anim, partner_csv, self._move_map, self._global_map)
            if (mapped or "").strip().lower() in MISSION_GENERIC_VAR_LABELS:
                print(f"[mission generic var mapped] slot={slot_label} partner={partner_slot} mapped={mapped!r}")
                return True

        print(f"[mission generic var miss] slot={slot_label} partner={partner_slot} label={partner_label!r}")
        return False

    def _doronjo_damage_pass(
        self, character_name: str, expected_labels: list[str], damage_values: list[int]
    ) -> bool:
        if character_name != "Doronjo":
            return False
        labels_norm = {str(x).strip().lower() for x in (expected_labels or []) if str(x).strip()}
        if not labels_norm or not damage_values:
            return False
        for label in labels_norm:
            allowed = DORONJO_DAMAGE_PASS.get(label)
            if allowed and any(d in allowed for d in damage_values):
                return True
        return False
    def _doronjo_damage_pass(
        self, character_name: str, expected_labels: list[str], damage_values: list[int]
    ) -> bool:
        if character_name != "Doronjo":
            return False
        labels_norm = {str(x).strip().lower() for x in (expected_labels or []) if str(x).strip()}
        if not labels_norm or not damage_values:
            return False
        for label in labels_norm:
            allowed = DORONJO_DAMAGE_PASS.get(label)
            if allowed and any(d in allowed for d in damage_values):
                return True
        return False

    def _can_repeat_same_step_on_damage(
        self,
        steps: list,
        progress_index: int,
        pending_labels: list[str],
        current_label: str,
        current_anim,
        damage_values: list[int],
    ) -> bool:
        if not damage_values:
            return False

        if progress_index <= 0 or progress_index >= len(steps):
            return False

        current_labels = self._step_labels(steps[progress_index])
        previous_labels = self._step_labels(steps[progress_index - 1])

        if not current_labels or not previous_labels:
            return False

        current_norm = {str(x).strip().lower() for x in current_labels}
        previous_norm = {str(x).strip().lower() for x in previous_labels}
        pending_norm = {str(x).strip().lower() for x in pending_labels}

        if not current_norm or current_norm != previous_norm:
            return False

        if pending_norm and pending_norm != current_norm:
            return False

        if (current_label or "").strip().lower() not in current_norm:
            return False

        last_anim = self._runtime.get("last_seen_anim")
        if current_anim != last_anim:
            return False


        return True

    def _step_is_baroque_cancel(self, expected_labels: list[str]) -> bool:
        labels_norm = {
            str(x).strip().lower()
            for x in (expected_labels or [])
            if str(x).strip()
        }
        return "baroque cancel" in labels_norm

    # ------------------------------------------------------------------
    # Core augment (was _augment_payload_with_runtime)
    # ------------------------------------------------------------------
    def _step_labels(self, step) -> list[str]:
        if isinstance(step, dict):
            raw = step.get("labels", [])
            if isinstance(raw, list):
                return [str(x).strip() for x in raw if str(x).strip()]
            label = str(step.get("label", "")).strip()
            return [label] if label else []

        if isinstance(step, list):
            return [str(x).strip() for x in step if str(x).strip()]

        text = str(step).strip()
        return [text] if text else []

    def _step_grace(self, step) -> int:
        if not isinstance(step, dict):
            return 0
        try:
            return max(0, int(step.get("grace", 0) or 0))
        except Exception:
            return 0

    def _step_is_pass(self, step) -> bool:
        return isinstance(step, dict) and bool(step.get("pass", False))
    def _step_grace_keeps_alive_only(self, step) -> bool:
        return isinstance(step, dict) and bool(step.get("grace_keeps_alive_only", False))
    def _augment_payload_with_runtime(self, payload: dict, snaps_dict: dict) -> dict:
        payload = dict(payload or {})
        slot = payload.get("slot")
        mission_id = payload.get("active_mission_id")
        steps = list(payload.get("active_mission_steps") or [])
        mission_goal = dict(payload.get("active_mission_goal") or {})
        character_name = payload.get("character")
        frame_idx = self._frame_idx

        def _clear_payload(final_count, final_idx, final_label):
            print(f"[mission clear] slot={slot} mission_id={mission_id} character={character_name!r}")
            if character_name and mission_id:
                progress = load_progress()
                progress = mark_mission_complete(progress, character_name, mission_id)
                save_progress(progress)

            next_seq = int(self._runtime.get("clear_seq", 0)) + 1
            next_token = int(self._runtime.get("celebrate_token", 0)) + 1

            cp = build_overlay_payload(character_name or "")
            cp["active"] = True
            cp["slot"] = slot
            cp["just_cleared"] = True
            cp["clear_seq"] = next_seq
            cp["celebrate_pending"] = True
            cp["celebrate_token"] = next_token
            cp["completed_step_count"] = final_count
            cp["current_step_index"] = final_idx
            cp["current_step_label"] = final_label

            self._runtime = _new_mission_runtime(
                slot=slot,
                mission_id=mission_id,
                clear_seq=next_seq,
                celebrate_token=next_token,
                celebrate_pending=True,
                celebrate_acked_token=0,
            )
            return cp

        if not payload.get("active") or not slot or not mission_id or (not steps and not mission_goal):
            self._runtime = _new_mission_runtime()
            payload.update({
                "completed_step_count": 0,
                "current_step_index": 0,
                "current_step_label": steps[0] if steps else None,
                "just_cleared": False,
                "celebrate_pending": False,
                "celebrate_token": 0,
            })
            return payload

        if (
            self._runtime.get("slot") != slot
            or self._runtime.get("mission_id") != mission_id
        ):
            self._runtime = _new_mission_runtime(
                slot=slot,
                mission_id=mission_id,
                clear_seq=int(self._runtime.get("clear_seq", 0) or 0),
                celebrate_token=int(self._runtime.get("celebrate_token", 0) or 0),
                celebrate_pending=bool(self._runtime.get("celebrate_pending", False)),
                celebrate_acked_token=int(self._runtime.get("celebrate_acked_token", 0) or 0),
            )

        snap = snaps_dict.get(slot) or self._render_snap_by_slot.get(slot) if hasattr(self, "_render_snap_by_slot") else snaps_dict.get(slot) or {}
        snap = snap or {}
        current_label = (snap.get("mv_label") or "").strip()
        current_anim = snap.get("mv_id_display")
        current_inputs = snap.get("inputs") or {}
        opponent_in_hitstun = self._opponent_in_hitstun(slot, snaps_dict)
        opponent_in_megacrash = self._opponent_in_megacrash(slot, snaps_dict)
        global_combo_count = self._global_combo_count()
        global_combo_active = bool(global_combo_count is not None and global_combo_count > 0)
        self._runtime["global_combo_count"] = int(global_combo_count or 0)
        damage_values = self._opponent_damage_this_frame(slot, snaps_dict)
        opponent_took_damage = bool(damage_values)
        frame_damage = sum(int(x) for x in damage_values)

        # A valid HP drop is authoritative evidence that the opponent was hit.
        # Keep this as a same-frame Mission Mode fallback in addition to the
        # action-state list: crouching characters can pass through a reaction
        # state Mission Mode has not mapped yet, but their real hit still must
        # be eligible to confirm the current mission step.
        opponent_hit_confirmed_this_frame = opponent_took_damage
        baroque_pool_adjusted = bool(snap.get("baroque_cancel_raw"))

        # Meter-refill mission gate. Refill outside combo, disable refill during combo.
        if mission_id in MISSION_METER_REFILL_MISSIONS:
            meter_val = int(snap.get("meter", 0) or 0)
            if self._runtime.get("saved_meter_flag_mission") != mission_id:
                self._runtime["saved_p1meter_flag"] = int(self._read_debug_flag("P1Meter") or 0)
                self._runtime["saved_baroque_flag"] = int(self._read_debug_flag("BaroquePct") or 0)
                self._runtime["saved_meter_flag_mission"] = mission_id
            self._write_debug_flag("BaroquePct", 1)
            if opponent_in_hitstun:
                self._write_debug_flag("P1Meter", 0)
            else:
                self._write_debug_flag("P1Meter", 1 if meter_val < 50000 else 0)

        progress_index = int(self._runtime.get("progress_index", 0))

        # Keep the old action-state read as a fast local hint, but use the
        # verified global counter for sustained combo liveness. This is what
        # prevents crouching hitstun from looking like a one-frame HP event.
        self._runtime["hitstun_grace"] = 0

        # Shell release grace
        shell_release_grace = int(self._runtime.get("shell_release_grace", 0) or 0)
        if shell_release_grace > 0:
            self._runtime["shell_release_grace"] = shell_release_grace - 1

        reset_grace_active_now = int(self._runtime.get("reset_grace_frames", 0)) > 0

        opponent_real_combo_state = (
            global_combo_active
            or opponent_in_hitstun
            or opponent_in_megacrash
            or opponent_hit_confirmed_this_frame
            or int(self._runtime.get("shell_release_grace", 0)) > 0
        )

        opponent_in_combo_state = (
            opponent_real_combo_state
            or reset_grace_active_now
        )

        if (
            self._runtime.get("shell_installed")
            and self._saki_shell_release_label(current_label)
            and opponent_in_combo_state
        ):
            self._runtime["shell_release_grace"] = 20

        # Goal-type missions
        if mission_goal:
            goal_type = str(mission_goal.get("type", "")).strip().lower()

            if opponent_in_combo_state and frame_damage > 0:
                self._runtime["goal_combo_damage"] = int(self._runtime.get("goal_combo_damage", 0)) + frame_damage
                if self._runtime.get("goal_last_damage_frame") != frame_idx:
                    self._runtime["goal_combo_hits"] = int(self._runtime.get("goal_combo_hits", 0)) + 1
                    self._runtime["goal_last_damage_frame"] = frame_idx

            def _goal_base(payload):
                payload["just_cleared"] = False
                payload["clear_seq"] = int(self._runtime.get("clear_seq", 0))
                payload["celebrate_pending"] = bool(self._runtime.get("celebrate_pending", False))
                payload["celebrate_token"] = int(self._runtime.get("celebrate_token", 0) or 0)
                payload["completed_step_count"] = 0
                payload["current_step_index"] = 0

            if goal_type == "state_duration":
                target_state = str(mission_goal.get("target_state", "")).strip().lower()
                needed = int(mission_goal.get("frames", 0) or 0)
                in_target = (
                    self._opponent_in_state(slot, snaps_dict, MISSION_BLOCKSTUN_STATES)
                    if target_state == "blockstun" else False
                )
                if in_target:
                    self._runtime["goal_state_frames"] = int(self._runtime.get("goal_state_frames", 0)) + 1
                else:
                    self._runtime["goal_state_frames"] = 0
                current_f = int(self._runtime.get("goal_state_frames", 0))
                _goal_base(payload)
                payload.update({
                    "current_step_label": f"{current_f}/{needed} frames",
                    "goal_progress_type": "state_duration",
                    "goal_target_state": target_state,
                    "goal_current_frames": current_f,
                    "goal_needed_frames": needed,
                    "goal_timer_active": bool(in_target),
                })
                if needed > 0 and current_f >= needed:
                    return _clear_payload(1, 0, payload["current_step_label"])
                return payload

            if goal_type == "damage_under_hits":
                needed_dmg = int(mission_goal.get("damage", 0) or 0)
                max_hits = int(mission_goal.get("max_hits", 0) or 0)
                combo_dmg = int(self._runtime.get("goal_combo_damage", 0))
                combo_hits = int(self._runtime.get("goal_combo_hits", 0))
                if max_hits > 0 and combo_hits > max_hits:
                    self._runtime["goal_failed"] = True
                _goal_base(payload)
                payload["current_step_label"] = f"{combo_dmg}/{needed_dmg} damage, {combo_hits}/{max_hits} hits"
                if (
                    needed_dmg > 0 and combo_dmg >= needed_dmg
                    and combo_hits <= max_hits
                    and not self._runtime.get("goal_failed", False)
                ):
                    return _clear_payload(1, 0, payload["current_step_label"])
                if not opponent_in_combo_state:
                    self._runtime.update({
                        "goal_combo_damage": 0, "goal_combo_hits": 0,
                        "goal_failed": False, "goal_last_damage_frame": -1,
                    })
                return payload

            if goal_type == "combo_damage":
                needed_dmg = int(mission_goal.get("damage", 0) or 0)
                combo_dmg = int(self._runtime.get("goal_combo_damage", 0))
                _goal_base(payload)
                payload["current_step_label"] = f"{combo_dmg}/{needed_dmg} combo damage"
                if needed_dmg > 0 and combo_dmg >= needed_dmg:
                    return _clear_payload(1, 0, payload["current_step_label"])
                if not opponent_in_combo_state:
                    self._runtime.update({
                        "goal_combo_damage": 0, "goal_combo_hits": 0,
                        "goal_failed": False, "goal_last_damage_frame": -1,
                    })
                return payload

        # Step-list missions
        expected_step_for_reset = steps[progress_index] if progress_index < len(steps) else None
        expected_labels_for_reset = self._step_labels(expected_step_for_reset)

        grace_left = int(self._runtime.get("reset_grace_frames", 0) or 0)
        grace_step_index = self._runtime.get("reset_grace_step_index")
        previous_step = steps[progress_index - 1] if progress_index > 0 else None
        grace_keep_alive_only = self._step_grace_keeps_alive_only(previous_step)

        if progress_index > 0 and grace_keep_alive_only and grace_step_index == progress_index:
            if grace_left > 0:
                grace_left -= 1
                self._runtime["reset_grace_frames"] = grace_left

            if grace_left <= 0:
                progress_index = 0
                self._runtime.update({
                    "progress_index": 0,
                    "pending_step_index": None,
                    "pending_labels": [],
                    "pending_anim": None,
                    "reset_grace_frames": 0,
                    "reset_grace_labels": [],
                    "reset_grace_step_index": None,
                    "reset_grace_keeps_alive_only": False,
                    "shell_installed": False,
                    "shell_release_grace": 0,
                })

        elif progress_index > 0 and not opponent_real_combo_state:
            if grace_left > 0 and grace_step_index == progress_index:
                self._runtime["reset_grace_frames"] = grace_left - 1

            elif self._runtime.get("shell_installed"):
                pass  # don't reset until opponent first hit

            else:
                progress_index = 0
                self._runtime.update({
                    "progress_index": 0,
                    "pending_step_index": None,
                    "pending_labels": [],
                    "pending_anim": None,
                    "reset_grace_frames": 0,
                    "reset_grace_labels": [],
                    "reset_grace_step_index": None,
                    "shell_installed": False,
                    "shell_release_grace": 0,
                })

        last_seen_label = self._runtime.get("last_seen_label", "")
        last_seen_anim = self._runtime.get("last_seen_anim")
        last_seen_hitstun = bool(self._runtime.get("last_seen_hitstun", False))
        last_inputs = self._runtime.get("last_inputs") or {}

        has_fresh_attack_input = self._has_fresh_attack_input(current_inputs, last_inputs)

        is_fresh_instance = (
            current_anim != last_seen_anim
            or current_label != last_seen_label
            or (opponent_in_combo_state and not last_seen_hitstun)
            or has_fresh_attack_input
        )

        expected_step = steps[progress_index] if progress_index < len(steps) else None
        expected_labels = self._step_labels(expected_step)

        generic_partner_var_step = self._step_is_generic_partner_var(mission_id, expected_labels)
        partner_var_matched = generic_partner_var_step and self._partner_matches_generic_var(slot, snaps_dict)

        current_matches_expected = current_label in expected_labels
        non_damage_confirm = self._step_has_non_damage_confirm(expected_labels, snap, current_label)
        step_allows_whiff = self._step_allows_whiff_confirm(expected_labels, expected_step)
        pass_confirm = self._step_is_pass(expected_step)
        zero_damage_confirm = (
            self._step_allows_zero_damage_confirm(character_name, expected_labels, current_label)
            and not pass_confirm
        )
        doronjo_pass = self._doronjo_damage_pass(character_name, expected_labels, damage_values)
        baroque_damage_confirm = (
            self._step_is_baroque_cancel(expected_labels)
            and opponent_in_combo_state
            and baroque_pool_adjusted
        )

        if self._runtime.get("pending_step_index") != progress_index:
            self._runtime.update({
                "pending_step_index": None,
                "pending_labels": [],
                "pending_anim": None,
            })

        pending_step_index = self._runtime.get("pending_step_index")
        pending_labels = list(self._runtime.get("pending_labels") or [])

        reset_grace_active = int(self._runtime.get("reset_grace_frames", 0) or 0) > 0
        reset_grace_confirm_allowed = (
    reset_grace_active
    and not self._step_grace_keeps_alive_only(expected_step)
)
        reset_grace_match = (
            reset_grace_active
            and current_matches_expected
            and not self._label_is_ignorable(current_label)
        )
        post_install_match = (
            character_name == "Saki"
            and int(self._runtime.get("post_install_hold_frames", 0) or 0) > 0
            and current_matches_expected
            and has_fresh_attack_input
            and not self._is_saki_shell_label(current_label)
        )

        baroque_buffered_for_next_step = (
            progress_index + 1 < len(steps)
            and baroque_pool_adjusted
            and opponent_in_combo_state
            and self._step_is_baroque_cancel(
                self._step_labels(steps[progress_index + 1])
            )
        )

        matched_fresh_expected = (
            expected_labels
            and (
                partner_var_matched
                or (
                    current_matches_expected
                    and not self._label_is_ignorable(current_label)
                    and (
                        pass_confirm
                        or (
                            is_fresh_instance
                            and (
                                opponent_in_combo_state
                                or step_allows_whiff
                                or reset_grace_match
                                or zero_damage_confirm
                                or post_install_match
                            )
                        )
                    )
                )
            )
        )

        if partner_var_matched:
            print(f"[mission generic var] slot={slot} step={progress_index} labels={expected_labels!r}")
            progress_index += 1
            self._runtime.update({
                "progress_index": progress_index,
                "pending_step_index": None,
                "pending_labels": [],
                "pending_anim": None,
                "reset_grace_frames": completed_grace,
                "reset_grace_labels": [],
                "reset_grace_step_index": progress_index if completed_grace > 0 else None,
                "shell_install_hold": 0,
            })
            if (
                progress_index < len(steps)
                and self._step_is_baroque_cancel(self._step_labels(steps[progress_index]))
                    and baroque_pool_adjusted
                    and opponent_in_combo_state
                ):
                    progress_index += 1
                    self._runtime.update({
                        "progress_index": progress_index,
                        "pending_step_index": None,
                        "pending_labels": [],
                        "pending_anim": None,
                        "reset_grace_frames": self._step_grace(steps[progress_index - 1]),
                        "reset_grace_labels": [],
                        "reset_grace_step_index": progress_index,
                        "shell_install_hold": 0,
                    })
        elif MISSION_REQUIRE_DAMAGE_CONFIRM:
            if matched_fresh_expected:
                if pass_confirm:
                    print(
                        f"[mission pass] slot={slot} step={progress_index} "
                        f"matched={expected_labels!r}"
                    )

                    pass_grace = self._step_grace(expected_step)

                    progress_index += 1
                    self._runtime.update({
                        "progress_index": progress_index,
                        "reset_grace_frames": pass_grace,
                        "reset_grace_labels": [],
                        "reset_grace_step_index": progress_index if pass_grace > 0 else None,
                        "reset_grace_keeps_alive_only": self._step_grace_keeps_alive_only(expected_step),
                    })

                elif step_allows_whiff or zero_damage_confirm:
                    print(
                        f"[mission confirm immediate] slot={slot} step={progress_index} "
                        f"matched={expected_labels!r} zero={zero_damage_confirm}"
                    )

                    completed_grace = self._step_grace(expected_step)
                    progress_index += 1

                    self._runtime.update({
                        "progress_index": progress_index,
                        "pending_step_index": None,
                        "pending_labels": [],
                        "pending_anim": None,
                        "reset_grace_frames": completed_grace,
                        "reset_grace_labels": [],
                        "reset_grace_step_index": progress_index if completed_grace > 0 else None,
                        "reset_grace_keeps_alive_only": self._step_grace_keeps_alive_only(expected_step),
                        "shell_install_hold": 0,
                        "post_install_hold_frames": 12 if zero_damage_confirm else 0,
                        "shell_installed": zero_damage_confirm,
                        "shell_release_grace": 0,
                        "last_seen_label": "",
                        "last_seen_anim": None,
                        "last_seen_hitstun": False,
                        "last_inputs": {},
                        "hitstun_grace": 0,
                    })

                else:
                    self._runtime.update({
                        "pending_step_index": progress_index,
                        "pending_labels": expected_labels[:],
                        "pending_anim": current_anim,
                    })
                    pending_step_index = progress_index
                    pending_labels = expected_labels[:]

            repeat_same_step_damage = self._can_repeat_same_step_on_damage(
                steps,
                progress_index,
                pending_labels,
                current_label,
                current_anim,
                damage_values,
            )

            if (
                baroque_damage_confirm
                and pending_step_index != progress_index
            ):
                self._runtime.update({
                    "pending_step_index": progress_index,
                    "pending_labels": expected_labels[:],
                    "pending_anim": current_anim,
                })
                pending_step_index = progress_index
                pending_labels = expected_labels[:]

            if (
                pending_step_index == progress_index
                and pending_labels
                and (opponent_in_combo_state or reset_grace_confirm_allowed)
                and (
                    opponent_took_damage
                    or non_damage_confirm
                    or doronjo_pass
                    or repeat_same_step_damage
                    or baroque_damage_confirm
                )
            ):
                completed_grace = self._step_grace(expected_step)

                progress_index += 1

                if (
                    baroque_buffered_for_next_step
                    and progress_index < len(steps)
                    and self._step_is_baroque_cancel(self._step_labels(steps[progress_index]))
                ):
                    progress_index += 1

                self._runtime.update({
                    "progress_index": progress_index,
                    "pending_step_index": None,
                    "pending_labels": [],
                    "pending_anim": None,
                    "reset_grace_frames": completed_grace,
                    "reset_grace_labels": [],
                    "reset_grace_step_index": progress_index if completed_grace > 0 else None,
                    "reset_grace_keeps_alive_only": self._step_grace_keeps_alive_only(expected_step),
                    "shell_install_hold": 0,
                })
        else:
            if matched_fresh_expected:
                print(f"[mission advance] slot={slot} step={progress_index} matched={current_label!r}")
                progress_index += 1
                self._runtime["progress_index"] = progress_index

        if not partner_var_matched:
            self._runtime.update({
                "last_seen_label": current_label,
                "last_seen_anim": current_anim,
                # Store the same sustained signal used for liveness. Otherwise
                # a crouching reaction with an unmapped action ID can look like a
                # new combo edge every frame while the global counter is nonzero.
                "last_seen_hitstun": opponent_in_combo_state,
                "last_inputs": dict(current_inputs),
            })

        if progress_index >= len(steps):
            final_idx = max(0, len(steps) - 1)
            final_label = (
                (" / ".join(steps[final_idx]) if isinstance(steps[final_idx], list) else steps[final_idx])
                if steps else None
            )
            return _clear_payload(len(steps), final_idx, final_label)

        payload.update({
            "just_cleared": False,
            "clear_seq": int(self._runtime.get("clear_seq", 0)),
            "celebrate_pending": bool(self._runtime.get("celebrate_pending", False)),
            "celebrate_token": int(self._runtime.get("celebrate_token", 0) or 0),
            "completed_step_count": progress_index,
            "current_step_index": progress_index,
            "current_step_label": (
                " / ".join(self._step_labels(steps[progress_index]))
                if progress_index < len(steps) else None
            ),
        })
        return payload

    # ------------------------------------------------------------------
    # Empty payload builder
    # ------------------------------------------------------------------

    def _build_empty_overlay_payload(self) -> dict:
        return {
            "active": False,
            "slot": self._active_slot,
            "character": None,
            "mission_count": 0,
            "active_mission_id": None,
            "active_mission_name": None,
            "active_mission_steps": [],
            "missions": [],
            "completed_step_count": 0,
            "current_step_index": 0,
            "current_step_label": None,
            "just_cleared": False,
            "celebrate_pending": False,
            "celebrate_token": 0,
            "selector_open": False,
            "selector_index": 0,
            "selector_hint": "Down, Down, Taunt: Open Mission Select",
            "selector_controls": "Down: Move  Taunt: Select  Mouse still works",
            "scanlines": True,
            "goal_progress_type": None,
            "goal_target_state": None,
            "goal_current_frames": 0,
            "goal_needed_frames": 0,
            "goal_timer_active": False,
        }