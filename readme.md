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

# TvC Continuo — Offsets Reference

---

## Slot Pointers

```
803C9FCC  PTR_P1_CHAR1
803C9FDC  PTR_P1_CHAR2
803C9FD4  PTR_P2_CHAR1
803C9FE4  PTR_P2_CHAR2
```

---

## Fighter Struct Offsets (relative to resolved base)

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

---

## Baroque Detection Offsets (relative to resolved base)

```
+0x28  HP32
+0x2C  Pool32
```

---

## Fighter Struct — Additional Fields (from fighter.py)

```
+0x02A  HP Pool Byte (u8) — experimental pooled health byte
+0x02B  Mystery Byte (u8) — unknown, tracked alongside pool
```

---

## Control / State Offsets (from fighter.py / constants.py)

```
CTRL_WORD_OFF   Control word (u32)
FLAG_062        State flag byte at +0x062
FLAG_063        State flag byte at +0x063
FLAG_064        State flag byte at +0x064
FLAG_072        State flag byte at +0x072
```

---

## Position Offsets (from resolver.py / fighter.py)

```
POSX_OFF        Position X (f32)              — defined in constants.py
POSY_CANDS      Y-offset candidates list       — sampled and selected by variance
0xF4            Fallback Y offset (f32)        — used if variance picker fails
```

---

## Move Block Pattern Offsets (from fd_patterns.py)

```
Phase Record Header:   04 01 02 3F  [u32 phase]  [u16 anim_id]
  - Anim ID at:        +8 from record header start

Legacy Anim Header:    01 ?? 01 3C
  - Anim ID at:        +1 from header start

Speed Mod Pattern:     20 3F 00 00 00 [XX] 04 17
  - Value byte (XX):   +5 from anchor start

SuperBG Pattern:       04 [XX] 60  (after anim anchor)
  - Toggle byte (XX):  +1 from 0x04 marker
  - ON  = 0x04
  - OFF = 0x01

Combo KB Modifier:     01 AC 3D 00 00 00 [XX]
                       01 AC 3F 00 00 00 [XX]
  - Value byte (XX):   +6 from pattern start
  - Scan range:        first 0x200 bytes of move block

Fallback Hitbox Offset: 0x21C
Hitbox Scan Max:        0x600
```

---

## Projectile Pattern (from fd_utils.py)

```
Suffix anchor:   00 00 00 0C FF FF FF FF
Damage word:     4 bytes immediately before suffix — format: 00 00 XX YY
  - Damage:      low 16 bits (XX YY, big-endian)
  - proj_tpl:    absolute address of the 00 00 XX YY word

Strength Slice Anchor:  A6 F0
  - slice[0] = L
  - slice[1] = M
  - slice[2] = H / C
```

---

## Scan Regions (from fd_window.py / fd_utils.py)

```
Default scan start:   0x92477400
Default scan end:     0x94477500
Default region size:  0x1400  (per-move block scan)
Max region clamp:     0x6000
```

---

## Hit Reaction Values (from fd_format.py)

```
0x000000  Stay on ground
0x000001  Ground/Air > KB
0x000002  Ground/Air > KD
0x000003  Ground/Air > Spiral KD
0x000004  Sweep
0x000008  Stagger
0x000010  Ground > Stay Ground, Air > KB
0x000040  Ground > Stay Ground, Air > KB, OTG > Stay OTG
0x000041  Ground/Air > KB, OTG > Stay OTG
0x000042  Ground/Air > KD, OTG > Stay OTG
0x000080  Ground > Stay Ground, Air > KB
0x000082  Ground/Air > KD
0x000083  Ground/Air > Spiral KD
0x000400  Launcher
0x000800  Ground > Stay Ground, Air > Soft KD
0x000848  Ground > Stagger, Air > Soft KD
0x002010  Ground > Stay Ground, Air > KB
0x003010  Ground > Stay Ground, Air > KB
0x004200  Ground/Air > KD
0x800002  Ground/Air > KD, Wall > Wallbounce
0x800008  Alex Flash Chop
0x800020  Snap Back
0x800080  Ground > Crumple, Air > KB
0x800082  Ground/Air > KD, Wall > Wallbounce
0x001001  Wonky: Friender/Zombies grab if KD near ground
0x001003  Wonky variant
```

---

## Knockback Trajectory Values (from fd_format.py)

```
0xBC  Up KB (Spiral)
0xBD  Up Forward KB
0xBE  Down Forward KB
0xC4  Up Pop (j.L / j.M)
```

---

## Stun Value Encoding (from fd_format.py)

```
Display  Raw Byte
10       0x0C
15       0x0F
17       0x11
21       0x15
```

---

## Float Cluster Range

```
BA40-BA9F  Fighter float cluster (read/fill/restore via tvc_fill_bacluster.py)
```

---

## Hitbox Overlay — Slot Base Addresses (MEM2, US Build)

```
P1: 0x9246B9C0
P2: 0x92B6BA00
P3: 0x927EB9E0
P4: 0x92EEBA20
```

---

## Hitbox Struct Layout (relative to slot_base + struct_shift)

```
struct_shift: +0x4C0       (hitbox struct starts here, relative to slot base)

Block offsets within struct:
  Block 0: +0x64
  Block 1: +0xA4
  Block 2: +0xE4

Per-block field offsets:
  +0x00  X position (f32)
  +0x04  Y position (f32)
  +0x18  Radius (f32)
  +0xC3  Active flag (u8)  — 0x53 = active hitbox
```

---

## Hitbox Motion / Camera Struct (static base)

```
Base: 0x8053CB20
  +0x00  X (f32)
  +0x04  Y (f32)
  +0x08  Z (f32)
  +0x0C  W (f32)
```

---

## gui_hitbox_probe.py — Table Probe Constants

```
TAIL_PATTERN:        00 00 00 38 01 33 00 00
ANCHOR_BYTES:        FF FF FF FE
ANCHOR_REL_FROM_TAIL: -0xB0   (anchor is 0xB0 bytes before tail)
SAMPLE_TAIL_REL:     0x10     (capture starts 0x10 before tail)
SCAN_MATCH_LEN:      0x40     (bytes matched when identifying character table)
CAPTURE_SIZE:        0x2000   (8KB capture window)
```

---

## memscan.py — Memory Region Definitions

```
MEM1: 0x80000000 - 0x81800000  (~24 MB)
MEM2: 0x90000000 - 0x94000000  (up to 64 MB)
Chunk size: 0x10000 (64KB reads)
Local scan radius: ±0x4000 around fighter base
```

---

## Valid Memory Ranges

```
MEM1: 0x80000000 - 0x817FFFFF
MEM2: 0x90000000 - 0x93FFFFFF
```
---
## Global Table Pointer
```
Global Table Pointer 0x803AA4C0
```
---

## Bad Pointers

```
0x00000000
0x80520000
```

---

## Meter Offsets (from meter.py / constants.py)

```
METER_OFF_PRIMARY    Super meter primary read address   (relative to fighter base)
METER_OFF_SECONDARY  Super meter secondary/mirror bank  (relative to fighter base)
  - Valid meter range: 0 .. 200,000
  - Known full-meter sentinel: 50,000 (0xC350)
```

---

## Attack / Move ID Offsets (from moves.py / constants.py)

```
ATT_ID_OFF_PRIMARY   Primary attack/state ID (u32, relative to fighter base)
ATT_ID_OFF_SECOND    Secondary attack ID     (u32, relative to fighter base)
```

---

## Normal Move Anim ID Map (from scan_normals_all.py)

```
0x00  5A       0x01  5B       0x02  5C
0x03  2A       0x04  2B       0x05  2C
0x06  6C       0x08  3C
0x09  j.A      0x0A  j.B      0x0B  j.C
0x0E  6B
```

---

## Flag 0x62 State Decode Table (from moves.py)

```
Value   State
0       ATTACK_ACTIVE
8       STUN_LOCK
16      THROW
32      MOVEMENT
40      IMPACTED
48      THROW_TECH
64      ??? (throw knockdown only)
128     ATK_REC
136     ATK_END
160     IDLE_BASE
168     ENGAGED
```

---

## Flag 0x63 State Decode Table (from moves.py)

```
Value   State
0       STARTUP
1       NEUTRAL
4       HITSTUN_PUSH
6       HIT_COMMIT
16      BLOCK_PUSH
17      ATKR_READY
32      STARTUP
34      CHAIN_BUFFER
36      HIT_RESOLVE
37/5    RECOVERY
64      AIR_ASCEND_ATK
65      AIR_CANCEL
68      AIR_IMPACT
70      AIR_PREHIT
96      AIR_CHAIN_BUF1
168     DEF_READY
192     AIR_DESC_ATK
193     FALLING
194     AIR_CHAIN_END
196     KB_VERTICAL
197     KB_GROUNDED
198     KB_VERTICAL_PEAK
224     AIR_CHAIN_BUF2
230     AIR_CHAIN_BUF3
```

---

## scan_normals_all.py — Scanner Constants

```
CLUSTER_GAP:         0x4000   (max gap between blocks treated as same cluster)
CLUSTER_PAD_BACK:    0x400    (padding behind cluster start for region read)
LOOKAHEAD_AFTER_HDR: 0x80     (bytes scanned after anim header for frame data)
PAIR_RANGE:          0x600    (search range when pairing move blocks)
INLINE_ACTIVE_OFF:   0xB0     (offset within move block where inline active frames live)
SLOT_SCAN_BEFORE:    0x2000   (bytes before slot base to begin scan)
SLOT_SCAN_LENS:      0x30000, 0x50000, 0x80000  (192KB / 320KB / 512KB scan windows)
```

---

## scan_normals_all.py — Pattern Headers

```
DAMAGE_HDR:          35 10 20 3F 00
HITREACTION_CODE_OFF: +28 from HITREACTION_HDR match start
HITBOX_OFF_X:        +0x40 from hitbox block base
HITBOX_OFF_Y:        +0x48 from hitbox block base
INLINE_ACTIVE_LEN:   17 bytes
```

---

## Projectile Instance Struct Offsets (from projectile.py)

```
+0x68  Collider pointer (u32)
+0x70  Owner fighter_base pointer (u32)
+0x94  Projectile life counter (u32)
```

---

## Projectile Definition / Template Block (from projectile.py / projectiles.py)

```
Segment header:        00 00 00 04  (block starts here)
Delimiter markers:     FF FF FF FF FF FF FF FF  (FF*8)
                       FF FF FF FF              (FF*4)
Hitbox marker set:     35 0D 20 3F
                       33 0D 20 3F
                       37 0D 20 3F
  - Hitbox radius:     +0x44 from marker
Behavior triple:       ?? [family] [variant]
  - Default family:    0xA5
  - Default variant:   0xA0
Physics cluster:       [f32 speed] [f32 accel] [u32 0x00000000] [f32 cap]
  - Dominant cluster gap: ~0x40 .. 0x800
DEF_REGION (scan target): 0x90800000 .. 0x90A80000
```

---

## redscan.py — Fighter Struct Scan Range

```
SCAN_START: +0x000  (relative to fighter base)
SCAN_END:   +0x100  (exclusive; scans offsets 0x000..0x0FF)
```

---

## special_runtime_finder.py — Special Animation Scan

```
Pattern:    01 [XX] 01 3C   where XX in [0x01 .. 0x1E]
Region:     MEM2 full scan (MEM2_LO .. MEM2_HI)
```
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

- Jaaaames - for his amazing foundational work
- The TvC community led by Dr. Science
- Capcom and their amazing work over the years
- Brian Transeau
- This fish sandwich with homemade coleslaw and spicy mayo sitting in front of me, man I wish I had some cajun fries.