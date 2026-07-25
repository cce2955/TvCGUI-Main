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
import re
import time
import threading
from pathlib import Path
from typing import Any

from tvcgui.platform.dolphin import rd8, wd8
from tvcgui.runtime import input_monitor
from tvcgui.core.paths import user_data_path
from tvcgui.features.training.mission_mode import (
    build_overlay_payload,
    load_progress,
    save_progress,
    mark_mission_complete,
    set_selected_mission_id,
)

MISSION_MODE_FILE          = user_data_path("training", "mission_mode_state.json")
MISSION_OVERLAY_FILE       = user_data_path("training", "mission_overlay_data.json")
MISSION_SELECT_FILE        = user_data_path("training", "mission_select_command.json")
MISSION_CELEBRATE_ACK_FILE = user_data_path("training", "mission_celebrate_ack.json")

MISSION_COMMAND_POLL_INTERVAL = 0.10

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
    # 448 is Megacrash/burst. Mission routes classify the forced burst as a
    # defender reaction so the next counter step remains valid.
    48, 49, 50, 52, 53, 60, 61, 62, 64, 65, 66, 73, 74, 75, 76, 79, 80,
    81, 82, 83, 88, 89, 90, 91, 92, 94, 95, 96, 97, 98, 101, 102, 105,
    106, 142, 448, 449,
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
        "Quick Upper B", "yatter step", "Omochama", "Jump Cancel",
    }
}

MISSION_GENERIC_VAR_LABELS = {"VAR", "var"}

MISSION_SELF_VAR_MISSIONS = {"alex_011"}

MISSION_METER_REFILL_MISSIONS = {"ryu_008", "saki_009", "alex_017"}

MISSION_INPUT_DIRECTION_MASK = 0x0F
MISSION_SELECTOR_DOWN_MASK = 0x08
MISSION_SELECTOR_DOWN_DIRECTIONS = frozenset((0x08, 0x09, 0x0A))
MISSION_SELECTOR_REPEAT_WINDOW = 1.35
MISSION_INPUT_A = 0x80
MISSION_INPUT_B = 0x40
MISSION_INPUT_C = 0x20
MISSION_INPUT_P = 0x10
MISSION_INPUT_TAUNT = 0x0C00
MISSION_INPUT_ATTACK_MASK = MISSION_INPUT_A | MISSION_INPUT_B | MISSION_INPUT_C | MISSION_INPUT_P | MISSION_INPUT_TAUNT
MISSION_INPUT_ABC_MASK = MISSION_INPUT_A | MISSION_INPUT_B | MISSION_INPUT_C
MISSION_INPUT_BUTTON_MASKS = {
    "A": MISSION_INPUT_A,
    "B": MISSION_INPUT_B,
    "C": MISSION_INPUT_C,
    "L": MISSION_INPUT_A,
    "M": MISSION_INPUT_B,
    "H": MISSION_INPUT_C,
    "P": MISSION_INPUT_P,
    "T": MISSION_INPUT_TAUNT,
}
def _mission_selector_down_rising(direction: int, previous_direction: int, pressed: int) -> bool:
    """Accept clean down taps and down diagonals for the selector shortcut."""
    current = int(direction) & MISSION_INPUT_DIRECTION_MASK
    previous = int(previous_direction) & MISSION_INPUT_DIRECTION_MASK
    pressed_word = int(pressed) & 0xFFFF
    if pressed_word & MISSION_SELECTOR_DOWN_MASK:
        return True
    return (
        current in MISSION_SELECTOR_DOWN_DIRECTIONS
        and previous not in MISSION_SELECTOR_DOWN_DIRECTIONS
    )


def _mission_route_combo_live(
    global_combo_active: bool,
    opponent_in_hitstun: bool,
    dedicated_megacrash_match: bool = False,
) -> bool:
    """Return whether the defender is in a real mission reaction state.

    The global combo counter remains useful as hit-count evidence, but it can
    stay nonzero after recovery and must not keep an ordered mission route alive.
    Normal routes live only while the defender's primary action is a verified
    reaction state. Megacrash gets its dedicated state-edge frame.
    """
    _ = global_combo_active
    return bool(opponent_in_hitstun or dedicated_megacrash_match)


def _mission_combo_reset_tick(
    progress_index: int,
    combo_live: bool,
    grace_left: int,
    grace_step_index,
) -> tuple[bool, int]:
    """Return ``(reset_now, remaining_grace)`` for one mission frame.

    Grace is consumed only after both combo-counter and hitstun liveness end.
    A step with N grace frames receives exactly N dropped-combo frames.
    Without matching explicit grace, any dropped route resets immediately.
    """
    progress = max(0, int(progress_index or 0))
    remaining = max(0, int(grace_left or 0))
    if progress <= 0 or bool(combo_live):
        return False, remaining
    if remaining > 0 and grace_step_index == progress:
        return False, remaining - 1
    return True, 0


MISSION_INPUT_BAROQUE_PAIRS = (
    ("AP", MISSION_INPUT_A | MISSION_INPUT_P),
    ("BP", MISSION_INPUT_B | MISSION_INPUT_P),
    ("CP", MISSION_INPUT_C | MISSION_INPUT_P),
)
MISSION_INPUT_EVENT_WINDOW = 60
MISSION_INPUT_NORMAL_LATCH = 14
MISSION_INPUT_BAROQUE_LATCH = 14
MISSION_INPUT_LEGS_WINDOW = 42
MISSION_INPUT_COMMAND_WINDOW = 36
MISSION_INPUT_MASH_COUNT = 4
MISSION_HIT_CONFIRM_WINDOW = 16
MISSION_HIT_CORRELATION_WINDOW = 4
MISSION_HIT_PRELABEL_WINDOW = 8
MISSION_BUFFERED_STEP_LIMIT = 16
MISSION_BUFFERED_NORMAL_HIT_WINDOW = 20

# Prediction is display-only until a real HP or combo event confirms the hit.
# Native action IDs let the HUD move with the player immediately, while frame
# data supplies a bounded confirmation window for each attack.
MISSION_PREDICTION_MAX_STEPS = 16
MISSION_PREDICTION_FALLBACK_STARTUP = 12
MISSION_PREDICTION_FALLBACK_ACTIVE = 4
MISSION_PREDICTION_CONFIRM_PADDING = 12
MISSION_PREDICTION_MAX_AGE_SECONDS = 1.50
MISSION_ACTION_EVENT_LIMIT = 32
MISSION_HIT_EVENT_LIMIT = 32

_MISSION_DIRECTION_MIRROR = {"1": "3", "3": "1", "4": "6", "6": "4", "7": "9", "9": "7"}

DORONJO_DAMAGE_PASS: dict[str, set] = {
    "clutch a": {2}, "clutch b": {2}, "clutch c": {2},
    "pummel a": {880}, "pummel b": {880}, "pummel c": {880},
    "tree a": {1360}, "tree b": {1360}, "tree c": {1360},
    "rock a": {4480}, "rock b": {4480}, "rock c": {4480},
}

MISSION_GENERIC_ACTION_LABELS = {
    0x100: "5A",
    0x101: "5B",
    0x102: "5C",
    0x103: "2A",
    0x104: "2B",
    0x105: "2C",
    0x106: "6C",
    0x107: "4C",
    0x108: "3C",
    0x109: "j.A",
    0x10A: "j.B",
    0x10B: "j.C",
    0x10C: "j.6C",
    0x10D: "j.4C",
    0x10E: "6B",
}
MISSION_AIR_NORMAL_ACTIONS = {
    0x109: "A",
    0x10A: "B",
    0x10B: "C",
    0x10C: "C",
    0x10D: "C",
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
        "mission_input_events": [],
        "mission_input_serial": 0,
        "mission_input_consumed_serial": 0,
        "mission_action_events": [],
        "mission_action_serial": 0,
        "mission_action_consumed_serial": 0,
        "mission_last_action_id": None,
        "mission_last_action_sample_seq": -1,
        "mission_realtime_hp_by_slot": {},
        "mission_realtime_hitstun_by_slot": {},
        "mission_input_match_serial": 0,
        "mission_step_start_serial": 0,
        "pending_input_serial": 0,
        "mission_prev_direction": None,
        "mission_prev_buttons": 0,
        "mission_baroque_latch": 0,
        "mission_last_input_token": "",
        "mission_last_input_frame": -9999,
        "mission_hit_events": [],
        "mission_hit_serial": 0,
        "mission_last_hit_evidence_frame": -9999,
        "mission_buffered_advances": 0,
        "predicted_progress_index": 0,
        "prediction_entries": [],
        "prediction_revision": 0,
        "pending_label_confirmed": False,
        "pending_arm_source": "",
        "last_global_combo_count": None,
        "last_actual_hitstun": False,
        "last_opponent_megacrash": False,
        "hitstun_grace": 0,
        "global_combo_count": 0,
        "pending_step_index": None,
        "pending_labels": [],
        "pending_anim": None,
        "pending_started_frame": -9999,
        "opponent_hp_by_base": {},
           "attacker_hp_by_base": {},
        "goal_state_frames": 0,
        "goal_combo_damage": 0,
        "goal_combo_hits": 0,
        "goal_hp_hit_count": 0,
        "goal_hit_latch": 0,
        "goal_failed": False,
        "goal_last_damage_frame": -1,
        "last_sampled_damage_frame": -1,
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
        self._mission_timing_by_action = self._load_mission_frame_timings()

        # Core runtime state
        self._runtime: dict = _new_mission_runtime()
        self._active_slot: str | None = None

        # Selector state
        self._selector: dict = {
            "open": False,
            "selected_index": 0,
            "sequence": [],
            "last_direction": 0,
            "last_taunt_held": False,
            "opened_at": 0.0,
            "hint_until": 0.0,
        }

        # Debug override save/restore state
        self._setup_state: dict = {
            "mission_key": None,
            "saved_debug_values": {},
            "applied_debug_values": {},
        }

        # Frame index, updated each call to update().
        self._frame_idx: int = 0

        # Health history is manager-wide rather than mission-wide. Mission
        # selection and route resets must not erase the baseline needed to
        # recognize the next frame's first hit.
        self._health_history_by_base: dict[int, dict[str, Any]] = {}
        self._health_damage_events: list[dict[str, Any]] = []
        self._health_damage_frame: int = -1

        # Last overlay payload built by write_overlay_data().  main.py uses this
        # to sync mission-scoped helpers such as Megacrash Trainer without
        # rereading the JSON file the module just wrote.
        self._last_overlay_payload: dict = self._build_empty_overlay_payload()
        self._next_command_poll: float = 0.0
        self._last_overlay_serialized: str = ""
        self._last_overlay_write_time: float = 0.0
        self._last_overlay_progress_signature: tuple = ()
        self._last_mode_serialized: str = ""
        self._overlay_write_condition = threading.Condition()
        self._pending_overlay_payload: dict | None = None
        self._overlay_writer_stop = False
        self._overlay_writer_thread = threading.Thread(
            target=self._overlay_writer_loop,
            name="TvCMissionOverlayWriter",
            daemon=True,
        )
        self._overlay_writer_thread.start()

        # The normal GUI loop can be busy, but HudOverlayManager already owns a
        # dedicated 240 Hz Dolphin input sampler. Mission mode consumes that
        # ordered queue so short taps and complete strings are never reduced to
        # one late 60 Hz snapshot.
        self._input_sample_provider = None
        self._input_sample_cursor_by_consumer: dict[tuple[str, str], int] = {}

    def _load_mission_frame_timings(self) -> dict[tuple[int, int], dict[str, int]]:
        """Load compact startup, active, and hitstun data for prediction.

        Mission mode only needs three integers per native action. Keeping this
        small index avoids searching the full frame-data profile on every hit.
        """
        try:
            from tvcgui.features.frame_data.profile_store import iter_frame_data_profiles
            profiles = iter_frame_data_profiles()
        except Exception:
            return {}

        timings: dict[tuple[int, int], dict[str, int]] = {}
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            try:
                char_id = int(profile.get("char_id", 0) or 0)
            except Exception:
                char_id = 0
            if char_id <= 0:
                continue
            for move in profile.get("moves", []) or []:
                if not isinstance(move, dict):
                    continue
                try:
                    action_id = int(move.get("id", 0) or 0) & 0x7FFF
                except Exception:
                    action_id = 0
                if action_id < 0x100:
                    continue

                def _int_field(*names: str) -> int:
                    for name in names:
                        value = move.get(name)
                        try:
                            if value is not None and str(value).strip() != "":
                                return max(0, int(float(value)))
                        except Exception:
                            continue
                    return 0

                startup = _int_field("active_start", "startup")
                active_end = _int_field("active_end")
                hitstun = _int_field("hitstun")
                active = max(
                    1,
                    active_end - startup + 1
                    if startup > 0 and active_end >= startup
                    else MISSION_PREDICTION_FALLBACK_ACTIVE,
                )
                candidate = {
                    "startup": startup or MISSION_PREDICTION_FALLBACK_STARTUP,
                    "active": active,
                    "hitstun": hitstun,
                }
                key = (char_id, action_id)
                existing = timings.get(key)
                if existing is None or candidate["startup"] < existing["startup"]:
                    timings[key] = candidate
        return timings

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

    def set_input_sample_provider(self, provider) -> None:
        """Use a high-frequency input source supplied by HudOverlayManager."""
        self._input_sample_provider = provider

    def _sync_input_cursors_to_latest(self, slot_label: str, snap: dict | None = None) -> None:
        provider = self._input_sample_provider
        if not callable(provider) or not slot_label:
            return
        try:
            _latest, samples = provider(
                slot_label,
                int((snap or {}).get("base") or 0),
            )
        except Exception:
            return
        newest = max(
            [int((sample or {}).get("seq", 0) or 0) for sample in (samples or [])]
            or [0]
        )
        for consumer in ("selector", "mission", "mission-opponent"):
            self._input_sample_cursor_by_consumer[(consumer, str(slot_label))] = newest

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
        self._sample_health_deltas(render_snap_by_slot, frame_idx)
        self._update_selector_from_inputs(snaps, now)
        if now >= self._next_command_poll:
            self._next_command_poll = now + MISSION_COMMAND_POLL_INTERVAL
            self.consume_select_command()
            self.consume_celebrate_ack()

        # Evaluate and publish mission state immediately, before the GUI's
        # profiler and rendering work. write_overlay_data is content-deduped, so
        # an unchanged frame performs no filesystem write.
        self.write_mode_state()
        self.write_overlay_data(render_snap_by_slot)

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
        if not os.path.isfile(MISSION_SELECT_FILE):
            return
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
        if not os.path.isfile(MISSION_CELEBRATE_ACK_FILE):
            return
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

    def _queue_overlay_payload(self, payload: dict) -> None:
        with self._overlay_write_condition:
            self._pending_overlay_payload = dict(payload or {})
            self._overlay_write_condition.notify()

    def _overlay_writer_loop(self) -> None:
        while True:
            with self._overlay_write_condition:
                while self._pending_overlay_payload is None and not self._overlay_writer_stop:
                    self._overlay_write_condition.wait(timeout=0.25)
                if self._overlay_writer_stop and self._pending_overlay_payload is None:
                    return
                payload = self._pending_overlay_payload
                self._pending_overlay_payload = None
            try:
                serialized = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                if serialized == self._last_overlay_serialized and os.path.isfile(MISSION_OVERLAY_FILE):
                    continue
                tmp = f"{MISSION_OVERLAY_FILE}.tmp"
                os.makedirs(os.path.dirname(MISSION_OVERLAY_FILE), exist_ok=True)
                with open(tmp, "w", encoding="utf-8") as handle:
                    handle.write(serialized)
                os.replace(tmp, MISSION_OVERLAY_FILE)
                self._last_overlay_serialized = serialized
            except Exception:
                pass

    # ------------------------------------------------------------------
    # File writers
    # ------------------------------------------------------------------

    def write_overlay_data(self, render_snap_by_slot: dict | None = None, *, force: bool = False) -> None:
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

        progress_signature = (
            bool(payload.get("active")),
            str(payload.get("active_mission_id") or ""),
            int(payload.get("completed_step_count", 0) or 0),
            int(payload.get("current_step_index", 0) or 0),
            bool(payload.get("just_cleared", False)),
            int(payload.get("clear_seq", 0) or 0),
        )
        # Do not time-throttle mission UI changes. The latest-only writer
        # publishes the newest state and discards stale intermediate payloads.
        self._last_overlay_progress_signature = progress_signature
        self._last_overlay_write_time = float(getattr(self, "_now", 0.0) or time.time())
        self._queue_overlay_payload(payload)


    def write_mode_state(self, *, force: bool = False) -> None:
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
            serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            if not force and serialized == self._last_mode_serialized:
                return
            os.makedirs(os.path.dirname(MISSION_MODE_FILE), exist_ok=True)
            with open(MISSION_MODE_FILE, "w", encoding="utf-8") as f:
                f.write(serialized)
            self._last_mode_serialized = serialized
        except Exception:
            pass

    def close(self) -> None:
        with self._overlay_write_condition:
            self._overlay_writer_stop = True
            self._overlay_write_condition.notify_all()
        if self._overlay_writer_thread.is_alive():
            self._overlay_writer_thread.join(timeout=1.0)

    def restore_debug_overrides(self) -> None:
        'Call at shutdown to restore any debug flags the module overwrote.'
        self._restore_debug_overrides()

    # ------------------------------------------------------------------
    # Toggle active slot (called from click handler in main)
    # ------------------------------------------------------------------

    def toggle_active_slot(self, slot_label: str) -> None:
        self._active_slot = None if self._active_slot == slot_label else slot_label
        if self._active_slot:
            snap = getattr(self, "_render_snap_by_slot", {}).get(self._active_slot)
            self._sync_input_cursors_to_latest(self._active_slot, snap)
        self._runtime = _new_mission_runtime(slot=self._active_slot)
        self.write_mode_state(force=True)
        self.write_overlay_data(force=True)

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

    def _input_packets_for_slot(
        self,
        slot_label: str,
        snap: dict,
        *,
        consumer: str,
    ) -> list[dict]:
        """Return every unseen high-frequency sample for one independent consumer."""
        provider = self._input_sample_provider
        latest: dict = {}
        samples: list[dict] = []
        if callable(provider):
            try:
                latest, samples = provider(
                    slot_label,
                    int((snap or {}).get("base") or 0),
                )
            except Exception:
                latest, samples = {}, []

        cursor_key = (str(consumer), str(slot_label))
        cursor = int(self._input_sample_cursor_by_consumer.get(cursor_key, 0) or 0)
        fresh = [
            dict(sample)
            for sample in (samples or [])
            if int((sample or {}).get("seq", 0) or 0) > cursor
        ]
        fresh.sort(key=lambda sample: int(sample.get("seq", 0) or 0))
        if fresh:
            self._input_sample_cursor_by_consumer[cursor_key] = max(
                int(sample.get("seq", 0) or 0) for sample in fresh
            )
            return fresh

        if latest:
            packet = dict(latest)
            packet.setdefault("seq", cursor)
            return [packet]

        try:
            packet = input_monitor.read_overlay_input_packet(
                slot_label,
                int((snap or {}).get("base") or 0),
            )
        except Exception:
            packet = {}
        return [packet] if packet else []

    def _update_selector_from_inputs(self, snaps_dict: dict, now: float) -> None:
        """Drive Mission Select from every captured Down and Taunt edge."""
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

        packets = self._input_packets_for_slot(
            selector_slot, snap, consumer="selector"
        )
        if not packets:
            return

        for sample_index, packet in enumerate(packets):
            sample_now = float(now) + sample_index * 0.000001
            held = int((packet or {}).get("held") or 0) & 0xFFFF
            pressed = int((packet or {}).get("pressed") or 0) & 0xFFFF
            direction = held & MISSION_INPUT_DIRECTION_MASK
            previous_direction = int(self._selector.get("last_direction", 0) or 0)

            down_rising = _mission_selector_down_rising(direction, previous_direction, pressed)
            up_rising = direction == 0x04 and previous_direction != 0x04
            taunt_held = (held & MISSION_INPUT_TAUNT) == MISSION_INPUT_TAUNT
            taunt_pressed = (pressed & MISSION_INPUT_TAUNT) == MISSION_INPUT_TAUNT
            taunt_rising = taunt_pressed or (
                taunt_held and not bool(self._selector.get("last_taunt_held", False))
            )

            if self._selector["open"]:
                if sample_now - self._selector["opened_at"] > 8.0:
                    self._close_selector()
                else:
                    if down_rising:
                        self._selector["selected_index"] = (
                            int(self._selector.get("selected_index", 0)) + 1
                        ) % len(missions)
                        self._selector["opened_at"] = sample_now
                        self._selector["hint_until"] = sample_now + 8.0

                    if up_rising:
                        self._selector["selected_index"] = (
                            int(self._selector.get("selected_index", 0)) - 1
                        ) % len(missions)
                        self._selector["opened_at"] = sample_now
                        self._selector["hint_until"] = sample_now + 8.0

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
                            self.write_mode_state(force=True)
            else:
                if down_rising:
                    seq = self._selector["sequence"]
                    if not seq or (sample_now - seq[-1]) <= MISSION_SELECTOR_REPEAT_WINDOW:
                        seq.append(sample_now)
                    else:
                        seq[:] = [sample_now]
                    if len(seq) > 2:
                        del seq[:-2]

                if taunt_rising and len(self._selector["sequence"]) >= 2:
                    if (
                        self._selector["sequence"][-1]
                        - self._selector["sequence"][-2]
                    ) <= MISSION_SELECTOR_REPEAT_WINDOW:
                        self._active_slot = selector_slot
                        self._runtime = _new_mission_runtime(slot=self._active_slot)
                        self._open_selector(character_name, sample_now)
                        self.write_mode_state(force=True)

            self._selector["last_direction"] = direction
            self._selector["last_taunt_held"] = taunt_held

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

    def _sample_health_deltas(self, snaps_dict: dict, frame_idx: int) -> None:
        """Capture health changes continuously, even while mission mode is idle."""
        events: list[dict[str, Any]] = []
        live_bases: set[int] = set()

        for snap in (snaps_dict or {}).values():
            if not isinstance(snap, dict):
                continue
            base = snap.get("base")
            cur_hp = snap.get("cur")
            teamtag = str(snap.get("teamtag") or "")
            if not isinstance(base, int) or not isinstance(cur_hp, int):
                continue

            live_bases.add(base)
            previous = self._health_history_by_base.get(base)
            if isinstance(previous, dict):
                prev_hp = previous.get("cur")
                if isinstance(prev_hp, int) and cur_hp < prev_hp:
                    events.append({
                        "base": base,
                        "teamtag": teamtag,
                        "damage": prev_hp - cur_hp,
                    })

            self._health_history_by_base[base] = {
                "cur": cur_hp,
                "teamtag": teamtag,
            }

        for base in list(self._health_history_by_base):
            if base not in live_bases:
                del self._health_history_by_base[base]

        self._health_damage_events = events
        self._health_damage_frame = int(frame_idx)

    def _opponent_in_state(self, slot_label: str, snaps_dict: dict, state_ids: set) -> bool:
        if not slot_label:
            return False

        my_team = "P1" if slot_label.startswith("P1") else "P2"

        for other_snap in snaps_dict.values():
            if not isinstance(other_snap, dict):
                continue
            if other_snap.get("teamtag") == my_team:
                continue

            # Only the primary live action is authoritative. ``attB`` can
            # retain an old reaction value after recovery and previously kept
            # missions alive long enough for late steps to count.
            att_a = other_snap.get("attA")
            if att_a in state_ids:
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

        # Prefer the manager-wide sample. It is collected before mission
        # commands and survives mission changes, so the first hit cannot be
        # lost just because the mission runtime was reset that frame.
        sampled_frame = int(self._health_damage_frame)
        if (
            sampled_frame == int(self._frame_idx)
            and int(self._runtime.get("last_sampled_damage_frame", -1)) != sampled_frame
        ):
            self._runtime["last_sampled_damage_frame"] = sampled_frame
            return [
                int(event.get("damage", 0) or 0)
                for event in self._health_damage_events
                if str(event.get("teamtag") or "") != my_team
                and int(event.get("damage", 0) or 0) > 0
            ]

        # Fallback for direct calls that occur without update().
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


    def _record_mission_hit_events(
        self,
        global_combo_count: int | None,
        damage_values: list[int],
        frame_idx: int,
    ) -> int:
        """Merge HP and combo-counter evidence into physical hit events.

        TvC does not publish the related writes in a stable order. HP can fall
        before the combo counter changes, or the counter can change first. An
        HP edge creates an event immediately for responsiveness. A nearby combo
        edge is attached to that same event instead of creating a duplicate.
        """
        events = self._runtime.setdefault("mission_hit_events", [])
        previous_count = self._runtime.get("last_global_combo_count")

        combo_hits = 0
        if global_combo_count is not None:
            current_count = max(0, int(global_combo_count))
            if previous_count is None:
                # A fresh mission/runtime can first observe the counter after
                # several rapid hits. Preserve the full count instead of
                # collapsing the entire opening string into one hit event.
                combo_hits = current_count
            else:
                previous_count = max(0, int(previous_count))
                if current_count > previous_count:
                    combo_hits = current_count - previous_count
            self._runtime["last_global_combo_count"] = current_count

        positive_damage = [int(value) for value in damage_values if int(value or 0) > 0]
        new_event_count = 0

        def new_event(*, hp_seen: bool, combo_seen: bool, damage: int = 0) -> dict:
            nonlocal new_event_count
            serial = int(self._runtime.get("mission_hit_serial", 0) or 0) + 1
            self._runtime["mission_hit_serial"] = serial
            event = {
                "serial": serial,
                "frame": int(frame_idx),
                "last_frame": int(frame_idx),
                "hp_seen": bool(hp_seen),
                "combo_seen": bool(combo_seen),
                "damage": max(0, int(damage or 0)),
                "consumed": False,
            }
            events.append(event)
            new_event_count += 1
            return event

        # HP is the lowest-latency signal. Pair it with a combo-only event from
        # the previous few frames when the counter happened to publish first.
        for damage in positive_damage:
            match = next((
                event for event in reversed(events)
                if not event.get("hp_seen")
                and 0 <= int(frame_idx) - int(event.get("last_frame", event.get("frame", frame_idx))) <= MISSION_HIT_CORRELATION_WINDOW
            ), None)
            if match is None:
                new_event(hp_seen=True, combo_seen=False, damage=damage)
            else:
                match["hp_seen"] = True
                match["damage"] = int(match.get("damage", 0) or 0) + damage
                match["last_frame"] = int(frame_idx)

        # Counter increments are authoritative for hit count. Pair each one to
        # a recent HP-only event before allocating another physical hit.
        for _ in range(max(0, int(combo_hits))):
            match = next((
                event for event in reversed(events)
                if not event.get("combo_seen")
                and 0 <= int(frame_idx) - int(event.get("last_frame", event.get("frame", frame_idx))) <= MISSION_HIT_CORRELATION_WINDOW
            ), None)
            if match is None:
                new_event(hp_seen=False, combo_seen=True)
            else:
                match["combo_seen"] = True
                match["last_frame"] = int(frame_idx)

        if positive_damage or combo_hits > 0 or new_event_count > 0:
            self._runtime["mission_last_hit_evidence_frame"] = int(frame_idx)

        events[:] = [
            event for event in events
            if int(frame_idx) - int(event.get("frame", frame_idx)) <= MISSION_HIT_CONFIRM_WINDOW
            and not (
                bool(event.get("consumed"))
                and int(frame_idx) - int(event.get("last_frame", event.get("frame", frame_idx))) > MISSION_HIT_CORRELATION_WINDOW
            )
        ]
        return new_event_count

    def _peek_mission_hit_event(self, frame_idx: int, min_frame: int | None = None) -> dict | None:
        floor = -999999 if min_frame is None else int(min_frame)
        for event in self._runtime.get("mission_hit_events", []):
            event_frame = int(event.get("frame", frame_idx))
            age = int(frame_idx) - event_frame
            if (
                event_frame >= floor
                and 0 <= age <= MISSION_HIT_CONFIRM_WINDOW
                and not event.get("consumed")
            ):
                return event
        return None

    def _consume_mission_hit_event(self, frame_idx: int, min_frame: int | None = None) -> bool:
        event = self._peek_mission_hit_event(frame_idx, min_frame=min_frame)
        if not event:
            return False
        event["consumed"] = True
        return True

    def _mission_input_event_by_serial(self, serial: int) -> dict | None:
        wanted = int(serial or 0)
        if wanted <= 0:
            return None
        for event in self._runtime.get("mission_input_events", []):
            if int(event.get("serial", 0) or 0) == wanted:
                return event
        return None

    def _action_event_matches_step(self, event: dict, step: Any, labels: list[str]) -> bool:
        candidates = list((event or {}).get("labels") or [])
        notation = self._step_input_notation(step)
        expected = list(labels or [])
        if notation:
            expected.append(notation)
        for candidate in candidates:
            if self._mission_label_matches(str(candidate), expected):
                return True
        return False

    def _prediction_timing_for_action(self, event: dict) -> dict[str, int]:
        char_id = int((event or {}).get("char_id", 0) or 0)
        action_id = int((event or {}).get("action_id", 0) or 0) & 0x7FFF
        timing = self._mission_timing_by_action.get((char_id, action_id))
        if timing:
            return dict(timing)
        return {
            "startup": MISSION_PREDICTION_FALLBACK_STARTUP,
            "active": MISSION_PREDICTION_FALLBACK_ACTIVE,
            "hitstun": 0,
        }

    def _prediction_window_ns(self, event: dict) -> tuple[int, int]:
        timing = self._prediction_timing_for_action(event)
        startup = max(1, int(timing.get("startup", 0) or 0))
        active = max(1, int(timing.get("active", 0) or 0))
        start_ns = int((event or {}).get("sample_ns", 0) or 0)
        if start_ns <= 0:
            start_ns = time.monotonic_ns()
        earliest = start_ns + int(max(0, startup - 2) * (1_000_000_000 / 60.0))
        latest_frames = startup + active + MISSION_PREDICTION_CONFIRM_PADDING
        latest = start_ns + int(latest_frames * (1_000_000_000 / 60.0))
        return earliest, latest

    def _step_supports_prediction(self, step: Any, labels: list[str]) -> bool:
        """Return whether the step may advance visually before hit confirmation."""
        if not labels or self._step_grace(step) > 0:
            return False
        if (
            self._step_is_pass(step)
            or self._step_is_baroque_cancel(labels)
            or self._step_is_megacrash(labels)
            or self._step_allows_whiff_confirm(labels, step)
        ):
            return False
        return True

    def _refresh_predicted_progress(
        self,
        steps: list,
        confirmed_progress: int,
    ) -> int:
        """Predict the ordered prefix from native actions without confirming it.

        The real progress index remains authoritative. This method only lets the
        overlay follow already-observed native action transitions while HP and
        combo evidence arrive a few frames later.
        """
        confirmed = max(0, int(confirmed_progress or 0))
        if confirmed >= len(steps):
            self._runtime["predicted_progress_index"] = confirmed
            self._runtime["prediction_entries"] = []
            return confirmed

        now_ns = time.monotonic_ns()
        max_age_ns = int(MISSION_PREDICTION_MAX_AGE_SECONDS * 1_000_000_000)
        actions = sorted(
            [
                event for event in self._runtime.get("mission_action_events", [])
                if not event.get("consumed")
                and not event.get("prediction_rejected")
                and int(event.get("serial", 0) or 0)
                > int(self._runtime.get("mission_action_consumed_serial", 0) or 0)
                and (
                    int(event.get("sample_ns", 0) or 0) <= 0
                    or now_ns - int(event.get("sample_ns", 0) or 0) <= max_age_ns
                )
            ],
            key=lambda event: (
                int(event.get("sample_ns", 0) or 0),
                int(event.get("sample_seq", 0) or 0),
                int(event.get("serial", 0) or 0),
            ),
        )

        predicted = confirmed
        entries: list[dict[str, Any]] = []
        action_cursor = 0
        while (
            predicted < len(steps)
            and len(entries) < MISSION_PREDICTION_MAX_STEPS
        ):
            step = steps[predicted]
            labels = self._step_labels(step)
            if not self._step_supports_prediction(step, labels):
                break

            match_index = None
            for index in range(action_cursor, len(actions)):
                action = actions[index]
                if self._action_event_matches_step(action, step, labels):
                    match_index = index
                    break
                # Native attack actions are ordered route evidence. Do not jump
                # over a different attack to make a later step appear valid.
                if int(action.get("action_id", 0) or 0) >= 0x100:
                    break
            if match_index is None:
                break

            action = actions[match_index]
            earliest_ns, deadline_ns = self._prediction_window_ns(action)
            if now_ns > deadline_ns:
                action["prediction_rejected"] = True
                action_cursor = match_index + 1
                continue

            timing = self._prediction_timing_for_action(action)
            entries.append({
                "step_index": predicted,
                "action_serial": int(action.get("serial", 0) or 0),
                "action_id": int(action.get("action_id", 0) or 0),
                "label": str((action.get("labels") or [""])[0]),
                "startup": int(timing.get("startup", 0) or 0),
                "active": int(timing.get("active", 0) or 0),
                "hitstun": int(timing.get("hitstun", 0) or 0),
                "earliest_confirm_ns": earliest_ns,
                "deadline_ns": deadline_ns,
            })
            predicted += 1
            action_cursor = match_index + 1

        previous = int(self._runtime.get("predicted_progress_index", confirmed) or confirmed)
        self._runtime["predicted_progress_index"] = predicted
        self._runtime["prediction_entries"] = entries
        if predicted != previous:
            self._runtime["prediction_revision"] = int(
                self._runtime.get("prediction_revision", 0) or 0
            ) + 1
        return predicted

    def _prune_mission_event_buffers(self) -> None:
        """Keep only the compact live suffix required by mission matching."""
        actions = list(self._runtime.get("mission_action_events", []) or [])
        actions = [event for event in actions if not event.get("consumed")]
        self._runtime["mission_action_events"] = actions[-MISSION_ACTION_EVENT_LIMIT:]

        # Keep recently consumed hits briefly because the global combo counter
        # may update after the HP edge and still needs to correlate with that
        # same physical hit instead of creating a duplicate.
        hits = list(self._runtime.get("mission_hit_events", []) or [])
        self._runtime["mission_hit_events"] = hits[-MISSION_HIT_EVENT_LIMIT:]

        inputs = list(self._runtime.get("mission_input_events", []) or [])
        consumed = int(self._runtime.get("mission_input_consumed_serial", 0) or 0)
        inputs = [
            event for event in inputs
            if int(event.get("serial", 0) or 0) > consumed
        ]
        self._runtime["mission_input_events"] = inputs[-64:]

    def _find_action_hit_pair_for_step(
        self,
        step: Any,
        labels: list[str],
        frame_idx: int,
    ) -> tuple[dict, dict] | None:
        actions = sorted(
            [
                event for event in self._runtime.get("mission_action_events", [])
                if not event.get("consumed")
                and int(event.get("serial", 0) or 0)
                > int(self._runtime.get("mission_action_consumed_serial", 0) or 0)
            ],
            key=lambda event: (
                int(event.get("sample_seq", 0) or 0),
                int(event.get("serial", 0) or 0),
            ),
        )
        hits = sorted(
            [
                hit for hit in self._runtime.get("mission_hit_events", [])
                if not hit.get("consumed")
                and 0 <= int(frame_idx) - int(hit.get("frame", frame_idx)) <= MISSION_HIT_CONFIRM_WINDOW
            ],
            key=lambda hit: (
                int(hit.get("sample_seq", 0) or 0),
                int(hit.get("serial", 0) or 0),
            ),
        )
        for action in actions:
            if not self._action_event_matches_step(action, step, labels):
                continue
            action_serial = int(action.get("serial", 0) or 0)
            action_seq = int(action.get("sample_seq", 0) or 0)
            for hit in hits:
                owner = int(hit.get("owner_action_serial", 0) or 0)
                hit_seq = int(hit.get("sample_seq", 0) or 0)
                if owner and owner != action_serial:
                    continue
                if hit_seq and action_seq and hit_seq < action_seq:
                    continue
                return action, hit
        return None

    def _drain_buffered_action_steps(
        self,
        steps: list,
        progress_index: int,
        frame_idx: int,
    ) -> tuple[int, int]:
        progress = max(0, int(progress_index or 0))
        advanced = 0
        while progress < len(steps) and advanced < MISSION_BUFFERED_STEP_LIMIT:
            step = steps[progress]
            labels = self._step_labels(step)
            if not labels:
                break
            if (
                self._step_is_pass(step)
                or self._step_is_baroque_cancel(labels)
                or self._step_is_megacrash(labels)
                or self._step_allows_whiff_confirm(labels, step)
            ):
                break
            pair = self._find_action_hit_pair_for_step(step, labels, frame_idx)
            if pair is None:
                break
            action_event, hit_event = pair
            action_event["consumed"] = True
            hit_event["consumed"] = True
            action_serial = int(action_event.get("serial", 0) or 0)
            input_serial = int(action_event.get("input_serial", 0) or 0)
            self._runtime["mission_action_consumed_serial"] = max(
                int(self._runtime.get("mission_action_consumed_serial", 0) or 0),
                action_serial,
            )
            if input_serial > 0:
                self._consume_mission_input_through(input_serial)
            completed_grace = self._step_grace(step)
            progress += 1
            advanced += 1
            self._runtime.update({
                "progress_index": progress,
                "pending_step_index": None,
                "pending_labels": [],
                "pending_anim": None,
                "pending_started_frame": -9999,
                "pending_input_serial": 0,
                "pending_label_confirmed": False,
                "pending_arm_source": "",
                "reset_grace_frames": completed_grace,
                "reset_grace_labels": [],
                "reset_grace_step_index": progress if completed_grace > 0 else None,
                "reset_grace_keeps_alive_only": self._step_grace_keeps_alive_only(step),
                "mission_step_start_serial": input_serial,
                "shell_install_hold": 0,
                "mission_last_hit_evidence_frame": int(frame_idx),
            })
        return progress, advanced

    def _buffered_normal_spec(self, step: Any, labels: list[str]) -> tuple[str, str] | None:
        """Return a fast input spec for ordinary ground or air normals.

        These are safe to queue directly because the input uniquely names the
        required attack. Specials and system actions continue through the full
        label-aware checker below.
        """
        candidates: list[str] = []
        notation = self._step_input_notation(step)
        if notation:
            candidates.append(notation)
        candidates.extend(labels or [])

        for raw in candidates:
            text = str(raw or "").strip().upper().replace(" ", "")
            match = re.fullmatch(r"([1-9])([ABC])", text)
            if match:
                return match.group(1), match.group(2)
            air_match = re.fullmatch(r"(?:J\.|AIR|JUMP(?:ING)?)([ABC])", text)
            if air_match:
                return "AIR", air_match.group(1)
        return None

    def _find_buffered_normal_input(
        self,
        step: Any,
        labels: list[str],
        frame_idx: int,
    ) -> dict | None:
        spec = self._buffered_normal_spec(step, labels)
        if spec is None:
            return None
        wanted_direction, wanted_button = spec
        wanted_mask = MISSION_INPUT_BUTTON_MASKS.get(wanted_button, 0)
        if not wanted_mask:
            return None

        events = sorted(
            self._recent_mission_events(frame_idx, MISSION_INPUT_EVENT_WINDOW),
            key=lambda event: int(event.get("serial", 0) or 0),
        )
        for event in events:
            pressed = int(event.get("pressed", 0) or 0)
            if not (pressed & wanted_mask):
                continue
            direction = self._mission_event_direction_digit(event)
            if wanted_direction == "AIR":
                action_id = int(event.get("action_id", 0) or 0) & 0x7FFF
                action_button = MISSION_AIR_NORMAL_ACTIONS.get(action_id)
                if action_button == wanted_button:
                    return event
                if direction not in {"7", "8", "9"}:
                    # Air normals are commonly pressed after the stick returns
                    # to neutral. Accept a recent jump direction instead of
                    # relabeling that input as a grounded normal.
                    serial = int(event.get("serial", 0) or 0)
                    prior_jump = any(
                        int(prior.get("serial", 0) or 0) < serial
                        and serial - int(prior.get("serial", 0) or 0) <= 12
                        and self._mission_event_direction_digit(prior) in {"7", "8", "9"}
                        for prior in events
                    )
                    if not prior_jump:
                        continue
            else:
                mirrored = _MISSION_DIRECTION_MIRROR.get(wanted_direction, wanted_direction)
                if direction not in {wanted_direction, mirrored}:
                    continue
            return event
        return None

    def _find_hit_for_buffered_input(self, input_event: dict, frame_idx: int) -> dict | None:
        input_frame = int((input_event or {}).get("frame", frame_idx))
        candidates = []
        for hit in self._runtime.get("mission_hit_events", []):
            if hit.get("consumed"):
                continue
            hit_frame = int(hit.get("frame", frame_idx))
            age = int(frame_idx) - hit_frame
            if not 0 <= age <= MISSION_HIT_CONFIRM_WINDOW:
                continue
            if hit_frame < input_frame - MISSION_HIT_PRELABEL_WINDOW:
                continue
            if hit_frame > input_frame + MISSION_BUFFERED_NORMAL_HIT_WINDOW:
                continue
            candidates.append(hit)
        if not candidates:
            return None
        return min(candidates, key=lambda hit: (int(hit.get("frame", frame_idx)), int(hit.get("serial", 0))))

    def _drain_buffered_normal_steps(
        self,
        steps: list,
        progress_index: int,
        frame_idx: int,
    ) -> tuple[int, int]:
        """Advance all ready ordinary-normal steps in one update.

        Input and hit events are both persistent queues. Only the exact input
        and hit claimed by a completed step are consumed, so later buffered
        attacks remain available to the following steps.
        """
        progress = max(0, int(progress_index or 0))
        advanced = 0

        while progress < len(steps) and advanced < MISSION_BUFFERED_STEP_LIMIT:
            step = steps[progress]
            labels = self._step_labels(step)
            if not labels:
                break
            if (
                self._step_is_pass(step)
                or self._step_is_baroque_cancel(labels)
                or self._step_is_megacrash(labels)
                or self._step_allows_whiff_confirm(labels, step)
            ):
                break

            input_event = self._find_buffered_normal_input(step, labels, frame_idx)
            if input_event is None:
                break
            hit_event = self._find_hit_for_buffered_input(input_event, frame_idx)
            if hit_event is None:
                break

            hit_event["consumed"] = True
            input_serial = int(input_event.get("serial", 0) or 0)
            self._consume_mission_input_through(input_serial)

            completed_grace = self._step_grace(step)
            progress += 1
            advanced += 1
            self._runtime.update({
                "progress_index": progress,
                "pending_step_index": None,
                "pending_labels": [],
                "pending_anim": None,
                "pending_started_frame": -9999,
                "pending_input_serial": 0,
                "pending_label_confirmed": False,
                "pending_arm_source": "",
                "reset_grace_frames": completed_grace,
                "reset_grace_labels": [],
                "reset_grace_step_index": progress if completed_grace > 0 else None,
                "reset_grace_keeps_alive_only": self._step_grace_keeps_alive_only(step),
                "mission_step_start_serial": input_serial,
                "shell_install_hold": 0,
                "mission_last_hit_evidence_frame": int(frame_idx),
            })

        self._runtime["mission_buffered_advances"] = advanced
        return progress, advanced

    def _clear_trial_detection_buffers(self) -> None:
        self._runtime.update({
            "mission_hit_events": [],
            "mission_input_events": [],
            "mission_action_events": [],
            "mission_input_serial": 0,
            "mission_action_serial": 0,
            "mission_action_consumed_serial": 0,
            "mission_last_action_id": None,
            "mission_last_action_sample_seq": -1,
            "mission_input_consumed_serial": 0,
            "mission_input_match_serial": 0,
            "pending_input_serial": 0,
            "pending_label_confirmed": False,
            "pending_arm_source": "",
            "mission_step_start_serial": int(self._runtime.get("mission_input_serial", 0) or 0),
            "mission_last_input_token": "",
            "mission_last_input_frame": -9999,
            "mission_last_input_sample_seq": -1,
            "mission_buffered_advances": 0,
            "predicted_progress_index": 0,
            "prediction_entries": [],
        })


    # ------------------------------------------------------------------
    # Input-aware mission safeguards
    # ------------------------------------------------------------------

    def _read_mission_input_packet(self, slot_label: str, snap: dict) -> dict:
        try:
            return input_monitor.read_overlay_input_packet(
                slot_label,
                int((snap or {}).get("base") or 0),
            )
        except Exception:
            return {}

    def _mission_button_letters(self, bits: int) -> str:
        word = int(bits) & 0xFFFF
        labels = []
        if word & MISSION_INPUT_A:
            labels.append("A")
        if word & MISSION_INPUT_B:
            labels.append("B")
        if word & MISSION_INPUT_C:
            labels.append("C")
        if word & MISSION_INPUT_P:
            labels.append("P")
        if (word & MISSION_INPUT_TAUNT) == MISSION_INPUT_TAUNT:
            labels.append("T")
        return "".join(labels)

    def _record_mission_input(
        self,
        packet: dict,
        frame_idx: int,
        sample_seq: int = 0,
    ) -> dict:
        held = int((packet or {}).get("held") or 0) & 0xFFFF
        pressed = int((packet or {}).get("pressed") or 0) & 0xFFFF
        direction = held & MISSION_INPUT_DIRECTION_MASK
        held_buttons = held & MISSION_INPUT_ATTACK_MASK
        previous_direction = self._runtime.get("mission_prev_direction")
        previous_buttons = int(self._runtime.get("mission_prev_buttons", 0) or 0)
        raw_pressed_buttons = pressed & MISSION_INPUT_ATTACK_MASK
        transition_pressed_buttons = held_buttons & ~previous_buttons
        pressed_buttons = raw_pressed_buttons | transition_pressed_buttons
        events = self._runtime.setdefault("mission_input_events", [])

        tokens = []
        if direction != previous_direction:
            tokens.append(str({0: 5, 1: 6, 2: 4, 4: 8, 5: 9, 6: 7, 8: 2, 9: 3, 10: 1}.get(direction, 5)))
        if pressed_buttons:
            digit = str({0: 5, 1: 6, 2: 4, 4: 8, 5: 9, 6: 7, 8: 2, 9: 3, 10: 1}.get(direction, 5))
            tokens.append(digit + self._mission_button_letters(pressed_buttons))

        active_baroque_pairs = [
            (label, mask)
            for label, mask in MISSION_INPUT_BAROQUE_PAIRS
            if (held_buttons & mask) == mask
        ]
        previous_baroque_pairs = {
            label
            for label, mask in MISSION_INPUT_BAROQUE_PAIRS
            if (previous_buttons & mask) == mask
        }
        new_baroque_pairs = [
            (label, mask)
            for label, mask in active_baroque_pairs
            if label not in previous_baroque_pairs
        ]
        if new_baroque_pairs:
            self._runtime["mission_baroque_latch"] = MISSION_INPUT_BAROQUE_LATCH
            tokens.extend(label for label, _mask in new_baroque_pairs)
        else:
            self._runtime["mission_baroque_latch"] = max(
                0,
                int(self._runtime.get("mission_baroque_latch", 0) or 0) - 1,
            )

        for token in tokens:
            if (
                token == self._runtime.get("mission_last_input_token")
                and int(sample_seq or 0) > 0
                and int(sample_seq or 0)
                == int(self._runtime.get("mission_last_input_sample_seq", -1) or -1)
            ):
                continue
            if token.isdigit():
                event_pressed = 0
                event_buttons = ""
            elif token in {"AP", "BP", "CP"}:
                pair_mask = dict(MISSION_INPUT_BAROQUE_PAIRS).get(token, 0)
                event_pressed = pair_mask
                event_buttons = token
            else:
                event_pressed = pressed_buttons
                event_buttons = self._mission_button_letters(pressed_buttons)
            self._runtime["mission_input_serial"] = int(
                self._runtime.get("mission_input_serial", 0) or 0
            ) + 1
            events.append({
                "serial": int(self._runtime["mission_input_serial"]),
                "frame": int(frame_idx),
                "token": token,
                "direction": direction,
                "pressed": event_pressed,
                "buttons": event_buttons,
                "held_buttons": held_buttons,
                "sample_seq": int(sample_seq or 0),
                "sample_ns": int((packet or {}).get("sample_ns", 0) or 0),
                "action_id": int((packet or {}).get("action_id", 0) or 0) & 0x7FFF,
                "char_id": int((packet or {}).get("char_id", 0) or 0),
            })
            self._runtime["mission_last_input_token"] = token
            self._runtime["mission_last_input_frame"] = int(frame_idx)
            self._runtime["mission_last_input_sample_seq"] = int(sample_seq or 0)

        events[:] = [
            event for event in events
            if int(frame_idx) - int(event.get("frame", frame_idx)) <= MISSION_INPUT_EVENT_WINDOW
        ]
        self._runtime["mission_prev_direction"] = direction
        self._runtime["mission_prev_buttons"] = held_buttons

        return {
            "held": held,
            "pressed": pressed,
            "direction": direction,
            "pressed_buttons": pressed_buttons,
            "fresh_attack": bool(pressed_buttons),
            "baroque": int(self._runtime.get("mission_baroque_latch", 0) or 0) > 0,
        }

    def _action_label_candidates(self, action_id: int, char_id: int = 0) -> list[str]:
        action = int(action_id or 0) & 0x7FFF
        labels: list[str] = []
        generic = MISSION_GENERIC_ACTION_LABELS.get(action)
        if generic:
            labels.append(generic)
        try:
            mapped = self._move_label_for(
                action,
                int(char_id or 0),
                self._move_map,
                self._global_map,
            )
        except Exception:
            mapped = None
        if mapped and str(mapped).strip() and str(mapped).strip() not in labels:
            labels.append(str(mapped).strip())
        return labels

    def _record_mission_action_sample(
        self,
        packet: dict,
        frame_idx: int,
        sample_seq: int = 0,
    ) -> None:
        action_id = int((packet or {}).get("action_id", 0) or 0) & 0x7FFF
        char_id = int((packet or {}).get("char_id", 0) or 0)
        last_action = self._runtime.get("mission_last_action_id")
        last_seq = int(self._runtime.get("mission_last_action_sample_seq", -1) or -1)
        if action_id == int(last_action or 0) and int(sample_seq or 0) == last_seq:
            return
        self._runtime["mission_last_action_id"] = action_id
        self._runtime["mission_last_action_sample_seq"] = int(sample_seq or 0)
        if action_id < 0x100:
            return
        labels = self._action_label_candidates(action_id, char_id)
        if not labels:
            return
        events = self._runtime.setdefault("mission_action_events", [])
        previous = events[-1] if events else None
        if previous and int(previous.get("action_id", 0) or 0) == action_id:
            previous["last_sample_seq"] = int(sample_seq or 0)
            previous["last_frame"] = int(frame_idx)
            return
        serial = int(self._runtime.get("mission_action_serial", 0) or 0) + 1
        self._runtime["mission_action_serial"] = serial
        events.append({
            "serial": serial,
            "frame": int(frame_idx),
            "last_frame": int(frame_idx),
            "sample_seq": int(sample_seq or 0),
            "last_sample_seq": int(sample_seq or 0),
            "sample_ns": int((packet or {}).get("sample_ns", 0) or 0),
            "action_id": action_id,
            "char_id": char_id,
            "labels": labels,
            "input_serial": int(self._runtime.get("mission_input_serial", 0) or 0),
            "consumed": False,
        })
        del events[:-MISSION_ACTION_EVENT_LIMIT]

    def _record_opponent_realtime_stream(
        self,
        slot_label: str,
        snaps_dict: dict,
        frame_idx: int,
    ) -> tuple[list[int], bool, bool | None]:
        my_team = "P1" if str(slot_label).startswith("P1") else "P2"
        hp_cache = self._runtime.setdefault("mission_realtime_hp_by_slot", {})
        hitstun_cache = self._runtime.setdefault("mission_realtime_hitstun_by_slot", {})
        damage_values: list[int] = []
        hitstun_exit = False
        any_sampled = False
        any_hitstun = False

        for other_slot, other_snap in (snaps_dict or {}).items():
            if not isinstance(other_snap, dict) or other_snap.get("teamtag") == my_team:
                continue
            packets = self._input_packets_for_slot(
                str(other_slot), other_snap, consumer="mission-opponent"
            )
            for packet in packets:
                seq = int((packet or {}).get("seq", 0) or 0)
                hp = int((packet or {}).get("current_hp", 0) or 0)
                action = int((packet or {}).get("action_id", 0) or 0) & 0x7FFF
                if hp > 0:
                    previous_hp = hp_cache.get(str(other_slot))
                    if isinstance(previous_hp, int) and hp < previous_hp:
                        damage = previous_hp - hp
                        damage_values.append(damage)
                        serial = int(self._runtime.get("mission_hit_serial", 0) or 0) + 1
                        self._runtime["mission_hit_serial"] = serial
                        action_events = [
                            event for event in self._runtime.get("mission_action_events", [])
                            if not event.get("consumed")
                            and int(event.get("sample_seq", 0) or 0) <= seq
                        ]
                        owner_action_serial = (
                            int(action_events[-1].get("serial", 0) or 0)
                            if action_events else 0
                        )
                        self._runtime.setdefault("mission_hit_events", []).append({
                            "serial": serial,
                            "frame": int(frame_idx),
                            "last_frame": int(frame_idx),
                            "sample_seq": seq,
                            "sample_ns": int((packet or {}).get("sample_ns", 0) or 0),
                            "hp_seen": True,
                            "combo_seen": False,
                            "damage": int(damage),
                            "owner_action_serial": owner_action_serial,
                            "consumed": False,
                        })
                        del self._runtime["mission_hit_events"][:-MISSION_HIT_EVENT_LIMIT]
                        self._runtime["mission_last_hit_evidence_frame"] = int(frame_idx)
                    hp_cache[str(other_slot)] = hp

                in_hitstun = action in MISSION_COMBO_KEEPALIVE_STATES
                previous_hitstun = hitstun_cache.get(str(other_slot))
                if previous_hitstun is True and not in_hitstun:
                    hitstun_exit = True
                hitstun_cache[str(other_slot)] = in_hitstun
                any_sampled = True
                any_hitstun = any_hitstun or in_hitstun

        return damage_values, hitstun_exit, (any_hitstun if any_sampled else None)

    def _record_mission_input_stream(
        self,
        slot_label: str,
        snap: dict,
        frame_idx: int,
    ) -> dict:
        """Consume every unseen 240 Hz input sample before route evaluation."""
        packets = self._input_packets_for_slot(
            slot_label, snap, consumer="mission"
        )
        aggregate = {
            "held": 0,
            "pressed": 0,
            "direction": 0,
            "pressed_buttons": 0,
            "fresh_attack": False,
            "baroque": False,
        }
        for packet in packets:
            sample_seq = int((packet or {}).get("seq", 0) or 0)
            result = self._record_mission_input(
                packet,
                frame_idx,
                sample_seq=sample_seq,
            )
            self._record_mission_action_sample(packet, frame_idx, sample_seq)
            aggregate["held"] = int(result.get("held", 0) or 0)
            aggregate["direction"] = int(result.get("direction", 0) or 0)
            aggregate["pressed"] |= int(result.get("pressed", 0) or 0)
            aggregate["pressed_buttons"] |= int(result.get("pressed_buttons", 0) or 0)
            aggregate["fresh_attack"] = bool(
                aggregate["fresh_attack"] or result.get("fresh_attack", False)
            )
            aggregate["baroque"] = bool(
                aggregate["baroque"] or result.get("baroque", False)
            )
        return aggregate

    def _step_input_notation(self, step: Any) -> str:
        if not isinstance(step, dict):
            return ""
        return str(
            step.get("input")
            or step.get("command")
            or step.get("notation")
            or ""
        ).strip()

    def _recent_mission_events(self, frame_idx: int, window: int) -> list[dict]:
        consumed_serial = max(
            int(self._runtime.get("mission_input_consumed_serial", 0) or 0),
            int(self._runtime.get("mission_step_start_serial", 0) or 0),
        )
        return [
            event for event in self._runtime.get("mission_input_events", [])
            if int(event.get("serial", 0) or 0) > consumed_serial
            and int(frame_idx) - int(event.get("frame", frame_idx)) <= int(window)
        ]

    def _mark_mission_input_match(self, event_or_serial) -> None:
        if isinstance(event_or_serial, dict):
            serial = int(event_or_serial.get("serial", 0) or 0)
        else:
            serial = int(event_or_serial or 0)
        if serial > 0:
            self._runtime["mission_input_match_serial"] = serial

    def _consume_mission_input_through(self, serial: int) -> None:
        serial = int(serial or 0)
        if serial <= 0:
            return
        current = int(
            self._runtime.get("mission_input_consumed_serial", 0) or 0
        )
        consumed = max(current, serial)
        self._runtime["mission_input_consumed_serial"] = consumed
        self._runtime["mission_input_match_serial"] = 0
        self._runtime["pending_input_serial"] = 0
        self._runtime["mission_input_events"] = [
            event
            for event in self._runtime.get("mission_input_events", [])
            if int(event.get("serial", 0) or 0) > consumed
        ]
        # Label-based and special-case confirmations also retire the native
        # action that owned the consumed input. Otherwise that stale action can
        # sit in front of the prediction queue and block every later step.
        for action in self._runtime.get("mission_action_events", []):
            action_input = int(action.get("input_serial", 0) or 0)
            if action_input > 0 and action_input <= consumed:
                action["consumed"] = True
                self._runtime["mission_action_consumed_serial"] = max(
                    int(self._runtime.get("mission_action_consumed_serial", 0) or 0),
                    int(action.get("serial", 0) or 0),
                )

    def _normal_input_matches(self, notation: str, frame_idx: int) -> bool:
        compact = str(notation or "").replace(" ", "").upper()
        match = __import__("re").fullmatch(r"([1-9])([ABC])", compact)
        if not match:
            return False
        wanted_direction, wanted_button = match.groups()
        mirrored = _MISSION_DIRECTION_MIRROR.get(wanted_direction, wanted_direction)
        for event in reversed(self._recent_mission_events(frame_idx, MISSION_INPUT_NORMAL_LATCH)):
            token = str(event.get("token") or "").upper()
            if token in {wanted_direction + wanted_button, mirrored + wanted_button}:
                self._mark_mission_input_match(event)
                return True
        return False

    def _mission_event_direction_digit(self, event: dict) -> str:
        token = str((event or {}).get("token") or "").strip().upper()
        if token and token[0] in "123456789":
            return token[0]
        direction = int((event or {}).get("direction") or 0) & MISSION_INPUT_DIRECTION_MASK
        return str({0: 5, 1: 6, 2: 4, 4: 8, 5: 9, 6: 7, 8: 2, 9: 3, 10: 1}.get(direction, 5))

    def _neutral_button_input_matches(self, button: str, frame_idx: int) -> bool:
        wanted = str(button or "").upper()
        wanted_mask = MISSION_INPUT_BUTTON_MASKS.get(wanted, 0)
        if not wanted_mask:
            return False
        for event in reversed(self._recent_mission_events(frame_idx, MISSION_INPUT_NORMAL_LATCH)):
            if self._mission_event_direction_digit(event) != "5":
                continue
            pressed = int(event.get("pressed") or 0)
            held = int(event.get("held_buttons") or pressed)
            if pressed & wanted_mask and (held & wanted_mask) == wanted_mask:
                self._mark_mission_input_match(event)
                return True
        return False

    def _button_chord_input_matches(
        self,
        buttons: str,
        frame_idx: int,
        *,
        neutral_only: bool = False,
    ) -> bool:
        required = 0
        for button in str(buttons or "").upper():
            required |= MISSION_INPUT_BUTTON_MASKS.get(button, 0)
        if not required:
            return False
        for event in reversed(self._recent_mission_events(frame_idx, MISSION_INPUT_NORMAL_LATCH)):
            if neutral_only and self._mission_event_direction_digit(event) != "5":
                continue
            pressed = int(event.get("pressed") or 0)
            held = int(event.get("held_buttons") or pressed)
            if pressed & required and (held & required) == required:
                self._mark_mission_input_match(event)
                return True
        return False

    def _double_attack_input_matches(self, frame_idx: int, *, neutral_only: bool = False) -> bool:
        for event in reversed(self._recent_mission_events(frame_idx, MISSION_INPUT_NORMAL_LATCH)):
            if neutral_only and self._mission_event_direction_digit(event) != "5":
                continue
            pressed = int(event.get("pressed") or 0) & MISSION_INPUT_ABC_MASK
            held = int(event.get("held_buttons") or pressed) & MISSION_INPUT_ABC_MASK
            if pressed and int(held).bit_count() >= 2:
                self._mark_mission_input_match(event)
                return True
        return False

    def _rotation_input_matches(
        self,
        frame_idx: int,
        *,
        button: str = "",
        double_attack: bool = False,
        any_attack: bool = False,
        motion_only: bool = False,
    ) -> bool:
        events = self._recent_mission_events(frame_idx, MISSION_INPUT_COMMAND_WINDOW)
        if not events:
            return False
        wanted_mask = MISSION_INPUT_BUTTON_MASKS.get(str(button or "").upper(), 0)
        for final_index in range(len(events) - 1, -1, -1):
            final_event = events[final_index]
            pressed = int(final_event.get("pressed") or 0)
            held = int(final_event.get("held_buttons") or pressed)
            abc_pressed = pressed & MISSION_INPUT_ABC_MASK
            abc_held = held & MISSION_INPUT_ABC_MASK
            if motion_only:
                pass
            elif double_attack:
                if not abc_pressed or int(abc_held).bit_count() < 2:
                    continue
            elif wanted_mask:
                if not (pressed & wanted_mask):
                    continue
            elif any_attack:
                if not abc_pressed:
                    continue
            else:
                continue

            directions: list[str] = []
            for event in events[: final_index + 1]:
                digit = self._mission_event_direction_digit(event)
                if digit == "5":
                    continue
                cardinal = {
                    "1": "2", "2": "2", "3": "6", "6": "6",
                    "9": "8", "8": "8", "7": "4", "4": "4",
                }.get(digit)
                if cardinal and (not directions or directions[-1] != cardinal):
                    directions.append(cardinal)
            tail = directions[-8:]
            clockwise = ["6", "2", "4", "8"]
            counter = ["6", "8", "4", "2"]
            doubled = tail + tail
            if all(cardinal in tail for cardinal in clockwise):
                for sequence in (clockwise, counter):
                    for start in range(max(1, len(doubled) - len(sequence) + 1)):
                        if doubled[start:start + len(sequence)] == sequence:
                            self._mark_mission_input_match(final_event)
                            return True
        return False

    def _motion_input_matches(
        self,
        motion: str,
        frame_idx: int,
        *,
        button: str = "",
        double_attack: bool = False,
        any_attack: bool = False,
        required_attack_mask: int = 0,
        motion_only: bool = False,
    ) -> bool:
        motion = "".join(ch for ch in str(motion or "") if ch in "123456789")
        if not motion:
            return False

        mirrored_motion = "".join(_MISSION_DIRECTION_MIRROR.get(ch, ch) for ch in motion)
        wanted_button_mask = MISSION_INPUT_BUTTON_MASKS.get(
            str(button or "").upper(),
            0,
        )

        events = self._recent_mission_events(frame_idx, MISSION_INPUT_COMMAND_WINDOW)
        for final_index in range(len(events) - 1, -1, -1):
            final_event = events[final_index]
            pressed = int(final_event.get("pressed") or 0)
            held_buttons = int(final_event.get("held_buttons") or pressed)
            abc_pressed = pressed & MISSION_INPUT_ABC_MASK
            abc_held = held_buttons & MISSION_INPUT_ABC_MASK

            if motion_only:
                pass
            elif required_attack_mask:
                if not abc_pressed or (abc_held & required_attack_mask) != required_attack_mask:
                    continue
            elif double_attack:
                if not abc_pressed or int(abc_held).bit_count() < 2:
                    continue
            elif wanted_button_mask:
                if not (abc_pressed & wanted_button_mask):
                    continue
            elif any_attack:
                if not abc_pressed:
                    continue
            else:
                continue

            directions: list[str] = []
            for event in events[: final_index + 1]:
                digit = self._mission_event_direction_digit(event)
                if digit == "5":
                    continue
                if not directions or directions[-1] != digit:
                    directions.append(digit)

            for wanted in {motion, mirrored_motion}:
                wanted_digits = list(wanted)
                if len(directions) >= len(wanted_digits) and directions[-len(wanted_digits):] == wanted_digits:
                    self._mark_mission_input_match(final_event)
                    return True

        return False

    def _mash_input_matches(self, button: str, frame_idx: int) -> bool:
        wanted = str(button or "X").upper()
        wanted_mask = {
            "A": MISSION_INPUT_A,
            "B": MISSION_INPUT_B,
            "C": MISSION_INPUT_C,
        }.get(wanted, MISSION_INPUT_ABC_MASK)

        count = 0
        for event in self._recent_mission_events(frame_idx, MISSION_INPUT_LEGS_WINDOW):
            pressed = int(event.get("pressed") or 0) & MISSION_INPUT_ABC_MASK
            if pressed & wanted_mask:
                count += 1
                if count >= MISSION_INPUT_MASH_COUNT:
                    self._mark_mission_input_match(event)
                    return True
        return False

    def _button_sequence_input_matches(self, sequence: str, frame_idx: int) -> bool:
        wanted = [letter for letter in str(sequence or "").upper() if letter in "ABC"]
        if not wanted:
            return False

        observed: list[tuple[str, int]] = []
        for event in self._recent_mission_events(frame_idx, MISSION_INPUT_COMMAND_WINDOW):
            pressed = int(event.get("pressed") or 0) & MISSION_INPUT_ABC_MASK
            serial = int(event.get("serial", 0) or 0)
            for letter, mask in (
                ("A", MISSION_INPUT_A),
                ("B", MISSION_INPUT_B),
                ("C", MISSION_INPUT_C),
            ):
                if pressed & mask:
                    observed.append((letter, serial))

        if len(observed) < len(wanted):
            return False
        tail = observed[-len(wanted):]
        if [letter for letter, _serial in tail] != wanted:
            return False
        self._mark_mission_input_match(tail[-1][1])
        return True

    def _command_input_matches(self, notation: str, frame_idx: int) -> bool:
        raw = str(notation or "").strip().upper()
        if not raw:
            return False

        alternatives = re.split(r"\s+/\s+", raw)
        if len(alternatives) > 1:
            return any(
                self._command_input_matches(alternative, frame_idx)
                for alternative in alternatives
                if alternative.strip()
            )

        if raw in {"TAUNT", "TAUNT(T)", "T"}:
            return self._button_chord_input_matches("T", frame_idx)

        compact = raw.replace("AIR", "").replace("J.", "")
        compact = compact.replace("[", "").replace("]", "")
        compact = compact.replace("CHARGE", "").replace("HOLD", "")
        compact = compact.replace(" ", "")
        compact = compact.replace("HCB", "63214").replace("HCF", "41236")
        compact = compact.replace("QCF", "236").replace("QCB", "214")
        compact = compact.replace("RDP", "421").replace("DP", "623")

        if "RELEASE" in compact:
            return False

        if compact == "ABCP":
            return self._button_chord_input_matches("ABCP", frame_idx)
        if compact == "XX":
            return self._double_attack_input_matches(frame_idx, neutral_only=True)
        if compact == "X":
            return any(
                self._neutral_button_input_matches(button, frame_idx)
                for button in "ABC"
            )
        if re.fullmatch(r"[ABCLMHP]", compact):
            return self._neutral_button_input_matches(compact, frame_idx)

        rotation_match = re.fullmatch(r"360(?:(XX)|([ABCLMH])|(X))?", compact)
        if rotation_match:
            double_token, button_token, any_token = rotation_match.groups()
            return self._rotation_input_matches(
                frame_idx,
                button=button_token or "",
                double_attack=bool(double_token),
                any_attack=bool(any_token),
                motion_only=not any((double_token, button_token, any_token)),
            )

        mash_match = re.fullmatch(r"MASH([ABCLMHX])", compact)
        if mash_match:
            button = mash_match.group(1)
            button = {"L": "A", "M": "B", "H": "C"}.get(button, button)
            return self._mash_input_matches(button, frame_idx)

        sequence_match = re.fullmatch(r"[ABC]{3,}", compact)
        if sequence_match:
            return self._button_sequence_input_matches(compact, frame_idx)

        pair_match = re.fullmatch(r"([1-9]+)([ABCLMH])\+([ABCLMH])", compact)
        if pair_match:
            motion, first_button, second_button = pair_match.groups()
            required_mask = (
                MISSION_INPUT_BUTTON_MASKS[first_button]
                | MISSION_INPUT_BUTTON_MASKS[second_button]
            )
            return self._motion_input_matches(
                motion,
                frame_idx,
                required_attack_mask=required_mask,
            )

        generic_list_match = re.fullmatch(r"([1-9]+)([ABCLMH](?:/[ABCLMH])+)", compact)
        if generic_list_match:
            motion, buttons = generic_list_match.groups()
            return any(
                self._motion_input_matches(motion, frame_idx, button=button)
                for button in buttons.split("/")
            )

        exact_match = re.fullmatch(r"([1-9]+)([ABCLMH])", compact)
        if exact_match:
            motion, button = exact_match.groups()
            return self._motion_input_matches(motion, frame_idx, button=button)

        double_match = re.fullmatch(r"([1-9]+)XX", compact)
        if double_match:
            return self._motion_input_matches(
                double_match.group(1),
                frame_idx,
                double_attack=True,
            )

        generic_match = re.fullmatch(r"([1-9]+)(?:A/B/C|L/M/H|X)", compact)
        if generic_match:
            return self._motion_input_matches(
                generic_match.group(1),
                frame_idx,
                any_attack=True,
            )

        motion_only_match = re.fullmatch(r"([1-9]+)", compact)
        if motion_only_match:
            return self._motion_input_matches(
                motion_only_match.group(1),
                frame_idx,
                motion_only=True,
            )

        return False

    def _chun_legs_input_matches(self, expected_labels: list[str], frame_idx: int) -> bool:
        labels = [str(label or "").strip() for label in expected_labels]
        labels_norm = [label.lower() for label in labels]
        if not any("legs" in label for label in labels_norm):
            return False
        wanted_button = ""
        for label in reversed(labels):
            match = __import__("re").search(r"(?:^|\s)([ABC])$", label, flags=__import__("re").IGNORECASE)
            if match:
                wanted_button = match.group(1).upper()
                break
        counts = {"A": 0, "B": 0, "C": 0}
        for event in self._recent_mission_events(frame_idx, MISSION_INPUT_LEGS_WINDOW):
            pressed = int(event.get("pressed") or 0) & MISSION_INPUT_ABC_MASK
            serial = int(event.get("serial", 0) or 0)
            if pressed & MISSION_INPUT_A:
                counts["A"] += 1
                if (wanted_button in {"", "A"}) and counts["A"] >= MISSION_INPUT_MASH_COUNT:
                    self._mark_mission_input_match(serial)
                    return True
            if pressed & MISSION_INPUT_B:
                counts["B"] += 1
                if (wanted_button in {"", "B"}) and counts["B"] >= MISSION_INPUT_MASH_COUNT:
                    self._mark_mission_input_match(serial)
                    return True
            if pressed & MISSION_INPUT_C:
                counts["C"] += 1
                if (wanted_button in {"", "C"}) and counts["C"] >= MISSION_INPUT_MASH_COUNT:
                    self._mark_mission_input_match(serial)
                    return True
        return False

    def _step_is_megacrash(self, expected_labels: list[str]) -> bool:
        return any(
            self._canonical_mission_label(label) == "megacrash"
            for label in expected_labels
        )

    def _megacrash_step_matches(
        self,
        expected_labels: list[str],
        opponent_in_megacrash: bool,
        previous_opponent_megacrash: bool,
    ) -> bool:
        """Use the opponent's dedicated Megacrash state edge for this step."""
        return bool(
            self._step_is_megacrash(expected_labels)
            and opponent_in_megacrash
            and not previous_opponent_megacrash
        )

    def _step_input_matches(
        self,
        character_name: str,
        expected_step: Any,
        expected_labels: list[str],
        frame_idx: int,
    ) -> bool:
        self._runtime["mission_input_match_serial"] = 0
        labels_norm = {str(label or "").strip().lower() for label in expected_labels}
        notation = self._step_input_notation(expected_step)
        compact_notation = notation.replace(" ", "").upper()
        if compact_notation in {"ATK+P", "ATKP"}:
            for event in reversed(
                self._recent_mission_events(
                    frame_idx,
                    MISSION_INPUT_BAROQUE_LATCH,
                )
            ):
                held_buttons = int(event.get("held_buttons") or 0)
                pressed_buttons = int(event.get("pressed") or 0)
                has_attack = bool(held_buttons & MISSION_INPUT_ABC_MASK)
                has_partner = bool(held_buttons & MISSION_INPUT_P)
                fresh_pair_edge = bool(
                    pressed_buttons
                    & (MISSION_INPUT_ABC_MASK | MISSION_INPUT_P)
                )
                if has_attack and has_partner and fresh_pair_edge:
                    self._mark_mission_input_match(event)
                    return True
            return False

        if (
            "baroque cancel" in labels_norm
            or compact_notation in {"AP", "A+P", "BP", "B+P", "CP", "C+P", "A+P/B+P/C+P"}
        ):
            for event in reversed(
                self._recent_mission_events(frame_idx, MISSION_INPUT_BAROQUE_LATCH)
            ):
                if str(event.get("token") or "").upper() in {"AP", "BP", "CP"}:
                    self._mark_mission_input_match(event)
                    return True
            return False
        if self._command_input_matches(notation, frame_idx):
            return True
        if self._normal_input_matches(notation, frame_idx):
            return True
        for label in expected_labels:
            if self._command_input_matches(label, frame_idx):
                return True
            if self._normal_input_matches(label, frame_idx):
                return True
        if str(character_name or "").strip().lower().replace("-", " ") in {"chun li", "chunli"}:
            return self._chun_legs_input_matches(expected_labels, frame_idx)
        return False

    # ------------------------------------------------------------------
    # Step predicate helpers
    # ------------------------------------------------------------------

    def _canonical_mission_label(self, label: str) -> str:
        value = str(label or "").strip().lower()
        value = value.replace("j.", "air ")
        value = value.replace("jumping ", "air ")
        value = value.replace("jump ", "air ")
        value = __import__("re").sub(r"[^a-z0-9]+", " ", value)
        return " ".join(value.split())

    def _mission_label_matches(self, current_label: str, expected_labels: list[str]) -> bool:
        current = self._canonical_mission_label(current_label)
        if not current:
            return False
        for expected in expected_labels or []:
            wanted = self._canonical_mission_label(expected)
            if not wanted:
                continue
            if current == wanted:
                return True
            if {current, wanted} <= {"air a", "ja"}:
                return True
            if {current, wanted} <= {"air b", "jb"}:
                return True
            if {current, wanted} <= {"air c", "jc"}:
                return True
        return False

    def _mission_snapshot_label_matches(self, snap: dict, expected_labels: list[str]) -> bool:
        """Match the base move label plus projectile-proven level aliases.

        Charged moves often share one action ID.  The projectile level detector
        adds aliases such as ``Hyper Zero Blaster Lv2`` only after the exact
        projectile variant spawns, so missions can distinguish levels without
        breaking older steps that still expect the unqualified move name.
        """
        candidates = [
            str((snap or {}).get("mv_label") or "").strip(),
            str((snap or {}).get("mv_label_display") or "").strip(),
        ]
        candidates.extend(str(x or "").strip() for x in ((snap or {}).get("mv_label_aliases") or []))
        seen = set()
        for candidate in candidates:
            canon = self._canonical_mission_label(candidate)
            if not candidate or canon in seen:
                continue
            seen.add(canon)
            if self._mission_label_matches(candidate, expected_labels):
                return True

        try:
            detected_level = int((snap or {}).get("move_level") or 0)
        except Exception:
            detected_level = 0
        if detected_level <= 0:
            return False

        import re
        level_re = re.compile(r"(?:level|lvl|lv|l)\s*[-_:]?\s*([1-9][0-9]*)", re.I)
        strip_re = re.compile(r"\b(?:level|lvl|lv|l)\s*[-_:]?\s*[1-9][0-9]*\b", re.I)
        candidate_bases = []
        for candidate in candidates:
            stripped = strip_re.sub("", candidate)
            canon = self._canonical_mission_label(stripped)
            if canon:
                candidate_bases.append(canon)
        for expected in expected_labels or []:
            match = level_re.search(str(expected or ""))
            if not match or int(match.group(1)) != detected_level:
                continue
            expected_base = self._canonical_mission_label(strip_re.sub("", str(expected or "")))
            if not expected_base:
                return True
            for candidate_base in candidate_bases:
                if candidate_base == expected_base:
                    return True
                # Permit a concise mission label such as "Hyper Blaster Lv2"
                # to match the game's longer "Hyper Zero Blaster" label.
                expected_tokens = set(expected_base.split())
                candidate_tokens = set(candidate_base.split())
                if expected_tokens and expected_tokens <= candidate_tokens:
                    return True
        return False

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
        step_text = " ".join(
            [str(value or "") for value in expected_labels]
            + ([
                str(step.get("display") or ""),
                str(step.get("input") or step.get("command") or step.get("notation") or ""),
            ] if isinstance(step, dict) else [])
        ).lower()
        jump_cancel = "jump cancel" in step_text or "7 / 8 / 9" in step_text
        if matched or explicit or jump_cancel:
            print(
                f"[mission whiff confirm] labels={sorted(matched)!r} "
                f"explicit={explicit}"
            )
        return explicit or bool(matched) or jump_cancel

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
            raw = step.get("labels")
            if isinstance(raw, list):
                labels = [str(x).strip() for x in raw if str(x).strip()]
                if labels:
                    return labels
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
            cp["confirmed_step_count"] = final_count
            cp["predicted_step_count"] = 0
            cp["prediction_active"] = False
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
        mission_input = self._record_mission_input_stream(slot, snap, frame_idx)
        realtime_damage_values, realtime_hitstun_exit, realtime_hitstun_now = (
            self._record_opponent_realtime_stream(slot, snaps_dict, frame_idx)
        )
        current_inputs = {
            "A": 1 if mission_input.get("pressed_buttons", 0) & MISSION_INPUT_A else 0,
            "B": 1 if mission_input.get("pressed_buttons", 0) & MISSION_INPUT_B else 0,
            "C": 1 if mission_input.get("pressed_buttons", 0) & MISSION_INPUT_C else 0,
            "P": 1 if mission_input.get("pressed_buttons", 0) & MISSION_INPUT_P else 0,
            "T": 1 if mission_input.get("pressed_buttons", 0) & MISSION_INPUT_TAUNT else 0,
        }
        opponent_in_hitstun = self._opponent_in_hitstun(slot, snaps_dict)
        opponent_in_megacrash = self._opponent_in_megacrash(slot, snaps_dict)
        previous_opponent_megacrash = bool(
            self._runtime.get("last_opponent_megacrash", False)
        )
        global_combo_count = self._global_combo_count()
        global_combo_active = bool(global_combo_count is not None and global_combo_count > 0)
        self._runtime["global_combo_count"] = int(global_combo_count or 0)
        fallback_damage_values = self._opponent_damage_this_frame(slot, snaps_dict)
        damage_values = list(realtime_damage_values or fallback_damage_values)
        opponent_took_damage = bool(damage_values)
        frame_damage = sum(int(x) for x in damage_values)
        new_hit_evidence = self._record_mission_hit_events(
            global_combo_count,
            [] if realtime_damage_values else damage_values,
            frame_idx,
        )

        baroque_pool_adjusted = bool(
            snap.get("baroque_cancel_raw")
            or snap.get("baroque_cancel_latched")
            or mission_input.get("baroque")
        )

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
        # Primary reaction state is the route authority. The global combo
        # counter remains available only for hit-count evidence.
        self._runtime["hitstun_grace"] = 0

        # Shell release grace
        shell_release_grace = int(self._runtime.get("shell_release_grace", 0) or 0)
        if shell_release_grace > 0:
            self._runtime["shell_release_grace"] = shell_release_grace - 1

        reset_grace_active_now = int(self._runtime.get("reset_grace_frames", 0)) > 0

        expected_step_for_reset = steps[progress_index] if progress_index < len(steps) else None
        expected_labels_for_reset = self._step_labels(expected_step_for_reset)
        dedicated_megacrash_reset_edge = self._megacrash_step_matches(
            expected_labels_for_reset,
            opponent_in_megacrash,
            previous_opponent_megacrash,
        )
        opponent_real_combo_state = _mission_route_combo_live(
            global_combo_active,
            opponent_in_hitstun,
            dedicated_megacrash_reset_edge,
        )
        actual_hitstun_now = bool(
            realtime_hitstun_now
            if realtime_hitstun_now is not None
            else (opponent_in_hitstun or opponent_in_megacrash)
        )
        previous_actual_hitstun = bool(
            self._runtime.get("last_actual_hitstun", False)
        )
        hitstun_exit_edge = bool(
            realtime_hitstun_exit
            or (previous_actual_hitstun and not actual_hitstun_now)
        )
        self._runtime["last_actual_hitstun"] = actual_hitstun_now

        # No inferred latches are allowed to keep a route alive after the real
        # reaction flag drops. Only an explicit mission-step grace value can
        # extend the route beyond the true hitstun exit edge.
        opponent_in_combo_state = opponent_real_combo_state

        explicit_reset_grace = bool(
            int(self._runtime.get("reset_grace_frames", 0) or 0) > 0
            and self._runtime.get("reset_grace_step_index") == progress_index
        )
        if (
            steps
            and progress_index > 0
            and hitstun_exit_edge
            and not explicit_reset_grace
        ):
            print(
                f"[mission hitstun ended] slot={slot} mission_id={mission_id} "
                f"step={progress_index}; resetting on exact reaction edge"
            )
            progress_index = 0
            self._runtime.update({
                "progress_index": 0,
                "pending_step_index": None,
                "pending_labels": [],
                "pending_anim": None,
                "pending_started_frame": -9999,
                "pending_label_confirmed": False,
                "pending_arm_source": "",
                "reset_grace_frames": 0,
                "reset_grace_labels": [],
                "reset_grace_step_index": None,
                "reset_grace_keeps_alive_only": False,
                "shell_installed": False,
                "shell_release_grace": 0,
                "last_seen_label": "",
                "last_seen_anim": None,
                "last_seen_hitstun": False,
                "last_opponent_megacrash": False,
                "last_inputs": {},
            })
            self._clear_trial_detection_buffers()
            payload.update({
                "just_cleared": False,
                "clear_seq": int(self._runtime.get("clear_seq", 0)),
                "celebrate_pending": bool(self._runtime.get("celebrate_pending", False)),
                "celebrate_token": int(self._runtime.get("celebrate_token", 0) or 0),
                "completed_step_count": 0,
                "current_step_index": 0,
                "current_step_label": (
                    " / ".join(self._step_labels(steps[0])) if steps else None
                ),
            })
            return payload

        if (
            self._runtime.get("shell_installed")
            and self._saki_shell_release_label(current_label)
            and opponent_in_combo_state
        ):
            self._runtime["shell_release_grace"] = 20

        # Goal-type missions have no ordered-step grace. The exact frame the
        # defender leaves real hitstun ends the attempt immediately.
        if mission_goal and hitstun_exit_edge:
            self._runtime.update({
                "goal_combo_damage": 0,
                "goal_combo_hits": 0,
                "goal_hp_hit_count": 0,
                "goal_hit_latch": 0,
                "goal_failed": False,
                "goal_last_damage_frame": -1,
            })

        # Goal-type missions
        if mission_goal:
            goal_type = str(mission_goal.get("type", "")).strip().lower()

            if goal_type in {"damage_under_hits", "combo_damage"}:
                hit_latch = max(0, int(self._runtime.get("goal_hit_latch", 0) or 0) - 1)
                if frame_damage > 0:
                    # HP may update before the reaction action ID. Damage is
                    # authoritative and must start the combo goal immediately.
                    self._runtime["goal_combo_damage"] = (
                        int(self._runtime.get("goal_combo_damage", 0)) + frame_damage
                    )
                    hp_hit_count = max(1, len(damage_values))
                    self._runtime["goal_hp_hit_count"] = (
                        int(self._runtime.get("goal_hp_hit_count", 0)) + hp_hit_count
                    )
                    self._runtime["goal_last_damage_frame"] = frame_idx
                    hit_latch = 2

                counter_hits = max(0, int(global_combo_count or 0))
                self._runtime["goal_combo_hits"] = max(
                    int(self._runtime.get("goal_combo_hits", 0)),
                    int(self._runtime.get("goal_hp_hit_count", 0)),
                    counter_hits,
                )
                self._runtime["goal_hit_latch"] = hit_latch

            goal_combo_live = bool(
                opponent_in_combo_state
                or global_combo_active
                or frame_damage > 0
                or new_hit_evidence > 0
                or int(self._runtime.get("goal_hit_latch", 0) or 0) > 0
            )

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
                if not goal_combo_live:
                    self._runtime.update({
                        "goal_combo_damage": 0, "goal_combo_hits": 0,
                        "goal_hp_hit_count": 0, "goal_hit_latch": 0,
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
                if not goal_combo_live:
                    self._runtime.update({
                        "goal_combo_damage": 0, "goal_combo_hits": 0,
                        "goal_hp_hit_count": 0, "goal_hit_latch": 0,
                        "goal_failed": False, "goal_last_damage_frame": -1,
                    })
                return payload

        # Drain every already-confirmed ordinary normal before applying route
        # reset logic. This removes the one-step-per-update bottleneck and keeps
        # later buffered inputs available for their own mission steps.
        progress_index, action_advances = self._drain_buffered_action_steps(
            steps, progress_index, frame_idx
        )
        progress_index, normal_advances = self._drain_buffered_normal_steps(
            steps, progress_index, frame_idx
        )
        buffered_advances = action_advances + normal_advances
        self._runtime["mission_buffered_advances"] = buffered_advances
        self._runtime["progress_index"] = progress_index

        if progress_index >= len(steps):
            final_idx = max(0, len(steps) - 1)
            final_label = (
                (" / ".join(steps[final_idx]) if isinstance(steps[final_idx], list) else steps[final_idx])
                if steps else None
            )
            return _clear_payload(len(steps), final_idx, final_label)

        # Step-list missions. Route liveness is strict: once both the global
        # combo counter and actual hitstun end, the route fails immediately
        # unless the completed step supplied an explicit grace frame count.
        grace_left = int(self._runtime.get("reset_grace_frames", 0) or 0)
        grace_step_index = self._runtime.get("reset_grace_step_index")
        dropped_with_matching_grace = bool(
            not actual_hitstun_now
            and grace_left > 0
            and grace_step_index == progress_index
        )
        if hitstun_exit_edge or dropped_with_matching_grace:
            reset_now, grace_left = _mission_combo_reset_tick(
                progress_index,
                False,
                grace_left,
                grace_step_index,
            )
        else:
            reset_now = False
        self._runtime["reset_grace_frames"] = grace_left

        if reset_now:
            print(
                f"[mission combo dropped] slot={slot} mission_id={mission_id} "
                f"step={progress_index} reaction={int(opponent_in_hitstun)} "
                f"combo_counter={int(global_combo_count or 0)}; resetting immediately"
            )
            progress_index = 0
            self._runtime.update({
                "progress_index": 0,
                "pending_step_index": None,
                "pending_labels": [],
                "pending_anim": None,
                "pending_started_frame": -9999,
                "pending_label_confirmed": False,
                "pending_arm_source": "",
                "reset_grace_frames": 0,
                "reset_grace_labels": [],
                "reset_grace_step_index": None,
                "reset_grace_keeps_alive_only": False,
                "shell_installed": False,
                "shell_release_grace": 0,
                "last_seen_label": "",
                "last_seen_anim": None,
                "last_seen_hitstun": False,
                "last_actual_hitstun": False,
                "last_opponent_megacrash": False,
                "last_inputs": {},
            })
            self._clear_trial_detection_buffers()

        last_seen_label = self._runtime.get("last_seen_label", "")
        last_seen_anim = self._runtime.get("last_seen_anim")
        last_seen_hitstun = bool(self._runtime.get("last_seen_hitstun", False))
        last_inputs = self._runtime.get("last_inputs") or {}

        has_fresh_attack_input = self._has_fresh_attack_input(current_inputs, last_inputs)

        current_move_level = int(snap.get("move_level") or 0)
        last_seen_move_level = int(self._runtime.get("last_seen_move_level", 0) or 0)
        is_fresh_instance = (
            current_anim != last_seen_anim
            or current_label != last_seen_label
            or current_move_level != last_seen_move_level
            or (opponent_in_combo_state and not last_seen_hitstun)
            or has_fresh_attack_input
        )

        starting_progress_index = progress_index
        expected_step = steps[progress_index] if progress_index < len(steps) else None
        expected_labels = self._step_labels(expected_step)

        generic_partner_var_step = self._step_is_generic_partner_var(mission_id, expected_labels)
        partner_var_matched = generic_partner_var_step and self._partner_matches_generic_var(slot, snaps_dict)

        expected_norm = {str(label or "").strip().lower() for label in expected_labels}
        dedicated_megacrash_match = self._megacrash_step_matches(
            expected_labels,
            opponent_in_megacrash,
            previous_opponent_megacrash,
        )
        current_matches_expected = self._mission_snapshot_label_matches(
            snap,
            expected_labels,
        )
        hit_confirm_available = self._peek_mission_hit_event(frame_idx) is not None
        input_matches_expected = self._step_input_matches(
            character_name, expected_step, expected_labels, frame_idx
        )
        non_damage_confirm = (
            self._step_has_non_damage_confirm(expected_labels, snap, current_label)
            or (self._step_is_baroque_cancel(expected_labels) and input_matches_expected)
        )
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
        input_can_identify_expected = bool(
            input_matches_expected
            and (
                current_matches_expected
                or self._label_is_ignorable(current_label)
                or step_allows_whiff
                or pass_confirm
                or zero_damage_confirm
                or self._step_is_baroque_cancel(expected_labels)
            )
        )

        if self._runtime.get("pending_step_index") != progress_index:
            self._runtime.update({
                "pending_step_index": None,
                "pending_labels": [],
                "pending_anim": None,
                "pending_started_frame": -9999,
                "pending_input_serial": 0,
                "pending_label_confirmed": False,
                "pending_arm_source": "",
            })

        pending_step_index = self._runtime.get("pending_step_index")
        pending_labels = list(self._runtime.get("pending_labels") or [])
        pending_input_serial = int(
            self._runtime.get("pending_input_serial", 0) or 0
        )

        # Arm from the expected command before the action label catches up.
        # Input alone never completes the step. It only opens the ownership
        # window so an HP or combo edge that arrives first remains claimable.
        if (
            MISSION_REQUIRE_DAMAGE_CONFIRM
            and pending_step_index != progress_index
            and expected_labels
            and input_matches_expected
            and not pass_confirm
            and not step_allows_whiff
            and not zero_damage_confirm
        ):
            matched_input_serial = int(
                self._runtime.get("mission_input_match_serial", 0) or 0
            )
            self._runtime.update({
                "pending_step_index": progress_index,
                "pending_labels": expected_labels[:],
                "pending_anim": current_anim if current_matches_expected else None,
                "pending_started_frame": int(frame_idx),
                "pending_input_serial": matched_input_serial,
                "pending_label_confirmed": bool(current_matches_expected),
                "pending_arm_source": "label+input" if current_matches_expected else "input",
            })
            pending_step_index = progress_index
            pending_labels = expected_labels[:]
            pending_input_serial = matched_input_serial

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
                or dedicated_megacrash_match
                or (
                    (current_matches_expected or input_can_identify_expected)
                    and (input_can_identify_expected or not self._label_is_ignorable(current_label))
                    and (
                        pass_confirm
                        or (
                            (is_fresh_instance or input_can_identify_expected)
                            and (
                                opponent_in_combo_state
                                or hit_confirm_available
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

        # Mission routes are ordered subsequences. Unlisted moves are ignored,
        # but a normal required move must still own its confirmation window.
        # Only explicit whiff, pass, zero-damage, and grace rules may bypass that.

        if partner_var_matched:
            print(f"[mission generic var] slot={slot} step={progress_index} labels={expected_labels!r}")
            completed_grace = self._step_grace(expected_step)
            progress_index += 1
            self._runtime.update({
                "progress_index": progress_index,
                "pending_step_index": None,
                "pending_labels": [],
                "pending_anim": None,
                "pending_started_frame": -9999,
                "pending_label_confirmed": False,
                "pending_arm_source": "",
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
                    matched_input_serial = int(
                        self._runtime.get("mission_input_match_serial", 0) or 0
                    )
                    buffered_hit = self._peek_mission_hit_event(
                        frame_idx,
                        min_frame=int(frame_idx) - MISSION_HIT_PRELABEL_WINDOW,
                    )
                    buffered_frame = int(buffered_hit.get("frame", frame_idx)) if buffered_hit else int(frame_idx)
                    self._runtime.update({
                        "pending_step_index": progress_index,
                        "pending_labels": expected_labels[:],
                        "pending_anim": current_anim,
                        "pending_started_frame": buffered_frame,
                        "pending_input_serial": matched_input_serial,
                        "pending_label_confirmed": bool(current_matches_expected),
                        "pending_arm_source": "label" if current_matches_expected else "input",
                    })
                    pending_step_index = progress_index
                    pending_labels = expected_labels[:]
                    pending_input_serial = matched_input_serial

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
                matched_input_serial = int(
                    self._runtime.get("mission_input_match_serial", 0) or 0
                )
                buffered_hit = self._peek_mission_hit_event(
                    frame_idx,
                    min_frame=int(frame_idx) - MISSION_HIT_PRELABEL_WINDOW,
                )
                self._runtime.update({
                    "pending_step_index": progress_index,
                    "pending_labels": expected_labels[:],
                    "pending_anim": current_anim,
                    "pending_started_frame": int(buffered_hit.get("frame", frame_idx)) if buffered_hit else int(frame_idx),
                    "pending_input_serial": matched_input_serial,
                    "pending_label_confirmed": bool(current_matches_expected),
                    "pending_arm_source": "baroque",
                })
                pending_step_index = progress_index
                pending_labels = expected_labels[:]
                pending_input_serial = matched_input_serial

            pending_anim = self._runtime.get("pending_anim")
            if pending_step_index == progress_index and current_matches_expected:
                self._runtime["pending_label_confirmed"] = True
                if pending_anim is None:
                    pending_anim = current_anim
                    self._runtime["pending_anim"] = current_anim
                buffered_hit = self._peek_mission_hit_event(
                    frame_idx,
                    min_frame=int(frame_idx) - MISSION_HIT_PRELABEL_WINDOW,
                )
                if buffered_hit is not None:
                    self._runtime["pending_started_frame"] = min(
                        int(self._runtime.get("pending_started_frame", frame_idx) or frame_idx),
                        int(buffered_hit.get("frame", frame_idx)),
                    )
            pending_started_frame = int(self._runtime.get("pending_started_frame", -9999) or -9999)
            pending_label_confirmed = bool(self._runtime.get("pending_label_confirmed", False))
            pending_hit_confirm_available = self._peek_mission_hit_event(
                frame_idx,
                min_frame=pending_started_frame,
            ) is not None
            pending_age = int(frame_idx) - pending_started_frame
            pending_move_still_owns_window = bool(
                current_matches_expected
                or current_anim == pending_anim
                or (
                    not pending_label_confirmed
                    and 0 <= pending_age <= MISSION_HIT_PRELABEL_WINDOW
                    and bool(pending_input_serial)
                )
                or reset_grace_confirm_allowed
                or step_allows_whiff
            )

            # If another non-ignorable move starts before the required move is
            # confirmed, discard the pending marker. The unrelated move remains
            # irrelevant and cannot donate its later damage to this step.
            if (
                pending_step_index == progress_index
                and pending_labels
                and not pending_move_still_owns_window
                and not self._label_is_ignorable(current_label)
            ):
                self._runtime.update({
                    "pending_step_index": None,
                    "pending_labels": [],
                    "pending_anim": None,
                    "pending_started_frame": -9999,
                    "pending_input_serial": 0,
                    "pending_label_confirmed": False,
                    "pending_arm_source": "",
                })
                pending_step_index = None
                pending_labels = []
                pending_input_serial = 0

            if (
                pending_step_index == progress_index
                and pending_labels
                and pending_move_still_owns_window
                and (
                    pending_label_confirmed
                    or current_matches_expected
                    or input_can_identify_expected
                    or baroque_damage_confirm
                )
                and (opponent_in_combo_state or pending_hit_confirm_available or reset_grace_confirm_allowed)
                and (
                    pending_hit_confirm_available
                    or opponent_took_damage
                    or non_damage_confirm
                    or doronjo_pass
                    or repeat_same_step_damage
                    or baroque_damage_confirm
                )
            ):
                completed_grace = self._step_grace(expected_step)
                if pending_hit_confirm_available or opponent_took_damage or doronjo_pass or repeat_same_step_damage:
                    self._consume_mission_hit_event(frame_idx, min_frame=pending_started_frame)

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
                    "pending_started_frame": -9999,
                    "pending_label_confirmed": False,
                    "pending_arm_source": "",
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

        if progress_index > starting_progress_index:
            # The next route step may only use inputs that occur after this step
            # completed. This prevents an earlier 5B from satisfying a future 5B
            # after 5A completes, while still ignoring arbitrary extra inputs
            # performed between the required ordered markers.
            serial_to_consume = max(
                int(self._runtime.get("mission_input_consumed_serial", 0) or 0),
                int(pending_input_serial or 0),
                int(self._runtime.get("pending_input_serial", 0) or 0),
                int(self._runtime.get("mission_input_match_serial", 0) or 0),
            )
            self._consume_mission_input_through(serial_to_consume)
            self._runtime["mission_step_start_serial"] = serial_to_consume

        if not partner_var_matched:
            self._runtime.update({
                "last_seen_label": current_label,
                "last_seen_anim": current_anim,
                "last_seen_move_level": current_move_level,
                # Store the same sustained signal used for liveness. Otherwise
                # a crouching reaction with an unmapped action ID can look like a
                # new combo edge every frame while the global counter is nonzero.
                "last_seen_hitstun": opponent_in_combo_state,
                "last_actual_hitstun": opponent_in_hitstun or opponent_in_megacrash,
                "last_opponent_megacrash": opponent_in_megacrash,
                "last_inputs": dict(current_inputs),
            })

        if progress_index >= len(steps):
            final_idx = max(0, len(steps) - 1)
            final_label = (
                (" / ".join(steps[final_idx]) if isinstance(steps[final_idx], list) else steps[final_idx])
                if steps else None
            )
            return _clear_payload(len(steps), final_idx, final_label)

        predicted_progress = self._refresh_predicted_progress(
            steps,
            progress_index,
        )
        self._prune_mission_event_buffers()
        display_index = min(
            max(0, int(predicted_progress)),
            max(0, len(steps) - 1),
        )

        payload.update({
            "just_cleared": False,
            "clear_seq": int(self._runtime.get("clear_seq", 0)),
            "celebrate_pending": bool(self._runtime.get("celebrate_pending", False)),
            "celebrate_token": int(self._runtime.get("celebrate_token", 0) or 0),
            "completed_step_count": predicted_progress,
            "confirmed_step_count": progress_index,
            "predicted_step_count": max(0, predicted_progress - progress_index),
            "prediction_active": bool(predicted_progress > progress_index),
            "prediction_revision": int(self._runtime.get("prediction_revision", 0) or 0),
            "current_step_index": display_index,
            "current_step_label": (
                " / ".join(self._step_labels(steps[display_index]))
                if display_index < len(steps) else None
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
            "confirmed_step_count": 0,
            "predicted_step_count": 0,
            "prediction_active": False,
            "current_step_index": 0,
            "current_step_label": None,
            "just_cleared": False,
            "celebrate_pending": False,
            "celebrate_token": 0,
            "selector_open": False,
            "selector_index": 0,
            "selector_hint": "Down, Down, Taunt: Open Mission Select",
            "selector_controls": "Up/Down: Move  Taunt: Select  Mouse still works",
            "scanlines": True,
            "goal_progress_type": None,
            "goal_target_state": None,
            "goal_current_frames": 0,
            "goal_needed_frames": 0,
            "goal_timer_active": False,
        }