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
import pygame
from subprocess_compat import frozen_exe


def resource_path(*parts):
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)


def _safe_pygame_event_get():
    """Return pygame events without letting a corrupted SDL event crash the GUI.

    On pygame 2.6.x + Python 3.13, pygame.event.get() can very rarely surface
    as SystemError("<built-in function get> returned a result with an exception set")
    with an internal KeyError: 0.  That is not a TvC state problem; it means the
    pygame C extension left a Python error set while translating an SDL event.
    Clear the queue and keep the overlay alive.
    """
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

from constants import (
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

import pygame

try:
    import pyperclip
except ImportError:
    pyperclip = None

from layout import compute_layout, reassign_slots_for_giants
from scan_worker import ScanNormalsWorker
from training_flags import read_training_flags
from debug_panel import read_debug_flags, draw_debug_overlay

from dolphin_io import hook, rd8, rd32, wd8, wd32, wbytes, addr_in_ram, rbytes

from config import (
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

from portraits import (
    load_portrait_placeholder,
    load_portraits_from_dir,
    get_portrait_for_snap,
)

from resolver import RESOLVER, pick_posy_off_no_jump
from meter import read_meter, METER_CACHE
from fighter import read_fighter, dist2
from advantage import ADV_TRACK
from moves import (
    load_move_map,
    move_label_for,
    CHAR_ID_CORRECTION,
)
from move_id_map import lookup_move_name
from hud_draw import (
    draw_panel_classic,
    draw_activity,
    draw_event_log,
    draw_scan_normals,
)

from redscan import RedHealthScanner
from global_redscan import GlobalRedScanner
from events import log_engaged, log_hit, log_frame_advantage

try:
    import scan_normals_all
    HAVE_SCAN_NORMALS = True
    from scan_normals_all import ANIM_MAP as SCAN_ANIM_MAP
except Exception:
    scan_normals_all = None
    HAVE_SCAN_NORMALS = False
    SCAN_ANIM_MAP = {}

from frame_data_window import open_frame_data_window
from proj_scanner_window import open_proj_scanner_window
try:
    from megacrash_trainer_window import open_megacrash_trainer_window
except Exception as e:
    open_megacrash_trainer_window = None
    print(f"WARNING: megacrash trainer window not available ({e!r})")
try:
    from hud_editor_window import open_hud_editor_window, tick_hud_editor_state, get_hud_editor_runtime_state, reset_hud_editor_runtime_state
except Exception as e:
    open_hud_editor_window = None
    tick_hud_editor_state = None
    get_hud_editor_runtime_state = None
    reset_hud_editor_runtime_state = None
    print(f"WARNING: HUD editor window not available ({e!r})")
try:
    from overseer_window import open_overseer_window
except Exception as e:
    open_overseer_window = None
    print(f"WARNING: Tool State window not available ({e!r})")
try:
    import fd_patch_runtime
except Exception as e:
    fd_patch_runtime = None
    print(f"WARNING: fd patch runtime not available ({e!r})")
try:
    import runtime_patch_manager as runtime_pm
except Exception as e:
    runtime_pm = None
    print(f"WARNING: runtime patch manager not available ({e!r})")
try:
    from select_screen_probe import (
        new_probe_state as new_select_probe_state,
        zero_next_probe as zero_next_select_probe,
        restore_all_probes as restore_select_probes,
        probe_button_label as select_probe_button_label,
        open_select_probe_window,
        get_probe_debug_state as get_select_probe_debug_state,
    )
except Exception as e:
    print(f"WARNING: select screen probe not available ({e!r})")
    def new_select_probe_state():
        return {"index": 0, "saved": {}, "last": "select probe unavailable", "modified_count": 0, "total": 0}
    def zero_next_select_probe(_state, _read_fn, _write_fn):
        return {"ok": False, "message": "select probe unavailable"}
    def restore_select_probes(_state, _write_fn):
        return {"ok": False, "message": "select probe unavailable"}
    def select_probe_button_label(_state):
        return "CS Probe"
    def open_select_probe_window(_state, _read_fn, _write_fn):
        return None
    def get_select_probe_debug_state(_state):
        return dict(_state or {})
try:
    from assist_scanner_window import (
        open_assist_scanner_window,
        tick_assist_profiles_from_main,
        get_quick_assists_for_slot,
        apply_quick_assist_from_main,
        get_assist_runtime_debug_state,
        restore_assist_runtime_defaults_from_main,
        clear_assist_runtime_state,
    )
except Exception:
    from assist_scanner_window import open_assist_scanner_window
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

from mission_manager import MissionManager
from hud_overlay_manager import HudOverlayManager

MASTER_CONTROL_FILE = "master_overlay_control.json"

TARGET_FPS          = 60
DAMAGE_EVERY_FRAMES = 3
ADV_EVERY_FRAMES    = 2

PANEL_SLIDE_DURATION = 2.0
PANEL_FLASH_FRAMES   = 12
SCAN_SLIDE_DURATION  = 0.7

HP32_OFF   = 0x28
POOL32_OFF = 0x2C

FIGHTER_BLOCK_SIZE = 0x120

REACTION_STATES = {48, 64, 65, 66, 73, 79, 80, 81, 82, 90, 92, 95, 96, 97}

GIANT_IDS = {11, 22}

# Megacrash training mode. The old one-click global poke proved that writing
# the live action/move-id field to 448 can force Megacrash. The trainer keeps
# that same write primitive, but only pulses it on point characters during
# hitstun when the opponent advances to a new combo label.
MEGACRASH_MOVE_ID = 448
MEGACRASH_TRAINER_CONFIG_FILE = "megacrash_trainer.json"
MEGACRASH_TRAINER_DEFAULT_CHANCE = 0
MEGACRASH_TRAINER_DEFAULT_MODE = "percent"
MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES = 0
MEGACRASH_TRAINER_MAX_DELAY_FRAMES = 300
MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC = 2.0
MEGACRASH_TRAINER_MAX_COOLDOWN_SEC = 60.0
MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL = ""
MEGACRASH_TRAINER_PULSE_SEC = 0.08
MEGACRASH_TRAINER_WRITE_OFFSETS = (ATT_ID_OFF_PRIMARY,)
MEGACRASH_TRAINER_CHANCE_PRESETS = (0, 5, 10, 15, 20, 25, 33, 50, 75, 100)
MEGACRASH_SUPPORT_STATE_IDS = {420, 424, 425, 426, 427, 428, 430, 431, 432, 433, 0x01A1, 0x01A8, 0x01AE}

HB_BTN_X, HB_BTN_Y = 8, 8
HB_BTN_W, HB_BTN_H = 130, 22
TOP_UI_RESERVED = 66
QUICK_ASSIST_PERSIST_EVERY_FRAMES = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def u32be_from_block(block: bytes, off: int) -> int | None:
    if not block or off + 4 > len(block):
        return None
    return (
        (block[off] << 24)
        | (block[off + 1] << 16)
        | (block[off + 2] << 8)
        | block[off + 3]
    )


def _copy_to_clipboard(text: str) -> None:
    if not text:
        return
    if pyperclip is not None:
        try:
            pyperclip.copy(text)
            print(f"[copy] {text}")
            return
        except Exception as e:
            print(f"[copy] failed ({e!r}) -> {text}")
    print(f"[copy] (no pyperclip) -> {text}")


# Lightweight performance tracing. This intentionally rate-limits itself so
# a slow frame does not create console spam and make the stutter worse. Set
# TVC_PERF_LOG=0 to silence it.
PERF_LOG_ENABLED = os.environ.get("TVC_PERF_LOG", "1").strip().lower() not in {"0", "false", "off", "no"}
PERF_FRAME_WARN_MS = float(os.environ.get("TVC_PERF_FRAME_WARN_MS", "45"))
PERF_SECTION_WARN_MS = float(os.environ.get("TVC_PERF_SECTION_WARN_MS", "12"))
_PERF_LAST_LOG_TS: dict[str, float] = {}
_PERF_LAST_ELAPSED_MS: dict[str, float] = {}

# Automatic frame-data refreshes now use a cache-only worker and are debounced.
# Manual scans / opening the frame-data window can still run the full dynamic
# scanner when needed, but the main HUD loop must not keep launching dynamic
# scans during character changes or round reloads.
FD_AUTOSCAN_ENABLED = os.environ.get("TVC_FD_AUTOSCAN", "1").strip().lower() not in {"0", "false", "off", "no"}
FD_AUTOSCAN_DEBOUNCE_SEC = float(os.environ.get("TVC_FD_AUTOSCAN_DEBOUNCE_SEC", "1.0") or "1.0")
FD_AUTOSCAN_MIN_INTERVAL_SEC = float(os.environ.get("TVC_FD_AUTOSCAN_MIN_INTERVAL_SEC", "3.0") or "3.0")

# Cache-only auto refresh keeps gameplay smooth, but a never-seen character needs
# one full dynamic profile build or it will sit forever as an empty normals card.
# This only fires for cache misses, is debounced, and can be disabled.
FD_BUILD_MISSING_PROFILES = os.environ.get("TVC_FD_BUILD_MISSING_PROFILES", "1").strip().lower() not in {"0", "false", "off", "no"}
FD_MISSING_PROFILE_BUILD_DELAY_SEC = float(os.environ.get("TVC_FD_MISSING_PROFILE_BUILD_DELAY_SEC", "2.0") or "2.0")
FD_MISSING_PROFILE_BUILD_MIN_INTERVAL_SEC = float(os.environ.get("TVC_FD_MISSING_PROFILE_BUILD_MIN_INTERVAL_SEC", "20.0") or "20.0")


def _perf_warn(label: str, start_perf: float, *, threshold_ms: float | None = None, min_interval: float = 1.0) -> None:
    if not PERF_LOG_ENABLED:
        return
    try:
        elapsed_ms = (time.perf_counter() - float(start_perf)) * 1000.0
        _PERF_LAST_ELAPSED_MS[str(label)] = round(float(elapsed_ms), 1)
    except Exception:
        return
    limit = PERF_SECTION_WARN_MS if threshold_ms is None else float(threshold_ms)
    if elapsed_ms < limit:
        return
    now_perf = time.perf_counter()
    last = float(_PERF_LAST_LOG_TS.get(label, 0.0) or 0.0)
    if now_perf - last < float(min_interval):
        return
    _PERF_LAST_LOG_TS[label] = now_perf
    try:
        print(f"[perf] {label} {elapsed_ms:.1f}ms")
    except Exception:
        pass


def _runtime_output_dir(*parts: str) -> str:
    """Writable app-adjacent output path for source and onefile builds."""
    try:
        if getattr(sys, "frozen", False):
            base = os.path.dirname(os.path.abspath(sys.executable))
        else:
            base = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base = os.getcwd()
    return os.path.join(base, *parts)


def _start_memory_dump(mem_dump_state: dict) -> bool:
    """Start a background MEM1/MEM2 dump without freezing the GUI.

    The dump is written as a zip containing mem1.raw, mem2.raw, and a small
    manifest.  Files land beside the app under memory_dumps/, which keeps them
    easy to grab/upload for diffing while avoiding Git-tracked runtime JSON.
    """
    if bool(mem_dump_state.get("active", False)):
        return False

    def _worker():
        chunk_size = 0x100000  # 1 MiB keeps the UI responsive and progress smooth.
        started = time.time()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = _runtime_output_dir("memory_dumps")
        zip_path = os.path.join(out_dir, f"tvc_memdump_{stamp}.zip")
        manifest = {
            "created_at": stamp,
            "ranges": [],
            "errors": [],
            "duration_sec": None,
        }

        try:
            os.makedirs(out_dir, exist_ok=True)
            ranges = [
                ("mem1.raw", MEM1_LO, MEM1_HI),
                ("mem2.raw", MEM2_LO, MEM2_HI),
            ]
            total = sum(max(0, hi - lo) for _name, lo, hi in ranges)
            done = 0
            mem_dump_state.update({
                "active": True,
                "progress": 0.0,
                "label": "Dumping 0%",
                "path": zip_path,
                "error": "",
            })

            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
                for arc_name, lo, hi in ranges:
                    size = max(0, hi - lo)
                    bad_chunks = []
                    range_started = time.time()
                    with zf.open(arc_name, "w") as out_f:
                        addr = lo
                        while addr < hi:
                            n = min(chunk_size, hi - addr)
                            data = b""
                            try:
                                data = rbytes(addr, n)
                            except Exception as e:
                                manifest["errors"].append(f"{arc_name} read exception at 0x{addr:08X}: {e!r}")
                            if len(data) != n:
                                bad_chunks.append({
                                    "addr": f"0x{addr:08X}",
                                    "requested": int(n),
                                    "read": int(len(data) if data else 0),
                                })
                                # Keep raw offsets stable for diffing even if a read misses.
                                data = (data or b"") + (b"\x00" * max(0, n - len(data or b"")))
                            out_f.write(data)
                            addr += n
                            done += n
                            pct = (done / total) if total else 1.0
                            mem_dump_state["progress"] = pct
                            mem_dump_state["label"] = f"Dumping {int(pct * 100):d}%"
                    manifest["ranges"].append({
                        "file": arc_name,
                        "lo": f"0x{lo:08X}",
                        "hi": f"0x{hi:08X}",
                        "size": int(size),
                        "bad_chunks": bad_chunks,
                        "duration_sec": round(time.time() - range_started, 3),
                    })
                manifest["duration_sec"] = round(time.time() - started, 3)
                zf.writestr("manifest.json", json.dumps(manifest, indent=2))

            mem_dump_state.update({
                "active": False,
                "progress": 1.0,
                "label": "Dump complete",
                "path": zip_path,
                "last_done_time": time.time(),
                "error": "",
            })
            print(f"[memdump] wrote {zip_path}", flush=True)
        except Exception as e:
            mem_dump_state.update({
                "active": False,
                "label": "Dump failed",
                "error": repr(e),
                "last_done_time": time.time(),
            })
            print(f"[memdump] failed: {e!r}", flush=True)

    mem_dump_state.update({
        "active": True,
        "progress": 0.0,
        "label": "Dumping 0%",
        "error": "",
    })
    th = threading.Thread(target=_worker, name="TvCMemoryDump", daemon=True)
    mem_dump_state["thread"] = th
    th.start()
    return True


def _u32be_bytes(value: int) -> bytes:
    value = int(value) & 0xFFFFFFFF
    return bytes([
        (value >> 24) & 0xFF,
        (value >> 16) & 0xFF,
        (value >> 8) & 0xFF,
        value & 0xFF,
    ])


def _clamp_megacrash_chance(value) -> int:
    try:
        value = int(round(float(value)))
    except Exception:
        value = MEGACRASH_TRAINER_DEFAULT_CHANCE
    return max(0, min(100, value))


def _clamp_megacrash_delay_frames(value) -> int:
    try:
        value = int(round(float(value)))
    except Exception:
        value = MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES
    return max(0, min(MEGACRASH_TRAINER_MAX_DELAY_FRAMES, value))


def _clamp_megacrash_cooldown_sec(value) -> float:
    try:
        value = float(value)
    except Exception:
        value = MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC
    value = max(0.0, min(MEGACRASH_TRAINER_MAX_COOLDOWN_SEC, value))
    return round(value, 2)


def _clean_megacrash_target_label(value) -> str:
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    while "  " in text:
        text = text.replace("  ", " ")
    return text[:96]


def _megacrash_target_tokens(value) -> list[str]:
    text = _clean_megacrash_target_label(value)
    if not text or text.strip().lower() in {"*", "any", "all"}:
        return []
    raw = text.replace(";", ",").replace("|", ",").split(",")
    return [part.strip() for part in raw if part.strip()]


def _megacrash_norm_label(value) -> str:
    text = str(value or "").replace("\u00a0", " ").replace("_", " ").replace("-", " ").strip().casefold()
    while "  " in text:
        text = text.replace("  ", " ")
    return text


def _megacrash_tight_label(value) -> str:
    text = _megacrash_norm_label(value)
    return "".join(ch for ch in text if ch.isalnum())


_MEGACRASH_LABEL_ID_CACHE: dict[str, set[int]] | None = None


def _megacrash_label_id_cache() -> dict[str, set[int]]:
    """Map normalized move labels/aliases from the CSV to their move IDs.

    This lets the trainer target labels with spaces like "Knee A" even if the
    live HUD snapshot is carrying the move as an ID/fallback label for a frame.
    "5A" and other compact labels still work the same way.
    """
    global _MEGACRASH_LABEL_ID_CACHE
    if _MEGACRASH_LABEL_ID_CACHE is not None:
        return _MEGACRASH_LABEL_ID_CACHE

    out: dict[str, set[int]] = {}

    def add(label, mid) -> None:
        try:
            mid_i = int(mid)
        except Exception:
            return
        norm = _megacrash_norm_label(label)
        tight = _megacrash_tight_label(label)
        for key in (norm, tight):
            if key:
                out.setdefault(key, set()).add(mid_i)

    csv_path = resource_path("move_id_map_charagnostic.csv")
    try:
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.reader(f)
            for row in reader:
                if not row:
                    continue
                first = str(row[0] or "").strip()
                if not first or first.startswith("#"):
                    continue
                try:
                    mid = int(float(first))
                except Exception:
                    continue
                # Primary label plus legacy/example label columns.  This is
                # intentionally broad because several specials have display
                # aliases that differ from the canonical column.
                for idx in (2, 3, 4, 5):
                    if idx < len(row):
                        add(row[idx], mid)
    except Exception as e:
        print(f"[megacrash trainer] label alias cache unavailable: {e!r}")

    _MEGACRASH_LABEL_ID_CACHE = out
    return out


def _megacrash_label_matches(target_label, atk_label, atk_id) -> bool:
    tokens = _megacrash_target_tokens(target_label)
    if not tokens:
        return True

    label = str(atk_label or "").strip()
    candidates = set()
    if label:
        candidates.update({
            label.casefold(),
            _megacrash_norm_label(label),
            _megacrash_tight_label(label),
        })

    try:
        mid = int(atk_id) if atk_id is not None else None
    except Exception:
        mid = None
    if mid is not None:
        candidates.update({
            str(mid).casefold(),
            f"0x{mid:04x}",
            f"0x{mid:x}",
            f"{mid:04x}",
            f"{mid:x}",
        })

    alias_cache = _megacrash_label_id_cache()
    for token in tokens:
        token_norm = _megacrash_norm_label(token)
        token_tight = _megacrash_tight_label(token)
        if token.casefold() in candidates or token_norm in candidates or token_tight in candidates:
            return True
        if mid is not None:
            alias_ids = set()
            if token_norm:
                alias_ids.update(alias_cache.get(token_norm, set()))
            if token_tight:
                alias_ids.update(alias_cache.get(token_tight, set()))
            if mid in alias_ids:
                return True
    return False


def _megacrash_target_summary(value) -> str:
    text = _clean_megacrash_target_label(value)
    if not text or text.lower() in {"*", "any", "all"}:
        return "Any label"
    if len(text) > 28:
        return f"Label {text[:25]}..."
    return f"Label {text}"


def _normalize_megacrash_mode(value) -> str:
    value = str(value or "").strip().lower()
    if value in {"target", "targeted", "delay", "delayed"}:
        return "targeted"
    return "percent"


def _megacrash_mode_summary(state: dict) -> str:
    mode = _normalize_megacrash_mode(state.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE))
    cd = _clamp_megacrash_cooldown_sec(state.get("cooldown_sec", MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC))
    target_txt = _megacrash_target_summary(state.get("target_label", MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL))
    cd_txt = f" cd {cd:g}s"
    if mode == "targeted":
        return f"{target_txt} +{_clamp_megacrash_delay_frames(state.get('delay_frames', 0))}f{cd_txt}"
    return f"{target_txt} roll {_clamp_megacrash_chance(state.get('chance', MEGACRASH_TRAINER_DEFAULT_CHANCE))}%{cd_txt}"


def _load_megacrash_trainer_config() -> dict:
    cfg = {
        # Safety rule: Megacrash never auto-enables on app startup.
        # Persist the user's trainer settings, but require an explicit ON click
        # every run so an exported build or stale JSON cannot force bursts by
        # surprise.
        "enabled": False,
        "mode": MEGACRASH_TRAINER_DEFAULT_MODE,
        "chance": MEGACRASH_TRAINER_DEFAULT_CHANCE,
        "delay_frames": MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES,
        "cooldown_sec": MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC,
        "target_label": MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL,
    }
    try:
        with open(MEGACRASH_TRAINER_CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            # Intentionally ignore raw["enabled"].  The trainer is always OFF
            # at startup, even if the previous session exited while ON.
            cfg["mode"] = _normalize_megacrash_mode(raw.get("mode", cfg["mode"]))
            # Startup safety: Megacrash always launches OFF with a 0% random roll,
            # even if an old config was saved at 25/50/100%.
            cfg["chance"] = MEGACRASH_TRAINER_DEFAULT_CHANCE
            cfg["delay_frames"] = _clamp_megacrash_delay_frames(raw.get("delay_frames", cfg["delay_frames"]))
            cfg["cooldown_sec"] = _clamp_megacrash_cooldown_sec(raw.get("cooldown_sec", cfg["cooldown_sec"]))
            cfg["target_label"] = _clean_megacrash_target_label(raw.get("target_label", cfg["target_label"]))
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[megacrash trainer] config load failed: {e!r}")
    return cfg


def _save_megacrash_trainer_config(state: dict) -> None:
    try:
        save_src = state
        if isinstance(state, dict) and state.get("mission_override_active"):
            saved = state.get("mission_saved_settings")
            if isinstance(saved, dict) and saved:
                save_src = saved
        payload = {
            # Do not persist an enabled state. Megacrash must default OFF on
            # every launch, while the rest of the trainer settings persist.
            # Mission-scoped overrides are also not persisted; save the user's
            # pre-mission settings if the trainer window is opened mid-trial.
            "enabled": False,
            "mode": _normalize_megacrash_mode(save_src.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE)),
            "chance": _clamp_megacrash_chance(save_src.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE)),
            "delay_frames": _clamp_megacrash_delay_frames(save_src.get("delay_frames", MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES)),
            "cooldown_sec": _clamp_megacrash_cooldown_sec(save_src.get("cooldown_sec", MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC)),
            "target_label": _clean_megacrash_target_label(save_src.get("target_label", MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL)),
        }
        with open(MEGACRASH_TRAINER_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        print(f"[megacrash trainer] config save failed: {e!r}")


def _extract_mission_megacrash_setup(payload: dict) -> dict:
    if not isinstance(payload, dict) or not payload.get("active"):
        return {}

    raw = (
        payload.get("active_mission_setup_megacrash_trainer")
        or payload.get("active_mission_megacrash_trainer")
        or payload.get("setup_megacrash_trainer")
        or {}
    )

    if not isinstance(raw, dict):
        return {}

    out = dict(raw)
    out["enabled"] = bool(out.get("enabled", True))
    out["mode"] = _normalize_megacrash_mode(out.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE))
    out["chance"] = _clamp_megacrash_chance(out.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE))
    out["delay_frames"] = _clamp_megacrash_delay_frames(out.get("delay_frames", MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES))
    out["cooldown_sec"] = _clamp_megacrash_cooldown_sec(out.get("cooldown_sec", MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC))
    out["target_label"] = _clean_megacrash_target_label(out.get("target_label", MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL))
    return out


def _clear_megacrash_runtime_state(state: dict) -> None:
    try:
        state.setdefault("last_combo_keys", {}).clear()
        state.setdefault("pulses", {}).clear()
        state.setdefault("scheduled_triggers", {}).clear()
        state["cooldown_until"] = 0.0
    except Exception:
        pass


def _sync_mission_megacrash_trainer(state: dict, payload: dict) -> dict:
    """Apply mission-scoped Megacrash Trainer setup, then restore user settings.

    Mission JSON can provide setup_megacrash_trainer.  This lets trials that
    need a controlled burst turn the trainer on only while that mission is the
    active mission.  It never persists enabled=True and it restores the user's
    normal Megacrash settings when the mission changes or mission mode is off.
    """
    if not isinstance(state, dict):
        state = _load_megacrash_trainer_config()

    setup = _extract_mission_megacrash_setup(payload)
    mission_key = None
    if setup:
        mission_key = (
            payload.get("slot"),
            payload.get("character"),
            payload.get("active_mission_id"),
        )

    current_key = state.get("mission_override_key")

    if not setup:
        if current_key is not None:
            saved = state.pop("mission_saved_settings", {}) or {}
            for key, value in saved.items():
                state[key] = value
            state.pop("mission_override_key", None)
            state.pop("mission_override_name", None)
            state["mission_override_active"] = False
            _clear_megacrash_runtime_state(state)
            print("[megacrash trainer] mission override restored user settings")
        return state

    if current_key != mission_key:
        saved = {
            "enabled": bool(state.get("enabled", False)),
            "mode": _normalize_megacrash_mode(state.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE)),
            "chance": _clamp_megacrash_chance(state.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE)),
            "delay_frames": _clamp_megacrash_delay_frames(state.get("delay_frames", MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES)),
            "cooldown_sec": _clamp_megacrash_cooldown_sec(state.get("cooldown_sec", MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC)),
            "target_label": _clean_megacrash_target_label(state.get("target_label", MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL)),
        }
        state["mission_saved_settings"] = saved
        state["mission_override_key"] = mission_key
        state["mission_override_name"] = str(payload.get("active_mission_name") or payload.get("active_mission_id") or "mission")
        _clear_megacrash_runtime_state(state)
        print(
            "[megacrash trainer] mission override "
            f"{payload.get('active_mission_id')}: "
            f"{setup.get('mode')} label={setup.get('target_label') or 'any'} "
            f"+{setup.get('delay_frames')}f cd={setup.get('cooldown_sec')}s"
        )

    state["mission_override_active"] = True
    state["enabled"] = bool(setup.get("enabled", True))
    state["mode"] = _normalize_megacrash_mode(setup.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE))
    state["chance"] = _clamp_megacrash_chance(setup.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE))
    state["delay_frames"] = _clamp_megacrash_delay_frames(setup.get("delay_frames", MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES))
    state["cooldown_sec"] = _clamp_megacrash_cooldown_sec(setup.get("cooldown_sec", MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC))
    state["target_label"] = _clean_megacrash_target_label(setup.get("target_label", MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL))
    return state


def _cycle_megacrash_chance(current: int) -> int:
    cur = _clamp_megacrash_chance(current)
    presets = list(MEGACRASH_TRAINER_CHANCE_PRESETS)
    for value in presets:
        if value > cur:
            return value
    return presets[0]


def _snap_action_id(snap: dict) -> int | None:
    if not isinstance(snap, dict):
        return None
    for key in ("mv_id_display", "attA", "attB", "move_id", "cur_anim", "current_anim"):
        try:
            value = snap.get(key)
            if value is not None:
                return int(value)
        except Exception:
            pass
    return None


def _snap_primary_action_id(snap: dict) -> int | None:
    """Return the live primary move/action word only.

    The trainer writes Megacrash through ATT_ID_OFF_PRIMARY (base+0x1E8).
    Using the display id here is unsafe because display id falls back from
    attA to attB; attB can mirror/stale a reaction value and make the
    attacking point look like a victim.
    """
    if not isinstance(snap, dict):
        return None
    try:
        value = snap.get("attA")
        return int(value) if value is not None else None
    except Exception:
        return None


def _snap_is_hitstun_primary(snap: dict) -> bool:
    mid = _snap_primary_action_id(snap)
    return bool(mid in REACTION_STATES if mid is not None else False)


def _opponent_teamtag(teamtag: str) -> str:
    return "P2" if str(teamtag) == "P1" else "P1"


def _snap_move_label(snap: dict) -> str:
    if not isinstance(snap, dict):
        return ""
    label = str(snap.get("mv_label") or "").strip()
    if label:
        return label
    mid = _snap_action_id(snap)
    return f"0x{mid:04X}" if mid is not None else ""


def _is_support_or_assist_snap(snap: dict) -> bool:
    if not isinstance(snap, dict):
        return True
    label = str(snap.get("mv_label") or "").strip().lower()
    mid = _snap_action_id(snap)
    ko_state = bool(("ko" in label) or ((snap.get("cur") or 0) <= 0))
    return bool(
        ko_state
        or (mid in MEGACRASH_SUPPORT_STATE_IDS if mid is not None else False)
        or ("assist" in label)
        or ("tag out" in label)
        or ("tag in taunt" in label)
    )


def _team_point_slot_for_megacrash(teamtag: str, snaps: dict) -> str | None:
    """Return the team's point slot for trainer purposes.

    Normal matches are C1 point / C2 assist. If C1 is visibly in a support/tag/KO
    state while C2 is not, treat C2 as the point so swapped teams still work.
    This intentionally keeps assists from being selected when they get clipped.
    """
    c1_key = f"{teamtag}-C1"
    c2_key = f"{teamtag}-C2"
    c1 = snaps.get(c1_key)
    c2 = snaps.get(c2_key)
    if c1 and not _is_support_or_assist_snap(c1):
        return c1_key
    if c2 and not _is_support_or_assist_snap(c2):
        return c2_key
    if c1:
        return c1_key
    if c2:
        return c2_key
    return None


def _nearest_opponent_snap(vic_snap: dict, snaps: dict) -> dict | None:
    if not isinstance(vic_snap, dict):
        return None
    vic_team = vic_snap.get("teamtag")
    candidates = [s for s in snaps.values() if isinstance(s, dict) and s.get("teamtag") != vic_team]
    if not candidates:
        return None

    best_snap = None
    best_d2 = None
    for cand in candidates:
        try:
            d2v = dist2(vic_snap, cand)
        except Exception:
            d2v = None
        if d2v is None:
            continue
        if best_d2 is None or d2v < best_d2:
            best_d2 = d2v
            best_snap = cand
    return best_snap or candidates[0]


def _megacrash_cooldown_remaining(state: dict, now: float) -> float:
    try:
        cooldown_until = float(state.get("cooldown_until", 0.0) or 0.0)
    except Exception:
        cooldown_until = 0.0
    return max(0.0, cooldown_until - float(now))


def _megacrash_combo_key_for_attacker(atk_slot: str, atk_snap: dict) -> tuple | None:
    atk_label = _snap_move_label(atk_snap)
    atk_id = _snap_action_id(atk_snap)
    if not atk_label and atk_id is None:
        return None
    return (
        str(atk_snap.get("base") or atk_slot),
        int(atk_id) if atk_id is not None else -1,
        str(atk_label).strip().lower(),
    )


def _megacrash_mark_visible_combo_keys(snaps: dict, last_keys: dict) -> None:
    """Consume current labels during cooldown without rolling on stale labels later."""
    for teamtag in ("P1", "P2"):
        vic_slot = _team_point_slot_for_megacrash(teamtag, snaps)
        if not vic_slot:
            continue
        vic_snap = snaps.get(vic_slot)
        if not isinstance(vic_snap, dict):
            continue
        try:
            base = int(vic_snap.get("base") or 0)
        except Exception:
            base = 0
        if not base:
            continue
        if not _snap_is_hitstun_primary(vic_snap):
            last_keys.pop(base, None)
            continue

        atk_slot = _team_point_slot_for_megacrash(_opponent_teamtag(teamtag), snaps)
        if not atk_slot:
            continue
        atk_snap = snaps.get(atk_slot)
        if not isinstance(atk_snap, dict) or _is_support_or_assist_snap(atk_snap):
            continue
        atk_primary = _snap_primary_action_id(atk_snap)
        if atk_primary in REACTION_STATES or atk_primary == MEGACRASH_MOVE_ID:
            continue
        combo_key = _megacrash_combo_key_for_attacker(atk_slot, atk_snap)
        if combo_key is not None:
            last_keys[base] = combo_key


def _start_megacrash_trainer_pulse(state: dict, vic_snap: dict, now: float, reason: str = "") -> bool:
    # Absolute safety gate.  No caller, stale schedule, or old pulse is allowed
    # to write Megacrash unless the trainer is currently enabled.  In random
    # mode, 0% is also a hard no-op.
    if not isinstance(state, dict) or not bool(state.get("enabled", False)):
        try:
            state.setdefault("pulses", {}).clear()
            state.setdefault("scheduled_triggers", {}).clear()
            state["cooldown_until"] = 0.0
        except Exception:
            pass
        return False

    mode = _normalize_megacrash_mode(state.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE))
    chance = _clamp_megacrash_chance(state.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE))
    if mode == "percent" and chance <= 0:
        try:
            state.setdefault("pulses", {}).clear()
            state.setdefault("scheduled_triggers", {}).clear()
            state["cooldown_until"] = 0.0
        except Exception:
            pass
        return False

    base = 0
    try:
        base = int(vic_snap.get("base") or 0)
    except Exception:
        base = 0
    if not base:
        return False

    pulses = state.setdefault("pulses", {})
    wrote_any = False
    pulse_entries = []
    for off in MEGACRASH_TRAINER_WRITE_OFFSETS:
        addr = base + int(off)
        if not addr_in_ram(addr):
            continue
        if runtime_pm is not None:
            ok_write = runtime_pm.write_u32(addr, MEGACRASH_MOVE_ID, key="megacrash:start", dirty=False, force=True)
        else:
            ok_write = wd32(addr, MEGACRASH_MOVE_ID)
        if ok_write:
            wrote_any = True
            pulse_entries.append(addr)

    if wrote_any:
        slot = str(vic_snap.get("slotname") or vic_snap.get("slot_label") or "?")
        cooldown_sec = _clamp_megacrash_cooldown_sec(state.get("cooldown_sec", MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC))
        state["cooldown_until"] = now + cooldown_sec if cooldown_sec > 0.0 else 0.0
        try:
            state.setdefault("scheduled_triggers", {}).clear()
        except Exception:
            pass
        pulses[base] = {
            "slot": slot,
            "addrs": pulse_entries,
            "end": now + MEGACRASH_TRAINER_PULSE_SEC,
            "reason": reason,
        }
        state["last_trigger"] = {
            "slot": slot,
            "time": now,
            "reason": reason,
        }
        state["trigger_count"] = int(state.get("trigger_count", 0) or 0) + 1
        print(f"[megacrash trainer] trigger {slot}: {reason}")
    return wrote_any


def _tick_megacrash_trainer(state: dict, snaps: dict, now: float, frame_idx: int | None = None) -> dict:
    if not isinstance(state, dict):
        state = {}

    state.setdefault("enabled", False)
    state["mode"] = _normalize_megacrash_mode(state.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE))
    state["chance"] = _clamp_megacrash_chance(state.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE))
    state["delay_frames"] = _clamp_megacrash_delay_frames(state.get("delay_frames", MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES))
    state["cooldown_sec"] = _clamp_megacrash_cooldown_sec(state.get("cooldown_sec", MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC))
    state["target_label"] = _clean_megacrash_target_label(state.get("target_label", MEGACRASH_TRAINER_DEFAULT_TARGET_LABEL))
    pulses = state.setdefault("pulses", {})
    last_keys = state.setdefault("last_combo_keys", {})
    scheduled = state.setdefault("scheduled_triggers", {})
    if frame_idx is None:
        try:
            frame_idx = int(round(now * TARGET_FPS))
        except Exception:
            frame_idx = 0

    # Absolute OFF gate comes before pulse replay.  The old order replayed an
    # already-started pulse for a few frames even after the trainer was turned
    # off.  OFF now means no writes this frame, period.
    if not bool(state.get("enabled", False)):
        try:
            pulses.clear()
            scheduled.clear()
            last_keys.clear()
            state["cooldown_until"] = 0.0
        except Exception:
            pass
        return state

    mode = _normalize_megacrash_mode(state.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE))
    chance = state["chance"]
    if mode == "percent" and chance <= 0:
        try:
            pulses.clear()
            scheduled.clear()
            state["cooldown_until"] = 0.0
        except Exception:
            pass
        return state

    snaps = snaps or {}
    snaps_by_base = {}
    for _slot, _snap in list(snaps.items()):
        if not isinstance(_snap, dict):
            continue
        try:
            _base = int(_snap.get("base") or 0)
        except Exception:
            _base = 0
        if _base:
            snaps_by_base[_base] = _snap

    # Keep the Megacrash poke pinned only until the game visibly accepts 448,
    # then release immediately.  This prevents the trainer from manufacturing
    # a permanent-looking Megacrash hitbox before the real burst animation owns
    # the state.
    for base, pulse in list(pulses.items()):
        try:
            base_i = int(base)
        except Exception:
            base_i = 0
        try:
            end_ts = float(pulse.get("end", 0.0) or 0.0)
        except Exception:
            end_ts = 0.0

        live_snap = snaps_by_base.get(base_i)
        live_primary = _snap_primary_action_id(live_snap) if live_snap else None
        if now >= end_ts or live_primary == MEGACRASH_MOVE_ID:
            pulses.pop(base, None)
            continue

        for addr in list(pulse.get("addrs") or []):
            try:
                addr = int(addr)
            except Exception:
                addr = 0
            if addr and addr_in_ram(addr):
                if runtime_pm is not None:
                    runtime_pm.write_u32(addr, MEGACRASH_MOVE_ID, key="megacrash:pulse", dirty=False, force=True)
                else:
                    wd32(addr, MEGACRASH_MOVE_ID)

    cooldown_remaining = _megacrash_cooldown_remaining(state, now)
    if cooldown_remaining > 0.0:
        # While cooling down, do not roll or fire scheduled bursts.  Consume the
        # currently visible combo labels so the trainer waits for a truly new
        # attacker label after the cooldown expires.
        scheduled.clear()
        _megacrash_mark_visible_combo_keys(snaps, last_keys)
        return state

    # Process targeted delayed-burst schedules. A schedule is tied to the victim
    # point base and only fires if that same point is still in primary hitstun.
    for base, pending in list(scheduled.items()):
        try:
            base_i = int(base)
        except Exception:
            base_i = 0
        if not base_i:
            scheduled.pop(base, None)
            continue
        live_snap = snaps_by_base.get(base_i)
        if not isinstance(live_snap, dict):
            scheduled.pop(base, None)
            continue
        if _snap_primary_action_id(live_snap) == MEGACRASH_MOVE_ID:
            scheduled.pop(base, None)
            continue
        if not _snap_is_hitstun_primary(live_snap):
            scheduled.pop(base, None)
            continue

        try:
            fire_frame = int(pending.get("fire_frame", 0) or 0)
        except Exception:
            fire_frame = 0
        try:
            fire_time = float(pending.get("fire_time", 0.0) or 0.0)
        except Exception:
            fire_time = 0.0

        due = bool(frame_idx >= fire_frame if fire_frame else now >= fire_time)
        if not due:
            continue

        reason = str(pending.get("reason") or "targeted delayed label")
        _start_megacrash_trainer_pulse(state, live_snap, now, reason=reason)
        scheduled.pop(base, None)

    # Trainer logic is team-point vs team-point.  Assists/projectiles are not
    # allowed to become the attacker key or the victim target.  A roll happens
    # once for the current attacker label while the point victim stays in
    # hitstun; the same label cannot roll again until the attacker label changes
    # or the victim leaves hitstun and starts a new hitstun sequence.
    for teamtag in ("P1", "P2"):
        vic_slot = _team_point_slot_for_megacrash(teamtag, snaps)
        if not vic_slot:
            continue
        vic_snap = snaps.get(vic_slot)
        if not isinstance(vic_snap, dict):
            continue
        if _is_support_or_assist_snap(vic_snap):
            continue

        try:
            base = int(vic_snap.get("base") or 0)
        except Exception:
            base = 0
        if not base:
            continue

        if base in pulses or str(base) in pulses:
            continue

        if _snap_primary_action_id(vic_snap) == MEGACRASH_MOVE_ID:
            last_keys.pop(base, None)
            continue

        if not _snap_is_hitstun_primary(vic_snap):
            last_keys.pop(base, None)
            continue

        atk_team = _opponent_teamtag(teamtag)
        atk_slot = _team_point_slot_for_megacrash(atk_team, snaps)
        if not atk_slot:
            continue
        atk_snap = snaps.get(atk_slot)
        if not isinstance(atk_snap, dict):
            continue
        if _is_support_or_assist_snap(atk_snap):
            continue

        atk_primary = _snap_primary_action_id(atk_snap)
        if atk_primary in REACTION_STATES or atk_primary == MEGACRASH_MOVE_ID:
            # Do not let a simultaneously hitstunned point roll against the
            # other victim. This was the path that could make both point chars
            # burst from one clean hit.
            continue

        atk_label = _snap_move_label(atk_snap)
        atk_id = _snap_action_id(atk_snap)
        if not atk_label and atk_id is None:
            continue
        if str(atk_label).strip().lower() == "megacrash":
            continue

        combo_key = _megacrash_combo_key_for_attacker(atk_slot, atk_snap)
        if combo_key is None:
            continue
        if last_keys.get(base) == combo_key:
            continue
        last_keys[base] = combo_key

        if not _megacrash_label_matches(state.get("target_label", ""), atk_label, atk_id):
            continue

        if mode == "targeted":
            delay_frames = _clamp_megacrash_delay_frames(state.get("delay_frames", 0))
            fire_frame = int(frame_idx or 0) + delay_frames
            scheduled[base] = {
                "slot": str(vic_snap.get("slotname") or vic_slot),
                "attacker": str(atk_slot),
                "label": str(atk_label or atk_id),
                "fire_frame": fire_frame,
                "fire_time": now + (delay_frames / float(TARGET_FPS)),
                "reason": f"{atk_slot} {atk_label or atk_id} targeted +{delay_frames}f",
            }
            state["roll_count"] = int(state.get("roll_count", 0) or 0) + 1
            if int(state.get("roll_count", 0) or 0) % 20 == 1:
                print(f"[megacrash trainer] schedule {vic_slot}: {atk_slot} {atk_label or atk_id} +{delay_frames}f")
            continue

        # Use random.random()*100 and a strict < comparison so 0% can never
        # pass, while 100% still always passes.
        roll = random.random() * 100.0
        state["roll_count"] = int(state.get("roll_count", 0) or 0) + 1
        if chance > 0 and roll < float(chance):
            reason = f"{atk_slot} {atk_label or atk_id} roll {roll:.1f}<{chance}%"
            _start_megacrash_trainer_pulse(state, vic_snap, now, reason=reason)
        else:
            if frame_idx_mod := int(state.get("roll_count", 0) or 0):
                if frame_idx_mod % 20 == 0:
                    print(f"[megacrash trainer] roll skip {atk_slot} {atk_label or atk_id}: {roll:.1f}>={chance}%")

    return state


# GUI polish helpers
# ---------------------------------------------------------------------------

GUI_BG_DARK = (10, 11, 16)
GUI_PANEL = (20, 22, 30)
GUI_PANEL_2 = (28, 31, 42)
GUI_PANEL_3 = (36, 41, 56)

GUI_BORDER = (80, 88, 112)
GUI_BORDER_HOT = (145, 165, 205)

GUI_TEXT = (226, 230, 238)
GUI_TEXT_MUTED = (150, 158, 176)
GUI_TEXT_DIM = (110, 116, 132)

# Cohesive accent system.
# Most of the UI uses the same steel-blue accent. Slot/player identity stays
# mostly in the slim side rails so the app feels unified instead of rainbow.
GUI_APP_ACCENT = (105, 145, 210)
GUI_CONFIRM = (95, 205, 165)
GUI_WARNING = (205, 170, 90)
GUI_DANGER = (205, 80, 90)

GUI_ACCENT_BLUE = GUI_APP_ACCENT
GUI_ACCENT_PURPLE = (125, 135, 185)
GUI_ACCENT_GOLD = GUI_APP_ACCENT
GUI_ACCENT_GREEN = GUI_CONFIRM
GUI_ACCENT_RED = GUI_DANGER

GUI_P1 = (205, 75, 82)
GUI_P2 = (82, 135, 215)
GUI_P3 = (180, 90, 175)
GUI_P4 = (92, 185, 135)

GUI_SLOT_MUTED = {
    "P1": (185, 78, 86),
    "P2": (82, 128, 200),
    "P3": (165, 82, 160),
    "P4": (82, 165, 122),
}


def _clamp_u8(v: int) -> int:
    return max(0, min(255, int(v)))


def _brighten(col: tuple[int, int, int], amt: int) -> tuple[int, int, int]:
    return (
        _clamp_u8(col[0] + amt),
        _clamp_u8(col[1] + amt),
        _clamp_u8(col[2] + amt),
    )


def _darken(col: tuple[int, int, int], amt: int) -> tuple[int, int, int]:
    return (
        _clamp_u8(col[0] - amt),
        _clamp_u8(col[1] - amt),
        _clamp_u8(col[2] - amt),
    )


def _mix_col(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, float(t)))
    return (
        _clamp_u8(a[0] * (1.0 - t) + b[0] * t),
        _clamp_u8(a[1] * (1.0 - t) + b[1] * t),
        _clamp_u8(a[2] * (1.0 - t) + b[2] * t),
    )


def _slot_accent_for_label(slot_label: str, *, muted: bool = False) -> tuple[int, int, int]:
    label = str(slot_label or "")
    if label.startswith("P1"):
        base = GUI_P1 if label.endswith("C1") else GUI_P3
    elif label.startswith("P2"):
        base = GUI_P2 if label.endswith("C1") else GUI_P4
    else:
        base = GUI_APP_ACCENT
    if muted:
        return _mix_col(base, GUI_APP_ACCENT, 0.45)
    return base


def _draw_vertical_gradient(
    surf: pygame.Surface,
    rect: pygame.Rect,
    top_col: tuple[int, int, int],
    bot_col: tuple[int, int, int],
    alpha: int = 255,
) -> None:
    if rect.width <= 0 or rect.height <= 0:
        return

    grad = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)

    for y in range(rect.height):
        t = y / max(1, rect.height - 1)
        r = int(top_col[0] * (1.0 - t) + bot_col[0] * t)
        g = int(top_col[1] * (1.0 - t) + bot_col[1] * t)
        b = int(top_col[2] * (1.0 - t) + bot_col[2] * t)
        pygame.draw.line(grad, (r, g, b, alpha), (0, y), (rect.width, y))

    surf.blit(grad, rect.topleft)


def _fit_text(
    font: pygame.font.Font,
    text: str,
    color: tuple[int, int, int],
    max_width: int,
) -> pygame.Surface:
    text = str(text or "")
    if max_width <= 8:
        return font.render("", True, color)

    surf = font.render(text, True, color)
    if surf.get_width() <= max_width:
        return surf

    if len(text) <= 1:
        return font.render("", True, color)

    low = 0
    high = len(text)
    best = ""

    while low <= high:
        mid = (low + high) // 2
        trial = text[:mid].rstrip() + "."
        trial_surf = font.render(trial, True, color)
        if trial_surf.get_width() <= max_width:
            best = trial
            low = mid + 1
        else:
            high = mid - 1

    return font.render(best, True, color)

def _render_outlined_text(
    font: pygame.font.Font,
    text: str,
    text_color: tuple[int, int, int],
    outline_color: tuple[int, int, int],
    max_width: int,
    outline_px: int = 1,
) -> pygame.Surface:
    base = _fit_text(font, text, text_color, max_width)

    w = base.get_width()
    h = base.get_height()

    if w <= 0 or h <= 0:
        return base

    pad = max(1, int(outline_px))
    out = pygame.Surface((w + pad * 2, h + pad * 2), pygame.SRCALPHA)

    for ox, oy in (
        (-pad, -pad), (0, -pad), (pad, -pad),
        (-pad, 0),                (pad, 0),
        (-pad, pad),  (0, pad),   (pad, pad),
    ):
        outline = _fit_text(font, text, outline_color, max_width)
        out.blit(outline, (pad + ox, pad + oy))

    out.blit(base, (pad, pad))
    return out


def _render_rainbow_outlined_text(
    font: pygame.font.Font,
    text: str,
    max_width: int,
    t_ms: int,
    outline_color: tuple[int, int, int] = (0, 0, 0),
    outline_px: int = 1,
) -> pygame.Surface:
    """Render fitted text with a soft animated rainbow fill and dark outline.

    Used for the Baroque line only. It is display-only and keeps the same
    truncation behavior as _fit_text.
    """
    base = _fit_text(font, text, (255, 255, 255), max_width)
    w = base.get_width()
    h = base.get_height()
    if w <= 0 or h <= 0:
        return base

    pad = max(1, int(outline_px))
    out = pygame.Surface((w + pad * 2, h + pad * 2), pygame.SRCALPHA)

    outline = _fit_text(font, text, outline_color, max_width)
    for ox, oy in (
        (-pad, -pad), (0, -pad), (pad, -pad),
        (-pad, 0),                (pad, 0),
        (-pad, pad),  (0, pad),   (pad, pad),
    ):
        out.blit(outline, (pad + ox, pad + oy))

    rainbow = pygame.Surface((w, h), pygame.SRCALPHA)
    phase = (float(t_ms) / 1000.0) * 0.35
    for x in range(w):
        t = (x / max(1, w - 1)) + phase
        r = int(190 + 55 * math.sin(2.0 * math.pi * (t + 0.00)))
        g = int(185 + 55 * math.sin(2.0 * math.pi * (t + 0.33)))
        b = int(220 + 35 * math.sin(2.0 * math.pi * (t + 0.66)))
        pygame.draw.line(
            rainbow,
            (_clamp_u8(r), _clamp_u8(g), _clamp_u8(b), 255),
            (x, 0),
            (x, h),
        )

    colored = base.copy()
    colored.blit(rainbow, (0, 0), special_flags=pygame.BLEND_MULT)
    out.blit(colored, (pad, pad))
    return out


def draw_glass_button(
    surf: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    font: pygame.font.Font,
    *,
    active: bool = False,
    hover: bool = False,
    accent: tuple[int, int, int] = GUI_ACCENT_BLUE,
    fill: tuple[int, int, int] | None = None,
    align: str = "center",
) -> None:
    """Shared button renderer with stronger hover/elevation feedback.

    The goal is to make the GUI feel a little more premium without changing any
    interaction logic. Hovered buttons lift slightly with a brighter shell and a
    faint shadow. Active buttons keep the accent rail.
    """
    base = fill if fill is not None else (GUI_PANEL_3 if active else GUI_PANEL_2)
    if hover:
        base = _brighten(base, 16)

    border = GUI_BORDER_HOT if hover else (accent if active else GUI_BORDER)
    text_col = GUI_TEXT if active or hover else GUI_TEXT_MUTED

    # Soft shadow/elevation on hover/active.
    if hover or active:
        shadow = pygame.Surface((rect.width + 6, rect.height + 6), pygame.SRCALPHA)
        pygame.draw.rect(shadow, (0, 0, 0, 45 if hover else 32), shadow.get_rect(), border_radius=6)
        surf.blit(shadow, (rect.x - 1, rect.y + 2))

    draw_rect = rect.move(0, -1 if hover else 0)

    _draw_vertical_gradient(
        surf,
        draw_rect,
        _brighten(base, 12),
        _darken(base, 6),
        235,
    )

    pygame.draw.rect(surf, border, draw_rect, 1, border_radius=4)

    shine = pygame.Rect(draw_rect.x + 2, draw_rect.y + 2, draw_rect.width - 4, max(2, draw_rect.height // 6))
    shine_col = (150, 165, 190, 16) if active or hover else (118, 128, 150, 11)
    pygame.draw.rect(surf, shine_col, shine, border_radius=3)

    if active:
        accent_rect = pygame.Rect(draw_rect.x + 4, draw_rect.bottom - 3, draw_rect.width - 8, 2)
        pygame.draw.rect(surf, accent, accent_rect, border_radius=1)

    label_surf = _render_outlined_text(
        font,
        label,
        text_col,
        (0, 0, 0),
        draw_rect.width - 12,
        outline_px=1,
    )

    if align == "left":
        tx = draw_rect.x + 7
    elif align == "right":
        tx = draw_rect.right - label_surf.get_width() - 7
    else:
        tx = draw_rect.x + (draw_rect.width - label_surf.get_width()) // 2

    ty = draw_rect.y + (draw_rect.height - label_surf.get_height()) // 2
    surf.blit(label_surf, (tx, ty))



def draw_slot_chip(
    surf: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    font: pygame.font.Font,
    *,
    enabled: bool,
    accent: tuple[int, int, int],
    hover: bool,
) -> None:
    fill = (28, 31, 42) if enabled else (22, 24, 31)
    border = accent if enabled else (62, 68, 84)
    text_col = GUI_TEXT if enabled else GUI_TEXT_DIM

    if hover:
        fill = _brighten(fill, 12)
        border = _brighten(border, 18)

    _draw_vertical_gradient(
        surf,
        rect,
        _brighten(fill, 8),
        _darken(fill, 7),
        235,
    )

    pygame.draw.rect(surf, border, rect, 1, border_radius=4)

    # Slot identity is a tiny left rail, not a full loud badge.
    if enabled:
        pygame.draw.rect(surf, accent, pygame.Rect(rect.x + 2, rect.y + 3, 2, rect.height - 6), border_radius=1)

    state = "ON" if enabled else "OFF"
    text = f"{label} {state}"

    label_surf = _render_outlined_text(
        font,
        text,
        text_col,
        (0, 0, 0),
        rect.width - 10,
        outline_px=1,
    )

    surf.blit(
        label_surf,
        (
            rect.x + (rect.width - label_surf.get_width()) // 2,
            rect.y + (rect.height - label_surf.get_height()) // 2,
        ),
    )


def draw_top_command_dock(
    screen: pygame.Surface,
    smallfont: pygame.font.Font,
    *,
    hitbox_slots: dict,
    overlay_enabled: bool,
    megacrash_trainer_enabled: bool = False,
    megacrash_trainer_chance: int = MEGACRASH_TRAINER_DEFAULT_CHANCE,
    megacrash_trainer_mode: str = MEGACRASH_TRAINER_DEFAULT_MODE,
    megacrash_trainer_delay_frames: int = MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES,
    megacrash_trainer_cooldown_sec: float = MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC,
    megacrash_trainer_cooldown_remaining: float = 0.0,
    mem_dump_active: bool = False,
    mem_dump_label: str = "",
    select_probe_label: str = "CS Probe",
    select_probe_active: bool = False,
    win_score_enabled: bool = False,
    mouse_pos: tuple[int, int],
    t_ms: int = 0,
) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect, pygame.Rect, pygame.Rect, pygame.Rect, pygame.Rect, pygame.Rect, dict]:
    """Draw the compact two-row main command dock.

    The old dock placed every command in one long row and kept a permanent tip
    string on the right.  This version keeps frequently-toggled state controls
    on the first row, tools on the second row, and replaces the permanent tip
    string with a hover-sensitive help box.  Runtime behavior and returned rects
    stay the same.
    """
    mx, my = mouse_pos
    w, _h = screen.get_size()

    dock_rect = pygame.Rect(0, 0, w, TOP_UI_RESERVED - 4)
    _draw_vertical_gradient(
        screen,
        dock_rect,
        (12, 13, 19),
        (8, 9, 13),
        255,
    )
    pygame.draw.line(screen, (58, 64, 82), (0, dock_rect.bottom - 1), (w, dock_rect.bottom - 1))

    gap = 8
    y_top = 7
    y_tools = 35
    btn_h = 22
    x = 8

    # Projectile editing lives inside each slot's Frame Data Workbench.  Keep a
    # dummy rect for older click handling and return tuple compatibility.
    ps_btn_rect = pygame.Rect(-9999, -9999, 0, 0)

    # First row: live toggles/status. Win Score is first because it defaults
    # on at 0, while keeping NEW HERO as the normal zero display.
    win_counter_btn_rect = pygame.Rect(x, y_top, 126, btn_h)
    draw_glass_button(
        screen,
        win_counter_btn_rect,
        "Win Score: ON" if win_score_enabled else "Win Score: OFF",
        smallfont,
        active=bool(win_score_enabled),
        hover=win_counter_btn_rect.collidepoint(mx, my),
        accent=GUI_APP_ACCENT,
        fill=(44, 56, 82) if win_score_enabled else (31, 33, 42),
        align="center",
    )

    x = win_counter_btn_rect.right + gap
    hud_btn_rect = pygame.Rect(x, y_top, 132, btn_h)
    draw_glass_button(
        screen,
        hud_btn_rect,
        "Overlay: ON" if overlay_enabled else "Overlay: OFF",
        smallfont,
        active=overlay_enabled,
        hover=hud_btn_rect.collidepoint(mx, my),
        accent=GUI_APP_ACCENT,
        align="center",
    )

    x = hud_btn_rect.right + gap
    hb_btn_rect = pygame.Rect(x, y_top, 134, btn_h)
    hb_on = any(hitbox_slots.values())
    draw_glass_button(
        screen,
        hb_btn_rect,
        "Hitboxes: ON" if hb_on else "Hitboxes: OFF",
        smallfont,
        active=hb_on,
        hover=hb_btn_rect.collidepoint(mx, my),
        accent=GUI_APP_ACCENT,
        align="center",
    )

    chip_y = y_top + 2
    chip_x = hb_btn_rect.right + 8
    chip_w = 50
    chip_h = 18
    chip_gap = 5
    slot_colors = dict(GUI_SLOT_MUTED)
    hb_filter_rects = {}

    for slot_name in ("P1", "P2", "P3", "P4"):
        chip_rect = pygame.Rect(chip_x, chip_y, chip_w, chip_h)
        draw_slot_chip(
            screen,
            chip_rect,
            slot_name,
            smallfont,
            enabled=bool(hitbox_slots.get(slot_name, False)),
            accent=slot_colors.get(slot_name, GUI_ACCENT_BLUE),
            hover=chip_rect.collidepoint(mx, my),
        )
        hb_filter_rects[slot_name] = chip_rect.inflate(4, 4)
        chip_x += chip_w + chip_gap

    x = chip_x + 4
    megacrash_btn_rect = pygame.Rect(x, y_top, 144, btn_h)
    mega_label = f"Megacrash: {'ON' if megacrash_trainer_enabled else 'OFF'}"
    draw_glass_button(
        screen,
        megacrash_btn_rect,
        mega_label,
        smallfont,
        active=bool(megacrash_trainer_enabled),
        hover=megacrash_btn_rect.collidepoint(mx, my),
        accent=GUI_APP_ACCENT,
        fill=(44, 56, 82) if megacrash_trainer_enabled else (31, 33, 42),
        align="center",
    )

    # Second row: tools that open helper windows or one-shot actions.
    x = 8
    as_btn_rect = pygame.Rect(x, y_tools, 132, btn_h)
    draw_glass_button(
        screen,
        as_btn_rect,
        "Assist Scanner",
        smallfont,
        active=False,
        hover=as_btn_rect.collidepoint(mx, my),
        accent=GUI_APP_ACCENT,
        align="center",
    )

    x = as_btn_rect.right + gap
    overseer_btn_rect = pygame.Rect(x, y_tools, 108, btn_h)
    draw_glass_button(
        screen,
        overseer_btn_rect,
        "Tool State",
        smallfont,
        active=False,
        hover=overseer_btn_rect.collidepoint(mx, my),
        accent=GUI_APP_ACCENT,
        fill=(31, 33, 42),
        align="center",
    )

    x = overseer_btn_rect.right + gap
    memdump_btn_rect = pygame.Rect(x, y_tools, 110, btn_h)
    dump_label = mem_dump_label if mem_dump_active and mem_dump_label else "Dump MEM"
    draw_glass_button(
        screen,
        memdump_btn_rect,
        dump_label,
        smallfont,
        active=bool(mem_dump_active),
        hover=memdump_btn_rect.collidepoint(mx, my),
        accent=GUI_APP_ACCENT,
        fill=(44, 56, 82) if mem_dump_active else (31, 33, 42),
        align="center",
    )

    x = memdump_btn_rect.right + gap
    select_probe_btn_rect = pygame.Rect(x, y_tools, 104, btn_h)
    draw_glass_button(
        screen,
        select_probe_btn_rect,
        select_probe_label or "CS Probe",
        smallfont,
        active=bool(select_probe_active),
        hover=select_probe_btn_rect.collidepoint(mx, my),
        accent=GUI_APP_ACCENT,
        fill=(52, 42, 32) if select_probe_active else (31, 33, 42),
        align="center",
    )

    help_tip = "Hover a command for help. Right click panels/debug rows to copy."
    help_accent = GUI_APP_ACCENT
    if hb_btn_rect.collidepoint(mx, my):
        help_tip = "Hitboxes: master toggle for drawing hitboxes in the HUD."
    elif any(rect.collidepoint(mx, my) for rect in hb_filter_rects.values()):
        help_tip = "P1-P4 chips: show or hide hitboxes for that specific slot."
    elif hud_btn_rect.collidepoint(mx, my):
        help_tip = "Overlay: starts or stops the master overlay process."
    elif megacrash_btn_rect.collidepoint(mx, my):
        help_tip = "Megacrash: opens trainer settings. It starts OFF and 0 percent by default."
    elif as_btn_rect.collidepoint(mx, my):
        help_tip = "Assist Scanner: inspect routes and choose quick assists."
    elif win_counter_btn_rect.collidepoint(mx, my):
        help_tip = "Win Score: visible win count controls. Defaults ON at 0, with NEW HERO kept for zero."
    elif overseer_btn_rect.collidepoint(mx, my):
        help_tip = "Tool State: live state, safe restore, hard reset, and debug dumps."
    elif memdump_btn_rect.collidepoint(mx, my):
        help_tip = "Dump MEM: save a memory dump for route/profile analysis."
    elif select_probe_btn_rect.collidepoint(mx, my):
        help_tip = "CS Probe: opens the character-select probe window. Right click still restores all probe writes."

    help_x = select_probe_btn_rect.right + 12
    help_w = max(160, w - help_x - 10)
    if help_w >= 180:
        help_rect = pygame.Rect(help_x, y_tools, help_w, btn_h)
        _draw_vertical_gradient(
            screen,
            help_rect,
            (23, 25, 35),
            (16, 18, 26),
            235,
        )
        pygame.draw.rect(screen, (52, 58, 76), help_rect, 1, border_radius=4)
        pulse = 0.5 + 0.5 * math.sin((t_ms / 1000.0) * 4.0)
        dot_col = _brighten(help_accent, int(35 * pulse))
        dot = pygame.Surface((8, 8), pygame.SRCALPHA)
        pygame.draw.circle(dot, (*dot_col, int(115 + 75 * pulse)), (4, 4), 3)
        screen.blit(dot, (help_rect.x + 8, help_rect.y + 7))
        help_surf = _fit_text(smallfont, help_tip, GUI_TEXT_DIM, help_rect.width - 28)
        screen.blit(help_surf, (help_rect.x + 22, help_rect.y + (help_rect.height - help_surf.get_height()) // 2))

    return hb_btn_rect, ps_btn_rect, as_btn_rect, hud_btn_rect, megacrash_btn_rect, memdump_btn_rect, win_counter_btn_rect, overseer_btn_rect, select_probe_btn_rect, hb_filter_rects


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


def draw_status_rail(
    screen: pygame.Surface,
    smallfont: pygame.font.Font,
    *,
    text: str,
) -> None:
    if not text:
        return

    w, h = screen.get_size()
    rail_h = 22
    rect = pygame.Rect(0, h - rail_h, w, rail_h)

    _draw_vertical_gradient(
        screen,
        rect,
        (18, 20, 28),
        (12, 13, 18),
        245,
    )

    pygame.draw.line(screen, (58, 64, 82), (0, rect.y), (w, rect.y))
    label = _fit_text(smallfont, text, GUI_TEXT_MUTED, w - 18)
    screen.blit(label, (8, rect.y + (rail_h - label.get_height()) // 2))




def draw_bottom_workspace_tabs(
    screen: pygame.Surface,
    rect: pygame.Rect,
    smallfont: pygame.font.Font,
    active_tab: str,
    mouse_pos: tuple[int, int],
) -> tuple[pygame.Rect, dict[str, pygame.Rect]]:
    """Draw the lower inspector as a tabbed workspace.

    This keeps the tabbed layout, but upgrades the presentation: active tabs
    feel raised, get a clearer accent underline, and visually match the more
    polished card language used elsewhere in the GUI.
    """
    mx, my = mouse_pos
    tab_h = 24
    pad = 4

    if rect.width <= 0 or rect.height <= tab_h + 8:
        return rect, {}

    panel = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
    _draw_vertical_gradient(
        panel,
        panel.get_rect(),
        (14, 16, 23),
        (10, 11, 16),
        245,
    )
    pygame.draw.rect(panel, (52, 58, 76), panel.get_rect(), 1, border_radius=4)
    screen.blit(panel, rect.topleft)

    tabs = [
        ("scan", "Normals Preview", GUI_APP_ACCENT),
        ("events", "Events", GUI_APP_ACCENT),
        ("debug", "Debug Flags", GUI_APP_ACCENT),
        ("activity", "Activity", GUI_APP_ACCENT),
    ]

    tab_rects: dict[str, pygame.Rect] = {}
    x = rect.x + pad
    y = rect.y + pad
    gap = 6

    for key, label, accent in tabs:
        width = max(96, min(160, smallfont.size(label)[0] + 26))
        tr = pygame.Rect(x, y, width, tab_h)
        tab_rects[key] = tr
        is_active = key == active_tab
        is_hover = tr.collidepoint(mx, my)

        # Active tabs feel slightly raised and have a stronger accent cue.
        fill = (31, 38, 56) if is_active else (21, 24, 34)
        draw_glass_button(
            screen,
            tr,
            label,
            smallfont,
            active=is_active,
            hover=is_hover,
            accent=accent,
            fill=fill,
            align="center",
        )

        if is_active:
            top_rail = pygame.Rect(tr.x + 5, tr.y + 2, tr.width - 10, 2)
            pygame.draw.rect(screen, (*accent, 220), top_rail, border_radius=1)
            glow = pygame.Surface((tr.width - 8, 8), pygame.SRCALPHA)
            pygame.draw.rect(glow, (*accent, 34), glow.get_rect(), border_radius=4)
            screen.blit(glow, (tr.x + 4, tr.bottom - 4))

        x += width + gap

    content = pygame.Rect(
        rect.x + pad,
        rect.y + tab_h + pad + 5,
        rect.width - pad * 2,
        rect.height - tab_h - pad * 2 - 5,
    )
    if content.height < 16:
        content.height = 16

    pygame.draw.rect(screen, (18, 20, 28), content, border_radius=4)
    pygame.draw.rect(screen, (45, 52, 72), content, 1, border_radius=4)

    return content, tab_rects



def _normal_button_accent(label: str) -> tuple[int, int, int]:
    text = str(label or "").upper()
    if "A" in text or text.endswith("L"):
        return (115, 155, 235)
    if "B" in text or text.endswith("M"):
        return (220, 195, 105)
    if "C" in text or text.endswith("H"):
        return (105, 215, 155)
    return GUI_ACCENT_BLUE


def _normal_id_to_label(value, *, allow_low: bool = True) -> str | None:
    """Resolve a normal label from the scanner's authoritative ANIM_MAP.

    The old preview table was shifted for crouching normals and also invented
    j.2B/j.2C rows from raw ids. Keep this helper tied to scan_normals_all so
    the preview cannot drift away from the actual scanner again.
    """
    try:
        raw = int(value)
    except Exception:
        return None

    if raw < 0:
        return None

    fallback_map = {
        0x00: "5A",
        0x01: "5B",
        0x02: "5C",
        0x03: "2A",
        0x04: "2B",
        0x05: "2C",
        0x06: "6C",
        0x08: "3C",
        0x09: "j.A",
        0x0A: "j.B",
        0x0B: "j.C",
        0x0E: "6B",
    }

    scan_map = SCAN_ANIM_MAP if isinstance(SCAN_ANIM_MAP, dict) and SCAN_ANIM_MAP else fallback_map
    low = raw & 0xFF

    if raw >= 0x100 and low in scan_map:
        return str(scan_map[low])

    if allow_low and raw in scan_map:
        return str(scan_map[raw])

    return None


def _normal_move_label(mv: dict) -> str:
    if not isinstance(mv, dict):
        return "?"

    forced = mv.get("_normal_display_label")
    if forced:
        return str(forced)

    # Prefer actual scanner/editor labels first. move_name is what
    # scan_normals_all attaches; the older preview code accidentally ignored it.
    for key in ("label", "move_name", "move", "pretty_name", "name"):
        value = mv.get(key)
        if value:
            return str(value)

    label = _normal_id_to_label(mv.get("id"), allow_low=True)
    if label:
        return label

    label = _normal_id_to_label(mv.get("table_index"), allow_low=False)
    if label:
        return label

    return "?"


def _normal_canon_label(label: str) -> str:
    """Canonicalize display labels for preview-row highlighting.

    The UI should prefer the live move label first, because multiple rows can
    sometimes share a raw animation ID or carry overlapping fallback IDs. Using
    the canonical display label avoids accidental double-highlighting.
    """
    text = str(label or "").strip().lower()
    if not text:
        return ""
    text = text.replace(" ", "")
    text = text.replace("jump.", "j.")
    text = text.replace("jump", "j")
    text = text.replace("crouching", "2")
    text = text.replace("crouch", "2")
    text = text.replace("standing", "5")
    text = text.replace("stand", "5")
    text = text.replace("close", "")
    text = text.replace("far", "")
    return text


def _normal_int(mv: dict, *keys: str) -> int | None:
    for key in keys:
        value = mv.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except Exception:
            continue
    return None



_NORMAL_PREVIEW_ORDER = (
    "5A", "2A",
    "5B", "2B",
    "6B",
    "5C", "2C",
    "4C", "6C", "3C",
    "j.A", "j.B", "j.C",
)
_NORMAL_PREVIEW_RANK = {name.lower(): i for i, name in enumerate(_NORMAL_PREVIEW_ORDER)}

# These labels are optional/character-specific. Do not let a raw fallback id
# manufacture them for everyone. j.2B/j.2C are intentionally not in the preview
# order until they are promoted by a real character-specific scanner label.
_OPTIONAL_PREVIEW_NORMALS = {"6B"}
_HIDDEN_PREVIEW_NORMALS = {"j.2B", "j.2C"}


def _normal_canonical_label(label: str) -> str | None:
    text = str(label or "").strip()
    if not text or text == "?":
        return None

    low = text.lower()
    low = low.replace(" ", "")
    low = low.replace("_", "")
    low = low.replace("jump.", "j.")
    low = low.replace("jump", "j.")
    low = low.replace("air.", "j.")
    low = low.replace("air", "j.")
    low = low.replace("stand", "5")
    low = low.replace("standing", "5")
    low = low.replace("crouch", "2")
    low = low.replace("crouching", "2")

    aliases = {
        "a": "5A", "5a": "5A",
        "2a": "2A",
        "b": "5B", "5b": "5B",
        "2b": "2B",
        "6b": "6B",
        "c": "5C", "5c": "5C",
        "2c": "2C",
        "4c": "4C",
        "6c": "6C",
        "3c": "3C",
        "j.a": "j.A", "ja": "j.A", "jA".lower(): "j.A",
        "j.b": "j.B", "jb": "j.B", "jB".lower(): "j.B",
        "j.c": "j.C", "jc": "j.C", "jC".lower(): "j.C",
    }

    return aliases.get(low)


def _normal_preview_label_allowed(mv: dict, canon: str, raw_label: str) -> bool:
    """Gate optional labels that raw ids can falsely create.

    Core normals are allowed from the scanner map. 6B is allowed only when the
    row was produced by an explicit/character-specific label source. This keeps
    random 0x010E system/script records from appearing as 6B for every cast
    member. j.2B/j.2C stay hidden from the compact preview for now.
    """
    if canon in _HIDDEN_PREVIEW_NORMALS:
        return False

    if canon not in _OPTIONAL_PREVIEW_NORMALS:
        return True

    if not isinstance(mv, dict):
        return False

    if bool(mv.get("normal_confirmed")):
        return True

    source = str(mv.get("move_name_source") or mv.get("label_source") or "").strip().lower()
    if source in {"lookup", "char_map", "character", "csv", "explicit"}:
        return True

    # If another module supplied an actual display label, trust that over the
    # raw id fallback. Do not count move_name here because older scanner builds
    # filled move_name from ANIM_MAP fallback.
    for key in ("label", "move", "pretty_name"):
        explicit = mv.get(key)
        if explicit and _normal_canonical_label(str(explicit)) == canon:
            return True

    return False


def _normal_row_quality(mv: dict) -> tuple[int, int, int, int]:
    """Prefer rows that actually have useful frame values if duplicates exist."""
    if not isinstance(mv, dict):
        return (0, 0, 0, 0)
    startup = _normal_int(mv, "startup", "start", "active_start")
    a1 = _normal_int(mv, "active_start", "a_start")
    a2 = _normal_int(mv, "active_end", "a_end")
    hit = _normal_int(mv, "hitstun", "hit", "h")
    block = _normal_int(mv, "blockstun", "block", "b")
    filled = sum(v is not None for v in (startup, a1, a2, hit, block))
    active_span = 0 if a1 is None or a2 is None else max(0, a2 - a1)
    damage = _normal_int(mv, "damage", "dmg") or 0
    return (filled, active_span, damage, -int(mv.get("_scan_index", 0) or 0))


def _normal_visible_moves(moves: list) -> list:
    """Return only the curated normal rows, in fighting-game notation order.

    The scan can contain duplicate/system/debug rows, and some characters put
    command normals before or after jump normals. The preview should not depend
    on raw scan order. It shows the useful set only:
      5A, 2A, 5B, 2B, optional confirmed 6B, 5C, 2C, optional 4C/6C/3C, j.A, j.B, j.C
    """
    if not isinstance(moves, list):
        return []

    best_by_label: dict[str, dict] = {}

    for scan_i, mv in enumerate(moves):
        if not isinstance(mv, dict):
            continue

        label = _normal_move_label(mv)
        canon = _normal_canonical_label(label)
        if canon is None:
            continue
        if not _normal_preview_label_allowed(mv, canon, label):
            continue

        row = dict(mv)
        row["_normal_display_label"] = canon
        row.setdefault("_scan_index", scan_i)

        old = best_by_label.get(canon)
        if old is None or _normal_row_quality(row) > _normal_row_quality(old):
            best_by_label[canon] = row

    out: list[dict] = []
    for label in _NORMAL_PREVIEW_ORDER:
        row = best_by_label.get(label)
        if row is not None:
            out.append(row)
    return out


def _draw_scan_metric_chip(
    surf: pygame.Surface,
    rect: pygame.Rect,
    smallfont: pygame.font.Font,
    label: str,
    value: str,
    accent: tuple[int, int, int],
) -> None:
    """Draw a cleaner metric cell for the normals preview.

    The earlier chip style worked, but looked a bit busy once multiplied across
    four cards. This version keeps a subtle boxed cell, a calm neutral fill,
    and a tiny accent rail on the left so the preview feels more like a polished
    data table and less like a wall of little buttons.
    """
    _draw_vertical_gradient(
        surf,
        rect,
        (22, 25, 35),
        (15, 18, 26),
        236,
    )
    pygame.draw.rect(surf, (44, 51, 71), rect, 1, border_radius=4)

    rail = pygame.Rect(rect.x + 1, rect.y + 1, 2, max(1, rect.height - 2))
    pygame.draw.rect(surf, _darken(accent, 12), rail, border_radius=1)

    label_s = smallfont.render(label, True, GUI_TEXT_DIM)
    value_s = _render_outlined_text(
        smallfont,
        value,
        GUI_TEXT,
        (0, 0, 0),
        rect.width - label_s.get_width() - 10,
        outline_px=1,
    )

    x = rect.x + 6
    surf.blit(label_s, (x, rect.y + (rect.height - label_s.get_height()) // 2))
    surf.blit(
        value_s,
        (
            rect.right - value_s.get_width() - 5,
            rect.y + (rect.height - value_s.get_height()) // 2,
        ),
    )


def draw_scan_normals_polished(
    surf: pygame.Surface,
    rect: pygame.Rect,
    font: pygame.font.Font,
    smallfont: pygame.font.Font,
    scan_data,
    *,
    t_ms: int = 0,
    scan_fx_by_slot: dict | None = None,
) -> None:
    """Draw the normals preview as polished grid cards with lightweight live FX."""
    if rect.width <= 0 or rect.height <= 0:
        return

    scan_fx_by_slot = scan_fx_by_slot or {}

    panel = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
    _draw_vertical_gradient(panel, panel.get_rect(), (14, 17, 26), (10, 12, 18), 255)
    surf.blit(panel, rect.topleft)

    title = smallfont.render("Scan: Normals Preview", True, GUI_TEXT)
    legend = smallfont.render("S startup | A active | H hitstun | B blockstun | blue = patched", True, GUI_TEXT_DIM)
    surf.blit(title, (rect.x + 10, rect.y + 8))
    surf.blit(legend, (rect.right - legend.get_width() - 10, rect.y + 8))
    pygame.draw.line(surf, (52, 61, 82), (rect.x + 8, rect.y + 28), (rect.right - 8, rect.y + 28))

    try:
        slots = list(scan_data or [])
    except Exception:
        slots = []

    ordered_labels = ["P1-C1", "P1-C2", "P2-C1", "P2-C2"]
    slot_map = {}
    for _s in [s for s in slots if isinstance(s, dict)]:
        _lbl = str(_s.get("slot_label") or _s.get("slot") or "")
        if _lbl and _lbl not in slot_map:
            slot_map[_lbl] = _s
    slots = [slot_map.get(lbl, {"slot_label": lbl, "char_name": "No character", "moves": []}) for lbl in ordered_labels]

    pad, gap = 8, 10
    top = rect.y + 36
    card_h = max(44, rect.height - 44)
    count = 4
    card_w = max(140, (rect.width - pad * 2 - gap * (count - 1)) // count)
    dense = rect.height < 260 or rect.width < 930
    header_h = 24 if not dense else 22
    table_header_h = 16 if not dense else 14

    def _section_for_label(label: str) -> str:
        low = str(label or "").lower()
        if low.startswith("j.") or low.startswith("j"):
            return "Jump"
        if low.startswith("2"):
            return "Crouch"
        if low.startswith(("3", "4", "6")):
            return "Command"
        return "Stand"

    for si, slot in enumerate(slots):
        card_x = rect.x + pad + si * (card_w + gap)
        card = pygame.Rect(card_x, top, card_w, card_h)
        slot_label = str(slot.get("slot_label") or slot.get("slot") or f"S{si + 1}")
        slot_fx = scan_fx_by_slot.get(slot_label, {}) if isinstance(scan_fx_by_slot, dict) else {}

        card_fill = pygame.Surface((card.width, card.height), pygame.SRCALPHA)
        _draw_vertical_gradient(card_fill, card_fill.get_rect(), (18, 22, 32), (12, 14, 21), 238)
        surf.blit(card_fill, card.topleft)

        char_name = str(slot.get("char_name") or slot.get("character") or slot.get("name") or "No character")
        accent = _slot_accent_for_label(slot_label, muted=True)
        pygame.draw.rect(surf, (43, 52, 72), card, 1, border_radius=6)
        pygame.draw.rect(surf, accent, pygame.Rect(card.x, card.y, 3, card.height), border_radius=2)

        header_rect = pygame.Rect(card.x + 1, card.y + 1, card.width - 2, header_h)
        _draw_vertical_gradient(surf, header_rect, (25, 30, 43), (17, 20, 30), 236)
        pygame.draw.rect(surf, (180, 205, 245, 16), pygame.Rect(header_rect.x + 4, header_rect.y + 2, header_rect.width - 8, max(2, header_rect.height // 5)), border_radius=3)
        pygame.draw.line(surf, (44, 52, 72), (header_rect.x + 6, header_rect.bottom), (header_rect.right - 6, header_rect.bottom))
        slot_s = _render_outlined_text(font, slot_label, accent, (0, 0, 0), 76, outline_px=1)
        surf.blit(slot_s, (card.x + 9, card.y + 4))
        name_s = _fit_text(smallfont, char_name, GUI_TEXT_MUTED, card.width - 90)
        surf.blit(name_s, (card.x + 72, card.y + 6))

        moves = slot.get("moves") or []
        if not isinstance(moves, list):
            moves = []
        visible_moves = _normal_visible_moves(moves)
        is_empty_card = len(visible_moves) <= 0

        cur_id = slot.get("cur_anim") or slot.get("current_anim") or slot.get("mv_id_display") or slot.get("move_id")
        try:
            cur_id = int(cur_id) if cur_id is not None else None
        except Exception:
            cur_id = None
        cur_label = str(slot.get("cur_label") or slot.get("current_move") or slot.get("mv_label") or "").strip().lower()

        table_x = card.x + 6
        table_y = card.y + header_h + 4
        table_w = card.width - 12
        table_h = card.height - header_h - 8
        move_col_w = 54 if card.width >= 220 else 48
        metric_col_w = max(24, (table_w - move_col_w) // 4)
        grid_x, grid_y = table_x, table_y
        grid_w, grid_h = move_col_w + metric_col_w * 4, table_h

        table_bg = pygame.Rect(grid_x, grid_y, grid_w, grid_h)
        pygame.draw.rect(surf, (13, 16, 24), table_bg, border_radius=4)
        pygame.draw.rect(surf, (34, 42, 58), table_bg, 1, border_radius=4)

        for i in range(4):
            col_left = grid_x + move_col_w + i * metric_col_w
            band = pygame.Surface((metric_col_w, grid_h - 2), pygame.SRCALPHA)
            band.fill((18, 22, 31, 80) if i % 2 == 0 else (15, 18, 27, 55))
            surf.blit(band, (col_left, grid_y + 1))

        hdr = pygame.Rect(grid_x, grid_y, grid_w, table_header_h)
        pygame.draw.rect(surf, (18, 22, 31), hdr, border_radius=4)
        pygame.draw.line(surf, (48, 56, 76), (hdr.x + 1, hdr.bottom), (hdr.right - 1, hdr.bottom))
        for i, txt in enumerate(("S", "A", "H", "B")):
            col_left = grid_x + move_col_w + i * metric_col_w
            hdr_s = smallfont.render(txt, True, GUI_TEXT_DIM)
            surf.blit(hdr_s, (col_left + (metric_col_w - hdr_s.get_width()) // 2, hdr.y + (hdr.height - hdr_s.get_height()) // 2))

        if is_empty_card:
            empty_body = pygame.Rect(grid_x + 1, grid_y + table_header_h + 1, grid_w - 2, grid_h - table_header_h - 2)
            pygame.draw.rect(surf, (11, 14, 21), empty_body, border_radius=4)
            had_scan_entry = bool(slot.get("_had_scan_entry"))
            if char_name == "No character":
                empty_msg = "No character loaded"
                sub_msg = "This slot is currently empty"
            elif bool(slot.get("profile_cache_miss")):
                empty_msg = "Building profile"
                sub_msg = "No cached normals yet; dynamic profile build is queued"
            elif had_scan_entry:
                empty_msg = "No normals returned"
                sub_msg = "The scan completed, but this slot returned no normal data"
            else:
                empty_msg = "Waiting for scan"
                sub_msg = "Normals will appear here when data is available"
            msg1 = _render_outlined_text(font, empty_msg, GUI_TEXT_DIM, (0, 0, 0), empty_body.width - 16, 1)
            msg2 = _fit_text(smallfont, sub_msg, GUI_TEXT_DIM, empty_body.width - 16)
            surf.blit(msg1, (empty_body.x + (empty_body.width - msg1.get_width()) // 2, empty_body.y + max(10, empty_body.height // 2 - 14)))
            surf.blit(msg2, (empty_body.x + (empty_body.width - msg2.get_width()) // 2, empty_body.y + max(28, empty_body.height // 2 + 4)))
            continue

        row_count = max(1, len(visible_moves))
        available_h = max(1, grid_h - table_header_h)
        row_h = max(13, min(18, available_h // row_count))

        for vx in (grid_x + move_col_w, grid_x + move_col_w + metric_col_w, grid_x + move_col_w + metric_col_w * 2, grid_x + move_col_w + metric_col_w * 3, grid_x + move_col_w + metric_col_w * 4):
            pygame.draw.line(surf, (38, 46, 64), (vx, grid_y + 1), (vx, grid_y + grid_h - 2))

        y = grid_y + table_header_h
        last_section = None
        sweep_frac = float(slot_fx.get("row_sweep", 0.0) or 0.0)
        for mi, mv in enumerate(visible_moves):
            if not isinstance(mv, dict):
                continue
            label = _normal_move_label(mv)
            section = _section_for_label(label)
            if last_section is not None and section != last_section:
                pygame.draw.line(surf, (66, 75, 98), (grid_x + 2, y), (grid_x + grid_w - 3, y))
                pygame.draw.line(surf, (*accent, 45), (grid_x + 2, y + 1), (grid_x + 30, y + 1))
            last_section = section

            row = pygame.Rect(grid_x, y, grid_w, row_h)
            row_fill = (16, 19, 28) if mi % 2 == 0 else (13, 16, 24)
            mv_id = mv.get("id") or mv.get("anim") or mv.get("move_id")
            try:
                mv_id = int(mv_id) if mv_id is not None else None
            except Exception:
                mv_id = None
            row_label_canon = _normal_canon_label(label)
            cur_label_canon = _normal_canon_label(cur_label)
            if cur_label_canon:
                is_current = (row_label_canon == cur_label_canon)
            else:
                is_current = (cur_id is not None and mv_id == cur_id)
            if is_current:
                glow = pygame.Surface((row.width, row.height), pygame.SRCALPHA)
                glow.fill((*accent, 48))
                surf.blit(glow, row.topleft)
                pygame.draw.rect(surf, (*accent, 130), row, 1)
                pygame.draw.line(surf, (*accent, 95), (row.x + 1, row.bottom - 1), (row.right - 1, row.bottom - 1))
                if sweep_frac > 0.0:
                    sweep_x = row.x - 20 + int((row.width + 40) * sweep_frac)
                    sweep = pygame.Surface((24, row.height + 6), pygame.SRCALPHA)
                    pygame.draw.rect(sweep, (*_brighten(accent, 28), 70), pygame.Rect(0, 0, 10, row.height + 6), border_radius=4)
                    pygame.draw.rect(sweep, (*_brighten(accent, 48), 28), pygame.Rect(8, 0, 16, row.height + 6), border_radius=4)
                    surf.blit(sweep, (sweep_x, row.y - 3), special_flags=pygame.BLEND_ALPHA_SDL2 if hasattr(pygame, 'BLEND_ALPHA_SDL2') else 0)
            else:
                pygame.draw.rect(surf, row_fill, row)
                pygame.draw.rect(surf, (34, 41, 58), row, 1)
            pygame.draw.line(surf, (28, 34, 48), (row.x + 1, row.bottom), (row.right - 1, row.bottom))

            label_col = GUI_TEXT if is_current else (218, 224, 234)
            label_s = _render_outlined_text(smallfont, label, label_col, (0, 0, 0), move_col_w - 8, outline_px=1)
            surf.blit(label_s, (row.x + 6, row.y + (row.height - label_s.get_height()) // 2))

            startup = _normal_int(mv, "startup", "start", "active_start")
            a1 = _normal_int(mv, "active_start", "a_start")
            a2 = _normal_int(mv, "active_end", "a_end")
            hit = _normal_int(mv, "hitstun", "hit", "h")
            block = _normal_int(mv, "blockstun", "block", "b")
            active_txt = "-"
            hit_segments = mv.get("hit_segments") or []
            if isinstance(hit_segments, list) and hit_segments:
                first_seg = hit_segments[0] if isinstance(hit_segments[0], dict) else {}
                startup = _normal_int(first_seg, "startup", "start", "active_start") or startup
                hit = _normal_int(first_seg, "hitstun", "hit", "h") if _normal_int(first_seg, "hitstun", "hit", "h") is not None else hit
                block = _normal_int(first_seg, "blockstun", "block", "b") if _normal_int(first_seg, "blockstun", "block", "b") is not None else block
            if isinstance(hit_segments, list) and len(hit_segments) > 1:
                parts = []
                for seg in hit_segments[:3]:
                    if not isinstance(seg, dict):
                        continue
                    s1 = _normal_int(seg, "active_start", "a_start")
                    s2 = _normal_int(seg, "active_end", "a_end")
                    if s1 is not None and s2 is not None:
                        parts.append(f"{s1}-{s2}")
                    elif s1 is not None:
                        parts.append(str(s1))
                if len(hit_segments) > 3:
                    parts.append(f"+{len(hit_segments) - 3}")
                active_txt = "/".join(parts) if parts else "-"
            elif a1 is not None and a2 is not None:
                active_txt = f"{a1}-{a2}"
            elif a1 is not None:
                active_txt = str(a1)
            values = ["-" if startup is None else str(startup), active_txt, "-" if hit is None else str(hit), "-" if block is None else str(block)]
            patch_fields = mv.get("_fd_patch_fields") or set()
            try:
                patch_fields = set(patch_fields)
            except Exception:
                patch_fields = set()
            if isinstance(hit_segments, list):
                for seg in hit_segments:
                    if not isinstance(seg, dict):
                        continue
                    try:
                        patch_fields.update(set(seg.get("_fd_patch_fields") or []))
                    except Exception:
                        pass
            metric_groups = ("active", "active", "hitstun", "blockstun")
            value_col = GUI_TEXT if is_current else (205, 211, 224)
            patched_col = _brighten(accent, 52) if is_current else (145, 194, 255)
            for i, value in enumerate(values):
                col_left = grid_x + move_col_w + i * metric_col_w
                is_patched_metric = metric_groups[i] in patch_fields
                if is_patched_metric:
                    chip_rect = pygame.Rect(col_left + 2, row.y + 2, metric_col_w - 4, max(1, row.height - 4))
                    chip = pygame.Surface((chip_rect.width, chip_rect.height), pygame.SRCALPHA)
                    pygame.draw.rect(chip, (*patched_col, 28), chip.get_rect(), border_radius=3)
                    pygame.draw.rect(chip, (*patched_col, 92), chip.get_rect(), 1, border_radius=3)
                    surf.blit(chip, chip_rect.topleft)
                draw_col = patched_col if is_patched_metric else value_col
                val_s = _render_outlined_text(smallfont, value, draw_col, (0, 0, 0), metric_col_w - 6, outline_px=1)
                surf.blit(val_s, (col_left + (metric_col_w - val_s.get_width()) // 2, row.y + (row.height - val_s.get_height()) // 2))
            y += row_h


_QUICK_ASSIST_STRENGTH_MARKS = ("α", "β", "γ")
# UMvC3-style assist strength colors: Alpha red, Beta green, Gamma blue.
_QUICK_ASSIST_STRENGTH_COLORS = (
    (236, 70, 82),
    (74, 214, 114),
    (82, 156, 255),
)


def _quick_assist_strength_meta(quick_index: int, is_default: bool = False) -> tuple[str, tuple[int, int, int]] | None:
    """Return the visual assist-strength marker for custom quick assists.

    This is intentionally display-only. The quick-assist JSON labels stay
    unchanged for lookup/write logic, while the first three non-default buttons
    get Marvel-style Alpha/Beta/Gamma markers.
    """
    if is_default:
        return None
    try:
        qi = int(quick_index)
    except Exception:
        return None
    if 0 <= qi < len(_QUICK_ASSIST_STRENGTH_MARKS):
        return _QUICK_ASSIST_STRENGTH_MARKS[qi], _QUICK_ASSIST_STRENGTH_COLORS[qi]
    return None


def _quick_assist_display_label(label: str, quick_index: int, is_default: bool = False) -> str:
    """Return the raw visible move label. Strength marks are drawn separately."""
    return str(label or "")


def _quick_assist_accent_for_label(
    label: str,
    is_default: bool = False,
    quick_index: int | None = None,
) -> tuple[int, int, int]:
    """Return the accent color used by quick-assist buttons."""
    if is_default:
        return GUI_TEXT_DIM
    meta = _quick_assist_strength_meta(quick_index, is_default) if quick_index is not None else None
    if meta:
        return meta[1]
    return GUI_CONFIRM


def _draw_quick_assist_button(
    surf: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    font: pygame.font.Font,
    *,
    active: bool = False,
    hover: bool = False,
    accent: tuple[int, int, int] = GUI_ACCENT_BLUE,
    fill: tuple[int, int, int] | None = None,
    mark_meta: tuple[str, tuple[int, int, int]] | None = None,
) -> None:
    """Draw a quick-assist button with a colored Alpha/Beta/Gamma marker lane."""
    draw_glass_button(
        surf,
        rect,
        "",
        font,
        active=active,
        hover=hover,
        accent=accent,
        fill=fill,
        align="center",
    )

    draw_rect = rect.move(0, -1 if hover else 0)
    text_col = GUI_TEXT if active or hover else GUI_TEXT_MUTED

    if mark_meta:
        mark, mark_col = mark_meta
        mark_lane_w = max(20, min(26, rect.width // 4))
        divider_x = draw_rect.x + mark_lane_w

        mark_surf = _render_outlined_text(
            font,
            mark,
            mark_col,
            (0, 0, 0),
            max(8, mark_lane_w - 5),
            outline_px=1,
        )
        surf.blit(
            mark_surf,
            (
                draw_rect.x + (mark_lane_w - mark_surf.get_width()) // 2,
                draw_rect.y + (draw_rect.height - mark_surf.get_height()) // 2,
            ),
        )

        divider_top = draw_rect.y + 4
        divider_bottom = draw_rect.bottom - 4
        pygame.draw.line(
            surf,
            _darken(mark_col, 46),
            (divider_x, divider_top),
            (divider_x, divider_bottom),
            1,
        )
        pygame.draw.line(
            surf,
            _brighten(mark_col, 20),
            (divider_x + 1, divider_top),
            (divider_x + 1, divider_bottom),
            1,
        )

        text_x = divider_x + 6
        text_w = max(8, draw_rect.right - text_x - 6)
        label_surf = _render_outlined_text(
            font,
            label,
            text_col,
            (0, 0, 0),
            text_w,
            outline_px=1,
        )
        tx = text_x + (text_w - label_surf.get_width()) // 2
    else:
        text_w = max(8, draw_rect.width - 12)
        label_surf = _render_outlined_text(
            font,
            label,
            text_col,
            (0, 0, 0),
            text_w,
            outline_px=1,
        )
        tx = draw_rect.x + (draw_rect.width - label_surf.get_width()) // 2

    ty = draw_rect.y + (draw_rect.height - label_surf.get_height()) // 2
    surf.blit(label_surf, (tx, ty))


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return 1.0 - ((1.0 - t) * (1.0 - t) * (1.0 - t))


def _ease_in_out_smootherstep(t: float) -> float:
    """Smooth 0..1 easing with gentle start and finish.

    This reads better for short UI travel than a pure ease-out curve because
    the selector does not launch at full speed on the first visible frame.
    """
    t = max(0.0, min(1.0, float(t)))
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)

def _apply_panel_element_enter_animation(
    panel_surf: pygame.Surface,
    panel_fx: dict | None,
    now: float,
) -> pygame.Surface:
    """Cascade fighter-card contents in after the card itself starts entering.

    This is intentionally cheap: it slices the already-rendered card into a few
    horizontal content bands and gives each band a tiny delayed fade/slide.  The
    scanner profile cache buys us enough room for this polish without creating
    new per-widget draw state or expensive per-pixel work every frame.
    """
    if not isinstance(panel_fx, dict):
        return panel_surf
    entry = panel_fx.get("panel_enter")
    if not isinstance(entry, dict):
        return panel_surf

    try:
        start = float(entry.get("start", 0.0) or 0.0)
        dur = max(0.001, float(entry.get("dur", 0.68) or 0.68))
    except Exception:
        return panel_surf
    if start <= 0.0:
        return panel_surf

    raw = (float(now) - start) / dur
    if raw <= 0.0:
        raw = 0.0
    if raw >= 1.0:
        return panel_surf

    w, h = panel_surf.get_size()
    if w <= 0 or h <= 0:
        return panel_surf

    out = pygame.Surface((w, h), pygame.SRCALPHA)

    # Leave a low-alpha ghost of the full card so the panel never looks empty
    # while the content bands are staggering in.
    ghost = panel_surf.copy()
    ghost.set_alpha(max(20, min(110, int(28 + 72 * _ease_out_cubic(raw)))))
    out.blit(ghost, (0, 0))

    bands = [
        (0, int(h * 0.35), 0.00, -10, 5),       # portrait/header/HP
        (int(h * 0.27), int(h * 0.56), 0.08, -8, 4),   # pool/baroque
        (int(h * 0.48), int(h * 0.74), 0.16, -6, 3),   # move/status
        (int(h * 0.66), h, 0.24, 0, 5),         # buttons/quick assists
    ]

    for y0, y1, delay, dx, dy in bands:
        y0 = max(0, min(h, int(y0)))
        y1 = max(y0, min(h, int(y1)))
        if y1 <= y0:
            continue
        denom = max(0.001, 1.0 - delay)
        local = max(0.0, min(1.0, (raw - delay) / denom))
        eased = _ease_out_cubic(local)
        if eased <= 0.0:
            continue
        piece = panel_surf.subsurface(pygame.Rect(0, y0, w, y1 - y0)).copy()
        piece.set_alpha(max(0, min(255, int(255 * eased))))
        out.blit(piece, (int((1.0 - eased) * dx), y0 + int((1.0 - eased) * dy)))

    return out

def draw_quick_assist_footer(
    surf: pygame.Surface,
    panel_rect: pygame.Rect,
    slot_label: str,
    snap: dict | None,
    smallfont: pygame.font.Font,
    *,
    mx_local: int,
    my_local: int,
    btn_y: int,
    get_quick_defs_fn,
    active_quick_index: int | None = None,
    flash_quick_index: int | None = None,
    slide_anim: dict | None = None,
) -> dict:
    """Draw a compact one-line quick-assist strip.

    The first polish pass used a two-line footer with a visible header plus
    buttons. It looked nice, but it stole too much vertical room from the
    character panels. This version keeps the same click behavior and assist
    logic, but compresses the UI into one clean row:

        Assist | move | move | move | default
    """
    quick_defs = []

    if snap:
        try:
            quick_defs = get_quick_defs_fn(slot_label, snap)[:4]
        except Exception:
            quick_defs = []

    if not quick_defs and snap:
        quick_defs = [
            {"label": "304", "table": 304},
            {"label": "305", "table": 305},
            {"label": "306", "table": 306},
            {"label": "Default", "default": True},
        ]

    if not quick_defs:
        return {}

    qa_count = min(4, len(quick_defs))
    qa_gap = 6
    qa_h = 20
    label_w = 64
    side_pad = 10

    qa_y = max(72, btn_y - qa_h - 10)
    strip_y = max(0, qa_y - 5)
    strip_h = min(panel_rect.height - strip_y - 4, qa_h + 10)
    strip_rect = pygame.Rect(6, strip_y, panel_rect.width - 12, strip_h)

    _draw_vertical_gradient(
        surf,
        strip_rect,
        (22, 25, 35),
        (15, 17, 24),
        230,
    )
    pygame.draw.rect(surf, (54, 62, 82), strip_rect, 1, border_radius=5)

    label_surf = smallfont.render("Assist", True, GUI_TEXT_DIM)
    surf.blit(
        label_surf,
        (
            strip_rect.x + 8,
            qa_y + (qa_h - label_surf.get_height()) // 2,
        ),
    )

    qa_x0 = strip_rect.x + label_w
    qa_total_w = strip_rect.width - label_w - side_pad
    qa_w = max(48, int((qa_total_w - qa_gap * (qa_count - 1)) / qa_count))

    out = {}

    # Precompute button geometry so the selected marker can slide from the old
    # quick assist to the new quick assist without keeping the old one lit.
    button_rows = []
    for qi, quick in enumerate(quick_defs):
        qx = qa_x0 + qi * (qa_w + qa_gap)
        qrect_local = pygame.Rect(qx, qa_y, qa_w, qa_h)
        raw_qlabel = str(quick.get("label", f"A{qi + 1}"))
        is_default_quick = bool(quick.get("default", False))
        qlabel = _quick_assist_display_label(raw_qlabel, qi, is_default_quick)
        mark_meta = _quick_assist_strength_meta(qi, is_default_quick)
        accent = _quick_assist_accent_for_label(raw_qlabel, is_default_quick, qi)
        button_rows.append((qi, quick, qrect_local, qlabel, accent, mark_meta))

    # Sliding selection plate. Use a time-based smootherstep motion, a longer
    # duration, and no immediate selected-button fill during travel. That keeps
    # the change readable at 60 FPS instead of feeling like a jump plus a small
    # underline animation.
    selected_rect = None
    selected_accent = GUI_ACCENT_BLUE
    slide_is_active = False
    slide_frac = 1.0

    if active_quick_index is not None:
        for qi, _quick, qrect_local, _qlabel, accent, _mark_meta in button_rows:
            if qi == int(active_quick_index):
                selected_rect = qrect_local
                selected_accent = accent
                break

    if selected_rect is not None:
        marker_rect = selected_rect.copy()
        src_rect = None
        dst_rect = selected_rect.copy()

        if isinstance(slide_anim, dict):
            try:
                src_i = int(slide_anim.get("from", active_quick_index))
                dst_i = int(slide_anim.get("to", active_quick_index))
                start_ts = float(slide_anim.get("start", 0.0) or 0.0)
                dur = max(0.001, float(slide_anim.get("dur", 0.38) or 0.38))

                if dst_i == int(active_quick_index) and start_ts > 0.0:
                    for qi, _quick, qrect_local, _qlabel, _accent, _mark_meta in button_rows:
                        if qi == src_i:
                            src_rect = qrect_local
                        if qi == dst_i:
                            dst_rect = qrect_local

                    if src_rect is not None and dst_rect is not None:
                        raw_frac = max(0.0, min(1.0, (time.time() - start_ts) / dur))
                        slide_frac = _ease_in_out_smootherstep(raw_frac)
                        slide_is_active = raw_frac < 0.995 and src_i != dst_i

                        marker_rect = pygame.Rect(
                            round(src_rect.x + (dst_rect.x - src_rect.x) * slide_frac),
                            round(src_rect.y + (dst_rect.y - src_rect.y) * slide_frac),
                            round(src_rect.width + (dst_rect.width - src_rect.width) * slide_frac),
                            round(src_rect.height + (dst_rect.height - src_rect.height) * slide_frac),
                        )
            except Exception:
                marker_rect = selected_rect.copy()
                slide_is_active = False

        # Motion trail. This is subtle, but it gives the selector a continuous
        # path across the buttons instead of only a single hard-edged rectangle.
        if slide_is_active and src_rect is not None and dst_rect is not None:
            for back_i, alpha_mul in ((2, 0.22), (1, 0.38)):
                lag = max(0.0, slide_frac - 0.08 * back_i)
                trail_rect = pygame.Rect(
                    round(src_rect.x + (dst_rect.x - src_rect.x) * lag),
                    round(src_rect.y + (dst_rect.y - src_rect.y) * lag),
                    round(src_rect.width + (dst_rect.width - src_rect.width) * lag),
                    round(src_rect.height + (dst_rect.height - src_rect.height) * lag),
                )
                trail = pygame.Surface((trail_rect.width + 14, trail_rect.height + 14), pygame.SRCALPHA)
                pygame.draw.rect(
                    trail,
                    (*selected_accent, int(46 * alpha_mul)),
                    pygame.Rect(7, 7, trail_rect.width, trail_rect.height),
                    border_radius=8,
                )
                surf.blit(trail, (trail_rect.x - 7, trail_rect.y - 7))

        # Main selector plate. Keep it pronounced, but with a smoother glow and
        # a softer top sheen so the movement reads cleanly.
        glow = pygame.Surface((marker_rect.width + 20, marker_rect.height + 20), pygame.SRCALPHA)
        pygame.draw.rect(
            glow,
            (*selected_accent, 68),
            pygame.Rect(10, 10, marker_rect.width, marker_rect.height),
            border_radius=8,
        )
        pygame.draw.rect(
            glow,
            (*selected_accent, 28),
            pygame.Rect(4, 4, marker_rect.width + 12, marker_rect.height + 12),
            2,
            border_radius=10,
        )
        surf.blit(glow, (marker_rect.x - 10, marker_rect.y - 10))

        plate = pygame.Surface((marker_rect.width + 4, marker_rect.height + 4), pygame.SRCALPHA)
        plate_rect = plate.get_rect()
        pygame.draw.rect(
            plate,
            (*selected_accent, 46),
            plate_rect,
            border_radius=6,
        )
        pygame.draw.rect(
            plate,
            (150, 165, 190, 18),
            pygame.Rect(2, 2, plate_rect.width - 4, max(2, plate_rect.height // 5)),
            border_radius=5,
        )
        pygame.draw.rect(
            plate,
            (*selected_accent, 165),
            plate_rect,
            2,
            border_radius=6,
        )
        surf.blit(plate, (marker_rect.x - 2, marker_rect.y - 2))

        rail_h = 4
        rail_rect = pygame.Rect(
            marker_rect.x + 5,
            marker_rect.bottom - rail_h - 1,
            max(4, marker_rect.width - 10),
            rail_h,
        )
        pygame.draw.rect(surf, selected_accent, rail_rect, border_radius=2)

        # Tiny settle pulse after the slide lands.
        if isinstance(slide_anim, dict):
            try:
                start_ts = float(slide_anim.get("start", 0.0) or 0.0)
                dur = max(0.001, float(slide_anim.get("dur", 0.38) or 0.38))
                raw = (time.time() - start_ts) / dur if start_ts else 99.0
                if 1.0 <= raw <= 1.32:
                    settle_t = (raw - 1.0) / 0.32
                    ring_alpha = int((1.0 - settle_t) * 85)
                    ring_expand = int(settle_t * 7)
                    pulse_rect = marker_rect.inflate(6 + ring_expand * 2, 4 + ring_expand * 2)
                    pulse = pygame.Surface((pulse_rect.width + 8, pulse_rect.height + 8), pygame.SRCALPHA)
                    pygame.draw.rect(
                        pulse,
                        (*selected_accent, ring_alpha),
                        pygame.Rect(4, 4, pulse_rect.width, pulse_rect.height),
                        2,
                        border_radius=10,
                    )
                    surf.blit(pulse, (pulse_rect.x - 4, pulse_rect.y - 4))
            except Exception:
                pass

    for qi, quick, qrect_local, qlabel, accent, mark_meta in button_rows:
        qhover = qrect_local.collidepoint(mx_local, my_local)

        is_selected = active_quick_index is not None and int(active_quick_index) == qi
        is_flashing = flash_quick_index is not None and int(flash_quick_index) == qi

        # During a slide, the moving selector is the highlight. Do not also
        # repaint the destination button as fully selected on frame 1; that
        # double-state is what made the animation feel choppy.
        is_selected_fill = is_selected and not slide_is_active
        active = bool(quick.get("active", False)) or is_selected_fill or is_flashing

        fill = (58, 72, 104) if is_selected_fill else (35, 43, 62)
        if is_flashing:
            fill = _brighten(fill, 30)

        _draw_quick_assist_button(
            surf,
            qrect_local,
            qlabel,
            smallfont,
            active=active,
            hover=qhover,
            accent=accent,
            fill=fill,
            mark_meta=mark_meta,
        )

        if is_selected_fill:
            pygame.draw.rect(
                surf,
                (*accent, 95),
                qrect_local.inflate(-3, -3),
                2,
                border_radius=4,
            )

        qclick = pygame.Rect(
            panel_rect.x + qrect_local.x,
            panel_rect.y + qrect_local.y,
            qrect_local.width,
            qrect_local.height,
        ).inflate(8, 8)

        out[(slot_label, qi)] = qclick

    return out




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
    if last_scan_normals is not None:
        return last_scan_normals, last_scan_time
    if HAVE_SCAN_NORMALS and scan_normals_all is not None:
        try:
            data = scan_normals_all.scan_once()
            return data, time.time()
        except Exception as e:
            print("sync scan failed:", e)
    return None, last_scan_time


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
        # Auto refresh must stay lightweight.  Cache-only avoids the full dynamic
        # MEM2 scanner from running in the background during gameplay.  Manual
        # scans still call scan_normals_all.scan_once() directly and can create
        # new profiles when needed.
        scan_worker = ScanNormalsWorker(
            lambda: scan_normals_all.scan_once(cache_only=True),
            full_scan_func=lambda: scan_normals_all.scan_once(force_dynamic=True, cache_only=False),
        )
        scan_worker.start()
    else:
        scan_worker = None

    # Saved frame-data patches can be shared as JSON configs. Detect them at
    # launch and let the user choose one of three friendly modes: skip, ask per
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
    last_scan_normals = None
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
        """Cheap quick-assist state maintenance.

        The selected assist is already stored in assist_scanner_backend when the
        user clicks a quick-assist button.  The backend now patches that stored
        slot profile on assist standby/jump-in/attack, so this main-loop helper
        must not re-apply the full assist route at idle.  Re-applying here was
        the remaining 25-35 ms assist_persist spike.
        """
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

    last_adv_display = ""
    pending_hits     = []
    frame_idx        = 0

    # ------------------------------------------------------------------
    # Master overlay subprocess
    # ------------------------------------------------------------------
    master_overlay_proc   = None
    master_overlay_active = False
    overlay_enabled       = True

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
    HITBOX_FILTER_FILE = "hitbox_filter.json"
    hitbox_slots = {"P1": False, "P2": False, "P3": False, "P4": False}

    def _write_hitbox_filter():
        try:
            with open(HITBOX_FILTER_FILE, "w") as f:
                json.dump(hitbox_slots, f)
        except Exception:
            pass

    def _write_master_control():
        payload = {
            "show_hud":      overlay_enabled,
            "show_hitboxes": any(hitbox_slots.values()),
            "show_debug":    False,
        }
        try:
            with open(MASTER_CONTROL_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    def _sync_master_overlay_state():
        want_hitboxes = any(hitbox_slots.values())
        want_process  = overlay_enabled or want_hitboxes
        if want_process and not master_overlay_active:
            _launch_master_overlay()
        elif not want_process and master_overlay_active:
            _stop_master_overlay()

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

    select_probe_state = new_select_probe_state()

    def _select_probe_zero_next() -> dict:
        return zero_next_select_probe(select_probe_state, rbytes, wbytes)

    def _select_probe_restore_all() -> dict:
        return restore_select_probes(select_probe_state, wbytes)

    def _select_probe_open_window() -> None:
        try:
            open_select_probe_window(select_probe_state, rbytes, wbytes)
        except Exception as e:
            print(f"[select probe] window unavailable: {e!r}", flush=True)

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
            "select_probe": get_select_probe_debug_state(select_probe_state),
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
        patch_result = {}
        try:
            if runtime_pm is not None:
                patch_result = runtime_pm.clear_runtime_patch_state(clear_cache=True)
        except Exception as e:
            patch_result = {"error": repr(e)}
        select_probe_result = {}
        try:
            select_probe_result = _select_probe_restore_all()
        except Exception as e:
            select_probe_result = {"error": repr(e)}
        try:
            _save_megacrash_trainer_config(megacrash_trainer_state)
        except Exception:
            pass
        return {"assist": assist_result, "hud": "released", "megacrash": "off", "patch_manager": patch_result, "select_probe": select_probe_result}

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
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.VIDEORESIZE:
                screen = pygame.display.set_mode(ev.size, pygame.RESIZABLE)
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

        # Scan worker results
        if scan_worker:
            res, ts = scan_worker.get_latest()
            if res is not None and ts > last_scan_time:
                last_scan_normals = res
                if fd_patch_runtime is not None:
                    try:
                        if fd_patch_controller is not None:
                            fd_patch_controller.apply_to_scan_data(last_scan_normals)
                        else:
                            fd_patch_runtime.overlay_scan_data(last_scan_normals)
                    except Exception as e:
                        print(f"[fd patch] scan overlay/apply failed: {e!r}")
                last_scan_time    = ts
                scan_anim = {"start": now, "dur": SCAN_SLIDE_DURATION}
                try:
                    _mode = scan_worker.last_mode() if hasattr(scan_worker, "last_mode") else "scan"
                    if _mode == "full":
                        print("[fd profile] full dynamic profile build completed")
                except Exception:
                    pass

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

        # HUD Editor persistent state tick.  Hold Visible Wins must keep writing
        # even when the editor window is closed, so the main loop owns the
        # runtime tick and the Tk window is only a control panel.
        if tick_hud_editor_state is not None:
            _perf_section_start = time.perf_counter()
            try:
                tick_hud_editor_state(now=now)
            except Exception as e:
                if frame_idx % 60 == 0:
                    print(f"[hud editor] persistent tick failed: {e!r}")
            _perf_warn("hud_editor_tick", _perf_section_start)

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

        hb_btn_rect, ps_btn_rect, as_btn_rect, hud_btn_rect, megacrash_btn_rect, memdump_btn_rect, win_counter_btn_rect, overseer_btn_rect, select_probe_btn_rect, hb_filter_rects = draw_top_command_dock(
            screen,
            smallfont,
            hitbox_slots=hitbox_slots,
            overlay_enabled=overlay_enabled,
            megacrash_trainer_enabled=bool(megacrash_trainer_state.get("enabled", False)),
            megacrash_trainer_chance=int(megacrash_trainer_state.get("chance", MEGACRASH_TRAINER_DEFAULT_CHANCE)),
            megacrash_trainer_mode=str(megacrash_trainer_state.get("mode", MEGACRASH_TRAINER_DEFAULT_MODE)),
            megacrash_trainer_delay_frames=int(megacrash_trainer_state.get("delay_frames", MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES)),
            mem_dump_active=bool(mem_dump_state.get("active", False)),
            mem_dump_label=str(mem_dump_state.get("label") or ""),
            select_probe_label=select_probe_button_label(select_probe_state),
            select_probe_active=bool((select_probe_state or {}).get("modified_count", 0)),
            win_score_enabled=_win_score_active_for_dock(),
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
            frame_btn_w    = 110
            mission_btn_w  = 110
            btn_gap        = 8
            bottom_pad     = 8
            total_btn_w    = frame_btn_w + btn_gap + mission_btn_w
            btn_x          = panel_rect.width - total_btn_w - 10
            btn_y          = panel_rect.height - btn_h - bottom_pad

            frame_btn_local   = pygame.Rect(btn_x, btn_y, frame_btn_w, btn_h)
            mission_btn_local = pygame.Rect(btn_x + frame_btn_w + btn_gap, btn_y, mission_btn_w, btn_h)

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

            draw_scan_normals_polished(scan_surf, scan_surf.get_rect(), font, smallfont, scan_display, t_ms=t_ms, scan_fx_by_slot=scan_fx)
            if scan_anim is not None:
                t    = now - scan_anim["start"]
                dur  = scan_anim.get("dur", SCAN_SLIDE_DURATION)
                frac = max(0.0, min(1.0, t / dur)) if dur else 1.0
                y    = (scan_rect.y + scan_rect.height + 8) + (scan_rect.y - (scan_rect.y + scan_rect.height + 8)) * frac
                if frac >= 1.0:
                    scan_anim = None
            else:
                y = scan_rect.y
            scan_surf.set_alpha(255)
            screen.blit(scan_surf, (scan_rect.x, int(y)))

        elif active_bottom_tab == "events":
            draw_event_log(screen, bottom_content_rect, font, smallfont)

        elif active_bottom_tab == "debug":
            if frame_idx % DEBUG_REFRESH_EVERY == 0:
                debug_cache = merged_debug_values()
            debug_click_areas, debug_max_scroll = draw_debug_overlay(
                screen, bottom_content_rect, smallfont, debug_cache, debug_scroll_offset
            )

        elif active_bottom_tab == "activity":
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

        probe_last = str((select_probe_state or {}).get("last") or "")
        if probe_last:
            status_parts.append(f"CS Probe {probe_last}")

        draw_status_rail(
            screen,
            smallfont,
            text=" | ".join(status_parts),
        )

        pygame.display.flip()

        # ------------------------------------------------------------------
        # Click handling
        # ------------------------------------------------------------------
        if mouse_right_clicked_pos is not None:
            mx, my = mouse_right_clicked_pos
            if select_probe_btn_rect.collidepoint(mx, my):
                _select_probe_restore_all()
                mouse_right_clicked_pos = None

        if mouse_clicked_pos is not None:
            mx, my = mouse_clicked_pos

            if hb_btn_rect.collidepoint(mx, my):
                new_state = not any(hitbox_slots.values())
                for k in hitbox_slots:
                    hitbox_slots[k] = new_state
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

            elif megacrash_btn_rect.collidepoint(mx, my):
                if open_megacrash_trainer_window is not None:
                    open_megacrash_trainer_window(megacrash_trainer_state, _save_megacrash_trainer_config)
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
                _select_probe_open_window()
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
                for slot_name, cb_rect in hb_filter_rects.items():
                    if cb_rect.collidepoint(mx, my):
                        hitbox_slots[slot_name] = not hitbox_slots[slot_name]
                        _write_hitbox_filter()
                        _write_master_control()
                        _sync_master_overlay_state()
                        break

            for _tab_key, _tab_rect in list(bottom_tab_rects.items()):
                if _tab_rect.collidepoint(mx, my):
                    if active_bottom_tab != _tab_key:
                        active_bottom_tab = _tab_key
                        bottom_tab_fade = {"start": now, "dur": 0.18}
                    mouse_clicked_pos = None
                    break
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
                    if mission_mgr.active_slot and (not overlay_enabled) and (not any(hitbox_slots.values())):
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
                    last_scan_normals, last_scan_time = ensure_scan_now(last_scan_normals, last_scan_time)
                    if _fd_row_needs_dynamic_profile(slot_label, last_scan_normals) and scan_worker:
                        try:
                            scan_worker.request(force_dynamic=True)
                            print(f"[fd profile] queued full profile build for {slot_label}; cached normals are missing")
                        except TypeError:
                            scan_worker.request()
                    if last_scan_normals:
                        open_frame_data_window(slot_label, last_scan_normals)
                    panel_btn_flash[slot_label] = PANEL_FLASH_FRAMES
                    break


        # Right-click copy handling
        if mouse_right_clicked_pos is not None:
            mx, my = mouse_right_clicked_pos

            if megacrash_btn_rect.collidepoint(mx, my):
                if open_megacrash_trainer_window is not None:
                    open_megacrash_trainer_window(megacrash_trainer_state, _save_megacrash_trainer_config)
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

        # Cache-only scans intentionally do not build brand-new profiles. If a
        # loaded slot reports profile_cache_miss, run one debounced full dynamic
        # scan in the background so characters like Ippatsuman do not remain
        # permanently blank. Existing cached-profile characters stay fast.
        if HAVE_SCAN_NORMALS and FD_BUILD_MISSING_PROFILES and scan_worker and last_scan_normals:
            _missing_sig = _fd_missing_profile_signature(last_scan_normals)
            if _missing_sig:
                if pending_missing_profile_signature != _missing_sig:
                    pending_missing_profile_signature = _missing_sig
                    pending_missing_profile_since = now
                _worker_busy = False
                try:
                    _worker_busy = bool(scan_worker.is_busy())
                except Exception:
                    _worker_busy = False
                if (
                    not _worker_busy
                    and (now - pending_missing_profile_since) >= FD_MISSING_PROFILE_BUILD_DELAY_SEC
                    and (now - last_missing_profile_build_time) >= FD_MISSING_PROFILE_BUILD_MIN_INTERVAL_SEC
                    and _missing_sig != last_missing_profile_build_signature
                ):
                    try:
                        scan_worker.request(force_dynamic=True)
                    except TypeError:
                        scan_worker.request()
                    last_missing_profile_build_time = now
                    last_missing_profile_build_signature = _missing_sig
                    names = ", ".join(f"{slot}:{key}" for slot, key in _missing_sig)
                    print(f"[fd profile] queued one-time full profile build for missing cached normals: {names}")
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