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
EVENT_REACTION_BEGIN = "REACTION_BEGIN"
EVENT_REACTION_END = "REACTION_END"
EVENT_BLOCKSTUN_BEGIN = "BLOCKSTUN_BEGIN"
EVENT_BLOCKSTUN_END = "BLOCKSTUN_END"
EVENT_COMBO_BEGIN = "COMBO_BEGIN"
EVENT_COMBO_CHANGE = "COMBO_CHANGE"
EVENT_COMBO_END = "COMBO_END"
EVENT_MEGACRASH_BEGIN = "MEGACRASH_BEGIN"
EVENT_MEGACRASH_END = "MEGACRASH_END"
EVENT_JUMP = "JUMP"
EVENT_RECOVERY_IDLE = "RECOVERY_IDLE"

# Mission-only reaction classification. +0x1210 remains authoritative when it
# is available in the sampler packet, while these native actions cover the
# reaction edge on the same sample that changes the action ID.
MISSION_REACTION_ACTIONS = frozenset({
    # Native unavailable/reaction states from reaction_state_profiler.py.
    # Block, wakeup, air-recovery and KO states are intentionally excluded:
    # those are recovery boundaries rather than combo-continuation windows.
    55, 56, 57, 58, 59, 60, 61, 62, 64, 65, 66, 67, 70, 73, 74, 75, 76,
    77, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93, 94,
    95, 96, 97, 98, 99, 101, 102, 105, 106, 108, 109, 124, 128, 129, 130,
    132, 142, 161, 449,
    # Giant/special victim reaction states already observed by Megacrash.
    4562, 4565, 4568, 4571, 4573, 4608, 4609, 4610, 4611, 4613, 4614,
    4615, 4616, 4617, 4618, 4619, 4620, 4621, 4622, 4623, 4625, 4631,
})
MISSION_MEGACRASH_ACTIONS = frozenset({448})
# Native passive states used when the game accepts a normal/super jump.
# These are intentionally emitted separately from move ACTION events, which
# only cover authored attack actions >= 0x100.
MISSION_JUMP_ACTIONS = frozenset({19, 20, 21, 22, 28, 29, 31})
MISSION_IDLE_ACTIONS = frozenset({1})


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
    untech_remaining: int = 0
    blockstun_remaining: int = 0
    reaction_timer_remaining: int = 0
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
            "untech_remaining": self.untech_remaining,
            "blockstun_remaining": self.blockstun_remaining,
            "reaction_timer_remaining": self.reaction_timer_remaining,
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
    blockstun: bool = False
    special_reaction: bool = False
    megacrash: bool = False
    recovery_idle: bool = False


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
            untech_remaining=max(0, int(packet.get("untech_remaining", 0) or 0)),
            blockstun_remaining=max(0, int(packet.get("blockstun_remaining", 0) or 0)),
            reaction_timer_remaining=max(0, int(packet.get("reaction_timer_remaining", 0) or 0)),
            combo_count=max(0, int(packet.get("combo_count", 0) or 0)),
        )
        self._events.append(event)
        return event

    @staticmethod
    def _packet_hitstun(packet: dict) -> bool:
        """Mirror the HUD's authoritative reaction clock.

        Air routes use +0x1220 (untech / AIR HS) while grounded routes use
        +0x1210.  Mission failure must follow the same native clocks the user
        sees in the STUN rail, not a lagging combo counter or reaction action.
        """
        return bool(
            int(packet.get("untech_remaining", 0) or 0) > 0
            or int(packet.get("hitstun_remaining", 0) or 0) > 0
        )

    @staticmethod
    def _packet_recovery_idle(packet: dict) -> bool:
        """Return true only when the fighter has genuinely settled back to idle.

        This is a final mission-route failsafe, not the primary recovery clock.
        It deliberately requires all native unavailable clocks/states to be
        clear so an idle-looking action cannot reset a route during hitstop,
        blockstun, wall bounce, stagger, knockdown, or another bridged reaction.
        """
        action_id = int(packet.get("action_id", 0) or 0) & 0x7FFF
        if action_id not in MISSION_IDLE_ACTIONS:
            return False
        if MissionEventStream._packet_hitstun(packet):
            return False
        if max(0, int(packet.get("blockstun_remaining", 0) or 0)) > 0:
            return False
        if MissionEventStream._packet_special_reaction(packet):
            return False
        if action_id in MISSION_MEGACRASH_ACTIONS:
            return False
        return True

    @staticmethod
    def _packet_special_reaction(packet: dict) -> bool:
        """Return whether the victim is unavailable in a non-STUN reaction.

        +0x1228 is a confirmed secondary reaction countdown for one native
        reaction family. For wall bounce, hard knockdown, stagger, crumple,
        launcher/bounce and related families that do not populate that timer,
        the native reaction action itself is the authoritative gate.
        """
        action_id = int(packet.get("action_id", 0) or 0) & 0x7FFF
        timer = max(0, int(packet.get("reaction_timer_remaining", 0) or 0))
        # While +0x1210/+0x1220 is populated, the ordinary STUN lane already
        # owns continuity. Only promote an action-family reaction when those
        # clocks are empty, which is exactly the wall-bounce/KD/stagger hole.
        native_stun = MissionEventStream._packet_hitstun(packet)
        return bool(timer > 0 or (not native_stun and action_id in MISSION_REACTION_ACTIONS))

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
            blockstun = max(0, int(packet.get("blockstun_remaining", 0) or 0)) > 0
            special_reaction = self._packet_special_reaction(packet)
            megacrash = action_id in MISSION_MEGACRASH_ACTIONS
            recovery_idle = self._packet_recovery_idle(packet)

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
                action_changed = action_id != previous.action_id
                if action_id >= 0x100 and (
                    action_changed or action_restarted
                ):
                    self._append(kind=EVENT_ACTION, slot=slot, packet=packet)
                if (
                    action_changed
                    and action_id in MISSION_JUMP_ACTIONS
                    and previous.action_id not in MISSION_JUMP_ACTIONS
                ):
                    self._append(kind=EVENT_JUMP, slot=slot, packet=packet)
                if hp > 0 and previous.current_hp > 0 and hp < previous.current_hp:
                    self._append(
                        kind=EVENT_DAMAGE,
                        slot=slot,
                        packet=packet,
                        damage=previous.current_hp - hp,
                    )
                if special_reaction and not previous.special_reaction:
                    self._append(kind=EVENT_REACTION_BEGIN, slot=slot, packet=packet)
                elif previous.special_reaction and not special_reaction:
                    self._append(kind=EVENT_REACTION_END, slot=slot, packet=packet)
                if hitstun and not previous.hitstun:
                    self._append(kind=EVENT_HITSTUN_BEGIN, slot=slot, packet=packet)
                elif previous.hitstun and not hitstun:
                    self._append(kind=EVENT_HITSTUN_END, slot=slot, packet=packet)
                if blockstun and not previous.blockstun:
                    self._append(kind=EVENT_BLOCKSTUN_BEGIN, slot=slot, packet=packet)
                elif previous.blockstun and not blockstun:
                    self._append(kind=EVENT_BLOCKSTUN_END, slot=slot, packet=packet)
                if megacrash and not previous.megacrash:
                    self._append(kind=EVENT_MEGACRASH_BEGIN, slot=slot, packet=packet)
                elif previous.megacrash and not megacrash:
                    self._append(kind=EVENT_MEGACRASH_END, slot=slot, packet=packet)
                if recovery_idle and not previous.recovery_idle:
                    self._append(kind=EVENT_RECOVERY_IDLE, slot=slot, packet=packet)

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
                blockstun=blockstun,
                special_reaction=special_reaction,
                megacrash=megacrash,
                recovery_idle=recovery_idle,
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
