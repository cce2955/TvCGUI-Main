from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def test_throw_inspector_is_read_only() -> None:
    text = _read("tvcgui/features/training/throw_inspector_readonly.py")
    assert "wd8" not in text
    assert "wd32" not in text
    assert "wdf32" not in text
    assert "wbytes" not in text
    assert "from tvcgui.platform.dolphin import addr_in_ram, rd32, rdf32, rbytes" in text


def test_throw_packet_layout_contract() -> None:
    text = _read("tvcgui/features/training/throw_inspector_readonly.py")
    assert "ACTION_PACKET_TABLE_OFF = 0x198C" in text
    assert "ACTION_PACKET_ENTRY_SIZE = 0x1C" in text
    assert "ACTION_PACKET_MAX_ENTRIES = 90" in text
    assert "ENTRY_THROW_ACTION_OFF = 0x04" in text
    assert "ENTRY_ACTIVE_FRAMES_OFF = 0x08" in text
    assert "ENTRY_RANGE_RAW_OFF = 0x14" in text
    assert "range_raw * 0.01" in text
    assert "rbytes(table, wanted)" in text


def test_throw_flag_contract() -> None:
    text = _read("tvcgui/features/training/throw_inspector_readonly.py")
    for needle in (
        "THROW_GROUND = 0x00000080",
        "THROW_AIR = 0x00000100",
        "TARGET_STANDING = 0x00000200",
        "TARGET_CROUCHING = 0x00000400",
        "TARGET_AIRBORNE = 0x00000800",
        "FAILED_CAPTURE_ACTION = 0x00004000",
        "CONTACT_CAPTURE = 0x00008000",
    ):
        assert needle in text


def test_throw_window_has_no_write_language_or_imports() -> None:
    text = _read("tvcgui/features/training/throw_inspector_window.py")
    assert "READ ONLY / NO GAME WRITES" in text
    assert "wd32" not in text
    assert "wbytes" not in text
