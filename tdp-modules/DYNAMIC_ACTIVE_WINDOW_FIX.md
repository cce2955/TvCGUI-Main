# Dynamic sweep: active-window truth fix

## Symptom

Dynamic sweep captured startup circles for normals such as Ryu 2A, 5B, and 6C.
The overlay then labeled those stored circles as active even before the visible
strike was active.

## Cause

The compact frame-gate builder treated raw `active2_start` / `active2_end`
fields as a second active window for every move. Those fields are not a
reliable second-hit source. On many ordinary moves they contain a setup or
neighboring timing value:

- Ryu 2A: primary 6-8, misleading raw `active2` 1-4
- Ryu 5B: primary 8-10, misleading raw `active2` 2-4
- Ryu 6C: primary 16-18, misleading raw `active2` 4

That exactly explains the early Dynamic samples.

## Fix

The overlay now uses:

1. the move's primary `active_start` / `active_end` window; and
2. explicit `hit_segments[*].active_start` / `active_end` windows for genuine
   multi-hits.

It deliberately ignores raw `active2_*` in the live frame gate.

## Result

- Dynamic sweep samples only the primary/validated hit-segment active phases.
- Ryu 2A no longer samples 1-4 startup.
- Ryu 5B no longer samples 2-4 startup.
- Ryu 6C no longer samples frame 4 startup.
- Legitimate multi-hit phases such as Ryu 6B's later 20-21 segment remain
  available.

## Existing learned dynamic sweeps

Dynamic sweep payloads now use version 2. Version-1 sweep data is treated as
obsolete automatically: the static ruler geometry is retained, while Dynamic
will capture and replace only the supplemental `dynamic_sweep` payload the next
time that move is performed with Dynamic enabled. No manual JSON editing is
needed.
