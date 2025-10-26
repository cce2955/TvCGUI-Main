import time, os, csv, pygame

from dolphin_io import hook
from config import (
    INTERVAL, MIN_HIT_DAMAGE,
    SCREEN_W, SCREEN_H,
    PANEL_W, PANEL_H,
    ROW1_Y, ROW2_Y,
    STACK_TOP_Y, ACTIVITY_H,
    LOG_H, INSP_H,
    FONT_MAIN_SIZE, FONT_SMALL_SIZE,
    HIT_CSV,
    GENERIC_MAPPING_CSV,  # path to move_id_map_charagnostic.csv
    PAIR_MAPPING_CSV,     # path to move_id_map_charpair.csv
    COL_BG,
)
from constants import SLOTS
from resolver import RESOLVER, pick_posy_off_no_jump
from meter import read_meter, METER_CACHE
from fighter import read_fighter, dist2
from advantage import ADV_TRACK
from events import log_hit_line
from moves import (
    load_move_map,
    move_label_for,
    decode_flag_062,
    decode_flag_063,
    CHAR_ID_CORRECTION,
)
from hud_draw import (
    draw_panel_classic,
    draw_activity,
    draw_event_log,
    draw_inspector,
)


def main():
    print("GUI HUD: waiting for Dolphin…")
    hook()
    print("GUI HUD: hooked Dolphin.")

    # Build maps:
    # move_map[char_id][atk_id] = "Name"
    # global_map[atk_id]        = "Name" for shared/system flags
    move_map, global_map = load_move_map(
        GENERIC_MAPPING_CSV,
        PAIR_MAPPING_CSV
    )

    pygame.init()
    try:
        font      = pygame.font.SysFont("consolas", FONT_MAIN_SIZE)
        smallfont = pygame.font.SysFont("consolas", FONT_SMALL_SIZE)
    except Exception:
        font      = pygame.font.Font(None, FONT_MAIN_SIZE)
        smallfont = pygame.font.Font(None, FONT_SMALL_SIZE)

    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    pygame.display.set_caption("TvC Live HUD / Frame Probe")
    clock = pygame.time.Clock()

    last_base_by_slot = {}
    y_off_by_base     = {}
    prev_hp           = {}

    frame_idx = 0
    running = True

    while running:
        frame_start = time.time()

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False

        # Identify each slot's fighter struct
        resolved = []
        for slotname, ptr, teamtag in SLOTS:
            base, changed = RESOLVER.resolve_base(ptr)
            if base and last_base_by_slot.get(ptr) != base:
                last_base_by_slot[ptr] = base
                METER_CACHE.drop(base)
                y_off_by_base[base] = pick_posy_off_no_jump(base)
            resolved.append((slotname, teamtag, base))

        # Meter for first active characters
        p1c1_base = next((b for n, t, b in resolved if n == "P1-C1" and b), None)
        p2c1_base = next((b for n, t, b in resolved if n == "P2-C1" and b), None)
        meter_p1 = read_meter(p1c1_base)
        meter_p2 = read_meter(p2c1_base)

        # Snapshots for HUD
        snaps = {}
        for slotname, teamtag, base in resolved:
            if not base:
                continue

            yoff = y_off_by_base.get(base, 0xF4)
            s = read_fighter(base, yoff)
            if not s:
                continue

            s["teamtag"]  = teamtag
            s["slotname"] = slotname

            # Pick which ID represents the current state/move.
            # You said: use the thing that's currently showing up as "FLAG_266".
            # That's your attA.
            atk_a = s.get("attA")
            atk_b = s.get("attB")
            chosen_id = atk_a if atk_a is not None else atk_b

            # Normalize char_id using HUD name -> CSV ID mapping.
            display_name = s.get("name")
            csv_char_id  = CHAR_ID_CORRECTION.get(display_name, s.get("id"))

            # Label resolution with per-char first, then global/system, else FLAG.
            nice_label = move_label_for(chosen_id, csv_char_id, move_map, global_map)

            # Store for HUD
            s["mv_label"]      = nice_label
            s["mv_id_display"] = chosen_id
            s["csv_char_id"]   = csv_char_id

            snaps[slotname] = s

        # Hit detection (HP drop)
        for slotname, v_snap in snaps.items():
            base = v_snap["base"]
            hp_now  = v_snap["cur"]
            hp_prev = prev_hp.get(base, hp_now)
            prev_hp[base] = hp_now

            dmg = hp_prev - hp_now
            if dmg >= MIN_HIT_DAMAGE:
                vic_team = v_snap["teamtag"]
                attackers = [s for s in snaps.values() if s["teamtag"] != vic_team]
                if not attackers:
                    continue

                best_d2 = None
                atk_snap = None
                for cand in attackers:
                    d2v = dist2(v_snap, cand)
                    if best_d2 is None or d2v < best_d2:
                        best_d2 = d2v
                        atk_snap = cand
                if not atk_snap:
                    continue

                atk_a = atk_snap.get("attA")
                atk_b = atk_snap.get("attB")
                chosen_id = atk_a if atk_a is not None else atk_b

                atk_display_name = atk_snap.get("name")
                atk_csv_char_id  = CHAR_ID_CORRECTION.get(atk_display_name, atk_snap.get("id"))

                mv_label = move_label_for(chosen_id, atk_csv_char_id, move_map, global_map)
                atk_hex  = f"0x{chosen_id:X}" if chosen_id is not None else "NONE"

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

                ADV_TRACK.start_contact(atk_snap["base"], v_snap["base"], frame_idx)

                newcsv = not os.path.exists(HIT_CSV)
                with open(HIT_CSV, "a", newline="", encoding="utf-8") as fh:
                    w = csv.writer(fh)
                    if newcsv:
                        w.writerow([
                            "t", "victim_label", "victim_char", "dmg",
                            "hp_before", "hp_after",
                            "attacker_label", "attacker_char", "attacker_char_id",
                            "attacker_id_dec", "attacker_id_hex", "attacker_move",
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
                        atk_csv_char_id,
                        chosen_id, atk_hex, mv_label,
                        0.0 if best_d2 is None else f"{best_d2:.3f}",
                        atk_snap["f062"], atk_snap["f063"],
                        v_snap["f062"], v_snap["f063"],
                        f"0x{(atk_snap['ctrl'] or 0):08X}",
                        f"0x{(v_snap['ctrl'] or 0):08X}",
                    ])

        # Advantage model updates
        pairs_to_check = [
            ("P1-C1","P2-C1"), ("P1-C1","P2-C2"),
            ("P1-C2","P2-C1"), ("P1-C2","P2-C2"),
            ("P2-C1","P1-C1"), ("P2-C1","P1-C2"),
            ("P2-C2","P1-C1"), ("P2-C2","P1-C2"),
        ]
        for atk_slot, vic_slot in pairs_to_check:
            atk_snap = snaps.get(atk_slot)
            vic_snap = snaps.get(vic_slot)
            if atk_snap and vic_snap:
                d2_val = dist2(atk_snap, vic_snap)
                ADV_TRACK.update_pair(atk_snap, vic_snap, d2_val, frame_idx)

        # Advantage summary for HUD
        adv_line = ""
        if "P1-C1" in snaps and "P2-C1" in snaps:
            adv_val = ADV_TRACK.get_latest_adv(
                snaps["P1-C1"]["base"],
                snaps["P2-C1"]["base"]
            )
            if adv_val is not None:
                adv_line = f"P1 vs P2 frame adv ~ {adv_val:+.1f}f"

        # RENDER
        screen.fill(COL_BG)

        r_p1c1 = pygame.Rect(10, ROW1_Y, PANEL_W, PANEL_H)
        r_p2c1 = pygame.Rect(10 + PANEL_W + 20, ROW1_Y, PANEL_W, PANEL_H)
        draw_panel_classic(screen, r_p1c1, snaps.get("P1-C1"), meter_p1, font, smallfont, "P1-C1")
        draw_panel_classic(screen, r_p2c1, snaps.get("P2-C1"), meter_p2, font, smallfont, "P2-C1")

        r_p1c2 = pygame.Rect(10, ROW2_Y, PANEL_W, PANEL_H)
        r_p2c2 = pygame.Rect(10 + PANEL_W + 20, ROW2_Y, PANEL_W, PANEL_H)
        draw_panel_classic(screen, r_p1c2, snaps.get("P1-C2"), None, font, smallfont, "P1-C2")
        draw_panel_classic(screen, r_p2c2, snaps.get("P2-C2"), None, font, smallfont, "P2-C2")

        act_rect = pygame.Rect(10, STACK_TOP_Y, SCREEN_W-20, ACTIVITY_H)
        draw_activity(screen, act_rect, font, adv_line)

        log_rect = pygame.Rect(10, act_rect.bottom+10, SCREEN_W-20, LOG_H)
        draw_event_log(screen, log_rect, font, smallfont)

        insp_rect = pygame.Rect(10, log_rect.bottom+10, SCREEN_W-20, INSP_H)
        draw_inspector(screen, insp_rect, font, smallfont, snaps)

        pygame.display.flip()

        elapsed = time.time() - frame_start
        sleep_left = INTERVAL - elapsed
        if sleep_left > 0:
            time.sleep(sleep_left)

        clock.tick()
        frame_idx += 1

    pygame.quit()


if __name__ == "__main__":
    main()
