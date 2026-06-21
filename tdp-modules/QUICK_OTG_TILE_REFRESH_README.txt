QUICK OTG + TILE READOUT V1

- Adds an OTG tile to the compact selection strip for normal move rows.
  * Shows Off, On, On + reaction, a custom raw value, or Not profiled.
  * It is cache-only: clicking/selecting a move still does not scan Dolphin.
  * Clicking the OTG tile opens the existing edit/on-demand resolve route.

- Reworks compact selection values from outlined input-looking rectangles into
  stat tiles with a small color rail, muted label, and larger display value.
  Editable values keep their current click-to-edit behavior.

- Highlighting uses low-key surfaces:
  * blue = important values
  * teal = OTG on / OTG + reaction
  * steel = OTG off
  * muted = not profiled

No profiling, Dolphin reads, or selection-time scans were added.
