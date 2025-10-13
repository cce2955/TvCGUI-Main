# TvC HUD — Dolphin memory overlay (Tk GUI)

Small Python package that reads Tatsunoko vs. Capcom state from Dolphin via ```dolphin-memory-engine``` and shows a live HUD:

per-fighter HP, meter, position, last damage chunk

“Recent Hits” feed with inferred attacker (nearest opponent at hit time)

⚠️ Addresses are from the US build observed in RAM. If your revision differs, some pointers may need adjusting.

# 1) Install
Prereqs

Python 3.10+ (Windows/macOS/Linux)

Dolphin Emulator running TvC (US)

```dolphin-memory-engine``` (PyPI)

# Quick start (Windows, from the project root)

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

If you don’t have ```requirements.txt```, install directly:

```bat
pip install dolphin-memory-engine
```

# 2) Run

From the parent directory of the ```tvc_hud``` package:

```bat
python -m tvc_hud.main
```

Tip: make sure both ```init.py``` and ```main.py``` exist inside ```tvc_hud/```.

# 3) What you’ll see (GUI)

<img width="1176" height="623" alt="image" src="https://github.com/user-attachments/assets/89f0df71-4364-4e1d-8933-c8730483fb4b" />


Four panels (P1-C1, P1-C2, P2-C1, P2-C2)

```HP: cur/max (percent)``` — colored Green/Yellow/Red by health

```Meter``` — shown only for C1 (shared per team)

```Pos: X/Y``` — world coordinates (float)

```LastDmg``` — the latest damage chunk seen on that victim

Recent Hits (scrolling log)

Timestamp, victim, damage, ```hp from -> to```, inferred attacker, and rough distance²

# 4) Memory map (US build)
Slot pointers (static, point to fighter/manager -> may require one-hop indirection)

```
803C9FCC PTR_P1_CHAR1
803C9FDC PTR_P1_CHAR2
803C9FD4 PTR_P2_CHAR1
803C9FE4 PTR_P2_CHAR2
```

Fighter structure offsets (relative to resolved ```base```)

```
+0x14 OFF_CHAR_ID (u32) Character ID
+0x24 OFF_MAX_HP (s32) Max HP (10k–60k)
+0x28 OFF_CUR_HP (s32) Current HP
+0x2C OFF_AUX_HP (s32) “Red life” / aux bar (0..max) – used for validation only
+0x40 OFF_LAST_HIT (s32) Last damage chunk RECEIVED (victim-side). Resets/overwrites per hit.
+0x4C METER_PRIMARY (s32) Super meter (shared per team via C1)
+0xF0 POS_X (f32) Position X
+0xF4* POS_Y candidate (f32) Position Y (auto-picked from neighbors)
```

Meter (mirrored):
```
base + 0x4C (primary)
base + 0x9380 + 0x4C (secondary mirror in another bank)
```

Position Y candidates
```0xF4, 0xEC, 0xE8, 0xF8, 0xFC``` — the app samples briefly and picks the lowest-variance stream.

RAM sanity:
```
MEM1: 0x80000000..0x817FFFFF
MEM2: 0x90000000..0x93FFFFFF
BAD_PTRS: { 0x00000000, 0x80520000 }
```

# 5) Character IDs (known)

```
1 Ken the Eagle
2 Casshan
3 Tekkaman
4 Polimar
5 Yatterman-1
6 Doronjo
7 Ippatsuman
8 Jun the Swan
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

Add more in ```constants.py``` (```CHAR_NAMES```) as you discover them.

# 6) How it infers “who hit whom”

We read ```OFF_LAST_HIT``` on each fighter (victim-side). When it changes, or when ```CUR_HP``` drops, we register a HIT.
The attacker is approximated as the nearest opponent (distance²) at that moment. This is heuristic, but works well with “Hit Anywhere” testing.

Optional Gecko for easy collision testing

```
Hit Anywhere (Both Players) – by nolberto82
0404FC88 60000000
0407EE84 60000000
```

The Gecko is not required for the HUD; it just makes generating collisions trivial while mapping memory.

# 7) Project layout

```
tvc_hud/
init.py
main.py # python -m tvc_hud
main.py # boot the Tk HUD
ui_tk.py # Tkinter UI components
poller.py # background thread: pointer resolve, reads, hit inference
memory.py # safe reads, validators, pointer helpers
constants.py # addresses, offsets, char map, tunables
game.py # (optional) future game/logic helpers
```

Run via ```python -m tvc_hud``` or ```python -m tvc_hud.main```.

# 8) Troubleshooting

“No module named tvc_hud”
Run the command from the parent folder of ```tvc_hud```. Verify ```init.py``` exists.

“No module named tvc_hud.main”
Ensure ```tvc_hud/main.py``` exists (not ```main.py.txt```). Then ```python -m tvc_hud```.

GUI opens but shows “(waiting)”
Start a match/training so the slots are populated. Some pointers are transient on menus.

OneDrive/Explorer saved wrong
Turn on “File name extensions” and confirm the filenames are correct.

Dolphin not hooked
Make sure Dolphin is running, the game is booted, and ```dolphin-memory-engine``` is installed in the same venv.

Wrong region/revision
If your build isn’t US, slot pointers may differ. Update ```constants.py```.

Character not loading
There are two instances, the first one is for giants, as of now 10/12/2025 Giants are not supported, I'll have to look into that. The other is that sometimes....it just doesn't work. There may be some pointer reference shenanigans. Start a new match, that will kick it back into place, or restart the instance of TvC all over again, if it's still not working, restart the program itself, usually at that point it starts working

# 9) Notes & limits

```OFF_LAST_HIT``` is victim-side and not guaranteed to persist long; read frequently.

Meter is shown on C1 only because TvC shares meter per team.

Attacker detection is heuristic (nearest opponent). For frame-perfect accuracy, you’d need to locate the engine’s attacker pointer in the hit event or hitbox record.

10) License

Use, modify, and share freely within the community. If you publish derivatives, please credit the original memory map and this HUD.

11) Credits

Memory reads via ```dolphin-memory-engine```

“Hit Anywhere” Gecko Code Reference: nolberto82

HP Gecko Code reference:  lee4

And of course the hard work of Jaaaaaames who found a bunch of information previously

TL;DR

```bat
git clone <this repo>
cd <repo root>
python -m venv .venv
..venv\Scripts\activate
pip install -r requirements.txt
python -m tvc_hud
```

Start a match in Dolphin → watch the HUD update.
