# TvC chrsel MDL0 TEX0 pointer quicktry

This is the narrow test after the 0x60 row-copy crash.

It does **not** touch:

- chrsel.seq 0x60 wheel rows
- thumbnail strings
- live wheel object pointers
- navigation/cursor fields
- material names

It only writes the resolved absolute TEX0 pointer at:

```text
MDL0 material + 0x420
```

Targets:

```text
B27 / slot 0x1B -> icon_fra
B28 / slot 0x1C -> icon_tkb
B29 / slot 0x1D -> icon_ya2
```

Run while character select is already visible, after Extra Characters are ON.
Use `run_chrsel_mdl0_texptr_quicktry_RESTORE.bat` to put the original pointers back, or just restart Dolphin.
