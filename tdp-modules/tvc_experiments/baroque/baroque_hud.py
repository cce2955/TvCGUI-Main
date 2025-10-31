# baroque_hud.py
#
# Standalone mini-HUD to watch Baroque-related clusters for one slot.
# - Resolves fighter_base via the per-slot manager address.
# - Shows:
#     * Absolute Baroque flag bytes at fighter_base + 0xCBA8..0xCBA9
#     * Cluster A: +0x00F2..+0x00F7 (6 bytes)
#     * Cluster B: +0x01D9..+0x01DA (2 bytes)
#     * Cluster C: +0x021E..+0x0223 (6 bytes)
#
# Controls:
#   Esc / Q : quit
#   R       : force re-resolve fighter_base (if you swap characters)
#
# Example:
#   python tvc_experiments/baroque_hud.py --slot P1C1 --hz 30
#
# Requires:
#   - dolphin_memory_engine installed
#   - tvc_experiments/dolphin_io.py present with: hook(), addr_in_ram(), rbytes(), rd8(), rd32(), rdf32()
#   - constants.py defining MEM1/MEM2 ranges (already in your repo)

import argparse
import time
import pygame
import struct

import dolphin_io as dio

# Per-slot "manager" statics you already confirmed
MAN = {
    "P1C1": 0x803C9FCC,  # P1 active
    "P1C2": 0x803C9FDC,  # P1 partner
    "P2C1": 0x803C9FD4,  # P2 active
    "P2C2": 0x803C9FE4,  # P2 partner
}

# Relative offsets we want to watch (all relative to fighter_base)
OFF_BAROQUE = 0xCBA8  # two-byte window (CBA8..CBA9)
CLUSTER_A   = (0x00F2, 6)  # 6 bytes
CLUSTER_B   = (0x01D9, 2)  # 2 bytes
CLUSTER_C   = (0x021E, 6)  # 6 bytes

# A tiny helper to format bytes as "AA BB CC ..."
def fmt_bytes(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)

def resolve_fighter_base(slot: str) -> int | None:
    """
    Resolve fighter_base by reading a 32-bit pointer from the slot manager.
    Do a light sanity check (X/Y floats at +0xF0/+0xF4).
    """
    man_addr = MAN.get(slot)
    if man_addr is None:
        return None

    base = dio.rd32(man_addr)
    if base is None or not dio.addr_in_ram(base):
        return None

    # Sanity: read X/Y floats
    xf = dio.rdf32(base + 0x0F0)
    yf = dio.rdf32(base + 0x0F4)
    if xf is None or yf is None:
        # In practice this happens during loads; let caller know it's not ready
        return None
    return base

def safe_read_bytes(addr: int, n: int) -> bytes:
    b = dio.rbytes(addr, n)
    if not b or len(b) != n:
        return b""  # unified empty on failure
    return b

def main():
    ap = argparse.ArgumentParser(description="Standalone Baroque-mini-HUD watcher")
    ap.add_argument("--slot", default="P1C1", choices=list(MAN.keys()))
    ap.add_argument("--hz", type=float, default=30.0, help="polling rate (frames per second)")
    ap.add_argument("--font-size", type=int, default=18)
    ap.add_argument("--width", type=int, default=600)
    ap.add_argument("--height", type=int, default=260)
    args = ap.parse_args()

    # Hook Dolphin
    dio.hook()

    # Pygame init
    pygame.init()
    pygame.display.set_caption(f"Baroque HUD – {args.slot}")
    screen = pygame.display.set_mode((args.width, args.height))
    clock = pygame.time.Clock()
    font = pygame.font.SysFont("consolas", args.font_size)

    last_baroque = None
    fighter_base = None
    last_resolved = 0.0

    def draw_line(y, text, color=(230,230,230)):
        surf = font.render(text, True, color)
        screen.blit(surf, (10, y))
        return y + surf.get_height() + 4

    def try_resolve(force=False):
        nonlocal fighter_base, last_resolved
        now = time.time()
        if force or fighter_base is None or (now - last_resolved) > 1.0:
            fb = resolve_fighter_base(args.slot)
            if fb is not None:
                fighter_base = fb
                last_resolved = now
            # else: leave as-is; we keep trying next frame

    # Colors
    COL_BG      = (10,10,12)
    COL_PANEL   = (24,24,28)
    COL_TEXT    = (230,230,230)
    COL_GOOD    = (80,220,80)
    COL_WARN    = (255,180,0)
    COL_BAD     = (255,60,60)
    COL_MID     = (160,200,255)

    panel_rect = pygame.Rect(6, 6, args.width-12, args.height-12)

    running = True
    while running:
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif ev.key == pygame.K_r:
                    fighter_base = None  # force re-resolve next pass

        # Ensure we have a base
        try_resolve(force=False)

        screen.fill(COL_BG)
        pygame.draw.rect(screen, COL_PANEL, panel_rect, border_radius=8)
        y = 14

        title = f"[{args.slot}] fighter_base="
        if fighter_base is None:
            y = draw_line(y, f"{title} <resolving...>", COL_WARN)
            pygame.display.flip()
            clock.tick(args.hz)
            continue
        else:
            y = draw_line(y, f"{title} 0x{fighter_base:08X}", COL_MID)

        # Read clusters
        baro = safe_read_bytes(fighter_base + OFF_BAROQUE, 2)
        a    = safe_read_bytes(fighter_base + CLUSTER_A[0], CLUSTER_A[1])
        b    = safe_read_bytes(fighter_base + CLUSTER_B[0], CLUSTER_B[1])
        c    = safe_read_bytes(fighter_base + CLUSTER_C[0], CLUSTER_C[1])

        # Decide colors for baroque ready
        ready = False
        if len(baro) == 2:
            ready = not (baro[0] == 0x00 and baro[1] == 0x00)

        # Edge print to console if state changed
        if baro and last_baroque is not None and baro != last_baroque:
            prev = fmt_bytes(last_baroque)
            curr = fmt_bytes(baro)
            print(f"[EDGE] {time.strftime('%H:%M:%S')} {args.slot} BAROQUE {prev} -> {curr}")
        last_baroque = baro

        y += 6
        y = draw_line(y, f"BARO  +0x{OFF_BAROQUE:04X} (2): {fmt_bytes(baro) if baro else '<read-failed>'}",
                      COL_GOOD if ready else COL_TEXT)

        y += 4
        y = draw_line(y, f"A     +0x{CLUSTER_A[0]:04X} (6): {fmt_bytes(a) if a else '<read-failed>'}", COL_TEXT)
        y = draw_line(y, f"B     +0x{CLUSTER_B[0]:04X} (2): {fmt_bytes(b) if b else '<read-failed>'}", COL_TEXT)
        y = draw_line(y, f"C     +0x{CLUSTER_C[0]:04X} (6): {fmt_bytes(c) if c else '<read-failed>'}", COL_TEXT)

        y += 8
        y = draw_line(y, "Tips: R=re-resolve base   Esc/Q=quit", (180,180,180))

        pygame.display.flip()
        clock.tick(args.hz)

    pygame.quit()

if __name__ == "__main__":
    main()
