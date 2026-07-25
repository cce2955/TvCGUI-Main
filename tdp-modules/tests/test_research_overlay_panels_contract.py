from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_master_control_persists_all_research_panel_flags() -> None:
    text = _read("tvcgui/features/overlay/master_renderer.py")
    for token in (
        "show_damage_badge",
        "show_meter_panel",
        "show_red_health_panel",
        "show_attack_property_panel",
    ):
        assert f"{token}: bool" in text
        assert f'"{token}": self.control.{token}' in text
        assert f'data.get("{token}"' in text


def test_main_dock_has_independent_native_research_buttons_and_clear_behavior() -> None:
    text = _read("main.py")
    for token in (
        "meter_panel_btn_rect",
        "red_health_panel_btn_rect",
        "attack_property_panel_btn_rect",
    ):
        assert token in text
    assert "show_damage_badge = not show_damage_badge" in text
    assert "show_meter_panel = not show_meter_panel" in text
    assert "show_red_health_panel = not show_red_health_panel" in text
    assert "show_attack_property_panel = not show_attack_property_panel" in text
    assert "show_damage_badge = False" in text
    assert "show_meter_panel = False" in text
    assert "show_red_health_panel = False" in text
    assert "show_attack_property_panel = False" in text


def test_command_dock_return_count_matches_main_assignment() -> None:
    components_tree = ast.parse(_read("tvcgui/ui/components.py"))
    draw_fn = next(
        node for node in components_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "draw_top_command_dock"
    )
    returns = [node for node in ast.walk(draw_fn) if isinstance(node, ast.Return)]
    tuple_return = next(node.value for node in returns if isinstance(node.value, ast.Tuple))
    return_count = len(tuple_return.elts)

    main_tree = ast.parse(_read("main.py"))
    assignment_count = None
    for node in ast.walk(main_tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        if not isinstance(node.value, ast.Call):
            continue
        func = node.value.func
        if isinstance(func, ast.Name) and func.id == "draw_top_command_dock":
            target = node.targets[0]
            assert isinstance(target, ast.Tuple)
            assignment_count = len(target.elts)
            break
    assert assignment_count is not None
    assert return_count == assignment_count


def test_overlay_payload_exports_profiler_fields() -> None:
    text = _read("tvcgui/features/overlay/manager.py")
    for token in (
        '"attack_property_live_a_text"',
        '"attack_property_live_b_text"',
        '"meter_profile_last_delta"',
        '"meter_profile_last_predicted"',
        '"red_health_recoverable"',
        '"red_health_pending_current"',
        '"red_health_last_event"',
    ):
        assert token in text


def test_hud_renderer_has_four_independent_research_panels() -> None:
    text = _read("tvcgui/features/overlay/hud_renderer.py")
    for function_name in (
        "_draw_damage_modifier_badge",
        "_draw_meter_generation_panel",
        "_draw_red_health_panel",
        "_draw_attack_property_panel",
        "_draw_research_panels",
    ):
        assert f"def {function_name}" in text
    assert 'getattr(control, "show_meter_panel", False)' in text
    assert 'getattr(control, "show_red_health_panel", False)' in text
    assert 'getattr(control, "show_attack_property_panel", False)' in text
    assert "if not hud_visible and not research_visible" in text


def test_profilers_publish_live_overlay_annotations() -> None:
    meter = _read("tvcgui/features/training/meter_generation_profiler.py")
    red = _read("tvcgui/features/training/red_health_profiler.py")
    attack = _read("tvcgui/features/training/attack_property_profiler.py")
    assert 'snap["meter_profile_last_delta"]' in meter
    assert 'snap["meter_profile_last_predicted"]' in meter
    assert 'snap["red_health_recoverable"]' in red
    assert 'snap["red_health_last_event"]' in red
    assert 'snap["attack_property_live_a_text"]' in attack
    assert 'snap["attack_property_live_b_text"]' in attack
