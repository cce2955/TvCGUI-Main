# Extra Characters button-integrated MDL0 TEX0 pointer quicktry

This build wires the narrow MDL0 thumbnail texture-pointer quicktry directly into the normal **Extra Characters** toggle.

What the button now does when ON:

- keeps the native extra Yami slots/profile rows active
- keeps the three donor profile rows active
- applies the safe MDL0 material TEX0 pointer patch:
  - B27 / slot 0x1B -> icon_fra
  - B28 / slot 0x1C -> icon_tkb
  - B29 / slot 0x1D -> icon_ya2

What it still does **not** do:

- no 0x60 carousel row copy
- no thumbnail row string aliasing
- no live wheel object pointer aliasing

The standalone BAT quicktry files are still included for diagnostics, but normal testing should use only the GUI Extra Characters button.
