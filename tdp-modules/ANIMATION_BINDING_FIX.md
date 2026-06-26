# Animation Binding Resolver

The animation editor resolves phase-bound animation commands before direct
legacy commands.

Phase-bound records use this layout:

```text
04 01 02 3F 00 00 [anim_hi anim_lo] 01 3C
```

The writable animation word begins six bytes after the phase-record header.
This target is selected before the direct `01 XX 01 3C` command because the
phase record drives action playback.

The resolver keeps the phase command as the stable target after an animation
replacement changes its value.
