from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANAGER = ROOT / "tvcgui" / "features" / "overlay" / "manager.py"
HUD = ROOT / "tvcgui" / "features" / "overlay" / "hud_renderer.py"
SCALING = ROOT / "tvcgui" / "features" / "overlay" / "damage_scaling.py"
COMPAT = ROOT / "tvcgui" / "features" / "overlay" / "damage_scaling_patch.py"


def _load_scaling_module():
    spec = importlib.util.spec_from_file_location("damage_scaling_contract", SCALING)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_manager_keeps_current_keyword_arguments():
    tree = ast.parse(MANAGER.read_text(encoding="utf-8"))
    write_data = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "write_data"
    )
    names = [arg.arg for arg in write_data.args.args]
    assert "punish_overlay" in names
    assert "timing_payload" in names


def test_manager_uses_direct_payload_annotation():
    source = MANAGER.read_text(encoding="utf-8")
    assert "annotate_damage_scaling_payload(payload, render_snap_by_slot)" in source
    assert "write_data_with_damage" not in source


def test_combo_card_records_last_hit_and_breakdown():
    source = HUD.read_text(encoding="utf-8")
    assert 'ledger["last_hit_damage"] = int(damage)' in source
    assert 'ledger["damage_breakdown_lines"] = build_damage_breakdown_lines(' in source
    assert 'title += f"  |  LAST {last_hit:,}"' in source


def test_roll_buffs_remain_separate_rows():
    module = _load_scaling_module()
    slots = {
        "P1-C1": {
            "id": 19,
            "cur": 9000,
            "max": 10000,
            "damage_live": True,
            "damage_combo_scale": 0.85,
            "damage_roll_power_flags": 1,
            "damage_roll_puddle_stacks": 3,
            "damage_team_correction": 1.0,
        },
        "P2-C1": {
            "cur": 8000,
            "max": 10000,
            "damage_height": 0.0,
        },
    }
    lines = module.build_damage_breakdown_lines(
        slots,
        "P1-C1",
        "P2-C1",
        500,
        victim_is_point=True,
    )
    text = "\n".join(lines)
    assert "ROLL POWER 110%" in text
    assert "PUDDLES ×3 130%" in text
    assert "POINT TRACK 5%→35%" in text


def test_old_import_hook_is_neutralized():
    source = COMPAT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    install = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "install_damage_scaling_patch"
    )
    assert len(install.body) == 1
    assert isinstance(install.body[0], ast.Return)
