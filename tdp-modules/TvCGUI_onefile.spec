# TvCGUI_onefile.spec
# -----------------------------------------------------------------------
# PyInstaller spec that produces ONE TvCGUI.exe.
#
# The EXE re-launches itself with --mode <n> for subprocesses.
# No sibling EXEs needed. You ship just TvCGUI.exe.
#
# Usage:
#   pyinstaller TvCGUI_onefile.spec
#
# Output: dist\TvCGUI.exe
# -----------------------------------------------------------------------

block_cipher = None

a = Analysis(
    ['launcher.py'],        # <-- single entry point, routes to main/overlays
    pathex=['.'],
    binaries=[],
datas=[
    ('assets',  'assets'),      # portraits, icons — bundled inside EXE
    ('*.csv',   '.'),           # move-mapping CSVs
    ('quick_assists.json', '.'),
    ('master_overlay_control.json', '.'),
    ('missions', 'missions'),
],
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
