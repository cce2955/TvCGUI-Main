"""Constants and process-local caches for assist_scanner_backend.

This file only holds data/state that used to live at the top of
assist_scanner_backend.py. Keeping it separate makes the backend logic easier to
read without changing the runtime objects it uses.
"""

from __future__ import annotations

import os
import threading

SCAN_START = 0x90000000
SCAN_END = 0x94000000
SCAN_BLOCK = 0x40000
OVERLAP = 0x400

_CHR_TBL_BASES = [
    0x90896640,
    0x908F1920,
    0x909478E0,
    0x9099D9C0,
]

# Confirmed Chun assist selector graft.
#
# Validated live patch:
#   Graft this 0x2C-byte Ryu-style selector setup over the active Chun assist
#   block that begins 0x48 bytes before the live Tensho state wrapper.
#
# Original live Chun block:
#   0F060027 3732203F 00000003 3733203F 00000009
#   013C0000 00001610 013C0000 000016C8 013C0000 00001744
#
# Grafted selector block:
#   0F060027 01500060 000044B4 00000003
#   0003BCC8 0003C4AC 0003CC90
#   3732203F 00000002 3733203F 00000004
#
# After install, the selector words are:
#   graft_addr + 0x10
#   graft_addr + 0x14
#   graft_addr + 0x18
CHUN_SELECTOR_ORIGINAL_BLOCK = bytes.fromhex(
    "0F 06 00 27 "
    "37 32 20 3F 00 00 00 03 "
    "37 33 20 3F 00 00 00 09 "
    "01 3C 00 00 00 00 16 10 "
    "01 3C 00 00 00 00 16 C8 "
    "01 3C 00 00 00 00 17 44"
)

CHUN_SELECTOR_GRAFT_BLOCK = bytes.fromhex(
    "0F 06 00 27 "
    "01 50 00 60 00 00 44 B4 00 00 00 03 "
    "00 03 BC C8 00 03 C4 AC 00 03 CC 90 "
    # Ryu-style selector args. The original Chun graft used 3/9; that proved
    # the graft mechanism, but direct chr_tbl move presets behave more like
    # Ryu's native assist selector and need 2/4.
    "37 32 20 3F 00 00 00 02 "
    "37 33 20 3F 00 00 00 04"
)

CHUN_SELECTOR_GRAFT_DELTA_TO_WRAPPER = 0x48
CHUN_SELECTOR_WORD_OFFSETS = (0x10, 0x14, 0x18)

CHUN_SELECTOR_PAYLOADS = [
    ("force all lanes -> 0003D620", bytes.fromhex("00 03 D6 20")),
    ("force all lanes -> 0003BCC8", bytes.fromhex("00 03 BC C8")),
    ("force all lanes -> 0003C4AC", bytes.fromhex("00 03 C4 AC")),
    ("force all lanes -> 0003CC90", bytes.fromhex("00 03 CC 90")),
]

CHUN_SELECTOR_RESET_WORDS = {
    0x10: bytes.fromhex("00 03 BC C8"),
    0x14: bytes.fromhex("00 03 C4 AC"),
    0x18: bytes.fromhex("00 03 CC 90"),
}

CHUN_SELECTOR_WORD_PRESETS = [
    ("Chun payload 0003D620", 0x0003D620),
    ("Chun payload 0003BCC8", 0x0003BCC8),
    ("Chun payload 0003C4AC", 0x0003C4AC),
    ("Chun payload 0003CC90", 0x0003CC90),
]

_FIGHTER_BASES = [
    0x9246B9C0,
    0x927EB9E0,
    0x92B6BA00,
    0x92EEBA20,
]
_CHAR_ID_OFF = 0x14

FIGHTER_SLOT_LABELS = ("P1-C1", "P2-C1", "P1-C2", "P2-C2")


# Main/HUD context piggyback.
# main.py owns the reliable live slot snapshots. The scanner keeps assist logic
# here, but uses the HUD snapshots to avoid full MEM2 scans and to support
# quick-assist buttons without making main.py understand assist internals.
_MAIN_SNAPS_CACHE: dict[str, dict] = {}
_MAIN_SNAPS_LOCK = threading.RLock()

QUICK_ASSISTS_FILE = "quick_assists.json"
QUICK_ASSISTS_FILE_CANDIDATES = (
    QUICK_ASSISTS_FILE,
    os.path.join("assets", QUICK_ASSISTS_FILE),
    os.path.join("data", QUICK_ASSISTS_FILE),
)
_QUICK_ASSISTS_CACHE: tuple[float, dict] | None = None

DEFAULT_QUICK_ASSISTS = {
    "default": [
        {"label": "304", "table": 304},
        {"label": "305", "table": 305},
        {"label": "306", "table": 306},
        {"label": "Default", "default": True},
    ]
}

# Headless profiles are used when quick assists are clicked from the HUD while
# the Tk scanner window is not open. If the window is open, it mirrors the same
# profiles in its existing per-fighter table.
_HEADLESS_ASSIST_PROFILES: dict[int, dict[str, object]] = {}
_HEADLESS_LAST_ASSIST_ATTACK_BY_BASE: dict[int, bool] = {}
_HEADLESS_LAST_PATCH_KEY: tuple[int, int] | None = None

# Quick route cache. Quick assist buttons should not rescan/re-resolve the
# active graft route on every click. main.py feeds live snaps each frame; this
# cache is warmed in the background when a loaded character is seen, then quick
# clicks only resolve table -> word and write the already-known route bytes.
_QUICK_ROUTE_CACHE: dict[tuple[int, int], dict] = {}
_QUICK_ROUTE_INFLIGHT: set[tuple[int, int]] = set()
_QUICK_ROUTE_LOCK = threading.RLock()
_QUICK_ROUTE_FAIL_UNTIL: dict[tuple[int, int], float] = {}
_QUICK_ROUTE_FAIL_TTL_SECONDS = 0.75
_QUICK_ROUTE_MAX_INFLIGHT = 1

# UI scan result cache. This never changes assist write behavior. It only lets
# the scanner window reuse rows that the proven scanner already found, instead
# of rescanning MEM2 just to redraw the same table.
_ASSIST_SCAN_CACHE_LOCK = threading.RLock()
_ASSIST_SCAN_CACHE_SIGNATURE: tuple | None = None
_ASSIST_SCAN_CACHE_HITS: list[dict] = []

# Runtime cache invalidation.
#
# Route rows are absolute MEM2 addresses. If a match ends and the same character
# is picked again, owner_base + char_id can be identical while the live chr_tbl /
# graft address has been rebuilt. Keep a cheap epoch and bump it whenever main.py
# shows a slot unload, invalid/transitional char id, or character/base change.
# Cached route rows from older epochs are ignored and re-resolved on the next
# quick-assist click. This does not change any write/graft logic.
_ASSIST_CACHE_EPOCH = 0
_ASSIST_LAST_SLOT_SIGNATURES: dict[str, tuple[int, int]] = {}


# Shared-character selector tables mean duplicate characters cannot keep
# separate table bytes at rest. Per-fighter profiles are stored separately,
# then Auto Slot Switch patches the shared character table when that fighter
# enters an assist-ish state.
ASSIST_RUNTIME_ATTACK_STATES = {420, 426, 427, 428}
ASSIST_RUNTIME_PREFETCH_STATES = {430, 424, 425, 432, 433}
ASSIST_RUNTIME_STATE_PRIORITY = {426: 100, 430: 45, 424: 35, 425: 30, 427: 30}
FIGHTER_MOVE_SCAN_SIZE = 0x6000

# Generic characters may have many selector-looking tables. For assist
# hijacking, prefer candidates physically near the assist route entries.
GENERIC_ASSIST_ROUTE_TABLE_INDICES = (420, 424, 425, 426, 427, 428, 430, 431, 432, 433)
GENERIC_ASSIST_ROUTE_BEFORE = 0x40
GENERIC_ASSIST_ROUTE_AFTER = 0x260
GENERIC_ASSIST_ROUTE_SCORE_BONUS = 600

# Generic fallback for characters whose assist route does not expose a usable
# Ryu/Chun-style selector table. This keeps the assist attack chr_tbl entry
# pointed at the original wrapper, then patches internal wrapper call operands.
# That preserves hop-in/positioning while redirecting the attack body.
ANCHOR_ASSIST_FALLBACK_TABLE_INDEX = 426
ANCHOR_ASSIST_WRAPPER_SCAN_SIZE = 0x900
ANCHOR_ASSIST_OPERAND_MIN = 0x00003600
ANCHOR_ASSIST_OPERAND_MAX = 0x00090000
ANCHOR_ASSIST_MAX_OPERANDS = 8
ANCHOR_ASSIST_CALL_OPS = (
    b"\x01\x32\x00\x00",
    b"\x01\x33\x00\x00",
    b"\x01\x36\x00\x00",
)

# Viewtiful Joe special case.
#
# Viewtiful Joe special case.
#
# VJoe is handled as a static/direct chr_tbl[426] route patch for testing.
# Selecting a VJoe preset writes chr_tbl[426] immediately and leaves it there.
# The main auto-trigger deliberately does not prearm/rewrite VJoe during 430,
# because the 430-prearm route tested worse than the static/direct write.
VJOE_CHAR_ID = 17
VJOE_TRAMPOLINE_SCAN_SIZE = 0x2400
VJOE_TRAMPOLINE_STATE_SEARCH_START = 0x80
VJOE_TRAMPOLINE_STATE_SEARCH_END = 0x600
VJOE_TRAMPOLINE_CONT_OFF_FROM_STATE_WRAPPER = 0x18
VJOE_TRAMPOLINE_STATE_FIELD_OFF = 0x12
VJOE_TRAMPOLINE_STATE_BY_TABLE_INDEX = {
    304: 0x0112,  # Voomerang A
    305: 0x0113,  # Voomerang B
    306: 0x0114,  # Voomerang C
}

# Volnutt special case.
#
# Volnutt's assist is not just a normal move selector. His 6B-style assist body
# is augmented by the active weapon arm. Quick assists can therefore include a
# Volnutt weapon value and still point the assist route at the normal 6B table.
# The scanner searches only inside Volnutt's resolved chr_tbl[426] wrapper and
# patches the small weapon selector bytes observed in the assist package.
VOLNUTT_CHAR_ID = 18
VOLNUTT_6B_TABLE_INDEX = 270
VOLNUTT_WEAPON_NAMES = {
    0: "Arm",
    1: "Drill",
    2: "Gun",
    3: "Shield",
}
VOLNUTT_WEAPON_ALIASES = {
    "arm": 0,
    "normal": 0,
    "default_arm": 0,
    "default arm": 0,
    "drill": 1,
    "gun": 2,
    "shield": 3,
}
VOLNUTT_WEAPON_WRAPPER_SCAN_SIZE = 0x3000
VOLNUTT_WEAPON_ASSIST_START_HEAD = bytes.fromhex(
    "04 01 60 00 00 00 00 58 3F 00 00 00 00 40 02 00 "
    "04 02 60 00 00 00 45 FC 3F 00 00 00 00 00 00"
)
VOLNUTT_WEAPON_ASSIST_START_TAIL = bytes.fromhex(
    "01 33 00 00 00 04 42 5C"
)
VOLNUTT_WEAPON_SETUP_HEAD = bytes.fromhex(
    "04 15 60 00 00 00 46 04 3F 00 00 00 00 00 00 02 "
    "04 01 60 00 00 00 46 00 3F 00 00 00 00 00 00"
)
VOLNUTT_WEAPON_SETUP_TAIL = bytes.fromhex(
    "04 02 60 00 00 00 45 FC 3F 00 00 00 00 00 00 00"
)

# Tatsunoko fallback class. These characters do not always expose a clean
# Ryu/Chun-style selector block. For them, the assist hop-in is handled by
# standby/430, while chr_tbl[426] is the attack route. A wrapper-preserving row patches the active chr_tbl[426]
# wrapper operands instead of trusting the broad slot base. This avoids fake
# direct rows when the real chr_tbl sits shortly before the nominal slot.
TATSUNOKO_DIRECT_426_CHAR_IDS = {
    1, 2, 3, 4, 5, 6, 7, 8, 10, 11, 26, 27, 28,
}
DIRECT_426_TABLE_INDEX = 426
# Tatsunoko assists use the same 430 -> 426 -> taunt -> leave flow, but the
# first visible attack body starts inside the 426 wrapper around +0x154.
# Patching the later anchored call makes Casshan do default first, then the
# selected move. Graft at the attack-body start instead.
TATSUNOKO_WRAPPER_GRAFT_OFF = 0x154
TATSUNOKO_WRAPPER_GRAFT_SCAN_END = 0x380


CHAR_ID_TO_KEY = {
    1: "Ken the Eagle", 2: "Casshan", 3: "Tekkaman", 4: "Polimar",
    5: "Yatterman-1", 6: "Doronjo", 7: "Ippatsuman", 8: "Jun the Swan",
    10: "Karas", 11: "Gold Lightan", 12: "Ryu", 13: "Chun-Li",
    14: "Batsu", 15: "Morrigan", 16: "Alex", 17: "Viewtiful Joe",
    18: "Volnutt", 19: "Roll", 20: "Saki", 21: "Soki", 22: "PTX-40A",
    23: "Yami", 24: "Yami", 25: "Yami", 26: "Tekkaman Blade",
    27: "Joe the Condor", 28: "Yatterman-2", 29: "Zero", 30: "Frank West",
}

# Confirmed live Ryu selector chain:
#   0x908C7680: 00 03 11 0C 00 03 14 6C 00 03 1C DC 37 32 20 3F
# Validated effects:
#   0003110C = Hadouken
#   0003146C = Shoryu
#   00031CDC = Tatsu
RYU_KNOWN_TARGETS = {
    0x0003110C: "Ryu Hadouken",
    0x0003146C: "Ryu Shoryu",
    0x00031CDC: "Ryu Tatsu",
}
RYU_PRESETS = [
    ("Hadouken", 0x0003110C),
    ("Shoryu", 0x0003146C),
    ("Tatsu", 0x00031CDC),
]


# Confirmed Ryu assist selector graft/table.
#
# Ryu already has the full Ryu-style selector setup in the live assist route.
# This exposes it the same way as the Chun graft table, so a lane can be
# double-clicked and written with a preset/manual selector word.
RYU_SELECTOR_GRAFT_BLOCK = bytes.fromhex(
    "0F 06 00 27 "
    "01 50 00 60 00 00 44 B4 00 00 00 03 "
    "00 03 11 0C 00 03 14 6C 00 03 1C DC "
    "37 32 20 3F 00 00 00 02 "
    "37 33 20 3F 00 00 00 04"
)

RYU_SELECTOR_WORD_OFFSETS = (0x10, 0x14, 0x18)

RYU_SELECTOR_RESET_WORDS = {
    0x10: bytes.fromhex("00 03 11 0C"),
    0x14: bytes.fromhex("00 03 14 6C"),
    0x18: bytes.fromhex("00 03 1C DC"),
}

RYU_SELECTOR_WORD_PRESETS = [
    ("Ryu Hadouken 0003110C", 0x0003110C),
    ("Ryu Shoryu 0003146C", 0x0003146C),
    ("Ryu Tatsu 00031CDC", 0x00031CDC),
]


# Generic assist selector graft probing.
#
# This is the experimental path for every other character. It looks for two
# shapes:
#   1) Native Ryu-style selector tables:
#      0F060027 01500060 000044B4 00000003 [3 words] 3732203F ... 3733203F ...
#   2) Chun-style graftable assist blocks:
#      0F060027 3732203F [arg] 3733203F [arg] 013C0000 ...
#
# Double-clicking a generic lane writes a full Ryu-style selector setup first.
# For graft candidates that do not have selector words yet, the chosen manual
# word is applied to all three lanes by default so the install has valid words.
GENERIC_SELECTOR_SETUP_PREFIX = bytes.fromhex(
    "0F 06 00 27 "
    "01 50 00 60 00 00 44 B4 00 00 00 03"
)
GENERIC_SELECTOR_WORD_OFFSETS = (0x10, 0x14, 0x18)
GENERIC_SELECTOR_TABLE_LEN = 0x2C
GENERIC_SELECTOR_DEFAULT_ARG_32 = bytes.fromhex("00 00 00 03")
GENERIC_SELECTOR_DEFAULT_ARG_33 = bytes.fromhex("00 00 00 09")
GENERIC_SELECTOR_MANUAL_PRESETS = [
    ("Chun 0003D620", 0x0003D620),
    ("Chun 0003BCC8", 0x0003BCC8),
    ("Chun 0003C4AC", 0x0003C4AC),
    ("Chun 0003CC90", 0x0003CC90),
    ("Ryu Hadouken 0003110C", 0x0003110C),
    ("Ryu Shoryu 0003146C", 0x0003146C),
    ("Ryu Tatsu 00031CDC", 0x00031CDC),
]

# Move preset harvesting for assist grafts.
#
# These rows use the same basic logic as scan normals all:
#   selector_word = move_abs - chr_tbl_abs
# Double-clicking one writes that selector word into the active graft lanes.
MOVE_PRESET_CHR_TBL_NUM_ENTRIES = 705
MOVE_PRESET_DATA_START_OFF = 0x3600
MOVE_PRESET_SLOT_SIZE = 0x90000
# Some Tatsunoko/Capcom runtime chr_tbls are laid down shortly before
# the nominal broad slot base. Scan back far enough to catch the active
# table instead of falling back to random script bytes at the broad base.
CHR_TBL_PRE_START_BACK = 0x12000
MOVE_PRESET_LOOKAHEAD = 0x80
MOVE_PRESET_ENTRY_OFFSETS = (0x00, 0x10, 0x20, 0x40, 0x70, 0x90)
MOVE_PRESET_ANIM_HDR = bytes.fromhex("04 01 60 00 00 00 01 E8 3F 00 00 00")
MOVE_PRESET_NORMAL_LABELS = {
    0x00: "5A", 0x01: "5B", 0x02: "5C",
    0x03: "2A", 0x04: "2B", 0x05: "2C",
    0x06: "6C", 0x08: "3C",
    0x09: "j.A", 0x0A: "j.B", 0x0B: "j.C",
    0x0E: "6B",
}
MOVE_PRESET_NORMAL_IDS = set(MOVE_PRESET_NORMAL_LABELS.keys())

# Optional assist preset name map.
#
# Do not embed/manual-define the preset names here. The assist picker reads
# move_id_map_charagnostic.csv at runtime and uses its table/index number as
# the preset key. Put the CSV beside this scanner/module, in the current
# working directory, or in ./assets or ./data.
ASSIST_PRESET_NAME_FILE = "move_id_map_charagnostic.csv"
ASSIST_PRESET_NAME_FILE_CANDIDATES = (
    ASSIST_PRESET_NAME_FILE,
    os.path.join("assets", ASSIST_PRESET_NAME_FILE),
    os.path.join("data", ASSIST_PRESET_NAME_FILE),
)

_ASSIST_PRESET_NAME_CACHE: dict[tuple[int, int], tuple[int, str]] | None = None
