#!/usr/bin/env python3
"""Conservative pre-release repository scan.

This tool finds likely proprietary game files, private captures, generated
recomp/decomp output, secrets, and absolute local paths. It never proves legal
compliance. Every result requires human review.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

BLOCKED_SUFFIXES = {
    ".iso", ".gcm", ".rvz", ".wia", ".wbfs", ".ciso", ".nfs", ".gcz",
    ".tgc", ".wad", ".dol", ".rel", ".elf", ".rom", ".brres", ".bres",
    ".szs", ".arc", ".fpk", ".pac", ".bns", ".brstm", ".dmp", ".mem",
    ".raw", ".savestate", ".state", ".o", ".obj", ".pdb",
}

BLOCKED_DIR_NAMES = {
    ".git", ".venv", "venv", "env", "orig", "generated", "disassembly",
    "asm_dumps", "private_research", "extracted", "crashdumps",
}

NAME_PATTERNS = [
    re.compile(r"memdump", re.I),
    re.compile(r"recomp", re.I),
    re.compile(r"decomp", re.I),
    re.compile(r"proj_dump\.bin$", re.I),
    re.compile(r"runtime_.*(?:events|profiles|contacts|sources)", re.I),
]

TEXT_PATTERNS = {
    "Windows home path": re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+\\"),
    "Unix home path": re.compile(r"/home/[^/\s]+/"),
    "Possible private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "Possible API token": re.compile(r"(?i)(?:api[_-]?key|token|secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{16,}"),
}

TEXT_SUFFIXES = {
    ".py", ".md", ".txt", ".json", ".jsonl", ".csv", ".toml", ".yaml",
    ".yml", ".ini", ".cfg", ".bat", ".ps1", ".sh", ".xml",
}


def iter_files(root: Path):
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in BLOCKED_DIR_NAMES]
        base = Path(current)
        for filename in files:
            yield base / filename


def scan(root: Path) -> list[str]:
    findings: list[str] = []
    for path in iter_files(root):
        rel = path.relative_to(root)
        lower_parts = {part.lower() for part in rel.parts}
        if lower_parts & BLOCKED_DIR_NAMES:
            findings.append(f"BLOCKED DIRECTORY: {rel}")
        if path.suffix.lower() in BLOCKED_SUFFIXES:
            findings.append(f"BLOCKED FILE TYPE: {rel}")
        if any(pattern.search(path.name) for pattern in NAME_PATTERNS):
            findings.append(f"REVIEW FILE NAME: {rel}")

        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        # Avoid matching this scanner's own detection-pattern literals.
        if rel.as_posix() == "tools/release_audit.py":
            continue
        try:
            if path.stat().st_size > 5_000_000:
                findings.append(f"LARGE TEXT FILE: {rel} ({path.stat().st_size} bytes)")
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            findings.append(f"UNREADABLE: {rel}: {exc}")
            continue
        for label, pattern in TEXT_PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{label.upper()}: {rel}")
    return sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", nargs="?", default=".")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: not a directory: {root}", file=sys.stderr)
        return 2
    findings = scan(root)
    if findings:
        print("Release audit found items requiring review:\n")
        print("\n".join(f"- {item}" for item in findings))
        print("\nThe scan is conservative. Review every item before release.")
        return 1
    print("No obvious blocked files, private paths, or secret patterns found.")
    print("Manual license and Git-history review is still required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
