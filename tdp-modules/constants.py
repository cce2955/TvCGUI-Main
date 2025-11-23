# constants.py
# Static addresses, offsets, and ID maps. :contentReference[oaicite:2]{index=2}

# Character pointer slots (Wii guest addresses)
PTR_P1_CHAR1 = 0x803C9FCC
PTR_P1_CHAR2 = 0x803C9FDC
PTR_P2_CHAR1 = 0x803C9FD4
PTR_P2_CHAR2 = 0x803C9FE4

# Slot list: (slot label, slot pointer address, team tag)
SLOTS = [
    ("P1-C1", PTR_P1_CHAR1, "P1"),
    ("P1-C2", PTR_P1_CHAR2, "P1"),
    ("P2-C1", PTR_P2_CHAR1, "P2"),
    ("P2-C2", PTR_P2_CHAR2, "P2"),
]

# Fighter struct offsets
OFF_MAX_HP   = 0x24
OFF_CUR_HP   = 0x28
OFF_AUX_HP   = 0x2C
OFF_LAST_HIT = 0x40
OFF_CHAR_ID  = 0x14

# World position offsets
POSX_OFF   = 0xF0
POSY_CANDS = [0xF4, 0xEC, 0xE8, 0xF8, 0xFC]

# Meter offsets
METER_OFF_PRIMARY   = 0x4C
METER_OFF_SECONDARY = 0x9380 + 0x4C  # mirrored bank in memory

# Attack / state / control offsets
ATT_ID_OFF_PRIMARY = 0x1E8
ATT_ID_OFF_SECOND  = 0x1EC

CTRL_WORD_OFF = 0x70
FLAG_062      = 0x062
FLAG_063      = 0x063
FLAG_064      = 0x064
FLAG_072      = 0x072

# Memory ranges
MEM1_LO, MEM1_HI = 0x80000000, 0x81800000
MEM2_LO, MEM2_HI = 0x90000000, 0x94000000

# Bad pointers / indirections
BAD_PTRS      = {0x00000000, 0x80520000}
INDIR_PROBES  = [0x10, 0x18, 0x1C, 0x20]
LAST_GOOD_TTL = 1.0  # seconds to trust last good base

# Character ID → Name
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
