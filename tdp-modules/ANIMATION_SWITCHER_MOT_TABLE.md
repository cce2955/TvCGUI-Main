# Animation Switcher: MOT Table Path

Animation-only replacement writes the selected source action's entry in the
loaded `chr/<character>/0000.mot` pointer table.

- SEQ action IDs and commands remain unchanged.
- Hitbox, cancel, damage, and command-flow data remain owned by the source
  action.
- The source action's rendered clip changes to the selected target clip.
- The resolver validates the loaded MOT header, table count, table address,
  uncompressed size, and clip offsets before writing.

For Ryu in the validation capture:

```text
source action: 0x0100 (5A)
source slot:   0x926FE330
source clip:   0x9279C990
target action: 0x0108 (3C)
target clip:   0x92784510
```

The write target is the MOT table slot, not an `01 xx 01 3C` SEQ packet.
