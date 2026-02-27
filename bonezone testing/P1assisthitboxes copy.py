#!/usr/bin/env python3
"""
hitbox_overlay.py
Transparent always-on-top hitbox overlay for TvC.
Reads live hitbox data from Dolphin memory.

4-slot expanded version.
"""

import pygame
import struct
import math
import win32gui
import win32con

from dolphin_io import hook, rd32

# ----------------------------
# Hitbox layout (4 slots)
# ----------------------------
from dolphin_io import hook, rd32
from constants import (
    PTR_P1_CHAR1,
    PTR_P1_CHAR2,
    PTR_P2_CHAR1,
    PTR_P2_CHAR2,
)

def resolve_base(ptr_addr):
    base = rd32(ptr_addr)
    if base is None:
        return 0
    return base
BLOCKS = [0x64, 0xA4, 0xE4]
OFF_X  = 0x00
OFF_Y  = 0x04
OFF_R  = 0x18

# ----------------------------
# Camera layout
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

COLORS = {
    "P1": [(255,60,60),(255,140,0),(255,220,0)],
    "P2": [(180,60,255),(60,200,255),(60,255,180)],
    "P3": [(255,80,180),(255,0,120),(255,120,200)],
    "P4": [(80,255,120),(0,255,80),(120,255,200)],
}

COL_CROSS = (255,255,255)
COL_DIM   = (120,120,120)
COL_BG    = (0,0,0)

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


def apply_overlay_style(hwnd):
    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    ex |= (win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST)
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)
    win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 0, win32con.LWA_COLORKEY)
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST,0,0,0,0,win32con.SWP_NOMOVE|win32con.SWP_NOSIZE)


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
    except:
        return 0.0


def read_hitboxes(base):
    boxes = []
    for b in BLOCKS:
        x = rf(base + b + OFF_X)
        y = rf(base + b + OFF_Y)
        r = rf(base + b + OFF_R)
        boxes.append((x,y,r))
    return boxes


def read_camera_pos():
    x = rf(CAM_BASE + CAM_OFF_X)
    y = rf(CAM_BASE + CAM_OFF_Y)
    z = rf(CAM_BASE + CAM_OFF_Z)
    w = rf(CAM_BASE + CAM_OFF_W)
    return x,y,z,w


# ----------------------------
# World -> screen
# ----------------------------
def world_to_screen(x,y,cx,cy):
    sx = cx + int((x - CAM_X) * PPU * ZOOM)
    sy = cy - int((y - CAM_Y) * PPU * ZOOM)
    return sx,sy


def draw_hitbox(surf,x,y,r,color,label,font,cx,cy):
    if r <= 0.001 or not math.isfinite(r):
        return

    r = min(r, MAX_RADIUS_UNITS)

    sx,sy = world_to_screen(x,y,cx,cy)
    rpx = max(2,int(r*PPU*ZOOM))

    hit_surf = pygame.Surface((rpx*2+2,rpx*2+2),pygame.SRCALPHA)
    pygame.draw.circle(hit_surf,(*color,60),(rpx+1,rpx+1),rpx)
    pygame.draw.circle(hit_surf,(*color,220),(rpx+1,rpx+1),rpx,2)
    surf.blit(hit_surf,(sx-rpx-1,sy-rpx-1))

    if label:
        txt = font.render(f"{label} r={r:.2f}",True,color)
        surf.blit(txt,(sx+rpx+4,sy-8))


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
    _,_,WINDOW_W,WINDOW_H = get_client_rect_screen(dolphin_hwnd)

    pygame.init()
    font_small = pygame.font.SysFont("consolas",11)
    font_hud   = pygame.font.SysFont("consolas",13,bold=True)

    screen = pygame.display.set_mode((WINDOW_W,WINDOW_H),pygame.SRCALPHA)
    hwnd = pygame.display.get_wm_info()["window"]
    apply_overlay_style(hwnd)

    clock = pygame.time.Clock()

    cx = WINDOW_W//2
    cy = WINDOW_H//2 + CAM_CENTER_Y_OFFSET_PX

    global CAM_X,CAM_Y

    running = True
    while running:

        camx,camy,_,_ = read_camera_pos()
        if USE_LIVE_CAMERA:
            CAM_X = camx
            CAM_Y = camy

        bases = {
            "P1": resolve_base(PTR_P1_CHAR1),
            "P2": resolve_base(PTR_P2_CHAR1),
            "P3": resolve_base(PTR_P1_CHAR2),
            "P4": resolve_base(PTR_P2_CHAR2),
        }

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False

        screen.fill(COL_BG)

        active_counts = {}
        slots = { name: read_hitboxes(base) for name, base in bases.items() if base }
        for name, boxes in slots.items():
            active = 0
            for i,(x,y,r) in enumerate(boxes):
                if r > 0.001:
                    active += 1
                    draw_hitbox(
                        screen,
                        x,y,r,
                        COLORS[name][i % 3],
                        f"{name}[{i}]",
                        font_small,
                        cx,cy
                    )
            active_counts[name] = active

        hud_text = " | ".join([f"{k}={v}" for k,v in active_counts.items()])
        hud = font_hud.render(hud_text,True,COL_DIM)
        screen.blit(hud,(8,8))

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


if __name__ == "__main__":
    main()