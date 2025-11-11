# hud_draw.py
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


def _rainbow_color(t_ms: int, step: int = 0):
    # simple cycling hsv-ish fake
    # t_ms is pygame.time.get_ticks()
    # step offsets the color so multiple lines look animated
    base = (t_ms // 80 + step * 20) % 360
    # tiny hsv→rgb
    h = base / 60.0
    c = 255
    x = int((1 - abs((h % 2) - 1)) * c)
    if 0 <= h < 1:
        r, g, b = c, x, 0
    elif 1 <= h < 2:
        r, g, b = x, c, 0
    elif 2 <= h < 3:
        r, g, b = 0, c, x
    elif 3 <= h < 4:
        r, g, b = 0, x, c
    elif 4 <= h < 5:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    return (r, g, b)


def _blit_rainbow_text(surface, text, pos, font, t_ms):
    # draw character-by-character with shifting color
    x, y = pos
    for i, ch in enumerate(text):
        col = _rainbow_color(t_ms, i)
        img = font.render(ch, True, col)
        surface.blit(img, (x, y))
        x += img.get_width()


def draw_panel_classic(surface, rect, snap, portrait_surf, font, smallfont, header_label, t_ms=0):
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
        font.render(f"HP {cur_hp}/{max_hp}     Meter:{meter_str}", True, hp_col),
        (text_x0, y0 + 24),
    )

    # old small pool byte still shown
    pool_pct_val = snap.get("pool_pct")
    pool_pct_str = f"{pool_pct_val:.1f}%" if pool_pct_val is not None else "--"
    pool_raw = snap.get("hp_pool_byte")
    surface.blit(
        font.render(f"POOL(02A): {pool_pct_str}  raw:{pool_raw}", True, COL_TEXT),
        (text_x0, y0 + 44),
    )

    # NEW: real baroque
    hp32 = snap.get("baroque_local_hp32", 0)
    pool32 = snap.get("baroque_local_pool32", 0)
    red_amt = snap.get("baroque_red_amt", 0)
    red_pct = snap.get("baroque_red_pct", 0.0)
    ready_local = snap.get("baroque_ready_local", False)

    if ready_local:
        txt = f"Baroque: READY  red:{red_amt} ({red_pct:.1f}%)  HP32:{hp32} POOL32:{pool32}"
        _blit_rainbow_text(surface, txt, (text_x0, y0 + 62), smallfont, t_ms)
    else:
        txt = f"Baroque: off     HP32:{hp32} POOL32:{pool32}"
        surface.blit(smallfont.render(txt, True, COL_TEXT), (text_x0, y0 + 62))


def draw_activity(surface, rect, font, text):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=3)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=3)
    if text:
        surface.blit(font.render(text, True, COL_TEXT), (rect.x + 6, rect.y + 4))


def draw_event_log(surface, rect, font, smallfont):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=3)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=3)
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


def draw_scan_normals(surface, rect, font, smallfont, scan_data):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=3)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=3)

    x = rect.x + 6
    y = rect.y + 4

    surface.blit(font.render("Scan: normals (preview)", True, COL_TEXT), (x, y))
    y += 18

    if not scan_data:
        surface.blit(smallfont.render("No scan data", True, COL_TEXT), (x, y))
        return

    for slot in scan_data:
        label = slot.get("slot_label", "?")
        cname = slot.get("char_name", "—")
        surface.blit(smallfont.render(f"{label} ({cname})", True, COL_TEXT), (x, y))
        y += 14
        moves = slot.get("moves", [])
        for mv in moves[:4]:
            aid = mv.get("id")
            name = SCAN_ANIM_MAP.get(aid, f"anim_{aid:02X}" if aid is not None else "???")
            hs = mv.get("hitstun")
            bs = mv.get("blockstun")
            surface.blit(
                smallfont.render(f"{name} H:{hs} B:{bs}", True, COL_TEXT),
                (x + 12, y),
            )
            y += 14
        y += 4
        if y > rect.bottom - 14:
            break
