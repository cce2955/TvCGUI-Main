"""Compatibility import for :mod:`tvcgui.runtime.utilities`.

New code should import from ``tvcgui.runtime.utilities``.
"""
from tvcgui.runtime.utilities import *  # noqa: F401,F403

# ``import *`` honors the implementation module's ``__all__``.  The legacy
# module namespace historically exposed every non-dunder module attribute,
# including a few intentionally public constants omitted from ``__all__``.
# Copy those attributes too so old direct module access remains compatible.
import tvcgui.runtime.utilities as _implementation
for _name in dir(_implementation):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_implementation, _name)
del _name, _implementation
