"""Thin public entry point for the assist system.

The heavy route logic lives in assist_scanner_backend.py so main.py only imports
this small surface module. This keeps the UI-facing scanner file small while the
validated character-specific logic is preserved in the backend.
"""
from __future__ import annotations

from assist_scanner_backend import (
    open_assist_scanner_window,
    tick_assist_profiles_from_main,
    get_quick_assists_for_slot,
    apply_quick_assist_from_main,
    get_assist_runtime_debug_state,
    restore_assist_runtime_defaults_from_main,
    clear_assist_runtime_state,
)

__all__ = [
    "open_assist_scanner_window",
    "tick_assist_profiles_from_main",
    "get_quick_assists_for_slot",
    "apply_quick_assist_from_main",
    "get_assist_runtime_debug_state",
    "restore_assist_runtime_defaults_from_main",
    "clear_assist_runtime_state",
]
