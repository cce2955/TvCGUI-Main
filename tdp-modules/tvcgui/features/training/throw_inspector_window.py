"""Standalone read-only throw packet inspector for TvCGUI."""
from __future__ import annotations

import copy
import tkinter as tk
from tkinter import ttk
from typing import Any

from tvcgui.core.tk_host import tk_call
from tvcgui.features.training.throw_inspector_readonly import read_all_throw_snapshots

try:
    from tvcgui.features.frame_data.widgets import apply_titlebar_icon
except Exception:
    def apply_titlebar_icon(_window):
        return None

POLL_MS = 50
_ACTIVE_WINDOW: "ThrowInspectorWindow | None" = None


def _fmt_float(value: Any, digits: int = 2) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except Exception:
        return "-"


def _fmt_ptr(value: Any) -> str:
    try:
        raw = int(value)
    except Exception:
        raw = 0
    return f"0x{raw:08X}" if raw else "-"


def _entry_signature(snap: dict) -> tuple:
    authored = snap.get("authored") or {}
    rows = authored.get("entries") or []
    return tuple(
        (
            int(row.get("index") or 0),
            int(row.get("flags") or 0),
            int(row.get("throw_action") or 0),
            int(row.get("active_frames") or 0),
            round(float(row.get("range_raw") or 0.0), 5),
        )
        for row in rows
        if isinstance(row, dict)
    )


def _live_signature(live: dict) -> tuple:
    return (
        int(live.get("flags") or 0),
        int(live.get("thrower_action") or -1),
        int(live.get("victim_action") or -1),
        int(live.get("packet_slot") or -1),
        round(float(live.get("range_effective") or 0.0), 5),
    )


class ThrowInspectorWindow:
    def __init__(self, master: tk.Misc) -> None:
        self.window = tk.Toplevel(master)
        self.window.title("TvC Throw Inspector")
        self.window.geometry("1080x690")
        self.window.minsize(920, 580)
        self.window.configure(bg="#0d121b")
        apply_titlebar_icon(self.window)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._closing = False
        self._after_id = None

        self._latest: dict[str, dict] = {}
        self._latched_authored: dict[str, dict] = {}
        self._latched_live: dict[str, dict] = {}
        self._last_authored_signature: dict[str, tuple] = {}
        self._last_live_signature: dict[str, tuple] = {}

        self.slot_var = tk.StringVar(value="P1-C1")
        self.auto_follow_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Watching all fighters for a throw...")
        self.source_var = tk.StringVar(value="Perform a throw. The last packet will stay latched here.")
        self.live_var = tk.StringVar(value="No live throw descriptor")
        self.link_var = tk.StringVar(value="")

        self._configure_styles()
        self._build_ui()
        self._schedule(initial=True)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.window)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Throw.TFrame", background="#0d121b")
        style.configure("ThrowCard.TFrame", background="#151d29")
        style.configure("Throw.TLabel", background="#0d121b", foreground="#dbe8f6", font=("Segoe UI", 9))
        style.configure("ThrowCard.TLabel", background="#151d29", foreground="#dbe8f6", font=("Consolas", 9))
        style.configure("ThrowHeader.TLabel", background="#0d121b", foreground="#b69cff", font=("Segoe UI Semibold", 12))
        style.configure("ThrowReadOnly.TLabel", background="#0d121b", foreground="#7ee787", font=("Segoe UI Semibold", 9))
        style.configure("Throw.TCombobox", fieldbackground="#151d29", background="#151d29", foreground="#dbe8f6")
        style.configure("Throw.TCheckbutton", background="#0d121b", foreground="#dbe8f6")
        style.configure("Throw.TButton", font=("Segoe UI Semibold", 9), padding=(10, 5))
        style.configure("Treeview", background="#111925", foreground="#dbe8f6", fieldbackground="#111925", rowheight=24, font=("Consolas", 9))
        style.configure("Treeview.Heading", background="#222d3e", foreground="#f3f7ff", font=("Segoe UI Semibold", 9))
        style.map("Treeview", background=[("selected", "#51447d")], foreground=[("selected", "#ffffff")])

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.window, style="Throw.TFrame", padding=10)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="Throw.TFrame")
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="THROW INSPECTOR", style="ThrowHeader.TLabel").pack(side="left")
        ttk.Label(header, text="READ ONLY / NO GAME WRITES", style="ThrowReadOnly.TLabel").pack(side="left", padx=(14, 0))
        ttk.Label(header, textvariable=self.status_var, style="Throw.TLabel").pack(side="left", padx=(16, 0))

        controls = ttk.Frame(outer, style="Throw.TFrame")
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, text="Fighter", style="Throw.TLabel").pack(side="left")
        slot_box = ttk.Combobox(
            controls,
            textvariable=self.slot_var,
            values=("P1-C1", "P1-C2", "P2-C1", "P2-C2"),
            state="readonly",
            width=8,
            style="Throw.TCombobox",
        )
        slot_box.pack(side="left", padx=(6, 0))
        slot_box.bind("<<ComboboxSelected>>", lambda _event: self._render())

        ttk.Checkbutton(
            controls,
            text="Auto-follow newest throw",
            variable=self.auto_follow_var,
            style="Throw.TCheckbutton",
        ).pack(side="left", padx=(14, 0))
        ttk.Button(
            controls,
            text="Clear latched",
            command=self._clear_latched,
            style="Throw.TButton",
        ).pack(side="left", padx=(14, 0))

        ttk.Label(outer, textvariable=self.source_var, style="Throw.TLabel").pack(fill="x", pady=(0, 8))

        live_card = ttk.Frame(outer, style="ThrowCard.TFrame", padding=10)
        live_card.pack(fill="x", pady=(0, 8))
        ttk.Label(live_card, text="RESOLVED DESCRIPTOR", style="ThrowCard.TLabel").pack(anchor="w")
        ttk.Label(live_card, textvariable=self.live_var, style="ThrowCard.TLabel").pack(anchor="w", pady=(6, 0))
        ttk.Label(live_card, textvariable=self.link_var, style="ThrowCard.TLabel").pack(anchor="w", pady=(3, 0))

        table_card = ttk.Frame(outer, style="ThrowCard.TFrame", padding=6)
        table_card.pack(fill="both", expand=True)
        columns = ("slot", "context", "targets", "action", "active", "raw_range", "range", "fail", "contact", "flags")
        self.tree = ttk.Treeview(table_card, columns=columns, show="headings", selectmode="browse")
        headings = {
            "slot": "Pkt", "context": "Context", "targets": "Targets", "action": "Action",
            "active": "Active", "raw_range": "Raw range", "range": "Range",
            "fail": "Fail action", "contact": "Contact", "flags": "Flags",
        }
        widths = {
            "slot": 48, "context": 80, "targets": 190, "action": 70,
            "active": 64, "raw_range": 90, "range": 75, "fail": 78,
            "contact": 70, "flags": 100,
        }
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], minwidth=45, stretch=col == "targets")
        ybar = ttk.Scrollbar(table_card, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(table_card, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        table_card.rowconfigure(0, weight=1)
        table_card.columnconfigure(0, weight=1)

        note = (
            "The window watches all four roster slots while open. Authored throw packets are latched when seen, "
            "so returning to idle does not erase the result. Range is packet +0x14 multiplied by 0.01. "
            "Partner pointers are bookkeeping and are not treated as an active-grab boolean."
        )
        ttk.Label(outer, text=note, style="Throw.TLabel", wraplength=1040).pack(fill="x", pady=(8, 0))

    def _clear_latched(self) -> None:
        self._latched_authored.clear()
        self._latched_live.clear()
        self._last_authored_signature.clear()
        self._last_live_signature.clear()
        self.source_var.set("Latched throw history cleared. Perform another throw.")
        self._render()

    def _capture_new_throw_data(self, snapshots: dict[str, dict]) -> str | None:
        newest_slot: str | None = None
        for slot, snap in snapshots.items():
            if not isinstance(snap, dict) or not snap.get("connected"):
                continue

            authored = snap.get("authored") or {}
            entries = authored.get("entries") or []
            if entries:
                sig = (int(snap.get("action_id") or -1), _entry_signature(snap))
                if sig != self._last_authored_signature.get(slot):
                    self._last_authored_signature[slot] = sig
                    self._latched_authored[slot] = copy.deepcopy(snap)
                    newest_slot = slot

            live = snap.get("live") or {}
            if live.get("descriptor_valid") or live.get("contact_capture"):
                sig = _live_signature(live)
                if sig != self._last_live_signature.get(slot):
                    self._last_live_signature[slot] = sig
                    self._latched_live[slot] = copy.deepcopy(live)
                    newest_slot = slot

        return newest_slot

    def _poll(self) -> None:
        if self._closing:
            return
        try:
            self._latest = read_all_throw_snapshots()
            newest = self._capture_new_throw_data(self._latest)
            if newest and bool(self.auto_follow_var.get()):
                self.slot_var.set(newest)
            self._render()
        except Exception as exc:
            self.status_var.set(f"Read failed: {exc}")
        self._schedule()

    def _render(self) -> None:
        slot = str(self.slot_var.get() or "P1-C1")
        current = self._latest.get(slot) or {}
        latched = self._latched_authored.get(slot) or {}

        if not current.get("connected") and not latched.get("connected"):
            self.status_var.set(f"{slot}: fighter unavailable")
            self.source_var.set("Waiting for fighter...")
            self.live_var.set("No resolved throw descriptor")
            self.link_var.set("")
            self.tree.delete(*self.tree.get_children())
            return

        identity = current if current.get("connected") else latched
        character = str(identity.get("character") or "-")
        base = int(identity.get("fighter_base") or 0)

        current_authored = current.get("authored") or {}
        current_entries = list(current_authored.get("entries") or [])
        if current_entries:
            authored_source = current
            entries = current_entries
            source_label = "LIVE ACTION"
        else:
            authored_source = latched
            entries = list((latched.get("authored") or {}).get("entries") or [])
            source_label = "LATCHED" if entries else "NONE"

        current_live = current.get("live") or {}
        if current_live.get("descriptor_valid") or current_live.get("contact_capture"):
            live = current_live
            live_source_label = "LIVE"
        else:
            live = self._latched_live.get(slot) or current_live
            live_source_label = "LATCHED" if self._latched_live.get(slot) else "CURRENT/EMPTY"

        current_action = current.get("action_id", -1)
        shown_action = authored_source.get("action_id", -1) if authored_source else -1
        self.status_var.set(
            f"{slot}  {character}  current action={current_action}  base=0x{base:08X}  throws={len(entries)}"
        )

        if entries:
            self.source_var.set(
                f"Showing {source_label} authored throw packet from action {shown_action}. "
                f"Resolved descriptor source: {live_source_label}."
            )
        else:
            self.source_var.set(
                "No throw packet latched for this fighter yet. Perform a throw while this window is open."
            )

        if live.get("descriptor_valid") or live.get("contact_capture"):
            descriptor_kind = "contact capture" if live.get("contact_capture") else "ordinary throw"
            live_text = (
                f"{live_source_label} {descriptor_kind} | {live.get('context', '-')} | "
                f"targets {live.get('targets_text', '-')} | "
                f"action {live.get('thrower_action', -1)} -> victim {live.get('victim_action', -1)} | "
                f"active {live.get('active_frames_remaining', 0)}f | "
                f"range {_fmt_float(live.get('range_effective'))} | packet {live.get('packet_slot', -1)} | "
                f"flags {live.get('flags_hex', '0x00000000')}"
            )
        else:
            live_text = (
                f"No resolved descriptor latched | flags {live.get('flags_hex', '0x00000000')} | "
                f"action {live.get('thrower_action', -1)} | packet {live.get('packet_slot', -1)}"
            )
        self.live_var.set(live_text)
        self.link_var.set(
            f"linked victim {_fmt_ptr(live.get('linked_victim'))}   "
            f"linked thrower {_fmt_ptr(live.get('linked_thrower'))}   "
            f"reaction source {_fmt_ptr(live.get('reaction_source'))}"
        )

        self.tree.delete(*self.tree.get_children())
        for row in entries:
            self.tree.insert(
                "", "end",
                values=(
                    row.get("index"),
                    row.get("context"),
                    row.get("targets_text"),
                    row.get("throw_action"),
                    f"{row.get('active_frames')}f",
                    _fmt_float(row.get("range_raw")),
                    _fmt_float(row.get("range_effective")),
                    "yes" if row.get("failed_capture_action") else "no",
                    "yes" if row.get("contact_capture") else "no",
                    row.get("flags_hex"),
                ),
            )

    def _schedule(self, *, initial: bool = False) -> None:
        try:
            self._after_id = self.window.after(20 if initial else POLL_MS, self._poll)
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


def open_throw_inspector_window() -> None:
    """Open or focus the dedicated read-only throw inspector."""
    def create(master: tk.Misc) -> None:
        global _ACTIVE_WINDOW
        existing = _ACTIVE_WINDOW
        try:
            if existing is not None and bool(existing.window.winfo_exists()):
                existing.window.deiconify()
                existing.window.lift()
                existing.window.focus_force()
                return
        except Exception:
            _ACTIVE_WINDOW = None
        _ACTIVE_WINDOW = ThrowInspectorWindow(master)

    tk_call(create)


__all__ = ["ThrowInspectorWindow", "open_throw_inspector_window"]
