"""Compatibility alias for the relocated training module."""
from importlib import import_module as _import_module
import sys as _sys

_sys.modules[__name__] = _import_module("tvcgui.features.training.win_counter_window")
