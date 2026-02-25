# Tatsunoko vs. Capcom – Live HUD & Memory Scanner

a Python-based live memory overlay for Tatsunoko vs. Capcom: Ultimate All-Stars (Wii), running on Dolphin Emulator.  
This suite connects directly to Dolphin’s RAM and provides:

- A real-time training HUD with health, meter, and frame advantage tracking  
- Automated move-table and frame-data scanning  
- Memory tools for debugging and reverse-engineering fighter structs  

---

## Overview

The HUD and scanner work together to visualize live match data pulled directly from Dolphin’s memory.  
It supports all four character slots (P1-C1, P1-C2, P2-C1, P2-C2) and provides dynamic pointer resolution to track state even across tags and swaps.

---

## Features

### Real-Time HUD
- 4-panel live display for both teams (HP, meter, position, current move)
- Color-coded health and pooled HP (red-life style)
- Baroque readiness and activation flags
- Real-time frame advantage computation based on live hits
- Event feed for hits and inferred attacker/victim pairs

### Frame Data Scanner
- Deep MEM2 analysis via `scan_normals_all.py`:
  - Extracts startup, active, recovery, hitstun, blockstun, damage, knockback, and meter values
  - Computes estimated frame advantage on hit/block
- Interactive Tkinter move-table window per slot (F1–F4)
- Supports auto-scan or manual F5 triggers

### Memory Tools
- `redscan.py` and `global_redscan.py`: detect HP-correlated bytes (red-life/mystery bytes)
- `memscan.py`: scans MEM1/MEM2 for ASCII strings and backreferences
- `resolver.py`: automatically resolves and validates fighter base pointers
- `tvc_fill_bacluster.py`: read/fill/restore fighter float clusters (BA40–BA9F)

### HUD Hotkeys

| Key | Action |
|-----|--------|
| **F5** | Manual move-table re-scan |
| **Mouse click on frame-data button** | Opens Tkinter move list |

---

## Installation

### Requirements
- Python 3.10+
- Dolphin Emulator (US build recommended)
- Pygame

```

pip install pygame

```

### Quick Setup (Windows example)

```

python -m venv .venv
.venv\Scripts\activate
pip install pygame

```

Ensure Dolphin is running TvC and memory read/write is enabled.  
If your revision differs, update addresses in `constants.py`.

---

## Running the HUD

```

python main.py

```

Wait until the console shows that Dolphin is hooked.  
When connected, four fighter panels and event logs appear automatically.

---

## Frame Data Scanning

To scan and view per-character move tables:

```

python -m scan_normals_all

```

Or use HUD hotkeys (F5) to refresh, then click “Show frame data.”  
Displays move labels, startup, active frames, hitstun, blockstun, and computed frame advantage.

---

## Fighter Cluster Utility

```

python tvc_fill_bacluster.py --action scan
python tvc_fill_bacluster.py --slot P1C1 --action info
python tvc_fill_bacluster.py --slot P1C1 --action fill --value 0x00

```

This safely backs up and restores the memory region 0xBA40–0xBA9F, useful for red-life and transformation testing.

---

## Memory Reference (US Build)

### Slot Pointers

```

803C9FCC PTR_P1_CHAR1
803C9FDC PTR_P1_CHAR2
803C9FD4 PTR_P2_CHAR1
803C9FE4 PTR_P2_CHAR2

```

### Fighter Struct (relative to resolved base)

```

+0x14 Character ID (u32)
+0x24 Max HP (s32)
+0x28 Current HP (s32)
+0x2C Aux HP / Red-life (s32)
+0x40 Last damage chunk (s32)
+0x4C Super meter primary (s32)
+0xF0 Position X (f32)
+0xF4 Position Y (auto-picked by variance)

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

1  Ken the Eagle
2  Casshan
3  Tekkaman
4  Polimar
5  Yatterman-1
6  Doronjo
7  Ippatsuman
8  Jun the Swan
10 Karas
12 Ryu
13 Chun-Li
14 Batsu
15 Morrigan
16 Alex
17 Viewtiful Joe
18 Volnutt
19 Roll
20 Saki
21 Soki
26 Tekkaman Blade
27 Joe the Condor
28 Yatterman-2
29 Zero
30 Frank West

```

Extend the list in `constants.py` under `CHAR_NAMES`.

---

## Attacker Detection Logic

When a victim’s last-hit field or HP drops, the system:
1. Logs a hit for that fighter
2. Finds the nearest opponent (distance² heuristic)
3. Associates that attacker to compute live frame advantage

This method is consistent during “Hit Anywhere” testing and normal gameplay.

---

## File Overview

| File | Purpose |
|------|----------|
| **main.py** | Main Pygame HUD loop |
| **fighter.py** | Reads fighter structs (HP, state, flags) |
| **advantage.py** | Computes and tracks advantage between players |
| **hud_draw.py** | Visual HUD rendering |
| **resolver.py** | Slot pointer resolution logic |
| **redscan.py / global_redscan.py** | HP correlation scanners |
| **memscan.py** | Global ASCII + pointer reference search |
| **scan_normals_all.py** | Full move table scan with advantage computation |
| **tvc_fill_bacluster.py** | Fighter cluster inspection utility |
| **constants.py** | Addresses, IDs, offsets |
| **config.py** | Screen, color, and address config |
| **events.py** | Logs and CSV output for hits/advantage |
| **moves.py** | Character move label mapping (CSV-based) |

---

## Troubleshooting/Bugs

- HUD says “waiting for Dolphin”: ensure Dolphin is running and TvC is loaded.  
- HP not updating: verify slot pointers and region in `constants.py`.  
- Tk window not opening: run a scan first (F5) and then open it.  
- Character shows “```”: map the ID in `CHAR_NAMES`.  
- Giants are currently not supported, they do something weird with the table references, this will be investigated later
- HUD Will not hook to characters despite dolphin being loaded seems like a scanning problem I never got around to, load a savestate or restart dolphin, I'm gonna look for a solution later

---

## Developer Notes

- RedScan requires multiple snapshots for meaningful results.  
- Frame advantage auto-corrects using active frames when hit timing is off.  
- CSV output logged under `HIT_CSV` with frame index, damage, and participants.  
- Fully compatible with newer Dolphin memory APIs (`dolphin_io.py`).

---

## License

MIT License , free for community use and research.  
Not affiliated with Capcom, Tatsunoko, or Dolphin developers.
