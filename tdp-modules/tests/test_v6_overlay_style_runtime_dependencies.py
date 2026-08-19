from __future__ import annotations

import ast
from pathlib import Path

HUD = Path(__file__).resolve().parents[1] / "tvcgui" / "features" / "overlay" / "hud_renderer.py"
SOURCE = HUD.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _module_functions() -> dict[str, ast.FunctionDef]:
    return {
        node.name: node
        for node in TREE.body
        if isinstance(node, ast.FunctionDef)
    }


def _function_source(name: str) -> str:
    funcs = _module_functions()
    node = funcs[name]
    return ast.get_source_segment(SOURCE, node) or ""


def test_v6_styling_helpers_are_overlay_local_and_defined_before_use():
    funcs = _module_functions()
    required = {"_hud_brighten", "_hud_darken", "_draw_vertical_gradient"}
    assert required <= funcs.keys()

    first_style_call = min(
        SOURCE.index("_draw_vertical_gradient(", SOURCE.index("def _draw_compact_meter")),
        SOURCE.index("_hud_brighten(", SOURCE.index("def _draw_compact_meter")),
        SOURCE.index("_hud_darken(", SOURCE.index("def _draw_compact_meter")),
    )
    for helper in required:
        assert SOURCE.index(f"def {helper}") < first_style_call


def test_v6_renderers_do_not_borrow_missing_main_gui_color_helpers():
    renderers = (
        "_draw_compact_meter",
        "_draw_compact_health",
        "_draw_compact_damage_scaling_rows",
        "_draw_compact_untech_scaling_row",
    )
    funcs = _module_functions()
    calls = set()
    for name in renderers:
        for node in ast.walk(funcs[name]):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                calls.add(node.func.id)
    assert "_brighten" not in calls
    assert "_darken" not in calls
    assert "_hud_brighten" in calls
    assert "_hud_darken" in calls


def test_overlay_local_color_helpers_execute_without_pygame_namespace():
    funcs = _module_functions()
    module = ast.Module(
        body=[funcs["_hud_brighten"], funcs["_hud_darken"]],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace: dict[str, object] = {}
    exec(compile(module, str(HUD), "exec"), namespace)

    brighten = namespace["_hud_brighten"]
    darken = namespace["_hud_darken"]
    assert brighten((100, 200, 250), 20) == (120, 220, 255)
    assert darken((10, 30, 50), 20) == (0, 10, 30)


def test_realtime_meter_zero_cannot_erase_known_nonzero_main_meter():
    assert 'main_meter = max(0, min(200000, int(snap.get("meter", 0) or 0)))' in SOURCE
    assert "if live_meter > 0 or main_meter <= 0:" in SOURCE
    assert "realtime_team_meter[team] = live_meter" in SOURCE


def test_team_meter_rail_uses_resolved_team_value_not_only_point_snapshot():
    team_panel = _function_source("_draw_compact_team_panel")
    rail = _function_source("_draw_compact_meter_rail")
    assert "team_meter_target = _compact_team_meter_value(point, partner, slots.get(first_label), slots.get(second_label))" in team_panel
    assert "meter_value=team_meter_target" in team_panel
    assert "meter_value if meter_value is not None else _compact_team_meter_value(snap)" in rail


def test_hud_renderer_has_no_unresolved_private_helper_calls():
    """Catch runtime-only NameErrors for overlay helper calls before shipping."""
    import builtins

    known = set(dir(builtins))
    for node in TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            known.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                known.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                known.add(alias.asname or alias.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    known.add(target.id)

    unresolved = []
    for function in [node for node in TREE.body if isinstance(node, ast.FunctionDef)]:
        local = {
            arg.arg
            for arg in (
                list(function.args.posonlyargs)
                + list(function.args.args)
                + list(function.args.kwonlyargs)
            )
        }
        if function.args.vararg:
            local.add(function.args.vararg.arg)
        if function.args.kwarg:
            local.add(function.args.kwarg.arg)
        for node in ast.walk(function):
            if isinstance(node, ast.FunctionDef) and node is not function:
                local.add(node.name)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                local.add(node.id)
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                name = node.func.id
                if name.startswith("_") and name not in known and name not in local:
                    unresolved.append((function.name, node.lineno, name))

    assert unresolved == []
