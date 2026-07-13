"""Application resource and organized data-path helpers."""
from __future__ import annotations

import os
import sys


def _source_root() -> str:
    """Return the source-tree root regardless of the current working directory."""
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _runtime_root() -> str:
    """Return the persistent runtime root, never PyInstaller's temporary bundle."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return _source_root()


def resource_path(*parts: str) -> str:
    """Return a bundled resource path or a source-tree resource path."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = _source_root()
    return os.path.join(base, *parts)


def data_path(*parts: str) -> str:
    """Return a read-only bundled/source data path under ``data/``."""
    return resource_path("data", *parts)


def user_data_path(*parts: str) -> str:
    """Return a writable persistent data path under ``data/``.

    Source runs write into the project data tree. Frozen builds write beside the
    executable instead of into the transient one-file extraction directory.
    """
    return os.path.join(_runtime_root(), "data", *parts)


def resolve_data_path(*parts: str) -> str:
    """Prefer persistent data when present, otherwise fall back to bundled data."""
    writable = user_data_path(*parts)
    if os.path.exists(writable):
        return writable
    return data_path(*parts)


__all__ = ["resource_path", "data_path", "user_data_path", "resolve_data_path"]
