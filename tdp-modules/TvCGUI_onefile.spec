# TvCGUI_onefile.spec
# -----------------------------------------------------------------------
# PyInstaller spec that produces ONE TvCGUI.exe.
#
# Optional binary/JSON/CSV resources only; build-note text files are excluded.
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
datas += _data_file('data/animation/animation_frames.json', 'data/animation')
datas += _data_file('data/animation/character_fpk_registry.json', 'data/animation')
datas += _data_file('data/combat/move_id_map_charagnostic.csv', 'data/combat')
datas += _data_file('data/frame_data/frame_data_profiles.json', 'data/frame_data')
datas += _data_file('data/frame_data/frame_data_preview_profiles.json', 'data/frame_data')
datas += _data_file('data/templates/TvC_Frame_Data_Observed_template.csv', 'data/templates')
# Seed only: hitboxesscaling.py copies this beside TvCGUI.exe on first run
# and writes all auto-learned profile data to that persistent copy.
datas += _data_file('data/hitboxes/hitbox_range_profiles.json', 'data/hitboxes')
datas += _data_file('data/assists/quick_assists.json', 'data/assists')
datas += _data_file('data/combat/projectilemap.json', 'data/combat')
datas += _data_file('data/combat/projectile_ids.json', 'data/combat')
# Mutable master-overlay control is intentionally not bundled.
# megacrash_trainer.json is intentionally not bundled.
# Megacrash must default OFF for every exported build; runtime saves stay local.
datas += _data_dir('missions', 'missions')

a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=['tvcgui.tools.scanners.normal_scanner', 'tvcgui.tools.scanners.bone_scanner', 'tvcgui.tools.scanners.special_runtime_finder', 
        # All three scripts get pulled in as imports by launcher.py,
        # but list their deps explicitly so PyInstaller doesn't miss anything.
        'main',
        'tvcgui.features.overlay.master_renderer',
        'tvcgui.features.overlay.hud_renderer',
        'tvcgui.features.overlay.hud_renderer',
        'tvcgui.features.overlay.master_renderer',
        'tvcgui.features.overlay.drawing',
        'tvcgui.features.overlay.editor',
        'tvcgui.features.overlay.manager',
        'tvcgui.features.hitboxes.renderer',

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
        'tvcgui.core.constants',
        'tvcgui.core.config',
        'tvcgui.core.layout',
        'tvcgui.tools.scanners.normal_scan_worker',
        'tvcgui.features.training.flags',
        'tvcgui.features.training.mission_manager',
        'tvcgui.features.training.megacrash_window',
        'tvcgui.features.training.win_counter_gate',
        'tvcgui.features.training.win_counter_window',
        'tvcgui.features.training.stun_profiler',
        'tvcgui.platform.dolphin',
        'tvcgui.platform.patch_manager',
        'tvcgui.ui.debug_panel',
        'tvcgui.ui.portraits',
        'tvcgui.ui.overseer',
        'tvcgui.ui.main_window',
        'tvcgui.features.training.timer_debug',
        'tvcgui.ui.debug_panel',
        'tvcgui.platform.dolphin',
        'tvcgui.ui.portraits',
        'tvcgui.tools.scanners.fighter_resolver',
        'tvcgui.features.combat.meter',
        'tvcgui.tools.scanners.fighter_state',
        'tvcgui.features.combat.advantage',
        'tvcgui.features.combat.moves',
        'tvcgui.features.combat.move_id_map',
        'tvcgui.features.overlay.drawing',
        'tvcgui.tools.scanners.red_health_scanner',
        'tvcgui.tools.scanners.global_red_health_scanner',
        'tvcgui.core.events',
        'tvcgui.features.frame_data.window',
        'tvcgui.features.frame_data.workbench',
        'tvcgui.features.frame_data.tree',
        'tvcgui.features.frame_data.move_families',
        'tvcgui.features.frame_data.spreadsheet_export',
        'tvcgui.features.frame_data.editors',
        'tvcgui.features.frame_data.widgets',
        'tvcgui.features.frame_data.utils',
        'tvcgui.features.frame_data.patterns',
        'tvcgui.features.frame_data.formatters',
        'tvcgui.features.frame_data.dialogs',
        'tvcgui.features.frame_data.write_helpers',
        'tvcgui.features.frame_data.patch_runtime',
        'tvcgui.features.frame_data.projectile_integration',
        'tvcgui.features.combat.projectile_scanner',
        'tvcgui.features.training.mission_mode',
        'tvcgui.core.subprocess_compat',
        'tvcgui.features.assists.api',
        'tvcgui.features.assists.backend',
        'tvcgui.core.tk_host',

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
    icon=None,  # swap for a .ico if the operator have one
)
