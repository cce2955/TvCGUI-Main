# TvCGUI_onefile.spec
# -----------------------------------------------------------------------
# PyInstaller spec that produces ONE TvCGUI.exe.
#
# Data files are optional on purpose: local dev copies sometimes do not
# have generated/debug files such as fd_region_hits.txt yet.  Missing
# optional files should not break the EXE build.
# -----------------------------------------------------------------------

from pathlib import Path
from glob import glob

block_cipher = None


def _data_file(src, dst='.'):
    if Path(src).exists():
        return [(src, dst)]
    print(f'[spec] optional data missing, skipping: {src}')
    return []


def _data_glob(pattern, dst='.'):
    matches = sorted(glob(pattern))
    if not matches:
        print(f'[spec] optional data glob empty, skipping: {pattern}')
    return [(m, dst) for m in matches]


def _data_dir(src, dst):
    if Path(src).is_dir():
        return [(src, dst)]
    print(f'[spec] optional data dir missing, skipping: {src}')
    return []


datas = []
datas += _data_dir('assets', 'assets')
datas += _data_glob('*.csv', '.')
datas += _data_file('fd_region_hits.txt', '.')
datas += _data_file('frame_data_profiles.json', '.')
datas += _data_file('quick_assists.json', '.')
datas += _data_file('projectilemap.json', '.')
datas += _data_file('projectile_ids.json', '.')
datas += _data_file('master_overlay_control.json', '.')
# megacrash_trainer.json is intentionally not bundled.
# Megacrash must default OFF for every exported build; runtime saves stay local.
datas += _data_dir('missions', 'missions')

a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=[
        # All three scripts get pulled in as imports by launcher.py,
        # but list their deps explicitly so PyInstaller doesn't miss anything.
        'main',
        'master_overlay',
        'hud_overlay',

        # Common deps
        'pyperclip',
        'pygame',
        'pygame.font',
        'pygame.image',
        'pygame.display',
        'pygame.event',
        'pygame.draw',
        'pygame.surface',
        'pygame.time',
        'pygame.transform',

        # project modules (add any that PyInstaller misses at runtime)
        'constants',
        'config',
        'layout',
        'scan_worker',
        'training_flags',
        'debug_panel',
        'dolphin_io',
        'portraits',
        'resolver',
        'meter',
        'fighter',
        'advantage',
        'moves',
        'move_id_map',
        'hud_draw',
        'redscan',
        'global_redscan',
        'events',
        'frame_data_window',
        'fd_window',
        'fd_tree',
        'fd_move_families',
        'fd_editors',
        'fd_widgets',
        'fd_utils',
        'fd_patterns',
        'fd_format',
        'fd_dialogs',
        'fd_write_helpers',
        'fd_patch_runtime',
        'fd_projectile_integration',
        'proj_scanner_window',
        'mission_mode',
        'subprocess_compat',
        'assist_scanner_window',
        'assist_scanner_backend',
        'tk_host',

        # stdlib
        'csv',
        'json',
        'subprocess',
        'ctypes',
        'ctypes.wintypes',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='TvCGUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,              # set True if UPX installed, reduces EXE size ~30%
    upx_exclude=[],
    runtime_tmpdir=None,    # extracts to %TEMP%\TvCGUI_<hash>\ at launch
    console=True,           # keep True for print() debug; False hides terminal
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,  # swap for a .ico if you have one
)
