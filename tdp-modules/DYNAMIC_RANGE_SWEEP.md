# Dynamic Range Sweep

The existing ruler remains a single maximum forward hitbox tip.

`Dynamic` is an optional second layer that records the complete active-window coverage of a move: each active frame stores the fighter root displacement from action start and every hitbox local to that moving root. The display reconstructs this trajectory from the fighter's current root, so it can show rising/falling/advancing/multi-hit shapes without trying to bind to live skeletal descriptor identities.

## Learning rules

* A missing move profile learns static ruler + dynamic sweep in the same one-time active-window capture.
* A pre-existing static profile is not rescanned normally. Turning on Dynamic permits one supplemental capture only when that entry lacks `dynamic_sweep`.
* The supplement never alters `reach_from_start`, tip radius/position, calibration pips, or other single-ruler fields.
* Once `dynamic_sweep` exists, no more attack descriptors are read for that move.

`dynamic_sweep` is stored as `root` travel plus per-frame local hitbox circles, not as stale world coordinates.
