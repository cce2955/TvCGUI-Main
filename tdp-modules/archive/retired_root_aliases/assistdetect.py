"""Legacy compatibility entry point for :mod:`tvcgui.tools.diagnostics.assist_detector`."""
from __future__ import annotations

from importlib import import_module as _import_module
import sys as _sys

_target = _import_module("tvcgui.tools.diagnostics.assist_detector")

if __name__ == "__main__":
    _target.main()
else:
    _sys.modules[__name__] = _target
