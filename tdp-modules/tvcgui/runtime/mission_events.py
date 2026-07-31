"""High-frequency immutable event stream for Mission Mode.

The Dolphin sampler publishes small packets here. Mission Mode consumes the
resulting ordered events using independent cursors. The producer performs no
JSON or disk I/O and never calls MissionManager.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
import time
from typing import Callable, Iterable


EVENT_INPUT = "INPUT"
EVENT_ACTION = "ACTION"
EVENT_DAMAGE = "DAMAGE"
EVENT_HITSTUN_BEGIN = "HITSTUN_BEGIN"
EVENT_HITSTUN_END = "HITSTUN_END"
EVENT_COMBO_BEGIN = "COMBO_BEGIN"
EVENT_COMBO_CHANGE = "COMBO_CHANGE"
EVENT_COMBO_END = "COMBO_END"
EVENT_MEGACRASH_BEGIN = "MEGACRASH_BEGIN"
EVENT_MEGACRASH_END = "MEGACRASH_END"

# Mission-only reaction classification. +0x1210 remains authoritative when it
# is available in the sampler packet, while these native actions cover the
# reaction edge on the same sample that changes the action ID.
MISSION_REACTION_ACTIONS = frozenset({
    48, 49, 50, 51, 52, 53, 60, 61, 62, 64, 65, 66, 67, 73, 74, 75, 76,
    79, 80, 81, 82, 83, 88, 89, 90, 91, 92, 94, 95, 96, 97, 98, 101,
    102, 105, 106, 142, 448, 449,
    4562, 4565, 4568, 4571, 4573, 4608, 4609, 4610, 4611, 4613, 4614,
    4615, 4616, 4617, 4618, 4619, 4620, 4621, 4622, 4623, 4625, 4631,
})
MISSION_MEGACRASH_ACTIONS = frozenset({448})


@dataclass(frozen=True, slots=True)
class MissionEvent:
    """One immutable observation from the realtime sampler."""

    sequence: int
    sample_sequence: int
    timestamp_ns: int
    kind: str
    slot: str
    fighter_base: int = 0
    char_id: int = 0
    action_id: int = 0
    action_frame: int = 0
    held: int = 0
    pressed: int = 0
    released: int = 0
    current_hp: int = 0
    damage: int = 0
    hitstun_remaining: int = 0
    combo_count: int = 0

    @property
    def team(self) -> str:
        return "P1" if self.slot.startswith("P1") else "P2" if self.slot.startswith("P2") else ""

    def as_packet(self) -> dict:
        """Compatibility packet for legacy notation helpers during migration."""
        return {
            "seq": self.sequence,
            "sample_seq": self.sample_sequence,
            "sample_ns": self.timestamp_ns,
            "mission_event_kind": self.kind,
            "slot": self.slot,
            "base": self.fighter_base,
            "char_id": self.char_id,
            "action_id": self.action_id,
            "action_frame": self.action_frame,
            "held": self.held,
            "pressed": self.pressed if self.kind == EVENT_INPUT else 0,
            "released": self.released if self.kind == EVENT_INPUT else 0,
            "current_hp": self.current_hp,
            "damage": self.damage,
            "hitstun_remaining": self.hitstun_remaining,
            "combo_count": self.combo_count,
        }


@dataclass(slots=True)
class _SlotState:
    fighter_base: int = 0
    char_id: int = 0
    action_id: int = 0
    action_frame: int = 0
    held: int = 0
    current_hp: int = 0
    hitstun: bool = False
    megacrash: bool = False


class MissionEventStream:
    """Bounded thread-safe event ring fed by the 240 Hz sampler."""

    def __init__(self, capacity: int = 4096) -> None:
        self._capacity = max(256, int(capacity))
        self._events: deque[MissionEvent] = deque(maxlen=self._capacity)
        self._slot_state: dict[str, _SlotState] = {}
        self._sequence = 0
        self._combo_count: int | None = None
        self._lock = threading.RLock()

    @property
    def latest_sequence(self) -> int:
        with self._lock:
            return int(self._sequence)

    def _append(self, *, kind: str, slot: str, packet: dict, damage: int = 0) -> MissionEvent:
        self._sequence += 1
        event = MissionEvent(
            sequence=self._sequence,
            sample_sequence=int(packet.get("seq", 0) or 0),
            timestamp_ns=int(packet.get("sample_ns", 0) or time.monotonic_ns()),
            kind=str(kind),
            slot=str(slot),
            fighter_base=int(packet.get("base", 0) or 0),
            char_id=int(packet.get("char_id", 0) or 0),
            action_id=int(packet.get("action_id", 0) or 0) & 0x7FFF,
            action_frame=max(0, int(packet.get("action_frame", 0) or 0)),
            held=int(packet.get("held", 0) or 0) & 0xFFFF,
            pressed=int(packet.get("pressed", 0) or 0) & 0xFFFF,
            released=int(packet.get("released", 0) or 0) & 0xFFFF,
            current_hp=int(packet.get("current_hp", 0) or 0),
            damage=max(0, int(damage or packet.get("damage", 0) or 0)),
            hitstun_remaining=max(0, int(packet.get("hitstun_remaining", 0) or 0)),
            combo_count=max(0, int(packet.get("combo_count", 0) or 0)),
        )
        self._events.append(event)
        return event

    @staticmethod
    def _packet_hitstun(packet: dict) -> bool:
        action_id = int(packet.get("action_id", 0) or 0) & 0x7FFF
        return bool(
            int(packet.get("hitstun_remaining", 0) or 0) > 0
            or action_id in MISSION_REACTION_ACTIONS
        )

    def publish_sample(self, slot_label: str, packet: dict) -> None:
        """Translate one sampler packet into zero or more ordered events."""
        if not isinstance(packet, dict):
            return
        slot = str(slot_label or packet.get("slot") or "")
        if not slot:
            return

        with self._lock:
            previous = self._slot_state.get(slot)
            action_id = int(packet.get("action_id", 0) or 0) & 0x7FFF
            action_frame = max(0, int(packet.get("action_frame", 0) or 0))
            held = int(packet.get("held", 0) or 0) & 0xFFFF
            pressed = int(packet.get("pressed", 0) or 0) & 0xFFFF
            released = int(packet.get("released", 0) or 0) & 0xFFFF
            hp = int(packet.get("current_hp", 0) or 0)
            hitstun = self._packet_hitstun(packet)
            megacrash = action_id in MISSION_MEGACRASH_ACTIONS

            if previous is None:
                # Establish baselines without inventing a hit or combo. A real
                # button edge on the first packet is still a valid input event.
                if pressed or released:
                    self._append(kind=EVENT_INPUT, slot=slot, packet=packet)
                if action_id >= 0x100 and (action_frame <= 2 or bool(pressed & 0x0CF0)):
                    self._append(kind=EVENT_ACTION, slot=slot, packet=packet)
            else:
                if held != previous.held or pressed or released:
                    self._append(kind=EVENT_INPUT, slot=slot, packet=packet)
                action_restarted = bool(
                    action_id >= 0x100
                    and action_id == previous.action_id
                    and action_frame <= 2
                    and previous.action_frame > action_frame
                )
                if action_id >= 0x100 and (
                    action_id != previous.action_id or action_restarted
                ):
                    self._append(kind=EVENT_ACTION, slot=slot, packet=packet)
                if hp > 0 and previous.current_hp > 0 and hp < previous.current_hp:
                    self._append(
                        kind=EVENT_DAMAGE,
                        slot=slot,
                        packet=packet,
                        damage=previous.current_hp - hp,
                    )
                if hitstun and not previous.hitstun:
                    self._append(kind=EVENT_HITSTUN_BEGIN, slot=slot, packet=packet)
                elif previous.hitstun and not hitstun:
                    self._append(kind=EVENT_HITSTUN_END, slot=slot, packet=packet)
                if megacrash and not previous.megacrash:
                    self._append(kind=EVENT_MEGACRASH_BEGIN, slot=slot, packet=packet)
                elif previous.megacrash and not megacrash:
                    self._append(kind=EVENT_MEGACRASH_END, slot=slot, packet=packet)

            combo_count = max(0, int(packet.get("combo_count", 0) or 0))
            if self._combo_count is None:
                self._combo_count = combo_count
            elif combo_count != self._combo_count:
                if self._combo_count <= 0 < combo_count:
                    combo_kind = EVENT_COMBO_BEGIN
                elif self._combo_count > 0 and combo_count <= 0:
                    combo_kind = EVENT_COMBO_END
                else:
                    combo_kind = EVENT_COMBO_CHANGE
                self._append(kind=combo_kind, slot=slot, packet=packet)
                self._combo_count = combo_count

            self._slot_state[slot] = _SlotState(
                fighter_base=int(packet.get("base", 0) or 0),
                char_id=int(packet.get("char_id", 0) or 0),
                action_id=action_id,
                action_frame=action_frame,
                held=held,
                current_hp=hp,
                hitstun=hitstun,
                megacrash=megacrash,
            )

    # Listener alias used by HudOverlayManager.
    on_sample = publish_sample

    def events_since(
        self,
        cursor: int,
        slot_label: str | None = None,
    ) -> tuple[int, tuple[MissionEvent, ...]]:
        """Return all available events after cursor and the newest sequence.

        If the consumer fell behind the bounded ring, it receives every event
        still available. Consumers never mutate or remove shared events.
        """
        requested = max(0, int(cursor or 0))
        slot_filter = str(slot_label) if slot_label else None
        with self._lock:
            newest = int(self._sequence)
            events = tuple(
                event
                for event in self._events
                if event.sequence > requested
                and (slot_filter is None or event.slot == slot_filter)
            )
        return newest, events

    def snapshot(self) -> tuple[MissionEvent, ...]:
        with self._lock:
            return tuple(self._events)
