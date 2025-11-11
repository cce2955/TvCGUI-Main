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


def draw_panel_classic(surface, rect, snap, portrait_surf, font, smallfont, header_label, t_ms=0):
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    pad = 6
    portrait_size = 64

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

    pool_pct_val = snap.get("pool_pct")
    pool_pct_str = f"{pool_pct_val:.1f}%" if pool_pct_val is not None else "--"
    pool_raw = snap.get("hp_pool_byte")
    surface.blit(
        font.render(f"POOL(02A): {pool_pct_str}  raw:{pool_raw}", True, COL_TEXT),
        (text_x0, y0 + 44),
    )

    hp32 = snap.get("baroque_local_hp32", 0)
    pool32 = snap.get("baroque_local_pool32", 0)
    red_amt = snap.get("baroque_red_amt", 0)
    red_pct = snap.get("baroque_red_pct", 0.0)
    ready_local = snap.get("baroque_ready_local", False)

    if ready_local:
        txt = f"Baroque: READY red:{red_amt} ({red_pct:.1f}%)"
        _blit_rainbow_text(surface, txt, (text_x0, y0 + 62), smallfont, t_ms)
    else:
        txt = f"Baroque: off  HP32:{hp32} POOL32:{pool32}"
        surface.blit(smallfont.render(txt, True, COL_TEXT), (text_x0, y0 + 62))

    mv_label = snap.get("mv_label", "—")
    mv_id = snap.get("mv_id_display")
    if mv_id is not None:
        mv_text = f"Move: {mv_label} ({mv_id})"
    else:
        mv_text = f"Move: {mv_label}"
    surface.blit(smallfont.render(mv_text, True, COL_TEXT), (text_x0, y0 + 80))


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


# ------------------------------------------------------------
# REFACTORED: scan normals preview
# ------------------------------------------------------------
# moves we actually care about in the crowded bottom box
PREVIEW_MOVES_ORDERED = [
    "5A", "5B", "5C",
    "2A", "2B", "2C",
    "3C",
    "j.A", "j.B", "j.C",
]


def _normalize_move_name(aid, mv_dict):
    """
    Try to get a human name for this move.
    1) use scan_normals_all map if aid is present
    2) fallback to mv['name'] if present
    3) fallback to "anim_XX"
    """
    if aid is not None and aid in SCAN_ANIM_MAP:
        return SCAN_ANIM_MAP[aid]
    # some scanners store name / move_name / anim_name
    for key in ("name", "move_name", "anim_name"):
        if key in mv_dict and mv_dict[key]:
            return mv_dict[key]
    if aid is not None:
        return f"anim_{aid:02X}"
    return "???"


def draw_scan_normals(surface, rect, font, smallfont, scan_data):
    """
    Draws a compact, filtered normals preview:
    only S, A, H, B
    only these moves: 5A 5B 5C 2A 2B 2C 3C j.A j.B j.C
    """
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=3)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=3)

    x0 = rect.x + 4
    y0 = rect.y + 4
    surface.blit(font.render("Scan: normals (preview)", True, COL_TEXT), (x0, y0))
    y0 += 18

    if not scan_data:
        surface.blit(smallfont.render("No scan data", True, COL_TEXT), (x0, y0))
        return

    n_slots = len(scan_data)
    col_w = max(130, rect.width // max(1, n_slots))

    for col, slot in enumerate(scan_data):
        col_x = rect.x + 4 + col * col_w
        y = y0
        label = slot.get("slot_label", "?")
        cname = slot.get("char_name", "—")
        surface.blit(smallfont.render(f"{label} ({cname})", True, COL_TEXT), (col_x, y))
        y += 14

        # build a dict of {normalized_name: move_dict} for quick lookup
        # while keeping the first occurrence only (like you did before)
        name_to_mv = {}
        seen_ids = set()
        for mv in slot.get("moves", []):
            aid = mv.get("id")
            if aid is not None:
                if aid in seen_ids:
                    continue
                seen_ids.add(aid)
            nice = _normalize_move_name(aid, mv)
            # only keep the first occurrence
            if nice not in name_to_mv:
                name_to_mv[nice] = mv

        # now render exactly in PREVIEW_MOVES_ORDERED
        for wanted in PREVIEW_MOVES_ORDERED:
            mv = name_to_mv.get(wanted)
            if not mv:
                # sometimes you have "jA" or "j.A" mismatch, so try a tiny fallback
                if wanted.startswith("j.") and wanted[2:] in name_to_mv:
                    mv = name_to_mv[wanted[2:]]
                elif wanted.startswith("j") and ("j." + wanted[1:]) in name_to_mv:
                    mv = name_to_mv["j." + wanted[1:]]
            if not mv:
                continue

            aid = mv.get("id")
            name = wanted  # we already know what we asked for

            s = mv.get("active_start") or mv.get("startup")  # some scans store it differently
            a0 = mv.get("active_start")
            a1 = mv.get("active_end")
            if a0 is not None and a1 is not None:
                a_txt = f"{a0}-{a1}"
            elif a1 is not None:
                a_txt = str(a1)
            else:
                a_txt = "-"

            hs = mv.get("hitstun")
            bs = mv.get("blockstun")

            line = (
                f"{name} "
                f"S:{s if s is not None else '-'} "
                f"A:{a_txt} "
                f"H:{hs if hs is not None else '-'} "
                f"B:{bs if bs is not None else '-'}"
            )

            surface.blit(smallfont.render(line, True, COL_TEXT), (col_x + 10, y))
            y += 14
            if y > rect.bottom - 14:
                break
