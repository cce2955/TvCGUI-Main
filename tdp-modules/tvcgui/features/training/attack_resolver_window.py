"""Standalone read-only attack research window."""
from __future__ import annotations

import json
import os
import time
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Any

from tvcgui.core.tk_host import tk_call
from tvcgui.features.training.attack_resolver_readonly import (
    ReadOnlyAttackResearch,
    get_attack_resolver_research,
)

try:
    from tvcgui.features.frame_data.widgets import apply_titlebar_icon
except Exception:
    def apply_titlebar_icon(_window):
        return None


POLL_MS = 100
MAX_VISIBLE_ROWS = 750
_ACTIVE_WINDOW: "AttackResolverWindow | None" = None


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _hex32(value: Any) -> str:
    return f"0x{_safe_int(value) & 0xFFFFFFFF:08X}"


def _short_hex(value: Any) -> str:
    return f"{_safe_int(value) & 0xFFFFFFFF:08X}"


def _value(value: Any, fallback: str = "-") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def _delta(before: Any, after: Any) -> str:
    a = _safe_int(before)
    b = _safe_int(after)
    return f"{a} -> {b} ({b - a:+d})"


class AttackResolverWindow:
    def __init__(self, master: tk.Misc, engine: ReadOnlyAttackResearch) -> None:
        self.engine = engine
        self.window = tk.Toplevel(master)
        self.window.title("TvC Read-Only Attack Research")
        self.window.geometry("1480x860")
        self.window.minsize(1120, 680)
        self.window.configure(bg="#0d121b")
        apply_titlebar_icon(self.window)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self._closing = False
        self._after_id = None
        self._known_sequences: set[int] = set()
        self._row_records: dict[str, dict] = {}
        self._last_selection_sequence = 0
        self._paused = False
        self._latest_signature = None

        self.status_var = tk.StringVar(value="Opening read-only research capture...")
        self.capture_var = tk.BooleanVar(value=True)
        self.pause_var = tk.BooleanVar(value=False)
        self.autoscroll_var = tk.BooleanVar(value=True)
        self.slot_filter_var = tk.StringVar(value="ALL")
        self.search_var = tk.StringVar(value="")

        self._configure_styles()
        self._build_ui()
        self.engine.set_capture_enabled(True)
        self._schedule_poll(initial=True)

    def _configure_styles(self) -> None:
        style = ttk.Style(self.window)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Research.TFrame", background="#0d121b")
        style.configure("Card.TFrame", background="#151d29")
        style.configure("Research.TLabel", background="#0d121b", foreground="#dbe8f6", font=("Segoe UI", 9))
        style.configure("Card.TLabel", background="#151d29", foreground="#dbe8f6", font=("Consolas", 9))
        style.configure("Header.TLabel", background="#0d121b", foreground="#b69cff", font=("Segoe UI Semibold", 12))
        style.configure("ReadOnly.TLabel", background="#0d121b", foreground="#7ee787", font=("Segoe UI Semibold", 9))
        style.configure("Research.TButton", font=("Segoe UI Semibold", 9), padding=(10, 5))
        style.configure("Research.TCheckbutton", background="#0d121b", foreground="#dbe8f6")
        style.configure("Research.TCombobox", fieldbackground="#151d29", background="#151d29", foreground="#dbe8f6")
        style.configure("Treeview", background="#111925", foreground="#dbe8f6", fieldbackground="#111925", rowheight=24, font=("Consolas", 9))
        style.configure("Treeview.Heading", background="#222d3e", foreground="#f3f7ff", font=("Segoe UI Semibold", 9))
        style.map("Treeview", background=[("selected", "#51447d")], foreground=[("selected", "#ffffff")])
        style.configure("TNotebook", background="#0d121b", borderwidth=0)
        style.configure("TNotebook.Tab", background="#1b2533", foreground="#cdd9e8", padding=(12, 6))
        style.map("TNotebook.Tab", background=[("selected", "#51447d")], foreground=[("selected", "#ffffff")])

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.window, style="Research.TFrame", padding=10)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer, style="Research.TFrame")
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="READ-ONLY ATTACK RESEARCH V3", style="Header.TLabel").pack(side="left")
        ttk.Label(header, text="NO GAME WRITES", style="ReadOnly.TLabel").pack(side="left", padx=(14, 0))
        ttk.Label(header, textvariable=self.status_var, style="Research.TLabel").pack(side="left", padx=(16, 0))

        controls = ttk.Frame(outer, style="Research.TFrame")
        controls.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(
            controls,
            text="Capture",
            variable=self.capture_var,
            command=self._toggle_capture,
            style="Research.TCheckbutton",
        ).pack(side="left")
        ttk.Checkbutton(
            controls,
            text="Pause display",
            variable=self.pause_var,
            command=self._toggle_pause,
            style="Research.TCheckbutton",
        ).pack(side="left", padx=(10, 0))
        ttk.Checkbutton(
            controls,
            text="Auto-scroll",
            variable=self.autoscroll_var,
            style="Research.TCheckbutton",
        ).pack(side="left", padx=(10, 0))

        ttk.Label(controls, text="Slot", style="Research.TLabel").pack(side="left", padx=(22, 6))
        slot_box = ttk.Combobox(
            controls,
            textvariable=self.slot_filter_var,
            values=("ALL", "P1-C1", "P1-C2", "P2-C1", "P2-C2"),
            state="readonly",
            width=8,
            style="Research.TCombobox",
        )
        slot_box.pack(side="left")
        slot_box.bind("<<ComboboxSelected>>", lambda _event: self._rebuild_rows())

        ttk.Label(controls, text="Filter", style="Research.TLabel").pack(side="left", padx=(16, 6))
        search = ttk.Entry(controls, textvariable=self.search_var, width=24)
        search.pack(side="left")
        search.bind("<KeyRelease>", lambda _event: self._rebuild_rows())

        ttk.Button(controls, text="Clear", style="Research.TButton", command=self._clear).pack(side="right")
        ttk.Button(controls, text="Export JSON", style="Research.TButton", command=self._export_json).pack(side="right", padx=(0, 6))
        ttk.Button(controls, text="Export sources", style="Research.TButton", command=self._export_sources_csv).pack(side="right", padx=(0, 6))
        ttk.Button(controls, text="Export contacts", style="Research.TButton", command=self._export_csv).pack(side="right", padx=(0, 6))
        ttk.Button(controls, text="Copy selected", style="Research.TButton", command=self._copy_selected).pack(side="right", padx=(0, 6))

        paned = ttk.Panedwindow(outer, orient="horizontal")
        paned.pack(fill="both", expand=True)

        left = ttk.Frame(paned, style="Card.TFrame", padding=6)
        right = ttk.Frame(paned, style="Card.TFrame", padding=6)
        paned.add(left, weight=3)
        paned.add(right, weight=2)

        columns = (
            "seq", "slot", "move", "victim", "a", "b", "route",
            "authored", "resolved", "observed", "outcome", "reaction", "confidence",
        )
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        headings = {
            "seq": "Seq", "slot": "Attacker", "move": "Move", "victim": "Victim",
            "a": "Property A", "b": "Property B", "route": "B route, inferred",
            "authored": "Authored", "resolved": "Resolved", "observed": "HP loss",
            "outcome": "Outcome", "reaction": "Immediate / terminal", "confidence": "Correlation",
        }
        widths = {
            "seq": 64, "slot": 78, "move": 190, "victim": 92, "a": 90, "b": 90,
            "route": 165, "authored": 66, "resolved": 66, "observed": 66,
            "outcome": 90, "reaction": 175, "confidence": 100,
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            self.tree.column(column, width=widths[column], minwidth=45, stretch=column in {"move", "reaction", "route"})
        ybar = ttk.Scrollbar(left, orient="vertical", command=self.tree.yview)
        xbar = ttk.Scrollbar(left, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        left.rowconfigure(0, weight=1)
        left.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        self.notebook = ttk.Notebook(right)
        self.notebook.pack(fill="both", expand=True)
        self.contact_text = self._add_text_tab("Contact")
        self.damage_text = self._add_text_tab("Damage / Outcome")
        self.defender_text = self._add_text_tab("Defender")
        self.source_text = self._add_text_tab("Source / Route")
        self.raw_text = self._add_text_tab("Raw JSON")

        footer = ttk.Frame(outer, style="Research.TFrame")
        footer.pack(fill="x", pady=(8, 0))
        self.paths_var = tk.StringVar(value="")
        ttk.Label(footer, textvariable=self.paths_var, style="Research.TLabel").pack(side="left")

    def _add_text_tab(self, title: str) -> tk.Text:
        frame = ttk.Frame(self.notebook, style="Card.TFrame")
        text = tk.Text(
            frame,
            wrap="none",
            bg="#0f1621",
            fg="#dce8f5",
            insertbackground="#ffffff",
            selectbackground="#51447d",
            relief="flat",
            font=("Consolas", 9),
            padx=10,
            pady=10,
        )
        ybar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        xbar = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        text.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.notebook.add(frame, text=title)
        return text

    def _toggle_capture(self) -> None:
        self.engine.set_capture_enabled(bool(self.capture_var.get()))

    def _toggle_pause(self) -> None:
        self._paused = bool(self.pause_var.get())

    def _matches_filter(self, record: dict) -> bool:
        slot_filter = str(self.slot_filter_var.get() or "ALL").upper()
        if slot_filter != "ALL" and str(record.get("attacker_slot") or "").upper() != slot_filter:
            return False
        query = str(self.search_var.get() or "").strip().lower()
        if not query:
            return True
        haystack = " ".join(
            str(record.get(key) or "")
            for key in (
                "attacker_name", "attacker_slot", "action_name", "victim_name", "victim_slot",
                "property_a_text", "property_b_text", "property_b_route_inference", "outcome",
                "reaction_phase_after", "victim_action_path", "source_kind", "source_confidence",
            )
        ).lower()
        return query in haystack

    def _row_values(self, record: dict) -> tuple[str, ...]:
        route = str(record.get("property_b_route_inference") or "-")
        if len(route) > 34:
            route = route[:31] + "..."
        action_after = _safe_int(record.get("victim_action_after"))
        action_terminal = _safe_int(record.get("victim_action_terminal"), action_after)
        reaction = f"{action_after:04X}"
        if action_terminal != action_after:
            reaction += f" -> {action_terminal:04X}"
        phase = str(record.get("reaction_phase_after") or "")
        if phase:
            reaction += f" {phase}"
        confidence = f"{record.get('source_confidence') or 'low'} {float(record.get('source_score') or 0.0):.0f}"
        authored_known = bool(record.get("authored_damage_known", record.get("base_damage_known")))
        authored_text = (
            str(_safe_int(record.get("authored_damage", record.get("base_damage"))))
            if authored_known else "?"
        )
        resolved_text = (
            str(_safe_int(record.get("resolved_damage")))
            if record.get("resolved_damage_known") else "?"
        )
        observed_damage = _safe_int(
            record.get("observed_hp_loss"),
            _safe_int(record.get("contact_hp_delta"), _safe_int(record.get("final_damage"))),
        )
        outcome = str(record.get("outcome") or "pending")
        if record.get("coalesced_contacts_suspected"):
            outcome += " x2?"
        return (
            str(_safe_int(record.get("sequence"))),
            str(record.get("attacker_slot") or record.get("attacker_name") or "?"),
            str(record.get("action_name") or f"0x{_safe_int(record.get('action_id')):04X}"),
            str(record.get("victim_slot") or record.get("victim_name") or "?"),
            _short_hex(record.get("property_a")),
            _short_hex(record.get("property_b")),
            route,
            authored_text,
            resolved_text,
            str(observed_damage),
            outcome,
            reaction,
            confidence,
        )

    def _rebuild_rows(self, records: list[dict] | None = None) -> None:
        if records is None:
            records = list(self._row_records.values())
        selected_seq = self._selected_sequence()
        self.tree.delete(*self.tree.get_children())
        self._known_sequences.clear()
        self._row_records.clear()
        visible = [row for row in records if self._matches_filter(row)][-MAX_VISIBLE_ROWS:]
        for record in visible:
            seq = _safe_int(record.get("sequence"))
            item = f"seq_{seq}"
            self.tree.insert("", "end", iid=item, values=self._row_values(record))
            self._known_sequences.add(seq)
            self._row_records[item] = record
        if selected_seq and f"seq_{selected_seq}" in self.tree.get_children(""):
            self.tree.selection_set(f"seq_{selected_seq}")
            self.tree.see(f"seq_{selected_seq}")
        elif visible and self.autoscroll_var.get():
            last = f"seq_{_safe_int(visible[-1].get('sequence'))}"
            self.tree.selection_set(last)
            self.tree.see(last)
            self._show_record(visible[-1])

    def _selected_sequence(self) -> int:
        selection = self.tree.selection()
        if not selection:
            return self._last_selection_sequence
        record = self._row_records.get(selection[0], {})
        return _safe_int(record.get("sequence"))

    def _on_select(self, _event=None) -> None:
        selection = self.tree.selection()
        if not selection:
            return
        record = self._row_records.get(selection[0])
        if isinstance(record, dict):
            self._last_selection_sequence = _safe_int(record.get("sequence"))
            self._show_record(record)

    def _set_text(self, widget: tk.Text, content: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", content)
        widget.configure(state="disabled")

    def _show_record(self, record: dict) -> None:
        result_candidates = [
            _hex32(value) for value in (record.get("property_a_result_candidates") or [])
        ]
        final_candidates = [
            _hex32(value) for value in (record.get("property_a_final_candidates") or [])
        ]
        matched = ", ".join(str(value) for value in (record.get("matched_phase_indices") or [])) or "-"
        contact_lines = [
            "READ-ONLY CORRELATED CONTACT",
            f"Sequence / series : {record.get('sequence')} / {record.get('series_id', '-')} hit {record.get('series_hit_index', '-')}",
            f"Frame             : {record.get('frame')} -> {record.get('terminal_frame', '-')}",
            f"Terminal reason   : {record.get('terminal_reason') or 'pending'}",
            f"Time              : {record.get('timestamp_utc')}",
            f"Attacker          : {record.get('attacker_slot') or '-'} {record.get('attacker_name') or ''} @ {_hex32(record.get('attacker_base'))}",
            f"Victim            : {record.get('victim_slot') or '-'} {record.get('victim_name') or ''} @ {_hex32(record.get('victim_base'))}",
            f"Move              : 0x{_safe_int(record.get('action_id')):04X} {record.get('action_name') or ''}",
            f"Trigger           : {', '.join(record.get('trigger_reasons') or [])}",
            "",
            f"Property A raw    : {_hex32(record.get('property_a'))}",
            f"                    {record.get('property_a_text') or ''}",
            f"A class           : {_hex32(record.get('property_a_class'))}",
            f"A result flags    : {_hex32(record.get('property_a_result_flags')) if _safe_int(record.get('property_a_result_flags')) else '-'}",
            f"                    {record.get('property_a_result_text') or ''}",
            f"A result candidates: {', '.join(result_candidates) or '-'}",
            f"A final candidates : {', '.join(final_candidates) or '-'}",
            f"Definition phases : {_safe_int(record.get('definition_phase_count'))}  matched={matched}",
            f"Property B        : {_hex32(record.get('property_b'))}",
            f"                    {record.get('property_b_text') or ''}",
            f"Phase A / B       : {_hex32(record.get('phase_property_a'))} / {_hex32(record.get('phase_property_b'))}",
            f"Runtime status 20 : {_hex32(record.get('runtime_status_20'))}",
            f"Actor             : {_hex32(record.get('actor'))}",
            "",
            f"Source            : {record.get('source_kind') or '-'}",
            f"Correlation       : {record.get('source_confidence') or '-'} ({float(record.get('source_score') or 0.0):.2f})",
            f"Source age        : {_safe_int(record.get('source_age_frames'))} frames",
            f"Why               : {'; '.join(record.get('source_score_reasons') or [])}",
        ]
        self._set_text(self.contact_text, "\n".join(contact_lines))

        ratio = record.get("base_to_final_ratio")
        ratio_text = f"{float(ratio) * 100.0:.2f}%" if isinstance(ratio, (int, float)) else "-"
        authored_known = bool(record.get("authored_damage_known", record.get("base_damage_known")))
        authored_text = (
            str(_safe_int(record.get("authored_damage", record.get("base_damage"))))
            if authored_known else "Unknown"
        )
        resolved_text = (
            str(_safe_int(record.get("resolved_damage")))
            if record.get("resolved_damage_known") else "Unknown"
        )
        attributed = record.get("attributed_damage")
        attributed_text = "Unknown" if attributed in (None, "") else str(_safe_int(attributed))
        calc_text = (
            str(_safe_int(record.get("damage_calc_output")))
            if record.get("damage_calc_output_known") else "Unknown"
        )
        damage_lines = [
            f"Outcome           : {record.get('outcome') or 'pending'}",
            f"Authored damage   : {authored_text}",
            f"Calculator output : {calc_text}",
            f"Applied damage    : {resolved_text}",
            f"Calculator aux    : {record.get('damage_calc_aux') if record.get('native_damage_calc_complete') else 'Unknown'}",
            f"Observed HP loss  : {_safe_int(record.get('observed_hp_loss'), _safe_int(record.get('contact_hp_delta'), _safe_int(record.get('final_damage'))))}",
            f"Attributed damage : {attributed_text}",
            f"Attribution       : {record.get('damage_attribution_source') or '-'}  confident={bool(record.get('damage_attribution_confident'))}",
            f"Calculator complete: {bool(record.get('native_damage_calc_complete'))}",
            f"Application complete: {bool(record.get('native_damage_complete'))}",
            f"Last-hit value    : {_safe_int(record.get('last_hit_value'))}",
            f"Same-frame remainder: {_safe_int(record.get('same_frame_unattributed_damage'))}",
            f"Clamped by remaining HP: {_safe_int(record.get('damage_clamped_by_remaining_hp'))}",
            f"Evidence          : {record.get('contact_evidence_kind') or '-'}",
            f"State-only candidate: {bool(record.get('state_only_contact_candidate'))}",
            f"Coalesced contact : {bool(record.get('coalesced_contacts_suspected'))}  estimate={_safe_int(record.get('coalesced_contact_count_estimate'), 1)}",
            f"Later damage excluded: {_safe_int(record.get('followthrough_damage_ignored'))}",
            f"Chip damage       : {_safe_int(record.get('chip_damage'))}",
            f"Authored to attributed: {ratio_text}",
            "",
            f"Victim HP contact : {_safe_int(record.get('hp_before'))} -> {_safe_int(record.get('hp_after'))} / {_safe_int(record.get('max_hp'))}",
            f"Victim HP terminal: {_safe_int(record.get('terminal_hp'))}",
            f"Recoverable contact: {_safe_int(record.get('recoverable_before'))} -> {_safe_int(record.get('recoverable_after'))} ({_safe_int(record.get('recoverable_delta')):+d})",
            f"Recoverable terminal: {_safe_int(record.get('recoverable_terminal'))}",
            "",
            f"Attacker meter contact: {_delta(record.get('attacker_meter_before'), record.get('attacker_meter_after'))}",
            f"Attacker meter terminal: {_safe_int(record.get('attacker_meter_terminal'))}",
            f"Victim meter contact: {_delta(record.get('victim_meter_before'), record.get('victim_meter_after'))}",
            f"Victim meter terminal: {_safe_int(record.get('victim_meter_terminal'))}",
            f"Combo contact     : {_delta(record.get('combo_before'), record.get('combo_after'))}",
            f"Combo terminal    : {_safe_int(record.get('combo_terminal'))}",
            f"Combo scale       : {float(record.get('combo_scale_before') or 1.0):.4f} -> {float(record.get('combo_scale_after') or 1.0):.4f} -> {float(record.get('combo_scale_terminal') or 1.0):.4f}",
            f"Team correction   : {float(record.get('team_correction') or 1.0):.4f}",
            f"Baroque active    : {bool(record.get('baroque_active'))}  red spent={_safe_int(record.get('baroque_red_spent'))}",
            f"Roll power flags  : {_hex32(record.get('roll_power_flags'))}  puddles={_safe_int(record.get('puddle_stacks'))}",
        ]
        self._set_text(self.damage_text, "\n".join(damage_lines))

        defender_lines = [
            f"Immediate action  : 0x{_safe_int(record.get('victim_action_after')):04X} {record.get('reaction_phase_after') or '-'}",
            f"Terminal action   : 0x{_safe_int(record.get('victim_action_terminal')):04X} {record.get('reaction_phase_terminal') or '-'}",
            f"Action path       : {record.get('victim_action_path') or '-'}",
            f"Reaction path     : {record.get('reaction_phase_path') or '-'}",
            f"Max blockstun     : {_safe_int(record.get('max_blockstun'))}",
            f"Max hitstun       : {_safe_int(record.get('max_hitstun'))}",
            f"Reaction family   : {_hex32(record.get('reaction_family_before'))} -> {_hex32(record.get('reaction_family_after'))} -> {_hex32(record.get('reaction_family_terminal'))}",
            "",
            f"Victim X contact  : {_value(record.get('position_x_before'))} -> {_value(record.get('position_x_after'))}",
            f"Victim X terminal : {_value(record.get('position_x_terminal'))}",
            f"Normalized Y contact: {_value(record.get('position_y_before'))} -> {_value(record.get('position_y_after'))}",
            f"Normalized Y terminal: {_value(record.get('position_y_terminal'))}",
            f"Contact velocity estimate: X {_value(record.get('velocity_x_est'))}  Y {_value(record.get('velocity_y_est'))} units/frame",
            "",
            f"Relative offset contact : X {_value(record.get('relative_x_contact'))}  Y {_value(record.get('relative_y_contact'))}",
            f"Relative offset terminal: X {_value(record.get('relative_x_terminal'))}  Y {_value(record.get('relative_y_terminal'))}",
            f"Contact offset drift    : {_value(record.get('relative_offset_max_drift'))}",
            f"Series offset drift     : {_value(record.get('series_relative_offset_max_drift'))}",
            f"Series attacker travel  : {_value(record.get('series_attacker_travel'))}",
            f"Series victim travel    : {_value(record.get('series_victim_travel'))}",
            f"Series motion mismatch  : {_value(record.get('series_motion_mismatch'))}",
            f"Series stabilized       : {bool(record.get('series_position_stabilized_observed'))}",
            "",
            f"Knockdown seen    : {bool(record.get('knockdown_observed'))}",
            f"Wall reaction seen: {bool(record.get('wall_reaction_observed'))}",
            f"Air recovery seen : {bool(record.get('air_recovery_observed'))}",
            f"Position stabilized: {bool(record.get('position_stabilized_observed'))}",
            f"Samples           : {_safe_int(record.get('sample_count'))}  complete={bool(record.get('post_complete'))}",
        ]
        attacker_by_frame = {
            _safe_int(sample.get("frame")): sample
            for sample in (record.get("attacker_post_samples") or [])
            if isinstance(sample, dict)
        }
        for sample in record.get("post_samples") or []:
            frame = _safe_int(sample.get("frame"))
            attacker_sample = attacker_by_frame.get(frame, {})
            rel_x = _safe_int(0)
            try:
                rel_x = float(sample.get("x") or 0.0) - float(attacker_sample.get("x") or 0.0)
                rel_y = abs(float(sample.get("y") or 0.0)) - abs(float(attacker_sample.get("y") or 0.0))
            except Exception:
                rel_x, rel_y = 0.0, 0.0
            defender_lines.append(
                f"  f{frame:6d}  HP {_safe_int(sample.get('hp')):6d}  "
                f"act {_safe_int(sample.get('action_id')):04X}  HS {_safe_int(sample.get('hitstun')):3d}  "
                f"BS {_safe_int(sample.get('blockstun')):3d}  {sample.get('reaction_phase') or '-':12s}  "
                f"V {float(sample.get('x') or 0.0):7.3f}/{abs(float(sample.get('y') or 0.0)):7.3f}  "
                f"A {float(attacker_sample.get('x') or 0.0):7.3f}/{abs(float(attacker_sample.get('y') or 0.0)):7.3f}  "
                f"rel {rel_x:7.3f}/{rel_y:7.3f}"
            )
        self._set_text(self.defender_text, "\n".join(defender_lines))

        source = record.get("source") or {}
        phase_rows = source.get("definition_phases") or []
        source_lines = [
            "PROPERTY B ROUTE IS INFERRED FROM THE OBSERVED WORD",
            "No resolver registers, result pointers, code hooks, or route-helper return values are used.",
            "",
            f"Inferred route    : {record.get('property_b_route_inference') or '-'}",
            f"Source kind       : {record.get('source_kind') or '-'}",
            f"Packet state      : {source.get('packet_state') or '-'}",
            f"Definition status : {source.get('definition_status') or '-'}",
            f"Definition source : {source.get('definition_source') or '-'}",
            f"Victim hint       : {source.get('victim_slot_hint') or '-'}",
            f"Projectile ID     : 0x{_safe_int(source.get('projectile_id')):04X}",
            f"Cleanup candidate : {bool(source.get('cleanup_candidate'))}",
            f"Result ambiguous  : {bool(record.get('property_a_result_ambiguous'))}",
            "",
            "Definition phase candidates:",
        ]
        if phase_rows:
            for phase in phase_rows:
                source_lines.append(
                    f"  #{_safe_int(phase.get('phase_index')):02d} "
                    f"A {_hex32(phase.get('property_a'))} "
                    f"B {_hex32(phase.get('property_b'))} "
                    f"result {_hex32(phase.get('property_a_result_flags')) if phase.get('property_a_result_flags') is not None else '-'} "
                    f"final {_hex32(phase.get('property_a_final'))} "
                    f"script +0x{_safe_int(phase.get('script_offset')):04X}"
                )
        else:
            source_lines.append("  none")
        source_lines.extend([
            "",
            f"Correlation notes : {record.get('correlation_notes') or '-'}",
        ])
        self._set_text(self.source_text, "\n".join(source_lines))
        self._set_text(self.raw_text, json.dumps(record, indent=2, sort_keys=True))

    def _clear(self) -> None:
        self.engine.clear_history()
        self.tree.delete(*self.tree.get_children())
        self._known_sequences.clear()
        self._row_records.clear()
        for widget in (self.contact_text, self.damage_text, self.defender_text, self.source_text, self.raw_text):
            self._set_text(widget, "")
        self.status_var.set("Read-only research history cleared.")

    def _copy_selected(self) -> None:
        selection = self.tree.selection()
        record = self._row_records.get(selection[0]) if selection else None
        if not isinstance(record, dict):
            self.status_var.set("Select a contact first.")
            return
        payload = json.dumps(record, indent=2, sort_keys=True)
        try:
            self.window.clipboard_clear()
            self.window.clipboard_append(payload)
            self.window.update_idletasks()
            self.status_var.set(f"Copied contact {record.get('sequence')}.")
        except Exception as exc:
            self.status_var.set(f"Copy failed: {exc}")

    def _export_csv(self) -> None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            parent=self.window,
            title="Export read-only contacts CSV",
            defaultextension=".csv",
            initialfile=f"tvc_readonly_attack_contacts_{stamp}.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        ok = self.engine.export_contacts_csv(path)
        self.status_var.set(f"Exported {os.path.basename(path)}." if ok else "Contact CSV export failed.")

    def _export_sources_csv(self) -> None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            parent=self.window,
            title="Export read-only sources CSV",
            defaultextension=".csv",
            initialfile=f"tvc_readonly_attack_sources_{stamp}.csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        ok = self.engine.export_sources_csv(path)
        self.status_var.set(f"Exported {os.path.basename(path)}." if ok else "Source CSV export failed.")

    def _export_json(self) -> None:
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = filedialog.asksaveasfilename(
            parent=self.window,
            title="Export read-only attack research JSON",
            defaultextension=".json",
            initialfile=f"tvc_readonly_attack_research_{stamp}.json",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        ok = self.engine.export_json(path)
        self.status_var.set(f"Exported {os.path.basename(path)}." if ok else "JSON export failed.")

    def _poll(self) -> None:
        if self._closing:
            return
        snap = self.engine.snapshot(limit=MAX_VISIBLE_ROWS)
        self.capture_var.set(bool(snap.get("enabled")))
        self.status_var.set(
            f"{snap.get('status', 'PAUSED')}  contacts={snap.get('contact_count', 0)}  "
            f"sources={snap.get('source_count', 0)}  pending={snap.get('pending_post_count', 0)}  "
            f"echoes={snap.get('echo_suppressed_count', 0)}  resets={snap.get('identity_reset_count', 0)}  "
            f"frames={snap.get('observed_frames', 0)}"
        )
        self.paths_var.set(
            f"Contacts: {snap.get('contact_csv_path', '')}    Sources: {snap.get('source_csv_path', '')}"
        )

        if not self._paused:
            records = [row for row in snap.get("contacts") or [] if isinstance(row, dict)]
            latest_signature = tuple(
                (
                    _safe_int(row.get("sequence")),
                    bool(row.get("post_complete")),
                    _safe_int(row.get("final_damage")),
                    _safe_int(row.get("victim_action_after")),
                    len(row.get("post_samples") or []),
                )
                for row in records[-32:]
            )
            if latest_signature != self._latest_signature or any(
                _safe_int(row.get("sequence")) not in self._known_sequences for row in records
            ):
                self._latest_signature = latest_signature
                self._rebuild_rows(records)

        self._schedule_poll()

    def _schedule_poll(self, *, initial: bool = False) -> None:
        delay = 20 if initial else POLL_MS
        try:
            self._after_id = self.window.after(delay, self._poll)
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
        self.engine.set_capture_enabled(False)
        try:
            self.window.destroy()
        except Exception:
            pass
        if _ACTIVE_WINDOW is self:
            _ACTIVE_WINDOW = None


def open_attack_resolver_window() -> None:
    """Open or focus the dedicated read-only attack research window."""
    engine = get_attack_resolver_research()

    def create(master: tk.Misc) -> None:
        global _ACTIVE_WINDOW
        existing = _ACTIVE_WINDOW
        try:
            if existing is not None and bool(existing.window.winfo_exists()):
                existing.window.deiconify()
                existing.window.lift()
                existing.window.focus_force()
                existing.capture_var.set(True)
                existing.engine.set_capture_enabled(True)
                return
        except Exception:
            _ACTIVE_WINDOW = None
        _ACTIVE_WINDOW = AttackResolverWindow(master, engine)

    tk_call(create)


__all__ = ["AttackResolverWindow", "open_attack_resolver_window"]
