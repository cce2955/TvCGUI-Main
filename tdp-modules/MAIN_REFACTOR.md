# Main.py modularization — first pass

`main.py` remains the executable entry point, but the large independent
subsystems now live in focused modules. This pass deliberately preserves the
original function bodies and call names; it moves code rather than redesigning
behavior.

## Extracted modules

- `app_paths.py` — bundled/source resource-path resolution.
- `ko_control_runtime.py` — KO rescue, rewind, input injection, and DOL patch
  controls.
- `main_runtime_utils.py` — memory-dump, clipboard, performance, and automatic
  frame-data scan helpers.
- `megacrash_trainer_runtime.py` — Megacrash configuration, combo tracking,
  and pulse runtime.
- `ui_components.py` — shared visual palette, drawing primitives, command dock,
  status rail, and workspace tabs.
- `normal_preview_ui.py` — normal-move preview, scan display, and quick-assist
  footer rendering.

## Compatibility contract

The entry point imports the exact names it needs from each focused module, so
existing app-loop calls keep their original names and behavior. The three
state-provider helpers that depend on optional GUI/runtime services stay in
`main.py`.

## Validation performed

- `main.py`: 8,649 lines → 3,582 lines.
- All 153 original top-level function bodies matched AST-for-AST after the
  move; `resource_path` is intentionally centralized in `app_paths.py`.
- All original top-level bindings remain present across the entry point and new
  modules.
- Byte compilation and an entry-point import smoke check with runtime stubs
  passed.
- Test files, test runners, and the regression baseline were not modified.

## Existing archive test-layout note

`run_regression_tests.py` still points to `tests/`, but this archive contains
only root-level `test_*.py` files and no `tests/` directory. The runner fails
before discovery for that pre-existing path mismatch. No test logic or runner
logic was changed in this refactor.
