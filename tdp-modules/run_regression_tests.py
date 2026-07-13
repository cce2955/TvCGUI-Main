"""Run the TvC Continuo V19 regression gate.

The gate uses no live Dolphin connection. It discovers every contract test,
locks the reviewed test inventory, checks protected V19 files, compiles all
active Python sources into a temporary directory, and rejects cache artifacts.

Use --accept-baseline only after an intentional reviewed change.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import py_compile
import shutil
import sys
import tempfile
import traceback
import types
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

APP_DIR = Path(__file__).resolve().parent
TEST_DIR = APP_DIR / "tests"
BASELINE_PATH = APP_DIR / "test_contract_baseline.json"

CRITICAL_MODULES = (
    "main.py",
    "launcher.py",
    "tvcgui/tools/scanners/normal_scanner.py",
    "tvcgui/tools/scanners/normal_scan_worker.py",
    "tvcgui/features/frame_data/binding.py",
    "tvcgui/features/frame_data/window.py",
    "tvcgui/features/combat/move_writer.py",
    "tvcgui/features/frame_data/workbench.py",
    "tvcgui/ui/components.py",
    "tvcgui/ui/normal_preview.py",
    "tvcgui/ui/advantage_window.py",
    "tvcgui/runtime/ko_control.py",
    "tvcgui/features/overlay/hud_renderer.py",
    "tvcgui/features/overlay/master_renderer.py",
    "tvcgui/features/training/mission_manager.py",
)

REQUIRED_FILES = (
    *CRITICAL_MODULES,
    "run_regression_tests.bat",
    "run_regression_tests.py",
    "data/frame_data/frame_data_preview_profiles.json",
    "data/frame_data/observed_block_advantage_profiles.json",
    "tdp-modules/tvcgui/ui/advantage_window.py",
    "tdp-modules/tvcgui/runtime/ko_control.py",
)

PROTECTED_FILES = (
    "main.py",
    "run_regression_tests.py",
    "run_regression_tests.bat",
    "tvcgui/ui/components.py",
    "tvcgui/ui/normal_preview.py",
    "tvcgui/ui/advantage_window.py",
    "tdp-modules/tvcgui/ui/advantage_window.py",
    "tvcgui/runtime/ko_control.py",
    "tdp-modules/tvcgui/runtime/ko_control.py",
    "tvcgui/features/overlay/hud_renderer.py",
    "tvcgui/features/overlay/master_renderer.py",
    "tvcgui/features/training/mission_manager.py",
    "data/frame_data/frame_data_preview_profiles.json",
    "data/frame_data/observed_block_advantage_profiles.json",
    "tests/v19_contract_helpers.py",
) + tuple(
    path.relative_to(APP_DIR).as_posix()
    for path in sorted(TEST_DIR.glob("test_*.py"))
)

ACTIVE_PYTHON_ROOTS = (
    "main.py",
    "launcher.py",
    "run_regression_tests.py",
    "run_unit_tests.py",
    "run_dolphin_smoke_tests.py",
    "tests",
    "tvcgui",
    "tdp-modules/tvcgui",
)


def install_offline_dolphin_stub() -> None:
    """Allow contract discovery without a live Dolphin package or process."""
    if "dolphin_memory_engine" in sys.modules:
        return
    try:
        __import__("dolphin_memory_engine")
        return
    except ModuleNotFoundError:
        pass
    sys.modules["dolphin_memory_engine"] = types.SimpleNamespace(
        is_hooked=lambda: False,
        hook=lambda: None,
        read_bytes=lambda _addr, size: b"\0" * int(size),
        write_bytes=lambda _addr, _data: None,
    )


def dependency_problems() -> list[str]:
    problems: list[str] = []
    try:
        __import__("pygame")
    except Exception as exc:
        problems.append(
            f"pygame is unavailable in {sys.executable}: {exc!r}. "
            "Run this gate with the TvCGUI project virtual environment."
        )
    return problems


def discover_suite() -> unittest.TestSuite:
    if str(APP_DIR) not in sys.path:
        sys.path.insert(0, str(APP_DIR))
    os.chdir(APP_DIR)
    return unittest.defaultTestLoader.discover(
        start_dir=str(TEST_DIR),
        pattern="test_*.py",
        top_level_dir=str(APP_DIR),
    )


def flatten_tests(suite: unittest.TestSuite) -> list[unittest.TestCase]:
    out: list[unittest.TestCase] = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            out.extend(flatten_tests(item))
        else:
            out.append(item)
    return out


def flatten_ids(suite: unittest.TestSuite) -> list[str]:
    return [item.id() for item in flatten_tests(suite)]


def discovery_failures(suite: unittest.TestSuite) -> list[tuple[str, BaseException]]:
    failures: list[tuple[str, BaseException]] = []
    for item in flatten_tests(suite):
        exc = getattr(item, "_exception", None)
        if item.__class__.__name__ == "_FailedTest" and isinstance(exc, BaseException):
            failures.append((item.id(), exc))
    return failures


def print_discovery_failures(failures: list[tuple[str, BaseException]]) -> None:
    print(f"[regression] FAIL: {len(failures)} test module(s) could not be imported")
    print(f"[regression] Interpreter: {sys.executable}")
    for test_id, exc in failures:
        print(f"[regression] DISCOVERY ERROR: {test_id}")
        traceback.print_exception(type(exc), exc, exc.__traceback__, file=sys.stdout)


def class_prefix(test_id: str) -> str:
    parts = test_id.split(".")
    return ".".join(parts[:3]) if len(parts) >= 3 else test_id


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_baseline() -> dict:
    if not BASELINE_PATH.is_file():
        return {}
    try:
        payload = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def current_protected_hashes() -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in PROTECTED_FILES:
        path = APP_DIR / relative
        if path.is_file():
            hashes[relative] = sha256_file(path)
    return hashes


def save_baseline(test_ids: list[str]) -> None:
    payload = {
        "suite": "TvC Continuo V19 regression contracts",
        "minimum_tests": len(test_ids),
        "required_test_ids": sorted(test_ids),
        "required_prefixes": sorted({class_prefix(test_id) for test_id in test_ids}),
        "critical_modules": list(CRITICAL_MODULES),
        "required_files": list(REQUIRED_FILES),
        "protected_hashes": current_protected_hashes(),
    }
    BASELINE_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_baseline(test_ids: list[str], baseline: dict) -> list[str]:
    problems: list[str] = []
    if not baseline:
        return ["baseline manifest missing or invalid"]

    minimum = int(baseline.get("minimum_tests") or 0)
    if len(test_ids) < minimum:
        problems.append(f"test count regressed: {len(test_ids)} < baseline {minimum}")

    current_ids = set(test_ids)
    for test_id in baseline.get("required_test_ids") or ():
        if test_id not in current_ids:
            problems.append(f"required test missing: {test_id}")

    current_prefixes = {class_prefix(test_id) for test_id in test_ids}
    for prefix in baseline.get("required_prefixes") or ():
        if prefix not in current_prefixes:
            problems.append(f"required contract group missing: {prefix}")

    for relative in baseline.get("critical_modules") or CRITICAL_MODULES:
        if not (APP_DIR / str(relative)).is_file():
            problems.append(f"critical module missing: {relative}")

    for relative in baseline.get("required_files") or REQUIRED_FILES:
        if not (APP_DIR / str(relative)).is_file():
            problems.append(f"required file missing: {relative}")

    protected_hashes = baseline.get("protected_hashes") or {}
    for relative, expected in protected_hashes.items():
        path = APP_DIR / str(relative)
        if not path.is_file():
            problems.append(f"protected file missing: {relative}")
            continue
        actual = sha256_file(path)
        if actual != expected:
            problems.append(f"protected file changed without baseline review: {relative}")

    return problems


def iter_active_python() -> list[Path]:
    files: set[Path] = set()
    for relative in ACTIVE_PYTHON_ROOTS:
        root = APP_DIR / relative
        if root.is_file() and root.suffix.lower() == ".py":
            files.add(root)
        elif root.is_dir():
            files.update(path for path in root.rglob("*.py") if path.is_file())
    return sorted(files)


def compile_active_python() -> list[str]:
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="tvc_v19_compile_") as temp_name:
        temp = Path(temp_name)
        for source in iter_active_python():
            relative = source.relative_to(APP_DIR)
            target_name = "__".join(relative.parts) + "c"
            target = temp / target_name
            try:
                py_compile.compile(str(source), cfile=str(target), doraise=True)
            except Exception as exc:
                problems.append(f"compile failed for {relative.as_posix()}: {exc}")
    return problems


def find_cache_artifacts() -> list[str]:
    artifacts: list[str] = []
    for path in APP_DIR.rglob("*"):
        if path.is_dir() and path.name == "__pycache__":
            artifacts.append(path.relative_to(APP_DIR).as_posix() + "/")
        elif path.is_file() and path.suffix.lower() in {".pyc", ".pyo"}:
            artifacts.append(path.relative_to(APP_DIR).as_posix())
    return sorted(set(artifacts))


def remove_cache_artifacts() -> None:
    for path in sorted(APP_DIR.rglob("__pycache__"), reverse=True):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    for pattern in ("*.pyc", "*.pyo"):
        for path in APP_DIR.rglob(pattern):
            try:
                path.unlink()
            except OSError:
                pass


def print_problems(problems: list[str]) -> None:
    for problem in problems:
        print(f"[regression] FAIL: {problem}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run TvC V19 regression contracts.")
    parser.add_argument(
        "--accept-baseline",
        action="store_true",
        help="Replace the reviewed baseline after intentionally changing tests or protected files.",
    )
    args = parser.parse_args(argv)

    remove_cache_artifacts()
    print(f"[regression] Interpreter: {sys.executable}")

    preflight_problems = dependency_problems()
    if preflight_problems:
        print_problems(preflight_problems)
        return 5

    install_offline_dolphin_stub()
    suite = discover_suite()
    import_failures = discovery_failures(suite)
    if import_failures:
        print_discovery_failures(import_failures)
        return 6

    test_ids = flatten_ids(suite)

    if args.accept_baseline:
        save_baseline(test_ids)
        print(f"[regression] accepted V19 baseline: {len(test_ids)} tests")

    baseline_problems = check_baseline(test_ids, load_baseline())
    if baseline_problems:
        print_problems(baseline_problems)
        return 2

    print(f"[regression] running {len(test_ids)} V19 contract tests")
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    if not result.wasSuccessful():
        return 1

    compile_problems = compile_active_python()
    if compile_problems:
        print_problems(compile_problems)
        return 3

    cache_artifacts = find_cache_artifacts()
    if cache_artifacts:
        print_problems([f"cache artifact present: {item}" for item in cache_artifacts])
        return 4

    print(f"[regression] PASS: {len(test_ids)} contracts + protected hashes + full active compile + clean package")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
