#!/usr/bin/env python3
"""
master_overlay.py
-----------------
Single Dolphin-parented transparent overlay window that will eventually host:
- HUD drawing
- hitbox drawing
- projectile drawing
- debug overlays

For now, this is the master shell only:
- one pygame window
- one Dolphin sync loop
- one event loop
- one flip per frame
- simple toggles for HUD / hitboxes
- optional control file so another process can toggle behavior later

This file is intentionally the first step only.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Optional, Protocol

import pygame
import win32con
import win32gui


TARGET_FPS = 60
COLORKEY = (0, 0, 0)

BASE_W = 1280
BASE_H = 720

MASTER_CONTROL_FILE = "master_overlay_control.json"
MISSION_MODE_FILE = "mission_mode_state.json"
MISSION_OVERLAY_FILE = "mission_overlay_data.json"
MISSION_SELECT_FILE = "mission_select_command.json"
CRASH_LOG_FILE = "master_overlay_crash.log"

def pause_on_error(context: str, exc: BaseException) -> None:
    print(f"\n[{context}]")
    print(f"error={exc!r}")
    traceback.print_exc()
    try:
        with open(CRASH_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"\n[{context}]\n")
            f.write(f"error={exc!r}\n")
            traceback.print_exc(file=f)
            f.write("\n")
    except Exception:
        pass

    try:
        input("\nCrash detected. Press Enter to close...")
    except EOFError:
        pass


def set_dpi_aware() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def find_dolphin_hwnd() -> Optional[int]:
    candidates: list[tuple[int, int, str]] = []

    def score_title(title: str) -> int:
        tl = title.lower()
        if "dolphin" not in tl:
            return -10_000

        score = 0

        if "|" in title:
            score += 50
            score += min(30, title.count("|") * 5)

        for token in ("jit", "jit64", "opengl", "vulkan", "d3d", "direct3d", "hle"):
            if token in tl:
                score += 20

        if "(" in title and ")" in title:
            score += 30

        for bad in (
            "memory",
            "watch",
            "log",
            "breakpoint",
            "register",
            "disassembly",
            "config",
            "settings",
        ):
            if bad in tl:
                score -= 25

        if title.count("|") >= 3:
            score += 20

        return score

    def cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        title = win32gui.GetWindowText(hwnd) or ""
        if not title:
            return
        if "dolphin" not in title.lower():
            return
        candidates.append((score_title(title), hwnd, title))

    win32gui.EnumWindows(cb, None)

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def get_client_screen_rect(hwnd: int) -> tuple[int, int, int, int]:
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    tl = win32gui.ClientToScreen(hwnd, (left, top))
    br = win32gui.ClientToScreen(hwnd, (right, bottom))
    return tl[0], tl[1], br[0] - tl[0], br[1] - tl[1]


def apply_overlay_style(hwnd: int) -> None:
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    style &= ~(
        win32con.WS_CAPTION
        | win32con.WS_THICKFRAME
        | win32con.WS_MINIMIZE
        | win32con.WS_MAXIMIZE
        | win32con.WS_SYSMENU
    )
    style |= win32con.WS_POPUP
    win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)

    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    ex |= win32con.WS_EX_LAYERED
    ex &= ~win32con.WS_EX_TOPMOST
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)

    win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 0, win32con.LWA_COLORKEY)
    win32gui.SetWindowPos(
        hwnd,
        win32con.HWND_NOTOPMOST,
        0,
        0,
        0,
        0,
        win32con.SWP_FRAMECHANGED
        | win32con.SWP_NOMOVE
        | win32con.SWP_NOSIZE
        | win32con.SWP_NOACTIVATE,
    )


def sync_overlay_to_dolphin(dolphin_hwnd: int, overlay_hwnd: int) -> tuple[int, int]:
    x, y, w, h = get_client_screen_rect(dolphin_hwnd)
    win32gui.SetWindowPos(
        overlay_hwnd,
        win32con.HWND_NOTOPMOST,
        x,
        y,
        w,
        h,
        win32con.SWP_NOACTIVATE,
    )
    return w, h


@dataclass
class MasterControl:
    show_hud: bool = True
    show_hitboxes: bool = True
    show_debug: bool = False


class Renderer(Protocol):
    def on_resize(self, w: int, h: int) -> None:
        ...

    def update(self, dt: float, control: MasterControl) -> None:
        ...

    def draw(self, screen: pygame.Surface, control: MasterControl) -> None:
        ...


class NullHudRenderer:
    """
    Placeholder HUD renderer.
    This will get replaced by the converted HUD module later.
    """

    def __init__(self) -> None:
        self.font: Optional[pygame.font.Font] = None
        self.smallfont: Optional[pygame.font.Font] = None
        self.w = BASE_W
        self.h = BASE_H

    def on_resize(self, w: int, h: int) -> None:
        self.w = w
        self.h = h
        scale = min(w / BASE_W, h / BASE_H)
        font_size = max(10, int(16 * scale))
        small_size = max(8, int(13 * scale))

        try:
            self.font = pygame.font.SysFont("consolas", font_size, bold=True)
        except Exception:
            self.font = pygame.font.Font(None, font_size)

        try:
            self.smallfont = pygame.font.SysFont("consolas", small_size)
        except Exception:
            self.smallfont = pygame.font.Font(None, small_size)

    def update(self, dt: float, control: MasterControl) -> None:
        return

    def draw(self, screen: pygame.Surface, control: MasterControl) -> None:
        if not control.show_hud:
            return
        if self.font is None or self.smallfont is None:
            return

        label = self.font.render("MASTER HUD SLOT", True, (220, 220, 220))
        sub = self.smallfont.render("HUD renderer not wired yet", True, (150, 150, 150))

        x = 24
        y = max(50, int(screen.get_height() * 0.22))

        bg_w = max(label.get_width(), sub.get_width()) + 16
        bg_h = label.get_height() + sub.get_height() + 14

        bg = pygame.Surface((bg_w, bg_h), pygame.SRCALPHA)
        bg.fill((18, 18, 18, 180))
        screen.blit(bg, (x - 8, y - 6))

        screen.blit(label, (x, y))
        screen.blit(sub, (x, y + label.get_height() + 4))


class NullHitboxRenderer:
    """
    Placeholder hitbox renderer.
    This will get replaced by the converted hitbox module later.
    """

    def __init__(self) -> None:
        self.w = BASE_W
        self.h = BASE_H
        self.phase = 0.0

    def on_resize(self, w: int, h: int) -> None:
        self.w = w
        self.h = h

    def update(self, dt: float, control: MasterControl) -> None:
        self.phase += dt * 2.0

    def draw(self, screen: pygame.Surface, control: MasterControl) -> None:
        if not control.show_hitboxes:
            return

        cx = screen.get_width() // 2
        cy = screen.get_height() // 2
        pulse = 40 + int(10 * (0.5 + 0.5 * __import__("math").sin(self.phase)))
        r = max(20, pulse)

        pygame.draw.circle(screen, (255, 120, 120, 100), (cx, cy), r, 2)
        pygame.draw.circle(screen, (120, 180, 255, 100), (cx + 80, cy - 20), max(12, r - 18), 2)
        pygame.draw.line(screen, (200, 200, 200, 120), (cx - 10, cy), (cx + 10, cy), 1)
        pygame.draw.line(screen, (200, 200, 200, 120), (cx, cy - 10), (cx, cy + 10), 1)


class MasterOverlay:
    def __init__(self) -> None:
        self.dolphin_hwnd: Optional[int] = None
        self.overlay_hwnd: Optional[int] = None
        self.screen: Optional[pygame.Surface] = None
        self.clock: Optional[pygame.time.Clock] = None

        self.w = BASE_W
        self.h = BASE_H

        self.running = True
        self.control = MasterControl()

        self.font: Optional[pygame.font.Font] = None
        self.smallfont: Optional[pygame.font.Font] = None

        self._last_control_mtime = 0.0
        
        self.mission_active = False
        self.mission_slot: Optional[str] = None
        self._last_mission_mtime = 0.0
        self._last_mission_overlay_mtime = 0.0
        self.mission_overlay_data: dict = {}
        self.mission_click_rects: list[tuple[pygame.Rect, Optional[str]]] = []
        self.mission_panel_rect: Optional[pygame.Rect] = None
        self.mission_toggle_rect: Optional[pygame.Rect] = None
        self.mission_show_all: bool = False

        self.hud_renderer: Renderer = NullHudRenderer()
        self.hitbox_renderer: Renderer = NullHitboxRenderer()

        # Mission step animation state
        # step_anim[idx] = t in [0.0, 1.0]  (1.0 = fully active/metallic, 0.0 = dark/done)
        self.step_anim: dict[int, float] = {}
        self._prev_current_step: int = -1
        self._prev_completed_count: int = 0

        # Celebration state
        self._celebrate_active: bool = False
        self._celebrate_phase: float = 0.0      # total elapsed since trigger
        self._celebrate_particles: list = []
        self._prev_mission_complete: bool = False
        self._last_mission_id_seen: str = ""
        self._mission_hold_frames: int = 0
        self._mission_hold_data: dict = {}
        self._mission_hold_duration_frames: int = 120
        self._completion_consumed_mission_id: str = ""
        self._completion_block_mission_id: str = ""
        self._completion_block_frames: int = 0
        

    def init(self) -> None:
        set_dpi_aware()

        # Hook Dolphin memory before anything tries to read it
        from dolphin_io import hook
        hook()

        self.dolphin_hwnd = find_dolphin_hwnd()

        self.dolphin_hwnd = find_dolphin_hwnd()
        if not self.dolphin_hwnd:
            raise RuntimeError("Dolphin window not found.")

        pygame.init()

        try:
            from hud_overlay import HudRenderer
            self.hud_renderer = HudRenderer()
            print("[master] hud renderer loaded")
        except Exception:
            print("[master] failed to import hud renderer")
            traceback.print_exc()
            self.hud_renderer = NullHudRenderer()

        try:
            from hitboxesscaling import HitboxRenderer
            self.hitbox_renderer = HitboxRenderer()
            print("[master] hitbox renderer loaded")
        except Exception:
            print("[master] failed to import hitbox renderer")
            traceback.print_exc()
            self.hitbox_renderer = NullHitboxRenderer()

        self.screen = pygame.display.set_mode((self.w, self.h), pygame.SRCALPHA)
        pygame.display.set_caption("TvC Master Overlay")

        icon_path = os.path.join("assets", "portraits", "Placeholder.png")
        if not os.path.exists(icon_path):
            icon_path = os.path.join("assets", "icon.png")
        if os.path.exists(icon_path):
            try:
                icon = pygame.image.load(icon_path).convert_alpha()
                pygame.display.set_icon(icon)
            except Exception:
                pass

        self.overlay_hwnd = pygame.display.get_wm_info()["window"]
        apply_overlay_style(self.overlay_hwnd)
        win32gui.SetWindowLong(self.overlay_hwnd, win32con.GWL_HWNDPARENT, self.dolphin_hwnd)

        self.clock = pygame.time.Clock()

        self._refresh_fonts()
        self.hud_renderer.on_resize(self.w, self.h)
        self.hitbox_renderer.on_resize(self.w, self.h)

        self._write_default_control_file()

    def _refresh_fonts(self) -> None:
        scale = min(self.w / BASE_W, self.h / BASE_H)
        font_size = max(10, int(14 * scale))
        small_size = max(8, int(12 * scale))

        try:
            self.font = pygame.font.SysFont("consolas", font_size, bold=True)
        except Exception:
            self.font = pygame.font.Font(None, font_size)

        try:
            self.smallfont = pygame.font.SysFont("consolas", small_size)
        except Exception:
            self.smallfont = pygame.font.Font(None, small_size)

    def _write_default_control_file(self) -> None:
        if os.path.exists(MASTER_CONTROL_FILE):
            return
        self._write_control_file()

    def _write_control_file(self) -> None:
        payload = {
            "show_hud": self.control.show_hud,
            "show_hitboxes": self.control.show_hitboxes,
            "show_debug": self.control.show_debug,
        }
        try:
            with open(MASTER_CONTROL_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    def _read_control_file(self) -> None:
        try:
            mt = os.path.getmtime(MASTER_CONTROL_FILE)
            if mt == self._last_control_mtime:
                return
            self._last_control_mtime = mt

            with open(MASTER_CONTROL_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.control.show_hud = bool(data.get("show_hud", True))
            self.control.show_hitboxes = bool(data.get("show_hitboxes", True))
            self.control.show_debug = bool(data.get("show_debug", False))
        except Exception:
            pass
    def _read_control_file(self) -> None:
            try:
                mt = os.path.getmtime(MASTER_CONTROL_FILE)
                if mt == self._last_control_mtime:
                    return
                self._last_control_mtime = mt

                with open(MASTER_CONTROL_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)

                self.control.show_hud = bool(data.get("show_hud", True))
                self.control.show_hitboxes = bool(data.get("show_hitboxes", True))
                self.control.show_debug = bool(data.get("show_debug", False))
            except Exception:
                pass

    def _read_mission_mode_file(self) -> None:
        try:
            mt = os.path.getmtime(MISSION_MODE_FILE)
            if mt == self._last_mission_mtime:
                return
            self._last_mission_mtime = mt

            with open(MISSION_MODE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.mission_active = bool(data.get("active", False))
            self.mission_slot = data.get("slot")
        except Exception:
            self.mission_active = False
            self.mission_slot = None

    def _read_mission_overlay_file(self) -> None:
        try:
            mt = os.path.getmtime(MISSION_OVERLAY_FILE)
            if mt == self._last_mission_overlay_mtime:
                return

            with open(MISSION_OVERLAY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                self.mission_overlay_data = data
                self._last_mission_overlay_mtime = mt
        except Exception:
            pass

    def on_resize(self, w: int, h: int) -> None:
        if w <= 0 or h <= 0:
            return
        if w == self.w and h == self.h:
            return

        self.w = w
        self.h = h

        self.screen = pygame.display.set_mode((w, h), pygame.SRCALPHA)
        self._refresh_fonts()

        self.hud_renderer.on_resize(w, h)
        self.hitbox_renderer.on_resize(w, h)

    def _write_mission_select_command(self, payload: dict) -> None:
        try:
            with open(MISSION_SELECT_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    def handle_events(self) -> None:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False

            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self.mission_toggle_rect and self.mission_toggle_rect.collidepoint(event.pos):
                    self.mission_show_all = not self.mission_show_all
                    return

                if self.mission_panel_rect and self.mission_panel_rect.collidepoint(event.pos):
                    for rect, mission_id in self.mission_click_rects:
                        if rect.collidepoint(event.pos):
                            if mission_id:
                                self._write_mission_select_command({
                                    "action": "select",
                                    "slot": self.mission_slot,
                                    "mission_id": mission_id,
                                })
                            return
                elif self.mission_overlay_data.get("selector_open"):
                    self._write_mission_select_command({
                        "action": "close",
                    })
                    return

            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False

                elif event.key == pygame.K_F1:
                    self.control.show_hud = not self.control.show_hud
                    print(f"[master] show_hud={self.control.show_hud}")
                    self._write_control_file()

                elif event.key == pygame.K_F2:
                    self.control.show_hitboxes = not self.control.show_hitboxes
                    print(f"[master] show_hitboxes={self.control.show_hitboxes}")
                    self._write_control_file()

                elif event.key == pygame.K_F3:
                    self.control.show_debug = not self.control.show_debug
                    print(f"[master] show_debug={self.control.show_debug}")
                    self._write_control_file()

    def clear(self) -> None:
        assert self.screen is not None
        self.screen.fill(COLORKEY)

    def draw_master_debug(self, dt: float) -> None:
        if not self.control.show_debug:
            return
        if self.screen is None or self.font is None or self.smallfont is None:
            return

        lines = [
            "MASTER OVERLAY",
            f"{self.w}x{self.h}",
            f"HUD: {'ON' if self.control.show_hud else 'OFF'}",
            f"HITBOXES: {'ON' if self.control.show_hitboxes else 'OFF'}",
            "F1 HUD  F2 HITBOXES  F3 DEBUG  ESC QUIT",
            f"dt={dt:.4f}",
        ]

        rendered = [self.smallfont.render(line, True, (180, 180, 180)) for line in lines]
        box_w = max(s.get_width() for s in rendered) + 12
        box_h = sum(s.get_height() for s in rendered) + 12

        bg = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
        bg.fill((16, 16, 16, 190))
        self.screen.blit(bg, (10, 10))

        y = 16
        for surf in rendered:
            self.screen.blit(surf, (16, y))
            y += surf.get_height()


        y = 16
        for surf in rendered:
            self.screen.blit(surf, (16, y))
            y += surf.get_height()

    def _trigger_celebration(self) -> None:
        import math, random
        self._celebrate_active = True
        self._celebrate_phase = 0.0

        cx, cy = self.w // 2, self.h // 2 - int(self.h * 0.05)
        self._celebrate_particles = []

        for _ in range(120):
            angle = random.uniform(0, math.tau)
            speed = random.uniform(90, 460)
            size = random.randint(2, 8)
            lifetime = random.uniform(0.7, 1.8)
            color_choice = random.choice([
                (255, 220, 60),
                (255, 245, 190),
                (120, 210, 255),
                (255, 255, 255),
            ])
            self._celebrate_particles.append({
                "x": float(cx),
                "y": float(cy),
                "vx": math.cos(angle) * speed,
                "vy": math.sin(angle) * speed - random.uniform(20, 140),
                "size": size,
                "life": lifetime,
                "max_life": lifetime,
                "color": color_choice,
            })

        # extra upward gold burst
        for _ in range(36):
            angle = random.uniform(-2.2, -0.9)
            speed = random.uniform(140, 340)
            size = random.randint(3, 7)
            lifetime = random.uniform(0.8, 1.5)
            self._celebrate_particles.append({
                "x": float(cx + random.uniform(-60, 60)),
                "y": float(cy + random.uniform(-8, 8)),
                "vx": math.cos(angle) * speed,
                "vy": math.sin(angle) * speed,
                "size": size,
                "life": lifetime,
                "max_life": lifetime,
                "color": (255, 225, 90),
            })

    def update_celebration(self, dt: float) -> None:
        if not self._celebrate_active:
            return

        CELEBRATE_DURATION = 3.0
        self._celebrate_phase += dt
        if self._celebrate_phase >= CELEBRATE_DURATION:
            self._celebrate_active = False
            self._celebrate_particles = []
            return

        gravity = 300.0
        for p in self._celebrate_particles:
            p["x"] += p["vx"] * dt
            p["y"] += p["vy"] * dt
            p["vy"] += gravity * dt
            p["life"] -= dt

        self._celebrate_particles = [p for p in self._celebrate_particles if p["life"] > 0]

    def draw_celebration(self) -> None:
        import math
        if not self._celebrate_active or self.screen is None:
            return

        phase = self._celebrate_phase
        CELEBRATE_DURATION = 3.0

        if phase < 0.22:
            bloom_alpha = int(72 * (1.0 - phase / 0.22))
            bloom = pygame.Surface((self.w, self.h), pygame.SRCALPHA)
            bloom.fill((255, 236, 140, bloom_alpha))
            self.screen.blit(bloom, (0, 0))

        cx_screen = self.w // 2
        cy_screen = self.h // 2 - int(self.h * 0.05)

        ring_t = min(1.0, phase / 0.45)
        if ring_t < 1.0:
            ring_radius = int(80 + ring_t * min(self.w, self.h) * 0.32)
            ring_alpha = int(140 * (1.0 - ring_t))
            ring_thickness = max(2, int(8 - 5 * ring_t))

            ring_surf = pygame.Surface((ring_radius * 2 + 20, ring_radius * 2 + 20), pygame.SRCALPHA)
            pygame.draw.circle(
                ring_surf,
                (255, 225, 120, ring_alpha),
                (ring_radius + 10, ring_radius + 10),
                ring_radius,
                ring_thickness,
            )
            self.screen.blit(ring_surf, (cx_screen - ring_radius - 10, cy_screen - ring_radius - 10))

        for p in self._celebrate_particles:
            fade = max(0.0, p["life"] / p["max_life"])
            r, g, b = p["color"]
            alpha = int(255 * fade)
            size = max(1, int(p["size"] * (0.4 + 0.6 * fade)))

            glow_size = max(2, int(size * 2.2))
            glow = pygame.Surface((glow_size * 2, glow_size * 2), pygame.SRCALPHA)
            pygame.draw.circle(glow, (r, g, b, alpha // 5), (glow_size, glow_size), glow_size)
            self.screen.blit(glow, (int(p["x"]) - glow_size, int(p["y"]) - glow_size))

            surf = pygame.Surface((size * 2, size * 2), pygame.SRCALPHA)
            pygame.draw.circle(surf, (r, g, b, alpha), (size, size), size)
            self.screen.blit(surf, (int(p["x"]) - size, int(p["y"]) - size))

        stamp_in = min(1.0, phase / 0.25)
        stamp_out = max(0.0, 1.0 - (phase - (CELEBRATE_DURATION - 0.55)) / 0.55)
        stamp_alpha = int(255 * min(stamp_in, stamp_out))

        if stamp_alpha > 0 and self.font is not None:
            try:
                stamp_font = pygame.font.SysFont(
                    "consolas",
                    max(18, int(38 * min(self.w / 1280, self.h / 720))),
                    bold=True,
                )
            except Exception:
                stamp_font = self.font

            scale_t = min(1.0, phase / 0.25)
            overshoot = 1.0 + 0.10 * math.sin(scale_t * math.pi)
            scale = max(0.01, scale_t * overshoot)

            base_text = "MISSION COMPLETE"
            text_surf = stamp_font.render(base_text, True, (255, 245, 255))

            tw = max(1, int(text_surf.get_width() * scale))
            th = max(1, int(text_surf.get_height() * scale))
            drift_y = int((1.0 - min(1.0, phase / 0.35)) * 14)

            plate_w = tw + 90
            plate_h = th + 34
            plate = pygame.Surface((plate_w, plate_h), pygame.SRCALPHA)

            for yy in range(plate_h):
                frac = yy / max(plate_h - 1, 1)

                if frac < 0.16:
                    r, g, b = 250, 252, 255
                elif frac < 0.38:
                    t2 = (frac - 0.16) / 0.22
                    r = int(250 + (170 - 250) * t2)
                    g = int(252 + (215 - 252) * t2)
                    b = int(255 + (255 - 255) * t2)
                elif frac < 0.68:
                    t2 = (frac - 0.38) / 0.30
                    r = int(170 + (52 - 170) * t2)
                    g = int(215 + (120 - 215) * t2)
                    b = int(255 + (225 - 255) * t2)
                else:
                    t2 = (frac - 0.68) / 0.32
                    r = int(52 + (18 - 52) * t2)
                    g = int(120 + (58 - 120) * t2)
                    b = int(225 + (145 - 225) * t2)

                pygame.draw.line(
                    plate,
                    (r, g, b, min(220, stamp_alpha)),
                    (0, yy),
                    (plate_w, yy),
                )

            pygame.draw.rect(
                plate,
                (255, 255, 255, min(130, stamp_alpha)),
                (2, 2, plate_w - 4, max(2, plate_h // 7)),
                border_radius=4,
            )

            pygame.draw.rect(
                plate,
                (220, 240, 255, min(180, stamp_alpha)),
                (0, 0, plate_w, plate_h),
                2,
                border_radius=5,
            )

            pygame.draw.rect(
                plate,
                (16, 46, 98, min(150, stamp_alpha)),
                (3, 3, plate_w - 6, plate_h - 6),
                1,
                border_radius=4,
            )

            px = cx_screen - plate_w // 2
            py = cy_screen - plate_h // 2 - drift_y
            self.screen.blit(plate, (px, py))

            shadow_surf = stamp_font.render(base_text, True, (0, 0, 0))
            shadow_scaled = pygame.transform.smoothscale(shadow_surf, (tw, th))
            shadow_scaled.set_alpha(stamp_alpha // 2)

            scaled = pygame.transform.smoothscale(text_surf, (tw, th))
            scaled.set_alpha(stamp_alpha)

            sheen = pygame.Surface((tw, th), pygame.SRCALPHA)
            pygame.draw.rect(
                sheen,
                (255, 255, 255, min(80, stamp_alpha // 3)),
                (0, 0, tw, max(2, th // 6)),
            )
            scaled.blit(sheen, (0, 0), special_flags=pygame.BLEND_RGBA_ADD)

            tx = cx_screen - tw // 2
            ty = cy_screen - th // 2 - drift_y
            self.screen.blit(shadow_scaled, (tx + 4, ty + 4))
            self.screen.blit(scaled, (tx, ty))

    def update_mission_animations(self, dt: float) -> None:
        """Drive per-step metallic gradient animations each frame."""
        live_data = self.mission_overlay_data or {}
        holding = self._mission_hold_frames > 0
        data = self._mission_hold_data if holding else live_data

        steps = data.get("active_mission_steps") or []
        completed_count = int(data.get("completed_step_count", 0))
        current_idx = int(data.get("current_step_index", 0))

        mission_id = (live_data.get("active_mission_id") or "")
        if self._completion_block_frames > 0:
            self._completion_block_frames -= 1
            if self._completion_block_frames <= 0:
                self._completion_block_mission_id = ""

        ANIM_SPEED = 4.0  # how fast the gradient slides in/out (t units per second)

        for idx in range(len(steps)):
            t = self.step_anim.get(idx, None)
            if t is None:
                # Initialize: active step starts fully lit, others start dark
                t = 1.0 if idx == current_idx else 0.0

            if idx < completed_count:
                # Completed: animate down toward 0
                t = max(0.0, t - dt * ANIM_SPEED)
            elif idx == current_idx:
                # Active: animate up toward 1
                t = min(1.0, t + dt * ANIM_SPEED)
            else:
                # Pending: stay at 0 (no gradient yet)
                t = max(0.0, t - dt * ANIM_SPEED)

            self.step_anim[idx] = t

        # On step change, clear state for steps no longer in range
        # While holding the finished mission on screen, do not re-run completion detection.
        if holding:
            self._mission_hold_frames -= 1
            if self._mission_hold_frames <= 0:
                self._mission_hold_data = {}
        else:
            just_cleared = bool(live_data.get("just_cleared", False))
            is_complete_now = bool(steps) and completed_count >= len(steps)

            same_mission_temporarily_blocked = (
                mission_id
                and mission_id == self._completion_block_mission_id
                and self._completion_block_frames > 0
            )

            completion_event = (
                just_cleared
                and mission_id
                and not same_mission_temporarily_blocked
            )

            if completion_event:
                held = dict(live_data)
                held_steps = held.get("active_mission_steps") or []
                held["completed_step_count"] = len(held_steps)
                held["current_step_index"] = max(0, len(held_steps) - 1)
                self._mission_hold_data = held
                self._mission_hold_frames = self._mission_hold_duration_frames

                self._completion_block_mission_id = mission_id
                self._completion_block_frames = self._mission_hold_duration_frames + 12

                self._trigger_celebration()

            if mission_id != self._last_mission_id_seen:
                self._last_mission_id_seen = mission_id
                self._prev_mission_complete = False

            self._prev_mission_complete = is_complete_now



    def draw_mission_overlay(self) -> None:
        self.mission_click_rects = []
        self.mission_panel_rect = None
        self.mission_toggle_rect = None

        if not self.mission_active or not self.mission_slot:
            return
        if self.screen is None or self.font is None or self.smallfont is None:
            return

        data = self._mission_hold_data if self._mission_hold_frames > 0 else (self.mission_overlay_data or {})
        character = data.get("character") or "Unknown"
        mission_name = data.get("active_mission_name") or "No mission loaded"
        steps = data.get("active_mission_steps") or []
        missions = data.get("missions") or []

        selector_open = bool(data.get("selector_open", False))
        selector_index = int(data.get("selector_index", 0))
        selector_hint = data.get("selector_hint") or ""
        selector_controls = data.get("selector_controls") or ""

        title = self.font.render(
            f"{character} Mission Mode - {self.mission_slot}",
            True,
            (235, 235, 235),
        )

        pad = 10

        if selector_open:
            sub = self.smallfont.render("Mission Select", True, (180, 180, 180))
            ctrl = self.smallfont.render(selector_controls, True, (180, 180, 180))

            line_surfs = []
            for idx, mission in enumerate(missions):
                selected = idx == selector_index
                completed = bool(mission.get("completed", False))
                name = mission.get("name") or mission.get("mission_id") or f"Mission {idx + 1}"

                prefix = "->" if selected else "  "
                suffix = " [done]" if completed else ""
                color = (
                    (255, 220, 90)
                    if selected
                    else ((120, 220, 140) if completed else (220, 220, 220))
                )

                surf = self.smallfont.render(
                    f"{prefix} {idx + 1}. {name}{suffix}",
                    True,
                    color,
                )
                line_surfs.append((surf, mission.get("mission_id")))

            content_w = max(
                [title.get_width(), sub.get_width(), ctrl.get_width()]
                + [surf.get_width() for surf, _mission_id in line_surfs]
                + [260]
            )
            content_h = (
                title.get_height()
                + sub.get_height()
                + ctrl.get_height()
                + 12
                + sum(surf.get_height() + 6 for surf, _mission_id in line_surfs)
            )

            box_w = content_w + pad * 2
            box_h = content_h + pad * 2

            x = (self.w - box_w) // 2
            y = max(24, int(self.h * 0.08))
            self.mission_panel_rect = pygame.Rect(x, y, box_w, box_h)

            bg = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
            bg.fill((24, 16, 40, 220))
            self.screen.blit(bg, (x, y))
            pygame.draw.rect(
                self.screen,
                (170, 120, 255),
                (x, y, box_w, box_h),
                1,
                border_radius=4,
            )

            draw_y = y + pad
            self.screen.blit(title, (x + pad, draw_y))
            draw_y += title.get_height() + 4
            self.screen.blit(sub, (x + pad, draw_y))
            draw_y += sub.get_height() + 4
            self.screen.blit(ctrl, (x + pad, draw_y))
            draw_y += ctrl.get_height() + 8

            for surf, mission_id in line_surfs:
                row_rect = pygame.Rect(
                    x + pad - 4,
                    draw_y - 2,
                    box_w - pad * 2 + 8,
                    surf.get_height() + 4,
                )
                self.mission_click_rects.append((row_rect, mission_id))

                if row_rect.collidepoint(pygame.mouse.get_pos()):
                    row_bg = pygame.Surface((row_rect.width, row_rect.height), pygame.SRCALPHA)
                    row_bg.fill((80, 60, 120, 120))
                    self.screen.blit(row_bg, (row_rect.x, row_rect.y))

                self.screen.blit(surf, (x + pad, draw_y))
                draw_y += surf.get_height() + 6

        else:
            sub = self.smallfont.render(mission_name, True, (180, 180, 180))
            hint = self.smallfont.render(selector_hint, True, (150, 150, 150))

            completed_step_count = int(data.get("completed_step_count", 0))
            current_step_index = int(data.get("current_step_index", 0))

            STEP_PAD_X = 8
            STEP_PAD_Y = 4
            STEP_GAP = 4
            STEP_VISIBLE_COUNT = 6

            max_start = max(0, len(steps) - STEP_VISIBLE_COUNT)
            if self.mission_show_all or len(steps) <= STEP_VISIBLE_COUNT:
                visible_start = 0
                visible_end = len(steps)
            else:
                visible_start = min(max(0, current_step_index - 2), max_start)
                visible_end = min(len(steps), visible_start + STEP_VISIBLE_COUNT)

            visible_steps = list(enumerate(steps[visible_start:visible_end], start=visible_start))

            toggle_text = "Show Less" if self.mission_show_all else "Show All"
            toggle_surf = self.smallfont.render(toggle_text, True, (220, 220, 220))

            # Build step surfaces to measure content width
            step_surfs = []
            for idx, step in visible_steps:
                step_text = " / ".join(step) if isinstance(step, list) else str(step)
                if idx < completed_step_count:
                    label = f"[x] {idx + 1}. {step_text}"
                    color = (120, 200, 140)
                elif idx == current_step_index:
                    label = f"[>] {idx + 1}. {step_text}"
                    color = (255, 220, 80)
                else:
                    label = f"[ ] {idx + 1}. {step_text}"
                    color = (180, 180, 180)
                surf = self.smallfont.render(label, True, color)
                step_surfs.append((idx, surf, color))

            step_row_h = (step_surfs[0][1].get_height() if step_surfs else 18) + STEP_PAD_Y * 2

            content_w = max(
                [title.get_width(), sub.get_width(), hint.get_width(), toggle_surf.get_width() + 20]
                + [surf.get_width() + STEP_PAD_X * 2 for _, surf, _ in step_surfs]
                + [260]
            )
            content_h = (
                title.get_height()
                + sub.get_height()
                + hint.get_height()
                + 16
                + len(step_surfs) * (step_row_h + STEP_GAP)
            )

            box_w = content_w + pad * 2
            box_h = content_h + pad * 2

            x = (self.w - box_w) // 2
            y = max(24, int(self.h * 0.08))
            self.mission_panel_rect = pygame.Rect(x, y, box_w, box_h)

            # Outer panel
            bg = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
            bg.fill((24, 16, 40, 210))
            self.screen.blit(bg, (x, y))
            pygame.draw.rect(
                self.screen,
                (170, 120, 255),
                (x, y, box_w, box_h),
                1,
                border_radius=4,
            )

            draw_y = y + pad
            self.screen.blit(title, (x + pad, draw_y))
            draw_y += title.get_height() + 4
            self.screen.blit(sub, (x + pad, draw_y))
            draw_y += sub.get_height() + 4
            self.screen.blit(hint, (x + pad, draw_y))

            toggle_w = toggle_surf.get_width() + 14
            toggle_h = toggle_surf.get_height() + 6
            toggle_x = x + box_w - pad - toggle_w
            toggle_y = draw_y - 2
            self.mission_toggle_rect = pygame.Rect(toggle_x, toggle_y, toggle_w, toggle_h)

            toggle_col = (70, 70, 95) if not self.mission_show_all else (110, 80, 150)
            if self.mission_toggle_rect.collidepoint(pygame.mouse.get_pos()):
                toggle_col = tuple(min(255, c + 25) for c in toggle_col)

            pygame.draw.rect(self.screen, toggle_col, self.mission_toggle_rect, border_radius=3)
            pygame.draw.rect(self.screen, (180, 180, 200), self.mission_toggle_rect, 1, border_radius=3)
            self.screen.blit(toggle_surf, (toggle_x + 7, toggle_y + 3))

            draw_y += hint.get_height() + 8

            # Step boxes
            step_box_w = box_w - pad * 2
            for idx, surf, text_color in step_surfs:
                t = self.step_anim.get(idx, 0.0)
                is_completed = idx < completed_step_count
                is_active = idx == current_step_index

                # --- background box ---
                row_surf = pygame.Surface((step_box_w, step_row_h), pygame.SRCALPHA)

                if is_completed:
                    # Dark, slightly greenish, no gradient
                    alpha = int(180 - t * 60)  # fades as t drops to 0
                    row_surf.fill((20, 38, 26, alpha))
                    border_color = (60, 140, 80, int(80 + t * 100))
                elif is_active and t > 0.0:
                    # Metallic blue gradient — drawn as horizontal strips
                    for strip_y in range(step_row_h):
                        frac = strip_y / max(step_row_h - 1, 1)
                        # gradient: dark blue at top/bottom, brighter mid
                        mid = 1.0 - abs(frac - 0.45) * 2.2
                        mid = max(0.0, min(1.0, mid))

                        base_r = int(18 + mid * 20)
                        base_g = int(60 + mid * 60)
                        base_b = int(110 + mid * 100)

                        # Blend toward dark base as t approaches 0
                        dark_r, dark_g, dark_b = 28, 22, 44
                        r = int(dark_r + (base_r - dark_r) * t)
                        g = int(dark_g + (base_g - dark_g) * t)
                        b = int(dark_b + (base_b - dark_b) * t)

                        pygame.draw.line(row_surf, (r, g, b, 210), (0, strip_y), (step_box_w, strip_y))

                    border_color = (
                        int(40 + 60 * t),
                        int(100 + 100 * t),
                        int(180 + 60 * t),
                        220,
                    )

                else:
                    # Pending or animating out — dark base
                    row_surf.fill((28, 22, 44, 180))
                    border_color = (80, 70, 110, 120)

                self.screen.blit(row_surf, (x + pad, draw_y))
                pygame.draw.rect(
                    self.screen,
                    border_color[:3],
                    (x + pad, draw_y, step_box_w, step_row_h),
                    1,
                    border_radius=3,
                )

                # Text — dim completed steps based on animation
                step_text = " / ".join(steps[idx]) if isinstance(steps[idx], list) else str(steps[idx])
                label_str = (
                    f"[x] {idx + 1}. {step_text}" if is_completed
                    else f"[>] {idx + 1}. {step_text}" if is_active
                    else f"[ ] {idx + 1}. {step_text}"
                )
                if is_completed:
                    dim = max(60, int(120 * (1.0 - t) + 180 * t))
                    draw_color = (
                        min(255, int(text_color[0] * dim // 200)),
                        min(255, int(text_color[1] * dim // 200)),
                        min(255, int(text_color[2] * dim // 200)),
                    )
                else:
                    draw_color = text_color

                draw_surf = self.smallfont.render(label_str, True, draw_color)

                self.screen.blit(draw_surf, (x + pad + STEP_PAD_X, draw_y + STEP_PAD_Y))
                draw_y += step_row_h + STEP_GAP

            if not self.mission_show_all and len(steps) > STEP_VISIBLE_COUNT:
                footer = self.smallfont.render(
                    f"Showing {visible_start + 1}-{visible_end} of {len(steps)}",
                    True,
                    (140, 140, 160),
                )
                self.screen.blit(footer, (x + pad, draw_y + 2))


    def present(self) -> None:
        pygame.display.flip()

    def run(self) -> None:
        self.init()
        assert self.clock is not None
        assert self.screen is not None
        assert self.dolphin_hwnd is not None
        assert self.overlay_hwnd is not None

        print("[master] started")
        print("[master] F1 = toggle HUD")
        print("[master] F2 = toggle hitboxes")
        print("[master] F3 = toggle debug")
        print("[master] ESC = quit")

        while self.running:
            try:
                w, h = sync_overlay_to_dolphin(self.dolphin_hwnd, self.overlay_hwnd)
                self.on_resize(w, h)

                self._read_control_file()
                self._read_mission_mode_file()
                self._read_mission_overlay_file()
                self.handle_events()

                dt = self.clock.tick(TARGET_FPS) / 1000.0

                self.clear()

                try:
                    self.hitbox_renderer.update(dt, self.control)
                except Exception as exc:
                    print("[master] hitbox update failed")
                    traceback.print_exc()
                    self.hitbox_renderer = NullHitboxRenderer()
                    self.hitbox_renderer.on_resize(self.w, self.h)

                try:
                    self.hud_renderer.update(dt, self.control)
                except Exception as exc:
                    print("[master] hud update failed")
                    traceback.print_exc()
                    self.hud_renderer = NullHudRenderer()
                    self.hud_renderer.on_resize(self.w, self.h)

                self.update_mission_animations(dt)
                self.update_celebration(dt)

                try:
                    self.hitbox_renderer.draw(self.screen, self.control)
                except Exception as exc:
                    print("[master] hitbox draw failed")
                    traceback.print_exc()
                    self.hitbox_renderer = NullHitboxRenderer()
                    self.hitbox_renderer.on_resize(self.w, self.h)

                try:
                    self.hud_renderer.draw(self.screen, self.control)
                except Exception as exc:
                    print("[master] hud draw failed")
                    traceback.print_exc()
                    self.hud_renderer = NullHudRenderer()
                    self.hud_renderer.on_resize(self.w, self.h)

                self.draw_mission_overlay()
                self.draw_celebration()
                self.draw_master_debug(dt)

                self.present()

            except Exception as exc:
                pause_on_error("MasterLoopCrash", exc)
                self.running = False

        pygame.quit()


def main() -> None:
    overlay = MasterOverlay()
    overlay.run()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        pause_on_error("FatalCrash", exc)
        raise