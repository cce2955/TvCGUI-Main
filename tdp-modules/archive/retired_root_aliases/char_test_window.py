"""Compatibility alias for :mod:`tvcgui.features.character_select.window`.

New code should import from ``tvcgui.features.character_select.window``.
"""
import sys as _sys
from tvcgui.features.character_select import window as _implementation

_sys.modules[__name__] = _implementation
