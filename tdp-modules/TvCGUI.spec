# -*- mode: python ; coding: utf-8 -*-


def _data(src, dst):
    return (src, dst) if __import__("pathlib").Path(src).exists() else None


a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[x for x in [
        _data('data/animation/animation_frames.json', 'data/animation'),
        _data('data/animation/character_fpk_registry.json', 'data/animation'),
        _data('data/assists/quick_assists.json', 'data/assists'),
        _data('data/combat/projectilemap.json', 'data/combat'),
        _data('data/combat/projectile_ids.json', 'data/combat'),
        _data('data/combat/move_id_map_charagnostic.csv', 'data/combat'),
        _data('data/frame_data/frame_data_profiles.json', 'data/frame_data'),
        _data('data/frame_data/frame_data_preview_profiles.json', 'data/frame_data'),
        _data('data/hitboxes/hitbox_range_profiles.json', 'data/hitboxes'),
        ('missions', 'missions') if __import__('pathlib').Path('missions').is_dir() else None,
        # Mutable runtime state is intentionally not bundled.
    ] if x],
    hiddenimports=['tvcgui.platform.dolphin', 'tvcgui.platform.patch_manager', 'tvcgui.ui.debug_panel', 'tvcgui.ui.portraits', 'tvcgui.ui.overseer', 'tvcgui.ui.main_window', 'tvcgui.features.training.timer_debug', 'tvcgui.tools.scanners.normal_scanner', 'tvcgui.tools.scanners.bone_scanner', 'tvcgui.tools.scanners.special_runtime_finder', 'tvcgui.features.frame_data.move_families', 'tvcgui.features.frame_data.projectile_integration', 'tvcgui.features.combat.projectile_scanner', 'tvcgui.features.training.flags', 'tvcgui.features.training.mission_manager', 'tvcgui.features.training.mission_mode', 'tvcgui.features.training.megacrash_window', 'tvcgui.features.training.win_counter_gate', 'tvcgui.features.training.win_counter_window', 'tvcgui.features.training.stun_profiler', 'tvcgui.features.overlay.master_renderer', 'tvcgui.features.overlay.hud_renderer', 'tvcgui.features.hitboxes.renderer'],
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
