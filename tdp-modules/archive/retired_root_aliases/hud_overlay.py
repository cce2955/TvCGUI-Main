"""Compatibility alias for :mod:`tvcgui.features.overlay.hud_renderer`."""
import sys as _sys
import tvcgui.features.overlay.hud_renderer as _implementation

_sys.modules[__name__] = _implementation

if __name__ == "__main__":
    _implementation.main()
