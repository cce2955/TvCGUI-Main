# Ruler profile truth

The source-slot chips (`1 2 | 3 4`) are filters only. They do not create a reach profile.

A visible ruler requires all of these:

1. The fighter is on stage (assist standby slots do not draw).
2. The source chip is enabled.
3. The fighter performs a grounded normal.
4. That exact character + move exists in `hitbox_range_profiles.json`.

When condition 4 is missing, the first use of a standard grounded normal (`A/B/C`, `2A`, `5B`, `6C`, etc.) starts a small **move-local** collector. It reads that one fighter's three hitbox descriptors only during the normal's labelled active frames, keeps the farthest valid sample, then writes one new schema-5 entry at recovery.

After that, the move is treated exactly like every other known profile: it renders directly and does not live-scan again. Air normals and missing specials/projectiles/supers are not auto-created.

No idle 5A fallback is drawn. This avoids making one profiled fighter look like the only enabled ruler source.

Visible ruler labels use `Character Name (raw slot)`, e.g. `Ryu (P3)`, to make the raw memory-slot mapping explicit.
