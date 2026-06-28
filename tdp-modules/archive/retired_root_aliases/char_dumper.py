"""Legacy module alias for ``tvcgui.features.frame_data.dumper``."""
from __future__ import annotations
import sys as _sys
from tvcgui.features.frame_data import dumper as _canonical
_sys.modules[__name__] = _canonical
