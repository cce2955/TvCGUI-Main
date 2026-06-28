# Phase 1 — Package layout migration

This phase moves only the modules that were already extracted from `main.py`.

## New homes

- `tvcgui/runtime/ko_control.py`
- `tvcgui/runtime/megacrash.py`
- `tvcgui/runtime/utilities.py`
- `tvcgui/ui/components.py`
- `tvcgui/ui/normal_preview.py`

`main.py` now imports these package modules directly. The five old root-level
filenames remain as compatibility shims for existing scripts or external users.
They contain no feature logic.

## Deliberately unchanged

- `main.py` remains the launcher and stable application entry point.
- `app_paths.py` and `subprocess_compat.py` remain at the root because source
  and PyInstaller path semantics depend on their physical location.
- Test files, test runners, baseline, build specs, launchers, runtime data,
  and feature behavior are unchanged.

## Path preservation

`utilities._runtime_output_dir()` now calls the established root-level
`resource_path()` helper when running from source. This keeps memory-dump
output beside the application exactly as before even though the implementation
module is now under `tvcgui/runtime/`.
