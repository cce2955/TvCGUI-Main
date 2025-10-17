# constants.py
# Static addresses, offsets, RAM bounds, and character IDs for TvC (US build).
# Other modules import these values (resolver, meter, hud, etc.).

# ---------------- Pointer slots (character object pointers) ----------------
# These are the absolute guest addresses of the slot structures that indirect to
# the active fighter instances. We resolve to a "base" by probing or verifying.
PTR_P1_CHAR1 = 0x803C9FCC
PTR_P1_CHAR2 = 0x803C9FDC
PTR_P2_CHAR1 = 0x803C9FD4
PTR_P2_CHAR2 = 0x803C9FE4

SLOTS = [
    ("P1-C1", PTR_P1_CHAR1),
    ("P1-C2", PTR_P1_CHAR2),
    ("P2-C1", PTR_P2_CHAR1),
    ("P2-C2", PTR_P2_CHAR2),
]

# ---------------- Fighter object layout (offsets from resolved base) --------
OFF_MAX_HP   = 0x24
OFF_CUR_HP   = 0x28
OFF_AUX_HP   = 0x2C
OFF_LAST_HIT = 0x40   # victim "last damage chunk"
OFF_CHAR_ID  = 0x14

# ---------------- World position offsets -----------------------------------
POSX_OFF   = 0xF0
POSY_CANDS = [0xF4, 0xEC, 0xE8, 0xF8, 0xFC]  # we auto-pick the best Y at runtime

# ---------------- Meter offsets ---------------------------------------------
METER_OFF_PRIMARY   = 0x4C
METER_OFF_SECONDARY = 0x9380 + 0x4C   # mirrored bank

# ---------------- RAM ranges (MEM1/MEM2) ------------------------------------
MEM1_LO, MEM1_HI = 0x80000000, 0x81800000
MEM2_LO, MEM2_HI = 0x90000000, 0x94000000

# Known-bad pointer sentinels
BAD_PTRS = {0x00000000, 0x80520000}

# When a slot value doesn't directly look like a fighter object, try these indirections.
# (slot + off) -> pointer candidate; we then verify that candidate by HP-looking fields.
INDIR_PROBES = [0x10, 0x18, 0x1C, 0x20]

# How long (seconds) we keep using the last known-good base if a read briefly fails.
LAST_GOOD_TTL = 1.0

# ---------------- Character IDs (US build) ----------------------------------
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
    26: "Tekkaman Blade",
    27: "Joe the Condor",
    28: "Yatterman-2",
    29: "Zero",
    30: "Frank West",
}
