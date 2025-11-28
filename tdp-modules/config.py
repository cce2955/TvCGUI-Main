# config.py
#
# Central configuration and constants for the TvC HUD, debug overlay,
# and memory-probing tools.
#
# The goal for this file is simplicity: all global tunables, color choices,
# struct offsets, and known memory addresses live here so the rest of the
# codebase stays focused on logic instead of housekeeping.


# ------------------------------
# Update / timing / rendering
# ------------------------------

# HUD refresh rate. The game runs at 60 FPS, but most UI elements only
# need ~30 updates per second to feel responsive.
INTERVAL = 1 / 30.0

# Minimum HP loss required before logging a hit. This prevents noise from
# chip damage quirks or animation-driven health polling artifacts.
MIN_HIT_DAMAGE = 10

# Default window size for the HUD.
SCREEN_W = 1280
SCREEN_H = 800

# Panel dimensions for character info blocks.
PANEL_W = 300
PANEL_H = 200

ROW1_Y = 10
ROW2_Y = ROW1_Y + PANEL_H + 10

# Vertical placement for the activity strip and inspector section.
STACK_TOP_Y = ROW2_Y + PANEL_H + 20
ACTIVITY_H = 40

LOG_H = 140    # event log box height
INSP_H = 200   # memory-inspector panel height

FONT_MAIN_SIZE = 16
FONT_SMALL_SIZE = 14

# CSV data sources for move ID labeling logic.
HIT_CSV = "collisions.csv"
GENERIC_MAPPING_CSV = "move_id_map_charagnostic.csv"
PAIR_MAPPING_CSV = "move_id_map_charpair.csv"


# ------------------------------
# Debug / presentation flags
# ------------------------------
#
# These are bytes in memory that control on-screen elements such as pause
# overlays, director state, and visual effects. The HUD only *reads* them;
# debug tools elsewhere handle toggling.
#
# Address notes come from runtime tracing and memory dumps.

DEBUG_FLAG_ADDRS = [
    ("PauseOverlay", 0x805610F0 + 0x1B),  # dims the screen when pause is active
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
    Return a color based on % life remaining.
    pct is expressed as [0,1]. None returns neutral text.

    The thresholds loosely match the in-game bar’s visual behavior.
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
#
# These offsets are relative to each fighter’s resolved base pointer. They
# represent the small “wire” slice we display in the inspector panel to help
# with reverse-engineering and debugging. Offsets were identified through
# pattern scanning and comparing across characters.

HEALTH_WIRE_OFFSETS = [
    # HP clusters around 0x000–0x00B (typically 32-bit fields).
    0x000, 0x001, 0x002, 0x003,  # cur_hp
    0x004, 0x005, 0x006, 0x007,  # max_hp
    0x008, 0x009, 0x00A, 0x00B,  # last damage / hit response

    # Additional bytes showing red-life behavior and internal health pools.
    0x02A,  # pooled “red life” total; decrements as damage is taken
    0x02B,  # decrementing byte with wrap behavior (still under study)
]

WIRE_OFFSETS = [
    # Status / mode flags.
    0x062,
    0x063,
    0x064,
    0x072,

    # Control state block.
    0x090, 0x091, 0x092, 0x093,

    # Action / subaction cluster (includes attA, attB, and Y-pos).
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
# Based on capture analysis:
#   If byte at 0x9246CB9D == 0x00 → Baroque not ready
#   Else → Baroque ready.
#
# We read both the primary “gate” byte and its adjacent buddy byte for clarity.

BAROQUE_STATUS_ADDR_MAIN  = 0x9246CBAB  # authoritative readiness byte
BAROQUE_STATUS_ADDR_BUDDY = 0x9246CB9C  # secondary neighbor byte

BAROQUE_FLAG_ADDR_0 = 0x9246CC48
BAROQUE_FLAG_ADDR_1 = 0x9246CC50

# P1 input monitor:
# These regions pulse with specific action bytes (attacks, assists, taunt).
# We expose them directly so the inspector can mirror physical controller input.

INPUT_MONITOR_ADDRS = {
    "A0": 0x9246CC40,
    "A1": 0x9246CC50,
    "A2": 0x9246CC60,
}

# Broad dump of the CC40 region to make controller state fully visible.
BAROQUE_MONITOR_ADDR = 0x9246CC40
BAROQUE_MONITOR_SIZE = 0x80  # covers CC40 → CCBF


# ------------------------------
# Advantage tracking
# ------------------------------
#
# When computing frame advantage, we locate the “attacker closest to victim”
# using squared distance. These constants determine when a pair is considered
# close enough to be related to the same interaction.

MAX_CONTACT_DIST = 250.0
MAX_DIST2 = MAX_CONTACT_DIST * MAX_CONTACT_DIST

# How long we keep a hit/block interaction alive before discarding it.
ADV_FORGET_FRAMES = 120
