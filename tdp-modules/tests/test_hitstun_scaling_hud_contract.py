from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
MANAGER = ROOT / "tvcgui" / "features" / "overlay" / "manager.py"
HUD = ROOT / "tvcgui" / "features" / "overlay" / "hud_renderer.py"
MASTER = ROOT / "tvcgui" / "features" / "overlay" / "master_renderer.py"
SCALING = ROOT / "tvcgui" / "features" / "overlay" / "hitstun_scaling.py"
COMPONENTS = ROOT / "tvcgui" / "ui" / "components.py"
HELP = ROOT / "tvcgui" / "ui" / "help_window.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("hitstun_scaling_contract", SCALING)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_native_offsets_and_formula_contract():
    module = _load_module()
    assert module.OFF_COMBO_COUNT == 0x11C4
    assert module.OFF_DECAY_COUNTER == 0x11CC
    assert module.OFF_UNTECH_TIMER == 0x1220
    assert module.OFF_STATE_FLAGS_6C == 0x6C
    assert module.CANTUKEMI_MASK == 0x02000000
    assert module.HITSTUN_DECAY_RULE_INDEX == 11
    assert module.MIN_UNTECH_FRAMES == 4
    assert module.DECAY_NUMERATOR == 1
    assert module.DECAY_DENOMINATOR == 4


def test_snapshot_reports_one_lost_frame_per_four_count_units():
    module = _load_module()
    attacker = 0x90001000
    victim = 0x90002000
    values = {
        attacker + module.OFF_COMBO_COUNT: 9,
        attacker + module.OFF_DECAY_COUNTER: 9,
        victim + module.OFF_UNTECH_TIMER: 17,
        victim + module.OFF_STATE_FLAGS_6C: 0,
    }
    module._read_u32 = lambda address: values.get(address, 0)
    payload = {
        "P1-C1": {"base": attacker, "damage_point_active": True},
        "P1-C2": {"base": 0, "damage_point_active": False},
        "P2-C1": {"base": victim, "damage_point_active": True},
        "P2-C2": {"base": 0, "damage_point_active": False},
    }
    state = module._snapshot_team(payload, "P1", True)
    assert state["hitstun_decay_combo_count"] == 9
    assert state["hitstun_decay_counter"] == 9
    assert state["hitstun_decay_frames"] == 2
    assert state["hitstun_untech_remaining"] == 17
    assert state["hitstun_decay_owner_slot"] == "P1-C1"
    assert state["hitstun_decay_victim_slot"] == "P2-C1"


def test_untech_latch_reconstructs_deflated_bar():
    module = _load_module()
    module._LATCHES.clear()
    state = {
        "hitstun_untech_remaining": 21,
        "hitstun_decay_counter": 16,
        "hitstun_decay_combo_count": 16,
        "hitstun_decay_frames": 4,
        "_hitstun_victim_base": 0x90002000,
    }
    state = module._apply_untech_latch("P1", state)
    assert state["hitstun_untech_effective_start"] == 21
    assert state["hitstun_untech_base_estimate"] == 25
    assert state["hitstun_untech_latched_loss"] == 4
    assert state["hitstun_untech_approximate"] is True

    state2 = dict(state)
    state2["hitstun_untech_remaining"] = 18
    state2 = module._apply_untech_latch("P1", state2)
    assert state2["hitstun_untech_effective_start"] == 21
    assert state2["hitstun_untech_base_estimate"] == 25


def test_native_rule_off_suppresses_reported_loss():
    module = _load_module()
    attacker = 0x90001000
    victim = 0x90002000
    values = {
        attacker + module.OFF_COMBO_COUNT: 20,
        attacker + module.OFF_DECAY_COUNTER: 20,
        victim + module.OFF_UNTECH_TIMER: 8,
        victim + module.OFF_STATE_FLAGS_6C: 0,
    }
    module._read_u32 = lambda address: values.get(address, 0)
    payload = {
        "P1-C1": {"base": attacker, "damage_point_active": True},
        "P2-C1": {"base": victim, "damage_point_active": True},
    }
    state = module._snapshot_team(payload, "P1", False)
    assert state["hitstun_decay_frames"] == 0
    assert state["hitstun_decay_rule_enabled"] is False


def test_cantukemi_is_reported_separately_from_timer():
    module = _load_module()
    attacker = 0x90001000
    victim = 0x90002000
    values = {
        attacker + module.OFF_COMBO_COUNT: 8,
        attacker + module.OFF_DECAY_COUNTER: 8,
        victim + module.OFF_UNTECH_TIMER: 12,
        victim + module.OFF_STATE_FLAGS_6C: module.CANTUKEMI_MASK,
    }
    module._read_u32 = lambda address: values.get(address, 0)
    payload = {
        "P1-C1": {"base": attacker, "damage_point_active": True},
        "P2-C1": {"base": victim, "damage_point_active": True},
    }
    state = module._snapshot_team(payload, "P1", True)
    assert state["hitstun_cantukemi"] is True
    assert state["hitstun_untech_remaining"] == 12


def test_manager_annotates_after_damage_point_resolution():
    source = MANAGER.read_text(encoding="utf-8")
    damage_call = "annotate_damage_scaling_payload(payload, render_snap_by_slot)"
    stun_call = "annotate_hitstun_scaling_payload(payload, render_snap_by_slot)"
    assert damage_call in source
    assert stun_call in source
    assert source.index(damage_call) < source.index(stun_call)


def test_untech_is_its_own_main_gui_button_and_control_flag():
    main = MAIN.read_text(encoding="utf-8")
    components = COMPONENTS.read_text(encoding="utf-8")
    master = MASTER.read_text(encoding="utf-8")
    assert "show_untech_panel     = False" in main
    assert "show_untech_panel = not show_untech_panel" in main
    assert '"show_untech_panel": bool(show_untech_panel)' in main
    assert '"HS Scale: ON" if show_untech_panel else "HS Scale: OFF"' in components

    # Research preset enables HS Scale; Core leaves it off and Full retains it.
    apply_start = main.index("def _apply_hud_info_set")
    apply_end = main.index("def _mark_hud_info_set_custom", apply_start)
    preset_block = main[apply_start:apply_end]
    core_block = preset_block[preset_block.index('if normalized == "CORE"'):preset_block.index('elif normalized == "RESEARCH"')]
    research_block = preset_block[preset_block.index('elif normalized == "RESEARCH"'):preset_block.index('else:', preset_block.index('elif normalized == "RESEARCH"'))]
    full_block = preset_block[preset_block.index('else:', preset_block.index('elif normalized == "RESEARCH"')):]
    assert "show_untech_panel = False" in core_block
    assert "show_untech_panel = True" in research_block
    assert "show_untech_panel = True" in full_block
    assert 'show_untech_panel: bool = False' in master
    last_reader = master.rsplit("def _read_control_file(self) -> None:", 1)[-1]
    assert 'self.control.show_untech_panel = bool(data.get("show_untech_panel", False))' in last_reader


def test_hud_draws_a_real_deflation_gauge_not_a_percentage():
    source = HUD.read_text(encoding="utf-8")
    assert 'font_sm.render("HS SCALE"' in source
    assert 'left_text = f"DECAY -{loss}F"' in source
    assert 'right_text = f"{remaining}/{prefix}{base_est}F"' in source
    assert "hitstun_untech_base_estimate" in source
    assert "base_est - effective" in source
    assert "(235, 91, 108)" in source
    assert "show_untech_panel" in source


def test_help_explains_untech_color_segments_and_separation_from_damage():
    source = HELP.read_text(encoding="utf-8")
    assert "Hitstun Scaling" in source
    assert "Blue segment" in source
    assert "Gray segment" in source
    assert "Red segment" in source
    assert "separately from damage scaling" in source
    assert "NO TECH" in source


def test_dock_return_contract_stays_aligned_after_untech_button():
    comp_tree = ast.parse(COMPONENTS.read_text(encoding="utf-8"))
    return_count = None
    for node in ast.walk(comp_tree):
        if isinstance(node, ast.FunctionDef) and node.name == "draw_top_command_dock":
            for child in ast.walk(node):
                if isinstance(child, ast.Return) and isinstance(child.value, ast.Tuple):
                    return_count = len(child.value.elts)
    main_tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    unpack_count = None
    for node in ast.walk(main_tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "draw_top_command_dock" and isinstance(node.targets[0], ast.Tuple):
            unpack_count = len(node.targets[0].elts)
            break
    assert return_count is not None
    assert unpack_count == return_count
