# Extra Characters 0300 face-resource path build

This build backs out the active visual-scratch patch because it targets the large selected portrait bank, not the bottom carousel face.

Extra Characters ON now also patches only the Yami select-screen `0300.brres` face-resource paths:

- `chr/tk1/0300.brres` -> `chr/fra/0300.brres`
- `chr/tk2/0300.brres` -> `chr/tkb/0300.brres`
- `chr/tk3/0300.brres` -> `chr/ya2/0300.brres`

It does not patch Yami gameplay/body resources `0000/0100/0200.brres`.
It does not copy 0x60 carousel rows.
It does not patch the active scratch bank at `0x90818460`, which was affecting the large portrait.

Use from a clean Dolphin restart. Turn Extra Characters ON before testing the appended slots.
