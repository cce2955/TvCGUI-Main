# Normals Preview Top Padding

## Change

Added a fixed 3 px body gap between the preview metric header and the first normal row.

## Why

On compact cards, the first row's top/border treatment visually collided with the `GND / S / A / R / +H / +B / D` header and looked clipped.

## Layout rule

The gap is reserved from the data body height, so it cannot push the last row outside the grid. Empty-state cards use the same body inset.

## Data safety

No JSON/profile data changed.
