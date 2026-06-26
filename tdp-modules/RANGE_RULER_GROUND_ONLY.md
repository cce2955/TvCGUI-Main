# Range ruler: ground normals only

The persistent range ruler now ignores all saved frame-data rows explicitly labelled as jumping/air normals (for example `j.A`, `j.B`, `j.C`).

When one is detected, the renderer clears only its in-memory retained ruler for that fighter. It does not load, alter, resample, calibrate, or write the profile JSON. A later grounded normal with an existing saved profile restores the ruler normally.

`hitbox_range_profiles.json` remains read-only during match play.
