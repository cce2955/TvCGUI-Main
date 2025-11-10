import os
import csv
import time
import threading

import pygame

from dolphin_io import hook, rd8
from config import (
    MIN_HIT_DAMAGE,
    SCREEN_W, SCREEN_H,
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
    draw_scan_normals,
    draw_slot_button,
)
from redscan import RedHealthScanner
from global_redscan import GlobalRedScanner
from events import log_engaged, log_hit, log_frame_advantage

# optional deep scan
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
SCAN_MIN_INTERVAL_SEC = 180.0  # 3 min


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
    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.RESIZABLE)
    pygame.display.set_caption("TvC Live HUD / Frame Probe")
    return screen, font, smallfont


# --------- helper to fix bogus scan advantages ---------
def sanitize_scan_adv(mvinfo):
    base_adv_hit = mvinfo.get("adv_hit")
    base_adv_block = mvinfo.get("adv_block")
    active_end = mvinfo.get("active_end")
    hs = mvinfo.get("hitstun")
    bs = mvinfo.get("blockstun")

    rec_hit = None
    rec_blk = None
    if hs is not None and base_adv_hit is not None:
        rec_hit = hs - base_adv_hit
    if bs is not None and base_adv_block is not None:
        rec_blk = bs - base_adv_block

    def is_suspicious(r):
        return r is not None and r >= 40

    if is_suspicious(rec_hit) or is_suspicious(rec_blk):
        assumed_total = 20
        if active_end is not None:
            recovery = max(0, assumed_total - active_end)
        else:
            recovery = 6

        if hs is not None:
            base_adv_hit = hs - recovery
        else:
            base_adv_hit = None

        if bs is not None:
            base_adv_block = bs - recovery
        else:
            base_adv_block = None

    return base_adv_hit, base_adv_block, active_end


# ------------------ frame-data popup ------------------ #
def _open_frame_data_window_thread(slot_label, target_slot):
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        print("tkinter not available")
        return

    from scan_normals_all import ANIM_MAP as SCAN_ANIM_MAP_LOCAL

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

    headers = [
        ("move", "Move"), ("kind", "Kind"), ("damage", "Dmg"), ("meter", "Meter"),
        ("startup", "Start"), ("active", "Active"), ("hitstun", "HS"), ("blockstun", "BS"),
        ("advH", "advH"), ("advB", "advB"), ("abs", "ABS")
    ]
    for c, txt in headers:
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
            deduped.append(mv)
            continue
        name = SCAN_ANIM_MAP_LOCAL.get(aid, f"anim_{aid:02X}")
        if not name.startswith("anim_") and "?" not in name:
            if aid in seen_named:
                continue
            seen_named.add(aid)
        deduped.append(mv)

    def _fmt_stun(v):
        if v is None:
            return ""
        if v == 0x0C:
            return "10"
        if v == 0x0F:
            return "15"
        if v == 0x11:
            return "17"
        if v == 0x15:
            return "21"
        return str(v)

    for mv in deduped:
        aid = mv.get("id")
        move_name = (
            SCAN_ANIM_MAP_LOCAL.get(aid, f"anim_{aid:02X}")
            if aid is not None else "anim_--"
        )
        a_s = mv.get("active_start")
        a_e = mv.get("active_end")
        active_txt = (
            f"{a_s}-{a_e}"
            if (a_s is not None and a_e is not None)
            else (str(a_e) if a_e is not None else "")
        )

        sane_adv_h, sane_adv_b, _ = sanitize_scan_adv(mv)

        tree.insert(
            "",
            "end",
            values=(
                move_name,
                mv.get("kind", ""),
                "" if mv.get("damage") is None else str(mv.get("damage")),
                "" if mv.get("meter") is None else str(mv.get("meter")),
                "" if a_s is None else str(a_s),
                active_txt,
                _fmt_stun(mv.get("hitstun")),
                _fmt_stun(mv.get("blockstun")),
                "" if sane_adv_h is None else f"{sane_adv_h:+d}",
                "" if sane_adv_b is None else f"{sane_adv_b:+d}",
                f"0x{mv.get('abs', 0):08X}" if mv.get("abs") else "",
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
    if not scan_data:
        return {}
    out = {}
    for slot in scan_data:
        sl = slot.get("slot_label")
        d = {}
        for mv in slot.get("moves", []):
            aid = mv.get("id")
            if aid is None:
                continue
            d[aid] = mv
        out[sl] = d
    return out


def compute_layout(w, h):
    pad = 10
    gap_x = 20
    gap_y = 10

    panel_w = (w - pad * 2 - gap_x) // 2
    panel_h = 175

    row1_y = pad
    row2_y = row1_y + panel_h + gap_y

    r_p1c1 = pygame.Rect(pad, row1_y, panel_w, panel_h)
    r_p2c1 = pygame.Rect(pad + panel_w + gap_x, row1_y, panel_w, panel_h)
    r_p1c2 = pygame.Rect(pad, row2_y, panel_w, panel_h)
    r_p2c2 = pygame.Rect(pad + panel_w + gap_x, row2_y, panel_w, panel_h)

    btn_h = 22
    btn_w = 130

    btn_p1c1 = pygame.Rect(r_p1c1.x, r_p1c1.bottom + 3, btn_w, btn_h)
    btn_p2c1 = pygame.Rect(r_p2c1.x, r_p2c1.bottom + 3, btn_w, btn_h)
    btn_p1c2 = pygame.Rect(r_p1c2.x, r_p1c2.bottom + 3, btn_w, btn_h)
    btn_p2c2 = pygame.Rect(r_p2c2.x, r_p2c2.bottom + 3, btn_w, btn_h)

    lowest_btn_bottom = max(
        btn_p1c1.bottom,
        btn_p2c1.bottom,
        btn_p1c2.bottom,
        btn_p2c2.bottom,
    )
    act_rect = pygame.Rect(pad, lowest_btn_bottom + 5, w - pad * 2, 32)

    events_y = act_rect.bottom + 8
    events_h = 150
    events_rect = pygame.Rect(pad, events_y, w - pad * 2, events_h)

    scan_y = events_rect.bottom + 8
    scan_h = max(90, h - scan_y - pad)
    scan_rect = pygame.Rect(pad, scan_y, w - pad * 2, scan_h)

    return {
        "p1c1": r_p1c1,
        "p2c1": r_p2c1,
        "p1c2": r_p1c2,
        "p2c2": r_p2c2,
        "btn_p1c1": btn_p1c1,
        "btn_p2c1": btn_p2c1,
        "btn_p1c2": btn_p1c2,
        "btn_p2c2": btn_p2c2,
        "act": act_rect,
        "events": events_rect,
        "scan": scan_rect,
    }


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

    last_move_start_frame = {}
    last_move_anim_id = {}

    local_scan = RedHealthScanner()
    global_scan = GlobalRedScanner()

    # scan normals immediately on load so HUD has data even before F5
    last_scan_normals = None
    last_scan_time = 0.0
    cached_lookup = {}
    if HAVE_SCAN_NORMALS:
        try:
            last_scan_normals = scan_normals_all.scan_once()
            last_scan_time = time.time()
            cached_lookup = build_move_lookup(last_scan_normals)
            print("initial scan_normals: OK")
        except Exception as e:
            print("initial scan_normals failed:", e)

    manual_scan_requested = False

    last_adv_display = ""
    pending_hits = []
    frame_idx = 0
    running = True
    last_real_adv_frame = -99999

    while running:
        mouse_clicked_pos = None
        snapshot_p1c1_local = False
        run_local_analysis = False
        snapshot_global_full = False
        run_global_analysis = False

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.VIDEORESIZE:
                screen = pygame.display.set_mode(ev.size, pygame.RESIZABLE)
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

        # meter reads
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

            atk_a = snap.get("attA")
            atk_b = snap.get("attB")
            cur_anim = atk_a if atk_a is not None else atk_b

            char_name = snap.get("name")
            csv_char_id = CHAR_ID_CORRECTION.get(char_name, snap.get("id"))
            mv_label = move_label_for(cur_anim, csv_char_id, move_map, global_map)
            snap["mv_label"] = mv_label
            snap["mv_id_display"] = cur_anim
            snap["csv_char_id"] = csv_char_id

            prev_anim = last_move_anim_id.get(base)
            if cur_anim is not None and cur_anim != 0 and cur_anim != prev_anim:
                last_move_start_frame[base] = frame_idx
                last_move_anim_id[base] = cur_anim
            else:
                last_move_anim_id[base] = cur_anim

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

        # redscan hotkeys
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

        # DAMAGE DETECT (live-corrected adv)
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

                ADV_TRACK.start_contact(atk_snap["base"], vic_snap["base"], frame_idx)

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
                        base_adv_hit, base_adv_block, active_end = sanitize_scan_adv(mvinfo)

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
                            name = SCAN_ANIM_MAP.get(atk_move_id, f"anim_{atk_move_id:02X}")
                            h_txt = f"H:{real_adv_hit:+d}" if real_adv_hit is not None else "H:?"
                            b_txt = f"B:{real_adv_block:+d}" if real_adv_block is not None else "B:?"
                            live_adv_str = f"{atk_slotname} {name} {h_txt} {b_txt}"
                            if real_adv_hit is not None:
                                log_frame_advantage(atk_snap, vic_snap, real_adv_hit)

                log_engaged(atk_snap, vic_snap, frame_idx)
                log_hit(atk_snap, vic_snap, dmg, frame_idx)

                if live_adv_str:
                    last_adv_display = live_adv_str
                    last_real_adv_frame = frame_idx

        # periodic tracker (lower priority)
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
                        atk_snap, vic_snap, dist2(atk_snap, vic_snap), frame_idx
                    )

            freshest = ADV_TRACK.get_freshest_final_info()
            if freshest:
                atk_b, vic_b, plusf, fin_frame = freshest

                if abs(plusf) <= 64:
                    if frame_idx - last_real_adv_frame > 20:
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

        # DRAW
        screen.fill(COL_BG)
        w, h = screen.get_size()
        layout = compute_layout(w, h)

        draw_panel_classic(screen, layout["p1c1"], snaps.get("P1-C1"), meter_p1, font, smallfont, "P1-C1")
        draw_panel_classic(screen, layout["p2c1"], snaps.get("P2-C1"), meter_p2, font, smallfont, "P2-C1")
        draw_panel_classic(screen, layout["p1c2"], snaps.get("P1-C2"), None, font, smallfont, "P1-C2")
        draw_panel_classic(screen, layout["p2c2"], snaps.get("P2-C2"), None, font, smallfont, "P2-C2")

        draw_slot_button(screen, layout["btn_p1c1"], smallfont, "Show frame data")
        draw_slot_button(screen, layout["btn_p2c1"], smallfont, "Show frame data")
        draw_slot_button(screen, layout["btn_p1c2"], smallfont, "Show frame data")
        draw_slot_button(screen, layout["btn_p2c2"], smallfont, "Show frame data")

        draw_activity(screen, layout["act"], font, last_adv_display)
        draw_event_log(screen, layout["events"], font, smallfont)
        draw_scan_normals(screen, layout["scan"], font, smallfont, last_scan_normals)

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

            if layout["btn_p1c1"].collidepoint(mx, my):
                data = ensure_scan()
                if data:
                    open_frame_data_window("P1-C1", data)
            elif layout["btn_p2c1"].collidepoint(mx, my):
                data = ensure_scan()
                if data:
                    open_frame_data_window("P2-C1", data)
            elif layout["btn_p1c2"].collidepoint(mx, my):
                data = ensure_scan()
                if data:
                    open_frame_data_window("P1-C2", data)
            elif layout["btn_p2c2"].collidepoint(mx, my):
                data = ensure_scan()
                if data:
                    open_frame_data_window("P2-C2", data)

        # slow auto-scan / manual scan
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

        # CSV header init if you ever push pending_hits here
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
