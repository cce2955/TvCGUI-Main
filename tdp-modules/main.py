import os
import csv
import time
import json
import subprocess
import sys
import pygame
from subprocess_compat import frozen_exe


def resource_path(*parts):
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, *parts)

from constants import (
    SLOTS,
    CHAR_NAMES,
    OFF_CHAR_ID,
)

import pygame

try:
    import pyperclip
except ImportError:
    pyperclip = None

from layout import compute_layout, reassign_slots_for_giants
from scan_worker import ScanNormalsWorker
from training_flags import read_training_flags
from debug_panel import read_debug_flags, draw_debug_overlay

from dolphin_io import hook, rd8, rd32, wd8, addr_in_ram, rbytes

from config import (
    MIN_HIT_DAMAGE,
    SCREEN_W, SCREEN_H,
    FONT_MAIN_SIZE, FONT_SMALL_SIZE,
    HIT_CSV,
    GENERIC_MAPPING_CSV,
    PAIR_MAPPING_CSV,
    COL_BG,
    INPUT_MONITOR_ADDRS,
    DEBUG_FLAG_ADDRS,
)

from portraits import (
    load_portrait_placeholder,
    load_portraits_from_dir,
    get_portrait_for_snap,
)

from resolver import RESOLVER, pick_posy_off_no_jump
from meter import read_meter, METER_CACHE
from fighter import read_fighter, dist2
from advantage import ADV_TRACK
from moves import (
    load_move_map,
    move_label_for,
    CHAR_ID_CORRECTION,
)
from move_id_map import lookup_move_name
from hud_draw import (
    draw_panel_classic,
    draw_activity,
    draw_event_log,
    draw_scan_normals,
)

from redscan import RedHealthScanner
from global_redscan import GlobalRedScanner
from events import log_engaged, log_hit, log_frame_advantage

try:
    import scan_normals_all
    HAVE_SCAN_NORMALS = True
    from scan_normals_all import ANIM_MAP as SCAN_ANIM_MAP
except Exception:
    scan_normals_all = None
    HAVE_SCAN_NORMALS = False
    SCAN_ANIM_MAP = {}

from frame_data_window import open_frame_data_window
from proj_scanner_window import open_proj_scanner_window
try:
    from assist_scanner_window import (
        open_assist_scanner_window,
        tick_assist_profiles_from_main,
        get_quick_assists_for_slot,
        apply_quick_assist_from_main,
    )
except Exception:
    from assist_scanner_window import open_assist_scanner_window
    def tick_assist_profiles_from_main(_snaps):
        return None
    def get_quick_assists_for_slot(_slot_label, _snap=None):
        return [
            {"label": "304", "table": 304},
            {"label": "305", "table": 305},
            {"label": "306", "table": 306},
            {"label": "Default", "default": True},
        ]
    def apply_quick_assist_from_main(_slot_label, _quick_index, _snap=None):
        return False

from mission_manager import MissionManager
from hud_overlay_manager import HudOverlayManager

MASTER_CONTROL_FILE = "master_overlay_control.json"

TARGET_FPS          = 60
DAMAGE_EVERY_FRAMES = 3
ADV_EVERY_FRAMES    = 2

PANEL_SLIDE_DURATION = 2.0
PANEL_FLASH_FRAMES   = 12
SCAN_SLIDE_DURATION  = 0.7

HP32_OFF   = 0x28
POOL32_OFF = 0x2C

FIGHTER_BLOCK_SIZE = 0x120

REACTION_STATES = {48, 64, 65, 66, 73, 79, 80, 81, 82, 90, 92, 95, 96, 97}

GIANT_IDS = {11, 22}

HB_BTN_X, HB_BTN_Y = 8, 8
HB_BTN_W, HB_BTN_H = 130, 22
TOP_UI_RESERVED = 60


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def u32be_from_block(block: bytes, off: int) -> int | None:
    if not block or off + 4 > len(block):
        return None
    return (
        (block[off] << 24)
        | (block[off + 1] << 16)
        | (block[off + 2] << 8)
        | block[off + 3]
    )


def _copy_to_clipboard(text: str) -> None:
    if not text:
        return
    if pyperclip is not None:
        try:
            pyperclip.copy(text)
            print(f"[copy] {text}")
            return
        except Exception as e:
            print(f"[copy] failed ({e!r}) -> {text}")
    print(f"[copy] (no pyperclip) -> {text}")


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

GUI_ACCENT_BLUE = (95, 135, 215)
GUI_ACCENT_PURPLE = (145, 105, 210)
GUI_ACCENT_GOLD = (190, 145, 60)
GUI_ACCENT_GREEN = (80, 185, 115)
GUI_ACCENT_RED = (210, 80, 90)

GUI_P1 = (235, 90, 90)
GUI_P2 = (95, 150, 240)
GUI_P3 = (230, 90, 170)
GUI_P4 = (90, 220, 140)


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
    base = fill if fill is not None else (GUI_PANEL_3 if active else GUI_PANEL_2)
    if hover:
        base = _brighten(base, 18)

    border = GUI_BORDER_HOT if hover else (accent if active else GUI_BORDER)
    text_col = GUI_TEXT if active or hover else GUI_TEXT_MUTED

    _draw_vertical_gradient(
        surf,
        rect,
        _brighten(base, 12),
        _darken(base, 6),
        235,
    )

    pygame.draw.rect(surf, border, rect, 1, border_radius=4)

    # Soft graphite-blue topper instead of white gloss. The white strip looked
    # too harsh on short buttons and fought the text outline. This keeps a
    # glass/metal highlight without washing out the label.
    shine = pygame.Rect(rect.x + 2, rect.y + 2, rect.width - 4, max(2, rect.height // 6))
    shine_col = (150, 165, 190, 16) if active or hover else (118, 128, 150, 11)
    pygame.draw.rect(surf, shine_col, shine, border_radius=3)

    if active:
        accent_rect = pygame.Rect(rect.x + 4, rect.bottom - 3, rect.width - 8, 2)
        pygame.draw.rect(surf, accent, accent_rect, border_radius=1)

    label_surf = _render_outlined_text(
        font,
        label,
        text_col,
        (0, 0, 0),
        rect.width - 12,
        outline_px=1,
    )

    if align == "left":
        tx = rect.x + 7
    elif align == "right":
        tx = rect.right - label_surf.get_width() - 7
    else:
        tx = rect.x + (rect.width - label_surf.get_width()) // 2

    ty = rect.y + (rect.height - label_surf.get_height()) // 2
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
    fill = GUI_PANEL_3 if enabled else (24, 25, 32)
    border = accent if enabled else (70, 74, 88)
    text_col = GUI_TEXT if enabled else GUI_TEXT_DIM

    if hover:
        fill = _brighten(fill, 16)
        border = _brighten(border, 28)

    _draw_vertical_gradient(
        surf,
        rect,
        _brighten(fill, 10),
        _darken(fill, 8),
        235,
    )

    pygame.draw.rect(surf, border, rect, 1, border_radius=4)

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
    mouse_pos: tuple[int, int],
) -> tuple[pygame.Rect, pygame.Rect, pygame.Rect, pygame.Rect, dict]:
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

    # Intentionally no title/status text here; keep the command dock compact.
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
        accent=GUI_ACCENT_GREEN,
        align="center",
    )

    x = hb_btn_rect.right + gap
    ps_btn_rect = pygame.Rect(x, y, 150, btn_h)
    draw_glass_button(
        screen,
        ps_btn_rect,
        "Proj Scanner",
        smallfont,
        active=False,
        hover=ps_btn_rect.collidepoint(mx, my),
        accent=GUI_ACCENT_BLUE,
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
        accent=GUI_ACCENT_PURPLE,
        align="center",
    )

    x = as_btn_rect.right + gap
    hud_btn_rect = pygame.Rect(x, y, 142, btn_h)
    draw_glass_button(
        screen,
        hud_btn_rect,
        "Overlay: ON" if overlay_enabled else "Overlay: OFF",
        smallfont,
        active=overlay_enabled,
        hover=hud_btn_rect.collidepoint(mx, my),
        accent=GUI_ACCENT_GOLD,
        align="center",
    )

    chip_y = y + btn_h + 6
    label_surf = smallfont.render("Hitbox Slots:", True, GUI_TEXT_MUTED)
    screen.blit(label_surf, (8, chip_y + 3))

    chip_x = 8 + label_surf.get_width() + 10
    chip_w = 60
    chip_h = 18
    chip_gap = 7

    slot_colors = {
        "P1": GUI_P1,
        "P2": GUI_P2,
        "P3": GUI_P3,
        "P4": GUI_P4,
    }

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

    return hb_btn_rect, ps_btn_rect, as_btn_rect, hud_btn_rect, hb_filter_rects


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

    This replaces the old always-visible Events + Debug + Scan stack. The
    default tab is Normals Preview, which gets the full lower workspace height
    instead of being clipped at the bottom of the window.
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
        ("scan", "Normals Preview", GUI_ACCENT_BLUE),
        ("events", "Events", GUI_ACCENT_PURPLE),
        ("debug", "Debug Flags", GUI_ACCENT_GOLD),
        ("activity", "Activity", GUI_ACCENT_GREEN),
    ]

    tab_rects: dict[str, pygame.Rect] = {}
    x = rect.x + pad
    y = rect.y + pad
    gap = 6

    for key, label, accent in tabs:
        width = max(96, min(160, smallfont.size(label)[0] + 26))
        tr = pygame.Rect(x, y, width, tab_h)
        tab_rects[key] = tr
        draw_glass_button(
            screen,
            tr,
            label,
            smallfont,
            active=(key == active_tab),
            hover=tr.collidepoint(mx, my),
            accent=accent,
            fill=(30, 36, 52) if key == active_tab else (21, 24, 34),
            align="center",
        )
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


def _normal_move_label(mv: dict) -> str:
    forced = mv.get("_normal_display_label") if isinstance(mv, dict) else None
    if forced:
        return str(forced)

    for key in ("label", "name", "move", "pretty_name"):
        value = mv.get(key)
        if value:
            return str(value)

    aid = mv.get("id")
    normal_names = {
        0x0100: "5A",
        0x0101: "5B",
        0x0102: "5C",
        0x0103: "6C",
        0x0104: "3C",
        0x0105: "2A",
        0x0106: "2B",
        0x0107: "2C",
        0x0108: "j.A",
        0x0109: "j.B",
        0x010A: "j.C",
        0x010B: "j.2B",
        0x010C: "j.2C",
        0x010E: "6B",
    }
    try:
        if int(aid) in normal_names:
            return normal_names[int(aid)]
    except Exception:
        pass

    table_index = mv.get("table_index")
    try:
        if int(table_index) in normal_names:
            return normal_names[int(table_index)]
    except Exception:
        pass

    return "?"


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
    "j.A", "j.B", "j.C", "j.2B", "j.2C",
)
_NORMAL_PREVIEW_RANK = {name.lower(): i for i, name in enumerate(_NORMAL_PREVIEW_ORDER)}


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
        "j.2b": "j.2B", "j2b": "j.2B", "j2B".lower(): "j.2B",
        "j.2c": "j.2C", "j2c": "j.2C", "j2C".lower(): "j.2C",
    }

    return aliases.get(low)


def _normal_row_quality(mv: dict) -> tuple[int, int, int, int]:
    """Prefer rows that actually have useful frame values if duplicates exist."""
    if not isinstance(mv, dict):
        return (0, 0, 0, 0)
    startup = _normal_int(mv, "startup", "start", "active_start")
    a1 = _normal_int(mv, "active_start", "a_start")
    a2 = _normal_int(mv, "active_end", "a_end")
    hit = _normal_int(mv, "hitstun", "hit", "h")
    block = _normal_int(mv, "blockstun", "block", "b")
    filled = sum(v is not None for v in (startup, a1, a2, hit, block))
    active_span = 0 if a1 is None or a2 is None else max(0, a2 - a1)
    damage = _normal_int(mv, "damage", "dmg") or 0
    return (filled, active_span, damage, -int(mv.get("_scan_index", 0) or 0))


def _normal_visible_moves(moves: list) -> list:
    """Return only the curated normal rows, in fighting-game notation order.

    The scan can contain duplicate/system/debug rows, and some characters put
    command normals before or after jump normals. The preview should not depend
    on raw scan order. It shows the useful set only:
      5A, 2A, 5B, 2B, optional 6B, 5C, 2C, optional 4C/6C/3C, j.A, j.B, j.C, optional j.2B/j.2C
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


def _draw_scan_metric_chip(
    surf: pygame.Surface,
    rect: pygame.Rect,
    smallfont: pygame.font.Font,
    label: str,
    value: str,
    accent: tuple[int, int, int],
) -> None:
    # Neutral metric chip. The row/slot can carry accent color; the data itself
    # stays calm and readable.
    _draw_vertical_gradient(
        surf,
        rect,
        (24, 28, 40),
        (16, 18, 26),
        235,
    )
    pygame.draw.rect(surf, (48, 56, 78), rect, 1, border_radius=4)

    label_s = smallfont.render(label, True, GUI_TEXT_DIM)
    value_s = _render_outlined_text(
        smallfont,
        value,
        GUI_TEXT,
        (0, 0, 0),
        rect.width - label_s.get_width() - 10,
        outline_px=1,
    )

    x = rect.x + 5
    y = rect.y + (rect.height - label_s.get_height()) // 2
    surf.blit(label_s, (x, y))
    surf.blit(value_s, (rect.right - value_s.get_width() - 5, rect.y + (rect.height - value_s.get_height()) // 2))


def draw_scan_normals_polished(
    surf: pygame.Surface,
    rect: pygame.Rect,
    font: pygame.font.Font,
    smallfont: pygame.font.Font,
    scan_data,
) -> None:
    """Draw the normals preview as four readable cards instead of raw text.

    This is display-only. It consumes the same scan result that hud_draw's
    draw_scan_normals() used and does not change scan timing, memory reads, or
    move detection.
    """
    if rect.width <= 0 or rect.height <= 0:
        return

    bg = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
    _draw_vertical_gradient(
        bg,
        bg.get_rect(),
        (17, 20, 29),
        (12, 13, 19),
        255,
    )
    surf.blit(bg, rect.topleft)

    title = smallfont.render("Scan: Normals Preview", True, GUI_TEXT)
    subtitle = smallfont.render("S startup | A active | H hit | B block", True, GUI_TEXT_DIM)
    surf.blit(title, (rect.x + 10, rect.y + 8))
    surf.blit(subtitle, (rect.right - subtitle.get_width() - 10, rect.y + 8))

    if not scan_data:
        msg = smallfont.render("No scan data yet. Trigger a normals scan or wait for the scan worker.", True, GUI_TEXT_MUTED)
        surf.blit(msg, (rect.x + 10, rect.y + 34))
        return

    slots = []
    try:
        slots = list(scan_data or [])
    except Exception:
        slots = []

    if not slots:
        msg = smallfont.render("No normals found.", True, GUI_TEXT_MUTED)
        surf.blit(msg, (rect.x + 10, rect.y + 34))
        return

    wanted_order = {"P1-C1": 0, "P1-C2": 1, "P2-C1": 2, "P2-C2": 3}

    def slot_key(slot: dict) -> tuple[int, str]:
        label = str(slot.get("slot_label") or slot.get("slot") or "")
        return (wanted_order.get(label, 99), label)

    slots = sorted([s for s in slots if isinstance(s, dict)], key=slot_key)[:4]

    pad = 10
    gap = 10
    cards_top = rect.y + 32
    cards_h = max(40, rect.height - 42)
    card_w = max(120, (rect.width - pad * 2 - gap * (len(slots) - 1)) // max(1, len(slots)))

    # Base rhythm. Each card can tighten further after we know how many curated
    # normal rows that character actually has.
    header_h = 22 if cards_h < 220 else 24
    row_h = 15

    for si, slot in enumerate(slots):
        card_x = rect.x + pad + si * (card_w + gap)
        card = pygame.Rect(card_x, cards_top, card_w, cards_h)

        _draw_vertical_gradient(
            surf,
            card,
            (22, 26, 38),
            (14, 16, 23),
            238,
        )
        pygame.draw.rect(surf, (52, 62, 86), card, 1, border_radius=6)

        slot_label = str(slot.get("slot_label") or slot.get("slot") or f"S{si + 1}")
        char_name = str(slot.get("char_name") or slot.get("character") or slot.get("name") or "?")
        accent = GUI_ACCENT_BLUE
        if slot_label.startswith("P1"):
            accent = GUI_P1 if slot_label.endswith("C1") else GUI_P3
        elif slot_label.startswith("P2"):
            accent = GUI_P2 if slot_label.endswith("C1") else GUI_P4

        accent_bar = pygame.Rect(card.x, card.y, 3, card.height)
        pygame.draw.rect(surf, accent, accent_bar, border_radius=2)

        header_rect = pygame.Rect(card.x + 1, card.y + 1, card.width - 2, header_h)
        _draw_vertical_gradient(
            surf,
            header_rect,
            (31, 37, 52),
            (21, 25, 36),
            235,
        )

        slot_s = _render_outlined_text(font, slot_label, accent, (0, 0, 0), 84, outline_px=1)
        surf.blit(slot_s, (card.x + 10, card.y + 5))

        char_s = _fit_text(smallfont, char_name, GUI_TEXT_MUTED, card.width - 88)
        surf.blit(char_s, (card.x + 72, card.y + 8))

        moves = slot.get("moves") or []
        if not isinstance(moves, list):
            moves = []

        # Preview rows are curated and reordered; never show anonymous/debug
        # rows and never cut off j.B/j.C just because raw scan order was odd.
        visible_moves = _normal_visible_moves(moves)
        desired_rows = max(1, len(visible_moves))
        available_h = max(1, card.height - header_h - 8)
        local_row_h = max(12, min(17, available_h // desired_rows))

        y = card.y + header_h + 5
        for mi, mv in enumerate(visible_moves):
            if not isinstance(mv, dict):
                continue

            row = pygame.Rect(card.x + 8, y, card.width - 16, local_row_h)
            row_fill = (19, 22, 31) if mi % 2 == 0 else (16, 18, 26)
            pygame.draw.rect(surf, row_fill, row, border_radius=4)

            label = _normal_move_label(mv)
            btn_accent = _normal_button_accent(label)
            pygame.draw.rect(surf, btn_accent, pygame.Rect(row.x, row.y, 3, row.height), border_radius=2)

            label_s = _render_outlined_text(smallfont, label, GUI_TEXT, (0, 0, 0), 38, outline_px=1)
            surf.blit(label_s, (row.x + 8, row.y + (row.height - label_s.get_height()) // 2))

            startup = _normal_int(mv, "startup", "start", "active_start")
            a1 = _normal_int(mv, "active_start", "a_start")
            a2 = _normal_int(mv, "active_end", "a_end")
            hit = _normal_int(mv, "hitstun", "hit", "h")
            block = _normal_int(mv, "blockstun", "block", "b")

            active_txt = "-"
            if a1 is not None and a2 is not None:
                active_txt = f"{a1}-{a2}"
            elif a1 is not None:
                active_txt = str(a1)

            metrics = [
                ("S", "-" if startup is None else str(startup), GUI_TEXT_MUTED),
                ("A", active_txt, btn_accent),
                ("H", "-" if hit is None else str(hit), (175, 205, 245)),
                ("B", "-" if block is None else str(block), (215, 190, 235)),
            ]

            metric_w = max(34, min(54, (row.width - 52) // 4))
            mx = row.right - metric_w * 4 - 4
            chip_h = max(10, row.height - 2)
            for label2, value, col in metrics:
                chip = pygame.Rect(mx, row.y + 1, metric_w - 4, chip_h)
                _draw_scan_metric_chip(surf, chip, smallfont, label2, value, col)
                mx += metric_w

            y += local_row_h


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

    for qi, quick in enumerate(quick_defs):
        qx = qa_x0 + qi * (qa_w + qa_gap)
        qrect_local = pygame.Rect(qx, qa_y, qa_w, qa_h)
        qhover = qrect_local.collidepoint(mx_local, my_local)

        qlabel = str(quick.get("label", f"A{qi + 1}"))
        is_selected = active_quick_index is not None and int(active_quick_index) == qi
        is_flashing = flash_quick_index is not None and int(flash_quick_index) == qi
        active = bool(quick.get("active", False)) or is_selected or is_flashing

        accent = GUI_ACCENT_BLUE
        low = qlabel.lower()
        if bool(quick.get("default", False)):
            accent = GUI_TEXT_DIM
        elif " c" in low or low.endswith("c") or " h" in low or low.endswith("h"):
            accent = (105, 215, 155)
        elif " b" in low or low.endswith("b") or " m" in low or low.endswith("m"):
            accent = (220, 195, 105)
        elif " a" in low or low.endswith("a") or " l" in low or low.endswith("l"):
            accent = (115, 155, 235)

        fill = (48, 58, 82) if is_selected else (35, 43, 62)
        if is_flashing:
            fill = _brighten(fill, 22)

        draw_glass_button(
            surf,
            qrect_local,
            qlabel,
            smallfont,
            active=active,
            hover=qhover,
            accent=accent,
            fill=fill,
            align="center",
        )

        if is_selected:
            # Persistent selected-assist marker: subtle inner glow + underline.
            pygame.draw.rect(
                surf,
                (*accent, 48),
                qrect_local.inflate(-3, -3),
                1,
                border_radius=4,
            )
            pygame.draw.rect(
                surf,
                accent,
                pygame.Rect(qrect_local.x + 5, qrect_local.bottom - 4, qrect_local.width - 10, 2),
                border_radius=1,
            )

        qclick = pygame.Rect(
            panel_rect.x + qrect_local.x,
            panel_rect.y + qrect_local.y,
            qrect_local.width,
            qrect_local.height,
        ).inflate(8, 8)

        out[(slot_label, qi)] = qclick

    return out




def merged_debug_values():
    core_flags = read_debug_flags()
    training   = read_training_flags()

    trpause_row    = None
    remaining_training = []
    for entry in training:
        if entry and entry[0] == "TrPause" and trpause_row is None:
            trpause_row = entry
        else:
            remaining_training.append(entry)

    if trpause_row is not None:
        if core_flags:
            core_flags = [core_flags[0], trpause_row] + core_flags[1:]
        else:
            core_flags = [trpause_row]

    return core_flags + remaining_training


def safe_read_fighter(base: int, yoff: int) -> dict | None:
    try:
        snap = read_fighter(base, yoff)
    except Exception as e:
        print(f"[safe_read_fighter] read_fighter raised {e!r} for base=0x{base:08X}")
        return None
    return snap if snap else None


def init_pygame():
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TvCGUI.HUD.1")

    pygame.init()

    try:
        font = pygame.font.SysFont("consolas", FONT_MAIN_SIZE)
    except Exception:
        font = pygame.font.Font(None, FONT_MAIN_SIZE)

    try:
        smallfont = pygame.font.SysFont("consolas", FONT_SMALL_SIZE)
    except Exception:
        smallfont = pygame.font.Font(None, FONT_SMALL_SIZE)

    screen = pygame.display.set_mode((SCREEN_W, SCREEN_H), pygame.RESIZABLE)
    pygame.display.set_caption("TvC Continuo Tool")

    icon_path = resource_path("assets", "portraits", "Placeholder.png")
    if not os.path.exists(icon_path):
        icon_path = resource_path("assets", "icon.png")
    if os.path.exists(icon_path):
        icon = pygame.image.load(icon_path).convert_alpha()
        pygame.display.set_icon(icon)

    return screen, font, smallfont


def resolve_bases(last_base_by_ptr: dict, y_off_by_base: dict) -> list:
    resolved = []
    for slotname, ptr_addr, teamtag in SLOTS:
        raw_base = rd32(ptr_addr)
        if raw_base is None or not addr_in_ram(raw_base):
            base = None
        else:
            base = raw_base

        changed = base is not None and last_base_by_ptr.get(ptr_addr) != base
        if base and changed:
            last_base_by_ptr[ptr_addr] = base
            METER_CACHE.drop(base)
            y_off_by_base[base] = pick_posy_off_no_jump(base)

        resolved.append((slotname, teamtag, base))
    return resolved


def compute_team_giant_solo(snaps: dict) -> tuple[bool, bool]:
    def team_solo(prefix: str) -> bool:
        c1 = snaps.get(f"{prefix}-C1")
        c2 = snaps.get(f"{prefix}-C2")
        if not c1:
            return False
        if (c1.get("id") or 0) not in GIANT_IDS:
            return False
        if not c2:
            return True
        b1, b2 = c1.get("base"), c2.get("base")
        return isinstance(b1, int) and isinstance(b2, int) and b1 == b2

    return team_solo("P1"), team_solo("P2")


def ensure_scan_now(last_scan_normals, last_scan_time):
    if last_scan_normals is not None:
        return last_scan_normals, last_scan_time
    if HAVE_SCAN_NORMALS and scan_normals_all is not None:
        try:
            data = scan_normals_all.scan_once()
            return data, time.time()
        except Exception as e:
            print("sync scan failed:", e)
    return None, last_scan_time


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def legacy_main():
    print("HUD: waiting for Dolphin...")
    hook()
    print("HUD: hooked Dolphin.")

    move_map, global_map = load_move_map(GENERIC_MAPPING_CSV, PAIR_MAPPING_CSV)

    screen, font, smallfont = init_pygame()
    clock = pygame.time.Clock()

    placeholder_portrait = load_portrait_placeholder()
    portraits = load_portraits_from_dir(resource_path("assets", "portraits"))
    print(f"HUD: loaded {len(portraits)} portraits.")

    if HAVE_SCAN_NORMALS and scan_normals_all is not None:
        scan_worker = ScanNormalsWorker(scan_normals_all.scan_once)
        scan_worker.start()
    else:
        scan_worker = None

    # ------------------------------------------------------------------
    # Managers
    # ------------------------------------------------------------------
    mission_mgr = MissionManager(
        move_map=move_map,
        global_map=global_map,
        debug_flag_addrs=DEBUG_FLAG_ADDRS,
        read_debug_flags_fn=merged_debug_values,
        move_label_for_fn=move_label_for,
    )
    hud_mgr = HudOverlayManager(move_map=move_map, global_map=global_map)

    # ------------------------------------------------------------------
    # Runtime state
    # ------------------------------------------------------------------
    last_scan_normals = None
    last_scan_time    = 0.0
    scan_anim         = None

    def _scan_move_window_for_slot(slot_label: str, cur_anim: int | None):
        if cur_anim is None or not last_scan_normals:
            return None, None
        try:
            for slot_data in last_scan_normals:
                if slot_data.get("slot_label") != slot_label:
                    continue
                for mv in slot_data.get("moves", []):
                    if mv.get("id") == cur_anim:
                        return mv.get("active_start"), mv.get("active_end")
        except Exception:
            pass
        return None, None

    last_base_by_ptr  = {}
    y_off_by_base     = {}
    prev_hp           = {}
    pool_baseline     = {}
    char_meta_by_base = {}
    last_move_anim_id = {}
    last_char_by_slot = {}

    baroque_latch_by_base        = {}
    last_baroque_pct_by_base     = {}
    last_baroque_ready_by_base   = {}
    baroque_peak_by_base         = {}

    render_snap_by_slot    = {}
    render_portrait_by_slot = {}

    panel_anim            = {}
    anim_queue_after_scan = set()
    panel_btn_flash       = {s: 0 for (s, _, _) in SLOTS}
    quick_btn_flash       = {}
    active_quick_assist_by_slot = {}

    manual_scan_requested = False
    need_rescan_normals   = False

    last_adv_display = ""
    pending_hits     = []
    frame_idx        = 0

    # ------------------------------------------------------------------
    # Master overlay subprocess
    # ------------------------------------------------------------------
    master_overlay_proc   = None
    master_overlay_active = False
    overlay_enabled       = True

    def _launch_master_overlay():
        nonlocal master_overlay_proc, master_overlay_active
        try:
            master_overlay_proc = subprocess.Popen(
                frozen_exe("master_overlay"),
                creationflags=(
                    subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
                ),
            )
            master_overlay_active = True
            print("[master] launched")
        except Exception as e:
            print(f"[master] launch failed: {e}")

    def _stop_master_overlay():
        nonlocal master_overlay_proc, master_overlay_active
        if master_overlay_proc and master_overlay_proc.poll() is None:
            try:
                master_overlay_proc.terminate()
            except Exception:
                pass
        master_overlay_proc = None
        master_overlay_active = False
        print("[master] stopped")

    def _check_master_overlay_proc():
        nonlocal master_overlay_proc, master_overlay_active
        if master_overlay_proc and master_overlay_proc.poll() is not None:
            master_overlay_proc = None
            master_overlay_active = False
            print("[master] closed")

    # Hitbox filter
    HITBOX_FILTER_FILE = "hitbox_filter.json"
    hitbox_slots = {"P1": True, "P2": True, "P3": True, "P4": True}

    def _write_hitbox_filter():
        try:
            with open(HITBOX_FILTER_FILE, "w") as f:
                json.dump(hitbox_slots, f)
        except Exception:
            pass

    def _write_master_control():
        payload = {
            "show_hud":      overlay_enabled,
            "show_hitboxes": any(hitbox_slots.values()),
            "show_debug":    False,
        }
        try:
            with open(MASTER_CONTROL_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    def _sync_master_overlay_state():
        want_hitboxes = any(hitbox_slots.values())
        want_process  = overlay_enabled or want_hitboxes
        if want_process and not master_overlay_active:
            _launch_master_overlay()
        elif not want_process and master_overlay_active:
            _stop_master_overlay()

    _write_hitbox_filter()
    _write_master_control()
    mission_mgr.write_mode_state()
    mission_mgr.write_overlay_data(render_snap_by_slot)
    _sync_master_overlay_state()

    # Debug overlay
    debug_overlay     = True
    debug_click_areas = {}
    debug_scroll_offset = 0
    debug_max_scroll    = 0
    debug_cache         = []
    DEBUG_REFRESH_EVERY = 6

    # Lower inspector workspace. Default to Normals Preview because it is the
    # most useful always-on view, while Events/Debug/Activity are available as
    # tabs without stealing vertical room.
    active_bottom_tab = "scan"
    bottom_tab_rects: dict[str, pygame.Rect] = {}

    # Momentary write restore
    hype_restore_addr  = None
    hype_restore_ts    = 0.0
    hype_restore_orig  = 0
    special_restore_addr = None
    special_restore_ts   = 0.0
    special_restore_orig = 0

    running = True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------
    while running:
        now  = time.time()
        t_ms = pygame.time.get_ticks()
        mouse_clicked_pos = None

        # Events
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.VIDEORESIZE:
                screen = pygame.display.set_mode(ev.size, pygame.RESIZABLE)
            elif ev.type == pygame.MOUSEBUTTONDOWN:
                if ev.button == 1:
                    mouse_clicked_pos = ev.pos
                elif ev.button == 4 and debug_overlay and active_bottom_tab == "debug":
                    if debug_scroll_offset > 0:
                        debug_scroll_offset -= 1
                elif ev.button == 5 and debug_overlay and active_bottom_tab == "debug":
                    if debug_scroll_offset < debug_max_scroll:
                        debug_scroll_offset += 1
            elif ev.type == pygame.MOUSEWHEEL and debug_overlay and active_bottom_tab == "debug":
                if ev.y > 0 and debug_scroll_offset > 0:
                    debug_scroll_offset -= 1
                elif ev.y < 0 and debug_scroll_offset < debug_max_scroll:
                    debug_scroll_offset += 1

        # Scan worker results
        if scan_worker:
            res, ts = scan_worker.get_latest()
            if res is not None and ts > last_scan_time:
                last_scan_normals = res
                last_scan_time    = ts
                scan_anim = {"start": now, "dur": SCAN_SLIDE_DURATION}

        # Resolve slot bases
        resolved_slots = resolve_bases(last_base_by_ptr, y_off_by_base)
        p1c1_base = next((b for n, t, b in resolved_slots if n == "P1-C1" and b), None)
        p2c1_base = next((b for n, t, b in resolved_slots if n == "P2-C1" and b), None)
        meter_p1 = read_meter(p1c1_base, teamtag="P1")
        meter_p2 = read_meter(p2c1_base, teamtag="P2")

        # Build snapshots
        snaps = {}
        for slotname, teamtag, base in resolved_slots:
            if not base:
                if last_char_by_slot.get(slotname) is not None:
                    anim_queue_after_scan.add((slotname, "fadeout"))
                    last_char_by_slot[slotname] = None
                    need_rescan_normals = True
                continue

            yoff = y_off_by_base.get(base, 0xF4)
            snap = safe_read_fighter(base, yoff)
            if not snap:
                continue

            snap["base"]     = base
            snap["teamtag"]  = teamtag
            snap["slotname"] = slotname

            blk = rbytes(base, FIGHTER_BLOCK_SIZE)

            true_id_current = None
            if blk:
                true_id_current = u32be_from_block(blk, OFF_CHAR_ID)
            if true_id_current in (None, 0):
                try:
                    true_id_current = rd32(base + OFF_CHAR_ID)
                except Exception:
                    true_id_current = None

            meta = char_meta_by_base.get(base)
            if meta is None or meta.get("id") != true_id_current:
                name_cached   = CHAR_NAMES.get(true_id_current)
                csv_id_cached = CHAR_ID_CORRECTION.get(name_cached, true_id_current)
                char_meta_by_base[base] = {
                    "id": true_id_current,
                    "name": name_cached,
                    "csv_char_id": csv_id_cached,
                }

            meta = char_meta_by_base.get(base)
            if meta:
                snap["id"]          = meta["id"]
                snap["name"]        = meta["name"]
                snap["csv_char_id"] = meta["csv_char_id"]
            else:
                snap["csv_char_id"] = true_id_current

            csv_char_id = snap.get("csv_char_id")
            cur_anim    = snap.get("attA") or snap.get("attB")
            mv_label    = lookup_move_name(cur_anim, csv_char_id)
            if not mv_label:
                mv_label = move_label_for(cur_anim, csv_char_id, move_map, global_map)

            snap["mv_label"]      = mv_label
            snap["mv_id_display"] = cur_anim

            active_start, active_end = _scan_move_window_for_slot(slotname, cur_anim)
            snap["active_start"] = active_start
            snap["active_end"]   = active_end
            last_move_anim_id[base] = cur_anim

            pool_byte = snap.get("hp_pool_byte")
            if pool_byte is not None:
                prev_max = pool_baseline.get(base, 0)
                if pool_byte > prev_max:
                    pool_baseline[base] = pool_byte
                max_pool = pool_baseline.get(base, 1)
                snap["pool_pct"] = (pool_byte / max_pool) * 100.0 if max_pool else 0.0
            else:
                snap["pool_pct"] = 0.0

            max_hp_stat = snap.get("max") or 0
            hp32 = 0
            pool32 = 0
            if blk:
                tmp_hp   = u32be_from_block(blk, HP32_OFF)
                tmp_pool = u32be_from_block(blk, POOL32_OFF)
                if tmp_hp   is not None: hp32   = tmp_hp
                if tmp_pool is not None: pool32 = tmp_pool
            if hp32   == 0: hp32   = rd32(base + HP32_OFF)   or 0
            if pool32 == 0: pool32 = rd32(base + POOL32_OFF) or 0

            ready_local  = False
            red_amt      = 0
            red_pct_max  = 0.0
            if hp32 and pool32 and hp32 != pool32:
                ready_local = True
                bigger  = max(hp32, pool32)
                smaller = min(hp32, pool32)
                red_amt = bigger - smaller
                if max_hp_stat:
                    red_pct_max = (red_amt / float(max_hp_stat)) * 100.0

            snap["baroque_local_hp32"]   = hp32
            snap["baroque_local_pool32"] = pool32
            snap["baroque_ready_local"]  = ready_local
            snap["baroque_red_amt"]      = red_amt
            snap["baroque_red_pct_max"]  = red_pct_max

            baroque_peak_by_base[base] = max(red_pct_max, baroque_peak_by_base.get(base, 0.0))
            baroque_drop_pct   = baroque_peak_by_base[base] - red_pct_max
            raw_baroque_cancel = baroque_drop_pct >= 1.0

            if raw_baroque_cancel:
                baroque_latch_by_base[base] = 5
            else:
                baroque_latch_by_base[base] = max(
                    0, int(baroque_latch_by_base.get(base, 0)) - 1
                )

            snap["baroque_cancel_raw"]         = raw_baroque_cancel
            snap["baroque_cancel_latched"]     = int(baroque_latch_by_base.get(base, 0)) > 0
            snap["baroque_cancel_latch_frames"] = int(baroque_latch_by_base.get(base, 0))

            last_baroque_pct_by_base[base]   = float(red_pct_max)
            if raw_baroque_cancel:
                baroque_peak_by_base[base] = float(red_pct_max)
            last_baroque_ready_by_base[base] = bool(ready_local)

            snap["meter"] = meter_p1 if teamtag == "P1" else meter_p2

            if teamtag == "P1":
                inputs_struct = {}
                for key, addr in INPUT_MONITOR_ADDRS.items():
                    v = rd8(addr)
                    inputs_struct[key] = 0 if v is None else v
                snap["inputs"] = inputs_struct
            else:
                snap["inputs"] = {}

            snaps[slotname] = snap

            if last_char_by_slot.get(slotname) != snap.get("name"):
                last_char_by_slot[slotname] = snap.get("name")
                anim_queue_after_scan.add((slotname, "fadein"))
                need_rescan_normals = True

            render_snap_by_slot[slotname]    = snap
            render_portrait_by_slot[slotname] = get_portrait_for_snap(
                snap, portraits, placeholder_portrait
            )

        # Giant normalisation
        p1_giant_solo, p2_giant_solo = compute_team_giant_solo(snaps)
        if p1_giant_solo or p2_giant_solo:
            snaps = reassign_slots_for_giants(snaps)

        # Assist selector runtime hook. The assist scanner stores per-fighter
        # desired assists; main.py owns the reliable current move label/id, so
        # when a fighter enters assist attack (426), patch that fighter profile
        # into the shared character selector table immediately.
        try:
            tick_assist_profiles_from_main(snaps)
        except Exception as e:
            if frame_idx % 60 == 0:
                print(f"[assist scanner] main trigger failed: {e!r}")

        # Mission manager tick
        mission_mgr.update(snaps, render_snap_by_slot, frame_idx, now)

        # Damage / hit logging
        if frame_idx % DAMAGE_EVERY_FRAMES == 0:
            for vic_slot, vic_snap in snaps.items():
                vic_move_id = vic_snap.get("attA") or vic_snap.get("attB")
                if vic_move_id not in REACTION_STATES:
                    continue

                vic_team = vic_snap["teamtag"]
                attackers = [s for s in snaps.values() if s["teamtag"] != vic_team]
                if not attackers:
                    continue

                best_d2  = None
                atk_snap = None
                for cand in attackers:
                    d2v = dist2(vic_snap, cand)
                    if best_d2 is None or d2v < best_d2:
                        best_d2  = d2v
                        atk_snap = cand
                if not atk_snap:
                    continue

                atk_move_id    = atk_snap.get("attA") or atk_snap.get("attB")
                atk_move_label = atk_snap.get("mv_label")

                ADV_TRACK.start_contact(
                    atk_snap["base"], vic_snap["base"],
                    frame_idx, atk_move_id, vic_move_id,
                )

                base      = vic_snap["base"]
                hp_now    = vic_snap["cur"]
                hp_prev   = prev_hp.get(base, hp_now)
                prev_hp[base] = hp_now
                dmg = hp_prev - hp_now
                if dmg >= MIN_HIT_DAMAGE:
                    log_engaged(atk_snap, vic_snap, frame_idx)
                    log_hit(atk_snap, vic_snap, dmg, frame_idx, atk_move_label, atk_move_id)

        if frame_idx % ADV_EVERY_FRAMES == 0:
            pairs = [
                ("P1-C1", "P2-C1"), ("P1-C1", "P2-C2"),
                ("P1-C2", "P2-C1"), ("P1-C2", "P2-C2"),
                ("P2-C1", "P1-C1"), ("P2-C1", "P1-C2"),
                ("P2-C2", "P1-C1"), ("P2-C2", "P1-C2"),
            ]
            for atk_slot, vic_slot in pairs:
                atk_snap = snaps.get(atk_slot)
                vic_snap = snaps.get(vic_slot)
                if atk_snap and vic_snap:
                    ADV_TRACK.update_pair(
                        atk_snap["base"], vic_snap["base"], frame_idx,
                        atk_snap.get("attA") or atk_snap.get("attB"),
                        vic_snap.get("attA") or vic_snap.get("attB"),
                    )

            freshest = ADV_TRACK.get_freshest_final_info()
            if freshest:
                atk_b, vic_b, plusf, fin_frame = freshest
                if abs(plusf) <= 64:
                    atk_obj = next((s for s in snaps.values() if s["base"] == atk_b), None)
                    vic_obj = next((s for s in snaps.values() if s["base"] == vic_b), None)
                    if atk_obj and vic_obj:
                        last_adv_display = (
                            f"{atk_obj['slotname']}({atk_obj['name']}) vs "
                            f"{vic_obj['slotname']}({vic_obj['name']}): "
                            f"{plusf:+.1f}f"
                        )
                        log_frame_advantage(atk_obj, vic_obj, plusf)
                    else:
                        last_adv_display = f"Frame adv: {plusf:+.1f}f"

        # ------------------------------------------------------------------
        # Rendering
        # ------------------------------------------------------------------
        screen.fill(COL_BG)
        w, h  = screen.get_size()
        layout = compute_layout(w, h - TOP_UI_RESERVED, snaps)

        for key, value in layout.items():
            if isinstance(value, pygame.Rect):
                value.y += TOP_UI_RESERVED

        # Give the character panels a dedicated footer area for Quick Assists.
        # This keeps the assist buttons from crowding the move text or the
        # Frame Data / Mission Mode buttons, without making main.py own any
        # assist logic. The lower HUD areas are shifted down and the scan
        # preview absorbs the height loss.
        qa_panel_extra = 26 if h >= 700 else 18
        if qa_panel_extra > 0:
            for _key in ("p1c1", "p2c1"):
                _rect = layout.get(_key)
                if isinstance(_rect, pygame.Rect):
                    _rect.height += qa_panel_extra

            for _key in ("p1c2", "p2c2"):
                _rect = layout.get(_key)
                if isinstance(_rect, pygame.Rect):
                    _rect.y += qa_panel_extra
                    _rect.height += qa_panel_extra

            qa_total_shift = qa_panel_extra * 2
            for _key in ("act", "events", "debug", "scan"):
                _rect = layout.get(_key)
                if isinstance(_rect, pygame.Rect):
                    _rect.y += qa_total_shift

            _scan_rect = layout.get("scan")
            if isinstance(_scan_rect, pygame.Rect):
                _scan_rect.height = max(54, _scan_rect.height - qa_total_shift)

        layout["p1_is_giant"] = bool(p1_giant_solo)
        layout["p2_is_giant"] = bool(p2_giant_solo)

        # Panel animations
        if anim_queue_after_scan:
            slot_rect_lookup = {
                "P1-C1": layout["p1c1"],
                "P2-C1": layout["p2c1"],
                "P1-C2": layout["p1c2"],
                "P2-C2": layout["p2c2"],
            }
            for slot_label, kind in list(anim_queue_after_scan):
                base_rect = slot_rect_lookup.get(slot_label)
                if base_rect is None:
                    anim_queue_after_scan.discard((slot_label, kind))
                    continue

                panel_height = base_rect.height
                offscreen_y  = -panel_height - 8
                anim = {
                    "start":  now,
                    "dur":    PANEL_SLIDE_DURATION,
                    "from_y": None,
                    "to_y":   None,
                    "from_a": 255,
                    "to_a":   255,
                }
                if kind == "fadein":
                    anim["from_y"] = offscreen_y
                    anim["to_y"]   = base_rect.y
                    anim["from_a"] = 0
                    anim["to_a"]   = 255
                elif kind == "fadeout":
                    anim["from_y"] = base_rect.y
                    anim["to_y"]   = offscreen_y
                    anim["from_a"] = 255
                    anim["to_a"]   = 0
                else:
                    anim_queue_after_scan.discard((slot_label, kind))
                    continue

                panel_anim[slot_label] = anim
                anim_queue_after_scan.discard((slot_label, kind))

        def anim_rect_and_alpha(slot_label, base_rect):
            anim = panel_anim.get(slot_label)
            if not anim:
                return base_rect, 255

            if anim.get("to_y") is None:
                anim["to_y"] = base_rect.y
            if anim.get("from_y") is None:
                anim["from_y"] = base_rect.y

            t    = now - anim["start"]
            dur  = anim.get("dur") or PANEL_SLIDE_DURATION
            frac = max(0.0, min(1.0, t / dur)) if dur else 1.0

            y = anim["from_y"] + (anim["to_y"] - anim["from_y"]) * frac

            from_a = anim.get("from_a", 255)
            to_a   = anim.get("to_a",   255)
            if from_a == 0 and to_a > 0:
                inner = max(0.0, min(1.0, (frac - 0.9) / 0.1)) if frac > 0.9 else 0.0
                alpha = int(from_a + (to_a - from_a) * inner)
            else:
                alpha = int(from_a + (to_a - from_a) * frac)

            if frac >= 1.0:
                if to_a == 0:
                    render_snap_by_slot.pop(slot_label, None)
                    render_portrait_by_slot.pop(slot_label, None)
                panel_anim.pop(slot_label, None)

            r = base_rect.copy()
            r.y = int(y)
            return r, max(0, min(255, alpha))

        # Top command dock
        _check_master_overlay_proc()
        mx_h, my_h = pygame.mouse.get_pos()

        hb_btn_rect, ps_btn_rect, as_btn_rect, hud_btn_rect, hb_filter_rects = draw_top_command_dock(
            screen,
            smallfont,
            hitbox_slots=hitbox_slots,
            overlay_enabled=overlay_enabled,
            mouse_pos=(mx_h, my_h),
        )

        # Panel rects
        r_p1c1, a_p1c1 = anim_rect_and_alpha("P1-C1", layout["p1c1"])
        r_p2c1, a_p2c1 = anim_rect_and_alpha("P2-C1", layout["p2c1"])
        r_p1c2, a_p1c2 = anim_rect_and_alpha("P1-C2", layout["p1c2"])
        r_p2c2, a_p2c2 = anim_rect_and_alpha("P2-C2", layout["p2c2"])

        quick_btn_areas = {}

        def blit_panel_with_buttons(panel_rect, slot_label, alpha, header):
            snap     = render_snap_by_slot.get(slot_label)
            portrait = render_portrait_by_slot.get(slot_label, placeholder_portrait)

            surf = pygame.Surface((panel_rect.width, panel_rect.height), pygame.SRCALPHA)
            draw_panel_classic(surf, surf.get_rect(), snap, portrait, font, smallfont, header, t_ms)

            btn_h          = 20
            frame_btn_w    = 110
            mission_btn_w  = 110
            btn_gap        = 8
            bottom_pad     = 8
            total_btn_w    = frame_btn_w + btn_gap + mission_btn_w
            btn_x          = panel_rect.width - total_btn_w - 10
            btn_y          = panel_rect.height - btn_h - bottom_pad

            frame_btn_local   = pygame.Rect(btn_x, btn_y, frame_btn_w, btn_h)
            mission_btn_local = pygame.Rect(btn_x + frame_btn_w + btn_gap, btn_y, mission_btn_w, btn_h)

            mx, my       = pygame.mouse.get_pos()
            mx_local     = mx - panel_rect.x
            my_local     = my - panel_rect.y
            frame_hover  = frame_btn_local.collidepoint(mx_local, my_local)
            mission_hover = mission_btn_local.collidepoint(mx_local, my_local)
            flash_left   = panel_btn_flash.get(slot_label, 0)

            if flash_left > 0:
                frame_base_col   = (70, 105, 170)
                frame_border_col = (235, 240, 255)
            elif frame_hover:
                frame_base_col   = (48, 54, 68)
                frame_border_col = (210, 220, 235)
            else:
                frame_base_col   = (31, 33, 42)
                frame_border_col = (135, 145, 165)

            if mission_mgr.active_slot == slot_label:
                mission_base_col   = (88, 68, 135)
                mission_border_col = (235, 240, 255)
            elif mission_hover:
                mission_base_col   = (48, 54, 68)
                mission_border_col = (210, 220, 235)
            else:
                mission_base_col   = (31, 33, 42)
                mission_border_col = (135, 145, 165)

            pygame.draw.rect(surf, frame_base_col,   frame_btn_local,   border_radius=3)
            pygame.draw.rect(surf, frame_border_col, frame_btn_local,   1, border_radius=3)
            surf.blit(smallfont.render("Frame Data", True, (220, 220, 220)),
                      (frame_btn_local.x + 6, frame_btn_local.y + 2))

            pygame.draw.rect(surf, mission_base_col,   mission_btn_local, border_radius=3)
            pygame.draw.rect(surf, mission_border_col, mission_btn_local, 1, border_radius=3)
            surf.blit(smallfont.render("Mission Mode", True, (220, 220, 220)),
                      (mission_btn_local.x + 6, mission_btn_local.y + 2))

            if flash_left > 0:
                pygame.draw.rect(surf, (255, 255, 255),
                                 frame_btn_local.inflate(4, 4), 2, border_radius=4)

            # Optional quick-assist buttons. main.py only draws/clicks these;
            # assist_scanner_window owns the JSON, route resolution, and writes.
            active_quick_index = None
            active_row = active_quick_assist_by_slot.get(slot_label)
            if isinstance(active_row, dict):
                active_char_id = int(active_row.get("char_id") or 0)
                snap_char_id = 0
                if isinstance(snap, dict):
                    for _field in ("id", "csv_char_id", "char_id"):
                        try:
                            snap_char_id = int(snap.get(_field) or 0)
                        except Exception:
                            snap_char_id = 0
                        if snap_char_id:
                            break
                if active_char_id == 0 or snap_char_id == 0 or active_char_id == snap_char_id:
                    try:
                        active_quick_index = int(active_row.get("quick_index"))
                    except Exception:
                        active_quick_index = None

            flash_quick_index = None
            for (_slot, _qi), _frames in list(quick_btn_flash.items()):
                if _slot == slot_label and int(_frames or 0) > 0:
                    flash_quick_index = int(_qi)
                    break

            quick_btn_areas.update(
                draw_quick_assist_footer(
                    surf,
                    panel_rect,
                    slot_label,
                    snap,
                    smallfont,
                    mx_local=mx_local,
                    my_local=my_local,
                    btn_y=btn_y,
                    get_quick_defs_fn=get_quick_assists_for_slot,
                    active_quick_index=active_quick_index,
                    flash_quick_index=flash_quick_index,
                )
            )

            surf.set_alpha(alpha)
            screen.blit(surf, (panel_rect.x, panel_rect.y))

            frame_btn_rect   = pygame.Rect(
                panel_rect.x + frame_btn_local.x,
                panel_rect.y + frame_btn_local.y,
                frame_btn_w, btn_h,
            )
            mission_btn_rect = pygame.Rect(
                panel_rect.x + mission_btn_local.x,
                panel_rect.y + mission_btn_local.y,
                mission_btn_w, btn_h,
            )
            return frame_btn_rect, mission_btn_rect

        btn_p1c1, mission_btn_p1c1 = blit_panel_with_buttons(r_p1c1, "P1-C1", a_p1c1, "P1-C1")
        btn_p2c1, mission_btn_p2c1 = blit_panel_with_buttons(r_p2c1, "P2-C1", a_p2c1, "P2-C1")

        if (not layout.get("p1_is_giant")) and ("P1-C2" in snaps):
            btn_p1c2, mission_btn_p1c2 = blit_panel_with_buttons(r_p1c2, "P1-C2", a_p1c2, "P1-C2")
        else:
            btn_p1c2        = pygame.Rect(0, 0, 0, 0)
            mission_btn_p1c2 = pygame.Rect(0, 0, 0, 0)

        if (not layout.get("p2_is_giant")) and ("P2-C2" in snaps):
            btn_p2c2, mission_btn_p2c2 = blit_panel_with_buttons(r_p2c2, "P2-C2", a_p2c2, "P2-C2")
        else:
            btn_p2c2        = pygame.Rect(0, 0, 0, 0)
            mission_btn_p2c2 = pygame.Rect(0, 0, 0, 0)

        # Bottom inspector workspace: one active tab at a time. This prevents
        # the Normals Preview from being clipped while keeping Events, Debug,
        # and Activity one click away.
        lower_keys = ("act", "events", "debug", "scan")
        lower_tops = [layout[k].y for k in lower_keys if isinstance(layout.get(k), pygame.Rect)]
        lower_top = min(lower_tops) if lower_tops else max(TOP_UI_RESERVED, int(h * 0.62))
        status_rail_h = 22
        bottom_workspace_rect = pygame.Rect(
            0,
            lower_top,
            w,
            max(60, h - status_rail_h - lower_top),
        )

        bottom_content_rect, bottom_tab_rects = draw_bottom_workspace_tabs(
            screen,
            bottom_workspace_rect,
            smallfont,
            active_bottom_tab,
            pygame.mouse.get_pos(),
        )

        debug_click_areas = {}
        debug_max_scroll = 0

        if active_bottom_tab == "scan":
            scan_rect = bottom_content_rect
            scan_surf = pygame.Surface((scan_rect.width, scan_rect.height), pygame.SRCALPHA)
            draw_scan_normals_polished(scan_surf, scan_surf.get_rect(), font, smallfont, last_scan_normals)
            if scan_anim is not None:
                t    = now - scan_anim["start"]
                dur  = scan_anim.get("dur", SCAN_SLIDE_DURATION)
                frac = max(0.0, min(1.0, t / dur)) if dur else 1.0
                y    = (scan_rect.y + scan_rect.height + 8) + (scan_rect.y - (scan_rect.y + scan_rect.height + 8)) * frac
                if frac >= 1.0:
                    scan_anim = None
            else:
                y = scan_rect.y
            scan_surf.set_alpha(255)
            screen.blit(scan_surf, (scan_rect.x, int(y)))

        elif active_bottom_tab == "events":
            draw_event_log(screen, bottom_content_rect, font, smallfont)

        elif active_bottom_tab == "debug":
            if frame_idx % DEBUG_REFRESH_EVERY == 0:
                debug_cache = merged_debug_values()
            debug_click_areas, debug_max_scroll = draw_debug_overlay(
                screen, bottom_content_rect, smallfont, debug_cache, debug_scroll_offset
            )

        elif active_bottom_tab == "activity":
            draw_activity(screen, bottom_content_rect, font, last_adv_display)

        # Write data files for subprocesses
        mission_mgr.write_overlay_data(render_snap_by_slot)
        hud_mgr.write_data(render_snap_by_slot, last_scan_normals, mission_mgr)
        hud_mgr.check_proc()

        status_parts = []
        status_parts.append("Dolphin hooked")
        status_parts.append("Overlay ON" if overlay_enabled else "Overlay OFF")
        if any(hitbox_slots.values()):
            active_hitbox_slots = ", ".join(k for k, v in hitbox_slots.items() if v)
            status_parts.append(f"Hitboxes {active_hitbox_slots}")
        else:
            status_parts.append("Hitboxes OFF")
        if mission_mgr.active_slot:
            status_parts.append(f"Mission {mission_mgr.active_slot}")

        draw_status_rail(
            screen,
            smallfont,
            text=" | ".join(status_parts),
        )

        pygame.display.flip()

        # ------------------------------------------------------------------
        # Click handling
        # ------------------------------------------------------------------
        if mouse_clicked_pos is not None:
            mx, my = mouse_clicked_pos

            if hb_btn_rect.collidepoint(mx, my):
                new_state = not any(hitbox_slots.values())
                for k in hitbox_slots:
                    hitbox_slots[k] = new_state
                _write_hitbox_filter()
                _write_master_control()
                _sync_master_overlay_state()
                mouse_clicked_pos = None
                continue

            elif ps_btn_rect.collidepoint(mx, my):
                def _get_active_chars():
                    return [s.get("name") for slot in ["P1-C1","P1-C2","P2-C1","P2-C2"]
                            for s in [render_snap_by_slot.get(slot)]
                            if s and s.get("name")]
                open_proj_scanner_window(_get_active_chars)
                mouse_clicked_pos = None
                continue

            elif as_btn_rect.collidepoint(mx, my):
                open_assist_scanner_window()
                mouse_clicked_pos = None
                continue

            elif hud_btn_rect.collidepoint(mx, my):
                overlay_enabled = not overlay_enabled
                _write_master_control()
                _sync_master_overlay_state()
                mouse_clicked_pos = None
                continue

            else:
                for slot_name, cb_rect in hb_filter_rects.items():
                    if cb_rect.collidepoint(mx, my):
                        hitbox_slots[slot_name] = not hitbox_slots[slot_name]
                        _write_hitbox_filter()
                        _write_master_control()
                        _sync_master_overlay_state()
                        break

            for _tab_key, _tab_rect in list(bottom_tab_rects.items()):
                if _tab_rect.collidepoint(mx, my):
                    active_bottom_tab = _tab_key
                    mouse_clicked_pos = None
                    break
            if mouse_clicked_pos is None:
                continue

            quick_clicked = False
            for (slot_label, quick_index), qrect in list(quick_btn_areas.items()):
                if qrect.collidepoint(mx, my):
                    snap = render_snap_by_slot.get(slot_label) or snaps.get(slot_label)
                    try:
                        ok = bool(apply_quick_assist_from_main(slot_label, quick_index, snap))
                    except Exception as e:
                        ok = False
                        print(f"[assist quick] click failed: {e!r}")
                    if ok:
                        char_id = 0
                        if isinstance(snap, dict):
                            for _field in ("id", "csv_char_id", "char_id"):
                                try:
                                    char_id = int(snap.get(_field) or 0)
                                except Exception:
                                    char_id = 0
                                if char_id:
                                    break
                        active_quick_assist_by_slot[slot_label] = {
                            "quick_index": int(quick_index),
                            "char_id": int(char_id or 0),
                        }

                        # Only one assist button may be highlighted/flashing per
                        # slot. Clear any previous flash entries for this slot so
                        # changing assists on the same character does not leave
                        # the old assist highlighted beside the new one.
                        for _flash_key in list(quick_btn_flash.keys()):
                            try:
                                if _flash_key[0] == slot_label:
                                    quick_btn_flash.pop(_flash_key, None)
                            except Exception:
                                quick_btn_flash.pop(_flash_key, None)

                        quick_btn_flash[(slot_label, int(quick_index))] = PANEL_FLASH_FRAMES
                    quick_clicked = True
                    break
            if quick_clicked:
                mouse_clicked_pos = None
                continue

            # Debug panel row -> copy address
            copied = False
            if active_bottom_tab == "debug":
                for name, (r, addr) in debug_click_areas.items():
                    if r.collidepoint(mx, my):
                        _copy_to_clipboard(f"0x{addr:08X}" if isinstance(addr, int) else str(addr))
                        copied = True
                        break

            # Character panel -> copy base
            if not copied:
                slot_panels = [
                    ("P1-C1", r_p1c1), ("P2-C1", r_p2c1),
                    ("P1-C2", r_p1c2), ("P2-C2", r_p2c2),
                ]
                for slot_label, rect in slot_panels:
                    if rect and rect.collidepoint(mx, my):
                        snap = render_snap_by_slot.get(slot_label)
                        if snap:
                            base = snap.get("base")
                            _copy_to_clipboard(f"0x{base:08X}" if isinstance(base, int) else str(base))
                        break

            # Mission mode buttons
            for slot_label, btn_rect in [
                ("P1-C1", mission_btn_p1c1), ("P2-C1", mission_btn_p2c1),
                ("P1-C2", mission_btn_p1c2), ("P2-C2", mission_btn_p2c2),
            ]:
                if btn_rect.collidepoint(mx, my):
                    mission_mgr.toggle_active_slot(slot_label)
                    break

            # Frame data buttons
            for slot_label, btn_rect in [
                ("P1-C1", btn_p1c1), ("P2-C1", btn_p2c1),
                ("P1-C2", btn_p1c2), ("P2-C2", btn_p2c2),
            ]:
                if btn_rect.collidepoint(mx, my):
                    last_scan_normals, last_scan_time = ensure_scan_now(last_scan_normals, last_scan_time)
                    if last_scan_normals:
                        open_frame_data_window(slot_label, last_scan_normals)
                    panel_btn_flash[slot_label] = PANEL_FLASH_FRAMES
                    break

            else:
                # Debug toggles / cycles
                def _toggle_u8(name: str):
                    entry = debug_click_areas.get(name)
                    if not entry:
                        return False
                    r, addr = entry
                    if not r.collidepoint(mx, my):
                        return False
                    cur = rd8(addr) or 0
                    wd8(addr, 0x01 if cur == 0x00 else 0x00)
                    return True

                def _cycle_u8(name: str, mod: int):
                    entry = debug_click_areas.get(name)
                    if not entry:
                        return False
                    r, addr = entry
                    if not r.collidepoint(mx, my):
                        return False
                    cur = rd8(addr) or 0
                    wd8(addr, (cur + 1) % mod)
                    return True

                _toggle_u8("PauseOverlay")

                entry = debug_click_areas.get("TrPause")
                if entry:
                    r, addr_tr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr_tr) or 0
                        wd8(addr_tr, 0x01 if cur == 0x00 else 0x00)

                entry = debug_click_areas.get("P2Pause")
                if entry:
                    r, addr_p2 = entry
                    if r.collidepoint(mx, my):
                        cur_p2   = rd8(addr_p2) or 0
                        entry_tr = debug_click_areas.get("TrPause")
                        addr_tr  = entry_tr[1] if entry_tr else None
                        if cur_p2 == 0x00:
                            if addr_tr is not None: wd8(addr_tr, 0x01)
                            wd8(addr_p2, 0x01)
                        else:
                            if addr_tr is not None: wd8(addr_tr, 0x00)
                            wd8(addr_p2, 0x00)

                _cycle_u8("DummyMeter", 3)
                _cycle_u8("CpuAction",  6)
                _cycle_u8("CpuGuard",   3)
                _toggle_u8("CpuPushblock")
                _toggle_u8("CameraLock")
                _toggle_u8("CpuThrowTech")
                _cycle_u8("P1Meter", 3)
                _toggle_u8("P1Life")
                _toggle_u8("FreeBaroque")
                _toggle_u8("Orientation")

                entry = debug_click_areas.get("SuperBG")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr)
                        wd8(addr, 0x01 if cur == 0x04 else 0x04)

                entry = debug_click_areas.get("BaroquePct")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        wd8(addr, (cur + 1) if cur < 0x0A else 0x00)

                _toggle_u8("AttackData")
                _toggle_u8("InputDisplay")

                entry = debug_click_areas.get("CpuDifficulty")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur   = rd8(addr) or 0
                        level = ((cur // 0x20) % 8 + 1) % 8
                        wd8(addr, level * 0x20)

                entry = debug_click_areas.get("DamageOutput")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        wd8(addr, (cur + 1) & 0x03)

                entry = debug_click_areas.get("HypeTrigger")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        orig = rd8(addr)
                        if orig is None or orig == 0:
                            orig = 0x45
                        wd8(addr, 0x40)
                        hype_restore_addr = addr
                        hype_restore_orig = orig
                        hype_restore_ts   = now + 0.5

                entry = debug_click_areas.get("ComboStore[1]")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        wd8(addr, 0x41)

                _toggle_u8("ComboCountOnly")

                entry = debug_click_areas.get("SpecialPopup")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr)
                        if cur is None or cur == 0:
                            cur = 0x45
                        special_restore_orig = cur
                        wd8(addr, 0x40)
                        special_restore_addr = addr
                        special_restore_ts   = now + 0.5

        # Momentary write restores
        if hype_restore_addr is not None and now >= hype_restore_ts:
            try:
                wd8(hype_restore_addr, hype_restore_orig)
            except Exception:
                pass
            hype_restore_addr = None

        if special_restore_addr is not None and now >= special_restore_ts:
            try:
                wd8(special_restore_addr, special_restore_orig)
            except Exception:
                pass
            special_restore_addr = None

        # Button flash countdown
        for k in panel_btn_flash:
            if panel_btn_flash[k] > 0:
                panel_btn_flash[k] -= 1

        for k in list(quick_btn_flash.keys()):
            try:
                if int(quick_btn_flash.get(k, 0) or 0) > 0:
                    quick_btn_flash[k] = int(quick_btn_flash.get(k, 0) or 0) - 1
                if int(quick_btn_flash.get(k, 0) or 0) <= 0:
                    quick_btn_flash.pop(k, None)
            except Exception:
                quick_btn_flash.pop(k, None)

        # Normals rescan triggers
        if HAVE_SCAN_NORMALS and need_rescan_normals and scan_worker:
            scan_worker.request()
            need_rescan_normals = False

        if HAVE_SCAN_NORMALS and manual_scan_requested:
            if scan_worker:
                scan_worker.request()
            else:
                try:
                    last_scan_normals = scan_normals_all.scan_once()
                    last_scan_time    = time.time()
                except Exception as e:
                    print("manual scan failed:", e)
            manual_scan_requested = False

        # CSV flush
        if pending_hits and (frame_idx % 30 == 0):
            newcsv = not os.path.exists(HIT_CSV)
            with open(HIT_CSV, "a", newline="", encoding="utf-8") as fh:
                wcsv = csv.writer(fh)
                if newcsv:
                    wcsv.writerow([
                        "t",
                        "victim_label", "victim_char", "dmg",
                        "hp_before", "hp_after",
                        "attacker_label", "attacker_char", "attacker_char_id",
                        "attacker_id_dec", "attacker_id_hex", "attacker_move",
                        "dist2",
                        "atk_flag062", "atk_flag063",
                        "vic_flag062", "vic_flag063",
                        "atk_ctrl", "vic_ctrl",
                    ])
            pending_hits.clear()

        clock.tick(TARGET_FPS)
        frame_idx += 1

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------
    mission_mgr.restore_debug_overrides()

    if master_overlay_proc and master_overlay_proc.poll() is None:
        try:
            master_overlay_proc.terminate()
        except Exception:
            pass

    pygame.quit()


def main():
    legacy_main()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("\n[main crash]")
        print(f"error={e!r}")
        traceback.print_exc()
        try:
            input("\nCrash detected. Press Enter to close...")
        except EOFError:
            pass
        raise