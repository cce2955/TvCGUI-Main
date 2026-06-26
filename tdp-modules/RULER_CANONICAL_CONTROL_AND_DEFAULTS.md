# Ruler control and automatic learning

- The main command dock is the sole ruler control surface.
- The legend does not write `show_range_ruler`; it only says where the control lives.
- `Ruler: ON` enables saved grounded-normal reach guides for the enabled raw source slots.
- A known move uses its existing profile immediately and never reads live hitbox geometry for the ruler.
- A missing standard grounded normal is captured once, written into `hitbox_range_profiles.json`, and becomes profile-only on later uses.
- Air normals remain excluded.
- The full Frame Data/MEM2 normal scanner is never launched during this process.

The build preserves the JSON it ships with. During play, the program may add missing move entries and update the existing calibration block.
