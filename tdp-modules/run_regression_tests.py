"""Run TvC Continuo's regression contract suite.

This is the gate for substantive project changes. It intentionally uses only
the standard library and no live Dolphin connection.

Default behavior:
  1. discovers/runs every tests/test_*.py contract;
  2. verifies the minimum baseline count and required regression modules;
  3. byte-compiles the critical runtime modules.

Use ``--accept-baseline`` only after intentionally adding/replacing tests and
reviewing the change. It updates the checked-in baseline manifest.
"""
from __future__ import annotations

import argparse
import json
import os
import py_compile
import sys
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
TEST_DIR = APP_DIR / "tests"
BASELINE_PATH = APP_DIR / "test_contract_baseline.json"
CRITICAL_MODULES = (
    "main.py",
    "scan_normals_all.py",
    "scan_worker.py",
    "frame_data_binding.py",
    "frame_data_window.py",
    "move_writer.py",
    "fd_window.py",
)


def discover_suite() -> unittest.TestSuite:
    if str(APP_DIR) not in sys.path:
        sys.path.insert(0, str(APP_DIR))
    os.chdir(APP_DIR)
    return unittest.defaultTestLoader.discover(
        start_dir=str(TEST_DIR),
        pattern="test_*.py",
        top_level_dir=str(APP_DIR),
    )


def flatten_ids(suite: unittest.TestSuite) -> list[str]:
    out: list[str] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            out.extend(flatten_ids(item))
        else:
            out.append(item.id())
    return out


def load_baseline() -> dict:
    try:
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"minimum_tests": 0, "required_prefixes": []}


def save_baseline(test_ids: list[str]) -> None:
    required_prefixes = sorted({
        ".".join(test_id.split(".")[:3])
        for test_id in test_ids
        if test_id.startswith("tests.")
    })
    payload = {
        "suite": "TvC Continuo regression contracts",
        "minimum_tests": len(test_ids),
        "required_prefixes": required_prefixes,
        "critical_modules": list(CRITICAL_MODULES),
    }
    BASELINE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_baseline(test_ids: list[str], baseline: dict) -> list[str]:
    problems: list[str] = []
    minimum = int(baseline.get("minimum_tests") or 0)
    if len(test_ids) < minimum:
        problems.append(f"test count regressed: {len(test_ids)} < baseline {minimum}")

    all_ids = "\n".join(test_ids)
    for prefix in baseline.get("required_prefixes") or ():
        if prefix not in all_ids:
            problems.append(f"required contract group missing: {prefix}")

    for module in baseline.get("critical_modules") or CRITICAL_MODULES:
        if not (APP_DIR / str(module)).is_file():
            problems.append(f"critical module missing: {module}")
    return problems


def compile_critical_modules() -> list[str]:
    problems: list[str] = []
    for relative in CRITICAL_MODULES:
        path = APP_DIR / relative
        try:
            py_compile.compile(str(path), doraise=True)
        except Exception as exc:
            problems.append(f"compile failed for {relative}: {exc}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run TvC regression contracts.")
    parser.add_argument(
        "--accept-baseline",
        action="store_true",
        help="Replace the reviewed test baseline after intentionally changing the suite.",
    )
    args = parser.parse_args(argv)

    suite = discover_suite()
    test_ids = flatten_ids(suite)
    if args.accept_baseline:
        save_baseline(test_ids)
        print(f"[regression] accepted baseline: {len(test_ids)} tests")

    baseline = load_baseline()
    baseline_problems = check_baseline(test_ids, baseline)
    if baseline_problems:
        for problem in baseline_problems:
            print(f"[regression] FAIL: {problem}")
        return 2

    print(f"[regression] running {len(test_ids)} contract tests")
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1

    compile_problems = compile_critical_modules()
    if compile_problems:
        for problem in compile_problems:
            print(f"[regression] FAIL: {problem}")
        return 3

    print("[regression] PASS: contracts + critical compile checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
