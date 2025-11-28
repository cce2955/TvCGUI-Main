# debug_panel.py
#
# Read a curated set of runtime flags from memory and render them
# in a scrollable, click-friendly overlay.

import pygame

from dolphin_io import rd8
from config import COL_PANEL, COL_BORDER, COL_TEXT, DEBUG_FLAG_ADDRS

# User-friendly label overrides
DISPLAY_LABEL_OVERRIDES = {
    "PauseOverlay":      "Pause rendering",
    "HypeTrigger":       "Combo announcer",
    "ComboStore[1]":     "Last Combo + dmg",
    "TrPause":           "Pause",
}

def read_debug_flags():
    """
    Collect a list of (label, addr, value) entries for the debug overlay.
    """
    out = []

    # Main debug flags defined in config.py
    for label, addr in DEBUG_FLAG_ADDRS:
        try:
            val = rd8(addr)
        except Exception:
            val = None
        out.append((label, addr, val))

    # Individually mapped flags discovered during RE work
    hype_addr = 0x803FB9D9
    try: hype_val = rd8(hype_addr)
    except: hype_val = None
    out.append(("HypeTrigger", hype_addr, hype_val))

    combo1_addr = 0x803FB949
    try: combo1_val = rd8(combo1_addr)
    except: combo1_val = None
    out.append(("ComboStore[1]", combo1_addr, combo1_val))

    sp_addr = 0x803FBA69
    try: sp_val = rd8(sp_addr)
    except: sp_val = None
    out.append(("SpecialPopup", sp_addr, sp_val))

    return out


# ------------------------------------------------------------
# Formatting helpers
# ------------------------------------------------------------

def _format_value(v):
    """Format hex/decimal as always."""
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
    Produce the ON/OFF/Mode text that now appears between name and hex value.
    """

    if v is None:
        return "--"

    # -------------------------
    # Basic renamed debug flags
    # -------------------------
    if name in ("PauseOverlay",):      # Pause rendering
        return "ON" if v != 0 else "OFF"

    if name in ("TrPause",):           # Pause
        return "ON" if v != 0 else "OFF"

    # -------------------------
    # Combo-style momentary flags
    # -------------------------
    if name in ("HypeTrigger", "ComboAnnouncer", "Combo announcer"):
        return "ON" if v == 0x40 else "OFF"

    if name in ("ComboStore[1]", "Last Combo + dmg"):
        return "ON" if v == 0x41 else "OFF"

    if name == "SpecialPopup":
        return "ON" if v == 0x40 else "OFF"

    # -------------------------
    # Training mode flags
    # -------------------------

    if name in ("DummyMeter",):
        if v == 0: return "OFF"
        if v == 1: return "Recovery"
        if v == 2: return "Infinite"
        return str(v)

    if name in ("P1Meter",):
        if v == 0: return "OFF"
        if v == 1: return "Recovery"
        if v == 2: return "Infinite"
        return str(v)

    if name in ("P1Life",):
        if v == 0: return "OFF"
        if v == 1: return "Recover"
        if v == 2: return "Infinite"
        return str(v)

    if name == "CpuAction":
        mapping = {
            0: "OFF",
            1: "Crouch",
            2: "Jump",
            3: "Super jump",
            4: "CPU control",
            5: "Player control",
        }
        return mapping.get(v, str(v))

    if name == "CpuGuard":
        if v == 0: return "OFF"
        if v == 1: return "Auto guard"
        if v == 2: return "All guard"
        return str(v)


    if name == "CpuPushblock":
        return "ON" if v != 0 else "OFF"

    if name == "CpuThrowTech":
        return "ON" if v != 0 else "OFF"

    if name == "FreeBaroque":
        return "ON" if v != 0 else "OFF"

    if name == "BaroquePct":
        if v == 0:
            return "OFF"
        # value 05 = 50%, etc.
        return f"{v * 10}%"

    if name == "AttackData":
        return "ON" if v != 0 else "OFF"

    if name == "InputDisplay":
        return "ON" if v != 0 else "OFF"

    if name == "CpuDifficulty":
        # decode same way you cycle it
        level = (v // 0x20) % 8
        return f"Lv {level + 1}"

    if name == "DamageOutput":
        # 4 levels, starts at 2
        lvl = (v & 0x03)
        return f"Lv {lvl}"

    # -------------------------
    # Default fallback
    # -------------------------
    return "ON" if v != 0 else "OFF"


# ------------------------------------------------------------
# Rendering
# ------------------------------------------------------------

def draw_debug_overlay(surface, rect, font_small, dbg_values, scroll_offset):
    """
    Scrollable panel of flags.
    Now shows: NAME | STATE | HEX(DEC)
    """
    pygame.draw.rect(surface, COL_PANEL, rect, border_radius=4)
    pygame.draw.rect(surface, COL_BORDER, rect, 1, border_radius=4)

    x0 = rect.x + 6
    y0 = rect.y + 4

    # Header
    header = "Debug flags (rd8)"
    surface.blit(font_small.render(header, True, COL_TEXT), (x0, y0))
    y0 += font_small.get_height() + 2

    hint = "Wheel to scroll, click a row to toggle/cycle"
    surface.blit(font_small.render(hint, True, (170,170,190)), (x0, y0))
    y0 += font_small.get_height() + 4

    inner_top = y0
    inner_bottom = rect.bottom - 4
    row_h = font_small.get_height() + 4
    max_rows = max(1, (inner_bottom - inner_top) // row_h)

    click_areas = {}

    if not dbg_values:
        surface.blit(font_small.render("(no debug flags)", True, COL_TEXT),
                     (x0, inner_top))
        return click_areas, 0

    total = len(dbg_values)
    max_scroll = max(0, total - max_rows)

    scroll_offset = max(0, min(scroll_offset, max_scroll))

    start = scroll_offset
    end = min(total, start + max_rows)

    row_x = rect.x + 4
    row_w = rect.width - 8

    inserted_training_header = False

    for idx in range(start, end):

        name, addr, val = dbg_values[idx]
        disp_name = DISPLAY_LABEL_OVERRIDES.get(name, name)

        row_y = inner_top + (idx - start) * row_h

        # Insert help row above DummyMeter (only once)
        if name == "DummyMeter" and not inserted_training_header:
            help_rect = pygame.Rect(row_x, row_y, row_w, row_h)
            pygame.draw.rect(surface, (32,32,40), help_rect, border_radius=2)
            help_surf = font_small.render("Training mode only", True, (200,200,200))
            surface.blit(help_surf, (row_x + 4, row_y + 2))
            row_y += row_h
            inserted_training_header = True

        # Highlight rules
        active = (val is not None and val != 0)
        bg_col = (55,65,105) if active else ((32,32,40) if (idx % 2) else (26,26,32))

        row_rect = pygame.Rect(row_x, row_y, row_w, row_h)
        pygame.draw.rect(surface, bg_col, row_rect, border_radius=2)

        pygame.draw.line(surface, (60,60,70),
                         (row_x, row_y + row_h - 1),
                         (row_x + row_w, row_y + row_h - 1), 1)

        # Left: label
        label_surf = font_small.render(disp_name, True, COL_TEXT)
        surface.blit(label_surf, (row_x + 4, row_y + 2))

        # Middle: state word
        state_s = _state_label(disp_name, val)
        state_surf = font_small.render(state_s, True, COL_TEXT)
        surface.blit(state_surf, (row_x + 180, row_y + 2))

        # Right: hex/dec
        val_surf = font_small.render(_format_value(val), True, COL_TEXT)
        surface.blit(val_surf, (row_x + row_w - val_surf.get_width() - 4, row_y + 2))

        click_areas[name] = (row_rect, addr)

    # Scroll thumb
    if max_scroll > 0:
        bar_area_h = inner_bottom - inner_top
        bar_x = rect.right - 3
        bar_y = inner_top

        pygame.draw.line(surface, (80,80,110),
                         (bar_x, bar_y), (bar_x, inner_bottom), 2)

        frac = scroll_offset / float(max_scroll)
        thumb_h = max(10, bar_area_h // max_rows)
        thumb_y = int(bar_y + frac * (bar_area_h - thumb_h))

        pygame.draw.rect(surface, (180,180,220),
                         pygame.Rect(bar_x - 1, thumb_y, 3, thumb_h),
                         border_radius=2)

    return click_areas, max_scroll
