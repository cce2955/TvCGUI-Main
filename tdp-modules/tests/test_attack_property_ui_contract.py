import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUD = ROOT / "tvcgui" / "features" / "overlay" / "hud_renderer.py"
HELPERS = {
    "_attack_property_a_ui",
    "_attack_property_b_ui",
    "_attack_scaling_ui",
    "_attack_source_ui",
}


def _helper_namespace():
    tree = ast.parse(HUD.read_text(encoding="utf-8"))
    body = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in HELPERS
    ]
    namespace = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(HUD), "exec"), namespace)
    return namespace


def test_property_a_is_player_facing():
    ns = _helper_namespace()
    assert ns["_attack_property_a_ui"](0x09) == ("MID", "LIGHT")
    assert ns["_attack_property_a_ui"](0x04) == ("UNBLOCKABLE", "HEAVY")


def test_property_b_names_code_proven_routes_and_preserves_unknowns():
    ns = _helper_namespace()
    labels = [label for label, _color in ns["_attack_property_b_ui"](0x00040044)]
    assert labels == [
        "CAPTURE/CINEMATIC FAMILY",
        "CHIP 1/8",
        "CAPTURE/CINEMATIC MOD +0010",
    ]
    assert not any("UNKNOWN" in label for label in labels)

    unknown = [label for label, _color in ns["_attack_property_b_ui"](0x02000040)]
    assert unknown == ["STANDARD CONTACT", "B UNKNOWN 0x02000000"]

    correlated = [label for label, _color in ns["_attack_property_b_ui"](0x01000040)]
    assert correlated == ["STANDARD CONTACT", "REPEAT-CONTACT FAMILY (CORRELATED)"]


def test_projectile_labels_preserve_unverified_layout_boundaries():
    ns = _helper_namespace()
    labels = [
        label for label, _color in ns["_attack_property_b_ui"](
            0x00000024, source_kind="projectile"
        )
    ]
    assert labels == [
        "PROJECTILE RESULT MOD +0010 (UNVERIFIED)",
        "PROJECTILE ROUTE (CORRELATED)",
    ]


def test_promoted_native_reaction_labels_and_capture_init_flag_exist():
    source = HUD.read_text(encoding="utf-8")
    assert '0x00000001: "SOFT KNOCKDOWN"' in source
    assert '0x00000002: "HARD KNOCKDOWN"' in source
    assert '"CAPTURE INIT FLAG 0x00200000 (CORRELATED)"' in source
    assert '"CAPTURE/CINEMATIC ROUTE"' in source
    assert '"STANDARD HIT RESULT +0004"' in source
    assert '"GUARD DECODE UNVERIFIED"' in source


def test_scaling_and_source_labels_are_plain_language():
    ns = _helper_namespace()
    assert ns["_attack_scaling_ui"]({
        "attack_property_live_scaling_loss_per_hit": 0.05,
        "attack_property_live_scaling_floor": 0.35,
    }) == "PRORATE 5% / MIN 35%"
    assert ns["_attack_source_ui"]("P1-C1", {
        "attack_property_packet_state": "CONTACT",
        "attack_property_event_sequence": 17,
        "attack_property_packet_action_name": "Tatsu H",
    }) == "C1 CONTACT #17: TATSU H"


def test_compact_panel_does_not_render_debug_words():
    source = HUD.read_text(encoding="utf-8")
    attack_block = source[source.index('    if mode == "attack":'):source.index('    return "DATA", []')]
    for debug_label in ('f"A {prop_a}"', 'f"B {prop_b}', 'f"ST {status20}"', 'f"ACT {actor:08X}"'):
        assert debug_label not in attack_block
    assert '"BASE DMG' in attack_block
    assert '"NEXT PHASE"' in attack_block
    assert '"NO PROPERTY FOR CURRENT ACTION"' in attack_block
    assert 'attack_property_definition_status' in attack_block


def test_attack_property_has_a_distinct_native_only_multiphase_badge():
    source = HUD.read_text(encoding="utf-8")
    assert "def _draw_attack_property_badge(" in source
    assert 'font_sm.render("ATK PROPERTY"' in source
    assert "_draw_attack_property_badge(" in source[source.index("def _draw_compact_team_panel"):]
    assert 'attack.get("attack_property_phases")' in source
    assert "NATIVE SCRIPT" in source
    assert "SCRIPT BLOCK" in source
    assert "_attack_native_operation_text" in source
    assert "B UNKNOWN" in source
    assert "LIVE PROJECTILE PROPERTIES" in source
    assert "_attack_projectile_rows" in source
    assert "HIT_REACTION_MAP" not in source
    assert 'attack.get("attack_property_profile_hits")' not in source
    assert 'attack.get("attack_property_move_details")' not in source

    assert "Attack Property intentionally excludes frame-data/profile context." in source
