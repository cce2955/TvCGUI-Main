from __future__ import annotations

import csv
import os
import struct
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from tkinter import filedialog, ttk
import tkinter as tk
from typing import Any

from tvcgui.core.tk_host import tk_call
try:
    from tvcgui.features.combat.moves import CHAR_ID_CORRECTION, move_label_for
except Exception:
    CHAR_ID_CORRECTION = {}

    def move_label_for(action_id, char_id, move_map, global_map):
        try:
            char_rows = move_map.get(int(char_id), {}) if isinstance(move_map, dict) else {}
            if int(action_id) in char_rows:
                return char_rows[int(action_id)]
        except Exception:
            pass
        try:
            if isinstance(global_map, dict) and int(action_id) in global_map:
                return global_map[int(action_id)]
        except Exception:
            pass
        return f"Action 0x{int(action_id) & 0xFFFF:04X}"
from tvcgui.features.frame_data.widgets import apply_titlebar_icon
try:
    from tvcgui.features.combat.timing_engine import TIMING_ENGINE
except Exception:
    TIMING_ENGINE = None

try:
    from tvcgui.platform.dolphin import rd8, rd32
except Exception:
    rd8 = None
    rd32 = None


SLOT_POINTERS = {
    "P1-C1": 0x803C9FCC,
    "P1-C2": 0x803C9FDC,
    "P2-C1": 0x803C9FD4,
    "P2-C2": 0x803C9FE4,
}

OFF_CHAR_ID = 0x0014
OFF_CUR_HP = 0x0028
OFF_STATE_063 = 0x0063
OFF_ACTION_ID = 0x01E8
OFF_ACTION_FRAME_FLOAT = 0x01D8
OFF_FRAME_A = 0x021C
OFF_FRAME_B = 0x0220
OFF_BLOCKSTUN_REMAINING = 0x1204
OFF_RESOLVED_STUN = 0x1210
OFF_STUN_REMAINING = 0x1228
OFF_FREEZE_A = 0x211C
OFF_FREEZE_B = 0x2120
STUN_SCAN_OFFSETS = tuple(range(0x1200, 0x1244, 4))

POLL_MS = 8
PREBUFFER_SAMPLES = 18
MAX_CAPTURE_SECONDS = 4.0
POST_CONTACT_MIN_SECONDS = 0.10
BLOCK_SCAN_POST_CONTACT_SECONDS = 0.45
POST_CONTACT_MAX_SECONDS = 2.5
MAX_HISTORY = 80

_ACTIVE_WINDOW: "TimingProbeWindow | None" = None


@dataclass
class FighterTimingState:
    slot: str
    base: int
    char_id: int
    action_id: int
    action_frame: int
    frame_a: int
    frame_b: int
    resolved_stun: int
    stun_remaining: int
    freeze_a: int
    freeze_b: int
    state_063: int
    hp: int
    blockstun_remaining: int = 0
    stun_scan: tuple[int, ...] = field(default_factory=tuple)


@dataclass
class TimingSample:
    elapsed_ms: float
    attacker: FighterTimingState
    defender: FighterTimingState


@dataclass
class TimingCapture:
    index: int
    timestamp: str
    expected: str
    observed: str
    result: str
    attacker_slot: str
    defender_slot: str
    char_id: int
    action_id: int
    action_name: str
    contact_ms: float | None
    notes: str
    samples: list[TimingSample] = field(default_factory=list)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _read_u32(addr: int, default: int = 0) -> int:
    if rd32 is None:
        return int(default) & 0xFFFFFFFF
    try:
        value = rd32(int(addr))
    except Exception:
        value = None
    if value is None:
        return int(default) & 0xFFFFFFFF
    return int(value) & 0xFFFFFFFF


def _read_u8(addr: int, default: int = 0) -> int:
    if rd8 is None:
        return int(default) & 0xFF
    try:
        value = rd8(int(addr))
    except Exception:
        value = None
    if value is None:
        return int(default) & 0xFF
    return int(value) & 0xFF


def _valid_base(value: Any) -> bool:
    base = _as_int(value, 0)
    return 0x90000000 <= base < 0x94000000 and (base & 0x3) == 0


def _decode_action_frame(word: int) -> int:
    try:
        value = struct.unpack(">f", struct.pack(">I", int(word) & 0xFFFFFFFF))[0]
    except Exception:
        return 0
    if value != value or value < 0.0 or value > 2000.0:
        return 0
    return max(0, int(round(value - 1.0)))


def _character_name(char_id: int) -> str:
    cid = int(char_id)
    for name, mapped in CHAR_ID_CORRECTION.items():
        try:
            if int(mapped) == cid:
                return str(name)
        except Exception:
            continue
    return f"Character {cid}" if cid >= 0 else "Unknown"


def classify_contact(previous: FighterTimingState | None, current: FighterTimingState) -> str | None:
    """Classify a defender-side contact from explicit state or HP loss."""
    if int(current.state_063) == 16:
        return "block"
    if int(current.state_063) == 4:
        return "hit"
    if previous is not None and int(previous.hp) > 0 and int(current.hp) < int(previous.hp):
        return "hit"
    return None


def bilateral_impact_started(
    previous_attacker: FighterTimingState | None,
    current_attacker: FighterTimingState,
    previous_defender: FighterTimingState | None,
    current_defender: FighterTimingState,
) -> bool:
    """Detect the shared impact-freeze edge seen on both fighters."""
    current = (
        max(int(current_attacker.freeze_a), int(current_attacker.freeze_b)) > 0
        and max(int(current_defender.freeze_a), int(current_defender.freeze_b)) > 0
    )
    if not current:
        return False
    if previous_attacker is None or previous_defender is None:
        return True
    previous = (
        max(int(previous_attacker.freeze_a), int(previous_attacker.freeze_b)) > 0
        and max(int(previous_defender.freeze_a), int(previous_defender.freeze_b)) > 0
    )
    return not previous


def resolve_impact_contact(samples: list[TimingSample], fallback: str | None = None) -> str | None:
    """Resolve a shared impact into hit or block after the counters settle.

    For the controlled 5A timing pass, HP loss is a hit. Shared impact
    freeze with unchanged defender HP is a block. Explicit state results
    remain authoritative when available.
    """
    if fallback in {"hit", "block"}:
        return fallback
    hp_values = [int(sample.defender.hp) for sample in samples if int(sample.defender.hp) > 0]
    if hp_values and min(hp_values) < max(hp_values):
        return "hit"
    if fallback == "impact":
        return "block"
    return fallback


def _first_nonzero_ms(samples: list[TimingSample], role: str, field_name: str) -> float | None:
    for sample in samples:
        state = sample.attacker if role == "attacker" else sample.defender
        if _as_int(getattr(state, field_name, 0), 0) > 0:
            return float(sample.elapsed_ms)
    return None


def _max_field(samples: list[TimingSample], role: str, field_name: str) -> int:
    values = []
    for sample in samples:
        state = sample.attacker if role == "attacker" else sample.defender
        values.append(_as_int(getattr(state, field_name, 0), 0))
    return max(values, default=0)


def _scan_value(state: FighterTimingState, offset: int) -> int:
    try:
        index = STUN_SCAN_OFFSETS.index(int(offset))
    except ValueError:
        return 0
    try:
        return _as_int(state.stun_scan[index], 0)
    except Exception:
        return 0


def _scan_series(capture: TimingCapture, offset: int, role: str = "defender") -> list[tuple[float, int]]:
    rows: list[tuple[float, int]] = []
    for sample in capture.samples:
        state = sample.attacker if role == "attacker" else sample.defender
        rows.append((float(sample.elapsed_ms), _scan_value(state, offset)))
    return rows


def _scan_max(capture: TimingCapture | None, offset: int, role: str = "defender") -> int:
    if capture is None:
        return 0
    return max((value for _ms, value in _scan_series(capture, offset, role)), default=0)


def _scan_first_nonzero(capture: TimingCapture | None, offset: int, role: str = "defender") -> float | None:
    if capture is None:
        return None
    for elapsed_ms, value in _scan_series(capture, offset, role):
        if value > 0:
            return elapsed_ms
    return None


def _scan_countdown_steps(capture: TimingCapture | None, offset: int, role: str = "defender") -> int:
    if capture is None:
        return 0
    contact_ms = float(capture.contact_ms or 0.0)
    values = [value for elapsed_ms, value in _scan_series(capture, offset, role) if elapsed_ms >= contact_ms and value >= 0]
    steps = 0
    previous = None
    for value in values:
        if previous is not None and previous > 0 and 0 <= value < previous:
            steps += 1
        previous = value
    return steps


def select_scan_baselines(captures: list[TimingCapture]) -> dict[str, TimingCapture]:
    """Return the newest matching whiff, hit, and block captures for one move and slot pair."""
    if not captures:
        return {}
    anchor = captures[-1]
    selected: dict[str, TimingCapture] = {}
    for capture in reversed(captures):
        if capture.action_id != anchor.action_id:
            continue
        if capture.attacker_slot != anchor.attacker_slot or capture.defender_slot != anchor.defender_slot:
            continue
        observed = str(capture.observed).lower()
        if observed in {"whiff", "hit", "block"} and observed not in selected:
            selected[observed] = capture
        if len(selected) == 3:
            break
    return selected


def rank_blockstun_candidates(captures: list[TimingCapture]) -> list[dict[str, int | float | str | None]]:
    """Rank +0x1200..+0x1240 fields that behave like a defender blockstun countdown."""
    baselines = select_scan_baselines(captures)
    whiff = baselines.get("whiff")
    hit = baselines.get("hit")
    block = baselines.get("block")
    rows: list[dict[str, int | float | str | None]] = []
    for offset in STUN_SCAN_OFFSETS:
        whiff_max = _scan_max(whiff, offset)
        hit_max = _scan_max(hit, offset)
        block_max = _scan_max(block, offset)
        countdown = _scan_countdown_steps(block, offset)
        first_block = _scan_first_nonzero(block, offset)
        score = 0
        notes: list[str] = []
        if whiff is not None:
            if whiff_max == 0:
                score += 5
            else:
                score -= 6
                notes.append("active on whiff")
        if block is not None:
            if block_max > 0:
                score += 8
                notes.append("nonzero on block")
                if block_max <= 600:
                    score += 2
                else:
                    score -= 2
                    notes.append("large value")
            else:
                score -= 8
        if countdown > 0:
            score += min(8, countdown * 2)
            notes.append(f"{countdown} countdown steps")
        if hit is not None:
            if hit_max == 0 and block_max > 0:
                score += 4
                notes.append("block-specific")
            elif hit_max > 0 and block_max > 0 and hit_max != block_max:
                score += 2
                notes.append("hit/block differ")
            elif hit_max == block_max and block_max > 0:
                notes.append("shared contact field")
        rows.append(
            {
                "offset": offset,
                "score": score,
                "whiff_max": whiff_max,
                "hit_max": hit_max,
                "block_max": block_max,
                "countdown": countdown,
                "first_block_ms": first_block,
                "notes": ", ".join(notes) if notes else "no useful pattern",
            }
        )
    rows.sort(key=lambda row: (int(row["score"]), int(row["countdown"]), int(row["block_max"])), reverse=True)
    return rows


def summarize_blockstun_scan(captures: list[TimingCapture]) -> str:
    baselines = select_scan_baselines(captures)
    missing = [name for name in ("whiff", "hit", "block") if name not in baselines]
    lines = [
        "BLOCKSTUN FIELD SCAN",
        "Defender neighborhood: +0x1200 through +0x1240, sampled every 4 bytes.",
        "",
    ]
    if captures:
        anchor = captures[-1]
        lines.append(f"Move: {anchor.action_name} [0x{anchor.action_id:04X}]  |  {anchor.attacker_slot} vs {anchor.defender_slot}")
    if missing:
        lines.append("Missing baselines: " + ", ".join(name.upper() for name in missing))
        lines.append("Capture one clean Whiff, Hit, and Block with the same move and fighter slots.")
        lines.append("")
    if not baselines:
        return "\n".join(lines)
    lines.append("Baselines: " + "  |  ".join(f"{name.upper()} #{baselines[name].index}" for name in ("whiff", "hit", "block") if name in baselines))
    if missing:
        return "\n".join(lines)
    lines.extend([
        "",
        "Rank  Offset   Score  Whiff  Hit  Block  Down  First block   Interpretation",
        "----  -------  -----  -----  ---  -----  ----  ------------  --------------",
    ])
    for rank, row in enumerate(rank_blockstun_candidates(captures)[:10], start=1):
        first = row["first_block_ms"]
        first_text = "never" if first is None else f"{float(first):.1f} ms"
        lines.append(
            f"{rank:>4}  +0x{int(row['offset']):04X}  {int(row['score']):>5}  "
            f"{int(row['whiff_max']):>5}  {int(row['hit_max']):>3}  {int(row['block_max']):>5}  "
            f"{int(row['countdown']):>4}  {first_text:>12}  {row['notes']}"
        )
    if not missing:
        top = rank_blockstun_candidates(captures)[0]
        lines.extend([
            "",
            f"Best current candidate: +0x{int(top['offset']):04X}",
            "A strong result is zero on whiff, nonzero on block, and visibly counting down after contact.",
        ])
    return "\n".join(lines)


def summarize_capture(capture: TimingCapture) -> str:
    samples = list(capture.samples)
    fields = (
        ("resolved_stun", "+1210"),
        ("stun_remaining", "+1228"),
        ("freeze_a", "+211C"),
        ("freeze_b", "+2120"),
    )
    lines = [
        f"Capture {capture.index}: {capture.action_name} [0x{capture.action_id:04X}]",
        f"Expected {capture.expected.upper()}  |  Observed {capture.observed.upper()}  |  {capture.result}",
        f"Attacker {capture.attacker_slot}  |  Defender {capture.defender_slot}",
        f"Contact: {'none' if capture.contact_ms is None else f'{capture.contact_ms:.1f} ms from move start'}",
        "",
        "Candidate field summary",
    ]
    for role in ("attacker", "defender"):
        lines.append(role.upper())
        for field_name, label in fields:
            maximum = _max_field(samples, role, field_name)
            first = _first_nonzero_ms(samples, role, field_name)
            first_text = "never" if first is None else f"{first:.1f} ms"
            lines.append(f"  {label}: max {maximum}, first nonzero {first_text}")

    attacker_freeze_pairs = 0
    defender_freeze_pairs = 0
    for before, after in zip(samples, samples[1:]):
        if before.attacker.frame_a == after.attacker.frame_a and before.attacker.frame_b != after.attacker.frame_b:
            attacker_freeze_pairs += 1
        if before.defender.frame_a == after.defender.frame_a and before.defender.frame_b != after.defender.frame_b:
            defender_freeze_pairs += 1
    lines.extend(
        [
            "",
            "Frame-counter behavior",
            f"  Attacker frame A held while frame B moved: {attacker_freeze_pairs} observed steps",
            f"  Defender frame A held while frame B moved: {defender_freeze_pairs} observed steps",
        ]
    )
    if capture.notes:
        lines.extend(["", capture.notes])
    return "\n".join(lines)


class TimingProbeWindow:
    def __init__(
        self,
        parent: tk.Misc,
        move_map: dict[int, dict[int, str]] | None,
        global_map: dict[int, str] | None,
        initial_attacker: str = "P1-C1",
    ) -> None:
        self.parent = parent
        self.move_map = move_map if isinstance(move_map, dict) else {}
        self.global_map = global_map if isinstance(global_map, dict) else {}
        self.window = tk.Toplevel(parent)
        apply_titlebar_icon(self.window, parent)
        self.window.title("Timing Monitor")
        self.window.geometry("1220x800")
        self.window.minsize(930, 610)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.configure(bg="#08111d")

        initial_attacker = initial_attacker if initial_attacker in SLOT_POINTERS else "P1-C1"
        default_defender = "P2-C1" if initial_attacker.startswith("P1") else "P1-C1"
        self.attacker_var = tk.StringVar(master=self.window, value=initial_attacker)
        self.defender_var = tk.StringVar(master=self.window, value=default_defender)
        self.status_var = tk.StringVar(master=self.window, value="Choose Whiff, Hit, or Block, then perform the next move.")
        self.live_attacker_var = tk.StringVar(master=self.window, value="Waiting for attacker...")
        self.live_defender_var = tk.StringVar(master=self.window, value="Waiting for defender...")
        self.arm_var = tk.StringVar(master=self.window, value="NOT ARMED")
        self.analysis_var = tk.StringVar(master=self.window, value="Select a completed capture for analysis.")
        self.live_result_var = tk.StringVar(master=self.window, value="No completed timing interaction yet.")

        self.captures: list[TimingCapture] = []
        self._capture_by_item: dict[str, TimingCapture] = {}
        self._prebuffer: deque[TimingSample] = deque(maxlen=PREBUFFER_SAMPLES)
        self._armed_expected: str | None = None
        self._armed_at = 0.0
        self._baseline_action = 0
        self._active: dict[str, Any] | None = None
        self._last_attacker: FighterTimingState | None = None
        self._last_defender: FighterTimingState | None = None
        self._last_signature: tuple[Any, ...] | None = None
        self._after_id: str | None = None
        self._closing = False

        self._configure_styles()
        self._build_ui()
        self._schedule_poll()

    def _configure_styles(self) -> None:
        style = ttk.Style(self.window)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TP.Root.TFrame", background="#08111d")
        style.configure("TP.Card.TFrame", background="#0d1a29", borderwidth=1, relief="solid")
        style.configure("TP.Header.TLabel", background="#0d1a29", foreground="#f2f7ff", font=("Segoe UI Semibold", 17))
        style.configure("TP.Sub.TLabel", background="#0d1a29", foreground="#8fa6bb", font=("Segoe UI", 10))
        style.configure("TP.Section.TLabel", background="#0d1a29", foreground="#59d2ff", font=("Segoe UI Semibold", 10))
        style.configure("TP.Value.TLabel", background="#0d1a29", foreground="#e8f4ff", font=("Segoe UI Semibold", 10))
        style.configure("TP.Muted.TLabel", background="#0d1a29", foreground="#8298ac", font=("Segoe UI", 9))
        style.configure("TP.TButton", background="#16283a", foreground="#e8f4ff", bordercolor="#29465f", padding=(11, 8), font=("Segoe UI Semibold", 9))
        style.map("TP.TButton", background=[("active", "#1d3950"), ("pressed", "#102435")])
        style.configure("TP.Hit.TButton", background="#174b68", foreground="#ffffff", bordercolor="#39bce9", padding=(13, 8), font=("Segoe UI Semibold", 9))
        style.map("TP.Hit.TButton", background=[("active", "#1d6385"), ("pressed", "#123f57")])
        style.configure("TP.Block.TButton", background="#4a3867", foreground="#ffffff", bordercolor="#a888df", padding=(13, 8), font=("Segoe UI Semibold", 9))
        style.map("TP.Block.TButton", background=[("active", "#5d4780"), ("pressed", "#382b50")])
        style.configure("TP.Whiff.TButton", background="#4b4425", foreground="#ffffff", bordercolor="#d8be62", padding=(13, 8), font=("Segoe UI Semibold", 9))
        style.map("TP.Whiff.TButton", background=[("active", "#62582d"), ("pressed", "#38321c")])
        style.configure("TP.TCombobox", fieldbackground="#0a1623", background="#16283a", foreground="#eef7ff", arrowcolor="#59d2ff", bordercolor="#29465f", padding=6)
        style.configure("TP.Treeview", background="#091521", fieldbackground="#091521", foreground="#dce8f3", rowheight=27, bordercolor="#20384d", font=("Segoe UI", 9))
        style.map("TP.Treeview", background=[("selected", "#164766")], foreground=[("selected", "#ffffff")])
        style.configure("TP.Treeview.Heading", background="#102437", foreground="#91dcfa", bordercolor="#29465f", font=("Segoe UI Semibold", 9), padding=(6, 7))
        style.configure("TP.TNotebook", background="#0d1a29", borderwidth=0)
        style.configure("TP.TNotebook.Tab", background="#102437", foreground="#9fb4c6", padding=(14, 8), font=("Segoe UI Semibold", 9))
        style.map("TP.TNotebook.Tab", background=[("selected", "#174b68"), ("active", "#15354b")], foreground=[("selected", "#ffffff")])

    def _build_ui(self) -> None:
        root = ttk.Frame(self.window, style="TP.Root.TFrame", padding=12)
        root.pack(fill="both", expand=True)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(3, weight=1)

        header = ttk.Frame(root, style="TP.Card.TFrame", padding=(16, 14))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.grid_columnconfigure(0, weight=1)
        ttk.Label(header, text="TIMING MONITOR / RESEARCH PROBE", style="TP.Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Live hitstop, hitstun, blockstun, and observed advantage, with the controlled research probe preserved below.",
            style="TP.Sub.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Label(header, textvariable=self.arm_var, style="TP.Section.TLabel").grid(row=0, column=1, rowspan=2, sticky="e", padx=(18, 0))

        controls = ttk.Frame(root, style="TP.Card.TFrame", padding=(14, 12))
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        controls.grid_columnconfigure(11, weight=1)
        ttk.Label(controls, text="Attacker", style="TP.Section.TLabel").grid(row=0, column=0, sticky="w")
        attacker_combo = ttk.Combobox(controls, textvariable=self.attacker_var, values=list(SLOT_POINTERS), state="readonly", width=10, style="TP.TCombobox")
        attacker_combo.grid(row=1, column=0, sticky="w", pady=(5, 0), padx=(0, 8))
        attacker_combo.bind("<<ComboboxSelected>>", self._slots_changed)
        ttk.Label(controls, text="Defender", style="TP.Section.TLabel").grid(row=0, column=1, sticky="w")
        defender_combo = ttk.Combobox(controls, textvariable=self.defender_var, values=list(SLOT_POINTERS), state="readonly", width=10, style="TP.TCombobox")
        defender_combo.grid(row=1, column=1, sticky="w", pady=(5, 0), padx=(0, 12))
        defender_combo.bind("<<ComboboxSelected>>", self._slots_changed)

        ttk.Button(controls, text="Arm Whiff", command=lambda: self.arm("whiff"), style="TP.Whiff.TButton").grid(row=1, column=2, padx=(0, 6), pady=(5, 0))
        ttk.Button(controls, text="Arm Hit", command=lambda: self.arm("hit"), style="TP.Hit.TButton").grid(row=1, column=3, padx=(0, 6), pady=(5, 0))
        ttk.Button(controls, text="Arm Block", command=lambda: self.arm("block"), style="TP.Block.TButton").grid(row=1, column=4, padx=(0, 6), pady=(5, 0))
        ttk.Button(controls, text="Stop", command=self.stop, style="TP.TButton").grid(row=1, column=5, padx=(0, 6), pady=(5, 0))
        ttk.Button(controls, text="Clear", command=self.clear, style="TP.TButton").grid(row=1, column=6, padx=(0, 6), pady=(5, 0))
        ttk.Button(controls, text="Copy", command=self.copy_selected, style="TP.TButton").grid(row=1, column=7, padx=(0, 6), pady=(5, 0))
        ttk.Button(controls, text="Save CSV", command=self.save_csv, style="TP.TButton").grid(row=1, column=8, padx=(0, 6), pady=(5, 0))
        ttk.Button(controls, text="Copy Scan", command=self.copy_scan, style="TP.TButton").grid(row=1, column=9, padx=(0, 8), pady=(5, 0))
        ttk.Label(controls, textvariable=self.status_var, style="TP.Muted.TLabel").grid(row=1, column=11, sticky="e", padx=(12, 0), pady=(5, 0))

        live = ttk.Frame(root, style="TP.Card.TFrame", padding=(14, 11))
        live.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        live.grid_columnconfigure(0, weight=1)
        live.grid_columnconfigure(1, weight=1)
        ttk.Label(live, text="ATTACKER LIVE", style="TP.Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(live, text="DEFENDER LIVE", style="TP.Section.TLabel").grid(row=0, column=1, sticky="w", padx=(20, 0))
        ttk.Label(live, textvariable=self.live_attacker_var, style="TP.Value.TLabel", justify="left").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Label(live, textvariable=self.live_defender_var, style="TP.Value.TLabel", justify="left").grid(row=1, column=1, sticky="w", padx=(20, 0), pady=(5, 0))
        ttk.Label(live, text="LATEST OBSERVED RESULT", style="TP.Section.TLabel").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Label(live, textvariable=self.live_result_var, style="TP.Value.TLabel", justify="left").grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        split = ttk.Panedwindow(root, orient="vertical")
        split.grid(row=3, column=0, sticky="nsew")
        history_card = ttk.Frame(split, style="TP.Card.TFrame", padding=8)
        detail_card = ttk.Frame(split, style="TP.Card.TFrame", padding=8)
        split.add(history_card, weight=2)
        split.add(detail_card, weight=3)
        history_card.grid_columnconfigure(0, weight=1)
        history_card.grid_rowconfigure(0, weight=1)

        columns = ("index", "time", "expected", "observed", "move", "attacker", "defender", "resolved", "remaining", "freeze_a", "freeze_b", "result")
        self.capture_tree = ttk.Treeview(history_card, columns=columns, show="headings", style="TP.Treeview", selectmode="browse")
        yscroll = ttk.Scrollbar(history_card, orient="vertical", command=self.capture_tree.yview)
        xscroll = ttk.Scrollbar(history_card, orient="horizontal", command=self.capture_tree.xview)
        self.capture_tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.capture_tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        headings = {
            "index": "#", "time": "Time", "expected": "Expected", "observed": "Observed", "move": "Move",
            "attacker": "Attacker", "defender": "Defender", "resolved": "+1210 D", "remaining": "+1228 D",
            "freeze_a": "+211C A/D", "freeze_b": "+2120 A/D", "result": "Result",
        }
        widths = {
            "index": 42, "time": 78, "expected": 76, "observed": 76, "move": 180, "attacker": 72, "defender": 72,
            "resolved": 86, "remaining": 86, "freeze_a": 102, "freeze_b": 102, "result": 90,
        }
        for key in columns:
            self.capture_tree.heading(key, text=headings[key])
            self.capture_tree.column(key, width=widths[key], minwidth=40, stretch=key == "move")
        self.capture_tree.tag_configure("match", foreground="#dceeff")
        self.capture_tree.tag_configure("mismatch", foreground="#ffadad")
        self.capture_tree.bind("<<TreeviewSelect>>", self._capture_selected)

        detail_card.grid_columnconfigure(0, weight=1)
        detail_card.grid_rowconfigure(0, weight=1)
        self.detail_tabs = ttk.Notebook(detail_card, style="TP.TNotebook")
        self.detail_tabs.grid(row=0, column=0, sticky="nsew")
        analysis_tab = ttk.Frame(self.detail_tabs, style="TP.Root.TFrame")
        scan_tab = ttk.Frame(self.detail_tabs, style="TP.Root.TFrame")
        timeline_tab = ttk.Frame(self.detail_tabs, style="TP.Root.TFrame")
        neighborhood_tab = ttk.Frame(self.detail_tabs, style="TP.Root.TFrame")
        self.detail_tabs.add(analysis_tab, text="Analysis")
        self.detail_tabs.add(scan_tab, text="Blockstun Field Scan")
        self.detail_tabs.add(timeline_tab, text="Raw Timeline")
        self.detail_tabs.add(neighborhood_tab, text="0x1200 Neighborhood")

        analysis_tab.grid_columnconfigure(0, weight=1)
        analysis_tab.grid_rowconfigure(0, weight=1)
        self.analysis_text = tk.Text(
            analysis_tab,
            bg="#091521",
            fg="#dce8f3",
            insertbackground="#ffffff",
            relief="flat",
            borderwidth=0,
            font=("Segoe UI", 10),
            wrap="word",
            padx=12,
            pady=10,
        )
        analysis_scroll = ttk.Scrollbar(analysis_tab, orient="vertical", command=self.analysis_text.yview)
        self.analysis_text.configure(yscrollcommand=analysis_scroll.set)
        self.analysis_text.grid(row=0, column=0, sticky="nsew")
        analysis_scroll.grid(row=0, column=1, sticky="ns")
        self._set_analysis("Select a completed capture for analysis.")

        scan_tab.grid_columnconfigure(0, weight=1)
        scan_tab.grid_rowconfigure(0, weight=1)
        self.scan_text = tk.Text(
            scan_tab,
            bg="#091521",
            fg="#dce8f3",
            insertbackground="#ffffff",
            relief="flat",
            borderwidth=0,
            font=("Cascadia Mono", 10),
            wrap="none",
            padx=12,
            pady=10,
        )
        scan_y = ttk.Scrollbar(scan_tab, orient="vertical", command=self.scan_text.yview)
        scan_x = ttk.Scrollbar(scan_tab, orient="horizontal", command=self.scan_text.xview)
        self.scan_text.configure(yscrollcommand=scan_y.set, xscrollcommand=scan_x.set)
        self.scan_text.grid(row=0, column=0, sticky="nsew")
        scan_y.grid(row=0, column=1, sticky="ns")
        scan_x.grid(row=1, column=0, sticky="ew")
        self._set_scan_text(summarize_blockstun_scan(self.captures))

        timeline_tab.grid_columnconfigure(0, weight=1)
        timeline_tab.grid_rowconfigure(0, weight=1)
        timeline_columns = (
            "ms", "role", "action", "move_frame", "frame_a", "frame_b", "resolved", "remaining", "freeze_a", "freeze_b", "state", "hp"
        )
        self.timeline_tree = ttk.Treeview(timeline_tab, columns=timeline_columns, show="headings", style="TP.Treeview")
        timeline_y = ttk.Scrollbar(timeline_tab, orient="vertical", command=self.timeline_tree.yview)
        timeline_x = ttk.Scrollbar(timeline_tab, orient="horizontal", command=self.timeline_tree.xview)
        self.timeline_tree.configure(yscrollcommand=timeline_y.set, xscrollcommand=timeline_x.set)
        self.timeline_tree.grid(row=0, column=0, sticky="nsew")
        timeline_y.grid(row=0, column=1, sticky="ns")
        timeline_x.grid(row=1, column=0, sticky="ew")
        timeline_headings = {
            "ms": "ms", "role": "Role", "action": "Action", "move_frame": "Move frame", "frame_a": "Frame A", "frame_b": "Frame B",
            "resolved": "+1210", "remaining": "+1228", "freeze_a": "+211C", "freeze_b": "+2120", "state": "f063", "hp": "HP",
        }
        timeline_widths = {
            "ms": 76, "role": 78, "action": 180, "move_frame": 82, "frame_a": 72, "frame_b": 72, "resolved": 72, "remaining": 72,
            "freeze_a": 72, "freeze_b": 72, "state": 62, "hp": 82,
        }
        for key in timeline_columns:
            self.timeline_tree.heading(key, text=timeline_headings[key])
            self.timeline_tree.column(key, width=timeline_widths[key], minwidth=50, stretch=key == "action")
        self.timeline_tree.tag_configure("attacker", foreground="#8bdcff")
        self.timeline_tree.tag_configure("defender", foreground="#c7b8ff")

        neighborhood_tab.grid_columnconfigure(0, weight=1)
        neighborhood_tab.grid_rowconfigure(0, weight=1)
        neighborhood_columns = ("ms",) + tuple(f"o{offset:04x}" for offset in STUN_SCAN_OFFSETS)
        self.neighborhood_tree = ttk.Treeview(neighborhood_tab, columns=neighborhood_columns, show="headings", style="TP.Treeview")
        neighborhood_y = ttk.Scrollbar(neighborhood_tab, orient="vertical", command=self.neighborhood_tree.yview)
        neighborhood_x = ttk.Scrollbar(neighborhood_tab, orient="horizontal", command=self.neighborhood_tree.xview)
        self.neighborhood_tree.configure(yscrollcommand=neighborhood_y.set, xscrollcommand=neighborhood_x.set)
        self.neighborhood_tree.grid(row=0, column=0, sticky="nsew")
        neighborhood_y.grid(row=0, column=1, sticky="ns")
        neighborhood_x.grid(row=1, column=0, sticky="ew")
        self.neighborhood_tree.heading("ms", text="ms")
        self.neighborhood_tree.column("ms", width=76, minwidth=60, stretch=False)
        for offset in STUN_SCAN_OFFSETS:
            key = f"o{offset:04x}"
            self.neighborhood_tree.heading(key, text=f"+{offset:04X}")
            self.neighborhood_tree.column(key, width=72, minwidth=62, stretch=False, anchor="e")

    def _set_analysis(self, text: str) -> None:
        try:
            self.analysis_text.configure(state="normal")
            self.analysis_text.delete("1.0", "end")
            self.analysis_text.insert("1.0", str(text))
            self.analysis_text.configure(state="disabled")
        except Exception:
            pass

    def _set_scan_text(self, text: str) -> None:
        try:
            self.scan_text.configure(state="normal")
            self.scan_text.delete("1.0", "end")
            self.scan_text.insert("1.0", str(text))
            self.scan_text.configure(state="disabled")
        except Exception:
            pass

    def _refresh_scan(self) -> None:
        self._set_scan_text(summarize_blockstun_scan(self.captures))

    def _slots_changed(self, _event=None) -> None:
        if self.attacker_var.get() == self.defender_var.get():
            self.status_var.set("Attacker and defender must be different slots.")
        else:
            self.status_var.set("Slots updated. Arm the next scenario.")
        self.stop()
        self._prebuffer.clear()
        self._last_attacker = None
        self._last_defender = None
        self._last_signature = None

    def _read_state(self, slot: str, include_stun_scan: bool = False) -> FighterTimingState | None:
        pointer = SLOT_POINTERS.get(str(slot))
        if pointer is None:
            return None
        base = _read_u32(pointer, 0)
        if not _valid_base(base):
            return None
        return FighterTimingState(
            slot=str(slot),
            base=int(base),
            char_id=_read_u32(base + OFF_CHAR_ID, 0),
            action_id=_read_u32(base + OFF_ACTION_ID, 0) & 0xFFFF,
            action_frame=_decode_action_frame(_read_u32(base + OFF_ACTION_FRAME_FLOAT, 0)),
            frame_a=_read_u32(base + OFF_FRAME_A, 0),
            frame_b=_read_u32(base + OFF_FRAME_B, 0),
            resolved_stun=_read_u32(base + OFF_RESOLVED_STUN, 0),
            stun_remaining=_read_u32(base + OFF_STUN_REMAINING, 0),
            freeze_a=_read_u32(base + OFF_FREEZE_A, 0),
            freeze_b=_read_u32(base + OFF_FREEZE_B, 0),
            state_063=_read_u8(base + OFF_STATE_063, 0),
            hp=_read_u32(base + OFF_CUR_HP, 0),
            blockstun_remaining=_read_u32(base + OFF_BLOCKSTUN_REMAINING, 0),
            stun_scan=tuple(_read_u32(base + offset, 0) for offset in STUN_SCAN_OFFSETS) if include_stun_scan else tuple(),
        )

    def _label(self, action_id: int, char_id: int) -> str:
        try:
            label = move_label_for(int(action_id), int(char_id), self.move_map, self.global_map)
        except Exception:
            label = ""
        text = str(label or "").strip()
        return text if text else f"Action 0x{int(action_id) & 0xFFFF:04X}"

    @staticmethod
    def _state_signature(state: FighterTimingState) -> tuple[int, ...]:
        return (
            state.base, state.char_id, state.action_id, state.action_frame, state.frame_a, state.frame_b,
            state.blockstun_remaining, state.resolved_stun, state.stun_remaining, state.freeze_a, state.freeze_b, state.state_063, state.hp,
            *state.stun_scan,
        )

    def _update_live_text(self, attacker: FighterTimingState, defender: FighterTimingState) -> None:
        self.live_attacker_var.set(
            f"{attacker.slot}  {_character_name(attacker.char_id)}  |  {self._label(attacker.action_id, attacker.char_id)} [0x{attacker.action_id:04X}]  frame {attacker.action_frame}\n"
            f"STOP {max(attacker.freeze_a, attacker.freeze_b)}   BS {attacker.blockstun_remaining}   HS {attacker.stun_remaining}/{attacker.resolved_stun}"
        )
        self.live_defender_var.set(
            f"{defender.slot}  {_character_name(defender.char_id)}  |  {self._label(defender.action_id, defender.char_id)} [0x{defender.action_id:04X}]  frame {defender.action_frame}\n"
            f"STOP {max(defender.freeze_a, defender.freeze_b)}   BS {defender.blockstun_remaining}   HS {defender.stun_remaining}/{defender.resolved_stun}"
        )
        latest = TIMING_ENGINE.latest_result() if TIMING_ENGINE is not None else None
        if latest is None:
            self.live_result_var.set("No completed timing interaction yet.")
        elif latest.kind == "block":
            adv = "?" if latest.block_advantage is None else f"{int(latest.block_advantage):+d}"
            self.live_result_var.set(
                f"{latest.attacker_slot} {latest.action_name} -> {latest.defender_slot} BLOCK  |  "
                f"ADV {adv}   BS {latest.blockstun}   STOP {latest.hitstop}   "
                f"READY {latest.attacker_ready_frames}/{latest.defender_ready_frames}"
            )
        else:
            self.live_result_var.set(
                f"{latest.attacker_slot} {latest.action_name} -> {latest.defender_slot} HIT  |  "
                f"HS {latest.hitstun}   STOP {latest.hitstop}   DMG {latest.damage:,}"
            )

    def arm(self, expected: str) -> None:
        expected = str(expected).lower().strip()
        if expected not in {"whiff", "hit", "block"}:
            return
        if self.attacker_var.get() == self.defender_var.get():
            self.status_var.set("Choose different attacker and defender slots first.")
            return
        attacker = self._read_state(self.attacker_var.get(), include_stun_scan=False)
        defender = self._read_state(self.defender_var.get(), include_stun_scan=True)
        if attacker is None or defender is None:
            self.status_var.set("Waiting for both fighter pointers. Start a match, then arm again.")
            return
        self._armed_expected = expected
        self._armed_at = time.monotonic()
        self._baseline_action = attacker.action_id
        self._active = None
        self._prebuffer.clear()
        self._last_attacker = attacker
        self._last_defender = defender
        self.arm_var.set(f"ARMED: {expected.upper()}")
        self.status_var.set(f"Armed for {expected}. Perform the next move with {attacker.slot}.")

    def stop(self) -> None:
        self._armed_expected = None
        self._active = None
        self.arm_var.set("NOT ARMED")

    def clear(self) -> None:
        self.stop()
        self.captures.clear()
        self._capture_by_item.clear()
        for item in self.capture_tree.get_children(""):
            self.capture_tree.delete(item)
        for item in self.timeline_tree.get_children(""):
            self.timeline_tree.delete(item)
        for item in self.neighborhood_tree.get_children(""):
            self.neighborhood_tree.delete(item)
        self._set_analysis("Select a completed capture for analysis.")
        self._refresh_scan()
        self.status_var.set("Timing capture history cleared.")

    def _start_capture(self, attacker: FighterTimingState, defender: FighterTimingState, now: float) -> None:
        expected = self._armed_expected
        if expected is None:
            return
        sample = TimingSample(0.0, attacker, defender)
        seed: list[TimingSample] = []
        for pre in self._prebuffer:
            seed.append(TimingSample(pre.elapsed_ms, pre.attacker, pre.defender))
        seed.append(sample)
        start_time = now
        # Rebase any prebuffer records so the move start remains exactly zero.
        if seed:
            last_pre_ms = seed[-2].elapsed_ms if len(seed) > 1 else 0.0
            for index, item in enumerate(seed[:-1]):
                distance = (len(seed) - 1 - index) * POLL_MS
                item.elapsed_ms = float(-distance)
            seed[-1].elapsed_ms = 0.0
        self._active = {
            "expected": expected,
            "start_time": start_time,
            "action_id": attacker.action_id,
            "char_id": attacker.char_id,
            "samples": seed,
            "contact": None,
            "contact_time": None,
            "contact_basis": None,
            "last_signature": None,
        }
        self.status_var.set(f"Capturing {self._label(attacker.action_id, attacker.char_id)} for expected {expected}.")

    def _append_active_sample(self, attacker: FighterTimingState, defender: FighterTimingState, now: float) -> None:
        if self._active is None:
            return
        elapsed_ms = (now - float(self._active["start_time"])) * 1000.0
        signature = self._state_signature(attacker) + self._state_signature(defender)
        if signature == self._active.get("last_signature"):
            return
        self._active["last_signature"] = signature
        self._active["samples"].append(TimingSample(round(elapsed_ms, 3), attacker, defender))

    def _finish_capture(self, observed: str, note: str = "") -> None:
        active = self._active
        expected = self._armed_expected
        if active is None or expected is None:
            self.stop()
            return
        samples = list(active.get("samples") or [])
        observed = str(observed or "unknown")
        result = "MATCH" if observed == expected else "MISMATCH"
        contact_time = active.get("contact_time")
        contact_ms = None
        if contact_time is not None:
            contact_ms = (float(contact_time) - float(active["start_time"])) * 1000.0
        capture = TimingCapture(
            index=len(self.captures) + 1,
            timestamp=time.strftime("%H:%M:%S"),
            expected=expected,
            observed=observed,
            result=result,
            attacker_slot=self.attacker_var.get(),
            defender_slot=self.defender_var.get(),
            char_id=int(active["char_id"]),
            action_id=int(active["action_id"]) & 0xFFFF,
            action_name=self._label(active["action_id"], active["char_id"]),
            contact_ms=round(contact_ms, 3) if contact_ms is not None else None,
            notes=str(note or ""),
            samples=samples,
        )
        self.captures.append(capture)
        while len(self.captures) > MAX_HISTORY:
            self.captures.pop(0)
        resolved = _max_field(samples, "defender", "resolved_stun")
        remaining = _max_field(samples, "defender", "stun_remaining")
        freeze_a_a = _max_field(samples, "attacker", "freeze_a")
        freeze_a_d = _max_field(samples, "defender", "freeze_a")
        freeze_b_a = _max_field(samples, "attacker", "freeze_b")
        freeze_b_d = _max_field(samples, "defender", "freeze_b")
        item = self.capture_tree.insert(
            "",
            "end",
            values=(
                capture.index, capture.timestamp, capture.expected.upper(), capture.observed.upper(),
                f"{capture.action_name} [0x{capture.action_id:04X}]", capture.attacker_slot, capture.defender_slot,
                resolved, remaining, f"{freeze_a_a}/{freeze_a_d}", f"{freeze_b_a}/{freeze_b_d}", capture.result,
            ),
            tags=("match" if result == "MATCH" else "mismatch",),
        )
        self._capture_by_item[item] = capture
        self.capture_tree.selection_set(item)
        self.capture_tree.focus(item)
        self.capture_tree.see(item)
        self._show_capture(capture)
        self._refresh_scan()
        self.status_var.set(f"Captured {capture.action_name}: expected {expected}, observed {observed}.")
        self.stop()

    def _sample_is_settled(self, sample: TimingSample) -> bool:
        defender = sample.defender
        return defender.stun_remaining == 0 and defender.freeze_a == 0 and defender.freeze_b == 0

    def _process_capture(self, attacker: FighterTimingState, defender: FighterTimingState, now: float) -> None:
        active = self._active
        if active is None:
            return
        self._append_active_sample(attacker, defender, now)
        previous_attacker = self._last_attacker
        previous_defender = self._last_defender
        explicit_contact = classify_contact(previous_defender, defender)
        if explicit_contact is not None:
            if active.get("contact") in {None, "impact"}:
                active["contact"] = explicit_contact
                active["contact_basis"] = "state or HP"
                if active.get("contact_time") is None:
                    active["contact_time"] = now
        elif active.get("contact") is None and bilateral_impact_started(
            previous_attacker, attacker, previous_defender, defender
        ):
            active["contact"] = "impact"
            active["contact_basis"] = "bilateral freeze"
            active["contact_time"] = now

        expected = str(active.get("expected") or "")
        action_changed = attacker.action_id != int(active["action_id"])
        elapsed = now - float(active["start_time"])
        contact_kind = active.get("contact")

        if contact_kind is not None:
            contact_elapsed = now - float(active.get("contact_time") or now)
            settled = self._sample_is_settled(TimingSample(0.0, attacker, defender))
            minimum_contact_seconds = BLOCK_SCAN_POST_CONTACT_SECONDS if expected == "block" else POST_CONTACT_MIN_SECONDS
            if contact_elapsed >= minimum_contact_seconds and settled:
                observed = resolve_impact_contact(list(active.get("samples") or []), str(contact_kind)) or "unknown"
                if contact_kind == "impact":
                    note = (
                        "Shared impact freeze was observed on both fighters. "
                        + ("Defender HP decreased, so the contact resolved as hit." if observed == "hit" else "Defender HP stayed unchanged, so the controlled 5A contact resolved as block.")
                    )
                else:
                    note = "Contact counters returned to zero."
                self._finish_capture(observed, note)
                return
            if contact_elapsed >= POST_CONTACT_MAX_SECONDS:
                observed = resolve_impact_contact(list(active.get("samples") or []), str(contact_kind)) or "unknown"
                self._finish_capture(observed, "Capture ended at the post-contact safety limit.")
                return
        elif action_changed and elapsed >= 0.05:
            self._finish_capture("whiff", "Move exited without bilateral impact freeze, defender state, or HP loss.")
            return

        if elapsed >= MAX_CAPTURE_SECONDS:
            observed = resolve_impact_contact(list(active.get("samples") or []), str(contact_kind) if contact_kind else None) or "timeout"
            self._finish_capture(observed, "Capture reached the four-second safety limit.")

    def _capture_selected(self, _event=None) -> None:
        selection = self.capture_tree.selection()
        if not selection:
            return
        capture = self._capture_by_item.get(selection[0])
        if capture is not None:
            self._show_capture(capture)

    def _show_capture(self, capture: TimingCapture) -> None:
        self._set_analysis(summarize_capture(capture))
        for item in self.timeline_tree.get_children(""):
            self.timeline_tree.delete(item)
        for sample in capture.samples:
            for role, state in (("Attacker", sample.attacker), ("Defender", sample.defender)):
                self.timeline_tree.insert(
                    "",
                    "end",
                    values=(
                        f"{sample.elapsed_ms:.1f}", role, f"{self._label(state.action_id, state.char_id)} [0x{state.action_id:04X}]",
                        state.action_frame, state.frame_a, state.frame_b, state.resolved_stun, state.stun_remaining,
                        state.freeze_a, state.freeze_b, state.state_063, state.hp,
                    ),
                    tags=(role.lower(),),
                )
        for item in self.neighborhood_tree.get_children(""):
            self.neighborhood_tree.delete(item)
        for sample in capture.samples:
            values = [f"{sample.elapsed_ms:.1f}"]
            values.extend(_scan_value(sample.defender, offset) for offset in STUN_SCAN_OFFSETS)
            self.neighborhood_tree.insert("", "end", values=tuple(values))

    def copy_selected(self) -> None:
        selection = self.capture_tree.selection()
        capture = self._capture_by_item.get(selection[0]) if selection else (self.captures[-1] if self.captures else None)
        if capture is None:
            self.status_var.set("Nothing to copy.")
            return
        text = summarize_capture(capture)
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(text)
            self.window.update_idletasks()
            self.status_var.set(f"Copied capture {capture.index} analysis.")
        except Exception as exc:
            self.status_var.set(f"Copy failed: {exc}")

    def copy_scan(self) -> None:
        text = summarize_blockstun_scan(self.captures)
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(text)
            self.window.update_idletasks()
            self.status_var.set("Copied blockstun field scan.")
        except Exception as exc:
            self.status_var.set(f"Copy scan failed: {exc}")

    def save_csv(self) -> None:
        if not self.captures:
            self.status_var.set("Nothing to save.")
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            parent=self.window,
            title="Save timing probe CSV",
            defaultextension=".csv",
            initialfile=f"tvc_timing_probe_{stamp}.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        fieldnames = [
            "capture", "timestamp", "expected", "observed", "result", "attacker_slot", "defender_slot", "char_id", "action_id", "action_name",
            "contact_ms", "elapsed_ms", "role", "base", "state_char_id", "state_action_id", "action_frame", "frame_a", "frame_b",
            "resolved_stun_1210", "stun_remaining_1228", "freeze_a_211c", "freeze_b_2120", "state_063", "hp",
            *[f"scan_{offset:04x}" for offset in STUN_SCAN_OFFSETS],
            "notes",
        ]
        try:
            with open(path, "w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for capture in self.captures:
                    for sample in capture.samples:
                        for role, state in (("attacker", sample.attacker), ("defender", sample.defender)):
                            writer.writerow(
                                {
                                    "capture": capture.index,
                                    "timestamp": capture.timestamp,
                                    "expected": capture.expected,
                                    "observed": capture.observed,
                                    "result": capture.result,
                                    "attacker_slot": capture.attacker_slot,
                                    "defender_slot": capture.defender_slot,
                                    "char_id": capture.char_id,
                                    "action_id": capture.action_id,
                                    "action_name": capture.action_name,
                                    "contact_ms": "" if capture.contact_ms is None else capture.contact_ms,
                                    "elapsed_ms": sample.elapsed_ms,
                                    "role": role,
                                    "base": f"0x{state.base:08X}",
                                    "state_char_id": state.char_id,
                                    "state_action_id": f"0x{state.action_id:04X}",
                                    "action_frame": state.action_frame,
                                    "frame_a": state.frame_a,
                                    "frame_b": state.frame_b,
                                    "resolved_stun_1210": state.resolved_stun,
                                    "stun_remaining_1228": state.stun_remaining,
                                    "freeze_a_211c": state.freeze_a,
                                    "freeze_b_2120": state.freeze_b,
                                    "state_063": state.state_063,
                                    "hp": state.hp,
                                    **{f"scan_{offset:04x}": _scan_value(state, offset) for offset in STUN_SCAN_OFFSETS},
                                    "notes": capture.notes,
                                }
                            )
            self.status_var.set(f"Saved {len(self.captures)} captures to {os.path.basename(path)}.")
        except Exception as exc:
            self.status_var.set(f"CSV save failed: {exc}")

    def _poll(self) -> None:
        if self._closing:
            return
        now = time.monotonic()
        scan_active = self._armed_expected is not None or self._active is not None
        attacker = self._read_state(self.attacker_var.get(), include_stun_scan=False)
        defender = self._read_state(self.defender_var.get(), include_stun_scan=scan_active)
        if attacker is None or defender is None:
            self.live_attacker_var.set("Waiting for attacker pointer...")
            self.live_defender_var.set("Waiting for defender pointer...")
            self._schedule_poll()
            return

        self._update_live_text(attacker, defender)
        signature = self._state_signature(attacker) + self._state_signature(defender)
        if signature != self._last_signature:
            pre_elapsed = (now - self._armed_at) * 1000.0 if self._armed_at else 0.0
            self._prebuffer.append(TimingSample(round(pre_elapsed, 3), attacker, defender))
            self._last_signature = signature

        if self._armed_expected is not None:
            if self._active is None:
                move_started = attacker.action_id >= 0x0100 and attacker.action_id != self._baseline_action
                frame_reset = (
                    attacker.action_id >= 0x0100
                    and self._last_attacker is not None
                    and attacker.action_id == self._last_attacker.action_id
                    and attacker.action_frame <= 2
                    and self._last_attacker.action_frame > attacker.action_frame + 2
                )
                if move_started or frame_reset:
                    self._start_capture(attacker, defender, now)
            else:
                self._process_capture(attacker, defender, now)

        self._last_attacker = attacker
        self._last_defender = defender
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        if self._closing:
            return
        try:
            self._after_id = self.window.after(POLL_MS, self._poll)
        except Exception:
            self._after_id = None

    def close(self) -> None:
        global _ACTIVE_WINDOW
        if self._closing:
            return
        self._closing = True
        if self._after_id is not None:
            try:
                self.window.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        try:
            self.window.destroy()
        except Exception:
            pass
        if _ACTIVE_WINDOW is self:
            _ACTIVE_WINDOW = None


def open_timing_probe_window(
    move_map: dict[int, dict[int, str]] | None = None,
    global_map: dict[int, str] | None = None,
    initial_attacker: str = "P1-C1",
) -> None:
    """Open or focus the standalone timing research probe."""
    def create(master_root: tk.Misc) -> None:
        global _ACTIVE_WINDOW
        existing = _ACTIVE_WINDOW
        try:
            if existing is not None and bool(existing.window.winfo_exists()):
                existing.move_map = move_map if isinstance(move_map, dict) else existing.move_map
                existing.global_map = global_map if isinstance(global_map, dict) else existing.global_map
                existing.window.deiconify()
                existing.window.lift()
                existing.window.focus_force()
                return
        except Exception:
            _ACTIVE_WINDOW = None
        _ACTIVE_WINDOW = TimingProbeWindow(master_root, move_map, global_map, initial_attacker)

    tk_call(create)


__all__ = [
    "SLOT_POINTERS",
    "FighterTimingState",
    "TimingSample",
    "TimingCapture",
    "STUN_SCAN_OFFSETS",
    "OFF_BLOCKSTUN_REMAINING",
    "classify_contact",
    "bilateral_impact_started",
    "resolve_impact_contact",
    "select_scan_baselines",
    "rank_blockstun_candidates",
    "summarize_blockstun_scan",
    "summarize_capture",
    "TimingProbeWindow",
    "open_timing_probe_window",
]
