"""Compatibility alias for :mod:`tvcgui.tools.probes.hitbox_probe`."""
import sys as _sys

if __name__ == "__main__":
    import runpy as _runpy
    _runpy.run_module("tvcgui.tools.probes.hitbox_probe", run_name="__main__")
else:
    from importlib import import_module as _import_module
    _sys.modules[__name__] = _import_module("tvcgui.tools.probes.hitbox_probe")
