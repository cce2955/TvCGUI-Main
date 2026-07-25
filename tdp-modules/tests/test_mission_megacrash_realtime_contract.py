from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _module():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if "dolphin_memory_engine" not in sys.modules:
        dme = types.ModuleType("dolphin_memory_engine")
        dme.is_hooked = lambda: False
        dme.hook = lambda: None
        dme.un_hook = lambda: None
        dme.read_byte = lambda *_args, **_kwargs: 0
        dme.read_bytes = lambda *_args, **_kwargs: b""
        dme.write_byte = lambda *_args, **_kwargs: None
        dme.write_bytes = lambda *_args, **_kwargs: None
        sys.modules["dolphin_memory_engine"] = dme
    return importlib.import_module("tvcgui.runtime.megacrash")


def test_mission_megacrash_uses_sample_clock_not_gui_frame(monkeypatch) -> None:
    module = _module()
    writes = []
    monkeypatch.setattr(module.input_monitor, "action_name", lambda action, char: "5C" if int(action) == 0x102 else "")
    monkeypatch.setattr(
        module,
        "_mission_megacrash_write_action",
        lambda snap, key: writes.append((dict(snap), str(key))) or [int(snap.get("base") or 0) + 0x1E8],
    )

    controller = module.MissionMegacrashRealtime()
    payload = {
        "active": True,
        "slot": "P1-C2",
        "character": "Casshan",
        "active_mission_id": "casshan_008",
        "active_mission_steps": [
            {"label": "5B"},
            {"label": "5C"},
            {"label": "Knee A"},
        ],
        "active_mission_setup_megacrash_trainer": {
            "enabled": True,
            "target_label": "5C",
            "delay_frames": 5,
        },
        "completed_step_count": 1,
    }
    snaps = {
        "P1-C2": {"base": 0x92000000, "teamtag": "P1"},
        "P2-C1": {"base": 0x93000000, "teamtag": "P2"},
    }
    controller.sync(payload, snaps)

    start = 1_000_000_000
    controller.on_sample("P2-C1", {
        "slot": "P2-C1", "base": 0x93000000, "current_hp": 46000,
        "action_id": 0, "char_id": 13, "sample_ns": start,
    })
    controller.on_sample("P1-C2", {
        "slot": "P1-C2", "base": 0x92000000, "current_hp": 45000,
        "action_id": 0x102, "action_frame": 12, "char_id": 2,
        "sample_ns": start + 5_000_000,
    })
    hit_ns = start + 10_000_000
    controller.on_sample("P2-C1", {
        "slot": "P2-C1", "base": 0x93000000, "current_hp": 42448,
        "action_id": 50, "action_frame": 1, "char_id": 13,
        "sample_ns": hit_ns,
    })

    due_ns = hit_ns + int((5 / 60.0) * 1_000_000_000)
    controller.on_sample("P1-C2", {
        "slot": "P1-C2", "base": 0x92000000, "current_hp": 45000,
        "action_id": 0x136, "action_frame": 3, "char_id": 2,
        "sample_ns": due_ns - 1,
    })
    assert writes == []

    controller.on_sample("P1-C2", {
        "slot": "P1-C2", "base": 0x92000000, "current_hp": 45000,
        "action_id": 0x136, "action_frame": 4, "char_id": 2,
        "sample_ns": due_ns + 1,
    })
    assert writes
    assert writes[0][0]["base"] == 0x93000000
    assert "realtime" in writes[0][1]


def test_action_frame_samples_do_not_bloat_mission_edge_queue() -> None:
    source = (ROOT / "tvcgui" / "features" / "overlay" / "manager.py").read_text(encoding="utf-8")
    assert "if meaningful_change:" in source
    assert "Action-frame-only" in source
    assert "listener(str(slot_label), dict(queued_sample))" in source
