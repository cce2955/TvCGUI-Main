# TvC Continuo

TvC Continuo is a Windows training, overlay, and research suite for **Tatsunoko vs. Capcom: Ultimate All-Stars** running in Dolphin Emulator. It reads the game's live emulated memory to provide match information, training controls, visual overlays, frame-data tooling, and character-select experiments.

> **Release users only need `TvCGUI.exe`.** Python, a virtual environment, `run.bat`, source files, test scripts, and build tools are not required to use a release.

---

## Install and Quick Start

### What is needed

- Windows
- Dolphin Emulator, running as `Dolphin.exe`
- A compatible copy of *Tatsunoko vs. Capcom: Ultimate All-Stars* configured in Dolphin
- `TvCGUI.exe` from the [GitHub Releases page](https://github.com/cce2955/TvCGUI-Main/releases/)

### Installation

There is no installer and no separate setup process.

1. Open the [GitHub Releases page](https://github.com/cce2955/TvCGUI-Main/releases/) and download **`TvCGUI.exe`** from the latest release.
2. Place it in a normal writable folder, such as a folder under Documents or a dedicated tools folder.
3. Do not launch it from inside a ZIP archive, a browser download preview, or a protected Windows folder.

The EXE creates and uses a local `data` folder beside itself for saved settings, range profiles, overlay state, and trainer configuration. Keeping the EXE in one writable location preserves those settings between sessions.

### Launch order

1. Start Dolphin.
2. Launch *Tatsunoko vs. Capcom* and reach a menu, training mode, or match.
3. Launch `TvCGUI.exe`.
4. Wait for the application status to report that Dolphin is hooked.
5. Enter or continue a match to populate the fighter-specific tools.

The EXE can be started as soon as you see the intro scene, but as long as dolphin is showing any visual, the GUI should be live.

### Not part of normal installation

The following are for source development only:

- `run.bat`
- `.venv`
- `main.py`
- test runners
- PyInstaller `.spec` files
- scanner scripts, probes, and memory-dump utilities

A normal release session is simply: **start Dolphin, start the game, start `TvCGUI.exe`.**

### Quick troubleshooting

| Symptom | Check |
|---|---|
| The EXE does not hook Dolphin | Confirm that the emulator process is named `Dolphin.exe`, that Dolphin and TvCGUI are running at the same Windows permission level, and that only one Dolphin instance is open. TvCGUI hooks the first active `Dolphin.exe` instance it finds; later instances are not hooked. |
| The EXE hooks but fighter panels are empty | Enter a match so the game allocates live fighter data. |
| Settings do not persist | Move the EXE to a writable folder and keep it in that location between sessions. |
| An overlay does not appear | Enable it from the main application after Dolphin is hooked. Overlay windows are separate managed processes. |
| A memory-writing tool does not behave as expected | Confirm the supported game build and current scene before changing any offsets, patches, or runtime guards. |

---

## High-Level Overview

TvC Continuo adds a live training and research layer around a running Dolphin session. It does not replace Dolphin or modify the game files on disk. The application reads the game's active emulated memory and presents useful match information, visual tools, training controls, and focused workbenches while the game is running.

### Live HUD and match information

The main window provides a live view of the active match. It tracks fighter health, meter, Baroque state, current move labels, combat events, and frame-advantage results. Fighter panels update across tags, assists, giant characters, and normal match transitions so the displayed data follows the active fighter structures rather than a fixed character selection.

### Hitbox, hurtbox, and range tools

The hitbox overlay draws active collision data over the Dolphin window. It supports per-slot visibility filters, hitbox and hurtbox display, invulnerability information, contact auditing, and saved range profiles. The range-ruler tools provide visual horizontal or vertical reference lines for normal attacks and can retain learned adjustments in the local `data` folder.

### Frame data, editing, and runtime patches

The frame-data workbench scans and organizes normals, specials, supers, and projectiles for the selected fighter. It presents decoded values such as startup, active frames, recovery, hitstun, blockstun, damage, knockback, meter behavior, and available runtime signals.

The workbench also includes controlled editing and patch support. Approved edits are applied to the running session through narrow write paths, can be restored, and may be stored as reviewed patch records. Unknown-data tools keep unclassified values visible for research without presenting them as confirmed mechanics.

### Missions and practical training tools

Mission Mode provides character-specific combo and execution trials that validate live move sequences, track progress, reset failed routes, and save completion state. The surrounding training tools include the MegaCrash trainer, runtime stun profiling, debug flags, timer controls, win-counter editing, and KO control behavior for supported training scenarios.

### Character-select experiments

Character-select tools provide guarded controls for extra slots, hover data, icons, thumbnails, material routes, and team-size behavior. These tools are isolated behind scene checks and dedicated patch routes because character-select memory is especially sensitive to incorrect writes.

### Research and diagnostics

The suite also includes developer-facing scanners and diagnostics for fighter resolution, memory searches, animation and MOT lookup, projectile discovery, assist analysis, collision probes, and character-select research. These tools support continued mapping work without making scanner scripts or memory-dump utilities part of normal release use.

### Runtime behavior and write safety

Most TvC Continuo features are read-oriented. Features that modify live memory use guarded runtime modules, editor actions, or narrow patch systems because game memory is scene- and build-sensitive. A feature that writes memory should only be used with the supported game build and in the scene it was designed to handle.

### How the application works

1. `TvCGUI.exe` starts through `launcher.py`.
2. `main.py` connects to the running Dolphin process.
3. The Dolphin layer translates Wii guest addresses into safe process-memory reads and writes.
4. Fighter slots are resolved into current fighter structures and normalized snapshots.
5. Feature modules consume those snapshots for HUD panels, scanners, overlays, training tools, and editor windows.
6. Settings and learned profiles are stored under `data/` beside the EXE in a frozen release.

---

## Project Map

The source tree is rooted at `tdp-modules/`.

```text
tdp-modules/
├── launcher.py                 Frozen-EXE entry point and overlay mode router
├── main.py                     Main Pygame application and runtime coordinator
├── tvcgui/
│   ├── core/                   Shared constants, paths, configuration, Tk helpers
│   ├── platform/               Dolphin memory I/O and patch safety boundary
│   ├── runtime/                Always-on runtime controls such as KO and MegaCrash
│   ├── ui/                     Main application UI, portraits, previews, debug panels
│   ├── features/               User-facing combat, training, frame-data, and overlay tools
│   └── tools/                  Scanners, diagnostics, read-only probes, research utilities
├── data/                       Seed data and persisted settings/profile files
├── missions/                   Character-specific mission definitions
├── assets/portraits/           Portrait textures used by the primary UI
├── tests/                      Standard unit and contract tests
├── archive/                    Retired aliases, backups, and legacy material
├── dist/                       Generated release output
└── build/                      PyInstaller intermediate output
```

---

## Technical Reference: Main Files and Folders

### Root files

| File | Role |
|---|---|
| `launcher.py` | Entry point used by the frozen EXE. Starts the normal application by default and routes `--mode master_overlay` or `--mode hud_overlay` to the appropriate overlay process. |
| `main.py` | Main Pygame application loop. Hooks Dolphin, resolves fighter snapshots, coordinates live feature updates, handles the main window, and opens feature-specific tools. |
| `TvCGUI_onefile.spec` | PyInstaller definition for the public one-file release. Packages `launcher.py`, required assets, hidden imports, and bundled seed data. |
| `TvCGUI.spec` | Alternate PyInstaller build definition retained for development/build workflows. |
| `run_dolphin_smoke_tests.py` | Live Dolphin validation for the hook boundary, memory reads, fighter slot pointers, character-table resolution, and move lookup. |
| `run_unit_tests.py` / `run_unit_tests.bat` | Runs the standard `unittest` suite under `tests/`. |
| `run_regression_tests.py` / `run_regression_tests.bat` | Runs contract tests, verifies the approved test baseline, and byte-compiles critical modules. |
| `test_*.py` at the root | Focused regression scripts, primarily for character-select routes, icon behavior, frame-data unknowns, and runtime stun profiling. |
| `test_contract_baseline.json` | Expected contract-test inventory used by the regression runner. |

### `tvcgui/core/`: shared application definitions

| File | Role |
|---|---|
| `constants.py` | Central shared memory map: fighter pointer slots, common fighter-structure offsets, memory ranges, bad-pointer markers, and character IDs. This is the primary reference for shared offsets. |
| `config.py` | Presentation settings, refresh values, colors, debug addresses, Baroque monitoring values, data-file references, and shared UI configuration. |
| `paths.py` | Separates bundled resources from writable user data. Frozen builds read seed data from the packaged app and write mutable state beside `TvCGUI.exe`. |
| `layout.py` | Computes main-window layout and applies special slot handling for large characters. |
| `events.py` | Shared combat-event logging used by the HUD and combat tracking. |
| `tk_host.py` | Safe Tk-window hosting and coordination for tools opened from the Pygame application. |
| `subprocess_compat.py` | Frozen/source-compatible process launching for overlay and helper processes. |

### `tvcgui/platform/`: Dolphin memory boundary

| File | Role |
|---|---|
| `dolphin.py` | Primary Dolphin interface. Hooks `Dolphin.exe`, reads and writes MEM1/MEM2 guest addresses, performs big-endian conversion, validates address ranges, primes the MEM2 latch, and applies the character-select write quarantine. |
| `patch_manager.py` | Tracks reviewed memory patches, applies and restores them, and keeps patch behavior separate from general runtime reads. |

No feature module should create its own general-purpose Dolphin memory interface. Shared reads and writes belong behind this package.

### `tvcgui/runtime/`: always-on runtime controls

| File | Role |
|---|---|
| `ko_control.py` | KO-control state machine, baseline capture, input recovery, auto-mode handling, slot/global holds, and controlled restore behavior. |
| `megacrash.py` | Runtime side of the MegaCrash trainer: hit recognition, sequence/cooldown tracking, armed state, and crash trigger timing. |
| `utilities.py` | Shared runtime helpers used by main-loop feature coordination. |

### `tvcgui/ui/`: primary application interface

| File | Role |
|---|---|
| `main_window.py` | Main-window UI composition and interaction helpers used by the primary Pygame surface. |
| `components.py` | Reusable Pygame controls, labels, panels, and input elements. |
| `portraits.py` | Portrait loading and fighter-to-portrait selection. |
| `normal_preview.py` | Normal-move preview panel and readable label/display handling. |
| `debug_panel.py` | Debug flag reads and debug overlay rendering. |
| `overseer.py` | Tk tool-state/status window for inspecting active subsystems. |

---

## Memory Map and Offset Reference

The addresses below are the shared stable values currently defined in `tvcgui/core/constants.py` and `tvcgui/core/config.py`. They describe the supported runtime layout and are not guaranteed to apply to another game revision or a changed emulator layout.

### Address ranges

| Range | Purpose |
|---|---|
| `0x80000000`–`0x81800000` | Wii MEM1 guest memory range. |
| `0x90000000`–`0x94000000` | Wii MEM2 guest memory range. |

### Fighter pointer slots

Each slot points to a fighter-related structure. `fighter_resolver.py` validates the pointer and follows approved indirections to locate the active live fighter base.

| Slot | Guest address |
|---|---:|
| P1 character 1 | `0x803C9FCC` |
| P1 character 2 | `0x803C9FDC` |
| P2 character 1 | `0x803C9FD4` |
| P2 character 2 | `0x803C9FE4` |

### Common fighter-structure offsets

All offsets in this table are relative to the resolved fighter base unless noted otherwise.

| Field | Offset | Use |
|---|---:|---|
| Character ID | `+0x14` | Character/name/portrait lookup. |
| Maximum health | `+0x24` | Max HP read. |
| Current health | `+0x28` | Current HP read. |
| Auxiliary health | `+0x2C` | Additional/mirrored HP field used by some states. |
| Last-hit value | `+0x40` | Recent damage-event input; behavior varies by character/state. |
| Meter, primary bank | `+0x4C` | Current meter read. |
| Meter, secondary bank | `+0x93CC` | Mirrored/deeper meter-bank read (`0x9380 + 0x4C`). |
| Control word | `+0x70` | Action/control gating bitfield. |
| Status flag | `+0x62` | Common hitstop/guard-related state signal. |
| Status flag | `+0x63` | Common runtime state signal. |
| Status flag | `+0x64` | Common runtime state signal. |
| Status flag | `+0x72` | Common airborne/ground-related state signal. |
| World X | `+0xF0` | Fighter X position. |
| World Y candidates | `+0xE8`, `+0xEC`, `+0xF4`, `+0xF8`, `+0xFC` | Candidate Y fields; the resolver selects a valid value for the current fighter/state. |
| Primary action ID | `+0x1E8` | Main action/subaction value. |
| Secondary action ID | `+0x1EC` | Secondary action/subaction value. |
| Resolved stun | `+0x1210` | Victim-side runtime stun value used by the stun profiler. |
| Stun remaining | `+0x1228` | Victim-side live stun countdown. |
| Impact freeze | `+0x2120` | Contact-related impact-freeze countdown. |

### Pointer safety values

| Value | Purpose |
|---|---|
| `0x00000000` | Invalid/null fighter pointer marker. |
| `0x80520000` | Invalid/transient fighter pointer marker. |
| `+0x10`, `+0x18`, `+0x1C`, `+0x20` | Approved internal pointer probes used by fighter recovery logic. |
| 1.0 second | Last-known-good fighter-base cache lifetime. |

### Global configuration addresses

These values are feature-specific global addresses held in `tvcgui/core/config.py`.

| Name | Guest address | Used by |
|---|---:|---|
| Orientation flag | `0x908AEE38` | Scene/display-state logic. |
| Super-background flag | `0x908AEE21` | Super/background state logic. |
| Pause overlay flag | `0x8056110B` | Debug/pause overlay state. |
| Baroque readiness | `0x9246CBAB` | Primary Baroque state. |
| Baroque neighbor byte | `0x9246CB9C` | Secondary Baroque-adjacent state. |
| Baroque flag 0 | `0x9246CC48` | Baroque monitor state. |
| Baroque flag 1 | `0x9246CC50` | Baroque monitor state. |
| Input monitor A0 | `0x9246CC40` | Input-monitor group. |
| Input monitor A1 | `0x9246CC50` | Input-monitor group. |
| Input monitor A2 | `0x9246CC60` | Input-monitor group. |

### Feature-local offset ownership

The shared table above is intentionally limited to values reused across the application. Larger feature-specific maps stay with the code that validates and uses them.

| Area | Primary location | Contents |
|---|---|---|
| Frame-data packet fields | `features/frame_data/patterns.py` | Command-stream signatures, parser primitives, and decoded packet-field rules. |
| Hitbox structures | `features/hitboxes/renderer.py`, `tools/probes/hitbox_probe.py` | Collision-table reads, camera conversion, filters, and focused structure probes. |
| Animation/MOT resource tables | `features/animation/runtime.py` | Resource windows, MOT table lookup, and tightly scoped animation pointer changes. |
| MegaCrash state | `runtime/megacrash.py`, `training/megacrash_window.py` | Trainer state, timing, sequence rules, and persisted configuration. |
| KO-control values | `runtime/ko_control.py` | KO state checks, recovery baselines, and guarded input/control restoration. |
| Character-select patches | `features/character_select/runtime.py`, `tools/character_select/` | Scene-guarded roster, hover, icon, thumbnail, and material-route writes. |
| Projectile and assist scans | `features/combat/projectile_*.py`, `features/assists/backend.py` | Projectile pools, templates, assist scans, and character-specific scan anchors. |

---

## Technical Reference: Feature Packages

### `tvcgui/tools/scanners/`: live fighter and memory readers

| File | Role |
|---|---|
| `fighter_resolver.py` | Resolves the four pointer slots into validated fighter bases, handles indirection and invalid markers, and holds short-lived last-known-good pointers through transient swaps. |
| `fighter_state.py` | Builds normalized per-frame fighter snapshots: character ID, HP, position, action fields, and combat-facing values. |
| `normal_scanner.py` | Scans and decodes normal-move information for the frame-data path. |
| `normal_scan_worker.py` | Background/cooperative worker for long normal scans without blocking the main UI. |
| `memory_scanner.py` | Shared memory-search helpers for research and diagnostic scans. |
| `special_runtime_finder.py` | Finds runtime special-move structures and relationships. |
| `red_health_scanner.py` | Fighter-local red-health analysis. |
| `global_red_health_scanner.py` | Match/global red-health analysis. |
| `bone_scanner.py` | Bone and transform discovery for rendering and hitbox research. |

### `tvcgui/features/combat/`: live combat data

| File | Role |
|---|---|
| `moves.py` | Reads live action state, resolves display labels, and applies character-aware move mapping. |
| `move_id_map.py` | Lookup layer for move IDs and readable move names. |
| `move_writer.py` | Controlled move/action write helpers for reviewed edit paths. |
| `meter.py` | Meter reads and meter-state cache handling. |
| `advantage.py` | Frame-advantage tracking and combat timing state. |
| `projectile_scanner.py` | Projectile scanner UI and live projectile discovery route. |
| `projectile_backtrace.py` | Connects projectile instances to likely source/action information. |
| `projectile_templates.py` | Projectile structure/template definitions used by projectile reads. |

### `tvcgui/features/frame_data/`: frame-data workbench

| File | Role |
|---|---|
| `window.py` | Public opener for the frame-data tool. |
| `workbench.py` | Main editable Tk workbench, including refresh, selection, probes, and write actions. |
| `binding.py` | Binds cached rows to live fighter-table identity rather than only a slot label, preventing stale tag-swap data from being reused. |
| `tree.py` | Builds the normal/special/super/projectile hierarchy, grouping, filtering, and tree presentation. |
| `patterns.py` | Command-stream packet signatures, parser rules, and decoded field definitions. |
| `patch_runtime.py` | Applies, tracks, restores, and persists reviewed runtime frame-data patches. |
| `editors.py` / `write_helpers.py` | Validates editable fields and converts approved edits into runtime actions. |
| `dumper.py` | Captures/exports decoded frame-data structures. |
| `formatters.py` | Formats decoded values for the workbench. |
| `move_families.py` | Classifies and groups moves for the tree. |
| `projectile_integration.py` | Adds projectile-derived information to the workbench model. |
| `super_integration.py` | Adds super-specific static and runtime information. |
| `unknowns.py` | Presents unclassified static signals without treating them as confirmed mechanics. |
| `widgets.py`, `dialogs.py`, `ui_prefs.py`, `utils.py` | Workbench UI components, dialogs, persistent presentation preferences, and shared helpers. |

### `tvcgui/features/hitboxes/` and `tvcgui/features/overlay/`

| File | Role |
|---|---|
| `features/hitboxes/renderer.py` | Transparent hitbox/hurtbox overlay. Handles collision reads, camera state, filters, invulnerability display, ranges/rulers, contact audits, and persisted range profiles. |
| `features/overlay/manager.py` | Writes the serialized per-frame payload consumed by overlay processes and manages overlay state. |
| `features/overlay/hud_renderer.py` | Transparent HUD overlay process parented to Dolphin's window. |
| `features/overlay/master_renderer.py` | Transparent master-overlay host for consolidated overlay rendering. |
| `features/overlay/drawing.py` | Shared Pygame drawing utilities for live combat panels and move information. |
| `features/overlay/editor.py` | Tk editor for controlled arcade/HUD values such as timer, score, stage, and win display. |

### `tvcgui/features/training/`

| File | Role |
|---|---|
| `mission_manager.py` | Loads mission definitions, tracks active mission progress, handles success/failure/reset conditions, and exposes display-ready state. |
| `mission_mode.py` | Mission schema, parsing, validation, and data-routing helpers. |
| `megacrash_window.py` | Tk configuration window for the MegaCrash trainer. Runtime behavior remains in `runtime/megacrash.py`. |
| `stun_profiler.py` | Learns engine-resolved stun values when static frame-data rows use unresolved/sentinel values. |
| `flags.py` | Training/debug-flag reads and writes. |
| `timer_debug.py` | Timer and timing diagnostic helpers. |
| `win_counter_gate.py` | Runtime permission gate for win-counter editing. |
| `win_counter_window.py` | Tk interface for controlled win-counter edits. |

### `tvcgui/features/animation/` and `tvcgui/features/assists/`

| File | Role |
|---|---|
| `animation/database.py` | Loads the FPK-derived animation database and character registry. |
| `animation/runtime.py` | Resolves MOT runtime tables and performs narrowly scoped animation-only pointer changes. Gameplay SEQ links and visual MOT selection remain separate. |
| `assists/api.py` | Public entry points for assist analysis. |
| `assists/backend.py` | Assist scan implementation, anchors, payload recognition, and character-specific analysis logic. |
| `assists/config.py` | Assist scan configuration and persisted quick-assist data access. |

### `tvcgui/features/character_select/`

| File | Role |
|---|---|
| `runtime.py` | Main character-select patch system. Owns roster state, safe patch/restore flow, extra-slot behavior, thumbnail/icon routes, hover display profiles, and scene checks. |
| `window.py` | Tk control surface for character-select changes, clone choices, route controls, state display, and restoration. |

Character-select writes are deliberately isolated and scene-guarded. The default platform layer quarantines writes during select unless an approved character-select route has enabled them.

### `tvcgui/tools/diagnostics/`, `tvcgui/tools/probes/`, and `tvcgui/tools/character_select/`

| File | Role |
|---|---|
| `diagnostics/assist_detector.py` | Live assist-activity diagnostic. |
| `diagnostics/projectile_pool_monitor.py` | Projectile-pool allocation and instance monitor. |
| `probes/hitbox_probe.py` | Focused collision-structure probe. |
| `probes/hitbox_spawn_probe.py` | Standalone hitbox-spawn/translation research utility. |
| `probes/select_screen_probe.py` | Read-oriented character-select scene probe. |
| `character_select/icon_material_patch.py` | Guarded extra-slot material-row patcher using reviewed donor rows. |
| `character_select/icon_tex0_probe.py` | Read-only BRRES/TEX0 capture and icon-mapping probe. |
| `character_select/mdl0_texptr_patch.py` | Focused texture-pointer patch experiment. |
| `character_select/thumbnail_probe.py` | Read-only donor/appended thumbnail-row dump. |
| `character_select/yami_cmn_icon.py` | Narrow `icon_cmn` material route for appended Yami/Solo thumbnails. |

---

## Data, Assets, Missions, and Generated Output

| Path | Role |
|---|---|
| `assets/portraits/` | Portrait textures loaded by `tvcgui.ui.portraits`. |
| `data/animation/` | FPK-derived animation-frame database and character FPK registry. |
| `data/assists/` | Quick-assist profiles. |
| `data/combat/` | Character-agnostic move-ID map plus projectile IDs and maps. |
| `data/frame_data/` | Static/preview frame-data profiles and workbench UI preferences. These files should not be casually regenerated. |
| `data/hitboxes/` | Hitbox filter state and saved/learned range profiles. |
| `data/overlay/` | HUD/master-overlay serialized state. In frozen builds, writable files live beside the EXE. |
| `data/runtime/` | Runtime stun profiles, character-select write trace, and diagnostic binary dumps. |
| `data/training/` | MegaCrash settings, mission overlay state, and mission progress. |
| `fd_patches/` | Persistent frame-data patch records. |
| `missions/` | Character-specific mission JSON files and `example_json.json` schema reference. |
| `tests/` | Standard deterministic unit/contract tests. |
| `dist/` | Generated release output. `TvCGUI.exe` is the public artifact. |
| `build/` | Generated PyInstaller intermediate files. Rebuildable; not source of truth. |
| `archive/` | Protected retired aliases, old data, backups, and integrity hashes. Active code should not import from this tree. |
| `memory_dumps/`, `char_test_traces/`, `chrsel_icon_tex0_probe_dump/`, `chrsel_yami_cmn_icon_backups/` | Diagnostic evidence and research artifacts. They are not required for normal launch. |

---

## Developer Source Runs

This section is only for source work. It is not part of release installation.

### `run.bat`

`run.bat` is a Windows convenience launcher for the source tree. It:

1. Looks for `.venv\Scripts\python.exe`.
2. Calls the local setup script when the project environment is missing.
3. Sets `PYTHONPATH` to `tdp-modules`.
4. Starts `tdp-modules\main.py`.

The release EXE does not use `run.bat`.

### Direct source launch

From `tdp-modules/`:

```bat
python main.py
```

From the repository root after configuring the project environment:

```bat
run.bat
```

### Test and smoke-test commands

Run from `tdp-modules/`:

```bat
python run_unit_tests.py
python run_regression_tests.py
python run_dolphin_smoke_tests.py
```

`run_dolphin_smoke_tests.py` is read-only by default. Its optional write-echo mode should only be used with a reviewed safe address because it writes the existing byte value back to that same location.

```bat
python run_dolphin_smoke_tests.py --write-echo 0x90000000
```

### Release build

The public one-file release is built from `TvCGUI_onefile.spec`. Generated output belongs under `dist/`; `build/` is PyInstaller working output.

---

## Change Guide

| Change type | Start with |
|---|---|
| Main application behavior or launch wiring | `main.py`, then `launcher.py` and the relevant `tvcgui.features.*` module. |
| Dolphin read/write behavior | `platform/dolphin.py`, `platform/patch_manager.py`, and `core/constants.py`. |
| Shared offset or memory-range work | `core/constants.py`; place feature-only values beside the feature that validates them. |
| Fighter snapshots, move labels, or slot behavior | `tools/scanners/fighter_resolver.py`, `tools/scanners/fighter_state.py`, `features/combat/moves.py`, and `features/combat/move_id_map.py`. |
| Frame data | `features/frame_data/workbench.py`, `features/frame_data/patterns.py`, `features/frame_data/patch_runtime.py`, and `tools/scanners/normal_scanner.py`. |
| Hitbox rendering or range learning | `features/hitboxes/renderer.py` and `data/hitboxes/`. |
| Missions | `features/training/mission_manager.py`, `features/training/mission_mode.py`, and the matching file under `missions/`. |
| MegaCrash behavior | `runtime/megacrash.py` and `features/training/megacrash_window.py`. |
| KO-control behavior | `runtime/ko_control.py` and its call sites in `main.py`. |
| Character-select work | `features/character_select/runtime.py`, then the relevant guarded probe or narrow patch module. |
| HUD/transparent overlay behavior | `features/overlay/manager.py`, `hud_renderer.py`, `master_renderer.py`, and `core/subprocess_compat.py`. |
| Persisted settings or packaged data | `core/paths.py`, then the corresponding `data/` directory. |
| Regression coverage | `tests/`, root `test_*.py` scripts, `run_regression_tests.py`, and `test_contract_baseline.json`. |

---

## Compatibility and Maintenance Notes

TvC Continuo depends on the supported game's live memory layout and Dolphin's process behavior. A different game revision, changed emulator behavior, or incompatible scene can invalidate reads, overlays, scans, and runtime patches.

When a feature stops working after a game or emulator change, validate the Dolphin boundary first:

```bat
python run_dolphin_smoke_tests.py
```

Then validate fighter resolution, move mapping, affected parser rules, feature-local offsets, and the corresponding regression contracts before changing shared constants.

- Keep shared offsets in `core/constants.py`.
- Keep feature-specific signatures and addresses with the feature that validates them.
- Use `core/paths.py` for mutable data; do not write into PyInstaller's temporary extraction directory.
- Keep general memory I/O behind `platform/dolphin.py`.
- Treat character-select writes as guarded runtime patches.
- Preserve unclassified values as unknown until behavior is repeatably validated and covered by regression tests.
