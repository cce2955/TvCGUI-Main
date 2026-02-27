#!/usr/bin/env python3
"""
hitbox_overlay.py
Transparent always-on-top hitbox overlay for TvC.
Reads live hitbox data from Dolphin memory.

Requires: pip install pygame pywin32
"""

import pygame
import struct
import time
import math
import ctypes
import win32gui
import win32con
import win32api
import win32process
import subprocess

from dolphin_io import hook, rd32

# ── Hitbox layout ─────────────────────────────────────────────────────────────
# Found at runtime: BASE + BLOCK + offsets
P1_BASE   = 0x9246BE80
P2_BASE   = None          # set once found

BLOCKS    = [0x64, 0xA4, 0xE4]   # up to 3 hitbox slots per character

OFF_X     = 0x00
OFF_Y     = 0x04
OFF_R     = 0x18

# ── Display ───────────────────────────────────────────────────────────────────
WINDOW_W  = 1280
WINDOW_H  = 720
PPU       = 160            # pixels per game unit — tune this

# Colors
COL_HIT   = [(255,  60,  60),   # slot 0 — red
             (255, 140,   0),   # slot 1 — orange
             (255, 220,   0)]   # slot 2 — yellow

COL_HURT  = [(60, 180, 255),    # hurtbox blue (future)
             (60, 255, 180),
             (140, 60, 255)]

COL_CROSS = (255, 255, 255)
COL_LABEL = (255, 255, 255)
COL_DIM   = (120, 120, 120)
COL_BG    = (0, 0, 0)           # key color for transparency

# Max sane radius in game units (clip anything bigger)
MAX_RADIUS_UNITS = 8.0

# ── Helpers ───────────────────────────────────────────────────────────────────
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

def world_to_screen(x, y, cx, cy):
    sx = cx + int(x * PPU)
    sy = cy - int(y * PPU)   # Y flipped (up = positive in game)
    return sx, sy

def draw_hitbox(surf, x, y, r, color, label, font, cx, cy):
    if r <= 0.001 or not math.isfinite(r):
        return
    r = min(r, MAX_RADIUS_UNITS)
    sx, sy = world_to_screen(x, y, cx, cy)
    rpx = max(2, int(r * PPU))

    # Filled circle at low alpha via surface
    hit_surf = pygame.Surface((rpx*2+2, rpx*2+2), pygame.SRCALPHA)
    pygame.draw.circle(hit_surf, (*color, 60), (rpx+1, rpx+1), rpx)
    pygame.draw.circle(hit_surf, (*color, 220), (rpx+1, rpx+1), rpx, 2)
    surf.blit(hit_surf, (sx - rpx - 1, sy - rpx - 1))

    # Crosshair at center
    cs = 5
    pygame.draw.line(surf, COL_CROSS, (sx-cs, sy), (sx+cs, sy), 1)
    pygame.draw.line(surf, COL_CROSS, (sx, sy-cs), (sx, sy+cs), 1)

    # Label
    txt = font.render(f"{label} ({x:.2f},{y:.2f}) r={r:.2f}", True, color)
    surf.blit(txt, (sx + rpx + 4, sy - 8))

def draw_grid(surf, cx, cy, font):
    """Light reference grid."""
    col = (40, 40, 40)
    # vertical lines every 1 game unit
    for xu in range(-8, 9):
        px = cx + xu * PPU
        pygame.draw.line(surf, col, (px, 0), (px, WINDOW_H))
        if xu != 0:
            t = font.render(str(xu), True, (50,50,50))
            surf.blit(t, (px+2, cy+2))
    # horizontal lines every 1 game unit
    for yu in range(-4, 8):
        py = cy - yu * PPU
        pygame.draw.line(surf, col, (0, py), (WINDOW_W, py))
        if yu != 0:
            t = font.render(str(yu), True, (50,50,50))
            surf.blit(t, (cx+2, py+2))
    # axes
    pygame.draw.line(surf, (80,80,80), (cx, 0), (cx, WINDOW_H), 1)
    pygame.draw.line(surf, (80,80,80), (0, cy), (WINDOW_W, cy), 1)

def find_dolphin_hwnd():
    result = []
    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if 'dolphin' in title.lower():
                result.append(hwnd)
    win32gui.EnumWindows(cb, None)
    return result[0] if result else None

def set_overlay_style(hwnd):
    """Make window layered + always on top, black = transparent."""
    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE,
                           ex | win32con.WS_EX_LAYERED | win32con.WS_EX_TOPMOST)
    # Black (0,0,0) pixels become fully transparent
    win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 0, win32con.LWA_COLORKEY)
    # Also set always-on-top via SetWindowPos
    win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                          win32con.SWP_NOMOVE | win32con.SWP_NOSIZE)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Connecting to Dolphin...")
    hook()
    print("Hooked.")

    pygame.init()
    font_small = pygame.font.SysFont("consolas", 11)
    font_hud   = pygame.font.SysFont("consolas", 13, bold=True)

    screen = pygame.display.set_mode((WINDOW_W, WINDOW_H),
                                     pygame.NOFRAME | pygame.SRCALPHA)
    pygame.display.set_caption("TvC Hitbox Overlay")

    hwnd = pygame.display.get_wm_info()["window"]
    set_overlay_style(hwnd)

    clock = pygame.time.Clock()

    # Center of world coords on screen
    # Tune cx if characters aren't centered
    cx = WINDOW_W // 2
    cy = WINDOW_H // 2 + 80   # push ground level down a bit

    show_grid   = True
    show_labels = True
    ppu_step    = 10
    global PPU

    print("Controls:")
    print("  G     — toggle grid")
    print("  L     — toggle labels")
    print("  +/-   — zoom in/out (pixels per unit)")
    print("  Arrow — nudge world origin")
    print("  ESC   — quit")

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_g:
                    show_grid = not show_grid
                elif event.key == pygame.K_l:
                    show_labels = not show_labels
                elif event.key in (pygame.K_EQUALS, pygame.K_PLUS):
                    PPU = min(PPU + ppu_step, 400)
                elif event.key == pygame.K_MINUS:
                    PPU = max(PPU - ppu_step, 20)
                elif event.key == pygame.K_LEFT:
                    cx -= 10
                elif event.key == pygame.K_RIGHT:
                    cx += 10
                elif event.key == pygame.K_UP:
                    cy -= 10
                elif event.key == pygame.K_DOWN:
                    cy += 10

        # ── Draw ──────────────────────────────────────────────────────────────
        screen.fill(COL_BG)   # black = transparent via colorkey

        if show_grid:
            draw_grid(screen, cx, cy, font_small)

        # P1 hitboxes
        p1_boxes = read_hitboxes(P1_BASE)
        active_count = 0
        for i, (x, y, r) in enumerate(p1_boxes):
            if r > 0.001 and math.isfinite(r):
                active_count += 1
                label = f"P1[{i}]" if show_labels else ""
                draw_hitbox(screen, x, y, r, COL_HIT[i % len(COL_HIT)],
                            label, font_small, cx, cy)

        # P2 hitboxes (when found)
        if P2_BASE:
            p2_boxes = read_hitboxes(P2_BASE)
            for i, (x, y, r) in enumerate(p2_boxes):
                if r > 0.001 and math.isfinite(r):
                    label = f"P2[{i}]" if show_labels else ""
                    draw_hitbox(screen, x, y, r, COL_HURT[i % len(COL_HURT)],
                                label, font_small, cx, cy)

        # ── HUD ───────────────────────────────────────────────────────────────
        hud_y = 8
        def hud(text, color=COL_LABEL):
            nonlocal hud_y
            t = font_hud.render(text, True, color)
            screen.blit(t, (8, hud_y))
            hud_y += 16

        hud(f"PPU={PPU}  origin=({cx},{cy})  active={active_count}",
            COL_DIM)

        for i, (x, y, r) in enumerate(p1_boxes):
            if r > 0.001 and math.isfinite(r):
                hud(f"  P1[{i}]  x={x:+.3f}  y={y:.3f}  r={r:.3f}",
                    COL_HIT[i % len(COL_HIT)])

        hud("G=grid  L=labels  +/-=zoom  arrows=pan  ESC=quit", COL_DIM)

        pygame.display.flip()
        clock.tick(60)

    pygame.quit()
    print("Done.")

if __name__ == "__main__":
    main()