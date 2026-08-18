from pathlib import Path

HUD = Path(__file__).resolve().parents[1] / "tvcgui" / "features" / "overlay" / "hud_renderer.py"


def _source() -> str:
    return HUD.read_text(encoding="utf-8")


def test_stun_clock_identity_colors_are_distinct_and_named():
    src = _source()
    assert "STUN_CLOCK_HIT_COLOR = (122, 183, 198)" in src
    assert "STUN_CLOCK_UNTECH_COLOR = (167, 151, 220)" in src
    assert "STUN_CLOCK_BLOCK_COLOR = (235, 136, 91)" in src


def test_hitstun_and_untech_text_and_fill_share_their_identity_color():
    src = _source()
    assert 'value_color = STUN_CLOCK_UNTECH_COLOR if clock_source == "untech" else STUN_CLOCK_HIT_COLOR' in src
    assert 'fill_color = STUN_CLOCK_UNTECH_COLOR if clock_source == "untech" else STUN_CLOCK_HIT_COLOR' in src
    assert 'head_color = STUN_CLOCK_UNTECH_HEAD if clock_source == "untech" else STUN_CLOCK_HIT_HEAD' in src


def test_blockstun_text_fill_and_marker_are_orange():
    src = _source()
    assert "block_color = STUN_CLOCK_BLOCK_COLOR if block_remaining > 0" in src
    assert "STUN_CLOCK_BLOCK_COLOR," in src
    assert "STUN_CLOCK_BLOCK_HEAD," in src
