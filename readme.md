# TvC Continuo

A Python-based live memory overlay for Tatsunoko vs. Capcom: Ultimate All-Stars (Wii), running on Dolphin Emulator.
This suite connects directly to Dolphin's RAM and provides:

- A real-time training HUD with health, meter, baroque tracking, and frame advantage
- Live hitbox overlay with per-slot filtering
- Automated move-table and frame-data scanning
- Memory tools for debugging and reverse-engineering fighter structs

---

## Overview

TvC Continuo visualizes live match data pulled directly from Dolphin's memory.
It supports all four character slots (P1-C1, P1-C2, P2-C1, P2-C2) with dynamic pointer resolution to track state across tags, swaps, and giant normalization logic.

The HUD runs at a fixed 60 FPS and is designed to remain stable even during pointer churn, character swaps, assists, and match transitions.

---

## Features

### Real-Time HUD

- 4-panel live display for both teams (HP, meter, position, current move)
- Color-coded health bars with pooled HP (red-life style)
- True 32-bit baroque detection using live HP32 vs Pool32 comparison
- Baroque readiness and activation tracking
- Real-time frame advantage computation based on live hits
- Event feed for hits and inferred attacker/victim pairs
- Correct giant-solo detection (only when C1 and C2 share the same base)
- Per-slot assist phase tracking (fly-in / attack / recover inference)
- Per-slot input monitor (P1-C1)
- Dynamic character metadata caching (true struct ID + CSV correction)
- Panel slide and fade animations
- Clipboard integration (click fighter panel to copy base address)
- Scrollable debug overlay

---

### Hitbox Overlay

- Launch a live hitbox visualizer directly from the HUD with one click
- Per-slot color-coded filter checkboxes (P1/P2/P3/P4)
- Runtime slot filter persisted via hitbox_filter.json
- Overlay runs as an independent subprocess
- Automatic overlay shutdown when the HUD exits
- Live process monitoring (overlay state reflects actual subprocess state)

---

### Frame Data Scanner

- Deep MEM2 analysis via scan_normals_all.py
- Extracts:
  - Startup
  - Active
  - Recovery
  - Hitstun
  - Blockstun
  - Damage
  - Knockback
  - Meter values
- Computes estimated frame advantage on hit and block
- Interactive move-table window per character slot
- Background scan worker (non-blocking)
- Auto-scan on character change
- Manual F5 trigger
- Synchronous fallback scan if worker is unavailable
- Slide-in animation when new scan data arrives

---

### Assist Phase Tracking

Each slot maintains a lightweight assist state machine:

```
None -> flyin -> attack -> recover -> None
```

Assist phases are inferred from animation IDs using:

```
ASSIST_FLYIN_IDS
ASSIST_ATTACK_IDS
```

Snapshots are augmented with:

```
snap["assist_phase"]
snap["is_assist"]
```

This system is conservative and animation-driven.
Future refinement can replace animation ID inference with explicit assist struct mapping.

---

### True 32-bit Baroque Detection

In addition to pool-byte tracking, TvC Continuo reads:

```
+0x28  HP32
+0x2C  Pool32
```

Baroque readiness is determined by:

```
hp32 != pool32
```

The HUD computes:

- baroque_local_hp32
- baroque_local_pool32
- baroque_ready_local
- baroque_red_amt
- baroque_red_pct_max

This avoids inaccuracies from 8-bit pool tracking and reflects actual red-life state.

---

### Memory Tools

- redscan.py / global_redscan.py - detect HP-correlated bytes (red-life / mystery bytes)
- memscan.py - scans MEM1 and MEM2 for ASCII strings and backreferences
- resolver.py - automatically resolves and validates fighter base pointers
- tvc_fill_bacluster.py - read, fill, and restore fighter float clusters (BA40-BA9F)

---

### Interactive Debug Flags

The debug panel supports direct memory writes:

- Toggle flags (PauseOverlay, FreeBaroque, CameraLock, etc.)
- Cycle values (CpuAction, CpuGuard, DummyMeter, CpuDifficulty)
- Momentary triggers (HypeTrigger, SpecialPopup)
- Coupled logic (P2Pause auto-syncs TrPause)

Momentary writes are automatically restored after a short delay to prevent unintended state corruption.

All debug writes are performed safely via wd8() and are guarded against failure.

---

## HUD Controls

| Input | Action |
|-------|--------|
| F5 | Manual move-table re-scan |
| Click "Activate Hitboxes" | Toggle hitbox overlay on or off |
| Filter: P1/P2/P3/P4 | Toggle hitbox visibility per slot |
| Click "Frame Data" on panel | Open move list for that character |
| Click debug row | Copy memory address to clipboard |
| Click character panel | Copy fighter base address to clipboard |
| Scroll wheel on debug panel | Scroll debug flag list |

---

## Installation

### Requirements

- Python 3.10+
- Dolphin Emulator (US build recommended)
- Pygame

---

### Quick Setup (Windows)

Full HUD plus all tools:

```
run.bat
```

Hitbox overlay only:

```
hitbox.bat
```

Manual setup:

```
python -m venv .venv
.venv\Scripts\activate
pip install pygame
```

Ensure Dolphin is running TvC and memory read and write is enabled.
If your revision differs from the US build, update addresses in constants.py.

---

## Running the HUD

```
python main.py
```

Wait until the console shows Dolphin is hooked.
When connected, four fighter panels and the event log appear automatically.

---

## Frame Data Scanning

```
python -m scan_normals_all
```

Or use the HUD (F5 then click "Frame Data").
Displays move labels, startup, active frames, hitstun, blockstun, and computed frame advantage.

---

## Memory Reference (US Build)

### Slot Pointers

```
803C9FCC  PTR_P1_CHAR1
803C9FDC  PTR_P1_CHAR2
803C9FD4  PTR_P2_CHAR1
803C9FE4  PTR_P2_CHAR2
```

### Fighter Struct (relative to resolved base)

```
+0x14  Character ID (u32)
+0x24  Max HP (s32)
+0x28  Current HP (s32)
+0x2C  Aux HP / Red-life (s32)
+0x40  Last damage chunk (s32)
+0x4C  Super meter primary (s32)
+0xF0  Position X (f32)
+0xF4  Position Y (auto-picked by variance)
```

### Valid Ranges

```
MEM1: 0x80000000-0x817FFFFF
MEM2: 0x90000000-0x93FFFFFF
BAD_PTRS: {0x00000000, 0x80520000}
```

---

## Character IDs (known)

```
1   Ken the Eagle
2   Casshan
3   Tekkaman
4   Polimar
5   Yatterman-1
6   Doronjo
7   Ippatsuman
8   Jun the Swan
10  Karas
11  PTX-40A
12  Ryu
13  Chun-Li
14  Batsu
15  Morrigan
16  Alex
17  Viewtiful Joe
18  Volnutt
19  Roll
20  Saki
21  Soki
22  Gold Lightan
26  Tekkaman Blade
27  Joe the Condor
28  Yatterman-2
29  Zero
30  Frank West
```

Extend the list in constants.py under CHAR_NAMES.

---

## Attacker Detection Logic

When a victim's HP drops or a hit state is detected, the system:

1. Logs a hit for that fighter
2. Finds the nearest opponent (distance squared heuristic)
3. Associates that attacker to compute live frame advantage

This method is consistent during training mode and normal gameplay.

---

## File Overview

| File | Purpose |
|------|---------|
| main.py | Main Pygame HUD loop |
| hitboxesscaling.py | Standalone hitbox overlay |
| fighter.py | Reads fighter structs |
| advantage.py | Frame advantage tracker |
| hud_draw.py | Visual HUD rendering |
| layout.py | Panel layout and giant normalization |
| resolver.py | Slot pointer resolution |
| meter.py | Meter state reading |
| moves.py | Move label mapping |
| move_id_map.py | Move ID to name lookup |
| scan_normals_all.py | Full move table scanner |
| scan_worker.py | Background scan thread |
| frame_data_window.py | Interactive frame data GUI |
| debug_panel.py | Debug overlay rendering |
| training_flags.py | Training flag reader |
| redscan.py / global_redscan.py | HP correlation scanners |
| memscan.py | ASCII and pointer reference search |
| tvc_fill_bacluster.py | Fighter float cluster tool |
| events.py | Hit and advantage logging |
| constants.py | Offsets and IDs |
| config.py | Screen and color config |
| portraits.py | Portrait loading |

---

## Troubleshooting

If the HUD says "waiting for Dolphin":

- Ensure Dolphin is running TvC
- Close duplicate Dolphin instances
- Relaunch both Dolphin and the HUD

---

## Developer Notes

- Bulk fighter struct reads use rbytes() for performance
- Character metadata cache refreshes automatically on ID change
- Safe wrappers prevent pointer churn from crashing the render loop
- RedScan requires multiple snapshots
- Frame advantage auto-corrects using active frames
- CSV output logged under HIT_CSV
- Compatible with modern Dolphin memory APIs (dolphin_io.py)

---

## License

MIT License - free for community use and research.
Not affiliated with Capcom, Tatsunoko, or the Dolphin project.

---

## Special Thanks

Jaaaames - for his amazing foundational work
The TvC community led by Dr. Science
Capcom and their amazing work over the years
Brian Transeau
This fish sandwich with homemade coleslaw and spicy mayo sitting in front of me