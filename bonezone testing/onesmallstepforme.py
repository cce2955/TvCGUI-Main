import pygame
import struct
import time
import win32gui
import win32con
from dolphin_io import hook, rd32

BASE = 0x9246BE80
BLOCKS = [0x64, 0xA4, 0xE4]

WINDOW_W = 1280
WINDOW_H = 720
PIXELS_PER_UNIT = 160  # adjust

def rf(addr):
    v = rd32(addr)
    if v is None:
        return 0.0
    return struct.unpack(">f", struct.pack(">I", v))[0]

def read_hitboxes():
    boxes = []
    for b in BLOCKS:
        x = rf(BASE + b + 0x00)
        y = rf(BASE + b + 0x04)
        r = rf(BASE + b + 0x18)
        boxes.append((x, y, r))
    return boxes

def make_window_alpha(hwnd):
    styles = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    win32gui.SetWindowLong(
        hwnd,
        win32con.GWL_EXSTYLE,
        styles | win32con.WS_EX_LAYERED
    )
    win32gui.SetLayeredWindowAttributes(hwnd, 0, 200, win32con.LWA_ALPHA)

def world_to_screen(x, y):
    sx = WINDOW_W // 2 + int(x * PIXELS_PER_UNIT)
    sy = WINDOW_H // 2 - int(y * PIXELS_PER_UNIT)
    return sx, sy

def main():
    hook()

    pygame.init()
    screen = pygame.display.set_mode((WINDOW_W, WINDOW_H))
    pygame.display.set_caption("????")

    hwnd = pygame.display.get_wm_info()["window"]
    make_window_alpha(hwnd)

    clock = pygame.time.Clock()

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

        screen.fill((0, 0, 0))  # will be semi-transparent via alpha

        boxes = read_hitboxes()

        for i, (x, y, r) in enumerate(boxes):
            if r <= 0:
                continue

            sx, sy = world_to_screen(x, y)
            import math

            if not math.isfinite(r):
                continue

            radius_px = int(max(0.0, min(r, 5.0)) * PIXELS_PER_UNIT)

            colors = [(255, 0, 0), (0, 255, 0), (0, 128, 255)]
            pygame.draw.circle(screen, colors[i % 3], (sx, sy), radius_px, 2)

        pygame.display.update()
        clock.tick(60)

    pygame.quit()

if __name__ == "__main__":
    main()