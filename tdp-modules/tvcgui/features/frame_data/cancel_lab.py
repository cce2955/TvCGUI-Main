from __future__ import annotations

import struct
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Sequence

from . import cancel_mapper as FCM
from . import cancel_windows as FCW
from .widgets import apply_titlebar_icon
try:
    from tvcgui.platform.dolphin import rd32, wd32
except Exception:
    rd32 = None
    wd32 = None


SLOT_POINTERS = {
    "P1-C1": 0x803C9FCC,
    "P2-C1": 0x803C9FD4,
    "P1-C2": 0x803C9FDC,
    "P2-C2": 0x803C9FE4,
}

OFF_ACTION_ID = 0x01E8
OFF_ACTION_REQUEST = 0x0200
OFF_ANIM_FRAME_FLOAT = 0x01D8
OFF_FRAME_A = 0x021C
OFF_FRAME_B = 0x0220

# Recomp-backed input and command fields.
# 0x80048270 snapshots these words before command resolution.
OFF_INPUT_HELD = 0x13CC
OFF_INPUT_PRESSED = 0x13D8
OFF_COMMAND_TABLE = 0x13E8

# 0x800587F0 and related command recognizer paths write the recognized
# special/super command here as action_id - 0x100. The 0x210C slot is the
# raw candidate used before some native permission checks.
OFF_SPECIAL_FLAGS = 0x1990
OFF_SPECIAL_CANDIDATE = 0x1994
OFF_RAW_SPECIAL_FLAGS = 0x2108
OFF_RAW_SPECIAL_CANDIDATE = 0x210C

COMMAND_ROW_SIZE = 24
COMMAND_ROW_LIMIT = 192

POLL_MS = 16
MAX_PULSES_PER_SOURCE = 180
DEFAULT_EARLIEST_FRAME = 8
_ACTIVE_BY_SLOT: dict[str, "CancelLabWindow"] = {}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def normalize_slot_label(value: Any) -> str:
    text = str(value or "P1-C1").strip().upper().replace("_", "-").replace(" ", "")
    aliases = {
        "P1": "P1-C1",
        "P2": "P2-C1",
        "P1C1": "P1-C1",
        "P1C2": "P1-C2",
        "P2C1": "P2-C1",
        "P2C2": "P2-C2",
        "1": "P1-C1",
        "2": "P2-C1",
    }
    return aliases.get(text, text if text in SLOT_POINTERS else "P1-C1")


def mailbox_value_for_action(action_id: int) -> int:
    """Encode an action request the same way the existing trainer mailbox does."""
    return (int(action_id) - 0x4000) & 0xFFFFFFFF


def frame_in_window(frame: int, earliest: int, latest: int) -> bool:
    frame_i = max(0, int(frame))
    earliest_i = max(0, int(earliest))
    latest_i = max(0, int(latest))
    return frame_i >= earliest_i and (latest_i == 0 or frame_i <= latest_i)


def elapsed_source_frame(started_at: float, now: float) -> int:
    """Return a stable 60 Hz frame age for the currently observed source action."""
    try:
        started = float(started_at)
        current = float(now)
    except Exception:
        return 0
    if started <= 0.0 or current < started:
        return 0
    return max(1, int((current - started) * 60.0) + 1)


def _valid_fighter_base(value: Any) -> bool:
    base = _as_int(value, 0)
    return 0x90000000 <= base < 0x94000000 and (base & 0x3) == 0


def _read_u32(addr: int, default: int = 0) -> int:
    if rd32 is None:
        return int(default) & 0xFFFFFFFF
    try:
        value = rd32(int(addr))
    except Exception:
        value = None
    return int(default) & 0xFFFFFFFF if value is None else int(value) & 0xFFFFFFFF


def _write_u32(addr: int, value: int) -> bool:
    if wd32 is None:
        return False
    try:
        result = wd32(int(addr), int(value) & 0xFFFFFFFF)
        return result is not False
    except Exception:
        return False


def _float_from_word(word: int) -> float | None:
    try:
        value = struct.unpack(">f", struct.pack(">I", int(word) & 0xFFFFFFFF))[0]
    except Exception:
        return None
    if value != value or value < 0.0 or value > 10000.0:
        return None
    return float(value)


def read_frame_snapshot(base: int) -> dict[str, Any]:
    frame_a = _read_u32(base + OFF_FRAME_A, 0)
    frame_b = _read_u32(base + OFF_FRAME_B, 0)
    anim_word = _read_u32(base + OFF_ANIM_FRAME_FLOAT, 0)
    anim_float = _float_from_word(anim_word)

    integer_candidates = [value for value in (frame_a, frame_b) if 0 < value <= 10000]
    if integer_candidates:
        frame = max(integer_candidates)
        source = "fighter frame"
    elif anim_float is not None and anim_float > 0.0:
        frame = max(0, int(anim_float))
        source = "animation frame"
    else:
        frame = 0
        source = "unknown"

    return {
        "frame": int(frame),
        "source": source,
        "frame_a": frame_a,
        "frame_b": frame_b,
        "anim_word": anim_word,
        "anim_float": anim_float,
    }


def _signed_u16(value: int) -> int:
    value_i = int(value) & 0xFFFF
    return value_i - 0x10000 if value_i & 0x8000 else value_i


def command_rows_for_target(base: int, target_id: int) -> list[dict[str, int]]:
    """Read normal-command rows for one action from fighter+0x13E8.

    Recomp function 0x80045D7C walks 24-byte rows, stops when the leading
    signed halfword is -1, and returns the action at row+0x14 when the row
    input/state tests pass.
    """
    table = _read_u32(int(base) + OFF_COMMAND_TABLE, 0)
    if not _valid_fighter_base(table):
        return []
    target = int(target_id) & 0xFFFF
    rows: list[dict[str, int]] = []
    for index in range(COMMAND_ROW_LIMIT):
        row_addr = table + index * COMMAND_ROW_SIZE
        word0 = _read_u32(row_addr, 0xFFFFFFFF)
        row_type = _signed_u16(word0 >> 16)
        if row_type == -1:
            break
        action = _read_u32(row_addr + 20, 0) & 0xFFFF
        if action != target:
            continue
        rows.append(
            {
                "addr": row_addr,
                "index": index,
                "type": row_type,
                "mode": word0 & 0xFFFF,
                "direction": _read_u32(row_addr + 4, 0),
                "buttons": _read_u32(row_addr + 8, 0),
                "state_a": _read_u32(row_addr + 12, 0),
                "state_b": _read_u32(row_addr + 16, 0),
                "target": action,
            }
        )
    return rows


def recognized_special_actions(base: int) -> tuple[set[int], dict[str, int]]:
    """Return special/super actions recognized by TvC's command parser.

    The recomp shows +0x1994 and +0x210C storing action_id - 0x100.
    +0x1994 is the normal recognized-command slot, while +0x210C is a raw
    candidate path that can exist before the native cancel gate consumes it.
    """
    cooked = _read_u32(int(base) + OFF_SPECIAL_CANDIDATE, 0)
    raw = _read_u32(int(base) + OFF_RAW_SPECIAL_CANDIDATE, 0xFFFFFFFF)
    actions: set[int] = set()
    for value in (cooked, raw):
        if value in (0, 0xFFFFFFFF):
            continue
        if 0 < value < 0x3F00:
            actions.add((value + 0x100) & 0xFFFF)
    return actions, {
        "cooked": cooked,
        "raw": raw,
        "flags": _read_u32(int(base) + OFF_SPECIAL_FLAGS, 0),
        "raw_flags": _read_u32(int(base) + OFF_RAW_SPECIAL_FLAGS, 0),
    }


def normal_input_match(base: int, target_id: int) -> dict[str, int] | None:
    """Match the selected normal against the live relative direction/button edge.

    Standard rows use type 6. Their direction is row+4 and their newly pressed
    button mask is row+8. The live parser combines fighter+0x13CC low direction
    bits with fighter+0x13D8 attack-button edge bits.
    """
    held = _read_u32(int(base) + OFF_INPUT_HELD, 0)
    pressed = _read_u32(int(base) + OFF_INPUT_PRESSED, 0)
    live_direction = held & 0xF
    for row in command_rows_for_target(base, target_id):
        button_mask = int(row.get("buttons", 0)) & 0xFFFFFFFF
        required_direction = int(row.get("direction", 0)) & 0xF
        if int(row.get("type", -1)) != 6 or button_mask == 0:
            continue
        if (pressed & button_mask) != button_mask:
            continue
        if live_direction != required_direction:
            continue
        result = dict(row)
        result.update(held=held, pressed=pressed, live_direction=live_direction)
        return result
    return None


def manual_target_trigger(base: int, target_id: int, target_kind: str) -> dict[str, Any] | None:
    kind = str(target_kind or "").strip().lower()
    if kind == "normal":
        row = normal_input_match(base, target_id)
        if row:
            return {
                "kind": "normal command row",
                "detail": (
                    f"row 0x{int(row['addr']):08X}, dir 0x{int(row['direction']) & 0xF:X}, "
                    f"buttons 0x{int(row['buttons']):08X}, pressed 0x{int(row['pressed']):08X}"
                ),
                "row": row,
            }
        return None

    actions, evidence = recognized_special_actions(base)
    target = int(target_id) & 0xFFFF
    if target in actions:
        return {
            "kind": "TvC special command candidate",
            "detail": (
                f"cooked 0x{evidence['cooked']:08X}, raw 0x{evidence['raw']:08X}, "
                f"flags 0x{evidence['flags']:08X}/0x{evidence['raw_flags']:08X}"
            ),
            "actions": sorted(actions),
            "evidence": evidence,
        }
    return None


def _move_label(move: dict[str, Any], char_name: str = "") -> str:
    name = FCM.display_name(move, char_name)
    action_id = _as_int(move.get("id"), -1)
    kind = FCM.move_kind(move).title()
    if action_id >= 0:
        return f"{name} [0x{action_id:04X}] ({kind})"
    return f"{name} ({kind})"


class CancelLabWindow:
    """Live source-to-target action request probe.

    This is intentionally an experiment harness, not a permanent cancel patch.
    It pulses TvC's existing fighter action mailbox only while the chosen source
    action and frame window are active, then removes any request that was not
    consumed so a failed test cannot fire later from neutral.
    """

    def __init__(
        self,
        parent: tk.Misc,
        slot_label: str,
        target_slot: dict[str, Any] | None,
        moves: Sequence[dict[str, Any]],
        source_move: dict[str, Any] | None = None,
        target_move: dict[str, Any] | None = None,
        status_callback: Callable[[str], None] | None = None,
        profile_refresh_callback: Callable[[int | None], None] | None = None,
    ) -> None:
        self.parent = parent
        self.slot_label = normalize_slot_label(slot_label)
        self.target_slot = target_slot if isinstance(target_slot, dict) else {}
        self.status_callback = status_callback
        self.profile_refresh_callback = profile_refresh_callback
        self._after_id: str | None = None
        self._closing = False

        self.armed = False
        self.was_in_source = False
        self.completed_for_source = False
        self.request_pending = False
        self.request_value = 0
        self.request_addr = 0
        self.request_source_id = 0
        self.request_target_id = 0
        self.request_source_frame = 0
        self.request_started_at = 0.0
        self.manual_deadline = 0.0
        self.pulses_this_source = 0
        self.attempt_count = 0
        self.accept_count = 0
        self.reject_count = 0
        self.last_action = 0
        self.last_base = 0
        self.last_frame = 0
        self.last_result = "Ready."
        self.source_started_at = 0.0
        self.armed_source_id = 0
        self.armed_target_id = 0
        self.armed_earliest = DEFAULT_EARLIEST_FRAME
        self.armed_latest = 0
        self.armed_target_kind = "other"
        self.armed_mode = "manual"
        self._route_controls: list[tk.Widget] = []
        self._last_trigger_signature: tuple[Any, ...] | None = None
        self._last_special_evidence: tuple[int, int] | None = None
        self._source_special_baseline: set[int] = set()
        self._source_pressed_baseline = 0

        canonical = FCM.canonical_moves(list(moves or []))
        for extra in (source_move, target_move):
            if not isinstance(extra, dict) or extra.get("id") is None:
                continue
            extra_id = _as_int(extra.get("id"), -1)
            if not any(_as_int(item.get("id"), -2) == extra_id for item in canonical):
                canonical.append(extra)
        canonical.sort(
            key=lambda move: (
                {"normal": 0, "special": 1, "super": 2}.get(FCM.move_kind(move), 9),
                _as_int(move.get("id"), 0xFFFF),
            )
        )
        self.moves = canonical
        self.char_name = str(self.target_slot.get("char_name") or "")

        self.move_by_label: dict[str, dict[str, Any]] = {}
        self.labels: list[str] = []
        for move in self.moves:
            label = _move_label(move, self.char_name)
            if label in self.move_by_label:
                label = f"{label} @ 0x{_as_int(move.get('abs'), 0):08X}"
            self.move_by_label[label] = move
            self.labels.append(label)

        source_label = self._label_for_move(source_move) or (self.labels[0] if self.labels else "")
        target_label = self._label_for_move(target_move)
        if not target_label:
            target_label = next((label for label in self.labels if label != source_label), source_label)

        self.window = tk.Toplevel(parent)
        apply_titlebar_icon(self.window, parent)
        self.window.title("Live Cancel Lab")
        self.window.geometry("940x720")
        self.window.minsize(780, 590)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        try:
            self.window.configure(bg="#101722")
        except Exception:
            pass

        self.source_var = tk.StringVar(master=self.window, value=source_label)
        self.target_var = tk.StringVar(master=self.window, value=target_label)
        self.earliest_var = tk.StringVar(master=self.window, value=str(DEFAULT_EARLIEST_FRAME))
        self.latest_var = tk.StringVar(master=self.window, value="0")
        self.pulse_var = tk.BooleanVar(master=self.window, value=True)
        self.repeat_var = tk.BooleanVar(master=self.window, value=True)
        self.auto_save_var = tk.BooleanVar(master=self.window, value=True)
        self.mode_var = tk.StringVar(master=self.window, value="manual")
        self.telemetry_var = tk.StringVar(master=self.window, value="Waiting for live fighter data...")
        self.status_var = tk.StringVar(master=self.window, value=self.last_result)
        self.counts_var = tk.StringVar(master=self.window, value="Attempts 0 | Accepted 0 | Rejected 0")
        self.arm_button_text = tk.StringVar(master=self.window, value="Arm manual cancel")

        self._build_ui()
        self._sync_mode_text()
        self._load_saved_window_for_source(announce=False)
        self._log(
            "Live Cancel Lab opened. Manual mode waits for the selected target input, then uses the action mailbox. "
            "Auto mode remains a timing probe."
        )
        self._schedule_poll()

    def _label_for_move(self, move: dict[str, Any] | None) -> str:
        if not isinstance(move, dict):
            return ""
        action_id = _as_int(move.get("id"), -1)
        address = _as_int(move.get("abs"), 0)
        for label, candidate in self.move_by_label.items():
            if candidate is move:
                return label
            if _as_int(candidate.get("id"), -2) == action_id:
                if not address or _as_int(candidate.get("abs"), 0) == address:
                    return label
        return ""

    def _build_ui(self) -> None:
        shell = ttk.Frame(self.window, style="FD.TFrame", padding=(12, 12))
        shell.pack(fill="both", expand=True)

        hero = ttk.Frame(shell, style="Hero.TFrame", padding=(14, 12))
        hero.pack(fill="x", pady=(0, 10))
        ttk.Label(hero, text="LIVE CANCEL LAB", style="HeroTitle.TLabel").pack(anchor="w")
        ttk.Label(
            hero,
            text=(
                "Manual input mode waits for TvC to recognize the selected target command, then routes it through "
                "the action mailbox during the custom frame window. Normal inputs come from the live 24-byte command "
                "table. Special and super inputs come from TvC's recognized-command candidate fields. Auto force remains "
                "available only as a timing probe."
            ),
            style="HeroSub.TLabel",
            wraplength=880,
            justify="left",
        ).pack(anchor="w", pady=(3, 0))

        route = ttk.Frame(shell, style="Card.TFrame", padding=(12, 10))
        route.pack(fill="x", pady=(0, 10))
        route.grid_columnconfigure(1, weight=1)

        ttk.Label(route, text="Source action", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        self.source_combo = ttk.Combobox(route, textvariable=self.source_var, values=self.labels, state="readonly")
        self.source_combo.grid(row=0, column=1, columnspan=5, sticky="ew", pady=4)
        self.source_combo.bind("<<ComboboxSelected>>", lambda _event: self._load_saved_window_for_source())

        ttk.Label(route, text="Target action", style="Card.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        self.target_combo = ttk.Combobox(route, textvariable=self.target_var, values=self.labels, state="readonly")
        self.target_combo.grid(row=1, column=1, columnspan=5, sticky="ew", pady=4)

        ttk.Label(route, text="Earliest frame", style="Card.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=(8, 4))
        self.earliest_entry = ttk.Entry(route, textvariable=self.earliest_var, width=8)
        self.earliest_entry.grid(row=2, column=1, sticky="w", pady=(8, 4))
        ttk.Label(route, text="Latest frame", style="Card.TLabel").grid(row=2, column=2, sticky="w", padx=(18, 8), pady=(8, 4))
        self.latest_entry = ttk.Entry(route, textvariable=self.latest_var, width=8)
        self.latest_entry.grid(row=2, column=3, sticky="w", pady=(8, 4))
        ttk.Label(route, text="0 = until source ends", style="CardMuted.TLabel").grid(row=2, column=4, columnspan=2, sticky="w", padx=(8, 0), pady=(8, 4))

        ttk.Label(route, text="Trigger mode", style="Card.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=(8, 2)
        )
        self.manual_radio = ttk.Radiobutton(
            route,
            text="Manual target input",
            variable=self.mode_var,
            value="manual",
            command=self._sync_mode_text,
        )
        self.manual_radio.grid(row=3, column=1, columnspan=2, sticky="w", pady=(8, 2))
        self.auto_radio = ttk.Radiobutton(
            route,
            text="Auto force timing probe",
            variable=self.mode_var,
            value="auto",
            command=self._sync_mode_text,
        )
        self.auto_radio.grid(row=3, column=3, columnspan=3, sticky="w", pady=(8, 2))

        self.pulse_check = ttk.Checkbutton(route, text="Pulse request through the window (auto only)", variable=self.pulse_var)
        self.pulse_check.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 2))
        self.repeat_check = ttk.Checkbutton(route, text="Repeat on every source use", variable=self.repeat_var)
        self.repeat_check.grid(row=4, column=3, columnspan=3, sticky="w", pady=(6, 2))
        self.auto_save_check = ttk.Checkbutton(route, text="Save accepted window to Frame Data", variable=self.auto_save_var)
        self.auto_save_check.grid(row=5, column=0, columnspan=6, sticky="w", pady=(4, 2))
        self._route_controls = [
            self.source_combo, self.target_combo, self.earliest_entry, self.latest_entry,
            self.manual_radio, self.auto_radio, self.pulse_check, self.repeat_check, self.auto_save_check,
        ]

        actions = ttk.Frame(shell, style="FD.TFrame")
        actions.pack(fill="x", pady=(0, 10))
        self.arm_button = ttk.Button(actions, textvariable=self.arm_button_text, command=self.toggle_arm)
        self.arm_button.pack(side="left")
        ttk.Button(actions, text="Force target now", command=self.request_now).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Save window to Frame Data", command=self.save_window_to_profile).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Clear request", command=lambda: self._clear_request("Request cleared manually.")).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Reset counts", command=self.reset_counts).pack(side="left", padx=(6, 0))
        ttk.Button(actions, text="Close", command=self.close).pack(side="right")

        live = ttk.Frame(shell, style="Card.TFrame", padding=(12, 10))
        live.pack(fill="x", pady=(0, 10))
        ttk.Label(live, text="LIVE STATE", style="Section.TLabel").pack(anchor="w")
        ttk.Label(live, textvariable=self.telemetry_var, style="Card.TLabel", wraplength=880, justify="left").pack(anchor="w", pady=(6, 0))
        ttk.Label(live, textvariable=self.status_var, style="HeroSub.TLabel", wraplength=880, justify="left").pack(anchor="w", pady=(6, 0))
        ttk.Label(live, textvariable=self.counts_var, style="CardMuted.TLabel").pack(anchor="w", pady=(5, 0))

        log_frame = ttk.Frame(shell, style="Card.TFrame", padding=(10, 8))
        log_frame.pack(fill="both", expand=True)
        ttk.Label(log_frame, text="TEST LOG", style="Section.TLabel").pack(anchor="w", pady=(0, 6))
        text_frame = ttk.Frame(log_frame, style="Card.TFrame")
        text_frame.pack(fill="both", expand=True)
        self.log_text = tk.Text(
            text_frame,
            height=14,
            wrap="word",
            bg="#0B111A",
            fg="#D9E7F5",
            insertbackground="#D9E7F5",
            relief="flat",
            borderwidth=0,
            font=("Consolas", 9),
            state="disabled",
        )
        scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _sync_mode_text(self) -> None:
        mode = str(self.mode_var.get() or "manual").strip().lower()
        if not self.armed:
            self.arm_button_text.set("Arm manual cancel" if mode == "manual" else "Arm auto force")
        try:
            self.pulse_check.configure(state="normal" if mode == "auto" and not self.armed else "disabled")
        except Exception:
            pass

    def _target_kind_for_id(self, target_id: int) -> str:
        target = int(target_id) & 0xFFFF
        for move in self.moves:
            if _as_int(move.get("id"), -1) == target:
                return FCM.move_kind(move)
        if 0x130 <= target < 0x160:
            return "special"
        if 0x160 <= target < 0x180:
            return "super"
        if 0x100 <= target < 0x130:
            return "normal"
        return "other"

    def _load_saved_window_for_source(self, announce: bool = True) -> None:
        source = self._selected_move(self.source_var)
        source_id = _as_int((source or {}).get("id"), -1)
        if source_id < 0:
            return
        saved = FCW.get_window(self.char_name, source_id)
        if not saved:
            if announce:
                self._announce(f"No saved custom cancel window for 0x{source_id:04X}.")
            return
        self.earliest_var.set(str(int(saved.get("earliest", DEFAULT_EARLIEST_FRAME) or DEFAULT_EARLIEST_FRAME)))
        self.latest_var.set(str(int(saved.get("latest", 0) or 0)))
        if announce:
            self._announce(f"Loaded custom cancel window {FCW.format_window(saved)} for 0x{source_id:04X}.")

    def save_window_to_profile(self, *, automatic: bool = False) -> bool:
        if self.armed:
            source_id = int(self.armed_source_id)
            target_id = int(self.armed_target_id)
            earliest = int(self.armed_earliest)
            latest = int(self.armed_latest)
        else:
            ids = self._selected_ids(show_error=not automatic)
            values = self._window_values(show_error=not automatic)
            if ids is None or values is None:
                return False
            source_id, target_id = ids
            earliest, latest = values
        saved = FCW.set_window(
            self.char_name, source_id, earliest, latest,
            source="Live Cancel Lab", tested_target_id=target_id,
        )
        if not saved:
            message = "Could not save the custom cancel window."
            if not automatic:
                messagebox.showerror("Live Cancel Lab", message, parent=self.window)
            self._announce(message)
            self._log(message)
            return False
        message = (
            f"Saved custom cancel window {FCW.format_window(saved)} for source 0x{source_id:04X} "
            f"after testing target 0x{target_id:04X}."
        )
        self._announce(message)
        self._log(message)
        if callable(self.profile_refresh_callback):
            try:
                self.profile_refresh_callback(source_id)
            except Exception:
                pass
        return True

    def _set_route_controls_enabled(self, enabled: bool) -> None:
        for widget in self._route_controls:
            try:
                if widget in (self.source_combo, self.target_combo):
                    widget.configure(state="readonly" if enabled else "disabled")
                else:
                    widget.configure(state="normal" if enabled else "disabled")
            except Exception:
                pass
        if enabled:
            self._sync_mode_text()

    def _selected_move(self, variable: tk.StringVar) -> dict[str, Any] | None:
        move = self.move_by_label.get(variable.get())
        return move if isinstance(move, dict) else None

    def _selected_ids(self, show_error: bool = False) -> tuple[int, int] | None:
        source = self._selected_move(self.source_var)
        target = self._selected_move(self.target_var)
        source_id = _as_int((source or {}).get("id"), -1)
        target_id = _as_int((target or {}).get("id"), -1)
        if source_id < 0 or target_id < 0:
            if show_error:
                messagebox.showerror("Live Cancel Lab", "Choose a source and target with valid action IDs.", parent=self.window)
            return None
        if source_id == target_id:
            if show_error:
                messagebox.showerror("Live Cancel Lab", "Source and target must be different actions.", parent=self.window)
            return None
        return source_id & 0xFFFF, target_id & 0xFFFF

    def _window_values(self, show_error: bool = False) -> tuple[int, int] | None:
        try:
            earliest = max(0, int(str(self.earliest_var.get()).strip() or "0", 0))
            latest = max(0, int(str(self.latest_var.get()).strip() or "0", 0))
        except Exception:
            if show_error:
                messagebox.showerror("Live Cancel Lab", "Frames must be whole numbers. Use 0 for no latest-frame limit.", parent=self.window)
            return None
        if latest and latest < earliest:
            if show_error:
                messagebox.showerror("Live Cancel Lab", "Latest frame cannot be earlier than earliest frame.", parent=self.window)
            return None
        return earliest, latest

    def _resolve_base(self) -> int:
        # The live slot pointer wins. Workbench snapshots can outlive a round or
        # character change, so their cached fighter base is fallback-only.
        pointer = SLOT_POINTERS.get(self.slot_label, SLOT_POINTERS["P1-C1"])
        base = _read_u32(pointer, 0)
        if _valid_fighter_base(base):
            return base
        for key in ("fighter_base", "base", "ea"):
            value = self.target_slot.get(key)
            if _valid_fighter_base(value):
                return int(value)
        return 0

    def _announce(self, text: str) -> None:
        self.last_result = str(text)
        self.status_var.set(self.last_result)
        if callable(self.status_callback):
            try:
                self.status_callback(self.last_result)
            except Exception:
                pass

    def _log(self, text: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", f"[{stamp}] {text}\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

    def _update_counts(self) -> None:
        self.counts_var.set(
            f"Attempts {self.attempt_count} | Accepted {self.accept_count} | Rejected {self.reject_count}"
        )

    def _clear_request(self, reason: str = "", announce: bool = True) -> bool:
        cleared = False
        if self.request_addr and self.request_value:
            current = _read_u32(self.request_addr, 0)
            if current == self.request_value:
                cleared = _write_u32(self.request_addr, 0)
        self.request_pending = False
        self.request_addr = 0
        self.request_value = 0
        self.request_source_id = 0
        self.request_target_id = 0
        self.request_source_frame = 0
        self.request_started_at = 0.0
        self.manual_deadline = 0.0
        if reason:
            if announce:
                self._announce(reason)
            self._log(reason + ("" if cleared else " No matching pending mailbox word remained."))
        return cleared

    def _write_target_request(
        self,
        base: int,
        source_id: int,
        target_id: int,
        frame: int,
        reason: str,
    ) -> bool:
        request_addr = int(base) + OFF_ACTION_REQUEST
        mailbox = _read_u32(request_addr, 0)
        encoded = mailbox_value_for_action(target_id)
        if mailbox not in (0, encoded):
            self._announce(f"Mailbox busy at 0x{request_addr:08X}: 0x{mailbox:08X}")
            return False
        if mailbox == encoded:
            self.request_pending = True
            self.request_addr = request_addr
            self.request_value = encoded
            self.request_source_id = int(source_id) & 0xFFFF
            self.request_target_id = int(target_id) & 0xFFFF
            self.request_source_frame = max(0, int(frame))
            self.request_started_at = time.monotonic()
            return True
        if not _write_u32(request_addr, encoded):
            self._announce(f"Write failed at action mailbox 0x{request_addr:08X}.")
            return False

        self.request_pending = True
        self.request_addr = request_addr
        self.request_value = encoded
        self.request_source_id = int(source_id) & 0xFFFF
        self.request_target_id = int(target_id) & 0xFFFF
        self.request_source_frame = max(0, int(frame))
        self.request_started_at = time.monotonic()
        self.pulses_this_source += 1
        self._log(
            f"{reason}: wrote target 0x{target_id:04X} as 0x{encoded:08X} at "
            f"0x{request_addr:08X}, source frame {frame}."
        )
        return True

    def toggle_arm(self) -> None:
        if self.armed:
            self.disarm("Cancel rule disarmed.")
            return
        if self._selected_ids(show_error=True) is None or self._window_values(show_error=True) is None:
            return
        self._clear_request(announce=False)
        source_id, target_id = self._selected_ids() or (0, 0)
        earliest, latest = self._window_values() or (0, 0)
        mode = str(self.mode_var.get() or "manual").strip().lower()
        if mode not in {"manual", "auto"}:
            mode = "manual"
        self.armed_source_id = source_id
        self.armed_target_id = target_id
        self.armed_target_kind = self._target_kind_for_id(target_id)
        self.armed_earliest = earliest
        self.armed_latest = latest
        self.armed_mode = mode
        self.armed = True
        self.was_in_source = False
        self.source_started_at = 0.0
        self.completed_for_source = False
        self.pulses_this_source = 0
        self._last_trigger_signature = None
        self._last_special_evidence = None
        self.arm_button_text.set("Disarm manual cancel" if mode == "manual" else "Disarm auto force")
        self._set_route_controls_enabled(False)
        limit_text = str(latest) if latest else "source end"
        mode_text = "manual target-input" if mode == "manual" else "automatic timing-probe"
        self._announce(
            f"Armed {mode_text} route 0x{source_id:04X} to 0x{target_id:04X}, "
            f"frames {earliest} through {limit_text}."
        )
        self._log(self.last_result)

    def disarm(self, reason: str = "Cancel rule disarmed.") -> None:
        self.armed = False
        self.arm_button_text.set(
            "Arm manual cancel" if str(self.mode_var.get() or "manual").lower() == "manual" else "Arm auto force"
        )
        self._set_route_controls_enabled(True)
        self._clear_request(announce=False)
        self._announce(reason)
        self._log(reason)

    def request_now(self) -> None:
        ids = self._selected_ids(show_error=True)
        if ids is None:
            return
        base = self._resolve_base()
        if not base:
            self._announce(f"Waiting for {self.slot_label} fighter base.")
            return
        source_id, target_id = ids
        current = _read_u32(base + OFF_ACTION_ID, 0)
        if current != source_id:
            message = (
                f"Request blocked: current action is 0x{current:04X}, not selected source 0x{source_id:04X}. "
                "Perform the source move first, then press the button during it."
            )
            self._announce(message)
            self._log(message)
            return
        now = time.monotonic()
        if not self.was_in_source or self.source_started_at <= 0.0:
            self.was_in_source = True
            self.source_started_at = now
        frame = elapsed_source_frame(self.source_started_at, now)
        self._clear_request(announce=False)
        self.attempt_count += 1
        self._update_counts()
        self.last_action = current
        if self._write_target_request(base, source_id, target_id, frame, "Manual source-active request"):
            self.manual_deadline = time.monotonic() + 0.75
            self._announce(
                f"Requested 0x{target_id:04X} while source 0x{source_id:04X} is active at frame {frame}."
            )
        else:
            self.reject_count += 1
            self._update_counts()

    def reset_counts(self) -> None:
        self.attempt_count = 0
        self.accept_count = 0
        self.reject_count = 0
        self._update_counts()
        self._announce("Test counts reset.")
        self._log("Test counts reset.")

    def _finish_accept(self, target_id: int) -> None:
        request_frame = int(self.request_source_frame or 0)
        request_source = int(self.request_source_id or 0)
        elapsed_ms = 0
        if self.request_started_at > 0.0:
            elapsed_ms = max(0, int((time.monotonic() - self.request_started_at) * 1000.0))
        self.accept_count += 1
        self._update_counts()
        self._clear_request(announce=False)
        self.completed_for_source = True
        message = (
            f"ACCEPTED: mailbox transitioned 0x{request_source:04X} to 0x{target_id:04X}; "
            f"request was issued at source frame {request_frame} ({elapsed_ms} ms ago)."
        )
        self._announce(message)
        self._log(message)
        if bool(self.auto_save_var.get()):
            self.save_window_to_profile(automatic=True)
        if self.armed and not bool(self.repeat_var.get()):
            self.armed = False
            self.arm_button_text.set(
                "Arm manual cancel" if str(self.mode_var.get() or "manual").lower() == "manual" else "Arm auto force"
            )
            self._set_route_controls_enabled(True)

    def _finish_reject(self, message: str) -> None:
        self.reject_count += 1
        self._update_counts()
        self._clear_request(announce=False)
        self.completed_for_source = True
        self._announce(message)
        self._log(message)
        if self.armed and not bool(self.repeat_var.get()):
            self.armed = False
            self.arm_button_text.set(
                "Arm manual cancel" if str(self.mode_var.get() or "manual").lower() == "manual" else "Arm auto force"
            )
            self._set_route_controls_enabled(True)

    def _tick(self) -> None:
        if self._closing:
            return
        try:
            base = self._resolve_base()
            self.last_base = base
            if not base:
                self.telemetry_var.set(f"Slot {self.slot_label} | fighter base unavailable | no writes")
                self._schedule_poll()
                return

            now = time.monotonic()
            current_action = _read_u32(base + OFF_ACTION_ID, 0)
            mailbox = _read_u32(base + OFF_ACTION_REQUEST, 0)
            engine_frame = read_frame_snapshot(base)
            held_word = _read_u32(base + OFF_INPUT_HELD, 0)
            pressed_word = _read_u32(base + OFF_INPUT_PRESSED, 0)
            special_actions, special_evidence = recognized_special_actions(base)

            selected_ids = self._selected_ids(show_error=False)
            selected_source, selected_target = selected_ids if selected_ids is not None else (-1, -1)
            if self.armed:
                source_id = self.armed_source_id
                target_id = self.armed_target_id
                target_kind = self.armed_target_kind
                earliest = self.armed_earliest
                latest = self.armed_latest
                mode = self.armed_mode
            else:
                source_id, target_id = selected_source, selected_target
                target_kind = self._target_kind_for_id(target_id)
                earliest, latest = self._window_values(show_error=False) or (0, 0)
                mode = str(self.mode_var.get() or "manual").strip().lower()

            in_source = current_action == source_id
            if in_source and not self.was_in_source:
                self.was_in_source = True
                self.source_started_at = now
                self.completed_for_source = False
                self.pulses_this_source = 0
                self._last_trigger_signature = None
                self._source_special_baseline = set(special_actions)
                self._source_pressed_baseline = pressed_word
                self._log(
                    f"Source 0x{source_id:04X} entered; cancel frame counter reset to 1 "
                    f"(engine raw {engine_frame['frame_a']}/{engine_frame['frame_b']})."
                )

            source_frame = elapsed_source_frame(self.source_started_at, now) if in_source else 0
            self.last_frame = source_frame
            frame_text = str(source_frame) if in_source else "-"
            locked_text = (
                f"locked {mode} 0x{source_id:04X}->0x{target_id:04X}" if self.armed else "not armed"
            )
            special_text = ",".join(f"0x{action:04X}" for action in sorted(special_actions)) or "-"
            self.telemetry_var.set(
                f"Slot {self.slot_label} | base 0x{base:08X} | action 0x{current_action:04X} | "
                f"source age {frame_text}f | input held 0x{held_word:08X} pressed 0x{pressed_word:08X} | "
                f"special candidates {special_text} "
                f"(0x{special_evidence['cooked']:08X}/0x{special_evidence['raw']:08X}) | "
                f"mailbox 0x{mailbox:08X} | {locked_text}"
            )

            expected_target = int(self.request_target_id or target_id)
            if self.request_pending and current_action == expected_target:
                self._finish_accept(expected_target)

            if self.request_pending and self.manual_deadline and now >= self.manual_deadline:
                self._finish_reject(
                    f"REJECTED: target 0x{expected_target:04X} was not consumed before the safety timeout."
                )

            if self.was_in_source and not in_source:
                self.was_in_source = False
                self.source_started_at = 0.0
                if self.request_pending and current_action != expected_target:
                    self._finish_reject(
                        f"REJECTED: source 0x{source_id:04X} ended into 0x{current_action:04X}, "
                        f"not target 0x{expected_target:04X}."
                    )
                self.pulses_this_source = 0
                self._source_special_baseline.clear()
                self._source_pressed_baseline = 0

            in_window = (
                self.armed
                and in_source
                and not self.completed_for_source
                and frame_in_window(source_frame, earliest, latest)
            )

            if in_window and mode == "manual" and not self.request_pending:
                if target_kind in {"special", "super"} and target_id not in special_actions:
                    self._source_special_baseline.discard(target_id)
                trigger = manual_target_trigger(base, target_id, target_kind)
                if trigger and target_kind in {"special", "super"}:
                    # Do not consume a stale special candidate that was already
                    # present when the source action began. A fresh command must
                    # appear during the armed source window.
                    if target_id in self._source_special_baseline:
                        trigger = None
                    else:
                        signature = (
                            "special",
                            target_id,
                            special_evidence.get("cooked", 0),
                            special_evidence.get("raw", 0),
                        )
                        if signature == self._last_trigger_signature:
                            trigger = None
                        else:
                            self._last_trigger_signature = signature
                elif trigger:
                    row = trigger.get("row") or {}
                    signature = (
                        "normal",
                        target_id,
                        int(row.get("addr", 0)),
                        pressed_word,
                        held_word & 0xF,
                    )
                    if signature == self._last_trigger_signature:
                        trigger = None
                    else:
                        self._last_trigger_signature = signature

                if trigger:
                    self.attempt_count += 1
                    self._update_counts()
                    detail = str(trigger.get("detail") or trigger.get("kind") or "target input")
                    if self._write_target_request(
                        base,
                        source_id,
                        target_id,
                        source_frame,
                        f"Manual input recognized ({detail})",
                    ):
                        self.manual_deadline = now + 0.35
                        self._announce(
                            f"Recognized target input for 0x{target_id:04X} at source frame {source_frame}; "
                            "requesting the manual cancel."
                        )
                    else:
                        self.reject_count += 1
                        self._update_counts()

            if in_window and mode == "auto":
                should_write = not self.request_pending
                if bool(self.pulse_var.get()) and mailbox == 0:
                    should_write = True
                if should_write and self.pulses_this_source < MAX_PULSES_PER_SOURCE:
                    first_pulse = self.pulses_this_source == 0
                    if self._write_target_request(
                        base, source_id, target_id, source_frame, "Armed auto pulse"
                    ):
                        if first_pulse:
                            self.attempt_count += 1
                            self._update_counts()
                            self._announce(
                                f"Auto timing probe 0x{source_id:04X} to 0x{target_id:04X} "
                                f"at source frame {source_frame}."
                            )
                        if not bool(self.pulse_var.get()):
                            self.completed_for_source = True

            if (
                self.armed
                and in_source
                and not self.completed_for_source
                and latest > 0
                and source_frame > latest
            ):
                if mode == "manual" and not self.request_pending:
                    self.completed_for_source = True
                    message = (
                        f"WINDOW EXPIRED: no matching input for target 0x{target_id:04X} was recognized "
                        f"during source frames {earliest}-{latest}."
                    )
                    self._announce(message)
                    self._log(message)
                else:
                    self._finish_reject(
                        f"REJECTED: source-age window {earliest}-{latest} expired before "
                        f"target 0x{target_id:04X} was accepted."
                    )

            self.last_action = current_action
        except Exception as exc:
            self._announce(f"Cancel Lab poll error: {exc!r}")
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        if self._closing:
            return
        try:
            self._after_id = self.window.after(POLL_MS, self._tick)
        except Exception:
            self._after_id = None

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        self.armed = False
        try:
            if self._after_id is not None:
                self.window.after_cancel(self._after_id)
        except Exception:
            pass
        self._clear_request(announce=False)
        if _ACTIVE_BY_SLOT.get(self.slot_label) is self:
            _ACTIVE_BY_SLOT.pop(self.slot_label, None)
        try:
            self.window.destroy()
        except Exception:
            pass


def open_cancel_lab(
    parent: tk.Misc,
    slot_label: str,
    target_slot: dict[str, Any] | None,
    moves: Sequence[dict[str, Any]],
    source_move: dict[str, Any] | None = None,
    target_move: dict[str, Any] | None = None,
    status_callback: Callable[[str], None] | None = None,
    profile_refresh_callback: Callable[[int | None], None] | None = None,
) -> CancelLabWindow:
    normalized_slot = normalize_slot_label(slot_label)
    existing = _ACTIVE_BY_SLOT.get(normalized_slot)
    if existing is not None:
        try:
            existing.close()
        except Exception:
            pass
    opened = CancelLabWindow(
        parent=parent,
        slot_label=normalized_slot,
        target_slot=target_slot,
        moves=moves,
        source_move=source_move,
        target_move=target_move,
        status_callback=status_callback,
        profile_refresh_callback=profile_refresh_callback,
    )
    _ACTIVE_BY_SLOT[normalized_slot] = opened
    return opened
