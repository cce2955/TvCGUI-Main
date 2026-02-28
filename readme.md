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
It supports all four character slots (P1-C1, P1-C2, P2-C1, P2-C2) with dynamic pointer resolution to track state across tags and swaps.  
The name is a nod to the basso continuo — the continuous harmonic backbone of Baroque music — fitting for a tool built around TvC's Baroque mechanic.

---

## Features

### Real-Time HUD
- 4-panel live display for both teams (HP, meter, position, current move)
- Color-coded health bars with pooled HP (red-life style)
- Baroque readiness and activation tracking
- Real-time frame advantage computation based on live hits
- Event feed for hits and inferred attacker/victim pairs
- Giant character support (PTX-40A, Gold Lightan) with automatic solo panel layout

### Hitbox Overlay
- Launch a live hitbox visualizer directly from the HUD with one click
- Per-slot color-coded filter checkboxes (P1/P2/P3/P4) to toggle individual slots
- Overlay runs as an independent process and closes automatically when the HUD exits

### Frame Data Scanner
- Deep MEM2 analysis via `scan_normals_all.py`:
  - Extracts startup, active, recovery, hitstun, blockstun, damage, knockback, and meter values
  - Computes estimated frame advantage on hit/block
- Interactive move-table window per character slot
- Auto-scan on character change or manual F5 trigger

### Memory Tools
- `redscan.py` / `global_redscan.py`: detect HP-correlated bytes (red-life / mystery bytes)
- `memscan.py`: scans MEM1/MEM2 for ASCII strings and backreferences
- `resolver.py`: automatically resolves and validates fighter base pointers
- `tvc_fill_bacluster.py`: read/fill/restore fighter float clusters (BA40–BA9F)

### HUD Controls

| Input | Action |
|-------|--------|
| **F5** | Manual move-table re-scan |
| **Click "Activate Hitboxes"** | Toggle hitbox overlay on/off |
| **Filter: P1/P2/P3/P4** | Toggle hitbox visibility per slot |
| **Click "Frame Data" on panel** | Open move list for that character |
| **Click debug row** | Copy memory address to clipboard |
| **Click character panel** | Copy fighter base address to clipboard |
| **Scroll wheel on debug panel** | Scroll debug flag list |

---

## Installation

### Requirements
- Python 3.10+
- Dolphin Emulator (US build recommended)
- Pygame

### Quick Setup (Windows)

The easiest way is to use the included batch files. They will set up the virtual environment automatically if it doesn't exist.

**Full HUD + all tools:**
```
run.bat
```

**Hitbox overlay only (lightweight):**
```
hitbox.bat
```

To set up manually:
```
python -m venv .venv
.venv\Scripts\activate
pip install pygame
```

Ensure Dolphin is running TvC and memory read/write is enabled.  
If your revision differs from the US build, update addresses in `constants.py`.

---

## Running the HUD

```
python main.py
```

Wait until the console shows that Dolphin is hooked.  
When connected, four fighter panels and the event log appear automatically.

---

## Frame Data Scanning

To scan and view per-character move tables:

```
python -m scan_normals_all
```

Or use the HUD (F5 to refresh, then click "Frame Data" on any panel).  
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
MEM1: 0x80000000–0x817FFFFF
MEM2: 0x90000000–0x93FFFFFF
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

Extend the list in `constants.py` under `CHAR_NAMES`.

---

## Attacker Detection Logic

When a victim's HP drops or a hit state is detected, the system:
1. Logs a hit for that fighter
2. Finds the nearest opponent (distance² heuristic)
3. Associates that attacker to compute live frame advantage

This method is consistent during training mode and normal gameplay.

---

## File Overview

| File | Purpose |
|------|---------|
| **main.py** | Main Pygame HUD loop |
| **hitboxesscaling.py** | Standalone hitbox overlay |
| **fighter.py** | Reads fighter structs (HP, state, flags) |
| **advantage.py** | Computes and tracks frame advantage |
| **hud_draw.py** | Visual HUD rendering |
| **layout.py** | Panel layout and giant slot normalization |
| **resolver.py** | Slot pointer resolution logic |
| **meter.py** | Meter state reading and caching |
| **moves.py** | Character move label mapping (CSV-based) |
| **move_id_map.py** | Move ID to name lookup |
| **scan_normals_all.py** | Full move table scan with advantage computation |
| **scan_worker.py** | Background thread for non-blocking scans |
| **frame_data_window.py** | Interactive move-table GUI window |
| **debug_panel.py** | Debug flag overlay rendering and click areas |
| **training_flags.py** | Training mode flag reader |
| **redscan.py / global_redscan.py** | HP correlation scanners |
| **memscan.py** | Global ASCII + pointer reference search |
| **tvc_fill_bacluster.py** | Fighter cluster inspection utility |
| **events.py** | Hit and advantage logging / CSV output |
| **constants.py** | Addresses, character IDs, offsets |
| **config.py** | Screen, color, and address config |
| **portraits.py** | Portrait loading and slot matching |

---

## Troubleshooting

- **HUD says "waiting for Dolphin"** — ensure Dolphin is running with TvC loaded. If Dolphin is loaded it may be looking at a stale process or a different Dolphin PID. Close all Dolphin windows and the HUD, then relaunch both

---

## Developer Notes

- RedScan requires multiple snapshots for meaningful results.
- Frame advantage auto-corrects using active frames when hit timing is off.
- CSV output logged under `HIT_CSV` with frame index, damage, and participants.
- Fully compatible with newer Dolphin memory APIs (`dolphin_io.py`).

---

## License

MIT License — free for community use and research.  
Not affiliated with Capcom, Tatsunoko, or the Dolphin project.