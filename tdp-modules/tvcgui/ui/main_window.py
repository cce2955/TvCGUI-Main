"""UI drawing helpers for the main TvC Continuo window.

This module is intentionally presentation-only. The runtime loop and memory
logic stay in main.py; these helpers were moved out of the launcher file so the
main event loop is easier to audit without changing behavior.
"""

import math
import time
import json
import os
from collections import OrderedDict

import pygame

from tvcgui.core.paths import resolve_data_path

try:
    from tvcgui.tools.scanners.normal_scanner import ANIM_MAP as SCAN_ANIM_MAP
except Exception:
    SCAN_ANIM_MAP = {}

from tvcgui.features.combat.move_filters import is_purged_move_label

TOP_UI_RESERVED = 60

# GUI polish helpers
# ---------------------------------------------------------------------------

GUI_BG_DARK = (10, 11, 16)
GUI_PANEL = (20, 22, 30)
GUI_PANEL_2 = (28, 31, 42)
GUI_PANEL_3 = (36, 41, 56)

GUI_BORDER = (80, 88, 112)
GUI_BORDER_HOT = (145, 165, 205)

GUI_TEXT = (226, 230, 238)
GUI_TEXT_MUTED = (150, 158, 176)
GUI_TEXT_DIM = (110, 116, 132)

# Cohesive accent system.
# Most of the UI uses the same steel-blue accent. Slot/player identity stays
# mostly in the slim side rails so the app feels unified instead of rainbow.
GUI_APP_ACCENT = (105, 145, 210)
GUI_CONFIRM = (95, 205, 165)
GUI_WARNING = (205, 170, 90)
GUI_DANGER = (205, 80, 90)

GUI_ACCENT_BLUE = GUI_APP_ACCENT
GUI_ACCENT_PURPLE = (125, 135, 185)
GUI_ACCENT_GOLD = GUI_APP_ACCENT
GUI_ACCENT_GREEN = GUI_CONFIRM
GUI_ACCENT_RED = GUI_DANGER

GUI_P1 = (205, 75, 82)
GUI_P2 = (82, 135, 215)
GUI_P3 = (180, 90, 175)
GUI_P4 = (92, 185, 135)

GUI_SLOT_MUTED = {
    "P1": (185, 78, 86),
    "P2": (82, 128, 200),
    "P3": (165, 82, 160),
    "P4": (82, 165, 122),
}


_GRADIENT_CACHE: "OrderedDict[tuple, tuple[pygame.Surface, int]]" = OrderedDict()
_GRADIENT_CACHE_PIXELS = 0
_GRADIENT_CACHE_PIXEL_LIMIT = 3_000_000
_GRADIENT_CACHE_ENTRY_LIMIT = 64


def _cached_gradient_surface(
    width: int,
    height: int,
    top_col: tuple[int, int, int],
    bot_col: tuple[int, int, int],
    alpha: int,
) -> pygame.Surface:
    global _GRADIENT_CACHE_PIXELS

    key = (
        int(width),
        int(height),
        tuple(int(v) for v in top_col),
        tuple(int(v) for v in bot_col),
        int(alpha),
    )
    cached = _GRADIENT_CACHE.get(key)
    if cached is not None:
        _GRADIENT_CACHE.move_to_end(key)
        return cached[0]

    grad = pygame.Surface((int(width), int(height)), pygame.SRCALPHA)
    for y in range(int(height)):
        t = y / max(1, int(height) - 1)
        r = int(top_col[0] * (1.0 - t) + bot_col[0] * t)
        g = int(top_col[1] * (1.0 - t) + bot_col[1] * t)
        b = int(top_col[2] * (1.0 - t) + bot_col[2] * t)
        pygame.draw.line(grad, (r, g, b, int(alpha)), (0, y), (int(width), y))

    area = int(width) * int(height)
    if area <= 500_000:
        _GRADIENT_CACHE[key] = (grad, area)
        _GRADIENT_CACHE_PIXELS += area
        while (
            len(_GRADIENT_CACHE) > _GRADIENT_CACHE_ENTRY_LIMIT
            or _GRADIENT_CACHE_PIXELS > _GRADIENT_CACHE_PIXEL_LIMIT
        ):
            _old_key, (_old_surface, old_area) = _GRADIENT_CACHE.popitem(last=False)
            _GRADIENT_CACHE_PIXELS = max(0, _GRADIENT_CACHE_PIXELS - int(old_area))
    return grad



def _clamp_u8(v: int) -> int:
    return max(0, min(255, int(v)))


def _brighten(col: tuple[int, int, int], amt: int) -> tuple[int, int, int]:
    return (
        _clamp_u8(col[0] + amt),
        _clamp_u8(col[1] + amt),
        _clamp_u8(col[2] + amt),
    )


def _darken(col: tuple[int, int, int], amt: int) -> tuple[int, int, int]:
    return (
        _clamp_u8(col[0] - amt),
        _clamp_u8(col[1] - amt),
        _clamp_u8(col[2] - amt),
    )


def _mix_col(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, float(t)))
    return (
        _clamp_u8(a[0] * (1.0 - t) + b[0] * t),
        _clamp_u8(a[1] * (1.0 - t) + b[1] * t),
        _clamp_u8(a[2] * (1.0 - t) + b[2] * t),
    )


def _slot_accent_for_label(slot_label: str, *, muted: bool = False) -> tuple[int, int, int]:
    label = str(slot_label or "")
    if label.startswith("P1"):
        base = GUI_P1 if label.endswith("C1") else GUI_P3
    elif label.startswith("P2"):
        base = GUI_P2 if label.endswith("C1") else GUI_P4
    else:
        base = GUI_APP_ACCENT
    if muted:
        return _mix_col(base, GUI_APP_ACCENT, 0.45)
    return base


def _draw_vertical_gradient(
    surf: pygame.Surface,
    rect: pygame.Rect,
    top_col: tuple[int, int, int],
    bot_col: tuple[int, int, int],
    alpha: int = 255,
) -> None:
    if rect.width <= 0 or rect.height <= 0:
        return

    grad = _cached_gradient_surface(
        rect.width,
        rect.height,
        top_col,
        bot_col,
        alpha,
    )
    surf.blit(grad, rect.topleft)


def _fit_text(
    font: pygame.font.Font,
    text: str,
    color: tuple[int, int, int],
    max_width: int,
) -> pygame.Surface:
    text = str(text or "")
    if max_width <= 8:
        return font.render("", True, color)

    surf = font.render(text, True, color)
    if surf.get_width() <= max_width:
        return surf

    if len(text) <= 1:
        return font.render("", True, color)

    low = 0
    high = len(text)
    best = ""

    while low <= high:
        mid = (low + high) // 2
        trial = text[:mid].rstrip() + "."
        trial_surf = font.render(trial, True, color)
        if trial_surf.get_width() <= max_width:
            best = trial
            low = mid + 1
        else:
            high = mid - 1

    return font.render(best, True, color)

def _render_outlined_text(
    font: pygame.font.Font,
    text: str,
    text_color: tuple[int, int, int],
    outline_color: tuple[int, int, int],
    max_width: int,
    outline_px: int = 1,
) -> pygame.Surface:
    base = _fit_text(font, text, text_color, max_width)

    w = base.get_width()
    h = base.get_height()

    if w <= 0 or h <= 0:
        return base

    pad = max(1, int(outline_px))
    out = pygame.Surface((w + pad * 2, h + pad * 2), pygame.SRCALPHA)

    outline = _fit_text(font, text, outline_color, max_width)
    for ox, oy in (
        (-pad, -pad), (0, -pad), (pad, -pad),
        (-pad, 0),                (pad, 0),
        (-pad, pad),  (0, pad),   (pad, pad),
    ):
        out.blit(outline, (pad + ox, pad + oy))

    out.blit(base, (pad, pad))
    return out


def _render_rainbow_outlined_text(
    font: pygame.font.Font,
    text: str,
    max_width: int,
    t_ms: int,
    outline_color: tuple[int, int, int] = (0, 0, 0),
    outline_px: int = 1,
) -> pygame.Surface:
    """Render fitted text with a soft animated rainbow fill and dark outline.

    Used for the Baroque line only. It is display-only and keeps the same
    truncation behavior as _fit_text.
    """
    base = _fit_text(font, text, (255, 255, 255), max_width)
    w = base.get_width()
    h = base.get_height()
    if w <= 0 or h <= 0:
        return base

    pad = max(1, int(outline_px))
    out = pygame.Surface((w + pad * 2, h + pad * 2), pygame.SRCALPHA)

    outline = _fit_text(font, text, outline_color, max_width)
    for ox, oy in (
        (-pad, -pad), (0, -pad), (pad, -pad),
        (-pad, 0),                (pad, 0),
        (-pad, pad),  (0, pad),   (pad, pad),
    ):
        out.blit(outline, (pad + ox, pad + oy))

    rainbow = pygame.Surface((w, h), pygame.SRCALPHA)
    phase = (float(t_ms) / 1000.0) * 0.35
    for x in range(w):
        t = (x / max(1, w - 1)) + phase
        r = int(190 + 55 * math.sin(2.0 * math.pi * (t + 0.00)))
        g = int(185 + 55 * math.sin(2.0 * math.pi * (t + 0.33)))
        b = int(220 + 35 * math.sin(2.0 * math.pi * (t + 0.66)))
        pygame.draw.line(
            rainbow,
            (_clamp_u8(r), _clamp_u8(g), _clamp_u8(b), 255),
            (x, 0),
            (x, h),
        )

    colored = base.copy()
    colored.blit(rainbow, (0, 0), special_flags=pygame.BLEND_MULT)
    out.blit(colored, (pad, pad))
    return out


def draw_glass_button(
    surf: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    font: pygame.font.Font,
    *,
    active: bool = False,
    hover: bool = False,
    accent: tuple[int, int, int] = GUI_ACCENT_BLUE,
    fill: tuple[int, int, int] | None = None,
    align: str = "center",
) -> None:
    """Shared button renderer with stronger hover/elevation feedback.

    The goal is to make the GUI feel a little more premium without changing any
    interaction logic. Hovered buttons lift slightly with a brighter shell and a
    faint shadow. Active buttons keep the accent rail.
    """
    base = fill if fill is not None else (GUI_PANEL_3 if active else GUI_PANEL_2)
    if hover:
        base = _brighten(base, 16)

    border = GUI_BORDER_HOT if hover else (accent if active else GUI_BORDER)
    text_col = GUI_TEXT if active or hover else GUI_TEXT_MUTED

    # Soft shadow/elevation on hover/active.
    if hover or active:
        shadow = pygame.Surface((rect.width + 6, rect.height + 6), pygame.SRCALPHA)
        pygame.draw.rect(shadow, (0, 0, 0, 45 if hover else 32), shadow.get_rect(), border_radius=6)
        surf.blit(shadow, (rect.x - 1, rect.y + 2))

    draw_rect = rect.move(0, -1 if hover else 0)

    _draw_vertical_gradient(
        surf,
        draw_rect,
        _brighten(base, 12),
        _darken(base, 6),
        235,
    )

    pygame.draw.rect(surf, border, draw_rect, 1, border_radius=4)

    shine = pygame.Rect(draw_rect.x + 2, draw_rect.y + 2, draw_rect.width - 4, max(2, draw_rect.height // 6))
    shine_col = (150, 165, 190, 16) if active or hover else (118, 128, 150, 11)
    pygame.draw.rect(surf, shine_col, shine, border_radius=3)

    if active:
        accent_rect = pygame.Rect(draw_rect.x + 4, draw_rect.bottom - 3, draw_rect.width - 8, 2)
        pygame.draw.rect(surf, accent, accent_rect, border_radius=1)

    label_surf = _render_outlined_text(
        font,
        label,
        text_col,
        (0, 0, 0),
        draw_rect.width - 12,
        outline_px=1,
    )

    if align == "left":
        tx = draw_rect.x + 7
    elif align == "right":
        tx = draw_rect.right - label_surf.get_width() - 7
    else:
        tx = draw_rect.x + (draw_rect.width - label_surf.get_width()) // 2

    ty = draw_rect.y + (draw_rect.height - label_surf.get_height()) // 2
    surf.blit(label_surf, (tx, ty))



def draw_slot_chip(
    surf: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    font: pygame.font.Font,
    *,
    enabled: bool,
    accent: tuple[int, int, int],
    hover: bool,
) -> None:
    fill = (28, 31, 42) if enabled else (22, 24, 31)
    border = accent if enabled else (62, 68, 84)
    text_col = GUI_TEXT if enabled else GUI_TEXT_DIM

    if hover:
        fill = _brighten(fill, 12)
        border = _brighten(border, 18)

    _draw_vertical_gradient(
        surf,
        rect,
        _brighten(fill, 8),
        _darken(fill, 7),
        235,
    )

    pygame.draw.rect(surf, border, rect, 1, border_radius=4)

    # Slot identity is a tiny left rail, not a full loud badge.
    if enabled:
        pygame.draw.rect(surf, accent, pygame.Rect(rect.x + 2, rect.y + 3, 2, rect.height - 6), border_radius=1)

    state = "ON" if enabled else "OFF"
    text = f"{label} {state}"

    label_surf = _render_outlined_text(
        font,
        text,
        text_col,
        (0, 0, 0),
        rect.width - 10,
        outline_px=1,
    )

    surf.blit(
        label_surf,
        (
            rect.x + (rect.width - label_surf.get_width()) // 2,
            rect.y + (rect.height - label_surf.get_height()) // 2,
        ),
    )


def draw_top_command_dock(
    screen: pygame.Surface,
    smallfont: pygame.font.Font,
    *,
    hitbox_slots: dict,
    overlay_enabled: bool,
    megacrash_active: bool = False,
    megacrash_remaining: float = 0.0,
    mouse_pos: tuple[int, int],
    t_ms: int = 0,
) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect, pygame.Rect, pygame.Rect, dict]:
    mx, my = mouse_pos
    w, _h = screen.get_size()

    dock_rect = pygame.Rect(0, 0, w, TOP_UI_RESERVED - 4)
    _draw_vertical_gradient(
        screen,
        dock_rect,
        (12, 13, 19),
        (8, 9, 13),
        255,
    )
    pygame.draw.line(screen, (58, 64, 82), (0, dock_rect.bottom - 1), (w, dock_rect.bottom - 1))

    # Quiet status text plus a tiny heartbeat indicator.
    status_text = "Left click to interact | Right click panels/debug rows to copy"
    status_surf = _fit_text(smallfont, status_text, GUI_TEXT_DIM, max(80, w - 640))
    status_x = w - status_surf.get_width() - 10
    pulse = 0.5 + 0.5 * math.sin((t_ms / 1000.0) * 4.0)
    dot_col = _brighten(GUI_APP_ACCENT, int(40 * pulse))
    dot_alpha = int(120 + 80 * pulse)
    dot = pygame.Surface((8, 8), pygame.SRCALPHA)
    pygame.draw.circle(dot, (*dot_col, dot_alpha), (4, 4), 3)
    pygame.draw.circle(dot, (*dot_col, 50), (4, 4), 4, 1)
    screen.blit(dot, (status_x - 14, 13))
    screen.blit(status_surf, (status_x, 11))

    x = 8
    y = 8
    btn_h = 23
    gap = 8

    hb_btn_rect = pygame.Rect(x, y, 142, btn_h)
    hb_on = any(hitbox_slots.values())
    draw_glass_button(
        screen,
        hb_btn_rect,
        "Hitboxes: ON" if hb_on else "Hitboxes: OFF",
        smallfont,
        active=hb_on,
        hover=hb_btn_rect.collidepoint(mx, my),
        accent=GUI_APP_ACCENT,
        align="center",
    )

    x = hb_btn_rect.right + gap
    hud_btn_rect = pygame.Rect(x, y, 142, btn_h)
    draw_glass_button(
        screen,
        hud_btn_rect,
        "Overlay: ON" if overlay_enabled else "Overlay: OFF",
        smallfont,
        active=overlay_enabled,
        hover=hud_btn_rect.collidepoint(mx, my),
        accent=GUI_APP_ACCENT,
        align="center",
    )

    x = hud_btn_rect.right + gap
    ps_btn_rect = pygame.Rect(x, y, 150, btn_h)
    draw_glass_button(
        screen,
        ps_btn_rect,
        "Proj Scanner",
        smallfont,
        active=False,
        hover=ps_btn_rect.collidepoint(mx, my),
        accent=GUI_APP_ACCENT,
        align="center",
    )

    x = ps_btn_rect.right + gap
    as_btn_rect = pygame.Rect(x, y, 132, btn_h)
    draw_glass_button(
        screen,
        as_btn_rect,
        "Assist Scanner",
        smallfont,
        active=False,
        hover=as_btn_rect.collidepoint(mx, my),
        accent=GUI_APP_ACCENT,
        align="center",
    )

    x = as_btn_rect.right + gap
    megacrash_btn_rect = pygame.Rect(x, y, 124, btn_h)
    mega_label = f"Megacrash {max(0.0, megacrash_remaining):.1f}s" if megacrash_active else "Megacrash"
    draw_glass_button(
        screen,
        megacrash_btn_rect,
        mega_label,
        smallfont,
        active=megacrash_active,
        hover=megacrash_btn_rect.collidepoint(mx, my),
        accent=GUI_APP_ACCENT,
        fill=(72, 42, 48) if megacrash_active else (31, 33, 42),
        align="center",
    )

    chip_y = y + btn_h + 6
    label_surf = smallfont.render("Hitbox Slots:", True, GUI_TEXT_MUTED)
    screen.blit(label_surf, (8, chip_y + 3))

    chip_x = 8 + label_surf.get_width() + 10
    chip_w = 60
    chip_h = 18
    chip_gap = 7

    slot_colors = dict(GUI_SLOT_MUTED)

    hb_filter_rects = {}

    for slot_name in ("P1", "P2", "P3", "P4"):
        chip_rect = pygame.Rect(chip_x, chip_y, chip_w, chip_h)
        draw_slot_chip(
            screen,
            chip_rect,
            slot_name,
            smallfont,
            enabled=bool(hitbox_slots.get(slot_name, False)),
            accent=slot_colors.get(slot_name, GUI_ACCENT_BLUE),
            hover=chip_rect.collidepoint(mx, my),
        )
        hb_filter_rects[slot_name] = chip_rect.inflate(4, 4)
        chip_x += chip_w + chip_gap

    return hb_btn_rect, ps_btn_rect, as_btn_rect, hud_btn_rect, megacrash_btn_rect, hb_filter_rects


def draw_status_rail(
    screen: pygame.Surface,
    smallfont: pygame.font.Font,
    *,
    text: str,
) -> None:
    if not text:
        return

    w, h = screen.get_size()
    rail_h = 22
    rect = pygame.Rect(0, h - rail_h, w, rail_h)

    _draw_vertical_gradient(
        screen,
        rect,
        (18, 20, 28),
        (12, 13, 18),
        245,
    )

    pygame.draw.line(screen, (58, 64, 82), (0, rect.y), (w, rect.y))
    label = _fit_text(smallfont, text, GUI_TEXT_MUTED, w - 18)
    screen.blit(label, (8, rect.y + (rail_h - label.get_height()) // 2))




def draw_bottom_workspace_tabs(
    screen: pygame.Surface,
    rect: pygame.Rect,
    smallfont: pygame.font.Font,
    active_tab: str,
    mouse_pos: tuple[int, int],
) -> tuple[pygame.Rect, dict[str, pygame.Rect]]:
    """Draw the lower inspector as a tabbed workspace.

    This keeps the tabbed layout, but upgrades the presentation: active tabs
    feel raised, get a clearer accent underline, and visually match the more
    polished card language used elsewhere in the GUI.
    """
    mx, my = mouse_pos
    tab_h = 24
    pad = 4

    if rect.width <= 0 or rect.height <= tab_h + 8:
        return rect, {}

    panel = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
    _draw_vertical_gradient(
        panel,
        panel.get_rect(),
        (14, 16, 23),
        (10, 11, 16),
        245,
    )
    pygame.draw.rect(panel, (52, 58, 76), panel.get_rect(), 1, border_radius=4)
    screen.blit(panel, rect.topleft)

    tabs = [
        ("scan", "Normals Preview", GUI_APP_ACCENT),
        ("events", "Events", GUI_APP_ACCENT),
        ("debug", "Debug Flags", GUI_APP_ACCENT),
        ("activity", "Activity", GUI_APP_ACCENT),
    ]

    tab_rects: dict[str, pygame.Rect] = {}
    x = rect.x + pad
    y = rect.y + pad
    gap = 6

    for key, label, accent in tabs:
        width = max(96, min(160, smallfont.size(label)[0] + 26))
        tr = pygame.Rect(x, y, width, tab_h)
        tab_rects[key] = tr
        is_active = key == active_tab
        is_hover = tr.collidepoint(mx, my)

        # Active tabs feel slightly raised and have a stronger accent cue.
        fill = (31, 38, 56) if is_active else (21, 24, 34)
        draw_glass_button(
            screen,
            tr,
            label,
            smallfont,
            active=is_active,
            hover=is_hover,
            accent=accent,
            fill=fill,
            align="center",
        )

        if is_active:
            top_rail = pygame.Rect(tr.x + 5, tr.y + 2, tr.width - 10, 2)
            pygame.draw.rect(screen, (*accent, 220), top_rail, border_radius=1)
            glow = pygame.Surface((tr.width - 8, 8), pygame.SRCALPHA)
            pygame.draw.rect(glow, (*accent, 34), glow.get_rect(), border_radius=4)
            screen.blit(glow, (tr.x + 4, tr.bottom - 4))

        x += width + gap

    content = pygame.Rect(
        rect.x + pad,
        rect.y + tab_h + pad + 5,
        rect.width - pad * 2,
        rect.height - tab_h - pad * 2 - 5,
    )
    if content.height < 16:
        content.height = 16

    pygame.draw.rect(screen, (18, 20, 28), content, border_radius=4)
    pygame.draw.rect(screen, (45, 52, 72), content, 1, border_radius=4)

    return content, tab_rects



def _normal_button_accent(label: str) -> tuple[int, int, int]:
    text = str(label or "").upper()
    if "A" in text or text.endswith("L"):
        return (115, 155, 235)
    if "B" in text or text.endswith("M"):
        return (220, 195, 105)
    if "C" in text or text.endswith("H"):
        return (105, 215, 155)
    return GUI_ACCENT_BLUE


def _normal_id_to_label(value, *, allow_low: bool = True) -> str | None:
    """Resolve a normal label from the scanner's authoritative ANIM_MAP.

    The old preview table was shifted for crouching normals and also invented
    j.2B/j.2C rows from raw ids. Keep this helper tied to scan_normals_all so
    the preview cannot drift away from the actual scanner again.
    """
    try:
        raw = int(value)
    except Exception:
        return None

    if raw < 0:
        return None

    fallback_map = {
        0x00: "5A",
        0x01: "5B",
        0x02: "5C",
        0x03: "2A",
        0x04: "2B",
        0x05: "2C",
        0x06: "6C",
        0x08: "3C",
        0x09: "j.A",
        0x0A: "j.B",
        0x0B: "j.C",
        0x0E: "6B",
    }

    scan_map = SCAN_ANIM_MAP if isinstance(SCAN_ANIM_MAP, dict) and SCAN_ANIM_MAP else fallback_map
    low = raw & 0xFF

    if raw >= 0x100 and low in scan_map:
        return str(scan_map[low])

    if allow_low and raw in scan_map:
        return str(scan_map[raw])

    return None


def _normal_move_label(mv: dict) -> str:
    if not isinstance(mv, dict):
        return "?"

    forced = mv.get("_normal_display_label")
    if forced:
        return str(forced)

    # Prefer actual scanner/editor labels first. move_name is what
    # scan_normals_all attaches; the older preview code accidentally ignored it.
    for key in ("label", "move_name", "move", "pretty_name", "name"):
        value = mv.get(key)
        if value:
            return str(value)

    label = _normal_id_to_label(mv.get("id"), allow_low=True)
    if label:
        return label

    label = _normal_id_to_label(mv.get("table_index"), allow_low=False)
    if label:
        return label

    return "?"


def _normal_canon_label(label: str) -> str:
    """Canonicalize display labels for preview-row highlighting.

    The UI should prefer the live move label first, because multiple rows can
    sometimes share a raw animation ID or carry overlapping fallback IDs. Using
    the canonical display label avoids accidental double-highlighting.
    """
    text = str(label or "").strip().lower()
    if not text:
        return ""
    text = text.replace(" ", "")
    text = text.replace("jump.", "j.")
    text = text.replace("jump", "j")
    text = text.replace("crouching", "2")
    text = text.replace("crouch", "2")
    text = text.replace("standing", "5")
    text = text.replace("stand", "5")
    text = text.replace("close", "")
    text = text.replace("far", "")
    return text


def _normal_int(mv: dict, *keys: str) -> int | None:
    for key in keys:
        value = mv.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except Exception:
            continue
    return None



_NORMAL_PREVIEW_ORDER = (
    "5A", "2A",
    "5B", "2B",
    "6B",
    "5C", "2C",
    "4C", "6C", "3C",
    "j.A", "j.B", "j.C",
)
_NORMAL_PREVIEW_RANK = {name.lower(): i for i, name in enumerate(_NORMAL_PREVIEW_ORDER)}

# These labels are optional/character-specific. Do not let a raw fallback id
# manufacture them for everyone. j.2B/j.2C are intentionally not in the preview
# order until they are promoted by a real character-specific scanner label.
_OPTIONAL_PREVIEW_NORMALS = {"6B"}
_HIDDEN_PREVIEW_NORMALS = {"j.2B", "j.2C"}


def _normal_canonical_label(label: str) -> str | None:
    text = str(label or "").strip()
    if not text or text == "?":
        return None

    low = text.lower()
    low = low.replace(" ", "")
    low = low.replace("_", "")
    low = low.replace("jump.", "j.")
    low = low.replace("jump", "j.")
    low = low.replace("air.", "j.")
    low = low.replace("air", "j.")
    low = low.replace("stand", "5")
    low = low.replace("standing", "5")
    low = low.replace("crouch", "2")
    low = low.replace("crouching", "2")

    aliases = {
        "a": "5A", "5a": "5A",
        "2a": "2A",
        "b": "5B", "5b": "5B",
        "2b": "2B",
        "6b": "6B",
        "c": "5C", "5c": "5C",
        "2c": "2C",
        "4c": "4C",
        "6c": "6C",
        "3c": "3C",
        "j.a": "j.A", "ja": "j.A", "jA".lower(): "j.A",
        "j.b": "j.B", "jb": "j.B", "jB".lower(): "j.B",
        "j.c": "j.C", "jc": "j.C", "jC".lower(): "j.C",
    }

    return aliases.get(low)


def _normal_preview_label_allowed(mv: dict, canon: str, raw_label: str, char_ref: dict | None = None) -> bool:
    """Gate optional labels that raw ids can falsely create.

    Core normals are allowed from the scanner map. 6B is allowed only when the
    row was produced by an explicit/character-specific label source. This keeps
    random 0x010E system/script records from appearing as 6B for every cast
    member. j.2B/j.2C stay hidden from the compact preview for now.
    """
    if canon in _HIDDEN_PREVIEW_NORMALS:
        return False

    if is_purged_move_label(char_ref or mv, mv, canon):
        return False

    if canon not in _OPTIONAL_PREVIEW_NORMALS:
        return True

    if not isinstance(mv, dict):
        return False

    if bool(mv.get("normal_confirmed")):
        return True

    source = str(mv.get("move_name_source") or mv.get("label_source") or "").strip().lower()
    if source in {"lookup", "char_map", "character", "csv", "explicit"}:
        return True

    # If another module supplied an actual display label, trust that over the
    # raw id fallback. Do not count move_name here because older scanner builds
    # filled move_name from ANIM_MAP fallback.
    for key in ("label", "move", "pretty_name"):
        explicit = mv.get(key)
        if explicit and _normal_canonical_label(str(explicit)) == canon:
            return True

    return False


def _normal_recovery(mv: dict) -> int | None:
    """Return the MOT-derived/static recovery stored on a normal row."""
    return _normal_int(mv, "recovery", "recover", "recovery_frames")


_MANUAL_OBSERVED_CACHE: dict[int, dict[str, int]] | None = None

def _manual_observed_map() -> dict[int, dict[str, int]]:
    global _MANUAL_OBSERVED_CACHE
    if _MANUAL_OBSERVED_CACHE is not None:
        return _MANUAL_OBSERVED_CACHE
    result: dict[int, dict[str, int]] = {}
    paths = [
        resolve_data_path("frame_data", "observed_block_advantage_profiles.json"),
        os.path.join("data", "frame_data", "observed_block_advantage_profiles.json"),
        os.path.join("tdp-modules", "data", "frame_data", "observed_block_advantage_profiles.json"),
    ]
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f)
            for profile in (doc.get("profiles") or {}).values():
                if not isinstance(profile, dict):
                    continue
                cid = int(profile.get("char_id"))
                cmap = result.setdefault(cid, {})
                for move in profile.get("moves") or []:
                    if not isinstance(move, dict):
                        continue
                    name = str(move.get("move_name") or "").strip().lower().replace(" ", "")
                    if not name:
                        continue
                    value = move.get("adv_block_observed")
                    if value in (None, ""):
                        value = move.get("observed_block_advantage")
                    if value in (None, ""):
                        continue
                    try:
                        cmap[name] = int(str(value).strip())
                    except Exception:
                        pass
            if result:
                break
        except Exception:
            continue
    _MANUAL_OBSERVED_CACHE = result
    return result

def _manual_observed_for_slot(slot: dict, label: str) -> int | None:
    try:
        cid = int(slot.get("char_id"))
    except Exception:
        cid = None
    if cid is None:
        return None
    key = str(label or "").strip().lower().replace(" ", "")
    return _manual_observed_map().get(cid, {}).get(key)

def _normal_observed_block_advantage(mv: dict) -> int | None:
    """Return the wiki/observed block advantage when a row carries it."""
    return _normal_int(
        mv,
        "adv_block_observed",
        "observed_adv_block",
        "wiki_adv_block",
        "block_adv_observed",
        "on_block_observed",
        "observed_block_advantage",
    )


def _normal_derived_block_advantage(mv: dict) -> int | None:
    """Return the scanner-derived block advantage, with legacy fallback."""
    return _normal_int(mv, "adv_block_derived", "derived_adv_block", "adv_block", "on_block", "block_adv", "plus_block")


def _normal_advantage(mv: dict, kind: str, *, prefer_observed: bool = False) -> int | None:
    """Return advantage with explicit observed/derived block separation."""
    if str(kind).lower().startswith("b"):
        if prefer_observed:
            return _normal_observed_block_advantage(mv)
        stored = _normal_derived_block_advantage(mv)
        stun = _normal_int(mv, "blockstun", "block", "b")
    else:
        stored = _normal_int(mv, "adv_hit", "adv_hit_derived", "derived_adv_hit", "on_hit", "hit_adv", "plus_hit")
        stun = _normal_int(mv, "hitstun", "hit", "h")
    if stored is not None:
        return stored
    recovery = _normal_recovery(mv)
    if stun is None or recovery is None:
        return None
    return int(stun) - int(recovery)

def _normal_damage(mv: dict) -> int | None:
    """Return the decoded primary-hit damage without summing multihits."""
    direct = _normal_int(mv, "damage", "dmg", "base_damage")
    if direct is not None:
        return direct
    segments = mv.get("hit_segments")
    if isinstance(segments, list):
        for seg in segments:
            if isinstance(seg, dict):
                damage = _normal_int(seg, "damage", "dmg", "base_damage")
                if damage is not None:
                    return damage
    return None


def _normal_row_quality(mv: dict) -> tuple[int, int, int, int]:
    """Prefer rows that actually have useful frame values if duplicates exist."""
    if not isinstance(mv, dict):
        return (0, 0, 0, 0)
    startup = _normal_int(mv, "startup", "start", "active_start")
    a1 = _normal_int(mv, "active_start", "a_start")
    a2 = _normal_int(mv, "active_end", "a_end")
    hit = _normal_int(mv, "hitstun", "hit", "h")
    block = _normal_int(mv, "blockstun", "block", "b")
    recovery = _normal_recovery(mv)
    adv_hit = _normal_advantage(mv, "hit")
    adv_block = _normal_advantage(mv, "block", prefer_observed=True)
    filled = sum(v is not None for v in (startup, a1, a2, recovery, hit, block, adv_hit, adv_block))
    active_span = 0 if a1 is None or a2 is None else max(0, a2 - a1)
    damage = _normal_damage(mv) or 0
    return (filled, active_span, damage, -int(mv.get("_scan_index", 0) or 0))


def _normal_visible_moves(moves: list, char_ref: dict | None = None) -> list:
    """Return only the curated normal rows, in fighting-game notation order.

    The scan can contain duplicate/system/debug rows, and some characters put
    command normals before or after jump normals. The preview should not depend
    on raw scan order. It shows the useful set only:
      5A, 2A, 5B, 2B, optional confirmed 6B, 5C, 2C, optional 4C/6C/3C, j.A, j.B, j.C
    """
    if not isinstance(moves, list):
        return []

    best_by_label: dict[str, dict] = {}

    for scan_i, mv in enumerate(moves):
        if not isinstance(mv, dict):
            continue

        label = _normal_move_label(mv)
        canon = _normal_canonical_label(label)
        if canon is None:
            continue
        if not _normal_preview_label_allowed(mv, canon, label, char_ref):
            continue

        row = dict(mv)
        row["_normal_display_label"] = canon
        row.setdefault("_scan_index", scan_i)

        old = best_by_label.get(canon)
        if old is None or _normal_row_quality(row) > _normal_row_quality(old):
            best_by_label[canon] = row

    out: list[dict] = []
    for label in _NORMAL_PREVIEW_ORDER:
        row = best_by_label.get(label)
        if row is not None:
            out.append(row)
    return out



_NORMAL_PREVIEW_MODE_META = {
    "fast": {"label": "Fast", "color": (112, 182, 245)},
    "damage": {"label": "Damage", "color": (232, 190, 96)},
    "adv_block": {"label": "+Block", "color": (177, 145, 244)},
    "punish": {"label": "Punish", "color": (236, 136, 112)},
    "live_punish": {"label": "Live", "color": (100, 209, 223)},
}


def _normal_preview_move_id(mv: dict) -> int | None:
    """Return the most specific available move identifier for a preview row."""
    if not isinstance(mv, dict):
        return None
    for key in ("id", "anim", "move_id", "table_index"):
        value = mv.get(key)
        if value is None or value == "":
            continue
        try:
            return int(value)
        except Exception:
            continue
    return None


def _normal_preview_key(mv: dict) -> str:
    """Build a stable local key for preview selection and highlighting."""
    label = _normal_canonical_label(_normal_move_label(mv)) or _normal_canon_label(_normal_move_label(mv)) or "?"
    move_id = _normal_preview_move_id(mv)
    return f"{label}|{'' if move_id is None else move_id}"


def _normal_preview_selection_matches(selection: dict | None, slot_label: str, mv: dict) -> bool:
    """Check whether a preview row matches the retained selection."""
    if not isinstance(selection, dict):
        return False
    if str(selection.get("slot_label") or "") != str(slot_label or ""):
        return False
    return str(selection.get("key") or "") == _normal_preview_key(mv)


def _normal_preview_selected_move(slots: list[dict], selection: dict | None) -> tuple[str, dict] | tuple[None, None]:
    """Resolve the selected preview row from the current slot dataset."""
    if not isinstance(selection, dict):
        return None, None
    wanted_slot = str(selection.get("slot_label") or "")
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        slot_label = str(slot.get("slot_label") or slot.get("slot") or "")
        if slot_label != wanted_slot:
            continue
        for mv in _normal_visible_moves(slot.get("moves") or [], slot):
            if _normal_preview_selection_matches(selection, slot_label, mv):
                return slot_label, mv
    return None, None


def _normal_preview_is_air_move(mv: dict) -> bool:
    """Return whether a preview normal is an aerial attack."""
    label = _normal_canonical_label(_normal_move_label(mv)) or _normal_canon_label(_normal_move_label(mv)) or ""
    return str(label).lower().startswith("j.")


def _normal_preview_current_move(slot: dict) -> dict | None:
    """Resolve the currently executing visible normal for one slot."""
    if not isinstance(slot, dict):
        return None
    visible = _normal_visible_moves(slot.get("moves") or [], slot)
    if not visible:
        return None

    cur_id = slot.get("cur_anim") or slot.get("current_anim") or slot.get("mv_id_display") or slot.get("move_id")
    try:
        cur_id = int(cur_id) if cur_id is not None else None
    except Exception:
        cur_id = None
    cur_label = str(slot.get("cur_label") or slot.get("current_move") or slot.get("mv_label") or "").strip()
    cur_label_canon = _normal_canon_label(cur_label)

    if cur_label_canon:
        for mv in visible:
            if _normal_canon_label(_normal_move_label(mv)) == cur_label_canon:
                return mv
    if cur_id is not None:
        for mv in visible:
            if _normal_preview_move_id(mv) == cur_id:
                return mv
    return None


def _normal_preview_assist_standby(slot: dict) -> bool:
    """Identify the inactive partner state used by the character slots."""
    if not isinstance(slot, dict):
        return False
    text = " ".join(str(slot.get(key) or "") for key in ("cur_label", "current_move", "mv_label")).lower()
    if "assist standby" in text:
        return True
    current_id = slot.get("cur_anim") or slot.get("current_anim") or slot.get("mv_id_display") or slot.get("move_id")
    try:
        return int(current_id) == 430
    except Exception:
        return False


def _normal_preview_active_slot(slots: list[dict], team: str) -> tuple[str, dict] | tuple[None, None]:
    """Resolve the point slot for a team from the current two-character state."""
    team = str(team or "").upper()
    candidates: list[tuple[str, dict]] = []
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        label = str(slot.get("slot_label") or slot.get("slot") or "")
        if label.startswith(f"{team}-"):
            candidates.append((label, slot))
    if not candidates:
        return None, None
    if len(candidates) == 1:
        return candidates[0]

    active = [(label, slot) for label, slot in candidates if not _normal_preview_assist_standby(slot)]
    if len(active) == 1:
        return active[0]
    for label, slot in candidates:
        if label.endswith("C1"):
            return label, slot
    return candidates[0]


def _normal_preview_live_source(slots: list[dict]) -> tuple[str, dict] | tuple[None, None]:
    """Resolve a single current point-normal as the live punish source."""
    found: list[tuple[str, dict]] = []
    for team in ("P1", "P2"):
        slot_label, slot = _normal_preview_active_slot(slots, team)
        if not slot_label or not isinstance(slot, dict):
            continue
        move = _normal_preview_current_move(slot)
        if isinstance(move, dict):
            found.append((slot_label, move))
    if len(found) != 1:
        return None, None
    return found[0]


def _normal_preview_best_keys(moves: list[dict], mode: str) -> set[str]:
    """Return the best metric rows for one character card.

    Fast/damage/+block highlights are split into grounded and aerial groups so
    each card can show one best standing normal and one best jumping normal.
    Fast ignores 1f/2f startup values to avoid false positives.
    """
    mode = str(mode or "")
    if mode == "adv_hit":
        mode = "adv_block"

    grouped: dict[bool, list[tuple[int, str]]] = {False: [], True: []}
    for mv in moves:
        if not isinstance(mv, dict):
            continue
        is_air = _normal_preview_is_air_move(mv)
        if mode == "fast":
            value = _normal_int(mv, "startup", "start", "active_start")
            if value is None:
                continue
            value = int(value)
            if value < 3:
                continue
            grouped[is_air].append((-value, _normal_preview_key(mv)))
        elif mode == "damage":
            value = _normal_damage(mv)
            if value is None:
                continue
            grouped[is_air].append((int(value), _normal_preview_key(mv)))
        elif mode == "adv_block":
            value = _normal_advantage(mv, "block", prefer_observed=True)
            if value is None:
                continue
            grouped[is_air].append((int(value), _normal_preview_key(mv)))

    out: set[str] = set()
    for is_air in (False, True):
        ranked = grouped[is_air]
        if not ranked:
            continue
        best_value = max(value for value, _key in ranked)
        out.update(key for value, key in ranked if value == best_value)
    return out


def _normal_preview_punish_candidate(slot: dict, source_mv: dict, window: int) -> str | None:
    """Return the best legal normal response for a block-punish window."""
    source_is_air = _normal_preview_is_air_move(source_mv)
    candidates: list[tuple[int, int, str]] = []
    for mv in _normal_visible_moves(slot.get("moves") or [], slot):
        if _normal_preview_is_air_move(mv) != source_is_air:
            continue
        startup = _normal_int(mv, "startup", "start", "active_start")
        if startup is None or int(startup) > int(window):
            continue
        damage = _normal_damage(mv)
        candidates.append((int(damage) if damage is not None else -1, -int(startup), _normal_preview_key(mv)))
    return max(candidates)[2] if candidates else None


def _normal_preview_punish_from_source(
    slots: list[dict],
    source_slot: str,
    source_mv: dict,
) -> tuple[dict[str, set[str]], int | None, str | None]:
    """Return the active opposing point-normal that punishes a source move."""
    adv_block = _normal_advantage(source_mv, "block", prefer_observed=True)
    if adv_block is None or adv_block >= 0:
        return {}, adv_block, source_slot

    source_team = "P1" if str(source_slot).startswith("P1") else "P2" if str(source_slot).startswith("P2") else ""
    target_team = "P2" if source_team == "P1" else "P1" if source_team == "P2" else ""
    if not target_team:
        return {}, adv_block, source_slot

    target_slot, target = _normal_preview_active_slot(slots, target_team)
    if not target_slot or not isinstance(target, dict):
        return {}, adv_block, source_slot

    key = _normal_preview_punish_candidate(target, source_mv, -int(adv_block))
    return ({target_slot: {key}} if key else {}), adv_block, source_slot


def _normal_preview_punish_keys(slots: list[dict], selection: dict | None) -> tuple[dict[str, set[str]], int | None, str | None]:
    """Resolve a selected normal into an active point punish response."""
    source_slot, source_mv = _normal_preview_selected_move(slots, selection)
    if not source_slot or not isinstance(source_mv, dict):
        return {}, None, None
    return _normal_preview_punish_from_source(slots, source_slot, source_mv)


def _normal_preview_live_punish_keys(slots: list[dict]) -> tuple[dict[str, set[str]], int | None, str | None]:
    """Resolve the current live normal into an active point punish response."""
    source_slot, source_mv = _normal_preview_live_source(slots)
    if not source_slot or not isinstance(source_mv, dict):
        return {}, None, None
    return _normal_preview_punish_from_source(slots, source_slot, source_mv)


def _normal_preview_highlight_keys(slots: list[dict], mode: str, selection: dict | None) -> tuple[dict[str, set[str]], int | None, str | None]:
    """Return highlight keys for the active preview mode."""
    mode = str(mode or "none")
    if mode == "adv_hit":
        mode = "adv_block"
    if mode == "punish":
        return _normal_preview_punish_keys(slots, selection)
    if mode == "live_punish":
        return _normal_preview_live_punish_keys(slots)
    if mode not in {"fast", "damage", "adv_block"}:
        return {}, None, None
    out: dict[str, set[str]] = {}
    for slot in slots:
        if not isinstance(slot, dict):
            continue
        slot_label = str(slot.get("slot_label") or slot.get("slot") or "")
        keys = _normal_preview_best_keys(_normal_visible_moves(slot.get("moves") or [], slot), mode)
        if keys:
            out[slot_label] = keys
    return out, None, None


def _normal_preview_status(slots: list[dict], mode: str, selection: dict | None) -> str:
    """Build the compact status label beside the preview controls."""
    mode = str(mode or "none")
    if mode == "adv_hit":
        mode = "adv_block"
    if mode not in {"punish", "live_punish"}:
        return "Click active filter to clear"

    if mode == "live_punish":
        source_slot, source_mv = _normal_preview_live_source(slots)
        if not source_slot or not isinstance(source_mv, dict):
            return "Waiting for one live normal"
    else:
        source_slot, source_mv = _normal_preview_selected_move(slots, selection)
        if not source_slot or not isinstance(source_mv, dict):
            return "Select a move to test"

    label = _normal_move_label(source_mv)
    adv_block = _normal_advantage(source_mv, "block", prefer_observed=True)
    prefix = "Live " if mode == "live_punish" else ""
    if adv_block is None:
        return f"{prefix}{source_slot} {label}: block value unavailable"
    if adv_block >= 0:
        return f"{prefix}{source_slot} {label}: safe on block ({adv_block:+d})"

    highlighted, _adv, _source = _normal_preview_punish_from_source(slots, source_slot, source_mv)
    response_kind = "air-to-air" if _normal_preview_is_air_move(source_mv) else "ground"
    window = -int(adv_block)
    if not highlighted:
        return f"{prefix}{source_slot} {label}: no {response_kind} punish in {window}f"
    target_slot = next(iter(highlighted))
    return f"{prefix}{source_slot} {label}: {target_slot} punish window {window}f"

def _draw_scan_metric_chip(
    surf: pygame.Surface,
    rect: pygame.Rect,
    smallfont: pygame.font.Font,
    label: str,
    value: str,
    accent: tuple[int, int, int],
) -> None:
    """Draw a cleaner metric cell for the normals preview.

    The earlier chip style worked, but looked a bit busy once multiplied across
    four cards. This version keeps a subtle boxed cell, a calm neutral fill,
    and a tiny accent rail on the left so the preview feels more like a polished
    data table and less like a wall of little buttons.
    """
    _draw_vertical_gradient(
        surf,
        rect,
        (22, 25, 35),
        (15, 18, 26),
        236,
    )
    pygame.draw.rect(surf, (44, 51, 71), rect, 1, border_radius=4)

    rail = pygame.Rect(rect.x + 1, rect.y + 1, 2, max(1, rect.height - 2))
    pygame.draw.rect(surf, _darken(accent, 12), rail, border_radius=1)

    label_s = smallfont.render(label, True, GUI_TEXT_DIM)
    value_s = _render_outlined_text(
        smallfont,
        value,
        GUI_TEXT,
        (0, 0, 0),
        rect.width - label_s.get_width() - 10,
        outline_px=1,
    )

    x = rect.x + 6
    surf.blit(label_s, (x, rect.y + (rect.height - label_s.get_height()) // 2))
    surf.blit(
        value_s,
        (
            rect.right - value_s.get_width() - 5,
            rect.y + (rect.height - value_s.get_height()) // 2,
        ),
    )


def draw_scan_normals_polished(
    surf: pygame.Surface,
    rect: pygame.Rect,
    font: pygame.font.Font,
    smallfont: pygame.font.Font,
    scan_data,
    *,
    t_ms: int = 0,
    scan_fx_by_slot: dict | None = None,
    highlight_mode: str = "none",
    selection: dict | None = None,
    mouse_pos: tuple[int, int] | None = None,
) -> dict:
    """Draw the normals preview and return local click targets."""
    interaction = {"controls": {}, "rows": []}
    if rect.width <= 0 or rect.height <= 0:
        return interaction

    scan_fx_by_slot = scan_fx_by_slot or {}
    highlight_mode = str(highlight_mode or "none")
    mouse_pos = mouse_pos or (-10000, -10000)

    panel = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
    _draw_vertical_gradient(panel, panel.get_rect(), (14, 17, 26), (10, 12, 18), 255)
    surf.blit(panel, rect.topleft)

    title = smallfont.render("Scan: Normals Preview", True, GUI_TEXT)
    legend = smallfont.render("Attack | S startup | A active | HS hitstun | BS blockstun | BA observed block adv | DMG damage", True, GUI_TEXT_DIM)
    surf.blit(title, (rect.x + 10, rect.y + 7))
    surf.blit(legend, (rect.right - legend.get_width() - 10, rect.y + 7))
    pygame.draw.line(surf, (52, 61, 82), (rect.x + 8, rect.y + 24), (rect.right - 8, rect.y + 24))

    try:
        slots = list(scan_data or [])
    except Exception:
        slots = []

    ordered_labels = ["P1-C1", "P1-C2", "P2-C1", "P2-C2"]
    slot_map = {}
    for _s in [s for s in slots if isinstance(s, dict)]:
        _lbl = str(_s.get("slot_label") or _s.get("slot") or "")
        if _lbl and _lbl not in slot_map:
            slot_map[_lbl] = _s
    slots = [slot_map.get(lbl, {"slot_label": lbl, "char_name": "No character", "moves": []}) for lbl in ordered_labels]

    highlight_keys, _punish_adv, _punish_source_slot = _normal_preview_highlight_keys(slots, highlight_mode, selection)
    status_text = _normal_preview_status(slots, highlight_mode, selection)

    control_y = rect.y + 28
    control_h = 17
    label_s = smallfont.render("Highlight", True, GUI_TEXT_DIM)
    surf.blit(label_s, (rect.x + 10, control_y + (control_h - label_s.get_height()) // 2))
    control_x = rect.x + 10 + label_s.get_width() + 7
    for control_key in ("fast", "damage", "adv_block"):
        meta = _NORMAL_PREVIEW_MODE_META[control_key]
        control_w = max(38, smallfont.size(meta["label"])[0] + 14)
        control_rect = pygame.Rect(control_x, control_y, control_w, control_h)
        draw_glass_button(
            surf,
            control_rect,
            meta["label"],
            smallfont,
            active=(highlight_mode == control_key),
            hover=control_rect.collidepoint(mouse_pos),
            accent=meta["color"],
            fill=(24, 29, 41),
        )
        interaction["controls"][control_key] = control_rect.copy()
        control_x += control_w + 4

    divider_x = control_x + 2
    pygame.draw.line(surf, (62, 73, 99), (divider_x, control_y + 2), (divider_x, control_y + control_h - 2))
    control_x = divider_x + 6
    punish_meta = _NORMAL_PREVIEW_MODE_META["punish"]
    punish_rect = pygame.Rect(control_x, control_y, max(48, smallfont.size(punish_meta["label"])[0] + 14), control_h)
    draw_glass_button(
        surf,
        punish_rect,
        punish_meta["label"],
        smallfont,
        active=(highlight_mode == "punish"),
        hover=punish_rect.collidepoint(mouse_pos),
        accent=punish_meta["color"],
        fill=(24, 29, 41),
    )
    interaction["controls"]["punish"] = punish_rect.copy()
    control_x = punish_rect.right + 4

    live_meta = _NORMAL_PREVIEW_MODE_META["live_punish"]
    live_rect = pygame.Rect(control_x, control_y, max(38, smallfont.size(live_meta["label"])[0] + 14), control_h)
    draw_glass_button(
        surf,
        live_rect,
        live_meta["label"],
        smallfont,
        active=(highlight_mode == "live_punish"),
        hover=live_rect.collidepoint(mouse_pos),
        accent=live_meta["color"],
        fill=(24, 29, 41),
    )
    interaction["controls"]["live_punish"] = live_rect.copy()
    control_x = live_rect.right + 9
    status_w = max(0, rect.right - control_x - 10)
    if status_w >= 48:
        status_s = _fit_text(smallfont, status_text, GUI_TEXT_MUTED, status_w)
        surf.blit(status_s, (control_x, control_y + (control_h - status_s.get_height()) // 2))

    pygame.draw.line(surf, (52, 61, 82), (rect.x + 8, rect.y + 49), (rect.right - 8, rect.y + 49))

    pad, gap = 8, 10
    top = rect.y + 54
    card_h = max(44, rect.height - 62)
    count = 4
    card_w = max(140, (rect.width - pad * 2 - gap * (count - 1)) // count)
    dense = rect.height < 260 or rect.width < 930
    header_h = 24 if not dense else 22
    table_header_h = 16 if not dense else 14

    def _section_for_label(label: str) -> str:
        low = str(label or "").lower()
        if low.startswith("j.") or low.startswith("j"):
            return "Jump"
        if low.startswith("2"):
            return "Crouch"
        if low.startswith(("3", "4", "6")):
            return "Command"
        return "Stand"

    for si, slot in enumerate(slots):
        card_x = rect.x + pad + si * (card_w + gap)
        card = pygame.Rect(card_x, top, card_w, card_h)
        slot_label = str(slot.get("slot_label") or slot.get("slot") or f"S{si + 1}")
        slot_fx = scan_fx_by_slot.get(slot_label, {}) if isinstance(scan_fx_by_slot, dict) else {}

        card_fill = pygame.Surface((card.width, card.height), pygame.SRCALPHA)
        _draw_vertical_gradient(card_fill, card_fill.get_rect(), (18, 22, 32), (12, 14, 21), 238)
        surf.blit(card_fill, card.topleft)

        char_name = str(slot.get("char_name") or slot.get("character") or slot.get("name") or "No character")
        accent = _slot_accent_for_label(slot_label, muted=True)
        pygame.draw.rect(surf, (43, 52, 72), card, 1, border_radius=6)
        pygame.draw.rect(surf, accent, pygame.Rect(card.x, card.y, 3, card.height), border_radius=2)

        header_rect = pygame.Rect(card.x + 1, card.y + 1, card.width - 2, header_h)
        _draw_vertical_gradient(surf, header_rect, (25, 30, 43), (17, 20, 30), 236)
        pygame.draw.rect(surf, (180, 205, 245, 16), pygame.Rect(header_rect.x + 4, header_rect.y + 2, header_rect.width - 8, max(2, header_rect.height // 5)), border_radius=3)
        pygame.draw.line(surf, (44, 52, 72), (header_rect.x + 6, header_rect.bottom), (header_rect.right - 6, header_rect.bottom))
        slot_s = _render_outlined_text(font, slot_label, accent, (0, 0, 0), 76, outline_px=1)
        surf.blit(slot_s, (card.x + 9, card.y + 4))
        name_s = _fit_text(smallfont, char_name, GUI_TEXT_MUTED, card.width - 90)
        surf.blit(name_s, (card.x + 72, card.y + 6))

        moves = slot.get("moves") or []
        if not isinstance(moves, list):
            moves = []
        visible_moves = _normal_visible_moves(moves, slot)
        is_empty_card = len(visible_moves) <= 0

        cur_id = slot.get("cur_anim") or slot.get("current_anim") or slot.get("mv_id_display") or slot.get("move_id")
        try:
            cur_id = int(cur_id) if cur_id is not None else None
        except Exception:
            cur_id = None
        cur_label = str(slot.get("cur_label") or slot.get("current_move") or slot.get("mv_label") or "").strip().lower()

        table_x = card.x + 6
        table_y = card.y + header_h + 4
        table_w = card.width - 12
        table_h = card.height - header_h - 8
        metric_headers = ("S", "A", "HS", "BS", "BA", "DMG")
        metric_count = len(metric_headers)
        # The preview prioritizes startup, active, recovery, advantage, and
        # damage. Raw hitstun/blockstun remain available in the Frame Data view.
        preferred_move_col_w = 48 if card.width >= 260 else 42
        metric_col_w = max(1, (table_w - preferred_move_col_w) // metric_count)
        move_col_w = table_w - metric_col_w * metric_count
        grid_x, grid_y = table_x, table_y
        grid_w, grid_h = table_w, table_h

        table_bg = pygame.Rect(grid_x, grid_y, grid_w, grid_h)
        pygame.draw.rect(surf, (13, 16, 24), table_bg, border_radius=4)
        pygame.draw.rect(surf, (49, 59, 82), table_bg, 1, border_radius=4)

        hdr = pygame.Rect(grid_x, grid_y, grid_w, table_header_h)
        pygame.draw.rect(surf, (18, 22, 31), hdr, border_radius=4)
        header_labels = ("Attack",) + metric_headers
        header_widths = (move_col_w,) + (metric_col_w,) * metric_count
        cell_x = grid_x
        for i, (txt, cell_w) in enumerate(zip(header_labels, header_widths)):
            header_cell = pygame.Rect(cell_x, grid_y, cell_w, table_header_h)
            header_fill = (29, 35, 49) if i % 2 == 0 else (23, 28, 40)
            pygame.draw.rect(surf, header_fill, header_cell)
            pygame.draw.rect(surf, (57, 68, 94), header_cell, 1)
            hdr_s = smallfont.render(txt, True, GUI_TEXT_DIM)
            surf.blit(hdr_s, (header_cell.x + (header_cell.width - hdr_s.get_width()) // 2, header_cell.y + (header_cell.height - hdr_s.get_height()) // 2))
            cell_x += cell_w

        if is_empty_card:
            empty_body = pygame.Rect(grid_x + 1, grid_y + table_header_h + 1, grid_w - 2, grid_h - table_header_h - 2)
            pygame.draw.rect(surf, (11, 14, 21), empty_body, border_radius=4)
            had_scan_entry = bool(slot.get("_had_scan_entry"))
            if char_name == "No character":
                empty_msg = "No character loaded"
                sub_msg = "This slot is currently empty"
            elif had_scan_entry:
                empty_msg = "No normals returned"
                sub_msg = "The scan completed, but this slot returned no normal data"
            else:
                empty_msg = "Scanning character"
                sub_msg = "Normals will appear here when data is available"
            def _wrap_preview_message(text, text_font, max_width):
                words = str(text or "").split()
                if not words:
                    return [""]
                lines = []
                current = ""
                for word in words:
                    candidate = word if not current else f"{current} {word}"
                    if current and text_font.size(candidate)[0] > max_width:
                        lines.append(current)
                        current = word
                    else:
                        current = candidate
                if current:
                    lines.append(current)
                return lines

            title_lines = _wrap_preview_message(empty_msg, font, empty_body.width - 16)
            detail_lines = _wrap_preview_message(sub_msg, smallfont, empty_body.width - 16)
            line_items = [(line, font) for line in title_lines] + [(line, smallfont) for line in detail_lines]
            total_h = sum(text_font.get_height() + 2 for _, text_font in line_items)
            text_y = empty_body.y + max(8, (empty_body.height - total_h) // 2)
            for line, text_font in line_items:
                line_s = _render_outlined_text(text_font, line, GUI_TEXT_DIM, (0, 0, 0), empty_body.width - 16, 1)
                surf.blit(line_s, (empty_body.x + (empty_body.width - line_s.get_width()) // 2, text_y))
                text_y += text_font.get_height() + 2
            continue

        row_count = max(1, len(visible_moves))
        available_h = max(1, grid_h - table_header_h)
        row_h = max(13, min(18, available_h // row_count))

        y = grid_y + table_header_h
        last_section = None
        sweep_frac = float(slot_fx.get("row_sweep", 0.0) or 0.0)
        for mi, mv in enumerate(visible_moves):
            if not isinstance(mv, dict):
                continue
            label = _normal_move_label(mv)
            section = _section_for_label(label)
            if last_section is not None and section != last_section:
                pygame.draw.line(surf, (66, 75, 98), (grid_x + 2, y), (grid_x + grid_w - 3, y))
                pygame.draw.line(surf, (*accent, 45), (grid_x + 2, y + 1), (grid_x + 30, y + 1))
            last_section = section

            row = pygame.Rect(grid_x, y, grid_w, row_h)
            row_fill = (16, 19, 28) if mi % 2 == 0 else (13, 16, 24)
            mv_id = mv.get("id") or mv.get("anim") or mv.get("move_id")
            try:
                mv_id = int(mv_id) if mv_id is not None else None
            except Exception:
                mv_id = None
            row_label_canon = _normal_canon_label(label)
            row_key = _normal_preview_key(mv)
            is_selected = _normal_preview_selection_matches(selection, slot_label, mv)
            is_highlighted = row_key in highlight_keys.get(slot_label, set())
            cur_label_canon = _normal_canon_label(cur_label)
            if cur_label_canon:
                is_current = (row_label_canon == cur_label_canon)
            else:
                is_current = (cur_id is not None and mv_id == cur_id)
            if is_current:
                glow = pygame.Surface((row.width, row.height), pygame.SRCALPHA)
                glow.fill((*accent, 48))
                surf.blit(glow, row.topleft)
                pygame.draw.rect(surf, (*accent, 130), row, 1)
                pygame.draw.line(surf, (*accent, 95), (row.x + 1, row.bottom - 1), (row.right - 1, row.bottom - 1))
                if sweep_frac > 0.0:
                    sweep_x = row.x - 20 + int((row.width + 40) * sweep_frac)
                    sweep = pygame.Surface((24, row.height + 6), pygame.SRCALPHA)
                    pygame.draw.rect(sweep, (*_brighten(accent, 28), 70), pygame.Rect(0, 0, 10, row.height + 6), border_radius=4)
                    pygame.draw.rect(sweep, (*_brighten(accent, 48), 28), pygame.Rect(8, 0, 16, row.height + 6), border_radius=4)
                    surf.blit(sweep, (sweep_x, row.y - 3), special_flags=pygame.BLEND_ALPHA_SDL2 if hasattr(pygame, 'BLEND_ALPHA_SDL2') else 0)
            else:
                pygame.draw.rect(surf, row_fill, row)
                pygame.draw.rect(surf, (34, 41, 58), row, 1)
            pygame.draw.line(surf, (28, 34, 48), (row.x + 1, row.bottom), (row.right - 1, row.bottom))

            if is_highlighted:
                highlight_col = _NORMAL_PREVIEW_MODE_META.get(highlight_mode, {}).get("color", GUI_ACCENT_BLUE)
                highlight_fill = pygame.Surface((row.width, row.height), pygame.SRCALPHA)
                highlight_fill.fill((*highlight_col, 28))
                surf.blit(highlight_fill, row.topleft)
                pygame.draw.rect(surf, (*highlight_col, 178), pygame.Rect(row.x + 1, row.y + 1, 3, max(1, row.height - 2)), border_radius=1)
                pygame.draw.line(surf, (*highlight_col, 108), (row.x + 5, row.bottom - 2), (row.right - 3, row.bottom - 2))

            if is_selected:
                selection_col = (244, 180, 92)
                pygame.draw.rect(surf, (*selection_col, 205), row.inflate(-2, -2), 1, border_radius=2)
                pygame.draw.rect(surf, (*selection_col, 190), pygame.Rect(row.right - 4, row.y + 2, 2, max(1, row.height - 4)), border_radius=1)

            interaction["rows"].append({"rect": row.copy(), "slot_label": slot_label, "key": row_key})

            label_col = GUI_TEXT if (is_current or is_selected) else (218, 224, 234)
            label_s = _render_outlined_text(smallfont, label, label_col, (0, 0, 0), move_col_w - 8, outline_px=1)
            surf.blit(label_s, (row.x + 6, row.y + (row.height - label_s.get_height()) // 2))

            startup = _normal_int(mv, "startup", "start", "active_start")
            a1 = _normal_int(mv, "active_start", "a_start")
            a2 = _normal_int(mv, "active_end", "a_end")
            recovery = _normal_recovery(mv)
            hit = _normal_int(mv, "hitstun", "hit", "h")
            block = _normal_int(mv, "blockstun", "block", "b")
            adv_block = _manual_observed_for_slot(slot, label)
            if adv_block is None:
                adv_block = _normal_advantage(mv, "block", prefer_observed=True)
            if adv_block is None and block is not None and recovery is not None:
                adv_block = int(block) - int(recovery)
            damage = _normal_damage(mv)
            active_txt = "-"
            if a1 is not None and a2 is not None:
                active_txt = f"{a1}-{a2}"
            elif a1 is not None:
                active_txt = str(a1)
            values = [
                "-" if startup is None else str(startup),
                active_txt,
                "-" if hit is None else str(hit),
                "-" if block is None else str(block),
                "-" if adv_block is None else f"{adv_block:+d}",
                "-" if damage is None else str(damage),
            ]
            values = [str(value).replace(",", "") for value in values]
            patch_fields = mv.get("_fd_patch_fields") or set()
            try:
                patch_fields = set(patch_fields)
            except Exception:
                patch_fields = set()
            metric_groups = ("startup", "active", "hitstun", "blockstun", "adv_block", "damage")
            value_col = GUI_TEXT if is_current else (205, 211, 224)
            patched_col = _brighten(accent, 52) if is_current else (145, 194, 255)
            for i, value in enumerate(values):
                col_left = grid_x + move_col_w + i * metric_col_w
                is_patched_metric = metric_groups[i] in patch_fields
                if is_patched_metric:
                    chip_rect = pygame.Rect(col_left + 2, row.y + 2, metric_col_w - 4, max(1, row.height - 4))
                    chip = pygame.Surface((chip_rect.width, chip_rect.height), pygame.SRCALPHA)
                    pygame.draw.rect(chip, (*patched_col, 28), chip.get_rect(), border_radius=3)
                    pygame.draw.rect(chip, (*patched_col, 92), chip.get_rect(), 1, border_radius=3)
                    surf.blit(chip, chip_rect.topleft)
                draw_col = patched_col if is_patched_metric else value_col
                val_s = _render_outlined_text(smallfont, value, draw_col, (0, 0, 0), metric_col_w - 6, outline_px=1)
                surf.blit(val_s, (col_left + (metric_col_w - val_s.get_width()) // 2, row.y + (row.height - val_s.get_height()) // 2))
            y += row_h

        # Full-height separators are drawn after the row fills so each column
        # remains visible across the complete data grid.
        for i in range(metric_count + 1):
            vx = grid_x + move_col_w + metric_col_w * i
            pygame.draw.line(surf, (62, 73, 99), (vx, grid_y + 1), (vx, grid_y + grid_h - 2))
        pygame.draw.line(surf, (62, 73, 99), (grid_x, grid_y + table_header_h), (grid_x + grid_w, grid_y + table_header_h))

    return interaction


_QUICK_ASSIST_STRENGTH_MARKS = ("α", "β", "γ")
# UMvC3-style assist strength colors: Alpha red, Beta green, Gamma blue.
_QUICK_ASSIST_STRENGTH_COLORS = (
    (236, 70, 82),
    (74, 214, 114),
    (82, 156, 255),
)


def _quick_assist_strength_meta(quick_index: int, is_default: bool = False) -> tuple[str, tuple[int, int, int]] | None:
    """Return the visual assist-strength marker for custom quick assists.

    This is intentionally display-only. The quick-assist JSON labels stay
    unchanged for lookup/write logic, while the first three non-default buttons
    get Marvel-style Alpha/Beta/Gamma markers.
    """
    if is_default:
        return None
    try:
        qi = int(quick_index)
    except Exception:
        return None
    if 0 <= qi < len(_QUICK_ASSIST_STRENGTH_MARKS):
        return _QUICK_ASSIST_STRENGTH_MARKS[qi], _QUICK_ASSIST_STRENGTH_COLORS[qi]
    return None


def _quick_assist_display_label(label: str, quick_index: int, is_default: bool = False) -> str:
    """Return the raw visible move label. Strength marks are drawn separately."""
    return str(label or "")


def _quick_assist_accent_for_label(
    label: str,
    is_default: bool = False,
    quick_index: int | None = None,
) -> tuple[int, int, int]:
    """Return the accent color used by quick-assist buttons."""
    if is_default:
        return GUI_TEXT_DIM
    meta = _quick_assist_strength_meta(quick_index, is_default) if quick_index is not None else None
    if meta:
        return meta[1]
    return GUI_CONFIRM


def _draw_quick_assist_button(
    surf: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    font: pygame.font.Font,
    *,
    active: bool = False,
    hover: bool = False,
    accent: tuple[int, int, int] = GUI_ACCENT_BLUE,
    fill: tuple[int, int, int] | None = None,
    mark_meta: tuple[str, tuple[int, int, int]] | None = None,
) -> None:
    """Draw a quick-assist button with a colored Alpha/Beta/Gamma marker lane."""
    draw_glass_button(
        surf,
        rect,
        "",
        font,
        active=active,
        hover=hover,
        accent=accent,
        fill=fill,
        align="center",
    )

    draw_rect = rect.move(0, -1 if hover else 0)
    text_col = GUI_TEXT if active or hover else GUI_TEXT_MUTED

    if mark_meta:
        mark, mark_col = mark_meta
        mark_lane_w = max(20, min(26, rect.width // 4))
        divider_x = draw_rect.x + mark_lane_w

        mark_surf = _render_outlined_text(
            font,
            mark,
            mark_col,
            (0, 0, 0),
            max(8, mark_lane_w - 5),
            outline_px=1,
        )
        surf.blit(
            mark_surf,
            (
                draw_rect.x + (mark_lane_w - mark_surf.get_width()) // 2,
                draw_rect.y + (draw_rect.height - mark_surf.get_height()) // 2,
            ),
        )

        divider_top = draw_rect.y + 4
        divider_bottom = draw_rect.bottom - 4
        pygame.draw.line(
            surf,
            _darken(mark_col, 46),
            (divider_x, divider_top),
            (divider_x, divider_bottom),
            1,
        )
        pygame.draw.line(
            surf,
            _brighten(mark_col, 20),
            (divider_x + 1, divider_top),
            (divider_x + 1, divider_bottom),
            1,
        )

        text_x = divider_x + 6
        text_w = max(8, draw_rect.right - text_x - 6)
        label_surf = _render_outlined_text(
            font,
            label,
            text_col,
            (0, 0, 0),
            text_w,
            outline_px=1,
        )
        tx = text_x + (text_w - label_surf.get_width()) // 2
    else:
        text_w = max(8, draw_rect.width - 12)
        label_surf = _render_outlined_text(
            font,
            label,
            text_col,
            (0, 0, 0),
            text_w,
            outline_px=1,
        )
        tx = draw_rect.x + (draw_rect.width - label_surf.get_width()) // 2

    ty = draw_rect.y + (draw_rect.height - label_surf.get_height()) // 2
    surf.blit(label_surf, (tx, ty))


def _ease_out_cubic(t: float) -> float:
    t = max(0.0, min(1.0, float(t)))
    return 1.0 - ((1.0 - t) * (1.0 - t) * (1.0 - t))


def _ease_in_out_smootherstep(t: float) -> float:
    """Smooth 0..1 easing with gentle start and finish.

    This reads better for short UI travel than a pure ease-out curve because
    the selector does not launch at full speed on the first visible frame.
    """
    t = max(0.0, min(1.0, float(t)))
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)

def _apply_panel_element_enter_animation(
    panel_surf: pygame.Surface,
    panel_fx: dict | None,
    now: float,
) -> pygame.Surface:
    """Cascade fighter-card contents in after the card itself starts entering.

    This is intentionally cheap: it slices the already-rendered card into a few
    horizontal content bands and gives each band a tiny delayed fade/slide.  The
    scanner profile cache buys us enough room for this polish without creating
    new per-widget draw state or expensive per-pixel work every frame.
    """
    if not isinstance(panel_fx, dict):
        return panel_surf
    entry = panel_fx.get("panel_enter")
    if not isinstance(entry, dict):
        return panel_surf

    try:
        start = float(entry.get("start", 0.0) or 0.0)
        dur = max(0.001, float(entry.get("dur", 0.68) or 0.68))
    except Exception:
        return panel_surf
    if start <= 0.0:
        return panel_surf

    raw = (float(now) - start) / dur
    if raw <= 0.0:
        raw = 0.0
    if raw >= 1.0:
        return panel_surf

    w, h = panel_surf.get_size()
    if w <= 0 or h <= 0:
        return panel_surf

    out = pygame.Surface((w, h), pygame.SRCALPHA)

    # Leave a low-alpha ghost of the full card so the panel never looks empty
    # while the content bands are staggering in.
    ghost = panel_surf.copy()
    ghost.set_alpha(max(20, min(110, int(28 + 72 * _ease_out_cubic(raw)))))
    out.blit(ghost, (0, 0))

    bands = [
        (0, int(h * 0.35), 0.00, -10, 5),       # portrait/header/HP
        (int(h * 0.27), int(h * 0.56), 0.08, -8, 4),   # pool/baroque
        (int(h * 0.48), int(h * 0.74), 0.16, -6, 3),   # move/status
        (int(h * 0.66), h, 0.24, 0, 5),         # buttons/quick assists
    ]

    for y0, y1, delay, dx, dy in bands:
        y0 = max(0, min(h, int(y0)))
        y1 = max(y0, min(h, int(y1)))
        if y1 <= y0:
            continue
        denom = max(0.001, 1.0 - delay)
        local = max(0.0, min(1.0, (raw - delay) / denom))
        eased = _ease_out_cubic(local)
        if eased <= 0.0:
            continue
        piece = panel_surf.subsurface(pygame.Rect(0, y0, w, y1 - y0)).copy()
        piece.set_alpha(max(0, min(255, int(255 * eased))))
        out.blit(piece, (int((1.0 - eased) * dx), y0 + int((1.0 - eased) * dy)))

    return out

def draw_quick_assist_footer(
    surf: pygame.Surface,
    panel_rect: pygame.Rect,
    slot_label: str,
    snap: dict | None,
    smallfont: pygame.font.Font,
    *,
    mx_local: int,
    my_local: int,
    btn_y: int,
    get_quick_defs_fn,
    active_quick_index: int | None = None,
    flash_quick_index: int | None = None,
    slide_anim: dict | None = None,
) -> dict:
    """Draw a compact one-line quick-assist strip.

    The first polish pass used a two-line footer with a visible header plus
    buttons. It looked nice, but it stole too much vertical room from the
    character panels. This version keeps the same click behavior and assist
    logic, but compresses the UI into one clean row:

        Assist | move | move | move | default
    """
    quick_defs = []

    if snap:
        try:
            quick_defs = get_quick_defs_fn(slot_label, snap)[:4]
        except Exception:
            quick_defs = []

    if not quick_defs and snap:
        quick_defs = [
            {"label": "304", "table": 304},
            {"label": "305", "table": 305},
            {"label": "306", "table": 306},
            {"label": "Default", "default": True},
        ]

    if not quick_defs:
        return {}

    qa_count = min(4, len(quick_defs))
    qa_gap = 6
    qa_h = 20
    label_w = 64
    side_pad = 10

    qa_y = max(72, btn_y - qa_h - 10)
    strip_y = max(0, qa_y - 5)
    strip_h = min(panel_rect.height - strip_y - 4, qa_h + 10)
    strip_rect = pygame.Rect(6, strip_y, panel_rect.width - 12, strip_h)

    _draw_vertical_gradient(
        surf,
        strip_rect,
        (22, 25, 35),
        (15, 17, 24),
        230,
    )
    pygame.draw.rect(surf, (54, 62, 82), strip_rect, 1, border_radius=5)

    label_surf = smallfont.render("Assist", True, GUI_TEXT_DIM)
    surf.blit(
        label_surf,
        (
            strip_rect.x + 8,
            qa_y + (qa_h - label_surf.get_height()) // 2,
        ),
    )

    qa_x0 = strip_rect.x + label_w
    qa_total_w = strip_rect.width - label_w - side_pad
    qa_w = max(48, int((qa_total_w - qa_gap * (qa_count - 1)) / qa_count))

    out = {}

    # Precompute button geometry so the selected marker can slide from the old
    # quick assist to the new quick assist without keeping the old one lit.
    button_rows = []
    for qi, quick in enumerate(quick_defs):
        qx = qa_x0 + qi * (qa_w + qa_gap)
        qrect_local = pygame.Rect(qx, qa_y, qa_w, qa_h)
        raw_qlabel = str(quick.get("label", f"A{qi + 1}"))
        is_default_quick = bool(quick.get("default", False))
        qlabel = _quick_assist_display_label(raw_qlabel, qi, is_default_quick)
        mark_meta = _quick_assist_strength_meta(qi, is_default_quick)
        accent = _quick_assist_accent_for_label(raw_qlabel, is_default_quick, qi)
        button_rows.append((qi, quick, qrect_local, qlabel, accent, mark_meta))

    # Sliding selection plate. Use a time-based smootherstep motion, a longer
    # duration, and no immediate selected-button fill during travel. That keeps
    # the change readable at 60 FPS instead of feeling like a jump plus a small
    # underline animation.
    selected_rect = None
    selected_accent = GUI_ACCENT_BLUE
    slide_is_active = False
    slide_frac = 1.0

    if active_quick_index is not None:
        for qi, _quick, qrect_local, _qlabel, accent, _mark_meta in button_rows:
            if qi == int(active_quick_index):
                selected_rect = qrect_local
                selected_accent = accent
                break

    if selected_rect is not None:
        marker_rect = selected_rect.copy()
        src_rect = None
        dst_rect = selected_rect.copy()

        if isinstance(slide_anim, dict):
            try:
                src_i = int(slide_anim.get("from", active_quick_index))
                dst_i = int(slide_anim.get("to", active_quick_index))
                start_ts = float(slide_anim.get("start", 0.0) or 0.0)
                dur = max(0.001, float(slide_anim.get("dur", 0.38) or 0.38))

                if dst_i == int(active_quick_index) and start_ts > 0.0:
                    for qi, _quick, qrect_local, _qlabel, _accent, _mark_meta in button_rows:
                        if qi == src_i:
                            src_rect = qrect_local
                        if qi == dst_i:
                            dst_rect = qrect_local

                    if src_rect is not None and dst_rect is not None:
                        raw_frac = max(0.0, min(1.0, (time.time() - start_ts) / dur))
                        slide_frac = _ease_in_out_smootherstep(raw_frac)
                        slide_is_active = raw_frac < 0.995 and src_i != dst_i

                        marker_rect = pygame.Rect(
                            round(src_rect.x + (dst_rect.x - src_rect.x) * slide_frac),
                            round(src_rect.y + (dst_rect.y - src_rect.y) * slide_frac),
                            round(src_rect.width + (dst_rect.width - src_rect.width) * slide_frac),
                            round(src_rect.height + (dst_rect.height - src_rect.height) * slide_frac),
                        )
            except Exception:
                marker_rect = selected_rect.copy()
                slide_is_active = False

        # Motion trail. This is subtle, but it gives the selector a continuous
        # path across the buttons instead of only a single hard-edged rectangle.
        if slide_is_active and src_rect is not None and dst_rect is not None:
            for back_i, alpha_mul in ((2, 0.22), (1, 0.38)):
                lag = max(0.0, slide_frac - 0.08 * back_i)
                trail_rect = pygame.Rect(
                    round(src_rect.x + (dst_rect.x - src_rect.x) * lag),
                    round(src_rect.y + (dst_rect.y - src_rect.y) * lag),
                    round(src_rect.width + (dst_rect.width - src_rect.width) * lag),
                    round(src_rect.height + (dst_rect.height - src_rect.height) * lag),
                )
                trail = pygame.Surface((trail_rect.width + 14, trail_rect.height + 14), pygame.SRCALPHA)
                pygame.draw.rect(
                    trail,
                    (*selected_accent, int(46 * alpha_mul)),
                    pygame.Rect(7, 7, trail_rect.width, trail_rect.height),
                    border_radius=8,
                )
                surf.blit(trail, (trail_rect.x - 7, trail_rect.y - 7))

        # Main selector plate. Keep it pronounced, but with a smoother glow and
        # a softer top sheen so the movement reads cleanly.
        glow = pygame.Surface((marker_rect.width + 20, marker_rect.height + 20), pygame.SRCALPHA)
        pygame.draw.rect(
            glow,
            (*selected_accent, 68),
            pygame.Rect(10, 10, marker_rect.width, marker_rect.height),
            border_radius=8,
        )
        pygame.draw.rect(
            glow,
            (*selected_accent, 28),
            pygame.Rect(4, 4, marker_rect.width + 12, marker_rect.height + 12),
            2,
            border_radius=10,
        )
        surf.blit(glow, (marker_rect.x - 10, marker_rect.y - 10))

        plate = pygame.Surface((marker_rect.width + 4, marker_rect.height + 4), pygame.SRCALPHA)
        plate_rect = plate.get_rect()
        pygame.draw.rect(
            plate,
            (*selected_accent, 46),
            plate_rect,
            border_radius=6,
        )
        pygame.draw.rect(
            plate,
            (150, 165, 190, 18),
            pygame.Rect(2, 2, plate_rect.width - 4, max(2, plate_rect.height // 5)),
            border_radius=5,
        )
        pygame.draw.rect(
            plate,
            (*selected_accent, 165),
            plate_rect,
            2,
            border_radius=6,
        )
        surf.blit(plate, (marker_rect.x - 2, marker_rect.y - 2))

        rail_h = 4
        rail_rect = pygame.Rect(
            marker_rect.x + 5,
            marker_rect.bottom - rail_h - 1,
            max(4, marker_rect.width - 10),
            rail_h,
        )
        pygame.draw.rect(surf, selected_accent, rail_rect, border_radius=2)

        # Tiny settle pulse after the slide lands.
        if isinstance(slide_anim, dict):
            try:
                start_ts = float(slide_anim.get("start", 0.0) or 0.0)
                dur = max(0.001, float(slide_anim.get("dur", 0.38) or 0.38))
                raw = (time.time() - start_ts) / dur if start_ts else 99.0
                if 1.0 <= raw <= 1.32:
                    settle_t = (raw - 1.0) / 0.32
                    ring_alpha = int((1.0 - settle_t) * 85)
                    ring_expand = int(settle_t * 7)
                    pulse_rect = marker_rect.inflate(6 + ring_expand * 2, 4 + ring_expand * 2)
                    pulse = pygame.Surface((pulse_rect.width + 8, pulse_rect.height + 8), pygame.SRCALPHA)
                    pygame.draw.rect(
                        pulse,
                        (*selected_accent, ring_alpha),
                        pygame.Rect(4, 4, pulse_rect.width, pulse_rect.height),
                        2,
                        border_radius=10,
                    )
                    surf.blit(pulse, (pulse_rect.x - 4, pulse_rect.y - 4))
            except Exception:
                pass

    for qi, quick, qrect_local, qlabel, accent, mark_meta in button_rows:
        qhover = qrect_local.collidepoint(mx_local, my_local)

        is_selected = active_quick_index is not None and int(active_quick_index) == qi
        is_flashing = flash_quick_index is not None and int(flash_quick_index) == qi

        # During a slide, the moving selector is the highlight. Do not also
        # repaint the destination button as fully selected on frame 1; that
        # double-state is what made the animation feel choppy.
        is_selected_fill = is_selected and not slide_is_active
        active = bool(quick.get("active", False)) or is_selected_fill or is_flashing

        fill = (58, 72, 104) if is_selected_fill else (35, 43, 62)
        if is_flashing:
            fill = _brighten(fill, 30)

        _draw_quick_assist_button(
            surf,
            qrect_local,
            qlabel,
            smallfont,
            active=active,
            hover=qhover,
            accent=accent,
            fill=fill,
            mark_meta=mark_meta,
        )

        if is_selected_fill:
            pygame.draw.rect(
                surf,
                (*accent, 95),
                qrect_local.inflate(-3, -3),
                2,
                border_radius=4,
            )

        qclick = pygame.Rect(
            panel_rect.x + qrect_local.x,
            panel_rect.y + qrect_local.y,
            qrect_local.width,
            qrect_local.height,
        ).inflate(8, 8)

        out[(slot_label, qi)] = qclick

    return out

def _panel_bar_fraction(value, maximum) -> float:
    try:
        v = float(value or 0)
        m = float(maximum or 0)
        if m <= 0:
            return 0.0
        return max(0.0, min(1.0, v / m))
    except Exception:
        return 0.0


def _draw_panel_stat_bar(
    surf: pygame.Surface,
    rect: pygame.Rect,
    fraction: float,
    fill_col: tuple[int, int, int],
    *,
    empty_col: tuple[int, int, int] = (24, 27, 36),
    border_col: tuple[int, int, int] = (58, 66, 88),
) -> None:
    fraction = max(0.0, min(1.0, float(fraction or 0.0)))
    pygame.draw.rect(surf, empty_col, rect, border_radius=3)
    pygame.draw.rect(surf, border_col, rect, 1, border_radius=3)

    if fraction > 0.0:
        fill_w = max(2, int((rect.width - 2) * fraction))
        fill_rect = pygame.Rect(rect.x + 1, rect.y + 1, fill_w, max(1, rect.height - 2))
        _draw_vertical_gradient(
            surf,
            fill_rect,
            _brighten(fill_col, 24),
            _darken(fill_col, 18),
            235,
        )
        # Soft internal highlight, graphite-blue rather than white.
        hi = pygame.Rect(fill_rect.x + 1, fill_rect.y + 1, max(1, fill_rect.width - 2), max(1, fill_rect.height // 3))
        pygame.draw.rect(surf, (170, 190, 225, 20), hi, border_radius=2)


def _meter_fraction_from_snap(snap: dict | None) -> tuple[float, str]:
    if not isinstance(snap, dict):
        return 0.0, "0"
    meter_val = snap.get("meter")
    try:
        raw = float(meter_val if meter_val is not None else 0.0)
    except Exception:
        raw = 0.0
    frac = max(0.0, min(1.0, raw / 50000.0))
    bars = int(max(0, min(5, raw // 10000)))
    return frac, f"{bars}/5"


def _meter_value_text_color(raw_meter: int | float) -> tuple[int, int, int]:
    """Color meter text by raw meter amount.

    Near zero is intentionally dark/muted. At each bar threshold it brightens,
    then ramps through cool light colors until it reaches red at 50k/max.
    """
    try:
        raw = max(0.0, min(50000.0, float(raw_meter or 0)))
    except Exception:
        raw = 0.0

    if raw <= 0:
        return (72, 78, 92)

    stops = [
        (0.0,     (72, 78, 92)),     # near zero: dark steel
        (10000.0, (132, 176, 245)),   # lvl 1: light blue
        (20000.0, (110, 218, 190)),   # lvl 2: mint/cyan
        (30000.0, (230, 210, 120)),   # lvl 3: pale gold
        (40000.0, (245, 160, 95)),    # lvl 4: warm orange
        (50000.0, (235, 80, 95)),     # max: red
    ]

    for i in range(len(stops) - 1):
        a_raw, a_col = stops[i]
        b_raw, b_col = stops[i + 1]
        if raw <= b_raw:
            t = (raw - a_raw) / max(1.0, b_raw - a_raw)
            return (
                int(a_col[0] + (b_col[0] - a_col[0]) * t),
                int(a_col[1] + (b_col[1] - a_col[1]) * t),
                int(a_col[2] + (b_col[2] - a_col[2]) * t),
            )

    return stops[-1][1]


def draw_panel_polished_stats(
    surf: pygame.Surface,
    rect: pygame.Rect,
    snap: dict | None,
    portrait: pygame.Surface | None,
    font: pygame.font.Font,
    smallfont: pygame.font.Font,
    header: str,
    t_ms: int,
    *,
    assist_label: str = "--",
    panel_fx: dict | None = None,
) -> None:
    """Compact fighter card with strong hierarchy plus lightweight premium FX."""
    panel_fx = panel_fx or {}
    now = time.time()

    def _fx(entry):
        if not isinstance(entry, dict):
            return 0.0
        try:
            start = float(entry.get("start", 0.0) or 0.0)
            dur = max(0.001, float(entry.get("dur", 0.3) or 0.3))
        except Exception:
            return 0.0
        if not start:
            return 0.0
        return max(0.0, min(1.0, (now - start) / dur))

    _draw_vertical_gradient(surf, rect, (20, 22, 30), (14, 15, 22), 255)
    if not isinstance(snap, dict):
        pygame.draw.rect(surf, (55, 63, 84), rect, 1, border_radius=5)
        title = _render_outlined_text(smallfont, f"{header}  empty", GUI_TEXT_DIM, (0, 0, 0), rect.width - 20, 1)
        surf.blit(title, (10, 8))
        return

    accent = _slot_accent_for_label(header, muted=False)
    move_preview = str(snap.get("mv_label") or "").strip().lower()
    try:
        early_move_id = int(snap.get("mv_id_display") or 0)
    except Exception:
        early_move_id = 0
    try:
        early_hp_cur = int(snap.get("cur") or 0)
    except Exception:
        early_hp_cur = 0

    assist_state_ids = {420, 424, 425, 426, 427, 428, 430, 431, 432, 433, 0x01A1, 0x01A8, 0x01AE}
    ko_state = (("ko" in move_preview) or ("k.o" in move_preview) or ("knock out" in move_preview) or ("dead" in move_preview) or ("death" in move_preview) or ("defeat" in move_preview) or ("slow motion" in move_preview and "ko" in move_preview) or (early_hp_cur <= 0))
    is_support = (("assist" in move_preview) or ("tag out" in move_preview) or ("tag in taunt" in move_preview) or ko_state or (early_move_id in assist_state_ids))
    is_active_panel = not is_support

    border_col = (84, 74, 74) if ko_state else (_brighten(accent, 22) if is_active_panel else (55, 63, 84))
    pygame.draw.rect(surf, border_col, rect, 1, border_radius=5)
    side_accent = (116, 92, 92) if ko_state else accent
    pygame.draw.rect(surf, side_accent, pygame.Rect(0, 0, 3, rect.height), border_radius=2)

    victory_pulse_live = bool(panel_fx.get("victory_pulse_live")) and not ko_state
    if victory_pulse_live:
        vp = 0.5 + 0.5 * math.sin((t_ms / 1000.0) * 5.2)
        halo = pygame.Surface((rect.width + 10, rect.height + 10), pygame.SRCALPHA)
        pygame.draw.rect(halo, (*_brighten(accent, 34), int(34 + 24 * vp)), pygame.Rect(5, 5, rect.width, rect.height), 2, border_radius=8)
        surf.blit(halo, (-5, -5))
        pulse_rail = pygame.Surface((7, rect.height), pygame.SRCALPHA)
        pygame.draw.rect(pulse_rail, (*_brighten(accent, 30), int(55 + 55 * vp)), pulse_rail.get_rect(), border_radius=3)
        surf.blit(pulse_rail, (0, 0))

    # Active point panels get a very faint moving scanline.
    if is_active_panel:
        pulse = 0.5 + 0.5 * math.sin((t_ms / 1000.0) * 2.2)
        glow = pygame.Surface((rect.width - 4, rect.height - 4), pygame.SRCALPHA)
        pygame.draw.rect(glow, (*accent, int(18 + 10 * pulse)), glow.get_rect(), 2, border_radius=6)
        surf.blit(glow, (2, 2))
        sweep_x = int((rect.width + 60) * (((t_ms / 1000.0) * 0.16) % 1.0)) - 30
        scanline = pygame.Surface((18, rect.height - 10), pygame.SRCALPHA)
        pygame.draw.rect(scanline, (*_brighten(accent, 24), 16), pygame.Rect(0, 0, 8, rect.height - 10), border_radius=3)
        pygame.draw.rect(scanline, (*_brighten(accent, 40), 8), pygame.Rect(8, 0, 10, rect.height - 10), border_radius=3)
        surf.blit(scanline, (sweep_x, 5))
    elif ko_state:
        glow = pygame.Surface((rect.width - 4, rect.height - 4), pygame.SRCALPHA)
        pygame.draw.rect(glow, (180, 90, 90, 22), glow.get_rect(), 1, border_radius=6)
        surf.blit(glow, (2, 2))

    pad = 8
    portrait_size = max(48, min(64, rect.height - 58))
    portrait_rect = pygame.Rect(pad, pad + 8, portrait_size, portrait_size)
    shadow = pygame.Surface((portrait_rect.width + 12, portrait_rect.height + 12), pygame.SRCALPHA)
    pygame.draw.rect(shadow, (0, 0, 0, 52), shadow.get_rect(), border_radius=8)
    surf.blit(shadow, (portrait_rect.x - 4, portrait_rect.y + 4))
    glow = pygame.Surface((portrait_rect.width + 8, portrait_rect.height + 8), pygame.SRCALPHA)
    glow_alpha = 18 if ko_state else (46 if is_active_panel else 22)
    pygame.draw.rect(glow, (*accent, glow_alpha), glow.get_rect(), 2, border_radius=7)
    surf.blit(glow, (portrait_rect.x - 4, portrait_rect.y - 4))
    frame_rect = portrait_rect.inflate(6, 6)
    pygame.draw.rect(surf, (11, 13, 19), frame_rect, border_radius=6)
    pygame.draw.rect(surf, _brighten(accent, 10) if is_active_panel else (62, 70, 94), frame_rect, 1, border_radius=6)
    if portrait is not None:
        try:
            p = pygame.transform.smoothscale(portrait, (portrait_rect.width, portrait_rect.height))
            if not is_active_panel:
                p = p.copy()
                veil = pygame.Surface(p.get_size(), pygame.SRCALPHA)
                veil.fill((18, 12, 12, 80) if ko_state else (0, 0, 0, 35))
                p.blit(veil, (0, 0))
            surf.blit(p, portrait_rect.topleft)
        except Exception:
            pygame.draw.rect(surf, (28, 31, 42), portrait_rect, border_radius=4)
    else:
        pygame.draw.rect(surf, (28, 31, 42), portrait_rect, border_radius=4)

    info_x = portrait_rect.right + 10
    info_right = rect.width - 10
    info_w = max(160, info_right - info_x)
    y = pad + 2
    char_name = str(snap.get("name") or "???")
    try:
        base_addr = int(snap.get("base") or 0)
        base_txt = f" @0x{base_addr:08X}" if base_addr else ""
    except Exception:
        base_txt = ""
    title_text = f"{header}  {char_name}{base_txt}"
    title_col = (220, 205, 205) if ko_state else (GUI_TEXT if is_active_panel else (210, 215, 225))
    title_s = _render_outlined_text(smallfont, title_text, title_col, (0, 0, 0), info_w, 1)
    surf.blit(title_s, (info_x, y))
    if ko_state:
        kf = _fx(panel_fx.get("ko_fade"))
        badge_alpha = int(255 * (kf if kf > 0 else 1.0))
        badge_w, badge_h = 46, 18
        badge = pygame.Surface((badge_w, badge_h), pygame.SRCALPHA)
        pygame.draw.rect(badge, (66, 32, 32, badge_alpha), badge.get_rect(), border_radius=5)
        pygame.draw.rect(badge, (176, 102, 102, badge_alpha), badge.get_rect(), 1, border_radius=5)
        badge_s = _render_outlined_text(font, "KO", (240, 228, 228), (0, 0, 0), badge_w - 6, 1)
        badge.blit(badge_s, ((badge_w - badge_s.get_width()) // 2, (badge_h - badge_s.get_height()) // 2 - 1))
        bx = rect.width - badge_w - 10
        by = 8
        surf.blit(badge, (bx, by))
    y += title_s.get_height() + 3

    hp_cur = snap.get("cur") or 0
    hp_max = snap.get("max") or 0
    hp_frac = _panel_bar_fraction(hp_cur, hp_max)
    hp_col = GUI_DANGER if hp_frac <= 0.30 else GUI_CONFIRM
    meter_frac, meter_txt = _meter_fraction_from_snap(snap)
    meter_val = snap.get("meter")
    try:
        raw_meter = int(float(meter_val if meter_val is not None else 0))
    except Exception:
        raw_meter = 0
    hp_text = f"HP {int(hp_cur or 0)}/{int(hp_max or 0)}"
    meter_text = f"Meter:{raw_meter}/Lvl {meter_txt.split('/')[0]}"
    hp_s = _render_outlined_text(smallfont, hp_text, hp_col, (0, 0, 0), max(90, info_w // 2 - 8), 1)
    meter_s = _render_outlined_text(smallfont, meter_text, _meter_value_text_color(raw_meter), (0, 0, 0), max(90, info_w // 2), 1)
    hp_x = info_x
    meter_x = info_x + max(170, info_w // 2)
    if meter_x + meter_s.get_width() > info_right:
        meter_x = info_x + min(190, max(150, info_w - meter_s.get_width()))
    surf.blit(hp_s, (hp_x, y))
    surf.blit(meter_s, (meter_x, y))

    bar_y = y + hp_s.get_height() + 2
    hp_bar_w = max(90, min(180, meter_x - hp_x - 16))
    meter_bar_w = max(90, min(180, info_right - meter_x))
    hp_bar = pygame.Rect(hp_x, bar_y, hp_bar_w, 3)
    meter_bar = pygame.Rect(meter_x, bar_y, meter_bar_w, 3)
    _draw_panel_stat_bar(surf, hp_bar, hp_frac, hp_col, empty_col=(18, 20, 28), border_col=(38, 44, 60))
    _draw_panel_stat_bar(surf, meter_bar, meter_frac, GUI_APP_ACCENT, empty_col=(18, 20, 28), border_col=(38, 44, 60))

    # HP trailing damage segment.
    hp_loss = panel_fx.get("hp_loss")
    hp_loss_t = _fx(hp_loss)
    if hp_loss_t > 0.0 and isinstance(hp_loss, dict):
        old_frac = float(hp_loss.get("from_frac", hp_frac) or hp_frac)
        cur_frac = float(hp_loss.get("to_frac", hp_frac) or hp_frac)
        if old_frac > cur_frac:
            x1 = hp_bar.x + 1 + int((hp_bar.width - 2) * cur_frac)
            x2 = hp_bar.x + 1 + int((hp_bar.width - 2) * old_frac)
            if x2 > x1:
                trail_rect = pygame.Rect(x1, hp_bar.y + 1, x2 - x1, max(1, hp_bar.height - 2))
                trail_alpha = int((1.0 - hp_loss_t) * 170)
                trail = pygame.Surface((trail_rect.width, trail_rect.height), pygame.SRCALPHA)
                _draw_vertical_gradient(trail, trail.get_rect(), (210, 72, 72), (140, 42, 42), trail_alpha)
                surf.blit(trail, trail_rect.topleft)

    # Meter gain flash + tiny floating gain indicator.
    meter_gain = panel_fx.get("meter_gain")
    meter_gain_t = _fx(meter_gain)
    if meter_gain_t > 0.0 and isinstance(meter_gain, dict):
        flash_alpha = int((1.0 - meter_gain_t) * 120)
        flash = pygame.Surface((meter_bar.width, max(4, meter_bar.height + 2)), pygame.SRCALPHA)
        pygame.draw.rect(flash, (*GUI_APP_ACCENT, flash_alpha), flash.get_rect(), border_radius=3)
        surf.blit(flash, (meter_bar.x, meter_bar.y - 1))
        delta = int(meter_gain.get("delta", 0) or 0)
        if delta > 0:
            plus = _render_outlined_text(smallfont, f"+{delta}", GUI_APP_ACCENT, (0, 0, 0), 60, 1)
            float_y = meter_bar.y - 12 - int(10 * meter_gain_t)
            plus.set_alpha(max(0, int(255 * (1.0 - meter_gain_t))))
            surf.blit(plus, (meter_bar.right - plus.get_width(), float_y))

    y = bar_y + 7
    try:
        pool_pct = float(snap.get("pool_pct") or 0.0)
    except Exception:
        pool_pct = 0.0
    try:
        raw_pool = int(snap.get("hp_pool_byte") or 0)
    except Exception:
        raw_pool = 0
    pool_text = f"POOL (02A): {pool_pct:5.1f}%   raw:{raw_pool}"
    pool_s = _render_outlined_text(smallfont, pool_text, GUI_TEXT, (0, 0, 0), info_w, 1)
    surf.blit(pool_s, (info_x, y))
    y += pool_s.get_height() + 2

    pct = float(snap.get("baroque_red_pct_max") or 0.0)
    ready = bool(snap.get("baroque_ready_local", False))
    ready_txt = "READY" if ready else "not ready"
    baroque_text = f"Baroque: {ready_txt}  red:{pct:.1f}%"
    if ready:
        bq_s = _render_rainbow_outlined_text(smallfont, baroque_text, info_w, t_ms, (0, 0, 0), 1)
    else:
        bq_s = _render_outlined_text(smallfont, baroque_text, GUI_TEXT_MUTED, (0, 0, 0), info_w, 1)
    surf.blit(bq_s, (info_x, y))
    ready_ping_t = _fx(panel_fx.get("baroque_ready"))
    if ready_ping_t > 0.0:
        sweep_x = info_x - 20 + int((bq_s.get_width() + 40) * ready_ping_t)
        sweep = pygame.Surface((18, bq_s.get_height() + 2), pygame.SRCALPHA)
        pygame.draw.rect(sweep, (255, 255, 255, int((1.0 - ready_ping_t) * 42)), pygame.Rect(0, 0, 8, bq_s.get_height() + 2), border_radius=3)
        surf.blit(sweep, (sweep_x, y - 1))
    y += bq_s.get_height() + 2

    move_id = snap.get("mv_id_display")
    mv_label = str(snap.get("mv_label") or "").strip()
    move_id_dec = None
    if move_id is not None:
        try:
            move_id_dec = int(move_id)
        except Exception:
            move_id_dec = None

    if not mv_label and move_id_dec is not None:
        mv_label = f"0x{move_id_dec:04X}"
    elif not mv_label and move_id is not None:
        mv_label = str(move_id)
    if not mv_label:
        mv_label = "--"

    if move_id_dec is not None:
        move_text = f"Move: {mv_label} ({move_id_dec})"
    else:
        move_text = f"Move: {mv_label}"

    move_pulse_t = _fx(panel_fx.get("move_pulse"))
    if move_pulse_t > 0.0:
        move_col = _brighten(GUI_TEXT if is_active_panel else GUI_TEXT_MUTED, int((1.0 - move_pulse_t) * 40))
    else:
        move_col = GUI_TEXT if is_active_panel else GUI_TEXT_MUTED
    move_s = _render_outlined_text(smallfont, move_text, move_col, (0, 0, 0), info_w, 1)
    surf.blit(move_s, (info_x, y))
    if move_pulse_t > 0.0:
        pulse_w = min(info_w, max(60, move_s.get_width() + 12))
        pulse_bg = pygame.Surface((pulse_w, move_s.get_height() + 4), pygame.SRCALPHA)
        pygame.draw.rect(pulse_bg, (*accent, int((1.0 - move_pulse_t) * 36)), pulse_bg.get_rect(), border_radius=4)
        surf.blit(pulse_bg, (info_x - 2, y - 2))
        surf.blit(move_s, (info_x, y))

    pulse = 0.5 + 0.5 * math.sin((t_ms / 1000.0) * 3.0)
    alpha = int((18 if is_active_panel else 8) + (14 if is_active_panel else 6) * pulse)
    glow_line = pygame.Surface((min(info_w, 220), 1), pygame.SRCALPHA)
    glow_line.fill((*accent, alpha))
    surf.blit(glow_line, (info_x, max(4, y + move_s.get_height() + 2)))

__all__ = [
    "TOP_UI_RESERVED",
    "GUI_BG_DARK",
    "GUI_PANEL",
    "GUI_PANEL_2",
    "GUI_PANEL_3",
    "GUI_BORDER",
    "GUI_BORDER_HOT",
    "GUI_TEXT",
    "GUI_TEXT_MUTED",
    "GUI_TEXT_DIM",
    "GUI_APP_ACCENT",
    "GUI_CONFIRM",
    "GUI_WARNING",
    "GUI_DANGER",
    "GUI_ACCENT_BLUE",
    "GUI_ACCENT_PURPLE",
    "GUI_ACCENT_GOLD",
    "GUI_ACCENT_GREEN",
    "GUI_ACCENT_RED",
    "GUI_P1",
    "GUI_P2",
    "GUI_P3",
    "GUI_P4",
    "GUI_SLOT_MUTED",
    "_clamp_u8",
    "_brighten",
    "_darken",
    "_mix_col",
    "_slot_accent_for_label",
    "_draw_vertical_gradient",
    "_fit_text",
    "_render_outlined_text",
    "_render_rainbow_outlined_text",
    "draw_glass_button",
    "draw_slot_chip",
    "draw_top_command_dock",
    "draw_status_rail",
    "draw_bottom_workspace_tabs",
    "_normal_button_accent",
    "_normal_id_to_label",
    "_normal_move_label",
    "_normal_canon_label",
    "_normal_int",
    "_NORMAL_PREVIEW_ORDER",
    "_NORMAL_PREVIEW_RANK",
    "_OPTIONAL_PREVIEW_NORMALS",
    "_HIDDEN_PREVIEW_NORMALS",
    "_normal_canonical_label",
    "_normal_preview_label_allowed",
    "_normal_row_quality",
    "_normal_visible_moves",
    "_draw_scan_metric_chip",
    "draw_scan_normals_polished",
    "_quick_assist_accent_for_label",
    "_ease_out_cubic",
    "_ease_in_out_smootherstep",
    "draw_quick_assist_footer",
    "_panel_bar_fraction",
    "_draw_panel_stat_bar",
    "_meter_fraction_from_snap",
    "_meter_value_text_color",
    "draw_panel_polished_stats",
]
