# hud_draw.py
# All pygame drawing helpers (panels, log, inspector). :contentReference[oaicite:13]{index=13}

import pygame
from config import (
    COL_BG, COL_PANEL, COL_BORDER, COL_TEXT, COL_DIM, COL_GOOD, COL_ACCENT,
)
from moves import move_label_for, decode_flag_062, decode_flag_063
from events import event_log, MAX_LOG_LINES

def hp_color(pct):
    # In your HUD you always treated HP text as green-ish.
    # We keep that behavior.
    if pct is None:
        return COL_TEXT
    if pct > 0.66:
        return COL_GOOD
    if pct > 0.33:
        return COL_GOOD
    return COL_GOOD

def draw_panel_classic(surface, rect, snap, meter_val, font, smallfont, header_label):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    if not snap:
        surface.blit(
            font.render(f"{header_label} ---", True, COL_TEXT),
            (rect.x+6, rect.y+4)
        )
        return

    hdr = f"{header_label} {snap['name']} @{snap['base']:08X}"
    surface.blit(font.render(hdr, True, COL_TEXT),(rect.x+6, rect.y+4))

    cur_hp = snap["cur"]; max_hp = snap["max"]
    meter_str = str(meter_val) if meter_val is not None else "--"
    pct = (cur_hp / max_hp) if (max_hp and max_hp > 0) else None
    hp_line = f"HP {cur_hp}/{max_hp}    Meter:{meter_str}"
    surface.blit(font.render(hp_line, True, hp_color(pct)), (rect.x+6, rect.y+24))

    lastdmg = snap["last"] if snap["last"] is not None else 0
    pos_line = (
        f"Pos X:{snap['x']:.2f} Y:{(snap['y'] or 0.0):.2f}   "
        f"LastDmg:{lastdmg}"
    )
    surface.blit(font.render(pos_line, True, COL_TEXT),(rect.x+6, rect.y+44))

    atk_id = snap["attA"]
    sub_id = snap["attB"]
    labelA = move_label_for(atk_id, snap["id"], pair_map={}, generic_map={})
    # We won't actually pass empty maps at runtime; main will pass closures or partials if needed.

    mv_line = f"MoveID:{atk_id} {labelA}   sub:{sub_id}"
    surface.blit(font.render(mv_line, True, COL_TEXT),(rect.x+6, rect.y+64))

    f062_val, f062_desc = decode_flag_062(snap["f062"])
    f063_val, f063_desc = decode_flag_063(snap["f063"])
    f064_val = snap["f064"] if snap["f064"] is not None else 0
    f072_val = snap["f072"] if snap["f072"] is not None else 0
    ctrl_hex = f"0x{(snap['ctrl'] or 0):08X}"

    row1 = (
        f"062:{f062_val} {f062_desc}   "
        f"063:{f063_val} {f063_desc}   "
        f"064:{f064_val} UNK({f064_val})"
    )
    surface.blit(font.render(row1, True, COL_TEXT),(rect.x+6, rect.y+84))

    row2 = f"072:{f072_val}   ctrl:{ctrl_hex}"
    surface.blit(font.render(row2, True, COL_TEXT),(rect.x+6, rect.y+104))

    surface.blit(font.render("impact:--", True, COL_TEXT),(rect.x+6, rect.y+124))

def draw_activity(surface, rect, font, adv_line):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)
    txt = "Activity / Frame Advantage"
    surface.blit(font.render(txt, True, COL_TEXT),(rect.x+6, rect.y+4))

    if adv_line:
        surface.blit(font.render(adv_line, True, COL_TEXT),(rect.x+6, rect.y+20))

def draw_event_log(surface, rect, font, smallfont):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    title = "Event Log (latest at bottom)"
    surface.blit(font.render(title, True, COL_TEXT),(rect.x+6, rect.y+4))

    lines = event_log[-MAX_LOG_LINES:]
    y = rect.y + 24
    max_w = rect.w - 12

    for line in lines[-12:]:
        words = line.split(' ')
        curr = ""
        for w in words:
            test = curr + (" " if curr else "") + w
            if font.size(test)[0] > max_w and curr:
                surface.blit(
                    smallfont.render(curr, True, COL_TEXT),
                    (rect.x+6, y)
                )
                y += smallfont.get_height()
                curr = w
            else:
                curr = test
        if curr:
            surface.blit(
                smallfont.render(curr, True, COL_TEXT),
                (rect.x+6, y)
            )
            y += smallfont.get_height()

def draw_inspector(surface, rect, font, smallfont, snaps):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    title = "Inspector (0x050-0x08F wires, per character)"
    surface.blit(font.render(title, True, COL_TEXT),(rect.x+6, rect.y+4))

    col_w  = rect.w // 4
    base_y = rect.y + 24

    order = ["P1-C1","P1-C2","P2-C1","P2-C2"]
    for i, slot in enumerate(order):
        subr_x = rect.x + i * col_w
        subr_y = base_y
        snap   = snaps.get(slot)

        if not snap:
            header_txt = f"{slot} [---]"
            surface.blit(
                smallfont.render(header_txt, True, COL_DIM),
                (subr_x+4, subr_y)
            )
            continue

        cid = snap["id"]
        header_txt = f"{slot} {snap['name']} (ID:{cid})"
        surface.blit(
            smallfont.render(header_txt, True, COL_TEXT),
            (subr_x+4, subr_y)
        )
        line_y = subr_y + smallfont.get_height() + 2

        f062_val, f062_desc = decode_flag_062(snap["f062"])
        f063_val, f063_desc = decode_flag_063(snap["f063"])
        ctrl_hex            = f"0x{(snap['ctrl'] or 0):08X}"
        f064_val            = snap["f064"] if snap["f064"] is not None else 0
        f072_val            = snap["f072"] if snap["f072"] is not None else 0

        info_lines = [
            f"ctrl:{ctrl_hex}",
            f"062:{f062_val} {f062_desc}",
            f"063:{f063_val} {f063_desc}",
            f"064:{f064_val} 072:{f072_val}",
        ]

        for ln in info_lines:
            surface.blit(
                smallfont.render(ln, True, COL_ACCENT),
                (subr_x+4, line_y)
            )
            line_y += smallfont.get_height() + 2

        # wires dump
        chunks = []
        for off, b in snap["wires"]:
            if off < 0x050 or off >= 0x090:
                continue
            val = "--" if b is None else str(b)
            chunks.append(f"{off:03X}:{val}")
        blob = " ".join(chunks)

        words = blob.split(" ")
        curr = ""
        for w in words:
            test = curr + (" " if curr else "") + w
            if smallfont.size(test)[0] > (col_w - 8) and curr:
                surface.blit(
                    smallfont.render(curr, True, COL_TEXT),
                    (subr_x+4, line_y)
                )
                line_y += smallfont.get_height() + 2
                curr = w
            else:
                curr = test
        if curr:
            surface.blit(
                smallfont.render(curr, True, COL_TEXT),
                (subr_x+4, line_y)
            )
            # advance line_y if needed next loop
