"""Extracted runtime module from :mod:`main`.

This module deliberately preserves the original function names and behavior so
`main.py` can remain a compatibility-oriented entry point while the subsystem
has a focused home.
"""
from __future__ import annotations

import math

import pygame

from tvcgui.runtime.megacrash import (
    MEGACRASH_TRAINER_DEFAULT_CHANCE,
    MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC,
    MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES,
    MEGACRASH_TRAINER_DEFAULT_MODE,
)

TOP_UI_RESERVED = 66

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

    grad = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)

    for y in range(rect.height):
        t = y / max(1, rect.height - 1)
        r = int(top_col[0] * (1.0 - t) + bot_col[0] * t)
        g = int(top_col[1] * (1.0 - t) + bot_col[1] * t)
        b = int(top_col[2] * (1.0 - t) + bot_col[2] * t)
        pygame.draw.line(grad, (r, g, b, alpha), (0, y), (rect.width, y))

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

    for ox, oy in (
        (-pad, -pad), (0, -pad), (pad, -pad),
        (-pad, 0),                (pad, 0),
        (-pad, pad),  (0, pad),   (pad, pad),
    ):
        outline = _fit_text(font, text, outline_color, max_width)
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
    compact: bool = False,
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
    text = str(label) if compact else f"{label} {state}"

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
    hurtbox_slots: dict,
    ruler_slots: dict,
    ruler_enabled: bool,
    ruler_axes: dict,
    overlay_enabled: bool,
    show_interaction_card: bool,
    show_combo_card: bool,
    show_tag_card: bool,
    megacrash_trainer_enabled: bool = False,
    megacrash_trainer_chance: int = MEGACRASH_TRAINER_DEFAULT_CHANCE,
    megacrash_trainer_mode: str = MEGACRASH_TRAINER_DEFAULT_MODE,
    megacrash_trainer_delay_frames: int = MEGACRASH_TRAINER_DEFAULT_DELAY_FRAMES,
    megacrash_trainer_cooldown_sec: float = MEGACRASH_TRAINER_DEFAULT_COOLDOWN_SEC,
    megacrash_trainer_cooldown_remaining: float = 0.0,
    mem_dump_active: bool = False,
    mem_dump_label: str = "",
    win_score_enabled: bool = False,
    char_test_active: bool = False,
    ko_control_enabled: bool = False,
    ko_control_live_active: bool = False,
    solo_team_active: bool = False,
    tools_open: bool = False,
    mouse_pos: tuple[int, int],
    t_ms: int = 0,
) -> tuple:
    """Draw the compact command dock with routine visuals up front and lab tools in a drawer.

    The dock is intentionally split into two layers:
      * always-visible: overlay/cards and collision visualization controls;
      * optional drawer: tooling that is used occasionally rather than every match.

    This keeps the in-match strip readable without removing an existing tool.
    """
    mx, my = mouse_pos
    w, _h = screen.get_size()

    dock_rect = pygame.Rect(0, 0, w, TOP_UI_RESERVED - 4)
    _draw_vertical_gradient(screen, dock_rect, (12, 13, 19), (8, 9, 13), 255)
    pygame.draw.line(screen, (58, 64, 82), (0, dock_rect.bottom - 1), (w, dock_rect.bottom - 1))

    gap = 8
    y_top = 7
    y_tools = 35
    btn_h = 22

    # The main dock favors complete labels over abbreviations.  A compact
    # dedicated font lets "Horizontal", "Vertical", and "Hit and Block"
    # remain readable without crowding the live match controls.
    try:
        dockfont = pygame.font.SysFont("consolas", 11)
    except Exception:
        dockfont = smallfont

    # Dummy rect kept for the older projectile-scanner click path.
    ps_btn_rect = pygame.Rect(-9999, -9999, 0, 0)
    solo_team_btn_rect = pygame.Rect(-10000, -10000, 0, 0)

    def draw_group(rect: pygame.Rect) -> None:
        _draw_vertical_gradient(screen, rect, (22, 26, 37), (15, 18, 27), 235)
        pygame.draw.rect(screen, (55, 66, 88), rect, 1, border_radius=5)

    # ------------------------------------------------------------------
    # Row 1: things worth seeing/changing in the middle of a match.
    # ------------------------------------------------------------------
    x = 8

    # Overlay/card cluster.  It is outlined as one block so card switches no
    # longer look like unrelated free-floating buttons.
    hud_group_rect = pygame.Rect(x, y_top - 2, 356, btn_h + 4)
    draw_group(hud_group_rect)

    hud_btn_rect = pygame.Rect(hud_group_rect.x + 4, y_top, 100, btn_h)
    draw_glass_button(
        screen, hud_btn_rect,
        "Overlay: ON" if overlay_enabled else "Overlay: OFF",
        dockfont, active=overlay_enabled,
        hover=hud_btn_rect.collidepoint(mx, my), accent=GUI_APP_ACCENT,
        fill=(44, 56, 82) if overlay_enabled else (31, 33, 42), align="center",
    )

    interaction_card_btn_rect = pygame.Rect(hud_btn_rect.right + 4, y_top, 82, btn_h)
    draw_glass_button(
        screen, interaction_card_btn_rect, "Hit / Block", dockfont,
        active=bool(show_interaction_card),
        hover=interaction_card_btn_rect.collidepoint(mx, my), accent=GUI_ACCENT_BLUE,
        fill=(44, 56, 82) if show_interaction_card else (31, 33, 42), align="center",
    )

    combo_card_btn_rect = pygame.Rect(interaction_card_btn_rect.right + 4, y_top, 55, btn_h)
    draw_glass_button(
        screen, combo_card_btn_rect, "Combo", dockfont,
        active=bool(show_combo_card),
        hover=combo_card_btn_rect.collidepoint(mx, my), accent=GUI_ACCENT_BLUE,
        fill=(44, 56, 82) if show_combo_card else (31, 33, 42), align="center",
    )

    tag_card_btn_rect = pygame.Rect(combo_card_btn_rect.right + 4, y_top, 43, btn_h)
    draw_glass_button(
        screen, tag_card_btn_rect, "Tag", dockfont,
        active=bool(show_tag_card),
        hover=tag_card_btn_rect.collidepoint(mx, my), accent=GUI_ACCENT_BLUE,
        fill=(44, 56, 82) if show_tag_card else (31, 33, 42), align="center",
    )

    # This clears the three optional live cards without shutting down the
    # master HUD itself.  That lets the player declutter instantly while
    # keeping name/health/meter information visible.
    cards_active = bool(show_interaction_card or show_combo_card or show_tag_card)
    clear_card_btn_rect = pygame.Rect(tag_card_btn_rect.right + 4, y_top, 52, btn_h)
    draw_glass_button(
        screen, clear_card_btn_rect, "Clear", dockfont,
        active=cards_active,
        hover=clear_card_btn_rect.collidepoint(mx, my), accent=GUI_ACCENT_RED,
        fill=(59, 38, 43) if cards_active else (31, 33, 42), align="center",
    )

    # Collision cluster.  Master buttons say what each row controls; the slot
    # chips are compact raw-slot numbers because the parent label supplies the
    # context.  A small divider preserves the P1/P2 vs P3/P4 pairing at a
    # glance without treating P1/P3 as a team.
    x = hud_group_rect.right + gap
    visual_group_rect = pygame.Rect(x, y_top - 2, 450, btn_h + 4)
    draw_group(visual_group_rect)

    hb_on = any(hitbox_slots.values())
    hb_btn_rect = pygame.Rect(visual_group_rect.x + 4, y_top, 100, btn_h)
    draw_glass_button(
        screen, hb_btn_rect, "Hitboxes: ON" if hb_on else "Hitboxes: OFF", dockfont,
        active=hb_on, hover=hb_btn_rect.collidepoint(mx, my), accent=GUI_APP_ACCENT,
        align="center",
    )

    chip_y = y_top + 2
    chip_w = 22
    chip_h = 18
    chip_gap = 2
    pair_gap = 6
    chip_x = hb_btn_rect.right + 5
    slot_colors = dict(GUI_SLOT_MUTED)
    hb_filter_rects = {}
    for index, slot_name in enumerate(("P1", "P2", "P3", "P4")):
        if index == 2:
            chip_x += pair_gap
        chip_rect = pygame.Rect(chip_x, chip_y, chip_w, chip_h)
        draw_slot_chip(
            screen, chip_rect, slot_name[-1], smallfont,
            enabled=bool(hitbox_slots.get(slot_name, False)),
            accent=slot_colors.get(slot_name, GUI_ACCENT_BLUE),
            hover=chip_rect.collidepoint(mx, my), compact=True,
        )
        hb_filter_rects[slot_name] = chip_rect.inflate(4, 4)
        chip_x += chip_w + chip_gap

    hurt_on = any(hurtbox_slots.values())
    hurt_btn_rect = pygame.Rect(chip_x + 8, y_top, 110, btn_h)
    draw_glass_button(
        screen, hurt_btn_rect, "Hurtboxes: ON" if hurt_on else "Hurtboxes: OFF", dockfont,
        active=hurt_on, hover=hurt_btn_rect.collidepoint(mx, my), accent=GUI_ACCENT_GREEN,
        align="center",
    )

    hurt_chip_x = hurt_btn_rect.right + 5
    hurt_filter_rects = {}
    for index, slot_name in enumerate(("P1", "P2", "P3", "P4")):
        if index == 2:
            hurt_chip_x += pair_gap
        chip_rect = pygame.Rect(hurt_chip_x, chip_y, chip_w, chip_h)
        draw_slot_chip(
            screen, chip_rect, slot_name[-1], smallfont,
            enabled=bool(hurtbox_slots.get(slot_name, False)),
            accent=slot_colors.get(slot_name, GUI_ACCENT_GREEN),
            hover=chip_rect.collidepoint(mx, my), compact=True,
        )
        hurt_filter_rects[slot_name] = chip_rect.inflate(4, 4)
        hurt_chip_x += chip_w + chip_gap

    # Ruler sources deliberately live in their own cluster.  Hitbox chips
    # filter only live attack-box drawings; they no longer decide which of the
    # four saved-profile rulers can appear.
    x = visual_group_rect.right + gap
    ruler_group_rect = pygame.Rect(x, y_top - 2, 358, btn_h + 4)
    draw_group(ruler_group_rect)

    ruler_btn_rect = pygame.Rect(ruler_group_rect.x + 4, y_top, 82, btn_h)
    draw_glass_button(
        screen, ruler_btn_rect, "Ruler: ON" if ruler_enabled else "Ruler: OFF", dockfont,
        active=bool(ruler_enabled), hover=ruler_btn_rect.collidepoint(mx, my), accent=GUI_ACCENT_PURPLE,
        fill=(44, 48, 76) if ruler_enabled else (31, 33, 42), align="center",
    )

    # Horizontal and Vertical are independent layers. The old Dynamic ghost
    # display is retired: active-frame samples build the two compact rulers.
    # Keeping both on is intentional: Horizontal answers reach, Vertical
    # answers height.
    ruler_axes = ruler_axes if isinstance(ruler_axes, dict) else {}
    ruler_horz_on = bool(ruler_axes.get("horizontal", True))
    ruler_vert_on = bool(ruler_axes.get("vertical", False))
    ruler_axis_h_rect = pygame.Rect(ruler_btn_rect.right + 5, y_top, 80, btn_h)
    draw_glass_button(
        screen, ruler_axis_h_rect, "Horizontal", dockfont,
        active=ruler_horz_on, hover=ruler_axis_h_rect.collidepoint(mx, my), accent=GUI_ACCENT_PURPLE,
        fill=(52, 42, 76) if ruler_horz_on else (31, 33, 42), align="center",
    )
    ruler_axis_v_rect = pygame.Rect(ruler_axis_h_rect.right + 2, y_top, 66, btn_h)
    draw_glass_button(
        screen, ruler_axis_v_rect, "Vertical", dockfont,
        active=ruler_vert_on, hover=ruler_axis_v_rect.collidepoint(mx, my), accent=GUI_ACCENT_PURPLE,
        fill=(52, 42, 76) if ruler_vert_on else (31, 33, 42), align="center",
    )

    ruler_chip_x = ruler_axis_v_rect.right + 5
    ruler_filter_rects = {}
    for index, slot_name in enumerate(("P1", "P2", "P3", "P4")):
        if index == 2:
            ruler_chip_x += pair_gap
        chip_rect = pygame.Rect(ruler_chip_x, chip_y, chip_w, chip_h)
        draw_slot_chip(
            screen, chip_rect, slot_name[-1], smallfont,
            enabled=bool(ruler_slots.get(slot_name, True)),
            accent=slot_colors.get(slot_name, GUI_ACCENT_PURPLE),
            hover=chip_rect.collidepoint(mx, my), compact=True,
        )
        ruler_filter_rects[slot_name] = chip_rect.inflate(4, 4)
        ruler_chip_x += chip_w + chip_gap

    x = ruler_group_rect.right + gap
    tools_btn_rect = pygame.Rect(x, y_top, 70, btn_h)
    draw_glass_button(
        screen, tools_btn_rect, "Tools  ▾" if not tools_open else "Tools  ▴", dockfont,
        active=bool(tools_open), hover=tools_btn_rect.collidepoint(mx, my),
        accent=GUI_APP_ACCENT, fill=(38, 49, 70) if tools_open else (31, 33, 42), align="center",
    )

    # ------------------------------------------------------------------
    # Row 2: explicit lab drawer or a quiet single-line contextual hint.
    # ------------------------------------------------------------------
    as_btn_rect = pygame.Rect(-9999, -9999, 0, 0)
    memdump_btn_rect = pygame.Rect(-9999, -9999, 0, 0)
    win_counter_btn_rect = pygame.Rect(-9999, -9999, 0, 0)
    select_probe_btn_rect = pygame.Rect(-9999, -9999, 0, 0)
    yami_stage_btn_rect = pygame.Rect(-9999, -9999, 0, 0)
    ko_control_btn_rect = pygame.Rect(-9999, -9999, 0, 0)
    megacrash_btn_rect = pygame.Rect(-9999, -9999, 0, 0)
    overseer_btn_rect = pygame.Rect(-9999, -9999, 0, 0)

    if tools_open:
        drawer_rect = pygame.Rect(8, y_tools - 2, min(w - 16, 1160), btn_h + 4)
        draw_group(drawer_rect)
        x = drawer_rect.x + 4

        win_counter_btn_rect = pygame.Rect(x, y_tools, 122, btn_h)
        draw_glass_button(
            screen, win_counter_btn_rect,
            "Win Score: ON" if win_score_enabled else "Win Score: OFF",
            dockfont, active=bool(win_score_enabled), hover=win_counter_btn_rect.collidepoint(mx, my),
            accent=GUI_APP_ACCENT, fill=(44, 56, 82) if win_score_enabled else (31, 33, 42), align="center",
        )

        x = win_counter_btn_rect.right + 4
        as_btn_rect = pygame.Rect(x, y_tools, 106, btn_h)
        draw_glass_button(screen, as_btn_rect, "Assist Setup", dockfont, active=False,
                          hover=as_btn_rect.collidepoint(mx, my), accent=GUI_APP_ACCENT, align="center")

        x = as_btn_rect.right + 4
        memdump_btn_rect = pygame.Rect(x, y_tools, 118, btn_h)
        dump_label = mem_dump_label if mem_dump_active and mem_dump_label else "Dump Memory"
        draw_glass_button(
            screen, memdump_btn_rect, dump_label, dockfont, active=bool(mem_dump_active),
            hover=memdump_btn_rect.collidepoint(mx, my), accent=GUI_APP_ACCENT,
            fill=(44, 56, 82) if mem_dump_active else (31, 33, 42), align="center",
        )

        x = memdump_btn_rect.right + 4
        select_probe_btn_rect = pygame.Rect(x, y_tools, 174, btn_h)
        draw_glass_button(
            screen, select_probe_btn_rect,
            "Extra Characters: ON" if char_test_active else "Extra Characters: OFF", dockfont,
            active=bool(char_test_active), hover=select_probe_btn_rect.collidepoint(mx, my),
            accent=GUI_APP_ACCENT, fill=(44, 56, 82) if char_test_active else (31, 33, 42), align="center",
        )

        x = select_probe_btn_rect.right + 4
        yami_stage_btn_rect = pygame.Rect(x, y_tools, 112, btn_h)
        draw_glass_button(
            screen, yami_stage_btn_rect,
            "Stage Control", dockfont, active=False,
            hover=yami_stage_btn_rect.collidepoint(mx, my), accent=GUI_APP_ACCENT, align="center",
        )

        x = yami_stage_btn_rect.right + 4
        ko_control_btn_rect = pygame.Rect(x, y_tools, 160, btn_h)
        ko_label = "KO Control: ACTIVE" if ko_control_live_active else ("KO Control: ARMED" if ko_control_enabled else "KO Control: OFF")
        draw_glass_button(
            screen, ko_control_btn_rect, ko_label, dockfont, active=bool(ko_control_enabled),
            hover=ko_control_btn_rect.collidepoint(mx, my), accent=GUI_ACCENT_GREEN,
            fill=((37, 64, 42) if ko_control_live_active else ((31, 42, 36) if ko_control_enabled else (31, 33, 42))),
            align="center",
        )

        x = ko_control_btn_rect.right + 4
        megacrash_btn_rect = pygame.Rect(x, y_tools, 190, btn_h)
        draw_glass_button(
            screen, megacrash_btn_rect,
            "Megacrash Trainer: ON" if megacrash_trainer_enabled else "Megacrash Trainer",
            dockfont, active=bool(megacrash_trainer_enabled), hover=megacrash_btn_rect.collidepoint(mx, my),
            accent=GUI_APP_ACCENT, fill=(44, 56, 82) if megacrash_trainer_enabled else (31, 33, 42), align="center",
        )

        x = megacrash_btn_rect.right + 4
        overseer_btn_rect = pygame.Rect(x, y_tools, 110, btn_h)
        draw_glass_button(screen, overseer_btn_rect, "Tool Status", dockfont, active=False,
                          hover=overseer_btn_rect.collidepoint(mx, my), accent=GUI_APP_ACCENT, align="center")
    else:
        help_tip = "Visual controls stay here. Tools holds memory dumps, assists, extra characters, KO control, Megacrash training, and tool status."
        help_accent = GUI_APP_ACCENT
        if hb_btn_rect.collidepoint(mx, my):
            help_tip = "Hitboxes: master attack/projectile boxes. Turning them on also turns on the ground-normal range ruler."
        elif any(rect.collidepoint(mx, my) for rect in hb_filter_rects.values()):
            help_tip = "Hit slots: raw P1 / P2 / P3 / P4 visibility. The gap separates Team 1 (1/2) from Team 2 (3/4)."
        elif hurt_btn_rect.collidepoint(mx, my):
            help_tip = "Hurtboxes: master defender/body bubbles. The profile ruler uses these to determine whether its saved reach touches."
            help_accent = GUI_ACCENT_GREEN
        elif any(rect.collidepoint(mx, my) for rect in hurt_filter_rects.values()):
            help_tip = "Hurt slots: raw P1 / P2 / P3 / P4 visibility. The gap separates Team 1 (1/2) from Team 2 (3/4)."
            help_accent = GUI_ACCENT_GREEN
        elif ruler_btn_rect.collidepoint(mx, my):
            help_tip = "Ruler: saved active-frame reach guides. Horizontal and Vertical are independent; turn on either or both. Each missing envelope is sampled once and saved. Chips only filter eligible sources. Hurtboxes are required for Horizontal touch checks."
            help_accent = GUI_ACCENT_PURPLE
        elif ruler_axis_h_rect.collidepoint(mx, my):
            help_tip = "Horizontal: toggle the furthest forward active hitbox edge across the whole move. Can be on with Vertical."
            help_accent = GUI_ACCENT_PURPLE
        elif ruler_axis_v_rect.collidepoint(mx, my):
            help_tip = "Vertical: toggle the full highest-to-lowest active hitbox envelope across the whole move. Can be on with Horizontal."
            help_accent = GUI_ACCENT_PURPLE
        elif any(rect.collidepoint(mx, my) for rect in ruler_filter_rects.values()):
            help_tip = "Ruler slots: choose which raw fighters can show a saved-profile ruler. Independent from Hitboxes. Off-stage partners are naturally hidden."
            help_accent = GUI_ACCENT_PURPLE
        elif hud_btn_rect.collidepoint(mx, my):
            help_tip = "Overlay: starts or stops the master HUD process."
        elif clear_card_btn_rect.collidepoint(mx, my):
            help_tip = "Clear: turn off Hit and Block, Combo, and Tag together while leaving the main HUD on."
            help_accent = GUI_ACCENT_RED
        elif interaction_card_btn_rect.collidepoint(mx, my):
            help_tip = "Hit and Block: show or hide the live hit/block interaction ribbon."
        elif combo_card_btn_rect.collidepoint(mx, my):
            help_tip = "Combo: show or hide the live combo ledger."
        elif tag_card_btn_rect.collidepoint(mx, my):
            help_tip = "Tag: show or hide the incoming tag resource card."
        elif tools_btn_rect.collidepoint(mx, my):
            help_tip = "Tools: open occasional tools without keeping them in the match-time strip."

        help_rect = pygame.Rect(8, y_tools, max(120, w - 16), btn_h)
        _draw_vertical_gradient(screen, help_rect, (21, 24, 34), (15, 17, 25), 230)
        pygame.draw.rect(screen, (49, 57, 75), help_rect, 1, border_radius=4)
        pulse = 0.5 + 0.5 * math.sin((t_ms / 1000.0) * 4.0)
        dot_col = _brighten(help_accent, int(35 * pulse))
        dot = pygame.Surface((8, 8), pygame.SRCALPHA)
        pygame.draw.circle(dot, (*dot_col, int(115 + 75 * pulse)), (4, 4), 3)
        screen.blit(dot, (help_rect.x + 8, help_rect.y + 7))
        help_surf = _fit_text(smallfont, help_tip, GUI_TEXT_DIM, help_rect.width - 28)
        screen.blit(help_surf, (help_rect.x + 22, help_rect.y + (help_rect.height - help_surf.get_height()) // 2))

    return (
        hb_btn_rect, hurt_btn_rect, ps_btn_rect, as_btn_rect, hud_btn_rect,
        megacrash_btn_rect, memdump_btn_rect, win_counter_btn_rect,
        overseer_btn_rect, select_probe_btn_rect, yami_stage_btn_rect, ko_control_btn_rect,
        solo_team_btn_rect, interaction_card_btn_rect, combo_card_btn_rect,
        tag_card_btn_rect, clear_card_btn_rect, tools_btn_rect, hb_filter_rects, hurt_filter_rects,
        ruler_btn_rect, ruler_axis_h_rect, ruler_axis_v_rect, ruler_filter_rects,
    )


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

__all__ = [
    'TOP_UI_RESERVED',
    'GUI_BG_DARK',
    'GUI_PANEL',
    'GUI_PANEL_2',
    'GUI_PANEL_3',
    'GUI_BORDER',
    'GUI_BORDER_HOT',
    'GUI_TEXT',
    'GUI_TEXT_MUTED',
    'GUI_TEXT_DIM',
    'GUI_APP_ACCENT',
    'GUI_CONFIRM',
    'GUI_WARNING',
    'GUI_DANGER',
    'GUI_ACCENT_BLUE',
    'GUI_ACCENT_PURPLE',
    'GUI_ACCENT_GOLD',
    'GUI_ACCENT_GREEN',
    'GUI_ACCENT_RED',
    'GUI_P1',
    'GUI_P2',
    'GUI_P3',
    'GUI_P4',
    'GUI_SLOT_MUTED',
    '_clamp_u8',
    '_brighten',
    '_darken',
    '_mix_col',
    '_slot_accent_for_label',
    '_draw_vertical_gradient',
    '_fit_text',
    '_render_outlined_text',
    '_render_rainbow_outlined_text',
    'draw_glass_button',
    'draw_slot_chip',
    'draw_top_command_dock',
    'draw_status_rail',
    'draw_bottom_workspace_tabs'
]
