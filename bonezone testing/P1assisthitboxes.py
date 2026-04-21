#!/usr/bin/env python3
"""
hitbox_overlay.py
Transparent always-on-top hitbox overlay for TvC.
Reads live hitbox data from Dolphin memory.

Requires: pip install pygame pywin32
"""

import pygame
import struct
import math
import win32gui
import win32con

from dolphin_io import hook, rd32

# ----------------------------
# Hitbox layout
# ----------------------------
P1_BASE = 0x9246BE80
P2_BASE = 0x92B6BE80  # SLOT 2 BASE (change if needed)

BLOCKS = [0x64, 0xA4, 0xE4]
OFF_X  = 0x00
OFF_Y  = 0x04
OFF_R  = 0x18

# ----------------------------
# Camera layout (MEM1)
# ----------------------------
CAM_BASE = 0x8053CB20
CAM_OFF_X = 0x00
CAM_OFF_Y = 0x04
CAM_OFF_Z = 0x08
CAM_OFF_W = 0x0C

USE_LIVE_CAMERA = True

# ----------------------------
# Display
# ----------------------------
WINDOW_W = 1280
WINDOW_H = 720

PPU  = 160.0
ZOOM = 1.0

CAM_X = 0.0
CAM_Y = 0.0

DEV_MODE = True

CAM_CENTER_Y_OFFSET_PX = 80

COL_HIT_P1 = [
    (255, 60, 60),
    (255, 140, 0),
    (255, 220, 0),
]

COL_HIT_P2 = [
    (180, 60, 255),
    (60, 200, 255),
    (60, 255, 180),
]

COL_CROSS = (255, 255, 255)
COL_LABEL = (255, 255, 255)
COL_DIM   = (120, 120, 120)
COL_BG    = (0, 0, 0)

MAX_RADIUS_UNITS = 8.0


# ----------------------------
# Dolphin window helpers
# ----------------------------
def find_dolphin_hwnd():
    found = []

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if "dolphin" in title.lower():
            found.append(hwnd)

    win32gui.EnumWindows(cb, None)
    return found[0] if found else None


def get_client_rect_screen(hwnd):
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    w = right - left
    h = bottom - top
    x, y = win32gui.ClientToScreen(hwnd, (0, 0))
    return x, y, w, h


def is_window_minimized(hwnd):
    return win32gui.IsIconic(hwnd)


def apply_overlay_style(hwnd, click_through: bool):
    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    ex |= (win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST)

    if DEV_MODE:
        ex &= ~win32con.WS_EX_TOOLWINDOW
    else:
        ex |= win32con.WS_EX_TOOLWINDOW

    if click_through:
        ex |= win32con.WS_EX_TRANSPARENT
    else:
        ex &= ~win32con.WS_EX_TRANSPARENT

    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)
    win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 0, win32con.LWA_COLORKEY)

    flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE
    if click_through:
        flags |= win32con.SWP_NOACTIVATE

    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)


def attach_overlay_to(hwnd_overlay, hwnd_target, last_rect):
    if not hwnd_target or not win32gui.IsWindow(hwnd_target):
        return last_rect

    if is_window_minimized(hwnd_target):
        win32gui.ShowWindow(hwnd_overlay, win32con.SW_HIDE)
        return last_rect

    win32gui.ShowWindow(hwnd_overlay, win32con.SW_SHOW)

    x, y, w, h = get_client_rect_screen(hwnd_target)
    if w <= 0 or h <= 0:
        return last_rect

    rect = (x, y, w, h)
    if rect != last_rect:
        win32gui.SetWindowPos(
            hwnd_overlay,
            win32con.HWND_TOPMOST,
            x, y, w, h,
            win32con.SWP_NOACTIVATE
        )
    return rect


# ----------------------------
# Memory helpers
# ----------------------------
def rf(addr):
    v = rd32(addr)
    if v is None:
        return 0.0
    try:
        f = struct.unpack(">f", struct.pack(">I", v))[0]
        return f if math.isfinite(f) else 0.0
    except Exception:
        return 0.0


def read_hitboxes(base):
    boxes = []
    for b in BLOCKS:
        x = rf(base + b + OFF_X)
        y = rf(base + b + OFF_Y)
        r = rf(base + b + OFF_R)
        boxes.append((x, y, r))
    return boxes


def read_camera_pos():
    x = rf(CAM_BASE + CAM_OFF_X)
    y = rf(CAM_BASE + CAM_OFF_Y)
    z = rf(CAM_BASE + CAM_OFF_Z)
    w = rf(CAM_BASE + CAM_OFF_W)
    return x, y, z, w


# ----------------------------
# World -> screen
# ----------------------------
def world_to_screen(x, y, cx, cy):
    sx = cx + int((x - CAM_X) * PPU * ZOOM)
    sy = cy - int((y - CAM_Y) * PPU * ZOOM)
    return sx, sy


def draw_hitbox(surf, x, y, r, color, label, font, cx, cy):
    if r <= 0.001 or not math.isfinite(r):
        return

    r = min(r, MAX_RADIUS_UNITS)

    sx, sy = world_to_screen(x, y, cx, cy)
    rpx = max(2, int(r * PPU * ZOOM))

    hit_surf = pygame.Surface((rpx * 2 + 2, rpx * 2 + 2), pygame.SRCALPHA)
    pygame.draw.circle(hit_surf, (*color, 60), (rpx + 1, rpx + 1), rpx)
    pygame.draw.circle(hit_surf, (*color, 220), (rpx + 1, rpx + 1), rpx, 2)
    surf.blit(hit_surf, (sx - rpx - 1, sy - rpx - 1))

    cs = 5
    pygame.draw.line(surf, COL_CROSS, (sx - cs, sy), (sx + cs, sy), 1)
    pygame.draw.line(surf, COL_CROSS, (sx, sy - cs), (sx, sy + cs), 1)

    if label:
        txt = font.render(f"{label} r={r:.2f}", True, color)
        surf.blit(txt, (sx + rpx + 4, sy - 8))


# ----------------------------
# Main
# ----------------------------
def main():
    hook()

    dolphin_hwnd = find_dolphin_hwnd()
    if not dolphin_hwnd:
        print("Dolphin not found.")
        return

    global WINDOW_W, WINDOW_H
    _, _, WINDOW_W, WINDOW_H = get_client_rect_screen(dolphin_hwnd)

    pygame.init()
    font_small = pygame.font.SysFont("consolas", 11)
    font_hud   = pygame.font.SysFont("consolas", 13, bold=True)

    flags = pygame.SRCALPHA if DEV_MODE else (pygame.NOFRAME | pygame.SRCALPHA)
    screen = pygame.display.set_mode((WINDOW_W, WINDOW_H), flags)

    hwnd = pygame.display.get_wm_info()["window"]
    apply_overlay_style(hwnd, click_through=False)

    last_rect = None
    last_rect = attach_overlay_to(hwnd, dolphin_hwnd, last_rect)

    clock = pygame.time.Clock()

    cx = WINDOW_W // 2
    cy = WINDOW_H // 2 + CAM_CENTER_Y_OFFSET_PX

    global CAM_X, CAM_Y

    running = True
    while running:

        last_rect = attach_overlay_to(hwnd, dolphin_hwnd, last_rect)

        camx, camy, camz, camw = read_camera_pos()
        if USE_LIVE_CAMERA:
            if math.isfinite(camx) and math.isfinite(camy):
                CAM_X = camx
                CAM_Y = camy

        p1_boxes = read_hitboxes(P1_BASE)
        p2_boxes = read_hitboxes(P2_BASE)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False

        screen.fill(COL_BG)

        active_p1 = 0
        active_p2 = 0

        for i, (x, y, r) in enumerate(p1_boxes):
            if r > 0.001:
                active_p1 += 1
                draw_hitbox(screen, x, y, r, COL_HIT_P1[i % 3], f"P1[{i}]", font_small, cx, cy)

        for i, (x, y, r) in enumerate(p2_boxes):
            if r > 0.001:
                active_p2 += 1
                draw_hitbox(screen, x, y, r, COL_HIT_P2[i % 3], f"P2[{i}]", font_small, cx, cy)

        hud = font_hud.render(
            f"P1 active={active_p1} | P2 active={active_p2}",
            True,
            COL_DIM
        )
        screen.blit(hud, (8, 8))

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


if __name__ == "__main__":
    main()