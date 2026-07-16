from __future__ import annotations

import csv
import os
import struct
import time
from collections import deque
import tkinter as tk
from dataclasses import dataclass, asdict
from tkinter import filedialog, ttk
from typing import Any

from tvcgui.core.action_event_bus import action_events_since, current_action_event_sequence
from tvcgui.core.tk_host import tk_call
from tvcgui.features.combat.moves import CHAR_ID_CORRECTION, move_label_for
from tvcgui.features.frame_data.widgets import apply_titlebar_icon

try:
    from tvcgui.platform.dolphin import rd32
except Exception:
    rd32 = None


SLOT_POINTERS = {
    "P1-C1": 0x803C9FCC,
    "P1-C2": 0x803C9FDC,
    "P2-C1": 0x803C9FD4,
    "P2-C2": 0x803C9FE4,
}

OFF_CHAR_ID = 0x0014
OFF_ACTION_ID = 0x01E8
OFF_ACTION_REQUEST = 0x0200
OFF_ANIM_FRAME_FLOAT = 0x01D8
OFF_FRAME_A = 0x021C
OFF_FRAME_B = 0x0220
OFF_INPUT_HELD = 0x13CC
OFF_INPUT_PRESSED = 0x13D8
OFF_COMMAND_TABLE = 0x13E8
OFF_SPECIAL_FLAGS = 0x1990
OFF_SPECIAL_CANDIDATE = 0x1994
OFF_RAW_SPECIAL_FLAGS = 0x2108
OFF_RAW_SPECIAL_CANDIDATE = 0x210C
OFF_RAW_SPECIAL_METADATA = 0x2110
OFF_RAW_SPECIAL_PARAM = 0x2114

COMMAND_ROW_SIZE = 24
COMMAND_ROW_LIMIT = 192
POLL_MS = 8
UI_REFRESH_SEC = 1.0 / 30.0
COMMAND_TIMEOUT_SEC = 0.28
RECENT_EVIDENCE_SEC = 0.42
COMMAND_BUFFER_SEC = 0.48
MAX_COMMAND_SAMPLES = 160
RAW_PACKET_BUFFER_SEC = 0.90
MAX_RAW_PROBE_RECORDS = 4000
CANCEL_EVENT_BUFFER_SEC = 1.50
MAX_CANCEL_EVENTS = 256

_ACTIVE_WINDOW: "ActionRecorderWindow | None" = None


@dataclass
class TransitionRecord:
    index: int
    timestamp: str
    elapsed: float
    slot: str
    character: str
    char_id: int
    source_id: int
    source_name: str
    target_id: int
    target_name: str
    source_frame: int
    raw_frame_a: int
    raw_frame_b: int
    cause: str
    result: str
    input_held: int
    input_pressed: int
    special_cooked: int
    special_raw: int
    mailbox_raw: int
    mailbox_target: int
    note: str
    special_flags: int = 0
    raw_special_flags: int = 0
    raw_metadata: int = 0
    raw_param_word: int = 0
    raw_param_float: float | None = None
    raw_evidence_age_ms: float = -1.0


@dataclass
class RawProbeRecord:
    index: int
    timestamp: str
    elapsed: float
    slot: str
    character: str
    char_id: int
    action_id: int
    action_name: str
    input_held: int
    input_pressed: int
    cooked_flags: int
    cooked_index: int
    raw_flags: int
    raw_index: int
    raw_metadata: int
    raw_param_word: int
    raw_param_float: float | None
    mailbox_raw: int
    mailbox_target: int
    changed_fields: str


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _valid_base(value: Any) -> bool:
    base = _as_int(value, 0)
    return 0x90000000 <= base < 0x94000000 and (base & 0x3) == 0


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


def _signed_u16(value: int) -> int:
    value_i = int(value) & 0xFFFF
    return value_i - 0x10000 if value_i & 0x8000 else value_i


def mailbox_target_from_value(value: int) -> int | None:
    raw = int(value) & 0xFFFFFFFF
    if raw == 0:
        return None
    return (raw + 0x4000) & 0xFFFF


def special_action_from_candidate(value: int) -> int | None:
    raw = int(value) & 0xFFFFFFFF
    if raw in (0, 0xFFFFFFFF) or not (0 < raw < 0x3F00):
        return None
    return (raw + 0x100) & 0xFFFF


def command_index_for_action(action_id: int) -> int | None:
    action = int(action_id) & 0xFFFF
    if 0x010F <= action < 0x4000:
        return (action - 0x0100) & 0xFFFFFFFF
    return None


def _raw_packet_active(packet: dict[str, Any]) -> bool:
    cooked = _as_int(packet.get("cooked"), 0) & 0xFFFFFFFF
    raw = _as_int(packet.get("raw"), 0xFFFFFFFF) & 0xFFFFFFFF
    cooked_flags = _as_int(packet.get("cooked_flags"), 0) & 0xFFFFFFFF
    raw_flags = _as_int(packet.get("raw_flags"), 0) & 0xFFFFFFFF
    metadata = _as_int(packet.get("raw_metadata"), 0) & 0xFFFFFFFF
    param_word = _as_int(packet.get("raw_param_word"), 0) & 0xFFFFFFFF
    return any(
        (
            cooked not in (0, 0xFFFFFFFF),
            raw not in (0, 0xFFFFFFFF),
            cooked_flags not in (0, 0xFFFFFFFF),
            raw_flags not in (0, 0xFFFFFFFF),
            metadata not in (0, 0xFFFFFFFF),
            param_word not in (0, 0xFFFFFFFF),
        )
    )


def _packet_matches_target(packet: dict[str, Any], target_id: int) -> bool:
    expected = command_index_for_action(target_id)
    if expected is None:
        return False
    return expected in {
        _as_int(packet.get("cooked"), -1) & 0xFFFFFFFF,
        _as_int(packet.get("raw"), -1) & 0xFFFFFFFF,
    }


def _packet_changed_fields(previous: tuple[int, ...] | None, current: tuple[int, ...]) -> str:
    names = ("cooked flags", "cooked index", "raw flags", "raw index", "metadata", "parameter", "mailbox")
    if previous is None:
        return ", ".join(names)
    changed = [name for name, before, after in zip(names, previous, current) if before != after]
    return ", ".join(changed) if changed else "none"


def classify_transition(
    source_id: int,
    target_id: int,
    *,
    mailbox_target: int | None,
    recognized_targets: set[int] | None,
    input_pressed: int,
) -> str:
    source = int(source_id) & 0xFFFF
    target = int(target_id) & 0xFFFF
    recognized = set(int(value) & 0xFFFF for value in (recognized_targets or set()))
    if mailbox_target is not None and (int(mailbox_target) & 0xFFFF) == target:
        if source >= 0x100 and target >= 0x100:
            return "Mailbox / custom cancel"
        return "Mailbox force"
    if target in recognized:
        return "Recognized command"
    if int(input_pressed) and target >= 0x100:
        return "Input / stock route"
    if target < 0x100:
        return "System / reaction"
    return "Unknown"


def _float_from_word(word: int) -> float | None:
    try:
        value = struct.unpack(">f", struct.pack(">I", int(word) & 0xFFFFFFFF))[0]
    except Exception:
        return None
    if value != value or value < 0.0 or value > 10000.0:
        return None
    return float(value)


def _frame_snapshot(base: int) -> dict[str, int | float | None]:
    frame_a = _read_u32(base + OFF_FRAME_A, 0)
    frame_b = _read_u32(base + OFF_FRAME_B, 0)
    anim_word = _read_u32(base + OFF_ANIM_FRAME_FLOAT, 0)
    return {
        "frame_a": frame_a,
        "frame_b": frame_b,
        "anim_word": anim_word,
        "anim_float": _float_from_word(anim_word),
    }


def _recognized_normal_actions(base: int, held: int, pressed: int) -> list[dict[str, int]]:
    if not int(pressed):
        return []
    table = _read_u32(int(base) + OFF_COMMAND_TABLE, 0)
    if not _valid_base(table):
        return []
    live_direction = int(held) & 0xF
    matches: list[dict[str, int]] = []
    seen: set[int] = set()
    for index in range(COMMAND_ROW_LIMIT):
        row_addr = table + index * COMMAND_ROW_SIZE
        word0 = _read_u32(row_addr, 0xFFFFFFFF)
        row_type = _signed_u16(word0 >> 16)
        if row_type == -1:
            break
        if row_type != 6:
            continue
        direction = _read_u32(row_addr + 4, 0) & 0xF
        buttons = _read_u32(row_addr + 8, 0)
        target = _read_u32(row_addr + 20, 0) & 0xFFFF
        if not buttons or target in seen:
            continue
        if direction != live_direction or (int(pressed) & buttons) != buttons:
            continue
        seen.add(target)
        matches.append(
            {
                "target": target,
                "row": row_addr,
                "index": index,
                "direction": direction,
                "buttons": buttons,
            }
        )
    return matches


def _character_name(char_id: int) -> str:
    cid = int(char_id)
    for name, mapped in CHAR_ID_CORRECTION.items():
        if int(mapped) == cid:
            return str(name)
    return f"Character {cid}" if cid >= 0 else "Unknown"


def _normalized_action_label(label: str) -> str:
    return " ".join(str(label or "").strip().lower().replace("_", " ").split())


def action_environment(action_id: int, label: str = "") -> str:
    """Return the best known ground or air context for a live action."""
    action = int(action_id) & 0xFFFF
    text = _normalized_action_label(label)
    if "landing" in text or "land recovery" in text:
        return "ground"
    if 0x0109 <= action <= 0x010D:
        return "air"
    if text.startswith("j.") or text.startswith("air ") or "air dash" in text:
        return "air"
    if any(token in text for token in ("jump", "fall", "flight", "hop", "airborne")):
        return "air"
    if action in {0x0013, 0x0014, 0x0015, 0x0016, 0x0017, 0x0018, 0x0019, 0x001A, 0x001B, 0x001C, 0x001D, 0x001F, 0x0022, 0x0023, 0x0024}:
        return "air"
    return "ground"


def normal_target_environment(action_id: int, label: str = "") -> str:
    action = int(action_id) & 0xFFFF
    text = _normalized_action_label(label)
    if 0x0109 <= action <= 0x010D or text.startswith("j.") or text.startswith("air "):
        return "air"
    if 0x0100 <= action <= 0x011F:
        return "ground"
    return "either"


def normal_candidate_matches_context(source_id: int, source_label: str, target_id: int, target_label: str) -> bool:
    target_env = normal_target_environment(target_id, target_label)
    if target_env == "either":
        return True
    return target_env == action_environment(source_id, source_label)


class ActionRecorderWindow:
    def __init__(
        self,
        parent: tk.Misc,
        move_map: dict[int, dict[int, str]] | None,
        global_map: dict[int, str] | None,
        initial_slot: str = "P1-C1",
    ) -> None:
        self.parent = parent
        self.move_map = move_map if isinstance(move_map, dict) else {}
        self.global_map = global_map if isinstance(global_map, dict) else {}
        self.slot_label = initial_slot if initial_slot in SLOT_POINTERS else "P1-C1"
        self.window = tk.Toplevel(parent)
        apply_titlebar_icon(self.window, parent)
        self.window.title("Action Recorder")
        self.window.geometry("1180x760")
        self.window.minsize(860, 560)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.configure(bg="#08111d")

        self.recording = True
        self.log_rejected = True
        self.records: list[TransitionRecord] = []
        self.transition_records: list[TransitionRecord] = []
        self.attempt_records: list[TransitionRecord] = []
        self.probe_records: list[RawProbeRecord] = []
        self._tree_record_by_item: dict[str, TransitionRecord] = {}
        self._attempt_record_by_item: dict[str, TransitionRecord] = {}
        self._probe_record_by_item: dict[str, RawProbeRecord] = {}
        self._after_id: str | None = None
        self._closing = False
        self._started_at = time.monotonic()
        self._last_base = 0
        self._last_action: int | None = None
        self._last_action_started_at = 0.0
        self._last_char_id = -1
        self._recent_mailbox: dict[str, Any] = {}
        self._recent_pressed: dict[str, Any] = {}
        self._recent_recognized: dict[int, dict[str, Any]] = {}
        self._pending_commands: dict[tuple[int, int], dict[str, Any]] = {}
        self._command_buffer: deque[dict[str, Any]] = deque(maxlen=MAX_COMMAND_SAMPLES)
        self._cancel_event_cursor = current_action_event_sequence()
        self._recent_cancel_events: deque[dict[str, Any]] = deque(maxlen=MAX_CANCEL_EVENTS)
        self._edge_serial = 0
        self._last_ui_refresh = 0.0
        self._last_normal_signature: tuple[Any, ...] | None = None
        self._last_special_signature: tuple[Any, ...] | None = None
        self._last_raw_packet_signature: tuple[int, ...] | None = None
        self._counts = {"transitions": 0, "mailbox": 0, "commands": 0, "attempts": 0, "probe": 0, "unknown": 0}

        self.slot_var = tk.StringVar(master=self.window, value=self.slot_label)
        self.record_button_var = tk.StringVar(master=self.window, value="Pause recording")
        self.status_var = tk.StringVar(master=self.window, value="Waiting for fighter data...")
        self.live_action_var = tk.StringVar(master=self.window, value="Action  ----")
        self.live_character_var = tk.StringVar(master=self.window, value="P1-C1  |  Waiting")
        self.live_frame_var = tk.StringVar(master=self.window, value="Frame --")
        self.live_input_var = tk.StringVar(master=self.window, value="Input held 00000000  pressed 00000000")
        self.live_command_var = tk.StringVar(master=self.window, value="Command cooked --------  raw --------")
        self.live_packet_var = tk.StringVar(master=self.window, value="Raw flags --------  meta --------  param --------")
        self.live_mailbox_var = tk.StringVar(master=self.window, value="Mailbox --------")
        self.count_var = tk.StringVar(master=self.window, value="Transitions 0  |  Commands 0  |  Mailbox 0  |  Attempts 0  |  Probe 0")
        self.detail_var = tk.StringVar(master=self.window, value="Select a recorded event for full evidence.")
        self.reject_var = tk.BooleanVar(master=self.window, value=True)

        self._configure_styles()
        self._build_ui()
        self._schedule_poll()

    def _configure_styles(self) -> None:
        style = ttk.Style(self.window)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("AR.Root.TFrame", background="#08111d")
        style.configure("AR.Card.TFrame", background="#0d1a29", borderwidth=1, relief="solid")
        style.configure("AR.Header.TLabel", background="#0d1a29", foreground="#f2f7ff", font=("Segoe UI Semibold", 17))
        style.configure("AR.Sub.TLabel", background="#0d1a29", foreground="#8fa6bb", font=("Segoe UI", 10))
        style.configure("AR.Section.TLabel", background="#0d1a29", foreground="#59d2ff", font=("Segoe UI Semibold", 10))
        style.configure("AR.Value.TLabel", background="#0d1a29", foreground="#e8f4ff", font=("Segoe UI Semibold", 11))
        style.configure("AR.Muted.TLabel", background="#0d1a29", foreground="#8298ac", font=("Segoe UI", 9))
        style.configure("AR.TButton", background="#16283a", foreground="#e8f4ff", bordercolor="#29465f", padding=(12, 8), font=("Segoe UI Semibold", 9))
        style.map("AR.TButton", background=[("active", "#1d3950"), ("pressed", "#102435")])
        style.configure("AR.Primary.TButton", background="#174b68", foreground="#ffffff", bordercolor="#39bce9", padding=(14, 8), font=("Segoe UI Semibold", 9))
        style.map("AR.Primary.TButton", background=[("active", "#1d6385"), ("pressed", "#123f57")])
        style.configure("AR.TCheckbutton", background="#0d1a29", foreground="#cbd9e6", font=("Segoe UI", 9))
        style.map("AR.TCheckbutton", background=[("active", "#0d1a29")])
        style.configure("AR.TCombobox", fieldbackground="#0a1623", background="#16283a", foreground="#eef7ff", arrowcolor="#59d2ff", bordercolor="#29465f", padding=6)
        style.configure("AR.TNotebook", background="#0d1a29", borderwidth=0)
        style.configure("AR.TNotebook.Tab", background="#102437", foreground="#9fb4c6", padding=(14, 8), font=("Segoe UI Semibold", 9))
        style.map("AR.TNotebook.Tab", background=[("selected", "#174b68"), ("active", "#15354b")], foreground=[("selected", "#ffffff")])
        style.configure(
            "AR.Treeview",
            background="#091521",
            fieldbackground="#091521",
            foreground="#dce8f3",
            rowheight=28,
            bordercolor="#20384d",
            font=("Segoe UI", 9),
        )
        style.map("AR.Treeview", background=[("selected", "#164766")], foreground=[("selected", "#ffffff")])
        style.configure("AR.Treeview.Heading", background="#102437", foreground="#91dcfa", bordercolor="#29465f", font=("Segoe UI Semibold", 9), padding=(6, 7))

    def _build_ui(self) -> None:
        root = ttk.Frame(self.window, style="AR.Root.TFrame", padding=12)
        root.pack(fill="both", expand=True)
        root.grid_columnconfigure(0, weight=1)
        root.grid_rowconfigure(2, weight=1)

        header = ttk.Frame(root, style="AR.Card.TFrame", padding=(16, 14))
        header.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        header.grid_columnconfigure(0, weight=1)
        ttk.Label(header, text="ACTION RECORDER", style="AR.Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Records clean action transitions in the main view, with filtered rejected command attempts kept separately.",
            style="AR.Sub.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))
        ttk.Label(header, textvariable=self.count_var, style="AR.Section.TLabel").grid(row=0, column=1, rowspan=2, sticky="e", padx=(18, 0))

        controls = ttk.Frame(root, style="AR.Card.TFrame", padding=(14, 12))
        controls.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        controls.grid_columnconfigure(8, weight=1)
        ttk.Label(controls, text="Fighter slot", style="AR.Section.TLabel").grid(row=0, column=0, sticky="w")
        slot_combo = ttk.Combobox(
            controls,
            textvariable=self.slot_var,
            values=list(SLOT_POINTERS),
            state="readonly",
            width=10,
            style="AR.TCombobox",
        )
        slot_combo.grid(row=1, column=0, sticky="w", pady=(5, 0), padx=(0, 10))
        slot_combo.bind("<<ComboboxSelected>>", self._on_slot_change)

        ttk.Button(controls, textvariable=self.record_button_var, command=self.toggle_recording, style="AR.Primary.TButton").grid(row=1, column=1, padx=(0, 7), pady=(5, 0))
        ttk.Button(controls, text="Clear", command=self.clear, style="AR.TButton").grid(row=1, column=2, padx=(0, 7), pady=(5, 0))
        ttk.Button(controls, text="Copy", command=self.copy_records, style="AR.TButton").grid(row=1, column=3, padx=(0, 7), pady=(5, 0))
        ttk.Button(controls, text="Save CSV", command=self.save_csv, style="AR.TButton").grid(row=1, column=4, padx=(0, 12), pady=(5, 0))
        ttk.Checkbutton(
            controls,
            text="Capture rejected attempts",
            variable=self.reject_var,
            command=self._sync_rejected_option,
            style="AR.TCheckbutton",
        ).grid(row=1, column=5, sticky="w", pady=(5, 0))
        ttk.Label(controls, textvariable=self.status_var, style="AR.Muted.TLabel").grid(row=1, column=8, sticky="e", padx=(14, 0), pady=(5, 0))

        body = ttk.Frame(root, style="AR.Root.TFrame")
        body.grid(row=2, column=0, sticky="nsew")
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        live = ttk.Frame(body, style="AR.Card.TFrame", padding=(14, 11))
        live.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        for column in range(3):
            live.grid_columnconfigure(column, weight=1)
        ttk.Label(live, textvariable=self.live_character_var, style="AR.Value.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(live, textvariable=self.live_action_var, style="AR.Value.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(live, textvariable=self.live_frame_var, style="AR.Value.TLabel").grid(row=0, column=2, sticky="e")
        ttk.Label(live, textvariable=self.live_input_var, style="AR.Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(5, 0))
        ttk.Label(live, textvariable=self.live_command_var, style="AR.Muted.TLabel").grid(row=1, column=1, sticky="w", pady=(5, 0))
        ttk.Label(live, textvariable=self.live_mailbox_var, style="AR.Muted.TLabel").grid(row=1, column=2, sticky="e", pady=(5, 0))
        ttk.Label(live, textvariable=self.live_packet_var, style="AR.Muted.TLabel").grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))

        split = ttk.Panedwindow(body, orient="vertical")
        split.grid(row=1, column=0, sticky="nsew")

        table_card = ttk.Frame(split, style="AR.Card.TFrame", padding=8)
        detail_card = ttk.Frame(split, style="AR.Card.TFrame", padding=(12, 9))
        split.add(table_card, weight=5)
        split.add(detail_card, weight=1)
        table_card.grid_columnconfigure(0, weight=1)
        table_card.grid_rowconfigure(0, weight=1)

        self.event_tabs = ttk.Notebook(table_card, style="AR.TNotebook")
        self.event_tabs.grid(row=0, column=0, sticky="nsew")
        transitions_tab = ttk.Frame(self.event_tabs, style="AR.Root.TFrame")
        attempts_tab = ttk.Frame(self.event_tabs, style="AR.Root.TFrame")
        probe_tab = ttk.Frame(self.event_tabs, style="AR.Root.TFrame")
        self.event_tabs.add(transitions_tab, text="Transitions")
        self.event_tabs.add(attempts_tab, text="Attempts")
        self.event_tabs.add(probe_tab, text="Raw Probe")

        columns = ("time", "slot", "source", "target", "frame", "cause", "result", "evidence")
        headings = {
            "time": "Time",
            "slot": "Slot",
            "source": "Source",
            "target": "Target",
            "frame": "Src frame",
            "cause": "Cause",
            "result": "Result",
            "evidence": "Evidence",
        }
        widths = {"time": 84, "slot": 68, "source": 180, "target": 180, "frame": 76, "cause": 154, "result": 82, "evidence": 320}
        self.tree = self._make_event_tree(transitions_tab, columns, headings, widths)
        self.attempt_tree = self._make_event_tree(attempts_tab, columns, headings, widths)
        probe_columns = ("time", "slot", "action", "input", "cooked", "raw", "flags", "metadata", "parameter", "mailbox", "changed")
        probe_headings = {
            "time": "Time",
            "slot": "Slot",
            "action": "Live action",
            "input": "Input",
            "cooked": "+1994",
            "raw": "+210C",
            "flags": "Flags +1990/+2108",
            "metadata": "+2110",
            "parameter": "+2114",
            "mailbox": "+0200",
            "changed": "Changed",
        }
        probe_widths = {
            "time": 84, "slot": 66, "action": 190, "input": 142, "cooked": 82, "raw": 82,
            "flags": 174, "metadata": 92, "parameter": 150, "mailbox": 92, "changed": 220,
        }
        self.probe_tree = self._make_event_tree(probe_tab, probe_columns, probe_headings, probe_widths)
        for tree in (self.tree, self.attempt_tree):
            tree.tag_configure("accepted", foreground="#dceeff")
            tree.tag_configure("mailbox", foreground="#79ddff")
            tree.tag_configure("rejected", foreground="#ff9d9d")
            tree.tag_configure("system", foreground="#c7b8ff")
            tree.tag_configure("unknown", foreground="#d5bd89")
        self.probe_tree.tag_configure("probe", foreground="#9fe7ff")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._show_selected_detail(self.tree))
        self.attempt_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_selected_detail(self.attempt_tree))
        self.probe_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_selected_detail(self.probe_tree))

        ttk.Label(detail_card, text="EVENT EVIDENCE", style="AR.Section.TLabel").pack(anchor="w")
        ttk.Label(detail_card, textvariable=self.detail_var, style="AR.Muted.TLabel", justify="left", wraplength=1080).pack(anchor="w", fill="x", pady=(5, 0))

    def _make_event_tree(
        self,
        parent: ttk.Frame,
        columns: tuple[str, ...],
        headings: dict[str, str],
        widths: dict[str, int],
    ) -> ttk.Treeview:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(0, weight=1)
        tree = ttk.Treeview(parent, columns=columns, show="headings", style="AR.Treeview", selectmode="browse")
        yscroll = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        xscroll = ttk.Scrollbar(parent, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        for key in columns:
            tree.heading(key, text=headings[key])
            tree.column(key, width=widths[key], minwidth=55, stretch=key in {"source", "target", "evidence"})
        return tree

    def _sync_rejected_option(self) -> None:
        self.log_rejected = bool(self.reject_var.get())

    def _on_slot_change(self, _event=None) -> None:
        new_slot = str(self.slot_var.get() or "P1-C1")
        if new_slot not in SLOT_POINTERS:
            new_slot = "P1-C1"
            self.slot_var.set(new_slot)
        self.slot_label = new_slot
        self._reset_live_baseline()
        self.status_var.set(f"Now recording {new_slot}.")

    def _reset_live_baseline(self) -> None:
        self._last_base = 0
        self._last_action = None
        self._last_action_started_at = 0.0
        self._last_char_id = -1
        self._recent_mailbox.clear()
        self._recent_pressed.clear()
        self._recent_recognized.clear()
        self._pending_commands.clear()
        self._command_buffer.clear()
        self._last_normal_signature = None
        self._last_special_signature = None
        self._last_raw_packet_signature = None
        self._recent_cancel_events.clear()
        self._cancel_event_cursor = current_action_event_sequence()

    def toggle_recording(self) -> None:
        self.recording = not self.recording
        self.record_button_var.set("Pause recording" if self.recording else "Resume recording")
        self.status_var.set("Recording live transitions." if self.recording else "Recording paused. Live monitor remains active.")
        self._reset_live_baseline()

    def clear(self) -> None:
        self.records.clear()
        self.transition_records.clear()
        self.attempt_records.clear()
        self.probe_records.clear()
        self._tree_record_by_item.clear()
        self._attempt_record_by_item.clear()
        self._probe_record_by_item.clear()
        for tree in (self.tree, self.attempt_tree, self.probe_tree):
            for item in tree.get_children(""):
                tree.delete(item)
        for key in self._counts:
            self._counts[key] = 0
        self._update_counts()
        self.detail_var.set("Select a recorded event for full evidence.")
        self.status_var.set("Recorder history cleared.")

    def copy_records(self) -> None:
        active_tree = self.tree
        record_map: dict[str, Any] = self._tree_record_by_item
        tab_index = 0
        try:
            tab_index = int(self.event_tabs.index(self.event_tabs.select()))
        except Exception:
            tab_index = 0
        if tab_index == 1:
            active_tree = self.attempt_tree
            record_map = self._attempt_record_by_item
        elif tab_index == 2:
            active_tree = self.probe_tree
            record_map = self._probe_record_by_item

        selection = active_tree.selection()
        records = [record_map[item] for item in selection if item in record_map]
        if not records:
            if tab_index == 1:
                records = list(self.attempt_records)
            elif tab_index == 2:
                records = list(self.probe_records)
            else:
                records = list(self.transition_records)
        if not records:
            self.status_var.set("Nothing to copy in this view.")
            return

        lines: list[str] = []
        for record in records:
            if isinstance(record, RawProbeRecord):
                param_text = "n/a" if record.raw_param_float is None else f"{record.raw_param_float:.6g}"
                lines.append(
                    f"{record.timestamp} {record.slot} {record.action_name} [0x{record.action_id:04X}] | "
                    f"input {record.input_held:08X}/{record.input_pressed:08X} | "
                    f"+1990 {record.cooked_flags:08X} +1994 {record.cooked_index:08X} | "
                    f"+2108 {record.raw_flags:08X} +210C {record.raw_index:08X} "
                    f"+2110 {record.raw_metadata:08X} +2114 {record.raw_param_word:08X} ({param_text}) | "
                    f"mailbox {record.mailbox_raw:08X} | changed {record.changed_fields}"
                )
            else:
                lines.append(
                    f"{record.timestamp} {record.slot} {record.source_name} [0x{record.source_id:04X}] -> "
                    f"{record.target_name} [0x{record.target_id:04X}] | frame {record.source_frame} | "
                    f"{record.cause} | {record.result} | {record.note}"
                )
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append("\n".join(lines))
            self.window.update_idletasks()
            self.status_var.set(f"Copied {len(lines)} event{'s' if len(lines) != 1 else ''} from the active view.")
        except Exception as exc:
            self.status_var.set(f"Copy failed: {exc}")

    def save_csv(self) -> None:
        tab_index = 0
        try:
            tab_index = int(self.event_tabs.index(self.event_tabs.select()))
        except Exception:
            tab_index = 0
        if tab_index == 2:
            records: list[Any] = list(self.probe_records)
            stem = "tvc_action_raw_probe"
        else:
            records = list(self.records)
            stem = "tvc_action_recorder"
        if not records:
            self.status_var.set("Nothing to save in this view.")
            return
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            parent=self.window,
            title="Save action recorder CSV",
            defaultextension=".csv",
            initialfile=f"{stem}_{stamp}.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            fieldnames = list(asdict(records[0]).keys())
            with open(path, "w", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames)
                writer.writeheader()
                for record in records:
                    writer.writerow(asdict(record))
            self.status_var.set(f"Saved {len(records)} events to {os.path.basename(path)}.")
        except Exception as exc:
            self.status_var.set(f"CSV save failed: {exc}")

    def _label(self, action_id: int, char_id: int) -> str:
        try:
            label = move_label_for(int(action_id), int(char_id), self.move_map, self.global_map)
        except Exception:
            label = f"FLAG_{int(action_id)}"
        text = str(label or "").strip()
        return text if text else f"Action 0x{int(action_id) & 0xFFFF:04X}"

    def _recognized_targets(
        self,
        base: int,
        source_id: int,
        char_id: int,
        held: int,
        pressed: int,
        cooked: int,
        raw: int,
        now: float,
    ) -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        source_label = self._label(source_id, char_id)
        all_normal_rows = _recognized_normal_actions(base, held, pressed)
        normal_rows = []
        for row in all_normal_rows:
            target = int(row["target"]) & 0xFFFF
            target_label = self._label(target, char_id)
            if normal_candidate_matches_context(source_id, source_label, target, target_label):
                normal_rows.append(row)

        normal_signature = tuple((row["target"], row["row"], row["buttons"], row["direction"]) for row in normal_rows)
        if normal_rows and normal_signature != self._last_normal_signature:
            self._edge_serial += 1
            edge_id = self._edge_serial
            seen_inputs: set[tuple[int, int]] = set()
            for row in normal_rows:
                input_key = (int(row["direction"]), int(row["buttons"]))
                if input_key in seen_inputs:
                    continue
                seen_inputs.add(input_key)
                target = int(row["target"]) & 0xFFFF
                out[target] = {
                    "kind": "normal command row",
                    "time": now,
                    "edge_id": edge_id,
                    "held": int(held) & 0xFFFFFFFF,
                    "pressed": int(pressed) & 0xFFFFFFFF,
                    "cooked": int(cooked) & 0xFFFFFFFF,
                    "raw": int(raw) & 0xFFFFFFFF,
                    "detail": f"row 0x{row['row']:08X}, dir 0x{row['direction']:X}, buttons 0x{row['buttons']:08X}",
                }
        self._last_normal_signature = normal_signature if normal_rows else None

        special_targets = [target for target in (special_action_from_candidate(cooked), special_action_from_candidate(raw)) if target is not None]
        special_signature = tuple(sorted(set(special_targets))) + (int(cooked), int(raw))
        if special_targets and special_signature != self._last_special_signature:
            self._edge_serial += 1
            edge_id = self._edge_serial
            for target in sorted(set(special_targets)):
                out[int(target)] = {
                    "kind": "special command candidate",
                    "time": now,
                    "edge_id": edge_id,
                    "held": int(held) & 0xFFFFFFFF,
                    "pressed": int(pressed) & 0xFFFFFFFF,
                    "cooked": int(cooked) & 0xFFFFFFFF,
                    "raw": int(raw) & 0xFFFFFFFF,
                    "detail": f"cooked 0x{int(cooked):08X}, raw 0x{int(raw):08X}",
                }
        self._last_special_signature = special_signature if special_targets else None
        return out

    def _add_pending_commands(
        self,
        source_id: int,
        source_frame: int,
        recognized: dict[int, dict[str, Any]],
        now: float,
    ) -> None:
        for target, evidence in recognized.items():
            if int(target) == int(source_id):
                continue
            edge_id = int(evidence.get("edge_id", 0))
            key = (edge_id, int(target) & 0xFFFF)
            self._pending_commands[key] = {
                "source": int(source_id) & 0xFFFF,
                "target": int(target) & 0xFFFF,
                "source_frame": max(0, int(source_frame)),
                "time": now,
                "edge_id": edge_id,
                "kind": str(evidence.get("kind") or "command"),
                "detail": str(evidence.get("detail") or ""),
                "held": _as_int(evidence.get("held"), 0),
                "pressed": _as_int(evidence.get("pressed"), 0),
                "cooked": _as_int(evidence.get("cooked"), 0),
                "raw": _as_int(evidence.get("raw"), 0xFFFFFFFF),
            }

    def _expire_pending_commands(self, current_action: int, char_id: int, snapshot: dict[str, Any], now: float) -> None:
        expired: list[tuple[int, int]] = []
        logged_edges: set[int] = set()
        for key, pending in list(self._pending_commands.items()):
            if now - float(pending.get("time", 0.0)) < COMMAND_TIMEOUT_SEC:
                continue
            expired.append(key)
            edge_id = int(pending.get("edge_id", 0))
            if edge_id in logged_edges or not self.recording or not self.log_rejected:
                continue
            logged_edges.add(edge_id)
            source = int(pending.get("source", current_action)) & 0xFFFF
            target_i = int(pending.get("target", key[1])) & 0xFFFF
            note = (
                f"Fresh {pending.get('kind', 'command')} matched the current ground or air context, "
                f"but action 0x{target_i:04X} did not become active. {pending.get('detail', '')}"
            ).strip()
            self._append_record(
                source_id=source,
                target_id=target_i,
                char_id=char_id,
                source_frame=int(pending.get("source_frame", 0)),
                snapshot=snapshot,
                cause="Command attempt",
                result="Rejected",
                held=_as_int(pending.get("held"), 0),
                pressed=_as_int(pending.get("pressed"), 0),
                cooked=_as_int(pending.get("cooked"), 0),
                raw=_as_int(pending.get("raw"), 0xFFFFFFFF),
                mailbox_raw=0,
                mailbox_target=None,
                note=note,
            )
        for key in expired:
            self._pending_commands.pop(key, None)

    def _append_probe_record(
        self,
        *,
        now: float,
        action: int,
        char_id: int,
        held: int,
        pressed: int,
        cooked_flags: int,
        cooked: int,
        raw_flags: int,
        raw: int,
        raw_metadata: int,
        raw_param_word: int,
        raw_param_float: float | None,
        mailbox_raw: int,
        mailbox_target: int | None,
        changed_fields: str,
    ) -> None:
        record = RawProbeRecord(
            index=len(self.probe_records) + 1,
            timestamp=time.strftime("%H:%M:%S"),
            elapsed=round(max(0.0, now - self._started_at), 3),
            slot=self.slot_label,
            character=_character_name(char_id),
            char_id=int(char_id),
            action_id=int(action) & 0xFFFF,
            action_name=self._label(action, char_id),
            input_held=int(held) & 0xFFFFFFFF,
            input_pressed=int(pressed) & 0xFFFFFFFF,
            cooked_flags=int(cooked_flags) & 0xFFFFFFFF,
            cooked_index=int(cooked) & 0xFFFFFFFF,
            raw_flags=int(raw_flags) & 0xFFFFFFFF,
            raw_index=int(raw) & 0xFFFFFFFF,
            raw_metadata=int(raw_metadata) & 0xFFFFFFFF,
            raw_param_word=int(raw_param_word) & 0xFFFFFFFF,
            raw_param_float=raw_param_float,
            mailbox_raw=int(mailbox_raw) & 0xFFFFFFFF,
            mailbox_target=int(mailbox_target) & 0xFFFF if mailbox_target is not None else -1,
            changed_fields=str(changed_fields or "unknown"),
        )
        self.probe_records.append(record)
        param_text = "n/a" if record.raw_param_float is None else f"{record.raw_param_float:.5g}"
        item = self.probe_tree.insert(
            "",
            "end",
            values=(
                record.timestamp,
                record.slot,
                f"{record.action_name} [0x{record.action_id:04X}]",
                f"{record.input_held:08X}/{record.input_pressed:08X}",
                f"{record.cooked_index:08X}",
                f"{record.raw_index:08X}",
                f"{record.cooked_flags:08X}/{record.raw_flags:08X}",
                f"{record.raw_metadata:08X}",
                f"{record.raw_param_word:08X} ({param_text})",
                f"{record.mailbox_raw:08X}",
                record.changed_fields,
            ),
            tags=("probe",),
        )
        self._probe_record_by_item[item] = record
        self.probe_tree.see(item)
        self._counts["probe"] += 1
        while len(self.probe_records) > MAX_RAW_PROBE_RECORDS:
            self.probe_records.pop(0)
            children = self.probe_tree.get_children("")
            if children:
                first = children[0]
                self._probe_record_by_item.pop(first, None)
                self.probe_tree.delete(first)
        self._update_counts()

    def _capture_command_sample(
        self,
        *,
        now: float,
        action: int,
        char_id: int,
        held: int,
        pressed: int,
        cooked_flags: int,
        cooked: int,
        raw_flags: int,
        raw: int,
        raw_metadata: int,
        raw_param_word: int,
        raw_param_float: float | None,
        mailbox_raw: int,
        mailbox_target: int | None,
        recognized: dict[int, dict[str, Any]],
    ) -> None:
        sample = {
            "time": now,
            "action": int(action) & 0xFFFF,
            "held": int(held) & 0xFFFFFFFF,
            "pressed": int(pressed) & 0xFFFFFFFF,
            "cooked_flags": int(cooked_flags) & 0xFFFFFFFF,
            "cooked": int(cooked) & 0xFFFFFFFF,
            "raw_flags": int(raw_flags) & 0xFFFFFFFF,
            "raw": int(raw) & 0xFFFFFFFF,
            "raw_metadata": int(raw_metadata) & 0xFFFFFFFF,
            "raw_param_word": int(raw_param_word) & 0xFFFFFFFF,
            "raw_param_float": raw_param_float,
            "mailbox_raw": int(mailbox_raw) & 0xFFFFFFFF,
            "mailbox_target": int(mailbox_target) & 0xFFFF if mailbox_target is not None else -1,
            "recognized": {int(target) & 0xFFFF: dict(evidence) for target, evidence in recognized.items()},
        }
        self._command_buffer.append(sample)
        while self._command_buffer and now - float(self._command_buffer[0].get("time", 0.0)) > RAW_PACKET_BUFFER_SEC:
            self._command_buffer.popleft()

        signature = (
            int(cooked_flags) & 0xFFFFFFFF,
            int(cooked) & 0xFFFFFFFF,
            int(raw_flags) & 0xFFFFFFFF,
            int(raw) & 0xFFFFFFFF,
            int(raw_metadata) & 0xFFFFFFFF,
            int(raw_param_word) & 0xFFFFFFFF,
            int(mailbox_raw) & 0xFFFFFFFF,
        )
        previous = self._last_raw_packet_signature
        if signature != previous:
            previous_packet = {}
            if previous is not None:
                previous_packet = {
                    "cooked_flags": previous[0],
                    "cooked": previous[1],
                    "raw_flags": previous[2],
                    "raw": previous[3],
                    "raw_metadata": previous[4],
                    "raw_param_word": previous[5],
                    "mailbox_raw": previous[6],
                }
            changed_fields = _packet_changed_fields(previous, signature)
            should_log = _raw_packet_active(sample) or _raw_packet_active(previous_packet)
            if self.recording and should_log:
                self._append_probe_record(
                    now=now,
                    action=action,
                    char_id=char_id,
                    held=held,
                    pressed=pressed,
                    cooked_flags=cooked_flags,
                    cooked=cooked,
                    raw_flags=raw_flags,
                    raw=raw,
                    raw_metadata=raw_metadata,
                    raw_param_word=raw_param_word,
                    raw_param_float=raw_param_float,
                    mailbox_raw=mailbox_raw,
                    mailbox_target=mailbox_target,
                    changed_fields=changed_fields,
                )
            self._last_raw_packet_signature = signature

    def _best_buffer_evidence(self, target_id: int, now: float) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        target = int(target_id) & 0xFFFF
        fallback: dict[str, Any] | None = None
        for sample in reversed(self._command_buffer):
            age = now - float(sample.get("time", 0.0))
            if age > RECENT_EVIDENCE_SEC:
                break
            if int(sample.get("mailbox_target", -1)) == target:
                return sample, {"kind": "mailbox request", "detail": f"mailbox 0x{int(sample.get('mailbox_raw', 0)):08X}"}
            recognized = sample.get("recognized") or {}
            if target in recognized:
                return sample, dict(recognized[target])
            if fallback is None and int(sample.get("pressed", 0)):
                fallback = sample
        return fallback, None

    def _best_raw_packet(self, target_id: int, now: float) -> tuple[dict[str, Any] | None, bool, float]:
        best: dict[str, Any] | None = None
        best_score = -1.0
        best_exact = False
        best_age = -1.0
        for sample in reversed(self._command_buffer):
            age = now - float(sample.get("time", 0.0))
            if age > RAW_PACKET_BUFFER_SEC:
                break
            if not _raw_packet_active(sample):
                continue
            exact = _packet_matches_target(sample, target_id)
            score = (1000.0 if exact else 0.0) + (100.0 if int(sample.get("pressed", 0)) else 0.0) - age * 100.0
            if score > best_score:
                best = sample
                best_score = score
                best_exact = exact
                best_age = age
        return best, best_exact, max(-1.0, best_age * 1000.0)

    def _pop_pending_for_target(self, target_id: int) -> dict[str, Any] | None:
        target = int(target_id) & 0xFFFF
        matches = [(key, value) for key, value in self._pending_commands.items() if int(value.get("target", key[1])) == target]
        if not matches:
            return None
        key, pending = max(matches, key=lambda item: float(item[1].get("time", 0.0)))
        edge_id = int(pending.get("edge_id", key[0]))
        for sibling_key, sibling in list(self._pending_commands.items()):
            if int(sibling.get("edge_id", sibling_key[0])) == edge_id:
                self._pending_commands.pop(sibling_key, None)
        return pending

    def _cancel_event_cause(self, event: dict[str, Any]) -> str:
        origin = str(event.get("origin") or "").strip().lower()
        return {
            "profile": "Profile cancel",
            "selected_source": "Custom cancel",
            "manual_request": "Cancel Lab request",
            "auto_probe": "Cancel Lab auto probe",
        }.get(origin, "Cancel Lab")

    def _cancel_event_note(self, event: dict[str, Any], *, accepted_transition: bool = False) -> str:
        origin = str(event.get("origin") or "").strip().lower()
        source_frame = max(0, _as_int(event.get("source_frame"), 0))
        earliest = max(0, _as_int(event.get("earliest"), 0))
        latest = max(0, _as_int(event.get("latest"), 0))
        window_text = f"{earliest}-{latest}" if latest else f"{earliest}+"
        origin_text = {
            "profile": "Persistent profile rule",
            "selected_source": "Selected-source rule",
            "manual_request": "Manual Cancel Lab request",
            "auto_probe": "Cancel Lab auto probe",
        }.get(origin, "Cancel Lab rule")
        event_type = str(event.get("event_type") or "")
        if accepted_transition:
            state = "confirmed accepted" if event_type == "cancel_accepted" else "mailbox request matched the committed transition"
        elif event_type == "cancel_rejected":
            state = "request failed"
        else:
            state = "mailbox request sent"
        parts = [
            f"{origin_text}: {state} at source frame {source_frame}, rule window {window_text}.",
        ]
        reason = str(event.get("reason") or "").strip()
        message = str(event.get("message") or "").strip()
        if reason:
            parts.append(reason)
        if message and message != reason:
            parts.append(message)
        request_value = _as_int(event.get("request_value"), 0) & 0xFFFFFFFF
        if request_value:
            parts.append(f"Mailbox word 0x{request_value:08X}.")
        return " ".join(parts)

    def _consume_cancel_lab_events(
        self,
        *,
        now: float,
        char_id: int,
        snapshot: dict[str, Any],
        held: int,
        pressed: int,
        cooked: int,
        raw: int,
        mailbox_raw: int,
        cooked_flags: int,
        raw_flags: int,
        raw_metadata: int,
        raw_param_word: int,
        raw_param_float: float | None,
    ) -> None:
        try:
            cursor, events = action_events_since(self._cancel_event_cursor)
            self._cancel_event_cursor = cursor
        except Exception:
            return

        for event in events:
            if str(event.get("tool") or "") != "cancel_lab":
                continue
            if str(event.get("slot") or "") != self.slot_label:
                continue
            event_copy = dict(event)
            event_copy.setdefault("monotonic", now)
            self._recent_cancel_events.append(event_copy)

            target_id = _as_int(event_copy.get("target_id"), -1) & 0xFFFF
            if target_id >= 0:
                # Cancel Lab owns this command attempt. Remove the recorder's
                # inferred pending command so one input cannot create a second,
                # misleading rejection later.
                self._pop_pending_for_target(target_id)

            if str(event_copy.get("event_type") or "") != "cancel_rejected":
                continue
            if not self.recording:
                continue
            source_id = _as_int(event_copy.get("source_id"), 0) & 0xFFFF
            source_frame = max(0, _as_int(event_copy.get("source_frame"), 0))
            request_value = _as_int(event_copy.get("request_value"), 0) & 0xFFFFFFFF
            event_mailbox_target = target_id if request_value else None
            self._append_record(
                source_id=source_id,
                target_id=target_id,
                char_id=char_id,
                source_frame=source_frame,
                snapshot=snapshot,
                cause=self._cancel_event_cause(event_copy),
                result="Rejected",
                held=held,
                pressed=pressed,
                cooked=cooked,
                raw=raw,
                mailbox_raw=request_value or mailbox_raw,
                mailbox_target=event_mailbox_target,
                note=self._cancel_event_note(event_copy),
                special_flags=cooked_flags,
                raw_special_flags=raw_flags,
                raw_metadata=raw_metadata,
                raw_param_word=raw_param_word,
                raw_param_float=raw_param_float,
            )

        while self._recent_cancel_events:
            event_time = float(self._recent_cancel_events[0].get("monotonic", 0.0) or 0.0)
            if now - event_time <= CANCEL_EVENT_BUFFER_SEC:
                break
            self._recent_cancel_events.popleft()

    def _best_cancel_lab_event(self, source_id: int, target_id: int, now: float) -> dict[str, Any] | None:
        source = int(source_id) & 0xFFFF
        target = int(target_id) & 0xFFFF
        candidates: list[dict[str, Any]] = []
        for event in self._recent_cancel_events:
            if str(event.get("slot") or "") != self.slot_label:
                continue
            if _as_int(event.get("source_id"), -1) & 0xFFFF != source:
                continue
            if _as_int(event.get("target_id"), -1) & 0xFFFF != target:
                continue
            event_type = str(event.get("event_type") or "")
            if event_type not in {"cancel_request", "cancel_accepted", "cancel_rejected"}:
                continue
            age = now - float(event.get("monotonic", 0.0) or 0.0)
            if age < -0.05 or age > CANCEL_EVENT_BUFFER_SEC:
                continue
            candidates.append(event)
        if not candidates:
            return None
        latest = max(
            candidates,
            key=lambda event: (
                _as_int(event.get("sequence"), 0),
                float(event.get("monotonic", 0.0) or 0.0),
            ),
        )
        if str(latest.get("event_type") or "") == "cancel_rejected":
            return None
        return latest

    def _append_record(
        self,
        *,
        source_id: int,
        target_id: int,
        char_id: int,
        source_frame: int,
        snapshot: dict[str, Any],
        cause: str,
        result: str,
        held: int,
        pressed: int,
        cooked: int,
        raw: int,
        mailbox_raw: int,
        mailbox_target: int | None,
        note: str,
        special_flags: int = 0,
        raw_special_flags: int = 0,
        raw_metadata: int = 0,
        raw_param_word: int = 0,
        raw_param_float: float | None = None,
        raw_evidence_age_ms: float = -1.0,
    ) -> None:
        index = len(self.records) + 1
        elapsed = max(0.0, time.monotonic() - self._started_at)
        character = _character_name(char_id)
        record = TransitionRecord(
            index=index,
            timestamp=time.strftime("%H:%M:%S"),
            elapsed=round(elapsed, 3),
            slot=self.slot_label,
            character=character,
            char_id=int(char_id),
            source_id=int(source_id) & 0xFFFF,
            source_name=self._label(source_id, char_id),
            target_id=int(target_id) & 0xFFFF,
            target_name=self._label(target_id, char_id),
            source_frame=max(0, int(source_frame)),
            raw_frame_a=int(snapshot.get("frame_a") or 0),
            raw_frame_b=int(snapshot.get("frame_b") or 0),
            cause=str(cause),
            result=str(result),
            input_held=int(held) & 0xFFFFFFFF,
            input_pressed=int(pressed) & 0xFFFFFFFF,
            special_cooked=int(cooked) & 0xFFFFFFFF,
            special_raw=int(raw) & 0xFFFFFFFF,
            mailbox_raw=int(mailbox_raw) & 0xFFFFFFFF,
            mailbox_target=int(mailbox_target) & 0xFFFF if mailbox_target is not None else -1,
            note=str(note or ""),
            special_flags=int(special_flags) & 0xFFFFFFFF,
            raw_special_flags=int(raw_special_flags) & 0xFFFFFFFF,
            raw_metadata=int(raw_metadata) & 0xFFFFFFFF,
            raw_param_word=int(raw_param_word) & 0xFFFFFFFF,
            raw_param_float=raw_param_float,
            raw_evidence_age_ms=round(float(raw_evidence_age_ms), 3),
        )
        self.records.append(record)

        rejected = record.result.lower() == "rejected"
        if rejected:
            self.attempt_records.append(record)
            tree = self.attempt_tree
            item_map = self._attempt_record_by_item
            tag = "rejected"
            self._counts["attempts"] += 1
        else:
            self.transition_records.append(record)
            tree = self.tree
            item_map = self._tree_record_by_item
            tag = "accepted"
            self._counts["transitions"] += 1
            if record.cause.startswith("Mailbox") or record.cause in {
                "Profile cancel", "Custom cancel", "Cancel Lab request", "Cancel Lab auto probe"
            }:
                tag = "mailbox"
                self._counts["mailbox"] += 1
            elif record.cause == "Recognized command":
                self._counts["commands"] += 1
            elif record.cause == "System / reaction":
                tag = "system"
            elif record.cause == "Unknown":
                tag = "unknown"
                self._counts["unknown"] += 1

        source_text = f"{record.source_name} [0x{record.source_id:04X}]"
        target_text = f"{record.target_name} [0x{record.target_id:04X}]"
        item = tree.insert(
            "",
            "end",
            values=(record.timestamp, record.slot, source_text, target_text, record.source_frame, record.cause, record.result, record.note),
            tags=(tag,),
        )
        item_map[item] = record
        tree.see(item)
        self._update_counts()

    def _update_counts(self) -> None:
        self.count_var.set(
            f"Transitions {self._counts['transitions']}  |  Commands {self._counts['commands']}  |  "
            f"Mailbox {self._counts['mailbox']}  |  Attempts {self._counts['attempts']}  |  Probe {self._counts['probe']}"
        )

    def _show_selected_detail(self, tree: ttk.Treeview | None = None) -> None:
        active_tree = tree or self.tree
        if active_tree is self.probe_tree:
            selection = active_tree.selection()
            if not selection:
                return
            record = self._probe_record_by_item.get(selection[0])
            if record is None:
                return
            mailbox_text = "none" if record.mailbox_target < 0 else f"0x{record.mailbox_target:04X}"
            param_text = "not a finite game float" if record.raw_param_float is None else f"{record.raw_param_float:.8g}"
            self.detail_var.set(
                f"RAW COMMAND PACKET  |  {record.character} ({record.slot})  |  "
                f"{record.action_name} [0x{record.action_id:04X}]  |  changed: {record.changed_fields}\n"
                f"Input held 0x{record.input_held:08X}, pressed 0x{record.input_pressed:08X}  |  "
                f"cooked flags/index 0x{record.cooked_flags:08X}/0x{record.cooked_index:08X}  |  "
                f"raw flags/index 0x{record.raw_flags:08X}/0x{record.raw_index:08X}\n"
                f"Metadata 0x{record.raw_metadata:08X}  |  parameter 0x{record.raw_param_word:08X} ({param_text})  |  "
                f"mailbox 0x{record.mailbox_raw:08X}, target {mailbox_text}."
            )
            return

        record_map = self._attempt_record_by_item if active_tree is self.attempt_tree else self._tree_record_by_item
        selection = active_tree.selection()
        if not selection:
            return
        record = record_map.get(selection[0])
        if record is None:
            return
        mailbox_text = "none" if record.mailbox_target < 0 else f"0x{record.mailbox_target:04X}"
        param_text = "n/a" if record.raw_param_float is None else f"{record.raw_param_float:.8g}"
        evidence_age = "n/a" if record.raw_evidence_age_ms < 0 else f"{record.raw_evidence_age_ms:.1f} ms before commit"
        self.detail_var.set(
            f"{record.character} ({record.slot})  |  {record.source_name} [0x{record.source_id:04X}] to "
            f"{record.target_name} [0x{record.target_id:04X}]  |  source frame {record.source_frame}  |  "
            f"raw frames {record.raw_frame_a}/{record.raw_frame_b}  |  cause {record.cause}  |  result {record.result}\n"
            f"Input held 0x{record.input_held:08X}, pressed 0x{record.input_pressed:08X}  |  "
            f"cooked flags/index 0x{record.special_flags:08X}/0x{record.special_cooked:08X}  |  "
            f"raw flags/index 0x{record.raw_special_flags:08X}/0x{record.special_raw:08X}\n"
            f"Metadata 0x{record.raw_metadata:08X}  |  parameter 0x{record.raw_param_word:08X} ({param_text})  |  "
            f"raw evidence {evidence_age}  |  mailbox raw 0x{record.mailbox_raw:08X}, target {mailbox_text}.  {record.note}"
        )

    def _poll(self) -> None:
        if self._closing:
            return
        now = time.monotonic()
        ptr_addr = SLOT_POINTERS.get(self.slot_label, SLOT_POINTERS["P1-C1"])
        base = _read_u32(ptr_addr, 0)
        if not _valid_base(base):
            if now - self._last_ui_refresh >= UI_REFRESH_SEC:
                self.live_character_var.set(f"{self.slot_label}  |  Waiting for fighter")
                self.live_action_var.set("Action  ----")
                self.live_frame_var.set("Frame --")
                self.status_var.set("Waiting for a live fighter in the selected slot.")
                self._last_ui_refresh = now
            self._reset_live_baseline()
            self._schedule_poll()
            return

        char_id = _read_u32(base + OFF_CHAR_ID, 0) & 0xFFFF
        action = _read_u32(base + OFF_ACTION_ID, 0) & 0xFFFF
        mailbox_raw = _read_u32(base + OFF_ACTION_REQUEST, 0)
        mailbox_target = mailbox_target_from_value(mailbox_raw)
        held = _read_u32(base + OFF_INPUT_HELD, 0)
        pressed = _read_u32(base + OFF_INPUT_PRESSED, 0)
        cooked_flags = _read_u32(base + OFF_SPECIAL_FLAGS, 0)
        cooked = _read_u32(base + OFF_SPECIAL_CANDIDATE, 0)
        raw_flags = _read_u32(base + OFF_RAW_SPECIAL_FLAGS, 0)
        raw = _read_u32(base + OFF_RAW_SPECIAL_CANDIDATE, 0xFFFFFFFF)
        raw_metadata = _read_u32(base + OFF_RAW_SPECIAL_METADATA, 0)
        raw_param_word = _read_u32(base + OFF_RAW_SPECIAL_PARAM, 0)
        raw_param_float = _float_from_word(raw_param_word)
        snapshot = _frame_snapshot(base)

        if base != self._last_base:
            self._last_base = base
            self._last_action = action
            self._last_action_started_at = now
            self._last_char_id = char_id
            self._pending_commands.clear()
            self._command_buffer.clear()
            self._last_raw_packet_signature = None
            self.status_var.set(f"Attached to {self.slot_label} at 0x{base:08X}.")

        source_age = max(1, int((now - self._last_action_started_at) * 60.0) + 1) if self._last_action_started_at else 0
        recognized_now = self._recognized_targets(base, action, char_id, held, pressed, cooked, raw, now)
        for target, evidence in recognized_now.items():
            self._recent_recognized[int(target)] = dict(evidence)
        if mailbox_target is not None:
            self._recent_mailbox = {"target": mailbox_target, "raw": mailbox_raw, "time": now}
        if pressed:
            self._recent_pressed = {"value": pressed, "held": held, "time": now}

        self._capture_command_sample(
            now=now,
            action=action,
            char_id=char_id,
            held=held,
            pressed=pressed,
            cooked_flags=cooked_flags,
            cooked=cooked,
            raw_flags=raw_flags,
            raw=raw,
            raw_metadata=raw_metadata,
            raw_param_word=raw_param_word,
            raw_param_float=raw_param_float,
            mailbox_raw=mailbox_raw,
            mailbox_target=mailbox_target,
            recognized=recognized_now,
        )
        if self.recording:
            self._add_pending_commands(action, source_age, recognized_now, now)

        self._consume_cancel_lab_events(
            now=now,
            char_id=char_id,
            snapshot=snapshot,
            held=held,
            pressed=pressed,
            cooked=cooked,
            raw=raw,
            mailbox_raw=mailbox_raw,
            cooked_flags=cooked_flags,
            raw_flags=raw_flags,
            raw_metadata=raw_metadata,
            raw_param_word=raw_param_word,
            raw_param_float=raw_param_float,
        )

        character = _character_name(char_id)
        action_name = self._label(action, char_id)
        raw_frame = max(int(snapshot.get("frame_a") or 0), int(snapshot.get("frame_b") or 0))
        if now - self._last_ui_refresh >= UI_REFRESH_SEC:
            self.live_character_var.set(f"{self.slot_label}  |  {character}  |  base 0x{base:08X}")
            self.live_action_var.set(f"{action_name}  [0x{action:04X}]")
            self.live_frame_var.set(f"Age {source_age}f  |  raw {raw_frame}")
            self.live_input_var.set(f"Input held {held:08X}  pressed {pressed:08X}")
            self.live_command_var.set(
                f"Command +1990/+1994 {cooked_flags:08X}/{cooked:08X}  "
                f"+2108/+210C {raw_flags:08X}/{raw:08X}"
            )
            param_text = "n/a" if raw_param_float is None else f"{raw_param_float:.5g}"
            self.live_packet_var.set(
                f"Raw packet +2110 {raw_metadata:08X}  +2114 {raw_param_word:08X} ({param_text})"
            )
            self.live_mailbox_var.set(f"Mailbox {mailbox_raw:08X}" + (f" -> 0x{mailbox_target:04X}" if mailbox_target is not None else ""))
            self._last_ui_refresh = now

        if self._last_action is None:
            self._last_action = action
            self._last_action_started_at = now

        if action != self._last_action:
            source_id = int(self._last_action) & 0xFFFF
            target_id = int(action) & 0xFFFF
            source_frame = max(1, int((now - self._last_action_started_at) * 60.0) + 1) if self._last_action_started_at else 0
            pending = self._pop_pending_for_target(target_id)
            cancel_event = self._best_cancel_lab_event(source_id, target_id, now)
            sample, buffered_evidence = self._best_buffer_evidence(target_id, now)
            raw_packet, raw_exact, raw_age_ms = self._best_raw_packet(target_id, now)

            evidence_sample = sample or {}
            if raw_exact and raw_packet is not None:
                evidence_sample = raw_packet
            recent_mailbox_target = _as_int(evidence_sample.get("mailbox_target"), -1)
            recent_mailbox_raw = _as_int(evidence_sample.get("mailbox_raw"), 0)
            recent_pressed = _as_int(evidence_sample.get("pressed"), 0)
            recent_held = _as_int(evidence_sample.get("held"), held)
            recent_cooked = _as_int(evidence_sample.get("cooked"), cooked)
            recent_raw = _as_int(evidence_sample.get("raw"), raw)
            recognized_targets = set(int(value) for value in (evidence_sample.get("recognized") or {}).keys())
            if raw_exact:
                recognized_targets.add(target_id)

            cause = classify_transition(
                source_id,
                target_id,
                mailbox_target=recent_mailbox_target if recent_mailbox_target >= 0 else None,
                recognized_targets=recognized_targets,
                input_pressed=recent_pressed,
            )
            if cancel_event is not None:
                cause = self._cancel_event_cause(cancel_event)
                source_frame = max(0, _as_int(cancel_event.get("source_frame"), source_frame))
                event_mailbox = _as_int(cancel_event.get("request_value"), 0) & 0xFFFFFFFF
                if event_mailbox:
                    recent_mailbox_raw = event_mailbox
                    recent_mailbox_target = target_id
            note_parts: list[str] = []
            if cancel_event is not None:
                note_parts.append(self._cancel_event_note(cancel_event, accepted_transition=True))
            if pending:
                note_parts.append(f"Accepted {pending.get('kind', 'command')}: {pending.get('detail', '')}")
            elif buffered_evidence:
                note_parts.append(f"Matched rolling command buffer: {buffered_evidence.get('kind', 'command')}. {buffered_evidence.get('detail', '')}")

            packet_for_record = raw_packet if raw_packet is not None else evidence_sample
            packet_flags = _as_int(packet_for_record.get("cooked_flags"), cooked_flags)
            packet_raw_flags = _as_int(packet_for_record.get("raw_flags"), raw_flags)
            packet_metadata = _as_int(packet_for_record.get("raw_metadata"), raw_metadata)
            packet_param_word = _as_int(packet_for_record.get("raw_param_word"), raw_param_word)
            packet_param_float = packet_for_record.get("raw_param_float", raw_param_float)
            packet_cooked = _as_int(packet_for_record.get("cooked"), recent_cooked)
            packet_raw = _as_int(packet_for_record.get("raw"), recent_raw)

            if raw_packet is not None and (raw_exact or target_id >= 0x0120):
                expected_index = command_index_for_action(target_id)
                match_text = "exact target match" if raw_exact else "nearest changed packet"
                expected_text = "n/a" if expected_index is None else f"0x{expected_index:08X}"
                note_parts.append(
                    f"Raw command packet ({match_text}, {raw_age_ms:.1f} ms before commit): "
                    f"expected index {expected_text}, cooked 0x{packet_cooked:08X}, raw 0x{packet_raw:08X}, "
                    f"flags 0x{packet_flags:08X}/0x{packet_raw_flags:08X}, "
                    f"metadata 0x{packet_metadata:08X}, parameter 0x{packet_param_word:08X}."
                )
            if recent_mailbox_target >= 0:
                note_parts.append(f"Recent mailbox target 0x{recent_mailbox_target:04X}")
            if not note_parts:
                note_parts.append("No target-specific command or mailbox evidence was captured in the rolling window.")

            if self.recording:
                self._append_record(
                    source_id=source_id,
                    target_id=target_id,
                    char_id=char_id,
                    source_frame=source_frame,
                    snapshot=snapshot,
                    cause=cause,
                    result="Accepted",
                    held=recent_held,
                    pressed=recent_pressed,
                    cooked=packet_cooked,
                    raw=packet_raw,
                    mailbox_raw=recent_mailbox_raw or mailbox_raw,
                    mailbox_target=recent_mailbox_target if recent_mailbox_target >= 0 else None,
                    note="  ".join(note_parts),
                    special_flags=packet_flags,
                    raw_special_flags=packet_raw_flags,
                    raw_metadata=packet_metadata,
                    raw_param_word=packet_param_word,
                    raw_param_float=packet_param_float if isinstance(packet_param_float, (int, float)) else None,
                    raw_evidence_age_ms=raw_age_ms if raw_packet is not None else -1.0,
                )

            self._last_action = action
            self._last_action_started_at = now
            self._last_char_id = char_id
            self._last_normal_signature = None
            self._last_special_signature = None
            self._recent_mailbox.clear()
            self._recent_pressed.clear()

        self._expire_pending_commands(action, char_id, snapshot, now)
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


def open_action_recorder_window(
    move_map: dict[int, dict[int, str]] | None = None,
    global_map: dict[int, str] | None = None,
    initial_slot: str = "P1-C1",
) -> None:
    """Open or focus the standalone live action recorder."""
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
        _ACTIVE_WINDOW = ActionRecorderWindow(master_root, move_map, global_map, initial_slot)

    tk_call(create)


__all__ = [
    "SLOT_POINTERS",
    "mailbox_target_from_value",
    "special_action_from_candidate",
    "classify_transition",
    "action_environment",
    "normal_target_environment",
    "normal_candidate_matches_context",
    "open_action_recorder_window",
    "ActionRecorderWindow",
    "TransitionRecord",
]
