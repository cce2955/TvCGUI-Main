# TAKEWHEEL_V41_RESULT_RESOLVER_UNLOCK
import os
import csv
import time
import json
import math
import random
import subprocess
import sys
import threading
import zipfile
from datetime import datetime
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame
from tvcgui.core.subprocess_compat import frozen_exe


from tvcgui.core.paths import resource_path

# Python 3.13 + pygame 2.6.1 can hard-abort inside pygame.event.get()
# with PyEval_RestoreThread before Python can raise/catch an exception.
# Default to a mouse-state input path, but still read *window lifecycle* events
# so the title-bar X / Alt+F4 can actually close the app.
# Set TVC_USE_PYGAME_EVENT_GET=1 only if the operator specifically need full SDL events.
_PYGAME_EVENT_GET_ENABLED = str(os.environ.get("TVC_USE_PYGAME_EVENT_GET", "0")).strip().lower() in {"1", "true", "yes", "on"}
_PYGAME_EVENT_WARNED = False


def _pygame_window_event_types():
    """Small event whitelist used by the safe pygame input path."""
    names = (
        "QUIT",
        "VIDEORESIZE",
        "WINDOWCLOSE",
        "WINDOWRESIZED",
        "WINDOWSIZECHANGED",
    )
    out = []
    for name in names:
        val = getattr(pygame, name, None)
        if val is not None and val not in out:
            out.append(val)
    return out


def _is_pygame_close_event(ev):
    close_types = {pygame.QUIT}
    for name in ("WINDOWCLOSE",):
        val = getattr(pygame, name, None)
        if val is not None:
            close_types.add(val)
    return getattr(ev, "type", None) in close_types


def _safe_pygame_event_get():
    """Return pygame events.

    Full event.get() stays opt-in because it was the crash source on the
    Python 3.13/pygame combo.  The default path only drains window lifecycle
    events, then the HUD uses pygame.mouse.get_pressed() for clicks.  That keeps
    the UI responsive *and* lets the title-bar X close main.py.
    """
    global _PYGAME_EVENT_WARNED
    if not _PYGAME_EVENT_GET_ENABLED:
        try:
            pygame.event.pump()
            return pygame.event.get(_pygame_window_event_types())
        except Exception as e:
            if not _PYGAME_EVENT_WARNED:
                _PYGAME_EVENT_WARNED = True
                try:
                    print(f"[pygame event] window-event poll failed, continuing with mouse fallback only: {e!r}", flush=True)
                except Exception:
                    pass
            return []

    try:
        return pygame.event.get()
    except (SystemError, KeyError) as e:
        try:
            print(f"[pygame event] recovered from event queue error: {e!r}; clearing queue", flush=True)
        except Exception:
            pass
        try:
            pygame.event.clear()
        except Exception:
            try:
                pygame.event.pump()
            except Exception:
                pass
        return []
    except Exception as e:
        try:
            print(f"[pygame event] recovered from unexpected event queue error: {e!r}; clearing queue", flush=True)
        except Exception:
            pass
        try:
            pygame.event.clear()
        except Exception:
            pass
        return []

from tvcgui.core.constants import (
    SLOTS,
    CHAR_NAMES,
    OFF_CHAR_ID,
    ATT_ID_OFF_PRIMARY,
    ATT_ID_OFF_SECOND,
    MEM1_LO,
    MEM1_HI,
    MEM2_LO,
    MEM2_HI,
)


try:
    import pyperclip
except ImportError:
    pyperclip = None

from tvcgui.core.layout import compute_layout, reassign_slots_for_giants
from tvcgui.tools.scanners.normal_scan_worker import ScanNormalsWorker
from tvcgui.features.frame_data.binding import (
    editable_row_for_live_slot,
    is_live_proven,
    live_binding,
)
from tvcgui.features.training.flags import read_training_flags
from tvcgui.ui.debug_panel import read_debug_flags, draw_debug_overlay

from tvcgui.platform.dolphin import hook, rd8, rd32, wd8, wd32, wbytes, addr_in_ram, rbytes, prime_mem2_latch, set_emulated_write_quarantine

try:
    from tvcgui.features.training.stun_profiler import RuntimeStunProfiler
except Exception as _runtime_stun_profiler_import_error:
    RuntimeStunProfiler = None

try:
    from tvcgui.features.frame_data.spreadsheet_export import FrameDataSpreadsheetExporter
except Exception as _frame_data_export_import_error:
    FrameDataSpreadsheetExporter = None


from tvcgui.runtime.ko_control import (
    KO_GLOBAL_HOLD_GROUPS,
    apply_ko_control_auto_mode,
    apply_ko_control_full_toggle,
    apply_ko_global_hold,
    apply_ko_input_inject,
    apply_ko_rescue_packet,
    apply_slot_only_ko_hold,
    capture_ko_rewind_baselines,
    idle_restore_status_text,
    tick_ko_control_auto,
)

from tvcgui.core.config import (
    MIN_HIT_DAMAGE,
    SCREEN_W, SCREEN_H,
    FONT_MAIN_SIZE, FONT_SMALL_SIZE,
    HIT_CSV,
    GENERIC_MAPPING_CSV,
    PAIR_MAPPING_CSV,
    COL_BG,
    INPUT_MONITOR_ADDRS,
    DEBUG_FLAG_ADDRS,
)

from tvcgui.ui.portraits import (
    load_portrait_placeholder,
    load_portraits_from_dir,
    get_portrait_for_snap,
)

from tvcgui.tools.scanners.fighter_resolver import RESOLVER, pick_posy_off_no_jump
from tvcgui.features.combat.meter import read_meter, METER_CACHE
from tvcgui.tools.scanners.fighter_state import read_fighter, dist2
from tvcgui.features.combat.advantage import ADV_TRACK
from tvcgui.features.combat.moves import (
    load_move_map,
    move_label_for,
    CHAR_ID_CORRECTION,
)
from tvcgui.features.combat.move_id_map import lookup_move_name
from tvcgui.features.overlay.drawing import (
    draw_panel_classic,
    draw_activity,
    draw_event_log,
    draw_scan_normals,
)

from tvcgui.tools.scanners.red_health_scanner import RedHealthScanner
from tvcgui.tools.scanners.global_red_health_scanner import GlobalRedScanner
from tvcgui.core.events import log_engaged, log_hit, log_frame_advantage

try:
    import tvcgui.tools.scanners.normal_scanner as scan_normals_all
    HAVE_SCAN_NORMALS = True
    from tvcgui.tools.scanners.normal_scanner import ANIM_MAP as SCAN_ANIM_MAP
except Exception:
    scan_normals_all = None
    HAVE_SCAN_NORMALS = False
    SCAN_ANIM_MAP = {}

from tvcgui.features.frame_data.window import (
    open_frame_data_window,
    open_frame_data_loading_window,
    close_frame_data_loading_window,
)
from tvcgui.features.combat.projectile_scanner import open_proj_scanner_window
try:
    from tvcgui.features.training.megacrash_window import open_megacrash_trainer_window
except Exception as e:
    open_megacrash_trainer_window = None
    print(f"WARNING: megacrash trainer window not available ({e!r})")
try:
    from tvcgui.features.overlay.editor import open_hud_editor_window, tick_hud_editor_state, get_hud_editor_runtime_state, reset_hud_editor_runtime_state
except Exception as e:
    open_hud_editor_window = None
    tick_hud_editor_state = None
    get_hud_editor_runtime_state = None
    reset_hud_editor_runtime_state = None
    print(f"WARNING: HUD editor window not available ({e!r})")
try:
    from tvcgui.features.training.win_counter_gate import set_win_counter_runtime_active
except Exception as e:
    set_win_counter_runtime_active = None
    print(f"WARNING: Win Counter runtime gate not available ({e!r})")
try:
    from tvcgui.features.training.input_spoof_window import open_input_spoof_window
except Exception as e:
    open_input_spoof_window = None
    print(f"WARNING: Input Spoof window not available ({e!r})")
try:
    from tvcgui.features.training.action_force_window import open_action_force_window
except Exception as e:
    open_action_force_window = None
    print(f"WARNING: Action Force window not available ({e!r})")
try:
    from tvcgui.ui.overseer import open_overseer_window
except Exception as e:
    open_overseer_window = None
    print(f"WARNING: Tool State window not available ({e!r})")
try:
    from tvcgui.features.stage_select.window import open_stage_select_window
    from tvcgui.features.stage_select.runtime import tick_stage_probe
except Exception as e:
    open_stage_select_window = None
    tick_stage_probe = None
    print(f"WARNING: Stage Select tool not available ({e!r})")

try:
    from tvcgui.features.character_select.runtime import (
        get_char_test_state,
        stop_char_test,
        restore_char_test,
        tick_char_test,
        char_test_needs_service,
        is_character_select_active,
        toggle_extra_characters,
        toggle_solo_team,
    )
except Exception as e:
    get_char_test_state = None
    stop_char_test = None
    restore_char_test = None
    tick_char_test = None
    char_test_needs_service = None
    is_character_select_active = None
    toggle_extra_characters = None
    toggle_solo_team = None
    print(f"WARNING: Extra characters toggle not available ({e!r})")
try:
    import tvcgui.features.frame_data.patch_runtime as fd_patch_runtime
except Exception as e:
    fd_patch_runtime = None
    print(f"WARNING: fd patch runtime not available ({e!r})")
try:
    import tvcgui.platform.patch_manager as runtime_pm
except Exception as e:
    runtime_pm = None
    print(f"WARNING: runtime patch manager not available ({e!r})")
try:
    from tvcgui.features.assists import (
        open_assist_scanner_window,
        tick_assist_profiles_from_main,
        get_quick_assists_for_slot,
        apply_quick_assist_from_main,
        get_assist_runtime_debug_state,
        restore_assist_runtime_defaults_from_main,
        clear_assist_runtime_state,
    )
except Exception:
    open_assist_scanner_window = None
    def tick_assist_profiles_from_main(_snaps):
        return None
    def get_quick_assists_for_slot(_slot_label, _snap=None):
        return [
            {"label": "304", "table": 304},
            {"label": "305", "table": 305},
            {"label": "306", "table": 306},
            {"label": "Default", "default": True},
        ]
    def apply_quick_assist_from_main(_slot_label, _quick_index, _snap=None, quiet=False):
        return False
    def get_assist_runtime_debug_state(_snaps=None):
        return {}
    def restore_assist_runtime_defaults_from_main(_snaps=None):
        return {"restored": [], "failed": []}
    def clear_assist_runtime_state(clear_route_cache=False):
        return None

from tvcgui.features.training.mission_manager import MissionManager
from tvcgui.features.overlay.manager import HudOverlayManager
from tvcgui.core.paths import user_data_path

MASTER_CONTROL_FILE = user_data_path("overlay", "master_overlay_control.json")

TARGET_FPS          = 60
DAMAGE_EVERY_FRAMES = 3
ADV_EVERY_FRAMES    = 2

PANEL_SLIDE_DURATION = 2.0
PANEL_FLASH_FRAMES   = 12
SCAN_SLIDE_DURATION  = 0.7

HP32_OFF   = 0x28
POOL32_OFF = 0x2C

FIGHTER_BLOCK_SIZE = 0x120

# Include 51 so generic hit logging also recognizes the low/crouching reaction lane.
REACTION_STATES = {48, 51, 64, 65, 66, 73, 79, 80, 81, 82, 90, 92, 95, 96, 97}

GIANT_IDS = {11, 22}

from tvcgui.runtime.megacrash import (
    MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE,
    MEGACRASH_TRAINER_DEFAULT_CHANCE,
    MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES,
    MEGACRASH_TRAINER_DEFAULT_MODE,
    MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL,
    MEGACRASH_TRAINER_DEFAULT_TARGET_OCCURRENCE,
    _load_megacrash_trainer_config,
    _megacrash_cooldown_remaining,
    _megacrash_mode_summary,
    _megacrash_roster_context,
    _save_megacrash_trainer_config,
    _sync_mission_megacrash_trainer,
    _tick_megacrash_trainer,
)

HB_BTN_X, HB_BTN_Y = 8, 8
HB_BTN_W, HB_BTN_H = 130, 22
QUICK_ASSIST_PERSIST_EVERY_FRAMES = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

from tvcgui.runtime.utilities import (
    FD_AUTOSCAN_DEBOUNCE_SEC,
    FD_AUTOSCAN_ENABLED,
    FD_AUTOSCAN_MIN_INTERVAL_SEC,
    FD_BUILD_MISSING_PROFILES,
    FD_MISSING_PROFILE_BUILD_DELAY_SEC,
    FD_MISSING_PROFILE_BUILD_MIN_INTERVAL_SEC,
    FD_SHEET_EXPORT_ENABLED,
    FD_WORKBENCH_PREWARM_ENABLED,
    PERF_FRAME_WARN_MS,
    _PERF_LAST_ELAPSED_MS,
    _copy_to_clipboard,
    _perf_warn,
    _start_memory_dump,
    u32be_from_block,
)



from tvcgui.ui.components import (
    TOP_UI_RESERVED,
    GUI_APP_ACCENT,
    GUI_CONFIRM,
    GUI_DANGER,
    GUI_TEXT,
    GUI_TEXT_DIM,
    GUI_TEXT_MUTED,
    _brighten,
    _darken,
    _draw_vertical_gradient,
    _render_outlined_text,
    _render_rainbow_outlined_text,
    _slot_accent_for_label,
    draw_bottom_workspace_tabs,
    draw_glass_button,
    draw_status_rail,
    draw_top_command_dock,
)

def _char_test_active_for_dock() -> bool:
    """Return whether Char test is currently running."""
    if get_char_test_state is None:
        return False
    try:
        return bool(get_char_test_state().get("running", False))
    except Exception:
        return False


def _solo_team_active_for_dock() -> bool:
    if get_char_test_state is None:
        return False
    try:
        state = get_char_test_state() or {}
        return bool(state.get("solo_team_requested") or state.get("solo_team_enabled"))
    except Exception:
        return False


def _win_score_active_for_dock() -> bool:
    """Return whether visible win score hold should render as active in the dock."""
    if get_hud_editor_runtime_state is None:
        return False
    try:
        state = get_hud_editor_runtime_state() or {}
        holds = state.get("holds") or {}
        return any(bool((holds.get(player) or {}).get("enabled", False)) for player in ("P1", "P2"))
    except Exception:
        return False


from tvcgui.ui.normal_preview import (
    _apply_panel_element_enter_animation,
    _ease_out_cubic,
    draw_quick_assist_footer,
    draw_scan_normals_polished,
)
from tvcgui.ui.advantage_window import draw_advantage_window, open_advantage_window

def merged_debug_values():
    core_flags = read_debug_flags()
    training   = read_training_flags()

    trpause_row    = None
    remaining_training = []
    for entry in training:
        if entry and entry[0] == "TrPause" and trpause_row is None:
            trpause_row = entry
        else:
            remaining_training.append(entry)

    if trpause_row is not None:
        if core_flags:
            core_flags = [core_flags[0], trpause_row] + core_flags[1:]
        else:
            core_flags = [trpause_row]

    return core_flags + remaining_training


def safe_read_fighter(base: int, yoff: int) -> dict | None:
    try:
        snap = read_fighter(base, yoff)
    except Exception as e:
        print(f"[safe_read_fighter] read_fighter raised {e!r} for base=0x{base:08X}")
        return None
    return snap if snap else None


def init_pygame():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TvCGUI.HUD.1")

    pygame.init()

    try:
        font = pygame.font.SysFont("consolas", FONT_MAIN_SIZE)
    except Exception:
        font = pygame.font.Font(None, FONT_MAIN_SIZE)

    try:
        smallfont = pygame.font.SysFont("consolas", FONT_SMALL_SIZE)
    except Exception:
        smallfont = pygame.font.Font(None, FONT_SMALL_SIZE)

    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.RESIZABLE)
    pygame.display.set_caption("TvC Continuo Tool")

    icon_path = resource_path("assets", "portraits", "Placeholder.png")
    if not os.path.exists(icon_path):
        icon_path = resource_path("assets", "icon.png")
    if os.path.exists(icon_path):
        icon = pygame.image.load(icon_path).convert_alpha()
        pygame.display.set_icon(icon)

    return screen, font, smallfont


def resolve_bases(last_base_by_ptr: dict, y_off_by_base: dict) -> list:
    resolved = []
    for slotname, ptr_addr, teamtag in SLOTS:
        raw_base = rd32(ptr_addr)
        if raw_base is None or not addr_in_ram(raw_base):
            base = None
        else:
            base = raw_base

        changed = base is not None and last_base_by_ptr.get(ptr_addr) != base
        if base:
            # Non-blocking MEM2 primer. This keeps the tool armed when it was
            # launched from menus: as soon as any live fighter pointer appears,
            # dolphin_io can latch the correct MEM2 host map before read
            # fighter/y-position data.
            try:
                prime_mem2_latch()
            except Exception:
                pass

        if base and changed:
            last_base_by_ptr[ptr_addr] = base
            METER_CACHE.drop(base)
            y_off_by_base[base] = pick_posy_off_no_jump(base)

        resolved.append((slotname, teamtag, base))
    return resolved


def compute_team_giant_solo(snaps: dict) -> tuple[bool, bool]:
    def team_solo(prefix: str) -> bool:
        c1 = snaps.get(f"{prefix}-C1")
        c2 = snaps.get(f"{prefix}-C2")
        if not c1:
            return False
        if (c1.get("id") or 0) not in GIANT_IDS:
            return False
        if not c2:
            return True
        b1, b2 = c1.get("base"), c2.get("base")
        return isinstance(b1, int) and isinstance(b2, int) and b1 == b2

    return team_solo("P1"), team_solo("P2")


def ensure_scan_now(last_scan_normals, last_scan_time):
    """Return only the already-ready background snapshot.

    A profile cache can be tens of megabytes and rebasing/refeshing its move
    packets is still real work.  That work belongs to ScanNormalsWorker, never
    to the pygame click handler.  The Frame Data button therefore opens from
    the most recent ready snapshot, or waits for the normal background refresh
    when no snapshot exists yet.
    """
    return last_scan_normals, last_scan_time


def _fd_missing_profile_signature(scan_rows) -> tuple:
    parts = []
    try:
        rows = list(scan_rows or [])
    except Exception:
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if not row.get("profile_cache_miss"):
            continue
        slot = str(row.get("slot_label") or row.get("slot") or "")
        key = str(row.get("profile_key") or row.get("char_name") or "")
        if slot and key:
            parts.append((slot, key))
    return tuple(sorted(parts))


def _fd_row_needs_dynamic_profile(slot_label: str, scan_rows) -> bool:
    try:
        rows = list(scan_rows or [])
    except Exception:
        rows = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        lbl = str(row.get("slot_label") or row.get("slot") or "")
        if lbl == str(slot_label) and bool(row.get("profile_cache_miss")):
            return True
    return False

def _fd_row_has_editable_fields(row) -> bool:
    """Whether a single workbench row carries at least one writable packet."""
    if not isinstance(row, dict):
        return False
    for mv in list(row.get("moves") or []):
        if not isinstance(mv, dict):
            continue
        if any(mv.get(key) for key in ("damage_addr", "stun_addr", "active_addr", "meter_addr")):
            return True
    return False


def _fd_current_live_binding(slot_label: str, preview_rows, render_snaps: dict) -> dict:
    """Identity of the fighter currently shown behind a Frame Data button."""
    return live_binding(
        str(slot_label),
        preview_rows,
        (render_snaps or {}).get(str(slot_label)) or {},
    )


def _fd_live_editable_row(slot_label: str, workbench_rows, preview_rows, render_snaps: dict):
    """Return only a workbench row proven to belong to the clicked live fighter.

    A slot name alone is never enough: P1-C1 can be Ryu in the old match and
    Jun in the current one. Unknown identities intentionally return None so the
    UI shows its loading shell until the current preview lands.
    """
    row = editable_row_for_live_slot(
        str(slot_label),
        workbench_rows,
        preview_rows,
        (render_snaps or {}).get(str(slot_label)) or {},
    )
    return row if _fd_row_has_editable_fields(row) else None


def _panel_bar_fraction(value, maximum) -> float:
    try:
        v = float(value or 0)
        m = float(maximum or 0)
        if m <= 0:
            return 0.0
        return max(0.0, min(1.0, v / m))
    except Exception:
        return 0.0


def _draw_panel_stat_bar(
    surf: pygame.Surface,
    rect: pygame.Rect,
    fraction: float,
    fill_col: tuple[int, int, int],
    *,
    empty_col: tuple[int, int, int] = (24, 27, 36),
    border_col: tuple[int, int, int] = (58, 66, 88),
) -> None:
    fraction = max(0.0, min(1.0, float(fraction or 0.0)))
    pygame.draw.rect(surf, empty_col, rect, border_radius=3)
    pygame.draw.rect(surf, border_col, rect, 1, border_radius=3)

    if fraction > 0.0:
        fill_w = max(2, int((rect.width - 2) * fraction))
        fill_rect = pygame.Rect(rect.x + 1, rect.y + 1, fill_w, max(1, rect.height - 2))
        _draw_vertical_gradient(
            surf,
            fill_rect,
            _brighten(fill_col, 24),
            _darken(fill_col, 18),
            235,
        )
        # Soft internal highlight, graphite-blue rather than white.
        hi = pygame.Rect(fill_rect.x + 1, fill_rect.y + 1, max(1, fill_rect.width - 2), max(1, fill_rect.height // 3))
        pygame.draw.rect(surf, (170, 190, 225, 20), hi, border_radius=2)


def _meter_fraction_from_snap(snap: dict | None) -> tuple[float, str]:
    if not isinstance(snap, dict):
        return 0.0, "0"
    meter_val = snap.get("meter")
    try:
        raw = float(meter_val if meter_val is not None else 0.0)
    except Exception:
        raw = 0.0
    frac = max(0.0, min(1.0, raw / 50000.0))
    bars = int(max(0, min(5, raw // 10000)))
    return frac, f"{bars}/5"


def _meter_value_text_color(raw_meter: int | float) -> tuple[int, int, int]:
    """Color meter text by raw meter amount.

    Near zero is intentionally dark/muted. At each bar threshold it brightens,
    then ramps through cool light colors until it reaches red at 50k/max.
    """
    try:
        raw = max(0.0, min(50000.0, float(raw_meter or 0)))
    except Exception:
        raw = 0.0

    if raw <= 0:
        return (72, 78, 92)

    stops = [
        (0.0,     (72, 78, 92)),     # near zero: dark steel
        (10000.0, (132, 176, 245)),   # lvl 1: light blue
        (20000.0, (110, 218, 190)),   # lvl 2: mint/cyan
        (30000.0, (230, 210, 120)),   # lvl 3: pale gold
        (40000.0, (245, 160, 95)),    # lvl 4: warm orange
        (50000.0, (235, 80, 95)),     # max: red
    ]

    for i in range(len(stops) - 1):
        a_raw, a_col = stops[i]
        b_raw, b_col = stops[i + 1]
        if raw <= b_raw:
            t = (raw - a_raw) / max(1.0, b_raw - a_raw)
            return (
                int(a_col[0] + (b_col[0] - a_col[0]) * t),
                int(a_col[1] + (b_col[1] - a_col[1]) * t),
                int(a_col[2] + (b_col[2] - a_col[2]) * t),
            )

    return stops[-1][1]


def draw_panel_polished_stats(
    surf: pygame.Surface,
    rect: pygame.Rect,
    snap: dict | None,
    portrait: pygame.Surface | None,
    font: pygame.font.Font,
    smallfont: pygame.font.Font,
    header: str,
    t_ms: int,
    *,
    assist_label: str = "--",
    panel_fx: dict | None = None,
) -> None:
    """Compact fighter card with strong hierarchy plus lightweight premium FX."""
    panel_fx = panel_fx or {}
    now = time.time()

    def _fx(entry):
        if not isinstance(entry, dict):
            return 0.0
        try:
            start = float(entry.get("start", 0.0) or 0.0)
            dur = max(0.001, float(entry.get("dur", 0.3) or 0.3))
        except Exception:
            return 0.0
        if not start:
            return 0.0
        return max(0.0, min(1.0, (now - start) / dur))

    _draw_vertical_gradient(surf, rect, (20, 22, 30), (14, 15, 22), 255)
    if not isinstance(snap, dict):
        pygame.draw.rect(surf, (55, 63, 84), rect, 1, border_radius=5)
        title = _render_outlined_text(smallfont, f"{header}  empty", GUI_TEXT_DIM, (0, 0, 0), rect.width - 20, 1)
        surf.blit(title, (10, 8))
        return

    accent = _slot_accent_for_label(header, muted=False)
    move_preview = str(snap.get("mv_label") or "").strip().lower()
    try:
        early_move_id = int(snap.get("mv_id_display") or 0)
    except Exception:
        early_move_id = 0
    try:
        early_hp_cur = int(snap.get("cur") or 0)
    except Exception:
        early_hp_cur = 0

    assist_state_ids = {420, 424, 425, 426, 427, 428, 430, 431, 432, 433, 0x01A1, 0x01A8, 0x01AE}
    ko_state = (("ko" in move_preview) or ("k.o" in move_preview) or ("knock out" in move_preview) or ("dead" in move_preview) or ("death" in move_preview) or ("defeat" in move_preview) or ("slow motion" in move_preview and "ko" in move_preview) or (early_hp_cur <= 0))
    is_support = (("assist" in move_preview) or ("tag out" in move_preview) or ("tag in taunt" in move_preview) or ko_state or (early_move_id in assist_state_ids))
    is_active_panel = not is_support

    border_col = (84, 74, 74) if ko_state else (_brighten(accent, 22) if is_active_panel else (55, 63, 84))
    pygame.draw.rect(surf, border_col, rect, 1, border_radius=5)
    side_accent = (116, 92, 92) if ko_state else accent
    pygame.draw.rect(surf, side_accent, pygame.Rect(0, 0, 3, rect.height), border_radius=2)

    victory_pulse_live = bool(panel_fx.get("victory_pulse_live")) and not ko_state
    if victory_pulse_live:
        vp = 0.5 + 0.5 * math.sin((t_ms / 1000.0) * 5.2)
        halo = pygame.Surface((rect.width + 10, rect.height + 10), pygame.SRCALPHA)
        pygame.draw.rect(halo, (*_brighten(accent, 34), int(34 + 24 * vp)), pygame.Rect(5, 5, rect.width, rect.height), 2, border_radius=8)
        surf.blit(halo, (-5, -5))
        pulse_rail = pygame.Surface((7, rect.height), pygame.SRCALPHA)
        pygame.draw.rect(pulse_rail, (*_brighten(accent, 30), int(55 + 55 * vp)), pulse_rail.get_rect(), border_radius=3)
        surf.blit(pulse_rail, (0, 0))

    # Active point panels get a very faint moving scanline.
    if is_active_panel:
        pulse = 0.5 + 0.5 * math.sin((t_ms / 1000.0) * 2.2)
        glow = pygame.Surface((rect.width - 4, rect.height - 4), pygame.SRCALPHA)
        pygame.draw.rect(glow, (*accent, int(18 + 10 * pulse)), glow.get_rect(), 2, border_radius=6)
        surf.blit(glow, (2, 2))
        sweep_x = int((rect.width + 60) * (((t_ms / 1000.0) * 0.16) % 1.0)) - 30
        scanline = pygame.Surface((18, rect.height - 10), pygame.SRCALPHA)
        pygame.draw.rect(scanline, (*_brighten(accent, 24), 16), pygame.Rect(0, 0, 8, rect.height - 10), border_radius=3)
        pygame.draw.rect(scanline, (*_brighten(accent, 40), 8), pygame.Rect(8, 0, 10, rect.height - 10), border_radius=3)
        surf.blit(scanline, (sweep_x, 5))
    elif ko_state:
        glow = pygame.Surface((rect.width - 4, rect.height - 4), pygame.SRCALPHA)
        pygame.draw.rect(glow, (180, 90, 90, 22), glow.get_rect(), 1, border_radius=6)
        surf.blit(glow, (2, 2))

    pad = 8
    portrait_size = max(48, min(64, rect.height - 58))
    portrait_rect = pygame.Rect(pad, pad + 8, portrait_size, portrait_size)
    shadow = pygame.Surface((portrait_rect.width + 12, portrait_rect.height + 12), pygame.SRCALPHA)
    pygame.draw.rect(shadow, (0, 0, 0, 52), shadow.get_rect(), border_radius=8)
    surf.blit(shadow, (portrait_rect.x - 4, portrait_rect.y + 4))
    glow = pygame.Surface((portrait_rect.width + 8, portrait_rect.height + 8), pygame.SRCALPHA)
    glow_alpha = 18 if ko_state else (46 if is_active_panel else 22)
    pygame.draw.rect(glow, (*accent, glow_alpha), glow.get_rect(), 2, border_radius=7)
    surf.blit(glow, (portrait_rect.x - 4, portrait_rect.y - 4))
    frame_rect = portrait_rect.inflate(6, 6)
    pygame.draw.rect(surf, (11, 13, 19), frame_rect, border_radius=6)
    pygame.draw.rect(surf, _brighten(accent, 10) if is_active_panel else (62, 70, 94), frame_rect, 1, border_radius=6)
    if portrait is not None:
        try:
            p = pygame.transform.smoothscale(portrait, (portrait_rect.width, portrait_rect.height))
            if not is_active_panel:
                p = p.copy()
                veil = pygame.Surface(p.get_size(), pygame.SRCALPHA)
                veil.fill((18, 12, 12, 80) if ko_state else (0, 0, 0, 35))
                p.blit(veil, (0, 0))
            surf.blit(p, portrait_rect.topleft)
        except Exception:
            pygame.draw.rect(surf, (28, 31, 42), portrait_rect, border_radius=4)
    else:
        pygame.draw.rect(surf, (28, 31, 42), portrait_rect, border_radius=4)

    info_x = portrait_rect.right + 10
    info_right = rect.width - 10
    info_w = max(160, info_right - info_x)
    y = pad + 2
    char_name = str(snap.get("name") or "???")
    try:
        base_addr = int(snap.get("base") or 0)
        base_txt = f" @0x{base_addr:08X}" if base_addr else ""
    except Exception:
        base_txt = ""
    title_text = f"{header}  {char_name}{base_txt}"
    title_col = (220, 205, 205) if ko_state else (GUI_TEXT if is_active_panel else (210, 215, 225))
    title_s = _render_outlined_text(smallfont, title_text, title_col, (0, 0, 0), info_w, 1)
    surf.blit(title_s, (info_x, y))
    if ko_state:
        kf = _fx(panel_fx.get("ko_fade"))
        badge_alpha = int(255 * (kf if kf > 0 else 1.0))
        badge_w, badge_h = 46, 18
        badge = pygame.Surface((badge_w, badge_h), pygame.SRCALPHA)
        pygame.draw.rect(badge, (66, 32, 32, badge_alpha), badge.get_rect(), border_radius=5)
        pygame.draw.rect(badge, (176, 102, 102, badge_alpha), badge.get_rect(), 1, border_radius=5)
        badge_s = _render_outlined_text(font, "KO", (240, 228, 228), (0, 0, 0), badge_w - 6, 1)
        badge.blit(badge_s, ((badge_w - badge_s.get_width()) // 2, (badge_h - badge_s.get_height()) // 2 - 1))
        bx = rect.width - badge_w - 10
        by = 8
        surf.blit(badge, (bx, by))
    y += title_s.get_height() + 3

    hp_cur = snap.get("cur") or 0
    hp_max = snap.get("max") or 0
    hp_frac = _panel_bar_fraction(hp_cur, hp_max)
    hp_col = GUI_DANGER if hp_frac <= 0.30 else GUI_CONFIRM
    meter_frac, meter_txt = _meter_fraction_from_snap(snap)
    meter_val = snap.get("meter")
    try:
        raw_meter = int(float(meter_val if meter_val is not None else 0))
    except Exception:
        raw_meter = 0
    hp_text = f"HP {int(hp_cur or 0)}/{int(hp_max or 0)}"
    meter_text = f"Meter:{raw_meter}/Lvl {meter_txt.split('/')[0]}"
    hp_s = _render_outlined_text(smallfont, hp_text, hp_col, (0, 0, 0), max(90, info_w // 2 - 8), 1)
    meter_s = _render_outlined_text(smallfont, meter_text, _meter_value_text_color(raw_meter), (0, 0, 0), max(90, info_w // 2), 1)
    hp_x = info_x
    meter_x = info_x + max(170, info_w // 2)
    if meter_x + meter_s.get_width() > info_right:
        meter_x = info_x + min(190, max(150, info_w - meter_s.get_width()))
    surf.blit(hp_s, (hp_x, y))
    surf.blit(meter_s, (meter_x, y))

    bar_y = y + hp_s.get_height() + 2
    hp_bar_w = max(90, min(180, meter_x - hp_x - 16))
    meter_bar_w = max(90, min(180, info_right - meter_x))
    hp_bar = pygame.Rect(hp_x, bar_y, hp_bar_w, 3)
    meter_bar = pygame.Rect(meter_x, bar_y, meter_bar_w, 3)
    _draw_panel_stat_bar(surf, hp_bar, hp_frac, hp_col, empty_col=(18, 20, 28), border_col=(38, 44, 60))
    _draw_panel_stat_bar(surf, meter_bar, meter_frac, GUI_APP_ACCENT, empty_col=(18, 20, 28), border_col=(38, 44, 60))

    # HP trailing damage segment.
    hp_loss = panel_fx.get("hp_loss")
    hp_loss_t = _fx(hp_loss)
    if hp_loss_t > 0.0 and isinstance(hp_loss, dict):
        old_frac = float(hp_loss.get("from_frac", hp_frac) or hp_frac)
        cur_frac = float(hp_loss.get("to_frac", hp_frac) or hp_frac)
        if old_frac > cur_frac:
            x1 = hp_bar.x + 1 + int((hp_bar.width - 2) * cur_frac)
            x2 = hp_bar.x + 1 + int((hp_bar.width - 2) * old_frac)
            if x2 > x1:
                trail_rect = pygame.Rect(x1, hp_bar.y + 1, x2 - x1, max(1, hp_bar.height - 2))
                trail_alpha = int((1.0 - hp_loss_t) * 170)
                trail = pygame.Surface((trail_rect.width, trail_rect.height), pygame.SRCALPHA)
                _draw_vertical_gradient(trail, trail.get_rect(), (210, 72, 72), (140, 42, 42), trail_alpha)
                surf.blit(trail, trail_rect.topleft)

    # Meter gain flash + tiny floating gain indicator.
    meter_gain = panel_fx.get("meter_gain")
    meter_gain_t = _fx(meter_gain)
    if meter_gain_t > 0.0 and isinstance(meter_gain, dict):
        flash_alpha = int((1.0 - meter_gain_t) * 120)
        flash = pygame.Surface((meter_bar.width, max(4, meter_bar.height + 2)), pygame.SRCALPHA)
        pygame.draw.rect(flash, (*GUI_APP_ACCENT, flash_alpha), flash.get_rect(), border_radius=3)
        surf.blit(flash, (meter_bar.x, meter_bar.y - 1))
        delta = int(meter_gain.get("delta", 0) or 0)
        if delta > 0:
            plus = _render_outlined_text(smallfont, f"+{delta}", GUI_APP_ACCENT, (0, 0, 0), 60, 1)
            float_y = meter_bar.y - 12 - int(10 * meter_gain_t)
            plus.set_alpha(max(0, int(255 * (1.0 - meter_gain_t))))
            surf.blit(plus, (meter_bar.right - plus.get_width(), float_y))

    y = bar_y + 7
    try:
        pool_pct = float(snap.get("pool_pct") or 0.0)
    except Exception:
        pool_pct = 0.0
    try:
        raw_pool = int(snap.get("hp_pool_byte") or 0)
    except Exception:
        raw_pool = 0
    pool_text = f"POOL (02A): {pool_pct:5.1f}%   raw:{raw_pool}"
    pool_s = _render_outlined_text(smallfont, pool_text, GUI_TEXT, (0, 0, 0), info_w, 1)
    surf.blit(pool_s, (info_x, y))
    y += pool_s.get_height() + 2

    pct = float(snap.get("baroque_red_pct_max") or 0.0)
    ready = bool(snap.get("baroque_ready_local", False))
    ready_txt = "READY" if ready else "not ready"
    baroque_text = f"Baroque: {ready_txt}  red:{pct:.1f}%"
    if ready:
        bq_s = _render_rainbow_outlined_text(smallfont, baroque_text, info_w, t_ms, (0, 0, 0), 1)
    else:
        bq_s = _render_outlined_text(smallfont, baroque_text, GUI_TEXT_MUTED, (0, 0, 0), info_w, 1)
    surf.blit(bq_s, (info_x, y))
    ready_ping_t = _fx(panel_fx.get("baroque_ready"))
    if ready_ping_t > 0.0:
        sweep_x = info_x - 20 + int((bq_s.get_width() + 40) * ready_ping_t)
        sweep = pygame.Surface((18, bq_s.get_height() + 2), pygame.SRCALPHA)
        pygame.draw.rect(sweep, (255, 255, 255, int((1.0 - ready_ping_t) * 42)), pygame.Rect(0, 0, 8, bq_s.get_height() + 2), border_radius=3)
        surf.blit(sweep, (sweep_x, y - 1))
    y += bq_s.get_height() + 2

    move_id = snap.get("mv_id_display")
    mv_label = str(snap.get("mv_label") or "").strip()
    move_id_dec = None
    if move_id is not None:
        try:
            move_id_dec = int(move_id)
        except Exception:
            move_id_dec = None

    if not mv_label and move_id_dec is not None:
        mv_label = f"0x{move_id_dec:04X}"
    elif not mv_label and move_id is not None:
        mv_label = str(move_id)
    if not mv_label:
        mv_label = "--"

    if move_id_dec is not None:
        move_text = f"Move: {mv_label} ({move_id_dec})"
    else:
        move_text = f"Move: {mv_label}"

    move_pulse_t = _fx(panel_fx.get("move_pulse"))
    if move_pulse_t > 0.0:
        move_col = _brighten(GUI_TEXT if is_active_panel else GUI_TEXT_MUTED, int((1.0 - move_pulse_t) * 40))
    else:
        move_col = GUI_TEXT if is_active_panel else GUI_TEXT_MUTED
    move_s = _render_outlined_text(smallfont, move_text, move_col, (0, 0, 0), info_w, 1)
    surf.blit(move_s, (info_x, y))
    if move_pulse_t > 0.0:
        pulse_w = min(info_w, max(60, move_s.get_width() + 12))
        pulse_bg = pygame.Surface((pulse_w, move_s.get_height() + 4), pygame.SRCALPHA)
        pygame.draw.rect(pulse_bg, (*accent, int((1.0 - move_pulse_t) * 36)), pulse_bg.get_rect(), border_radius=4)
        surf.blit(pulse_bg, (info_x - 2, y - 2))
        surf.blit(move_s, (info_x, y))

    pulse = 0.5 + 0.5 * math.sin((t_ms / 1000.0) * 3.0)
    alpha = int((18 if is_active_panel else 8) + (14 if is_active_panel else 6) * pulse)
    glow_line = pygame.Surface((min(info_w, 220), 1), pygame.SRCALPHA)
    glow_line.fill((*accent, alpha))
    surf.blit(glow_line, (info_x, max(4, y + move_s.get_height() + 2)))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def legacy_main():
    print("HUD: waiting for Dolphin...")
    hook()
    print("HUD: hooked Dolphin.")

    move_map, global_map = load_move_map(GENERIC_MAPPING_CSV, PAIR_MAPPING_CSV)

    screen, font, smallfont = init_pygame()
    clock = pygame.time.Clock()

    placeholder_portrait = load_portrait_placeholder()
    portraits = load_portraits_from_dir(resource_path("assets", "portraits"))
    print(f"HUD: loaded {len(portraits)} portraits.")

    if HAVE_SCAN_NORMALS and scan_normals_all is not None:
        # Normal gameplay reads a compact, immutable normals-preview snapshot.
        # Do not load/rebase the 90+ MB workbench cache and do not fall through
        # into a dynamic MEM2 scan when a character changes. The full scanner is
        # reserved for an explicit Frame Data request.
        scan_worker = ScanNormalsWorker(
            # Tiny immutable snapshot for the HUD only. It intentionally omits
            # live write addresses and must never be handed to the editor.
            lambda: scan_normals_all.scan_once(cache_only=True, preview_only=True),
            # Full saved profile rebase for the Frame Data editor. This is still
            # cache-only: no one-minute dynamic discovery scan on ordinary click.
            workbench_scan_func=lambda: scan_normals_all.scan_once(
                force_dynamic=False,
                cache_only=True,
                preview_only=False,
            ),
            full_scan_func=lambda dynamic_char_ids=None: scan_normals_all.scan_once(
                force_dynamic=True,
                cache_only=False,
                preview_only=False,
                dynamic_char_ids=dynamic_char_ids,
            ),
        )
        scan_worker.start()
        # Prewarm the compact HUD snapshot immediately, rather than waiting for
        # a button click to be the first request.
        scan_worker.request()
    else:
        scan_worker = None

    # Saved frame-data patches can be shared as JSON configs. Detect them at
    # launch and expose three modes: skip, ask per
    # character, or auto-load every matching character section. The actual write
    # path still uses the same low-level handlers as the frame-data workbench.
    fd_patch_controller = None
    if fd_patch_runtime is not None:
        try:
            fd_patch_controller = fd_patch_runtime.create_patch_autoload_controller()
        except Exception as e:
            print(f"[fd patch] controller init failed: {e!r}")
            fd_patch_controller = None

    # ------------------------------------------------------------------
    # Managers
    # ------------------------------------------------------------------
    mission_mgr = MissionManager(
        move_map=move_map,
        global_map=global_map,
        debug_flag_addrs=DEBUG_FLAG_ADDRS,
        read_debug_flags_fn=merged_debug_values,
        move_label_for_fn=move_label_for,
    )
    hud_mgr = HudOverlayManager(move_map=move_map, global_map=global_map)

    # ------------------------------------------------------------------
    # Runtime state
    # ------------------------------------------------------------------
    last_scan_normals = None              # compact HUD preview rows
    last_workbench_scan_normals = None    # rich rows with live editable addresses
    # slot -> immutable identity requested by the click. This prevents a stale
    # Ryu/Chun cache row from opening after the user loads Jun/Polimar.
    fd_pending_window_targets = {}
    fd_workbench_prewarmed = False
    last_scan_time    = 0.0
    scan_anim         = None

    def _scan_move_window_for_slot(slot_label: str, cur_anim: int | None):
        if cur_anim is None or not last_scan_normals:
            return None, None
        try:
            for slot_data in last_scan_normals:
                if slot_data.get("slot_label") != slot_label:
                    continue
                for mv in slot_data.get("moves", []):
                    if mv.get("id") == cur_anim:
                        return mv.get("active_start"), mv.get("active_end")
        except Exception:
            pass
        return None, None

    last_base_by_ptr  = {}
    y_off_by_base     = {}
    prev_hp           = {}
    pool_baseline     = {}
    char_meta_by_base = {}
    last_move_anim_id = {}
    last_char_by_slot = {}

    baroque_latch_by_base        = {}
    last_baroque_pct_by_base     = {}
    last_baroque_ready_by_base   = {}
    baroque_peak_by_base         = {}

    render_snap_by_slot    = {}
    render_portrait_by_slot = {}

    panel_anim            = {}
    anim_queue_after_scan = set()
    panel_btn_flash       = {s: 0 for (s, _, _) in SLOTS}
    quick_btn_flash       = {}
    active_quick_assist_by_slot = {}
    quick_assist_reapply_state = {}
    quick_assist_slide_by_slot = {}
    panel_fx_state = {}

    # KO lab UI feedback.  A one-frame write can be overwritten by the
    # round-end manager, so clicking Idle Restore also holds/reapplies the
    # packet briefly.  idle_restore_status is shown in the on-screen status rail.
    idle_restore_hold_until_by_slot = {}
    idle_restore_status = {"text": "", "until": 0.0}
    ko_rewind_baseline_by_slot = {}
    ko_dol_patch_index = -1
    ko_control_full_enabled = False  # operator armed auto-mode; starts OFF
    ko_control_live_active = False    # whether the DOL Control+Full packet is currently applied
    ko_control_last_apply = 0.0
    ko_control_auto_state = {"any_team_dead": False, "summary": ""}
    ko_global_hold_baseline = None

    # Off by default means the DOL addresses are restored when the GUI starts,
    # not merely that the button is visually off.
    try:
        apply_ko_control_full_toggle(False, verify=False)
        print("[ko control] default OFF/auto unarmed; restored KO/input DOL originals", flush=True)
    except Exception as _e:
        print(f"[ko control] default OFF restore skipped: {_e!r}", flush=True)

    def _quick_assist_snap_char_id(_snap) -> int:
        if not isinstance(_snap, dict):
            return 0
        for _field in ("id", "csv_char_id", "char_id"):
            try:
                _cid = int(_snap.get(_field) or 0)
            except Exception:
                _cid = 0
            if _cid:
                return _cid
        return 0

    def _tick_persistent_quick_assists(_snaps: dict, _frame_idx: int) -> None:
        'Cheap quick-assist state maintenance.\n\n        The selected assist is already stored in assist_scanner_backend when the\n        operator clicks a quick-assist button.  The backend now patches that stored\n        slot profile on assist standby/jump-in/attack, so this main-loop helper\n        must not re-apply the full assist route at idle.  Re-applying here was\n        the remaining 25-35 ms assist_persist spike.\n        '
        if not active_quick_assist_by_slot or not isinstance(_snaps, dict):
            return

        # Validate/clean selection state only a few times per second.  The actual
        # assist write path runs in tick_assist_profiles_from_main(), which sees
        # every frame and patches only when an assist-ish state is active.
        if (_frame_idx % 15) != 0:
            return

        for _slot_label, _row in list(active_quick_assist_by_slot.items()):
            if not isinstance(_row, dict):
                active_quick_assist_by_slot.pop(_slot_label, None)
                quick_assist_reapply_state.pop(_slot_label, None)
                continue
            try:
                _quick_index = int(_row.get("quick_index"))
            except Exception:
                active_quick_assist_by_slot.pop(_slot_label, None)
                quick_assist_reapply_state.pop(_slot_label, None)
                continue

            _snap = _snaps.get(_slot_label) or render_snap_by_slot.get(_slot_label)
            if not isinstance(_snap, dict):
                continue
            _snap_char_id = _quick_assist_snap_char_id(_snap)
            try:
                _stored_char_id = int(_row.get("char_id") or 0)
            except Exception:
                _stored_char_id = 0
            if _stored_char_id and _snap_char_id and _stored_char_id != _snap_char_id:
                active_quick_assist_by_slot.pop(_slot_label, None)
                quick_assist_reapply_state.pop(_slot_label, None)
                continue

            try:
                _fighter_base = int(_snap.get("base") or 0)
            except Exception:
                _fighter_base = 0
            _key = (_quick_index, _stored_char_id or _snap_char_id, _fighter_base)
            _prev = quick_assist_reapply_state.get(_slot_label) or {}
            if _prev.get("key") != _key:
                quick_assist_reapply_state[_slot_label] = {"key": _key, "last_frame": int(_frame_idx)}


    prev_hp_frac_by_slot = {}
    prev_meter_by_slot = {}
    prev_move_by_slot = {}
    prev_baroque_ready_by_slot = {}
    prev_ko_by_slot = {}
    bottom_tab_fade = None

    manual_scan_requested = False
    need_rescan_normals   = False
    pending_rescan_normals_since = 0.0
    pending_rescan_signature = None
    last_rescan_request_time = 0.0
    last_rescan_request_signature = None
    pending_missing_profile_signature = None
    pending_missing_profile_since = 0.0
    last_missing_profile_build_time = 0.0
    last_missing_profile_build_signature = None

    # Individual character CSV export is intentionally a cached workbench
    # read, scheduled once per stable roster. It never modifies a master CSV
    # and never asks the dynamic compiler to build data during gameplay.
    fd_csv_pending_roster_signature = None
    fd_csv_pending_since = 0.0
    fd_csv_last_requested_signature = None

    def _fd_scan_signature(_snaps: dict) -> tuple:
        parts = []
        if isinstance(_snaps, dict):
            for _slot_label in ("P1-C1", "P2-C1", "P1-C2", "P2-C2"):
                _snap = _snaps.get(_slot_label) or {}
                if not isinstance(_snap, dict):
                    _snap = {}
                try:
                    _base = int(_snap.get("base") or 0)
                except Exception:
                    _base = 0
                _cid = 0
                for _field in ("id", "csv_char_id", "char_id"):
                    try:
                        _cid = int(_snap.get(_field) or 0)
                    except Exception:
                        _cid = 0
                    if _cid:
                        break
                _name = str(_snap.get("name") or _snap.get("char_name") or "")
                parts.append((_slot_label, _base, _cid, _name))
        return tuple(parts)


    def _missing_preview_profile_signature(scan_rows) -> tuple:
        """Return only active roster entries that missed the compact snapshot.

        A miss means the one-file EXE needs a single full profile build before
        the range-ruler auto-learner can identify active windows for that
        fighter.  Existing preview entries never reach this path.
        """
        missing = []
        for _row in list(scan_rows or []):
            if not isinstance(_row, dict) or not bool(_row.get("profile_cache_miss")):
                continue
            try:
                _cid = int(_row.get("char_id") or 0)
            except Exception:
                _cid = 0
            _sig = str(_row.get("profile_table_signature") or "").strip()
            _slot = str(_row.get("slot_label") or "")
            if _cid > 0 and _sig:
                missing.append((_cid, _sig, _slot))
        return tuple(sorted(set(missing)))

    def _fd_export_roster_signature(scan_rows) -> tuple:
        """Identify the current live roster for cached per-character CSV export.

        This uses only a completed compact HUD snapshot.  The follow-up request
        is workbench/cache-only, never a dynamic scan, so roster changes cannot
        make the frame-data compiler run during gameplay.
        """
        signature = []
        for _row in list(scan_rows or []):
            if not isinstance(_row, dict):
                continue
            try:
                _cid = int(_row.get("char_id") or 0)
            except Exception:
                _cid = 0
            if _cid <= 0:
                continue
            _slot = str(_row.get("slot_label") or "")
            _profile_key = str(_row.get("profile_key") or "")
            _table_sig = str(_row.get("profile_table_signature") or "")
            signature.append((_slot, _cid, _profile_key, _table_sig))
        return tuple(sorted(signature))

    last_adv_display = ""
    pending_hits     = []
    frame_idx        = 0

    # Reverse profile resolved stun from live victim counters.  This is
    # observation-only and fails closed when the optional module is absent.
    runtime_stun_profiler = None
    if RuntimeStunProfiler is not None:
        try:
            runtime_stun_profiler = RuntimeStunProfiler()
        except Exception as _runtime_stun_profiler_error:
            print(f"[runtime stun] unavailable: {_runtime_stun_profiler_error!r}", flush=True)

    # Runtime sheet export is opt-in because compiling/exporting CSVs can stall
    # the HUD during startup. Set TVC_FD_SHEET_EXPORT=1 when a sheet rebuild is
    # needed from live scans.
    frame_data_exporter = None
    if FD_SHEET_EXPORT_ENABLED and FrameDataSpreadsheetExporter is not None:
        try:
            frame_data_exporter = FrameDataSpreadsheetExporter()
            print(
                f"[fd sheet] writing character sources to {frame_data_exporter.directory}; "
                f"compiling master to {frame_data_exporter.path}",
                flush=True,
            )
        except Exception as _frame_data_export_error:
            print(f"[fd sheet] unavailable: {_frame_data_export_error!r}", flush=True)

    def _export_frame_data_sheet(scan_rows) -> None:
        if frame_data_exporter is None:
            return
        try:
            frame_data_exporter.upsert_scan_rows(scan_rows)
        except Exception as _frame_data_export_tick_error:
            if frame_idx % 300 == 0:
                print(f"[fd sheet] update failed: {_frame_data_export_tick_error!r}", flush=True)

    def _sync_runtime_engine_profile_targets(scan_rows) -> None:
        """Give the reverse profiler only scanner-proven engine-default normals.

        This is the hard gate: direct signature rows never enter the runtime
        candidate pool, even when they happen to share an action ID with stale
        evidence in runtime_stun_profiles.json.
        """
        if runtime_stun_profiler is None:
            return
        targets = {}
        for slot_data in list(scan_rows or []):
            if not isinstance(slot_data, dict):
                continue
            try:
                char_id = int(slot_data.get("char_id") or 0)
            except Exception:
                char_id = 0
            if char_id <= 0:
                continue
            for mv in list(slot_data.get("moves") or []):
                if not isinstance(mv, dict):
                    continue
                if str(mv.get("kind") or "").lower() != "normal":
                    continue
                source = str(mv.get("stun_source") or "")
                if not bool(mv.get("runtime_profile_eligible")) and source != "engine_default_hit_level":
                    continue
                try:
                    action_id = int(mv.get("id"))
                except Exception:
                    continue
                if action_id < 0x0100:
                    continue
                try:
                    active_end = int(mv.get("active_end") or 0)
                except Exception:
                    active_end = 0
                targets[(char_id, action_id)] = {
                    "active_end": active_end if active_end > 0 else None,
                    "move_label": str(mv.get("pretty_name") or mv.get("move_name") or ""),
                }
        try:
            runtime_stun_profiler.set_engine_move_targets(targets)
        except Exception as _runtime_profile_target_error:
            if frame_idx % 300 == 0:
                print(f"[runtime stun] target sync failed: {_runtime_profile_target_error!r}", flush=True)

    # ------------------------------------------------------------------
    # Master overlay subprocess
    # ------------------------------------------------------------------
    master_overlay_proc   = None
    master_overlay_active = False
    overlay_enabled       = True
    show_interaction_card = True
    show_combo_card       = True
    show_tag_card         = True

    def _launch_master_overlay():
        nonlocal master_overlay_proc, master_overlay_active
        try:
            master_overlay_proc = subprocess.Popen(
                frozen_exe("master_overlay"),
                creationflags=(
                    subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
                ),
            )
            master_overlay_active = True
            print("[master] launched")
        except Exception as e:
            print(f"[master] launch failed: {e}")

    def _stop_master_overlay():
        nonlocal master_overlay_proc, master_overlay_active
        if master_overlay_proc and master_overlay_proc.poll() is None:
            try:
                master_overlay_proc.terminate()
            except Exception:
                pass
        master_overlay_proc = None
        master_overlay_active = False
        print("[master] stopped")

    def _check_master_overlay_proc():
        nonlocal master_overlay_proc, master_overlay_active
        if master_overlay_proc and master_overlay_proc.poll() is not None:
            master_overlay_proc = None
            master_overlay_active = False
            print("[master] closed")

    # Hitbox filter
    HITBOX_FILTER_FILE = user_data_path("hitboxes", "hitbox_filter.json")
    hitbox_slots = {"P1": False, "P2": False, "P3": False, "P4": False}
    hurtbox_slots = {"P1": False, "P2": False, "P3": False, "P4": False}
    # Defaults are all four sources enabled. They stay independent from the
    # hitbox chips so a clean visual view never removes a character's ruler.
    ruler_slots = {"P1": True, "P2": True, "P3": True, "P4": True}
    ruler_enabled = False
    # Axes are independent: Horz is the default, Vert can be added without
    # replacing it. Legacy configs below migrate into this shape.
    ruler_axes = {"horizontal": True, "vertical": False}

    try:
        if os.path.exists(HITBOX_FILTER_FILE):
            with open(HITBOX_FILTER_FILE, "r", encoding="utf-8") as f:
                _existing_filter = json.load(f)
            if isinstance(_existing_filter, dict):
                _stored_ruler_slots = _existing_filter.get("ruler_slots")
                if isinstance(_stored_ruler_slots, dict):
                    for _slot_name in ruler_slots:
                        if _slot_name in _stored_ruler_slots:
                            ruler_slots[_slot_name] = bool(_stored_ruler_slots[_slot_name])
                ruler_enabled = bool(_existing_filter.get("show_range_ruler", ruler_enabled))
                _stored_axes = _existing_filter.get("range_ruler_axes")
                if isinstance(_stored_axes, dict):
                    ruler_axes["horizontal"] = bool(_stored_axes.get("horizontal", ruler_axes["horizontal"]))
                    ruler_axes["vertical"] = bool(_stored_axes.get("vertical", ruler_axes["vertical"]))
                else:
                    # One-time migration from the old exclusive selector.
                    _stored_axis = str(_existing_filter.get("range_ruler_axis", "horizontal") or "horizontal").strip().lower()
                    ruler_axes["horizontal"] = _stored_axis != "vertical"
                    ruler_axes["vertical"] = _stored_axis == "vertical"
    except Exception:
        pass

    def _write_hitbox_filter(*, enable_range_ruler: bool = False):
        nonlocal ruler_enabled, ruler_axes
        # Keep P1/P2/P3/P4 at top-level for older overlay builds, and add
        # separate hurtbox controls for the new split layer.  The saved range
        # profiles are read-only; this only controls whether the existing
        # ground-normal ruler is visible.
        payload = dict(hitbox_slots)
        try:
            if os.path.exists(HITBOX_FILTER_FILE):
                with open(HITBOX_FILTER_FILE, "r", encoding="utf-8") as f:
                    previous = json.load(f)
                if isinstance(previous, dict):
                    # Preserve view modes owned by the overlay itself. Ruler
                    # visibility/source slots are owned by this dock and are
                    # written explicitly below.
                    for _key in ("hitbox_view_mode", "hurtbox_view_mode"):
                        if _key in previous:
                            payload[_key] = previous[_key]
        except Exception:
            pass
        payload["show_hitboxes"] = any(hitbox_slots.values())
        if enable_range_ruler:
            # A fresh Hitboxes ON action begins with saved-profile rulers on.
            # Later writes respect Ruler OFF and the separate source chips.
            ruler_enabled = True
        payload["show_range_ruler"] = bool(ruler_enabled)
        # Dynamic ghosts are retired.  Active-frame samples remain internal
        # profile data for the Horz/Vert rulers, never a second display layer.
        payload["show_range_dynamic"] = False
        payload["range_ruler_axes"] = {
            "horizontal": bool(ruler_axes.get("horizontal", True)),
            "vertical": bool(ruler_axes.get("vertical", False)),
        }
        # Keep an old single-axis value for older overlay builds. The current
        # overlay reads range_ruler_axes and can render both at once.
        payload["range_ruler_axis"] = "vertical" if (payload["range_ruler_axes"]["vertical"] and not payload["range_ruler_axes"]["horizontal"]) else "horizontal"
        payload["ruler_slots"] = dict(ruler_slots)
        payload["hurtbox_slots"] = dict(hurtbox_slots)
        payload["show_hurtboxes"] = any(hurtbox_slots.values())
        try:
            os.makedirs(os.path.dirname(HITBOX_FILTER_FILE), exist_ok=True)
            with open(HITBOX_FILTER_FILE, "w") as f:
                json.dump(payload, f)
        except Exception:
            pass

    def _write_master_control():
        payload = {
            "show_hud":       overlay_enabled,
            "show_hitboxes":  any(hitbox_slots.values()),
            "show_hurtboxes": any(hurtbox_slots.values()),
            "show_debug":     False,
            "show_interaction_card": bool(show_interaction_card),
            "show_combo_card": bool(show_combo_card),
            "show_tag_card": bool(show_tag_card),
        }
        try:
            os.makedirs(os.path.dirname(MASTER_CONTROL_FILE), exist_ok=True)
            with open(MASTER_CONTROL_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    def _sync_master_overlay_state():
        want_hitboxes = any(hitbox_slots.values())
        want_hurtboxes = any(hurtbox_slots.values())
        want_process  = overlay_enabled or want_hitboxes or want_hurtboxes
        if want_process and not master_overlay_active:
            _launch_master_overlay()
        elif not want_process and master_overlay_active:
            _stop_master_overlay()

    try:
        if os.path.exists(MASTER_CONTROL_FILE):
            with open(MASTER_CONTROL_FILE, "r", encoding="utf-8") as f:
                _existing_master = json.load(f)
            overlay_enabled = bool(_existing_master.get("show_hud", overlay_enabled))
            show_interaction_card = bool(_existing_master.get("show_interaction_card", show_interaction_card))
            show_combo_card = bool(_existing_master.get("show_combo_card", show_combo_card))
            show_tag_card = bool(_existing_master.get("show_tag_card", show_tag_card))
    except Exception:
        pass

    _write_hitbox_filter()
    _write_master_control()
    mission_mgr.write_mode_state()
    mission_mgr.write_overlay_data(render_snap_by_slot)
    _sync_master_overlay_state()

    # Debug overlay
    debug_overlay     = True
    debug_click_areas = {}
    debug_scroll_offset = 0
    debug_max_scroll    = 0
    debug_cache         = []
    DEBUG_REFRESH_EVERY = 6

    # Lower inspector workspace. Default to Normals Preview because it is the
    # most useful always-on view, while Events/Debug/Activity are available as
    # tabs without stealing vertical room.
    active_bottom_tab = "scan"
    bottom_tab_rects: dict[str, pygame.Rect] = {}
    normal_preview_mode = "none"
    normal_preview_selection: dict | None = None
    normal_preview_ui = {"controls": {}, "rows": []}
    normal_preview_offset = (0, 0)
    normal_preview_advanced_open = False
    advantage_selection: dict | None = None
    advantage_ui = {"controls": {}, "rows": [], "char_order": [], "current_char_key": None}
    advantage_offset = (0, 0)

    # Momentary write restore
    hype_restore_addr  = None
    hype_restore_ts    = 0.0
    hype_restore_orig  = 0
    special_restore_addr = None
    special_restore_ts   = 0.0
    special_restore_orig = 0

    megacrash_trainer_state = _load_megacrash_trainer_config()
    megacrash_trainer_state.setdefault("pulses", {})
    megacrash_trainer_state.setdefault("last_combo_keys", {})
    megacrash_trainer_state.setdefault("scheduled_triggers", {})
    megacrash_trainer_state.setdefault("cooldown_until", 0.0)
    megacrash_trainer_state.setdefault("target_label", MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL)
    megacrash_trainer_state.setdefault("attacker_scope", MEGACRASH_TRAINER_DEFAULT_ATTACKER_SCOPE)
    megacrash_trainer_state.setdefault("target_occurrence", MEGACRASH_TRAINER_DEFAULT_TARGET_OCCURRENCE)
    megacrash_trainer_state.setdefault("occurrence_counter", 0)
    megacrash_trainer_state.setdefault("match_occurrences", {})
    megacrash_trainer_state.setdefault("combo_counter_probes", {})
    megacrash_trainer_state.setdefault("live_combo_counter", 0)
    megacrash_trainer_state.setdefault("live_combo_counter_source", "")
    megacrash_trainer_state.setdefault("roll_count", 0)
    megacrash_trainer_state.setdefault("trigger_count", 0)

    mem_dump_state = {
        "active": False,
        "progress": 0.0,
        "label": "",
        "path": "",
        "error": "",
        "last_done_time": 0.0,
    }

    def _overseer_state_snapshot() -> dict:
        slot_state = {}
        try:
            for _slot_label in ("P1-C1", "P1-C2", "P2-C1", "P2-C2"):
                _snap = (render_snap_by_slot.get(_slot_label) or {})
                if isinstance(_snap, dict):
                    slot_state[_slot_label] = {
                        "name": _snap.get("name") or _snap.get("char_name") or "",
                        "base": int(_snap.get("base") or 0),
                        "char_id": int(_snap.get("id") or _snap.get("csv_char_id") or _snap.get("char_id") or 0),
                        "move": _snap.get("mv_label") or "",
                        "move_id": _snap.get("mv_id_display"),
                    }
                else:
                    slot_state[_slot_label] = {}
        except Exception:
            slot_state = {}
        hud_state = {}
        try:
            if get_hud_editor_runtime_state is not None:
                _h = get_hud_editor_runtime_state() or {}
                hud_state = {
                    "force_zero_as_win": bool(_h.get("force_zero_as_win", False)),
                    "use_hud": bool(_h.get("use_hud", True)),
                    "use_svm": bool(_h.get("use_svm", False)),
                    "status": str(_h.get("status") or ""),
                    "holds": {k: dict(v) for k, v in dict(_h.get("holds") or {}).items()},
                }
        except Exception as e:
            hud_state = {"error": repr(e)}
        mega_state = {
            "enabled": bool(megacrash_trainer_state.get("enabled", False)),
            "mode": str(megacrash_trainer_state.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE)),
            "chance": int(megacrash_trainer_state.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE) or 0),
            "pulse_count": len(megacrash_trainer_state.get("pulses") or {}),
            "scheduled_count": len(megacrash_trainer_state.get("scheduled_triggers") or {}),
            "cooldown_until": float(megacrash_trainer_state.get("cooldown_until", 0.0) or 0.0),
        }
        try:
            assist_state = get_assist_runtime_debug_state(dict(render_snap_by_slot))
        except Exception as e:
            assist_state = {"error": repr(e)}
        try:
            active_quick_state = {
                k: dict(v) for k, v in list(active_quick_assist_by_slot.items())
                if isinstance(v, dict)
            }
        except Exception as e:
            active_quick_state = {"error": repr(e)}
        try:
            perf_state = dict(_PERF_LAST_ELAPSED_MS)
        except Exception:
            perf_state = {}
        try:
            patch_state = runtime_pm.get_runtime_patch_state() if runtime_pm is not None else {}
        except Exception as e:
            patch_state = {"error": repr(e)}
        return {
            "hooked": True,
            "slots": slot_state,
            "megacrash": mega_state,
            "hud_editor": hud_state,
            "assist": assist_state,
            "runtime_patch_manager": patch_state,
            "perf": perf_state,
            "active_quick_assist_by_slot": active_quick_state,
        }

    def _overseer_safe_restore() -> dict:
        nonlocal megacrash_trainer_state
        assist_result = restore_assist_runtime_defaults_from_main(render_snap_by_slot)
        active_quick_assist_by_slot.clear()
        quick_assist_reapply_state.clear()
        quick_assist_slide_by_slot.clear()
        if reset_hud_editor_runtime_state is not None:
            hud_result = reset_hud_editor_runtime_state(apply_zero=False)
        else:
            hud_result = {}
        megacrash_trainer_state["enabled"] = False
        megacrash_trainer_state["chance"] = 0
        megacrash_trainer_state["pulses"] = {}
        megacrash_trainer_state["scheduled_triggers"] = {}
        megacrash_trainer_state["cooldown_until"] = 0.0
        megacrash_trainer_state["last_combo_keys"] = {}
        char_test_result = {}
        try:
            if stop_char_test is not None:
                stop_char_test()
            if restore_char_test is not None:
                char_test_result = restore_char_test()
        except Exception as e:
            char_test_result = {"error": repr(e)}
        patch_result = {}
        try:
            if runtime_pm is not None:
                patch_result = runtime_pm.clear_runtime_patch_state(clear_cache=True)
        except Exception as e:
            patch_result = {"error": repr(e)}
        try:
            _save_megacrash_trainer_config(megacrash_trainer_state)
        except Exception:
            pass
        return {"assist": assist_result, "hud": "released", "megacrash": "off", "char_test": char_test_result, "patch_manager": patch_result}

    def _overseer_hard_reset() -> dict:
        result = _overseer_safe_restore()
        clear_assist_runtime_state(clear_route_cache=True)
        quick_btn_flash.clear()
        panel_btn_flash.update({s: 0 for (s, _, _) in SLOTS})
        try:
            if runtime_pm is not None:
                result["patch_manager_hard_reset"] = runtime_pm.clear_runtime_patch_state(clear_cache=True)
        except Exception as e:
            result["patch_manager_hard_reset"] = {"error": repr(e)}
        result["route_cache"] = "cleared"
        result["ui_latches"] = "cleared"
        return result

    def _overseer_dump_state() -> str:
        import datetime
        os.makedirs("debug_dumps", exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join("debug_dumps", f"overseer_state_{stamp}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(_overseer_state_snapshot(), f, indent=2, default=str)
        return path

    running = True
    _last_mouse_buttons = (False, False, False)
    _mouse_input_warned = False
    # Occasional tooling stays tucked behind the compact Lab drawer so the
    # top strip remains focused on live overlay/collision controls.
    dock_tools_open = False

    # Extra Characters used to tick every HUD frame.  That is too aggressive
    # for character select: the game can render the wheel while the external
    # process is re-reading/re-applying guarded roster rows, which shows up as
    # select-screen flicker on some machines.  The runtime itself is guarded,
    # but the main HUD now services it at a low cadence and wakes it immediately
    # only after the button is toggled.
    char_test_next_tick = 0.0
    CHAR_TEST_TICK_INTERVAL = 0.25
    chrsel_quarantine_next_tick = 0.0
    chrsel_quarantine_active = False
    CHRSEL_QUARANTINE_TICK_INTERVAL = 0.20

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    while running:
        _frame_perf_start = time.perf_counter()
        now  = time.time()
        t_ms = pygame.time.get_ticks()
        mouse_clicked_pos = None
        mouse_right_clicked_pos = None

        # Events
        for ev in _safe_pygame_event_get():
            if _is_pygame_close_event(ev):
                running = False
            elif ev.type == pygame.VIDEORESIZE:
                screen = pygame.display.set_mode(ev.size, pygame.RESIZABLE)
            elif ev.type in {getattr(pygame, "WINDOWRESIZED", -1), getattr(pygame, "WINDOWSIZECHANGED", -2)}:
                try:
                    size = getattr(ev, "size", None) or pygame.display.get_window_size()
                    screen = pygame.display.set_mode(size, pygame.RESIZABLE)
                except Exception:
                    pass
            elif ev.type == pygame.MOUSEBUTTONDOWN:
                if ev.button == 1:
                    mouse_clicked_pos = ev.pos
                elif ev.button == 3:
                    mouse_right_clicked_pos = ev.pos
                elif ev.button == 4 and debug_overlay and active_bottom_tab == "debug":
                    if debug_scroll_offset > 0:
                        debug_scroll_offset -= 1
                elif ev.button == 5 and debug_overlay and active_bottom_tab == "debug":
                    if debug_scroll_offset < debug_max_scroll:
                        debug_scroll_offset += 1
            elif ev.type == pygame.MOUSEWHEEL and debug_overlay and active_bottom_tab == "debug":
                if ev.y > 0 and debug_scroll_offset > 0:
                    debug_scroll_offset -= 1
                elif ev.y < 0 and debug_scroll_offset < debug_max_scroll:
                    debug_scroll_offset += 1

        # Python 3.13/pygame 2.6.1 safe path: when SDL event object
        # translation is disabled, synthesize button-down edges from mouse
        # state so the GUI remains clickable without pygame.event.get().
        if not _PYGAME_EVENT_GET_ENABLED:
            try:
                buttons = tuple(bool(x) for x in pygame.mouse.get_pressed(3))
                pos = pygame.mouse.get_pos()
                if buttons[0] and not _last_mouse_buttons[0]:
                    mouse_clicked_pos = pos
                if len(buttons) > 2 and buttons[2] and not _last_mouse_buttons[2]:
                    mouse_right_clicked_pos = pos
                _last_mouse_buttons = buttons
            except Exception as e:
                if not _mouse_input_warned:
                    _mouse_input_warned = True
                    try:
                        print(f"[pygame input] mouse fallback failed: {e!r}", flush=True)
                    except Exception:
                        pass

        # Scan worker results. The tiny preview feeds the HUD; the rich
        # workbench snapshot is kept separately so edits always receive live
        # packet addresses rather than the stripped preview rows.
        if scan_worker:
            res, ts = scan_worker.get_latest()
            if res is not None and ts > last_scan_time:
                try:
                    _mode = scan_worker.last_mode() if hasattr(scan_worker, "last_mode") else "cache"
                except Exception:
                    _mode = "cache"

                if fd_patch_runtime is not None:
                    try:
                        if fd_patch_controller is not None:
                            fd_patch_controller.apply_to_scan_data(res)
                        else:
                            fd_patch_runtime.overlay_scan_data(res)
                    except Exception as e:
                        print(f"[fd patch] scan overlay/apply failed: {e!r}")

                if _mode in {"workbench", "full"}:
                    last_workbench_scan_normals = res
                    if _mode == "full":
                        print("[fd profile] full dynamic profile build completed")

                    # A click already opened a native loading shell. Never
                    # replace it merely because this result has the same *slot*
                    # label: P1-C1 might still be Ryu's old cache while the HUD
                    # already shows Jun. The binding guard requires live
                    # character/table identity before the editable window opens.
                    for _slot, _requested in list(fd_pending_window_targets.items()):
                        _current = _fd_current_live_binding(
                            _slot, last_scan_normals, render_snap_by_slot
                        )
                        if is_live_proven(_current):
                            # A roster change while the shell is open retargets
                            # the pending click to the fighter now occupying that
                            # visible button. It can never open the old fighter.
                            fd_pending_window_targets[_slot] = _current

                        _target_row = _fd_live_editable_row(
                            _slot,
                            res,
                            last_scan_normals,
                            render_snap_by_slot,
                        )
                        if _target_row is not None:
                            close_frame_data_loading_window(_slot)
                            # Pass only the validated row. fd_window selects by
                            # slot label, so this also makes accidental fallback
                            # to another cached slot impossible.
                            open_frame_data_window(_slot, [_target_row])
                            fd_pending_window_targets.pop(_slot, None)
                        elif is_live_proven(_current):
                            # The workbench result was for a different/stale
                            # roster. Request one current cache rebase; do not
                            # dynamically scan until the matching live identity
                            # is actually visible.
                            if _fd_row_needs_dynamic_profile(_slot, res) and _mode == "workbench":
                                print(f"[fd profile] no saved editable profile for {_slot}; running one background discovery pass")
                                try:
                                    scan_worker.request(force_dynamic=True)
                                except TypeError:
                                    scan_worker.request()
                            else:
                                try:
                                    scan_worker.request(workbench=True)
                                except TypeError:
                                    scan_worker.request()
                        else:
                            # Preview has not caught up with the new match yet.
                            # Refresh the compact identity first; opening a stale
                            # same-character workbench row is never acceptable.
                            try:
                                scan_worker.request()
                            except TypeError:
                                pass
                else:
                    last_scan_normals = res
                    # Once a current compact preview confirms the clicked
                    # fighter/table, request the matching rich workbench row.
                    # This is the second half of the stale-slot guard.
                    for _slot in list(fd_pending_window_targets):
                        _live = _fd_current_live_binding(
                            _slot, last_scan_normals, render_snap_by_slot
                        )
                        if is_live_proven(_live):
                            fd_pending_window_targets[_slot] = _live
                            try:
                                scan_worker.request(workbench=True)
                            except TypeError:
                                scan_worker.request()
                    # Optional workbench prewarm is disabled by default because
                    # loading the full editable profile cache can stutter startup.
                    # Set TVC_FD_PREWARM_WORKBENCH=1 to restore the old behavior.
                    if FD_WORKBENCH_PREWARM_ENABLED and not fd_workbench_prewarmed:
                        fd_workbench_prewarmed = True
                        try:
                            scan_worker.request(workbench=True)
                        except TypeError:
                            pass

                    # A confirmed roster swap gets one later *cache-only*
                    # workbench read. The resulting rich rows are routed to
                    # individual character CSVs, never to a master workbook.
                    if frame_data_exporter is not None:
                        _fd_csv_sig = _fd_export_roster_signature(last_scan_normals)
                        if _fd_csv_sig and _fd_csv_sig != fd_csv_last_requested_signature:
                            fd_csv_pending_roster_signature = _fd_csv_sig
                            fd_csv_pending_since = now

                # HUD-facing consumers retain the newest rows; workbench rows
                # are never substituted into the click path unless explicitly
                # selected above.
                _sync_runtime_engine_profile_targets(res)
                last_scan_time = ts
                scan_anim = {"start": now, "dur": SCAN_SLIDE_DURATION}

        # CSV export observes every completed rich result through the worker's
        # dedicated queue.  A full profile scan can finish and then be replaced
        # by a compact HUD refresh before ``get_latest`` is polled; exporting
        # from the queue prevents that successful profile (notably Yatterman-2)
        # from being silently lost.  This never schedules scans or touches the
        # frame-data compiler itself.
        if scan_worker and frame_data_exporter is not None:
            try:
                _rich_results = scan_worker.drain_completed_rich_results()
            except AttributeError:
                _rich_results = []
            except Exception as _frame_sheet_drain_error:
                _rich_results = []
                if frame_idx % 300 == 0:
                    print(f"[fd sheet] rich-result drain failed: {_frame_sheet_drain_error!r}", flush=True)
            for _rich_generation, _rich_rows, _rich_ts, _rich_mode in _rich_results:
                try:
                    _export_frame_data_sheet(_rich_rows)
                    print(
                        f"[fd sheet] exported completed {_rich_mode} scan "
                        f"#{_rich_generation}",
                        flush=True,
                    )
                except Exception as _frame_sheet_rich_export_error:
                    if frame_idx % 300 == 0:
                        print(f"[fd sheet] completed scan export failed: {_frame_sheet_rich_export_error!r}", flush=True)

        # Resolve slot bases
        resolved_slots = resolve_bases(last_base_by_ptr, y_off_by_base)
        p1c1_base = next((b for n, t, b in resolved_slots if n == "P1-C1" and b), None)
        p2c1_base = next((b for n, t, b in resolved_slots if n == "P2-C1" and b), None)
        meter_p1 = read_meter(p1c1_base, teamtag="P1")
        meter_p2 = read_meter(p2c1_base, teamtag="P2")

        # Build snapshots
        snaps = {}
        for slotname, teamtag, base in resolved_slots:
            if not base:
                if last_char_by_slot.get(slotname) is not None:
                    anim_queue_after_scan.add((slotname, "fadeout"))
                    last_char_by_slot[slotname] = None
                    need_rescan_normals = True
                continue

            yoff = y_off_by_base.get(base, 0xF4)
            snap = safe_read_fighter(base, yoff)
            if not snap:
                continue

            snap["base"]     = base
            snap["teamtag"]  = teamtag
            snap["slotname"] = slotname

            blk = rbytes(base, FIGHTER_BLOCK_SIZE)

            true_id_current = None
            if blk:
                true_id_current = u32be_from_block(blk, OFF_CHAR_ID)
            if true_id_current in (None, 0):
                try:
                    true_id_current = rd32(base + OFF_CHAR_ID)
                except Exception:
                    true_id_current = None

            meta = char_meta_by_base.get(base)
            if meta is None or meta.get("id") != true_id_current:
                name_cached   = CHAR_NAMES.get(true_id_current)
                csv_id_cached = CHAR_ID_CORRECTION.get(name_cached, true_id_current)
                char_meta_by_base[base] = {
                    "id": true_id_current,
                    "name": name_cached,
                    "csv_char_id": csv_id_cached,
                }

            meta = char_meta_by_base.get(base)
            if meta:
                snap["id"]          = meta["id"]
                snap["name"]        = meta["name"]
                snap["csv_char_id"] = meta["csv_char_id"]
            else:
                snap["csv_char_id"] = true_id_current

            csv_char_id = snap.get("csv_char_id")
            cur_anim    = snap.get("attA") or snap.get("attB")
            mv_label    = lookup_move_name(cur_anim, csv_char_id)
            if not mv_label:
                mv_label = move_label_for(cur_anim, csv_char_id, move_map, global_map)

            snap["mv_label"]      = mv_label
            snap["mv_id_display"] = cur_anim

            active_start, active_end = _scan_move_window_for_slot(slotname, cur_anim)
            snap["active_start"] = active_start
            snap["active_end"]   = active_end
            last_move_anim_id[base] = cur_anim

            pool_byte = snap.get("hp_pool_byte")
            if pool_byte is not None:
                prev_max = pool_baseline.get(base, 0)
                if pool_byte > prev_max:
                    pool_baseline[base] = pool_byte
                max_pool = pool_baseline.get(base, 1)
                snap["pool_pct"] = (pool_byte / max_pool) * 100.0 if max_pool else 0.0
            else:
                snap["pool_pct"] = 0.0

            max_hp_stat = snap.get("max") or 0
            hp32 = 0
            pool32 = 0
            if blk:
                tmp_hp   = u32be_from_block(blk, HP32_OFF)
                tmp_pool = u32be_from_block(blk, POOL32_OFF)
                if tmp_hp   is not None: hp32   = tmp_hp
                if tmp_pool is not None: pool32 = tmp_pool
            if hp32   == 0: hp32   = rd32(base + HP32_OFF)   or 0
            if pool32 == 0: pool32 = rd32(base + POOL32_OFF) or 0

            ready_local  = False
            red_amt      = 0
            red_pct_max  = 0.0
            if hp32 and pool32 and hp32 != pool32:
                ready_local = True
                bigger  = max(hp32, pool32)
                smaller = min(hp32, pool32)
                red_amt = bigger - smaller
                if max_hp_stat:
                    red_pct_max = (red_amt / float(max_hp_stat)) * 100.0

            snap["baroque_local_hp32"]   = hp32
            snap["baroque_local_pool32"] = pool32
            snap["baroque_ready_local"]  = ready_local
            snap["baroque_red_amt"]      = red_amt
            snap["baroque_red_pct_max"]  = red_pct_max

            baroque_peak_by_base[base] = max(red_pct_max, baroque_peak_by_base.get(base, 0.0))
            baroque_drop_pct   = baroque_peak_by_base[base] - red_pct_max
            raw_baroque_cancel = baroque_drop_pct >= 1.0

            if raw_baroque_cancel:
                baroque_latch_by_base[base] = 5
            else:
                baroque_latch_by_base[base] = max(
                    0, int(baroque_latch_by_base.get(base, 0)) - 1
                )

            snap["baroque_cancel_raw"]         = raw_baroque_cancel
            snap["baroque_cancel_latched"]     = int(baroque_latch_by_base.get(base, 0)) > 0
            snap["baroque_cancel_latch_frames"] = int(baroque_latch_by_base.get(base, 0))

            last_baroque_pct_by_base[base]   = float(red_pct_max)
            if raw_baroque_cancel:
                baroque_peak_by_base[base] = float(red_pct_max)
            last_baroque_ready_by_base[base] = bool(ready_local)

            snap["meter"] = meter_p1 if teamtag == "P1" else meter_p2

            # Lightweight state-driven UI effects.
            slot_fx = panel_fx_state.setdefault(slotname, {})
            hp_frac_now = _panel_bar_fraction(snap.get("cur") or 0, snap.get("max") or 0)
            prev_hp_frac = prev_hp_frac_by_slot.get(slotname, hp_frac_now)
            if hp_frac_now < (prev_hp_frac - 0.0005):
                slot_fx["hp_loss"] = {"start": now, "dur": 0.55, "from_frac": prev_hp_frac, "to_frac": hp_frac_now}
            prev_hp_frac_by_slot[slotname] = hp_frac_now

            try:
                meter_now = int(float(snap.get("meter") or 0))
            except Exception:
                meter_now = 0
            prev_meter = prev_meter_by_slot.get(slotname, meter_now)
            if meter_now > prev_meter:
                slot_fx["meter_gain"] = {"start": now, "dur": 0.45, "delta": meter_now - prev_meter}
            prev_meter_by_slot[slotname] = meter_now

            move_key = (snap.get("mv_id_display"), str(snap.get("mv_label") or ""))
            prev_move = prev_move_by_slot.get(slotname)
            if prev_move is not None and prev_move != move_key:
                slot_fx["move_pulse"] = {"start": now, "dur": 0.34}
                slot_fx["row_sweep"] = {"start": now, "dur": 0.36}
            prev_move_by_slot[slotname] = move_key

            ready_now = bool(snap.get("baroque_ready_local", False))
            if ready_now and not bool(prev_baroque_ready_by_slot.get(slotname, False)):
                slot_fx["baroque_ready"] = {"start": now, "dur": 0.55}
            prev_baroque_ready_by_slot[slotname] = ready_now

            ko_now = bool((("ko" in str(snap.get("mv_label") or "").lower()) or ((snap.get("cur") or 0) <= 0)))
            if ko_now and not bool(prev_ko_by_slot.get(slotname, False)):
                slot_fx["ko_fade"] = {"start": now, "dur": 0.40}
            prev_ko_by_slot[slotname] = ko_now

            if teamtag == "P1":
                inputs_struct = {}
                for key, addr in INPUT_MONITOR_ADDRS.items():
                    v = rd8(addr)
                    inputs_struct[key] = 0 if v is None else v
                snap["inputs"] = inputs_struct
            else:
                snap["inputs"] = {}

            snaps[slotname] = snap

            if last_char_by_slot.get(slotname) != snap.get("name"):
                last_char_by_slot[slotname] = snap.get("name")
                anim_queue_after_scan.add((slotname, "fadein"))
                # Stagger the internal card elements after the shell starts
                # sliding in.  This keeps the first appearance premium without
                # running any extra scanner work.
                slot_fx["panel_enter"] = {"start": now + 0.18, "dur": 0.74}
                need_rescan_normals = True

            render_snap_by_slot[slotname]    = snap
            render_portrait_by_slot[slotname] = get_portrait_for_snap(
                snap, portraits, placeholder_portrait
            )

        # Giant normalisation
        p1_giant_solo, p2_giant_solo = compute_team_giant_solo(snaps)
        if p1_giant_solo or p2_giant_solo:
            snaps = reassign_slots_for_giants(snaps)

        # Character-select write quarantine blocks battle-only tool writes while
        # Extra Characters and Solo Team are inactive. The barrier is global so
        # direct and managed memory writers share the same scene gate.
        if now >= chrsel_quarantine_next_tick:
            try:
                _chrsel_active = bool(is_character_select_active()) if is_character_select_active is not None else False
            except Exception:
                _chrsel_active = False
            _extras_armed = False
            try:
                if get_char_test_state is not None:
                    _chrsel_state = get_char_test_state() or {}
                    _extras_armed = bool(
                        _chrsel_state.get("extra_characters_requested")
                        or _chrsel_state.get("solo_team_requested")
                    )
            except Exception:
                _extras_armed = False
            chrsel_quarantine_active = bool(_chrsel_active and not _extras_armed)
            try:
                set_emulated_write_quarantine(
                    chrsel_quarantine_active,
                    reason="character_select_extra_off" if chrsel_quarantine_active else "",
                )
            except Exception:
                pass
            chrsel_quarantine_next_tick = now + CHRSEL_QUARANTINE_TICK_INTERVAL

        # Runtime stun profiler: after a live target counter is armed, keep the
        # actual resolver result tied to the recent opposing move.  This has no
        # write path and only schedules a light cache refresh when new evidence
        # lands, so existing static profiles remain untouched.
        if runtime_stun_profiler is not None:
            try:
                if runtime_stun_profiler.update(snaps, frame=frame_idx, now=now):
                    need_rescan_normals = True
            except Exception as _runtime_stun_tick_error:
                if frame_idx % 300 == 0:
                    print(f"[runtime stun] tick failed: {_runtime_stun_tick_error!r}", flush=True)

        # Assist selector runtime hook. The assist scanner stores per-fighter
        # desired assists; main.py owns the reliable current move label/id, so
        # when a fighter enters assist attack (426), patch that fighter profile
        # into the shared character selector table immediately.
        _perf_section_start = time.perf_counter()
        try:
            tick_assist_profiles_from_main(snaps)
        except Exception as e:
            if frame_idx % 60 == 0:
                print(f"[assist scanner] main trigger failed: {e!r}")
        _perf_warn("assist_tick", _perf_section_start)

        _perf_section_start = time.perf_counter()
        try:
            _tick_persistent_quick_assists(snaps, frame_idx)
        except Exception as e:
            if frame_idx % 60 == 0:
                print(f"[assist quick] persistent state failed: {e!r}")
        _perf_warn("assist_persist", _perf_section_start)

        # Mission manager tick
        _perf_section_start = time.perf_counter()
        mission_mgr.update(snaps, render_snap_by_slot, frame_idx, now)
        _perf_warn("mission_tick", _perf_section_start)

        # Mission-scoped Megacrash setup.  Joe Condor's counter trials can now
        # temporarily arm a targeted burst on 5C without making Megacrash stay
        # enabled globally or on the next launch.
        megacrash_trainer_state = _sync_mission_megacrash_trainer(
            megacrash_trainer_state,
            mission_mgr.last_overlay_payload,
        )

        # Megacrash Trainer tick: point-only, hitstun-only, per-new-combo-label dice roll.
        _perf_section_start = time.perf_counter()
        megacrash_trainer_state = _tick_megacrash_trainer(megacrash_trainer_state, snaps, now, frame_idx)
        _perf_warn("megacrash_tick", _perf_section_start)

        # KO lab v4: learn a rolling last-good pre-KO frame for each live slot.
        ko_rewind_baseline_by_slot = capture_ko_rewind_baselines(snaps, render_snap_by_slot, ko_rewind_baseline_by_slot)

        # KO Ctrl auto-mode: while armed, only apply the DOL input/control packet
        # after one whole team is KO'd.  Restore originals as soon as both teams
        # are live/new match begins, preventing P1 input from leaking into CPU
        # control during the next arcade match.
        try:
            _old_live = bool(ko_control_live_active)
            ko_control_live_active, ko_control_last_apply, _ko_auto_result, ko_control_auto_state = tick_ko_control_auto(
                bool(ko_control_full_enabled),
                bool(ko_control_live_active),
                snaps,
                now,
                ko_control_last_apply,
                verify=False,
            )
            if _ko_auto_result is not None:
                _phase = "ACTIVE" if ko_control_live_active else "RESTORED"
                idle_restore_status = {
                    "text": f"KO Ctrl auto {_phase}: {ko_control_auto_state.get('summary', '')} {ko_control_auto_state.get('auto_mode', '')}",
                    "until": now + 2.0,
                }
                if _old_live != bool(ko_control_live_active):
                    print(f"[ko control] auto {_phase}: {ko_control_auto_state.get('summary', '')}", flush=True)
        except Exception as _e:
            if frame_idx % 60 == 0:
                print(f"[ko control] auto tick failed: {_e!r}", flush=True)

        # KO lab: keep reapplying the rewind packet for a short burst after the
        # click.  This makes it obvious the button fired and fights the
        # post-KO manager reasserting winner/loser states every frame.
        for _slot_label, _hold_info in list(idle_restore_hold_until_by_slot.items()):
            if isinstance(_hold_info, dict):
                _until = float(_hold_info.get("until") or 0.0)
                _hold_kind = str(_hold_info.get("kind") or "ko_rescue")
            else:
                _until = float(_hold_info or 0.0)
                _hold_kind = "ko_rescue"
            if now > _until:
                idle_restore_hold_until_by_slot.pop(_slot_label, None)
                continue
            _bases_by_slot = {}
            for _sl, _snap_obj in (snaps or {}).items():
                if isinstance(_snap_obj, dict):
                    try:
                        _bases_by_slot[_sl] = int(_snap_obj.get("base") or 0)
                    except Exception:
                        pass
            for _sl, _snap_obj in (render_snap_by_slot or {}).items():
                if isinstance(_snap_obj, dict):
                    try:
                        _bases_by_slot[_sl] = int(_snap_obj.get("base") or 0)
                    except Exception:
                        pass
            if _hold_kind in KO_GLOBAL_HOLD_GROUPS:
                _result = apply_ko_global_hold(_hold_info.get("global_baseline") if isinstance(_hold_info, dict) else ko_global_hold_baseline, verify=False)
            elif _hold_kind in {"slot_flags", "slot_flags_action", "slot_clear_result"}:
                _result = apply_slot_only_ko_hold(_slot_label, _bases_by_slot, ko_rewind_baseline_by_slot, hold_kind=_hold_kind, verify=False)
            elif str(_hold_kind).startswith("input_inject"):
                _result = apply_ko_input_inject(_slot_label, _bases_by_slot, mode=_hold_kind, verify=False)
            else:
                _result = apply_ko_rescue_packet(_slot_label, _bases_by_slot, verify=False, baseline_by_slot=ko_rewind_baseline_by_slot)
            idle_restore_status = {"text": idle_restore_status_text(_result, held=True), "until": now + 1.25}

        _win_counter_chrsel_active = False
        try:
            _win_counter_chrsel_active = bool(is_character_select_active()) if is_character_select_active is not None else False
        except Exception:
            _win_counter_chrsel_active = False

        _win_counter_teams = {"P1": False, "P2": False}
        for _win_counter_snap in (snaps or {}).values():
            if not isinstance(_win_counter_snap, dict):
                continue
            _win_counter_team = str(_win_counter_snap.get("teamtag") or "").upper()
            if _win_counter_team not in _win_counter_teams:
                continue
            try:
                _win_counter_base = int(_win_counter_snap.get("base") or 0)
                _win_counter_hp_max = int(_win_counter_snap.get("max") or 0)
            except Exception:
                continue
            if _win_counter_base and _win_counter_hp_max > 0:
                _win_counter_teams[_win_counter_team] = True

        _win_counter_match_active = bool(
            not _win_counter_chrsel_active
            and _win_counter_teams["P1"]
            and _win_counter_teams["P2"]
        )
        # Yami's appended roster route does not share the normal HUD resource
        # lifetime.  The Win Counter writes texture-pane addresses, so fail
        # closed before any write/freeze tick when a Yami is in either team.
        _win_counter_unsafe_ids = set()
        for _win_counter_snap in (snaps or {}).values():
            if not isinstance(_win_counter_snap, dict):
                continue
            try:
                _win_counter_char_id = int(_win_counter_snap.get("id") or 0)
            except Exception:
                _win_counter_char_id = 0
            if _win_counter_char_id in (0x17, 0x18, 0x19):
                _win_counter_unsafe_ids.add(_win_counter_char_id)
        if set_win_counter_runtime_active is not None:
            try:
                set_win_counter_runtime_active(
                    _win_counter_match_active,
                    reason="active_match" if _win_counter_match_active else "outside_match",
                    unsafe_character_ids=tuple(sorted(_win_counter_unsafe_ids)),
                )
            except TypeError:
                # Compatibility with an older gate module during partial source
                # updates. The current module receives the Yami IDs above.
                set_win_counter_runtime_active(
                    _win_counter_match_active and not bool(_win_counter_unsafe_ids),
                    reason="yami_safeguard" if _win_counter_unsafe_ids else ("active_match" if _win_counter_match_active else "outside_match"),
                )
            except Exception:
                pass

        # HUD Editor persistent state owns only live match HUD values.
        if tick_hud_editor_state is not None:
            _perf_section_start = time.perf_counter()
            try:
                tick_hud_editor_state(now=now)
            except Exception as e:
                if frame_idx % 60 == 0:
                    print(f"[hud editor] persistent tick failed: {e!r}")
            _perf_warn("hud_editor_tick", _perf_section_start)

        # Stage Select capture service is independent from Extra Characters.
        # It only performs queued read-only scene snapshots.
        if tick_stage_probe is not None:
            try:
                tick_stage_probe()
            except Exception as e:
                if frame_idx % 60 == 0:
                    print(f"[stage select] capture tick failed: {e!r}")

        # Character-select runtime service is enabled only while a request
        # or queued action is present. The disabled state does not access
        # character-select memory.
        if (
            tick_char_test is not None
            and char_test_needs_service is not None
            and char_test_needs_service()
            and now >= char_test_next_tick
        ):
            _perf_section_start = time.perf_counter()
            try:
                tick_char_test()
            except Exception as e:
                if frame_idx % 60 == 0:
                    print(f"[char test] tick failed: {e!r}")
            finally:
                char_test_next_tick = now + CHAR_TEST_TICK_INTERVAL
            _perf_warn("char_test_tick", _perf_section_start)

        # Damage / hit logging
        if frame_idx % DAMAGE_EVERY_FRAMES == 0:
            for vic_slot, vic_snap in snaps.items():
                vic_move_id = vic_snap.get("attA") or vic_snap.get("attB")
                if vic_move_id not in REACTION_STATES:
                    continue

                vic_team = vic_snap["teamtag"]
                attackers = [s for s in snaps.values() if s["teamtag"] != vic_team]
                if not attackers:
                    continue

                best_d2  = None
                atk_snap = None
                for cand in attackers:
                    d2v = dist2(vic_snap, cand)
                    if best_d2 is None or d2v < best_d2:
                        best_d2  = d2v
                        atk_snap = cand
                if not atk_snap:
                    continue

                atk_move_id    = atk_snap.get("attA") or atk_snap.get("attB")
                atk_move_label = atk_snap.get("mv_label")

                ADV_TRACK.start_contact(
                    atk_snap["base"], vic_snap["base"],
                    frame_idx, atk_move_id, vic_move_id,
                )

                base      = vic_snap["base"]
                hp_now    = vic_snap["cur"]
                hp_prev   = prev_hp.get(base, hp_now)
                prev_hp[base] = hp_now
                dmg = hp_prev - hp_now
                if dmg >= MIN_HIT_DAMAGE:
                    log_engaged(atk_snap, vic_snap, frame_idx)
                    log_hit(atk_snap, vic_snap, dmg, frame_idx, atk_move_label, atk_move_id)

        if frame_idx % ADV_EVERY_FRAMES == 0:
            pairs = [
                ("P1-C1", "P2-C1"), ("P1-C1", "P2-C2"),
                ("P1-C2", "P2-C1"), ("P1-C2", "P2-C2"),
                ("P2-C1", "P1-C1"), ("P2-C1", "P1-C2"),
                ("P2-C2", "P1-C1"), ("P2-C2", "P1-C2"),
            ]
            for atk_slot, vic_slot in pairs:
                atk_snap = snaps.get(atk_slot)
                vic_snap = snaps.get(vic_slot)
                if atk_snap and vic_snap:
                    ADV_TRACK.update_pair(
                        atk_snap["base"], vic_snap["base"], frame_idx,
                        atk_snap.get("attA") or atk_snap.get("attB"),
                        vic_snap.get("attA") or vic_snap.get("attB"),
                    )

            freshest = ADV_TRACK.get_freshest_final_info()
            if freshest:
                atk_b, vic_b, plusf, fin_frame = freshest
                if abs(plusf) <= 64:
                    atk_obj = next((s for s in snaps.values() if s["base"] == atk_b), None)
                    vic_obj = next((s for s in snaps.values() if s["base"] == vic_b), None)
                    if atk_obj and vic_obj:
                        last_adv_display = (
                            f"{atk_obj['slotname']}({atk_obj['name']}) vs "
                            f"{vic_obj['slotname']}({vic_obj['name']}): "
                            f"{plusf:+.1f}f"
                        )
                        log_frame_advantage(atk_obj, vic_obj, plusf)
                    else:
                        last_adv_display = f"Frame adv: {plusf:+.1f}f"

        # ------------------------------------------------------------------
        # Rendering
        # ------------------------------------------------------------------
        screen.fill(COL_BG)
        w, h  = screen.get_size()
        layout = compute_layout(w, h - TOP_UI_RESERVED, snaps)

        for key, value in layout.items():
            if isinstance(value, pygame.Rect):
                value.y += TOP_UI_RESERVED

        # Give the character panels a dedicated footer area for Quick Assists.
        # This keeps the assist buttons from crowding the move text or the
        # Frame Data / Mission Mode buttons, without making main.py own any
        # assist logic. The lower HUD areas are shifted down and the scan
        # preview absorbs the height loss.
        qa_panel_extra = 26 if h >= 700 else 18
        if qa_panel_extra > 0:
            for _key in ("p1c1", "p2c1"):
                _rect = layout.get(_key)
                if isinstance(_rect, pygame.Rect):
                    _rect.height += qa_panel_extra

            for _key in ("p1c2", "p2c2"):
                _rect = layout.get(_key)
                if isinstance(_rect, pygame.Rect):
                    _rect.y += qa_panel_extra
                    _rect.height += qa_panel_extra

            qa_total_shift = qa_panel_extra * 2
            for _key in ("act", "events", "debug", "scan"):
                _rect = layout.get(_key)
                if isinstance(_rect, pygame.Rect):
                    _rect.y += qa_total_shift

            _scan_rect = layout.get("scan")
            if isinstance(_scan_rect, pygame.Rect):
                _scan_rect.height = max(54, _scan_rect.height - qa_total_shift)

        layout["p1_is_giant"] = bool(p1_giant_solo)
        layout["p2_is_giant"] = bool(p2_giant_solo)

        # Panel animations
        if anim_queue_after_scan:
            slot_rect_lookup = {
                "P1-C1": layout["p1c1"],
                "P2-C1": layout["p2c1"],
                "P1-C2": layout["p1c2"],
                "P2-C2": layout["p2c2"],
            }
            for slot_label, kind in list(anim_queue_after_scan):
                base_rect = slot_rect_lookup.get(slot_label)
                if base_rect is None:
                    anim_queue_after_scan.discard((slot_label, kind))
                    continue

                panel_height = base_rect.height
                offscreen_y  = -panel_height - 8
                anim = {
                    "start":  now,
                    "dur":    PANEL_SLIDE_DURATION,
                    "from_y": None,
                    "to_y":   None,
                    "from_a": 255,
                    "to_a":   255,
                }
                if kind == "fadein":
                    anim["from_y"] = offscreen_y
                    anim["to_y"]   = base_rect.y
                    anim["from_a"] = 0
                    anim["to_a"]   = 255
                elif kind == "fadeout":
                    anim["from_y"] = base_rect.y
                    anim["to_y"]   = offscreen_y
                    anim["from_a"] = 255
                    anim["to_a"]   = 0
                else:
                    anim_queue_after_scan.discard((slot_label, kind))
                    continue

                panel_anim[slot_label] = anim
                anim_queue_after_scan.discard((slot_label, kind))

        def anim_rect_and_alpha(slot_label, base_rect):
            anim = panel_anim.get(slot_label)
            if not anim:
                return base_rect, 255

            if anim.get("to_y") is None:
                anim["to_y"] = base_rect.y
            if anim.get("from_y") is None:
                anim["from_y"] = base_rect.y

            t    = now - anim["start"]
            dur  = anim.get("dur") or PANEL_SLIDE_DURATION
            frac = max(0.0, min(1.0, t / dur)) if dur else 1.0

            eased_frac = _ease_out_cubic(frac)
            y = anim["from_y"] + (anim["to_y"] - anim["from_y"]) * eased_frac

            from_a = anim.get("from_a", 255)
            to_a   = anim.get("to_a",   255)
            if from_a == 0 and to_a > 0:
                alpha = int(from_a + (to_a - from_a) * eased_frac)
            else:
                alpha = int(from_a + (to_a - from_a) * frac)

            if frac >= 1.0:
                if to_a == 0:
                    render_snap_by_slot.pop(slot_label, None)
                    render_portrait_by_slot.pop(slot_label, None)
                panel_anim.pop(slot_label, None)

            r = base_rect.copy()
            r.y = int(y)
            return r, max(0, min(255, alpha))

        # Top command dock
        _check_master_overlay_proc()
        mx_h, my_h = pygame.mouse.get_pos()

        hb_btn_rect, hurt_btn_rect, ps_btn_rect, as_btn_rect, hud_btn_rect, megacrash_btn_rect, memdump_btn_rect, win_counter_btn_rect, overseer_btn_rect, select_probe_btn_rect, yami_stage_btn_rect, input_spoof_btn_rect, action_force_btn_rect, ko_control_btn_rect, solo_team_btn_rect, interaction_card_btn_rect, combo_card_btn_rect, tag_card_btn_rect, clear_card_btn_rect, tools_btn_rect, hb_filter_rects, hurt_filter_rects, ruler_btn_rect, ruler_axis_h_rect, ruler_axis_v_rect, ruler_filter_rects = draw_top_command_dock(
            screen,
            smallfont,
            hitbox_slots=hitbox_slots,
            hurtbox_slots=hurtbox_slots,
            ruler_slots=ruler_slots,
            ruler_enabled=bool(ruler_enabled),
            ruler_axes=dict(ruler_axes),
            overlay_enabled=overlay_enabled,
            show_interaction_card=show_interaction_card,
            show_combo_card=show_combo_card,
            show_tag_card=show_tag_card,
            megacrash_trainer_enabled=bool(megacrash_trainer_state.get("enabled", False)),
            megacrash_trainer_chance=int(megacrash_trainer_state.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE)),
            megacrash_trainer_mode=str(megacrash_trainer_state.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE)),
            megacrash_trainer_delay_frames=int(megacrash_trainer_state.get("delay_frames", MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES)),
            mem_dump_active=bool(mem_dump_state.get("active", False)),
            mem_dump_label=str(mem_dump_state.get("label") or ""),
            win_score_enabled=_win_score_active_for_dock(),
            char_test_active=_char_test_active_for_dock(),
            ko_control_enabled=bool(ko_control_full_enabled),
            ko_control_live_active=bool(ko_control_live_active),
            solo_team_active=_solo_team_active_for_dock(),
            tools_open=bool(dock_tools_open),
            mouse_pos=(mx_h, my_h),
            t_ms=t_ms,
        )

        # Panel rects
        r_p1c1, a_p1c1 = anim_rect_and_alpha("P1-C1", layout["p1c1"])
        r_p2c1, a_p2c1 = anim_rect_and_alpha("P2-C1", layout["p2c1"])
        r_p1c2, a_p1c2 = anim_rect_and_alpha("P1-C2", layout["p1c2"])
        r_p2c2, a_p2c2 = anim_rect_and_alpha("P2-C2", layout["p2c2"])

        quick_btn_areas = {}

        # If an entire team is KO'd, let the winning team's slots pulse a bit.
        def _snap_is_ko(_snap):
            if not isinstance(_snap, dict):
                return False
            try:
                if int(_snap.get("cur") or 0) <= 0:
                    return True
            except Exception:
                pass
            _mv = str(_snap.get("mv_label") or "").strip().lower()
            return ("ko" in _mv) or ("k.o" in _mv) or ("dead" in _mv) or ("defeat" in _mv)

        victory_slots = set()
        _p1c1 = render_snap_by_slot.get("P1-C1")
        _p1c2 = render_snap_by_slot.get("P1-C2")
        _p2c1 = render_snap_by_slot.get("P2-C1")
        _p2c2 = render_snap_by_slot.get("P2-C2")
        p1_team_ko = _snap_is_ko(_p1c1) and _snap_is_ko(_p1c2)
        p2_team_ko = _snap_is_ko(_p2c1) and _snap_is_ko(_p2c2)
        if p1_team_ko and not p2_team_ko:
            victory_slots.update(("P2-C1", "P2-C2"))
        elif p2_team_ko and not p1_team_ko:
            victory_slots.update(("P1-C1", "P1-C2"))

        def blit_panel_with_buttons(panel_rect, slot_label, alpha, header):
            snap     = render_snap_by_slot.get(slot_label)
            portrait = render_portrait_by_slot.get(slot_label, placeholder_portrait)

            surf = pygame.Surface((panel_rect.width, panel_rect.height), pygame.SRCALPHA)

            current_assist_label = "--"
            _active_row_for_panel = active_quick_assist_by_slot.get(slot_label)
            if isinstance(_active_row_for_panel, dict):
                _active_char_id = int(_active_row_for_panel.get("char_id") or 0)
                _snap_char_id = 0
                if isinstance(snap, dict):
                    for _field in ("id", "csv_char_id", "char_id"):
                        try:
                            _snap_char_id = int(snap.get(_field) or 0)
                        except Exception:
                            _snap_char_id = 0
                        if _snap_char_id:
                            break
                if _active_char_id == 0 or _snap_char_id == 0 or _active_char_id == _snap_char_id:
                    current_assist_label = str(_active_row_for_panel.get("label") or "--")

            slot_panel_fx = {
                **panel_fx_state.get(slot_label, {}),
                "victory_pulse_live": (slot_label in victory_slots),
            }

            draw_panel_polished_stats(
                surf,
                surf.get_rect(),
                snap,
                portrait,
                font,
                smallfont,
                header,
                t_ms,
                assist_label=current_assist_label,
                panel_fx=slot_panel_fx,
            )

            btn_h          = 20
            frame_btn_w    = 98
            mission_btn_w  = 124
            btn_gap        = 7
            bottom_pad     = 8
            total_btn_w    = frame_btn_w + btn_gap + mission_btn_w
            btn_x          = panel_rect.width - total_btn_w - 10
            if btn_x < 10:
                # Very narrow window fallback: keep the buttons visible instead
                # of letting them disappear off the left edge.
                btn_x = 10
            btn_y          = panel_rect.height - btn_h - bottom_pad

            frame_btn_local   = pygame.Rect(btn_x, btn_y, frame_btn_w, btn_h)
            mission_btn_local = pygame.Rect(frame_btn_local.right + btn_gap, btn_y, mission_btn_w, btn_h)

            mx, my       = pygame.mouse.get_pos()
            mx_local     = mx - panel_rect.x
            my_local     = my - panel_rect.y
            frame_hover  = frame_btn_local.collidepoint(mx_local, my_local)
            mission_hover = mission_btn_local.collidepoint(mx_local, my_local)
            flash_left   = panel_btn_flash.get(slot_label, 0)

            draw_glass_button(
                surf,
                frame_btn_local,
                "Frame Data",
                smallfont,
                active=flash_left > 0,
                hover=frame_hover,
                accent=GUI_APP_ACCENT,
                fill=(44, 56, 82) if flash_left > 0 else (31, 33, 42),
                align="center",
            )

            draw_glass_button(
                surf,
                mission_btn_local,
                "Mission Mode",
                smallfont,
                active=(mission_mgr.active_slot == slot_label),
                hover=mission_hover,
                accent=GUI_APP_ACCENT,
                fill=(44, 56, 82) if mission_mgr.active_slot == slot_label else (31, 33, 42),
                align="center",
            )

            # Optional quick-assist buttons. main.py only draws/clicks these;
            # assist_scanner_window owns the JSON, route resolution, and writes.
            active_quick_index = None
            active_row = active_quick_assist_by_slot.get(slot_label)
            if isinstance(active_row, dict):
                active_char_id = int(active_row.get("char_id") or 0)
                snap_char_id = 0
                if isinstance(snap, dict):
                    for _field in ("id", "csv_char_id", "char_id"):
                        try:
                            snap_char_id = int(snap.get(_field) or 0)
                        except Exception:
                            snap_char_id = 0
                        if snap_char_id:
                            break
                if active_char_id == 0 or snap_char_id == 0 or active_char_id == snap_char_id:
                    try:
                        active_quick_index = int(active_row.get("quick_index"))
                    except Exception:
                        active_quick_index = None

            flash_quick_index = None
            for (_slot, _qi), _frames in list(quick_btn_flash.items()):
                if _slot == slot_label and int(_frames or 0) > 0:
                    flash_quick_index = int(_qi)
                    break

            quick_btn_areas.update(
                draw_quick_assist_footer(
                    surf,
                    panel_rect,
                    slot_label,
                    snap,
                    smallfont,
                    mx_local=mx_local,
                    my_local=my_local,
                    btn_y=btn_y,
                    get_quick_defs_fn=get_quick_assists_for_slot,
                    active_quick_index=active_quick_index,
                    flash_quick_index=flash_quick_index,
                    slide_anim=quick_assist_slide_by_slot.get(slot_label),
                )
            )

            surf = _apply_panel_element_enter_animation(surf, slot_panel_fx, now)
            surf.set_alpha(alpha)
            screen.blit(surf, (panel_rect.x, panel_rect.y))

            frame_btn_rect   = pygame.Rect(
                panel_rect.x + frame_btn_local.x,
                panel_rect.y + frame_btn_local.y,
                frame_btn_w, btn_h,
            )
            mission_btn_rect = pygame.Rect(
                panel_rect.x + mission_btn_local.x,
                panel_rect.y + mission_btn_local.y,
                mission_btn_w, btn_h,
            )
            return frame_btn_rect, mission_btn_rect

        btn_p1c1, mission_btn_p1c1 = blit_panel_with_buttons(r_p1c1, "P1-C1", a_p1c1, "P1-C1")
        btn_p2c1, mission_btn_p2c1 = blit_panel_with_buttons(r_p2c1, "P2-C1", a_p2c1, "P2-C1")

        if (not layout.get("p1_is_giant")) and ("P1-C2" in snaps):
            btn_p1c2, mission_btn_p1c2 = blit_panel_with_buttons(r_p1c2, "P1-C2", a_p1c2, "P1-C2")
        else:
            btn_p1c2        = pygame.Rect(0, 0, 0, 0)
            mission_btn_p1c2 = pygame.Rect(0, 0, 0, 0)

        if (not layout.get("p2_is_giant")) and ("P2-C2" in snaps):
            btn_p2c2, mission_btn_p2c2 = blit_panel_with_buttons(r_p2c2, "P2-C2", a_p2c2, "P2-C2")
        else:
            btn_p2c2        = pygame.Rect(0, 0, 0, 0)
            mission_btn_p2c2 = pygame.Rect(0, 0, 0, 0)

        # Bottom inspector workspace: one active tab at a time. This prevents
        # the Normals Preview from being clipped while keeping Events, Debug,
        # and Activity one click away.
        lower_keys = ("act", "events", "debug", "scan")
        lower_tops = [layout[k].y for k in lower_keys if isinstance(layout.get(k), pygame.Rect)]
        lower_top = min(lower_tops) if lower_tops else max(TOP_UI_RESERVED, int(h * 0.62))
        status_rail_h = 22
        bottom_workspace_rect = pygame.Rect(
            0,
            lower_top,
            w,
            max(60, h - status_rail_h - lower_top),
        )

        bottom_content_rect, bottom_tab_rects = draw_bottom_workspace_tabs(
            screen,
            bottom_workspace_rect,
            smallfont,
            active_bottom_tab,
            pygame.mouse.get_pos(),
        )

        debug_click_areas = {}
        debug_max_scroll = 0
        if active_bottom_tab == "advantage":
            active_bottom_tab = "scan"

        if active_bottom_tab == "scan":
            scan_rect = bottom_content_rect
            scan_surf = pygame.Surface((scan_rect.width, scan_rect.height), pygame.SRCALPHA)

            scan_display = []
            base_scan_map = {}
            try:
                if fd_patch_runtime is not None:
                    fd_patch_runtime.overlay_scan_data(last_scan_normals)
            except Exception:
                pass
            try:
                for _row in list(last_scan_normals or []):
                    if isinstance(_row, dict):
                        _lbl = str(_row.get("slot_label") or _row.get("slot") or "")
                        if _lbl:
                            base_scan_map[_lbl] = dict(_row)
            except Exception:
                base_scan_map = {}
            for _lbl in ("P1-C1", "P1-C2", "P2-C1", "P2-C2"):
                _row = dict(base_scan_map.get(_lbl, {"slot_label": _lbl, "moves": []}))
                _row["_had_scan_entry"] = (_lbl in base_scan_map)
                _snap = render_snap_by_slot.get(_lbl)
                if isinstance(_snap, dict):
                    _row["mv_id_display"] = _snap.get("mv_id_display")
                    _row["mv_label"] = _snap.get("mv_label")
                    _row["char_name"] = _snap.get("name") or _row.get("char_name")
                scan_display.append(_row)

            scan_fx = {}
            for _lbl, _fxs in panel_fx_state.items():
                if not isinstance(_fxs, dict):
                    continue
                _entry = _fxs.get("row_sweep")
                if isinstance(_entry, dict):
                    try:
                        _start = float(_entry.get("start", 0.0) or 0.0)
                        _dur = max(0.001, float(_entry.get("dur", 0.3) or 0.3))
                        _prog = max(0.0, min(1.0, (now - _start) / _dur)) if _start else 0.0
                        if _prog < 1.0:
                            scan_fx[_lbl] = {"row_sweep": _prog}
                    except Exception:
                        pass

            if scan_anim is not None:
                t    = now - scan_anim["start"]
                dur  = scan_anim.get("dur", SCAN_SLIDE_DURATION)
                frac = max(0.0, min(1.0, t / dur)) if dur else 1.0
                y    = (scan_rect.y + scan_rect.height + 8) + (scan_rect.y - (scan_rect.y + scan_rect.height + 8)) * frac
                if frac >= 1.0:
                    scan_anim = None
            else:
                y = scan_rect.y
            local_mouse = (pygame.mouse.get_pos()[0] - scan_rect.x, pygame.mouse.get_pos()[1] - int(y))
            normal_preview_ui = draw_scan_normals_polished(
                scan_surf,
                scan_surf.get_rect(),
                font,
                smallfont,
                scan_display,
                t_ms=t_ms,
                scan_fx_by_slot=scan_fx,
                highlight_mode=normal_preview_mode,
                selection=normal_preview_selection,
                mouse_pos=local_mouse,
                advanced_open=bool(normal_preview_advanced_open),
            )
            normal_preview_offset = (scan_rect.x, int(y))
            scan_surf.set_alpha(255)
            screen.blit(scan_surf, normal_preview_offset)
            advantage_ui = {"controls": {}, "rows": [], "char_order": [], "current_char_key": None}

        elif active_bottom_tab == "advantage":
            normal_preview_ui = {"controls": {}, "rows": []}
            adv_rect = bottom_content_rect
            adv_surf = pygame.Surface((adv_rect.width, adv_rect.height), pygame.SRCALPHA)

            adv_display = []
            adv_base_scan_map = {}
            try:
                if fd_patch_runtime is not None:
                    fd_patch_runtime.overlay_scan_data(last_scan_normals)
            except Exception:
                pass
            try:
                for _row in list(last_scan_normals or []):
                    if isinstance(_row, dict):
                        _lbl = str(_row.get("slot_label") or _row.get("slot") or "")
                        if _lbl:
                            adv_base_scan_map[_lbl] = dict(_row)
            except Exception:
                adv_base_scan_map = {}
            for _lbl in ("P1-C1", "P1-C2", "P2-C1", "P2-C2"):
                _row = dict(adv_base_scan_map.get(_lbl, {"slot_label": _lbl, "moves": []}))
                _snap = render_snap_by_slot.get(_lbl)
                if isinstance(_snap, dict):
                    _row["mv_id_display"] = _snap.get("mv_id_display")
                    _row["mv_label"] = _snap.get("mv_label")
                    _row["char_name"] = _snap.get("name") or _row.get("char_name")
                    if _snap.get("char_id") is not None:
                        _row["char_id"] = _snap.get("char_id")
                adv_display.append(_row)

            local_mouse = (pygame.mouse.get_pos()[0] - adv_rect.x, pygame.mouse.get_pos()[1] - adv_rect.y)
            advantage_ui = draw_advantage_window(
                adv_surf,
                adv_surf.get_rect(),
                font,
                smallfont,
                adv_display,
                selection=advantage_selection,
                mouse_pos=local_mouse,
                t_ms=t_ms,
            )
            advantage_offset = (adv_rect.x, adv_rect.y)
            adv_surf.set_alpha(255)
            screen.blit(adv_surf, advantage_offset)

        elif active_bottom_tab == "events":
            normal_preview_ui = {"controls": {}, "rows": []}
            advantage_ui = {"controls": {}, "rows": [], "char_order": [], "current_char_key": None}

            draw_event_log(screen, bottom_content_rect, font, smallfont)

        elif active_bottom_tab == "debug":
            advantage_ui = {"controls": {}, "rows": [], "char_order": [], "current_char_key": None}
            if frame_idx % DEBUG_REFRESH_EVERY == 0:
                debug_cache = merged_debug_values()
            debug_click_areas, debug_max_scroll = draw_debug_overlay(
                screen, bottom_content_rect, smallfont, debug_cache, debug_scroll_offset
            )

        elif active_bottom_tab == "activity":
            advantage_ui = {"controls": {}, "rows": [], "char_order": [], "current_char_key": None}
            draw_activity(screen, bottom_content_rect, font, last_adv_display)

        if bottom_tab_fade is not None:
            try:
                frac = max(0.0, min(1.0, (now - float(bottom_tab_fade.get("start", 0.0) or 0.0)) / max(0.001, float(bottom_tab_fade.get("dur", 0.18) or 0.18))))
            except Exception:
                frac = 1.0
            if frac >= 1.0:
                bottom_tab_fade = None
            else:
                fade = pygame.Surface((bottom_content_rect.width, bottom_content_rect.height), pygame.SRCALPHA)
                fade.fill((5, 7, 10, int((1.0 - frac) * 120)))
                screen.blit(fade, bottom_content_rect.topleft)

        # Write data files for subprocesses
        mission_mgr.write_overlay_data(render_snap_by_slot)
        hud_mgr.write_data(render_snap_by_slot, last_scan_normals, mission_mgr)
        hud_mgr.check_proc()

        status_parts = []
        status_parts.append("Dolphin hooked")
        status_parts.append("Overlay ON" if overlay_enabled else "Overlay OFF")
        if any(hitbox_slots.values()):
            active_hitbox_slots = ", ".join(k for k, v in hitbox_slots.items() if v)
            status_parts.append(f"Hitboxes {active_hitbox_slots}")
        else:
            status_parts.append("Hitboxes OFF")
        if any(hurtbox_slots.values()):
            active_hurtbox_slots = ", ".join(k for k, v in hurtbox_slots.items() if v)
            status_parts.append(f"Hurtboxes {active_hurtbox_slots}")
        else:
            status_parts.append("Hurtboxes OFF")
        if ruler_enabled:
            active_ruler_slots = ", ".join(k for k, v in ruler_slots.items() if v)
            _axis_labels = []
            if bool(ruler_axes.get("horizontal", False)):
                _axis_labels.append("Horizontal")
            if bool(ruler_axes.get("vertical", False)):
                _axis_labels.append("Vertical")
            axis_label = "+".join(_axis_labels) or "No axis"
            status_parts.append(f"Ruler {axis_label} {active_ruler_slots or 'OFF'}")
        else:
            status_parts.append("Ruler OFF")
        if mission_mgr.active_slot:
            status_parts.append(f"Mission {mission_mgr.active_slot}")
        if bool(megacrash_trainer_state.get("enabled", False)):
            status_parts.append(f"Mega Trainer {_megacrash_mode_summary(megacrash_trainer_state)}")
            cd_left = _megacrash_cooldown_remaining(megacrash_trainer_state, now)
            if cd_left > 0.0:
                status_parts.append(f"Mega cooldown {cd_left:.1f}s")
            last_trig = megacrash_trainer_state.get("last_trigger")
            if isinstance(last_trig, dict):
                try:
                    age = now - float(last_trig.get("time", 0.0) or 0.0)
                except Exception:
                    age = 999.0
                if age < 2.0:
                    status_parts.append(f"Mega hit {last_trig.get('slot', '?')}")
        if bool(mem_dump_state.get("active", False)):
            status_parts.append(str(mem_dump_state.get("label") or "Memory dump running"))
        else:
            dump_err = str(mem_dump_state.get("error") or "")
            dump_done_t = float(mem_dump_state.get("last_done_time", 0.0) or 0.0)
            if dump_err and now - dump_done_t < 6.0:
                status_parts.append(f"Dump failed {dump_err}")
            elif dump_done_t and now - dump_done_t < 6.0:
                status_parts.append(f"Dump saved {os.path.basename(str(mem_dump_state.get('path') or ''))}")
        if get_char_test_state is not None:
            try:
                _ct_state = get_char_test_state()
                if bool(_ct_state.get("running", False)):
                    status_parts.append("Extra chars ON")
                if bool(_ct_state.get("solo_team_requested", False)):
                    status_parts.append("Solo team ON")
            except Exception:
                pass
        if bool(ko_control_full_enabled):
            _ko_state_txt = "ACTIVE" if bool(ko_control_live_active) else "ARMED"
            _ko_summary = str((ko_control_auto_state or {}).get("summary") or "")
            status_parts.append(f"KO Ctrl {_ko_state_txt}" + (f" [{_ko_summary}]" if _ko_summary else ""))
        if float(idle_restore_status.get("until", 0.0) or 0.0) > now:
            _idle_status_text = str(idle_restore_status.get("text") or "")
            if _idle_status_text:
                status_parts.append(_idle_status_text)

        draw_status_rail(
            screen,
            smallfont,
            text=" | ".join(status_parts),
        )

        pygame.display.flip()

        # ------------------------------------------------------------------
        # Click handling
        # ------------------------------------------------------------------
        if mouse_clicked_pos is not None:
            mx, my = mouse_clicked_pos

            if tools_btn_rect.collidepoint(mx, my):
                dock_tools_open = not bool(dock_tools_open)
                mouse_clicked_pos = None
                continue

            if hb_btn_rect.collidepoint(mx, my):
                had_hitboxes = any(hitbox_slots.values())
                new_state = not had_hitboxes
                for k in hitbox_slots:
                    hitbox_slots[k] = new_state
                _write_hitbox_filter(enable_range_ruler=(new_state and not had_hitboxes))
                _write_master_control()
                _sync_master_overlay_state()
                mouse_clicked_pos = None
                continue

            elif hurt_btn_rect.collidepoint(mx, my):
                new_state = not any(hurtbox_slots.values())
                for k in hurtbox_slots:
                    hurtbox_slots[k] = new_state
                _write_hitbox_filter()
                _write_master_control()
                _sync_master_overlay_state()
                mouse_clicked_pos = None
                continue

            elif ruler_btn_rect.collidepoint(mx, my):
                ruler_enabled = not bool(ruler_enabled)
                _write_hitbox_filter()
                _write_master_control()
                _sync_master_overlay_state()
                mouse_clicked_pos = None
                continue

            elif ruler_axis_h_rect.collidepoint(mx, my):
                ruler_axes["horizontal"] = not bool(ruler_axes.get("horizontal", False))
                _write_hitbox_filter()
                _write_master_control()
                _sync_master_overlay_state()
                mouse_clicked_pos = None
                continue

            elif ruler_axis_v_rect.collidepoint(mx, my):
                ruler_axes["vertical"] = not bool(ruler_axes.get("vertical", False))
                _write_hitbox_filter()
                _write_master_control()
                _sync_master_overlay_state()
                mouse_clicked_pos = None
                continue

            elif ps_btn_rect.collidepoint(mx, my):
                def _get_active_chars():
                    return [s.get("name") for slot in ["P1-C1","P1-C2","P2-C1","P2-C2"]
                            for s in [render_snap_by_slot.get(slot)]
                            if s and s.get("name")]
                open_proj_scanner_window(_get_active_chars)
                mouse_clicked_pos = None
                continue

            elif as_btn_rect.collidepoint(mx, my):
                open_assist_scanner_window()
                mouse_clicked_pos = None
                continue

            elif hud_btn_rect.collidepoint(mx, my):
                overlay_enabled = not overlay_enabled
                _write_master_control()
                _sync_master_overlay_state()
                mouse_clicked_pos = None
                continue

            elif clear_card_btn_rect.collidepoint(mx, my):
                # Keep the core HUD alive; this is a one-click declutter for
                # the three optional live cards only.
                show_interaction_card = False
                show_combo_card = False
                show_tag_card = False
                _write_master_control()
                _sync_master_overlay_state()
                mouse_clicked_pos = None
                continue

            elif interaction_card_btn_rect.collidepoint(mx, my):
                show_interaction_card = not show_interaction_card
                _write_master_control()
                _sync_master_overlay_state()
                mouse_clicked_pos = None
                continue

            elif combo_card_btn_rect.collidepoint(mx, my):
                show_combo_card = not show_combo_card
                _write_master_control()
                _sync_master_overlay_state()
                mouse_clicked_pos = None
                continue

            elif tag_card_btn_rect.collidepoint(mx, my):
                show_tag_card = not show_tag_card
                _write_master_control()
                _sync_master_overlay_state()
                mouse_clicked_pos = None
                continue

            elif megacrash_btn_rect.collidepoint(mx, my):
                if open_megacrash_trainer_window is not None:
                    open_megacrash_trainer_window(megacrash_trainer_state, _save_megacrash_trainer_config, lambda: _megacrash_roster_context(render_snap_by_slot))
                else:
                    print("[megacrash trainer] settings window unavailable")
                mouse_clicked_pos = None
                continue

            elif memdump_btn_rect.collidepoint(mx, my):
                if _start_memory_dump(mem_dump_state):
                    print("[memdump] started", flush=True)
                else:
                    print("[memdump] already running", flush=True)
                mouse_clicked_pos = None
                continue

            elif select_probe_btn_rect.collidepoint(mx, my):
                if toggle_extra_characters is not None:
                    result = toggle_extra_characters()
                    # Wake the debounced char-test tick immediately for the
                    # click event, then fall back to the safe cadence.
                    char_test_next_tick = 0.0
                    print(f"[extra chars] toggle queued: {result}", flush=True)
                else:
                    print("[extra chars] toggle unavailable", flush=True)
                mouse_clicked_pos = None
                continue

            elif yami_stage_btn_rect.collidepoint(mx, my):
                if open_stage_select_window is not None:
                    open_stage_select_window()
                    print("[stage select] window opened", flush=True)
                else:
                    print("[stage select] window unavailable", flush=True)
                mouse_clicked_pos = None
                continue

            elif input_spoof_btn_rect.collidepoint(mx, my):
                if open_input_spoof_window is not None:
                    open_input_spoof_window()
                    print("[input spoof] window opened", flush=True)
                else:
                    print("[input spoof] window unavailable", flush=True)
                mouse_clicked_pos = None
                continue

            elif action_force_btn_rect.collidepoint(mx, my):
                if open_action_force_window is not None:
                    open_action_force_window()
                    print("[action force] window opened", flush=True)
                else:
                    print("[action force] window unavailable", flush=True)
                mouse_clicked_pos = None
                continue

            elif ko_control_btn_rect.collidepoint(mx, my):
                ko_control_full_enabled = not bool(ko_control_full_enabled)
                # The top-dock button is an ARM switch now, not a permanent
                # patch.  Always restore immediately; the main-loop auto tick
                # applies Control+Full only while a complete team KO is present.
                ko_dol_patch_index = -1
                idle_restore_hold_until_by_slot.clear()
                ko_global_hold_baseline = None
                ko_control_live_active = False
                ko_control_last_apply = 0.0
                result = apply_ko_control_auto_mode("safe" if ko_control_full_enabled else "off", verify=True)
                ko_control_last_apply = now
                _mode_txt = "ARMED auto" if ko_control_full_enabled else "OFF"
                idle_restore_status = {
                    "text": f"KO Ctrl {_mode_txt}; SAFE armed, FULL only after full-team KO",
                    "until": now + 5.0,
                }
                print(f"[ko control] {idle_restore_status['text']}", flush=True)
                mouse_clicked_pos = None
                continue

            elif win_counter_btn_rect.collidepoint(mx, my):
                if open_hud_editor_window is not None:
                    open_hud_editor_window()
                else:
                    print("[hud editor] settings window unavailable")
                mouse_clicked_pos = None
                continue

            elif overseer_btn_rect.collidepoint(mx, my):
                if open_overseer_window is not None:
                    open_overseer_window(_overseer_state_snapshot, _overseer_safe_restore, _overseer_hard_reset, _overseer_dump_state)
                else:
                    print("[tool state] window unavailable")
                mouse_clicked_pos = None
                continue

            else:
                clicked_filter = False
                for slot_name, cb_rect in hb_filter_rects.items():
                    if cb_rect.collidepoint(mx, my):
                        had_hitboxes = any(hitbox_slots.values())
                        hitbox_slots[slot_name] = not hitbox_slots[slot_name]
                        _write_hitbox_filter(enable_range_ruler=(any(hitbox_slots.values()) and not had_hitboxes))
                        _write_master_control()
                        _sync_master_overlay_state()
                        clicked_filter = True
                        break
                if not clicked_filter:
                    for slot_name, cb_rect in hurt_filter_rects.items():
                        if cb_rect.collidepoint(mx, my):
                            hurtbox_slots[slot_name] = not hurtbox_slots[slot_name]
                            _write_hitbox_filter()
                            _write_master_control()
                            _sync_master_overlay_state()
                            clicked_filter = True
                            break
                if not clicked_filter:
                    for slot_name, cb_rect in ruler_filter_rects.items():
                        if cb_rect.collidepoint(mx, my):
                            ruler_slots[slot_name] = not bool(ruler_slots.get(slot_name, True))
                            _write_hitbox_filter()
                            _write_master_control()
                            _sync_master_overlay_state()
                            clicked_filter = True
                            break

            for _tab_key, _tab_rect in list(bottom_tab_rects.items()):
                if _tab_rect.collidepoint(mx, my):
                    if _tab_key == "advantage":
                        open_advantage_window(last_scan_normals, render_snap_by_slot)
                    elif active_bottom_tab != _tab_key:
                        active_bottom_tab = _tab_key
                        bottom_tab_fade = {"start": now, "dur": 0.18}
                    mouse_clicked_pos = None
                    break

            if mouse_clicked_pos is not None and active_bottom_tab == "scan":
                _preview_controls = normal_preview_ui.get("controls") if isinstance(normal_preview_ui, dict) else {}
                _preview_rows = normal_preview_ui.get("rows") if isinstance(normal_preview_ui, dict) else []
                _off_x, _off_y = normal_preview_offset
                _handled_preview_click = False
                if isinstance(_preview_controls, dict):
                    for _mode_key, _local_rect in list(_preview_controls.items()):
                        if isinstance(_local_rect, pygame.Rect) and _local_rect.move(_off_x, _off_y).collidepoint(mx, my):
                            if _mode_key == "__more__":
                                normal_preview_advanced_open = not bool(normal_preview_advanced_open)
                            else:
                                normal_preview_mode = "none" if normal_preview_mode == _mode_key else str(_mode_key)
                                if _mode_key in {"fast", "damage", "adv_block", "safe", "unsafe"}:
                                    normal_preview_selection = None
                                if _mode_key in {"safe", "unsafe"}:
                                    normal_preview_advanced_open = True
                            _handled_preview_click = True
                            break
                if not _handled_preview_click:
                    for _row_meta in list(_preview_rows or []):
                        if not isinstance(_row_meta, dict):
                            continue
                        _local_rect = _row_meta.get("rect")
                        if isinstance(_local_rect, pygame.Rect) and _local_rect.move(_off_x, _off_y).collidepoint(mx, my):
                            _new_selection = {
                                "slot_label": str(_row_meta.get("slot_label") or ""),
                                "key": str(_row_meta.get("key") or ""),
                            }
                            if (
                                isinstance(normal_preview_selection, dict)
                                and str(normal_preview_selection.get("slot_label") or "") == _new_selection["slot_label"]
                                and str(normal_preview_selection.get("key") or "") == _new_selection["key"]
                            ):
                                normal_preview_selection = None
                                if normal_preview_mode == "punish":
                                    normal_preview_mode = "none"
                            else:
                                normal_preview_selection = _new_selection
                            _handled_preview_click = True
                            break
                if _handled_preview_click:
                    mouse_clicked_pos = None

            if mouse_clicked_pos is not None and active_bottom_tab == "advantage":
                _adv_controls = advantage_ui.get("controls") if isinstance(advantage_ui, dict) else {}
                _adv_rows = advantage_ui.get("rows") if isinstance(advantage_ui, dict) else []
                _adv_order = advantage_ui.get("char_order") if isinstance(advantage_ui, dict) else []
                _adv_current = str(advantage_ui.get("current_char_key") or "") if isinstance(advantage_ui, dict) else ""
                _off_x, _off_y = advantage_offset
                _handled_adv_click = False

                if isinstance(_adv_controls, dict):
                    for _ctrl_key, _local_rect in list(_adv_controls.items()):
                        if isinstance(_local_rect, pygame.Rect) and _local_rect.move(_off_x, _off_y).collidepoint(mx, my):
                            if _ctrl_key in {"__char_prev__", "__char_next__"}:
                                _order = [str(item) for item in list(_adv_order or []) if str(item)]
                                if _order:
                                    try:
                                        _idx = _order.index(_adv_current)
                                    except Exception:
                                        _idx = 0
                                    _step = -1 if _ctrl_key == "__char_prev__" else 1
                                    _new_key = _order[(_idx + _step) % len(_order)]
                                    _next_sel = dict(advantage_selection or {})
                                    _next_sel["source_char_key"] = _new_key
                                    _next_sel["lock_char"] = True
                                    advantage_selection = _next_sel
                            elif _ctrl_key == "__live_default__":
                                _next_sel = dict(advantage_selection or {})
                                _next_sel.pop("source_char_key", None)
                                _next_sel["lock_char"] = False
                                advantage_selection = _next_sel if _next_sel.get("attack_key") else None
                            _handled_adv_click = True
                            break

                if not _handled_adv_click:
                    for _row_meta in list(_adv_rows or []):
                        if not isinstance(_row_meta, dict):
                            continue
                        _local_rect = _row_meta.get("rect")
                        if isinstance(_local_rect, pygame.Rect) and _local_rect.move(_off_x, _off_y).collidepoint(mx, my):
                            if str(_row_meta.get("type") or "") == "source_attack":
                                _next_sel = dict(advantage_selection or {})
                                _next_sel["attack_key"] = str(_row_meta.get("attack_key") or "")
                                if bool(_next_sel.get("lock_char")):
                                    _next_sel["source_char_key"] = str(_row_meta.get("source_char_key") or "")
                                advantage_selection = _next_sel
                            _handled_adv_click = True
                            break

                if _handled_adv_click:
                    mouse_clicked_pos = None

            if mouse_clicked_pos is None:
                continue

            quick_clicked = False
            for (slot_label, quick_index), qrect in list(quick_btn_areas.items()):
                if qrect.collidepoint(mx, my):
                    snap = render_snap_by_slot.get(slot_label) or snaps.get(slot_label)
                    try:
                        ok = bool(apply_quick_assist_from_main(slot_label, quick_index, snap))
                    except Exception as e:
                        ok = False
                        print(f"[assist quick] click failed: {e!r}")
                    if ok:
                        char_id = 0
                        if isinstance(snap, dict):
                            for _field in ("id", "csv_char_id", "char_id"):
                                try:
                                    char_id = int(snap.get(_field) or 0)
                                except Exception:
                                    char_id = 0
                                if char_id:
                                    break
                        prev_quick_index = None
                        prev_row = active_quick_assist_by_slot.get(slot_label)
                        if isinstance(prev_row, dict):
                            try:
                                prev_quick_index = int(prev_row.get("quick_index"))
                            except Exception:
                                prev_quick_index = None

                        active_quick_assist_by_slot[slot_label] = {
                            "quick_index": int(quick_index),
                            "char_id": int(char_id or 0),
                        }

                        if prev_quick_index is not None and prev_quick_index != int(quick_index):
                            quick_assist_slide_by_slot[slot_label] = {
                                "from": int(prev_quick_index),
                                "to": int(quick_index),
                                "start": time.time(),
                                "dur": 0.42,
                                "char_id": int(char_id or 0),
                            }
                        else:
                            quick_assist_slide_by_slot[slot_label] = {
                                "from": int(quick_index),
                                "to": int(quick_index),
                                "start": time.time(),
                                "dur": 0.20,
                                "char_id": int(char_id or 0),
                            }

                        # Only one assist button may be highlighted/flashing per
                        # slot. Clear any previous flash entries for this slot so
                        # changing assists on the same character does not leave
                        # the old assist highlighted beside the new one.
                        for _flash_key in list(quick_btn_flash.keys()):
                            try:
                                if _flash_key[0] == slot_label:
                                    quick_btn_flash.pop(_flash_key, None)
                            except Exception:
                                quick_btn_flash.pop(_flash_key, None)

                        quick_btn_flash[(slot_label, int(quick_index))] = PANEL_FLASH_FRAMES
                    quick_clicked = True
                    break
            if quick_clicked:
                mouse_clicked_pos = None
                continue

            # KO DOL lab buttons were removed from the portrait panels.
            # The supported control path is now the top-dock KO Ctrl auto toggle.

            # Mission mode buttons
            for slot_label, btn_rect in [
                ("P1-C1", mission_btn_p1c1), ("P2-C1", mission_btn_p2c1),
                ("P1-C2", mission_btn_p1c2), ("P2-C2", mission_btn_p2c2),
            ]:
                if btn_rect.collidepoint(mx, my):
                    mission_mgr.toggle_active_slot(slot_label)

                    # Mission Mode is drawn by the master overlay process. If
                    # both HUD overlay and hitboxes are off, there is no process
                    # alive to display the mission route. Auto-enable Overlay
                    # for active missions so Mission Mode works from a fully
                    # quiet/off state.
                    if mission_mgr.active_slot and (not overlay_enabled) and (not any(hitbox_slots.values())) and (not any(hurtbox_slots.values())):
                        overlay_enabled = True
                        _write_master_control()
                        _sync_master_overlay_state()

                    mouse_clicked_pos = None
                    break
            if mouse_clicked_pos is None:
                continue

            # Frame data buttons
            for slot_label, btn_rect in [
                ("P1-C1", btn_p1c1), ("P2-C1", btn_p2c1),
                ("P1-C2", btn_p1c2), ("P2-C2", btn_p2c2),
            ]:
                if btn_rect.collidepoint(mx, my):
                    # Never open the editor from the compact HUD preview: it is
                    # deliberately read-only and has no trustworthy write packet
                    # addresses. Use a prewarmed rich cache when it exists; if
                    # it does not, show a native launch shell immediately and
                    # ask the worker for the editable cache exactly once.
                    _target_row = _fd_live_editable_row(
                        slot_label,
                        last_workbench_scan_normals,
                        last_scan_normals,
                        render_snap_by_slot,
                    )
                    if _target_row is not None:
                        # fd_window receives only the identity-checked row.
                        open_frame_data_window(slot_label, [_target_row])
                    elif scan_worker:
                        _snap = render_snap_by_slot.get(slot_label) or {}
                        _name = str(_snap.get("char_name") or _snap.get("name") or "")
                        open_frame_data_loading_window(slot_label, _name)
                        _binding = _fd_current_live_binding(
                            slot_label, last_scan_normals, render_snap_by_slot
                        )
                        fd_pending_window_targets[slot_label] = _binding
                        try:
                            if is_live_proven(_binding):
                                scan_worker.request(workbench=True)
                            else:
                                # The HUD changed before the compact scan did.
                                # Ask for fresh identity first; do not reopen a
                                # prior slot's cache while it catches up.
                                scan_worker.request()
                        except TypeError:
                            # Compatibility fallback for any older worker copy.
                            scan_worker.request()
                    panel_btn_flash[slot_label] = PANEL_FLASH_FRAMES
                    break


        # Right-click copy handling
        if mouse_right_clicked_pos is not None:
            mx, my = mouse_right_clicked_pos

            if megacrash_btn_rect.collidepoint(mx, my):
                if open_megacrash_trainer_window is not None:
                    open_megacrash_trainer_window(megacrash_trainer_state, _save_megacrash_trainer_config, lambda: _megacrash_roster_context(render_snap_by_slot))
                else:
                    print("[megacrash trainer] settings window unavailable")
                mouse_right_clicked_pos = None
                continue

            copied = False
            if active_bottom_tab == "debug":
                for name, (r, addr) in debug_click_areas.items():
                    if r.collidepoint(mx, my):
                        _copy_to_clipboard(f"0x{addr:08X}" if isinstance(addr, int) else str(addr))
                        copied = True
                        break

            if not copied:
                slot_panels = [
                    ("P1-C1", r_p1c1), ("P2-C1", r_p2c1),
                    ("P1-C2", r_p1c2), ("P2-C2", r_p2c2),
                ]
                for slot_label, rect in slot_panels:
                    if rect and rect.collidepoint(mx, my):
                        snap = render_snap_by_slot.get(slot_label)
                        if snap:
                            base = snap.get("base")
                            _copy_to_clipboard(f"0x{base:08X}" if isinstance(base, int) else str(base))
                        break

            else:
                # Debug toggles / cycles
                def _toggle_u8(name: str):
                    entry = debug_click_areas.get(name)
                    if not entry:
                        return False
                    r, addr = entry
                    if not r.collidepoint(mx, my):
                        return False
                    cur = rd8(addr) or 0
                    wd8(addr, 0x01 if cur == 0x00 else 0x00)
                    return True

                def _cycle_u8(name: str, mod: int):
                    entry = debug_click_areas.get(name)
                    if not entry:
                        return False
                    r, addr = entry
                    if not r.collidepoint(mx, my):
                        return False
                    cur = rd8(addr) or 0
                    wd8(addr, (cur + 1) % mod)
                    return True

                _toggle_u8("PauseOverlay")

                entry = debug_click_areas.get("TrPause")
                if entry:
                    r, addr_tr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr_tr) or 0
                        wd8(addr_tr, 0x01 if cur == 0x00 else 0x00)

                entry = debug_click_areas.get("P2Pause")
                if entry:
                    r, addr_p2 = entry
                    if r.collidepoint(mx, my):
                        cur_p2   = rd8(addr_p2) or 0
                        entry_tr = debug_click_areas.get("TrPause")
                        addr_tr  = entry_tr[1] if entry_tr else None
                        if cur_p2 == 0x00:
                            if addr_tr is not None: wd8(addr_tr, 0x01)
                            wd8(addr_p2, 0x01)
                        else:
                            if addr_tr is not None: wd8(addr_tr, 0x00)
                            wd8(addr_p2, 0x00)

                _cycle_u8("DummyMeter", 3)
                _cycle_u8("CpuAction",  6)
                _cycle_u8("CpuGuard",   3)
                _toggle_u8("CpuPushblock")
                _toggle_u8("CameraLock")
                _toggle_u8("CpuThrowTech")
                _cycle_u8("P1Meter", 3)
                _toggle_u8("P1Life")
                _toggle_u8("FreeBaroque")
                _toggle_u8("Orientation")

                entry = debug_click_areas.get("SuperBG")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr)
                        wd8(addr, 0x01 if cur == 0x04 else 0x04)

                entry = debug_click_areas.get("BaroquePct")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        wd8(addr, (cur + 1) if cur < 0x0A else 0x00)

                _toggle_u8("AttackData")
                _toggle_u8("InputDisplay")

                entry = debug_click_areas.get("CpuDifficulty")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur   = rd8(addr) or 0
                        level = ((cur // 0x20) % 8 + 1) % 8
                        wd8(addr, level * 0x20)

                entry = debug_click_areas.get("DamageOutput")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        wd8(addr, (cur + 1) & 0x03)

                entry = debug_click_areas.get("HypeTrigger")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        orig = rd8(addr)
                        if orig is None or orig == 0:
                            orig = 0x45
                        wd8(addr, 0x40)
                        hype_restore_addr = addr
                        hype_restore_orig = orig
                        hype_restore_ts   = now + 0.5

                entry = debug_click_areas.get("ComboStore[1]")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        wd8(addr, 0x41)

                _toggle_u8("ComboCountOnly")

                entry = debug_click_areas.get("SpecialPopup")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr)
                        if cur is None or cur == 0:
                            cur = 0x45
                        special_restore_orig = cur
                        wd8(addr, 0x40)
                        special_restore_addr = addr
                        special_restore_ts   = now + 0.5

        # Momentary write restores
        if hype_restore_addr is not None and now >= hype_restore_ts:
            try:
                wd8(hype_restore_addr, hype_restore_orig)
            except Exception:
                pass
            hype_restore_addr = None

        if special_restore_addr is not None and now >= special_restore_ts:
            try:
                wd8(special_restore_addr, special_restore_orig)
            except Exception:
                pass
            special_restore_addr = None

        # Button flash countdown
        for k in panel_btn_flash:
            if panel_btn_flash[k] > 0:
                panel_btn_flash[k] -= 1

        for k in list(quick_btn_flash.keys()):
            try:
                if int(quick_btn_flash.get(k, 0) or 0) > 0:
                    quick_btn_flash[k] = int(quick_btn_flash.get(k, 0) or 0) - 1
                if int(quick_btn_flash.get(k, 0) or 0) <= 0:
                    quick_btn_flash.pop(k, None)
            except Exception:
                quick_btn_flash.pop(k, None)

        for _slot, _anim in list(quick_assist_slide_by_slot.items()):
            try:
                start = float(_anim.get("start", 0.0) or 0.0)
                dur = float(_anim.get("dur", 0.18) or 0.18)
                if start and (time.time() - start) > (dur + 0.10):
                    quick_assist_slide_by_slot.pop(_slot, None)
            except Exception:
                quick_assist_slide_by_slot.pop(_slot, None)

        # Expire one-shot panel effects.
        for _slot, _fxs in list(panel_fx_state.items()):
            if not isinstance(_fxs, dict):
                panel_fx_state[_slot] = {}
                continue
            for _name, _entry in list(_fxs.items()):
                try:
                    _start = float(_entry.get("start", 0.0) or 0.0)
                    _dur = float(_entry.get("dur", 0.3) or 0.3)
                    if _start and (now - _start) > (_dur + 0.06):
                        _fxs.pop(_name, None)
                except Exception:
                    _fxs.pop(_name, None)

        # Normals rescan triggers.  Auto scans are cache-only, debounced, and
        # coalesced so character-select churn / round reloads do not keep the
        # Python scanner fighting the HUD loop for the GIL.  Full dynamic scans
        # still happen through the manual scan path below.
        if HAVE_SCAN_NORMALS and need_rescan_normals and scan_worker and FD_AUTOSCAN_ENABLED:
            _sig = _fd_scan_signature(snaps)
            if pending_rescan_signature != _sig:
                pending_rescan_signature = _sig
                pending_rescan_normals_since = now
            _worker_busy = False
            try:
                _worker_busy = bool(scan_worker.is_busy())
            except Exception:
                _worker_busy = False
            if (
                _sig
                and not _worker_busy
                and (now - pending_rescan_normals_since) >= FD_AUTOSCAN_DEBOUNCE_SEC
                and (now - last_rescan_request_time) >= FD_AUTOSCAN_MIN_INTERVAL_SEC
                and _sig != last_rescan_request_signature
            ):
                scan_worker.request()
                last_rescan_request_time = now
                last_rescan_request_signature = _sig
                need_rescan_normals = False
        elif HAVE_SCAN_NORMALS and need_rescan_normals and not FD_AUTOSCAN_ENABLED:
            need_rescan_normals = False

        # Per-character CSV export requests a saved workbench cache rebase only
        # after a compact scan has confirmed a stable new roster. This avoids
        # the old background full-scan loop entirely: no dynamic discovery is
        # requested here, no live data is patched, and no master CSV is opened.
        if frame_data_exporter is not None and scan_worker and fd_csv_pending_roster_signature:
            _fd_csv_current = _fd_export_roster_signature(last_scan_normals)
            if _fd_csv_current != fd_csv_pending_roster_signature:
                fd_csv_pending_roster_signature = None
                fd_csv_pending_since = 0.0
            else:
                try:
                    _fd_csv_busy = bool(scan_worker.is_busy())
                except Exception:
                    _fd_csv_busy = False
                if not _fd_csv_busy and (now - fd_csv_pending_since) >= 0.45:
                    try:
                        scan_worker.request(workbench=True)
                        fd_csv_last_requested_signature = fd_csv_pending_roster_signature
                        print(
                            f"[fd sheet] queued cached per-character CSV export for "
                            f"{len(fd_csv_pending_roster_signature)} live slot(s)",
                            flush=True,
                        )
                    except Exception as _fd_csv_request_error:
                        print(f"[fd sheet] cached character export request failed: {_fd_csv_request_error!r}", flush=True)
                    finally:
                        fd_csv_pending_roster_signature = None
                        fd_csv_pending_since = 0.0

        # A compact-preview cache miss is different from ordinary live ruler
        # sampling: without normal frame data, a brand-new fighter cannot tell
        # us which frames are active.  Bootstrap exactly one background dynamic
        # scan for that stable roster signature, persist the normal profile plus
        # compact preview, then return to cache-only play.
        if HAVE_SCAN_NORMALS and FD_BUILD_MISSING_PROFILES and scan_worker:
            _missing_sig = _missing_preview_profile_signature(last_scan_normals)
            if _missing_sig:
                if pending_missing_profile_signature != _missing_sig:
                    pending_missing_profile_signature = _missing_sig
                    pending_missing_profile_since = now
                _worker_busy = False
                try:
                    _worker_busy = bool(scan_worker.is_busy())
                except Exception:
                    _worker_busy = False
                _new_or_retry = (
                    _missing_sig != last_missing_profile_build_signature
                    or (now - last_missing_profile_build_time) >= FD_MISSING_PROFILE_BUILD_MIN_INTERVAL_SEC
                )
                if (
                    _new_or_retry
                    and not _worker_busy
                    and (now - pending_missing_profile_since) >= FD_MISSING_PROFILE_BUILD_DELAY_SEC
                ):
                    labels = ", ".join(f"id={_cid} {_slot}" for _cid, _sig, _slot in _missing_sig)
                    target_ids = tuple(sorted({_cid for _cid, _sig, _slot in _missing_sig}))
                    print(f"[fd profile] auto-build missing preview: {labels}", flush=True)
                    scan_worker.request(force_dynamic=True, dynamic_char_ids=target_ids)
                    last_missing_profile_build_time = now
                    last_missing_profile_build_signature = _missing_sig
                    pending_missing_profile_signature = None
            else:
                pending_missing_profile_signature = None
        else:
            pending_missing_profile_signature = None

        if HAVE_SCAN_NORMALS and manual_scan_requested:
            if scan_worker:
                try:
                    scan_worker.request(force_dynamic=True)
                except TypeError:
                    scan_worker.request()
            else:
                try:
                    last_scan_normals = scan_normals_all.scan_once()
                    if fd_patch_runtime is not None:
                        try:
                            if fd_patch_controller is not None:
                                fd_patch_controller.apply_to_scan_data(last_scan_normals)
                            else:
                                fd_patch_runtime.overlay_scan_data(last_scan_normals)
                        except Exception as e:
                            print(f"[fd patch] manual scan overlay/apply failed: {e!r}")
                    _sync_runtime_engine_profile_targets(last_scan_normals)
                    _export_frame_data_sheet(last_scan_normals)
                    last_scan_time    = time.time()
                except Exception as e:
                    print("manual scan failed:", e)
            manual_scan_requested = False

        # CSV flush
        if pending_hits and (frame_idx % 30 == 0):
            newcsv = not os.path.exists(HIT_CSV)
            with open(HIT_CSV, "a", newline="", encoding="utf-8") as fh:
                wcsv = csv.writer(fh)
                if newcsv:
                    wcsv.writerow([
                        "t",
                        "victim_label", "victim_char", "dmg",
                        "hp_before", "hp_after",
                        "attacker_label", "attacker_char", "attacker_char_id",
                        "attacker_id_dec", "attacker_id_hex", "attacker_move",
                        "dist2",
                        "atk_flag062", "atk_flag063",
                        "vic_flag062", "vic_flag063",
                        "atk_ctrl", "vic_ctrl",
                    ])
            pending_hits.clear()

        _perf_warn("frame_work", _frame_perf_start, threshold_ms=PERF_FRAME_WARN_MS)
        clock.tick(TARGET_FPS)
        frame_idx += 1

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    mission_mgr.restore_debug_overrides()

    if master_overlay_proc and master_overlay_proc.poll() is None:
        try:
            master_overlay_proc.terminate()
        except Exception:
            pass

    try:
        if runtime_stun_profiler is not None:
            runtime_stun_profiler.flush()
    except Exception:
        pass

    try:
        set_emulated_write_quarantine(False)
    except Exception:
        pass
    pygame.quit()


def main():
    legacy_main()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("\n[main crash]")
        print(f"error={e!r}")
        traceback.print_exc()
        try:
            input("\nCrash detected. Press Enter to close...")
        except EOFError:
            pass
        raise