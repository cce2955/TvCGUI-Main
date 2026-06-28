"""Legacy module alias for :mod:`tvcgui.features.frame_data.super_integration`.

The root import remains temporarily so existing launch scripts, saved tools, and
contract tests continue to resolve the canonical implementation.
"""
from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("tvcgui.features.frame_data.super_integration")
