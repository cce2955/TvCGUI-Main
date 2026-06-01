# Performance pass

This build keeps the current HUD editor, mirror assist behavior, and Megacrash safety fixes, but removes several stutter sources.

Changes:

- Added rate-limited performance logs in main.py for assist tick, assist persistence, mission tick, Megacrash tick, HUD editor tick, and total frame work.
- Bumped persistent quick-assist idle reapply from every 10 frames to every 30 frames. Assist-entry runtime patching still handles the actual assist-call window.
- HUD Editor persistent win/score/stage/timer writes are now dirty writes. The code reads the live value first and skips writing if the value is already correct.
- HUD Editor freeze loop now runs every 150 ms instead of 100 ms, with dirty writes doing the real protection.
- Assist mirror runtime writes are throttled per slot while still writing during the assist window.
- Runtime assist route refresh no longer performs synchronous route scans from the frame loop. It uses warmed cache rows or schedules background prewarm.
- Assist runtime error/log spam is rate-limited.

Perf logs are on by default only when a section spikes. To silence them:

    set TVC_PERF_LOG=0

To tune warning thresholds:

    set TVC_PERF_SECTION_WARN_MS=12
    set TVC_PERF_FRAME_WARN_MS=45
