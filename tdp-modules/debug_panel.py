# debug_panel.py
#
# Read a curated set of runtime flags from memory and render them
# in a scrollable, click-friendly overlay.

import pygame

from dolphin_io import rd8
from config import COL_PANEL, COL_BORDER, COL_TEXT, DEBUG_FLAG_ADDRS

DISPLAY_LABEL_OVERRIDES = {
    "PauseOverlay":   "Pause rendering",
    "P2Pause":        "P2 pause (Warning: Buggy)",
    "HypeTrigger":    "Combo announcer",
    "ComboCountOnly": "Combo counter only",
    "ComboStore[1]":  "Last Combo + dmg",
    "TrPause":        "Pause",
    "Orientation":    "Orientation",
    "SuperBG":        "Super background",
}


def read_debug_flags():
    """
    Collect a list of (label, addr, value) entries for the debug overlay.

    DEBUG_FLAG_ADDRS comes from config.py and is a list of (label, addr).
    We also append a few individually mapped flags discovered during
    reverse-engineering.

    Returns:
        A list of tuples: (label, address, byte_value or None)
    """
    out = []

    # Main debug flags defined in config.py
    for label, addr in DEBUG_FLAG_ADDRS:
        if not isinstance(addr, int):
            out.append((label, addr, None))
            continue
        try:
            val = rd8(addr)
        except Exception:
            val = None
        out.append((label, addr, val))


    # Individually mapped flags discovered during reverse-engineering.

    # 1) P2 pause flag (independent of P1 on our side)
    p2pause_addr = 0x803F563B
    try:
        p2pause_val = rd8(p2pause_addr)
    except Exception:
        p2pause_val = None
    out.append(("P2Pause", p2pause_addr, p2pause_val))

    # 2) Combo announcer trigger
    hype_addr = 0x803FB9D9
    try:
        hype_val = rd8(hype_addr)
    except Exception:
        hype_val = None
    out.append(("HypeTrigger", hype_addr, hype_val))

    # 3) Combo counter only (just the count, no damage)
    combo_count_addr = 0x803FB959
    try:
        combo_count_val = rd8(combo_count_addr)
    except Exception:
        combo_count_val = None
    out.append(("ComboCountOnly", combo_count_addr, combo_count_val))

    # 4) Last combo + damage
    combo1_addr = 0x803FB949
    try:
        combo1_val = rd8(combo1_addr)
    except Exception:
        combo1_val = None
    out.append(("ComboStore[1]", combo1_addr, combo1_val))

    # 5) Special popup
    sp_addr = 0x803FBA69
    try:
        sp_val = rd8(sp_addr)
    except Exception:
        sp_val = None
    out.append(("SpecialPopup", sp_addr, sp_val))

    return out


def _format_value(v):
    """
    Format a small integer as both hex and decimal.
    """
    if v is None:
        return "--"
    if not isinstance(v, int):
        return str(v)
    if v < 0:
        return str(v)
    if v <= 0xFF:
        return f"{v:02X} ({v:d})"
    return f"{v:08X} ({v:d})"


def _state_label(name: str, v):
    """
    Human-friendly ON/OFF state for a given flag.

    For most flags we treat 0 as OFF and non-zero as ON.

    For the three momentary debug flags we special-case the values
    that we actively write from main.py:

        - HypeTrigger / ComboAnnouncer: 0x40 = ON, anything else = OFF
        - ComboStore[1] / Last Combo + dmg: 0x41 = ON, anything else = OFF
        - SpecialPopup: 0x40 = ON, anything else = OFF
    """
    if v is None:
        return "--"

    if name in ("HypeTrigger", "ComboAnnouncer"):
        return "ON" if v == 0x40 else "OFF"

    if name in ("ComboStore[1]", "Last Combo + dmg"):
        return "ON" if v == 0x41 else "OFF"

    if name == "SpecialPopup":
        return "ON" if v == 0x40 else "OFF"

    # Default: simple boolean interpretation (also fine for P2Pause)
    return "ON" if int(v) != 0 else "OFF"


def draw_debug_overlay(surface, rect, font_small, dbg_values, scroll_offset):
    """
    Draw a scrollable list of debug / training flags.

    dbg_values is a list of tuples (name, addr, value). We only rely on:
        name = entry[0]
        addr = entry[1]
        val  = entry[2]

    Returns:
        click_areas: dict[name] = (rect, addr)
        max_scroll:  maximum scroll offset based on rows.
    """
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    x0 = rect.x + 6
    y0 = rect.y + 4

    header = "Debug flags (rd8)"
    surface.blit(font_small.render(header, True, COL_TEXT), (x0, y0))
    y0 += font_small.get_height() + 2

    hint = "Wheel to scroll, click a row to toggle/cycle"
    surface.blit(font_small.render(hint, True, (170, 170, 190)), (x0, y0))
    y0 += font_small.get_height() + 4

    inner_top = y0
    inner_bottom = rect.bottom - 4
    row_h = font_small.get_height() + 4
    max_rows = max(1, (inner_bottom - inner_top) // row_h)

    click_areas = {}

    if not dbg_values:
        surface.blit(
            font_small.render("(no debug flags)", True, COL_TEXT),
            (x0, inner_top),
        )
        return click_areas, 0

    total = len(dbg_values)
    max_scroll = max(0, total - max_rows)

    if scroll_offset < 0:
        scroll_offset = 0
    elif scroll_offset > max_scroll:
        scroll_offset = max_scroll

    start = scroll_offset
    end = min(total, start + max_rows)

    row_x = rect.x + 4
    row_w = rect.width - 8

    for idx in range(start, end):
        name, addr, val = dbg_values[idx][0], dbg_values[idx][1], dbg_values[idx][2]
        disp_name = DISPLAY_LABEL_OVERRIDES.get(name, name)
        row_y = inner_top + (idx - start) * row_h

        active = bool(val)
        if active:
            bg_col = (55, 65, 105)
        else:
            bg_col = (32, 32, 40) if (idx % 2) else (26, 26, 32)

        row_rect = pygame.Rect(row_x, row_y, row_w, row_h)
        pygame.draw.rect(surface, bg_col, row_rect, border_radius=2)

        pygame.draw.line(
            surface,
            (60, 60, 70),
            (row_x, row_y + row_h - 1),
            (row_x + row_w, row_y + row_h - 1),
            1,
        )

        label_surf = font_small.render(disp_name, True, COL_TEXT)
        surface.blit(label_surf, (row_x + 4, row_y + 2))

        state_s = _state_label(name, val)
        state_surf = font_small.render(state_s, True, COL_TEXT)
        sx = row_x + row_w - state_surf.get_width() - 4
        surface.blit(state_surf, (sx, row_y + 2))

        val_s = _format_value(val)
        val_surf = font_small.render(val_s, True, COL_TEXT)
        vx = sx - val_surf.get_width() - 8
        surface.blit(val_surf, (vx, row_y + 2))

        click_areas[name] = (row_rect, addr)

    if max_scroll > 0:
        bar_area_h = inner_bottom - inner_top
        bar_x = rect.right - 3
        bar_y = inner_top

        pygame.draw.line(
            surface,
            (80, 80, 110),
            (bar_x, bar_y),
            (bar_x, inner_bottom),
            2,
        )

        frac = scroll_offset / float(max_scroll) if max_scroll > 0 else 0.0
        thumb_h = max(10, bar_area_h // max_rows)
        thumb_y = int(bar_y + frac * (bar_area_h - thumb_h))

        pygame.draw.rect(
            surface,
            (180, 180, 220),
            pygame.Rect(bar_x - 1, thumb_y, 3, thumb_h),
            border_radius=2,
        )

    return click_areas, max_scroll
