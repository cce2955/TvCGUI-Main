from __future__ import annotations

import collections
import threading
from typing import Any, Iterable


_SNAPSHOT_KEYS = {
    "base", "id", "name", "teamtag", "slotname", "slot_label",
    "f062", "f063", "f064", "f072", "x", "y",
    "attA", "attB", "timing_action_id", "move_id", "mv_id_display",
    "mv_label", "mv_label_display", "move_frame", "action_frame",
    "cur", "max", "aux", "meter",
    "attack_property_live_actor", "attack_property_live_damage",
    "attack_property_live_authored_damage", "attack_property_live_damage_calc_output",
    "attack_property_live_damage_calc_aux", "attack_property_native_damage_calc_complete",
    "attack_property_live_applied_damage", "attack_property_live_resolved_damage",
    "attack_property_live_resolved_aux", "attack_property_native_damage_complete",
    "attack_property_live_a", "attack_property_live_b",
    "attack_property_live_status20", "attack_property_live_phase_a",
    "attack_property_live_phase_b", "attack_property_live_victim_slot",
    "attack_property_live_action_frame", "attack_property_live_a_text",
    "attack_property_live_b_text", "attack_property_live_b_unknown",
    "attack_property_live_phase_a_text", "attack_property_live_phase_b_text",
    "attack_property_live_phase_b_unknown",
    "attack_property_live_owner_point_active",
    "attack_property_live_combo_lane_active",
    "attack_property_live_scaling_loss_per_hit",
    "attack_property_live_scaling_floor", "attack_property_live_scaling_track",
    "attack_property_packet_state", "attack_property_packet_source",
    "attack_property_packet_capture_frame", "attack_property_packet_age_frames",
    "attack_property_packet_action_id", "attack_property_packet_action_name",
    "attack_property_pool_manager", "attack_property_pool_free_head",
    "attack_property_pool_status", "attack_property_packet_count",
    "attack_property_display_active", "attack_property_display_source",
    "attack_property_resolver_hook_state", "attack_property_resolver_hook_error",
    "attack_property_resolver_lost_events", "attack_property_sampler_error",
    "damage_point_active", "damage_combo_lane_active", "damage_baroque_permission",
}

_ANNOTATION_PREFIXES = (
    "attack_property_",
    "meter_profile_",
    "red_health_",
    "reaction_",
)


class RuntimeProfilerScheduler:
    """Coalesce noncritical profilers onto one background telemetry lane.

    The HUD submits immutable shallow snapshots and never waits for profiling.
    When gameplay outruns telemetry, old pending frames are discarded and the
    worker consumes the newest state. Delta-based profilers still observe the
    total change between processed samples without blocking rendering.
    """

    def __init__(self, profilers: Iterable[Any], *, max_pending: int = 3) -> None:
        self._profilers = tuple(p for p in profilers if p is not None)
        self._pending = collections.deque(maxlen=max(1, int(max_pending)))
        self._condition = threading.Condition()
        self._latest: dict[str, dict[str, Any]] = {}
        self._latest_frame = -1
        self._stop = False
        self._thread = threading.Thread(
            target=self._run,
            name="TvCRuntimeTelemetry",
            daemon=True,
        )
        self._thread.start()

    @staticmethod
    def _copy_snapshots(snaps: dict[str, dict]) -> dict[str, dict]:
        return {
            str(slot): {key: value for key, value in snap.items() if key in _SNAPSHOT_KEYS}
            for slot, snap in (snaps or {}).items()
            if isinstance(snap, dict)
        }

    def submit(self, snaps: dict[str, dict], *, frame: int, now: float) -> None:
        item = (self._copy_snapshots(snaps), int(frame), float(now))
        with self._condition:
            self._pending.append(item)
            self._condition.notify()

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._pending and not self._stop:
                    self._condition.wait(timeout=0.25)
                if self._stop:
                    return
                snaps, frame, now = self._pending.pop()
                self._pending.clear()

            for profiler in self._profilers:
                try:
                    profiler.update(snaps, frame=frame, now=now)
                except Exception:
                    continue

            annotations: dict[str, dict[str, Any]] = {}
            for slot, snap in snaps.items():
                fields = {
                    key: value
                    for key, value in snap.items()
                    if key.startswith(_ANNOTATION_PREFIXES)
                }
                if fields:
                    annotations[slot] = fields
            with self._condition:
                self._latest = annotations
                self._latest_frame = frame

    def apply_latest(self, snaps: dict[str, dict]) -> int:
        with self._condition:
            latest = self._latest
            frame = self._latest_frame
        for slot, fields in latest.items():
            snap = (snaps or {}).get(slot)
            if isinstance(snap, dict):
                snap.update(fields)
        return int(frame)

    def close(self, timeout: float = 1.5) -> None:
        with self._condition:
            self._stop = True
            self._pending.clear()
            self._condition.notify_all()
        if self._thread.is_alive():
            self._thread.join(timeout=max(0.0, float(timeout)))
