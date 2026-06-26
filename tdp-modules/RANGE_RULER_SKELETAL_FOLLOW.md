# Range ruler — skeletal-follow tracking

## Why this exists

Root-only profiles stored a move's full start-to-tip reach, including any forward translation. The old renderer then placed that full reach from the fighter's *current* root even while the fighter was already moving. Advancing normals could therefore look too long during active frames.

## Runtime behavior

- **Known profile:** no hitbox descriptors are scanned.
- **Missing grounded normal:** the existing one-move collector still reads only that move's active hitbox descriptors, then saves a new profile.
- **Every profile:** uses the already-cached hurtbox bone matrices to select a stable pelvis descriptor when available.

### During an active normal

The ruler uses:

```text
current pelvis anchor -> saved tip relative to the active pelvis anchor
```

The move's forward travel is therefore represented by the live skeleton position once, not added again as a full start-to-tip offset.

### After recovery / while idle

The ruler becomes a repeatable training guide:

```text
current pelvis anchor -> saved maximum tip from move-start pelvis anchor
```

This answers “what will this move reach if I perform it again from here?”

## Existing entries

Existing root-only records are not hitbox-rescanned. The first time one is used, the tool performs a one-action **skeletal attachment**:

1. Capture pelvis descriptor/root at action start.
2. During active frames, choose the skeleton sample whose root advance most closely matches the profile's existing `advance_at_tip`.
3. Write only pelvis-relative anchor fields.

The saved reach, radius, calibration pips, and root geometry stay intact. Console line:

```text
[range profile] attached pelvis motion anchor to <character:move>; no hitbox rescan; reason=recovery
```

## New JSON fields

New or upgraded attacks may include:

```json
"motion_anchor": {
  "kind": "pelvis_hurtbox",
  "descriptor_index": 5,
  "region": "pelvis"
},
"reach_from_start_anchor": 1.42,
"tip_center_from_start_anchor": 1.18,
"tip_center_from_active_anchor": 0.63,
"tip_y_from_start_anchor": 0.57,
"tip_y_from_active_anchor": 0.43,
"skeleton_advance_at_tip": 0.55
```

The top-level JSON remains schema 5 for compatibility with the safe exporter. Individual enriched entries report `"profile_schema": 6`.

## Visible audit labels

- `SKEL ACTIVE`: live rig-following phase.
- `SKEL SET`: retained repeatable range guide.
- `ROOT ACTIVE` / `ROOT SET`: legacy fallback if a valid pelvis descriptor was unavailable.
