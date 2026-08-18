from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MONITOR = ROOT / "tvcgui/runtime/input_monitor.py"
SAMPLER = ROOT / "tvcgui/runtime/realtime_sampler.py"
MANAGER = ROOT / "tvcgui/features/overlay/manager.py"
HUD = ROOT / "tvcgui/features/overlay/hud_renderer.py"


def _extract_function(path: Path, name: str):
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.Module(body=[fn], type_ignores=[])
    ns = {}
    exec(compile(ast.fix_missing_locations(module), str(path), "exec"), ns)
    return ns[name]


def test_realtime_packet_reads_native_blockstun_and_impact_freeze_from_existing_blob():
    source = MONITOR.read_text(encoding="utf-8")
    assert "RUNTIME_BLOCKSTUN_REMAINING_OFF" in source
    assert "RUNTIME_IMPACT_FREEZE_OFF" in source
    assert "realtime_span_end = 0x44A4" in source
    assert "blockstun_remaining = blob_u32(RUNTIME_BLOCKSTUN_REMAINING_OFF)" in source
    assert "impact_freeze_remaining = blob_u32(RUNTIME_IMPACT_FREEZE_OFF)" in source
    assert '"blockstun_remaining": blockstun_remaining' in source
    assert '"impact_freeze_remaining": impact_freeze_remaining' in source


def test_realtime_sampler_propagates_blockstun_changes_to_listener_lane():
    source = SAMPLER.read_text(encoding="utf-8")
    assert 'blockstun_remaining = max(0, int(packet.get("blockstun_remaining", 0) or 0))' in source
    assert 'impact_freeze_remaining = max(0, int(packet.get("impact_freeze_remaining", 0) or 0))' in source
    assert "or blockstun_changed" in source
    assert "or impact_freeze_changed" in source
    assert '"blockstun_remaining": blockstun_remaining' in source


def test_block_contact_generation_uses_native_blockstun_and_freeze_rearm():
    source = MANAGER.read_text(encoding="utf-8")
    start = source.index("def _emit_realtime_blockstun_contact(")
    end = source.index("\n    def _on_realtime_input_sample", start)
    body = source[start:end]
    assert 'current_blockstun = max(0, int(sample.get("blockstun_remaining", 0) or 0))' in body
    assert "previous_blockstun <= 0" in body
    assert "current_blockstun > previous_blockstun" in body
    assert "current_freeze > 0 and current_freeze > previous_freeze" in body
    assert '"target": current_blockstun' in body
    assert '"native_blockstun": current_blockstun' in body
    assert "_bs_generation_by_team" in body


def test_realtime_sidecar_carries_blockstun_team_events():
    source = MANAGER.read_text(encoding="utf-8")
    assert '"bs_teams": bs_teams' in source
    assert '"blockstun_remaining": max(0, int(sample.get("blockstun_remaining", 0) or 0))' in source
    assert '"impact_freeze_remaining": max(0, int(sample.get("impact_freeze_remaining", 0) or 0))' in source


def test_renderer_blockstun_clock_is_native_and_never_wall_clock_driven():
    clock = _extract_function(HUD, "_realtime_blockstun_contact_clock")
    anim = {}
    event = {
        "generation": 1,
        "target": 12,
        "native_blockstun": 12,
        "native_blockstun_current": 12,
    }
    first = clock(anim, {"realtime_blockstun_contact": dict(event)})
    assert first["remaining"] == 12
    assert first["elapsed"] == 0

    for _ in range(20):
        held = clock(anim, {"realtime_blockstun_contact": dict(event)})
        assert held["remaining"] == 12
        assert held["elapsed"] == 0

    one_frame = dict(event, native_blockstun_current=11)
    moved = clock(anim, {"realtime_blockstun_contact": one_frame})
    assert moved["remaining"] == 11
    assert moved["elapsed"] == 1

    stale = dict(event, native_blockstun_current=12)
    protected = clock(anim, {"realtime_blockstun_contact": stale})
    assert protected["remaining"] == 11

    replacement = {
        "generation": 2,
        "target": 9,
        "native_blockstun": 9,
        "native_blockstun_current": 9,
    }
    reset = clock(anim, {"realtime_blockstun_contact": replacement})
    assert reset["remaining"] == 9
    assert reset["elapsed"] == 0
    assert reset["generation"] != first["generation"]


def test_stun_panel_renders_second_persona_style_blockstun_row():
    source = HUD.read_text(encoding="utf-8")
    start = source.index("def _draw_compact_untech_scaling_row(")
    end = source.index("\ndef _research_dock_active_panel", start)
    body = source[start:end]
    assert 'font_sm.render("STUN CLOCKS"' in body
    assert 'block_left = "BLOCKSTUN"' in body
    assert 'block_right = f"{block_remaining}/{block_target}F"' in body
    assert "block_fill_w = max(0, min(block_gauge_w" in body
    assert "_realtime_blockstun_contact_clock(slot_anim, snap)" in body


def test_stun_panel_layout_reserves_two_rows():
    source = HUD.read_text(encoding="utf-8")
    assert "untech_scale_height = untech_header_h + untech_row_h * 2" in source


def test_blockstun_contact_lifetime_is_native_zero_driven_not_wall_clock_ttl():
    manager_source = MANAGER.read_text(encoding="utf-8")
    start = manager_source.index("def _emit_realtime_blockstun_contact(")
    end = manager_source.index("\n    def _on_realtime_input_sample", start)
    body = manager_source[start:end]
    assert 'if current_blockstun <= 0:' in body
    assert 'state["latest"] = {}' in body
    assert 'now_ns -' not in body

    hud_source = HUD.read_text(encoding="utf-8")
    merge_start = hud_source.index("def _merge_realtime_inputs(")
    bs_start = hud_source.index("    # Blockstun uses its own native +0x1204", merge_start)
    merge_end = hud_source.index("\n# ---------------------------------------------------------------------------\n# Drawing helpers", bs_start)
    bs_body = hud_source[bs_start:merge_end]
    assert 'enriched_bs = dict(latest_bs) if latest_bs else {}' in bs_body
    assert 'bs_fresh' not in bs_body
    assert 'victim_fresh' not in bs_body
    assert 'now_ns - bs_ns' not in bs_body
    assert 'now_ns - victim_ns' not in bs_body


def test_stun_clock_transport_is_separate_tiny_temp_ipc():
    manager = MANAGER.read_text(encoding="utf-8")
    hud = HUD.read_text(encoding="utf-8")
    assert 'HUD_REALTIME_STUN_FILE = os.path.join(tempfile.gettempdir(), "tvcgui_hud_stun_realtime.json")' in manager
    assert 'REALTIME_STUN_FILE = os.path.join(tempfile.gettempdir(), "tvcgui_hud_stun_realtime.json")' in hud
    assert 'def _stun_bridge_writer_loop' in manager
    assert 'name="TvCRealtimeStunBridge"' in manager
    assert 'if combat_changed:\n                self._stun_bridge_dirty = True' in manager
    assert 'if input_changed:\n                samples = state.setdefault("samples", [])' in manager
    assert 'def read_realtime_stun_data()' in hud
    assert 'combat_payload = stun_payload if isinstance(stun_payload, dict) and stun_payload else realtime_payload' in hud


def test_chip_hp_loss_cannot_mint_hitstun_clock():
    source = MANAGER.read_text(encoding="utf-8")
    start = source.index("def _emit_realtime_hs_contact(")
    end = source.index("\n    def _emit_realtime_blockstun_contact", start)
    body = source[start:end]
    assert 'native_blockstun = max(0, int(sample.get("blockstun_remaining", 0) or 0))' in body
    assert 'if native_blockstun > 0:' in body
    assert 'state["latest"] = {}' in body
    chip_guard = body.index('if native_blockstun > 0:')
    resolver = body.index('native = _native_hs_contact_values')
    assert chip_guard < resolver
