# main.py
import os
import csv
import time
import threading

import pygame
from training_flags import read_training_flags
from debug_panel import read_debug_flags, draw_debug_overlay

from dolphin_io import hook, rd8, rd32, wd8
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
from constants import (
    SLOTS,
    CHAR_NAMES,
    OFF_MAX_HP,
    OFF_CUR_HP,
    OFF_AUX_HP,
    POSX_OFF,
    OFF_CHAR_ID,
    ATT_ID_OFF_PRIMARY,
    ATT_ID_OFF_SECOND,
    CTRL_WORD_OFF,
    FLAG_062,
    FLAG_063,
    FLAG_072,
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

# optional deep scan (the hitbox-augmented one)
try:
    import scan_normals_all
    HAVE_SCAN_NORMALS = True
    from scan_normals_all import ANIM_MAP as SCAN_ANIM_MAP
except Exception:
    scan_normals_all = None
    HAVE_SCAN_NORMALS = False
    SCAN_ANIM_MAP = {}

# NEW: Import the editable frame data window
try:
    from editable_frame_data_gui import open_editable_frame_data_window
    HAVE_EDITABLE_GUI = True
except ImportError:
    HAVE_EDITABLE_GUI = False
    print("WARNING: editable_frame_data_gui not available")

TARGET_FPS = 60
DAMAGE_EVERY_FRAMES = 3
ADV_EVERY_FRAMES = 2
SCAN_MIN_INTERVAL_SEC = 180.0

PANEL_SLIDE_DURATION = 0.35
PANEL_FLASH_FRAMES = 12

# offsets for "real" baroque (relative to fighter base)
HP32_OFF = 0x28
POOL32_OFF = 0x2C


# Same addresses as a mapping used by the debug overlay

def safe_read_fighter(base, yoff):
    """
    Wrap read_fighter(base, yoff) but fall back to a generic reader when
    read_fighter() returns None (e.g., giants like Gold Lightan / PTX).

    Returns a fighter snapshot dict or None if the base is really bad.
    """
    # First try the original implementation.
    snap = read_fighter(base, yoff)
    if snap:
        return snap

    # DEBUG: If we got here, read_fighter failed
    print(f"[safe_read_fighter] read_fighter failed for base=0x{base:08X}, trying fallback")

    # Fallback path: read key fields directly by offsets.
    try:
        max_hp = rd32(base + OFF_MAX_HP)
        cur_hp = rd32(base + OFF_CUR_HP)
        aux_hp = rd32(base + OFF_AUX_HP)

        pos_x = rd32(base + POSX_OFF)
        pos_y = rd32(base + yoff)

        attA = rd8(base + ATT_ID_OFF_PRIMARY)
        attB = rd8(base + ATT_ID_OFF_SECOND)

        ctrl = rd32(base + CTRL_WORD_OFF)

        flag062 = rd8(base + FLAG_062)
        flag063 = rd8(base + FLAG_063)
        flag072 = rd8(base + FLAG_072)
    except Exception as e:
        print(f"[safe_read_fighter] Exception reading fields: {e}")
        return None

    # DEBUG: Print what we read
    print(f"[safe_read_fighter] max_hp={max_hp} cur_hp={cur_hp}")

    # Basic sanity: non-zero, not totally insane.
    # Allow very large HP so giants don't get filtered out.
    if max_hp <= 0 or max_hp > 1000000:
        print(f"[safe_read_fighter] HP sanity check failed: max_hp={max_hp}")
        return None

    # Try to get character ID and name now (THIS IS THE FIX)
    # Character ID is 32-bit, not 8-bit! (same as scan_normals_all.py)
    try:
        char_id = rd32(base + OFF_CHAR_ID)
    except Exception:
        char_id = None
    
    print(f"[safe_read_fighter] char_id={char_id}, name={CHAR_NAMES.get(char_id)}")
    
    char_name = "Unknown"
    if char_id is not None and char_id != 0:
        char_name = CHAR_NAMES.get(char_id, f"ID_{char_id}")
    
    return {
        "max": max_hp,
        "cur": cur_hp,
        "aux": aux_hp,
        "pos_x": pos_x,
        "pos_y": pos_y,
        "attA": attA,
        "attB": attB,
        "ctrl": ctrl,
        "flag062": flag062,
        "flag063": flag063,
        "flag072": flag072,
        "id": char_id,
        "name": char_name,
        # old HUD expects this key; we can leave it None for giants.
        "hp_pool_byte": None,
    }


class ScanNormalsWorker(threading.Thread):
    """Background worker so big MEM2 scans don't lag pygame."""
    def __init__(self):
        super().__init__(daemon=True)
        self._want = threading.Event()
        self._lock = threading.Lock()
        self._last = None
        self._last_ts = 0.0

    def run(self):
        while True:
            self._want.wait()
            self._want.clear()
            if not HAVE_SCAN_NORMALS:
                continue
            try:
                res = scan_normals_all.scan_once()
                with self._lock:
                    self._last = res
                    self._last_ts = time.time()
            except Exception as e:
                print("scan worker failed:", e)

    def request(self):
        """Signal the worker to perform a scan."""
        self._want.set()

    def get_latest(self):
        """Return (result, timestamp) of the last completed scan."""
        with self._lock:
            return self._last, self._last_ts


def _normalize_char_key(s: str) -> str:
    s = s.strip().lower()
    for ch in (" ", "-", "_", "."):
        s = s.replace(ch, "")
    return s


PORTRAIT_ALIASES = {}


def load_portrait_placeholder():
    """
    Fallback portrait if a character portrait is missing.
    """
    path = os.path.join("assets", "portraits", "placeholder.png")
    if os.path.exists(path):
        try:
            return pygame.image.load(path).convert_alpha()
        except Exception:
            pass

    surf = pygame.Surface((64, 64), pygame.SRCALPHA)
    surf.fill((80, 80, 80, 255))
    pygame.draw.rect(surf, (140, 140, 140, 255), surf.get_rect(), 2)
    return surf


def load_portraits_from_dir(dirpath: str):
    """
    Load all PNG portraits from a directory into a dict keyed by a
    normalized, alias-resolved character name.
    """
    portraits = {}
    if not os.path.isdir(dirpath):
        return portraits

    for fname in os.listdir(dirpath):
        if not fname.lower().endswith(".png"):
            continue
        full = os.path.join(dirpath, fname)
        stem = os.path.splitext(fname)[0]
        key = _normalize_char_key(stem)
        try:
            img = pygame.image.load(full).convert_alpha()
            portraits[key] = img
        except Exception as e:
            print("portrait load failed for", full, e)
    return portraits


def get_portrait_for_snap(snap, portraits, placeholder):
    """
    Resolve the correct portrait Surface for a fighter snapshot.
    """
    if not snap:
        return None
    cname = snap.get("name")
    if not cname:
        return placeholder
    norm = _normalize_char_key(cname)
    norm = PORTRAIT_ALIASES.get(norm, norm)
    return portraits.get(norm, placeholder)


def init_pygame():
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
    pygame.display.set_caption("TvC Live HUD / Frame Probe")
    return screen, font, smallfont


# ------------------------------------------------------------
# Frame-data GUI (Tk) — legacy viewer with HB column
# ------------------------------------------------------------
def _open_frame_data_window_thread(slot_label, target_slot):
    """
    Legacy Tk-based frame data viewer.
    The editable version lives in editable_frame_data_gui and is used
    by open_frame_data_window when available.
    """
    try:
        import tkinter as tk
        from tkinter import ttk
    except Exception:
        print("tkinter not available")
        return

    cname = target_slot.get("char_name", "—")
    root = tk.Tk()
    root.title(f"Frame data: {slot_label} ({cname})")

    cols = (
        "move", "kind", "damage", "meter",
        "startup", "active", "hitstun", "blockstun", "hitstop",
        "advH", "advB",
        "hb",
        "abs",
    )

    frame = ttk.Frame(root)
    frame.pack(fill="both", expand=True)

    tree = ttk.Treeview(frame, columns=cols, show="headings", height=30)
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=vsb.set)

    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")

    frame.rowconfigure(0, weight=1)
    frame.columnconfigure(0, weight=1)

    headers = [
        ("move", "Move"),
        ("kind", "Kind"),
        ("damage", "Dmg"),
        ("meter", "Meter"),
        ("startup", "Start"),
        ("active", "Active"),
        ("hitstun", "HS"),
        ("blockstun", "BS"),
        ("hitstop", "Stop"),
        ("advH", "advH"),
        ("advB", "advB"),
        ("hb", "HB"),
        ("abs", "ABS"),
    ]
    for col_id, txt in headers:
        tree.heading(col_id, text=txt)

    # widths
    tree.column("move", width=180, anchor="w")
    tree.column("kind", width=60, anchor="w")
    tree.column("damage", width=65, anchor="center")
    tree.column("meter", width=55, anchor="center")
    tree.column("startup", width=55, anchor="center")
    tree.column("active", width=70, anchor="center")
    tree.column("hitstun", width=45, anchor="center")
    tree.column("blockstun", width=45, anchor="center")
    tree.column("hitstop", width=50, anchor="center")
    tree.column("advH", width=55, anchor="center")
    tree.column("advB", width=55, anchor="center")
    tree.column("hb", width=95, anchor="center")
    tree.column("abs", width=110, anchor="w")

    # sort + dedupe like HUD
    moves_sorted = sorted(
        target_slot.get("moves", []),
        key=lambda m: (
            m.get("id") is None,
            m.get("id", 0xFFFF),
            m.get("abs", 0xFFFFFFFF),
        ),
    )

    try:
        from scan_normals_all import ANIM_MAP as _ANIM_MAP_FOR_GUI
    except Exception:
        _ANIM_MAP_FOR_GUI = {}

    seen_named = set()
    deduped = []

    for mv in moves_sorted:
        aid = mv.get("id")
        if aid is None:
            deduped.append(mv)
            continue

        name = _ANIM_MAP_FOR_GUI.get(aid, f"anim_{aid:02X}")
        if not name.startswith("anim_") and "?" not in name:
            if aid in seen_named:
                continue
            seen_named.add(aid)
        deduped.append(mv)

    def _fmt_stun(v):
        if v is None:
            return ""
        if v == 0x0C:
            return "10"
        if v == 0x0F:
            return "15"
        if v == 0x11:
            return "17"
        if v == 0x15:
            return "21"
        return str(v)

    for mv in deduped:
        aid = mv.get("id")
        move_name = (
            _ANIM_MAP_FOR_GUI.get(aid, f"anim_{aid:02X}")
            if aid is not None else "anim_--"
        )

        a_s = mv.get("active_start")
        a_e = mv.get("active_end")
        if a_s is not None and a_e is not None:
            active_txt = f"{a_s}-{a_e}"
        elif a_e is not None:
            active_txt = str(a_e)
        else:
            active_txt = ""

        # format HB
        hb_x = mv.get("hb_x")
        hb_y = mv.get("hb_y")
        if hb_x is not None or hb_y is not None:
            if hb_x is None:
                hb_txt = f"-x{hb_y:.1f}"
            elif hb_y is None:
                hb_txt = f"{hb_x:.1f}x-"
            else:
                hb_txt = f"{hb_x:.1f}x{hb_y:.1f}"
        else:
            hb_txt = ""

        tree.insert(
            "",
            "end",
            values=(
                move_name,
                mv.get("kind", ""),
                "" if mv.get("damage") is None else str(mv.get("damage")),
                "" if mv.get("meter") is None else str(mv.get("meter")),
                "" if a_s is None else str(a_s),
                active_txt,
                _fmt_stun(mv.get("hitstun")),
                _fmt_stun(mv.get("blockstun")),
                "" if mv.get("hitstop") is None else str(mv.get("hitstop")),
                "" if mv.get("adv_hit") is None else f"{mv.get('adv_hit'):+d}",
                "" if mv.get("adv_block") is None else f"{mv.get('adv_block'):+d}",
                hb_txt,
                f"0x{mv.get('abs', 0):08X}" if mv.get("abs") else "",
            ),
        )

    root.mainloop()


def open_frame_data_window(slot_label, scan_data):
    """
    Open frame data window (editable when available, otherwise legacy Tk).
    """
    if HAVE_EDITABLE_GUI:
        open_editable_frame_data_window(slot_label, scan_data)
    else:
        print(f"Editable GUI not available for {slot_label}")
        if not scan_data:
            return

        target = None
        for s in scan_data:
            if s.get("slot_label") == slot_label:
                target = s
                break
        if not target:
            return

        t = threading.Thread(
            target=_open_frame_data_window_thread,
            args=(slot_label, target),
            daemon=True,
        )
        t.start()


def compute_layout(w, h, snaps):
    """
    Compute all main HUD rects (panels, activity bar, events, debug, scan).
    Adjusts layout when giants are detected (they have no partners).
    """
    pad = 10
    gap_x = 20
    gap_y = 10

    panel_w = (w - pad * 2 - gap_x) // 2
    panel_h = 155

    row1_y = pad
    row2_y = row1_y + panel_h + gap_y

    # Check for giants (IDs 11 = Gold Lightan, 22 = PTX-40A)
    GIANT_IDS = {11, 22}
    p1_is_giant = snaps.get("P1-C1", {}).get("id") in GIANT_IDS
    p2_is_giant = snaps.get("P2-C1", {}).get("id") in GIANT_IDS

    # Default positions
    r_p1c1 = pygame.Rect(pad, row1_y, panel_w, panel_h)
    r_p2c1 = pygame.Rect(pad + panel_w + gap_x, row1_y, panel_w, panel_h)
    r_p1c2 = pygame.Rect(pad, row2_y, panel_w, panel_h)
    r_p2c2 = pygame.Rect(pad + panel_w + gap_x, row2_y, panel_w, panel_h)

    # If P1 is a giant, move P2-C1 down to row 2 (where P2-C2 would be)
    if p1_is_giant:
        r_p2c1 = pygame.Rect(pad + panel_w + gap_x, row2_y, panel_w, panel_h)
        # P1-C2 stays in row 2 left but will be hidden/empty
    
    # If P2 is a giant, move P1-C1 stays top left, but adjust P1-C2 if needed
    if p2_is_giant:
        # P2-C1 stays top right, P2-C2 will be hidden/empty
        pass

    act_rect = pygame.Rect(pad, r_p1c2.bottom + 30, w - pad * 2, 32)

    events_y = act_rect.bottom + 8
    events_h = 150

    # split row into left (events) and right (debug) halves
    row_w = w - pad * 2
    half_w = (row_w - gap_x) // 2

    events_rect = pygame.Rect(pad, events_y, half_w, events_h)
    debug_rect = pygame.Rect(pad + half_w + gap_x, events_y, half_w, events_h)

    scan_y = events_rect.bottom + 8
    scan_h = max(90, h - scan_y - pad)
    scan_rect = pygame.Rect(pad, scan_y, w - pad * 2, scan_h)

    return {
        "p1c1": r_p1c1,
        "p2c1": r_p2c1,
        "p1c2": r_p1c2,
        "p2c2": r_p2c2,
        "act": act_rect,
        "events": events_rect,
        "debug": debug_rect,
        "scan": scan_rect,
        "p1_is_giant": p1_is_giant,
        "p2_is_giant": p2_is_giant,
    }


def reassign_slots_for_giants(snaps):
    """
    When giants are present, the game loads 3 character tables instead of 4.
    This function reassigns them to the correct logical slots.
    
    Rules:
    - If P1-C1 is giant (11/22): other 2 chars go to P2-C1, P2-C2
    - If P2-C1 is giant (11/22): other 2 chars go to P1-C1, P1-C2
    - If both P1-C1 and P2-C1 are giants: just P1-C1, P2-C1
    """
    GIANT_IDS = {11, 22}
    
    # Get current slot assignments
    p1c1 = snaps.get("P1-C1")
    p1c2 = snaps.get("P1-C2")
    p2c1 = snaps.get("P2-C1")
    p2c2 = snaps.get("P2-C2")
    
    # Check which slots have giants
    p1c1_is_giant = p1c1 and p1c1.get("id") in GIANT_IDS
    p2c1_is_giant = p2c1 and p2c1.get("id") in GIANT_IDS
    
    # Case 1: Both are giants - no reassignment needed
    if p1c1_is_giant and p2c1_is_giant:
        # Remove any partner data
        snaps.pop("P1-C2", None)
        snaps.pop("P2-C2", None)
        return snaps
    
    # Case 2: P1 is giant, P2 has a team
    if p1c1_is_giant:
        # P1-C2 should be empty, other slots should be P2 team
        reassigned = {
            "P1-C1": p1c1,
        }
        
        # The other 2 non-giant characters go to P2-C1 and P2-C2
        non_giant_chars = []
        if p1c2 and p1c2.get("id") not in GIANT_IDS:
            non_giant_chars.append(p1c2)
        if p2c1 and p2c1.get("id") not in GIANT_IDS:
            non_giant_chars.append(p2c1)
        if p2c2 and p2c2.get("id") not in GIANT_IDS:
            non_giant_chars.append(p2c2)
        
        # Assign them to P2 slots
        if len(non_giant_chars) >= 1:
            reassigned["P2-C1"] = non_giant_chars[0]
            reassigned["P2-C1"]["slotname"] = "P2-C1"
        if len(non_giant_chars) >= 2:
            reassigned["P2-C2"] = non_giant_chars[1]
            reassigned["P2-C2"]["slotname"] = "P2-C2"
        
        return reassigned
    
    # Case 3: P2 is giant, P1 has a team
    if p2c1_is_giant:
        # P2-C2 should be empty, other slots should be P1 team
        reassigned = {
            "P2-C1": p2c1,
        }
        
        # The other 2 non-giant characters go to P1-C1 and P1-C2
        non_giant_chars = []
        if p1c1 and p1c1.get("id") not in GIANT_IDS:
            non_giant_chars.append(p1c1)
        if p1c2 and p1c2.get("id") not in GIANT_IDS:
            non_giant_chars.append(p1c2)
        if p2c2 and p2c2.get("id") not in GIANT_IDS:
            non_giant_chars.append(p2c2)
        
        # Assign them to P1 slots
        if len(non_giant_chars) >= 1:
            reassigned["P1-C1"] = non_giant_chars[0]
            reassigned["P1-C1"]["slotname"] = "P1-C1"
        if len(non_giant_chars) >= 2:
            reassigned["P1-C2"] = non_giant_chars[1]
            reassigned["P1-C2"]["slotname"] = "P1-C2"
        
        return reassigned
    
    # Case 4: No giants - return as-is
    return snaps
def main():
    print("HUD: waiting for Dolphin…")
    hook()
    print("HUD: hooked Dolphin.")

    # Moves table and global flags map
    move_map, global_map = load_move_map(GENERIC_MAPPING_CSV, PAIR_MAPPING_CSV)

    screen, font, smallfont = init_pygame()
    clock = pygame.time.Clock()

    placeholder_portrait = load_portrait_placeholder()
    portraits = load_portraits_from_dir(os.path.join("assets", "portraits"))
    print(f"HUD: loaded {len(portraits)} portraits.")

    # background scan worker
    scan_worker = ScanNormalsWorker() if HAVE_SCAN_NORMALS else None
    if scan_worker:
        scan_worker.start()

    last_scan_normals = None
    last_scan_time = 0.0

    last_base_by_slot = {}
    y_off_by_base = {}
    prev_hp = {}
    pool_baseline = {}

    last_move_anim_id = {}
    last_char_by_slot = {}
    render_snap_by_slot = {}
    render_portrait_by_slot = {}

    panel_anim = {}
    anim_queue_after_scan = set()

    panel_btn_flash = {s: 0 for (s, _, _) in SLOTS}

    local_scan = RedHealthScanner()
    global_scan = GlobalRedScanner()

    manual_scan_requested = False
    need_rescan_normals = False

    last_adv_display = ""
    pending_hits = []
    frame_idx = 0
    running = True

    # Debug overlay state
    debug_overlay = False
    debug_btn_rect = pygame.Rect(0, 0, 0, 0)
    debug_click_areas = {}
    debug_scroll_offset = 0
    debug_max_scroll = 0

    # HypeTrigger timed restore state
    hype_restore_addr = None
    hype_restore_ts = 0.0
    hype_restore_orig = 0

    # SpecialPopup timed restore state
    special_restore_addr = None
    special_restore_ts = 0.0
    special_restore_orig = 0

    while running:
        now = time.time()
        t_ms = pygame.time.get_ticks()

        mouse_clicked_pos = None

        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.VIDEORESIZE:
                screen = pygame.display.set_mode(ev.size, pygame.RESIZABLE)
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_F5:
                    manual_scan_requested = True
            elif ev.type == pygame.MOUSEBUTTONDOWN:
                if ev.button == 1:
                    mouse_clicked_pos = ev.pos
                # wheel emulation on some backends
                elif ev.button in (4, 5) and debug_overlay:
                    if ev.button == 4 and debug_scroll_offset > 0:
                        debug_scroll_offset -= 1
                    elif ev.button == 5 and debug_scroll_offset < debug_max_scroll:
                        debug_scroll_offset += 1
            elif ev.type == pygame.MOUSEWHEEL and debug_overlay:
                # pygame 2 MOUSEWHEEL event: ev.y > 0 up, < 0 down
                if ev.y > 0 and debug_scroll_offset > 0:
                    debug_scroll_offset -= 1
                elif ev.y < 0 and debug_scroll_offset < debug_max_scroll:
                    debug_scroll_offset += 1

        # pull latest scan from worker
        if scan_worker:
            res, ts = scan_worker.get_latest()
            if res is not None and ts >= last_scan_time:
                last_scan_normals = res
                last_scan_time = ts

        # resolve bases
        resolved_slots = []
        for slotname, ptr_addr, teamtag in SLOTS:
            base, changed = RESOLVER.resolve_base(ptr_addr)
            if base and last_base_by_slot.get(ptr_addr) != base:
                last_base_by_slot[ptr_addr] = base
                METER_CACHE.drop(base)
                y_off_by_base[base] = pick_posy_off_no_jump(base)
            resolved_slots.append((slotname, teamtag, base))

        # read meters
        p1c1_base = next(
            (b for n, t, b in resolved_slots if n == "P1-C1" and b), None
        )
        p2c1_base = next(
            (b for n, t, b in resolved_slots if n == "P2-C1" and b), None
        )
        meter_p1 = read_meter(p1c1_base)
        meter_p2 = read_meter(p2c1_base)

        snaps = {}
        for slotname, teamtag, base in resolved_slots:
            if not base:
                if last_char_by_slot.get(slotname):
                    anim_queue_after_scan.add((slotname, "fadeout"))
                    last_char_by_slot[slotname] = None
                    need_rescan_normals = True
                continue

            yoff = y_off_by_base.get(base, 0xF4)
            snap = safe_read_fighter(base, yoff)
            if not snap:
                continue

            # Always store base for downstream systems
            snap["base"] = base
            snap["teamtag"] = teamtag
            snap["slotname"] = slotname

            # True character ID from the fighter struct:
            # base + OFF_CHAR_ID = 1-byte ID (Ryu=0x0C, Chun=0x0D, Lightan=0x0B, etc.)
            try:
                true_id = rd32(base + OFF_CHAR_ID)
            except Exception:
                true_id = None

            if true_id not in (None, 0):
                snap["id"] = true_id

                # If we have a name for this ID, override whatever read_fighter gave us.
                name_from_id = CHAR_NAMES.get(true_id)
                print(f"[main] slot={slotname} base=0x{base:08X} true_id={true_id} name_from_id={name_from_id} current_name={snap.get('name')}")
                if name_from_id:
                    snap["name"] = name_from_id

            # meter display string
            if slotname == "P1-C1":
                snap["meter_str"] = str(meter_p1) if meter_p1 is not None else "--"
            elif slotname == "P2-C1":
                snap["meter_str"] = str(meter_p2) if meter_p2 is not None else "--"
            else:
                snap["meter_str"] = "--"

            cur_anim = snap.get("attA") or snap.get("attB")

            # Use possibly updated name + ID now that we forced true_id in
            char_name = snap.get("name")
            csv_char_id = CHAR_ID_CORRECTION.get(char_name, snap.get("id"))

            # First try the new ID map CSV (decimal ID -> name, char-aware)
            mv_label = lookup_move_name(cur_anim, csv_char_id)

            # Fallback to your existing move_map / global_map system
            if not mv_label:
                mv_label = move_label_for(cur_anim, csv_char_id, move_map, global_map)

            snap["mv_label"] = mv_label
            snap["mv_id_display"] = cur_anim
            snap["csv_char_id"] = csv_char_id

            # track change
            prev_anim = last_move_anim_id.get(base)
            if cur_anim and cur_anim != prev_anim:
                last_move_anim_id[base] = cur_anim
            else:
                last_move_anim_id[base] = cur_anim

            # legacy pool byte %   (unchanged from your version)
            pool_byte = snap.get("hp_pool_byte")
            if pool_byte is not None:
                prev_max = pool_baseline.get(base, 0)
                if pool_byte > prev_max:
                    pool_baseline[base] = pool_byte
                max_pool = pool_baseline.get(base, 1)
                snap["pool_pct"] = (pool_byte / max_pool) * 100.0 if max_pool else 0.0
            else:
                snap["pool_pct"] = 0.0

            # REAL baroque: hp32 vs pool32
            max_hp_stat = snap.get("max") or 0
            hp32 = rd32(base + HP32_OFF) or 0
            pool32 = rd32(base + POOL32_OFF) or 0
            ready_local = False
            red_amt = 0
            red_pct_max = 0.0    # ONLY % vs max HP, current removed

            if hp32 and pool32 and hp32 != pool32:
                ready_local = True
                bigger = max(hp32, pool32)
                smaller = min(hp32, pool32)
                red_amt = bigger - smaller

                # percent of character max HP
                if max_hp_stat:
                    red_pct_max = (red_amt / float(max_hp_stat)) * 100.0

            snap["baroque_local_hp32"] = hp32
            snap["baroque_local_pool32"] = pool32
            snap["baroque_ready_local"] = ready_local
            snap["baroque_red_amt"] = red_amt
            snap["baroque_red_pct_max"] = red_pct_max

            # inputs: now taken from config.INPUT_MONITOR_ADDRS
            if slotname == "P1-C1":
                inputs_struct = {}
                for key, addr in INPUT_MONITOR_ADDRS.items():
                    v = rd8(addr)
                    inputs_struct[key] = 0 if v is None else v
                snap["inputs"] = inputs_struct
            else:
                snap["inputs"] = {}

            snaps[slotname] = snap

            # new character? animate + rescan
            if last_char_by_slot.get(slotname) != snap.get("name"):
                last_char_by_slot[slotname] = snap.get("name")
                anim_queue_after_scan.add((slotname, "fadein"))
                need_rescan_normals = True

            render_snap_by_slot[slotname] = snap
            render_portrait_by_slot[slotname] = get_portrait_for_snap(
                snap, portraits, placeholder_portrait
            )
        # Reassign slots when giants are present
        snaps = reassign_slots_for_giants(snaps)  
        if frame_idx % DAMAGE_EVERY_FRAMES == 0:
            REACTION_STATES = {48, 64, 65, 66, 73, 80, 81, 82, 90, 92, 95, 96, 97}
            for vic_slot, vic_snap in snaps.items():
                vic_move_id = vic_snap.get("attA") or vic_snap.get("attB")
                if vic_move_id not in REACTION_STATES:
                    continue
                vic_team = vic_snap["teamtag"]
                attackers = [s for s in snaps.values() if s["teamtag"] != vic_team]
                if not attackers:
                    continue

                # nearest attacker
                best_d2 = None
                atk_snap = None
                for cand in attackers:
                    d2v = dist2(vic_snap, cand)
                    if best_d2 is None or d2v < best_d2:
                        best_d2 = d2v
                        atk_snap = cand
                if not atk_snap:
                    continue

                atk_move_id = atk_snap.get("attA") or atk_snap.get("attB")
                atk_move_label = atk_snap.get("mv_label")

                ADV_TRACK.start_contact(
                    atk_snap["base"],
                    vic_snap["base"],
                    frame_idx,
                    atk_move_id,
                    vic_move_id,
                )

                base = vic_snap["base"]
                hp_now = vic_snap["cur"]
                hp_prev = prev_hp.get(base, hp_now)
                prev_hp[base] = hp_now
                dmg = hp_prev - hp_now
                if dmg >= MIN_HIT_DAMAGE:
                    log_engaged(atk_snap, vic_snap, frame_idx)
                    log_hit(
                        atk_snap,
                        vic_snap,
                        dmg,
                        frame_idx,
                        atk_move_label,
                        atk_move_id,
                    )

        # frame advantage
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
                    atk_move_id = atk_snap.get("attA") or atk_snap.get("attB")
                    vic_move_id = vic_snap.get("attA") or vic_snap.get("attB")
                    ADV_TRACK.update_pair(
                        atk_snap["base"],
                        vic_snap["base"],
                        frame_idx,
                        atk_move_id,
                        vic_move_id,
                    )
            freshest = ADV_TRACK.get_freshest_final_info()
            if freshest:
                atk_b, vic_b, plusf, fin_frame = freshest
                if abs(plusf) <= 64:
                    atk_slot = next(
                        (s for s in snaps.values() if s["base"] == atk_b), None
                    )
                    vic_slot = next(
                        (s for s in snaps.values() if s["base"] == vic_b), None
                    )
                    if atk_slot and vic_slot:
                        last_adv_display = (
                            f"{atk_slot['slotname']}({atk_slot['name']}) vs "
                            f"{vic_slot['slotname']}({vic_slot['name']}): "
                            f"{plusf:+.1f}f"
                        )
                        log_frame_advantage(atk_slot, vic_slot, plusf)
                    else:
                        last_adv_display = f"Frame adv: {plusf:+.1f}f"

        # draw
        screen.fill(COL_BG)
        w, h = screen.get_size()
        layout = compute_layout(w, h, snaps)


        def anim_rect_and_alpha(slot_label, base_rect):
            anim = panel_anim.get(slot_label)
            if not anim:
                return base_rect, 255

            if anim["to_y"] is None:
                anim["to_y"] = base_rect.y
            if anim["from_y"] is None:
                anim["from_y"] = base_rect.y

            t = now - anim["start"]
            dur = anim["dur"]

            if t <= 0:
                frac = 0.0
            elif t >= dur:
                frac = 1.0
            else:
                frac = t / dur

            y = anim["from_y"] + (anim["to_y"] - anim["from_y"]) * frac
            alpha = int(anim["from_a"] + (anim["to_a"] - anim["from_a"]) * frac)

            if frac >= 1.0:
                if anim["to_a"] == 0:
                    render_snap_by_slot.pop(slot_label, None)
                    render_portrait_by_slot.pop(slot_label, None)
                panel_anim.pop(slot_label, None)

            r = base_rect.copy()
            r.y = int(y)
            return r, max(0, min(255, alpha))

        r_p1c1, a_p1c1 = anim_rect_and_alpha("P1-C1", layout["p1c1"])
        r_p2c1, a_p2c1 = anim_rect_and_alpha("P2-C1", layout["p2c1"])
        r_p1c2, a_p1c2 = anim_rect_and_alpha("P1-C2", layout["p1c2"])
        r_p2c2, a_p2c2 = anim_rect_and_alpha("P2-C2", layout["p2c2"])

        def blit_panel_with_button(panel_rect, slot_label, alpha, header):
            snap = render_snap_by_slot.get(slot_label)
            portrait = render_portrait_by_slot.get(
                slot_label, placeholder_portrait
            )
            waiting = any(
                (slot_label == s and kind in ("fadein", "fadeout"))
                for (s, kind) in anim_queue_after_scan
            )

            surf = pygame.Surface(
                (panel_rect.width, panel_rect.height), pygame.SRCALPHA
            )
            draw_panel_classic(
                surf,
                surf.get_rect(),
                snap,
                portrait,
                font,
                smallfont,
                header,
                t_ms,
            )

            # button
            btn_w, btn_h = 110, 20
            btn_x = panel_rect.width - btn_w - 6
            btn_y = panel_rect.height - btn_h - 6
            btn_rect_local = pygame.Rect(btn_x, btn_y, btn_w, btn_h)

            flash_left = panel_btn_flash.get(slot_label, 0)
            if flash_left > 0:
                base_col = (90, 140, 255)
                border_col = (255, 255, 255)
            else:
                base_col = (40, 40, 40)
                border_col = (180, 180, 180)

            pygame.draw.rect(surf, base_col, btn_rect_local, border_radius=3)
            pygame.draw.rect(
                surf, border_col, btn_rect_local, 1, border_radius=3
            )
            label_surf = smallfont.render(
                "Show frame data", True, (220, 220, 220)
            )
            surf.blit(label_surf, (btn_x + 6, btn_y + 2))

            if flash_left > 0:
                pygame.draw.rect(
                    surf,
                    (255, 255, 255),
                    btn_rect_local.inflate(4, 4),
                    2,
                    border_radius=4,
                )

            surf.set_alpha(255 if waiting else alpha)
            screen.blit(surf, (panel_rect.x, panel_rect.y))

            return pygame.Rect(
                panel_rect.x + btn_x, panel_rect.y + btn_y, btn_w, btn_h
            )





        # Only draw panels that should be visible (skip giant partners)
        btn_p1c1 = blit_panel_with_button(r_p1c1, "P1-C1", a_p1c1, "P1-C1")
        btn_p2c1 = blit_panel_with_button(r_p2c1, "P2-C1", a_p2c1, "P2-C1")
        
        # Hide P1-C2 if P1 is a giant (check if P1-C2 slot has valid current data in snaps)
        if not layout.get("p1_is_giant") and "P1-C2" in snaps:
            btn_p1c2 = blit_panel_with_button(r_p1c2, "P1-C2", a_p1c2, "P1-C2")
        else:
            btn_p1c2 = pygame.Rect(0, 0, 0, 0)
        
        # Hide P2-C2 if P2 is a giant (check if P2-C2 slot has valid current data in snaps)
        if not layout.get("p2_is_giant") and "P2-C2" in snaps:
            btn_p2c2 = blit_panel_with_button(r_p2c2, "P2-C2", a_p2c2, "P2-C2")
        else:
            btn_p2c2 = pygame.Rect(0, 0, 0, 0)
        draw_activity(screen, layout["act"], font, last_adv_display)

        # left half: events
        draw_event_log(screen, layout["events"], font, smallfont)

        # right half: debug panel
        debug_rect = layout["debug"]

        if debug_overlay:
            # Combine normal debug flags + training flags
            dbg_values = read_debug_flags() + read_training_flags()
        else:
            dbg_values = []

        debug_click_areas, debug_max_scroll = draw_debug_overlay(
            screen, debug_rect, smallfont, dbg_values, debug_scroll_offset
        )

        # draw the Debug ON/OFF button at top-left of debug panel
        dbg_btn_w, dbg_btn_h = 120, 24
        dbg_btn_x = debug_rect.x + 8
        dbg_btn_y = debug_rect.y + 4
        debug_btn_rect = pygame.Rect(dbg_btn_x, dbg_btn_y, dbg_btn_w, dbg_btn_h)

        pygame.draw.rect(screen, (40, 40, 70), debug_btn_rect, border_radius=4)
        pygame.draw.rect(
            screen, (180, 180, 220), debug_btn_rect, 1, border_radius=4
        )

        btn_label = "Debug: ON" if debug_overlay else "Debug: OFF"
        label_surf = smallfont.render(btn_label, True, (230, 230, 230))
        screen.blit(label_surf, (dbg_btn_x + 6, dbg_btn_y + 4))

        # scan panel at the bottom
        draw_scan_normals(screen, layout["scan"], font, smallfont, last_scan_normals)

        pygame.display.flip()

        # clicks --------------------------------------------------------------
        if mouse_clicked_pos is not None:
            mx, my = mouse_clicked_pos

            def ensure_scan_now():
                nonlocal last_scan_normals, last_scan_time
                # try worker result first
                if last_scan_normals is not None:
                    return last_scan_normals
                # fallback: do synchronous scan
                if HAVE_SCAN_NORMALS:
                    try:
                        last_scan_normals = scan_normals_all.scan_once()
                        last_scan_time = time.time()
                        return last_scan_normals
                    except Exception as e:
                        print("sync scan failed:", e)
                        return None
                return None

            if btn_p1c1.collidepoint(mx, my):
                data = ensure_scan_now()
                if data:
                    open_frame_data_window("P1-C1", data)
                panel_btn_flash["P1-C1"] = PANEL_FLASH_FRAMES

            elif btn_p2c1.collidepoint(mx, my):
                data = ensure_scan_now()
                if data:
                    open_frame_data_window("P2-C1", data)
                panel_btn_flash["P2-C1"] = PANEL_FLASH_FRAMES

            elif btn_p1c2.collidepoint(mx, my):
                data = ensure_scan_now()
                if data:
                    open_frame_data_window("P1-C2", data)
                panel_btn_flash["P1-C2"] = PANEL_FLASH_FRAMES

            elif btn_p2c2.collidepoint(mx, my):
                data = ensure_scan_now()
                if data:
                    open_frame_data_window("P2-C2", data)
                panel_btn_flash["P2-C2"] = PANEL_FLASH_FRAMES

            elif debug_btn_rect and debug_btn_rect.collidepoint(mx, my):
                # Toggle debug overlay on/off and reset scroll
                debug_overlay = not debug_overlay
                debug_scroll_offset = 0

            else:
                # Clicks on individual debug flag lines
                # PauseOverlay toggle: 00 <-> 01
                entry = debug_click_areas.get("PauseOverlay")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        wd8(addr, 0x01 if cur == 0x00 else 0x00)

                # Training pause: 00 <-> 01
                entry = debug_click_areas.get("TrPause")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = 0x01 if cur == 0x00 else 0x00
                        wd8(addr, new)

                # Dummy meter: 00 -> 01 -> 02 -> 00 (normal / recovery / infinite)
                entry = debug_click_areas.get("DummyMeter")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = (cur + 1) % 3  # 0,1,2 cycle
                        wd8(addr, new)

                # CPU action: cycle 0..5 (stand, crouch, jump, sjump, CPU, player)
                entry = debug_click_areas.get("CpuAction")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = (cur + 1) % 6
                        wd8(addr, new)

                # CPU guard: 0->1->2->0 (none / auto / guard)
                entry = debug_click_areas.get("CpuGuard")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = (cur + 1) % 3
                        wd8(addr, new)

                # CPU pushblock: 00 <-> 01
                entry = debug_click_areas.get("CpuPushblock")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = 0x01 if cur == 0x00 else 0x00
                        wd8(addr, new)

                # CPU throw tech: 00 <-> 01
                entry = debug_click_areas.get("CpuThrowTech")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = 0x01 if cur == 0x00 else 0x00
                        wd8(addr, new)

                # Player meter (P1): 00 -> 01 -> 02 -> 00
                entry = debug_click_areas.get("P1Meter")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = (cur + 1) % 3
                        wd8(addr, new)

                # Player life: 00 <-> 01 (normal / infinite)
                entry = debug_click_areas.get("P1Life")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = 0x01 if cur == 0x00 else 0x00
                        wd8(addr, new)

                # Free Baroque: 00 <-> 01
                entry = debug_click_areas.get("FreeBaroque")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = 0x01 if cur == 0x00 else 0x00
                        wd8(addr, new)

                # Baroque percent: 0x00..0x0A => 0..100% in 10% steps
                entry = debug_click_areas.get("BaroquePct")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        if cur < 0x0A:
                            new = cur + 1
                        else:
                            new = 0x00
                        wd8(addr, new)

                # Attack data overlay: 00 <-> 01
                entry = debug_click_areas.get("AttackData")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = 0x01 if cur == 0x00 else 0x00
                        wd8(addr, new)

                # Input display overlay: 00 <-> 01
                entry = debug_click_areas.get("InputDisplay")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = 0x01 if cur == 0x00 else 0x00
                        wd8(addr, new)

                # CPU difficulty: 8 levels, step 0x20, wrap around
                entry = debug_click_areas.get("CpuDifficulty")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        level = (cur // 0x20) % 8  # 0..7
                        level = (level + 1) % 8
                        new = level * 0x20
                        wd8(addr, new)

                # Damage output: 00..03, wrap around
                entry = debug_click_areas.get("DamageOutput")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        cur = rd8(addr) or 0
                        new = (cur + 1) & 0x03
                        wd8(addr, new)

                # HypeTrigger: write 0x40 then restore to default 0x45 after 0.5 seconds
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
                        hype_restore_ts = now + 0.5

                # ComboStore[1]: write 0x41 to pop the combo counter
                entry = debug_click_areas.get("ComboStore[1]")
                if entry:
                    r, addr = entry
                    if r.collidepoint(mx, my):
                        wd8(addr, 0x41)

                # SpecialPopup: write 0x40, then restore to original (default 0x45) after 0.5s
                entry = debug_click_areas.get("SpecialPopup")
                if entry:
                    sp_rect, sp_addr = entry
                    if sp_rect.collidepoint(mx, my):
                        cur = rd8(sp_addr)
                        if cur is None or cur == 0:
                            cur = 0x45  # default "normal" value if we read 0
                        special_restore_orig = cur
                        wd8(sp_addr, 0x40)
                        special_restore_addr = sp_addr
                        special_restore_ts = now + 0.5

        # HypeTrigger restore timer
        if hype_restore_addr is not None and now >= hype_restore_ts:
            try:
                wd8(hype_restore_addr, hype_restore_orig)
            except Exception:
                pass
            hype_restore_addr = None

        # SpecialPopup restore timer
        if special_restore_addr is not None and now >= special_restore_ts:
            try:
                wd8(special_restore_addr, special_restore_orig)
            except Exception:
                pass
            special_restore_addr = None

        # tick down frame-data button flash
        for k in panel_btn_flash:
            if panel_btn_flash[k] > 0:
                panel_btn_flash[k] -= 1

        # schedule rescans
        if HAVE_SCAN_NORMALS and need_rescan_normals and scan_worker:
            scan_worker.request()
            need_rescan_normals = False

        if HAVE_SCAN_NORMALS and manual_scan_requested:
            if scan_worker:
                scan_worker.request()
            else:
                try:
                    last_scan_normals = scan_normals_all.scan_once()
                    last_scan_time = time.time()
                except Exception as e:
                    print("manual scan failed:", e)
            manual_scan_requested = False
        elif HAVE_SCAN_NORMALS and scan_worker:
            if time.time() - last_scan_time >= SCAN_MIN_INTERVAL_SEC:
                scan_worker.request()

        # csv flush placeholder
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

    pygame.quit()


if __name__ == "__main__":
    main()
