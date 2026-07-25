TvCGUI Mission Point Owner + Hit Confirmation Fix

CHANGES
1. Mission Mode pins the point fighter selected when Mission Mode is enabled.
   Assist calls may temporarily perturb live team slots, but the mission pack,
   character name, and route evaluation remain assigned to the pinned fighter.
2. Native actions from the assist character are ignored for the point route.
3. Prediction is visual tracking only. It never increments completed steps.
4. A normal/special/super step requires a new hit owned by that exact native
   action while the defender is in actual hitstun (or Megacrash where intended).
5. An earlier hit can no longer be donated to a later whiffed move. For example,
   Ken 5C cannot confirm Phoenix unless Phoenix itself connects.

APPLY
Extract this archive over the repository's existing tdp-modules directory and
allow these files to overwrite:
- tvcgui/features/training/mission_manager.py
- tvcgui/features/overlay/master_renderer.py

The two included tests are optional but recommended.

VALIDATION
28 focused mission contracts passed, including point identity, assist rejection,
stale-hit rejection, whiff rejection, and valid Phoenix hit confirmation.

RUN
Use run.bat for the source build. Rebuild the EXE to include this source change.
