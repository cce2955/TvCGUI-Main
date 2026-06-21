Inspector scroll + headline edit fix

Fixes two UX regressions from the overhaul:
- The right-hand inspector scrollbar now remains active over nested labels/buttons/chips. It no longer installs/removes global mouse-wheel bindings as the pointer crosses child controls.
- The visible Startup, Active, Hitstop, and Blockstun headline values are now direct editable click targets (also keyboard accessible via Enter/Space).
- The inspector scrollbar thumb has a higher-contrast style, and Page Up/Down/Home/End work once the inspector canvas has focus.

No new scans or Dolphin reads are triggered by scrolling or selecting headline values.
