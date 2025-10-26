# hud_draw.py
# Pygame HUD rendering for fighter panels, event log, inspector,
# and frame advantage display.

import pygame
from config import (
    COL_PANEL,
    COL_BORDER,
    COL_TEXT,
    COL_DIM,
    COL_GOOD,
    COL_ACCENT,
)
from moves import decode_flag_062, decode_flag_063
from events import event_log, MAX_LOG_LINES


def hp_color(pct):
    """
    Return color for HP text. Currently always green (COL_GOOD),
    but we could tier by pct later.
    """
    if pct is None:
        return COL_TEXT
    return COL_GOOD


def draw_panel_classic(surface, rect, snap, meter_val, font, smallfont, header_label):
    """
    Draws the per-character box (HP, meter, pooled HP, etc.)
    in the upper grid.
    """
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    if not snap:
        # panel header only
        surface.blit(
            font.render(f"{header_label} ---", True, COL_TEXT),
            (rect.x + 6, rect.y + 4),
        )
        return

    # header: slot name + fighter base address
    hdr = f"{header_label} {snap['name']} @{snap['base']:08X}"
    surface.blit(font.render(hdr, True, COL_TEXT), (rect.x + 6, rect.y + 4))

    # HP / meter line
    cur_hp    = snap["cur"]
    max_hp    = snap["max"]
    pct       = (cur_hp / max_hp) if (max_hp and max_hp > 0) else None
    meter_str = str(meter_val) if meter_val is not None else "--"
    hp_line   = f"HP {cur_hp}/{max_hp}    Meter:{meter_str}"
    surface.blit(
        font.render(hp_line, True, hp_color(pct)),
        (rect.x + 6, rect.y + 24),
    )

    # Position + last damage info
    lastdmg = snap["last"] if snap["last"] is not None else 0
    pos_line = (
        f"Pos X:{snap['x']:.2f} Y:{(snap['y'] or 0.0):.2f}   "
        f"LastDmg:{lastdmg}"
    )
    surface.blit(font.render(pos_line, True, COL_TEXT), (rect.x + 6, rect.y + 44))

    # NEW: pooled HP byte (0x02A) and mystery 0x02B byte.
    # We'll show both decimal and hex for 0x02A, and the raw 0x02B.
    pool_raw = snap.get("hp_pool_byte")
    pool_hex = f"0x{pool_raw:02X}" if pool_raw is not None else "--"
    pool_dec = str(pool_raw) if pool_raw is not None else "--"

    m2b_raw  = snap.get("mystery_2B")
    m2b_hex  = f"0x{m2b_raw:02X}" if m2b_raw is not None else "--"
    m2b_dec  = str(m2b_raw) if m2b_raw is not None else "--"

    # Label them clearly:
    # pool: total effective health-ish byte @0x02A
    # 2B:   weird wrapdown byte @0x02B
    pool_line = (
        f"POOL(02A): {pool_hex} ({pool_dec})   "
        f"2B (I dunno what this is yet):{m2b_hex}/{m2b_dec}"
    )
    surface.blit(font.render(pool_line, True, COL_TEXT), (rect.x + 6, rect.y + 64))

    # Move information now shifts down one row (was at y+64 before)
    shown_id   = snap.get("mv_id_display", snap.get("attB", snap.get("attA")))
    shown_name = snap.get("mv_label", f"FLAG_{shown_id}")
    sub_id     = snap.get("attB")
    mv_line    = f"MoveID:{shown_id} {shown_name}   sub:{sub_id}"
    surface.blit(font.render(mv_line, True, COL_TEXT), (rect.x + 6, rect.y + 84))

    # Decode state flags
    f062_val, f062_desc = decode_flag_062(snap["f062"])
    f063_val, f063_desc = decode_flag_063(snap["f063"])
    f064_val            = snap["f064"] if snap["f064"] is not None else 0
    f072_val            = snap["f072"] if snap["f072"] is not None else 0
    ctrl_hex            = f"0x{(snap['ctrl'] or 0):08X}"

    row1 = (
        f"062:{f062_val} {f062_desc}   "
        f"063:{f063_val} {f063_desc}   "
        f"064:{f064_val} UNK({f064_val})"
    )
    surface.blit(font.render(row1, True, COL_TEXT), (rect.x + 6, rect.y + 104))

    row2 = f"072:{f072_val}   ctrl:{ctrl_hex}"
    surface.blit(font.render(row2, True, COL_TEXT), (rect.x + 6, rect.y + 124))

    # impact placeholder still fits
    surface.blit(font.render("impact:--", True, COL_TEXT), (rect.x + 6, rect.y + 144))

def draw_activity(surface, rect, font, adv_line):
    """
    Draws the "Activity / Frame Advantage" strip.
    """
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    title = "Activity / Frame Advantage"
    surface.blit(font.render(title, True, COL_TEXT), (rect.x + 6, rect.y + 4))

    if adv_line:
        surface.blit(font.render(adv_line, True, COL_TEXT), (rect.x + 6, rect.y + 20))


def _wrap_and_blit(surface, text, font, color, max_w, start_x, start_y, line_gap=0):
    """
    Word-wrap `text` to fit max_w using `font.size`, blit line by line,
    return new Y after final line.
    """
    words = text.split(" ")
    curr = ""
    y = start_y

    for w in words:
        test = curr + (" " if curr else "") + w
        if font.size(test)[0] > max_w and curr:
            # draw current line, start new one
            surface.blit(font.render(curr, True, color), (start_x, y))
            y += font.get_height() + line_gap
            curr = w
        else:
            curr = test

    if curr:
        surface.blit(font.render(curr, True, color), (start_x, y))
        y += font.get_height() + line_gap

    return y


def draw_event_log(surface, rect, font, smallfont):
    """
    Shows scrollback of hit events and frame-advantage events.
    Latest lines are at the bottom.
    """
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    title = "Event Log (latest at bottom)"
    surface.blit(font.render(title, True, COL_TEXT), (rect.x + 6, rect.y + 4))

    lines = event_log[-MAX_LOG_LINES:]
    y = rect.y + 24
    max_w = rect.w - 12

    # show last ~12 entries word-wrapped
    for line in lines[-12:]:
        y = _wrap_and_blit(
            surface,
            line,
            smallfont,
            COL_TEXT,
            max_w,
            rect.x + 6,
            y,
            line_gap=0,
        )


def draw_inspector(surface, rect, font, smallfont, snaps):
    """
    Inspector panel:
      - ctrl word + key flags per slot
      - NEW: "HP blk" (0x020..0x03F) bytes ABOVE
      - OLD: "CTRL blk" (0x050..0x08F) bytes BELOW
    """
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    title = "Inspector (0x020-0x03F HP blk, 0x050-0x08F CTRL blk)"
    surface.blit(font.render(title, True, COL_TEXT), (rect.x + 6, rect.y + 4))

    col_w  = rect.w // 4
    base_y = rect.y + 24

    # Render columns in fixed slot order so you can train your eyes.
    order = ["P1-C1", "P1-C2", "P2-C1", "P2-C2"]

    for i, slot in enumerate(order):
        subr_x = rect.x + i * col_w
        subr_y = base_y
        snap   = snaps.get(slot)

        if not snap:
            header_txt = f"{slot} [---]"
            surface.blit(
                smallfont.render(header_txt, True, COL_DIM),
                (subr_x + 4, subr_y),
            )
            continue

        cid = snap["id"]
        header_txt = f"{slot} {snap['name']} (ID:{cid})"
        surface.blit(
            smallfont.render(header_txt, True, COL_TEXT),
            (subr_x + 4, subr_y),
        )
        line_y = subr_y + smallfont.get_height() + 2

        # pull the decoded states for quick glance
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
                (subr_x + 4, line_y),
            )
            line_y += smallfont.get_height() + 2

        # ------------- NEW BLOCK: HP-neighborhood bytes (0x020..0x03F) -------------
        # snap["wires_hp"] is list[(off, byteval)]
        hp_pairs = snap.get("wires_hp", [])
        hp_chunks = []
        for off, b in hp_pairs:
            # Sanity range check
            if off < 0x000 or off >= 0x090:
                continue
            val = "--" if b is None else str(b)
            hp_chunks.append(f"{off:03X}:{val}")
        hp_blob = " ".join(hp_chunks)

        if hp_blob:
            # label row for the HP block
            surface.blit(
                smallfont.render("HP blk:", True, COL_ACCENT),
                (subr_x + 4, line_y),
            )
            line_y += smallfont.get_height() + 2

            # word-wrap the HP block dump
            words = hp_blob.split(" ")
            curr = ""
            while words:
                w = words.pop(0)
                test = curr + (" " if curr else "") + w
                if smallfont.size(test)[0] > (col_w - 8) and curr:
                    surface.blit(
                        smallfont.render(curr, True, COL_TEXT),
                        (subr_x + 4, line_y),
                    )
                    line_y += smallfont.get_height() + 2
                    curr = w
                else:
                    curr = test
            if curr:
                surface.blit(
                    smallfont.render(curr, True, COL_TEXT),
                    (subr_x + 4, line_y),
                )
                line_y += smallfont.get_height() + 4  # spacer after HP blk

        # ------------- ORIGINAL BLOCK: control bytes (0x050..0x08F) -------------
        main_pairs = snap.get("wires_main", [])
        main_chunks = []
        for off, b in main_pairs:
            if off < 0x050 or off >= 0x090:
                continue
            val = "--" if b is None else str(b)
            main_chunks.append(f"{off:03X}:{val}")
        main_blob = " ".join(main_chunks)

        # label row for the "classic" control/state region
        surface.blit(
            smallfont.render("CTRL blk:", True, COL_ACCENT),
            (subr_x + 4, line_y),
        )
        line_y += smallfont.get_height() + 2

        # word-wrap the CTRL block dump
        words = main_blob.split(" ")
        curr = ""
        for w in words:
            test = curr + (" " if curr else "") + w
            if smallfont.size(test)[0] > (col_w - 8) and curr:
                surface.blit(
                    smallfont.render(curr, True, COL_TEXT),
                    (subr_x + 4, line_y),
                )
                line_y += smallfont.get_height() + 2
                curr = w
            else:
                curr = test
        if curr:
            surface.blit(
                smallfont.render(curr, True, COL_TEXT),
                (subr_x + 4, line_y),
            )
            line_y += smallfont.get_height() + 2
