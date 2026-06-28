"""Compatibility alias for :mod:`tvcgui.features.frame_data.window`.

New code should import from ``tvcgui.features.frame_data.window``.
This is a module alias, rather than a re-export, so legacy callers and
contract tests that monkeypatch module-level hooks affect the canonical
implementation too.
"""
import sys as _sys
from tvcgui.features.frame_data import window as _implementation

_sys.modules[__name__] = _implementation
