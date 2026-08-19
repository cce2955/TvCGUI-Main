from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUD = ROOT / "tvcgui" / "features" / "overlay" / "hud_renderer.py"
MANAGER = ROOT / "tvcgui" / "features" / "overlay" / "manager.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_realtime_combat_transport_carries_action_frame():
    source = _text(MANAGER)
    assert 'int(sample.get("action_frame", 0) or 0)' in source
    assert '"action_frame": max(0, int(sample.get("action_frame", 0) or 0))' in source
    assert '"action_id", "action_frame"' in source


def test_hud_promotes_native_realtime_action_into_move_history_identity():
    source = _text(HUD)
    assert 'snap["realtime_action_id"] = live_action' in source
    assert 'snap["realtime_action_frame"] = live_action_frame' in source
    assert 'snap["mv_id_display"] = live_action' in source
    assert 'snap["mv_label"] = f"0x{live_action:04X}"' in source


def test_move_history_renders_only_the_resolved_move_label():
    manager = _text(MANAGER)
    hud = _text(HUD)
    # Metadata may still exist elsewhere in the HUD/research pipeline, but the
    # diagnostic MOVES row must not append it to the move chip.
    assert '"move_jump_cancel"' in manager
    assert '"move_invuln"' in manager
    assert '"move_protection"' in manager
    assert 'secondary=flags' not in hud
    assert 'def _compact_move_metadata_flags' not in hud
    assert '"flags": direct_flags' not in hud
    assert 'newest["flags"]' not in hud
    assert 'or "jump" in str(matched_move.get("cancel_probe") or "").lower()' not in manager


def test_meter_gain_pop_and_max_are_decorative_not_delayed_state():
    source = _text(HUD)
    assert 'team_anim["meter_gain_flash"] = 1.0' in source
    assert 'team_anim["meter_stock_pop"] = 1.0' in source
    assert 'team_anim["meter_max_flash"] = 1.0' in source
    assert '# Gains remain realtime.' in source
    assert 'team_anim["meter_display_value"] = meter_target_f' in source


def test_damage_scale_change_uses_existing_pulse_for_marker_and_value():
    source = _text(HUD)
    assert 'slot_anim["damage_scale_pulse"] = 1.0' in source
    assert '"pulse": float(slot_anim.get("damage_scale_pulse", 0.0))' in source
    assert 'value_color = _lerp_color(item["gauge_color"], (255, 255, 255), pulse * 0.78)' in source


def test_stun_generation_and_expiration_have_distinct_feedback():
    source = _text(HUD)
    assert 'slot_anim["stun_generation_flash"] = 1.0' in source
    assert 'slot_anim["stun_expire_flash"] = 1.0' in source
    assert 'slot_anim["bs_generation_flash"] = 1.0' in source
    assert 'slot_anim["bs_expire_flash"] = 1.0' in source
    assert 'f"{hit_label} 0/{target}"' in source
    assert 'f"BS 0/{block_target}"' in source


def test_bbq_ko_and_numeric_change_feedback_are_present():
    source = _text(HUD)
    assert 'slot_anim["baroque_change_flash"] = 1.0' in source
    assert 'change_flash: float = 0.0' in source
    assert 'point_anim["ko_punch"] = 1.0' in source
    assert 'partner_anim["ko_punch"] = 1.0' in source
    assert 'slot_anim["hp_value_flash"] = 1.0' in source
    assert 'team_anim["meter_value_flash"] = 1.0' in source


def test_move_change_has_emphasis_but_no_positional_delay():
    source = _text(HUD)
    assert 'slot_anim["move_change_flash"] = 1.0' in source
    assert 'team_anim["move_history_slide"] = 0.54' not in source
    assert 'team_anim["move_history_slide"] = 0.0' in source
    assert 'emphasis=1.10 if idx == 0 else 1.0' in source


def test_move_history_identity_is_native_action_id_first():
    source = _text(HUD)
    assert 'f"id:{move_id_key}"' in source
    assert 'f"label:{base_move_label.lower()}"' in source


def test_native_action_edges_are_queued_on_tiny_combat_transport():
    manager = _text(MANAGER)
    hud = _text(HUD)
    assert 'state.setdefault("actions", [])' in manager
    assert '"actions": [dict(item) for item in state.get("actions", ())[-24:]]' in manager
    assert 'snap["realtime_action_samples"] = fresh_action_samples[-24:]' in hud
    assert 'pending_actions.sort' in hud
    assert '"sample_ns": sample_ns' in hud
