SELECTION FAST PATH V1

What changed
- Frame-data selection is cache-only. Clicking 5A -> 5B does not start optional
  Dolphin pattern probes or a lazy scan.
- Tree selection paints first; sidebar/detail rendering is deferred to Tk's next
  idle turn. Rapid navigation drops stale intermediate detail renders.
- The compact quick-stat strip uses 30 persistent slot widgets. Rows now update
  their labels/values instead of deleting and recreating 20-30 chip cards.
- Normal -> normal selection keeps the existing inspector layout in place rather
  than pack_forget/pack-ing every inspector card again.
- Inspector values, edit button state, and chip styles update only when a cached
  value/state actually changes.
- Explicit Refresh visible remains the route for filling optional loose script
  fields (Hit FX/reach/post link/OTG/etc.). Direct edits can still resolve a
  required missing signature as before.
