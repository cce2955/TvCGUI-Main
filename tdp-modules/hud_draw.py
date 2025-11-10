import pygame
from config import (
    COL_PANEL, COL_BORDER, COL_TEXT,
    hp_color,
    BAROQUE_MONITOR_ADDR,
)

# try to use the nice names from your scanner
try:
    from scan_normals_all import ANIM_MAP as SCAN_ANIM_MAP
except Exception:
    SCAN_ANIM_MAP = {}


def decode_flag_062(v): return v, f"{v}"
def decode_flag_063(v): return v, f"{v}"


def draw_panel_classic(surface, rect, snap, meter_val, font, smallfont, header_label):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    x0 = rect.x + 6
    y0 = rect.y

    if not snap:
        surface.blit(font.render(f"{header_label} ---", True, COL_TEXT), (x0, y0 + 4))
        return

    hdr = f"{header_label} {snap['name']} @{snap['base']:08X}"
    surface.blit(font.render(hdr, True, COL_TEXT), (x0, y0 + 4))

    cur_hp = snap["cur"]
    max_hp = snap["max"]
    pct = (cur_hp / max_hp) if (max_hp and max_hp > 0) else None
    meter_str = str(meter_val) if meter_val is not None else "--"
    hp_line = f"HP {cur_hp}/{max_hp}    Meter:{meter_str}"
    surface.blit(font.render(hp_line, True, hp_color(pct)), (x0, y0 + 24))

    pool_raw = snap.get("hp_pool_byte")
    pool_dec = str(pool_raw) if pool_raw is not None else "--"
    pool_pct_val = snap.get("pool_pct")
    pool_pct_str = f"{pool_pct_val:.1f}%" if pool_pct_val is not None else "--"
    m2b_raw = snap.get("mystery_2B")
    m2b_dec = str(m2b_raw) if m2b_raw is not None else "--"
    pool_line = f"POOL(02A): {pool_pct_str} raw:{pool_dec}   2B:{m2b_dec}"
    surface.blit(font.render(pool_line, True, COL_TEXT), (x0, y0 + 44))

    ready_txt = "YES" if snap.get("baroque_ready") else "no"
    active_txt = "ON" if snap.get("baroque_active") else "off"
    r0, r1 = snap.get("baroque_ready_raw", (0, 0))
    f0, f1 = snap.get("baroque_active_dbg", (0, 0))
    bar_line = (
        f"BAROQUE ready:{ready_txt} [{r0:02X},{r1:02X}] "
        f"active:{active_txt} [{f0:02X},{f1:02X}]"
    )
    surface.blit(font.render(bar_line, True, COL_TEXT), (x0, y0 + 64))

    inputs_struct = snap.get("inputs", {})
    inp_line = (
        f"INPUT A0:{inputs_struct.get('A0', 0):02X} "
        f"A1:{inputs_struct.get('A1', 0):02X} "
        f"A2:{inputs_struct.get('A2', 0):02X}"
    )
    surface.blit(font.render(inp_line, True, COL_TEXT), (x0, y0 + 84))

    lastdmg = snap["last"] if snap["last"] is not None else 0
    pos_line = f"Pos X:{snap['x']:.2f} Y:{(snap['y'] or 0.0):.2f}   LastDmg:{lastdmg}"
    surface.blit(font.render(pos_line, True, COL_TEXT), (x0, y0 + 104))

    shown_id = snap.get("mv_id_display", snap.get("attB", snap.get("attA")))
    shown_name = snap.get("mv_label", f"FLAG_{shown_id}")
    sub_id = snap.get("attB")
    mv_line = f"MoveID:{shown_id} {shown_name}   sub:{sub_id}"
    surface.blit(font.render(mv_line, True, COL_TEXT), (x0, y0 + 124))

    f062_val, f062_desc = decode_flag_062(snap["f062"])
    f063_val, f063_desc = decode_flag_063(snap["f063"])
    f064_val = snap["f064"] if snap["f064"] is not None else 0
    f072_val = snap["f072"] if snap["f072"] is not None else 0
    ctrl_hex = f"0x{(snap['ctrl'] or 0):08X}"

    row1 = f"062:{f062_val} {f062_desc}   063:{f063_val} {f063_desc}   064:{f064_val}"
    surface.blit(font.render(row1, True, COL_TEXT), (x0, y0 + 144))
    row2 = f"072:{f072_val}   ctrl:{ctrl_hex}"
    surface.blit(font.render(row2, True, COL_TEXT), (x0, y0 + 164))

    surface.blit(font.render("impact:--", True, COL_TEXT), (x0, y0 + 184))


def draw_activity(surface, rect, font, adv_line):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)
    line = adv_line if adv_line else "Frame adv: --"
    surface.blit(font.render(line, True, COL_TEXT), (rect.x + 6, rect.y + 6))


def draw_event_log(surface, rect, font, smallfont):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)
    surface.blit(font.render("Recent hits / adv", True, COL_TEXT), (rect.x + 6, rect.y + 4))


def draw_inspector(surface, rect, font, smallfont, snaps, baroque_blob):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)
    x0 = rect.x + 6
    y = rect.y + 4
    surface.blit(font.render("Inspector", True, COL_TEXT), (x0, y))
    y += 20

    for slotname in ("P1-C1", "P2-C1", "P1-C2", "P2-C2"):
        s = snaps.get(slotname)
        if not s:
            continue
        r0, r1 = s.get("baroque_ready_raw", (0, 0))
        f0, f1 = s.get("baroque_active_dbg", (0, 0))
        line_a = (
            f"{slotname} base:{s['base']:08X} "
            f"HP:{s['cur']}/{s['max']} "
            f"POOL:{s.get('hp_pool_byte')} "
            f"2B:{s.get('mystery_2B')} "
            f"READY:{'Y' if s.get('baroque_ready') else 'n'}[{r0:02X},{r1:02X}] "
            f"ACT:{'Y' if s.get('baroque_active') else 'n'}[{f0:02X},{f1:02X}]"
        )
        surface.blit(smallfont.render(line_a, True, COL_TEXT), (x0, y))
        y += 16
        ctrl_hex = f"0x{(s['ctrl'] or 0):08X}"
        line_b = (
            f"ctrl:{ctrl_hex} "
            f"f062:{s['f062']} f063:{s['f063']} "
            f"A0:{s.get('inputs',{}).get('A0',0):02X} "
            f"A1:{s.get('inputs',{}).get('A1',0):02X} "
            f"A2:{s.get('inputs',{}).get('A2',0):02X}"
        )
        surface.blit(smallfont.render(line_b, True, COL_TEXT), (x0, y))
        y += 16

    y += 8
    surface.blit(
        font.render(f"Baroque/Inputs block @ {BAROQUE_MONITOR_ADDR:08X}", True, COL_TEXT),
        (x0, y)
    )
    y += 20

    if baroque_blob:
        preview_len = min(len(baroque_blob), 32)
        chunk = baroque_blob[:preview_len]
        half = (preview_len + 1) // 2
        row1 = " ".join(f"{b:02X}" for b in chunk[:half])
        row2 = " ".join(f"{b:02X}" for b in chunk[half:])
        surface.blit(smallfont.render(row1, True, COL_TEXT), (x0, y)); y += 16
        surface.blit(smallfont.render(row2, True, COL_TEXT), (x0, y)); y += 16
    else:
        surface.blit(smallfont.render("(no data)", True, COL_TEXT), (x0, y))


def draw_slot_button(surface, rect, font, label):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=3)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=3)
    surface.blit(font.render(label, True, COL_TEXT), (rect.x + 4, rect.y + 2))


def _fmt_stun(val):
    if val is None:
        return "--"
    if val == 0x0C: return "10f"
    if val == 0x0F: return "15f"
    if val == 0x11: return "17f"
    if val == 0x15: return "21f"
    return f"{val:02X}"


def draw_scan_normals(surface, rect, font, smallfont, scan_data):
    """
    4 columns: P1-C1, P1-C2, P2-C1, P2-C2 with real names from scan_normals_all.ANIM_MAP
    """
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    x = rect.x + 6
    y = rect.y + 4
    surface.blit(font.render("Scan normals (preview)", True, COL_TEXT), (x, y))
    y += 18

    if not scan_data:
        surface.blit(smallfont.render("no scan / press F5", True, COL_TEXT), (x, y))
        return

    by_label = {s.get("slot_label"): s for s in scan_data}
    labels_order = ["P1-C1", "P1-C2", "P2-C1", "P2-C2"]
    col_w = (rect.width - 12) // 4
    top_y = y

    for i, lab in enumerate(labels_order):
        col_x = rect.x + 6 + i * col_w
        col_y = top_y
        slot = by_label.get(lab)
        if not slot:
            surface.blit(smallfont.render(lab, True, COL_TEXT), (col_x, col_y))
            continue
        cname = slot.get("char_name", "—")
        surface.blit(smallfont.render(f"{lab} ({cname})", True, COL_TEXT), (col_x, col_y))
        col_y += 14

        moves = slot.get("moves", [])[:4]
        for mv in moves:
            anim_id = mv.get("id")
            hs = _fmt_stun(mv.get("hitstun"))
            bs = _fmt_stun(mv.get("blockstun"))
            if anim_id is None:
                name = "anim_--"
            else:
                name = SCAN_ANIM_MAP.get(anim_id, f"anim_{anim_id:02X}")
            line = f"{name}: H{hs} B{bs}"
            surface.blit(smallfont.render(line, True, COL_TEXT), (col_x, col_y))
            col_y += 12
