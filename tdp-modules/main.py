import time
import os
import csv
import pygame

from dolphin_io import hook, rd8, rbytes
from config import (
    INTERVAL, MIN_HIT_DAMAGE,
    SCREEN_W, SCREEN_H,
    PANEL_W, PANEL_H,
    ROW1_Y, ROW2_Y,
    STACK_TOP_Y, ACTIVITY_H,
    LOG_H, INSP_H,
    FONT_MAIN_SIZE, FONT_SMALL_SIZE,
    HIT_CSV,
    GENERIC_MAPPING_CSV,
    PAIR_MAPPING_CSV,
    COL_BG,
    # baroque / input watch
    BAROQUE_STATUS_ADDR_MAIN,
    BAROQUE_STATUS_ADDR_BUDDY,
    BAROQUE_FLAG_ADDR_0,
    BAROQUE_FLAG_ADDR_1,
    INPUT_MONITOR_ADDRS,
    BAROQUE_MONITOR_ADDR,
    BAROQUE_MONITOR_SIZE,
)
from constants import SLOTS
from resolver import RESOLVER, pick_posy_off_no_jump
from meter import read_meter, METER_CACHE
from fighter import read_fighter, dist2
from advantage import ADV_TRACK
from events import log_hit_line, log_frame_advantage
from moves import (
    load_move_map,
    move_label_for,
    CHAR_ID_CORRECTION,
)
from hud_draw import (
    draw_panel_classic,
    draw_activity,
    draw_event_log,
    draw_inspector,
)
from redscan import RedHealthScanner
from global_redscan import GlobalRedScanner


def main():
    print("GUI HUD: waiting for Dolphin…")
    hook()
    print("GUI HUD: hooked Dolphin.")

    # load move ID -> label mapping for nice display
    move_map, global_map = load_move_map(
        GENERIC_MAPPING_CSV,
        PAIR_MAPPING_CSV
    )

    pygame.init()
    try:
        font      = pygame.font.SysFont("consolas", FONT_MAIN_SIZE)
        smallfont = pygame.font.SysFont("consolas", FONT_SMALL_SIZE)
    except Exception:
        # Fallback if Consolas isn't found
        font      = pygame.font.Font(None, FONT_MAIN_SIZE)
        smallfont = pygame.font.Font(None, FONT_SMALL_SIZE)

    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    pygame.display.set_caption("TvC Live HUD / Frame Probe")
    clock = pygame.time.Clock()

    # state tracked between frames
    last_base_by_slot = {}   # ptr_addr -> last base we resolved
    y_off_by_base     = {}   # fighter base -> vertical offset guess
    prev_hp           = {}   # fighter base -> previous HP to detect hits
    pool_baseline     = {}   # fighter base -> max observed pool byte

    frame_idx = 0
    running   = True

    last_adv_display    = ""
    last_adv_frame_seen = -1

    # scanners
    local_scan  = RedHealthScanner()   # per-character local offsets scan
    global_scan = GlobalRedScanner()   # global memory walker

    while running:
        frame_start = time.time()

        snapshot_p1c1_local  = False
        run_local_analysis   = False
        snapshot_global_full = False
        run_global_analysis  = False

        # poll pygame events (quit / hotkeys)
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_F1:
                    snapshot_p1c1_local = True
                elif ev.key == pygame.K_F2:
                    run_local_analysis = True
                elif ev.key == pygame.K_F3:
                    snapshot_global_full = True
                elif ev.key == pygame.K_F4:
                    run_global_analysis = True

        # -----------------------------
        # Resolve each "slot" (P1-C1, P1-C2, P2-C1, P2-C2)
        # into an actual fighter base pointer via RESOLVER
        # -----------------------------
        resolved_slots = []
        for slotname, ptr_addr, teamtag in SLOTS:
            base, changed = RESOLVER.resolve_base(ptr_addr)

            # if base changed for this ptr_addr, reset caches that assume stable base
            if base and last_base_by_slot.get(ptr_addr) != base:
                last_base_by_slot[ptr_addr] = base
                METER_CACHE.drop(base)
                y_off_by_base[base] = pick_posy_off_no_jump(base)

            resolved_slots.append((slotname, teamtag, base))

        # Grab meter for the on-point characters (C1s). If not found, just None.
        p1c1_base = next((b for n, t, b in resolved_slots if n == "P1-C1" and b), None)
        p2c1_base = next((b for n, t, b in resolved_slots if n == "P2-C1" and b), None)
        meter_p1  = read_meter(p1c1_base)
        meter_p2  = read_meter(p2c1_base)

        # We'll produce a dict of snapshots keyed by slotname for rendering/logging
        snaps = {}

        for slotname, teamtag, base in resolved_slots:
            if not base:
                continue

            yoff_guess = y_off_by_base.get(base, 0xF4)

            snap = read_fighter(base, yoff_guess)
            if not snap:
                continue

            snap["teamtag"]  = teamtag      # "P1" or "P2"
            snap["slotname"] = slotname     # e.g. "P1-C1" / "P1-C2"

            # Move info / animation info
            atk_a     = snap.get("attA")
            atk_b     = snap.get("attB")
            chosen_id = atk_a if atk_a is not None else atk_b

            char_name    = snap.get("name")
            csv_char_id  = CHAR_ID_CORRECTION.get(char_name, snap.get("id"))
            move_label   = move_label_for(chosen_id, csv_char_id, move_map, global_map)

            snap["mv_label"]      = move_label
            snap["mv_id_display"] = chosen_id
            snap["csv_char_id"]   = csv_char_id

            # pooled red-life % (byte 0x02A in fighter struct)
            pool_byte = snap.get("hp_pool_byte")
            if pool_byte is not None:
                prev_max = pool_baseline.get(base, 0)
                if pool_byte > prev_max:
                    pool_baseline[base] = pool_byte
                max_pool    = pool_baseline.get(base, 1)
                pool_pct    = (pool_byte / max_pool) * 100.0 if max_pool else 0.0
            else:
                pool_pct = 0.0
            snap["pool_pct"] = pool_pct

            # -----------------------------
            # Baroque + controller monitor (for P1-C1 only right now)
            # -----------------------------
            if slotname == "P1-C1":
                # Absolute addresses you identified in Dolphin:
                #   0x9246CB9D = main "can baroque" / ticking byte
                #   0x9246CB9C = buddy byte next to it
                #
                # Rule:
                #   if main != 0x00 => baroque_ready = 1
                #   else            => baroque_ready = 0

                buddy_val = rd8(BAROQUE_STATUS_ADDR_BUDDY)
                main_val  = rd8(BAROQUE_STATUS_ADDR_MAIN)

                if buddy_val is None:
                    buddy_val = 0
                if main_val is None:
                    main_val = 0

                ready_flag = (main_val != 0)

                # We *think* CC48/CC50 flip during instant Baroque activation flash.
                f0 = rd8(BAROQUE_FLAG_ADDR_0)
                f1 = rd8(BAROQUE_FLAG_ADDR_1)
                f0 = 0 if f0 is None else f0
                f1 = 0 if f1 is None else f1
                active_flag = (f0 != 0) or (f1 != 0)

                snap["baroque_ready"]      = 1 if ready_flag else 0
                snap["baroque_active"]     = 1 if active_flag else 0

                # We'll show [main, buddy] in the HUD. The first one 'main' is the
                # authoritative gate byte that drives ready:YES/no.
                snap["baroque_ready_raw"]  = (main_val, buddy_val)
                snap["baroque_active_dbg"] = (f0, f1)

                # Controller / button bytes you ID'd (A0/A1/A2)
                inputs_struct = {}
                for key, addr in INPUT_MONITOR_ADDRS.items():
                    v = rd8(addr)
                    inputs_struct[key] = 0 if v is None else v
                snap["inputs"] = inputs_struct

            else:
                # Not P1-C1, zero these out for clarity
                snap["baroque_ready"]      = 0
                snap["baroque_active"]     = 0
                snap["baroque_ready_raw"]  = (0, 0)
                snap["baroque_active_dbg"] = (0, 0)
                snap["inputs"]             = {}

            snaps[slotname] = snap

        # -----------------------------
        # HOTKEY SCANNERS
        # -----------------------------
        if snapshot_p1c1_local:
            targ = snaps.get("P1-C1")
            if targ:
                local_scan.snapshot(targ)
                print("redscan: snapshot captured for P1-C1")
            else:
                print("redscan: P1-C1 not available for snapshot")

        if run_local_analysis:
            local_scan.analyze()

        if snapshot_global_full:
            targ = snaps.get("P1-C1")
            if targ:
                global_scan.snapshot(targ)
            else:
                print("global_redscan: P1-C1 not available (skip snapshot)")

        if run_global_analysis:
            global_scan.analyze()

        # -----------------------------
        # HIT / DAMAGE LOGGING
        # -----------------------------
        for slotname, v_snap in snaps.items():
            base    = v_snap["base"]
            hp_now  = v_snap["cur"]
            hp_prev = prev_hp.get(base, hp_now)
            prev_hp[base] = hp_now

            dmg = hp_prev - hp_now
            if dmg >= MIN_HIT_DAMAGE:
                # victim team / attacker team
                vic_team  = v_snap["teamtag"]
                attackers = [s for s in snaps.values() if s["teamtag"] != vic_team]
                if not attackers:
                    continue

                # pick closest attacker (squared distance)
                best_d2  = None
                atk_snap = None
                for cand in attackers:
                    d2v = dist2(v_snap, cand)
                    if best_d2 is None or d2v < best_d2:
                        best_d2  = d2v
                        atk_snap = cand
                if not atk_snap:
                    continue

                atk_a     = atk_snap.get("attA")
                atk_b     = atk_snap.get("attB")
                chosen_id = atk_a if atk_a is not None else atk_b
                atk_name  = atk_snap.get("name")
                atk_csvid = CHAR_ID_CORRECTION.get(atk_name, atk_snap.get("id"))
                mv_label  = move_label_for(chosen_id, atk_csvid, move_map, global_map)
                atk_hex   = f"0x{chosen_id:X}" if chosen_id is not None else "NONE"

                hit_row = {
                    "t": time.time(),
                    "victim_label": v_snap["slotname"],
                    "victim_char": v_snap["name"],
                    "dmg": dmg,
                    "hp_before": hp_prev,
                    "hp_after": hp_now,
                    "attacker_label": atk_snap["slotname"],
                    "attacker_char": atk_snap["name"],
                    "attacker_id_dec": chosen_id,
                    "attacker_id_hex": atk_hex,
                    "attacker_move": mv_label,
                    "dist2": best_d2 if best_d2 is not None else -1.0,
                }
                log_hit_line(hit_row)

                # let ADV_TRACK know we just "connected" these two
                ADV_TRACK.start_contact(atk_snap["base"], v_snap["base"], frame_idx)

                # write CSV row (collisions.csv)
                newcsv = not os.path.exists(HIT_CSV)
                with open(HIT_CSV, "a", newline="", encoding="utf-8") as fh:
                    w = csv.writer(fh)
                    if newcsv:
                        w.writerow([
                            "t",
                            "victim_label","victim_char","dmg",
                            "hp_before","hp_after",
                            "attacker_label","attacker_char","attacker_char_id",
                            "attacker_id_dec","attacker_id_hex","attacker_move",
                            "dist2",
                            "atk_flag062","atk_flag063",
                            "vic_flag062","vic_flag063",
                            "atk_ctrl","vic_ctrl",
                        ])
                    w.writerow([
                        f"{hit_row['t']:.6f}",
                        hit_row["victim_label"], hit_row["victim_char"],
                        dmg, hp_prev, hp_now,
                        hit_row["attacker_label"], atk_snap["name"],
                        atk_csvid,
                        chosen_id, atk_hex, mv_label,
                        0.0 if best_d2 is None else f"{best_d2:.3f}",
                        atk_snap["f062"], atk_snap["f063"],
                        v_snap["f062"], v_snap["f063"],
                        f"0x{(atk_snap['ctrl'] or 0):08X}",
                        f"0x{(v_snap['ctrl'] or 0):08X}",
                    ])

        # -----------------------------
        # FRAME ADVANTAGE TRACKER
        # -----------------------------
        # We feed pairs of (attacker, victim) into ADV_TRACK to compute/frame-stamp
        # advantage after a hit/block sequence.
        pairs = [
            ("P1-C1","P2-C1"), ("P1-C1","P2-C2"),
            ("P1-C2","P2-C1"), ("P1-C2","P2-C2"),
            ("P2-C1","P1-C1"), ("P2-C1","P1-C2"),
            ("P2-C2","P1-C1"), ("P2-C2","P1-C2"),
        ]
        for atk_slot, vic_slot in pairs:
            atk_snap = snaps.get(atk_slot)
            vic_snap = snaps.get(vic_slot)
            if atk_snap and vic_snap:
                d2_val = dist2(atk_snap, vic_snap)
                ADV_TRACK.update_pair(atk_snap, vic_snap, d2_val, frame_idx)

        freshest_info = ADV_TRACK.get_freshest_final_info()
        if freshest_info:
            atk_b, vic_b, plusf, fin_frame = freshest_info
            atk_slot = next((s for s in snaps.values() if s["base"] == atk_b), None)
            vic_slot = next((s for s in snaps.values() if s["base"] == vic_b), None)

            if fin_frame > last_adv_frame_seen:
                if atk_slot and vic_slot:
                    last_adv_display = (
                        f"{atk_slot['slotname']}({atk_slot['name']}) "
                        f"vs {vic_slot['slotname']}({vic_slot['name']}): "
                        f"{plusf:+.1f}f"
                    )
                else:
                    last_adv_display = f"Frame adv {plusf:+.1f}f"
                last_adv_frame_seen = fin_frame

                log_frame_advantage(atk_slot, vic_slot, plusf)

        adv_line = last_adv_display

        # -----------------------------
        # INSPECTOR BLOB
        # -----------------------------
        # dump controller / misc HUD region (~0x9246CC40...) so you can watch
        # button states, assist bytes, etc live in the right-hand pane
        baroque_blob = rbytes(BAROQUE_MONITOR_ADDR, BAROQUE_MONITOR_SIZE)

        # -----------------------------
        # RENDER HUD
        # -----------------------------
        screen.fill(COL_BG)

        # top row (P1-C1 / P2-C1)
        r_p1c1 = pygame.Rect(10, ROW1_Y, PANEL_W, PANEL_H)
        r_p2c1 = pygame.Rect(10 + PANEL_W + 20, ROW1_Y, PANEL_W, PANEL_H)
        draw_panel_classic(screen, r_p1c1, snaps.get("P1-C1"), meter_p1, font, smallfont, "P1-C1")
        draw_panel_classic(screen, r_p2c1, snaps.get("P2-C1"), meter_p2, font, smallfont, "P2-C1")

        # second row (P1-C2 / P2-C2)
        r_p1c2 = pygame.Rect(10, ROW2_Y, PANEL_W, PANEL_H)
        r_p2c2 = pygame.Rect(10 + PANEL_W + 20, ROW2_Y, PANEL_W, PANEL_H)
        draw_panel_classic(screen, r_p1c2, snaps.get("P1-C2"), None, font, smallfont, "P1-C2")
        draw_panel_classic(screen, r_p2c2, snaps.get("P2-C2"), None, font, smallfont, "P2-C2")

        # frame advantage / status line
        act_rect = pygame.Rect(10, STACK_TOP_Y, SCREEN_W - 20, ACTIVITY_H)
        draw_activity(screen, act_rect, font, adv_line)

        # rolling hit/adv log
        log_rect = pygame.Rect(10, act_rect.bottom + 10, SCREEN_W - 20, LOG_H)
        draw_event_log(screen, log_rect, font, smallfont)

        # live inspector (baroque_blob dump + misc fighter bytes)
        insp_rect = pygame.Rect(10, log_rect.bottom + 10, SCREEN_W - 20, INSP_H)
        draw_inspector(screen, insp_rect, font, smallfont, snaps, baroque_blob)

        pygame.display.flip()

        # -----------------------------
        # pacing / frame advance
        # -----------------------------
        elapsed = time.time() - frame_start
        sleep_left = INTERVAL - elapsed
        if sleep_left > 0:
            time.sleep(sleep_left)

        clock.tick()
        frame_idx += 1

    pygame.quit()


if __name__ == "__main__":
    main()
