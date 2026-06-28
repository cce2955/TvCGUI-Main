"""Application resource-path helpers."""
from __future__ import annotations

import os
import sys

def resource_path(*parts: str) -> str:
    """Return a bundled resource path or a source-tree resource path."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)

__all__ = ["resource_path"]
