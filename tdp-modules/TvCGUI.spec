# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[x for x in [
        ('projectilemap.json', '.') if __import__('pathlib').Path('projectilemap.json').exists() else None,
        ('projectile_ids.json', '.') if __import__('pathlib').Path('projectile_ids.json').exists() else None,
        ('frame_data_profiles.json', '.') if __import__('pathlib').Path('frame_data_profiles.json').exists() else None,
        ('missions', 'missions') if __import__('pathlib').Path('missions').is_dir() else None,
        # megacrash_trainer.json is intentionally not bundled.
        # Megacrash must default OFF for every exported build; runtime saves stay local.
    ] if x],
    hiddenimports=['fd_move_families', 'fd_projectile_integration', 'proj_scanner_window'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TvCGUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TvCGUI',
)
