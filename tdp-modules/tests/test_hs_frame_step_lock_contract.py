from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUD = ROOT / "tvcgui" / "features" / "overlay" / "hud_renderer.py"
SCALING = ROOT / "tvcgui" / "features" / "overlay" / "hitstun_scaling.py"


def _extract_visual():
    source = HUD.read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_hs_visual_elapsed")
    module = ast.Module(body=[fn], type_ignores=[])
    ns = {"_frame": 0}
    exec(compile(ast.fix_missing_locations(module), str(HUD), "exec"), ns)
    return ns, ns["_hs_visual_elapsed"]


def test_visual_does_not_move_when_native_elapsed_is_held():
    ns, visual = _extract_visual()
    anim = {}
    assert visual(anim, 1000001, 5, 21, 1 / 60) == 5.0
    for frame in range(1, 20):
        ns["_frame"] = frame
        assert visual(anim, 1000001, 5, 21, 1.0) == 5.0
        assert anim["hs_visual_elapsed"] == 5.0


def test_visual_moves_exactly_one_step_when_native_elapsed_moves_one():
    ns, visual = _extract_visual()
    anim = {}
    assert visual(anim, 1000001, 5, 21, 0.0) == 5.0
    ns["_frame"] = 1
    assert visual(anim, 1000001, 6, 21, 0.0) == 6.0
    ns["_frame"] = 2
    assert visual(anim, 1000001, 6, 21, 99.0) == 6.0


def test_no_wall_clock_advancement_remains_in_hs_fallback_latch():
    source = SCALING.read_text(encoding="utf-8")
    start = source.index("def _apply_untech_latch(")
    end = source.index("\n\ndef annotate_hitstun_scaling_payload", start)
    body = source[start:end]
    assert "monotonic_ns" not in body
    assert "16_666_667" not in body
    assert "contact_elapsed" not in body


def test_compact_bar_fill_uses_exact_native_elapsed():
    source = HUD.read_text(encoding="utf-8")
    start = source.index("def _draw_compact_untech_scaling_row(")
    end = source.index("\ndef _research_dock_active_panel", start)
    body = source[start:end]
    assert "visual_elapsed = _hs_visual_elapsed(slot_anim, generation, elapsed, target, dt)" in body
    assert "visual_remaining = max(0.0, float(target) - visual_elapsed)" in body
    assert "exact_remaining = max(0, target - elapsed)" in body
