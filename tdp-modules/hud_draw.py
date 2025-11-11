import pygame
from config import COL_PANEL, COL_BORDER, COL_TEXT
from events import event_log

# we may or may not have the anim map, but try:
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
    base = (t_ms // 80 + step * 20) % 360
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
    x, y = pos
    for i, ch in enumerate(text):
        col = _rainbow_color(t_ms, i)
        glyph = font.render(ch, True, col)
        surface.blit(glyph, (x, y))
        x += glyph.get_width()


def draw_soft_shadow(target_surf: pygame.Surface, rect: pygame.Rect, alpha: int = 110, spread: int = 10):
    # very lightweight "shadow": draw a semi-transparent rect slightly offset
    shadow_surf = pygame.Surface((rect.width + spread * 2, rect.height + spread * 2), pygame.SRCALPHA)
    shadow_color = (0, 0, 0, alpha)
    pygame.draw.rect(
        shadow_surf,
        shadow_color,
        pygame.Rect(spread, spread, rect.width, rect.height),
        border_radius=16,
    )
    target_surf.blit(shadow_surf, (rect.x - spread, rect.y - spread))


def draw_panel_modern(surface, rect, snap, portrait_surf, font, smallfont, header_label, t_ms=0):
    # modern glassy-ish panel
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=16)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=16)

    pad_x = 14
    pad_y = 10
    portrait_size = 58

    text_x0 = rect.x + pad_x
    text_y0 = rect.y + pad_y

    if portrait_surf is not None:
        portrait = pygame.transform.smoothscale(portrait_surf, (portrait_size, portrait_size))
        surface.blit(portrait, (rect.x + pad_x, rect.y + pad_y))
        text_x0 = rect.x + pad_x + portrait_size + 10

    if not snap:
        # header only
        hdr = font.render(f"{header_label}", True, COL_TEXT)
        surface.blit(hdr, (text_x0, text_y0))
        return

    # header
    name = snap.get("name", "—")
    base_addr = snap.get("base", 0)
    hdr = font.render(f"{name}", True, COL_TEXT)
    sub = smallfont.render(f"{header_label} 0x{base_addr:08X}", True, (180, 180, 180))
    surface.blit(hdr, (text_x0, text_y0))
    surface.blit(sub, (text_x0, text_y0 + 20))

    # HP + meter
    cur_hp = snap.get("cur", 0)
    max_hp = snap.get("max", 1)
    hp_col = _hp_color(cur_hp, max_hp)
    meter_str = snap.get("meter_str", "--")

    hp_line = font.render(f"HP {cur_hp}/{max_hp}", True, hp_col)
    meter_line = smallfont.render(f"Meter {meter_str}", True, COL_TEXT)
    surface.blit(hp_line, (text_x0, text_y0 + 44))
    surface.blit(meter_line, (text_x0, text_y0 + 64))

    # pool and baroque
    pool_pct_val = snap.get("pool_pct")
    pool_raw = snap.get("hp_pool_byte")
    if pool_pct_val is not None:
        pool_line = smallfont.render(f"Pool 02A {pool_pct_val:.1f}%  raw:{pool_raw}", True, COL_TEXT)
        surface.blit(pool_line, (text_x0, text_y0 + 82))

    ready_local = snap.get("baroque_ready_local", False)
    red_amt = snap.get("baroque_red_amt", 0)
    red_pct = snap.get("baroque_red_pct", 0.0)
    if ready_local:
        txt = f"Baroque READY  red:{red_amt} ({red_pct:.1f}%)"
        _blit_rainbow_text(surface, txt, (text_x0, text_y0 + 100), smallfont, t_ms)
    else:
        baro_txt = f"Baroque off"
        surface.blit(smallfont.render(baro_txt, True, COL_TEXT), (text_x0, text_y0 + 100))

    # current move
    mv_label = snap.get("mv_label", "—")
    mv_id = snap.get("mv_id_display")
    if mv_id is not None:
        mv_text = f"Move: {mv_label} ({mv_id})"
    else:
        mv_text = f"Move: {mv_label}"
    surface.blit(smallfont.render(mv_text, True, COL_TEXT), (text_x0, text_y0 + 118))


def draw_activity(surface, rect, font, text):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=14)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=14)
    if text:
        surface.blit(font.render(text, True, COL_TEXT), (rect.x + 10, rect.y + 6))


def draw_event_log(surface, rect, font, smallfont):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=14)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=14)
    x = rect.x + 10
    y = rect.y + 6
    surface.blit(font.render("Events", True, COL_TEXT), (x, y))
    y += 20
    lines = event_log[-16:]
    for line in lines:
        surface.blit(smallfont.render(line, True, COL_TEXT), (x, y))
        y += 16
        if y > rect.bottom - 14:
            break


def draw_scan_normals(surface, rect, font, smallfont, scan_data):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=14)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=14)

    x0 = rect.x + 10
    y0 = rect.y + 6
    surface.blit(font.render("Scan: normals (preview)", True, COL_TEXT), (x0, y0))
    y0 += 22

    if not scan_data:
        surface.blit(smallfont.render("No scan data", True, COL_TEXT), (x0, y0))
        return

    # 1 column per slot
    n_slots = len(scan_data)
    col_w = max(120, rect.width // max(1, n_slots))

    for col, slot in enumerate(scan_data):
        col_x = rect.x + 10 + col * col_w
        y = y0
        label = slot.get("slot_label", "?")
        cname = slot.get("char_name", "—")
        surface.blit(smallfont.render(f"{label} ({cname})", True, COL_TEXT), (col_x, y))
        y += 16

        seen_ids = set()
        moves = []
        for mv in slot.get("moves", []):
            aid = mv.get("id")
            if aid is not None:
                if aid in seen_ids:
                    continue
                seen_ids.add(aid)
            moves.append(mv)

        for mv in moves[:7]:
            aid = mv.get("id")
            name = SCAN_ANIM_MAP.get(aid, f"anim_{aid:02X}" if aid is not None else "???")
            s = mv.get("active_start")
            e = mv.get("active_end")
            hs = mv.get("hitstun")
            bs = mv.get("blockstun")

            # match GUI's human stun values
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

            active_txt = f"{s}-{e}" if (s is not None and e is not None) else ""
            line = f"{name} S:{s or ''} A:{active_txt} H:{_fmt_stun(hs)} B:{_fmt_stun(bs)}"
            surface.blit(smallfont.render(line, True, COL_TEXT), (col_x + 6, y))
            y += 14
            if y > rect.bottom - 16:
                break
