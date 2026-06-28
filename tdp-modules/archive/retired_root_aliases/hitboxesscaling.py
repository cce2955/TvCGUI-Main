"""Compatibility alias for :mod:`tvcgui.features.hitboxes.renderer`."""
import sys as _sys
import tvcgui.features.hitboxes.renderer as _implementation

_sys.modules[__name__] = _implementation

if __name__ == "__main__":
    try:
        _implementation.main()
    except Exception as exc:
        _implementation.pause_on_error("FatalCrash", exc)
        raise
