from __future__ import annotations

import ast
import hashlib
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[1]


def path(relative: str) -> Path:
    return APP_DIR / relative


def read(relative: str) -> str:
    return path(relative).read_text(encoding="utf-8")


def tree(relative: str) -> ast.AST:
    return ast.parse(read(relative), filename=relative)


def sha256(relative: str) -> str:
    return hashlib.sha256(path(relative).read_bytes()).hexdigest()


def function_source(relative: str, name: str) -> str:
    source = read(relative)
    module = ast.parse(source, filename=relative)
    lines = source.splitlines()
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            end = getattr(node, "end_lineno", node.lineno)
            return "\n".join(lines[node.lineno - 1:end])
    raise AssertionError(f"function not found: {relative}:{name}")


def class_prefix(test_id: str) -> str:
    parts = test_id.split(".")
    return ".".join(parts[:3]) if len(parts) >= 3 else test_id
