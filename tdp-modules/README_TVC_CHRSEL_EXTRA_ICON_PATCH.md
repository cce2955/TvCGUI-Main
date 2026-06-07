# TvC extra character-select icon patch

This is the BRRES/material-row pass, not another `icon_*` string rename pass.

It patches the appended bottom-wheel thumbnail rows:

| Extra slot | Target row | Target address | Donor row | Donor address | Intended icon |
|---:|---|---:|---|---:|---|
| `0x1B` | `thumbnail_0622_B27` | `0x9083BDC8` | `thumbnail_0622_B15` | `0x9083B948` | Frank |
| `0x1C` | `thumbnail_0622_B28` | `0x9083BE28` | `thumbnail_0622_B10` | `0x9083B768` | Tekkaman Blade |
| `0x1D` | `thumbnail_0622_B29` | `0x9083BE88` | `thumbnail_0622_B12` | `0x9083B828` | Yatterman-2 |

Important: `B15`, `B10`, `B12`, `B27`, `B28`, and `B29` are decimal resource suffixes. Example: `B27` is row index `27 decimal`, which is `0x1B`.

## Install

Copy these two files into your `TvCGUI-Main` folder:

- `tvc_chrsel_extra_icon_material_patch.py`
- `run_brres_extra_icons_patch.bat`

## Use

Recommended clean test:

1. Start Dolphin and hook your normal tool stack.
2. Extra chars OFF once.
3. Leave character select.
4. Extra chars ON before entering character select.
5. Run `run_brres_extra_icons_patch.bat`.
6. Enter character select.
7. Check the Yami extra slots from both left and right cursor movement.

Manual command:

```bat
.venv\Scripts\python.exe tvc_chrsel_extra_icon_material_patch.py --apply
```

Audit only:

```bat
.venv\Scripts\python.exe tvc_chrsel_extra_icon_material_patch.py --audit
```

Dry run:

```bat
.venv\Scripts\python.exe tvc_chrsel_extra_icon_material_patch.py --apply --dry-run
```

## Backup / restore

Every write creates a backup folder:

```text
chrsel_icon_patch_backups\YYYYMMDD_HHMMSS\
```

Restore example:

```bat
.venv\Scripts\python.exe tvc_chrsel_extra_icon_material_patch.py --restore chrsel_icon_patch_backups\20260605_123456
```

## Advanced fallback

Default copies the full `0x60` row. If full-row copy visually works but breaks row identity/cursor logic, try preserving the row header and only copying payload bytes:

```bat
.venv\Scripts\python.exe tvc_chrsel_extra_icon_material_patch.py --apply --copy-offset 0x10
```

or:

```bat
.venv\Scripts\python.exe tvc_chrsel_extra_icon_material_patch.py --apply --copy-offset 0x20
```

## What this does not do

It does **not** rename `icon_fra`, `icon_tkb`, `icon_ya2`, or `icon_none` strings. Previous attempts failed because the visible face was already resolved through the material/row binding. This copies the donor thumbnail material row payload into the appended Yami rows instead.
