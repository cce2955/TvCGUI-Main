"""Small, failure-safe persistence for the Frame Data Workbench presentation.

This intentionally stores *only* UI chrome: window geometry, sash position,
column widths, density, and the last chosen column view. It never touches move
profiles or patch data.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tvcgui.core.paths import user_data_path

_PREF_PATH = Path(user_data_path("frame_data", "frame_data_ui_prefs.json"))
_DEFAULTS: dict[str, Any] = {
    "geometry": "1700x820",
    "sash_pos": None,
    "density": "standard",
    "view_mode": "frame",
    "column_widths": {},
}


def load() -> dict[str, Any]:
    data: dict[str, Any] = dict(_DEFAULTS)
    try:
        raw = json.loads(_PREF_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for key, default in _DEFAULTS.items():
                value = raw.get(key, default)
                if isinstance(default, dict):
                    data[key] = value if isinstance(value, dict) else dict(default)
                else:
                    data[key] = value
    except Exception:
        pass
    return data


def save(data: dict[str, Any]) -> None:
    payload = dict(_DEFAULTS)
    if isinstance(data, dict):
        payload.update(data)
    try:
        _PREF_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PREF_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass
