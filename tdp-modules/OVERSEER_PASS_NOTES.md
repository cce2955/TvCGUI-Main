# Overseer Pass

Added a Runtime Overseer window and reset controls.

## New button

The top dock now has an `Overseer` button.

## Overseer shows

- Dolphin/runtime heartbeat
- Megacrash enabled/chance/pending pulse counts
- HUD hold state for P1/P2
- Per-slot assist selection, route type, route block, assist-window status, and last runtime write age
- Last reported perf bucket timings

## Safe Restore

Safe Restore does not add new gameplay behavior. It only tries to put persistent systems back into a safe inactive state:

- writes selected assist routes back to default when a cached route is available
- clears quick-assist runtime selections/latches
- releases HUD visible-win holds
- turns Megacrash off
- clears Megacrash pending pulses/scheduled triggers/cooldown

## Hard Reset

Hard Reset runs Safe Restore, then clears assist route caches and UI latches. Use this when stale state seems stuck across rounds.

## Dump

Dump writes `debug_dumps/overseer_state_YYYYMMDD_HHMMSS.json` for bug reports.
