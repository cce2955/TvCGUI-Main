# debug_panel.py
#
# Helper routines for reading a curated set of runtime flags from memory
# and drawing them in an inspector-style overlay. This file keeps all
# debug-panel logic out of main.py so the main loop stays focused on
# gameplay-state tracking and rendering.


import pygame

from dolphin_io import rd8
from config import COL_BG, DEBUG_FLAG_ADDRS


def read_debug_flags():
    """
    Collect a list of (label, addr, value) entries for the debug overlay.

    DEBUG_FLAG_ADDRS covers the generic toggles (pause overlay, director block,
    etc.). In addition, we expose a few single-byte addresses found through
    memory tracing that correspond to hype, combo popup behavior, and special
    effect triggers.

    Returns:
        A list of tuples: (label, address, byte_value or None)
    """
    out = []

    # Main debug flags defined in config.py
    for label, addr in DEBUG_FLAG_ADDRS:
        try:
            val = rd8(addr)
        except Exception:
            val = None
        out.append((label, addr, val))

    # Individually mapped flags discovered during reverse-engineering.
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
    Draw the debug flag inspector.

    The overlay shows a scrollable list of (label, value, address) pairs,
    with each row clickable so that other parts of the HUD can toggle
    the corresponding byte in memory.

    Arguments:
        surface        The pygame Surface to draw on.
        rect           The region allocated for the debug panel.
        font           Font object for rendering text.
        values         Output of read_debug_flags().
        scroll_offset  Number of rows to skip from the top.

    Returns:
        click_areas : dict mapping label → (pygame.Rect, address)
                      Used for hit-testing click toggles.

        max_scroll  : Maximum valid value for scroll_offset given panel height.
    """
    # Panel base and border
    pygame.draw.rect(surface, COL_BG, rect, border_radius=4)
    pygame.draw.rect(surface, (120, 120, 160), rect, 1, border_radius=4)

    click_areas = {}

    x = rect.x + 8
    y = rect.y + 32   # leave room for the ON/OFF button printed in main HUD

    header = "Debug flags (rd8)"
    surface.blit(font.render(header, True, (220, 220, 220)), (x, y))
    y += 16

    # No data → no scrolling
    if not values:
        return click_areas, 0

    line_height = 14
    y_start = y
    visible_px = (rect.bottom - 10) - y_start
    max_visible = max(0, visible_px // line_height)

    total = len(values)
    max_scroll = max(0, total - max_visible)

    # Clamp scroll_offset once we know the valid range
    scroll_offset = max(0, min(scroll_offset, max_scroll))

    # Take the visible window of rows
    visible_values = values[scroll_offset:scroll_offset + max_visible]

    for label, addr, val in visible_values:
        if y > rect.bottom - 10:
            break

        vtxt = "--" if val is None else f"{val:02X}"
        line = f"{label}: {vtxt} @0x{addr:08X}"

        text_surf = font.render(line, True, (200, 200, 200))
        surface.blit(text_surf, (x, y))

        # Record bounding rect for click detection
        text_rect = text_surf.get_rect(topleft=(x, y))
        click_areas[label] = (text_rect, addr)

        y += line_height

    return click_areas, max_scroll
