import pygame
from config import COL_PANEL, COL_BORDER, COL_TEXT
from events import event_log

try:
    from scan_normals_all import ANIM_MAP as SCAN_ANIM_MAP
except Exception:
    SCAN_ANIM_MAP = {}


def _pct_color(pct: float):
    if pct is None:
        return COL_TEXT
    if pct > 50.0:
        return (0, 200, 0)
    elif pct > 30.0:
        return (220, 200, 0)
    else:
        return (220, 60, 60)


def _hp_color(cur_hp, max_hp):
    if not max_hp or max_hp <= 0:
        return COL_TEXT
    pct = (cur_hp / max_hp) * 100.0
    return _pct_color(pct)


def _pool_color(pool_pct_val):
    if pool_pct_val is None:
        return COL_TEXT
    return _pct_color(pool_pct_val)


def _draw_rainbow_text(surface, text, font, pos):
    txt_surf = font.render(text, True, (255, 255, 255))
    w, h = txt_surf.get_size()
    grad = pygame.Surface((w, h), pygame.SRCALPHA)

    colors = [
        (255, 0, 0),
        (255, 128, 0),
        (255, 255, 0),
        (0, 255, 0),
        (0, 255, 255),
        (0, 0, 255),
        (255, 0, 255),
    ]
    n = len(colors)
    for x in range(w):
        t = x / max(1, w - 1)
        idx = int(t * (n - 1))
        c1 = colors[idx]
        c2 = colors[min(idx + 1, n - 1)]
        local_t = (t * (n - 1)) - idx
        r = int(c1[0] + (c2[0] - c1[0]) * local_t)
        g = int(c1[1] + (c2[1] - c1[1]) * local_t)
        b = int(c1[2] + (c2[2] - c1[2]) * local_t)
        pygame.draw.line(grad, (r, g, b, 255), (x, 0), (x, h))

    grad.blit(txt_surf, (0, 0), None, pygame.BLEND_RGBA_MULT)
    surface.blit(grad, pos)


def draw_panel_classic(surface, rect, snap, portrait_surf, font, smallfont, header_label):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    portrait_size = 64
    pad = 6

    if portrait_surf is not None:
        surface.blit(
            pygame.transform.smoothscale(portrait_surf, (portrait_size, portrait_size)),
            (rect.x + pad, rect.y + pad),
        )
        text_x0 = rect.x + pad + portrait_size + 6
    else:
        text_x0 = rect.x + pad

    y0 = rect.y

    if not snap:
        surface.blit(font.render(f"{header_label} ---", True, COL_TEXT), (text_x0, y0 + 4))
        return

    hdr = f"{header_label} {snap['name']} @{snap['base']:08X}"
    surface.blit(font.render(hdr, True, COL_TEXT), (text_x0, y0 + 4))

    cur_hp = snap["cur"]
    max_hp = snap["max"]
    meter_str = snap.get("meter_str", "--")
    hp_col = _hp_color(cur_hp, max_hp)
    surface.blit(
        font.render(f"HP {cur_hp}/{max_hp}    Meter:{meter_str}", True, hp_col),
        (text_x0, y0 + 24),
    )

    pool_pct_val = snap.get("pool_pct")
    pool_pct_str = f"{pool_pct_val:.1f}%" if pool_pct_val is not None else "--"
    pool_raw = snap.get("hp_pool_byte")
    m2b_raw = snap.get("mystery_2B")
    pool_col = _pool_color(pool_pct_val)
    surface.blit(
        font.render(
            f"POOL(02A): {pool_pct_str} raw:{pool_raw}   2B:{m2b_raw}",
            True,
            pool_col,
        ),
        (text_x0, y0 + 44),
    )

    ready = bool(snap.get("baroque_ready"))
    active_txt = "ON" if snap.get("baroque_active") else "off"
    r0, r1 = snap.get("baroque_ready_raw", (0, 0))
    f0, f1 = snap.get("baroque_active_dbg", (0, 0))

    baroque_line = f"BAROQUE ready:{'YES' if ready else 'no'} [{r0:02X},{r1:02X}] active:{active_txt} [{f0:02X},{f1:02X}]"
    if ready:
        _draw_rainbow_text(surface, baroque_line, smallfont, (text_x0, y0 + 64))
    else:
        surface.blit(
            smallfont.render(baroque_line, True, COL_TEXT),
            (text_x0, y0 + 64),
        )

    lastdmg = snap["last"] if snap["last"] is not None else 0
    surface.blit(
        font.render(
            f"Pos X:{snap['x']:.2f} Y:{(snap['y'] or 0.0):.2f}   LastDmg:{lastdmg}",
            True,
            COL_TEXT,
        ),
        (text_x0, y0 + 84),
    )

    shown_id = snap.get("mv_id_display", snap.get("attB", snap.get("attA")))
    shown_name = snap.get("mv_label", f"FLAG_{shown_id}")
    sub_id = snap.get("attB")
    surface.blit(
        font.render(f"MoveID:{shown_id} {shown_name}   sub:{sub_id}", True, COL_TEXT),
        (text_x0, y0 + 104),
    )


def draw_activity(surface, rect, font, adv_line):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)
    text = adv_line if adv_line else "Frame adv: --"
    surface.blit(font.render(text, True, COL_TEXT), (rect.x + 6, rect.y + 6))


def draw_event_log(surface, rect, font, smallfont):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    x = rect.x + 6
    y = rect.y + 4

    surface.blit(font.render("Events", True, COL_TEXT), (x, y))
    y += 18

    lines = event_log[-16:]
    for line in lines:
        surface.blit(smallfont.render(line, True, COL_TEXT), (x, y))
        y += 14
        if y > rect.bottom - 14:
            break


def _fmt_stun(val):
    if val is None:
        return "?"
    if val == 0x0C:
        return "10"
    if val == 0x0F:
        return "15"
    if val == 0x11:
        return "17"
    if val == 0x15:
        return "21"
    return str(val)


def draw_scan_normals(surface, rect, font, smallfont, scan_data):
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

        moves = slot.get("moves", [])
        moves_sorted = sorted(
            moves,
            key=lambda m: (
                m.get("id") is None,
                m.get("id", 0xFFFF),
                m.get("abs", 0xFFFFFFFF),
            ),
        )
        seen_names = set()
        shown = 0
        for mv in moves_sorted:
            if shown >= 5:
                break
            aid = mv.get("id")
            if aid is None:
                name = "anim_--"
            else:
                name = SCAN_ANIM_MAP.get(aid, f"anim_{aid:02X}")
            if name in seen_names:
                continue
            seen_names.add(name)

            hs = _fmt_stun(mv.get("hitstun"))
            bs = _fmt_stun(mv.get("blockstun"))
            adv_h = mv.get("adv_hit")
            adv_b = mv.get("adv_block")
            adv_h_txt = "" if adv_h is None else f"{adv_h:+d}"
            adv_b_txt = "" if adv_b is None else f"{adv_b:+d}"
            line = f"{name} H:{hs} B:{bs} {adv_h_txt}/{adv_b_txt}"
            surface.blit(smallfont.render(line, True, COL_TEXT), (col_x, col_y))
            col_y += 12
            shown += 1


def draw_slot_button(surface, rect, font, label):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=3)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=3)
    surface.blit(font.render(label, True, COL_TEXT), (rect.x + 4, rect.y + 2))
