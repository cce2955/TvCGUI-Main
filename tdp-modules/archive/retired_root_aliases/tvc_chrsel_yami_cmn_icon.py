"""Legacy compatibility entry point for tvcgui.tools.character_select.yami_cmn_icon."""
from __future__ import annotations

from importlib import import_module as _import_module
import sys as _sys

_target = _import_module("tvcgui.tools.character_select.yami_cmn_icon")

if __name__ == "__main__":
    raise SystemExit(_target.main())

_sys.modules[__name__] = _target
