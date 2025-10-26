# config.py
# Runtime tuning knobs and UI layout.

import math

# =========================
# RUNTIME / POLLING
# =========================

POLL_HZ         = 60
INTERVAL        = 1.0 / POLL_HZ
COMBO_TIMEOUT   = 0.60
MIN_HIT_DAMAGE  = 10
MAX_DIST2       = 100.0
METER_DELTA_MIN = 5

# bytes to watch (0x050..0x08F)
# This region appears to carry a bunch of control / action state flags,
# cancel logic, etc. We already surface this in the Inspector.
WIRE_OFFSETS = list(range(0x050, 0x090))

# NEW:
# extra "health cluster" watch window.
# HP-related struct members live starting ~0x024-0x02C (max/cur/aux HP).
# We want to spy on the neighborhood around that block each frame,
# because that region tends to hold other per-fighter critical runtime data
# (armor, stun timers, etc.).
HEALTH_WIRE_OFFSETS = list(range(0x029, 0x030))

# bytes 0x050..0x08F and 0x020..0x03F are both dumped per fighter snapshot.
# HUD shows both, with the health-cluster bytes shown first.


# =========================
# MEMORY WATCH (debug wires)
# =========================

# WIRE_OFFSETS and HEALTH_WIRE_OFFSETS are consumed in fighter.read_fighter()
# and later rendered by hud_draw.draw_inspector().


# =========================
# WINDOW / LAYOUT CONSTANTS
# =========================

SCREEN_W = 1280
SCREEN_H = 1200

# Two character panels per row. Panels are fixed-height.
PANEL_W  = SCREEN_W // 2 - 20
PANEL_H  = 150

ROW1_Y   = 10
ROW2_Y   = ROW1_Y + PANEL_H + 10

ACTIVITY_H = 40
LOG_H      = 160
INSP_H     = 220

STACK_TOP_Y = ROW2_Y + PANEL_H + 10  # activity bar starts here

FONT_MAIN_SIZE  = 16  # monospace main
FONT_SMALL_SIZE = 14

# =========================
# CSV OUTPUT PATHS
# =========================

HIT_CSV             = "collisions.csv"
PAIR_MAPPING_CSV    = "move_id_map_charpair.csv"
GENERIC_MAPPING_CSV = "move_id_map_charagnostic.csv"

# =========================
# COLORS (RGB tuples)
# =========================

def rgb(r, g, b):
    return (r, g, b)

COL_BG      = rgb(10, 10, 10)
COL_PANEL   = rgb(20, 20, 20)
COL_BORDER  = rgb(100, 100, 100)
COL_TEXT    = rgb(220, 220, 220)
COL_DIM     = rgb(140, 140, 140)
COL_GOOD    = rgb(100, 220, 100)
COL_WARN    = rgb(230, 200, 70)
COL_BAD     = rgb(230, 80, 80)
COL_ACCENT  = rgb(190, 120, 255)
