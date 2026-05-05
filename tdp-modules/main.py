import os
import csv
import time
import json
import subprocess
import sys
import pygame
from subprocess_compat import frozen_exe


def resource_path(*parts):
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)

from constants import (
    SLOTS,
    CHAR_NAMES,
    OFF_CHAR_ID,
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

from dolphin_io import hook, rd8, rd32, wd8, addr_in_ram, rbytes

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
    from assist_scanner_window import (
        open_assist_scanner_window,
        tick_assist_profiles_from_main,
        get_quick_assists_for_slot,
        apply_quick_assist_from_main,
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
    def apply_quick_assist_from_main(_slot_label, _quick_index, _snap=None):
        return False

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

HB_BTN_X, HB_BTN_Y = 8, 8
HB_BTN_W, HB_BTN_H = 130, 22
TOP_UI_RESERVED = HB_BTN_Y + HB_BTN_H + 12


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
        scan_worker = ScanNormalsWorker(scan_normals_all.scan_once)
        scan_worker.start()
    else:
        scan_worker = None

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

    manual_scan_requested = False
    need_rescan_normals   = False

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
    hitbox_slots = {"P1": True, "P2": True, "P3": True, "P4": True}

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

    # Momentary write restore
    hype_restore_addr  = None
    hype_restore_ts    = 0.0
    hype_restore_orig  = 0
    special_restore_addr = None
    special_restore_ts   = 0.0
    special_restore_orig = 0

    running = True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    while running:
        now  = time.time()
        t_ms = pygame.time.get_ticks()
        mouse_clicked_pos = None

        # Events
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.VIDEORESIZE:
                screen = pygame.display.set_mode(ev.size, pygame.RESIZABLE)
            elif ev.type == pygame.MOUSEBUTTONDOWN:
                if ev.button == 1:
                    mouse_clicked_pos = ev.pos
                elif ev.button == 4 and debug_overlay:
                    if debug_scroll_offset > 0:
                        debug_scroll_offset -= 1
                elif ev.button == 5 and debug_overlay:
                    if debug_scroll_offset < debug_max_scroll:
                        debug_scroll_offset += 1
            elif ev.type == pygame.MOUSEWHEEL and debug_overlay:
                if ev.y > 0 and debug_scroll_offset > 0:
                    debug_scroll_offset -= 1
                elif ev.y < 0 and debug_scroll_offset < debug_max_scroll:
                    debug_scroll_offset += 1

        # Scan worker results
        if scan_worker:
            res, ts = scan_worker.get_latest()
            if res is not None and ts > last_scan_time:
                last_scan_normals = res
                last_scan_time    = ts
                scan_anim = {"start": now, "dur": SCAN_SLIDE_DURATION}

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
        try:
            tick_assist_profiles_from_main(snaps)
        except Exception as e:
            if frame_idx % 60 == 0:
                print(f"[assist scanner] main trigger failed: {e!r}")

        # Mission manager tick
        mission_mgr.update(snaps, render_snap_by_slot, frame_idx, now)

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

            y = anim["from_y"] + (anim["to_y"] - anim["from_y"]) * frac

            from_a = anim.get("from_a", 255)
            to_a   = anim.get("to_a",   255)
            if from_a == 0 and to_a > 0:
                inner = max(0.0, min(1.0, (frac - 0.9) / 0.1)) if frac > 0.9 else 0.0
                alpha = int(from_a + (to_a - from_a) * inner)
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

        # Top-bar buttons
        _check_master_overlay_proc()
        HB_BTN_X, HB_BTN_Y = 8, 8
        HB_BTN_W, HB_BTN_H = 150, 22
        hb_btn_rect = pygame.Rect(HB_BTN_X, HB_BTN_Y, HB_BTN_W, HB_BTN_H)

        hb_btn_col   = (60, 200, 80)  if any(hitbox_slots.values()) else (80, 80, 80)
        hb_btn_label = "Hitboxes: ON" if any(hitbox_slots.values()) else "Hitboxes: OFF"
        mx_h, my_h   = pygame.mouse.get_pos()
        if hb_btn_rect.collidepoint(mx_h, my_h):
            hb_btn_col = tuple(min(255, c + 30) for c in hb_btn_col)
        pygame.draw.rect(screen, hb_btn_col, hb_btn_rect, border_radius=3)
        pygame.draw.rect(screen, (200, 200, 200), hb_btn_rect, 1, border_radius=3)
        screen.blit(smallfont.render(hb_btn_label, True, (230, 230, 230)),
                    (HB_BTN_X + 6, HB_BTN_Y + 4))

        PS_BTN_X = HB_BTN_X + HB_BTN_W + 8
        PS_BTN_W, PS_BTN_H = 150, 22
        ps_btn_rect = pygame.Rect(PS_BTN_X, HB_BTN_Y, PS_BTN_W, PS_BTN_H)
        ps_col = (60, 80, 160) if not ps_btn_rect.collidepoint(mx_h, my_h) else (90, 110, 200)
        pygame.draw.rect(screen, ps_col, ps_btn_rect, border_radius=3)
        pygame.draw.rect(screen, (200, 200, 200), ps_btn_rect, 1, border_radius=3)
        screen.blit(smallfont.render("Proj Scanner", True, (230, 230, 230)),
                    (PS_BTN_X + 6, HB_BTN_Y + 4))

        AS_BTN_X = PS_BTN_X + PS_BTN_W + 8
        AS_BTN_W, AS_BTN_H = 130, 22
        as_btn_rect = pygame.Rect(AS_BTN_X, HB_BTN_Y, AS_BTN_W, AS_BTN_H)
        as_col = (90, 70, 150) if not as_btn_rect.collidepoint(mx_h, my_h) else (120, 100, 190)
        pygame.draw.rect(screen, as_col, as_btn_rect, border_radius=3)
        pygame.draw.rect(screen, (200, 200, 200), as_btn_rect, 1, border_radius=3)
        screen.blit(smallfont.render("Assist Scanner", True, (230, 230, 230)),
                    (AS_BTN_X + 6, HB_BTN_Y + 4))

        HUD_BTN_X = AS_BTN_X + AS_BTN_W + 8
        HUD_BTN_W, HUD_BTN_H = 140, 22
        hud_btn_rect = pygame.Rect(HUD_BTN_X, HB_BTN_Y, HUD_BTN_W, HUD_BTN_H)
        hud_btn_col   = (160, 110, 30) if overlay_enabled else (80, 80, 80)
        hud_btn_label = "Overlay: ON"  if overlay_enabled else "Overlay: OFF"
        if hud_btn_rect.collidepoint(mx_h, my_h):
            hud_btn_col = tuple(min(255, c + 30) for c in hud_btn_col)
        pygame.draw.rect(screen, hud_btn_col, hud_btn_rect, border_radius=3)
        pygame.draw.rect(screen, (200, 200, 200), hud_btn_rect, 1, border_radius=3)
        screen.blit(smallfont.render(hud_btn_label, True, (230, 230, 230)),
                    (HUD_BTN_X + 6, HB_BTN_Y + 4))

        # Hitbox filter checkboxes
        hb_filter_rects = {}
        fx = HB_BTN_X
        fy = HB_BTN_Y + HB_BTN_H + 4
        filter_label_surf = smallfont.render("Filter Hitboxes (click to toggle a slot):", True, (180, 180, 180))
        screen.blit(filter_label_surf, (fx, fy))
        fx += filter_label_surf.get_width() + 6
        slot_colors = {
            "P1": (255, 100, 100), "P2": (100, 160, 255),
            "P3": (255, 100, 200), "P4": (100, 255, 140),
        }
        for slot_name in ("P1", "P2", "P3", "P4"):
            col     = slot_colors[slot_name]
            cb_rect = pygame.Rect(fx, fy, 14, 14)
            if hitbox_slots[slot_name]:
                pygame.draw.rect(screen, col, cb_rect, border_radius=2)
                pygame.draw.rect(screen, (220, 220, 220), cb_rect, 1, border_radius=2)
                screen.blit(smallfont.render("✓", True, (0, 0, 0)), (fx + 1, fy - 1))
            else:
                pygame.draw.rect(screen, (40, 40, 40), cb_rect, border_radius=2)
                pygame.draw.rect(screen, (140, 140, 140), cb_rect, 1, border_radius=2)
            label_surf = smallfont.render(slot_name, True, col)
            screen.blit(label_surf, (fx + 18, fy))
            total_w = 18 + label_surf.get_width() + 8
            hb_filter_rects[slot_name] = pygame.Rect(fx, fy, total_w, 16)
            fx += total_w + 10

        # Panel rects
        r_p1c1, a_p1c1 = anim_rect_and_alpha("P1-C1", layout["p1c1"])
        r_p2c1, a_p2c1 = anim_rect_and_alpha("P2-C1", layout["p2c1"])
        r_p1c2, a_p1c2 = anim_rect_and_alpha("P1-C2", layout["p1c2"])
        r_p2c2, a_p2c2 = anim_rect_and_alpha("P2-C2", layout["p2c2"])

        quick_btn_areas = {}

        def blit_panel_with_buttons(panel_rect, slot_label, alpha, header):
            snap     = render_snap_by_slot.get(slot_label)
            portrait = render_portrait_by_slot.get(slot_label, placeholder_portrait)

            surf = pygame.Surface((panel_rect.width, panel_rect.height), pygame.SRCALPHA)
            draw_panel_classic(surf, surf.get_rect(), snap, portrait, font, smallfont, header, t_ms)

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

            if flash_left > 0:
                frame_base_col   = (70, 105, 170)
                frame_border_col = (235, 240, 255)
            elif frame_hover:
                frame_base_col   = (48, 54, 68)
                frame_border_col = (210, 220, 235)
            else:
                frame_base_col   = (31, 33, 42)
                frame_border_col = (135, 145, 165)

            if mission_mgr.active_slot == slot_label:
                mission_base_col   = (88, 68, 135)
                mission_border_col = (235, 240, 255)
            elif mission_hover:
                mission_base_col   = (48, 54, 68)
                mission_border_col = (210, 220, 235)
            else:
                mission_base_col   = (31, 33, 42)
                mission_border_col = (135, 145, 165)

            pygame.draw.rect(surf, frame_base_col,   frame_btn_local,   border_radius=3)
            pygame.draw.rect(surf, frame_border_col, frame_btn_local,   1, border_radius=3)
            surf.blit(smallfont.render("Frame Data", True, (220, 220, 220)),
                      (frame_btn_local.x + 6, frame_btn_local.y + 2))

            pygame.draw.rect(surf, mission_base_col,   mission_btn_local, border_radius=3)
            pygame.draw.rect(surf, mission_border_col, mission_btn_local, 1, border_radius=3)
            surf.blit(smallfont.render("Mission Mode", True, (220, 220, 220)),
                      (mission_btn_local.x + 6, mission_btn_local.y + 2))

            if flash_left > 0:
                pygame.draw.rect(surf, (255, 255, 255),
                                 frame_btn_local.inflate(4, 4), 2, border_radius=4)

            # Optional quick-assist buttons. main.py only draws/clicks these;
            # assist_scanner_window owns the JSON, route resolution, and writes.
            quick_defs = []
            if snap:
                try:
                    quick_defs = get_quick_assists_for_slot(slot_label, snap)[:4]
                except Exception:
                    quick_defs = []
            if not quick_defs and snap:
                quick_defs = [
                    {"label": "304", "table": 304},
                    {"label": "305", "table": 305},
                    {"label": "306", "table": 306},
                    {"label": "Default", "default": True},
                ]
            if quick_defs:
                qa_gap = 6
                qa_count = min(4, len(quick_defs))
                qa_h = 22
                qa_x0 = 10
                qa_total_w = panel_rect.width - 20
                qa_w = max(52, int((qa_total_w - qa_gap * (qa_count - 1)) / qa_count))
                qa_y = max(78, btn_y - qa_h - 12)

                # Sleek footer strip for Quick Assists. This visually separates
                # them from the live fighter text above and from utility buttons
                # below.
                strip_y = max(0, qa_y - 7)
                strip_h = min(panel_rect.height - strip_y - 4, qa_h + 16)
                pygame.draw.rect(surf, (20, 22, 30),
                                 pygame.Rect(6, strip_y, panel_rect.width - 12, strip_h),
                                 border_radius=4)
                pygame.draw.line(surf, (70, 76, 96),
                                 (10, strip_y), (panel_rect.width - 10, strip_y))

                for qi, quick in enumerate(quick_defs):
                    qx = qa_x0 + qi * (qa_w + qa_gap)
                    qrect_local = pygame.Rect(qx, qa_y, qa_w, qa_h)
                    qhover = qrect_local.collidepoint(mx_local, my_local)
                    qbase = (42, 52, 76) if not qhover else (64, 78, 112)
                    qborder = (110, 132, 170) if not qhover else (175, 195, 230)
                    pygame.draw.rect(surf, qbase, qrect_local, border_radius=4)
                    pygame.draw.rect(surf, qborder, qrect_local, 1, border_radius=4)
                    qlabel = str(quick.get("label", f"A{qi + 1}"))
                    max_chars = max(7, int((qa_w - 10) / 7))
                    if len(qlabel) > max_chars:
                        qlabel = qlabel[:max_chars - 1] + "."
                    label_surf = smallfont.render(qlabel, True, (232, 235, 242))
                    surf.blit(label_surf,
                              (qrect_local.x + 5, qrect_local.y + (qa_h - label_surf.get_height()) // 2))
                    # Drawn button stays compact, but the clickable area is
                    # intentionally larger. The 20px footer buttons were too
                    # easy to miss, especially on the lower-left P1-C2 panel.
                    qclick = pygame.Rect(
                        panel_rect.x + qrect_local.x,
                        panel_rect.y + qrect_local.y,
                        qrect_local.width,
                        qrect_local.height,
                    ).inflate(8, 8)
                    quick_btn_areas[(slot_label, qi)] = qclick

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

        draw_activity(screen, layout["act"], font, last_adv_display)
        draw_event_log(screen, layout["events"], font, smallfont)

        debug_rect = layout["debug"]
        if frame_idx % DEBUG_REFRESH_EVERY == 0:
            debug_cache = merged_debug_values()
        debug_click_areas, debug_max_scroll = draw_debug_overlay(
            screen, debug_rect, smallfont, debug_cache, debug_scroll_offset
        )

        scan_rect = layout["scan"]
        scan_surf = pygame.Surface((scan_rect.width, scan_rect.height), pygame.SRCALPHA)
        draw_scan_normals(scan_surf, scan_surf.get_rect(), font, smallfont, last_scan_normals)
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

        # Write data files for subprocesses
        mission_mgr.write_overlay_data(render_snap_by_slot)
        hud_mgr.write_data(render_snap_by_slot, last_scan_normals, mission_mgr)
        hud_mgr.check_proc()

        pygame.display.flip()

        # ------------------------------------------------------------------
        # Click handling
        # ------------------------------------------------------------------
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

            else:
                for slot_name, cb_rect in hb_filter_rects.items():
                    if cb_rect.collidepoint(mx, my):
                        hitbox_slots[slot_name] = not hitbox_slots[slot_name]
                        _write_hitbox_filter()
                        _write_master_control()
                        _sync_master_overlay_state()
                        break

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
                        panel_btn_flash[slot_label] = PANEL_FLASH_FRAMES
                    quick_clicked = True
                    break
            if quick_clicked:
                mouse_clicked_pos = None
                continue

            # Debug panel row -> copy address
            copied = False
            for name, (r, addr) in debug_click_areas.items():
                if r.collidepoint(mx, my):
                    _copy_to_clipboard(f"0x{addr:08X}" if isinstance(addr, int) else str(addr))
                    copied = True
                    break

            # Character panel -> copy base
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

            # Mission mode buttons
            for slot_label, btn_rect in [
                ("P1-C1", mission_btn_p1c1), ("P2-C1", mission_btn_p2c1),
                ("P1-C2", mission_btn_p1c2), ("P2-C2", mission_btn_p2c2),
            ]:
                if btn_rect.collidepoint(mx, my):
                    mission_mgr.toggle_active_slot(slot_label)
                    break

            # Frame data buttons
            for slot_label, btn_rect in [
                ("P1-C1", btn_p1c1), ("P2-C1", btn_p2c1),
                ("P1-C2", btn_p1c2), ("P2-C2", btn_p2c2),
            ]:
                if btn_rect.collidepoint(mx, my):
                    last_scan_normals, last_scan_time = ensure_scan_now(last_scan_normals, last_scan_time)
                    if last_scan_normals:
                        open_frame_data_window(slot_label, last_scan_normals)
                    panel_btn_flash[slot_label] = PANEL_FLASH_FRAMES
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

        # Normals rescan triggers
        if HAVE_SCAN_NORMALS and need_rescan_normals and scan_worker:
            scan_worker.request()
            need_rescan_normals = False

        if HAVE_SCAN_NORMALS and manual_scan_requested:
            if scan_worker:
                scan_worker.request()
            else:
                try:
                    last_scan_normals = scan_normals_all.scan_once()
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