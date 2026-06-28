"""Compatibility alias for :mod:`tvcgui.features.overlay.drawing`."""
import sys as _sys
import tvcgui.features.overlay.drawing as _implementation

_sys.modules[__name__] = _implementation
