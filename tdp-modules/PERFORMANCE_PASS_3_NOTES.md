# Performance Pass 3 - Assist Persistence V2

This pass targets the remaining `assist_persist` 25-35 ms spikes.

## Main change

`main.py` no longer re-applies selected quick assists at idle.

The quick-assist click still writes immediately and records the selected assist.
After that, the assist backend owns persistence and patches the selected slot only when that slot enters an assist-ish state.

## Why

The old periodic idle re-apply path called the full quick-assist write path every N frames. Even when it was quiet and cached, it could cost 25-35 ms and show up as repeated `assist_persist` spikes.

## New behavior

- `assist_persist` now only does cheap selected-slot validation every 15 frames.
- It clears a selected quick assist if the character in that visible slot changes.
- It does not call `apply_quick_assist_from_main()` from the frame loop anymore.
- The backend patches on assist standby/jump-in/attack for all selected slots, not just mirror matches.
- Mirror matches still use the slot-specific runtime path.
- If a selected slot reloads/changes, its route is prewarmed in the background.

## Expected logs

Normal idle gameplay should no longer show repeated:

```text
[perf] assist_persist 25ms-35ms
```

Assist runtime logs may still appear when an assist is actually called:

```text
[assist quick] runtime P1-C2 -> ...
```

That is expected.
