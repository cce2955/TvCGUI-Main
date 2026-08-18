"""
hud_overlay_manager.py
----------------------
Owns the hud_overlay subprocess and the per-frame data file it reads.

Public surface:
    mgr = HudOverlayManager(move_map, global_map)
    mgr.write_data(render_snap_by_slot, last_scan_normals, mission_mgr)
    mgr.check_proc()   # call each frame; detects if the proc died
    # subprocess is launched/stopped by master_overlay; this manager
    # only owns the data file write.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from typing import TYPE_CHECKING

from tvcgui.core.paths import user_data_path
from tvcgui.features.frame_data.attack_property_runtime import resolve_live_attack_definition
from tvcgui.runtime.realtime_sampler import RealtimeCombatSampler
from tvcgui.features.overlay.damage_scaling import annotate_damage_scaling_payload
from tvcgui.features.overlay.hitstun_scaling import annotate_hitstun_scaling_payload

if TYPE_CHECKING:
    from tvcgui.features.training.mission_manager import MissionManager

HUD_OVERLAY_DATA_FILE = user_data_path("overlay", "hud_overlay_data.json")
HUD_REALTIME_INPUT_FILE = user_data_path("overlay", "hud_input_realtime.json")
# Stun clocks are ephemeral realtime IPC, not persistent user data. Keep this
# tiny file off the project/EXE directory so OneDrive/AV cannot stall a short
# 9-15F blockstun countdown by coalescing several JSON rewrites.
HUD_REALTIME_STUN_FILE = os.path.join(tempfile.gettempdir(), "tvcgui_hud_stun_realtime.json")
ATTACK_PROPERTY_SHORT_LABELS = {
    0x01: "UNBLK",
    0x02: "UNBLK",
    0x04: "UNBLK",
    0x09: "MID",
    0x0A: "MID",
    0x0C: "MID",
    0x11: "OVERHEAD",
    0x12: "OVERHEAD",
    0x14: "OVERHEAD",
    0x21: "LOW",
    0x22: "LOW",
    0x24: "LOW",
}


def _native_hs_contact_values(sample: dict, decay_frames: int = 0) -> dict | None:
    """Choose the native victim clock already resolved on the contact sample.

    The hit resolver writes +0x1210 (resolved hitstun) and, when applicable,
    +0x1220 (scaled air-recovery/untech lockout) before the later HP
    subtraction. A 240 Hz HP-loss sample can therefore use those final engine
    values directly instead of rebuilding stun from the attacker's profile.
    """
    if not isinstance(sample, dict):
        return None
    try:
        hitstun = max(0, int(sample.get("hitstun_remaining", 0) or 0))
        untech = max(0, int(sample.get("untech_remaining", 0) or 0))
        decay = max(0, int(decay_frames or 0))
    except Exception:
        return None

    if untech > 0:
        return {
            "clock_source": "untech",
            "target": untech,
            "native_hitstun": hitstun,
            "native_untech": untech,
            "decay_frames": decay,
            # Reconstruction is display-only. The authoritative endpoint is
            # always the final native +0x1220 value above.
            "raw_estimate": untech + decay,
        }
    if hitstun > 0:
        return {
            "clock_source": "hitstun",
            "target": hitstun,
            "native_hitstun": hitstun,
            "native_untech": 0,
            "decay_frames": 0,
            "raw_estimate": hitstun,
        }
    return None


def _known_attack_property(value) -> bool:
    try:
        packed = int(value) & 0xFF
    except Exception:
        return False
    guard = packed & 0x38
    strength = packed & 0x07
    return guard in (0x00, 0x08, 0x10, 0x20) and strength in (0x01, 0x02, 0x04)


def _overlay_attack_property_label(value) -> str:
    try:
        packed = int(value) & 0xFF
    except Exception:
        return ""
    if not _known_attack_property(packed):
        return ""
    guard = packed & 0x38
    if guard == 0x10:
        return "OVERHEAD"
    if guard == 0x20:
        return "LOW"
    if guard == 0x08:
        return "MID"
    return "UNBLK"





def _attack_property_move_quality(move: dict | None) -> tuple[int, int, int]:
    """Prefer the populated owner when one action ID has duplicate scan rows."""
    if not isinstance(move, dict):
        return (0, 0, 0)
    segments = [row for row in (move.get("hit_segments") or []) if isinstance(row, dict)]
    populated = sum(
        1 for key in (
            "attack_property", "damage", "active_start", "active_end",
            "hit_reaction", "kb_type", "hitstun", "blockstun",
        )
        if move.get(key) is not None
    )
    source_bonus = 1 if str(move.get("source") or "") == "table" else 0
    return (len(segments), populated, source_bonus)


def _bridge_attack_property_annotations(slot_payload: dict, snap: dict) -> None:
    """Forward profiler annotations into the subprocess payload.

    The telemetry scheduler annotates the main-process fighter snapshot, but
    the overlay subprocess only sees fields serialized here. Static move
    definitions have no runtime actor address, so dropping display/source
    metadata makes valid properties look inactive.
    """
    for key, value in (snap or {}).items():
        if key.startswith("attack_property_") and key not in {
            "attack_property",
            "attack_property_label",
        }:
            slot_payload[key] = value


class HudOverlayManager:
    """
    Writes hud_overlay_data.json each frame so the hud_overlay subprocess
    (parented to Dolphin) can render the transparent HUD.

    Parameters
    ----------
    move_map : dict
        Per-character anim-ID -> label mapping.
    global_map : dict
        Global anim-ID -> label mapping.
    """

    def __init__(
        self,
        move_map: dict,
        global_map: dict,
        realtime_sampler: RealtimeCombatSampler | None = None,
    ) -> None:
        self._move_map = move_map
        self._global_map = global_map

        self._proc: subprocess.Popen | None = None
        self._active: bool = False
        self._last_serialized: str = ""
        self._payload_condition = threading.Condition()
        self._pending_payload: dict | None = None
        self._payload_writer_stop = False
        self._payload_writer_thread = threading.Thread(
            target=self._payload_writer_loop,
            name="TvCOverlayPayloadWriter",
            daemon=True,
        )
        self._payload_writer_thread.start()

        # Realtime Dolphin reads belong to a permanent scheduler outside the
        # overlay. HudOverlayManager only reads cached packets from it.
        self._realtime_sampler = realtime_sampler or RealtimeCombatSampler()
        self._owns_realtime_sampler = realtime_sampler is None

        # The normal HUD payload is produced by the main GUI frame and may be
        # delayed by heavier scanners. Inputs are latency-sensitive, so mirror
        # only real input transitions into a tiny sidecar file from a dedicated
        # writer thread. The HUD subprocess polls this file at its own 60 Hz
        # render cadence and no longer waits for the full telemetry payload.
        self._input_bridge_condition = threading.Condition()
        self._input_bridge_stop = False
        self._input_bridge_dirty = False
        self._stun_bridge_stop = False
        self._stun_bridge_dirty = False
        self._input_bridge_slots: dict[str, dict] = {}
        self._input_bridge_last_held: dict[str, int] = {}
        self._input_bridge_last_combat: dict[str, tuple] = {}
        self._realtime_latest_sample_by_slot: dict[str, dict] = {}

        # Attacker move identity is cached only as optional contact metadata.
        # The 240 Hz victim HP edge mints the HS event from the victim's own
        # already-resolved +0x1210/+0x1220 counters. Duration never comes from
        # the profile cache or an inferred wall-clock timer.
        self._hs_armed_by_slot: dict[str, dict] = {}
        self._hs_bridge_teams: dict[str, dict] = {
            "P1": {"latest": {}, "events": []},
            "P2": {"latest": {}, "events": []},
        }
        self._hs_generation_by_team: dict[str, int] = {"P1": 0, "P2": 0}
        self._bs_bridge_teams: dict[str, dict] = {
            "P1": {"latest": {}, "events": []},
            "P2": {"latest": {}, "events": []},
        }
        self._bs_generation_by_team: dict[str, int] = {"P1": 0, "P2": 0}
        self._last_realtime_input_serialized = ""
        self._last_realtime_stun_serialized = ""
        for path in (HUD_REALTIME_INPUT_FILE, HUD_REALTIME_STUN_FILE):
            try:
                os.remove(path)
            except OSError:
                pass
        self._input_bridge_thread = threading.Thread(
            target=self._input_bridge_writer_loop,
            name="TvCRealtimeInputBridge",
            daemon=True,
        )
        self._input_bridge_thread.start()
        self._stun_bridge_thread = threading.Thread(
            target=self._stun_bridge_writer_loop,
            name="TvCRealtimeStunBridge",
            daemon=True,
        )
        self._stun_bridge_thread.start()
        self._realtime_sampler.add_listener(self._on_realtime_input_sample)

    def _arm_realtime_hs_move(
        self,
        slot_label: str,
        action_id: int,
        hitstun: int,
        move_label: str,
        *,
        point_active: bool = False,
    ) -> None:
        """Cache attacker move identity for labeling a later native contact."""
        try:
            hitstun = max(0, int(hitstun or 0))
            action_id = int(action_id or 0) & 0x7FFF
        except Exception:
            return
        if action_id <= 0:
            return
        slot = str(slot_label or "")
        if not slot:
            return
        team = "P1" if slot.startswith("P1") else ("P2" if slot.startswith("P2") else "")
        if not team:
            return
        armed = {
            "slot": slot,
            "team": team,
            "action_id": action_id,
            "hitstun": hitstun,
            "move_label": str(move_label or ""),
            "point_active": bool(point_active),
            "armed_ns": time.monotonic_ns(),
        }
        with self._input_bridge_condition:
            self._hs_armed_by_slot[slot] = armed

    def _realtime_hs_arm_for_team(self, team: str, now_ns: int) -> dict | None:
        """Pick the move that was already armed when the opponent was hit."""
        candidates: list[tuple[int, int, int, dict]] = []
        for slot, armed in self._hs_armed_by_slot.items():
            if str(armed.get("team") or "") != team:
                continue
            armed_ns = int(armed.get("armed_ns", 0) or 0)
            # Keep a short projectile/assist grace window after the owner leaves
            # the action. A newer armed move always wins.
            if armed_ns <= 0 or now_ns - armed_ns > 2_000_000_000:
                continue
            latest = self._realtime_latest_sample_by_slot.get(slot) or {}
            action_matches = int(latest.get("action_id", 0) or 0) == int(armed.get("action_id", 0) or 0)
            candidates.append((1 if action_matches else 0, 1 if bool(armed.get("point_active")) else 0, armed_ns, armed))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return dict(candidates[0][3])

    def _emit_realtime_hs_contact(self, victim_slot: str, sample: dict, previous_sample: dict | None) -> None:
        """Mint one HS clock from the victim's native values on the HP edge."""
        if not isinstance(previous_sample, dict):
            return
        try:
            previous_hp = int(previous_sample.get("current_hp", 0) or 0)
            current_hp = int(sample.get("current_hp", 0) or 0)
        except Exception:
            return
        if previous_hp <= 0 or current_hp >= previous_hp:
            return

        victim_team = "P1" if victim_slot.startswith("P1") else ("P2" if victim_slot.startswith("P2") else "")
        if not victim_team:
            return
        attacker_team = "P2" if victim_team == "P1" else "P1"
        now_ns = int(sample.get("sample_ns", 0) or time.monotonic_ns())

        # HP loss while +0x1204 is active is chip, not a hit. Never let a
        # blocked/chip contact mint (or preserve) a HITSTUN/UNTECH generation.
        # This classification is native-state driven and does not guess from
        # damage amount.
        try:
            native_blockstun = max(0, int(sample.get("blockstun_remaining", 0) or 0))
        except Exception:
            native_blockstun = 0
        if native_blockstun > 0:
            state = self._hs_bridge_teams.setdefault(attacker_team, {"latest": {}, "events": []})
            latest = state.get("latest") if isinstance(state.get("latest"), dict) else {}
            if str(latest.get("victim_slot") or "") == victim_slot:
                state["latest"] = {}
            return

        # Attacker state is metadata plus the already-mapped deterioration
        # counter. It never supplies the duration. The victim's native timers
        # were resolved before HP subtraction in the game code.
        armed = self._realtime_hs_arm_for_team(attacker_team, now_ns) or {}
        attacker_slot = str(armed.get("slot") or "")
        # The deterioration counter belongs to the engine's fixed C1 owner
        # lane. Use that lane first even when C2 is currently the point fighter.
        decay_owner_slot = f"{attacker_team}-C1"
        attacker_sample = (
            self._realtime_latest_sample_by_slot.get(decay_owner_slot)
            or self._realtime_latest_sample_by_slot.get(attacker_slot)
            or {}
        )
        decay_counter = max(0, int(attacker_sample.get("decay_counter", 0) or 0))
        native = _native_hs_contact_values(sample, decay_counter // 4)
        if not native:
            return

        generation = int(self._hs_generation_by_team.get(attacker_team, 0) or 0) + 1
        self._hs_generation_by_team[attacker_team] = generation
        event = {
            "generation": generation,
            "sample_ns": now_ns,
            "attacker_team": attacker_team,
            "attacker_slot": attacker_slot,
            "victim_slot": victim_slot,
            "action_id": int(armed.get("action_id", 0) or 0),
            "move_label": str(armed.get("move_label") or ""),
            "clock_source": str(native["clock_source"]),
            "native_hitstun": int(native["native_hitstun"]),
            "native_untech": int(native["native_untech"]),
            "target": int(native["target"]),
            "decay_frames": int(native["decay_frames"]),
            "raw_estimate": int(native["raw_estimate"]),
            "native": True,
        }
        state = self._hs_bridge_teams.setdefault(attacker_team, {"latest": {}, "events": []})
        state["latest"] = dict(event)
        events = state.setdefault("events", [])
        events.append(dict(event))
        del events[:-32]


    def _emit_realtime_blockstun_contact(self, victim_slot: str, sample: dict, previous_sample: dict | None) -> None:
        """Mint one native blockstun clock when a blocked contact resolves.

        +0x1204 is already the game's resolved live blockstun countdown. A new
        blocked contact is identified by the counter starting/increasing, or by
        the native impact-freeze timer being re-armed while blockstun is active.
        The latter catches blockstrings where the new move overwrites a still-
        nonzero blockstun counter with an equal or shorter value.
        """
        if not isinstance(previous_sample, dict):
            return
        try:
            previous_blockstun = max(0, int(previous_sample.get("blockstun_remaining", 0) or 0))
            current_blockstun = max(0, int(sample.get("blockstun_remaining", 0) or 0))
            previous_freeze = max(0, int(previous_sample.get("impact_freeze_remaining", 0) or 0))
            current_freeze = max(0, int(sample.get("impact_freeze_remaining", 0) or 0))
        except Exception:
            return
        victim_team = "P1" if victim_slot.startswith("P1") else ("P2" if victim_slot.startswith("P2") else "")
        if not victim_team:
            return
        attacker_team = "P2" if victim_team == "P1" else "P1"
        state = self._bs_bridge_teams.setdefault(attacker_team, {"latest": {}, "events": []})

        # Clock lifetime is game-state driven, never wall-clock driven. When
        # Dolphin is paused on a nonzero +0x1204 value there are no new samples,
        # so the current contact must remain latched indefinitely. Clear it only
        # when the game itself advances +0x1204 to zero.
        if current_blockstun <= 0:
            latest = state.get("latest") if isinstance(state.get("latest"), dict) else {}
            if str(latest.get("victim_slot") or "") == victim_slot:
                state["latest"] = {}
            return

        new_contact = bool(
            previous_blockstun <= 0
            or current_blockstun > previous_blockstun
            or (current_freeze > 0 and current_freeze > previous_freeze)
        )
        if not new_contact:
            return

        now_ns = int(sample.get("sample_ns", 0) or time.monotonic_ns())
        armed = self._realtime_hs_arm_for_team(attacker_team, now_ns) or {}

        generation = int(self._bs_generation_by_team.get(attacker_team, 0) or 0) + 1
        self._bs_generation_by_team[attacker_team] = generation
        event = {
            "generation": generation,
            "sample_ns": now_ns,
            "attacker_team": attacker_team,
            "attacker_slot": str(armed.get("slot") or ""),
            "victim_slot": victim_slot,
            "action_id": int(armed.get("action_id", 0) or 0),
            "move_label": str(armed.get("move_label") or ""),
            "target": current_blockstun,
            "native_blockstun": current_blockstun,
            "impact_freeze": current_freeze,
            "native": True,
        }
        state["latest"] = dict(event)
        events = state.setdefault("events", [])
        events.append(dict(event))
        del events[:-32]

    def _on_realtime_input_sample(self, slot_label: str, sample: dict) -> None:
        """Queue only latency-sensitive input transitions for the HUD sidecar."""
        if not isinstance(sample, dict):
            return
        slot = str(slot_label or sample.get("slot") or "")
        if not slot:
            return
        try:
            held = int(sample.get("held", 0) or 0) & 0xFFFF
            pressed = int(sample.get("pressed", 0) or 0) & 0xFFFF
            released = int(sample.get("released", 0) or 0) & 0xFFFF
            seq = int(sample.get("seq", 0) or 0)
            sample_ns = int(sample.get("sample_ns", 0) or time.monotonic_ns())
        except Exception:
            return

        previous_sample = self._realtime_latest_sample_by_slot.get(slot)
        self._realtime_latest_sample_by_slot[slot] = dict(sample)

        previous_held = self._input_bridge_last_held.get(slot)
        input_changed = previous_held is None or held != previous_held or bool(pressed) or bool(released)
        self._input_bridge_last_held[slot] = held

        combat_key = (
            int(sample.get("blockstun_remaining", 0) or 0),
            int(sample.get("hitstun_remaining", 0) or 0),
            int(sample.get("untech_remaining", 0) or 0),
            int(sample.get("impact_freeze_remaining", 0) or 0),
            int(sample.get("fighter_combo_count", 0) or 0),
            int(sample.get("decay_counter", 0) or 0),
            int(sample.get("state_flags_6c", 0) or 0) & 0xFFFFFFFF,
            int(sample.get("current_hp", 0) or 0),
            int(sample.get("action_id", 0) or 0),
            bool(sample.get("point_active", False)),
        )
        combat_changed = self._input_bridge_last_combat.get(slot) != combat_key
        self._input_bridge_last_combat[slot] = combat_key
        if not input_changed and not combat_changed:
            return

        event = {
            "seq": seq,
            "held": held,
            "pressed": pressed,
            "released": released,
            "sample_ns": sample_ns,
            "current_hp": int(sample.get("current_hp", 0) or 0),
            "action_id": int(sample.get("action_id", 0) or 0),
            "blockstun_remaining": max(0, int(sample.get("blockstun_remaining", 0) or 0)),
            "hitstun_remaining": max(0, int(sample.get("hitstun_remaining", 0) or 0)),
            "untech_remaining": max(0, int(sample.get("untech_remaining", 0) or 0)),
            "impact_freeze_remaining": max(0, int(sample.get("impact_freeze_remaining", 0) or 0)),
            "fighter_combo_count": max(0, int(sample.get("fighter_combo_count", 0) or 0)),
            "decay_counter": max(0, int(sample.get("decay_counter", 0) or 0)),
            "state_flags_6c": int(sample.get("state_flags_6c", 0) or 0) & 0xFFFFFFFF,
            "point_active": bool(sample.get("point_active", False)),
        }
        with self._input_bridge_condition:
            if combat_changed:
                self._emit_realtime_hs_contact(slot, sample, previous_sample)
                self._emit_realtime_blockstun_contact(slot, sample, previous_sample)
            state = self._input_bridge_slots.setdefault(slot, {"latest": {}, "samples": []})
            state["latest"] = dict(event)
            if input_changed:
                samples = state.setdefault("samples", [])
                samples.append(dict(event))
                del samples[:-96]
                self._input_bridge_dirty = True
            if combat_changed:
                self._stun_bridge_dirty = True
            self._input_bridge_condition.notify_all()

    def _input_bridge_writer_loop(self) -> None:
        """Write input edges only. Combat clocks use the separate tiny stun IPC."""
        while True:
            with self._input_bridge_condition:
                while not self._input_bridge_dirty and not self._input_bridge_stop:
                    self._input_bridge_condition.wait(timeout=0.25)
                if self._input_bridge_stop and not self._input_bridge_dirty:
                    return
                slots = {
                    slot: {
                        "latest": dict(state.get("latest") or {}),
                        "samples": [dict(item) for item in state.get("samples", ())],
                    }
                    for slot, state in self._input_bridge_slots.items()
                }
                self._input_bridge_dirty = False

            payload = {"written_wall_ns": time.time_ns(), "slots": slots}
            try:
                serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                os.makedirs(os.path.dirname(HUD_REALTIME_INPUT_FILE), exist_ok=True)
                tmp = f"{HUD_REALTIME_INPUT_FILE}.tmp"
                with open(tmp, "w", encoding="utf-8") as handle:
                    handle.write(serialized)
                os.replace(tmp, HUD_REALTIME_INPUT_FILE)
                self._last_realtime_input_serialized = serialized
            except Exception:
                pass

    @staticmethod
    def _stun_slot_latest(state: dict) -> dict:
        latest = dict(state.get("latest") or {}) if isinstance(state, dict) else {}
        if not latest:
            return {}
        # Keep the realtime stun transport intentionally tiny.
        keys = (
            "seq", "sample_ns", "current_hp", "action_id",
            "blockstun_remaining", "hitstun_remaining", "untech_remaining",
            "impact_freeze_remaining", "fighter_combo_count", "decay_counter",
            "state_flags_6c", "point_active",
        )
        return {key: latest.get(key) for key in keys if key in latest}

    def _stun_bridge_writer_loop(self) -> None:
        """Write native stun counters independently of input-history JSON.

        Blockstun is short enough that a delayed full input-history rewrite can
        collapse several 1F native decrements. This transport contains only the
        current combat counters/contact tokens and lives in the OS temp dir.
        """
        while True:
            with self._input_bridge_condition:
                while not self._stun_bridge_dirty and not self._stun_bridge_stop:
                    self._input_bridge_condition.wait(timeout=0.25)
                if self._stun_bridge_stop and not self._stun_bridge_dirty:
                    return
                slots = {
                    slot: {"latest": self._stun_slot_latest(state)}
                    for slot, state in self._input_bridge_slots.items()
                }
                hs_teams = {
                    team: {
                        "latest": dict(state.get("latest") or {}),
                        "events": [dict(item) for item in state.get("events", ())[-8:]],
                    }
                    for team, state in self._hs_bridge_teams.items()
                }
                bs_teams = {
                    team: {
                        "latest": dict(state.get("latest") or {}),
                        "events": [dict(item) for item in state.get("events", ())[-8:]],
                    }
                    for team, state in self._bs_bridge_teams.items()
                }
                self._stun_bridge_dirty = False

            payload = {
                "written_wall_ns": time.time_ns(),
                "slots": slots,
                "hs_teams": hs_teams,
                "bs_teams": bs_teams,
            }
            try:
                serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                tmp = f"{HUD_REALTIME_STUN_FILE}.tmp"
                with open(tmp, "w", encoding="utf-8") as handle:
                    handle.write(serialized)
                os.replace(tmp, HUD_REALTIME_STUN_FILE)
                self._last_realtime_stun_serialized = serialized
            except Exception:
                pass

    def _set_input_sampler_targets(self, render_snap_by_slot: dict) -> None:
        self._realtime_sampler.set_targets(render_snap_by_slot)

    def _input_snapshot_for_slot(
        self,
        slot_label: str,
        base: int,
    ) -> tuple[dict, list[dict]]:
        return self._realtime_sampler.snapshot_for_slot(slot_label, base)

    def _queue_payload(self, payload: dict) -> None:
        with self._payload_condition:
            self._pending_payload = payload
            self._payload_condition.notify()

    def _payload_writer_loop(self) -> None:
        while True:
            with self._payload_condition:
                while self._pending_payload is None and not self._payload_writer_stop:
                    self._payload_condition.wait(timeout=0.25)
                if self._payload_writer_stop and self._pending_payload is None:
                    return
                payload = self._pending_payload
                self._pending_payload = None
            try:
                serialized = json.dumps(payload or {}, ensure_ascii=False, separators=(",", ":"))
                if serialized == self._last_serialized and os.path.isfile(HUD_OVERLAY_DATA_FILE):
                    continue
                os.makedirs(os.path.dirname(HUD_OVERLAY_DATA_FILE), exist_ok=True)
                tmp = f"{HUD_OVERLAY_DATA_FILE}.tmp"
                with open(tmp, "w", encoding="utf-8") as handle:
                    handle.write(serialized)
                os.replace(tmp, HUD_OVERLAY_DATA_FILE)
                self._last_serialized = serialized
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self._active

    def prime_input_sampler_targets(self, render_snap_by_slot: dict) -> None:
        """Point the 240 Hz input sampler at the current live fighter objects."""
        self._set_input_sampler_targets(render_snap_by_slot)

    def add_input_sample_listener(self, listener) -> None:
        self._realtime_sampler.add_listener(listener)

    def remove_input_sample_listener(self, listener) -> None:
        self._realtime_sampler.remove_listener(listener)

    def mission_input_bundle(self, slot_label: str, fighter_base: int = 0) -> tuple[dict, list[dict]]:
        """Return the latest packet plus the sampler's ordered edge queue."""
        return self._input_snapshot_for_slot(str(slot_label), int(fighter_base or 0))

    def write_data(
        self,
        render_snap_by_slot: dict,
        last_scan_normals,
        mission_mgr: "MissionManager",
        punish_overlay: dict | None = None,
        timing_payload: dict | None = None,
    ) -> None:
        """
        Build and write hud_overlay_data.json from the current frame's
        fighter snapshots, scan-normals data, and mission state.
        """
        payload: dict = {}
        self._set_input_sampler_targets(render_snap_by_slot)

        mission_active_slot = mission_mgr.active_slot

        # Compute VAR state once for the active mission slot
        mission_var: dict = {}
        if mission_active_slot:
            try:
                mission_var = mission_mgr.var_state(mission_active_slot, render_snap_by_slot)
            except Exception:
                mission_var = {}

        for slot_label, snap in render_snap_by_slot.items():
            # The main loop already produced the authoritative final label.
            # Do not re-read the profiler or rebuild the name in this sink.
            snap = dict(snap or {})
            cur_anim = snap.get("attA") or snap.get("attB")
            mv_label = snap.get("mv_label")
            mv_label_display = (
                snap.get("final_move_label")
                or snap.get("mv_label_display")
                or mv_label
            )
            active_start = None
            active_end = None
            attack_property = None
            attack_property_b = None
            attack_property_label = ""
            matched_move = None
            native_definition = None
            input_packet, input_samples = self._input_snapshot_for_slot(
                slot_label,
                int(snap.get("base") or 0),
            )
            input_held = int(input_packet.get("held", 0) or 0) & 0xFFFF
            input_pressed = int(input_packet.get("pressed", 0) or 0) & 0xFFFF
            input_released = int(input_packet.get("released", 0) or 0) & 0xFFFF

            slot_tree_row = None
            if last_scan_normals and cur_anim is not None:
                for slot_data in last_scan_normals:
                    if slot_data.get("slot_label") == slot_label:
                        slot_tree_row = slot_data
                        candidates = [
                            mv for mv in slot_data.get("moves", [])
                            if isinstance(mv, dict) and mv.get("id") == cur_anim
                        ]
                        if candidates:
                            matched_move = max(candidates, key=_attack_property_move_quality)
                            active_start = matched_move.get("active_start")
                            active_end = matched_move.get("active_end")
                        break

            # Cache move identity as optional metadata for the realtime contact
            # event. The duration itself comes from the victim's native clocks.
            if isinstance(matched_move, dict):
                self._arm_realtime_hs_move(
                    slot_label,
                    int(cur_anim or input_packet.get("action_id", 0) or 0),
                    int(matched_move.get("hitstun") or 0),
                    str(mv_label_display or mv_label or matched_move.get("pretty_name") or matched_move.get("move_name") or ""),
                    point_active=bool(input_packet.get("point_active", snap.get("damage_point_active", False))),
                )

            # Attack Property is native-only. Resolve the current action from
            # the fighter's character table and never fill this card from the
            # frame-data profile or scanner tree.
            if cur_anim is not None:
                try:
                    tree_root = int((slot_tree_row or {}).get("chr_tbl_abs") or 0)
                except Exception:
                    tree_root = 0
                native_definition = resolve_live_attack_definition(
                    int(snap.get("base") or 0),
                    int(cur_anim),
                    chr_tbl_abs=tree_root or None,
                )
                if native_definition.get("status") == "OK":
                    attack_property = native_definition.get("property_a")
                    attack_property_b = native_definition.get("property_b")
                    attack_property_label = _overlay_attack_property_label(attack_property)

            partner_slot = mission_var.get("partner_slot")

            slot_payload = {
                "base":                   int(snap.get("base") or 0),
                "name":                   snap.get("name"),
                "cur":                    snap.get("cur"),
                "max":                    snap.get("max"),
                "meter":                  snap.get("meter"),
                # Authored stun is a zero-latency contact-side hint for the HS
                # clock. The realtime native timer remains authoritative once
                # it appears, but we do not wait for it before showing/draining.
                "move_hitstun":           matched_move.get("hitstun") if isinstance(matched_move, dict) else None,
                "mv_id_display":          cur_anim,
                "mv_id_label_display":    snap.get("mv_id_label_display"),
                "mv_label":               mv_label,
                "mv_label_display":       mv_label_display,
                "final_move_label":       mv_label_display,
                "mv_label_base":          snap.get("mv_label_base") or snap.get("mv_label"),
                "mv_label_aliases":       list(snap.get("mv_label_aliases") or []),
                "move_level":             snap.get("move_level"),
                "move_level_total":       snap.get("move_level_total"),
                "move_level_label":       snap.get("move_level_label"),
                "move_level_source":      snap.get("move_level_source"),
                "move_level_confidence":  snap.get("move_level_confidence"),
                "move_level_projectile_id": snap.get("move_level_projectile_id"),
                "profile_history_label":    snap.get("profile_history_label"),
                "profile_history_action_id": snap.get("profile_history_action_id"),
                "profile_history_projectile_id": snap.get("profile_history_projectile_id"),
                "profile_history_static_addr": snap.get("profile_history_static_addr"),
                "profile_history_age":      snap.get("profile_history_age"),
                "profile_history_seen_wall_time": snap.get("profile_history_seen_wall_time"),
                "profile_history_token":    snap.get("profile_history_token"),
                "profile_live_label":       snap.get("profile_live_label"),
                "profile_resolved_label":   snap.get("profile_resolved_label"),
                "profile_live_active":      bool(snap.get("profile_live_active", False)),
                "move_label_source":        snap.get("move_label_source"),
                "profile_label_override":   snap.get("profile_label_override", False),
                "baroque_ready_local":    snap.get("baroque_ready_local", False),
                "baroque_red_pct_max":    snap.get("baroque_red_pct_max", 0.0),
                "baroque_cancel_raw":     snap.get("baroque_cancel_raw", False),
                "baroque_cancel_latched": snap.get("baroque_cancel_latched", False),
                "baroque_cancel_frames":  snap.get("baroque_cancel_latch_frames", 0),
                "input_previous":         input_packet.get("previous", 0),
                "input_held":             input_held,
                "input_pressed":          input_pressed,
                "input_released":         input_released,
                "input_samples":          input_samples,
                "input_text":             input_packet.get("held_text", "5"),
                "input_pressed_text":     input_packet.get("pressed_text", "none"),
                "input_released_text":    input_packet.get("released_text", "none"),
                "realtime_sample_ns":     int(input_packet.get("sample_ns", 0) or 0),
                "realtime_current_hp":    int(input_packet.get("current_hp", snap.get("cur") or 0) or 0),
                "realtime_action_id":     int(input_packet.get("action_id", cur_anim or 0) or 0),
                "realtime_hitstun_remaining": max(0, int(input_packet.get("hitstun_remaining", 0) or 0)),
                "realtime_untech_remaining": max(0, int(input_packet.get("untech_remaining", 0) or 0)),
                "realtime_fighter_combo_count": max(0, int(input_packet.get("fighter_combo_count", 0) or 0)),
                "realtime_decay_counter": max(0, int(input_packet.get("decay_counter", 0) or 0)),
                "realtime_state_flags_6c": int(input_packet.get("state_flags_6c", 0) or 0) & 0xFFFFFFFF,
                "realtime_point_active":  bool(input_packet.get("point_active", False)),
                "active_start":           active_start,
                "active_end":             active_end,
                "attack_property":        attack_property,
                "attack_property_label":  attack_property_label,
                "attack_property_packet_count": snap.get("attack_property_packet_count", 0),
                "attack_property_live_actor": snap.get("attack_property_live_actor"),
                "attack_property_live_a": snap.get("attack_property_live_a"),
                "attack_property_live_b": snap.get("attack_property_live_b"),
                "attack_property_live_a_text": snap.get("attack_property_live_a_text", ""),
                "attack_property_live_b_text": snap.get("attack_property_live_b_text", ""),
                "attack_property_live_b_unknown": snap.get("attack_property_live_b_unknown"),
                "attack_property_live_status20": snap.get("attack_property_live_status20"),
                "attack_property_live_damage": snap.get("attack_property_live_damage"),
                "attack_property_live_phase_a": snap.get("attack_property_live_phase_a"),
                "attack_property_live_phase_b": snap.get("attack_property_live_phase_b"),
                "attack_property_live_victim_slot": snap.get("attack_property_live_victim_slot", ""),
                "attack_property_live_action_frame": snap.get("attack_property_live_action_frame"),
                "recoverable_hp":          snap.get("recoverable_hp", 0),
                "recoverable_ceiling":     snap.get("recoverable_ceiling", snap.get("aux")),
                "recoverable_pct_max":     snap.get("recoverable_pct_max", 0.0),
                "red_health_current":      snap.get("red_health_current", snap.get("cur")),
                "red_health_aux":          snap.get("red_health_aux", snap.get("aux")),
                "red_health_recoverable":  snap.get("red_health_recoverable", snap.get("recoverable_hp", 0)),
                "red_health_pct_max":      snap.get("red_health_pct_max", snap.get("recoverable_pct_max", 0.0)),
                "red_health_pending_current": snap.get("red_health_pending_current", 0),
                "red_health_pending_aux":  snap.get("red_health_pending_aux", 0),
                "red_health_heal_sync":    snap.get("red_health_heal_sync", 0),
                "red_health_point":        snap.get("red_health_point", False),
                "red_health_baroque":      snap.get("red_health_baroque", False),
                "red_health_red_spent":    snap.get("red_health_red_spent", 0),
                "red_health_last_event":   snap.get("red_health_last_event", ""),
                "red_health_last_current_delta": snap.get("red_health_last_current_delta", 0),
                "red_health_last_aux_delta": snap.get("red_health_last_aux_delta", 0),
                "red_health_last_red_delta": snap.get("red_health_last_red_delta", 0),
                "red_health_last_predicted": snap.get("red_health_last_predicted"),
                "red_health_last_match":   snap.get("red_health_last_match"),
                "red_health_last_attacker": snap.get("red_health_last_attacker", ""),
                "red_health_last_move":    snap.get("red_health_last_move", ""),
                "meter_profile_current":   snap.get("meter_profile_current", snap.get("meter")),
                "meter_profile_last_delta": snap.get("meter_profile_last_delta", 0),
                "meter_profile_last_kind": snap.get("meter_profile_last_kind", ""),
                "meter_profile_last_role": snap.get("meter_profile_last_role", ""),
                "meter_profile_last_source": snap.get("meter_profile_last_source", ""),
                "meter_profile_last_move": snap.get("meter_profile_last_move", ""),
                "meter_profile_last_predicted": snap.get("meter_profile_last_predicted"),
                "meter_profile_last_difference": snap.get("meter_profile_last_difference"),
                "meter_profile_last_match": snap.get("meter_profile_last_match"),
                "meter_profile_last_base_damage": snap.get("meter_profile_last_base_damage", 0),
                "meter_profile_last_property_a": snap.get("meter_profile_last_property_a", ""),
                "meter_profile_last_property_b": snap.get("meter_profile_last_property_b", ""),
                "mission_target":         slot_label == mission_active_slot,
                "mission_var_partner":    slot_label == partner_slot,
                "mission_wrong_ready":    bool(
                    mission_var.get("wrong_character_ready", False)
                    and slot_label == partner_slot
                ),
                "mission_var_ready":      bool(
                    mission_var.get("partner_airborne", False)
                    and slot_label == partner_slot
                ),
                "mission_varing":         bool(
                    mission_var.get("partner_varing", False)
                    and slot_label == partner_slot
                ),
                "timing_action_frame":     int(snap.get("timing_action_frame") or 0),
                "timing_blockstun":        int(snap.get("timing_blockstun") or 0),
                "timing_hitstun_total":    int(snap.get("timing_hitstun_total") or 0),
                "timing_hitstun_remaining": int(snap.get("timing_hitstun_remaining") or 0),
                "timing_hitstop_active":   int(snap.get("timing_hitstop_active") or 0),
                "timing_hitstop_pending":  int(snap.get("timing_hitstop_pending") or 0),
                "timing_hitstop":          int(snap.get("timing_hitstop") or 0),
            }

            # Preserve all attack-property annotations produced by the
            # background profiler. The earlier serializer copied only raw
            # actor fields, which dropped the move-definition active/source
            # flags and made the HUD show only the detected action.
            _bridge_attack_property_annotations(slot_payload, snap)

            # Native script is the authoritative Attack Property source.
            # Always overwrite any older pool/resolver/profile annotation for
            # the current action so this badge stays a clean native harvest.
            existing_attack_source = str(slot_payload.get("attack_property_display_source") or "")
            existing_attack_phases = [
                row for row in (slot_payload.get("attack_property_phases") or [])
                if isinstance(row, dict)
            ]
            projectile_only_profiler_result = bool(
                slot_payload.get("attack_property_projectiles")
                and not existing_attack_phases
                and existing_attack_source in {
                    "live_attack_actor", "live_attack_actor_latched",
                    "live_projectile_actor", "live_projectile_latched",
                }
            )
            if (
                not projectile_only_profiler_result
                and isinstance(native_definition, dict)
                and native_definition.get("status") == "OK"
                and _known_attack_property(attack_property)
            ):
                native_phases = [
                    dict(phase)
                    for phase in (native_definition.get("phases") or [])
                    if isinstance(phase, dict)
                ]
                projectile_rows = [
                    row for row in (slot_payload.get("attack_property_projectiles") or [])
                    if isinstance(row, dict)
                ]
                has_projectiles = bool(projectile_rows)
                has_live_projectiles = any(bool(row.get("projectile_live")) for row in projectile_rows)
                slot_payload.update({
                    "attack_property_display_active": True,
                    "attack_property_display_source": (
                        "native_script_and_live_attack_actor"
                        if has_live_projectiles
                        else ("native_script_and_last_attack_actor" if has_projectiles else "move_definition")
                    ),
                    "attack_property_packet_state": (
                        "CURRENT MOVE + LIVE ATTACK ACTOR"
                        if has_live_projectiles
                        else ("CURRENT MOVE + LAST ATTACK ACTOR" if has_projectiles else "CURRENT MOVE")
                    ),
                    "attack_property_packet_source": "move_definition",
                    "attack_property_packet_action_id": int(cur_anim or 0),
                    "attack_property_packet_action_name": str(mv_label_display or mv_label or ""),
                    "attack_property_live_actor": 0,
                    "attack_property_live_a": int(attack_property) & 0xFF,
                    "attack_property_live_b": int(attack_property_b or 0) & 0xFFFFFFFF,
                    "attack_property_definition_status": "OK",
                    "attack_property_definition_action_id": int(cur_anim or 0),
                    "attack_property_definition_action_source": "native_action_script",
                    "attack_property_phase_count": len(native_phases),
                    "attack_property_phases": native_phases,
                })

            payload[slot_label] = slot_payload

        annotate_damage_scaling_payload(payload, render_snap_by_slot)
        annotate_hitstun_scaling_payload(payload, render_snap_by_slot)
        payload["_punish_trainer"] = dict(punish_overlay or {})
        payload["_timing_engine"] = dict(timing_payload or {})

        self._queue_payload(payload)

    def check_proc(self) -> None:
        """Poll the subprocess handle; update active flag if it has exited."""
        if self._proc and self._proc.poll() is not None:
            self._proc = None
            self._active = False
    def close(self) -> None:
        self._realtime_sampler.remove_listener(self._on_realtime_input_sample)
        if self._owns_realtime_sampler:
            self._realtime_sampler.close()
        with self._input_bridge_condition:
            self._input_bridge_stop = True
            self._stun_bridge_stop = True
            self._input_bridge_condition.notify_all()
        if self._input_bridge_thread.is_alive():
            self._input_bridge_thread.join(timeout=1.0)
        if self._stun_bridge_thread.is_alive():
            self._stun_bridge_thread.join(timeout=1.0)
        with self._payload_condition:
            self._payload_writer_stop = True
            self._payload_condition.notify_all()
        if self._payload_writer_thread.is_alive():
            self._payload_writer_thread.join(timeout=1.0)

