# Ruler source-slot filters

The saved-profile ruler now has its own source filters in `hitbox_filter.json`:

```json
{
  "show_range_ruler": true,
  "ruler_slots": {"P1": true, "P2": true, "P3": true, "P4": true}
}
```

`hitbox_slots` governs only live hitbox/projection drawing. It no longer determines who can arm, retain, or display a saved ground-normal ruler. A ruler source still needs: a rendered fighter, a currently active grounded normal with a saved profile, global Ruler ON, and Hurtboxes ON for the live body/reach test. Air normals remain intentionally excluded.

Profiles remain read-only.
