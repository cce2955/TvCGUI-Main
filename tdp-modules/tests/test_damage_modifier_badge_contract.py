from __future__ import annotations

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main.py"
COMPONENTS = ROOT / "tvcgui" / "ui" / "components.py"
MASTER = ROOT / "tvcgui" / "features" / "overlay" / "master_renderer.py"
HUD = ROOT / "tvcgui" / "features" / "overlay" / "hud_renderer.py"
SCALING = ROOT / "tvcgui" / "features" / "overlay" / "damage_scaling.py"


def _load_scaling_module():
    spec = importlib.util.spec_from_file_location("damage_badge_contract", SCALING)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_live_roll_modifier_multiplies_buffs_separately():
    module = _load_scaling_module()
    slots = {
        "P1-C1": {
            "id": 19,
            "cur": 9000,
            "max": 10000,
            "damage_combo_scale": 1.0,
            "damage_roll_power_flags": 1,
            "damage_roll_puddle_stacks": 3,
            "damage_team_correction": 1.0,
        },
        "P2-C1": {
            "cur": 9000,
            "max": 10000,
            "damage_height": 0.0,
        },
    }
    result = module.build_live_damage_modifier(slots, "P1-C1", "P2-C1")
    assert abs(result["multiplier"] - 1.43) < 0.0001
    assert "ROLL 110%" in result["factors"]
    assert "PUDDLES 130%" in result["factors"]


def test_damage_button_toggles_native_scaling_rows_independently():
    main_source = MAIN.read_text(encoding="utf-8")
    component_source = COMPONENTS.read_text(encoding="utf-8")
    hud_source = HUD.read_text(encoding="utf-8")
    assert "show_damage_badge = not show_damage_badge" in main_source
    assert '"Dmg: ON" if show_damage_badge else "Dmg: OFF"' in component_source
    assert "def _draw_compact_damage_scaling_rows" in hud_source
    assert "slot_rows = sorted(" in hud_source
    assert 'key=lambda item: 0 if item[0].endswith("C1") else 1' in hud_source


def test_point_selection_uses_stable_44a0_field():
    hud_source = HUD.read_text(encoding="utf-8")
    scaling_source = SCALING.read_text(encoding="utf-8")
    assert "def _damage_point_slot" in hud_source
    assert 'snapshot.get("damage_point_active"' in hud_source
    assert "base + 0x44A0" in scaling_source
    assert '"damage_combo_lane_active"' in scaling_source
    assert '"damage_baroque_permission"' in scaling_source


def test_live_modifier_splits_team_owner_from_active_fighter():
    module = _load_scaling_module()
    slots = {
        "P1-C1": {
            "id": 12,
            "cur": 48000,
            "max": 48000,
            "damage_live": True,
            "damage_combo_scale": 0.70,
            "damage_script_state_active": False,
            "damage_team_correction": 1.0,
        },
        "P1-C2": {
            "id": 19,
            "cur": 46000,
            "max": 46000,
            "damage_live": True,
            "damage_point_active": True,
            "damage_combo_scale": 1.0,
            "damage_roll_power_flags": 1,
            "damage_roll_puddle_stacks": 5,
            "damage_team_correction": 1.0,
        },
        "P2-C1": {
            "cur": 44000,
            "max": 44000,
            "damage_live": True,
            "damage_height": 0.0,
        },
    }
    result = module.build_live_damage_modifier(
        slots,
        "P1-C2",
        "P2-C1",
        owner_slot="P1-C1",
    )
    assert abs(result["multiplier"] - (0.70 * 1.10 * 1.50)) < 0.0001
    assert "TEAM SCALE 70%" in result["factors"]
    assert "ROLL 110%" in result["factors"]
    assert "PUDDLES 150%" in result["factors"]
    assert result["attacker_slot"] == "P1-C2"
    assert result["owner_slot"] == "P1-C1"


def test_badge_has_two_hybrid_team_rows():
    source = HUD.read_text(encoding="utf-8")
    assert 'p1 = _damage_point_slot("P1")' in source
    assert 'p2 = _damage_point_slot("P2")' in source
    assert 'owner_slot = _damage_team_owner_slot(team, attacker_slot)' in source
    assert 'owner_slot=owner_slot' in source
    assert 'CURRENT DAMAGE MODIFIER  ·  LIVE FIGHTERS' in source
    assert "ALL SLOTS" not in source
    assert "ACTIVE ONLY" not in source


def test_badge_labels_use_character_names_not_c1_c2_identity():
    source = HUD.read_text(encoding="utf-8")
    assert 'attacker_name = _compact_trim(str(attacker.get("name") or "---"), 16)' in source
    assert 'label = f"{team}  {attacker_name}  >  {victim_team} {victim_name}"' in source
    assert 'slot_short = attacker_slot.rsplit' not in source
    assert 'data["source_slot"] = attacker_slot' in source


def test_live_state_refreshes_without_waiting_for_damage():
    source = SCALING.read_text(encoding="utf-8")
    assert "periodic_refresh = (_POLL_COUNTER % 4) == 0" in source
    assert "or periodic_refresh" in source


def test_badge_wraps_full_factors_and_draws_modifier_meter():
    source = HUD.read_text(encoding="utf-8")
    assert "def _wrap_damage_factor_lines" in source
    assert "def _draw_damage_modifier_meter" in source
    assert "meter_max_percent = 200.0" in source
    assert "baseline_x = rect.x + int(round(rect.width * 0.5))" in source
    assert "_compact_trim(factor_text, 35)" not in source
    assert "factor_surfaces = [" in source


def test_master_control_remains_backward_compatible():
    source = MASTER.read_text(encoding="utf-8")
    assert "show_damage_badge: bool = False" in source
    assert "show_damage_inactive: bool = True" in source
