# --- Static slots (US build observed) ---
PTR_P1_CHAR1 = 0x803C9FCC
PTR_P1_CHAR2 = 0x803C9FDC
PTR_P2_CHAR1 = 0x803C9FD4
PTR_P2_CHAR2 = 0x803C9FE4

SLOTS = [
    ("P1-C1", PTR_P1_CHAR1, 0),
    ("P1-C2", PTR_P1_CHAR2, 0),
    ("P2-C1", PTR_P2_CHAR1, 1),
    ("P2-C2", PTR_P2_CHAR2, 1),
]

# --- Fighter offsets ---
OFF_MAX_HP   = 0x24
OFF_CUR_HP   = 0x28
OFF_AUX_HP   = 0x2C
OFF_LAST_HIT = 0x40
OFF_CHAR_ID  = 0x14
POSX_OFF     = 0xF0
POSY_OFFS    = [0xF4, 0xEC, 0xE8, 0xF8, 0xFC]
METER_OFF_PRIMARY   = 0x4C
METER_OFF_SECONDARY = 0x9380 + 0x4C

# --- Validation ---
HP_MIN_MAX = 10_000
HP_MAX_MAX = 60_000
MEM1_LO, MEM1_HI = 0x80000000, 0x81800000
MEM2_LO, MEM2_HI = 0x90000000, 0x94000000
BAD_PTRS = {0x00000000, 0x80520000}

# --- Timing ---
POLL_HZ = 30
POLL_DT = 1.0 / POLL_HZ

# --- Characters ---
CHAR_NAMES = {
    1: "Ken the Eagle", 2: "Casshan", 3: "Tekkaman", 4: "Polimar", 5: "Yatterman-1",
    6: "Doronjo", 7: "Ippatsuman", 8: "Jun the Swan", 10: "Karas", 12: "Ryu",
    13: "Chun-Li", 14: "Batsu", 15: "Morrigan", 16: "Alex", 17: "Viewtiful Joe",
    18: "Volnutt", 19: "Roll", 20: "Saki", 21: "Soki", 26: "Tekkaman Blade",
    27: "Joe the Condor", 28: "Yatterman-2", 29: "Zero", 30: "Frank West",
}

# --- Event keys ---
EVT_UPDATE = "update"
EVT_HIT    = "hit"
