import pygame
from config import (
    COL_PANEL, COL_BORDER, COL_TEXT,
    hp_color,
    BAROQUE_MONITOR_ADDR,
)

def decode_flag_062(v):
    return v, f"{v}"
def decode_flag_063(v):
    return v, f"{v}"


def draw_panel_classic(surface, rect, snap, meter_val, font, smallfont, header_label):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    x0 = rect.x + 6
    y0 = rect.y

    if not snap:
        surface.blit(font.render(f"{header_label} ---", True, COL_TEXT),(x0, y0 + 4))
        return

    # header
    hdr = f"{header_label} {snap['name']} @{snap['base']:08X}"
    surface.blit(font.render(hdr, True, COL_TEXT), (x0, y0 + 4))

    # HP / Meter
    cur_hp    = snap["cur"]
    max_hp    = snap["max"]
    pct       = (cur_hp / max_hp) if (max_hp and max_hp > 0) else None
    meter_str = str(meter_val) if meter_val is not None else "--"
    hp_line   = f"HP {cur_hp}/{max_hp}    Meter:{meter_str}"
    surface.blit(font.render(hp_line, True, hp_color(pct)), (x0, y0 + 24))

    # Pool + 2B
    pool_raw     = snap.get("hp_pool_byte")
    pool_dec     = str(pool_raw) if pool_raw is not None else "--"
    pool_pct_val = snap.get("pool_pct")
    pool_pct_str = f"{pool_pct_val:.1f}%" if pool_pct_val is not None else "--"
    m2b_raw      = snap.get("mystery_2B")
    m2b_dec      = str(m2b_raw) if m2b_raw is not None else "--"

    pool_line = (
        f"POOL(02A): {pool_pct_str} raw:{pool_dec}   "
        f"2B:{m2b_dec}"
    )
    surface.blit(font.render(pool_line, True, COL_TEXT), (x0, y0 + 44))

    # Baroque status line (with raw dbg bytes)
    ready_txt  = "YES" if snap.get("baroque_ready") else "no"
    active_txt = "ON"  if snap.get("baroque_active") else "off"

    r0, r1   = snap.get("baroque_ready_raw", (0,0))
    f0, f1   = snap.get("baroque_active_dbg", (0,0))

    bar_line = (
        f"BAROQUE ready:{ready_txt} [{r0 if r0 is not None else 0:02X},{r1 if r1 is not None else 0:02X}] "
        f"active:{active_txt} [{f0 if f0 is not None else 0:02X},{f1 if f1 is not None else 0:02X}]"
    )
    surface.blit(font.render(bar_line, True, COL_TEXT), (x0, y0 + 64))

    # Input monitor (raw A0/A1/A2)
    inputs_struct = snap.get("inputs", {})
    a0v = inputs_struct.get("A0", 0)
    a1v = inputs_struct.get("A1", 0)
    a2v = inputs_struct.get("A2", 0)
    inp_line = f"INPUT A0:{a0v:02X} A1:{a1v:02X} A2:{a2v:02X}"
    surface.blit(font.render(inp_line, True, COL_TEXT), (x0, y0 + 84))

    # Pos / last damage
    lastdmg  = snap["last"] if snap["last"] is not None else 0
    pos_line = (
        f"Pos X:{snap['x']:.2f} Y:{(snap['y'] or 0.0):.2f}   "
        f"LastDmg:{lastdmg}"
    )
    surface.blit(font.render(pos_line, True, COL_TEXT), (x0, y0 + 104))

    # Move line
    shown_id   = snap.get("mv_id_display", snap.get("attB", snap.get("attA")))
    shown_name = snap.get("mv_label", f"FLAG_{shown_id}")
    sub_id     = snap.get("attB")
    mv_line    = f"MoveID:{shown_id} {shown_name}   sub:{sub_id}"
    surface.blit(font.render(mv_line, True, COL_TEXT), (x0, y0 + 124))

    # Flags / ctrl
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
    surface.blit(font.render(row1, True, COL_TEXT), (x0, y0 + 144))

    row2 = f"072:{f072_val}   ctrl:{ctrl_hex}"
    surface.blit(font.render(row2, True, COL_TEXT), (x0, y0 + 164))

    surface.blit(font.render("impact:--", True, COL_TEXT), (x0, y0 + 184))


def draw_activity(surface, rect, font, adv_line):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    txt = adv_line if adv_line else "Frame adv: --"
    surface.blit(font.render(txt, True, COL_TEXT), (rect.x + 6, rect.y + 8))


def draw_event_log(surface, rect, font, smallfont):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    surface.blit(font.render("Recent hits / adv", True, COL_TEXT), (rect.x + 6, rect.y + 4))


def draw_inspector(surface, rect, font, smallfont, snaps, baroque_blob):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    x0 = rect.x + 6
    y  = rect.y + 4

    surface.blit(font.render("Inspector", True, COL_TEXT), (x0, y))
    y += 20

    for slotname in ("P1-C1", "P2-C1", "P1-C2", "P2-C2"):
        s = snaps.get(slotname)
        if not s:
            continue

        r0, r1 = s.get("baroque_ready_raw", (0,0))
        f0, f1 = s.get("baroque_active_dbg", (0,0))

        line_a = (
            f"{slotname} base:{s['base']:08X} "
            f"HP:{s['cur']}/{s['max']} "
            f"POOL:{s.get('hp_pool_byte')} "
            f"2B:{s.get('mystery_2B')} "
            f"READY:{'Y' if s.get('baroque_ready') else 'n'}[{r0 if r0 is not None else 0:02X},{r1 if r1 is not None else 0:02X}] "
            f"ACT:{'Y' if s.get('baroque_active') else 'n'}[{f0 if f0 is not None else 0:02X},{f1 if f1 is not None else 0:02X}]"
        )
        surface.blit(
            smallfont.render(line_a, True, COL_TEXT),
            (x0, y)
        )
        y += 16

        ctrl_hex = f"0x{(s['ctrl'] or 0):08X}"
        line_b = (
            f"ctrl:{ctrl_hex} "
            f"f062:{s['f062']} f063:{s['f063']} "
            f"A0:{s.get('inputs',{}).get('A0',0):02X} "
            f"A1:{s.get('inputs',{}).get('A1',0):02X} "
            f"A2:{s.get('inputs',{}).get('A2',0):02X}"
        )
        surface.blit(
            smallfont.render(line_b, True, COL_TEXT),
            (x0, y)
        )
        y += 16

    y += 8

    surface.blit(
        font.render(
            f"Baroque/Inputs block @ {BAROQUE_MONITOR_ADDR:08X}",
            True, COL_TEXT
        ),
        (x0, y)
    )
    y += 20

    if baroque_blob:
        preview_len = min(len(baroque_blob), 32)
        chunk = baroque_blob[:preview_len]
        half = (preview_len + 1) // 2

        row1 = " ".join(f"{b:02X}" for b in chunk[:half])
        row2 = " ".join(f"{b:02X}" for b in chunk[half:])

        surface.blit(
            smallfont.render(row1, True, COL_TEXT),
            (x0, y)
        )
        y += 16
        surface.blit(
            smallfont.render(row2, True, COL_TEXT),
            (x0, y)
        )
        y += 16
    else:
        surface.blit(
            smallfont.render("(no data)", True, COL_TEXT),
            (x0, y)
        )
        y += 16
