"""Legacy compatibility entry point for :mod:`tvcgui.tools.diagnostics.projectile_pool_monitor`."""
from __future__ import annotations

from importlib import import_module as _import_module
import sys as _sys

_target = _import_module("tvcgui.tools.diagnostics.projectile_pool_monitor")

if __name__ == "__main__":
    try:
        _target.main()
    except KeyboardInterrupt:
        _target.log("[main] exiting")
else:
    _sys.modules[__name__] = _target
