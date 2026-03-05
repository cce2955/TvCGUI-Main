#!/usr/bin/env python3
from __future__ import annotations

import ctypes
import math
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional
import os
import pygame
import win32con
import win32gui

from dolphin_io import hook, rd32, rbytes

import json as _json

WORLD_Y_OFFSET = -0.7
PROJECTILE_Y_OFFSET: float = 1.2
PROJECTILE_RADIUS_SCALE: float = 0.5
PROJECTILE_DESPAWN_FRAMES: int = 6   # frames of inactivity before hiding

PERSPECTIVE_Z_OVERRIDE: Optional[float] = None
HITBOX_FILTER_FILE = "hitbox_filter.json"
_last_filter_mtime = 0.0
_slot_filter = {"P1": True, "P2": True, "P3": True, "P4": True}

MOTION_THRESHOLD: float = 0.003
STILL_FRAME_LIMIT: int = 4
MOTION_FRAME_REQUIRED: int = 2

PROJ_SIG         = b"\x04\x01\x02\x00\x00"
PROJ_RADIUS_OFF  = 0x2F

PROJ_SCAN_START  = 0x90000000
PROJ_SCAN_END    = 0x94000000
PROJ_SCAN_BLOCK  = 0x40000

PROJECTILE_POOLS       = [0x91B15A10, 0x91B15B50]
PROJECTILE_NODE_STRIDE = 0x30
PROJECTILE_NODE_COUNT  = 16

PROJ_PAIR_DISTANCE = 999.0
OFF_CHAR_ID = 0x14


def _read_slot_filter() -> dict:
    global _last_filter_mtime, _slot_filter
    try:
        mt = os.path.getmtime(HITBOX_FILTER_FILE)
        if mt != _last_filter_mtime:
            _last_filter_mtime = mt
            with open(HITBOX_FILTER_FILE) as f:
                _slot_filter = _json.load(f)
    except Exception:
        pass
    return _slot_filter


def set_dpi_aware() -> None:
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

set_dpi_aware()


@dataclass(frozen=True)
class HitboxLayout:
    struct_shift: int
    blocks: Tuple[int, ...]
    off_x: int
    off_y: int
    off_r: int
    off_flag: int

@dataclass(frozen=True)
class CameraLayout:
    base: int
    off_x: int
    off_y: int
    off_z: int
    off_w: int

@dataclass(frozen=True)
class DisplayConfig:
    baseline_w: int
    baseline_h: int
    baseline_ppu: float
    zoom: float
    center_y_offset_px: int
    max_radius_units: float
    fps: int
    show_debug_axes: bool


SLOT_BASES: Dict[str, int] = {
    "P1": 0x9246B9C0,
    "P2": 0x92B6BA00,
    "P3": 0x927EB9E0,
    "P4": 0x92EEBA20,
}

HITBOX = HitboxLayout(
    struct_shift=0x4C0,
    blocks=(0x64, 0xA4, 0xE4),
    off_x=0x00,
    off_y=0x04,
    off_r=0x18,
    off_flag=0xC3,
)

CAMERA = CameraLayout(
    base=0x8053CB20,
    off_x=0x00,
    off_y=0x04,
    off_z=0x08,
    off_w=0x0C,
)

DISPLAY = DisplayConfig(
    baseline_w=1280,
    baseline_h=720,
    baseline_ppu=160.0,
    zoom=1.0,
    center_y_offset_px=0,
    max_radius_units=8.0,
    fps=60,
    show_debug_axes=False,
)

USE_LIVE_CAMERA = True

COLORS: Dict[str, List[Tuple[int, int, int]]] = {
    "P1": [(255, 60, 60), (255, 140, 0), (255, 220, 0)],
    "P2": [(180, 60, 255), (60, 200, 255), (60, 255, 180)],
    "P3": [(255, 80, 180), (255, 0, 120), (255, 120, 200)],
    "P4": [(80, 255, 120), (0, 255, 80), (120, 255, 200)],
}

COL_CROSS = (255, 255, 255)
COL_DIM = (120, 120, 120)
COL_BG = (0, 0, 0)
COL_DEBUG = (0, 255, 0)
COL_PROJ  = (255, 255, 255)


# ----------------------------
# Projectile scanner
# ----------------------------

class ProjectileScanner:
    def __init__(self):
        self._radius_addrs: List[int] = []
        self._scan_count: int = 0

    def scan(self) -> int:
        found: List[int] = []
        for base_addr in range(PROJ_SCAN_START, PROJ_SCAN_END, PROJ_SCAN_BLOCK):
            data = rbytes(base_addr, PROJ_SCAN_BLOCK)
            if not data:
                continue
            idx = data.find(PROJ_SIG)
            while idx != -1:
                sig_addr    = base_addr + idx
                radius_addr = sig_addr + PROJ_RADIUS_OFF
                r = _rf(radius_addr)
                if 0.0 < r < 20.0:
                    found.append(radius_addr)
                idx = data.find(PROJ_SIG, idx + 1)

        self._radius_addrs = found
        self._scan_count  += 1
        print(f"[ProjectileScanner] scan #{self._scan_count}: {len(found)} radius address(es) found")
        for a in found:
            print(f"  radius_addr=0x{a:08X}  r={_rf(a):.4f}")
        return len(found)

    def dump(self, max_hits: int = 3) -> None:
        print(f"\n[ProjectileScanner.dump] first {max_hits} sig hits:")
        hits = 0
        for base_addr in range(PROJ_SCAN_START, PROJ_SCAN_END, PROJ_SCAN_BLOCK):
            if hits >= max_hits:
                break
            data = rbytes(base_addr, PROJ_SCAN_BLOCK)
            if not data:
                continue
            idx = data.find(PROJ_SIG)
            while idx != -1 and hits < max_hits:
                sig_addr = base_addr + idx
                chunk    = data[idx : idx + 0x60]
                print(f"\n  sig @ 0x{sig_addr:08X}")
                for row in range(0, len(chunk), 16):
                    row_bytes = chunk[row : row + 16]
                    hex_str   = " ".join(f"{b:02x}" for b in row_bytes)
                    floats = []
                    for fi in range(0, len(row_bytes) - 3, 4):
                        try:
                            fv = struct.unpack(">f", row_bytes[fi:fi+4])[0]
                            if math.isfinite(fv) and 0.0 < abs(fv) < 20.0:
                                floats.append(f"+0x{row+fi:02X}={fv:.4f}")
                        except Exception:
                            pass
                    float_str = "  " + " ".join(floats) if floats else ""
                    print(f"    +0x{row:02X}  {hex_str}{float_str}")
                hits += 1
                idx = data.find(PROJ_SIG, idx + 1)
        print(f"\n[dump] done.")

    @property
    def radius_addrs(self) -> List[int]:
        return self._radius_addrs

    @property
    def scan_count(self) -> int:
        return self._scan_count


# ----------------------------
# Projectile node tracker
# ----------------------------

@dataclass
class ProjectileNodeState:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    r: float = 0.0
    prev_x: float = 0.0
    inactive_frames: int = 0
    active: bool = False


class ProjectileNodeTracker:
    """
    Tracks per-node liveness. A node is considered active when it has a
    plausible position. After PROJECTILE_DESPAWN_FRAMES consecutive frames
    without a valid position the node is hidden. This prevents flicker and
    gives a clean despawn.
    """

    def __init__(self, pool_count: int):
        self._nodes: Dict[int, ProjectileNodeState] = {
            i: ProjectileNodeState() for i in range(pool_count)
        }

    def update(self, node_idx: int, x: float, y: float, z: float, r: float) -> None:
        state = self._nodes[node_idx]
        is_moving = abs(x - state.prev_x) > 0.0001
        state.prev_x = x

        if is_moving and abs(x) < 50 and abs(y) < 50:
            state.x = x
            state.y = y
            state.z = z
            state.r = r
            state.inactive_frames = 0
            state.active = True
        else:
            state.inactive_frames += 1
            if state.inactive_frames >= PROJECTILE_DESPAWN_FRAMES:
                state.active = False

    def visible_nodes(self) -> List[ProjectileNodeState]:
        return [s for s in self._nodes.values() if s.active]


# ----------------------------
# Translation tracker
# ----------------------------

@dataclass
class HitboxMotionState:
    still_frames: int = 0
    motion_frames: int = 0
    suppressed: bool = False
    prev_x: float = 0.0
    prev_y: float = 0.0
    initialized: bool = False


class MotionFilter:
    def __init__(self):
        self._states: Dict[Tuple[str, int], HitboxMotionState] = {}

    def _key(self, slot: str, idx: int) -> Tuple[str, int]:
        return (slot, idx)

    def update(self, slot: str, idx: int, x: float, y: float, r: float) -> bool:
        key = self._key(slot, idx)
        if key not in self._states:
            self._states[key] = HitboxMotionState()
        state = self._states[key]

        if r <= 0.001:
            state.still_frames = 0
            state.motion_frames = 0
            state.suppressed = False
            state.initialized = False
            return False

        if not state.initialized:
            state.prev_x = x
            state.prev_y = y
            state.initialized = True
            state.still_frames = 0
            state.motion_frames = MOTION_FRAME_REQUIRED
            state.suppressed = False
            return True

        dx = x - state.prev_x
        dy = y - state.prev_y
        delta = math.sqrt(dx * dx + dy * dy)
        state.prev_x = x
        state.prev_y = y

        if delta >= MOTION_THRESHOLD:
            state.still_frames = 0
            state.motion_frames = min(state.motion_frames + 1, MOTION_FRAME_REQUIRED + 1)
            if state.suppressed and state.motion_frames >= MOTION_FRAME_REQUIRED:
                state.suppressed = False
        else:
            state.motion_frames = 0
            state.still_frames = min(state.still_frames + 1, STILL_FRAME_LIMIT + 1)
            if not state.suppressed and state.still_frames >= STILL_FRAME_LIMIT:
                state.suppressed = True

        return not state.suppressed

    def reset_slot(self, slot: str, count: int):
        for i in range(count):
            key = self._key(slot, i)
            if key in self._states:
                del self._states[key]


# ----------------------------
# Memory helpers
# ----------------------------

def rb(addr: int) -> int:
    v = rd32(addr & ~3)
    if v is None:
        return 0
    shift = (3 - (addr & 3)) * 8
    return (v >> shift) & 0xFF


def _rf(addr: int) -> float:
    v = rd32(addr)
    if v is None:
        return 0.0
    try:
        f = struct.unpack(">f", struct.pack(">I", v))[0]
        return f if math.isfinite(f) else 0.0
    except Exception:
        return 0.0

rf = _rf


def read_hitboxes(slot_base: int, layout: HitboxLayout):
    base = slot_base + layout.struct_shift
    out = []
    flag = rb(base + layout.off_flag)
    for b in layout.blocks:
        x = _rf(base + b + layout.off_x)
        y = _rf(base + b + layout.off_y)
        r = _rf(base + b + layout.off_r)
        out.append((x, y, r, flag))
    return out


def read_fighter_root(slot_base: int):
    return _rf(slot_base + 0xB0), _rf(slot_base + 0xB4), _rf(slot_base + 0xB8)


def read_camera_pos(layout: CameraLayout):
    return (
        _rf(layout.base + layout.off_x),
        _rf(layout.base + layout.off_y),
        _rf(layout.base + layout.off_z),
        _rf(layout.base + layout.off_w),
    )


def update_projectile_nodes(
    tracker: ProjectileNodeTracker,
    scanner: ProjectileScanner,
) -> None:
    """
    Read all node positions from pools and feed them into the tracker.
    The tracker handles liveness / despawn logic.
    """
    radii = [_rf(a) for a in scanner.radius_addrs]
    default_r = next((r for r in radii if r > 0.001), 0.0)

    node_idx = 0
    
    for pool in PROJECTILE_POOLS:
        for i in range(PROJECTILE_NODE_COUNT):
            node = pool + i * PROJECTILE_NODE_STRIDE
            x = _rf(node + 0x00)
            y = _rf(node + 0x04)
            z = _rf(node + 0x08)
            if pool == PROJECTILE_POOLS[0] and i == 0:
                print(f"node[0] x={x:.4f} y={y:.4f} z={z:.4f}")
            tracker.update(node_idx, x, y, z, default_r if (abs(x) > 0.001 and abs(x) < 50 and abs(y) < 50) else 0.0)
            node_idx += 1


# ----------------------------
# Win32 helpers
# ----------------------------

def find_dolphin_hwnd() -> Optional[int]:
    candidates: List[Tuple[int, int, str]] = []

    def score_title(t: str) -> int:
        tl = t.lower()
        if "dolphin" not in tl:
            return -10_000
        s = 0
        if "|" in t:
            s += 50
            s += min(30, t.count("|") * 5)
        for tok in ("jit", "jit64", "opengl", "vulkan", "d3d", "direct3d", "hle"):
            if tok in tl:
                s += 20
        if "(" in t and ")" in t:
            s += 30
        for bad in ("memory", "watch", "log", "breakpoint", "register", "disassembly", "config", "settings"):
            if bad in tl:
                s -= 25
        if t.count("|") >= 3:
            s += 20
        return s

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


def get_client_screen_rect(hwnd: int):
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    tl = win32gui.ClientToScreen(hwnd, (left, top))
    br = win32gui.ClientToScreen(hwnd, (right, bottom))
    return tl[0], tl[1], br[0] - tl[0], br[1] - tl[1]


def apply_overlay_style(hwnd: int) -> None:
    style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
    style &= ~(win32con.WS_CAPTION | win32con.WS_THICKFRAME |
               win32con.WS_MINIMIZE | win32con.WS_MAXIMIZE | win32con.WS_SYSMENU)
    style |= win32con.WS_POPUP
    win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)

    ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
    ex |= win32con.WS_EX_LAYERED
    ex &= ~win32con.WS_EX_TOPMOST
    win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex)

    win32gui.SetLayeredWindowAttributes(hwnd, 0x000000, 0, win32con.LWA_COLORKEY)
    win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
        win32con.SWP_FRAMECHANGED | win32con.SWP_NOMOVE |
        win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE)


def sync_overlay_to_dolphin(dolphin_hwnd: int, overlay_hwnd: int):
    x, y, w, h = get_client_screen_rect(dolphin_hwnd)
    win32gui.SetWindowPos(overlay_hwnd, win32con.HWND_NOTOPMOST, x, y, w, h,
        win32con.SWP_NOACTIVATE)
    return w, h


# ----------------------------
# Overlay
# ----------------------------

class Overlay:
    def __init__(self, cfg: DisplayConfig):
        self.cfg = cfg
        self.cam_x = 0.0
        self.cam_y = 0.0
        self.cam_z = 0.0
        self.ref_cam_z = None
        self.ppu = cfg.baseline_ppu
        self.zoom = cfg.zoom
        self.w = cfg.baseline_w
        self.h = cfg.baseline_h
        self.cx = self.w // 2
        self.cy = self.h // 2 + cfg.center_y_offset_px
        self.screen: Optional[pygame.Surface] = None
        self.font_small: Optional[pygame.font.Font] = None
        self.font_hud: Optional[pygame.font.Font] = None
        self.debug_axes = cfg.show_debug_axes

    def init_pygame(self) -> int:
        pygame.init()
        self.font_small = pygame.font.SysFont("consolas", 11)
        self.font_hud = pygame.font.SysFont("consolas", 13, bold=True)
        self.screen = pygame.display.set_mode((self.w, self.h), pygame.SRCALPHA)
        pygame.display.set_caption("TvC Hitbox Overlay v3")

        icon_path = os.path.join("assets", "portraits", "Placeholder.png")
        if not os.path.exists(icon_path):
            icon_path = os.path.join("assets", "icon.png")
        if os.path.exists(icon_path):
            icon = pygame.image.load(icon_path).convert_alpha()
            pygame.display.set_icon(icon)

        hwnd = pygame.display.get_wm_info()["window"]
        apply_overlay_style(hwnd)
        return hwnd

    def on_resize(self, w: int, h: int):
        if w <= 0 or h <= 0:
            return
        if w == self.w and h == self.h:  
            return
        self.w, self.h = w, h
        self.screen = pygame.display.set_mode((w, h), pygame.SRCALPHA)
        self.viewport_scale = h / 720.0
        self.aspect_ratio = w / float(h)
        self.base_aspect = 4.0 / 3.0
        self.x_aspect_scale = self.base_aspect / self.aspect_ratio
        self.cx = w * 0.5
        self.cy = h * 0.5
        self.window_aspect = w / float(h)
        self.stretch_factor = self.window_aspect / self.base_aspect

    def world_to_screen(self, world_x: float, world_y: float, world_z: float):
        if self.ref_cam_z is None and abs(self.cam_z) > 0.0001:
            self.ref_cam_z = self.cam_z
        camera_scale = (
            self.ref_cam_z / self.cam_z
            if (self.ref_cam_z is not None and abs(self.cam_z) > 0.0001)
            else 1.0
        )
        zoom_scale = self.cfg.baseline_ppu * self.viewport_scale * camera_scale
        sx = self.cx + (world_x - self.cam_x) * zoom_scale * self.stretch_factor
        sy = self.cy - ((world_y - self.cam_y) + WORLD_Y_OFFSET) * zoom_scale
        return int(sx), int(sy), 1.0, zoom_scale

    def clear(self):
        self.screen.fill(COL_BG)

    def draw_debug_axes(self):
        if not self.debug_axes:
            return
        pygame.draw.line(self.screen, COL_DEBUG, (0, self.h // 2), (self.w, self.h // 2), 1)
        pygame.draw.line(self.screen, COL_DEBUG, (self.w // 2, 0), (self.w // 2, self.h), 1)

    def draw_hitbox(self, x, y, z, r, color, label, is_active=False):
        if r <= 0.001 or not math.isfinite(r):
            return
        if not math.isfinite(x) or not math.isfinite(y):
            return
        r = min(r, self.cfg.max_radius_units)
        proj = self.world_to_screen(x, y, z)
        if not proj:
            return
        sx, sy, depth, focal = proj
        rpx = max(2, int((r / depth) * focal))
        if rpx <= 0 or rpx > 5000:
            return

        if len(color) == 3:
            r_c, g_c, b_c = color
        else:
            r_c, g_c, b_c, _ = color

        hit_surf = pygame.Surface((rpx * 2 + 8, rpx * 2 + 8), pygame.SRCALPHA)
        center = (rpx + 4, rpx + 4)

        if is_active:
            pygame.draw.circle(hit_surf, (r_c, g_c, b_c, 140), center, rpx)
            pygame.draw.circle(hit_surf, (255, 255, 255, 255), center, rpx, 4)
            pygame.draw.circle(hit_surf, (r_c, g_c, b_c, 120), center, rpx + 3, 4)
        else:
            pygame.draw.circle(hit_surf, (r_c, g_c, b_c, 70), center, rpx + 2, 3)
            pygame.draw.circle(hit_surf, (r_c, g_c, b_c, 220), center, rpx, 3)

        self.screen.blit(hit_surf, (sx - rpx - 4, sy - rpx - 4))
        pygame.draw.circle(self.screen, COL_CROSS, (sx, sy), 2)
        cs = 6
        pygame.draw.line(self.screen, COL_CROSS, (sx - cs, sy), (sx + cs, sy), 2)
        pygame.draw.line(self.screen, COL_CROSS, (sx, sy - cs), (sx, sy + cs), 2)
        txt = self.font_small.render(f"{label} r={r:.2f}", True, (r_c, g_c, b_c))
        self.screen.blit(txt, (sx + rpx + 6, sy - 10))

    def draw_hud(self, counts, motion_filter: MotionFilter, scanner: ProjectileScanner):
        base = " | ".join([f"{k}={v}" for k, v in counts.items()])
        ref_str = f"{self.ref_cam_z:.4f}" if self.ref_cam_z is not None else "none"
        debug = f"  |  cam_z={self.cam_z:.4f}  ref_z={ref_str}"
        suppressed_total = sum(1 for s in motion_filter._states.values() if s.suppressed)
        supp_str = f"  |  suppressed={suppressed_total}"
        prj_str = f"  |  prj_addrs={len(scanner.radius_addrs)}  [F2=rescan]"
        hud = self.font_hud.render(base + debug + supp_str + prj_str, True, COL_DIM)
        self.screen.blit(hud, (8, 8))

    def present(self):
        pygame.display.flip()


# ----------------------------
# Main
# ----------------------------

def main():
    hook()
    dolphin_hwnd = find_dolphin_hwnd()
    if not dolphin_hwnd:
        print("Dolphin not found.")
        return

    overlay = Overlay(DISPLAY)
    overlay_hwnd = overlay.init_pygame()
    win32gui.SetWindowLong(overlay_hwnd, win32con.GWL_HWNDPARENT, dolphin_hwnd)
    clock = pygame.time.Clock()

    motion_filter = MotionFilter()

    scanner = ProjectileScanner()
    print("Running initial projectile signature scan…")
    scanner.scan()

    total_nodes = len(PROJECTILE_POOLS) * PROJECTILE_NODE_COUNT
    node_tracker = ProjectileNodeTracker(total_nodes)

    _last_char_ids: Dict[str, int] = {}

    running = True
    while running:
        w, h = sync_overlay_to_dolphin(dolphin_hwnd, overlay_hwnd)
        overlay.on_resize(w, h)

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_F1:
                    overlay.debug_axes = not overlay.debug_axes
                elif event.key == pygame.K_F2:
                    print("F2: re-scanning projectile signatures…")
                    scanner.scan()
                elif event.key == pygame.K_F3:
                    scanner.dump()

        # Detect character changes and rescan
        for name, base in SLOT_BASES.items():
            cid = rd32(base + OFF_CHAR_ID) or 0
            if _last_char_ids.get(name) != cid:
                print(f"[CharChange] {name} char_id {_last_char_ids.get(name)} -> {cid}, rescanning…")
                _last_char_ids[name] = cid
                scanner.scan()
                break

        camx, camy, camz, camw = read_camera_pos(CAMERA)
        if USE_LIVE_CAMERA:
            overlay.cam_x = camx
            overlay.cam_y = camy
            overlay.cam_z = camz

        _slot_filter = _read_slot_filter()

        # Update projectile node tracker every frame
        update_projectile_nodes(node_tracker, scanner)

        overlay.clear()
        overlay.draw_debug_axes()

        # Slot hitboxes
        counts = {}
        for name, base in SLOT_BASES.items():
            if not _slot_filter.get(name, True):
                continue
            boxes = read_hitboxes(base, HITBOX)
            active = 0
            palette = COLORS.get(name, [(255, 255, 255)])
            for i, (x, y, r, flag) in enumerate(boxes):
                if not motion_filter.update(name, i, x, y, r):
                    continue
                if r > 0.001:
                    active += 1
                    base_color = palette[i % len(palette)]
                    is_active = (flag == 0x53)
                    overlay.draw_hitbox(x, y, 0, r, base_color, f"{name}[{i}]", is_active=is_active)
            counts[name] = active

        # Projectile nodes — drawn from tracker, despawn after 6f inactivity
        for state in node_tracker.visible_nodes():
            overlay.draw_hitbox(
                state.x, state.y + PROJECTILE_Y_OFFSET, state.z,
                state.r * PROJECTILE_RADIUS_SCALE,
                COL_PROJ, "PRJ", is_active=True,
            )

        overlay.draw_hud(counts, motion_filter, scanner)
        overlay.present()
        clock.tick(DISPLAY.fps)

    pygame.quit()


if __name__ == "__main__":
    main()