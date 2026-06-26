# Range Ruler Root-Anchor Fix

## What changed

The retained range ruler no longer draws from a captured move-start position.
It always begins at the fighter's **current live model/root position**.

Attack profiling still measures the farthest active hitbox edge from the
fighter root at the start of the action, so advancing normals retain their own
forward travel in the learned reach.

## Profile reset

`hitbox_range_profiles.json` is now schema 5. Schema-4 attack entries used a
pose-dependent hurtbox center and can contain stale-anchor reach values. They
are intentionally discarded and must be learned again. Learned body envelopes
are preserved.

## Per-move audit fields

Each learned attack now stores:

- `anchor`: always `fighter_root`
- `reach_from_start`: farthest hitbox edge from the action-start root
- `tip_center_from_start`: hitbox center from that same start root
- `advance_at_tip`: fighter-root travel at the furthest sample
- `tip_center_from_active_root`: the hitbox's own extension beyond the root
  at that active frame
- `last_sample.start_root` and `last_sample.active_root`: the exact roots used
  by the latest valid sample

Values with more than `2.25u` of root travel are rejected as invalid for this
normal-range profiler, which prevents a stale action capture from becoming a
permanent reach record.

## Expected on-screen behavior

After a normal ends, the range band should stay attached to the fighter's
current position. It must not stretch back toward where the move began.
The label uses `RANGE` for learned start-to-tip reach and `MOVE` for the
forward root travel included in that reach.
