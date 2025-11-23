# config.py
#
# Global config / constants for TvC HUD & runtime probes.

# ------------------------------
# Update / timing / rendering
# ------------------------------

INTERVAL = 1 / 30.0           # seconds per HUD update tick
MIN_HIT_DAMAGE = 10           # min HP delta to consider "a hit" for logging

SCREEN_W = 1280
SCREEN_H = 800

PANEL_W  = 300
PANEL_H  = 200

ROW1_Y   = 10
ROW2_Y   = ROW1_Y + PANEL_H + 10

STACK_TOP_Y = ROW2_Y + PANEL_H + 20
ACTIVITY_H  = 40

LOG_H   = 140
INSP_H  = 200

FONT_MAIN_SIZE  = 16
FONT_SMALL_SIZE = 14

HIT_CSV             = "collisions.csv"
GENERIC_MAPPING_CSV = "move_id_map_charagnostic.csv"
PAIR_MAPPING_CSV    = "move_id_map_charpair.csv"
# ------------------------------
# Debug / presentation flags
# ------------------------------
# These are global UI / presentation toggles we want to watch live.
# For now we only READ one byte from each and display it in the HUD.
#
# Notes from lab work:
#   0x805610F0: pause controller (zeroing 0x18–0x24 disables pause)
#   0x80561100: pause darken overlay (0x24 dims)
#   0x805C78B0..0x805FAB70: big match/camera/announcer "director" blob
#   0x803FB940..: combo popup store/trigger
#   0x803FB950..: counter formatting/visibility
#   0x803FB9E0..: hype word trigger
#   0x803FBA70..: Baroque popup trigger
#   0x803FBB80/0x803FBB90: super UI glow/superflash

DEBUG_FLAG_ADDRS = [
    ("PauseOverlay", 0x805610F0 + 0x1B),
    ("PauseCtrl",    0x80561100 + 0x24),
    ("Director",     0x805C78B0),
]

# ------------------------------
# Colors / HUD helpers
# ------------------------------

COL_BG     = (10, 10, 12)
COL_PANEL  = (24, 24, 28)
COL_BORDER = (80, 80, 90)
COL_TEXT   = (230, 230, 230)
COL_GOOD   = (80, 220, 80)
COL_WARN   = (255, 180, 0)
COL_BAD    = (255, 60, 60)

def hp_color(pct):
    """
    Pick a text color based on % life remaining.
    pct should be in [0,1]; None falls back to neutral text.
    """
    if pct is None:
        return COL_TEXT
    if pct > 0.50:
        return COL_GOOD
    if pct > 0.25:
        return COL_WARN
    return COL_BAD

# ------------------------------
# Fighter struct probing
# ------------------------------
# We read small slices of each fighter struct, relative to that fighter's
# resolved base pointer. These bytes are dumped into the HUD inspector.

HEALTH_WIRE_OFFSETS = [
    # 0x000..0x00B is typically current HP, max HP, last_damage, etc.
    0x000, 0x001, 0x002, 0x003,  # cur_hp (likely 32-bit int)
    0x004, 0x005, 0x006, 0x007,  # max_hp (32-bit)
    0x008, 0x009, 0x00A, 0x00B,  # most recent hit dmg / "last damage"
    # bytes of interest around health / red-life
    0x02A,  # pooled life / "red bar total" style aggregate (goes down as you lose health)
    0x02B,  # odd decrementer that seems to tick down in steps and wrap
]

WIRE_OFFSETS = [
    # Various status / flags already cared about
    0x062,
    0x063,
    0x064,
    0x072,
    # Control state (0x90..0x93)
    0x090, 0x091, 0x092, 0x093,
    # "attA/attB" / subaction / y-pos cluster around 0x0F0..0x0F7
    0x0F0,
    0x0F1,
    0x0F2,
    0x0F3,
    0x0F4, 0x0F5, 0x0F6, 0x0F7,
]

# ------------------------------
# Baroque / input monitor
# ------------------------------
#
#
# Rule:
#   if (0x9246CB9D == 0x00): "Baroque not ready"
#   else:                    "Baroque ready"
#
# We'll read *both* for debug, and expose them in HUD.
# We'll call 0x9246CB9D the "main" readiness byte and drive HUD readiness off it.

BAROQUE_STATUS_ADDR_MAIN  = 0x9246CBab  # authoritative gate byte
BAROQUE_STATUS_ADDR_BUDDY = 0x9246CB9C  # neighbor / buddy byte

# 
# 
# 
BAROQUE_FLAG_ADDR_0 = 0x9246CC48
BAROQUE_FLAG_ADDR_1 = 0x9246CC50

# Controller / input monitor for P1:
# From the captures:
#   0x9246CC40 region: heavy, assist style codes showed up here
#   0x9246CC50 region: light / medium press bytes
#   0x9246CC60 region: taunt, etc
#
# We'll read these direct and display them.

INPUT_MONITOR_ADDRS = {
    "A0": 0x9246CC40,
    "A1": 0x9246CC50,
    "A2": 0x9246CC60,
}

# dump a big slab of that CC40 range each frame in the inspector so
# you can watch all those 05 01 sequences live.
BAROQUE_MONITOR_ADDR = 0x9246CC40
BAROQUE_MONITOR_SIZE = 0x80  # 128 bytes to cover CC40..CCBF-ish

# ------------------------------
# Advantage tracking
# ------------------------------
# Distance cutoff for deciding who is "in contact" for frame advantage logging.
MAX_CONTACT_DIST = 250.0
MAX_DIST2 = MAX_CONTACT_DIST * MAX_CONTACT_DIST

# How long (in frames) we keep the interaction alive in ADV_TRACK after hit/block.
ADV_FORGET_FRAMES = 120
