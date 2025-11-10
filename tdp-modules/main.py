# main.py
import os
import csv
import time
import threading

import pygame

from dolphin_io import hook, rd8, rbytes
from config import (
    MIN_HIT_DAMAGE,
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
    draw_scan_normals,
    draw_slot_button,
)
from redscan import RedHealthScanner
from global_redscan import GlobalRedScanner
from events import log_engaged, log_hit, log_frame_advantage

# optional scanner with stuns/actives/etc.
try:
    import scan_normals_all
    HAVE_SCAN_NORMALS = True
    from scan_normals_all import ANIM_MAP as SCAN_ANIM_MAP
except Exception:
    scan_normals_all = None
    HAVE_SCAN_NORMALS = False
    SCAN_ANIM_MAP = {}

TARGET_FPS = 60
DAMAGE_EVERY_FRAMES = 3
ADV_EVERY_FRAMES = 2
SCAN_MIN_INTERVAL_SEC = 180.0  # 3 minutes


def init_pygame():
    pygame.init()
    try:
        font = pygame.font.SysFont("consolas", FONT_MAIN_SIZE)
        smallfont = pygame.font.SysFont("consolas", FONT_SMALL_SIZE)
    except Exception:
        font = pygame.font.Font(None, FONT_MAIN_SIZE)
        smallfont = pygame.font.Font(None, FONT_SMALL_SIZE)
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H))
    pygame.display.set_caption("TvC Live HUD / Frame Probe")
    return screen, font, smallfont


def _fmt_stun_popup(v):
    if v is None:
        return ""
    if v == 0x0C: return "10"
    if v == 0x0F: return "15"
    if v == 0x11: return "17"
    if v == 0x15: return "21"
    return str(v)


def _open_frame_data_window_thread(slot_label, target_slot):
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        print("tkinter not available")
        return

    cname = target_slot.get("char_name", "—")
    root = tk.Tk()
    root.title(f"Frame data: {slot_label} ({cname})")

    cols = (
        "move", "kind", "damage", "meter",
        "startup", "active", "hitstun", "blockstun",
        "advH", "advB", "abs"
    )
    frame = ttk.Frame(root)
    frame.pack(fill="both", expand=True)

    tree = ttk.Treeview(frame, columns=cols, show="headings", height=30)
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)

    for c, txt in [
        ("move", "Move"), ("kind", "Kind"), ("damage", "Dmg"), ("meter", "Meter"),
        ("startup", "Start"), ("active", "Active"), ("hitstun", "HS"), ("blockstun", "BS"),
        ("advH", "advH"), ("advB", "advB"), ("abs", "ABS")
    ]:
        tree.heading(c, text=txt)

    tree.column("move", width=160, anchor="w")
    tree.column("kind", width=60, anchor="w")
    tree.column("damage", width=60, anchor="center")
    tree.column("meter", width=55, anchor="center")
    tree.column("startup", width=55, anchor="center")
    tree.column("active", width=65, anchor="center")
    tree.column("hitstun", width=45, anchor="center")
    tree.column("blockstun", width=45, anchor="center")
    tree.column("advH", width=55, anchor="center")
    tree.column("advB", width=55, anchor="center")
    tree.column("abs", width=110, anchor="w")

    # sort & dedupe
    moves_sorted = sorted(
        target_slot.get("moves", []),
        key=lambda m: (
            m.get("id") is None,
            m.get("id", 0xFFFF),
            m.get("abs", 0xFFFFFFFF),
        ),
    )
    seen_named = set()
    deduped = []
    for mv in moves_sorted:
        aid = mv.get("id")
        if aid is None:
            deduped.append(mv); continue
        name = SCAN_ANIM_MAP.get(aid, f"anim_{aid:02X}")
        unknownish = name.startswith("anim_") or "?" in name
        if not unknownish:
            if aid in seen_named:
                continue
            seen_named.add(aid)
        deduped.append(mv)

    for mv in deduped:
        aid = mv.get("id")
        if aid is None:
            move_name = "anim_--"
        else:
            move_name = SCAN_ANIM_MAP.get(aid, f"anim_{aid:02X}")

        a_s = mv.get("active_start")
        a_e = mv.get("active_end")
        if a_s is not None and a_e is not None:
            active_txt = f"{a_s}-{a_e}"
        else:
            active_txt = "" if a_e is None else str(a_e)

        tree.insert(
            "",
            "end",
            values=(
                move_name,
                mv.get("kind", ""),
                "" if mv.get("damage") is None else str(mv.get("damage")),
                "" if mv.get("meter") is None else str(mv.get("meter")),
                "" if mv.get("active_start") is None else str(mv.get("active_start")),
                active_txt,
                _fmt_stun_popup(mv.get("hitstun")),
                _fmt_stun_popup(mv.get("blockstun")),
                "" if mv.get("adv_hit") is None else f"{mv['adv_hit']:+d}",
                "" if mv.get("adv_block") is None else f"{mv['adv_block']:+d}",
                f"0x{mv.get('abs',0):08X}" if mv.get("abs") else "",
            )
        )

    root.mainloop()


def open_frame_data_window(slot_label, scan_data):
    if not scan_data:
        return
    target = None
    for s in scan_data:
        if s.get("slot_label") == slot_label:
            target = s
            break
    if not target:
        return
    t = threading.Thread(
        target=_open_frame_data_window_thread,
        args=(slot_label, target),
        daemon=True,
    )
    t.start()


def build_move_lookup(scan_data):
    """
    slot_label -> { anim_id: move_dict }
    """
    if not scan_data:
        return {}
    out = {}
    for slot in scan_data:
        sl = slot.get("slot_label")
        moves = slot.get("moves", [])
        d = {}
        for mv in moves:
            aid = mv.get("id")
            if aid is None:
                continue
            d[aid] = mv
        out[sl] = d
    return out


def main():
    print("HUD: waiting for Dolphin…")
    hook()
    print("HUD: hooked Dolphin.")

    move_map, global_map = load_move_map(GENERIC_MAPPING_CSV, PAIR_MAPPING_CSV)
    screen, font, smallfont = init_pygame()
    clock = pygame.time.Clock()

    last_base_by_slot = {}
    y_off_by_base = {}
    prev_hp = {}
    pool_baseline = {}

    # track move starts
    last_move_start_frame = {}   # base -> frame_idx
    last_move_anim_id = {}       # base -> anim_id

    local_scan = RedHealthScanner()
    global_scan = GlobalRedScanner()

    last_adv_display = ""
    last_scan_normals = None
    last_scan_time = 0.0
    manual_scan_requested = False
    cached_lookup = {}

    pending_hits = []
    frame_idx = 0
    running = True

    while running:
        mouse_clicked_pos = None
        snapshot_p1c1_local = False
        run_local_analysis = False
        snapshot_global_full = False
        run_global_analysis = False

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
                elif ev.key == pygame.K_F5:
                    manual_scan_requested = True
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                mouse_clicked_pos = ev.pos

        # resolve slots
        resolved_slots = []
        for slotname, ptr_addr, teamtag in SLOTS:
            base, changed = RESOLVER.resolve_base(ptr_addr)
            if base and last_base_by_slot.get(ptr_addr) != base:
                last_base_by_slot[ptr_addr] = base
                METER_CACHE.drop(base)
                y_off_by_base[base] = pick_posy_off_no_jump(base)
            resolved_slots.append((slotname, teamtag, base))

        # meters
        p1c1_base = next((b for n, t, b in resolved_slots if n == "P1-C1" and b), None)
        p2c1_base = next((b for n, t, b in resolved_slots if n == "P2-C1" and b), None)
        meter_p1 = read_meter(p1c1_base)
        meter_p2 = read_meter(p2c1_base)

        # fighter snapshots
        snaps = {}
        for slotname, teamtag, base in resolved_slots:
            if not base:
                continue
            yoff = y_off_by_base.get(base, 0xF4)
            snap = read_fighter(base, yoff)
            if not snap:
                continue
            snap["teamtag"] = teamtag
            snap["slotname"] = slotname

            # anim / move detection
            atk_a = snap.get("attA")
            atk_b = snap.get("attB")
            cur_anim = atk_a if atk_a is not None else atk_b

            char_name = snap.get("name")
            csv_char_id = CHAR_ID_CORRECTION.get(char_name, snap.get("id"))
            mv_label = move_label_for(cur_anim, csv_char_id, move_map, global_map)
            snap["mv_label"] = mv_label
            snap["mv_id_display"] = cur_anim
            snap["csv_char_id"] = csv_char_id

            # detect move start
            prev_anim = last_move_anim_id.get(base)
            if cur_anim is not None and cur_anim != 0 and cur_anim != prev_anim:
                last_move_start_frame[base] = frame_idx
                last_move_anim_id[base] = cur_anim
            else:
                # keep anim id (might be 0)
                last_move_anim_id[base] = cur_anim

            # pool %
            pool_byte = snap.get("hp_pool_byte")
            if pool_byte is not None:
                prev_max = pool_baseline.get(base, 0)
                if pool_byte > prev_max:
                    pool_baseline[base] = pool_byte
                max_pool = pool_baseline.get(base, 1)
                pool_pct = (pool_byte / max_pool) * 100.0 if max_pool else 0.0
            else:
                pool_pct = 0.0
            snap["pool_pct"] = pool_pct

            # baroque + inputs
            if slotname == "P1-C1":
                buddy_val = rd8(BAROQUE_STATUS_ADDR_BUDDY) or 0
                main_val = rd8(BAROQUE_STATUS_ADDR_MAIN) or 0
                ready_flag = (main_val != 0)
                f0 = rd8(BAROQUE_FLAG_ADDR_0) or 0
                f1 = rd8(BAROQUE_FLAG_ADDR_1) or 0
                active_flag = (f0 != 0) or (f1 != 0)
                snap["baroque_ready"] = 1 if ready_flag else 0
                snap["baroque_active"] = 1 if active_flag else 0
                snap["baroque_ready_raw"] = (main_val, buddy_val)
                snap["baroque_active_dbg"] = (f0, f1)
                inputs_struct = {}
                for key, addr in INPUT_MONITOR_ADDRS.items():
                    v = rd8(addr)
                    inputs_struct[key] = 0 if v is None else v
                snap["inputs"] = inputs_struct
            else:
                snap["baroque_ready"] = 0
                snap["baroque_active"] = 0
                snap["baroque_ready_raw"] = (0, 0)
                snap["baroque_active_dbg"] = (0, 0)
                snap["inputs"] = {}

            snaps[slotname] = snap

        # scanner hotkeys
        if snapshot_p1c1_local:
            targ = snaps.get("P1-C1")
            if targ:
                local_scan.snapshot(targ)
        if run_local_analysis:
            local_scan.analyze()
        if snapshot_global_full:
            targ = snaps.get("P1-C1")
            if targ:
                global_scan.snapshot(targ)
        if run_global_analysis:
            global_scan.analyze()

        # DAMAGE DETECTION (live-corrected adv)
        if frame_idx % DAMAGE_EVERY_FRAMES == 0:
            if last_scan_normals:
                cached_lookup = build_move_lookup(last_scan_normals)

            for slotname, vic_snap in snaps.items():
                base = vic_snap["base"]
                hp_now = vic_snap["cur"]
                hp_prev = prev_hp.get(base, hp_now)
                prev_hp[base] = hp_now

                dmg = hp_prev - hp_now
                if dmg < MIN_HIT_DAMAGE:
                    continue

                # pick attacker from other team, closest
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

                # feed old tracker too
                ADV_TRACK.start_contact(atk_snap["base"], vic_snap["base"], frame_idx)

                # --- live-corrected advantage using scan baseline ---
                live_adv_str = ""
                atk_base = atk_snap["base"]
                atk_slotname = atk_snap["slotname"]
                atk_move_id = last_move_anim_id.get(atk_base)
                atk_move_start = last_move_start_frame.get(atk_base)

                if atk_move_id is not None and atk_move_start is not None:
                    hit_offset = frame_idx - atk_move_start
                    if hit_offset < 0:
                        hit_offset = 0

                    mvinfo = None
                    slot_moves = cached_lookup.get(atk_slotname)
                    if slot_moves:
                        mvinfo = slot_moves.get(atk_move_id)

                    if mvinfo:
                        base_adv_hit = mvinfo.get("adv_hit")
                        base_adv_block = mvinfo.get("adv_block")
                        active_end = mvinfo.get("active_end")

                        # scan assumed: hit at active_end
                        # we actually hit at hit_offset
                        # so we lose (active_end - hit_offset) frames
                        real_adv_hit = None
                        real_adv_block = None
                        if active_end is not None:
                            delta = active_end - hit_offset
                            if delta < 0:
                                delta = 0
                            if base_adv_hit is not None:
                                real_adv_hit = base_adv_hit - delta
                            if base_adv_block is not None:
                                real_adv_block = base_adv_block - delta

                        if real_adv_hit is not None or real_adv_block is not None:
                            h_txt = f"H:{real_adv_hit:+d}" if real_adv_hit is not None else "H:?"
                            b_txt = f"B:{real_adv_block:+d}" if real_adv_block is not None else "B:?"
                            live_adv_str = f"{atk_slotname} {SCAN_ANIM_MAP.get(atk_move_id, f'anim_{atk_move_id:02X}')} {h_txt} {b_txt}"
                            if real_adv_hit is not None:
                                log_frame_advantage(atk_snap, vic_snap, real_adv_hit)

                # log general stuff
                log_engaged(atk_snap, vic_snap, frame_idx)
                log_hit(atk_snap, vic_snap, dmg, frame_idx)

                if live_adv_str:
                    last_adv_display = live_adv_str

        # periodic advantage from old tracker
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
                    ADV_TRACK.update_pair(atk_snap, vic_snap, dist2(atk_snap, vic_snap), frame_idx)
            freshest = ADV_TRACK.get_freshest_final_info()
            if freshest:
                atk_b, vic_b, plusf, fin_frame = freshest
                atk_slot = next((s for s in snaps.values() if s["base"] == atk_b), None)
                vic_slot = next((s for s in snaps.values() if s["base"] == vic_b), None)
                if atk_slot and vic_slot:
                    last_adv_display = (
                        f"{atk_slot['slotname']}({atk_slot['name']}) vs "
                        f"{vic_slot['slotname']}({vic_slot['name']}): {plusf:+.1f}f"
                    )
                else:
                    last_adv_display = f"Frame adv {plusf:+.1f}f"
                log_frame_advantage(atk_slot, vic_slot, plusf)

        # inspector blob
        baroque_blob = rbytes(BAROQUE_MONITOR_ADDR, BAROQUE_MONITOR_SIZE)

        # DRAW
        screen.fill(COL_BG)

        r_p1c1 = pygame.Rect(10, ROW1_Y, PANEL_W, PANEL_H)
        r_p2c1 = pygame.Rect(10 + PANEL_W + 20, ROW1_Y, PANEL_W, PANEL_H)
        r_p1c2 = pygame.Rect(10, ROW2_Y, PANEL_W, PANEL_H)
        r_p2c2 = pygame.Rect(10 + PANEL_W + 20, ROW2_Y, PANEL_W, PANEL_H)

        draw_panel_classic(screen, r_p1c1, snaps.get("P1-C1"), meter_p1, font, smallfont, "P1-C1")
        draw_panel_classic(screen, r_p2c1, snaps.get("P2-C1"), meter_p2, font, smallfont, "P2-C1")
        draw_panel_classic(screen, r_p1c2, snaps.get("P1-C2"), None, font, smallfont, "P1-C2")
        draw_panel_classic(screen, r_p2c2, snaps.get("P2-C2"), None, font, smallfont, "P2-C2")

        # buttons
        btn_h = 20
        btn_p1c1 = pygame.Rect(r_p1c1.x, r_p1c1.bottom + 3, 130, btn_h)
        btn_p2c1 = pygame.Rect(r_p2c1.x, r_p2c1.bottom + 3, 130, btn_h)
        btn_p1c2 = pygame.Rect(r_p1c2.x, r_p1c2.bottom + 3, 130, btn_h)
        btn_p2c2 = pygame.Rect(r_p2c2.x, r_p2c2.bottom + 3, 130, btn_h)

        draw_slot_button(screen, btn_p1c1, smallfont, "Show frame data")
        draw_slot_button(screen, btn_p2c1, smallfont, "Show frame data")
        draw_slot_button(screen, btn_p1c2, smallfont, "Show frame data")
        draw_slot_button(screen, btn_p2c2, smallfont, "Show frame data")

        act_rect = pygame.Rect(10, STACK_TOP_Y, SCREEN_W - 20, ACTIVITY_H)
        draw_activity(screen, act_rect, font, last_adv_display)

        log_rect = pygame.Rect(10, act_rect.bottom + 10, SCREEN_W - 20, LOG_H)
        draw_event_log(screen, log_rect, font, smallfont)

        scan_rect = pygame.Rect(10, log_rect.bottom + 10, SCREEN_W - 20, 140)
        draw_scan_normals(screen, scan_rect, font, smallfont, last_scan_normals)

        insp_rect = pygame.Rect(10, scan_rect.bottom + 10, SCREEN_W - 20, INSP_H)
        draw_inspector(screen, insp_rect, font, smallfont, snaps, baroque_blob)

        pygame.display.flip()

        # mouse → popup
        if mouse_clicked_pos is not None:
            mx, my = mouse_clicked_pos

            def ensure_scan():
                nonlocal last_scan_normals, last_scan_time, cached_lookup
                if last_scan_normals is None and HAVE_SCAN_NORMALS:
                    try:
                        last_scan_normals = scan_normals_all.scan_once()
                        last_scan_time = time.time()
                        cached_lookup = build_move_lookup(last_scan_normals)
                    except Exception as e:
                        print("scan failed:", e)
                return last_scan_normals

            if btn_p1c1.collidepoint(mx, my):
                data = ensure_scan()
                if data:
                    open_frame_data_window("P1-C1", data)
            elif btn_p2c1.collidepoint(mx, my):
                data = ensure_scan()
                if data:
                    open_frame_data_window("P2-C1", data)
            elif btn_p1c2.collidepoint(mx, my):
                data = ensure_scan()
                if data:
                    open_frame_data_window("P1-C2", data)
            elif btn_p2c2.collidepoint(mx, my):
                data = ensure_scan()
                if data:
                    open_frame_data_window("P2-C2", data)

        # slow auto-scan
        now = time.time()
        should_auto_scan = (
            HAVE_SCAN_NORMALS
            and (last_scan_normals is not None)
            and (now - last_scan_time) >= SCAN_MIN_INTERVAL_SEC
        )
        if HAVE_SCAN_NORMALS and manual_scan_requested:
            try:
                last_scan_normals = scan_normals_all.scan_once()
                last_scan_time = time.time()
                cached_lookup = build_move_lookup(last_scan_normals)
            except Exception as e:
                print("manual scan failed:", e)
            manual_scan_requested = False
        elif should_auto_scan:
            try:
                last_scan_normals = scan_normals_all.scan_once()
                last_scan_time = time.time()
                cached_lookup = build_move_lookup(last_scan_normals)
            except Exception as e:
                print("auto scan failed:", e)

        # optional CSV dump (kept from earlier)
        if pending_hits and (frame_idx % 30 == 0):
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
            pending_hits.clear()

        clock.tick(TARGET_FPS)
        frame_idx += 1

    pygame.quit()


if __name__ == "__main__":
    main()
