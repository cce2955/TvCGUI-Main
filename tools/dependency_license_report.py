#!/usr/bin/env python3
"""Print installed Python package metadata for a manual license review."""

from __future__ import annotations

from importlib import metadata


def clean(value: str | None) -> str:
    return " ".join((value or "UNKNOWN").split())


def main() -> None:
    rows = []
    for dist in metadata.distributions():
        meta = dist.metadata
        name = clean(meta.get("Name"))
        version = clean(dist.version)
        license_value = clean(meta.get("License-Expression") or meta.get("License"))
        homepage = clean(meta.get("Home-page") or meta.get("Project-URL"))
        rows.append((name.lower(), name, version, license_value, homepage))

    print("Name\tVersion\tDeclared license\tHomepage or project URL")
    for _, name, version, license_value, homepage in sorted(rows):
        print(f"{name}\t{version}\t{license_value}\t{homepage}")


if __name__ == "__main__":
    main()
