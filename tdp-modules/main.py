# main.py
#
# Entry point for the TvC live HUD / frame probe.
# This script:
#   - Hooks into Dolphin
#   - Tracks fighters, health, meter, assists, and frame advantage
#   - Spawns the background scan worker for normals
#   - Renders the main HUD, debug overlay, and "Show frame data" buttons

import os
import csv
import time
import threading

import pygame

from layout import compute_layout, reassign_slots_for_giants
from scan_worker import ScanNormalsWorker
from training_flags import read_training_flags
from debug_panel import read_debug_flags, draw_debug_overlay

from dolphin_io import hook, rd8, rd32, wd8, addr_in_ram

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

from constants import (
    SLOTS,
    CHAR_NAMES,
    OFF_MAX_HP,
    OFF_CUR_HP,
    OFF_AUX_HP,
    POSX_OFF,
    OFF_CHAR_ID,
    ATT_ID_OFF_PRIMARY,
    ATT_ID_OFF_SECOND,
    CTRL_WORD_OFF,
    FLAG_062,
    FLAG_063,
    FLAG_072,
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
    # It is fine for this import to fail; HUD still works without deep scanning.
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
SCAN_MIN_INTERVAL_SEC = 180.0

PANEL_SLIDE_DURATION = 2
PANEL_FLASH_FRAMES = 12
SCAN_SLIDE_DURATION = 0.7
# offsets for "real" baroque (relative to fighter base)
HP32_OFF = 0x28
POOL32_OFF = 0x2C


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
        # True while the character is in any assist-related phase
        self.is_assisting = False
        # "flyin", "attack", "recover", or None
        self.phase = None
        # Last seen animation / attack ID (attA)
        self.last_anim = None


# These sets are intentionally left empty.
# They can be overridden by data mining real assist IDs per character.
ASSIST_FLYIN_IDS = set()
ASSIST_ATTACK_IDS = set()

# Slot name -> AssistState
_ASSIST_BY_SLOT = {}


def update_assist_for_snap(slotname: str, snap: dict, cur_anim: int | None):
    """
    Update the assist state for a given slot based on the current snapshot.

    This is deliberately conservative: if the current animation is not in one
    of the known assist sets, the state decays over a couple of frames to
    avoid flickering.
    """
    if not slotname or snap is None:
        return

    state = _ASSIST_BY_SLOT.get(slotname)
    if state is None:
        state = AssistState()
        _ASSIST_BY_SLOT[slotname] = state

    state.last_anim = cur_anim

    # Hard assist classification based on curated sets
    if cur_anim in ASSIST_FLYIN_IDS:
        state.is_assisting = True
        state.phase = "flyin"
    elif cur_anim in ASSIST_ATTACK_IDS:
        state.is_assisting = True
        state.phase = "attack"
    else:
        # Soft decay: after an assist animation, mark "recover" for one step,
        # then return to idle. This gives the HUD a more stable classification.
        if state.is_assisting and state.phase in ("flyin", "attack"):
            state.phase = "recover"
        elif state.phase == "recover":
            state.is_assisting = False
            state.phase = None

    snap["assist_phase"] = state.phase
    snap["is_assist"] = state.is_assisting


def get_assist_state(slotname: str) -> AssistState | None:
    """Return the current AssistState for the given slot, if any."""
    return _ASSIST_BY_SLOT.get(slotname)


def safe_read_fighter(base, yoff):
    """
    Wrapper around read_fighter(base, yoff) with a fallback path for giants.

    Some characters (e.g. giants) do not match the standard fighter struct
    layout used by read_fighter(). If the primary reader fails, we fall back
    to a generic reader that pulls out only the fields we care about.

    Returns:
        A snapshot dict compatible with the rest of the HUD, or None if
        the data looks unreasonable.
    """
    snap = read_fighter(base, yoff)
    if snap:
        return snap

    print(f"[safe_read_fighter] read_fighter failed for base=0x{base:08X}, trying fallback")

    try:
        max_hp = rd32(base + OFF_MAX_HP)
        cur_hp = rd32(base + OFF_CUR_HP)
        aux_hp = rd32(base + OFF_AUX_HP)

        pos_x = rd32(base + POSX_OFF)
        pos_y = rd32(base + yoff)

        attA = rd8(base + ATT_ID_OFF_PRIMARY)
        attB = rd8(base + ATT_ID_OFF_SECOND)

        ctrl = rd32(base + CTRL_WORD_OFF)

        flag062 = rd8(base + FLAG_062)
        flag063 = rd8(base + FLAG_063)
        flag072 = rd8(base + FLAG_072)
    except Exception as e:
        print(f"[safe_read_fighter] Exception reading fields: {e}")
        return None

    print(f"[safe_read_fighter] max_hp={max_hp} cur_hp={cur_hp}")

    # Simple sanity guard. If these fail, we treat the struct as invalid.
    if max_hp <= 30000 or max_hp > 1000000:
        print(f"[safe_read_fighter] HP sanity check failed: max_hp={max_hp}")
        return None

    try:
        char_id = rd32(base + OFF_CHAR_ID)
    except Exception:
        char_id = None

    print(f"[safe_read_fighter] char_id={char_id}, name={CHAR_NAMES.get(char_id)}")

    char_name = "Unknown"
    if char_id is not None and char_id != 0:
        char_name = CHAR_NAMES.get(char_id, f"ID_{char_id}")

    return {
        "max": max_hp,
        "cur": cur_hp,
        "aux": aux_hp,
        "pos_x": pos_x,
        "pos_y": pos_y,
        "attA": attA,
        "attB": attB,
        "ctrl": ctrl,
        "flag062": flag062,
        "flag063": flag063,
        "flag072": flag072,
        "id": char_id,
        "name": char_name,
        # Giants do not use the standard byte pool in the same way
        "hp_pool_byte": None,
    }


def init_pygame():
    """
    Initialize pygame, set up fonts and the main window, and return:
        (screen_surface, main_font, small_font)
    """
    pygame.init()
    try:
        font = pygame.font.SysFont("consolas", FONT_MAIN_SIZE)
    except Exception:
        font = pygame.font.Font(None, FONT_MAIN_SIZE)
    try:
        smallfont = pygame.font.SysFont("consolas", FONT_SMALL_SIZE)
    except Exception:
        smallfont = pygame.font.Font(None, FONT_SMALL_SIZE)

    # The HUD is resizable; layout.compute_layout adapts to the current size.
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.RESIZABLE)
    pygame.display.set_caption("TvC Live HUD / Frame Probe")
    return screen, font, smallfont


def main():
    """
    Main loop for the TvC HUD.

    High-level flow:
        - Hook Dolphin
        - Start background normals scanner (if available)
        - Each frame:
            * Resolve fighter bases
            * Build per-slot snapshots
            * Track assists, meter, baroque, and frame advantage
            * Render HUD panels, debug tools, and scan results
            * Handle mouse / keyboard input, including "Show frame data"
    """
    print("HUD: waiting for Dolphin…")
    hook()
    print("HUD: hooked Dolphin.")

    # Load move label data from the CSV mapping files.
    move_map, global_map = load_move_map(GENERIC_MAPPING_CSV, PAIR_MAPPING_CSV)

    screen, font, smallfont = init_pygame()
    clock = pygame.time.Clock()

    # Portraits are optional but make the HUD easier to read.
    placeholder_portrait = load_portrait_placeholder()
    portraits = load_portraits_from_dir(os.path.join("assets", "portraits"))
    print(f"HUD: loaded {len(portraits)} portraits.")

    # Start the background scan worker if the deep normals scanner is available.
    if HAVE_SCAN_NORMALS and scan_normals_all is not None:
        scan_worker = ScanNormalsWorker(scan_normals_all.scan_once)
        scan_worker.start()
    else:
        scan_worker = None

    last_scan_normals = None
    last_scan_time = 0.0
    scan_anim = None
    # Per-slot / per-base state caches used to smooth behavior over time.
    last_base_by_slot = {}
    y_off_by_base = {}
    prev_hp = {}
    pool_baseline = {}

    last_move_anim_id = {}
    last_char_by_slot = {}
    render_snap_by_slot = {}
    render_portrait_by_slot = {}

    # Panel animation and "Show frame data" affordances.
    panel_anim = {}
    anim_queue_after_scan = set()
    panel_btn_flash = {s: 0 for (s, _, _) in SLOTS}

    # Hit tracking: local red scan for P1 only and a global scanner.
    local_scan = RedHealthScanner()
    global_scan = GlobalRedScanner()

    manual_scan_requested = False
    need_rescan_normals = False

    last_adv_display = ""
    pending_hits = []
    frame_idx = 0
    running = True

    # Debug overlay state (per-frame overlay + scrollable list).
    
    debug_overlay = True
    debug_btn_rect = pygame.Rect(0, 0, 0, 0) 
    debug_click_areas = {}
    debug_scroll_offset = 0
    debug_max_scroll = 0

    # Temporary writebacks for hype and special popup toggles.
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

        # Basic event pump: resize, quit, F5 for manual scan, mouse wheel for debug.
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
                elif ev.button in (4, 5) and debug_overlay:
                    # Legacy wheel events on some platforms.
                    if ev.button == 4 and debug_scroll_offset > 0:
                        debug_scroll_offset -= 1
                    elif ev.button == 5 and debug_scroll_offset < debug_max_scroll:
                        debug_scroll_offset += 1
            elif ev.type == pygame.MOUSEWHEEL and debug_overlay:
                # Newer wheel events (pygame 2).
                if ev.y > 0 and debug_scroll_offset > 0:
                    debug_scroll_offset -= 1
                elif ev.y < 0 and debug_scroll_offset < debug_max_scroll:
                    debug_scroll_offset += 1

        # Pull the latest scan results from the background worker, if any.
        if scan_worker:
            res, ts = scan_worker.get_latest()
            if res is not None and ts > last_scan_time:
                last_scan_normals = res
                last_scan_time = ts

                # Kick off a slide-in animation for the scan panel.
                scan_anim = {
                    "start": now,
                    "dur": SCAN_SLIDE_DURATION,
                }

        # Resolve base addresses for each character slot using the resolver.
        resolved_slots = []
        for slotname, ptr_addr, teamtag in SLOTS:
            # Read the fighter base directly from the slot pointer
            raw_base = rd32(ptr_addr)

            if raw_base is None or not addr_in_ram(raw_base):
                base = None
            else:
                base = raw_base

            # Detect changes the same way as before
            changed = base is not None and last_base_by_slot.get(ptr_addr) != base

            if base and changed:
                last_base_by_slot[ptr_addr] = base
                # Reset cached meter reads when bases move.
                METER_CACHE.drop(base)
                # Recompute Y offset heuristics when a new base appears.
                y_off_by_base[base] = pick_posy_off_no_jump(base)

            resolved_slots.append((slotname, teamtag, base))


        # Read meter using P1/P2 point characters as canonical.
        p1c1_base = next((b for n, t, b in resolved_slots if n == "P1-C1" and b), None)
        p2c1_base = next((b for n, t, b in resolved_slots if n == "P2-C1" and b), None)
        meter_p1 = read_meter(p1c1_base)
        meter_p2 = read_meter(p2c1_base)

        # Build per-slot snapshots that the HUD and logging code consume.
        snaps = {}
        for slotname, teamtag, base in resolved_slots:
            if not base:
                # Slot was previously occupied but now empty: fade the panel out
                # and trigger a rescan so normals reflect the new team.
                if last_char_by_slot.get(slotname):
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

            # Some characters report a different ID via the generic reader.
            # We override with the "true" ID where possible.
            try:
                true_id = rd32(base + OFF_CHAR_ID)
            except Exception:
                true_id = None

            if true_id not in (None, 0):
                snap["id"] = true_id
                name_from_id = CHAR_NAMES.get(true_id)
                
                if name_from_id:
                    snap["name"] = name_from_id

            # Meter text only matters on the point characters.
            if slotname == "P1-C1":
                snap["meter_str"] = str(meter_p1) if meter_p1 is not None else "--"
            elif slotname == "P2-C1":
                snap["meter_str"] = str(meter_p2) if meter_p2 is not None else "--"
            else:
                snap["meter_str"] = "--"

            # Current animation IDs are used for move labeling and assist tracking.
            cur_anim = snap.get("attA") or snap.get("attB")

            assist_phase = None
            is_assist = False

            # Temporary hardcoded assist move for known cases.
            if cur_anim == 268:  # 0x010C
                is_assist = True
                assist_phase = "attack"

            mv_name_lower = (snap.get("mv_label") or "").lower()
            if "assist standby" in mv_name_lower:
                assist_phase = "standby"

            snap["assist_phase"] = assist_phase
            snap["is_assist"] = is_assist

            # Apply the generic assist state machine on top of the above hints.
            update_assist_for_snap(slotname, snap, cur_anim)

            # Resolve move label using per-character mapping and global fallback.
            char_name = snap.get("name")
            csv_char_id = CHAR_ID_CORRECTION.get(char_name, snap.get("id"))

            mv_label = lookup_move_name(cur_anim, csv_char_id)
            if not mv_label:
                mv_label = move_label_for(cur_anim, csv_char_id, move_map, global_map)

            snap["mv_label"] = mv_label
            snap["mv_id_display"] = cur_anim
            snap["csv_char_id"] = csv_char_id

            # Track last animation ID per base. This allows basic change detection
            # if needed later.
            prev_anim = last_move_anim_id.get(base)
            if cur_anim and cur_anim != prev_anim:
                last_move_anim_id[base] = cur_anim
            else:
                last_move_anim_id[base] = cur_anim

            # Baroque "pool" is tracked in hp_pool_byte, but we also maintain
            # a per-base baseline for relative percentage.
            pool_byte = snap.get("hp_pool_byte")
            if pool_byte is not None:
                prev_max = pool_baseline.get(base, 0)
                if pool_byte > prev_max:
                    pool_baseline[base] = pool_byte
                max_pool = pool_baseline.get(base, 1)
                snap["pool_pct"] = (pool_byte / max_pool) * 100.0 if max_pool else 0.0
            else:
                snap["pool_pct"] = 0.0

            # Local 32-bit baroque health values for more accurate tracking.
            max_hp_stat = snap.get("max") or 0
            hp32 = rd32(base + HP32_OFF) or 0
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

            # Input monitoring is only wired up for P1 in this HUD.
            if slotname == "P1-C1":
                inputs_struct = {}
                for key, addr in INPUT_MONITOR_ADDRS.items():
                    v = rd8(addr)
                    inputs_struct[key] = 0 if v is None else v
                snap["inputs"] = inputs_struct
            else:
                snap["inputs"] = {}

            snaps[slotname] = snap

            # When a character changes in a slot, animate the panel and
            # request a normals rescan to rebuild move tables for the new team.
            if last_char_by_slot.get(slotname) != snap.get("name"):
                last_char_by_slot[slotname] = snap.get("name")
                anim_queue_after_scan.add((slotname, "fadein"))
                need_rescan_normals = True

            # Store what we actually render; these are allowed to lag slightly
            # behind the raw snapshots to decouple animation from raw data.
            render_snap_by_slot[slotname] = snap
            render_portrait_by_slot[slotname] = get_portrait_for_snap(
                snap, portraits, placeholder_portrait
            )

        # Giants occupy both character panels; we reshuffle the mapping to
        # keep the HUD consistent.
        snaps = reassign_slots_for_giants(snaps)
        if frame_idx < 120:  # first 2 seconds at 60 FPS
            for slotname, teamtag, base in resolved_slots:
                if base:
                    print(f"[slots] {slotname}: base=0x{base:08X}")
                else:
                    print(f"[slots] {slotname}: (none)")
        # -------------------------------------------------------------------
        # Damage / hit logging and frame advantage tracking
        # -------------------------------------------------------------------
        if frame_idx % DAMAGE_EVERY_FRAMES == 0:
            # These animation IDs represent "being hit" / hitstun states.
            REACTION_STATES = {48, 64, 65, 66, 73, 80, 81, 82, 90, 92, 95, 96, 97}
            for vic_slot, vic_snap in snaps.items():
                vic_move_id = vic_snap.get("attA") or vic_snap.get("attB")
                if vic_move_id not in REACTION_STATES:
                    continue
                vic_team = vic_snap["teamtag"]
                attackers = [s for s in snaps.values() if s["teamtag"] != vic_team]
                if not attackers:
                    continue

                # Pick the closest opponent as the attacker candidate.
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

                # Feed into the frame advantage tracker.
                ADV_TRACK.start_contact(
                    atk_snap["base"],
                    vic_snap["base"],
                    frame_idx,
                    atk_move_id,
                    vic_move_id,
                )

                # Compute raw damage from the victim's HP delta.
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
            # Evaluate frame advantage for all ordered attacker/victim pairs.
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
                # Guard against obviously bogus values.
                if abs(plusf) <= 64:
                    atk_slot = next((s for s in snaps.values() if s["base"] == atk_b), None)
                    vic_slot = next((s for s in snaps.values() if s["base"] == vic_b), None)
                    if atk_slot and vic_slot:
                        last_adv_display = (
                            f"{atk_slot['slotname']}({atk_slot['name']}) vs "
                            f"{vic_slot['slotname']}({vic_slot['name']}): "
                            f"{plusf:+.1f}f"
                        )
                        log_frame_advantage(atk_slot, vic_slot, plusf)
                    else:
                        last_adv_display = f"Frame adv: {plusf:+.1f}f"

        # -------------------------------------------------------------------
        # Rendering
        # -------------------------------------------------------------------
        screen.fill(COL_BG)
        w, h = screen.get_size()
        layout = compute_layout(w, h, snaps)
                # Consume any queued panel animations now that we know the layout.
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
                    # If we don't have a rect for this slot, just drop the request.
                    anim_queue_after_scan.discard((slot_label, kind))
                    continue

                panel_height = base_rect.height
                offscreen_y = -panel_height - 8  # start just above the top

                anim = {
                    "start": now,
                    "dur": PANEL_SLIDE_DURATION,
                    "from_y": None,
                    "to_y": None,
                    "from_a": 255,
                    "to_a": 255,
                }

                if kind == "fadein":
                    # Slide in from the top, fully transparent at first.
                    anim["from_y"] = offscreen_y
                    anim["to_y"] = base_rect.y
                    anim["from_a"] = 0
                    anim["to_a"] = 255
                elif kind == "fadeout":
                    # Optional: slide back up and fade out when a slot empties.
                    anim["from_y"] = base_rect.y
                    anim["to_y"] = offscreen_y
                    anim["from_a"] = 255
                    anim["to_a"] = 0
                else:
                    anim_queue_after_scan.discard((slot_label, kind))
                    continue

                panel_anim[slot_label] = anim
                anim_queue_after_scan.discard((slot_label, kind))

        # Small helper to apply slide/alpha animation on panels.
        # Small helper to apply slide/alpha animation on panels.
        def anim_rect_and_alpha(slot_label, base_rect):
            anim = panel_anim.get(slot_label)
            if not anim:
                # No animation: use the layout rect and full opacity.
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

            # Slide along the full duration.
            y = anim["from_y"] + (anim["to_y"] - anim["from_y"]) * frac

            from_a = anim.get("from_a", 255)
            to_a = anim.get("to_a", 255)

            # 90/10 split for fade-ins: fully transparent for the first 90%
            # of the slide, then fade in quickly over the last 10%.
            if from_a == 0 and to_a > 0:
                if frac <= 0.9:
                    alpha = 0
                else:
                    inner = (frac - 0.9) / 0.1  # 0..1 over the last 10%
                    if inner < 0.0:
                        inner = 0.0
                    elif inner > 1.0:
                        inner = 1.0
                    alpha = int(from_a + (to_a - from_a) * inner)
            else:
                # Default: linear alpha over the whole duration.
                alpha = int(from_a + (to_a - from_a) * frac)

            # When the animation finishes, clean up the state and, in the case
            # of fade-outs, drop the render snapshot entirely.
            if frac >= 1.0:
                if to_a == 0:
                    render_snap_by_slot.pop(slot_label, None)
                    render_portrait_by_slot.pop(slot_label, None)
                panel_anim.pop(slot_label, None)

            r = base_rect.copy()
            r.y = int(y)
            return r, max(0, min(255, alpha))

        r_p1c1, a_p1c1 = anim_rect_and_alpha("P1-C1", layout["p1c1"])
        r_p2c1, a_p2c1 = anim_rect_and_alpha("P2-C1", layout["p2c1"])
        r_p1c2, a_p1c2 = anim_rect_and_alpha("P1-C2", layout["p1c2"])
        r_p2c2, a_p2c2 = anim_rect_and_alpha("P2-C2", layout["p2c2"])

        def blit_panel_with_button(panel_rect, slot_label, alpha, header):
            """
            Draw a character panel (portrait + stats) and its "Show frame data"
            button into an offscreen surface, then blit it into the main screen.

            Returns:
                The absolute Rect of the button on the main screen.
            """
            snap = render_snap_by_slot.get(slot_label)
            portrait = render_portrait_by_slot.get(slot_label, placeholder_portrait)
            waiting = any(
                (slot_label == s and kind in ("fadein", "fadeout"))
                for (s, kind) in anim_queue_after_scan
            )

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

            # Button positioning is relative to the panel rect.
            btn_w, btn_h = 110, 20
            btn_x = panel_rect.width - btn_w - 6
            btn_y = panel_rect.height - btn_h - 6
            btn_rect_local = pygame.Rect(btn_x, btn_y, btn_w, btn_h)

            flash_left = panel_btn_flash.get(slot_label, 0)
            if flash_left > 0:
                base_col = (90, 140, 255)
                border_col = (255, 255, 255)
            else:
                base_col = (40, 40, 40)
                border_col = (180, 180, 180)

            pygame.draw.rect(surf, base_col, btn_rect_local, border_radius=3)
            pygame.draw.rect(surf, border_col, btn_rect_local, 1, border_radius=3)
            label_surf = smallfont.render("Show frame data", True, (220, 220, 220))
            surf.blit(label_surf, (btn_x + 6, btn_y + 2))

            if flash_left > 0:
                pygame.draw.rect(
                    surf,
                    (255, 255, 255),
                    btn_rect_local.inflate(4, 4),
                    2,
                    border_radius=4,
                )

            # Use the animated alpha value for the whole panel surface.
            surf.set_alpha(alpha)
            screen.blit(surf, (panel_rect.x, panel_rect.y))


            # Return the absolute screen-space rect for click tests.
            return pygame.Rect(
                panel_rect.x + btn_x, panel_rect.y + btn_y, btn_w, btn_h
            )

        # Panels for each slot, with their own button hitboxes exposed.
        btn_p1c1 = blit_panel_with_button(r_p1c1, "P1-C1", a_p1c1, "P1-C1")
        btn_p2c1 = blit_panel_with_button(r_p2c1, "P2-C1", a_p2c1, "P2-C1")

        if not layout.get("p1_is_giant") and "P1-C2" in snaps:
            btn_p1c2 = blit_panel_with_button(r_p1c2, "P1-C2", a_p1c2, "P1-C2")
        else:
            btn_p1c2 = pygame.Rect(0, 0, 0, 0)

        if not layout.get("p2_is_giant") and "P2-C2" in snaps:
            btn_p2c2 = blit_panel_with_button(r_p2c2, "P2-C2", a_p2c2, "P2-C2")
        else:
            btn_p2c2 = pygame.Rect(0, 0, 0, 0)

        # Activity strip + event log.
        draw_activity(screen, layout["act"], font, last_adv_display)
        draw_event_log(screen, layout["events"], font, smallfont)

        # Debug overlay region (always on now).
        debug_rect = layout["debug"]

        dbg_values = read_debug_flags() + read_training_flags()

        debug_click_areas, debug_max_scroll = draw_debug_overlay(
            screen, debug_rect, smallfont, dbg_values, debug_scroll_offset
        )



        # ---- scan panel at the bottom: NORMALS ONLY (animated slide-in) ----
        scan_rect = layout["scan"]

        # Render into an offscreen surface first.
        scan_surf = pygame.Surface(
            (scan_rect.width, scan_rect.height), pygame.SRCALPHA
        )
        # Note: use scan_surf.get_rect() so draw_scan_normals can treat (0,0) as origin.
        draw_scan_normals(scan_surf, scan_surf.get_rect(), font, smallfont, last_scan_normals)

        # Animate the whole block sliding up from below with a 90/10 ghost fade.
                # Animate the whole block sliding up from below (no heavy fade).
        if scan_anim is not None:
            t = now - scan_anim["start"]
            dur = scan_anim.get("dur", SCAN_SLIDE_DURATION)

            if t <= 0:
                frac = 0.0
            elif t >= dur:
                frac = 1.0
            else:
                frac = t / dur

            from_y = scan_rect.y + scan_rect.height + 8  # start just below
            to_y = scan_rect.y
            y = from_y + (to_y - from_y) * frac

            # Keep it fully visible while sliding.
            alpha = 255

            if frac >= 1.0:
                scan_anim = None
        else:
            y = scan_rect.y
            alpha = 255

        scan_surf.set_alpha(alpha)
        screen.blit(scan_surf, (scan_rect.x, int(y)))


        pygame.display.flip()

        # -------------------------------------------------------------------
        # Click handling
        # -------------------------------------------------------------------
        if mouse_clicked_pos is not None:
            mx, my = mouse_clicked_pos

            def ensure_scan_now():
                """
                Make sure we have at least one set of normals scan data.

                If the background worker has not produced anything yet,
                run a synchronous scan as a fallback.
                """
                nonlocal last_scan_normals, last_scan_time
                if last_scan_normals is not None:
                    return last_scan_normals
                if HAVE_SCAN_NORMALS:
                    try:
                        last_scan_normals = scan_normals_all.scan_once()
                        last_scan_time = time.time()
                        return last_scan_normals
                    except Exception as e:
                        print("sync scan failed:", e)
                        return None
                return None

            # Frame data buttons
            if btn_p1c1.collidepoint(mx, my):
                data = ensure_scan_now()
                if data:
                    open_frame_data_window("P1-C1", data)
                panel_btn_flash["P1-C1"] = PANEL_FLASH_FRAMES

            elif btn_p2c1.collidepoint(mx, my):
                data = ensure_scan_now()
                if data:
                    open_frame_data_window("P2-C1", data)
                panel_btn_flash["P2-C1"] = PANEL_FLASH_FRAMES

            elif btn_p1c2.collidepoint(mx, my):
                data = ensure_scan_now()
                if data:
                    open_frame_data_window("P1-C2", data)
                panel_btn_flash["P1-C2"] = PANEL_FLASH_FRAMES

            elif btn_p2c2.collidepoint(mx, my):
                data = ensure_scan_now()
                if data:
                    open_frame_data_window("P2-C2", data)
                panel_btn_flash["P2-C2"] = PANEL_FLASH_FRAMES

            else:
                # All other debug click areas are keyed by name in debug_click_areas.
                # Each entry is (rect, addr). When clicked, we toggle or cycle the
                # underlying training/debug flag in memory.
                entry = debug_click_areas.get("PauseOverlay")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        wd8(addr, 0x01 if cur == 0x00 else 0x00)

                entry = debug_click_areas.get("TrPause")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = 0x01 if cur == 0x00 else 0x00
                        wd8(addr, new)

                entry = debug_click_areas.get("DummyMeter")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = (cur + 1) % 3
                        wd8(addr, new)

                entry = debug_click_areas.get("CpuAction")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = (cur + 1) % 6
                        wd8(addr, new)

                entry = debug_click_areas.get("CpuGuard")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = (cur + 1) % 3
                        wd8(addr, new)

                entry = debug_click_areas.get("CpuPushblock")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = 0x01 if cur == 0x00 else 0x00
                        wd8(addr, new)

                entry = debug_click_areas.get("CpuThrowTech")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = 0x01 if cur == 0x00 else 0x00
                        wd8(addr, new)

                entry = debug_click_areas.get("P1Meter")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = (cur + 1) % 3
                        wd8(addr, new)

                entry = debug_click_areas.get("P1Life")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = 0x01 if cur == 0x00 else 0x00
                        wd8(addr, new)

                entry = debug_click_areas.get("FreeBaroque")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = 0x01 if cur == 0x00 else 0x00
                        wd8(addr, new)

                entry = debug_click_areas.get("BaroquePct")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        if cur < 0x0A:
                            new = cur + 1
                        else:
                            new = 0x00
                        wd8(addr, new)

                entry = debug_click_areas.get("AttackData")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = 0x01 if cur == 0x00 else 0x00
                        wd8(addr, new)

                entry = debug_click_areas.get("InputDisplay")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = 0x01 if cur == 0x00 else 0x00
                        wd8(addr, new)

                entry = debug_click_areas.get("CpuDifficulty")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        level = (cur // 0x20) % 8
                        level = (level + 1) % 8
                        new = level * 0x20
                        wd8(addr, new)

                entry = debug_click_areas.get("DamageOutput")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = (cur + 1) & 0x03
                        wd8(addr, new)

                entry = debug_click_areas.get("HypeTrigger")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        orig = rd8(addr)
                        if orig is None or orig == 0:
                            orig = 0x45
                        # Temporarily force the hype trigger, then restore later.
                        wd8(addr, 0x40)
                        hype_restore_addr = addr
                        hype_restore_orig = orig
                        hype_restore_ts = now + 0.5

                entry = debug_click_areas.get("ComboStore[1]")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        wd8(addr, 0x41)

                entry = debug_click_areas.get("SpecialPopup")
                if entry:
                    sp_rect, sp_addr = entry
                    if sp_rect.collidepoint(mx, my):
                        cur = rd8(sp_addr)
                        if cur is None or cur == 0:
                            cur = 0x45
                        special_restore_orig = cur
                        wd8(sp_addr, 0x40)
                        special_restore_addr = sp_addr
                        special_restore_ts = now + 0.5

        # Restore temporarily overridden hype / special values once the
        # timer expires.
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

        # Step down any outstanding button flash counters.
        for k in panel_btn_flash:
            if panel_btn_flash[k] > 0:
                panel_btn_flash[k] -= 1

        # Trigger a background normals rescan whenever the team composition
        # changes or the periodic timer expires.
        if HAVE_SCAN_NORMALS and need_rescan_normals and scan_worker:
            scan_worker.request()
            need_rescan_normals = False

        if HAVE_SCAN_NORMALS and manual_scan_requested:
            if scan_worker:
                scan_worker.request()
            else:
                # Synchronous fallback when the worker is not in use.
                try:
                    last_scan_normals = scan_normals_all.scan_once()
                    last_scan_time = time.time()
                except Exception as e:
                    print("manual scan failed:", e)
            manual_scan_requested = False
        elif HAVE_SCAN_NORMALS and scan_worker:
            if time.time() - last_scan_time >= SCAN_MIN_INTERVAL_SEC:
                scan_worker.request()

        # Flush any pending hit log entries to CSV every so often.
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

        # Frame limiting and loop bookkeeping.
        clock.tick(TARGET_FPS)
        frame_idx += 1

    pygame.quit()


if __name__ == "__main__":
    main()
