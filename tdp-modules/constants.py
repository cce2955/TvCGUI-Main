# constants.py
#
# Static addresses, struct offsets, and lookup tables used across the HUD
# and memory-probing tools. These represent stable findings from reverse-
# engineering TvC’s runtime memory layout on Wii/Dolphin.
#
# Nothing here contains logic; this file exists so the rest of the project
# never hardcodes numbers or magic offsets.


# ------------------------------------------------------------
# Character pointer slots (guest/Wii addresses)
# ------------------------------------------------------------
# Each entry points to the base of a fighter struct. The resolver then
# follows internal indirections to find the actual live base address.

PTR_P1_CHAR1 = 0x803C9FCC
PTR_P1_CHAR2 = 0x803C9FDC
PTR_P2_CHAR1 = 0x803C9FD4
PTR_P2_CHAR2 = 0x803C9FE4

# (slot label, pointer address, team tag)
SLOTS = [
    ("P1-C1", PTR_P1_CHAR1, "P1"),
    ("P1-C2", PTR_P1_CHAR2, "P1"),
    ("P2-C1", PTR_P2_CHAR1, "P2"),
    ("P2-C2", PTR_P2_CHAR2, "P2"),
]


# ------------------------------------------------------------
# Fighter struct offsets
# ------------------------------------------------------------
# These offsets are consistent across the majority of the cast; giants
# (PTX/Lightan) have exceptions, which the safe_read_fighter logic handles.

OFF_MAX_HP   = 0x24   # 32-bit max HP
OFF_CUR_HP   = 0x28   # 32-bit current HP
OFF_AUX_HP   = 0x2C   # sometimes tracks a mirrored or pooled HP value
OFF_LAST_HIT = 0x40   # most recent damage event (varies by character)
OFF_CHAR_ID  = 0x14   # character ID used for portrait/name lookup


# ------------------------------------------------------------
# Position offsets in the world coordinate system
# ------------------------------------------------------------
# X position is consistent; Y varies slightly across characters/animations,
# so the resolver tries a small set and picks the one that produces valid values.

POSX_OFF   = 0xF0
POSY_CANDS = [0xF4, 0xEC, 0xE8, 0xF8, 0xFC]


# ------------------------------------------------------------
# Meter offsets
# ------------------------------------------------------------
# The game maintains two mirrored meter banks; the secondary one appears
# deep into a larger bank of global match state.

METER_OFF_PRIMARY   = 0x4C
METER_OFF_SECONDARY = 0x9380 + 0x4C


# ------------------------------------------------------------
# Action / animation / control state
# ------------------------------------------------------------

ATT_ID_OFF_PRIMARY = 0x1E8   # main action/subaction byte (attA)
ATT_ID_OFF_SECOND  = 0x1EC   # secondary action/subaction (attB)

CTRL_WORD_OFF = 0x70         # action gating bitfield
FLAG_062      = 0x062        # commonly toggles during hitstop/guard
FLAG_063      = 0x063
FLAG_064      = 0x064
FLAG_072      = 0x072        # appears tied to airborne/ground state


# ------------------------------------------------------------
# Valid memory ranges (Wii guest address space under Dolphin)
# ------------------------------------------------------------

MEM1_LO, MEM1_HI = 0x80000000, 0x81800000
MEM2_LO, MEM2_HI = 0x90000000, 0x94000000


# ------------------------------------------------------------
# Pointer safety helpers
# ------------------------------------------------------------
# Some pointer reads occasionally produce these invalid markers. The
# resolver rejects them and tries fallback probes to recover the real base.

BAD_PTRS      = {0x00000000, 0x80520000}

# These offsets are common places where the actual base pointer is stored
# indirectly inside the struct. The resolver walks these during recovery.
INDIR_PROBES  = [0x10, 0x18, 0x1C, 0x20]

# How long (in seconds) to trust the last known-good base pointer before
# attempting a fresh pointer resolution.
LAST_GOOD_TTL = 1.0


# ------------------------------------------------------------
# Character ID → Name lookup
# ------------------------------------------------------------
# The game uses numeric IDs internally. We maintain an explicit map so HUD
# code never relies on CSV files just to display a name.

CHAR_NAMES = {
    1:  "Ken the Eagle",
    2:  "Casshan",
    3:  "Tekkaman",
    4:  "Polimar",
    5:  "Yatterman-1",
    6:  "Doronjo",
    7:  "Ippatsuman",
    8:  "Jun the Swan",
    10: "Karas",
    11: "Gold Lightan",
    12: "Ryu",
    13: "Chun-Li",
    14: "Batsu",
    15: "Morrigan",
    16: "Alex",
    17: "Viewtiful Joe",
    18: "Volnutt",
    19: "Roll",
    20: "Saki",
    21: "Soki",
    22: "PTX-40A",
    26: "Tekkaman Blade",
    27: "Joe the Condor",
    28: "Yatterman-2",
    29: "Zero",
    30: "Frank West",
}
