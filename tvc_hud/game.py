from dataclasses import dataclass
from typing import Optional
from .constants import CHAR_NAMES

@dataclass
class FighterState:
    label: str
    team: int
    base: Optional[int] = None
    posy_off: Optional[int] = None

    char_id: Optional[int] = None
    name: str = "???"

    max: int = 0
    cur: int = 0
    aux: int = 0
    meter: Optional[int] = None

    x: Optional[float] = None
    y: Optional[float] = None

    last_hit: Optional[int] = None
    prev_last_hit: Optional[int] = None
    last_hp: Optional[int] = None

    def label_name(self) -> str:
        return f"{self.label} — {self.name}"

def name_for_char(char_id: Optional[int]) -> str:
    if char_id is None: return "???"
    return CHAR_NAMES.get(char_id, f"Unknown_ID_{char_id}")

def dist2(a: FighterState, b: FighterState) -> float:
    if a.x is None or a.y is None or b.x is None or b.y is None:
        return 9e9
    dx = (a.x - b.x); dy = (a.y - b.y)
    return dx*dx + dy*dy
