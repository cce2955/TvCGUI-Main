"""Compatibility alias for :mod:`tvcgui.features.character_select.runtime`.

New code should import from ``tvcgui.features.character_select.runtime``.
The alias preserves module identity so legacy tools/tests that monkeypatch
runtime hooks continue to affect the canonical implementation.
"""
import sys as _sys
from tvcgui.features.character_select import runtime as _implementation

_sys.modules[__name__] = _implementation
