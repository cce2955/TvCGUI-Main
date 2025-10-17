# models.py
# Fighter snapshot + ComboState logic and small helpers.

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any

from constants import (
    OFF_MAX_HP, OFF_CUR_HP, OFF_AUX_HP, OFF_LAST_HIT, OFF_CHAR_ID,
    POSX_OFF, CHAR_NAMES,
)
from config import COMBO_TIMEOUT, HP_MIN_MAX, HP_MAX_MAX
from dolphin_io import rd32, rdf32


@dataclass
class Fighter:
    base: int
    max: int
    cur: int
    aux: Optional[int]
    id: Optional[int]
    name: str
    x: Optional[float]
    y: Optional[float]
    last: Optional[int]


def _looks_like_hp(maxhp: Optional[int], curhp: Optional[int], auxhp: Optional[int]) -> bool:
    if maxhp is None or curhp is None:
        return False
    if not (HP_MIN_MAX <= maxhp <= HP_MAX_MAX):
        return False
    if not (0 <= curhp <= maxhp):
        return False
    if auxhp is not None and not (0 <= auxhp <= maxhp):
        return False
    return True


def read_fighter(base: Optional[int], posy_off: Optional[int]) -> Optional[Fighter]:
    """Read a fighter block from memory and return a Fighter snapshot, or None if invalid."""
    if not base:
        return None

    max_hp = rd32(base + OFF_MAX_HP)
    cur_hp = rd32(base + OFF_CUR_HP)
    aux_hp = rd32(base + OFF_AUX_HP)

    if not _looks_like_hp(max_hp, cur_hp, aux_hp):
        return None

    cid  = rd32(base + OFF_CHAR_ID)
    name = CHAR_NAMES.get(cid, f"ID_{cid}") if cid is not None else "???"

    x = rdf32(base + POSX_OFF)
    y = rdf32(base + (posy_off or 0)) if posy_off is not None else None

    last = rd32(base + OFF_LAST_HIT)
    if last is None or last < 0 or last > 200_000:
        last = None

    return Fighter(
        base=base,
        max=int(max_hp),
        cur=int(cur_hp),
        aux=(int(aux_hp) if aux_hp is not None else None),
        id=(int(cid) if cid is not None else None),
        name=name,
        x=x,
        y=y,
        last=(int(last) if last is not None else None),
    )


def dist2(a: Optional[Fighter], b: Optional[Fighter]) -> float:
    """Squared distance between two fighters; returns inf if positions are missing."""
    if a is None or b is None:
        return float("inf")
    if a.x is None or a.y is None or b.x is None or b.y is None:
        return float("inf")
    dx = a.x - b.x
    dy = a.y - b.y
    return dx * dx + dy * dy


class ComboState:
    """Tracks an active combo window for a single victim (by victim base)."""
    def __init__(self) -> None:
        self.active: bool = False
        self.victim_base: Optional[int] = None
        self.victim_label: Optional[str] = None
        self.victim_name: Optional[str] = None
        self.attacker_label: Optional[str] = None
        self.attacker_name: Optional[str] = None
        self.attacker_move: Optional[str] = None
        self.team_guess: Optional[str] = None
        self.hits: int = 0
        self.total: int = 0
        self.hp_start: int = 0
        self.hp_end: int = 0
        self.start_t: float = 0.0
        self.last_t: float = 0.0

    def begin(
        self,
        t: float,
        victim_base: int,
        victim_label: str,
        victim_name: str,
        hp_before: int,
        attacker_label: Optional[str],
        attacker_name: Optional[str],
        attacker_move: Optional[str],
        team_guess: Optional[str],
    ) -> None:
        self.active = True
        self.victim_base = victim_base
        self.victim_label = victim_label
        self.victim_name = victim_name
        self.attacker_label = attacker_label
        self.attacker_name = attacker_name
        self.attacker_move = attacker_move
        self.team_guess = team_guess
        self.hits = 0
        self.total = 0
        self.hp_start = hp_before
        self.hp_end = hp_before
        self.start_t = t
        self.last_t = t

    def add_hit(self, t: float, dmg: int, hp_after: int) -> None:
        self.hits += 1
        self.total += dmg
        self.hp_end = hp_after
        self.last_t = t

    def expired(self, t: float) -> bool:
        return self.active and (t - self.last_t) > COMBO_TIMEOUT

    def end(self) -> Dict[str, Any]:
        self.active = False
        return {
            "t0": self.start_t,
            "t1": self.last_t,
            "dur": self.last_t - self.start_t,
            "victim_label": self.victim_label,
            "victim_name": self.victim_name,
            "attacker_label": self.attacker_label,
            "attacker_name": self.attacker_name,
            "attacker_move": self.attacker_move,
            "team_guess": self.team_guess,
            "hits": self.hits,
            "total": self.total,
            "hp_start": self.hp_start,
            "hp_end": self.hp_end,
        }
