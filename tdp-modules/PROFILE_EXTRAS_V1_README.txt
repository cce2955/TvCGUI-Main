TvC Mapping — Saved Projectile + Specials Profiles
===================================================

What changed
------------
The Frame Data workbench now keeps projectile and special/action discovery in
the same per-character frame-data profile as normals.  Those discovery passes
are explicit: changing to the Projectiles or Supers view never starts a broad
scan.

Workflow
--------
1. Load a match with the character you want to profile.
2. Open that character's Frame Data workbench.
3. Click Build full profile.
   - This runs the projectile pass, then the specials/action pass.
   - Each completed pass is written immediately to frame_data_profiles.json.
4. On later openings, the workbench loads the saved projectile/special rows
   from that character profile.  Use Projectile pass or Specials pass only
   when you want to rebuild one section.

Notes
-----
- An empty completed pass is still saved.  That distinguishes “this character
  has no discovered rows” from “this character has not been profiled yet.”
- Cached address fields are stored relative to the live chr_tbl and rebased on
  the next match allocation, matching the existing normal-move profile logic.
- Rebuilding normal moves keeps completed projectile/special sections when the
  character table signature still matches.
- The projectiles/specials views are display-only.  They do not scan.
- Delete the character entry (or all) from frame_data_profiles.json to force a
  fresh profile from scratch.
