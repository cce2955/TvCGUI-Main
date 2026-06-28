"""
Launch helpers for the main GUI and its companion overlay modes.

The overlay modules live inside ``tvcgui.features.overlay``.  Source launches
therefore re-enter the root launcher, which owns the same ``--mode`` dispatch
as the frozen one-file build.
"""
from __future__ import annotations

import os
import sys


def _project_root() -> str:
    """Return the source-tree root from ``tvcgui/core``."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def frozen_exe(script_name: str) -> list[str]:
    """Return an argv list that starts a companion overlay mode.

    Source and frozen execution intentionally share the root ``launcher.py``
    mode dispatcher.  Companion overlay code is package-based now; there are
    no root-level ``master_overlay.py`` or ``hud_overlay.py`` scripts to spawn.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--mode", script_name]

    launcher_path = os.path.join(_project_root(), "launcher.py")
    return [sys.executable, launcher_path, "--mode", script_name]


def base_dir() -> str:
    """Return the persistent root containing project data and launch files."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return _project_root()
