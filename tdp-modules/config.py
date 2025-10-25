# config.py
# Runtime tuning knobs and UI layout. :contentReference[oaicite:1]{index=1}

import math

POLL_HZ         = 60
INTERVAL        = 1.0 / POLL_HZ
COMBO_TIMEOUT   = 0.60
MIN_HIT_DAMAGE  = 10
MAX_DIST2       = 100.0
METER_DELTA_MIN = 5

# bytes to watch (0x050..0x08F)
WIRE_OFFSETS = list(range(0x050, 0x090))

# WINDOW / LAYOUT
SCREEN_W = 1280
SCREEN_H = 800

PANEL_W  = SCREEN_W // 2 - 20  # two panels per row
PANEL_H  = 150                 # panel height
ROW1_Y   = 10
ROW2_Y   = ROW1_Y + PANEL_H + 10

ACTIVITY_H = 40
LOG_H      = 160
INSP_H     = 220

STACK_TOP_Y = ROW2_Y + PANEL_H + 10   # activity bar starts here

FONT_MAIN_SIZE  = 16  # monospace main
FONT_SMALL_SIZE = 14

# CSV paths
HIT_CSV             = "collisions.csv"
PAIR_MAPPING_CSV    = "move_id_map_charpair.csv"
GENERIC_MAPPING_CSV = "move_id_map_charagnostic.csv"

# Colors (RGB tuples)
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
