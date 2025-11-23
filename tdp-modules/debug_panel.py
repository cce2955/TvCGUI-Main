# debug_panel.py
#
# Debug flag helpers + drawing logic extracted from main.py.

import pygame

from dolphin_io import rd8
from config import COL_BG, DEBUG_FLAG_ADDRS


def read_debug_flags():
    """
    Returns list of (label, addr, value) for the debug panel.

    Uses config.DEBUG_FLAG_ADDRS plus the exact addresses:
      - HypeTrigger   @ 0x803FB9D9
      - ComboStore[1] @ 0x803FB949
      - SpecialPopup  @ 0x803FBA69
    """
    out = []

    # Base debug flags from config (PauseOverlay, PauseCtrl, Director, etc.)
    for label, addr in DEBUG_FLAG_ADDRS:
        try:
            val = rd8(addr)
        except Exception:
            val = None
        out.append((label, addr, val))

    # Exact addresses you found in MEM1/MEM2
    hype_addr = 0x803FB9D9
    try:
        hype_val = rd8(hype_addr)
    except Exception:
        hype_val = None
    out.append(("HypeTrigger", hype_addr, hype_val))

    combo1_addr = 0x803FB949
    try:
        combo1_val = rd8(combo1_addr)
    except Exception:
        combo1_val = None
    out.append(("ComboStore[1]", combo1_addr, combo1_val))

    sp_addr = 0x803FBA69
    try:
        sp_val = rd8(sp_addr)
    except Exception:
        sp_val = None
    out.append(("SpecialPopup", sp_addr, sp_val))

    return out


def draw_debug_overlay(surface, rect, font, values, scroll_offset):
    """
    Render the debug flag list inside a dedicated panel rectangle.

    scroll_offset: how many entries from 'values' to skip (for scrolling).

    Returns:
        click_areas: dict[label] -> (pygame.Rect, addr)
        max_scroll: maximum allowed scroll_offset for current rect/values.
    """
    # panel background + border
    pygame.draw.rect(surface, COL_BG, rect, border_radius=4)
    pygame.draw.rect(surface, (120, 120, 160), rect, 1, border_radius=4)

    click_areas = {}

    # Header position
    x = rect.x + 8
    y = rect.y + 32  # leave room for the Debug ON/OFF button row

    header = "Debug flags (rd8)"
    surface.blit(font.render(header, True, (220, 220, 220)), (x, y))
    y += 16

    # No values -> no scrolling needed
    if not values:
        return click_areas, 0

    line_height = 14
    y_start = y
    visible_px = (rect.bottom - 10) - y_start
    max_visible = max(0, visible_px // line_height)

    # Clamp scroll_offset to safe range
    total = len(values)
    max_scroll = max(0, total - max_visible)
    if scroll_offset < 0:
        scroll_offset = 0
    elif scroll_offset > max_scroll:
        scroll_offset = max_scroll

    # Slice the list by scroll offset
    visible_values = values[scroll_offset:scroll_offset + max_visible]

    for label, addr, val in visible_values:
        if y > rect.bottom - 10:
            break

        if val is None:
            vtxt = "--"
        else:
            vtxt = f"{val:02X}"

        line = f"{label}: {vtxt} @0x{addr:08X}"
        text_surf = font.render(line, True, (200, 200, 200))
        surface.blit(text_surf, (x, y))

        text_rect = text_surf.get_rect(topleft=(x, y))
        click_areas[label] = (text_rect, addr)

        y += line_height

    return click_areas, max_scroll
