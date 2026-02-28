
import os
import csv
import time
import json
import subprocess
import sys
import pygame

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
    draw_scan_normals,   # ONLY this used on HUD
)

from redscan import RedHealthScanner
from global_redscan import GlobalRedScanner
from events import log_engaged, log_hit, log_frame_advantage

# optional deep scan (the hitbox-augmented one)
try:
    import scan_normals_all
    HAVE_SCAN_NORMALS = True
    from scan_normals_all import ANIM_MAP as SCAN_ANIM_MAP
except Exception:
    scan_normals_all = None
    HAVE_SCAN_NORMALS = False
    SCAN_ANIM_MAP = {}

# Frame data window (editable GUI + legacy Tk)
from frame_data_window import open_frame_data_window


# ---------------------------------------------------------------------------
# Tunables / globals for timing and animation
# ---------------------------------------------------------------------------

TARGET_FPS = 60
DAMAGE_EVERY_FRAMES = 3
ADV_EVERY_FRAMES = 2

PANEL_SLIDE_DURATION = 2.0
PANEL_FLASH_FRAMES = 12
SCAN_SLIDE_DURATION = 0.7

# offsets for "real" baroque (relative to fighter base)
HP32_OFF = 0x28
POOL32_OFF = 0x2C
# ---------------------------------------------------------------------------
# Bulk fighter struct read helpers
# ---------------------------------------------------------------------------

FIGHTER_BLOCK_SIZE = 0x120  # safely covers OFF_CHAR_ID, HP32_OFF, POOL32_OFF

def u32be_from_block(block: bytes, off: int) -> int | None:
    if not block or off + 4 > len(block):
        return None
    return (
        (block[off] << 24)
        | (block[off + 1] << 16)
        | (block[off + 2] << 8)
        | block[off + 3]
    )

# Reaction / hitstun IDs used as a crude "victim is being hit" signal
REACTION_STATES = {48, 64, 65, 66, 73, 80, 81, 82, 90, 92, 95, 96, 97}

# TvC "giants" (PTX-40A, Gold Lightan). If you later add others, put IDs here.
GIANT_IDS = {11, 22}

HB_BTN_X, HB_BTN_Y = 8, 8
HB_BTN_W, HB_BTN_H = 130, 22

TOP_UI_RESERVED = HB_BTN_Y + HB_BTN_H + 12
# ---------------------------------------------------------------------------
# Assist tracking (per slot)
# ---------------------------------------------------------------------------

class AssistState:
    """
    Lightweight per-slot assist tracker.

    We currently infer assists from the primary/secondary attack IDs.
    Once assist animations are fully mapped, ASSIST_FLYIN_IDS /
    ASSIST_ATTACK_IDS can be populated per character to refine this.
    """
    __slots__ = ("is_assisting", "phase", "last_anim")

    def __init__(self):
        self.is_assisting = False
        self.phase = None
        self.last_anim = None


ASSIST_FLYIN_IDS = set()
ASSIST_ATTACK_IDS = set()
_ASSIST_BY_SLOT = {}


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


def update_assist_for_snap(slotname: str, snap: dict, cur_anim: int | None) -> None:
    if not slotname or snap is None:
        return

    state = _ASSIST_BY_SLOT.get(slotname)
    if state is None:
        state = AssistState()
        _ASSIST_BY_SLOT[slotname] = state

    state.last_anim = cur_anim

    if cur_anim in ASSIST_FLYIN_IDS:
        state.is_assisting = True
        state.phase = "flyin"
    elif cur_anim in ASSIST_ATTACK_IDS:
        state.is_assisting = True
        state.phase = "attack"
    else:
        if state.is_assisting and state.phase in ("flyin", "attack"):
            state.phase = "recover"
        elif state.phase == "recover":
            state.is_assisting = False
            state.phase = None

    snap["assist_phase"] = state.phase
    snap["is_assist"] = state.is_assisting


def merged_debug_values():
    """
    Combine debug flags and training flags into a single ordered list for the
    overlay, with TrPause placed directly under the PauseOverlay row.
    """
    core_flags = read_debug_flags()
    training = read_training_flags()

    trpause_row = None
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
    if not snap:
        return None
    return snap


def init_pygame():
    pygame.init()

    try:
        font = pygame.font.SysFont("consolas", FONT_MAIN_SIZE)
    except Exception:
        font = pygame.font.Font(None, FONT_MAIN_SIZE)

    try:
        smallfont = pygame.font.SysFont("consolas", FONT_SMALL_SIZE)
    except Exception:
        smallfont = pygame.font.Font(None, FONT_SMALL_SIZE)

    # --- WINDOW / TASKBAR ICON ---
    icon_path = os.path.join("assets", "icon.png")
    if os.path.exists(icon_path):
        icon = pygame.image.load(icon_path).convert_alpha()
        pygame.display.set_icon(icon)

    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.RESIZABLE)
    pygame.display.set_caption("TvC Live HUD / Frame Probe")

    return screen, font, smallfont



def resolve_bases(last_base_by_ptr: dict, y_off_by_base: dict) -> list[tuple[str, str, int | None]]:
    """
    Resolve base addresses for each character slot using SLOTS pointer addresses.
    Returns a list of (slotname, teamtag, base|None).
    """
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
    """
    Determine whether each team should be treated as "giant occupies both slots".

    
      - A team is giant_solo only if:
          * C1 exists AND is a giant ID, AND
          * (C2 is missing) OR (C2 base == C1 base)

    If a giant is present but a real partner exists (different base), giant_solo is False.
    """
    def team_solo(prefix: str) -> bool:
        c1 = snaps.get(f"{prefix}-C1")
        c2 = snaps.get(f"{prefix}-C2")
        if not c1:
            return False
        c1_id = c1.get("id") or 0
        if c1_id not in GIANT_IDS:
            return False
        if not c2:
            return True
        b1 = c1.get("base")
        b2 = c2.get("base")
        if isinstance(b1, int) and isinstance(b2, int) and b1 == b2:
            return True
        return False

    return team_solo("P1"), team_solo("P2")


def ensure_scan_now(last_scan_normals, last_scan_time):
    """
    Ensure we have at least one normals scan payload.
    If the worker hasn't produced anything yet, do a synchronous scan fallback.
    Returns (data, last_scan_time_updated).
    """
    if last_scan_normals is not None:
        return last_scan_normals, last_scan_time

    if HAVE_SCAN_NORMALS and scan_normals_all is not None:
        try:
            data = scan_normals_all.scan_once()
            return data, time.time()
        except Exception as e:
            print("sync scan failed:", e)
            return None, last_scan_time

    return None, last_scan_time


def main():
    print("HUD: waiting for Dolphin...")
    hook()
    print("HUD: hooked Dolphin.")

    move_map, global_map = load_move_map(GENERIC_MAPPING_CSV, PAIR_MAPPING_CSV)

    screen, font, smallfont = init_pygame()
    clock = pygame.time.Clock()

    placeholder_portrait = load_portrait_placeholder()
    portraits = load_portraits_from_dir(os.path.join("assets", "portraits"))
    print(f"HUD: loaded {len(portraits)} portraits.")

    if HAVE_SCAN_NORMALS and scan_normals_all is not None:
        scan_worker = ScanNormalsWorker(scan_normals_all.scan_once)
        scan_worker.start()
    else:
        scan_worker = None

    last_scan_normals = None
    last_scan_time = 0.0
    scan_anim = None

    last_base_by_ptr = {}
    y_off_by_base = {}
    prev_hp = {}
    pool_baseline = {}
    char_meta_by_base = {}

    last_move_anim_id = {}
    last_char_by_slot = {}
    render_snap_by_slot = {}
    render_portrait_by_slot = {}

    panel_anim = {}
    anim_queue_after_scan = set()
    panel_btn_flash = {s: 0 for (s, _, _) in SLOTS}

    
    manual_scan_requested = False
    need_rescan_normals = False

    last_adv_display = ""
    pending_hits = []
    frame_idx = 0
    running = True
    debug_cache = []
    DEBUG_REFRESH_EVERY = 6 

    # ------------------------------------------------------------------
    # Hitbox overlay state
    # ------------------------------------------------------------------
    HITBOX_FILTER_FILE = "hitbox_filter.json"
    hitbox_proc = None          # subprocess.Popen handle
    hitbox_active = False
    hitbox_slots = {"P1": True, "P2": True, "P3": True, "P4": True}

    def _write_hitbox_filter():
        try:
            with open(HITBOX_FILTER_FILE, "w") as f:
                json.dump(hitbox_slots, f)
        except Exception:
            pass

    def _launch_hitbox_overlay():
        nonlocal hitbox_proc, hitbox_active
        _write_hitbox_filter()
        try:
            hitbox_proc = subprocess.Popen(
                [sys.executable, "hitboxesscaling.py"],
                creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0,
            )
            hitbox_active = True
            print(f"[hitbox] launched PID {hitbox_proc.pid}")
        except Exception as e:
            print(f"[hitbox] failed to launch: {e}")

    def _stop_hitbox_overlay():
        nonlocal hitbox_proc, hitbox_active
        if hitbox_proc and hitbox_proc.poll() is None:
            hitbox_proc.terminate()
        hitbox_proc = None
        hitbox_active = False

    def _check_hitbox_proc():
        nonlocal hitbox_proc, hitbox_active
        if hitbox_proc and hitbox_proc.poll() is not None:
            hitbox_proc = None
            hitbox_active = False
    # ------------------------------------------------------------------

    debug_overlay = True
    debug_click_areas = {}
    debug_scroll_offset = 0
    debug_max_scroll = 0

    hype_restore_addr = None
    hype_restore_ts = 0.0
    hype_restore_orig = 0

    special_restore_addr = None
    special_restore_ts = 0.0
    special_restore_orig = 0

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------
    while running:
        now = time.time()
        t_ms = pygame.time.get_ticks()

        mouse_clicked_pos = None

        # -----------------------------
        # Events / input
        # -----------------------------
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False

            elif ev.type == pygame.VIDEORESIZE:
                screen = pygame.display.set_mode(ev.size, pygame.RESIZABLE)

            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_F5:
                    manual_scan_requested = True

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
                # Newer pygame wheel event
                if ev.y > 0 and debug_scroll_offset > 0:
                    debug_scroll_offset -= 1
                elif ev.y < 0 and debug_scroll_offset < debug_max_scroll:
                    debug_scroll_offset += 1

        # -----------------------------
        # Background scan worker results
        # -----------------------------
        if scan_worker:
            res, ts = scan_worker.get_latest()
            if res is not None and ts > last_scan_time:
                last_scan_normals = res
                last_scan_time = ts
                scan_anim = {"start": now, "dur": SCAN_SLIDE_DURATION}

        # -----------------------------
        # Resolve slot bases
        # -----------------------------
        resolved_slots = resolve_bases(last_base_by_ptr, y_off_by_base)

        p1c1_base = next((b for n, t, b in resolved_slots if n == "P1-C1" and b), None)
        p2c1_base = next((b for n, t, b in resolved_slots if n == "P2-C1" and b), None)
        meter_p1 = read_meter(p1c1_base)
        meter_p2 = read_meter(p2c1_base)

        # -----------------------------
        # Build snapshots
        # -----------------------------
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

            snap["base"] = base
            snap["teamtag"] = teamtag
            snap["slotname"] = slotname

            # Prefer "true" ID from struct if present
            blk = rbytes(base, FIGHTER_BLOCK_SIZE)

            # ------------------------------------------------------------------
            # Character metadata cache (per base, ID-aware)
            # ------------------------------------------------------------------

            # Always determine current true ID first
            true_id_current = None

            if blk:
                true_id_current = u32be_from_block(blk, OFF_CHAR_ID)

            if true_id_current in (None, 0):
                try:
                    true_id_current = rd32(base + OFF_CHAR_ID)
                except Exception:
                    true_id_current = None

            meta = char_meta_by_base.get(base)

            # Refresh cache if new base OR ID changed
            if (
                meta is None
                or meta.get("id") != true_id_current
            ):
                name_cached = CHAR_NAMES.get(true_id_current)
                csv_id_cached = CHAR_ID_CORRECTION.get(name_cached, true_id_current)

                char_meta_by_base[base] = {
                    "id": true_id_current,
                    "name": name_cached,
                    "csv_char_id": csv_id_cached,
                }

            meta = char_meta_by_base.get(base)

            # Inject into snap
            if meta:
                snap["id"] = meta["id"]
                snap["name"] = meta["name"]
                snap["csv_char_id"] = meta["csv_char_id"]
            else:
                snap["csv_char_id"] = true_id_current
            csv_char_id = snap.get("csv_char_id")
            # Determine current animation ID
            cur_anim = snap.get("attA") or snap.get("attB")
            mv_label = lookup_move_name(cur_anim, csv_char_id)
            if not mv_label:
                mv_label = move_label_for(cur_anim, csv_char_id, move_map, global_map)

            snap["mv_label"] = mv_label
            snap["mv_id_display"] = cur_anim
            last_move_anim_id[base] = cur_anim

            # Pool percent baseline
            pool_byte = snap.get("hp_pool_byte")
            if pool_byte is not None:
                prev_max = pool_baseline.get(base, 0)
                if pool_byte > prev_max:
                    pool_baseline[base] = pool_byte
                max_pool = pool_baseline.get(base, 1)
                snap["pool_pct"] = (pool_byte / max_pool) * 100.0 if max_pool else 0.0
            else:
                snap["pool_pct"] = 0.0

            # Local 32-bit baroque values
            max_hp_stat = snap.get("max") or 0
            hp32 = 0
            pool32 = 0

            if blk:
                tmp_hp = u32be_from_block(blk, HP32_OFF)
                tmp_pool = u32be_from_block(blk, POOL32_OFF)

                if tmp_hp is not None:
                    hp32 = tmp_hp
                if tmp_pool is not None:
                    pool32 = tmp_pool

            # fallback if block read failed
            if hp32 == 0:
                hp32 = rd32(base + HP32_OFF) or 0
            if pool32 == 0:
                pool32 = rd32(base + POOL32_OFF) or 0

            ready_local = False
            red_amt = 0
            red_pct_max = 0.0

            if hp32 and pool32 and hp32 != pool32:
                ready_local = True
                bigger = max(hp32, pool32)
                smaller = min(hp32, pool32)
                red_amt = bigger - smaller
                if max_hp_stat:
                    red_pct_max = (red_amt / float(max_hp_stat)) * 100.0

            snap["baroque_local_hp32"] = hp32
            snap["baroque_local_pool32"] = pool32
            snap["baroque_ready_local"] = ready_local
            snap["baroque_red_amt"] = red_amt
            snap["baroque_red_pct_max"] = red_pct_max

            # Inputs only for P1-C1
            if slotname == "P1-C1":
                inputs_struct = {}
                for key, addr in INPUT_MONITOR_ADDRS.items():
                    v = rd8(addr)
                    inputs_struct[key] = 0 if v is None else v
                snap["inputs"] = inputs_struct
            else:
                snap["inputs"] = {}

            snaps[slotname] = snap

            # Slot change detection -> animations and rescan
            if last_char_by_slot.get(slotname) != snap.get("name"):
                last_char_by_slot[slotname] = snap.get("name")
                anim_queue_after_scan.add((slotname, "fadein"))
                need_rescan_normals = True

            render_snap_by_slot[slotname] = snap
            render_portrait_by_slot[slotname] = get_portrait_for_snap(
                snap, portraits, placeholder_portrait
            )

        # -----------------------------
        # Giant logic normalization (fixed)
        # -----------------------------
        # Only do the old "giant occupies both panels" reshuffle when truly solo.
        p1_giant_solo, p2_giant_solo = compute_team_giant_solo(snaps)
        if p1_giant_solo or p2_giant_solo:
            snaps = reassign_slots_for_giants(snaps)

        # -----------------------------
        # Damage / hit logging and frame advantage
        # -----------------------------
        if frame_idx % DAMAGE_EVERY_FRAMES == 0:
            for vic_slot, vic_snap in snaps.items():
                vic_move_id = vic_snap.get("attA") or vic_snap.get("attB")
                if vic_move_id not in REACTION_STATES:
                    continue

                vic_team = vic_snap["teamtag"]
                attackers = [s for s in snaps.values() if s["teamtag"] != vic_team]
                if not attackers:
                    continue

                best_d2 = None
                atk_snap = None
                for cand in attackers:
                    d2v = dist2(vic_snap, cand)
                    if best_d2 is None or d2v < best_d2:
                        best_d2 = d2v
                        atk_snap = cand
                if not atk_snap:
                    continue

                atk_move_id = atk_snap.get("attA") or atk_snap.get("attB")
                atk_move_label = atk_snap.get("mv_label")

                ADV_TRACK.start_contact(
                    atk_snap["base"],
                    vic_snap["base"],
                    frame_idx,
                    atk_move_id,
                    vic_move_id,
                )

                base = vic_snap["base"]
                hp_now = vic_snap["cur"]
                hp_prev = prev_hp.get(base, hp_now)
                prev_hp[base] = hp_now
                dmg = hp_prev - hp_now
                if dmg >= MIN_HIT_DAMAGE:
                    log_engaged(atk_snap, vic_snap, frame_idx)
                    log_hit(
                        atk_snap,
                        vic_snap,
                        dmg,
                        frame_idx,
                        atk_move_label,
                        atk_move_id,
                    )

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
                    atk_move_id = atk_snap.get("attA") or atk_snap.get("attB")
                    vic_move_id = vic_snap.get("attA") or vic_snap.get("attB")
                    ADV_TRACK.update_pair(
                        atk_snap["base"],
                        vic_snap["base"],
                        frame_idx,
                        atk_move_id,
                        vic_move_id,
                    )

            freshest = ADV_TRACK.get_freshest_final_info()
            if freshest:
                atk_b, vic_b, plusf, fin_frame = freshest
                if abs(plusf) <= 64:
                    atk_slot_obj = next((s for s in snaps.values() if s["base"] == atk_b), None)
                    vic_slot_obj = next((s for s in snaps.values() if s["base"] == vic_b), None)
                    if atk_slot_obj and vic_slot_obj:
                        last_adv_display = (
                            f"{atk_slot_obj['slotname']}({atk_slot_obj['name']}) vs "
                            f"{vic_slot_obj['slotname']}({vic_slot_obj['name']}): "
                            f"{plusf:+.1f}f"
                        )
                        log_frame_advantage(atk_slot_obj, vic_slot_obj, plusf)
                    else:
                        last_adv_display = f"Frame adv: {plusf:+.1f}f"

        # -----------------------------
        # Rendering
        # -----------------------------
        screen.fill(COL_BG)
        w, h = screen.get_size()
        layout = compute_layout(w, h - TOP_UI_RESERVED, snaps)

        for key, value in layout.items():
            if isinstance(value, pygame.Rect):
                value.y += TOP_UI_RESERVED

        # Override layout's "giant" flags with the corrected solo detection.
        # but HUD hides the partner panel due to a misdetected giant.
        layout["p1_is_giant"] = bool(p1_giant_solo)
        layout["p2_is_giant"] = bool(p2_giant_solo)

        # Consume queued panel animations
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
                offscreen_y = -panel_height - 8

                anim = {
                    "start": now,
                    "dur": PANEL_SLIDE_DURATION,
                    "from_y": None,
                    "to_y": None,
                    "from_a": 255,
                    "to_a": 255,
                }

                if kind == "fadein":
                    anim["from_y"] = offscreen_y
                    anim["to_y"] = base_rect.y
                    anim["from_a"] = 0
                    anim["to_a"] = 255
                elif kind == "fadeout":
                    anim["from_y"] = base_rect.y
                    anim["to_y"] = offscreen_y
                    anim["from_a"] = 255
                    anim["to_a"] = 0
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

            t = now - anim["start"]
            dur = anim.get("dur") or PANEL_SLIDE_DURATION

            if t <= 0:
                frac = 0.0
            elif t >= dur:
                frac = 1.0
            else:
                frac = t / dur

            y = anim["from_y"] + (anim["to_y"] - anim["from_y"]) * frac

            from_a = anim.get("from_a", 255)
            to_a = anim.get("to_a", 255)

            if from_a == 0 and to_a > 0:
                if frac <= 0.9:
                    alpha = 0
                else:
                    inner = (frac - 0.9) / 0.1
                    if inner < 0.0:
                        inner = 0.0
                    elif inner > 1.0:
                        inner = 1.0
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

        # ------------------------------------------------------------------
        # Hitbox overlay button + slot filter (top-left)
        # ------------------------------------------------------------------
        _check_hitbox_proc()

        HB_BTN_X, HB_BTN_Y = 8, 8
        HB_BTN_W, HB_BTN_H = 130, 22
        hb_btn_rect = pygame.Rect(HB_BTN_X, HB_BTN_Y, HB_BTN_W, HB_BTN_H)

        if hitbox_active:
            hb_btn_col = (60, 200, 80)
            hb_btn_label = "Hitboxes: ON"
        else:
            hb_btn_col = (80, 80, 80)
            hb_btn_label = "Activate Hitboxes"

        mx_h, my_h = pygame.mouse.get_pos()
        hb_hover = hb_btn_rect.collidepoint(mx_h, my_h)
        if hb_hover:
            hb_btn_col = tuple(min(255, c + 30) for c in hb_btn_col)

        pygame.draw.rect(screen, hb_btn_col, hb_btn_rect, border_radius=3)
        pygame.draw.rect(screen, (200, 200, 200), hb_btn_rect, 1, border_radius=3)
        screen.blit(smallfont.render(hb_btn_label, True, (230, 230, 230)),
                    (HB_BTN_X + 6, HB_BTN_Y + 4))
        hb_filter_rects = {}
        # Slot filter checkboxes (only shown when active)
        fx = HB_BTN_X
        fy = HB_BTN_Y + HB_BTN_H + 4
        gap = 10

        slot_colors = {
            "P1": (255, 100, 100),
            "P2": (100, 160, 255),
            "P3": (255, 100, 200),
            "P4": (100, 255, 140),
        }

        for slot_name in ("P1", "P2", "P3", "P4"):
            col = slot_colors[slot_name]

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

            fx += total_w + gap
        # ------------------------------------------------------------------
        r_p1c1, a_p1c1 = anim_rect_and_alpha("P1-C1", layout["p1c1"])
        r_p2c1, a_p2c1 = anim_rect_and_alpha("P2-C1", layout["p2c1"])
        r_p1c2, a_p1c2 = anim_rect_and_alpha("P1-C2", layout["p1c2"])
        r_p2c2, a_p2c2 = anim_rect_and_alpha("P2-C2", layout["p2c2"])

        def blit_panel_with_button(panel_rect, slot_label, alpha, header):
            snap = render_snap_by_slot.get(slot_label)
            portrait = render_portrait_by_slot.get(slot_label, placeholder_portrait)

            surf = pygame.Surface((panel_rect.width, panel_rect.height), pygame.SRCALPHA)
            draw_panel_classic(
                surf,
                surf.get_rect(),
                snap,
                portrait,
                font,
                smallfont,
                header,
                t_ms,
            )

            btn_w, btn_h = 110, 20
            btn_x = panel_rect.width - btn_w - 6
            btn_y = panel_rect.height - btn_h - 6
            btn_rect_local = pygame.Rect(btn_x, btn_y, btn_w, btn_h)
            # Hover detection (mouse is in screen coords; convert to panel-local coords)
            mx, my = pygame.mouse.get_pos()
            mx_local = mx - panel_rect.x
            my_local = my - panel_rect.y
            is_hover = btn_rect_local.collidepoint(mx_local, my_local)

            flash_left = panel_btn_flash.get(slot_label, 0)

            if flash_left > 0:
                # clicked/flash state
                base_col = (90, 140, 255)
                border_col = (255, 255, 255)
            elif is_hover:
                # hover state (slightly brighter so it reads as interactive)
                base_col = (55, 55, 55)
                border_col = (220, 220, 220)
            else:
                # normal
                base_col = (40, 40, 40)
                border_col = (180, 180, 180)


            pygame.draw.rect(surf, base_col, btn_rect_local, border_radius=3)
            pygame.draw.rect(surf, border_col, btn_rect_local, 1, border_radius=3)
            label_surf = smallfont.render("Frame Data", True, (220, 220, 220))
            surf.blit(label_surf, (btn_x + 6, btn_y + 2))

            if flash_left > 0:
                pygame.draw.rect(
                    surf,
                    (255, 255, 255),
                    btn_rect_local.inflate(4, 4),
                    2,
                    border_radius=4,
                )

            surf.set_alpha(alpha)
            screen.blit(surf, (panel_rect.x, panel_rect.y))

            return pygame.Rect(panel_rect.x + btn_x, panel_rect.y + btn_y, btn_w, btn_h)

        # Always draw C1 panels if present in layout; draw C2 panels unless giant_solo hides them.
        btn_p1c1 = blit_panel_with_button(r_p1c1, "P1-C1", a_p1c1, "P1-C1")
        btn_p2c1 = blit_panel_with_button(r_p2c1, "P2-C1", a_p2c1, "P2-C1")

        if (not layout.get("p1_is_giant")) and ("P1-C2" in snaps):
            btn_p1c2 = blit_panel_with_button(r_p1c2, "P1-C2", a_p1c2, "P1-C2")
        else:
            btn_p1c2 = pygame.Rect(0, 0, 0, 0)

        if (not layout.get("p2_is_giant")) and ("P2-C2" in snaps):
            btn_p2c2 = blit_panel_with_button(r_p2c2, "P2-C2", a_p2c2, "P2-C2")
        else:
            btn_p2c2 = pygame.Rect(0, 0, 0, 0)

        draw_activity(screen, layout["act"], font, last_adv_display)
        draw_event_log(screen, layout["events"], font, smallfont)

        debug_rect = layout["debug"]
        if frame_idx % DEBUG_REFRESH_EVERY == 0:
            debug_cache = merged_debug_values()

        dbg_values = debug_cache
        debug_click_areas, debug_max_scroll = draw_debug_overlay(
            screen, debug_rect, smallfont, dbg_values, debug_scroll_offset
        )

        scan_rect = layout["scan"]
        scan_surf = pygame.Surface((scan_rect.width, scan_rect.height), pygame.SRCALPHA)
        draw_scan_normals(scan_surf, scan_surf.get_rect(), font, smallfont, last_scan_normals)

        if scan_anim is not None:
            t = now - scan_anim["start"]
            dur = scan_anim.get("dur", SCAN_SLIDE_DURATION)
            if t <= 0:
                frac = 0.0
            elif t >= dur:
                frac = 1.0
            else:
                frac = t / dur

            from_y = scan_rect.y + scan_rect.height + 8
            to_y = scan_rect.y
            y = from_y + (to_y - from_y) * frac

            if frac >= 1.0:
                scan_anim = None
        else:
            y = scan_rect.y

        scan_surf.set_alpha(255)
        screen.blit(scan_surf, (scan_rect.x, int(y)))

        pygame.display.flip()

        # -----------------------------
        # Click handling
        # -----------------------------
        if mouse_clicked_pos is not None:
            mx, my = mouse_clicked_pos

            # Hitbox button
            if hb_btn_rect.collidepoint(mx, my):
                if hitbox_active:
                    _stop_hitbox_overlay()
                else:
                    _launch_hitbox_overlay()
                mouse_clicked_pos = None
                continue

            # Hitbox slot filter checkboxes
            elif hitbox_active:
                for slot_name, cb_rect in hb_filter_rects.items():
                    if cb_rect.collidepoint(mx, my):
                        hitbox_slots[slot_name] = not hitbox_slots[slot_name]
                        _write_hitbox_filter()
                        break

            # Debug panel rows -> copy address
            copied = False
            for name, (r, addr) in debug_click_areas.items():
                if r.collidepoint(mx, my):
                    if isinstance(addr, int):
                        _copy_to_clipboard(f"0x{addr:08X}")
                    else:
                        _copy_to_clipboard(str(addr))
                    copied = True
                    break

            # Character panels -> copy base
            if not copied:
                slot_panels = [
                    ("P1-C1", r_p1c1),
                    ("P2-C1", r_p2c1),
                    ("P1-C2", r_p1c2),
                    ("P2-C2", r_p2c2),
                ]
                for slot_label, rect in slot_panels:
                    if rect and rect.collidepoint(mx, my):
                        snap = render_snap_by_slot.get(slot_label)
                        if snap:
                            base = snap.get("base")
                            if isinstance(base, int):
                                _copy_to_clipboard(f"0x{base:08X}")
                            else:
                                _copy_to_clipboard(str(base))
                        break

            # Frame data buttons
            if btn_p1c1.collidepoint(mx, my):
                last_scan_normals, last_scan_time = ensure_scan_now(last_scan_normals, last_scan_time)
                if last_scan_normals:
                    open_frame_data_window("P1-C1", last_scan_normals)
                panel_btn_flash["P1-C1"] = PANEL_FLASH_FRAMES

            elif btn_p2c1.collidepoint(mx, my):
                last_scan_normals, last_scan_time = ensure_scan_now(last_scan_normals, last_scan_time)
                if last_scan_normals:
                    open_frame_data_window("P2-C1", last_scan_normals)
                panel_btn_flash["P2-C1"] = PANEL_FLASH_FRAMES

            elif btn_p1c2.collidepoint(mx, my):
                last_scan_normals, last_scan_time = ensure_scan_now(last_scan_normals, last_scan_time)
                if last_scan_normals:
                    open_frame_data_window("P1-C2", last_scan_normals)
                panel_btn_flash["P1-C2"] = PANEL_FLASH_FRAMES

            elif btn_p2c2.collidepoint(mx, my):
                last_scan_normals, last_scan_time = ensure_scan_now(last_scan_normals, last_scan_time)
                if last_scan_normals:
                    open_frame_data_window("P2-C2", last_scan_normals)
                panel_btn_flash["P2-C2"] = PANEL_FLASH_FRAMES

            else:
                # Debug click areas -> toggles/cycles
                # Each entry is (rect, addr)
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

                # Pause overlay
                _toggle_u8("PauseOverlay")

                # TrPause
                entry = debug_click_areas.get("TrPause")
                if entry:
                    r, addr_tr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr_tr) or 0
                        wd8(addr_tr, 0x01 if cur == 0x00 else 0x00)

                # P2Pause special coupling
                entry = debug_click_areas.get("P2Pause")
                if entry:
                    r, addr_p2 = entry
                    if r.collidepoint(mx, my):
                        cur_p2 = rd8(addr_p2) or 0
                        entry_tr = debug_click_areas.get("TrPause")
                        addr_tr = entry_tr[1] if entry_tr else None
                        if cur_p2 == 0x00:
                            if addr_tr is not None:
                                wd8(addr_tr, 0x01)
                            wd8(addr_p2, 0x01)
                            print("[P2Pause] TrPause=01, P2Pause=01")
                        else:
                            if addr_tr is not None:
                                wd8(addr_tr, 0x00)
                            wd8(addr_p2, 0x00)
                            print("[P2Pause] TrPause=00, P2Pause=00")

                _cycle_u8("DummyMeter", 3)
                _cycle_u8("CpuAction", 6)
                _cycle_u8("CpuGuard", 3)

                _toggle_u8("CpuPushblock")
                _toggle_u8("CameraLock")
                _toggle_u8("CpuThrowTech")
                _cycle_u8("P1Meter", 3)
                _toggle_u8("P1Life")
                _toggle_u8("FreeBaroque")
                _toggle_u8("Orientation")

                # SuperBG special (01 <-> 04)
                entry = debug_click_areas.get("SuperBG")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr)
                        wd8(addr, 0x01 if cur == 0x04 else 0x04)

                # BaroquePct (0..0A then wrap)
                entry = debug_click_areas.get("BaroquePct")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        wd8(addr, (cur + 1) if cur < 0x0A else 0x00)

                _toggle_u8("AttackData")
                _toggle_u8("InputDisplay")

                # CpuDifficulty stored in steps of 0x20, 0..7
                entry = debug_click_areas.get("CpuDifficulty")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        level = (cur // 0x20) % 8
                        level = (level + 1) % 8
                        wd8(addr, level * 0x20)

                # DamageOutput cycle 0..3
                entry = debug_click_areas.get("DamageOutput")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        wd8(addr, (cur + 1) & 0x03)

                # HypeTrigger momentary
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
                        hype_restore_ts = now + 0.5

                # ComboStore[1] momentary
                entry = debug_click_areas.get("ComboStore[1]")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        wd8(addr, 0x41)

                _toggle_u8("ComboCountOnly")

                # SpecialPopup momentary
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
                        special_restore_ts = now + 0.5

        # -----------------------------
        # Restore momentary writes
        # -----------------------------
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

        # Trigger normals rescan when team changes
        if HAVE_SCAN_NORMALS and need_rescan_normals and scan_worker:
            scan_worker.request()
            need_rescan_normals = False

        # Manual F5 scan
        if HAVE_SCAN_NORMALS and manual_scan_requested:
            if scan_worker:
                scan_worker.request()
            else:
                try:
                    last_scan_normals = scan_normals_all.scan_once()
                    last_scan_time = time.time()
                except Exception as e:
                    print("manual scan failed:", e)
            manual_scan_requested = False

        # CSV flush placeholder (kept as-is, since logging writes are elsewhere)
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
    
    pygame.quit()


if __name__ == "__main__":
    main()